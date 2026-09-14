#!/usr/bin/env python3
"""Corroborate a single reading against an independent extractor's own reading.

Consensus accepts a field where two independent model vendors agreed. Where only
one engine read a field, the finding is `single_engine` -- not a disagreement,
an absence. On one run that was 52,528 findings, 94.9% of them line-item cells,
and no amount of tolerance would close them: nothing had read the field twice.

Something had. Google Document AI ran over the same corpus, retained its own
table evidence, and was never consulted, because
`independent_table_reconcile.py` matches a mapped row against a table cell and
Document AI merges whole logical rows into one cell -- `"12497\\nPonte
Gaeda\\n33,144.35\\n..."` -- so cell-identity matching found nothing. Value
presence finds it. That was a matching defect, not absent evidence.

This lane asks one question per finding: does the value the engine read appear
in what an independent extractor read from the same page? It answers it two
ways, and both are recorded:

* a **corroboration** -- one engine read a value and an independent extractor's
  own reading of that page contains it;
* a **tie-break** -- two engines disagreed and exactly one of the candidates
  appears in that evidence.

What this is not
----------------

This is **not vendor agreement**, and nothing here may be presented as it.
Presence proves the string is printed on that page. It does not prove the string
belongs to that row's column, because the evidence is matched over the page
rather than the cell. It therefore defeats a **misread** and does not defeat a
**misattribution**: an engine that read `5,193.60` correctly but filed it under
the wrong column is corroborated by this lane and still wrong.

`occurrences` is recorded for that reason. A value appearing exactly once on a
page is nearly as located as a cell match; a value appearing twenty times is
weak evidence about which row it belongs to. Read it before treating a
corroboration as settling a field.

A short value cannot be matched as a substring without matching inside longer
numbers, so anything under `MINIMUM_SUBSTRING_LENGTH` characters is matched as a
whole token or not at all.

This lane resolves nothing. It writes evidence and exceptions; accepting a value
on that evidence is a separate, authorized step.
"""

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from run_io import empty_output_path
from runtime_config import load_project_env

ARTIFACT_TYPE = "independent_corroboration_v1"
EVIDENCE_KIND = "corroborated_by_independent_extractor"

# Below this length a value is matched as a whole token: "2" as a substring
# appears inside "1,234" and would corroborate almost anything.
MINIMUM_SUBSTRING_LENGTH = 3

# Separators a printed figure carries and a reading may not: thousands commas,
# currency marks, and whitespace. Decimal points and signs are kept, because
# they are what distinguishes one amount from another.
SEPARATORS = re.compile(r"[\s,$£€]")
TOKEN = re.compile(r"[a-z0-9().%/-]+")


def normalize(value):
    """Reduce a value to what two readers of the same glyphs should share."""
    if value is None:
        return ""
    return SEPARATORS.sub("", str(value)).strip().casefold()


def page_evidence(paths):
    """Read every independent extractor artifact into per-page evidence.

    More than one artifact is normal and matters: on one run the main Document
    AI artifact carried tables for 459 pages and its recovery pass carried them
    for 677, covering 708 of 716 pages between them. Measuring against either
    alone understates corroboration by roughly half.
    """
    raw = {}
    for path in paths:
        data = json.loads(Path(path).read_text())
        records = data.get("records") if isinstance(data, dict) else data
        if not isinstance(records, list):
            raise ValueError(f"not an extractor artifact with records: {path}")
        for record in records:
            page = record.get("page_id") or record.get("document_id")
            if not page:
                continue
            parts = raw.setdefault(page, [])
            for table in record.get("source_tables") or []:
                for row in table.get("source_rows") or []:
                    for cell in row.get("cells") or []:
                        parts.append(cell.get("evidence_text") or "")
            parts.append(record.get("document_text") or "")
    evidence = {}
    for page, parts in raw.items():
        text = " ".join(parts)
        normalized = normalize(text)
        if not normalized:
            continue
        evidence[page] = {
            "text": normalized,
            "tokens": Counter(TOKEN.findall(text.lower().replace(",", ""))),
        }
    return evidence


def presence(value, evidence):
    """Say whether an independent reading of the page contains this value.

    Returns the match mode and how many times it occurs, because a value seen
    once on a page is far better located than one seen twenty times.
    """
    normalized = normalize(value)
    if not normalized:
        return None, 0
    if len(normalized) < MINIMUM_SUBSTRING_LENGTH:
        count = evidence["tokens"].get(normalized, 0)
        return ("token", count) if count else (None, 0)
    count = evidence["text"].count(normalized)
    return ("substring", count) if count else (None, 0)


def corroborate(findings, evidence):
    """Weigh every finding against the independent evidence for its page."""
    corroborations, tie_breaks, unresolved = [], [], []
    for finding in findings:
        page = finding.get("document_id")
        field = finding.get("field")
        candidates = [c for c in (finding.get("candidates") or []) if c is not None]
        available = evidence.get(page)
        if available is None:
            unresolved.append({**_stub(page, field), "reason": "no_independent_evidence_for_page"})
            continue
        if len(candidates) == 1:
            mode, count = presence(candidates[0], available)
            if mode is None:
                unresolved.append(
                    {**_stub(page, field), "reason": "value_absent_from_independent_evidence"}
                )
                continue
            corroborations.append(
                {
                    **_stub(page, field),
                    "value": candidates[0],
                    "evidence": EVIDENCE_KIND,
                    "match_scope": "page",
                    "match_mode": mode,
                    "occurrences": count,
                }
            )
            continue
        if len(candidates) == 2:
            found = [(c, *presence(c, available)) for c in candidates]
            present = [item for item in found if item[1] is not None]
            if len(present) == 1:
                value, mode, count = present[0]
                tie_breaks.append(
                    {
                        **_stub(page, field),
                        "candidates": candidates,
                        "supported_value": value,
                        "evidence": EVIDENCE_KIND,
                        "match_scope": "page",
                        "match_mode": mode,
                        "occurrences": count,
                    }
                )
                continue
            unresolved.append(
                {
                    **_stub(page, field),
                    "candidates": candidates,
                    "reason": "both_candidates_present"
                    if len(present) == 2
                    else "neither_candidate_present",
                }
            )
            continue
        unresolved.append(
            {**_stub(page, field), "reason": "no_candidate_value_to_weigh_against_evidence"}
        )
    return corroborations, tie_breaks, unresolved


def _stub(page, field):
    return {"document_id": page, "field": field}


def build(findings, evidence, sources, reference=None):
    """Assemble the retained corroboration artifact."""
    corroborations, tie_breaks, unresolved = corroborate(findings, evidence)
    return {
        "artifact_type": ARTIFACT_TYPE,
        "generated_at": datetime.now(UTC).isoformat(),
        "reference_engine": reference,
        "evidence_sources": [str(path) for path in sources],
        "evidence_pages": len(evidence),
        "summary": {
            "findings_examined": len(findings),
            "corroborated": len(corroborations),
            "tie_broken": len(tie_breaks),
            "unresolved": len(unresolved),
            "unresolved_reasons": dict(Counter(item["reason"] for item in unresolved)),
        },
        "corroborations": corroborations,
        "tie_breaks": tie_breaks,
        # Never presentable as agreement, and never as a resolution.
        "limits": (
            "Presence proves the value is printed on the page, not that it belongs to "
            "that row's column: this defeats a misread and not a misattribution. It is "
            "not independent model vendor agreement and must not be recorded as it. "
            "Read `occurrences` before treating a corroboration as settling a field. "
            "This artifact resolves nothing; accepting a value on it is a separate, "
            "authorized step."
        ),
    }, unresolved


def load_findings(path):
    """Read the consensus exception artifact this lane weighs."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get("exceptions"), list):
        raise ValueError(f"not an exception artifact with an exceptions list: {path}")
    return data["exceptions"]


def main(argv=None):
    """Weigh single readings and disagreements against independent evidence."""
    load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("findings", help="Consensus exception artifact.")
    parser.add_argument(
        "evidence",
        nargs="+",
        help=(
            "Independent extractor artifacts. Pass every one the run retained: a "
            "recovery pass commonly covers pages the first pass did not."
        ),
    )
    parser.add_argument("--out", required=True, help="New corroboration artifact path.")
    parser.add_argument("--exceptions", required=True, help="New exception artifact path.")
    parser.add_argument("--reference-engine", help="Engine whose readings are being weighed.")
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args(argv)
    try:
        findings = load_findings(args.findings)
        evidence = page_evidence(args.evidence)
        if not evidence:
            raise ValueError(
                "the independent artifacts carried no page evidence; a control that "
                "processed nothing has not passed"
            )
        result, unresolved = build(findings, evidence, args.evidence, args.reference_engine)
        empty_output_path(Path(args.out)).write_text(json.dumps(result, indent=2) + "\n")
        empty_output_path(Path(args.exceptions)).write_text(
            json.dumps({"summary": {"count": len(unresolved)}, "exceptions": unresolved}, indent=2)
            + "\n"
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Independent corroboration failed: {exc}")
    if not args.quiet:
        summary = result["summary"]
        print(f"findings examined : {summary['findings_examined']:,}")
        print(f"  corroborated    : {summary['corroborated']:,}")
        print(f"  tie broken      : {summary['tie_broken']:,}")
        print(f"  unresolved      : {summary['unresolved']:,}")
        for reason, count in sorted(summary["unresolved_reasons"].items()):
            print(f"      {reason}: {count:,}")
        print("This is not vendor agreement and resolves nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
