#!/usr/bin/env python3
"""Build the canonical export envelope that Phase 6 consumes.

`canonical_load.py`, `csv_api_staging.py`, `retrieval_store.py`, and
`client_delivery_package.py` all take a canonical export as their first input,
and no command produced one: the published delivery sequence began with an
artifact an operator had to write by hand. This is that missing producer.

It is a gate, not a converter. A fact reaches the export only when the document
carrying it has retained source provenance, a review-clear document status, and
arithmetic that is proved or genuinely not applicable, and only when the
canonical column it would occupy actually exists. Everything else is retained as
an explicit exception naming the document and the control that withheld it, so
an empty export is readable as "nothing is approved yet" rather than as
"nothing was found".

Extraction fields that no canonical column accepts are withheld per document and
counted; they are a mapping question for the client, not a licence to invent a
column. Nothing here approves anything -- an authorization still comes from the
client-decision path, and this command only refuses to carry what that path has
not cleared.
"""

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from client_review.queue import resolutions
from review_status import ARITHMETIC_CLEAR, REVIEW_CLEAR
from runtime_config import load_project_env

SCHEMA_VERSION = "canonical_export_v1"
# A stable namespace, so re-exporting the same run produces the same keys and the
# documented idempotent upsert actually is idempotent across reruns.
KEY_NAMESPACE = uuid.UUID("6f0b7f7a-1c3d-5f9e-9a2b-0c1d2e3f4a5b")
# Header fields that name a party. Each becomes a party row; none becomes a
# party_role, because the role vocabulary is a client decision, not a synonym
# for the printed label.
PARTY_NAME_FIELDS = (
    "seller_name",
    "vendor_name",
    "buyer_name",
    "customer_name",
    "bill_to_name",
    "sold_to_name",
    "ship_to_name",
    "remit_to_name",
    "payer_name",
    "payee_name",
    "shipper_name",
    "consignee_name",
    "carrier_name",
    "broker_name",
    "agent_name",
    "manufacturer_name",
    "dealer_name",
    "brand_name",
)
# The party that issued the invoice, in preference order.
BILLER_FIELDS = ("seller_name", "vendor_name", "remit_to_name", "brand_name")
# Extraction header field -> canonical invoice_header column.
INVOICE_HEADER_COLUMNS = {
    "invoice_number": "invoice_number",
    "invoice_date": "invoice_date",
    "due_date": "due_date",
    "payment_terms": "payment_terms",
    "purchase_order_number": "po_number",
    "currency": "currency_code",
    "subtotal": "subtotal",
    "tax_amount": "tax_amount",
    "freight_amount": "freight_amount",
    "accessorial_total": "accessorial_total",
    "discount_amount": "discount_amount",
    "total_amount": "total_amount",
    "amount_due": "amount_due",
    "acknowledgement_number": "ack_number",
    "job_number": "job_number",
}
# Extraction line field -> canonical invoice_line column.
INVOICE_LINE_COLUMNS = {
    "description": "description",
    "quantity": "quantity",
    "uom": "uom_code",
    "unit_price": "unit_price",
    "extended_amount": "extended_amount",
    "discount_amount": "discount",
    "tax_code": "tax_code",
    "gl_account_code": "gl_code",
    "cost_center": "cost_centre",
    "country_of_origin": "country_of_origin",
    "job_number": "job_number",
}


def key_for(*parts):
    """Derive a stable canonical key so a re-export upserts instead of duplicating."""
    return str(uuid.uuid5(KEY_NAMESPACE, "|".join(str(part) for part in parts)))


def load_object(path, label):
    """Read one retained artifact, naming it when it is the wrong shape."""
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value


def text(entry):
    """Pull a string out of a {value, ...} extraction field or a bare scalar."""
    if isinstance(entry, dict):
        entry = entry.get("value")
    return "" if entry is None else str(entry).strip()


def accepted_fields(record):
    """Return the consensus field names this document actually agreed on."""
    fields = record.get("fields")
    if not isinstance(fields, dict):
        return set()
    return {
        name for name, field in fields.items() if isinstance(field, dict) and field.get("accepted")
    }


def manifest_documents(manifest):
    """Group retained pages into the documents their provenance describes.

    Reassembly groups pages into documents; an unassigned page is its own
    one-page document, which is exactly how every upstream control keyed this
    corpus. Provenance is taken from the retained page, never rebuilt.
    """
    pages = manifest.get("pages")
    if not isinstance(pages, list):
        raise ValueError("Intake manifest must contain a pages list")
    documents = {}
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError("Intake manifest pages must be objects")
        document_id = text(page.get("document_id")) or text(page.get("page_id"))
        if not document_id:
            raise ValueError("Intake manifest page requires page_id or document_id")
        entry = documents.setdefault(
            document_id,
            {
                "document_id": document_id,
                "source_file": text(page.get("source_file")),
                "source_sha256": text(page.get("source_sha256")),
                "page_ranges": [],
                "page_count": 0,
                "classification_status": text(page.get("classification_status")),
                "classified_type": text(page.get("document_type")),
            },
        )
        entry["page_ranges"].append(text(page.get("source_page_range")))
        entry["page_count"] += 1
    for entry in documents.values():
        entry["source_page_range"] = ",".join(part for part in entry["page_ranges"] if part)
    return documents


def open_review_documents(queue):
    """Return the document IDs the final gate still has unresolved work against."""
    items = queue.get("items")
    if not isinstance(items, list):
        raise ValueError("Final-review queue must contain an items list")
    return {text(item.get("document_id")) for item in items if isinstance(item, dict)} - {""}


def arithmetic_status(record):
    """Return the document's arithmetic status, defaulting to unproved."""
    return text(record.get("arithmetic_status")) or "not_reported"


def exclusion(document_id, reason, detail, control):
    """Create the standard retained exclusion record."""
    return {
        "priority": "high",
        "document_id": document_id,
        "page_id": "",
        "region_id": "",
        "field": "",
        "reason": reason,
        "detail": detail,
        "review_source": control,
        "disposition": "client_review_required",
    }


def party_rows(record, document_id, batch_id, status, accepted):
    """Create one party row per distinct accepted printed name on the document."""
    header = record.get("header") if isinstance(record.get("header"), dict) else {}
    rows, keys = {}, {}
    for field in PARTY_NAME_FIELDS:
        if f"header.{field}" not in accepted:
            continue
        name = text(header.get(field))
        if not name:
            continue
        normalized = " ".join(name.casefold().split())
        party_key = key_for("party", normalized)
        keys[field] = party_key
        rows[party_key] = {
            "party_key": party_key,
            "natural_key": normalized,
            "canonical_name": name,
            "normalized_name": normalized,
            "source_document_id": document_id,
            "review_status": status,
            "batch_id": batch_id,
        }
    return rows, keys


def invoice_rows(record, document_id, batch_id, status, proof, accepted, party_keys):
    """Create the invoice header and lines, or say which required column is absent."""
    header = record.get("header") if isinstance(record.get("header"), dict) else {}
    invoice_number = (
        text(header.get("invoice_number")) if "header.invoice_number" in accepted else ""
    )
    if not invoice_number:
        return None, [], "canonical_invoice_header_requires_invoice_number"
    biller = next((party_keys[field] for field in BILLER_FIELDS if field in party_keys), "")
    if not biller:
        return None, [], "canonical_invoice_header_requires_biller_party"
    invoice_key = key_for("invoice", document_id, invoice_number)
    row = {
        "invoice_key": invoice_key,
        "natural_key": f"{document_id}|{invoice_number}",
        "biller_party_key": biller,
        # The proof comes from the arithmetic control, not from the consensus
        # record, which has no such field. Reading it off the record wrote
        # "not_reported" onto invoices the control had actually proved -- and
        # this column is what an analyst filters on to get verified figures.
        "arithmetic_status": proof,
        "consensus_flag": text(record.get("consensus_flag")) or "no_consensus",
        "source_document_id": document_id,
        "has_handwriting": bool(record.get("has_handwriting")),
        "review_status": status,
        "batch_id": batch_id,
    }
    for field, column in INVOICE_HEADER_COLUMNS.items():
        if f"header.{field}" in accepted and text(header.get(field)):
            row[column] = text(header.get(field))
    lines = []
    for index, line in enumerate(record.get("lines") or []):
        if not isinstance(line, dict):
            raise ValueError(f"{document_id} lines must be objects")
        number = text(line.get("line_number")) or str(index + 1)
        entry = {
            "invoice_line_key": key_for("invoice_line", invoice_key, number),
            "invoice_key": invoice_key,
            "line_number": number,
            "review_status": status,
            "batch_id": batch_id,
        }
        for field, column in INVOICE_LINE_COLUMNS.items():
            if f"lines[{index}].{field}" in accepted and text(line.get(field)):
                entry[column] = text(line.get(field))
        lines.append(entry)
    return row, lines, None


def carried_fields(record, accepted):
    """Return which accepted fields a canonical column actually accepts."""
    carried = {"document_type"}
    for field in (*PARTY_NAME_FIELDS, *INVOICE_HEADER_COLUMNS):
        carried.add(f"header.{field}")
    for index in range(len(record.get("lines") or [])):
        # line_number is carried as the invoice_line row's own column rather than
        # through the column map, so name it here or it reads as unmapped.
        carried.add(f"lines[{index}].line_number")
        for field in INVOICE_LINE_COLUMNS:
            carried.add(f"lines[{index}].{field}")
    return accepted & carried


def build(manifest, consensus, arithmetic, queue, batch_id, registry_version, classifications=None):
    """Gate every extracted document into canonical rows or an explicit exception.

    ``classifications`` maps a document to the type the classification consensus
    accepted. The extraction consensus writes ``unknown`` wherever its lanes could
    not agree a type, which on the commission run was nearly every document, so a
    document it left unknown takes the accepted type. A type extraction did
    establish is kept.
    """
    documents = manifest_documents(manifest)
    records = consensus.get("documents")
    if not isinstance(records, list):
        raise ValueError("Consensus artifact must contain a documents list")
    arithmetic_by_document = {
        text(entry.get("document_id")): entry
        for entry in (arithmetic.get("documents") or [])
        if isinstance(entry, dict)
    }
    open_review = open_review_documents(queue) if queue else set()
    tables = {name: [] for name in ("currency", "document_type", "document", "party")}
    tables["invoice_header"], tables["invoice_line"] = [], []
    exceptions, parties, types, currencies, withheld = [], {}, {}, {}, 0
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Consensus documents must be objects")
        document_id = text(record.get("document_id"))
        if not document_id:
            raise ValueError("Consensus document requires document_id")
        provenance = documents.get(document_id)
        if provenance is None:
            exceptions.append(
                exclusion(
                    document_id,
                    "document_provenance_missing",
                    "No retained intake page names this document, so its source file, "
                    "page range, and hash cannot be cited.",
                    "canonical_export",
                )
            )
            continue
        missing = [
            field
            for field in ("source_file", "source_page_range", "source_sha256")
            if not provenance.get(field)
        ]
        if missing:
            exceptions.append(
                exclusion(
                    document_id,
                    "document_provenance_incomplete",
                    f"Retained intake provenance is missing {', '.join(missing)}.",
                    "canonical_export",
                )
            )
            continue
        status = text(record.get("review_status"))
        if status not in REVIEW_CLEAR:
            exceptions.append(
                exclusion(
                    document_id,
                    "document_review_status_not_clear",
                    f"Consensus reports {status or 'no review status'}; canonical load "
                    f"admits only {', '.join(REVIEW_CLEAR)} and has no override.",
                    "consensus",
                )
            )
            continue
        proof = arithmetic_status(arithmetic_by_document.get(document_id, {}))
        if proof not in ARITHMETIC_CLEAR:
            exceptions.append(
                exclusion(
                    document_id,
                    f"arithmetic_{proof}",
                    "Arithmetic is not proved and not established as inapplicable; a "
                    "batch decision cannot clear an arithmetic finding.",
                    "arithmetic_check",
                )
            )
            continue
        if document_id in open_review:
            exceptions.append(
                exclusion(
                    document_id,
                    "open_final_review_item",
                    "The final review gate still holds unresolved items for this document.",
                    "final_review_queue",
                )
            )
            continue
        accepted = accepted_fields(record)
        extracted = text(record.get("document_type"))
        accepted_type = text((classifications or {}).get(document_id, {}).get("document_type"))
        if extracted and extracted != "unknown":
            document_type = extracted
        else:
            document_type = accepted_type or extracted or provenance["classified_type"]
        if not document_type:
            exceptions.append(
                exclusion(
                    document_id,
                    "document_type_not_established",
                    "Neither consensus nor the intake classifier established a document "
                    "type, and the canonical document row requires one.",
                    "consensus",
                )
            )
            continue
        types.setdefault(
            document_type,
            {
                "document_type": document_type,
                "description": document_type.replace("_", " "),
                "is_financial": document_type != "unknown",
            },
        )
        tables["document"].append(
            {
                "document_id": document_id,
                "document_type": document_type,
                "source_file": provenance["source_file"],
                "source_page_range": provenance["source_page_range"],
                "source_sha256": provenance["source_sha256"],
                "page_count": provenance["page_count"],
                "consensus_flag": text(record.get("consensus_flag")) or "no_consensus",
                "arithmetic_status": proof,
                "has_handwriting": bool(record.get("has_handwriting")),
                "review_status": status,
                "batch_id": batch_id,
            }
        )
        document_parties, party_keys = party_rows(record, document_id, batch_id, status, accepted)
        parties.update(document_parties)
        invoice, lines, refusal = invoice_rows(
            record, document_id, batch_id, status, proof, accepted, party_keys
        )
        if invoice is None:
            exceptions.append(
                exclusion(
                    document_id,
                    refusal,
                    "The document is review-clear, but the canonical invoice tables "
                    "require a column this layout does not supply. Its document and "
                    "party rows are exported; its transactional rows are not.",
                    "canonical_export",
                )
            )
        else:
            tables["invoice_header"].append(invoice)
            tables["invoice_line"].extend(lines)
            currency = invoice.get("currency_code")
            if currency:
                currencies.setdefault(
                    currency, {"currency_code": currency, "description": currency}
                )
        unmapped = sorted(accepted - carried_fields(record, accepted))
        if unmapped:
            withheld += len(unmapped)
            exceptions.append(
                exclusion(
                    document_id,
                    "no_canonical_column_for_accepted_field",
                    f"{len(unmapped)} agreed fields have no canonical column and were "
                    "withheld rather than mapped: " + ", ".join(unmapped),
                    "canonical_export",
                )
            )
    tables["party"] = sorted(parties.values(), key=lambda row: row["party_key"])
    tables["document_type"] = sorted(types.values(), key=lambda row: row["document_type"])
    tables["currency"] = sorted(currencies.values(), key=lambda row: row["currency_code"])
    return tables, exceptions, withheld


def findings_for(tables, exceptions, records):
    """State what an empty or partial export means, rather than leaving it silent."""
    findings = []
    if not tables["document"]:
        findings.append(
            f"No document of {records} reached the canonical export. This is a blocked "
            "gate, not a clean one: every document is named in the companion exception "
            "artifact with the control that withheld it."
        )
    elif len(tables["document"]) < records:
        findings.append(
            f"{records - len(tables['document'])} of {records} documents were withheld; "
            "the export is a partial batch and the rest remain open review work."
        )
    if tables["document"] and not tables["invoice_header"]:
        findings.append(
            "Documents were exported with no transactional rows. Retrieval will answer "
            "about document identity and provenance only."
        )
    if exceptions:
        findings.append(
            f"{len(exceptions)} retained exclusions; none is closed by producing this export."
        )
    return findings


def main():
    parser = argparse.ArgumentParser(
        description="Build the canonical export envelope from retained run artifacts."
    )
    parser.add_argument("--manifest", required=True, help="intake ingestion_manifest.json")
    parser.add_argument("--consensus", required=True, help="consensus.json")
    parser.add_argument("--arithmetic", help="arithmetic.json; absent means unproved")
    parser.add_argument("--final-review", help="final review queue JSON")
    parser.add_argument("--batch-id", required=True, help="load batch identifier")
    parser.add_argument("--registry-version", help="approved mapping registry version")
    parser.add_argument(
        "--classifications",
        action="append",
        default=[],
        metavar="ARTIFACT",
        help=(
            "Repeatable classification_consensus_v1 artifact. A document the extraction "
            "consensus left 'unknown' takes the type classification accepted; a type "
            "extraction established is kept."
        ),
    )
    parser.add_argument("--out", required=True, help="new canonical export JSON")
    parser.add_argument("--exceptions", required=True, help="retained exclusion JSON")
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args()
    load_project_env()
    try:
        if not args.batch_id.strip():
            raise ValueError("--batch-id must be non-empty")
        for path in (args.out, args.exceptions):
            if Path(path).exists():
                raise ValueError(f"Output already exists: {path}")
        consensus = load_object(args.consensus, "Consensus artifact")
        classifications = resolutions(args.classifications) if args.classifications else None
        tables, exceptions, withheld = build(
            load_object(args.manifest, "Intake manifest"),
            consensus,
            load_object(args.arithmetic, "Arithmetic artifact") if args.arithmetic else {},
            load_object(args.final_review, "Final-review queue") if args.final_review else {},
            args.batch_id.strip(),
            args.registry_version,
            classifications,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Canonical export failed: {exc}")
    records = len(consensus.get("documents") or [])
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "artifact_type": SCHEMA_VERSION,
        "input_documents": records,
        "exported_documents": len(tables["document"]),
        "exported_rows": {name: len(rows) for name, rows in tables.items()},
        "withheld_accepted_fields": withheld,
        "client_review_items": len(exceptions),
        "findings": findings_for(tables, exceptions, records),
    }
    export = {
        "schema_version": SCHEMA_VERSION,
        "summary": summary,
        "batch_id": args.batch_id.strip(),
        "registry_version": args.registry_version,
        "gate_status": "clear"
        if tables["document"] and not exceptions
        else "blocked_pending_client_review",
        "tables": tables,
    }
    try:
        for path, value in (
            (args.out, export),
            (
                args.exceptions,
                {"summary": {"count": len(exceptions)}, "exceptions": exceptions},
            ),
        ):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with Path(path).open("x") as stream:
                stream.write(json.dumps(value, indent=2) + "\n")
    except OSError as exc:
        sys.exit(f"Canonical export failed: {exc}")
    if not args.quiet:
        print(f"Canonical documents exported: {len(tables['document'])} of {records}")
        print(f"Retained exclusions: {len(exceptions)}")
        for finding in summary["findings"]:
            print(f"  - {finding}")


if __name__ == "__main__":
    main()
