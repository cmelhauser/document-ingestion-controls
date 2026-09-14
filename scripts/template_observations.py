#!/usr/bin/env python3
"""Build source-template observations from a completed consensus run.

Three lanes consume a source-template artifact -- `schema_discovery.py discover`,
`allocation_policy.py`, and `template_drift.py analyze` -- and nothing in this
repository produced one. Each was documented with an input an operator had to
hand-build, which is how a lane ends up never run.

The material now exists. Consensus retains every printed label an engine could
not fit into the controlled vocabulary, verbatim, under
`source_labelled_field_proposals`; on a real 18-page corpus that was 109 distinct
labels across every document. This command groups documents that show the same
labels into one observed template and writes the artifact those three lanes ask
for.

What this is not: a mapping. A label is copied exactly as printed and never
renamed, matched, or interpreted here. Deciding what "Comm. Date Paid" means is
`schema_discovery`'s job, and approving that meaning is the client's.

A template here is one document family, not one document. Grouping on the exact
set of labels a page happened to show produced 18 templates from 18 pages of a
single statement layout, because label capture varies page to page -- between 2
and 47 labels on the trial corpus. That variance is extraction noise, not layout
difference, and a template per page tells drift detection nothing.

So the family's labels are the union of what its documents showed, and each label
carries the number of documents it appeared in. That keeps the variance visible
rather than averaging it away: a label seen in 2 of 18 documents is a different
observation from one seen in all 18, and the artifact says which.

One further limitation is deliberate. Labels are ordered deterministically rather
than in layout order, because the extension channel's occurrence index reflects
the order an engine happened to enumerate in, not the order they appear on the
page. Template drift therefore detects an added or removed label, which is the
case that produces structurally wrong extraction, and not a pure reordering of
the same labels.
"""

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import classification_consensus
from cli_help import apply_shared_help
from runtime_config import load_project_env

ARTIFACT_TYPE = "source_template_observations_v1"
LABEL_SUFFIX = ".source_label"


def document_labels(document):
    """Return the printed labels retained for one document, with their engines.

    Labels come from the extension channel, which holds them exactly as printed.
    A label seen by more than one engine is one label: the engines are recorded
    beside it rather than producing a duplicate.
    """
    seen = defaultdict(set)
    proposals = document.get("source_labelled_field_proposals") or {}
    for engine, paths in proposals.items():
        if not isinstance(paths, dict):
            continue
        for path, entry in paths.items():
            if not path.endswith(LABEL_SUFFIX) or not isinstance(entry, dict):
                continue
            label = entry.get("value")
            if isinstance(label, str) and label.strip():
                seen[label.strip()].add(str(engine))
    return {label: sorted(engines) for label, engines in seen.items()}


def document_family(document, classifications=None):
    """Return the document's family, preferring an independently agreed classification.

    ``document_type`` on a consensus document is the *intake* rule classification
    copied through, and a corpus whose family carries no distinctive printed
    phrase leaves every one of them ``unknown`` -- which groups the whole corpus
    into one template that is not a layout. An accepted
    ``classification_consensus_v1`` entry is two independent model vendors
    agreeing on the family, so it is preferred where it exists.
    """
    if classifications:
        agreed = classifications.get(str(document.get("document_id") or ""))
        if agreed:
            return agreed
    family = document.get("document_type")
    if isinstance(family, dict):
        family = family.get("value")
    family = str(family or "").strip()
    return family or "unknown"


MINIMUM_LABEL_COHESION = 0.25


def label_cohesion(headers, document_count):
    """Return the share of a template's documents that its commonest label appeared in.

    A template is a claim that these documents share a layout. That claim is
    measurable: if the most widely observed printed label still appears in only a
    small fraction of the grouped documents, the group is a union of unlike
    layouts wearing one identity, not an observed template.
    """
    if document_count < 1 or not headers:
        return 0.0
    return max(header["observed_in_documents"] for header in headers) / document_count


def template_id(family, labels):
    """Return a stable identity for one observed layout."""
    signature = "\x1f".join([family, *labels])
    return f"observed-{hashlib.sha256(signature.encode()).hexdigest()[:16]}"


def build_templates(documents, classifications=None):
    """Group documents showing the same labels into observed templates.

    A document that retained no printed labels is not a template and not
    silently dropped: it is returned as an exception naming what was missing, so
    a corpus that produced no observations says so rather than yielding a
    confident empty artifact.
    """
    grouped = defaultdict(lambda: {"documents": [], "labels": {}, "label_documents": {}})
    exceptions = []
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("consensus documents must be objects")
        document_id = str(document.get("document_id") or "")
        labels = document_labels(document)
        if not labels:
            exceptions.append(
                {
                    "priority": "normal",
                    "document_id": document_id,
                    "page_id": document_id,
                    "region_id": "",
                    "field": "source_labelled_field_proposals",
                    "reason": (
                        "no printed labels were retained for this document, so it "
                        "contributes no observed template"
                    ),
                    "review_source": "template_observations",
                    "disposition": "client_review_required",
                }
            )
            continue
        family = document_family(document, classifications)
        bucket = grouped[family]
        bucket["documents"].append(document_id)
        for label, engines in labels.items():
            bucket["labels"].setdefault(label, set()).update(engines)
            bucket["label_documents"][label] = bucket["label_documents"].get(label, 0) + 1

    templates = []
    for family, bucket in sorted(grouped.items()):
        # Deterministic rather than layout order: see the module docstring.
        ordered = tuple(sorted(bucket["labels"]))
        templates.append(
            {
                "template_id": template_id(family, ordered),
                "document_family": family,
                "family_source": (
                    "classification_consensus"
                    if classifications and family in set(classifications.values())
                    else "intake_classification"
                ),
                "headers": [
                    {
                        "source_label": label,
                        "observed_by_engines": sorted(bucket["labels"][label]),
                        # How consistently this label appeared. A label in 2 of 18
                        # documents is a different observation from one in all 18.
                        "observed_in_documents": bucket["label_documents"][label],
                    }
                    for label in ordered
                ],
                "source_labels": list(ordered),
                "document_count": len(bucket["documents"]),
                "evidence": [
                    {"document_id": document_id, "page_id": document_id, "region_id": ""}
                    for document_id in sorted(bucket["documents"])
                ],
                "label_order": "deterministic_not_layout_order",
                "observation_only": True,
                "canonical_mapping_permitted": False,
            }
        )
    for template in templates:
        cohesion = label_cohesion(template["headers"], template["document_count"])
        template["label_cohesion"] = round(cohesion, 4)
        template["cohesive_layout"] = cohesion >= MINIMUM_LABEL_COHESION
        if template["cohesive_layout"]:
            continue
        # A whole corpus once grouped into one "template" of 2,430 labels over 645
        # documents because Phase 2 classification left every family unresolved.
        # Nothing refused it: drift compared it, mapping discovery proposed rules
        # against its fingerprint, and no artifact said the fingerprint stood for
        # nothing. Keep the observation -- never drop evidence -- and state it.
        exceptions.append(
            {
                "priority": "high",
                "document_id": template["template_id"],
                "page_id": template["template_id"],
                "region_id": "",
                "field": "observed_template_layout",
                "reason": (
                    "observed template groups documents that share no common printed "
                    f"label: its commonest label appears in {cohesion:.1%} of "
                    f"{template['document_count']} documents, below the "
                    f"{MINIMUM_LABEL_COHESION:.0%} cohesion floor, so this is a union of "
                    "unlike layouts rather than one template and its fingerprint must "
                    "not be treated as a layout identity"
                ),
                "review_source": "template_observations",
                "disposition": "client_review_required",
            }
        )
    return templates, exceptions


def load_documents(path):
    """Read the consensus artifact this observation is built from."""
    data = json.loads(Path(path).read_text())
    documents = data.get("documents") if isinstance(data, dict) else None
    if not isinstance(documents, list) or not documents:
        raise ValueError("consensus input must contain a non-empty documents list")
    return documents


def main(argv=None):
    """Write the source-template observation artifact."""
    load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "consensus", type=Path, help="consensus.py output whose retained printed labels are grouped"
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="New source-template observation artifact."
    )
    parser.add_argument(
        "--exceptions",
        type=Path,
        required=True,
        help="New exception artifact naming every document that contributed no template.",
    )
    parser.add_argument(
        "--classifications",
        type=Path,
        help=(
            "Accepted classification_consensus_v1 artifact whose independently agreed "
            "document types decide the family a document is grouped under. Without it "
            "the intake rule classification decides, and a corpus it could not classify "
            "groups into one template that is not a layout."
        ),
    )
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args(argv)

    try:
        for path in (args.out, args.exceptions):
            if path.exists():
                raise FileExistsError(f"refusing to overwrite existing artifact: {path}")
        documents = load_documents(args.consensus)
        classifications = None
        if args.classifications is not None:
            classifications = classification_consensus.classification_map(args.classifications)
            matched = sum(
                1
                for document in documents
                if str(document.get("document_id") or "") in classifications
            )
            if not matched:
                # Rule 9: a classification artifact that matched nothing would
                # silently leave every family unresolved while looking applied.
                raise ValueError(
                    f"{args.classifications.name} matched no document in "
                    f"{args.consensus.name}; the two artifacts describe different runs"
                )
        templates, exceptions = build_templates(documents, classifications)
        if not templates:
            # Rule 9: an artifact with no templates would be accepted by all
            # three consumers as "this corpus has no layouts", which is a claim
            # nothing supports.
            raise ValueError(
                f"no document in {args.consensus.name} retained a printed label, so there "
                "is no observed template to write"
            )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.exceptions.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {
                    "artifact_type": ARTIFACT_TYPE,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "source_artifact": args.consensus.name,
                    "summary": {
                        "documents": len(documents),
                        "templates": len(templates),
                        "distinct_labels": len(
                            {header["source_label"] for t in templates for header in t["headers"]}
                        ),
                        "documents_without_labels": sum(
                            1
                            for item in exceptions
                            if item["field"] == "source_labelled_field_proposals"
                        ),
                        "non_cohesive_templates": sum(
                            1 for item in templates if not item["cohesive_layout"]
                        ),
                    },
                    "templates": templates,
                },
                indent=2,
            )
            + "\n"
        )
        args.exceptions.write_text(
            json.dumps({"summary": {"count": len(exceptions)}, "exceptions": exceptions}, indent=2)
            + "\n"
        )
    except (OSError, ValueError, FileExistsError, json.JSONDecodeError) as exc:
        sys.exit(f"Template observations failed: {exc}")

    if not args.quiet:
        labels = {header["source_label"] for t in templates for header in t["headers"]}
        print(f"Observed templates: {len(templates)} from {len(documents)} documents")
        print(f"  distinct printed labels: {len(labels)}")
        unlabelled = [
            item for item in exceptions if item["field"] == "source_labelled_field_proposals"
        ]
        if unlabelled:
            print(f"  documents contributing no template: {len(unlabelled)}")
        for template in templates:
            if not template["cohesive_layout"]:
                print(
                    f"  NOT A LAYOUT: {template['template_id']} groups "
                    f"{template['document_count']} documents sharing no common label "
                    f"({template['label_cohesion']:.1%} cohesion)"
                )
        print(f"  written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
