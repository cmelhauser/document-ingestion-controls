#!/usr/bin/env python3
"""Build and query a local, provenance-citing retrieval database from canonical exports."""

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

from canonical_load import IDEMPOTENCY_KEYS, LOAD_ORDER, build_plan, plan_checksum, stable_checksum
from cli_help import apply_shared_help
from crm_service import SNAPSHOT_SCHEMA_VERSION

CLEARED = {"auto_accepted", "sampled_verified", "exception_resolved"}
TOKEN = re.compile(r"[A-Za-z0-9]+")


def load_export(path):
    """Read a canonical export envelope shared with the load orchestration step."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get("tables"), dict):
        raise ValueError("Canonical export must contain a tables object")
    if not isinstance(data.get("batch_id"), str) or not data["batch_id"]:
        raise ValueError("Canonical export requires batch_id")
    return data


def value(item):
    """Preserve normal scalar values while accepting extraction field objects."""
    return item.get("value") if isinstance(item, dict) and "value" in item else item


def allowed(row):
    """Keep every unresolved record out of the factual retrieval corpus."""
    return row.get("review_status") in CLEARED


def document_chunks(export):
    """Create one factual, citation-bearing chunk per approved canonical document."""
    tables = export["tables"]
    invoices = {row.get("invoice_key"): row for row in tables.get("invoice_header", [])}
    acknowledgements = {
        row.get("ack_number"): row
        for row in tables.get("acknowledgement", [])
        if isinstance(row, dict) and allowed(row)
    }
    jobs = {
        row.get("job_number"): row
        for row in tables.get("job", [])
        if isinstance(row, dict) and allowed(row)
    }
    locations = {
        row.get("selling_location_key"): row
        for row in tables.get("selling_location", [])
        if isinstance(row, dict) and allowed(row)
    }
    # An invoice names its parties only by key, so a chunk built without this
    # join contains no party name anywhere and cannot answer the runbook's own
    # example question ("unpaid ACME invoices"). Resolve the approved party rows
    # and carry their canonical names as facts, exactly as they were approved.
    parties = {
        row.get("party_key"): row
        for row in tables.get("party", [])
        if isinstance(row, dict) and allowed(row)
    }
    attributions = {}
    for row in tables.get("attribution", []):
        if isinstance(row, dict) and allowed(row):
            attributions.setdefault(row.get("source_document_id"), []).append(row)
    lines = {}
    for row in tables.get("invoice_line", []):
        lines.setdefault(row.get("invoice_key"), []).append(row)
    chunks = []
    for row in tables.get("document", []):
        if not isinstance(row, dict) or not isinstance(row.get("document_id"), str):
            raise ValueError("document rows require document_id")
        if not allowed(row):
            continue
        for field in ("source_file", "source_page_range", "source_sha256"):
            if not isinstance(row.get(field), str) or not row[field]:
                raise ValueError(f"document rows require {field}")
        document_id = row["document_id"]
        related = [
            item
            for item in invoices.values()
            if item.get("source_document_id") == document_id and allowed(item)
        ]
        facts = {
            key: value(item)
            for key, item in row.items()
            if key not in {"batch_id", "review_status"}
        }
        invoice_facts = []
        for invoice in related:
            detail = {
                key: value(item)
                for key, item in invoice.items()
                if key not in {"batch_id", "review_status"}
            }
            for column, name in (
                ("biller_party_key", "biller"),
                ("payer_party_key", "payer"),
                ("ship_to_party_key", "ship_to"),
            ):
                party = parties.get(invoice.get(column))
                if party:
                    detail[name] = value(party.get("canonical_name"))
            detail["lines"] = [
                {key: value(item) for key, item in line.items() if key != "batch_id"}
                for line in lines.get(invoice.get("invoice_key"), [])
                if allowed(line)
            ]
            invoice_facts.append(detail)
        attribution_facts = []
        for attribution in attributions.get(document_id, []):
            detail = {
                key: value(item)
                for key, item in attribution.items()
                if key not in {"batch_id", "review_status"}
            }
            ack = acknowledgements.get(attribution.get("ack_number"))
            job = jobs.get(attribution.get("job_number"))
            location = locations.get(attribution.get("selling_location_key"))
            if ack:
                detail["acknowledgement"] = {
                    key: value(item)
                    for key, item in ack.items()
                    if key not in {"batch_id", "review_status"}
                }
            if job:
                detail["job"] = {
                    key: value(item)
                    for key, item in job.items()
                    if key not in {"batch_id", "review_status"}
                }
            if location:
                detail["selling_location"] = {
                    key: value(item)
                    for key, item in location.items()
                    if key not in {"batch_id", "review_status"}
                }
            attribution_facts.append(detail)
        citation = {
            "document_id": document_id,
            "source_file": row.get("source_file"),
            "source_page_range": row.get("source_page_range"),
            "source_hash": row.get("source_sha256"),
            "batch_id": export.get("batch_id"),
            "registry_version": export.get("registry_version"),
            "review_status": row.get("review_status"),
        }
        content = json.dumps(
            {
                "document": facts,
                "invoices": invoice_facts,
                "attributions": attribution_facts,
            },
            sort_keys=True,
            default=str,
            allow_nan=False,
        )
        chunks.append({"document_id": document_id, "content": content, "citation": citation})
    return chunks


def build_database(export, destination, allow_empty=False):
    """Create a new FTS5 SQLite database without mutating the source export.

    An export whose rows are all unapproved yields zero chunks. Building that
    silently produces a database that answers "no results" for every question,
    which is indistinguishable from the facts simply not being present. The build
    refuses unless the empty result is explicitly accepted.
    """
    destination = Path(destination)
    if destination.exists():
        raise ValueError(f"Retrieval database already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    plan = build_plan(export)
    chunks = document_chunks(export)
    if not chunks and not allow_empty:
        raise ValueError(
            "canonical export produced no approved retrieval chunks; supply an "
            "export with review-clear documents or pass --allow-empty"
        )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        connection = sqlite3.connect(temporary)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    try:
        connection.executescript(
            "CREATE TABLE retrieval_chunk (chunk_id INTEGER PRIMARY KEY, document_id TEXT NOT NULL, content TEXT NOT NULL, citation_json TEXT NOT NULL); "
            "CREATE VIRTUAL TABLE retrieval_fts USING fts5(content, content=retrieval_chunk, content_rowid=chunk_id); "
            "CREATE TABLE crm_metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL); "
            "CREATE TABLE crm_record (record_id INTEGER PRIMARY KEY, table_name TEXT NOT NULL, ordinal INTEGER NOT NULL, idempotency_key_json TEXT NOT NULL, row_json TEXT NOT NULL, row_sha256 TEXT NOT NULL, UNIQUE (table_name, ordinal), UNIQUE (table_name, idempotency_key_json)); "
            "CREATE INDEX crm_record_table ON crm_record(table_name, ordinal); "
            "CREATE VIRTUAL TABLE crm_record_fts USING fts5(table_name UNINDEXED, idempotency_key_json UNINDEXED, row_json);"
        )
        for chunk in chunks:
            cursor = connection.execute(
                "INSERT INTO retrieval_chunk(document_id, content, citation_json) VALUES (?, ?, ?)",
                (
                    chunk["document_id"],
                    chunk["content"],
                    json.dumps(chunk["citation"], sort_keys=True),
                ),
            )
            connection.execute(
                "INSERT INTO retrieval_fts(rowid, content) VALUES (?, ?)",
                (cursor.lastrowid, chunk["content"]),
            )
        metadata = {
            "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
            "batch_id": export["batch_id"],
            "registry_version": export.get("registry_version"),
            "source_export_sha256": stable_checksum(export),
            "load_plan_sha256": plan_checksum(plan),
            "table_manifest": {
                table: {
                    "records": len(export["tables"].get(table, [])),
                    "rows_sha256": stable_checksum(export["tables"].get(table, [])),
                }
                for table in LOAD_ORDER
            },
        }
        connection.executemany(
            "INSERT INTO crm_metadata(key, value_json) VALUES (?, ?)",
            [
                (key, json.dumps(value, sort_keys=True, separators=(",", ":")))
                for key, value in metadata.items()
            ],
        )
        crm_records = 0
        for table in LOAD_ORDER:
            for ordinal, row in enumerate(export["tables"].get(table, [])):
                key = [row[name] for name in IDEMPOTENCY_KEYS[table]]
                row_json = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)
                cursor = connection.execute(
                    "INSERT INTO crm_record(table_name, ordinal, idempotency_key_json, row_json, row_sha256) VALUES (?, ?, ?, ?, ?)",
                    (
                        table,
                        ordinal,
                        json.dumps(key, sort_keys=True, separators=(",", ":")),
                        row_json,
                        stable_checksum(row),
                    ),
                )
                connection.execute(
                    "INSERT INTO crm_record_fts(rowid, table_name, idempotency_key_json, row_json) VALUES (?, ?, ?, ?)",
                    (
                        cursor.lastrowid,
                        table,
                        json.dumps(key, sort_keys=True, separators=(",", ":")),
                        row_json,
                    ),
                )
                crm_records += 1
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise ValueError("Retrieval database integrity check failed before publication")
    finally:
        connection.close()
        if sys.exc_info()[0] is not None:
            temporary.unlink(missing_ok=True)
    try:
        os.link(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    temporary.unlink()
    return {
        "chunks": len(chunks),
        "crm_records": crm_records,
        "crm_tables": len(LOAD_ORDER),
        "factual_rows_only": True,
    }


def fts_query(question):
    """Convert a natural-language question into safe all-token FTS syntax."""
    return " AND ".join(TOKEN.findall(question))


def read_only_connection(database):
    """Open an existing SQLite file without creating or mutating it."""
    try:
        uri = Path(database).resolve(strict=True).as_uri()
        connection = sqlite3.connect(f"{uri}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        return connection
    except (OSError, TypeError, sqlite3.Error) as exc:
        raise ValueError("Retrieval database is unavailable or invalid") from exc


def search(database, question, limit=5):
    """Return compact source-backed results for read-only tool use."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ValueError("limit must be an integer from 1 through 20")
    if not isinstance(question, str):
        raise ValueError("question must be a string")
    query = fts_query(question)
    if not query:
        return []
    connection = read_only_connection(database)
    try:
        rows = connection.execute(
            "SELECT c.document_id, c.content, c.citation_json FROM retrieval_fts f JOIN retrieval_chunk c ON c.chunk_id = f.rowid WHERE retrieval_fts MATCH ? ORDER BY bm25(retrieval_fts), c.chunk_id LIMIT ?",
            (query, limit),
        ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"Retrieval query failed: {exc}") from exc
    finally:
        connection.close()
    try:
        return [
            {"document_id": row[0], "content": row[1], "citation": json.loads(row[2])}
            for row in rows
        ]
    except json.JSONDecodeError as exc:
        raise ValueError("Retrieval citation JSON is invalid; rebuild the database") from exc


def document(database, document_id):
    """Retrieve a specific source-backed document chunk without fuzzy inference."""
    connection = read_only_connection(database)
    try:
        row = connection.execute(
            "SELECT document_id, content, citation_json FROM retrieval_chunk WHERE document_id = ?",
            (document_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError(f"Retrieval query failed: {exc}") from exc
    finally:
        connection.close()
    if row is None:
        return None
    try:
        citation = json.loads(row[2])
    except json.JSONDecodeError as exc:
        raise ValueError("Retrieval citation JSON is invalid; rebuild the database") from exc
    return {"document_id": row[0], "content": row[1], "citation": citation}


def main():
    parser = argparse.ArgumentParser(
        description="Build or query a local canonical retrieval SQLite database."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("canonical_export")
    build.add_argument("--out", required=True)
    build.add_argument(
        "--allow-empty",
        action="store_true",
        help="retain a retrieval database with no approved chunks",
    )
    query = commands.add_parser("query")
    query.add_argument("database")
    query.add_argument(
        "question", help="Natural-language question converted to all-token full-text search."
    )
    query.add_argument("--limit", type=int, default=5)
    get = commands.add_parser("get")
    get.add_argument("database")
    get.add_argument(
        "document_id", help="Exact document identifier to retrieve. No fuzzy inference is applied."
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        if args.command == "build":
            result = build_database(load_export(args.canonical_export), args.out, args.allow_empty)
        elif args.command == "query":
            result = search(args.database, args.question, args.limit)
        else:
            result = document(args.database, args.document_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Retrieval store failed: {exc}")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
