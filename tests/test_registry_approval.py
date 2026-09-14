"""Approval provenance must distinguish a client decision from an operator one."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import registry_approval  # noqa: E402


def test_an_absent_authority_still_means_the_client_decided():
    """Every decision artifact written before this existed was a client decision."""
    provenance = registry_approval.approval_provenance({"decisions": []})
    assert provenance == {
        "status": "client_approved",
        "approval_authority": "client",
        "approved_by": None,
    }


def test_an_engagement_owner_approval_is_labelled_as_its_own_authority():
    """An operator decision stays usable, but never claims the client made it."""
    provenance = registry_approval.approval_provenance(
        {
            "decisions": [],
            "approval_authority": "engagement_owner",
            "approved_by": "  C. Melhauser  ",
        }
    )
    assert provenance == {
        "status": "engagement_owner_approved",
        "approval_authority": "engagement_owner",
        "approved_by": "C. Melhauser",
    }


def test_an_engagement_owner_approval_must_name_its_approver():
    """An approval recorded against nobody is the ambiguity this exists to remove."""
    with pytest.raises(ValueError, match="approved_by"):
        registry_approval.approval_provenance(
            {"decisions": [], "approval_authority": "engagement_owner"}
        )
    with pytest.raises(ValueError, match="approved_by"):
        registry_approval.approval_provenance(
            {"decisions": [], "approval_authority": "engagement_owner", "approved_by": "   "}
        )


def test_an_unknown_authority_is_refused():
    with pytest.raises(ValueError, match="approval_authority"):
        registry_approval.approval_provenance({"decisions": [], "approval_authority": "auditor"})


def test_a_non_object_decision_artifact_is_refused():
    with pytest.raises(ValueError, match="JSON object"):
        registry_approval.approval_provenance([])


def test_both_authorities_count_as_approved_and_nothing_else_does():
    """A rule is applied on either authority; the provenance says which one."""
    assert registry_approval.is_approved({"status": "client_approved"})
    assert registry_approval.is_approved({"status": "engagement_owner_approved"})
    assert not registry_approval.is_approved({"status": "proposed"})
    assert not registry_approval.is_approved({})
    assert not registry_approval.is_approved("client_approved")


def test_an_operator_approved_rule_is_applied_and_stays_attributed():
    """Route two must work end to end without ever claiming the client approved it."""
    import schema_discovery

    discovery = {
        "mapping_proposals": [
            {
                "proposal_id": "proposal-1",
                "decision_source": "llm",
                "template_fingerprint": "fingerprint-1",
                "source_label": "Total Commissions to date",
                "canonical_field": "header.total_amount",
                "semantic_type": "money",
            }
        ]
    }
    updated = schema_discovery.update_registry(
        {"rules": [], "registry_version": 3},
        discovery,
        {
            "approval_authority": "engagement_owner",
            "approved_by": "C. Melhauser",
            "decisions": [{"proposal_id": "proposal-1", "decision": "approve"}],
        },
    )
    rule = updated["rules"][0]
    assert rule["status"] == "engagement_owner_approved"
    assert rule["approval_authority"] == "engagement_owner"
    assert rule["approved_by"] == "C. Melhauser"
    assert updated["registry_version"] == 4

    # The rule is honoured downstream exactly like a client-approved one.
    assert schema_discovery.rule_for(updated, "fingerprint-1", "Total Commissions to date") is rule
