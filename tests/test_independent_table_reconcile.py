"""Tests for deterministic Document AI/source-row reconciliation."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

reconcile = importlib.import_module("independent_table_reconcile")


def write(path, value):
    path.write_text(json.dumps(value) if not isinstance(value, str) else value)
    return path


def registry():
    fingerprint = reconcile.template_fingerprint(["Amount", "Dealer"])
    return {
        "rules": [
            {
                "rule_id": "amount",
                "status": "client_approved",
                "template_fingerprint": fingerprint,
                "source_label": "Amount",
                "canonical_field": "commission_amount",
                "semantic_type": "financial",
            },
            {
                "rule_id": "dealer",
                "status": "client_approved",
                "template_fingerprint": fingerprint,
                "source_label": "Dealer",
                "canonical_field": "dealer_name",
                "semantic_type": "dealer",
            },
        ]
    }


def rows(value="12.50"):
    return {
        "source_rows": [
            {
                "document_id": "p1",
                "page_id": "p1",
                "source_row_id": "r1",
                "row_number": 1,
                "cells": [
                    {
                        "source_label": "Amount",
                        "visible_value": value,
                        "evidence_text": value,
                        "canonical_mapping": {
                            "status": "client_approved",
                            "canonical_field": "commission_amount",
                            "registry_rule_id": "amount",
                        },
                    },
                    {
                        "source_label": "Dealer",
                        "visible_value": "Acme",
                        "evidence_text": "Acme",
                        "canonical_mapping": {
                            "status": "client_approved",
                            "canonical_field": "dealer_name",
                            "registry_rule_id": "dealer",
                        },
                    },
                ],
            }
        ]
    }


def handoff(cells=None):
    return {
        "provider": "google_document_ai",
        "records": [
            {
                "page_id": "p1",
                "independence_group": "google_document_ai",
                "raw_response": "raw.json",
                "engine": "google_document_ai/ocr",
                "source_tables": [
                    {
                        "source_headers": ["Amount", "Dealer"],
                        "source_rows": [
                            {
                                "source_row_number": 1,
                                "cells": cells
                                or [
                                    {"source_label": "Amount", "evidence_text": "$12.50"},
                                    {"source_label": "Dealer", "evidence_text": "ACME"},
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }


def test_normalization_and_reconciliation(tmp_path):
    assert reconcile.normalized("($1,200.00)", True) == "-1200.00"
    assert reconcile.normalized("not-a-number", True) is None
    assert reconcile.normalized(" A  C M E ") == "a c m e"
    reg = registry()
    candidates = reconcile.document_ai_cells([write(tmp_path / "ai.json", handoff())], reg)
    outcomes, exceptions = reconcile.reconcile(reconcile.source_rows(rows()), candidates, reg)
    assert {item["decision"] for item in outcomes} == {"independent_exact_agreement"}
    assert {item["reason"] for item in exceptions} == {
        "financial_identity_independent_reconciliation_required"
    }
    assert all(item["client_review_required"] for item in outcomes)
    disagree, errors = reconcile.reconcile(reconcile.source_rows(rows("13")), candidates, reg)
    assert disagree[0]["decision"] == "independent_value_disagreement"
    assert errors[0]["reason"] == "independent_value_disagreement"
    missing, _ = reconcile.reconcile(reconcile.source_rows(rows()), {}, reg)
    assert missing[0]["decision"] == "independent_cell_missing"
    ambiguous = {("p1", 1, "commission_amount"): [{}, {}]}
    assert (
        reconcile.reconcile(reconcile.source_rows(rows()), ambiguous, reg)[0][0]["decision"]
        == "independent_cell_ambiguous"
    )


def test_boundaries_and_no_clobber(tmp_path):
    source = write(tmp_path / "rows.json", rows())
    registry_path = write(tmp_path / "registry.json", registry())
    evidence = write(tmp_path / "ai.json", handoff())
    result = reconcile.run(
        source,
        registry_path,
        [evidence],
        tmp_path / "out.json",
        tmp_path / "exceptions.json",
        tmp_path / "adapter.json",
    )
    assert result == {
        "reconciliations": 2,
        "compared_against_independent_evidence": 2,
        "review_items": 2,
        "corroborated": True,
        "coverage": {
            "tables_seen": 1,
            "tables_rejected": 0,
            "rows_seen": 1,
            "rows_rejected": 0,
            "cells_seen": 2,
            "cells_rejected": 0,
            "cells_unmapped": 0,
            "cells_mapped": 2,
        },
    }
    adapter = json.loads((tmp_path / "adapter.json").read_text())
    assert adapter["client_approval_permitted"] is False

    # A corroboration run that compared nothing corroborated nothing. Counting
    # outcome records instead of comparisons let 300 `independent_cell_missing`
    # results -- every one a cell with no independent counterpart -- report
    # `corroborated: true` with zero review items, on a real corpus.
    unmatched = write(tmp_path / "unmatched-ai.json", handoff())
    payload = json.loads(unmatched.read_text())
    for record in payload["records"]:
        for table in record["source_tables"]:
            table["source_headers"] = ["a label no approved rule names"]
    unmatched.write_text(json.dumps(payload))
    missed = reconcile.run(
        source,
        registry_path,
        [unmatched],
        tmp_path / "missed.json",
        tmp_path / "missed-exceptions.json",
        tmp_path / "missed-adapter.json",
    )
    assert missed["reconciliations"] == 2, "the cells are still reported, not dropped"
    assert missed["compared_against_independent_evidence"] == 0
    assert missed["corroborated"] is False
    written = json.loads((tmp_path / "missed-exceptions.json").read_text())
    reasons = [item["reason"] for item in written["exceptions"]]
    assert "independent_reconciliation_produced_no_comparison" in reasons
    with pytest.raises(ValueError, match="exists"):
        reconcile.new_path(tmp_path / "out.json")
    with pytest.raises(ValueError, match="distinct"):
        reconcile.run(
            source,
            registry_path,
            [evidence],
            tmp_path / "x.json",
            tmp_path / "x.json",
            tmp_path / "a.json",
        )
    with pytest.raises(ValueError, match="Source-row"):
        reconcile.source_rows({})
    with pytest.raises(ValueError, match="handoff"):
        reconcile.document_ai_cells([write(tmp_path / "bad.json", {})], registry())
    with pytest.raises(ValueError, match="provenance"):
        reconcile.document_ai_cells(
            [
                write(
                    tmp_path / "bad-record.json",
                    {"provider": "google_document_ai", "records": [{}]},
                )
            ],
            registry(),
        )
    with pytest.raises(ValueError, match="rules"):
        reconcile.run(
            source,
            write(tmp_path / "bad-registry.json", {}),
            [evidence],
            tmp_path / "o2.json",
            tmp_path / "e2.json",
            tmp_path / "a2.json",
        )
    with pytest.raises(ValueError, match="object"):
        reconcile.load_object(write(tmp_path / "list.json", []), "list")
    assert reconcile.normalized(None) is None and reconcile.protected({}) is False
    sparse = {
        "provider": "google_document_ai",
        "records": [
            {
                "page_id": "p1",
                "independence_group": "google_document_ai",
                "source_tables": [
                    "skip",
                    {
                        "source_headers": ["Amount"],
                        "source_rows": [
                            "skip",
                            {"source_row_number": 1, "cells": ["skip", {"source_label": 1}]},
                        ],
                    },
                ],
            }
        ],
    }
    assert reconcile.document_ai_cells([write(tmp_path / "sparse.json", sparse)], registry()) == {}
    bad_page = {
        "provider": "google_document_ai",
        "records": [
            {"page_id": None, "independence_group": "google_document_ai", "source_tables": []}
        ],
    }
    with pytest.raises(ValueError, match="page_id"):
        reconcile.document_ai_cells([write(tmp_path / "bad-page.json", bad_page)], registry())
    unknown = {
        "provider": "google_document_ai",
        "records": [
            {
                "page_id": "p1",
                "independence_group": "google_document_ai",
                "source_tables": [
                    {
                        "source_headers": ["Other"],
                        "source_rows": [
                            {"source_row_number": 1, "cells": [{"source_label": "Other"}]}
                        ],
                    }
                ],
            }
        ],
    }
    assert (
        reconcile.document_ai_cells([write(tmp_path / "unknown.json", unknown)], registry()) == {}
    )
    skipped = [
        {"page_id": None, "row_number": 1},
        {"page_id": "p1", "row_number": None, "cells": ["skip", {"canonical_mapping": "bad"}]},
        {
            "page_id": "p1",
            "row_number": 1,
            "cells": [
                "skip",
                {"canonical_mapping": "bad"},
                {"canonical_mapping": {"status": "client_approved", "canonical_field": None}},
            ],
        },
    ]
    assert list(reconcile.llm_cells(skipped, registry())) == []
    unprotected = {
        "rules": [
            {"rule_id": "name", "canonical_field": "description", "semantic_type": "description"}
        ]
    }
    rows_unprotected = [
        {
            "page_id": "p1",
            "row_number": 1,
            "cells": [
                {
                    "visible_value": "x",
                    "canonical_mapping": {
                        "status": "client_approved",
                        "canonical_field": "description",
                        "registry_rule_id": "name",
                    },
                }
            ],
        }
    ]
    assert (
        reconcile.reconcile(
            rows_unprotected, {("p1", 1, "description"): [{"value": "x"}]}, unprotected
        )[1]
        == []
    )


def test_main(monkeypatch, capsys):
    monkeypatch.setattr(reconcile, "run", lambda *args: {"reconciliations": 1})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "independent_table_reconcile.py",
            "rows",
            "--registry",
            "registry",
            "--document-ai",
            "a",
            "b",
            "--out",
            "out",
            "--exceptions",
            "errors",
            "--adapter-out",
            "adapter",
        ],
    )
    reconcile.main()
    assert json.loads(capsys.readouterr().out) == {"reconciliations": 1}
    monkeypatch.setattr(reconcile, "run", lambda *args: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(SystemExit, match="failed"):
        reconcile.main()
