"""Tests for writing the status that says an exception was answered."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

resolve_module = importlib.import_module("exception_resolve")

NOW = "2026-01-01T00:00:00+00:00"


def write(path, value):
    path.write_text(json.dumps(value))
    return path


def field(queued=True):
    return {"value": None, "consensus_flag": "no_consensus", "queue_for_review": queued}


def consensus(documents=None):
    return {
        "summary": {"documents": 1, "fields": 2},
        "documents": documents
        if documents is not None
        else [
            {
                "document_id": "doc-1",
                "review_status": "open_exception",
                "fields": {"header.total": field(), "header.brand_name": field(queued=False)},
            }
        ],
    }


def authorization(fields=("header.total",), document_ids=("doc-1",), status="operator_authorized"):
    return {
        "authorization_id": "auth-1",
        "authorization_status": status,
        "authorized_patch_ids": ["patch-1"],
        "compiled_plan": {
            "patches": [
                {
                    "patch_id": "patch-1",
                    "change_type": "document_type_rule",
                    "impact": {"document_ids": list(document_ids), "fields": list(fields)},
                },
                {
                    "patch_id": "patch-deferred",
                    "change_type": "document_type_rule",
                    "impact": {"document_ids": ["doc-other"], "fields": ["header.total"]},
                },
            ]
        },
    }


def run(cons=None, auth=None):
    cons = consensus() if cons is None else cons
    auth = authorization() if auth is None else auth
    return resolve_module.resolve(cons, auth, resolve_module.authorized_resolutions(auth), NOW)


def test_a_document_whose_every_open_finding_is_authorized_is_cleared():
    result, report = run()
    document = result["documents"][0]
    assert document["review_status"] == "exception_resolved"
    # The status it reached on its own readings is retained, never overwritten.
    assert document["prior_review_status"] == "open_exception"
    assert document["resolution"] == {
        "evidence": "operator_authorized_exception_resolution",
        "authorization_id": "auth-1",
        "resolved_at": NOW,
        "resolved_fields": ["header.total"],
    }
    assert result["summary"]["exception_resolved"] == 1
    assert result["summary"]["left_open"] == 0
    assert report["summary"]["exception_resolved"] == 1
    assert report["named_but_not_cleared"] == []


def test_a_partially_authorized_document_is_left_open_and_named():
    cons = consensus(
        [
            {
                "document_id": "doc-1",
                "review_status": "open_exception",
                "fields": {"header.total": field(), "header.date": field()},
            }
        ]
    )
    result, report = run(cons=cons)
    # Clearing here would bury the finding nobody answered.
    assert result["documents"][0]["review_status"] == "open_exception"
    assert "resolution" not in result["documents"][0]
    assert result["summary"]["exception_resolved"] == 0
    assert result["summary"]["left_open"] == 1
    entry = report["named_but_not_cleared"][0]
    assert entry["document_id"] == "doc-1"
    assert entry["unresolved_fields"] == ["header.date"]
    assert entry["open_finding_count"] == 2
    assert "not every finding" in entry["reason"]


def test_a_document_the_authorization_never_named_is_left_open_without_a_report_entry():
    cons = consensus(
        [
            {
                "document_id": "doc-2",
                "review_status": "open_exception",
                "fields": {"header.total": field()},
            }
        ]
    )
    result, report = run(cons=cons)
    assert result["documents"][0]["review_status"] == "open_exception"
    assert result["summary"]["left_open"] == 1
    assert report["named_but_not_cleared"] == []
    assert report["authorized_documents_absent_from_consensus"] == ["doc-1"]
    assert report["summary"]["authorized_documents_absent_from_consensus"] == 1


def test_a_document_with_no_queued_finding_is_reported_rather_than_cleared():
    cons = consensus(
        [
            {
                "document_id": "doc-1",
                "review_status": "open_exception",
                "fields": {"header.total": field(queued=False)},
            }
        ]
    )
    result, report = run(cons=cons)
    # Its status is held by a provider exception or an extension-only record,
    # which this control does not resolve.
    assert result["documents"][0]["review_status"] == "open_exception"
    assert "does not resolve" in report["named_but_not_cleared"][0]["reason"]
    assert report["named_but_not_cleared"][0]["unresolved_finding_count"] == 0


@pytest.mark.parametrize("status", ["auto_accepted", "sampled_verified", "exception_resolved"])
def test_a_document_already_clear_is_never_relabelled(status):
    cons = consensus(
        [{"document_id": "doc-1", "review_status": status, "fields": {"header.total": field()}}]
    )
    result, report = run(cons=cons)
    assert result["documents"][0]["review_status"] == status
    assert "resolution" not in result["documents"][0]
    assert result["summary"]["already_clear"] == 1
    assert report["named_but_not_cleared"] == []


def test_only_the_named_fields_are_resolved_however_the_payload_scopes_itself():
    auth = authorization()
    auth["compiled_plan"]["patches"][0]["append_only_payload"] = {"scope": "document"}
    cons = consensus(
        [
            {
                "document_id": "doc-1",
                "review_status": "open_exception",
                "fields": {"header.total": field(), "header.date": field()},
            }
        ]
    )
    result, _ = run(cons=cons, auth=auth)
    # A document-wide scope beside a single named field must not clear the rest.
    assert result["documents"][0]["review_status"] == "open_exception"


def test_an_unauthorized_plan_is_refused():
    with pytest.raises(ValueError, match="operator-authorized compiled plan is required"):
        resolve_module.authorized_resolutions(authorization(status="operator_review"))


def test_a_plan_that_is_not_an_object_is_refused():
    with pytest.raises(ValueError, match="authorization must be a JSON object"):
        resolve_module.authorized_resolutions(["not", "an", "object"])


def test_an_authorization_naming_no_patches_is_refused():
    auth = authorization()
    auth["authorized_patch_ids"] = []
    with pytest.raises(ValueError, match="names no authorized patches"):
        resolve_module.authorized_resolutions(auth)


def test_an_authorization_carrying_no_compiled_plan_is_refused():
    auth = authorization()
    auth["compiled_plan"] = {"patches": "not-a-list"}
    with pytest.raises(ValueError, match="does not carry a compiled plan"):
        resolve_module.authorized_resolutions(auth)


def test_an_authorized_patch_without_impact_is_refused():
    auth = authorization()
    del auth["compiled_plan"]["patches"][0]["impact"]
    with pytest.raises(ValueError, match="carries no impact"):
        resolve_module.authorized_resolutions(auth)


def test_an_authorized_patch_naming_no_field_is_refused():
    auth = authorization(fields=())
    with pytest.raises(ValueError, match="must name the fields it resolves"):
        resolve_module.authorized_resolutions(auth)


def test_an_authorization_resolving_no_document_is_refused():
    auth = authorization(document_ids=())
    with pytest.raises(ValueError, match="resolves no documents"):
        resolve_module.authorized_resolutions(auth)


def test_a_non_consensus_artifact_is_refused(tmp_path):
    path = write(tmp_path / "c.json", {"accepted": []})
    with pytest.raises(ValueError, match="not a consensus record artifact"):
        resolve_module.load_consensus(path)


def test_a_document_that_is_not_an_object_is_refused(tmp_path):
    path = write(tmp_path / "c.json", {"documents": ["doc-1"]})
    with pytest.raises(ValueError, match="each consensus document must be an object"):
        resolve_module.load_consensus(path)


def test_a_document_without_fields_is_refused(tmp_path):
    path = write(tmp_path / "c.json", {"documents": [{"document_id": "doc-1"}]})
    with pytest.raises(ValueError, match="requires a fields object"):
        resolve_module.load_consensus(path)


def test_the_command_writes_both_artifacts(tmp_path, capsys):
    cons = write(tmp_path / "consensus.json", consensus())
    auth = write(tmp_path / "auth.json", authorization())
    out = tmp_path / "out.json"
    report = tmp_path / "report.json"
    assert (
        resolve_module.main([str(cons), str(auth), "--out", str(out), "--report", str(report)]) == 0
    )
    written = json.loads(out.read_text())
    assert written["documents"][0]["review_status"] == "exception_resolved"
    assert json.loads(report.read_text())["artifact_type"] == "exception_resolution_report_v1"
    printed = capsys.readouterr().out
    assert "Documents marked exception_resolved: 1" in printed
    assert "auth-1" in printed


def test_the_report_is_written_even_when_nothing_cleared(tmp_path, capsys):
    cons = write(
        tmp_path / "consensus.json",
        consensus(
            [
                {
                    "document_id": "doc-1",
                    "review_status": "open_exception",
                    "fields": {"header.total": field(), "header.date": field()},
                }
            ]
        ),
    )
    auth = write(tmp_path / "auth.json", authorization())
    out = tmp_path / "out.json"
    report = tmp_path / "report.json"
    assert (
        resolve_module.main([str(cons), str(auth), "--out", str(out), "--report", str(report)]) == 0
    )
    # A run where nothing cleared and a run never executed must not look alike.
    assert json.loads(report.read_text())["summary"]["named_but_not_cleared"] == 1
    assert "Documents marked exception_resolved: 0" in capsys.readouterr().out


def test_absent_authorized_documents_are_reported_by_the_command(tmp_path, capsys):
    cons = write(
        tmp_path / "consensus.json",
        consensus(
            [
                {
                    "document_id": "doc-2",
                    "review_status": "open_exception",
                    "fields": {"header.total": field()},
                }
            ]
        ),
    )
    auth = write(tmp_path / "auth.json", authorization())
    out = tmp_path / "out.json"
    report = tmp_path / "report.json"
    assert (
        resolve_module.main([str(cons), str(auth), "--out", str(out), "--report", str(report)]) == 0
    )
    assert "authorized but absent from consensus: 1" in capsys.readouterr().out


def test_quiet_prints_nothing(tmp_path, capsys):
    cons = write(tmp_path / "consensus.json", consensus())
    auth = write(tmp_path / "auth.json", authorization())
    out = tmp_path / "out.json"
    report = tmp_path / "report.json"
    argv = [str(cons), str(auth), "--out", str(out), "--report", str(report), "--quiet"]
    assert resolve_module.main(argv) == 0
    assert capsys.readouterr().out == ""


def test_a_refusal_exits_with_the_reason(tmp_path):
    cons = write(tmp_path / "consensus.json", consensus())
    auth = write(tmp_path / "auth.json", authorization(status="operator_review"))
    with pytest.raises(SystemExit) as exc:
        resolve_module.main(
            [
                str(cons),
                str(auth),
                "--out",
                str(tmp_path / "out.json"),
                "--report",
                str(tmp_path / "report.json"),
            ]
        )
    assert "Exception resolution failed" in str(exc.value)


def exceptions(entries=None):
    return {
        "summary": {"count": 2},
        "exceptions": entries
        if entries is not None
        else [
            {"document_id": "doc-1", "field": "header.total", "reason": "no_majority"},
            {"document_id": "doc-1", "field": "header.brand_name", "reason": "no_majority"},
        ],
    }


def test_an_authorized_finding_is_marked_answered_and_retained():
    """The finding stays in the artifact; only its client-review flag changes."""
    amended, answered = resolve_module.answer_exceptions(
        exceptions(), {"doc-1": {"header.total"}}, {"authorization_id": "auth-1"}
    )
    assert answered == 1
    first, second = amended["exceptions"]
    assert first["client_review_required"] is False
    assert first["resolution"] == resolve_module.RESOLUTION_EVIDENCE
    assert first["authorization_id"] == "auth-1"
    assert first["reason"] == "no_majority"
    assert "client_review_required" not in second
    assert amended["summary"]["resolved_by_operator_authorization"] == 1
    assert amended["summary"]["still_client_review_required"] == 1
    assert amended["summary"]["count"] == 2


def test_a_field_the_authorization_never_named_is_left_standing():
    """Answering one finding must not suppress the ones nobody was asked about."""
    _, answered = resolve_module.answer_exceptions(
        exceptions(), {"doc-1": {"header.discount"}}, {"authorization_id": "auth-1"}
    )
    assert answered == 0


def test_a_document_the_authorization_never_named_is_left_standing():
    _, answered = resolve_module.answer_exceptions(
        exceptions(), {"doc-2": {"header.total"}}, {"authorization_id": "auth-1"}
    )
    assert answered == 0


def test_an_already_answered_finding_is_not_counted_twice():
    entries = [
        {
            "document_id": "doc-1",
            "field": "header.total",
            "client_review_required": False,
        }
    ]
    amended, answered = resolve_module.answer_exceptions(
        exceptions(entries), {"doc-1": {"header.total"}}, {"authorization_id": "auth-1"}
    )
    assert answered == 0
    assert amended["exceptions"][0] == entries[0]


@pytest.mark.parametrize("artifact", [[], {"exceptions": {}}, {"summary": {}}])
def test_an_artifact_without_an_exceptions_list_is_refused(artifact):
    with pytest.raises(ValueError, match="exceptions list"):
        resolve_module.answer_exceptions(artifact, {}, {})


def test_a_non_object_exception_is_refused():
    with pytest.raises(ValueError, match="each exception must be an object"):
        resolve_module.answer_exceptions({"exceptions": ["nope"]}, {}, {})


def test_the_cli_writes_each_amended_exception_artifact(tmp_path, capsys):
    """Without this half the gate keeps asking a question the operator answered."""
    cons = write(tmp_path / "consensus.json", consensus())
    auth = write(tmp_path / "auth.json", authorization())
    source = write(tmp_path / "exceptions.json", exceptions())
    destination = tmp_path / "exceptions_answered.json"
    argv = [
        str(cons),
        str(auth),
        "--out",
        str(tmp_path / "out.json"),
        "--report",
        str(tmp_path / "report.json"),
        "--exceptions",
        str(source),
        str(destination),
    ]
    assert resolve_module.main(argv) == 0
    amended = json.loads(destination.read_text())
    assert amended["exceptions"][0]["client_review_required"] is False
    assert json.loads(source.read_text()) == exceptions()
    assert "exceptions marked answered: 1" in capsys.readouterr().out
