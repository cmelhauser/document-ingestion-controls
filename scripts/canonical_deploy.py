#!/usr/bin/env python3
"""Create a non-secret, reviewable deployment plan for the PostgreSQL canonical schema."""

import argparse
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help

OBJECT = re.compile(r"CREATE (?:TABLE|TYPE|VIEW) ([a-z_]+)", re.I)
TABLE_BODY = re.compile(r"CREATE TABLE\s+([a-z_]+)\s*\((.*?)\n\);", re.I | re.S)
COLUMN = re.compile(
    r"^\s{4}([a-z_]+)\s+(?:UUID|TEXT|CHAR|VARCHAR|BOOLEAN|INTEGER|BIGSERIAL|NUMERIC|DATE|TIMESTAMPTZ|JSONB|[a-z_]+_t)\b",
    re.I | re.M,
)
ENVIRONMENT = re.compile(r"[A-Z][A-Z0-9_]*")
REQUIRED_TABLES = {
    "currency",
    "unit_of_measure",
    "country",
    "charge_code",
    "document_type",
    "selling_location",
    "document",
    "party",
    "party_name_variant",
    "address",
    "contact",
    "party_role",
    "carrier",
    "item",
    "lane",
    "acknowledgement",
    "job",
    "shipment",
    "invoice_header",
    "invoice_line",
    "invoice_accessorial",
    "payment",
    "payment_application",
    "attribution",
    "handwriting_region",
    "amendment",
    "exception_event",
    "financial_event",
    "load_manifest",
}


def sha256(path):
    """Return a content fingerprint for immutable schema-release evidence."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def schema_objects(path):
    """List deployable objects and reject a noncanonical or unreadable schema."""
    text = Path(path).read_text()
    objects = sorted(set(OBJECT.findall(text)))
    missing = REQUIRED_TABLES - set(objects)
    if missing:
        raise ValueError(f"Schema is missing required canonical tables: {sorted(missing)}")
    for table, body in TABLE_BODY.findall(text):
        columns = COLUMN.findall(body)
        duplicates = sorted({column for column in columns if columns.count(column) > 1})
        if duplicates:
            raise ValueError(f"Schema table {table} has duplicate columns: {duplicates}")
    if not re.search(r"\bBEGIN\s*;", text, re.I) or not re.search(r"\bCOMMIT\s*;", text, re.I):
        raise ValueError("Schema must be wrapped in an explicit transaction")
    return objects


def deployment_plan(schema, database_url_env):
    """Describe an auditable PostgreSQL deployment without recording a credential."""
    if not ENVIRONMENT.fullmatch(database_url_env):
        raise ValueError("database URL environment variable must be uppercase with underscores")
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "dialect": "postgresql",
        "schema_path": str(Path(schema)),
        "schema_sha256": sha256(schema),
        "objects": schema_objects(schema),
        "credential_reference": database_url_env,
        "execution": "explicit_psql_transaction",
        "post_deploy_checks": ["foreign_keys", "append_only_amendments", "load_manifest"],
    }


def execute(schema, database_url_env, expected_schema_sha256=None):
    """Apply the schema only after explicit operator invocation and credentials."""
    database_url = os.environ.get(database_url_env)
    if not database_url:
        raise ValueError(f"Required credential environment variable is not set: {database_url_env}")
    if expected_schema_sha256 is not None and not hmac.compare_digest(
        sha256(schema), expected_schema_sha256
    ):
        raise ValueError("Canonical schema changed after the deployment plan was written")
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["psql", "-v", "ON_ERROR_STOP=1", "-f", str(schema)],  # noqa: S607 - psql is a documented install dependency
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PGDATABASE": database_url},
    )
    if result.returncode:
        raise ValueError(f"psql deployment failed: {result.stderr.strip() or result.returncode}")


def main():
    parser = argparse.ArgumentParser(
        description="Plan or explicitly deploy the canonical PostgreSQL schema."
    )
    parser.add_argument(
        "--schema",
        default="assets/canonical_schema.sql",
        help="Canonical PostgreSQL DDL file the plan is built from.",
    )
    parser.add_argument("--out", required=True, help="new non-secret deployment-plan JSON")
    parser.add_argument(
        "--database-url-env",
        default="CANONICAL_DATABASE_URL",
        help="Name of the environment variable holding the database URL. The value is never written to the plan.",
    )
    parser.add_argument(
        "--execute", action="store_true", help="apply with psql after writing the plan"
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        output = Path(args.out)
        if output.exists():
            raise ValueError(f"Output already exists: {output}")
        plan = deployment_plan(args.schema, args.database_url_env)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(plan, indent=2) + "\n")
        if args.execute:
            execute(args.schema, args.database_url_env, plan["schema_sha256"])
    except (OSError, ValueError) as exc:
        sys.exit(f"Canonical deployment failed: {exc}")
    print(json.dumps({"objects": len(plan["objects"]), "executed": args.execute}, sort_keys=True))


if __name__ == "__main__":
    main()
