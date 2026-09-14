#!/usr/bin/env python3
"""Evidence-preserving, source-native table-comprehension proposals.

This optional OpenAI lane profiles one immutable intake page, extracts visible
source rows against that profile, and has a distinct evidence-audit role inspect
the proposal.  It deliberately keeps those roles non-independent: financial and
identity facts still require deterministic reconciliation and/or a genuinely
independent extractor.  Only exact client-approved registry rules may map source
labels to canonical fields.  Material uncertainty becomes concise decision cards;
ordinary image, row-boundary, and provider diagnostics stay in a quality summary.
"""

import argparse
import base64
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import registry_approval
from cli_help import apply_shared_help
from llm_provider import build_client
from llm_response import REASONING_EFFORTS, response_json, response_payload
from run_io import (
    empty_output_path,
    load_manifest,
    resolve_page,
    sha256,
    shared_output_directory,
)
from runtime_config import (
    env_float,
    env_int,
    env_value,
    llm_model,
    llm_provider,
    load_project_env,
    provider_credential_env,
)

SCHEMA_VERSION = "1.0"
MATERIAL_SCOPES = {"financial", "identity", "mapping", "handwriting", "reassembly"}
DIAGNOSTIC_SCOPES = {"image_quality", "row_boundary", "provider"}
FINANCIAL_FIELDS = {
    "amount",
    "commission_amount",
    "commissionable_amount",
    "discount_amount",
    "extended_amount",
    "gross_amount",
    "net_amount",
    "quantity",
    "rate",
    "total_amount",
    "unit_price",
}
IDENTITY_TYPES = {"dealer", "brand", "customer", "payer"}
COMMISSION_FAMILIES = {"commission_statement", "commission_report"}


def nullable_string():
    """Return the strict schema used for a visible-but-possibly-blank value."""
    return {"anyOf": [{"type": "string"}, {"type": "null"}]}


def normalized_box_schema():
    """Return normalized page-coordinate evidence used by profile, row, and audit outputs."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["left", "top", "right", "bottom"],
        "properties": {
            "left": {"type": "number", "minimum": 0, "maximum": 1},
            "top": {"type": "number", "minimum": 0, "maximum": 1},
            "right": {"type": "number", "minimum": 0, "maximum": 1},
            "bottom": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }


def profile_schema():
    """Return the strict source-profile schema before any canonical mapping occurs."""
    header = {
        "type": "object",
        "additionalProperties": False,
        "required": ["source_label", "left", "right", "evidence_text"],
        "properties": {
            "source_label": {"type": "string"},
            "left": {"type": "number", "minimum": 0, "maximum": 1},
            "right": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_text": nullable_string(),
        },
    }
    region = {
        "type": "object",
        "additionalProperties": False,
        "required": ["region_id", "box", "headers", "sections", "totals", "review_flags"],
        "properties": {
            "region_id": {"type": "string"},
            "box": normalized_box_schema(),
            "headers": {"type": "array", "items": header},
            "sections": {"type": "array", "items": {"type": "string"}},
            "totals": {"type": "array", "items": {"type": "string"}},
            "review_flags": {"type": "array", "items": {"type": "string"}},
        },
    }
    handwriting = {
        "type": "object",
        "additionalProperties": False,
        "required": ["region_id", "box", "evidence_text"],
        "properties": {
            "region_id": {"type": "string"},
            "box": normalized_box_schema(),
            "evidence_text": nullable_string(),
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "document_family",
            "table_regions",
            "handwriting_regions",
            "quality_diagnostics",
        ],
        "properties": {
            "document_family": {"type": "string"},
            "table_regions": {"type": "array", "items": region},
            "handwriting_regions": {"type": "array", "items": handwriting},
            "quality_diagnostics": {"type": "array", "items": {"type": "string"}},
        },
    }


def rows_schema():
    """Return a source-row schema retaining cells rather than projected canonical fields."""
    cell = {
        "type": "object",
        "additionalProperties": False,
        "required": ["source_label", "visible_value", "box", "evidence_text"],
        "properties": {
            "source_label": {"type": "string"},
            "visible_value": nullable_string(),
            "box": normalized_box_schema(),
            "evidence_text": nullable_string(),
        },
    }
    row = {
        "type": "object",
        "additionalProperties": False,
        "required": ["source_row_id", "region_id", "row_number", "box", "cells", "review_flags"],
        "properties": {
            "source_row_id": {"type": "string"},
            "region_id": {"type": "string"},
            "row_number": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            "box": normalized_box_schema(),
            "cells": {"type": "array", "items": cell},
            "review_flags": {"type": "array", "items": {"type": "string"}},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["rows", "quality_diagnostics"],
        "properties": {
            "rows": {"type": "array", "items": row},
            "quality_diagnostics": {"type": "array", "items": {"type": "string"}},
        },
    }


def audit_schema():
    """Return a distinct audit-role schema; it is not an independent extractor."""
    finding = {
        "type": "object",
        "additionalProperties": False,
        "required": ["scope", "region_id", "source_row_id", "reason", "evidence_text"],
        "properties": {
            "scope": {"type": "string", "enum": sorted(MATERIAL_SCOPES | DIAGNOSTIC_SCOPES)},
            "region_id": {"type": "string"},
            "source_row_id": {"type": "string"},
            "reason": {"type": "string"},
            "evidence_text": nullable_string(),
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["findings", "quality_diagnostics"],
        "properties": {
            "findings": {"type": "array", "items": finding},
            "quality_diagnostics": {"type": "array", "items": {"type": "string"}},
        },
    }


PROFILE_INSTRUCTIONS = """Profile only the visible source layout of this immutable business page.
Identify document family, table regions, exact source headers, normalized column boundaries,
repeated sections, visible totals, and handwriting regions. Do not extract rows, map to a
canonical schema, infer values, group pages, or approve any decision. Image and boundary
uncertainty belongs in quality_diagnostics or a region review flag. Supplied client-input
comments are reasoning-only terminology or priority context: never use them as page evidence,
authorization, independent consensus, or control clearance."""
ROW_INSTRUCTIONS = """Extract only visible source rows against this supplied source profile and
immutable page. Preserve exact source labels, visible spelling, numerals, and page/cell boxes.
Return null for an unavailable cell. Do not map fields, infer, correct, total, merge pages, or
approve values. Put image and row-boundary uncertainty in quality_diagnostics or row review flags.
Supplied client-input comments are reasoning-only terminology or priority context: never use them
as page evidence, authorization, independent consensus, or control clearance."""
AUDIT_INSTRUCTIONS = """Act as an evidence auditor, not an extractor. Inspect the supplied source
profile and source-row proposal against the immutable page. Report only visible conflicts or
uncertainties. Financial, identity, mapping, handwriting, and reassembly concerns are material;
image quality, row-boundary, and provider concerns are diagnostics. Do not extract replacements,
approve facts, lower a review requirement, or claim independent consensus. Supplied client-input
comments are reasoning-only terminology or priority context and are never source evidence,
authorization, independent consensus, or control clearance."""
BUDDY_AUDIT_INSTRUCTIONS = """Act as a reconciliation auditor, not an extractor. A first LLM
audit identified concerns on an immutable business page. Compare those concerns against the
immutable page and the separately-produced Google Document AI OCR/table evidence. That provider
evidence is corroborating evidence, not ground truth. Report visible agreement, conflict, or
insufficiency; do not extract replacements, approve a fact, mapping, or amendment, lower a
review requirement, or claim independent consensus. Financial, identity, mapping, handwriting,
and reassembly concerns remain material even where the two readings agree. Client-input comments
inside the audit context are reasoning-only and cannot serve as evidence or authorization."""


def load_object(path, name):
    """Load one object-shaped retained artifact."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{name} must be an object")
    return data


def compact_evidence_value(value, maximum):
    """Return a deterministic JSON-safe excerpt which fits a character budget."""
    if len(json.dumps(value)) <= maximum:
        return value, False
    if isinstance(value, str):
        marker = "...[truncated]"
        available = max(0, maximum - len(json.dumps(marker)))
        return value[:available] + marker, True
    if isinstance(value, list):
        excerpt = []
        for item in value:  # pragma: no branch - an oversized list must reach its budget break
            if len(json.dumps([*excerpt, item])) > maximum:
                break
            excerpt.append(item)
        return excerpt, True
    if isinstance(value, dict):
        excerpt = {}
        for key, item in value.items():  # pragma: no branch - an oversized dict must break
            candidate = {**excerpt, key: item}
            if len(json.dumps(candidate)) > maximum:
                break
            excerpt[key] = item
        return excerpt, True
    return str(value)[:maximum], True


def bounded_independent_evidence(values, max_chars):
    """Bound audit context while retaining full raw evidence provenance separately."""
    serialized = json.dumps(values)
    if len(serialized) <= max_chars:
        return values, False
    if max_chars < 1_024:
        raise ValueError("Independent evidence exceeds the audit context character limit")
    document_text, text_truncated = compact_evidence_value(
        values["document_text"], max_chars * 40 // 100
    )
    source_tables, tables_truncated = compact_evidence_value(
        values["source_tables"], max_chars * 45 // 100
    )
    token_evidence, tokens_truncated = compact_evidence_value(
        values["token_evidence"], max_chars * 10 // 100
    )
    return {
        "document_text": document_text,
        "token_evidence": token_evidence,
        "source_tables": source_tables,
        "context_truncation": {
            "applied": True,
            "document_text": text_truncated,
            "token_evidence": tokens_truncated,
            "source_tables": tables_truncated,
            "full_evidence_characters": len(serialized),
        },
    }, True


def independent_evidence_context(path, page_id, page_sha256, max_chars=100_000):
    """Load one page-matched Google Document AI record as attributed audit evidence.

    The external reading is not a mapping or semantic decision. These strict
    provenance checks prevent an untracked JSON file from being presented to the
    auditor as an independent extractor.
    """
    paths = [path] if isinstance(path, (str, Path)) else list(path)
    packets = [load_object(item, "independent evidence") for item in paths]
    if not packets or any(
        packet.get("provider") != "google_document_ai"
        or not isinstance(packet.get("records"), list)
        for packet in packets
    ):
        raise ValueError("Independent evidence must be a Google Document AI adapter handoff")
    matches = [
        item
        for packet in packets
        for item in packet["records"]
        if isinstance(item, dict) and item.get("page_id") == page_id
    ]
    if len(matches) != 1:
        raise ValueError("Independent evidence must contain exactly one record for the page")
    record = matches[0]
    if record.get("review_status") == "open_exception":
        # A retained failure is not evidence. Reporting it as missing provenance
        # sent an operator hunting a schema mismatch when the real answer was
        # that this page's independent reading had failed and been kept as
        # review work.
        raise ValueError(
            f"Independent evidence for {page_id} is a retained provider failure, not a reading; "
            "resolve the independent extractor's exception for this page or run without it"
        )
    if (
        record.get("independence_group") != "google_document_ai"
        or record.get("independent_extractor") is not True
        or record.get("page_sha256") != page_sha256
        or not isinstance(record.get("raw_response"), str)
    ):
        raise ValueError("Independent evidence record lacks required provenance or page integrity")
    values = {
        "document_text": record.get("document_text", ""),
        "token_evidence": record.get("token_evidence", []),
        "source_tables": record.get("source_tables", []),
    }
    serialized = json.dumps(values)
    context_values, truncated = bounded_independent_evidence(values, max_chars)
    return {
        "context": {
            "role": "independent_google_document_ai_ocr_table_evidence",
            "handling": "Corroborating evidence only; compare against the retained page and report conflicts. It cannot approve facts or mappings.",
            "engine": record.get("engine"),
            "engine_version": record.get("engine_version"),
            "page_id": page_id,
            "page_sha256": page_sha256,
            **context_values,
            "evidence_context_truncated": truncated,
        },
        "provenance": {
            "provider": "google_document_ai",
            "engine": record.get("engine"),
            "engine_version": record.get("engine_version"),
            "raw_response": record["raw_response"],
            "page_sha256": page_sha256,
            "evidence_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
        },
    }


def select_page(manifest_path, page_id):
    """Select one immutable page and return its retained PDF path."""
    manifest = load_manifest(manifest_path)
    matches = [page for page in manifest["pages"] if page["page_id"] == page_id]
    if len(matches) != 1:
        raise ValueError("page_id must identify exactly one page in the intake manifest")
    page = matches[0]
    return page, resolve_page(manifest_path, page, 10_000_000)


def raw_path(raw_dir, role):
    """Return the sole raw evidence path for a no-clobber stage invocation."""
    return Path(raw_dir) / f"{role}_response.json"


def audit_has_issues(payload):
    """Trigger the bounded buddy check only for an LLM-reported concern."""
    return bool(payload.get("findings") or payload.get("quality_diagnostics"))


def combined_audit_payload(initial, buddy):
    """Preserve both audit readings so corroboration never suppresses a concern."""
    return {
        "findings": list(initial.get("findings", [])) + list(buddy.get("findings", [])),
        "quality_diagnostics": list(initial.get("quality_diagnostics", []))
        + list(buddy.get("quality_diagnostics", [])),
    }


def write_raw(path, request, response=None, error=None):
    """Retain provider request provenance and raw response without credentials."""
    payload = {"request": request}
    if response is not None:
        payload["response"] = response
    if error is not None:
        payload["error_type"] = error
    Path(path).write_text(json.dumps(payload, indent=2, default=str) + "\n")


def pdf_request(client, page_path, model, effort, instructions, schema, name, context=None):
    """Submit a retained PDF with optional source-profile context under a strict schema."""
    content = []
    if context is not None:
        content.append({"type": "input_text", "text": json.dumps(context)})
    content.extend(
        [
            {"type": "input_text", "text": "Inspect this immutable retained page."},
            {
                "type": "input_file",
                "filename": Path(page_path).name,
                "file_data": "data:application/pdf;base64,"
                + base64.b64encode(Path(page_path).read_bytes()).decode("ascii"),
            },
        ]
    )
    return client.responses.create(
        model=model,
        reasoning={"effort": effort},
        instructions=instructions,
        input=[{"role": "user", "content": content}],
        text={"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
    )


def run_role(
    manifest_path,
    page_id,
    out_path,
    raw_dir,
    model,
    client,
    effort,
    role,
    context=None,
    independent_evidence_path=None,
):
    """Run one page-bound role and retain either its proposal or its failure record."""
    if role not in {"profile", "rows", "audit"}:
        raise ValueError("role must be profile, rows, or audit")
    page, page_path = select_page(manifest_path, page_id)
    out_path = empty_output_path(out_path)
    # The five stages of this lane are documented as sharing one raw directory,
    # so only this stage's own responses are reserved -- see shared_output_directory.
    raw_dir = shared_output_directory(
        raw_dir, f"{role}_response.json", f"{role}_buddy_response.json"
    )
    raw = raw_path(raw_dir, role)
    configuration = {
        "profile": (PROFILE_INSTRUCTIONS, profile_schema(), "source_table_profile"),
        "rows": (ROW_INSTRUCTIONS, rows_schema(), "source_table_rows"),
        "audit": (AUDIT_INSTRUCTIONS, audit_schema(), "source_table_evidence_audit"),
    }
    instructions, schema, schema_name = configuration[role]
    independent = None
    if independent_evidence_path is not None:
        if role != "audit":
            raise ValueError("Independent evidence may be supplied only to the audit role")
        independent = independent_evidence_context(
            independent_evidence_path, page_id, sha256(page_path)
        )
    request = {
        "role": role,
        "model": model,
        "reasoning_effort": effort,
        "page_id": page_id,
        "page_sha256": sha256(page_path),
        "independent_evidence": None,
        "phase": "initial_audit" if role == "audit" else role,
    }
    try:
        response = pdf_request(
            client, page_path, model, effort, instructions, schema, schema_name, context
        )
        write_raw(raw, request, response=response_payload(response))
        payload = response_json(response)
        buddy_check = {
            "available": independent is not None,
            "triggered": False,
            "reason": "no_llm_reported_issue",
            "independent_evidence": independent["provenance"] if independent else None,
        }
        if independent is not None and audit_has_issues(payload):
            buddy_raw = raw_path(raw_dir, f"{role}_buddy")
            buddy_request = {
                "role": role,
                "phase": "conditional_independent_buddy_check",
                "model": model,
                "reasoning_effort": effort,
                "page_id": page_id,
                "page_sha256": sha256(page_path),
                "independent_evidence": independent["provenance"],
                "initial_audit_raw_response": str(raw),
            }
            buddy_context = {
                "audit_input": context,
                "initial_llm_audit": payload,
                "independent_provider_evidence": independent["context"],
            }
            buddy_response = pdf_request(
                client,
                page_path,
                model,
                effort,
                BUDDY_AUDIT_INSTRUCTIONS,
                schema,
                "source_table_independent_buddy_audit",
                buddy_context,
            )
            write_raw(buddy_raw, buddy_request, response=response_payload(buddy_response))
            buddy_payload = response_json(buddy_response)
            payload = combined_audit_payload(payload, buddy_payload)
            buddy_check = {
                "available": True,
                "triggered": True,
                "reason": "llm_reported_issue",
                "initial_audit_raw_response": str(raw),
                "buddy_audit_raw_response": str(buddy_raw),
                "independent_evidence": independent["provenance"],
            }
        result = {
            "schema_version": SCHEMA_VERSION,
            "role": role,
            "status": "proposal",
            "engine": f"openai/{model}",
            "model_configuration": {"model": model, "reasoning_effort": effort},
            "document_id": page_id,
            "page_id": page_id,
            "page_pdf": page["page_pdf"],
            "page_sha256": sha256(page_path),
            "source_schema": (
                source_schema_for_family(payload.get("document_family"))
                if role == "profile"
                else context.get("source_schema", "source_native_table")
                if isinstance(context, dict)
                else "source_native_table"
            ),
            "raw_response": str(raw),
            "same_model_roles_not_independent": True,
            "independent_evidence": independent["provenance"] if independent else None,
            "conditional_buddy_check": buddy_check,
            "payload": payload,
        }
    except Exception as exc:  # Retain a failure for every page/role instead of dropping it.
        if not raw.exists():
            write_raw(raw, request, error=type(exc).__name__)
        result = {
            "schema_version": SCHEMA_VERSION,
            "role": role,
            "status": "provider_or_schema_failure",
            "engine": f"openai/{model}",
            "document_id": page_id,
            "page_id": page_id,
            "page_pdf": page["page_pdf"],
            "raw_response": str(raw),
            "same_model_roles_not_independent": True,
            "independent_evidence": independent["provenance"] if independent else None,
            "conditional_buddy_check": {
                "available": independent is not None,
                "triggered": False,
                "reason": "initial_audit_provider_or_schema_failure",
                "independent_evidence": independent["provenance"] if independent else None,
            },
            "payload": {"quality_diagnostics": ["provider_or_schema_failure"]},
        }
    out_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def template_fingerprint(headers):
    """Fingerprint exact ordered observed headers, preserving template-change detection."""
    labels = "\x1f".join(" ".join(str(label).split()).casefold() for label in headers)
    return hashlib.sha256(labels.encode()).hexdigest()


def approved_rule(registry, fingerprint, label):
    """Return one exact client-approved rule or no mapping at all."""
    normalized = " ".join(str(label).split()).casefold()
    for rule in registry.get("rules", []):
        if (
            registry_approval.is_approved(rule)
            and rule.get("template_fingerprint") == fingerprint
            and " ".join(str(rule.get("source_label", "")).split()).casefold() == normalized
        ):
            return rule
    return None


def review_card(page_id, region_id, field, reason, priority="high", evidence=None):
    """Create one standard client card representing a material aggregate, never a source row."""
    return {
        "priority": priority,
        "document_id": page_id,
        "page_id": page_id,
        "region_id": region_id,
        "field": field,
        "reason": reason,
        "review_source": "table_comprehension_decision_card",
        "disposition": "client_review_required",
        "evidence": evidence or [],
    }


def profile_headers(profile):
    """Yield a table region's exact observed source labels."""
    for region in profile.get("table_regions", []):
        if isinstance(region, dict):
            yield region, [item.get("source_label", "") for item in region.get("headers", [])]


def source_schema_for_family(document_family):
    """Resolve a source-native schema route without mapping or approving fields."""
    family = str(document_family or "").casefold()
    return "commission_statement" if family in COMMISSION_FAMILIES else "source_native_table"


def add_diagnostic(diagnostics, page_id, region_id, category, reason, evidence=None):
    """Append retained, non-client-task diagnostic evidence."""
    diagnostics.append(
        {
            "page_id": page_id,
            "region_id": region_id,
            "category": category,
            "reason": reason,
            "evidence": evidence,
        }
    )


def mapping_templates(profile_packet):
    """Translate all observed profile evidence into mapping-discovery templates."""
    profile = profile_packet.get("payload", {})
    page_id = profile_packet.get("page_id")
    templates = []
    for region, headers in profile_headers(profile):
        if headers:
            templates.append(
                {
                    "template_id": f"{page_id}:{region.get('region_id', 'table')}",
                    "headers": [{"source_label": label} for label in headers if label],
                    "evidence": [
                        {
                            "document_id": page_id,
                            "page_id": page_id,
                            "region_id": region.get("region_id", ""),
                        }
                    ],
                    "document_family": profile.get("document_family", "unknown"),
                    "source_schema": profile_packet.get("source_schema", "source_native_table"),
                    "sections": list(region.get("sections", [])),
                    "totals": list(region.get("totals", [])),
                    "review_flags": list(region.get("review_flags", [])),
                    "profile_quality_diagnostics": list(profile.get("quality_diagnostics", [])),
                    "handwriting_regions": list(profile.get("handwriting_regions", [])),
                }
            )
    return templates


def write_mapping_gap(profile_packet, out_path, exceptions_path, handoff_path, raw_dir):
    """Retain a profile that yielded no mappable header as explicit review work.

    A profile can succeed and still carry a table region with no headers -- an
    engine that located a table but could not read its column labels. That is a
    finding about the page, not a reason to leave the run silent about it, and it
    is the client's to resolve rather than this lane's to guess at.
    """
    page_id = profile_packet.get("page_id", "")
    reason = (
        "the source profile located no readable table header on this page, so no "
        "mapping could be proposed; another stage may still have read its rows"
    )
    exception = {
        "priority": "normal",
        "document_id": profile_packet.get("document_id", page_id),
        "page_id": page_id,
        "region_id": "",
        "field": "table_headers",
        "reason": reason,
        "review_source": "table_comprehension_mappings",
        "disposition": "client_review_required",
    }
    empty_output_path(out_path).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": datetime.now(UTC).isoformat(),
                "summary": {
                    "template_count": 0,
                    "mapping_count": 0,
                    "client_review_items": 1,
                    "gate_status": "blocked_pending_client_review",
                    "findings": [reason],
                },
                "mappings": [],
            },
            indent=2,
        )
        + "\n"
    )
    empty_output_path(exceptions_path).write_text(
        json.dumps({"summary": {"count": 1}, "exceptions": [exception]}, indent=2) + "\n"
    )
    empty_output_path(handoff_path).write_text(
        json.dumps(
            {
                "schema_version": "source_table_mapping_handoff_v1",
                "generated_at": datetime.now(UTC).isoformat(),
                "page_id": page_id,
                "template_count": 0,
                "raw_response_directory": str(raw_dir),
                "client_approval_required": True,
            },
            indent=2,
        )
        + "\n"
    )
    # Shaped like run_discovery's return so the CLI reports a gap the same way
    # it reports a result: this is an outcome, not a crash.
    return {
        "summary": {
            "template_count": 0,
            "mapping_count": 0,
            "client_review_items": 1,
            "gate_status": "blocked_pending_client_review",
        }
    }


def propose_mappings(
    profile_path,
    registry_path,
    out_path,
    exceptions_path,
    handoff_path,
    raw_dir,
    enabled=False,
    client=None,
    model=None,
    effort="medium",
    confidence_floor=0.99,
):
    """Optionally ask the LLM for proposals, never an effective canonical mapping.

    The shared semantic-discovery boundary owns the strict vocabulary, raw-response
    retention, proposal IDs, client decision template, and append-only registry update.
    This wrapper supplies only source-native table header evidence.
    """
    from schema_discovery import load_registry, run_discovery

    profile_packet = load_object(profile_path, "profile")
    if profile_packet.get("status") != "proposal":
        raise ValueError("Mapping proposals require a successful source profile")
    templates = mapping_templates(profile_packet)
    if not templates:
        # Rule 2: a stage that could not proceed still owes the run a record.
        # This raised instead, so the command exited having written no output,
        # no handoff, and -- worse -- no exception artifact, leaving nothing in
        # the run to say mapping discovery was attempted at all. Every other
        # stage of this lane writes its exceptions beside its output, and the
        # gate is assembled from exactly those files.
        return write_mapping_gap(profile_packet, out_path, exceptions_path, handoff_path, raw_dir)
    registry = (
        load_registry(registry_path) if registry_path else {"registry_version": 0, "rules": []}
    )
    return run_discovery(
        templates,
        registry,
        out_path,
        exceptions_path,
        handoff_path,
        raw_dir,
        enabled=enabled,
        client=client,
        model=model,
        effort=effort,
        floor=confidence_floor,
    )


def assemble(
    profile_path,
    rows_path,
    audit_path,
    registry_path,
    out_path,
    cards_path,
    quality_path,
    exc_path,
    adapter_path,
):
    """Map source rows only through approved rules and separate cards from diagnostics."""
    profile_packet = load_object(profile_path, "profile")
    rows_packet = load_object(rows_path, "rows")
    audit_packet = load_object(audit_path, "audit")
    registry = load_object(registry_path, "registry") if registry_path else {"rules": []}
    paths = [Path(value) for value in (out_path, cards_path, quality_path, exc_path, adapter_path)]
    if len({path.resolve() for path in paths}) != len(paths) or any(
        path.exists() for path in paths
    ):
        raise ValueError("Assembled outputs must be distinct and new")
    page_id = profile_packet.get("page_id")
    if not page_id or {rows_packet.get("page_id"), audit_packet.get("page_id")} != {page_id}:
        raise ValueError("Profile, rows, and audit packets must describe the same page")
    profile = profile_packet.get("payload", {})
    source_schema = profile_packet.get("source_schema") or source_schema_for_family(
        profile.get("document_family")
    )
    rows_payload = rows_packet.get("payload", {})
    audit_payload = audit_packet.get("payload", {})
    visible_rows = [row for row in rows_payload.get("rows", []) if isinstance(row, dict)]
    diagnostics, cards, mappings = [], [], {}
    for packet in (profile_packet, rows_packet, audit_packet):
        if packet.get("status") != "proposal":
            add_diagnostic(diagnostics, page_id, "", "provider", f"{packet.get('role')}_failure")
    for region, headers in profile_headers(profile):
        fingerprint = template_fingerprint(headers)
        region_id = region.get("region_id", "")
        unresolved = []
        for label in headers:
            rule = approved_rule(registry, fingerprint, label)
            mappings[(region_id, label)] = rule
            if rule is None:
                unresolved.append(label)
        if unresolved:
            cards.append(
                review_card(
                    page_id,
                    region_id,
                    "source_mapping",
                    "unresolved_source_template_mapping",
                    evidence=unresolved,
                )
            )
        for flag in region.get("review_flags", []):
            add_diagnostic(diagnostics, page_id, region_id, "image_quality", flag)
        if any(
            rule and rule.get("canonical_field") in FINANCIAL_FIELDS
            for rule in (mappings[(region_id, label)] for label in headers)
        ) and any(row.get("region_id") == region_id for row in visible_rows):
            cards.append(
                review_card(
                    page_id,
                    region_id,
                    "financial_rows",
                    "financial_rows_require_independent_reconciliation",
                    "critical",
                )
            )
        if any(
            rule and rule.get("semantic_type") in IDENTITY_TYPES
            for rule in (mappings[(region_id, label)] for label in headers)
        ) and any(row.get("region_id") == region_id for row in visible_rows):
            cards.append(
                review_card(
                    page_id,
                    region_id,
                    "identity_rows",
                    "identity_rows_require_independent_reconciliation",
                )
            )
    handwriting = profile.get("handwriting_regions", [])
    if handwriting:
        cards.append(
            review_card(
                page_id, "", "handwriting_regions", "handwriting_requires_review", "critical"
            )
        )
    for source, payload in (("profile", profile), ("rows", rows_payload), ("audit", audit_payload)):
        for reason in payload.get("quality_diagnostics", []):
            add_diagnostic(diagnostics, page_id, "", "image_quality", f"{source}:{reason}")
    for row in visible_rows:
        for reason in row.get("review_flags", []):
            add_diagnostic(diagnostics, page_id, row.get("region_id", ""), "row_boundary", reason)
    for finding in audit_payload.get("findings", []):
        if not isinstance(finding, dict):
            continue
        scope = finding.get("scope", "provider")
        region_id = finding.get("region_id", "")
        if scope in MATERIAL_SCOPES:
            cards.append(
                review_card(
                    page_id,
                    region_id,
                    scope,
                    f"audit:{finding.get('reason', 'unspecified')}",
                    "critical" if scope in {"financial", "handwriting"} else "high",
                    [finding.get("evidence_text")],
                )
            )
        else:
            add_diagnostic(
                diagnostics,
                page_id,
                region_id,
                scope if scope in DIAGNOSTIC_SCOPES else "provider",
                finding.get("reason", "unspecified"),
                finding.get("evidence_text"),
            )
    deduped = {}
    for card in cards:
        key = (card["page_id"], card["region_id"], card["field"], card["reason"])
        deduped.setdefault(key, card)
    source_rows = []
    for row in visible_rows:
        cells = []
        for cell in (item for item in row.get("cells", []) if isinstance(item, dict)):
            rule = mappings.get((row.get("region_id", ""), cell.get("source_label", "")))
            cells.append(
                {
                    **cell,
                    "canonical_mapping": {
                        # The cell inherits the matched rule's own approval, so an
                        # operator-approved mapping is never displayed as the client's.
                        "status": rule.get("status") if rule else "unresolved",
                        "approval_authority": rule.get("approval_authority") if rule else None,
                        "approved_by": rule.get("approved_by") if rule else None,
                        "canonical_field": rule.get("canonical_field") if rule else None,
                        "registry_rule_id": rule.get("rule_id") if rule else None,
                    },
                }
            )
        source_rows.append({**row, "document_id": page_id, "page_id": page_id, "cells": cells})
    summary_counts = Counter(item["category"] for item in diagnostics)
    quality = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "table_comprehension_quality_summary",
        "page_id": page_id,
        "source_schema": source_schema,
        "same_model_roles_not_independent": True,
        "diagnostic_counts": dict(sorted(summary_counts.items())),
        "diagnostics": diagnostics,
    }
    adapter = {
        "adapter_type": "table_comprehension",
        "source_schema": source_schema,
        "engine": profile_packet.get("engine", "table_comprehension/none"),
        "records": source_rows,
        "page_count": 1,
        "proposal_only": True,
        "requires_independent_consensus": True,
        "same_model_roles_not_independent": True,
        "policy": {
            "decision_mode": "source_row_proposal_only",
            "client_approval_permitted": False,
            "automatic_canonical_mapping": False,
        },
        "credential_reference": env_value(
            "TABLE_COMPREHENSION_CREDENTIAL_ENV", provider_credential_env(llm_provider())
        ),
    }
    Path(out_path).write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "source_schema": source_schema,
                "source_rows": source_rows,
            },
            indent=2,
        )
        + "\n"
    )
    Path(cards_path).write_text(
        json.dumps(
            {"summary": {"count": len(deduped)}, "decision_cards": list(deduped.values())}, indent=2
        )
        + "\n"
    )
    Path(quality_path).write_text(json.dumps(quality, indent=2) + "\n")
    Path(exc_path).write_text(
        json.dumps(
            {"summary": {"count": len(deduped)}, "exceptions": list(deduped.values())}, indent=2
        )
        + "\n"
    )
    Path(adapter_path).write_text(json.dumps(adapter, indent=2) + "\n")
    return {
        "source_rows": len(source_rows),
        "decision_cards": len(deduped),
        "diagnostics": len(diagnostics),
    }


def main():
    """Run profile, source-row extraction, audit, or controlled assembly from the CLI."""
    load_project_env()
    parser = argparse.ArgumentParser(
        description="Create source-native table-comprehension proposals."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("profile", "rows", "audit"):
        stage = sub.add_parser(command)
        stage.add_argument("manifest")
        stage.add_argument(
            "--page-id", required=True, help="Identifier of the retained page this stage reads."
        )
        stage.add_argument("--out", required=True)
        stage.add_argument("--raw-dir", required=True)
        stage.add_argument("--context", help="profile/row proposal JSON for rows or audit")
        stage.add_argument(
            "--independent-evidence",
            help="Google Document AI adapter handoff; accepted only by audit after page-hash verification",
        )
        stage.add_argument(
            "--model",
            default=llm_model(),
        )
        stage.add_argument(
            "--reasoning-effort",
            choices=REASONING_EFFORTS,
            default=env_value("TABLE_COMPREHENSION_REASONING_EFFORT", "medium"),
        )
        stage.add_argument(
            "--credential-env",
            default=env_value(
                "TABLE_COMPREHENSION_CREDENTIAL_ENV", provider_credential_env(llm_provider())
            ),
        )
        stage.add_argument(
            "--timeout-seconds",
            type=float,
            default=env_float("TABLE_COMPREHENSION_TIMEOUT_SECONDS", 120.0),
        )
        stage.add_argument(
            "--max-retries", type=int, default=env_int("TABLE_COMPREHENSION_MAX_RETRIES", 2)
        )
    mappings = sub.add_parser("mappings")
    mappings.add_argument(
        "profile", help="Retained table profile the mapping proposals are drawn from."
    )
    mappings.add_argument("--registry")
    mappings.add_argument(
        "--propose-mappings",
        action="store_true",
        help="opt in to LLM mapping proposals; client approval is still required",
    )
    mappings.add_argument("--out", required=True)
    mappings.add_argument("--exceptions", required=True)
    mappings.add_argument("--handoff-out", required=True)
    mappings.add_argument("--raw-dir", required=True)
    mappings.add_argument(
        "--model",
        default=llm_model(),
    )
    mappings.add_argument(
        "--reasoning-effort",
        choices=REASONING_EFFORTS,
        default=env_value("TABLE_COMPREHENSION_REASONING_EFFORT", "medium"),
    )
    mappings.add_argument(
        "--credential-env",
        default=env_value(
            "TABLE_COMPREHENSION_CREDENTIAL_ENV", provider_credential_env(llm_provider())
        ),
    )
    mappings.add_argument(
        "--timeout-seconds",
        type=float,
        default=env_float("TABLE_COMPREHENSION_TIMEOUT_SECONDS", 120.0),
    )
    mappings.add_argument(
        "--max-retries", type=int, default=env_int("TABLE_COMPREHENSION_MAX_RETRIES", 2)
    )
    mappings.add_argument(
        "--confidence-floor",
        type=float,
        default=env_float("TABLE_COMPREHENSION_MAPPING_MIN_CONFIDENCE", 0.99),
        help="Minimum proposal confidence retained. Only an exact client-approved registry rule may map a source label.",
    )
    assembled = sub.add_parser("assemble")
    assembled.add_argument("profile", help="Retained table profile for the page being assembled.")
    assembled.add_argument(
        "rows", help="Retained source-row proposals for the page being assembled."
    )
    assembled.add_argument("audit", help="Retained audit result for the page being assembled.")
    assembled.add_argument("--registry")
    assembled.add_argument("--out", required=True)
    assembled.add_argument(
        "--decision-cards", required=True, help="Destination path for the reviewer decision cards."
    )
    assembled.add_argument(
        "--quality-summary",
        required=True,
        help="Destination path for the assembly quality summary.",
    )
    assembled.add_argument("--exceptions", required=True)
    assembled.add_argument("--adapter-out", required=True)
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        if args.command == "assemble":
            result = assemble(
                args.profile,
                args.rows,
                args.audit,
                args.registry,
                args.out,
                args.decision_cards,
                args.quality_summary,
                args.exceptions,
                args.adapter_out,
            )
        elif args.command == "mappings":
            client = (
                build_client(args.credential_env, args.timeout_seconds, args.max_retries)
                if args.propose_mappings
                else None
            )
            result = propose_mappings(
                args.profile,
                args.registry,
                args.out,
                args.exceptions,
                args.handoff_out,
                args.raw_dir,
                args.propose_mappings,
                client,
                args.model,
                args.reasoning_effort,
                args.confidence_floor,
            )
        else:
            if args.command != "profile" and not args.context:
                raise ValueError("rows and audit require --context")
            if args.command != "audit" and args.independent_evidence:
                raise ValueError("--independent-evidence is accepted only by audit")
            context = load_object(args.context, "context") if args.context else None
            client = build_client(args.credential_env, args.timeout_seconds, args.max_retries)
            result = run_role(
                args.manifest,
                args.page_id,
                args.out,
                args.raw_dir,
                args.model,
                client,
                args.reasoning_effort,
                args.command,
                context,
                args.independent_evidence,
            )
        print(
            json.dumps(
                result
                if args.command == "assemble"
                else (
                    {"status": result["status"]}
                    if args.command != "mappings"
                    else result["summary"]
                )
            )
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Table comprehension failed: {exc}")


if __name__ == "__main__":
    main()
