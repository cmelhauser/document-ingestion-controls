#!/usr/bin/env python3
"""Assemble the final client-ready delivery folder and nothing more.

A run directory holds everything the pipeline needed: raw provider responses,
intermediate control artifacts, caches, resume state, and operator notes. None of
that belongs in a client hand-off, and several parts of it must never leave the
operator's machine at all.

This copies only the deliverables into a new no-clobber directory, refuses to
copy anything on the exclusion list even when a caller names it explicitly, and
writes a manifest hashing every delivered file.

The evidence graph is delivered *cleaned*: nodes and edges whose approval state
is still a proposal are withheld, because a proposal is not a fact and a client
folder is read as a statement of fact. Withholding is not hiding -- the exact
count of every withheld state is retained in the manifest and restated in the
delivered README, so the client can see that proposals existed and how many.
"""

import argparse
import hashlib
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help

SCHEMA_VERSION = "client_delivery_package_v1"
# Approval states that represent an observed or approved fact. Everything else is
# a proposal or an open finding and is withheld from the cleaned graph.
DELIVERABLE_GRAPH_STATES = frozenset({"approved", "observed"})
# Never delivered, at any --include the caller supplies. Raw provider responses
# and credentials are the two that would cause real harm; the rest is operator
# working state that would only mislead.
FORBIDDEN_NAME_PARTS = (
    ".env",
    "credential",
    "secret",
    "token",
    "api_key",
    "apikey",
    "raw_response",
    "checkpoint",
    "resume_state",
    ".llm-cache",
    ".llm-rate-limit",
)
FORBIDDEN_DIRECTORY_NAMES = frozenset({"raw", "raw_responses", "providers", "cache", "tmp"})


def is_forbidden(path, root=None):
    """Return whether a path must never reach a client folder.

    Directory names are judged only inside the run being delivered from. Walking
    every ancestor to the filesystem root would reject an artifact for living
    under an unrelated system directory such as ``/tmp``.
    """
    path = Path(path)
    lowered = path.name.casefold()
    if any(part in lowered for part in FORBIDDEN_NAME_PARTS):
        return True
    if root is None:
        parents = [path.parent]
    else:
        try:
            relative = path.resolve().relative_to(Path(root).resolve())
        except ValueError:
            parents = [path.parent]
        else:
            parents = [Path(part) for part in relative.parts[:-1]]
    return any(parent.name.casefold() in FORBIDDEN_DIRECTORY_NAMES for parent in parents)


def sha256(path):
    """Hash a delivered file so the client can verify what they received."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path, description):
    """Read a required JSON artifact with a clear failure message."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{description} is not readable JSON: {path}") from exc


def gate_status(final_review):
    """Return the final-review gate status without inferring one."""
    for key in ("gate_status", "status"):
        value = final_review.get(key)
        if isinstance(value, str) and value:
            return value
    summary = final_review.get("summary")
    if isinstance(summary, dict):
        for key in ("gate_status", "status"):
            value = summary.get(key)
            if isinstance(value, str) and value:
                return value
    raise ValueError("final review artifact does not state a gate status")


def clean_graph(graph):
    """Withhold proposal-state graph content, retaining an exact count of what was withheld."""
    nodes = [node for node in graph.get("nodes", []) if _deliverable(node)]
    delivered_ids = {node["node_id"] for node in nodes}
    edges = [
        edge
        for edge in graph.get("edges", [])
        if _deliverable(edge)
        and edge["from_node_id"] in delivered_ids
        and edge["to_node_id"] in delivered_ids
    ]
    withheld = {}
    for item in list(graph.get("nodes", [])) + list(graph.get("edges", [])):
        if _deliverable(item):
            continue
        state = str(item.get("approval_state", "unknown"))
        withheld[state] = withheld.get(state, 0) + 1
    cleaned = {
        "artifact_type": "client_delivered_evidence_graph_v1",
        "source_graph_sha256": graph.get("graph_sha256"),
        "run_id": graph.get("run_id"),
        "nodes": nodes,
        "edges": edges,
        "withheld_by_approval_state": dict(sorted(withheld.items())),
        "withheld_total": sum(withheld.values()),
        "note": (
            "Proposal-state nodes and edges are withheld because a proposal is not "
            "an approved fact. Their exact counts are retained above; nothing was "
            "deleted from the operator's retained graph."
        ),
    }
    return cleaned, sum(withheld.values())


def _deliverable(item):
    """Return whether a graph node or edge represents an approved or observed fact."""
    return (
        isinstance(item, dict)
        and item.get("approval_state") in DELIVERABLE_GRAPH_STATES
        and item.get("protected") is not True
    )


def copy_deliverable(source, destination_dir, delivered, root=None):
    """Copy one permitted deliverable and record its hash.

    Existence and the restricted-artifact rule are checked in one preflight
    before the folder is created, over exactly the list copied here, so a
    re-check at copy time cannot fire and is not kept.
    """
    source = Path(source)
    target = destination_dir / source.name
    if target.exists():
        raise ValueError(f"duplicate deliverable name: {source.name}")
    shutil.copy2(source, target)
    delivered.append(
        {
            "file": str(target.relative_to(destination_dir.parent)),
            "sha256": sha256(target),
            "bytes": target.stat().st_size,
            "source": str(source),
        }
    )


def build(
    out_dir,
    final_review,
    canonical_export=None,
    evidence_graph=None,
    review_files=(),
    crm_files=(),
    documents=(),
    allow_open_findings=False,
    source_root=None,
):
    """Create the client folder from an explicit deliverable list."""
    source_root = Path(final_review).resolve().parent if source_root is None else Path(source_root)
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise ValueError(f"client delivery directory already exists: {out_dir}")
    review = load_json(final_review, "final review queue")
    status = gate_status(review)
    if status != "clear" and not allow_open_findings:
        raise ValueError(
            f"final review gate is {status!r}; a client folder states delivered facts. "
            "Resolve the queue, or pass --allow-open-findings to deliver an explicitly "
            "provisional package."
        )
    # Check every deliverable before creating anything. Creating the directory
    # first and failing on a missing file left a folder behind that then blocked
    # the retry with "already exists" -- the no-clobber rule working against the
    # operator over a mistake they had already been told about.
    for source in (
        final_review,
        *review_files,
        *crm_files,
        *documents,
        *(item for item in (canonical_export, evidence_graph) if item),
    ):
        candidate = Path(source)
        if not candidate.is_file():
            raise ValueError(f"deliverable does not exist: {candidate}")
        if is_forbidden(candidate, source_root):
            raise ValueError(f"refusing to deliver a restricted artifact: {candidate}")

    out_dir.mkdir(parents=True)
    delivered = []
    for name, sources in (
        ("review", [final_review, *review_files]),
        ("crm", crm_files),
        ("documents", documents),
    ):
        if not sources:
            continue
        section = out_dir / name
        section.mkdir()
        for source in sources:
            copy_deliverable(source, section, delivered, source_root)
    if canonical_export is not None:
        section = out_dir / "data"
        section.mkdir()
        copy_deliverable(canonical_export, section, delivered, source_root)
    withheld_total = 0
    if evidence_graph is not None:
        cleaned, withheld_total = clean_graph(load_json(evidence_graph, "evidence graph"))
        section = out_dir / "evidence"
        section.mkdir()
        target = section / "evidence_graph.json"
        target.write_text(json.dumps(cleaned, indent=2, sort_keys=True) + "\n")
        delivered.append(
            {
                "file": str(target.relative_to(out_dir.parent)),
                "sha256": sha256(target),
                "bytes": target.stat().st_size,
                "source": str(evidence_graph),
            }
        )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "final_review_gate_status": status,
        "provisional": status != "clear",
        "files": sorted(delivered, key=lambda item: item["file"]),
        "file_count": len(delivered),
        "graph_proposals_withheld": withheld_total,
        "excluded_by_policy": (
            "Raw provider responses, credentials, caches, resume state, and "
            "intermediate control artifacts are never delivered."
        ),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (out_dir / "README.md").write_text(readme(manifest))
    return manifest


def readme(manifest):
    """Write the client-facing explanation of what the folder is and is not."""
    provisional = (
        "\n> **This package is provisional.** The final review gate is "
        f"`{manifest['final_review_gate_status']}`, so at least one finding is still "
        "open. Figures here may change once those findings are resolved.\n"
        if manifest["provisional"]
        else ""
    )
    withheld = manifest["graph_proposals_withheld"]
    graph_note = (
        f"\n`evidence/evidence_graph.json` contains approved and observed facts only. "
        f"{withheld} proposal-state entries were withheld because a proposal is not an "
        "approved fact; their counts are recorded in the graph file itself.\n"
        if withheld
        else ""
    )
    return f"""# Delivered records

Generated {manifest["generated_at"]}.
{provisional}
This folder contains {manifest["file_count"]} delivered files and nothing else.
`manifest.json` lists every file with its SHA-256 so you can verify what you
received.

| Folder | Contents |
|---|---|
| `data/` | The approved canonical export |
| `evidence/` | The cleaned evidence graph |
| `review/` | The final review record and any accompanying review files |
| `crm/` | Target-system staging files |
| `documents/` | Generated reports |
{graph_note}
What is deliberately not here: raw provider responses, credentials, caches,
resume state, and intermediate working artifacts. They stay with the operator as
the audit record.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="new client folder; must not exist")
    parser.add_argument("--final-review", required=True, help="final review queue artifact")
    parser.add_argument(
        "--canonical-export", default=None, help="Approved canonical export to deliver."
    )
    parser.add_argument(
        "--evidence-graph",
        default=None,
        help="Evidence graph to deliver. Proposal-state nodes and edges are withheld and their counts retained.",
    )
    parser.add_argument(
        "--review-file",
        action="append",
        default=[],
        help="Reviewer-facing file to deliver. Repeatable.",
    )
    parser.add_argument(
        "--crm-file",
        action="append",
        default=[],
        help="Target-system staging file to deliver. Repeatable.",
    )
    parser.add_argument(
        "--document",
        action="append",
        default=[],
        help="Generated client document to deliver. Repeatable.",
    )
    parser.add_argument(
        "--allow-open-findings",
        action="store_true",
        help="deliver an explicitly provisional package while findings remain open",
    )
    parser.add_argument(
        "--source-root",
        default=None,
        help="run directory the deliverables come from; defaults to the final review's directory",
    )
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        manifest = build(
            args.out_dir,
            args.final_review,
            canonical_export=args.canonical_export,
            evidence_graph=args.evidence_graph,
            review_files=args.review_file,
            crm_files=args.crm_file,
            documents=args.document,
            allow_open_findings=args.allow_open_findings,
            source_root=args.source_root,
        )
    except (OSError, ValueError) as exc:
        sys.exit(f"Client delivery package failed: {exc}")
    if not args.quiet:
        print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
