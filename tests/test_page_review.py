"""Check the page-review lane against the failures that shaped it."""

import collections
import csv
import json
from decimal import Decimal

import page_review
import pytest

LINE_COLUMNS = (
    "document_id",
    "line_index",
    "commission_amount",
    "sales_amount",
    "duplicate_of_line",
    "restates_a_total",
    "repeats_an_earlier_block",
    "amount_without_line_identity",
    "repeats_a_job_and_amount_above",
)
DOCUMENT_COLUMNS = (
    "document_id",
    "total_amount",
    "total_amount__amount",
    "deposit_amount",
    "commission_amount",
    "commissionable_amount",
    "vendor_name",
)


def write_csv(path, columns, rows):
    """Write a delivery-shaped CSV the lane can read."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in columns})


def delivery(tmp_path, lines=(), documents=()):
    """A minimal delivery pair, returning both paths."""
    lines_path = tmp_path / "lines.csv"
    documents_path = tmp_path / "documents.csv"
    write_csv(lines_path, LINE_COLUMNS, lines)
    write_csv(documents_path, DOCUMENT_COLUMNS, documents)
    return str(lines_path), str(documents_path)


def review(**overrides):
    """A complete review, with the keys the checker requires."""
    base = {
        "page": "p1",
        "document_kind": "commission_statement",
        "page_rows": 1,
        "money_cells_checked": 1,
        "money_cells_wrong": 0,
        "page_money": ["100.00"],
        "money_column": "commission_amount",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,750.97", "1750.97"),
        ("$6,226.00", "6226.00"),
        ("(662.89)", "-662.89"),
        ("57.06 USD", "57.06"),
        ("USD 1,234.00", "1234.00"),
        ("", None),
        ("   ", None),
        ("PROJECT CANCELLED", None),
    ],
)
def test_money_is_read_as_the_page_prints_it(raw, expected):
    """Parentheses mean negative here; a value that will not parse is not money."""
    value = page_review.money_value(raw)
    assert (str(value) if value is not None else None) == expected


def test_a_page_that_matches_its_export_reconciles(tmp_path):
    """The plain case: what the page prints is what the export counts."""
    lines, documents = delivery(
        tmp_path,
        lines=[{"document_id": "run__p1", "line_index": "0", "commission_amount": "100.00"}],
    )
    assert page_review.reconcile(review(), lines, documents) == ([], [])


def test_money_the_export_holds_nowhere_is_lost(tmp_path):
    """A figure printed on the page and absent from every export row.

    Off-by-one row registration dropped a page's largest commission entirely,
    and no total on that document was short enough to notice.
    """
    lines, documents = delivery(
        tmp_path,
        lines=[{"document_id": "run__p1", "line_index": "0", "commission_amount": "100.00"}],
    )
    lost, invented = page_review.reconcile(
        review(page_money=["100.00", "13885.00"]), lines, documents
    )
    assert [str(v) for v in lost] == ["13885.00"]
    assert invented == []


def test_money_the_page_never_printed_is_invented(tmp_path):
    """A countable row carrying a figure that appears nowhere on the page."""
    lines, documents = delivery(
        tmp_path,
        lines=[
            {"document_id": "run__p1", "line_index": "0", "commission_amount": "100.00"},
            {"document_id": "run__p1", "line_index": "1", "commission_amount": "48.10"},
        ],
    )
    lost, invented = page_review.reconcile(review(), lines, documents)
    assert lost == []
    assert [str(v) for v in invented] == ["48.10"]


def test_a_flagged_duplicate_stub_is_not_an_invention(tmp_path):
    """A cheque prints two identical stubs and the export counts one.

    Measuring invention against everything the export retains rather than
    against its countable rows called every correctly de-duplicated remittance
    a defect.
    """
    lines, documents = delivery(
        tmp_path,
        lines=[
            {"document_id": "run__p1", "line_index": "0", "commission_amount": "4797.00"},
            {
                "document_id": "run__p1",
                "line_index": "1",
                "commission_amount": "4797.00",
                "duplicate_of_line": "0",
            },
        ],
    )
    result = page_review.reconcile(review(page_money=["4797.00", "4797.00"]), lines, documents)
    assert result == ([], [])


def test_a_value_the_export_only_excluded_is_not_lost(tmp_path):
    """The export retains a subtotal row and excludes it; the page prints it.

    Lost money is measured against everything retained, so a flag is never
    mistaken for a loss.
    """
    lines, documents = delivery(
        tmp_path,
        lines=[
            {"document_id": "run__p1", "line_index": "0", "commission_amount": "100.00"},
            {
                "document_id": "run__p1",
                "line_index": "1",
                "commission_amount": "38.00",
                "restates_a_total": "yes",
            },
        ],
    )
    lost, invented = page_review.reconcile(review(page_money=["100.00", "38.00"]), lines, documents)
    assert (lost, invented) == ([], [])


def test_a_remittance_states_its_money_in_the_header(tmp_path):
    """A page with no line items is not a page with no money.

    Reading only the line rows called four correct deposit advices a
    disagreement.
    """
    lines, documents = delivery(
        tmp_path,
        documents=[
            {"document_id": "run__p1", "total_amount": "9,182.39", "deposit_amount": "9,182.39"}
        ],
    )
    result = page_review.reconcile(review(page_money=["9,182.39"]), lines, documents)
    assert result == ([], [])


def test_a_page_stating_no_money_asks_nothing(tmp_path):
    """A blank page names no money column and reconciles trivially."""
    lines, documents = delivery(tmp_path)
    result = page_review.reconcile(
        review(page_money=[], money_column="", document_kind="blank"), lines, documents
    )
    assert result == ([], [])


def test_an_unparseable_page_value_is_ignored_not_guessed(tmp_path):
    """A reviewer's stray text never becomes a phantom loss."""
    lines, documents = delivery(
        tmp_path,
        lines=[{"document_id": "run__p1", "line_index": "0", "commission_amount": "100.00"}],
    )
    result = page_review.reconcile(review(page_money=["100.00", "n/a"]), lines, documents)
    assert result == ([], [])


def test_a_review_missing_its_money_list_is_a_schema_error():
    """A review that omitted the check would otherwise reconcile with anything."""
    incomplete = review()
    del incomplete["page_money"]
    assert "page_money" in page_review.schema_error(incomplete)
    assert page_review.schema_error(review()) == ""


def test_a_packet_carries_the_image_and_the_export(tmp_path):
    """The reviewer gets one page's image path and the rows claimed for it."""
    lines, documents = delivery(
        tmp_path, lines=[{"document_id": "run__p1", "line_index": "0", "commission_amount": "1.00"}]
    )
    images = tmp_path / "images"
    images.mkdir()
    (images / "000001_run__p1.png").write_bytes(b"")
    out = page_review.packet(lines, documents, str(images), "p1")
    assert out["image"].endswith("000001_run__p1.png")
    assert len(out["lines"]) == 1


def test_a_packet_for_a_page_with_no_image_still_emits(tmp_path):
    """A missing image is reported as empty, not as a crash."""
    lines, documents = delivery(tmp_path)
    out = page_review.packet(lines, documents, str(tmp_path / "none"), "p1")
    assert out["image"] == ""


def test_misreadings_and_spurious_rows_are_counted_apart():
    """A page can emit forty-four records for eleven rows and misread nothing."""
    counts = page_review.accuracy(
        [review(money_cells_checked=48, money_cells_wrong=26, misread_cells=0, spurious_rows=26)]
    )
    assert counts["misread_cells"] == 0
    assert counts["spurious_rows"] == 26


def test_a_review_written_before_the_split_still_counts():
    """Older reviews carry only the combined count and are not reinterpreted."""
    counts = page_review.accuracy([review(money_cells_checked=10, money_cells_wrong=3)])
    assert counts["money_cells_wrong"] == 3
    assert counts["misread_cells"] == 0


def test_unparseable_review_files_are_named_not_skipped(tmp_path):
    """A file that will not parse is a reported failure, never a silent pass."""
    (tmp_path / "p1.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "p2.json").write_text(json.dumps(review(page="p2")), encoding="utf-8")
    reviews, broken = page_review.load_reviews(str(tmp_path))
    assert [r["page"] for r in reviews] == ["p2"]
    assert broken[0][0] == "p1.json"


def test_the_summary_separates_clean_pages_from_disagreements(tmp_path):
    """The rate a reader acts on, and the pages behind it."""
    lines, documents = delivery(
        tmp_path,
        lines=[{"document_id": "run__p1", "line_index": "0", "commission_amount": "100.00"}],
    )
    incomplete = review(page="p3")
    del incomplete["money_column"]
    summary = page_review.summarize(
        [review(), review(page="p2", page_money=["999.00"]), incomplete], lines, documents
    )
    assert summary["pages_reconciling"] == 1
    assert summary["pages_disagreeing"] == 1
    assert summary["schema_errors"][0]["page"] == "p3"


def test_main_emits_a_packet(tmp_path, capsys):
    """`--packet` prints one page and stops."""
    lines, documents = delivery(tmp_path)
    code = page_review.main(["--lines-csv", lines, "--documents-csv", documents, "--packet", "p1"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["page"] == "p1"


def test_main_checks_the_reviews_and_writes_the_summary(tmp_path, capsys):
    """The reported rate, the reconciliation, and the artifact beside them."""
    lines, documents = delivery(
        tmp_path,
        lines=[{"document_id": "run__p1", "line_index": "0", "commission_amount": "100.00"}],
    )
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    (reviews / "p1.json").write_text(json.dumps(review()), encoding="utf-8")
    out = tmp_path / "summary.json"
    page_review.main(
        [
            "--lines-csv",
            lines,
            "--documents-csv",
            documents,
            "--reviews",
            str(reviews),
            "--out",
            str(out),
        ]
    )
    printed = capsys.readouterr().out
    assert "reconciling with the export: 1" in printed
    assert "accuracy over every checked cell: 100.00%" in printed
    assert json.loads(out.read_text())["pages_reviewed"] == 1


def test_main_reports_a_disagreement_and_a_broken_file(tmp_path, capsys):
    """Both kinds of failure reach the reader."""
    lines, documents = delivery(tmp_path)
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    (reviews / "p1.json").write_text(json.dumps(review()), encoding="utf-8")
    (reviews / "p9.json").write_text("{oops", encoding="utf-8")
    page_review.main(
        ["--lines-csv", lines, "--documents-csv", documents, "--reviews", str(reviews)]
    )
    printed = capsys.readouterr().out
    assert "lost ['100.00']" in printed
    assert "unparseable" in printed


def test_main_stays_quiet_when_asked(tmp_path, capsys):
    """`--quiet` writes the artifact and prints nothing."""
    lines, documents = delivery(tmp_path)
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    page_review.main(
        ["--lines-csv", lines, "--documents-csv", documents, "--reviews", str(reviews), "--quiet"]
    )
    assert capsys.readouterr().out == ""


def test_main_refuses_without_a_page_or_a_review_directory(tmp_path):
    """Neither mode chosen is a mistake, not a silent success."""
    lines, documents = delivery(tmp_path)
    with pytest.raises(SystemExit) as exc:
        page_review.main(["--lines-csv", lines, "--documents-csv", documents])
    assert "--packet" in str(exc.value)


def test_main_reports_an_unreadable_delivery(tmp_path):
    """A missing CSV names itself rather than raising a traceback."""
    with pytest.raises(SystemExit) as exc:
        page_review.main(
            [
                "--lines-csv",
                str(tmp_path / "missing.csv"),
                "--documents-csv",
                str(tmp_path / "missing.csv"),
                "--packet",
                "p1",
            ]
        )
    assert "Page review failed" in str(exc.value)


def test_a_line_row_with_an_empty_money_cell_contributes_nothing(tmp_path):
    """A row exists but states nothing in the column being checked."""
    lines, documents = delivery(
        tmp_path,
        lines=[
            {"document_id": "run__p1", "line_index": "0", "commission_amount": "100.00"},
            {"document_id": "run__p1", "line_index": "1", "commission_amount": ""},
        ],
    )
    assert page_review.reconcile(review(), lines, documents) == ([], [])


def test_a_typed_companion_is_not_counted_twice_in_a_header(tmp_path):
    """`total_amount` and `total_amount__amount` are one figure, not two.

    The typed companion sits beside every money column. Counting it as a
    separate header value would double every remittance.
    """
    lines, documents = delivery(
        tmp_path,
        documents=[
            {
                "document_id": "run__p1",
                "total_amount": "9,182.39",
                "total_amount__amount": "9182.39",
            }
        ],
    )
    result = page_review.reconcile(review(page_money=["9,182.39"]), lines, documents)
    assert result == ([], [])


def test_a_header_restating_the_page_total_is_not_an_invention(tmp_path):
    """A tail page prints only its report's overall total, and the header holds it.

    The page lists no data-row money, so the total is all there is; the header
    is where a total belongs.
    """
    lines, documents = delivery(
        tmp_path, documents=[{"document_id": "run__p1", "total_amount": "1,008,964.99"}]
    )
    tail = review(page_money=[], money_column="total_amount", page_total="$1,008,964.99")
    assert page_review.reconcile(tail, lines, documents) == ([], [])


def test_a_header_is_compared_on_the_page_money_column_only(tmp_path):
    """A header's commissionable base is printed too, but is not the page's money.

    Sweeping every header figure in called a correct one-line statement's base
    of 54,859.50 an invention.
    """
    lines, documents = delivery(
        tmp_path,
        documents=[
            {
                "document_id": "run__p1",
                "commission_amount": "1,371.49",
                "commissionable_amount": "54,859.50",
            }
        ],
    )
    result = page_review.reconcile(review(page_money=["1,371.49"]), lines, documents)
    assert result == ([], [])


def test_a_header_without_the_money_column_is_read_whole(tmp_path):
    """No header column matches the page's, so every money column is consulted."""
    lines, documents = delivery(
        tmp_path, documents=[{"document_id": "run__p1", "total_amount": "1,156,674.25"}]
    )
    tail = review(page_money=[], money_column="extended_amount", page_total="1,156,674.25")
    assert page_review.reconcile(tail, lines, documents) == ([], [])


def test_a_line_restating_the_page_total_is_still_an_invention(tmp_path):
    """The same figure on a line row is the page's money counted twice."""
    lines, documents = delivery(
        tmp_path,
        lines=[
            {"document_id": "run__p1", "line_index": "0", "commission_amount": "100.00"},
            {"document_id": "run__p1", "line_index": "1", "commission_amount": "100.00"},
        ],
    )
    lost, invented = page_review.reconcile(review(page_total="100.00"), lines, documents)
    assert lost == []
    assert [str(v) for v in invented] == ["100.00"]


def test_a_run_with_nothing_checked_reports_no_rate(capsys):
    """No cells checked is not 100%; it is no measurement at all."""
    page_review.report(
        {
            "pages_reviewed": 0,
            "pages_reconciling": 0,
            "pages_disagreeing": 0,
            "disagreements": [],
            "schema_errors": [],
            "money_cells_checked": 0,
            "money_cells_wrong": 0,
            "misread_cells": 0,
            "spurious_rows": 0,
        }
    )
    assert "accuracy" not in capsys.readouterr().out


FIELD_COLUMNS = ("document_id", "field", "all_readings")


def extractor_dir(tmp_path, pages):
    """Retained extractor responses, one file per page, holding the page's text."""
    directory = tmp_path / "docai"
    directory.mkdir()
    for index, (page, text) in enumerate(pages):
        body = {"response": {"document": {"text": text}}}
        (directory / f"{index:06d}_run__{page}.json").write_text(json.dumps(body), encoding="utf-8")
    return str(directory)


def test_the_extractor_counts_what_each_page_prints(tmp_path):
    """Every number the extractor read, keeping the most any one response saw.

    A page with no readable response is absent rather than empty: nothing seen
    is not the same as nothing printed.
    """
    directory = extractor_dir(
        tmp_path, [("p1", "Total 799.67 and 799.67"), ("p1", "799.67"), ("p2", "(662.89)")]
    )
    (tmp_path / "docai" / "900000_run__p3.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "docai" / "900001_run__p4.json").write_text(json.dumps({"response": {}}))
    found = page_review.extractor_numbers(directory)
    assert found["p1"][Decimal("799.67")] == 2
    assert found["p2"][Decimal("-662.89")] == 1
    assert "p3" not in found
    assert "p4" not in found
    assert page_review.extractor_numbers("") == {}


def test_a_page_read_only_in_its_retry_counts_under_its_own_page(tmp_path):
    """A retry keeps its suffix in the filename; it is still that page's reading.

    Keyed by the name's last segment, every retry was filed under a page called
    `retry1`, and the 222 pages read only in a retry had no extractor evidence.
    """
    directory = tmp_path / "docai"
    directory.mkdir()
    empty = {"response": {"document": {"text": ""}}}
    read = {"response": {"document": {"text": "Total 42.10"}}}
    (directory / "000001_run__p5.json").write_text(json.dumps(empty), encoding="utf-8")
    (directory / "000002_run__p5__retry1.json").write_text(json.dumps(read), encoding="utf-8")
    found = page_review.extractor_numbers(str(directory))
    assert found["p5"][Decimal("42.10")] == 1
    assert "retry1" not in found


def test_every_engine_reading_is_kept_for_a_field(tmp_path):
    """Consensus keeps one value; the field grain keeps every engine's reading."""
    path = tmp_path / "fields.csv"
    write_csv(
        path,
        FIELD_COLUMNS,
        [
            {
                "document_id": "run__p1",
                "field": "lines[3].commission_amount",
                "all_readings": "1,750.97; 8,918.38",
            }
        ],
    )
    readings = page_review.retained_readings(str(path))
    assert readings[("p1", "lines[3].commission_amount")] == [
        Decimal("1750.97"),
        Decimal("8918.38"),
    ]
    assert page_review.retained_readings("") == {}


@pytest.mark.parametrize(
    ("readings", "printed", "expected"),
    [
        ([Decimal("799.67")], {Decimal("799.67"): 1}, "extractor_text_and_engine"),
        ([], {Decimal("799.67"): 1}, "extractor_text"),
        ([Decimal("799.67")], None, "engine"),
        ([], None, "reviewer_only"),
    ],
)
def test_a_correction_names_its_evidence(readings, printed, expected):
    """A reviewer's reading is one model's; the grade says what stands beside it."""
    counter = None if printed is None else collections.Counter(printed)
    assert page_review.correction_evidence(Decimal("799.67"), readings, counter) == expected


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("799.67", "799.67"),
        ("799.67 (1,404.13 is job 13046's)", "799.67"),
        ("14,041.33 (28,950.92 is job 12951's)", "14041.33"),
        ("1,558 per the TOT column", "1558"),
        ("(662.89) credit", "-662.89"),
        ("2 rows: 100.00 and 200.00", None),
        ("blank - prints nothing", None),
        ("", None),
    ],
)
def test_a_reviewer_figure_is_read_with_its_note_set_aside(said, expected):
    """A shifted row's fix is a figure and a note; both halves of the fix must land.

    Reading only a bare number skipped `799.67 (1,404.13 is job 13046's)`, and
    amending the other half alone counted 1,404.13 twice on one page.
    """
    value = page_review.reviewer_value(said)
    assert (None if value is None else str(value)) == expected


def test_a_correction_names_the_other_lines_that_hold_its_figure(tmp_path):
    """Moving a figure onto its line is half a fix while another line still holds it.

    A line already excluded as a total does not count, and zero is exempt: it
    repeats on every cancelled line and moves no money.
    """
    lines, documents = delivery(
        tmp_path,
        lines=[
            {"document_id": "run__p1", "line_index": "4", "commission_amount": ""},
            {"document_id": "run__p1", "line_index": "10", "commission_amount": "1,404.13"},
            {
                "document_id": "run__p1",
                "line_index": "11",
                "commission_amount": "1,404.13",
                "restates_a_total": "yes",
            },
            {"document_id": "run__p1", "line_index": "12", "commission_amount": "0.00"},
        ],
    )
    first = review(
        page_money=["1,404.13", "0.00"],
        wrong_cells=[
            {"line_index": "4", "column": "commission_amount", "export": "", "page": "1,404.13"},
            {"line_index": "5", "column": "commission_amount", "export": "", "page": "0.00"},
            {
                "line_index": "header",
                "column": "commissionable_amount",
                "export": "",
                "page": "1,404.13",
            },
        ],
    )
    out = page_review.proposals([first], lines, documents, "", "")
    moves = {c["line_index"]: c["moves_from_lines"] for c in out["corrections"]}
    assert moves == {"4": ["10"], "5": [], "header": []}


def test_the_reviews_become_proposals_graded_by_evidence(tmp_path):
    """Corrections, rows the page never prints, and lost values, each graded.

    Job 13075's commission prints 799.67 and the export carries 799.87 -- a
    reading one retained engine got right. A second pass re-emits a row the
    page prints once, so the extractor sees the figure fewer times than the
    export counts it. A page the extractor never read is marked as such rather
    than treated as a page that printed nothing.
    """
    lines, documents = delivery(
        tmp_path,
        lines=[
            {"document_id": "run__p1", "line_index": "0", "commission_amount": "799.87"},
            {"document_id": "run__p1", "line_index": "1", "commission_amount": "500.00"},
            {
                "document_id": "run__p1",
                "line_index": "2",
                "commission_amount": "26.96",
                "restates_a_total": "yes",
            },
            {"document_id": "run__p1", "line_index": "3", "commission_amount": ""},
            {"document_id": "run__p1", "line_index": "4", "commission_amount": "40.00"},
            {"document_id": "run__p2", "line_index": "0", "commission_amount": "10.00"},
            {"document_id": "run__p2", "line_index": "1", "commission_amount": "7.00"},
        ],
    )
    fields = tmp_path / "fields.csv"
    write_csv(
        fields,
        FIELD_COLUMNS,
        [
            {
                "document_id": "run__p1",
                "field": "lines[0].commission_amount",
                "all_readings": "799.87; 799.67",
            }
        ],
    )
    raw = extractor_dir(tmp_path, [("p1", "799.67 13,885.00 40.00 40.00")])
    first = review(
        page_money=["799.67", "40.00", "13,885.00", "1.00"],
        wrong_cells=[
            {
                "line_index": "0",
                "column": "commission_amount",
                "export": "799.87",
                "page": "799.67",
            },
            {
                "line_index": "header",
                "column": "commissionable_amount",
                "export": "993,673.65",
                "page": "93,673.65",
            },
            {"line_index": "1", "column": "commission_amount", "page": "not printed - second pass"},
            {"line_index": "2", "column": "commission_amount", "page": "duplicate subtotal row"},
            {"line_index": "3", "column": "commission_amount", "page": "not printed"},
            {"line_index": "4", "column": "commission_amount", "page": "repeat of line 0"},
            {"line_index": "12-47", "column": "commission_amount", "page": "second pass"},
            {"line_index": "5", "column": "customer_name", "export": "X", "page": "Y"},
            {"line_index": "0", "column": "commission_amount", "page": "prints nothing here"},
            {"line_index": "", "column": "commission_amount", "page": "12.00"},
            "a note, not a cell",
        ],
    )
    unread = review(
        page="p2",
        wrong_cells=[
            {"line_index": "0", "column": "commission_amount", "export": "10.00", "page": "100.00"},
            {"line_index": "1", "column": "commission_amount", "page": "not printed"},
        ],
    )
    blank = review(
        page="p4",
        money_column="",
        page_money=[],
        wrong_cells=[{"line_index": "0", "page": "not printed"}],
    )
    broken = review(page="p3")
    del broken["money_column"]
    out = page_review.proposals([first, unread, blank, broken], lines, documents, str(fields), raw)
    corrections = {(c["page"], c["line_index"]): c for c in out["corrections"]}
    assert corrections[("p1", "0")]["evidence"] == "extractor_text_and_engine"
    assert corrections[("p1", "0")]["difference"] == "0.20"
    assert corrections[("p1", "header")]["evidence"] == "reviewer_only"
    assert corrections[("p2", "0")]["evidence"] == "reviewer_only"
    assert len(corrections) == 3
    extra = {(r["page"], r["line_index"]): r["evidence"] for r in out["rows_not_printed"]}
    assert extra == {
        ("p1", "1"): "extractor_prints_it_fewer_times",
        ("p1", "3"): "no_money_on_row",
        ("p1", "4"): "extractor_prints_it_as_often",
        ("p2", "1"): "no_extractor_response",
    }
    lost = {(v["page"], v["value"]): v["evidence"] for v in out["lost_values"]}
    assert lost[("p1", "13885.00")] == "extractor_saw_it"
    assert lost[("p1", "1.00")] == "extractor_missed_it"
    assert lost[("p2", "100.00")] == "no_extractor_response"
    assert out["summary"]["corrections"]["reviewer_only"]["count"] == 2
    assert out["pages_the_extractor_read"] == 1
    assert out["decision_mode"] == "proposal_only"


def test_main_writes_the_proposals(tmp_path, capsys):
    """`--proposals` writes the graded artifact beside the summary and prints it."""
    lines, documents = delivery(
        tmp_path,
        lines=[{"document_id": "run__p1", "line_index": "0", "commission_amount": "799.87"}],
    )
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    cell = {"line_index": "0", "column": "commission_amount", "export": "799.87", "page": "799.67"}
    (reviews / "p1.json").write_text(
        json.dumps(review(page_money=["799.67"], wrong_cells=[cell])), encoding="utf-8"
    )
    out = tmp_path / "proposals.json"
    args = ["--lines-csv", lines, "--documents-csv", documents, "--reviews", str(reviews)]
    page_review.main([*args, "--proposals", str(out)])
    printed = capsys.readouterr().out
    assert "corrections:" in printed
    assert "reviewer_only: 1 (0.20)" in printed
    assert json.loads(out.read_text())["artifact_type"] == "page_review_proposals_v1"
    page_review.main([*args, "--proposals", str(tmp_path / "quiet.json"), "--quiet"])
    assert capsys.readouterr().out == ""


def second_read(
    tmp_path, pages, name="second.json", provider="openrouter", model="google/gemini-2.5-flash"
):
    """Another vendor's retained handoff: each page's lines and header."""
    records = [
        {"page_id": f"run__{page}", "lines": lines, "header": header}
        for page, (lines, header) in pages.items()
    ]
    path = tmp_path / name
    body = {"engine": f"{provider}/{model}", "provider": provider, "model": model}
    path.write_text(json.dumps({**body, "records": records}), encoding="utf-8")
    return str(path)


def test_a_second_vendor_regrades_only_what_it_can_bear_out(tmp_path):
    """Another vendor reading the figure lifts a correction; missing a row labels nothing.

    p1: the reviewer alone says line 0 prints 799.67 and the second read holds
    it; line 3's fix already has the extractor's text and is left as it was.
    p2: the second read holds 7.00 once where the export counts it twice, but it
    misses 9.00 as well, so its silence about line 1 is no evidence. p3: the
    second read returned no line, so only its header counts. p4: it holds every
    other figure and 50.00 once, bearing the reviewer out. p5: the figure the
    reviewer names already sits on another line, as the second read has it.
    """

    def row(page, index, amount):
        return {"document_id": f"run__{page}", "line_index": index, "commission_amount": amount}

    def wrong(line, page, export="", column="commission_amount"):
        return {"line_index": line, "column": column, "export": export, "page": page}

    lines, documents = delivery(
        tmp_path,
        lines=[
            row("p1", "0", "799.87"),
            row("p1", "3", "12.00"),
            row("p2", "0", "7.00"),
            row("p2", "1", "7.00"),
            row("p2", "2", "9.00"),
            row("p3", "0", "5.00"),
            row("p3", "1", "5.00"),
            row("p4", "0", "50.00"),
            row("p4", "1", "50.00"),
            row("p4", "2", "12.00"),
            row("p5", "0", "30.00"),
            row("p5", "1", "40.00"),
        ],
    )
    raw = extractor_dir(
        tmp_path,
        [
            ("p1", "13.00"),
            ("p2", "7.00 7.00 9.00"),
            ("p3", "5.00 5.00"),
            ("p4", "50.00 50.00 12.00"),
            ("p5", "30.00"),
        ],
    )
    reviews = [
        review(
            page="p1",
            page_money=["799.67", "13.00"],
            wrong_cells=[wrong("0", "799.67", "799.87"), wrong("3", "13.00", "12.00")],
        ),
        review(page="p2", page_money=["7.00", "9.00"], wrong_cells=[wrong("1", "not printed")]),
        review(
            page="p3",
            page_money=["5.00"],
            wrong_cells=[
                wrong("header", "1,100.00", "1,000.00", "commissionable_amount"),
                wrong("0", "6.00", "5.00"),
                wrong("1", "duplicate of line 0"),
            ],
        ),
        review(page="p4", page_money=["50.00", "12.00"], wrong_cells=[wrong("1", "second pass")]),
        review(page="p5", page_money=["40.00"], wrong_cells=[wrong("0", "40.00", "30.00")]),
    ]
    second = page_review.load_second_read(
        second_read(
            tmp_path,
            {
                "p1": ([{"commission_amount": {"value": "799.67"}, "description": "Chair"}], {}),
                "p2": ([{"commission_amount": "7.00"}], {}),
                "p3": ([], {"commissionable_amount": {"value": "1,100.00"}, "vendor_name": "Acme"}),
                "p4": ([{"commission_amount": "50.00"}, {"commission_amount": "12.00"}], {}),
                "p5": ([{"commission_amount": "40.00"}], {}),
            },
        ),
        "Anthropic",
    )
    out = page_review.proposals(reviews, lines, documents, "", raw, second)

    corrections = {(c["page"], c["line_index"]): c for c in out["corrections"]}
    assert corrections[("p1", "0")]["evidence"] == "second_vendor"
    assert corrections[("p1", "0")]["second_read"] == {
        "holds_it": 1,
        "export_holds_it_elsewhere": 0,
        "agrees": True,
    }
    assert corrections[("p1", "3")]["evidence"] == "extractor_text"
    assert "second_read" not in corrections[("p1", "3")]
    assert corrections[("p3", "header")]["evidence"] == "second_vendor"
    assert corrections[("p3", "0")]["evidence"] == "reviewer_only"
    assert "second_read" not in corrections[("p3", "0")]
    assert corrections[("p5", "0")]["evidence"] == "reviewer_only"
    assert corrections[("p5", "0")]["second_read"]["export_holds_it_elsewhere"] == 1
    rows = {(r["page"], r["line_index"]): r for r in out["rows_not_printed"]}
    assert rows[("p4", "1")]["evidence"] == "second_vendor_prints_it_fewer_times"
    assert rows[("p2", "1")]["evidence"] == "extractor_prints_it_as_often"
    assert rows[("p2", "1")]["second_read"]["holds_every_other_figure"] is False
    assert "second_read" not in rows[("p3", "1")]
    assert out["second_read"]["vendor"] == "google"
    assert out["second_read"]["reviewer_vendor"] == "anthropic"
    assert out["second_read"]["pages_with_no_line"] == ["p3"]
    assert out["summary"]["corrections"]["second_vendor"]["count"] == 2
    assert "reviewers' own" in out["findings"][-1]


def test_a_second_read_must_be_by_another_known_vendor(tmp_path):
    """The reviewers' own vendor, a router choosing its own model, and no named reviewer."""
    own = second_read(tmp_path, {"p1": ([], {})}, "own.json", "anthropic", "claude-opus-5")
    with pytest.raises(ValueError, match="reviewers' own vendor"):
        page_review.load_second_read(own, "Anthropic")
    with pytest.raises(ValueError, match="--reviewer-vendor"):
        page_review.load_second_read(own, " ")
    routed = second_read(tmp_path, {"p1": ([], {})}, "routed.json", "openrouter", "openrouter/auto")
    with pytest.raises(ValueError, match="cannot resolve"):
        page_review.load_second_read(routed, "anthropic")


def test_main_regrades_with_a_second_read_and_needs_proposals_for_it(tmp_path, capsys):
    """`--second-read` says what it read; without proposals, or by the reviewers' vendor, no."""
    lines, documents = delivery(
        tmp_path,
        lines=[{"document_id": "run__p1", "line_index": "0", "commission_amount": "799.87"}],
    )
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    cell = {"line_index": "0", "column": "commission_amount", "export": "799.87", "page": "799.67"}
    (reviews / "p1.json").write_text(
        json.dumps(review(page_money=["799.67"], wrong_cells=[cell])), encoding="utf-8"
    )
    second = second_read(tmp_path, {"p1": ([{"commission_amount": "799.67"}], {})})
    args = ["--lines-csv", lines, "--documents-csv", documents, "--reviews", str(reviews)]
    args += ["--second-read", second]
    out = tmp_path / "proposals.json"
    page_review.main([*args, "--reviewer-vendor", "anthropic", "--proposals", str(out)])
    printed = capsys.readouterr().out
    assert "second read: openrouter/google/gemini-2.5-flash (google) over 1 pages" in printed
    assert "second_vendor: 1 (0.20)" in printed
    assert json.loads(out.read_text())["second_read"]["reviewer_vendor"] == "anthropic"
    with pytest.raises(SystemExit, match="give --proposals"):
        page_review.main(args)
    with pytest.raises(SystemExit, match="Page review failed"):
        page_review.main(
            [*args, "--reviewer-vendor", "google", "--proposals", str(tmp_path / "x.json")]
        )
