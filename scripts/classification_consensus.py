#!/usr/bin/env python3
"""Accept a document type only where independent model vendors already agreed.

Phase 2 classification is a deterministic phrase-rule pass over the page's own
text.  When a client's document family carries no distinctive printed phrase the
rules produce nothing, which is the correct conservative outcome: a misclassified
document extracts *structurally* wrong, not merely inaccurately.

What was missing is the second chance.  A 716-page commission corpus rule-classified
25 pages and escalated 691, and every later lane inherited ``document_family:
unknown``: the whole corpus collapsed into one observed "template" of 2,430
labels, mapping discovery proposed rules against a fingerprint that stood for
nothing, and drift compared layouts that were never layouts.  Meanwhile both
extraction engines had independently and correctly classified those same pages,
and their answers sat unused in ``model_document_type`` -- retained the entire
time as review proposals nothing could act on.

This command turns that retained evidence into an explicit, reviewable
classification decision.  It applies exactly the standard the rest of the
pipeline applies to a fact: two *genuinely independent model vendors* must agree.
A single engine's opinion is never enough, a router does not create a second
vendor, and ``unknown`` is never an agreement -- those stay explicit exceptions.

It is append-only.  It reads the retained handoffs and writes one new artifact;
it never edits an extraction record, a manifest, or a page.  Its acceptances are
consensus evidence for the *family* of a document, which is what template
observation groups by; they do not re-open extraction, and they do not clear any
control.
"""

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import consensus
from cli_help import apply_shared_help
from runtime_config import load_project_env

ARTIFACT_TYPE = "classification_consensus_v1"
MINIMUM_VENDORS = 2
# A model that says "I could not tell" has not classified the page. Treating it
# as agreement would manufacture a family from two engines' shared uncertainty.
NON_ANSWERS = frozenset({"", "none", "null", "unknown"})


def proposed_type(record):
    """Return one engine's own document-type claim, ignoring the intake verdict.

    ``document_type`` on an extraction record is the *intake* classification
    copied through; the engine's own reading is ``model_document_type``. Reading
    the wrong one here would compare the failed rule pass against itself.
    """
    value = record.get("model_document_type")
    if isinstance(value, dict):
        value = value.get("value")
    text = str(value or "").strip()
    return "" if text.casefold() in NON_ANSWERS else text


def vendor_claims(engines):
    """Map each independent model vendor to the document type it proposed.

    ``load_records`` already refused a duplicate vendor, so one vendor carries at
    most one claim here and agreement is a straight count of vendors.
    """
    claims = {}
    for payload in engines.values():
        identity = payload.get("_verified_identity") or {}
        vendor = identity.get("model_vendor")
        proposal = proposed_type(payload)
        if vendor and proposal:
            claims[vendor] = proposal
    return claims


def resolve(document_id, engines, minimum_vendors=MINIMUM_VENDORS):
    """Accept one document type, or explain exactly why none was accepted."""
    claims = vendor_claims(engines)
    counts = Counter(claims.values())
    agreed = [document_type for document_type, votes in counts.items() if votes >= minimum_vendors]
    if len(agreed) == 1:
        document_type = agreed[0]
        return {
            "document_id": document_id,
            "page_id": document_id,
            "document_type": document_type,
            "agreeing_vendors": sorted(
                vendor for vendor, claim in claims.items() if claim == document_type
            ),
            "evidence": "independent_model_vendor_agreement",
        }, None
    if not claims:
        reason = "no engine proposed a document type for this page"
    elif len(claims) < minimum_vendors:
        reason = (
            f"only {len(claims)} independent model vendor proposed a document type; "
            f"{minimum_vendors} must agree and a router does not create a second vendor"
        )
    else:
        detail = "; ".join(f"{vendor}={claim}" for vendor, claim in sorted(claims.items()))
        reason = f"independent model vendors disagree on the document type ({detail})"
    return None, {
        "priority": "normal",
        "document_id": document_id,
        "page_id": document_id,
        "region_id": "",
        "field": "document_type",
        "reason": reason,
        "review_source": "classification_consensus",
        "disposition": "client_review_required",
    }


def build(by_document, minimum_vendors=MINIMUM_VENDORS):
    """Resolve every retained document, accepting none without independent agreement."""
    accepted, exceptions = [], []
    for document_id in sorted(by_document):
        decision, exception = resolve(document_id, by_document[document_id], minimum_vendors)
        if decision is not None:
            accepted.append(decision)
        else:
            exceptions.append(exception)
    return accepted, exceptions


def classification_map(path):
    """Read accepted classifications keyed by document, refusing a foreign artifact."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or data.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(f"{path}: not a {ARTIFACT_TYPE} artifact")
    accepted = data.get("accepted")
    if not isinstance(accepted, list):
        raise ValueError(f"{path}: accepted must be a list")
    resolved = {}
    for item in accepted:
        if not isinstance(item, dict):
            raise ValueError(f"{path}: every accepted entry must be an object")
        document_id = str(item.get("document_id") or "")
        document_type = str(item.get("document_type") or "")
        if not document_id or not document_type:
            raise ValueError(f"{path}: accepted entry requires document_id and document_type")
        resolved[document_id] = document_type
    return resolved


def require_new_file(path):
    """Refuse to replace a retained artifact."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def main(argv=None):
    """Write the classification-consensus artifact and its exception queue."""
    load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "handoffs",
        nargs="+",
        help=(
            "Retained independent extraction handoffs, one per consensus lane. "
            "Independence is resolved to the model vendor behind any router, so two "
            "lanes reaching one vendor are refused as a single reading."
        ),
    )
    parser.add_argument("--out", required=True, help="New accepted-classification artifact path.")
    parser.add_argument(
        "--exceptions",
        required=True,
        help=(
            "New exception artifact naming every document whose type no independent "
            "pair agreed on. Absence of exceptions is a result, not a formality."
        ),
    )
    parser.add_argument(
        "--minimum-vendors",
        type=int,
        default=MINIMUM_VENDORS,
        help=(
            "Distinct model vendors that must propose the same document type before it "
            "is accepted. Below two there is no independent agreement to speak of."
        ),
    )
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args(argv)

    try:
        if args.minimum_vendors < 2:
            raise ValueError(
                "minimum vendors must be at least 2; a single engine classifying a page "
                "is a proposal, not independent agreement"
            )
        out_path = require_new_file(args.out)
        exceptions_path = require_new_file(args.exceptions)
        by_document = consensus.load_records(args.handoffs)
        if not by_document:
            raise ValueError("the supplied handoffs retained no document to classify")
        accepted, exceptions = build(by_document, args.minimum_vendors)
        out_path.write_text(
            json.dumps(
                {
                    "artifact_type": ARTIFACT_TYPE,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "minimum_vendors": args.minimum_vendors,
                    "source_handoffs": [
                        {
                            "path": str(path),
                            "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                        }
                        for path in args.handoffs
                    ],
                    "summary": {
                        "documents": len(by_document),
                        "accepted": len(accepted),
                        "unresolved": len(exceptions),
                    },
                    "proposal_only": False,
                    "clears_no_control": True,
                    "accepted": accepted,
                },
                indent=2,
            )
            + "\n"
        )
        exceptions_path.write_text(
            json.dumps({"summary": {"count": len(exceptions)}, "exceptions": exceptions}, indent=2)
            + "\n"
        )
    except (OSError, ValueError, FileExistsError, json.JSONDecodeError) as exc:
        sys.exit(f"Classification consensus failed: {exc}")

    if not args.quiet:
        print(f"Documents read: {len(by_document)}")
        print(f"  accepted on independent vendor agreement: {len(accepted)}")
        print(f"  unresolved, retained for review: {len(exceptions)}")
        print(f"  written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
