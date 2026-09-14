"""Tests for precision-first handwriting, validation, and client-review controls."""

import importlib
import json
import sys
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

final_review_queue = importlib.import_module("client_review.queue")
handwriting_review = importlib.import_module("handwriting_review")
validate_extraction = importlib.import_module("validate_extraction")
extraction_schema = importlib.import_module("extraction_schema")
address_normalize = importlib.import_module("address_normalize")


def write_json(path, value):
    path.write_text(json.dumps(value))
    return path


def invoke(monkeypatch, module, *args):
    monkeypatch.setattr(sys, "argv", [module.__file__, *map(str, args)])
    module.main()


def annotation(value, semantic_type="quantity_correction", **extra):
    return {
        "document_id": "doc-1",
        "page_id": "page-1",
        "region_id": "region-1",
        "semantic_type": semantic_type,
        "content_class": "numeric",
        "value": value,
        **extra,
    }


def test_handwriting_helpers_and_decision_paths(tmp_path):
    htr = handwriting_review
    assert htr.normalize(None, False) is None
    assert htr.normalize("  ", False) is None
    assert htr.normalize("$1,200", True) == "1200.0000"
    assert htr.normalize("bad", True) is None
    assert htr.normalize(" A   Note ", False) == "a note"
    assert htr.region_key(annotation("1")) == ("doc-1", "page-1", "region-1")
    with pytest.raises(ValueError, match="needs"):
        htr.region_key({})
    assert htr.annotation_kind(annotation("1")) == ("quantity_correction", True)
    list_path = write_json(tmp_path / "a.json", [annotation("1")])
    object_path = write_json(
        tmp_path / "b.json",
        {
            "engine": "vendor-b",
            "independence_group": "vendor-b-provider",
            "annotations": [annotation("1")],
        },
    )
    assert htr.load_run(list_path)[0] == "a"
    assert htr.load_run(object_path)[:2] == ("vendor-b", "vendor-b-provider")
    with pytest.raises(ValueError, match="annotation list"):
        htr.load_run(write_json(tmp_path / "bad.json", {}))
    with pytest.raises(ValueError, match="engine name"):
        htr.load_run(write_json(tmp_path / "empty-engine.json", {"engine": " ", "annotations": []}))
    with pytest.raises(ValueError, match="independence group"):
        htr.load_run(
            write_json(
                tmp_path / "empty-group.json",
                {"engine": "valid", "independence_group": " ", "annotations": []},
            )
        )
    extraction_path = write_json(
        tmp_path / "openai-extraction.json",
        [
            {
                "engine": "openai/model-a",
                "document_id": "doc-1",
                "page_id": "page-1",
                "page_sha256": "sha",
                "raw_response": "raw/one.json",
                "handwriting_regions": [
                    {
                        "region_id": "region-1",
                        "left": 0,
                        "top": 0,
                        "right": 1,
                        "bottom": 1,
                    }
                ],
                "handwriting_readings": [
                    {
                        "region_id": "region-1",
                        "semantic_type": "damage_note",
                        "content_class": "text",
                        "value": "received",
                    }
                ],
            }
        ],
    )
    engine, group, extracted_annotations = htr.load_run(extraction_path)
    assert (engine, group) == ("openai/model-a", "openai")
    assert extracted_annotations[0]["document_id"] == "doc-1"
    assert extracted_annotations[0]["box"]["right"] == 1
    assert htr.provider_group("google_genai_vertex/model") == "google"
    # A router is not a vendor: a routed engine resolves to the model vendor
    # behind it, so an OpenRouter lane serving an OpenAI model cannot corroborate
    # a direct OpenAI lane. An unresolvable slug fails closed to one shared group.
    assert htr.provider_group("openrouter/openai/gpt-5.6") == "openai"
    assert htr.provider_group("openrouter/nvidia/nemotron-3") == "nvidia"
    assert htr.provider_group("openrouter/model") == "openrouter:unresolved"
    mixed = json.loads(extraction_path.read_text()) + json.loads(extraction_path.read_text())
    mixed[1]["engine"] = "other/model"
    with pytest.raises(ValueError, match="one engine"):
        htr.load_run(write_json(tmp_path / "mixed.json", mixed))
    invalid_readings = json.loads(extraction_path.read_text())
    invalid_readings[0]["handwriting_readings"] = {}
    with pytest.raises(ValueError, match="readings must be a list"):
        htr.load_run(write_json(tmp_path / "invalid-readings.json", invalid_readings))
    invalid_readings[0]["handwriting_readings"] = ["bad"]
    with pytest.raises(ValueError, match="reading must be an object"):
        htr.load_run(write_json(tmp_path / "invalid-reading.json", invalid_readings))
    invalid_record = json.loads(extraction_path.read_text()) + ["bad"]
    with pytest.raises(ValueError, match="non-object record"):
        htr.load_run(write_json(tmp_path / "invalid-record.json", invalid_record))
    missing_engine = json.loads(extraction_path.read_text())
    missing_engine[0].pop("engine")
    with pytest.raises(ValueError, match="engine name"):
        htr.load_run(write_json(tmp_path / "missing-engine.json", missing_engine))
    invalid_regions = json.loads(extraction_path.read_text())
    invalid_regions[0]["handwriting_regions"] = {}
    with pytest.raises(ValueError, match="regions must be a list"):
        htr.load_run(write_json(tmp_path / "invalid-regions.json", invalid_regions))
    invalid_regions[0]["handwriting_regions"] = ["bad"]
    with pytest.raises(ValueError, match="region must be an object"):
        htr.load_run(write_json(tmp_path / "invalid-region.json", invalid_regions))
    missing_region_id = json.loads(extraction_path.read_text())
    missing_region_id[0]["handwriting_regions"][0].pop("region_id")
    with pytest.raises(ValueError, match="requires region_id"):
        htr.load_run(write_json(tmp_path / "missing-region-id.json", missing_region_id))
    duplicate_region = json.loads(extraction_path.read_text())
    duplicate_region[0]["handwriting_regions"] *= 2
    with pytest.raises(ValueError, match="duplicate region_id"):
        htr.load_run(write_json(tmp_path / "duplicate-region.json", duplicate_region))
    unmatched_reading = json.loads(extraction_path.read_text())
    unmatched_reading[0]["handwriting_readings"][0]["region_id"] = "foreign-region"
    with pytest.raises(ValueError, match="no matching region"):
        htr.load_run(write_json(tmp_path / "unmatched-reading.json", unmatched_reading))
    key = ("doc-1", "page-1", "region-1")
    _, missing_kind = htr.decide(key, {"a": {**annotation("1"), "semantic_type": ""}}, 2)
    assert missing_kind["reason"] == "missing_or_conflicting_semantic_type"
    _, conflicting_kind = htr.decide(key, {"a": annotation("1"), "b": annotation("1", "amount")}, 2)
    assert conflicting_kind["reason"] == "missing_or_conflicting_semantic_type"
    _, signature = htr.decide(
        key, {"a": annotation("x", "signature"), "b": annotation("x", "signature")}, 2
    )
    assert signature["reason"] == "signature_never_transcribed_as_data"
    _, limited = htr.decide(key, {"a": annotation("1", iteration=3)}, 2)
    assert limited["reason"] == "iteration_limit_exceeded"
    _, invalid = htr.decide(key, {"a": annotation("bad")}, 2)
    assert invalid["reason"] == "missing_or_invalid_reading"
    readings = {name: annotation("38", financial_amendment=True) for name in ("a", "b", "c")}
    financial, no_exception = htr.decide(key, readings, 2)
    assert no_exception is None and financial["client_review_required"]
    _, disagreement = htr.decide(
        key, {"a": annotation("38"), "b": annotation("39"), "c": annotation("38")}, 2
    )
    assert disagreement["reason"] == "insufficient_independent_agreement"
    text_readings = {
        name: {**annotation("received", "damage_note"), "content_class": "text"}
        for name in ("a", "b")
    }
    text, no_exception = htr.decide(key, text_readings, 2)
    assert no_exception is None and text["disposition"] == "accepted_reading"


def test_handwriting_group_reconcile_and_cli(monkeypatch, tmp_path, capsys):
    htr = handwriting_review
    first = write_json(
        tmp_path / "a.json", {"engine": "a", "annotations": [annotation("38"), annotation("38")]}
    )
    second = write_json(tmp_path / "b.json", {"engine": "b", "annotations": [annotation("38")]})
    third = write_json(tmp_path / "c.json", {"engine": "c", "annotations": [annotation("38")]})
    grouped, duplicate = htr.group_runs([first, second, third])
    assert len(grouped) == 1 and duplicate[0]["reason"] == "duplicate_engine_region"
    same_provider = write_json(
        tmp_path / "same-provider.json",
        {
            "engine": "different-model",
            "independence_group": "a",
            "annotations": [annotation("38")],
        },
    )
    _, provider_duplicate = htr.group_runs([first, same_provider])
    assert any(
        item["reason"] == "duplicate_independence_group_region" for item in provider_duplicate
    )
    accepted, amendments, exceptions = htr.reconcile([first, second, third], 2)
    assert not accepted and len(amendments) == 1 and exceptions
    uncertain = write_json(
        tmp_path / "uncertain.json",
        {"engine": "uncertain", "annotations": [{**annotation("bad"), "region_id": "region-2"}]},
    )
    _, _, uncertain_exceptions = htr.reconcile([uncertain], 2)
    assert uncertain_exceptions[0]["reason"] == "missing_or_invalid_reading"
    text_a = write_json(
        tmp_path / "text-a.json",
        {
            "engine": "text-a",
            "annotations": [
                {
                    **annotation("damage", "damage_note"),
                    "content_class": "text",
                    "region_id": "region-3",
                }
            ],
        },
    )
    text_b = write_json(
        tmp_path / "text-b.json",
        {
            "engine": "text-b",
            "annotations": [
                {
                    **annotation("damage", "damage_note"),
                    "content_class": "text",
                    "region_id": "region-3",
                }
            ],
        },
    )
    text_c = write_json(
        tmp_path / "text-c.json",
        {
            "engine": "text-c",
            "annotations": [
                {
                    **annotation("wet", "damage_note"),
                    "content_class": "text",
                    "region_id": "region-3",
                }
            ],
        },
    )
    text_accepted, _, text_exceptions = htr.reconcile([text_a, text_b, text_c], 2)
    assert text_accepted and text_exceptions[0]["reason"] == "partial_text_disagreement"
    unanimous_text, _, unanimous_exceptions = htr.reconcile([text_a, text_b], 2)
    assert unanimous_text and not unanimous_exceptions
    non_object = write_json(tmp_path / "non-object.json", ["bad"])
    with pytest.raises(ValueError, match="non-object"):
        htr.group_runs([non_object])
    out, exc = tmp_path / "decision.json", tmp_path / "exceptions.json"
    invoke(monkeypatch, htr, first, second, third, "--out", out, "--exceptions", exc)
    assert json.loads(out.read_text())["summary"]["amendment_proposals"] == 1
    assert "Client review items:" in capsys.readouterr().out
    quiet_out, quiet_exc = tmp_path / "quiet.json", tmp_path / "quiet-exc.json"
    invoke(
        monkeypatch,
        htr,
        first,
        second,
        third,
        "--out",
        quiet_out,
        "--exceptions",
        quiet_exc,
        "--quiet",
    )
    assert capsys.readouterr().out == ""
    with pytest.raises(SystemExit, match="Handwriting reconciliation failed"):
        invoke(monkeypatch, htr, tmp_path / "missing.json", "--out", out, "--exceptions", exc)


def record(**header):
    return {
        "document_id": "doc-1",
        "header": header,
        "lines": [
            {
                "quantity": {"value": "2", "source": "printed"},
                "unit_price": {"value": "5", "source": "printed"},
            }
        ],
    }


def test_validation_helpers_records_and_cli(monkeypatch, tmp_path, capsys):
    validator = validate_extraction
    assert validator.scalar({"value": 1}) == 1 and validator.scalar(2) == 2
    assert validator.numeric("(5)") == -5 and validator.numeric(True) is None
    assert validator.numeric(2) == 2 and validator.numeric(2.5) == 2.5
    assert validator.numeric(None) is None and validator.numeric("bad") is None
    assert validator.date_value("2026-01-01").isoformat() == "2026-01-01"
    assert validator.date_value("01/01/2026") is None and validator.date_value(1) is None
    paths = dict(validator.flattened_values(record(invoice_date="2026-01-01")))
    assert paths["header.invoice_date"] == "2026-01-01" and "lines[0].quantity" in paths
    ignored = dict(
        validator.flattened_values(
            {
                "fields": {"ignored": 1},
                "amendments": [],
                "handwriting_regions": [],
                "list": ["skip"],
            }
        )
    )
    assert not ignored
    good = record(
        invoice_number={"value": "INV-10", "source": "printed"},
        currency="USD",
        invoice_date="2026-01-01",
        due_date="2026-01-15",
        total_amount={"value": 10, "source": "printed"},
    )
    assert not validator.validate_record(good, {"USD"})
    bad = record(
        invoice_number="X",
        currency="ZZZ",
        invoice_date="2026-01-02",
        due_date="2025-01-01",
        ship_date="2026-01-02",
        delivery_date="2026-01-01",
        total_amount="bad",
        quantity=-1,
        subtotal={"value": 1},
    )
    reasons = {issue["reason"] for issue in validator.validate_record(bad, {"USD"})}
    invalid_date = record(payment_date="01/01/2026")
    reasons |= {issue["reason"] for issue in validator.validate_record(invalid_date, {"USD"})}
    assert {
        "implausible_identifier_format",
        "unsupported_currency",
        "invalid_or_ambiguous_iso_date",
        "due_date_before_invoice_date",
        "delivery_date_before_ship_date",
        "invalid_numeric_value",
        "negative_value_requires_review",
        "missing_field_provenance",
    } <= reasons
    input_path = write_json(tmp_path / "records.json", {"documents": [good, bad]})
    assert validator.records_from(input_path) == [good, bad]
    assert validator.records_from(write_json(tmp_path / "single.json", good)) == [good]
    assert validator.records_from(write_json(tmp_path / "list.json", [good])) == [good]
    with pytest.raises(ValueError, match="Input must"):
        validator.records_from(write_json(tmp_path / "wrong.json", {}))
    with pytest.raises(ValueError, match="Input must"):
        validator.records_from(write_json(tmp_path / "scalar.json", "bad"))
    with pytest.raises(ValueError, match="object"):
        validator.validate(["bad"], {"USD"})
    validated, exceptions = validator.validate([good, bad], {"USD"})
    assert validated[0]["field_validation_status"] == "clear" and exceptions
    out, exc = tmp_path / "validated.json", tmp_path / "validation-exceptions.json"
    invoke(monkeypatch, validator, input_path, "--out", out, "--exceptions", exc)
    assert json.loads(out.read_text())["summary"]["documents"] == 2
    assert "Validated documents:" in capsys.readouterr().out
    quiet_out, quiet_exc = tmp_path / "quiet-validated.json", tmp_path / "quiet-validation.json"
    invoke(
        monkeypatch, validator, input_path, "--out", quiet_out, "--exceptions", quiet_exc, "--quiet"
    )
    assert capsys.readouterr().out == ""
    with pytest.raises(SystemExit, match="At least one"):
        invoke(
            monkeypatch,
            validator,
            input_path,
            "--out",
            out,
            "--exceptions",
            exc,
            "--currencies",
            ",",
        )
    with pytest.raises(SystemExit, match="Field validation failed"):
        invoke(monkeypatch, validator, tmp_path / "missing.json", "--out", out, "--exceptions", exc)
    with pytest.raises(SystemExit, match="Field validation failed"):
        invoke(
            monkeypatch,
            validator,
            write_json(tmp_path / "cli-bad.json", {}),
            "--out",
            out,
            "--exceptions",
            exc,
        )


def test_final_review_queue_helpers_and_cli(monkeypatch, tmp_path, capsys):
    queue = final_review_queue
    artifact = {
        "exceptions": [
            {"document_id": "d1", "field": "total", "reason": "arithmetic_failed"},
            {"document_id": "d1", "field": "date", "cause": "partial_disagreement"},
        ],
        "reassembly_exceptions": [{"page_id": "p1", "reason": "reassembly_conflict"}],
        "duplicate_candidates": [{"page_id": "p2", "reason": "duplicate"}],
        "amendments": [{"document_id": "d1", "reason": "handwriting_total"}],
        "amendment_proposals": [{"document_id": "d2"}],
        "register": [{"document_id": "d5", "reason": "unattributed"}],
        "pending_adjudication": [{"document_id": "d6", "reason": "party_ambiguous"}],
        "gate_status": "blocked",
        "documents": [
            {"document_id": "d3", "review_status": "open_exception", "arithmetic_status": "failed"},
            {"document_id": "d4", "review_status": "auto_accepted", "arithmetic_status": "proved"},
        ],
    }
    path = write_json(tmp_path / "artifact.json", artifact)
    assert queue.load(path) == artifact
    with pytest.raises(ValueError, match="must be an object"):
        queue.load(write_json(tmp_path / "list.json", []))
    assert queue.review_key({}) == ("", "", "", "", "")
    assert queue.priority({"reason": "jbig2_hazard"}) == "critical"
    assert queue.priority({"reason": "financial_refinement_source_value_differs"}) == "critical"
    assert queue.priority({"reason": "currency_invalid"}) == "high"
    assert queue.priority({"reason": "other"}) == "normal"
    review_items = queue.review_items(artifact)
    assert len(review_items) == 11
    assert next(item for item in review_items if item["field"] == "date")["reason"] == (
        "partial_disagreement"
    )
    fallback_items = queue.review_items(
        {
            "exceptions": [
                {"field": "type", "flag": "no_consensus"},
                {"field": "total", "rule": "handwritten_numeric_3of3"},
                {"field": "page"},
            ]
        }
    )
    assert [item["reason"] for item in fallback_items] == [
        "exceptions_no_consensus",
        "exceptions_handwritten_numeric_3of3",
        "exceptions_review_required",
    ]
    sparse = {
        "exceptions": ["skip"],
        "amendments": ["skip"],
        "documents": ["skip", {"document_id": "clear", "review_status": "clear"}],
    }
    with pytest.raises(ValueError, match="entries must be objects"):
        queue.review_items(sparse)
    for malformed in (
        {"exceptions": {}},
        {"amendments": {}},
        {"amendments": ["skip"]},
        {"documents": ["skip"]},
    ):
        with pytest.raises(ValueError):
            queue.review_items(malformed)
    assert (
        queue.review_items(
            {
                "artifact_type": "table_comprehension_quality_summary",
                "exceptions": [{"document_id": "not-a-client-task"}],
            }
        )
        == []
    )
    items, sources, _ = queue.consolidate([path, path])
    assert len(items) == 11 and len(sources) == 2 and items[0]["priority"] == "critical"
    cards = queue.review_cards(items)
    assert len(cards) == 8 and cards[0]["priority"] == "critical"
    assert sum(card["item_count"] for card in cards) == len(items)
    assert sources == ["artifact.json", "artifact.json"]
    assert all(set(queue.REVIEW_FIELDS) <= set(item) for item in items)
    # Every item is citable, and no two items share a citation.
    ids = [item["review_item_id"] for item in items]
    assert all(value.startswith("review-item-") for value in ids)
    assert len(set(ids)) == len(items)
    out = tmp_path / "final-review.json"
    invoke(monkeypatch, queue, path, "--out", out)
    written = json.loads(out.read_text())
    assert written["summary"]["gate_status"] == "blocked_pending_client_review"
    assert written["summary"]["schema_version"] == "1.0"
    assert written["summary"]["client_review_cards"] == 8
    assert "Final client-review items:" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="Final review queue failed"):
        invoke(monkeypatch, queue, path, "--out", out)
    invoke(monkeypatch, queue, path, "--out", tmp_path / "quiet.json", "--quiet")
    assert capsys.readouterr().out == ""
    with pytest.raises(SystemExit, match="Final review queue failed"):
        invoke(monkeypatch, queue, tmp_path / "missing.json", "--out", out)


def test_address_normalization_helpers_records_and_cli(monkeypatch, tmp_path, capsys):
    normalizer = address_normalize
    assert normalizer.scalar({"value": "x"}) == "x" and normalizer.scalar("x") == "x"
    assert normalizer.field("x") == {"value": "x", "confidence": None, "source": "system"}
    assert normalizer.address_role("buyer_address") == "buyer"
    assert normalizer.address_parts(" 1 Main,\n Boston ") == ["1 Main", "Boston"]
    assert normalizer.country_from([]) is None
    parts = ["Toronto", "Canada"]
    assert normalizer.country_from(parts) == "CA" and parts == ["Toronto"]
    assert normalizer.country_from(["Mars"]) is None
    assert normalizer.postal_from("Boston MA 02110") == ("02110", "US", 10)
    assert normalizer.postal_from("Toronto ON M5V 3A8") == ("M5V3A8", "CA", 11)
    assert normalizer.postal_from("London SW1A 1AA") == ("SW1A1AA", "GB", 7)
    assert normalizer.postal_from("No postal") == (None, None, None)
    assert normalizer.postal_from("Singapore 569059", "SG") == ("569059", "SG", 10)
    assert normalizer.postal_from("Shanghai 201202", "CN") == ("201202", "CN", 9)
    assert normalizer.postal_from("Ayutthaya 13160", "TH") == ("13160", "TH", 10)
    parsed, reasons = normalizer.parse_address(
        "101 Market Street, Suite 400, San Francisco, CA 94105, USA"
    )
    assert not reasons and parsed == {
        "address_line1": "101 Market Street",
        "address_line2": "Suite 400",
        "city": "San Francisco",
        "state_or_region": "CA",
        "postal_code": "94105",
        "country_code": "US",
    }
    assert normalizer.parse_address("1 Main St\nBoston MA 02110")[0]["city"] == "Boston"
    assert normalizer.parse_address("PO Box 9, London, SW1A 1AA, United Kingdom")[0] == {
        "address_line1": "PO Box 9",
        "address_line2": None,
        "city": "London",
        "state_or_region": None,
        "postal_code": "SW1A1AA",
        "country_code": "GB",
    }
    assert normalizer.parse_address("10 Ang Mo Kio Street 65, Singapore 569059, Singapore")[0] == {
        "address_line1": "10 Ang Mo Kio Street 65",
        "address_line2": None,
        "city": "Singapore",
        "state_or_region": None,
        "postal_code": "569059",
        "country_code": "SG",
    }
    assert normalizer.parse_address("330 Zheng Ding Road, Shanghai 201202, China")[0] == {
        "address_line1": "330 Zheng Ding Road",
        "address_line2": None,
        "city": "Shanghai",
        "state_or_region": None,
        "postal_code": "201202",
        "country_code": "CN",
    }
    assert normalizer.parse_address("94 Moo 1, Ayutthaya 13160, Thailand")[0] == {
        "address_line1": "94 Moo 1",
        "address_line2": None,
        "city": "Ayutthaya",
        "state_or_region": None,
        "postal_code": "13160",
        "country_code": "TH",
    }
    assert normalizer.parse_address("US")[1] == ["address_parse_incomplete"]
    assert normalizer.parse_address("One Street, City")[0]["city"] == "City"
    assert normalizer.parse_address("02110")[1] == ["address_parse_incomplete"]
    assert normalizer.parse_address("Boston")[0]["address_line1"] == "Boston"
    assert normalizer.issue("d", "buyer_address", "bad", "x")["field"] == "header.buyer_address"
    source = {
        "document_id": "doc-1",
        "header": {
            "buyer_address": {
                "value": "101 Market Street, Suite 400, San Francisco, CA 94105, USA",
                "confidence": 0.9,
                "source": "printed",
            }
        },
    }
    normalized, findings = normalizer.normalize_record(source)
    assert (
        not findings and normalized["header"]["buyer_address"] == source["header"]["buyer_address"]
    )
    assert normalized["header"]["buyer_city"] == normalizer.field("San Francisco")
    assert normalized["address_normalizations"][0]["external_validation_status"] == "not_requested"
    conflict_source = {
        "document_id": "doc-2",
        "buyer_address": "2 Side Road, Chicago, IL 60601",
        "buyer_city": "Elsewhere",
    }
    conflicted, conflicts = normalizer.normalize_record(conflict_source)
    assert conflicted["address_validation_status"] == "client_review_required"
    assert conflicts[0]["reason"] == "address_component_conflict"
    matching, matching_findings = normalizer.normalize_record(
        {
            "document_id": "doc-same",
            "buyer_address": "2 Side Road, Chicago, IL 60601",
            "buyer_city": "Chicago",
        }
    )
    assert not matching_findings and matching["header" if "header" in matching else "buyer_city"]
    incomplete, incomplete_findings = normalizer.normalize_record(
        {"document_id": "doc-incomplete", "buyer_address": "02110"}
    )
    assert incomplete_findings[0]["reason"] == "address_parse_incomplete"
    empty, empty_findings = normalizer.normalize_record({"document_id": "doc-3"})
    assert empty["address_normalizations"] == [] and not empty_findings
    with pytest.raises(ValueError, match="header"):
        normalizer.normalize_record({"document_id": "bad", "header": []})
    record_path = write_json(tmp_path / "record.json", source)
    list_path = write_json(tmp_path / "list.json", [source])
    docs_path = write_json(tmp_path / "docs.json", {"documents": [source]})
    assert normalizer.records_from(record_path) == [source]
    assert normalizer.records_from(list_path) == [source]
    assert normalizer.records_from(docs_path) == [source]
    for path in (
        write_json(tmp_path / "bad.json", {}),
        write_json(tmp_path / "scalar.json", "bad"),
    ):
        with pytest.raises(ValueError, match="Input must"):
            normalizer.records_from(path)
    with pytest.raises(ValueError, match="Every record"):
        normalizer.normalize(["bad"])
    all_normalized, all_findings = normalizer.normalize([source, conflict_source])
    assert len(all_normalized) == 2 and all_findings
    out, exc = tmp_path / "address.json", tmp_path / "address-exceptions.json"
    invoke(monkeypatch, normalizer, docs_path, "--out", out, "--exceptions", exc)
    assert json.loads(out.read_text())["summary"]["addresses_normalized"] == 1
    assert "Normalized addresses:" in capsys.readouterr().out
    invoke(monkeypatch, normalizer, docs_path, "--out", out, "--exceptions", exc, "--quiet")
    assert capsys.readouterr().out == ""
    with pytest.raises(SystemExit, match="Address normalization failed"):
        invoke(
            monkeypatch, normalizer, tmp_path / "missing.json", "--out", out, "--exceptions", exc
        )


def test_google_address_validation_evidence_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_ADDRESS_VALIDATION_ENABLED", "false")
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY_ENV", "TEST_GOOGLE_MAPS_API_KEY")
    normalizer = address_normalize

    class FakeResponse:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps(self.value).encode()

    response = {
        "responseId": "google-response-1",
        "result": {
            "verdict": {
                "addressComplete": True,
                "validationGranularity": "PREMISE",
                "geocodeGranularity": "PREMISE",
            },
            "address": {
                "formattedAddress": "101 Market St, San Francisco, CA 94105, USA",
                "postalAddress": {
                    "addressLines": ["101 Market St", "Suite 400"],
                    "locality": "San Francisco",
                    "administrativeArea": "CA",
                    "postalCode": "94105",
                    "regionCode": "US",
                },
            },
            "geocode": {"location": {"latitude": 37.79, "longitude": -122.39}, "placeId": "p-1"},
            "uspsData": {},
        },
    }
    parsed = {"country_code": "US"}
    assert normalizer.google_payload("101 Market St", parsed, True)["enableUspsCass"]
    assert not normalizer.google_payload("1 King St", {"country_code": "CA"}, True)[
        "enableUspsCass"
    ]
    assert "regionCode" not in normalizer.google_payload("Unknown", {}, False)["address"]
    request = normalizer.google_request(
        normalizer.google_payload("101 Market St", parsed, True),
        "not-a-real-key",
        2,
        opener=lambda *_args, **_kwargs: FakeResponse(response),
    )
    assert request == response
    with pytest.raises(RuntimeError, match="failed"):
        normalizer.google_request(
            {}, "key", 2, opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("x"))
        )
    with pytest.raises(RuntimeError, match="result object"):
        normalizer.google_request({}, "key", 2, opener=lambda *_args, **_kwargs: FakeResponse({}))
    unsupported = HTTPError(
        "https://example.test",
        400,
        "Bad Request",
        {},
        BytesIO(
            b'{"error":{"status":"INVALID_ARGUMENT","message":"Unsupported region code: CN."}}'
        ),
    )
    with pytest.raises(RuntimeError, match="unsupported_region"):
        normalizer.google_request(
            {}, "key", 2, opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(unsupported)
        )
    denied = HTTPError(
        "https://example.test",
        403,
        "Forbidden",
        {},
        BytesIO(b'{"error":{"status":"PERMISSION_DENIED"}}'),
    )
    with pytest.raises(RuntimeError, match="permission_denied"):
        normalizer.google_request(
            {}, "key", 2, opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(denied)
        )
    rejected = HTTPError(
        "https://example.test",
        400,
        "Bad Request",
        {},
        BytesIO(b'{"error":{"status":"INVALID_ARGUMENT","message":"Malformed address."}}'),
    )
    with pytest.raises(RuntimeError, match="request_rejected"):
        normalizer.google_request(
            {}, "key", 2, opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(rejected)
        )
    assert (
        normalizer.google_error_category(
            type("BrokenError", (), {"read": lambda _self: (_ for _ in ()).throw(OSError("x"))})()
        )
        == "failed"
    )

    evidence = normalizer.google_evidence(response, "buyer")
    assert evidence["status"] == "validated"
    assert evidence["provider_derived_fields"]["buyer_geocode_latitude"] == normalizer.field(37.79)
    assert evidence["usps_data_present"]
    no_geocode = json.loads(json.dumps(response))
    no_geocode["result"]["geocode"] = {}
    assert (
        "buyer_geocode_latitude"
        not in normalizer.google_evidence(no_geocode, "buyer")["provider_derived_fields"]
    )
    review = json.loads(json.dumps(response))
    review["result"]["verdict"]["hasInferredComponents"] = True
    assert normalizer.google_evidence(review, "buyer")["status"] == "review_required"
    header = {"buyer_validated_city": "San Francisco"}
    assert not normalizer.add_provider_fields(header, evidence["provider_derived_fields"])
    conflicting = {"buyer_validated_city": "Elsewhere"}
    assert normalizer.add_provider_fields(conflicting, evidence["provider_derived_fields"]) == [
        "buyer_validated_city"
    ]

    source = {
        "document_id": "google-doc",
        "header": {
            "buyer_address": "101 Market Street, Suite 400, San Francisco, CA 94105, USA",
            "seller_address": "101 Market Street, Suite 400, San Francisco, CA 94105, USA",
        },
    }
    normalized, local_exceptions = normalizer.normalize([source])
    assert not local_exceptions
    monkeypatch.setattr(normalizer, "google_request", lambda *_args: response)
    google_exceptions, requests = normalizer.apply_google_validation(
        normalized, "not-a-real-key", 1, 2, True
    )
    assert not google_exceptions and requests == 1
    assert normalized[0]["header"]["buyer_validated_city"] == normalizer.field("San Francisco")
    assert normalized[0]["address_normalizations"][0]["external_validation_status"] == "validated"

    review_records, _ = normalizer.normalize([source])
    monkeypatch.setattr(normalizer, "google_request", lambda *_args: review)
    review_exceptions, _ = normalizer.apply_google_validation(review_records, "key", 1, 2, True)
    assert review_exceptions[0]["reason"] == "google_address_validation_review_required"
    assert review_records[0]["address_validation_status"] == "client_review_required"

    failed_records, _ = normalizer.normalize([source])
    monkeypatch.setattr(
        normalizer, "google_request", lambda *_args: (_ for _ in ()).throw(RuntimeError("down"))
    )
    failed_exceptions, failed_requests = normalizer.apply_google_validation(
        failed_records, "key", 1, 2, True
    )
    assert failed_requests == 1 and len(failed_exceptions) == 2
    assert failed_exceptions[0]["reason"] == "google_address_validation_failed"

    unsupported_records, _ = normalizer.normalize([source])
    monkeypatch.setattr(
        normalizer,
        "google_request",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("unsupported_region")),
    )
    unsupported_exceptions, _ = normalizer.apply_google_validation(
        unsupported_records, "key", 1, 2, True
    )
    assert unsupported_exceptions[0]["reason"] == "google_address_validation_unsupported_region"
    assert (
        unsupported_records[0]["address_normalizations"][0]["external_validation_status"]
        == "unsupported_region"
    )

    limited_records, _ = normalizer.normalize([source])
    limited_exceptions, limited_requests = normalizer.apply_google_validation(
        limited_records, "key", 0, 2, True
    )
    assert (
        not limited_requests
        and limited_exceptions[0]["reason"] == "google_address_validation_limit_reached"
    )

    conflicting_records, _ = normalizer.normalize(
        [{**source, "header": {**source["header"], "buyer_validated_city": "Elsewhere"}}]
    )
    monkeypatch.setattr(normalizer, "google_request", lambda *_args: response)
    conflict_exceptions, _ = normalizer.apply_google_validation(
        conflicting_records, "key", 1, 2, True
    )
    assert any(
        item["reason"] == "google_address_validation_component_conflict"
        for item in conflict_exceptions
    )
    assert conflicting_records[0]["address_validation_status"] == "client_review_required"

    input_path = write_json(tmp_path / "google-input.json", source)
    out, exc = tmp_path / "google-output.json", tmp_path / "google-exceptions.json"
    invoke(
        monkeypatch,
        normalizer,
        input_path,
        "--out",
        out,
        "--exceptions",
        exc,
        "--no-google-address-validation",
        "--quiet",
    )
    assert (
        json.loads(out.read_text())["summary"]["external_validation"]["provider"] == "not_requested"
    )
    monkeypatch.setenv("TEST_GOOGLE_MAPS_API_KEY", "not-a-real-key")
    monkeypatch.setattr(normalizer, "apply_google_validation", lambda records, *_args: ([], 1))
    invoke(
        monkeypatch,
        normalizer,
        input_path,
        "--out",
        out,
        "--exceptions",
        exc,
        "--google-address-validation",
        "--max-google-requests",
        "1",
        "--no-google-usps-cass",
        "--quiet",
    )
    summary = json.loads(out.read_text())["summary"]["external_validation"]
    assert summary["requests_sent"] == 1 and not summary["usps_cass_enabled"]
    for args, message in (
        (("--max-google-requests", "1"), "requires"),
        (("--google-address-validation", "--max-google-requests", "5001"), "between"),
        (
            (
                "--google-address-validation",
                "--max-google-requests",
                "1",
                "--google-timeout-seconds",
                "0",
            ),
            "between",
        ),
    ):
        with pytest.raises(SystemExit, match=message):
            invoke(monkeypatch, normalizer, input_path, "--out", out, "--exceptions", exc, *args)
    monkeypatch.delenv("TEST_GOOGLE_MAPS_API_KEY")
    with pytest.raises(SystemExit, match="TEST_GOOGLE_MAPS_API_KEY"):
        invoke(
            monkeypatch,
            normalizer,
            input_path,
            "--out",
            out,
            "--exceptions",
            exc,
            "--google-address-validation",
            "--max-google-requests",
            "1",
        )


def test_the_gate_creates_its_own_phase_directory(monkeypatch, tmp_path, capsys):
    """`RUN/review/` does not exist when the gate is the first to write there.

    A run directory is organised by phase, and the gate is usually what creates
    the review phase. Refusing with a bare ENOENT made the one command a clear
    result is claimed from the one an operator had to mkdir for by hand -- on a
    real run it failed with `[Errno 2] No such file or directory`.
    """
    artifact = write_json(
        tmp_path / "controls.json",
        {"exceptions": [{"document_id": "d1", "field": "total", "reason": "arithmetic_failed"}]},
    )
    out = tmp_path / "review" / "phase7" / "final_queue.json"
    argv = ["final_review_queue.py", str(artifact), "--out", str(out)]
    monkeypatch.setattr(sys, "argv", argv)
    final_review_queue.main()
    assert out.is_file()
    assert json.loads(out.read_text())["summary"]["client_review_items"] == 1
    assert "Final client-review items: 1" in capsys.readouterr().out

    # Creating the directory does not license overwriting a queue already in it:
    # a second run against the same path is still refused.
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="Final review queue failed"):
        final_review_queue.main()


def test_the_gate_survives_an_input_it_cannot_read(tmp_path, monkeypatch, capsys):
    """One bad member of a shell glob must not cost the operator the whole queue.

    The gate is handed `RUN/controls/*.json`. A real run died on
    `[Errno 21] Is a directory` -- one lane's `--out` is a directory, and the
    glob swept it in -- so the command produced no queue at all and an operator
    had nothing instead of everything-but-one-file.

    An input this cannot read is not zero findings from that input, so it becomes
    a critical blocking item naming the file. The unread artifact is then
    impossible to mistake for a clean one.
    """
    good = write_json(
        tmp_path / "controls.json",
        {"exceptions": [{"document_id": "d1", "field": "total", "reason": "arithmetic_failed"}]},
    )
    unreadable = tmp_path / "a-directory.json"
    unreadable.mkdir()
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not json")

    items, sources, _ = final_review_queue.consolidate([good, unreadable, malformed])
    # Every input is still credited as supplied, readable or not.
    assert set(sources) == {"controls.json", "a-directory.json", "malformed.json"}

    unread = [item for item in items if item["review_source"] == "final_review_queue"]
    assert {item["source_artifact"] for item in unread} == {"a-directory.json", "malformed.json"}
    assert all(item["priority"] == "critical" for item in unread)
    assert all(item["disposition"] == "client_review_required" for item in unread)
    assert all("input_unreadable" in item["reason"] for item in unread)
    # The readable artifact's own finding still reaches the queue.
    assert any(item.get("reason") == "arithmetic_failed" for item in items)

    out = tmp_path / "review" / "final_queue.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "final_review_queue.py",
            str(good),
            str(unreadable),
            "--out",
            str(out),
        ],
    )
    final_review_queue.main()
    written = json.loads(out.read_text())
    assert written["summary"]["gate_status"] == "blocked_pending_client_review"
    assert "Final client-review items: 2" in capsys.readouterr().out


def test_an_untyped_numeric_handwritten_reading_still_reaches_review(tmp_path):
    """The case this control exists for was the one case it let through.

    `financial_amendment` is copied from the supplied region, and no detector on
    the commission run set it. `semantic_type` came back `handwritten_text` for
    all 3,319 annotations, so the typed-financial branch never fired either.
    Both signals were always false, and a handwritten number beside a printed one
    was accepted as ordinary text against a recommended review threshold of $0.

    A numeric reading is not thereby an amendment -- nothing here establishes
    what a mark modifies. It is routed to review because it could be one and
    nothing has established that it is not.
    """
    htr = handwriting_review
    runs = [
        write_json(
            tmp_path / f"{group}.json",
            {
                "engine": group,
                "independence_group": group,
                "annotations": [
                    annotation("1250.00", semantic_type="handwritten_text", region_id="region-9")
                ],
            },
        )
        for group in ("alpha", "beta", "gamma")
    ]
    accepted, amendments, _ = htr.reconcile(runs, 2)
    assert not accepted
    assert len(amendments) == 1
    proposal = amendments[0]
    assert proposal["disposition"] == "amendment_proposal_requires_client_review"
    assert proposal["financial_basis"] == "untyped_numeric_reading"
    assert proposal["client_review_required"] is True


def test_a_reading_typed_as_financial_is_still_recorded_as_typed(tmp_path):
    """The explicit signal keeps its own basis, so the two are told apart."""
    htr = handwriting_review
    runs = [
        write_json(
            tmp_path / f"{group}.json",
            {"engine": group, "independence_group": group, "annotations": [annotation("38")]},
        )
        for group in ("alpha", "beta", "gamma")
    ]
    _, amendments, _ = htr.reconcile(runs, 2)
    assert amendments[0]["financial_basis"] == "typed_as_financial"


def test_handwritten_text_that_is_not_numeric_is_not_escalated_as_financial(tmp_path):
    """`comment_only` handwriting stays a comment; this is not a policy change."""
    htr = handwriting_review
    runs = [
        write_json(
            tmp_path / f"{group}.json",
            {
                "engine": group,
                "independence_group": group,
                "annotations": [
                    {
                        **annotation("approved by JM", semantic_type="handwritten_text"),
                        "content_class": "text",
                        "region_id": "region-7",
                    }
                ],
            },
        )
        for group in ("alpha", "beta", "gamma")
    ]
    accepted, amendments, _ = htr.reconcile(runs, 2)
    assert amendments == []
    assert accepted and "financial_basis" not in accepted[0]


def test_the_ocr_lane_says_whether_the_financial_flag_was_supplied_at_all():
    """An unset flag and "no financial handwriting" are different findings."""
    import google_handwriting_ocr as ocr

    assert ocr.infer_content_class("1,250.00") == "numeric"
    assert ocr.infer_content_class("approved by JM") == "text"


def test_a_queue_item_keeps_its_name_when_the_queue_is_rebuilt():
    """An item id must be the finding, not its position in the list.

    The queue had no ``review_item_id`` at all: 0 of 35,745 items carried one,
    so `review_grouping.py` gave every group an empty ``source_item_ids`` and
    `client_decision_compile.py` -- which requires a change's affected ids to be
    non-empty and a subset of its group's -- could never compile a client answer
    into a change. The whole client review lane ran, and its answers had nothing
    to bind to.

    Naming items by position would have closed that hole and opened a worse one.
    Rebuilding the queue with one extra artifact shifts every later item, so an
    already-authorized change would silently repoint at a different finding.
    """
    queue = final_review_queue
    finding = {
        "document_id": "d2",
        "page_id": "p2",
        "field": "invoice_total",
        "reason": "consensus_disagreement",
    }
    earlier = {**finding, "document_id": "d1", "page_id": "p1"}
    before = queue.review_item_id(finding)
    # An item inserted ahead of it does not rename it.
    assert queue.review_item_id(finding) == before
    assert queue.review_item_id(earlier) != before
    # A finding that actually changed gets a new name rather than inheriting an
    # approval given for the old one.
    assert queue.review_item_id({**finding, "reason": "arithmetic_mismatch"}) != before
    # Absent parts are absent, not the string "None".
    assert queue.review_item_id({}) == queue.review_item_id({"document_id": None})


def date_reasons(**header):
    """Classify one record's date cells the way the validator does."""
    return {
        issue["reason"] for issue in validate_extraction.validate_record(record(**header), {"USD"})
    }


def test_every_schema_date_field_is_validated():
    """The date field set is derived from the schema, so it cannot fall behind it."""
    schema_dates = {
        name
        for name in (*extraction_schema.HEADER_FIELDS, *extraction_schema.LINE_FIELDS)
        if name.endswith("_date") or name in {"period_start", "period_end"}
    }
    assert schema_dates <= validate_extraction.DATE_FIELDS
    # The field that held an identifier while the hand-kept list omitted it.
    assert "transaction_date" in validate_extraction.DATE_FIELDS
    assert "date_field_holds_a_non_date" in date_reasons(transaction_date="128975")


def test_a_date_the_source_never_rendered_is_its_own_finding():
    """Excel overflow, scientific notation and truncation are source defects."""
    for value in ("########", "5.21E+08", "01/21/20...", "01/21/20…"):
        assert date_reasons(document_date=value) == {"date_not_rendered_in_source"}


def test_a_date_without_a_day_is_separated_from_a_non_date():
    """A month or a year alone is a real reading that cannot name a day."""
    for value in ("Jun 2023", "December-24", "April", "10/25", "2025"):
        assert date_reasons(document_date=value) == {"date_lacks_a_day"}
    for value in ("TBD", "N/A", "Not paid yet", "P9 2021", "2023-0219", "Decemb"):
        assert date_reasons(document_date=value) == {"date_field_holds_a_non_date"}


def test_an_unambiguous_date_is_not_reported_as_ambiguous():
    """A named month, a year-first slash date and a clock time settle themselves."""
    for value in (
        "May 31, 2024",
        "2026/03/31",
        "Apr 13, 2023 at 10:14:20 AM ET",
        "Fri, Nov 4, 2022, 9:26 AM",
        "13 May 2024",
    ):
        assert not date_reasons(document_date=value), value


def test_a_document_that_proves_its_own_order_settles_its_slash_dates():
    """A part above twelve can only be a day, and it fixes the whole document."""
    assert date_reasons(document_date="12/9/22") == {"invalid_or_ambiguous_iso_date"}
    assert not date_reasons(document_date="12/9/22", ship_date="25/9/22")
    # Both orders proved at once proves neither.
    assert date_reasons(document_date="12/9/22", ship_date="25/9/22", invoice_date="9/25/22") == {
        "invalid_or_ambiguous_iso_date"
    }


def test_a_blank_date_cell_earns_nothing():
    """Whitespace is not a reading and must not become a finding."""
    assert validate_extraction.date_text("   ") is None
    assert validate_extraction.date_text(None) is None
    assert validate_extraction.date_text({"value": None}) is None
    assert not validate_extraction.date_issues("doc", "header.document_date", "   ", False)


def test_the_commission_fields_the_corpus_populates_are_defined():
    """A field the schema names but never defines is a field engines guess at.

    `stated_commission_rate` was undefined while 5,088 line rows carried it, and
    a vendor's "Commission Split" column was read into it: zero of those rows
    reconcile as a percentage.
    """
    definitions = extraction_schema.FIELD_DEFINITIONS
    for name in (
        "stated_commission_rate",
        "allocation_share",
        "extended_amount",
        "invoice_amount",
        "quantity",
        "unit_price",
        "product_sku",
        "purchase_order_number",
        "sales_order_number",
    ):
        assert name in definitions, name
        assert name in extraction_schema.LINE_FIELDS, name
    rate = definitions["stated_commission_rate"].lower()
    assert "split" in rate and "null" in rate
    assert "not the commission rate" in definitions["allocation_share"].lower()
    # The glossary is what actually reaches an engine.
    glossary = extraction_schema.field_glossary()
    assert "stated_commission_rate" in glossary and "Split" in glossary


def test_the_specifier_has_a_field_of_its_own():
    """A printed column with no home is forced into the nearest-looking field.

    145 pages print a SPECIFIER column and the schema had nowhere to put it, so
    the design firm landed in `brand_name` or was discarded entirely.
    """
    for scope in (extraction_schema.HEADER_FIELDS, extraction_schema.LINE_FIELDS):
        assert "specifier_name" in scope
    definitions = extraction_schema.FIELD_DEFINITIONS
    specifier = definitions["specifier_name"].lower()
    assert "specifier" in specifier and "not the manufacturer" in specifier
    brand = definitions["brand_name"].lower()
    assert "specifier_name" in brand, "brand_name must send a specifier elsewhere"
    assert "dealer_name" in brand and "customer_name" in brand
    # It must reach an engine, and be readable back off a line.
    assert "specifier_name" in extraction_schema.field_glossary()
    line = extraction_schema.EXTRACTION_SCHEMA["properties"]["lines"]["items"]["properties"]
    assert "specifier_name" in line
    header = extraction_schema.EXTRACTION_SCHEMA["properties"]["header"]["items"]
    assert "specifier_name" in header["properties"]["name"]["enum"]
