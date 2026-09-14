"""Products read by each manufacturer's own rule, never a column guessed at.

The CRM built Product Name from `description` and Product Code from
`item_code`, and on this corpus those columns hold a murbrook project, a HALVOR
project client and territory, an Linden World payer number, and -- on pages
whose code column slipped a row against the amounts -- another line's product.
"""

import csv
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

lane = importlib.import_module("augment_export")


def party(key, name, variants=()):
    """A party as the master writes it."""
    return {
        "party_key": key,
        "canonical_name": name,
        "normalized_name": lane.normalize_name(name),
        "name_variants": [name, *variants],
    }


PARTIES = [
    party("PTY-AW", "Linden World"),
    party("PTY-MB", "Marlow / Bramwell", ["Marlow Bramwell"]),
    party("PTY-MF", "murbrook"),
    party("PTY-JC", "KD Frost", ["D FROST COMPANY"]),
]
LINDEN = {
    "brand": "Linden World",
    "code": "[A-Z][A-Z0-9][0-9]{4,5}",
    "code_letters": 2,
    "code_from": ["product_sku", "item_code", "description"],
    "code_may_follow_a_name": True,
    "check_the_printed_row": True,
    "name_from": ["description", "item_code"],
    "evidence": "pages 238 and 262",
}
MARTIN = {
    "brand": "Marlow / Bramwell",
    "code": "[A-Za-z][0-9]{6}|[0-9]{3,4}|[A-Za-z][A-Za-z .&/'-]*[A-Za-z0-9]",
    "code_from": ["item_code", "description"],
    "name_is_the_code": True,
    "variant_from": ["product_sku"],
    "not_products": ["FABRIC", "XSH"],
    "evidence": "pages 369 and 488",
}
MURBROOK = {"brand": "murbrook", "no_product": "its statements list jobs", "evidence": "page 70"}
NOBODY = {"brand": "Nobody Makes This", "no_product": "none", "evidence": "none"}


def test_product_rules_must_rest_on_an_authorization_and_evidence(tmp_path):
    path = tmp_path / "rules.json"

    def refused(data, message):
        path.write_text(json.dumps(data))
        with pytest.raises(ValueError, match=message):
            lane.load_product_rules(path)

    refused([], "authorization")
    refused({"rules": [LINDEN]}, "authorization")
    refused({"authorization": "a", "rules": []}, "non-empty list")
    refused({"authorization": "a", "rules": "x"}, "non-empty list")
    refused({"authorization": "a", "rules": ["x"]}, "must name a brand")
    refused({"authorization": "a", "rules": [{"brand": "X"}]}, "evidence")
    refused({"authorization": "a", "rules": [{"brand": "X", "evidence": "p1"}]}, "no_product")
    path.write_text(json.dumps({"authorization": "a", "rules": [LINDEN, MURBROOK]}))
    assert [rule["brand"] for rule in lane.load_product_rules(path)] == ["Linden World", "murbrook"]


def test_a_code_comes_from_the_first_column_printing_one_and_never_a_label_or_a_party():
    names = lane.party_names(PARTIES)
    # The payer fused into the code's cell: its last word is the code.
    fused = {"item_code": "Workfield BQ1336"}
    assert lane.product_code_in(fused, LINDEN, names) == ("BQ1336", "item_code", None)
    both = {"product_sku": "SO1610", "item_code": "ME5890"}
    assert lane.product_code_in(both, LINDEN, names) == ("SO1610", "product_sku", None)
    # A substyle under item_code gives way to the style beside it.
    style = {"item_code": "XSH", "description": "MEDINAH"}
    assert lane.product_code_in(style, MARTIN, names) == ("MEDINAH", "description", None)
    # Every candidate refused: the first refusal is what is said.
    held = {"item_code": "D FROST COMPANY", "description": "FABRIC"}
    assert lane.product_code_in(held, MARTIN, names) == (
        "",
        "",
        ("D FROST COMPANY", "names a party, not a product", "lines_naming_a_party_not_a_product"),
    )
    assert lane.product_code_in({"item_code": "fabric"}, MARTIN, names)[2][1] == "is not a product"
    assert lane.product_code_in({"description": "April 2024-Comm"}, MARTIN, names) == ("", "", None)
    # Without its own columns a rule reads the default ones.
    assert lane.product_code_in({"item_code": "7100"}, {"code": "[0-9]{4}"}, {}) == (
        "7100",
        "item_code",
        None,
    )


def test_a_products_name_is_never_a_party_the_code_or_another_code():
    names = lane.party_names(PARTIES)
    beside = {"description": "ME6551", "item_code": "Lounge Chair"}
    assert lane.product_name_in(beside, LINDEN, "ME1754", names) == "Lounge Chair"
    assert lane.product_name_in({"description": "Workfield BQ1336"}, LINDEN, "BQ1336", names) == ""
    assert (
        lane.product_name_in(
            {"description": "KD Frost", "item_code": "12"}, LINDEN, "BQ1336", names
        )
        == ""
    )
    assert lane.product_name_in({}, MARTIN, "MEDINAH", names) == "MEDINAH"
    assert (
        lane.product_name_in({"description": "Chair"}, {"code": "[0-9]{4}"}, "7100", {}) == "Chair"
    )
    # The key reads a code's letter positions as letters; the reading is kept.
    assert lane.product_code_key("s01610", LINDEN) == "SO1610"
    assert lane.product_code_key("S11300", LINDEN) == "SI1300"
    assert lane.product_code_key("medinah", MARTIN) == "MEDINAH"


def test_a_line_belongs_to_the_ruled_maker_its_document_or_its_own_brand_names():
    names = lane.party_names(PARTIES)
    ruled = {"PTY-AW": LINDEN, "PTY-MB": MARTIN}
    assert lane.line_brand_key({}, {"brand_name__party_key": "PTY-AW"}, names, ruled) == "PTY-AW"
    # A brand the export left unkeyed is matched by its letters.
    assert lane.line_brand_key({}, {"brand_name": "Marlow/Bramwell"}, names, ruled) == "PTY-MB"
    # A document whose brand holds a dealer gives way to its line's maker.
    dealer = {"brand_name__party_key": "PTY-JC"}
    assert lane.line_brand_key({"brand_name": "Linden World"}, dealer, names, ruled) == "PTY-AW"
    inferred = {"brand_name__inferred": "marlow bramwell"}
    assert lane.line_brand_key(inferred, {}, names, ruled) == "PTY-MB"
    # No ruled maker named: the first party named, to be counted as unruled.
    assert lane.line_brand_key({}, dealer, names, ruled) == "PTY-JC"
    assert lane.line_brand_key({}, {}, names, ruled) == ""


def printed_page(*rows):
    """Positioned words, one printed row per tuple, each row its own band."""
    found = []
    for number, words in enumerate(rows):
        top = 0.1 + number * 0.02
        found.extend((word, top, top + 0.01) for word in words)
    return found


PAGE = printed_page(
    ("S01610", "ARMCHAIR", "1,853.00"),
    ("ME5890", "Table", "968.40"),
    ("(BM4726", "391.95"),
    ("BQ1309", "2,346.00"),
    ("SO1860", "2,346.00"),
    ("BU0597", "10.00", "20.00"),
    ("ME1747", "ME6551", "55.00"),
)


def corpus():
    """Lines and documents covering every way a line gets, or is refused, a product."""
    documents = [
        {
            "document_id": "run__aw",
            "brand_name__party_key": "PTY-AW",
            "invoice_date__iso": "2025-04-25",
        },
        {"document_id": "run__mb", "brand_name": "Marlow Bramwell"},
        {"document_id": "run__mf", "brand_name__party_key": "PTY-MF"},
        {"document_id": "run__jc", "brand_name__party_key": "PTY-JC"},
        {"document_id": "run__sum"},
        {"document_id": "run__t"},
    ]
    aw = [
        # The printed row agrees, read another way.
        {"product_sku": "SO1610", "description": "ARMCHAIR", "commissionable_amount": "1,853.00"},
        # The code column slipped a row: the amount's row prints another code.
        {"product_sku": "ME4753", "description": "TABLE ME4", "commissionable_amount": "968.40"},
        # The engines read no code; the row of the amount prints one.
        {"item_code": "TR3517.00", "description": "BASE TABLI", "commissionable_amount": "391.95"},
        # An amount the page prints twice finds no row.
        {"product_sku": "BQ1309", "commissionable_amount": "2346.00"},
        {"description": "ARMCHAIR", "commissionable_amount": "777.00"},
        {"product_sku": "SO1610", "duplicate_of_line": "0"},
        # One code two lines claim is neither's.
        {"commission_amount": "10.00"},
        {"commission_amount": "20.00"},
        # A row printing two codes is left unread.
        {"product_sku": "ME1747", "extended_amount": "55.00"},
    ]
    lines = [{"document_id": "run__aw", **row} for row in aw]
    lines += [
        {
            "document_id": "run__mb",
            "item_code": "XSH",
            "description": "MEDINAH",
            "product_sku": "36CD",
        },
        {"document_id": "run__mb", "item_code": "D FROST COMPANY"},
        {"document_id": "run__mb", "description": "FABRIC"},
        {"document_id": "run__mb", "description": "Medinah"},
        {"document_id": "run__mf", "description": "FL Sky Global"},
        {"document_id": "run__jc", "description": "anything"},
        {"document_id": "run__sum", "brand_name": "Linden World", "product_sku": "SO1610"},
        {"document_id": "run__sum", "brand_name": "Marlow Bramwell", "description": "MEDINAH"},
        {"document_id": "run__t", "brand_name": "Linden World", "product_sku": "SO1545"},
    ]
    return lines, documents


def test_every_line_gets_its_product_by_its_makers_rule_or_says_why_not():
    lines, documents = corpus()
    rules = [LINDEN, MARTIN, MURBROOK, NOBODY]
    tally, unmatched = lane.assign_products(
        lines, documents, rules, PARTIES, lambda doc: PAGE if doc == "run__aw" else []
    )
    assert unmatched == ["Nobody Makes This"]
    assert dict(tally) == {
        "lines_with_a_product": 8,
        "codes_the_printed_row_agrees_with": 1,
        "codes_taken_from_the_printed_row_over_the_engines": 1,
        "codes_read_only_from_the_printed_row": 1,
        "lines_printing_no_product_code": 3,
        "lines_that_are_not_a_line": 1,
        "lines_naming_a_party_not_a_product": 1,
        "lines_naming_something_other_than_a_product": 1,
        "lines_of_a_manufacturer_listing_no_products": 1,
        "lines_whose_manufacturer_has_no_product_rule": 1,
        "lines_on_a_page_naming_several_manufacturers": 2,
    }
    agrees, differs, recovered, twice = lines[:4]
    assert (agrees["product__code"], agrees["product__printed_row_code"]) == ("SO1610", "S01610")
    assert agrees["product__printed_row_check"] == "agrees"
    assert agrees["product__rule"] == "Linden World: code from product_sku; the printed row agrees"
    assert (agrees["product__name"], agrees["product__key"]) == ("ARMCHAIR", "PTY-AW:SO1610")
    # The engines' reading stays in its column; the product is the printed row's.
    assert (differs["product_sku"], differs["product__code"]) == ("ME4753", "ME5890")
    assert differs["product__printed_row_check"] == "differs" and differs["product__name"] == ""
    assert differs["product__rule"].endswith("the engines read ME4753")
    assert (recovered["product__code"], recovered["product__name"]) == ("BM4726", "BASE TABLI")
    assert recovered["product__printed_row_check"] == "recovered"
    assert (twice["product__code"], twice["product__printed_row_check"]) == ("BQ1309", "")
    assert [row["product__printed_row_code"] for row in lines[6:9]] == ["", "", ""]
    style, held, fabric, again = lines[9:13]
    assert (style["product__code"], style["product__variant"]) == ("MEDINAH", "36CD")
    assert (
        held["product__rule"] == "Marlow / Bramwell: D FROST COMPANY names a party, not a product"
    )
    assert fabric["product__rule"] == "Marlow / Bramwell: FABRIC is not a product"
    assert again["product__key"] == "PTY-MB:MEDINAH"
    assert lines[13]["product__rule"] == "no product: its statements list jobs"
    assert (lines[14]["product__brand"], lines[14]["product__rule"]) == ("", "")
    assert lines[15]["product__rule"].startswith("a page naming several manufacturers")
    assert lines[17]["product__code"] == "SO1545"
    products = lane.product_rows(lines, documents)
    assert [(p["brand"], p["code"]) for p in products] == [
        ("Linden World", "BM4726"),
        ("Linden World", "BQ1309"),
        ("Linden World", "ME1747"),
        ("Linden World", "ME5890"),
        ("Linden World", "SO1545"),
        ("Linden World", "SO1610"),
        ("Marlow / Bramwell", "MEDINAH"),
    ]
    medinah = products[-1]
    assert (medinah["other_codes"], medinah["variants"], medinah["line_rows"]) == (
        "Medinah",
        "36CD",
        2,
    )
    assert (products[3]["name"], products[3]["first_document_date"]) == ("", "2025-04-25")
    assert (products[4]["first_document_date"], products[4]["last_document_date"]) == ("", "")


def test_without_the_retained_page_no_code_is_checked_against_its_row():
    lines, documents = corpus()
    tally, _ = lane.assign_products(lines, documents, [LINDEN, MARTIN], PARTIES)
    assert "codes_read_only_from_the_printed_row" not in tally
    assert (lines[1]["product__code"], lines[1]["product__printed_row_check"]) == ("ME4753", "")


def respond(directory, document_id, text, rows):
    """A retained Document AI response printing `rows`, one positioned word each."""
    tokens, cursor = [], 0
    for top, words in rows:
        for word in words:
            start = text.index(word, cursor)
            cursor = start + len(word)
            corners = [{"y": top}, {"y": top + 0.01}]
            anchor = {"textSegments": [{"startIndex": str(start), "endIndex": str(cursor)}]}
            tokens.append(
                {"layout": {"textAnchor": anchor, "boundingPoly": {"normalizedVertices": corners}}}
            )
    payload = {"response": {"document": {"text": text, "pages": [{"tokens": tokens}]}}}
    (directory / f"000001_{document_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_the_retained_pages_are_read_by_document(tmp_path):
    with pytest.raises(ValueError, match="No retained extractor responses"):
        lane.page_tokens(tmp_path)
    respond(tmp_path, "run__aw", "S01610 1,853.00", [(0.1, ("S01610", "1,853.00"))])
    tokens_for = lane.page_tokens(tmp_path)
    assert tokens_for("run__aw") == [("S01610", 0.1, 0.11), ("1,853.00", 0.1, 0.11)]
    assert tokens_for("run__elsewhere") == []


def write_csv(path, rows):
    """Write rows with the union of their columns."""
    columns = list(dict.fromkeys(name for row in rows for name in row))
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_main_writes_products_only_from_rules_resting_on_a_party_master(tmp_path, capsys):
    lines_in, documents_in = tmp_path / "lines.csv", tmp_path / "documents.csv"
    write_csv(
        lines_in,
        [{"document_id": "run__aw", "product_sku": "SO1610", "commissionable_amount": "1,853.00"}],
    )
    write_csv(documents_in, [{"document_id": "run__aw", "brand_name__party_key": "PTY-AW"}])
    master = tmp_path / "parties.json"
    master.write_text(json.dumps({"parties": PARTIES}))
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"authorization": "amendment", "rules": [LINDEN, NOBODY]}))
    raw = tmp_path / "raw"
    raw.mkdir()
    respond(raw, "run__aw", "S01610 1,853.00", [(0.1, ("S01610", "1,853.00"))])
    base = ["--lines-csv", str(lines_in), "--documents-csv", str(documents_in)]

    def outputs(name):
        return [
            "--out-lines",
            str(tmp_path / f"{name}_lines.csv"),
            "--out-documents",
            str(tmp_path / f"{name}_documents.csv"),
            "--out",
            str(tmp_path / f"{name}.json"),
        ]

    for asked, message in (
        (["--product-rules", str(rules)], "need --parties"),
        (
            ["--parties", str(master), "--out-products", str(tmp_path / "p.csv")],
            "need --product-rules",
        ),
        (["--parties", str(master), "--extractor-raw", str(raw)], "need --product-rules"),
    ):
        with pytest.raises(SystemExit) as exc:
            lane.main([*base, *outputs("a"), *asked])
        assert message in str(exc.value)
    products = tmp_path / "b_products.csv"
    full = ["--parties", str(master), "--product-rules", str(rules), "--extractor-raw", str(raw)]
    assert lane.main([*base, *outputs("b"), *full, "--out-products", str(products)]) == 0
    printed = capsys.readouterr().out
    assert "products: 1" in printed and "  codes the printed row agrees with: 1" in printed
    assert "product rules naming no party: ['Nobody Makes This']" in printed
    written = list(csv.DictReader(open(products, encoding="utf-8")))
    assert [(row["product_key"], row["other_codes"]) for row in written] == [("PTY-AW:SO1610", "")]
    line = next(csv.DictReader(open(tmp_path / "b_lines.csv", encoding="utf-8")))
    assert (line["product__code"], line["product__printed_row_check"]) == ("SO1610", "agrees")
    summary = json.loads((tmp_path / "b.json").read_text())
    assert summary["inputs"]["extractor_raw"] == str(raw)
    assert summary["products"]["rules_naming_no_party"] == ["Nobody Makes This"]
    # Rules without a products file still put each line's product beside it.
    only = tmp_path / "only_rules.json"
    only.write_text(json.dumps({"authorization": "amendment", "rules": [LINDEN]}))
    assert (
        lane.main([*base, *outputs("c"), "--parties", str(master), "--product-rules", str(only)])
        == 0
    )
    printed = capsys.readouterr().out
    assert "products: 1" in printed and "naming no party" not in printed
    assert not (tmp_path / "c_products.csv").exists()
    # A rule file resting on no authorization fails the run whole.
    bad = tmp_path / "bad_rules.json"
    bad.write_text(json.dumps({"rules": [LINDEN]}))
    with pytest.raises(SystemExit) as exc:
        lane.main([*base, *outputs("d"), "--parties", str(master), "--product-rules", str(bad)])
    assert "Augmentation failed" in str(exc.value)
    assert not (tmp_path / "d_lines.csv").exists()
