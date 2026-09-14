"""Places is asked about dealers and brands only, within a cap, and decides nothing.

A Places answer rests on a business name, so a match is a proposal for client
review. These tests never reach Google: the request is answered by a fake.
"""

import io
import json
from urllib.error import URLError

import party_locations as lane
import pytest


def party(key, name, roles=("dealer",), mentions=1, variants=None):
    return {
        "party_key": key,
        "canonical_name": name,
        "roles": list(roles),
        "mention_count": mentions,
        "name_variants": variants or [name],
    }


def place(name, city="Tampa", state="FL", pid="p1", components=True):
    found = {
        "id": pid,
        "displayName": {"text": name},
        "formattedAddress": f"1 Main St, {city}, {state}",
    }
    if components:
        found["addressComponents"] = [
            {"longText": city, "shortText": city, "types": ["locality", "political"]},
            {"longText": "Florida", "shortText": state, "types": ["administrative_area_level_1"]},
            {"longText": "United States", "shortText": "US", "types": ["country"]},
        ]
        found["businessStatus"] = "OPERATIONAL"
    return found


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def opener_for(answers, seen=None):
    """A stand-in for urlopen answering each query from `answers`."""

    def opener(request, timeout):
        body = json.loads(request.data)
        if seen is not None:
            seen.append(({k.lower(): v for k, v in request.header_items()}, timeout))
        answer = answers[body["textQuery"]]
        if isinstance(answer, Exception):
            raise answer
        return Response(json.dumps(answer).encode())

    return opener


def test_only_dealers_and_brands_are_asked_about_most_mentioned_first():
    parties = [
        party("PTY-1", "Grove Realty Trust Inc", roles=("customer",), mentions=50),
        party("PTY-2", "KD Frost", mentions=10),
        party("PTY-3", "HALVOR", roles=("brand",), mentions=5),
        party("PTY-4", "Office Surr...", mentions=9),
        party("PTY-5", "Cornerwi", mentions=8),
        party("PTY-6", "Cornerwise Design Services", mentions=3),
        party("PTY-7", "ABC", mentions=7),
        party("PTY-8", "QRC Business Interiors", mentions=6),
    ]
    chosen, unsent, passed = lane.select(parties, 2, known=["QRC BUSINESS INTERIORS"])
    assert [p["canonical_name"] for p in chosen] == ["KD Frost", "HALVOR"]
    assert [p["canonical_name"] for p in unsent] == ["Cornerwise Design Services"]
    assert passed == {"name_cut_off_or_too_short": 3, "already_covered_by_a_public_source": 1}


def test_a_lone_word_several_firms_begin_is_not_asked_about():
    names = [
        "Corporate",
        "CORPORATE QUARTERS-FL",
        "Corporate Notions",
        "KD Frost",
        "KD FROST OFFICE FURNITURE",
        "Workfields",
        "Workfields Inc - Tampa",
        "***",
    ]
    assert lane.cut_off_names(names) == {"Corporate"}


def test_the_key_is_read_from_the_named_variable(monkeypatch):
    monkeypatch.setenv("TEST_PLACES_KEY", "k-123")
    sent = lane.headers("key", "TEST_PLACES_KEY")
    assert sent["X-Goog-Api-Key"] == "k-123"
    assert sent["X-Goog-FieldMask"] == lane.FIELD_MASK
    monkeypatch.delenv("TEST_PLACES_KEY")
    with pytest.raises(RuntimeError, match="places_key_missing: TEST_PLACES_KEY"):
        lane.headers("key", "TEST_PLACES_KEY")


class Creds:
    """Stand-in credentials; the bearer is a placeholder, not a secret."""

    def __init__(self, bearer="placeholder", quota=None):
        self.token, self.quota_project_id, self.refreshed = bearer, quota, False

    def refresh(self, request):
        self.refreshed = True


def test_adc_sends_a_bearer_token_billed_to_its_quota_project():
    sent = lane.headers("adc", "unused", lambda: (Creds(quota="billing-q"), "default-p"))
    assert sent["Authorization"] == "Bearer placeholder"
    assert sent["X-Goog-User-Project"] == "billing-q"
    assert (
        lane.headers("adc", "unused", lambda: (Creds(), "default-p"))["X-Goog-User-Project"]
        == "default-p"
    )
    with pytest.raises(RuntimeError, match="places_adc_unavailable"):
        lane.headers("adc", "unused", lambda: (Creds(bearer=None), "default-p"))


def test_default_credentials_refreshes_application_default_credentials(monkeypatch):
    import google.auth

    creds = Creds()
    calls = []

    def fake_default(scopes):
        calls.append(scopes)
        return creds, "proj"

    monkeypatch.setattr(google.auth, "default", fake_default)
    assert lane.default_credentials() == (creds, "proj")
    assert creds.refreshed and calls == [[lane.ADC_SCOPE]]


def test_a_close_name_is_accepted_and_every_candidate_kept():
    seen = []
    answers = {
        "WORKFIELDS commercial furniture": {
            "places": [
                place("Workfield Design Studio", pid="x"),
                place("Workfields", city="Orlando", pid="w1"),
            ]
        }
    }
    variants = ["WORKFIELDS", "Workfield"]
    accepted, lookups, failures = lane.look_up(
        [party("PTY-1", "WORKFIELDS", variants=variants)],
        {"X-Test": "1"},
        7,
        "commercial furniture",
        opener_for(answers, seen),
    )
    assert accepted == [
        {
            "names": ["WORKFIELDS", "Workfield"],
            "identity": "Workfields",
            "city": "Orlando",
            "state": "FL",
            "country": "US",
            "address": "1 Main St, Orlando, FL",
            "website": "",
            "business_status": "OPERATIONAL",
            "source": "google_places:w1",
            "confidence": "probable",
            "match_score": 1.0,
        }
    ]
    assert lookups[0]["accepted"] is True and len(lookups[0]["candidates"]) == 2
    assert lookups[0]["raw"] == answers["WORKFIELDS commercial furniture"]["places"]
    assert failures == {}
    assert seen == [({"x-test": "1"}, 7)]


def test_a_distant_name_is_kept_but_not_accepted():
    answers = {
        "Kingfisher": {"places": [place("Kingfisher Alley Restaurant and Bar", components=False)]}
    }
    accepted, lookups, _ = lane.look_up(
        [party("PTY-1", "Kingfisher")], {}, 5, opener=opener_for(answers)
    )
    assert accepted == []
    assert lookups[0]["accepted"] is False
    assert lookups[0]["candidates"][0]["city"] == ""


def candidate(name, city="Tampa", state="FL"):
    return lane.summarize(place(name, city=city, state=state))


def test_every_word_of_the_name_must_be_accounted_for():
    """The New York BGE does not account for `de Puer`; KD Frost's full name does."""
    assert not lane.acceptable(
        "BGE Contract Furniture de Puer", candidate("BGE Contract Furniture", "New York", "NY")
    )
    assert lane.acceptable(
        "KD Frost", candidate("KD Frost Architectural Interior Supply", "West Palm Beach")
    )
    assert lane.acceptable("W B PINE / NY", candidate("WB Pine", "New York", "NY"))
    assert lane.acceptable("The QDF Gr", candidate("QDF Group - Hawksmoor Dealer", "Dayton", "OH"))
    assert not lane.acceptable(
        "Office Quarters Inc - NY", candidate("Office Quarters, Inc.", "Louisville", "KY")
    )
    shop = dict(
        candidate("Fine-Line Furniture & Accessories", "Coral Gables"),
        address="4217 Alhambra plaza Blvd, Coral Gables, FL 33146, USA",
    )
    assert not lane.acceptable("Alhambra Plaza", shop)
    assert lane.acceptable(
        "Lynx Rothman West Palm Beach", candidate("Lynx Rothman LLP", "West Palm Beach")
    )


def test_a_lone_word_needs_six_letters_and_a_close_match():
    assert not lane.acceptable("THE COVES", candidate("Coves & Co LLC"))
    assert not lane.acceptable("Kingfisher", candidate("Kingfisher Alley Restaurant and Bar"))
    assert lane.acceptable("HALVOR", candidate("HALVOR", "Stewartville", "MN"))
    assert lane.acceptable("BRENNER BEST LLC", candidate("BrennerBest", "Berkeley Heights", "NJ"))


def test_a_place_is_neither_asked_about_nor_accepted():
    """A building suite names where a project was delivered, not a business."""
    assert lane.address_like("One Bayfront Suite 540")
    assert lane.address_like("3-USA")
    assert not lane.address_like("Suite Dreams Interiors")
    assert not lane.address_like("Aline 1 Solutions")
    assert lane.address_like("Miami, FL")
    assert not lane.address_like("Norcross - Miami, FL")
    parties = [
        party("PTY-1", "Harbour Point Suite 605E", mentions=9),
        party("PTY-2", "HALVOR", mentions=1),
    ]
    chosen, _, passed = lane.select(parties, 5)
    assert [p["canonical_name"] for p in chosen] == ["HALVOR"]
    assert passed == {"looks_like_an_address": 1}
    assert not lane.acceptable("Meridiana Suite 160", candidate("Meridiana Suite 160"))
    assert not lane.acceptable("Alhambra Plaza", candidate("2020 Alhambra plaza", "Coral Gables"))


def test_a_one_letter_word_must_be_a_whole_word_of_the_candidate():
    assert not lane.acceptable(
        "INTERIOR E", candidate("G&F Interior Design (Direct Office Furniture)")
    )
    assert lane.acceptable("D Hall", candidate("D.Hall Design", "Coral Gables"))


def test_a_candidate_outside_the_named_countries_is_kept_but_not_accepted():
    malaysia = dict(candidate("I Room Furniture Dot Com", "Ulu Tiram", "JH"), country="MY")
    assert lane.acceptable("i ROOM FURNITURE INC.", malaysia)
    assert not lane.acceptable("i ROOM FURNITURE INC.", malaysia, countries={"US", "CA"})


def test_retained_candidates_are_decided_again_without_a_request(tmp_path, capsys):
    """A refined rule costs no request: every candidate was kept."""
    jc = candidate("KD Frost Architectural Interior Supply", "West Palm Beach")
    prior = {
        "auth": "adc",
        "lookups": [
            {
                "party_key": "PTY-1",
                "name": "KD Frost",
                "query": "KD Frost",
                "score": 0.38,
                "accepted": False,
                "candidates": [jc],
                "raw": [],
            },
            {
                "party_key": "PTY-2",
                "name": "Alpha Dealer",
                "query": "Alpha Dealer",
                "error": "places_request_failed",
            },
            {
                "party_key": "PTY-3",
                "name": "Kingfisher",
                "query": "Kingfisher",
                "score": 0.15,
                "accepted": False,
                "candidates": [candidate("Kingfisher Alley Restaurant and Bar")],
                "raw": [],
            },
        ],
    }
    master = write(
        tmp_path / "parties.json",
        {
            "parties": [
                party("PTY-1", "KD Frost", variants=["KD Frost", "KD FROST OFFICE FURNITURE"])
            ]
        },
    )
    previous = write(tmp_path / "places_01.json", prior)
    out = tmp_path / "places_02.json"

    def refuse(request, timeout):
        raise AssertionError("no request may be sent when re-deciding")

    assert lane.main([master, "--out", str(out), "--from-lookups", previous], opener=refuse) == 0
    report = json.loads(out.read_text())
    assert report["rescored_from"] == previous
    assert report["summary"]["requests_sent"] == 0
    assert report["summary"]["failures"] == {"places_request_failed": 1}
    assert report["parties"][0]["names"] == ["KD FROST OFFICE FURNITURE", "KD Frost"]
    assert report["parties"][0]["city"] == "West Palm Beach"
    assert report["findings"][1].startswith("Decided again from the candidates retained in")
    assert "requests sent: 0" in capsys.readouterr().out


def test_a_failed_or_invalid_answer_is_counted_never_guessed():
    answers = {"Alpha Dealer": URLError("down"), "Beta Dealer": [], "Gamma Dealer": {}}
    parties = [
        party("PTY-1", "Alpha Dealer"),
        party("PTY-2", "Beta Dealer"),
        party("PTY-3", "Gamma Dealer"),
    ]
    accepted, lookups, failures = lane.look_up(parties, {}, 5, opener=opener_for(answers))
    assert accepted == []
    assert failures == {"places_request_failed": 1, "places_response_invalid": 1}
    assert [entry.get("error", "") for entry in lookups] == [
        "places_request_failed",
        "places_response_invalid",
        "",
    ]
    assert lookups[2]["candidates"] == [] and lookups[2]["score"] == 0.0


def write(path, data):
    path.write_text(json.dumps(data))
    return str(path)


@pytest.fixture
def master(tmp_path):
    parties = [
        party("PTY-1", "Alpha Dealer", mentions=3),
        party("PTY-2", "Beta Dealer", mentions=2),
    ]
    return write(tmp_path / "parties.json", {"parties": parties + [party("PTY-3", "Gamma Dealer")]})


@pytest.fixture(autouse=True)
def no_project_env(monkeypatch):
    monkeypatch.setattr(lane, "load_project_env", lambda: {})


def test_main_asks_within_the_cap_and_writes_no_key(tmp_path, master, monkeypatch, capsys):
    monkeypatch.setenv("TEST_PLACES_KEY", "k-secret")
    out = tmp_path / "locations.json"
    answers = {"Alpha Dealer": {"places": [place("Alpha Dealer")]}, "Beta Dealer": {}}
    argv = [
        master,
        "--out",
        str(out),
        "--enabled",
        "--auth",
        "key",
        "--api-key-env",
        "TEST_PLACES_KEY",
    ]
    assert lane.main(argv + ["--max-requests", "2"], opener=opener_for(answers)) == 0
    report = json.loads(out.read_text())
    assert report["summary"]["requests_sent"] == 2
    assert report["summary"]["parties_accepted"] == 1
    assert report["summary"]["not_asked_over_the_limit"] == 1
    assert "limit of 2 was reached" in report["findings"][1]
    assert "k-secret" not in out.read_text()
    assert "requests sent: 2 (limit 2)" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="Party location lookup failed"):
        lane.main(argv, opener=opener_for(answers))


def test_main_skips_names_a_public_source_covers(tmp_path, master, capsys):
    known = write(
        tmp_path / "known.json", {"parties": [{"names": ["Gamma Dealer", "Beta Dealer"]}]}
    )
    out = tmp_path / "locations.json"
    answers = {"Alpha Dealer": {}}
    argv = [
        master,
        "--out",
        str(out),
        "--enabled",
        "--auth",
        "adc",
        "--skip-known",
        known,
        "--quiet",
    ]
    lane.main(argv, opener=opener_for(answers), credentials=lambda: (Creds(quota="q"), "p"))
    report = json.loads(out.read_text())
    assert report["summary"]["passed_over"] == {"already_covered_by_a_public_source": 2}
    assert report["findings"][1] == "Every selected party was asked about within the per-run limit."
    assert capsys.readouterr().out == ""


def test_main_refuses_when_places_is_disabled_or_unbounded(tmp_path, master):
    out = str(tmp_path / "locations.json")
    with pytest.raises(SystemExit, match="disabled"):
        lane.main([master, "--out", out, "--no-enabled"])
    with pytest.raises(SystemExit, match="--max-requests must be positive"):
        lane.main([master, "--out", out, "--enabled", "--max-requests", "0"])


def test_a_party_master_must_be_an_object(tmp_path):
    bad = write(tmp_path / "parties.json", [1])
    with pytest.raises(SystemExit, match="not a party master"):
        lane.main([bad, "--out", str(tmp_path / "o.json"), "--enabled", "--auth", "adc"])
