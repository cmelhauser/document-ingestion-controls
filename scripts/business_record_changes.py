#!/usr/bin/env python3
"""Append-only governed record changes and client-neutral target delivery."""

import argparse
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from canonical_load import LOAD_ORDER, schema_catalog, stable_checksum
from cli_help import apply_shared_help
from client_platform_config import fingerprint, load
from crm_service import get_record

VERSION = "business_record_change_v1"
STORE_VERSION = "business_record_change_journal_v1"
MAX_REQUEST_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 1_000_000


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Record change must be finite JSON") from exc


def _text(value, label, maximum=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must be non-empty text of at most {maximum} characters")
    return value


def _object(value, label, allowed, required=()):
    if not isinstance(value, dict) or not set(required) <= set(value):
        raise ValueError(f"{label} is not a complete object")
    if set(value) - set(allowed):
        raise ValueError(f"{label} contains unsupported fields")
    return value


def _private_directory(directory, label):
    root = Path(directory).absolute()
    if root.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.stat().st_mode & 0o077:
        raise ValueError(f"{label} must have private 0700 permissions")
    return root


def object_catalog(config):
    """Validate and expose only configured writable fields and target mappings."""
    canonical = schema_catalog()
    result = {}
    for name, item in config["record_maintenance"]["objects"].items():
        _text(name, "object name", 100)
        _object(
            item,
            f"object {name}",
            {
                "canonical_table",
                "external_key",
                "writable_fields",
                "target_object",
                "target_endpoint",
                "field_mapping",
            },
            {
                "canonical_table",
                "external_key",
                "writable_fields",
                "target_object",
                "target_endpoint",
                "field_mapping",
            },
        )
        table = item["canonical_table"]
        if table not in LOAD_ORDER or item["external_key"] not in canonical[table]:
            raise ValueError(f"object {name} has an unsupported canonical table or key")
        fields = item["writable_fields"]
        if (
            not isinstance(fields, list)
            or not fields
            or len(fields) > 100
            or len(set(fields)) != len(fields)
        ):
            raise ValueError(f"object {name} writable_fields must be a unique non-empty list")
        mapping = item["field_mapping"]
        if not isinstance(mapping, dict) or item["external_key"] not in mapping:
            raise ValueError(f"object {name} must map its external key")
        if set(fields) - set(canonical[table]) or set(mapping) - (
            {item["external_key"]} | set(fields)
        ):
            raise ValueError(f"object {name} names unsupported fields")
        if not all(isinstance(value, str) and value for value in mapping.values()):
            raise ValueError(f"object {name} target fields must be non-empty text")
        _text(item["target_object"], f"object {name} target_object", 200)
        endpoint = _text(item["target_endpoint"], f"object {name} target_endpoint", 1000)
        if not endpoint.startswith("/") or ".." in endpoint:
            raise ValueError(f"object {name} target_endpoint must be an absolute safe path")
        result[name] = item
    return result


def change_schema(config):
    """Describe the writable objects, fields and workflow a configuration permits."""
    catalog = object_catalog(config)
    return {
        "schema_version": VERSION,
        "configuration_sha256": fingerprint(config),
        "objects": {
            name: {
                "external_key": item["external_key"],
                "writable_fields": item["writable_fields"],
                "operations": ["create", "amend"],
            }
            for name, item in catalog.items()
        },
        "workflow": ["propose", "preview", "authorize", "apply", "reconcile"],
        "model_authorization_permitted": False,
        "hard_delete_permitted": False,
    }


def validate_request(config, request):
    """Validate one change request against the configured objects and writable fields."""
    _object(
        request,
        "change request",
        {
            "schema_version",
            "operation",
            "object",
            "record_key",
            "expected_record_sha256",
            "values",
            "clear_fields",
            "reason",
            "assertion_type",
            "source_references",
        },
        {
            "schema_version",
            "operation",
            "object",
            "record_key",
            "expected_record_sha256",
            "values",
            "clear_fields",
            "reason",
            "assertion_type",
            "source_references",
        },
    )
    if request["schema_version"] != VERSION or request["operation"] not in {"create", "amend"}:
        raise ValueError("Unsupported change schema or operation")
    objects = object_catalog(config)
    if request["object"] not in objects:
        raise ValueError("Unsupported business object")
    _text(request["record_key"], "record_key", 500)
    _text(request["reason"], "reason", 4000)
    if request["assertion_type"] not in {"source_backed", "user_assertion"}:
        raise ValueError("assertion_type must be source_backed or user_assertion")
    expected = request["expected_record_sha256"]
    if request["operation"] == "create" and expected is not None:
        raise ValueError("create must not supply expected_record_sha256")
    if request["operation"] == "amend" and (not isinstance(expected, str) or len(expected) != 64):
        raise ValueError("amend requires the exact prior record SHA-256")
    values, clear = request["values"], request["clear_fields"]
    if (
        not isinstance(values, dict)
        or len(values) > 100
        or not isinstance(clear, list)
        or len(clear) > 100
    ):
        raise ValueError("values and clear_fields exceed their object bounds")
    allowed = set(objects[request["object"]]["writable_fields"])
    named = set(values) | set(clear)
    if (not values and not clear) or (named - allowed) or (set(values) & set(clear)):
        raise ValueError("change must name distinct configured writable fields")
    if any(isinstance(value, (dict, list)) for value in values.values()) or any(
        not isinstance(field, str) for field in clear
    ):
        raise ValueError("change values must be scalars and clear fields must be strings")
    references = request["source_references"]
    if not isinstance(references, list) or len(references) > 100:
        raise ValueError("source_references must be a list of at most 100 items")
    for reference in references:
        _object(
            reference,
            "source reference",
            {"document_id", "page", "field", "evidence"},
            {"document_id", "page", "field", "evidence"},
        )
        for key, value in reference.items():
            _text(value, f"source reference {key}", 1000)
    if request["assertion_type"] == "source_backed" and not references:
        raise ValueError("source_backed changes require a source reference")
    if len(_json(request).encode()) > MAX_REQUEST_BYTES:
        raise ValueError("Record change exceeds the request byte limit")
    return request


class ChangeStore:
    """Private tenant/config-bound journal whose state is derived from immutable events."""

    def __init__(self, directory, tenant, configuration_sha256):
        self.tenant = _text(tenant, "tenant", 200)
        self.configuration_sha256 = _text(configuration_sha256, "configuration_sha256", 64)
        self.root = _private_directory(directory, "Change directory")
        self.path = self.root / "changes.sqlite"
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        if self.path.stat().st_mode & 0o077:
            raise ValueError("Change database must have private 0600 permissions")
        with closing(self._connect()) as connection, connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY, change_id TEXT NOT NULL, owner TEXT NOT NULL,
                    kind TEXT NOT NULL, request_key TEXT NOT NULL, request_hash TEXT NOT NULL,
                    payload TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL,
                    integrity TEXT NOT NULL, UNIQUE(owner, kind, request_key));
                CREATE TRIGGER IF NOT EXISTS changes_no_update BEFORE UPDATE ON events
                    BEGIN SELECT RAISE(ABORT, 'append-only journal'); END;
                CREATE TRIGGER IF NOT EXISTS changes_no_delete BEFORE DELETE ON events
                    BEGIN SELECT RAISE(ABORT, 'append-only journal'); END;
            """)
            for key, value in {
                "version": STORE_VERSION,
                "tenant": tenant,
                "configuration_sha256": configuration_sha256,
            }.items():
                connection.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (key, value))
            if dict(connection.execute("SELECT key,value FROM metadata")) != {
                "version": STORE_VERSION,
                "tenant": tenant,
                "configuration_sha256": configuration_sha256,
            }:
                raise ValueError("Change database tenant, version, or configuration mismatch")

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _read(row):
        fields = {
            key: row[key]
            for key in (
                "event_id",
                "change_id",
                "owner",
                "kind",
                "request_key",
                "request_hash",
                "payload",
                "result",
                "created_at",
            )
        }
        if stable_checksum(fields) != row["integrity"]:
            raise ValueError("Change journal integrity mismatch")
        return json.loads(row["result"])

    def _append(self, owner, kind, request_key, change_id, payload, result):
        _text(owner, "owner", 200)
        _text(request_key, "request_key", 200)
        request_hash = stable_checksum(payload)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT * FROM events WHERE owner=? AND kind=? AND request_key=?",
                (owner, kind, request_key),
            ).fetchone()
            if prior is not None:
                if prior["request_hash"] != request_hash:
                    raise ValueError("Idempotency key already names different input")
                return self._read(prior)
            event_id, created = str(uuid.uuid4()), datetime.now(UTC).isoformat()
            fields = {
                "event_id": event_id,
                "change_id": change_id,
                "owner": owner,
                "kind": kind,
                "request_key": request_key,
                "request_hash": request_hash,
                "payload": _json(payload),
                "result": _json(result),
                "created_at": created,
            }
            connection.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?)",
                (*fields.values(), stable_checksum(fields)),
            )
            return result

    def propose(self, owner, request_key, config, request):
        """Retain a validated change request as a proposal; it authorizes and writes nothing."""
        validate_request(config, request)
        change_id = str(uuid.uuid4())
        result = {
            "schema_version": VERSION,
            "change_id": change_id,
            "request_sha256": stable_checksum(request),
            "status": "proposed",
            "proposal_only": True,
            "authorization_required": True,
            "canonical_write_permitted": False,
        }
        return self._append(owner, "proposal", request_key, change_id, request, result)

    def proposal(self, owner, change_id):
        """Return one owner's retained proposal and its receipt."""
        _text(owner, "owner", 200)
        _text(change_id, "change_id", 200)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM events WHERE owner=? AND change_id=? AND kind='proposal'",
                (owner, change_id),
            ).fetchone()
            if row is None:
                raise ValueError("Record change was not found")
            return json.loads(row["payload"]), self._read(row)

    def record_lifecycle(self, owner, kind, request_key, change_id, artifact):
        """Append a signed, application, or reconciliation artifact receipt."""
        if kind not in {"authorization", "application", "reconciliation"}:
            raise ValueError("Unsupported record-change lifecycle event")
        self.proposal(owner, change_id)
        if not isinstance(artifact, dict) or artifact.get("change_id") != change_id:
            raise ValueError("Lifecycle artifact does not bind the retained change")
        result = {
            "schema_version": "business_record_change_event_receipt_v1",
            "change_id": change_id,
            "kind": kind,
            "artifact_sha256": stable_checksum(artifact),
            "status": artifact.get("status", f"{kind}_retained"),
        }
        return self._append(owner, kind, request_key, change_id, artifact, result)

    def history(self, owner, change_id):
        """Return integrity-verified event receipts for one owner-scoped change."""
        self.proposal(owner, change_id)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE owner=? AND change_id=? ORDER BY created_at,event_id",
                (owner, change_id),
            ).fetchall()
            return [self._read(row) for row in rows]

    def lifecycle_artifact(self, owner, change_id, kind, artifact_sha256):
        """Return one exact integrity-verified lifecycle artifact by checksum."""
        if kind not in {"authorization", "application", "reconciliation"}:
            raise ValueError("Unsupported record-change lifecycle event")
        self.proposal(owner, change_id)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE owner=? AND change_id=? AND kind=?",
                (owner, change_id, kind),
            ).fetchall()
            for row in rows:
                receipt = self._read(row)
                if receipt["artifact_sha256"] == artifact_sha256:
                    return json.loads(row["payload"]), receipt
        raise ValueError("Record-change lifecycle artifact was not found")

    def application_for_authorization(self, owner, change_id, authorization_sha256):
        """Return an existing application so one approval cannot be executed twice."""
        self.proposal(owner, change_id)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE owner=? AND change_id=? AND kind='application'",
                (owner, change_id),
            ).fetchall()
            for row in rows:
                receipt = self._read(row)
                artifact = json.loads(row["payload"])
                if artifact.get("authorization_sha256") == authorization_sha256:
                    return artifact, receipt
        return None

    def preview(self, owner, change_id, database, config):
        """Preview a proposal against the approved snapshot, refusing a conflicting operation."""
        request, receipt = self.proposal(owner, change_id)
        item = object_catalog(config)[request["object"]]
        before = None
        try:
            before = get_record(database, item["canonical_table"], request["record_key"])["record"]
        except ValueError as exc:
            if "not found" not in str(exc):
                raise
        if request["operation"] == "create" and before is not None:
            raise ValueError("create conflicts with an existing record")
        if request["operation"] == "amend" and before is None:
            raise ValueError("amend requires an existing record")
        before_hash = stable_checksum(before) if before is not None else None
        if request["operation"] == "amend" and before_hash != request["expected_record_sha256"]:
            raise ValueError("stale record version; create a new proposal")
        after = {} if before is None else dict(before)
        after.update(request["values"])
        for field in request["clear_fields"]:
            after[field] = None
        after[item["external_key"]] = request["record_key"]
        target = {target: after.get(source) for source, target in item["field_mapping"].items()}
        value = {
            "schema_version": "business_record_change_preview_v1",
            "change_id": change_id,
            "proposal_receipt": receipt,
            "configuration_sha256": self.configuration_sha256,
            "snapshot_record_sha256": before_hash,
            "before": before,
            "after": after,
            "target_object": item["target_object"],
            "target_endpoint": item["target_endpoint"],
            "target_payload": target,
            "downstream_requirements": [
                "target reconciliation",
                "new approved snapshot before canonical retrieval changes",
            ],
            "authorization_required": True,
        }
        value["preview_sha256"] = stable_checksum(value)
        return value


def authorize(
    preview,
    submitter,
    approver,
    expires_at,
    secret,
    *,
    separation_required=True,
    maximum_ttl_seconds=604_800,
):
    """Bind a human/operator decision to one immutable preview using HMAC."""
    _text(submitter, "submitter", 200)
    _text(approver, "approver", 200)
    if separation_required and submitter == approver:
        raise ValueError("submitter and approver must differ")
    try:
        expiry = datetime.fromisoformat(expires_at)
    except (TypeError, ValueError) as exc:
        raise ValueError("expires_at must be an offset-bearing ISO timestamp") from exc
    if expiry.tzinfo is None or expiry <= datetime.now(UTC):
        raise ValueError("authorization expiry must be a future offset-bearing timestamp")
    if (expiry - datetime.now(UTC)).total_seconds() > maximum_ttl_seconds:
        raise ValueError("authorization expiry exceeds the configured approval TTL")
    if not isinstance(secret, str) or len(secret) < 32:
        raise ValueError("authorization signing secret must contain at least 32 characters")
    result = {
        "schema_version": "business_record_change_authorization_v1",
        "change_id": preview["change_id"],
        "preview_sha256": preview["preview_sha256"],
        "configuration_sha256": preview["configuration_sha256"],
        "submitter": submitter,
        "approver": approver,
        "decision": "authorize",
        "expires_at": expiry.isoformat(),
    }
    result["signature"] = hmac.new(
        secret.encode(), _json(result).encode(), hashlib.sha256
    ).hexdigest()
    return result


def verify_authorization(authorization, preview, secret):
    """Verify an authorization's signature and its binding to a preview before applying it."""
    if not isinstance(secret, str) or len(secret) < 32:
        raise ValueError("authorization signing secret must contain at least 32 characters")
    signature = authorization.get("signature") if isinstance(authorization, dict) else None
    unsigned = (
        {key: value for key, value in authorization.items() if key != "signature"}
        if isinstance(authorization, dict)
        else {}
    )
    expected = hmac.new(secret.encode(), _json(unsigned).encode(), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise ValueError("authorization signature is invalid")
    if (
        unsigned.get("schema_version") != "business_record_change_authorization_v1"
        or unsigned.get("decision") != "authorize"
        or unsigned.get("preview_sha256") != preview["preview_sha256"]
        or unsigned.get("change_id") != preview["change_id"]
        or unsigned.get("configuration_sha256") != preview["configuration_sha256"]
    ):
        raise ValueError("authorization does not bind this preview")
    try:
        expiry = datetime.fromisoformat(unsigned["expires_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("authorization expiry is invalid") from exc
    if expiry.tzinfo is None or expiry <= datetime.now(UTC):
        raise ValueError("authorization is expired")
    return unsigned


def apply_change(
    config,
    preview,
    authorization,
    secret,
    request_key,
    output_directory,
    opener=urllib.request.urlopen,
):
    """Apply one authorized payload through file or generic JSON HTTP adapter."""
    verified = verify_authorization(authorization, preview, secret)
    adapter = config["record_maintenance"]["adapter"]
    payload = _json(preview["target_payload"]).encode()
    if adapter["kind"] == "file":
        root = _private_directory(output_directory, "File-adapter output directory")
        target = root / f"{preview['change_id']}.json"
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        external_id, response_hash = (
            preview["target_payload"].get("external_id"),
            hashlib.sha256(payload).hexdigest(),
        )
        transport = "file"
    else:
        credential_env = _text(adapter.get("credential_env"), "adapter credential_env", 200)
        credential = os.environ.get(credential_env)
        if not credential:
            raise ValueError("target adapter credential is unavailable")
        url = adapter.get("base_url", "").rstrip("/") + preview["target_endpoint"]
        headers = {
            "Authorization": f"Bearer {credential}",
            "Content-Type": "application/json",
            "Idempotency-Key": request_key,
            **adapter.get("headers", {}),
        }
        method = "POST" if preview["before"] is None else "PATCH"
        request = urllib.request.Request(  # noqa: S310 - base URL is validated HTTPS.
            url, data=payload, headers=headers, method=method
        )
        try:
            with opener(request, timeout=float(adapter.get("timeout_seconds", 30))) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, urllib.error.URLError) as exc:
            raise ValueError("target adapter request failed; reconcile before retrying") from exc
        if len(data) > MAX_RESPONSE_BYTES:
            raise ValueError("target adapter response exceeds the byte limit")
        try:
            decoded = json.loads(data)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("target adapter returned invalid JSON") from exc
        external_id = (
            decoded.get(adapter.get("response_id_field", "id"))
            if isinstance(decoded, dict)
            else None
        )
        response_hash, transport = hashlib.sha256(data).hexdigest(), "http_json"
    receipt = {
        "schema_version": "business_record_change_application_v1",
        "change_id": preview["change_id"],
        "preview_sha256": preview["preview_sha256"],
        "authorization_sha256": stable_checksum(authorization),
        "approved_by": verified["approver"],
        "transport": transport,
        "external_id": external_id,
        "response_sha256": response_hash,
        "reconciliation_status": "pending",
        "canonical_snapshot_changed": False,
    }
    receipt["receipt_sha256"] = stable_checksum(receipt)
    return receipt


def reconcile(config, preview, application, output_directory, opener=urllib.request.urlopen):
    """Read the target independently and account for every expected target field."""
    adapter = config["record_maintenance"]["adapter"]
    receipt_hash = application.get("receipt_sha256") if isinstance(application, dict) else None
    unsigned_application = (
        {key: value for key, value in application.items() if key != "receipt_sha256"}
        if isinstance(application, dict)
        else {}
    )
    if (
        not isinstance(application, dict)
        or application.get("change_id") != preview["change_id"]
        or receipt_hash != stable_checksum(unsigned_application)
    ):
        raise ValueError("application receipt does not bind this preview")
    if adapter["kind"] == "file":
        root = _private_directory(output_directory, "File-adapter output directory")
        target = root / f"{preview['change_id']}.json"
        if not target.is_file() or target.is_symlink():
            raise ValueError("file-adapter artifact is unavailable")
        try:
            observed = json.loads(target.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("file-adapter artifact is invalid") from exc
    else:
        credential_env = _text(adapter.get("credential_env"), "adapter credential_env", 200)
        credential = os.environ.get(credential_env)
        if not credential or not application.get("external_id"):
            raise ValueError("target reconciliation credential or external ID is unavailable")
        url = (
            adapter.get("base_url", "").rstrip("/")
            + preview["target_endpoint"]
            + "/"
            + urllib.parse.quote(str(application["external_id"]), safe="")
        )
        request = urllib.request.Request(  # noqa: S310 - base URL is validated HTTPS.
            url,
            headers={"Authorization": f"Bearer {credential}", **adapter.get("headers", {})},
            method="GET",
        )
        try:
            with opener(request, timeout=float(adapter.get("timeout_seconds", 30))) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, urllib.error.URLError) as exc:
            raise ValueError("target reconciliation request failed") from exc
        if len(data) > MAX_RESPONSE_BYTES:
            raise ValueError("target reconciliation response exceeds the byte limit")
        try:
            observed = json.loads(data)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("target reconciliation returned invalid JSON") from exc
    expected = preview["target_payload"]
    mismatches = [
        {
            "field": field,
            "expected": value,
            "observed": observed.get(field) if isinstance(observed, dict) else None,
        }
        for field, value in expected.items()
        if not isinstance(observed, dict) or observed.get(field) != value
    ]
    result = {
        "schema_version": "business_record_change_reconciliation_v1",
        "change_id": preview["change_id"],
        "application_receipt_sha256": application.get("receipt_sha256"),
        "expected_fields": len(expected),
        "matched_fields": len(expected) - len(mismatches),
        "mismatches": mismatches,
        "status": "reconciled" if not mismatches else "blocked_mismatch",
        "new_approved_snapshot_required": True,
    }
    result["reconciliation_sha256"] = stable_checksum(result)
    return result


def _write_new(path, value):
    destination = Path(path)
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(
        description="Propose, preview, authorize, or apply governed business-record changes."
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Validated client-specific platform YAML file without embedded secrets.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    schema_parser = subparsers.add_parser(
        "schema", help="Print the configured writable object schema."
    )
    schema_parser.set_defaults(action="schema")
    propose = subparsers.add_parser(
        "propose", help="Retain a proposal in the append-only change journal."
    )
    for name in ("owner", "request-key", "request"):
        propose.add_argument(f"--{name}", required=True, help=f"Proposal {name.replace('-', ' ')}.")
    propose.set_defaults(action="propose")
    preview = subparsers.add_parser(
        "preview", help="Preview a proposal against the immutable snapshot."
    )
    preview.add_argument("--owner", required=True, help="Exact proposal owner.")
    preview.add_argument("--change-id", required=True, help="Retained proposal identifier.")
    preview.set_defaults(action="preview")
    authorize_parser = subparsers.add_parser(
        "authorize",
        help="Create a signed operator authorization for the current immutable preview.",
    )
    for name in ("owner", "change-id", "approver", "expires-at", "request-key", "out"):
        authorize_parser.add_argument(
            f"--{name}", required=True, help=f"Authorization {name.replace('-', ' ')}."
        )
    authorize_parser.set_defaults(action="authorize")
    apply_parser = subparsers.add_parser(
        "apply", help="Apply an authorized preview through the configured target adapter."
    )
    for name in ("owner", "change-id", "authorization", "request-key", "out"):
        apply_parser.add_argument(
            f"--{name}", required=True, help=f"Application {name.replace('-', ' ')}."
        )
    apply_parser.add_argument(
        "--execute",
        action="store_true",
        help="Permit the configured live HTTP adapter; file adapters remain no-send artifacts.",
    )
    apply_parser.set_defaults(action="apply")
    reconcile_parser = subparsers.add_parser(
        "reconcile", help="Read the target independently and compare every expected field."
    )
    for name in ("owner", "change-id", "application", "request-key", "out"):
        reconcile_parser.add_argument(
            f"--{name}", required=True, help=f"Reconciliation {name.replace('-', ' ')}."
        )
    reconcile_parser.set_defaults(action="reconcile")
    apply_shared_help(parser)
    args = parser.parse_args()
    config = load(args.config)
    if args.action == "schema":
        value = change_schema(config)
    else:
        store = ChangeStore(
            config["deployment"]["change_dir"], config["client"]["tenant_id"], fingerprint(config)
        )
        if args.action == "propose":
            value = store.propose(
                args.owner, args.request_key, config, json.loads(Path(args.request).read_text())
            )
        elif args.action == "preview":
            value = store.preview(
                args.owner, args.change_id, config["deployment"]["snapshot"], config
            )
        else:
            preview_value = store.preview(
                args.owner, args.change_id, config["deployment"]["snapshot"], config
            )
            secret = os.environ.get(config["record_maintenance"]["authorization_secret_env"], "")
            if args.action == "authorize":
                value = authorize(
                    preview_value,
                    args.owner,
                    args.approver,
                    args.expires_at,
                    secret,
                    separation_required=config["record_maintenance"]["separation_of_duties"],
                    maximum_ttl_seconds=config["record_maintenance"]["approval_ttl_seconds"],
                )
                store.record_lifecycle(
                    args.owner, "authorization", args.request_key, args.change_id, value
                )
            elif args.action == "apply":
                if (
                    config["record_maintenance"]["adapter"]["kind"] == "http_json"
                    and not args.execute
                ):
                    raise ValueError("live HTTP application requires --execute")
                value = apply_change(
                    config,
                    preview_value,
                    json.loads(Path(args.authorization).read_text()),
                    secret,
                    args.request_key,
                    config["record_maintenance"]["adapter"].get("output_directory", "."),
                )
                store.record_lifecycle(
                    args.owner, "application", args.request_key, args.change_id, value
                )
            else:
                value = reconcile(
                    config,
                    preview_value,
                    json.loads(Path(args.application).read_text()),
                    config["record_maintenance"]["adapter"].get("output_directory", "."),
                )
                store.record_lifecycle(
                    args.owner, "reconciliation", args.request_key, args.change_id, value
                )
            _write_new(args.out, value)
    json.dump(value, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
