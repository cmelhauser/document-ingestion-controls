"""Write the workbook a client answers in.

The instruction PDF explains the questions; this is where the answers are
recorded and sent back. Its shape follows from that job: the answering surface
comes first and is short, the exhaustive queue is present but out of the way, and
every column says what it means on a tab of its own.

Answer cells carry real dropdowns where a question offers options, so a returned
workbook contains values this pipeline can read back rather than free text that
has to be interpreted. The Notes column stays free-form for the case the options
do not cover, because forcing a client to pick a wrong option to proceed is how a
review produces a confident wrong answer.

Nothing written here is an approval. A returned workbook is a client decision
artifact and still passes through the documented import path before anything
downstream changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import xlsxwriter

QUESTION_COLUMNS = (
    ("Question", 12, "The question number. It matches the numbered page in the PDF."),
    ("What we need to know", 62, "The question, in plain language."),
    (
        "What we read",
        38,
        "The values our systems read for this group, separated by |. Two or more "
        "means they disagreed and your answer decides; one means only one system "
        "read it and your answer confirms it. Blank means there is no value to "
        "show, and the question is about something other than a reading.",
    ),
    ("Your answer", 34, "Pick from the dropdown, or leave blank and use Notes."),
    ("Your notes", 46, "Anything the options do not cover. Free text."),
    (
        "How your answer is used",
        52,
        "Whether answering closes the group or informs an item-by-item recheck.",
    ),
    ("Items this settles", 16, "How many individual findings your answer resolves."),
    ("Documents affected", 16, "How many documents those findings span."),
    ("Priority", 12, "How urgent this is: critical, high, or normal."),
    (
        "What we think these are",
        26,
        "The kind of document this question covers, as far as we could establish it.",
    ),
    (
        "Fields and columns this is about",
        52,
        "The specific fields and column captions the question refers to.",
    ),
    ("Example document", 30, "The document shown on the question's PDF page."),
    ("Example field", 28, "The specific field the example illustrates."),
)
ITEM_COLUMNS = (
    ("priority", 12),
    ("document_id", 30),
    ("page_id", 30),
    ("field", 28),
    ("reason", 60),
    ("review_source", 24),
    ("disposition", 26),
)


def _formats(book: xlsxwriter.Workbook) -> dict[str, Any]:
    """Build the shared cell formats once."""
    return {
        "title": book.add_format(
            {"bold": True, "font_size": 16, "font_color": "#FFFFFF", "bg_color": "#1F4E78"}
        ),
        "head": book.add_format(
            {"bold": True, "bg_color": "#DDEBF7", "border": 1, "text_wrap": True, "valign": "top"}
        ),
        "body": book.add_format({"text_wrap": True, "valign": "top", "border": 1}),
        "answer": book.add_format(
            {"text_wrap": True, "valign": "top", "border": 1, "bg_color": "#FFF7E6"}
        ),
        "protected": book.add_format(
            {
                "text_wrap": True,
                "valign": "top",
                "border": 1,
                "bg_color": "#F2F2F2",
                "font_color": "#7F7F7F",
            }
        ),
        "prose": book.add_format({"text_wrap": True, "valign": "top"}),
        "bold": book.add_format({"bold": True}),
    }


def _start_here(book: xlsxwriter.Workbook, payload: dict[str, Any], fmt: dict[str, Any]) -> None:
    """Write the short orientation tab a client opens first."""
    sheet = book.add_worksheet("Start here")
    summary = payload.get("summary", {})
    sheet.set_column("A:A", 4)
    sheet.set_column("B:B", 110)
    sheet.merge_range("A1:B1", "Client review - please start here", fmt["title"])
    lines = [
        "",
        "We read every document twice and checked the results. What is left could not be",
        "settled from the documents alone, so we need your decision on it.",
        "",
        f"Questions to answer: {summary.get('answerable_question_count', 0)}",
        f"Individual items they resolve: {summary.get('items_covered', 0)} of "
        f"{summary.get('queue_item_count', 0)} ({summary.get('coverage_pct', 0)}%)",
        f"Average items settled per question: {summary.get('items_per_question', 0)}",
        "",
        "How to answer:",
        "  1. Go to the Questions tab.",
        "  2. For each row, read the matching numbered page in the PDF that came with this file.",
        "  3. Look at the document page shown there.",
        "  4. Choose an answer from the dropdown, or write one in Your notes.",
        "  5. Save this file and send it back. Keep the PDF for reference.",
        "",
        "You do not need to look at the All items tab. It is the complete list behind the",
        "questions, included so nothing is hidden from you.",
        "",
        "Anything you do not answer stays open. We will not close an item because it looks",
        "likely, and we will not choose an answer on your behalf.",
    ]
    for row, line in enumerate(lines, start=2):
        sheet.write(row, 1, line, fmt["prose"])
    sheet.freeze_panes(2, 0)


def _questions(
    book: xlsxwriter.Workbook,
    payload: dict[str, Any],
    fmt: dict[str, Any],
    prefill: dict[str, Any] | None = None,
) -> None:
    """Write the answering surface, with dropdowns where options exist."""
    prefill = prefill or {}
    sheet = book.add_worksheet("Questions")
    sheet.merge_range(
        0, 0, 0, len(QUESTION_COLUMNS) - 1, "Questions we need answered", fmt["title"]
    )
    for column, (name, width, _) in enumerate(QUESTION_COLUMNS):
        sheet.set_column(column, column, width)
        sheet.write(2, column, name, fmt["head"])
    for row, question in enumerate(payload.get("questions", []), start=3):
        answerable = question.get("client_answer_permitted", True)
        cell = fmt["answer"] if answerable else fmt["protected"]
        body = fmt["body"] if answerable else fmt["protected"]
        example = question.get("example", {})
        sheet.write(row, 0, question.get("question_id", ""), body)
        sheet.write(row, 1, question.get("headline", ""), body)
        prefilled = prefill.get(question.get("question_id", "")) or {}
        # A carried answer is written into the same cells the client types in,
        # so a second round is a review of what they already said rather than a
        # blank form they have to fill twice.
        # The readings sit immediately before the answer box, because choosing
        # between them is the answer. Anywhere else and the client is asked
        # which value is right without being shown one.
        sheet.write(row, 2, question.get("what_we_read", ""), body)
        sheet.write(
            row, 3, prefilled.get("answer") or ("" if answerable else "No answer needed"), cell
        )
        sheet.write(row, 4, prefilled.get("note", ""), cell)
        sheet.write(row, 5, question.get("how_your_answer_is_used", ""), body)
        sheet.write(row, 6, question.get("resolves_item_count", 0), body)
        sheet.write(row, 7, question.get("document_count", 0), body)
        sheet.write(row, 8, question.get("priority", "normal"), body)
        sheet.write(
            row, 9, str(question.get("what_we_think_these_are") or "").replace("_", " "), body
        )
        sheet.write(row, 10, question.get("fields_in_question", ""), body)
        sheet.write(row, 11, example.get("document_id", ""), body)
        sheet.write(row, 12, example.get("field", ""), body)
        options = question.get("answer_options") or []
        if answerable and options:
            # A dropdown is what makes a returned workbook machine-readable. It
            # is deliberately not enforced: "Your notes" stays open for the
            # answer the options did not anticipate.
            sheet.data_validation(row, 3, row, 3, {"validate": "list", "source": list(options)})
    # Freeze through the readings, so scrolling to the wider columns never takes
    # the values being decided off screen.
    sheet.freeze_panes(3, 3)
    sheet.autofilter(2, 0, max(3, 2 + len(payload.get("questions", []))), len(QUESTION_COLUMNS) - 1)


def _all_items(book: xlsxwriter.Workbook, items: list[dict[str, Any]], fmt: dict[str, Any]) -> None:
    """Write the exhaustive queue so the questions can always be traced back."""
    sheet = book.add_worksheet("All items")
    sheet.merge_range(
        0, 0, 0, len(ITEM_COLUMNS) - 1, "Every finding behind the questions", fmt["title"]
    )
    for column, (name, width) in enumerate(ITEM_COLUMNS):
        sheet.set_column(column, column, width)
        sheet.write(2, column, name, fmt["head"])
    for row, item in enumerate(items, start=3):
        for column, (name, _) in enumerate(ITEM_COLUMNS):
            sheet.write(row, column, str(item.get(name, "")), fmt["body"])
    sheet.freeze_panes(3, 0)
    sheet.autofilter(2, 0, max(3, 2 + len(items)), len(ITEM_COLUMNS) - 1)


def _glossary(book: xlsxwriter.Workbook, fmt: dict[str, Any]) -> None:
    """Define every column, so no header has to be guessed at."""
    sheet = book.add_worksheet("How to read this")
    sheet.set_column("A:A", 30)
    sheet.set_column("B:B", 90)
    sheet.merge_range("A1:B1", "What each column means", fmt["title"])
    row = 2
    sheet.write(row, 0, "Questions tab", fmt["head"])
    sheet.write(row, 1, "", fmt["head"])
    for name, _, meaning in QUESTION_COLUMNS:
        row += 1
        sheet.write(row, 0, name, fmt["body"])
        sheet.write(row, 1, meaning, fmt["body"])
    row += 2
    sheet.write(row, 0, "All items tab", fmt["head"])
    sheet.write(row, 1, "", fmt["head"])
    for name, meaning in (
        ("priority", "How urgent the finding is."),
        ("document_id", "Which document it belongs to."),
        ("page_id", "Which retained page it came from."),
        ("field", "The specific value in question."),
        ("reason", "Why it could not be settled automatically."),
        ("review_source", "Which control raised it."),
        ("disposition", "What needs to happen to it."),
    ):
        row += 1
        sheet.write(row, 0, name, fmt["body"])
        sheet.write(row, 1, meaning, fmt["body"])


def write_workbook(
    payload: dict[str, Any],
    items: list[dict[str, Any]],
    output: Path,
    prefill: dict[str, Any] | None = None,
) -> Path:
    """Write the client answering workbook, optionally carrying prior answers in."""
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing workbook: {output}")
    book = xlsxwriter.Workbook(
        str(output), {"strings_to_formulas": False, "strings_to_urls": False}
    )
    fmt = _formats(book)
    _start_here(book, payload, fmt)
    _questions(book, payload, fmt, prefill)
    _all_items(book, items, fmt)
    _glossary(book, fmt)
    book.close()
    return output
