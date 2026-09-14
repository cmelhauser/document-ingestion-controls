import importlib
import json
import sys

import pytest

lane = importlib.import_module("client_review.inference")


class Response:
    output_text = json.dumps(
        {
            "candidates": [
                {
                    "document_id": "d1",
                    "candidate_type": "ack_reference",
                    "candidate_value": "ACK-1",
                    "evidence_quote": "ACK-1",
                    "rationale": "printed",
                }
            ],
            "next_action": "stabilized",
        }
    )


class Client:
    class responses:
        @staticmethod
        def create(**kwargs):
            return Response()


class FailingClient:
    class responses:
        @staticmethod
        def create(**kwargs):
            raise RuntimeError("provider down")


class SelfCheckingClient:
    class responses:
        @staticmethod
        def create(**kwargs):
            if kwargs["text"]["format"]["name"] == "reference_discovery_self_check":
                return type(
                    "CheckResponse",
                    (),
                    {
                        "output_text": json.dumps(
                            {
                                "checks": [
                                    {
                                        "candidate_index": 0,
                                        "status": "verified",
                                        "evidence_quote": "ACK-1",
                                        "rationale": "supported",
                                    }
                                ],
                                "next_action": "stabilized",
                            }
                        )
                    },
                )()
            return Response()


def context(tmp_path):
    value = {
        "artifact_type": "client_review_context_v1",
        "reasoning_only": True,
        "independent_consensus_input": False,
        "policy": {"client_comments_are_untrusted_context": True},
        "client_comments": [
            {"review_item_id": "g1", "client_comment": "Use date", "evidence_role": "untrusted"}
        ],
        "pilot_records": [{"document_id": "d1", "document_type": "invoice"}, {"document_id": "d2"}],
    }
    path = tmp_path / "context.json"
    path.write_text(json.dumps(value))
    return path


def test_validation_batches_packet_and_reduction(tmp_path):
    records, comments = lane.validate_context(json.loads(context(tmp_path).read_text()))
    assert len(records) == 2 and comments
    assert len(lane.batches(records, 1, 1)) == 1
    assert [len(item) for item in lane.adaptive_batches(records, comments, 1, 2, 120000)] == [1, 1]
    assert [len(item) for item in lane.adaptive_batches(records, comments, 1, 2, 1)] == [1, 1]
    assert list(lane.adaptive_batches([], comments, 1, 2, 120000)) == []
    original_packet = lane.packet
    lane.packet = lambda *args: (_ for _ in ()).throw(ValueError("other"))
    with pytest.raises(ValueError, match="other"):
        list(lane.adaptive_batches(records, comments, 1, 2, 120000))
    lane.packet = original_packet
    assert lane.compact_record(records[0])["document_id"] == "d1"
    detailed = {
        "document_id": "d3",
        "header": {"invoice": {"value": "I-1", "source": "printed"}, "raw": "header"},
        "fields": {
            "po_reference": {
                "value": "PO-1",
                "candidate_values": ["PO-1", 3],
                "evidence": {"page": 1},
            },
            "other": {"value": "x" * 400, "review_reason": ["r"]},
        },
        "lines": [{"description": {"value": "part", "source": "printed"}}, "ignored"],
    }
    compact = lane.compact_record(detailed)
    assert compact["fields"]["po_reference"]["candidate_values"] == ["PO-1", "3"]
    assert len(compact["fields"]["other"]["value"]) == 240
    assert lane.verification_packet(records, comments, [], 1, 1, 1000)["round"] == 1
    with pytest.raises(ValueError, match="self-check packet"):
        lane.verification_packet(records, comments, [], 1, 1, 1)
    checked, rejected = lane.reduce_self_check(
        {"checks": [{"candidate_index": 0, "status": "verified", "evidence_quote": "x"}]},
        [{"document_id": "d1"}],
    )
    assert checked and not rejected
    assert lane.reduce_self_check(
        {"checks": [{"candidate_index": 9, "status": "verified"}]}, [{"document_id": "d1"}]
    )[1]
    assert lane.reduce_self_check(
        {"checks": [{"candidate_index": 0, "status": "needs_review"}]}, [{"document_id": "d1"}]
    )[1]
    with pytest.raises(ValueError, match="self-check response"):
        lane.reduce_self_check({}, [])
    assert lane.reduce_candidates(
        {
            "candidates": [
                {
                    "document_id": "foreign",
                    "candidate_type": "ack_reference",
                    "candidate_value": "X",
                    "evidence_quote": "X",
                }
            ]
        },
        {"d1"},
    )[1]
    with pytest.raises(ValueError, match="reasoning-only"):
        lane.validate_context(
            {
                "artifact_type": "client_review_context_v1",
                "pilot_records": [],
                "client_comments": [],
            }
        )
    with pytest.raises(ValueError, match="untrusted"):
        lane.validate_context(
            {
                "artifact_type": "client_review_context_v1",
                "reasoning_only": True,
                "independent_consensus_input": False,
                "pilot_records": [],
                "client_comments": [],
            }
        )
    with pytest.raises(ValueError, match="pilot_records"):
        lane.validate_context(
            {
                "artifact_type": "client_review_context_v1",
                "reasoning_only": True,
                "independent_consensus_input": False,
                "policy": {"client_comments_are_untrusted_context": True},
                "pilot_records": ["bad"],
                "client_comments": [],
            }
        )
    with pytest.raises(ValueError, match="client_comments"):
        lane.validate_context(
            {
                "artifact_type": "client_review_context_v1",
                "reasoning_only": True,
                "independent_consensus_input": False,
                "policy": {"client_comments_are_untrusted_context": True},
                "pilot_records": [],
                "client_comments": {},
            }
        )
    with pytest.raises(ValueError, match="positive"):
        lane.batches([], 0, 1)
    with pytest.raises(ValueError, match="positive"):
        list(lane.adaptive_batches([], [], 0, 1, 100))
    with pytest.raises(ValueError, match="packet"):
        lane.packet(records, comments, 1, 1)
    with pytest.raises(ValueError, match="candidates"):
        lane.reduce_candidates({}, {"d1"})


def test_run_retains_raw_and_exceptions(tmp_path):
    source = context(tmp_path)
    out, exc, raw = tmp_path / "out.json", tmp_path / "exceptions.json", tmp_path / "raw"
    result = lane.run(
        source, out, exc, raw, Client(), "gemini-2.5-pro", "google", batch_size=1, max_batches=1
    )
    assert result["summary"]["candidates"] == 1
    assert (raw / "batch-001.json").exists()
    checked = lane.run(
        source,
        tmp_path / "checked.json",
        tmp_path / "checked-exc.json",
        tmp_path / "checked-raw",
        SelfCheckingClient(),
        "gemini-2.5-pro",
        "google",
        batch_size=1,
        max_batches=1,
        self_check_rounds=1,
    )
    assert checked["summary"]["candidates"] == 1
    assert (tmp_path / "checked-raw/batch-001-self-check-001.json").exists()
    with pytest.raises(ValueError, match="non-negative"):
        lane.run(
            source,
            tmp_path / "bad-round.json",
            tmp_path / "bad-round-exc.json",
            tmp_path / "bad-round-raw",
            Client(),
            "gemini-2.5-pro",
            "google",
            self_check_rounds=-1,
        )
    with pytest.raises(ValueError, match="Output already exists"):
        lane.run(source, out, exc, raw, Client(), "gemini-2.5-pro", "google")
    failed = lane.run(
        source,
        tmp_path / "failed.json",
        tmp_path / "failed-exc.json",
        tmp_path / "failed-raw",
        FailingClient(),
        "gemini-2.5-pro",
        "google",
        batch_size=1,
        max_batches=1,
    )
    assert failed["summary"]["failed_batches"] == 1


def test_main_disabled_and_bad_context(monkeypatch, tmp_path, capsys):
    source = context(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_inference.py",
            str(source),
            "--out",
            str(tmp_path / "out"),
            "--exceptions",
            str(tmp_path / "exc"),
            "--raw-dir",
            str(tmp_path / "raw"),
        ],
    )
    monkeypatch.setenv("CLIENT_REVIEW_CONTEXT_LLM_ENABLED", "false")
    lane.main()
    assert "disabled" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    with pytest.raises(ValueError, match="client_review_context"):
        lane.validate_context(json.loads(bad.read_text()))


def test_main_enabled(monkeypatch, tmp_path, capsys):
    source = context(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_inference.py",
            str(source),
            "--enable",
            "--provider",
            "google",
            "--model",
            "gemini-2.5-pro",
            "--out",
            str(tmp_path / "out"),
            "--exceptions",
            str(tmp_path / "exc"),
            "--raw-dir",
            str(tmp_path / "raw"),
            "--batch-size",
            "1",
            "--max-batches",
            "1",
            "--self-check-rounds",
            "0",
        ],
    )
    monkeypatch.setattr(lane, "build_reviewer_client", lambda *args: Client())
    lane.main()
    assert "candidates: 1" in capsys.readouterr().out
    bad_out = tmp_path / "bad-out"
    monkeypatch.setenv("CLIENT_REVIEW_CONTEXT_LLM_REASONING_EFFORT", "invalid")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_inference.py",
            str(source),
            "--enable",
            "--out",
            str(bad_out),
            "--exceptions",
            str(tmp_path / "bad-exc"),
            "--raw-dir",
            str(tmp_path / "bad-raw"),
        ],
    )
    with pytest.raises(SystemExit, match="unsupported"):
        lane.main()


def test_a_context_carrying_no_records_refuses_instead_of_reading_nothing(
    monkeypatch, tmp_path, capsys
):
    """Rule 9, one level up: this lane reasoned over nothing and reported success.

    `client_input_comments.py` builds the same context artifact but always with an
    empty `pilot_records`, so before any client workbook came back this lane ran
    against a comments-only context and printed "batches: 0; candidates: 0" --
    indistinguishable from a corpus with no references in it.
    """
    empty = json.loads(context(tmp_path).read_text())
    empty["pilot_records"] = []
    source = tmp_path / "no-records.json"
    source.write_text(json.dumps(empty))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_inference.py",
            str(source),
            "--enable",
            "--out",
            str(tmp_path / "empty-out"),
            "--exceptions",
            str(tmp_path / "empty-exc"),
            "--raw-dir",
            str(tmp_path / "empty-raw"),
        ],
    )
    monkeypatch.setattr(lane, "build_reviewer_client", lambda *args: Client())
    # A refusal names itself, and names the fix, rather than handing an operator
    # a traceback to interpret.
    with pytest.raises(SystemExit, match="rebuild the context with --records") as refusal:
        lane.main()
    assert "carries no pilot records" in str(refusal.value)
    assert capsys.readouterr().out == ""


def test_worker_count_changes_speed_but_never_the_retained_artifact(tmp_path):
    """Batches may finish in any order; candidates must land in batch order.

    `Client` answers every request identically, so call order cannot influence
    the comparison -- only the lane's own assembly of results is under test.
    """
    source = context(tmp_path)

    def discover(tag, workers):
        out = tmp_path / f"{tag}.json"
        summary = lane.run(
            source,
            out,
            tmp_path / f"{tag}-exc.json",
            tmp_path / f"{tag}-raw",
            Client(),
            "gemini-2.5-pro",
            "google",
            batch_size=1,
            max_batches=2,
            workers=workers,
        )
        return summary, json.loads(out.read_text())

    serial_summary, serial = discover("serial", 1)
    concurrent_summary, concurrent = discover("concurrent", 4)

    assert len(serial["batches"]) > 1, "the fixture must produce more than one batch"
    assert concurrent_summary["summary"] == serial_summary["summary"]
    assert [item["batch"] for item in concurrent["batches"]] == [
        item["batch"] for item in serial["batches"]
    ]
    assert concurrent["candidates"] == serial["candidates"]
