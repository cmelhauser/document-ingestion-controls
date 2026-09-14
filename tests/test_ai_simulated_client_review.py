"""Tests for the explicitly non-authoritative simulated-client review lane."""

import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

lane = importlib.import_module("client_review.simulated")


def write(path, value):
    path.write_text(json.dumps(value))
    return path


def evidence(tmp_path):
    path = write(
        tmp_path / "evidence.json",
        {
            "records": [
                {
                    "document_id": "doc-1",
                    "page_id": "page-1",
                    "text": "Simple note",
                    "value": "Simple note",
                },
                {"document_id": "doc-2", "page_id": "page-2", "text": "100.00", "value": "100.00"},
            ]
        },
    )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, [f"source:{digest}:0", f"source:{digest}:1"]


def queue(cards=True):
    result = {
        "items": [
            {
                "document_id": "doc-1",
                "page_id": "page-1",
                "field": "formatting_note",
                "reason": "formatting_review",
                "value": "Simple note",
            },
            {
                "document_id": "doc-2",
                "page_id": "page-2",
                "field": "invoice_total",
                "reason": "arithmetic_exception",
                "value": "100.00",
            },
        ],
        "review_cards": [
            {"card_id": "document:doc-1", "item_indexes": [0]},
            {"card_id": "document:doc-2", "item_indexes": [1]},
        ],
    }
    if not cards:
        result["review_cards"] = []
    return result


def decision(
    card_id,
    item_id,
    recommendation="propose_accept_as_simulated_client",
    quote="Simple note",
    ref="source:test:0",
):
    return {
        "card_id": card_id,
        "rationale": "The source-backed item has an explicit recommendation.",
        "item_decisions": [
            {
                "item_id": item_id,
                "recommendation": recommendation,
                "confidence": 0.9,
                "rationale": "The evidence is visible in the review item.",
                "evidence_quote": quote,
                "evidence_refs": [ref],
            }
        ],
    }


class Response:
    def __init__(self, value):
        self.output_text = json.dumps(value)

    def model_dump(self, mode="json"):
        return {"id": "simulated-review", "output": self.output_text}


class Responses:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class Client:
    def __init__(self, outcomes):
        self.responses = Responses(outcomes)


def paths(tmp_path):
    return (
        tmp_path / "comments.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        tmp_path / "comments.csv",
        tmp_path / "comments.xlsx",
    )


def test_queue_context_packet_and_contract_boundaries(tmp_path):
    source = write(tmp_path / "queue.json", queue())
    assert lane.load_queue(source)["items"][0]["document_id"] == "doc-1"
    assert lane.item_id(5) == "review-item-5"
    members = lane.card_items(lane.load_queue(source), queue()["review_cards"][0])
    members[0]["source_evidence"] = [{"ref": "source:test:0", "record": {"text": "Simple note"}}]
    assert members[0]["item_id"] == "review-item-0"
    for card, message in (
        ({"item_indexes": "bad"}, "integer list"),
        ({"item_indexes": []}, "at least"),
        ({"item_indexes": [0, 0]}, "repeat"),
        ({"item_indexes": [9]}, "invalid"),
    ):
        with pytest.raises(ValueError, match=message):
            lane.card_items(lane.load_queue(source), card)
    with pytest.raises(ValueError, match="items list"):
        lane.load_queue(write(tmp_path / "bad.json", {"review_cards": []}))
    with pytest.raises(ValueError, match="review_cards"):
        lane.load_queue(write(tmp_path / "bad-cards.json", {"items": []}))
    with pytest.raises(ValueError, match="items must"):
        lane.load_queue(write(tmp_path / "bad-items.json", {"items": ["x"], "review_cards": []}))
    with pytest.raises(ValueError, match="cards must"):
        lane.load_queue(
            write(tmp_path / "bad-card-item.json", {"items": [], "review_cards": ["x"]})
        )
    context = write(tmp_path / "context.json", {"message": "context value"})
    summary = lane.context_summary([context], 100, 10)
    assert summary[0]["truncated"]
    assert summary[0]["reasoning_only"]
    omitted = lane.context_summary([context, context], 100, 1)
    assert omitted[1]["included"] is False
    assert omitted[1]["omitted_reason"] == "packet_context_cap_reached"
    assert (
        lane.packet(queue()["review_cards"][0], members, summary)["authorization_boundary"][
            "client_authorization"
        ]
        is False
    )
    with pytest.raises(ValueError, match="limits"):
        lane.context_summary([], 0, 1)
    with pytest.raises(ValueError, match="exceeds"):
        lane.context_summary([context], 1, 100)
    allowed = {"review-item-0"}
    source_text = lane.source_text([members[0]["source_evidence"][0]])
    valid = decision("document:doc-1", "review-item-0")["item_decisions"][0]
    cases = [
        ("bad", "decision_not_object"),
        ({**valid, "item_id": "other"}, "decision_item_outside_card"),
        ({**valid, "recommendation": "bad"}, "decision_recommendation_invalid"),
        ({**valid, "confidence": True}, "decision_confidence_invalid"),
        ({**valid, "rationale": ""}, "decision_rationale_missing"),
        ({**valid, "evidence_quote": "missing"}, "decision_evidence_quote_not_in_packet"),
        ({**valid, "evidence_refs": []}, "decision_evidence_refs_invalid"),
        ({**valid, "evidence_refs": ["bad"]}, "decision_evidence_ref_outside_packet"),
        (valid, None),
    ]
    assert [
        lane.rejection_reason(value, allowed, source_text, {"source:test:0"}) for value, _ in cases
    ] == [expected for _, expected in cases]
    assert "not client authorization" in lane.comment_text(
        lane.LABEL_DEFAULT, "abstain", "why", "quote"
    )
    assert lane.raw_name(1, {"card_id": "a / b"}).startswith("000001_a___b")


def test_run_retains_comments_protection_and_views(tmp_path, monkeypatch):
    queue_path = write(tmp_path / "queue.json", queue())
    evidence_path, refs = evidence(tmp_path)
    context = write(tmp_path / "context.json", {"client_comments": ["retained only"]})
    output_path, exceptions_path, raw_dir, csv_path, xlsx_path = paths(tmp_path)
    client = Client(
        [
            Response(decision("document:doc-1", "review-item-0", ref=refs[0])),
            Response(decision("document:doc-2", "review-item-1", quote="100.00", ref=refs[1])),
        ]
    )
    monkeypatch.setenv("LLM_FAIL_FAST_ON_PROVIDER_QUOTA", "false")
    output = lane.run(
        queue_path,
        [context],
        output_path,
        exceptions_path,
        raw_dir,
        csv_path,
        xlsx_path,
        client,
        provider="openai",
        model="gpt-5.6-luna",
        effort="high",
        retries=0,
        backoff=1,
        max_backoff=2,
        max_cards=0,
        max_context_bytes=10000,
        max_context_artifact_bytes=10000,
        label=lane.LABEL_DEFAULT,
        evidence_paths=[evidence_path],
    )
    assert output["summary"] == {
        "input_items": 2,
        "input_cards": 2,
        "scoped_cards": 2,
        "scoped_items": 2,
        "comments": 2,
        "rejected_model_decisions": 0,
        "exceptions": 0,
        "authorized_items": 0,
        "applied_changes": 0,
    }
    assert output["comments"][0]["simulation_status"] == "simulated_client_proposal"
    assert output["comments"][1]["simulation_status"] == "protected_review_required"
    assert output["comments"][1]["simulated_client_recommendation"] == "needs_human_review"
    assert output["client_authorization"] is False
    assert len(list(raw_dir.glob("0*.json"))) == 2
    assert csv_path.read_text().startswith("review_item_id")
    assert xlsx_path.read_bytes().startswith(b"PK")
    assert json.loads(exceptions_path.read_text())["exceptions"] == []
    assert client.responses.calls[0]["model"] == "gpt-5.6-luna"


def test_run_retains_invalid_provider_capped_and_uncovered_items(tmp_path, monkeypatch):
    queue_path = write(tmp_path / "queue.json", queue())
    evidence_path, _ = evidence(tmp_path)
    output_path, exceptions_path, raw_dir, csv_path, xlsx_path = paths(tmp_path)
    bad = decision("document:doc-1", "review-item-0", quote="missing")
    client = Client([Response(bad)])
    monkeypatch.setenv("LLM_FAIL_FAST_ON_PROVIDER_QUOTA", "false")
    output = lane.run(
        queue_path,
        [],
        output_path,
        exceptions_path,
        raw_dir,
        csv_path,
        xlsx_path,
        client,
        provider="openai",
        model="gpt-5.6-sol",
        effort="high",
        retries=0,
        backoff=1,
        max_backoff=2,
        max_cards=1,
        max_context_bytes=10000,
        max_context_artifact_bytes=10000,
        label=lane.LABEL_DEFAULT,
        evidence_paths=[evidence_path],
    )
    assert output["summary"]["comments"] == 1
    assert output["summary"]["rejected_model_decisions"] == 1
    assert output["comments"][0]["simulation_status"] == "invalid_or_missing_model_decision"
    reasons = {item["reason"] for item in json.loads(exceptions_path.read_text())["exceptions"]}
    assert "simulated_review_card_cap_reached" in reasons
    with pytest.raises(ValueError, match="not client authorization"):
        lane.run(
            queue_path,
            [],
            tmp_path / "a.json",
            tmp_path / "b.json",
            tmp_path / "r",
            tmp_path / "c.csv",
            tmp_path / "d.xlsx",
            client,
            provider="openai",
            model="m",
            effort="high",
            retries=0,
            backoff=1,
            max_backoff=1,
            max_cards=0,
            max_context_bytes=1,
            max_context_artifact_bytes=1,
            label="unsafe",
        )


def test_run_retains_invalid_card_oversized_and_provider_failure(tmp_path, monkeypatch):
    source = queue()
    source["review_cards"] = [{"card_id": "bad", "item_indexes": [9]}, *source["review_cards"]]
    queue_path = write(tmp_path / "queue.json", source)
    evidence_path, _ = evidence(tmp_path)
    output_path, exceptions_path, raw_dir, csv_path, xlsx_path = paths(tmp_path)
    client = Client([RuntimeError("provider failed")])
    monkeypatch.setenv("LLM_FAIL_FAST_ON_PROVIDER_QUOTA", "false")
    output = lane.run(
        queue_path,
        [],
        output_path,
        exceptions_path,
        raw_dir,
        csv_path,
        xlsx_path,
        client,
        provider="openai",
        model="m",
        effort="high",
        retries=0,
        backoff=1,
        max_backoff=1,
        max_cards=0,
        max_context_bytes=10000,
        max_context_artifact_bytes=10000,
        label=lane.LABEL_DEFAULT,
        evidence_paths=[evidence_path],
    )
    assert output["summary"]["comments"] == 0
    reasons = {item["reason"] for item in json.loads(exceptions_path.read_text())["exceptions"]}
    assert {"invalid_review_card", "simulated_review_provider_or_schema_failure"} <= reasons
    assert not list(raw_dir.glob("0*.json"))
    oversized_paths = paths(tmp_path / "oversized")
    (tmp_path / "oversized").mkdir()
    oversized_evidence, _ = evidence(tmp_path / "oversized")
    output = lane.run(
        write(
            tmp_path / "small.json",
            {
                "items": [{"document_id": "doc-1", "page_id": "page-1", "value": "x" * 100}],
                "review_cards": [{"card_id": "small", "item_indexes": [0]}],
            },
        ),
        [],
        *oversized_paths,
        client=Client([]),
        provider="openai",
        model="m",
        effort="high",
        retries=0,
        backoff=1,
        max_backoff=1,
        max_cards=0,
        max_context_bytes=100,
        max_context_artifact_bytes=10000,
        label=lane.LABEL_DEFAULT,
        evidence_paths=[oversized_evidence],
    )
    assert output["summary"]["exceptions"] == 1

    response_paths = paths(tmp_path / "wrong-response")
    (tmp_path / "wrong-response").mkdir()
    response_evidence, _ = evidence(tmp_path / "wrong-response")
    response_output = lane.run(
        write(
            tmp_path / "one-card.json",
            {"items": queue()["items"][:1], "review_cards": queue()["review_cards"][:1]},
        ),
        [],
        *response_paths,
        client=Client([Response(decision("wrong-card", "review-item-0"))]),
        provider="openai",
        model="m",
        effort="high",
        retries=0,
        backoff=1,
        max_backoff=1,
        max_cards=0,
        max_context_bytes=10000,
        max_context_artifact_bytes=10000,
        label=lane.LABEL_DEFAULT,
        evidence_paths=[response_evidence],
    )
    assert response_output["summary"]["comments"] == 0
    assert json.loads(response_paths[1].read_text())["exceptions"][0]["error_type"] == "ValueError"

    mismatch_paths = paths(tmp_path / "mismatch")
    (tmp_path / "mismatch").mkdir()
    mismatch = lane.run(
        write(tmp_path / "mismatch-queue.json", queue(False)),
        [],
        *mismatch_paths,
        client=Client([]),
        provider="openai",
        model="m",
        effort="high",
        retries=0,
        backoff=1,
        max_backoff=1,
        max_cards=0,
        max_context_bytes=100,
        max_context_artifact_bytes=100,
        label=lane.LABEL_DEFAULT,
    )
    assert mismatch["summary"]["exceptions"] == 1
    assert json.loads(mismatch_paths[1].read_text())["exceptions"][0]["reason"] == (
        "simulated_review_items_not_covered_by_cards"
    )


def test_disabled_and_request_helpers(tmp_path, monkeypatch):
    output_path, exceptions_path, raw_dir, csv_path, xlsx_path = paths(tmp_path)
    result = lane.disabled(output_path, exceptions_path, raw_dir, csv_path, xlsx_path)
    assert result["summary"]["enabled"] is False
    assert xlsx_path.read_bytes().startswith(b"PK")
    with pytest.raises(ValueError, match="review limits"):
        lane.run(
            write(tmp_path / "queue.json", queue(False)),
            [],
            tmp_path / "one",
            tmp_path / "two",
            tmp_path / "three",
            tmp_path / "four",
            tmp_path / "five",
            Client([]),
            provider="openai",
            model="m",
            effort="high",
            retries=0,
            backoff=1,
            max_backoff=1,
            max_cards=-1,
            max_context_bytes=1,
            max_context_artifact_bytes=1,
            label=lane.LABEL_DEFAULT,
        )
    monkeypatch.setattr(lane, "retry_call", lambda function, *args, **kwargs: function())
    client = Client([Response(decision("a", "review-item-0"))])
    response = lane.request(client, "openai", "gpt-5.6-luna", "high", {}, 0, 1, 1)
    assert response.output_text


def test_evidence_validation_and_duplicate_protection(tmp_path):
    with pytest.raises(ValueError, match="provider must"):
        lane.validate_config("bad", "m", "high", 0, 1, 1)
    with pytest.raises(ValueError, match="non-empty"):
        lane.validate_config("openai", "", "high", 0, 1, 1)
    with pytest.raises(ValueError, match="reasoning"):
        lane.validate_config("openai", "m", "bad", 0, 1, 1)
    with pytest.raises(ValueError, match="retries"):
        lane.validate_config("openai", "m", "high", -1, 1, 1)
    with pytest.raises(ValueError, match="backoff"):
        lane.validate_config("openai", "m", "high", 0, 2, 1)
    with pytest.raises(ValueError, match="recognized"):
        lane._records({"unknown": {}})
    with pytest.raises(ValueError, match="recognized"):
        lane._records("not-an-object")
    assert lane._records({"documents": "not-a-list", "items": []}) == []
    with pytest.raises(ValueError, match="recognized"):
        lane._records(
            {
                key: "not-a-list"
                for key in ("documents", "records", "results", "pages", "evidence", "items")
            }
        )
    with pytest.raises(ValueError, match="positive"):
        lane.load_source_evidence([], 0)
    plain = write(tmp_path / "plain.json", [{"document_id": "d", "text": "quote"}])
    with pytest.raises(ValueError, match="not an object"):
        lane.load_source_evidence([write(tmp_path / "bad-evidence.json", {"records": [1]})], 1000)
    with pytest.raises(ValueError, match="exceeds"):
        lane.load_source_evidence([plain], 1)
    metadata, records = lane.load_source_evidence([plain], 1000)
    assert metadata[0]["record_count"] == 1
    assert lane.evidence_for_item({"document_id": "d", "page_id": "x"}, records)
    assert lane.evidence_for_item({"document_id": "other", "page_id": "x"}, records) == []
    members = [
        {
            "item_id": "review-item-0",
            "item": {"document_id": "d", "reason": "formatting_review"},
            "source_evidence": records,
        }
    ]
    valid = decision("c", "review-item-0", quote="quote", ref=records[0]["ref"])["item_decisions"]
    with pytest.raises(ValueError, match="item_decisions"):
        lane.reduce_card(members, {}, lane.LABEL_DEFAULT, "hash")
    comments, rejected = lane.reduce_card(members, valid + valid, lane.LABEL_DEFAULT, "hash")
    assert comments[0]["simulation_status"] == "duplicate_model_decision"
    assert any(item["reason"] == "duplicate_model_decision" for item in rejected)
    lane.reduce_card(members, [{"item_id": "foreign"}], lane.LABEL_DEFAULT, "hash")


def test_item_evidence_is_isolated_and_packet_records_are_deduplicated(tmp_path):
    source, refs = evidence(tmp_path)
    _, records = lane.load_source_evidence([source], 10000)
    members = [
        {
            "item_id": "review-item-0",
            "item": {
                "document_id": "doc-1",
                "page_id": "page-1",
                "reason": "formatting_review",
            },
            "source_evidence": [records[0]],
        },
        {
            "item_id": "review-item-1",
            "item": {
                "document_id": "doc-2",
                "page_id": "page-2",
                "reason": "formatting_review",
            },
            "source_evidence": [records[1]],
        },
    ]
    body = lane.packet({"card_id": "mixed", "item_indexes": [0, 1]}, members, [])
    assert len(body["source_evidence"]) == 2
    assert body["review_items"][0]["source_evidence_refs"] == [refs[0]]
    assert "source_evidence" not in body["review_items"][0]

    cross_item = decision("mixed", "review-item-0", quote="100.00", ref=refs[1])["item_decisions"]
    comments, rejected = lane.reduce_card(members, cross_item, lane.LABEL_DEFAULT, "hash")
    assert comments[0]["simulation_status"] == "invalid_or_missing_model_decision"
    assert rejected[0]["reason"] == "decision_evidence_ref_outside_item"

    valid = decision("mixed", "review-item-0", quote="Simple note", ref=refs[0])["item_decisions"]
    comments, rejected = lane.reduce_card(members, valid, lane.LABEL_DEFAULT, "hash")
    assert all(item.get("review_item_id") != "review-item-0" for item in rejected)
    assert comments[0]["evidence_locations"][0]["json_pointer"] in {"/text", "/value"}


def test_context_limit_is_utf8_bytes(tmp_path):
    context = write(tmp_path / "unicode.json", {"message": "漢字漢字"})
    prefix = len(b'{"message":"')
    summary = lane.context_summary([context], 1000, prefix + 1)
    assert len(summary[0]["json"].encode("utf-8")) <= prefix + 1
    assert summary[0]["truncated"] is True
    assert list(lane.source_scalar_locations({"a/b": [None, "value"]})) == [("/a~1b/1", "value")]
    reason, locations = lane.item_evidence_validation(
        {
            "item_id": "review-item-0",
            "evidence_quote": "absent",
            "evidence_refs": ["source:test:0"],
        },
        {"review-item-0": [{"ref": "source:test:0", "record": {"text": "visible"}}]},
    )
    assert reason == "decision_evidence_quote_not_in_cited_source"
    assert locations == []


def test_retry_scope_selects_only_recoverable_card_exceptions(tmp_path):
    manifest = write(
        tmp_path / "exceptions.json",
        {
            "artifact_type": "ai_simulated_client_review_exceptions_v1",
            "exceptions": [
                {"card_id": "provider", "reason": "simulated_review_provider_or_schema_failure"},
                {"card_id": "large", "reason": "simulated_review_card_too_large"},
                {"card_id": "missing", "reason": "source_evidence_missing"},
                {"card_id": "audit", "reason": "simulated_review_evidence_audit_failure"},
                {"card_id": "ignored", "reason": "invalid_review_card"},
            ],
        },
    )
    assert lane.retry_card_ids(manifest) == {"provider", "large", "missing", "audit"}
    with pytest.raises(ValueError, match="exception artifact"):
        lane.retry_card_ids(write(tmp_path / "bad-retry.json", []))


def test_checkpoint_resume_reuses_completed_cards(tmp_path, monkeypatch):
    queue_path = write(tmp_path / "queue.json", queue())
    evidence_path, refs = evidence(tmp_path)
    output_path, exceptions_path, raw_dir, csv_path, xlsx_path = paths(tmp_path)
    first_client = Client(
        [
            Response(decision("document:doc-1", "review-item-0", ref=refs[0])),
            KeyboardInterrupt(),
        ]
    )
    monkeypatch.setenv("LLM_FAIL_FAST_ON_PROVIDER_QUOTA", "false")
    with pytest.raises(KeyboardInterrupt):
        lane.run(
            queue_path,
            [],
            output_path,
            exceptions_path,
            raw_dir,
            csv_path,
            xlsx_path,
            first_client,
            provider="openai",
            model="gpt-5.6-luna",
            effort="high",
            retries=0,
            backoff=1,
            max_backoff=2,
            max_cards=0,
            max_context_bytes=10000,
            max_context_artifact_bytes=10000,
            label=lane.LABEL_DEFAULT,
            evidence_paths=[evidence_path],
        )
    checkpoint = json.loads((raw_dir / "checkpoint.json").read_text())
    assert checkpoint["completed_card_indexes"] == [0]

    with pytest.raises(ValueError, match="does not match"):
        lane.run(
            queue_path,
            [],
            output_path,
            exceptions_path,
            raw_dir,
            csv_path,
            xlsx_path,
            Client([]),
            provider="openai",
            model="different-model",
            effort="high",
            retries=0,
            backoff=1,
            max_backoff=2,
            max_cards=0,
            max_context_bytes=10000,
            max_context_artifact_bytes=10000,
            label=lane.LABEL_DEFAULT,
            evidence_paths=[evidence_path],
            resume=True,
        )

    resumed = lane.run(
        queue_path,
        [],
        output_path,
        exceptions_path,
        raw_dir,
        csv_path,
        xlsx_path,
        Client(
            [Response(decision("document:doc-2", "review-item-1", quote="100.00", ref=refs[1]))]
        ),
        provider="openai",
        model="gpt-5.6-luna",
        effort="high",
        retries=0,
        backoff=1,
        max_backoff=2,
        max_cards=0,
        max_context_bytes=10000,
        max_context_artifact_bytes=10000,
        label=lane.LABEL_DEFAULT,
        evidence_paths=[evidence_path],
        resume=True,
    )
    assert resumed["summary"]["comments"] == 2
    assert resumed["resolved_configuration"]["max_context_bytes"] == 10000
    assert len(first_client.responses.calls) == 2
    assert (raw_dir / "checkpoint.json").exists()
    assert json.loads((raw_dir / "checkpoint.json").read_text())["status"] == "complete"

    missing_paths = paths(tmp_path / "no-checkpoint")
    (tmp_path / "no-checkpoint").mkdir()
    with pytest.raises(ValueError, match="requires a retained"):
        lane.run(
            queue_path,
            [],
            *missing_paths,
            client=Client([]),
            provider="openai",
            model="m",
            effort="high",
            retries=0,
            backoff=1,
            max_backoff=1,
            max_cards=0,
            max_context_bytes=10000,
            max_context_artifact_bytes=10000,
            label=lane.LABEL_DEFAULT,
            evidence_paths=[evidence_path],
            resume=True,
        )


def test_retry_overlay_processes_only_selected_cards(tmp_path, monkeypatch):
    queue_path = write(tmp_path / "queue.json", queue())
    evidence_path, refs = evidence(tmp_path)
    output_path, exceptions_path, raw_dir, csv_path, xlsx_path = paths(tmp_path)
    monkeypatch.setenv("LLM_FAIL_FAST_ON_PROVIDER_QUOTA", "false")
    output = lane.run(
        queue_path,
        [],
        output_path,
        exceptions_path,
        raw_dir,
        csv_path,
        xlsx_path,
        Client(
            [Response(decision("document:doc-2", "review-item-1", quote="100.00", ref=refs[1]))]
        ),
        provider="openai",
        model="m",
        effort="high",
        retries=0,
        backoff=1,
        max_backoff=1,
        max_cards=0,
        max_context_bytes=10000,
        max_context_artifact_bytes=10000,
        label=lane.LABEL_DEFAULT,
        evidence_paths=[evidence_path],
        only_card_ids={"document:doc-2"},
    )
    assert output["run_scope"] == "retry_overlay"
    assert output["summary"]["scoped_cards"] == 1
    assert [comment["review_item_id"] for comment in output["comments"]] == ["review-item-1"]

    unknown_paths = paths(tmp_path / "unknown-scope")
    (tmp_path / "unknown-scope").mkdir()
    with pytest.raises(ValueError, match="outside the final-review queue"):
        lane.run(
            queue_path,
            [],
            *unknown_paths,
            client=Client([]),
            provider="openai",
            model="m",
            effort="high",
            retries=0,
            backoff=1,
            max_backoff=1,
            max_cards=0,
            max_context_bytes=10000,
            max_context_artifact_bytes=10000,
            label=lane.LABEL_DEFAULT,
            evidence_paths=[evidence_path],
            only_card_ids={"not-in-queue"},
        )


def test_queue_duplicate_and_missing_source_are_explicit(tmp_path):
    duplicate = queue()
    duplicate["review_cards"] = [
        {"card_id": "a", "item_indexes": [0]},
        {"card_id": "b", "item_indexes": [0]},
    ]
    with pytest.raises(ValueError, match="more than one"):
        lane.load_queue(write(tmp_path / "duplicate.json", duplicate))
    with pytest.raises(ValueError, match="non-negative"):
        lane.load_queue(
            write(
                tmp_path / "negative.json",
                {"items": [{}], "review_cards": [{"item_indexes": [-1]}]},
            )
        )
    assert lane.load_queue(
        write(tmp_path / "unindexed.json", {"items": [{}], "review_cards": [{}]})
    )
    source = write(tmp_path / "queue.json", queue(False))
    output_path, exceptions_path, raw_dir, csv_path, xlsx_path = paths(tmp_path)
    result = lane.run(
        source,
        [],
        output_path,
        exceptions_path,
        raw_dir,
        csv_path,
        xlsx_path,
        Client([]),
        provider="openai",
        model="m",
        effort="high",
        retries=0,
        backoff=1,
        max_backoff=1,
        max_cards=0,
        max_context_bytes=100,
        max_context_artifact_bytes=1000,
        label=lane.LABEL_DEFAULT,
        evidence_paths=[],
        context_reserve_bytes=10,
    )
    assert result["summary"]["exceptions"] == 1
    assert lane.source_text([None]) == ""
    missing = queue()
    missing["items"][0]["document_id"] = "not-in-evidence"
    missing_result = lane.run(
        write(tmp_path / "missing.json", missing),
        [],
        tmp_path / "missing-out.json",
        tmp_path / "missing-exceptions.json",
        tmp_path / "missing-raw",
        tmp_path / "missing.csv",
        tmp_path / "missing.xlsx",
        Client([]),
        provider="openai",
        model="m",
        effort="high",
        retries=0,
        backoff=1,
        max_backoff=1,
        max_cards=1,
        max_context_bytes=1000,
        max_context_artifact_bytes=1000,
        label=lane.LABEL_DEFAULT,
        evidence_paths=[
            write(
                tmp_path / "one-record.json",
                {"records": [{"document_id": "doc-2", "page_id": "page-2", "text": "100.00"}]},
            )
        ],
    )
    assert missing_result["summary"]["exceptions"] >= 1

    mixed = queue()
    mixed["review_cards"] = [{"card_id": "mixed", "item_indexes": [0, 1]}]
    mixed_evidence = write(
        tmp_path / "mixed-evidence.json",
        {"records": [{"document_id": "doc-2", "page_id": "page-2", "text": "100.00"}]},
    )
    mixed_digest = hashlib.sha256(mixed_evidence.read_bytes()).hexdigest()
    mixed_result = lane.run(
        write(tmp_path / "mixed-queue.json", mixed),
        [],
        tmp_path / "mixed-out.json",
        tmp_path / "mixed-exceptions.json",
        tmp_path / "mixed-raw",
        tmp_path / "mixed.csv",
        tmp_path / "mixed.xlsx",
        Client(
            [
                Response(
                    decision(
                        "mixed",
                        "review-item-1",
                        quote="100.00",
                        ref=f"source:{mixed_digest}:0",
                    )
                )
            ]
        ),
        provider="openai",
        model="m",
        effort="high",
        retries=0,
        backoff=1,
        max_backoff=1,
        max_cards=0,
        max_context_bytes=10000,
        max_context_artifact_bytes=10000,
        label=lane.LABEL_DEFAULT,
        evidence_paths=[mixed_evidence],
    )
    assert mixed_result["summary"]["comments"] == 1


def test_main_respects_enablement_configuration_and_validates_provider(tmp_path, monkeypatch):
    common = [
        "ai_simulated_client_review.py",
        "queue.json",
        "--out",
        str(tmp_path / "out.json"),
        "--exceptions-out",
        str(tmp_path / "exceptions.json"),
        "--raw-dir",
        str(tmp_path / "raw"),
        "--comments-csv",
        str(tmp_path / "comments.csv"),
        "--comments-xlsx",
        str(tmp_path / "comments.xlsx"),
    ]
    monkeypatch.setattr(lane, "load_project_env", lambda: None)
    disabled = []
    monkeypatch.setattr(lane, "disabled", lambda *args: disabled.append(args))
    monkeypatch.setattr(lane, "env_bool", lambda *args: False)
    monkeypatch.setattr(sys, "argv", common)
    lane.main()
    assert len(disabled) == 1

    monkeypatch.setattr(lane, "env_bool", lambda *args: True)
    monkeypatch.setattr(
        lane, "env_value", lambda key, default: "invalid" if key.endswith("PROVIDER") else default
    )
    with pytest.raises(ValueError, match="must be one of: anthropic, google, openai, openrouter"):
        lane.main()

    calls = []
    monkeypatch.setattr(lane, "env_value", lambda key, default: default)
    monkeypatch.setattr(lane, "env_int", lambda key, default: default)
    monkeypatch.setattr(lane, "env_float", lambda key, default: default)
    monkeypatch.setattr(lane, "provider_credential_env", lambda provider: "OPENAI_API_KEY")
    monkeypatch.setattr(lane, "build_reviewer_client", lambda *args: "client")
    monkeypatch.setattr(
        lane,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"summary": {"ok": True}},
    )
    monkeypatch.setattr(sys, "argv", [*common, "--enable"])
    lane.main()
    assert calls[0][1]["model"] == "gpt-5.6-luna"

    calls.clear()
    monkeypatch.setattr(
        lane,
        "env_int",
        lambda key, default: (
            100
            if key
            in {
                "AI_SIMULATED_CLIENT_REVIEW_MAX_CONTEXT_BYTES",
                "AI_SIMULATED_CLIENT_REVIEW_CONTEXT_RESERVE_BYTES",
            }
            else default
        ),
    )
    lane.main()
    assert calls[0][1]["context_reserve_bytes"] == 12

    empty_retry = write(
        tmp_path / "empty-retry.json",
        {"artifact_type": "ai_simulated_client_review_exceptions_v1", "exceptions": []},
    )
    monkeypatch.setattr(sys, "argv", [*common, "--enable", "--retry-exceptions", str(empty_retry)])
    with pytest.raises(ValueError, match="no recoverable"):
        lane.main()


# Measured against the packet this queue actually builds: three of its six
# items fit one request and four do not, so the card must become two packets.
SPLIT_BUDGET = 1300


def split_queue(items):
    """One card holding many items, so its packet cannot fit a single request."""
    return {
        "items": [
            {
                "document_id": "doc-1",
                "page_id": "page-1",
                "field": f"field_{number}",
                "reason": "formatting_review",
                "value": "Simple note",
            }
            for number in range(items)
        ],
        "review_cards": [{"card_id": "document:doc-1", "item_indexes": list(range(items))}],
    }


def test_oversized_card_is_split_into_packets_not_discarded(tmp_path):
    source_evidence, _ = evidence(tmp_path)
    queue_path = write(tmp_path / "split-queue.json", split_queue(6))
    members = [
        {"item_id": lane.item_id(index), "item": item, "source_evidence": []}
        for index, item in enumerate(split_queue(6)["items"])
    ]
    card = split_queue(6)["review_cards"][0]
    whole = len(json.dumps(lane.packet(card, members, [])).encode())
    parts, oversized, _ = lane.card_parts(card, members, [], whole // 2)
    assert len(parts) > 1
    assert not oversized
    assert [entry["item_id"] for part in parts for entry in part] == [
        entry["item_id"] for entry in members
    ]

    # The same card through the lane: every item is still reviewed, across as
    # many requests as the budget needs, and nothing is dropped as too large.
    client = Client(
        [
            Response(
                {
                    "card_id": "document:doc-1",
                    "rationale": "Each packet is reviewed on its own source evidence.",
                    "item_decisions": [],
                }
            )
            for _ in range(6)
        ]
    )
    output_path, exceptions_path, raw_dir, csv_path, xlsx_path = paths(tmp_path)
    output = lane.run(
        queue_path,
        [],
        output_path,
        exceptions_path,
        raw_dir,
        csv_path,
        xlsx_path,
        client=client,
        provider="openai",
        model="m",
        effort="high",
        retries=0,
        backoff=1,
        max_backoff=1,
        max_cards=0,
        max_context_bytes=SPLIT_BUDGET,
        max_context_artifact_bytes=10000,
        label=lane.LABEL_DEFAULT,
        evidence_paths=[source_evidence],
    )
    assert len(client.responses.calls) > 1
    assert output["summary"]["comments"] == 6
    reasons = {item["reason"] for item in json.loads(exceptions_path.read_text())["exceptions"]}
    assert "simulated_review_card_too_large" not in reasons
    names = sorted(path.name for path in raw_dir.iterdir() if path.name != "checkpoint.json")
    assert names == [
        "000000_document_doc-1.part01.json",
        "000000_document_doc-1.part02.json",
    ]
    checkpoint = json.loads((raw_dir / "checkpoint.json").read_text())
    assert checkpoint["completed_packet_parts"] == ["0:1", "0:2"]


def test_retained_packet_part_is_not_re_sent_on_resume(tmp_path):
    source_evidence, _ = evidence(tmp_path)
    queue_path = write(tmp_path / "resume-queue.json", split_queue(6))
    answer = {
        "card_id": "document:doc-1",
        "rationale": "Each packet is reviewed on its own source evidence.",
        "item_decisions": [],
    }
    arguments = {
        "provider": "openai",
        "model": "m",
        "effort": "high",
        "retries": 0,
        "backoff": 1,
        "max_backoff": 1,
        "max_cards": 0,
        "max_context_bytes": SPLIT_BUDGET,
        "max_context_artifact_bytes": 10000,
        "label": lane.LABEL_DEFAULT,
        "evidence_paths": [source_evidence],
    }
    first_paths = paths(tmp_path / "first")
    (tmp_path / "first").mkdir()
    failing = Client([Response(answer), KeyboardInterrupt("interrupted")])
    with pytest.raises(KeyboardInterrupt, match="interrupted"):
        lane.run(queue_path, [], *first_paths, client=failing, **arguments)
    raw_dir = first_paths[2]
    retained = (raw_dir / "000000_document_doc-1.part01.json").read_bytes()
    assert json.loads((raw_dir / "checkpoint.json").read_text())["completed_packet_parts"] == [
        "0:1"
    ]

    # Resume writes new outputs beside the retained raw directory; the packet
    # that already answered must not be sent again or have its raw file rewritten.
    resumed_paths = (tmp_path / "resume.json", tmp_path / "resume-exceptions.json", raw_dir,
                     tmp_path / "resume.csv", tmp_path / "resume.xlsx")  # fmt: skip
    resuming = Client([Response(answer)])
    output = lane.run(queue_path, [], *resumed_paths, client=resuming, resume=True, **arguments)
    assert len(resuming.responses.calls) == 1
    assert (raw_dir / "000000_document_doc-1.part01.json").read_bytes() == retained
    assert output["summary"]["comments"] == 6


def test_context_cannot_crowd_the_card_out_of_its_own_packet(tmp_path):
    source_evidence, _ = evidence(tmp_path)
    queue_path = write(tmp_path / "reserve-queue.json", split_queue(2))
    # A context artifact far larger than the fixed 16000-byte reserve. Clipped
    # to the ceiling less that reserve, it left no room for the card and every
    # card overflowed before a request was made.
    context_path = write(tmp_path / "context.json", {"notes": ["x" * 400] * 200})
    client = Client(
        [
            Response(
                {
                    "card_id": "document:doc-1",
                    "rationale": "The packet still carries the card it is about.",
                    "item_decisions": [],
                }
            )
        ]
    )
    output = lane.run(
        queue_path,
        [context_path],
        *paths(tmp_path),
        client=client,
        provider="openai",
        model="m",
        effort="high",
        retries=0,
        backoff=1,
        max_backoff=1,
        max_cards=0,
        max_context_bytes=40000,
        max_context_artifact_bytes=1000000,
        label=lane.LABEL_DEFAULT,
        evidence_paths=[source_evidence],
        context_reserve_bytes=16000,
    )
    assert output["summary"]["comments"] == 2
    assert output["summary"]["exceptions"] == 0
    assert output["context"][0]["truncated"] is True
    assert len(client.responses.calls) == 1


def test_an_item_larger_than_the_whole_ceiling_is_still_retained(tmp_path):
    source_evidence, _ = evidence(tmp_path)
    queue_path = write(tmp_path / "tiny-queue.json", split_queue(2))
    output = lane.run(
        queue_path,
        [],
        *paths(tmp_path),
        client=Client([]),
        provider="openai",
        model="m",
        effort="high",
        retries=0,
        backoff=1,
        max_backoff=1,
        max_cards=0,
        max_context_bytes=200,
        max_context_artifact_bytes=10000,
        label=lane.LABEL_DEFAULT,
        evidence_paths=[source_evidence],
    )
    exceptions = json.loads((tmp_path / "exceptions.json").read_text())["exceptions"]
    assert exceptions[0]["reason"] == "simulated_review_card_too_large"
    assert exceptions[0]["packet_parts"] == 0
    assert output["summary"]["comments"] == 0


class CardKeyedClient:
    """Answer each request from the card inside it, never by call order.

    Concurrency is free to reorder calls, so a stub that pops replies off a list
    would hand one card another card's decision. Keying the reply to the request
    makes a crossed response fail rather than pass quietly.
    """

    def __init__(self, refs):
        self.responses = self
        self.refs = refs
        self.calls = 0

    def create(self, **kwargs):
        sent = json.loads(kwargs["input"][0]["content"][0]["text"])
        card_id = sent["card"]["card_id"]
        item_id = sent["review_items"][0]["item_id"]
        quote = "100.00" if card_id == "document:doc-2" else None
        ref = self.refs[0] if card_id == "document:doc-1" else self.refs[1]
        self.calls += 1
        if quote is None:
            return Response(decision(card_id, item_id, ref=ref))
        return Response(decision(card_id, item_id, quote=quote, ref=ref))


def test_worker_count_changes_speed_but_never_the_retained_artifact(tmp_path, monkeypatch):
    """Cards may finish in any order; comments and the checkpoint must not show it."""
    monkeypatch.setenv("LLM_FAIL_FAST_ON_PROVIDER_QUOTA", "false")
    queue_path = write(tmp_path / "queue.json", queue())
    evidence_path, refs = evidence(tmp_path)
    context = write(tmp_path / "context.json", {"client_comments": ["retained only"]})

    def review(tag, workers):
        out = tmp_path / f"{tag}-out.json"
        return lane.run(
            queue_path,
            [context],
            out,
            tmp_path / f"{tag}-exceptions.json",
            tmp_path / f"{tag}-raw",
            tmp_path / f"{tag}.csv",
            tmp_path / f"{tag}.xlsx",
            CardKeyedClient(refs),
            provider="openai",
            model="gpt-5.6-luna",
            effort="high",
            retries=0,
            backoff=1,
            max_backoff=2,
            max_cards=0,
            max_context_bytes=10000,
            max_context_artifact_bytes=10000,
            label=lane.LABEL_DEFAULT,
            evidence_paths=[evidence_path],
            workers=workers,
        )

    serial = review("serial", 1)
    concurrent = review("concurrent", 4)

    assert serial["summary"]["comments"] == 2
    assert concurrent["summary"] == serial["summary"]
    assert [item["review_item_id"] for item in concurrent["comments"]] == [
        item["review_item_id"] for item in serial["comments"]
    ]
    assert concurrent["comments"] == serial["comments"]
