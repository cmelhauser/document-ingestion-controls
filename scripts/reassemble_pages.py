#!/usr/bin/env python3
"""Phase 1/2: conservatively group page records and flag duplicate evidence.

This stage is append-only: it consumes an ingestion manifest and writes grouping
proposals. It never edits page provenance, moves source files, or force-groups a
page whose consecutive-page and identifier evidence do not agree.
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from runtime_config import env_bool, load_project_env

PAGE_MARKER = re.compile(r"\bpage\s*(\d+)\s*(?:of|/)\s*(\d+)\b", re.I)
IDENTIFIER_PATTERNS = (
    re.compile(
        r"\binvoice\s+(?:number|no\.?|#)\s*[:#]?\s*\n?\s*((?=[A-Z0-9-]*\d)[A-Z0-9-]{4,})", re.I
    ),
    re.compile(
        r"\b(?:po|purchase order|bol|b/l|awb|pro)\s+(?:number|no\.?|#)\s*[:#]?\s*\n?\s*((?=[A-Z0-9-]*\d)[A-Z0-9-]{4,})",
        re.I,
    ),
    re.compile(r"\b(?:bol/awb|po)\s*[:#]?\s*\n?\s*((?=[A-Z0-9-]*\d)[A-Z0-9-]{4,})", re.I),
)


def load_manifest(path):
    """Load a manifest with page records and fail clearly on a wrong artifact."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get("pages"), list):
        raise ValueError("Manifest must contain a pages list")
    if not all(
        isinstance(record, dict) and record.get("page_id") and record.get("page_pdf")
        for record in data["pages"]
    ):
        raise ValueError("Every manifest page requires page_id and page_pdf")
    page_ids = [record["page_id"] for record in data["pages"]]
    if len(page_ids) != len(set(page_ids)):
        raise ValueError("Manifest page_id values must be unique")
    return data


def resolve_manifest_artifact(root, value):
    """Resolve a manifest-relative artifact without absolute paths or path escape."""
    candidate = Path(value)
    if candidate.is_absolute():
        raise ValueError("Manifest artifact path must be relative")
    root = Path(root).resolve()
    path = (root / candidate).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("Manifest artifact path escapes the manifest directory") from exc
    return path


def page_evidence(record, root):
    """Read retained native text and extract conservative reassembly evidence."""
    rel = record.get("text_file")
    text = ""
    if rel:
        path = resolve_manifest_artifact(root, rel)
        if path.is_file():
            text = path.read_text(errors="replace")
    marker = PAGE_MARKER.search(text)
    identifiers = set()
    for pattern in IDENTIFIER_PATTERNS:
        identifiers.update(match.upper() for match in pattern.findall(text))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if re.fullmatch(
            r"(?:invoice|po|purchase order|bol|b/l|awb|pro)(?: number| no\.?)?", line, re.I
        ):
            nearby = lines[max(0, index - 3) : index] + lines[index + 1 : index + 4]
            for candidate in nearby:
                if re.fullmatch(r"(?=[A-Z0-9-]*\d)[A-Z0-9-]{4,}", candidate, re.I):
                    identifiers.add(candidate.upper())
    return {
        "marker": (int(marker.group(1)), int(marker.group(2))) if marker else None,
        "identifiers": sorted(identifiers),
        "text": text,
    }


def sha256(path):
    """Hash a retained artifact without reading it into a mutable working record."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def duplicate_candidates(records, root):
    """Return exact PDF/text duplicates; near matches remain review work."""
    seen, duplicates = {}, []
    for record in records:
        key_parts = []
        page_pdf = record.get("page_pdf")
        if page_pdf:
            page_path = resolve_manifest_artifact(root, page_pdf)
            if page_path.is_file():
                key_parts.append(f"pdf:{sha256(page_path)}")
        text_file = record.get("text_file")
        if text_file:
            text_path = resolve_manifest_artifact(root, text_file)
            if text_path.is_file():
                normalized = " ".join(text_path.read_text(errors="replace").lower().split())
                key_parts.append(f"text:{hashlib.sha256(normalized.encode()).hexdigest()}")
        key = "|".join(key_parts)
        if not key:
            continue
        if key in seen:
            duplicates.append(
                {
                    "page_id": record["page_id"],
                    "duplicate_of": seen[key],
                    "match": "exact_retained_artifact",
                    "disposition": "review_before_exclusion",
                }
            )
        else:
            seen[key] = record["page_id"]
    return duplicates


def reassemble(records, root, broad=False):
    """Propose only complete, consecutive PAGE X OF Y groups with a shared ID."""
    ordered = sorted(records, key=lambda item: item.get("source_page_number", 0))
    evidence = [page_evidence(record, root) for record in ordered]
    groups, exceptions, consumed = [], [], set()
    for index, (record, proof) in enumerate(zip(ordered, evidence, strict=True)):
        marker = proof["marker"]
        if record["page_id"] in consumed or marker is None or marker[0] != 1 or marker[1] < 2:
            continue
        total = marker[1]
        candidates = list(range(index, index + total))
        if index + total > len(ordered):
            exceptions.append({"page_id": record["page_id"], "reason": "incomplete_page_sequence"})
            continue
        segment = [(ordered[i], evidence[i]) for i in candidates]
        sequence_ok = all(
            item[1]["marker"] == (offset + 1, total) for offset, item in enumerate(segment)
        )
        shared = set(segment[0][1]["identifiers"])
        for _, item_proof in segment[1:]:
            shared &= set(item_proof["identifiers"])
        types = {item[0].get("document_type") for item in segment}
        if sequence_ok and shared and len(types) == 1 and None not in types:
            group_id = f"reassembly_{len(groups) + 1:05d}"
            groups.append(
                {
                    "document_group_id": group_id,
                    "document_type": types.pop(),
                    "page_ids": [item[0]["page_id"] for item in segment],
                    "source_page_range": f"{segment[0][0]['source_page_number']}-{segment[-1][0]['source_page_number']}",
                    "evidence": {
                        "page_markers": [item[1]["marker"] for item in segment],
                        "shared_identifiers": sorted(shared),
                    },
                    "disposition": "proposed_group_requires_review",
                }
            )
            consumed.update(item[0]["page_id"] for item in segment)
        else:
            exceptions.append(
                {
                    "page_id": record["page_id"],
                    "reason": "conflicting_reassembly_evidence",
                    "sequence_ok": sequence_ok,
                    "shared_identifiers": sorted(shared),
                    "document_types": sorted(str(value) for value in types),
                }
            )
    if broad:
        broad_groups, broad_exceptions = broad_reassembly(ordered, root, consumed, len(groups))
        groups.extend(broad_groups)
        exceptions.extend(broad_exceptions)
    unassigned = [record["page_id"] for record in ordered if record["page_id"] not in consumed]
    return groups, exceptions, unassigned


def broad_reassembly(records, root, consumed, group_offset):
    """Propose unordered groups only when a unique typed identifier joins pages.

    This is deliberately a candidate stage: matching identifiers can be reused or
    misread, so it never changes page order, document IDs, or review status.
    """
    candidates = {}
    for record in records:
        if record["page_id"] in consumed or not record.get("document_type"):
            continue
        for identifier in page_evidence(record, root)["identifiers"]:
            candidates.setdefault((record["document_type"], identifier), []).append(record)
    groups, exceptions = [], []
    for (document_type, identifier), members in sorted(candidates.items()):
        unique = {member["page_id"]: member for member in members}
        ordered = sorted(unique.values(), key=lambda item: item.get("source_page_number", 0))
        if len(ordered) < 2 or any(member["page_id"] in consumed for member in ordered):
            continue
        other_identifiers = {
            other
            for member in ordered
            for other in page_evidence(member, root)["identifiers"]
            if other != identifier
        }
        if other_identifiers:
            exceptions.append(
                {
                    "page_ids": [member["page_id"] for member in ordered],
                    "reason": "ambiguous_unordered_identifier_evidence",
                    "shared_identifier": identifier,
                    "other_identifiers": sorted(other_identifiers),
                }
            )
            continue
        group_id = f"reassembly_{group_offset + len(groups) + 1:05d}"
        groups.append(
            {
                "document_group_id": group_id,
                "document_type": document_type,
                "page_ids": [member["page_id"] for member in ordered],
                "source_page_range": "unordered:"
                + ",".join(str(member.get("source_page_number")) for member in ordered),
                "evidence": {
                    "method": "shared_identifier_unordered",
                    "shared_identifiers": [identifier],
                    "source_page_numbers": [member.get("source_page_number") for member in ordered],
                },
                "disposition": "proposed_group_requires_order_and_client_review",
            }
        )
        consumed.update(member["page_id"] for member in ordered)
    return groups, exceptions


def main():
    load_project_env()
    parser = argparse.ArgumentParser(
        description="Propose evidence-backed page groups and duplicate review candidates."
    )
    parser.add_argument("manifest", help="ingestion_manifest.json from scripts/ingest_pages.py")
    parser.add_argument("--out", required=True, help="new reassembly proposal JSON")
    parser.add_argument(
        "--exceptions", required=True, help="new unresolved reassembly/duplicate JSON"
    )
    parser.add_argument(
        "--broad-unordered-proposals",
        action="store_true",
        default=env_bool("REASSEMBLY_BROAD_UNORDERED_PROPOSALS", False),
        help="also propose review-required unordered groups with exactly one shared typed identifier",
    )
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        manifest_path = Path(args.manifest).resolve()
        manifest = load_manifest(manifest_path)
        root = manifest_path.parent
        groups, exceptions, unassigned = reassemble(
            manifest["pages"], root, args.broad_unordered_proposals
        )
        duplicates = duplicate_candidates(manifest["pages"], root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Reassembly failed: {exc}")
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "pages": len(manifest["pages"]),
        "proposed_groups": len(groups),
        "unassigned_pages": len(unassigned),
        "exact_duplicate_candidates": len(duplicates),
        "findings": [
            "Groups are proposals; original page provenance remains immutable.",
            "Unordered identifier groups are explicitly review-required and do not establish page sequence.",
        ],
    }
    Path(args.out).write_text(
        json.dumps(
            {"summary": summary, "groups": groups, "unassigned_page_ids": unassigned}, indent=2
        )
        + "\n"
    )
    Path(args.exceptions).write_text(
        json.dumps(
            {
                "summary": {"count": len(exceptions) + len(duplicates)},
                "reassembly_exceptions": exceptions,
                "duplicate_candidates": duplicates,
            },
            indent=2,
        )
        + "\n"
    )
    if not args.quiet:
        print(f"Proposed groups: {len(groups)}")
        print(f"Unassigned pages: {len(unassigned)}")
        print(f"Exact duplicate candidates: {len(duplicates)}")


if __name__ == "__main__":
    main()
