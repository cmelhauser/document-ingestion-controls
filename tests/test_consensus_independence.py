import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

consensus = importlib.import_module("consensus")


def canonical_sha(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def handoff(tmp_path, name, provider, model, lane, value="10.00", **updates):
    raw = tmp_path / f"{name}-raw.json"
    raw.write_text(json.dumps({"value": value}) + "\n")
    engine_prefix = {
        "openai": "openai",
        "google": "google_genai_vertex",
        "openrouter": "openrouter",
        "anthropic": "anthropic",
    }[provider]
    engine = f"{engine_prefix}/{model}"
    records = [
        {
            "engine": engine,
            "engine_version": model,
            "document_id": "doc-1",
            "page_id": "doc-1",
            "page_sha256": "a" * 64,
            "raw_response": str(raw),
            "raw_response_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
            "total_amount": {"value": value, "source": "printed"},
        }
    ]
    data = {
        "schema_version": "independent_extraction_handoff_v1",
        "adapter_type": "ocr",
        "engine": engine,
        "provider": provider,
        "model": model,
        "lane": lane,
        "independence_group": provider,
        "records_sha256": canonical_sha(records),
        "records": records,
        "raw_response_retention_required": True,
    }
    data.update(updates)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(data))
    return path


def test_consensus_requires_two_verified_independent_provider_handoffs(tmp_path):
    first = handoff(tmp_path, "primary", "openai", "model-a", "consensus_primary")
    second = handoff(tmp_path, "secondary", "google", "model-b", "consensus_secondary")

    loaded = consensus.load_records([first, second])

    merged, exceptions = consensus.merge_document("doc-1", loaded["doc-1"])
    assert not exceptions
    assert merged["fields"]["total_amount"]["accepted"] is True
    assert {item["independence_group"] for item in merged["engine_identities"]} == {
        "openai",
        "google",
    }


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda data: data.pop("schema_version"), "schema_version"),
        (lambda data: data.update(provider="google"), "provider"),
        (lambda data: data.update(independence_group="renamed"), "independence_group"),
        (lambda data: data.update(records_sha256="0" * 64), "records hash"),
        (lambda data: data["records"][0].update(engine="renamed/model"), "record engine"),
        (lambda data: data["records"][0].update(raw_response_sha256="0" * 64), "raw response"),
    ],
)
def test_consensus_rejects_unverified_or_tampered_handoffs(tmp_path, mutator, message):
    path = handoff(tmp_path, "primary", "openai", "model-a", "consensus_primary")
    data = json.loads(path.read_text())
    mutator(data)
    path.write_text(json.dumps(data))

    with pytest.raises(ValueError, match=message):
        consensus.load_records([path])


def test_consensus_rejects_renamed_same_provider_and_duplicate_lanes(tmp_path):
    first = handoff(tmp_path, "one", "openai", "model-a", "consensus_primary")
    renamed = handoff(tmp_path, "different-file", "openai", "model-b", "consensus_secondary")
    with pytest.raises(ValueError, match="independence group"):
        consensus.load_records([first, renamed])

    second_primary = handoff(tmp_path, "again", "google", "model-b", "consensus_primary")
    with pytest.raises(ValueError, match="consensus lane"):
        consensus.load_records([first, second_primary])


def test_consensus_rejects_extraction_lane_and_copied_records(tmp_path):
    extraction = handoff(tmp_path, "extract", "openai", "model-a", "extraction")
    with pytest.raises(ValueError, match="consensus lane"):
        consensus.load_records([extraction])

    first = handoff(tmp_path, "one", "openai", "model-a", "consensus_primary")
    copied = handoff(tmp_path, "two", "google", "model-b", "consensus_secondary")
    first_data = json.loads(first.read_text())
    copied_data = json.loads(copied.read_text())
    copied_data["records"] = first_data["records"]
    copied_data["records_sha256"] = canonical_sha(copied_data["records"])
    copied.write_text(json.dumps(copied_data))
    with pytest.raises(ValueError, match="record engine"):
        consensus.load_records([first, copied])


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"adapter_type": "proposal"}, "adapter_type"),
        ({"provider": "unknown", "independence_group": "unknown"}, "unsupported provider"),
        ({"engine": 1}, "unsupported provider"),
        ({"engine": "openai/"}, "provider does not match engine"),
        ({"model": "other"}, "model does not match engine"),
        ({"model_configuration": {"model": "other"}}, "model_configuration"),
        ({"raw_response_retention_required": False}, "retention"),
        ({"records": None}, "records must be a list"),
    ],
)
def test_consensus_rejects_malformed_handoff_metadata(tmp_path, updates, message):
    path = handoff(tmp_path, "primary", "openai", "model-a", "consensus_primary")
    data = json.loads(path.read_text())
    data.update(updates)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=message):
        consensus.load_records([path])


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ("bad", "must be an object"),
        ({"engine": "openai/model-a", "engine_version": "model-a"}, "raw response"),
        (
            {
                "engine": "openai/model-a",
                "engine_version": "model-a",
                "raw_response": "missing.json",
                "raw_response_sha256": "0" * 64,
            },
            "invalid raw response evidence",
        ),
    ],
)
def test_consensus_rejects_malformed_record_evidence(tmp_path, record, message):
    path = handoff(tmp_path, "primary", "openai", "model-a", "consensus_primary")
    data = json.loads(path.read_text())
    data["records"] = [record]
    data["records_sha256"] = canonical_sha(data["records"])
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=message):
        consensus.load_records([path])


def test_consensus_resolves_relative_raw_paths_and_rejects_bad_page_or_duplicate(tmp_path):
    path = handoff(tmp_path, "primary", "openai", "model-a", "consensus_primary")
    data = json.loads(path.read_text())
    data["records"][0]["raw_response"] = Path(data["records"][0]["raw_response"]).name
    data["records_sha256"] = canonical_sha(data["records"])
    path.write_text(json.dumps(data))
    assert consensus.load_records([path])["doc-1"]

    data["records"][0]["page_sha256"] = "bad"
    data["records_sha256"] = canonical_sha(data["records"])
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="page_sha256"):
        consensus.load_records([path])

    duplicate = handoff(tmp_path, "duplicate", "openai", "model-a", "consensus_primary")
    duplicate_data = json.loads(duplicate.read_text())
    duplicate_data["records"].append(dict(duplicate_data["records"][0]))
    duplicate_data["records_sha256"] = canonical_sha(duplicate_data["records"])
    duplicate.write_text(json.dumps(duplicate_data))
    with pytest.raises(ValueError, match="duplicate engine"):
        consensus.load_records([duplicate])


def test_consensus_resolves_contained_run_relative_raw_paths(tmp_path):
    """Workspace-adapter handoffs keep raw evidence relative to the run root."""
    providers = tmp_path / "providers"
    raw_directory = providers / "raw" / "primary"
    raw_directory.mkdir(parents=True)
    raw = raw_directory / "page.json"
    raw.write_text('{"response": "retained"}\n')
    path = handoff(providers, "primary", "openai", "model-a", "consensus_primary")
    data = json.loads(path.read_text())
    data["raw_response_directory"] = "providers/raw/primary"
    data["records"][0]["raw_response"] = "providers/raw/primary/page.json"
    data["records"][0]["raw_response_sha256"] = hashlib.sha256(raw.read_bytes()).hexdigest()
    data["records_sha256"] = canonical_sha(data["records"])
    path.write_text(json.dumps(data))

    assert consensus.load_records([path])["doc-1"]


@pytest.mark.parametrize(
    ("handoff_data", "raw_response"),
    [
        ({}, "providers/raw/primary/page.json"),
        ({"raw_response_directory": "/outside/run"}, "providers/raw/primary/page.json"),
        ({"raw_response_directory": "../outside/run"}, "providers/raw/primary/page.json"),
        ({"raw_response_directory": "providers/raw/primary"}, "other/page.json"),
        (
            {"raw_response_directory": "providers/raw/primary"},
            "providers/raw/primary/../../../../outside-run/page.json",
        ),
    ],
)
def test_consensus_raw_path_resolver_refuses_undeclared_or_unsafe_run_roots(
    tmp_path, handoff_data, raw_response
):
    """Only a declared, contained raw directory may use a run-root path."""
    handoff_path = tmp_path / "providers" / "primary.json"
    expected_direct = handoff_path.parent / raw_response

    assert (
        consensus.resolve_raw_response_path(handoff_path, handoff_data, raw_response)
        == expected_direct
    )


def test_consensus_main_rejects_empty_verified_handoff(tmp_path, monkeypatch):
    path = handoff(tmp_path, "empty", "openai", "model-a", "consensus_primary")
    data = json.loads(path.read_text())
    data["records"] = []
    data["records_sha256"] = canonical_sha([])
    path.write_text(json.dumps(data))
    monkeypatch.setattr(sys, "argv", ["consensus.py", str(path)])
    with pytest.raises(SystemExit, match="No records with a document_id"):
        consensus.main()


def test_consensus_main_reports_retained_extension_proposals(tmp_path, monkeypatch, capsys):
    """The extension channel leaves consensus as proposals, and the run says so."""
    extension = {
        "field_1_1": {
            "source_label": {"value": "Certificate Reference", "source": "printed"},
            "observed_value": {"value": "CERT-7", "source": "printed"},
        }
    }
    paths = []
    for name, provider, model in (
        ("primary", "openai", "model-a"),
        ("secondary", "google", "model-b"),
    ):
        lane = "consensus_primary" if name == "primary" else "consensus_secondary"
        path = handoff(tmp_path, name, provider, model, lane)
        data = json.loads(path.read_text())
        data["records"][0]["source_labelled_fields"] = extension
        data["records_sha256"] = canonical_sha(data["records"])
        path.write_text(json.dumps(data))
        paths.append(str(path))

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "consensus.py",
            *paths,
            "--out",
            str(tmp_path / "consensus.json"),
            "--exceptions",
            str(tmp_path / "exceptions.json"),
        ],
    )
    consensus.main()
    assert "retained as mapping proposals" in capsys.readouterr().out
    written = json.loads((tmp_path / "consensus.json").read_text())
    assert written["summary"]["source_labelled_field_proposals"] == 4
    document = written["documents"][0]
    assert not any(path.startswith("source_labelled_fields") for path in document["fields"])
    assert set(document["source_labelled_field_proposals"]) == {
        "openai/model-a",
        "google_genai_vertex/model-b",
    }


def test_an_anthropic_lane_can_be_reconciled_with_another_vendor(tmp_path):
    """A supported provider absent from the prefix map reads as an unknown engine.

    An Anthropic secondary lane read a whole corpus and then could not be
    reconciled with the primary at all: consensus refused the handoff as
    "unsupported provider or engine", which is right for an unknown engine and
    wrong for a configured one.
    """
    primary = handoff(tmp_path, "primary", "openai", "gpt-5.6-luna", "consensus_primary")
    secondary = handoff(
        tmp_path,
        "secondary",
        "anthropic",
        "claude-haiku-4-5-20251001",
        "consensus_secondary",
        value="11.00",
    )
    records = consensus.load_records([primary, secondary])
    assert records, "both handoffs are accepted"
    engines = {
        engine for document in records.values() for engine in document if not engine.startswith("_")
    }
    assert engines == {"openai/gpt-5.6-luna", "anthropic/claude-haiku-4-5-20251001"}


def test_lanes_conditioned_by_different_corpus_notes_are_not_independent(tmp_path):
    # Coaching one engine on what a label means and not the other leaves two
    # readings whose agreement measures the coaching, not the page.
    first = handoff(
        tmp_path,
        "primary",
        "openai",
        "model-a",
        "consensus_primary",
        corpus_context={"file": "context.txt", "sha256": "a" * 64},
    )
    second = handoff(
        tmp_path,
        "secondary",
        "google",
        "model-b",
        "consensus_secondary",
        corpus_context={"file": "context.txt", "sha256": "b" * 64},
    )
    with pytest.raises(ValueError, match="are not independent readings"):
        consensus.load_records([first, second])


def test_conditioning_only_one_lane_is_refused(tmp_path):
    first = handoff(tmp_path, "primary", "openai", "model-a", "consensus_primary")
    second = handoff(
        tmp_path,
        "secondary",
        "google",
        "model-b",
        "consensus_secondary",
        corpus_context={"file": "context.txt", "sha256": "b" * 64},
    )
    with pytest.raises(ValueError, match="are not independent readings"):
        consensus.load_records([first, second])


def test_both_lanes_conditioned_by_the_same_context_are_accepted(tmp_path):
    context = {"file": "context.txt", "sha256": "c" * 64}
    first = handoff(
        tmp_path, "primary", "openai", "model-a", "consensus_primary", corpus_context=context
    )
    second = handoff(
        tmp_path,
        "secondary",
        "google",
        "model-b",
        "consensus_secondary",
        corpus_context=dict(context),
    )
    assert set(consensus.load_records([first, second])) == {"doc-1"}
    assert consensus.handoff_corpus_context([first, second]) == context


def test_a_malformed_corpus_context_is_refused(tmp_path):
    path = handoff(
        tmp_path,
        "primary",
        "openai",
        "model-a",
        "consensus_primary",
        corpus_context="notes.txt",
    )
    with pytest.raises(ValueError, match="corpus_context must be an object or null"):
        consensus.load_records([path])


def test_two_vendors_through_one_router_are_independent_when_each_read_the_source():
    """A router billing for both readings does not make them one reading.

    x-ai's weights and Google's are different weights. What would make them one
    reading is a shared reader, so the test is what read the source -- not who
    invoiced for it.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        first = handoff(
            tmp,
            "grok",
            "openrouter",
            "x-ai/grok-4.20",
            "consensus_primary",
            source_read_by="model",
        )
        second = handoff(
            tmp,
            "gemini",
            "openrouter",
            "google/gemini-2.5-flash",
            "consensus_secondary",
            source_read_by="model",
        )
        loaded = consensus.load_records([first, second])
        merged, exceptions = consensus.merge_document("doc-1", loaded["doc-1"])
        assert not exceptions
        assert {item["model_vendor"] for item in merged["engine_identities"]} == {
            "x-ai",
            "google",
        }


def test_one_router_is_refused_when_a_lane_did_not_read_the_source_itself():
    """OpenRouter's default PDF handling OCRs once and feeds both models.

    Both then inherit that one pass's errors identically, which is corroboration
    that is not corroboration -- so the shared transport is refused unless every
    lane on it read the source itself.
    """
    import tempfile

    for readers in (("model", "mistral-ocr"), ("mistral-ocr", "model"), (None, None)):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            updates = [{"source_read_by": value} if value is not None else {} for value in readers]
            first = handoff(
                tmp, "grok", "openrouter", "x-ai/grok-4.20", "consensus_primary", **updates[0]
            )
            second = handoff(
                tmp,
                "gemini",
                "openrouter",
                "google/gemini-2.5-flash",
                "consensus_secondary",
                **updates[1],
            )
            with pytest.raises(ValueError, match="read the source itself"):
                consensus.load_records([first, second])


def test_the_same_vendor_twice_is_still_refused_however_it_is_reached():
    """The rule that a router does not create independence is unchanged."""
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        direct = handoff(
            tmp, "direct", "openai", "gpt-5.6", "consensus_primary", source_read_by="model"
        )
        routed = handoff(
            tmp,
            "routed",
            "openrouter",
            "openai/gpt-5.6",
            "consensus_secondary",
            source_read_by="model",
        )
        with pytest.raises(ValueError, match="a router does not create independence"):
            consensus.load_records([direct, routed])
