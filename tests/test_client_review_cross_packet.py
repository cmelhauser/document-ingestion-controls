import importlib
import json
import sys

import pytest

lane = importlib.import_module("client_review.cross_packet")
graph_model = importlib.import_module("evidence_graph")


raw_run = lane.run


def configured_run(*args, **kwargs):
    """Exercise the explicit-role API without repeating fixture routing."""
    kwargs.setdefault("primary_provider", "google")
    kwargs.setdefault("buddy_provider", "openai")
    return raw_run(*args, **kwargs)


lane.run = configured_run


class Response:
    def __init__(self, value):
        self.output_text = json.dumps(value)

    def model_dump(self, **kwargs):
        del kwargs
        return {"output_text": self.output_text}


class Google:
    class responses:
        @staticmethod
        def create(**kwargs):
            request = json.loads(kwargs["input"][0]["content"][0]["text"])
            if "existing_candidates" in request:
                return Response(
                    {
                        "decisions": [
                            {
                                "candidate_id": item["candidate_id"],
                                "status": "confirmed",
                                "rationale": "related packet supports it",
                            }
                            for item in request["existing_candidates"]
                        ]
                    }
                )
            records = request["source_records"]
            return Response(
                {
                    "candidates": [
                        {
                            "document_id": records[0]["document_id"],
                            "candidate_type": "cross_record_link",
                            "candidate_value": "ACK-9",
                            "evidence_quote": "ACK-9",
                            "rationale": "shared printed identifier",
                        }
                    ],
                    "next_action": "stabilized",
                }
            )


class OpenAI:
    class responses:
        @staticmethod
        def create(**kwargs):
            request = json.loads(kwargs["input"][0]["content"][0]["text"])
            if "existing_candidates" in request:
                return Response(
                    {
                        "decisions": [
                            {
                                "candidate_id": item["candidate_id"],
                                "status": "confirmed",
                                "rationale": "independently supported",
                            }
                            for item in request["existing_candidates"]
                        ]
                    }
                )
            candidate = request["primary_candidates"][0]["candidate_id"]
            return Response(
                {
                    "decisions": [
                        {"candidate_id": candidate, "status": "confirmed", "rationale": "ok"}
                    ]
                }
            )


def write(path, value):
    path.write_text(json.dumps(value))
    return path


@pytest.fixture(autouse=True)
def immediate_provider_calls(monkeypatch):
    """Keep lane unit tests independent from shared transport throttling."""

    def invoke(client, model, instructions, schema, request, provider, effort, *args):
        del instructions, schema, provider, effort
        response = client.responses.create(
            model=model,
            input=[
                {"role": "user", "content": [{"type": "input_text", "text": json.dumps(request)}]}
            ],
        )
        args[-1].write_text(json.dumps({"request": request, "response": response.model_dump()}))
        return json.loads(response.output_text), []

    monkeypatch.setattr(lane, "call", invoke)


def context(tmp_path):
    return write(
        tmp_path / "context.json",
        {
            "artifact_type": "client_review_context_v1",
            "reasoning_only": True,
            "independent_consensus_input": False,
            "policy": {"client_comments_are_untrusted_context": True},
            "pilot_records": [],
            "client_comments": [{"client_comment": "keep source evidence"}],
        },
    )


def records(tmp_path):
    return write(
        tmp_path / "records.json",
        {
            "documents": [
                {"document_id": "d1", "fields": {"ack_number": {"value": "ACK-9"}}},
                {"document_id": "d2", "fields": {"ack_number": {"value": "ACK-9"}}},
                {"document_id": "d3", "fields": {"invoice_number": {"value": "INV-1"}}},
            ]
        },
    )


def prior(tmp_path):
    primary = write(
        tmp_path / "primary.json",
        {
            "artifact_type": "client_review_iterative_primary_v1",
            "candidates": [
                {
                    "document_id": "d1",
                    "candidate_type": "ack_reference",
                    "candidate_value": "ACK-9",
                }
            ],
        },
    )
    buddy = write(
        tmp_path / "buddy.json",
        {
            "artifact_type": "client_review_iterative_buddy_v1",
            "candidates": [
                {"document_id": "d1", "candidate_type": "ack_reference", "candidate_value": "ACK-9"}
            ],
        },
    )
    value = {
        "artifact_type": "evidence_graph_v1",
        "schema_version": "1.0",
        "run_id": "r",
        "manifest_sha256": "0" * 64,
        "nodes": [],
        "edges": [],
        "exceptions": [],
    }
    value["graph_sha256"] = graph_model._content_hash(value)
    graph = write(tmp_path / "graph.json", value)
    return primary, buddy, graph


def test_cross_packet_neighborhoods_cover_discovery_and_resolved_verification(tmp_path):
    source = records(tmp_path)
    primary, buddy, graph = prior(tmp_path)
    selected, skipped = lane.neighborhoods(source, primary, buddy, graph, 8, 10)
    assert selected == [
        {"target_document_ids": ["d2"], "related_document_ids": ["d1"], "tokens": ["ack-9"]}
    ]
    assert skipped == [
        {
            "document_id": "d3",
            "source_document_ids": ["d3"],
            "reason": "cross_packet_no_shared_source_identifier",
        }
    ]
    verification, verification_skipped = lane.verification_neighborhoods(
        source, buddy, graph, 8, 10
    )
    assert verification == [
        {"target_document_ids": ["d1"], "related_document_ids": ["d2"], "tokens": ["ack-9"]}
    ]
    assert verification_skipped == []
    with pytest.raises(ValueError, match="positive"):
        lane.neighborhoods(source, primary, buddy, graph, 0, 10)
    with pytest.raises(ValueError, match="target document IDs"):
        lane.neighborhoods(
            source,
            primary,
            buddy,
            graph,
            8,
            10,
            target_document_ids={"missing"},
        )
    with pytest.raises(ValueError, match="prior artifact"):
        lane._candidates(write(tmp_path / "bad-prior.json", {}), "expected")
    with pytest.raises(ValueError, match="graph"):
        lane._graph(write(tmp_path / "bad-graph.json", {}))
    with pytest.raises(ValueError, match="object"):
        lane._load(write(tmp_path / "list.json", []), "list")
    assert lane._tokens({"fields": {"vendor_name": {"value": " Acme   LLC "}}}) == ["acme llc"]
    assert lane._tokens({"items": [{"unrelated_field": "ignored"}]}) == []
    assert lane._tokens({"fields": {"vendor_name": {"value": "!!!"}}}) == []
    with pytest.raises(ValueError, match="unique"):
        duplicate = write(
            tmp_path / "duplicate.json",
            {"documents": [{"document_id": "d"}, {"document_id": "d"}]},
        )
        lane.neighborhoods(duplicate, primary, buddy, graph, 8, 10)
    limited, _ = lane.neighborhoods(source, primary, buddy, graph, 8, 1)
    assert len(limited) == 1
    assert (
        lane._verification_status({"status": "confirmed"}, {"status": "confirmed"}) == "confirmed"
    )
    assert lane._verification_status({"status": "confirmed"}, {"status": "conflict"}) == "conflict"
    assert (
        lane._verification_status({"status": "unsupported"}, {"status": "unsupported"})
        == "unsupported"
    )
    assert (
        lane._verification_status({"status": "confirmed"}, {"status": "close_needs_review"})
        == "close_needs_review"
    )
    with pytest.raises(ValueError, match="contract"):
        lane._verification_decisions({"decisions": [{"candidate_id": "bad"}]}, [])
    proposal = {"document_id": "d1", "candidate_type": "ack_reference", "candidate_value": "ACK-9"}
    with pytest.raises(ValueError, match="response must"):
        lane._verification_decisions({}, [proposal])
    duplicate_decision = lane._verification_decisions(
        {
            "decisions": [
                {"candidate_id": lane.key(proposal), "status": "confirmed", "rationale": "first"},
                {"candidate_id": lane.key(proposal), "status": "conflict", "rationale": "second"},
            ]
        },
        [proposal],
    )[lane.key(proposal)]
    assert duplicate_decision["status"] == "conflict"
    assert "duplicate candidate_id decisions merged" in duplicate_decision["rationale"]
    assert (
        lane._verification_decisions({"decisions": []}, [proposal])[lane.key(proposal)]["status"]
        == "unsupported"
    )
    extra_records = [
        {"document_id": "d1", "fields": {"ack_number": {"value": "ACK-9"}}},
        {"document_id": "d2", "fields": {"ack_number": {"value": "ACK-9"}}},
        {"document_id": "d4", "fields": {"ack_number": {"value": "ACK-9"}}},
    ]
    _, deferred = lane._neighborhoods(extra_records, {"d1"}, 8, 1, select_resolved=False)
    assert deferred == [
        {
            "document_id": "d4",
            "source_document_ids": ["d4"],
            "reason": "cross_packet_discovery_deferred_by_cap",
        }
    ]
    _, verification_deferred = lane._neighborhoods(
        extra_records, {"d1", "d2"}, 8, 1, select_resolved=True
    )
    assert verification_deferred == [
        {
            "document_id": "d2",
            "source_document_ids": ["d2"],
            "reason": "cross_packet_verification_deferred_by_cap",
        }
    ]
    with pytest.raises(ValueError, match="max-context"):
        lane._packet([{"document_id": "d"}], [], [], 1, ["a"], 1)


def test_retry_targets_select_only_provider_failures(tmp_path):
    failures = write(
        tmp_path / "exceptions.json",
        {
            "artifact_type": "client_review_cross_packet_exceptions_v1",
            "exceptions": [
                {
                    "reason": "cross_packet_primary_or_buddy_failure",
                    "source_document_ids": ["d1", "d2"],
                },
                {
                    "reason": "cross_packet_verification_primary_or_buddy_failure",
                    "source_document_ids": ["d3"],
                },
                {"reason": "cross_packet_verification_deferred_by_cap"},
            ],
        },
    )
    assert lane.retry_targets(failures) == ({"d1", "d2"}, {"d3"})
    with pytest.raises(ValueError, match="no provider-failed"):
        lane.retry_targets(
            write(
                tmp_path / "deferred.json",
                {"artifact_type": "client_review_cross_packet_exceptions_v1", "exceptions": []},
            )
        )
    with pytest.raises(ValueError, match="client_review_cross_packet_exceptions_v1"):
        lane.retry_targets(write(tmp_path / "wrong.json", {"exceptions": []}))
    with pytest.raises(ValueError, match="source document IDs"):
        lane.retry_targets(
            write(
                tmp_path / "bad.json",
                {
                    "artifact_type": "client_review_cross_packet_exceptions_v1",
                    "exceptions": [
                        {
                            "reason": "cross_packet_primary_or_buddy_failure",
                            "source_document_ids": [""],
                        }
                    ],
                },
            )
        )
    with pytest.raises(ValueError, match="source document IDs"):
        lane.retry_targets(
            write(
                tmp_path / "bad-nonretry-source-ids.json",
                {
                    "artifact_type": "client_review_cross_packet_exceptions_v1",
                    "exceptions": [
                        {
                            "reason": "cross_packet_verification_deferred_by_cap",
                            "source_document_ids": [1],
                        }
                    ],
                },
            )
        )
    with pytest.raises(ValueError, match="source document IDs"):
        lane.retry_targets(
            write(
                tmp_path / "missing-source-ids.json",
                {
                    "artifact_type": "client_review_cross_packet_exceptions_v1",
                    "exceptions": [{"reason": "cross_packet_primary_or_buddy_failure"}],
                },
            )
        )
    with pytest.raises(ValueError, match="entries must be objects"):
        lane.retry_targets(
            write(
                tmp_path / "non-object.json",
                {
                    "artifact_type": "client_review_cross_packet_exceptions_v1",
                    "exceptions": ["bad"],
                },
            )
        )
    with pytest.raises(ValueError, match="exceptions list"):
        lane.retry_targets(
            write(
                tmp_path / "non-list.json",
                {"artifact_type": "client_review_cross_packet_exceptions_v1", "exceptions": {}},
            )
        )


def test_run_preserves_cross_packet_raw_output_and_exceptions(tmp_path):
    source = records(tmp_path)
    primary, buddy, graph = prior(tmp_path)
    result = lane.run(
        context(tmp_path),
        source,
        primary,
        buddy,
        graph,
        tmp_path / "out.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        Google(),
        OpenAI(),
        "gemini",
        "luna",
        max_documents=8,
        max_neighborhoods=10,
        max_iterations=1,
    )
    assert result["summary"]["neighborhoods"] == 1
    assert result["summary"]["candidates"] == 1
    assert result["summary"]["verifications"] == 1
    assert result["candidates"][0]["buddy_status"] == "confirmed"
    assert (result["provider"], result["buddy_provider"]) == ("google", "openai")
    assert result["verifications"][0]["verification_status"] == "confirmed"
    assert (tmp_path / "raw/iteration-01/neighborhood-001-primary.json").exists()
    assert (tmp_path / "raw/iteration-01/verification-001-buddy.json").exists()
    exceptions = json.loads((tmp_path / "exceptions.json").read_text())["exceptions"]
    assert exceptions[0]["document_id"] == "d3"
    with pytest.raises(ValueError, match="exists"):
        lane.run(
            context(tmp_path),
            source,
            primary,
            buddy,
            graph,
            tmp_path / "out.json",
            tmp_path / "x.json",
            tmp_path / "raw2",
            Google(),
            OpenAI(),
            "g",
            "o",
            max_iterations=1,
        )


def test_cross_packet_roles_are_explicit_and_independent(tmp_path):
    source = records(tmp_path)
    primary, buddy, graph = prior(tmp_path)
    with pytest.raises(ValueError, match="genuinely independent"):
        raw_run(
            context(tmp_path),
            source,
            primary,
            buddy,
            graph,
            tmp_path / "same-provider.json",
            tmp_path / "same-provider-exceptions.json",
            tmp_path / "same-provider-raw",
            Google(),
            OpenAI(),
            "p",
            "b",
            primary_provider="openai",
            buddy_provider="openai",
        )
    result = raw_run(
        context(tmp_path),
        source,
        primary,
        buddy,
        graph,
        tmp_path / "reversed.json",
        tmp_path / "reversed-exceptions.json",
        tmp_path / "reversed-raw",
        Google(),
        OpenAI(),
        "p",
        "b",
        primary_provider="openai",
        buddy_provider="google",
        max_documents=8,
        max_neighborhoods=10,
        max_iterations=1,
    )
    assert (result["provider"], result["buddy_provider"]) == ("openai", "google")


def test_run_accepts_retained_failure_retry_targets(tmp_path):
    source = records(tmp_path)
    primary, buddy, graph = prior(tmp_path)
    retry = write(
        tmp_path / "retry.json",
        {
            "artifact_type": "client_review_cross_packet_exceptions_v1",
            "exceptions": [
                {"reason": "cross_packet_primary_or_buddy_failure", "source_document_ids": ["d1"]}
            ],
        },
    )
    result = lane.run(
        context(tmp_path),
        source,
        primary,
        buddy,
        graph,
        tmp_path / "retry-out.json",
        tmp_path / "retry-exceptions.json",
        tmp_path / "retry-raw",
        Google(),
        OpenAI(),
        "g",
        "o",
        max_documents=8,
        max_neighborhoods=10,
        max_iterations=1,
        retry_exceptions_path=retry,
    )
    assert result["retry_exceptions_sha256"]


def test_run_automatically_refines_new_cross_packet_relationships(tmp_path):
    source = records(tmp_path)
    primary, buddy, graph = prior(tmp_path)
    result = lane.run(
        context(tmp_path),
        source,
        primary,
        buddy,
        graph,
        tmp_path / "out.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        Google(),
        OpenAI(),
        "gemini",
        "luna",
        max_documents=8,
        max_neighborhoods=10,
        max_iterations=2,
        convergence_min_new_candidates=1,
    )
    assert result["summary"]["completed_iterations"] == 2
    assert result["summary"]["termination_reason"] == "converged_no_new_material_relationships"
    assert [round_["new_material_candidate_count"] for round_ in result["iterations"]] == [1, 0]
    assert (tmp_path / "raw/iteration-02/verification-001-primary.json").exists()


def test_cli_help_does_not_describe_primary_buddy_as_provider_names():
    """--primary/--buddy are the iterative lane's artifact paths, not provider
    selectors -- provider choice for this lane comes only from .env. The shared
    help vocabulary in cli_help.py has a generic "--primary"/"--buddy" entry
    meant for provider-name options elsewhere; this command must override it
    with its own inline help or silently inherit the wrong description."""
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(root / "scripts" / "client_review_cross_packet.py"), "--help"],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    options_text = completed.stdout.split("options:", 1)[1]
    primary_start = options_text.index("--primary PRIMARY")
    buddy_start = options_text.index("--buddy BUDDY")
    primary_section = options_text[primary_start:buddy_start]
    buddy_section = options_text[buddy_start : options_text.index("--graph GRAPH")]
    for section in (primary_section, buddy_section):
        assert "provider" not in section.lower() or "PROVIDER" in section
        assert "iterative" in section.lower()


def test_failure_and_main_paths(monkeypatch, tmp_path, capsys):
    source = records(tmp_path)
    primary, buddy, graph = prior(tmp_path)
    monkeypatch.setenv("CLIENT_REVIEW_CROSS_PACKET_PRIMARY_PROVIDER", "google")
    monkeypatch.setenv("CLIENT_REVIEW_CROSS_PACKET_BUDDY_PROVIDER", "openai")
    monkeypatch.setenv("CLIENT_REVIEW_CROSS_PACKET_PRIMARY_MODEL", "test-primary")
    monkeypatch.setenv("CLIENT_REVIEW_CROSS_PACKET_BUDDY_MODEL", "test-buddy")

    class Broken:
        class responses:
            @staticmethod
            def create(**kwargs):
                del kwargs
                raise RuntimeError("down")

    result = lane.run(
        context(tmp_path),
        source,
        primary,
        buddy,
        graph,
        tmp_path / "failed.json",
        tmp_path / "failed-exceptions.json",
        tmp_path / "failed-raw",
        Broken(),
        OpenAI(),
        "g",
        "o",
    )
    assert result["summary"]["exceptions"] == 3
    assert (tmp_path / "failed-raw/iteration-01/neighborhood-001-exception.json").exists()
    assert (tmp_path / "failed-raw/iteration-01/verification-001-exception.json").exists()
    with pytest.raises(ValueError, match="reasoning effort"):
        lane.run(
            context(tmp_path),
            source,
            primary,
            buddy,
            graph,
            tmp_path / "invalid.json",
            tmp_path / "invalid-exceptions.json",
            tmp_path / "invalid-raw",
            Google(),
            OpenAI(),
            "g",
            "o",
            reasoning_effort="invalid",
        )
    with pytest.raises(ValueError, match="max iterations"):
        lane.run(
            context(tmp_path),
            source,
            primary,
            buddy,
            graph,
            tmp_path / "invalid-iterations.json",
            tmp_path / "invalid-iterations-exceptions.json",
            tmp_path / "invalid-iterations-raw",
            Google(),
            OpenAI(),
            "g",
            "o",
            max_iterations=0,
        )
    with pytest.raises(ValueError, match="convergence minimum"):
        lane.run(
            context(tmp_path),
            source,
            primary,
            buddy,
            graph,
            tmp_path / "invalid-minimum.json",
            tmp_path / "invalid-minimum-exceptions.json",
            tmp_path / "invalid-minimum-raw",
            Google(),
            OpenAI(),
            "g",
            "o",
            convergence_min_new_candidates=0,
        )

    called = {}
    monkeypatch.setattr(lane, "build_reviewer_client", lambda *args: args[0])
    monkeypatch.setattr(
        lane,
        "run",
        lambda *args, **kwargs: called.update(args=args, kwargs=kwargs) or {"summary": {"ok": 1}},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_cross_packet.py",
            "context",
            "--records",
            "records",
            "--primary",
            "primary",
            "--buddy",
            "buddy",
            "--graph",
            "graph",
            "--out",
            "out",
            "--exceptions",
            "exceptions",
            "--raw-dir",
            "raw",
            "--retry-exceptions",
            "failed-exceptions",
            "--max-documents",
            "4",
            "--max-neighborhoods",
            "2",
            "--max-verification-neighborhoods",
            "3",
            "--max-context-bytes",
            "999",
            "--max-iterations",
            "4",
            "--convergence-min-new-candidates",
            "2",
            "--reasoning-effort",
            "high",
        ],
    )
    lane.main()
    assert called["kwargs"]["max_documents"] == 4
    assert called["kwargs"]["max_verification_neighborhoods"] == 3
    assert called["kwargs"]["max_iterations"] == 4
    assert called["kwargs"]["convergence_min_new_candidates"] == 2
    assert called["kwargs"]["retry_exceptions_path"] == "failed-exceptions"
    assert '"ok": 1' in capsys.readouterr().out
    monkeypatch.setenv("CLIENT_REVIEW_CROSS_PACKET_MAX_DOCUMENTS", "5")
    monkeypatch.setenv("CLIENT_REVIEW_CROSS_PACKET_MAX_NEIGHBORHOODS", "6")
    monkeypatch.setenv("CLIENT_REVIEW_CROSS_PACKET_MAX_VERIFICATION_NEIGHBORHOODS", "7")
    monkeypatch.setenv("CLIENT_REVIEW_CROSS_PACKET_MAX_CONTEXT_BYTES", "800")
    monkeypatch.setenv("CLIENT_REVIEW_CROSS_PACKET_REASONING_EFFORT", "low")
    monkeypatch.setenv("CLIENT_REVIEW_CROSS_PACKET_MAX_ITERATIONS", "1")
    monkeypatch.setenv("CLIENT_REVIEW_CROSS_PACKET_CONVERGENCE_MIN_NEW_CANDIDATES", "3")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_cross_packet.py",
            "context",
            "--records",
            "records",
            "--primary",
            "primary",
            "--buddy",
            "buddy",
            "--graph",
            "graph",
            "--out",
            "out-defaults",
            "--exceptions",
            "exceptions-defaults",
            "--raw-dir",
            "raw-defaults",
        ],
    )
    lane.main()
    assert (
        called["kwargs"]
        | {
            "max_documents": 5,
            "max_neighborhoods": 6,
            "max_verification_neighborhoods": 7,
            "max_context_bytes": 800,
            "reasoning_effort": "low",
            "max_iterations": 1,
            "convergence_min_new_candidates": 3,
        }
        == called["kwargs"]
    )
    monkeypatch.setattr(lane, "load_project_env", lambda: None)
    monkeypatch.setattr(
        lane, "build_reviewer_client", lambda *args: (_ for _ in ()).throw(ValueError("bad"))
    )
    with pytest.raises(SystemExit, match="Cross-packet review failed"):
        lane.main()


def test_buddy_packet_overflow_is_a_retained_exception(monkeypatch, tmp_path):
    source = records(tmp_path)
    primary, buddy, graph = prior(tmp_path)
    monkeypatch.setattr(
        lane,
        "_packet",
        lambda *args: {"source_records": [{"document_id": "d2"}]},
    )
    result = lane.run(
        context(tmp_path),
        source,
        primary,
        buddy,
        graph,
        tmp_path / "overflow.json",
        tmp_path / "overflow-exceptions.json",
        tmp_path / "overflow-raw",
        Google(),
        OpenAI(),
        "g",
        "o",
        max_context_bytes=150,
    )
    assert result["summary"]["exceptions"] == 3


def test_verification_candidate_schema_overflow_is_a_retained_exception(monkeypatch, tmp_path):
    source = records(tmp_path)
    primary, buddy, graph = prior(tmp_path)
    buddy_value = json.loads(buddy.read_text())
    buddy_value["candidates"] *= 19
    write(buddy, buddy_value)
    monkeypatch.setattr(lane, "neighborhoods", lambda *args, **kwargs: ([], []))
    monkeypatch.setattr(
        lane,
        "verification_neighborhoods",
        lambda *args, **kwargs: (
            [
                {
                    "target_document_ids": ["d1"],
                    "related_document_ids": ["d2"],
                    "tokens": ["ack-9"],
                }
            ],
            [],
        ),
    )
    result = lane.run(
        context(tmp_path),
        source,
        primary,
        buddy,
        graph,
        tmp_path / "schema-overflow.json",
        tmp_path / "schema-overflow-exceptions.json",
        tmp_path / "schema-overflow-raw",
        Google(),
        OpenAI(),
        "g",
        "o",
    )
    assert result["summary"]["verifications"] == 0
    assert any(
        item["reason"] == "cross_packet_verification_primary_or_buddy_failure"
        and item["source_document_ids"] == ["d1", "d2"]
        for item in json.loads((tmp_path / "schema-overflow-exceptions.json").read_text())[
            "exceptions"
        ]
    )
