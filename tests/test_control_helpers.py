"""Focused tests for control decisions and error-handling branches."""

import importlib

import pytest
from PIL import Image
from test_cli_workflows import (
    arithmetic_check,
    attribution,
    completeness,
    consensus,
    entity_resolve,
    ingest_pages,
    sampling,
    scan_profile,
    write_json,
)


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, None),
        (True, None),
        ({"value": "$1,200"}, 1200.0),
        ("(4)", -4.0),
        ("bad", None),
    ],
)
def test_arithmetic_number_parser(value, expected):
    assert arithmetic_check.num(value) == expected


def test_arithmetic_helpers_cover_amendments_and_missing_lines():
    record = {
        "header": {"subtotal": {"value": 2, "source": "printed"}},
        "amendments": [
            {"field": "subtotal", "amended_value": "3", "amendment_source": "handwritten"}
        ],
        "lines": [
            {"quantity": None, "unit_price": 2, "extended_amount": 2},
            {
                "line_number": "no",
                "quantity": {"value": 1, "source": "handwritten"},
                "unit_price": 2,
                "extended_amount": 2,
                "discount": 1,
            },
        ],
        "accessorials": [{"charge_amount": 1}],
    }
    assert arithmetic_check.effective(record, "subtotal") == (3.0, "handwritten")
    lines, subtotal, missing = arithmetic_check.check_lines(record, 0.01)
    assert lines[0]["status"] == "not_applicable" and subtotal == 4 and missing == 1
    result = arithmetic_check.check_document(record, 0.01)
    assert result["arithmetic_status"] == "not_provable" or result["checks"]


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, 0.0),
        (True, 0.0),
        ({"value": "2"}, 2.0),
        ("(3)", -3.0),
        ("bad", 0.0),
    ],
)
def test_shared_numeric_parsers(value, expected):
    assert attribution.num(value) == expected
    assert completeness.num(value) == expected
    assert sampling.num(value) == expected


def test_attribution_helper_paths(tmp_path):
    csv = tmp_path / "ref.csv"
    csv.write_text(
        "ack_number,date,customer,amount,po_number,selling_location\nAB-1,01/02/2026,Acme,20,PO,NY\n"
    )
    ref = attribution.load_reference(csv, r"AB-\\d", None)
    assert attribution.parse_date("bad") is None
    assert attribution.parse_date("2026-01-02T00:00:00").isoformat() == "2026-01-02"
    assert attribution.validate_key("AB-1", ref)[0]
    assert not attribution.validate_key("ZZ", ref)[0]
    assert (
        attribution.resolve_explicit(
            {"header": {"ack_number": {"value": "AB-1", "source": "handwritten"}}}, ref
        )["rank"]
        == 2
    )
    assert attribution.resolve_po({"po_number": "PO"}, ref)["rank"] == 3
    idx = attribution.build_index(
        [{"document_id": "donor", "bol_number": "B"}, {"document_id": "child", "bol_number": "B"}]
    )
    resolved = {"donor": {"ack_number": "AB-1", "rank": 1, "evidence_chain": []}}
    assert (
        attribution.resolve_inherited({"document_id": "child", "bol_number": "B"}, resolved, idx)[
            "rank"
        ]
        == 4
    )
    assert (
        attribution.resolve_inherited(
            {"document_id": "child", "bol_number": "B"}, resolved, idx, depth=2
        )
        is None
    )
    assert (
        attribution.resolve_fuzzy(
            {"buyer_name": "Acme", "invoice_date": "2026-01-02", "total_amount": 20}, ref
        )["rank"]
        == 6
    )
    assert (
        attribution.resolve_fuzzy(
            {"buyer_name": "No", "invoice_date": "2026-01-02", "total_amount": 20}, ref
        )
        is None
    )
    assert (
        attribution.resolve_selling_location({"letterhead_city": "LA"}, ref, {})["method"]
        == "letterhead"
    )
    assert (
        attribution.resolve_selling_location({}, ref, {"ack_number": "AB-1"})["method"]
        == "reference_table_via_ack"
    )
    assert (
        attribution.resolve_selling_location({}, ref, {"ack_number": "CHI-99"})["method"]
        == "ack_prefix_inferred"
    )
    assert (
        attribution.resolve_selling_location({"salesperson": "s"}, ref, {})["method"]
        == "salesperson_needs_territory_table"
    )


def test_completeness_helper_paths(tmp_path):
    assert completeness.split_invoice_number(None) == (None, None, None)
    assert completeness.split_invoice_number("INV-0042") == ("INV-", 42, 4)
    assert completeness.split_invoice_number("ABC") == ("ABC", None, None)
    assert completeness.month_of("01/15/2026") == "2026-01"
    assert completeness.month_of("bad") is None
    assert completeness.month_range("2025-12", "2026-02") == ["2025-12", "2026-01", "2026-02"]
    docs = [
        {
            "vendor": "V",
            "invoice_number": f"I-{x:03d}",
            "month": f"2026-0{x}-01"[:7],
            "amount": 10,
            "doc_type": "invoice",
            "credits": 0,
            "explicit_status": "",
            "date": "2026-01-01",
        }
        for x in (1, 2, 4, 5, 6)
    ]
    assert completeness.sequence_gaps(docs)[0]["missing_count"] == 1
    calendar_docs = [{**docs[0], "month": f"2026-{m:02d}"} for m in (1, 2, 4, 5)]
    assert completeness.calendar_gaps(calendar_docs)[0]["missing_months"] == ["2026-03"]
    gl_csv = tmp_path / "gl.csv"
    gl_csv.write_text("period,vendor,amount\n2026-01,V,5\n")
    gl_buckets, gl_rejected = completeness.load_gl(gl_csv)
    assert gl_buckets["2026-01"]["V"] == 5
    assert gl_rejected == []
    variance = completeness.gl_variance(docs, {"2026-01": {"V": 5, "ALL": 1}}, 2)
    assert variance["periods_compared"]
    closure = completeness.aging_closure(
        docs + [{**docs[0], "invoice_number": "X", "amount": 10}],
        [{"invoice_number": "X", "amount": 5}],
    )
    assert closure["partially_applied"] == 1


def test_aging_closure_does_not_cross_apply_duplicate_invoice_numbers():
    docs = [
        {
            "vendor": vendor,
            "invoice_number": "1001",
            "currency": "USD",
            "month": "2026-01",
            "amount": 10,
            "doc_type": "invoice",
            "credits": 0,
            "explicit_status": "",
            "date": "2026-01-01",
        }
        for vendor in ("Vendor A", "Vendor B")
    ]
    ambiguous = completeness.aging_closure(docs, [{"invoice_number": "1001", "amount": 10}])
    assert ambiguous["ambiguous_payment_application_count"] == 1
    assert ambiguous["unresolved"] == 2
    matched = completeness.aging_closure(
        docs, [{"invoice_number": "1001", "vendor": "Vendor A", "currency": "USD", "amount": 10}]
    )
    assert matched["ambiguous_payment_application_count"] == 0
    assert matched["closed"] == 1 and matched["unresolved"] == 1


def test_consensus_helpers_cover_normalization_and_shapes(tmp_path):
    assert consensus.is_numeric_field("total_amount", "x")
    assert not consensus.is_numeric_field("name", "x")
    assert not consensus.is_numeric_field("account_number", 1001)
    assert consensus.normalize("") is None
    assert consensus.normalize(" ACME   INC ") == "acme inc"
    assert consensus.values_agree(None, None) and not consensus.values_agree(None, 1)
    assert consensus.values_agree(None, None, "invoice_number")
    assert not consensus.values_agree(None, "1", "invoice_number")
    assert consensus.values_agree("001", "1", "invoice_number") is False
    assert consensus.values_agree(True, 1, "invoice_number") is False
    assert consensus.values_agree(True, True)
    assert consensus.unwrap({"value": 2, "confidence": 0.5, "source": "handwritten"}) == (
        2,
        0.5,
        "handwritten",
    )
    single = consensus.reconcile_field("name", [("a", "x")])
    assert not single["accepted"] and single["consensus_flag"] == "single_engine"
    hw = consensus.reconcile_field(
        "amount",
        [
            ("a", {"value": 1, "source": "handwritten"}),
            ("b", {"value": 1, "source": "handwritten"}),
        ],
    )
    assert not hw["accepted"]
    flat = consensus.flatten({"header": {"total": 1}, "lines": [{"quantity": 2}], "plain": "x"})
    rebuilt = consensus.unflatten(
        {
            key: {"value": value, "source": "printed", "confidence": None}
            for key, value in flat.items()
        }
    )
    assert rebuilt["lines"][0]["quantity"]["value"] == 2
    extension_result, extension_exceptions = consensus.merge_document(
        "d-extension",
        {
            "a": {
                "source_labelled_fields": {
                    "field_1_1": {
                        "source_label": {"value": "Certificate Reference", "source": "printed"},
                        "observed_value": {"value": "CERT-7", "source": "printed"},
                    }
                }
            },
            "b": {
                "source_labelled_fields": {
                    "field_1_1": {
                        "source_label": {"value": "Certificate Reference", "source": "printed"},
                        "observed_value": {"value": "CERT-7", "source": "printed"},
                    }
                }
            },
        },
    )
    # The extension channel is retained per engine, never reconciled: its key is
    # a label hash plus an occurrence index, so agreement across engines would
    # measure spelling and enumeration order rather than a fact.
    assert not any(path.startswith("source_labelled_fields") for path in extension_result["fields"])
    proposals = extension_result["source_labelled_field_proposals"]
    assert set(proposals) == {"a", "b"}
    assert proposals["a"]["source_labelled_fields.field_1_1.observed_value"]["value"] == "CERT-7"
    assert extension_result["source_labelled_field_proposal_count"] == 4
    # Rule 9: extension entries alone are not a document that passed consensus.
    assert extension_result["review_status"] == "open_exception"
    assert [e["flag"] for e in extension_exceptions] == ["no_consensus_eligible_field"]
    assert extension_exceptions[0]["engines"] == {"a": 2, "b": 2}

    # A document that also carries a controlled-vocabulary field still reconciles
    # that field, and the extension entries stay out of the consensus surface.
    mixed_result, mixed_exceptions = consensus.merge_document(
        "d-mixed",
        {
            eng: {
                "header": {"total_amount": {"value": "10.00", "source": "printed"}},
                "source_labelled_fields": {
                    "field_1_1": {
                        "source_label": {"value": "Certificate Reference", "source": "printed"},
                        "observed_value": {"value": "CERT-7", "source": "printed"},
                    }
                },
            }
            for eng in ("a", "b")
        },
    )
    assert mixed_result["fields"]["header.total_amount"]["accepted"]
    assert mixed_result["field_count"] == 1
    assert mixed_result["source_labelled_field_proposal_count"] == 4
    assert mixed_result["review_status"] == "auto_accepted"
    assert not mixed_exceptions
    bad = write_json(tmp_path / "bad.json", [{"engine": "a", "value": 1}])
    with pytest.raises(ValueError, match="handoff must be a JSON object"):
        consensus.load_records([bad])
    duplicate = write_json(
        tmp_path / "duplicate.json",
        [
            {"engine": "same", "document_id": "d", "value": 1},
            {"engine": "same", "document_id": "d", "value": 2},
        ],
    )
    with pytest.raises(ValueError, match="handoff must be a JSON object"):
        consensus.load_records([duplicate])


def test_consensus_uses_voted_document_type_not_first_engine():
    result, exceptions = consensus.merge_document(
        "d",
        {
            "a": {"document_type": "receipt", "amount": 1},
            "b": {"document_type": "invoice", "amount": 1},
            "c": {"document_type": "invoice", "amount": 1},
        },
    )
    assert result["document_type"] == "invoice"
    assert any(item["field"] == "document_type" for item in exceptions)
    tied, tied_exceptions = consensus.merge_document(
        "d",
        {
            "a": {"document_type": "receipt", "amount": 1},
            "b": {"document_type": "invoice", "amount": 1},
        },
    )
    assert tied["document_type"] == "unknown"
    assert any(item["field"] == "document_type" for item in tied_exceptions)


def test_entity_helpers_cover_identity_rules():
    assert entity_resolve.collapse_initials(["A", "B", "Co"]) == ["AB", "Co"]
    assert entity_resolve.normalize_name("The ACME, Incorporated") == "acme"
    assert entity_resolve.normalize_address("1 Main Street, Boston MA 02110")
    assert entity_resolve.postal_key("Boston MA 02110") == "02110"
    assert entity_resolve.token_set_ratio("a b", "b c") > 0
    assert entity_resolve.sequence_ratio("abc", "abc") == 1
    assert (
        entity_resolve.similarity(
            {"norm_name": "acme", "norm_address": "1 main", "postal": "1"},
            {"norm_name": "acme", "norm_address": "1 main", "postal": "1"},
        )
        == 1
    )
    assert entity_resolve.blocking_keys("acme smith", "boston ma 02110")
    records = entity_resolve.load_records({"documents": [{"document_id": "d", "seller_name": "A"}]})
    assert entity_resolve.extract_parties(records)[0][0]["role"] == "biller"
    mentions, _ = entity_resolve.extract_parties(
        records + [{"document_id": "e", "seller_name": "A"}]
    )
    groups, evidence, ambiguous = entity_resolve.cluster(mentions, 0.8, 0.5)
    assert groups and evidence and not ambiguous
    assert entity_resolve.choose_survivor(["a", "A Long Name"]) == "A Long Name"
    assert entity_resolve.build_parties(mentions, groups)[0]["canonical_name"]


def test_sampling_helpers_cover_planning_and_projection():
    assert sampling.eligible({"review_status": "open_exception"})[0] is False
    assert sampling.eligible({"arithmetic_status": "failed"})[0] is False
    assert sampling.eligible({"review_status": "auto_accepted"})[0] is False
    assert sampling.eligible(
        {"review_status": "auto_accepted", "arithmetic_status": "not_applicable"}
    )[0]
    assert sampling.stratum_of({"invoice_date": "bad"})["year"] == "unknown"
    population = [
        {
            "document_id": "a",
            "value": 100,
            "stratum": {
                "document_type": "invoice",
                "year": "2026",
                "branch": "A",
                "has_handwriting": False,
                "jbig2_suspect": False,
            },
        },
        {
            "document_id": "b",
            "value": 10,
            "stratum": {
                "document_type": "invoice",
                "year": "2026",
                "branch": "B",
                "has_handwriting": True,
                "jbig2_suspect": True,
            },
        },
    ]
    assert sampling.build_mus(population, 50, 0, 1)[1] == []
    certainty, selected, interval = sampling.build_mus(population, 50, 1, 1)
    assert certainty and selected and interval
    assert sampling.build_attribute_sample(population, 1, 1)[1]["oversample_multiplier"] == 9
    projection = sampling.project(
        [
            {"document_id": "a", "recorded_value": 100, "audited_value": 90},
            {"document_id": "b", "recorded_value": 10, "audited_value": 0},
            {"document_id": "z", "recorded_value": 1, "audited_value": 0},
            {"document_id": "z", "recorded_value": 0, "audited_value": 0},
        ],
        interval,
        certainty,
        selected,
    )
    assert projection["findings_unmatched_to_sample"] == ["z"]
    assert projection["invalid_findings"] == [
        {
            "document_id": "z",
            "reason": "finding requires document_id, positive recorded_value, and audited_value",
        }
    ]


def test_sampling_moves_interval_sized_units_to_certainty_and_blocks_factor_overflow():
    population = [
        {"document_id": "large", "value": 60, "stratum": {}},
        {"document_id": "small", "value": 40, "stratum": {}},
    ]
    certainty, selected, interval = sampling.build_mus(population, 1_000, 2, 1)
    assert [item["document_id"] for item in certainty] == ["large"]
    assert [item["document_id"] for item in selected] == ["small"]
    assert interval == 40
    sample = [{"document_id": str(index)} for index in range(11)]
    findings = [
        {"document_id": str(index), "recorded_value": 10, "audited_value": 9} for index in range(11)
    ]
    projection = sampling.project(findings, 10, [], sample)
    assert projection["upper_overstatement_bound_95pct"] is None
    assert projection["projection_limitations"] == ["confidence_factor_table_exhausted"]
    all_certainty, selected, interval = sampling.build_mus(population, 1_000, 3, 1)
    assert {item["document_id"] for item in all_certainty} == {"large", "small"}
    assert selected == [] and interval == 0

    duplicate = sampling.project(
        [
            {"document_id": "small", "recorded_value": 40, "audited_value": 50},
            {"document_id": "small", "recorded_value": 40, "audited_value": 40},
            {"document_id": "equal", "recorded_value": 10, "audited_value": 10},
        ],
        40,
        [],
        [{"document_id": "small"}, {"document_id": "equal"}],
    )
    assert duplicate["observed_understatement_not_projected"] == 10
    assert duplicate["invalid_findings"][0]["reason"].startswith("duplicate")


def test_attribute_sampling_keeps_jbig2_hazard_in_its_own_stratum():
    base = {
        "document_type": "invoice",
        "year": "2026",
        "branch": "A",
        "has_handwriting": False,
    }
    population = [
        {
            "document_id": "clean",
            "value": 1,
            "stratum": {**base, "jbig2_suspect": False},
        },
        {
            "document_id": "hazard",
            "value": 1,
            "stratum": {**base, "jbig2_suspect": True},
        },
    ]
    plan = sampling.build_attribute_sample(population, 2, 1)
    assert len(plan) == 2
    assert {entry["oversample_multiplier"] for entry in plan} == {1, 5}


def test_ingestion_classification_rules_and_safe_output_directory(tmp_path, monkeypatch):
    assert ingest_pages.categorize_text("INVOICE NUMBER: 1\nINVOICE DATE: today") == (
        "commercial_invoice",
        1.0,
        "rule_classified",
    )
    assert ingest_pages.categorize_text("Bill of Lading") == (
        "bill_of_lading",
        1.0,
        "rule_classified",
    )
    assert ingest_pages.categorize_text("Project Instructions") == (
        "out_of_scope_document",
        1.0,
        "out_of_scope",
    )
    assert ingest_pages.categorize_text("") == (None, None, "no_extractable_text")
    assert ingest_pages.categorize_text("ordinary correspondence") == (
        None,
        None,
        "no_high_signal_document_type",
    )
    assert ingest_pages.page_output_name("My Batch.pdf", 7, "bill_of_lading") == (
        "000007__my_batch__p0007__bill_of_lading.pdf",
        "my_batch__p0007__bill_of_lading",
    )
    assert ingest_pages.schema_label("!!!", "source") == "source"
    assert ingest_pages.schema_label(None, "unclassified") == "unclassified"
    hashed = tmp_path / "hashed.txt"
    hashed.write_text("evidence")
    assert len(ingest_pages.sha256(hashed)) == 64
    missing_text, missing_status = ingest_pages.extract_page_text(tmp_path / "none.pdf", 1)
    assert missing_text == "" and missing_status.startswith("pdftotext_failed")
    output = tmp_path / "output"
    ingest_pages.ensure_empty_output_dir(output)
    assert output.is_dir()
    (output / "existing").write_text("x")
    with pytest.raises(ValueError, match="must be empty"):
        ingest_pages.ensure_empty_output_dir(output)
    monkeypatch.setattr(
        ingest_pages.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no"))
    )
    assert ingest_pages.extract_page_text("input.pdf", 1) == ("", "pdftotext_unavailable:OSError")


def test_scan_helpers_cover_pixel_routing_and_summaries(tmp_path, monkeypatch):
    grey = Image.new("RGB", (8, 8), (128, 128, 128))
    coloured = Image.new("RGB", (8, 8), (255, 0, 0))
    assert scan_profile._filters({}) == []
    monkeypatch.setattr(scan_profile.pikepdf, "Array", list)
    assert scan_profile._filters({"/Filter": ["/A", "/B"]}) == ["/A", "/B"]
    assert scan_profile._measure_quality(grey)
    assert scan_profile._measure_chroma(coloured)[0] > 0
    assert scan_profile.quality_band({"dpi": 250, "quality": {}})["quality_band"] == "marginal"
    for rec, bucket in [
        (
            {
                "bit_depth": 1,
                "filters": ["/CCITTFaxDecode"],
                "chroma_fraction": None,
                "notes": [],
                "quality": {},
            },
            "B1",
        ),
        (
            {
                "bit_depth": 1,
                "filters": ["/JBIG2Decode"],
                "chroma_fraction": None,
                "notes": [],
                "quality": {},
            },
            "B2",
        ),
        (
            {
                "bit_depth": 8,
                "colorspace": "/DeviceRGB",
                "filters": [],
                "chroma_fraction": None,
                "notes": [],
                "quality": {},
            },
            "C",
        ),
        (
            {
                "bit_depth": 8,
                "colorspace": "/DeviceRGB",
                "filters": [],
                "chroma_fraction": 0,
                "notes": [],
                "quality": {},
            },
            "G",
        ),
        (
            {
                "bit_depth": 8,
                "colorspace": "/DeviceRGB",
                "filters": [],
                "chroma_fraction": 0.5,
                "notes": [],
                "quality": {},
            },
            "C",
        ),
        (
            {"bit_depth": None, "filters": [], "chroma_fraction": None, "notes": [], "quality": {}},
            "G",
        ),
    ]:
        assert scan_profile.classify(rec)["bucket"] == bucket
    native = scan_profile.classify(
        {
            "bit_depth": None,
            "filters": [],
            "chroma_fraction": None,
            "notes": ["no_raster_image"],
            "quality": {},
        }
    )
    assert native["bucket"] == "N" and native["branch"] == "native_text"
    assert scan_profile.profile_pdf(str(tmp_path / "missing.pdf"))[0]["notes"][0].startswith(
        "open_failed"
    )
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "x.PDF").write_bytes(b"")
    assert scan_profile.collect_pdfs(str(nested))
    pages = [
        {
            "bucket": "G",
            "branch": "B",
            "quality_band": "poor",
            "quality_problems": ["low_contrast"],
            "dpi": 150,
            "notes": ["false_colour"],
            "jbig2_suspect": False,
        },
        {
            "bucket": "B2",
            "branch": "B",
            "quality_band": "marginal",
            "quality_problems": [],
            "dpi": 400,
            "notes": ["jbig2_compressed"],
            "jbig2_suspect": True,
        },
        {
            "bucket": "C",
            "branch": "A",
            "quality_band": "good",
            "quality_problems": [],
            "dpi": 300,
            "notes": ["chroma_unverified"],
            "jbig2_suspect": False,
        },
    ]
    summary = scan_profile.summarize(pages)
    assert summary["hazards"]["jbig2_suspect_pages"] == 1
    native_summary = scan_profile.summarize([native])
    assert native_summary["native_text_only_pages"] == 1
    assert native_summary["corpus_is_black_and_white"] is False
    assert scan_profile.summarize([])["findings"] == ["No hazards detected."]
    assert "All 1 pages are 8-bit grayscale" in scan_profile.summarize([pages[0]])["findings"][1]
    assert "All 1 pages are 1-bit bilevel" in scan_profile.summarize([pages[1]])["findings"][1]
    assert (
        "Mixed black-and-white corpus"
        in scan_profile.summarize([pages[0], pages[1]])["findings"][1]
    )


def test_scan_profile_page_handles_images_and_failures(monkeypatch):
    class Page:
        def __init__(self, objects, fail=False):
            self.Resources = type("Resources", (), {"XObject": objects})()
            self.fail = fail

        def get(self, key, default=None):
            if self.fail:
                raise ValueError("bad")
            return {"/Contents": "text", "/Resources": "/Font", "/MediaBox": [0, 0, 72, 72]}.get(
                key, default
            )

    class Broken:
        def get(self, *args):
            raise ValueError("bad")

    image = {
        "/Subtype": "/Image",
        "/Width": "144",
        "/Height": "144",
        "/Filter": "/JBIG2Decode",
        "/BitsPerComponent": "8",
        "/ColorSpace": "/DeviceRGB",
    }
    monkeypatch.setattr(
        scan_profile.pikepdf,
        "PdfImage",
        lambda _: type("Pi", (), {"as_pil_image": lambda self: Image.new("RGB", (4, 4), "red")})(),
    )
    rec = scan_profile.profile_page(
        Page(
            {
                "bad": Broken(),
                "small": {"/Subtype": "/Image", "/Width": 1, "/Height": 1},
                "image": image,
            }
        ),
        0,
    )
    assert rec["has_text_layer"] and rec["dpi"] == 144 and rec["notes"] == ["jbig2_compressed"]
    assert scan_profile.profile_page(Page({}, fail=True), 1)["notes"] == ["no_raster_image"]
    monkeypatch.setattr(
        scan_profile.pikepdf, "PdfImage", lambda _: (_ for _ in ()).throw(RuntimeError("fail"))
    )
    assert (
        scan_profile.profile_page(Page({"image": image}), 2)["notes"][-1]
        == "pixel_read_failed:RuntimeError"
    )


def test_scan_pixel_absence_and_profile_pdf_limit(monkeypatch, tmp_path):
    old = scan_profile.HAVE_PIXEL_TOOLS
    monkeypatch.setattr(scan_profile, "HAVE_PIXEL_TOOLS", False)
    assert scan_profile._measure_quality(Image.new("L", (1, 1))) == {}
    assert scan_profile._measure_chroma(Image.new("RGB", (1, 1))) == (None, None)
    monkeypatch.setattr(scan_profile, "HAVE_PIXEL_TOOLS", old)

    class Pdf:
        docinfo = {"/Producer": "test"}
        pages = [{}, {}]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(scan_profile.pikepdf, "open", lambda _: Pdf())
    monkeypatch.setattr(
        scan_profile,
        "profile_page",
        lambda page, index, deep: {
            "bit_depth": None,
            "filters": [],
            "chroma_fraction": None,
            "notes": [],
            "quality": {},
        },
    )
    assert len(scan_profile.profile_pdf(str(tmp_path / "x.pdf"), limit=1)) == 1


def test_a_month_written_as_a_word_is_read_not_refused():
    """Every date on a real commission corpus was spelled, and none parsed.

    The parser handled `2024-05-31` and `05/31/2024` and nothing else, so 18 of
    18 documents were reported as having no parseable date and excluded from
    every calendar and GL check -- while `statement_date: "May 31, 2024"` sat in
    each header. Completeness reported its period as None to None.

    A spelled month carries no ambiguity for the date-order policy to resolve:
    "May 31, 2024" is 2024-05 under every convention. That is why it is read
    before the policy is consulted rather than governed by it.
    """
    for text in ("May 31, 2024", "31 May 2024", "May 2024", "MAY 31, 2024", "  may 31 2024  "):
        assert completeness.month_of(text) == "2024-05", text
    assert completeness.month_of("Sept. 4, 2021") == "2021-09"
    assert completeness.month_of("September 2020") == "2020-09"
    assert completeness.month_of("2024-05-31") == "2024-05"

    # Unambiguous means unambiguous: iso_only refuses to *guess*, and a named
    # month is not a guess. A numeric date still is, and is still refused.
    assert completeness.month_of("May 31, 2024", order="iso_only") == "2024-05"
    assert completeness.month_of("05/03/2023", order="iso_only") is None
    assert completeness.month_of("05/03/2023", order="day_first") == "2023-03"

    # Nothing is invented. A month name this does not know, a year that is not
    # there, and a word that merely begins with one all return None -- the last
    # because a month must end where the word does.
    for text in ("not a date", "Mayonnaise 2024", "May 31", "Smarch 2024", "2024", ""):
        assert completeness.month_of(text) is None, text
    assert completeness.named_month(None) is None


def test_a_commission_statement_carries_its_own_vendor_and_date():
    """The invoice vocabulary named neither, and both sat in the header.

    On a real corpus this produced one vendor -- "UNKNOWN" -- across 18
    documents, and no date on any of them. The invoice fields stay first so
    nothing about an invoice changes.
    """
    statement = {
        "document_id": "s1",
        "header": {
            "brand_name": {"value": "Murbrook"},
            "dealer_name": {"value": "Northgate Co"},
            "statement_date": {"value": "May 31, 2024"},
        },
        "lines": [{"commission_amount": "662.89"}],
    }
    doc = completeness.extract_docs([statement])[0]
    # The brand issues the statement, so it is the series a missing-statement
    # gap is measured against -- not the dealer, who is the same on every page.
    assert doc["vendor"] == "Murbrook"
    assert doc["date"] == "May 31, 2024"
    assert doc["month"] == "2024-05"

    # An invoice is untouched: its own fields still win over the commission ones.
    invoice = dict(statement)
    invoice["header"] = dict(statement["header"])
    invoice["header"]["seller_name"] = {"value": "Acme"}
    invoice["header"]["invoice_date"] = {"value": "2023-01-15"}
    invoice_doc = completeness.extract_docs([invoice])[0]
    assert invoice_doc["vendor"] == "Acme"
    assert invoice_doc["month"] == "2023-01"

    # A statement with neither is still UNKNOWN and undated rather than guessed.
    bare = completeness.extract_docs([{"document_id": "s2", "header": {}, "lines": []}])[0]
    assert bare["vendor"] == "UNKNOWN" and bare["date"] is None

    # period_end stands in for an undated statement.
    undated = completeness.extract_docs(
        [{"document_id": "s3", "header": {"period_end": {"value": "June 2024"}}, "lines": []}]
    )[0]
    assert undated["month"] == "2024-06"


def test_every_declared_document_type_is_detectable_or_knowingly_exempt():
    """A schema value no rule can produce is a classifier blind spot nothing reports.

    A 716-page commission corpus classified 25 pages and escalated 691 because
    `commission_statement` and `commission_report` were declared document types
    with no rule able to emit them. Every later lane inherited an unresolved
    family and no artifact said why, so the gap has to fail here instead.
    """
    import extraction_schema

    declared = set(extraction_schema.DOCUMENT_TYPES)
    ruled = {document_type for document_type, _ in ingest_pages.TYPE_RULES}
    ruled.discard("out_of_scope_document")
    exempt = ingest_pages.RULE_EXEMPT_DOCUMENT_TYPES

    assert ruled <= declared, sorted(ruled - declared)
    assert exempt <= declared, sorted(exempt - declared)
    assert not (ruled & exempt), sorted(ruled & exempt)
    assert declared - ruled - exempt == set(), sorted(declared - ruled - exempt)


def test_commission_documents_are_rule_classified():
    """The corpus family this classifier was blind to must now classify."""
    for text, expected in (
        ("Commission Statement for June 2024", "commission_statement"),
        ("STATEMENT OF COMMISSIONS - Q2", "commission_statement"),
        ("Commission Report — dealer summary", "commission_report"),
        ("Commission Detail Report", "commission_report"),
    ):
        document_type, confidence, status = ingest_pages.categorize_text(text)
        assert (document_type, confidence, status) == (expected, 1.0, "rule_classified")


def test_every_explicit_key_field_is_a_name_the_extractor_can_emit():
    """Rank 1 must ask for the fields the schema actually writes.

    This lane looked for ``ack_number`` for a whole corpus. It is not in
    ``HEADER_FIELDS`` and never was -- the schema calls it
    ``acknowledgement_number`` -- so every document that printed its
    acknowledgement number was read correctly and then registered as
    unresolved. The alias stays only because attribution writes it back onto
    the records it resolves; everything else must be a real schema field.
    """
    import extraction_schema

    declared = set(extraction_schema.HEADER_FIELDS)
    written_back = {"ack_number"}
    unknown = set(attribution.EXPLICIT_KEY_FIELDS) - declared - written_back
    assert unknown == set(), sorted(unknown)


def test_rank_one_reads_the_acknowledgement_number_the_corpus_prints():
    ref = attribution.empty_reference()
    resolved = attribution.resolve_explicit(
        {"header": {"acknowledgement_number": {"value": "137783", "source": "printed"}}}, ref
    )
    assert (resolved["key"], resolved["rank"], resolved["field"]) == (
        "137783",
        1,
        "acknowledgement_number",
    )


def test_key_field_order_can_be_overridden_for_a_different_engagement():
    ref = attribution.empty_reference()
    record = {
        "header": {
            "job_number": {"value": "12497"},
            "acknowledgement_number": {"value": "137783"},
        }
    }
    assert attribution.resolve_explicit(record, ref)["field"] == "acknowledgement_number"
    assert (
        attribution.resolve_explicit(record, ref, ("job_number", "acknowledgement_number"))["field"]
        == "job_number"
    )


def printed(**fields):
    """A line whose fields are printed readings."""
    return {name: {"value": value, "source": "printed"} for name, value in fields.items()}


def test_a_key_every_line_prints_attributes_the_document():
    """Every line names the same project and the header names none.

    The lines are ignored on purpose when they name several keys; this page
    names one, on every line that names any, and was registered unresolved.
    """
    ref = attribution.empty_reference()
    record = {
        "header": {},
        "lines": [
            printed(project_number="22W0000108"),
            printed(project_number="22W0000108"),
            printed(description="Total"),
        ],
    }
    resolved = attribution.resolve_from_lines(record, ref)
    assert (resolved["key"], resolved["rank"], resolved["method"]) == (
        "22W0000108",
        1,
        "explicit_printed_on_lines",
    )


def test_lines_naming_several_keys_are_an_allocation_question_not_a_key():
    """Two jobs on one statement is two attributions, never a choice of one."""
    ref = attribution.empty_reference()
    several = {"lines": [printed(job_number="12951"), printed(job_number="13096")]}
    assert attribution.resolve_from_lines(several, ref) is None
    assert attribution.resolve_from_lines({"lines": [printed(description="x")]}, ref) is None
    assert attribution.resolve_from_lines({}, ref) is None


def test_a_key_on_the_lines_is_held_to_the_corpus_format():
    """A line key the corpus's own formats refuse is rejected, not credited."""
    ref = attribution.empty_reference()
    ref["formats"] = {"######"}
    resolved = attribution.resolve_from_lines(
        {"lines": [printed(project_number="22W0000108")]}, ref
    )
    assert resolved["key"] is None
    assert resolved["rejected_key"] == "22W0000108"


def test_the_run_records_a_key_found_on_the_lines_as_its_own_method(tmp_path, monkeypatch):
    """The method mix says how much of an attribution set came off the lines."""
    import json
    import sys

    records = tmp_path / "records.json"
    records.write_text(
        json.dumps(
            [
                {
                    "document_id": "run__p1",
                    "header": {},
                    "lines": [printed(sales_order_number="2200004491")],
                }
            ]
        )
    )
    out, register = tmp_path / "attributed.json", tmp_path / "register.json"
    argv = ["attribution.py", str(records), "--out", str(out), "--register", str(register)]
    monkeypatch.setattr(sys, "argv", [*argv, "--quiet"])
    try:
        attribution.main()
    except SystemExit as exc:
        assert not exc.code
    summary = json.loads(out.read_text())["summary"]
    assert summary["attribution_by_method"] == {"explicit_printed_on_lines": 1}


def test_the_crediting_rule_credits_each_money_line_to_the_key_it_prints():
    """Several jobs on one statement are credited line by line, never to a chosen one."""
    ref = attribution.empty_reference()
    record = {
        "lines": [
            printed(job_number="12951", commission_amount="100.00"),
            printed(job_number="13096", project_number="P-1", commission_amount="(25.00)"),
            printed(description="carried forward", commission_amount="0.00"),
            printed(description="a heading"),
            "not a line",
        ]
    }
    assert attribution.credit_by_line(record, ref) == [
        {"line": 0, "key": "12951", "key_field": "job_number", "amount": 100.0},
        {"line": 1, "key": "13096", "key_field": "job_number", "amount": -25.0},
    ]
    # A money line printing no key, or a key the corpus formats refuse, credits nothing.
    unkeyed = {
        "lines": [
            printed(job_number="12951", commission_amount="1.00"),
            printed(commission_amount="2.00"),
        ]
    }
    assert attribution.credit_by_line(unkeyed, ref) is None
    formats = attribution.empty_reference()
    formats["formats"] = {"######"}
    keyed = {"lines": [printed(job_number="12951", commission_amount="1.00")]}
    assert attribution.credit_by_line(keyed, formats) is None
    # A document with no money line has nothing to credit.
    assert attribution.credit_by_line({"lines": [printed(job_number="12951")]}, ref) is None
    assert attribution.credit_by_line({}, ref) is None


def test_the_run_registers_a_statement_credited_by_line_as_an_answer(tmp_path, monkeypatch):
    """Under the rule a statement of several jobs is credited, not left unresolved."""
    import json
    import sys

    records = tmp_path / "records.json"
    records.write_text(
        json.dumps(
            [
                {
                    "document_id": "run__p1",
                    "header": {},
                    "lines": [
                        printed(job_number="12951", commission_amount="100.00"),
                        printed(job_number="13096", commission_amount="50.00"),
                    ],
                },
                {
                    "document_id": "run__p2",
                    "header": {},
                    "lines": [printed(commission_amount="10.00")],
                },
            ]
        )
    )

    def run(*extra):
        out, register = tmp_path / "attributed.json", tmp_path / "register.json"
        argv = ["attribution.py", str(records), "--out", str(out), "--register", str(register)]
        monkeypatch.setattr(sys, "argv", [*argv, "--quiet", *extra])
        try:
            attribution.main()
        except SystemExit as exc:
            assert not exc.code
        registered = {e["document_id"]: e for e in json.loads(register.read_text())["register"]}
        return json.loads(out.read_text())["summary"], registered

    summary, registered = run("--credit-by-line", "run-authorization-amendment-63")
    credited = registered["run__p1"]
    assert (credited["reason_code"], credited["is_failure"]) == ("credited_by_line", False)
    assert [credit["key"] for credit in credited["line_credits"]] == ["12951", "13096"]
    assert credited["crediting_authorization"] == "run-authorization-amendment-63"
    assert (registered["run__p2"]["reason_code"], registered["run__p2"]["is_failure"]) == (
        "unresolved",
        True,
    )
    assert (summary["credited_by_line_documents"], summary["credited_by_line_value"]) == (1, 150.0)
    assert "credited line by line under run-authorization-amendment-63" in " ".join(
        summary["findings"]
    )

    # Without the rule the same statement is an allocation question, unresolved.
    summary, registered = run()
    assert {entry["reason_code"] for entry in registered.values()} == {"unresolved"}
    assert summary["credited_by_line_documents"] == 0


def test_a_payment_record_and_a_blank_page_are_answers_not_failures(tmp_path, monkeypatch):
    """A page the image shows is a payment, or blank, is registered with its reason."""
    import json
    import sys

    records = tmp_path / "records.json"
    records.write_text(
        json.dumps(
            [
                {"document_id": f"run__p{n}", "header": {"total_amount": "10.00"}, "lines": []}
                for n in (1, 2, 3)
            ]
        )
    )
    dispositions = tmp_path / "dispositions.json"
    dispositions.write_text(json.dumps({"run__p1": "payment_record", "run__p2": "blank_page"}))
    out, register = tmp_path / "attributed.json", tmp_path / "register.json"
    argv = ["attribution.py", str(records), "--out", str(out), "--register", str(register)]
    monkeypatch.setattr(sys, "argv", [*argv, "--dispositions", str(dispositions), "--quiet"])
    try:
        attribution.main()
    except SystemExit:
        pass
    entries = {e["document_id"]: e for e in json.loads(register.read_text())["register"]}
    assert (entries["run__p1"]["reason_code"], entries["run__p1"]["is_failure"]) == (
        "payment_record",
        False,
    )
    assert (entries["run__p2"]["reason_code"], entries["run__p2"]["is_failure"]) == (
        "blank_page",
        False,
    )
    # A document nobody answered is still the honest residual.
    assert (entries["run__p3"]["reason_code"], entries["run__p3"]["is_failure"]) == (
        "unresolved",
        True,
    )


def test_a_populated_key_field_rank_one_never_reads_is_reported():
    """A blind rank and an absent key look identical in the attribution rate."""
    assert attribution.KEY_FIELD_NAME.search("acknowledgement_number")
    assert attribution.KEY_FIELD_NAME.search("work_order_no")
    assert not attribution.KEY_FIELD_NAME.search("customer_name")
    assert not attribution.KEY_FIELD_NAME.search("statement_number")


def test_every_field_a_control_reads_is_one_the_schema_can_emit():
    """A lookup that can never match reports the same as a corpus with nothing to find.

    Three controls shipped reading names `extraction_schema` does not declare,
    and each reported a clean-looking zero rather than a defect:

    * attribution rank 1 asked for ``ack_number`` (the schema says
      ``acknowledgement_number``) and reported 26 attributed documents where the
      true figure was 137;
    * the selling-location hierarchy asked for ``branch``, ``office``,
      ``letterhead_city`` and ``salesperson`` and resolved 0 of 716, while the
      corpus carried ``sales_representative_name`` on 112;
    * completeness read ``credits_applied`` and ``payment_status`` and ran aging
      closure on permanently empty inputs.

    A name that genuinely cannot come from extraction is legitimate -- a client
    reference export or an operator amendment can supply one -- but it has to be
    written down as a decision in ``NON_SCHEMA_RECORD_ALIASES`` rather than left
    to look like a field.
    """
    import re
    from pathlib import Path

    import extraction_schema

    declared = set(extraction_schema.HEADER_FIELDS)
    for name in ("LINE_FIELDS", "ACCESSORIAL_FIELDS"):
        declared |= set(getattr(extraction_schema, name, ()))
    allowed = declared | set(extraction_schema.NON_SCHEMA_RECORD_ALIASES)

    lookup = re.compile(r'\bget\((?:rec|record|doc|document|entry)\w*,\s*"([a-z_]{3,})"')
    unknown = {}
    for path in sorted((Path(__file__).resolve().parents[1] / "scripts").rglob("*.py")):
        for field in lookup.findall(path.read_text(encoding="utf-8")):
            if field not in allowed:
                unknown.setdefault(field, set()).add(path.name)

    assert unknown == {}, (
        "these controls read a field name the schema does not emit; either use the "
        "schema's name or register it in NON_SCHEMA_RECORD_ALIASES with the reason: "
        + ", ".join(
            f"{field} ({', '.join(sorted(where))})" for field, where in sorted(unknown.items())
        )
    )


def test_the_alias_registry_does_not_shadow_a_real_schema_field():
    """An alias for a name the schema already declares hides the real field."""
    import extraction_schema

    declared = set(extraction_schema.HEADER_FIELDS)
    for name in ("LINE_FIELDS", "ACCESSORIAL_FIELDS"):
        declared |= set(getattr(extraction_schema, name, ()))
    shadowed = declared & set(extraction_schema.NON_SCHEMA_RECORD_ALIASES)
    assert shadowed == set(), sorted(shadowed)


def test_the_selling_location_hierarchy_reads_the_representative_the_schema_emits():
    """`salesperson` is `sales_representative_name`, and the corpus carries 112."""
    ref = attribution.empty_reference()
    resolved = attribution.resolve_selling_location(
        {"header": {"sales_representative_name": {"value": "J. Mercer"}}}, ref, {}
    )
    assert resolved["method"] == "salesperson_needs_territory_table"
    assert attribution.resolve_selling_location({"header": {}}, ref, {})["method"] == "unresolved"


def test_an_explicit_selling_location_is_read_from_the_schema_field_first():
    ref = attribution.empty_reference()
    resolved = attribution.resolve_selling_location(
        {"header": {"warehouse_location": {"value": "Boston"}, "branch": {"value": "NY"}}}, ref, {}
    )
    assert (resolved["selling_location"], resolved["method"]) == ("Boston", "explicit_field")


def test_whitespace_inside_a_name_is_not_information():
    """One inserted space made a supplier two parties across 125 documents.

    `token_set_ratio` compares token *sets*, so `murbrook` and `murb rook` are
    disjoint and score 0.000; the blend put them at 0.320, below even the 0.72
    review band, so they never reached adjudication either. Identical characters
    differently spaced are the same name.
    """
    import entity_resolve

    assert entity_resolve.token_set_ratio("murbrook", "murb rook") == 0.0
    assert entity_resolve.similarity("murbrook", "murb rook") == 1.0
    assert entity_resolve.similarity("lumen weft", "lumenweft") == 1.0
    # Names that differ by more than spacing are untouched, so this cannot
    # become a way to merge two companies that share a prefix.
    assert entity_resolve.similarity("lumen weft", "weft") < 0.72
    assert entity_resolve.similarity("halvor", "norvena") < 0.1


def test_a_space_variant_is_blocked_into_the_same_comparison():
    """The scores above never got a chance: the pair was never compared.

    `murbrook` blocked on {'murbro'} and `murb rook` on {'mf', 'mura'}, so no
    threshold could have merged them.
    """
    import entity_resolve

    assert entity_resolve.blocking_keys("murbrook", None) & entity_resolve.blocking_keys(
        "murb rook", None
    )
    assert not (
        entity_resolve.blocking_keys("halvor", None) & entity_resolve.blocking_keys("norvena", None)
    )


def test_the_brand_is_resolved_as_the_party_it_is():
    """A supplier is the counterparty that owes the commission, not a stray label."""
    import entity_resolve

    mentions, _ = entity_resolve.extract_parties(
        [
            {"document_id": "d1", "header": {"brand_name": {"value": "MURBROOK INC."}}},
            {"document_id": "d2", "header": {"brand_name": {"value": "murb rook"}}},
            {"document_id": "d3", "header": {"manufacturer_name": {"value": "Norvena"}}},
        ]
    )
    assert {mention["role"] for mention in mentions} == {"brand"}
    assert {mention["field"] for mention in mentions} == {"brand_name", "manufacturer_name"}


def test_the_engagement_s_own_party_is_not_a_supplier():
    """A rep agency appears on every page of its own commission statements.

    An extractor with no notion of whose engagement this is reads that name into
    `brand_name` as readily as the manufacturer's. On the commission corpus that
    put the agency into a supplier field on 142 documents, where it silently
    became the largest "brand" in the dataset and every figure grouped by brand
    was wrong. Flagged, never removed: the extractor read what was on the page,
    and which name is the supplier is the client's statement.
    """
    import validate_extraction

    party = frozenset({validate_extraction.normalized_party("Northgate Co")})
    for spelling in ("Northgate Co.", "NORTHGATE CO LLC", "Northgate & Co", "northgate co"):
        issue = validate_extraction.engagement_party_in_supplier_field(
            "d1", "header.brand_name", "brand_name", {"value": spelling}, party
        )
        assert issue is not None, spelling
        assert issue["reason"] == "engagement_party_named_as_supplier"

    # A real supplier is untouched, and so is the agency in a field where it belongs.
    assert (
        validate_extraction.engagement_party_in_supplier_field(
            "d1", "header.brand_name", "brand_name", {"value": "Murbrook"}, party
        )
        is None
    )
    assert (
        validate_extraction.engagement_party_in_supplier_field(
            "d1", "header.payee_name", "payee_name", {"value": "Northgate Co."}, party
        )
        is None
    )
    # Configured with no engagement party, the rule does nothing at all.
    assert (
        validate_extraction.engagement_party_in_supplier_field(
            "d1", "header.brand_name", "brand_name", {"value": "Northgate Co."}, frozenset()
        )
        is None
    )


def test_party_names_compare_without_punctuation_or_corporate_suffixes():
    import validate_extraction

    normalized = validate_extraction.normalized_party
    assert normalized("Northgate Co.") == normalized("NORTHGATE CO LLC") == "northgate"
    assert normalized("Marlow / Bramwell Inc.") == "marlow bramwell"
    assert normalized("Murbrook") != normalized("Northgate Co")


def test_an_empty_supplier_field_is_not_the_engagement_party():
    """A null brand is a missing supplier, not the agency in the wrong place."""
    import validate_extraction

    party = frozenset({validate_extraction.normalized_party("Northgate Co")})
    assert (
        validate_extraction.engagement_party_in_supplier_field(
            "d1", "header.brand_name", "brand_name", {"value": None}, party
        )
        is None
    )


def test_the_rule_reaches_a_record_through_the_validator():
    """The finding has to arrive on the record, not just from the helper."""
    import validate_extraction

    party = frozenset({validate_extraction.normalized_party("Northgate Co")})
    validated, exceptions = validate_extraction.validate(
        [
            {
                "document_id": "d1",
                "header": {
                    "brand_name": {"value": "Northgate Co LLC", "source": "printed"},
                    "vendor_name": {"value": "Murbrook", "source": "printed"},
                },
            }
        ],
        {"USD"},
        party,
    )
    reasons = [issue["reason"] for issue in validated[0]["field_validation_findings"]]
    assert reasons == ["engagement_party_named_as_supplier"]
    assert validated[0]["field_validation_status"] == "client_review_required"
    assert any(item["reason"] == "engagement_party_named_as_supplier" for item in exceptions)


def test_a_declared_canonical_name_outranks_the_count():
    """Frequency made an OCR artefact the canonical name of a supplier.

    `murb rook` appeared on 147 documents against 136 spellings of `Murbrook`,
    so the party -- and every report and CRM record derived from it -- carried
    the artefact. Spelling authority belongs to the engagement.
    """
    import entity_resolve

    variants = ["murb rook"] * 147 + ["murbrook"] * 78 + ["Murbrook"] * 52 + ["Murbrook"] * 3
    assert entity_resolve.choose_survivor(variants) == "murb rook"
    assert entity_resolve.choose_survivor(variants, ["Murbrook"]) == "Murbrook"
    # Matched ignoring case and whitespace, so an operator need not reproduce a
    # variant exactly.
    assert entity_resolve.choose_survivor(variants, ["MURB ROOK"]) == "MURB ROOK"
    # A declaration for some other party changes nothing here.
    assert entity_resolve.choose_survivor(variants, ["Halvor"]) == "murb rook"


def test_preferring_the_spelling_without_a_space_would_corrupt_two_names_to_fix_one():
    """The obvious repair is worse than the defect, which is why it is not used.

    `Lumen Weft` appears 321 times against 11 of `LumenWeft`, and
    `Marlow / Bramwell` 671 times against 67 of `Marlow/Bramwell`. A rule
    preferring the space-free spelling would take the misreading in both cases.
    A space wrongly inserted and a space wrongly dropped are both OCR damage and
    are indistinguishable from inside the corpus.
    """
    import entity_resolve

    lumen = ["Lumen Weft"] * 321 + ["LumenWeft"] * 11
    martin = ["Marlow / Bramwell"] * 671 + ["Marlow/Bramwell"] * 67
    assert entity_resolve.choose_survivor(lumen) == "Lumen Weft"
    assert entity_resolve.choose_survivor(martin) == "Marlow / Bramwell"


def test_a_spacing_ambiguity_is_reported_rather_than_guessed():
    """The canonical name was chosen by a count between forms OCR produced."""
    import entity_resolve

    assert entity_resolve.spacing_variants({"murb rook", "Murbrook", "MURBROOK"}) == [
        "MURBROOK",
        "Murbrook",
        "murb rook",
    ]
    # A name with no spacing disagreement raises no question.
    assert entity_resolve.spacing_variants({"HALVOR", "Halvor"}) == []
    assert entity_resolve.spacing_variants({"Norvena LLC"}) == []
    # Two genuinely different names are not one ambiguity.
    assert entity_resolve.spacing_variants({"Halvor", "Norvena"}) == []


def test_a_party_name_with_no_letter_is_not_a_party():
    """`7144`, `25556` and `0` became three suppliers on a real run.

    A field that should hold a supplier held an account number, and each one
    resolved to a party of its own -- which is a CRM account of its own, with
    its own concentration and retention figures. The reading is real and the
    field it landed in is wrong, so it is retained as a mapping finding rather
    than resolved into a party.
    """
    records = [
        {"document_id": "d1", "header": {"brand_name": "7144"}},
        {"document_id": "d2", "header": {"brand_name": "0"}},
        {"document_id": "d3", "header": {"brand_name": "Lumen Weft"}},
        {"document_id": "d4", "header": {"brand_name": "3-USA"}},
    ]
    mentions, refused = entity_resolve.extract_parties(records)
    assert [m["raw_name"] for m in mentions] == ["Lumen Weft", "3-USA"]
    assert [r["raw_name"] for r in refused] == ["7144", "0"]
    assert {r["reason"] for r in refused} == {"party_name_carries_no_letter"}
    # The field it came from is the finding, so it is named on the exception.
    assert {r["field"] for r in refused} == {"brand_name"}


def test_nameable_needs_one_letter_not_a_tidy_name():
    """The guard is deliberately narrow: it refuses non-names, not odd ones."""
    assert entity_resolve.nameable("A") and entity_resolve.nameable("3-USA")
    assert entity_resolve.nameable("18313 NORTHGATE C")
    assert not entity_resolve.nameable("7144")
    assert not entity_resolve.nameable("0")
    assert not entity_resolve.nameable("-")


def party(key, name, mentions, variants=None, roles=("brand",), docs=1):
    return {
        "party_key": key,
        "canonical_name": name,
        "normalized_name": entity_resolve.normalize_name(name),
        "name_variants": list(variants or [name]),
        "variant_count": len(list(variants or [name])),
        "roles": list(roles),
        "mention_count": mentions,
        "source_document_count": docs,
    }


def test_one_edit_apart_is_one_edit_and_no_more():
    assert entity_resolve.one_edit_apart("lumen weft", "lumen wefy")
    assert entity_resolve.one_edit_apart("linden world", "lindew world")
    # A deletion counts; two edits do not.
    assert entity_resolve.one_edit_apart("halvor", "halvr")
    assert not entity_resolve.one_edit_apart("lumen weft", "lumen vxyz")
    assert not entity_resolve.one_edit_apart("halvor", "norvena")
    assert not entity_resolve.one_edit_apart("abc", "abc")


def test_a_rare_fragment_is_named_but_a_longer_rare_name_is_not():
    """Direction matters: a product line is not its brand."""
    assert entity_resolve.variant_reason("bramwell", "marlow bramwell") == (
        "every_word_appears_in_a_far_more_common_name"
    )
    assert entity_resolve.variant_reason("lumen wefy", "lumen weft") == (
        "one_character_variant_of_a_far_more_common_name"
    )
    # `murbrook espace de jour` carries words `murbrook` does not. It is a
    # product line and merging it would erase that.
    assert entity_resolve.variant_reason("murbrook espace de jour", "murbrook") is None
    assert entity_resolve.variant_reason("halvor", "norvena") is None


def test_absorption_needs_the_frequency_gate_not_only_the_similarity():
    """The standing rule holds where two names are comparably common.

    Merging on a single character is how a separate subsidiary is absorbed into
    its parent. What makes it safe here is the ratio: a name seen once, one
    character from a name seen sixty times on the same corpus, is a misreading.
    Seen thirty times against sixty, it is a question for a person.
    """
    common = party("PTY-1", "Lumen Weft", 60)
    rare = party("PTY-2", "Lumen Wefy", 1)
    kept, absorbed = entity_resolve.absorb_ocr_variants([common, rare], 10)
    assert [p["party_key"] for p in kept] == ["PTY-1"]
    assert absorbed[0]["mention_ratio"] == 60.0
    assert absorbed[0]["absorbed_name"] == "Lumen Wefy"
    # The absorbed spelling survives on the party, so the merge is auditable.
    assert "Lumen Wefy" in kept[0]["name_variants"]
    assert kept[0]["mention_count"] == 61
    # Comparable frequencies are left alone.
    close = [party("PTY-1", "Lumen Weft", 60), party("PTY-2", "Lumen Wefy", 30)]
    kept, absorbed = entity_resolve.absorb_ocr_variants(close, 10)
    assert len(kept) == 2 and absorbed == []


def test_absorption_is_off_unless_asked_for():
    """The standing rule is the safer default; this is a judgement about a corpus."""
    parties = [party("PTY-1", "Lumen Weft", 60), party("PTY-2", "Lumen Wefy", 1)]
    kept, absorbed = entity_resolve.absorb_ocr_variants(parties, 0)
    assert len(kept) == 2 and absorbed == []


def test_a_party_already_absorbed_does_not_then_absorb_others():
    """Otherwise a chain of misreadings collapses through a name nothing kept."""
    parties = [
        party("PTY-1", "Lumen Weft", 100),
        party("PTY-2", "Lumen Wefy", 5),
        party("PTY-3", "Lumen Wefz", 1),
    ]
    kept, absorbed = entity_resolve.absorb_ocr_variants(parties, 10)
    assert [p["party_key"] for p in kept] == ["PTY-1"]
    assert {a["surviving_party_key"] for a in absorbed} == {"PTY-1"}


def test_a_trailing_character_is_one_edit_too():
    """The zip runs out before any difference: one name is the other plus a letter."""
    assert entity_resolve.one_edit_apart("halvor", "halvors")
    assert entity_resolve.one_edit_apart("halvors", "halvor")


def test_an_unrelated_name_is_not_absorbed_however_rare():
    """The ratio gate opens the door; similarity still has to walk through it."""
    parties = [party("PTY-1", "HALVOR", 100), party("PTY-2", "Norvena LLC", 1)]
    kept, absorbed = entity_resolve.absorb_ocr_variants(parties, 10)
    assert len(kept) == 2 and absorbed == []


def test_a_variant_does_not_absorb_into_a_party_that_was_itself_absorbed():
    """Otherwise a party could be merged into a name no longer in the master."""
    parties = [
        party("PTY-1", "alpha beta", 1000),
        party("PTY-2", "beta", 50),
        party("PTY-3", "beto", 1),
    ]
    kept, absorbed = entity_resolve.absorb_ocr_variants(parties, 10)
    # `beta` is a fragment of `alpha beta` and goes. `beto` is one edit from
    # `beta` but `beta` is gone, and it is neither edit nor fragment of
    # `alpha beta`, so it survives on its own rather than following a name that
    # is no longer there.
    assert sorted(p["party_key"] for p in kept) == ["PTY-1", "PTY-3"]
    assert [a["absorbed_party_key"] for a in absorbed] == ["PTY-2"]


def test_a_fragment_several_names_carry_is_left_alone():
    """`Design` sits inside more than one name, so folding it into one is a guess.

    At 10:1 only the most common of them passed the frequency gate, and the
    fragment went into Cornerwise Design Services on nothing but its count.
    `Bramwell` sits inside one name only and is still absorbed.
    """
    parties = [
        party("PTY-1", "CORNERWISE DESIGN SERV.", 146),
        party("PTY-2", "ONE GLOBE DESIGN SOURCE", 30),
        party("PTY-3", "Design", 14),
        party("PTY-4", "Marlow / Bramwell", 520),
        party("PTY-5", "Bramwell", 28),
    ]
    kept, absorbed = entity_resolve.absorb_ocr_variants(parties, 10)
    assert [a["absorbed_name"] for a in absorbed] == ["Bramwell"]
    assert "PTY-3" in {p["party_key"] for p in kept}


def test_a_decided_pair_and_a_short_name_are_never_absorbed():
    """A person's decision outranks the ratio, and one letter of three is a new name."""
    kept_apart = entity_resolve.kept_apart_by(
        [
            {"decision": "branch_of", "parent_party_key": "PTY-2", "branch_party_key": "PTY-1"},
            {"decision": "different_parties", "party_keys": ["PTY-5", "PTY-6"]},
            {"decision": "same_party", "party_keys": ["PTY-7"]},
        ]
    )
    assert kept_apart == {frozenset({"PTY-1", "PTY-2"}), frozenset({"PTY-5", "PTY-6"})}
    parties = [
        party("PTY-1", "Office Quarters Inc - NY", 67),
        party("PTY-2", "Office Quarters Inc", 1),
        party("PTY-3", "XTB, LLC", 13),
        party("PTY-4", "XTK", 1),
    ]
    kept, absorbed = entity_resolve.absorb_ocr_variants(parties, 10, kept_apart)
    assert absorbed == [] and len(kept) == 4
    assert entity_resolve.variant_reason("xtk", "xtb") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10%", 10.0),
        ("-2%", -2.0),
        ("7.5%", 7.5),
        ("1,250%", 1250.0),
        ({"value": "10%", "source": "printed"}, 10.0),
        ("0.75", 0.75),
        (0.1, 0.1),
        ({"value": None}, None),
        ("%", None),
        ("ten%", None),
        (None, None),
    ],
)
def test_rate_parser_reads_a_stated_percent_sign(value, expected):
    """A rate written `10%` is a rate, not a missing term."""
    assert arithmetic_check.rate_num(value) == expected


def test_a_percent_rate_lets_a_commission_line_prove_itself():
    """The three terms are on the page; the parser used not to see one of them.

    17,509.70 at 10% is 1,750.97 exactly. Before the rate parser read the sign
    this document reported `not_provable`, and the canonical export refused it.
    """
    record = {
        "lines": [
            {
                "commissionable_amount": "17509.70",
                "stated_commission_rate": "10%",
                "commission_amount": "1750.97",
            }
        ]
    }
    assert arithmetic_check.commission_rate_unit(record, 0.01) == "percent"
    lines, failed = arithmetic_check.check_commission_lines(record, 0.01, "percent")
    assert failed == 0
    assert len(lines) == 1


def test_a_percent_sign_is_not_read_as_evidence_about_the_unit():
    """The sign is an annotation. Only the arithmetic settles percent vs fraction."""
    record = {
        "lines": [
            {
                "commissionable_amount": "1000.00",
                "stated_commission_rate": "0%",
                "commission_amount": "0.00",
            }
        ]
    }
    assert arithmetic_check.commission_rate_unit(record, 0.01) is None


@pytest.mark.parametrize("status", ["auto_accepted", "sampled_verified", "exception_resolved"])
def test_the_gate_files_no_item_against_a_review_clear_status(status):
    """The queue and the export must agree about what "clear" means.

    They did not. The queue filed an item against every status but
    `auto_accepted`, and the canonical export then refused the document for
    having an open item -- refusing it for having been reviewed. No document
    could reach canonical as `sampled_verified` or `exception_resolved`.
    """
    queue = importlib.import_module("client_review.queue")
    items = queue.review_items({"documents": [{"document_id": "doc-1", "review_status": status}]})
    assert [item for item in items if str(item["reason"]).startswith("document_status")] == []


def test_the_gate_still_files_an_item_against_an_open_status():
    queue = importlib.import_module("client_review.queue")
    items = queue.review_items(
        {"documents": [{"document_id": "doc-1", "review_status": "open_exception"}]}
    )
    assert items[0]["reason"] == "document_status_open_exception"


def test_the_party_role_list_covers_the_schema():
    """A party field the schema carries must not sit outside this control.

    The list had fallen twelve fields behind and omitted `customer_name` and
    `dealer_name`, so the corpus that names its counterparty on every line
    resolved almost nothing.
    """
    extraction_schema = importlib.import_module("extraction_schema")
    records = entity_resolve.load_records(
        [{"document_id": "d", "customer_name": "Acme", "dealer_name": "Beta"}]
    )
    covered = {m["field"] for m in entity_resolve.extract_parties(records)[0]}
    assert {"customer_name", "dealer_name"} <= covered
    named = {
        name
        for name in (*extraction_schema.HEADER_FIELDS, *extraction_schema.LINE_FIELDS)
        if name.endswith("_name")
    }
    # People and product brands are excluded on purpose; everything else is a party.
    people = {"contact_name", "sales_representative_name"}
    # A project is not a party either. `project_name` holds the job a line was
    # sold into -- `One Bayfront Suite 540` -- and resolving those would give a
    # building its own CRM account. Having no field of its own is exactly how it
    # reached `dealer_name` on 476 lines in the first place.
    not_a_party = people | {"project_name"}
    role_fields = {field for field, _, _ in entity_resolve.PARTY_ROLE_FIELDS}
    assert named - not_a_party <= role_fields


def test_a_party_named_only_on_a_line_is_resolved():
    """A commission statement names its customer per line and never in the header."""
    records = [
        {
            "document_id": "d",
            "header": {},
            "lines": [{"customer_name": "Acme Inc"}, {"customer_name": "ACME, INC."}],
        }
    ]
    mentions, _ = entity_resolve.extract_parties(records)
    assert [m["scope"] for m in mentions] == ["lines[0]", "lines[1]"]
    groups, evidence, _ = entity_resolve.cluster(mentions, 0.86, 0.72)
    assert len(groups) == 1 and evidence


def test_a_truncated_reading_never_becomes_the_canonical_name():
    """The cut-off marker is evidence about the reading, not about the party."""
    assert entity_resolve.is_truncated("CORPORATE QUARTERS...")
    assert not entity_resolve.is_truncated("CORPORATE QUARTERS INC.")
    variants = ["MERROW OFFICE...", "MERROW OFFICE...", "Merrow Office Products"]
    assert entity_resolve.choose_survivor(variants) == "Merrow Office Products"
    # When every reading is cut off there is nothing better to choose.
    assert entity_resolve.choose_survivor(["DAX INSPIRING..."]) == "DAX INSPIRING..."


def test_addresses_postcodes_and_branches_still_decide_a_merge():
    """Distinct readings still go through the full pairwise comparison.

    Uniting identical readings first must not bypass the evidence that decides
    a real merge -- a shared address, a shared postal code, a branch marker --
    nor the case where a third pair finds both readings already joined.
    """

    def mention(name, index):
        return {
            "raw_name": name,
            "raw_address": "1 Main Street, Boston MA 02110",
            "role": "biller",
            "document_id": f"d{index}",
            "field": "seller_name",
        }

    mentions = [
        mention("Acme Industries North Branch", 0),
        mention("Acme Industries West Branch", 1),
        mention("Acme Industries East Branch", 2),
    ]
    groups, evidence, _ = entity_resolve.cluster(mentions, 0.6, 0.5)
    assert len(groups) == 1, "three branches of one company are one party"
    assert sum(len(members) for members in groups.values()) == 3
    merged = [item for item in evidence if item["decision"] == "merged"]
    # All three pairs are compared: the last finds them already joined, which
    # must leave the group intact rather than splitting it.
    assert len(merged) == 3
    assert all(item["same_postal"] for item in merged)
    assert any(item["address_score"] == 1 for item in merged)
    assert all(item["reason"] == "branch_variant_of_same_party" for item in merged)


def test_a_name_the_page_cut_off_joins_the_name_it_cuts():
    """Similarity cannot rescue a name with a third of it missing.

    `holden` and `holdens business environments` are one party, and they
    score nowhere near the merge threshold. Containment is the evidence:
    a strict prefix that stops inside a word was cut off.
    """

    def mention(name, index):
        return {
            "raw_name": name,
            "raw_address": None,
            "role": "customer",
            "document_id": f"d{index}",
            "field": "customer_name",
        }

    mentions = [mention("Holden", 0), mention("Holdens Business Environments", 1)]
    groups, evidence, _ = entity_resolve.cluster(mentions, 0.86, 0.72)
    assert len(groups) == 1
    assert any(
        item["reason"] == "name_cut_off_mid_word_and_completed_by_one_other" for item in evidence
    )


def test_a_cut_name_with_two_possible_completions_is_left_alone():
    """Choosing between two completions is a question, not an answer."""
    assert entity_resolve.completed_by({"acme co", "acme cost", "acme cottage"}) == {}
    # A prefix that stops at a word break is a different name, not a cut one.
    assert entity_resolve.completed_by({"ponte", "ponte verra"}) == {}
    # Too short to carry the evidence.
    assert entity_resolve.completed_by({"abc", "abcdef"}) == {}
    assert entity_resolve.completed_by({"wb latha", "wb latham"}) == {"wb latha": "wb latham"}


def test_a_cut_name_goes_to_the_company_not_the_branch_its_label_names():
    """A cut stops inside its word; it carries no evidence for a branch label.

    On the commission run `cornerwise design service` went to the Jacksonville
    branch although `cornerwise design services` was printed on its own.
    """
    names = {
        "cornerwise design service",
        "cornerwise design services",
        "cornerwise design services jacksonville",
    }
    bases = {"cornerwise design services jacksonville": "cornerwise design services"}
    assert entity_resolve.completed_by(names, bases) == {
        "cornerwise design service": "cornerwise design services"
    }
    # Without the label's evidence the longest completion stands, as before.
    assert entity_resolve.completed_by(names) == {
        "cornerwise design service": "cornerwise design services jacksonville"
    }
    # A base never printed on its own is no candidate, so the branch completes it.
    only_branch = {"merrow off", "merrow office products haileah fl"}
    assert entity_resolve.completed_by(
        only_branch, {"merrow office products haileah fl": "merrow office products"}
    ) == {"merrow off": "merrow office products haileah fl"}
    assert entity_resolve.branch_base("Cornerwise Design Services-Jacksonville") == (
        "cornerwise design services"
    )
    assert entity_resolve.branch_base("Office Quarters Inc - NY") == "office quarters"
    assert entity_resolve.branch_base("Holdens Business Environments") is None


def test_clustering_puts_a_cut_name_with_the_company_and_keeps_the_branch_apart():
    def mention(name, index):
        return {
            "raw_name": name,
            "raw_address": None,
            "role": "dealer",
            "document_id": f"d{index}",
            "field": "dealer_name",
        }

    mentions = [
        mention("CORNERWISE DESIGN SERVICE", 0),
        mention("CORNERWISE DESIGN SERVICES", 1),
        mention("Cornerwise Design Services-Jacksonville", 2),
    ]
    groups, evidence, _ = entity_resolve.cluster(mentions, 0.99, 0.98)
    together = sorted(
        sorted(mentions[i]["raw_name"] for i in members) for members in groups.values()
    )
    assert together == [
        ["CORNERWISE DESIGN SERVICE", "CORNERWISE DESIGN SERVICES"],
        ["Cornerwise Design Services-Jacksonville"],
    ]
    assert any(
        (item["a"], item["b"]) == ("CORNERWISE DESIGN SERVICE", "CORNERWISE DESIGN SERVICES")
        for item in evidence
    )
