"""Additional exceptional and boundary paths for the control layer."""

import importlib
import json
import sys

import pytest
from contract_helpers import write_consensus_handoff
from PIL import Image
from test_cli_workflows import (
    arithmetic_check,
    attribution,
    completeness,
    consensus,
    entity_resolve,
    ingest_pages,
    invoke,
    sampling,
    scan_profile,
    write_json,
)

final_review_queue = importlib.import_module("client_review.queue")
runtime_config = importlib.import_module("runtime_config")
analytics = importlib.import_module("analytics")


def test_arithmetic_boundary_paths_and_low_rate_warning(monkeypatch, tmp_path):
    lines = [{"line_number": 1, "quantity": 1, "unit_price": 2, "extended_amount": 2}]
    record = {
        "document_id": "amended",
        "lines": lines,
        "header": {
            "subtotal": 2,
            "total_amount": 2,
            "amount_due": 2,
            "credits_applied": 0,
            "accessorial_total": 3,
        },
        "accessorials": [{"charge_amount": 1}],
        "amendments": [
            {"target_line": 1, "target_column": "unit_price", "amended_value": 3},
            {"target_line": 1, "target_column": "extended_amount", "amended_value": 3},
            {"target_line": 2, "target_column": "quantity", "amended_value": "bad"},
        ],
    }
    checks, _, _ = arithmetic_check.check_lines(record, 0.01)
    assert checks[0]["is_handwritten"] and checks[0]["status"] == "pass"
    assert arithmetic_check.check_document({"header": {"subtotal": 1}}, 0.01)["notes"]
    assert arithmetic_check.check_document({"header": {"total_amount": 1}}, 0.01)["notes"]
    records = [
        {"document_id": str(index), "header": {"total_amount": 10, "subtotal": 1}}
        for index in range(21)
    ]
    inp, out = (
        write_json(tmp_path / "records.json", {"documents": records}),
        tmp_path / "proofed.json",
    )
    invoke(monkeypatch, arithmetic_check, inp, "--out", out, "--quiet")
    assert "Pass rate" in json.loads(out.read_text())["summary"]["findings"][-1]
    deferred = tmp_path / "deferred.json"
    invoke(
        monkeypatch,
        arithmetic_check,
        write_json(tmp_path / "unassigned.json", [{"document_id": "p1", "header": {}}]),
        "--defer-unassigned-pages",
        "--out",
        deferred,
        "--quiet",
    )
    assert json.loads(deferred.read_text())["summary"]["deferred_reassembly"] == 1
    commission_out = tmp_path / "commission-proofed.json"
    invoke(
        monkeypatch,
        arithmetic_check,
        write_json(
            tmp_path / "commission.json",
            [{"document_id": "commission-1", "document_type": "commission_report"}],
        ),
        "--out",
        commission_out,
        "--quiet",
    )
    commission_summary = json.loads(commission_out.read_text())["summary"]
    # A commission document carrying no line terms is not "nothing to check
    # here" now that a check exists for it; it is a check that could not run.
    assert commission_summary["not_provable"] == 1
    assert commission_summary["not_applicable"] == 0


def test_commission_documents_are_proved_on_their_own_arithmetic_not_invoice_rollups():
    """Invoice rollups still do not apply; the commission identity does.

    The line here carries invoice fields and no commission terms, so the check
    that applies cannot run. That is `not_provable` -- tried and could not prove
    -- rather than `not_applicable`, which would read as "nothing to check".
    """
    result = arithmetic_check.check_document(
        {
            "document_id": "commission-1",
            "document_type": "commission_statement",
            "header": {"commission_amount": "10.00", "commissionable_amount": "100.00"},
            "lines": [{"quantity": 10, "unit_price": 1, "extended_amount": 999}],
        },
        0.01,
    )
    assert result["arithmetic_status"] == "not_provable"
    assert result["arithmetic_scope"] == "commission_line_extension"
    assert result["rate_unit"] is None
    assert result["checks"] == []


def test_a_commission_type_survives_the_consensus_field_wrapper():
    """The type arithmetic routes on arrives wrapped, not bare.

    A provider record carries `model_document_type` as a plain string. Consensus
    reconciles it and hands on the same fact as a {value, confidence, source}
    object, and `str()` over that yields a dict repr that matches no document
    type. Every test here passed a bare string, so 18 commission statements were
    reported against the wrong scope on a real run, because `str()` over that
    object matches no document type. A wrong status that reads as conservative
    is the kind that survives a full run unquestioned. The routing this pins is
    unchanged; only what a commission document is then proved against has moved
    from "nothing" to its own line identity.
    """
    assert arithmetic_check.text({"value": "commission_statement"}) == "commission_statement"
    assert arithmetic_check.text("commission_report") == "commission_report"
    assert arithmetic_check.text({"value": None}) == ""
    assert arithmetic_check.text(None) == ""
    assert arithmetic_check.text({}) == ""
    assert arithmetic_check.text(7) == "7"

    wrapped = arithmetic_check.check_document(
        {
            "document_id": "commission-1",
            # Exactly what consensus writes: the intake type is still the
            # unreviewed "unknown", and the model's proposal is the wrapped one.
            "document_type": "unknown",
            "model_document_type": {
                "value": "commission_statement",
                "confidence": None,
                "source": "printed",
            },
            "lines": [{"commission_amount": "662.89", "commissionable_amount": "33144.35"}],
        },
        0.01,
    )
    # The routing is what this pins: the wrapped type must still reach the
    # commission branch. The line carries two of the three terms, so the
    # identity cannot run and the document is unproved rather than unrouted.
    assert wrapped["arithmetic_scope"] == "commission_line_extension"
    assert wrapped["arithmetic_status"] == "not_provable"

    # An unwrapped intake type still wins over the model's proposal, and a
    # document that is genuinely neither stays unprovable rather than routed.
    neither = arithmetic_check.check_document(
        {"document_id": "d2", "document_type": "unknown", "model_document_type": {"value": ""}},
        0.01,
    )
    assert neither["arithmetic_status"] == "not_provable"


def test_consensus_boundary_paths_and_warning_mix(monkeypatch, tmp_path, capsys):
    assert consensus.normalize(True) is True
    three = consensus.reconcile_field("name", [("a", "x"), ("b", "x"), ("c", "x")])
    assert three["consensus_flag"] == "consensus_3of3"
    split = consensus.reconcile_field("name", [("a", "x"), ("b", "y")])
    assert split["consensus_flag"] == "no_consensus"
    assert consensus.unflatten({"lines[x].value": {"value": 1}})["lines"][0]["value"] == 1
    a = write_consensus_handoff(
        tmp_path / "a.json",
        [{"document_id": "single", "field": "x"}],
        "openai",
        "model-a",
        "consensus_primary",
    )
    b = write_consensus_handoff(
        tmp_path / "b.json",
        [{"document_id": "multi", "field": {"value": 1, "source": "handwritten"}}],
        "google",
        "model-b",
        "consensus_secondary",
    )
    c = write_consensus_handoff(
        tmp_path / "c.json",
        [{"document_id": "multi", "field": {"value": 2, "source": "handwritten"}}],
        "openrouter",
        "nvidia/model-c",
        "consensus_tiebreaker",
    )
    out, exc = tmp_path / "out.json", tmp_path / "exc.json"
    invoke(
        monkeypatch,
        consensus,
        a,
        b,
        c,
        "--handwriting-policy",
        "strict",
        "--out",
        out,
        "--exceptions",
        exc,
    )
    assert "only one engine" in capsys.readouterr().out


def test_gate_aware_analytics_and_record_shapes(monkeypatch, tmp_path):
    input_path = write_json(
        tmp_path / "validated.json", {"documents": [{"document_id": "d1", "has_handwriting": True}]}
    )
    review = write_json(
        tmp_path / "review.json", {"summary": {"gate_status": "blocked_pending_client_review"}}
    )
    parties = write_json(tmp_path / "parties.json", {"summary": {"status": "complete"}})
    completeness = write_json(tmp_path / "completeness.json", {"summary": {"status": "not_run"}})
    output = tmp_path / "analytics.json"
    invoke(
        monkeypatch,
        analytics,
        input_path,
        "--review",
        review,
        "--parties",
        parties,
        "--completeness",
        completeness,
        "--out",
        output,
    )
    result = json.loads(output.read_text())
    assert result["corpus"]["documents"] == 1
    assert result["financial_analytics"]["status"] == "blocked"
    assert analytics.records_from({"records": []}) == []
    assert analytics.records_from([]) == []
    assert analytics.records_from({"other": 1}) == [{"other": 1}]
    with pytest.raises(ValueError):
        analytics.records_from("invalid")
    monkeypatch.setenv("ANALYTICS_ENABLED", "false")
    with pytest.raises(SystemExit, match="Analytics disabled"):
        invoke(monkeypatch, analytics, input_path, "--out", tmp_path / "disabled.json")


def test_nonblocking_review_items_and_redacted_effective_settings(monkeypatch):
    items = final_review_queue.review_items(
        {
            "exceptions": [
                {"blocking": False, "field": "handwriting"},
                {"field": "total", "reason": "arithmetic"},
            ]
        }
    )
    assert len(items) == 1 and items[0]["reason"] == "arithmetic"
    monkeypatch.setattr(runtime_config, "load_project_env", lambda: {})
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    monkeypatch.setenv("OPENAI_CREDENTIAL_ENV", "OPENAI_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY_ENV", "OPENAI_API_KEY")
    monkeypatch.setenv("ANALYTICS_ENABLED", "true")
    snapshot = runtime_config.effective_settings_snapshot()
    assert snapshot["OPENAI_API_KEY"] == "[redacted]"
    assert snapshot["OPENAI_CREDENTIAL_ENV"] == "OPENAI_API_KEY"
    assert snapshot["OPENAI_API_KEY_ENV"] == "OPENAI_API_KEY"

    # A connection string carries a password no secret-sounding name warns of,
    # and this snapshot reaches analytics.py and the operations manifest, which
    # are shared. The target survives so an audit can name it; the credential
    # does not.
    monkeypatch.setenv("CANONICAL_DATABASE_URL", "postgresql://svc:p4ss@db.internal:5432/canonical")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    snapshot = runtime_config.effective_settings_snapshot()
    assert (
        snapshot["CANONICAL_DATABASE_URL"] == "postgresql://[redacted]@db.internal:5432/canonical"
    )
    assert "p4ss" not in json.dumps(snapshot)
    # A URL with no credential in it is an ordinary setting and is left alone.
    assert snapshot["OPENROUTER_BASE_URL"] == "https://openrouter.ai/api/v1"
    for untouched in ("", "localhost:5432", "user:pass@host", "medium"):
        assert runtime_config.redact_embedded_credential(untouched) == untouched


def test_handwriting_comment_only_does_not_create_consensus_exception():
    field = consensus.reconcile_field(
        "header.total_amount",
        [
            ("a", {"value": "10", "source": "handwritten"}),
            ("b", {"value": "11", "source": "handwritten"}),
        ],
        "comment_only",
    )
    assert field["accepted"] is False
    assert field["queue_for_review"] is False
    assert field["blocking"] is False
    assert field["handwriting_comment"]["client_review_required"] is False


def test_arithmetic_can_defer_unassigned_page_proof():
    result = arithmetic_check.check_document(
        {"document_id": "page-1", "reassembly_status": "unassigned", "lines": []},
        0.01,
        defer_unassigned=True,
    )
    assert result["arithmetic_status"] == "deferred_reassembly"


def test_entity_edge_cases_and_cli_exits(monkeypatch, tmp_path):
    assert entity_resolve.normalize_name(None) == ""
    assert entity_resolve.normalize_address(None) == ""
    assert entity_resolve.postal_key(None) is None
    assert entity_resolve.postal_key("Toronto ON M5V 3A8") == "M5V"
    assert entity_resolve.token_set_ratio("", "a") == 0
    assert entity_resolve.sequence_ratio("a", "b") == 0
    assert entity_resolve.load_records("bad") == []
    assert entity_resolve.blocking_keys("", "") == {"_unblocked"}

    def mention(name, index):
        return {
            "raw_name": name,
            "raw_address": None,
            "role": "biller",
            "document_id": str(index),
            "field": "seller_name",
        }

    # Repetitions of one reading collapse before any pairwise work, so a name
    # printed on hundreds of lines resolves instead of exceeding the cap.
    repeated = [mention("same", i) for i in range(401)]
    groups, evidence, ambiguous = entity_resolve.cluster(repeated, 0.9, 0.8)
    assert len(groups) == 1
    assert [e["reason"] for e in evidence] == ["identical_normalized_reading"]
    assert not ambiguous
    # A block of that many genuinely distinct readings still defers, and says so.
    distinct = [
        mention(f"same company {i}", i) for i in range(entity_resolve.MAX_BLOCK_PAIRWISE + 1)
    ]
    deferred = entity_resolve.cluster(distinct, 0.9, 0.8)[2]
    assert any(item["a"] is None for item in deferred)
    with pytest.raises(SystemExit, match="below --threshold"):
        invoke(
            monkeypatch,
            entity_resolve,
            write_json(tmp_path / "one.json", [{"seller_name": "A"}]),
            "--review-band",
            ".9",
            "--threshold",
            ".9",
        )
    none_out, none_log = tmp_path / "none-parties.json", tmp_path / "none-merges.json"
    invoke(
        monkeypatch,
        entity_resolve,
        write_json(tmp_path / "none.json", [{}]),
        "--out",
        none_out,
        "--log",
        none_log,
    )
    assert json.loads(none_out.read_text())["summary"]["party_mentions"] == 0
    assert json.loads(none_log.read_text())["pending_adjudication"][0]["reason"] == (
        "no_party_mentions"
    )
    invoke(
        monkeypatch,
        entity_resolve,
        write_json(tmp_path / "none-quiet.json", [{}]),
        "--out",
        tmp_path / "none-quiet-parties.json",
        "--log",
        tmp_path / "none-quiet-merges.json",
        "--quiet",
    )


def test_sampling_error_paths_and_tolerance_messages(monkeypatch, tmp_path):
    assert sampling.num({"value": None}) == 0
    assert sampling.get({}, "missing", "fallback") == "fallback"
    records = [
        {
            "document_id": "zero",
            "review_status": "auto_accepted",
            "arithmetic_status": "proved",
            "total_amount": 0,
        },
        {"document_id": "bad", "review_status": "failed", "total_amount": 1},
    ]
    # An empty frame is a result about the corpus, not a crash. This used to exit
    # having written nothing, so the run held no record that sampling had been
    # attempted -- indistinguishable from never invoking it, both to the gate,
    # which is assembled from retained artifacts, and to the lane coverage report.
    empty_plan = tmp_path / "empty-plan.json"
    invoke(
        monkeypatch,
        sampling,
        write_json(tmp_path / "none.json", records),
        "--materiality",
        "1",
        "--out",
        str(empty_plan),
    )
    written = json.loads(empty_plan.read_text())
    assert written["summary"]["population_documents"] == 0
    assert written["summary"]["excluded_documents"] == 2
    assert written["summary"]["gate_status"] == "blocked_pending_client_review"
    assert written["monetary_unit_sample"] == [] and written["attribute_sample"] == []
    # The finding names why the frame emptied: one document carries no money and
    # one is not auto-accepted, and those are different problems to work.
    finding = written["summary"]["findings"][0]
    assert "1 no monetary value" in finding and "1 not auto-accepted" in finding
    # Every excluded document is retained with its own reason, not just counted.
    assert {item["document_id"] for item in written["excluded"]} == {"zero", "bad"}

    # --quiet suppresses the narration and still writes the artifact: the record
    # is the point, the printing is not.
    quiet_plan = tmp_path / "quiet-plan.json"
    invoke(
        monkeypatch,
        sampling,
        write_json(tmp_path / "none2.json", records),
        "--materiality",
        "1",
        "--out",
        str(quiet_plan),
        "--quiet",
    )
    assert json.loads(quiet_plan.read_text())["summary"]["population_documents"] == 0
    records = [
        {
            "document_id": "only",
            "review_status": "auto_accepted",
            "arithmetic_status": "proved",
            "total_amount": 10,
        }
    ]
    one_input = write_json(tmp_path / "one.json", records)
    one_plan = tmp_path / "one-plan.json"
    invoke(
        monkeypatch,
        sampling,
        one_input,
        "--materiality",
        "100",
        "--out",
        one_plan,
        "--quiet",
    )
    plan_hash = json.loads(one_plan.read_text())["sample_plan_sha256"]
    findings = write_json(
        tmp_path / "findings.json",
        {
            "schema_version": "sample_review_results_v1",
            "sample_plan_sha256": plan_hash,
            "outcomes": [
                {
                    "document_id": "only",
                    "recorded_value": 10,
                    "audited_value": 0,
                    "outcome": "reviewed_error",
                }
            ],
        },
    )
    out = tmp_path / "sample.json"
    invoke(
        monkeypatch,
        sampling,
        one_input,
        "--materiality",
        "100",
        "--findings",
        findings,
        "--tolerance",
        ".1",
        "--out",
        out,
        "--quiet",
    )
    assert "ESCALATE" in json.loads(out.read_text())["notes"][-1]
    out = tmp_path / "sample-ok.json"
    invoke(
        monkeypatch,
        sampling,
        one_input,
        "--materiality",
        "100",
        "--findings",
        findings,
        "--tolerance",
        "9999",
        "--out",
        out,
        "--quiet",
    )
    assert "within" in json.loads(out.read_text())["notes"][-1]

    invalid_findings = write_json(
        tmp_path / "invalid-findings.json",
        {
            "schema_version": "sample_review_results_v1",
            "sample_plan_sha256": plan_hash,
            "outcomes": [
                {
                    "document_id": "only",
                    "recorded_value": 0,
                    "audited_value": 0,
                    "outcome": "reviewed_correct",
                }
            ],
        },
    )
    invalid_out = tmp_path / "invalid-findings-out.json"
    invoke(
        monkeypatch,
        sampling,
        one_input,
        "--materiality",
        "100",
        "--findings",
        invalid_findings,
        "--out",
        invalid_out,
        "--quiet",
    )
    invalid_result = json.loads(invalid_out.read_text())
    assert invalid_result["accuracy_statement"]["status"] == "blocked_incomplete_sample_review"
    assert "every certainty and MUS selection" in invalid_result["notes"][0]

    many_records = [
        {
            "document_id": f"sample-{index}",
            "review_status": "auto_accepted",
            "arithmetic_status": "proved",
            "total_amount": 10,
        }
        for index in range(11)
    ]
    many_input = write_json(tmp_path / "many-input.json", many_records)
    many_plan = tmp_path / "many-plan.json"
    invoke(
        monkeypatch,
        sampling,
        many_input,
        "--materiality",
        "1000",
        "--mus-n",
        "11",
        "--out",
        many_plan,
        "--quiet",
    )
    many_findings = write_json(
        tmp_path / "many-findings.json",
        {
            "schema_version": "sample_review_results_v1",
            "sample_plan_sha256": json.loads(many_plan.read_text())["sample_plan_sha256"],
            "outcomes": [
                {
                    "document_id": f"sample-{index}",
                    "recorded_value": 10,
                    "audited_value": 9,
                    "outcome": "reviewed_error",
                }
                for index in range(11)
            ],
        },
    )
    many_out = tmp_path / "many-out.json"
    invoke(
        monkeypatch,
        sampling,
        many_input,
        "--materiality",
        "1000",
        "--mus-n",
        "11",
        "--findings",
        many_findings,
        "--out",
        many_out,
        "--quiet",
    )
    many_result = json.loads(many_out.read_text())
    assert many_result["accuracy_statement"]["status"] == (
        "blocked_confidence_factor_table_exhausted"
    )
    assert "confidence-factor table" in many_result["notes"][0]


def test_completeness_no_optional_data_and_edge_controls(monkeypatch, tmp_path):
    # An unbucketable row is retained as an explicit rejection, never dropped:
    # a silently discarded GL row shrinks the baseline and therefore the variance.
    buckets, rejected = completeness.load_gl(
        write_json(tmp_path / "gl.json", [{"period": "", "amount": 1}])
    )
    assert buckets == {}
    assert rejected == [
        {
            "row_index": 0,
            "reason": "gl_row_period_unparseable",
            "raw_period": "",
            "raw_amount": "1",
            "disposition": "client_review_required",
        }
    ]
    # A period that is not an ISO year-month must not become a month bucket.
    quarter_buckets, quarter_rejected = completeness.load_gl(
        write_json(tmp_path / "gl-q.json", [{"period": "Q1-2026", "amount": 5}])
    )
    assert quarter_buckets == {}
    assert quarter_rejected[0]["reason"] == "gl_row_period_unparseable"
    # An unparseable amount is a rejection, not a silent zero.
    amount_buckets, amount_rejected = completeness.load_gl(
        write_json(tmp_path / "gl-a.json", [{"period": "2026-01", "amount": "n/a"}])
    )
    assert amount_buckets == {}
    assert amount_rejected[0]["reason"] == "gl_row_amount_unparseable"
    assert completeness.sequence_gaps([{"vendor": "V", "invoice_number": "x"}]) == []
    docs = [
        {
            "vendor": "V",
            "month": "2026-01",
            "amount": 1,
            "doc_type": "note",
            "invoice_number": "x",
            "credits": 0,
            "explicit_status": "",
            "date": "2026-01-01",
        }
    ]
    assert completeness.calendar_gaps(docs) == []
    assert (
        completeness.gl_variance(docs, {"2026-02": {"ALL": 1}}, 1)["by_period"][0]["variance_pct"]
        is None
    )
    inp, out = (
        write_json(tmp_path / "input.json", [{"document_id": "n", "document_type": "note"}]),
        tmp_path / "out.json",
    )
    invoke(monkeypatch, completeness, inp, "--out", out, "--quiet")
    report = json.loads(out.read_text())
    assert report["gl_variance"]["status"] == "not_run"
    assert report["aging_closure"]["status"] == "not_run"
    assert report["gate_status"] == "blocked"
    assert report["gate_reasons"] == [
        "gl_reconciliation_not_run",
        "aging_closure_not_run",
        "documents_without_parseable_date",
    ]


def test_completeness_reports_partial_and_ambiguous_payment_closure(monkeypatch, tmp_path):
    records = [
        {
            "document_id": f"{vendor}-{invoice}",
            "document_type": "invoice",
            "seller_name": vendor,
            "invoice_number": invoice,
            "invoice_date": "2026-01-15",
            "total_amount": 100,
        }
        for vendor, invoice in (("Vendor A", "1001"), ("Vendor B", "1001"), ("Vendor C", "2001"))
    ]
    payments = write_json(
        tmp_path / "closure-payments.json",
        {
            "payments": [
                {"invoice_number": "1001", "amount": 100},
                {"invoice_number": "2001", "vendor": "Vendor C", "amount": 50},
            ]
        },
    )
    gl = write_json(tmp_path / "closure-gl.json", [{"period": "2026-01", "amount": 300}])
    output = tmp_path / "closure-report.json"
    invoke(
        monkeypatch,
        completeness,
        write_json(tmp_path / "closure-input.json", records),
        "--gl",
        gl,
        "--payments",
        payments,
        "--out",
        output,
        "--quiet",
    )
    result = json.loads(output.read_text())
    assert result["aging_closure"]["partially_applied"] == 1
    assert result["aging_closure"]["ambiguous_payment_application_count"] == 1
    assert "aging_closure_unresolved" in result["gate_reasons"]
    assert "payment_application_ambiguous" in result["gate_reasons"]


def test_scan_quality_and_summary_hazard_paths(monkeypatch, tmp_path):
    assert scan_profile._measure_quality(Image.new("L", (2, 2), 255))["contrast"] == 0
    assert scan_profile._measure_quality(Image.new("L", (4, 4), 0))["speckle_ratio"] == 0
    assert (
        scan_profile.quality_band(
            {
                "dpi": 100,
                "quality": {
                    "contrast": 0.1,
                    "sharpness": 1,
                    "speckle_ratio": 0.2,
                    "ink_coverage": 0.5,
                },
            }
        )["quality_band"]
        == "poor"
    )
    pages = [
        {
            "bucket": "C",
            "branch": "A",
            "quality_band": "good",
            "quality_problems": [],
            "dpi": 300,
            "notes": ["pixel_read_failed:ValueError"],
            "jbig2_suspect": False,
        },
        {
            "bucket": "G",
            "branch": "B",
            "quality_band": "marginal",
            "quality_problems": [],
            "dpi": 250,
            "notes": ["false_colour", "chroma_unverified"],
            "jbig2_suspect": False,
        },
    ]
    summary = scan_profile.summarize(pages)
    assert summary["hazards"]["read_failures"] == 1
    assert any("could not be read" in finding for finding in summary["findings"])
    with pytest.raises(SystemExit, match="No PDFs"):
        monkeypatch.setattr(
            sys, "argv", [scan_profile.__file__, str(tmp_path), "--out", str(tmp_path / "x.json")]
        )
        scan_profile.main()


def test_attribution_remaining_resolution_and_summary_paths(monkeypatch, tmp_path, capsys):
    assert attribution.num({"value": None}) == 0
    assert attribution.parse_date(None) is None
    assert attribution.parse_date("2026-99-99") is None
    reference = write_json(
        tmp_path / "ref.json",
        [
            {"job_number": "JOB-1", "date": "2026-01-01", "customer": "A", "amount": 1},
            {"ack_number": "ACK-1", "date": "", "customer": "", "amount": 0},
            {"amount": 99},
        ],
    )
    ref = attribution.load_reference(reference, r"NEW-\d+", r"JOB-\d+")
    assert attribution.validate_key("NEW-2", ref)[0]
    assert attribution.validate_key("JOB-9", ref)[0]
    assert (
        attribution.validate_key("JOB-2", {**ref, "formats": {}})[1]
        == "matches_job_pattern_not_in_reference"
    )
    assert attribution.validate_key("x", {**ref, "formats": {}})[1] == "no_reference_available"
    idx = attribution.build_index(
        [
            {"document_id": "d", "invoice_number": "I"},
            {"document_id": "c", "reference_invoice": "I"},
        ]
    )
    low = {"d": {"ack_number": "ACK", "rank": 6}}
    assert (
        attribution.resolve_inherited({"document_id": "c", "reference_invoice": "I"}, low, idx)
        is None
    )
    good = {"d": {"ack_number": "ACK", "rank": 1}}
    assert (
        attribution.resolve_inherited({"document_id": "c", "reference_invoice": "I"}, good, idx)[
            "rank"
        ]
        == 5
    )
    fuzzy_ref = {
        "by_customer": {
            "a": [
                {"key": "NO-DATE", "date": None, "amount": 1},
                {"key": "FAR", "date": attribution.parse_date("2020-01-01"), "amount": 1},
                {"key": "ZERO", "date": attribution.parse_date("2026-01-01"), "amount": 0},
                {"key": "WRONG", "date": attribution.parse_date("2026-01-01"), "amount": 100},
            ]
        }
    }
    assert (
        attribution.resolve_fuzzy(
            {"buyer_name": "A", "invoice_date": "2026-01-01", "total_amount": 1}, fuzzy_ref
        )
        is None
    )
    records = [
        {
            "document_id": "fuzzy",
            "buyer_name": "A",
            "invoice_date": "2026-01-01",
            "total_amount": 10,
            "branch": "",
            "salesperson": "Sam",
        },
        {
            "document_id": "no-ref",
            "ack_number": "FREE-1",
            "total_amount": 1,
            "header": "not-a-dict",
        },
    ]
    out, register = tmp_path / "out.json", tmp_path / "register.json"
    invoke(
        monkeypatch,
        attribution,
        write_json(tmp_path / "records.json", {"records": records}),
        "--out",
        out,
        "--register",
        register,
    )
    result = json.loads(out.read_text())
    assert "No reference table" in result["summary"]["findings"][0]
    assert "method mix" in capsys.readouterr().out


def test_scan_profile_exceptional_page_metadata_paths(monkeypatch):
    class Page:
        class Resources:
            XObject = {}

        def get(self, _key, _default=None):
            raise RuntimeError("bad metadata")

    assert scan_profile.profile_page(Page(), 1)["notes"] == ["no_raster_image"]

    class BrokenImage:
        def get(self, key, default=None):
            if key in {"/Width", "/BitsPerComponent", "/ColorSpace"}:
                raise ValueError("bad")
            return "/Image" if key == "/Subtype" else default

    class ImagePage:
        Resources = type("R", (), {"XObject": {"x": BrokenImage()}})()

        def get(self, key, default=None):
            return {"/MediaBox": [0, 0, 72, 72]}.get(key, default)

    rec = scan_profile.profile_page(ImagePage(), 2, deep=False)
    assert rec["width_px"] == 0 and rec["bit_depth"] is None

    class BadDoc:
        pages = []

        @property
        def docinfo(self):
            raise RuntimeError("bad producer")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(scan_profile.pikepdf, "open", lambda _path: BadDoc())
    assert scan_profile.profile_pdf("x.pdf") == []


def test_remaining_small_branch_outcomes(monkeypatch, tmp_path):
    assert arithmetic_check.num({"value": None}) is None
    assert arithmetic_check.effective(
        {"amendments": [{"field": "subtotal", "amended_value": "bad"}], "subtotal": 2}, "subtotal"
    ) == (2.0, "printed")
    due = arithmetic_check.check_document(
        {"header": {"subtotal": 1, "total_amount": 1, "amount_due": 1}}, 0.01
    )
    assert any(check["check"] == "total_less_credits_equals_amount_due" for check in due["checks"])
    assert (
        attribution.validate_key(
            None, {"table": {}, "ack_pattern": None, "job_pattern": None, "formats": {}}
        )[1]
        == "empty"
    )
    ref = {"table": {}, "ack_pattern": None, "job_pattern": None, "formats": {"A#": 1}, "by_po": {}}
    assert attribution.validate_key("A2", ref)[1] == "matches_corpus_format_not_in_reference"
    assert attribution.resolve_po({"po_number": "missing"}, ref) is None
    assert (
        attribution.resolve_selling_location({"branch": "DAX"}, ref, {})["method"]
        == "explicit_field"
    )
    assert (
        attribution.resolve_selling_location({}, ref, {"ack_number": "NO"})["method"]
        == "unresolved"
    )
    assert (
        completeness.sequence_gaps(
            [{"vendor": "V", "invoice_number": f"I{x}"} for x in range(1, 6)]
        )
        == []
    )
    serial = [{"vendor": "V", "invoice_number": f"I{x}"} for x in (1, 3, 5, 7, 9)]
    assert len(completeness.sequence_gaps(serial)[0]["missing_runs"]) == 4
    thin = [
        {"vendor": "V", "month": month, "amount": 1}
        for month in ("2026-01", "2026-02", "2026-03")
        for _ in range(4)
    ]
    thin.append({"vendor": "V", "month": "2026-04", "amount": 1})
    assert completeness.calendar_gaps(thin)[0]["thin_months"]
    payment_docs = [
        {
            "doc_type": "invoice",
            "invoice_number": "I",
            "amount": 10,
            "credits": 0,
            "explicit_status": "",
            "vendor": "V",
            "date": "",
            "month": None,
        },
        {
            "doc_type": "note",
            "invoice_number": "N",
            "amount": 1,
            "credits": 0,
            "explicit_status": "",
            "vendor": "V",
            "date": "",
            "month": None,
        },
        {
            "doc_type": "invoice",
            "invoice_number": "Z",
            "amount": 0,
            "credits": 0,
            "explicit_status": "",
            "vendor": "V",
            "date": "",
            "month": None,
        },
    ]
    assert (
        completeness.aging_closure(
            payment_docs, [{"applications": [{"invoice_number": "I", "amount_applied": 10}]}]
        )["closed"]
        == 1
    )
    assert consensus.flatten({"lines": ["not a line"]}) == {}
    assert entity_resolve.sequence_ratio("", "x") == 0
    assert entity_resolve.load_records({"records": []}) == []
    assert (
        ingest_pages.has_text_layer(
            type("P", (), {"get": lambda *_args: (_ for _ in ()).throw(ValueError())})()
        )
        is False
    )
    output_file = tmp_path / "file"
    output_file.write_text("x")
    with pytest.raises(ValueError, match="not a directory"):
        ingest_pages.ensure_empty_output_dir(output_file)
    with pytest.raises(SystemExit, match="Input must"):
        invoke(monkeypatch, ingest_pages, tmp_path / "none.pdf", "--out", tmp_path / "out")
    assert sampling.stratum_of({"invoice_date": "2026-01-01"})["year"] == "2026"


def test_remaining_main_shapes_and_control_edges(monkeypatch, tmp_path):
    lines = [{"quantity": 1, "unit_price": 1, "extended_amount": None}]
    arithmetic_check.check_lines(
        {
            "lines": lines,
            "amendments": [{"target_line": 1, "target_column": "other", "amended_value": "bad"}],
        },
        0.01,
    )
    proven = {"header": {"subtotal": 1, "total_amount": 1}}
    out = tmp_path / "proof.json"
    invoke(
        monkeypatch,
        arithmetic_check,
        write_json(tmp_path / "proof-input.json", proven),
        "--out",
        out,
        "--quiet",
    )
    assert json.loads(out.read_text())["summary"]["findings"] == [
        "All documents passed self-proof."
    ]
    for module, args in (
        (arithmetic_check, ()),
        (attribution, ("--quiet",)),
        (completeness, ("--quiet",)),
        (sampling, ("--materiality", "1", "--quiet")),
    ):
        path = write_json(tmp_path / f"{module.__name__}.json", "not-a-record")
        with pytest.raises(SystemExit, match="No records"):
            invoke(monkeypatch, module, path, *args)
    assert (
        attribution.resolve_inherited(
            {"document_id": "d", "bol_number": "B"},
            {},
            {"by_shipment": {"B": ["d", "missing"]}, "by_invoice_number": {}},
        )
        is None
    )
    assert (
        attribution.resolve_inherited(
            {"document_id": "d", "invoice_number": "I"},
            {},
            {"by_shipment": {}, "by_invoice_number": {"I": "d"}},
        )
        is None
    )
    assert (
        attribution.resolve_fuzzy({"buyer_name": "A", "total_amount": 1}, {"by_customer": {}})
        is None
    )
    assert entity_resolve.load_records({"other": 1}) == [{"other": 1}]
    assert (
        entity_resolve.extract_parties([{"header": {"seller_name": {"value": "A"}}}])[0][0][
            "raw_name"
        ]
        == "A"
    )
    assert completeness.num(True) == 0
    assert (
        completeness.gl_variance(
            [{"month": "2026-01", "vendor": "V", "amount": 1}], {"2026-01": {"V": 1}}, 1
        )["largest_vendor_variances"]
        == []
    )
    assert (
        completeness.aging_closure(
            [
                {
                    "doc_type": "invoice",
                    "invoice_number": "",
                    "amount": 1,
                    "credits": 0,
                    "explicit_status": "",
                    "vendor": "V",
                    "date": "",
                }
            ],
            [{"applied": [{"invoice_number": "", "amount": 1}]}],
        )["unresolved"]
        == 1
    )
    assert sampling.project(
        [{"document_id": "unknown", "recorded_value": 1, "audited_value": 0}], 1, [], []
    )["findings_unmatched_to_sample"] == ["unknown"]
    assert sampling.build_attribute_sample(
        [
            {
                "document_id": "x",
                "value": 1,
                "stratum": {
                    "document_type": "x",
                    "year": "x",
                    "branch": "unknown",
                    "has_handwriting": False,
                    "jbig2_suspect": False,
                },
            }
        ],
        1,
        1,
    )
    mixed = Image.new("L", (4, 4), 255)
    mixed.putpixel((0, 0), 0)
    assert scan_profile._measure_quality(mixed)["contrast"] > 0
    assert (
        scan_profile.quality_band({"dpi": 300, "quality": {"ink_coverage": 0.001}})["quality_band"]
        == "poor"
    )

    class NonImagePage:
        Resources = type("R", (), {"XObject": {"n": {"/Subtype": "/Form"}}})()

        def get(self, key, default=None):
            return {"/MediaBox": [0, 0, 72, 72]}.get(key, default)

    assert scan_profile.profile_page(NonImagePage(), 1)["notes"] == ["no_raster_image"]


def test_final_reachable_branches(monkeypatch, tmp_path):
    arithmetic_check.check_lines(
        {
            "lines": [{"quantity": 1, "unit_price": 1, "extended_amount": 1}],
            "amendments": [{"target_line": 1, "target_column": "other", "amended_value": 1}],
        },
        0.01,
    )
    attribution.load_reference(
        write_json(
            tmp_path / "fuzzy-ref.json",
            [
                {
                    "ack_number": "ACK-9",
                    "date": "2026-01-01",
                    "customer": "Fuzzy",
                    "amount": 10,
                    "selling_location": "Boston",
                }
            ],
        ),
        None,
        None,
    )
    fuzzy = [
        {
            "document_id": "f",
            "buyer_name": "Fuzzy",
            "invoice_date": "2026-01-01",
            "total_amount": 10,
        }
    ]
    out, register = tmp_path / "fuzzy.json", tmp_path / "register.json"
    invoke(
        monkeypatch,
        attribution,
        write_json(tmp_path / "fuzzy-input.json", fuzzy),
        "--reference",
        tmp_path / "fuzzy-ref.json",
        "--out",
        out,
        "--register",
        register,
        "--quiet",
    )
    assert "inference" in json.loads(out.read_text())["summary"]["findings"][0]
    clean = [{"document_id": "c", "ack_number": "ANY", "branch": "B", "total_amount": 1}]
    invoke(
        monkeypatch,
        attribution,
        write_json(tmp_path / "clean.json", clean),
        "--out",
        tmp_path / "clean-out.json",
        "--register",
        tmp_path / "clean-register.json",
        "--quiet",
    )
    assert (
        json.loads((tmp_path / "clean-out.json").read_text())["summary"]["gate_status"] == "clear"
    )
    assert completeness.num({"value": None}) == 0
    consecutive = [{"vendor": "V", "invoice_number": f"I{x}"} for x in (1, 4, 5, 6, 7)]
    assert completeness.sequence_gaps(consecutive)[0]["missing_runs"][0]["count"] == 2
    clear_months = [{"vendor": "V", "month": f"2026-0{x}", "amount": 1} for x in range(1, 5)]
    assert completeness.calendar_gaps(clear_months) == []
    assert consensus.unflatten({"": {"value": 1}}) == {}
    ambiguous_mentions = [
        {
            "raw_name": "Alpha Cargo",
            "raw_address": None,
            "role": "biller",
            "document_id": "a",
            "field": "seller_name",
        },
        {
            "raw_name": "Alpha Cargx",
            "raw_address": None,
            "role": "biller",
            "document_id": "b",
            "field": "seller_name",
        },
    ]
    assert entity_resolve.cluster(ambiguous_mentions, 0.99, 0.5)[2]
    many_variants = [
        {
            "raw_name": f"Name {i}",
            "raw_address": None,
            "role": "biller",
            "document_id": str(i),
            "field": "seller_name",
        }
        for i in range(7)
    ]
    assert entity_resolve.build_parties(many_variants, {0: list(range(7))})[0]["needs_review"]
    records = [
        {
            "document_id": "sample",
            "review_status": "auto_accepted",
            "arithmetic_status": "proved",
            "total_amount": 1,
        }
    ]
    sample_input = write_json(tmp_path / "sample.json", records)
    sample_plan = tmp_path / "sample-plan.json"
    invoke(
        monkeypatch,
        sampling,
        sample_input,
        "--materiality",
        "10",
        "--out",
        sample_plan,
        "--quiet",
    )
    findings = write_json(
        tmp_path / "unmatched.json",
        {
            "schema_version": "sample_review_results_v1",
            "sample_plan_sha256": json.loads(sample_plan.read_text())["sample_plan_sha256"],
            "outcomes": [
                {
                    "document_id": "other",
                    "recorded_value": 1,
                    "audited_value": 0,
                    "outcome": "reviewed_error",
                }
            ],
        },
    )
    out = tmp_path / "unmatched-out.json"
    invoke(
        monkeypatch,
        sampling,
        sample_input,
        "--materiality",
        "10",
        "--findings",
        findings,
        "--out",
        out,
        "--quiet",
    )
    assert "every certainty and MUS selection" in json.loads(out.read_text())["notes"][0]
    assert (
        json.loads(out.read_text())["accuracy_statement"]["status"]
        == "blocked_incomplete_sample_review"
    )
    no_branch = tmp_path / "no-branch.json"
    invoke(
        monkeypatch,
        sampling,
        write_json(tmp_path / "no-branch-input.json", records),
        "--materiality",
        "10",
        "--out",
        no_branch,
        "--quiet",
    )
    assert "No branch attribute" in json.loads(no_branch.read_text())["notes"][-1]
    monkeypatch.setattr(scan_profile.np, "asarray", lambda _image: scan_profile.np.array([]))
    assert scan_profile._measure_quality(Image.new("L", (1, 1))) == {}


def test_strict_coverage_remaining_reachable_paths(monkeypatch, tmp_path, capsys):
    """Exercise real edge behavior rather than weakening the coverage gate."""
    # Completeness: retain undated documents and report a reconciled GL without
    # inventing a tolerance failure.
    docs = [
        {
            "document_id": "dated",
            "document_type": "invoice",
            "seller_name": "V",
            "invoice_number": "I-1",
            "invoice_date": "2026-01-01",
            "total_amount": 10,
        },
        {
            "document_id": "undated",
            "document_type": "invoice",
            "seller_name": "V",
            "invoice_number": "I-2",
            "total_amount": 2,
        },
    ]
    gl = write_json(
        tmp_path / "exact-gl.json", [{"period": "2026-01", "vendor": "V", "amount": 10}]
    )
    report_out = tmp_path / "exact-report.json"
    invoke(
        monkeypatch,
        completeness,
        write_json(tmp_path / "exact-docs.json", docs),
        "--gl",
        gl,
        "--out",
        report_out,
        "--quiet",
    )
    assert "periods exceed" not in " ".join(json.loads(report_out.read_text())["findings"])
    assert (
        completeness.gl_variance(
            [
                {"month": None, "vendor": "V", "amount": 2},
                {"month": "2026-01", "vendor": "V", "amount": 1},
            ],
            {"2026-01": {"V": 1}},
            1,
        )["by_period"][0]["variance"]
        == 0
    )

    # Consensus: a missing engine must not turn a no-consensus field into an
    # accepted value; a separate run covers the all-engine, quiet success path.
    merged, _ = consensus.merge_document("d", {"a": {"x": "one"}, "b": {"x": "two"}, "c": {}})
    assert merged["fields"]["x"]["consensus_flag"] == "no_consensus"
    queued, queued_exceptions = consensus.merge_document(
        "queued",
        {
            "a": {"x": "one", "y": "extra"},
            "b": {"x": "one", "y": "extra"},
            "c": {"x": "one"},
        },
    )
    assert queued["review_status"] == "review_queued" and queued_exceptions
    ca = write_consensus_handoff(
        tmp_path / "consensus-a.json",
        [{"document_id": "d", "amount": 1}],
        "openai",
        "model-a",
        "consensus_primary",
    )
    cb = write_consensus_handoff(
        tmp_path / "consensus-b.json",
        [
            {
                "document_id": "d",
                "amount": 1,
                "openai_input_mode": "pdf",
                "provider_review_flags": ["diagnostic"],
            }
        ],
        "google",
        "model-b",
        "consensus_secondary",
    )
    invoke(
        monkeypatch,
        consensus,
        ca,
        cb,
        "--out",
        tmp_path / "consensus.json",
        "--exceptions",
        tmp_path / "consensus-exceptions.json",
        "--quiet",
    )
    assert (
        json.loads((tmp_path / "consensus.json").read_text())["summary"]["open_exception_documents"]
        == 1
    )
    loaded = consensus.load_records([ca, cb])
    assert loaded["d"]["google_genai_vertex/model-b"]["_provider_exception"][
        "provider_review_flags"
    ] == ["diagnostic"]
    clean_a = write_consensus_handoff(
        tmp_path / "clean-a.json",
        [{"document_id": "clean", "amount": 1}],
        "openai",
        "model-a",
        "consensus_primary",
    )
    clean_b = write_consensus_handoff(
        tmp_path / "clean-b.json",
        [{"document_id": "clean", "amount": 1}],
        "google",
        "model-b",
        "consensus_secondary",
    )
    invoke(
        monkeypatch,
        consensus,
        clean_a,
        clean_b,
        "--out",
        tmp_path / "clean-consensus.json",
        "--exceptions",
        tmp_path / "clean-exceptions.json",
        "--quiet",
    )
    assert json.loads((tmp_path / "clean-consensus.json").read_text())["summary"]["warnings"] == [
        "No structural warnings."
    ]

    # Entity resolution: exact duplicates exercise the already-unioned branch;
    # typo candidates are logged for human adjudication and role-rich parties
    # remain explicitly reviewable in the CLI output.
    duplicate_mentions = [
        {
            "raw_name": "Alpha Cargo",
            "raw_address": None,
            "role": "biller",
            "document_id": str(i),
            "field": "seller_name",
        }
        for i in range(3)
    ]
    assert entity_resolve.cluster(duplicate_mentions, 0.9, 0.5)[1]
    entity_records = [
        {
            "document_id": "amb",
            "seller_name": "Alpha Cargo",
            "buyer_name": "Alpha Cargx",
            "ship_to_name": "Alpha Cargo",
            "shipper_name": "Alpha Cargo",
        }
    ]
    entity_out, entity_log = tmp_path / "parties.json", tmp_path / "merges.json"
    invoke(
        monkeypatch,
        entity_resolve,
        write_json(tmp_path / "parties-input.json", entity_records),
        "--threshold",
        ".99",
        "--review-band",
        ".5",
        "--out",
        entity_out,
        "--log",
        entity_log,
        "--quiet",
    )
    entity_data = json.loads(entity_out.read_text())
    assert entity_data["summary"]["pending_adjudication"] >= 1
    assert entity_data["summary"]["parties_needing_review"] >= 1
    invoke(
        monkeypatch,
        entity_resolve,
        write_json(tmp_path / "one-party.json", [{"seller_name": "Solo"}]),
        "--out",
        tmp_path / "one-party-out.json",
        "--log",
        tmp_path / "one-party-log.json",
    )
    assert "Mentions:" in capsys.readouterr().out

    # Intake: preserve an empty existing output directory; show both textless
    # exception routing and a classified page with no exception summary.
    empty_out = tmp_path / "empty-output"
    empty_out.mkdir()
    ingest_pages.ensure_empty_output_dir(empty_out)
    pdf = tmp_path / "source.pdf"
    document = scan_profile.pikepdf.Pdf.new()
    document.add_blank_page(page_size=(72, 72))
    document.save(pdf)
    monkeypatch.setattr(ingest_pages, "extract_page_text", lambda *_args: ("", "native_text"))
    textless_out = tmp_path / "textless-output"
    textless_out.mkdir()
    _, exceptions = ingest_pages.split_pdf(pdf, textless_out)
    assert exceptions["summary"]["count"] == 1
    monkeypatch.setattr(
        ingest_pages,
        "extract_page_text",
        lambda *_args: ("Invoice Number Invoice Date", "native_text"),
    )
    classified_out = tmp_path / "classified-output"
    classified_out.mkdir()
    manifest, exceptions = ingest_pages.split_pdf(pdf, classified_out)
    assert manifest["summary"]["exception_pages"] == exceptions["summary"]["count"] == 0

    # Scan profiling: explicit empty pixel data and pages with more than one
    # raster image cover the robust fallbacks used on imperfect PDFs.
    class EmptyPixels:
        size = 0

        def astype(self, _dtype):
            return self

    monkeypatch.setattr(scan_profile.np, "asarray", lambda _image: EmptyPixels())
    assert scan_profile._measure_quality(Image.new("L", (1, 1))) == {}
    monkeypatch.undo()
    assert (
        scan_profile.quality_band({"dpi": 300, "quality": {"ink_coverage": 0.1}})["quality_band"]
        == "good"
    )

    class XObject(dict):
        def __init__(self, width, height, bits=1):
            super().__init__(
                {
                    "/Subtype": "/Image",
                    "/Width": width,
                    "/Height": height,
                    "/BitsPerComponent": bits,
                    "/ColorSpace": "/DeviceGray",
                }
            )

    class TwoImagePage:
        Resources = type(
            "Resources", (), {"XObject": {"large": XObject(4, 4), "small": XObject(2, 2)}}
        )()

        def get(self, key, default=None):
            return {"/MediaBox": [0, 0, 72, 72]}.get(key, default)

    assert scan_profile.profile_page(TwoImagePage(), 0, deep=False)["width_px"] == 4
    monkeypatch.setattr(
        scan_profile.pikepdf,
        "PdfImage",
        lambda _obj: type(
            "Decoded", (), {"as_pil_image": lambda self: Image.new("L", (2, 2), 255)}
        )(),
    )
    assert scan_profile.profile_page(TwoImagePage(), 0, deep=True)["quality"]
    assert scan_profile.collect_pdfs(str(tmp_path))
    (tmp_path / "not-a-pdf.txt").write_text("ignored")
    assert all(path.endswith(".pdf") for path in scan_profile.collect_pdfs(str(tmp_path)))
    monkeypatch.setattr(scan_profile, "HAVE_PIXEL_TOOLS", False)
    scan_out = tmp_path / "forced-shallow.json"
    invoke(monkeypatch, scan_profile, pdf, "--out", scan_out)
    assert "numpy/Pillow unavailable" in capsys.readouterr().err
    invoke(monkeypatch, scan_profile, pdf, "--out", tmp_path / "quiet-profile.json", "--quiet")
