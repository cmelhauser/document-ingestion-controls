"""Contract tests for clearly labelled fictional schema samples."""

import json
import re
import zipfile
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
NOTICE = "SAMPLE - FICTIONAL - NOT CLIENT DATA"


def load_sample(name):
    return json.loads((EXAMPLES / name).read_text())


def test_samples_are_clearly_labelled_and_linked():
    extracted = load_sample("extracted_record_sample.json")
    derived = load_sample("derived_control_sample.json")
    adjudication = load_sample("llm_adjudication_sample.json")
    canonical = load_sample("canonical_export_sample.json")
    crm = load_sample("crm_api_mcp_summary_sample.json")

    assert extracted["sample_metadata"]["label"] == NOTICE
    assert derived["sample_metadata"]["label"] == NOTICE
    assert extracted["sample_metadata"]["authoritative_schema"] == "references/extraction-schema.md"
    assert (
        derived["sample_metadata"]["authoritative_schema"] == "references/derived-field-schema.md"
    )
    assert adjudication["sample_metadata"]["label"] == NOTICE
    assert (
        adjudication["sample_metadata"]["authoritative_schema"]
        == "references/artifact-contracts.md"
    )
    assert extracted["records"][0]["document_id"] == derived["document_id"]
    assert canonical["sample_metadata"]["label"] == NOTICE
    assert (
        canonical["sample_metadata"]["authoritative_schema"]
        == "references/canonical-deployment-retrieval.md"
    )
    assert {
        "selling_location",
        "acknowledgement",
        "job",
        "attribution",
        "handwriting_region",
    } <= set(canonical["tables"])
    assert crm["sample_metadata"]["label"] == NOTICE
    assert len(crm["canonical_tables"]) == 28
    assert len(crm["common_crm_import"]["files"]) == 12
    assert crm["common_crm_import"]["mode"].startswith("no-send")
    assert {item["vendor"] for item in crm["common_crm_import"]["vendor_profiles"]} == {
        "Salesforce",
        "HubSpot",
        "Microsoft Dynamics 365 / Dataverse",
        "Zoho CRM",
    }
    assert {item["mcp"] for item in crm["api_mcp_capabilities"]} >= {
        "search_crm_records",
        "get_crm_record",
        "query_crm_records",
        "get_account_card",
        "analyze_sales",
    }
    assert len(crm["standard_reports"]) == 7 and len(crm["query_examples"]) == 8


def test_extracted_sample_uses_evidence_bearing_fields():
    record = load_sample("extracted_record_sample.json")["records"][0]
    assert record["document_type"] == "commercial_invoice"
    assert {"invoice_number", "seller_name", "buyer_name", "total_amount"} <= set(record["header"])
    assert {"item_code", "description", "quantity", "uom", "unit_price", "extended_amount"} <= set(
        record["lines"][0]
    )
    for entry in (record["header"]["invoice_number"], record["lines"][0]["item_code"]):
        assert {"value", "confidence", "source"} <= set(entry)


def test_derived_sample_separates_control_results_from_source_data():
    controls = load_sample("derived_control_sample.json")["control_results"]
    assert controls["consensus"]["consensus_flag"] == "consensus_2of2"
    assert controls["arithmetic_proof"]["arithmetic_status"] == "proved"
    assert controls["address_normalization"]["address_validation_status"] == "clear"
    normalization = controls["address_normalization"]["address_normalizations"][0]
    assert normalization["derived_fields"]["buyer_city"]["value"] == "Sampletown"
    assert normalization["external_validation_status"] == "validated"
    assert (
        normalization["google_address_validation"]["provider"] == "google_maps_address_validation"
    )
    assert (
        normalization["google_address_validation"]["provider_derived_fields"][
            "buyer_geocode_latitude"
        ]["value"]
        == 40.71
    )
    assert controls["attribution"]["attribution_method"] == "purchase_order_reference"
    assert controls["final_review"]["disposition"] == "not_queued"


def test_llm_adjudication_sample_is_a_review_required_amendment_only():
    sample = load_sample("llm_adjudication_sample.json")
    assert sample["candidate"]["field"] == "seller_name"
    assert sample["result"]["decision_source"] == "llm"
    assert sample["result"]["client_review_required"]
    assert sample["result"]["disposition"] == "llm_generated_amendment_requires_client_review"


def test_schema_workbook_contains_clear_notice_and_views():
    workbook = EXAMPLES / "business_document_schema_samples.xlsx"
    with zipfile.ZipFile(workbook) as archive:
        names = archive.namelist()
        workbook_xml = archive.read("xl/workbook.xml").decode()
        sheet_xmls = [
            archive.read(name).decode()
            for name in names
            if name.startswith("xl/worksheets/") and name.endswith(".xml")
        ]
        strings = "".join(sheet_xmls)
        sheet_text = unescape(strings)
        # Inspect the generated attributes without expanding XML entities.
        tables = {
            re.search(r'\bname="([^"]+)"', table).group(1): re.search(
                r'\bref="([^"]+)"', table
            ).group(1)
            for name in names
            if name.startswith("xl/tables/") and name.endswith(".xml")
            for table in [archive.read(name).decode()]
        }
    assert "xl/workbook.xml" in names
    for sheet in (
        "Read Me",
        "Extracted Sample",
        "Derived Controls",
        "LLM Adjudication",
        "Schema Index",
        "CRM Export",
        "CRM Writes",
        "API and MCP",
        "Connector Access",
        "Standard Reports",
        "Query Examples",
        "Visual Intake",
    ):
        assert sheet in workbook_xml
    assert NOTICE in strings
    assert "header.total_amount" in strings
    assert "consensus_flag" in strings
    assert "LLM Adjudication" in strings
    assert "llm_generated_amendment_proposal" in strings
    assert "analyze_sales" in strings
    assert "receivables_aging" in strings
    assert "Query Recipes" in strings
    assert "get_ingestion_schema" in strings
    assert "submit_record_proposal" in strings
    assert "visual_ingestion_export.py" in strings
    intake_rows = load_sample("crm_api_mcp_summary_sample.json")["visual_intake"]
    assert tables["VisualIntakeGuideTable"] == f"A4:C{4 + len(intake_rows)}"
    for row in intake_rows:
        for value in row:
            assert value in sheet_text
    control_end = tables["CrmExportControlsTable"].split(":")[1]
    canonical_start = tables["CanonicalCrmTablesTable"].split(":")[0]
    # Growing the control list must never overwrite a number with the next header.
    assert int(re.search(r"\d+$", canonical_start).group()) > int(
        re.search(r"\d+$", control_end).group()
    )
