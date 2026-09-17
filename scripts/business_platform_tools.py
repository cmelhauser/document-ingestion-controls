"""Closed-world MCP/API operations for governed analytics and change proposals."""

from pathlib import Path

from business_analytics import query, run_saved, semantic_model
from business_record_changes import ChangeStore, apply_change, authorize, change_schema, reconcile
from client_platform_config import fingerprint, public_summary

READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
PROPOSAL = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
IDENTIFIER = {"type": "string", "minLength": 1, "maxLength": 200}
OPERATIONS = {
    "get_business_platform_capabilities": (
        "Inspect the configured tenant-safe analytics and governed record-maintenance surface.",
        {},
        "platform:read",
        READ_ONLY,
    ),
    "get_business_object_schema": (
        "Inspect configured writable objects. This grants no write or approval authority.",
        {},
        "platform:read",
        READ_ONLY,
    ),
    "propose_business_record_change": (
        "Retain a create/amend proposal. It remains unapplied and requires separate human/operator authorization.",
        {"idempotency_key": IDENTIFIER, "request": {"type": "object"}},
        "records:propose",
        PROPOSAL,
    ),
    "preview_business_record_change": (
        "Preview before/after and target payload against the immutable approved snapshot.",
        {"change_id": IDENTIFIER},
        "records:propose",
        READ_ONLY,
    ),
    "get_business_record_change": (
        "Retrieve the exact owner-scoped record-change request and receipt.",
        {"change_id": IDENTIFIER},
        "records:propose",
        READ_ONLY,
    ),
    "get_analytics_semantic_model": (
        "Inspect allowed datasets, dimensions, metrics, joins, and saved reports.",
        {},
        "analytics:read",
        READ_ONLY,
    ),
    "query_business_analytics": (
        "Execute one bounded typed query plan; unrestricted SQL is never accepted.",
        {"plan": {"type": "object"}},
        "analytics:read",
        READ_ONLY,
    ),
    "run_saved_business_report": (
        "Run one configured, version-bound saved analytical report.",
        {"report": IDENTIFIER},
        "analytics:read",
        READ_ONLY,
    ),
    "create_business_analytics_export": (
        "Create a checksummed CSV/XLSX download job from one typed plan or saved report.",
        {
            "request": {"type": "object"},
            "format": {"type": "string", "enum": ["csv", "xlsx"]},
        },
        "crm:export",
        PROPOSAL,
    ),
}

OPERATOR_OPERATIONS = {
    "authorize_business_record_change": "records:authorize",
    "apply_business_record_change": "records:apply",
    "reconcile_business_record_change": "records:apply",
}


def tool_definitions(config=None, exports=False):
    """Return the platform MCP tool definitions a configuration enables."""
    enabled = set(OPERATIONS)
    if not exports:
        enabled.discard("create_business_analytics_export")
    if config is not None:
        if not config["record_maintenance"]["enabled"]:
            enabled -= {
                "get_business_object_schema",
                "propose_business_record_change",
                "preview_business_record_change",
                "get_business_record_change",
            }
        if not config["analytics"]["enabled"]:
            enabled -= {
                "get_analytics_semantic_model",
                "query_business_analytics",
                "run_saved_business_report",
                "create_business_analytics_export",
            }
    tools = []
    for name, (description, properties, _scope, annotations) in OPERATIONS.items():
        if name not in enabled:
            continue
        tools.append(
            {
                "name": name,
                "title": name.replace("_", " ").title(),
                "description": description,
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(properties),
                    "properties": properties,
                },
                "outputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["result"],
                    "properties": {"result": {}},
                },
                "annotations": annotations,
            }
        )
    return tools


def required_scope(name):
    """Return the OAuth scope a platform operation requires."""
    return OPERATIONS[name][2]


class PlatformService:
    """The client platform's operations, bound to one configuration, snapshot and change store."""

    def __init__(self, config, database, export_jobs=None, authorization_secret=None):
        if Path(config["deployment"]["snapshot"]).resolve() != Path(database).resolve():
            raise ValueError("Client configuration snapshot does not match the served database")
        self.config = config
        self.database = database
        self.export_jobs = export_jobs
        self.authorization_secret = authorization_secret
        if config["record_maintenance"]["enabled"]:
            change_schema(config)
        if config["analytics"]["enabled"]:
            semantic_model(config)
        self.store = ChangeStore(
            config["deployment"]["change_dir"],
            config["client"]["tenant_id"],
            fingerprint(config),
        )

    def tool_definitions(self):
        """Return the tools this service's configuration and export store enable."""
        return tool_definitions(self.config, self.export_jobs is not None)

    def invoke(self, owner, name, arguments):
        """Run one platform operation for an authenticated owner, refusing unknown operations."""
        if name not in OPERATIONS or not isinstance(arguments, dict):
            raise ValueError("Unknown platform operation or invalid arguments")
        if (
            name
            in {
                "get_business_object_schema",
                "propose_business_record_change",
                "preview_business_record_change",
                "get_business_record_change",
            }
            and not self.config["record_maintenance"]["enabled"]
        ):
            raise ValueError("Record maintenance is disabled")
        if (
            name
            in {
                "get_analytics_semantic_model",
                "query_business_analytics",
                "run_saved_business_report",
                "create_business_analytics_export",
            }
            and not self.config["analytics"]["enabled"]
        ):
            raise ValueError("Analytics is disabled")
        required = set(OPERATIONS[name][1])
        if set(arguments) != required:
            raise ValueError(f"{name} requires exactly {sorted(required)}")
        if name == "get_business_platform_capabilities":
            return {
                **public_summary(self.config),
                "operations": [item["name"] for item in self.tool_definitions()],
                "record_changes_are_proposals": True,
                "model_authorization_permitted": False,
                "canonical_snapshot_read_only": True,
            }
        if name == "get_business_object_schema":
            return change_schema(self.config)
        if name == "propose_business_record_change":
            return self.store.propose(
                owner, arguments["idempotency_key"], self.config, arguments["request"]
            )
        if name == "preview_business_record_change":
            return self.store.preview(owner, arguments["change_id"], self.database, self.config)
        if name == "get_business_record_change":
            request, receipt = self.store.proposal(owner, arguments["change_id"])
            return {
                "request": request,
                "receipt": receipt,
                "event_history": self.store.history(owner, arguments["change_id"]),
            }
        if name == "get_analytics_semantic_model":
            return semantic_model(self.config)
        if name == "query_business_analytics":
            return query(self.database, self.config, arguments["plan"])
        if name == "create_business_analytics_export":
            if self.export_jobs is None:
                raise ValueError("Business analytics exports are unavailable")
            request = arguments["request"]
            if not isinstance(request, dict) or set(request) not in ({"plan"}, {"report"}):
                raise ValueError("request must contain exactly one of plan or report")
            result = (
                query(self.database, self.config, request["plan"])
                if "plan" in request
                else run_saved(self.database, self.config, request["report"])
            )
            subject_name = request.get("report", request.get("plan", {}).get("dataset", "query"))
            return self.export_jobs.create_rows(
                owner,
                subject={"kind": "analytics", "name": subject_name, "arguments": request},
                rows=result["rows"],
                snapshot=result["snapshot"],
                format=arguments["format"],
            )
        return run_saved(self.database, self.config, arguments["report"])

    def invoke_operator(self, actor, name, arguments):
        """Execute non-MCP operator actions after distinct OAuth scope checks."""
        if name not in OPERATOR_OPERATIONS or not isinstance(arguments, dict):
            raise ValueError("Unknown operator operation or invalid arguments")
        if not self.config["record_maintenance"]["enabled"]:
            raise ValueError("Record maintenance is disabled")
        if not self.authorization_secret:
            raise ValueError("Operator record-change actions are unavailable")
        schemas = {
            "authorize_business_record_change": {
                "owner",
                "change_id",
                "idempotency_key",
                "expires_at",
                "decision",
            },
            "apply_business_record_change": {
                "owner",
                "change_id",
                "idempotency_key",
                "authorization_sha256",
                "execute",
            },
            "reconcile_business_record_change": {
                "owner",
                "change_id",
                "idempotency_key",
                "application_sha256",
            },
        }
        if set(arguments) != schemas[name]:
            raise ValueError(f"{name} requires exactly {sorted(schemas[name])}")
        owner, change_id = arguments["owner"], arguments["change_id"]
        preview = self.store.preview(owner, change_id, self.database, self.config)
        if name == "authorize_business_record_change":
            if arguments["decision"] != "authorize":
                raise ValueError("decision must be the explicit value authorize")
            artifact = authorize(
                preview,
                owner,
                actor,
                arguments["expires_at"],
                self.authorization_secret,
                separation_required=self.config["record_maintenance"]["separation_of_duties"],
                maximum_ttl_seconds=self.config["record_maintenance"]["approval_ttl_seconds"],
            )
            receipt = self.store.record_lifecycle(
                owner,
                "authorization",
                arguments["idempotency_key"],
                change_id,
                artifact,
            )
            return {"authorization": artifact, "receipt": receipt}
        if name == "apply_business_record_change":
            if not isinstance(arguments["execute"], bool):
                raise ValueError("execute must be Boolean")
            authorization_artifact, _authorization_receipt = self.store.lifecycle_artifact(
                owner,
                change_id,
                "authorization",
                arguments["authorization_sha256"],
            )
            prior = self.store.application_for_authorization(
                owner, change_id, arguments["authorization_sha256"]
            )
            if prior is not None:
                artifact, receipt = prior
                return {"application": artifact, "receipt": receipt, "idempotent_replay": True}
            adapter = self.config["record_maintenance"]["adapter"]
            if adapter["kind"] == "http_json" and not arguments["execute"]:
                raise ValueError("live HTTP application requires execute=true")
            artifact = apply_change(
                self.config,
                preview,
                authorization_artifact,
                self.authorization_secret,
                # The approval is the one durable authorization for a target
                # mutation.  Pin the outbound key to it so two concurrent,
                # differently keyed API requests cannot create two target-side
                # mutations before this local journal observes the first one.
                arguments["authorization_sha256"],
                adapter.get("output_directory", "."),
            )
            receipt = self.store.record_lifecycle(
                owner,
                "application",
                arguments["idempotency_key"],
                change_id,
                artifact,
            )
            return {"application": artifact, "receipt": receipt, "idempotent_replay": False}
        application_artifact, _application_receipt = self.store.lifecycle_artifact(
            owner, change_id, "application", arguments["application_sha256"]
        )
        artifact = reconcile(
            self.config,
            preview,
            application_artifact,
            self.config["record_maintenance"]["adapter"].get("output_directory", "."),
        )
        receipt = self.store.record_lifecycle(
            owner,
            "reconciliation",
            arguments["idempotency_key"],
            change_id,
            artifact,
        )
        return {"reconciliation": artifact, "receipt": receipt}
