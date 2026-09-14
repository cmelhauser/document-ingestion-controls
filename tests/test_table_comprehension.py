"""Tests for source-native table comprehension; all provider calls are faked."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

table = importlib.import_module("table_comprehension")
queue = importlib.import_module("client_review.queue")


def write(path, value):
    path.write_text(json.dumps(value) if not isinstance(value, str) else value)
    return path


def manifest(tmp_path):
    write(tmp_path / "page.pdf", "%PDF-test")
    return write(
        tmp_path / "ingestion_manifest.json", {"pages": [{"page_id": "p1", "page_pdf": "page.pdf"}]}
    )


def box():
    return {"left": 0.1, "top": 0.2, "right": 0.3, "bottom": 0.4}


def profile_payload():
    return {
        "document_family": "commission_statement",
        "table_regions": [
            {
                "region_id": "table-1",
                "box": box(),
                "headers": [
                    {
                        "source_label": "Commission",
                        "left": 0.1,
                        "right": 0.2,
                        "evidence_text": "Commission",
                    },
                    {
                        "source_label": "Dealer",
                        "left": 0.2,
                        "right": 0.3,
                        "evidence_text": "Dealer",
                    },
                    {
                        "source_label": "New Layer",
                        "left": 0.3,
                        "right": 0.4,
                        "evidence_text": "New Layer",
                    },
                ],
                "sections": ["North"],
                "totals": ["Total commission"],
                "review_flags": ["low contrast"],
            }
        ],
        "handwriting_regions": [{"region_id": "ink-1", "box": box(), "evidence_text": "note"}],
        "quality_diagnostics": ["skew"],
    }


def rows_payload():
    return {
        "rows": [
            {
                "source_row_id": "r1",
                "region_id": "table-1",
                "row_number": 1,
                "box": box(),
                "cells": [
                    {
                        "source_label": "Commission",
                        "visible_value": "12.50",
                        "box": box(),
                        "evidence_text": "12.50",
                    },
                    {
                        "source_label": "Dealer",
                        "visible_value": "Acme",
                        "box": box(),
                        "evidence_text": "Acme",
                    },
                    {
                        "source_label": "New Layer",
                        "visible_value": "X",
                        "box": box(),
                        "evidence_text": "X",
                    },
                ],
                "review_flags": ["row boundary faint"],
            }
        ],
        "quality_diagnostics": ["blur"],
    }


def audit_payload():
    return {
        "findings": [
            {
                "scope": "financial",
                "region_id": "table-1",
                "source_row_id": "r1",
                "reason": "total unclear",
                "evidence_text": "12.50",
            },
            {
                "scope": "row_boundary",
                "region_id": "table-1",
                "source_row_id": "r1",
                "reason": "merged rows",
                "evidence_text": "line",
            },
        ],
        "quality_diagnostics": ["compression"],
    }


class Response:
    def __init__(self, payload):
        self.output_text = json.dumps(payload)

    def model_dump(self, mode="json"):
        return {"id": "response"}


class Client:
    def __init__(self, output):
        self.output = output
        self.responses = self
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.output, Exception):
            raise self.output
        return Response(self.output)


def packet(role, payload, status="proposal"):
    return {
        "role": role,
        "status": status,
        "page_id": "p1",
        "engine": "openai/test",
        "payload": payload,
    }


def independent_handoff(tmp_path):
    page = tmp_path / "page.pdf"
    return write(
        tmp_path / "document-ai.json",
        {
            "provider": "google_document_ai",
            "records": [
                {
                    "page_id": "p1",
                    "page_sha256": table.sha256(page),
                    "raw_response": "raw/000001_p1.json",
                    "engine": "google_document_ai/ocr",
                    "engine_version": "default",
                    "independence_group": "google_document_ai",
                    "independent_extractor": True,
                    "document_text": "Amount 12.50",
                    "token_evidence": [],
                    "source_tables": [],
                }
            ],
        },
    )


def test_schemas_and_mapping_helpers(tmp_path):
    assert (
        table.profile_schema()["properties"]["table_regions"]["items"]["required"][-1]
        == "review_flags"
    )
    assert table.rows_schema()["properties"]["rows"]["items"]["properties"]["cells"]
    assert table.audit_schema()["properties"]["findings"]["items"]["properties"]["scope"]["enum"]
    assert table.load_object(write(tmp_path / "object.json", {}), "item") == {}
    with pytest.raises(ValueError, match="object"):
        table.load_object(write(tmp_path / "list.json", []), "item")
    assert table.source_schema_for_family("commission_statement") == "commission_statement"
    assert table.source_schema_for_family("commission_report") == "commission_statement"
    assert table.source_schema_for_family("other_report") == "source_native_table"
    assert len(table.template_fingerprint([" Dealer ", "Commission"])) == 64
    fingerprint = table.template_fingerprint(["Dealer"])
    rule = {
        "status": "client_approved",
        "template_fingerprint": fingerprint,
        "source_label": " dealer ",
    }
    assert table.approved_rule({"rules": [rule]}, fingerprint, "Dealer") is rule
    assert table.approved_rule({}, fingerprint, "Dealer") is None
    assert (
        table.review_card("p", "r", "x", "why")["review_source"]
        == "table_comprehension_decision_card"
    )
    diagnostics = []
    table.add_diagnostic(diagnostics, "p", "r", "provider", "timeout")
    assert diagnostics[0]["reason"] == "timeout"
    assert list(table.profile_headers(profile_payload()))[0][1][0] == "Commission"
    profile = packet("profile", profile_payload())
    template = table.mapping_templates(profile)[0]
    assert template["evidence"][0]["region_id"] == "table-1"
    assert template["document_family"] == "commission_statement"
    assert template["sections"] == ["North"]
    assert template["totals"] == ["Total commission"]
    assert template["handwriting_regions"] == profile_payload()["handwriting_regions"]
    # A profile with nothing mappable yields no templates. It no longer raises:
    # the caller turns that into retained review work -- see the test below.
    assert table.mapping_templates(packet("profile", {"table_regions": []})) == []
    assert list(table.profile_headers({"table_regions": ["skip"]})) == []
    assert table.mapping_templates(packet("profile", {"table_regions": [{"headers": []}]})) == []


def test_a_profile_with_no_readable_header_is_retained_not_a_bare_crash(tmp_path):
    """Rule 2: a stage that could not proceed still owes the run a record.

    On a real page an engine located a table region but read no column labels.
    `mappings` raised, so the command exited having written no output, no
    handoff, and -- worse -- no exception artifact. Nothing in the run said
    mapping discovery had been attempted at all, and the gate is assembled from
    exactly those files. Every other stage of this lane writes its exceptions
    beside its output.
    """
    profile = packet("profile", {"table_regions": [{"region_id": "table_1", "headers": []}]})
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile))
    out = tmp_path / "mappings.json"
    exceptions = tmp_path / "mapping_exceptions.json"
    handoff = tmp_path / "mapping_handoff.json"

    result = table.propose_mappings(profile_path, None, out, exceptions, handoff, tmp_path / "raw")
    assert result["summary"]["template_count"] == 0
    assert result["summary"]["gate_status"] == "blocked_pending_client_review"

    # All three artifacts exist, so the stage is visible in the run.
    retained = json.loads(exceptions.read_text())["exceptions"]
    assert len(retained) == 1
    assert retained[0]["review_source"] == "table_comprehension_mappings"
    assert retained[0]["disposition"] == "client_review_required"
    # The reason does not overclaim: another stage may still have read the rows.
    assert "another stage may still have read its rows" in retained[0]["reason"]
    assert json.loads(out.read_text())["mappings"] == []
    assert json.loads(handoff.read_text())["client_approval_required"] is True

    # Nothing is overwritten on a rerun; the no-clobber guarantee is unchanged.
    with pytest.raises(ValueError):
        table.propose_mappings(profile_path, None, out, exceptions, handoff, tmp_path / "raw")


def test_run_role_retains_success_and_failure(tmp_path):
    client = Client(profile_payload())
    result = table.run_role(
        manifest(tmp_path),
        "p1",
        tmp_path / "profile.json",
        tmp_path / "raw",
        "test",
        client,
        "medium",
        "profile",
    )
    assert result["status"] == "proposal" and result["same_model_roles_not_independent"]
    assert result["source_schema"] == "commission_statement"
    assert client.calls[0]["input"][0]["content"][-1]["type"] == "input_file"
    assert (
        json.loads((tmp_path / "raw" / "profile_response.json").read_text())["response"]["id"]
        == "response"
    )
    failed = table.run_role(
        manifest(tmp_path),
        "p1",
        tmp_path / "failed.json",
        tmp_path / "raw-failed",
        "test",
        Client(RuntimeError("offline")),
        "medium",
        "rows",
        {"profile": profile_payload()},
    )
    assert failed["status"] == "provider_or_schema_failure"
    assert (
        json.loads((tmp_path / "raw-failed" / "rows_response.json").read_text())["error_type"]
        == "RuntimeError"
    )
    bad_schema = table.run_role(
        manifest(tmp_path),
        "p1",
        tmp_path / "bad-schema.json",
        tmp_path / "raw-bad-schema",
        "test",
        Client([]),
        "medium",
        "audit",
        {"profile": profile_payload(), "rows": rows_payload()},
    )
    assert bad_schema["status"] == "provider_or_schema_failure"
    with pytest.raises(ValueError, match="role"):
        table.run_role(
            manifest(tmp_path),
            "p1",
            tmp_path / "bad.json",
            tmp_path / "raw-bad",
            "test",
            client,
            "medium",
            "bad",
        )
    with pytest.raises(ValueError, match="exactly one"):
        table.select_page(manifest(tmp_path), "missing")


def test_independent_google_evidence_is_hash_bound_and_audit_only(tmp_path):
    intake = manifest(tmp_path)
    evidence = independent_handoff(tmp_path)
    bound = table.independent_evidence_context(evidence, "p1", table.sha256(tmp_path / "page.pdf"))
    assert bound["provenance"]["provider"] == "google_document_ai"
    assert len(bound["provenance"]["evidence_sha256"]) == 64
    client = Client(audit_payload())
    result = table.run_role(
        intake,
        "p1",
        tmp_path / "audit.json",
        tmp_path / "raw-audit",
        "test",
        client,
        "medium",
        "audit",
        {"profile": profile_payload(), "rows": rows_payload()},
        evidence,
    )
    initial_context = json.loads(client.calls[0]["input"][0]["content"][0]["text"])
    context = json.loads(client.calls[1]["input"][0]["content"][0]["text"])
    assert "independent_provider_evidence" not in initial_context
    assert context["independent_provider_evidence"]["document_text"] == "Amount 12.50"
    assert result["independent_evidence"]["raw_response"] == "raw/000001_p1.json"
    assert result["conditional_buddy_check"]["triggered"] is True
    assert len(result["payload"]["findings"]) == 4
    clear = table.run_role(
        intake,
        "p1",
        tmp_path / "clear-audit.json",
        tmp_path / "raw-clear-audit",
        "test",
        Client({"findings": [], "quality_diagnostics": []}),
        "medium",
        "audit",
        {"profile": profile_payload(), "rows": rows_payload()},
        evidence,
    )
    assert clear["conditional_buddy_check"]["triggered"] is False
    with pytest.raises(ValueError, match="only to the audit"):
        table.run_role(
            intake,
            "p1",
            tmp_path / "rows.json",
            tmp_path / "raw-rows",
            "test",
            client,
            "medium",
            "rows",
            {},
            evidence,
        )
    bad = json.loads(evidence.read_text())
    bad["records"][0]["page_sha256"] = "wrong"
    bad_path = write(tmp_path / "bad-evidence.json", bad)
    with pytest.raises(ValueError, match="provenance"):
        table.independent_evidence_context(bad_path, "p1", table.sha256(tmp_path / "page.pdf"))
    # A retained provider failure is not evidence. On a real corpus every page's
    # independent reading had failed, and "lacks required provenance" sent the
    # operator looking for a schema mismatch instead of the failed lane.
    failed = json.loads(evidence.read_text())
    failed["records"][0]["review_status"] = "open_exception"
    with pytest.raises(ValueError, match="retained provider failure, not a reading"):
        table.independent_evidence_context(
            write(tmp_path / "failed-evidence.json", failed),
            "p1",
            table.sha256(tmp_path / "page.pdf"),
        )
    with pytest.raises(ValueError, match="adapter handoff"):
        table.independent_evidence_context(
            write(tmp_path / "wrong-provider.json", {"records": []}),
            "p1",
            table.sha256(tmp_path / "page.pdf"),
        )
    with pytest.raises(ValueError, match="exactly one"):
        table.independent_evidence_context(
            write(tmp_path / "no-match.json", {"provider": "google_document_ai", "records": []}),
            "p1",
            table.sha256(tmp_path / "page.pdf"),
        )
    oversized = json.loads(evidence.read_text())
    oversized["records"][0]["document_text"] = "x" * 100
    with pytest.raises(ValueError, match="character limit"):
        table.independent_evidence_context(
            write(tmp_path / "large.json", oversized), "p1", table.sha256(tmp_path / "page.pdf"), 10
        )
    oversized["records"][0]["document_text"] = "x" * 200_000
    bounded = table.independent_evidence_context(
        write(tmp_path / "bounded.json", oversized), "p1", table.sha256(tmp_path / "page.pdf")
    )
    assert bounded["context"]["evidence_context_truncated"] is True
    assert bounded["context"]["context_truncation"]["document_text"] is True
    assert len(json.dumps(bounded["context"])) < 100_000
    assert table.audit_has_issues(audit_payload()) is True
    assert table.audit_has_issues({"findings": [], "quality_diagnostics": []}) is False
    assert table.combined_audit_payload(
        {"findings": [1], "quality_diagnostics": []}, {"findings": [2], "quality_diagnostics": [3]}
    ) == {"findings": [1, 2], "quality_diagnostics": [3]}


def test_compact_independent_evidence_values_are_deterministic_and_bounded():
    assert table.compact_evidence_value("ok", 10) == ("ok", False)
    assert table.compact_evidence_value(["a", "b"], 5) == (["a"], True)
    assert table.compact_evidence_value({"a": "b", "c": "d"}, 10) == ({"a": "b"}, True)
    assert table.compact_evidence_value(12345, 2) == ("12", True)
    values = {
        "document_text": "x" * 5_000,
        "token_evidence": [{"token": "y" * 5_000}],
        "source_tables": [{"cells": "z" * 5_000}],
    }
    compact, truncated = table.bounded_independent_evidence(values, 1_500)
    assert truncated is True
    assert compact["context_truncation"]["applied"] is True
    assert len(json.dumps(compact)) < 1_500


def test_assemble_separates_decision_cards_from_quality_summary(tmp_path):
    profile = packet("profile", profile_payload())
    headers = [item["source_label"] for item in profile_payload()["table_regions"][0]["headers"]]
    fingerprint = table.template_fingerprint(headers)
    registry = {
        "rules": [
            {
                "rule_id": "financial",
                "status": "client_approved",
                "template_fingerprint": fingerprint,
                "source_label": "Commission",
                "canonical_field": "commission_amount",
                "semantic_type": "financial",
            },
            {
                "rule_id": "identity",
                "status": "client_approved",
                "template_fingerprint": fingerprint,
                "source_label": "Dealer",
                "canonical_field": "dealer_name",
                "semantic_type": "dealer",
            },
        ]
    }
    profile_path = write(tmp_path / "profile.json", profile)
    result = table.assemble(
        profile_path,
        write(tmp_path / "rows.json", packet("rows", rows_payload())),
        write(tmp_path / "audit.json", packet("audit", audit_payload())),
        write(tmp_path / "registry.json", registry),
        tmp_path / "source-rows.json",
        tmp_path / "cards.json",
        tmp_path / "quality.json",
        tmp_path / "exceptions.json",
        tmp_path / "adapter.json",
    )
    assert (
        result["source_rows"] == 1 and result["decision_cards"] == 5 and result["diagnostics"] == 6
    )
    rows = json.loads((tmp_path / "source-rows.json").read_text())["source_rows"]
    assert rows[0]["cells"][0]["canonical_mapping"]["status"] == "client_approved"
    assert rows[0]["cells"][2]["canonical_mapping"]["status"] == "unresolved"
    cards = json.loads((tmp_path / "cards.json").read_text())["decision_cards"]
    assert all(card["field"] != "row_boundary" for card in cards)
    quality = json.loads((tmp_path / "quality.json").read_text())
    assert quality["artifact_type"] == "table_comprehension_quality_summary"
    assert queue.consolidate([tmp_path / "quality.json"])[0] == []
    adapter = json.loads((tmp_path / "adapter.json").read_text())
    assert adapter["policy"]["automatic_canonical_mapping"] is False
    with pytest.raises(ValueError, match="distinct and new"):
        table.assemble(
            profile_path,
            tmp_path / "rows.json",
            tmp_path / "audit.json",
            None,
            tmp_path / "source-rows.json",
            tmp_path / "new.json",
            tmp_path / "new2.json",
            tmp_path / "new3.json",
            tmp_path / "new4.json",
        )
    wrong = packet("audit", audit_payload())
    wrong["page_id"] = "other"
    with pytest.raises(ValueError, match="same page"):
        table.assemble(
            profile_path,
            tmp_path / "rows.json",
            write(tmp_path / "wrong.json", wrong),
            None,
            tmp_path / "x.json",
            tmp_path / "y.json",
            tmp_path / "z.json",
            tmp_path / "e.json",
            tmp_path / "a.json",
        )


def test_assemble_handles_retained_non_material_and_invalid_items(tmp_path):
    profile = profile_payload()
    profile["table_regions"][0]["headers"] = [
        {"source_label": "Dealer", "left": 0.1, "right": 0.2, "evidence_text": "Dealer"}
    ]
    profile["table_regions"][0]["review_flags"] = []
    profile["handwriting_regions"] = []
    rows = rows_payload()
    rows["rows"] += ["skip", {"region_id": "table-1", "cells": ["skip"], "review_flags": []}]
    rows["rows"][0]["cells"] = [
        {"source_label": "Dealer", "visible_value": "Acme", "box": box(), "evidence_text": "Acme"}
    ]
    audit = {
        "findings": [
            "skip",
            {
                "scope": "odd",
                "region_id": "table-1",
                "source_row_id": "",
                "reason": "other",
                "evidence_text": None,
            },
        ],
        "quality_diagnostics": [],
    }
    fingerprint = table.template_fingerprint(["Dealer"])
    registry = {
        "rules": [
            {
                "status": "client_approved",
                "template_fingerprint": fingerprint,
                "source_label": "Dealer",
                "canonical_field": "dealer_name",
                "semantic_type": "dealer",
            }
        ]
    }
    result = table.assemble(
        write(tmp_path / "profile.json", packet("profile", profile)),
        write(tmp_path / "rows.json", packet("rows", rows, "provider_or_schema_failure")),
        write(tmp_path / "audit.json", packet("audit", audit)),
        write(tmp_path / "registry.json", registry),
        tmp_path / "out.json",
        tmp_path / "cards.json",
        tmp_path / "quality.json",
        tmp_path / "exceptions.json",
        tmp_path / "adapter.json",
    )
    assert result["decision_cards"] == 1
    assert json.loads((tmp_path / "quality.json").read_text())["diagnostic_counts"]["provider"] == 2
    no_rows = {"rows": [], "quality_diagnostics": []}
    empty_audit = {"findings": [], "quality_diagnostics": []}
    no_card = table.assemble(
        write(tmp_path / "profile2.json", packet("profile", profile)),
        write(tmp_path / "rows2.json", packet("rows", no_rows)),
        write(tmp_path / "audit2.json", packet("audit", empty_audit)),
        write(tmp_path / "registry2.json", registry),
        tmp_path / "out2.json",
        tmp_path / "cards2.json",
        tmp_path / "quality2.json",
        tmp_path / "exceptions2.json",
        tmp_path / "adapter2.json",
    )
    assert no_card["decision_cards"] == 0


def test_mapping_proposal_option_stays_outside_effective_mapping(tmp_path):
    profile = write(tmp_path / "profile.json", packet("profile", profile_payload()))
    result = table.propose_mappings(
        profile,
        None,
        tmp_path / "proposals.json",
        tmp_path / "exceptions.json",
        tmp_path / "handoff.json",
        tmp_path / "raw",
        False,
    )
    assert result["summary"]["client_review_items"] == 3
    assert (
        json.loads((tmp_path / "handoff.json").read_text())["policy"]["client_approval_permitted"]
        is False
    )
    with pytest.raises(ValueError, match="successful"):
        table.propose_mappings(
            write(tmp_path / "bad.json", packet("profile", {}, "provider_or_schema_failure")),
            None,
            tmp_path / "p.json",
            tmp_path / "e.json",
            tmp_path / "h.json",
            tmp_path / "r",
        )


def test_main_commands(monkeypatch, capsys):
    monkeypatch.setattr(table, "load_project_env", lambda: None)
    monkeypatch.setattr(table, "assemble", lambda *args: {"source_rows": 1})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "table_comprehension.py",
            "assemble",
            "p",
            "r",
            "a",
            "--out",
            "o",
            "--decision-cards",
            "c",
            "--quality-summary",
            "q",
            "--exceptions",
            "e",
            "--adapter-out",
            "h",
        ],
    )
    table.main()
    assert json.loads(capsys.readouterr().out) == {"source_rows": 1}
    monkeypatch.setattr(table, "propose_mappings", lambda *args: {"summary": {"mapping_count": 1}})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "table_comprehension.py",
            "mappings",
            "p",
            "--out",
            "o",
            "--exceptions",
            "e",
            "--handoff-out",
            "h",
            "--raw-dir",
            "r",
        ],
    )
    table.main()
    assert json.loads(capsys.readouterr().out) == {"mapping_count": 1}
    monkeypatch.setattr(table, "build_client", lambda *args: "client")
    monkeypatch.setattr(table, "run_role", lambda *args: {"status": "proposal"})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "table_comprehension.py",
            "profile",
            "m",
            "--page-id",
            "p",
            "--out",
            "o",
            "--raw-dir",
            "r",
        ],
    )
    table.main()
    assert json.loads(capsys.readouterr().out) == {"status": "proposal"}
    monkeypatch.setattr(
        sys,
        "argv",
        ["table_comprehension.py", "rows", "m", "--page-id", "p", "--out", "o", "--raw-dir", "r"],
    )
    with pytest.raises(SystemExit, match="require --context"):
        table.main()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "table_comprehension.py",
            "profile",
            "m",
            "--page-id",
            "p",
            "--out",
            "o",
            "--raw-dir",
            "r",
            "--independent-evidence",
            "e",
        ],
    )
    with pytest.raises(SystemExit, match="only by audit"):
        table.main()
