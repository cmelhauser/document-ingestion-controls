import importlib
import json
import sys

import pytest

lane = importlib.import_module("client_review.iterative")


raw_run = lane.run
raw_finalize_existing = lane.finalize_existing


def configured_run(*args, **kwargs):
    """Exercise the explicit-role API without repeating fixture routing."""
    kwargs.setdefault("primary_provider", "google")
    kwargs.setdefault("buddy_provider", "openai")
    return raw_run(*args, **kwargs)


def configured_finalize_existing(*args, **kwargs):
    kwargs.setdefault("primary_provider", "google")
    kwargs.setdefault("buddy_provider", "openai")
    return raw_finalize_existing(*args, **kwargs)


lane.run = configured_run
lane.finalize_existing = configured_finalize_existing


@pytest.fixture(autouse=True)
def isolated_throttle(monkeypatch, tmp_path):
    """Keep fake provider tests independent from the live run's shared limiter."""
    monkeypatch.setenv("LLM_THROTTLE_DIR", str(tmp_path / "throttle"))
    monkeypatch.setenv("GOOGLE_VERTEX_AI_RATE_LIMIT_REQUESTS_PER_MINUTE", "1000000")
    monkeypatch.setenv("GOOGLE_VERTEX_AI_RATE_LIMIT_TOKENS_PER_MINUTE", "100000000")
    monkeypatch.setenv("OPENAI_RATE_LIMIT_REQUESTS_PER_MINUTE", "1000000")
    monkeypatch.setenv("OPENAI_RATE_LIMIT_TOKENS_PER_MINUTE", "100000000")


class Response:
    def __init__(self, value):
        self.output_text = json.dumps(value)


class Google:
    calls = []

    class responses:
        @staticmethod
        def create(**kwargs):
            Google.calls.append(kwargs)
            return Response(
                {
                    "candidates": [
                        {
                            "document_id": "d1",
                            "candidate_type": "ack_reference",
                            "candidate_value": "A1",
                            "evidence_quote": "A1",
                            "rationale": "printed",
                        }
                    ],
                    "next_action": "stabilized",
                }
            )


class OpenAI:
    class responses:
        @staticmethod
        def create(**kwargs):
            return Response(
                {
                    "decisions": [
                        {
                            "candidate_id": "d1|ack_reference|A1",
                            "status": "confirmed",
                            "rationale": "supported",
                        }
                    ]
                }
            )


def context(tmp_path):
    path = tmp_path / "context.json"
    path.write_text(
        json.dumps(
            {
                "artifact_type": "client_review_context_v1",
                "reasoning_only": True,
                "independent_consensus_input": False,
                "policy": {"client_comments_are_untrusted_context": True},
                "pilot_records": [{"document_id": "d1", "document_type": "invoice"}],
                "client_comments": [],
            }
        )
    )
    return path


def test_helpers_and_contracts(tmp_path):
    assert lane.SCHEMA["properties"]["candidates"]["maxItems"] == 18
    assert lane.BUDDY_SCHEMA["properties"]["decisions"]["maxItems"] == 18
    proposal = {
        "document_id": "d1",
        "candidate_type": "ack_reference",
        "candidate_value": "A1",
        "evidence_quote": "A1",
    }
    assert lane.key(proposal) == "d1|ack_reference|A1"
    assert lane.compact(proposal)["candidate_value"] == "A1"
    packet = lane.buddy_packet([{"document_id": "d1"}], [], [proposal], 1, 1, 10000)
    assert packet["iteration"] == 1
    with pytest.raises(ValueError, match="buddy packet"):
        lane.buddy_packet([{"document_id": "d1"}], [], [proposal], 1, 1, 1)
    accepted, rejected = lane.reduce_buddy(
        {
            "decisions": [
                {"candidate_id": lane.key(proposal), "status": "confirmed", "rationale": "ok"}
            ]
        },
        [proposal],
    )
    assert accepted and not rejected and accepted[0]["production_approval_permitted"] is False
    assert lane.reduce_buddy(
        {"decisions": [{"candidate_id": "bad", "status": "confirmed"}]}, [proposal]
    )[1]
    assert lane.reduce_buddy(
        {"decisions": [{"candidate_id": lane.key(proposal), "status": "conflict"}]}, [proposal]
    )[1]
    assert lane.reduce_buddy(
        {"decisions": [{"candidate_id": lane.key(proposal), "status": "unknown"}]}, [proposal]
    )[1]
    with pytest.raises(ValueError, match="decisions"):
        lane.reduce_buddy({}, [])
    # Rule 9, one level up: a comments-only context carries no pilot records, and
    # this lane reasoned over nothing while reporting success on it.
    empty = json.loads(context(tmp_path).read_text())
    empty["pilot_records"] = []
    no_records = tmp_path / "no-records.json"
    no_records.write_text(json.dumps(empty))
    with pytest.raises(ValueError, match="carries no pilot records"):
        lane.run(
            no_records,
            tmp_path / "empty-primary.json",
            tmp_path / "empty-buddy.json",
            tmp_path / "empty-exceptions.json",
            tmp_path / "empty-raw",
            Google(),
            OpenAI(),
            "g",
            "o",
        )
    with pytest.raises(ValueError, match="iterations"):
        lane.run(
            context(tmp_path),
            tmp_path / "p",
            tmp_path / "b",
            tmp_path / "e",
            tmp_path / "r",
            Google(),
            OpenAI(),
            "g",
            "o",
            iterations=6,
        )
    with pytest.raises(ValueError, match="convergence minimum"):
        lane.run(
            context(tmp_path),
            tmp_path / "minimum-primary.json",
            tmp_path / "minimum-buddy.json",
            tmp_path / "minimum-exceptions.json",
            tmp_path / "minimum-raw",
            Google(),
            OpenAI(),
            "g",
            "o",
            convergence_min_new_candidates=0,
        )
    with pytest.raises(ValueError, match="reasoning effort"):
        lane.run(
            context(tmp_path),
            tmp_path / "bad-primary.json",
            tmp_path / "bad-buddy.json",
            tmp_path / "bad-exceptions.json",
            tmp_path / "bad-raw",
            Google(),
            OpenAI(),
            "g",
            "o",
            reasoning_effort="invalid",
        )
    invalid_context = tmp_path / "invalid-context.json"
    invalid_context.write_text(json.dumps({"pilot_records": [], "client_comments": []}))
    with pytest.raises(ValueError, match="client_review_context_v1"):
        lane.run(
            invalid_context,
            tmp_path / "invalid-primary.json",
            tmp_path / "invalid-buddy.json",
            tmp_path / "invalid-exceptions.json",
            tmp_path / "invalid-raw",
            Google(),
            OpenAI(),
            "g",
            "o",
        )
    records = [{"document_id": "a", "text": "x"}, {"document_id": "b", "text": "y"}]
    assert [len(batch) for batch in lane.adaptive_batches(records, [], 10, 100000, 2)] == [2]
    assert [len(batch) for batch in lane.adaptive_batches(records, [], 1, 100000, 2)] == [1, 1]
    assert [len(batch) for batch in lane.adaptive_batches(records, [], 1, 100000, 1)] == [1]
    assert [len(batch) for batch in lane.adaptive_batches(records, [], 1, 100000, 0)] == [1, 1]
    assert list(lane.adaptive_batches([{"document_id": "a", "text": "x"}], [], 1, 1, 1)) == [
        [{"document_id": "a", "text": "x"}]
    ]
    assert len(list(lane.adaptive_batches(records, [], 1, 1, 2))) == 2
    assert list(lane.adaptive_batches([], [], 1, 100000, 1)) == []
    with pytest.raises(ValueError, match="positive"):
        list(lane.adaptive_batches(records, [], 0, 100000, 1))
    with pytest.raises(ValueError, match="non-negative"):
        list(lane.adaptive_batches(records, [], 1, 100000, -1))
    material = lane.new_material_candidates(
        [
            {**proposal, "buddy_status": "confirmed"},
            {**proposal, "buddy_status": "close_needs_review"},
        ],
        set(),
    )
    assert [lane.key(candidate) for candidate in material] == [lane.key(proposal)]
    assert (
        lane.new_material_candidates(
            [{**proposal, "buddy_status": "confirmed"}], {lane.key(proposal)}
        )
        == []
    )


def test_run_iterates_and_no_clobber(tmp_path):
    result = lane.run(
        context(tmp_path),
        tmp_path / "primary.json",
        tmp_path / "buddy.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        Google(),
        OpenAI(),
        "gemini",
        "gpt",
        iterations=2,
        batch_size=1,
        max_batches=1,
        reasoning_effort="high",
    )
    assert result == {
        "primary": 2,
        "buddy": 2,
        "exceptions": 0,
        "requested_iterations": 2,
        "completed_iterations": 2,
        "termination_reason": "max_iterations_reached",
    }
    assert (tmp_path / "raw/iteration-01-primary-001.json").exists()
    assert Google.calls[-1]["reasoning"] == {"effort": "high"}
    assert json.loads((tmp_path / "primary.json").read_text())["reasoning_effort"] == "high"
    assert json.loads((tmp_path / "primary.json").read_text())["provider"] == "google"
    with pytest.raises(ValueError, match="Output already exists"):
        lane.run(
            context(tmp_path),
            tmp_path / "primary.json",
            tmp_path / "other.json",
            tmp_path / "other-e.json",
            tmp_path / "other-r",
            Google(),
            OpenAI(),
            "g",
            "o",
        )


def test_explicit_roles_are_required_and_independent(tmp_path):
    with pytest.raises(ValueError, match="supported providers"):
        lane.validate_reviewer_roles("unknown", "openai", "primary", "buddy")
    with pytest.raises(ValueError, match="models must be configured"):
        lane.validate_reviewer_roles("google", "openai", "", "buddy")
    with pytest.raises(ValueError, match="genuinely independent"):
        raw_run(
            context(tmp_path),
            tmp_path / "primary.json",
            tmp_path / "buddy.json",
            tmp_path / "exceptions.json",
            tmp_path / "raw",
            Google(),
            OpenAI(),
            "primary-model",
            "buddy-model",
            primary_provider="openai",
            buddy_provider="openai",
        )
    result = raw_run(
        context(tmp_path),
        tmp_path / "reversed-primary.json",
        tmp_path / "reversed-buddy.json",
        tmp_path / "reversed-exceptions.json",
        tmp_path / "reversed-raw",
        Google(),
        OpenAI(),
        "primary-model",
        "buddy-model",
        primary_provider="openai",
        buddy_provider="google",
        batch_size=1,
        max_batches=1,
    )
    assert result["primary"] == 1
    primary = json.loads((tmp_path / "reversed-primary.json").read_text())
    buddy = json.loads((tmp_path / "reversed-buddy.json").read_text())
    assert (primary["provider"], buddy["provider"]) == ("openai", "google")


def test_retained_response_helpers_reject_malformed_artifacts(tmp_path):
    assert lane.raw_output_value(
        {"response": {"text": "not json", "nested": [{"output_text": '{"candidates": []}'}]}},
        "candidates",
    ) == {"candidates": []}
    with pytest.raises(ValueError, match="valid candidates"):
        lane.raw_output_value({"response": {"text": "[]"}}, "candidates")

    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "iteration-invalid-name.json").write_text("{}")
    (raw / "iteration-01-primary-001.json").write_text("{")
    (raw / "iteration-01-primary-001-retry-01.json").write_text('{"response": {}}')
    packets = lane.retained_packets(raw)
    assert packets[(1, "primary", 1)]["payload"] == {"response": {}}
    (raw / "iteration-02-buddy-002.json").write_text("{")
    assert "error" in lane.retained_packets(raw)[(2, "buddy", 2)]


def test_finalize_existing_records_missing_buddy_and_main_finalize(monkeypatch, tmp_path, capsys):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "iteration-01-primary-001.json").write_text(
        json.dumps(
            {
                "request": {"source_records": [{"document_id": "d1"}]},
                "response": {"output_text": '{"candidates": []}'},
            }
        )
    )
    with pytest.raises(ValueError, match="iterations"):
        raw_finalize_existing(
            context(tmp_path),
            tmp_path / "p0",
            tmp_path / "b0",
            tmp_path / "e0",
            raw,
            "p",
            "b",
            primary_provider="google",
            buddy_provider="openai",
            requested_iterations=0,
        )
    with pytest.raises(ValueError, match="convergence minimum"):
        raw_finalize_existing(
            context(tmp_path),
            tmp_path / "p-minimum",
            tmp_path / "b-minimum",
            tmp_path / "e-minimum",
            raw,
            "p",
            "b",
            primary_provider="google",
            buddy_provider="openai",
            requested_iterations=1,
            convergence_min_new_candidates=0,
        )
    (raw / "iteration-01-primary-002.json").write_text("{")
    result = raw_finalize_existing(
        context(tmp_path),
        tmp_path / "p",
        tmp_path / "b",
        tmp_path / "e",
        raw,
        "p",
        "b",
        primary_provider="google",
        buddy_provider="openai",
        requested_iterations=1,
    )
    assert result["exceptions"] == 2

    called = {}
    monkeypatch.setenv("CLIENT_REVIEW_CONTEXT_LLM_PROVIDER", "google")
    monkeypatch.setenv("CLIENT_REVIEW_CONTEXT_LLM_BUDDY_PROVIDER", "openai")
    monkeypatch.setenv("CLIENT_REVIEW_CONTEXT_LLM_MODEL", "test-primary")
    monkeypatch.setenv("CLIENT_REVIEW_CONTEXT_LLM_BUDDY_MODEL", "test-buddy")
    monkeypatch.setattr(
        lane, "finalize_existing", lambda *args, **kwargs: called.update(kwargs) or {"ok": 1}
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_iterative.py",
            str(context(tmp_path)),
            "--finalize-existing",
            "--primary-out",
            str(tmp_path / "main-p"),
            "--buddy-out",
            str(tmp_path / "main-b"),
            "--exceptions",
            str(tmp_path / "main-e"),
            "--raw-dir",
            str(raw),
        ],
    )
    lane.main()
    assert called["primary_provider"]
    assert '"ok": 1' in capsys.readouterr().out


def test_run_stops_after_a_stable_iteration(tmp_path):
    result = lane.run(
        context(tmp_path),
        tmp_path / "primary.json",
        tmp_path / "buddy.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        Google(),
        OpenAI(),
        "gemini",
        "gpt",
        iterations=4,
        batch_size=1,
        max_batches=1,
        convergence_min_new_candidates=1,
    )
    assert result["requested_iterations"] == 4
    assert result["completed_iterations"] == 2
    assert result["termination_reason"] == "converged_no_new_material_relationships"
    primary = json.loads((tmp_path / "primary.json").read_text())
    assert [round_["new_material_candidate_count"] for round_ in primary["iterations"]] == [1, 0]


def test_finalize_existing_rebuilds_completed_passes_without_provider_calls(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    request = {"source_records": [{"document_id": "d1"}]}
    proposal = {
        "document_id": "d1",
        "candidate_type": "ack_reference",
        "candidate_value": "A1",
        "evidence_quote": "A1",
        "rationale": "printed",
    }
    (raw / "iteration-01-primary-001.json").write_text(
        json.dumps(
            {
                "request": request,
                "response": {"output_text": json.dumps({"candidates": [proposal]})},
            }
        )
    )
    (raw / "iteration-01-buddy-001.json").write_text(
        json.dumps(
            {
                "request": request,
                "response": {
                    "output_text": json.dumps(
                        {
                            "decisions": [
                                {
                                    "candidate_id": "d1|ack_reference|A1",
                                    "status": "confirmed",
                                    "rationale": "supported",
                                }
                            ]
                        }
                    )
                },
            }
        )
    )
    result = lane.finalize_existing(
        context(tmp_path),
        tmp_path / "primary.json",
        tmp_path / "buddy.json",
        tmp_path / "exceptions.json",
        raw,
        "gemini",
        "gpt",
        requested_iterations=2,
    )
    assert result == {
        "primary": 1,
        "buddy": 1,
        "exceptions": 0,
        "requested_iterations": 2,
        "completed_iterations": 1,
        "termination_reason": "operator_stopped_after_completed_iterations",
    }
    assert (
        json.loads((tmp_path / "buddy.json").read_text())["candidates"][0]["buddy_status"]
        == "confirmed"
    )


def test_failure_packet_and_main(monkeypatch, tmp_path, capsys):
    class Bad:
        class responses:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("down")

    class MalformedResponse:
        output_text = "{"

        def model_dump(self, **kwargs):
            del kwargs
            return {"output_text": self.output_text}

    class Malformed:
        class responses:
            @staticmethod
            def create(**kwargs):
                return MalformedResponse()

    class Recovering:
        calls = 0

        class responses:
            @staticmethod
            def create(**kwargs):
                Recovering.calls += 1
                return MalformedResponse() if Recovering.calls == 1 else Google.responses.create()

    monkeypatch.setenv("CLIENT_REVIEW_CONTEXT_LLM_PROVIDER", "google")
    monkeypatch.setenv("CLIENT_REVIEW_CONTEXT_LLM_BUDDY_PROVIDER", "openai")
    monkeypatch.setenv("CLIENT_REVIEW_CONTEXT_LLM_MODEL", "test-primary")
    monkeypatch.setenv("CLIENT_REVIEW_CONTEXT_LLM_BUDDY_MODEL", "test-buddy")

    result = lane.run(
        context(tmp_path),
        tmp_path / "p.json",
        tmp_path / "b.json",
        tmp_path / "e.json",
        tmp_path / "r",
        Bad(),
        OpenAI(),
        "g",
        "o",
        batch_size=1,
        max_batches=1,
    )
    assert result["exceptions"] == 1
    failed = json.loads((tmp_path / "e.json").read_text())["exceptions"]
    assert failed[0]["source_document_ids"] == ["d1"]
    raw_exception = json.loads((tmp_path / "r/iteration-01-batch-001-exception.json").read_text())
    assert raw_exception["source_document_ids"] == ["d1"]
    malformed = lane.run(
        context(tmp_path),
        tmp_path / "malformed-primary.json",
        tmp_path / "malformed-buddy.json",
        tmp_path / "malformed-exceptions.json",
        tmp_path / "malformed-raw",
        Malformed(),
        OpenAI(),
        "g",
        "o",
        batch_size=1,
        max_batches=1,
    )
    assert malformed["exceptions"] == 1
    raw = json.loads((tmp_path / "malformed-raw/iteration-01-primary-001.json").read_text())
    assert raw["response"]["output_text"] == "{"
    recovered = lane.run(
        context(tmp_path),
        tmp_path / "recovered-primary.json",
        tmp_path / "recovered-buddy.json",
        tmp_path / "recovered-exceptions.json",
        tmp_path / "recovered-raw",
        Recovering(),
        OpenAI(),
        "g",
        "o",
        batch_size=1,
        max_batches=1,
        retries=1,
    )
    assert recovered["primary"] == recovered["buddy"] == 1
    assert (tmp_path / "recovered-raw/iteration-01-primary-001-retry-01.json").exists()
    tiny = lane.run(
        context(tmp_path),
        tmp_path / "tp.json",
        tmp_path / "tb.json",
        tmp_path / "te.json",
        tmp_path / "tr",
        Google(),
        OpenAI(),
        "g",
        "o",
        batch_size=1,
        max_batches=1,
        max_context_bytes=1,
    )
    assert tiny["exceptions"] == 1
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_iterative.py",
            str(context(tmp_path)),
            "--primary-out",
            str(tmp_path / "mp"),
            "--buddy-out",
            str(tmp_path / "mb"),
            "--exceptions",
            str(tmp_path / "me"),
            "--raw-dir",
            str(tmp_path / "mr"),
        ],
    )
    monkeypatch.setattr(lane, "build_reviewer_client", lambda *args: Google())
    lane.main()
    assert "primary" in capsys.readouterr().out


def test_a_router_serving_the_primary_vendor_is_not_an_independent_buddy():
    """A buddy that is the primary's own weights cannot disagree with it.

    `consensus.py` already resolves independence to the model vendor. Comparing
    provider names alone would let an OpenAI primary be "confirmed" by an
    OpenRouter lane routing `openai/...`.
    """
    with pytest.raises(ValueError, match="a router does not create independence"):
        lane.validate_reviewer_roles("openai", "openrouter", "gpt-5.6-luna", "openai/gpt-oss-120b")


def test_a_router_serving_a_different_vendor_is_an_independent_buddy():
    assert (
        lane.validate_reviewer_roles(
            "openai", "openrouter", "gpt-5.6-luna", "nvidia/nemotron-3-ultra-550b-a55b:free"
        )
        is None
    )


def test_a_routed_buddy_slug_without_a_vendor_prefix_is_refused():
    with pytest.raises(ValueError, match="vendor-prefixed model slug is required"):
        lane.validate_reviewer_roles("openai", "openrouter", "gpt-5.6-luna", "gpt-oss-120b")


def test_a_routed_primary_slug_without_a_vendor_prefix_is_refused():
    with pytest.raises(ValueError, match="vendor-prefixed model slug is required"):
        lane.validate_reviewer_roles("openrouter", "openai", "gpt-oss-120b", "gpt-5.6-luna")


def test_two_distinct_direct_providers_remain_independent():
    assert lane.validate_reviewer_roles("google", "openai", "gemini-2.5-pro", "gpt-5.6-sol") is None


def multi_record_context(tmp_path):
    """A context with several records, so batch_size=1 yields several batches."""
    path = tmp_path / "multi-context.json"
    path.write_text(
        json.dumps(
            {
                "artifact_type": "client_review_context_v1",
                "reasoning_only": True,
                "independent_consensus_input": False,
                "policy": {"client_comments_are_untrusted_context": True},
                "pilot_records": [
                    {"document_id": f"d{n}", "document_type": "invoice"} for n in range(1, 5)
                ],
                "client_comments": [],
            }
        )
    )
    return path


def test_worker_count_changes_speed_but_never_the_retained_artifact(tmp_path):
    """Batches inside a pass may finish in any order; the artifact must not show it.

    Passes stay sequential because each reads the prior pass, so only the batches
    within a pass overlap. Both stubs answer every request identically, leaving
    only the lane's own assembly of a pass under test.
    """
    source = multi_record_context(tmp_path)

    def relate(tag, workers):
        primary_out = tmp_path / f"{tag}-primary.json"
        summary = lane.run(
            source,
            primary_out,
            tmp_path / f"{tag}-buddy.json",
            tmp_path / f"{tag}-exceptions.json",
            tmp_path / f"{tag}-raw",
            Google(),
            OpenAI(),
            "gemini",
            "gpt",
            iterations=1,
            batch_size=1,
            max_batches=4,
            workers=workers,
        )
        return summary, json.loads(primary_out.read_text())

    serial_summary, serial = relate("serial", 1)
    concurrent_summary, concurrent = relate("concurrent", 4)

    assert len(serial["iterations"][0]["batches"]) > 1, "fixture must produce >1 batch"
    assert concurrent_summary == serial_summary
    assert [item["batch"] for item in concurrent["iterations"][0]["batches"]] == [
        item["batch"] for item in serial["iterations"][0]["batches"]
    ]
    assert concurrent["candidates"] == serial["candidates"]
