#!/usr/bin/env python3
"""Launch the client-configured OAuth MCP/API business data platform."""

import argparse
import os
import sys

from business_platform_tools import PlatformService
from cli_help import apply_shared_help
from client_platform_config import load
from crm_export_jobs import ExportJobs
from retrieval_remote_mcp import AuditLog, OAuthIntrospector, build_server
from visual_ingestion import IngestionStore


def build_from_config(config):
    """Build the unified platform server, reading every secret from the environment."""
    deployment = config["deployment"]
    base_url = deployment["public_base_url"].rstrip("/")
    resource = f"{base_url}/mcp"
    secret = os.environ.get(deployment["client_secret_env"], "")
    private_key = os.environ.get(deployment.get("tls_private_key_env", ""), "")
    authorization_secret = os.environ.get(
        config["record_maintenance"]["authorization_secret_env"], ""
    )
    if (
        not secret
        or not private_key
        or not deployment.get("tls_certificate")
        or (config["record_maintenance"]["enabled"] and len(authorization_secret) < 32)
    ):
        raise ValueError(
            "Configured introspection, TLS, and record-authorization secrets/files are required"
        )
    introspector = OAuthIntrospector(
        deployment["introspection_endpoint"],
        deployment["client_id"],
        secret,
        resource,
        deployment["tenant_claim"],
        config["client"]["tenant_id"],
        deployment["role_claim"],
        set(deployment["allowed_roles"]),
    )
    ingestion = (
        IngestionStore(
            deployment["intake_dir"],
            config["client"]["tenant_id"],
            protected_database=deployment["snapshot"],
        )
        if deployment.get("intake_dir")
        else None
    )
    export_jobs = ExportJobs(
        deployment["snapshot"],
        deployment["export_dir"],
        base_url,
        deployment.get("export_ttl_seconds", 900),
        deployment.get("max_export_rows", 10_000),
    )
    platform = PlatformService(
        config,
        deployment["snapshot"],
        export_jobs=export_jobs,
        authorization_secret=authorization_secret,
    )
    return build_server(
        deployment["snapshot"],
        deployment["bind_host"],
        deployment["port"],
        deployment["tls_certificate"],
        private_key,
        introspector,
        export_jobs,
        resource,
        deployment["authorization_server"],
        AuditLog(deployment["audit_log"]),
        deployment.get("rate_limit_per_minute", 60),
        deployment.get("max_request_bytes", 1_000_000),
        deployment["allowed_origins"],
        ingestion=ingestion,
        platform=platform,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Launch the OAuth MCP/API platform from one validated client YAML."
    )
    parser.add_argument(
        "config",
        help="Client platform YAML path; credentials remain in named environment variables.",
    )
    parser.add_argument(
        "--enable",
        action="store_true",
        help="Explicitly enable the network listener after deployment authorization.",
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    if not args.enable:
        sys.exit("Business platform failed: disabled; pass --enable after deployment authorization")
    try:
        server = build_from_config(load(args.config))
    except (OSError, ValueError) as exc:
        sys.exit(f"Business platform failed: {exc}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
