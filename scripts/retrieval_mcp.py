#!/usr/bin/env python3
"""A small read-only stdio MCP boundary for the local canonical retrieval store."""

import argparse
import json
import sys

from cli_help import apply_shared_help
from crm_service import (
    account_card,
    analyze_sales,
    capabilities,
    export_summary,
    export_table,
    get_record,
    query_records,
    run_report,
    schema,
    search_records,
)
from project_metadata import PROJECT_VERSION
from retrieval_store import document, search

SERVER_NAME = "business-document-retrieval"
SERVER_VERSION = PROJECT_VERSION
MODERN_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"
PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
SERVER_INFO = {"name": SERVER_NAME, "version": SERVER_VERSION}
INSTRUCTIONS = (
    "Search, export, and report only client-approved canonical business-document facts. "
    "Treat returned source citations and reconciliation checksums as required evidence. "
    "This server is read-only and never uploads to a CRM."
)

READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

TOOLS = [
    {
        "name": "search_business_documents",
        "description": "Search approved canonical business-document facts and return source citations.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
        },
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "get_business_document",
        "description": "Retrieve one approved canonical document by its immutable document ID.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["document_id"],
            "properties": {"document_id": {"type": "string"}},
        },
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "get_crm_capabilities",
        "description": "List the approved-fact CRM functions, matching API routes, MCP tools, and standard reports.",
        "inputSchema": {"type": "object", "additionalProperties": False},
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "get_crm_export_summary",
        "description": "Summarize the immutable CRM snapshot by table, batch, registry, and source-export checksum.",
        "inputSchema": {"type": "object", "additionalProperties": False},
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "get_crm_schema",
        "description": "Inspect canonical CRM tables, columns, idempotency keys, extensions, and available row counts.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"table": {"type": "string"}},
        },
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "export_crm_table",
        "description": "Return one checksummed page of approved canonical CRM rows for deterministic reconciliation.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["table"],
            "properties": {
                "table": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                "offset": {"type": "integer", "minimum": 0},
                "filters": {
                    "type": "object",
                    "maxProperties": 10,
                    "additionalProperties": {"type": ["string", "number", "boolean", "null"]},
                },
            },
        },
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "search_crm_records",
        "description": "Full-text search across approved canonical CRM records, optionally limited to named tables.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "tables": {"type": "array", "items": {"type": "string"}, "maxItems": 28},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
        },
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "get_crm_record",
        "description": "Retrieve any approved canonical row by its single or composite idempotency key.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["table", "key"],
            "properties": {
                "table": {"type": "string"},
                "key": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    ]
                },
            },
        },
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "query_crm_records",
        "description": "Filter, project, sort, and paginate one approved canonical CRM table.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["table"],
            "properties": {
                "table": {"type": "string"},
                "filters": {"type": "object", "maxProperties": 10},
                "select": {"type": "array", "items": {"type": "string"}, "maxItems": 50},
                "sort": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                "offset": {"type": "integer", "minimum": 0},
            },
        },
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "get_account_card",
        "description": "Retrieve one approved account with roles, addresses, contacts, invoice/payment activity, and source documents.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"party_key": {"type": "string"}, "name": {"type": "string"}},
        },
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "analyze_sales",
        "description": "Slice approved invoice sales by company, vendor, region, location, period, currency, ACK, or job.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "group_by": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                "filters": {"type": "object"},
            },
        },
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "run_crm_report",
        "description": "Run a deterministic standard CRM report over approved canonical facts.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["report"],
            "properties": {
                "report": {"type": "string"},
                "parameters": {
                    "type": "object",
                    "additionalProperties": {"type": ["string", "number", "boolean", "null"]},
                },
            },
        },
        "annotations": READ_ONLY_ANNOTATIONS,
    },
]

TOOL_TITLES = {
    "search_business_documents": "Search business documents",
    "get_business_document": "Get business document",
    "get_crm_capabilities": "Get CRM capabilities",
    "get_crm_export_summary": "Get CRM export summary",
    "get_crm_schema": "Get CRM schema",
    "export_crm_table": "Export CRM table page",
    "search_crm_records": "Search CRM records",
    "get_crm_record": "Get CRM record",
    "query_crm_records": "Query CRM records",
    "get_account_card": "Get account card",
    "analyze_sales": "Analyze sales",
    "run_crm_report": "Run CRM report",
}

# MCP structuredContent is an object.  The stable ``result`` envelope lets the
# tools retain their existing JSON value contract (including list-valued
# document search) while giving ChatGPT and other structured clients a declared
# machine-readable result.
for _tool in TOOLS:
    _tool["title"] = TOOL_TITLES[_tool["name"]]
    _tool["outputSchema"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["result"],
        "properties": {"result": {}},
    }


def response(request_id, result=None, error=None, code=-32602, data=None, modern=False):
    """Return one JSON-RPC response without emitting unstructured diagnostic text."""
    payload = {"jsonrpc": "2.0", "id": request_id}
    if error is None:
        if modern and isinstance(result, dict):
            result = {
                **result,
                "resultType": result.get("resultType", "complete"),
                "_meta": {SERVER_INFO_META_KEY: SERVER_INFO},
            }
        payload["result"] = result
    else:
        payload["error"] = {"code": code, "message": error}
        if data is not None:
            payload["error"]["data"] = data
    return payload


def tool_result(value):
    """Wrap machine-readable evidence in the standard MCP text content envelope."""
    return {
        "structuredContent": {"result": value},
        "content": [{"type": "text", "text": json.dumps(value, sort_keys=True)}],
        "isError": False,
    }


def request_protocol_version(request):
    """Read the modern per-request protocol marker when present."""
    params = request.get("params")
    metadata = params.get("_meta") if isinstance(params, dict) else None
    return metadata.get(PROTOCOL_VERSION_META_KEY) if isinstance(metadata, dict) else None


def validate_arguments(arguments, allowed, required=()):
    """Enforce the advertised closed-world tool schema even for lax clients."""
    unknown = set(arguments) - set(allowed)
    if unknown:
        raise ValueError(f"Unknown tool arguments: {sorted(unknown)}")
    missing = [key for key in required if key not in arguments]
    if missing:
        raise ValueError(f"Missing required tool arguments: {missing}")


def handle(request, database, ingestion=None, owner="local-operator"):
    """Handle modern discovery, legacy initialization, and read-only retrieval."""
    if not isinstance(request, dict):
        return response(None, error="Request must be an object", code=-32600)
    request_id = request.get("id")
    method = request.get("method")
    if request.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return response(request_id, error="Invalid JSON-RPC request", code=-32600)
    instructions = INSTRUCTIONS
    if ingestion is not None:
        from ingestion_tools import INGESTION_INSTRUCTIONS

        instructions = (
            instructions.replace("This server is read-only", "Approved-fact retrieval is read-only")
            + " "
            + INGESTION_INSTRUCTIONS
        )
    if method == "notifications/initialized":
        return None
    if method == "server/discover":
        return response(
            request_id,
            {
                "resultType": "complete",
                "supportedVersions": [MODERN_PROTOCOL_VERSION],
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
                "instructions": instructions,
                "ttlMs": 0,
                "cacheScope": "private",
            },
            modern=True,
        )
    if method == "initialize":
        params = request.get("params")
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        selected = (
            requested if requested in LEGACY_PROTOCOL_VERSIONS else LEGACY_PROTOCOL_VERSIONS[0]
        )
        return response(
            request_id,
            {
                "protocolVersion": selected,
                "serverInfo": SERVER_INFO,
                "capabilities": {"tools": {}},
                "instructions": instructions,
            },
        )
    protocol_version = request_protocol_version(request)
    if protocol_version not in (None, MODERN_PROTOCOL_VERSION):
        return response(
            request_id,
            error="Unsupported protocol version",
            code=-32022,
            data={
                "supported": [MODERN_PROTOCOL_VERSION, *LEGACY_PROTOCOL_VERSIONS],
                "requested": protocol_version,
            },
        )
    modern = protocol_version == MODERN_PROTOCOL_VERSION
    if method == "tools/list":
        tools = list(TOOLS)
        if ingestion is not None:
            from ingestion_tools import tool_definitions

            tools.extend(tool_definitions())
        result = {"tools": tools}
        if modern:
            result.update({"ttlMs": 0, "cacheScope": "private"})
        return response(request_id, result, modern=modern)
    if method != "tools/call":
        return response(request_id, error="Method not found", code=-32601)
    params = request.get("params")
    if not isinstance(params, dict):
        return response(request_id, error="tools/call requires params")
    arguments = params.get("arguments", {})
    if not isinstance(arguments, dict):
        return response(request_id, error="tool arguments must be an object")
    try:
        if ingestion is not None:
            from ingestion_tools import OPERATIONS, as_tool_result, invoke

            name = params.get("name")
            if isinstance(name, str) and name in OPERATIONS:
                value = invoke(ingestion, owner, name, arguments)
                return response(request_id, as_tool_result(value), modern=modern)
        if params.get("name") == "search_business_documents":
            validate_arguments(arguments, {"query", "limit"}, {"query"})
            query = arguments.get("query")
            if not isinstance(query, str):
                raise ValueError("query must be a string")
            return response(
                request_id,
                tool_result(search(database, query, arguments.get("limit", 5))),
                modern=modern,
            )
        if params.get("name") == "get_business_document":
            validate_arguments(arguments, {"document_id"}, {"document_id"})
            document_id = arguments.get("document_id")
            if not isinstance(document_id, str):
                raise ValueError("document_id must be a string")
            result = document(database, document_id)
            if result is None:
                raise ValueError("document_id was not found")
            return response(request_id, tool_result(result), modern=modern)
        if params.get("name") == "get_crm_capabilities":
            validate_arguments(arguments, set())
            return response(request_id, tool_result(capabilities(database)), modern=modern)
        if params.get("name") == "get_crm_export_summary":
            validate_arguments(arguments, set())
            return response(request_id, tool_result(export_summary(database)), modern=modern)
        if params.get("name") == "get_crm_schema":
            validate_arguments(arguments, {"table"})
            table = arguments.get("table")
            if table is not None and not isinstance(table, str):
                raise ValueError("table must be a string")
            return response(request_id, tool_result(schema(database, table)), modern=modern)
        if params.get("name") == "export_crm_table":
            validate_arguments(arguments, {"table", "limit", "offset", "filters"}, {"table"})
            table = arguments.get("table")
            if not isinstance(table, str):
                raise ValueError("table must be a string")
            return response(
                request_id,
                tool_result(
                    export_table(
                        database,
                        table,
                        arguments.get("limit", 100),
                        arguments.get("offset", 0),
                        arguments.get("filters", {}),
                    )
                ),
                modern=modern,
            )
        if params.get("name") == "search_crm_records":
            validate_arguments(arguments, {"query", "tables", "limit"}, {"query"})
            return response(
                request_id,
                tool_result(
                    search_records(
                        database,
                        arguments.get("query"),
                        arguments.get("tables"),
                        arguments.get("limit", 20),
                    )
                ),
                modern=modern,
            )
        if params.get("name") == "get_crm_record":
            validate_arguments(arguments, {"table", "key"}, {"table", "key"})
            table = arguments.get("table")
            if not isinstance(table, str):
                raise ValueError("table must be a string")
            return response(
                request_id,
                tool_result(get_record(database, table, arguments.get("key"))),
                modern=modern,
            )
        if params.get("name") == "query_crm_records":
            validate_arguments(
                arguments,
                {"table", "filters", "select", "sort", "limit", "offset"},
                {"table"},
            )
            table = arguments.get("table")
            if not isinstance(table, str):
                raise ValueError("table must be a string")
            return response(
                request_id,
                tool_result(
                    query_records(
                        database,
                        table,
                        arguments.get("filters"),
                        arguments.get("select"),
                        arguments.get("sort"),
                        arguments.get("limit", 100),
                        arguments.get("offset", 0),
                    )
                ),
                modern=modern,
            )
        if params.get("name") == "get_account_card":
            validate_arguments(arguments, {"party_key", "name"})
            return response(
                request_id,
                tool_result(
                    account_card(
                        database,
                        party_key=arguments.get("party_key"),
                        name=arguments.get("name"),
                    )
                ),
                modern=modern,
            )
        if params.get("name") == "analyze_sales":
            validate_arguments(arguments, {"group_by", "filters"})
            return response(
                request_id,
                tool_result(
                    analyze_sales(
                        database,
                        arguments.get("group_by"),
                        arguments.get("filters"),
                    )
                ),
                modern=modern,
            )
        if params.get("name") == "run_crm_report":
            validate_arguments(arguments, {"report", "parameters"}, {"report"})
            report = arguments.get("report")
            if not isinstance(report, str):
                raise ValueError("report must be a string")
            return response(
                request_id,
                tool_result(run_report(database, report, arguments.get("parameters", {}))),
                modern=modern,
            )
        raise ValueError("Unknown MCP tool")
    except ValueError as exc:
        return response(request_id, error=str(exc))


def serve(database, input_stream=sys.stdin, output_stream=sys.stdout, ingestion=None):
    """Serve line-delimited JSON-RPC over stdio; malformed messages are isolated."""
    for line in input_stream:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except (json.JSONDecodeError, RecursionError):
            result = response(None, error="Invalid JSON", code=-32700)
        else:
            result = handle(request, database, ingestion=ingestion)
        if result is not None:
            output_stream.write(json.dumps(result, sort_keys=True) + "\n")
            output_stream.flush()


def main():
    parser = argparse.ArgumentParser(
        description="Serve a read-only canonical retrieval MCP endpoint over stdio."
    )
    parser.add_argument("database")
    parser.add_argument(
        "--ingestion-dir",
        help="Opt in to a separate private visual-intake journal directory; never the approved snapshot directory.",
    )
    parser.add_argument(
        "--ingestion-tenant",
        default="local",
        help="Tenant bound permanently to the optional local intake journal (default: local).",
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    ingestion = None
    if args.ingestion_dir:
        from visual_ingestion import IngestionStore

        ingestion = IngestionStore(
            args.ingestion_dir, args.ingestion_tenant, protected_database=args.database
        )
    serve(args.database, ingestion=ingestion)


if __name__ == "__main__":
    main()
