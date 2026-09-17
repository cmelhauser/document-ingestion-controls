#!/usr/bin/env python3
"""Load one secret-free, client-specific business platform YAML contract."""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

VERSION = "business_data_platform_v1"
MAX_CONFIG_BYTES = 1_000_000
TOP_LEVEL = {"schema_version", "client", "deployment", "record_maintenance", "analytics"}
SECRET_NAME = re.compile(r"(?:^|_)(?:secret|password|token|api_key|private_key)(?:$|_)")
ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that refuses ambiguous duplicate mapping keys."""


def _unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _object(value, label, allowed, required=()):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = set(value) - set(allowed)
    missing = set(required) - set(value)
    if unknown:
        raise ValueError(f"{label} contains unsupported fields: {sorted(unknown)}")
    if missing:
        raise ValueError(f"{label} is missing fields: {sorted(missing)}")
    return value


def _text(value, label, maximum=500):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must be non-empty text of at most {maximum} characters")
    return value


def _positive(value, label, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{label} must be an integer from 1 through {maximum}")
    return value


def _secret_scan(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            current = f"{path}.{key}" if path else str(key)
            if SECRET_NAME.search(str(key)) and not str(key).endswith("_env"):
                if item not in (None, ""):
                    raise ValueError(f"Client YAML must not contain secret material: {current}")
            _secret_scan(item, current)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _secret_scan(item, f"{path}[{index}]")


def _mapping(value, label):
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{label} must be a non-empty object")
    for key in value:
        _text(key, f"{label} key", 100)
    return value


def _environment_name(value, label):
    _text(value, label, 200)
    if not ENVIRONMENT_NAME.fullmatch(value):
        raise ValueError(f"{label} must name an environment variable")
    return value


def _absolute_path(value, label):
    _text(value, label, 2000)
    if not Path(value).is_absolute() or ".." in Path(value).parts:
        raise ValueError(f"{label} must be an absolute path without parent traversal")
    return value


def _https_origin(value, label):
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError(f"{label} must be an absolute HTTPS origin")
    return value


def validate(config):
    """Validate the closed outer contract; engines validate their typed internals."""
    _object(config, "configuration", TOP_LEVEL, TOP_LEVEL)
    if config["schema_version"] != VERSION:
        raise ValueError(f"schema_version must be {VERSION}")
    client = _object(
        config["client"],
        "client",
        {"tenant_id", "display_name", "timezone", "fiscal_year_start_month"},
        {"tenant_id", "display_name", "timezone", "fiscal_year_start_month"},
    )
    _text(client["tenant_id"], "client.tenant_id", 200)
    _text(client["display_name"], "client.display_name", 200)
    _text(client["timezone"], "client.timezone", 100)
    try:
        ZoneInfo(client["timezone"])
    except ZoneInfoNotFoundError as exc:
        raise ValueError("client.timezone must be an IANA timezone") from exc
    _positive(client["fiscal_year_start_month"], "client.fiscal_year_start_month", 12)
    deployment = _object(
        config["deployment"],
        "deployment",
        {
            "snapshot",
            "bind_host",
            "port",
            "public_base_url",
            "tls_certificate",
            "tls_private_key_env",
            "authorization_server",
            "introspection_endpoint",
            "client_id",
            "client_secret_env",
            "tenant_claim",
            "role_claim",
            "allowed_roles",
            "allowed_origins",
            "audit_log",
            "export_dir",
            "intake_dir",
            "change_dir",
            "max_request_bytes",
            "rate_limit_per_minute",
            "export_ttl_seconds",
            "max_export_rows",
        },
        {
            "snapshot",
            "bind_host",
            "port",
            "public_base_url",
            "authorization_server",
            "introspection_endpoint",
            "client_id",
            "client_secret_env",
            "tenant_claim",
            "role_claim",
            "allowed_roles",
            "allowed_origins",
            "audit_log",
            "export_dir",
            "change_dir",
            "tls_certificate",
            "tls_private_key_env",
        },
    )
    for name in ("snapshot", "audit_log", "export_dir", "change_dir", "tls_certificate"):
        _absolute_path(deployment[name], f"deployment.{name}")
    if deployment.get("intake_dir") is not None:
        _absolute_path(deployment["intake_dir"], "deployment.intake_dir")
    for name in ("bind_host", "client_id", "tenant_claim", "role_claim"):
        _text(deployment[name], f"deployment.{name}", 2000)
    for name in ("client_secret_env", "tls_private_key_env"):
        _environment_name(deployment[name], f"deployment.{name}")
    _https_origin(deployment["public_base_url"], "deployment.public_base_url")
    for name in ("authorization_server", "introspection_endpoint"):
        parsed = urlparse(deployment[name])
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError(f"deployment.{name} must be an absolute HTTPS URL without user info")
    _positive(deployment["port"], "deployment.port", 65_535)
    _positive(
        deployment.get("max_request_bytes", 1_000_000), "deployment.max_request_bytes", 50_000_000
    )
    _positive(
        deployment.get("rate_limit_per_minute", 60), "deployment.rate_limit_per_minute", 100_000
    )
    _positive(deployment.get("export_ttl_seconds", 900), "deployment.export_ttl_seconds", 3600)
    if deployment.get("export_ttl_seconds", 900) < 60:
        raise ValueError("deployment.export_ttl_seconds must be at least 60")
    _positive(deployment.get("max_export_rows", 10_000), "deployment.max_export_rows", 100_000)
    for name in ("allowed_roles", "allowed_origins"):
        values = deployment[name]
        if not isinstance(values, list) or not values or len(values) > 100:
            raise ValueError(f"deployment.{name} must be a non-empty list of at most 100 values")
        for item in values:
            _text(item, f"deployment.{name} item", 500)
        if len(set(values)) != len(values):
            raise ValueError(f"deployment.{name} values must be unique")
    for origin in deployment["allowed_origins"]:
        _https_origin(origin, "deployment.allowed_origins item")
    maintenance = _object(
        config["record_maintenance"],
        "record_maintenance",
        {
            "enabled",
            "approval_ttl_seconds",
            "separation_of_duties",
            "authorization_secret_env",
            "objects",
            "adapter",
        },
        {
            "enabled",
            "approval_ttl_seconds",
            "separation_of_duties",
            "authorization_secret_env",
            "objects",
            "adapter",
        },
    )
    if not isinstance(maintenance["enabled"], bool) or not isinstance(
        maintenance["separation_of_duties"], bool
    ):
        raise ValueError("record-maintenance switches must be Boolean")
    _positive(
        maintenance["approval_ttl_seconds"], "record_maintenance.approval_ttl_seconds", 604_800
    )
    _environment_name(
        maintenance["authorization_secret_env"], "record_maintenance.authorization_secret_env"
    )
    _mapping(maintenance["objects"], "record_maintenance.objects")
    _object(
        maintenance["adapter"],
        "record_maintenance.adapter",
        {
            "kind",
            "output_directory",
            "base_url",
            "credential_env",
            "timeout_seconds",
            "headers",
            "response_id_field",
        },
        {"kind"},
    )
    if maintenance["adapter"]["kind"] not in {"file", "http_json"}:
        raise ValueError("record_maintenance.adapter.kind must be file or http_json")
    if maintenance["adapter"]["kind"] == "http_json":
        base_url = maintenance["adapter"].get("base_url")
        credential_env = maintenance["adapter"].get("credential_env")
        parsed = urlparse(base_url) if isinstance(base_url, str) else None
        if (
            not parsed
            or parsed.scheme != "https"
            or not parsed.netloc
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("HTTP adapter base_url must be an HTTPS origin")
        _environment_name(credential_env, "record_maintenance.adapter.credential_env")
    else:
        _absolute_path(
            maintenance["adapter"].get("output_directory"),
            "record_maintenance.adapter.output_directory",
        )
    _positive(
        maintenance["adapter"].get("timeout_seconds", 30),
        "record_maintenance.adapter.timeout_seconds",
        300,
    )
    _text(
        maintenance["adapter"].get("response_id_field", "id"),
        "record_maintenance.adapter.response_id_field",
        200,
    )
    headers = maintenance["adapter"].get("headers", {})
    if not isinstance(headers, dict) or len(headers) > 50:
        raise ValueError("record_maintenance.adapter.headers must be an object of at most 50 items")
    for key, value in headers.items():
        _text(key, "record_maintenance.adapter header name", 200)
        _text(value, "record_maintenance.adapter header value", 2000)
        if key.casefold() in {"authorization", "proxy-authorization"}:
            raise ValueError(
                "Authorization credentials must use the named credential environment variable"
            )
    analytics = _object(
        config["analytics"],
        "analytics",
        {"enabled", "max_input_rows", "max_result_rows", "datasets", "saved_reports"},
        {"enabled", "max_input_rows", "max_result_rows", "datasets", "saved_reports"},
    )
    if not isinstance(analytics["enabled"], bool):
        raise ValueError("analytics.enabled must be Boolean")
    _positive(analytics["max_input_rows"], "analytics.max_input_rows", 1_000_000)
    _positive(analytics["max_result_rows"], "analytics.max_result_rows", 10_000)
    _mapping(analytics["datasets"], "analytics.datasets")
    if not isinstance(analytics["saved_reports"], dict) or len(analytics["saved_reports"]) > 100:
        raise ValueError("analytics.saved_reports must be an object with at most 100 reports")
    _secret_scan(config)
    return config


def load(path):
    """Load bounded YAML without following a symlink or accepting duplicate state."""
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ValueError("Client configuration must be a regular non-symlink file")
    if source.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError("Client configuration exceeds the byte limit")
    try:
        loader = _UniqueKeyLoader(source.read_text(encoding="utf-8"))
        try:
            value = loader.get_single_data()
        finally:
            loader.dispose()
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("Client configuration is unreadable or invalid YAML") from exc
    return validate(value)


def fingerprint(config):
    """Return the SHA-256 of a configuration's canonical JSON, binding work to that exact configuration."""
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def public_summary(config):
    """Return a safe deploy receipt without filesystem paths or secret variable names."""
    return {
        "schema_version": VERSION,
        "configuration_sha256": fingerprint(config),
        "tenant_id": config["client"]["tenant_id"],
        "display_name": config["client"]["display_name"],
        "public_base_url": config["deployment"]["public_base_url"],
        "record_maintenance_enabled": config["record_maintenance"]["enabled"],
        "analytics_enabled": config["analytics"]["enabled"],
        "objects": sorted(config["record_maintenance"]["objects"]),
        "datasets": sorted(config["analytics"]["datasets"]),
        "saved_reports": sorted(config["analytics"]["saved_reports"]),
    }
