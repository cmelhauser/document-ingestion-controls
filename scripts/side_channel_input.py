#!/usr/bin/env python3
"""One ingestion contract for client-supplied side-channel inputs.

Document-side controls enumerate everything they could not process: attribution
registers every unattributed dollar, completeness registers every unresolved
item. The inputs that arrive from the client rather than the scanner -- GL
exports, payment files, reference exports -- reached that standard one module at
a time, and a bare ``continue`` in any of them silently shrinks the very baseline
the control is measuring against.

Every loader here returns ``(accepted, rejected)``. A caller must write
``rejected`` into its artifact; ``scripts/release_check.py`` enforces that the
loaders keep their registers.
"""

import re
from pathlib import Path


def first_populated(*values):
    """Return the first value that is present and not blank.

    A plain ``or`` chain discards a legitimate zero; testing only for ``None``
    refuses to fall through when an export carries two columns and the first is
    blank. Both are real export shapes, so both are handled.
    """
    for value in values:
        if value is not None and str(value).strip():
            return value
    return None


# A client export names its key column the way the client's system prints it:
# "Job #", "ACK No.", "Invoice Number". Matching only exact snake_case rejected
# every row of an otherwise perfect export and made the operator rename columns
# by hand, which edits client evidence before it is read.
COLUMN_NUMBER_ALIASES = ("no", "no_", "num", "nbr", "nr")


def normalized_column_name(name):
    """Reduce a printed column heading to a comparable key.

    Punctuation collapses to a single separator, so ``Job #`` and ``Job No.``
    become ``job`` and ``job_no``. The value is untouched and the original
    heading is never replaced -- this is a lookup key, not a rename.
    """
    key = re.sub(r"[^a-z0-9]+", "_", str(name).strip().casefold()).strip("_")
    return key


def column_lookup_keys(name):
    """Return every key a heading should answer to, most literal first.

    ``Job No.`` must reach a loader asking for ``job_number`` without that loader
    enumerating every abbreviation a finance system might print. Expansion is
    deliberately narrow: only a heading that *says* it is a number expands, by a
    trailing abbreviation or a literal ``#``.

    Expanding every single-word heading was tried and is wrong. It invents
    ``amount_number`` from ``Amount``, and worse, an export carrying both
    ``Customer`` and ``Customer Number`` would let the name column answer to the
    identifier key and be read as the identifier.
    """
    raw = str(name).strip()
    key = normalized_column_name(raw)
    keys = [key]
    parts = key.split("_")
    if len(parts) > 1 and parts[-1] in COLUMN_NUMBER_ALIASES:
        keys.append("_".join([*parts[:-1], "number"]))
    elif key and "#" in raw:
        keys.append(f"{key}_number")
    return keys


def normalized_columns(row):
    """Normalize column names for lookup while leaving values untouched.

    Each heading is registered under every key it should answer to. An earlier
    column wins a collision, so an export carrying both ``job`` and ``Job #``
    keeps the explicit one.
    """
    result = {}
    for key, value in row.items():
        for candidate in column_lookup_keys(key):
            result.setdefault(candidate, value)
    return result


def lowercase_columns(row):
    """Normalize column names for lookup while leaving values untouched."""
    return normalized_columns(row)


def rejection(index, reason, **evidence):
    """Build a rejection record that retains the row's raw content."""
    return {
        "row_index": index,
        "reason": reason,
        **{key: "" if value is None else str(value) for key, value in evidence.items()},
        "disposition": "client_review_required",
    }


def load_rows(path, *, extractor, source_kind):
    """Apply ``extractor`` to each row, returning accepted values and rejections.

    ``extractor(index, columns)`` returns ``(value, None)`` for an accepted row or
    ``(None, rejection(...))`` for one that cannot be used. No row may be dropped
    without producing one or the other.
    """
    accepted, rejected = [], []
    for index, row in enumerate(rows_from(path, source_kind)):
        value, refusal = extractor(index, lowercase_columns(row))
        if refusal is not None:
            rejected.append(refusal)
            continue
        accepted.append(value)
    return accepted, rejected


def rows_from(path, source_kind):
    """Read CSV or JSON rows from a client-supplied export."""
    import csv
    import json

    path = Path(path)
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            for key in ("entries", "rows", "acks", "payments", source_kind):
                if isinstance(data.get(key), list):
                    return data[key]
            raise ValueError(f"{source_kind} JSON object has no recognized rows list")
        if not isinstance(data, list):
            raise ValueError(f"{source_kind} JSON must be a list or an object with rows")
        return data
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))
