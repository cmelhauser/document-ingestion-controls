#!/usr/bin/env python3
"""
Phase 3 -- Machine double-entry consensus.

Merges 2-3 independent engine extractions of the same document into one record
with per-field consensus flags, and routes disagreements to an exception queue.

This is what makes sampling-only QA defensible. Reading each document once and
trusting a confidence score does not produce a dataset anyone can sign off on --
OCR confidence is well calibrated on glyph shape and poorly calibrated on field
assignment. Two independent readings disagreeing is a far stronger signal than
one reading being unsure.

Disposition:

    both agree                          -> auto-accept          consensus_2of2
    disagree, tiebreaker gives majority -> accept + queue       consensus_2of3
    no majority                         -> hard exception       no_consensus

Source-labelled extension fields are retained per engine rather than reconciled.
Their key is the printed label plus an occurrence index, so comparing them
across engines measures label spelling and enumeration order rather than
agreement about a fact. They leave here as mapping proposals for
``schema_discovery.py``, which is the schema-review work they were always meant
to become.

Handwritten fields (source == "handwritten") are held to a stricter rule:
numerics need 3-of-3, free text 2-of-3. Digit confusion pairs (1/7, 4/9, 3/5/8,
0/6) are non-recoverable errors in a financial dataset, so the automation saving
is not worth taking.

Usage:
    python consensus.py engineA.json engineB.json engineC.json \
        --out consensus.json --exceptions exceptions.json
"""

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from runtime_config import (
    SOURCE_READ_BY_MODEL,
    UNRESOLVED_MODEL_VENDOR,
    env_value,
    load_project_env,
    model_vendor,
)

# Values within this absolute delta are treated as agreeing. Covers rounding
# representation differences between engines, not genuine disagreement.
NUMERIC_TOLERANCE = 0.005

NUMERIC_HINTS = (
    "amount",
    "total",
    "subtotal",
    "price",
    "qty",
    "quantity",
    "weight",
    "rate",
    "tax",
    "freight",
    "discount",
    "value",
    "duty",
    "count",
    "pieces",
)
IDENTIFIER_HINTS = (
    "account",
    "code",
    "identifier",
    "invoice_number",
    "number",
    "postal",
    "reference",
    "tracking",
    "zip",
)

# These fields describe the adapter transport or retained evidence, not facts
# to be voted on by independent extraction engines. They remain available in
# the source engine artifact and are carried by the adapter contract; feeding
# them into consensus would manufacture review work for hashes, file paths,
# raw-response locations, and provider diagnostics.
CONSENSUS_METADATA_KEYS = frozenset(
    {
        "source_file",
        "source_page_range",
        "source_page_number",
        "page_id",
        "page_pdf",
        "page_sha256",
        "raw_response",
        "raw_response_sha256",
        "google_genai_input_mode",
        "openai_input_mode",
        "model_confidences",
        "provider_review_flags",
        "review_status",
        "requires_independent_consensus",
    }
)


def is_numeric_field(name, value):
    """Return whether this field may be compared as a number.

    Identifier-shaped names are excluded by name before the value is inspected.
    An invoice number that happens to be all digits is not a quantity, and
    comparing two of them numerically makes `007` and `7` agree.
    """
    lowered = name.lower()
    if any(hint in lowered for hint in IDENTIFIER_HINTS):
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    return any(h in lowered for h in NUMERIC_HINTS)


def resolve_raw_response_path(handoff_path, handoff, raw_response):
    """Resolve retained raw evidence without losing contained-run portability.

    Current adapters write a run-root-relative artifact reference (for example
    ``providers/raw/primary/page.json``) because the workspace wrapper makes the
    run root their working directory. Older direct adapter invocations instead
    wrote paths relative to the handoff's directory. Prefer that established
    form, then accept the contained run-root form only when it agrees exactly
    with the handoff's declared raw-response directory. This preserves the
    current retained evidence while refusing an arbitrary traversal fallback.
    """
    raw_path = Path(raw_response)
    if raw_path.is_absolute():
        return raw_path
    direct = handoff_path.parent / raw_path
    if direct.is_file():
        return direct

    raw_directory = handoff.get("raw_response_directory")
    if not isinstance(raw_directory, str) or not raw_directory.strip():
        return direct
    declared = Path(raw_directory)
    if declared.is_absolute() or ".." in declared.parts:
        return direct
    if raw_path.parts[: len(declared.parts)] != declared.parts:
        return direct

    run_root = handoff_path.parent.parent.resolve()
    workspace_path = (run_root / raw_path).resolve()
    try:
        workspace_path.relative_to(run_root)
    except ValueError:
        return direct
    return workspace_path


def normalize(value):
    """Canonical form for comparison. Does not mutate the stored value."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 4)
    s = str(value).strip()
    if not s:
        return None
    cleaned = s.replace(",", "").replace("$", "").replace(" ", "")
    try:
        return round(float(cleaned), 4)
    except ValueError:
        pass
    # Case and internal whitespace differ between engines constantly and are not
    # substantive disagreement.
    return " ".join(s.lower().split())


# Two engines reading the same printed prose differ on punctuation and spacing
# constantly: `Northgate & Co` against `Northgate Co`, `murb rook` against
# `murbrook`, `Flint X; -70%` against `Flint X -70%`. On one run 303 blocking
# findings were exactly this and nothing else -- the engines had read the same
# glyphs and disagreed about a separator.
#
# This is deliberately not applied to numbers or identifiers. `is_numeric_field`
# already excludes anything matching IDENTIFIER_HINTS from the numeric path, so
# identifiers reach this branch and must be compared strictly: `001` against `1`
# is a real disagreement about an invoice number, and a hyphen inside a
# reference code can be load-bearing.
PUNCTUATION = re.compile(r"[^0-9a-z]+")


def punctuation_insensitive(value):
    """Reduce printed prose to the characters both engines certainly saw."""
    return PUNCTUATION.sub("", str(value).casefold())


def values_agree(a, b, field_name=None):
    """Report whether two readings of one field agree within the field's tolerance."""
    if field_name is not None and not (
        is_numeric_field(field_name, a) or is_numeric_field(field_name, b)
    ):
        if a is None or b is None:
            return a is None and b is None
        if isinstance(a, bool) or isinstance(b, bool):
            return a is b
        if " ".join(str(a).strip().casefold().split()) == " ".join(
            str(b).strip().casefold().split()
        ):
            return True
        if any(hint in field_name.lower() for hint in IDENTIFIER_HINTS):
            return False
        return punctuation_insensitive(a) == punctuation_insensitive(b)
    na, nb = normalize(a), normalize(b)
    if na is None and nb is None:
        return True
    if na is None or nb is None:
        return False
    if isinstance(na, float) and isinstance(nb, float):
        return abs(na - nb) <= NUMERIC_TOLERANCE
    return na == nb


def unwrap(entry):
    """Accept either {value, confidence, source} or a bare scalar."""
    if isinstance(entry, dict) and "value" in entry:
        return (entry.get("value"), entry.get("confidence"), entry.get("source", "printed"))
    return (entry, None, "printed")


def cluster_key(value):
    """Return a stable ordering key for a normalized reading."""
    return json.dumps(normalize(value), sort_keys=True, default=str)


def cluster_readings(unwrapped, field_name):
    """Group engine readings by complete-linkage agreement, independent of input order.

    Tolerance-based agreement is not transitive: with a 0.005 tolerance, 100.000
    agrees with 100.004 and 100.004 agrees with 100.008, but 100.000 does not
    agree with 100.008. Assigning each engine to the first cluster it happens to
    match therefore lets the order of the command-line arguments decide both the
    consensus strength and the accepted value.

    Requiring every member of a cluster to agree with every other member removes
    that dependence. A reading that could legitimately join two clusters is a
    genuine borderline; it stays separate and the caller withholds consensus.
    """
    ordered = sorted(unwrapped, key=lambda item: (cluster_key(item[1]), str(item[0])))
    clusters, ambiguous = [], False
    for eng, val, conf, src in ordered:
        matches = [
            cluster
            for cluster in clusters
            if all(values_agree(member, val, field_name) for member in cluster["values"])
        ]
        if len(matches) == 1:
            matches[0]["values"].append(val)
            matches[0]["engines"].append(eng)
            matches[0]["confidences"].append(conf)
            continue
        ambiguous = ambiguous or bool(matches)
        clusters.append(
            {
                "value": val,
                "values": [val],
                "engines": [eng],
                "confidences": [conf],
                "source": src,
            }
        )
    clusters.sort(key=lambda cluster: (-len(cluster["engines"]), cluster_key(cluster["value"])))
    return clusters, ambiguous


# A row's ordinal is not read off the page; it is where the row sits in the
# table, and the pipeline knows that without asking anyone. Putting it through
# vendor consensus produced 4,086 blocking findings on one run -- 5% of the whole
# client gate -- none of which protected a value. Worse, the field invites a
# specific misread: the schema notes that one engine filled `line_number` with
# the row's invoice number, so most of those "disagreements" were an engine
# answering a different question.
#
# Deriving it is not inventing a value. The field is *defined* as the row's
# position counting from 1, and the readings are retained rather than discarded,
# so nothing is lost and nothing is normalized into passing status. It is
# recorded as `derived_ordinal` and never as consensus, because no vendor
# agreement occurred.
DERIVED_ORDINAL_FIELDS = ("line_number",)
ROW_INDEX = re.compile(r"\[(\d+)\]")


def derived_ordinal_path(path):
    """Say whether this flattened path is a positional ordinal the run derives."""
    return path.rsplit(".", 1)[-1] in DERIVED_ORDINAL_FIELDS and bool(ROW_INDEX.search(path))


def derive_ordinal(path, entries):
    """Build the ordinal field from the row's own position, retaining the readings."""
    index = int(ROW_INDEX.findall(path)[-1])
    return {
        "value": index + 1,
        "candidate_values": None,
        "confidence": None,
        "source": "derived",
        # Never `consensus_*`: no vendor agreed to anything here.
        "consensus_flag": "derived_ordinal",
        "agreeing_engines": [],
        "engine_count": len(entries),
        "rule": "derived_row_position",
        "accepted": True,
        "is_handwritten": False,
        "blocking": False,
        "queue_for_review": False,
        # Provenance, not a decision term: what each engine put here is kept so a
        # reviewer can still see an engine that answered a different question.
        "engine_readings": {eng: unwrap(raw)[0] for eng, raw in entries},
    }


def reconcile_field(field_name, entries, handwriting_policy="strict"):
    """
    entries: list of (engine_name, raw_entry) across engines that produced it.
    Returns the consensus field dict.
    """
    unwrapped = [(eng, *unwrap(raw)) for eng, raw in entries]
    sources = {u[3] for u in unwrapped}
    handwritten = "handwritten" in sources
    numeric = any(is_numeric_field(field_name, u[1]) for u in unwrapped)

    clusters, ambiguous_grouping = cluster_readings(unwrapped, field_name)
    top = clusters[0]
    agree_count = len(top["engines"])
    n_engines = len(unwrapped)

    if handwritten and handwriting_policy == "comment_only":
        # Preserve every candidate reading as commentary, but do not promote a
        # handwritten value into the canonical field or make it a gate.
        required = 0
        rule = "handwritten_comment_only"
    elif handwritten:
        # A handwritten monetary/identifier reading is never safe to accept from
        # fewer than three independent engines. Keeping this at three (rather
        # than merely requiring unanimity among the available engines) prevents
        # an under-provisioned run from silently weakening the stated control.
        required = 3 if numeric else max(2, (n_engines // 2) + 1)
        rule = "handwritten_numeric_3of3" if numeric else "handwritten_text_majority"
    else:
        required = 2 if n_engines >= 2 else 1
        rule = "printed_majority"

    accepted = (
        False
        if handwritten and handwriting_policy == "comment_only"
        else (agree_count >= required and n_engines >= required)
    )

    if n_engines == 1:
        flag = "single_engine"
        accepted = False
    elif ambiguous_grouping:
        # A reading sat within tolerance of two different clusters. Which value
        # "wins" would be an artefact of grouping, not evidence of agreement.
        flag = "no_consensus"
        accepted = False
    elif agree_count == n_engines and n_engines == 2:
        flag = "consensus_2of2"
    elif agree_count == n_engines:
        flag = f"consensus_{agree_count}of{n_engines}"
    elif agree_count > n_engines / 2:
        flag = f"consensus_{agree_count}of{n_engines}"
    else:
        flag = "no_consensus"
        accepted = False

    confs = [c for c in top["confidences"] if isinstance(c, (int, float))]

    if flag.startswith("consensus_") and not accepted and required > agree_count:
        # e.g. two engines unanimously reading a handwritten numeric still fall
        # short of the 3-of-3 rule. Do not label that "consensus_2of2".
        flag = f"{flag}_below_required_{required}"

    field = {
        "value": top["value"] if accepted else None,
        "candidate_values": [c["value"] for c in clusters] if not accepted else None,
        "confidence": round(sum(confs) / len(confs), 4) if confs else None,
        "source": top["source"],
        "consensus_flag": flag,
        "agreeing_engines": top["engines"],
        "engine_count": n_engines,
        "rule": rule,
        "accepted": accepted,
        "is_handwritten": handwritten,
        "blocking": not (handwritten and handwriting_policy == "comment_only"),
    }

    if handwritten and handwriting_policy == "comment_only":
        field["handwriting_comment"] = {
            "candidate_values": [c["value"] for c in clusters],
            "source": "independent_extraction_observation",
            "client_review_required": False,
        }

    # A 2-of-3 result is accepted but still queued: one engine reading something
    # different is enough signal to warrant a look, and these are cheap to review
    # relative to the cost of a wrong figure surviving.
    field["queue_for_review"] = (
        False
        if handwritten and handwriting_policy == "comment_only"
        else (not accepted) or (agree_count < n_engines and n_engines >= 2)
    )
    return field


# The extension channel preserves printed labels that fall outside the controlled
# vocabulary. Its normalized key is derived from the label text plus an
# occurrence index, which makes it unusable as a consensus key: two engines
# agree only when they spell a label identically *and* enumerate repeated
# labels in the same order. Measured against a real 18-page corpus, 317 of 339
# label slots resolved to one engine and 147 carried a label that repeats within
# its own document, so reconciling this channel compared list positions rather
# than facts and produced 41% of all consensus exceptions. The entries are
# retained per engine instead and routed to schema review, which is the work
# they were always meant to become.
EXTENSION_ROOT = "source_labelled_fields"


def is_extension_path(path):
    """Report whether a flattened path belongs to the source-label extension channel."""
    return path == EXTENSION_ROOT or path.startswith(f"{EXTENSION_ROOT}.")


def flatten(record, prefix=""):
    """Flatten a document record into {dotted_path: raw_entry}."""
    out = {}
    for key, val in record.items():
        path = f"{prefix}{key}"
        if key in ("lines", "accessorials") and isinstance(val, list):
            for i, line in enumerate(val):
                if isinstance(line, dict):
                    out.update(flatten(line, prefix=f"{path}[{i}]."))
        elif isinstance(val, dict) and "value" not in val:
            out.update(flatten(val, prefix=f"{path}."))
        else:
            out[path] = val
    return out


PATH_RE = re.compile(r"^([^\[\].]+)(?:\[(\d+)\])?\.?(.*)$")


def unflatten(fields):
    """
    Rebuild the nested record shape from consensus fields.

    Downstream scripts (arithmetic_check, entity_resolve, attribution,
    completeness, sampling) all consume the record shape, not the flat field
    map. Emitting both keeps the pipeline chainable without every script having
    to understand consensus internals.
    """
    record = {}
    for path, field in fields.items():
        m = PATH_RE.match(path)
        if not m:
            continue
        head, index, rest = m.group(1), m.group(2), m.group(3)
        entry = {
            "value": field.get("value"),
            "confidence": field.get("confidence"),
            "source": field.get("source", "printed"),
        }
        if index is not None:
            bucket = record.setdefault(head, {})
            row = bucket.setdefault(int(index), {})
            row[rest or head] = entry
        elif rest:
            record.setdefault(head, {})[rest] = entry
        else:
            record[head] = entry

    for key in ("lines", "accessorials"):
        if isinstance(record.get(key), dict):
            record[key] = [record[key][i] for i in sorted(record[key])]
    return record


# The nested views a record carries beside its flat `fields`, for the controls
# that read a record's shape rather than its field map.
VIEWS = ("header", "lines", "accessorials")


def view_slot(path):
    """Where a field path sits in the views -- (view, row or None, column) -- or None."""
    match = PATH_RE.match(path)
    if not match or match.group(1) not in VIEWS:
        return None
    head, index, rest = match.groups()
    if not rest or (head == "header") != (index is None):
        return None
    return head, None if index is None else int(index), rest


def view_cells(document):
    """Every cell a document's views hold, as (field path, cell)."""
    header = document.get("header")
    if isinstance(header, dict):
        for column, cell in header.items():
            yield f"header.{column}", cell
    for key in ("lines", "accessorials"):
        rows = document.get(key)
        for index, row in enumerate(rows if isinstance(rows, list) else []):
            if isinstance(row, dict):
                for column, cell in row.items():
                    yield f"{key}[{index}].{column}", cell


def view_value(cell):
    """The value a view cell holds, whether it is an entry or a bare value."""
    return cell.get("value") if isinstance(cell, dict) else cell


def refresh_views(document, retired=()):
    """Bring a document's header, lines and accessorials into step with its fields.

    The fields are the record; the views are derived from them for the controls
    that read a record's shape -- attribution, arithmetic, valuation, entity
    resolution and the canonical export. A lane that writes a field and not its
    view leaves those controls deciding on the value it replaced: on the
    commission run 4,099 accepted values reached the export and none of them.

    Each field's entry replaces its view cell. A view cell no field names is kept
    -- an approved mapping or a lane's flag written to the view alone must not be
    lost because some other field changed -- unless its path is in `retired`,
    which is how a lane that removes a field says its view entry goes with it.
    """
    placed = {}
    for path, field in (document.get("fields") or {}).items():
        slot = view_slot(path)
        if slot and isinstance(field, dict):
            head, row, column = slot
            placed.setdefault(head, {}).setdefault(row, {})[column] = {
                "value": field.get("value"),
                "confidence": field.get("confidence"),
                "source": field.get("source", "printed"),
            }
    gone = {view_slot(path) for path in retired} - {None}

    def kept(head, row, cells):
        if not isinstance(cells, dict):
            return {}
        return {column: cell for column, cell in cells.items() if (head, row, column) not in gone}

    if "header" in placed or isinstance(document.get("header"), dict):
        document["header"] = {
            **kept("header", None, document.get("header")),
            **placed.get("header", {}).get(None, {}),
        }
    for key in ("lines", "accessorials"):
        stored = document.get(key) if isinstance(document.get(key), list) else []
        rows = placed.get(key, {})
        if not rows and key not in document:
            continue
        size = max([len(stored), *(row + 1 for row in rows)])
        document[key] = [
            {
                **kept(key, index, stored[index] if index < len(stored) else None),
                **rows.get(index, {}),
            }
            for index in range(size)
        ]
    return document


def views_out_of_step(document):
    """Where a document's views and its fields disagree.

    `behind` names each field whose value its view cell does not hold, and
    `view_only` each view cell holding a value no field does.
    """
    fields = document.get("fields") or {}
    cells = dict(view_cells(document))
    behind = [
        path
        for path, field in fields.items()
        if isinstance(field, dict)
        and view_slot(path)
        and field.get("value") not in (None, "")
        and view_value(cells.get(path)) != field.get("value")
    ]
    view_only = [
        path
        for path, cell in cells.items()
        if view_value(cell) not in (None, "")
        and (not isinstance(fields.get(path), dict) or fields[path].get("value") in (None, ""))
    ]
    return {"behind": sorted(behind), "view_only": sorted(view_only)}


def merge_document(doc_id, engine_records, handwriting_policy="strict"):
    """engine_records: {engine_name: record_dict}"""
    provider_exceptions = [
        {
            "document_id": doc_id,
            "field": "page",
            "flag": "provider_exception",
            "rule": "provider_must_return_a_valid_extraction",
            "is_handwritten": False,
            "accepted": False,
            "candidates": [],
            "engines": {eng: rec["_provider_exception"]},
            "cause": "provider_extraction_exception",
        }
        for eng, rec in engine_records.items()
        if rec.get("_provider_exception")
    ]
    flat_by_engine = {
        eng: flatten(
            {
                key: value
                for key, value in rec.items()
                if key not in ("_provider_exception", "_verified_identity")
            }
        )
        for eng, rec in engine_records.items()
    }
    all_paths = sorted({p for f in flat_by_engine.values() for p in f})
    consensus_paths = [path for path in all_paths if not is_extension_path(path)]
    extension_paths = [path for path in all_paths if is_extension_path(path)]

    fields, exceptions = {}, list(provider_exceptions)
    for path in consensus_paths:
        entries = [(eng, f[path]) for eng, f in flat_by_engine.items() if path in f]
        # A field one engine found and another did not is itself a disagreement
        # worth surfacing, not a field to quietly take from whoever saw it.
        missing = [eng for eng in flat_by_engine if path not in flat_by_engine[eng]]
        if derived_ordinal_path(path):
            fields[path] = derive_ordinal(path, entries)
            continue
        field = reconcile_field(path, entries, handwriting_policy)
        if missing:
            field["missing_from_engines"] = missing
            if field["consensus_flag"] not in ("no_consensus",):
                field["queue_for_review"] = True
        fields[path] = field
        if field["queue_for_review"]:
            exceptions.append(
                {
                    "document_id": doc_id,
                    "field": path,
                    "flag": field["consensus_flag"],
                    "rule": field["rule"],
                    "is_handwritten": field["is_handwritten"],
                    "accepted": field["accepted"],
                    "candidates": field["candidate_values"] or [field["value"]],
                    "engines": {
                        eng: unwrap(f[path])[0] for eng, f in flat_by_engine.items() if path in f
                    },
                    "blocking": field.get("blocking", True),
                    "cause": (
                        "no_majority"
                        if field["consensus_flag"] == "no_consensus"
                        else "partial_disagreement"
                    ),
                }
            )

    extension_proposals = {
        eng: {path: flat[path] for path in extension_paths if path in flat}
        for eng, flat in flat_by_engine.items()
    }
    extension_proposals = {eng: paths for eng, paths in extension_proposals.items() if paths}

    # Rule 9: a document whose engines returned only extension entries has not
    # been through consensus at all. Excluding that channel must not turn such a
    # document into a clean result, so it becomes an explicit exception naming
    # what was retained instead.
    extension_only = bool(extension_paths) and not consensus_paths
    if extension_only:
        exceptions.append(
            {
                "document_id": doc_id,
                "field": EXTENSION_ROOT,
                "flag": "no_consensus_eligible_field",
                "rule": "consensus_requires_a_controlled_vocabulary_field",
                "is_handwritten": False,
                "accepted": False,
                "candidates": [],
                "engines": {eng: len(paths) for eng, paths in extension_proposals.items()},
                "blocking": True,
                "cause": "only_source_labelled_extension_fields_returned",
            }
        )

    accepted = sum(1 for f in fields.values() if f["accepted"])
    hard = sum(
        1
        for f in fields.values()
        if f["consensus_flag"] == "no_consensus" and f.get("blocking", True)
    )
    hw = sum(1 for f in fields.values() if f["is_handwritten"])

    if hard or provider_exceptions or extension_only:
        status = "open_exception"
    elif exceptions:
        status = "review_queued"
    else:
        status = "auto_accepted"

    merged = {
        "document_id": doc_id,
        "engines": sorted(engine_records.keys()),
        "review_status": status,
        "field_count": len(fields),
        "accepted_field_count": accepted,
        "hard_exception_count": hard,
        "handwritten_field_count": hw,
        # Retained verbatim per engine, never merged and never accepted. These
        # are the mapping proposals schema_discovery turns into review work.
        "source_labelled_field_proposals": extension_proposals,
        "source_labelled_field_proposal_count": sum(
            len(paths) for paths in extension_proposals.values()
        ),
        "consensus_flag": (
            "no_consensus" if hard else f"consensus_{len(engine_records)}of{len(engine_records)}"
        ),
        "fields": fields,
    }
    identities = [
        rec["_verified_identity"]
        for rec in engine_records.values()
        if isinstance(rec.get("_verified_identity"), dict)
    ]
    if identities:
        merged["engine_identities"] = sorted(identities, key=lambda item: item["lane"])
    # Carry the record shape alongside the field detail so the rest of the
    # pipeline can consume this file directly.
    merged.update(unflatten(fields))
    # Document type is a voted fact, not transport metadata. Using the first
    # engine's raw value here would contradict a 2-of-3 majority and could send
    # the record through the wrong downstream schema before review. A tie
    # remains explicitly unknown until its exception is resolved.
    document_type = fields.get("document_type")
    if document_type is not None:
        merged["document_type"] = (
            document_type.get("value") if document_type.get("accepted") else "unknown"
        )
    for passthrough in (
        "branch",
        "has_handwriting",
        "jbig2_suspect",
        "amendments",
        "handwriting_regions",
    ):
        for rec in engine_records.values():
            if passthrough in rec:
                val = rec[passthrough]
                merged[passthrough] = (
                    val.get("value") if isinstance(val, dict) and "value" in val else val
                )
                break
    merged["handwriting_policy"] = handwriting_policy
    merged["handwriting_comments"] = [
        {"field": path, **field["handwriting_comment"]}
        for path, field in fields.items()
        if field.get("handwriting_comment")
    ]
    return merged, exceptions


# Above this share of fields in exception, a consensus is not a queue to work but
# a signal that both engines were reading without the vocabulary the corpus uses.
# The threshold is deliberately far above the 5-15% working band: it marks the
# point where hand-working the queue stops being the cheaper answer.
CONTEXT_UPDATE_THRESHOLD_PCT = 50.0


def handoff_corpus_context(paths):
    """Return the corpus context shared by every lane, or ``None``.

    ``load_records`` has already refused a set of handoffs that disagree, so any
    one of them answers for all. Reading it separately keeps ``load_records``'
    signature unchanged for the other commands that reuse it.
    """
    for path in paths:
        with Path(path).open() as fh:
            context = json.load(fh).get("corpus_context")
        if context:
            return {k: v for k, v in context.items() if k != "text"}
    return None


def load_records(paths, handwriting_policy="strict"):
    """
    Returns {document_id: {engine: record}}.

    Each input must be a hash-bound independent extraction handoff. Provider
    identity is derived from the adapter engine, never from a filename or a
    caller-supplied label.
    """
    by_doc = defaultdict(dict)
    seen_groups = {}
    seen_vendors = set()
    seen_lanes = set()
    allowed_lanes = {"consensus_primary", "consensus_secondary", "consensus_tiebreaker"}
    # Every provider a consensus lane may be configured for. A provider absent
    # here is refused as "unsupported", which is correct for an unknown engine
    # and wrong for a supported one: an Anthropic secondary lane read a whole
    # corpus and then could not be reconciled with the primary at all.
    provider_prefixes = {
        "openai": "openai/",
        "google": "google_genai_vertex/",
        "openrouter": "openrouter/",
        "anthropic": "anthropic/",
    }
    seen_contexts = []
    for path in paths:
        path = Path(path)
        with path.open() as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError(f"{path}: handoff must be a JSON object")
        if data.get("schema_version") != "independent_extraction_handoff_v1":
            raise ValueError(f"{path}: invalid schema_version")
        if data.get("adapter_type") != "ocr":
            raise ValueError(f"{path}: adapter_type must be ocr")
        provider = data.get("provider")
        engine = data.get("engine")
        model = data.get("model")
        if provider not in provider_prefixes or not isinstance(engine, str):
            raise ValueError(f"{path}: unsupported provider or engine")
        prefix = provider_prefixes[provider]
        if not engine.startswith(prefix) or engine == prefix:
            raise ValueError(f"{path}: provider does not match engine")
        derived_model = engine[len(prefix) :]
        if not isinstance(model, str) or model != derived_model:
            raise ValueError(f"{path}: model does not match engine")
        configuration = data.get("model_configuration")
        if isinstance(configuration, dict) and configuration.get("model") != model:
            raise ValueError(f"{path}: model_configuration does not match model")
        group = data.get("independence_group")
        if group != provider:
            raise ValueError(f"{path}: independence_group must equal verified provider")
        # Independence must survive a router. Two lanes reaching the same weights
        # through different transports are one reading, not two.
        vendor = model_vendor(provider, model)
        if vendor == UNRESOLVED_MODEL_VENDOR:
            raise ValueError(
                f"{path}: routed model vendor cannot be resolved from {model!r}; "
                "a vendor-prefixed model slug is required for independence"
            )
        if vendor in seen_vendors:
            raise ValueError(
                f"{path}: duplicate independence group {vendor!r}; "
                "a router does not create independence"
            )
        # Two lanes may share a router when they are different vendors: x-ai's
        # weights and Google's are not one reading because one company billed
        # for both. What would make them one reading is a shared reader -- with
        # OpenRouter's default PDF handling a single `mistral-ocr` pass feeds
        # both models, and they then inherit its errors identically. So a shared
        # router is allowed only where every lane on it read the source itself.
        # An absent declaration is unknown, and unknown fails closed.
        reader = data.get("source_read_by")
        # Membership, not the stored value: a lane that declared nothing stores
        # None, and `.get` would then be indistinguishable from an unseen router
        # -- letting exactly the undeclared pair through that must fail closed.
        previous = seen_groups.get(group)
        if group in seen_groups and not (
            reader == SOURCE_READ_BY_MODEL and previous == SOURCE_READ_BY_MODEL
        ):
            raise ValueError(
                f"{path}: {group!r} already carries a lane, and independence through one "
                f"transport requires every lane to have read the source itself; this lane "
                f"declares {reader!r} and the earlier one declared {previous!r}"
            )
        seen_vendors.add(vendor)
        lane = data.get("lane")
        if lane not in allowed_lanes:
            raise ValueError(f"{path}: invalid consensus lane")
        if lane in seen_lanes:
            raise ValueError(f"{path}: duplicate consensus lane {lane!r}")
        if data.get("raw_response_retention_required") is not True:
            raise ValueError(f"{path}: raw response retention must be required")
        # Both lanes must have been conditioned by exactly the same corpus notes.
        # Telling one engine what a label means and not the other does not produce
        # two independent readings of the page -- it produces one reading and one
        # coached reading, and their agreement would measure the coaching.
        context = data.get("corpus_context")
        if context is not None and not isinstance(context, dict):
            raise ValueError(f"{path}: corpus_context must be an object or null")
        context_sha = None if context is None else context.get("sha256")
        seen_contexts.append((path, context_sha))
        first_path, first_sha = seen_contexts[0]
        if context_sha != first_sha:
            raise ValueError(
                f"{path}: corpus context {context_sha!r} differs from "
                f"{first_path}: {first_sha!r}; lanes conditioned by different "
                "notes are not independent readings"
            )
        records = data.get("records")
        if not isinstance(records, list):
            raise ValueError(f"{path}: records must be a list")
        digest = hashlib.sha256(
            json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        handoff_identity = {
            "provider": provider,
            "model": model,
            "engine": engine,
            "lane": lane,
            "independence_group": group,
            "model_vendor": vendor,
            "source_read_by": data.get("source_read_by"),
            "handoff_file": str(path),
            "handoff_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for rec in records:
            if not isinstance(rec, dict):
                raise ValueError(f"record in {path} must be an object")
            if rec.get("engine") != engine or rec.get("engine_version") != model:
                raise ValueError(f"record engine in {path} does not match handoff")
            raw_response = rec.get("raw_response")
            raw_sha = rec.get("raw_response_sha256")
            if not isinstance(raw_response, str) or not raw_response.strip():
                raise ValueError(f"record in {path} has no retained raw response")
            raw_path = resolve_raw_response_path(path, data, raw_response)
            if not raw_path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", str(raw_sha or "")):
                raise ValueError(f"record in {path} has invalid raw response evidence")
            if hashlib.sha256(raw_path.read_bytes()).hexdigest() != raw_sha:
                raise ValueError(f"record in {path} has a raw response hash mismatch")
            if not re.fullmatch(r"[0-9a-f]{64}", str(rec.get("page_sha256") or "")):
                raise ValueError(f"record in {path} has invalid page_sha256")
            doc_id = rec.get("document_id") or rec.get("id")
            if not doc_id:
                raise ValueError(f"record in {path} has no document_id; refusing to drop it")
            if engine in by_doc[doc_id]:
                raise ValueError(
                    f"duplicate engine {engine!r} for document {doc_id!r}; "
                    "refusing to overwrite an extraction"
                )
            payload = {
                k: v
                for k, v in rec.items()
                if k
                not in (
                    "engine",
                    "engine_version",
                    "document_id",
                    "id",
                )
                and k not in CONSENSUS_METADATA_KEYS
            }
            flags = rec.get("provider_review_flags", [])
            if handwriting_policy == "comment_only":
                flags = [
                    flag
                    for flag in flags
                    if "handwrit"
                    not in str(flag.get("reason", "") if isinstance(flag, dict) else flag).lower()
                ]
            provider_blocked = rec.get("review_status") == "open_exception" or bool(flags)
            if provider_blocked:
                payload["_provider_exception"] = {
                    "review_status": rec.get("review_status"),
                    "provider_review_flags": flags,
                }
            payload["_verified_identity"] = handoff_identity
            by_doc[doc_id][engine] = payload
        if data.get("records_sha256") != digest:
            raise ValueError(f"{path}: records hash mismatch")
        seen_groups[group] = data.get("source_read_by")
        seen_lanes.add(lane)
    return by_doc


def main():
    load_project_env()
    ap = argparse.ArgumentParser(description="Merge multi-engine extractions into consensus.")
    ap.add_argument("inputs", nargs="+", help="one JSON file per engine")
    ap.add_argument("--out", default="consensus.json")
    ap.add_argument("--exceptions", default="exceptions.json")
    ap.add_argument(
        "--handwriting-policy",
        choices=("strict", "comment_only"),
        default=None,
        help="strict gates handwritten fields; comment_only preserves them as non-blocking observations",
    )
    ap.add_argument("--quiet", action="store_true")
    apply_shared_help(ap)
    args = ap.parse_args()

    handwriting_policy = args.handwriting_policy or env_value("HANDWRITING_POLICY", "comment_only")
    try:
        by_doc = load_records(args.inputs, handwriting_policy)
        context_identity = handoff_corpus_context(args.inputs)
    except ValueError as exc:
        sys.exit(f"Consensus failed: {exc}")
    if not by_doc:
        sys.exit("No records with a document_id found.")

    documents, all_exceptions = [], []
    single_engine_docs = []
    for doc_id, engines in sorted(by_doc.items()):
        if len(engines) < 2:
            single_engine_docs.append(doc_id)
        merged, exc = merge_document(doc_id, engines, handwriting_policy)
        documents.append(merged)
        all_exceptions.extend(exc)

    total_fields = sum(d["field_count"] for d in documents)
    auto = sum(1 for d in documents if d["review_status"] == "auto_accepted")
    queued = sum(1 for d in documents if d["review_status"] == "review_queued")
    openx = sum(1 for d in documents if d["review_status"] == "open_exception")

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "documents": len(documents),
        "fields": total_fields,
        "auto_accepted_documents": auto,
        "review_queued_documents": queued,
        "open_exception_documents": openx,
        "exception_fields": len(all_exceptions),
        "exception_rate_pct": round(100.0 * len(all_exceptions) / total_fields, 2)
        if total_fields
        else 0.0,
        "single_engine_documents": len(single_engine_docs),
        "source_labelled_field_proposals": sum(
            d["source_labelled_field_proposal_count"] for d in documents
        ),
    }

    warnings = []
    if single_engine_docs:
        warnings.append(
            f"{len(single_engine_docs)} documents were extracted by only one engine. "
            "These cannot reach consensus and are not eligible for auto-acceptance -- "
            "the sampling-only QA argument does not cover them."
        )
    if summary["source_labelled_field_proposals"]:
        warnings.append(
            f"{summary['source_labelled_field_proposals']} source-labelled extension "
            "entries were retained as mapping proposals and were not reconciled. They "
            "are not canonical fields and no consensus was claimed over them. The whole "
            "path is schema_discovery.py discover, then an explicit approval through "
            "registry-update, then apply_mappings.py to carry an approved rule into the "
            "controlled field. Stopping at discovery leaves every one of these values "
            "invisible to attribution, completeness, and the inferred controls, which "
            "read document records rather than mapping proposals."
        )
    hw_exc = sum(1 for e in all_exceptions if e["is_handwritten"])
    if hw_exc:
        warnings.append(
            f"{hw_exc} exception fields are handwritten. Expected -- the 3-of-3 "
            "numeric rule is deliberately strict. Work these before anything else; "
            "handwritten amendments usually carry the largest per-record impact."
        )
    if summary["exception_rate_pct"] > 15:
        warnings.append(
            f"Exception rate {summary['exception_rate_pct']}% is above the typical "
            "5-15% band. Check whether the engines are configured for the same "
            "document type before working the queue by hand."
        )
    # A corpus reading this divergent is not a queue to work by hand: at half the
    # fields in exception, most of them are fields only one engine saw at all, and
    # every downstream control inherits that. The run is required to take a
    # context-aware extraction update before it can be called complete. This is
    # recorded in the artifact rather than left to the operator to remember, so a
    # future engagement cannot pass the closing gate by forgetting it.
    summary["context_aware_update_threshold_pct"] = CONTEXT_UPDATE_THRESHOLD_PCT
    summary["corpus_context"] = context_identity
    summary["context_aware_update_required"] = bool(
        summary["exception_rate_pct"] > CONTEXT_UPDATE_THRESHOLD_PCT and context_identity is None
    )
    if summary["context_aware_update_required"]:
        warnings.append(
            f"Exception rate {summary['exception_rate_pct']}% exceeds the "
            f"{CONTEXT_UPDATE_THRESHOLD_PCT}% context-aware update threshold and "
            "neither lane was conditioned by a corpus context. Build one with "
            "extraction_context.py and re-extract both lanes through "
            "LLM_CORPUS_CONTEXT before treating this consensus as the run's "
            "reading. run_lane_coverage.py fails until that update exists."
        )
    summary["warnings"] = warnings or ["No structural warnings."]

    with open(args.out, "w") as fh:
        json.dump({"summary": summary, "documents": documents}, fh, indent=2)
    with open(args.exceptions, "w") as fh:
        json.dump(
            {"summary": {"count": len(all_exceptions)}, "exceptions": all_exceptions}, fh, indent=2
        )

    if not args.quiet:
        print(f"Documents: {summary['documents']}   Fields: {summary['fields']}")
        print(f"  auto-accepted : {auto}")
        print(f"  review queued : {queued}")
        print(f"  open exception: {openx}")
        print(f"  exception rate: {summary['exception_rate_pct']}% of fields")
        for w in summary["warnings"]:
            print(f"  - {w}")
        print(f"\nWritten to {args.out} and {args.exceptions}")


if __name__ == "__main__":
    main()
