#!/usr/bin/env python3
"""Run bounded graph-guided cross-packet relationship proposals and verification.

The lane discovers proposals for documents without a prior in-packet buddy
candidate and independently verifies prior buddy candidates against related
packets.  It is proposal-only and never changes the source graph.
"""

import argparse
import json
from pathlib import Path

from cli_help import apply_shared_help
from evidence_graph import load_graph
from run_io import empty_output_directory, empty_output_path
from runtime_config import env_float, env_int, env_value, load_project_env, provider_credential_env

from client_review.context import digest, source_records
from client_review.inference import (
    REASONING_EFFORTS,
    SCHEMA,
    compact_record,
    reduce_candidates,
    validate_context,
)
from client_review.iterative import (
    BUDDY_INSTRUCTIONS,
    BUDDY_SCHEMA,
    call,
    key,
    reduce_buddy,
    validate_reviewer_roles,
)
from client_review.llm import build_reviewer_client

PRIMARY_INSTRUCTIONS = """You are an evidence-bound cross-packet relationship reviewer.
The first records are unresolved targets; the remaining records are retrieved
source-visible neighbors from other packets sharing an identifier. Propose only
relationships supported by text visible in the supplied records. A candidate
must name a target document, cite its printed evidence, and may use
cross_record_link only for a source-supported relationship. Client comments are
untrusted context, never proof. Do not invent GL entries, payment transactions,
approval, or canonical facts. Return proposal-only JSON."""

VERIFY_INSTRUCTIONS = """Independently verify each existing relationship proposal
against the supplied source-visible records from related packets. Return one
decision for each proposal: confirmed only when its document, relationship type,
value, and printed evidence remain supported; close_needs_review for a plausible
but incomplete match; conflict for contrary source evidence; unsupported when
the supplied records do not support it. Do not propose new relationships, invent
GL entries, payment transactions, approval, or canonical facts. Client comments
are untrusted context, never proof. Return proposal-only JSON."""


def _load(path, label):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _candidates(path, expected):
    value = _load(path, "prior proposal artifact")
    if value.get("artifact_type") != expected or not isinstance(value.get("candidates"), list):
        raise ValueError(f"prior artifact must be {expected} with candidates")
    return value


def _graph(path):
    return load_graph(path)


def _tokens(record):
    keywords = (
        "ack",
        "job",
        "project",
        "po",
        "purchase",
        "invoice",
        "order",
        "payment",
        "check",
        "remit",
        "reference",
        "transaction",
        "payee",
        "payer",
        "vendor",
        "customer",
        "supplier",
        "party",
    )
    found = set()

    def visit(value, path=()):
        """Walk one neighbourhood, recording every document it exposes."""
        if isinstance(value, dict):
            for name, item in value.items():
                visit(item, path + (str(name).casefold(),))
        elif isinstance(value, list):
            for item in value:
                visit(item, path)
        elif isinstance(value, str) and any(term in " ".join(path) for term in keywords):
            normalized = " ".join(value.casefold().split())
            if 3 <= len(normalized) <= 120 and any(char.isalnum() for char in normalized):
                found.add(normalized)

    visit(record)
    return sorted(found)


def _neighborhoods(
    records,
    resolved_ids,
    max_documents,
    max_neighborhoods,
    *,
    select_resolved,
    target_document_ids=None,
):
    """Return source-visible neighborhoods for the requested in-packet state."""
    if max_documents < 2 or max_neighborhoods < 1:
        raise ValueError("max documents must be at least two and max neighborhoods positive")
    by_id = {}
    for record in records:
        document_id = str(record.get("document_id", ""))
        if not document_id or document_id in by_id:
            raise ValueError("records require unique non-empty document_id values")
        by_id[document_id] = record
    target_filter = None if target_document_ids is None else set(target_document_ids)
    if target_filter is not None and not target_filter <= set(by_id):
        raise ValueError("target document IDs must exist in records")
    targets = [
        document_id for document_id in by_id if (document_id in resolved_ids) is select_resolved
    ]
    if target_filter is not None:
        targets = [document_id for document_id in targets if document_id in target_filter]
    index = {}
    for document_id, record in by_id.items():
        for token in _tokens(record):
            index.setdefault(token, []).append(document_id)
    selected, skipped = [], []
    for document_id in targets:
        exception_source = {
            "document_id": document_id,
            "source_document_ids": [document_id],
        }
        matches = {}
        for token in _tokens(by_id[document_id]):
            for neighbor in index[token]:
                if neighbor != document_id:
                    matches.setdefault(neighbor, []).append(token)
        if not matches:
            skipped.append(
                {
                    **exception_source,
                    "reason": (
                        "cross_packet_verification_no_shared_source_identifier"
                        if select_resolved
                        else "cross_packet_no_shared_source_identifier"
                    ),
                }
            )
            continue
        if len(selected) >= max_neighborhoods:
            skipped.append(
                {
                    **exception_source,
                    "reason": (
                        "cross_packet_verification_deferred_by_cap"
                        if select_resolved
                        else "cross_packet_discovery_deferred_by_cap"
                    ),
                }
            )
            continue
        ordered = sorted(matches, key=lambda item: (-len(matches[item]), item))
        related = ordered[: max_documents - 1]
        tokens = sorted({token for neighbor in related for token in matches[neighbor]})
        selected.append(
            {
                "target_document_ids": [document_id],
                "related_document_ids": related,
                "tokens": tokens,
            }
        )
    return selected, skipped


def neighborhoods(
    records_path,
    primary_path,
    buddy_path,
    graph_path,
    max_documents,
    max_neighborhoods,
    *,
    target_document_ids=None,
    extra_buddy_candidates=(),
):
    """Select bounded cross-packet discovery neighborhoods for unresolved documents."""
    records = source_records(records_path)
    _candidates(primary_path, "client_review_iterative_primary_v1")
    buddy = _candidates(buddy_path, "client_review_iterative_buddy_v1")
    _graph(graph_path)
    resolved_ids = {
        str(item.get("document_id", "")) for item in [*buddy["candidates"], *extra_buddy_candidates]
    }
    return _neighborhoods(
        records,
        resolved_ids,
        max_documents,
        max_neighborhoods,
        select_resolved=False,
        target_document_ids=target_document_ids,
    )


def verification_neighborhoods(
    records_path,
    buddy_path,
    graph_path,
    max_documents,
    max_neighborhoods,
    *,
    target_document_ids=None,
    extra_buddy_candidates=(),
):
    """Select bounded, cross-packet verification neighborhoods for resolved documents."""
    records = source_records(records_path)
    buddy = _candidates(buddy_path, "client_review_iterative_buddy_v1")
    _graph(graph_path)
    resolved_ids = {
        str(item.get("document_id", "")) for item in [*buddy["candidates"], *extra_buddy_candidates]
    }
    return _neighborhoods(
        records,
        resolved_ids,
        max_documents,
        max_neighborhoods,
        select_resolved=True,
        target_document_ids=target_document_ids,
    )


def _packet(targets, related, comments, number, tokens, max_bytes):
    value = {
        "lane": "client_review_cross_packet_primary",
        "neighborhood": number,
        "instructions": PRIMARY_INSTRUCTIONS,
        "client_context": comments,
        "target_document_ids": [item["document_id"] for item in targets],
        "shared_source_identifiers": tokens,
        "source_records": [compact_record(item) for item in targets + related],
    }
    if len(json.dumps(value, sort_keys=True).encode()) > max_bytes:
        raise ValueError("cross-packet packet exceeds max-context-bytes")
    return value


def _verification_packet(targets, related, comments, number, tokens, proposals, max_bytes):
    value = {
        "lane": "client_review_cross_packet_verify",
        "neighborhood": number,
        "instructions": VERIFY_INSTRUCTIONS,
        "client_context": comments,
        "target_document_ids": [item["document_id"] for item in targets],
        "shared_source_identifiers": tokens,
        "source_records": [compact_record(item) for item in targets + related],
        "existing_candidates": [
            {"candidate_id": key(item), "candidate": item} for item in proposals
        ],
    }
    if len(json.dumps(value, sort_keys=True).encode()) > max_bytes:
        raise ValueError("cross-packet verification packet exceeds max-context-bytes")
    return value


def _verification_decisions(value, proposals):
    """Validate decisions and conservatively merge repeated candidate IDs.

    Providers occasionally emit the same decision more than once. The raw
    response is retained unchanged; here we collapse repeats into one
    proposal-level decision so a malformed repetition cannot discard an entire
    neighborhood. Conflicting repeated statuses remain ``conflict``.
    """
    if not isinstance(value, dict) or not isinstance(value.get("decisions"), list):
        raise ValueError("verification response must contain decisions")
    allowed = {key(item): item for item in proposals}
    decisions = {}
    for decision in value["decisions"]:
        candidate_id = decision.get("candidate_id") if isinstance(decision, dict) else None
        status = decision.get("status") if isinstance(decision, dict) else None
        if candidate_id not in allowed or status not in {
            "confirmed",
            "close_needs_review",
            "conflict",
            "unsupported",
        }:
            raise ValueError("verification decision violates contract")
        rationale = str(decision.get("rationale", ""))
        if candidate_id in decisions:
            existing = decisions[candidate_id]
            statuses = {existing["status"], status}
            merged_status = status if len(statuses) == 1 else "conflict"
            rationales = [existing["rationale"], rationale]
            existing["status"] = merged_status
            existing["rationale"] = (
                "duplicate candidate_id decisions merged; "
                f"statuses={','.join(sorted(statuses))}; "
                + " | ".join(item for item in rationales if item)
            )
            continue
        decisions[candidate_id] = {"status": status, "rationale": rationale}
    return {
        candidate_id: decisions.get(
            candidate_id,
            {"status": "unsupported", "rationale": "provider omitted verification decision"},
        )
        for candidate_id in allowed
    }


def _verification_status(primary, buddy):
    statuses = {primary["status"], buddy["status"]}
    if "conflict" in statuses:
        return "conflict"
    if "close_needs_review" in statuses:
        return "close_needs_review"
    if statuses == {"confirmed"}:
        return "confirmed"
    return "unsupported"


def retry_targets(exceptions_path):
    """Select only provider-failed neighborhoods from a retained exception artifact."""
    value = _load(exceptions_path, "cross-packet exceptions")
    if value.get("artifact_type") != "client_review_cross_packet_exceptions_v1":
        raise ValueError("retry exceptions must be client_review_cross_packet_exceptions_v1")
    exceptions = value.get("exceptions")
    if not isinstance(exceptions, list):
        raise ValueError("retry exceptions must contain an exceptions list")
    discovery, verification = set(), set()
    for item in exceptions:
        if not isinstance(item, dict):
            raise ValueError("retry exception entries must be objects")
        reason = item.get("reason")
        source_ids = item.get("source_document_ids")
        if reason in {
            "cross_packet_primary_or_buddy_failure",
            "cross_packet_verification_primary_or_buddy_failure",
        } and (
            not isinstance(source_ids, list)
            or not source_ids
            or not all(isinstance(source_id, str) and source_id for source_id in source_ids)
        ):
            raise ValueError("retry exception source document IDs must be non-empty strings")
        if source_ids is not None and (
            not isinstance(source_ids, list)
            or not all(isinstance(source_id, str) and source_id for source_id in source_ids)
        ):
            raise ValueError("retry exception source document IDs must be non-empty strings")
        if reason == "cross_packet_primary_or_buddy_failure":
            discovery.update(source_ids)
        elif reason == "cross_packet_verification_primary_or_buddy_failure":
            verification.update(source_ids)
    if not discovery and not verification:
        raise ValueError("retry exceptions contain no provider-failed cross-packet neighborhoods")
    return discovery, verification


def _run_once(
    context_path,
    records_path,
    primary_path,
    buddy_path,
    graph_path,
    out_path,
    exceptions_path,
    raw_dir,
    primary_client,
    buddy_client,
    primary_model,
    buddy_model,
    *,
    max_documents=12,
    max_neighborhoods=100,
    max_verification_neighborhoods=100,
    max_context_bytes=160000,
    reasoning_effort="medium",
    retries=1,
    backoff=1.0,
    max_backoff=30.0,
    discovery_target_document_ids=None,
    verification_target_document_ids=None,
    extra_buddy_candidates=(),
    primary_provider,
    buddy_provider,
):
    if reasoning_effort not in REASONING_EFFORTS:
        raise ValueError(f"unsupported reasoning effort: {reasoning_effort}")
    validate_reviewer_roles(primary_provider, buddy_provider, primary_model, buddy_model)
    context = _load(context_path, "client review context")
    _, comments = validate_context(context)
    selected, skipped = neighborhoods(
        records_path,
        primary_path,
        buddy_path,
        graph_path,
        max_documents,
        max_neighborhoods,
        target_document_ids=discovery_target_document_ids,
        extra_buddy_candidates=extra_buddy_candidates,
    )
    verification_selected, verification_skipped = verification_neighborhoods(
        records_path,
        buddy_path,
        graph_path,
        max_documents,
        max_verification_neighborhoods,
        target_document_ids=verification_target_document_ids,
        extra_buddy_candidates=extra_buddy_candidates,
    )
    records = {str(item["document_id"]): item for item in source_records(records_path)}
    prior_buddy = _candidates(buddy_path, "client_review_iterative_buddy_v1")
    prior_by_document = {}
    for candidate in [*prior_buddy["candidates"], *extra_buddy_candidates]:
        prior_by_document.setdefault(str(candidate.get("document_id", "")), []).append(candidate)
    out_path, exceptions_path = empty_output_path(out_path), empty_output_path(exceptions_path)
    raw_dir = empty_output_directory(raw_dir)
    candidates, exceptions, completed = [], list(skipped) + list(verification_skipped), []
    for number, neighborhood in enumerate(selected, 1):
        target_ids = neighborhood["target_document_ids"]
        related_ids = neighborhood["related_document_ids"]
        all_ids = target_ids + related_ids
        try:
            request = _packet(
                [records[item] for item in target_ids],
                [records[item] for item in related_ids],
                comments,
                number,
                neighborhood["tokens"],
                max_context_bytes,
            )
            response, primary_retry = call(
                primary_client,
                primary_model,
                PRIMARY_INSTRUCTIONS,
                {"name": "reference_discovery", "schema": SCHEMA},
                request,
                primary_provider,
                reasoning_effort,
                retries,
                backoff,
                max_backoff,
                raw_dir / f"neighborhood-{number:03d}-primary.json",
            )
            proposed, rejected = reduce_candidates(response, set(target_ids))
            buddy_request = {
                **_packet(
                    [records[item] for item in target_ids],
                    [records[item] for item in related_ids],
                    comments,
                    number,
                    neighborhood["tokens"],
                    max_context_bytes,
                ),
                "lane": "client_review_cross_packet_buddy",
                "instructions": BUDDY_INSTRUCTIONS,
                "primary_candidates": [
                    {"candidate_id": key(item), "candidate": item} for item in proposed
                ],
            }
            if len(json.dumps(buddy_request, sort_keys=True).encode()) > max_context_bytes:
                raise ValueError("cross-packet buddy packet exceeds max-context-bytes")
            buddy_response, buddy_retry = call(
                buddy_client,
                buddy_model,
                BUDDY_INSTRUCTIONS,
                {"name": "reference_buddy_check", "schema": BUDDY_SCHEMA},
                buddy_request,
                buddy_provider,
                reasoning_effort,
                retries,
                backoff,
                max_backoff,
                raw_dir / f"neighborhood-{number:03d}-buddy.json",
            )
            accepted, buddy_rejected = reduce_buddy(buddy_response, proposed)
            candidates.extend(accepted)
            completed.append(
                {
                    "neighborhood": number,
                    "target_document_ids": target_ids,
                    "related_document_ids": related_ids,
                    "tokens": neighborhood["tokens"],
                    "primary_count": len(proposed),
                    "buddy_count": len(accepted),
                    "rejected": rejected + buddy_rejected,
                    "primary_retry_log": primary_retry,
                    "buddy_retry_log": buddy_retry,
                }
            )
        except Exception as exc:
            exception = {
                "neighborhood": number,
                "source_document_ids": all_ids,
                "reason": "cross_packet_primary_or_buddy_failure",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "disposition": "client_review_required",
            }
            (raw_dir / f"neighborhood-{number:03d}-exception.json").write_text(
                json.dumps(exception, indent=2) + "\n"
            )
            exceptions.append(exception)
    verifications, verification_completed = [], []
    for number, neighborhood in enumerate(verification_selected, 1):
        target_ids = neighborhood["target_document_ids"]
        related_ids = neighborhood["related_document_ids"]
        all_ids = target_ids + related_ids
        proposals = [
            candidate for document_id in target_ids for candidate in prior_by_document[document_id]
        ]
        try:
            if len(proposals) > BUDDY_SCHEMA["properties"]["decisions"]["maxItems"]:
                raise ValueError("cross-packet verification candidate count exceeds schema limit")
            request = _verification_packet(
                [records[item] for item in target_ids],
                [records[item] for item in related_ids],
                comments,
                number,
                neighborhood["tokens"],
                proposals,
                max_context_bytes,
            )
            primary_response, primary_retry = call(
                primary_client,
                primary_model,
                VERIFY_INSTRUCTIONS,
                {"name": "cross_packet_primary_verification", "schema": BUDDY_SCHEMA},
                request,
                primary_provider,
                reasoning_effort,
                retries,
                backoff,
                max_backoff,
                raw_dir / f"verification-{number:03d}-primary.json",
            )
            primary_decisions = _verification_decisions(primary_response, proposals)
            buddy_request = {
                **request,
                "lane": "client_review_cross_packet_verify_buddy",
                "instructions": VERIFY_INSTRUCTIONS,
            }
            buddy_response, buddy_retry = call(
                buddy_client,
                buddy_model,
                VERIFY_INSTRUCTIONS,
                {"name": "cross_packet_buddy_verification", "schema": BUDDY_SCHEMA},
                buddy_request,
                buddy_provider,
                reasoning_effort,
                retries,
                backoff,
                max_backoff,
                raw_dir / f"verification-{number:03d}-buddy.json",
            )
            buddy_decisions = _verification_decisions(buddy_response, proposals)
            for proposal in proposals:
                candidate_id = key(proposal)
                primary_decision = primary_decisions[candidate_id]
                buddy_decision = buddy_decisions[candidate_id]
                verifications.append(
                    {
                        "candidate_id": candidate_id,
                        "existing_candidate": proposal,
                        "primary_status": primary_decision["status"],
                        "primary_rationale": primary_decision["rationale"],
                        "buddy_status": buddy_decision["status"],
                        "buddy_rationale": buddy_decision["rationale"],
                        "verification_status": _verification_status(
                            primary_decision, buddy_decision
                        ),
                        "proposal_only": True,
                        "production_approval_permitted": False,
                    }
                )
            verification_completed.append(
                {
                    "neighborhood": number,
                    "target_document_ids": target_ids,
                    "related_document_ids": related_ids,
                    "tokens": neighborhood["tokens"],
                    "candidate_count": len(proposals),
                    "primary_retry_log": primary_retry,
                    "buddy_retry_log": buddy_retry,
                }
            )
        except Exception as exc:
            exception = {
                "neighborhood": number,
                "source_document_ids": all_ids,
                "reason": "cross_packet_verification_primary_or_buddy_failure",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "disposition": "client_review_required",
            }
            (raw_dir / f"verification-{number:03d}-exception.json").write_text(
                json.dumps(exception, indent=2) + "\n"
            )
            exceptions.append(exception)
    output = {
        "artifact_type": "client_review_cross_packet_v1",
        "proposal_only": True,
        "production_approval_permitted": False,
        "gate_status": "blocked_pending_client_review",
        "provider": primary_provider,
        "model": primary_model,
        "buddy_provider": buddy_provider,
        "buddy_model": buddy_model,
        "context_sha256": digest(context_path),
        "records_sha256": digest(records_path),
        "primary_sha256": digest(primary_path),
        "buddy_sha256": digest(buddy_path),
        "graph_sha256": digest(graph_path),
        "summary": {
            "neighborhoods": len(selected),
            "completed_neighborhoods": len(completed),
            "candidates": len(candidates),
            "verification_neighborhoods": len(verification_selected),
            "completed_verification_neighborhoods": len(verification_completed),
            "verifications": len(verifications),
            "exceptions": len(exceptions),
        },
        "neighborhoods": completed,
        "candidates": candidates,
        "verification_neighborhoods": verification_completed,
        "verifications": verifications,
    }
    out_path.write_text(json.dumps(output, indent=2) + "\n")
    exceptions_path.write_text(
        json.dumps(
            {
                "artifact_type": "client_review_cross_packet_exceptions_v1",
                "proposal_only": True,
                "exceptions": exceptions,
            },
            indent=2,
        )
        + "\n"
    )
    return output


def _material_candidates(candidates, known_candidate_ids):
    """Return new, independently confirmed cross-packet relationships."""
    return [
        candidate
        for candidate in candidates
        if candidate.get("buddy_status") == "confirmed"
        and key(candidate) not in known_candidate_ids
    ]


def _next_discovery_targets(records, material_candidates, resolved_ids, processed_ids):
    """Focus the next pass on unresolved records exposed by new relationships."""
    tokens = {
        " ".join(str(candidate.get("candidate_value", "")).casefold().split())
        for candidate in material_candidates
    }
    tokens.discard("")
    return {
        str(record.get("document_id", ""))
        for record in records
        if str(record.get("document_id", "")) not in resolved_ids | processed_ids
        and tokens.intersection(_tokens(record))
    }


def run(
    context_path,
    records_path,
    primary_path,
    buddy_path,
    graph_path,
    out_path,
    exceptions_path,
    raw_dir,
    primary_client,
    buddy_client,
    primary_model,
    buddy_model,
    *,
    max_documents=12,
    max_neighborhoods=100,
    max_verification_neighborhoods=100,
    max_context_bytes=160000,
    reasoning_effort="medium",
    retries=1,
    backoff=1.0,
    max_backoff=30.0,
    max_iterations=2,
    convergence_min_new_candidates=1,
    primary_provider,
    buddy_provider,
    retry_exceptions_path=None,
):
    """Run one focused follow-up only while new confirmed links are found."""
    if not 1 <= max_iterations <= 5:
        raise ValueError("max iterations must be between 1 and 5")
    if convergence_min_new_candidates < 1:
        raise ValueError("convergence minimum must be positive")
    validate_reviewer_roles(primary_provider, buddy_provider, primary_model, buddy_model)
    out_path, exceptions_path = empty_output_path(out_path), empty_output_path(exceptions_path)
    raw_dir = empty_output_directory(raw_dir)
    records = source_records(records_path)
    base_buddy = _candidates(buddy_path, "client_review_iterative_buddy_v1")["candidates"]
    known_candidate_ids = {key(candidate) for candidate in base_buddy}
    confirmed_cross_candidates, processed_discovery_ids = [], set()
    if retry_exceptions_path:
        discovery_targets, verification_targets = retry_targets(retry_exceptions_path)
    else:
        discovery_targets = verification_targets = None
    all_candidates, all_verifications, all_neighborhoods, all_verification_neighborhoods = (
        [],
        [],
        [],
        [],
    )
    all_exceptions, rounds = [], []
    termination_reason = "max_iterations_reached"
    for iteration in range(1, max_iterations + 1):
        iteration_out = raw_dir / f"iteration-{iteration:02d}-result.json"
        iteration_exceptions = raw_dir / f"iteration-{iteration:02d}-exceptions.json"
        result = _run_once(
            context_path,
            records_path,
            primary_path,
            buddy_path,
            graph_path,
            iteration_out,
            iteration_exceptions,
            raw_dir / f"iteration-{iteration:02d}",
            primary_client,
            buddy_client,
            primary_model,
            buddy_model,
            max_documents=max_documents,
            max_neighborhoods=max_neighborhoods,
            max_verification_neighborhoods=max_verification_neighborhoods,
            max_context_bytes=max_context_bytes,
            reasoning_effort=reasoning_effort,
            retries=retries,
            backoff=backoff,
            max_backoff=max_backoff,
            discovery_target_document_ids=discovery_targets,
            verification_target_document_ids=verification_targets,
            extra_buddy_candidates=confirmed_cross_candidates,
            primary_provider=primary_provider,
            buddy_provider=buddy_provider,
        )
        exception_items = _load(iteration_exceptions, "cross-packet exceptions")["exceptions"]
        material_candidates = _material_candidates(result["candidates"], known_candidate_ids)
        known_candidate_ids.update(key(candidate) for candidate in material_candidates)
        confirmed_cross_candidates.extend(material_candidates)
        processed_discovery_ids.update(
            document_id
            for item in result["neighborhoods"]
            for document_id in item["target_document_ids"]
        )
        all_candidates.extend(
            {**candidate, "cross_packet_iteration": iteration} for candidate in result["candidates"]
        )
        all_verifications.extend(
            {**verification, "cross_packet_iteration": iteration}
            for verification in result["verifications"]
        )
        all_neighborhoods.extend(result["neighborhoods"])
        all_verification_neighborhoods.extend(result["verification_neighborhoods"])
        all_exceptions.extend(exception_items)
        resolved_ids = {
            str(candidate.get("document_id", ""))
            for candidate in [*base_buddy, *confirmed_cross_candidates]
        }
        discovery_targets = _next_discovery_targets(
            records, material_candidates, resolved_ids, processed_discovery_ids
        )
        verification_targets = {
            str(candidate.get("document_id", "")) for candidate in material_candidates
        }
        rounds.append(
            {
                "iteration": iteration,
                "summary": result["summary"],
                "new_material_candidate_count": len(material_candidates),
                "new_material_candidate_ids": [key(candidate) for candidate in material_candidates],
                "next_discovery_target_count": len(discovery_targets),
                "next_verification_target_count": len(verification_targets),
            }
        )
        if len(material_candidates) < convergence_min_new_candidates:
            termination_reason = "converged_no_new_material_relationships"
            break
    output = {
        "artifact_type": "client_review_cross_packet_v1",
        "proposal_only": True,
        "production_approval_permitted": False,
        "gate_status": "blocked_pending_client_review",
        "provider": primary_provider,
        "model": primary_model,
        "buddy_provider": buddy_provider,
        "buddy_model": buddy_model,
        "context_sha256": digest(context_path),
        "records_sha256": digest(records_path),
        "primary_sha256": digest(primary_path),
        "buddy_sha256": digest(buddy_path),
        "graph_sha256": digest(graph_path),
        "retry_exceptions_sha256": digest(retry_exceptions_path) if retry_exceptions_path else None,
        "summary": {
            "neighborhoods": len(all_neighborhoods),
            "candidates": len(all_candidates),
            "verification_neighborhoods": len(all_verification_neighborhoods),
            "verifications": len(all_verifications),
            "exceptions": len(all_exceptions),
            "requested_iterations": max_iterations,
            "completed_iterations": len(rounds),
            "termination_reason": termination_reason,
        },
        "iterations": rounds,
        "neighborhoods": all_neighborhoods,
        "candidates": all_candidates,
        "verification_neighborhoods": all_verification_neighborhoods,
        "verifications": all_verifications,
    }
    out_path.write_text(json.dumps(output, indent=2) + "\n")
    exceptions_path.write_text(
        json.dumps(
            {
                "artifact_type": "client_review_cross_packet_exceptions_v1",
                "proposal_only": True,
                "exceptions": all_exceptions,
            },
            indent=2,
        )
        + "\n"
    )
    return output


def main():
    parser = argparse.ArgumentParser(
        description="Run bounded graph-guided cross-packet proposals and verification."
    )
    parser.add_argument("context")
    parser.add_argument("--records", required=True)
    parser.add_argument(
        "--primary",
        required=True,
        help=(
            "Retained primary-lane relationship-proposal artifact from "
            "client_review_iterative.py (--primary-out). Provider selection for "
            "this lane comes only from CLIENT_REVIEW_CROSS_PACKET_PRIMARY_PROVIDER "
            "in .env, never from this option."
        ),
    )
    parser.add_argument(
        "--buddy",
        required=True,
        help=(
            "Retained buddy-lane confirmation artifact from "
            "client_review_iterative.py (--buddy-out). Provider selection for "
            "this lane comes only from CLIENT_REVIEW_CROSS_PACKET_BUDDY_PROVIDER "
            "in .env, never from this option."
        ),
    )
    parser.add_argument("--graph", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--exceptions", required=True)
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument(
        "--retry-exceptions",
        help="retry only provider-failed neighborhoods from this retained exception artifact",
    )
    parser.add_argument("--max-documents", type=int)
    parser.add_argument(
        "--max-neighborhoods",
        type=int,
        help="Maximum source neighborhoods inspected for new links in one pass.",
    )
    parser.add_argument(
        "--max-verification-neighborhoods",
        type=int,
        help="Maximum neighborhoods used to independently verify already-resolved proposals.",
    )
    parser.add_argument("--max-context-bytes", type=int)
    parser.add_argument("--max-iterations", type=int)
    parser.add_argument(
        "--convergence-min-new-candidates",
        type=int,
        help="Stop after a completed pass yields fewer than this many new source-backed, buddy-confirmed relationships.",
    )
    parser.add_argument("--reasoning-effort", choices=REASONING_EFFORTS)
    apply_shared_help(parser)
    args = parser.parse_args()
    load_project_env()
    try:
        primary_provider = env_value("CLIENT_REVIEW_CROSS_PACKET_PRIMARY_PROVIDER", "")
        buddy_provider = env_value("CLIENT_REVIEW_CROSS_PACKET_BUDDY_PROVIDER", "")
        primary_model = env_value("CLIENT_REVIEW_CROSS_PACKET_PRIMARY_MODEL", "")
        buddy_model = env_value("CLIENT_REVIEW_CROSS_PACKET_BUDDY_MODEL", "")
        validate_reviewer_roles(primary_provider, buddy_provider, primary_model, buddy_model)
        primary = build_reviewer_client(
            primary_provider,
            env_value(
                "CLIENT_REVIEW_CROSS_PACKET_PRIMARY_CREDENTIAL_ENV",
                provider_credential_env(primary_provider),
            ),
            env_float("CLIENT_REVIEW_CROSS_PACKET_PRIMARY_TIMEOUT_SECONDS", 180.0),
            env_int("CLIENT_REVIEW_CROSS_PACKET_MAX_RETRIES", 4),
        )
        buddy = build_reviewer_client(
            buddy_provider,
            env_value(
                "CLIENT_REVIEW_CROSS_PACKET_BUDDY_CREDENTIAL_ENV",
                provider_credential_env(buddy_provider),
            ),
            env_float("CLIENT_REVIEW_CROSS_PACKET_BUDDY_TIMEOUT_SECONDS", 300.0),
            env_int("CLIENT_REVIEW_CROSS_PACKET_MAX_RETRIES", 4),
        )
        result = run(
            args.context,
            args.records,
            args.primary,
            args.buddy,
            args.graph,
            args.out,
            args.exceptions,
            args.raw_dir,
            primary,
            buddy,
            primary_model,
            buddy_model,
            max_documents=(
                args.max_documents
                if args.max_documents is not None
                else env_int("CLIENT_REVIEW_CROSS_PACKET_MAX_DOCUMENTS", 12)
            ),
            max_neighborhoods=(
                args.max_neighborhoods
                if args.max_neighborhoods is not None
                else env_int("CLIENT_REVIEW_CROSS_PACKET_MAX_NEIGHBORHOODS", 100)
            ),
            max_verification_neighborhoods=(
                args.max_verification_neighborhoods
                if args.max_verification_neighborhoods is not None
                else env_int("CLIENT_REVIEW_CROSS_PACKET_MAX_VERIFICATION_NEIGHBORHOODS", 100)
            ),
            max_context_bytes=(
                args.max_context_bytes
                if args.max_context_bytes is not None
                else env_int("CLIENT_REVIEW_CROSS_PACKET_MAX_CONTEXT_BYTES", 160000)
            ),
            reasoning_effort=(
                args.reasoning_effort
                if args.reasoning_effort is not None
                else env_value("CLIENT_REVIEW_CROSS_PACKET_REASONING_EFFORT", "medium")
            ),
            retries=env_int("CLIENT_REVIEW_CROSS_PACKET_MAX_RETRIES", 4),
            backoff=env_float("CLIENT_REVIEW_CROSS_PACKET_RETRY_BACKOFF_SECONDS", 5.0),
            max_backoff=env_float("CLIENT_REVIEW_CROSS_PACKET_MAX_BACKOFF_SECONDS", 180.0),
            max_iterations=(
                args.max_iterations
                if args.max_iterations is not None
                else env_int("CLIENT_REVIEW_CROSS_PACKET_MAX_ITERATIONS", 2)
            ),
            convergence_min_new_candidates=(
                args.convergence_min_new_candidates
                if args.convergence_min_new_candidates is not None
                else env_int("CLIENT_REVIEW_CROSS_PACKET_CONVERGENCE_MIN_NEW_CANDIDATES", 1)
            ),
            primary_provider=primary_provider,
            buddy_provider=buddy_provider,
            retry_exceptions_path=args.retry_exceptions,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cross-packet review failed: {exc}") from exc
    print(json.dumps(result["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
