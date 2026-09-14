#!/usr/bin/env python3
"""Serve a minimal internal retrieval sidecar for co-located applications."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import crm_service
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from runtime_config import load_project_env

DEFAULT_HOST = "0.0.0.0"  # noqa: S104 - required default bind target for ECS sidecar use.
DEFAULT_PORT = 8080
DEFAULT_LOG_LEVEL = "info"
DEFAULT_DATABASE_NAME = "business_retrieval.sqlite"
SERVICE_NAME = "business-doc-ingestion-retrieval"
MAX_LIMIT = 200
CAPABILITIES = [
    "account_card",
    "query_records",
    "search_records",
    "analyze_sales",
    "run_report",
]
LOGGER = logging.getLogger(__name__)


class _StrictModel(BaseModel):
    """Base request model that rejects unknown keys."""

    model_config = ConfigDict(extra="forbid")


class AccountCardRequest(_StrictModel):
    """Validated request body for a focused account-card lookup."""

    name: str = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _strip_and_require_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class SearchRecordsRequest(_StrictModel):
    """Validated request body for free-text record search."""

    query: str = Field(min_length=1)
    tables: list[str] | None = None
    limit: int = Field(default=10, ge=1, le=MAX_LIMIT)

    @field_validator("query")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class QueryRecordsRequest(_StrictModel):
    """Validated request body for one-table structured querying."""

    table: str = Field(min_length=1)
    filters: dict[str, Any] | None = None
    select: list[str] | None = None
    sort: list[str] | None = None
    limit: int = Field(default=100, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0)

    @field_validator("table")
    @classmethod
    def _strip_table(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class AnalyzeSalesRequest(_StrictModel):
    """Validated request body for grouped sales analysis."""

    group_by: list[str] | None = None
    filters: dict[str, Any] | None = None


class RunReportRequest(_StrictModel):
    """Validated request body for deterministic report execution."""

    name: str = Field(min_length=1)
    parameters: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class Citation(BaseModel):
    """One source-visible citation returned by the sidecar transport."""

    source_document_id: str
    label: str


class RetrievalResponse(BaseModel):
    """Stable JSON envelope for account and general record retrieval."""

    summary: str
    records: list[dict[str, Any]]
    citations: list[Citation]


class AnalysisResponse(RetrievalResponse):
    """Stable JSON envelope for structured sales analysis."""

    metrics: dict[str, Any] = Field(default_factory=dict)


def _validation_detail(exc: RequestValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", ()) if item != "body")
        message = error.get("msg", "invalid value")
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts) or "request body is invalid"


def _database_path_from_url(value: str) -> str:
    """Resolve a supported SQLite URL or bare filesystem path."""
    parsed = urlparse(value)
    if parsed.scheme in {"", "file"}:
        path = parsed.path or value
        if not path:
            raise ValueError("RETRIEVAL_DB_URL must not be empty")
        return unquote(path)
    if parsed.scheme != "sqlite":
        raise ValueError("RETRIEVAL_DB_URL must be a filesystem path or sqlite:/// URL")
    if parsed.params or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("RETRIEVAL_DB_URL must not include parameters, query, fragment, or auth")
    path = unquote(parsed.path)
    if parsed.netloc == "localhost":
        path = path or "/"
    elif parsed.netloc:
        path = f"//{parsed.netloc}{path}"
    elif path.startswith("//"):
        path = path[1:]
    if not path:
        raise ValueError("RETRIEVAL_DB_URL must include a SQLite database path")
    return path


def resolve_database_path(database_path: str | None = None) -> str:
    """Resolve the sidecar's immutable retrieval snapshot path."""
    if database_path is not None:
        return str(database_path)
    configured = os.environ.get("RETRIEVAL_DB_URL")
    if configured:
        return _database_path_from_url(configured)
    artifact_root = os.environ.get("RETRIEVAL_ARTIFACT_ROOT")
    if artifact_root:
        root = Path(artifact_root)
        database = root if root.suffix == ".sqlite" else root / DEFAULT_DATABASE_NAME
        return str(database)
    raise RuntimeError(
        "Retrieval database is not configured; set RETRIEVAL_DB_URL or RETRIEVAL_ARTIFACT_ROOT"
    )


def _citation_label(document: dict[str, Any]) -> str:
    source_file = document.get("source_file") or "unknown-source"
    page_range = document.get("source_page_range") or "unknown-page"
    return f"{source_file}#page={page_range}"


def _citations_from_account_cards(cards: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    citations = []
    for card in cards:
        for document in card.get("source_documents", []):
            document_id = document.get("document_id")
            if not isinstance(document_id, str) or not document_id:
                continue
            label = _citation_label(document)
            key = (document_id, label)
            if key in seen:
                continue
            seen.add(key)
            citations.append({"source_document_id": document_id, "label": label})
    return citations


def _citations_from_records(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    citations = []
    for entry in records:
        record = entry.get("record", {})
        if not isinstance(record, dict):
            continue
        document_id = record.get("source_document_id") or record.get("document_id")
        if not isinstance(document_id, str) or not document_id:
            continue
        label = f"{entry.get('table', 'record')}:{document_id}"
        key = (document_id, label)
        if key in seen:
            continue
        seen.add(key)
        citations.append({"source_document_id": document_id, "label": label})
    return citations


def _citations_from_rows(records: list[dict[str, Any]], label_prefix: str) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    citations = []
    for record in records:
        if not isinstance(record, dict):
            continue
        document_id = record.get("source_document_id") or record.get("document_id")
        if not isinstance(document_id, str) or not document_id:
            continue
        label = f"{label_prefix}:{document_id}"
        key = (document_id, label)
        if key in seen:
            continue
        seen.add(key)
        citations.append({"source_document_id": document_id, "label": label})
    return citations


def _citations_from_sales_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    citations = []
    for row in rows:
        dimensions = [
            f"{key}={value}"
            for key, value in row.items()
            if key
            not in {
                "invoice_count",
                "total_amount",
                "amount_due",
                "average_invoice_amount",
                "source_document_ids",
            }
        ]
        label = ", ".join(dimensions) or "sales-analysis"
        for document_id in row.get("source_document_ids", []):
            if not isinstance(document_id, str) or not document_id:
                continue
            key = (document_id, label)
            if key in seen:
                continue
            seen.add(key)
            citations.append({"source_document_id": document_id, "label": label})
    return citations


def _empty_response(summary: str = "No matching records found.") -> dict[str, Any]:
    return {"summary": summary, "records": [], "citations": []}


def create_app(database_path: str | None = None) -> FastAPI:
    """Create the sidecar application with one immutable database configuration."""
    app = FastAPI(title=SERVICE_NAME)
    app.state.database_path = database_path

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid request", "details": _validation_detail(exc)},
        )

    @app.exception_handler(Exception)
    async def _handle_runtime(_request: Request, exc: Exception) -> JSONResponse:
        LOGGER.exception("Retrieval sidecar request failed")
        return JSONResponse(
            status_code=500,
            content={"error": "Service failure", "details": str(exc)},
        )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "service": SERVICE_NAME}

    @app.get("/capabilities")
    async def capabilities() -> dict[str, Any]:
        return {"capabilities": CAPABILITIES}

    @app.post("/account_card", response_model=RetrievalResponse)
    async def account_card(request: AccountCardRequest) -> dict[str, Any]:
        database = resolve_database_path(app.state.database_path)
        try:
            record = crm_service.account_card(database, name=request.name)
        except ValueError as exc:
            if "not found" in str(exc):
                return _empty_response()
            raise
        return {
            "summary": "Found 1 matching account card.",
            "records": [record],
            "citations": _citations_from_account_cards([record]),
        }

    @app.post("/query_records", response_model=RetrievalResponse)
    async def query_records(request: QueryRecordsRequest) -> dict[str, Any]:
        database = resolve_database_path(app.state.database_path)
        result = crm_service.query_records(
            database,
            request.table,
            filters=request.filters,
            select=request.select,
            sort=request.sort,
            limit=request.limit,
            offset=request.offset,
        )
        records = result["rows"]
        if not records:
            return _empty_response()
        count = len(records)
        return {
            "summary": f"Found {count} matching approved row{'s' if count != 1 else ''}.",
            "records": records,
            "citations": _citations_from_rows(records, request.table),
        }

    @app.post("/search_records", response_model=RetrievalResponse)
    async def search_records(request: SearchRecordsRequest) -> dict[str, Any]:
        database = resolve_database_path(app.state.database_path)
        result = crm_service.search_records(
            database,
            request.query,
            tables=request.tables,
            limit=request.limit,
        )
        records = result["results"]
        if not records:
            return _empty_response()
        count = len(records)
        return {
            "summary": f"Found {count} matching approved record{'s' if count != 1 else ''}.",
            "records": records,
            "citations": _citations_from_records(records),
        }

    @app.post("/analyze_sales", response_model=AnalysisResponse)
    async def analyze_sales(request: AnalyzeSalesRequest) -> dict[str, Any]:
        database = resolve_database_path(app.state.database_path)
        analysis = crm_service.analyze_sales(
            database,
            group_by=request.group_by,
            filters=request.filters,
        )
        records = analysis["rows"]
        if not records:
            return {**_empty_response(), "metrics": {}}
        return {
            "summary": f"Sales analysis returned {len(records)} row(s).",
            "records": records,
            "metrics": {
                "group_by": analysis["group_by"],
                "effective_group_by": analysis["effective_group_by"],
                "row_count": analysis["row_count"],
                "currency_partitioned": analysis["currency_partitioned"],
            },
            "citations": _citations_from_sales_rows(records),
        }

    @app.post("/run_report", response_model=AnalysisResponse)
    async def run_report(request: RunReportRequest) -> dict[str, Any]:
        database = resolve_database_path(app.state.database_path)
        report = crm_service.run_report(database, request.name, request.parameters)
        records = report["rows"]
        if not records:
            return {**_empty_response(), "metrics": {}}
        return {
            "summary": f"Report {request.name} returned {len(records)} row(s).",
            "records": records,
            "metrics": {
                "report": report["report"],
                "parameters": report["parameters"],
                "row_count": report["row_count"],
            },
            "citations": _citations_from_rows(records, request.name),
        }

    return app


def parse_args() -> argparse.Namespace:
    """Parse explicit runtime overrides for local sidecar execution."""
    parser = argparse.ArgumentParser(
        description="Serve the internal retrieval sidecar over FastAPI and uvicorn."
    )
    parser.add_argument(
        "--database",
        help="Immutable retrieval SQLite database. Overrides RETRIEVAL_DB_URL and RETRIEVAL_ARTIFACT_ROOT.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", DEFAULT_HOST),
        help=f"Bind address for the sidecar HTTP listener (default: {DEFAULT_HOST}).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", str(DEFAULT_PORT))),
        help=f"Port for the sidecar HTTP listener (default: {DEFAULT_PORT}).",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", DEFAULT_LOG_LEVEL),
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        help=f"Uvicorn log level (default: {DEFAULT_LOG_LEVEL}).",
    )
    return parser.parse_args()


app = create_app()


def main() -> None:
    """Run the sidecar listener directly for local development or containers."""
    # Here and not at import: test collection imports this module, and loading
    # `.env` at import put the operator's settings into the whole test process.
    # Before `parse_args`, whose --host, --port and --log-level defaults come
    # from the environment.
    load_project_env()
    args = parse_args()
    if args.port < 1 or args.port > 65535:
        raise SystemExit("Retrieval sidecar failed: port must be from 1 through 65535")

    import uvicorn

    uvicorn.run(
        create_app(database_path=args.database),
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
