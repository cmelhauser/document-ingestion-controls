#!/usr/bin/env python3
"""Promote source-labelled proposals into controlled fields under approved rules.

An extraction engine that meets a printed label it cannot place in the controlled
vocabulary retains it as a source-labelled proposal rather than guessing. That is
correct, and it is where the value stops: ``consensus.py`` claims no consensus
over extension entries, and the approved mapping registry was read only by the
table lanes and by reconciliation. Nothing carried an approved rule back to the
document record that attribution, completeness, and the inferred controls read.

The cost of that gap is concrete. A 716-page commission corpus printed its
acknowledgement numbers under ``ACK NO``, ``ACK #``, ``ACK#`` and dealer-qualified
variants on almost every page, and retained 21,374 source-labelled entries. Zero
documents carried ``header.acknowledgement_number``, so the one attribution lane
available to a client with no reference master reported
``no_explicit_attribution_key`` for 715 of 716 documents -- not because the key
was missing from the page, but because no command existed to move it.

This command closes that gap without weakening a control:

* Only a rule already carrying an approval (client or engagement owner) can
  promote anything. An unapproved proposal is ignored, not applied.
* A promotion is reconciled to the same standard as any other field. Two
  independent engines must agree on the value; a lone engine or a disagreement is
  retained as an explicit exception rather than promoted.
* Nothing is rewritten. The consensus artifact is read-only input, every promoted
  field records the rule, source label, and engines behind it, and the augmented
  record set is a new artifact.
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import independent_corroboration
import registry_approval
import schema_discovery
from cli_help import apply_shared_help
from runtime_config import load_project_env

ARTIFACT_TYPE = "applied_source_label_mappings_v1"
RECORDS_ARTIFACT_TYPE = "records_with_applied_mappings_v1"
MINIMUM_ENGINES = 2

# An independent non-LLM extractor reads the printed column captions too, and it
# reads them off the same page. Where only one engine met a label, that reading
# is the second one -- 750 of 1,642 refusals on one run were a label the
# independent extractor had also read. It is corroboration, never vendor
# agreement, and the flag below says so wherever the promotion travels.
#
# The caption is not the value, and corroborating only the caption is not
# enough. Measured on a real run, 183 of 742 promotions made on caption evidence
# alone carried a value that was the entire column concatenated -- `total_amount`
# promoted as `"878,311.91 77,236.58 48,258.24"`, and one page whose `P.O. Total`
# read `"60 0 0 13 0 74"` entered attribution as $600,013,074 against its own
# printed statement total of $8,673. The lone engine's reading of the *value* has
# to be corroborated too, or this lane promotes whatever that engine emitted.
INDEPENDENT_EVIDENCE_FLAG = "corroborated_by_independent_extractor"
# What an approved mapping writes as the field's acceptance where two engines
# read the value under the label. The export registers it as its own status: a
# rule placing a value is not two vendors agreeing on which field it belongs in.
MAPPING_ACCEPTED_BY = "approved_source_label_mapping"

# Document AI merges captions, so a header cell arrives as
# `"Project\nJob #\nProject\n"` and a printed label is a substring of it
# rather than equal to it. Short labels are matched as whole words for the same
# reason the corroboration lane guards them: `"#"` is inside almost everything.
MINIMUM_LABEL_LENGTH = 3
LABEL_SEPARATORS = re.compile(r"[^0-9a-z]+")


def normalize(label):
    """Collapse a printed label the way the mapping registry compares them."""
    return " ".join(str(label or "").split()).casefold()


def independent_label_evidence(paths):
    """Read every independent extractor artifact into per-page printed-caption text.

    Both the table headers and each cell's own `source_label` are read: a caption
    that survives into only one of the two is still a caption the extractor saw.
    """
    captions, readings = {}, {}
    for path in paths:
        data = json.loads(Path(path).read_text())
        records = data.get("records") if isinstance(data, dict) else data
        if not isinstance(records, list):
            raise ValueError(f"not an extractor artifact with records: {path}")
        for record in records:
            page = record.get("page_id") or record.get("document_id")
            if not page:
                continue
            labels, values = [], [str(record.get("document_text") or "")]
            for table in record.get("source_tables") or []:
                labels.extend(str(header or "") for header in table.get("source_headers") or [])
                for row in table.get("source_rows") or []:
                    for cell in row.get("cells") or []:
                        labels.append(str(cell.get("source_label") or ""))
                        values.append(str(cell.get("evidence_text") or ""))
            text = " ".join(LABEL_SEPARATORS.sub(" ", " ".join(labels).casefold()).split())
            if text:
                captions[page] = captions.get(page, "") + " " + text
            readings[page] = readings.get(page, "") + " " + " ".join(values)
    evidence = {
        page: {"captions": text, "readings": readings.get(page, "")}
        for page, text in captions.items()
    }
    if not evidence:
        # Rule 9: evidence that carried no captions corroborates nothing.
        raise ValueError(
            "the independent artifacts carried no printed captions; a control "
            "that processed nothing has not passed"
        )
    return evidence


def label_independently_read(label, page_evidence):
    """Say whether the independent extractor read this printed caption on this page."""
    if not page_evidence:
        return False
    reduced = " ".join(LABEL_SEPARATORS.sub(" ", str(label or "").casefold()).split())
    if not reduced:
        return False
    captions = page_evidence["captions"]
    if len(reduced.replace(" ", "")) < MINIMUM_LABEL_LENGTH:
        return reduced in captions.split()
    return reduced in captions


def value_independently_read(value, page_evidence):
    """Say whether the independent extractor read this value on this page.

    A lone engine that emitted the whole column as one string -- three amounts
    joined by spaces or semicolons -- did not read a field, and no contiguous
    run of the page says otherwise. Matching the value the same way the
    corroboration lane matches one is what separates the two.
    """
    if not page_evidence:
        return False
    text = independent_corroboration.normalize(page_evidence["readings"])
    if not text:
        return False
    mode, _ = independent_corroboration.presence(
        value, {"text": text, "tokens": Counter(text.split())}
    )
    return mode is not None


def require_new_file(path):
    """Refuse to replace a retained artifact."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_json(path):
    """Read one retained JSON artifact."""
    return json.loads(Path(path).read_text())


def document_fingerprints(templates):
    """Map every document to the fingerprint of the template that observed it."""
    resolved = {}
    for template in templates.get("templates", []):
        if not isinstance(template, dict):
            raise ValueError("every observed template must be an object")
        fingerprint = schema_discovery.template_fingerprint(template)
        for item in template.get("evidence", []):
            document_id = str((item or {}).get("document_id") or "")
            if document_id:
                resolved[document_id] = fingerprint
    if not resolved:
        raise ValueError("observed templates bound no document, so nothing can be mapped")
    return resolved


def approved_rules(registry):
    """Index every approved source-label rule by fingerprint and normalized label."""
    rules = {}
    for rule in registry.get("rules", []):
        if not registry_approval.is_approved(rule):
            continue
        if rule.get("rule_type", "source_label_mapping") != "source_label_mapping":
            continue
        key = (rule.get("template_fingerprint"), normalize(rule.get("source_label")))
        rules[key] = rule
    return rules


def engine_labels(document):
    """Return {engine: {normalized_label: (printed_label, observed_value)}}."""
    resolved = {}
    for engine, proposals in (document.get("source_labelled_field_proposals") or {}).items():
        if not isinstance(proposals, dict):
            continue
        pairs = defaultdict(dict)
        for path, entry in proposals.items():
            if not isinstance(entry, dict):
                continue
            field, _, leaf = path.rpartition(".")
            if leaf in ("source_label", "observed_value"):
                pairs[field][leaf] = entry.get("value")
        found = {}
        for parts in pairs.values():
            label = parts.get("source_label")
            value = parts.get("observed_value")
            if label is None or value in (None, ""):
                continue
            # The field identifier is engine-local: two engines rarely agree on
            # it for one printed label, so the label itself is the join key.
            found[normalize(label)] = (label, value)
        if found:
            resolved[engine] = found
    return resolved


def promote(
    document, fingerprint, rules, minimum_engines=MINIMUM_ENGINES, independent_evidence=None
):
    """Resolve every approved label on one document into promotions and exceptions."""
    by_engine = engine_labels(document)
    document_id = str(document.get("document_id") or "")
    promotions, exceptions = [], []
    labels = {label for found in by_engine.values() for label in found}
    for label in sorted(labels):
        rule = rules.get((fingerprint, label))
        if rule is None:
            continue
        readings = {engine: found[label] for engine, found in by_engine.items() if label in found}
        values = defaultdict(list)
        for engine, (_, value) in readings.items():
            values[str(value)].append(engine)
        printed = next(iter(readings.values()))[0]
        top_value, top_engines = max(values.items(), key=lambda item: (len(item[1]), item[0]))
        base = {
            "document_id": document_id,
            "page_id": document_id,
            "canonical_field": rule.get("canonical_field"),
            "source_label": printed,
            "registry_rule_id": rule.get("rule_id"),
            "approval_authority": rule.get("approval_authority"),
            "approved_by": rule.get("approved_by"),
        }
        if len(readings) < minimum_engines:
            if label_independently_read(printed, independent_evidence) and (
                value_independently_read(top_value, independent_evidence)
            ):
                # One engine read the label and an independent extractor read it
                # from the same page. Never labelled `consensus_*`: no second
                # model vendor was involved.
                promotions.append(
                    {
                        **base,
                        "value": top_value,
                        "agreeing_engines": sorted(top_engines),
                        "engine_count": len(readings),
                        "consensus_flag": INDEPENDENT_EVIDENCE_FLAG,
                        "independent_evidence": {
                            "accepted_by": INDEPENDENT_EVIDENCE_FLAG,
                            "match_scope": "page",
                            # Both halves, because a caption is not a value.
                            "caption_corroborated": True,
                            "value_corroborated": True,
                            "vendor_agreement": False,
                        },
                        "accepted": True,
                    }
                )
                continue
            exceptions.append(
                {
                    **base,
                    "priority": "normal",
                    "region_id": "",
                    "field": rule.get("canonical_field"),
                    "reason": (
                        f"only {len(readings)} engine read the source label {printed!r}, so "
                        "promoting it would create a controlled value no independent "
                        "reading supports"
                    ),
                    "review_source": "apply_mappings",
                    "disposition": "client_review_required",
                }
            )
            continue
        if len(top_engines) < minimum_engines:
            detail = "; ".join(
                f"{engine}={value}"
                for value, engines in sorted(values.items())
                for engine in engines
            )
            exceptions.append(
                {
                    **base,
                    "priority": "high",
                    "region_id": "",
                    "field": rule.get("canonical_field"),
                    "reason": (
                        f"engines disagree on the value printed under {printed!r} ({detail})"
                    ),
                    "review_source": "apply_mappings",
                    "disposition": "client_review_required",
                }
            )
            continue
        promotions.append(
            {
                **base,
                "value": top_value,
                "agreeing_engines": sorted(top_engines),
                "engine_count": len(readings),
                "consensus_flag": f"consensus_{len(top_engines)}of{len(readings)}",
                "accepted": True,
            }
        )
    return promotions, exceptions


def mapped_field(promotion, prior=None):
    """The controlled field an approved mapping writes, accepted by what earned it.

    Two engines reading the value under an approved label is the mapping's own
    evidence; a lone engine the independent extractor bore out is corroboration
    and says so. Either way the rule, the label and the engines travel with the
    field, and anything else the field carried stays on it.
    """
    corroborated = promotion.get("consensus_flag") == INDEPENDENT_EVIDENCE_FLAG
    return {
        **(prior or {}),
        "value": promotion["value"],
        "source": "printed",
        "accepted": True,
        "consensus_flag": promotion.get("consensus_flag"),
        "agreeing_engines": list(promotion.get("agreeing_engines") or []),
        "mapped_from_source_label": promotion.get("source_label"),
        "registry_rule_id": promotion.get("registry_rule_id"),
        "acceptance": {
            "accepted_by": INDEPENDENT_EVIDENCE_FLAG if corroborated else MAPPING_ACCEPTED_BY,
            "registry_rule_id": promotion.get("registry_rule_id"),
            "approval_authority": promotion.get("approval_authority"),
            "source_label": promotion.get("source_label"),
        },
    }


def augmented_records(documents, promotions_by_document):
    """Copy each document, merging its accepted promotions into its fields and header.

    The consensus artifact is never edited. A promoted value is written to the
    controlled field the export reads, accepted by what earned it, and to the
    header view the controls read, both keeping the rule, label and engines that
    produced it, so a later reader can tell a promoted value from one the engines
    placed in the controlled field themselves. Written to the header alone, 393
    approved mappings on the commission run never reached the export.
    """
    records = []
    for document in documents:
        document_id = str(document.get("document_id") or "")
        record = json.loads(json.dumps(document))
        header = dict(record.get("header") or {})
        fields = record.setdefault("fields", {})
        applied = []
        for promotion in promotions_by_document.get(document_id, []):
            field = promotion["canonical_field"]
            path = f"header.{field}"
            held = header.get(field) if isinstance(header.get(field), dict) else {}
            prior = fields.get(path) if isinstance(fields.get(path), dict) else {}
            if held.get("value") or prior.get("value"):
                # An engine already placed a value in the controlled field. The
                # promotion is retained as provenance and never overwrites it.
                applied.append({**promotion, "applied": False, "reason": "field already populated"})
                continue
            fields[path] = mapped_field(promotion, prior)
            header[field] = {
                "value": promotion["value"],
                "source": "printed",
                "mapped_from_source_label": promotion["source_label"],
                "registry_rule_id": promotion["registry_rule_id"],
                "approval_authority": promotion["approval_authority"],
                "agreeing_engines": promotion["agreeing_engines"],
                "consensus_flag": promotion["consensus_flag"],
            }
            applied.append({**promotion, "applied": True})
        record["header"] = header
        record["applied_source_label_mappings"] = applied
        records.append(record)
    return records


def main(argv=None):
    """Apply approved source-label rules and write both retained artifacts."""
    load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "consensus", help="Completed consensus artifact whose retained source labels are mapped."
    )
    parser.add_argument(
        "templates",
        help=(
            "Source-template observation artifact binding each document to the layout "
            "whose fingerprint the registry rules are keyed by."
        ),
    )
    parser.add_argument(
        "--registry",
        required=True,
        help="Approved semantic-mapping registry. Only an approved rule may promote a value.",
    )
    parser.add_argument("--out", required=True, help="New applied-mapping decision artifact.")
    parser.add_argument(
        "--records-out",
        required=True,
        help=(
            "New record set carrying the accepted promotions in the controlled header, "
            "for the controls that read document records rather than table rows."
        ),
    )
    parser.add_argument(
        "--exceptions",
        required=True,
        help=(
            "New exception artifact naming every approved label that could not be promoted. "
            "Absence of exceptions is a result, not a formality."
        ),
    )
    parser.add_argument(
        "--minimum-engines",
        type=int,
        default=MINIMUM_ENGINES,
        help=(
            "Independent engines that must agree on a value before it is promoted into a "
            "controlled field. Below two a promotion would rest on a single reading."
        ),
    )
    parser.add_argument(
        "--independent-evidence",
        action="append",
        default=[],
        metavar="ARTIFACT",
        help=(
            "Repeatable independent extractor artifact. A label only one engine "
            "read, which the independent extractor also read from that page, is "
            "promoted as `corroborated_by_independent_extractor` rather than "
            "refused. This accepts a controlled value on weaker evidence than "
            "vendor agreement, so it requires --authorization."
        ),
    )
    parser.add_argument(
        "--authorization",
        help=(
            "Client decision that independent corroboration of a printed label "
            "may stand in for a second engine. Required with "
            "--independent-evidence and meaningless without it."
        ),
    )
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args(argv)

    try:
        if args.independent_evidence and not args.authorization:
            raise ValueError(
                "--independent-evidence promotes a controlled value on weaker "
                "evidence than vendor agreement; name the client decision with "
                "--authorization"
            )
        if args.authorization and not args.independent_evidence:
            raise ValueError(
                "--authorization applies only to --independent-evidence; supply "
                "the artifacts it authorizes or drop the flag"
            )
        if args.minimum_engines < 2:
            raise ValueError(
                "minimum engines must be at least 2; promoting a controlled value from one "
                "reading is not consensus"
            )
        out_path = require_new_file(args.out)
        records_path = require_new_file(args.records_out)
        exceptions_path = require_new_file(args.exceptions)
        consensus = load_json(args.consensus)
        documents = consensus.get("documents") if isinstance(consensus, dict) else None
        if not isinstance(documents, list) or not documents:
            raise ValueError("consensus input must contain a non-empty documents list")
        fingerprints = document_fingerprints(load_json(args.templates))
        rules = approved_rules(load_json(args.registry))

        independent = (
            independent_label_evidence(args.independent_evidence)
            if args.independent_evidence
            else {}
        )
        promotions_by_document = defaultdict(list)
        exceptions = []
        for document in documents:
            document_id = str(document.get("document_id") or "")
            fingerprint = fingerprints.get(document_id)
            if fingerprint is None:
                continue
            promoted, refused = promote(
                document,
                fingerprint,
                rules,
                args.minimum_engines,
                independent.get(document_id),
            )
            promotions_by_document[document_id].extend(promoted)
            exceptions.extend(refused)
        promotions = [item for items in promotions_by_document.values() for item in items]
        records = augmented_records(documents, promotions_by_document)

        out_path.write_text(
            json.dumps(
                {
                    "artifact_type": ARTIFACT_TYPE,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "source_artifact": Path(args.consensus).name,
                    "minimum_engines": args.minimum_engines,
                    "independent_evidence_sources": [
                        Path(path).name for path in args.independent_evidence
                    ],
                    "independent_evidence_authorization": args.authorization,
                    "summary": {
                        "documents": len(documents),
                        "approved_rules": len(rules),
                        "promoted": len(promotions),
                        "unresolved": len(exceptions),
                    },
                    "clears_no_control": True,
                    "promotions": promotions,
                },
                indent=2,
            )
            + "\n"
        )
        records_path.write_text(
            json.dumps(
                {
                    "artifact_type": RECORDS_ARTIFACT_TYPE,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "source_artifact": Path(args.consensus).name,
                    "summary": {"documents": len(records), "promoted": len(promotions)},
                    "documents": records,
                },
                indent=2,
            )
            + "\n"
        )
        exceptions_path.write_text(
            json.dumps({"summary": {"count": len(exceptions)}, "exceptions": exceptions}, indent=2)
            + "\n"
        )
    except (OSError, ValueError, FileExistsError, json.JSONDecodeError) as exc:
        sys.exit(f"Apply mappings failed: {exc}")

    if not args.quiet:
        print(f"Approved source-label rules: {len(rules)}")
        print(f"  values promoted into controlled fields: {len(promotions)}")
        print(f"  approved labels retained for review: {len(exceptions)}")
        if not rules:
            # Rule 9: a lane that promoted nothing because nothing was approved
            # has not applied mappings, and must not read as though it had.
            print("  no approved rule exists yet, so no mapping was applied")
        print(f"  written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
