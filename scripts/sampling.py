#!/usr/bin/env python3
"""
Phase 4Q -- Sampling QA.

Builds the two sample plans and computes the accuracy statement.

The purpose is not to feel confident about the data. It is to produce a
defensible one-sided monetary-unit-sampling bound on overstatement. It does not
bound understatement and it does not prove corpus completeness; those require
separate controls.

Two schemes, both drawing only from the auto-accepted population. Exceptions are
worked exhaustively and sit outside the sampling frame.

  monetary unit sampling  -- selection proportional to document value, so the
                             sample lands where the dollars are. A $10,000
                             invoice is 200x more likely to be drawn than a $50
                             one, which is correct: that is where the risk is.

  stratified attribute    -- per field per stratum, answering "which categories
                             are unreliable" rather than "how many dollars are
                             wrong". Handwriting strata are oversampled 3-5x in
                             Branch A and 8-10x in Branch B.

Run once to build the plan, then again with --findings after review to compute
the projection and the upper bound.

Usage:
    python sampling.py proofed.json --materiality 5000 --out sample_plan.json
    python sampling.py proofed.json --materiality 5000 --findings findings.json \
        --out accuracy_statement.json
"""

import argparse
import hashlib
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime

from cli_help import apply_shared_help
from document_value import basis_summary, document_value

# MUS confidence factors (Poisson reliability factors) at 95% confidence, by
# number of overstatement findings. Index 0 = zero errors, which is where the
# rule of three comes from.
CONFIDENCE_FACTORS_95 = [3.00, 4.75, 6.30, 7.76, 9.16, 10.52, 11.85, 13.15, 14.44, 15.71, 16.97]

HANDWRITING_OVERSAMPLE = {"A": 4, "B": 9, "B-rescan": 4}


def sample_plan_sha256(plan):
    """Hash the reproducible selection contract, excluding presentation fields."""
    stable = {
        key: value
        for key, value in plan.items()
        if key not in {"generated_at", "notes", "sample_plan_sha256", "accuracy_statement"}
    }
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def reconcile_review_results(data, expected_plan_hash, certainty_docs, sampled_docs):
    """Require one explicit, hash-bound review outcome for every MUS selection."""
    selected = {
        str(item["document_id"]): float(item["value"]) for item in [*certainty_docs, *sampled_docs]
    }
    result = {
        "status": "blocked",
        "findings": [],
        "missing_document_ids": [],
        "extra_document_ids": [],
        "duplicate_document_ids": [],
        "unresolved_document_ids": [],
        "invalid_outcomes": [],
        "plan_hash_mismatch": False,
    }
    if not isinstance(data, dict) or data.get("schema_version") != "sample_review_results_v1":
        result["invalid_outcomes"].append("invalid sample review result schema")
        result["missing_document_ids"] = sorted(selected)
        return result
    result["plan_hash_mismatch"] = data.get("sample_plan_sha256") != expected_plan_hash
    outcomes = data.get("outcomes")
    if not isinstance(outcomes, list):
        result["invalid_outcomes"].append("outcomes must be a list")
        result["missing_document_ids"] = sorted(selected)
        return result

    seen = set()
    complete = {}
    for index, item in enumerate(outcomes):
        if not isinstance(item, dict):
            result["invalid_outcomes"].append(f"outcomes[{index}] must be an object")
            continue
        doc_id = str(item.get("document_id") or "").strip()
        if not doc_id:
            result["invalid_outcomes"].append(f"outcomes[{index}] has no document_id")
            continue
        if doc_id in seen:
            result["duplicate_document_ids"].append(doc_id)
            continue
        seen.add(doc_id)
        if doc_id not in selected:
            result["extra_document_ids"].append(doc_id)
            continue
        recorded = _finite_number(item.get("recorded_value"))
        audited = _finite_number(item.get("audited_value"))
        status = item.get("outcome")
        if recorded is None or recorded != selected[doc_id]:
            result["invalid_outcomes"].append(f"{doc_id}: recorded_value does not match plan")
            continue
        if status in {"abstained", "failed"}:
            result["unresolved_document_ids"].append(doc_id)
            continue
        if status not in {"reviewed_correct", "reviewed_error"} or audited is None:
            result["invalid_outcomes"].append(f"{doc_id}: invalid reviewed outcome")
            continue
        if (status == "reviewed_correct") != (audited == recorded):
            result["invalid_outcomes"].append(f"{doc_id}: outcome contradicts audited value")
            continue
        complete[doc_id] = {
            "document_id": doc_id,
            "recorded_value": recorded,
            "audited_value": audited,
        }

    result["missing_document_ids"] = sorted(set(selected) - seen)
    result["extra_document_ids"] = sorted(set(result["extra_document_ids"]))
    result["duplicate_document_ids"] = sorted(set(result["duplicate_document_ids"]))
    result["unresolved_document_ids"] = sorted(set(result["unresolved_document_ids"]))
    result["findings"] = [complete[doc_id] for doc_id in selected if doc_id in complete]
    blockers = any(
        result[key]
        for key in (
            "missing_document_ids",
            "extra_document_ids",
            "duplicate_document_ids",
            "unresolved_document_ids",
            "invalid_outcomes",
        )
    )
    if not blockers and not result["plan_hash_mismatch"] and len(complete) == len(selected):
        result["status"] = "completed"
    return result


def num(entry, default=0.0):
    """Parse a monetary value without inferring a unit or separator convention."""
    if entry is None:
        return default
    if isinstance(entry, dict):
        entry = entry.get("value")
    if entry is None:
        return default
    if isinstance(entry, (int, float)) and not isinstance(entry, bool):
        return float(entry)
    s = str(entry).strip().replace(",", "").replace("$", "").replace(" ", "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return default


def get(rec, key, default=None):
    """Read a document-level field from the record or its header."""
    header = rec.get("header", {})
    for src in (rec, header):
        if key in src:
            v = src[key]
            return v.get("value") if isinstance(v, dict) else v
    return default


def eligible(rec):
    """
    Only the auto-accepted population is sampled. Anything already in the
    exception queue is worked in full, so including it would both double the
    work and bias the projection.
    """
    status = rec.get("review_status")
    if status != "auto_accepted":
        return False, "not auto-accepted -- worked or resolved outside sampling frame"
    if rec.get("arithmetic_status") not in ("proved", "not_applicable"):
        return False, "arithmetic status is not proved/not_applicable -- outside sampling frame"
    return True, None


def stratum_of(rec):
    """Return the stratum a record belongs to, for sample allocation."""
    date = str(get(rec, "invoice_date") or get(rec, "bill_date") or "")
    year = date[:4] if len(date) >= 4 and date[:4].isdigit() else "unknown"
    return {
        "document_type": rec.get("document_type", "unknown"),
        "year": year,
        "branch": rec.get("branch", "unknown"),
        "has_handwriting": bool(rec.get("has_handwriting") or rec.get("handwriting_involved")),
        "jbig2_suspect": bool(rec.get("jbig2_suspect")),
    }


def stratum_key(s):
    """Return the stratum a record belongs to, for sample allocation."""
    return "|".join(
        [
            s["document_type"],
            s["year"],
            f"branch:{s['branch']}",
            f"hw:{int(s['has_handwriting'])}",
            f"jbig2:{int(s['jbig2_suspect'])}",
        ]
    )


def build_mus(population, materiality, target_n, seed):
    """
    Systematic probability-proportional-to-size selection.

    Everything at or above materiality is examined with certainty rather than
    sampled -- no projection is needed for a stratum that was fully examined.
    """
    rng = random.Random(seed)  # noqa: S311 - sampling seeds must be reproducible, not cryptographic
    certainty = [p for p in population if p["value"] >= materiality]
    remainder = [p for p in population if p["value"] < materiality]

    remaining_value = sum(p["value"] for p in remainder)
    if target_n <= 0 or remaining_value <= 0:
        return certainty, [], 0.0

    # A unit larger than the provisional interval can contain more than one
    # systematic selection point. Move those units to the fully examined top
    # stratum and recalculate until every sampled unit can be selected at most
    # once. The caller's materiality threshold remains an additional certainty
    # rule; it is not a substitute for this sampling-design requirement.
    sample_points = target_n
    while True:
        provisional_interval = remaining_value / sample_points
        interval_certainty = [p for p in remainder if p["value"] > provisional_interval]
        if not interval_certainty:
            break
        certainty.extend(interval_certainty)
        sample_points -= len(interval_certainty)
        selected_ids = {id(p) for p in interval_certainty}
        remainder = [p for p in remainder if id(p) not in selected_ids]
        remaining_value = sum(p["value"] for p in remainder)
        if remaining_value <= 0:
            return certainty, [], 0.0

    interval = remaining_value / sample_points
    start = rng.uniform(0, interval)

    selected, cumulative, hit, idx = [], 0.0, start, 0
    for p in remainder:
        cumulative += p["value"]
        while hit < cumulative and len(selected) < sample_points:
            sel = dict(p)
            sel["selection_point"] = round(hit, 2)
            selected.append(sel)
            hit += interval
        idx += 1
    return certainty, selected, interval


def build_attribute_sample(population, base_n, seed):
    """
    Stratified draw with handwriting oversampling.

    Sample sizes derive from the tolerable error rate, not from corpus size --
    n=300 gives the same ~1% bound whether the corpus is 10,000 pages or
    500,000. Worth stating to clients who expect QA cost to scale with volume.
    """
    rng = random.Random(seed + 1)  # noqa: S311 - sampling seeds must be reproducible, not cryptographic
    strata = defaultdict(list)
    for p in population:
        strata[stratum_key(p["stratum"])].append(p)

    plan = []
    for key, members in sorted(strata.items()):
        s = members[0]["stratum"]
        share = len(members) / len(population)
        n = max(5, int(round(base_n * share)))
        multiplier = 1
        if s["has_handwriting"]:
            multiplier = HANDWRITING_OVERSAMPLE.get(s["branch"], 5)
        if s["jbig2_suspect"]:
            multiplier = max(multiplier, 5)
        n = min(len(members), n * multiplier)

        drawn = rng.sample(members, n)
        plan.append(
            {
                "stratum": key,
                "attributes": s,
                "population": len(members),
                "population_share_pct": round(100 * share, 2),
                "sample_size": n,
                "oversample_multiplier": multiplier,
                "upper_error_bound_if_zero_findings_pct": round(300.0 / n, 2),
                "document_ids": [d["document_id"] for d in drawn],
            }
        )
    plan.sort(key=lambda x: -x["population"])
    return plan


def project(findings, interval, certainty_docs, sampled_docs):
    """
    MUS projection.

      taint      = (recorded - audited) / recorded, per finding
      projected  = sum(taint) * interval
      upper      = projected + basic precision + incremental allowances

    Certainty-stratum errors are added at face value: that stratum was fully
    examined, so there is nothing to project.
    """
    sampled_ids = {d["document_id"] for d in sampled_docs}
    certainty_ids = {d["document_id"] for d in certainty_docs}

    taints, certainty_overstatement, observed_understatement, unmatched = [], 0.0, 0.0, []
    invalid: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    matched_certainty = 0
    matched_sample = 0
    for f in findings:
        doc_id = str(f.get("document_id") or "").strip()
        recorded = num(f.get("recorded_value"), None)
        audited = num(f.get("audited_value"), None)
        if not doc_id or recorded is None or recorded <= 0 or audited is None:
            invalid.append(
                {
                    "document_id": doc_id,
                    "reason": "finding requires document_id, positive recorded_value, and audited_value",
                }
            )
            continue
        if doc_id in seen_ids:
            invalid.append(
                {
                    "document_id": doc_id,
                    "reason": "duplicate document finding; supply one item-level audited value",
                }
            )
            continue
        seen_ids.add(doc_id)
        error = recorded - audited
        if doc_id in certainty_ids:
            matched_certainty += 1
            certainty_overstatement += max(0.0, error)
            observed_understatement += max(0.0, -error)
        elif doc_id in sampled_ids:
            matched_sample += 1
            if error > 0:
                taints.append(error / recorded)
            elif error < 0:
                observed_understatement += -error
        else:
            unmatched.append(doc_id)

    taints.sort(reverse=True)
    projected = sum(taints) * interval

    k = min(len(taints), len(CONFIDENCE_FACTORS_95) - 1)
    basic_precision = CONFIDENCE_FACTORS_95[0] * interval
    incremental = sum(
        (CONFIDENCE_FACTORS_95[i + 1] - CONFIDENCE_FACTORS_95[i] - 1) * interval * taints[i]
        for i in range(k)
    )
    factor_table_exhausted = len(taints) >= len(CONFIDENCE_FACTORS_95)
    upper_bound = None if factor_table_exhausted else projected + basic_precision + incremental

    return {
        "reviewed_sample_items": matched_sample,
        "overstatement_findings_in_sample": len(taints),
        "reviewed_certainty_items": matched_certainty,
        "findings_unmatched_to_sample": unmatched,
        "invalid_findings": invalid,
        "projection_limitations": (
            ["confidence_factor_table_exhausted"] if factor_table_exhausted else []
        ),
        "sampling_interval": round(interval, 2),
        "projected_overstatement": round(projected, 2),
        "certainty_stratum_overstatement": round(certainty_overstatement, 2),
        "observed_understatement_not_projected": round(observed_understatement, 2),
        "basic_precision": round(basic_precision, 2),
        "incremental_allowance": round(incremental, 2),
        "upper_overstatement_bound_95pct": (
            round(upper_bound + certainty_overstatement, 2) if upper_bound is not None else None
        ),
        "projection_scope": "one_sided_overstatement_only",
        "confidence_level": 0.95,
    }


def main():
    ap = argparse.ArgumentParser(
        description="Build MUS and attribute samples; compute accuracy statement."
    )
    ap.add_argument("input", help="proofed.json or a list of records")
    ap.add_argument("--out", default="sample_plan.json")
    ap.add_argument(
        "--materiality",
        type=float,
        required=True,
        help="documents at or above this are examined with certainty",
    )
    ap.add_argument(
        "--mus-n", type=int, default=60, help="MUS sample size below materiality (default 60)"
    )
    ap.add_argument(
        "--attribute-n",
        type=int,
        default=300,
        help="base attribute sample size before oversampling (default 300)",
    )
    ap.add_argument(
        "--tolerance",
        type=float,
        default=None,
        help="tolerable misstatement in currency units; drives escalation",
    )
    ap.add_argument(
        "--findings",
        default=None,
        help="hash-bound sample_review_results_v1 JSON; switches to projection mode",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=20260812,
        help="fixed for reproducibility -- a sample nobody can reproduce is not auditable",
    )
    ap.add_argument("--quiet", action="store_true")
    apply_shared_help(ap)
    args = ap.parse_args()

    with open(args.input) as fh:
        data = json.load(fh)
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict) and "document_id" in data:
        records = [data]
    elif isinstance(data, dict):
        records = next(
            (
                data[k]
                for k in ("documents", "results", "attributions", "records")
                if isinstance(data.get(k), list)
            ),
            None,
        )
        if records is None:
            sys.exit(
                f"Input has no recognized per-document list (looked for "
                f"documents/results/attributions/records) and is not itself a "
                f"record. Refusing rather than sampling a synthesized "
                f"single-document frame. Got top-level keys: "
                f"{sorted(data.keys())}"
            )
    else:
        records = []
    if not records:
        sys.exit("No records found in input.")

    population, excluded = [], []
    for rec in records:
        ok, reason = eligible(rec)
        doc_id = rec.get("document_id", "unknown")
        if not ok:
            excluded.append({"document_id": doc_id, "reason": reason})
            continue
        resolved = document_value(rec)
        value = resolved["value"]
        if value <= 0:
            excluded.append(
                {
                    "document_id": doc_id,
                    "reason": "no monetary value -- not in MUS frame",
                    "value_basis": resolved["basis"],
                }
            )
            continue
        population.append(
            {
                "document_id": doc_id,
                "value": value,
                "stratum": stratum_of(rec),
                # A derived total is weaker evidence than a printed one, so the
                # sampled document carries which it was.
                "value_basis": resolved["basis"],
                "value_is_derived": resolved["derived"],
            }
        )

    if not population:
        # Name the reason that actually emptied the frame. Reporting a missing
        # total when every document was excluded as an exception sends an
        # operator to fix extraction instead of working the review queue.
        tally = Counter(item["reason"].split(" -- ")[0] for item in excluded)
        breakdown = "; ".join(f"{count} {reason}" for reason, count in tally.most_common())
        finding = (
            f"No documents entered the sampling frame ({len(excluded)} excluded: "
            f"{breakdown}). Sampling covers only the auto-accepted population; "
            "documents in the exception queue are worked in full."
        )
        # An empty frame is a result about the corpus, not a crash. Exiting here
        # wrote no artifact at all, so the run held no record that sampling had
        # been attempted -- indistinguishable from never invoking it, both to the
        # gate, which is assembled from retained artifacts, and to the lane
        # coverage report. Every excluded document is retained with its reason.
        with open(args.out, "w") as fh:
            json.dump(
                {
                    "schema_version": "sample_plan_v1",
                    "generated_at": datetime.now(UTC).isoformat(),
                    "summary": {
                        "population_documents": 0,
                        "excluded_documents": len(excluded),
                        "materiality": args.materiality,
                        "gate_status": "blocked_pending_client_review",
                        "findings": [finding],
                    },
                    "population": [],
                    "excluded": excluded,
                    "monetary_unit_sample": [],
                    "attribute_sample": [],
                },
                fh,
                indent=2,
            )
        if not args.quiet:
            print(finding)
            print(f"  written to {args.out}")
        return 0

    population.sort(key=lambda p: p["document_id"])
    total_value = sum(p["value"] for p in population)

    certainty, mus_sample, interval = build_mus(population, args.materiality, args.mus_n, args.seed)
    attribute_plan = build_attribute_sample(population, args.attribute_n, args.seed)

    result = {
        "schema_version": "sample_plan_v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "seed": args.seed,
        "population": {
            "eligible_documents": len(population),
            "eligible_value": round(total_value, 2),
            "value_basis": basis_summary(records),
            "derived_value_documents": sum(1 for p in population if p["value_is_derived"]),
            "excluded_documents": len(excluded),
            "excluded_note": (
                "Exceptions and zero-value documents. Exceptions are "
                "worked in full and sit outside the sampling frame."
            ),
        },
        "monetary_unit_sample": {
            "materiality": args.materiality,
            "certainty_stratum_count": len(certainty),
            "certainty_stratum_value": round(sum(c["value"] for c in certainty), 2),
            "sampled_count": len(mus_sample),
            "sampling_interval": round(interval, 2),
            "certainty_document_ids": [c["document_id"] for c in certainty],
            "sampled_document_ids": [s["document_id"] for s in mus_sample],
        },
        "attribute_sample": {
            "base_n": args.attribute_n,
            "strata": len(attribute_plan),
            "total_selected": sum(s["sample_size"] for s in attribute_plan),
            "plan": attribute_plan,
        },
        "excluded": excluded[:200],
    }
    result["sample_plan_sha256"] = sample_plan_sha256(result)

    findings_note = []
    if args.findings:
        with open(args.findings) as fh:
            fdata = json.load(fh)
        reconciliation = reconcile_review_results(
            fdata, result["sample_plan_sha256"], certainty, mus_sample
        )
        if reconciliation["status"] != "completed":
            result["accuracy_statement"] = {
                "status": "blocked_incomplete_sample_review",
                "upper_overstatement_bound_95pct": None,
                "review_reconciliation": reconciliation,
                "projection_scope": "one_sided_overstatement_only",
                "confidence_level": 0.95,
            }
            findings_note.append(
                "Accuracy statement blocked: every certainty and MUS selection requires one "
                "explicit reviewed outcome bound to this sample plan hash."
            )
            proj = None
        else:
            proj = project(reconciliation["findings"], interval, certainty, mus_sample)
            proj["review_reconciliation"] = reconciliation
        if proj is not None:
            blockers = list(proj["projection_limitations"])
            proj["status"] = "blocked_" + "_and_".join(blockers) if blockers else "completed"
            result["accuracy_statement"] = proj

        if proj is not None and proj["projection_limitations"]:
            findings_note.append(
                "The checked-in confidence-factor table does not cover this many "
                "overstatement findings. Escalate to a qualified sampling specialist "
                "or 100% review; no upper bound is reported."
            )

        if proj is not None and proj["status"] == "completed":
            ub = proj["upper_overstatement_bound_95pct"]
            findings_note.append(
                f"Overstatement statement: 95% confident that total overstatement does not "
                f"exceed ${ub:,.2f} across ${total_value:,.2f} of eligible value "
                f"({round(100 * ub / total_value, 3)}%). Understatement and completeness "
                "require separate controls."
            )
        if args.tolerance is not None and proj is not None and proj["status"] == "completed":
            if ub > args.tolerance:
                findings_note.append(
                    f"ESCALATE: upper bound ${ub:,.2f} exceeds tolerance "
                    f"${args.tolerance:,.2f}. Identify the failing strata and take "
                    "them to 100% review. Do not accept with a caveat -- sampling "
                    "decides where full review is required, it does not replace it. "
                    "Re-sample after remediation to confirm the stratum clears."
                )
            else:
                findings_note.append(
                    f"Upper bound is within the ${args.tolerance:,.2f} tolerance. "
                    "Pair this with the Completeness Report before presenting -- "
                    "accuracy without completeness is a half-answer."
                )
    else:
        findings_note.append(
            "Plan only. Review the drawn documents, record findings as "
            "sample_review_results_v1 with this sample_plan_sha256 and exactly one "
            "outcome per certainty/MUS document, then re-run with --findings."
        )
        findings_note.append(
            f"MUS: {len(certainty)} certainty + {len(mus_sample)} sampled. "
            f"Attribute: {result['attribute_sample']['total_selected']} across "
            f"{len(attribute_plan)} strata."
        )
        hw_strata = [s for s in attribute_plan if s["attributes"]["has_handwriting"]]
        if hw_strata:
            findings_note.append(
                f"{len(hw_strata)} handwriting strata oversampled up to "
                f"{max(s['oversample_multiplier'] for s in hw_strata)}x population "
                "share. That is where the errors concentrate, so that is where the "
                "statistical resolution belongs."
            )
        if not any(s["attributes"]["branch"] != "unknown" for s in attribute_plan):
            findings_note.append(
                "No branch attribute found on the records. Branch A and B have "
                "different error profiles -- carry the Phase 0.5 branch assignment "
                "through to extraction or the oversampling rates cannot be applied."
            )

    result["notes"] = findings_note

    with open(args.out, "w") as fh:
        json.dump(result, fh, indent=2)

    if not args.quiet:
        print(f"Eligible: {len(population)} documents, ${total_value:,.2f}")
        print(f"  certainty stratum : {len(certainty)}")
        print(f"  MUS sampled       : {len(mus_sample)} (interval ${interval:,.2f})")
        print(
            f"  attribute sampled : {result['attribute_sample']['total_selected']}"
            f" across {len(attribute_plan)} strata"
        )
        for n in findings_note:
            print(f"  - {n}")
        print(f"\nWritten to {args.out}")


if __name__ == "__main__":
    main()
