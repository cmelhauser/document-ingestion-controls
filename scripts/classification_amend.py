#!/usr/bin/env python3
"""Carry an operator-authorized client decision into document classification.

`classification_consensus.py` accepts a document type only where two independent
model vendors already agreed. That is the right rule, and it leaves a gap: a
corpus where the vendors could not agree keeps `document_family: unknown`, every
later lane inherits it, and the client is asked to confirm the type. When the
client answers, nothing carries the answer back.

One run made that concrete. 166 of 716 documents were unresolved; the client
confirmed the type for 136 of them; the answer was compiled, authorized, and
written into the approved template registry -- and not one document's
classification changed, because no control reads that registry. The approval was
real and inert.

This command closes that one step. It reads an operator-authorized compiled plan
and appends a classification for each document its authorized patches name. Two
change types reach it, because the review lane compiles two different questions:
a client approving a layout produces `template_registry_rule`, which carries the
family on its approved template, and a client confirming what a document *is*
produces `document_type_rule`, which carries the family as the proposed value and
has no layout fingerprint at all. Both name a document family, so both apply
here; an authorization holding neither is refused by name.

Three refusals keep it from becoming an override channel:

* an authorization that is not `operator_authorized` is refused, so a client
  note alone can never reach classification;
* a document accepted on independent vendor agreement is refused rather than
  replaced -- vendor agreement is evidence, and a client assertion does not
  outrank it;
* a document whose type came from an *earlier client decision* is a different
  case, and was being refused with the same message. Nothing about it is vendor
  agreement: the same authority is correcting itself, which is the normal
  correction path and the reason amendments are append-only. It is still not
  something to do by accident, so it needs `--supersede-prior-client-decisions`;
  the refusal without that flag reports the whole set and the retyping it would
  perform, so an operator decides once rather than one document at a time;
* a document outside the authorized patches' own impact lists is refused, so
  the blast radius is exactly what the operator authorized.

Amended entries carry `evidence: operator_authorized_client_decision` and the
authorization id, so they never read as vendor agreement. The input artifact is
not modified; this writes a new one.

Appending an acceptance is only half the job. The classification exception that
sent the document to the client is still standing, and `final_review_queue.py`
builds the gate from those exception artifacts -- so without the second half the
client is asked the same question again after answering it. Pass
`--exceptions` and `--exceptions-out` to mark each resolved entry
`client_review_required: false`, which is the mechanism the gate already honours.
Nothing is deleted: the exception stays in the artifact, carrying the
authorization that resolved it.
"""

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from run_io import empty_output_path
from runtime_config import load_project_env

ARTIFACT_TYPE = "classification_consensus_v1"
DEFAULT_OUTLIER_FACTOR = 4.0
CLIENT_EVIDENCE = "operator_authorized_client_decision"
REGISTRY_CHANGE = "template_registry_rule"
# A client confirming what a document *is* answers a different question from a
# client approving a layout. Only the layout change had a consumer, so a run
# whose operator had signed 506 documents into a document type could compile the
# decision, bind the authorization, and then refuse at the apply step -- the
# refusal arriving after the signature, which is the wrong order to find it.
DOCUMENT_TYPE_CHANGE = "document_type_rule"
APPLICABLE_CHANGES = (REGISTRY_CHANGE, DOCUMENT_TYPE_CHANGE)


def load_classification(path):
    """Read a classification-consensus artifact and refuse another artifact type."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or data.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(f"not a {ARTIFACT_TYPE} artifact: {path}")
    if not isinstance(data.get("accepted"), list):
        raise ValueError("classification artifact requires an accepted list")
    return data


def authorized_patches(authorization):
    """Return the authorized registry patches, refusing an unauthorized plan."""
    if not isinstance(authorization, dict):
        raise ValueError("authorization must be a JSON object")
    if authorization.get("authorization_status") != "operator_authorized":
        raise ValueError("an operator-authorized compiled plan is required")
    authorized = set(authorization.get("authorized_patch_ids") or ())
    if not authorized:
        raise ValueError("authorization names no authorized patches")
    plan = authorization.get("compiled_plan")
    if not isinstance(plan, dict) or not isinstance(plan.get("patches"), list):
        raise ValueError("authorization does not carry a compiled plan")
    patches = [
        patch
        for patch in plan["patches"]
        if patch.get("patch_id") in authorized and patch.get("change_type") in APPLICABLE_CHANGES
    ]
    if not patches:
        raise ValueError(
            "authorization contains no authorized patches this control can apply; "
            f"expected one of: {', '.join(APPLICABLE_CHANGES)}"
        )
    return patches


def patch_family(patch):
    """Read the approved document family a patch carries.

    A document-type decision names the family directly: the client was asked what
    the document is, not what its layout looks like, so requiring a layout
    signature of it would demand a fingerprint the decision never had.
    """
    if patch.get("change_type") == DOCUMENT_TYPE_CHANGE:
        payload = patch.get("append_only_payload")
        family = payload.get("proposed_value") if isinstance(payload, dict) else None
        if not isinstance(family, str) or not family.strip():
            raise ValueError("document type patch requires a proposed_value naming the family")
        return family
    payload = patch.get("append_only_payload")
    if not isinstance(payload, dict):
        raise ValueError("registry patch requires an append-only payload")
    signature = payload.get("layout_signature")
    if not isinstance(signature, dict):
        raise ValueError("registry patch requires a layout_signature")
    family = signature.get("document_family")
    if not isinstance(family, str) or not family.strip():
        raise ValueError("registry patch layout_signature requires a document_family")
    return family


def resolve_exceptions(exceptions, resolved, authorization):
    """Mark each resolved document's exception answered, without removing it."""
    if not isinstance(exceptions, dict) or not isinstance(exceptions.get("exceptions"), list):
        raise ValueError("exceptions artifact requires an exceptions list")
    entries = []
    answered = 0
    for entry in exceptions["exceptions"]:
        if not isinstance(entry, dict):
            raise ValueError("each exception must be an object")
        if (
            entry.get("document_id") in resolved
            and entry.get("client_review_required") is not False
        ):
            answered += 1
            entries.append(
                {
                    **entry,
                    # The gate excludes an item explicitly marked as not requiring
                    # client review. The finding is retained, not deleted: it was
                    # true, and the authorization that answered it is named here.
                    "client_review_required": False,
                    "resolution": CLIENT_EVIDENCE,
                    "resolved_document_type": resolved[entry["document_id"]],
                    "authorization_id": authorization.get("authorization_id"),
                }
            )
            continue
        entries.append(entry)
    summary = dict(exceptions.get("summary") or {})
    summary["count"] = len(entries)
    summary["resolved_by_client_decision"] = answered
    summary["still_client_review_required"] = sum(
        1 for entry in entries if entry.get("client_review_required") is not False
    )
    return {**exceptions, "summary": summary, "exceptions": entries}, answered


def populated_field_counts(records):
    """Count how many fields each retained record actually carries a value for."""
    counts = {}
    for artifact in records:
        data = json.loads(Path(artifact).read_text())
        documents = data.get("documents") if isinstance(data, dict) else data
        if not isinstance(documents, list):
            raise ValueError(f"record artifact carries no document list: {artifact}")
        for document in documents:
            if not isinstance(document, dict):
                continue
            document_id = document.get("document_id")
            fields = document.get("fields")
            if document_id is None or not isinstance(fields, dict):
                continue
            counts[str(document_id)] = sum(
                1 for value in fields.values() if value not in (None, "", [], {})
            )
    return counts


def contradicted_by_content(document_ids, counts, factor):
    """Name the documents a group's answer was visibly not given about.

    A client answers one question for a whole group after seeing a few worked
    examples. Where a member carries far more extracted content than the group's
    typical member, the answer the client gave is not an answer about that
    member. On this corpus one group of 30 was answered "Blank Page. Can be
    ignored."; three of its members carried 46, 129 and 190 populated fields,
    two of them commission lines with amounts. Typing those from that answer
    would put "Blank Page. Can be ignored." on documents carrying money.

    Only the richer side is withheld, and that asymmetry is the point. A sparse
    document typed from a content-bearing group is not contradicted -- it is the
    same kind of document with less read off it. A content-bearing document
    typed from an answer given about empty ones is contradicted by its own
    extraction. Comparing against the group's own median rather than a fixed
    threshold keeps this from needing to know what any answer means.

    This never decides what the document is. It withholds it from the amendment
    so it stays an open question, which is the outcome Rule 10 asks for.
    """
    known = [
        (str(document_id), counts[str(document_id)])
        for document_id in document_ids
        if str(document_id) in counts
    ]
    if len(known) < 2:
        return [], None
    median = statistics.median(count for _, count in known)
    if median <= 0:
        return [], median
    withheld = [
        {
            "document_id": document_id,
            "populated_fields": count,
            "group_median_populated_fields": median,
            "reason": "group_answer_contradicted_by_document_content",
        }
        for document_id, count in known
        if count > factor * median
    ]
    return sorted(withheld, key=lambda entry: entry["document_id"]), median


def prior_acceptance(classification):
    """Index accepted entries by document, keeping the one a reader would see.

    Entries are appended, so a later one supersedes an earlier one for the same
    document. Indexing first-wins would hand this function a type that no
    downstream reader uses.
    """
    index = {}
    for entry in classification["accepted"]:
        if isinstance(entry, dict) and entry.get("document_id") is not None:
            index[entry["document_id"]] = entry
    return index


def amend(
    classification,
    authorization,
    counts=None,
    factor=DEFAULT_OUTLIER_FACTOR,
    supersede_prior_client_decisions=False,
):
    """Append a client-decided classification for each authorized document."""
    patches = authorized_patches(authorization)
    accepted = prior_acceptance(classification)
    additions = []
    withheld = []
    superseded = []
    conflicts = []
    unchanged = 0
    seen = {}
    for patch in patches:
        family = patch_family(patch)
        impact = patch.get("impact")
        if not isinstance(impact, dict) or not isinstance(impact.get("document_ids"), list):
            raise ValueError("registry patch requires an impact document list")
        contradicted = {}
        if counts:
            entries, median = contradicted_by_content(impact["document_ids"], counts, factor)
            for entry in entries:
                withheld.append(
                    {
                        **entry,
                        "proposed_document_type": family,
                        "source_patch_id": patch.get("patch_id"),
                    }
                )
            contradicted = {entry["document_id"] for entry in entries}
            del median
        for document_id in impact["document_ids"]:
            if str(document_id) in contradicted:
                continue
            # Settle what the authorized patches say about this document before
            # asking what to do about it. A document named twice must not be
            # counted twice, and two patches that disagree must be refused as a
            # disagreement rather than resolved into whichever one came second.
            if document_id in seen and seen[document_id] != family:
                raise ValueError(
                    f"authorized patches disagree on a document's family: {document_id}"
                )
            if document_id in seen:
                continue
            prior = accepted.get(document_id)
            if prior is not None:
                if prior.get("evidence") != CLIENT_EVIDENCE:
                    raise ValueError(
                        "independent vendor agreement accepted a type for this document "
                        "and a client assertion does not outrank it: "
                        f"{document_id} ({prior.get('document_type')})"
                    )
                if prior.get("document_type") == family:
                    # The client repeated an answer they had already given. There
                    # is nothing to append and nothing to supersede -- but it is
                    # still an answer, so any exception still standing on the
                    # document is resolved by it.
                    unchanged += 1
                    seen[document_id] = family
                    continue
                if not supersede_prior_client_decisions:
                    conflicts.append((document_id, prior, family))
                    continue
                superseded.append(
                    {
                        "document_id": document_id,
                        "previous_document_type": prior.get("document_type"),
                        "previous_authorization_id": prior.get("authorization_id"),
                        "document_type": family,
                        "authorization_id": authorization.get("authorization_id"),
                        "source_patch_id": patch.get("patch_id"),
                    }
                )
            seen[document_id] = family
            additions.append(
                {
                    "document_id": document_id,
                    "page_id": document_id,
                    "document_type": family,
                    "agreeing_vendors": [],
                    "evidence": CLIENT_EVIDENCE,
                    "authorization_id": authorization.get("authorization_id"),
                    "authorized_by": authorization.get("operator_id"),
                    "source_patch_id": patch.get("patch_id"),
                    **(
                        {"supersedes_authorization_id": prior.get("authorization_id")}
                        if prior is not None
                        else {}
                    ),
                }
            )
    if conflicts:
        # Refuse once, with the whole set. Refusing on the first document made an
        # operator re-run the command to discover the size of what they were being
        # asked about.
        prior_ids = sorted({str(prior.get("authorization_id")) for _, prior, _ in conflicts})
        moves = sorted(
            {f"{prior.get('document_type')} -> {family}" for _, prior, family in conflicts}
        )
        raise ValueError(
            f"{len(conflicts)} document{'' if len(conflicts) == 1 else 's'} already "
            "carry a type from an earlier client "
            f"decision ({', '.join(prior_ids)}), not from vendor agreement, and this "
            f"authorization retypes them ({'; '.join(moves)}). A client may correct "
            "their own earlier answer, but not by accident: pass "
            "--supersede-prior-client-decisions to append the correction, which "
            "retains the earlier acceptance and links both authorizations."
        )
    summary = dict(classification.get("summary") or {})
    documents = summary.get("documents")
    # Count documents, not entries. A superseding acceptance is appended beside
    # the one it replaces, so the list grows while the typed corpus does not.
    summary["accepted"] = len(set(accepted) | set(seen))
    summary["client_decided"] = len(additions)
    summary["superseded_prior_client_decisions"] = len(superseded)
    summary["reaffirmed_prior_client_decisions"] = unchanged
    # Rule 9: an operator must be able to tell "nothing was contradicted" from
    # "nothing was checked". Absent records this is null, never 0.
    summary["content_contradiction_checked"] = bool(counts)
    summary["withheld_contradicted_by_content"] = len(withheld) if counts else None
    if isinstance(documents, int):
        summary["unresolved"] = documents - len(set(accepted) | set(seen))
    return seen, {
        **classification,
        "generated_at": datetime.now(UTC).isoformat(),
        "amended_from": classification.get("generated_at"),
        "client_decision_authorization_id": authorization.get("authorization_id"),
        "summary": summary,
        "accepted": [*classification["accepted"], *additions],
        "withheld_contradicted_by_content": withheld,
        "superseded_prior_client_decisions": superseded,
    }


def main(argv=None):
    """Append operator-authorized client classifications to a retained artifact."""
    load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("classification", help="Retained classification-consensus artifact.")
    parser.add_argument("authorization", help="Operator-authorized compiled plan JSON.")
    parser.add_argument("--out", required=True, help="New classification artifact path.")
    parser.add_argument(
        "--exceptions",
        help=(
            "Retained classification-exception artifact. Without it the exceptions that "
            "sent these documents to the client stay open and the gate asks again."
        ),
    )
    parser.add_argument("--exceptions-out", help="New exception artifact path.")
    parser.add_argument(
        "--records",
        action="append",
        default=[],
        metavar="ARTIFACT",
        help=(
            "Repeatable retained extraction artifact. A client answers one question for a "
            "whole group; a member carrying far more extracted content than the group's "
            "median is a member that answer was visibly not about, and is withheld from the "
            "amendment rather than typed from it. Without this the check does not run and "
            "the summary says so instead of reporting zero."
        ),
    )
    parser.add_argument(
        "--outlier-factor",
        type=float,
        default=DEFAULT_OUTLIER_FACTOR,
        help=(
            "How many times its group's median populated-field count a document may carry "
            "before its group's answer is treated as not covering it."
        ),
    )
    parser.add_argument(
        "--supersede-prior-client-decisions",
        action="store_true",
        help=(
            "Let this authorization retype a document an earlier client decision typed. "
            "The earlier acceptance is retained and both authorizations are linked; "
            "vendor agreement is never superseded, with or without this."
        ),
    )
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args(argv)
    try:
        if bool(args.exceptions) != bool(args.exceptions_out):
            raise ValueError("--exceptions and --exceptions-out are used together")
        classification = load_classification(args.classification)
        authorization = json.loads(Path(args.authorization).read_text())
        if args.outlier_factor <= 0:
            raise ValueError("--outlier-factor must be greater than zero")
        counts = populated_field_counts(args.records) if args.records else None
        if args.records and not counts:
            # Rule 9 again: records that name no document is an unread control,
            # not a corpus with nothing to contradict.
            raise ValueError("supplied record artifacts carry no identified documents to check")
        resolved, result = amend(
            classification,
            authorization,
            counts,
            args.outlier_factor,
            args.supersede_prior_client_decisions,
        )
        answered = None
        if args.exceptions:
            exceptions = json.loads(Path(args.exceptions).read_text())
            amended, answered = resolve_exceptions(exceptions, resolved, authorization)
            empty_output_path(Path(args.exceptions_out)).write_text(
                json.dumps(amended, indent=2) + "\n"
            )
        empty_output_path(Path(args.out)).write_text(json.dumps(result, indent=2) + "\n")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Classification amendment failed: {exc}")
    if not args.quiet:
        summary = result["summary"]
        print(f"Client-decided classifications appended: {summary['client_decided']}")
        print(f"  accepted now  : {summary['accepted']}")
        print(f"  unresolved    : {summary.get('unresolved')}")
        print(f"  authorization : {result['client_decision_authorization_id']}")
        if answered is not None:
            print(f"  exceptions marked answered: {answered}")
        if summary["superseded_prior_client_decisions"]:
            print(
                "  superseded an earlier client decision: "
                f"{summary['superseded_prior_client_decisions']}"
            )
        if summary["reaffirmed_prior_client_decisions"]:
            print(
                "  repeated an earlier client decision, nothing appended: "
                f"{summary['reaffirmed_prior_client_decisions']}"
            )
        if summary["content_contradiction_checked"]:
            print(
                f"  withheld, contradicted by content: {summary['withheld_contradicted_by_content']}"
            )
        else:
            print("  content contradiction: NOT CHECKED (no --records supplied)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
