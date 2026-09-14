#!/usr/bin/env python3
"""Record an operator-authorized resolution for the findings holding a document open.

`consensus.py` is the only control that computes a document's `review_status`,
and it computes it once, from the readings. Every later control -- the
arithmetic vote, the corroboration applier, the classification amendment --
copies that status forward untouched. A document that entered review as
`open_exception` therefore stays `open_exception` no matter what is later
established about it, and `canonical_export.py` admits only `auto_accepted`,
`sampled_verified` and `exception_resolved`.

`exception_resolved` is the status reserved for exactly this case, and nothing
in the repository wrote it. Three controls read it; none produced it. One run
made that concrete: the canonical export reported 0 of 716 documents after every
round of repair, including one where the arithmetic vote reported 620 documents
promoted. The values changed and the status did not, so the export excluded the
whole corpus and named the consensus status as its reason.

This control writes that status, and only where an operator has authorized the
resolution of every finding still holding the document open.

Four refusals keep it from becoming an override channel:

* an authorization that is not `operator_authorized` is refused, so no client
  note and no model output can clear a document by itself;
* a document carrying an open finding the authorization does not name is left
  exactly as it is and reported -- clearing on a partial authorization would
  bury the findings nobody answered, which is the failure this control exists
  to make impossible;
* a document already clear is never relabelled: `exception_resolved` claims
  something weaker than vendor agreement and must not overwrite it;
* a document the authorization names but the consensus artifact does not carry
  is reported as an exception rather than silently skipped.

Nothing is deleted. A cleared document keeps `prior_review_status` and gains a
`resolution` block naming the authorization, the moment, and every finding it
answered, so a cleared status always reads back to the signature that produced
it. The input artifact is not modified; this writes a new one.

The report is not optional. A run where nothing cleared and a run that was never
executed must not look alike, so the artifact is written either way and names
every document left open and the findings that held it.

Clearing the status is only half the job. The final review gate is built from
the exception artifacts, and the canonical export refuses any document the gate
still holds an item against -- so an answered finding left standing in those
artifacts blocks the document the operator just cleared. Pass `--exceptions IN
OUT`, once per artifact, to mark each answered finding
`client_review_required: false`, which is the mechanism the gate already
honours and the one `classification_amend.py` established. Nothing is deleted:
the finding stays, carrying the authorization that answered it.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from review_status import REVIEW_CLEAR
from run_io import empty_output_path
from runtime_config import load_project_env

RESOLVED_STATUS = "exception_resolved"
RESOLUTION_EVIDENCE = "operator_authorized_exception_resolution"
# The statuses canonical export already admits. A document sitting on one of
# these has nothing for this control to resolve and must not be relabelled
# downward. Imported rather than restated: this list had been written out in
# four places, and the copies disagreed about what "clear" meant.
ALREADY_CLEAR = REVIEW_CLEAR
# How many field names a report entry names before it counts the rest. The whole
# set stays in the artifact's own count; this keeps one document's entry
# readable when it holds hundreds of open findings.
REPORT_FIELD_SAMPLE = 12


def load_consensus(path):
    """Read a consensus record artifact and refuse a shape this cannot resolve."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
        raise ValueError(f"not a consensus record artifact: {path}")
    for document in data["documents"]:
        if not isinstance(document, dict):
            raise ValueError("each consensus document must be an object")
        if not isinstance(document.get("fields"), dict):
            raise ValueError(
                "each consensus document requires a fields object; a record without "
                "fields has no findings to resolve and must not be cleared"
            )
    return data


def authorized_resolutions(authorization):
    """Map each authorized document to the fields the operator resolved for it.

    A patch resolves the fields its own impact names and no others. The scope
    recorded in the payload is deliberately not read as a licence to clear a
    document whole: every compiled patch on the run this was written for
    declared `scope: document` while naming a single field, so honouring the
    scope would have cleared hundreds of findings nobody was asked about.
    """
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
    resolutions = {}
    for patch in plan["patches"]:
        if patch.get("patch_id") not in authorized:
            continue
        impact = patch.get("impact")
        if not isinstance(impact, dict):
            raise ValueError(f"authorized patch carries no impact: {patch.get('patch_id')}")
        fields = impact.get("fields")
        if not isinstance(fields, list) or not fields:
            raise ValueError(
                "an authorized patch must name the fields it resolves; a patch naming "
                f"none resolves nothing: {patch.get('patch_id')}"
            )
        for document_id in impact.get("document_ids") or ():
            resolutions.setdefault(str(document_id), set()).update(str(f) for f in fields)
    if not resolutions:
        raise ValueError("authorization resolves no documents")
    return resolutions


def open_findings(document):
    """Name the fields still queued for review on a document."""
    return {
        name
        for name, field in document["fields"].items()
        if isinstance(field, dict) and field.get("queue_for_review")
    }


def resolve(consensus, authorization, resolutions, now):
    """Clear the documents whose every open finding the operator resolved."""
    authorization_id = authorization.get("authorization_id")
    documents = []
    left_open = []
    cleared = 0
    already_clear = 0
    seen = set()
    for document in consensus["documents"]:
        document_id = str(document.get("document_id"))
        seen.add(document_id)
        resolved_fields = resolutions.get(document_id)
        if document.get("review_status") in ALREADY_CLEAR:
            # Nothing here is an exception, so there is nothing to resolve. Saying
            # so beats writing a weaker status over a stronger one.
            already_clear += 1
            documents.append(document)
            continue
        findings = open_findings(document)
        unresolved = sorted(findings - (resolved_fields or set()))
        if not findings or unresolved:
            if resolved_fields is not None:
                left_open.append(
                    {
                        "document_id": document_id,
                        "review_status": document.get("review_status"),
                        "open_finding_count": len(findings),
                        "unresolved_finding_count": len(unresolved),
                        "unresolved_fields": unresolved[:REPORT_FIELD_SAMPLE],
                        "reason": (
                            "the authorization named this document but not every finding "
                            "holding it open"
                            if findings
                            else "the document carries no queued finding, so its status is "
                            "held by something this control does not resolve"
                        ),
                    }
                )
            documents.append(document)
            continue
        cleared += 1
        documents.append(
            {
                **document,
                "review_status": RESOLVED_STATUS,
                # Retained, never overwritten: the status this document reached on
                # its own readings is the thing an auditor compares the clearance to.
                "prior_review_status": document.get("review_status"),
                "resolution": {
                    "evidence": RESOLUTION_EVIDENCE,
                    "authorization_id": authorization_id,
                    "resolved_at": now,
                    "resolved_fields": sorted(findings),
                },
            }
        )
    missing = sorted(document_id for document_id in resolutions if document_id not in seen)
    summary = {
        **(consensus.get("summary") or {}),
        "generated_at": now,
        "authorization_id": authorization_id,
        "exception_resolved": cleared,
        "already_clear": already_clear,
        "left_open": len(documents) - cleared - already_clear,
        "authorized_documents_absent_from_consensus": len(missing),
    }
    result = {**consensus, "summary": summary, "documents": documents}
    report = {
        "artifact_type": "exception_resolution_report_v1",
        "generated_at": now,
        "authorization_id": authorization_id,
        "summary": {
            "exception_resolved": cleared,
            "already_clear": already_clear,
            "left_open": summary["left_open"],
            "named_but_not_cleared": len(left_open),
            "authorized_documents_absent_from_consensus": len(missing),
        },
        "named_but_not_cleared": left_open,
        "authorized_documents_absent_from_consensus": missing,
    }
    return result, report


def answer_exceptions(exceptions, resolutions, authorization):
    """Mark each authorized finding answered in a retained exception artifact.

    Clearing a document's `review_status` is only half the job. The final review
    gate is built from the exception artifacts, not from the record, and
    `open_review_documents` in the canonical export counts every item it finds
    regardless of that item's own status -- so a document whose exception is
    still standing is refused by the export even after the operator answered it.
    `classification_amend.py` met the same wall and this is the mechanism it
    established: the finding is retained, marked as no longer requiring client
    review, and carries the authorization that answered it.

    A finding is marked only where the authorization named that document *and*
    that field. A finding on a document the operator answered in part is left
    exactly as it is.
    """
    if not isinstance(exceptions, dict) or not isinstance(exceptions.get("exceptions"), list):
        raise ValueError("exceptions artifact requires an exceptions list")
    entries = []
    answered = 0
    for entry in exceptions["exceptions"]:
        if not isinstance(entry, dict):
            raise ValueError("each exception must be an object")
        fields = resolutions.get(str(entry.get("document_id")))
        if (
            fields
            and str(entry.get("field")) in fields
            and entry.get("client_review_required") is not False
        ):
            answered += 1
            entries.append(
                {
                    **entry,
                    "client_review_required": False,
                    "resolution": RESOLUTION_EVIDENCE,
                    "authorization_id": authorization.get("authorization_id"),
                }
            )
            continue
        entries.append(entry)
    summary = dict(exceptions.get("summary") or {})
    summary["count"] = len(entries)
    summary["resolved_by_operator_authorization"] = answered
    summary["still_client_review_required"] = sum(
        1 for entry in entries if entry.get("client_review_required") is not False
    )
    return {**exceptions, "summary": summary, "exceptions": entries}, answered


def build_parser():
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__.strip())
    parser.add_argument("consensus", help="Retained consensus record artifact.")
    parser.add_argument("authorization", help="Operator-authorized compiled plan JSON.")
    parser.add_argument("--out", required=True, help="new consensus artifact carrying the status")
    parser.add_argument(
        "--report",
        required=True,
        help="new report naming every authorized document this control did not clear",
    )
    parser.add_argument(
        "--exceptions",
        nargs=2,
        action="append",
        metavar=("IN", "OUT"),
        default=[],
        help=(
            "Retained exception artifact and the new artifact to write. Repeatable. "
            "Without it the findings this authorization answered stay standing in the "
            "final review gate, and the canonical export refuses the document anyway."
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="suppress the summary")
    return parser


def main(argv=None):
    load_project_env()
    parser = build_parser()
    apply_shared_help(parser)
    args = parser.parse_args(argv)
    try:
        consensus = load_consensus(args.consensus)
        authorization = json.loads(Path(args.authorization).read_text())
        resolutions = authorized_resolutions(authorization)
        now = datetime.now(UTC).isoformat()
        result, report = resolve(consensus, authorization, resolutions, now)
        answered = 0
        for source, destination in args.exceptions:
            amended, count = answer_exceptions(
                json.loads(Path(source).read_text()), resolutions, authorization
            )
            answered += count
            empty_output_path(Path(destination)).write_text(json.dumps(amended, indent=2) + "\n")
        empty_output_path(Path(args.report)).write_text(json.dumps(report, indent=2) + "\n")
        empty_output_path(Path(args.out)).write_text(json.dumps(result, indent=2) + "\n")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Exception resolution failed: {exc}")
    if not args.quiet:
        summary = result["summary"]
        print(f"Documents marked {RESOLVED_STATUS}: {summary['exception_resolved']}")
        print(f"  already clear : {summary['already_clear']}")
        print(f"  left open     : {summary['left_open']}")
        print(f"  authorization : {summary['authorization_id']}")
        if args.exceptions:
            print(f"  exceptions marked answered: {answered}")
        if summary["authorized_documents_absent_from_consensus"]:
            print(
                "  authorized but absent from consensus: "
                f"{summary['authorized_documents_absent_from_consensus']}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
