"""A record's views follow its fields, and keep what only a view holds."""

import consensus


def entry(value, confidence=None):
    return {"value": value, "confidence": confidence, "source": "printed"}


def test_a_field_replaces_its_view_cell_and_what_only_a_view_holds_is_kept():
    """An agreed value reaches the view; a mapping or a flag written there stays."""
    document = {
        "fields": {
            "header.total_amount": {"value": "10.00", "confidence": 0.9},
            "lines[1].job_number": {"value": "12951", "source": "printed"},
            "document_type": {"value": "commission_statement"},
            "header.broken": "not a field",
        },
        "header": {
            "total_amount": entry("9.00"),
            "reference_number": {"value": "R-1", "mapped_from_source_label": "REF"},
        },
        "lines": [{"flag": "yes"}],
    }
    consensus.refresh_views(document)
    assert document["header"]["total_amount"] == entry("10.00", 0.9)
    assert document["header"]["reference_number"]["mapped_from_source_label"] == "REF"
    assert document["lines"] == [{"flag": "yes"}, {"job_number": entry("12951")}]
    # A field that is not a view slot makes no view.
    assert "document_type" not in document


def test_a_retired_field_takes_its_view_entry_with_it():
    """A re-file removes the field it moved; its old view entry goes with it."""
    document = {
        "fields": {"lines[0].specifier_name": {"value": "X"}, "lines[1].amount": {"value": "1"}},
        "lines": [{"dealer_name": entry("X"), "flag": "yes"}, "not a row"],
    }
    consensus.refresh_views(document, retired=["lines[0].dealer_name", "not a path"])
    assert document["lines"] == [
        {"flag": "yes", "specifier_name": entry("X")},
        {"amount": entry("1")},
    ]


def test_views_are_made_only_where_there_is_something_to_hold():
    document = {"fields": {"lines[0].amount": {"value": "1"}}}
    consensus.refresh_views(document)
    assert "header" not in document and "accessorials" not in document
    assert document["lines"] == [{"amount": entry("1")}]
    bare = {"header": None, "lines": "not a list"}
    consensus.refresh_views(bare)
    assert bare == {"header": None, "lines": []}


def test_where_views_and_fields_disagree_is_named_both_ways():
    document = {
        "fields": {
            "header.total_amount": {"value": "10.00"},
            "lines[0].job_number": {"value": "12951"},
            "lines[0].amount": {"value": None},
            "note": {"value": "x"},
            "header.broken": "not a field",
        },
        "header": {
            "total_amount": {"value": "9.00"},
            "reference_number": {"value": "R-1"},
            "empty": {"value": ""},
        },
        "lines": [{"job_number": {"value": "12951"}, "amount": {"value": "5"}}, "not a row"],
        "accessorials": [{"fee": "3"}],
    }
    assert consensus.views_out_of_step(document) == {
        "behind": ["header.total_amount"],
        "view_only": ["accessorials[0].fee", "header.reference_number", "lines[0].amount"],
    }
    assert consensus.view_slot("header") is None
    assert consensus.view_slot("lines.amount") is None
    assert consensus.view_slot("header[2].x") is None
    assert consensus.view_slot("accessorials[0].fee") == ("accessorials", 0, "fee")
    assert consensus.views_out_of_step({}) == {"behind": [], "view_only": []}
