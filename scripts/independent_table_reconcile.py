#!/usr/bin/env python3
"""Deterministically reconcile source rows with Google Document AI table evidence.

The adapter maps only exact, client-approved source-template rules. It never
uses an LLM mapping proposal or changes a source value. Agreement is evidence,
not approval: financial and identity cells remain client-review work.
"""

import argparse
import hashlib
import json
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

import registry_approval
from cli_help import apply_shared_help
from table_comprehension import approved_rule, template_fingerprint

FINANCIAL_FIELDS = {
    "amount",
    "commission_amount",
    "commissionable_amount",
    "discount_amount",
    "extended_amount",
    "gross_amount",
    "net_amount",
    "quantity",
    "rate",
    "total_amount",
    "unit_price",
}
IDENTITY_TYPES = {"dealer", "brand", "customer", "payer"}


def load_object(path, label):
    """Load one object-shaped retained artifact."""
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def new_path(path):
    """Refuse to overwrite a retained reconciliation artifact."""
    path = Path(path)
    if path.exists():
        raise ValueError(f"Output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def normalized(value, financial=False):
    """Compare values deterministically without replacing either source string."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = " ".join(value.split())
    if not financial:
        return value.casefold()
    candidate = value.replace(",", "").replace("$", "")
    if candidate.startswith("(") and candidate.endswith(")"):
        candidate = f"-{candidate[1:-1]}"
    try:
        return str(Decimal(candidate))
    except InvalidOperation:
        return None


# A reconciliation either compared this cell against an independent reading or it
# did not. Only the first two are evidence of anything; the others record that no
# independent counterpart was found, which is the absence of corroboration.
COMPARED_DECISIONS = frozenset({"independent_exact_agreement", "independent_value_disagreement"})


def protected(rule):
    """Identify facts that cannot leave review solely on independent agreement."""
    return (
        rule.get("canonical_field") in FINANCIAL_FIELDS
        or rule.get("semantic_type") in IDENTITY_TYPES
    )


def source_rows(packet):
    """Read rows from either page or corpus table-comprehension output."""
    rows = packet.get("source_rows")
    if not isinstance(rows, list):
        raise ValueError("Source-row artifact requires source_rows list")
    return [row for row in rows if isinstance(row, dict)]


def document_ai_cells(handoff_paths, registry, coverage=None):
    """Create independent semantic candidates from exact registry rules only.

    ``coverage`` accumulates what the corroborating side actually yielded. A
    structurally unexpected table, row, or cell is skipped by design, but a run
    that skipped everything must not be reportable as a completed corroboration.
    """
    coverage = {} if coverage is None else coverage
    for name in (
        "tables_seen",
        "tables_rejected",
        "rows_seen",
        "rows_rejected",
        "cells_seen",
        "cells_rejected",
        "cells_unmapped",
        "cells_mapped",
    ):
        coverage.setdefault(name, 0)
    candidates = {}
    for path in handoff_paths:
        handoff = load_object(path, "Document AI handoff")
        if handoff.get("provider") != "google_document_ai" or not isinstance(
            handoff.get("records"), list
        ):
            raise ValueError("Document AI evidence must be a Google Document AI adapter handoff")
        for record in handoff["records"]:
            if (
                not isinstance(record, dict)
                or record.get("independence_group") != "google_document_ai"
            ):
                raise ValueError("Document AI record lacks independent-provider provenance")
            page_id = record.get("page_id")
            if not isinstance(page_id, str) or not isinstance(record.get("source_tables"), list):
                raise ValueError("Document AI record requires page_id and source_tables")
            for table in record["source_tables"]:
                coverage["tables_seen"] += 1
                if not isinstance(table, dict) or not isinstance(table.get("source_headers"), list):
                    coverage["tables_rejected"] += 1
                    continue
                headers = [
                    item for item in table["source_headers"] if isinstance(item, str) and item
                ]
                fingerprint = template_fingerprint(headers)
                for row in table.get("source_rows", []):
                    coverage["rows_seen"] += 1
                    if not isinstance(row, dict) or not isinstance(
                        row.get("source_row_number"), int
                    ):
                        coverage["rows_rejected"] += 1
                        continue
                    for cell in row.get("cells", []):
                        coverage["cells_seen"] += 1
                        if not isinstance(cell, dict) or not isinstance(
                            cell.get("source_label"), str
                        ):
                            coverage["cells_rejected"] += 1
                            continue
                        rule = approved_rule(registry, fingerprint, cell["source_label"])
                        if rule is None:
                            coverage["cells_unmapped"] += 1
                            continue
                        coverage["cells_mapped"] += 1
                        key = (page_id, row["source_row_number"], rule.get("canonical_field"))
                        candidates.setdefault(key, []).append(
                            {
                                "value": cell.get("evidence_text"),
                                "source_label": cell["source_label"],
                                "rule_id": rule.get("rule_id"),
                                "raw_response": record.get("raw_response"),
                                "engine": record.get("engine"),
                                "rule": rule,
                            }
                        )
    return candidates


def llm_cells(rows, registry):
    """Yield source-row cells whose exact approved mapping remains visible."""
    for row in rows:
        page_id, number = row.get("page_id"), row.get("row_number")
        if not isinstance(page_id, str) or not isinstance(number, int):
            continue
        for cell in row.get("cells", []):
            if not isinstance(cell, dict):
                continue
            mapping = cell.get("canonical_mapping")
            if not registry_approval.is_approved(mapping):
                continue
            field = mapping.get("canonical_field")
            rule_id = mapping.get("registry_rule_id")
            rules = [
                rule
                for rule in registry.get("rules", [])
                if isinstance(rule, dict) and rule.get("rule_id") == rule_id
            ]
            rule = rules[0] if len(rules) == 1 else {"canonical_field": field}
            if isinstance(field, str):
                yield row, cell, rule


def reconcile(rows, candidates, registry):
    """Compare independently read cells and aggregate protected review work."""
    outcomes, review = [], Counter()
    for row, cell, rule in llm_cells(rows, registry):
        field, page_id, number = rule.get("canonical_field"), row["page_id"], row["row_number"]
        matches = candidates.get((page_id, number, field), [])
        protected_fact = protected(rule)
        outcome = {
            "document_id": row.get("document_id", page_id),
            "page_id": page_id,
            "source_row_id": row.get("source_row_id"),
            "row_number": number,
            "canonical_field": field,
            "llm_value": cell.get("visible_value"),
            "llm_evidence_text": cell.get("evidence_text"),
            "protected_fact": protected_fact,
            "client_review_required": protected_fact,
        }
        if len(matches) != 1:
            outcome["decision"] = (
                "independent_cell_missing" if not matches else "independent_cell_ambiguous"
            )
            outcome["document_ai_candidates"] = matches
        else:
            match = matches[0]
            left = normalized(cell.get("visible_value"), field in FINANCIAL_FIELDS)
            right = normalized(match.get("value"), field in FINANCIAL_FIELDS)
            outcome.update({"document_ai_value": match.get("value"), "document_ai_evidence": match})
            outcome["decision"] = (
                "independent_exact_agreement"
                if left is not None and left == right
                else "independent_value_disagreement"
            )
        outcomes.append(outcome)
        if protected_fact:
            reason = (
                outcome["decision"]
                if outcome["decision"] != "independent_exact_agreement"
                else "financial_identity_independent_reconciliation_required"
            )
            review[(page_id, field, reason)] += 1
    exceptions = [
        {
            "document_id": page_id,
            "page_id": page_id,
            "field": field,
            "reason": reason,
            "affected_cells": count,
            "disposition": "client_review_required",
        }
        for (page_id, field, reason), count in sorted(review.items())
    ]
    return outcomes, exceptions


def run(source_rows_path, registry_path, handoffs, out_path, exceptions_path, adapter_path):
    """Create new source-preserving reconciliation and provider-handoff artifacts."""
    targets = [Path(item).resolve() for item in (out_path, exceptions_path, adapter_path)]
    if len(set(targets)) != len(targets):
        raise ValueError("Output, exception, and adapter paths must be distinct")
    rows = source_rows(load_object(source_rows_path, "source rows"))
    registry = load_object(registry_path, "registry")
    if not isinstance(registry.get("rules"), list):
        raise ValueError("Registry requires rules list")
    coverage = {}
    outcomes, exceptions = reconcile(
        rows, document_ai_cells(handoffs, registry, coverage), registry
    )
    compared = sum(1 for item in outcomes if item.get("decision") in COMPARED_DECISIONS)
    # A corroboration run that compared nothing corroborated nothing. Counting
    # outcome records instead of comparisons let 300 `independent_cell_missing`
    # results -- every one of them a cell with no independent counterpart --
    # report `corroborated: true` with zero review items.
    if handoffs and not compared:
        exceptions = [
            *exceptions,
            {
                "document_id": None,
                "page_id": None,
                "field": None,
                "reason": "independent_reconciliation_produced_no_comparison",
                "affected_cells": len(outcomes),
                "coverage": dict(coverage),
                "disposition": "client_review_required",
            },
        ]
    out_path, exceptions_path, adapter_path = (
        new_path(item) for item in (out_path, exceptions_path, adapter_path)
    )
    out_path.write_text(
        json.dumps({"schema_version": "1.0", "reconciliations": outcomes}, indent=2) + "\n"
    )
    exceptions_path.write_text(
        json.dumps(
            {
                "summary": {"count": len(exceptions), "coverage": dict(coverage)},
                "exceptions": exceptions,
            },
            indent=2,
        )
        + "\n"
    )
    adapter_path.write_text(
        json.dumps(
            {
                "engine": "google_document_ai/deterministic_semantic_reconciliation",
                "provider": "google_document_ai",
                "records": outcomes,
                "proposal_only": True,
                "requires_deterministic_reconciliation": True,
                "client_approval_permitted": False,
                "source_row_artifact_sha256": hashlib.sha256(
                    Path(source_rows_path).read_bytes()
                ).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )
    return {
        "reconciliations": len(outcomes),
        "compared_against_independent_evidence": compared,
        "review_items": len(exceptions),
        "coverage": dict(coverage),
        "corroborated": bool(compared),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Reconcile source rows with independent Document AI table evidence."
    )
    parser.add_argument("source_rows", help="Mapped source-native table rows to be compared.")
    parser.add_argument("--registry", required=True)
    parser.add_argument(
        "--document-ai",
        nargs="+",
        required=True,
        help="One or more Document AI handoffs supplying independent table cells. Repeatable.",
    )
    parser.add_argument("--out", required=True)
    parser.add_argument("--exceptions", required=True)
    parser.add_argument("--adapter-out", required=True)
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                run(
                    args.source_rows,
                    args.registry,
                    args.document_ai,
                    args.out,
                    args.exceptions,
                    args.adapter_out,
                ),
                sort_keys=True,
            )
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Independent table reconciliation failed: {exc}")


if __name__ == "__main__":
    main()
