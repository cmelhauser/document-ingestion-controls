"""Tests for the optional cross-record client-review evidence pass."""

import copy
import hashlib
import importlib
import json
import sys
from pathlib import Path

import pikepdf
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

cross = importlib.import_module("client_review.cross_record")
ingest = importlib.import_module("ingest_pages")
operations = importlib.import_module("operations")
reviewer = importlib.import_module("client_review.llm")

SOURCE_RECORD_ID = "source__p0002"
INTAKE_SOURCE_FILE = "source/source.pdf"


def write(path, value):
    path.write_text(json.dumps(value))
    return path


def queue():
    return {
        "items": [
            {"document_id": "doc-1", "field": "document_type", "reason": "formatting_review"},
            {"document_id": "doc-1", "field": "total", "reason": "financial_total"},
        ],
        "review_cards": [{"card_id": "document:doc-1", "item_indexes": [0, 1]}],
    }


def proposal():
    return {
        "field": "document_type",
        "original_value": "unknown",
        "proposed_value": "commercial_invoice",
        "update_type": "correction",
        "evidence": f"{INTAKE_SOURCE_FILE}#page=2#field=document_type",
        "rationale": "The retained classification is exact",
    }


def evidence_record():
    return {
        "record_id": SOURCE_RECORD_ID,
        "field": "document_type",
        "value": "commercial_invoice",
        "evidence": f"{INTAKE_SOURCE_FILE}#page=2#field=document_type",
        "evidence_kind": "source_evidence",
        "source_sha256": "a" * 64,
        "source_page_range": "2",
    }


def bound_evidence_entry():
    return {
        "record_id": SOURCE_RECORD_ID,
        "field": "document_type",
        "value": "commercial_invoice",
        "evidence": f"{INTAKE_SOURCE_FILE}#page=2#field=document_type",
        "source_sha256": "replaced-by-helper",
        "source_page_range": "2",
    }


def write_source_pdf(path, pages=2):
    document = pikepdf.Pdf.new()
    for _index in range(pages):
        document.add_blank_page(page_size=(72, 72))
    document.save(path)
    return path


def write_text_source_pdf(
    path, texts=("supporting page", "invoice number INV-2 invoice date 2026-08-20")
):
    document = pikepdf.Pdf.new()
    for text in texts:
        page = document.add_blank_page(page_size=(612, 792))
        font = document.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Font"),
                Subtype=pikepdf.Name("/Type1"),
                BaseFont=pikepdf.Name("/Helvetica"),
            )
        )
        page.obj["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        page.obj["/Contents"] = document.make_stream(
            f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        )
    document.save(path)
    return path


def artifact_ref(path):
    return {
        "path": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def write_run_manifest(root, paths):
    return write(root / "run_manifest.json", operations.artifact_manifest(paths))


def rebind_producer_artifacts(context, *artifacts):
    envelope = json.loads(context.read_text())
    producer = envelope["producer"]
    operations_path = context.parent / producer["operations_manifest"]["path"]
    run_manifest = json.loads(operations_path.read_text())
    for artifact in artifacts:
        artifact = Path(artifact)
        matches = [item for item in run_manifest["artifacts"] if item["name"] == artifact.name]
        assert len(matches) == 1
        matches[0]["bytes"] = artifact.stat().st_size
        matches[0]["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if artifact.name == producer["path"]:
            producer["sha256"] = matches[0]["sha256"]
        for value in producer.values():
            if isinstance(value, dict) and value.get("path") == artifact.name:
                value["sha256"] = matches[0]["sha256"]
    operations_path.write_text(json.dumps(run_manifest))
    producer["operations_manifest"]["sha256"] = hashlib.sha256(
        operations_path.read_bytes()
    ).hexdigest()
    context.write_text(json.dumps(envelope))


def intake_evidence_envelope(tmp_path, entries=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source.pdf"
    write_text_source_pdf(source)
    intake_dir = tmp_path / "intake"
    intake_dir.mkdir()
    manifest_value, exceptions_value = ingest.split_pdf(source, intake_dir)
    manifest = write(intake_dir / "ingestion_manifest.json", manifest_value)
    exceptions = write(intake_dir / "classification_exceptions.json", exceptions_value)
    retained_source = intake_dir / manifest_value["summary"]["source_file"]
    run_paths = [manifest, exceptions, retained_source]
    for page in manifest_value["pages"]:
        run_paths.append(intake_dir / page["page_pdf"])
        if page["text_file"]:
            run_paths.append(intake_dir / page["text_file"])
    run_manifest = write_run_manifest(intake_dir, run_paths)
    source_page = manifest_value["pages"][1]
    source_sha256 = source_page["source_sha256"]
    entry = {
        **bound_evidence_entry(),
        "record_id": source_page["page_id"],
        "evidence": (f"{source_page['source_file']}#page=2#field=document_type"),
        "source_sha256": source_sha256,
    }
    envelope = {
        "schema_version": "cross_record_evidence_v1",
        "producer": {
            "kind": "intake_manifest",
            "schema_version": "intake_evidence_producer_v1",
            "path": manifest.name,
            "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "operations_manifest": artifact_ref(run_manifest),
            "classification_exceptions": artifact_ref(exceptions),
        },
        "entries": entries if entries is not None else [entry],
    }
    return write(intake_dir / "evidence-envelope.json", envelope), entry


def canonical_evidence_envelope(tmp_path, review_status="sampled_verified"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "canonical-source.pdf"
    write_source_pdf(source)
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    canonical_export = write(
        tmp_path / "canonical-export.json",
        {
            "batch_id": "batch-1",
            "tables": {
                "document": [
                    {
                        "document_id": SOURCE_RECORD_ID,
                        "batch_id": "batch-1",
                        "review_status": review_status,
                        "source_file": source.name,
                        "source_sha256": source_sha256,
                        "source_page_range": "2",
                        "document_type": "commercial_invoice",
                    }
                ]
            },
        },
    )
    load_plan = write(
        tmp_path / "canonical-load-plan.json",
        cross.build_canonical_plan(cross.load_canonical_export(canonical_export)),
    )
    final_review = write(
        tmp_path / "final-client-review.json",
        {
            "summary": {
                "schema_version": "1.0",
                "generated_at": "2026-08-20T12:00:00+00:00",
                "source_artifacts": [canonical_export.name],
                "client_review_items": 0,
                "client_review_cards": 0,
                "gate_status": "clear",
                "findings": ["Exhaustive supplied review artifacts are clear."],
            },
            "items": [],
            "review_cards": [],
        },
    )
    run_manifest = write_run_manifest(tmp_path, [canonical_export, load_plan, final_review, source])
    entry = {
        **bound_evidence_entry(),
        "evidence": f"{source.name}#page=2#field=document_type",
        "source_sha256": source_sha256,
    }
    envelope = {
        "schema_version": "cross_record_evidence_v1",
        "producer": {
            "kind": "canonical_export",
            "schema_version": "canonical_evidence_producer_v1",
            "path": canonical_export.name,
            "sha256": hashlib.sha256(canonical_export.read_bytes()).hexdigest(),
            "operations_manifest": artifact_ref(run_manifest),
            "load_plan": artifact_ref(load_plan),
            "final_review": artifact_ref(final_review),
        },
        "entries": [entry],
    }
    return write(tmp_path / "canonical-evidence-envelope.json", envelope), entry


@pytest.mark.parametrize(
    ("page_range", "expected"),
    (("1", True), ("1-2", True), ("3", False), ("1-2-3", False), ("2-1", False)),
)
def test_source_page_binding_requires_an_existing_ordered_pdf_range(tmp_path, page_range, expected):
    source = write_source_pdf(tmp_path / "source.pdf")
    assert cross._source_page_exists(source, page_range) is expected

    invalid_pdf = tmp_path / "invalid.pdf"
    invalid_pdf.write_bytes(b"not a retained PDF")
    assert cross._source_page_exists(invalid_pdf, "1") is False


class Response:
    def __init__(self, value):
        self.output_text = json.dumps(value)

    def model_dump(self, mode="json"):
        return {"output": self.output_text, "mode": mode}


class Responses:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def create(self, **kwargs):
        del kwargs
        self.calls += 1
        value = self.outcomes.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class Client:
    def __init__(self, outcomes):
        self.responses = Responses(outcomes)


def decision():
    return {
        "rationale": "The same value appears on another retained record.",
        "matches": [
            {
                "review_item_id": "review-item-0",
                "source_record_id": SOURCE_RECORD_ID,
                "source_field": "document_type",
                "decision": "propose_resolution",
                "confidence": 0.995,
                "proposed_update": proposal(),
                "rationale": "Independent record evidence",
            },
            {
                "review_item_id": "review-item-1",
                "source_record_id": SOURCE_RECORD_ID,
                "source_field": "total",
                "decision": "propose_resolution",
                "confidence": 1.0,
                "proposed_update": proposal(),
                "rationale": "Should remain protected",
            },
        ],
    }


def test_packet_and_normalization_boundaries(tmp_path):
    q = queue()
    q["review_cards"].append({"card_id": "not-selected", "item_indexes": [99]})
    packet = cross.global_packet(q, [{"artifact": "all.json"}])
    assert len(packet["review_items"]) == 2
    context, _entry = intake_evidence_envelope(tmp_path)
    index = cross.build_retained_evidence_index(q, [context])
    accepted, remaining = cross.normalize_matches(decision()["matches"], q, index, 0.99, True)
    assert len(accepted) == 1
    assert len(remaining) == 1
    assert remaining[0]["retained_review_reason"] == "protected_review_item"
    mismatched = {
        **decision()["matches"][0],
        "proposed_update": {**proposal(), "field": "other"},
    }
    accepted, remaining = cross.normalize_matches([mismatched], q, index, 0.99, True)
    assert accepted == []
    assert remaining[0]["retained_review_reason"] == "invalid_proposed_update"
    assert cross.global_packet(q, [], [0])["review_cards"]
    with pytest.raises(ValueError, match="packet bytes"):
        cross.batch_item_indexes(q, [], 0, 1)
    with pytest.raises(ValueError, match="items per batch"):
        cross.batch_item_indexes(q, [], 1, 0)
    assert cross.batch_item_indexes({"items": [], "review_cards": []}, [], 1, 1) == []


def test_normalization_requires_exact_unique_retained_evidence(tmp_path):
    q = queue()
    context, _entry = intake_evidence_envelope(tmp_path)
    index = cross.build_retained_evidence_index(q, [context])
    exact = decision()["matches"][0]

    accepted, remaining = cross.normalize_matches([exact], q, index, 0.99, False)
    assert accepted == []
    assert remaining[0]["retained_review_reason"] == "auto_accept_disabled"

    accepted, remaining = cross.normalize_matches([exact], q, index, 0.99, True)
    assert remaining == []
    assert accepted[0]["auto_accept_status"] == "auto_accepted"
    assert accepted[0]["proposal_only"] is True
    assert accepted[0]["resolved_evidence_reference"]["json_path"] == "$.entries[0]"

    unknown_queue = queue()
    unknown_queue["items"][0]["reason"] = "brand_new_unclassified_reason"
    accepted, remaining = cross.normalize_matches([exact], unknown_queue, index, 0.99, True)
    assert accepted == []
    assert remaining[0]["retained_review_reason"] == "protected_review_item"

    cases = (
        ({**exact, "source_record_id": "does-not-exist"}, "source_record_not_found"),
        ({**exact, "source_field": "invalid_field"}, "source_field_not_found"),
        (
            {
                **exact,
                "proposed_update": {**proposal(), "evidence": "invented free text"},
            },
            "retained_evidence_not_found",
        ),
        (
            {
                **exact,
                "proposed_update": {**proposal(), "proposed_value": "invented value"},
            },
            "retained_evidence_not_found",
        ),
        ({**exact, "source_record_id": ""}, "malformed_source_reference"),
        ({**exact, "review_item_id": "review-item-99"}, "unknown_review_item"),
    )
    for match, reason in cases:
        accepted, remaining = cross.normalize_matches([match], q, index, 0.99, True)
        assert accepted == []
        assert remaining[0]["retained_review_reason"] == reason

    accepted, remaining = cross.normalize_matches(
        ["malformed-provider-match"], q, index, 0.99, True
    )
    assert accepted == []
    assert remaining == [
        {
            "provider_match": "malformed-provider-match",
            "proposal_only": True,
            "auto_accept_status": "disabled",
            "retained_review_reason": "malformed_match",
        }
    ]

    ambiguous_context, valid_entry = intake_evidence_envelope(tmp_path / "ambiguous")
    ambiguous_envelope = json.loads(ambiguous_context.read_text())
    ambiguous_envelope["entries"] = [valid_entry, valid_entry]
    ambiguous_context.write_text(json.dumps(ambiguous_envelope))
    ambiguous = cross.build_retained_evidence_index(q, [ambiguous_context])
    accepted, remaining = cross.normalize_matches([exact], q, ambiguous, 0.99, True)
    assert accepted == []
    assert remaining[0]["retained_review_reason"] == "ambiguous_retained_evidence"


def test_evidence_index_excludes_untrusted_proposals_and_reports_reasons(tmp_path):
    context, valid_entry = intake_evidence_envelope(tmp_path / "context")
    raw_proposal = {
        **valid_entry,
        "proposed_value": "commercial_invoice",
        "status": "proposal_only",
    }
    unresolved = {
        **valid_entry,
        "review_status": "open_exception",
    }
    provider_update = {
        **valid_entry,
        "proposed_update": {"field": "document_type"},
    }
    envelope = json.loads(context.read_text())
    envelope["entries"] = [valid_entry, raw_proposal, unresolved, provider_update]
    context.write_text(json.dumps(envelope))

    index = cross.build_retained_evidence_index(queue(), [context])
    reasons = {entry["eligibility_reason"] for entry in index["ineligible_context"]}
    assert reasons == {
        "proposal_only_context",
        "unresolved_review_context",
        "provider_update_context",
    }
    assert index["context_artifacts"][0]["eligible_evidence"] == 1
    assert index["context_artifacts"][0]["ineligible_evidence"] == 3

    accepted, remaining = cross.normalize_matches(
        [decision()["matches"][0]], queue(), index, 0.99, True
    )
    assert remaining == []
    reference = accepted[0]["resolved_evidence_reference"]
    assert reference["artifact_sha256"]
    assert reference["source_sha256"] == valid_entry["source_sha256"]
    assert reference["source_page_range"] == "2"

    proposal_only_context, _entry = intake_evidence_envelope(
        tmp_path / "proposal-only", [raw_proposal]
    )
    proposal_index = cross.build_retained_evidence_index(queue(), [proposal_only_context])
    accepted, remaining = cross.normalize_matches(
        [decision()["matches"][0]], queue(), proposal_index, 0.99, True
    )
    assert accepted == []
    assert remaining[0]["retained_review_reason"] == "source_record_not_found"
    assert proposal_index["ineligible_context"][0]["eligibility_reason"] == (
        "proposal_only_context"
    )


def test_evidence_index_requires_a_hash_bound_recognized_producer(tmp_path):
    bound_context, bound_entry = intake_evidence_envelope(tmp_path)
    bound_index = cross.build_retained_evidence_index(queue(), [bound_context])
    bound_match = {
        **decision()["matches"][0],
        "proposed_update": {
            **proposal(),
            "evidence": bound_entry["evidence"],
        },
    }
    accepted, remaining = cross.normalize_matches([bound_match], queue(), bound_index, 0.99, True)
    assert remaining == []
    assert len(accepted) == 1
    assert accepted[0]["resolved_evidence_reference"]["producer_kind"] == "intake_manifest"

    self_attested = write(tmp_path / "self-attested.json", {"records": [evidence_record()]})
    unbound_index = cross.build_retained_evidence_index(queue(), [self_attested])
    accepted, remaining = cross.normalize_matches(
        [decision()["matches"][0]], queue(), unbound_index, 0.99, True
    )
    assert accepted == []
    assert remaining[0]["retained_review_reason"] == "source_record_not_found"
    assert unbound_index["context_artifacts"][0]["eligible_evidence"] == 0
    assert unbound_index["context_artifacts"][0]["ineligible_evidence"] == 1
    assert unbound_index["ineligible_context"][0]["eligibility_reason"] == (
        "invalid_evidence_envelope"
    )


def test_blank_pdf_and_minimal_intake_manifest_cannot_mint_semantic_evidence(tmp_path):
    source = write_source_pdf(tmp_path / "source.pdf", pages=1)
    page_pdf = write_source_pdf(tmp_path / "page.pdf", pages=1)
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = write(
        tmp_path / "ingestion_manifest.json",
        {
            "summary": {
                "source_file": source.name,
                "source_sha256": source_sha256,
                "pages": 1,
            },
            "pages": [
                {
                    "page_id": "doc-2",
                    "page_pdf": page_pdf.name,
                    "source_file": source.name,
                    "source_sha256": source_sha256,
                    "source_page_range": "1",
                    "document_type": "commercial_invoice",
                }
            ],
        },
    )
    exceptions = write(
        tmp_path / "classification_exceptions.json",
        {"summary": {"count": 0}, "exceptions": []},
    )
    run_manifest = write_run_manifest(tmp_path, [manifest, exceptions, source, page_pdf])
    entry = {
        **bound_evidence_entry(),
        "record_id": "doc-2",
        "source_sha256": source_sha256,
        "source_page_range": "1",
        "evidence": "source.pdf#page=1#field=document_type",
    }
    context = write(
        tmp_path / "evidence-envelope.json",
        {
            "schema_version": "cross_record_evidence_v1",
            "producer": {
                "kind": "intake_manifest",
                "schema_version": "intake_evidence_producer_v1",
                "path": manifest.name,
                "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "operations_manifest": artifact_ref(run_manifest),
                "classification_exceptions": artifact_ref(exceptions),
            },
            "entries": [entry],
        },
    )

    index = cross.build_retained_evidence_index(queue(), [context])

    assert index["context_artifacts"][0]["eligible_evidence"] == 0
    assert index["ineligible_context"][0]["eligibility_reason"] == (
        "unrecognized_producer_artifact"
    )


def test_unsupported_extra_intake_page_field_cannot_qualify(tmp_path):
    context, _entry = intake_evidence_envelope(tmp_path)
    envelope = json.loads(context.read_text())
    producer_path = context.parent / envelope["producer"]["path"]
    manifest = json.loads(producer_path.read_text())
    manifest["pages"][0]["invoice_number"] = "FAKE-999"
    producer_path.write_text(json.dumps(manifest))
    envelope["entries"][0].update(
        field="invoice_number",
        value="FAKE-999",
        evidence=f"{INTAKE_SOURCE_FILE}#page=2#field=invoice_number",
    )
    context.write_text(json.dumps(envelope))
    rebind_producer_artifacts(context, producer_path)

    index = cross.build_retained_evidence_index(queue(), [context])

    assert index["context_artifacts"][0]["eligible_evidence"] == 0
    assert index["ineligible_context"][0]["eligibility_reason"] == (
        "unsupported_intake_manifest_fields"
    )


def test_extra_canonical_document_field_cannot_qualify(tmp_path):
    context, _entry = canonical_evidence_envelope(tmp_path)
    envelope = json.loads(context.read_text())
    producer_path = context.parent / envelope["producer"]["path"]
    export = json.loads(producer_path.read_text())
    export["tables"]["document"][0]["invented_fact"] = "FAKE-999"
    producer_path.write_text(json.dumps(export))
    envelope["entries"][0].update(
        field="invented_fact",
        value="FAKE-999",
        evidence="canonical-source.pdf#page=2#field=invented_fact",
    )
    context.write_text(json.dumps(envelope))
    rebind_producer_artifacts(context, producer_path)

    index = cross.build_retained_evidence_index(queue(), [context])

    assert index["context_artifacts"][0]["eligible_evidence"] == 0
    assert index["ineligible_context"][0]["eligibility_reason"] == (
        "unsupported_canonical_document_fields"
    )


def test_review_clear_fact_requires_a_validated_hash_bound_canonical_producer(tmp_path):
    context, entry = canonical_evidence_envelope(tmp_path / "clear")
    index = cross.build_retained_evidence_index(queue(), [context])
    match = {
        **decision()["matches"][0],
        "proposed_update": {**proposal(), "evidence": entry["evidence"]},
    }
    accepted, remaining = cross.normalize_matches([match], queue(), index, 0.99, True)
    assert remaining == []
    assert accepted[0]["resolved_evidence_reference"]["producer_kind"] == "canonical_export"

    open_context, _entry = canonical_evidence_envelope(tmp_path / "open")
    open_envelope = json.loads(open_context.read_text())
    final_review_path = open_context.parent / open_envelope["producer"]["final_review"]["path"]
    final_review = json.loads(final_review_path.read_text())
    final_review["items"] = [
        {
            "priority": "normal",
            "document_id": SOURCE_RECORD_ID,
            "page_id": "",
            "region_id": "",
            "field": "document_type",
            "reason": "unresolved_evidence",
            "review_source": "canonical_export",
            "disposition": "client_review_required",
        }
    ]
    final_review["summary"].update(
        client_review_items=1,
        gate_status="blocked_pending_client_review",
    )
    final_review_path.write_text(json.dumps(final_review))
    rebind_producer_artifacts(open_context, final_review_path)
    open_index = cross.build_retained_evidence_index(queue(), [open_context])
    assert open_index["context_artifacts"][0]["eligible_evidence"] == 0
    assert open_index["ineligible_context"][0]["eligibility_reason"] == (
        "canonical_final_review_not_clear"
    )


def test_evidence_envelope_reports_producer_reference_failures(tmp_path):
    cases = (
        ("not-object", lambda envelope: envelope.update(producer=[]), "invalid_producer_reference"),
        (
            "extra-key",
            lambda envelope: envelope["producer"].update(extra="x"),
            "invalid_producer_reference",
        ),
        (
            "unsupported-kind",
            lambda envelope: envelope["producer"].update(kind="raw_provider_response"),
            "unsupported_producer_kind",
        ),
        (
            "missing-path",
            lambda envelope: envelope["producer"].update(path="missing.json"),
            "invalid_producer_reference",
        ),
        (
            "absolute-path",
            lambda envelope: envelope["producer"].update(path=str(tmp_path / "outside.json")),
            "invalid_producer_reference",
        ),
        (
            "escape-path",
            lambda envelope: envelope["producer"].update(path="../outside.json"),
            "invalid_producer_reference",
        ),
        (
            "hash-mismatch",
            lambda envelope: envelope["producer"].update(sha256="0" * 64),
            "producer_hash_mismatch",
        ),
    )
    for name, mutate, expected_reason in cases:
        context, _entry = intake_evidence_envelope(tmp_path / name)
        envelope = json.loads(context.read_text())
        mutate(envelope)
        context.write_text(json.dumps(envelope))
        index = cross.build_retained_evidence_index(queue(), [context])
        assert index["context_artifacts"][0]["eligible_evidence"] == 0
        assert index["ineligible_context"][0]["eligibility_reason"] == expected_reason

    context, _entry = intake_evidence_envelope(tmp_path / "unrecognized")
    envelope = json.loads(context.read_text())
    producer_path = context.parent / envelope["producer"]["path"]
    producer_path.write_text(json.dumps({"summary": {}, "pages": [{"page_id": SOURCE_RECORD_ID}]}))
    envelope["producer"]["sha256"] = hashlib.sha256(producer_path.read_bytes()).hexdigest()
    context.write_text(json.dumps(envelope))
    index = cross.build_retained_evidence_index(queue(), [context])
    assert index["ineligible_context"][0]["eligibility_reason"] == (
        "unrecognized_producer_artifact"
    )


def test_evidence_envelope_reports_every_producer_binding_failure(tmp_path):
    def context_with(name, *, entry_update=None, manifest_update=None, source_update=None):
        context, entry = intake_evidence_envelope(tmp_path / name)
        envelope = json.loads(context.read_text())
        if entry_update is not None:
            envelope["entries"][0].update(entry_update)
        producer_path = context.parent / envelope["producer"]["path"]
        manifest = json.loads(producer_path.read_text())
        if manifest_update is not None:
            manifest_update(manifest)
            producer_path.write_text(json.dumps(manifest))
            envelope["producer"]["sha256"] = hashlib.sha256(producer_path.read_bytes()).hexdigest()
        if source_update is not None:
            source_update(context.parent / INTAKE_SOURCE_FILE)
        context.write_text(json.dumps(envelope))
        return context, entry

    cases = (
        (
            "record-not-found",
            {"record_id": "absent"},
            None,
            None,
            "producer_record_not_found",
        ),
        (
            "value-mismatch",
            {"value": "packing_list"},
            None,
            None,
            "producer_value_mismatch",
        ),
        (
            "provenance-mismatch",
            {"source_page_range": "999"},
            None,
            None,
            "producer_provenance_mismatch",
        ),
        (
            "source-hash-mismatch",
            None,
            None,
            lambda source: source.write_bytes(b"changed after manifest"),
            "source_artifact_hash_mismatch",
        ),
        (
            "evidence-mismatch",
            {"evidence": "invented locator"},
            None,
            None,
            "producer_evidence_mismatch",
        ),
    )
    for name, entry_update, manifest_update, source_update, expected_reason in cases:
        context, _entry = context_with(
            name,
            entry_update=entry_update,
            manifest_update=manifest_update,
            source_update=source_update,
        )
        index = cross.build_retained_evidence_index(queue(), [context])
        assert index["context_artifacts"][0]["eligible_evidence"] == 0
        assert index["ineligible_context"][0]["eligibility_reason"] == expected_reason


def test_declared_collection_reports_every_missing_or_invalid_field(tmp_path):
    _context, valid = intake_evidence_envelope(tmp_path)
    entries = []
    for field_value in ("omitted", None, 7):
        entry = dict(valid)
        if field_value == "omitted":
            entry.pop("field")
        else:
            entry["field"] = field_value
        entries.append(entry)
    context, _entry = intake_evidence_envelope(tmp_path / "malformed", entries)

    index = cross.build_retained_evidence_index(queue(), [context])

    assert index["context_artifacts"][0]["eligible_evidence"] == 0
    assert index["context_artifacts"][0]["ineligible_evidence"] == 3
    assert [entry["eligibility_reason"] for entry in index["ineligible_context"]] == [
        "missing_context_field",
        "missing_context_field",
        "missing_context_field",
    ]


def test_repeated_entries_read_and_open_retained_source_once(tmp_path, monkeypatch):
    context, entry = intake_evidence_envelope(tmp_path)
    envelope = json.loads(context.read_text())
    envelope["entries"] = [dict(entry) for _index in range(50)]
    context.write_text(json.dumps(envelope))
    source = (context.parent / INTAKE_SOURCE_FILE).resolve()
    hash_calls = 0
    pdf_open_calls = 0
    original_sha256 = cross._sha256
    original_pdf_open = cross.pikepdf.open

    def counted_sha256(path, cache=None):
        nonlocal hash_calls
        if Path(path).resolve() == source:
            hash_calls += 1
        return original_sha256(path, cache)

    def counted_pdf_open(path, *args, **kwargs):
        nonlocal pdf_open_calls
        if Path(path).resolve() == source:
            pdf_open_calls += 1
        return original_pdf_open(path, *args, **kwargs)

    monkeypatch.setattr(cross, "_sha256", counted_sha256)
    monkeypatch.setattr(cross.pikepdf, "open", counted_pdf_open)

    index = cross.build_retained_evidence_index(queue(), [context])

    assert index["context_artifacts"][0]["eligible_evidence"] == 50
    assert hash_calls == 1
    assert pdf_open_calls == 1


@pytest.mark.parametrize(
    ("limit_name", "limit_value", "expected_reason"),
    (
        (
            "max_evidence_artifact_bytes",
            1,
            "cross_record_evidence_artifact_bytes_limit_exceeded",
        ),
        (
            "max_evidence_entries",
            1,
            "cross_record_evidence_entries_limit_exceeded",
        ),
    ),
)
def test_evidence_preflight_limits_fail_without_provider_call(
    tmp_path, limit_name, limit_value, expected_reason
):
    queue_path = write(tmp_path / "queue.json", queue())
    context, entry = intake_evidence_envelope(tmp_path / "evidence")
    if limit_name == "max_evidence_entries":
        envelope = json.loads(context.read_text())
        envelope["entries"] = [entry, dict(entry)]
        context.write_text(json.dumps(envelope))
    client = Client([Response(decision())])

    summary = cross.run_cross_record(
        queue_path,
        [context],
        tmp_path / f"{limit_name}-out.json",
        tmp_path / f"{limit_name}-exceptions.json",
        tmp_path / f"{limit_name}-raw",
        "model",
        client,
        **{limit_name: limit_value},
    )

    exceptions = json.loads((tmp_path / f"{limit_name}-exceptions.json").read_text())["exceptions"]
    assert summary["analysis_status"] == "failed"
    assert exceptions[0]["reason"] == expected_reason
    assert client.responses.calls == 0


def test_aggregate_entry_limit_preflights_every_envelope_before_loading_producers(
    tmp_path, monkeypatch
):
    first, _entry = intake_evidence_envelope(tmp_path / "first")
    second, _entry = intake_evidence_envelope(tmp_path / "second")
    producer_loads = 0
    original_load_producer = cross._load_producer

    def counted_load_producer(*args, **kwargs):
        nonlocal producer_loads
        producer_loads += 1
        return original_load_producer(*args, **kwargs)

    monkeypatch.setattr(cross, "_load_producer", counted_load_producer)

    with pytest.raises(
        cross.EvidencePreflightError,
        match="cross_record_evidence_entries_limit_exceeded",
    ):
        cross.build_retained_evidence_index(queue(), [first, second], max_entries=1)

    assert producer_loads == 0


def test_oversized_retained_page_is_rejected_before_pdf_open(tmp_path, monkeypatch):
    context, _entry = intake_evidence_envelope(tmp_path)
    envelope = json.loads(context.read_text())
    manifest = json.loads((context.parent / envelope["producer"]["path"]).read_text())
    page_pdf = (context.parent / manifest["pages"][0]["page_pdf"]).resolve()
    maximum = max(
        path.stat().st_size
        for path in context.parent.rglob("*")
        if path.is_file() and path.resolve() != page_pdf
    )
    page_pdf.write_bytes(page_pdf.read_bytes() + b"0" * (maximum + 1))
    page_open_calls = 0
    original_pdf_open = cross.pikepdf.open

    def counted_pdf_open(path, *args, **kwargs):
        nonlocal page_open_calls
        if Path(path).resolve() == page_pdf:
            page_open_calls += 1
        return original_pdf_open(path, *args, **kwargs)

    monkeypatch.setattr(cross.pikepdf, "open", counted_pdf_open)

    with pytest.raises(
        cross.EvidencePreflightError,
        match="cross_record_evidence_artifact_bytes_limit_exceeded",
    ):
        cross.build_retained_evidence_index(
            queue(),
            [context],
            max_artifact_bytes=maximum,
        )

    assert page_open_calls == 0


def test_intake_producer_rejects_a_retained_page_with_multiple_pdf_pages(tmp_path):
    context, _entry = intake_evidence_envelope(tmp_path)
    envelope = json.loads(context.read_text())
    manifest = json.loads((context.parent / envelope["producer"]["path"]).read_text())
    exceptions = json.loads(
        (context.parent / envelope["producer"]["classification_exceptions"]["path"]).read_text()
    )
    page_pdf = context.parent / manifest["pages"][0]["page_pdf"]
    write_source_pdf(page_pdf, pages=2)

    with pytest.raises(cross.ProducerValidationError, match="unrecognized_producer_artifact"):
        cross._validate_intake_producer(
            manifest,
            exceptions,
            context.parent,
            {},
            cross.DEFAULT_MAX_EVIDENCE_ARTIFACT_BYTES,
        )


def test_empty_collection_with_invalid_producer_reports_one_machine_reason(tmp_path):
    context, _entry = intake_evidence_envelope(tmp_path)
    envelope = json.loads(context.read_text())
    envelope["producer"]["sha256"] = "0" * 64
    envelope["entries"] = []
    context.write_text(json.dumps(envelope))

    index = cross.build_retained_evidence_index(queue(), [context])

    assert index["context_artifacts"][0]["eligible_evidence"] == 0
    assert index["context_artifacts"][0]["ineligible_evidence"] == 1
    assert index["ineligible_context"] == [
        {
            "retained_context_reference": {
                "artifact": str(context),
                "artifact_sha256": hashlib.sha256(context.read_bytes()).hexdigest(),
                "json_path": "$.producer",
            },
            "eligibility_reason": "producer_hash_mismatch",
            "disposition": "not_eligible_as_cross_record_evidence",
        }
    ]


def test_nonempty_invalid_producer_accounts_for_every_declared_entry(tmp_path):
    context, entry = intake_evidence_envelope(tmp_path)
    envelope = json.loads(context.read_text())
    envelope["producer"]["sha256"] = "0" * 64
    envelope["entries"] = [entry, dict(entry)]
    context.write_text(json.dumps(envelope))

    index = cross.build_retained_evidence_index(queue(), [context])

    assert index["context_artifacts"][0]["eligible_evidence"] == 0
    assert index["context_artifacts"][0]["ineligible_evidence"] == 3
    assert [
        (
            item["retained_context_reference"]["json_path"],
            item["eligibility_reason"],
        )
        for item in index["ineligible_context"]
    ] == [
        ("$.producer", "producer_hash_mismatch"),
        ("$.entries[0]", "producer_not_authenticated"),
        ("$.entries[1]", "producer_not_authenticated"),
    ]


def test_local_preflight_helpers_fail_closed_and_cache(tmp_path, monkeypatch):
    artifact = tmp_path / "artifact.json"
    artifact.write_text("[]")
    cache = {}
    assert cross._sha256(artifact, cache) == cross._sha256(artifact, cache)
    with pytest.raises(cross.ProducerValidationError, match="unrecognized_producer_artifact"):
        cross._load_json_artifact(artifact, 100)
    assert cross._valid_utc_timestamp(None) is False
    assert cross._valid_utc_timestamp("not-a-timestamp") is False
    assert cross._valid_utc_timestamp("2026-08-20T12:00:00") is False

    valid_identity = cross._file_identity(artifact)
    changed_identity = (*valid_identity[:-1], valid_identity[-1] + 1)
    identities = iter((valid_identity, changed_identity))
    monkeypatch.setattr(cross, "_file_identity", lambda _path: next(identities))
    with pytest.raises(cross.ProducerValidationError, match="artifact_changed_during_preflight"):
        cross._sha256(artifact, {})


def test_pdf_cache_and_change_guard(tmp_path, monkeypatch):
    source = write_source_pdf(tmp_path / "source.pdf", pages=1)
    cache = {}
    assert cross._pdf_page_count(source, cache) == 1
    assert cross._pdf_page_count(source, cache) == 1
    valid_identity = cross._file_identity(source)
    changed_identity = (*valid_identity[:-1], valid_identity[-1] + 1)
    identities = iter((valid_identity, changed_identity))
    monkeypatch.setattr(cross, "_file_identity", lambda _path: next(identities))
    with pytest.raises(cross.ProducerValidationError, match="artifact_changed_during_preflight"):
        cross._pdf_page_count(source, {})


@pytest.mark.parametrize(
    ("reference", "expected_reason"),
    (
        ([], "invalid_producer_handoff_reference"),
        ({"path": "missing.json", "sha256": "0" * 64}, "invalid_producer_handoff_reference"),
        ({"path": "artifact.json", "sha256": "0" * 64}, "producer_handoff_hash_mismatch"),
    ),
)
def test_nested_producer_artifact_references_are_exact(tmp_path, reference, expected_reason):
    (tmp_path / "artifact.json").write_text("{}")
    with pytest.raises(cross.ProducerValidationError, match=expected_reason):
        cross._artifact_reference(tmp_path, reference, {}, 100)


def test_operations_manifest_requires_exact_generated_binding(tmp_path):
    artifact = tmp_path / "artifact.json"
    artifact.write_text("{}")
    valid = operations.artifact_manifest([artifact])
    cross._validate_operations_manifest(valid, [artifact], {})
    cases = [
        {**valid, "pipeline_version": "unsupported"},
        {**valid, "effective_runtime_settings": []},
        {**valid, "artifacts": [{**valid["artifacts"][0], "bytes": True}]},
        {**valid, "artifacts": [{**valid["artifacts"][0], "artifact_id": "wrong"}]},
    ]
    for candidate in cases:
        with pytest.raises(cross.ProducerValidationError, match="unrecognized_operations_manifest"):
            cross._validate_operations_manifest(candidate, [artifact], {})
    unbound = operations.artifact_manifest([artifact])
    unbound["artifacts"][0]["sha256"] = "0" * 64
    with pytest.raises(
        cross.ProducerValidationError, match="producer_artifact_not_operations_bound"
    ):
        cross._validate_operations_manifest(unbound, [artifact], {})


def test_intake_producer_rejects_each_generated_state_mismatch(tmp_path, monkeypatch):
    context, _entry = intake_evidence_envelope(tmp_path)
    envelope = json.loads(context.read_text())
    manifest_path = context.parent / envelope["producer"]["path"]
    exceptions_path = context.parent / envelope["producer"]["classification_exceptions"]["path"]
    base = json.loads(manifest_path.read_text())
    base_exceptions = json.loads(exceptions_path.read_text())
    cases = (
        (lambda data, _exc: data.update(extra=True), "unrecognized_producer_artifact"),
        (lambda data, _exc: data.update(pages=[]), "unrecognized_producer_artifact"),
        (
            lambda data, _exc: data["summary"].update(pages=999),
            "unrecognized_producer_artifact",
        ),
        (
            lambda data, _exc: data["summary"].update(generated_at="not-time"),
            "unrecognized_producer_artifact",
        ),
        (
            lambda data, _exc: data["summary"].update(source_file="missing.pdf"),
            "unrecognized_producer_artifact",
        ),
        (
            lambda data, _exc: data["summary"].update(source_sha256="0" * 64),
            "source_artifact_hash_mismatch",
        ),
        (
            lambda data, _exc: (
                data.update(pages=data["pages"][:1]),
                data["summary"].update(pages=1),
            ),
            "source_page_count_mismatch",
        ),
        (
            lambda data, _exc: data["pages"][0].update(reassembly_status="grouped"),
            "unrecognized_producer_artifact",
        ),
        (
            lambda data, _exc: data["pages"][0].update(page_label="invented"),
            "unrecognized_producer_artifact",
        ),
        (
            lambda data, _exc: data["pages"][0].update(document_type="packing_list"),
            "unrecognized_producer_artifact",
        ),
        (
            lambda data, _exc: data["pages"][0].update(text_extraction_status="invented"),
            "intake_semantic_evidence_mismatch",
        ),
        (
            lambda data, _exc: data["pages"][0].update(text_file="text/wrong.txt"),
            "intake_semantic_evidence_mismatch",
        ),
        (
            lambda data, _exc: data["summary"].update(native_text_pages=999),
            "unrecognized_producer_artifact",
        ),
        (
            lambda data, _exc: data["summary"].update(findings=[]),
            "unrecognized_producer_artifact",
        ),
        (
            lambda _data, exc: exc["summary"].update(count=999),
            "unrecognized_classification_exceptions",
        ),
    )
    for mutate, expected_reason in cases:
        data = copy.deepcopy(base)
        exceptions = copy.deepcopy(base_exceptions)
        mutate(data, exceptions)
        with pytest.raises(cross.ProducerValidationError, match=expected_reason):
            cross._validate_intake_producer(
                data,
                exceptions,
                context.parent,
                {},
                cross.DEFAULT_MAX_EVIDENCE_ARTIFACT_BYTES,
            )

    cache = {}
    source = context.parent / INTAKE_SOURCE_FILE
    first = cross._classification_for_page(source, 1, cache)
    assert cross._classification_for_page(source, 1, cache) == first

    original_relative = cross._relative_file
    page_pdf = base["pages"][0]["page_pdf"]
    monkeypatch.setattr(
        cross,
        "_relative_file",
        lambda root, value: None if value == page_pdf else original_relative(root, value),
    )
    with pytest.raises(cross.ProducerValidationError, match="unrecognized_producer_artifact"):
        cross._validate_intake_producer(
            base,
            base_exceptions,
            context.parent,
            {},
            cross.DEFAULT_MAX_EVIDENCE_ARTIFACT_BYTES,
        )


def test_intake_producer_validates_missing_and_exact_text_artifacts(tmp_path, monkeypatch):
    context, _entry = intake_evidence_envelope(tmp_path / "base")
    envelope = json.loads(context.read_text())
    manifest = json.loads((context.parent / envelope["producer"]["path"]).read_text())
    exceptions = json.loads(
        (context.parent / envelope["producer"]["classification_exceptions"]["path"]).read_text()
    )
    original_relative = cross._relative_file
    source_file = manifest["summary"]["source_file"]
    with monkeypatch.context() as scoped:
        scoped.setattr(
            cross,
            "_relative_file",
            lambda root, value: None if value == source_file else original_relative(root, value),
        )
        with pytest.raises(cross.ProducerValidationError, match="source_artifact_hash_mismatch"):
            cross._validate_intake_producer(
                manifest,
                exceptions,
                context.parent,
                {},
                cross.DEFAULT_MAX_EVIDENCE_ARTIFACT_BYTES,
            )

    text_file = manifest["pages"][0]["text_file"]
    with monkeypatch.context() as scoped:
        scoped.setattr(
            cross,
            "_relative_file",
            lambda root, value: None if value == text_file else original_relative(root, value),
        )
        with pytest.raises(
            cross.ProducerValidationError, match="intake_semantic_evidence_mismatch"
        ):
            cross._validate_intake_producer(
                manifest,
                exceptions,
                context.parent,
                {},
                cross.DEFAULT_MAX_EVIDENCE_ARTIFACT_BYTES,
            )
    retained_text = context.parent / text_file
    retained_text.write_text("wrong retained text")
    with pytest.raises(cross.ProducerValidationError, match="intake_semantic_evidence_mismatch"):
        cross._validate_intake_producer(
            manifest,
            exceptions,
            context.parent,
            {},
            cross.DEFAULT_MAX_EVIDENCE_ARTIFACT_BYTES,
        )

    blank_source = write_source_pdf(tmp_path / "blank.pdf", pages=1)
    blank_out = tmp_path / "blank-intake"
    blank_out.mkdir()
    blank_manifest, blank_exceptions = ingest.split_pdf(blank_source, blank_out)
    blank_manifest["pages"][0]["text_file"] = "text/invented.txt"
    with monkeypatch.context() as scoped:
        scoped.setattr(
            cross,
            "_classification_for_page",
            lambda _source, _page, _cache: (
                "",
                "native_text",
                (None, None, "no_extractable_text"),
            ),
        )
        with pytest.raises(
            cross.ProducerValidationError, match="intake_semantic_evidence_mismatch"
        ):
            cross._validate_intake_producer(
                blank_manifest,
                blank_exceptions,
                blank_out,
                {},
                cross.DEFAULT_MAX_EVIDENCE_ARTIFACT_BYTES,
            )
        blank_manifest["pages"][0]["text_file"] = None
        paths, _sources = cross._validate_intake_producer(
            blank_manifest,
            blank_exceptions,
            blank_out,
            {},
            cross.DEFAULT_MAX_EVIDENCE_ARTIFACT_BYTES,
        )
        assert paths


def test_intake_producer_accepts_generated_run_without_exceptions(tmp_path):
    source = write_text_source_pdf(
        tmp_path / "classified.pdf",
        texts=("invoice number INV-2 invoice date 2026-08-20",),
    )
    out = tmp_path / "classified-intake"
    out.mkdir()
    manifest, exceptions = ingest.split_pdf(source, out)
    paths, sources = cross._validate_intake_producer(
        manifest,
        exceptions,
        out,
        {},
        cross.DEFAULT_MAX_EVIDENCE_ARTIFACT_BYTES,
    )
    assert paths and sources


def test_canonical_producer_rejects_each_handoff_mismatch(tmp_path):
    context, _entry = canonical_evidence_envelope(tmp_path)
    envelope = json.loads(context.read_text())
    export_path = context.parent / envelope["producer"]["path"]
    plan_path = context.parent / envelope["producer"]["load_plan"]["path"]
    review_path = context.parent / envelope["producer"]["final_review"]["path"]
    export = json.loads(export_path.read_text())
    plan = json.loads(plan_path.read_text())
    review = json.loads(review_path.read_text())
    cases = (
        ({**export, "extra": True}, plan, review, "unrecognized_producer_artifact"),
        (export, {**plan, "load_mode": "invented"}, review, "canonical_load_plan_mismatch"),
        (export, plan, {**review, "extra": True}, "unrecognized_final_review_artifact"),
        (
            export,
            plan,
            {**review, "summary": {**review["summary"], "schema_version": "2.0"}},
            "unrecognized_final_review_artifact",
        ),
        (
            export,
            plan,
            {
                **review,
                "summary": {**review["summary"], "client_review_items": 1},
            },
            "unrecognized_final_review_artifact",
        ),
    )
    for candidate_export, candidate_plan, candidate_review, expected_reason in cases:
        with pytest.raises(cross.ProducerValidationError, match=expected_reason):
            cross._validate_canonical_producer(
                copy.deepcopy(candidate_export),
                copy.deepcopy(candidate_plan),
                copy.deepcopy(candidate_review),
                export_path,
            )


def test_canonical_source_authentication_failures_are_reported(tmp_path):
    def load_after(name, mutate):
        context, _entry = canonical_evidence_envelope(tmp_path / name)
        envelope = json.loads(context.read_text())
        export_path = context.parent / envelope["producer"]["path"]
        plan_path = context.parent / envelope["producer"]["load_plan"]["path"]
        export = json.loads(export_path.read_text())
        mutate(export)
        export_path.write_text(json.dumps(export))
        plan_path.write_text(json.dumps(cross.build_canonical_plan(export)))
        rebind_producer_artifacts(context, export_path, plan_path)
        return cross.build_retained_evidence_index(queue(), [context])

    cases = (
        (
            "invalid-source-shape",
            lambda export: export["tables"]["document"][0].update(source_sha256="bad"),
            "source_artifact_hash_mismatch",
        ),
        (
            "missing-source",
            lambda export: export["tables"]["document"][0].update(source_file="missing.pdf"),
            "source_artifact_hash_mismatch",
        ),
        (
            "source-hash",
            lambda export: export["tables"]["document"][0].update(source_sha256="0" * 64),
            "source_artifact_hash_mismatch",
        ),
        (
            "source-page",
            lambda export: export["tables"]["document"][0].update(source_page_range="999"),
            "source_page_not_found",
        ),
    )
    for name, mutate, expected_reason in cases:
        index = load_after(name, mutate)
        assert index["ineligible_context"][0]["eligibility_reason"] == expected_reason

    repeated = load_after(
        "repeated-source",
        lambda export: export["tables"]["document"].append(
            {**export["tables"]["document"][0], "document_id": "source-copy"}
        ),
    )
    assert repeated["context_artifacts"][0]["eligible_evidence"] == 1


def test_producer_loader_and_binding_cover_fail_closed_edges(tmp_path, monkeypatch):
    context, entry = intake_evidence_envelope(tmp_path / "intake")
    envelope = json.loads(context.read_text())
    envelope["producer"]["schema_version"] = "unsupported"
    producer, reason = cross._load_producer(
        context,
        envelope["producer"],
        {},
        cross.DEFAULT_MAX_EVIDENCE_ARTIFACT_BYTES,
    )
    assert producer is None and reason == "unsupported_producer_schema_version"

    valid_envelope = json.loads(context.read_text())
    with pytest.raises(cross.EvidencePreflightError):
        cross._load_producer(context, valid_envelope["producer"], {}, 1)

    producer_path = context.parent / valid_envelope["producer"]["path"]
    producer_path.write_text("not-json")
    valid_envelope["producer"]["sha256"] = hashlib.sha256(producer_path.read_bytes()).hexdigest()
    producer, reason = cross._load_producer(
        context,
        valid_envelope["producer"],
        {},
        cross.DEFAULT_MAX_EVIDENCE_ARTIFACT_BYTES,
    )
    assert producer is None and reason == "unrecognized_producer_artifact"

    context, entry = intake_evidence_envelope(tmp_path / "binding")
    producer_envelope = json.loads(context.read_text())
    producer, reason = cross._load_producer(
        context,
        producer_envelope["producer"],
        {},
        cross.DEFAULT_MAX_EVIDENCE_ARTIFACT_BYTES,
    )
    assert reason is None
    source_record = producer["index"][entry["record_id"]][0][1]
    normalized_entry, eligibility_reason = cross._eligible_context_evidence(entry)
    assert eligibility_reason is None
    unsupported = {**normalized_entry, "field": "invoice_number"}
    assert cross._source_binding(unsupported, producer, source_record, "$")[1] == (
        "unsupported_intake_manifest_evidence_field"
    )
    untrusted_record = {**source_record, "classification_status": "open"}
    assert cross._source_binding(normalized_entry, producer, untrusted_record, "$")[1] == (
        "intake_semantic_evidence_not_authoritative"
    )
    producer["authenticated_sources"] = {}
    assert cross._source_binding(normalized_entry, producer, source_record, "$")[1] == (
        "source_artifact_hash_mismatch"
    )
    source_key = (source_record["source_file"], source_record["source_sha256"])
    producer["authenticated_sources"] = {source_key: {"page_count": 0}}
    assert cross._source_binding(normalized_entry, producer, source_record, "$")[1] == (
        "source_page_not_found"
    )
    producer["index"][entry["record_id"]].append((999, source_record))
    assert cross._bind_to_producer(normalized_entry, producer)[1] == "ambiguous_producer_record"
    assert cross._producer_index([None, {}], "record_id") == {}


@pytest.mark.parametrize("value", (True, 0, 1.5))
def test_evidence_preflight_limits_require_positive_integers(value):
    with pytest.raises(ValueError, match="positive integer"):
        cross.build_retained_evidence_index(queue(), [], max_artifact_bytes=value, max_entries=1)


def test_invalid_json_context_is_reported(tmp_path):
    context = tmp_path / "invalid.json"
    context.write_text("not-json")
    index = cross.build_retained_evidence_index(queue(), [context])
    assert index["ineligible_context"][0]["eligibility_reason"] == ("invalid_evidence_envelope")


def test_evidence_index_reports_each_admissibility_failure(tmp_path):
    context_path = tmp_path / "invalid-context"
    context, base = intake_evidence_envelope(context_path)

    def changed(**updates):
        return {**base, **updates}

    records = [
        "not-an-object",
        changed(evidence_kind="source_evidence"),
        changed(approval_status="unapproved"),
        changed(approved=False),
        {key: value for key, value in base.items() if key != "record_id"},
        {
            **{key: value for key, value in base.items() if key != "record_id"},
            "document_id": SOURCE_RECORD_ID,
        },
        changed(field=" "),
        {
            **{key: value for key, value in base.items() if key != "field"},
            "source_field": "document_type",
        },
        changed(value=["not", "scalar"]),
        changed(value=float("nan")),
        changed(evidence=""),
        changed(source_sha256=None),
        changed(source_sha256="a" * 63),
        changed(source_sha256="z" * 64),
        changed(source_page_range=""),
        changed(source_page_range=False),
        changed(source_page_range=0),
        changed(source_page_range=1.5),
        {
            **{key: value for key, value in base.items() if key != "source_page_range"},
            "source_page_number": 2,
        },
        changed(source_page_range=2),
        changed(record_id=f" {SOURCE_RECORD_ID}"),
    ]
    context_data = json.loads(context.read_text())
    context_data["entries"] = records
    context.write_text(json.dumps(context_data))
    index = cross.build_retained_evidence_index(queue(), [context])

    assert [entry["eligibility_reason"] for entry in index["ineligible_context"]] == [
        "malformed_context_entry",
        "unsupported_context_fields",
        "unapproved_context",
        "unapproved_context",
        "missing_context_record_identity",
        "missing_context_record_identity",
        "missing_context_field",
        "missing_context_field",
        "invalid_context_value",
        "invalid_context_value",
        "missing_context_evidence",
        "missing_or_invalid_source_sha256",
        "missing_or_invalid_source_sha256",
        "missing_or_invalid_source_sha256",
        "missing_page_provenance",
        "missing_page_provenance",
        "missing_page_provenance",
        "missing_page_provenance",
        "missing_page_provenance",
        "malformed_context_record_identity",
    ]
    assert index["context_artifacts"][0]["eligible_evidence"] == 1
    only_reference = next(iter(index["exact"].values()))[0]
    assert only_reference["source_page_range"] == "2"


def test_normalization_retains_non_resolution_and_invalid_confidence(tmp_path):
    q = queue()
    nested_context, _entry = intake_evidence_envelope(tmp_path)
    index = cross.build_retained_evidence_index(q, [nested_context])
    exact = decision()["matches"][0]
    cases = (
        ({**exact, "decision": "retain_review"}, "proposal_not_requested"),
        ({**exact, "confidence": "high"}, "invalid_confidence_type"),
        ({**exact, "confidence": 0.989}, "below_auto_accept_threshold"),
    )
    for match, reason in cases:
        accepted, remaining = cross.normalize_matches([match], q, index, 0.99, True)
        assert accepted == []
        assert remaining[0]["retained_review_reason"] == reason

    accepted, remaining = cross.normalize_matches([exact], q, index, 0.99, True)
    assert remaining == []
    assert accepted[0]["resolved_evidence_reference"]["json_path"] == "$.entries[0]"


def test_normalization_requires_cross_record_identity_and_bounded_confidence(tmp_path):
    q = queue()
    context, _entry = intake_evidence_envelope(tmp_path)
    index = cross.build_retained_evidence_index(q, [context])
    exact = decision()["matches"][0]
    cases = (
        ({**exact, "source_record_id": "doc-1"}, "same_record_reference"),
        ({**exact, "confidence": True}, "boolean_confidence"),
        ({**exact, "confidence": 1.01}, "confidence_out_of_range"),
        ({**exact, "confidence": float("nan")}, "non_finite_confidence"),
        ({**exact, "confidence": float("inf")}, "non_finite_confidence"),
    )
    for match, reason in cases:
        accepted, remaining = cross.normalize_matches([match], q, index, 0.99, True)
        assert accepted == []
        assert remaining[0]["retained_review_reason"] == reason

    missing_target = queue()
    missing_target["items"][0].pop("document_id")
    accepted, remaining = cross.normalize_matches([exact], missing_target, index, 0.99, True)
    assert accepted == []
    assert remaining[0]["retained_review_reason"] == "missing_target_record_identity"


def test_normalization_rejects_whitespace_in_target_and_source_identities(tmp_path):
    context, _entry = intake_evidence_envelope(tmp_path)
    index = cross.build_retained_evidence_index(queue(), [context])
    exact = decision()["matches"][0]

    target_whitespace = queue()
    target_whitespace["items"][0]["document_id"] = f"{SOURCE_RECORD_ID} "
    accepted, remaining = cross.normalize_matches([exact], target_whitespace, index, 0.99, True)
    assert accepted == []
    assert remaining[0]["retained_review_reason"] == "malformed_target_record_identity"

    accepted, remaining = cross.normalize_matches(
        [{**exact, "source_record_id": f" {SOURCE_RECORD_ID}"}], queue(), index, 0.99, True
    )
    assert accepted == []
    assert remaining[0]["retained_review_reason"] == "malformed_source_record_identity"
    assert cross.resolve_retained_evidence(
        {"source_record_id": f" {SOURCE_RECORD_ID}", "source_field": "document_type"},
        proposal(),
        index,
    ) == (None, "malformed_source_record_identity")


def test_context_is_fitted_to_packet_budget():
    q = queue()
    context = [{"artifact": "records.json", "sha256": "a" * 64, "json": "x" * 5000}]
    assert cross._context_with_budget([], 10) == []
    assert cross._context_with_budget([{"json": "small"}], 10)[0]["context_truncated"] is False
    fitted, truncated = cross.fit_context_to_packet(q, context, 1000)
    assert truncated is True
    assert cross.packet_size_bytes(cross.global_packet(q, fitted, [0])) <= 1000
    empty, empty_truncated = cross.fit_context_to_packet(q, [], 1)
    assert empty == [] and empty_truncated is False
    with pytest.raises(ValueError, match="even after context omission"):
        cross.fit_context_to_packet(q, context, 1)


def test_cross_record_batches_preserve_global_ids_and_partial_results(tmp_path):
    q = queue()
    q["items"] = [
        {"document_id": f"doc-{index}", "field": "seller_name", "reason": "formatting_review"}
        for index in range(3)
    ]
    q["review_cards"] = [
        {"card_id": "all", "item_indexes": [0, 1, 2]},
    ]
    queue_path = write(tmp_path / "queue.json", q)
    context = write(tmp_path / "context.json", {"records": []})
    batches = cross.batch_item_indexes(
        q, [{"artifact": "context"}], max_packet_bytes=400, max_items_per_batch=1
    )
    assert batches == [[0], [1], [2]]
    assert cross.global_packet(q, [], [2])["review_items"][0]["review_item_id"] == "review-item-2"
    out = tmp_path / "batched.json"
    exc = tmp_path / "batched-exc.json"
    summary = cross.run_cross_record(
        queue_path,
        [context],
        out,
        exc,
        tmp_path / "raw",
        "model",
        Client([Response(decision()), RuntimeError("provider"), Response(decision())]),
        max_context_bytes=1000,
        max_packet_bytes=100000,
        max_items_per_batch=1,
    )
    assert summary["batches"] == 3
    assert summary["successful_batches"] == 2
    assert summary["provider_exceptions"] == 1
    assert summary["partial_results_retained"] is True
    payload = json.loads(out.read_text())
    assert len(payload["matches"]) == 4
    assert json.loads(exc.read_text())["summary"]["count"] == 1
    oversized = cross.run_cross_record(
        queue_path,
        [],
        tmp_path / "oversized.json",
        tmp_path / "oversized-exc.json",
        tmp_path / "oversized-raw",
        "model",
        Client([]),
        max_packet_bytes=1,
    )
    assert oversized["provider_exceptions"] == 3
    assert oversized["analysis_status"] == "failed"
    preflight = cross.run_cross_record(
        queue_path,
        [context],
        tmp_path / "preflight.json",
        tmp_path / "preflight-exc.json",
        tmp_path / "preflight-raw",
        "model",
        Client([]),
        max_context_bytes=1000,
        max_packet_bytes=1,
    )
    assert preflight["provider_exceptions"] == 1
    assert preflight["analysis_status"] == "failed"


def test_run_cross_record_success_and_failure(tmp_path):
    queue_path = write(tmp_path / "queue.json", queue())
    context, _entry = intake_evidence_envelope(tmp_path)
    out = tmp_path / "out.json"
    exc = tmp_path / "exc.json"
    summary = cross.run_cross_record(
        queue_path,
        [context],
        out,
        exc,
        tmp_path / "raw",
        "model",
        Client([Response(decision())]),
        auto_accept=True,
    )
    assert summary["auto_accepted_updates"] == 1
    assert json.loads(out.read_text())["auto_accepted_updates"]
    assert json.loads(exc.read_text())["summary"]["count"] == 0
    failed = cross.run_cross_record(
        queue_path,
        [],
        tmp_path / "failed.json",
        tmp_path / "failed-exc.json",
        tmp_path / "failed-raw",
        "model",
        Client([RuntimeError("provider")]),
    )
    assert failed["provider_exceptions"] == 1


def test_resume_reuses_valid_batches_and_preserves_global_ids(tmp_path):
    q = queue()
    queue_path = write(tmp_path / "queue.json", q)
    context = write(tmp_path / "context.json", {"records": []})
    raw = tmp_path / "raw"
    raw.mkdir()
    packet = cross.global_packet(q, [], [0])
    (raw / "cross_record-0001.json").write_text(
        json.dumps({"request": packet, "response": decision()})
    )
    summary = cross.run_cross_record(
        queue_path,
        [context],
        tmp_path / "resumed.json",
        tmp_path / "resumed-exc.json",
        raw,
        "model",
        Client([Response(decision())]),
        max_items_per_batch=1,
        resume_raw_dir=raw,
    )
    assert summary["successful_batches"] == 2
    assert summary["provider_exceptions"] == 0
    assert len(json.loads((tmp_path / "resumed.json").read_text())["matches"]) == 4


def test_resume_revalidates_cross_record_identity_and_confidence(tmp_path):
    q = queue()
    queue_path = write(tmp_path / "queue.json", q)
    context, _entry = intake_evidence_envelope(tmp_path)
    exact = decision()["matches"][0]
    resumed_matches = [
        {**exact, "source_record_id": "doc-1"},
        {**exact, "confidence": True},
        {**exact, "confidence": 2.0},
        {**exact, "confidence": float("nan")},
        {**exact, "confidence": float("inf")},
    ]
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "cross_record-0001.json").write_text(
        json.dumps(
            {
                "request": cross.global_packet(q, [], [0, 1]),
                "response": {"matches": resumed_matches, "rationale": "resumed"},
            }
        )
    )

    summary = cross.run_cross_record(
        queue_path,
        [context],
        tmp_path / "resumed-guards.json",
        tmp_path / "resumed-guards-exc.json",
        raw,
        "model",
        Client([]),
        auto_accept=True,
        resume_raw_dir=raw,
    )

    result = json.loads((tmp_path / "resumed-guards.json").read_text())
    assert summary["auto_accepted_updates"] == 0
    assert [match["retained_review_reason"] for match in result["remaining_matches"]] == [
        "same_record_reference",
        "boolean_confidence",
        "confidence_out_of_range",
        "non_finite_confidence",
        "non_finite_confidence",
    ]


def test_resume_rejects_whitespace_in_target_and_source_identities(tmp_path):
    q = {
        "items": [
            {
                "document_id": f"{SOURCE_RECORD_ID} ",
                "field": "document_type",
                "reason": "formatting_review",
            },
            {"document_id": "doc-1", "field": "document_type", "reason": "formatting_review"},
        ],
        "review_cards": [{"card_id": "all", "item_indexes": [0, 1]}],
    }
    queue_path = write(tmp_path / "queue.json", q)
    context, _entry = intake_evidence_envelope(tmp_path)
    exact = decision()["matches"][0]
    matches = [
        exact,
        {
            **exact,
            "review_item_id": "review-item-1",
            "source_record_id": f"{SOURCE_RECORD_ID} ",
        },
    ]
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "cross_record-0001.json").write_text(
        json.dumps(
            {
                "request": cross.global_packet(q, [], [0, 1]),
                "response": {"matches": matches, "rationale": "resumed"},
            }
        )
    )

    summary = cross.run_cross_record(
        queue_path,
        [context],
        tmp_path / "resumed-whitespace.json",
        tmp_path / "resumed-whitespace-exc.json",
        raw,
        "model",
        Client([]),
        auto_accept=True,
        resume_raw_dir=raw,
    )

    result = json.loads((tmp_path / "resumed-whitespace.json").read_text())
    assert summary["auto_accepted_updates"] == 0
    assert [match["retained_review_reason"] for match in result["remaining_matches"]] == [
        "malformed_target_record_identity",
        "malformed_source_record_identity",
    ]


def test_resumable_batch_validator_handles_provider_response_shapes(tmp_path):
    raw = tmp_path / "raw.json"
    request = {"review_items": [{"review_item_id": "review-item-0"}]}
    provider_response = {
        "output": [
            {"content": [{"text": "not-json"}]},
            {"content": [{"text": json.dumps(decision())}]},
        ]
    }
    raw.write_text(json.dumps({"request": request, "response": provider_response}))
    assert cross.load_resumable_batch(raw, [0]) == decision()
    assert cross.load_resumable_batch(raw, [1]) is None
    raw.write_text(json.dumps({"request": request, "response": {"matches": []}}))
    assert cross.load_resumable_batch(raw, [0]) is None
    raw.write_text("bad-json")
    assert cross.load_resumable_batch(raw, [0]) is None
    raw.write_text(json.dumps({"request": [], "response": {}}))
    assert cross.load_resumable_batch(raw, [0]) is None
    for output in (
        [{"content": [{"text": "not-json"}]}],
        ["not-a-message"],
        [{"content": ["not-content"]}],
        [{"content": [{"text": 42}]}],
        [{"content": [{"text": json.dumps({"matches": []})}]}],
        [],
        None,
    ):
        raw.write_text(json.dumps({"request": request, "response": {"output": output}}))
        assert cross.load_resumable_batch(raw, [0]) is None


def test_resume_requires_existing_raw_directory(tmp_path):
    queue_path = write(tmp_path / "queue.json", queue())
    with pytest.raises(ValueError, match="Resume raw directory"):
        cross.run_cross_record(
            queue_path,
            [],
            tmp_path / "out.json",
            tmp_path / "exc.json",
            tmp_path / "raw",
            "model",
            Client([]),
            resume_raw_dir=tmp_path / "missing-raw",
        )


def test_cross_record_cli_disabled_and_enabled(monkeypatch, tmp_path):
    queue_path = write(tmp_path / "queue.json", queue())
    monkeypatch.delenv("CLIENT_REVIEW_LLM_CROSS_RECORD_ENABLED", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            cross.__file__,
            str(queue_path),
            "--out",
            str(tmp_path / "disabled.json"),
            "--exceptions",
            str(tmp_path / "disabled-exc.json"),
            "--raw-dir",
            str(tmp_path / "disabled-raw"),
        ],
    )
    cross.main()
    assert json.loads((tmp_path / "disabled.json").read_text())["summary"]["enabled"] is False
    monkeypatch.setenv("CLIENT_REVIEW_LLM_CROSS_RECORD_ENABLED", "true")
    monkeypatch.setenv("LLM_CLIENT_REVIEW_PROVIDER", "google")
    monkeypatch.setenv("LLM_POST_REVIEW_PROVIDER", "openrouter")
    monkeypatch.setattr(cross, "lane_model", lambda lane, provider: f"{provider}-reasoning")
    monkeypatch.setattr(cross, "load_project_env", lambda: None)
    client_args = []
    monkeypatch.setattr(
        reviewer,
        "build_reviewer_client",
        lambda *args: (client_args.append(args), Client([Response(decision())]))[1],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            cross.__file__,
            str(queue_path),
            "--out",
            str(tmp_path / "enabled.json"),
            "--exceptions",
            str(tmp_path / "enabled-exc.json"),
            "--raw-dir",
            str(tmp_path / "enabled-raw"),
            "--enable",
            "--timeout-seconds",
            "1",
            "--max-retries",
            "0",
            "--retry-backoff-seconds",
            "0",
            "--max-backoff-seconds",
            "1",
            "--max-context-bytes",
            "1000",
            "--max-packet-bytes",
            "100000",
            "--max-items-per-batch",
            "10",
            "--max-evidence-artifact-bytes",
            "10000000",
            "--max-evidence-entries",
            "10000",
            "--auto-accept-threshold",
            "0.99",
        ],
    )
    cross.main()
    assert json.loads((tmp_path / "enabled.json").read_text())["summary"]["matches"] == 2
    assert client_args[0][0] == "openrouter"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            cross.__file__,
            str(queue_path),
            "--out",
            str(tmp_path / "enabled-defaults.json"),
            "--exceptions",
            str(tmp_path / "enabled-defaults-exc.json"),
            "--raw-dir",
            str(tmp_path / "enabled-defaults-raw"),
            "--enable",
        ],
    )
    cross.main()
    assert json.loads((tmp_path / "enabled-defaults.json").read_text())["summary"]["matches"] == 2
    with pytest.raises(ValueError):
        cross.validate_auto_accept_threshold(0.98)


class UniformClient:
    """Answer every request identically, so replies cannot depend on call order.

    A stub that pops replies off a list answers by call order, which is exactly
    what concurrency is free to change. Returning the same decision to every
    batch removes call order from the comparison, leaving only the lane's own
    assembly of results under test.
    """

    def __init__(self):
        self.responses = self
        self.calls = 0

    def create(self, **kwargs):
        del kwargs
        self.calls += 1
        return Response(decision())


def test_worker_count_changes_speed_but_never_the_retained_artifact(tmp_path):
    """Batches may finish in any order; the artifact must not show it."""
    q = queue()
    q["items"] = [
        {"document_id": f"doc-{index}", "field": "seller_name", "reason": "formatting_review"}
        for index in range(6)
    ]
    q["review_cards"] = [{"card_id": "all", "item_indexes": list(range(6))}]
    queue_path = write(tmp_path / "queue.json", q)
    context = write(tmp_path / "context.json", {"records": []})

    def search(tag, workers):
        out = tmp_path / f"{tag}.json"
        summary = cross.run_cross_record(
            queue_path,
            [context],
            out,
            tmp_path / f"{tag}-exc.json",
            tmp_path / f"{tag}-raw",
            "model",
            UniformClient(),
            max_context_bytes=1000,
            max_packet_bytes=100000,
            max_items_per_batch=1,
            workers=workers,
        )
        return summary, json.loads(out.read_text())

    serial_summary, serial = search("serial", 1)
    concurrent_summary, concurrent = search("concurrent", 4)

    assert serial_summary["batches"] == 6
    assert concurrent_summary["batches"] == serial_summary["batches"]
    assert concurrent_summary["successful_batches"] == serial_summary["successful_batches"]
    assert concurrent_summary["matches"] == serial_summary["matches"]
    assert concurrent["batches"] == serial["batches"]
    assert concurrent["matches"] == serial["matches"]
    assert [item["batch"] for item in concurrent["batches"]] == list(range(1, 7))
