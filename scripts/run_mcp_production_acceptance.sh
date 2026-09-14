#!/usr/bin/env bash
# Exercise the complete approved-fact CRM/MCP query path with fictional data.
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: bash scripts/run_mcp_production_acceptance.sh [OUTPUT_DIRECTORY]

Exercise the complete local/remote approved-fact CRM MCP path with the tracked
fictional fixture and write hash-bound production-test evidence.

Arguments:
  OUTPUT_DIRECTORY  New directory for the load plan, staging, retrieval database,
                    exports, audit log, and mcp_production_acceptance_summary.json.
                    Defaults to output/mcp_production_acceptance in the repository.

Environment:
  PYTHON_BIN  Python executable; defaults to .venv/bin/python.

The command refuses an existing output directory. A pass establishes repository
code readiness only; deployed TLS, OAuth, workspace, operations, and the exact
approved client snapshot still require separate production acceptance.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
if (( $# > 1 )); then
  usage >&2
  exit 2
fi

output_dir="${1:-$root_dir/output/mcp_production_acceptance}"
python_bin="${PYTHON_BIN:-$root_dir/.venv/bin/python}"
fixture="$root_dir/fixtures/mcp_production_acceptance/canonical_export.json"

if [[ ! -x "$python_bin" ]]; then
  echo "Python runtime not found: $python_bin" >&2
  exit 1
fi
if [[ -e "$output_dir" ]]; then
  echo "Refusing to overwrite existing output: $output_dir" >&2
  exit 1
fi

mkdir -p "$output_dir"
"$python_bin" "$root_dir/scripts/canonical_load.py" "$fixture" \
  --out "$output_dir/canonical_load_plan.json" >/dev/null
"$python_bin" "$root_dir/scripts/csv_api_staging.py" \
  "$fixture" "$output_dir/canonical_load_plan.json" \
  --out "$output_dir/crm_staging" >/dev/null
"$python_bin" "$root_dir/scripts/retrieval_store.py" build "$fixture" \
  --out "$output_dir/business_retrieval.sqlite" >/dev/null

ROOT_DIR="$root_dir" OUTPUT_DIR="$output_dir" PYTHON_BIN="$python_bin" \
  "$python_bin" - <<'PY'
import hashlib
import http.client
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse

root = Path(os.environ["ROOT_DIR"])
out = Path(os.environ["OUTPUT_DIR"])
python_bin = os.environ["PYTHON_BIN"]
sys.path.insert(0, str(root / "scripts"))

from crm_export_jobs import ExportJobs  # noqa: E402
from retrieval_remote_mcp import (  # noqa: E402
    AuditLog,
    MODERN_PROTOCOL_VERSION,
    Principal,
    RateLimiter,
    handler,
)

database = out / "business_retrieval.sqlite"


def rpc(request_id, method, params=None):
    request = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        request["params"] = params
    return request


calls = [
    rpc(1, "initialize", {"protocolVersion": "2025-11-25"}),
    rpc(2, "tools/list"),
    rpc(3, "tools/call", {"name": "get_crm_capabilities", "arguments": {}}),
]
tool_calls = [
    ("get_crm_export_summary", {}),
    ("get_crm_schema", {"table": "party"}),
    ("search_crm_records", {"query": "Fictional Customer Incorporated"}),
    ("get_crm_record", {"table": "party_role", "key": ["customer-1", "payer"]}),
    (
        "query_crm_records",
        {
            "table": "address",
            "filters": {"state_province": "MA"},
            "select": ["party_key", "line1", "city", "postal_code"],
        },
    ),
    ("export_crm_table", {"table": "invoice_header", "limit": 1, "offset": 0}),
    ("get_account_card", {"party_key": "customer-1"}),
    (
        "analyze_sales",
        {
            "group_by": ["company", "region", "quarter"],
            "filters": {"currency": "USD"},
        },
    ),
    ("search_business_documents", {"query": "Fictional", "limit": 5}),
    ("get_business_document", {"document_id": "doc-1"}),
]
reports = [
    "account_directory",
    "invoice_register",
    "receivables_aging",
    "sales_by_customer",
    "sales_by_selling_location",
    "attribution_coverage",
    "shipment_performance",
]
for name, arguments in tool_calls:
    calls.append(rpc(len(calls) + 1, "tools/call", {"name": name, "arguments": arguments}))
for report in reports:
    parameters = {"as_of": "2026-03-31"} if report == "receivables_aging" else {}
    calls.append(
        rpc(
            len(calls) + 1,
            "tools/call",
            {"name": "run_crm_report", "arguments": {"report": report, "parameters": parameters}},
        )
    )

completed = subprocess.run(
    ["bash", str(root / "scripts/run_retrieval_mcp.sh"), str(database)],
    input="".join(json.dumps(call) + "\n" for call in calls),
    text=True,
    capture_output=True,
    timeout=30,
    env={**os.environ, "PYTHON_BIN": python_bin},
    check=False,
)
if completed.returncode != 0 or completed.stderr:
    raise SystemExit(
        f"Local MCP failed: returncode={completed.returncode}, stderr={completed.stderr!r}"
    )
responses = [json.loads(line) for line in completed.stdout.splitlines()]
if len(responses) != len(calls) or any("error" in item for item in responses):
    raise SystemExit("Local MCP did not return one successful response per acceptance request")
listed = responses[1]["result"]["tools"]
if len(listed) != 12 or not all(tool["annotations"]["readOnlyHint"] for tool in listed):
    raise SystemExit("Local MCP tool discovery is not the expected twelve-tool read-only surface")


def result(index):
    return responses[index]["result"]["structuredContent"]["result"]


capabilities = result(2)
summary = result(3)
schema = result(4)
search = result(5)
record = result(6)
query = result(7)
table_page = result(8)
card = result(9)
sales = result(10)
document_search = result(11)
document = result(12)
report_results = [result(index) for index in range(13, 20)]

assert capabilities["read_only"] and not capabilities["network_upload_permitted"]
assert summary["batch_id"] == "mcp-production-acceptance" and not summary["empty"]
assert schema["tables"][0]["table"] == "party"
assert search["results"][0]["table"] == "party_name_variant"
assert record["record"]["role"] == "payer"
assert query["rows"] == [
    {
        "address_key": "address-1",
        "party_key": "customer-1",
        "line1": "1 Main Street",
        "city": "Boston",
        "postal_code": "02108",
    }
]
assert table_page["rows"][0]["invoice_number"] == "FICTIONAL-INV-1"
assert card["contacts"][0]["name"] == "Fictional Accounts Payable"
assert sales["rows"][0]["currency"] == "USD"
assert document_search[0]["document_id"] == "doc-1"
assert document["citation"]["source_hash"] == "a" * 64
assert [item["report"] for item in report_results] == reports
assert all(len(item["report_sha256"]) == 64 for item in report_results)

jobs = ExportJobs(database, out / "direct_exports", "https://crm.example.test")
direct_csv = jobs.create("direct-owner", table="party", format="csv")
direct_xlsx = jobs.create("direct-owner", report="account_directory", format="xlsx")
for created in (direct_csv, direct_xlsx):
    token = created["download_url"].split("token=", 1)[1]
    artifact, manifest = jobs.download(created["job_id"], token)
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == manifest["file_sha256"]
try:
    jobs.status("different-owner", direct_csv["job_id"])
except ValueError:
    pass
else:
    raise SystemExit("Direct export owner isolation failed")


class AcceptanceIntrospector:
    def authenticate(self, headers):
        value = headers.get("Authorization", "")
        if not value.startswith("Bearer owner-"):
            raise PermissionError("missing or malformed bearer token")
        subject = value.removeprefix("Bearer ")
        return Principal(
            subject,
            "acceptance-tenant",
            frozenset({"crm_reader"}),
            frozenset({"crm:read", "crm:export"}),
        )


resource = "https://crm.example.test/mcp"
remote_jobs = ExportJobs(database, out / "remote_exports", "https://crm.example.test")
audit = AuditLog(out / "remote_audit.jsonl")
request_handler = handler(
    database,
    AcceptanceIntrospector(),
    remote_jobs,
    resource,
    "https://identity.example.test",
    audit,
    RateLimiter(100),
    100_000,
    frozenset({"https://chat.example.test"}),
)
from http.server import ThreadingHTTPServer  # noqa: E402

server = ThreadingHTTPServer(("127.0.0.1", 0), request_handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()


def remote_request(method, path, body=None, owner="owner-a", extra=None):
    connection = http.client.HTTPConnection(*server.server_address, timeout=10)
    headers = {
        "Authorization": f"Bearer {owner}",
        "Content-Type": "application/json",
        "Origin": "https://chat.example.test",
        **(extra or {}),
    }
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    response_headers = dict(response.getheaders())
    connection.close()
    content_type = response_headers.get("Content-Type", "")
    payload = json.loads(raw) if "application/json" in content_type else raw
    return response.status, response_headers, payload


try:
    metadata_status, _, metadata = remote_request(
        "GET", "/.well-known/oauth-protected-resource/mcp"
    )
    assert metadata_status == 200 and metadata["resource"] == resource
    remote_list_request = rpc(100, "tools/list")
    status, _, remote_list = remote_request(
        "POST", "/mcp", json.dumps(remote_list_request)
    )
    assert status == 200 and len(remote_list["result"]["tools"]) == 14
    create_tool = next(
        tool for tool in remote_list["result"]["tools"] if tool["name"] == "create_crm_export"
    )
    assert create_tool["annotations"]["readOnlyHint"] is False
    modern_request = rpc(
        101,
        "tools/list",
        {"_meta": {"io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION}},
    )
    modern_status, modern_headers, modern = remote_request(
        "POST",
        "/mcp",
        json.dumps(modern_request),
        extra={"MCP-Protocol-Version": MODERN_PROTOCOL_VERSION, "Mcp-Method": "tools/list"},
    )
    assert modern_status == 200
    assert modern_headers["MCP-Protocol-Version"] == MODERN_PROTOCOL_VERSION
    assert modern["result"]["resultType"] == "complete"
    create_request = rpc(
        102,
        "tools/call",
        {"name": "create_crm_export", "arguments": {"report": "account_directory", "format": "xlsx"}},
    )
    _, _, created_response = remote_request("POST", "/mcp", json.dumps(create_request))
    remote_created = created_response["result"]["structuredContent"]["result"]
    status_request = rpc(
        103,
        "tools/call",
        {"name": "get_crm_export_job", "arguments": {"job_id": remote_created["job_id"]}},
    )
    _, _, owner_status = remote_request("POST", "/mcp", json.dumps(status_request))
    assert owner_status["result"]["structuredContent"]["result"]["status"] == "completed"
    _, _, other_owner_status = remote_request(
        "POST", "/mcp", json.dumps(status_request), owner="owner-b"
    )
    assert other_owner_status["error"]["code"] == -32602
    assert isinstance(other_owner_status["error"]["message"], str)
    download_path = urlparse(remote_created["download_url"])
    download_status, download_headers, download = remote_request(
        "GET", f"{download_path.path}?{download_path.query}"
    )
    assert download_status == 200
    assert hashlib.sha256(download).hexdigest() == download_headers["X-Content-SHA256"]
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=10)

audit_events = [json.loads(line) for line in audit.path.read_text().splitlines()]
if not audit_events or any("owner-a" in json.dumps(event) for event in audit_events):
    raise SystemExit("Remote MCP audit log is missing or contains a raw subject")


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


acceptance = {
    "schema_version": "mcp_production_acceptance_v1",
    "fixture": "SAMPLE - FICTIONAL - NOT CLIENT DATA",
    "source_export_sha256": summary["source_export_sha256"],
    "gates": {
        "canonical_load": "passed",
        "no_send_staging": "passed",
        "retrieval_snapshot": "passed",
        "local_stdio_mcp": "passed",
        "remote_streamable_http_mcp": "passed",
        "crm_csv_xlsx_exports": "passed",
    },
    "coverage": {
        "local_tools": len(listed),
        "remote_tools": len(remote_list["result"]["tools"]),
        "standard_reports": len(report_results),
        "canonical_records": summary["total_records"],
    },
    "artifacts": {
        "canonical_load_plan_sha256": file_sha256(out / "canonical_load_plan.json"),
        "retrieval_database_sha256": file_sha256(database),
        "direct_csv_sha256": direct_csv["file_sha256"],
        "direct_xlsx_sha256": direct_xlsx["file_sha256"],
        "remote_xlsx_sha256": remote_created["file_sha256"],
    },
    "external_acceptance_still_required": [
        "production DNS, reverse proxy, and trusted TLS chain",
        "real OAuth discovery, consent, token refresh, deprovisioning, tenant, role, and scope claims",
        "official MCP Inspector against the deployed URL",
        "selected Claude and/or ChatGPT workspace registration and representative prompts",
        "load, restart, backup, cleanup, alerting, and incident revoke/rebuild exercises",
        "the exact approved non-empty client snapshot after every Phase 6 activation gate passes",
    ],
    "status": "passed",
}
(out / "mcp_production_acceptance_summary.json").write_text(
    json.dumps(acceptance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print(json.dumps(acceptance, indent=2, sort_keys=True))
PY
