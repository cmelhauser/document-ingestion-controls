"""Behavioural tests for the auditable control-layer contracts."""

from __future__ import annotations

import csv
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from contract_helpers import write_consensus_handoff

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

arithmetic_check = importlib.import_module("arithmetic_check")
attribution = importlib.import_module("attribution")
completeness = importlib.import_module("completeness")
consensus = importlib.import_module("consensus")
entity_resolve = importlib.import_module("entity_resolve")
sampling = importlib.import_module("sampling")
scan_profile = importlib.import_module("scan_profile")


def field(value, source="printed"):
    return {"value": value, "confidence": 0.99, "source": source}


def invoice(document_id="INV-001", total=110.0):
    return {
        "document_id": document_id,
        "document_type": "invoice",
        "branch": "B",
        "header": {
            "invoice_number": "1001",
            "invoice_date": "2026-01-15",
            "seller_name": "ACME Logistics LLC",
            "seller_address": "1 Main St, Boston MA 02110",
            "buyer_name": "Northwind Inc",
            "buyer_address": "2 State St, Boston MA 02110",
            "subtotal": field(100),
            "tax_amount": field(10),
            "total_amount": field(total),
            "ack_number": "ACK-1001",
        },
        "lines": [
            {
                "line_number": 1,
                "quantity": field(2),
                "unit_price": field(50),
                "extended_amount": field(100),
            }
        ],
    }


def test_consensus_accepts_agreement_and_routes_disagreement():
    agreed = consensus.reconcile_field("total_amount", [("a", field("100.00")), ("b", field(100))])
    majority = consensus.reconcile_field(
        "total_amount", [("a", field(100)), ("b", field(100.001)), ("c", field(101))]
    )
    handwritten = consensus.reconcile_field(
        "total_amount", [("a", field(100, "handwritten")), ("b", field(100, "handwritten"))]
    )
    assert agreed["accepted"] and agreed["consensus_flag"] == "consensus_2of2"
    assert majority["accepted"] and majority["queue_for_review"]
    assert not handwritten["accepted"] and handwritten["rule"] == "handwritten_numeric_3of3"
    identifier = consensus.reconcile_field(
        "invoice_number", [("a", field("001")), ("b", field("1"))]
    )
    assert not identifier["accepted"] and identifier["consensus_flag"] == "no_consensus"


def test_consensus_document_roundtrip_preserves_record_shape():
    engines = {"a": invoice(), "b": invoice(), "c": invoice()}
    result, exceptions = consensus.merge_document("INV-001", engines)
    assert result["review_status"] == "auto_accepted"
    assert result["header"]["total_amount"]["value"] == 110
    assert result["lines"][0]["quantity"]["value"] == 2
    assert not exceptions


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$1,234.50", 1234.5),
        ("(15)", -15.0),
        (True, None),
        ("bad", None),
        # A decimal comma with a space thousands separator is a convention this
        # cannot decide, and guessing turns it into a hundred times the money.
        ("16 430,72", None),
        ("0,00", None),
    ],
)
def test_arithmetic_number_parsing(raw, expected):
    assert arithmetic_check.num(raw) == expected


def test_arithmetic_proves_and_rejects_document():
    passed = arithmetic_check.check_document(invoice(), 0.01)
    failed = arithmetic_check.check_document(invoice(total=109), 0.01)
    assert passed["arithmetic_status"] == "proved"
    assert failed["arithmetic_status"] == "failed"
    assert failed["largest_discrepancy"] == 1.0


def test_entity_normalization_and_cluster_are_reversible():
    records = [invoice("1"), invoice("2")]
    records[1]["header"]["seller_name"] = "Acme Logistics, L.L.C."
    mentions, _ = entity_resolve.extract_parties(records)
    groups, evidence, ambiguous = entity_resolve.cluster(mentions, 0.80, 0.60)
    parties, merge_log = entity_resolve.build_parties(mentions, groups)
    assert entity_resolve.normalize_name("ACME, L.L.C.") == "acme"
    assert any(len(group) == 2 for group in groups.values())
    assert parties and merge_log
    assert isinstance(evidence, list) and isinstance(ambiguous, list)


def test_attribution_reference_and_resolution(tmp_path):
    reference = tmp_path / "acks.csv"
    with reference.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["ack_number", "date", "customer", "amount", "selling_location"]
        )
        writer.writeheader()
        writer.writerow(
            {
                "ack_number": "ACK-1001",
                "date": "2026-01-15",
                "customer": "Northwind Inc",
                "amount": "110",
                "selling_location": "Boston",
            }
        )
    refs = attribution.load_reference(reference, r"ACK-\d{4}", None)
    resolved = attribution.resolve_explicit(invoice(), refs)
    assert attribution.parse_date("01/15/2026").isoformat() == "2026-01-15"
    assert resolved["key"] == "ACK-1001"
    assert attribution.validate_key("ACK-1001", refs)[0]


def test_completeness_reports_sequence_calendar_gl_and_aging(tmp_path):
    docs = [invoice(str(number)) for number in range(1, 6)]
    for document, number, month in zip(docs, (1, 3, 5, 7, 9), (1, 3, 4, 5, 6), strict=True):
        document["header"]["invoice_number"] = f"INV-{number:03d}"
        document["header"]["invoice_date"] = f"2026-{month:02d}-15"
    normalized = completeness.extract_docs(docs)
    gaps = completeness.sequence_gaps(normalized)
    calendar = completeness.calendar_gaps(normalized)
    gl = tmp_path / "gl.csv"
    gl.write_text("period,amount\n2026-01,550\n")
    gl_buckets, gl_rejected = completeness.load_gl(str(gl))
    assert gl_rejected == []
    variance = completeness.gl_variance(normalized, gl_buckets, 2.0, gl_rejected)
    aging = completeness.aging_closure(normalized, [{"invoice_number": "INV-001", "amount": 50}])
    assert gaps[0]["missing_count"] == 4
    assert calendar[0]["missing_months"] == ["2026-02"]
    assert variance["extracted_total"] == 550.0
    assert aging["partially_applied"] == 1


def test_sampling_builds_reproducible_plan_and_projection():
    population = [
        {
            "document_id": "a",
            "value": 10,
            "stratum": {
                "document_type": "invoice",
                "year": "2026",
                "branch": "B",
                "has_handwriting": False,
                "jbig2_suspect": False,
            },
        },
        {
            "document_id": "b",
            "value": 20,
            "stratum": {
                "document_type": "invoice",
                "year": "2026",
                "branch": "B",
                "has_handwriting": True,
                "jbig2_suspect": False,
            },
        },
        {
            "document_id": "c",
            "value": 100,
            "stratum": {
                "document_type": "invoice",
                "year": "2026",
                "branch": "B",
                "has_handwriting": False,
                "jbig2_suspect": False,
            },
        },
    ]
    certainty, selected, interval = sampling.build_mus(population, 50, 2, 7)
    plan = sampling.build_attribute_sample(population, 3, 7)
    projection = sampling.project([], interval, certainty, selected)
    assert [doc["document_id"] for doc in certainty] == ["c", "b"]
    assert [doc["document_id"] for doc in selected] == ["a"]
    assert selected and plan and projection["upper_overstatement_bound_95pct"] >= 0
    assert not sampling.eligible({"review_status": "auto_accepted", "arithmetic_status": "failed"})[
        0
    ]


def test_sampling_refuses_a_rollup_report_instead_of_faking_one_record(tmp_path, monkeypatch):
    rollup = tmp_path / "completeness.json"
    rollup.write_text(json.dumps({"corpus": {}, "gate_reasons": [], "gate_status": "blocked"}))
    out = tmp_path / "sample_plan.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["sampling.py", str(rollup), "--materiality", "1000", "--out", str(out)],
    )
    with pytest.raises(SystemExit, match="no recognized per-document list"):
        sampling.main()
    assert not out.exists()


def test_sampling_accepts_a_bare_single_document_record(tmp_path, monkeypatch):
    record = tmp_path / "record.json"
    record.write_text(json.dumps({"document_id": "solo", "value": 10}))
    out = tmp_path / "sample_plan.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["sampling.py", str(record), "--materiality", "1000", "--out", str(out)],
    )
    sampling.main()
    assert out.exists()


def test_scan_profile_classifies_and_summarizes():
    gray = scan_profile.classify(
        {"bit_depth": 8, "filters": [], "chroma_fraction": 0.0, "notes": []}
    )
    bilevel = scan_profile.classify(
        {
            "bit_depth": 1,
            "filters": ["/JBIG2Decode"],
            "chroma_fraction": None,
            "notes": ["jbig2_compressed"],
        }
    )
    scan_profile.quality_band(gray)
    summary = scan_profile.summarize([gray, bilevel])
    assert gray["branch"] == "B"
    assert bilevel["jbig2_suspect"]
    assert summary["total_pages_profiled"] == 2


def test_every_cli_supports_help():
    for script in sorted(SCRIPTS.glob("*.py")):
        completed = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr


def test_json_control_chain_cli(tmp_path):
    engines = []
    configurations = (
        ("a", "openai", "model-a", "consensus_primary"),
        ("b", "google", "model-b", "consensus_secondary"),
        ("c", "openrouter", "nvidia/model-c", "consensus_tiebreaker"),
    )
    for name, provider, model, lane in configurations:
        path = tmp_path / f"{name}.json"
        write_consensus_handoff(path, [invoice()], provider, model, lane)
        engines.append(path)
    consensus_out, exceptions = tmp_path / "consensus.json", tmp_path / "exceptions.json"
    proofed, parties, merge_log = (
        tmp_path / "proofed.json",
        tmp_path / "parties.json",
        tmp_path / "merges.json",
    )
    reference, attributed = tmp_path / "acks.csv", tmp_path / "attributed.json"
    register, completeness_out, sampling_out = (
        tmp_path / "unattributable.json",
        tmp_path / "completeness.json",
        tmp_path / "sample.json",
    )
    reference.write_text(
        "ack_number,date,customer,amount,selling_location\nACK-1001,2026-01-15,Northwind Inc,110,Boston\n"
    )
    gl = tmp_path / "gl.csv"
    gl.write_text("period,amount\n2026-01,110\n")
    payments = tmp_path / "payments.json"
    payments.write_text(json.dumps({"payments": [{"invoice_number": "1001", "amount": 110}]}))
    commands = [
        [
            "consensus.py",
            *map(str, engines),
            "--out",
            str(consensus_out),
            "--exceptions",
            str(exceptions),
            "--quiet",
        ],
        ["arithmetic_check.py", str(consensus_out), "--out", str(proofed), "--quiet"],
        [
            "entity_resolve.py",
            str(proofed),
            "--out",
            str(parties),
            "--log",
            str(merge_log),
            "--quiet",
        ],
        [
            "attribution.py",
            str(proofed),
            "--reference",
            str(reference),
            "--out",
            str(attributed),
            "--register",
            str(register),
            "--quiet",
        ],
        [
            "completeness.py",
            str(attributed),
            "--gl",
            str(gl),
            "--payments",
            str(payments),
            "--out",
            str(completeness_out),
            "--quiet",
        ],
        [
            "sampling.py",
            str(attributed),
            "--materiality",
            "1000",
            "--out",
            str(sampling_out),
            "--quiet",
        ],
    ]
    for command in commands:
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / command[0]), *command[1:]],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
    assert json.loads(proofed.read_text())["summary"]["proved"] == 1
    assert json.loads(parties.read_text())["summary"]["party_mentions"] >= 1
    assert json.loads(attributed.read_text())["summary"]["gate_status"] == "clear"
    assert json.loads(completeness_out.read_text())["gate_status"] == "clear"
    assert json.loads(sampling_out.read_text())["population"]["eligible_documents"] == 1


def commission_document(lines, document_type="commission_statement", document_id="doc-c"):
    """A commission record shaped the way an engine handoff carries one."""
    return {
        "document_id": document_id,
        "document_type": document_type,
        "lines": [
            {
                "line_number": index + 1,
                **{
                    name: {"value": value, "source": "printed"}
                    for name, value in row.items()
                    if value is not None
                },
            }
            for index, row in enumerate(lines)
        ],
    }


def commission_line(base, rate, commission):
    return {
        "commissionable_amount": base,
        "stated_commission_rate": rate,
        "commission_amount": commission,
    }


def test_a_commission_line_proves_itself_without_a_second_engine():
    """commissionable x rate = commission is an answer key the page carries."""
    record = commission_document(
        [
            commission_line("4,712.40", "10.00", "471.24"),
            commission_line("$7,218.00", "10.00", "721.80"),
        ]
    )
    result = arithmetic_check.check_document(record, 0.01)
    assert result["arithmetic_status"] == "proved"
    assert result["arithmetic_scope"] == "commission_line_extension"
    assert result["rate_unit"] == "percent"
    assert result["checks_run"] == 2 and result["checks_failed"] == 0


def test_the_rate_unit_comes_from_arithmetic_not_from_magnitude():
    """0.75 means 75% on some layouts and 0.75% on others; the page decides."""
    fraction = commission_document([commission_line("6,014.26", "0.75", "4,510.70")])
    result = arithmetic_check.check_document(fraction, 0.01)
    assert result["rate_unit"] == "fraction"
    assert result["arithmetic_status"] == "proved"
    percent = commission_document([commission_line("38,443.06", "0.75", "288.32")])
    assert arithmetic_check.check_document(percent, 0.01)["rate_unit"] == "percent"


def test_a_document_whose_lines_disagree_about_the_unit_is_refused():
    """Two units in one document is not something to resolve by picking one."""
    mixed = commission_document(
        [
            commission_line("6,014.26", "0.75", "4,510.70"),  # fraction
            commission_line("38,443.06", "0.75", "288.32"),  # percent
        ]
    )
    result = arithmetic_check.check_document(mixed, 0.01)
    assert result["arithmetic_status"] == "not_provable"
    assert result["rate_unit"] == "mixed"
    assert "not inferred from magnitude" in result["notes"][0]


def test_a_commission_document_with_no_complete_line_is_unprovable_not_passing():
    """An unproved document is not a passing one."""
    record = commission_document([commission_line("4,712.40", None, "471.24")])
    result = arithmetic_check.check_document(record, 0.01)
    assert result["arithmetic_status"] == "not_provable"
    assert result["rate_unit"] is None
    assert result["checks_run"] == 0


def test_an_authorization_counts_unprovable_as_not_applicable_and_keeps_the_prior():
    """The relabel is the operator's, made by name, and reads back to its signature."""
    unprovable = arithmetic_check.check_document(
        commission_document([commission_line("4,712.40", None, "471.24")]), 0.01
    )
    failed = arithmetic_check.check_document(
        commission_document(
            [
                commission_line("4,712.40", "10.00", "471.24"),
                commission_line("1,116.59", "10.00", "1,116.59"),
            ]
        ),
        0.01,
    )
    assert arithmetic_check.relabel_not_provable([unprovable, failed], "amendment-55") == 1
    assert unprovable["arithmetic_status"] == "not_applicable"
    assert unprovable["prior_arithmetic_status"] == "not_provable"
    assert unprovable["arithmetic_status_authorization"] == "amendment-55"
    # A failure is a finding about the document, never a policy question.
    assert failed["arithmetic_status"] == "failed"
    assert "prior_arithmetic_status" not in failed


def test_the_arithmetic_cli_relabels_only_under_a_named_authorization(tmp_path, monkeypatch):
    import json
    import sys

    source = tmp_path / "records.json"
    source.write_text(
        json.dumps([commission_document([commission_line("4,712.40", None, "471.24")])])
    )
    out = tmp_path / "out.json"
    argv = ["arithmetic_check.py", str(source), "--out", str(out), "--quiet"]
    monkeypatch.setattr(sys, "argv", [*argv, "--not-provable-as-not-applicable", "amendment-55"])
    arithmetic_check.main()
    summary = json.loads(out.read_text())["summary"]
    assert (summary["not_provable"], summary["not_applicable"]) == (0, 1)
    assert summary["not_provable_relabelled"] == 1
    assert summary["not_provable_relabel_authorization"] == "amendment-55"
    assert any("under amendment-55" in finding for finding in summary["findings"])

    # Without it the document stays unprovable.
    plain = tmp_path / "plain.json"
    monkeypatch.setattr(sys, "argv", ["arithmetic_check.py", str(source), "--out", str(plain)])
    arithmetic_check.main()
    unchanged = json.loads(plain.read_text())["summary"]
    assert (unchanged["not_provable"], unchanged["not_provable_relabelled"]) == (1, 0)
    assert unchanged["not_provable_relabel_authorization"] is None

    # An authorization with no name authorizes nothing.
    monkeypatch.setattr(sys, "argv", [*argv, "--not-provable-as-not-applicable", " "])
    with pytest.raises(SystemExit, match="authorization's name"):
        arithmetic_check.main()


def test_a_commission_line_that_does_not_reconcile_is_a_failure():
    record = commission_document(
        [
            commission_line("4,712.40", "10.00", "471.24"),
            # A ten-times error, the shape these actually take.
            commission_line("1,116.59", "10.00", "1,116.59"),
        ]
    )
    result = arithmetic_check.check_document(record, 0.01)
    assert result["arithmetic_status"] == "failed"
    assert result["checks_failed"] == 1
    failed = [c for c in result["checks"] if c["status"] == "fail"][0]
    assert failed["expected"] == 111.66 and failed["stated"] == 1116.59


def test_a_line_missing_a_term_is_not_applicable_rather_than_passing():
    record = commission_document(
        [commission_line("4,712.40", "10.00", "471.24"), commission_line("100.00", None, None)]
    )
    result = arithmetic_check.check_document(record, 0.01)
    assert result["lines_missing_fields"] == 1
    statuses = [c["status"] for c in result["checks"]]
    assert statuses == ["pass", "not_applicable"]


def test_an_accepted_classification_outranks_an_unknown_intake_guess(tmp_path):
    """An engine handoff says 'unknown' for a document the control has classified."""
    record = commission_document(
        [commission_line("4,712.40", "10.00", "471.24")], document_type="unknown"
    )
    # Without the accepted classification, the commission proof never runs.
    assert arithmetic_check.check_document(record, 0.01)["arithmetic_status"] != "proved"
    path = tmp_path / "classification.json"
    path.write_text(
        json.dumps(
            {"accepted": [{"document_id": "doc-c", "document_type": "commission_statement"}]}
        )
    )
    accepted = arithmetic_check.accepted_classifications(path)
    assert accepted == {"doc-c": "commission_statement"}
    result = arithmetic_check.check_document(record, 0.01, classifications=accepted)
    assert result["arithmetic_status"] == "proved"


def test_a_malformed_classification_artifact_is_refused(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"documents": []}))
    with pytest.raises(ValueError, match="accepted list"):
        arithmetic_check.accepted_classifications(path)


def test_a_commission_line_number_is_read_however_it_is_wrapped():
    """Line numbers arrive bare from an engine and wrapped from consensus."""
    record = {
        "document_id": "doc-w",
        "document_type": "commission_statement",
        "lines": [
            # Wrapped, as consensus writes it.
            {
                "line_number": {"value": 7},
                **commission_line({"value": "100.00"}, {"value": "10.00"}, {"value": "10.00"}),
            },
            # Unreadable, so the position stands in rather than failing the line.
            {
                "line_number": "not-a-number",
                **commission_line({"value": "200.00"}, {"value": "10.00"}, {"value": "20.00"}),
            },
        ],
    }
    result = arithmetic_check.check_document(record, 0.01)
    assert result["arithmetic_status"] == "proved"
    assert [c["line"] for c in result["checks"]] == [7, 2]


def test_a_row_ordinal_is_derived_rather_than_put_to_a_vote():
    """4,086 blocking findings on one run, none of which protected a value.

    `line_number` is defined as the row's position counting from 1. Nothing
    reads it off the page, so vendor disagreement about it is noise -- and the
    schema records an engine that filled it with the row's invoice number, which
    is what most of that "disagreement" actually was.
    """
    left, right = invoice(), invoice()
    # One engine answers the question; the other answers a different one.
    left["lines"][0]["line_number"] = 1
    right["lines"][0]["line_number"] = 4471
    result, exceptions = consensus.merge_document("INV-001", {"a": left, "b": right})

    assert result["lines"][0]["line_number"]["value"] == 1
    ordinal = result["fields"]["lines[0].line_number"]
    assert ordinal["accepted"] and not ordinal["queue_for_review"] and not ordinal["blocking"]
    # It is never labelled consensus: no vendor agreed to anything here.
    assert ordinal["consensus_flag"] == "derived_ordinal"
    assert ordinal["rule"] == "derived_row_position"
    assert ordinal["agreeing_engines"] == []
    # Nothing is discarded -- both readings stay as provenance.
    assert ordinal["engine_readings"] == {"a": 1, "b": 4471}
    assert not [item for item in exceptions if item["field"].endswith("line_number")]

    # The ordinal is the row's own position, not the first row's.
    assert consensus.derive_ordinal("lines[7].line_number", [])["value"] == 8
    # A field of the same name outside a row is not a positional ordinal.
    assert not consensus.derived_ordinal_path("header.line_number")
    assert not consensus.derived_ordinal_path("lines[0].quantity")
    assert consensus.derived_ordinal_path("lines[0].line_number")

    # Every other cell still goes through consensus untouched.
    assert result["fields"]["lines[0].quantity"]["consensus_flag"] == "consensus_2of2"


def test_two_readings_of_the_same_prose_are_not_a_disagreement_about_a_separator():
    """303 blocking findings on one run were a comma, a semicolon, or a space.

    The engines had read the same glyphs. Punctuation is not a fact about the
    document, so for printed prose it is not a disagreement -- but for an
    identifier it is, because a hyphen inside a reference code can be
    load-bearing and `001` against `1` is a real difference.
    """
    assert consensus.values_agree("Northgate & Co", "Northgate Co", "header.dealer_name")
    assert consensus.values_agree("murb rook", "murbrook", "header.brand_name")
    assert consensus.values_agree("Flint X; -70%", "Flint X -70%", "lines[0].description")
    # Different words are still different words.
    assert not consensus.values_agree("Philips", "Phillips", "header.brand_name")

    # Identifiers are held to the strict comparison.
    assert not consensus.values_agree("ACK-1001", "ACK 1001", "header.ack_number")
    assert not consensus.values_agree("001", "1", "header.invoice_number")
    # And numbers never reach the text path at all.
    assert not consensus.values_agree(100.0, 101.0, "header.total_amount")
    assert consensus.values_agree("1,234.50", "1234.50", "header.total_amount")

    left, right = invoice(), invoice()
    left["header"]["seller_name"] = "ACME Logistics, LLC"
    right["header"]["seller_name"] = "ACME Logistics LLC"
    result, exceptions = consensus.merge_document("INV-001", {"a": left, "b": right})
    assert result["fields"]["header.seller_name"]["accepted"]
    assert not [item for item in exceptions if item["field"] == "header.seller_name"]
