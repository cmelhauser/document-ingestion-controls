#!/usr/bin/env python3
"""Audit-only semantic schema discovery and mapping-registry update controls."""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import registry_approval
from cli_help import apply_shared_help
from extraction_schema import HEADER_FIELDS, LINE_FIELDS
from llm_provider import build_client
from llm_response import REASONING_EFFORTS, response_json, response_payload
from llm_runtime import retry_call
from runtime_config import (
    env_bool,
    env_float,
    env_int,
    env_value,
    lane_model,
    load_project_env,
    provider_credential_env,
)

FIELDS = tuple(
    dict.fromkeys(
        (
            "business_reference_id",
            "reference_semantic_type",
            "product_name",
            "sales_city",
            *HEADER_FIELDS,
            *LINE_FIELDS,
        )
    )
)
TYPES = (
    "customer_ack",
    "order_reference",
    "dealer_job",
    "dealer",
    "customer",
    "payer",
    "brand",
    "product",
    "financial",
    "location",
    "contact",
    "sales_representative",
    "payment",
    "shipping",
    "tax",
    "inventory",
    "service",
    "workforce",
    "expense",
    "contract",
    "approval",
    "compliance",
    "document_control",
    "unknown",
)
ENTITY_TYPES = (
    "dealer",
    "brand",
    "customer",
    "vendor",
    "seller",
    "payer",
    "payee",
    "carrier",
    "contact",
    "sales_representative",
    "employee",
    "location",
    "unknown",
)
RELATIONSHIP_TYPES = (
    "dealer_represents_brand",
    "dealer_serves_customer",
    "entity_operates_at_location",
    "party_bills_party",
    "party_ships_to_party",
    "party_pays_party",
    "contact_represents_party",
    "sales_representative_serves_party",
    "document_references_document",
    "document_fulfills_document",
    "document_settles_document",
    "unknown",
)
PLACES_ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
GRAPH_INVENTORY_MAX_FIELDS = 50
GRAPH_INVENTORY_MAX_CANDIDATE_FIELDS = 25
GRAPH_INVENTORY_MAX_EXAMPLES = 3
GRAPH_INVENTORY_MAX_CLAIM_IDS = 10
GRAPH_INVENTORY_MAX_BYTES = 65_536
GRAPH_SURFACE_MAX_ARTIFACT_BYTES = 10_000_000
GRAPH_SURFACE_EXAMPLE_MAX_CHARS = 512

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "mapping_proposals",
        "entity_proposals",
        "relationship_proposals",
        "schema_changes",
    ],
    "properties": {
        "mapping_proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "source_label",
                    "canonical_field",
                    "semantic_type",
                    "confidence",
                    "rationale",
                    "alternatives",
                ],
                "properties": {
                    "source_label": {"type": "string"},
                    "canonical_field": {"type": "string", "enum": list(FIELDS)},
                    "semantic_type": {"type": "string", "enum": list(TYPES)},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                    "alternatives": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "entity_proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "entity_id",
                    "entity_type",
                    "canonical_name",
                    "confidence",
                    "rationale",
                ],
                "properties": {
                    "entity_id": {"type": "string"},
                    "entity_type": {"type": "string", "enum": list(ENTITY_TYPES)},
                    "canonical_name": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                },
            },
        },
        "relationship_proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "from_entity_id",
                    "to_entity_id",
                    "relationship_type",
                    "confidence",
                    "rationale",
                ],
                "properties": {
                    "from_entity_id": {"type": "string"},
                    "to_entity_id": {"type": "string"},
                    "relationship_type": {"type": "string", "enum": list(RELATIONSHIP_TYPES)},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                },
            },
        },
        "schema_changes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source_label", "suggested_name", "confidence", "rationale"],
                "properties": {
                    "source_label": {"type": "string"},
                    "suggested_name": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                },
            },
        },
    },
}
INSTRUCTIONS = """You are an evidence-bound business data-model analyst. Map only supplied labels and entity IDs to the controlled vocabulary. Use every supplied source-evidence item and iterative-mapping-context item; compare the current template with prior proposals, retain the best-supported match, and challenge earlier proposals when new evidence conflicts. A graph-schema-inventory, when supplied, is bounded corpus context only: it can help identify vocabulary already observed, but it is not source-template evidence and cannot by itself support a mapping, entity, relationship, or schema change. ACK, order reference, and job number are not interchangeable facts. Propose relationships only from supplied evidence. Return a schema change when the vocabulary is insufficient. Never approve a mapping, invent a location, erase a source label, or treat Google candidates as proof of identity."""
BUDDY_INSTRUCTIONS = """You are an independent schema-proposal verifier. Inspect the supplied immutable source-template packet and each primary mapping proposal. Classify it as confirmed only when the exact source label and retained template/page evidence support the proposed controlled field and semantic type; classify close_needs_review for plausible ambiguity, conflict for a material disagreement, and unsupported when the source does not support it. The graph inventory is corpus context only, never mapping evidence. Never approve, invent, normalize, or create a canonical fact."""
BUDDY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decisions"],
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source_label", "status", "rationale"],
                "properties": {
                    "source_label": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["confirmed", "close_needs_review", "conflict", "unsupported"],
                    },
                    "rationale": {"type": "string"},
                },
            },
        }
    },
}


def timestamp():
    """Return a UTC audit timestamp."""
    return datetime.now(UTC).isoformat()


def normalize(value):
    """Normalize comparison keys only; source labels are never changed."""
    return re.sub(r"\s+", " ", str(value).strip()).casefold()


def template_fingerprint(template):
    """Hash ordered labels so changed templates cannot reuse a prior rule."""
    labels = "\x1f".join(normalize(item["source_label"]) for item in template["headers"])
    return hashlib.sha256(labels.encode()).hexdigest()


def load_templates(path):
    """Read explicit source-template observations."""
    data = json.loads(Path(path).read_text())
    templates = data.get("templates") if isinstance(data, dict) else None
    valid = (
        isinstance(templates, list)
        and templates
        and all(
            isinstance(item, dict)
            and isinstance(item.get("template_id"), str)
            and isinstance(item.get("headers"), list)
            and item["headers"]
            and all(
                isinstance(header, dict)
                and isinstance(header.get("source_label"), str)
                and header["source_label"].strip()
                for header in item["headers"]
            )
            for item in templates
        )
    )
    if not valid:
        raise ValueError(
            "Input must contain templates with template_id and non-empty header source_label values"
        )
    return templates


def load_registry(path):
    """Read a registry or return a safe empty registry."""
    if path is None:
        return {"registry_version": 0, "rules": []}
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise ValueError("Registry must be an object containing rules")
    return data


def load_graph_schema_inventory(path):
    """Load a bounded, proposal-only graph inventory for LLM packet context."""
    raw = Path(path).read_bytes()
    if len(raw) > GRAPH_SURFACE_MAX_ARTIFACT_BYTES:
        raise ValueError("Graph schema inventory exceeds the 10000000-byte artifact limit")
    data = json.loads(raw)
    if (
        not isinstance(data, dict)
        or data.get("artifact_type")
        not in {"evidence_graph_schema_inventory_v1", "evidence_graph_schema_surface_inventory_v1"}
        or data.get("proposal_only") is not True
        or data.get("canonical_mapping_permitted") is not False
        or not isinstance(data.get("graph_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", data["graph_sha256"]) is None
    ):
        raise ValueError("Graph schema inventory must be a proposal-only inventory artifact")
    is_surface = data["artifact_type"] == "evidence_graph_schema_surface_inventory_v1"
    if not is_surface and len(raw) > GRAPH_INVENTORY_MAX_BYTES:
        raise ValueError("Graph schema inventory exceeds the 65536-byte context limit")
    observed = data.get("observed_fields") if is_surface else data.get("fields")
    if not isinstance(observed, list):
        raise ValueError("Graph schema inventory must contain observed fields")
    fields = []
    for item in observed[:GRAPH_INVENTORY_MAX_FIELDS]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("observed_field"), str)
            or not item["observed_field"].strip()
            or any(
                not isinstance(item.get(key), int) or item[key] < 0
                for key in ("claim_count", "document_count", "distinct_value_count")
            )
            or not isinstance(item.get("value_examples"), list)
            or not isinstance(item.get("claim_node_ids"), list)
            or item.get("proposal_only") is not True
            or item.get("canonical_mapping_permitted") is not False
        ):
            raise ValueError("Graph schema inventory contains an invalid field")
        if not all(isinstance(value, str) for value in item["claim_node_ids"]):
            raise ValueError("Graph schema inventory contains an invalid field")
        fields.append(
            {
                "observed_field": item["observed_field"],
                "claim_count": item["claim_count"],
                "document_count": item["document_count"],
                "distinct_value_count": item["distinct_value_count"],
                "value_examples": item["value_examples"][:GRAPH_INVENTORY_MAX_EXAMPLES],
                "claim_node_ids": item["claim_node_ids"][:GRAPH_INVENTORY_MAX_CLAIM_IDS],
                "proposal_only": True,
                "canonical_mapping_permitted": False,
            }
        )
    retained_candidate_fields = []
    unstructured_signal_summary = []
    total_candidate_field_count = 0
    if is_surface:
        source = data.get("source_artifacts", {}).get("consensus")
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("name"), str)
            or not isinstance(source.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", source["sha256"]) is None
            or not isinstance(data.get("retained_candidate_fields"), list)
        ):
            raise ValueError(
                "Graph schema surface inventory is missing retained candidate provenance"
            )
        candidates = data["retained_candidate_fields"]
        total_candidate_field_count = len(candidates)
        for item in sorted(
            candidates,
            key=lambda value: (
                not bool(value.get("discovery_topics")) if isinstance(value, dict) else True,
                -value.get("candidate_occurrence_count", 0) if isinstance(value, dict) else 0,
                value.get("candidate_field", "") if isinstance(value, dict) else "",
            ),
        )[:GRAPH_INVENTORY_MAX_CANDIDATE_FIELDS]:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("candidate_field"), str)
                or not item["candidate_field"].strip()
                or any(
                    not isinstance(item.get(key), int) or item[key] < 0
                    for key in (
                        "candidate_occurrence_count",
                        "document_count",
                        "distinct_value_count",
                    )
                )
                or not all(
                    isinstance(item.get(key), list)
                    for key in (
                        "candidate_value_examples",
                        "document_ids",
                        "source_pointers",
                        "consensus_flags",
                        "discovery_topics",
                    )
                )
                or item.get("evidence_state") != "retained_candidate"
                or item.get("proposal_only") is not True
                or item.get("canonical_mapping_permitted") is not False
            ):
                raise ValueError(
                    "Graph schema surface inventory contains an invalid candidate field"
                )
            if not all(
                isinstance(value, str)
                for key in (
                    "document_ids",
                    "source_pointers",
                    "consensus_flags",
                    "discovery_topics",
                )
                for value in item[key]
            ):
                raise ValueError(
                    "Graph schema surface inventory contains an invalid candidate field"
                )
            retained_candidate_fields.append(
                {
                    "candidate_field": item["candidate_field"],
                    "candidate_occurrence_count": item["candidate_occurrence_count"],
                    "document_count": item["document_count"],
                    "distinct_value_count": item["distinct_value_count"],
                    "candidate_value_examples": [
                        _clip_context_value(value)
                        for value in item["candidate_value_examples"][:GRAPH_INVENTORY_MAX_EXAMPLES]
                    ],
                    "document_ids": item["document_ids"][:GRAPH_INVENTORY_MAX_CLAIM_IDS],
                    "source_pointers": item["source_pointers"][:GRAPH_INVENTORY_MAX_CLAIM_IDS],
                    "consensus_flags": item["consensus_flags"],
                    "discovery_topics": item["discovery_topics"],
                    "evidence_state": "retained_candidate",
                    "proposal_only": True,
                    "canonical_mapping_permitted": False,
                }
            )
        summaries = data.get("unstructured_signal_summary", [])
        if not isinstance(summaries, list):
            raise ValueError("Graph schema surface inventory has invalid signal summary")
        for item in summaries:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("signal_type"), str)
                or not item["signal_type"].strip()
                or any(
                    not isinstance(item.get(key), int) or item[key] < 0
                    for key in ("occurrence_count", "document_count", "field_count")
                )
                or not isinstance(item.get("evidence_examples"), list)
                or item.get("proposal_only") is not True
                or item.get("canonical_mapping_permitted") is not False
            ):
                raise ValueError("Graph schema surface inventory has invalid signal summary")
            examples = item["evidence_examples"][:GRAPH_INVENTORY_MAX_EXAMPLES]
            if not all(
                isinstance(example, dict)
                and all(
                    isinstance(example.get(key), str)
                    for key in ("document_id", "field", "json_pointer", "evidence_text")
                )
                for example in examples
            ):
                raise ValueError("Graph schema surface inventory has invalid signal summary")
            unstructured_signal_summary.append(
                {
                    "signal_type": item["signal_type"],
                    "occurrence_count": item["occurrence_count"],
                    "document_count": item["document_count"],
                    "field_count": item["field_count"],
                    "evidence_examples": examples,
                    "proposal_only": True,
                    "canonical_mapping_permitted": False,
                }
            )
    result = {
        "graph_sha256": data["graph_sha256"],
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "fields": fields,
        "retained_candidate_fields": retained_candidate_fields,
        "unstructured_signal_summary": unstructured_signal_summary,
        "inventory_type": "schema_surface" if is_surface else "observed_claims",
        "total_observed_field_count": len(observed),
        "total_candidate_field_count": total_candidate_field_count,
        "candidate_fields_omitted_from_context": max(
            0, total_candidate_field_count - len(retained_candidate_fields)
        ),
        "proposal_only": True,
        "canonical_mapping_permitted": False,
    }
    if (
        len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode())
        > GRAPH_INVENTORY_MAX_BYTES
    ):
        raise ValueError("Reduced graph schema inventory exceeds the 65536-byte context limit")
    return result


def _clip_context_value(value):
    """Bound an LLM context excerpt without changing the retained source artifact."""
    if isinstance(value, str):
        return value[:GRAPH_SURFACE_EXAMPLE_MAX_CHARS]
    if isinstance(value, list):
        return [_clip_context_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _clip_context_value(item) for key, item in value.items()}
    return value


def rule_for(registry, template_hash, label):
    """Reuse only an exact template-label rule carrying a recorded approval."""
    for rule in registry["rules"]:
        if (
            registry_approval.is_approved(rule)
            and rule.get("rule_type", "source_label_mapping") == "source_label_mapping"
            and rule.get("template_fingerprint") == template_hash
            and normalize(rule.get("source_label", "")) == normalize(label)
        ):
            return rule
    return None


def review(template, field, reason, priority="high"):
    """Create the standard eight-field client-review record."""
    evidence = template.get("evidence", [{}])
    evidence = (
        evidence[0]
        if isinstance(evidence, list) and evidence and isinstance(evidence[0], dict)
        else {}
    )
    return {
        "priority": priority,
        "document_id": evidence.get("document_id", template["template_id"]),
        "page_id": evidence.get("page_id", template["template_id"]),
        "region_id": evidence.get("region_id", ""),
        "field": field,
        "reason": reason,
        "review_source": "schema_discovery",
        "disposition": "client_review_required",
    }


def write_raw(path, request, response=None, error=None):
    """Retain raw provider output and request provenance without keys."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    value = {"request": request}
    if response is not None:
        value["response"] = response
    if error is not None:
        value["error_type"] = error
    Path(path).write_text(json.dumps(value, indent=2, default=str) + "\n")


def ask_model(
    client, template, labels, model, effort, mapping_context=None, graph_schema_inventory=None
):
    """Make one strict-schema request using current evidence and prior proposals."""
    packet = {
        "template_id": template["template_id"],
        "template_fingerprint": template_fingerprint(template),
        "headers": [item for item in template["headers"] if item["source_label"] in labels],
        "entities": template.get("entities", []),
        "relationships": template.get("relationships", []),
        "source_evidence": {
            key: template[key]
            for key in (
                "document_family",
                "sections",
                "totals",
                "review_flags",
                "profile_quality_diagnostics",
                "handwriting_regions",
            )
            if key in template
        },
        "iterative_mapping_context": mapping_context
        or {
            "iteration": 1,
            "prior_templates": [],
            "prior_mapping_proposals": [],
            "prior_entity_proposals": [],
            "prior_relationship_proposals": [],
            "prior_schema_changes": [],
        },
    }
    if graph_schema_inventory is not None:
        packet["graph_schema_inventory"] = graph_schema_inventory
    response = client.responses.create(
        model=model,
        reasoning={"effort": effort},
        instructions=INSTRUCTIONS,
        input=[{"role": "user", "content": [{"type": "input_text", "text": json.dumps(packet)}]}],
        text={
            "format": {
                "type": "json_schema",
                "name": "semantic_schema_discovery",
                "strict": True,
                "schema": SCHEMA,
            }
        },
    )
    return packet, response


def ask_buddy_model(client, packet, mapping_proposals, model, effort):
    """Independently verify primary mapping proposals against the same source packet."""
    request = {
        "source_template_packet": packet,
        "primary_mapping_proposals": mapping_proposals,
        "proposal_only": True,
        "canonical_mapping_permitted": False,
    }
    response = client.responses.create(
        model=model,
        reasoning={"effort": effort},
        instructions=BUDDY_INSTRUCTIONS,
        input=[{"role": "user", "content": [{"type": "input_text", "text": json.dumps(request)}]}],
        text={
            "format": {
                "type": "json_schema",
                "name": "semantic_schema_buddy_check",
                "strict": True,
                "schema": BUDDY_SCHEMA,
            }
        },
    )
    return request, response


def buddy_lookup(value, labels):
    """Keep only one valid independent decision for each primary source label."""
    if not isinstance(value, dict) or not isinstance(value.get("decisions"), list):
        raise ValueError("Schema buddy response must contain decisions")
    allowed, result = {normalize(label) for label in labels}, {}
    for item in value["decisions"]:
        label = normalize(item.get("source_label", "")) if isinstance(item, dict) else ""
        status = item.get("status") if isinstance(item, dict) else None
        if label in allowed and status in {
            "confirmed",
            "close_needs_review",
            "conflict",
            "unsupported",
        }:
            result.setdefault(label, {"status": status, "rationale": item.get("rationale", "")})
    return result


def source_evidenced(template):
    """Require retained template/page identifiers before an inferred state is emitted."""
    return any(
        isinstance(item, dict)
        and isinstance(item.get("document_id"), str)
        and isinstance(item.get("page_id"), str)
        for item in template.get("evidence", [])
    )


def google_places(name, city_hint, country_code, key, timeout, opener=urlopen):
    """Request candidate business locations; do not select or merge them."""
    payload = {
        "textQuery": " ".join(
            part for part in (name, city_hint) if isinstance(part, str) and part.strip()
        ),
        "pageSize": 5,
    }
    if country_code:
        payload["regionCode"] = country_code
    request = Request(
        PLACES_ENDPOINT,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.addressComponents,places.location,places.businessStatus,places.movedPlace,places.movedPlaceId",
        },
    )
    try:
        with opener(request, timeout=timeout) as response:
            data = json.loads(response.read().decode())
    except (HTTPError, URLError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("google_places_request_failed") from exc
    if not isinstance(data, dict) or not isinstance(data.get("places"), list):
        raise RuntimeError("google_places_response_invalid")
    return data["places"]


def locations(template, enabled, key, limit, timeout):
    """Bound location discovery to explicitly observed dealers and brands."""
    result, findings, sent = [], [], 0
    if not enabled:
        return result, findings, sent
    for entity in template.get("entities", []):
        if not isinstance(entity, dict) or entity.get("entity_type") not in {"dealer", "brand"}:
            continue
        if not isinstance(entity.get("name"), str) or not entity["name"].strip():
            findings.append(
                review(template, "entity.location", "google_places_entity_missing_name")
            )
            continue
        if sent >= limit:
            findings.append(
                review(template, "entity.location", "google_places_request_limit_reached")
            )
            continue
        sent += 1
        try:
            candidates = retry_call(
                lambda entity=entity: google_places(
                    entity["name"],
                    entity.get("city_hint"),
                    entity.get("country_code"),
                    key,
                    timeout,
                ),
                3,
                1.0,
                jitter=True,
                max_backoff_seconds=30.0,
                retryable=lambda error: str(error) == "google_places_request_failed",
            )
            result.append(
                {
                    "entity_id": entity.get("entity_id"),
                    "entity_name": entity["name"],
                    "entity_type": entity["entity_type"],
                    "provider": "google_places",
                    "candidates": candidates,
                    "client_review_required": True,
                }
            )
            if not candidates:
                findings.append(review(template, "entity.location", "google_places_no_candidate"))
        except RuntimeError as exc:
            findings.append(review(template, "entity.location", str(exc)))
    return result, findings, sent


def discover_template(
    template,
    registry,
    client,
    enabled,
    model,
    effort,
    floor,
    raw_dir,
    mapping_context=None,
    graph_schema_inventory=None,
    buddy_enabled=False,
    buddy_client=None,
    buddy_model=None,
    buddy_provider="openai",
):
    """Create versioned mapping, entity, relationship, and schema-change proposals."""
    buddy_model = buddy_model or lane_model("reasoning", buddy_provider)
    template_hash, mappings, findings = template_fingerprint(template), [], []
    unknown = []
    for header in template["headers"]:
        rule = rule_for(registry, template_hash, header["source_label"])
        if rule:
            mappings.append(
                {
                    "proposal_id": "registry:{}:{}".format(
                        template_hash[:12], normalize(header["source_label"])
                    ),
                    "template_id": template["template_id"],
                    "template_fingerprint": template_hash,
                    "source_label": header["source_label"],
                    "canonical_field": rule.get("canonical_field"),
                    "semantic_type": rule.get("semantic_type"),
                    "decision_source": "client_approved_registry",
                    "confidence": 1.0,
                    "client_review_required": False,
                    "status": "reused_approved_mapping",
                }
            )
        else:
            unknown.append(header["source_label"])
    proposal = {
        "mapping_proposals": [],
        "entity_proposals": [],
        "relationship_proposals": [],
        "schema_changes": [],
    }
    model_result_available = False
    decisions, buddy_available = {}, False
    if unknown and enabled:
        raw_path = raw_dir / f"{template_hash[:16]}.json"
        try:
            packet, response = ask_model(
                client,
                template,
                unknown,
                model,
                effort,
                mapping_context,
                graph_schema_inventory,
            )
            write_raw(raw_path, packet, response=response_payload(response))
            proposal = response_json(response)
            model_result_available = True
            if buddy_enabled:
                buddy_path = raw_dir / f"{template_hash[:16]}-buddy.json"
                try:
                    buddy_request, buddy_response = ask_buddy_model(
                        buddy_client,
                        packet,
                        proposal["mapping_proposals"],
                        buddy_model,
                        effort,
                    )
                    write_raw(buddy_path, buddy_request, response=response_payload(buddy_response))
                    decisions = buddy_lookup(
                        buddy_response and response_json(buddy_response), unknown
                    )
                    buddy_available = True
                except Exception as exc:
                    write_raw(
                        buddy_path,
                        {"template_id": template["template_id"]},
                        error=type(exc).__name__,
                    )
                    findings.extend(
                        review(template, f"mapping.{label}", "schema_discovery_buddy_failure")
                        for label in unknown
                    )
        except Exception as exc:
            write_raw(raw_path, {"template_id": template["template_id"]}, error=type(exc).__name__)
            findings.extend(
                review(
                    template,
                    f"mapping.{label}",
                    "schema_discovery_provider_or_schema_failure",
                )
                for label in unknown
            )
    elif unknown:
        findings.extend(
            review(
                template,
                f"mapping.{label}",
                "schema_discovery_unknown_mapping_requires_review",
            )
            for label in unknown
        )
    lookup = {
        normalize(item.get("source_label", "")): item
        for item in proposal["mapping_proposals"]
        if isinstance(item, dict)
    }
    for label in unknown:
        item = lookup.get(normalize(label))
        if item is None:
            if model_result_available:
                findings.append(
                    review(template, f"mapping.{label}", "schema_discovery_mapping_not_proposed")
                )
            continue
        confidence = item["confidence"]
        decision = decisions.get(normalize(label), {})
        buddy_status = decision.get("status", "unsupported") if buddy_available else "not_requested"
        status = "proposed" if confidence >= floor else "low_confidence"
        inference_state = "proposed"
        if buddy_enabled and buddy_available:
            if buddy_status == "confirmed" and confidence >= floor and source_evidenced(template):
                status, inference_state = "inferred_high_confidence", "inferred_high_confidence"
            elif buddy_status != "confirmed":
                inference_state = buddy_status
        mappings.append(
            {
                "proposal_id": f"llm:{template_hash[:12]}:{normalize(label)}",
                "template_id": template["template_id"],
                "template_fingerprint": template_hash,
                "source_label": label,
                "canonical_field": item["canonical_field"],
                "semantic_type": item["semantic_type"],
                "alternatives": item["alternatives"],
                "rationale": item["rationale"],
                "decision_source": "llm",
                "confidence": confidence,
                "client_review_required": True,
                "status": status,
                "inference_state": inference_state,
                "buddy_status": buddy_status,
                "buddy_rationale": decision.get("rationale", ""),
            }
        )
        findings.append(
            review(
                template,
                f"mapping.{label}",
                (
                    "schema_discovery_mapping_inferred_high_confidence_client_approval_required"
                    if inference_state == "inferred_high_confidence"
                    else "schema_discovery_mapping_client_approval_required"
                    if confidence >= floor
                    else "schema_discovery_low_confidence"
                ),
            )
        )
    entities = [
        {**item, "template_id": template["template_id"], "client_review_required": True}
        for item in proposal["entity_proposals"]
    ]
    relations = [
        {**item, "template_id": template["template_id"], "client_review_required": True}
        for item in proposal["relationship_proposals"]
    ]
    changes = [
        {**item, "template_id": template["template_id"], "client_review_required": True}
        for item in proposal["schema_changes"]
    ]
    findings.extend(
        review(template, "schema_or_relationship", "schema_discovery_client_approval_required")
        for _ in entities + relations + changes
    )
    return mappings, entities, relations, changes, findings


def run_discovery(
    templates,
    registry,
    out,
    exceptions,
    handoff,
    raw_dir,
    enabled=False,
    client=None,
    model=None,
    effort="medium",
    floor=0.99,
    places_enabled=False,
    google_key=None,
    google_limit=100,
    google_timeout=15.0,
    graph_schema_inventory=None,
    primary_provider="openai",
    buddy_enabled=False,
    buddy_client=None,
    buddy_model=None,
    buddy_provider="openai",
):
    """Run discovery with hard limits and immutable outputs."""
    model = model or lane_model("reasoning", primary_provider)
    buddy_model = buddy_model or lane_model("reasoning", buddy_provider)
    if (
        effort not in REASONING_EFFORTS
        or not 0 <= floor <= 1
        or google_limit < 1
        or google_timeout <= 0
    ):
        raise ValueError("Invalid semantic-discovery limits")
    targets = [Path(value) for value in (out, exceptions, handoff)]
    if len({path.resolve() for path in targets}) != len(targets) or any(
        path.exists() for path in targets
    ):
        raise ValueError("Output paths must be distinct and new")
    raw_dir = Path(raw_dir)
    if raw_dir.exists() and any(raw_dir.iterdir()):
        raise ValueError("Raw response directory must be new or empty")
    raw_dir.mkdir(parents=True, exist_ok=True)
    if enabled and client is None:
        raise ValueError("Enabled schema discovery requires an LLM client")
    if buddy_enabled and (
        not enabled
        or buddy_client is None
        or primary_provider == buddy_provider
        or buddy_provider not in {"google", "openai", "openrouter", "anthropic"}
    ):
        raise ValueError("Schema buddy requires an enabled independent primary and buddy client")
    if places_enabled and not google_key:
        raise ValueError("Google Places requires a configured API key")
    mappings, entities, relations, changes, findings, place_candidates, sent = (
        [],
        [],
        [],
        [],
        [],
        [],
        0,
    )
    mapping_context = {
        "iteration": 0,
        "prior_templates": [],
        "prior_mapping_proposals": [],
        "prior_entity_proposals": [],
        "prior_relationship_proposals": [],
        "prior_schema_changes": [],
    }
    for template in templates:
        mapping_context["iteration"] += 1
        result = discover_template(
            template,
            registry,
            client,
            enabled,
            model,
            effort,
            floor,
            raw_dir,
            mapping_context,
            graph_schema_inventory,
            buddy_enabled,
            buddy_client,
            buddy_model,
            buddy_provider,
        )
        for collection, key in zip(
            result[:4],
            (
                "prior_mapping_proposals",
                "prior_entity_proposals",
                "prior_relationship_proposals",
                "prior_schema_changes",
            ),
            strict=True,
        ):
            for item in collection:
                item["mapping_iteration"] = mapping_context["iteration"]
                item["mapping_context_templates"] = len(mapping_context["prior_templates"])
            mapping_context[key].extend(collection)
        mapping_context["prior_templates"].append(
            {
                "template_id": template["template_id"],
                "template_fingerprint": template_fingerprint(template),
                "headers": [item["source_label"] for item in template["headers"]],
            }
        )
        mappings.extend(result[0])
        entities.extend(result[1])
        relations.extend(result[2])
        changes.extend(result[3])
        findings.extend(result[4])
        candidates, location_findings, request_count = locations(
            template, places_enabled, google_key, google_limit - sent, google_timeout
        )
        place_candidates.extend(candidates)
        findings.extend(location_findings)
        sent += request_count
    output = {
        "summary": {
            "schema_version": "1.0",
            "generated_at": timestamp(),
            "template_count": len(templates),
            "mapping_count": len(mappings),
            "client_review_items": len(findings),
            "gate_status": "blocked_pending_client_review" if findings else "clear",
            "findings": [
                "LLM and Google results are proposals; registry updates require explicit client decisions."
            ],
            "iterative_mapping": {
                "enabled": True,
                "iterations": len(templates),
                "templates_considered": len(mapping_context["prior_templates"]),
                "prior_proposals_carried_forward": sum(
                    len(mapping_context[key])
                    for key in (
                        "prior_mapping_proposals",
                        "prior_entity_proposals",
                        "prior_relationship_proposals",
                        "prior_schema_changes",
                    )
                ),
                "approval_boundary": "client_approval_required",
            },
            "graph_schema_inventory": {
                "enabled": graph_schema_inventory is not None,
                "graph_sha256": (
                    graph_schema_inventory["graph_sha256"]
                    if graph_schema_inventory is not None
                    else None
                ),
                "artifact_sha256": (
                    graph_schema_inventory["artifact_sha256"]
                    if graph_schema_inventory is not None
                    else None
                ),
                "field_count": (
                    len(graph_schema_inventory["fields"])
                    if graph_schema_inventory is not None
                    else 0
                ),
                "candidate_field_count": (
                    len(graph_schema_inventory["retained_candidate_fields"])
                    if graph_schema_inventory is not None
                    else 0
                ),
                "inventory_type": (
                    graph_schema_inventory["inventory_type"]
                    if graph_schema_inventory is not None
                    else None
                ),
                "mapping_evidence": "source_template_required",
            },
        },
        "mapping_proposals": mappings,
        "dealer_proposals": [item for item in entities if item.get("entity_type") == "dealer"],
        "brand_proposals": [item for item in entities if item.get("entity_type") == "brand"],
        "entity_proposals": entities,
        "relationship_proposals": relations,
        "location_candidates": place_candidates,
        "schema_change_queue": changes,
        "review_items": findings,
    }
    provider = {
        "adapter_type": "schema_discovery",
        "engine": f"{primary_provider}/{model}" if enabled else "schema_discovery/deterministic",
        "template_count": len(templates),
        "policy": {
            "decision_mode": "proposal_only",
            "client_approval_permitted": False,
            "automatic_new_field_creation": False,
        },
        "model_configuration": {
            "primary": {"provider": primary_provider, "model": model, "reasoning_effort": effort},
            "buddy": {
                "enabled": buddy_enabled,
                "provider": buddy_provider if buddy_enabled else None,
                "model": buddy_model if buddy_enabled else None,
            },
        },
        "minimum_confidence": floor,
        "google_places": {
            "enabled": places_enabled,
            "requests_sent": sent,
            "per_run_limit": google_limit,
        },
        "raw_response_directory": str(raw_dir),
        "graph_schema_inventory": (
            {
                "graph_sha256": graph_schema_inventory["graph_sha256"],
                "artifact_sha256": graph_schema_inventory["artifact_sha256"],
                "field_count": len(graph_schema_inventory["fields"]),
                "candidate_field_count": len(graph_schema_inventory["retained_candidate_fields"]),
                "inventory_type": graph_schema_inventory["inventory_type"],
                "proposal_only": True,
                "canonical_mapping_permitted": False,
            }
            if graph_schema_inventory is not None
            else None
        ),
    }
    Path(out).write_text(json.dumps(output, indent=2) + "\n")
    Path(exceptions).write_text(
        json.dumps({"summary": {"count": len(findings)}, "exceptions": findings}, indent=2) + "\n"
    )
    Path(handoff).write_text(json.dumps(provider, indent=2) + "\n")
    return output


# --- Slot equivalence -------------------------------------------------------
#
# Two independent engines frequently read the same printed value and file it
# under two different controlled fields: on a real corpus, one engine returned
# 12497 as ``lines[0].item_code`` while the other returned the identical 12497
# as ``lines[0].job_number``. Consensus is right to refuse those -- it compares
# a slot at a time, and it cannot know the slots mean the same thing -- but the
# result is a disagreement about vocabulary reported as a disagreement about a
# fact. 314 of 875 non-extension exceptions on that corpus were this pattern.
#
# The equivalence is a property of the client's documents, not of the schema, so
# it cannot be hard-coded: a controlled vocabulary broad enough for unknown
# client schemas will always offer more than one plausible home for a fact. It is
# discovered per client, proposed with independent confirmation, and applied only
# from a client-approved registry rule -- the same boundary every other mapping
# in this repository crosses.
#
# Two facts about the detector matter for reading its output. It pairs
# placements only within one container (the same line row, or the header), so a
# value shared by two unrelated rows is never a candidate. And it fires only on
# an *identical* normalized value: engines that read different values are a
# genuine disagreement and stay in the exception queue where consensus put them.
SLOT_EQUIVALENCE_MIN_VALUE_LENGTH = 3
SLOT_EQUIVALENCE_INSTRUCTIONS = """You are an evidence-bound business data-model analyst judging whether two controlled schema fields denote the same business fact for one client's documents. You are given observations in which two independent extraction engines read the identical printed value from the same document container and filed it under two different fields. Decide only from the supplied observations. Return equivalent only when the observations show one business fact with two schema homes; return distinct when the fields denote genuinely different facts that happen to share a value; return needs_review when the evidence is insufficient or mixed. ACK, order reference, and job number are not interchangeable facts, and a shared value alone is not proof of equivalence. Choose preferred_field only from the two supplied fields. Never approve a rule, never invent a field, and never treat repetition as authorization."""
SLOT_EQUIVALENCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["slot_proposals"],
    "properties": {
        "slot_proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "field_a",
                    "field_b",
                    "relationship",
                    "preferred_field",
                    "confidence",
                    "rationale",
                ],
                "properties": {
                    "field_a": {"type": "string"},
                    "field_b": {"type": "string"},
                    "relationship": {
                        "type": "string",
                        "enum": ["equivalent", "distinct", "needs_review"],
                    },
                    "preferred_field": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                },
            },
        }
    },
}
SLOT_EQUIVALENCE_BUDDY_INSTRUCTIONS = """You are an independent verifier of proposed schema-field equivalences. Inspect the immutable observation packet and each primary proposal. Classify a proposal as confirmed only when the retained observations support the claim that both fields denote one business fact for these documents; classify close_needs_review for plausible ambiguity, conflict for a material disagreement, and unsupported when the observations do not support it. A shared value is evidence of a shared reading, not proof of a shared meaning. Never approve, invent, normalize, or create a canonical fact."""
SLOT_EQUIVALENCE_BUDDY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decisions"],
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["slot_pair", "status", "rationale"],
                "properties": {
                    "slot_pair": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["confirmed", "close_needs_review", "conflict", "unsupported"],
                    },
                    "rationale": {"type": "string"},
                },
            },
        }
    },
}


def load_consensus(path):
    """Read a consensus artifact, requiring the document detail the detector needs."""
    data = json.loads(Path(path).read_text())
    documents = data.get("documents") if isinstance(data, dict) else None
    if (
        not isinstance(documents, list)
        or not documents
        or not all(
            isinstance(item, dict)
            and isinstance(item.get("document_id"), str)
            and isinstance(item.get("fields"), dict)
            for item in documents
        )
    ):
        raise ValueError("Consensus input must contain documents with document_id and fields")
    return documents


def load_consensus_exceptions(path):
    """Read the matching consensus exception queue, keyed by document and field."""
    data = json.loads(Path(path).read_text())
    items = data.get("exceptions") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("Consensus exceptions input must contain an exceptions list")
    placements = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("engines"), dict):
            continue
        document_id, field = item.get("document_id"), item.get("field")
        if isinstance(document_id, str) and isinstance(field, str):
            placements[(document_id, field)] = item["engines"]
    return placements


def container_of(path):
    """Split a flattened consensus path into its container and leaf field name."""
    head, _, leaf = path.rpartition(".")
    return head, leaf or path


def engine_placements(document, exception_engines):
    """Recover which engine put which value in which slot for one document.

    The consensus field record names the engines behind its winning cluster but
    not the engines behind the losing ones, so the exception queue -- which
    records an exact value for every engine that produced the field -- is the
    authority wherever the two overlap.
    """
    placements = {}
    for path, field in document["fields"].items():
        if not isinstance(field, dict):
            continue
        engines = exception_engines.get((document["document_id"], path))
        if isinstance(engines, dict):
            for engine, value in engines.items():
                placements.setdefault(engine, {})[path] = value
            continue
        candidates = field.get("candidate_values") or [field.get("value")]
        value = candidates[0] if candidates else None
        for engine in field.get("agreeing_engines") or []:
            placements.setdefault(engine, {})[path] = value
    return placements


def slot_evidence_class(engines_a, engines_b):
    """Name the kind of evidence a shared value provides about two slots.

    The two shapes are not equally strong and must not be reported as one.
    ``cross_engine_placement`` is two independent engines filing one value under
    two different names -- neither corroborates the other's choice, and the
    disagreement is purely about vocabulary. ``intra_engine_duplicate`` is a
    single engine writing the value into both slots, usually because the schema
    offers two plausible homes and the engine declines to choose; the second
    engine typically fills one of them, so the fact itself is corroborated and
    only its name is open.

    Every slot reaching this function carries at least one engine, so the two
    cases are exhaustive: either each slot holds an engine the other lacks, or
    one set contains the other and they necessarily share an engine.
    """
    if engines_a - engines_b and engines_b - engines_a:
        return "cross_engine_placement"
    return "intra_engine_duplicate"


def slot_observations(document, exception_engines):
    """Find identical values two engines filed under two fields of one container."""
    placements = engine_placements(document, exception_engines)
    by_slot = {}
    for engine, paths in placements.items():
        for path, value in paths.items():
            text = normalize(value)
            if not text or len(text) < SLOT_EQUIVALENCE_MIN_VALUE_LENGTH:
                continue
            container, leaf = container_of(path)
            by_slot.setdefault((container, text), {}).setdefault(leaf, set()).add(engine)
    observations = []
    for (container, text), leaves in by_slot.items():
        names = sorted(leaves)
        for index, field_a in enumerate(names):
            for field_b in names[index + 1 :]:
                engines_a, engines_b = leaves[field_a], leaves[field_b]
                evidence_class = slot_evidence_class(engines_a, engines_b)
                observations.append(
                    {
                        "document_id": document["document_id"],
                        "container": container,
                        "field_a": field_a,
                        "field_b": field_b,
                        "value": text,
                        "evidence_class": evidence_class,
                        "engines_field_a": sorted(engines_a),
                        "engines_field_b": sorted(engines_b),
                    }
                )
    return observations


def slot_candidates(observations, min_observations, min_distinct_values):
    """Group observations into slot pairs strong enough to be worth proposing.

    A coincidental collision -- a quantity of 1 meeting a line number of 1 --
    repeats one trivial value, while a real equivalence carries many different
    values through the same two slots. Requiring distinct values is what
    separates them, and it is cheaper than asking a model about every pair.
    """
    grouped = {}
    for item in observations:
        key = (item["field_a"], item["field_b"])
        grouped.setdefault(key, []).append(item)
    candidates = []
    for (field_a, field_b), items in sorted(grouped.items()):
        values = sorted({item["value"] for item in items})
        if len(items) < min_observations or len(values) < min_distinct_values:
            continue
        classes = sorted({item["evidence_class"] for item in items})
        candidates.append(
            {
                "slot_pair": f"{field_a}|{field_b}",
                "field_a": field_a,
                "field_b": field_b,
                "observation_count": len(items),
                "distinct_value_count": len(values),
                "evidence_classes": classes,
                "cross_engine_observations": sum(
                    1 for item in items if item["evidence_class"] == "cross_engine_placement"
                ),
                "document_ids": sorted({item["document_id"] for item in items}),
                "observations": items,
            }
        )
    return candidates


SLOT_EQUIVALENCE_MAX_PACKET_OBSERVATIONS = 25


def slot_rule_for(registry, field_a, field_b):
    """Reuse only an exact client-approved equivalence for this ordered slot pair."""
    for rule in registry["rules"]:
        if (
            registry_approval.is_approved(rule)
            and rule.get("rule_type") == "slot_equivalence"
            and sorted([rule.get("field_a", ""), rule.get("field_b", "")])
            == sorted([field_a, field_b])
        ):
            return rule
    return None


def slot_review(candidate, reason, priority="high"):
    """Create the standard client-review record for a slot-equivalence finding."""
    return {
        "priority": priority,
        "document_id": candidate["document_ids"][0],
        "page_id": candidate["document_ids"][0],
        "region_id": "",
        "field": candidate["slot_pair"],
        "reason": reason,
        "review_source": "slot_equivalence",
        "disposition": "client_review_required",
    }


def slot_packet(candidate):
    """Build the bounded, deterministic evidence packet for one slot pair."""
    observations = sorted(
        candidate["observations"],
        key=lambda item: (item["document_id"], item["container"], item["value"]),
    )
    return {
        "slot_pair": candidate["slot_pair"],
        "field_a": candidate["field_a"],
        "field_b": candidate["field_b"],
        "observation_count": candidate["observation_count"],
        "distinct_value_count": candidate["distinct_value_count"],
        "cross_engine_observations": candidate["cross_engine_observations"],
        "evidence_classes": candidate["evidence_classes"],
        "document_ids": candidate["document_ids"],
        "observations_sent": min(len(observations), SLOT_EQUIVALENCE_MAX_PACKET_OBSERVATIONS),
        "observations": observations[:SLOT_EQUIVALENCE_MAX_PACKET_OBSERVATIONS],
        "proposal_only": True,
        "canonical_mapping_permitted": False,
    }


def ask_slot_model(client, packet, model, effort):
    """Make one strict-schema request judging a single slot pair."""
    response = client.responses.create(
        model=model,
        reasoning={"effort": effort},
        instructions=SLOT_EQUIVALENCE_INSTRUCTIONS,
        input=[{"role": "user", "content": [{"type": "input_text", "text": json.dumps(packet)}]}],
        text={
            "format": {
                "type": "json_schema",
                "name": "slot_equivalence_proposal",
                "strict": True,
                "schema": SLOT_EQUIVALENCE_SCHEMA,
            }
        },
    )
    return response


def ask_slot_buddy_model(client, packet, proposals, model, effort):
    """Independently verify primary slot proposals against the same packet."""
    request = {
        "slot_observation_packet": packet,
        "primary_slot_proposals": proposals,
        "proposal_only": True,
        "canonical_mapping_permitted": False,
    }
    response = client.responses.create(
        model=model,
        reasoning={"effort": effort},
        instructions=SLOT_EQUIVALENCE_BUDDY_INSTRUCTIONS,
        input=[{"role": "user", "content": [{"type": "input_text", "text": json.dumps(request)}]}],
        text={
            "format": {
                "type": "json_schema",
                "name": "slot_equivalence_buddy_check",
                "strict": True,
                "schema": SLOT_EQUIVALENCE_BUDDY_SCHEMA,
            }
        },
    )
    return request, response


def slot_buddy_lookup(value, pairs):
    """Keep one valid independent decision for each proposed slot pair."""
    if not isinstance(value, dict) or not isinstance(value.get("decisions"), list):
        raise ValueError("Slot-equivalence buddy response must contain decisions")
    allowed, result = set(pairs), {}
    for item in value["decisions"]:
        pair = item.get("slot_pair") if isinstance(item, dict) else None
        status = item.get("status") if isinstance(item, dict) else None
        if pair in allowed and status in {
            "confirmed",
            "close_needs_review",
            "conflict",
            "unsupported",
        }:
            result.setdefault(pair, {"status": status, "rationale": item.get("rationale", "")})
    return result


def slot_proposal_records(candidate, parsed, floor):
    """Turn one strict-schema response into retained proposals and findings."""
    proposals, findings = [], []
    pair = candidate["slot_pair"]
    seen = False
    for item in parsed.get("slot_proposals", []):
        if not isinstance(item, dict):
            continue
        fields = sorted([str(item.get("field_a", "")), str(item.get("field_b", ""))])
        if fields != sorted([candidate["field_a"], candidate["field_b"]]):
            findings.append(
                slot_review(candidate, "Model answered about fields that were not supplied")
            )
            continue
        seen = True
        preferred = str(item.get("preferred_field", ""))
        confidence = item.get("confidence")
        relationship = item.get("relationship")
        # The preferred field decides which slot a client-approved rule would
        # keep. A name outside the supplied pair would silently invent one.
        if preferred not in (candidate["field_a"], candidate["field_b"]):
            findings.append(
                slot_review(candidate, "Proposed preferred field is not one of the supplied slots")
            )
            continue
        proposal = {
            "proposal_id": "slot-"
            + hashlib.sha256(f"{pair}\x1f{preferred}".encode()).hexdigest()[:16],
            "slot_pair": pair,
            "field_a": candidate["field_a"],
            "field_b": candidate["field_b"],
            "relationship": relationship,
            "preferred_field": preferred,
            "confidence": confidence,
            "rationale": str(item.get("rationale", "")),
            "observation_count": candidate["observation_count"],
            "distinct_value_count": candidate["distinct_value_count"],
            "cross_engine_observations": candidate["cross_engine_observations"],
            "evidence_classes": candidate["evidence_classes"],
            "document_ids": candidate["document_ids"],
            "decision_source": "llm",
            "decision_mode": "proposal_only",
            "client_approval_required": True,
        }
        proposals.append(proposal)
        if relationship != "equivalent":
            findings.append(
                slot_review(
                    candidate,
                    f"Slot pair classified {relationship}; consensus exceptions stand",
                    priority="medium",
                )
            )
        elif not isinstance(confidence, (int, float)) or confidence < floor:
            findings.append(
                slot_review(candidate, "Equivalence proposed below the configured confidence floor")
            )
        elif not candidate["cross_engine_observations"]:
            # Only one engine ever used both names. That is an engine declining
            # to choose, not two engines agreeing, and it is the weaker case.
            findings.append(
                slot_review(
                    candidate,
                    "Equivalence rests only on one engine duplicating a value into both slots",
                )
            )
    if not seen:
        findings.append(slot_review(candidate, "Model returned no usable proposal for this pair"))
    return proposals, findings


def run_slot_equivalence(
    documents,
    exception_engines,
    registry,
    out,
    exceptions,
    handoff,
    raw_dir,
    enabled=False,
    client=None,
    model=None,
    effort="medium",
    floor=0.99,
    min_observations=2,
    min_distinct_values=2,
    primary_provider="openai",
    buddy_enabled=False,
    buddy_client=None,
    buddy_model=None,
    buddy_provider="openai",
):
    """Propose client-scoped schema-slot equivalences from retained consensus evidence."""
    model = model or lane_model("reasoning", primary_provider)
    buddy_model = buddy_model or lane_model("reasoning", buddy_provider)
    if effort not in REASONING_EFFORTS or not 0 <= floor <= 1:
        raise ValueError("Invalid slot-equivalence limits")
    if min_observations < 1 or min_distinct_values < 1:
        raise ValueError("Slot-equivalence thresholds must be positive")
    targets = [Path(value) for value in (out, exceptions, handoff)]
    if len({path.resolve() for path in targets}) != len(targets) or any(
        path.exists() for path in targets
    ):
        raise ValueError("Output paths must be distinct and new")
    raw_dir = Path(raw_dir)
    if raw_dir.exists() and any(raw_dir.iterdir()):
        raise ValueError("Raw response directory must be new or empty")
    raw_dir.mkdir(parents=True, exist_ok=True)
    if enabled and client is None:
        raise ValueError("Enabled slot equivalence requires an LLM client")
    if buddy_enabled and (
        not enabled
        or buddy_client is None
        or primary_provider == buddy_provider
        or buddy_provider not in {"google", "openai", "openrouter", "anthropic"}
    ):
        raise ValueError(
            "Slot-equivalence buddy requires an enabled independent primary and buddy client"
        )

    observations = []
    for document in documents:
        observations.extend(slot_observations(document, exception_engines))
    candidates = slot_candidates(observations, min_observations, min_distinct_values)

    proposals, findings, skipped = [], [], []
    for candidate in candidates:
        approved = slot_rule_for(registry, candidate["field_a"], candidate["field_b"])
        if approved is not None:
            # An approved rule already answers this pair. Asking again would
            # spend money to re-derive a client decision that already exists.
            skipped.append(
                {"slot_pair": candidate["slot_pair"], "rule_id": approved.get("rule_id")}
            )
            continue
        packet = slot_packet(candidate)
        raw_path = (
            raw_dir
            / f"slot_{hashlib.sha256(candidate['slot_pair'].encode()).hexdigest()[:16]}.json"
        )
        if not enabled:
            findings.append(
                slot_review(
                    candidate,
                    "Slot-equivalence lane is disabled; the pair is retained unresolved",
                    priority="medium",
                )
            )
            write_raw(raw_path, packet)
            continue
        try:
            response = ask_slot_model(client, packet, model, effort)
            parsed = response_json(response)
        except (ValueError, RuntimeError, OSError) as exc:
            write_raw(raw_path, packet, error=type(exc).__name__)
            findings.append(slot_review(candidate, "Slot-equivalence provider call failed"))
            continue
        write_raw(raw_path, packet, response=response_payload(response))
        pair_proposals, pair_findings = slot_proposal_records(candidate, parsed, floor)
        findings.extend(pair_findings)
        if buddy_enabled and pair_proposals:
            try:
                request, buddy_response = ask_slot_buddy_model(
                    buddy_client, packet, pair_proposals, buddy_model, effort
                )
                decisions = slot_buddy_lookup(
                    response_json(buddy_response), [candidate["slot_pair"]]
                )
            except (ValueError, RuntimeError, OSError) as exc:
                write_raw(raw_path.with_suffix(".buddy.json"), packet, error=type(exc).__name__)
                decisions = {}
            else:
                write_raw(
                    raw_path.with_suffix(".buddy.json"),
                    request,
                    response=response_payload(buddy_response),
                )
            decision = decisions.get(candidate["slot_pair"])
            for proposal in pair_proposals:
                proposal["buddy_status"] = decision["status"] if decision else "unverified"
                proposal["buddy_rationale"] = decision["rationale"] if decision else ""
                proposal["buddy_provider"] = buddy_provider
                proposal["buddy_model"] = buddy_model
                if proposal["buddy_status"] != "confirmed":
                    findings.append(
                        slot_review(
                            candidate,
                            f"Independent verifier returned {proposal['buddy_status']}",
                        )
                    )
        elif pair_proposals:
            for proposal in pair_proposals:
                # Without an independent verifier a proposal is one model's
                # opinion. It is retained, and it is never eligible on its own.
                proposal["buddy_status"] = "not_requested"
                proposal["buddy_rationale"] = ""
                proposal["buddy_provider"] = None
                proposal["buddy_model"] = None
            findings.append(
                slot_review(
                    candidate,
                    "Slot equivalence proposed without independent confirmation",
                )
            )
        proposals.extend(pair_proposals)

    eligible = [
        proposal
        for proposal in proposals
        if proposal["relationship"] == "equivalent"
        and proposal.get("buddy_status") == "confirmed"
        and isinstance(proposal["confidence"], (int, float))
        and proposal["confidence"] >= floor
        and proposal["cross_engine_observations"] > 0
    ]
    output = {
        "summary": {
            "schema_version": "1.0",
            "generated_at": timestamp(),
            "document_count": len(documents),
            "observation_count": len(observations),
            "candidate_pair_count": len(candidates),
            "proposal_count": len(proposals),
            "approval_eligible_count": len(eligible),
            "already_approved_pairs": skipped,
            "client_review_items": len(findings),
            "gate_status": "blocked_pending_client_review" if findings else "clear",
            "thresholds": {
                "minimum_observations": min_observations,
                "minimum_distinct_values": min_distinct_values,
                "minimum_confidence": floor,
            },
            "findings": [
                "Slot equivalences are proposals; registry updates require explicit client decisions."
            ],
        },
        "slot_proposals": proposals,
        "slot_candidates": candidates,
        "review_items": findings,
    }
    provider = {
        "adapter_type": "slot_equivalence",
        "engine": f"{primary_provider}/{model}" if enabled else "slot_equivalence/deterministic",
        "document_count": len(documents),
        "policy": {
            "decision_mode": "proposal_only",
            "client_approval_permitted": False,
            "automatic_field_collapse": False,
        },
        "model_configuration": {
            "primary": {"provider": primary_provider, "model": model, "reasoning_effort": effort},
            "buddy": {
                "enabled": buddy_enabled,
                "provider": buddy_provider if buddy_enabled else None,
                "model": buddy_model if buddy_enabled else None,
            },
        },
        "minimum_confidence": floor,
        "raw_response_directory": str(raw_dir),
    }
    Path(out).write_text(json.dumps(output, indent=2) + "\n")
    Path(exceptions).write_text(
        json.dumps({"summary": {"count": len(findings)}, "exceptions": findings}, indent=2) + "\n"
    )
    Path(handoff).write_text(json.dumps(provider, indent=2) + "\n")
    return output


def slot_equivalence_rule(proposal, provenance):
    """Build a client-approved slot-equivalence rule, or name why it is refused.

    A rule here collapses two schema slots for one client, so it is held to the
    evidence the proposal itself records. An equivalence no independent verifier
    confirmed is one model's opinion, and letting that become schema would make
    the buddy lane decorative; the refusal is returned rather than raised so the
    update log can show the client exactly what was declined and why.
    """
    if proposal.get("relationship") != "equivalent":
        return None, f"relationship is {proposal.get('relationship')}, not equivalent"
    if proposal.get("buddy_status") != "confirmed":
        return None, f"independent verifier status is {proposal.get('buddy_status')}"
    return {
        "rule_id": "rule-" + hashlib.sha256(proposal["proposal_id"].encode()).hexdigest()[:16],
        **provenance,
        "rule_type": "slot_equivalence",
        "field_a": proposal["field_a"],
        "field_b": proposal["field_b"],
        "preferred_field": proposal["preferred_field"],
        "evidence_classes": proposal["evidence_classes"],
        "observation_count": proposal["observation_count"],
        "cross_engine_observations": proposal["cross_engine_observations"],
        "buddy_status": proposal["buddy_status"],
        "effective_from": timestamp(),
        "approved_from_proposal": proposal["proposal_id"],
    }, None


def update_registry(registry, discovery, decisions):
    """Make a new registry snapshot only from LLM proposals explicitly approved by a client."""
    proposals = {
        item.get("proposal_id"): item
        for item in discovery.get("mapping_proposals", [])
        if isinstance(item, dict)
    }
    slot_proposals = {
        item.get("proposal_id"): item
        for item in discovery.get("slot_proposals", [])
        if isinstance(item, dict)
    }
    if not isinstance(decisions.get("decisions"), list):
        raise ValueError("Decisions must contain a decisions list")
    provenance = registry_approval.approval_provenance(decisions)
    rules, updates, refused = list(registry["rules"]), [], []
    for decision in decisions["decisions"]:
        if not isinstance(decision, dict) or decision.get("decision") != "approve":
            continue
        proposal_id = decision.get("proposal_id")
        proposal = proposals.get(proposal_id)
        slot_proposal = slot_proposals.get(proposal_id)
        if proposal is not None and proposal.get("decision_source") == "llm":
            rule = {
                "rule_id": "rule-"
                + hashlib.sha256(proposal["proposal_id"].encode()).hexdigest()[:16],
                **provenance,
                "rule_type": "source_label_mapping",
                "template_fingerprint": proposal["template_fingerprint"],
                "source_label": proposal["source_label"],
                "canonical_field": proposal["canonical_field"],
                "semantic_type": proposal["semantic_type"],
                "effective_from": timestamp(),
                "approved_from_proposal": proposal["proposal_id"],
            }
        elif slot_proposal is not None and slot_proposal.get("decision_source") == "llm":
            rule, reason = slot_equivalence_rule(slot_proposal, provenance)
            if rule is None:
                refused.append({"proposal_id": proposal_id, "reason": reason})
                continue
        else:
            continue
        rules.append(rule)
        updates.append(rule)
    return {
        "registry_version": int(registry.get("registry_version", 0)) + 1,
        "generated_at": timestamp(),
        "rules": rules,
        "update_log": updates,
        "refused_decisions": refused,
    }


def main():
    load_project_env()
    parser = argparse.ArgumentParser(
        description="Create review-required semantic schema proposals."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("discover")
    discover.add_argument(
        "input",
        help=(
            "Source-template observation artifact whose printed labels are scanned for "
            "candidate semantic mappings. Build it with template_observations.py; an "
            "extracted-record or consensus artifact is refused."
        ),
    )
    discover.add_argument("--registry")
    discover.add_argument("--out", required=True)
    discover.add_argument("--exceptions", required=True)
    discover.add_argument("--handoff-out", required=True)
    discover.add_argument("--raw-dir", required=True)
    discover.add_argument(
        "--provider",
        default=env_value("SCHEMA_DISCOVERY_PROVIDER", "google"),
        choices=("anthropic", "google", "openai", "openrouter"),
    )
    discover.add_argument(
        "--graph-schema-inventory",
        help="Proposal-only evidence_graph.py schema output; source templates remain required mapping evidence.",
    )
    discover.add_argument(
        "--enable", action="store_true", default=env_bool("SCHEMA_DISCOVERY_ENABLED", False)
    )
    discover.add_argument(
        "--model", default=env_value("SCHEMA_DISCOVERY_MODEL", lane_model("reasoning", "google"))
    )
    discover.add_argument(
        "--buddy",
        action=argparse.BooleanOptionalAction,
        default=env_bool("SCHEMA_DISCOVERY_BUDDY_ENABLED", True),
        help=(
            "Run the independent buddy pass (--no-buddy skips it). This is an on/off switch, not a "
            "provider name -- the buddy provider itself comes from --buddy-provider."
        ),
    )
    discover.add_argument(
        "--buddy-provider",
        default=env_value("SCHEMA_DISCOVERY_BUDDY_PROVIDER", "openai"),
        choices=("anthropic", "google", "openai", "openrouter"),
    )
    discover.add_argument(
        "--buddy-model",
        default=env_value(
            "SCHEMA_DISCOVERY_BUDDY_MODEL",
            lane_model("reasoning", env_value("SCHEMA_DISCOVERY_BUDDY_PROVIDER", "openai")),
        ),
    )
    discover.add_argument(
        "--reasoning-effort", default=env_value("SCHEMA_DISCOVERY_REASONING_EFFORT", "medium")
    )
    discover.add_argument(
        "--min-confidence",
        type=float,
        default=env_float("SCHEMA_DISCOVERY_MIN_CONFIDENCE", 0.99),
        help="Minimum proposal confidence retained. Client approval is still required for every mapping.",
    )
    discover.add_argument(
        "--google-places",
        action=argparse.BooleanOptionalAction,
        default=env_bool("GOOGLE_PLACES_ENABLED", False),
        help="Enable Google Places candidate lookups. Candidates are proposals, never an approved mapping.",
    )
    discover.add_argument(
        "--google-api-key-env",
        default=env_value("GOOGLE_PLACES_API_KEY_ENV", "GOOGLE_PLACES_API_KEY"),
        help="Name of the environment variable holding the Places API key.",
    )
    discover.add_argument(
        "--max-google-requests",
        type=int,
        default=env_int("GOOGLE_PLACES_MAX_REQUESTS", 100),
        help="Maximum Places requests this invocation may make.",
    )
    discover.add_argument(
        "--google-timeout-seconds",
        type=float,
        default=env_float("GOOGLE_PLACES_TIMEOUT_SECONDS", 15.0),
        help="Per-request timeout, in seconds, for a Places call.",
    )
    slots = sub.add_parser("slot-equivalence")
    slots.add_argument(
        "consensus", help="consensus.py output whose fields are scanned for slot collisions."
    )
    slots.add_argument(
        "consensus_exceptions",
        help="Matching consensus exception queue supplying each engine's exact value per field.",
    )
    slots.add_argument("--registry")
    slots.add_argument("--out", required=True)
    slots.add_argument("--exceptions", required=True)
    slots.add_argument("--handoff-out", required=True)
    slots.add_argument("--raw-dir", required=True)
    slots.add_argument(
        "--provider",
        default=env_value("SLOT_EQUIVALENCE_PROVIDER", "google"),
        choices=("anthropic", "google", "openai", "openrouter"),
    )
    slots.add_argument(
        "--enable", action="store_true", default=env_bool("SLOT_EQUIVALENCE_ENABLED", False)
    )
    slots.add_argument(
        "--model", default=env_value("SLOT_EQUIVALENCE_MODEL", lane_model("reasoning", "google"))
    )
    slots.add_argument(
        "--buddy",
        action=argparse.BooleanOptionalAction,
        default=env_bool("SLOT_EQUIVALENCE_BUDDY_ENABLED", True),
        help=(
            "Run the independent buddy pass (--no-buddy skips it). This is an on/off switch, not a "
            "provider name -- the buddy provider itself comes from --buddy-provider."
        ),
    )
    slots.add_argument(
        "--buddy-provider",
        default=env_value("SLOT_EQUIVALENCE_BUDDY_PROVIDER", "openai"),
        choices=("anthropic", "google", "openai", "openrouter"),
    )
    slots.add_argument(
        "--buddy-model",
        default=env_value("SLOT_EQUIVALENCE_BUDDY_MODEL", lane_model("reasoning", "openai")),
    )
    slots.add_argument(
        "--reasoning-effort", default=env_value("SLOT_EQUIVALENCE_REASONING_EFFORT", "medium")
    )
    slots.add_argument(
        "--min-confidence",
        type=float,
        default=env_float("SLOT_EQUIVALENCE_MIN_CONFIDENCE", 0.99),
        help="Minimum proposal confidence retained. Client approval is still required for every equivalence.",
    )
    slots.add_argument(
        "--min-observations",
        type=int,
        default=env_int("SLOT_EQUIVALENCE_MIN_OBSERVATIONS", 2),
        help="Shared-value observations a slot pair needs before it is proposed at all.",
    )
    slots.add_argument(
        "--min-distinct-values",
        type=int,
        default=env_int("SLOT_EQUIVALENCE_MIN_DISTINCT_VALUES", 2),
        help="Distinct shared values a slot pair needs, which separates a real equivalence from one repeated coincidence.",
    )

    update = sub.add_parser("registry-update")
    update.add_argument("registry", help="Existing approved semantic-mapping registry to amend.")
    update.add_argument("discovery")
    update.add_argument(
        "decisions", help="Client decision artifact naming which discovered mappings are approved."
    )
    update.add_argument("--out", required=True)
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        if args.command == "registry-update":
            result = update_registry(
                load_registry(args.registry),
                json.loads(Path(args.discovery).read_text()),
                json.loads(Path(args.decisions).read_text()),
            )
            if Path(args.out).exists():
                raise ValueError("Registry output must be new")
            Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
        elif args.command == "slot-equivalence":
            client = (
                build_client(
                    env_value(
                        "SLOT_EQUIVALENCE_CREDENTIAL_ENV",
                        provider_credential_env(args.provider),
                    ),
                    env_float("SLOT_EQUIVALENCE_TIMEOUT_SECONDS", 120.0),
                    env_int("SLOT_EQUIVALENCE_MAX_RETRIES", 2),
                    provider=args.provider,
                )
                if args.enable
                else None
            )
            buddy_client = (
                build_client(
                    env_value(
                        "SLOT_EQUIVALENCE_BUDDY_CREDENTIAL_ENV",
                        provider_credential_env(args.buddy_provider),
                    ),
                    env_float("SLOT_EQUIVALENCE_BUDDY_TIMEOUT_SECONDS", 120.0),
                    env_int("SLOT_EQUIVALENCE_BUDDY_MAX_RETRIES", 2),
                    provider=args.buddy_provider,
                )
                if args.enable and args.buddy
                else None
            )
            result = run_slot_equivalence(
                load_consensus(args.consensus),
                load_consensus_exceptions(args.consensus_exceptions),
                load_registry(args.registry),
                args.out,
                args.exceptions,
                args.handoff_out,
                args.raw_dir,
                args.enable,
                client,
                args.model,
                args.reasoning_effort,
                args.min_confidence,
                args.min_observations,
                args.min_distinct_values,
                args.provider,
                args.buddy if args.enable else False,
                buddy_client,
                args.buddy_model,
                args.buddy_provider,
            )
        else:
            client = (
                build_client(
                    env_value(
                        "SCHEMA_DISCOVERY_CREDENTIAL_ENV",
                        provider_credential_env(args.provider),
                    ),
                    env_float("SCHEMA_DISCOVERY_TIMEOUT_SECONDS", 120.0),
                    env_int("SCHEMA_DISCOVERY_MAX_RETRIES", 2),
                    provider=args.provider,
                )
                if args.enable
                else None
            )
            buddy_client = (
                build_client(
                    env_value(
                        "SCHEMA_DISCOVERY_BUDDY_CREDENTIAL_ENV",
                        provider_credential_env(args.buddy_provider),
                    ),
                    env_float("SCHEMA_DISCOVERY_BUDDY_TIMEOUT_SECONDS", 120.0),
                    env_int("SCHEMA_DISCOVERY_BUDDY_MAX_RETRIES", 2),
                    provider=args.buddy_provider,
                )
                if args.enable and args.buddy
                else None
            )
            result = run_discovery(
                load_templates(args.input),
                load_registry(args.registry),
                args.out,
                args.exceptions,
                args.handoff_out,
                args.raw_dir,
                args.enable,
                client,
                args.model,
                args.reasoning_effort,
                args.min_confidence,
                args.google_places,
                os.environ.get(args.google_api_key_env),
                args.max_google_requests,
                args.google_timeout_seconds,
                (
                    load_graph_schema_inventory(args.graph_schema_inventory)
                    if args.graph_schema_inventory
                    else None
                ),
                args.provider,
                args.buddy if args.enable else False,
                buddy_client,
                args.buddy_model,
                args.buddy_provider,
            )
        print(
            json.dumps(
                {
                    "client_review_items": result.get("summary", {}).get(
                        "client_review_items", len(result.get("update_log", []))
                    )
                },
                sort_keys=True,
            )
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Schema discovery failed: {exc}")


if __name__ == "__main__":
    main()
