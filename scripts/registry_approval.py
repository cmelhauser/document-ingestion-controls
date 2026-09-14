#!/usr/bin/env python3
"""Approval provenance for the client-approved mapping, allocation, and template registries.

A registry rule is the point where a proposal stops being a proposal, so the
artifact has to say *who* decided.  ``registry-update`` used to stamp every
accepted rule ``client_approved`` without reading or recording an approver, so a
decision the engagement owner made on the client's behalf became indistinguishable
from one the client made -- and that misstatement then propagated through table
reconciliation, allocation, canonical export, and delivery with nothing anywhere
able to tell the two apart.

An operator approval is a legitimate engagement decision and stays usable; it is
simply labelled as what it is.  Both statuses are approved for the purpose of
applying a rule, and ``approval_authority``/``approved_by`` carry the provenance
that the status alone cannot.
"""

CLIENT_AUTHORITY = "client"
ENGAGEMENT_OWNER_AUTHORITY = "engagement_owner"
AUTHORITIES = (CLIENT_AUTHORITY, ENGAGEMENT_OWNER_AUTHORITY)

CLIENT_APPROVED = "client_approved"
ENGAGEMENT_OWNER_APPROVED = "engagement_owner_approved"
APPROVED_STATUSES = frozenset({CLIENT_APPROVED, ENGAGEMENT_OWNER_APPROVED})

STATUS_FOR_AUTHORITY = {
    CLIENT_AUTHORITY: CLIENT_APPROVED,
    ENGAGEMENT_OWNER_AUTHORITY: ENGAGEMENT_OWNER_APPROVED,
}


def is_approved(rule):
    """Report whether a retained registry entry carries an approval of either authority."""
    return isinstance(rule, dict) and rule.get("status") in APPROVED_STATUSES


def approval_provenance(decisions):
    """Resolve the approving authority for a decision artifact, refusing an unnamed approver.

    ``approval_authority`` defaults to ``client`` so an existing client decision
    artifact keeps its exact meaning.  An ``engagement_owner`` decision must name
    ``approved_by``: an approval recorded against nobody is the ambiguity this
    provenance exists to remove.
    """
    if not isinstance(decisions, dict):
        raise ValueError("Decisions must be a JSON object")
    authority = decisions.get("approval_authority", CLIENT_AUTHORITY)
    if authority not in AUTHORITIES:
        raise ValueError(
            "approval_authority must be " + " or ".join(repr(item) for item in AUTHORITIES)
        )
    approved_by = str(decisions.get("approved_by") or "").strip()
    if authority == ENGAGEMENT_OWNER_AUTHORITY and not approved_by:
        raise ValueError(
            "An engagement_owner approval must name approved_by; the client did not make "
            "this decision and the registry cannot record it against nobody"
        )
    return {
        "status": STATUS_FOR_AUTHORITY[authority],
        "approval_authority": authority,
        "approved_by": approved_by or None,
    }
