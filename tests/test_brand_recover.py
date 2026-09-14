"""Recovering the manufacturer from the letterhead the page already printed.

354 of 683 documents carried no manufacturer -- $5.42M of commission with no
brand against it -- and the reason is instructive: `brand_name` held the client,
because a billing report prints the client as its agent of record. Refusing the
client, which is right, left those documents with nothing.
"""

import collections
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

lane = importlib.import_module("brand_recover")


def page(text):
    return {"response": {"document": {"text": text}}}


def records():
    return [
        # Pages that name their manufacturer plainly teach the vocabulary.
        {"document_id": "a", "header": {"brand_name": "Murbrook", "dealer_name": "KD Frost"}},
        {"document_id": "b", "header": {"brand_name": "murb rook", "dealer_name": "Empall"}},
        {"document_id": "c", "header": {"brand_name": "HALVOR"}},
        # One page mis-files a dealer into the brand. It must not become a
        # manufacturer, and it must not disqualify one either.
        {"document_id": "d", "header": {"brand_name": "KD Frost"}},
        # The page needing a brand.
        {"document_id": "e", "header": {"dealer_name": "Empall"}, "fields": {}},
    ]


# The fixture's makers each head two retained pages, as a vocabulary name must:
# a dealer's name reaches the top of one page by accident of layout, not two.
LETTERHEADS = {
    "a": "Murbrook\nCommission Statement",
    "b": "murb rook\nCommission Statement",
    "c": "HALVOR furniture\nOpen Order Status",
    "d": "KD Frost\nDealer copy",
}


def write_pages(directory, **texts):
    """Retained responses for the fixture's pages and any others given, indexed."""
    directory.mkdir(exist_ok=True)
    for index, (doc_id, text) in enumerate({**LETTERHEADS, **texts}.items()):
        (directory / f"{index:06d}_{doc_id}.json").write_text(json.dumps(page(text)))
    return lane.responses_by_document(directory)


def reasons(exceptions, doc_id):
    """The reasons retained for one document."""
    return [entry["reason"] for entry in exceptions if entry["document_id"] == doc_id]


def test_the_vocabulary_is_the_corpus_own_and_excludes_what_it_calls_something_else():
    """A name the pages call a brand more often than anything else is a brand."""
    vocabulary = lane.brand_vocabulary(records(), ["Northgate Co"])
    assert set(vocabulary) == {"murbrook", "murb rook", "halvor"}
    # `KD Frost` is named as a dealer twice and as a brand once.
    assert "kd frost" not in vocabulary


def test_the_client_is_never_a_manufacturer():
    """A billing report prints the client as its agent of record."""
    with_client = [
        *records(),
        {"document_id": "f", "header": {"brand_name": "NORTHGATE C"}},
        {"document_id": "g", "header": {"brand_name": "Northgate Co, LLC"}},
    ]
    assert not {
        name for name in lane.brand_vocabulary(with_client, ["Northgate Co"]) if "northgate" in name
    }
    # Undeclared, it would be learned like any other name -- which is the fault.
    assert "northgate c" in lane.brand_vocabulary(with_client)


def test_a_territory_code_is_not_a_manufacturer():
    """`3-USA` is the market column, and taking it would attribute a page to a region."""
    assert lane.NOT_A_BRAND.match("3-USA")
    assert lane.NOT_A_BRAND.match("150-NGC")
    assert not lane.NOT_A_BRAND.match("Murbrook")


def test_one_manufacturer_spelled_two_ways_is_one_answer():
    """Before these were grouped, 100 pages recovered nothing.

    `murb rook` and `murbrook` squash to one key, and `bramwell` is the shorter
    form of `marlow bramwell`. Each is one manufacturer, not several.
    """
    vocabulary = {"murb rook": 3, "murbrook": 9, "bramwell": 2, "marlow bramwell": 7}
    # The spelling the corpus prints most often wins, as elsewhere in the repo.
    assert lane.brands_in(lane._squash("MURBROOK invoice"), vocabulary) == ["murbrook"]
    assert lane.brands_in(lane._squash("Marlow / Bramwell Inc."), vocabulary) == ["marlow bramwell"]
    # Two genuinely different manufacturers are still two answers. Only the
    # spellings the page actually carries are candidates: this letterhead says
    # `Bramwell` without `Martin`, so the longer form is not in the running.
    assert lane.brands_in(lane._squash("Murbrook and Bramwell"), vocabulary) == [
        "bramwell",
        "murbrook",
    ]


def test_a_recovered_brand_is_retained_unaccepted(tmp_path):
    """One extractor read it and no model read it at all."""
    responses = write_pages(tmp_path / "raw", e="HALVOR furniture\nOpen Order Status")
    out, exceptions, attached, _ = lane.recover(records(), responses, ["Northgate Co"])
    assert attached == 1 and reasons(exceptions, "e") == []
    recovered = next(r for r in out if r["document_id"] == "e")
    field = recovered["header"]["brand_name"]
    assert field["value"] == "halvor"
    assert field["accepted"] is False
    assert field["consensus_flag"] == "single_engine"
    assert field["rule"] == lane.RECOVERED_EVIDENCE
    assert field["recovery_method"] == "letterhead"
    # The export pivots the flat field map, which scopes a header field as
    # `header.brand_name`. A bare `brand_name` key is one nothing reads.
    assert recovered["fields"]["header.brand_name"]["value"] == "halvor"
    assert "brand_name" not in recovered["fields"]
    # A document that already states a brand is untouched.
    assert out[0]["header"]["brand_name"] == "Murbrook"
    assert field["displaced_reading"] == ""


def test_a_brand_field_holding_the_client_counts_as_no_brand(tmp_path):
    """The case this lane exists for. Skipping those left 160 unrecovered.

    A billing report prints the client as its agent of record and the nearest
    company field takes it, so `brand_name` populated is not the same question
    as a manufacturer named.
    """
    responses = write_pages(tmp_path / "raw", f="HALVOR furniture")
    with_client = [
        *records(),
        {"document_id": "f", "header": {"brand_name": "NORTHGATE C"}, "fields": {}},
    ]
    out, _, attached, _ = lane.recover(with_client, responses, ["Northgate Co"])
    assert attached == 1
    recovered = next(r for r in out if r["document_id"] == "f")
    assert recovered["header"]["brand_name"]["value"] == "halvor"
    # The displaced reading travels with the amendment rather than being erased.
    assert recovered["header"]["brand_name"]["displaced_reading"] == "NORTHGATE C"
    # A territory code is no better an answer than the client.
    assert lane.states_a_manufacturer({"header": {"brand_name": "3-USA"}}, []) is False
    assert lane.states_a_manufacturer({"header": {"brand_name": "HALVOR"}}, []) is True
    assert (
        lane.states_a_manufacturer({"header": {"brand_name": "NORTHGATE C"}}, ["Northgate Co"])
        is False
    )


def test_a_dealer_in_the_brand_column_gives_way_to_the_letterhead(tmp_path):
    """`EMPALL OFFICE, INC.` stated as a brand has not named the manufacturer."""
    responses = write_pages(tmp_path / "raw", d="HALVOR furniture\nDealer copy", e="HALVOR")
    out, exceptions, attached, _ = lane.recover(records(), responses, ["Northgate Co"])
    assert attached == 2 and exceptions == []
    field = out[3]["header"]["brand_name"]
    assert (field["value"], field["displaced_reading"]) == ("halvor", "KD Frost")


@pytest.mark.parametrize(
    "text, reason",
    [
        ("Nothing recognisable here", "no_known_brand_in_the_letterhead"),
        ("HALVOR and Murbrook both", "letterhead_names_several_brands"),
    ],
)
def test_a_letterhead_that_cannot_be_settled_yields_an_exception(tmp_path, text, reason):
    """A page that names several, or none, is a question rather than a guess."""
    responses = write_pages(tmp_path / "raw", e=text)
    _, exceptions, attached, _ = lane.recover(records(), responses, ["Northgate Co"])
    assert attached == 0
    assert reasons(exceptions, "e") == [reason]


def test_the_only_name_at_the_top_is_the_pages_own():
    """A letterhead naming two named its maker at the top and a dealer lower down."""
    top, lower = (0, "halvor", True), (300, "murbrook", True)
    assert lane.settle([top]) == top
    assert lane.settle([top, lower]) == top
    assert lane.settle([top, (40, "murbrook", True)]) is None
    assert lane.settle([lower, (400, "tallis", True)]) is None


def test_a_page_with_no_retained_layout_is_an_exception(tmp_path):
    """A handwritten letterhead is not in a printed text layer either.

    A dealer in the brand column names no manufacturer, so `d` is a question
    too rather than a document already branded.
    """
    _, exceptions, attached, _ = lane.recover(records(), {}, ["Northgate Co"])
    assert attached == 0
    assert exceptions == [
        {"document_id": "d", "reason": "no_retained_extractor_layout"},
        {"document_id": "e", "reason": "no_retained_extractor_layout"},
    ]
    bad = tmp_path / "000005_e.json"
    bad.write_text("{not json")
    _, exceptions, _, _ = lane.recover(
        records(), lane.responses_by_document(tmp_path), ["Northgate Co"]
    )
    assert reasons(exceptions, "e") == ["extractor_layout_unreadable"]


def test_a_page_the_extractor_read_nothing_on_is_an_exception(tmp_path):
    responses = write_pages(tmp_path / "raw", e="   ")
    _, exceptions, _, _ = lane.recover(records(), responses, ["Northgate Co"])
    assert reasons(exceptions, "e") == ["no_text_in_the_retained_response"]


def test_a_page_read_only_in_its_retry_is_read(tmp_path):
    """222 pages of the commission run had text only in the extractor's retry."""
    raw = tmp_path / "raw"
    write_pages(raw)
    (raw / "000009_e.json").write_text(json.dumps(page("")))
    (raw / "000010_e__retry1.json").write_text(json.dumps(page("HALVOR furniture")))
    out, exceptions, attached, _ = lane.recover(
        records(), lane.responses_by_document(raw), ["Northgate Co"]
    )
    assert attached == 1 and reasons(exceptions, "e") == []
    assert out[4]["header"]["brand_name"]["value"] == "halvor"


def write_raw(directory, texts):
    """Retained responses for the given pages, indexed."""
    directory.mkdir()
    for index, (doc_id, text) in enumerate(texts.items()):
        (directory / f"{index:06d}_{doc_id}.json").write_text(json.dumps(page(text)))
    return lane.responses_by_document(directory)


def test_a_long_name_read_one_letter_off_is_that_name(tmp_path):
    """A display face is where the extractor misreads a letter: `Lindew World`."""
    responses = write_raw(
        tmp_path / "raw",
        {
            "p1": "Linden World\nBilling report",
            "p2": "Linden World\nBilling report",
            "p3": "Lindew World\nBilling report",
        },
    )
    stated = [{"document_id": f"p{n}", "header": {"brand_name": "Linden World"}} for n in (1, 2)]
    out, exceptions, attached, _ = lane.recover(
        [*stated, {"document_id": "p3", "header": {}}], responses
    )
    assert attached == 1 and exceptions == []
    field = out[2]["header"]["brand_name"]
    assert (field["value"], field["recovery_method"]) == (
        "linden world",
        "letterhead_one_letter_off",
    )
    assert field["evidence_text"].endswith("read one letter off")
    # A short name read one letter off is another word, and is not matched; a
    # long one printed nowhere, even one letter off, is not there.
    assert lane.letterhead_brands("halvar", {"halvor": 1}) == []
    assert lane.letterhead_brands("halvorfurniture", {"linden world": 1}) == []
    # A maker's name too short once its digits and marks are gone stays in the
    # opening, where taking it out would take out ordinary letters.
    assert lane.opening("3M Co Statement", {"3m co": 1}) == "mcostatement"


def test_a_page_opening_as_its_makers_pages_do_takes_that_maker(tmp_path):
    """HALVOR's monthly statement prints its logo as an image on some pages."""
    heading = "MONTHLY COMMISSION STATEMENT\n{}Month ended\nNorthgate Co."
    responses = write_raw(
        tmp_path / "raw",
        {
            "h1": heading.format("HALVOR\n"),
            "h2": heading.format("HALVOR\n"),
            "h3": heading.format(""),
        },
    )
    rows = [
        {"document_id": "h1", "header": {"brand_name": "HALVOR"}},
        {"document_id": "h2", "header": {"brand_name": "HALVOR"}},
        {"document_id": "h3", "header": {}},
    ]
    out, exceptions, attached, _ = lane.recover(rows, responses, ["Northgate Co"])
    assert attached == 1 and exceptions == []
    field = out[2]["header"]["brand_name"]
    assert (field["value"], field["recovery_method"]) == ("halvor", "opening")
    assert field["evidence_text"] == "the page opens as 2 pages of halvor do"


def test_a_letterhead_naming_a_company_never_called_a_brand_stays_a_question(tmp_path):
    """Crestline Design's funds-transfer advices read like Marlow/Bramwell's bill payments."""
    skyline = "Crestline Design Inc.\n1240 N Larkin Ave\nPaid To: Northgate Co."
    responses = write_pages(tmp_path / "raw", e=skyline)
    _, exceptions, attached, _ = lane.recover(records(), responses, ["Northgate Co"])
    assert attached == 0
    assert [entry for entry in exceptions if entry["document_id"] == "e"] == [
        {
            "document_id": "e",
            "reason": "letterhead_names_a_company_never_called_a_brand",
            "candidates": ["Crestline Design Inc."],
        }
    ]
    # The client and a known maker are not unknown companies, and a heading
    # reading `COMPANY` names none.
    top = "Northgate Co.\nHALVOR Inc.\nInvoice\nCOMPANY"
    assert lane.companies_named_at_top(top, {"halvor": 1}, ["Northgate Co"]) == []


def recover_among_makers(directory, target_text, shared=""):
    """Six settled pages each of three makers, and one page naming none."""
    pages = {}
    for maker, words in (
        ("HALVOR", "alpha bravo charlie delta"),
        ("Murbrook", "echo foxtrot golf hotel"),
        ("Tallis", "india juliet kilo lima"),
    ):
        pages.update({f"{maker[:3].lower()}{n}": (maker, f"{maker}\n{words}") for n in range(6)})
    if shared:
        # One Murbrook page prints HALVOR's words too, so they are no one maker's.
        pages["mur0"] = ("Murbrook", f"{pages['mur0'][1]}\n{shared}")
    texts = {doc_id: text for doc_id, (_, text) in pages.items()}
    responses = write_raw(directory, {**texts, "target": target_text})
    rows = [
        {"document_id": doc_id, "header": {"brand_name": maker}}
        for doc_id, (maker, _) in pages.items()
    ]
    out, exceptions, attached, _ = lane.recover(
        [*rows, {"document_id": "target", "header": {}}], responses
    )
    return out[-1]["header"].get("brand_name"), exceptions, attached


def test_a_continuation_page_takes_the_maker_only_whose_pages_print_its_words(tmp_path):
    """Linden World's SAP condition types ZCO1 and ZCO2 are on no other maker's pages."""
    field, exceptions, attached = recover_among_makers(
        tmp_path / "a", "alpha bravo charlie\nquebec"
    )
    assert attached == 1 and exceptions == []
    assert (field["value"], field["recovery_method"]) == ("halvor", "layout_signature")
    assert field["evidence_text"] == (
        "the page prints 3 words only halvor's pages print: alpha, bravo, charlie"
    )


def test_a_continuation_page_takes_the_maker_its_most_alike_pages_all_name(tmp_path):
    """Words two makers print sign for neither, but five alike pages still agree."""
    # `quebec` opens the page, so it does not open as HALVOR's pages do.
    field, _, attached = recover_among_makers(
        tmp_path / "a", "quebec\nalpha bravo charlie delta", shared="alpha bravo charlie delta"
    )
    assert attached == 1
    assert (field["value"], field["recovery_method"]) == ("halvor", "layout_likeness")
    assert field["evidence_text"] == (
        "the page's layout words are most like 5 pages of halvor "
        "(hal5, hal4, hal3, hal2, hal1), sharing 80% with the closest"
    )
    # A page sharing no layout word with any settled page stays a question.
    field, exceptions, attached = recover_among_makers(tmp_path / "b", "quebec romeo sierra")
    assert (field, attached) == (None, 0)
    assert reasons(exceptions, "target") == ["no_known_brand_in_the_letterhead"]


def test_the_passes_by_layout_decide_nothing_they_cannot_support():
    vocabulary = {"bramwell": 1, "marlow bramwell": 1, "halvor": 1}
    assert lane.maker_key("marlow / bramwell", vocabulary) == "bramwell"
    assert lane.maker_key("tallis", vocabulary) == "tallis"
    signature = {"alpha": "halvor", "bravo": "halvor", "charlie": "halvor", "echo": "murbrook"}
    assert lane.signed_maker({"alpha", "bravo", "charlie"}, signature) == (
        "halvor",
        ["alpha", "bravo", "charlie"],
    )
    # Words of two makers, or too few of one, name nobody.
    assert lane.signed_maker({"alpha", "bravo", "charlie", "echo"}, signature) == (None, [])
    assert lane.signed_maker({"alpha", "bravo"}, signature) == (None, [])
    assert lane.likeness(set(), set()) == 0.0
    settled = {f"p{n}": ("halvor", {"alpha", "bravo"}) for n in range(4)}
    # Fewer settled pages than the neighbours asked for.
    assert lane.nearest_maker({"alpha", "bravo"}, settled)[0] is None
    # Five alike pages naming two makers.
    settled["p4"] = ("murbrook", {"alpha", "bravo"})
    assert lane.nearest_maker({"alpha", "bravo"}, settled)[0] is None
    # A word on one page is a project or an amount, and a word on more than
    # half of them is every maker's.
    texts = {f"p{n}": "total alpha" + (" rare" if n == 0 else "") for n in range(10)}
    texts.update({f"q{n}": "total other" for n in range(10)})
    words = lane.layout_words(texts)
    assert (words["p0"], words["q0"]) == ({"alpha"}, {"other"})


def test_a_header_left_empty_by_disagreement_is_read_from_the_field_map(tmp_path):
    """The export prints the field map's reading -- here the client -- so recovery reads it too."""
    held = {
        "document_id": "e",
        "header": {"brand_name": {"value": None}},
        "fields": {"header.brand_name": {"value": "Northgate Co LLC"}},
    }
    assert lane.document_brand(held) == "Northgate Co LLC"
    flat = {"document_id": "z", "fields": {"brand_name": {"value": "HALVOR"}}}
    assert lane.document_brand(flat) == "HALVOR"
    lined = {"document_id": "z", "fields": {}, "lines": [{"brand_name": "Tallis"}]}
    assert lane.document_brand(lined) == "Tallis"
    responses = write_pages(tmp_path / "raw", e="HALVOR furniture")
    out, _, attached, _ = lane.recover([*records()[:4], held], responses, ["Northgate Co"])
    field = out[4]["header"]["brand_name"]
    assert attached == 1 and (field["value"], field["displaced_reading"]) == (
        "halvor",
        "Northgate Co LLC",
    )
    assert out[4]["fields"]["header.brand_name"]["value"] == "halvor"


def test_records_are_read_in_every_shape_the_run_writes(tmp_path):
    """A run writes a bare list, a documents artifact, or one record."""
    one = {"document_id": "x", "header": {}}
    path = tmp_path / "r.json"
    path.write_text(json.dumps([one]))
    assert lane.records_from(path) == [one]
    path.write_text(json.dumps({"documents": [one]}))
    assert lane.records_from(path) == [one]
    path.write_text(json.dumps(one))
    assert lane.records_from(path) == [one]
    # A dict that is neither an artifact nor a record is refused, whether it
    # carries a `documents` key of the wrong shape or nothing recognisable.
    # A JSON scalar is neither, and is refused rather than half-read.
    for shape in ({"documents": "not a list"}, {"nothing": "useful"}, "a bare string", 7):
        path.write_text(json.dumps(shape))
        with pytest.raises(ValueError):
            lane.records_from(path)
    path.write_text(json.dumps({"nope": 1}))
    with pytest.raises(ValueError):
        lane.records_from(path)


def test_a_brand_named_only_on_a_line_still_counts_as_stated():
    """A form naming its manufacturer per line has not lost it."""
    on_line = {"document_id": "z", "lines": [{"brand_name": "Murbrook"}, "not a dict"]}
    assert lane.document_brand(on_line) == "Murbrook"
    assert lane.document_brand({"document_id": "z", "lines": [{"dealer_name": "x"}]}) == ""
    # The search keeps going past a line that names no brand, and past one that
    # is not an object at all.
    later = {"document_id": "z", "lines": [{"dealer_name": "x"}, {"brand_name": "HALVOR"}]}
    assert lane.document_brand(later) == "HALVOR"
    ragged = {"document_id": "z", "lines": ["not a dict", {"brand_name": "HALVOR"}]}
    assert lane.document_brand(ragged) == "HALVOR"
    assert lane.document_brand({"document_id": "z", "header": "not a dict"}) == ""
    # A field may arrive plain or carrying its provenance.
    assert lane.field_text({"value": " Murbrook "}) == "Murbrook"
    assert lane.field_text(None) == ""


def test_a_record_without_a_header_object_keeps_the_brand_on_itself(tmp_path):
    """A run writes flat records too; the recovered field must reach them."""
    responses = write_raw(tmp_path / "raw", {"a": "HALVOR furniture", "e": "HALVOR furniture"})
    flat = [
        {"document_id": "a", "brand_name": "HALVOR"},
        # Stated, with no page retained: nothing to learn its layout from.
        {"document_id": "b", "brand_name": "HALVOR"},
        {"document_id": "e"},
    ]
    out, exceptions, attached, _ = lane.recover(flat, responses)
    assert attached == 1 and exceptions == []
    assert out[2]["brand_name"]["value"] == "halvor"
    # A document the retained layout never covered is an exception, not a guess.
    _, missing, attached, _ = lane.recover(flat, {})
    assert attached == 0
    assert missing == [{"document_id": "e", "reason": "no_retained_extractor_layout"}]


def test_each_recovery_method_is_counted_off_the_recovered_fields():
    recovered = [
        {"brand_name": {"value": "halvor", "recovery_method": "opening"}},
        {"brand_name": "plain"},
        {"header": {"brand_name": {"value": "tallis", "recovery_method": "opening"}}},
        {"header": {"brand_name": {"value": "stated"}}},
    ]
    assert lane.recovery_methods(recovered) == {"opening": 2}


def test_a_name_too_short_or_with_no_letter_is_not_a_manufacturer():
    """Below five characters a brand collides with ordinary letterhead words."""
    short = [
        {"document_id": "a", "header": {"brand_name": "AB"}},
        {"document_id": "b", "header": {"brand_name": "1234"}},
    ]
    assert lane.brand_vocabulary(short) == {}
    assert lane.nameable("1234") is False
    assert lane.nameable("Murbrook") is True
    # A line that is not an object contributes nothing to the counts either.
    assert lane.role_counts([{"document_id": "a", "lines": ["not a dict"]}]) == (
        collections.Counter(),
        collections.Counter(),
    )


def test_the_command_line_writes_both_artifacts(tmp_path, capsys):
    """The lane reports what it recovered and retains what it could not."""
    raw = tmp_path / "raw"
    write_pages(raw, e="HALVOR furniture")
    source = tmp_path / "records.json"
    source.write_text(json.dumps({"documents": records()}))
    out, exceptions = tmp_path / "out.json", tmp_path / "exceptions.json"
    sys.argv = [
        "brand_recover.py",
        str(source),
        "--extractor-raw",
        str(raw),
        "--out",
        str(out),
        "--exceptions",
        str(exceptions),
        "--client-name",
        "Northgate Co",
    ]
    lane.main()
    written = json.loads(out.read_text())
    assert written["summary"]["brands_recovered"] == 1
    assert written["summary"]["brands_recovered_by"] == {"letterhead": 1}
    assert written["summary"]["artifact_type"] == lane.ARTIFACT_TYPE
    assert json.loads(exceptions.read_text())["exceptions"] == [
        {"document_id": "d", "reason": "no_known_brand_in_the_letterhead"}
    ]
    printed = capsys.readouterr().out
    assert "brands recovered: 1" in printed and "by letterhead" in printed
    # `--quiet` writes the artifacts and says nothing.
    out.unlink()
    exceptions.unlink()
    sys.argv.append("--quiet")
    lane.main()
    assert capsys.readouterr().out == ""
    assert json.loads(out.read_text())["summary"]["brands_recovered"] == 1


def test_the_command_line_reports_a_bad_input_rather_than_crashing(tmp_path):
    source = tmp_path / "records.json"
    source.write_text(json.dumps({"nope": 1}))
    sys.argv = [
        "brand_recover.py",
        str(source),
        "--extractor-raw",
        str(tmp_path),
        "--out",
        str(tmp_path / "o.json"),
        "--exceptions",
        str(tmp_path / "e.json"),
    ]
    with pytest.raises(SystemExit) as exit_info:
        lane.main()
    assert "Brand recovery failed" in str(exit_info.value)


def test_the_exception_summary_names_each_reason(tmp_path, capsys):
    """A run that settles nothing must say why, per reason."""
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "000005_e.json").write_text(json.dumps(page("nothing recognisable")))
    source = tmp_path / "records.json"
    source.write_text(json.dumps(records()))
    sys.argv = [
        "brand_recover.py",
        str(source),
        "--extractor-raw",
        str(raw),
        "--out",
        str(tmp_path / "o.json"),
        "--exceptions",
        str(tmp_path / "e.json"),
        "--client-name",
        "Northgate Co",
    ]
    lane.main()
    assert "no_known_brand_in_the_letterhead" in capsys.readouterr().out
