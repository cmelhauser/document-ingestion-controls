#!/usr/bin/env python3
"""Deterministic JSON evidence graph and reusable exact-query commands."""

import argparse
import hashlib
import json
import re
from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help

SCHEMA_VERSION = "1.0"
ARTIFACT_TYPE = "evidence_graph_v1"
SUPPORTED = {
    "consensus_records",
    "validated_records",
    "reference_discovery",
    "reference_adjudication",
    "client_review_iterative_primary",
    "client_review_iterative_buddy",
    "client_review_cross_packet",
    "client_decisions",
}
MAX_LIMIT = 1000
# Whole-token reference terms. Substring matching on "po" and "ack" claimed
# postal_code, shipping_point, package_tracking, report_date, and backorder as
# business references, which polluted path/candidates/contradictions and made
# unrelated documents collide on one reference node.
REFERENCE_FIELD_TOKENS = frozenset(
    {
        "ack",
        "acknowledgement",
        "acknowledgment",
        "check",
        "claim",
        "job",
        "order",
        "payment",
        "po",
        "project",
        "remit",
        "remittance",
        "ro",
        "so",
        "wo",
    }
)
SCHEMA_SURFACE_ARTIFACT_TYPE = "evidence_graph_schema_surface_inventory_v1"
DISCOVERY_TOPIC_TERMS = {
    "address": ("address",),
    "contact": ("contact", "email", "e_mail", "phone", "telephone", "mobile", "fax", "attn"),
    "party": (
        "buyer",
        "seller",
        "vendor",
        "customer",
        "dealer",
        "payer",
        "payee",
        "shipper",
        "consignee",
        "brand",
        "origin",
        "destination",
        "sales_rep",
        "representative",
    ),
    "sales_representative": (
        "sales_rep",
        "sales_representative",
        "salesperson",
        "sales_person",
        "representative",
        "account_manager",
        "account_executive",
        "agent",
    ),
}
UNSTRUCTURED_SIGNAL_PATTERNS = {
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    # Require a terminal three/four-digit exchange so dates, decimal amounts,
    # and general numeric identifiers do not become phone candidates.
    "phone": re.compile(
        r"(?<!\w)(?:\+\d{1,3}[ .-]?)?(?:\(?\d{2,4}\)?[ .-]){1,3}\d{3,4}"
        r"(?:\s*(?:x|ext\.?|extension)\s*\d{1,6})?(?!\w)",
        re.IGNORECASE,
    ),
    "attention": re.compile(r"\b(?:attn\.?|attention)\b", re.IGNORECASE),
    "contact_or_representative_label": re.compile(
        r"\b(?:contact|sales\s*(?:rep(?:resentative)?|person)|representative|account\s*(?:manager|executive))\b",
        re.IGNORECASE,
    ),
}


def canonical_bytes(value):
    """Serialize a value the one way, so the same value always hashes the same."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def stable_id(kind, value):
    """Return a node ID that survives a rebuild, refusing an empty value.

    An empty value would collapse every node of a kind into one, which is a
    graph that silently claims things are the same thing.
    """
    if not str(value).strip():
        raise ValueError("stable ID value must be non-empty")
    return f"{kind}:{value}"


def digest(path):
    """Return the content hash that binds a graph to the artifact it was built from."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_manifest(path):
    """Load the hash-bound manifest and verify every artifact it names."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION or not str(data.get("run_id", "")).strip():
        raise ValueError("manifest schema_version and run_id are required")
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("manifest artifacts must be a non-empty list")
    seen = set()
    root = path.parent.resolve()
    entries = []
    for item in artifacts:
        if not isinstance(item, dict) or item.get("artifact_type") not in SUPPORTED:
            raise ValueError("unsupported artifact type")
        artifact_id = str(item.get("artifact_id", ""))
        if not artifact_id or artifact_id in seen:
            raise ValueError("artifact IDs must be non-empty and unique")
        seen.add(artifact_id)
        candidate = (root / str(item.get("path", ""))).resolve()
        if root not in candidate.parents or not candidate.is_file():
            raise ValueError("artifact path must remain inside manifest directory")
        expected = item.get("sha256")
        if (
            not isinstance(expected, str)
            or len(expected) != 64
            or expected != expected.casefold()
            or digest(candidate) != expected
        ):
            raise ValueError("artifact sha256 mismatch")
        entries.append((artifact_id, item["artifact_type"], candidate, expected))
    return data["run_id"], entries


def _records(data):
    """Return the records and the JSON Pointer prefix of the container holding them.

    The pointer must be derived by the same code that chose the container, so a
    top-level array cites ``/0`` and a ``results`` container cites ``/results/0``
    instead of a guessed path that does not resolve in the cited artifact.
    """
    if isinstance(data, list):
        return data, ""
    for key in ("documents", "results", "records", "candidates", "decisions"):
        if isinstance(data.get(key), list):
            return data[key], f"/{_pointer_token(key)}"
    raise ValueError("artifact records collection is missing")


def _artifact_records(data, artifact_type):
    """Return graph records, including immutable cross-packet verification overlays."""
    if artifact_type != "client_review_cross_packet":
        return _records(data)
    candidates = data.get("candidates")
    verifications = data.get("verifications", [])
    if not isinstance(candidates, list) or not isinstance(verifications, list):
        raise ValueError("cross-packet candidates and verifications must be lists")
    return [
        {**item, "_graph_json_pointer": f"/candidates/{index}"}
        for index, item in enumerate(candidates)
    ] + [
        {
            **item.get("existing_candidate", {}),
            "cross_packet_verification_status": item.get("verification_status"),
            "cross_packet_primary_status": item.get("primary_status"),
            "cross_packet_buddy_status": item.get("buddy_status"),
            "_graph_json_pointer": f"/verifications/{index}",
        }
        for index, item in enumerate(verifications)
    ], ""


def _reference_field(field):
    """Return whether a field name carries a business reference, by whole token."""
    tokens = set(re.split(r"[^a-z0-9]+", str(field).casefold()))
    return bool(tokens & REFERENCE_FIELD_TOKENS)


def _node(node_id, node_type, properties, state="observed", provenance=None):
    return {
        "node_id": node_id,
        "node_type": node_type,
        "properties": properties,
        "approval_state": state,
        "protected": state in {"exception", "close_needs_review"},
        "provenance": provenance or [],
    }


def _edge(edge_type, source, target, state="proposed", provenance=None, properties=None):
    raw = {
        "edge_type": edge_type,
        "from_node_id": source,
        "to_node_id": target,
        "properties": properties or {},
    }
    edge_id = "edge:" + hashlib.sha256(canonical_bytes(raw)).hexdigest()
    return {
        "edge_id": edge_id,
        **raw,
        "approval_state": state,
        "protected": state != "approved",
        "provenance": provenance or [],
    }


def _add_edge(edges, value):
    """Merge a content-addressed edge, keeping the most protected observed state.

    ``edge_id`` is a hash of type and endpoints, so two artifacts asserting the
    same relationship produce the same identifier. They are one edge observed
    twice: retain both provenance entries rather than emitting duplicate IDs,
    which ``load_graph`` rejects outright.
    """
    existing = edges.get(value["edge_id"])
    if existing is None:
        edges[value["edge_id"]] = value
        return
    if existing["approval_state"] != value["approval_state"]:
        existing["approval_state"] = "proposed"
        existing["protected"] = True
    existing["provenance"] = sorted(
        {
            json.dumps(p, sort_keys=True): p for p in existing["provenance"] + value["provenance"]
        }.values(),
        key=lambda item: json.dumps(item, sort_keys=True),
    )


def _add_node(nodes, value, exceptions, artifact_id, index):
    """Merge a node, retaining a conflicting reading as review evidence.

    Two artifacts describing the same entity differently is exactly the signal the
    graph exists to surface -- a consensus record and its validated counterpart
    disagree by design. Abandoning the build would emit neither a record nor an
    exception, so the first reading is retained, the node is protected, and the
    conflict is registered with both property sets and both provenance entries.
    """
    existing = nodes.get(value["node_id"])
    if existing is None:
        nodes[value["node_id"]] = value
        return
    if existing["properties"] != value["properties"] or existing["node_type"] != value["node_type"]:
        existing["protected"] = True
        exceptions.append(
            {
                "reason": "graph_node_property_conflict",
                "artifact_id": artifact_id,
                "index": index,
                "node_id": value["node_id"],
                "retained_node_type": existing["node_type"],
                "conflicting_node_type": value["node_type"],
                "retained_properties": existing["properties"],
                "conflicting_properties": value["properties"],
                "conflicting_provenance": value["provenance"],
                "disposition": "client_review_required",
            }
        )
    existing["provenance"] = sorted(
        {
            json.dumps(p, sort_keys=True): p for p in existing["provenance"] + value["provenance"]
        }.values(),
        key=lambda item: json.dumps(item, sort_keys=True),
    )


def _adapt(data, artifact_id, artifact_type, sha, nodes, edges, exceptions):
    records, pointer_prefix = _artifact_records(data, artifact_type)
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            exceptions.append(
                {
                    "reason": "graph_source_record_malformed",
                    "artifact_id": artifact_id,
                    "index": index,
                    "disposition": "client_review_required",
                }
            )
            continue
        provenance = [
            {
                "artifact_id": artifact_id,
                "artifact_sha256": sha,
                "json_pointer": item.get("_graph_json_pointer", f"{pointer_prefix}/{index}"),
            }
        ]
        if artifact_type in {"consensus_records", "validated_records"}:
            document_id = str(item.get("document_id", ""))
            if not document_id:
                exceptions.append(
                    {
                        "reason": "graph_missing_document_id",
                        "artifact_id": artifact_id,
                        "index": index,
                        "disposition": "client_review_required",
                    }
                )
                continue
            node_id = stable_id("document", document_id)
            _add_node(
                nodes,
                _node(
                    node_id,
                    "document",
                    {
                        "document_type": item.get("document_type"),
                        "review_status": item.get("review_status"),
                    },
                    "observed",
                    provenance,
                ),
                exceptions,
                artifact_id,
                index,
            )
            for field, claim in (item.get("fields") or {}).items():
                if not isinstance(claim, dict) or claim.get("value") is None:
                    continue
                claim_id = (
                    "claim:"
                    + hashlib.sha256(
                        canonical_bytes([node_id, field, claim["value"], artifact_id, index])
                    ).hexdigest()
                )
                _add_node(
                    nodes,
                    _node(
                        claim_id,
                        "field_claim",
                        {
                            "subject_node_id": node_id,
                            "field": field,
                            "value": claim["value"],
                            "accepted": claim.get("accepted"),
                        },
                        "observed",
                        provenance,
                    ),
                    exceptions,
                    artifact_id,
                    index,
                )
                _add_edge(
                    edges["edges"],
                    _edge("document_has_claim", node_id, claim_id, "observed", provenance),
                )
                if _reference_field(field):
                    ref = stable_id("reference", str(claim["value"]))
                    _add_node(
                        nodes,
                        _node(
                            ref,
                            "reference",
                            {"field": field, "value": claim["value"], "reference_only": True},
                            "observed",
                            provenance,
                        ),
                        exceptions,
                        artifact_id,
                        index,
                    )
                    _add_edge(
                        edges["edges"],
                        _edge("document_references", node_id, ref, "proposed", provenance),
                    )
        elif artifact_type in {
            "reference_discovery",
            "reference_adjudication",
            "client_review_iterative_primary",
            "client_review_iterative_buddy",
            "client_review_cross_packet",
        }:
            value = str(item.get("candidate_value", item.get("value", "")))
            document_id = str(item.get("document_id", ""))
            if not value or not document_id:
                exceptions.append(
                    {
                        "reason": "graph_missing_endpoint",
                        "artifact_id": artifact_id,
                        "index": index,
                        "disposition": "client_review_required",
                    }
                )
                continue
            status = item.get(
                "cross_packet_verification_status",
                item.get("adjudication_status", item.get("buddy_status", "proposed")),
            )
            # Written as statements, not a nested conditional expression: coverage
            # measures arcs between statements, so every arm of a ternary -- including
            # the seven conditions of this one -- is invisible to the branch gate.
            # This is the most consequential classification the graph makes.
            state = "proposed"
            if status in {"close", "close_needs_review"}:
                state = "close_needs_review"
            elif status in {"matched", "confirmed"}:
                required = (
                    item.get("deterministic_evidence"),
                    item.get("evidence_validated"),
                    item.get("arithmetic_pass"),
                    item.get("identity_pass"),
                )
                disqualifying = (
                    item.get("protected_contradiction"),
                    item.get("missing_authority_dependency"),
                )
                if all(flag is True for flag in required) and not any(
                    flag is True for flag in disqualifying
                ):
                    state = "inferred_high_confidence"
            proposal_id = (
                "proposal:"
                + hashlib.sha256(
                    canonical_bytes(
                        [document_id, item.get("candidate_type"), value, artifact_id, index]
                    )
                ).hexdigest()
            )
            _add_node(
                nodes,
                _node(
                    proposal_id,
                    "proposal",
                    {
                        "candidate_type": item.get("candidate_type"),
                        "candidate_value": value,
                        "evidence_quote": item.get("evidence_quote", ""),
                        "status": status,
                        "verification_status": item.get("cross_packet_verification_status"),
                        "verification_primary_status": item.get("cross_packet_primary_status"),
                        "verification_buddy_status": item.get("cross_packet_buddy_status"),
                    },
                    state,
                    provenance,
                ),
                exceptions,
                artifact_id,
                index,
            )
            _add_edge(
                edges["edges"],
                _edge(
                    "proposal_about_document",
                    proposal_id,
                    stable_id("document", document_id),
                    "proposed",
                    provenance,
                ),
            )
        else:
            decision_id = str(item.get("decision_id", ""))
            if not decision_id:
                exceptions.append(
                    {
                        "reason": "graph_missing_endpoint",
                        "artifact_id": artifact_id,
                        "index": index,
                        "disposition": "client_review_required",
                    }
                )
                continue
            _add_node(
                nodes,
                _node(
                    stable_id("client_decision", decision_id),
                    "client_decision",
                    {
                        "choice": item.get("your_choice", item.get("choice")),
                        "note": item.get("your_note", item.get("note")),
                    },
                    "proposed",
                    provenance,
                ),
                exceptions,
                artifact_id,
                index,
            )


def _content_hash(graph):
    value = {
        key: graph.get(key)
        for key in ("schema_version", "run_id", "manifest_sha256", "nodes", "edges", "exceptions")
    }
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def load_graph(path):
    """Load a graph only when its retained content and endpoints verify."""
    graph = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(graph, dict):
        raise ValueError("evidence graph must be an object")
    if graph.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("unexpected evidence graph artifact type")
    if graph.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported evidence graph schema version")
    required = ("run_id", "manifest_sha256", "nodes", "edges", "exceptions", "graph_sha256")
    if any(key not in graph for key in required):
        raise ValueError("evidence graph is missing required fields")
    if not all(isinstance(graph[key], list) for key in ("nodes", "edges", "exceptions")):
        raise ValueError("evidence graph collections must be lists")
    if graph["graph_sha256"] != _content_hash(graph):
        raise ValueError("evidence graph sha256 does not match retained content")
    node_ids = []
    for node in graph["nodes"]:
        if not isinstance(node, dict) or any(
            key not in node
            for key in (
                "node_id",
                "node_type",
                "properties",
                "approval_state",
                "protected",
                "provenance",
            )
        ):
            raise ValueError("evidence graph nodes require the complete graph schema")
        if not all(
            isinstance(node[key], str) and node[key].strip()
            for key in ("node_id", "node_type", "approval_state")
        ):
            raise ValueError("evidence graph node identifiers and state must be non-empty strings")
        if not isinstance(node["properties"], dict):
            raise ValueError("evidence graph node properties must be an object")
        if not isinstance(node["protected"], bool):
            raise ValueError("evidence graph node protected flag must be boolean")
        if not isinstance(node["provenance"], list):
            raise ValueError("evidence graph node provenance must be a list")
        node_ids.append(node["node_id"])
    if len(set(node_ids)) != len(node_ids):
        raise ValueError("evidence graph node ids must be unique and non-empty")
    known_nodes = set(node_ids)
    edge_ids = []
    for edge in graph["edges"]:
        if not isinstance(edge, dict) or any(
            key not in edge
            for key in (
                "edge_id",
                "edge_type",
                "from_node_id",
                "to_node_id",
                "properties",
                "approval_state",
                "protected",
                "provenance",
            )
        ):
            raise ValueError("evidence graph edges require the complete graph schema")
        if not all(
            isinstance(edge[key], str) and edge[key].strip()
            for key in ("edge_id", "edge_type", "from_node_id", "to_node_id", "approval_state")
        ):
            raise ValueError("evidence graph edge identifiers and state must be non-empty strings")
        if not isinstance(edge["properties"], dict):
            raise ValueError("evidence graph edge properties must be an object")
        if not isinstance(edge["protected"], bool):
            raise ValueError("evidence graph edge protected flag must be boolean")
        if not isinstance(edge["provenance"], list):
            raise ValueError("evidence graph edge provenance must be a list")
        if edge.get("from_node_id") not in known_nodes or edge.get("to_node_id") not in known_nodes:
            raise ValueError("evidence graph edge endpoint is missing")
        edge_ids.append(edge["edge_id"])
    if len(set(edge_ids)) != len(edge_ids):
        raise ValueError("evidence graph edge ids must be unique")
    return graph


def build(manifest_path, out_path, exceptions_path, allow_empty_graph=False):
    """Build the deterministic graph overlay from the hash-bound manifest.

    Refuses a build that would contain no nodes unless it is explicitly allowed:
    an empty graph answers "no results" to every question, which is
    indistinguishable from the facts being absent."""
    out_path, exceptions_path = Path(out_path), Path(exceptions_path)
    if out_path.exists() or exceptions_path.exists():
        raise ValueError("output already exists")
    run_id, entries = load_manifest(manifest_path)
    nodes, edges, exceptions = {}, {"edges": {}}, []
    for artifact_id, artifact_type, path, sha in sorted(entries):
        before = len(nodes)
        _adapt(
            json.loads(path.read_text(encoding="utf-8")),
            artifact_id,
            artifact_type,
            sha,
            nodes,
            edges,
            exceptions,
        )
        if len(nodes) == before:
            # A hash-verified artifact that contributes nothing is a silent upstream
            # failure. Recording it keeps "no contradictions found" distinguishable
            # from "nothing was ever loaded".
            exceptions.append(
                {
                    "reason": "graph_source_artifact_contributed_no_nodes",
                    "artifact_id": artifact_id,
                    "artifact_type": artifact_type,
                    "disposition": "client_review_required",
                }
            )
    if not nodes and not allow_empty_graph:
        raise ValueError(
            "evidence graph would contain no nodes; supply source artifacts with "
            "records or pass --allow-empty-graph to retain the empty overlay"
        )
    node_ids = set(nodes)
    valid_edges = []
    for edge in edges["edges"].values():
        missing = [
            endpoint
            for endpoint in (edge["from_node_id"], edge["to_node_id"])
            if endpoint not in node_ids
        ]
        if missing:
            exceptions.append(
                {
                    "reason": "graph_edge_endpoint_missing",
                    "edge_id": edge["edge_id"],
                    "missing_node_ids": sorted(set(missing)),
                    "disposition": "client_review_required",
                }
            )
            continue
        valid_edges.append(edge)
    valid_edge_list = valid_edges
    graph = {
        "artifact_type": ARTIFACT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "manifest_sha256": digest(manifest_path),
        "nodes": sorted(nodes.values(), key=lambda item: item["node_id"]),
        "edges": sorted(valid_edge_list, key=lambda item: item["edge_id"]),
        "exceptions": sorted(
            exceptions,
            key=lambda item: (item["reason"], item.get("artifact_id", ""), item.get("index", -1)),
        ),
    }
    graph["graph_sha256"] = _content_hash(graph)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    exceptions_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(graph, indent=2, sort_keys=True) + "\n")
    exceptions_path.write_text(
        json.dumps(
            {"artifact_type": "evidence_graph_exceptions_v1", "exceptions": graph["exceptions"]},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return graph


def _limit(value):
    if not isinstance(value, int) or not 1 <= value <= MAX_LIMIT:
        raise ValueError("limit must be between 1 and 1000")
    return value


def query(graph, node_type, properties, limit):
    """Return nodes matching every supplied type and property filter."""
    limit = _limit(limit)
    nodes = [
        node
        for node in graph["nodes"]
        if (node_type is None or node["node_type"] == node_type)
        and all(node["properties"].get(key) == value for key, value in properties.items())
    ]
    return {"graph_sha256": graph["graph_sha256"], "nodes": nodes[:limit]}


def path(graph, source, target, max_depth):
    """Return the shortest connecting path, treating edges as undirected.

    Exactly one path is returned. The key stays plural for contract stability and
    the accompanying ``paths_returned`` states the count outright, so a caller
    cannot read the list as exhaustive.
    """
    if not isinstance(max_depth, int) or not 1 <= max_depth <= 12:
        raise ValueError("depth must be between 1 and 12")
    node_ids = {node["node_id"] for node in graph["nodes"]}
    if source not in node_ids or target not in node_ids:
        return {"found": False, "paths": [], "paths_returned": 0, "edge_direction": "undirected"}
    if source == target:
        return {
            "found": True,
            "paths": [[source]],
            "paths_returned": 1,
            "edge_direction": "undirected",
        }
    adjacency = defaultdict(list)
    for edge in graph["edges"]:
        adjacency[edge["from_node_id"]].append(edge["to_node_id"])
        adjacency[edge["to_node_id"]].append(edge["from_node_id"])
    queue = deque([(source, [source])])
    seen = {source}
    while queue:
        current, trail = queue.popleft()
        if len(trail) - 1 >= max_depth:
            continue
        for neighbor in sorted(adjacency[current]):
            if neighbor == target:
                return {
                    "found": True,
                    "paths": [trail + [neighbor]],
                    "paths_returned": 1,
                    "edge_direction": "undirected",
                }
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, trail + [neighbor]))
    return {"found": False, "paths": [], "paths_returned": 0, "edge_direction": "undirected"}


def candidates(graph, source, relationship, limit):
    """Return outgoing relationship candidates, ranked by retained provenance.

    Edges are directed here, unlike ``path``, which treats the graph as
    undirected; both semantics are documented in artifact-contracts.md. The rank
    is a count of distinct retained provenance entries -- how many source
    artifacts asserted the relationship -- and is ordering only. It is not a
    confidence, and it never permits approval.
    """
    limit = _limit(limit)
    result = [
        {
            "target_node_id": edge["to_node_id"],
            "relationship": relationship,
            "provenance_count": len({json.dumps(p, sort_keys=True) for p in edge["provenance"]}),
            "approval_state": edge["approval_state"],
            "rank_basis": "distinct_retained_provenance_entries",
            "production_approval_permitted": False,
        }
        for edge in graph["edges"]
        if edge["from_node_id"] == source and edge["edge_type"] == relationship
    ]
    result.sort(key=lambda item: (-item["provenance_count"], item["target_node_id"]))
    return {
        "graph_sha256": graph["graph_sha256"],
        "edge_direction": "outgoing",
        "candidates": result[:limit],
    }


def contradictions(graph):
    """Report every retained node and edge conflict the graph preserved."""
    groups = defaultdict(list)
    for node in graph["nodes"]:
        if node["node_type"] == "field_claim":
            key = (node["properties"].get("subject_node_id"), node["properties"].get("field"))
            groups[key].append(node)
    result = [
        {
            "subject_node_id": key[0],
            "field": key[1],
            "claims": values,
            "status": "conflict",
            "production_approval_permitted": False,
        }
        for key, values in sorted(groups.items())
        if len({json.dumps(item["properties"].get("value"), sort_keys=True) for item in values}) > 1
    ]
    return {"graph_sha256": graph["graph_sha256"], "contradictions": result}


def schema_inventory(graph, sample_limit):
    """Summarize observed graph claims for proposal-only schema discovery."""
    sample_limit = _limit(sample_limit)
    fields = defaultdict(list)
    for node in graph["nodes"]:
        if node["node_type"] == "field_claim":
            field = node["properties"].get("field")
            if isinstance(field, str) and field:
                fields[field].append(node)
    inventory = []
    for field, claims in sorted(fields.items()):
        values = {json.dumps(item["properties"].get("value"), sort_keys=True) for item in claims}
        documents = {item["properties"].get("subject_node_id") for item in claims}
        examples = sorted(
            {json.dumps(item["properties"].get("value"), sort_keys=True) for item in claims}
        )[:sample_limit]
        inventory.append(
            {
                "observed_field": field,
                "claim_count": len(claims),
                "document_count": len(documents),
                "distinct_value_count": len(values),
                "value_examples": [json.loads(item) for item in examples],
                "claim_node_ids": sorted(item["node_id"] for item in claims)[:sample_limit],
                "proposal_only": True,
                "canonical_mapping_permitted": False,
            }
        )
    return {
        "artifact_type": "evidence_graph_schema_inventory_v1",
        "graph_sha256": graph["graph_sha256"],
        "proposal_only": True,
        "canonical_mapping_permitted": False,
        "fields": inventory,
    }


def _pointer_token(value):
    """Encode one JSON Pointer path token without changing the retained field name."""
    return str(value).replace("~", "~0").replace("/", "~1")


def _discovery_topics(field):
    """Classify a field name for discovery prioritization, never as a factual role."""
    normalized = re.sub(r"[^a-z0-9]+", "_", field.casefold()).strip("_")
    return sorted(
        topic
        for topic, terms in DISCOVERY_TOPIC_TERMS.items()
        if any(term in normalized for term in terms)
    )


def _string_leaves(value, pointer):
    """Yield retained string leaves with JSON Pointers for bounded signal inventorying."""
    if isinstance(value, str):
        yield pointer, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _string_leaves(item, f"{pointer}/{index}")
    elif isinstance(value, dict):
        for key, item in sorted(value.items()):
            yield from _string_leaves(item, f"{pointer}/{_pointer_token(key)}")


def _signal_types(value):
    """Return generic discovery labels for source-retained free text only."""
    return sorted(
        signal_type
        for signal_type, pattern in UNSTRUCTURED_SIGNAL_PATTERNS.items()
        if pattern.search(value)
    )


def _signal_summary(signals, sample_limit):
    """Aggregate retained signals for review without discarding their raw entries."""
    grouped = defaultdict(list)
    for signal in signals:
        grouped[signal["signal_type"]].append(signal)
    summary = []
    for signal_type, entries in sorted(grouped.items()):
        unique_evidence = {}
        for entry in entries:
            unique_evidence.setdefault(
                (
                    entry["document_id"],
                    entry["field"],
                    entry["json_pointer"],
                    entry["evidence_text"],
                ),
                entry,
            )
        examples = [
            {key: entry[key] for key in ("document_id", "field", "json_pointer", "evidence_text")}
            for _, entry in sorted(unique_evidence.items())[:sample_limit]
        ]
        summary.append(
            {
                "signal_type": signal_type,
                "occurrence_count": len(entries),
                "document_count": len({entry["document_id"] for entry in entries}),
                "field_count": len({entry["field"] for entry in entries}),
                "evidence_examples": examples,
                "proposal_only": True,
                "canonical_mapping_permitted": False,
            }
        )
    return summary


def _candidate_inventory(data):
    """Return retained candidate occurrences, signals, and explicit malformed-input findings."""
    documents = data.get("documents") if isinstance(data, dict) else None
    if not isinstance(documents, list):
        raise ValueError("retained consensus input must contain a documents list")
    candidates, source_exceptions, signals = defaultdict(list), [], []
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            source_exceptions.append(
                {"index": index, "reason": "candidate_inventory_source_record_malformed"}
            )
            continue
        document_id = document.get("document_id")
        if not isinstance(document_id, str) or not document_id.strip():
            source_exceptions.append(
                {"index": index, "reason": "candidate_inventory_document_id_missing"}
            )
            continue
        fields = document.get("fields")
        if not isinstance(fields, dict):
            source_exceptions.append(
                {
                    "document_id": document_id,
                    "index": index,
                    "reason": "candidate_inventory_fields_missing",
                }
            )
            continue
        for field, claim in sorted(fields.items()):
            if not isinstance(field, str) or not field.strip() or not isinstance(claim, dict):
                source_exceptions.append(
                    {
                        "document_id": document_id,
                        "index": index,
                        "field": field,
                        "reason": "candidate_inventory_field_claim_malformed",
                    }
                )
                continue
            candidate_values = claim.get("candidate_values")
            if candidate_values is None or claim.get("accepted") is True:
                continue
            if not isinstance(candidate_values, list):
                source_exceptions.append(
                    {
                        "document_id": document_id,
                        "index": index,
                        "field": field,
                        "reason": "candidate_inventory_values_malformed",
                    }
                )
                continue
            for candidate_index, value in enumerate(candidate_values):
                if value is None:
                    continue
                pointer = (
                    f"/documents/{index}/fields/{_pointer_token(field)}/candidate_values/"
                    f"{candidate_index}"
                )
                candidates[field].append(
                    {
                        "document_id": document_id,
                        "json_pointer": pointer,
                        "value": value,
                        "consensus_flag": claim.get("consensus_flag"),
                    }
                )
                for leaf_pointer, text in _string_leaves(value, pointer):
                    for signal_type in _signal_types(text):
                        signals.append(
                            {
                                "signal_type": signal_type,
                                "document_id": document_id,
                                "field": field,
                                "json_pointer": leaf_pointer,
                                "evidence_text": text,
                                "proposal_only": True,
                                "canonical_mapping_permitted": False,
                            }
                        )

    return candidates, source_exceptions, signals


def schema_surface_inventory(graph, consensus_path, sample_limit):
    """Inventory observed claims plus retained non-consensus discovery candidates.

    The resulting surface is proposal-only corpus context. It deliberately leaves
    accepted graph facts unchanged and never creates graph nodes from candidate
    values, because candidates have not passed consensus or client authorization.
    """
    sample_limit = _limit(sample_limit)
    consensus_path = Path(consensus_path)
    raw = consensus_path.read_bytes()
    candidates, source_exceptions, signals = _candidate_inventory(json.loads(raw))

    inventory = []
    for field, items in sorted(candidates.items()):
        unique_values = {
            json.dumps(item["value"], ensure_ascii=False, sort_keys=True) for item in items
        }
        inventory.append(
            {
                "candidate_field": field,
                "candidate_occurrence_count": len(items),
                "document_count": len({item["document_id"] for item in items}),
                "distinct_value_count": len(unique_values),
                "candidate_value_examples": [
                    json.loads(value) for value in sorted(unique_values)[:sample_limit]
                ],
                "document_ids": sorted({item["document_id"] for item in items})[:sample_limit],
                "source_pointers": sorted({item["json_pointer"] for item in items})[:sample_limit],
                "consensus_flags": sorted(
                    {
                        item["consensus_flag"]
                        for item in items
                        if isinstance(item["consensus_flag"], str)
                    }
                ),
                "discovery_topics": _discovery_topics(field),
                "evidence_state": "retained_candidate",
                "proposal_only": True,
                "canonical_mapping_permitted": False,
            }
        )
    return {
        "artifact_type": SCHEMA_SURFACE_ARTIFACT_TYPE,
        "graph_sha256": graph["graph_sha256"],
        "source_artifacts": {
            "consensus": {
                "name": consensus_path.name,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        },
        "proposal_only": True,
        "canonical_mapping_permitted": False,
        "observed_fields": schema_inventory(graph, sample_limit)["fields"],
        "retained_candidate_fields": inventory,
        "unstructured_signals": sorted(
            signals,
            key=lambda item: (
                item["signal_type"],
                item["document_id"],
                item["field"],
                item["json_pointer"],
            ),
        ),
        "unstructured_signal_summary": _signal_summary(signals, sample_limit),
        "source_exceptions": sorted(
            source_exceptions,
            key=lambda item: (
                item.get("reason", ""),
                item.get("document_id", ""),
                item.get("index", -1),
            ),
        ),
    }


def schema_recovery_scope(graph, consensus_path, topics, signal_types):
    """Create a minimal, provenance-bound scope for a later independent extraction retry."""
    topics = sorted(set(topics))
    signal_types = sorted(set(signal_types))
    if not topics and not signal_types:
        raise ValueError("at least one discovery topic or signal type is required")
    if unknown_topics := set(topics) - set(DISCOVERY_TOPIC_TERMS):
        raise ValueError("unsupported discovery topics: " + ", ".join(sorted(unknown_topics)))
    if unknown_signal_types := set(signal_types) - set(UNSTRUCTURED_SIGNAL_PATTERNS):
        raise ValueError("unsupported signal types: " + ", ".join(sorted(unknown_signal_types)))
    consensus_path = Path(consensus_path)
    raw = consensus_path.read_bytes()
    candidates, source_exceptions, signals = _candidate_inventory(json.loads(raw))
    selected_candidates = []
    selected_document_ids = set()
    for field, entries in sorted(candidates.items()):
        field_topics = _discovery_topics(field)
        if not set(topics).intersection(field_topics):
            continue
        for entry in entries:
            selected_document_ids.add(entry["document_id"])
            selected_candidates.append(
                {
                    "document_id": entry["document_id"],
                    "candidate_field": field,
                    "json_pointer": entry["json_pointer"],
                    "discovery_topics": field_topics,
                    "consensus_flag": entry["consensus_flag"],
                }
            )
    selected_signals = []
    for signal in signals:
        if signal["signal_type"] not in signal_types:
            continue
        selected_document_ids.add(signal["document_id"])
        selected_signals.append(signal)
    return {
        "artifact_type": "evidence_graph_schema_recovery_scope_v1",
        "graph_sha256": graph["graph_sha256"],
        "source_artifacts": {
            "consensus": {
                "name": consensus_path.name,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        },
        "selection": {
            "discovery_topics": topics,
            "signal_types": signal_types,
            "document_count": len(selected_document_ids),
            "document_ids": sorted(selected_document_ids),
            "candidate_occurrence_count": len(selected_candidates),
            "unstructured_signal_count": len(selected_signals),
        },
        "retained_candidate_occurrences": selected_candidates,
        "unstructured_signals": sorted(
            selected_signals,
            key=lambda item: (
                item["signal_type"],
                item["document_id"],
                item["field"],
                item["json_pointer"],
            ),
        ),
        "source_exceptions": source_exceptions,
        "proposal_only": True,
        "canonical_mapping_permitted": False,
    }


def build_manifest(run_id, typed_paths, output):
    """Write the typed artifact manifest ``build`` consumes.

    Nothing produced this shape, so an operator had to hand-build it and the
    graph lane was effectively unrunnable. It lives here rather than in a
    separate command because the loader that validates it is fifteen lines away:
    a manifest and its validator drifting apart is how a producer stops
    producing something usable.

    Types are supplied explicitly rather than guessed from filenames. A
    misclassified artifact would put the wrong claims in the graph under the
    right-looking provenance, and no later control could tell.
    """
    output = Path(output)
    if output.exists():
        raise ValueError(f"refusing to overwrite existing manifest: {output}")
    run_id = str(run_id).strip()
    if not run_id:
        raise ValueError("a manifest requires a non-empty run_id")
    if not typed_paths:
        raise ValueError(
            "a manifest requires at least one typed artifact; supported types are "
            + ", ".join(sorted(SUPPORTED))
        )
    root = output.resolve().parent
    artifacts = []
    for index, (artifact_type, path) in enumerate(typed_paths, start=1):
        if artifact_type not in SUPPORTED:
            raise ValueError(f"unsupported artifact type: {artifact_type}")
        candidate = Path(path).resolve()
        if not candidate.is_file():
            raise ValueError(f"artifact does not exist: {path}")
        if root not in candidate.parents:
            # build refuses a path outside the manifest's directory, so refusing
            # here names the problem while it is still fixable.
            raise ValueError(f"artifact must sit inside the manifest directory {root}: {path}")
        artifacts.append(
            {
                "artifact_id": f"artifact-{index:04d}",
                "artifact_type": artifact_type,
                "path": str(candidate.relative_to(root)),
                "sha256": digest(candidate),
            }
        )
    output.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "generated_at": datetime.now(UTC).isoformat(),
                "artifacts": artifacts,
            },
            indent=2,
        )
        + "\n"
    )
    return {"run_id": run_id, "artifacts": len(artifacts), "manifest": str(output)}


def main():
    parser = argparse.ArgumentParser(
        description="Build and query a deterministic JSON evidence graph."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    manifest_parser = sub.add_parser("manifest")
    manifest_parser.add_argument(
        "--run-id",
        required=True,
        help="Identity recorded in the manifest and carried into the graph.",
    )
    manifest_parser.add_argument(
        "--out",
        required=True,
        help="New manifest path. Every artifact must sit inside this file's directory.",
    )
    for artifact_type in sorted(SUPPORTED):
        manifest_parser.add_argument(
            f"--{artifact_type.replace('_', '-')}",
            action="append",
            default=[],
            dest=artifact_type,
            metavar="PATH",
            help=f"Retained {artifact_type.replace('_', ' ')} artifact. Repeatable.",
        )

    build_parser = sub.add_parser("build")
    build_parser.add_argument("manifest")
    build_parser.add_argument("--out", required=True)
    build_parser.add_argument("--exceptions", required=True)
    build_parser.add_argument(
        "--allow-empty-graph",
        action="store_true",
        help="retain a graph with no nodes instead of failing the build",
    )
    for name in (
        "query",
        "path",
        "candidates",
        "contradictions",
        "schema",
        "schema-surface",
        "schema-recovery-scope",
    ):
        command = sub.add_parser(name)
        command.add_argument(
            "graph",
            help="Retained evidence-graph artifact to read. The graph is never modified in place.",
        )
        command.add_argument("--out", required=True)
        if name == "query":
            command.add_argument("--node-type", help="Restrict results to this node type.")
            command.add_argument(
                "--property",
                action="append",
                default=[],
                help="Match a node property as NAME=VALUE. Repeatable; all supplied properties must match.",
            )
            command.add_argument("--limit", type=int, default=100)
        elif name == "path":
            command.add_argument(
                "--from", dest="source", required=True, help="Node the path search starts from."
            )
            command.add_argument(
                "--to", dest="target", required=True, help="Node the path search must reach."
            )
            command.add_argument(
                "--max-depth",
                type=int,
                default=6,
                help="Maximum edges traversed before the search stops.",
            )
        elif name == "candidates":
            command.add_argument(
                "--from",
                dest="source",
                required=True,
                help="Node whose candidate relationships are ranked.",
            )
            command.add_argument(
                "--relationship", required=True, help="Relationship type to rank candidates for."
            )
            command.add_argument("--limit", type=int, default=100)
        elif name in {"schema", "schema-surface", "schema-recovery-scope"}:
            if name == "schema-recovery-scope":
                command.add_argument("--consensus", required=True)
                command.add_argument(
                    "--topic",
                    action="append",
                    default=[],
                    help="Restrict the recovery scope to this topic. Repeatable.",
                )
                command.add_argument(
                    "--signal-type",
                    action="append",
                    default=[],
                    help="Restrict the recovery scope to this signal type. Repeatable.",
                )
                continue
            command.add_argument(
                "--sample-limit",
                type=int,
                default=5,
                help="Maximum observed sample values reported per claim.",
            )
            if name == "schema-surface":
                command.add_argument(
                    "--consensus",
                    required=True,
                    help="Retained consensus JSON whose non-accepted candidate values are inventoried.",
                )
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        if args.command == "manifest":
            typed = [
                (artifact_type, path)
                for artifact_type in sorted(SUPPORTED)
                for path in getattr(args, artifact_type, [])
            ]
            result = build_manifest(args.run_id, typed, args.out)
        elif args.command == "build":
            result = build(args.manifest, args.out, args.exceptions, args.allow_empty_graph)
        else:
            graph = load_graph(args.graph)
            if args.command == "query":
                properties = {
                    key: json.loads(value)
                    for key, value in (item.split("=", 1) for item in args.property)
                }
                result = query(graph, args.node_type, properties, args.limit)
            elif args.command == "path":
                result = path(graph, args.source, args.target, args.max_depth)
            elif args.command == "candidates":
                result = candidates(graph, args.source, args.relationship, args.limit)
            elif args.command == "schema":
                result = schema_inventory(graph, args.sample_limit)
            elif args.command == "schema-surface":
                result = schema_surface_inventory(graph, args.consensus, args.sample_limit)
            elif args.command == "schema-recovery-scope":
                result = schema_recovery_scope(graph, args.consensus, args.topic, args.signal_type)
            else:
                result = contradictions(graph)
            if Path(args.out).exists():
                raise ValueError("output already exists")
            Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(
            json.dumps(
                {
                    "status": "ok",
                    "artifact_type": result.get("artifact_type", "evidence_graph_result"),
                },
                sort_keys=True,
            )
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Evidence graph failed: {exc}") from exc


if __name__ == "__main__":
    main()
