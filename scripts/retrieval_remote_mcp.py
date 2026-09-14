#!/usr/bin/env python3
"""Serve the approved-fact CRM MCP over authenticated Streamable HTTP.

This is a stateless MCP transport with read-only canonical retrieval. It
delegates identity to an OAuth 2.1 authorization server through RFC 7662 token
introspection and binds one deployment to one approved tenant and immutable CRM
snapshot.  It never issues tokens, changes canonical facts, or writes to a CRM.
An optional separate journal accepts unapproved visual-intake proposals.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from cli_help import apply_shared_help
from crm_export_jobs import ExportJobs
from ingestion_tools import OPERATIONS as INGESTION_OPERATIONS
from ingestion_tools import invoke as invoke_ingestion
from retrieval_mcp import (
    LEGACY_PROTOCOL_VERSIONS,
    MODERN_PROTOCOL_VERSION,
    TOOLS,
    handle,
    request_protocol_version,
    response,
    tool_result,
)
from runtime_config import env_bool, load_project_env

MAX_INTROSPECTION_BYTES = 65_536
DEFAULT_MAX_REQUEST_BYTES = 1_000_000
DEFAULT_RATE_LIMIT = 60
TOKEN_PATTERN = re.compile(r"^Bearer ([^\s]+)$")
ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"
INGESTION_REVIEW_HTML = ASSETS_DIR / "ingestion_review_app.html"
INGESTION_REVIEW_JS = ASSETS_DIR / "ingestion_review_app.js"
EXPORT_TOOL_NAMES = frozenset({"export_crm_table", "create_crm_export", "get_crm_export_job"})
EXPORT_API_OPERATIONS = frozenset({"create", "status"})
REMOTE_TOOLS = (
    {
        "name": "create_crm_export",
        "title": "Create CRM Export",
        "description": (
            "Create a bounded, immutable CSV/XLSX export or ZIP package from the approved CRM snapshot "
            "and return a short-lived signed download URL."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "report": {"type": "string"},
                "package": {"type": "string", "enum": ["staging", "common_import"]},
                "format": {"type": "string", "enum": ["csv", "xlsx", "zip"], "default": "xlsx"},
                "filters": {"type": "object"},
                "parameters": {"type": "object"},
                "review_context": {
                    "type": "object",
                    "properties": {
                        "schema_version": {"type": "string", "enum": ["review_export_link_v1"]},
                        "source_kind": {"type": "string", "enum": ["visual_ingestion_session"]},
                        "session_id": {"type": "string"},
                        "source_set_sha256": {
                            "type": "string",
                            "pattern": "^[0-9a-f]{64}$",
                        },
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "required": ["result"],
            "properties": {"result": {"type": "object"}},
            "additionalProperties": False,
        },
        "annotations": {
            # The job never changes canonical/CRM facts, but it does create an
            # immutable server-side export artifact.  MCP defines readOnlyHint
            # against the complete tool environment, not only the source data.
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "get_crm_export_job",
        "title": "Get CRM Export Job",
        "description": "Get the status and checksums for an export job created by this user.",
        "inputSchema": {
            "type": "object",
            "required": ["job_id"],
            "properties": {"job_id": {"type": "string", "format": "uuid"}},
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "required": ["result"],
            "properties": {"result": {"type": "object"}},
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
)


def _split(value):
    return {item for item in re.split(r"[\s,]+", value or "") if item}


def _claim_values(value):
    if isinstance(value, str):
        return _split(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    return set()


@dataclass(frozen=True)
class Principal:
    """Validated identity and authorization claims for one MCP request."""

    subject: str
    tenant: str
    roles: frozenset[str]
    scopes: frozenset[str]

    @property
    def audit_subject(self):
        """Return the subject as an audit-safe digest.

        The audit log records who acted, never who they are: a bearer subject is
        a client identifier and belongs in the log only in a form that cannot be
        read back out of it.
        """
        return hashlib.sha256(self.subject.encode()).hexdigest()


class OAuthIntrospector:
    """Validate opaque OAuth tokens against a configured RFC 7662 endpoint."""

    def __init__(
        self,
        endpoint,
        client_id,
        client_secret,
        resource,
        tenant_claim,
        tenant,
        roles_claim,
        allowed_roles,
        timeout=10.0,
    ):
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("OAuth introspection endpoint must be an absolute HTTPS URL")
        if not all(isinstance(value, str) and value for value in (client_id, client_secret)):
            raise ValueError("OAuth introspection client ID and secret are required")
        if not resource.startswith("https://"):
            raise ValueError("MCP resource identifier must be an HTTPS URL")
        if not tenant_claim or not tenant or not roles_claim or not allowed_roles:
            raise ValueError("tenant and role authorization settings are required")
        self.endpoint = endpoint
        self.client_id = client_id
        self.client_secret = client_secret
        self.resource = resource.rstrip("/")
        self.tenant_claim = tenant_claim
        self.tenant = tenant
        self.roles_claim = roles_claim
        self.allowed_roles = frozenset(allowed_roles)
        self.timeout = timeout

    def authenticate(self, headers):
        """Introspect the request's bearer token, or refuse the request.

        Every failure raises rather than returning a falsy principal: a caller
        that forgets to check a return value must not end up serving approved
        facts to an unauthenticated client. Tenant and role are enforced here,
        not by the handler, so no route can reach data by skipping the check.
        """
        value = headers.get("Authorization", "")
        match = TOKEN_PATTERN.fullmatch(value)
        if not match:
            raise PermissionError("missing or malformed bearer token")
        token = match.group(1)
        credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        body = urllib.parse.urlencode({"token": token}).encode()
        request = urllib.request.Request(  # noqa: S310 - endpoint is validated HTTPS.
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                declared = response.headers.get("Content-Length")
                if declared:
                    try:
                        declared = int(declared)
                    except ValueError as exc:
                        raise PermissionError(
                            "OAuth introspection returned an invalid content length"
                        ) from exc
                    if declared > MAX_INTROSPECTION_BYTES:
                        raise PermissionError("OAuth introspection response is too large")
                raw = response.read(MAX_INTROSPECTION_BYTES + 1)
        except PermissionError:
            raise
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise PermissionError("OAuth token introspection failed") from exc
        if len(raw) > MAX_INTROSPECTION_BYTES:
            raise PermissionError("OAuth introspection response is too large")
        try:
            claims = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PermissionError("OAuth introspection returned invalid JSON") from exc
        if not isinstance(claims, dict) or claims.get("active") is not True:
            raise PermissionError("OAuth access token is inactive")
        expires = claims.get("exp")
        if (
            isinstance(expires, bool)
            or not isinstance(expires, (int, float))
            or expires <= time.time()
        ):
            raise PermissionError("OAuth access token is expired or lacks a valid expiry")
        audience = _claim_values(claims.get("aud"))
        if self.resource not in audience:
            raise PermissionError("OAuth access token audience does not match this MCP resource")
        subject = claims.get("sub")
        token_tenant = claims.get(self.tenant_claim)
        roles = _claim_values(claims.get(self.roles_claim))
        scopes = _claim_values(claims.get("scope"))
        if not isinstance(subject, str) or not subject:
            raise PermissionError("OAuth access token lacks a subject")
        if not isinstance(token_tenant, str) or not hmac.compare_digest(token_tenant, self.tenant):
            raise PermissionError("OAuth access token tenant is not authorized")
        if not roles.intersection(self.allowed_roles):
            raise PermissionError("OAuth access token role is not authorized")
        return Principal(subject, token_tenant, frozenset(roles), frozenset(scopes))


class RateLimiter:
    """Bound requests per client address over a rolling minute."""

    def __init__(self, limit):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("rate limit must be a positive integer")
        self.limit = limit
        self._events = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key, now=None):
        """Return whether this key may make another request in the trailing minute.

        `now` is injectable so the window can be tested without sleeping. The
        clock is monotonic: a wall-clock adjustment must not hand a caller a
        fresh allowance.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            events = self._events[key]
            while events and events[0] <= now - 60:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True


class AuditLog:
    """Append content-free security events to an operator-selected log."""

    def __init__(self, path):
        self.path = Path(path)
        if self.path.exists() and not self.path.is_file():
            raise ValueError("audit log path must be a file")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, event):
        """Append one content-free security event and flush it to disk.

        Flushed and fsynced under the lock, because an audit record that is
        still in a buffer when the process dies did not record anything. Nothing
        client-identifying or content-bearing may be placed in `event`.
        """
        encoded = json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
        with self._lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())


def _asset_bytes(path):
    if not path.is_file():
        raise ValueError(f"Missing required asset: {path.name}")
    return path.read_bytes()


def required_scope(request):
    """Return the OAuth scope this JSON-RPC request needs.

    Unknown and malformed requests fall through to the narrowest scope rather
    than the broadest: a request this function does not recognize must not be
    the one that gets export rights.
    """
    params = request.get("params") if isinstance(request, dict) else None
    name = params.get("name") if isinstance(params, dict) else None
    if isinstance(name, str) and name in INGESTION_OPERATIONS:
        return INGESTION_OPERATIONS[name][2]
    return "crm:export" if isinstance(name, str) and name in EXPORT_TOOL_NAMES else "crm:read"


def protected_resource_metadata(resource, authorization_server, ingestion=False):
    """Return the RFC 9728 protected-resource document this server publishes.

    The ingestion scopes appear only when an ingestion journal is actually
    mounted. Advertising a scope no route serves invites a client to request
    rights this deployment cannot honour.
    """
    parsed = urlparse(resource)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return {
        "resource": resource.rstrip("/"),
        "authorization_servers": [authorization_server.rstrip("/")],
        "scopes_supported": ["crm:read", "crm:export"]
        + (["ingestion:read", "ingestion:submit"] if ingestion else []),
        "resource_name": "Approved CRM Retrieval MCP",
        "resource_documentation": f"{origin}/docs",
    }


def _origin_allowed(origin, allowed_origins):
    return not origin or origin in allowed_origins


def _validate_modern_headers(headers, request):
    """Require 2026 routing/version headers to agree with the JSON-RPC body."""
    if headers.get("MCP-Protocol-Version") != MODERN_PROTOCOL_VERSION:
        return
    if request_protocol_version(request) != MODERN_PROTOCOL_VERSION:
        raise ValueError("Modern requests must carry the matching protocol version in params._meta")
    method = request.get("method") if isinstance(request, dict) else None
    if not isinstance(method, str) or headers.get("Mcp-Method") != method:
        raise ValueError("Mcp-Method header must match the JSON-RPC method")
    if method == "tools/call":
        params = request.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        if not isinstance(name, str) or headers.get("Mcp-Name") != name:
            raise ValueError("Mcp-Name header must match the requested tool")


def handler(
    database,
    introspector,
    export_jobs,
    resource,
    authorization_server,
    audit_log,
    rate_limiter,
    max_request_bytes,
    allowed_origins,
    ingestion=None,
):
    """Build one quiet stateless Streamable HTTP request handler."""
    parsed_resource = urlparse(resource)
    resource_origin = f"{parsed_resource.scheme}://{parsed_resource.netloc}"
    mcp_path = parsed_resource.path
    metadata_path = f"/.well-known/oauth-protected-resource{mcp_path}"
    metadata_url = f"{resource_origin}{metadata_path}"

    class RemoteMCPHandler(BaseHTTPRequestHandler):
        """The request handler, closed over one server's validated configuration.

        Built per server rather than configured per request so that no route can
        be reached with settings that were never validated.
        """

        def _json(self, status, payload, extra_headers=None):
            encoded = json.dumps(payload, sort_keys=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(encoded)

        def _challenge(self, message, status=401, scope="crm:read"):
            self._json(
                status,
                {"error": message},
                {"WWW-Authenticate": f'Bearer resource_metadata="{metadata_url}", scope="{scope}"'},
            )

        def _asset(self, media_type, content):
            self.send_response(200)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

        def _download(self, path, query):
            job_id = path.removeprefix("/downloads/")
            token = urllib.parse.parse_qs(query).get("token", [None])[0]
            started = time.monotonic()
            success = False
            try:
                artifact, manifest = export_jobs.download(job_id, token)
            except PermissionError as exc:
                self._json(403, {"error": str(exc)})
            except ValueError as exc:
                self._json(404, {"error": str(exc)})
            else:
                media_type = (
                    "text/csv; charset=utf-8"
                    if manifest["format"] == "csv"
                    else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                size = artifact.stat().st_size
                self.send_response(200)
                self.send_header("Content-Type", media_type)
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition", f'attachment; filename="{artifact.name}"')
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-SHA256", manifest["file_sha256"])
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                with artifact.open("rb") as stream:
                    while chunk := stream.read(65_536):
                        self.wfile.write(chunk)
                success = True
            finally:
                audit_log.write(
                    {
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "method": "downloads/get",
                        "job_id": job_id,
                        "success": success,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    }
                )

        def do_GET(self):  # noqa: N802
            """Serve only the discovery documents. No approved fact is readable here."""
            parsed = urlparse(self.path)
            path = parsed.path
            if ingestion is not None and path == "/ingestion/review":
                self._asset("text/html; charset=utf-8", _asset_bytes(INGESTION_REVIEW_HTML))
                return
            if ingestion is not None and path == "/ingestion/review.js":
                self._asset("text/javascript; charset=utf-8", _asset_bytes(INGESTION_REVIEW_JS))
                return
            if path in {"/.well-known/oauth-protected-resource", metadata_path}:
                self._json(
                    200,
                    protected_resource_metadata(
                        resource, authorization_server, ingestion is not None
                    ),
                )
                return
            if path == "/health":
                self._json(
                    200,
                    {
                        "status": "ok",
                        "transport": "streamable_http",
                        "read_only": ingestion is None,
                        "canonical_read_only": True,
                    },
                )
                return
            if path == "/docs":
                self._json(
                    200,
                    {
                        "name": "Approved CRM Retrieval MCP",
                        "endpoint": resource,
                        "transport": "streamable_http",
                        "authorization": "OAuth 2.1 bearer token",
                        "read_only": ingestion is None,
                        "canonical_read_only": True,
                        "ingestion_review_ui": "/ingestion/review"
                        if ingestion is not None
                        else None,
                        "crm_export_api": "/api/crm-export/{operation}",
                        "crm_package_kinds": ["staging", "common_import"],
                        "ingestion_api": "/api/ingestion/{operation}"
                        if ingestion is not None
                        else None,
                        "scopes": protected_resource_metadata(
                            resource, authorization_server, ingestion is not None
                        )["scopes_supported"],
                    },
                )
                return
            if path.startswith("/downloads/"):
                if not rate_limiter.allow(self.client_address[0]):
                    self._json(429, {"error": "Rate limit exceeded"}, {"Retry-After": "60"})
                    return
                self._download(path, parsed.query)
                return
            if path == mcp_path:
                if not _origin_allowed(self.headers.get("Origin"), allowed_origins):
                    self._json(403, {"error": "Origin is not allowed"})
                    return
                self._json(405, {"error": "Stateless MCP does not expose an SSE GET stream"})
                return
            self._json(404, {"error": "Not found"})

        def do_POST(self):  # noqa: N802
            """Serve one authenticated JSON-RPC or ingestion API call.

            Authentication, tenant, role, scope, origin, and rate limit are all
            settled before the request body reaches any handler.
            """
            started = time.monotonic()
            path = urlparse(self.path).path
            api_operation = path.removeprefix("/api/ingestion/")
            export_api_operation = path.removeprefix("/api/crm-export/")
            is_ingestion_api = (
                ingestion is not None
                and path.startswith("/api/ingestion/")
                and api_operation in INGESTION_OPERATIONS
            )
            is_export_api = (
                path.startswith("/api/crm-export/")
                and export_api_operation in EXPORT_API_OPERATIONS
            )
            if path != mcp_path and not is_ingestion_api and not is_export_api:
                self._json(404, {"error": "Not found"})
                return
            if not _origin_allowed(self.headers.get("Origin"), allowed_origins):
                self._json(403, {"error": "Origin is not allowed"})
                return
            client = self.client_address[0]
            if not rate_limiter.allow(client):
                self._json(429, {"error": "Rate limit exceeded"}, {"Retry-After": "60"})
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._json(411, {"error": "A valid Content-Length is required"})
                return
            if length < 1 or length > max_request_bytes:
                self._json(413, {"error": "MCP request exceeds the configured payload limit"})
                return
            if self.headers.get_content_type() != "application/json":
                self._json(415, {"error": "MCP requests must use application/json"})
                return
            protocol_version = self.headers.get("MCP-Protocol-Version")
            if protocol_version and protocol_version not in {
                MODERN_PROTOCOL_VERSION,
                *LEGACY_PROTOCOL_VERSIONS,
            }:
                self._json(400, {"error": "Unsupported MCP-Protocol-Version"})
                return
            try:
                principal = introspector.authenticate(dict(self.headers))
            except PermissionError as exc:
                self._challenge(str(exc))
                return
            try:
                request = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
                self._json(
                    400,
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Invalid JSON"},
                    },
                )
                return
            if is_ingestion_api:
                request = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": api_operation, "arguments": request},
                }
            elif is_export_api:
                request = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "create_crm_export"
                        if export_api_operation == "create"
                        else "get_crm_export_job",
                        "arguments": request,
                    },
                }
            try:
                if not is_ingestion_api and not is_export_api:
                    _validate_modern_headers(self.headers, request)
            except ValueError as exc:
                request_id = request.get("id") if isinstance(request, dict) else None
                self._json(400, response(request_id, error=str(exc), code=-32600))
                return
            scope = required_scope(request)
            if (
                ingestion is not None
                and isinstance(request, dict)
                and request.get("method")
                in ("tools/list", "initialize", "server/discover", "notifications/initialized")
                and "crm:read" not in principal.scopes
            ):
                scope = "ingestion:read"
            if scope not in principal.scopes:
                self._challenge(
                    f"OAuth access token lacks required scope {scope}",
                    status=403,
                    scope=scope,
                )
                return
            method = request.get("method") if isinstance(request, dict) else None
            tool = None
            if isinstance(request, dict) and isinstance(request.get("params"), dict):
                tool = request["params"].get("name")
            try:
                if is_ingestion_api:
                    value = invoke_ingestion(
                        ingestion, principal.subject, api_operation, request["params"]["arguments"]
                    )
                    result = response(1, tool_result(value))
                elif method == "tools/list":
                    result = handle(request, database, ingestion=ingestion, owner=principal.subject)
                    result["result"]["tools"] = [
                        *result["result"]["tools"],
                        *REMOTE_TOOLS,
                    ]
                    if ingestion is not None:
                        result["result"]["tools"] = [
                            item
                            for item in result["result"]["tools"]
                            if required_scope({"params": {"name": item["name"]}})
                            in principal.scopes
                        ]
                elif method == "tools/call" and tool in EXPORT_TOOL_NAMES - {"export_crm_table"}:
                    arguments = request["params"].get("arguments", {})
                    if not isinstance(arguments, dict):
                        raise ValueError("arguments must be an object")
                    if tool == "create_crm_export":
                        unknown = set(arguments) - {
                            "table",
                            "report",
                            "package",
                            "format",
                            "filters",
                            "parameters",
                            "review_context",
                        }
                        if unknown:
                            raise ValueError(f"Unsupported arguments: {sorted(unknown)}")
                        review_context = arguments.get("review_context")
                        if review_context is not None and ingestion is not None:
                            status = ingestion.status(
                                principal.subject, review_context["session_id"]
                            )
                            if status["source_set_sha256"] != review_context["source_set_sha256"]:
                                raise ValueError(
                                    "review context does not match the current visual intake session"
                                )
                        value = export_jobs.create(principal.subject, **arguments)
                    else:
                        if set(arguments) != {"job_id"}:
                            raise ValueError("get_crm_export_job requires only job_id")
                        value = export_jobs.status(principal.subject, arguments["job_id"])
                    result = response(
                        request.get("id"),
                        tool_result(value),
                        modern=request_protocol_version(request) == MODERN_PROTOCOL_VERSION,
                    )
                else:
                    result = handle(request, database, ingestion=ingestion, owner=principal.subject)
            except (KeyError, TypeError, ValueError) as exc:
                result = response(request.get("id"), error=str(exc), code=-32602)
            audit_log.write(
                {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "subject_sha256": principal.audit_subject,
                    "tenant": principal.tenant,
                    "method": method
                    if method
                    in (
                        "tools/list",
                        "tools/call",
                        "initialize",
                        "server/discover",
                        "notifications/initialized",
                        "ping",
                    )
                    else "unknown",
                    "tool": tool
                    if tool
                    in [item["name"] for item in (*TOOLS, *REMOTE_TOOLS)]
                    + list(INGESTION_OPERATIONS)
                    else None,
                    "success": not (isinstance(result, dict) and "error" in result),
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
            )
            if result is None:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if is_ingestion_api or is_export_api:
                if "error" in result:
                    self._json(400, {"error": result["error"]["message"]})
                else:
                    self._json(200, result["result"]["structuredContent"]["result"])
                return
            self._json(
                200,
                result,
                {
                    "MCP-Protocol-Version": self.headers.get(
                        "MCP-Protocol-Version", LEGACY_PROTOCOL_VERSIONS[-1]
                    )
                },
            )

        def do_DELETE(self):  # noqa: N802
            """Refuse every mutating verb. Sessions are stateless and nothing here writes."""
            self._json(405, {"error": "Stateless MCP sessions cannot be deleted"})

        do_PUT = do_DELETE
        do_PATCH = do_DELETE

        def log_message(self, _format, *_args):
            """Silence the stdlib access log.

            It writes the request line to stderr, and a request line on this
            server can carry a tenant identifier. Security events go to the
            audit log, in the content-free form it enforces.
            """
            return

    return RemoteMCPHandler


def build_server(
    database,
    host,
    port,
    certificate,
    private_key,
    introspector,
    export_jobs,
    resource,
    authorization_server,
    audit_log,
    rate_limit=DEFAULT_RATE_LIMIT,
    max_request_bytes=DEFAULT_MAX_REQUEST_BYTES,
    allowed_origins=frozenset(),
    ingestion=None,
):
    """Build the configured HTTPS server, validating every setting before it serves.

    Everything is settled here rather than per request: a handler closed over a
    validated configuration cannot be reached with settings that were never
    checked. Starting a listener is a deployment boundary -- see the MCP/API
    operations skill for the activation gates that precede it.
    """
    if not Path(database).is_file():
        raise ValueError("Retrieval database is not readable")
    if not Path(certificate).is_file() or not Path(private_key).is_file():
        raise ValueError("TLS certificate and private key must be readable files")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be from 1 through 65535")
    if (
        isinstance(max_request_bytes, bool)
        or not isinstance(max_request_bytes, int)
        or max_request_bytes < 1
    ):
        raise ValueError("maximum request bytes must be a positive integer")
    normalized_resource = resource.rstrip("/")
    parsed_resource = urlparse(normalized_resource)
    if (
        parsed_resource.scheme != "https"
        or not parsed_resource.netloc
        or parsed_resource.path != "/mcp"
        or parsed_resource.params
        or parsed_resource.query
        or parsed_resource.fragment
        or parsed_resource.username
        or parsed_resource.password
    ):
        raise ValueError("MCP resource must be an absolute HTTPS URL ending exactly in /mcp")
    resource_origin = f"{parsed_resource.scheme}://{parsed_resource.netloc}"
    parsed_authorization = urlparse(authorization_server)
    if parsed_authorization.scheme != "https" or not parsed_authorization.netloc:
        raise ValueError("OAuth authorization server must be an absolute HTTPS URL")
    if introspector.resource != normalized_resource:
        raise ValueError("OAuth introspector resource does not match the MCP resource")
    if export_jobs.base_url != resource_origin:
        raise ValueError("download base URL must match the MCP resource origin")
    if ingestion is not None and ingestion.tenant != introspector.tenant:
        raise ValueError("Intake tenant must match the authenticated deployment tenant")
    server = ThreadingHTTPServer(
        (host, port),
        handler(
            database,
            introspector,
            export_jobs,
            resource,
            authorization_server,
            audit_log,
            RateLimiter(rate_limit),
            max_request_bytes,
            frozenset(allowed_origins),
            ingestion=ingestion,
        ),
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, private_key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def main():
    load_project_env()
    parser = argparse.ArgumentParser(
        description="Serve approved CRM facts over OAuth-protected Streamable HTTP MCP."
    )
    parser.add_argument("database", help="Immutable approved-fact SQLite snapshot to serve.")
    parser.add_argument(
        "--ingestion-dir",
        help="Opt in to visual intake MCP tools and /api/ingestion/{operation}; private journal directory permanently bound to --tenant. Never permits canonical writes.",
    )
    parser.add_argument("--certificate", required=True, help="TLS certificate file.")
    parser.add_argument(
        "--private-key", required=True, help="TLS private key file outside the repository."
    )
    parser.add_argument(
        "--bind-host",
        default="127.0.0.1",
        help="Listener address; widen only after deployment approval.",
    )
    parser.add_argument("--port", type=int, default=9443, help="TLS listener port.")
    parser.add_argument(
        "--resource",
        required=True,
        help="Exact canonical public HTTPS resource URL ending in /mcp.",
    )
    parser.add_argument(
        "--authorization-server", required=True, help="OAuth authorization-server HTTPS issuer URL."
    )
    parser.add_argument(
        "--introspection-endpoint",
        required=True,
        help="RFC 7662 HTTPS token-introspection endpoint.",
    )
    parser.add_argument(
        "--client-id", required=True, help="OAuth introspection client identifier; not a secret."
    )
    parser.add_argument(
        "--client-secret-env",
        default="RETRIEVAL_REMOTE_MCP_CLIENT_SECRET",
        help="Environment variable containing the introspection client secret.",
    )
    parser.add_argument(
        "--tenant",
        required=True,
        help="Exact tenant identifier this immutable snapshot belongs to.",
    )
    parser.add_argument(
        "--tenant-claim",
        default="tenant_id",
        help="Introspection claim carrying the tenant identifier.",
    )
    parser.add_argument(
        "--roles-claim", default="roles", help="Introspection claim carrying user roles."
    )
    parser.add_argument(
        "--allow-role",
        action="append",
        required=True,
        help="Authorized role; repeat to allow multiple roles.",
    )
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        help="Allowed HTTP Origin; repeat as needed. Requests without Origin remain supported.",
    )
    parser.add_argument(
        "--audit-log",
        required=True,
        help="Append-only content-free audit-event file outside version control.",
    )
    parser.add_argument(
        "--export-dir",
        required=True,
        help="Dedicated directory for immutable short-lived CRM export jobs.",
    )
    parser.add_argument(
        "--download-base-url",
        required=True,
        help="Public HTTPS base URL used to construct signed export download URLs.",
    )
    parser.add_argument(
        "--export-ttl-seconds",
        type=int,
        default=900,
        help="Signed export download lifetime from 60 through 3600 seconds.",
    )
    parser.add_argument(
        "--max-export-rows",
        type=int,
        default=10_000,
        help="Maximum rows allowed in one CSV or XLSX export job.",
    )
    parser.add_argument(
        "--canonical-export",
        help="Canonical export JSON used to build ZIP staging or common-import packages.",
    )
    parser.add_argument(
        "--canonical-load-plan",
        help="Canonical load plan JSON paired with --canonical-export for package jobs.",
    )
    parser.add_argument(
        "--rate-limit",
        type=int,
        default=DEFAULT_RATE_LIMIT,
        help="Maximum requests per client address per rolling minute.",
    )
    parser.add_argument(
        "--max-request-bytes",
        type=int,
        default=DEFAULT_MAX_REQUEST_BYTES,
        help="Maximum JSON-RPC or ingestion JSON API request bytes (default: 1000000). Set at least 14000000 for a full 10 MB base64 page plus envelope; align proxy and client limits.",
    )
    parser.add_argument(
        "--enable",
        action="store_true",
        default=env_bool("RETRIEVAL_REMOTE_MCP_ENABLED", False),
        help="Explicitly enable the remote listener.",
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    if not args.enable:
        sys.exit(
            "Remote retrieval MCP failed: disabled; set RETRIEVAL_REMOTE_MCP_ENABLED=true or pass --enable"
        )
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", args.client_secret_env):
        sys.exit(
            "Remote retrieval MCP failed: client secret environment variable must be uppercase with underscores"
        )
    try:
        introspector = OAuthIntrospector(
            args.introspection_endpoint,
            args.client_id,
            os.environ.get(args.client_secret_env, ""),
            args.resource,
            args.tenant_claim,
            args.tenant,
            args.roles_claim,
            set(args.allow_role),
        )
        ingestion = None
        if args.ingestion_dir:
            from visual_ingestion import IngestionStore

            ingestion = IngestionStore(
                args.ingestion_dir, args.tenant, protected_database=args.database
            )
        server = build_server(
            args.database,
            args.bind_host,
            args.port,
            args.certificate,
            args.private_key,
            introspector,
            ExportJobs(
                args.database,
                args.export_dir,
                args.download_base_url,
                args.export_ttl_seconds,
                args.max_export_rows,
                canonical_export=args.canonical_export,
                canonical_load_plan=args.canonical_load_plan,
            ),
            args.resource,
            args.authorization_server,
            AuditLog(args.audit_log),
            args.rate_limit,
            args.max_request_bytes,
            args.allow_origin,
            ingestion=ingestion,
        )
    except (OSError, ValueError) as exc:
        sys.exit(f"Remote retrieval MCP failed: {exc}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
