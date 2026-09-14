"""Tests for the proposal-only client-review LLM and dependency reducer."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

reviewer = importlib.import_module("client_review.llm")
runtime = importlib.import_module("llm_runtime")


def write(path, value):
    path.write_text(json.dumps(value))
    return path


def item(reason="consensus_disagreement", document_id="doc-1", field="formatting_note"):
    return {
        "document_id": document_id,
        "page_id": "page-1",
        "field": field,
        "reason": reason,
    }


def queue():
    return {
        "summary": {"client_review_items": 2},
        "items": [item(), item("formatting_review", "doc-1")],
        "review_cards": [
            {"card_id": "document:doc-1", "document_id": "doc-1", "item_indexes": [0, 1]}
        ],
    }


def decision(card_id="document:doc-1", cover=True):
    return {
        "card_id": card_id,
        "overall_decision": "propose_reduction" if cover else "retain_review",
        "confidence": 0.98,
        "rationale": "The second item depends on the first item being reviewed.",
        "item_decisions": [
            {
                "item_id": "review-item-0",
                "decision": "propose_resolution",
                "confidence": 0.98,
                "rationale": "Primary review target.",
            },
            {
                "item_id": "review-item-1",
                "decision": "covered_by_other_item" if cover else "retain_review",
                "confidence": 0.98,
                "rationale": "Dependent formatting finding.",
            },
        ],
        "dependencies": [
            {
                "dependent_item_id": "review-item-1",
                "blocking_item_id": "review-item-0",
                "relationship": "same_document_context",
                "rationale": "Same source card.",
            }
        ]
        if cover
        else [],
    }


class Response:
    def __init__(self, value):
        self.output_text = json.dumps(value)

    def model_dump(self, mode="json"):
        return {"id": "review-1", "output": self.output_text}


class Responses:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Client:
    def __init__(self, outcomes):
        self.responses = Responses(outcomes)


def test_helpers_and_reducer_boundaries(tmp_path, monkeypatch):
    context = write(tmp_path / "context.json", {"summary": {"ok": True}})
    assert reviewer.load_object(context)["summary"]["ok"]
    with pytest.raises(ValueError, match="JSON object"):
        reviewer.load_object(write(tmp_path / "list.json", []))
    with pytest.raises(ValueError, match="items"):
        reviewer.load_queue(write(tmp_path / "bad-queue.json", {}))
    loaded = reviewer.load_queue(write(tmp_path / "queue.json", queue()))
    assert reviewer.item_id(3) == "review-item-3"
    assert len(reviewer.card_items(loaded, loaded["review_cards"][0])) == 2
    with pytest.raises(ValueError, match="invalid item"):
        reviewer.card_items(loaded, {"item_indexes": [9]})
    with pytest.raises(ValueError, match="item_indexes"):
        reviewer.card_items(loaded, {"item_indexes": "bad"})
    large_context = write(tmp_path / "large.json", {"payload": "x" * 20})
    assert reviewer.context_summary([large_context], limit=5)[0]["json"].endswith(
        "TRUNCATED_FOR_PACKET"
    )
    list_context = write(tmp_path / "list-context.json", [{"engine": "a"}])
    assert '"engine":"a"' in reviewer.context_summary([list_context])[0]["json"]
    assert reviewer.context_summary([context])[0]["artifact"] == str(context)
    packet = reviewer.packet(
        loaded["review_cards"][0],
        reviewer.card_items(loaded, loaded["review_cards"][0]),
        reviewer.context_summary([context]),
    )
    assert packet["pipeline_context"][0]["sha256"]
    assert reviewer.raw_name(1, loaded["review_cards"][0]).startswith("000001_document_doc-1")
    assert reviewer.protected(item("financial_total"))
    assert not reviewer.protected(item("formatting_review"))
    for reason in (
        "no_majority",
        "partial_disagreement",
        "openai_review_flag:ambiguous",
        "schema_mismatch",
        "missing_required_field",
    ):
        assert reviewer.protected(item(reason))
    assert reviewer.protected(item("formatting_review", field="seller_name"))
    assert reviewer.retry_after_seconds(object()) is None
    bad_retry_after = type(
        "BadRetryAfter",
        (),
        {"response": type("Response", (), {"headers": {"Retry-After": "bad"}})()},
    )()
    assert reviewer.retry_after_seconds(bad_retry_after) is None
    for values in (
        (0, 1, 1, 0, "medium"),
        (1, 0, 1, 0, "medium"),
        (1, 1, 0, 0, "medium"),
        (1, 1, 1, -1, "medium"),
        (1, 1, 1, 0, "bad"),
    ):
        with pytest.raises(ValueError):
            reviewer.validate_limits(*values)
    reviewer.validate_limits(1, 1, 1, 0, "medium")
    for values in ((-1, 1), (1, 0), (2, 1)):
        with pytest.raises(ValueError):
            reviewer.validate_retry_limits(*values)
    with pytest.raises(ValueError, match="PROVIDER"):
        reviewer.build_reviewer_client("other", "KEY", 1, 0)
    sentinel = object()
    monkeypatch.setattr("openai_adapter.build_client", lambda *args: sentinel)
    monkeypatch.setattr(reviewer, "build_client", lambda *args, **kwargs: sentinel)
    assert reviewer.build_reviewer_client("openai", "KEY", 1, 0) is sentinel
    assert reviewer.build_reviewer_client("google", "KEY", 1, 0) is sentinel
    router = importlib.import_module("openrouter_adapter")
    monkeypatch.setattr(router, "build_client", lambda *args: sentinel)
    assert reviewer.build_reviewer_client("openrouter", "KEY", 1, 0) is sentinel
    # Anthropic was accepted everywhere except here: the refusal below names it,
    # `resolve_reviewer_configuration` validates against it, and the adapter has
    # always exposed a matching build_client -- but the branch was missing, so a
    # lane configured with an anthropic buddy failed at construction. This test
    # enumerated the other three providers and mirrored the omission.
    anthropic = importlib.import_module("anthropic_adapter")
    monkeypatch.setattr(anthropic, "build_client", lambda *args: sentinel)
    assert reviewer.build_reviewer_client("anthropic", "KEY", 1, 0) is sentinel
    monkeypatch.setenv("LLM_CLIENT_REVIEW_PROVIDER", "openai")
    monkeypatch.setenv("CLIENT_REVIEW_LLM_MODEL", "current-model")
    assert reviewer.resolve_reviewer_configuration() == ("openai", "current-model")
    assert reviewer.resolve_reviewer_configuration("google", "advanced-gemini") == (
        "google",
        "advanced-gemini",
    )
    assert reviewer.resolve_reviewer_configuration("google", None, "legacy-model") == (
        "google",
        "legacy-model",
    )
    with pytest.raises(ValueError, match="final provider"):
        reviewer.resolve_reviewer_configuration("other", None)


def test_reviewer_inherits_named_reasoning_lane(monkeypatch):
    monkeypatch.delenv("LLM_CLIENT_REVIEW_PROVIDER", raising=False)
    monkeypatch.delenv("CLIENT_REVIEW_LLM_MODEL", raising=False)
    monkeypatch.setenv("LLM_REASONING_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_VERTEX_AI_REASONING_MODEL", "reasoning-gemini")
    assert reviewer.resolve_reviewer_configuration() == ("google", "reasoning-gemini")


def test_dependency_reducer_is_fail_closed():
    resolved = [
        {"item_id": "review-item-0", "item": item()},
        {"item_id": "review-item-1", "item": item("formatting_review")},
    ]
    reduced = reviewer.reduce_card(resolved, decision())
    assert len(reduced["all_items"]) == 2
    assert len(reduced["visible_items"]) == 1
    assert reduced["covered_items"][0]["covered_by"] == "review-item-0"
    invalid = decision()
    invalid["dependencies"] = [
        {
            "dependent_item_id": "unknown",
            "blocking_item_id": "review-item-0",
            "relationship": "same_document_context",
            "rationale": "bad",
        },
        {
            "dependent_item_id": "review-item-0",
            "blocking_item_id": "review-item-0",
            "relationship": "same_document_context",
            "rationale": "self",
        },
    ]
    assert reviewer.valid_dependencies(invalid, {"review-item-0": {}, "review-item-1": {}}) == []
    protected = [
        {"item_id": "review-item-0", "item": item("consensus_disagreement")},
        {"item_id": "review-item-1", "item": item("financial_total")},
    ]
    protected_decision = decision()
    protected_decision["item_decisions"][0]["decision"] = "propose_resolution"
    protected_decision["item_decisions"][1]["decision"] = "covered_by_other_item"
    assert reviewer.reduce_card(protected, protected_decision)["covered_items"] == []


def test_proposed_update_is_retained_and_optional_auto_accept_is_guarded():
    proposal = {
        "field": "formatting_note",
        "original_value": "Acne",
        "proposed_value": "Acme",
        "update_type": "correction",
        "evidence": "Printed header reads Acme",
        "rationale": "Independent evidence supports the spelling correction",
    }
    proposed = decision(cover=False)
    proposed["item_decisions"][0].update(
        decision="propose_resolution", confidence=0.995, proposed_update=proposal
    )
    reduced = reviewer.reduce_card(
        [{"item_id": "review-item-0", "item": item("formatting_review")}],
        proposed,
        auto_accept=True,
        auto_accept_threshold=0.99,
    )
    assert len(reduced["auto_accepted_updates"]) == 1
    assert reduced["all_items"][0]["llm_proposed_update"] == proposal
    assert reduced["all_items"][0]["llm_auto_accept_status"] == "auto_accepted"
    assert reduced["visible_items"] == []
    protected = proposed.copy()
    protected["item_decisions"] = [dict(proposed["item_decisions"][0])]
    protected["item_decisions"][0]["proposed_update"] = proposal
    blocked = reviewer.reduce_card(
        [{"item_id": "review-item-0", "item": item("financial_total")}],
        protected,
        auto_accept=True,
        auto_accept_threshold=0.99,
    )
    assert blocked["auto_accepted_updates"] == []
    assert len(blocked["visible_items"]) == 1
    assert reviewer.valid_proposed_update(None) is False
    assert reviewer.valid_proposed_update({"field": "x"}) is False
    assert reviewer.valid_proposed_update({**proposal, "update_type": "delete"}) is False
    assert reviewer.valid_proposed_update(proposal, item("formatting_review")) is True
    assert (
        reviewer.valid_proposed_update({**proposal, "field": "other"}, item("formatting_review"))
        is False
    )
    reviewer.validate_auto_accept_threshold(0.99)
    with pytest.raises(ValueError):
        reviewer.validate_auto_accept_threshold(0.98)


def test_page_request_and_run_retains_provider_failures(tmp_path):
    queue_path = write(tmp_path / "queue.json", queue())
    context = write(tmp_path / "context.json", {"schema": "v1"})
    probe_client = Client([Response(decision())])
    response = reviewer.page_request(
        probe_client, "model", "high", {"card": queue()["review_cards"][0]}
    )
    assert response.output_text
    assert probe_client.responses.calls[0]["reasoning"] == {"effort": "high"}
    out = tmp_path / "out.json"
    exceptions = tmp_path / "exceptions.json"
    raw = tmp_path / "raw"
    summary = reviewer.run_review(
        queue_path, [context], out, exceptions, raw, "model", Client([Response(decision())])
    )
    assert summary["reviewer_cards"] == 1
    assert summary["successful_cards"] == 1
    assert summary["covered_items"] == 1
    assert json.loads(out.read_text())["cards"][0]["reduction"]["all_items"]
    assert json.loads(exceptions.read_text())["summary"]["count"] == 0
    resumed_out = tmp_path / "resumed.json"
    resumed_summary = reviewer.run_review(
        queue_path,
        [context],
        resumed_out,
        tmp_path / "resumed-exc.json",
        tmp_path / "resumed-raw",
        "model",
        Client([]),
        resume_from=out,
    )
    assert resumed_summary["successful_cards"] == 1
    assert resumed_summary["retry_telemetry"]["resumed_from"] == str(out)
    with pytest.raises(ValueError, match="resume contract"):
        reviewer.run_review(
            queue_path,
            [context],
            tmp_path / "changed-instructions-resume.json",
            tmp_path / "changed-instructions-resume-exc.json",
            tmp_path / "changed-instructions-resume-raw",
            "model",
            Client([]),
            resume_from=out,
            final_run_include_protected_arithmetic_reassembly=True,
        )
    prior = json.loads(out.read_text())
    loaded_queue = reviewer.load_queue(queue_path)
    loaded_context = reviewer.context_summary([context])
    invalid_resumes = []
    missing_cards = json.loads(json.dumps(prior))
    missing_cards["cards"] = None
    invalid_resumes.append(missing_cards)
    missing_id = json.loads(json.dumps(prior))
    missing_id["cards"][0].pop("card_id")
    invalid_resumes.append(missing_id)
    duplicate = json.loads(json.dumps(prior))
    duplicate["cards"].append(dict(duplicate["cards"][0]))
    invalid_resumes.append(duplicate)
    stale_request = json.loads(json.dumps(prior))
    stale_request["cards"][0]["review_request_sha256"] = "0" * 64
    invalid_resumes.append(stale_request)
    for invalid in invalid_resumes:
        with pytest.raises(ValueError):
            reviewer.validate_resumed_cards(
                invalid, prior["resume_contract"], loaded_queue, loaded_context
            )
    changed_queue = queue()
    changed_queue["items"][0]["reason"] = "changed evidence"
    changed_queue_path = write(tmp_path / "changed-queue.json", changed_queue)
    with pytest.raises(ValueError, match="resume contract"):
        reviewer.run_review(
            changed_queue_path,
            [context],
            tmp_path / "changed-resume.json",
            tmp_path / "changed-resume-exc.json",
            tmp_path / "changed-resume-raw",
            "model",
            Client([]),
            resume_from=out,
        )
    changed_context = write(tmp_path / "changed-context.json", {"schema": "v2"})
    with pytest.raises(ValueError, match="resume contract"):
        reviewer.run_review(
            queue_path,
            [changed_context],
            tmp_path / "context-resume.json",
            tmp_path / "context-resume-exc.json",
            tmp_path / "context-resume-raw",
            "model",
            Client([]),
            resume_from=out,
        )
    Path(prior["cards"][0]["raw_response"]).write_text("tampered")
    with pytest.raises(ValueError, match="raw response"):
        reviewer.run_review(
            queue_path,
            [context],
            tmp_path / "raw-resume.json",
            tmp_path / "raw-resume-exc.json",
            tmp_path / "raw-resume-dir",
            "model",
            Client([]),
            resume_from=out,
        )
    with pytest.raises(ValueError, match="max cards"):
        reviewer.run_review(
            queue_path,
            [],
            tmp_path / "other.json",
            tmp_path / "other-exc.json",
            tmp_path / "other-raw",
            "m",
            Client([]),
            max_cards=0,
        )
    two_cards = queue()
    two_cards["review_cards"].append(
        {"card_id": "document:doc-2", "document_id": "doc-2", "item_indexes": [0]}
    )
    two_queue = write(tmp_path / "two-queue.json", two_cards)
    with pytest.raises(ValueError, match="card count exceeds"):
        reviewer.run_review(
            two_queue,
            [],
            tmp_path / "limit.json",
            tmp_path / "limit-exc.json",
            tmp_path / "limit-raw",
            "m",
            Client([]),
            max_cards=1,
        )
    mismatch = Client([Response(decision("wrong-card"))])
    mismatch_summary = reviewer.run_review(
        queue_path,
        [],
        tmp_path / "mismatch.json",
        tmp_path / "mismatch-exc.json",
        tmp_path / "mismatch-raw",
        "m",
        mismatch,
    )
    assert mismatch_summary["provider_exceptions"] == 1


def test_retry_page_request_handles_rate_limits_and_records_audit(monkeypatch):
    class RateLimitError(Exception):
        status_code = 429

        def __init__(self):
            self.response = type("Response", (), {"headers": {"retry-after": "2"}})()

    client = Client([RateLimitError(), Response(decision())])
    sleeps = []
    response, retry_log = reviewer.retry_page_request(
        client,
        "model",
        "medium",
        {"card": "x"},
        1,
        1,
        5,
        sleep=sleeps.append,
        uniform=lambda low, high: high,
    )
    assert response.output_text
    assert sleeps == [2]
    assert retry_log[0]["error_type"] == "RateLimitError"
    assert retry_log[0]["retry_after_seconds"] == 2.0
    with pytest.raises(RuntimeError):
        reviewer.retry_page_request(
            Client([RuntimeError("permanent")]),
            "model",
            "medium",
            {"card": "x"},
            2,
            1,
            5,
            sleep=lambda _delay: None,
            uniform=lambda low, high: high,
        )


def test_retry_page_request_respects_open_provider_quota_circuit(monkeypatch):
    runtime.reset_provider_quota_circuits()
    runtime.open_provider_quota_circuit("client_review")
    monkeypatch.setenv("LLM_FAIL_FAST_ON_PROVIDER_QUOTA", "true")
    with pytest.raises(runtime.ProviderQuotaExhausted, match="circuit is open"):
        reviewer.retry_page_request(
            object(),
            "model",
            "medium",
            {"card": "x"},
            2,
            1,
            5,
            sleep=lambda _delay: None,
        )
    runtime.reset_provider_quota_circuits()


def test_run_provider_exception_and_disabled_cli(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("CLIENT_REVIEW_LLM_ENABLED", raising=False)
    monkeypatch.setattr(reviewer, "load_project_env", lambda: None)
    queue_path = write(tmp_path / "queue.json", queue())
    client = Client([RuntimeError("provider")])
    summary = reviewer.run_review(
        queue_path, [], tmp_path / "out.json", tmp_path / "exc.json", tmp_path / "raw", "m", client
    )
    assert summary["provider_exceptions"] == 1
    assert (
        json.loads((tmp_path / "exc.json").read_text())["exceptions"][0]["disposition"]
        == "client_review_required"
    )
    two_card_queue = queue()
    two_card_queue["review_cards"].append(
        {"card_id": "document:doc-2", "document_id": "doc-2", "item_indexes": [0]}
    )
    two_card_path = write(tmp_path / "two-card-queue.json", two_card_queue)

    class ExhaustedRateLimit(Exception):
        status_code = 429

        def __str__(self):
            return "free-models-per-day-high-balance quota exhausted"

    circuit_summary = reviewer.run_review(
        two_card_path,
        [],
        tmp_path / "circuit.json",
        tmp_path / "circuit-exc.json",
        tmp_path / "circuit-raw",
        "m",
        Client([ExhaustedRateLimit()]),
        max_retries=0,
    )
    assert circuit_summary["retry_telemetry"]["circuit_breaker_skipped_cards"] == 1
    monkeypatch.setattr(
        sys,
        "argv",
        [
            reviewer.__file__,
            str(queue_path),
            "--out",
            str(tmp_path / "disabled.json"),
            "--exceptions",
            str(tmp_path / "disabled-exc.json"),
            "--raw-dir",
            str(tmp_path / "disabled-raw"),
        ],
    )
    reviewer.main()
    assert "disabled" in capsys.readouterr().out
    assert json.loads((tmp_path / "disabled.json").read_text())["summary"]["enabled"] is False
    monkeypatch.setenv("CLIENT_REVIEW_LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_CLIENT_REVIEW_PROVIDER", "google")
    monkeypatch.setattr(
        reviewer,
        "build_client",
        lambda *args, **kwargs: Client([Response(decision())]),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            reviewer.__file__,
            str(queue_path),
            "--out",
            str(tmp_path / "enabled.json"),
            "--exceptions",
            str(tmp_path / "enabled-exc.json"),
            "--raw-dir",
            str(tmp_path / "enabled-raw"),
        ],
    )
    reviewer.main()
    assert json.loads((tmp_path / "enabled.json").read_text())["summary"]["successful_cards"] == 1


def test_final_provider_and_model_override_inherit_other_settings(monkeypatch, tmp_path):
    queue_path = write(tmp_path / "queue.json", queue())
    client = Client([Response(decision())])
    monkeypatch.setenv("CLIENT_REVIEW_LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_CLIENT_REVIEW_PROVIDER", "openai")
    monkeypatch.setenv("CLIENT_REVIEW_LLM_MODEL", "ordinary-model")
    monkeypatch.setattr(reviewer, "build_reviewer_client", lambda *args: client)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            reviewer.__file__,
            str(queue_path),
            "--final-provider",
            "google",
            "--final-model",
            "advanced-gemini",
            "--out",
            str(tmp_path / "final.json"),
            "--exceptions",
            str(tmp_path / "final-exc.json"),
            "--raw-dir",
            str(tmp_path / "final-raw"),
        ],
    )
    reviewer.main()
    output = json.loads((tmp_path / "final.json").read_text())
    assert output["summary"]["reviewer_provider"] == "google"
    assert output["summary"]["reviewer_model"] == "advanced-gemini"
    assert output["summary"]["retry_policy"]["max_retries"] == 4
    monkeypatch.setattr(
        reviewer,
        "run_review",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad reviewer input")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            reviewer.__file__,
            str(queue_path),
            "--out",
            str(tmp_path / "error.json"),
            "--exceptions",
            str(tmp_path / "error-exc.json"),
            "--raw-dir",
            str(tmp_path / "error-raw"),
        ],
    )
    with pytest.raises(SystemExit, match="Client-review LLM failed"):
        reviewer.main()


def independent_queue(count=6):
    """A queue of independent one-item cards, so review order is free to vary."""
    return {
        "summary": {"client_review_items": count},
        "items": [item(document_id=f"doc-{n}") for n in range(count)],
        "review_cards": [
            {"card_id": f"document:doc-{n}", "document_id": f"doc-{n}", "item_indexes": [n]}
            for n in range(count)
        ],
    }


def single_card_decision(card_id):
    return {
        "card_id": card_id,
        "overall_decision": "retain_review",
        "confidence": 0.98,
        "rationale": "Retained for the client to answer.",
        "item_decisions": [
            {
                "item_id": "review-item-0",
                "decision": "retain_review",
                "confidence": 0.98,
                "rationale": "Primary review target.",
            }
        ],
        "dependencies": [],
    }


class CardKeyedClient:
    """Answer every request from its own packet.

    A stub that pops replies off a list answers by call order, which is exactly
    what concurrency is allowed to change. Keying the reply to the card in the
    request means a crossed response fails the card_id check rather than passing
    silently.
    """

    def __init__(self):
        self.responses = self
        self.calls = []

    def create(self, **kwargs):
        sent = json.loads(kwargs["input"][0]["content"][0]["text"])
        card_id = sent["card"]["card_id"]
        self.calls.append(card_id)
        return Response(single_card_decision(card_id))


def test_worker_count_changes_speed_but_never_the_retained_artifact(tmp_path):
    """Concurrency is a scheduling choice, so the artifact must not record it."""
    queue_path = write(tmp_path / "queue.json", independent_queue())
    context = write(tmp_path / "context.json", {"schema": "v1"})

    def review(tag, workers):
        out = tmp_path / f"{tag}.json"
        reviewer.run_review(
            queue_path,
            [context],
            out,
            tmp_path / f"{tag}-exc.json",
            tmp_path / f"{tag}-raw",
            "model",
            CardKeyedClient(),
            workers=workers,
        )
        return json.loads(out.read_text())

    serial = review("serial", 1)
    concurrent = review("concurrent", 4)

    assert [card["card_id"] for card in serial["cards"]] == [f"document:doc-{n}" for n in range(6)]
    # Order, decisions, and reductions are identical; only the run's own paths,
    # timestamps, and declared worker count may differ.
    assert [card["card_id"] for card in concurrent["cards"]] == [
        card["card_id"] for card in serial["cards"]
    ]
    assert [card["decision"] for card in concurrent["cards"]] == [
        card["decision"] for card in serial["cards"]
    ]
    assert [card["reduction"] for card in concurrent["cards"]] == [
        card["reduction"] for card in serial["cards"]
    ]
    assert concurrent["summary"]["successful_cards"] == 6
    assert concurrent["summary"]["workers"] == 4
    assert serial["summary"]["workers"] == 1


def test_workers_must_be_a_positive_integer(tmp_path):
    queue_path = write(tmp_path / "queue.json", independent_queue(1))
    context = write(tmp_path / "context.json", {"schema": "v1"})
    with pytest.raises(ValueError, match="workers must be a positive integer"):
        reviewer.run_review(
            queue_path,
            [context],
            tmp_path / "out.json",
            tmp_path / "exc.json",
            tmp_path / "raw",
            "model",
            CardKeyedClient(),
            workers=0,
        )


def test_a_failed_card_reports_what_was_actually_attempted(tmp_path, monkeypatch):
    """A failed card must carry its retry history, not an empty log.

    The retry record used to be returned only on success, so the one case where
    knowing what was tried actually matters -- a card that failed -- always
    reported zero attempts.
    """
    monkeypatch.setattr(reviewer.time, "sleep", lambda _seconds: None)
    queue_path = write(tmp_path / "queue.json", independent_queue(1))
    context = write(tmp_path / "context.json", {"schema": "v1"})

    class AlwaysTimesOut:
        def __init__(self):
            self.responses = self
            self.calls = 0

        def create(self, **kwargs):
            del kwargs
            self.calls += 1
            raise TimeoutError("provider timed out")

    client = AlwaysTimesOut()
    exceptions_path = tmp_path / "exc.json"
    reviewer.run_review(
        queue_path,
        [context],
        tmp_path / "out.json",
        exceptions_path,
        tmp_path / "raw",
        "model",
        client,
        max_retries=3,
    )
    failure = json.loads(exceptions_path.read_text())["exceptions"][0]
    assert failure["error_type"] == "TimeoutError"
    # Four requests were made; the three that were followed by another attempt
    # are the ones a retry log can describe.
    assert client.calls == 4
    assert failure["retry_attempts"] == 3
    assert [event["attempt"] for event in failure["retry_log"]] == [1, 2, 3]


def test_transport_limits_come_from_the_documented_environment(monkeypatch):
    """The client that enforces the timeout must read the settings we document.

    `run_review` validated these values while the SDK client was built from
    hardcoded defaults, so raising either setting changed nothing that governed
    a real request.
    """
    seen = {}

    def record(provider, credential_env, timeout_seconds, max_retries):
        seen.update(timeout=timeout_seconds, retries=max_retries)
        raise ValueError("stop before any provider work")

    monkeypatch.setattr(reviewer, "build_reviewer_client", record)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CLIENT_REVIEW_LLM_TIMEOUT_SECONDS", "1800")
    monkeypatch.setenv("CLIENT_REVIEW_LLM_MAX_RETRIES", "2")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_llm.py",
            "queue.json",
            "--enable",
            "--out",
            "out.json",
            "--exceptions",
            "exc.json",
            "--raw-dir",
            "raw",
        ],
    )
    with pytest.raises(SystemExit):
        reviewer.main()
    assert seen == {"timeout": 1800.0, "retries": 2}
