#!/usr/bin/env python3
"""Carry a review-lane pack and its answers to the authorization step.

There are two ways into this repository's client review, and until now only one
of them could reach a change an operator can authorize.

`client_decision_compile.py` takes a safe-consolidation artifact and a
workbook-import proposal keyed to its hash. Those come from
`safe_review_consolidation.py` and `client_review_package.py`. The review
*lane* -- `client_review_lane.py build` / `read-answers`, the path the operating
guide points an operator at for turning any blocked control into a
client-answerable pack -- produces neither. So a client could answer every
question in a lane pack, correctly, and none of it could be compiled, authorized
or applied. On the commission engagement that was 23 answers speaking to 27,855
review items, sitting unapplied with no route.

This module builds the two artifacts the compiler expects out of what the lane
already retains. It invents nothing: the groups are the pack's own groups, the
decisions are the client's own answers, and the lineage hashes are of the exact
workbooks issued and returned.

Two rules the compiler enforces that shape what is emitted here:

* every group must carry a decision, and `issued_group_count` must equal the
  number of decisions. A group the client did not answer therefore gets an
  explicit empty decision, which the compiler reads as non-actionable. Silence
  is recorded as silence rather than omitted.
* a decision's `protected` must equal its group's. Protection is the group's
  property and is copied, never asserted here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CONSOLIDATION_SCHEMA = "safe_review_consolidation_v1"
DECISIONS_SCHEMA = "client_decision_import_v1"


def file_digest(path: Path) -> str:
    """Return a file's SHA-256, which the compiler requires as exact lineage."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_digest(value: Any) -> str:
    """Hash a structure the way `client_decision_compile.digest` does."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def consolidation_from_pack(pack: dict[str, Any]) -> dict[str, Any]:
    """Return the consolidation-shaped artifact the compiler indexes groups from.

    The groups are the pack's, unchanged. Nothing is merged, split or renamed:
    a group the client answered must still be the group the answer applies to.
    """
    groups = pack.get("groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError("review pack contains no groups to compile against")
    return {
        "schema_version": CONSOLIDATION_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "client_review_lane",
        "all_source_items_retained": True,
        "production_approval_permitted": False,
        "gate_status": "blocked_pending_client_review",
        "decision_groups": groups,
    }


def decisions_from_answers(
    pack: dict[str, Any],
    answers: dict[str, Any],
    consolidation: dict[str, Any],
    issued_workbook: Path,
    returned_workbook: Path,
) -> dict[str, Any]:
    """Return the workbook-import proposal, one decision per group.

    An answer reaches its group by `group_id`, which the lane records on both.
    Question numbers are not used: they are positional and do not survive a
    rebuild.
    """
    by_group: dict[str, dict[str, Any]] = {}
    for answer in answers.get("answers", []):
        group_id = answer.get("group_id")
        if isinstance(group_id, str) and group_id:
            by_group[group_id] = answer

    decisions = []
    answered = 0
    for group in consolidation["decision_groups"]:
        group_id = group["group_id"]
        answer = by_group.get(group_id)
        choice = str((answer or {}).get("decision_choice") or "")
        comment = str((answer or {}).get("note") or "").strip()
        selection = str((answer or {}).get("answer") or "").strip()
        if answer is None:
            # No answer is a decision the client did not make, recorded as one.
            choice, comment = "", ""
        else:
            answered += 1
            # The compiler reads the alternative from `client_comment`, so a
            # client who chose from the dropdown and wrote nothing still needs
            # their selection carried there.
            comment = comment or selection
        deferred_choice = None
        if choice and bool(group.get("protected")):
            # Rule 7. A protected finding -- arithmetic, provider, handwriting,
            # identity -- informs an item-by-item recheck and is never cleared
            # in a batch, and `client_decision_compile.py` refuses an actionable
            # choice on a protected group. It refuses the *whole* compilation,
            # so carrying the client's dropdown choice through verbatim meant a
            # single answered protected group blocked every unprotected decision
            # in the same run: on the commission round that was 20 of 23
            # answers blocking the other 3.
            #
            # The answer is not discarded and not weakened -- it is recorded as
            # deferred, with the choice the client actually made retained beside
            # it, because what a protected answer authorizes is a recheck rather
            # than a change.
            deferred_choice, choice = choice, "Defer"
        decisions.append(
            {
                "decision_id": group_id,
                "client_decision": choice,
                "client_decision_as_answered": deferred_choice,
                "deferred_because_protected": deferred_choice is not None,
                "client_comment": comment,
                "proposal_only": True,
                # Copied from the group; the compiler refuses a mismatch.
                "protected": bool(group.get("protected")),
                "source_question_id": (answer or {}).get("question_id"),
                "template_family": group.get("template_family"),
                "finding_family": group.get("finding_family"),
            }
        )

    return {
        "schema_version": DECISIONS_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "client_review_lane",
        "proposal_only": True,
        "complete": True,
        "invalid_decisions": [],
        "unresolved_decision_ids": [],
        "issued_workbook_sha256": file_digest(issued_workbook),
        "returned_workbook_sha256": file_digest(returned_workbook),
        "safe_consolidation_sha256": canonical_digest(consolidation),
        "issued_group_count": len(decisions),
        "decision_count": len(decisions),
        "answered_group_count": answered,
        "decisions": decisions,
    }
