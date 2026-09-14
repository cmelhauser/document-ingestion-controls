import hashlib
import importlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

graph = importlib.import_module("evidence_graph")


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def manifest(tmp_path, artifact_type="consensus_records"):
    records = {
        "documents": [
            {
                "document_id": "d1",
                "document_type": "invoice",
                "fields": {
                    "po_number": {"value": "PO-1", "accepted": True},
                    "total_amount": {"value": "10.00", "accepted": True},
                },
            }
        ]
    }
    source = write(tmp_path / "records.json", records)
    return write(
        tmp_path / "manifest.json",
        {
            "schema_version": "1.0",
            "run_id": "run-1",
            "artifacts": [
                {
                    "artifact_id": "records",
                    "artifact_type": artifact_type,
                    "path": source.name,
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            ],
        },
    )


def test_canonical_ids_and_build(tmp_path):
    assert graph.canonical_bytes({"b": 1, "a": 2}) == graph.canonical_bytes({"a": 2, "b": 1})
    assert graph.stable_id("document", "d1") == "document:d1"
    output = tmp_path / "graph.json"
    exceptions = tmp_path / "exceptions.json"
    result = graph.build(manifest(tmp_path), output, exceptions)
    assert result["artifact_type"] == "evidence_graph_v1"
    assert any(node["node_id"] == "document:d1" for node in result["nodes"])
    assert output.exists() and exceptions.exists()
    assert graph.load_graph(output)["graph_sha256"] == result["graph_sha256"]
    inventory = graph.schema_inventory(result, 1)
    assert inventory["artifact_type"] == "evidence_graph_schema_inventory_v1"
    assert inventory["fields"] == [
        {
            "observed_field": "po_number",
            "claim_count": 1,
            "document_count": 1,
            "distinct_value_count": 1,
            "value_examples": ["PO-1"],
            "claim_node_ids": [inventory["fields"][0]["claim_node_ids"][0]],
            "proposal_only": True,
            "canonical_mapping_permitted": False,
        },
        {
            "observed_field": "total_amount",
            "claim_count": 1,
            "document_count": 1,
            "distinct_value_count": 1,
            "value_examples": ["10.00"],
            "claim_node_ids": [inventory["fields"][1]["claim_node_ids"][0]],
            "proposal_only": True,
            "canonical_mapping_permitted": False,
        },
    ]
    assert (
        graph.schema_inventory(
            {**result, "nodes": result["nodes"] + [{"node_type": "field_claim", "properties": {}}]},
            1,
        )["fields"]
        == inventory["fields"]
    )
    with pytest.raises(ValueError, match="limit"):
        graph.schema_inventory(result, 0)
    tampered = json.loads(output.read_text())
    tampered["nodes"][0]["approval_state"] = "approved"
    output.write_text(json.dumps(tampered))
    with pytest.raises(ValueError, match="sha256"):
        graph.load_graph(output)
    valid = deepcopy(result)

    def checked_graph(name, mutate, message):
        candidate = deepcopy(valid)
        mutate(candidate)
        if isinstance(candidate, dict) and {
            "schema_version",
            "run_id",
            "manifest_sha256",
            "nodes",
            "edges",
            "exceptions",
            "graph_sha256",
        }.issubset(candidate):
            candidate["graph_sha256"] = graph._content_hash(candidate)
        path = write(tmp_path / name, candidate)
        with pytest.raises(ValueError, match=message):
            graph.load_graph(path)

    write(tmp_path / "graph-list.json", [])
    with pytest.raises(ValueError, match="must be an object"):
        graph.load_graph(tmp_path / "graph-list.json")
    checked_graph("graph-type.json", lambda value: value.update({"artifact_type": "wrong"}), "type")
    checked_graph(
        "graph-schema.json", lambda value: value.update({"schema_version": "wrong"}), "schema"
    )
    checked_graph("graph-missing.json", lambda value: value.pop("run_id"), "required")
    checked_graph(
        "graph-collections.json", lambda value: value.update({"nodes": {}}), "collections"
    )
    checked_graph(
        "graph-nodes.json",
        lambda value: value.update({"nodes": value["nodes"] + [value["nodes"][0]]}),
        "node ids",
    )
    checked_graph(
        "graph-node-fields.json", lambda value: value["nodes"][0].pop("node_type"), "nodes require"
    )
    checked_graph(
        "graph-node-strings.json",
        lambda value: value["nodes"][0].update({"node_type": 1}),
        "strings",
    )
    checked_graph(
        "graph-node-properties.json",
        lambda value: value["nodes"][0].update({"properties": []}),
        "properties",
    )
    checked_graph(
        "graph-node-protected.json",
        lambda value: value["nodes"][0].update({"protected": 1}),
        "protected",
    )
    checked_graph(
        "graph-node-provenance.json",
        lambda value: value["nodes"][0].update({"provenance": {}}),
        "provenance",
    )
    checked_graph(
        "graph-edge-id.json",
        lambda value: value.update({"edges": [{**value["edges"][0], "edge_id": ""}]}),
        "strings",
    )
    checked_graph(
        "graph-endpoint.json",
        lambda value: value.update({"edges": [{**value["edges"][0], "from_node_id": "missing"}]}),
        "endpoint",
    )
    checked_graph(
        "graph-edges.json",
        lambda value: value.update(
            {
                "edges": [
                    {**value["edges"][0], "edge_id": "e"},
                    {**value["edges"][0], "edge_id": "e"},
                ]
            }
        ),
        "unique",
    )
    checked_graph(
        "graph-edge-fields.json", lambda value: value["edges"][0].pop("edge_type"), "edges require"
    )
    checked_graph(
        "graph-edge-properties.json",
        lambda value: value["edges"][0].update({"properties": []}),
        "properties",
    )
    checked_graph(
        "graph-edge-protected.json",
        lambda value: value["edges"][0].update({"protected": 1}),
        "protected",
    )
    checked_graph(
        "graph-edge-provenance.json",
        lambda value: value["edges"][0].update({"provenance": {}}),
        "provenance",
    )
    with pytest.raises(ValueError, match="exists"):
        graph.build(manifest(tmp_path), output, tmp_path / "new-exc.json")
    with pytest.raises(ValueError, match="non-empty"):
        graph.stable_id("x", "")
    with pytest.raises(ValueError, match="schema"):
        graph.load_manifest(
            write(
                tmp_path / "bad-manifest.json",
                {"schema_version": "2", "run_id": "r", "artifacts": []},
            )
        )
    with pytest.raises(ValueError, match="non-empty list"):
        graph.load_manifest(
            write(
                tmp_path / "empty-manifest.json",
                {"schema_version": "1.0", "run_id": "r", "artifacts": []},
            )
        )
    source = tmp_path / "records.json"
    duplicate = write(
        tmp_path / "duplicate.json",
        {
            "schema_version": "1.0",
            "run_id": "r",
            "artifacts": [
                {
                    "artifact_id": "x",
                    "artifact_type": "consensus_records",
                    "path": source.name,
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                },
                {
                    "artifact_id": "x",
                    "artifact_type": "consensus_records",
                    "path": source.name,
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                },
            ],
        },
    )
    with pytest.raises(ValueError, match="unique"):
        graph.load_manifest(duplicate)
    escape = write(
        tmp_path / "escape.json",
        {
            "schema_version": "1.0",
            "run_id": "r",
            "artifacts": [
                {
                    "artifact_id": "x",
                    "artifact_type": "consensus_records",
                    "path": "../outside.json",
                    "sha256": "0" * 64,
                }
            ],
        },
    )
    with pytest.raises(ValueError, match="inside"):
        graph.load_manifest(escape)
    bad_hash = write(
        tmp_path / "bad-hash.json",
        {
            "schema_version": "1.0",
            "run_id": "r",
            "artifacts": [
                {
                    "artifact_id": "x",
                    "artifact_type": "consensus_records",
                    "path": source.name,
                    "sha256": "0" * 64,
                }
            ],
        },
    )
    with pytest.raises(ValueError, match="sha256"):
        graph.load_manifest(bad_hash)


def test_schema_surface_retains_nonconsensus_fields_and_unstructured_signals(tmp_path):
    result = graph.build(manifest(tmp_path), tmp_path / "graph.json", tmp_path / "exceptions.json")
    consensus = write(
        tmp_path / "consensus.json",
        {
            "documents": [
                {
                    "document_id": "d1",
                    "fields": {
                        "header.seller_address": {
                            "value": None,
                            "accepted": False,
                            "consensus_flag": "single_engine",
                            "candidate_values": ["1 Main St, Boston MA 02110"],
                        },
                        "header.sales_representative": {
                            "value": None,
                            "accepted": False,
                            "consensus_flag": "single_engine",
                            "candidate_values": ["Alex Example"],
                        },
                        "notes": {
                            "value": None,
                            "accepted": False,
                            "candidate_values": [
                                {"label": "Contact", "value": "alex@example.test"}
                            ],
                        },
                    },
                }
            ]
        },
    )
    surface = graph.schema_surface_inventory(result, consensus, 2)
    assert surface["artifact_type"] == "evidence_graph_schema_surface_inventory_v1"
    assert (
        surface["source_artifacts"]["consensus"]["sha256"]
        == hashlib.sha256(consensus.read_bytes()).hexdigest()
    )
    fields = {item["candidate_field"]: item for item in surface["retained_candidate_fields"]}
    assert fields["header.seller_address"]["discovery_topics"] == ["address", "party"]
    assert fields["header.sales_representative"]["discovery_topics"] == [
        "party",
        "sales_representative",
    ]
    assert fields["header.sales_representative"]["source_pointers"] == [
        "/documents/0/fields/header.sales_representative/candidate_values/0"
    ]
    assert any(item["signal_type"] == "email" for item in surface["unstructured_signals"])
    summary = {item["signal_type"]: item for item in surface["unstructured_signal_summary"]}
    assert summary["email"]["occurrence_count"] == 1
    assert summary["email"]["evidence_examples"][0]["document_id"] == "d1"
    assert graph._signal_types("Call +1 (617) 555-0100 ext. 12") == ["phone"]
    assert graph._signal_types("2024-05-31 and 20905.15") == []
    assert all(item["proposal_only"] for item in surface["retained_candidate_fields"])
    assert surface["canonical_mapping_permitted"] is False

    malformed = write(tmp_path / "bad-consensus.json", {"documents": [{"document_id": "d2"}]})
    malformed_surface = graph.schema_surface_inventory(result, malformed, 1)
    assert malformed_surface["source_exceptions"] == [
        {
            "document_id": "d2",
            "index": 0,
            "reason": "candidate_inventory_fields_missing",
        }
    ]

    many_signals = write(
        tmp_path / "many-signals.json",
        {
            "documents": [
                {
                    "document_id": "d3",
                    "fields": {
                        "notes": {
                            "value": None,
                            "accepted": False,
                            "candidate_values": [
                                f"contact-{index}@example.test"
                                for index in range(graph.MAX_LIMIT + 1)
                            ],
                        }
                    },
                }
            ]
        },
    )
    many_surface = graph.schema_surface_inventory(result, many_signals, 1)
    assert len(many_surface["unstructured_signals"]) == 2 * (graph.MAX_LIMIT + 1)
    assert {item["signal_type"]: item for item in many_surface["unstructured_signal_summary"]}[
        "email"
    ]["occurrence_count"] == graph.MAX_LIMIT + 1


def test_schema_recovery_scope_retains_only_requested_discovery_work(tmp_path):
    result = graph.build(manifest(tmp_path), tmp_path / "graph.json", tmp_path / "exceptions.json")
    consensus = write(
        tmp_path / "consensus.json",
        {
            "documents": [
                {
                    "document_id": "d1",
                    "fields": {
                        "header.seller_address": {
                            "accepted": False,
                            "candidate_values": ["1 Main St"],
                        },
                        "notes": {
                            "accepted": False,
                            "candidate_values": ["Salesperson: Alex Example"],
                        },
                    },
                },
                {
                    "document_id": "d2",
                    "fields": {
                        "notes": {
                            "accepted": False,
                            "candidate_values": ["owner@example.test"],
                        }
                    },
                },
            ]
        },
    )
    scope = graph.schema_recovery_scope(
        result, consensus, ["address"], ["contact_or_representative_label", "email"]
    )
    assert scope["artifact_type"] == "evidence_graph_schema_recovery_scope_v1"
    assert scope["selection"]["document_ids"] == ["d1", "d2"]
    assert scope["selection"]["candidate_occurrence_count"] == 1
    assert scope["selection"]["unstructured_signal_count"] == 2
    assert scope["retained_candidate_occurrences"][0]["candidate_field"] == "header.seller_address"
    assert scope["proposal_only"] is True
    with pytest.raises(ValueError, match="unsupported discovery topics"):
        graph.schema_recovery_scope(result, consensus, ["not-a-topic"], [])


def test_schema_surface_inventory_retains_malformed_findings_and_all_leaf_shapes(tmp_path):
    result = graph.build(manifest(tmp_path), tmp_path / "graph.json", tmp_path / "exceptions.json")
    malformed = {
        "documents": [
            None,
            {},
            {"document_id": "d1"},
            {
                "document_id": "d2",
                "fields": {
                    "": {},
                    "not_a_claim": "bad",
                    "not_a_list": {"candidate_values": "bad"},
                    "skipped": {"accepted": True, "candidate_values": ["ignored"]},
                    "none": {"candidate_values": [None]},
                    "nested": {"candidate_values": [["call 617-555-0100"], {"email": "a@b.test"}]},
                },
            },
        ]
    }
    with pytest.raises(ValueError, match="documents list"):
        graph._candidate_inventory({"documents": "not-a-list"})
    candidates, findings, signals = graph._candidate_inventory(malformed)
    assert len(candidates["nested"]) == 2
    assert {item["reason"] for item in findings} == {
        "candidate_inventory_source_record_malformed",
        "candidate_inventory_document_id_missing",
        "candidate_inventory_fields_missing",
        "candidate_inventory_field_claim_malformed",
        "candidate_inventory_values_malformed",
    }
    assert {item["signal_type"] for item in signals} == {"email", "phone"}
    assert list(graph._string_leaves(12, "/value")) == []
    scope = graph.schema_recovery_scope(
        result,
        write(tmp_path / "malformed.json", malformed),
        [],
        ["email"],
    )
    assert scope["selection"]["document_ids"] == ["d2"]
    assert scope["selection"]["unstructured_signal_count"] == 1
    with pytest.raises(ValueError, match="at least one"):
        graph.schema_recovery_scope(result, tmp_path / "malformed.json", [], [])
    with pytest.raises(ValueError, match="unsupported signal types"):
        graph.schema_recovery_scope(result, tmp_path / "malformed.json", [], ["not-a-signal"])


def test_schema_surface_and_recovery_scope_cli_commands(tmp_path, monkeypatch):
    graph.build(manifest(tmp_path), tmp_path / "graph.json", tmp_path / "exceptions.json")
    consensus = write(
        tmp_path / "consensus.json",
        {
            "documents": [
                {"document_id": "d1", "fields": {"notes": {"candidate_values": ["a@b.test"]}}}
            ]
        },
    )
    graph_path = tmp_path / "graph.json"
    surface_out = tmp_path / "surface.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "evidence_graph.py",
            "schema-surface",
            str(graph_path),
            "--consensus",
            str(consensus),
            "--out",
            str(surface_out),
        ],
    )
    graph.main()
    assert (
        json.loads(surface_out.read_text())["artifact_type"] == graph.SCHEMA_SURFACE_ARTIFACT_TYPE
    )
    scope_out = tmp_path / "scope.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "evidence_graph.py",
            "schema-recovery-scope",
            str(graph_path),
            "--consensus",
            str(consensus),
            "--signal-type",
            "email",
            "--out",
            str(scope_out),
        ],
    )
    graph.main()
    assert json.loads(scope_out.read_text())["selection"]["document_ids"] == ["d1"]


def test_queries_paths_candidates_contradictions(tmp_path):
    source = write(
        tmp_path / "records.json",
        {
            "documents": [
                {
                    "document_id": "d1",
                    "document_type": "invoice",
                    "fields": {
                        "ref": {"value": "A", "accepted": True},
                        "total": {"value": "1", "accepted": True},
                    },
                    "review_status": "open_exception",
                },
                {
                    "document_id": "d2",
                    "document_type": "invoice",
                    "fields": {"ref": {"value": "B", "accepted": True}},
                },
            ]
        },
    )
    m = write(
        tmp_path / "manifest.json",
        {
            "schema_version": "1.0",
            "run_id": "r",
            "artifacts": [
                {
                    "artifact_id": "x",
                    "artifact_type": "consensus_records",
                    "path": source.name,
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            ],
        },
    )
    g = graph.build(m, tmp_path / "g.json", tmp_path / "e.json")
    assert graph.query(g, "document", {"document_type": "invoice"}, 10)["nodes"]
    assert graph.path(g, "document:d1", "document:d1", 3)["found"]
    assert graph.candidates(g, "document:d1", "document_references", 10)["candidates"] == []
    assert graph.contradictions(g)["contradictions"] == []
    with pytest.raises(ValueError, match="limit"):
        graph.query(g, None, {}, 0)
    with pytest.raises(ValueError, match="depth"):
        graph.path(g, "document:d1", "document:d1", 0)
    assert graph.path(g, "missing", "document:d1", 3)["found"] is False
    assert graph.path(g, "document:d1", "document:d2", 1)["found"] is False
    assert graph.candidates(g, "missing", "x", 10)["candidates"] == []
    chained = dict(g)
    chained["nodes"] = g["nodes"] + [
        {
            "node_id": "extra",
            "node_type": "x",
            "properties": {},
            "approval_state": "observed",
            "protected": False,
            "provenance": [],
        },
        {
            "node_id": "third",
            "node_type": "x",
            "properties": {},
            "approval_state": "observed",
            "protected": False,
            "provenance": [],
        },
    ]
    chained["edges"] = g["edges"] + [
        {
            "edge_id": "e",
            "edge_type": "x",
            "from_node_id": "document:d1",
            "to_node_id": "extra",
            "properties": {},
            "approval_state": "proposed",
            "protected": True,
            "provenance": [],
        },
        {
            "edge_id": "e2",
            "edge_type": "x",
            "from_node_id": "extra",
            "to_node_id": "third",
            "properties": {},
            "approval_state": "proposed",
            "protected": True,
            "provenance": [],
        },
    ]
    assert graph.path(chained, "document:d1", "extra", 2)["found"]
    assert graph.path(chained, "extra", "document:d1", 2)["found"]
    assert graph.path(chained, "document:d1", "third", 3)["found"]


def test_adapters_merge_and_register_node_conflicts(tmp_path):
    nodes, exceptions = {}, []
    graph._add_node(
        nodes, graph._node("x", "document", {}, provenance=[{"a": 1}]), exceptions, "a1", 0
    )
    graph._add_node(
        nodes, graph._node("x", "document", {}, provenance=[{"b": 2}]), exceptions, "a1", 1
    )
    assert len(nodes["x"]["provenance"]) == 2
    assert exceptions == []

    # A conflicting reading is retained as review evidence, never an aborted build.
    graph._add_node(
        nodes,
        graph._node("x", "document", {"bad": True}, provenance=[{"c": 3}]),
        exceptions,
        "a2",
        4,
    )
    assert nodes["x"]["properties"] == {}
    assert nodes["x"]["protected"] is True
    assert len(nodes["x"]["provenance"]) == 3
    assert exceptions == [
        {
            "reason": "graph_node_property_conflict",
            "artifact_id": "a2",
            "index": 4,
            "node_id": "x",
            "retained_node_type": "document",
            "conflicting_node_type": "document",
            "retained_properties": {},
            "conflicting_properties": {"bad": True},
            "conflicting_provenance": [{"c": 3}],
            "disposition": "client_review_required",
        }
    ]

    # A differing node_type is the same class of conflict.
    graph._add_node(nodes, graph._node("x", "reference", {}, provenance=[]), exceptions, "a3", 0)
    assert exceptions[-1]["conflicting_node_type"] == "reference"


def test_duplicate_edges_merge_provenance_instead_of_colliding():
    edges = {}
    first = graph._edge("document_has_claim", "document:d", "claim:c", "observed", [{"a": 1}])
    second = graph._edge("document_has_claim", "document:d", "claim:c", "observed", [{"b": 2}])
    graph._add_edge(edges, first)
    graph._add_edge(edges, second)
    assert list(edges) == [first["edge_id"]]
    assert len(edges[first["edge_id"]]["provenance"]) == 2

    # The same relationship asserted at two approval states degrades to proposed.
    graph._add_edge(
        edges, graph._edge("document_has_claim", "document:d", "claim:c", "approved", [{"c": 3}])
    )
    merged = edges[first["edge_id"]]
    assert merged["approval_state"] == "proposed"
    assert merged["protected"] is True
    assert len(merged["provenance"]) == 3


def test_cross_packet_proposals_are_supported_graph_artifacts(tmp_path):
    source = write(
        tmp_path / "cross-packet.json",
        {
            "candidates": [
                {
                    "document_id": "d1",
                    "candidate_type": "cross_record_link",
                    "candidate_value": "ACK-9",
                    "evidence_quote": "ACK-9",
                    "buddy_status": "confirmed",
                }
            ],
            "verifications": [
                {
                    "existing_candidate": {
                        "document_id": "d1",
                        "candidate_type": "ack_reference",
                        "candidate_value": "ACK-9",
                        "evidence_quote": "ACK-9",
                    },
                    "verification_status": "conflict",
                    "primary_status": "conflict",
                    "buddy_status": "confirmed",
                }
            ],
        },
    )
    manifest_path = write(
        tmp_path / "cross-packet-manifest.json",
        {
            "schema_version": "1.0",
            "run_id": "cross-packet",
            "artifacts": [
                {
                    "artifact_id": "cross-packet",
                    "artifact_type": "client_review_cross_packet",
                    "path": source.name,
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            ],
        },
    )
    result = graph.build(manifest_path, tmp_path / "graph.json", tmp_path / "exceptions.json")
    assert any(node["node_type"] == "proposal" for node in result["nodes"])
    assert any(
        node["properties"].get("verification_status") == "conflict"
        for node in result["nodes"]
        if node["node_type"] == "proposal"
    )
    assert any(
        provenance["json_pointer"] == "/verifications/0"
        for node in result["nodes"]
        for provenance in node["provenance"]
    )
    with pytest.raises(ValueError, match="missing"):
        graph._records({})
    assert graph._records([]) == ([], "")
    with pytest.raises(ValueError, match="must be lists"):
        graph._artifact_records({"candidates": {}}, "client_review_cross_packet")
    malformed = write(
        tmp_path / "malformed.json",
        {"documents": ["bad", {"document_id": "ok", "fields": {"po": {"value": "P"}}}]},
    )
    m = write(
        tmp_path / "malformed-manifest.json",
        {
            "schema_version": "1.0",
            "run_id": "r",
            "artifacts": [
                {
                    "artifact_id": "m",
                    "artifact_type": "consensus_records",
                    "path": malformed.name,
                    "sha256": hashlib.sha256(malformed.read_bytes()).hexdigest(),
                }
            ],
        },
    )
    result = graph.build(m, tmp_path / "mg.json", tmp_path / "me.json")
    assert result["exceptions"]
    proposals = write(
        tmp_path / "proposals.json",
        {
            "candidates": [
                {
                    "document_id": "ok",
                    "candidate_type": "po_reference",
                    "candidate_value": "P",
                    "evidence_quote": "P",
                    "adjudication_status": "matched",
                },
                {},
            ]
        },
    )
    decisions = write(
        tmp_path / "decisions.json",
        {"decisions": [{"decision_id": "c1", "your_choice": "Defer", "your_note": "note"}, {}]},
    )
    manifest_value = {
        "schema_version": "1.0",
        "run_id": "r2",
        "artifacts": [
            {
                "artifact_id": "p",
                "artifact_type": "reference_adjudication",
                "path": proposals.name,
                "sha256": hashlib.sha256(proposals.read_bytes()).hexdigest(),
            },
            {
                "artifact_id": "d",
                "artifact_type": "client_decisions",
                "path": decisions.name,
                "sha256": hashlib.sha256(decisions.read_bytes()).hexdigest(),
            },
        ],
    }
    result = graph.build(
        write(tmp_path / "all-manifest.json", manifest_value),
        tmp_path / "all.json",
        tmp_path / "all-e.json",
    )
    assert any(node["node_type"] == "proposal" for node in result["nodes"])
    assert any(item["reason"] == "graph_edge_endpoint_missing" for item in result["exceptions"])
    missing_doc = write(
        tmp_path / "missing-doc.json", {"documents": [{"fields": {"bad": "shape"}}]}
    )
    missing_manifest = write(
        tmp_path / "missing-doc-manifest.json",
        {
            "schema_version": "1.0",
            "run_id": "r3",
            "artifacts": [
                {
                    "artifact_id": "m",
                    "artifact_type": "consensus_records",
                    "path": missing_doc.name,
                    "sha256": hashlib.sha256(missing_doc.read_bytes()).hexdigest(),
                }
            ],
        },
    )
    with pytest.raises(ValueError, match="no nodes"):
        graph.build(
            missing_manifest, tmp_path / "refused-graph.json", tmp_path / "refused-exc.json"
        )
    missing_result = graph.build(
        missing_manifest,
        tmp_path / "missing-graph.json",
        tmp_path / "missing-exc.json",
        allow_empty_graph=True,
    )
    assert any(
        item["reason"] == "graph_missing_document_id" for item in missing_result["exceptions"]
    )
    assert any(
        item["reason"] == "graph_source_artifact_contributed_no_nodes"
        for item in missing_result["exceptions"]
    )
    invalid_field = write(
        tmp_path / "invalid-field.json",
        {"documents": [{"document_id": "d", "fields": {"bad": "not-a-claim"}}]},
    )
    invalid_manifest = write(
        tmp_path / "invalid-field-manifest.json",
        {
            "schema_version": "1.0",
            "run_id": "r4",
            "artifacts": [
                {
                    "artifact_id": "m",
                    "artifact_type": "consensus_records",
                    "path": invalid_field.name,
                    "sha256": hashlib.sha256(invalid_field.read_bytes()).hexdigest(),
                }
            ],
        },
    )
    assert graph.build(
        invalid_manifest, tmp_path / "invalid-graph.json", tmp_path / "invalid-exc.json"
    )["nodes"]
    nodes, edges, exceptions = {}, {"edges": {}}, []
    graph._adapt(
        {
            "candidates": [
                {
                    "document_id": "d1",
                    "candidate_type": "ack_reference",
                    "candidate_value": "A1",
                    "evidence_quote": "A1",
                    "buddy_status": "confirmed",
                    "deterministic_evidence": True,
                    "evidence_validated": True,
                    "arithmetic_pass": True,
                    "identity_pass": True,
                },
                {
                    "document_id": "d1",
                    "candidate_type": "ack_reference",
                    "candidate_value": "A2",
                    "buddy_status": "confirmed",
                },
                {
                    "document_id": "d1",
                    "candidate_type": "ack_reference",
                    "candidate_value": "A3",
                    "buddy_status": "close_needs_review",
                },
            ]
        },
        "iter",
        "client_review_iterative_buddy",
        "a" * 64,
        nodes,
        edges,
        exceptions,
    )
    states = {
        node["properties"]["candidate_value"]: node["approval_state"] for node in nodes.values()
    }
    assert states == {
        "A1": "inferred_high_confidence",
        "A2": "proposed",
        "A3": "close_needs_review",
    }


def test_malformed_and_cli(monkeypatch, tmp_path, capsys):
    bad = write(
        tmp_path / "bad.json",
        {
            "schema_version": "1.0",
            "run_id": "r",
            "artifacts": [
                {"artifact_id": "x", "artifact_type": "unknown", "path": "x", "sha256": "0" * 64}
            ],
        },
    )
    with pytest.raises(ValueError, match="unsupported"):
        graph.load_manifest(bad)
    source = manifest(tmp_path)
    real_build = graph.build
    monkeypatch.setattr(graph, "build", lambda *args: {"ok": True})
    monkeypatch.setattr(graph, "Path", Path)
    monkeypatch.setattr(
        __import__("sys"),
        "argv",
        [
            "evidence_graph.py",
            "build",
            str(source),
            "--out",
            str(tmp_path / "g"),
            "--exceptions",
            str(tmp_path / "e"),
        ],
    )
    graph.main()
    assert "ok" in capsys.readouterr().out
    source = manifest(tmp_path)
    built = tmp_path / "cli-graph.json"
    real_build(source, built, tmp_path / "cli-exc.json")
    for command, extra in (
        ("query", ["--node-type", "document", "--property", 'document_type="invoice"']),
        ("path", ["--from", "document:d1", "--to", "document:d1"]),
        ("candidates", ["--from", "document:d1", "--relationship", "document_references"]),
        ("contradictions", []),
        ("schema", ["--sample-limit", "1"]),
    ):
        out = tmp_path / f"{command}.json"
        argv = ["evidence_graph.py", command, str(built), "--out", str(out), *extra]
        monkeypatch.setattr(__import__("sys"), "argv", argv)
        graph.main()
        assert out.exists()
    with pytest.raises(SystemExit, match="exists"):
        monkeypatch.setattr(
            __import__("sys"),
            "argv",
            ["evidence_graph.py", "query", str(built), "--out", str(tmp_path / "query.json")],
        )
        graph.main()
    monkeypatch.setattr(
        __import__("sys"),
        "argv",
        [
            "evidence_graph.py",
            "query",
            str(tmp_path / "does-not-exist.json"),
            "--out",
            str(tmp_path / "bad-out.json"),
        ],
    )
    with pytest.raises(SystemExit, match="Evidence graph failed"):
        graph.main()


def test_manifest_producer_writes_what_build_consumes(tmp_path, monkeypatch):
    """The graph lane had an input nothing produced, so it was unrunnable.

    The strongest assertion is the round trip: what ``manifest`` writes is fed
    straight to ``build`` with no hand-editing in between.
    """
    consensus = tmp_path / "consensus.json"
    consensus.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "document_id": "d1",
                        "fields": {"header.invoice_number": {"value": "INV-1", "accepted": True}},
                    }
                ]
            }
        )
    )
    manifest = tmp_path / "graph_manifest.json"
    result = graph.build_manifest("run-1", [("consensus_records", consensus)], manifest)
    assert result["artifacts"] == 1
    written = json.loads(manifest.read_text())
    assert written["schema_version"] == graph.SCHEMA_VERSION
    assert written["run_id"] == "run-1"
    entry = written["artifacts"][0]
    assert entry["artifact_type"] == "consensus_records"
    # Recorded relative to the manifest, which is what build resolves against.
    assert entry["path"] == "consensus.json"
    assert entry["sha256"] == graph.digest(consensus)

    # The round trip: build accepts it unedited.
    run_id, entries = graph.load_manifest(manifest)
    assert run_id == "run-1" and len(entries) == 1


def test_manifest_producer_refuses_what_build_would_refuse_later(tmp_path):
    """Each refusal names the problem while it is still cheap to fix."""
    consensus = tmp_path / "consensus.json"
    consensus.write_text(json.dumps({"documents": []}))
    manifest = tmp_path / "m.json"

    with pytest.raises(ValueError, match="non-empty run_id"):
        graph.build_manifest("  ", [("consensus_records", consensus)], manifest)
    with pytest.raises(ValueError, match="at least one typed artifact"):
        graph.build_manifest("run-1", [], manifest)
    with pytest.raises(ValueError, match="unsupported artifact type"):
        graph.build_manifest("run-1", [("not_a_type", consensus)], manifest)
    with pytest.raises(ValueError, match="artifact does not exist"):
        graph.build_manifest("run-1", [("consensus_records", tmp_path / "gone.json")], manifest)

    # build resolves paths against the manifest's directory and refuses anything
    # outside it, so the producer refuses first and says why.
    outside = tmp_path.parent / "outside.json"
    outside.write_text("{}")
    nested = tmp_path / "nested"
    nested.mkdir()
    with pytest.raises(ValueError, match="must sit inside the manifest directory"):
        graph.build_manifest("run-1", [("consensus_records", outside)], nested / "m.json")

    graph.build_manifest("run-1", [("consensus_records", consensus)], manifest)
    with pytest.raises(ValueError, match="refusing to overwrite"):
        graph.build_manifest("run-1", [("consensus_records", consensus)], manifest)


def test_manifest_cli_accepts_every_supported_type(tmp_path, monkeypatch, capsys):
    """Types are supplied explicitly; a misclassified artifact is unrecoverable."""
    first = tmp_path / "consensus.json"
    first.write_text(json.dumps({"documents": []}))
    second = tmp_path / "validation.json"
    second.write_text(json.dumps({"documents": []}))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evidence_graph.py",
            "manifest",
            "--run-id",
            "run-9",
            "--consensus-records",
            str(first),
            "--validated-records",
            str(second),
            "--out",
            str(tmp_path / "m.json"),
        ],
    )
    graph.main()
    capsys.readouterr()
    written = json.loads((tmp_path / "m.json").read_text())
    assert [a["artifact_type"] for a in written["artifacts"]] == [
        "consensus_records",
        "validated_records",
    ]
