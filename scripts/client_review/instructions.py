"""Render the client instruction PDF: one question per page, beside its evidence.

The workbook is where a client records answers. This document is where they
understand what is being asked -- each question in plain language, followed by
the actual page the question came from, so the answer is checked against the
document rather than recalled.

The PDF is built directly rather than through a rendering dependency, matching
how this repository already writes OOXML. Two consequences are deliberate: the
text is real text, so a reviewer can search and copy it and a screen reader can
read it, and the page image is embedded as its original JPEG rather than being
decoded and re-encoded, so a fifty-page pack stays small enough to email.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pikepdf
from PIL import Image

PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0
MARGIN = 54.0
BODY_SIZE = 10.5
HEADING_SIZE = 15.0
LABEL_SIZE = 9.0
LINE_GAP = 1.35
# Helvetica advance widths per 1000 em for the printable ASCII range. Wrapping
# from real widths rather than a character count is what keeps a proportional
# font from overflowing the margin on a line of capitals.
HELVETICA_WIDTHS = {
    " ": 278,
    "!": 278,
    '"': 355,
    "#": 556,
    "$": 556,
    "%": 889,
    "&": 667,
    "'": 191,
    "(": 333,
    ")": 333,
    "*": 389,
    "+": 584,
    ",": 278,
    "-": 333,
    ".": 278,
    "/": 278,
    "0": 556,
    "1": 556,
    "2": 556,
    "3": 556,
    "4": 556,
    "5": 556,
    "6": 556,
    "7": 556,
    "8": 556,
    "9": 556,
    ":": 278,
    ";": 278,
    "<": 584,
    "=": 584,
    ">": 584,
    "?": 556,
    "@": 1015,
    "A": 667,
    "B": 667,
    "C": 722,
    "D": 722,
    "E": 667,
    "F": 611,
    "G": 778,
    "H": 722,
    "I": 278,
    "J": 500,
    "K": 667,
    "L": 556,
    "M": 833,
    "N": 722,
    "O": 778,
    "P": 667,
    "Q": 778,
    "R": 722,
    "S": 667,
    "T": 611,
    "U": 722,
    "V": 667,
    "W": 944,
    "X": 667,
    "Y": 667,
    "Z": 611,
    "[": 278,
    "\\": 278,
    "]": 278,
    "^": 469,
    "_": 556,
    "`": 333,
    "a": 556,
    "b": 556,
    "c": 500,
    "d": 556,
    "e": 556,
    "f": 278,
    "g": 556,
    "h": 556,
    "i": 222,
    "j": 222,
    "k": 500,
    "l": 222,
    "m": 833,
    "n": 556,
    "o": 556,
    "p": 556,
    "q": 556,
    "r": 333,
    "s": 500,
    "t": 278,
    "u": 556,
    "v": 500,
    "w": 722,
    "x": 500,
    "y": 500,
    "z": 500,
    "{": 334,
    "|": 260,
    "}": 334,
    "~": 584,
}
DEFAULT_WIDTH = 556
BOLD_FACTOR = 1.08


def text_width(text: str, size: float, bold: bool = False) -> float:
    """Return the rendered width of a string in points."""
    total = sum(HELVETICA_WIDTHS.get(char, DEFAULT_WIDTH) for char in text)
    width = total * size / 1000.0
    return width * BOLD_FACTOR if bold else width


def wrap(text: str, size: float, max_width: float, bold: bool = False) -> list[str]:
    """Break text into lines that fit the measured width.

    A single word wider than the column is emitted on its own line rather than
    split: a broken identifier is worse to read than a line that runs long, and
    the identifiers here are the ones a reviewer looks up.
    """
    lines: list[str] = []
    current = ""
    for word in str(text).split():
        candidate = f"{current} {word}".strip()
        if current and text_width(candidate, size, bold) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def escape(text: str) -> bytes:
    """Encode a string as a PDF literal, dropping what Helvetica cannot show."""
    encoded = str(text).encode("cp1252", "replace").decode("cp1252")
    out = encoded.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return out.encode("cp1252", "replace")


class Page:
    """Accumulate one page's content stream from the top margin downward."""

    def __init__(self, page_width: float = PAGE_WIDTH, page_height: float = PAGE_HEIGHT) -> None:
        self.page_width = page_width
        self.page_height = page_height
        self.parts: list[bytes] = []
        self.cursor = page_height - MARGIN
        self.image: dict[str, Any] | None = None

    @property
    def width(self) -> float:
        """Return the usable text column width."""
        return self.page_width - 2 * MARGIN

    def space(self, amount: float) -> None:
        """Advance the cursor without drawing."""
        self.cursor -= amount

    def rule(self, colour: str = "0.80 0.80 0.80") -> None:
        """Draw a full-width hairline separator."""
        self.parts.append(
            f"q {colour} RG 0.6 w {MARGIN} {self.cursor:.2f} m "
            f"{self.page_width - MARGIN} {self.cursor:.2f} l S Q\n".encode("cp1252")
        )
        self.cursor -= 8

    def text(
        self,
        value: str,
        size: float = BODY_SIZE,
        bold: bool = False,
        colour: str = "0 0 0",
        indent: float = 0.0,
    ) -> None:
        """Draw wrapped text and advance the cursor past it."""
        font = "/F2" if bold else "/F1"
        for line in wrap(value, size, self.width - indent, bold):
            self.cursor -= size * LINE_GAP
            body = escape(line)
            self.parts.append(
                b"BT "
                + colour.encode()
                + b" rg "
                + font.encode()
                + b" "
                + f"{size} Tf 1 0 0 1 {MARGIN + indent:.2f} {self.cursor:.2f} Tm ".encode()
                + b"("
                + body
                + b") Tj ET\n"
            )

    def label(self, value: str) -> None:
        """Draw a small uppercase section label."""
        self.space(6)
        self.text(value.upper(), size=LABEL_SIZE, bold=True, colour="0.35 0.35 0.35")
        self.space(1)

    def place_image(self, path: Path, caption: str) -> None:
        """Fit the rendered page image into whatever vertical space remains."""
        with Image.open(path) as handle:
            pixel_width, pixel_height = handle.size
        available_height = self.cursor - MARGIN - 14
        if available_height <= 40 or pixel_width <= 0 or pixel_height <= 0:
            return
        scale = min(self.width / pixel_width, available_height / pixel_height)
        draw_width = pixel_width * scale
        draw_height = pixel_height * scale
        if caption:
            self.text(caption, size=LABEL_SIZE, colour="0.35 0.35 0.35")
            self.space(4)
        bottom = self.cursor - draw_height
        left = MARGIN + (self.width - draw_width) / 2
        self.parts.append(
            f"q {draw_width:.2f} 0 0 {draw_height:.2f} {left:.2f} {bottom:.2f} cm /Im1 Do Q\n".encode(
                "cp1252"
            )
        )
        self.parts.append(
            f"q 0.75 0.75 0.75 RG 0.5 w {left:.2f} {bottom:.2f} "
            f"{draw_width:.2f} {draw_height:.2f} re S Q\n".encode("cp1252")
        )
        self.cursor = bottom
        self.image = {"path": Path(path), "width": pixel_width, "height": pixel_height}

    def stream(self) -> bytes:
        """Return the finished content stream."""
        return b"".join(self.parts)


def cover_page(payload: dict[str, Any]) -> Page:
    """Build the page explaining what this pack is and how to answer it."""
    summary = payload.get("summary", {})
    page = Page()
    page.text("Client review pack", size=HEADING_SIZE + 4, bold=True)
    page.space(4)
    page.text(
        payload.get("pack_title", "Questions we need answered to finish your dataset"),
        size=BODY_SIZE + 1,
        colour="0.30 0.30 0.30",
    )
    page.space(10)
    page.rule()
    page.label("What this is")
    page.text(
        "We read every document twice and checked the results. Most of it came "
        "through cleanly. What is left could not be settled from the documents "
        "alone, so we need your decision on it."
    )
    page.space(6)
    page.text(
        "We have grouped repeated problems together, so you are answering a small "
        "number of questions rather than the same question many times over."
    )
    page.label("How much this covers")
    for line in (
        f"Questions to answer: {summary.get('answerable_question_count', 0)}",
        f"Individual items they resolve: {summary.get('items_covered', 0)} "
        f"of {summary.get('queue_item_count', 0)} ({summary.get('coverage_pct', 0)}%)",
        f"Average items informed per question: {summary.get('items_per_question', 0)}",
        f"Questions whose answer closes the group outright: "
        f"{summary.get('batch_clearing_question_count', 0)}",
        f"Questions whose answer is applied and then re-checked item by item: "
        f"{summary.get('individually_rechecked_question_count', 0)}",
    ):
        page.text(f"•  {line}")
    page.label("How to answer")
    for step in (
        "Open the workbook that came with this pack and go to the Questions tab.",
        "Work down the list. Each row matches a numbered question in this document.",
        "Read the question here, look at the page shown beneath it, then record "
        "your answer in the workbook's Answer column.",
        "If a question offers options, pick one. If none fits, write what is "
        "correct in your own words in the Notes column.",
        "Send the workbook back. You do not need to return this document.",
    ):
        page.text(f"•  {step}")
    page.label("What we will not do")
    page.text(
        "We will not choose an answer on your behalf, and we will not close an "
        "item because it looks likely. Anything you do not answer stays open and "
        "visible rather than being quietly resolved."
    )
    return page


def question_page(question: dict[str, Any]) -> Page:
    """Build one question page, followed by the page it came from."""
    page = Page()
    page.text(
        f"{question['question_id']}   {question['headline']}",
        size=HEADING_SIZE,
        bold=True,
    )
    page.space(3)
    scope = (
        f"Answering this settles {question['resolves_item_count']} item(s) "
        f"across {question['document_count']} document(s)."
    )
    page.text(scope, size=LABEL_SIZE, colour="0.35 0.35 0.35")
    if not question.get("batch_clear_permitted", True) and question.get(
        "client_answer_permitted", True
    ):
        page.space(2)
        page.text(
            "Findings of this kind are never cleared in bulk - your answer is "
            "applied and each item re-checked individually.",
            size=LABEL_SIZE,
            bold=True,
            colour="0.55 0.35 0.05",
        )
    page.space(6)
    page.rule()
    # Name what the question is about before describing it. The pack asked the
    # client to confirm "these documents" and "the columns" while naming
    # neither, so the answer had nowhere to start.
    if question.get("what_we_think_these_are"):
        page.label("What we think these are")
        page.text(str(question["what_we_think_these_are"]).replace("_", " "))
    if question.get("fields_in_question"):
        page.label("Fields and columns this is about")
        page.text(question["fields_in_question"])
    # The values themselves, so the page and the workbook row agree on what is
    # being decided rather than the client having to hold one in their head.
    if question.get("what_we_read"):
        page.label("What we read")
        page.text(str(question["what_we_read"]))
    named = question.get("documents_in_question") or []
    if named:
        total = question.get("documents_in_question_total", len(named))
        page.label("Documents")
        page.text(", ".join(str(name) for name in named))
        if total > len(named):
            page.text(
                f"and {total - len(named)} more. The full list is on the Queue tab of the "
                "workbook, filtered by this question number.",
                size=LABEL_SIZE,
                colour="0.35 0.35 0.35",
            )
    page.label("What we found")
    page.text(question["what_we_found"])
    page.label("Why it matters")
    page.text(question["why_it_matters"])
    page.label("What we need from you")
    page.text(question["what_we_need"])
    options = question.get("answer_options") or []
    if options:
        page.label("Choose one")
        for index, option in enumerate(options, 1):
            page.text(f"{index}.  {option}", indent=10)
    example = question.get("example", {})
    page.space(8)
    page.rule()
    identity = ", ".join(
        part
        for part in (
            f"Document {example.get('document_id')}" if example.get("document_id") else "",
            f"field {example.get('field')}" if example.get("field") else "",
        )
        if part
    )
    if identity:
        page.label("Example")
        page.text(identity, size=LABEL_SIZE, colour="0.35 0.35 0.35")
    if not example.get("page_image"):
        page.space(4)
        page.text(
            "No source page could be attached to this question. The reason is "
            "recorded in the pack's exception file.",
            size=LABEL_SIZE,
            colour="0.45 0.45 0.45",
        )
    else:
        page.space(4)
        page.text(
            "The page this question came from follows overleaf.",
            size=LABEL_SIZE,
            colour="0.35 0.35 0.35",
        )
    return page


def example_pages(question: dict[str, Any]) -> list[Page]:
    """Give every worked example its own sheet.

    One example was not enough. A question covering 285 documents showed the
    group's first page, that page happened to be blank, and the client answered
    "Blank Page. Can be ignored." for all of them -- while the other 284 carried
    a median of 91 populated fields. Several pages spread across the group let a
    client see for themselves whether one answer fits.
    """
    from client_review.evidence import each_example

    pages = []
    examples = each_example(question)
    for number, example in enumerate(examples, 1):
        # Numbered so a client can see at a glance that a question covers more
        # than the one page in front of them.
        label = (
            f"{question.get('question_id', '')} (example {number} of {len(examples)})"
            if len(examples) > 1
            else question.get("question_id", "")
        )
        page = example_page({"question_id": label, "example": example})
        if page is not None:
            pages.append(page)
    return pages


def example_page(question: dict[str, Any]) -> Page | None:
    """Give the source page a full sheet of its own.

    Fitted into whatever space a question's text left over, a scanned commission
    statement is a grey rectangle: the reviewer cannot read the column they are
    being asked about, which defeats the purpose of attaching it. A page to
    itself is the difference between evidence and decoration.
    """
    example = question.get("example", {})
    image_path = example.get("page_image")
    if not image_path or not Path(image_path).is_file():
        return None
    with Image.open(image_path) as handle:
        pixel_width, pixel_height = handle.size
    # A landscape scan shown on a portrait sheet wastes half the paper and
    # halves the type size the reviewer has to read. Turn the sheet instead.
    landscape = pixel_width > pixel_height
    page = Page(
        page_width=PAGE_HEIGHT if landscape else PAGE_WIDTH,
        page_height=PAGE_WIDTH if landscape else PAGE_HEIGHT,
    )
    page.text(
        f"{question['question_id']} - source page",
        size=BODY_SIZE + 1,
        bold=True,
    )
    identity = " - ".join(
        part
        for part in (
            example.get("document_id") or "",
            f"field {example.get('field')}" if example.get("field") else "",
        )
        if part
    )
    if identity:
        page.text(identity, size=LABEL_SIZE, colour="0.35 0.35 0.35")
    page.space(4)
    page.place_image(Path(image_path), "")
    return page


def write_instructions_pdf(payload: dict[str, Any], output: Path) -> Path:
    """Write the instruction PDF for a question pack."""
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing instruction PDF: {output}")
    pages = [cover_page(payload)]
    for question in payload.get("questions", []):
        pages.append(question_page(question))
        pages.extend(example_pages(question))
    pdf = pikepdf.Pdf.new()
    regular = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica,
            Encoding=pikepdf.Name.WinAnsiEncoding,
        )
    )
    bold = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name("/Helvetica-Bold"),
            Encoding=pikepdf.Name.WinAnsiEncoding,
        )
    )
    for page in pages:
        resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=regular, F2=bold))
        if page.image is not None:
            resources[pikepdf.Name.XObject] = pikepdf.Dictionary(
                Im1=pikepdf.Stream(
                    pdf,
                    page.image["path"].read_bytes(),
                    Type=pikepdf.Name.XObject,
                    Subtype=pikepdf.Name.Image,
                    Width=page.image["width"],
                    Height=page.image["height"],
                    ColorSpace=pikepdf.Name.DeviceRGB,
                    BitsPerComponent=8,
                    Filter=pikepdf.Name.DCTDecode,
                )
            )
        pdf.pages.append(
            pikepdf.Page(
                pdf.make_indirect(
                    pikepdf.Dictionary(
                        Type=pikepdf.Name.Page,
                        MediaBox=[0, 0, page.page_width, page.page_height],
                        Resources=resources,
                        Contents=pikepdf.Stream(pdf, page.stream()),
                    )
                )
            )
        )
    pdf.save(output)
    return output
