#!/usr/bin/env python3
"""Create an exhaustive, de-duplicated final client-review package from pipeline artifacts."""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from review_status import ARITHMETIC_CLEAR, REVIEW_CLEAR
from runtime_config import load_project_env

SCHEMA_VERSION = "1.0"
REVIEW_FIELDS = (
    "priority",
    "document_id",
    "page_id",
    "region_id",
    "field",
    "reason",
    "review_source",
    "disposition",
)


def load(path):
    """Load one JSON review artifact with an explicit error for malformed input."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Review artifact {path} must be an object")
    return data


def review_key(item):
    """Deduplicate only exact same review targets while retaining distinct causes.

    An absent part and an explicitly null one are the same absence. Spelling the
    second ``"None"`` made two names for one finding, which now also means two
    ``review_item_id`` values for one finding.
    """
    return tuple(
        "" if item.get(key) is None else str(item.get(key))
        for key in ("document_id", "page_id", "region_id", "field", "reason")
    )


def review_item_id(item):
    """Name a queue item by the same key that deduplicates it.

    Every finding in the queue is unique under :func:`review_key`, so that key
    is already this item's identity -- it just never became a field anybody
    downstream could cite. Without one, ``review_grouping.py`` builds an empty
    ``source_item_ids`` for every group, and ``client_decision_compile.py``
    requires a change's ``affected_review_item_ids`` to be non-empty *and* a
    subset of its group's. On the commission run that was 0 of 35,745 items
    carrying an id, so no client answer could ever compile into a change: the
    lane asked the questions, the client answered them, and the authorization
    step had nothing to bind them to.

    The id is a digest of the key rather than a position, because a position is
    not an identity. Rebuilding the queue with one more input artifact renumbers
    every item after the insertion point, which would silently repoint a
    client's already-authorized change at a different finding. A digest of the
    finding itself survives the rebuild, and an item that genuinely changed gets
    a new id rather than quietly inheriting an old approval.
    """
    key = "\x1f".join(review_key(item))
    return "review-item-" + hashlib.sha256(key.encode()).hexdigest()[:16]


def priority(item):
    """Prioritize financial and provenance hazards above ordinary disagreement."""
    reason = str(item.get("reason", ""))
    if any(
        token in reason
        for token in ("financial", "handwriting", "arithmetic", "jbig2", "missing_field_provenance")
    ):
        return "critical"
    if any(
        token in reason for token in ("consensus", "reassembly", "identifier", "date", "currency")
    ):
        return "high"
    return "normal"


# A field path that is itself a handwriting observation. Matched structurally
# rather than as a substring of prose, because a finding is not about
# handwriting merely because its explanation mentions it.
HANDWRITING_FIELD = re.compile(r"(?:^|\.)(?:has_)?handwriting(?:_\w+)?(?:\.|$)")


def handwriting_comment_only(item):
    """Identify extraction-lane handwriting observations that are comments.

    ``comment_only`` says a handwritten *reading* is a non-blocking observation.
    It does not say the gate may drop a finding whose explanation happens to use
    the word, and that is what the substring test it replaced actually did. On
    the commission run it suppressed 2,590 findings it had no business
    suppressing:

    * 838 ``google_handwriting_region_unreadable`` exceptions -- the OCR lane
      reporting that it could not read a region at all. A lane failure is not an
      observation about a handwritten value, and this one carries no field.
    * 1,068 findings a control had marked ``blocking: true``. Consensus reports
      these with ``is_handwritten: false``: it did not classify them as
      handwritten fields, so the policy branch inside ``reconcile_field`` never
      applied and consensus meant them to block. A second suppression here
      silently overrides a control's own verdict.
    * ~684 findings on ordinary business fields -- ``commission_amount``,
      ``brand_name``, ``sales_representative_name``, ``identity`` -- whose prose
      described a printed value obscured by a handwritten mark. The finding is
      about the printed value.

    So: never override a control that said blocking, never suppress a finding
    that names no field, and match the field path rather than the prose.
    """
    if os.environ.get("HANDWRITING_POLICY", "comment_only").casefold() != "comment_only":
        return False
    if item.get("blocking") is True:
        return False
    if item.get("is_handwritten"):
        return True
    field = str(item.get("field") or "")
    return bool(field) and bool(HANDWRITING_FIELD.search(field))


# Every collection or key that makes an artifact review-bearing. An artifact
# carrying one of these was examined by this gate, whether or not it contributed
# an item; an artifact carrying none of them was not examined at all.
REVIEW_BEARING_KEYS = (
    "exceptions",
    "reassembly_exceptions",
    "duplicate_candidates",
    "register",
    "pending_adjudication",
    "amendments",
    "amendment_proposals",
    "documents",
    "gate_status",
)


def review_bearing(data):
    """Report whether this artifact is one the final gate can examine."""
    if not isinstance(data, dict):
        return False
    if data.get("artifact_type") == "table_comprehension_quality_summary":
        return True
    return any(key in data for key in REVIEW_BEARING_KEYS)


def review_items(data):
    """Extract only artifacts that have not reached a clear, non-review state."""
    if data.get("artifact_type") == "table_comprehension_quality_summary":
        # Retained diagnostics are evidence for operators, not client-review work items.
        return []
    items = []
    for name in (
        "exceptions",
        "reassembly_exceptions",
        "duplicate_candidates",
        "register",
        "pending_adjudication",
    ):
        collection = data.get(name, [])
        if not isinstance(collection, list):
            raise ValueError(f"{name} must be a list when present")
        for item in collection:
            if not isinstance(item, dict):
                raise ValueError(f"{name} entries must be objects")
            if item.get("blocking") is False or item.get("client_review_required") is False:
                continue
            if name == "register" and item.get("is_failure") is False:
                # A register entry carrying a Phase 0 reason code is an answer,
                # not a question: `attribution.py` sets `is_failure` false only
                # for a code in its DISPOSITION_CODES. Queued, every answered
                # document was held out of canonical by its own answer.
                continue
            if handwriting_comment_only(item):
                continue
            # Some upstream exception artifacts preserve their native
            # terminology (for example consensus uses ``cause``) while
            # the canonical final-review contract requires ``reason``.
            # Derive only the missing contract field; retain the original
            # evidence and any native cause/flag/rule fields unchanged.
            reason = item.get("reason") or item.get("cause")
            if not reason:
                flag = item.get("flag")
                rule = item.get("rule")
                if flag:
                    reason = f"{name}_{flag}"
                elif rule:
                    reason = f"{name}_{rule}"
            items.append(
                {
                    **item,
                    "reason": reason or f"{name}_review_required",
                    # A lane that named the control it came from is the authority
                    # on that. Overwriting it with the collection name ("exceptions")
                    # loses the only record of which control raised the finding,
                    # which is what the review lane's per-control thresholds read.
                    "review_source": item.get("review_source") or name,
                }
            )
    for name in ("amendments", "amendment_proposals"):
        collection = data.get(name, [])
        if not isinstance(collection, list):
            raise ValueError(f"{name} must be a list when present")
        for item in collection:
            if not isinstance(item, dict):
                raise ValueError(f"{name} entries must be objects")
            items.append(
                {
                    **item,
                    "review_source": item.get("review_source") or name,
                    "reason": item.get("reason", "amendment_requires_client_review"),
                }
            )
    for document in data.get("documents", []):
        if not isinstance(document, dict):
            raise ValueError("documents entries must be objects")
        status = document.get("review_status") or document.get("field_validation_status")
        arithmetic = document.get("arithmetic_status")
        # `canonical_export.py` admits every status in REVIEW_CLEAR. Filing a
        # review item against one of them made the export refuse the document
        # for having an open item -- refusing it for having been reviewed.
        if status and status not in REVIEW_CLEAR and status != "clear":
            if handwriting_comment_only(document):
                continue
            items.append(
                {
                    "document_id": document.get("document_id"),
                    "reason": f"document_status_{status}",
                    "review_source": "documents",
                }
            )
        # The list the canonical export reads. Filing an item for `not_applicable`
        # refused a document for arithmetic that does not apply to it.
        if arithmetic and arithmetic not in ARITHMETIC_CLEAR:
            items.append(
                {
                    "document_id": document.get("document_id"),
                    "reason": f"arithmetic_{arithmetic}",
                    "review_source": "documents",
                }
            )
    if data.get("gate_status") == "blocked":
        items.append({"reason": "upstream_gate_blocked", "review_source": "gate_status"})
    return items


RESOLUTION_ARTIFACTS = ("classification_consensus_v1",)


def resolutions(paths):
    """Read the downstream controls that already answered a raw adapter proposal.

    The gate is handed every review-bearing artifact, which is correct, and that
    includes the adapters' raw `document_type` proposals. On one run it also had
    `classification_consensus_04.json`, the control that resolved 686 of those
    716 documents -- and nothing reconciled the two, so 1,364 questions the run
    had already answered were put to the client anyway.

    Only an exact answer reconciles. A proposal that names a *different* type
    than the accepted one is a real disagreement with a resolved control and
    stays blocking.
    """
    accepted = {}
    for path in paths:
        data = json.loads(Path(path).read_text())
        if not isinstance(data, dict) or data.get("artifact_type") not in RESOLUTION_ARTIFACTS:
            raise ValueError(
                f"not a resolution artifact ({', '.join(RESOLUTION_ARTIFACTS)}): {path}"
            )
        for entry in data.get("accepted") or []:
            document = entry.get("document_id")
            if not document:
                continue
            accepted[document] = {
                "document_type": entry.get("document_type"),
                "evidence": entry.get("evidence"),
                "resolved_by": Path(path).name,
            }
    if not accepted:
        # Rule 9: a reconciliation that reconciled nothing has not run. Silently
        # returning an empty map would leave the queue looking reconciled.
        raise ValueError(
            "the supplied resolution artifacts accepted no document; a "
            "reconciliation that processed nothing has not passed"
        )
    return accepted


def reconciled_by(item, accepted):
    """Say which resolved control already answered this item, if any."""
    if item.get("field") != "document_type":
        return None
    answer = accepted.get(item.get("document_id"))
    if answer is None or item.get("candidate_value") != answer["document_type"]:
        return None
    return answer


def governed_pages(pairs, paths):
    """Read which earlier findings a later re-read supersedes, and on which pages.

    A subset re-read replaces a page's readings and the record carries the new
    ones, but the earlier readings' findings on that page are still in the
    artifacts the gate is handed -- so the queue asks about readings the record
    no longer holds. Each pair names an input artifact by file name and the
    manifest of the re-read that governs its pages.
    """
    names = {Path(path).name for path in paths}
    governed = {}
    for artifact, manifest in pairs:
        name = Path(artifact).name
        if name not in names:
            # Rule 9: a supersession that matched no input removed nothing, and
            # reads exactly like one that did.
            raise ValueError(f"--supersede names an artifact that is not an input: {name}")
        pages = json.loads(Path(manifest).read_text()).get("pages") or []
        ids = {
            str(page[key]) for page in pages for key in ("page_id", "document_id") if page.get(key)
        }
        if not ids:
            raise ValueError(f"a re-read manifest that names no page governs nothing: {manifest}")
        governed.setdefault(name, {}).update(dict.fromkeys(ids, Path(manifest).name))
    return governed


VOTE_ARTIFACT = "multi_engine_vote_v1"


def vote_settlements(pairs, paths):
    """Read which findings a vendor vote settled, and in which input artifact.

    A vote accepts a field the consensus left open and the record carries the
    agreed value, but the consensus finding on that field is still in the
    artifact the gate is handed -- so the queue asks about a value the record
    has already accepted. On the commission run every one of the 532 fields the
    vote over the OSALL pages settled was still queued. Each pair names the vote
    artifact and one input by file name; only that input's findings on a settled
    field are retained as settled, so a validation or arithmetic finding on the
    same field is still asked.
    """
    names = {Path(path).name for path in paths}
    settled = {}
    for vote_path, artifact in pairs:
        name = Path(artifact).name
        if name not in names:
            # Rule 9: a settlement that matched no input settled nothing, and
            # reads exactly like one that did.
            raise ValueError(f"--settled-by-vote names an artifact that is not an input: {name}")
        data = json.loads(Path(vote_path).read_text())
        summary = data.get("summary") if isinstance(data, dict) else None
        if not isinstance(summary, dict) or summary.get("artifact_type") != VOTE_ARTIFACT:
            raise ValueError(f"not a vote artifact ({VOTE_ARTIFACT}): {vote_path}")
        fields = settled.setdefault(name, {})
        for entry in data.get("resolutions") or []:
            fields[(str(entry.get("document_id") or ""), str(entry.get("field") or ""))] = {
                "settled_by": Path(vote_path).name,
                "resolved_value": entry.get("resolved_value"),
                "resolved_by": entry.get("resolved_by"),
                "agreeing_vendors": entry.get("agreeing_vendors"),
            }
    return settled


DISPOSITIONS_ARTIFACT = "operator_item_dispositions_v1"


def operator_dispositions(paths):
    """Read the queue items an operator authorized a disposition for, by item id.

    Some findings no control will ever answer: a lane that returned no
    candidate, a provider's own review flag, a handwritten region under a
    comment-only policy. Each stays in the queue, and each holds a document out
    of canonical, until someone decides it. This reads those decisions. Every
    entry names one `review_item_id` -- the digest of the finding itself, so it
    cannot drift onto another finding when the queue is rebuilt -- and the rule
    that disposes of it. Only an operator-authorized artifact is read, so no
    model output and no client note can dispose of a finding by itself.
    """
    disposed = {}
    for path in paths:
        data = json.loads(Path(path).read_text())
        if not isinstance(data, dict) or data.get("artifact_type") != DISPOSITIONS_ARTIFACT:
            raise ValueError(
                f"not an operator disposition artifact ({DISPOSITIONS_ARTIFACT}): {path}"
            )
        if data.get("authorization_status") != "operator_authorized" or not data.get(
            "authorization_id"
        ):
            raise ValueError(f"an operator-authorized disposition artifact is required: {path}")
        entries = data.get("dispositions")
        if not isinstance(entries, list) or not entries:
            # Rule 9: a disposition artifact naming nothing disposed of nothing,
            # and reads exactly like one that did.
            raise ValueError(
                f"a disposition artifact that names no item disposes of nothing: {path}"
            )
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or not entry.get("review_item_id")
                or not entry.get("rule")
            ):
                raise ValueError(f"each disposition names a review_item_id and a rule: {path}")
            disposed[str(entry["review_item_id"])] = {
                "disposed_by": Path(path).name,
                "disposition_authorization_id": data["authorization_id"],
                "disposition_rule": entry["rule"],
                "disposition_evidence": entry.get("evidence"),
            }
    return disposed


def consolidate(paths, accepted=None, governed=None, settled=None, disposed=None):
    """Create the final client queue; no item is silently removed or resolved.

    A finding a governing re-read supersedes is moved, not dropped: it joins the
    reconciled list with disposition `superseded_by_re_read` and the manifest
    that governs its page. A finding on a field a vendor vote settled joins it
    as `settled_by_vendor_vote`, naming the vote and the value it accepted. A
    finding an operator authorized a disposition for joins it as
    `dispositioned_by_operator`, naming the artifact, the authorization and the
    rule.
    """
    accepted = accepted or {}
    governed = governed or {}
    settled = settled or {}
    disposed = disposed or {}
    unique, sources, examined, reconciled = {}, [], [], []
    for path in paths:
        try:
            data = load(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            # This is the gate, and it is handed a shell glob. One unreadable
            # member of that glob aborted the whole command -- a real run died on
            # `[Errno 21] Is a directory` and produced no queue at all, so an
            # operator had nothing rather than everything-but-one-file.
            #
            # An input this cannot read is not zero findings. It becomes a
            # blocking item naming the file, so the queue is still built and the
            # unread artifact is impossible to mistake for a clean one.
            sources.append(Path(path).name)
            item = {
                "document_id": "",
                "page_id": "",
                "region_id": "",
                "field": "source_artifact",
                "reason": f"final_review_queue_input_unreadable: {exc}",
                "review_source": "final_review_queue",
                "source_artifact": Path(path).name,
                "priority": "critical",
                "disposition": "client_review_required",
            }
            item["review_item_id"] = review_item_id(item)
            unique.setdefault(review_key(item), item)
            continue
        sources.append(Path(path).name)
        if review_bearing(data):
            examined.append(Path(path).name)
        for item in review_items(data):
            # Which artifact an item came from is the most reliable signal of
            # which control raised it: operators name these files after the
            # control, while the collection name inside them is generic.
            item.setdefault("source_artifact", Path(path).name)
            item["priority"] = priority(item)
            item["disposition"] = "client_review_required"
            for field in REVIEW_FIELDS:
                value = item.get(field)
                item[field] = "" if value is None else str(value)
            item["review_item_id"] = review_item_id(item)
            answer = reconciled_by(item, accepted)
            if answer is not None:
                # Not dropped: moved, with the control that answered it named, so
                # the reconciliation is auditable from the queue itself.
                reconciled.append({**item, **answer, "disposition": "already_resolved"})
                continue
            pages = governed.get(Path(path).name, {})
            manifest = pages.get(item["document_id"]) or pages.get(item["page_id"])
            if manifest:
                reconciled.append(
                    {**item, "disposition": "superseded_by_re_read", "governed_by": manifest}
                )
                continue
            vote = settled.get(Path(path).name, {}).get((item["document_id"], item["field"]))
            if vote:
                reconciled.append({**item, **vote, "disposition": "settled_by_vendor_vote"})
                continue
            disposition = disposed.get(item["review_item_id"])
            if disposition:
                reconciled.append(
                    {**item, **disposition, "disposition": "dispositioned_by_operator"}
                )
                continue
            unique.setdefault(review_key(item), item)
    order = {"critical": 0, "high": 1, "normal": 2}
    items = sorted(unique.values(), key=lambda item: (order[item["priority"]], review_key(item)))
    if not examined:
        # Rule 9: this is the final gate. Reporting it clear because nothing was
        # examined is the one outcome it must never produce -- an operator who
        # points it at the wrong files, or at a stage that never ran, would read
        # a clean gate as authorization.
        raise ValueError(
            "no supplied artifact carries review-bearing content ("
            + ", ".join(sorted(sources))
            + "); the final queue cannot report a clear gate over nothing"
        )
    return items, sources, reconciled


def review_cards(items):
    """Group exhaustive field findings into document-level client decision cards."""
    grouped = {}
    order = {"critical": 0, "high": 1, "normal": 2}
    for index, item in enumerate(items):
        document_id = item.get("document_id") or item.get("page_id") or "run"
        card = grouped.setdefault(
            document_id,
            {
                "card_id": f"document:{document_id}",
                "document_id": document_id if document_id != "run" else "",
                "page_ids": set(),
                "priorities": [],
                "reasons": set(),
                "fields": set(),
                "item_indexes": [],
            },
        )
        if item.get("page_id"):
            card["page_ids"].add(str(item["page_id"]))
        card["priorities"].append(item.get("priority", "normal"))
        card["reasons"].add(str(item.get("reason", "review_required")))
        if item.get("field"):
            card["fields"].add(str(item["field"]))
        card["item_indexes"].append(index)
    cards = []
    for card in grouped.values():
        priority_value = min(card["priorities"], key=lambda value: order.get(value, 2))
        cards.append(
            {
                "card_id": card["card_id"],
                "document_id": card["document_id"],
                "page_ids": sorted(card["page_ids"]),
                "priority": priority_value,
                "item_count": len(card["item_indexes"]),
                "item_indexes": card["item_indexes"],
                "reasons": sorted(card["reasons"]),
                "fields": sorted(card["fields"]),
                "disposition": "client_review_required",
            }
        )
    return sorted(cards, key=lambda card: (order.get(card["priority"], 2), card["card_id"]))


def main():
    parser = argparse.ArgumentParser(
        description="Consolidate unresolved pipeline findings for final client review."
    )
    parser.add_argument(
        "artifacts", nargs="+", help="exception/proposal/proof/validation JSON artifacts"
    )
    parser.add_argument("--out", required=True, help="final client-review queue JSON")
    parser.add_argument(
        "--resolved-by",
        action="append",
        default=[],
        metavar="ARTIFACT",
        help=(
            "Repeatable accepted-classification artifact. A raw adapter "
            "`document_type` proposal for a document this control already "
            "accepted, naming the same type, is retained as reconciled instead "
            "of being asked again. A proposal naming a different type stays "
            "blocking."
        ),
    )
    parser.add_argument(
        "--supersede",
        nargs=2,
        action="append",
        default=[],
        metavar=("ARTIFACT", "MANIFEST"),
        help=(
            "Repeatable. Findings in the input named ARTIFACT (by file name) on the "
            "pages a subset re-read's MANIFEST lists are retained as superseded "
            "rather than queued, because the record carries the re-read's readings. "
            "Supply the re-read's own findings as inputs."
        ),
    )
    parser.add_argument(
        "--settled-by-vote",
        nargs=2,
        action="append",
        default=[],
        metavar=("VOTE", "ARTIFACT"),
        help=(
            "Repeatable. Findings in the input named ARTIFACT (by file name) on a field "
            "the multi_engine_vote.py artifact VOTE settled are retained as "
            "settled_by_vendor_vote rather than queued, because the record carries the "
            "agreed value. Name the artifact holding the open fields the vote was run on."
        ),
    )
    parser.add_argument(
        "--dispositions",
        action="append",
        default=[],
        metavar="ARTIFACT",
        help=(
            "Repeatable. An operator-authorized operator_item_dispositions_v1 artifact naming "
            "queue items by review_item_id, each with the rule and evidence that dispose of it. A "
            "named item is retained as dispositioned_by_operator rather than queued. An artifact "
            "that is not operator-authorized, or names no item, is refused; a disposition that "
            "matches no item is counted."
        ),
    )
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args()
    load_project_env()
    try:
        accepted = resolutions(args.resolved_by) if args.resolved_by else {}
        governed = governed_pages(args.supersede, args.artifacts)
        settled = vote_settlements(args.settled_by_vote, args.artifacts)
        disposed = operator_dispositions(args.dispositions) if args.dispositions else {}
        items, sources, reconciled = consolidate(
            args.artifacts, accepted, governed, settled, disposed
        )
        cards = review_cards(items)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Final review queue failed: {exc}")
    dispositioned = {
        r["review_item_id"] for r in reconciled if r["disposition"] == "dispositioned_by_operator"
    }
    summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_artifacts": sources,
        "client_review_items": len(items),
        "client_review_cards": len(cards),
        "reconciled_items": sum(1 for r in reconciled if r["disposition"] == "already_resolved"),
        "superseded_items": sum(
            1 for r in reconciled if r["disposition"] == "superseded_by_re_read"
        ),
        "superseded_by": [list(pair) for pair in args.supersede],
        "settled_by_vote_items": sum(
            1 for r in reconciled if r["disposition"] == "settled_by_vendor_vote"
        ),
        "settled_by_vote": [list(pair) for pair in args.settled_by_vote],
        "dispositioned_items": sum(
            1 for r in reconciled if r["disposition"] == "dispositioned_by_operator"
        ),
        "disposition_artifacts": [Path(path).name for path in args.dispositions],
        # Rule 9: a disposition naming no finding in this queue disposed of
        # nothing. Counted, so a stale or mistyped artifact is visible.
        "dispositions_matching_no_item": len(set(disposed) - dispositioned),
        "resolution_artifacts": [Path(path).name for path in args.resolved_by],
        "gate_status": "blocked_pending_client_review" if items else "clear",
        "findings": [
            "This queue is exhaustive for the supplied artifacts; it does not auto-close an exception.",
            "A reconciled item was answered by a named control in this run and is retained, not closed.",
        ],
    }
    try:
        # A run directory is organised by phase, so `RUN/review/` usually does
        # not exist when the gate is the first thing to write there. Refusing
        # with a bare ENOENT made the one command a clear result is claimed from
        # the one an operator had to mkdir for. The "x" mode below still refuses
        # to overwrite a queue that already exists.
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with Path(args.out).open("x") as stream:
            stream.write(
                json.dumps(
                    {
                        "summary": summary,
                        "items": items,
                        "review_cards": cards,
                        "reconciled": reconciled,
                    },
                    indent=2,
                )
                + "\n"
            )
    except OSError as exc:
        sys.exit(f"Final review queue failed: {exc}")
    if not args.quiet:
        print(f"Final client-review items: {len(items)} ({len(cards)} document cards)")
        if summary["reconciled_items"]:
            print(f"Retained as already resolved: {summary['reconciled_items']}")
        if summary["superseded_items"]:
            print(f"Retained as superseded by a re-read: {summary['superseded_items']}")
        if summary["settled_by_vote_items"]:
            print(f"Retained as settled by a vendor vote: {summary['settled_by_vote_items']}")
        if summary["dispositioned_items"]:
            print(f"Retained as dispositioned by an operator: {summary['dispositioned_items']}")
        if summary["dispositions_matching_no_item"]:
            print(
                f"Dispositions matching no queue item: {summary['dispositions_matching_no_item']}"
            )


if __name__ == "__main__":
    main()
