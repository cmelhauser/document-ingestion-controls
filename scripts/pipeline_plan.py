#!/usr/bin/env python3
"""Devise a pipeline from what the corpus turns out to be.

`tasks/run-pipeline.md` gives the spine: the order lanes must run in when they
run at all. It does not say which of the optional lanes this corpus needs, and
that question has a measurable answer. A corpus with no handwriting does not
need the handwriting lanes; one whose documents each name twenty jobs will not
be attributed by any reference table; one whose second engine read almost
nothing cannot reach consensus and will hand the client a queue made of
starvation rather than disagreement.

Run without those measurements, the pipeline is a fixed order applied to an
unknown corpus, and the cost lands on the client as review items. This command
takes the measurements from artifacts the run already retained and proposes an
ordered plan: which optional lanes to run, which to skip, and -- the part that
matters most -- which client questions the plan cannot avoid and what would
remove each one.

What it is not:

* It is not an authorization. A lane disabled by configuration stays disabled;
  the plan names the setting and the reason, and an operator decides.
* It is not a gate. It clears nothing, and a plan that says a lane is
  unnecessary is a proposal about cost, never a finding about evidence.
* It does not guess. A lane whose deciding measurement is missing is reported
  `undecided` with the artifact that would decide it. Defaulting an undecided
  lane to "skip" is how a corpus reaches delivery with a control never run, and
  defaulting it to "run" is how a run spends a client's money on a lane its own
  evidence says is pointless.

Rule 9 applies to this command as much as to any other: a plan built from no
measurements has not planned anything, and `build` refuses rather than emitting
an empty plan that looks like a decision.

Usage:
    python pipeline_plan.py RUN --out RUN/analytics/pipeline_plan.json
    python pipeline_plan.py RUN --objective fewest-client-questions --out plan.json
"""

import argparse
import fnmatch
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from run_lane_coverage import LANES

ARTIFACT_TYPE = "pipeline_plan_v1"

# This command's own artifact, recognized so discovery can skip it.
OWN_ARTIFACT = f'"{ARTIFACT_TYPE}"'

# A plan is one of exactly these. There is deliberately no fourth value that
# means "probably skip": an unmeasured lane is undecided, and saying so is the
# whole point of the command.
SELECTED = "selected"
SKIPPED = "skipped"
UNDECIDED = "undecided"

# Objectives change which way a close call falls, never whether a measurement is
# believed. `fewest-client-questions` prefers a lane that resolves items without
# the client even when it costs provider spend; `least-spend` prefers the
# reverse. `balanced` runs a lane when its measurement says it has work to do.
OBJECTIVES = ("balanced", "fewest-client-questions", "least-spend")

# How each measurement's source artifact is recognized. A marker is a literal
# string that must appear in the artifact's own opening bytes, and every one of
# these was read off a real artifact rather than guessed. Both cheap ways to do
# this are wrong: a top-level key alone misses `total_pages_profiled`, which
# lives inside `summary`, and a substring of the whole file matches every
# artifact that quotes another. Bounding the search to the prefix gives the
# precision of a key check at the cost of a header read.
#
# A run directory is large -- 52 GB and 21,637 JSON files on the corpus this was
# built against -- so nothing is parsed during discovery. The prefix scan picks
# candidates, the newest candidate per measurement wins, and only the winners
# are parsed. Parsing every file instead took longer than the pipeline lane it
# was planning.
MEASUREMENT_SOURCES = {
    "scan_profile": (('"total_pages_profiled"',),),
    # Consensus writes no `artifact_type`; this summary key is its own.
    "consensus": (('"single_engine_documents"',),),
    # The per-field flag breakdown, which the consensus summary does not carry.
    # Two strings, both required: either alone matches the relationship lanes'
    # retained responses as well.
    "consensus_exceptions": (('"is_handwritten"', '"flag"'),),
    # Any artifact carrying `documents[].fields`. Consensus itself qualifies, so
    # a run that never applied mappings or corroboration still measures.
    "records": (
        ('"single_engine_documents"',),
        ('"records_with_applied_mappings_v1"',),
        ('"records_with_corroborated_values_v1"',),
    ),
    "attribution": (('"attribution_by_method"',),),
    "classification": (('"classification_consensus_v1"',),),
    "corroboration": (('"independent_corroboration_v1"',),),
    "review_queue": (('"client_review_cards"',),),
}

# Which lanes this command can name, and the evidence each leaves behind, taken
# from the lane catalogue rather than restated here so the two cannot drift.
#
# A plan that recommends a lane the run has already executed is worse than no
# plan: it asks an operator to spend on evidence that is already retained. This
# command shipped doing exactly that -- it selected both handwriting lanes on a
# run where they had executed twice, produced 3,319 annotations, and covered
# every one of the 418 documents it was citing as the reason to run them.
PLANNABLE_COMMANDS = {command: (markers, filenames) for _, command, markers, filenames in LANES}

# Opening bytes searched for a marker. Large enough to cover a summary block
# with a long findings string in it, small enough that scanning a full run
# directory is a header read per file.
PREFIX_BYTES = 65_536

# A winning candidate above this size is reported as unscanned rather than
# parsed. Reported, never silently treated as absent: "too large to read" and
# "this lane never ran" are different findings and lead to different plans.
UNSCANNABLE_BYTES = 512_000_000

# The consensus exception rate above which `tasks/run-pipeline.md` requires the
# corpus-context rebuild before the run continues. Documented there, not chosen
# here.
CONTEXT_REQUIRED_EXCEPTION_RATE = 0.50

# Below this share of documents, a lane that costs a provider call per document
# is not worth its setup for this corpus alone. It is a cost threshold, never an
# evidence one: nothing is accepted or cleared on the strength of it.
MATERIAL_SHARE = 0.02

# A document whose line items name more distinct keys than this is a multi-key
# document -- an allocation question, not an attribution failure. One is
# unanimous; anything above one is already several.
UNANIMOUS = 1


def load(path):
    """Return one retained artifact, or None when it is absent or unreadable."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="ignore"))
    except (OSError, ValueError):
        return None


def is_raw(relative):
    """Return whether a path sits under a retained raw-response directory.

    A run names these both ways -- `providers/raw/` and
    `post_review_consolidation_02/client_review_cross_record_raw/` -- and a
    prefix test alone misses the second form. On the corpus this was built
    against that was 558 provider responses being read as though they were lane
    artifacts. A raw response names the lane that asked for it even when the
    request failed, so counting one is counting a lane that produced nothing.
    """
    # Directory parts only. The guard is about raw-response directories, and
    # testing the file name too would exclude a retained artifact that merely
    # happens to be called `raw_something.json`.
    return any(part.startswith("raw") or part.endswith("_raw") for part in relative.parts[:-1])


def candidates(run_dir):
    """Return every retained artifact prefix-matching each measurement's markers.

    Raw provider responses are excluded for the reason `run_lane_coverage.py`
    gives: a raw response names the lane that asked for it even when the request
    failed, so scanning them reports lanes that produced nothing.
    """
    root = Path(run_dir)
    if not root.is_dir():
        raise SystemExit(f"Not a run directory: {run_dir}")
    matches = {name: [] for name in MEASUREMENT_SOURCES}
    ran = {command: [] for command in PLANNABLE_COMMANDS}
    for path in root.rglob("*.json"):
        if not path.is_file():
            continue
        if is_raw(path.relative_to(root)):
            continue
        try:
            with path.open("rb") as handle:
                prefix = handle.read(PREFIX_BYTES).decode("utf-8", errors="ignore")
        except OSError:
            continue
        if OWN_ARTIFACT in prefix:
            # A plan is not evidence. Its `measured` block quotes the very keys
            # discovery matches on, and being the newest file in the run it wins
            # every slot it touches -- so a second run of this command measured
            # its own first run and reported the corpus as having no records.
            continue
        for name, groups in MEASUREMENT_SOURCES.items():
            if any(all(marker in prefix for marker in group) for group in groups):
                matches[name].append(path)
        for command, (markers, filenames) in PLANNABLE_COMMANDS.items():
            # The lane catalogue matches markers against an artifact's top-level
            # keys. Here they are matched as quoted strings in the opening bytes,
            # which is stricter and needs no parse; a lane whose marker sits past
            # the prefix is reported as not run, which is the safe direction --
            # it proposes work rather than hiding it.
            if any(fnmatch.fnmatch(path.name, pattern) for pattern in filenames) or any(
                f'"{marker}"' in prefix for marker in markers
            ):
                ran[command].append(path)
    return matches, ran


def artifacts(run_dir):
    """Index the run's retained artifacts by the measurement each can supply.

    The newest candidate wins, because a run reruns lanes into no-clobber paths
    and the fourth consensus pass is the one that describes the corpus. Only the
    winners are parsed.
    """
    found, unscanned = {}, []
    matches, ran = candidates(run_dir)
    for name, paths in matches.items():
        for path in sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True):
            if path.stat().st_size > UNSCANNABLE_BYTES:
                unscanned.append({"measurement": name, "path": str(path), "reason": "too_large"})
                continue
            data = load(path)
            if isinstance(data, dict):
                found[name] = (path, data)
                break
            unscanned.append({"measurement": name, "path": str(path), "reason": "unparseable"})
    return found, unscanned, {command: paths for command, paths in ran.items() if paths}


def key_cardinality(records, key_fields):
    """Count documents by how many distinct attribution keys their lines name.

    A document with no header key and one key repeated down its lines is a
    document-level attribution nobody has claimed yet. The same document with
    twenty keys is not an attribution failure at all -- it is one statement
    covering twenty jobs, and forcing it to a single key would invent a fact the
    page does not state. The two are opposite findings and the raw unattributed
    count conflates them.
    """
    counts = Counter()
    for document in records:
        header = document.get("header") or {}
        if isinstance(header, dict) and any(
            isinstance(header.get(field), dict) and header[field].get("value") not in (None, "")
            for field in key_fields
        ):
            counts["header_key"] += 1
            continue
        distinct = set()
        for path, cell in (document.get("fields") or {}).items():
            if not path.startswith("lines[") or path.rsplit(".", 1)[-1] not in key_fields:
                continue
            if isinstance(cell, dict) and cell.get("accepted"):
                value = str(cell.get("value") or "").strip()
                if value:
                    distinct.add(value)
        if not distinct:
            counts["no_key"] += 1
        elif len(distinct) <= UNANIMOUS:
            counts["unanimous_line_key"] += 1
        else:
            counts["multi_key"] += 1
    return dict(counts)


def measure(found, key_fields):
    """Reduce the retained artifacts to the numbers the decisions are made on.

    Every entry here is either a measurement or absent. Nothing is defaulted to
    zero: a missing measurement and a measured zero lead to different plans, and
    collapsing them is how an unrun lane comes to look like a clean one.
    """
    measured = {}

    if "scan_profile" in found:
        _, profile = found["scan_profile"]
        summary = profile.get("summary") or {}
        measured["pages"] = summary.get("total_pages_profiled")
        measured["quality_bands"] = summary.get("quality_bands")
        measured["branch_routing"] = summary.get("branch_routing")

    if "consensus" in found:
        _, consensus = found["consensus"]
        summary = consensus.get("summary") or {}
        documents = summary.get("documents")
        measured["consensus_documents"] = documents
        measured["consensus_exception_fields"] = summary.get("exception_fields")
        # The lane reports this as a percentage. Kept as a fraction here so the
        # threshold comparison cannot be off by a factor of a hundred.
        rate = summary.get("exception_rate_pct")
        measured["consensus_exception_rate"] = (rate / 100) if rate is not None else None
        # `single_engine_documents` counts documents only one engine produced at
        # all, which is not the same population as the fields only one engine
        # read. Both are recorded; the lane decision is made on the field-level
        # breakdown below, because a rate over a different population is not a
        # comparison.
        measured["single_engine_documents"] = summary.get("single_engine_documents")
        measured["context_aware_update_required"] = summary.get("context_aware_update_required")
        measured["corpus_context"] = summary.get("corpus_context")

    if "consensus_exceptions" in found:
        _, exceptions = found["consensus_exceptions"]
        flags = Counter(
            entry.get("flag") for entry in (exceptions.get("exceptions") or []) if entry.get("flag")
        )
        total = sum(flags.values())
        measured["consensus_exception_flags"] = dict(flags.most_common())
        measured["single_engine_fields"] = flags.get("single_engine")
        measured["single_engine_share"] = (flags.get("single_engine", 0) / total) if total else None

    if "corroboration" in found:
        _, corroboration = found["corroboration"]
        summary = corroboration.get("summary") or {}
        measured["corroborated_findings"] = summary.get("corroborated")
        measured["corroboration_tie_breaks"] = summary.get("tie_broken")
        measured["corroboration_unresolved"] = summary.get("unresolved")
        measured["corroboration_unresolved_reasons"] = summary.get("unresolved_reasons")

    if "records" in found:
        _, records_artifact = found["records"]
        records = records_artifact.get("documents") or []
        if records:
            measured["documents"] = len(records)
            measured["attribution_key_shape"] = key_cardinality(records, key_fields)
            census = Counter()
            handwriting = Counter()
            for document in records:
                fields = document.get("fields") or {}
                for path in fields:
                    census[path.rsplit(".", 1)[-1]] += 1
                cell = fields.get("has_handwriting")
                handwriting[str((cell or {}).get("value"))] += 1
            measured["field_census"] = dict(census.most_common(40))
            # The scan profile carries no handwriting count -- it routes on ink
            # separation and morphology, which is a different question. The
            # per-document flag the extractor emits is the measurement that
            # decides whether the handwriting lanes have anything to do.
            flagged = handwriting.get("True", 0)
            measured["handwriting_documents"] = flagged
            measured["handwriting_unknown_documents"] = handwriting.get("None", 0)
            measured["handwriting_share"] = flagged / len(records)

    if "attribution" in found:
        _, attributed = found["attribution"]
        summary = attributed.get("summary") or {}
        measured["attribution_by_method"] = summary.get("attribution_by_method")
        measured["unconsulted_key_fields"] = summary.get("unconsulted_key_fields")
        measured["reference_rows_ingested"] = summary.get("reference_rows_ingested")
        measured["unresolved_documents"] = summary.get("unresolved_documents")

    if "classification" in found:
        _, classification = found["classification"]
        summary = classification.get("summary") or {}
        measured["classification_accepted"] = summary.get("accepted")
        measured["classification_unresolved"] = summary.get("unresolved")

    if "review_queue" in found:
        _, queue = found["review_queue"]
        items = queue.get("items") or []
        measured["review_items"] = len(items)
        measured["review_items_by_source"] = dict(
            Counter(
                item.get("source_artifact") or item.get("review_source") or "unnamed"
                for item in items
            ).most_common()
        )

    return measured


def documents(count):
    """Pluralize a document count. These strings are read by a client."""
    return f"{count} document" + ("" if count == 1 else "s")


def decision(lane, command, phase, verdict, because, artifact=None, then=None):
    """One plan entry. `because` is a measurement, never an opinion."""
    return {
        "lane": lane,
        "command": command,
        "phase": phase,
        "decision": verdict,
        "because": because,
        "measured_from": str(artifact) if artifact else None,
        "then": then,
    }


def handwriting_lanes(measured, found, objective):
    """Handwriting is the cheapest lane family to decide and the most often run blind."""
    share = measured.get("handwriting_share")
    source = found.get("records", (None,))[0]
    if share is None:
        return [
            decision(
                "handwriting OCR",
                "google_handwriting_ocr.py",
                "3H",
                UNDECIDED,
                "no records artifact carries has_handwriting, so the share is unmeasured",
                then="run extraction and consensus, then read has_handwriting on the records",
            )
        ]
    pages = measured.get("handwriting_documents", 0)
    unknown = measured.get("handwriting_unknown_documents", 0)
    if share == 0 and not unknown:
        return [
            decision(
                "handwriting OCR",
                "google_handwriting_ocr.py",
                "3H",
                SKIPPED,
                f"no document of {measured.get('documents')} is flagged has_handwriting",
                source,
            ),
            decision(
                "handwriting reconciliation",
                "handwriting_review.py",
                "3H",
                SKIPPED,
                "nothing for it to reconcile: no handwriting lane output",
                source,
            ),
        ]
    material = share >= MATERIAL_SHARE or objective == "fewest-client-questions"
    return [
        decision(
            "handwriting OCR",
            "google_handwriting_ocr.py",
            "3H",
            SELECTED if material else UNDECIDED,
            f"{documents(pages)} flagged has_handwriting ({share:.1%} of the corpus)"
            + (f", and {unknown} more where the flag is absent" if unknown else ""),
            source,
            then="disabled by default: set GOOGLE_HANDWRITING_OCR_ENABLED and authorize the provider",
        ),
        decision(
            "handwriting reconciliation",
            "handwriting_review.py",
            "3H",
            SELECTED if material else UNDECIDED,
            "one provider group is one vote; a reading needs reconciling before it is used",
            source,
        ),
    ]


def extraction_lanes(measured, found, objective):
    """Whether this corpus can reach consensus, and what to do when it cannot."""
    plan = []
    source = found.get("consensus", (None,))[0]
    rate = measured.get("consensus_exception_rate")
    if rate is None:
        plan.append(
            decision(
                "context-aware update",
                "extraction_context.py",
                "3",
                UNDECIDED,
                "no consensus artifact in this run, so the exception rate is unknown",
                then="run both extraction lanes and consensus.py first",
            )
        )
    elif rate > CONTEXT_REQUIRED_EXCEPTION_RATE:
        plan.append(
            decision(
                "context-aware update",
                "extraction_context.py",
                "3",
                SELECTED,
                f"consensus exception rate {rate:.1%} is above the {CONTEXT_REQUIRED_EXCEPTION_RATE:.0%} "
                "threshold that run-pipeline.md makes mandatory",
                source,
                then="re-extract BOTH lanes with LLM_CORPUS_CONTEXT set to the same file",
            )
        )
    else:
        plan.append(
            decision(
                "context-aware update",
                "extraction_context.py",
                "3",
                SKIPPED,
                f"consensus exception rate {rate:.1%} is below the mandatory threshold",
                source,
            )
        )

    starved = measured.get("single_engine_share")
    exceptions_source = found.get("consensus_exceptions", (None,))[0]
    if starved is None:
        plan.append(
            decision(
                "independent corroboration",
                "independent_corroboration.py",
                "3",
                UNDECIDED,
                "no consensus exception artifact, so consensus starvation is unmeasured",
                then="run consensus.py and read the per-field flag breakdown it retains",
            )
        )
    elif starved >= 0.5:
        plan.append(
            decision(
                "independent corroboration",
                "independent_corroboration.py",
                "3",
                SELECTED,
                f"{measured.get('single_engine_fields')} of "
                f"{sum((measured.get('consensus_exception_flags') or {}).values())} open consensus "
                f"exceptions ({starved:.1%}) are single_engine -- one engine read the field and "
                "nothing else did, which is starvation rather than disagreement, and a second "
                "engine reading nothing is not a second reading",
                exceptions_source,
                then=(
                    "qualify the second reader with engine_agreement.py before committing the "
                    "corpus to it; corroboration is evidence for a client decision, not a control"
                ),
            )
        )
        available = measured.get("corroborated_findings")
        plan.append(
            decision(
                "applied corroboration",
                "apply_corroboration.py",
                "3",
                UNDECIDED,
                (
                    # When the corroboration lane has already run, the question is
                    # no longer whether the evidence exists. Saying so is the
                    # difference between "go and measure" and "this is yours to
                    # decide", and only the second is actually the client's.
                    f"{available} findings already carry independent corroboration and "
                    f"{measured.get('corroboration_tie_breaks')} were tie-broken by it; whether "
                    "that may stand in for a second model vendor is a client policy decision, "
                    "not a measurement"
                    if available
                    else "acceptance on page-scoped evidence is a client policy decision, not a "
                    "measurement"
                ),
                found.get("corroboration", (exceptions_source,))[0],
                then=(
                    "needs --authorization naming a client decision; page-scoped evidence defeats "
                    "a misread, not a misattribution"
                ),
            )
        )
    else:
        plan.append(
            decision(
                "independent corroboration",
                "independent_corroboration.py",
                "3",
                SKIPPED if objective == "least-spend" else UNDECIDED,
                f"{starved:.1%} of open consensus exceptions are single_engine; both engines are "
                "mostly reading the same fields",
                exceptions_source,
            )
        )
    return plan


def attribution_lanes(measured, found):
    """What the unattributed count is actually made of."""
    plan = []
    source = found.get("attribution", (None,))[0]
    records_source = found.get("records", (None,))[0]
    unconsulted = measured.get("unconsulted_key_fields")
    if unconsulted:
        plan.append(
            decision(
                "attribution",
                "attribution.py",
                "3A",
                SELECTED,
                "documents state a key under a header field rank 1 does not read: "
                + ", ".join(f"{name} ({count})" for name, count in unconsulted.items()),
                source,
                then="re-run with --key-field naming those fields before asking the client for anything",
            )
        )
    shape = measured.get("attribution_key_shape")
    if shape is None:
        plan.append(
            decision(
                "allocation policy",
                "allocation_policy.py",
                "3A",
                UNDECIDED,
                "no records artifact, so the key shape of unattributed documents is unmeasured",
                then="run attribution.py and read the records it read",
            )
        )
        return plan
    multi = shape.get("multi_key", 0)
    unanimous = shape.get("unanimous_line_key", 0)
    none = shape.get("no_key", 0)
    if multi:
        plan.append(
            decision(
                "allocation policy",
                "allocation_policy.py",
                "3A",
                SELECTED,
                f"{documents(multi)} name more than one key on their own line items -- these are "
                "allocation questions and no reference table will attribute them",
                records_source,
                then="effective-dated client-approved rules only; discovery output is a proposal",
            )
        )
    if unanimous:
        plan.append(
            decision(
                "attribution",
                "attribution.py",
                "3A",
                UNDECIDED,
                f"{documents(unanimous)} state one key, unanimously, on their lines and none in the "
                "header; whether unanimity is a document-level attribution is a client decision",
                records_source,
                then="rank 8 (manual assignment) until someone decides unanimity counts",
            )
        )
    if none:
        rows = measured.get("reference_rows_ingested")
        plan.append(
            decision(
                "attribution",
                "attribution.py",
                "3A",
                SELECTED if rows else UNDECIDED,
                f"{documents(none)} state no attribution key anywhere; ranks 3 and 6 are the only "
                "routes and both need the reference table"
                + ("" if rows else ", which this run did not have"),
                records_source,
                then=None
                if rows
                else "ask the client for the order-entry export: key, date, customer, amount",
            )
        )
    return plan


def classification_lanes(measured, found):
    """Whether the corpus still has documents nobody has typed.

    An unclassified document is not a small problem downstream: template
    fingerprints, mapping rules, and every family-conditioned control key off
    the document type, and a corpus that reaches them as one undifferentiated
    template is not a layout.
    """
    accepted = measured.get("classification_accepted")
    source = found.get("classification", (None,))[0]
    if accepted is None:
        return [
            decision(
                "classification consensus",
                "classification_consensus.py",
                "2",
                UNDECIDED,
                "no classification consensus artifact, so the unclassified share is unmeasured",
                then="run classification_consensus.py after extraction and feed template_observations.py",
            )
        ]
    unresolved = measured.get("classification_unresolved") or 0
    if not unresolved:
        return [
            decision(
                "classification amendment",
                "classification_amend.py",
                "—",
                SKIPPED,
                f"consensus typed all {accepted} documents; nothing abstained",
                source,
            )
        ]
    return [
        decision(
            "classification amendment",
            "classification_amend.py",
            "—",
            UNDECIDED,
            f"consensus abstained on {unresolved} of {accepted + unresolved} documents; only an "
            "operator-authorized client decision can type those",
            source,
            then="refuses a plan that is not operator_authorized; regenerate templates afterwards",
        )
    ]


def review_lanes(measured, found, objective):
    """Where reduction effort pays, measured by what the queue is made of."""
    items = measured.get("review_items")
    source = found.get("review_queue", (None,))[0]
    if items is None:
        return [
            decision(
                "root-cause grouping",
                "review_grouping.py",
                "4Q",
                UNDECIDED,
                "no final review queue in this run, so its composition is unmeasured",
                then="run final_review_queue.py with every review-bearing artifact",
            )
        ]
    plan = [
        decision(
            "root-cause grouping",
            "review_grouping.py",
            "4Q",
            SELECTED if items else SKIPPED,
            f"{items} queue items"
            + (
                "; grouping retains every underlying item and is the cheapest reduction available"
                if items
                else ", so there is nothing to group"
            ),
            source,
        )
    ]
    by_source = measured.get("review_items_by_source") or {}
    if by_source and items:
        top, count = next(iter(by_source.items()))
        plan.append(
            decision(
                "review reduction target",
                "safe_review_consolidation.py",
                "4Q",
                SELECTED if objective == "fewest-client-questions" else UNDECIDED,
                f"{count} of {items} items ({count / items:.0%}) come from {top}; reduction spent "
                "anywhere else moves a minority of the queue",
                source,
                then="proposal-only, and it re-runs its sub-lanes into its own output directory",
            )
        )
    return plan


def client_questions(measured, plan):
    """The interactions this plan cannot remove, and what would remove each.

    This is the objective the plan is optimising, so it is reported as its own
    section rather than left to be inferred from the lane list. Every entry names
    the input that would answer it, because "ask the client" without saying what
    to ask for is how a phase-0 request comes back vague.
    """
    questions = []
    shape = measured.get("attribution_key_shape") or {}
    if shape.get("no_key"):
        questions.append(
            {
                "question": "Can your order-entry or quoting system export its acknowledgement/job list?",
                "unblocks": f"{documents(shape['no_key'])} that state no key anywhere",
                "needs": "one file: key, date, customer, amount; optionally po_number and selling_location",
                "removes_lanes": ["manual assignment (rank 8) for those documents"],
            }
        )
    if shape.get("unanimous_line_key"):
        questions.append(
            {
                "question": (
                    "When every line of a statement names the same job, may we attribute the "
                    "statement to it?"
                ),
                "unblocks": documents(shape["unanimous_line_key"]),
                "needs": "a yes or no; the evidence is already retained",
                "removes_lanes": [],
            }
        )
    if measured.get("single_engine_share", 0) and measured["single_engine_share"] >= 0.5:
        questions.append(
            {
                "question": (
                    "May a value one engine read, which an independent non-LLM extractor also read "
                    "from that same page, be accepted without a second model vendor?"
                ),
                "unblocks": (
                    f"{measured['corroborated_findings']} corroborated findings"
                    if measured.get("corroborated_findings")
                    else f"{measured.get('single_engine_fields')} single_engine consensus exceptions"
                ),
                "needs": "a named decision to pass to apply_corroboration.py --authorization",
                "removes_lanes": [],
                "caveat": "page-scoped evidence defeats a misread, not a misattribution",
            }
        )
    undecided = [entry for entry in plan if entry["decision"] == UNDECIDED and entry.get("then")]
    for entry in undecided:
        if entry["lane"] not in {q.get("lane") for q in questions}:
            questions.append(
                {
                    "question": f"Decide the {entry['lane']} lane",
                    "lane": entry["lane"],
                    "unblocks": entry["because"],
                    "needs": entry["then"],
                    "removes_lanes": [],
                }
            )
    return questions


def build(run_dir, objective, key_fields):
    """Measure the run and return the plan, or refuse when nothing was measured."""
    if objective not in OBJECTIVES:
        raise SystemExit(f"Unknown objective {objective!r}; choose from {', '.join(OBJECTIVES)}")
    found, unscanned, ran = artifacts(run_dir)
    measured = measure(found, key_fields)
    if not measured:
        # Rule 9, applied to this command. An empty plan reads like a decision
        # that no optional lane is needed, which is exactly the claim the
        # evidence does not support.
        raise SystemExit(
            f"No plannable measurement found under {run_dir}. This command reads retained "
            "artifacts; run at least scan profiling or extraction first. It will not emit a "
            "plan built from nothing."
        )
    plan = []
    plan += handwriting_lanes(measured, found, objective)
    plan += classification_lanes(measured, found)
    plan += extraction_lanes(measured, found, objective)
    plan += attribution_lanes(measured, found)
    plan += review_lanes(measured, found, objective)

    # A recommendation to run a lane the run has already executed is the one
    # mistake this command must not make. The verdict still follows the
    # measurement -- the lane may genuinely need rerunning after new evidence --
    # but it never reads as untouched work when output is retained.
    for entry in plan:
        retained = ran.get(entry["command"]) or []
        if not retained:
            continue
        entry["already_run"] = [str(path) for path in retained[:5]]
        entry["because"] += (
            f" -- but this lane has already run: {len(retained)} artifacts retained, "
            f"first at {retained[0]}"
        )
        entry["then"] = (
            "check its findings reached the gate before rerunning; a lane that ran and "
            "whose exceptions were never handed to final_review_queue.py looks identical "
            "to one that never ran"
        )

    counts = Counter(entry["decision"] for entry in plan)
    return {
        "artifact_type": ARTIFACT_TYPE,
        "generated_at": datetime.now(UTC).isoformat(),
        "run_dir": str(run_dir),
        "objective": objective,
        "explicit_key_fields": list(key_fields),
        "proposal_only": True,
        "clears_no_control": True,
        "summary": {
            "measurements_taken": sorted(measured),
            "measurement_sources": {name: str(path) for name, (path, _) in sorted(found.items())},
            "lanes_planned": len(plan),
            "selected": counts[SELECTED],
            "skipped": counts[SKIPPED],
            "undecided": counts[UNDECIDED],
            # Named, not dropped. A measurement missing because its artifact was
            # too large is a different finding from one missing because the lane
            # never ran, and only one of the two is the operator's to fix.
            "unscanned": unscanned,
            "lanes_with_retained_output": sorted(ran),
        },
        "measured": measured,
        "plan": plan,
        "client_questions": client_questions(measured, plan),
    }


def render(report):
    """One screen an operator can act on, in plan order."""
    summary = report["summary"]
    lines = [
        f"Pipeline plan for {report['run_dir']} (objective: {report['objective']})",
        f"  {summary['selected']} selected, {summary['skipped']} skipped, "
        f"{summary['undecided']} undecided, from {len(summary['measurements_taken'])} measurements",
        "",
    ]
    for entry in report["plan"]:
        lines.append(
            f"  [{entry['decision']:>9}] {entry['lane']} ({entry['command']}, phase {entry['phase']})"
        )
        lines.append(f"              {entry['because']}")
        if entry.get("then"):
            lines.append(f"              -> {entry['then']}")
    if report["client_questions"]:
        lines.append("")
        lines.append(f"Client questions this plan cannot avoid: {len(report['client_questions'])}")
        for question in report["client_questions"]:
            lines.append(f"  - {question['question']}")
            lines.append(f"      unblocks: {question['unblocks']}")
            lines.append(f"      needs:    {question['needs']}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Propose the pipeline this corpus needs, from the run's own retained artifacts."
    )
    parser.add_argument(
        "run_dir", help="Run directory to measure. Read-only; nothing is written into it."
    )
    parser.add_argument(
        "--objective",
        default="balanced",
        choices=OBJECTIVES,
        help="Which way a close call falls. Never changes whether a measurement is believed.",
    )
    parser.add_argument(
        "--key-field",
        action="append",
        default=None,
        metavar="FIELD",
        help="Header field carrying the attribution key, repeatable. Defaults to attribution.py's own list.",
    )
    parser.add_argument("--out", default=None, help="Destination path for the JSON plan artifact.")
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args()

    # Imported here so the default stays the one attribution actually uses:
    # two lists that drift apart would plan for keys the lane never reads.
    from attribution import EXPLICIT_KEY_FIELDS

    key_fields = tuple(args.key_field) if args.key_field else EXPLICIT_KEY_FIELDS
    report = build(args.run_dir, args.objective, key_fields)

    if args.out:
        out = Path(args.out)
        if out.exists():
            sys.exit(f"Refusing to overwrite {out}; choose a fresh no-clobber path.")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n")
    if not args.quiet:
        print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
