import importlib
import json
import sys

import pytest

lane = importlib.import_module("client_review.consensus_review")


class Response:
    def __init__(self, value):
        self.output_text = json.dumps(value)


class Google:
    class responses:
        @staticmethod
        def create(**kwargs):
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
                            "status": "matched",
                            "rationale": "agree",
                        }
                    ],
                    "next_action": "stabilized",
                }
            )


def context(tmp_path):
    p = tmp_path / "context.json"
    p.write_text(
        json.dumps(
            {
                "pilot_records": [{"document_id": "d1", "document_type": "invoice"}],
                "client_comments": [],
                "artifact_type": "client_review_context_v1",
            }
        )
    )
    return p


def primary(tmp_path):
    p = tmp_path / "primary.json"
    p.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "document_id": "d1",
                        "candidate_type": "ack_reference",
                        "candidate_value": "A1",
                        "evidence_quote": "A1",
                        "rationale": "printed",
                    }
                ]
            }
        )
    )
    return p


def test_helpers(tmp_path):
    assert lane.load_candidates(primary(tmp_path))[0]["document_id"] == "d1"
    (tmp_path / "bad.json").write_text(json.dumps({}))
    with pytest.raises(ValueError, match="candidates"):
        lane.load_candidates(tmp_path / "bad.json")
    assert (
        len(
            lane.union_candidates(
                [{"document_id": "d1", "candidate_type": "ack_reference", "candidate_value": "A1"}],
                [{"document_id": "d1", "candidate_type": "ack_reference", "candidate_value": "A1"}],
            )
        )
        == 1
    )
    assert lane.compact_candidate({"document_id": "d1"})["candidate_type"] == ""
    assert (
        lane.adjudication_record({"document_id": "d1", "fields": {}, "lines": []})["document_id"]
        == "d1"
    )
    packet = lane.adjudication_packet([{"document_id": "d1"}], [], [], [], 1, 100000)
    assert packet["batch"] == 1
    with pytest.raises(ValueError, match="adjudication packet"):
        lane.adjudication_packet([{"document_id": "d1"}], [], [], [], 1, 1)
    final, rejected = lane.reduce_adjudication(
        {
            "decisions": [
                {"candidate_id": "d1|ack_reference|A1", "status": "matched", "rationale": "ok"}
            ]
        },
        [{"document_id": "d1", "candidate_type": "ack_reference", "candidate_value": "A1"}],
    )
    assert final and not rejected
    assert lane.reduce_adjudication(
        {"decisions": [{"candidate_id": "bad", "status": "conflict"}]}, []
    )[1]
    assert lane.reduce_adjudication(
        {"decisions": [{"candidate_id": "d1|ack_reference|A1", "status": "unsupported"}]},
        [{"document_id": "d1", "candidate_type": "ack_reference", "candidate_value": "A1"}],
    )[1]
    with pytest.raises(ValueError, match="decisions"):
        lane.reduce_adjudication({}, [])


def test_run_and_no_clobber(tmp_path):
    result = lane.run(
        context(tmp_path),
        primary(tmp_path),
        tmp_path / "secondary.json",
        tmp_path / "adjudication.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        Google(),
        OpenAI(),
        "gemini-2.5-pro",
        "gpt-5.6-luna",
        batch_size=1,
        max_batches=1,
    )
    assert result == {"secondary": 1, "adjudicated": 1, "exceptions": 0}
    assert (tmp_path / "raw/secondary-batch-001.json").exists()
    with pytest.raises(ValueError, match="Output already exists"):
        lane.run(
            context(tmp_path),
            primary(tmp_path),
            tmp_path / "secondary.json",
            tmp_path / "adjudication-2.json",
            tmp_path / "exceptions-2.json",
            tmp_path / "raw-2",
            Google(),
            OpenAI(),
            "gemini-2.5-pro",
            "gpt-5.6-luna",
            batch_size=1,
            max_batches=1,
        )


def test_run_failure_and_main(monkeypatch, tmp_path, capsys):
    class Bad:
        class responses:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("down")

    result = lane.run(
        context(tmp_path),
        primary(tmp_path),
        tmp_path / "s.json",
        tmp_path / "a.json",
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
    tiny = lane.run(
        context(tmp_path),
        primary(tmp_path),
        tmp_path / "ts.json",
        tmp_path / "ta.json",
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
            "client_review_consensus.py",
            str(context(tmp_path)),
            "--primary",
            str(primary(tmp_path)),
            "--secondary-out",
            str(tmp_path / "ms"),
            "--adjudication-out",
            str(tmp_path / "ma"),
            "--exceptions",
            str(tmp_path / "me"),
            "--raw-dir",
            str(tmp_path / "mr"),
        ],
    )
    monkeypatch.setattr(lane, "build_reviewer_client", lambda *args: Google())
    lane.main()
    assert "Secondary candidates" in capsys.readouterr().out
