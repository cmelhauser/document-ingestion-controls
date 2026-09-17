#!/usr/bin/env python3
"""Build a client review pack from any blocked control, at any point in a run.

Point it at whatever exception or proposal artifacts a control produced. It
consolidates them into the exhaustive queue, groups repeated causes, reduces
those to the smallest set of questions that settles them, attaches the source
page behind each question, and writes a workbook to answer in and an instruction
PDF to answer from.

The lane produces documents only when a control clears its configured threshold,
so it can be run after every stage without generating a pack nobody needed. Use
``--force`` for a deliberate mid-run client conversation; the pack records that
it was forced.

``read-answers`` closes the loop: it validates a returned workbook against the
immutable issued copy and extracts the client's answers as decision proposals.
Nothing is applied by either verb.
"""

import argparse
import sys
from pathlib import Path

from cli_help import apply_shared_help
from client_review.answers import write_answers
from client_review.lane import build_pack, write_compile_inputs
from runtime_config import env_int, load_project_env


def main(argv=None):
    """Write one review pack and report what it asks for."""
    load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    build = sub.add_parser("build", help="Build a review pack from blocked-control artifacts.")
    read = sub.add_parser("read-answers", help="Extract a returned workbook's answers.")
    read.add_argument("workbook", type=Path, help="Returned client workbook. Untrusted input.")
    read.add_argument(
        "--issued-workbook",
        type=Path,
        required=True,
        help="The immutable workbook that was issued; the returned copy is validated against it.",
    )
    read.add_argument(
        "--pack",
        type=Path,
        required=True,
        help="review_pack.json written beside the issued workbook.",
    )
    read.add_argument(
        "--out",
        type=Path,
        required=True,
        help="New client answer artifact; an existing path is refused.",
    )
    build.add_argument(
        "artifacts",
        nargs="+",
        help="Exception, proposal, or validation JSON artifacts from any blocked control.",
    )
    build.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="New directory for the pack; an existing directory is refused.",
    )
    build.add_argument(
        "--consensus",
        type=Path,
        help="Consensus output used only to label document families; groups are broader without it.",
    )
    build.add_argument(
        "--classifications",
        type=Path,
        action="append",
        help=(
            "Repeatable classification_consensus_v1 artifact. The extraction consensus writes "
            "'unknown' wherever its lanes could not agree a document type; without this the "
            "pack labels those documents unclassified even where classification accepted a type."
        ),
    )
    build.add_argument(
        "--resolved-by",
        type=Path,
        action="append",
        help=(
            "Repeatable accepted-classification artifact, as final_review_queue.py takes. A "
            "document_type proposal for a document that control already accepted, naming the same "
            "type, is retained as reconciled instead of being asked again."
        ),
    )
    build.add_argument(
        "--supersede",
        nargs=2,
        action="append",
        type=Path,
        metavar=("ARTIFACT", "MANIFEST"),
        help=(
            "Repeatable, as final_review_queue.py takes it. Findings in the input named ARTIFACT "
            "(by file name) on the pages a subset re-read's MANIFEST lists are retained as "
            "superseded rather than asked, because the record carries the re-read's readings."
        ),
    )
    build.add_argument(
        "--settled-by-vote",
        nargs=2,
        action="append",
        type=Path,
        metavar=("VOTE", "ARTIFACT"),
        help=(
            "Repeatable, as final_review_queue.py takes it. Findings in the input named ARTIFACT "
            "(by file name) on a field the multi_engine_vote.py artifact VOTE settled are "
            "retained as settled_by_vendor_vote rather than asked."
        ),
    )
    build.add_argument(
        "--dispositions",
        type=Path,
        action="append",
        metavar="ARTIFACT",
        help=(
            "Repeatable, as final_review_queue.py takes it. An operator-authorized "
            "operator_item_dispositions_v1 artifact; each item it names by review_item_id is "
            "retained as dispositioned_by_operator rather than asked."
        ),
    )
    build.add_argument(
        "--prefill-from",
        type=Path,
        help=(
            "A prior round's client answer artifact. Matching questions are pre-filled with what "
            "the client already said, matched by group rather than question number, so a rebuilt "
            "pack is a review of their answers rather than a blank form to fill twice."
        ),
    )
    build.add_argument(
        "--scan-profile",
        type=Path,
        help=(
            "Scan profile from scan_profile.py. Pages it flags near_blank are kept out of a "
            "question's worked examples: a client shown a blank page and asked about a layout "
            "answers about the blank page, and is right to."
        ),
    )
    build.add_argument(
        "--manifest",
        type=Path,
        help="Intake manifest used to attach the source page behind each question.",
    )
    build.add_argument(
        "--max-questions",
        type=int,
        default=env_int("CLIENT_REVIEW_LANE_MAX_QUESTIONS", 25),
        help="Largest number of questions to put to the client; the rest are retained as deferred.",
    )
    build.add_argument(
        "--render-dpi",
        type=int,
        default=env_int("CLIENT_REVIEW_LANE_RENDER_DPI", 150),
        help="Resolution each example page is rendered at before embedding.",
    )
    build.add_argument(
        "--max-image-bytes",
        type=int,
        default=env_int("CLIENT_REVIEW_LANE_MAX_IMAGE_BYTES", 4_000_000),
        help="Per-page render ceiling. An oversized render is an exception, never a downscaled guess.",
    )
    build.add_argument(
        "--force",
        action="store_true",
        help="Build the pack even when no control cleared its threshold; recorded in the pack.",
    )
    build.add_argument("--quiet", action="store_true")

    compile_inputs = sub.add_parser(
        "compile-inputs",
        help=(
            "Build the two artifacts client_decision_compile.py expects from a lane pack and "
            "its returned answers. Without this a lane answer cannot reach an authorized change."
        ),
    )
    compile_inputs.add_argument(
        "--pack", type=Path, required=True, help="review_pack.json from build."
    )
    compile_inputs.add_argument(
        "--answers", type=Path, required=True, help="Client answer artifact from read-answers."
    )
    compile_inputs.add_argument(
        "--issued-workbook",
        type=Path,
        required=True,
        help="The immutable workbook that was issued.",
    )
    compile_inputs.add_argument(
        "--returned-workbook", type=Path, required=True, help="The returned client workbook."
    )
    compile_inputs.add_argument(
        "--consolidation-out", type=Path, required=True, help="New consolidation-shaped artifact."
    )
    compile_inputs.add_argument(
        "--decisions-out", type=Path, required=True, help="New workbook-import proposal."
    )
    compile_inputs.add_argument("--quiet", action="store_true")
    read.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args(argv)
    if args.command is None:
        parser.error("a subcommand is required: build, read-answers or compile-inputs")

    if args.command == "compile-inputs":
        try:
            result = write_compile_inputs(
                args.pack,
                args.answers,
                args.issued_workbook,
                args.returned_workbook,
                args.consolidation_out,
                args.decisions_out,
            )
        except (OSError, ValueError, FileExistsError) as exc:
            sys.exit(f"Client review lane failed: {exc}")
        if not args.quiet:
            print(
                f"Compile inputs: {result['answered_group_count']} answered of "
                f"{result['decision_count']} decision groups"
            )
            print(f"  consolidation -> {args.consolidation_out}")
            print(f"  decisions     -> {args.decisions_out}")
            print("  nothing is authorized by writing these; run client_decision_compile.py next")
        return 0

    if args.command == "read-answers":
        try:
            result = write_answers(args.workbook, args.issued_workbook, args.pack, args.out)
        except (OSError, ValueError, FileExistsError) as exc:
            sys.exit(f"Client review lane failed: {exc}")
        if not args.quiet:
            summary = result["summary"]
            print(
                f"Answers: {summary['answered']} of {summary['questions_in_pack']} "
                f"questions ({summary['unanswered']} unanswered)"
            )
            print(
                f"  {summary['answers_that_close_their_group']} close their group, "
                f"{summary['answers_requiring_item_by_item_recheck']} inform a recheck"
            )
            if summary["answers_not_matching_an_offered_option"]:
                print(
                    f"  {summary['answers_not_matching_an_offered_option']} answer(s) "
                    "did not match an offered option and are retained as written"
                )
            print(f"  written to {args.out}; nothing has been applied")
        return 0

    try:
        pack = build_pack(
            args.artifacts,
            args.out_dir,
            consensus=args.consensus,
            classifications=args.classifications,
            resolved_by=args.resolved_by,
            supersede=args.supersede,
            settled_by_vote=args.settled_by_vote,
            dispositions=args.dispositions,
            prefill_from=args.prefill_from,
            scan_profile=args.scan_profile,
            manifest=args.manifest,
            max_questions=args.max_questions,
            dpi=args.render_dpi,
            max_image_bytes=args.max_image_bytes,
            force=args.force,
        )
    except (OSError, ValueError, FileExistsError) as exc:
        sys.exit(f"Client review lane failed: {exc}")

    if not args.quiet:
        summary = pack.get("summary", {})
        trigger = pack["trigger"]
        if not trigger["produced_documents"]:
            print(f"No pack produced: {summary.get('reason')}")
            print(f"  queue items retained: {summary.get('queue_item_count', 0)}")
        else:
            print(
                f"Questions: {summary.get('answerable_question_count', 0)} to ask "
                f"({summary.get('batch_clearing_question_count', 0)} clear their group "
                f"outright, {summary.get('individually_rechecked_question_count', 0)} "
                "inform an item-by-item recheck)"
            )
            print(
                f"  covering {summary.get('items_covered', 0)} of "
                f"{summary.get('queue_item_count', 0)} items "
                f"({summary.get('coverage_pct', 0)}%)"
            )
            print(f"  triggered by: {', '.join(trigger['triggered_blocks']) or 'force'}")
        for key, label in (
            ("reconciled_items", "retained as already resolved"),
            ("superseded_items", "retained as superseded by a re-read"),
            ("settled_by_vote_items", "retained as settled by a vendor vote"),
            ("dispositioned_items", "retained as dispositioned by an operator"),
        ):
            if summary.get(key):
                print(f"  {label}: {summary[key]}")
        if trigger["produced_documents"]:
            print(f"  written to {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
