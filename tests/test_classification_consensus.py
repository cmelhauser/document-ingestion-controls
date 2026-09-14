"""A document type is accepted only where independent model vendors already agreed.

The rule classifier is deliberately conservative, so a client whose documents
carry no distinctive printed phrase leaves the whole corpus unclassified. Both
extraction engines classify anyway, and that retained evidence is what this lane
turns into a decision -- under the same independence standard consensus applies
to every other fact.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import classification_consensus as lane  # noqa: E402


def engine(vendor, document_type):
    """Build one verified engine payload as `consensus.load_records` returns it."""
    return {
        "model_document_type": document_type,
        "_verified_identity": {"model_vendor": vendor},
    }


def test_two_independent_vendors_agreeing_accepts_the_type():
    decision, exception = lane.resolve(
        "doc-1",
        {
            "openai/model": engine("openai", "commission_statement"),
            "openrouter/x-ai/model": engine("x-ai", "commission_statement"),
        },
    )
    assert exception is None
    assert decision["document_type"] == "commission_statement"
    assert decision["agreeing_vendors"] == ["openai", "x-ai"]
    assert decision["evidence"] == "independent_model_vendor_agreement"


def test_one_vendor_alone_is_a_proposal_not_a_classification():
    decision, exception = lane.resolve(
        "doc-2", {"openai/model": engine("openai", "commission_report")}
    )
    assert decision is None
    assert "only 1 independent model vendor" in exception["reason"]
    assert exception["disposition"] == "client_review_required"


def test_disagreeing_vendors_are_retained_with_both_answers():
    decision, exception = lane.resolve(
        "doc-3",
        {
            "openai/model": engine("openai", "payment_confirmation"),
            "openrouter/x-ai/model": engine("x-ai", "remittance_advice"),
        },
    )
    assert decision is None
    assert "disagree" in exception["reason"]
    assert "openai=payment_confirmation" in exception["reason"]
    assert "x-ai=remittance_advice" in exception["reason"]


def test_shared_uncertainty_is_never_agreement():
    """Two engines both saying "unknown" have not classified anything."""
    for value in ("unknown", "", None, "  UNKNOWN  "):
        decision, exception = lane.resolve(
            "doc-4",
            {
                "openai/model": engine("openai", value),
                "openrouter/x-ai/model": engine("x-ai", value),
            },
        )
        assert decision is None
        assert exception["reason"] == "no engine proposed a document type for this page"


def test_a_wrapped_confidence_object_is_read_and_an_unidentified_engine_ignored():
    decision, exception = lane.resolve(
        "doc-5",
        {
            "openai/model": {
                "model_document_type": {"value": "purchase_order", "confidence": 0.9},
                "_verified_identity": {"model_vendor": "openai"},
            },
            "openrouter/x-ai/model": engine("x-ai", "purchase_order"),
            "unidentified": {"model_document_type": "purchase_order"},
        },
    )
    assert exception is None
    assert decision["agreeing_vendors"] == ["openai", "x-ai"]


def test_an_even_split_across_four_vendors_resolves_nothing():
    """Two types each reaching the floor is ambiguity, not a decision."""
    decision, exception = lane.resolve(
        "doc-6",
        {
            "a": engine("openai", "commission_statement"),
            "b": engine("x-ai", "commission_statement"),
            "c": engine("anthropic", "commission_report"),
            "d": engine("google", "commission_report"),
        },
    )
    assert decision is None
    assert "disagree" in exception["reason"]


def test_build_sorts_and_separates_accepted_from_unresolved():
    accepted, exceptions = lane.build(
        {
            "doc-b": {
                "one": engine("openai", "receipt"),
                "two": engine("x-ai", "receipt"),
            },
            "doc-a": {"one": engine("openai", "receipt")},
        }
    )
    assert [item["document_id"] for item in accepted] == ["doc-b"]
    assert [item["document_id"] for item in exceptions] == ["doc-a"]


def test_classification_map_reads_accepted_entries(tmp_path):
    path = tmp_path / "classifications.json"
    path.write_text(
        json.dumps(
            {
                "artifact_type": lane.ARTIFACT_TYPE,
                "accepted": [{"document_id": "doc-1", "document_type": "commission_report"}],
            }
        )
    )
    assert lane.classification_map(path) == {"doc-1": "commission_report"}


def test_classification_map_refuses_a_foreign_or_malformed_artifact(tmp_path):
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"artifact_type": "something_else", "accepted": []}))
    with pytest.raises(ValueError, match=lane.ARTIFACT_TYPE):
        lane.classification_map(wrong)

    not_a_list = tmp_path / "not_a_list.json"
    not_a_list.write_text(json.dumps({"artifact_type": lane.ARTIFACT_TYPE, "accepted": {}}))
    with pytest.raises(ValueError, match="accepted must be a list"):
        lane.classification_map(not_a_list)

    not_an_object = tmp_path / "not_an_object.json"
    not_an_object.write_text(json.dumps({"artifact_type": lane.ARTIFACT_TYPE, "accepted": ["x"]}))
    with pytest.raises(ValueError, match="must be an object"):
        lane.classification_map(not_an_object)

    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(
        json.dumps({"artifact_type": lane.ARTIFACT_TYPE, "accepted": [{"document_id": "d"}]})
    )
    with pytest.raises(ValueError, match="requires document_id and document_type"):
        lane.classification_map(incomplete)


def _handoff(tmp_path, name, provider, model, lane, document_type):
    """Write a verifiable extraction handoff carrying one engine's own classification."""
    import hashlib

    raw = tmp_path / f"{name}-raw.json"
    raw.write_text(json.dumps({"document_type": document_type}) + "\n")
    prefix = {"openai": "openai", "openrouter": "openrouter"}[provider]
    engine_name = f"{prefix}/{model}"
    records = [
        {
            "engine": engine_name,
            "engine_version": model,
            "document_id": "doc-1",
            "page_id": "doc-1",
            "page_sha256": "a" * 64,
            "raw_response": str(raw),
            "raw_response_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
            "document_type": "unknown",
            "model_document_type": document_type,
        }
    ]
    data = {
        "schema_version": "independent_extraction_handoff_v1",
        "adapter_type": "ocr",
        "engine": engine_name,
        "provider": provider,
        "model": model,
        "lane": lane,
        "independence_group": provider,
        "records_sha256": hashlib.sha256(
            json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest(),
        "records": records,
        "raw_response_retention_required": True,
    }
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(data))
    return path


def test_the_cli_writes_both_artifacts_and_refuses_to_overwrite(tmp_path, capsys):
    """The intake verdict stays `unknown`; the engines' own agreement decides."""
    first = _handoff(tmp_path, "primary", "openai", "model-a", "consensus_primary", "receipt")
    second = _handoff(
        tmp_path, "secondary", "openrouter", "x-ai/model-b", "consensus_secondary", "receipt"
    )
    out = tmp_path / "phase2" / "classifications.json"
    exceptions = tmp_path / "phase2" / "classification_exceptions.json"

    argv = [str(first), str(second), "--out", str(out), "--exceptions", str(exceptions)]
    assert lane.main(argv) == 0

    written = json.loads(out.read_text())
    assert written["artifact_type"] == lane.ARTIFACT_TYPE
    assert written["summary"] == {"documents": 1, "accepted": 1, "unresolved": 0}
    assert written["accepted"][0]["document_type"] == "receipt"
    assert written["accepted"][0]["agreeing_vendors"] == ["openai", "x-ai"]
    assert written["clears_no_control"] is True
    assert [item["sha256"] for item in written["source_handoffs"]]
    assert json.loads(exceptions.read_text())["summary"] == {"count": 0}

    output = capsys.readouterr().out
    assert "accepted on independent vendor agreement: 1" in output

    with pytest.raises(SystemExit, match="refusing to overwrite"):
        lane.main(argv)


def test_the_cli_refuses_a_single_vendor_floor(tmp_path):
    first = _handoff(tmp_path, "primary", "openai", "model-a", "consensus_primary", "receipt")
    with pytest.raises(SystemExit, match="minimum vendors must be at least 2"):
        lane.main(
            [
                str(first),
                "--out",
                str(tmp_path / "o.json"),
                "--exceptions",
                str(tmp_path / "e.json"),
                "--minimum-vendors",
                "1",
            ]
        )


def test_the_cli_refuses_handoffs_that_retained_no_document(tmp_path, monkeypatch):
    first = _handoff(tmp_path, "primary", "openai", "model-a", "consensus_primary", "receipt")
    second = _handoff(
        tmp_path, "secondary", "openrouter", "x-ai/model-b", "consensus_secondary", "receipt"
    )
    monkeypatch.setattr(lane.consensus, "load_records", lambda paths: {})
    with pytest.raises(SystemExit, match="retained no document to classify"):
        lane.main(
            [
                str(first),
                str(second),
                "--out",
                str(tmp_path / "o.json"),
                "--exceptions",
                str(tmp_path / "e.json"),
                "--quiet",
            ]
        )


def test_the_quiet_flag_suppresses_the_summary_only(tmp_path, capsys):
    """Quiet changes console output; the retained artifacts are identical."""
    first = _handoff(tmp_path, "primary", "openai", "model-a", "consensus_primary", "receipt")
    second = _handoff(
        tmp_path, "secondary", "openrouter", "x-ai/model-b", "consensus_secondary", "receipt"
    )
    out = tmp_path / "quiet.json"
    assert (
        lane.main(
            [
                str(first),
                str(second),
                "--out",
                str(out),
                "--exceptions",
                str(tmp_path / "quiet_exceptions.json"),
                "--quiet",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == ""
    assert json.loads(out.read_text())["summary"]["accepted"] == 1
