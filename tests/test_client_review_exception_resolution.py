"""Tests for the generic exception-resolution proposal lane."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

lane = importlib.import_module("client_review.exception_resolution")


def write(path, value):
    path.write_text(json.dumps(value))
    return path


def test_selection_is_generic_and_preserves_exception_kinds(tmp_path):
    records = write(
        tmp_path / "records.json",
        {"documents": [{"document_id": "a", "fields": {}}, {"document_id": "b", "fields": {}}]},
    )
    findings = write(
        tmp_path / "findings.json",
        {
            "exceptions": [
                {"document_id": "a", "reason": "unassigned_reassembly_page"},
                {"document_id": "b", "field": "header.date", "reason": "invalid_date"},
                {"document_id": "a", "reason": "unattributed_amount"},
                {"document_id": "missing", "reason": "invalid_date"},
            ]
        },
    )
    selected = lane.selected(lane.records_from(records), lane.exception_items([findings]))
    assert [item["kinds"] for item in selected] == [
        ["reassembly_grouping", "attribution_link"],
        ["validation_amendment"],
    ]
    assert selected[0]["exception_ids"] == ["exception-000000", "exception-000002"]
    assert lane.proposal_id({"document_id": "a"}) == lane.proposal_id({"document_id": "a"})

    normalized = lane.records_from(
        write(
            tmp_path / "normalized-records.json",
            {"documents": [{"document_id": 1}, {"document_id": None}]},
        )
    )
    assert normalized == {"1": {"document_id": "1"}}
    with pytest.raises(ValueError, match="duplicate document_id"):
        lane.records_from(
            write(
                tmp_path / "duplicate-records.json",
                {"documents": [{"document_id": "a"}, {"document_id": "a"}]},
            )
        )

    with pytest.raises(ValueError, match="documents"):
        lane.records_from(write(tmp_path / "bad-records.json", {"documents": "bad"}))
    with pytest.raises(ValueError, match="exception input"):
        lane.exception_items([write(tmp_path / "bad-findings.json", {"exceptions": "bad"})])
    with pytest.raises(ValueError, match="recognized exception list"):
        lane.exception_items([write(tmp_path / "missing-list.json", {"summary": {}})])
    assert lane.exception_items(
        [
            write(
                tmp_path / "reassembly.json",
                {"reassembly_exceptions": [{"document_id": "a", "reason": "unassigned_page"}]},
            )
        ]
    ) == [{"document_id": "a", "reason": "unassigned_page"}]
    with pytest.raises(ValueError, match="objects"):
        lane.exception_items([write(tmp_path / "invalid-item.json", {"exceptions": ["bad"]})])
    with pytest.raises(ValueError, match="retry_findings"):
        lane.exception_items(
            [write(tmp_path / "invalid-retry.json", {"exceptions": [{"retry_findings": ["bad"]}]})]
        )
    with pytest.raises(ValueError, match="JSON input"):
        lane.load(write(tmp_path / "list.json", []))
    batch_failure = write(
        tmp_path / "batch-failure.json",
        {
            "exceptions": [
                {
                    "source_document_ids": ["a", "b"],
                    "reason": "provider_failure",
                    "retry_findings": [
                        {"document_id": "a", "reason": "unattributed_amount"},
                        {"document_id": "b", "reason": "invalid_date"},
                    ],
                }
            ]
        },
    )
    recovered = lane.exception_items([batch_failure])
    assert [item["document_id"] for item in recovered] == ["a", "b"]
    assert [item["reason"] for item in recovered] == ["unattributed_amount", "invalid_date"]
    legacy_batch_failure = write(
        tmp_path / "legacy-batch-failure.json",
        {
            "exceptions": [
                {
                    "source_document_ids": ["a", "", None],
                    "reason": "provider_failure",
                }
            ]
        },
    )
    assert lane.exception_items([legacy_batch_failure]) == [
        {
            "source_document_ids": ["a"],
            "reason": "provider_failure",
            "document_id": "a",
        }
    ]
    context = write(
        tmp_path / "context.json",
        {
            "artifact_type": "client_review_context_v1",
            "reasoning_only": True,
            "independent_consensus_input": False,
            "policy": {"client_comments_are_untrusted_context": True},
            "client_comments": [{"client_comment": "preserved"}],
            "pilot_records": [],
        },
    )
    assert lane.reasoning_context(context)["client_comments"] == [{"client_comment": "preserved"}]
    with pytest.raises(ValueError, match="client_review_context_v1"):
        lane.reasoning_context(write(tmp_path / "wrong-context.json", {"artifact_type": "wrong"}))
    with pytest.raises(ValueError, match="reasoning-only"):
        lane.reasoning_context(
            write(
                tmp_path / "bad-context.json",
                {"artifact_type": "client_review_context_v1", "reasoning_only": False},
            )
        )
    with pytest.raises(ValueError, match="untrusted context"):
        lane.reasoning_context(
            write(
                tmp_path / "bad-policy-context.json",
                {
                    "artifact_type": "client_review_context_v1",
                    "reasoning_only": True,
                    "independent_consensus_input": False,
                    "policy": {},
                    "client_comments": [],
                    "pilot_records": [],
                },
            )
        )


def test_visible_source_and_proposal_contract_rejection_reasons():
    source_text = lane.visible_source_text(
        [
            {
                "document_id": "not-visible",
                "source": "printed",
                "fields": {
                    "one": {"value": "Invoice ABC-1"},
                    "two": {"candidate_values": ["PO-2", None, 3]},
                },
            }
        ]
    )
    assert "invoice abc-1" in source_text
    assert "po-2" in source_text
    assert "not-visible" not in source_text
    allowed = {"a"}
    base = {
        "document_id": "a",
        "kind": "validation_amendment",
        "field": "header.invoice",
        "related_document_ids": [],
        "evidence_quote": "Invoice ABC-1",
    }
    cases = [
        ("bad", "proposal_not_an_object"),
        ({**base, "document_id": "b"}, "proposal_document_outside_packet"),
        ({**base, "kind": "bad"}, "proposal_kind_unsupported"),
        ({**base, "field": ""}, "proposal_field_missing"),
        ({**base, "related_document_ids": "a"}, "proposal_related_document_outside_packet"),
        ({**base, "evidence_quote": ""}, "proposal_evidence_quote_missing"),
        ({**base, "evidence_quote": "x"}, "proposal_evidence_quote_not_in_packet"),
        ({**base, "evidence_quote": "missing"}, "proposal_evidence_quote_not_in_packet"),
        (base, None),
    ]
    assert [
        lane.proposal_rejection_reason(proposal, allowed, source_text) for proposal, _ in cases
    ] == [reason for _, reason in cases]
    assert (
        lane.proposal_rejection_reason(
            {
                **base,
                "document_id": "doc-1",
                "evidence_quote": "only visible on doc two",
            },
            {"doc-1", "doc-2"},
            {"doc-1": "visible on doc one", "doc-2": "only visible on doc two"},
        )
        == "proposal_evidence_quote_not_in_related_source"
    )


def test_result_artifacts_are_selected_and_adaptive_batches_retain_remainder(tmp_path):
    records = write(
        tmp_path / "records.json",
        {"documents": [{"document_id": "a", "fields": {}}, {"document_id": "b", "fields": {}}]},
    )
    findings = write(
        tmp_path / "results.json",
        {
            "results": [
                {"document_id": "a", "arithmetic_status": "deferred_reassembly"},
                {"document_id": "b", "arithmetic_status": "deferred_reassembly"},
            ]
        },
    )
    selected = lane.selected(lane.records_from(records), lane.exception_items([findings]))
    assert [item["kinds"] for item in selected] == [
        ["reassembly_grouping"],
        ["reassembly_grouping"],
    ]

    packets, oversized, deferred = lane.adaptive_batches(selected, 2, 0, 10000, None)
    assert [[record["exception_id"] for record in group] for group in packets] == [
        [selected[0]["exception_id"], selected[1]["exception_id"]]
    ]
    assert not oversized
    assert not deferred
    packets, oversized, deferred = lane.adaptive_batches(selected, 1, 1, 10000, None)
    assert packets == [[selected[0]]]
    assert not oversized
    assert deferred == [selected[1]]
    packets, oversized, deferred = lane.adaptive_batches(selected, 1, 1, 1, None)
    assert not packets
    assert len(oversized) == 2
    assert not deferred
    with pytest.raises(ValueError, match="batch/context limits"):
        lane.adaptive_batches(selected, 0, -1, 0, None)

    class FailingClient:
        class Responses:
            def create(self, **kwargs):
                raise AssertionError("nothing should be sent")

        responses = Responses()

    output = lane.run(
        records,
        [findings],
        tmp_path / "out.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        FailingClient(),
        FailingClient(),
        "primary",
        "buddy",
        "openai",
        "openrouter",
        None,
        batch_size=1,
        max_batches=0,
        max_context_bytes=1,
        effort="medium",
        retries=0,
        backoff=0,
        max_backoff=1,
    )
    assert output["summary"]["failed_batches"] == 0
    assert output["summary"]["exceptions"] == 2
    errors = json.loads((tmp_path / "exceptions.json").read_text())["exceptions"]
    assert {item["reason"] for item in errors} == {"exception_resolution_record_too_large"}


def test_run_retains_buddy_outcomes_and_fails_closed(tmp_path):
    class Response:
        def __init__(self, value):
            self.output_text = json.dumps(value)

        def model_dump(self, mode="json"):
            return {"output_text": self.output_text}

    class Responses:
        def __init__(self, values):
            self.values = list(values)
            self.requests = []

        def create(self, **kwargs):
            self.requests.append(kwargs)
            return Response(self.values.pop(0))

    class Client:
        def __init__(self, values):
            self.responses = Responses(values)

    records = write(
        tmp_path / "records.json",
        {
            "documents": [
                {
                    "document_id": "a",
                    "fields": {"header.date": {"value": "Date printed: 2026-01-01"}},
                }
            ]
        },
    )
    findings = write(
        tmp_path / "findings.json",
        {"exceptions": [{"document_id": "a", "reason": "invalid_date", "field": "header.date"}]},
    )
    context = write(
        tmp_path / "context.json",
        {
            "artifact_type": "client_review_context_v1",
            "reasoning_only": True,
            "independent_consensus_input": False,
            "policy": {"client_comments_are_untrusted_context": True},
            "client_comments": [],
            "pilot_records": [],
        },
    )
    primary = {
        "proposals": [
            {
                "document_id": "a",
                "kind": "validation_amendment",
                "field": "header.date",
                "proposed_value": "2026-01-01",
                "related_document_ids": [],
                "evidence_quote": "Date printed: 2026-01-01",
                "rationale": "Visible date",
            },
            {
                "document_id": "a",
                "kind": "attribution_link",
                "field": "related_document_id",
                "proposed_value": "outside-packet",
                "related_document_ids": ["outside-packet"],
                "evidence_quote": "Date printed: 2026-01-01",
                "rationale": "Related ID was not supplied",
            },
            {
                "document_id": "outside-packet",
                "kind": "validation_amendment",
                "field": "header.date",
                "proposed_value": "2026-01-01",
                "related_document_ids": [],
                "evidence_quote": "Not eligible",
                "rationale": "Must be rejected",
            },
            {
                "document_id": "a",
                "kind": "validation_amendment",
                "field": "header.date",
                "proposed_value": "2026-01-01",
                "related_document_ids": [],
                "evidence_quote": "a",
                "rationale": "A provenance ID is not visible source evidence",
            },
        ]
    }
    primary_client = Client([primary])
    buddy_client = Client([{"decisions": []}])
    output = lane.run(
        records,
        [findings],
        tmp_path / "out.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        primary_client,
        buddy_client,
        "primary",
        "buddy",
        "openai",
        "openrouter",
        context,
        batch_size=1,
        max_batches=1,
        max_context_bytes=10000,
        effort="medium",
        retries=0,
        backoff=0,
        max_backoff=1,
    )
    assert output["proposal_only"] is True
    assert output["client_review_context_sha256"] == lane.reasoning_context(context)["sha256"]
    assert output["proposals"][0]["buddy_status"] == "unsupported"
    assert output["proposals"][0]["source_exception_ids"] == ["exception-000000"]
    assert len(output["rejected_proposals"]) == 3
    assert {item["reason"] for item in output["rejected_proposals"]} == {
        "proposal_document_outside_packet",
        "proposal_related_document_outside_packet",
        "proposal_evidence_quote_not_in_packet",
    }
    assert json.loads((tmp_path / "exceptions.json").read_text())["exceptions"] == []
    buddy_packet = json.loads(buddy_client.responses.requests[0]["input"][0]["content"][0]["text"])
    assert buddy_packet["exceptions"][0]["findings"][0]["reason"] == "invalid_date"
    assert buddy_packet["exceptions"][0]["exception_ids"] == ["exception-000000"]
    assert buddy_packet["client_review_context"]["reasoning_only"] is True

    with pytest.raises(ValueError, match="limits"):
        lane.run(
            records,
            [findings],
            tmp_path / "second.json",
            tmp_path / "second-exceptions.json",
            tmp_path / "second-raw",
            Client([]),
            Client([]),
            "primary",
            "buddy",
            "openai",
            "openrouter",
            batch_size=0,
            max_batches=-1,
            max_context_bytes=1,
            effort="medium",
            retries=0,
            backoff=0,
            max_backoff=1,
        )


def test_run_retains_overflow_invalid_and_provider_failures(tmp_path):
    records = write(tmp_path / "records.json", {"documents": [{"document_id": "a", "fields": {}}]})
    findings = write(tmp_path / "findings.json", {"exceptions": [{"document_id": "a"}]})

    class FailingClient:
        class Responses:
            def create(self, **kwargs):
                raise RuntimeError("provider failed")

        responses = Responses()

    overflow = lane.run(
        records,
        [findings],
        tmp_path / "overflow.json",
        tmp_path / "overflow-exceptions.json",
        tmp_path / "overflow-raw",
        FailingClient(),
        FailingClient(),
        "p",
        "b",
        "openai",
        "openrouter",
        batch_size=1,
        max_batches=1,
        max_context_bytes=1,
        effort="medium",
        retries=0,
        backoff=0,
        max_backoff=1,
    )
    assert overflow["summary"]["failed_batches"] == 0
    assert overflow["summary"]["exceptions"] == 1
    failed = lane.run(
        records,
        [findings],
        tmp_path / "failed.json",
        tmp_path / "failed-exceptions.json",
        tmp_path / "failed-raw",
        FailingClient(),
        FailingClient(),
        "p",
        "b",
        "openai",
        "openrouter",
        batch_size=1,
        max_batches=1,
        max_context_bytes=10000,
        effort="medium",
        retries=0,
        backoff=0,
        max_backoff=1,
    )
    assert failed["summary"]["failed_batches"] == 1
    retained = json.loads((tmp_path / "failed-exceptions.json").read_text())["exceptions"]
    assert retained[0]["source_exception_ids"] == ["exception-000000"]
    assert retained[0]["retry_findings"] == [{"document_id": "a"}]


def test_run_retains_each_item_deferred_by_batch_cap(tmp_path):
    records = write(
        tmp_path / "records.json",
        {"documents": [{"document_id": "a"}, {"document_id": "b"}]},
    )
    findings = write(
        tmp_path / "findings.json",
        {
            "exceptions": [
                {"document_id": "a"},
                {"document_id": "b"},
                {"document_id": "missing", "reason": "missing source"},
                {"reason": "identifier absent"},
            ]
        },
    )

    class Response:
        output_text = '{"proposals": []}'

        def model_dump(self, mode="json"):
            return {"output_text": self.output_text}

    class BuddyResponse(Response):
        output_text = '{"decisions": []}'

    class Client:
        class Responses:
            def __init__(self, response):
                self.response = response

            def create(self, **kwargs):
                return self.response

        def __init__(self, response):
            self.responses = self.Responses(response)

    output = lane.run(
        records,
        [findings],
        tmp_path / "out.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        Client(Response()),
        Client(BuddyResponse()),
        "p",
        "b",
        "openai",
        "openrouter",
        batch_size=1,
        max_batches=1,
        max_context_bytes=10000,
        effort="medium",
        retries=0,
        backoff=0,
        max_backoff=1,
    )
    assert output["summary"] == {
        "input_findings": 4,
        "matched_work_items": 2,
        "unmatched_findings": 2,
        "work_items": 4,
        "batches": 1,
        "successful_batches": 1,
        "failed_batches": 0,
        "exceptions": 3,
        "proposals": 0,
        "rejected_proposals": 0,
    }
    errors = json.loads((tmp_path / "exceptions.json").read_text())["exceptions"]
    assert [item["reason"] for item in errors] == [
        "exception_resolution_source_record_missing",
        "exception_resolution_source_record_missing",
        "exception_resolution_batch_cap_reached",
    ]
    assert errors[0]["source_document_ids"] == ["missing"]
    assert errors[0]["finding"]["reason"] == "missing source"
    assert errors[1]["source_document_ids"] == []
    assert errors[2]["source_document_ids"] == ["b"]
    assert errors[2]["work_items"] == 1


def test_main_disabled_and_enabled_paths(tmp_path, monkeypatch):
    records = write(tmp_path / "records.json", {"documents": []})
    exceptions = write(tmp_path / "exceptions.json", {"exceptions": []})
    common = [
        "program",
        str(records),
        "--exceptions",
        str(exceptions),
        "--out",
        str(tmp_path / "out.json"),
        "--exceptions-out",
        str(tmp_path / "errors.json"),
        "--raw-dir",
        str(tmp_path / "raw"),
    ]
    monkeypatch.setattr(sys, "argv", common)
    monkeypatch.setenv("CLIENT_REVIEW_EXCEPTION_RESOLUTION_ENABLED", "false")
    lane.main()
    assert json.loads((tmp_path / "out.json").read_text())["summary"]["enabled"] is False

    blocked_out = tmp_path / "blocked-out.json"
    blocked_errors = tmp_path / "blocked-errors.json"
    blocked_errors.write_text("retained")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "program",
            str(records),
            "--exceptions",
            str(exceptions),
            "--out",
            str(blocked_out),
            "--exceptions-out",
            str(blocked_errors),
            "--raw-dir",
            str(tmp_path / "blocked-raw"),
        ],
    )
    with pytest.raises(ValueError, match="Output already exists"):
        lane.main()
    assert not blocked_out.exists()
    assert blocked_errors.read_text() == "retained"

    monkeypatch.setattr(sys, "argv", [*common, "--enable"])
    monkeypatch.setenv("CLIENT_REVIEW_EXCEPTION_RESOLUTION_PRIMARY_PROVIDER", "openai")
    monkeypatch.setenv("CLIENT_REVIEW_EXCEPTION_RESOLUTION_BUDDY_PROVIDER", "openrouter")
    monkeypatch.setenv("CLIENT_REVIEW_EXCEPTION_RESOLUTION_PRIMARY_MODEL", "p")
    # A routed buddy needs a vendor-prefixed slug: independence is resolved to
    # the model vendor, so an unprefixed slug cannot be proven independent.
    monkeypatch.setenv("CLIENT_REVIEW_EXCEPTION_RESOLUTION_BUDDY_MODEL", "nvidia/b")
    monkeypatch.setattr(lane, "build_reviewer_client", lambda *args: object())
    monkeypatch.setattr(lane, "run", lambda *args, **kwargs: {"summary": {"ok": True}})
    lane.main()


def test_worker_count_changes_speed_but_never_the_retained_artifact(tmp_path):
    """Batches may finish in any order; proposals must still land in batch order."""

    class Response:
        def __init__(self, value):
            self.output_text = json.dumps(value)

        def model_dump(self, mode="json"):
            return {"output_text": self.output_text}

    class UniformClient:
        """Answer from the request, never by call order."""

        def __init__(self, value):
            self.responses = self
            self.value = value
            self.calls = 0

        def create(self, **kwargs):
            del kwargs
            self.calls += 1
            return Response(self.value)

    records = write(
        tmp_path / "records.json",
        {
            "documents": [
                {
                    "document_id": name,
                    "fields": {"header.date": {"value": "Date printed: 2026-01-01"}},
                }
                for name in ("a", "b", "c", "d")
            ]
        },
    )
    findings = write(
        tmp_path / "findings.json",
        {
            "exceptions": [
                {"document_id": name, "reason": "invalid_date", "field": "header.date"}
                for name in ("a", "b", "c", "d")
            ]
        },
    )
    context = write(
        tmp_path / "context.json",
        {
            "artifact_type": "client_review_context_v1",
            "reasoning_only": True,
            "independent_consensus_input": False,
            "policy": {"client_comments_are_untrusted_context": True},
            "client_comments": [],
            "pilot_records": [],
        },
    )
    primary = {"proposals": []}
    buddy = {"decisions": []}

    def resolve(tag, workers):
        return lane.run(
            records,
            [findings],
            tmp_path / f"{tag}-out.json",
            tmp_path / f"{tag}-exceptions.json",
            tmp_path / f"{tag}-raw",
            UniformClient(primary),
            UniformClient(buddy),
            "primary",
            "buddy",
            "openai",
            "openrouter",
            context,
            batch_size=1,
            max_batches=0,
            max_context_bytes=10000,
            effort="medium",
            retries=0,
            backoff=0,
            max_backoff=1,
            workers=workers,
        )

    serial = resolve("serial", 1)
    concurrent = resolve("concurrent", 4)

    assert len(serial["batches"]) > 1, "the fixture must produce more than one batch"
    assert [item["batch"] for item in concurrent["batches"]] == [
        item["batch"] for item in serial["batches"]
    ]
    assert concurrent["proposals"] == serial["proposals"]
    assert concurrent["rejected_proposals"] == serial["rejected_proposals"]
