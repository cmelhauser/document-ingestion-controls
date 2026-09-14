#!/usr/bin/env python3
"""Phase 1 intake: split a PDF into immutable per-page records and PDFs.

The script deliberately does not reassemble pages into documents. A page is only
assigned to a document group when the evidence supports it; every unassigned
page remains visible in the manifest and classification exception queue.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pikepdf
from cli_help import apply_shared_help

# These are intentionally high-signal anchors. A passing mention of "invoice"
# in an email, manual, or contract is not enough to classify a page as an
# invoice. Everything below this threshold is queued for human classification.
TYPE_RULES = (
    # Explicit project material must not become an in-scope business record
    # merely because it names document types such as purchase orders.
    ("out_of_scope_document", (("project instructions",),)),
    (
        "commercial_invoice",
        (
            ("invoice number", "invoice date"),
            ("invoice #", "total due"),
            ("invoice no", "amount due"),
        ),
    ),
    ("carrier_freight_bill", (("pro number", "freight charges"), ("freight bill",))),
    ("bill_of_lading", (("bill of lading",), ("b/l number",))),
    ("proof_of_delivery", (("proof of delivery",),)),
    ("packing_list", (("packing list",),)),
    ("purchase_order", (("purchase order",), ("po number", "ship to"))),
    ("remittance_advice", (("remittance advice",),)),
    ("credit_memo", (("credit memo",),)),
    ("debit_memo", (("debit memo",),)),
    ("customs_entry", (("cbp form 7501",), ("entry summary", "importer of record"))),
    ("commission_statement", (("commission statement",), ("statement of commissions",))),
    ("commission_report", (("commission report",), ("commission detail report",))),
)

# Document types the extraction schema declares that these rules deliberately do
# not detect. The rules are high-signal phrase matches, and a type whose pages
# carry no distinctive printed phrase is left to human classification rather than
# guessed at -- a misclassified document extracts structurally wrong, which is
# worse than an unclassified one.
#
# This list exists because a 716-page commission corpus once classified 25 pages
# and escalated 691. `commission_statement` and `commission_report` were valid
# schema values with no rule able to produce them, so every later lane inherited
# `document_family: unknown` and nothing said why. A declared type must now be
# either detectable or knowingly exempt.
RULE_EXEMPT_DOCUMENT_TYPES = frozenset(
    {
        "sales_quote",
        "estimate",
        "request_for_quote",
        "receipt",
        "purchase_requisition",
        "sales_order",
        "order_acknowledgement",
        "order_confirmation",
        "work_order",
        "service_report",
        "timesheet",
        "expense_report",
        "air_waybill",
        "payment_confirmation",
        "account_statement",
        "bank_statement",
        "statement",
        "certificate",
        "customs_invoice",
        "return_authorization",
        "contract",
        "unknown",
    }
)


def has_text_layer(page):
    """Return a conservative indication that a PDF page exposes native text."""
    try:
        return bool(str(page.get("/Contents", "")) and "/Font" in str(page.get("/Resources", "")))
    except Exception:
        return False


def extract_page_text(source, page_number):
    """Extract one page with Poppler without treating a failure as an empty page."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["pdftotext", "-f", str(page_number), "-l", str(page_number), os.fspath(source), "-"],  # noqa: S607 - poppler is a documented install dependency
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return "", f"pdftotext_unavailable:{type(exc).__name__}"
    if result.returncode:
        return "", f"pdftotext_failed:{result.returncode}"
    return result.stdout, "native_text"


def categorize_text(text):
    """Return a high-signal page category, or an explicit unclassified state."""
    normalized = " ".join(text.lower().split())
    if not normalized:
        return None, None, "no_extractable_text"
    for document_type, alternatives in TYPE_RULES:
        for required_terms in alternatives:
            if all(term in normalized for term in required_terms):
                status = (
                    "out_of_scope"
                    if document_type == "out_of_scope_document"
                    else "rule_classified"
                )
                return document_type, 1.0, status
    return None, None, "no_high_signal_document_type"


def schema_label(value, fallback):
    """Create a stable, filename-safe label without losing raw provenance."""
    if value is None:
        return fallback
    label = re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")
    return label or fallback


def page_output_name(source, page_number, document_type):
    """Lexically sortable, one-page filename tied to schema provenance fields."""
    source_label = schema_label(Path(source).stem, "source")
    type_label = schema_label(document_type, "unclassified")
    page_label = f"{source_label}__p{page_number:04d}__{type_label}"
    return f"{page_number:06d}__{page_label}.pdf", page_label


def ensure_empty_output_dir(path):
    """Create an output directory without overwriting a prior ingestion run."""
    if path.exists():
        if not path.is_dir():
            raise ValueError(f"Output path is not a directory: {path}")
        if any(path.iterdir()):
            raise ValueError(f"Output directory must be empty: {path}")
    else:
        path.mkdir(parents=True)


def sha256(path):
    """Hash retained evidence without loading a source PDF into memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def split_pdf(source, output_dir):
    """Split every source page, emit per-page provenance, and queue uncertainty."""
    source = Path(source).resolve()
    output_dir = Path(output_dir)
    source_dir = output_dir / "source"
    pages_dir = output_dir / "pages"
    text_dir = output_dir / "text"
    source_dir.mkdir()
    pages_dir.mkdir()
    text_dir.mkdir()

    source_hash = sha256(source)
    retained_source = source_dir / source.name
    shutil.copyfile(source, retained_source)
    if sha256(retained_source) != source_hash:
        raise ValueError("Retained source copy failed SHA-256 verification")
    retained_source_path = os.fspath(retained_source.relative_to(output_dir))

    records, exceptions = [], []
    with pikepdf.open(source) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            text, text_status = extract_page_text(source, index)
            document_type, confidence, classification_status = categorize_text(text)
            page_filename, page_label = page_output_name(source, index, document_type)
            page_path = pages_dir / page_filename
            child = pikepdf.Pdf.new()
            child.pages.append(page)
            child.save(page_path)

            text_path = None
            if text:
                text_path = text_dir / f"{index:06d}__{page_label}.txt"
                text_path.write_text(text)
            out_of_scope = classification_status == "out_of_scope"
            classified = document_type is not None and not out_of_scope
            record = {
                "page_id": f"{schema_label(source.stem, 'source')}__p{index:04d}",
                "page_label": page_label,
                "output_sort_key": f"{index:06d}",
                "document_id": None,
                "source_file": retained_source_path,
                "source_original_name": source.name,
                "source_sha256": source_hash,
                "source_page_number": index,
                "source_page_range": str(index),
                "page_pdf": os.fspath(page_path.relative_to(output_dir)),
                "text_file": os.fspath(text_path.relative_to(output_dir)) if text_path else None,
                "has_text_layer": has_text_layer(page),
                "text_extraction_status": text_status,
                "document_type": document_type,
                "classification_confidence": confidence,
                "classification_status": classification_status,
                "document_group_id": None,
                "reassembly_status": "unassigned",
            }
            records.append(record)
            if not classified:
                exceptions.append(
                    {
                        "page_id": record["page_id"],
                        "source_file": record["source_file"],
                        "source_page_number": index,
                        "reason": classification_status,
                        "candidate_document_type": document_type,
                        "disposition": "human_classification_and_reassembly_required",
                    }
                )

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source_file": retained_source_path,
        "source_original_name": source.name,
        "source_sha256": source_hash,
        "pages": len(records),
        "page_output_directory": "pages",
        "output_order": "ascending source_page_number; zero-padded output_sort_key",
        "native_text_pages": sum(1 for r in records if r["has_text_layer"]),
        "rule_classified_pages": sum(
            1 for r in records if r["classification_status"] == "rule_classified"
        ),
        "exception_pages": len(exceptions),
        "unassigned_pages": len(records),
        "findings": [
            "Every source page was preserved as exactly one PDF in ascending source "
            "page order. Filenames begin with a zero-padded sort key and carry the "
            "page label and conservative document category. No pages were "
            "force-grouped into documents. The byte-identical source PDF is retained "
            "inside the intake run and verified by SHA-256."
        ],
    }
    if exceptions:
        summary["findings"].append(
            f"{len(exceptions)} pages require human classification and reassembly; "
            "they remain in the exception queue rather than being guessed."
        )
    return {"summary": summary, "pages": records}, {
        "summary": {"count": len(exceptions)},
        "exceptions": exceptions,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Split a multipage PDF into per-page PDFs and a conservative classification queue."
    )
    parser.add_argument("input", help="source PDF; it is never modified")
    parser.add_argument(
        "--out", required=True, help="new or empty directory for page PDFs and JSON manifests"
    )
    apply_shared_help(parser)
    args = parser.parse_args()

    source = Path(args.input)
    if not source.is_file() or source.suffix.lower() != ".pdf":
        sys.exit(f"Input must be a readable PDF: {source}")
    output_dir = Path(args.out)
    try:
        ensure_empty_output_dir(output_dir)
        manifest, queue = split_pdf(source, output_dir)
    except (OSError, ValueError, pikepdf.PdfError) as exc:
        sys.exit(f"Ingestion failed: {exc}")

    (output_dir / "ingestion_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output_dir / "classification_exceptions.json").write_text(json.dumps(queue, indent=2) + "\n")
    print(f"Pages split: {manifest['summary']['pages']}")
    print(f"Rule-classified: {manifest['summary']['rule_classified_pages']}")
    print(f"Queued for review: {manifest['summary']['exception_pages']}")
    print(f"Written to {output_dir}")


if __name__ == "__main__":
    main()
