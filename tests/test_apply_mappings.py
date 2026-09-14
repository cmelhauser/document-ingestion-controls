"""An approved mapping must reach the record the document controls actually read.

An engine that cannot place a printed label in the controlled vocabulary retains
it as a source-labelled proposal. The approved registry was read only by the
table lanes, so nothing carried a rule back to the document record. A corpus
printing acknowledgement numbers on almost every page reported
`no_explicit_attribution_key` for 715 of 716 documents as a result.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import apply_mappings as lane  # noqa: E402
import schema_discovery  # noqa: E402


def template(labels, family="commission_statement", documents=("doc-1",)):
    """Build an observed template binding documents to one layout."""
    return {
        "template_id": f"observed-{family}",
        "document_family": family,
        "headers": [
            {"source_label": label, "observed_by_engines": ["a"], "observed_in_documents": 1}
            for label in labels
        ],
        "source_labels": list(labels),
        "document_count": len(documents),
        "evidence": [{"document_id": d, "page_id": d, "region_id": ""} for d in documents],
    }


def proposals(*pairs):
    """Build one engine's source-labelled proposal block."""
    block = {}
    for index, (label, value) in enumerate(pairs, start=1):
        block[f"source_labelled_fields.field_{index}.source_label"] = {"value": label}
        block[f"source_labelled_fields.field_{index}.observed_value"] = {"value": value}
    return block


def document(document_id="doc-1", engines=None, header=None):
    return {
        "document_id": document_id,
        "header": header or {},
        "source_labelled_field_proposals": engines or {},
    }


def registry(fingerprint, label="ACK NO", field="acknowledgement_number", **overrides):
    rule = {
        "rule_id": "rule-1",
        "status": "client_approved",
        "approval_authority": "client",
        "approved_by": None,
        "rule_type": "source_label_mapping",
        "template_fingerprint": fingerprint,
        "source_label": label,
        "canonical_field": field,
    }
    rule.update(overrides)
    return {"rules": [rule]}


def fingerprint_of(tpl):
    return schema_discovery.template_fingerprint(tpl)


def test_two_engines_agreeing_promote_the_value_into_the_controlled_field():
    tpl = template(["ACK NO"])
    doc = document(
        engines={
            "openai/model": proposals(("ACK NO", "142437")),
            "anthropic/model": proposals(("ACK NO", "142437")),
        }
    )
    promoted, exceptions = lane.promote(
        doc, fingerprint_of(tpl), lane.approved_rules(registry(fingerprint_of(tpl)))
    )
    assert exceptions == []
    assert promoted[0]["canonical_field"] == "acknowledgement_number"
    assert promoted[0]["value"] == "142437"
    assert promoted[0]["agreeing_engines"] == ["anthropic/model", "openai/model"]
    assert promoted[0]["consensus_flag"] == "consensus_2of2"


def test_a_promoted_value_is_written_to_the_field_the_export_reads():
    """Written to the header alone, 393 approved mappings never reached the export."""
    promotion = {
        "document_id": "doc-1",
        "canonical_field": "acknowledgement_number",
        "source_label": "ACK NO",
        "registry_rule_id": "rule-1",
        "approval_authority": "client",
        "value": "142437",
        "agreeing_engines": ["anthropic/model", "openai/model"],
        "consensus_flag": "consensus_2of2",
    }
    held = {
        "document_id": "doc-2",
        "header": {},
        "fields": {"header.acknowledgement_number": {"value": "9"}},
    }
    record, kept = lane.augmented_records(
        [document(), held], {"doc-1": [promotion], "doc-2": [{**promotion, "document_id": "doc-2"}]}
    )
    field = record["fields"]["header.acknowledgement_number"]
    assert (field["value"], field["accepted"]) == ("142437", True)
    assert field["acceptance"]["accepted_by"] == lane.MAPPING_ACCEPTED_BY
    assert field["acceptance"]["registry_rule_id"] == "rule-1"
    assert record["header"]["acknowledgement_number"]["mapped_from_source_label"] == "ACK NO"
    # A field an engine already filled is never overwritten, from the field side either.
    assert kept["fields"]["header.acknowledgement_number"] == {"value": "9"}
    assert kept["applied_source_label_mappings"][0]["applied"] is False
    corroborated = lane.mapped_field(
        {**promotion, "consensus_flag": lane.INDEPENDENT_EVIDENCE_FLAG}
    )
    assert corroborated["acceptance"]["accepted_by"] == lane.INDEPENDENT_EVIDENCE_FLAG


def test_a_single_engine_reading_is_never_promoted():
    """A controlled value resting on one reading is not consensus."""
    tpl = template(["ACK NO"])
    doc = document(engines={"openai/model": proposals(("ACK NO", "142437"))})
    promoted, exceptions = lane.promote(
        doc, fingerprint_of(tpl), lane.approved_rules(registry(fingerprint_of(tpl)))
    )
    assert promoted == []
    assert "only 1 engine read the source label" in exceptions[0]["reason"]
    assert exceptions[0]["canonical_field"] == "acknowledgement_number"
    assert exceptions[0]["disposition"] == "client_review_required"


def test_engines_disagreeing_on_the_value_are_retained_not_promoted():
    tpl = template(["ACK NO"])
    doc = document(
        engines={
            "openai/model": proposals(("ACK NO", "142437")),
            "anthropic/model": proposals(("ACK NO", "999999")),
        }
    )
    promoted, exceptions = lane.promote(
        doc, fingerprint_of(tpl), lane.approved_rules(registry(fingerprint_of(tpl)))
    )
    assert promoted == []
    assert "disagree on the value" in exceptions[0]["reason"]
    assert exceptions[0]["priority"] == "high"


def test_an_unapproved_proposal_promotes_nothing():
    """Only an approval moves a value; a proposal is still a proposal."""
    tpl = template(["ACK NO"])
    unapproved = registry(fingerprint_of(tpl), status="proposed")
    doc = document(
        engines={
            "openai/model": proposals(("ACK NO", "142437")),
            "anthropic/model": proposals(("ACK NO", "142437")),
        }
    )
    promoted, exceptions = lane.promote(doc, fingerprint_of(tpl), lane.approved_rules(unapproved))
    assert promoted == [] and exceptions == []


def test_an_engagement_owner_approval_promotes_and_keeps_its_authority():
    tpl = template(["ACK NO"])
    owned = registry(
        fingerprint_of(tpl),
        status="engagement_owner_approved",
        approval_authority="engagement_owner",
        approved_by="C. Melhauser",
    )
    doc = document(
        engines={
            "openai/model": proposals(("ACK NO", "142437")),
            "anthropic/model": proposals(("ACK NO", "142437")),
        }
    )
    promoted, _ = lane.promote(doc, fingerprint_of(tpl), lane.approved_rules(owned))
    assert promoted[0]["approval_authority"] == "engagement_owner"
    assert promoted[0]["approved_by"] == "C. Melhauser"


def test_labels_join_on_the_printed_label_not_the_engine_local_field_id():
    """Two engines rarely produce the same field identifier for one label."""
    tpl = template(["Amount Due"])
    doc = document(
        engines={
            "openai/model": {
                "source_labelled_fields.field_2edce31.source_label": {"value": " Amount  Due "},
                "source_labelled_fields.field_2edce31.observed_value": {"value": "10.00"},
            },
            "anthropic/model": {
                "source_labelled_fields.field_ffffff.source_label": {"value": "amount due"},
                "source_labelled_fields.field_ffffff.observed_value": {"value": "10.00"},
            },
        }
    )
    rules = lane.approved_rules(
        registry(fingerprint_of(tpl), label="Amount Due", field="amount_due")
    )
    promoted, exceptions = lane.promote(doc, fingerprint_of(tpl), rules)
    assert exceptions == []
    assert promoted[0]["value"] == "10.00"


def test_a_populated_controlled_field_is_never_overwritten():
    """An engine that placed the value itself outranks a promotion."""
    tpl = template(["ACK NO"])
    doc = document(
        header={"acknowledgement_number": {"value": "already-here"}},
        engines={
            "openai/model": proposals(("ACK NO", "142437")),
            "anthropic/model": proposals(("ACK NO", "142437")),
        },
    )
    promoted, _ = lane.promote(
        doc, fingerprint_of(tpl), lane.approved_rules(registry(fingerprint_of(tpl)))
    )
    records = lane.augmented_records([doc], {"doc-1": promoted})
    header = records[0]["header"]["acknowledgement_number"]
    assert header["value"] == "already-here"
    applied = records[0]["applied_source_label_mappings"][0]
    assert applied["applied"] is False
    assert applied["reason"] == "field already populated"


def test_an_accepted_promotion_carries_its_provenance_into_the_header():
    tpl = template(["ACK NO"])
    doc = document(
        engines={
            "openai/model": proposals(("ACK NO", "142437")),
            "anthropic/model": proposals(("ACK NO", "142437")),
        }
    )
    promoted, _ = lane.promote(
        doc, fingerprint_of(tpl), lane.approved_rules(registry(fingerprint_of(tpl)))
    )
    records = lane.augmented_records([doc], {"doc-1": promoted})
    header = records[0]["header"]["acknowledgement_number"]
    assert header["value"] == "142437"
    assert header["mapped_from_source_label"] == "ACK NO"
    assert header["registry_rule_id"] == "rule-1"
    assert header["consensus_flag"] == "consensus_2of2"
    # The source artifact is never edited.
    assert doc["header"] == {}


def test_document_fingerprints_refuses_templates_binding_nothing():
    with pytest.raises(ValueError, match="bound no document"):
        lane.document_fingerprints({"templates": []})
    with pytest.raises(ValueError, match="must be an object"):
        lane.document_fingerprints({"templates": ["nope"]})


def test_engine_labels_skips_incomplete_and_malformed_blocks():
    doc = document(
        engines={
            "openai/model": {
                "source_labelled_fields.field_1.source_label": {"value": "Only A Label"},
                "source_labelled_fields.field_2.observed_value": {"value": "orphan value"},
                "source_labelled_fields.field_3.source_label": {"value": "Empty"},
                "source_labelled_fields.field_3.observed_value": {"value": ""},
                "source_labelled_fields.field_4.source_label": "not-an-object",
            },
            "broken/model": "not-a-dict",
        }
    )
    assert lane.engine_labels(doc) == {}


def test_the_cli_writes_three_artifacts_and_refuses_to_overwrite(tmp_path, capsys):
    tpl = template(["ACK NO"])
    consensus = tmp_path / "consensus.json"
    consensus.write_text(
        json.dumps(
            {
                "documents": [
                    document(
                        engines={
                            "openai/model": proposals(("ACK NO", "142437")),
                            "anthropic/model": proposals(("ACK NO", "142437")),
                        }
                    )
                ]
            }
        )
    )
    templates = tmp_path / "templates.json"
    templates.write_text(json.dumps({"templates": [tpl]}))
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps(registry(fingerprint_of(tpl))))

    argv = [
        str(consensus),
        str(templates),
        "--registry",
        str(reg),
        "--out",
        str(tmp_path / "out" / "applied.json"),
        "--records-out",
        str(tmp_path / "out" / "records.json"),
        "--exceptions",
        str(tmp_path / "out" / "exceptions.json"),
    ]
    assert lane.main(argv) == 0

    applied = json.loads((tmp_path / "out" / "applied.json").read_text())
    assert applied["artifact_type"] == lane.ARTIFACT_TYPE
    assert applied["summary"] == {
        "documents": 1,
        "approved_rules": 1,
        "promoted": 1,
        "unresolved": 0,
    }
    assert applied["clears_no_control"] is True

    records = json.loads((tmp_path / "out" / "records.json").read_text())
    assert records["artifact_type"] == lane.RECORDS_ARTIFACT_TYPE
    assert records["documents"][0]["header"]["acknowledgement_number"]["value"] == "142437"

    assert "values promoted into controlled fields: 1" in capsys.readouterr().out

    with pytest.raises(SystemExit, match="refusing to overwrite"):
        lane.main(argv)


def test_the_cli_says_plainly_when_no_rule_has_been_approved(tmp_path, capsys):
    """Rule 9: a lane that applied nothing must not read as though it had."""
    tpl = template(["ACK NO"])
    consensus = tmp_path / "consensus.json"
    consensus.write_text(json.dumps({"documents": [document()]}))
    templates = tmp_path / "templates.json"
    templates.write_text(json.dumps({"templates": [tpl]}))
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps({"rules": []}))

    assert (
        lane.main(
            [
                str(consensus),
                str(templates),
                "--registry",
                str(reg),
                "--out",
                str(tmp_path / "a.json"),
                "--records-out",
                str(tmp_path / "b.json"),
                "--exceptions",
                str(tmp_path / "c.json"),
            ]
        )
        == 0
    )
    assert "no approved rule exists yet" in capsys.readouterr().out


def test_the_cli_refuses_a_single_engine_floor_and_an_empty_corpus(tmp_path):
    tpl = template(["ACK NO"])
    consensus = tmp_path / "consensus.json"
    consensus.write_text(json.dumps({"documents": [document()]}))
    templates = tmp_path / "templates.json"
    templates.write_text(json.dumps({"templates": [tpl]}))
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps({"rules": []}))
    base = [str(consensus), str(templates), "--registry", str(reg)]

    with pytest.raises(SystemExit, match="minimum engines must be at least 2"):
        lane.main(
            base
            + [
                "--out",
                str(tmp_path / "x.json"),
                "--records-out",
                str(tmp_path / "y.json"),
                "--exceptions",
                str(tmp_path / "z.json"),
                "--minimum-engines",
                "1",
            ]
        )

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"documents": []}))
    with pytest.raises(SystemExit, match="non-empty documents list"):
        lane.main(
            [
                str(empty),
                str(templates),
                "--registry",
                str(reg),
                "--out",
                str(tmp_path / "p.json"),
                "--records-out",
                str(tmp_path / "q.json"),
                "--exceptions",
                str(tmp_path / "r.json"),
            ]
        )


def test_a_document_no_template_observed_is_skipped_not_guessed(tmp_path, capsys):
    """A document outside every observed template has no fingerprint to match."""
    tpl = template(["ACK NO"], documents=("doc-1",))
    consensus = tmp_path / "consensus.json"
    consensus.write_text(
        json.dumps(
            {
                "documents": [
                    document(
                        document_id="doc-unseen",
                        engines={
                            "openai/model": proposals(("ACK NO", "1")),
                            "anthropic/model": proposals(("ACK NO", "1")),
                        },
                    )
                ]
            }
        )
    )
    templates = tmp_path / "templates.json"
    templates.write_text(json.dumps({"templates": [tpl]}))
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps(registry(fingerprint_of(tpl))))
    assert (
        lane.main(
            [
                str(consensus),
                str(templates),
                "--registry",
                str(reg),
                "--out",
                str(tmp_path / "a.json"),
                "--records-out",
                str(tmp_path / "b.json"),
                "--exceptions",
                str(tmp_path / "c.json"),
                "--quiet",
            ]
        )
        == 0
    )
    applied = json.loads((tmp_path / "a.json").read_text())
    assert applied["summary"]["promoted"] == 0
    assert capsys.readouterr().out == ""


def test_a_slot_equivalence_rule_never_promotes_a_source_label():
    """The registry holds more than one rule type; only label mappings apply here."""
    tpl = template(["ACK NO"])
    mixed = {
        "rules": [
            {
                "rule_id": "slot-1",
                "status": "client_approved",
                "rule_type": "slot_equivalence",
                "field_a": "amount_due",
                "field_b": "balance_due",
            }
        ]
    }
    assert lane.approved_rules(mixed) == {}
    doc = document(
        engines={
            "openai/model": proposals(("ACK NO", "1")),
            "anthropic/model": proposals(("ACK NO", "1")),
        }
    )
    promoted, exceptions = lane.promote(doc, fingerprint_of(tpl), lane.approved_rules(mixed))
    assert promoted == [] and exceptions == []


def test_template_evidence_without_a_document_id_binds_nothing():
    tpl = template(["ACK NO"])
    tpl["evidence"] = [{"page_id": "no-document-id", "region_id": ""}, {"document_id": "doc-1"}]
    assert lane.document_fingerprints({"templates": [tpl]}) == {"doc-1": fingerprint_of(tpl)}


def test_unrelated_proposal_paths_are_ignored():
    """A source-labelled block carries leaves this lane does not read."""
    doc = document(
        engines={
            "openai/model": {
                "source_labelled_fields.field_1.source_label": {"value": "ACK NO"},
                "source_labelled_fields.field_1.observed_value": {"value": "142437"},
                "source_labelled_fields.field_1.confidence": {"value": 0.99},
                "source_labelled_fields.field_1.semantic_type": {"value": "customer_ack"},
            }
        }
    )
    assert lane.engine_labels(doc) == {"openai/model": {"ack no": ("ACK NO", "142437")}}


def docai(page="doc-1", headers=("Project\nACK NO\n",), cell_label="ACK NO", read="142437"):
    """One independent extractor record, shaped the way Document AI writes them."""
    return [
        {
            "page_id": page,
            "source_tables": [
                {
                    "source_headers": list(headers),
                    "source_rows": [
                        {"cells": [{"source_label": cell_label, "evidence_text": read}]}
                    ],
                }
            ],
        }
    ]


def test_an_independent_extractor_reading_the_caption_is_the_second_reading(tmp_path):
    """750 of 1,642 refusals on one run were a label Document AI had also read.

    The captions arrive merged -- `"Project\\nACK NO\\n"` -- so the printed label
    is a substring of the header cell rather than equal to it.
    """
    evidence = tmp_path / "docai.json"
    evidence.write_text(json.dumps(docai()))
    pages = lane.independent_label_evidence([evidence])

    tpl = template(["ACK NO"])
    doc = document(engines={"openai/model": proposals(("ACK NO", "142437"))})
    promoted, exceptions = lane.promote(
        doc,
        fingerprint_of(tpl),
        lane.approved_rules(registry(fingerprint_of(tpl))),
        independent_evidence=pages["doc-1"],
    )
    assert exceptions == []
    assert promoted[0]["value"] == "142437"
    # Never labelled consensus: no second model vendor was involved.
    assert promoted[0]["consensus_flag"] == lane.INDEPENDENT_EVIDENCE_FLAG
    assert promoted[0]["independent_evidence"]["vendor_agreement"] is False
    assert promoted[0]["independent_evidence"]["match_scope"] == "page"

    # The caption is not the value. A lone engine that emitted the whole column
    # as one string did not read a field, and no run of the page says otherwise:
    # on a real run 183 of 742 caption-only promotions looked like this, and one
    # entered attribution at $600,013,074 against a printed total of $8,673.
    concatenated = document(engines={"openai/model": proposals(("ACK NO", "142437 998001 771020"))})
    _, refused_value = lane.promote(
        concatenated,
        fingerprint_of(tpl),
        lane.approved_rules(registry(fingerprint_of(tpl))),
        independent_evidence=pages["doc-1"],
    )
    assert "only 1 engine read the source label" in refused_value[0]["reason"]
    assert lane.value_independently_read("142437", pages["doc-1"])
    assert not lane.value_independently_read("142437 998001 771020", pages["doc-1"])
    assert not lane.value_independently_read("142437", None)
    assert not lane.value_independently_read("142437", {"captions": "x", "readings": ""})
    assert promoted[0]["independent_evidence"]["value_corroborated"] is True
    assert promoted[0]["independent_evidence"]["caption_corroborated"] is True

    # A label the independent extractor never read is still refused.
    other = template(["DEALER REF"])
    doc2 = document(engines={"openai/model": proposals(("DEALER REF", "9"))})
    _, refused = lane.promote(
        doc2,
        fingerprint_of(other),
        lane.approved_rules(registry(fingerprint_of(other), label="DEALER REF")),
        independent_evidence=pages["doc-1"],
    )
    assert "only 1 engine read the source label" in refused[0]["reason"]

    # Without evidence for the page, nothing changes.
    assert not lane.label_independently_read("ACK NO", None)
    assert not lane.label_independently_read("", pages["doc-1"])
    # A short label is matched as a whole word, never as a substring.
    assert not lane.label_independently_read("#", pages["doc-1"])
    assert lane.label_independently_read("no", {"captions": "project ack no", "readings": ""})

    # Rule 9: evidence carrying no captions corroborates nothing.
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps([{"page_id": "doc-1", "source_tables": []}, {"no_page": 1}]))
    with pytest.raises(ValueError, match="control that processed nothing"):
        lane.independent_label_evidence([empty])
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"records": "not a list"}))
    with pytest.raises(ValueError, match="not an extractor artifact"):
        lane.independent_label_evidence([wrong])
    # A dict-wrapped artifact is read the same way as a bare list.
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"records": docai()}))
    assert lane.independent_label_evidence([wrapped])


def test_promoting_on_independent_evidence_requires_a_named_decision(tmp_path):
    """It accepts a controlled value on weaker evidence than vendor agreement."""
    tpl = template(["ACK NO"])
    consensus = tmp_path / "consensus.json"
    consensus.write_text(
        json.dumps(
            {"documents": [document(engines={"openai/model": proposals(("ACK NO", "142437"))})]}
        )
    )
    templates = tmp_path / "templates.json"
    templates.write_text(json.dumps({"templates": [tpl]}))
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps(registry(fingerprint_of(tpl))))
    evidence = tmp_path / "docai.json"
    evidence.write_text(json.dumps(docai()))

    def argv(out, *extra):
        return [
            str(consensus),
            str(templates),
            "--registry",
            str(reg),
            "--out",
            str(tmp_path / out / "applied.json"),
            "--records-out",
            str(tmp_path / out / "records.json"),
            "--exceptions",
            str(tmp_path / out / "exceptions.json"),
            *extra,
        ]

    with pytest.raises(SystemExit, match="name the client decision"):
        lane.main(argv("a", "--independent-evidence", str(evidence)))
    with pytest.raises(SystemExit, match="applies only to --independent-evidence"):
        lane.main(argv("b", "--authorization", "decision-5"))

    assert (
        lane.main(
            argv(
                "c",
                "--independent-evidence",
                str(evidence),
                "--authorization",
                "decision-5",
            )
        )
        == 0
    )
    applied = json.loads((tmp_path / "c" / "applied.json").read_text())
    assert applied["summary"]["promoted"] == 1
    assert applied["independent_evidence_authorization"] == "decision-5"
    assert applied["independent_evidence_sources"] == ["docai.json"]
    assert applied["promotions"][0]["consensus_flag"] == lane.INDEPENDENT_EVIDENCE_FLAG
