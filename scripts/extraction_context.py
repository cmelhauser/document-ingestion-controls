#!/usr/bin/env python3
"""Build the corpus context that conditions a context-aware extraction update.

Extraction reads one page at a time and remembers nothing between pages. A label
printed on six hundred pages is therefore rediscovered, or missed, six hundred
times. On a real 716-page commission corpus that cost most of the run: the
acknowledgement number -- the identifier the business ties money to -- was
printed under ``ACK#``, ``ACK NO`` and dealer-qualified variants throughout, and
reached the controlled header on four documents out of 716. Both engines saw it.
Neither could place it, so both filed it in the extension channel, and every
downstream control that needs an attribution key found none.

This command turns what the run has already *learned* about its own corpus into
prompt text for a second reading of it:

* every **approved** source-label rule becomes one glossary line, so a label an
  engine could not place is placed the way the engagement already approved;
* every independently agreed document family is named, so a page whose family
  the intake rules could not decide is not read as a generic form;
* an operator may add reasoning-only prose describing the business.

Three properties make this safe to put in front of an extraction model:

* **Nothing here is a value.** The glossary says where a printed label belongs,
  never what it says. The prompt preamble in ``extraction_schema`` states that
  the page wins over any note, and that an absent label stays null.
* **Only approved rules are used.** An unapproved mapping proposal is a
  suggestion the client or engagement owner has not accepted; putting it in the
  prompt would apply it to every page without anyone having approved it.
* **Both lanes get identical text.** The context is one file, referenced by both
  extraction lanes through ``LLM_CORPUS_CONTEXT``. ``consensus`` refuses a pair
  of handoffs whose context hashes differ, because coaching one engine and not
  the other measures the coaching rather than the page.
"""

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import registry_approval
from cli_help import apply_shared_help
from extraction_schema import MAX_CORPUS_CONTEXT_BYTES
from runtime_config import load_project_env

ARTIFACT_TYPE = "extraction_corpus_context_v1"


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


def approved_label_glossary(registry):
    """Return {canonical_field: sorted distinct printed labels} for approved rules.

    Rules are keyed by template fingerprint, and the same label is commonly
    approved once per template. The prompt does not carry fingerprints -- the
    model cannot see which template it is reading -- so the labels are collapsed
    to the distinct set per canonical field.
    """
    glossary = defaultdict(set)
    for rule in registry.get("rules", []):
        if not isinstance(rule, dict):
            continue
        if not registry_approval.is_approved(rule):
            continue
        if rule.get("rule_type", "source_label_mapping") != "source_label_mapping":
            continue
        field = rule.get("canonical_field")
        label = " ".join(str(rule.get("source_label") or "").split())
        if field and label:
            glossary[field].add(label)
    return {field: sorted(labels) for field, labels in sorted(glossary.items())}


def accepted_families(classifications):
    """Return {document_type: document count} over accepted classifications.

    Counts documents rather than entries. Acceptances are appended, so a client
    decision that corrects an earlier one leaves both in the list; counting
    entries reported the corpus as larger than it is and kept naming a family
    that no longer applied to any document.
    """
    resolved = {}
    for item in (classifications or {}).get("accepted", []):
        if isinstance(item, dict) and item.get("document_type"):
            resolved[str(item.get("document_id"))] = item["document_type"]
    counts = defaultdict(int)
    for document_type in resolved.values():
        counts[document_type] += 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def render(glossary, families, notes):
    """Render the context file an extraction lane will be conditioned by."""
    out = []
    if families:
        out.append(
            "Document families independently agreed by two model vendors elsewhere "
            "in this corpus, with how many pages carried each. Use them to "
            "recognise a family, never to assign one: classify the page in front "
            "of you from what it shows."
        )
        out += [f"- {name}: {count} pages" for name, count in families.items()]
        out.append("")
    if glossary:
        out.append(
            "Printed labels this engagement has already approved as belonging to a "
            "controlled field. When one of these labels appears on the page, put "
            "its visible value in the named field instead of the extension "
            "channel. A label not listed here still goes to the extension channel "
            "with its exact printed label. Approval covers where a label belongs, "
            "never what it says."
        )
        for field, labels in glossary.items():
            rendered = "; ".join(repr(label) for label in labels)
            out.append(f"- {field}: {rendered}")
        out.append("")
    if notes:
        out.append("Operator notes on this corpus:")
        out.append(notes.strip())
        out.append("")
    return "\n".join(out).strip() + "\n"


def main(argv=None):
    """Build the corpus-context file and its provenance record."""
    load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        help=(
            "Approved semantic-mapping registry. Only an approved rule becomes a "
            "glossary line; a proposal nobody accepted is not put in front of the model."
        ),
    )
    parser.add_argument(
        "--classifications",
        help=(
            "Classification-consensus artifact naming the document families two "
            "independent vendors agreed on."
        ),
    )
    parser.add_argument(
        "--notes",
        help=(
            "Optional operator prose about the business. Reasoning-only context: it "
            "is never evidence and authorizes no value."
        ),
    )
    parser.add_argument(
        "--out",
        required=True,
        help="New context file to point LLM_CORPUS_CONTEXT at for both extraction lanes.",
    )
    parser.add_argument(
        "--provenance",
        required=True,
        help=(
            "New JSON record of what this context was built from, so a later reader "
            "can say exactly what conditioned a reading."
        ),
    )
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args(argv)

    try:
        if not (args.registry or args.classifications or args.notes):
            raise ValueError(
                "a context needs at least one source: --registry, --classifications, or --notes"
            )
        out_path = require_new_file(args.out)
        provenance_path = require_new_file(args.provenance)

        glossary = approved_label_glossary(load_json(args.registry)) if args.registry else {}
        families = (
            accepted_families(load_json(args.classifications)) if args.classifications else {}
        )
        notes = Path(args.notes).read_text(encoding="utf-8") if args.notes else ""

        if not (glossary or families or notes.strip()):
            # Rule 9: a context built from nothing is not a context. Writing it
            # would let a run report a context-aware update it did not take.
            raise ValueError(
                "every named source was empty -- no approved rule, agreed family, or "
                "operator note was found, so there is nothing to condition a reading with"
            )

        text = render(glossary, families, notes)
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_CORPUS_CONTEXT_BYTES:
            raise ValueError(
                f"rendered context is {len(encoded)} bytes, above the "
                f"{MAX_CORPUS_CONTEXT_BYTES}-byte prompt ceiling. Narrow the registry "
                "or shorten the operator notes."
            )
        out_path.write_text(text)
        provenance_path.write_text(
            json.dumps(
                {
                    "artifact_type": ARTIFACT_TYPE,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "context_file": out_path.name,
                    "sha256": hashlib.sha256(encoded).hexdigest(),
                    "bytes": len(encoded),
                    "sources": {
                        "registry": args.registry,
                        "classifications": args.classifications,
                        "notes": args.notes,
                    },
                    "summary": {
                        "canonical_fields": len(glossary),
                        "approved_labels": sum(len(v) for v in glossary.values()),
                        "document_families": len(families),
                        "operator_notes_bytes": len(notes.encode("utf-8")),
                    },
                    "reasoning_only": True,
                    "clears_no_control": True,
                },
                indent=2,
            )
            + "\n"
        )
    except (OSError, ValueError, FileExistsError, json.JSONDecodeError) as exc:
        sys.exit(f"Extraction context build failed: {exc}")

    if not args.quiet:
        print(f"Corpus context written to {out_path}")
        print(f"  canonical fields with approved labels: {len(glossary)}")
        print(f"  approved printed labels: {sum(len(v) for v in glossary.values())}")
        print(f"  agreed document families: {len(families)}")
        print(f"  bytes: {len(encoded)} of {MAX_CORPUS_CONTEXT_BYTES}")
        print("  point LLM_CORPUS_CONTEXT at this file for BOTH extraction lanes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
