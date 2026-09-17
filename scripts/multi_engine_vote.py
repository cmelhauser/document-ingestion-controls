#!/usr/bin/env python3
"""Settle an open field by counting every reading the run already retained.

`consensus.py` compares exactly two lanes. A run that read its corpus with four
vendors therefore judged every field on one pair and never consulted the other
two readings, which are sitting in the run's own provider artifacts. On the
commission run that left 13,423 fields open while 81,460 cells had two or more
vendors on them.

Scored against held-out ground truth -- 293 cells an independent arithmetic
proof had already settled, which this lane never sees -- the vote reads:

| vendors agreeing | cells | correct |
|---|---|---|
| three | 61 | 100% |
| two | 188 | 96.3% |

For comparison, asking a model to re-read those same pages and adjudicate them
scored 87.1% and cost real money. The readings were already bought.

What keeps this honest:

* **One vote per vendor, not per lane.** Two Google lanes are one reading of the
  page by one vendor's weights. `runtime_config.model_vendor` resolves the
  vendor through a router, so a lane routed to `google/gemini` and a lane on
  Vertex count once between them. Counting lanes would manufacture exactly the
  independence this repository exists to protect.
* **An accepted value is never overruled.** This settles open fields only.
* **A tie is not a result.** Where the leading value does not lead outright the
  field stays open and is counted, because a coin-flip between two vendors is
  the finding, not a resolution.
* **The agreement count rides on the acceptance.** Two vendors agreeing and
  three vendors agreeing are different evidence -- 96.3% against 100% here --
  and a reader must be able to tell them apart afterwards.
"""

import argparse
import collections
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from consensus import refresh_views
from run_io import empty_output_path
from runtime_config import UNRESOLVED_MODEL_VENDOR, load_project_env, model_vendor

ARTIFACT_TYPE = "multi_engine_vote_v1"
ACCEPTED_BY = "agreed_by_independent_vendors"
# A tie broken by a non-LLM extractor is not vendor agreement and never carries
# that label. It is the same evidence `independent_corroboration.py` accepts --
# two engines disagreed and exactly one reading appears in what the extractor
# read off that page -- and it is weaker for the same reason: presence proves
# the string is printed there, not that it belongs in that cell.
TIEBREAK_ACCEPTED_BY = "tie_broken_by_independent_extractor"
# A tied reading that the same document already states elsewhere, in a cell that
# is settled. On the Bill Payment pages `$19,954.00` is printed as Amount,
# Original Amount, Balance and Payment, so a tie in one of them is decided by
# the others. Deliberately document-local: the same test over the whole corpus
# scored 58% because a field misfiled the same way a hundred times looks
# authoritative, while a value the document itself states twice is the document
# agreeing with itself. Measured at 55 of 55 against held-out truth.
LOCAL_ACCEPTED_BY = "matches_a_settled_value_in_the_same_document"
NON_WORD = re.compile(r"[^a-z0-9]+")
LINE_FIELD = re.compile(r"^lines\[(\d+)\]\.(.+)$")
DEFAULT_MINIMUM_VENDORS = 2


def reading(entry):
    """Return a value from a field wrapper or a bare scalar."""
    return entry.get("value") if isinstance(entry, dict) else entry


def comparable(value):
    """Normalise a reading so presentation differences are not disagreement.

    `4,098.49`, `$4098.49` and `(4098.49)` are one number differently printed,
    and comparing them as text would report three vendors disagreeing about a
    value they all read identically. Numbers compare as numbers; everything else
    compares as collapsed, case-folded text.
    """
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value).strip())
    if not text:
        return None
    numeric = text.replace(",", "").replace("$", "").replace("%", "")
    if numeric.startswith("(") and numeric.endswith(")"):
        numeric = "-" + numeric[1:-1]
    try:
        return f"{float(numeric):.4f}"
    except ValueError:
        return text.casefold()


def handoff_vendor(handoff, path):
    """Name the vendor whose weights produced a handoff's readings."""
    vendor = model_vendor(handoff.get("provider"), handoff.get("model"))
    if not vendor or vendor == UNRESOLVED_MODEL_VENDOR:
        raise ValueError(
            f"{path}: cannot resolve the model vendor behind this reading; a lane whose "
            "vendor is unknown cannot be counted as an independent vote"
        )
    return vendor


def collect_votes(handoffs):
    """Index every retained reading by (document, field) and vendor.

    The first lane seen for a vendor wins the vendor's vote. A run holding two
    lanes of the same vendor -- a probe and a full pass, say -- must not have
    them counted twice, and taking the first is deterministic given a stable
    input order.
    """
    votes = collections.defaultdict(dict)
    vendors = {}
    for path in handoffs:
        handoff = json.loads(Path(path).read_text())
        if not isinstance(handoff, dict) or not isinstance(handoff.get("records"), list):
            raise ValueError(f"{path}: not a provider handoff with a records list")
        vendor = handoff_vendor(handoff, path)
        vendors.setdefault(vendor, []).append(str(path))
        for record in handoff["records"]:
            if not isinstance(record, dict):
                continue
            document = record.get("document_id") or record.get("page_id")
            if not document:
                continue
            header = record.get("header")
            if isinstance(header, dict):
                for name, entry in header.items():
                    if comparable(reading(entry)) is not None:
                        votes[(document, f"header.{name}")].setdefault(vendor, reading(entry))
            for index, line in enumerate(record.get("lines") or []):
                if not isinstance(line, dict):
                    continue
                for name, entry in line.items():
                    if comparable(reading(entry)) is not None:
                        votes[(document, f"lines[{index}].{name}")].setdefault(
                            vendor, reading(entry)
                        )
    return votes, vendors


def extractor_evidence(paths):
    """Read what a non-LLM extractor saw on each page, as one normalised string."""
    evidence = {}
    for path in paths:
        data = json.loads(Path(path).read_text())
        records = data.get("records") if isinstance(data, dict) else data
        if not isinstance(records, list):
            raise ValueError(f"{path}: not an extractor artifact with records")
        for record in records:
            if not isinstance(record, dict):
                continue
            page = record.get("page_id") or record.get("document_id")
            if not page:
                continue
            parts = [record.get("document_text") or ""]
            for table in record.get("source_tables") or []:
                for row in table.get("source_rows") or []:
                    for cell in row.get("cells") or []:
                        parts.append(cell.get("evidence_text") or "")
            text = NON_WORD.sub(" ", " ".join(parts).casefold())
            evidence[page] = evidence.get(page, "") + " " + text
    return evidence


def settled_values(document):
    """Every value this document already has settled, normalised for comparison."""
    values = set()
    for entry in (document.get("fields") or {}).values():
        if isinstance(entry, dict) and entry.get("accepted"):
            value = comparable(entry.get("value"))
            if value is not None:
                values.add(value)
    return values


def break_tie_locally(cell_votes, settled):
    """Return the one tied reading this document already states elsewhere."""
    matched = [
        value
        for value in {str(item) for item in cell_votes.values()}
        if comparable(value) in settled
    ]
    return matched[0] if len(matched) == 1 else None


def break_tie(cell_votes, page_text):
    """Return the one reading the extractor saw, when it saw exactly one."""
    if not page_text:
        return None
    seen = []
    for value in {str(item) for item in cell_votes.values()}:
        needle = NON_WORD.sub(" ", value.casefold()).strip()
        if needle and needle in page_text:
            seen.append(value)
    return seen[0] if len(seen) == 1 else None


def tally(cell_votes):
    """Return the outright leader and how many vendors read it, or None."""
    counts = collections.Counter(comparable(value) for value in cell_votes.values())
    if not counts:
        return None, 0, "no_vendor_read_this_field"
    leader, count = counts.most_common(1)[0]
    if sum(1 for value in counts.values() if value == count) > 1:
        # Not a resolution. Two vendors reading different values is the finding.
        return None, count, "vendors_tied"
    original = next(value for value in cell_votes.values() if comparable(value) == leader)
    return original, count, None


def vote(documents, votes, minimum_vendors=DEFAULT_MINIMUM_VENDORS, evidence=None):
    """Resolve open fields the retained readings already agree on.

    Where the vendors tie, an independent non-LLM extractor breaks it if it read
    exactly one of the tied values off that page -- and the resolution is
    labelled as the weaker evidence it is, never as vendor agreement.
    """
    resolutions, unresolved = [], []
    counts = collections.Counter()
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("every record must be an object")
        document_id = document.get("document_id")
        settled = settled_values(document)
        for name, entry in sorted((document.get("fields") or {}).items()):
            if not isinstance(entry, dict) or entry.get("accepted"):
                continue
            counts["open"] += 1
            cell = votes.get((document_id, name), {})
            value, agreeing, reason = tally(cell)
            record = {
                "document_id": document_id,
                "field": name,
                "vendors_read": sorted(cell),
                "agreeing_vendors": agreeing,
            }
            if value is None and reason == "vendors_tied":
                local = break_tie_locally(cell, settled)
                if local is not None:
                    counts["tie_broken_within_document"] += 1
                    resolutions.append(
                        {**record, "resolved_value": local, "resolved_by": "same_document"}
                    )
                    continue
            if value is None and reason == "vendors_tied" and evidence is not None:
                broken = break_tie(cell, evidence.get(document_id))
                if broken is not None:
                    counts["tie_broken_by_extractor"] += 1
                    resolutions.append(
                        {**record, "resolved_value": broken, "resolved_by": "independent_extractor"}
                    )
                    continue
            if value is None or agreeing < minimum_vendors:
                record["reason"] = reason or "below_minimum_vendor_agreement"
                counts[record["reason"]] += 1
                unresolved.append(record)
                continue
            counts[f"agreed_by_{agreeing}_vendors"] += 1
            resolutions.append(
                {**record, "resolved_value": value, "resolved_by": "vendor_agreement"}
            )
    return resolutions, unresolved, counts


def apply_resolutions(documents, resolutions):
    """Append each agreed value to its cell, retaining what consensus recorded."""
    index = {(item["document_id"], item["field"]): item for item in resolutions}
    applied = 0
    output = []
    for document in documents:
        fields = dict(document.get("fields") or {})
        touched = False
        for name, entry in list(fields.items()):
            item = index.get((document.get("document_id"), name))
            if not item or not isinstance(entry, dict):
                continue
            applied += 1
            touched = True
            fields[name] = {
                **entry,
                "value": item["resolved_value"],
                "accepted": True,
                # Named for what it is, and carrying how many vendors agreed:
                # two and three are different evidence and the difference is
                # measurable, so it must survive onto the row.
                "acceptance": {
                    "accepted_by": {
                        "independent_extractor": TIEBREAK_ACCEPTED_BY,
                        "same_document": LOCAL_ACCEPTED_BY,
                    }.get(item.get("resolved_by"), ACCEPTED_BY),
                    "agreeing_vendors": item["agreeing_vendors"],
                    "vendors_read": item["vendors_read"],
                },
            }
        updated = {**document, "fields": fields}
        if touched:
            # The views attribution and arithmetic read carry the agreed value
            # too. Written to the fields alone, 3,905 of them never reached those
            # controls on the commission run.
            refresh_views(updated)
        output.append(updated)
    return output, applied


def pages_in(manifests):
    """Read the documents a subset read covers from its ingestion manifests.

    A subset re-read's handoffs hold readings for its own pages alone. Voted over
    the whole corpus, every other document's open field comes back as
    `no_vendor_read_this_field`, and the vote's counts describe documents it
    never had a reading for.
    """
    ids = set()
    for manifest in manifests:
        pages = json.loads(Path(manifest).read_text()).get("pages") or []
        found = {
            str(page[key]) for page in pages for key in ("page_id", "document_id") if page.get(key)
        }
        if not found:
            raise ValueError(f"a manifest that names no page scopes nothing: {manifest}")
        ids |= found
    return ids


def main(argv=None):
    """Write the vote proposal, and optionally the amended records."""
    load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", help="Retained record artifact whose open fields are voted on.")
    parser.add_argument(
        "handoffs",
        nargs="+",
        help=(
            "Retained provider handoffs. Pass every reading the run holds: each vendor "
            "votes once however many lanes it ran, and a vendor the run never read with "
            "cannot be counted."
        ),
    )
    parser.add_argument("--out", required=True, help="New vote artifact path.")
    parser.add_argument("--exceptions", required=True, help="New unresolved-field artifact path.")
    parser.add_argument("--records-out", help="Apply the resolutions to a new record artifact.")
    parser.add_argument(
        "--minimum-vendors",
        type=int,
        default=DEFAULT_MINIMUM_VENDORS,
        help=(
            "How many independent vendors must read the same value. Below two there is "
            "no agreement to speak of, and the measured accuracy differs by band: two "
            "vendors scored 96.3%% against held-out truth and three scored 100%%."
        ),
    )
    parser.add_argument(
        "--tiebreak-with",
        action="append",
        default=[],
        metavar="ARTIFACT",
        help=(
            "Repeatable non-LLM extractor artifact. Where the vendors tie, a reading "
            "the extractor read off that page and the other did not breaks it. Labelled "
            "tie_broken_by_independent_extractor, never vendor agreement: presence "
            "proves the string is printed on the page, not that it belongs in that cell."
        ),
    )
    parser.add_argument(
        "--pages",
        action="append",
        default=[],
        metavar="MANIFEST",
        help=(
            "Repeatable ingestion manifest of a subset read. Only the documents on its "
            "pages are voted on and counted; every other document passes to "
            "--records-out unchanged. Use it when the handoffs read only those pages."
        ),
    )
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args(argv)
    try:
        if args.minimum_vendors < 2:
            raise ValueError("minimum vendors must be at least 2; one reading is not agreement")
        data = json.loads(Path(args.records).read_text())
        documents = data.get("documents") if isinstance(data, dict) else None
        if not isinstance(documents, list):
            raise ValueError("record artifact carries no document list")
        votes, vendors = collect_votes(args.handoffs)
        if len(vendors) < args.minimum_vendors:
            raise ValueError(
                f"the supplied handoffs carry {len(vendors)} distinct vendor(s), below the "
                f"{args.minimum_vendors} required; lanes of one vendor are one reading"
            )
        evidence = extractor_evidence(args.tiebreak_with) if args.tiebreak_with else None
        scope = pages_in(args.pages) if args.pages else None
        # A malformed document is kept in scope so `vote` refuses it as before.
        voted = [
            document
            for document in documents
            if scope is None
            or not isinstance(document, dict)
            or str(document.get("document_id")) in scope
        ]
        if scope is not None and not voted:
            raise ValueError("no document in the records is on the supplied manifests' pages")
        resolutions, unresolved, counts = vote(voted, votes, args.minimum_vendors, evidence)
        summary = {
            "generated_at": datetime.now(UTC).isoformat(),
            "artifact_type": ARTIFACT_TYPE,
            "documents": len(voted),
            "documents_passed_through": len(documents) - len(voted),
            "pages_manifests": [Path(path).name for path in args.pages],
            "vendors": {name: sorted(paths) for name, paths in sorted(vendors.items())},
            "minimum_vendors": args.minimum_vendors,
            "open_fields": counts["open"],
            "resolved": len(resolutions),
            "unresolved": len(unresolved),
            "tie_broken_within_document": counts["tie_broken_within_document"],
            "tie_broken_by_extractor": counts["tie_broken_by_extractor"],
            "extractor_evidence_pages": len(evidence or {}),
            "by_agreement": {
                key: value for key, value in sorted(counts.items()) if key.startswith("agreed_by_")
            },
            "unresolved_reasons": {
                key: value
                for key, value in sorted(counts.items())
                if key
                in {"vendors_tied", "no_vendor_read_this_field", "below_minimum_vendor_agreement"}
            },
            "findings": [
                "A vote counts vendors, not lanes: two lanes of one vendor are one reading.",
                "A tie is retained as an open finding, not resolved to either side.",
                "This is vendor agreement over retained readings and clears no control.",
            ],
        }
        empty_output_path(Path(args.out)).write_text(
            json.dumps({"summary": summary, "resolutions": resolutions}, indent=2) + "\n"
        )
        empty_output_path(Path(args.exceptions)).write_text(
            json.dumps({"summary": {"count": len(unresolved)}, "exceptions": unresolved}, indent=2)
            + "\n"
        )
        applied = None
        if args.records_out:
            amended, applied = apply_resolutions(documents, resolutions)
            empty_output_path(Path(args.records_out)).write_text(
                json.dumps({**data, "documents": amended}, indent=2) + "\n"
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Multi-engine vote failed: {exc}")
    if not args.quiet:
        print(f"Vendors counted: {', '.join(sorted(summary['vendors']))}")
        if args.pages:
            print(
                f"Documents on the manifests' pages: {summary['documents']}; "
                f"passed through unchanged: {summary['documents_passed_through']}"
            )
        print(f"Open fields: {summary['open_fields']}")
        for key, value in summary["by_agreement"].items():
            print(f"  {value:6d}  {key}")
        for key, value in summary["unresolved_reasons"].items():
            print(f"  {value:6d}  {key}")
        if applied is not None:
            print(f"  applied to records: {applied}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
