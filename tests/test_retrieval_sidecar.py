"""Tests for the internal FastAPI retrieval sidecar."""

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

sidecar = importlib.import_module("retrieval_sidecar")
runtime_config = importlib.import_module("runtime_config")


def _removed_at_teardown(monkeypatch, *names):
    """Have teardown remove ``names`` even when the code under test sets them itself."""
    for name in names:
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


def test_health_and_capabilities_endpoints():
    client = TestClient(sidecar.create_app(database_path="unused.sqlite"))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "service": "business-doc-ingestion-retrieval",
    }

    capabilities = client.get("/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json() == {
        "capabilities": [
            "account_card",
            "query_records",
            "search_records",
            "analyze_sales",
            "run_report",
        ]
    }


def test_account_card_success_and_empty_normalization(monkeypatch):
    monkeypatch.setattr(sidecar, "resolve_database_path", lambda _path=None: "snapshot.sqlite")
    monkeypatch.setattr(
        sidecar.crm_service,
        "account_card",
        lambda _database, party_key=None, name=None: {
            "party": {"party_key": party_key, "canonical_name": name},
            "source_documents": [
                {
                    "document_id": "doc-acme",
                    "source_file": "acme.pdf",
                    "source_page_range": "1",
                }
            ],
        },
    )
    client = TestClient(sidecar.create_app())

    response = client.post("/account_card", json={"name": "Acme"})
    assert response.status_code == 200
    assert response.json() == {
        "summary": "Found 1 matching account card.",
        "records": [
            {
                "party": {"party_key": None, "canonical_name": "Acme"},
                "source_documents": [
                    {
                        "document_id": "doc-acme",
                        "source_file": "acme.pdf",
                        "source_page_range": "1",
                    }
                ],
            }
        ],
        "citations": [{"source_document_id": "doc-acme", "label": "acme.pdf#page=1"}],
    }

    monkeypatch.setattr(
        sidecar.crm_service,
        "account_card",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("CRM account was not found")),
    )
    empty = client.post("/account_card", json={"name": "Missing"})
    assert empty.status_code == 200
    assert empty.json() == {
        "summary": "No matching records found.",
        "records": [],
        "citations": [],
    }


def test_query_and_search_records_success_and_empty_normalization(monkeypatch):
    monkeypatch.setattr(sidecar, "resolve_database_path", lambda _path=None: "snapshot.sqlite")
    monkeypatch.setattr(
        sidecar.crm_service,
        "query_records",
        lambda *_args, **_kwargs: {
            "rows": [{"invoice_key": "invoice-1", "source_document_id": "doc-1"}]
        },
    )
    monkeypatch.setattr(
        sidecar.crm_service,
        "search_records",
        lambda *_args, **_kwargs: {
            "results": [
                {
                    "table": "invoice_header",
                    "key": ["invoice-1"],
                    "record": {"invoice_key": "invoice-1", "source_document_id": "doc-1"},
                }
            ]
        },
    )
    client = TestClient(sidecar.create_app())

    query = client.post("/query_records", json={"table": "invoice_header", "limit": 5, "offset": 0})
    assert query.status_code == 200
    assert query.json() == {
        "summary": "Found 1 matching approved row.",
        "records": [{"invoice_key": "invoice-1", "source_document_id": "doc-1"}],
        "citations": [{"source_document_id": "doc-1", "label": "invoice_header:doc-1"}],
    }

    search = client.post(
        "/search_records",
        json={"query": "open invoices", "tables": ["invoice_header"], "limit": 5},
    )
    assert search.status_code == 200
    assert search.json() == {
        "summary": "Found 1 matching approved record.",
        "records": [
            {
                "table": "invoice_header",
                "key": ["invoice-1"],
                "record": {"invoice_key": "invoice-1", "source_document_id": "doc-1"},
            }
        ],
        "citations": [{"source_document_id": "doc-1", "label": "invoice_header:doc-1"}],
    }

    monkeypatch.setattr(
        sidecar.crm_service, "query_records", lambda *_args, **_kwargs: {"rows": []}
    )
    monkeypatch.setattr(
        sidecar.crm_service, "search_records", lambda *_args, **_kwargs: {"results": []}
    )
    assert client.post("/query_records", json={"table": "invoice_header"}).json() == {
        "summary": "No matching records found.",
        "records": [],
        "citations": [],
    }
    assert client.post("/search_records", json={"query": "nothing"}).json() == {
        "summary": "No matching records found.",
        "records": [],
        "citations": [],
    }


def test_analyze_sales_and_run_report_success_and_empty_normalization(monkeypatch):
    monkeypatch.setattr(sidecar, "resolve_database_path", lambda _path=None: "snapshot.sqlite")
    monkeypatch.setattr(
        sidecar.crm_service,
        "analyze_sales",
        lambda *_args, **_kwargs: {
            "group_by": ["company", "currency"],
            "effective_group_by": ["company", "currency"],
            "row_count": 1,
            "currency_partitioned": True,
            "rows": [
                {
                    "company": "Acme",
                    "currency": "USD",
                    "invoice_count": 2,
                    "total_amount": "200.00",
                    "amount_due": "25.00",
                    "average_invoice_amount": "100.00",
                    "source_document_ids": ["doc-1"],
                }
            ],
        },
    )
    monkeypatch.setattr(
        sidecar.crm_service,
        "run_report",
        lambda *_args, **_kwargs: {
            "report": "receivables_aging",
            "parameters": {"as_of": "2026-09-08"},
            "row_count": 1,
            "rows": [{"invoice_key": "invoice-1", "source_document_id": "doc-1"}],
        },
    )
    client = TestClient(sidecar.create_app())

    sales = client.post("/analyze_sales", json={"group_by": ["company", "currency"], "filters": {}})
    assert sales.status_code == 200
    assert sales.json() == {
        "summary": "Sales analysis returned 1 row(s).",
        "records": [
            {
                "company": "Acme",
                "currency": "USD",
                "invoice_count": 2,
                "total_amount": "200.00",
                "amount_due": "25.00",
                "average_invoice_amount": "100.00",
                "source_document_ids": ["doc-1"],
            }
        ],
        "metrics": {
            "group_by": ["company", "currency"],
            "effective_group_by": ["company", "currency"],
            "row_count": 1,
            "currency_partitioned": True,
        },
        "citations": [{"source_document_id": "doc-1", "label": "company=Acme, currency=USD"}],
    }

    report = client.post(
        "/run_report",
        json={"name": "receivables_aging", "parameters": {"as_of": "2026-09-08"}},
    )
    assert report.status_code == 200
    assert report.json() == {
        "summary": "Report receivables_aging returned 1 row(s).",
        "records": [{"invoice_key": "invoice-1", "source_document_id": "doc-1"}],
        "metrics": {
            "report": "receivables_aging",
            "parameters": {"as_of": "2026-09-08"},
            "row_count": 1,
        },
        "citations": [{"source_document_id": "doc-1", "label": "receivables_aging:doc-1"}],
    }

    monkeypatch.setattr(
        sidecar.crm_service,
        "analyze_sales",
        lambda *_args, **_kwargs: {
            "group_by": ["company"],
            "effective_group_by": ["company", "currency"],
            "row_count": 0,
            "currency_partitioned": True,
            "rows": [],
        },
    )
    monkeypatch.setattr(
        sidecar.crm_service,
        "run_report",
        lambda *_args, **_kwargs: {
            "report": "receivables_aging",
            "parameters": {},
            "row_count": 0,
            "rows": [],
        },
    )
    assert client.post("/analyze_sales", json={"group_by": ["company"]}).json() == {
        "summary": "No matching records found.",
        "records": [],
        "metrics": {},
        "citations": [],
    }
    assert client.post("/run_report", json={"name": "receivables_aging"}).json() == {
        "summary": "No matching records found.",
        "records": [],
        "metrics": {},
        "citations": [],
    }


def test_request_validation_maps_to_400():
    client = TestClient(sidecar.create_app(database_path="unused.sqlite"))
    response = client.post("/search_records", json={"tables": ["invoice_header"], "limit": 5})
    assert response.status_code == 400
    assert response.json()["error"] == "Invalid request"
    assert "query" in response.json()["details"]


def test_request_validation_rejects_blank_values_and_unknown_keys():
    client = TestClient(sidecar.create_app(database_path="unused.sqlite"))

    account = client.post("/account_card", json={"name": "   "})
    assert account.status_code == 400
    assert "must not be blank" in account.json()["details"]

    search = client.post("/search_records", json={"query": "   "})
    assert search.status_code == 400
    assert "must not be blank" in search.json()["details"]

    query = client.post("/query_records", json={"table": "   "})
    assert query.status_code == 400
    assert "must not be blank" in query.json()["details"]

    report = client.post("/run_report", json={"name": "   "})
    assert report.status_code == 400
    assert "must not be blank" in report.json()["details"]

    unknown = client.post("/search_records", json={"query": "ok", "extra": True})
    assert unknown.status_code == 400
    assert "extra" in unknown.json()["details"]


def test_runtime_failures_map_to_500(monkeypatch):
    monkeypatch.setattr(
        sidecar,
        "resolve_database_path",
        lambda _path=None: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )
    client = TestClient(sidecar.create_app(), raise_server_exceptions=False)
    response = client.post("/search_records", json={"query": "test", "limit": 5})
    assert response.status_code == 500
    assert response.json() == {
        "error": "Service failure",
        "details": "database unavailable",
    }


def test_database_resolution_and_argument_parsing(monkeypatch, tmp_path):
    assert sidecar.resolve_database_path("explicit.sqlite") == "explicit.sqlite"

    sqlite_path = tmp_path / "business_retrieval.sqlite"
    monkeypatch.setattr(sidecar.os, "environ", {"RETRIEVAL_DB_URL": f"sqlite:///{sqlite_path}"})
    assert sidecar.resolve_database_path() == str(sqlite_path)

    direct_path = tmp_path / "direct.sqlite"
    monkeypatch.setattr(sidecar.os, "environ", {"RETRIEVAL_ARTIFACT_ROOT": str(direct_path)})
    assert sidecar.resolve_database_path() == str(direct_path)

    artifact_root = tmp_path / "artifacts"
    monkeypatch.setattr(sidecar.os, "environ", {"RETRIEVAL_ARTIFACT_ROOT": str(artifact_root)})
    assert sidecar.resolve_database_path() == str(artifact_root / "business_retrieval.sqlite")

    monkeypatch.setattr(sidecar.os, "environ", {})
    try:
        sidecar.resolve_database_path()
    except RuntimeError as exc:
        assert "not configured" in str(exc)
    else:  # pragma: no cover - explicit failure branch
        raise AssertionError("Expected RuntimeError for missing retrieval database configuration")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "retrieval_sidecar.py",
            "--database",
            "db.sqlite",
            "--port",
            "8081",
            "--log-level",
            "debug",
        ],
    )
    args = sidecar.parse_args()
    assert args.database == "db.sqlite"
    assert args.port == 8081
    assert args.log_level == "debug"


def test_database_path_url_validation_and_citation_helpers():
    assert sidecar._database_path_from_url("/tmp/test.sqlite") == "/tmp/test.sqlite"
    assert sidecar._database_path_from_url("file:///tmp/test.sqlite") == "/tmp/test.sqlite"
    assert sidecar._database_path_from_url("sqlite:////tmp/test.sqlite") == "/tmp/test.sqlite"
    assert (
        sidecar._database_path_from_url("sqlite://localhost/tmp/test.sqlite") == "/tmp/test.sqlite"
    )
    assert (
        sidecar._database_path_from_url("sqlite://server/share/test.sqlite")
        == "//server/share/test.sqlite"
    )

    for value, message in (
        ("", "must not be empty"),
        ("postgres:///tmp/test.sqlite", "filesystem path or sqlite:/// URL"),
        ("sqlite:///tmp/test.sqlite?mode=ro", "must not include parameters"),
        ("sqlite://", "must include a SQLite database path"),
    ):
        try:
            sidecar._database_path_from_url(value)
        except ValueError as exc:
            assert message in str(exc)
        else:  # pragma: no cover - explicit failure branch
            raise AssertionError(f"Expected ValueError for {value}")

    assert sidecar._citation_label({}) == "unknown-source#page=unknown-page"
    assert sidecar._citations_from_account_cards(
        [
            {
                "source_documents": [
                    {"document_id": "doc-1", "source_file": "a.pdf", "source_page_range": "1"},
                    {"document_id": "doc-1", "source_file": "a.pdf", "source_page_range": "1"},
                    {"document_id": "", "source_file": "a.pdf", "source_page_range": "2"},
                ]
            }
        ]
    ) == [{"source_document_id": "doc-1", "label": "a.pdf#page=1"}]
    assert sidecar._citations_from_records(
        [
            {"table": "party", "record": {"source_document_id": "doc-1"}},
            {"table": "party", "record": {"document_id": "doc-2"}},
            {"table": "party", "record": {"document_id": "doc-2"}},
            {"table": "party", "record": {"document_id": ""}},
            {"table": "party", "record": "not-a-dict"},
        ]
    ) == [
        {"source_document_id": "doc-1", "label": "party:doc-1"},
        {"source_document_id": "doc-2", "label": "party:doc-2"},
    ]
    assert sidecar._citations_from_rows(
        [
            {"source_document_id": "doc-1"},
            {"document_id": "doc-2"},
            {"document_id": "doc-2"},
            {"document_id": ""},
            "not-a-dict",
        ],
        "invoice_header",
    ) == [
        {"source_document_id": "doc-1", "label": "invoice_header:doc-1"},
        {"source_document_id": "doc-2", "label": "invoice_header:doc-2"},
    ]
    assert sidecar._citations_from_sales_rows(
        [
            {
                "company": "Acme",
                "source_document_ids": ["doc-1", "doc-1", "", None],
                "invoice_count": 1,
            },
            {"source_document_ids": [], "invoice_count": 2},
        ]
    ) == [{"source_document_id": "doc-1", "label": "company=Acme"}]
    assert sidecar._empty_response("none") == {"summary": "none", "records": [], "citations": []}


def test_main_runs_uvicorn_and_rejects_invalid_port(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        sidecar,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "database": "db.sqlite",
                "host": sidecar.DEFAULT_HOST,
                "port": 8080,
                "log_level": "info",
            },
        )(),
    )
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(
            run=lambda *args, **kwargs: captured.update({"args": args, "kwargs": kwargs})
        ),
    )
    monkeypatch.setattr(
        sidecar, "create_app", lambda database_path=None: {"database_path": database_path}
    )
    sidecar.main()
    assert captured["kwargs"] == {
        "host": sidecar.DEFAULT_HOST,
        "port": 8080,
        "log_level": "info",
    }
    assert captured["args"] == ({"database_path": "db.sqlite"},)

    monkeypatch.setattr(
        sidecar,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "database": "db.sqlite",
                "host": sidecar.DEFAULT_HOST,
                "port": 0,
                "log_level": "info",
            },
        )(),
    )
    try:
        sidecar.main()
    except SystemExit as exc:
        assert "port" in str(exc)
    else:  # pragma: no cover - explicit failure branch
        raise AssertionError("Expected SystemExit for invalid port")


def test_importing_the_sidecar_does_not_change_the_environment(monkeypatch, tmp_path):
    """Only ``main`` loads the project ``.env``; importing the module must not.

    Test collection imports this module before any fixture runs. While it loaded
    ``.env`` at import, a checkout holding the operator's file put every setting
    in it into the whole test process, and an unrelated slot-equivalence test
    failed for want of a credential -- there, and only in the full suite.
    """
    project_env = tmp_path / ".env"
    project_env.write_text("SIDECAR_IMPORT_PROBE=loaded\n")
    monkeypatch.setattr(runtime_config, "project_env_path", lambda: project_env)
    _removed_at_teardown(monkeypatch, "SIDECAR_IMPORT_PROBE")
    before = dict(os.environ)
    spec = importlib.util.spec_from_file_location(
        "retrieval_sidecar_import_probe", SCRIPTS / "retrieval_sidecar.py"
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    changed = sorted(
        name
        for name in before.keys() | os.environ.keys()
        if before.get(name) != os.environ.get(name)
    )
    assert changed == []


def test_main_loads_the_project_env_before_parsing_its_defaults(monkeypatch, tmp_path):
    """The entry point loads ``.env``, and before ``--port`` and ``--log-level`` read it."""
    project_env = tmp_path / ".env"
    project_env.write_text("PORT=9090\nLOG_LEVEL=debug\n")
    monkeypatch.setattr(runtime_config, "project_env_path", lambda: project_env)
    _removed_at_teardown(monkeypatch, "PORT", "LOG_LEVEL")
    monkeypatch.setattr(sys, "argv", ["retrieval_sidecar.py"])
    captured = {}
    monkeypatch.setitem(
        sys.modules, "uvicorn", SimpleNamespace(run=lambda *args, **kwargs: captured.update(kwargs))
    )
    monkeypatch.setattr(sidecar, "create_app", lambda database_path=None: None)
    sidecar.main()
    assert captured["port"] == 9090
    assert captured["log_level"] == "debug"


def test_account_card_non_not_found_error_re_raises_as_service_failure(monkeypatch):
    monkeypatch.setattr(sidecar, "resolve_database_path", lambda _path=None: "snapshot.sqlite")
    monkeypatch.setattr(
        sidecar.crm_service,
        "account_card",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("broken snapshot")),
    )
    client = TestClient(sidecar.create_app(), raise_server_exceptions=False)
    response = client.post("/account_card", json={"name": "Acme"})
    assert response.status_code == 500
    assert response.json() == {
        "error": "Service failure",
        "details": "broken snapshot",
    }
