#!/usr/bin/env python3
"""Explicitly enabled, TLS-only, bearer-authenticated read-only retrieval API."""

import argparse
import hmac
import json
import os
import re
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from cli_help import apply_shared_help
from crm_service import (
    account_card,
    analyze_sales,
    capabilities,
    export_summary,
    export_table,
    get_record,
    query_records,
    report_catalog,
    run_report,
    schema,
    search_records,
)
from retrieval_store import document, search
from runtime_config import env_bool, env_value, load_project_env


def authorized(headers, token):
    """Require one exact bearer token without logging or returning it."""
    value = headers.get("Authorization", "") if isinstance(headers, dict) else ""
    return bool(token) and hmac.compare_digest(value, f"Bearer {token}")


def route(path, headers, database, token):
    """Route the closed-world API without allowing writes or arbitrary database access."""
    if not authorized(headers, token):
        return 401, {"error": "Unauthorized"}
    parsed = urlparse(path)
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.path == "/health":
        if query_params:
            return 400, {"error": "health endpoint accepts no query parameters"}
        return 200, {
            "status": "ok",
            "read_only": True,
            "approved_facts_only": True,
            "network_upload_permitted": False,
        }
    if parsed.path == "/v1/capabilities":
        if query_params:
            return 400, {"error": "capabilities endpoint accepts no query parameters"}
        try:
            return 200, capabilities(database)
        except ValueError as exc:
            return 400, {"error": str(exc)}
    if parsed.path == "/v1/crm/summary":
        if query_params:
            return 400, {"error": "summary endpoint accepts no query parameters"}
        try:
            return 200, export_summary(database)
        except ValueError as exc:
            return 400, {"error": str(exc)}
    if parsed.path == "/v1/crm/schema":
        unknown = set(query_params) - {"table"}
        if unknown:
            return 400, {"error": f"unsupported schema parameters: {sorted(unknown)}"}
        values = query_params.get("table", [])
        if len(values) > 1:
            return 400, {"error": "table must be supplied at most once"}
        try:
            return 200, schema(database, values[0] if values else None)
        except ValueError as exc:
            return 400, {"error": str(exc)}
    if parsed.path == "/v1/crm/search":
        query = query_params.get("query", [])
        limit = query_params.get("limit", ["20"])
        if len(query) != 1 or len(limit) != 1:
            return 400, {"error": "query and limit must be supplied once"}
        unknown = set(query_params) - {"query", "limit", "table"}
        if unknown:
            return 400, {"error": f"unsupported search parameters: {sorted(unknown)}"}
        try:
            return 200, search_records(
                database,
                query[0],
                query_params.get("table") or None,
                int(limit[0]),
            )
        except (ValueError, TypeError) as exc:
            return 400, {"error": str(exc)}
    record_prefix = "/v1/crm/record/"
    if parsed.path.startswith(record_prefix):
        table = parsed.path[len(record_prefix) :]
        keys = query_params.get("key", [])
        if not table or "/" in table:
            return 404, {"error": "Not found"}
        if set(query_params) != {"key"} or not keys:
            return 400, {"error": "one or more key parameters are required"}
        try:
            return 200, get_record(database, table, keys)
        except ValueError as exc:
            return 400, {"error": str(exc)}
    query_prefix = "/v1/crm/query/"
    if parsed.path.startswith(query_prefix):
        table = parsed.path[len(query_prefix) :]
        if not table or "/" in table:
            return 404, {"error": "Not found"}
        try:
            limit = int(query_params.get("limit", ["100"])[0])
            offset = int(query_params.get("offset", ["0"])[0])
        except (ValueError, IndexError):
            return 400, {"error": "limit and offset must be integers"}
        if (
            len(query_params.get("limit", ["100"])) != 1
            or len(query_params.get("offset", ["0"])) != 1
        ):
            return 400, {"error": "limit and offset must be supplied once"}
        select = [
            field for value in query_params.get("select", []) for field in value.split(",") if field
        ]
        sort = [
            field for value in query_params.get("sort", []) for field in value.split(",") if field
        ]
        filters = {}
        unknown = []
        for key, values in query_params.items():
            if key in {"limit", "offset", "select", "sort"}:
                continue
            match = re.fullmatch(r"filter\.([a-z_][a-z0-9_]*?)(?:__(\w+))?", key)
            if not match or len(values) != 1:
                unknown.append(key)
                continue
            field, operator = match.group(1), match.group(2) or "eq"
            value = values[0].split(",") if operator == "in" else values[0]
            if operator == "is_null":
                if value not in {"true", "false"}:
                    return 400, {"error": "is_null filter must be true or false"}
                value = value == "true"
            filters[field] = {"operator": operator, "value": value}
        if unknown:
            return 400, {"error": f"unsupported query parameters: {sorted(unknown)}"}
        try:
            return 200, query_records(database, table, filters, select, sort, limit, offset)
        except ValueError as exc:
            return 400, {"error": str(exc)}
    account_prefix = "/v1/crm/accounts/"
    if parsed.path.startswith(account_prefix):
        party_key = parsed.path[len(account_prefix) :]
        if not party_key or "/" in party_key or query_params:
            return 404, {"error": "Not found"}
        try:
            return 200, account_card(database, party_key=party_key)
        except ValueError as exc:
            return 400, {"error": str(exc)}
    if parsed.path == "/v1/crm/analysis/sales":
        group_by = [
            field
            for value in query_params.get("group_by", ["customer"])
            for field in value.split(",")
            if field
        ]
        filters = {key: values[0] for key, values in query_params.items() if key != "group_by"}
        if any(len(values) != 1 for key, values in query_params.items() if key != "group_by"):
            return 400, {"error": "sales filters must be supplied once"}
        try:
            return 200, analyze_sales(database, group_by, filters)
        except ValueError as exc:
            return 400, {"error": str(exc)}
    if parsed.path == "/v1/crm/reports":
        if query_params:
            return 400, {"error": "report catalog accepts no query parameters"}
        return 200, report_catalog()
    report_prefix = "/v1/crm/reports/"
    if parsed.path.startswith(report_prefix):
        name = parsed.path[len(report_prefix) :]
        if not name or "/" in name:
            return 404, {"error": "Not found"}
        if any(len(values) != 1 for values in query_params.values()):
            return 400, {"error": "report parameters must be supplied once"}
        try:
            return 200, run_report(
                database, name, {key: values[0] for key, values in query_params.items()}
            )
        except ValueError as exc:
            return 400, {"error": str(exc)}
    export_prefix = "/v1/crm/export/"
    if parsed.path.startswith(export_prefix):
        table = parsed.path[len(export_prefix) :]
        if not table or "/" in table:
            return 404, {"error": "Not found"}
        if any(len(values) != 1 for values in query_params.values()):
            return 400, {"error": "export parameters must be supplied once"}
        try:
            limit = int(query_params.get("limit", ["100"])[0])
            offset = int(query_params.get("offset", ["0"])[0])
        except ValueError:
            return 400, {"error": "limit and offset must be integers"}
        filters = {
            key.removeprefix("filter."): values[0]
            for key, values in query_params.items()
            if key.startswith("filter.")
        }
        unknown = set(query_params) - {"limit", "offset"} - {f"filter.{key}" for key in filters}
        if unknown or "" in filters:
            return 400, {"error": f"unsupported export parameters: {sorted(unknown)}"}
        try:
            return 200, export_table(database, table, limit, offset, filters)
        except ValueError as exc:
            return 400, {"error": str(exc)}
    if parsed.path == "/v1/documents":
        unknown = set(query_params) - {"query", "limit"}
        query_values = query_params.get("query", [])
        limit_values = query_params.get("limit", ["5"])
        if unknown or len(query_values) != 1 or len(limit_values) != 1:
            return 400, {"error": "query and limit must be supplied at most once"}
        query = query_values[0]
        raw_limit = limit_values[0]
        try:
            limit = int(raw_limit)
        except ValueError:
            return 400, {"error": "limit must be an integer"}
        if not isinstance(query, str) or not query:
            return 400, {"error": "query is required"}
        try:
            return 200, {"results": search(database, query, limit)}
        except ValueError as exc:
            return 400, {"error": str(exc)}
    prefix = "/v1/documents/"
    if parsed.path.startswith(prefix):
        identifier = parsed.path[len(prefix) :]
        if not identifier or "/" in identifier:
            return 404, {"error": "Not found"}
        if query_params:
            return 400, {"error": "document lookup accepts no query parameters"}
        try:
            result = document(database, identifier)
        except ValueError as exc:
            return 400, {"error": str(exc)}
        return (200, result) if result is not None else (404, {"error": "Not found"})
    return 404, {"error": "Not found"}


def handler(database, token):
    """Return a quiet request handler bound to one approved retrieval database."""

    class RetrievalHandler(BaseHTTPRequestHandler):
        """The request handler, closed over one server's validated configuration."""

        def do_GET(self):  # noqa: N802 - required stdlib method name.
            """Serve one authenticated read-only retrieval request."""
            status, payload = route(self.path, dict(self.headers), database, token)
            encoded = json.dumps(payload, sort_keys=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(encoded)

        def _read_only_error(self):
            self.send_error(405, "Read-only API")

        def do_POST(self):  # noqa: N802 - required stdlib method name.
            """Refused. This API serves approved facts and never writes them."""
            self._read_only_error()

        def do_PUT(self):  # noqa: N802 - required stdlib method name.
            """Refused. This API serves approved facts and never writes them."""
            self._read_only_error()

        def do_PATCH(self):  # noqa: N802 - required stdlib method name.
            """Refused. This API serves approved facts and never writes them."""
            self._read_only_error()

        def do_DELETE(self):  # noqa: N802 - required stdlib method name.
            """Refused. This API serves approved facts and never writes them."""
            self._read_only_error()

        def log_message(self, _format, *_args):
            """Silence the stdlib access log; a request line here can name a tenant."""
            return

    return RetrievalHandler


def build_server(database, host, port, certificate, private_key, token):
    """Build a TLS listener; all network serving remains an explicit main action."""
    if not database or not os.path.isfile(database):
        raise ValueError("Retrieval database is not readable")
    if (
        not certificate
        or not os.path.isfile(certificate)
        or not private_key
        or not os.path.isfile(private_key)
    ):
        raise ValueError("TLS certificate and private key must be readable files")
    if not token:
        raise ValueError("Retrieval bearer token is required")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be from 1 through 65535")
    server = ThreadingHTTPServer((host, port), handler(database, token))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, private_key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def main():
    try:
        load_project_env()
        defaults = {
            "enabled": env_bool("RETRIEVAL_HTTPS_ENABLED", False),
            "credential_env": env_value(
                "RETRIEVAL_HTTPS_CREDENTIAL_ENV", "RETRIEVAL_HTTPS_BEARER_TOKEN"
            ),
        }
    except ValueError as exc:
        sys.exit(f"Retrieval HTTPS failed: {exc}")
    parser = argparse.ArgumentParser(
        description="Serve approved retrieval facts through explicit TLS/bearer transport."
    )
    parser.add_argument("database")
    parser.add_argument(
        "--certificate", required=True, help="TLS certificate file served to clients."
    )
    parser.add_argument(
        "--private-key", required=True, help="TLS private key file. Keep it outside the repository."
    )
    parser.add_argument(
        "--bind-host",
        default="127.0.0.1",
        help="Address to bind. Defaults to loopback; widen it only with named client authorization.",
    )
    parser.add_argument("--port", type=int, default=8443, help="Port to listen on.")
    parser.add_argument("--credential-env", default=defaults["credential_env"])
    parser.add_argument("--enable", action="store_true", default=defaults["enabled"])
    apply_shared_help(parser)
    args = parser.parse_args()
    if not args.enable:
        sys.exit(
            "Retrieval HTTPS failed: disabled; set RETRIEVAL_HTTPS_ENABLED=true or pass --enable"
        )
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", args.credential_env):
        sys.exit(
            "Retrieval HTTPS failed: credential environment variable must be uppercase with underscores"
        )
    try:
        server = build_server(
            args.database,
            args.bind_host,
            args.port,
            args.certificate,
            args.private_key,
            os.environ.get(args.credential_env),
        )
    except (OSError, ValueError) as exc:
        sys.exit(f"Retrieval HTTPS failed: {exc}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
