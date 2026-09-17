#!/usr/bin/env python3
"""Build a no-secret deployment and acceptance plan from client YAML."""

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from business_analytics import semantic_model
from business_record_changes import change_schema
from cli_help import apply_shared_help
from client_platform_config import fingerprint, load, public_summary
from crm_service import export_summary


def plan(config):
    """Build the no-secret deployment and acceptance plan for a configuration and its snapshot."""
    deployment = config["deployment"]
    base = deployment["public_base_url"].rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}:
        raise ValueError("deployment.public_base_url must be an HTTPS origin without a path")
    summary = export_summary(deployment["snapshot"])
    result = {
        "schema_version": "business_platform_deployment_plan_v1",
        "configuration": public_summary(config),
        "snapshot": {
            "batch_id": summary["batch_id"],
            "source_export_sha256": summary["source_export_sha256"],
            "total_records": summary["total_records"],
            "integrity_verified": summary["snapshot_integrity_verified"],
        },
        "endpoints": {
            "mcp": f"{base}/mcp",
            "health": f"{base}/health",
            "docs": f"{base}/docs",
            "portal": f"{base}/portal" if deployment.get("intake_dir") else None,
            "ingestion_api": f"{base}/api/ingestion/{{operation}}"
            if deployment.get("intake_dir")
            else None,
            "platform_api": f"{base}/api/platform/{{operation}}",
        },
        "secret_environment_variables": sorted(
            {
                deployment["client_secret_env"],
                deployment["tls_private_key_env"],
                *(
                    [config["record_maintenance"]["authorization_secret_env"]]
                    if config["record_maintenance"]["enabled"]
                    else []
                ),
                *(
                    [config["record_maintenance"]["adapter"]["credential_env"]]
                    if config["record_maintenance"]["adapter"]["kind"] == "http_json"
                    else []
                ),
            }
        ),
        "filesystem": {
            "read_only": [deployment["snapshot"], deployment["tls_certificate"]],
            "private_writable": [
                deployment["audit_log"],
                deployment["export_dir"],
                deployment["change_dir"],
            ]
            + ([deployment["intake_dir"]] if deployment.get("intake_dir") else []),
            "target_staging": (
                [config["record_maintenance"]["adapter"]["output_directory"]]
                if config["record_maintenance"]["adapter"]["kind"] == "file"
                else []
            ),
        },
        "command": [
            "python",
            "scripts/business_platform_server.py",
            "CLIENT_CONFIG.yaml",
            "--enable",
        ],
        "scopes": [
            "crm:read",
            "crm:export",
            "platform:read",
            "analytics:read",
            "records:propose",
            "records:authorize",
            "records:apply",
        ]
        + (["ingestion:read", "ingestion:submit"] if deployment.get("intake_dir") else []),
        "object_schema_sha256": fingerprint(change_schema(config)),
        "analytics_schema_sha256": fingerprint(semantic_model(config)),
        "acceptance_checks": [
            "configuration and snapshot hashes pinned",
            "TLS chain, proxy routes, payload limits, and no-content logs verified",
            "OAuth discovery, audience, tenant, role, scope, refresh, and revocation verified",
            "cross-owner and cross-tenant requests refused",
            "all configured analytics reports reconciled to golden totals",
            "proposal, stale-preview, separation-of-duties, expiry, replay, apply, and reconcile exercised",
            "backup, restore, restart, rate/load, retention, and incident shutdown exercised",
            "selected ChatGPT and Claude desktop/mobile clients accepted with fictional data",
        ],
        "production_accepted": False,
        "client_acceptance_required": True,
    }
    result["plan_sha256"] = fingerprint(result)
    return result


def write_new(path, value):
    """Write JSON to a new owner-only file, refusing an existing path or a symlink."""
    destination = Path(path)
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(
        description="Create a no-secret deployment and acceptance plan from client YAML."
    )
    parser.add_argument("config", help="Validated client-specific platform YAML.")
    parser.add_argument(
        "--out", required=True, help="New deployment-plan JSON path; existing files are refused."
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        result = plan(load(args.config))
        write_new(args.out, result)
    except (OSError, ValueError) as exc:
        sys.exit(f"Business platform deployment plan failed: {exc}")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
