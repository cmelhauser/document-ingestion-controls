import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
schema = importlib.import_module("schema_discovery")


def write(path, value):
    path.write_text(json.dumps(value))
    return path


def packet():
    return {
        "template_id": "sheet-v1",
        "evidence": [{"document_id": "d", "page_id": "p", "region_id": "r"}],
        "headers": [{"source_label": "ACK Number"}, {"source_label": "Brand"}],
        "entities": [
            {
                "entity_id": "dealer",
                "entity_type": "dealer",
                "name": "Sample Dealer",
                "city_hint": "Boston",
                "country_code": "US",
            },
            {"entity_id": "brand", "entity_type": "brand", "name": "Sample Brand"},
        ],
        "relationships": [],
    }


def graph_inventory():
    return {
        "artifact_type": "evidence_graph_schema_inventory_v1",
        "graph_sha256": "a" * 64,
        "proposal_only": True,
        "canonical_mapping_permitted": False,
        "fields": [
            {
                "observed_field": "po_number",
                "claim_count": 4,
                "document_count": 3,
                "distinct_value_count": 4,
                "value_examples": ["PO-1", "PO-2"],
                "claim_node_ids": ["field_claim:1"],
                "proposal_only": True,
                "canonical_mapping_permitted": False,
            }
        ],
    }


def graph_surface_inventory():
    return {
        "artifact_type": "evidence_graph_schema_surface_inventory_v1",
        "graph_sha256": "b" * 64,
        "source_artifacts": {"consensus": {"name": "consensus.json", "sha256": "c" * 64}},
        "proposal_only": True,
        "canonical_mapping_permitted": False,
        "observed_fields": graph_inventory()["fields"],
        "retained_candidate_fields": [
            {
                "candidate_field": "header.seller_address",
                "candidate_occurrence_count": 5,
                "document_count": 4,
                "distinct_value_count": 5,
                "candidate_value_examples": ["1 Main St"],
                "document_ids": ["d1"],
                "source_pointers": ["/documents/0/fields/header.seller_address/candidate_values/0"],
                "consensus_flags": ["single_engine"],
                "discovery_topics": ["address", "party"],
                "evidence_state": "retained_candidate",
                "proposal_only": True,
                "canonical_mapping_permitted": False,
            }
        ],
        "unstructured_signals": [],
        "unstructured_signal_summary": [
            {
                "signal_type": "contact_or_representative_label",
                "occurrence_count": 4,
                "document_count": 2,
                "field_count": 1,
                "evidence_examples": [
                    {
                        "document_id": "d1",
                        "field": "header.notes",
                        "json_pointer": "/documents/0/fields/header.notes/candidate_values/0",
                        "evidence_text": "Salesperson: Alex Example",
                    }
                ],
                "proposal_only": True,
                "canonical_mapping_permitted": False,
            }
        ],
        "source_exceptions": [],
    }


class Response:
    def __init__(self, value):
        self.output_text = json.dumps(value)

    def model_dump(self, mode="json"):
        return {"id": "r"}


class Client:
    def __init__(self, value):
        self.value = value
        self.responses = self
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.value, Exception):
            raise self.value
        return Response(self.value)


class BuddyClient(Client):
    pass


def output(confidence=0.995):
    return {
        "mapping_proposals": [
            {
                "source_label": "ACK Number",
                "canonical_field": "business_reference_id",
                "semantic_type": "customer_ack",
                "confidence": confidence,
                "rationale": "label",
                "alternatives": [],
            },
            {
                "source_label": "Brand",
                "canonical_field": "brand_name",
                "semantic_type": "brand",
                "confidence": 0.995,
                "rationale": "label",
                "alternatives": [],
            },
        ],
        "entity_proposals": [
            {
                "entity_id": "dealer",
                "entity_type": "dealer",
                "canonical_name": "Sample Dealer",
                "confidence": 1.0,
                "rationale": "source",
            },
            {
                "entity_id": "brand",
                "entity_type": "brand",
                "canonical_name": "Sample Brand",
                "confidence": 1.0,
                "rationale": "source",
            },
        ],
        "relationship_proposals": [
            {
                "from_entity_id": "dealer",
                "to_entity_id": "brand",
                "relationship_type": "dealer_represents_brand",
                "confidence": 1.0,
                "rationale": "source",
            }
        ],
        "schema_changes": [],
    }


def buddy_output(status="confirmed"):
    return {
        "decisions": [
            {"source_label": "ACK Number", "status": status, "rationale": "source label"},
            {"source_label": "Brand", "status": status, "rationale": "source label"},
        ]
    }


def test_inputs_registry_and_failsafe(tmp_path):
    value = packet()
    assert schema.load_templates(write(tmp_path / "input.json", {"templates": [value]})) == [value]
    assert schema.normalize(" ACK  Number ") == "ack number"
    assert len(schema.template_fingerprint(value)) == 64
    rule = {
        "status": "client_approved",
        "template_fingerprint": schema.template_fingerprint(value),
        "source_label": "ACK Number",
    }
    assert schema.rule_for({"rules": [rule]}, rule["template_fingerprint"], "ack number") is rule
    assert schema.rule_for({"rules": []}, "x", "x") is None
    assert schema.review(value, "x", "y")["document_id"] == "d"
    assert schema.load_registry(None)["rules"] == []
    with pytest.raises(ValueError):
        schema.load_templates(write(tmp_path / "bad.json", {}))
    with pytest.raises(ValueError):
        schema.load_registry(write(tmp_path / "bad-registry.json", {}))
    disabled = schema.run_discovery(
        [value],
        {"rules": []},
        tmp_path / "out",
        tmp_path / "exc",
        tmp_path / "hand",
        tmp_path / "raw",
    )
    assert disabled["mapping_proposals"] == [] and disabled["summary"]["client_review_items"] == 2


def test_google_and_proposals(monkeypatch, tmp_path):
    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"places":[{"id":"p"}]}'

    calls = []
    assert schema.google_places(
        "Dealer", "Boston", "US", "k", 3, lambda request, timeout: calls.append(request) or Stream()
    ) == [{"id": "p"}]
    assert b"Boston" in calls[0].data
    monkeypatch.setattr(schema, "google_places", lambda *args: [{"id": "p"}])
    found, findings, sent = schema.locations(packet(), True, "k", 1, 3)
    assert len(found) == 1 and sent == 1 and findings[0]["reason"].endswith("limit_reached")
    assert schema.locations(packet(), False, "k", 1, 3) == ([], [], 0)
    value = packet()
    registry = {
        "rules": [
            {
                "status": "client_approved",
                "template_fingerprint": schema.template_fingerprint(value),
                "source_label": "Brand",
                "canonical_field": "brand_name",
                "semantic_type": "brand",
            }
        ]
    }
    client = Client(output())
    result = schema.run_discovery(
        [value],
        registry,
        tmp_path / "out",
        tmp_path / "exc",
        tmp_path / "hand",
        tmp_path / "raw",
        True,
        client,
        places_enabled=True,
        google_key="key",
        google_limit=3,
    )
    request = json.loads(client.calls[-1]["input"][0]["content"][0]["text"])
    assert request["source_evidence"] == {}
    assert request["iterative_mapping_context"]["iteration"] == 1
    assert result["summary"]["iterative_mapping"]["templates_considered"] == 1
    assert {item["status"] for item in result["mapping_proposals"]} == {
        "reused_approved_mapping",
        "proposed",
    }
    assert (
        result["dealer_proposals"] and result["brand_proposals"] and result["location_candidates"]
    )
    proposal_id = next(
        item["proposal_id"]
        for item in result["mapping_proposals"]
        if item["decision_source"] == "llm"
    )
    updated = schema.update_registry(
        registry, result, {"decisions": [{"proposal_id": proposal_id, "decision": "approve"}]}
    )
    assert updated["registry_version"] == 1 and len(updated["update_log"]) == 1


def test_graph_inventory_enriches_packets_without_becoming_mapping_evidence(tmp_path):
    value = packet()
    inventory_path = write(tmp_path / "inventory.json", graph_inventory())
    inventory = schema.load_graph_schema_inventory(inventory_path)
    client = Client(output())
    result = schema.run_discovery(
        [value],
        {"rules": []},
        tmp_path / "out",
        tmp_path / "exc",
        tmp_path / "hand",
        tmp_path / "raw",
        enabled=True,
        client=client,
        graph_schema_inventory=inventory,
    )
    request = json.loads(client.calls[-1]["input"][0]["content"][0]["text"])
    assert request["graph_schema_inventory"]["fields"] == inventory["fields"]
    assert result["summary"]["graph_schema_inventory"]["enabled"] is True
    assert result["summary"]["graph_schema_inventory"]["graph_sha256"] == "a" * 64
    assert (
        json.loads((tmp_path / "hand").read_text())["graph_schema_inventory"]["artifact_sha256"]
        == inventory["artifact_sha256"]
    )
    assert all(item["client_review_required"] for item in result["mapping_proposals"])


def test_graph_schema_surface_enriches_packets_without_promoting_candidates(tmp_path):
    inventory_path = write(tmp_path / "surface.json", graph_surface_inventory())
    inventory = schema.load_graph_schema_inventory(inventory_path)
    assert inventory["inventory_type"] == "schema_surface"
    assert inventory["retained_candidate_fields"][0]["evidence_state"] == "retained_candidate"
    client = Client(output())
    schema.run_discovery(
        [packet()],
        {"rules": []},
        tmp_path / "out",
        tmp_path / "exc",
        tmp_path / "hand",
        tmp_path / "raw",
        enabled=True,
        client=client,
        graph_schema_inventory=inventory,
    )
    request = json.loads(client.calls[-1]["input"][0]["content"][0]["text"])
    assert (
        request["graph_schema_inventory"]["retained_candidate_fields"]
        == inventory["retained_candidate_fields"]
    )
    assert (
        request["graph_schema_inventory"]["unstructured_signal_summary"]
        == inventory["unstructured_signal_summary"]
    )
    result = schema.run_discovery(
        [packet()],
        {"rules": []},
        tmp_path / "out-2",
        tmp_path / "exc-2",
        tmp_path / "hand-2",
        tmp_path / "raw-2",
        enabled=False,
        graph_schema_inventory=inventory,
    )
    assert result["summary"]["graph_schema_inventory"]["candidate_field_count"] == 1
    assert result["summary"]["graph_schema_inventory"]["inventory_type"] == "schema_surface"
    large_surface = graph_surface_inventory()
    large_surface["padding"] = "x" * schema.GRAPH_INVENTORY_MAX_BYTES
    large_path = write(tmp_path / "large-surface.json", large_surface)
    bounded = schema.load_graph_schema_inventory(large_path)
    assert bounded["candidate_fields_omitted_from_context"] == 0
    assert len(json.dumps(bounded, ensure_ascii=False).encode()) <= schema.GRAPH_INVENTORY_MAX_BYTES


def test_schema_discovery_vocabulary_covers_contacts_representatives_and_business_references():
    assert {
        "contact_name",
        "sales_representative_name",
        "acknowledgement_number",
        "payment_reference",
        "tracking_number",
        "charge_amount",
        "work_order_number",
        "port_of_entry",
    }.issubset(schema.FIELDS)


def test_graph_schema_surface_rejects_invalid_boundaries_and_clips_nested_values(tmp_path):
    def expect(value, message):
        path = write(tmp_path / f"{len(list(tmp_path.iterdir()))}.json", value)
        with pytest.raises(ValueError, match=message):
            schema.load_graph_schema_inventory(path)

    too_large = tmp_path / "too-large.json"
    too_large.write_bytes(b"x" * (schema.GRAPH_SURFACE_MAX_ARTIFACT_BYTES + 1))
    with pytest.raises(ValueError, match="10000000"):
        schema.load_graph_schema_inventory(too_large)

    invalid_observed = graph_surface_inventory()
    invalid_observed.pop("observed_fields")
    expect(invalid_observed, "observed fields")
    invalid_source = graph_surface_inventory()
    invalid_source["source_artifacts"] = {}
    expect(invalid_source, "candidate provenance")
    invalid_candidate = graph_surface_inventory()
    invalid_candidate["retained_candidate_fields"][0]["candidate_field"] = ""
    expect(invalid_candidate, "invalid candidate field")
    invalid_candidate_list = graph_surface_inventory()
    invalid_candidate_list["retained_candidate_fields"][0]["document_ids"] = [1]
    expect(invalid_candidate_list, "invalid candidate field")
    invalid_summary_type = graph_surface_inventory()
    invalid_summary_type["unstructured_signal_summary"] = {}
    expect(invalid_summary_type, "invalid signal summary")
    invalid_summary = graph_surface_inventory()
    invalid_summary["unstructured_signal_summary"] = [{"signal_type": "email"}]
    expect(invalid_summary, "invalid signal summary")
    invalid_example = graph_surface_inventory()
    invalid_example["unstructured_signal_summary"][0]["evidence_examples"] = [
        {"document_id": 1, "field": "f", "json_pointer": "/x", "evidence_text": "x"}
    ]
    expect(invalid_example, "invalid signal summary")
    oversized_context = graph_surface_inventory()
    oversized_context["observed_fields"][0]["value_examples"] = ["x" * 70_000]
    expect(oversized_context, "Reduced graph schema inventory")
    assert schema._clip_context_value({"x": ["x" * 600, 1]}) == {"x": ["x" * 512, 1]}
    assert {"contact", "sales_representative", "payment", "shipping", "tax"}.issubset(schema.TYPES)


def test_independent_schema_buddy_marks_only_source_evidenced_confirmations(tmp_path):
    value = packet()
    primary, buddy = Client(output()), BuddyClient(buddy_output())
    result = schema.run_discovery(
        [value],
        {"rules": []},
        tmp_path / "out",
        tmp_path / "exc",
        tmp_path / "hand",
        tmp_path / "raw",
        enabled=True,
        client=primary,
        primary_provider="google",
        buddy_enabled=True,
        buddy_client=buddy,
    )
    mappings = result["mapping_proposals"]
    assert {item["status"] for item in mappings} == {"inferred_high_confidence"}
    assert all(item["client_review_required"] for item in mappings)
    request = json.loads(buddy.calls[-1]["input"][0]["content"][0]["text"])
    assert request["source_template_packet"]["template_id"] == "sheet-v1"
    assert request["canonical_mapping_permitted"] is False
    handoff = json.loads((tmp_path / "hand").read_text())
    assert handoff["model_configuration"]["buddy"]["provider"] == "openai"
    conflict = schema.discover_template(
        value,
        {"rules": []},
        Client(output()),
        True,
        "m",
        "medium",
        0.99,
        tmp_path / "conflict",
        buddy_enabled=True,
        buddy_client=BuddyClient(buddy_output("conflict")),
    )
    assert all(item["inference_state"] == "conflict" for item in conflict[0])


def test_schema_buddy_fails_closed_on_bad_client_or_contract(tmp_path):
    value = packet()
    with pytest.raises(ValueError, match="independent"):
        schema.run_discovery(
            [value],
            {"rules": []},
            tmp_path / "out",
            tmp_path / "exc",
            tmp_path / "hand",
            tmp_path / "raw",
            enabled=True,
            client=Client(output()),
            buddy_enabled=True,
            buddy_client=BuddyClient(buddy_output()),
        )
    broken = schema.discover_template(
        value,
        {"rules": []},
        Client(output()),
        True,
        "m",
        "medium",
        0.99,
        tmp_path / "broken-buddy",
        buddy_enabled=True,
        buddy_client=BuddyClient({}),
    )
    assert any(item["reason"] == "schema_discovery_buddy_failure" for item in broken[4])
    with pytest.raises(ValueError, match="decisions"):
        schema.buddy_lookup({}, ["ACK Number"])
    assert (
        schema.buddy_lookup(
            {
                "decisions": [
                    {"source_label": "other", "status": "confirmed", "rationale": "x"},
                    "bad",
                ]
            },
            ["ACK Number"],
        )
        == {}
    )


def test_schema_discovery_supports_openrouter_buddy_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_REASONING_MODEL", "router-reasoning")
    result = schema.run_discovery(
        [packet()],
        {"rules": []},
        tmp_path / "router-out.json",
        tmp_path / "router-exc.json",
        tmp_path / "router-hand.json",
        tmp_path / "router-raw",
        enabled=True,
        client=Client(output()),
        model="google-primary",
        primary_provider="google",
        buddy_enabled=True,
        buddy_client=BuddyClient(buddy_output()),
        buddy_provider="openrouter",
    )
    assert result["mapping_proposals"][0]["status"] == "inferred_high_confidence"
    handoff = json.loads((tmp_path / "router-hand.json").read_text())
    assert handoff["model_configuration"]["primary"] == {
        "provider": "google",
        "model": "google-primary",
        "reasoning_effort": "medium",
    }
    assert handoff["model_configuration"]["buddy"] == {
        "enabled": True,
        "provider": "openrouter",
        "model": "router-reasoning",
    }
    no_evidence = packet()
    no_evidence["evidence"] = []
    no_evidence_result = schema.discover_template(
        no_evidence,
        {"rules": []},
        Client(output()),
        True,
        "m",
        "medium",
        0.99,
        tmp_path / "no-evidence",
        buddy_enabled=True,
        buddy_client=BuddyClient(buddy_output()),
    )
    assert all(item["status"] == "proposed" for item in no_evidence_result[0])


def test_cli_runs_google_primary_and_openai_buddy(monkeypatch, tmp_path, capsys):
    value = packet()
    primary, buddy = Client(output()), BuddyClient(buddy_output())
    monkeypatch.setattr(schema, "load_project_env", lambda: {})
    monkeypatch.setattr(
        schema,
        "build_client",
        lambda _credential, _timeout, _retries, provider=None: (
            primary if provider == "google" else buddy
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            schema.__file__,
            "discover",
            str(write(tmp_path / "input.json", {"templates": [value]})),
            "--enable",
            "--provider",
            "google",
            "--model",
            "gemini-test",
            "--buddy",
            "--buddy-model",
            "luna-test",
            "--out",
            str(tmp_path / "out"),
            "--exceptions",
            str(tmp_path / "exc"),
            "--handoff-out",
            str(tmp_path / "hand"),
            "--raw-dir",
            str(tmp_path / "raw"),
        ],
    )
    schema.main()
    assert "client_review_items" in capsys.readouterr().out
    assert primary.calls and buddy.calls
    assert (tmp_path / "raw" / f"{schema.template_fingerprint(value)[:16]}-buddy.json").exists()


def test_graph_inventory_rejects_non_proposal_or_malformed_input(tmp_path):
    invalid = graph_inventory()
    invalid["canonical_mapping_permitted"] = True
    with pytest.raises(ValueError, match="proposal-only"):
        schema.load_graph_schema_inventory(write(tmp_path / "invalid.json", invalid))
    invalid = graph_inventory()
    invalid["fields"][0]["claim_count"] = -1
    with pytest.raises(ValueError, match="field"):
        schema.load_graph_schema_inventory(write(tmp_path / "negative.json", invalid))
    invalid = graph_inventory()
    invalid["fields"][0]["claim_node_ids"] = [1]
    with pytest.raises(ValueError, match="field"):
        schema.load_graph_schema_inventory(write(tmp_path / "claim-id.json", invalid))
    invalid = graph_inventory()
    invalid["padding"] = "x" * schema.GRAPH_INVENTORY_MAX_BYTES
    with pytest.raises(ValueError, match="context limit"):
        schema.load_graph_schema_inventory(write(tmp_path / "oversized.json", invalid))


def test_errors_and_low_confidence(tmp_path):
    value = packet()
    low = schema.discover_template(
        value, {"rules": []}, Client(output(0.1)), True, "m", "medium", 0.99, tmp_path / "low"
    )
    assert low[0][0]["status"] == "low_confidence"
    broken = schema.discover_template(
        value,
        {"rules": []},
        Client(RuntimeError("down")),
        True,
        "m",
        "medium",
        0.99,
        tmp_path / "broken",
    )
    assert all(item["reason"].endswith("failure") for item in broken[4])
    with pytest.raises(ValueError):
        schema.run_discovery(
            [value],
            {"rules": []},
            tmp_path / "a",
            tmp_path / "b",
            tmp_path / "c",
            tmp_path / "d",
            True,
        )
    with pytest.raises(ValueError):
        schema.run_discovery(
            [value],
            {"rules": []},
            tmp_path / "e",
            tmp_path / "f",
            tmp_path / "g",
            tmp_path / "h",
            places_enabled=True,
        )
    with pytest.raises(ValueError):
        schema.update_registry({"rules": []}, {}, {})


def test_remaining_branches(monkeypatch, tmp_path, capsys):
    value = packet()

    class BadStream:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b"{}"

    with pytest.raises(RuntimeError):
        schema.google_places("x", None, None, "k", 1, lambda *args, **kwargs: BadStream())
    with pytest.raises(RuntimeError):
        schema.google_places(
            "x", None, None, "k", 1, lambda *args, **kwargs: (_ for _ in ()).throw(OSError("x"))
        )
    monkeypatch.setattr(schema, "google_places", lambda *args: [])
    assert schema.locations(value, True, "k", 3, 2)[1][0]["reason"].endswith("no_candidate")
    invalid = packet()
    invalid["entities"] = ["skip", {"entity_type": "dealer", "name": ""}]
    assert schema.locations(invalid, True, "k", 3, 2)[1][0]["reason"].endswith("missing_name")
    monkeypatch.setattr(
        schema, "google_places", lambda *args: (_ for _ in ()).throw(RuntimeError("failed"))
    )
    assert schema.locations(packet(), True, "k", 3, 2)[1][0]["reason"] == "failed"
    first = tmp_path / "exists"
    first.write_text("x")
    with pytest.raises(ValueError):
        schema.run_discovery(
            [value], {"rules": []}, first, tmp_path / "x", tmp_path / "y", tmp_path / "z"
        )
    raw = tmp_path / "not-empty"
    raw.mkdir()
    (raw / "x").write_text("x")
    with pytest.raises(ValueError):
        schema.run_discovery(
            [value], {"rules": []}, tmp_path / "x1", tmp_path / "y1", tmp_path / "z1", raw
        )
    with pytest.raises(ValueError):
        schema.run_discovery(
            [value],
            {"rules": []},
            tmp_path / "x2",
            tmp_path / "y2",
            tmp_path / "z2",
            tmp_path / "q2",
            effort="bad",
        )
    monkeypatch.setattr(schema, "load_project_env", lambda: {})
    monkeypatch.setenv("GOOGLE_PLACES_ENABLED", "false")
    input_path = write(tmp_path / "input.json", {"templates": [value]})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            schema.__file__,
            "discover",
            str(input_path),
            "--out",
            str(tmp_path / "cli-out"),
            "--exceptions",
            str(tmp_path / "cli-exc"),
            "--handoff-out",
            str(tmp_path / "cli-hand"),
            "--raw-dir",
            str(tmp_path / "cli-raw"),
        ],
    )
    schema.main()
    assert "client_review_items" in capsys.readouterr().out
    discovery = {"mapping_proposals": []}
    registry = write(tmp_path / "registry.json", {"rules": []})
    discovery_path = write(tmp_path / "discovery.json", discovery)
    decisions = write(tmp_path / "decisions.json", {"decisions": []})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            schema.__file__,
            "registry-update",
            str(registry),
            str(discovery_path),
            str(decisions),
            "--out",
            str(tmp_path / "registry-out"),
        ],
    )
    schema.main()
    assert json.loads((tmp_path / "registry-out").read_text())["registry_version"] == 1


def test_coverage_boundaries(monkeypatch, tmp_path):
    value = packet()
    full_registry = {
        "rules": [
            {
                "status": "client_approved",
                "template_fingerprint": schema.template_fingerprint(value),
                "source_label": header["source_label"],
                "canonical_field": "brand_name",
                "semantic_type": "brand",
            }
            for header in value["headers"]
        ]
    }
    assert schema.discover_template(
        value, full_registry, None, False, "m", "medium", 0.99, tmp_path / "raw"
    )[0]
    assert (
        schema.update_registry(
            {"rules": []}, {"mapping_proposals": []}, {"decisions": ["skip", {"decision": "defer"}]}
        )["update_log"]
        == []
    )
    monkeypatch.setattr(schema, "load_project_env", lambda: {})
    registry = write(tmp_path / "registry.json", {"rules": []})
    discovery = write(tmp_path / "discovery.json", {"mapping_proposals": []})
    decisions = write(tmp_path / "decisions.json", {"decisions": []})
    existing = tmp_path / "existing.json"
    existing.write_text("x")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            schema.__file__,
            "registry-update",
            str(registry),
            str(discovery),
            str(decisions),
            "--out",
            str(existing),
        ],
    )
    with pytest.raises(SystemExit, match="Schema discovery failed"):
        schema.main()


def test_missing_model_mapping_is_not_dropped(tmp_path):
    value = packet()
    incomplete = output()
    incomplete["mapping_proposals"] = incomplete["mapping_proposals"][:1]
    result = schema.discover_template(
        value, {"rules": []}, Client(incomplete), True, "m", "medium", 0.99, tmp_path / "raw"
    )
    assert any(item["reason"].endswith("not_proposed") for item in result[4])


# --- Slot equivalence -------------------------------------------------------


def consensus_document(document_id="d1"):
    """Two engines: one files a value under both names, the other picks one."""
    return {
        "document_id": document_id,
        "fields": {
            "lines[0].job_number": {
                "value": "12497",
                "candidate_values": None,
                "agreeing_engines": ["engine-a", "engine-b"],
                "consensus_flag": "consensus_2of2",
            },
            "lines[0].item_code": {
                "value": None,
                "candidate_values": ["12497"],
                "agreeing_engines": ["engine-a"],
                "consensus_flag": "single_engine",
            },
            "lines[1].job_number": {
                "value": "12643",
                "candidate_values": None,
                "agreeing_engines": ["engine-a", "engine-b"],
                "consensus_flag": "consensus_2of2",
            },
            "lines[1].item_code": {
                "value": None,
                "candidate_values": ["12643"],
                "agreeing_engines": ["engine-a"],
                "consensus_flag": "single_engine",
            },
        },
    }


def slot_output(relationship="equivalent", confidence=0.995, preferred="job_number"):
    return {
        "slot_proposals": [
            {
                "field_a": "item_code",
                "field_b": "job_number",
                "relationship": relationship,
                "preferred_field": preferred,
                "confidence": confidence,
                "rationale": "one printed column",
            }
        ]
    }


def slot_buddy_output(status="confirmed", pair="item_code|job_number"):
    return {"decisions": [{"slot_pair": pair, "status": status, "rationale": "observations"}]}


def run_slots(tmp_path, name, **kwargs):
    base = tmp_path / name
    base.mkdir()
    defaults = {
        "documents": [consensus_document()],
        "exception_engines": {},
        "registry": {"registry_version": 0, "rules": []},
        "out": str(base / "out.json"),
        "exceptions": str(base / "exceptions.json"),
        "handoff": str(base / "handoff.json"),
        "raw_dir": str(base / "raw"),
    }
    defaults.update(kwargs)
    return schema.run_slot_equivalence(**defaults)


def test_slot_detector_separates_evidence_classes_and_ignores_coincidence():
    document = consensus_document()
    observations = schema.slot_observations(document, {})
    assert {item["evidence_class"] for item in observations} == {"intra_engine_duplicate"}
    assert all(item["container"].startswith("lines[") for item in observations)

    # Two engines filing one value under two names is the stronger class.
    cross = {
        "document_id": "d2",
        "fields": {
            "lines[0].customer_name": {
                "value": None,
                "candidate_values": ["argus glass"],
                "agreeing_engines": ["engine-a"],
            },
            "lines[0].description": {
                "value": None,
                "candidate_values": ["argus glass"],
                "agreeing_engines": ["engine-b"],
            },
        },
    }
    assert [item["evidence_class"] for item in schema.slot_observations(cross, {})] == [
        "cross_engine_placement"
    ]

    # A value shared across two different rows is never a candidate, because the
    # detector pairs placements only inside one container.
    unrelated = {
        "document_id": "d3",
        "fields": {
            "lines[0].customer_name": {
                "value": None,
                "candidate_values": ["shared"],
                "agreeing_engines": ["engine-a"],
            },
            "lines[1].description": {
                "value": None,
                "candidate_values": ["shared"],
                "agreeing_engines": ["engine-b"],
            },
        },
    }
    assert schema.slot_observations(unrelated, {}) == []

    # A value too short to identify anything is skipped, as is a field record
    # that is not an object at all.
    short = {
        "document_id": "d4",
        "fields": {
            "lines[0].quantity": {
                "value": "1",
                "candidate_values": None,
                "agreeing_engines": ["engine-a"],
            },
            "lines[0].line_number": {
                "value": "1",
                "candidate_values": None,
                "agreeing_engines": ["engine-b"],
            },
            "lines[0].broken": "not-a-field-record",
        },
    }
    assert schema.slot_observations(short, {}) == []
    assert schema.slot_evidence_class({"a"}, {"b"}) == "cross_engine_placement"
    assert schema.slot_evidence_class({"a"}, {"a", "b"}) == "intra_engine_duplicate"
    assert schema.container_of("document_type") == ("", "document_type")


def test_slot_candidates_require_repeated_and_varied_evidence():
    observations = []
    for document_id in ("d1", "d2"):
        observations.extend(schema.slot_observations(consensus_document(document_id), {}))
    candidates = schema.slot_candidates(observations, 2, 2)
    assert [item["slot_pair"] for item in candidates] == ["item_code|job_number"]
    assert candidates[0]["distinct_value_count"] == 2
    assert candidates[0]["cross_engine_observations"] == 0
    # One repeated value is a coincidence, not an equivalence.
    assert schema.slot_candidates(observations, 2, 3) == []
    assert schema.slot_candidates(observations, 99, 1) == []


def test_slot_placements_prefer_the_exception_queue_over_the_winning_cluster():
    # The consensus field record names only the engines behind its top cluster,
    # so a losing reading is recoverable only from the exception queue.
    document = {
        "document_id": "d1",
        "fields": {
            "header.total_amount": {
                "value": None,
                "candidate_values": ["100.00", "200.00"],
                "agreeing_engines": ["engine-a"],
            }
        },
    }
    placements = schema.engine_placements(
        document, {("d1", "header.total_amount"): {"engine-a": "100.00", "engine-b": "200.00"}}
    )
    assert placements["engine-b"]["header.total_amount"] == "200.00"
    # With no exception entry the winning cluster is the only available source.
    assert schema.engine_placements(document, {})["engine-a"]["header.total_amount"] == "100.00"


def test_slot_lane_disabled_retains_every_pair_without_spending(tmp_path):
    result = run_slots(
        tmp_path, "disabled", documents=[consensus_document("d1"), consensus_document("d2")]
    )
    assert result["summary"]["candidate_pair_count"] == 1
    assert result["summary"]["proposal_count"] == 0
    assert result["summary"]["gate_status"] == "blocked_pending_client_review"
    assert result["review_items"][0]["disposition"] == "client_review_required"
    handoff = json.loads((tmp_path / "disabled" / "handoff.json").read_text())
    assert handoff["engine"] == "slot_equivalence/deterministic"
    assert handoff["policy"]["automatic_field_collapse"] is False


def test_slot_lane_proposes_with_independent_confirmation(tmp_path):
    cross = {
        "document_id": "d1",
        "fields": {
            "lines[0].item_code": {
                "value": None,
                "candidate_values": ["12497"],
                "agreeing_engines": ["engine-a"],
            },
            "lines[0].job_number": {
                "value": None,
                "candidate_values": ["12497"],
                "agreeing_engines": ["engine-b"],
            },
            "lines[1].item_code": {
                "value": None,
                "candidate_values": ["12643"],
                "agreeing_engines": ["engine-a"],
            },
            "lines[1].job_number": {
                "value": None,
                "candidate_values": ["12643"],
                "agreeing_engines": ["engine-b"],
            },
        },
    }
    result = run_slots(
        tmp_path,
        "enabled",
        documents=[cross],
        enabled=True,
        client=Client(slot_output()),
        buddy_enabled=True,
        buddy_client=BuddyClient(slot_buddy_output()),
        buddy_provider="google",
        primary_provider="openai",
        model="m",
        buddy_model="b",
    )
    proposal = result["slot_proposals"][0]
    assert proposal["relationship"] == "equivalent"
    assert proposal["buddy_status"] == "confirmed"
    assert proposal["client_approval_required"] is True
    assert proposal["cross_engine_observations"] == 2
    assert result["summary"]["approval_eligible_count"] == 1
    assert not result["review_items"]


def test_slot_lane_holds_back_weak_or_unconfirmed_equivalences(tmp_path):
    # Only one engine ever used both names: retained, never eligible.
    intra = run_slots(
        tmp_path,
        "intra",
        documents=[consensus_document("d1"), consensus_document("d2")],
        enabled=True,
        client=Client(slot_output()),
        buddy_enabled=True,
        buddy_client=BuddyClient(slot_buddy_output()),
        buddy_provider="google",
        primary_provider="openai",
        model="m",
        buddy_model="b",
    )
    assert intra["summary"]["proposal_count"] == 1
    assert intra["summary"]["approval_eligible_count"] == 0
    assert any("one engine duplicating" in item["reason"] for item in intra["review_items"])

    # A verifier that does not confirm keeps the pair out of eligibility.
    denied = run_slots(
        tmp_path,
        "denied",
        enabled=True,
        client=Client(slot_output()),
        buddy_enabled=True,
        buddy_client=BuddyClient(slot_buddy_output(status="conflict")),
        buddy_provider="google",
        primary_provider="openai",
        model="m",
        buddy_model="b",
    )
    assert denied["slot_proposals"][0]["buddy_status"] == "conflict"
    assert denied["summary"]["approval_eligible_count"] == 0

    # No verifier at all is one model's opinion and is flagged as such.
    alone = run_slots(
        tmp_path,
        "alone",
        enabled=True,
        client=Client(slot_output()),
        primary_provider="openai",
        model="m",
    )
    assert alone["slot_proposals"][0]["buddy_status"] == "not_requested"
    assert any("without independent confirmation" in i["reason"] for i in alone["review_items"])

    # Below the confidence floor, and a non-equivalent classification.
    low = run_slots(
        tmp_path,
        "low",
        enabled=True,
        client=Client(slot_output(confidence=0.1)),
        primary_provider="openai",
        model="m",
    )
    assert any("confidence floor" in item["reason"] for item in low["review_items"])
    distinct = run_slots(
        tmp_path,
        "distinct",
        enabled=True,
        client=Client(slot_output(relationship="distinct")),
        primary_provider="openai",
        model="m",
    )
    assert any("classified distinct" in item["reason"] for item in distinct["review_items"])
    assert distinct["summary"]["approval_eligible_count"] == 0


def test_slot_lane_refuses_answers_about_fields_it_did_not_supply(tmp_path):
    wrong = run_slots(
        tmp_path,
        "wrong",
        enabled=True,
        client=Client(
            {"slot_proposals": [dict(slot_output()["slot_proposals"][0], field_a="other")]}
        ),
        primary_provider="openai",
        model="m",
    )
    assert any("not supplied" in item["reason"] for item in wrong["review_items"])
    assert wrong["summary"]["proposal_count"] == 0

    invented = run_slots(
        tmp_path,
        "invented",
        enabled=True,
        client=Client(slot_output(preferred="invented_field")),
        primary_provider="openai",
        model="m",
    )
    assert any(
        "not one of the supplied slots" in item["reason"] for item in invented["review_items"]
    )

    empty = run_slots(
        tmp_path,
        "empty",
        enabled=True,
        client=Client({"slot_proposals": ["not-an-object"]}),
        primary_provider="openai",
        model="m",
    )
    assert any("no usable proposal" in item["reason"] for item in empty["review_items"])


def test_slot_lane_records_provider_and_verifier_failures(tmp_path):
    failed = run_slots(
        tmp_path,
        "failed",
        enabled=True,
        client=Client(RuntimeError("boom")),
        primary_provider="openai",
        model="m",
    )
    assert any("provider call failed" in item["reason"] for item in failed["review_items"])
    raw = json.loads(next((tmp_path / "failed" / "raw").glob("slot_*.json")).read_text())
    assert raw["error_type"] == "RuntimeError"

    buddy_failed = run_slots(
        tmp_path,
        "buddyfail",
        enabled=True,
        client=Client(slot_output()),
        buddy_enabled=True,
        buddy_client=BuddyClient(RuntimeError("boom")),
        buddy_provider="google",
        primary_provider="openai",
        model="m",
        buddy_model="b",
    )
    assert buddy_failed["slot_proposals"][0]["buddy_status"] == "unverified"

    # A verifier answering about a different pair decides nothing.
    mismatched = run_slots(
        tmp_path,
        "mismatch",
        enabled=True,
        client=Client(slot_output()),
        buddy_enabled=True,
        buddy_client=BuddyClient(slot_buddy_output(pair="other|pair")),
        buddy_provider="google",
        primary_provider="openai",
        model="m",
        buddy_model="b",
    )
    assert mismatched["slot_proposals"][0]["buddy_status"] == "unverified"
    with pytest.raises(ValueError, match="must contain decisions"):
        schema.slot_buddy_lookup({}, ["a|b"])
    assert (
        schema.slot_buddy_lookup({"decisions": [{"slot_pair": "a|b", "status": "nope"}]}, ["a|b"])
        == {}
    )


def test_slot_lane_skips_pairs_a_client_already_decided(tmp_path):
    registry = {
        "registry_version": 1,
        "rules": [
            {
                "rule_id": "rule-1",
                "status": "client_approved",
                "rule_type": "slot_equivalence",
                "field_a": "job_number",
                "field_b": "item_code",
            }
        ],
    }
    result = run_slots(
        tmp_path,
        "approved",
        documents=[consensus_document("d1"), consensus_document("d2")],
        registry=registry,
        enabled=True,
        client=Client(slot_output()),
        primary_provider="openai",
        model="m",
    )
    assert result["summary"]["already_approved_pairs"] == [
        {"slot_pair": "item_code|job_number", "rule_id": "rule-1"}
    ]
    assert result["summary"]["proposal_count"] == 0
    # A slot rule must never be read as a source-label mapping rule.
    assert schema.rule_for(registry, "hash", "job_number") is None
    assert schema.slot_rule_for({"rules": [{"status": "draft"}]}, "a", "b") is None


def test_slot_lane_validates_inputs_and_limits(tmp_path):
    good = {"documents": [consensus_document()]}
    assert schema.load_consensus(write(tmp_path / "c.json", good))[0]["document_id"] == "d1"
    for bad in ({"documents": []}, {"documents": [{"document_id": "d"}]}, []):
        with pytest.raises(ValueError, match="Consensus input must contain"):
            schema.load_consensus(write(tmp_path / "bad.json", bad))
    engines = schema.load_consensus_exceptions(
        write(
            tmp_path / "e.json",
            {
                "exceptions": [
                    {"document_id": "d", "field": "f", "engines": {"a": "1"}},
                    {},
                    {"engines": {}},
                ]
            },
        )
    )
    assert engines == {("d", "f"): {"a": "1"}}
    with pytest.raises(ValueError, match="must contain an exceptions list"):
        schema.load_consensus_exceptions(write(tmp_path / "e2.json", {}))
    with pytest.raises(ValueError, match="Invalid slot-equivalence limits"):
        run_slots(tmp_path, "eff", effort="nonsense")
    with pytest.raises(ValueError, match="thresholds must be positive"):
        run_slots(tmp_path, "thr", min_observations=0)
    with pytest.raises(ValueError, match="requires an LLM client"):
        run_slots(tmp_path, "noclient", enabled=True)
    with pytest.raises(ValueError, match="independent primary and buddy"):
        run_slots(
            tmp_path,
            "sameVendor",
            enabled=True,
            client=Client(slot_output()),
            buddy_enabled=True,
            buddy_client=BuddyClient(slot_buddy_output()),
            primary_provider="openai",
            buddy_provider="openai",
            model="m",
            buddy_model="b",
        )
    base = tmp_path / "reuse"
    base.mkdir()
    (base / "out.json").write_text("{}")
    with pytest.raises(ValueError, match="distinct and new"):
        schema.run_slot_equivalence(
            documents=[consensus_document()],
            exception_engines={},
            registry={"registry_version": 0, "rules": []},
            out=str(base / "out.json"),
            exceptions=str(base / "exceptions.json"),
            handoff=str(base / "handoff.json"),
            raw_dir=str(base / "raw"),
        )
    used = tmp_path / "usedraw"
    used.mkdir()
    (used / "raw").mkdir()
    (used / "raw" / "old.json").write_text("{}")
    with pytest.raises(ValueError, match="new or empty"):
        schema.run_slot_equivalence(
            documents=[consensus_document()],
            exception_engines={},
            registry={"registry_version": 0, "rules": []},
            out=str(used / "out.json"),
            exceptions=str(used / "exceptions.json"),
            handoff=str(used / "handoff.json"),
            raw_dir=str(used / "raw"),
        )


def test_registry_writes_slot_rules_only_from_confirmed_equivalences(tmp_path):
    proposal = {
        "proposal_id": "slot-1",
        "slot_pair": "item_code|job_number",
        "field_a": "item_code",
        "field_b": "job_number",
        "relationship": "equivalent",
        "preferred_field": "job_number",
        "confidence": 0.99,
        "evidence_classes": ["cross_engine_placement"],
        "observation_count": 4,
        "cross_engine_observations": 4,
        "buddy_status": "confirmed",
        "decision_source": "llm",
    }
    decisions = {"decisions": [{"proposal_id": "slot-1", "decision": "approve"}]}
    updated = schema.update_registry(
        {"registry_version": 1, "rules": []}, {"slot_proposals": [proposal]}, decisions
    )
    rule = updated["update_log"][0]
    assert rule["rule_type"] == "slot_equivalence"
    assert rule["preferred_field"] == "job_number"
    assert updated["refused_decisions"] == []

    unconfirmed = schema.update_registry(
        {"registry_version": 1, "rules": []},
        {"slot_proposals": [dict(proposal, buddy_status="not_requested")]},
        decisions,
    )
    assert unconfirmed["update_log"] == []
    assert "independent verifier" in unconfirmed["refused_decisions"][0]["reason"]

    not_equivalent = schema.update_registry(
        {"registry_version": 1, "rules": []},
        {"slot_proposals": [dict(proposal, relationship="distinct")]},
        decisions,
    )
    assert "not equivalent" in not_equivalent["refused_decisions"][0]["reason"]

    # An unknown or unapproved decision changes nothing.
    assert (
        schema.update_registry(
            {"registry_version": 1, "rules": []},
            {"slot_proposals": [proposal]},
            {"decisions": [{"proposal_id": "missing", "decision": "approve"}, "junk"]},
        )["update_log"]
        == []
    )


def test_slot_cli_runs_end_to_end(monkeypatch, tmp_path, capsys):
    consensus = write(
        tmp_path / "consensus.json",
        {"documents": [consensus_document("d1"), consensus_document("d2")]},
    )
    exceptions_in = write(tmp_path / "exceptions_in.json", {"exceptions": []})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "schema_discovery.py",
            "slot-equivalence",
            str(consensus),
            str(exceptions_in),
            "--out",
            str(tmp_path / "slots.json"),
            "--exceptions",
            str(tmp_path / "slot_exceptions.json"),
            "--handoff-out",
            str(tmp_path / "slot_handoff.json"),
            "--raw-dir",
            str(tmp_path / "slot_raw"),
        ],
    )
    schema.main()
    assert json.loads(capsys.readouterr().out)["client_review_items"] == 1
    assert json.loads((tmp_path / "slots.json").read_text())["summary"]["candidate_pair_count"] == 1


def test_slot_cli_builds_clients_and_reports_failure(monkeypatch, tmp_path, capsys):
    consensus = write(tmp_path / "consensus.json", {"documents": [consensus_document()]})
    exceptions_in = write(tmp_path / "exceptions_in.json", {"exceptions": []})
    built = []

    def fake_build_client(credential_env, timeout, retries, provider):
        built.append(provider)
        return Client(slot_output()) if provider == "openai" else BuddyClient(slot_buddy_output())

    monkeypatch.setattr(schema, "build_client", fake_build_client)
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "y")
    argv = [
        "schema_discovery.py",
        "slot-equivalence",
        str(consensus),
        str(exceptions_in),
        "--out",
        str(tmp_path / "slots.json"),
        "--exceptions",
        str(tmp_path / "slot_exceptions.json"),
        "--handoff-out",
        str(tmp_path / "slot_handoff.json"),
        "--raw-dir",
        str(tmp_path / "slot_raw"),
        "--enable",
        "--provider",
        "openai",
        "--buddy",
        "--buddy-provider",
        "google",
        "--model",
        "m",
        "--buddy-model",
        "b",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    schema.main()
    assert built == ["openai", "google"]
    capsys.readouterr()

    # A second run against the same paths must refuse rather than overwrite.
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="Schema discovery failed"):
        schema.main()
