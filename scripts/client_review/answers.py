"""Read a client's answers back out of a returned review-lane workbook.

The lane asks questions and the instruction PDF tells the client to send the
workbook back. Without this, that sentence was a promise nothing kept: the pack
was a one-way document, and an operator receiving a completed workbook had to
retype its answers.

What comes back is untrusted. It arrived by email, it was opened in whatever
spreadsheet the client uses, and it may have been edited anywhere. So it is
validated against the immutable issued copy before a single answer is read: the
same archive bounds the package importer applies, the same question rows in the
same order, and every non-answer cell unchanged. A workbook that fails any of
those is refused rather than partially trusted.

Nothing here applies anything. An answer is a client decision proposal; it
reaches the dataset only through the documented authorization path, after which
the affected controls are rerun. An answer to a question that rule 7 protects is
recorded as informing an item-by-item recheck, never as clearing its group.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from client_review.package import _validate_workbook_archive, _xlsx_cell_values

ANSWER_SHEET = "Questions"
QUESTION_COLUMN = "Question"
# The only two cells a client is invited to change. Everything else is fixed and
# a difference in it means the workbook is not the one that was issued.
EDITABLE_HEADERS = {"Your answer", "Your notes"}
ARTIFACT_TYPE = "client_review_answers_v1"


def _sheet_rows(workbook: Path) -> tuple[list[str], list[list[str]]]:
    """Return the answering sheet's header and body rows."""
    values = _xlsx_cell_values(Path(workbook), ANSWER_SHEET)
    header_index = next(
        (index for index, row in enumerate(values) if row and row[0].strip() == QUESTION_COLUMN),
        None,
    )
    if header_index is None:
        raise ValueError(f"workbook has no {ANSWER_SHEET} header row")
    header = [cell.strip() for cell in values[header_index]]
    rows = [row for row in values[header_index + 1 :] if any(cell.strip() for cell in row)]
    return header, rows


def _aligned(header: list[str], row: list[str]) -> dict[str, str]:
    """Pair a row with its header, tolerating trailing cells a client deleted."""
    padded = list(row) + [""] * (len(header) - len(row))
    return {name: padded[index].strip() for index, name in enumerate(header)}


def carry_forward(previous: dict[str, Any], questions: list[dict[str, Any]]) -> dict[str, Any]:
    """Match a prior round's answers onto a rebuilt pack, by group identity.

    Question numbers are positional and do not survive a rebuild -- correcting
    how findings are grouped renumbers everything after it. A group is identified
    by the pair the grouping itself indexes on, its template family and its
    finding family, so an answer follows the question it was actually about.

    An answer is carried, never applied. A rebuilt pack exists because something
    about the questions was wrong, and a carried answer may have been given to a
    question that has since changed. Each one records where it came from so a
    reviewer can see it was not asked afresh.
    """
    by_group = {}
    for answer in previous.get("answers", []):
        key = (answer.get("template_family"), answer.get("finding_family"))
        if all(key) and (
            str(answer.get("answer") or "").strip() or str(answer.get("note") or "").strip()
        ):
            by_group[key] = answer
    carried, unmatched = {}, list(by_group)
    for question in questions:
        key = (question.get("template_family"), question.get("finding_family"))
        answer = by_group.get(key)
        if answer is None:
            continue
        unmatched = [item for item in unmatched if item != key]
        carried[question["question_id"]] = {
            "answer": str(answer.get("answer") or ""),
            "note": str(answer.get("note") or ""),
            "carried_from_question_id": answer.get("question_id"),
            "template_family": key[0],
            "finding_family": key[1],
        }
    return {
        "carried": carried,
        "questions_prefilled": len(carried),
        "previous_answers_with_no_matching_question": [
            {"template_family": key[0], "finding_family": key[1]} for key in unmatched
        ],
    }


def read_answers(returned: Path, issued: Path, pack: dict[str, Any]) -> dict[str, Any]:
    """Validate a returned workbook and extract every answer it carries.

    An answer that matches none of a question's offered options is not discarded
    and not guessed at: it is retained as written and reported unmatched, because
    the option list is this pipeline's vocabulary rather than the client's.
    """
    returned, issued = Path(returned), Path(issued)
    if returned.resolve() == issued.resolve():
        raise ValueError("returned workbook must be separate from the immutable issued workbook")
    _validate_workbook_archive(returned)
    _validate_workbook_archive(issued)

    returned_header, returned_rows = _sheet_rows(returned)
    issued_header, issued_rows = _sheet_rows(issued)
    if returned_header != issued_header:
        raise ValueError("returned workbook changed the Questions header")
    if len(returned_rows) != len(issued_rows):
        raise ValueError("returned workbook has a different number of question rows")

    questions = {
        str(question.get("question_id")): question for question in pack.get("questions", [])
    }
    # What the pack pre-filled, so an unchanged cell can be told apart from a
    # placeholder the client never touched.
    carried = (pack.get("carried_answers") or {}).get("carried") or {}
    answers: list[dict[str, Any]] = []
    unanswered: list[str] = []
    unmatched: list[dict[str, str]] = []

    for issued_row, returned_row in zip(issued_rows, returned_rows, strict=True):
        issued_cells = _aligned(issued_header, issued_row)
        cells = _aligned(returned_header, returned_row)
        question_id = issued_cells.get(QUESTION_COLUMN, "")
        if cells.get(QUESTION_COLUMN, "") != question_id:
            raise ValueError("returned workbook reordered or renamed a question row")
        for name in issued_header:
            if name in EDITABLE_HEADERS:
                continue
            if cells.get(name, "") != issued_cells.get(name, ""):
                raise ValueError(
                    f"returned workbook changed a fixed cell for {question_id}: {name}"
                )
        question = questions.get(question_id)
        if question is None:
            raise ValueError(
                f"returned workbook names a question absent from the pack: {question_id}"
            )

        # An answer is what the client *changed*. The workbook pre-fills the
        # answer cell of a question needing no reply with "No answer needed", and
        # reading any non-empty cell as a reply turned that placeholder into a
        # decision the client never made. Comparing against the issued copy also
        # covers anything else a future workbook pre-fills.
        # A cell the client left exactly as issued is only an absent answer when
        # what was issued was a placeholder. Once `--prefill-from` carries a
        # previous round's answers into these same cells, an unchanged cell is a
        # real answer the client reviewed and let stand -- and discarding it
        # reported 21 of 25 answers as unanswered on the second commission
        # round, keeping only the two questions that had nothing pre-filled.
        answer = cells.get("Your answer", "")
        note = cells.get("Your notes", "")
        prior = carried.get(question_id) or {}
        reaffirmed = False
        if answer == issued_cells.get("Your answer", ""):
            if answer and answer == str(prior.get("answer") or ""):
                reaffirmed = True
            else:
                answer = ""
        if note == issued_cells.get("Your notes", ""):
            if note and note == str(prior.get("note") or ""):
                reaffirmed = True
            else:
                note = ""
        if not answer and not note:
            unanswered.append(question_id)
            continue
        options = [str(option) for option in question.get("answer_options") or []]
        matched = answer in options if answer else False
        if answer and options and not matched:
            unmatched.append({"question_id": question_id, "answer": answer})
        answers.append(
            {
                "question_id": question_id,
                "group_id": question.get("group_id"),
                "answer": answer,
                "note": note,
                "matched_offered_option": matched,
                # True when the client reviewed a carried answer and let it
                # stand. That is a decision, and a quieter one than a fresh
                # answer, so it is recorded rather than inferred.
                "reaffirmed_carried_answer": reaffirmed,
                "resolves_item_count": question.get("resolves_item_count", 0),
                "source_item_indexes": question.get("source_item_indexes", []),
                # Rule 7: an answer to a protected question informs an
                # item-by-item recheck. It never closes the group.
                "batch_clear_permitted": bool(question.get("batch_clear_permitted")),
                "application": (
                    "applies_to_group"
                    if question.get("batch_clear_permitted")
                    else "informs_item_by_item_recheck"
                ),
                # The `client_decision_compile.py` choice this answer
                # constitutes, carried from the question. That command accepts
                # four fixed choices and the pack offered clients thirty-eight
                # of its own, sharing no string with them, so every returned
                # answer compiled as non-actionable and no client answer could
                # reach an authorized change at all.
                "decision_choice": question.get("decision_choice", ""),
                # The group this answer is about. Question numbers are
                # positional and do not survive a rebuild; this pair does, and
                # `carry_forward` matches on it.
                "template_family": question.get("template_family", ""),
                "finding_family": question.get("finding_family", ""),
                "decision_source": "client",
                "client_approval_required": False,
                "authorized_change": False,
            }
        )

    covered = sum(item["resolves_item_count"] for item in answers)
    return {
        "artifact_type": ARTIFACT_TYPE,
        "generated_at": datetime.now(UTC).isoformat(),
        "issued_workbook": issued.name,
        "returned_workbook": returned.name,
        "summary": {
            "questions_in_pack": len(issued_rows),
            "answered": len(answers),
            "unanswered": len(unanswered),
            "answers_not_matching_an_offered_option": len(unmatched),
            "answers_reaffirmed_from_a_previous_round": sum(
                1 for answer in answers if answer["reaffirmed_carried_answer"]
            ),
            "items_the_answers_speak_to": covered,
            "answers_that_close_their_group": sum(
                1 for item in answers if item["batch_clear_permitted"]
            ),
            "answers_requiring_item_by_item_recheck": sum(
                1 for item in answers if not item["batch_clear_permitted"]
            ),
        },
        "answers": answers,
        "unanswered_questions": unanswered,
        "answers_not_matching_an_offered_option": unmatched,
        "policy": {
            "decision_mode": "client_decision_proposal",
            "applied": False,
            "authorization_required_before_any_change": True,
            "affected_controls_must_be_rerun": True,
        },
    }


def write_answers(returned: Path, issued: Path, pack_path: Path, output: Path) -> dict[str, Any]:
    """Write the client answer artifact for one returned workbook."""
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing answer artifact: {output}")
    pack = json.loads(Path(pack_path).read_text())
    if pack.get("schema_version") != "client_review_pack_v1":
        raise ValueError("pack must be the review_pack.json written beside the issued workbook")
    result = read_answers(returned, issued, pack)
    output.write_text(json.dumps(result, indent=2) + "\n")
    return result
