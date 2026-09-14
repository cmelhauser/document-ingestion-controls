"""Cover the producer that gives three template-consuming lanes an input.

`schema_discovery.py discover`, `allocation_policy.py`, and
`template_drift.py analyze` all consume a source-template artifact that nothing
in this repository produced. The most important assertion here is the last one:
that what this writes is accepted by all three.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

observations = importlib.import_module("template_observations")


def document(document_id, labels, family="commission_statement", engine="openai/model"):
    """Build a consensus document carrying retained printed labels."""
    proposals = {
        engine: {
            f"source_labelled_fields.field_{index}.source_label": {"value": label}
            for index, label in enumerate(labels, start=1)
        }
    }
    return {
        "document_id": document_id,
        "document_type": family,
        "source_labelled_field_proposals": proposals,
    }


def write_consensus(tmp_path, documents, name="consensus.json"):
    """Write a consensus artifact containing the given documents."""
    path = tmp_path / name
    path.write_text(json.dumps({"summary": {}, "documents": documents}))
    return path


def test_a_family_is_one_template_and_label_variance_stays_visible():
    """Pages of one layout are one template; how often a label appeared is kept.

    Grouping on the exact label set produced 18 templates from 18 pages of a
    single statement, because capture varies page to page. That variance is
    extraction noise, not layout difference.
    """
    documents = [
        document("d1", ["Total Paid", "Comm. Date Paid"]),
        document("d2", ["Total Paid"]),
        document("d3", ["Total Paid", "Ship Date"]),
    ]
    templates, exceptions = observations.build_templates(documents)
    assert len(templates) == 1
    template = templates[0]
    assert template["document_count"] == 3
    assert template["source_labels"] == ["Comm. Date Paid", "Ship Date", "Total Paid"]
    counts = {h["source_label"]: h["observed_in_documents"] for h in template["headers"]}
    assert counts == {"Total Paid": 3, "Comm. Date Paid": 1, "Ship Date": 1}
    assert template["observation_only"] is True
    assert template["canonical_mapping_permitted"] is False
    assert template["label_order"] == "deterministic_not_layout_order"
    assert not exceptions

    # Two families are two templates, and the identity is stable.
    mixed = observations.build_templates(
        [document("d1", ["A"]), document("d2", ["A"], family="invoice")]
    )[0]
    assert {t["document_family"] for t in mixed} == {"commission_statement", "invoice"}
    assert observations.build_templates(documents)[0][0]["template_id"] == template["template_id"]


def test_a_label_is_copied_exactly_and_credited_to_every_engine_that_saw_it():
    """This is an observation, not a mapping: nothing is renamed or merged."""
    doc = {
        "document_id": "d1",
        "document_type": "commission_statement",
        "source_labelled_field_proposals": {
            "openai/luna": {"source_labelled_fields.f1.source_label": {"value": "  Total Paid  "}},
            "openrouter/grok": {"source_labelled_fields.f2.source_label": {"value": "Total Paid"}},
            # Non-label paths and malformed entries contribute nothing.
            "openai/luna ": {"source_labelled_fields.f1.observed_value": {"value": "999"}},
        },
    }
    labels = observations.document_labels(doc)
    assert labels == {"Total Paid": ["openai/luna", "openrouter/grok"]}

    # "Total Commissions to date" and "Total Commissions to date:" are different
    # printed labels and stay different; deciding they mean one thing is
    # schema_discovery's job, not this one's.
    both = observations.document_labels(
        {
            "source_labelled_field_proposals": {
                "e": {
                    "source_labelled_fields.a.source_label": {"value": "Total Commissions to date"},
                    "source_labelled_fields.b.source_label": {
                        "value": "Total Commissions to date:"
                    },
                }
            }
        }
    )
    assert len(both) == 2

    assert (
        observations.document_labels({"source_labelled_field_proposals": {"e": "not-a-dict"}}) == {}
    )
    # A label that is blank, absent, or not text at all contributes nothing.
    assert (
        observations.document_labels(
            {
                "source_labelled_field_proposals": {
                    "e": {
                        "source_labelled_fields.a.source_label": {"value": "   "},
                        "source_labelled_fields.b.source_label": {"value": 42},
                        "source_labelled_fields.c.source_label": {},
                    }
                }
            }
        )
        == {}
    )
    assert observations.document_labels({}) == {}
    assert observations.document_family({"document_type": {"value": "invoice"}}) == "invoice"
    assert observations.document_family({"document_type": ""}) == "unknown"
    assert observations.document_family({}) == "unknown"


def test_a_document_contributing_nothing_is_recorded_not_dropped():
    """Rule 2: a document with no retained label is an exception, not a silence."""
    templates, exceptions = observations.build_templates(
        [document("d1", ["Total Paid"]), {"document_id": "d2"}]
    )
    assert len(templates) == 1 and templates[0]["document_count"] == 1
    assert len(exceptions) == 1
    assert exceptions[0]["document_id"] == "d2"
    assert "no printed labels were retained" in exceptions[0]["reason"]
    assert exceptions[0]["disposition"] == "client_review_required"

    with pytest.raises(ValueError, match="documents must be objects"):
        observations.build_templates(["not-an-object"])


def test_a_corpus_with_no_labels_refuses_rather_than_writing_an_empty_artifact(
    tmp_path, monkeypatch
):
    """Rule 9: an empty templates artifact reads as 'this corpus has no layouts'."""
    consensus = write_consensus(tmp_path, [{"document_id": "d1"}])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "template_observations.py",
            str(consensus),
            "--out",
            str(tmp_path / "t.json"),
            "--exceptions",
            str(tmp_path / "e.json"),
        ],
    )
    with pytest.raises(SystemExit, match="no observed template to write"):
        observations.main()

    for payload, message in (
        ({"documents": []}, "non-empty documents list"),
        ([], "non-empty documents list"),
    ):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps(payload))
        with pytest.raises(ValueError, match=message):
            observations.load_documents(bad)


def test_cli_writes_both_artifacts_and_refuses_to_overwrite(tmp_path, monkeypatch, capsys):
    """The command reports what it observed and never clobbers a prior run."""
    consensus = write_consensus(
        tmp_path, [document("d1", ["Total Paid", "Ship Date"]), {"document_id": "d2"}]
    )
    argv = [
        "template_observations.py",
        str(consensus),
        "--out",
        str(tmp_path / "templates.json"),
        "--exceptions",
        str(tmp_path / "exceptions.json"),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    assert observations.main() == 0
    printed = capsys.readouterr().out
    assert "Observed templates: 1 from 2 documents" in printed
    assert "distinct printed labels: 2" in printed
    assert "documents contributing no template: 1" in printed

    # A run where every document contributed a template prints no exception line.
    clean = write_consensus(tmp_path, [document("d9", ["Total Paid"])], name="clean.json")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "template_observations.py",
            str(clean),
            "--out",
            str(tmp_path / "clean_templates.json"),
            "--exceptions",
            str(tmp_path / "clean_exceptions.json"),
        ],
    )
    assert observations.main() == 0
    assert "contributing no template" not in capsys.readouterr().out

    # A run directory is organised by phase, so the destination usually does not
    # exist yet. Every other writer creates it; refusing with a bare
    # FileNotFoundError made this the one command an operator had to mkdir for.
    nested = tmp_path / "templates" / "phase3"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "template_observations.py",
            str(consensus),
            "--out",
            str(nested / "t.json"),
            "--exceptions",
            str(nested / "e.json"),
            "--quiet",
        ],
    )
    assert observations.main() == 0
    assert (nested / "t.json").is_file() and (nested / "e.json").is_file()

    written = json.loads((tmp_path / "templates.json").read_text())
    assert written["artifact_type"] == observations.ARTIFACT_TYPE
    assert written["summary"] == {
        "documents": 2,
        "templates": 1,
        "distinct_labels": 2,
        "documents_without_labels": 1,
        "non_cohesive_templates": 0,
    }

    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        observations.main()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "template_observations.py",
            str(consensus),
            "--out",
            str(tmp_path / "q.json"),
            "--exceptions",
            str(tmp_path / "qe.json"),
            "--quiet",
        ],
    )
    assert observations.main() == 0
    assert capsys.readouterr().out == ""


def test_what_this_writes_is_accepted_by_all_three_consuming_lanes(tmp_path, monkeypatch):
    """The point of the producer: three lanes that could not run, now can."""
    schema_discovery = importlib.import_module("schema_discovery")
    allocation_policy = importlib.import_module("allocation_policy")
    template_drift = importlib.import_module("template_drift")

    consensus = write_consensus(
        tmp_path,
        [document("d1", ["Total Paid", "Comm. Date Paid"]), document("d2", ["Total Paid"])],
    )
    out = tmp_path / "templates.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "template_observations.py",
            str(consensus),
            "--out",
            str(out),
            "--exceptions",
            str(tmp_path / "exceptions.json"),
            "--quiet",
        ],
    )
    assert observations.main() == 0

    assert len(schema_discovery.load_templates(out)) == 1
    assert len(allocation_policy.load_templates(out)) == 1
    assert len(template_drift.load_templates(out)) == 1


def test_a_union_of_unlike_layouts_is_not_reported_as_one_template():
    """645 documents sharing no common label once became a single 2,430-label template.

    Nothing refused it: drift compared it, mapping discovery proposed rules
    against its fingerprint, and every consumer read a layout identity that stood
    for nothing. The observation is still retained -- evidence is never dropped --
    but it now states that it is not a layout.
    """
    documents = [document(f"doc-{index}", [f"Label {index}"]) for index in range(1, 21)]
    templates, exceptions = observations.build_templates(documents)

    assert len(templates) == 1
    template = templates[0]
    assert template["document_count"] == 20
    assert template["cohesive_layout"] is False
    assert template["label_cohesion"] == 0.05

    layout_findings = [item for item in exceptions if item["field"] == "observed_template_layout"]
    assert len(layout_findings) == 1
    assert layout_findings[0]["priority"] == "high"
    assert "union of unlike layouts" in layout_findings[0]["reason"]
    assert layout_findings[0]["document_id"] == template["template_id"]


def test_documents_sharing_a_layout_stay_cohesive():
    """A genuine template keeps its labels across its documents and raises nothing."""
    shared = ["Invoice Number", "Invoice Date", "Amount Due"]
    documents = [document(f"doc-{index}", shared) for index in range(1, 6)]
    templates, exceptions = observations.build_templates(documents)

    assert len(templates) == 1
    assert templates[0]["cohesive_layout"] is True
    assert templates[0]["label_cohesion"] == 1.0
    assert [item for item in exceptions if item["field"] == "observed_template_layout"] == []


def test_cohesion_of_an_empty_or_single_document_template():
    """The floor never divides by zero and a lone document is its own layout."""
    assert observations.label_cohesion([], 5) == 0.0
    assert observations.label_cohesion([{"observed_in_documents": 1}], 0) == 0.0
    assert observations.label_cohesion([{"observed_in_documents": 1}], 1) == 1.0


def test_the_console_says_when_a_template_is_not_a_layout(tmp_path, monkeypatch, capsys):
    """An operator reading the console must not mistake a union for a template."""
    documents = [document(f"doc-{index}", [f"Label {index}"]) for index in range(1, 21)]
    consensus = write_consensus(tmp_path, documents, name="scattered.json")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "template_observations.py",
            str(consensus),
            "--out",
            str(tmp_path / "scattered_templates.json"),
            "--exceptions",
            str(tmp_path / "scattered_exceptions.json"),
        ],
    )
    assert observations.main() == 0
    output = capsys.readouterr().out
    assert "NOT A LAYOUT" in output
    assert "20 documents sharing no common label" in output
    assert "5.0% cohesion" in output


def test_an_agreed_classification_decides_the_family_the_rules_could_not(tmp_path):
    """645 documents once grouped under `unknown` because intake classified nothing."""
    import classification_consensus

    documents = [
        document("doc-1", ["ACK NO", "Amount Due"], family="unknown"),
        document("doc-2", ["ACK NO", "Amount Due"], family="unknown"),
    ]
    path = tmp_path / "classifications.json"
    path.write_text(
        json.dumps(
            {
                "artifact_type": classification_consensus.ARTIFACT_TYPE,
                "accepted": [
                    {"document_id": "doc-1", "document_type": "commission_statement"},
                    {"document_id": "doc-2", "document_type": "commission_statement"},
                ],
            }
        )
    )
    classifications = classification_consensus.classification_map(path)

    templates, _ = observations.build_templates(documents, classifications)
    assert len(templates) == 1
    assert templates[0]["document_family"] == "commission_statement"
    assert templates[0]["family_source"] == "classification_consensus"

    without, _ = observations.build_templates(documents)
    assert without[0]["document_family"] == "unknown"
    assert without[0]["family_source"] == "intake_classification"


def test_a_classification_artifact_from_another_run_is_refused(tmp_path, monkeypatch, capsys):
    """Rule 9: an artifact matching nothing would look applied and change nothing."""
    import classification_consensus

    consensus = write_consensus(
        tmp_path, [document("doc-1", ["ACK NO"])], name="mismatch_consensus.json"
    )
    foreign = tmp_path / "foreign.json"
    foreign.write_text(
        json.dumps(
            {
                "artifact_type": classification_consensus.ARTIFACT_TYPE,
                "accepted": [
                    {"document_id": "other-run-doc", "document_type": "commission_report"}
                ],
            }
        )
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "template_observations.py",
            str(consensus),
            "--classifications",
            str(foreign),
            "--out",
            str(tmp_path / "mismatch_templates.json"),
            "--exceptions",
            str(tmp_path / "mismatch_exceptions.json"),
        ],
    )
    with pytest.raises(SystemExit, match="matched no document"):
        observations.main()


def test_a_document_missing_from_the_classification_keeps_its_intake_family(tmp_path):
    """A partial classification decides only the documents it actually resolved."""
    documents = [
        document("doc-1", ["ACK NO"], family="unknown"),
        document("doc-2", ["Invoice Number"], family="purchase_order"),
    ]
    templates, _ = observations.build_templates(documents, {"doc-1": "commission_statement"})
    families = {t["document_family"]: t["family_source"] for t in templates}
    assert families == {
        "commission_statement": "classification_consensus",
        "purchase_order": "intake_classification",
    }


def test_a_matching_classification_is_applied_through_the_cli(tmp_path, monkeypatch, capsys):
    """The supplied artifact resolves the family the intake rules could not."""
    import classification_consensus

    consensus = write_consensus(
        tmp_path, [document("doc-1", ["ACK NO"], family="unknown")], name="applied_consensus.json"
    )
    classifications = tmp_path / "applied.json"
    classifications.write_text(
        json.dumps(
            {
                "artifact_type": classification_consensus.ARTIFACT_TYPE,
                "accepted": [{"document_id": "doc-1", "document_type": "commission_statement"}],
            }
        )
    )
    out = tmp_path / "applied_templates.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "template_observations.py",
            str(consensus),
            "--classifications",
            str(classifications),
            "--out",
            str(out),
            "--exceptions",
            str(tmp_path / "applied_exceptions.json"),
            "--quiet",
        ],
    )
    assert observations.main() == 0
    written = json.loads(out.read_text())
    assert written["templates"][0]["document_family"] == "commission_statement"
    assert written["templates"][0]["family_source"] == "classification_consensus"
