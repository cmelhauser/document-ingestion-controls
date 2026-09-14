"""Tests for the intentionally small client review package."""

import importlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

package = importlib.import_module("client_review.package")


def payload():
    first = {
        "review_item_id": "review-item-1",
        "document_id": "doc-1",
        "field": "header.total",
        "reason": "partial_disagreement",
        "evidence_text": "100.00 / 100.01",
    }
    second = {
        "review_item_id": "review-item-2",
        "document_id": "doc-2",
        "field": "document_type",
        "reason": "document_type_proposal",
        "evidence_text": "commission statement",
    }
    groups = [
        {
            "group_id": "decision_group:00001",
            "template_family": "commission_report",
            "finding_family": "provider_disagreement",
            "field_families": ["header"],
            "item_count": 1,
            "document_count": 1,
            "protected": False,
            "source_item_ids": ["review-item-1"],
            "representative_reasons": ["partial_disagreement"],
        },
        {
            "group_id": "decision_group:00002",
            "template_family": "unknown",
            "finding_family": "document_type_proposal",
            "field_families": ["document_type"],
            "item_count": 1,
            "document_count": 1,
            "protected": False,
            "source_item_ids": ["review-item-2"],
            "representative_reasons": ["document_type_proposal"],
        },
    ]
    return {
        "summary": {"source_items": 2, "covered_dependency_items": 0},
        "decision_groups": groups,
        "client_visible_items": [first, second],
    }


def test_package_contains_only_client_workbook_and_word_guide(tmp_path):
    output = package.write_client_review_package(payload(), tmp_path / "client_review_package")
    assert sorted(path.name for path in output.iterdir()) == [
        "client_review_guide.docx",
        "client_review_package.xlsx",
        "decision_pages.docx",
    ]
    with zipfile.ZipFile(output / "client_review_guide.docx") as guide:
        assert "word/document.xml" in guide.namelist()
    with zipfile.ZipFile(output.parent / "client_review_package.zip") as package_zip:
        assert sorted(package_zip.namelist()) == [
            "client_review_guide.docx",
            "client_review_package.xlsx",
            "decision_pages.docx",
        ]
    with zipfile.ZipFile(output / "client_review_package.xlsx") as workbook:
        names = workbook.read("xl/workbook.xml").decode()
        assert "Executive Summary" in names
        assert "Decisions Needed" in names
        assert "Representative Cards" in names
        assert "Drilldown" not in names
        assert "Appendix" not in names


def test_package_refuses_to_overwrite(tmp_path):
    output = tmp_path / "client_review_package"
    package.write_client_review_package(payload(), output)
    with pytest.raises(FileExistsError, match="overwrite"):
        package.write_client_review_package(payload(), output)


def test_package_requires_groups_to_cover_visible_items():
    broken = payload()
    broken["decision_groups"][0]["source_item_ids"] = []
    with pytest.raises(ValueError, match="cover every client-visible item"):
        package._validate(broken)


@pytest.mark.parametrize(
    ("finding", "expected"),
    [
        ("provider_extraction_failure", "Internal only — no client choice"),
        ("document_type_proposal", "Confirm document family/type"),
        ("no_extractable_text", "Internal only — no client choice"),
        ("no_high_signal_document_type", "Confirm document type"),
        ("provider_disagreement", "Internal only — no client choice"),
    ],
)
def test_decision_guidance_covers_client_and_protected_families(finding, expected):
    guidance = package._decision_guidance({"finding_family": finding})
    assert expected in guidance[3]


def test_decision_guidance_uses_mapping_default():
    assert (
        "Approve proposed routing"
        in package._decision_guidance({"finding_family": "formatting_review"})[3]
    )


def test_validation_rejects_missing_and_duplicate_contract_parts():
    with pytest.raises(ValueError, match="non-empty"):
        package._validate({"decision_groups": [], "client_visible_items": []})
    with pytest.raises(ValueError, match="client_visible_items"):
        package._validate({"decision_groups": [{}]})
    broken = payload()
    broken["decision_groups"][1]["group_id"] = broken["decision_groups"][0]["group_id"]
    with pytest.raises(ValueError, match="unique group_id"):
        package._validate(broken)
    broken = payload()
    broken["client_visible_items"][1]["review_item_id"] = broken["client_visible_items"][0][
        "review_item_id"
    ]
    with pytest.raises(ValueError, match="unique review_item_id"):
        package._validate(broken)
    broken = payload()
    broken["decision_groups"][0]["item_count"] = 2
    with pytest.raises(ValueError, match="reconcile"):
        package._validate(broken)


def test_source_reference_uses_page_provenance_and_fallbacks():
    sibling = {"page_id": "source__p227", "source_file": "source.pdf"}
    assert package._source_reference({"page_id": "source__p3"}, [sibling]) == (
        "source.pdf",
        "3",
        "source__p3",
    )
    assert package._source_reference({}, []) == (
        "Source file recorded in the internal run",
        "Page number recorded in the internal run",
        "",
    )
    assert package._friendly_text("x" * 200, 10).endswith("…")


def test_package_rejects_existing_zip_and_imports_protected_or_invalid_choices(tmp_path):
    output = package.write_client_review_package(payload(), tmp_path / "client_review_package")
    (tmp_path / "client_review_package_2.zip").write_bytes(b"already exists")
    with pytest.raises(FileExistsError, match="ZIP"):
        package.write_client_review_package(payload(), tmp_path / "client_review_package_2")
    issued = output / "client_review_package.xlsx"
    returned = tmp_path / "returned.xlsx"
    shutil.copyfile(issued, returned)
    issued_rows = package._xlsx_cell_values(issued, "Decisions Needed")
    rows = [list(row) for row in issued_rows]
    header_index = next(index for index, row in enumerate(rows) if row and row[0] == "Group")
    positions = {name: index for index, name in enumerate(rows[header_index])}
    rows[header_index + 1][positions["Your choice"]] = "bad"
    rows[header_index + 2][positions["Your choice"]] = "not allowed"
    original = package._xlsx_cell_values
    package._xlsx_cell_values = lambda path, _sheet: rows if path == returned else issued_rows
    try:
        result = package.import_client_decisions(returned, tmp_path / "invalid.json", issued)
    finally:
        package._xlsx_cell_values = original
    assert result["complete"] is False
    assert len(result["invalid_decisions"]) == 2

    alternative_rows = [list(row) for row in issued_rows]
    alternative_rows[header_index + 2][positions["Your choice"]] = "Provide alternative in comment"
    package._xlsx_cell_values = lambda path, _sheet: (
        alternative_rows if path == returned else issued_rows
    )
    try:
        alternative = package.import_client_decisions(
            returned, tmp_path / "alternative.json", issued
        )
    finally:
        package._xlsx_cell_values = original
    assert alternative["invalid_decisions"] == [
        {
            "decision_id": "decision_group:00002",
            "reason": "alternative decision requires Your note",
        }
    ]
    alternative_rows[header_index + 2][positions["Your note"]] = "Use credit memo"
    package._xlsx_cell_values = lambda path, _sheet: (
        alternative_rows if path == returned else issued_rows
    )
    try:
        completed_alternative = package.import_client_decisions(
            returned, tmp_path / "completed-alternative.json", issued
        )
    finally:
        package._xlsx_cell_values = original
    assert completed_alternative["invalid_decisions"] == []


def test_workbook_formats_protected_rows(tmp_path):
    protected_payload = payload()
    protected_payload["decision_groups"][0]["protected"] = True
    output = package.write_client_review_package(protected_payload, tmp_path / "protected_package")
    assert output.is_dir()


def test_xlsx_reader_handles_inline_and_missing_cells(tmp_path):
    path = tmp_path / "minimal.xlsx"
    content_types = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
    workbook = '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Decisions Needed" r:id="rId1"/></sheets></workbook>'
    rels = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="xl/worksheets/sheet1.xml"/></Relationships>'
    sheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><v>Group</v></c><c r="bad"/><c r="C1"><v/></c></row></sheetData></worksheet>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    assert package._xlsx_cell_values(path, "Decisions Needed") == [["Group", "", ""]]


def test_cli_main_paths(tmp_path, monkeypatch):
    consolidation = tmp_path / "consolidation.json"
    consolidation.write_text(json.dumps(payload()))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_package.py",
            "create",
            str(consolidation),
            "--out-dir",
            str(tmp_path / "created"),
        ],
    )
    assert package.main() == 0
    returned = tmp_path / "returned.xlsx"
    shutil.copyfile(tmp_path / "created/client_review_package.xlsx", returned)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_package.py",
            "import-decisions",
            str(returned),
            "--issued-workbook",
            str(tmp_path / "created/client_review_package.xlsx"),
            "--out",
            str(tmp_path / "imported.json"),
        ],
    )
    assert package.main() == 2
    monkeypatch.setattr(
        package,
        "import_client_decisions",
        lambda _workbook, _out, _issued: {"complete": True},
    )
    assert package.main() == 0
    monkeypatch.setattr(sys, "argv", ["client_review_package.py"])
    with pytest.raises(SystemExit):
        package.main()


def test_import_decisions_rejects_bad_workbook_shapes(tmp_path, monkeypatch):
    monkeypatch.setattr(package, "_xlsx_cell_values", lambda _path, _sheet: [])
    with pytest.raises(ValueError, match="current Decisions Needed"):
        package._decision_rows(tmp_path / "missing.xlsx")
    monkeypatch.setattr(package, "_xlsx_cell_values", lambda _path, _sheet: [["Group"]])
    with pytest.raises(ValueError, match="exact contract"):
        package._decision_rows(tmp_path / "missing.xlsx")
    monkeypatch.setattr(
        package,
        "_xlsx_cell_values",
        lambda _path, _sheet: [list(package.DECISION_HEADERS) + ["Extra"]],
    )
    with pytest.raises(ValueError, match="exact contract"):
        package._decision_rows(tmp_path / "extra.xlsx")
    header = list(package.DECISION_HEADERS)
    monkeypatch.setattr(package, "_xlsx_cell_values", lambda _path, _sheet: [header, [""]])
    assert package._decision_rows(tmp_path / "missing.xlsx")[1] == []
    monkeypatch.setattr(
        package, "_xlsx_cell_values", lambda _path, _sheet: [header, ["", "populated"]]
    )
    with pytest.raises(ValueError, match="without a Group"):
        package._decision_rows(tmp_path / "missing.xlsx")
    monkeypatch.setattr(
        package,
        "_xlsx_cell_values",
        lambda _path, _sheet: [header, ["duplicate"], ["duplicate"]],
    )
    with pytest.raises(ValueError, match="duplicate"):
        package._decision_rows(tmp_path / "missing.xlsx")


def test_import_client_decisions_reads_returned_workbook(tmp_path):
    output = package.write_client_review_package(payload(), tmp_path / "client_review_package")
    decisions = tmp_path / "client_decisions.json"
    issued = output / "client_review_package.xlsx"
    workbook = tmp_path / "returned.xlsx"
    shutil.copyfile(issued, workbook)
    result = package.import_client_decisions(workbook, decisions, issued)
    imported = decisions.read_text(encoding="utf-8")
    assert '"proposal_only": true' in imported
    assert '"decision_id": "decision_group:00001"' in imported
    assert result["complete"] is False
    assert result["unresolved_decision_ids"] == ["decision_group:00002"]
    assert result["issued_group_count"] == 2
    assert result["safe_consolidation_sha256"] == package._json_sha256(payload())
    with pytest.raises(ValueError, match="separate"):
        package.import_client_decisions(issued, tmp_path / "same.json", issued)


def test_preserve_client_responses_records_exact_values(tmp_path, monkeypatch):
    output = package.write_client_review_package(payload(), tmp_path / "client_review_package")
    issued = output / "client_review_package.xlsx"
    returned = tmp_path / "returned.xlsx"
    shutil.copyfile(issued, returned)
    original_rows = package._decision_rows(returned)
    response_rows = [list(row) for row in original_rows[1]]
    response_rows[0][12] = "Provide alternative in comment"
    response_rows[0][13] = "Client supplied exact alternative."

    def rows_for(path):
        return original_rows if path != returned else (original_rows[0], response_rows)

    monkeypatch.setattr(package, "_decision_rows", rows_for)
    preserved = tmp_path / "client_responses_preserved.json"
    result = package.preserve_client_responses(returned, preserved, issued)
    assert result["lineage_status"] == "verified"
    assert result["response_count"] == 1
    assert result["responses"] == [
        {
            "decision_id": "decision_group:00001",
            "your_choice": "Provide alternative in comment",
            "your_note": "Client supplied exact alternative.",
        }
    ]
    assert json.loads(preserved.read_text(encoding="utf-8")) == result


def test_preserve_client_responses_retains_legacy_lineage_exception(tmp_path, monkeypatch):
    output = package.write_client_review_package(payload(), tmp_path / "client_review_package")
    issued = output / "client_review_package.xlsx"
    returned = tmp_path / "returned.xlsx"
    shutil.copyfile(issued, returned)
    monkeypatch.setattr(
        package,
        "_package_metadata",
        lambda _path: (_ for _ in ()).throw(
            ValueError("workbook is missing valid immutable package lineage")
        ),
    )
    result = package.preserve_client_responses(returned, tmp_path / "preserved.json", issued)
    assert result["lineage_status"] == "metadata_missing_or_invalid"
    assert result["response_count"] == 0
    assert "immutable package lineage" in result["lineage_exception"]


def test_preserve_client_responses_reports_contract_differences(tmp_path, monkeypatch):
    output = package.write_client_review_package(payload(), tmp_path / "client_review_package")
    issued = output / "client_review_package.xlsx"
    returned = tmp_path / "returned.xlsx"
    shutil.copyfile(issued, returned)
    header, rows = package._decision_rows(returned)

    changed_group = [list(row) for row in rows]
    changed_group[0][0] = "decision_group:changed"
    monkeypatch.setattr(
        package,
        "_decision_rows",
        lambda path: (header, changed_group) if path == returned else (header, rows),
    )
    result = package.preserve_client_responses(returned, tmp_path / "group-diff.json", issued)
    assert result["lineage_status"] == "group_rows_differ"

    changed_fixed = [list(row) for row in rows]
    changed_fixed[0][1] = "changed fixed value"
    monkeypatch.setattr(
        package,
        "_decision_rows",
        lambda path: (header, changed_fixed) if path == returned else (header, rows),
    )
    result = package.preserve_client_responses(returned, tmp_path / "fixed-diff.json", issued)
    assert result["lineage_status"] == "fixed_cells_changed"
    assert result["fixed_cell_differences"] == ["decision_group:00001"]


def test_preserve_client_responses_reports_changed_metadata_and_no_issued(tmp_path, monkeypatch):
    output = package.write_client_review_package(payload(), tmp_path / "client_review_package")
    issued = output / "client_review_package.xlsx"
    returned = tmp_path / "returned.xlsx"
    shutil.copyfile(issued, returned)
    result = package.preserve_client_responses(returned, tmp_path / "no-issued.json")
    assert result["lineage_status"] == "not_compared"

    real_metadata = package._package_metadata
    monkeypatch.setattr(
        package,
        "_package_metadata",
        lambda path: (
            {
                **real_metadata(path),
                "safe_consolidation_sha256": "0" * 64,
            }
            if path == returned
            else real_metadata(path)
        ),
    )
    result = package.preserve_client_responses(returned, tmp_path / "metadata-diff.json", issued)
    assert result["lineage_status"] == "metadata_changed"

    def missing_returned_metadata(path):
        if path == returned:
            raise ValueError("workbook is missing valid immutable package lineage")
        return real_metadata(path)

    monkeypatch.setattr(package, "_package_metadata", missing_returned_metadata)
    result = package.preserve_client_responses(
        returned, tmp_path / "returned-metadata-missing.json", issued
    )
    assert result["lineage_status"] == "metadata_missing_or_invalid"

    with pytest.raises(FileExistsError, match="preserved responses"):
        package.preserve_client_responses(returned, tmp_path / "metadata-diff.json", issued)


def test_cli_preserve_responses_path(tmp_path, monkeypatch):
    output = tmp_path / "preserved.json"
    monkeypatch.setattr(
        package,
        "preserve_client_responses",
        lambda workbook, out, issued: out.write_text(
            json.dumps({"workbook": str(workbook), "issued": str(issued)})
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_package.py",
            "preserve-responses",
            "returned.xlsx",
            "--issued-workbook",
            "issued.xlsx",
            "--out",
            str(output),
        ],
    )
    assert package.main() == 0
    assert output.exists()


def test_import_rejects_missing_or_changed_package_lineage(tmp_path, monkeypatch):
    output = package.write_client_review_package(payload(), tmp_path / "lineage")
    issued = output / "client_review_package.xlsx"
    returned = tmp_path / "returned.xlsx"
    shutil.copyfile(issued, returned)
    rows = package._xlsx_cell_values(issued, "Decisions Needed")
    with monkeypatch.context() as context:
        context.setattr(package, "_xlsx_cell_values", lambda *_args: [])
        with pytest.raises(ValueError, match="immutable package lineage"):
            package._package_metadata(issued)
    changed = [list(row) for row in rows]
    metadata_row = next(
        row for row in changed if len(row) >= 16 and row[14] == "safe_consolidation_sha256"
    )
    metadata_row[15] = "0" * 64
    monkeypatch.setattr(
        package,
        "_xlsx_cell_values",
        lambda path, _sheet: changed if path == returned else rows,
    )
    with pytest.raises(ValueError, match="changed immutable package lineage"):
        package.import_client_decisions(returned, tmp_path / "changed-lineage.json", issued)


def test_workbook_archive_preflight_bounds_untrusted_xlsx(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="does not exist"):
        package._validate_workbook_archive(tmp_path / "missing.xlsx")
    invalid = tmp_path / "invalid.xlsx"
    invalid.write_bytes(b"not a zip")
    with pytest.raises(ValueError, match="valid XLSX"):
        package._validate_workbook_archive(invalid)
    incomplete = tmp_path / "incomplete.xlsx"
    with zipfile.ZipFile(incomplete, "w") as archive:
        archive.writestr("only.txt", "x")
    with pytest.raises(ValueError, match="required XLSX"):
        package._validate_workbook_archive(incomplete)

    issued_dir = package.write_client_review_package(payload(), tmp_path / "archive_limits")
    issued = issued_dir / "client_review_package.xlsx"
    limits = (
        ("MAX_WORKBOOK_BYTES", 0, "compressed-size"),
        ("MAX_XLSX_MEMBERS", 0, "too many ZIP"),
        ("MAX_XLSX_MEMBER_BYTES", 0, "oversized uncompressed"),
        ("MAX_XLSX_UNCOMPRESSED_BYTES", 0, "total uncompressed"),
    )
    for name, value, message in limits:
        with monkeypatch.context() as context:
            context.setattr(package, name, value)
            with pytest.raises(ValueError, match=message):
                package._validate_workbook_archive(issued)


def test_import_rejects_deleted_rows_fixed_cell_edits_and_overwrite(tmp_path, monkeypatch):
    output = package.write_client_review_package(payload(), tmp_path / "client_review_package")
    issued = output / "client_review_package.xlsx"
    returned = tmp_path / "returned.xlsx"
    shutil.copyfile(issued, returned)
    issued_rows = package._xlsx_cell_values(issued, "Decisions Needed")
    header_index = next(index for index, row in enumerate(issued_rows) if row and row[0] == "Group")

    monkeypatch.setattr(
        package,
        "_xlsx_cell_values",
        lambda path, _sheet: issued_rows[:-1] if path == returned else issued_rows,
    )
    with pytest.raises(ValueError, match="Group rows differ"):
        package.import_client_decisions(returned, tmp_path / "deleted.json", issued)

    changed = [list(row) for row in issued_rows]
    changed[header_index + 1][1] = "Changed document type"
    monkeypatch.setattr(
        package,
        "_xlsx_cell_values",
        lambda path, _sheet: changed if path == returned else issued_rows,
    )
    with pytest.raises(ValueError, match="changed fixed cells"):
        package.import_client_decisions(returned, tmp_path / "changed.json", issued)

    result_path = tmp_path / "exists.json"
    result_path.write_text("retained")
    with pytest.raises(FileExistsError, match="overwrite"):
        package.import_client_decisions(returned, result_path, issued)


def test_package_generation_is_byte_reproducible(tmp_path):
    first = package.write_client_review_package(payload(), tmp_path / "first")
    second = package.write_client_review_package(payload(), tmp_path / "second")
    for name in ("client_review_package.xlsx", "client_review_guide.docx", "decision_pages.docx"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    assert (tmp_path / "first.zip").read_bytes() == (tmp_path / "second.zip").read_bytes()
