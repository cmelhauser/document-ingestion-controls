"""Regression tests for the critical and high-severity control corrections.

Each test names the defect it prevents from returning. Every one of these paths
passed the strict gate before the fix, so the assertions here are the only thing
standing between the repository and a silent regression.
"""

import importlib
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

allocation_policy = importlib.import_module("allocation_policy")
arithmetic_check = importlib.import_module("arithmetic_check")
client_review_package = importlib.import_module("client_review.package")
completeness = importlib.import_module("completeness")
consensus = importlib.import_module("consensus")
independent_table_reconcile = importlib.import_module("independent_table_reconcile")


# --- F22: an implausible commission rate must never classify as approved credit ---

POLICY_RULE = {
    "rule_id": "r1",
    "registry_version": "v1",
    "scope": {},
    "policy": {
        "effective_rate_threshold": 0.02,
        "explicit_ship_to_precedence": False,
        "allow_sales_generated": True,
    },
}


def leg(commissionable, commission, **extra):
    return {
        "allocation_id": "a1",
        "document_id": "d1",
        "commissionable_amount": commissionable,
        "commission_amount": commission,
        **extra,
    }


def test_commission_rate_above_the_ceiling_never_becomes_sales_credit():
    # A displaced decimal point produced a 1000% effective rate that classified as
    # approved sales credit with no exception and no review flag.
    output, exception = allocation_policy.classify(leg("500.00", "5000.00"), POLICY_RULE)
    assert output["classification"] == "review_required"
    assert output["sales_credit_eligible"] is None
    assert output["client_review_required"] is True
    assert output["reason"] == "allocation_effective_rate_exceeds_plausibility_ceiling"
    assert output["derived"]["effective_rate_ceiling"] == "0.5000"
    assert exception["priority"] == "critical"


def test_plausible_commission_rate_still_classifies():
    output, exception = allocation_policy.classify(leg("1000.00", "50.00"), POLICY_RULE)
    assert output["classification"] == "sales_generated"
    assert output["sales_credit_eligible"] is True
    assert exception is None


@pytest.mark.parametrize("ceiling", [5, 0, -1, "not-a-number", True])
def test_out_of_range_configured_ceiling_fails_closed(ceiling):
    rule = {**POLICY_RULE, "policy": {**POLICY_RULE["policy"], "effective_rate_ceiling": ceiling}}
    output, exception = allocation_policy.classify(leg("1000.00", "50.00"), rule)
    assert output["reason"] == "allocation_policy_effective_rate_ceiling_invalid"
    assert output["client_review_required"] is True
    assert exception["priority"] == "critical"


def test_a_lowered_ceiling_is_honoured():
    rule = {**POLICY_RULE, "policy": {**POLICY_RULE["policy"], "effective_rate_ceiling": 0.03}}
    output, _ = allocation_policy.classify(leg("1000.00", "40.00"), rule)
    assert output["reason"] == "allocation_effective_rate_exceeds_plausibility_ceiling"


# --- F7: rollup tolerance must scale with the rows a check actually sums ---


def test_header_identity_keeps_the_base_tolerance_on_a_long_document():
    # A 500-line document used to receive a $5.00 tolerance on its header total,
    # so a two-dollar misread in the dollars column passed self-proof.
    lines = [
        {"line_number": index + 1, "quantity": 1, "unit_price": 10.0, "extended_amount": 10.0}
        for index in range(500)
    ]
    record = {
        "document_id": "d1",
        "lines": lines,
        "header": {"subtotal": 5000.0, "tax_amount": 0.0, "total_amount": 5002.0},
    }
    result = arithmetic_check.check_document(record, 0.01)
    header = next(
        check
        for check in result["checks"]
        if check["check"] == "subtotal_plus_charges_equals_total"
    )
    assert header["tolerance_used"] == 0.01
    assert header["status"] == "fail"
    assert result["arithmetic_status"] == "failed"


def test_line_rollup_still_receives_its_per_row_allowance():
    lines = [
        {"line_number": index + 1, "quantity": 1, "unit_price": 10.0, "extended_amount": 10.0}
        for index in range(5)
    ]
    record = {"document_id": "d1", "lines": lines, "header": {"subtotal": 50.03}}
    rollup = next(
        check
        for check in arithmetic_check.check_document(record, 0.01)["checks"]
        if check["check"] == "lines_sum_to_subtotal"
    )
    assert rollup["tolerance_used"] == 0.05
    assert rollup["status"] == "pass"


def test_accessorial_rollup_scales_on_accessorial_rows_not_line_rows():
    lines = [
        {"line_number": index + 1, "quantity": 1, "unit_price": 10.0, "extended_amount": 10.0}
        for index in range(500)
    ]
    record = {
        "document_id": "d1",
        "lines": lines,
        "accessorials": [{"charge_amount": 5.0}],
        "header": {"subtotal": 5000.0, "accessorial_total": 7.0, "total_amount": 5007.0},
    }
    accessorial = next(
        check
        for check in arithmetic_check.check_document(record, 0.01)["checks"]
        if check["check"] == "accessorial_lines_sum_to_total"
    )
    assert accessorial["tolerance_used"] == 0.01
    assert accessorial["status"] == "fail"


def test_unreadable_lines_block_a_proved_status(tmp_path, capsys):
    # Header arithmetic cannot prove rows that were never read.
    record = {
        "document_id": "d2",
        "lines": [{"line_number": 1, "extended_amount": 100.0}, {"line_number": 2}],
        "header": {"subtotal": 100.0, "tax_amount": 0.0, "total_amount": 100.0},
    }
    result = arithmetic_check.check_document(record, 0.01)
    assert result["arithmetic_status"] == "proved_with_unproved_lines"
    assert result["lines_missing_fields"] == 2
    assert "not self-proved" in result["disposition"]

    source = tmp_path / "records.json"
    source.write_text(__import__("json").dumps([record]))
    sys.argv = ["arithmetic_check.py", str(source), "--out", str(tmp_path / "out.json")]
    arithmetic_check.main()
    assert "balanced at the header" in capsys.readouterr().out


# --- F3: consensus grouping must not depend on argument order ---


def test_consensus_clustering_is_independent_of_engine_order():
    # 100.000/100.004/100.008 sit inside a non-transitive tolerance chain. First
    # match grouping let the argument order pick both the strength and the value.
    readings = [
        ("e1", {"value": 100.000}),
        ("e2", {"value": 100.004}),
        ("e3", {"value": 100.008}),
    ]
    results = [
        consensus.reconcile_field("total_amount", list(order))
        for order in (readings, list(reversed(readings)), [readings[1], readings[0], readings[2]])
    ]
    assert {result["consensus_flag"] for result in results} == {"consensus_2of3"}
    assert {result["value"] for result in results} == {100.0}


def test_a_reading_matching_two_clusters_withholds_consensus():
    # Mixed scalar types compare numerically against a number and textually
    # against each other, so agreement is not transitive and no grouping is
    # canonical. That is a borderline reading, not a majority.
    readings = [
        ("a", {"value": "100.0"}),
        ("b", {"value": "100.000"}),
        ("c", {"value": 100.0}),
    ]
    field = consensus.reconcile_field("memo", readings)
    assert field["consensus_flag"] == "no_consensus"
    assert field["accepted"] is False


def test_a_unanimous_pair_below_the_required_rule_is_not_labelled_as_consensus():
    handwritten = [
        ("e1", {"value": 500, "source": "handwritten"}),
        ("e2", {"value": 500, "source": "handwritten"}),
    ]
    field = consensus.reconcile_field("total_amount", handwritten)
    assert field["consensus_flag"] == "consensus_2of2_below_required_3"
    assert field["accepted"] is False


# --- F5: independence must survive a router ---


def test_a_routed_model_does_not_create_a_second_independence_group(tmp_path):
    from contract_helpers import write_consensus_handoff

    direct = write_consensus_handoff(
        tmp_path / "direct.json",
        [{"document_id": "d1", "total_amount": 1}],
        "openai",
        "gpt-5.6-terra",
        "consensus_primary",
    )
    routed = write_consensus_handoff(
        tmp_path / "routed.json",
        [{"document_id": "d1", "total_amount": 1}],
        "openrouter",
        "openai/gpt-5.6-terra",
        "consensus_secondary",
    )
    with pytest.raises(ValueError, match="does not create independence"):
        consensus.load_records([direct, routed])


def test_a_routed_model_from_another_vendor_remains_independent(tmp_path):
    from contract_helpers import write_consensus_handoff

    direct = write_consensus_handoff(
        tmp_path / "direct.json",
        [{"document_id": "d1", "total_amount": 1}],
        "openai",
        "gpt-5.6-terra",
        "consensus_primary",
    )
    routed = write_consensus_handoff(
        tmp_path / "routed.json",
        [{"document_id": "d1", "total_amount": 1}],
        "openrouter",
        "nvidia/nemotron-3",
        "consensus_secondary",
    )
    loaded = consensus.load_records([direct, routed])
    assert set(loaded["d1"]) == {"openai/gpt-5.6-terra", "openrouter/nvidia/nemotron-3"}


def test_an_unresolvable_routed_slug_is_refused(tmp_path):
    from contract_helpers import write_consensus_handoff

    routed = write_consensus_handoff(
        tmp_path / "routed.json",
        [{"document_id": "d1", "total_amount": 1}],
        "openrouter",
        "bare-model",
        "consensus_primary",
    )
    with pytest.raises(ValueError, match="routed model vendor cannot be resolved"):
        consensus.load_records([routed])


# --- F2: the GL baseline must never shrink silently ---


def test_rejected_gl_rows_block_a_tolerance_verdict():
    buckets = {"2026-01": {"Acme": 100000.0}}
    rejected = [{"row_index": 1, "reason": "gl_row_period_unparseable"}]
    variance = completeness.gl_variance(
        [{"month": "2026-01", "vendor": "Acme", "amount": 100000.0}], buckets, 5.0, rejected
    )
    assert variance["status"] == "completed_with_rejected_gl_rows"
    assert variance["gl_rows_rejected"] == 1
    assert [row["within_tolerance"] for row in variance["by_period"]] == [None]
    assert "never plugged" in variance["rule"]


def test_rejected_gl_rows_block_the_completeness_gate(tmp_path, monkeypatch, capsys):
    import json

    records = tmp_path / "records.json"
    records.write_text(
        json.dumps(
            [
                {
                    "document_id": "i1",
                    "document_type": "invoice",
                    "vendor_name": "Acme",
                    "invoice_number": "INV-1",
                    "invoice_date": "2026-01-05",
                    "total_amount": 100000.0,
                }
            ]
        )
    )
    gl = tmp_path / "gl.json"
    gl.write_text(json.dumps([{"period": "Q1-2026", "vendor": "Acme", "amount": 250000}]))
    out = tmp_path / "completeness.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["completeness.py", str(records), "--gl", str(gl), "--out", str(out), "--quiet"],
    )
    completeness.main()
    capsys.readouterr()
    report = json.loads(out.read_text())
    assert report["gl_variance"]["gl_rows_rejected"] == 1
    assert "gl_rows_rejected" in report["gate_reasons"]
    assert report["gate_status"] == "blocked"


# --- F21: a control that compared nothing has not corroborated anything ---


def test_reconciliation_that_produced_no_comparison_is_an_exception(tmp_path):
    source = tmp_path / "rows.json"
    source.write_text('{"source_rows": []}')
    registry = tmp_path / "registry.json"
    registry.write_text('{"rules": []}')
    handoff = tmp_path / "ai.json"
    handoff.write_text('{"provider": "google_document_ai", "records": []}')
    result = independent_table_reconcile.run(
        source,
        registry,
        [handoff],
        tmp_path / "out.json",
        tmp_path / "exceptions.json",
        tmp_path / "adapter.json",
    )
    assert result["corroborated"] is False
    assert result["review_items"] == 1
    assert result["coverage"]["cells_mapped"] == 0


# --- F4: the untrusted workbook path must reject entity expansion ---


def workbook(tmp_path, name, workbook_xml, extra=None):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/_rels/workbook.xml.rels", "<Relationships/>")
        archive.writestr("xl/workbook.xml", workbook_xml)
        for member, value in (extra or {}).items():
            archive.writestr(member, value)
    return path


def test_a_workbook_declaring_entities_is_refused(tmp_path):
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol1 "&lol;&lol;&lol;&lol;">]><workbook>&lol1;</workbook>'
    )
    path = workbook(tmp_path, "bomb.xlsx", bomb)
    with pytest.raises(ValueError, match="declares a DTD or entity"):
        client_review_package._validate_workbook_archive(path)


def test_a_workbook_without_a_dtd_is_accepted(tmp_path):
    path = workbook(tmp_path, "clean.xlsx", "<workbook><sheets/></workbook>")
    client_review_package._validate_workbook_archive(path)


NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
REL_NS = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def test_a_malformed_workbook_package_raises_a_clean_error(tmp_path):
    # Each of these previously surfaced as TypeError, StopIteration, or KeyError.
    no_sheets = workbook(tmp_path, "no-sheets.xlsx", f"<workbook {NS}/>")
    with pytest.raises(ValueError, match="does not contain a 'Decisions Needed' worksheet"):
        client_review_package._xlsx_cell_values(no_sheets, "Decisions Needed")

    other_sheet = workbook(
        tmp_path, "other.xlsx", f'<workbook {NS}><sheets><sheet name="Other"/></sheets></workbook>'
    )
    with pytest.raises(ValueError, match="does not contain a 'Decisions Needed' worksheet"):
        client_review_package._xlsx_cell_values(other_sheet, "Decisions Needed")

    unknown_rel = workbook(
        tmp_path,
        "unknown-rel.xlsx",
        f"<workbook {NS} {REL_NS}><sheets>"
        f'<sheet name="Decisions Needed" r:id="rIdMissing"/></sheets></workbook>',
    )
    with pytest.raises(ValueError, match="no relationship target"):
        client_review_package._xlsx_cell_values(unknown_rel, "Decisions Needed")

    missing_part = tmp_path / "missing-part.xlsx"
    with zipfile.ZipFile(missing_part, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            f"<workbook {NS} {REL_NS}><sheets>"
            f'<sheet name="Decisions Needed" r:id="rId1"/></sheets></workbook>',
        )
    with pytest.raises(ValueError, match="worksheet part is missing"):
        client_review_package._xlsx_cell_values(missing_part, "Decisions Needed")


def test_an_unknown_shared_string_index_raises_a_clean_error(tmp_path):
    path = tmp_path / "shared.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            f"<workbook {NS} {REL_NS}><sheets>"
            f'<sheet name="Decisions Needed" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet {NS}><sheetData><row><c r="A1" t="s"><v>99</v></c>'
            f"</row></sheetData></worksheet>",
        )
        archive.writestr("xl/sharedStrings.xml", f"<sst {NS}><si><t>only</t></si></sst>")
    with pytest.raises(ValueError, match="unknown shared string"):
        client_review_package._xlsx_cell_values(path, "Decisions Needed")


# --- Medium and low corrections ---


def test_ambiguous_rate_units_are_registered_rather_than_guessed():
    # "0.75" is equally 0.75% and 75%; sub-1% override commissions are ordinary
    # here, and guessing read them 100x high.
    output, exception = allocation_policy.classify(
        leg("1000.00", "7.50", stated_commission_rate="0.75", allocation_share="100"),
        POLICY_RULE,
    )
    assert output["reason"] == "allocation_rate_unit_ambiguous"
    assert output["derived"]["ambiguous_rate_fields"] == ["stated_commission_rate"]
    assert output["client_review_required"] is True
    assert exception["priority"] == "critical"


def test_an_unproved_formula_retracts_eligibility():
    # The derived-field contract defines sales_credit_eligible as true only for
    # approved work and null when unresolved.
    output, exception = allocation_policy.classify(
        leg("1000.00", "50.00", stated_commission_rate="5", allocation_share="50"),
        POLICY_RULE,
    )
    assert output["reason"] == "allocation_formula_not_proved"
    assert output["classification"] == "review_required"
    assert output["sales_credit_eligible"] is None
    assert output["client_review_required"] is True
    assert exception["priority"] == "critical"


def test_an_unverified_bit_depth_says_so_in_the_verdict():
    import importlib

    scan_profile = importlib.import_module("scan_profile")
    unverified = scan_profile.classify(
        {"bit_depth": None, "filters": [], "chroma_fraction": None, "notes": []}
    )
    assert unverified["branch"] == "B"
    assert "depth_unverified" in unverified["notes"]
    measured = scan_profile.classify(
        {"bit_depth": 4, "filters": [], "chroma_fraction": None, "notes": []}
    )
    assert "depth_unverified" not in measured["notes"]


def test_the_effective_prompt_ceiling_names_the_bound_that_governs():
    import importlib

    llm_runtime = importlib.import_module("llm_runtime")
    # max_request_tokens looks generous but the per-minute budget binds first.
    throttle = llm_runtime.RequestThrottle(
        None,
        "google",
        0,
        tokens_per_minute=80000,
        max_request_tokens=200000,
        output_reserve=8192,
        safety_ratio=0.8,
    )
    assert throttle.effective_prompt_ceiling() == 64000 - 8192
    with pytest.raises(llm_runtime.RateLimitConfigurationError, match="effective per-request"):
        throttle.acquire(request_tokens=60000)
    # With no configured limits there is no ceiling to report.
    assert llm_runtime.RequestThrottle(None, "x", 0).effective_prompt_ceiling() == 0.0


def test_reference_rows_without_a_recognized_key_are_registered(tmp_path):
    import importlib

    attribution = importlib.import_module("attribution")
    reference = tmp_path / "ref.csv"
    reference.write_text("acknowledgement_no,amount\nACK-1,100\n")
    loaded = attribution.load_reference(str(reference), None, None)
    assert loaded["rows_ingested"] == 0
    assert loaded["rejected_rows"][0]["reason"] == "reference_row_has_no_recognized_key_column"
    assert loaded["rejected_rows"][0]["available_columns"] == ["acknowledgement_no", "amount"]


def test_an_oversized_blocking_key_is_deferred_not_dropped():
    import importlib

    entity_resolve = importlib.import_module("entity_resolve")
    # The excluded population is the highest-cardinality party names -- the ones
    # most worth resolving -- so the deferral must stay visible as review work.
    # Distinct readings, because repetitions of one reading are united before
    # any pairwise work and must not spend the cap -- a name printed on
    # hundreds of lines is the commonest party, not an unresolvable block.
    mentions = [
        {
            "raw_name": f"Placeholder Supply Co {index}",
            "raw_address": "",
            "document_id": f"d{index}",
        }
        for index in range(entity_resolve.MAX_BLOCK_PAIRWISE + 1)
    ]
    _, evidence, ambiguous = entity_resolve.cluster(mentions, 0.9, 0.8)
    deferred = [item for item in ambiguous if item.get("blocking_key")]
    assert deferred, "an over-cap block must remain visible as review work"
    assert deferred[0]["mention_count"] > entity_resolve.MAX_BLOCK_PAIRWISE
    assert deferred[0]["decision"] == "pending_adjudication"
    assert evidence == []
    repeated = [
        {"raw_name": "Placeholder Supply Co", "raw_address": "", "document_id": f"d{index}"}
        for index in range(entity_resolve.MAX_BLOCK_PAIRWISE + 1)
    ]
    groups, evidence, ambiguous = entity_resolve.cluster(repeated, 0.9, 0.8)
    assert len(groups) == 1 and not ambiguous
    assert [item["reason"] for item in evidence] == ["identical_normalized_reading"]


def test_series_group_across_a_zero_padding_rollover():
    coverage = []
    docs = [
        {"vendor": "V", "invoice_number": f"INV-{number}", "month": "2026-01", "amount": 1}
        for number in (997, 998, 999, 1000, 1002)
    ]
    gaps = completeness.sequence_gaps(docs, coverage)
    # A rollover used to split into two width-keyed series, each below the
    # inference threshold, so the gap at 1001 disappeared entirely.
    assert coverage == []
    assert gaps[0]["missing_count"] == 1
    assert gaps[0]["missing_runs_truncated"] == 0


def test_unanalyzed_series_and_vendors_are_enumerated():
    sequence_coverage, calendar_coverage = [], []
    completeness.sequence_gaps(
        [
            {"vendor": "V", "invoice_number": "NOSUFFIX", "month": "2026-01", "amount": 1},
            {"vendor": "W", "invoice_number": "INV-1", "month": "2026-01", "amount": 1},
        ],
        sequence_coverage,
    )
    reasons = {item["reason"] for item in sequence_coverage}
    assert reasons == {
        "invoice_number_has_no_numeric_suffix",
        "series_below_minimum_for_sequence_inference",
    }
    completeness.calendar_gaps(
        [
            {"vendor": "V", "month": None, "amount": 1},
            {"vendor": "W", "month": "2026-01", "amount": 1},
        ],
        calendar_coverage,
    )
    assert {item["reason"] for item in calendar_coverage} == {
        "document_date_not_parseable",
        "vendor_below_minimum_months_for_calendar_inference",
    }


def test_reference_nodes_require_a_whole_token_match():
    import importlib

    evidence_graph = importlib.import_module("evidence_graph")
    for field in ("po_number", "ack_number", "job_number", "payment_reference", "check_number"):
        assert evidence_graph._reference_field(field) is True
    for field in (
        "shipping_postal_code",
        "package_tracking",
        "report_date",
        "point_of_contact",
        "backorder_flag",
        "support_email",
    ):
        assert evidence_graph._reference_field(field) is False


def test_the_token_estimate_safety_factor_is_bounded(monkeypatch):
    import importlib

    llm_runtime = importlib.import_module("llm_runtime")
    monkeypatch.setenv("LLM_TOKEN_ESTIMATE_SAFETY_FACTOR", "1.5")
    assert llm_runtime.token_estimate_safety_factor() == 1.5
    monkeypatch.setenv("LLM_TOKEN_ESTIMATE_SAFETY_FACTOR", "9")
    with pytest.raises(ValueError, match="1.0 through 4.0"):
        llm_runtime.token_estimate_safety_factor()


def test_wide_characters_raise_the_token_estimate(monkeypatch):
    import importlib

    llm_runtime = importlib.import_module("llm_runtime")
    monkeypatch.delenv("LLM_TOKEN_ESTIMATE_SAFETY_FACTOR", raising=False)
    # Characters-over-four underestimated CJK by several times, and the error ran
    # in the permissive direction: an oversized request reached the provider.
    latin = llm_runtime.estimate_tokens("a" * 60)
    wide = llm_runtime.estimate_tokens("\u6587" * 60)
    assert wide > latin


def test_an_unanalyzed_population_is_stated_as_a_limitation(tmp_path, monkeypatch, capsys):
    import json

    records = tmp_path / "records.json"
    records.write_text(
        json.dumps(
            [
                {
                    "document_id": "i1",
                    "document_type": "invoice",
                    "vendor_name": "Acme",
                    "invoice_number": "INV-1",
                    "invoice_date": "2026-01-05",
                    "total_amount": 10.0,
                }
            ]
        )
    )
    out = tmp_path / "completeness.json"
    monkeypatch.setattr(
        sys, "argv", ["completeness.py", str(records), "--out", str(out), "--quiet"]
    )
    completeness.main()
    capsys.readouterr()
    report = json.loads(out.read_text())
    assert report["inference_coverage"]["sequence_not_analyzed"]
    assert any("not analyzed for gaps" in finding for finding in report["findings"])


def test_a_fully_analyzed_corpus_states_no_coverage_limitation(tmp_path, monkeypatch, capsys):
    import json

    records = tmp_path / "records.json"
    records.write_text(
        json.dumps(
            [
                {
                    "document_id": f"i{number}",
                    "document_type": "invoice",
                    "vendor_name": "Acme",
                    "invoice_number": f"INV-{1000 + number}",
                    "invoice_date": f"2026-0{number + 1}-05",
                    "total_amount": 10.0,
                }
                for number in range(5)
            ]
        )
    )
    out = tmp_path / "completeness.json"
    monkeypatch.setattr(
        sys, "argv", ["completeness.py", str(records), "--out", str(out), "--quiet"]
    )
    completeness.main()
    capsys.readouterr()
    report = json.loads(out.read_text())
    assert report["inference_coverage"] == {
        "sequence_not_analyzed": [],
        "calendar_not_analyzed": [],
    }
    assert not any("not analyzed for gaps" in finding for finding in report["findings"])
