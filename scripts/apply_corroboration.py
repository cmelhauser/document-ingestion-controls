#!/usr/bin/env python3
"""Accept a corroborated reading under an explicit client authorization.

`independent_corroboration.py` weighs a reading against an independent
extractor's own reading of the same page and deliberately resolves nothing:
presence is page-scoped, so it defeats a **misread** and not a
**misattribution**. Turning that evidence into an accepted value is the separate
authorized step the corroboration artifact says it is, and this is that step.

It runs only against a named client authorization. Without `--authorization`
there is no decision to apply and the lane refuses, because accepting a value on
weaker-than-consensus evidence is a policy choice and never a default.

What acceptance means here
--------------------------

* A **corroborated single reading** -- one engine read a value and an
  independent non-LLM extractor read it from that page -- is accepted.
* A **tie-break** -- two engines disagreed and exactly one candidate appears in
  that evidence -- is accepted as that candidate, and is recorded separately
  because it began as a disagreement.

Every accepted field keeps `accepted_by: corroborated_by_independent_extractor`,
the authorization it was accepted under, the match mode, and `occurrences`. It
is **never** relabelled `consensus_*`: no second model vendor read it, and an
artifact that claimed otherwise would launder the weaker evidence into the
stronger one. The original consensus field is retained beside it.

The known limit travels with every acceptance: an engine that read `5,193.60`
correctly and filed it under the wrong column is accepted here and still wrong.
`occurrences` says how located the match was, and `--unique-occurrence-only`
restricts acceptance to values that appear exactly once on their page.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from consensus import refresh_views
from independent_corroboration import ARTIFACT_TYPE as CORROBORATION_ARTIFACT
from independent_corroboration import EVIDENCE_KIND
from run_io import empty_output_path
from runtime_config import load_project_env

ARTIFACT_TYPE = "applied_corroboration_v1"
RECORDS_ARTIFACT_TYPE = "records_with_corroborated_values_v1"
ACCEPTANCE_FLAG = "accepted_on_independent_corroboration"

LIMITS = (
    "Accepted on page-scoped independent evidence, not on model vendor "
    "agreement. This defeats a misread and not a misattribution: a value read "
    "correctly and filed under the wrong column is accepted here and still "
    "wrong. Read `occurrences`. The acceptance is append-only and the original "
    "consensus field is retained beside it."
)


def load_corroboration(path):
    """Read the corroboration artifact, refusing anything else."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or data.get("artifact_type") != CORROBORATION_ARTIFACT:
        raise ValueError(f"not a {CORROBORATION_ARTIFACT} artifact: {path}")
    return data


def acceptances(corroboration, unique_only=False):
    """Index every acceptance this authorization covers by (document, field)."""
    accepted = {}
    for kind, entries in (
        ("corroborated_single_reading", corroboration.get("corroborations") or []),
        ("independent_evidence_tie_break", corroboration.get("tie_breaks") or []),
    ):
        for entry in entries:
            if unique_only and entry.get("occurrences") != 1:
                continue
            value = entry.get("value") if "value" in entry else entry.get("supported_value")
            if value is None:
                continue
            accepted[(entry.get("document_id"), entry.get("field"))] = {
                "kind": kind,
                "value": value,
                "match_mode": entry.get("match_mode"),
                "occurrences": entry.get("occurrences"),
            }
    return accepted


def apply_document(document, accepted, authorization):
    """Merge every acceptance for this document, retaining what it replaced."""
    applied = []
    fields = document.get("fields") or {}
    for path, field in fields.items():
        entry = accepted.get((document.get("document_id"), path))
        if entry is None:
            continue
        if field.get("accepted"):
            # Consensus already carried this field. Independent corroboration is
            # the weaker evidence and never overwrites the stronger one.
            continue
        fields[path] = {
            **field,
            "value": entry["value"],
            "accepted": True,
            "blocking": False,
            "queue_for_review": False,
            # Deliberately not a `consensus_*` flag: no second vendor read this.
            "acceptance": {
                "accepted_by": EVIDENCE_KIND,
                "acceptance_flag": ACCEPTANCE_FLAG,
                "kind": entry["kind"],
                "match_scope": "page",
                "match_mode": entry["match_mode"],
                "occurrences": entry["occurrences"],
                "authorization": authorization,
                "vendor_agreement": False,
            },
            # The reading this replaced, kept rather than overwritten.
            "superseded_consensus": {
                "value": field.get("value"),
                "consensus_flag": field.get("consensus_flag"),
                "candidate_values": field.get("candidate_values"),
            },
        }
        applied.append(
            {
                "document_id": document.get("document_id"),
                "field": path,
                "value": entry["value"],
                "kind": entry["kind"],
                "match_mode": entry["match_mode"],
                "occurrences": entry["occurrences"],
                "prior_consensus_flag": field.get("consensus_flag"),
            }
        )
    if applied:
        refresh_views(document)
        document["accepted_field_count"] = sum(1 for f in fields.values() if f.get("accepted"))
    return applied


def remaining_exceptions(exceptions, accepted):
    """Keep every consensus finding this authorization does not cover."""
    return [
        item for item in exceptions if (item.get("document_id"), item.get("field")) not in accepted
    ]


def build(records, exceptions, corroboration, authorization, unique_only=False):
    """Apply the authorization and return the artifact, records, and what is left."""
    accepted = acceptances(corroboration, unique_only)
    if not accepted:
        # Rule 9: an acceptance lane that accepted nothing has not run.
        raise ValueError(
            "the corroboration artifact carried no acceptance this authorization "
            "covers; a control that processed nothing has not passed"
        )
    applied = []
    for document in records.get("documents") or []:
        applied.extend(apply_document(document, accepted, authorization))
    left = remaining_exceptions(exceptions, accepted)
    kinds = Counter(item["kind"] for item in applied)
    artifact = {
        "artifact_type": ARTIFACT_TYPE,
        "generated_at": datetime.now(UTC).isoformat(),
        "authorization": authorization,
        "unique_occurrence_only": unique_only,
        "summary": {
            "acceptances_available": len(accepted),
            "fields_accepted": len(applied),
            "corroborated_single_readings": kinds["corroborated_single_reading"],
            "independent_evidence_tie_breaks": kinds["independent_evidence_tie_break"],
            "consensus_findings_before": len(exceptions),
            "consensus_findings_remaining": len(left),
        },
        "acceptances": applied,
        "limits": LIMITS,
    }
    records["artifact_type"] = RECORDS_ARTIFACT_TYPE
    records.setdefault("summary", {})["corroboration_authorization"] = authorization
    records["limits"] = LIMITS
    return artifact, records, left


def load_records(path):
    """Read the consensus record set this lane accepts into."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
        raise ValueError(f"not a consensus record artifact with documents: {path}")
    return data


def load_exceptions(path):
    """Read the consensus exception artifact this lane reduces."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get("exceptions"), list):
        raise ValueError(f"not an exception artifact with an exceptions list: {path}")
    return data["exceptions"]


def main(argv=None):
    """Apply an authorized acceptance of independently corroborated readings."""
    load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", help="Consensus record artifact to accept into.")
    parser.add_argument("exceptions_in", help="Consensus exception artifact to reduce.")
    parser.add_argument("corroboration", help="Independent corroboration artifact.")
    parser.add_argument(
        "--authorization",
        required=True,
        help=(
            "Client decision this acceptance is made under. Required: accepting "
            "a value on page-scoped independent evidence rather than model "
            "vendor agreement is a policy choice and never a default."
        ),
    )
    parser.add_argument("--out", required=True, help="New applied-corroboration artifact path.")
    parser.add_argument("--records-out", required=True, help="New accepted record set path.")
    parser.add_argument(
        "--exceptions",
        required=True,
        help="New exception artifact holding every finding this acceptance leaves open.",
    )
    parser.add_argument(
        "--unique-occurrence-only",
        action="store_true",
        help=(
            "Accept only values that appear exactly once on their page. A value "
            "seen once is nearly as located as a cell match; one seen twenty "
            "times says little about which row it belongs to."
        ),
    )
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args(argv)
    try:
        artifact, records, left = build(
            load_records(args.records),
            load_exceptions(args.exceptions_in),
            load_corroboration(args.corroboration),
            args.authorization,
            args.unique_occurrence_only,
        )
        empty_output_path(Path(args.out)).write_text(json.dumps(artifact, indent=2) + "\n")
        empty_output_path(Path(args.records_out)).write_text(json.dumps(records, indent=2) + "\n")
        empty_output_path(Path(args.exceptions)).write_text(
            json.dumps({"summary": {"count": len(left)}, "exceptions": left}, indent=2) + "\n"
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Applied corroboration failed: {exc}")
    if not args.quiet:
        summary = artifact["summary"]
        print(f"authorization      : {args.authorization}")
        print(f"fields accepted    : {summary['fields_accepted']:,}")
        print(f"  single readings  : {summary['corroborated_single_readings']:,}")
        print(f"  tie breaks       : {summary['independent_evidence_tie_breaks']:,}")
        print(
            f"findings remaining : {summary['consensus_findings_remaining']:,} "
            f"of {summary['consensus_findings_before']:,}"
        )
        print("Accepted on page evidence, not vendor agreement; misattribution is not defeated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
