"""Tests for the canonical export producer that Phase 6 was missing."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

canonical_export = importlib.import_module("canonical_export")
canonical_load = importlib.import_module("canonical_load")
csv_api_staging = importlib.import_module("csv_api_staging")
retrieval_store = importlib.import_module("retrieval_store")

BATCH = "batch-1"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def field(value, accepted=True):
    return {"value": value, "accepted": accepted}


def manifest(**overrides):
    page = {
        "page_id": "doc-1",
        "document_id": None,
        "source_file": "source/acme.pdf",
        "source_sha256": "a" * 64,
        "source_page_range": "1",
        "classification_status": "rule_classified",
        "document_type": "commercial_invoice",
    }
    page.update(overrides)
    return {"pages": [page]}


def consensus(review_status="auto_accepted", **overrides):
    record = {
        "document_id": "doc-1",
        "review_status": review_status,
        "consensus_flag": "consensus_2of2",
        "document_type": "commercial_invoice",
        "has_handwriting": False,
        "header": {
            "invoice_number": {"value": "INV-1"},
            "invoice_date": {"value": "2026-01-05"},
            "currency": {"value": "USD"},
            "total_amount": {"value": "110.00"},
            "seller_name": {"value": "ACME Supply Co"},
            "buyer_name": {"value": "Northwind Ltd"},
            "payment_terms": {"value": None},
            "statement_number": {"value": "S-9"},
        },
        "lines": [
            {
                "line_number": {"value": 1},
                "description": {"value": "Widget"},
                "quantity": {"value": "2"},
                "extended_amount": {"value": "110.00"},
                "lot_number": {"value": "L-1"},
            }
        ],
        "fields": {
            "document_type": field("commercial_invoice"),
            "header.invoice_number": field("INV-1"),
            "header.invoice_date": field("2026-01-05"),
            "header.currency": field("USD"),
            "header.total_amount": field("110.00"),
            "header.seller_name": field("ACME Supply Co"),
            "header.buyer_name": field("Northwind Ltd"),
            "header.payment_terms": field(None),
            "header.statement_number": field("S-9"),
            "lines[0].line_number": field(1),
            "lines[0].description": field("Widget"),
            "lines[0].quantity": field("2"),
            "lines[0].extended_amount": field("110.00"),
            "lines[0].lot_number": field("L-1"),
            "header.due_date": field("2026-02-05", accepted=False),
        },
    }
    record.update(overrides)
    return {"documents": [record]}


def arithmetic(status="proved"):
    return {"documents": [{"document_id": "doc-1", "arithmetic_status": status}]}


def build(**kwargs):
    arguments = {
        "manifest": manifest(),
        "consensus": consensus(),
        "arithmetic": arithmetic(),
        "queue": {"items": []},
        "batch_id": BATCH,
        "registry_version": None,
    }
    arguments.update(kwargs)
    return canonical_export.build(**arguments)


def test_review_clear_document_becomes_loadable_canonical_rows():
    tables, exceptions, withheld = build()
    assert [row["document_id"] for row in tables["document"]] == ["doc-1"]
    document = tables["document"][0]
    assert document["source_sha256"] == "a" * 64
    assert document["source_page_range"] == "1"
    assert document["review_status"] == "auto_accepted"
    assert document["arithmetic_status"] == "proved"
    assert {row["canonical_name"] for row in tables["party"]} == {
        "ACME Supply Co",
        "Northwind Ltd",
    }
    header = tables["invoice_header"][0]
    assert header["invoice_number"] == "INV-1"
    # The proof belongs to the arithmetic control; reading it off the consensus
    # record labelled a proved invoice "not_reported", which is the column an
    # analyst filters on to get verified figures.
    assert header["arithmetic_status"] == "proved"
    assert header["currency_code"] == "USD"
    assert header["total_amount"] == "110.00"
    assert header["biller_party_key"] == next(
        row["party_key"] for row in tables["party"] if row["canonical_name"] == "ACME Supply Co"
    )
    # payment_terms agreed on an empty value and due_date was never accepted, so
    # neither becomes a column asserting something the document does not say.
    assert "payment_terms" not in header
    assert "due_date" not in header
    assert tables["invoice_line"][0]["description"] == "Widget"
    assert tables["invoice_line"][0]["invoice_key"] == header["invoice_key"]
    assert [row["document_type"] for row in tables["document_type"]] == ["commercial_invoice"]
    assert [row["currency_code"] for row in tables["currency"]] == ["USD"]
    # statement_number and lot_number agreed but no canonical column takes them.
    assert withheld == 2
    unmapped = [
        item for item in exceptions if item["reason"] == "no_canonical_column_for_accepted_field"
    ]
    assert "header.statement_number" in unmapped[0]["detail"]

    # The rows this command emits are exactly the rows the next command accepts.
    plan = canonical_load.build_plan(
        {"batch_id": BATCH, "tables": tables, "registry_version": None}
    )
    assert plan["total_records"] == 7
    assert plan["findings"] == []


def test_keys_are_stable_across_re_export():
    first, _, _ = build()
    second, _, _ = build()
    assert first["invoice_header"][0]["invoice_key"] == second["invoice_header"][0]["invoice_key"]
    assert first["party"][0]["party_key"] == second["party"][0]["party_key"]


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    (
        ({"consensus": consensus("open_exception")}, "document_review_status_not_clear"),
        ({"arithmetic": arithmetic("failed")}, "arithmetic_failed"),
        ({"arithmetic": arithmetic("not_provable")}, "arithmetic_not_provable"),
        ({"arithmetic": {}}, "arithmetic_not_reported"),
        ({"manifest": {"pages": []}}, "document_provenance_missing"),
        ({"manifest": manifest(source_sha256="")}, "document_provenance_incomplete"),
        ({"queue": {"items": [{"document_id": "doc-1"}]}}, "open_final_review_item"),
    ),
)
def test_every_gate_withholds_the_document_and_says_which_control_did(kwargs, reason):
    tables, exceptions, _ = build(**kwargs)
    assert tables["document"] == []
    assert [item["reason"] for item in exceptions] == [reason]
    assert exceptions[0]["disposition"] == "client_review_required"


def test_document_without_a_type_is_withheld():
    tables, exceptions, _ = build(
        manifest=manifest(document_type=None),
        consensus=consensus(document_type=None, fields={}),
    )
    assert tables["document"] == []
    assert exceptions[0]["reason"] == "document_type_not_established"


def test_an_accepted_classification_types_a_document_extraction_left_unknown():
    """Extraction writes unknown where its lanes disagreed; the accepted type is used."""
    accepted = {
        "doc-1": {
            "document_type": "commission_statement",
            "evidence": "independent_model_vendor_agreement",
            "resolved_by": "classification_consensus.json",
        }
    }
    tables, _, _ = build(consensus=consensus(document_type="unknown"), classifications=accepted)
    assert [row["document_type"] for row in tables["document"]] == ["commission_statement"]
    assert [row["document_type"] for row in tables["document_type"]] == ["commission_statement"]
    # A type extraction established is kept, even where classification names another.
    tables, _, _ = build(classifications=accepted)
    assert tables["document"][0]["document_type"] == "commercial_invoice"
    # With no accepted type, unknown stays unknown.
    tables, _, _ = build(consensus=consensus(document_type="unknown"))
    assert tables["document"][0]["document_type"] == "unknown"


def test_reassembled_pages_become_one_document_with_a_joined_page_range():
    pages = manifest()["pages"] + [
        {
            "page_id": "page-2",
            "document_id": "doc-1",
            "source_file": "source/acme.pdf",
            "source_sha256": "a" * 64,
            "source_page_range": "2",
            "classification_status": "rule_classified",
            "document_type": "commercial_invoice",
        }
    ]
    tables, _, _ = build(manifest={"pages": pages})
    assert tables["document"][0]["source_page_range"] == "1,2"
    assert tables["document"][0]["page_count"] == 2


def test_layout_without_the_required_invoice_columns_keeps_its_document_row():
    header = {"dealer_name": {"value": "Regional Dealer"}, "statement_date": {"value": "May 2024"}}
    fields = {
        "document_type": field("commission_statement"),
        "header.dealer_name": field("Regional Dealer"),
        "header.statement_date": field("May 2024"),
    }
    tables, exceptions, _ = build(
        consensus=consensus(
            document_type="commission_statement", header=header, lines=[], fields=fields
        ),
        arithmetic=arithmetic("not_applicable"),
    )
    assert len(tables["document"]) == 1
    assert tables["invoice_header"] == []
    assert [row["canonical_name"] for row in tables["party"]] == ["Regional Dealer"]
    reasons = [item["reason"] for item in exceptions]
    assert "canonical_invoice_header_requires_invoice_number" in reasons

    # An invoice number with nobody to bill from is refused for its own reason.
    header["invoice_number"] = {"value": "INV-9"}
    fields["header.invoice_number"] = field("INV-9")
    _, refused, _ = build(
        consensus=consensus(
            document_type="commission_statement", header=header, lines=[], fields=fields
        ),
        arithmetic=arithmetic("not_applicable"),
    )
    assert "canonical_invoice_header_requires_biller_party" in [item["reason"] for item in refused]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"manifest": {}}, "pages list"),
        ({"manifest": {"pages": ["x"]}}, "pages must be objects"),
        ({"manifest": {"pages": [{}]}}, "requires page_id"),
        ({"consensus": {}}, "documents list"),
        ({"consensus": {"documents": ["x"]}}, "must be objects"),
        ({"consensus": {"documents": [{}]}}, "requires document_id"),
        ({"queue": {"items": "x"}}, "items list"),
        ({"consensus": consensus(lines=["x"])}, "lines must be objects"),
    ),
)
def test_malformed_input_is_named_rather_than_skipped(kwargs, message):
    with pytest.raises(ValueError, match=message):
        build(**kwargs)


def test_findings_describe_a_partial_and_a_document_only_batch():
    tables, exceptions, _ = build()
    assert canonical_export.findings_for(tables, [], 1) == []
    partial = canonical_export.findings_for(tables, exceptions, 3)
    assert "2 of 3 documents were withheld" in partial[0]
    document_only = canonical_export.findings_for({**tables, "invoice_header": []}, [], 1)
    assert "document identity and provenance only" in document_only[0]


def test_helpers_refuse_or_ignore_the_shapes_they_cannot_read(tmp_path):
    with pytest.raises(ValueError, match="must be a JSON object"):
        canonical_export.load_object(write(tmp_path / "list.json", ["x"]), "Consensus artifact")
    assert canonical_export.accepted_fields({"fields": "not-a-map"}) == set()
    # An agreed party field whose value is blank names nobody, so it makes no row.
    tables, _, _ = build(
        consensus=consensus(
            header={"seller_name": {"value": "   "}},
            lines=[],
            fields={"document_type": field("commercial_invoice"), "header.seller_name": field(" ")},
        )
    )
    assert tables["party"] == []


def test_invoice_without_a_currency_adds_no_currency_row_and_maps_everything():
    header = {"invoice_number": {"value": "INV-2"}, "seller_name": {"value": "ACME"}}
    tables, exceptions, withheld = build(
        consensus=consensus(
            header=header,
            lines=[],
            fields={
                "document_type": field("commercial_invoice"),
                "header.invoice_number": field("INV-2"),
                "header.seller_name": field("ACME"),
            },
        )
    )
    assert tables["currency"] == []
    assert withheld == 0
    assert exceptions == []
    assert tables["invoice_header"][0]["invoice_number"] == "INV-2"


def test_findings_call_an_empty_export_blocked_rather_than_clear():
    empty = {"document": [], "invoice_header": []}
    findings = canonical_export.findings_for(
        empty, [canonical_export.exclusion("d", "r", "x", "c")], 4
    )
    assert "No document of 4 reached the canonical export" in findings[0]
    assert "1 retained exclusions" in findings[1]


def run_cli(monkeypatch, tmp_path, *extra, **files):
    paths = {
        "manifest": write(tmp_path / "manifest.json", files.get("manifest", manifest())),
        "consensus": write(tmp_path / "consensus.json", files.get("consensus", consensus())),
        "arithmetic": write(tmp_path / "arithmetic.json", files.get("arithmetic", arithmetic())),
        "queue": write(tmp_path / "queue.json", files.get("queue", {"items": []})),
    }
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "canonical_export.py",
            "--manifest", str(paths["manifest"]),
            "--consensus", str(paths["consensus"]),
            "--arithmetic", str(paths["arithmetic"]),
            "--final-review", str(paths["queue"]),
            "--batch-id", BATCH,
            "--out", str(tmp_path / "out" / "export.json"),
            "--exceptions", str(tmp_path / "out" / "export_exceptions.json"),
            *extra,
        ],
    )  # fmt: skip
    canonical_export.main()
    return tmp_path / "out" / "export.json"


def test_cli_writes_a_loadable_export_and_then_refuses_to_overwrite_it(
    tmp_path, monkeypatch, capsys
):
    path = run_cli(monkeypatch, tmp_path, "--registry-version", "registry-2")
    export = json.loads(path.read_text())
    assert export["gate_status"] == "blocked_pending_client_review"
    assert export["registry_version"] == "registry-2"
    assert export["summary"]["exported_documents"] == 1
    assert "Canonical documents exported: 1 of 1" in capsys.readouterr().out

    plan_path = tmp_path / "plan.json"
    monkeypatch.setattr(sys, "argv", ["canonical_load.py", str(path), "--out", str(plan_path)])
    canonical_load.main()
    monkeypatch.setattr(
        sys,
        "argv",
        ["csv_api_staging.py", str(path), str(plan_path), "--out", str(tmp_path / "staging")],
    )
    csv_api_staging.main()
    manifest_json = json.loads((tmp_path / "staging" / "staging_manifest.json").read_text())
    assert manifest_json["records"] == 7
    assert manifest_json["findings"] == []
    monkeypatch.setattr(
        sys,
        "argv",
        ["retrieval_store.py", "build", str(path), "--out", str(tmp_path / "facts.sqlite")],
    )
    retrieval_store.main()
    assert (tmp_path / "facts.sqlite").is_file()

    with pytest.raises(SystemExit, match="Output already exists"):
        run_cli(monkeypatch, tmp_path)


def test_cli_reports_a_fully_withheld_run_without_claiming_a_clear_gate(
    tmp_path, monkeypatch, capsys
):
    path = run_cli(monkeypatch, tmp_path, "--quiet", consensus=consensus("open_exception"))
    export = json.loads(path.read_text())
    assert export["gate_status"] == "blocked_pending_client_review"
    assert export["tables"]["document"] == []
    assert capsys.readouterr().out == ""
    exceptions = json.loads((tmp_path / "out" / "export_exceptions.json").read_text())
    assert exceptions["summary"]["count"] == 1

    # The empty export still plans and stages, and both say they moved nothing.
    plan_path = tmp_path / "plan.json"
    monkeypatch.setattr(sys, "argv", ["canonical_load.py", str(path), "--out", str(plan_path)])
    canonical_load.main()
    assert "orders 0 rows" in json.loads(plan_path.read_text())["findings"][0]
    monkeypatch.setattr(
        sys,
        "argv",
        ["csv_api_staging.py", str(path), str(plan_path), "--out", str(tmp_path / "staging")],
    )
    csv_api_staging.main()
    staged = json.loads((tmp_path / "staging" / "staging_manifest.json").read_text())
    assert "stages 0 rows" in staged["findings"][0]


def test_cli_reads_accepted_types_and_refuses_another_artifact(tmp_path, monkeypatch):
    """The command takes the classification consensus, and nothing else, for types."""
    accepted = write(
        tmp_path / "classification_consensus.json",
        {
            "artifact_type": "classification_consensus_v1",
            "accepted": [{"document_id": "doc-1", "document_type": "commission_statement"}],
        },
    )
    path = run_cli(
        monkeypatch,
        tmp_path,
        "--quiet",
        "--classifications",
        str(accepted),
        consensus=consensus(document_type="unknown"),
    )
    assert json.loads(path.read_text())["tables"]["document"][0]["document_type"] == (
        "commission_statement"
    )
    other = write(tmp_path / "other.json", {"artifact_type": "something_else"})
    with pytest.raises(SystemExit, match="Canonical export failed: not a resolution artifact"):
        run_cli(monkeypatch, tmp_path / "second", "--classifications", str(other))


def test_cli_refuses_a_blank_batch_and_an_unreadable_input(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="--batch-id must be non-empty"):
        run_cli(monkeypatch, tmp_path, "--batch-id", "  ")
    with pytest.raises(SystemExit, match="Canonical export failed"):
        run_cli(monkeypatch, tmp_path, consensus={"documents": "x"})


def test_cli_reports_an_unwritable_output_directory(tmp_path, monkeypatch):
    (tmp_path / "out").write_text("not a directory")
    with pytest.raises(SystemExit, match="Canonical export failed"):
        run_cli(monkeypatch, tmp_path)
