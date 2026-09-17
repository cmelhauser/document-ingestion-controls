"""The one path from blocked controls to a review pack a client can answer.

Before this lane, producing a client review meant chaining four commands with
different inputs and conventions, at one point in the pipeline. Every other block
either went to the client as raw exception rows or waited until delivery. The
result was inconsistent: what a client received depended on which control
stopped and who assembled the request.

This runs the whole path -- queue, group, question, evidence, render -- from
whatever artifacts a blocked control produced, at any point in the pipeline, and
emits the same three things every time: a pack manifest, a workbook to answer in,
and an instruction PDF showing each question beside the page it came from.

The lane resolves nothing. It refuses to run when no configured threshold is met,
it never edits the queue behind a question, and a pack is a request for decisions
that remains unanswered until a client returns it through the documented import
path.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from client_review import evidence, instructions, questions, thresholds, workbook
from client_review.answers import carry_forward
from client_review.grouping import build_groups
from client_review.queue import (
    consolidate,
    governed_pages,
    operator_dispositions,
    resolutions,
    vote_settlements,
)

PACK_SCHEMA_VERSION = "client_review_pack_v1"


def timestamp() -> str:
    """Return the UTC generation time recorded in every pack."""
    return datetime.now(UTC).isoformat()


def build_pack(
    artifacts: list[Path],
    out_dir: Path,
    consensus: Path | None = None,
    classifications: list[Path] | None = None,
    resolved_by: list[Path] | None = None,
    supersede: list[tuple[Path, Path]] | None = None,
    settled_by_vote: list[tuple[Path, Path]] | None = None,
    dispositions: list[Path] | None = None,
    prefill_from: Path | None = None,
    scan_profile: Path | None = None,
    manifest: Path | None = None,
    max_questions: int | None = None,
    dpi: int = 150,
    max_image_bytes: int = 4_000_000,
    force: bool = False,
    renderer=evidence.render_page,
) -> dict[str, Any]:
    """Assemble one review pack from any set of blocked-control artifacts.

    ``force`` runs the lane even when no threshold is met, which is what an
    operator preparing a deliberate mid-run client conversation needs. It never
    lowers a threshold: the pack records that it was forced, so a reader can tell
    a pack the pipeline asked for from a pack a person asked for.

    ``resolved_by``, ``supersede``, ``settled_by_vote`` and ``dispositions`` are
    the final queue's reconciliations, read by the queue's own functions, so a
    pack built from the gate's inputs retains what the gate retains and refuses
    what it refuses.
    """
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise FileExistsError(f"refusing to overwrite existing review pack: {out_dir}")
    if not artifacts:
        raise ValueError("a review pack requires at least one source artifact")
    # The gate reconciles three ways before it asks (`final_review_queue.py
    # --resolved-by`, `--supersede`, `--settled-by-vote`). This lane builds the
    # same queue by a second path. With none of them a pack issued from it
    # re-asked 1,121 questions the run had already answered; with the first
    # alone, a pack from the commission run's final-queue inputs asked 1,054
    # findings the queue retains: 522 a re-read superseded, 532 a vote settled.
    paths = [Path(path) for path in artifacts]
    accepted = resolutions([Path(path) for path in resolved_by]) if resolved_by else None
    governed = governed_pages(supersede or [], paths)
    settled = vote_settlements(settled_by_vote or [], paths)
    disposed = operator_dispositions(dispositions) if dispositions else None
    items, sources, reconciled = consolidate(paths, accepted, governed, settled, disposed)
    reconciliation = _reconciliation(
        reconciled, resolved_by, supersede, settled_by_vote, dispositions
    )
    # Rule 9: a lane that processed nothing has not produced a clean review.
    if not items:
        if reconciled:
            # Not nothing: every finding was answered, superseded or settled.
            raise ValueError(
                f"all {len(reconciled)} findings in {', '.join(sources)} are retained "
                "as reconciled; there is nothing left to ask"
            )
        raise ValueError(
            "no review items were found in "
            f"{', '.join(sources)}; a pack built from nothing would report a clear "
            "review that nobody performed"
        )
    trigger = thresholds.evaluate(items)
    enabled = trigger["lane_enabled"]
    proceed = force or (enabled and trigger["triggered"])
    out_dir.mkdir(parents=True)
    pack: dict[str, Any] = {
        "schema_version": PACK_SCHEMA_VERSION,
        "generated_at": timestamp(),
        "source_artifacts": sources,
        "trigger": {**trigger, "forced": bool(force), "produced_documents": proceed},
        "decision_mode": "proposal_only",
        "client_approval_required": True,
    }
    if not proceed:
        # Not an error. Below every threshold there is nothing worth interrupting
        # a client for, and the queue keeps the items either way.
        pack["summary"] = {
            "question_count": 0,
            "queue_item_count": len(items),
            "reason": (
                "client review lane disabled"
                if not enabled
                else "no control cleared its configured threshold"
            ),
            **reconciliation,
        }
        (out_dir / "review_pack.json").write_text(json.dumps(pack, indent=2) + "\n")
        return pack

    groups = build_groups(
        {"items": items},
        _consensus_document(consensus),
        *(_consensus_document(path) for path in (classifications or [])),
    )
    uninformative = evidence.uninformative_pages(scan_profile, manifest)
    built = questions.build_questions(groups, items, max_questions, uninformative)
    render_exceptions = evidence.attach_examples(
        built["questions"],
        manifest,
        out_dir / "pages",
        dpi=dpi,
        max_image_bytes=max_image_bytes,
        renderer=renderer,
    )
    pack["summary"] = built["summary"]
    # Findings the run had already answered, superseded or settled, retained
    # rather than asked, and counted as the final queue counts them.
    pack["summary"].update(reconciliation)
    pack["reconciled"] = reconciled
    pack["questions"] = built["questions"]
    pack["deferred_questions"] = built["deferred_questions"]
    pack["groups"] = groups
    pack["queue_items"] = items
    pack["pack_title"] = "Questions we need answered to finish your dataset"

    carried = (
        carry_forward(json.loads(Path(prefill_from).read_text()), built["questions"])
        if prefill_from
        else {
            "carried": {},
            "questions_prefilled": 0,
            "previous_answers_with_no_matching_question": [],
        }
    )
    pack["summary"]["questions_prefilled"] = carried["questions_prefilled"]
    pack["carried_answers"] = carried
    workbook.write_workbook(pack, items, out_dir / "client_review.xlsx", carried["carried"])
    instructions.write_instructions_pdf(pack, out_dir / "client_review_instructions.pdf")
    (out_dir / "review_pack.json").write_text(json.dumps(pack, indent=2) + "\n")
    (out_dir / "review_pack_exceptions.json").write_text(
        json.dumps(
            {"summary": {"count": len(render_exceptions)}, "exceptions": render_exceptions},
            indent=2,
        )
        + "\n"
    )
    pack["exception_count"] = len(render_exceptions)
    return pack


def write_compile_inputs(
    pack_path: Path,
    answers_path: Path,
    issued_workbook: Path,
    returned_workbook: Path,
    consolidation_out: Path,
    decisions_out: Path,
) -> dict[str, Any]:
    """Write the two artifacts `client_decision_compile.py` reads, refusing to clobber.

    This is the join between the review lane and the authorization chain. It
    authorizes nothing itself: the compiler still validates every group and
    decision, and an operator still signs.
    """
    from client_review import compile_inputs

    consolidation_out, decisions_out = Path(consolidation_out), Path(decisions_out)
    for destination in (consolidation_out, decisions_out):
        if destination.exists():
            raise FileExistsError(f"refusing to overwrite existing artifact: {destination}")
    pack = json.loads(Path(pack_path).read_text())
    answers = json.loads(Path(answers_path).read_text())
    consolidation = compile_inputs.consolidation_from_pack(pack)
    decisions = compile_inputs.decisions_from_answers(
        pack, answers, consolidation, Path(issued_workbook), Path(returned_workbook)
    )
    consolidation_out.parent.mkdir(parents=True, exist_ok=True)
    decisions_out.parent.mkdir(parents=True, exist_ok=True)
    consolidation_out.write_text(json.dumps(consolidation, indent=2) + "\n")
    decisions_out.write_text(json.dumps(decisions, indent=2) + "\n")
    return decisions


def _reconciliation(
    reconciled: list[dict[str, Any]],
    resolved_by: list[Path] | None,
    supersede: list[tuple[Path, Path]] | None,
    settled_by_vote: list[tuple[Path, Path]] | None,
    dispositions: list[Path] | None = None,
) -> dict[str, Any]:
    """Count what the pack retained instead of asking, under the queue's names.

    ``final_review_queue.py`` counts each reconciliation separately, and the pack
    reports the same counts, so a pack and the queue can be compared directly.
    """

    def count(disposition: str) -> int:
        return sum(1 for entry in reconciled if entry["disposition"] == disposition)

    return {
        "reconciled_items": count("already_resolved"),
        "resolution_artifacts": [Path(path).name for path in (resolved_by or [])],
        "superseded_items": count("superseded_by_re_read"),
        "superseded_by": [[str(artifact), str(manifest)] for artifact, manifest in supersede or []],
        "settled_by_vote_items": count("settled_by_vendor_vote"),
        "settled_by_vote": [[str(vote), str(artifact)] for vote, artifact in settled_by_vote or []],
        "dispositioned_items": count("dispositioned_by_operator"),
        "disposition_artifacts": [Path(path).name for path in (dispositions or [])],
    }


def _consensus_document(consensus: Path | None) -> dict[str, Any]:
    """Load consensus output for document-family labelling, or stand in empty.

    Grouping reads document types from here to label families. Without it every
    family falls back to the conservative unclassified label, which is a coarser
    pack rather than a wrong one -- so a missing consensus artifact is allowed
    and simply produces broader groups.

    The extraction consensus is not the only artifact that answers this. It
    writes ``"unknown"`` wherever its lanes could not agree a type, which was
    691 of 718 documents on the commission run, and the classification consensus
    had accepted 686 of those same documents on two vendors agreeing. Pass both:
    classification artifacts are read afterwards and supply what extraction
    declined to claim.
    """
    if consensus is None:
        return {"documents": []}
    data = json.loads(Path(consensus).read_text())
    return data if isinstance(data, dict) else {"documents": []}
