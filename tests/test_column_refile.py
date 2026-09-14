"""A column filed under the wrong field moves whole, and nothing unsafe moves at all."""

import json

import column_refile as lane
import pytest


def reading(value, **extra):
    return {"value": value, "consensus_flag": "consensus_2of2", "accepted": True, **extra}


def records():
    return {
        "documents": [
            {
                "document_id": "run__p0657",
                "fields": {
                    "header.dealer_name": reading("Lumen Weft"),
                    "lines[0].dealer_name": reading("11346 NORCROSS - Tampa, FL"),
                    "lines[0].amount": reading("16,474"),
                    # Nothing was read here, so nothing moves.
                    "lines[1].dealer_name": reading(None),
                    # Two readings of one cell: review, never a mapping.
                    "lines[2].dealer_name": reading("Workfields"),
                    "lines[2].specifier_name": reading("Norcross"),
                    # An empty target is replaced, and kept.
                    "lines[3].dealer_name": reading("13192 Interior Drafters"),
                    "lines[3].specifier_name": reading(None),
                    # Candidates the engines left unsettled are a reading too:
                    # on the target they refuse the move...
                    "lines[4].dealer_name": reading("Studio MC+G"),
                    "lines[4].specifier_name": reading(None, candidate_values=["IA"]),
                    # ...and on the source they move, as the Lumen cells one
                    # engine alone read had to.
                    "lines[5].dealer_name": reading(
                        None,
                        consensus_flag="single_engine",
                        accepted=False,
                        candidate_values=["11406 HASKINS & HILL - CORAL GABELS, FL (SP)"],
                    ),
                    # Candidates that are themselves empty are no reading.
                    "lines[6].dealer_name": reading(None, candidate_values=[None, ""]),
                },
            },
            {
                "document_id": "run__p0001",
                "fields": {"lines[0].dealer_name": reading("OFDC")},
            },
        ]
    }


RULES = {
    "authorization": "run-authorization-amendment-55",
    "refiles": [
        {
            "pages": ["p0657", "p0999"],
            "from": "dealer_name",
            "to": "specifier_name",
            "evidence": "the page prints a Specifier column and no dealer column",
        }
    ],
}


def test_a_reading_moves_whole_and_says_where_it_came_from():
    data = records()
    authorization, rules = lane.load_rules(RULES)
    moved, refused, absent = lane.refile(data, authorization, rules)
    page, other = data["documents"]
    fields = page["fields"]

    assert [(entry["from"], entry["to"]) for entry in moved] == [
        ("header.dealer_name", "header.specifier_name"),
        ("lines[0].dealer_name", "lines[0].specifier_name"),
        ("lines[3].dealer_name", "lines[3].specifier_name"),
        ("lines[5].dealer_name", "lines[5].specifier_name"),
    ]
    moved_line = fields["lines[0].specifier_name"]
    assert moved_line["value"] == "11346 NORCROSS - Tampa, FL"
    assert moved_line["consensus_flag"] == "consensus_2of2"
    assert moved_line["refiled_from"]["field"] == "lines[0].dealer_name"
    assert moved_line["refiled_from"]["authorization"] == "run-authorization-amendment-55"
    assert "lines[0].dealer_name" not in fields
    assert fields["lines[3].specifier_name"]["refiled_from"]["target_was"] == reading(None)
    unsettled = fields["lines[5].specifier_name"]
    assert unsettled["value"] is None and unsettled["consensus_flag"] == "single_engine"
    assert unsettled["candidate_values"] == ["11406 HASKINS & HILL - CORAL GABELS, FL (SP)"]
    assert "lines[5].dealer_name" not in fields
    assert moved[3]["candidate_values"] == ["11406 HASKINS & HILL - CORAL GABELS, FL (SP)"]
    assert moved[0]["candidate_values"] == []
    assert fields["lines[6].dealer_name"]["candidate_values"] == [None, ""]
    assert "lines[6].specifier_name" not in fields
    # The record's own grains are rebuilt from the fields, so they agree.
    assert page["lines"][0]["specifier_name"]["value"] == "11346 NORCROSS - Tampa, FL"
    assert "dealer_name" not in page["lines"][0]
    assert page["header"]["specifier_name"]["value"] == "Lumen Weft"

    assert refused == [
        {
            "document_id": "run__p0657",
            "field": field,
            "reason": "the_target_field_holds_a_reading",
        }
        for field in ("lines[2].dealer_name", "lines[4].dealer_name")
    ]
    assert fields["lines[2].dealer_name"]["value"] == "Workfields"
    assert fields["lines[4].dealer_name"]["value"] == "Studio MC+G"
    assert fields["lines[4].specifier_name"]["candidate_values"] == ["IA"]
    assert fields["lines[1].dealer_name"]["value"] is None
    assert absent == ["p0999"]
    # A page no rule names is not touched.
    assert other["fields"] == {"lines[0].dealer_name": reading("OFDC")}
    assert "lines" not in other

    report = lane.build_report(moved, refused, absent, authorization, rules)
    assert report["readings_refiled"] == 4
    assert report["by_page"] == {"p0657": 4}
    assert report["pages_not_in_records"] == ["p0999"]


def test_a_page_named_where_nothing_matches_is_left_as_it_was():
    data = {"documents": [{"document_id": "run__p0657", "fields": {"lines[0].amount": {}}}]}
    authorization, rules = lane.load_rules(RULES)
    assert lane.refile(data, authorization, rules) == ([], [], ["p0999"])
    assert "lines" not in data["documents"][0]


@pytest.mark.parametrize(
    ("rules", "message"),
    [
        ([], "must be a JSON object"),
        ({"refiles": []}, "operator authorization"),
        ({"authorization": "a"}, "names no re-file"),
        ({"authorization": "a", "refiles": ["x"]}, "must be an object"),
        (
            {"authorization": "a", "refiles": [{"from": "dealer_name", "to": "dealer_name"}]},
            "two different fields",
        ),
        ({"authorization": "a", "refiles": [{"from": "a", "to": "b"}]}, "names no page"),
        (
            {"authorization": "a", "refiles": [{"from": "a", "to": "b", "pages": ["p1"]}]},
            "cites no evidence",
        ),
    ],
)
def test_a_rule_that_cannot_be_applied_is_refused_before_anything_is_read(rules, message):
    with pytest.raises(ValueError, match=message):
        lane.load_rules(rules)


def write(path, value):
    path.write_text(json.dumps(value))
    return str(path)


def test_main_writes_new_files_reports_and_never_overwrites(tmp_path, capsys):
    source = write(tmp_path / "records.json", records())
    rules = write(tmp_path / "rules.json", RULES)
    out, report = str(tmp_path / "out.json"), str(tmp_path / "report.json")
    argv = [source, "--rules", rules, "--out", out, "--report", report]
    assert lane.main(argv) == 0
    printed = capsys.readouterr().out
    assert "readings re-filed: 4" in printed
    assert "p0657: 4" in printed
    assert "the target field holds a reading: 2" in printed
    assert "pages not in the records: p0999" in printed
    written = json.loads((tmp_path / "out.json").read_text())
    assert written["summary"]["column_refile"]["readings_refiled"] == 4
    assert json.loads((tmp_path / "report.json").read_text())["readings_refiled"] == 4

    # A second run would overwrite: refused before anything is read.
    with pytest.raises(SystemExit, match="Column re-file failed"):
        lane.main(argv)


def test_main_stays_quiet_and_prints_only_what_happened(tmp_path, capsys):
    clean = {
        "documents": [
            {"document_id": "run__p0657", "fields": {"lines[0].dealer_name": reading("X")}}
        ]
    }
    rules = {**RULES, "refiles": [{**RULES["refiles"][0], "pages": ["p0657"]}]}
    argv = [
        write(tmp_path / "records.json", clean),
        "--rules",
        write(tmp_path / "rules.json", rules),
        "--out",
        str(tmp_path / "out.json"),
        "--report",
        str(tmp_path / "report.json"),
    ]
    assert lane.main(argv) == 0
    printed = capsys.readouterr().out
    assert "readings re-filed: 1" in printed
    assert "left alone" not in printed and "not in the records" not in printed
    assert (
        lane.main(
            [*argv[:4], str(tmp_path / "q.json"), "--report", str(tmp_path / "qr.json"), "--quiet"]
        )
        == 0
    )
    assert capsys.readouterr().out == ""


def test_a_run_that_moved_nothing_or_read_no_records_fails(tmp_path):
    rules = write(tmp_path / "rules.json", RULES)
    nothing = write(tmp_path / "nothing.json", {"documents": []})
    with pytest.raises(SystemExit, match="moved nothing"):
        lane.main(
            [
                nothing,
                "--rules",
                rules,
                "--out",
                str(tmp_path / "a.json"),
                "--report",
                str(tmp_path / "b.json"),
            ]
        )
    not_records = write(tmp_path / "list.json", [])
    with pytest.raises(SystemExit, match="not a record artifact"):
        lane.main(
            [
                not_records,
                "--rules",
                rules,
                "--out",
                str(tmp_path / "c.json"),
                "--report",
                str(tmp_path / "d.json"),
            ]
        )
