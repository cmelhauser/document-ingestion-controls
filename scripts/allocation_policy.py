#!/usr/bin/env python3
"""Apply evidence-bound commission allocation policies to normalized allocation legs."""

import argparse
import hashlib
import json
import sys
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

import registry_approval
from cli_help import apply_shared_help
from llm_provider import build_client
from llm_response import REASONING_EFFORTS, response_json, response_payload
from runtime_config import env_bool, env_float, env_int, env_value, llm_model, load_project_env

ROLES = (
    "commissionable_amount",
    "commission_amount",
    "stated_commission_rate",
    "allocation_share",
    "allocation_descriptor",
    "sales_origin_indicator",
    "unknown",
)
CLASSIFICATIONS = ("administrative_ship_to", "sales_generated", "review_required")
# An effective commission rate above this fraction of the commissionable amount is
# not a policy question. It is a misread figure -- most often a displaced decimal
# point -- and it must reach a human rather than an approved classification. A
# client may lower it per policy; it is deliberately not raisable past 1.0, because
# commission exceeding the commissionable base is never a self-proving fact.
AMBIGUOUS_RATE = "ambiguous_rate_unit"
DEFAULT_EFFECTIVE_RATE_CEILING = "0.5"
MAX_EFFECTIVE_RATE_CEILING = Decimal("1")
POLICY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["column_proposals", "policy_proposal"],
    "properties": {
        "column_proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source_label", "allocation_role", "confidence", "rationale"],
                "properties": {
                    "source_label": {"type": "string"},
                    "allocation_role": {"type": "string", "enum": list(ROLES)},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                },
            },
        },
        "policy_proposal": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "effective_rate_threshold",
                "explicit_ship_to_precedence",
                "allow_sales_generated",
                "confidence",
                "rationale",
            ],
            "properties": {
                "effective_rate_threshold": {"type": "number", "minimum": 0, "maximum": 0.1},
                "explicit_ship_to_precedence": {"type": "boolean"},
                "allow_sales_generated": {"type": "boolean"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "rationale": {"type": "string"},
            },
        },
    },
}
INSTRUCTIONS = """You are an evidence-bound commission-policy analyst. Map only supplied report labels to the controlled allocation roles. A nominal rate, allocation share, effective rate, commission amount, and sales credit are different facts. Propose a policy only from supplied evidence. Ship-to wording may indicate an administrative commission, but never approve a policy, create a field, invent a relationship, or treat a rate alone as proof of sales origin."""
MONEY = Decimal("0.01")
RATE = Decimal("0.0001")


def stamp():
    """Return a UTC audit timestamp."""
    return datetime.now(UTC).isoformat()


def scalar(value):
    """Read an evidence object without replacing its original value."""
    return value.get("value") if isinstance(value, dict) else value


def number(value):
    """Parse a decimal amount or rate without accepting empty/non-finite values."""
    raw = scalar(value)
    if raw is None or isinstance(raw, bool):
        return None
    try:
        parsed = Decimal(str(raw).strip().replace(",", "").replace("%", ""))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def percent(value):
    """Interpret a rate, refusing to guess the unit when the value is ambiguous.

    Magnitude alone cannot distinguish "0.75" meaning 0.75% from "0.75" meaning
    75%, and sub-1% override and split commissions are ordinary in this document
    family. Guessing read those 100x high. A value above 1 can only be a
    displayed percentage; a value of exactly 0 is unambiguous; everything in
    between returns ``AMBIGUOUS_RATE`` so the caller registers review work rather
    than choosing a reading.
    """
    parsed = number(value)
    if parsed is None:
        return None
    if parsed > 1:
        return parsed / 100
    if parsed == 0:
        return parsed
    return AMBIGUOUS_RATE


def key(value):
    """Normalize comparison text while retaining source wording separately."""
    return " ".join(str(value or "").casefold().split())


def fingerprint(template):
    """Hash ordered labels so a changed report layout cannot reuse a policy."""
    labels = template.get("source_labels") or [item["source_label"] for item in template["headers"]]
    return hashlib.sha256("\x1f".join(key(label) for label in labels).encode()).hexdigest()


def review(leg, field, reason, priority="high"):
    """Create a standard final-review record for a policy uncertainty."""
    return {
        "priority": priority,
        "document_id": leg.get("document_id", leg.get("allocation_id", "unknown")),
        "page_id": leg.get("page_id", leg.get("document_id", "unknown")),
        "region_id": leg.get("region_id", ""),
        "field": field,
        "reason": reason,
        "review_source": "allocation_policy",
        "disposition": "client_review_required",
    }


def load_legs(path):
    """Read an allocation artifact that keeps one source allocation leg per item."""
    data = json.loads(Path(path).read_text())
    legs = data.get("allocation_legs") if isinstance(data, dict) else None
    if not isinstance(legs, list) or not legs or not all(isinstance(item, dict) for item in legs):
        raise ValueError("Input must contain a non-empty allocation_legs list")
    return legs


def load_templates(path):
    """Read report-template observations for proposal-only policy discovery."""
    data = json.loads(Path(path).read_text())
    templates = data.get("templates") if isinstance(data, dict) else None
    if not isinstance(templates, list) or not templates:
        raise ValueError("Input must contain a non-empty templates list")
    for template in templates:
        labels = template.get("source_labels") or [
            item.get("source_label") for item in template.get("headers", [])
        ]
        if (
            not isinstance(template, dict)
            or not template.get("template_id")
            or not labels
            or not all(labels)
        ):
            raise ValueError("Each template requires template_id and non-empty source labels")
    return templates


def load_registry(path):
    """Load an approved-policy registry or return an empty safe registry."""
    if path is None:
        return {"registry_version": 0, "rules": []}
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise ValueError("Registry must be an object containing rules")
    return data


def scope_dimensions(leg):
    """Expose declared dimensions without rewriting the source allocation leg."""
    embedded = leg.get("dimensions", {})
    return {
        **(embedded if isinstance(embedded, dict) else {}),
        **{
            name: leg[name]
            for name in (
                "brand_name",
                "dealer_name",
                "product_name",
                "product_sku",
                "client_id",
                "selling_location",
            )
            if name in leg
        },
    }


def scope_matches(scope, leg):
    """Match arbitrary approved dimensions and an optional inclusive effective-date window."""
    if not isinstance(scope, dict):
        return False
    dimensions = scope.get("dimensions", scope)
    if not isinstance(dimensions, dict):
        return False
    date_value = leg.get("effective_date")
    try:
        effective = date.fromisoformat(str(date_value)) if date_value else None
        start = date.fromisoformat(scope["effective_from"]) if scope.get("effective_from") else None
        end = date.fromisoformat(scope["effective_to"]) if scope.get("effective_to") else None
    except ValueError:
        return False
    if (start or end) and effective is None:
        return False
    if (start and effective < start) or (end and effective > end):
        return False
    values = scope_dimensions(leg)
    return all(
        isinstance(value, str) and value.strip() and key(values.get(name)) == key(value)
        for name, value in dimensions.items()
    )


def scoped_policy(registry, template_hash, leg):
    """Choose one exact approved template policy with the most-specific active scope."""
    matches = []
    for rule in registry["rules"]:
        scope = rule.get("scope", {}) if isinstance(rule, dict) else {}
        if (
            not registry_approval.is_approved(rule)
            or rule.get("template_fingerprint") != template_hash
            or not isinstance(rule.get("policy"), dict)
            or not scope_matches(scope, leg)
        ):
            continue
        dimensions = scope.get("dimensions", scope)
        matches.append(
            (
                len(dimensions)
                + bool(scope.get("effective_from"))
                + bool(scope.get("effective_to")),
                rule,
            )
        )
    if not matches:
        return None
    highest = max(item[0] for item in matches)
    finalists = [item[1] for item in matches if item[0] == highest]
    return finalists[0] if len(finalists) == 1 else None


def effective_rate_ceiling(policy):
    """Resolve the plausibility ceiling; an out-of-range configured value fails closed."""
    raw = policy.get("effective_rate_ceiling", DEFAULT_EFFECTIVE_RATE_CEILING)
    if isinstance(raw, bool):
        return None
    value = number(raw)
    if value is None or not 0 < value <= MAX_EFFECTIVE_RATE_CEILING:
        return None
    return value


def has_ship_to(leg):
    """Detect only explicit source wording; it is not inferred from a city."""
    fields = ("allocation_descriptor", "sales_origin_indicator", "source_text", "source_label")
    text = " ".join(str(scalar(leg.get(field, ""))) for field in fields).casefold()
    return "ship to" in text or "ship-to" in text or "shipto" in text


def rendered(value, precision):
    """Render Decimal values deterministically in output artifacts."""
    return format(value.quantize(precision, rounding=ROUND_HALF_UP), "f")


def classify(leg, policy_rule):
    """Calculate an allocation leg and apply only a client-approved policy."""
    base = number(leg.get("commissionable_amount"))
    commission = number(leg.get("commission_amount"))
    stated = percent(leg.get("stated_commission_rate"))
    share = percent(leg.get("allocation_share"))
    raw = {
        name: leg.get(name)
        for name in (
            "commissionable_amount",
            "commission_amount",
            "stated_commission_rate",
            "allocation_share",
            "allocation_descriptor",
            "sales_origin_indicator",
        )
        if name in leg
    }
    output = {
        "allocation_id": leg.get("allocation_id"),
        "document_id": leg.get("document_id"),
        "page_id": leg.get("page_id"),
        "template_fingerprint": leg.get("template_fingerprint"),
        "raw_source_fields": raw,
        "derived": {},
        "classification": "review_required",
        "sales_credit_eligible": None,
        "client_review_required": True,
        "policy_rule_id": policy_rule.get("rule_id") if policy_rule else None,
        "policy_version": policy_rule.get("registry_version") if policy_rule else None,
        "policy_scope": policy_rule.get("scope", {}) if policy_rule else {},
    }
    if base is None or commission is None or base <= 0:
        output["reason"] = "allocation_missing_or_invalid_commissionable_amount"
        return output, review(leg, "allocation", output["reason"], "critical")
    effective = commission / base
    output["derived"] = {
        "effective_commission_rate": rendered(effective, RATE),
        "commissionable_amount": rendered(base, MONEY),
        "commission_amount": rendered(commission, MONEY),
        "explicit_ship_to_indicator": has_ship_to(leg),
    }
    if AMBIGUOUS_RATE in (stated, share):
        output["derived"]["ambiguous_rate_fields"] = sorted(
            name
            for name, parsed in (("stated_commission_rate", stated), ("allocation_share", share))
            if parsed is AMBIGUOUS_RATE
        )
        output["reason"] = "allocation_rate_unit_ambiguous"
        return output, review(leg, "allocation.rate", output["reason"], "critical")
    if stated is not None and share is not None:
        expected = base * stated * share
        output["derived"]["stated_rate"] = rendered(stated, RATE)
        output["derived"]["allocation_share"] = rendered(share, RATE)
        output["derived"]["formula_expected_commission_amount"] = rendered(expected, MONEY)
        output["derived"]["formula_delta"] = rendered(commission - expected, MONEY)
    if policy_rule is None:
        output["reason"] = "allocation_unapproved_template_policy"
        return output, review(leg, "allocation.policy", output["reason"])
    policy = policy_rule["policy"]
    threshold = Decimal(str(policy["effective_rate_threshold"]))
    ceiling = effective_rate_ceiling(policy)
    if ceiling is None:
        output["reason"] = "allocation_policy_effective_rate_ceiling_invalid"
        return output, review(leg, "allocation.policy", output["reason"], "critical")
    if effective > ceiling:
        output["derived"]["effective_rate_ceiling"] = rendered(ceiling, RATE)
        output["reason"] = "allocation_effective_rate_exceeds_plausibility_ceiling"
        return output, review(leg, "allocation.rate", output["reason"], "critical")
    if effective < 0:
        # The lower end of the same range. A negative commission is a clawback or
        # a reversal, not an administrative allocation, and it would otherwise fall
        # below the threshold and classify clean with no exception.
        output["reason"] = "allocation_effective_rate_negative"
        return output, review(leg, "allocation.rate", output["reason"], "critical")
    explicit = has_ship_to(leg)
    if explicit and policy.get("explicit_ship_to_precedence") is True:
        classification, reason = "administrative_ship_to", "explicit_ship_to_indicator"
    elif effective <= threshold:
        classification, reason = "administrative_ship_to", "effective_rate_at_or_below_threshold"
    elif policy.get("allow_sales_generated") is True:
        classification, reason = "sales_generated", "approved_policy_effective_rate_above_threshold"
    else:
        output["reason"] = "allocation_policy_does_not_authorize_sales_credit"
        return output, review(leg, "allocation.policy", output["reason"])
    output.update(
        {
            "classification": classification,
            "sales_credit_eligible": classification == "sales_generated",
            "client_review_required": False,
            "reason": reason,
        }
    )
    if "formula_delta" in output["derived"] and Decimal(output["derived"]["formula_delta"]) != 0:
        # The classification block above was written optimistically. A failed
        # formula proof must retract it: the derived-field contract defines
        # sales_credit_eligible as true only for *approved* sales-generated work
        # and null when unresolved, so leaving it true here would let a consumer
        # filtering on that field pick up unproved commission credit.
        output.update(
            {
                "classification": "review_required",
                "sales_credit_eligible": None,
                "client_review_required": True,
                "reason": "allocation_formula_not_proved",
            }
        )
        return output, review(leg, "allocation.formula", output["reason"], "critical")
    return output, None


def apply(legs, registry, out, exceptions):
    """Apply versioned policy rules without changing original allocation evidence."""
    targets = [Path(out), Path(exceptions)]
    if len({path.resolve() for path in targets}) != len(targets) or any(
        path.exists() for path in targets
    ):
        raise ValueError("Output paths must be distinct and new")
    results, findings = [], []
    for leg in legs:
        template_hash = leg.get("template_fingerprint")
        rule = (
            scoped_policy(registry, template_hash, leg) if isinstance(template_hash, str) else None
        )
        result, finding = classify(leg, rule)
        results.append(result)
        if finding:
            findings.append(finding)
    output = {
        "summary": {
            "schema_version": "1.0",
            "generated_at": stamp(),
            "allocation_leg_count": len(results),
            "administrative_ship_to_count": sum(
                item["classification"] == "administrative_ship_to" for item in results
            ),
            "sales_generated_count": sum(
                item["classification"] == "sales_generated" for item in results
            ),
            "client_review_items": len(findings),
            "gate_status": "blocked_pending_client_review" if findings else "clear",
            "findings": [
                "Source rates, shares, and amounts are retained; policy decisions are versioned."
            ],
        },
        "allocation_legs": results,
        "review_items": findings,
    }
    Path(out).write_text(json.dumps(output, indent=2) + "\n")
    Path(exceptions).write_text(json.dumps({"exceptions": findings}, indent=2) + "\n")
    return output


def ask_model(client, template, model, effort):
    """Request one strict, retained proposal for an unfamiliar report layout."""
    labels = template.get("source_labels") or [item["source_label"] for item in template["headers"]]
    packet = {
        "template_id": template["template_id"],
        "template_fingerprint": fingerprint(template),
        "source_labels": labels,
        "evidence": template.get("evidence", []),
    }
    response = client.responses.create(
        model=model,
        reasoning={"effort": effort},
        instructions=INSTRUCTIONS,
        input=[{"role": "user", "content": [{"type": "input_text", "text": json.dumps(packet)}]}],
        text={
            "format": {
                "type": "json_schema",
                "name": "allocation_policy",
                "strict": True,
                "schema": POLICY_SCHEMA,
            }
        },
    )
    return packet, response


def write_raw(path, request, response=None, error=None):
    """Write non-secret request and provider-response evidence."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    value = {"request": request}
    if response is not None:
        value["response"] = response
    if error is not None:
        value["error_type"] = error
    Path(path).write_text(json.dumps(value, indent=2, default=str) + "\n")


def discover(
    templates, registry, out, exceptions, handoff, raw_dir, enabled, client, model, effort
):
    """Produce review-required policies for only unfamiliar report templates."""
    targets = [Path(value) for value in (out, exceptions, handoff)]
    if (
        effort not in REASONING_EFFORTS
        or len({path.resolve() for path in targets}) != len(targets)
        or any(path.exists() for path in targets)
    ):
        raise ValueError("Invalid policy-discovery limits or output paths")
    raw_dir = Path(raw_dir)
    if raw_dir.exists() and any(raw_dir.iterdir()):
        raise ValueError("Raw response directory must be new or empty")
    raw_dir.mkdir(parents=True, exist_ok=True)
    if enabled and client is None:
        raise ValueError("Enabled policy discovery requires an LLM client")
    proposals, findings = [], []
    for template in templates:
        template_hash = fingerprint(template)
        if scoped_policy(registry, template_hash, {}):
            continue
        base = {
            "template_id": template["template_id"],
            "template_fingerprint": template_hash,
            "source_labels": template.get("source_labels")
            or [item["source_label"] for item in template.get("headers", [])],
            "client_review_required": True,
        }
        if not enabled:
            proposals.append({**base, "status": "provider_not_enabled"})
            findings.append(
                review(template, "allocation.policy", "allocation_policy_requires_client_review")
            )
            continue
        raw_path = raw_dir / f"{template_hash[:16]}.json"
        try:
            packet, response = ask_model(client, template, model, effort)
            write_raw(raw_path, packet, response_payload(response))
            proposal = response_json(response)
            proposals.append({**base, **proposal, "status": "proposed", "decision_source": "llm"})
            findings.append(
                review(template, "allocation.policy", "allocation_policy_client_approval_required")
            )
        except Exception as exc:
            write_raw(raw_path, {"template_id": template["template_id"]}, error=type(exc).__name__)
            proposals.append({**base, "status": "provider_failure"})
            findings.append(
                review(
                    template, "allocation.policy", "allocation_policy_provider_or_schema_failure"
                )
            )
    result = {
        "summary": {
            "template_count": len(templates),
            "client_review_items": len(findings),
            "gate_status": "blocked_pending_client_review" if findings else "clear",
        },
        "policy_proposals": proposals,
        "review_items": findings,
    }
    Path(out).write_text(json.dumps(result, indent=2) + "\n")
    Path(exceptions).write_text(json.dumps({"exceptions": findings}, indent=2) + "\n")
    Path(handoff).write_text(
        json.dumps(
            {
                "adapter_type": "allocation_policy",
                "engine": f"openai/{model}" if enabled else "allocation_policy/deterministic",
                "template_count": len(templates),
                "policy": {
                    "decision_mode": "proposal_only",
                    "client_approval_permitted": False,
                    "automatic_sales_credit": False,
                },
                "model_configuration": {"model": model, "reasoning_effort": effort},
                "raw_response_directory": str(raw_dir),
            },
            indent=2,
        )
        + "\n"
    )
    return result


def client_decision_template(discovery):
    """Create the only client-editable return file from concise proposed policy cards."""
    proposals = [
        item
        for item in discovery.get("policy_proposals", [])
        if isinstance(item, dict) and item.get("status") == "proposed"
    ]
    return {
        "review_metadata": {
            "label": "CLIENT DECISION RETURN FILE - EDIT ONLY decisions",
            "instructions": "For each card, approve or defer. Add approved dimensions and an effective date window. Use policy_override only for a client-approved threshold or boolean control; leave it empty to accept the proposal.",
            "proposal_count": len(proposals),
        },
        "decisions": [
            {
                "template_fingerprint": item["template_fingerprint"],
                "template_id": item["template_id"],
                "decision": "defer",
                "scope": {"dimensions": {}},
                "client_view": {
                    "question": "Should this report layout create sales credit above its approved effective-rate threshold?",
                    "safe_default": "defer",
                    "scope_help": "Use dimensions for any observed variable (brand, dealer, product, client, location, or another approved source concept). Add effective dates only when the policy changes over time.",
                    "source_labels": item.get("source_labels", []),
                },
                "policy_proposal": item.get("policy_proposal", {}),
                "policy_override": {},
                "client_comment": "",
            }
            for item in proposals
        ],
    }


def policy_with_override(policy, override):
    """Allow explicitly returned client values without permitting a new policy shape."""
    allowed = {"effective_rate_threshold", "explicit_ship_to_precedence", "allow_sales_generated"}
    if not isinstance(override, dict) or any(name not in allowed for name in override):
        return None
    chosen = {name: override.get(name, policy.get(name)) for name in allowed}
    if (
        not isinstance(chosen["effective_rate_threshold"], (int, float))
        or not 0 <= chosen["effective_rate_threshold"] <= 0.1
        or not isinstance(chosen["explicit_ship_to_precedence"], bool)
        or not isinstance(chosen["allow_sales_generated"], bool)
    ):
        return None
    return chosen


def update_registry(registry, discovery, decisions):
    """Create a new policy snapshot only from explicit, attributed approvals."""
    if not isinstance(decisions.get("decisions"), list):
        raise ValueError("Decisions must contain a decisions list")
    provenance = registry_approval.approval_provenance(decisions)
    proposals = {
        item.get("template_fingerprint"): item
        for item in discovery.get("policy_proposals", [])
        if isinstance(item, dict)
    }
    rules, updates = list(registry["rules"]), []
    for decision in decisions["decisions"]:
        proposal = (
            proposals.get(decision.get("template_fingerprint"))
            if isinstance(decision, dict)
            else None
        )
        if (
            not isinstance(decision, dict)
            or decision.get("decision") != "approve"
            or not proposal
            or proposal.get("status") != "proposed"
        ):
            continue
        policy = policy_with_override(
            proposal.get("policy_proposal", {}), decision.get("policy_override", {})
        )
        if policy is None:
            continue
        scope = decision.get("scope", {})
        dimensions = scope.get("dimensions", scope) if isinstance(scope, dict) else None
        date_values = (
            (scope.get("effective_from"), scope.get("effective_to"))
            if isinstance(scope, dict)
            else ()
        )
        try:
            valid_dates = all(value is None or date.fromisoformat(value) for value in date_values)
        except (TypeError, ValueError):
            valid_dates = False
        if (
            not isinstance(scope, dict)
            or not isinstance(dimensions, dict)
            or any(
                not isinstance(name, str) or not isinstance(value, str) or not value.strip()
                for name, value in dimensions.items()
            )
            or not valid_dates
        ):
            continue
        scope = {
            "dimensions": dimensions,
            **{name: scope[name] for name in ("effective_from", "effective_to") if scope.get(name)},
        }
        scope_key = json.dumps(scope, sort_keys=True)
        rule = {
            "rule_id": "allocation-"
            + hashlib.sha256((proposal["template_fingerprint"] + scope_key).encode()).hexdigest()[
                :16
            ],
            **provenance,
            "template_fingerprint": proposal["template_fingerprint"],
            "scope": scope,
            "policy": policy,
            "effective_from": stamp(),
            "approved_from_template": proposal["template_id"],
            "registry_version": int(registry.get("registry_version", 0)) + 1,
        }
        if not any(
            item.get("rule_id") == rule["rule_id"] for item in rules if isinstance(item, dict)
        ):
            rules.append(rule)
            updates.append(rule)
    return {
        "registry_version": int(registry.get("registry_version", 0)) + 1,
        "generated_at": stamp(),
        "rules": rules,
        "update_log": updates,
    }


def main():
    """Provide apply, proposal-only discovery, and immutable registry-update commands."""
    load_project_env()
    parser = argparse.ArgumentParser(
        description="Apply review-safe commission allocation policies."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument(
        "input", help="Attributed-record artifact whose legs the approved policy is applied to."
    )
    apply_parser.add_argument("--registry", required=True)
    apply_parser.add_argument("--out", required=True)
    apply_parser.add_argument("--exceptions", required=True)
    discover_parser = sub.add_parser("discover")
    discover_parser.add_argument(
        "input",
        help=(
            "Source-template observation artifact scanned for candidate effective-dated "
            "policy rules. Build it with template_observations.py; the attributed-record "
            "artifact belongs to `apply`, and is refused here."
        ),
    )
    discover_parser.add_argument("--registry")
    discover_parser.add_argument("--out", required=True)
    discover_parser.add_argument("--exceptions", required=True)
    discover_parser.add_argument("--handoff-out", required=True)
    discover_parser.add_argument("--raw-dir", required=True)
    discover_parser.add_argument(
        "--enable", action="store_true", default=env_bool("ALLOCATION_POLICY_ENABLED", False)
    )
    discover_parser.add_argument("--model", default=llm_model())
    discover_parser.add_argument(
        "--reasoning-effort", default=env_value("ALLOCATION_POLICY_REASONING_EFFORT", "medium")
    )
    template_parser = sub.add_parser("decision-template")
    template_parser.add_argument("discovery")
    template_parser.add_argument("--out", required=True)
    update_parser = sub.add_parser("registry-update")
    update_parser.add_argument(
        "registry", help="Existing approved allocation-policy registry to amend."
    )
    update_parser.add_argument("discovery")
    update_parser.add_argument(
        "decisions", help="Client decision artifact naming which discovered rules are approved."
    )
    update_parser.add_argument("--out", required=True)
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        if args.command == "apply":
            result = apply(
                load_legs(args.input), load_registry(args.registry), args.out, args.exceptions
            )
        elif args.command == "decision-template":
            if Path(args.out).exists():
                raise ValueError("Decision-template output must be new")
            result = client_decision_template(json.loads(Path(args.discovery).read_text()))
            Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
        elif args.command == "registry-update":
            if Path(args.out).exists():
                raise ValueError("Registry output must be new")
            result = update_registry(
                load_registry(args.registry),
                json.loads(Path(args.discovery).read_text()),
                json.loads(Path(args.decisions).read_text()),
            )
            Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
        else:
            client = (
                build_client(
                    env_value("ALLOCATION_POLICY_CREDENTIAL_ENV", "OPENAI_API_KEY"),
                    env_float("ALLOCATION_POLICY_TIMEOUT_SECONDS", 120.0),
                    env_int("ALLOCATION_POLICY_MAX_RETRIES", 2),
                )
                if args.enable
                else None
            )
            result = discover(
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
        sys.exit(f"Allocation policy failed: {exc}")


if __name__ == "__main__":
    main()
