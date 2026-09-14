"""A person's decision about a pair of names outranks a similarity score.

The commission run left 44 pairs between the review band and the merge
threshold. Nothing inside the corpus settles them: one is a company and its
branch, one is two firms printed in one cell, one is a job site with a PAID
stamp. What settles them is a public record or the client's ruling, and the
master has to carry that decision -- with the authorization it rests on --
rather than leave the pair pending forever or merge it on a score.
"""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

lane = importlib.import_module("entity_resolve")

AUTH = "run-authorization-amendment-39"


def party(key, name, variants=None, mentions=1, docs=None):
    variants = sorted(variants or [name])
    return {
        "party_key": key,
        "canonical_name": name,
        "normalized_name": lane.normalize_name(name),
        "name_variants": variants,
        "variant_count": len(variants),
        "roles": ["customer"],
        "location_identifiers": [],
        "addresses": [],
        "mention_count": mentions,
        "source_document_ids": docs or [f"doc-{key}"],
        "source_document_count": len(docs or [1]),
        "spacing_variants": [],
        "canonical_name_declared": False,
        "needs_review": False,
    }


def decide(kind, a, b, **extra):
    return {"decision": kind, "a": a, "b": b, **extra}


def test_one_company_in_two_spellings_becomes_one_party_with_both():
    parties = [
        party("PTY-000001", "OFFICE FURNISHINGS & DESIGN", mentions=3),
        party("PTY-000002", "Office Furnishings and Design Concept", mentions=1),
    ]
    kept, applied, unmatched, removed = lane.apply_party_decisions(
        parties,
        [
            decide(
                "same_party",
                "OFFICE FURNISHINGS & DESIGN",
                "office furnishings and design  concept",
                evidence="ofdc-inc.com",
            )
        ],
        AUTH,
    )
    assert [p["party_key"] for p in kept] == ["PTY-000001"]
    survivor = kept[0]
    assert survivor["name_variants"] == [
        "OFFICE FURNISHINGS & DESIGN",
        "Office Furnishings and Design Concept",
    ]
    assert survivor["mention_count"] == 4
    assert survivor["source_document_ids"] == ["doc-PTY-000001", "doc-PTY-000002"]
    assert survivor["canonical_name"] == "OFFICE FURNISHINGS & DESIGN"
    assert applied == [
        {
            "a": "OFFICE FURNISHINGS & DESIGN",
            "b": "office furnishings and design  concept",
            "decision": "same_party",
            "authorization": AUTH,
            "evidence": "ofdc-inc.com",
            "surviving_party_key": "PTY-000001",
            "absorbed_party_key": "PTY-000002",
            "canonical_name": "OFFICE FURNISHINGS & DESIGN",
        }
    ]
    assert unmatched == [] and removed == []


def test_a_declared_name_must_be_one_the_pages_print():
    """The master is named from the page; a public name belongs beside it."""
    parties = [
        party("PTY-000001", "Elwood Management", mentions=2),
        party("PTY-000002", "Elwood Investment Management"),
    ]
    kept, applied, _, _ = lane.apply_party_decisions(
        parties,
        [
            decide(
                "same_party",
                "Elwood Management",
                "Elwood Investment Management",
                canonical_name="elwood investment management",
            )
        ],
        AUTH,
    )
    assert kept[0]["canonical_name"] == "Elwood Investment Management"
    assert kept[0]["normalized_name"] == lane.normalize_name("Elwood Investment Management")
    assert kept[0]["canonical_name_declared"] is True
    assert applied[0]["canonical_name"] == "Elwood Investment Management"

    parties = [
        party("PTY-000001", "Elwood Management", mentions=2),
        party("PTY-000002", "Elwood Investment Management"),
    ]
    kept, applied, unmatched, _ = lane.apply_party_decisions(
        parties,
        [
            decide(
                "same_party",
                "Elwood Management",
                "Elwood Investment Management",
                canonical_name="Elwood Management L.P.",
            )
        ],
        AUTH,
    )
    assert len(kept) == 2 and applied == []
    assert unmatched[0]["reason"] == "declared_name_is_not_a_printed_spelling"


def test_a_branch_keeps_its_own_account_under_its_parent():
    parties = [
        party("PTY-000001", "EMPALL OFFICE, INC.", mentions=9),
        party("PTY-000002", "Empall Office - Tampa"),
    ]
    kept, applied, _, _ = lane.apply_party_decisions(
        parties, [decide("branch_of", "EMPALL OFFICE, INC.", "Empall Office - Tampa")], AUTH
    )
    parent, branch = kept
    assert len(kept) == 2
    assert branch["parent_party_key"] == "PTY-000001"
    assert branch["parent_name"] == "EMPALL OFFICE, INC."
    assert parent["branch_party_keys"] == ["PTY-000002"]
    assert applied[0]["parent_party_key"] == "PTY-000001"
    assert applied[0]["branch_party_key"] == "PTY-000002"


def test_a_branch_hangs_from_the_party_that_survives_a_merge_listed_after_it():
    parties = [
        party("PTY-000001", "CORNERWISE DESIGN SERVICES", mentions=5),
        party("PTY-000002", "CORNERWISE DESIGN"),
        party("PTY-000003", "2053 Cornerwise Design Services-Jacksonville"),
    ]
    kept, applied, _, _ = lane.apply_party_decisions(
        parties,
        [
            decide(
                "branch_of", "CORNERWISE DESIGN", "2053 Cornerwise Design Services-Jacksonville"
            ),
            decide("same_party", "CORNERWISE DESIGN SERVICES", "CORNERWISE DESIGN"),
        ],
        AUTH,
    )
    assert [d["decision"] for d in applied] == ["same_party", "branch_of"]
    assert [p["party_key"] for p in kept] == ["PTY-000001", "PTY-000003"]
    assert kept[1]["parent_party_key"] == "PTY-000001"


def test_two_parties_kept_apart_are_recorded_and_a_joined_pair_is_reported_not_split():
    parties = [
        party("PTY-000001", "Paycard Platinum Lounge SF"),
        party("PTY-000002", "Paycard Platinum Lounge Slc"),
    ]
    kept, applied, unmatched, _ = lane.apply_party_decisions(
        parties,
        [decide("different_parties", "Paycard Platinum Lounge SF", "Paycard Platinum Lounge Slc")],
        AUTH,
    )
    assert len(kept) == 2
    assert applied[0]["party_keys"] == ["PTY-000001", "PTY-000002"]
    assert unmatched == []

    joined = [
        party(
            "PTY-000001",
            "Clever Office Quarters - NY",
            ["Clever Office Quarters - MA", "Clever Office Quarters - NY"],
        )
    ]
    for kind in ("different_parties", "branch_of"):
        kept, applied, unmatched, _ = lane.apply_party_decisions(
            joined,
            [decide(kind, "Clever Office Quarters - NY", "Clever Office Quarters - MA")],
            AUTH,
        )
        assert kept == joined and applied == []
        assert unmatched[0]["reason"] == "clustering_already_joined_them"
        assert unmatched[0]["party_key"] == "PTY-000001"


def test_a_pair_already_one_party_is_applied_as_such():
    joined = [party("PTY-000001", "DAX Tampa", ["DAX - FL - Tampa", "DAX Tampa"])]
    kept, applied, unmatched, _ = lane.apply_party_decisions(
        joined, [decide("same_party", "DAX - FL - Tampa", "DAX Tampa")], AUTH
    )
    assert kept == joined and unmatched == []
    assert applied[0]["already_one_party"] is True


def test_a_project_in_a_party_column_leaves_the_master_and_is_retained():
    parties = [
        party(
            "PTY-000001",
            "2200 Bayshore Avenue",
            ["2200 Bayshore Avenue", "2200 Bayshore Avenue PAID"],
        ),
        party("PTY-000002", "K. D. Frost"),
    ]
    kept, applied, unmatched, removed = lane.apply_party_decisions(
        parties,
        [
            decide(
                "not_a_party",
                "2200 Bayshore Avenue PAID",
                "2200 Bayshore Avenue",
                evidence="a site with a PAID stamp",
            ),
            decide("not_a_party", "2200 Bayshore Avenue", "2200 Bayshore Avenue PAID"),
        ],
        AUTH,
    )
    assert [p["party_key"] for p in kept] == ["PTY-000002"]
    assert applied[0]["removed_party_keys"] == ["PTY-000001"]
    assert removed[0]["party_key"] == "PTY-000001"
    assert removed[0]["removed_by"]["evidence"] == "a site with a PAID stamp"
    # The second decision names a party the first already removed: reported.
    assert unmatched[0]["reason"] == "neither_name_resolved_to_a_party"


def test_a_name_that_resolved_to_no_party_is_reported_with_the_name():
    parties = [party("PTY-000001", "CORPORATE QUARTERS-FL")]
    kept, applied, unmatched, _ = lane.apply_party_decisions(
        parties, [decide("same_party", "CORPORATE QUARTERS-FL", "CORPORATE QUARTERS OF...")], AUTH
    )
    assert kept == parties and applied == []
    assert unmatched[0]["reason"] == "a_name_resolved_to_no_party"
    assert unmatched[0]["missing"] == ["CORPORATE QUARTERS OF..."]


def test_a_decided_pair_leaves_adjudication_and_keeps_its_score():
    ambiguous = [
        {
            "a": "2200 Bayshore Avenue PAID",
            "b": "2200 Bayshore Avenue",
            "name_score": 0.8035,
            "decision": "pending_adjudication",
        },
        {
            "a": "CORPORATE QUARTERS-FL",
            "b": "CORPORATE QUARTERS OF...",
            "name_score": 0.8,
            "decision": "pending_adjudication",
        },
        {"a": None, "b": None, "blocking_key": "northgate", "decision": "pending_adjudication"},
    ]
    pending, settled = lane.settle_pending(
        ambiguous, [decide("not_a_party", "2200 bayshore avenue", "2200 Bayshore Avenue PAID")]
    )
    assert [p["a"] for p in pending] == ["CORPORATE QUARTERS-FL", None]
    assert settled == [
        {
            "a": "2200 Bayshore Avenue PAID",
            "b": "2200 Bayshore Avenue",
            "name_score": 0.8035,
            "decision": "decided",
            "party_decision": "not_a_party",
        }
    ]


@pytest.mark.parametrize(
    "content, message",
    [
        ([], "must name the authorization"),
        ({"authorization": "  ", "decisions": []}, "must name the authorization"),
        ({"authorization": AUTH, "decisions": {}}, "'decisions' must be a list"),
        ({"authorization": AUTH, "decisions": ["same_party"]}, "decision 0 must be one of"),
        (
            {"authorization": AUTH, "decisions": [{"decision": "merge", "a": "x", "b": "y"}]},
            "decision 0 must be one of",
        ),
        (
            {"authorization": AUTH, "decisions": [{"decision": "same_party", "a": "x", "b": " "}]},
            "must name both",
        ),
    ],
)
def test_a_decision_file_that_cannot_be_trusted_is_refused(tmp_path, content, message):
    path = tmp_path / "decisions.json"
    path.write_text(json.dumps(content))
    with pytest.raises(SystemExit, match=message):
        lane.load_party_decisions(path)


def test_the_master_carries_each_decision_and_the_log_settles_its_pair(tmp_path):
    """End to end: three pairs clustering leaves pending, and one it never saw.

    The site is a building with a PAID stamp. A street address would now be
    refused before clustering, so the pair names the building without one.
    """
    names = [
        "Bayshore City Centre PAID",
        "Bayshore City Centre",
        "EMPALL OFFICE, INC.",
        "Empall Office - Tampa",
        "Elwood Investment Management",
        "Elwood Management",
        "K. D. Frost",
    ]
    records = tmp_path / "records.json"
    records.write_text(
        json.dumps(
            [{"document_id": f"d{i}", "header": {"customer_name": n}} for i, n in enumerate(names)]
        )
    )
    decisions = tmp_path / "decisions.json"
    decisions.write_text(
        json.dumps(
            {
                "authorization": AUTH,
                "decisions": [
                    decide("not_a_party", "Bayshore City Centre PAID", "Bayshore City Centre"),
                    decide(
                        "same_party",
                        "Elwood Investment Management",
                        "Elwood Management",
                        canonical_name="Elwood Investment Management",
                    ),
                    decide("branch_of", "EMPALL OFFICE, INC.", "Empall Office - Tampa"),
                    decide("same_party", "CORPORATE QUARTERS-FL", "CORPORATE QUARTERS OF..."),
                ],
            }
        )
    )
    out, log = tmp_path / "parties.json", tmp_path / "merges.json"
    sys.argv = [
        "entity_resolve.py",
        str(records),
        "--out",
        str(out),
        "--log",
        str(log),
        "--party-decisions",
        str(decisions),
        "--quiet",
    ]
    lane.main()
    written = json.loads(out.read_text())
    summary = written["summary"]
    assert summary["party_decisions_authorization"] == AUTH
    assert summary["party_decisions_supplied"] == 4
    assert summary["party_decisions_applied"] == 3
    assert summary["party_decisions_unmatched"] == 1
    assert summary["parties_removed_as_not_a_party"] == 2
    assert summary["pending_settled_by_decision"] == 3
    assert summary["pending_adjudication"] == 0
    assert summary["resolved_parties"] == 4
    assert sorted(p["canonical_name"] for p in written["parties"]) == [
        "EMPALL OFFICE, INC.",
        "Elwood Investment Management",
        "Empall Office - Tampa",
        "K. D. Frost",
    ]
    assert len(written["removed_as_not_a_party"]) == 2
    assert written["party_decisions_unmatched"][0]["missing"] == [
        "CORPORATE QUARTERS-FL",
        "CORPORATE QUARTERS OF...",
    ]
    assert any("3 party decisions were applied under" in f for f in summary["findings"])
    assert any("1 party decisions were not applied" in f for f in summary["findings"])
    merges = json.loads(log.read_text())
    assert merges["summary"]["settled_by_decision"] == 3
    assert merges["pending_adjudication"] == []
    assert {s["party_decision"] for s in merges["settled_by_decision"]} == {
        "not_a_party",
        "same_party",
        "branch_of",
    }


def test_without_decisions_the_summary_says_none_were_given(tmp_path):
    records = tmp_path / "records.json"
    records.write_text(json.dumps([{"document_id": "d1", "header": {"customer_name": "KD Frost"}}]))
    out, log = tmp_path / "parties.json", tmp_path / "merges.json"
    sys.argv = ["entity_resolve.py", str(records), "--out", str(out), "--log", str(log), "--quiet"]
    lane.main()
    summary = json.loads(out.read_text())["summary"]
    assert summary["party_decisions_authorization"] == ""
    assert summary["party_decisions_supplied"] == 0
    assert not any("party decisions" in f for f in summary["findings"])
    assert json.loads(log.read_text())["settled_by_decision"] == []


def test_a_decision_file_names_its_authorization(tmp_path):
    path = tmp_path / "decisions.json"
    decisions = [decide("branch_of", "EMPALL OFFICE, INC.", "Empall Office - Tampa")]
    path.write_text(json.dumps({"authorization": AUTH, "decisions": decisions}))
    assert lane.load_party_decisions(path) == (AUTH, decisions)
