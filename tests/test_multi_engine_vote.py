"""Tests for settling an open field from readings the run already retained."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

lane = importlib.import_module("multi_engine_vote")


def handoff(provider, model, records):
    return {"provider": provider, "model": model, "records": records}


def record(document_id="p1", header=None, lines=None):
    return {"document_id": document_id, "header": header or {}, "lines": lines or []}


def cell(value=None, accepted=False):
    return {"value": value, "accepted": accepted, "candidate_values": None}


def write(path, value):
    path.write_text(json.dumps(value))
    return str(path)


def test_an_agreed_value_reaches_the_views_the_controls_read():
    """Written to the fields alone, 3,905 agreed values never reached attribution."""
    document = {
        "document_id": "p1",
        "fields": {"lines[0].job_number": {"value": None, "candidate_values": ["12951", "12591"]}},
        "header": {"brand_name": {"value": "Acme", "mapped_from_source_label": "VENDOR"}},
        "lines": [{"job_number": {"value": None}}],
    }
    resolutions = [
        {
            "document_id": "p1",
            "field": "lines[0].job_number",
            "resolved_value": "12951",
            "agreeing_vendors": ["google", "openai"],
            "vendors_read": 3,
        }
    ]
    [updated], applied = lane.apply_resolutions([document], resolutions)
    assert applied == 1
    assert updated["lines"][0]["job_number"]["value"] == "12951"
    # A mapping written to the header view alone is kept.
    assert updated["header"]["brand_name"]["mapped_from_source_label"] == "VENDOR"
    # A document no resolution names keeps its views exactly as they were.
    [same], _ = lane.apply_resolutions([{**document, "document_id": "p2"}], resolutions)
    assert same["lines"] is document["lines"]


def test_a_number_printed_differently_is_not_a_disagreement():
    assert lane.comparable("4,098.49") == lane.comparable("$4098.49")
    assert lane.comparable("(662.89)") == lane.comparable("-662.89")
    assert lane.comparable(" Acme  Co ") == lane.comparable("acme co")
    assert lane.comparable("") is None and lane.comparable(None) is None


def test_two_lanes_of_one_vendor_are_one_reading(tmp_path):
    """Counting lanes rather than vendors manufactures the independence
    consensus exists to protect: a router does not create a second vendor."""
    a = write(
        tmp_path / "a.json",
        handoff("openrouter", "google/gemini-2.5-flash", [record(header={"total": cell("10")})]),
    )
    b = write(
        tmp_path / "b.json",
        handoff("google", "gemini-2.5-flash", [record(header={"total": cell("10")})]),
    )
    votes, vendors = lane.collect_votes([a, b])
    assert list(vendors) == ["google"]
    assert votes[("p1", "header.total")] == {"google": "10"}


def test_an_unresolvable_vendor_is_refused_rather_than_counted(tmp_path):
    path = write(
        tmp_path / "auto.json",
        handoff("openrouter", "openrouter/auto", [record(header={"total": cell("10")})]),
    )
    with pytest.raises(ValueError, match="cannot resolve the model vendor"):
        lane.collect_votes([path])


def test_a_tie_is_a_finding_and_not_a_resolution():
    documents = [record(header={"total": cell()})]
    documents[0]["fields"] = {"header.total": cell()}
    votes = {("p1", "header.total"): {"openai": "10", "x-ai": "11"}}
    resolutions, unresolved, counts = lane.vote(documents, votes)
    assert resolutions == []
    assert unresolved[0]["reason"] == "vendors_tied"
    assert counts["vendors_tied"] == 1


def test_a_majority_resolves_and_carries_how_many_agreed():
    """Two vendors agreeing and three agreeing are different evidence -- 96.3%
    against 100% measured -- so the count rides on the acceptance."""
    documents = [{"document_id": "p1", "fields": {"header.total": cell()}}]
    votes = {("p1", "header.total"): {"openai": "10", "google": "10", "x-ai": "11"}}
    resolutions, _, _ = lane.vote(documents, votes)
    assert resolutions[0]["resolved_value"] == "10"
    assert resolutions[0]["agreeing_vendors"] == 2
    amended, applied = lane.apply_resolutions(documents, resolutions)
    assert applied == 1
    acceptance = amended[0]["fields"]["header.total"]["acceptance"]
    assert acceptance["accepted_by"] == "agreed_by_independent_vendors"
    assert acceptance["agreeing_vendors"] == 2


def test_an_accepted_value_is_never_overruled():
    documents = [{"document_id": "p1", "fields": {"header.total": cell("9", accepted=True)}}]
    votes = {("p1", "header.total"): {"openai": "10", "google": "10"}}
    resolutions, unresolved, counts = lane.vote(documents, votes)
    assert (resolutions, unresolved) == ([], [])
    assert counts["open"] == 0


def test_a_document_that_states_the_value_elsewhere_breaks_its_own_tie():
    """`$19,954.00` is printed four times on a Bill Payment page, so a tie in one
    of those cells is settled by the others -- the document agreeing with itself."""
    documents = [
        {
            "document_id": "p1",
            "fields": {
                "header.total_amount": cell("19954.00", accepted=True),
                "header.total_paid": cell(),
            },
        }
    ]
    votes = {("p1", "header.total_paid"): {"openai": "19,954.00", "x-ai": "1995.40"}}
    resolutions, _, counts = lane.vote(documents, votes)
    assert resolutions[0]["resolved_value"] == "19,954.00"
    assert counts["tie_broken_within_document"] == 1
    amended, _ = lane.apply_resolutions(documents, resolutions)
    assert amended[0]["fields"]["header.total_paid"]["acceptance"]["accepted_by"] == (
        "matches_a_settled_value_in_the_same_document"
    )


def test_an_extractor_breaks_a_tie_only_when_it_read_exactly_one_side():
    documents = [{"document_id": "p1", "fields": {"header.buyer": cell()}}]
    votes = {("p1", "header.buyer"): {"openai": "Acme Co", "x-ai": "Zeta Ltd"}}
    evidence = {"p1": " invoice acme co 100 "}
    resolutions, _, counts = lane.vote(documents, votes, evidence=evidence)
    assert resolutions[0]["resolved_value"] == "Acme Co"
    assert counts["tie_broken_by_extractor"] == 1
    amended, _ = lane.apply_resolutions(documents, resolutions)
    # Presence proves the string is on the page, not that it belongs in that
    # cell, so it is never labelled vendor agreement.
    assert amended[0]["fields"]["header.buyer"]["acceptance"]["accepted_by"] == (
        "tie_broken_by_independent_extractor"
    )
    # Both sides present decides nothing.
    both = {"p1": " acme co and zeta ltd "}
    resolutions, unresolved, _ = lane.vote(documents, votes, evidence=both)
    assert resolutions == [] and unresolved[0]["reason"] == "vendors_tied"


def test_the_command_reports_what_it_could_not_settle(tmp_path, capsys):
    records = {
        "documents": [
            {
                "document_id": "p1",
                "fields": {"header.total": cell(), "header.other": cell()},
            }
        ]
    }
    a = write(
        tmp_path / "a.json",
        handoff("openai", "gpt-x", [record(header={"total": cell("10"), "other": cell("1")})]),
    )
    b = write(
        tmp_path / "b.json",
        handoff(
            "openrouter", "x-ai/grok", [record(header={"total": cell("10"), "other": cell("2")})]
        ),
    )
    assert (
        lane.main(
            [
                write(tmp_path / "r.json", records),
                a,
                b,
                "--out",
                str(tmp_path / "out.json"),
                "--exceptions",
                str(tmp_path / "exc.json"),
                "--records-out",
                str(tmp_path / "amended.json"),
            ]
        )
        == 0
    )
    summary = json.loads((tmp_path / "out.json").read_text())["summary"]
    assert summary["resolved"] == 1
    assert summary["unresolved_reasons"]["vendors_tied"] == 1
    assert sorted(summary["vendors"]) == ["openai", "x-ai"]
    printed = capsys.readouterr().out
    assert "agreed_by_2_vendors" in printed and "vendors_tied" in printed


def test_one_vendor_is_not_agreement_however_many_lanes_it_ran(tmp_path):
    records = write(tmp_path / "r.json", {"documents": []})
    only = write(tmp_path / "a.json", handoff("openai", "gpt-x", [record()]))
    with pytest.raises(SystemExit, match="below the 2 required"):
        lane.main(
            [
                records,
                only,
                "--out",
                str(tmp_path / "o.json"),
                "--exceptions",
                str(tmp_path / "e.json"),
            ]
        )
    with pytest.raises(SystemExit, match="one reading is not agreement"):
        lane.main(
            [
                records,
                only,
                "--out",
                str(tmp_path / "o2.json"),
                "--exceptions",
                str(tmp_path / "e2.json"),
                "--minimum-vendors",
                "1",
            ]
        )


def test_malformed_inputs_are_refused(tmp_path):
    good = write(tmp_path / "a.json", handoff("openai", "gpt-x", [record()]))
    with pytest.raises(SystemExit, match="carries no document list"):
        lane.main(
            [
                write(tmp_path / "r.json", {"documents": "no"}),
                good,
                "--out",
                str(tmp_path / "o.json"),
                "--exceptions",
                str(tmp_path / "e.json"),
            ]
        )
    with pytest.raises(ValueError, match="not a provider handoff"):
        lane.collect_votes([write(tmp_path / "bad.json", {"records": "no"})])
    with pytest.raises(ValueError, match="must be an object"):
        lane.vote(["not a document"], {})
    with pytest.raises(ValueError, match="not an extractor artifact"):
        lane.extractor_evidence([write(tmp_path / "e2.json", {"records": "no"})])


def test_readings_are_collected_from_lines_and_malformed_rows_are_skipped(tmp_path):
    path = write(
        tmp_path / "a.json",
        handoff(
            "openai",
            "gpt-x",
            [
                record(
                    header={"total": cell("1"), "empty": cell(None)},
                    lines=[{"amount": cell("5")}, "not a line"],
                ),
                {"page_id": "p2", "header": {"total": cell("7")}},
                {"header": {"total": cell("9")}},  # no document identity
                "not a record",
            ],
        ),
    )
    votes, _ = lane.collect_votes([path])
    assert votes[("p1", "header.total")] == {"openai": "1"}
    assert votes[("p1", "lines[0].amount")] == {"openai": "5"}
    assert votes[("p2", "header.total")] == {"openai": "7"}
    # A field no engine gave a value for is absent, not present-and-empty.
    assert ("p1", "header.empty") not in votes


def test_a_field_no_vendor_read_is_reported_as_such():
    documents = [{"document_id": "p1", "fields": {"header.total": cell(), "x": "not a cell"}}]
    _, unresolved, counts = lane.vote(documents, {})
    assert unresolved[0]["reason"] == "no_vendor_read_this_field"
    assert counts["no_vendor_read_this_field"] == 1


def test_a_single_vendor_reading_is_below_the_bar():
    documents = [{"document_id": "p1", "fields": {"header.total": cell()}}]
    _, unresolved, counts = lane.vote(documents, {("p1", "header.total"): {"openai": "10"}})
    assert unresolved[0]["reason"] == "below_minimum_vendor_agreement"
    assert counts["below_minimum_vendor_agreement"] == 1


def test_extractor_evidence_is_read_from_tables_and_text(tmp_path):
    path = write(
        tmp_path / "e.json",
        {
            "records": [
                {
                    "page_id": "p1",
                    "document_text": "Invoice ACME",
                    "source_tables": [
                        {"source_rows": [{"cells": [{"evidence_text": "4,098.49"}]}]}
                    ],
                },
                {"document_text": "no page id"},
                "not a record",
            ]
        },
    )
    evidence = lane.extractor_evidence([path])
    assert "acme" in evidence["p1"] and "4 098 49" in evidence["p1"]
    # A bare list of records is accepted too, as the extractor lanes emit one.
    assert (
        lane.extractor_evidence(
            [write(tmp_path / "l.json", [{"page_id": "p2", "document_text": "Zeta"}])]
        )["p2"].strip()
        == "zeta"
    )


def test_a_tie_with_no_extractor_page_stays_open():
    documents = [{"document_id": "p9", "fields": {"header.buyer": cell()}}]
    votes = {("p9", "header.buyer"): {"openai": "A", "x-ai": "B"}}
    resolutions, unresolved, _ = lane.vote(documents, votes, evidence={"other": "text"})
    assert resolutions == [] and unresolved[0]["reason"] == "vendors_tied"


def test_quiet_prints_nothing(tmp_path, capsys):
    records = write(tmp_path / "r.json", {"documents": []})
    a = write(tmp_path / "a.json", handoff("openai", "gpt-x", [record()]))
    b = write(tmp_path / "b.json", handoff("openrouter", "x-ai/grok", [record()]))
    lane.main(
        [
            records,
            a,
            b,
            "--out",
            str(tmp_path / "o.json"),
            "--exceptions",
            str(tmp_path / "e.json"),
            "--quiet",
        ]
    )
    assert capsys.readouterr().out == ""


def test_partial_shapes_do_not_stop_the_collection(tmp_path):
    """Loop exits that only occur on shapes a real handoff genuinely produces:
    a record with no header, a line whose every cell is empty, and a settled
    field carrying no value at all."""
    path = write(
        tmp_path / "a.json",
        handoff(
            "openai",
            "gpt-x",
            [
                {"document_id": "p1", "lines": [{"amount": cell(None)}]},  # header absent
                record("p2", header={"total": cell("3")}),
            ],
        ),
    )
    votes, _ = lane.collect_votes([path])
    assert list(votes) == [("p2", "header.total")]
    # A settled cell with no value contributes nothing to the document's own
    # evidence, so a tie cannot be broken against an empty string.
    assert lane.settled_values({"fields": {"a": cell(None, accepted=True)}}) == set()


def test_a_proposal_run_does_not_claim_to_have_applied_anything(tmp_path, capsys):
    records = write(
        tmp_path / "r.json",
        {"documents": [{"document_id": "p1", "fields": {"header.total": cell()}}]},
    )
    a = write(
        tmp_path / "a.json", handoff("openai", "gpt-x", [record(header={"total": cell("10")})])
    )
    b = write(
        tmp_path / "b.json",
        handoff("openrouter", "x-ai/grok", [record(header={"total": cell("10")})]),
    )
    lane.main(
        [records, a, b, "--out", str(tmp_path / "o.json"), "--exceptions", str(tmp_path / "e.json")]
    )
    printed = capsys.readouterr().out
    assert "agreed_by_2_vendors" in printed
    assert "applied to records" not in printed


def test_a_subset_read_votes_on_its_own_pages_and_passes_the_rest_through(tmp_path, capsys):
    """Handoffs that read 24 pages cannot speak for the corpus's other documents."""
    open_total = {"header.total": cell()}
    records = write(
        tmp_path / "r.json",
        {
            "documents": [
                {"document_id": "p1", "fields": dict(open_total)},
                {"document_id": "p2", "fields": dict(open_total)},
            ]
        },
    )
    both = [record("p1", header={"total": cell("10")}), record("p2", header={"total": cell("10")})]
    a = write(tmp_path / "a.json", handoff("openai", "gpt-x", both))
    b = write(tmp_path / "b.json", handoff("openrouter", "x-ai/grok", both))
    manifest = write(tmp_path / "manifest.json", {"pages": [{"page_id": "p1"}]})
    common = ["--pages", manifest, "--exceptions", str(tmp_path / "e.json")]
    lane.main(
        [records, a, b, *common, "--out", str(tmp_path / "o.json")]
        + ["--records-out", str(tmp_path / "amended.json")]
    )
    written = json.loads((tmp_path / "o.json").read_text())
    assert (written["summary"]["documents"], written["summary"]["documents_passed_through"]) == (
        1,
        1,
    )
    assert written["summary"]["pages_manifests"] == ["manifest.json"]
    assert [item["document_id"] for item in written["resolutions"]] == ["p1"]
    # The other document is neither voted on nor listed as read by no vendor.
    assert json.loads((tmp_path / "e.json").read_text())["exceptions"] == []
    amended = {
        d["document_id"]: d
        for d in json.loads((tmp_path / "amended.json").read_text())["documents"]
    }
    assert amended["p1"]["fields"]["header.total"]["accepted"] is True
    assert amended["p2"]["fields"] == open_total
    assert "passed through unchanged: 1" in capsys.readouterr().out

    # Rule 9: a manifest naming no page, or pages the records never hold, scopes
    # nothing and must not read as a vote that ran.
    unnamed = write(tmp_path / "unnamed.json", {"pages": [{"page_label": "x"}]})
    with pytest.raises(SystemExit, match="names no page"):
        lane.main(
            [
                records,
                a,
                b,
                "--pages",
                unnamed,
                "--out",
                str(tmp_path / "o2.json"),
                "--exceptions",
                str(tmp_path / "e2.json"),
            ]
        )
    elsewhere = write(tmp_path / "elsewhere.json", {"pages": [{"page_id": "p9"}]})
    with pytest.raises(SystemExit, match="manifests' pages"):
        lane.main(
            [
                records,
                a,
                b,
                "--pages",
                elsewhere,
                "--out",
                str(tmp_path / "o3.json"),
                "--exceptions",
                str(tmp_path / "e3.json"),
            ]
        )
    # A malformed document stays in scope, so it is refused rather than skipped.
    malformed = write(tmp_path / "m.json", {"documents": ["not a document"]})
    with pytest.raises(SystemExit, match="must be an object"):
        lane.main(
            [
                malformed,
                a,
                b,
                "--pages",
                manifest,
                "--out",
                str(tmp_path / "o4.json"),
                "--exceptions",
                str(tmp_path / "e4.json"),
            ]
        )
