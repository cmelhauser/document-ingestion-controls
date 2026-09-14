"""End-to-end control-layer tests using representative, disposable records."""

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from contract_helpers import write_consensus_handoff

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


arithmetic_check = importlib.import_module("arithmetic_check")
attribution = importlib.import_module("attribution")
completeness = importlib.import_module("completeness")
consensus = importlib.import_module("consensus")
entity_resolve = importlib.import_module("entity_resolve")
ingest_pages = importlib.import_module("ingest_pages")
sampling = importlib.import_module("sampling")
scan_profile = importlib.import_module("scan_profile")


def write_json(path, value):
    path.write_text(json.dumps(value))
    return path


def invoke(monkeypatch, module, *args):
    monkeypatch.setattr(sys, "argv", [module.__file__, *map(str, args)])
    module.main()


def test_arithmetic_cli_reports_every_disposition(monkeypatch, tmp_path, capsys):
    records = [
        {
            "document_id": "ok",
            "header": {"subtotal": "20", "tax_amount": "2", "total_amount": "22"},
            "lines": [
                {
                    "line_number": {"value": "1"},
                    "quantity": "2",
                    "unit_price": "10",
                    "extended_amount": "20",
                }
            ],
        },
        {
            "document_id": "bad",
            "header": {"subtotal": "20", "total_amount": "25"},
            "lines": [
                {"line_number": "x", "quantity": "2", "unit_price": "10", "extended_amount": "21"}
            ],
            "amendments": [{"target_line": 1, "target_column": "quantity", "amended_value": "3"}],
        },
        {"document_id": "unknown", "header": {}, "lines": []},
    ]
    inp, out = write_json(tmp_path / "in.json", records), tmp_path / "out.json"
    invoke(monkeypatch, arithmetic_check, inp, "--out", out)
    result = json.loads(out.read_text())
    assert {x["arithmetic_status"] for x in result["documents"]} == {
        "proved",
        "failed",
        "not_provable",
    }
    assert "Documents:" in capsys.readouterr().out
    filtered = tmp_path / "filtered.json"
    invoke(monkeypatch, arithmetic_check, inp, "--out", filtered, "--failures-only", "--quiet")
    assert len(json.loads(filtered.read_text())["documents"]) == 2


def test_consensus_cli_captures_exceptions_and_missing_fields(monkeypatch, tmp_path, capsys):
    a = write_consensus_handoff(
        tmp_path / "engine-a.json",
        [
            {
                "document_id": "d1",
                "total_amount": {"value": "10", "confidence": 0.9},
                "buyer_name": "Acme",
            },
            {"document_id": "solo", "total_amount": 5},
        ],
        "openai",
        "model-a",
        "consensus_primary",
    )
    b = write_consensus_handoff(
        tmp_path / "engine-b.json",
        [
            {
                "document_id": "d1",
                "total_amount": {"value": "11", "confidence": 0.8},
                "buyer_name": "ACME",
                "extra": "seen",
            },
        ],
        "google",
        "model-b",
        "consensus_secondary",
    )
    out, exc = tmp_path / "out.json", tmp_path / "exceptions.json"
    invoke(monkeypatch, consensus, a, b, "--out", out, "--exceptions", exc)
    data = json.loads(out.read_text())
    assert data["summary"]["single_engine_documents"] == 1
    assert json.loads(exc.read_text())["summary"]["count"] >= 1
    assert capsys.readouterr().err == ""
    bad = write_consensus_handoff(
        tmp_path / "bad-engine.json",
        [{"value": 99}],
        "openai",
        "model-a",
        "consensus_primary",
    )
    with pytest.raises(SystemExit, match="refusing to drop"):
        invoke(monkeypatch, consensus, bad, "--out", tmp_path / "bad-out.json")


def test_attribution_cli_runs_all_resolution_paths(monkeypatch, tmp_path, capsys):
    ref = write_json(
        tmp_path / "ref.json",
        {
            "acks": [
                {
                    "ack_number": "ACK-1",
                    "date": "2026-01-01",
                    "customer": "Acme",
                    "amount": "10",
                    "po_number": "PO-1",
                    "selling_location": "Boston",
                },
                {"ack_number": "ACK-2", "date": "2026-01-03", "customer": "Beta", "amount": "20"},
                {"ack_number": "ACK-3", "date": "2026-01-03", "customer": "Beta", "amount": "20"},
            ]
        },
    )
    dispositions = write_json(tmp_path / "dispositions.json", {"disposed": "overhead"})
    records = [
        {
            "document_id": "explicit",
            "document_type": "invoice",
            "ack_number": "ACK-1",
            "total_amount": 10,
        },
        {"document_id": "po", "document_type": "invoice", "po_number": "PO-1", "total_amount": 10},
        {
            "document_id": "shipment",
            "document_type": "invoice",
            "bol_number": "B1",
            "ack_number": "ACK-1",
            "total_amount": 10,
        },
        {
            "document_id": "inherits",
            "document_type": "invoice",
            "bol_number": "B1",
            "total_amount": 10,
        },
        {
            "document_id": "ambiguous",
            "document_type": "invoice",
            "buyer_name": "Beta",
            "invoice_date": "2026-01-03",
            "total_amount": 20,
        },
        {
            "document_id": "disposed",
            "document_type": "invoice",
            "total_amount": 2,
            "salesperson": "Sam",
        },
        {
            "document_id": "rejected",
            "document_type": "invoice",
            "ack_number": "NOT-VALID",
            "total_amount": 3,
        },
    ]
    inp, out, register = (
        write_json(tmp_path / "input.json", {"documents": records}),
        tmp_path / "out.json",
        tmp_path / "register.json",
    )
    invoke(
        monkeypatch,
        attribution,
        inp,
        "--reference",
        ref,
        "--dispositions",
        dispositions,
        "--out",
        out,
        "--register",
        register,
    )
    data = json.loads(out.read_text())
    assert data["summary"]["documents"] == len(records)
    assert data["summary"]["gate_status"] == "blocked"
    assert any(x["reason_code"] == "overhead" for x in json.loads(register.read_text())["register"])
    assert "method mix" in capsys.readouterr().out


def test_attribution_cli_reports_a_key_field_rank_one_never_reads(monkeypatch, tmp_path, capsys):
    """A blind rank and an absent key are identical in the attribution rate.

    Rank 1 spent a full corpus asking for `ack_number`, which the schema does
    not emit -- it writes `acknowledgement_number` -- and 111 documents that
    printed their key were read correctly and then registered as unresolved.
    Nothing in the artifact said so, which is what this reports.
    """
    records = [
        {
            "document_id": "blind",
            "document_type": "invoice",
            # Key-shaped, populated, and in no rank's list.
            "header": {"order_number": {"value": "SO-77"}, "total_amount": {"value": 5}},
        },
        {
            "document_id": "seen",
            "document_type": "invoice",
            "header": {"acknowledgement_number": {"value": "137783"}, "total_amount": {"value": 5}},
        },
        {
            "document_id": "not-a-key",
            "document_type": "invoice",
            # A PO is read by rank 3, a customer name by nothing; neither is a
            # field rank 1 was "not told to read", and reporting them would make
            # the finding about the design rather than about a gap.
            "header": {"purchase_order_number": {"value": "PO-9"}, "customer_name": "Acme"},
        },
        {
            "document_id": "empty-key",
            "document_type": "invoice",
            # Key-shaped and outside every rank's list, but stating nothing. An
            # empty cell is not a key rank 1 failed to read.
            "header": {"order_number": {"value": ""}},
        },
        {"document_id": "headerless", "document_type": "invoice", "header": "not a mapping"},
    ]
    inp, out, register = (
        write_json(tmp_path / "input.json", {"documents": records}),
        tmp_path / "out.json",
        tmp_path / "register.json",
    )
    invoke(monkeypatch, attribution, inp, "--out", out, "--register", register)
    capsys.readouterr()
    summary = json.loads(out.read_text())["summary"]
    assert summary["unconsulted_key_fields"] == {"order_number": 1}
    assert summary["explicit_key_fields"] == list(attribution.EXPLICIT_KEY_FIELDS)
    assert summary["attribution_by_method"]["explicit_printed"] == 1
    assert any("order_number (1)" in finding for finding in summary["findings"])


def test_attribution_cli_key_field_override_replaces_the_default_order(
    monkeypatch, tmp_path, capsys
):
    """An engagement whose key has another name must not need a code change."""
    records = [
        {
            "document_id": "d",
            "document_type": "invoice",
            "header": {
                "acknowledgement_number": {"value": "137783"},
                "contract_number": {"value": "C-1"},
                "total_amount": {"value": 5},
            },
        }
    ]
    inp, out, register = (
        write_json(tmp_path / "input.json", {"documents": records}),
        tmp_path / "out.json",
        tmp_path / "register.json",
    )
    invoke(
        monkeypatch,
        attribution,
        inp,
        "--key-field",
        "contract_number",
        "--out",
        out,
        "--register",
        register,
    )
    capsys.readouterr()
    summary = json.loads(out.read_text())["summary"]
    assert summary["explicit_key_fields"] == ["contract_number"]
    # The acknowledgement number is now a field no rank reads, and is reported.
    assert summary["unconsulted_key_fields"] == {"acknowledgement_number": 1}
    assert json.loads(out.read_text())["attributions"][0]["ack_number"] == "C-1"


def test_completeness_cli_generates_all_controls(monkeypatch, tmp_path, capsys):
    records = []
    for n in (1, 2, 4, 5, 6):
        records.append(
            {
                "document_id": f"v{n}",
                "document_type": "invoice",
                "seller_name": "Vendor",
                "invoice_number": f"INV-{n:03d}",
                "invoice_date": f"2026-0{n}-01",
                "total_amount": 100,
                "payment_status": "paid" if n == 1 else "",
            }
        )
    records.append(
        {
            "document_id": "open",
            "document_type": "invoice",
            "seller_name": "Other",
            "invoice_number": "O-1",
            "invoice_date": "2026-06-01",
            "total_amount": 50,
        }
    )
    gl = write_json(
        tmp_path / "gl.json", {"entries": [{"period": "2026-01", "vendor": "Vendor", "amount": 90}]}
    )
    payments = write_json(
        tmp_path / "payments.json", {"payments": [{"invoice_number": "INV-002", "amount": 100}]}
    )
    inp, out = write_json(tmp_path / "input.json", {"results": records}), tmp_path / "out.json"
    invoke(monkeypatch, completeness, inp, "--gl", gl, "--payments", payments, "--out", out)
    report = json.loads(out.read_text())
    assert report["gate_status"] == "blocked"
    assert report["aging_closure"]["unresolved"] >= 1
    assert "Gate status:" in capsys.readouterr().out


def test_entity_cli_clusters_and_emits_review_queue(monkeypatch, tmp_path, capsys):
    records = [
        {
            "document_id": "one",
            "document_type": "invoice",
            "seller_name": "ACME Incorporated",
            "seller_address": "1 Main St, Boston MA 02110",
        },
        {
            "document_id": "two",
            "document_type": "invoice",
            "seller_name": "Acme Inc",
            "seller_address": "1 Main Street Boston MA 02110",
        },
        {
            "document_id": "three",
            "document_type": "invoice",
            "buyer_name": "Other Co",
            "buyer_address": "2 Side Rd, NY 10001",
        },
    ]
    inp, out, queue = (
        write_json(tmp_path / "input.json", {"documents": records}),
        tmp_path / "out.json",
        tmp_path / "queue.json",
    )
    records.append({"document_id": "four", "document_type": "invoice", "vendor_name": "7144"})
    write_json(tmp_path / "input.json", {"documents": records})
    invoke(monkeypatch, entity_resolve, inp, "--out", out, "--log", queue)
    result = json.loads(out.read_text())
    assert result["summary"]["party_mentions"] == 3
    # An account number in a name field never becomes a party, and the refusal
    # is a reported finding rather than a silently shorter list.
    assert result["summary"]["mentions_refused_not_a_name"] == 1
    assert result["refused_not_a_name"] == [
        {
            "raw_name": "7144",
            "role": "biller",
            "document_id": "four",
            "field": "vendor_name",
            "scope": "header",
            "reason": "party_name_carries_no_letter",
        }
    ]
    printed = capsys.readouterr().out
    assert "Mentions:" in printed
    assert "1 mentions were refused as names" in printed
    # And with absorption asked for, a rare one-character variant of a far more
    # common name is merged and the merge is reported rather than silent.
    # The pair that stayed two parties on the real corpus: one character apart,
    # scoring below the merge threshold, sixty-odd mentions against one.
    records += [{"document_id": f"c{i}", "buyer_name": "Lumen Weft"} for i in range(12)]
    records.append({"document_id": "odd", "buyer_name": "Lumen Wefy"})
    write_json(tmp_path / "input.json", {"documents": records})
    invoke(
        monkeypatch,
        entity_resolve,
        inp,
        "--out",
        out,
        "--log",
        queue,
        "--absorb-ocr-variants",
        "10",
    )
    result = json.loads(out.read_text())
    assert result["summary"]["absorb_ocr_variants_ratio"] == 10.0
    assert result["summary"]["ocr_variants_absorbed"] == 1
    assert result["ocr_variants_absorbed"][0]["absorbed_name"] == "Lumen Wefy"
    assert result["ocr_variants_absorbed"][0]["surviving_name"] == "Lumen Weft"
    assert "absorbed into a name one character away" in capsys.readouterr().out


def test_absorption_runs_after_the_decisions_and_never_undoes_one(monkeypatch, tmp_path):
    """Two firms a person kept apart stay two, however lopsided their counts."""
    records = [{"document_id": f"c{i}", "buyer_name": "Lumen Weft"} for i in range(12)]
    records.append({"document_id": "odd", "buyer_name": "Lumen Wefy"})
    inp = write_json(tmp_path / "input.json", {"documents": records})
    decisions = write_json(
        tmp_path / "decisions.json",
        {
            "authorization": "test-authorization",
            "decisions": [
                {
                    "decision": "different_parties",
                    "a": "Lumen Weft",
                    "b": "Lumen Wefy",
                    "evidence": "two firms",
                }
            ],
        },
    )
    out = tmp_path / "out.json"
    invoke(
        monkeypatch,
        entity_resolve,
        inp,
        "--out",
        out,
        "--log",
        tmp_path / "queue.json",
        "--absorb-ocr-variants",
        "10",
        "--party-decisions",
        decisions,
    )
    result = json.loads(out.read_text())
    assert result["summary"]["ocr_variants_absorbed"] == 0
    assert result["summary"]["party_decisions_applied"] == 1
    assert {"Lumen Weft", "Lumen Wefy"} <= {p["canonical_name"] for p in result["parties"]}


def test_sampling_cli_builds_plan_and_accuracy_statement(monkeypatch, tmp_path, capsys):
    records = [
        {
            "document_id": "a",
            "review_status": "auto_accepted",
            "arithmetic_status": "proved",
            "document_type": "invoice",
            "branch": "A",
            "invoice_date": "2026-01-01",
            "total_amount": 100,
        },
        {
            "document_id": "b",
            "review_status": "auto_accepted",
            "arithmetic_status": "proved",
            "document_type": "invoice",
            "branch": "B",
            "has_handwriting": True,
            "invoice_date": "2026-01-02",
            "total_amount": 30,
        },
        {"document_id": "c", "review_status": "failed", "total_amount": 1000},
    ]
    inp, plan = (
        write_json(tmp_path / "input.json", {"attributions": records}),
        tmp_path / "plan.json",
    )
    invoke(
        monkeypatch,
        sampling,
        inp,
        "--materiality",
        "80",
        "--mus-n",
        "2",
        "--attribute-n",
        "5",
        "--out",
        plan,
    )
    assert json.loads(plan.read_text())["population"]["eligible_documents"] == 2
    plan_data = json.loads(plan.read_text())
    findings = write_json(
        tmp_path / "findings.json",
        {
            "schema_version": "sample_review_results_v1",
            "sample_plan_sha256": plan_data["sample_plan_sha256"],
            "outcomes": [
                {
                    "document_id": "b",
                    "recorded_value": 30,
                    "audited_value": 20,
                    "outcome": "reviewed_error",
                },
                {
                    "document_id": "a",
                    "recorded_value": 100,
                    "audited_value": 100,
                    "outcome": "reviewed_correct",
                },
            ],
        },
    )
    statement = tmp_path / "statement.json"
    invoke(
        monkeypatch,
        sampling,
        inp,
        "--materiality",
        "80",
        "--mus-n",
        "2",
        "--attribute-n",
        "5",
        "--findings",
        findings,
        "--out",
        statement,
    )
    accuracy = json.loads(statement.read_text())["accuracy_statement"]
    assert accuracy["status"] == "completed"
    assert accuracy["projected_overstatement"] >= 0
    assert "Eligible:" in capsys.readouterr().out


def test_scan_profiler_cli_handles_real_pdf(monkeypatch, tmp_path, capsys):
    pdf = tmp_path / "blank.pdf"
    doc = scan_profile.pikepdf.Pdf.new()
    doc.add_blank_page(page_size=(72, 72))
    doc.save(pdf)
    out = tmp_path / "profile.json"
    invoke(monkeypatch, scan_profile, pdf, "--shallow", "--out", out)
    result = json.loads(out.read_text())
    assert result["summary"]["total_pages_profiled"] == 1
    assert "Profiled 1 pages" in capsys.readouterr().out


def test_ingestion_splits_real_multipage_pdf_and_queues_uncertainty(monkeypatch, tmp_path, capsys):
    output = tmp_path / "intake"
    invoke(
        monkeypatch, ingest_pages, ROOT / "docs" / "TECHNICAL_DOCUMENTATION.pdf", "--out", output
    )
    manifest = json.loads((output / "ingestion_manifest.json").read_text())
    queue = json.loads((output / "classification_exceptions.json").read_text())
    page_count = len(ingest_pages.pikepdf.open(ROOT / "docs" / "TECHNICAL_DOCUMENTATION.pdf").pages)
    assert manifest["summary"]["pages"] == page_count
    retained_source = output / manifest["summary"]["source_file"]
    assert (
        retained_source.read_bytes() == (ROOT / "docs" / "TECHNICAL_DOCUMENTATION.pdf").read_bytes()
    )
    assert len(manifest["summary"]["source_sha256"]) == 64
    assert all(not Path(page["source_file"]).is_absolute() for page in manifest["pages"])
    assert all(
        page["source_sha256"] == manifest["summary"]["source_sha256"] for page in manifest["pages"]
    )
    assert manifest["summary"]["native_text_pages"] == page_count
    assert manifest["summary"]["unassigned_pages"] == page_count
    page_pdfs = sorted((output / "pages").glob("*.pdf"))
    assert [page.name[:6] for page in page_pdfs] == [f"{i:06d}" for i in range(1, page_count + 1)]
    assert all(len(ingest_pages.pikepdf.open(page).pages) == 1 for page in page_pdfs)
    assert queue["summary"]["count"] == page_count
    assert all(page["document_group_id"] is None for page in manifest["pages"])
    assert [page["output_sort_key"] for page in manifest["pages"]] == [
        f"{i:06d}" for i in range(1, page_count + 1)
    ]
    assert [page["source_page_range"] for page in manifest["pages"]] == [
        str(i) for i in range(1, page_count + 1)
    ]
    assert f"Pages split: {page_count}" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="Output directory must be empty"):
        invoke(
            monkeypatch,
            ingest_pages,
            ROOT / "docs" / "TECHNICAL_DOCUMENTATION.pdf",
            "--out",
            output,
        )


def test_ingestion_rejects_unverified_retained_source(monkeypatch, tmp_path):
    source = tmp_path / "source.pdf"
    pdf = ingest_pages.pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(72, 72))
    pdf.save(source)
    hashes = iter(("source-hash", "different-copy-hash"))
    monkeypatch.setattr(ingest_pages, "sha256", lambda _path: next(hashes))
    output = tmp_path / "intake"
    output.mkdir()
    with pytest.raises(ValueError, match="SHA-256 verification"):
        ingest_pages.split_pdf(source, output)


def test_acceptance_packet_runs_real_pdf_and_control_chain(tmp_path):
    output = tmp_path / "acceptance"
    env = {**os.environ, "PYTHON_BIN": sys.executable}
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "run_acceptance_packet.sh"), str(output)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert "completeness_gate" in result.stdout
    page_count = len(ingest_pages.pikepdf.open(ROOT / "docs" / "TECHNICAL_DOCUMENTATION.pdf").pages)
    native_text_pages = json.loads((output / "instructions_profile.json").read_text())["summary"][
        "native_text_only_pages"
    ]
    assert json.loads((output / "acceptance_summary.json").read_text()) == {
        "native_text_only_pages": native_text_pages,
        "ingested_pages": page_count,
        "ingestion_exception_pages": page_count,
        "consensus_documents": 2,
        "proved_documents": 2,
        "validation_review_items": 0,
        "final_client_review_gate": "blocked_pending_client_review",
        "final_client_review_items": page_count,
        "attribution_gate": "clear",
        "completeness_gate": "clear",
        "eligible_sampling_documents": 2,
    }


def test_mcp_production_acceptance_runs_complete_fictional_query_path(tmp_path):
    output = tmp_path / "mcp-production"
    env = {**os.environ, "PYTHON_BIN": sys.executable}
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "run_mcp_production_acceptance.sh"), str(output)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    summary = json.loads((output / "mcp_production_acceptance_summary.json").read_text())
    assert '"status": "passed"' in result.stdout
    assert summary["status"] == "passed"
    assert summary["gates"] == {
        "canonical_load": "passed",
        "no_send_staging": "passed",
        "retrieval_snapshot": "passed",
        "local_stdio_mcp": "passed",
        "remote_streamable_http_mcp": "passed",
        "crm_csv_xlsx_exports": "passed",
    }
    assert summary["coverage"] == {
        "local_tools": 12,
        "remote_tools": 14,
        "standard_reports": 7,
        "canonical_records": 15,
    }
    assert len(summary["source_export_sha256"]) == 64
    assert len(summary["external_acceptance_still_required"]) == 6


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ("run_retrieval_mcp.sh", "Phase 6 activation gate"),
        ("run_mcp_production_acceptance.sh", "code readiness only"),
    ],
)
def test_mcp_shell_entry_points_have_complete_help(script, expected):
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / script), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Usage:" in result.stdout
    assert expected in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize("script", ["run_retrieval_mcp.sh", "run_mcp_production_acceptance.sh"])
def test_mcp_shell_entry_points_reject_extra_arguments(script):
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / script), "one", "two"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Usage:" in result.stderr


def test_real_document_acceptance_uses_hash_bound_independent_handoffs():
    script = (ROOT / "scripts" / "run_real_document_acceptance.sh").read_text()
    assert '"schema_version": "independent_extraction_handoff_v1"' in script
    assert '"lane": lane' in script
    assert '"independence_group": provider' in script
    assert '"records_sha256"' in script
    assert '"raw_response_sha256"' in script
    assert '"$OUTPUT_DIR/audited_native_text_a_handoff.json"' in script
    assert '"$OUTPUT_DIR/audited_native_text_b_handoff.json"' in script


@pytest.mark.parametrize(
    "module", [arithmetic_check, attribution, completeness, consensus, sampling]
)
def test_json_cli_rejects_empty_inputs(monkeypatch, tmp_path, module):
    inp = write_json(tmp_path / f"{module.__name__}.json", [])
    with pytest.raises(SystemExit):
        invoke(monkeypatch, module, inp)
