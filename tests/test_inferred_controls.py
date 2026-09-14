import importlib
import json
import sys

import pytest

inferred = importlib.import_module("inferred_controls")


def write_json(path, value):
    path.write_text(json.dumps(value))
    return path


def records():
    return [
        {
            "document_id": "d1",
            "header": {
                "invoice_date": {"value": "2026-01-15"},
                "total_amount": {"value": "10.00"},
                "seller_name": {"value": "Acme"},
                "acknowledgement_number": {"value": "ACK-1"},
                "payment_amount": {"value": "4"},
                "payment_number": {"value": "CHK-1"},
            },
        },
        {
            "document_id": "d2",
            "invoice_date": "02/01/2026",
            "amount": "20",
            "vendor_name": "Beta",
            "job_number": "JOB-2",
            "payment_number": "CHK-2",
        },
        {"document_id": "d3", "document_date": "bad", "total_amount": "3"},
        {"document_id": "d4", "invoice_date": "2026-03-01", "acknowledgement_number": "ACK-4"},
    ]


def test_month_and_load_shapes(tmp_path):
    assert inferred.month_of("2026-01-02") == "2026-01"
    assert inferred.month_of("2026/02/02") == "2026-02"
    assert inferred.month_of("03/04/2026") == "2026-03"
    assert inferred.month_of("04-05-2026") == "2026-04"
    assert inferred.month_of("2026-13-01") is None
    assert inferred.month_of(None) is None
    path = write_json(tmp_path / "records.json", {"documents": records()})
    assert len(inferred.load_records(path)) == 4
    for key in ("results", "records", "attributions"):
        assert inferred.load_records(write_json(tmp_path / f"{key}.json", {key: []})) == []
    assert (
        inferred.load_records(write_json(tmp_path / "list.json", records()))[0]["document_id"]
        == "d1"
    )
    with pytest.raises(ValueError, match="must contain"):
        inferred.load_records(write_json(tmp_path / "bad.json", {"x": 1}))
    with pytest.raises(ValueError, match="must contain"):
        inferred.load_records(write_json(tmp_path / "bad-again.json", {"documents": "not-a-list"}))
    with pytest.raises(ValueError, match="must contain"):
        inferred.load_records(write_json(tmp_path / "scalar.json", "not-an-envelope"))
    with pytest.raises(ValueError, match="JSON objects"):
        inferred.load_records(write_json(tmp_path / "bad-record.json", {"documents": ["bad"]}))


def test_inference_lanes_cover_evidence_and_exceptions():
    attribution, attribution_exceptions = inferred.infer_attribution(records())
    assert [row["reference_key"] for row in attribution] == ["ACK-1", "JOB-2"]
    assert attribution_exceptions[0]["reason"] == "no_explicit_attribution_key"
    gl, gl_exceptions = inferred.infer_gl_rows(records())
    assert {row["period"] for row in gl} == {"2026-01", "2026-02"}
    assert gl_exceptions[0]["reason"] == "missing_or_invalid_date"
    assert any(x["reason"] == "missing_amount" for x in gl_exceptions)
    payments, payment_exceptions = inferred.infer_payments(records())
    assert payments[0]["payment_number"] == "CHK-1"
    assert any(x["reason"] == "payment_reference_without_amount" for x in payment_exceptions)
    assert any(x["reason"] == "no_explicit_payment_evidence" for x in payment_exceptions)
    assert any(x["reason"] == "missing_amount" for x in attribution_exceptions)


def test_run_writes_hash_bound_no_clobber_bundle(tmp_path, capsys):
    source = write_json(tmp_path / "input.json", {"documents": records()})
    out = tmp_path / "out"
    inferred.run(source, out)
    assert {p.name for p in out.iterdir()} == {
        "attributed.json",
        "gl.csv",
        "payments.json",
        "exceptions.json",
        "manifest.json",
    }
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["proposal_only"] is True
    assert manifest["authoritative"] is False
    assert manifest["completeness_gate"] == "blocked_inferred_controls_not_authoritative"
    assert manifest["artifacts"]["gl.csv"]["sha256"] == inferred._sha256(out / "gl.csv")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        inferred.run(source, out)
    monkey = [str(inferred.__file__), str(source), "--out-dir", str(tmp_path / "cli"), "--quiet"]
    old = sys.argv
    try:
        sys.argv = monkey
        inferred.main()
    finally:
        sys.argv = old
    assert capsys.readouterr().out == ""


def test_main_reports_errors(monkeypatch, tmp_path):
    output = tmp_path / "out"
    monkeypatch.setattr(
        sys,
        "argv",
        ["inferred_controls.py", str(tmp_path / "missing"), "--out-dir", str(output)],
    )
    with pytest.raises(SystemExit, match="No such file"):
        inferred.main()
    assert not output.exists()


def test_main_prints_summary(monkeypatch, tmp_path, capsys):
    source = write_json(tmp_path / "input.json", {"documents": records()})
    monkeypatch.setattr(
        sys,
        "argv",
        ["inferred_controls.py", str(source), "--out-dir", str(tmp_path / "out")],
    )
    inferred.main()
    assert "completeness gate remains blocked" in capsys.readouterr().out


def test_every_attribution_key_is_a_field_the_schema_can_actually_produce():
    """A key the schema cannot express is a key this lane can never find.

    The list led with `ack_number`, which the extraction schema has never
    defined -- its field is `acknowledgement_number`. A 716-page commission
    corpus printing "ACK NO" and "ACK#" on almost every page produced
    `no_explicit_attribution_key` for 715 of 716 documents, so the one lane that
    exists for a client with no general ledger returned nothing at all.
    """
    import extraction_schema

    declared = set(extraction_schema.HEADER_FIELDS) | set(extraction_schema.LINE_FIELDS)
    unknown = [field for field in inferred.ATTRIBUTION_KEY_FIELDS if field not in declared]
    assert unknown == [], unknown
    assert "acknowledgement_number" in inferred.ATTRIBUTION_KEY_FIELDS


def test_an_acknowledgement_number_attributes_the_document():
    """The corpus's own primary identifier must reach the attribution proposal."""
    attribution, exceptions = inferred.infer_attribution(
        [
            {
                "document_id": "d1",
                "header": {
                    "acknowledgement_number": {"value": "142437"},
                    "total_amount": {"value": "1250.00"},
                },
            }
        ]
    )
    assert exceptions == []
    assert attribution[0]["reference_key"] == "142437"
    assert attribution[0]["amount"] == "1250.00"
    # Self-attested: the document is the only thing vouching for this key.
    assert attribution[0]["client_reference_verified"] is False
    assert attribution[0]["proposal_only"] is True


def test_each_controlled_identifier_can_attribute_a_document():
    """Every key in the list is reachable, not just the first one."""
    for field in inferred.ATTRIBUTION_KEY_FIELDS:
        attribution, exceptions = inferred.infer_attribution(
            [
                {
                    "document_id": f"doc-{field}",
                    "header": {field: {"value": "KEY-1"}, "total_amount": {"value": "5.00"}},
                }
            ]
        )
        assert exceptions == [], field
        assert attribution[0]["reference_key"] == "KEY-1", field
