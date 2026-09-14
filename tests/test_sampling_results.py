import importlib
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

sampling = importlib.import_module("sampling")


def selected():
    certainty = [{"document_id": "certain", "value": 100.0}]
    sampled = [{"document_id": "sample", "value": 40.0}]
    return certainty, sampled


def result(plan_hash, outcomes):
    return {
        "schema_version": "sample_review_results_v1",
        "sample_plan_sha256": plan_hash,
        "outcomes": outcomes,
    }


def outcome(document_id, recorded, status="reviewed_correct", audited=None):
    value = {
        "document_id": document_id,
        "recorded_value": recorded,
        "outcome": status,
    }
    if audited is not None:
        value["audited_value"] = audited
    return value


def test_sample_plan_hash_is_stable_and_excludes_generated_output_fields():
    plan = {
        "schema_version": "sample_plan_v1",
        "generated_at": "one",
        "seed": 7,
        "population": {"eligible_documents": 2},
        "monetary_unit_sample": {"sampled_document_ids": ["a"]},
        "attribute_sample": {"plan": []},
        "notes": ["presentation"],
    }
    changed = {**plan, "generated_at": "two", "notes": ["different"]}
    assert sampling.sample_plan_sha256(plan) == sampling.sample_plan_sha256(changed)


def test_empty_or_incomplete_results_block_the_accuracy_statement():
    certainty, sampled = selected()
    plan_hash = "a" * 64
    empty = sampling.reconcile_review_results(result(plan_hash, []), plan_hash, certainty, sampled)
    assert empty["status"] == "blocked"
    assert empty["missing_document_ids"] == ["certain", "sample"]

    incomplete = sampling.reconcile_review_results(
        result(plan_hash, [outcome("certain", 100, audited=100)]),
        plan_hash,
        certainty,
        sampled,
    )
    assert incomplete["missing_document_ids"] == ["sample"]


def test_duplicate_extra_stale_and_value_mismatched_results_block():
    certainty, sampled = selected()
    plan_hash = "a" * 64
    cases = [
        result(
            plan_hash,
            [
                outcome("certain", 100, audited=100),
                outcome("certain", 100, audited=100),
                outcome("sample", 40, audited=40),
            ],
        ),
        result(
            plan_hash,
            [
                outcome("certain", 100, audited=100),
                outcome("sample", 40, audited=40),
                outcome("foreign", 1, audited=1),
            ],
        ),
        result(
            "b" * 64,
            [outcome("certain", 100, audited=100), outcome("sample", 40, audited=40)],
        ),
        result(
            plan_hash,
            [outcome("certain", 99, audited=99), outcome("sample", 40, audited=40)],
        ),
    ]
    for data in cases:
        assert (
            sampling.reconcile_review_results(data, plan_hash, certainty, sampled)["status"]
            == "blocked"
        )


def test_abstained_failed_and_malformed_outcomes_block():
    certainty, sampled = selected()
    plan_hash = "a" * 64
    for status in ("abstained", "failed"):
        data = result(
            plan_hash,
            [outcome("certain", 100, status), outcome("sample", 40, audited=40)],
        )
        reconciled = sampling.reconcile_review_results(data, plan_hash, certainty, sampled)
        assert reconciled["status"] == "blocked"
        assert reconciled["unresolved_document_ids"] == ["certain"]

    malformed = result(
        plan_hash,
        [outcome("certain", 100), outcome("sample", 40, "reviewed_error", audited=40)],
    )
    assert (
        sampling.reconcile_review_results(malformed, plan_hash, certainty, sampled)["status"]
        == "blocked"
    )


def test_complete_zero_error_and_error_results_project_only_after_reconciliation():
    certainty, sampled = selected()
    plan_hash = "a" * 64
    clean = sampling.reconcile_review_results(
        result(
            plan_hash,
            [outcome("certain", 100, audited=100), outcome("sample", 40, audited=40)],
        ),
        plan_hash,
        certainty,
        sampled,
    )
    assert clean["status"] == "completed"
    assert clean["findings"] == [
        {"document_id": "certain", "recorded_value": 100.0, "audited_value": 100.0},
        {"document_id": "sample", "recorded_value": 40.0, "audited_value": 40.0},
    ]

    with_error = sampling.reconcile_review_results(
        result(
            plan_hash,
            [
                outcome("certain", 100, "reviewed_error", audited=90),
                outcome("sample", 40, audited=40),
            ],
        ),
        plan_hash,
        certainty,
        sampled,
    )
    assert with_error["status"] == "completed"
    projection = sampling.project(with_error["findings"], 20, certainty, sampled)
    assert projection["certainty_stratum_overstatement"] == 10.0


@pytest.mark.parametrize(
    "data",
    [
        None,
        {"schema_version": "wrong", "outcomes": []},
        {"schema_version": "sample_review_results_v1", "outcomes": None},
    ],
)
def test_malformed_result_envelopes_block(data):
    certainty, sampled = selected()
    assert (
        sampling.reconcile_review_results(data, "a" * 64, certainty, sampled)["status"] == "blocked"
    )


@pytest.mark.parametrize(
    "bad_outcome",
    [
        "bad",
        {},
        outcome("certain", True, audited=100),
        outcome("certain", 100, audited=math.nan),
        outcome("certain", 100, "unknown", audited=100),
        outcome("certain", 100, "reviewed_correct", audited=90),
    ],
)
def test_malformed_individual_outcomes_block(bad_outcome):
    certainty, sampled = selected()
    data = result(
        "a" * 64,
        [bad_outcome, outcome("sample", 40, audited=40)],
    )
    assert (
        sampling.reconcile_review_results(data, "a" * 64, certainty, sampled)["status"] == "blocked"
    )
