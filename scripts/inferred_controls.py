#!/usr/bin/env python3
"""Build repeatable, proposal-only control artifacts when client ledgers are absent.

The lane never pretends that document evidence is a general ledger or bank
statement.  It creates deterministic rollups that can prioritize review and
explicitly records every limitation.  The output directory is no-clobber and
contains ``attributed.json``, ``gl.csv``, ``payments.json``, exceptions, and a
hash-bound manifest.
"""

import argparse
import csv
import hashlib
import json
import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from completeness import get, num

SCHEMA_VERSION = "1.0"
ARTIFACT_TYPE = "inferred_control_proposals"


def load_records(path):
    """Load consensus/proofed records from the supported envelope shapes."""
    with open(path, encoding="utf-8") as stream:
        data = json.load(stream)
    records = None
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        for key in ("documents", "results", "records", "attributions"):
            if isinstance(data.get(key), list):
                records = data[key]
                break
    if records is None:
        raise ValueError("input must contain a documents, results, records, or attributions array")
    if any(not isinstance(record, dict) for record in records):
        raise ValueError("input records must be JSON objects")
    return records


def month_of(value):
    """Return an ISO month or ``None`` for an unavailable/invalid date."""
    if not value:
        return None
    text = str(value).strip()
    if len(text) >= 7 and text[4] in "-/" and text[:4].isdigit():
        month = text[5:7]
        if month.isdigit() and 1 <= int(month) <= 12:
            return f"{text[:4]}-{int(month):02d}"
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).strftime("%Y-%m")
        except ValueError:
            continue
    return None


def _first(rec, *keys):
    for key in keys:
        value = get(rec, key)
        if value not in (None, ""):
            return value
    return None


def infer_gl_rows(records):
    """Aggregate observed document totals into proposal-only GL-shaped rows."""
    buckets = defaultdict(lambda: {"amount": 0.0, "documents": []})
    exceptions = []
    for rec in records:
        date = _first(rec, "invoice_date", "bill_date", "memo_date", "document_date")
        month = month_of(date)
        raw_amount = _first(rec, "total_amount", "amount")
        vendor = str(
            _first(rec, "seller_name", "vendor_name", "carrier_name", "payee_name") or "UNKNOWN"
        ).strip()
        document_id = str(rec.get("document_id", "unknown"))
        if not month or raw_amount is None:
            reason = "missing_or_invalid_date" if not month else "missing_amount"
            exceptions.append({"document_id": document_id, "reason": reason})
            continue
        amount = abs(num(raw_amount))
        key = (month, vendor)
        buckets[key]["amount"] += amount
        buckets[key]["documents"].append(document_id)
    rows = []
    for (period, vendor), bucket in sorted(buckets.items()):
        rows.append(
            {
                "period": period,
                "vendor": vendor,
                "amount": f"{bucket['amount']:.2f}",
                "document_count": str(len(bucket["documents"])),
                "inference_status": "inferred_document_rollup",
                "source_document_ids": ";".join(bucket["documents"]),
            }
        )
    return rows, exceptions


def infer_payments(records):
    """Extract only explicit payment evidence; never infer a payment from an invoice."""
    payments = []
    exceptions = []
    for rec in records:
        document_id = str(rec.get("document_id", "unknown"))
        amount = _first(rec, "payment_amount", "paid_amount", "amount_paid")
        reference = _first(rec, "payment_number", "check_number", "remittance_number")
        if amount is None and reference is None:
            exceptions.append(
                {"document_id": document_id, "reason": "no_explicit_payment_evidence"}
            )
            continue
        if amount is None:
            exceptions.append(
                {"document_id": document_id, "reason": "payment_reference_without_amount"}
            )
            continue
        payments.append(
            {
                "document_id": document_id,
                "invoice_number": _first(rec, "invoice_number", "pro_number"),
                "payment_number": reference,
                "amount": f"{abs(num(amount)):.2f}",
                "inference_status": "inferred_explicit_source_evidence",
            }
        )
    return payments, exceptions


# Every controlled identifier a document can attribute itself by, most specific
# first. These are the extraction schema's own field names.
#
# This list used to lead with `ack_number`, which the schema has never defined --
# the field is `acknowledgement_number`. A 716-page commission corpus whose pages
# print "ACK NO", "ACK #" and "ACK#" therefore produced
# `no_explicit_attribution_key` for 715 of 716 documents, and the one lane that
# exists for a client with no general ledger returned nothing at all. A key the
# schema cannot express is a key this lane can never find.
ATTRIBUTION_KEY_FIELDS = (
    "acknowledgement_number",
    "job_number",
    "project_number",
    "purchase_order_number",
    "sales_order_number",
    "order_number",
    "work_order_number",
    "service_order_number",
    "change_order_number",
)


def infer_attribution(records):
    """Create a deterministic attribution proposal without a client reference master."""
    output = []
    exceptions = []
    for rec in records:
        document_id = str(rec.get("document_id", "unknown"))
        raw_amount = _first(rec, "total_amount", "amount")
        key = _first(rec, *ATTRIBUTION_KEY_FIELDS)
        if key and raw_amount is not None:
            output.append(
                {
                    "document_id": document_id,
                    "reference_key": str(key),
                    "amount": f"{abs(num(raw_amount)):.2f}",
                    "attribution_method": "explicit_source_key_proposal",
                    "client_reference_verified": False,
                    "proposal_only": True,
                }
            )
        else:
            exceptions.append(
                {
                    "document_id": document_id,
                    "amount": f"{abs(num(raw_amount)):.2f}" if raw_amount is not None else None,
                    "reason": "no_explicit_attribution_key" if not key else "missing_amount",
                }
            )
    return output, exceptions


def _write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(input_path, output_dir):
    """Write all proposal artifacts once, refusing an existing output directory."""
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(
            f"refusing to overwrite existing inferred-control output: {output_dir}"
        )
    records = load_records(input_path)
    input_path = Path(input_path)
    input_hash = _sha256(input_path)
    output_dir.mkdir(parents=True)
    attributed, attribution_exceptions = infer_attribution(records)
    gl_rows, gl_exceptions = infer_gl_rows(records)
    payments, payment_exceptions = infer_payments(records)
    _write_json(
        output_dir / "attributed.json",
        {
            "artifact_type": "inferred_attribution_proposal",
            "schema_version": SCHEMA_VERSION,
            "proposal_only": True,
            "client_reference_verified": False,
            "attributions": attributed,
            "summary": {
                "records": len(records),
                "attributed": len(attributed),
                "unattributed": len(attribution_exceptions),
            },
        },
    )
    with (output_dir / "gl.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "period",
                "vendor",
                "amount",
                "document_count",
                "inference_status",
                "source_document_ids",
            ],
        )
        writer.writeheader()
        writer.writerows(gl_rows)
    _write_json(
        output_dir / "payments.json",
        {
            "artifact_type": "inferred_payment_proposal",
            "schema_version": SCHEMA_VERSION,
            "proposal_only": True,
            "authoritative": False,
            "payments": payments,
            "summary": {
                "records": len(records),
                "explicit_evidence": len(payments),
                "missing_evidence": len(payment_exceptions),
            },
        },
    )
    exceptions = {
        "artifact_type": ARTIFACT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "proposal_only": True,
        "attribution": attribution_exceptions,
        "gl": gl_exceptions,
        "payments": payment_exceptions,
    }
    _write_json(output_dir / "exceptions.json", exceptions)
    manifest = {
        "artifact_type": ARTIFACT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "proposal_only": True,
        "authoritative": False,
        "completeness_gate": "blocked_inferred_controls_not_authoritative",
        "source_input": os.path.basename(input_path),
        "source_input_sha256": input_hash,
        "artifacts": {},
        "exception_counts": {
            key: len(value) for key, value in exceptions.items() if isinstance(value, list)
        },
    }
    for name in ("attributed.json", "gl.csv", "payments.json", "exceptions.json"):
        manifest["artifacts"][name] = {"sha256": _sha256(output_dir / name)}
    _write_json(output_dir / "manifest.json", manifest)


def main():
    parser = argparse.ArgumentParser(
        description="Build proposal-only inferred attribution, GL, and payment controls."
    )
    parser.add_argument("input", help="consensus/proofed JSON envelope")
    parser.add_argument("--out-dir", required=True, help="new empty output directory")
    parser.add_argument("--quiet", action="store_true", help="suppress the summary")
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        run(args.input, args.out_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
    if not args.quiet:
        print(f"Proposal artifacts written to {args.out_dir}; completeness gate remains blocked.")


if __name__ == "__main__":
    main()
