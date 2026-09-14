"""Regression tests for the second-pass review corrections.

F29-F32 were found by comparing the tracked address normalizer against the
superseded `private/tmp/address_normalize.fixed.py` draft and by re-auditing the
first round of fixes for corner cases.
"""

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

address_normalize = importlib.import_module("address_normalize")
allocation_policy = importlib.import_module("allocation_policy")
completeness = importlib.import_module("completeness")
retrieval_store = importlib.import_module("retrieval_store")


# --- F29: a declared country contradicting its postal code must reach review ---


def test_a_country_and_postal_contradiction_is_registered():
    parsed, reasons = address_normalize.parse_address(
        "10 Downing Street, London, SW1A 1AA, UNITED STATES"
    )
    assert reasons == ["address_country_postal_mismatch"]
    assert parsed["country_code"] == "US"
    # Scoped matching alone dropped the postal silently and left it in the city
    # field with a clean format verdict.
    assert parsed["postal_code"] is None


def test_a_country_without_a_postal_pattern_is_never_judged():
    # France and Germany use five digits, the same shape as a US ZIP. Without a
    # pattern for them there is no basis to call that a mismatch.
    for raw in (
        "10 Rue de Rivoli, Paris, 75001, FRANCE",
        "Hauptstr 1, Berlin, 10115, GERMANY",
        "1-1 Chiyoda, Tokyo, 100-0001, JAPAN",
    ):
        _, reasons = address_normalize.parse_address(raw)
        assert reasons == [], raw


def test_a_declared_country_with_no_postal_at_all_is_not_flagged():
    parsed, reasons = address_normalize.parse_address("1 Main St, Springfield, UNITED STATES")
    assert reasons == []
    assert parsed["city"] == "Springfield"
    assert parsed["postal_code"] is None


def test_a_consistent_country_and_postal_is_not_flagged():
    for raw in (
        "1 Main St, Springfield, IL 62704, UNITED STATES",
        "500 King St W, Toronto ON M5V 1L9, CANADA",
        "10 Downing Street, London SW1A 1AA, UNITED KINGDOM",
        "1 Marina Blvd, Singapore 018989, SINGAPORE",
    ):
        _, reasons = address_normalize.parse_address(raw)
        assert reasons == [], raw


def test_the_mismatch_reaches_the_record_level_exception_queue():
    record = {
        "document_id": "d1",
        "header": {"buyer_address": {"value": "10 Downing St, London, SW1A 1AA, UNITED STATES"}},
    }
    normalized, exceptions = address_normalize.normalize_record(record)
    assert normalized["address_validation_status"] == "client_review_required"
    assert normalized["address_normalizations"][0]["format_validation_status"] == "review_required"
    assert [item["reason"] for item in exceptions] == ["address_country_postal_mismatch"]


# --- F30: a region in its own comma segment is a region, not the city ---


@pytest.mark.parametrize(
    "raw, city, region, postal",
    [
        ("1 Main St, Springfield, IL, 62704", "Springfield", "IL", "62704"),
        ("1 Main St, Springfield, IL 62704", "Springfield", "IL", "62704"),
        ("500 King St W, Toronto, ON, M5V 1L9, CANADA", "Toronto", "ON", "M5V1L9"),
        ("500 King St W, Toronto ON M5V 1L9, CANADA", "Toronto", "ON", "M5V1L9"),
        ("100 Calle Sol, San Juan, PR, 00901", "San Juan", "PR", "00901"),
    ],
)
def test_a_segmented_region_does_not_become_the_city(raw, city, region, postal):
    parsed, reasons = address_normalize.parse_address(raw)
    assert (parsed["city"], parsed["state_or_region"], parsed["postal_code"]) == (
        city,
        region,
        postal,
    )
    assert reasons == []


def test_a_canadian_province_is_only_recognized_once_the_country_resolves():
    # Without a country or a Canadian postal code, "ON" is not assumed to be a
    # province: that would be inference, not parsing.
    parsed, _ = address_normalize.parse_address("10 Elm St, Toronto, ON, 62704")
    assert parsed["state_or_region"] is None


def test_a_region_fragment_with_no_city_is_incomplete():
    parsed, reasons = address_normalize.parse_address("IL, 62704")
    assert parsed["state_or_region"] == "IL"
    assert parsed["city"] is None
    assert reasons == ["address_parse_incomplete"]


# --- F31: the lower end of the plausibility range ---


def test_a_negative_commission_rate_reaches_review():
    rule = {
        "rule_id": "r",
        "registry_version": "v",
        "scope": {},
        "policy": {
            "effective_rate_threshold": 0.02,
            "explicit_ship_to_precedence": False,
            "allow_sales_generated": True,
        },
    }
    leg = {
        "allocation_id": "a",
        "document_id": "d",
        "commissionable_amount": "1000",
        "commission_amount": "-50",
    }
    output, exception = allocation_policy.classify(leg, rule)
    # A clawback would otherwise fall below the threshold and classify as an
    # approved administrative allocation with no exception.
    assert output["reason"] == "allocation_effective_rate_negative"
    assert output["classification"] == "review_required"
    assert output["sales_credit_eligible"] is None
    assert exception["priority"] == "critical"


def test_a_zero_commission_remains_an_ordinary_administrative_allocation():
    rule = {
        "rule_id": "r",
        "registry_version": "v",
        "scope": {},
        "policy": {
            "effective_rate_threshold": 0.02,
            "explicit_ship_to_precedence": False,
            "allow_sales_generated": True,
        },
    }
    output, exception = allocation_policy.classify(
        {
            "allocation_id": "a",
            "document_id": "d",
            "commissionable_amount": "1000",
            "commission_amount": "0",
        },
        rule,
    )
    assert output["classification"] == "administrative_ship_to"
    assert exception is None


# --- F32: a blank amount column must still fall through to a total column ---


def test_a_blank_amount_column_falls_through_to_total(tmp_path):
    import json

    source = tmp_path / "gl.json"
    source.write_text(
        json.dumps(
            [
                {"period": "2026-01", "vendor": "V", "amount": "", "total": 500},
                {"period": "2026-02", "vendor": "V", "amount": 0, "total": 999},
                {"period": "2026-03", "vendor": "V", "amount": "", "total": ""},
            ]
        )
    )
    buckets, rejected = completeness.load_gl(str(source))
    # A plain `or` chain would discard the legitimate zero; testing only for None
    # would refuse to fall through when the first column is blank.
    assert buckets["2026-01"]["V"] == 500.0
    assert buckets["2026-02"]["V"] == 0.0
    assert [item["reason"] for item in rejected] == ["gl_row_amount_unparseable"]


def test_first_populated_ignores_blank_values():
    assert completeness.first_populated(None, "", "  ", "x") == "x"
    assert completeness.first_populated(0, "fallback") == 0
    assert completeness.first_populated(None, "") is None


# --- An empty approved corpus must not build a silently useless retrieval index ---


def test_an_export_with_no_approved_documents_refuses_to_build(tmp_path):
    export = {"batch_id": "b1", "tables": {"document": []}}
    with pytest.raises(ValueError, match="no approved retrieval chunks"):
        retrieval_store.build_database(export, tmp_path / "a.sqlite")
    result = retrieval_store.build_database(export, tmp_path / "b.sqlite", allow_empty=True)
    assert result == {
        "chunks": 0,
        "crm_records": 0,
        "crm_tables": 28,
        "factual_rows_only": True,
    }


# --- rule 9: a control that processed nothing has not passed ---


def test_a_corpus_with_no_address_is_an_exception_not_a_clean_run(tmp_path, monkeypatch, capsys):
    """18 documents in, 0 addresses out, 0 exceptions, exit 0.

    That is indistinguishable from a corpus whose addresses were all clean, and
    it is what a real commission-statement run reported. entity_resolve.py
    already treats its own empty run as review work; this one reported success
    over no work at all. The address-validation and CASS lanes are wired to this
    control, so a silent zero also means those lanes never ran and nothing said
    so.
    """
    import json

    consensus = tmp_path / "consensus.json"
    consensus.write_text(
        json.dumps(
            {
                "summary": {},
                "documents": [
                    {"document_id": f"d{index}", "header": {}, "lines": []} for index in range(18)
                ],
            }
        )
    )
    out, exceptions = tmp_path / "addresses.json", tmp_path / "address_exceptions.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "address_normalize.py",
            str(consensus),
            "--out",
            str(out),
            "--exceptions",
            str(exceptions),
        ],
    )
    address_normalize.main()

    summary = json.loads(out.read_text())["summary"]
    assert summary["documents"] == 18
    assert summary["addresses_normalized"] == 0
    # The finding names both possibilities rather than choosing one, because the
    # artifact cannot tell them apart and neither can a reader.
    assert summary["client_review_items"] == 1
    finding = summary["findings"][0]
    assert "source layout carries no address or extraction missed them" in finding

    retained = json.loads(exceptions.read_text())["exceptions"]
    assert len(retained) == 1
    assert retained[0]["review_source"] == "address_normalize"
    assert retained[0]["disposition"] == "client_review_required"
    assert finding in capsys.readouterr().out


def test_a_corpus_that_did_normalize_an_address_reports_no_such_finding(tmp_path, monkeypatch):
    """The finding is about an empty run, not about every run."""
    import json

    consensus = tmp_path / "consensus.json"
    consensus.write_text(
        json.dumps(
            {
                "summary": {},
                "documents": [
                    {
                        "document_id": "d1",
                        "header": {
                            "seller_address": {
                                "value": "1600 Pennsylvania Ave NW, Washington, DC 20500",
                                "source": "printed",
                            }
                        },
                        "lines": [],
                    }
                ],
            }
        )
    )
    out, exceptions = tmp_path / "a.json", tmp_path / "e.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "address_normalize.py",
            str(consensus),
            "--out",
            str(out),
            "--exceptions",
            str(exceptions),
            "--quiet",
        ],
    )
    address_normalize.main()
    summary = json.loads(out.read_text())["summary"]
    assert summary["addresses_normalized"] >= 1
    assert summary["findings"] == []
