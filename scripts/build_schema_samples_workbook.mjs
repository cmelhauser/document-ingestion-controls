import fs from "node:fs/promises";

if (process.argv.includes("--help")) {
  console.log("Usage: node scripts/build_schema_samples_workbook.mjs [examples-directory] [output-xlsx]");
  process.exit(0);
}

const { SpreadsheetFile, Workbook } = await import("@oai/artifact-tool");

const examplesDirectory = process.argv[2] || "examples";
const outputPath = process.argv[3] || `${examplesDirectory}/business_document_schema_samples.xlsx`;
const extracted = JSON.parse(
  await fs.readFile(`${examplesDirectory}/extracted_record_sample.json`, "utf8"),
);
const derived = JSON.parse(
  await fs.readFile(`${examplesDirectory}/derived_control_sample.json`, "utf8"),
);
const adjudication = JSON.parse(
  await fs.readFile(`${examplesDirectory}/llm_adjudication_sample.json`, "utf8"),
);
const crm = JSON.parse(
  await fs.readFile(`${examplesDirectory}/crm_api_mcp_summary_sample.json`, "utf8"),
);

const navy = "#1F4E78";
const blue = "#4472C4";
const paleBlue = "#D9EAF7";
const paleYellow = "#FFF2CC";
const gray = "#666666";
const border = { preset: "inside", style: "thin", color: "#D9E2F3" };
const titleStyle = { fill: navy, font: { bold: true, color: "#FFFFFF", size: 16 } };
const headerStyle = {
  fill: blue,
  font: { bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
const noteStyle = { fill: paleYellow, font: { bold: true, color: gray }, wrapText: true };

function cellValue(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  // A zero-width prefix keeps Excel-compatible renderers from turning ISO text
  // examples into serial numbers while leaving the displayed sample unchanged.
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}(?:T|$)/.test(value)) return `\u200B${value}`;
  return value;
}

function flattenExtracted(record) {
  const rows = [];
  const provenance = [
    "document_id", "page_id", "source_file", "source_page_range", "document_type", "engine",
    "engine_version", "run_timestamp", "branch", "has_handwriting", "jbig2_suspect",
  ];
  for (const field of provenance) {
    rows.push(["record", field, cellValue(record[field]), typeof record[field], "", ""]);
  }
  for (const [field, entry] of Object.entries(record.header || {})) {
    rows.push(["header", `header.${field}`, cellValue(entry.value), typeof entry.value, entry.confidence ?? "", entry.source ?? ""]);
  }
  for (const [index, line] of (record.lines || []).entries()) {
    for (const [field, entry] of Object.entries(line)) {
      if (field === "line_number") {
        rows.push(["line", `lines[${index}].line_number`, entry, typeof entry, "", ""]);
      } else {
        rows.push(["line", `lines[${index}].${field}`, cellValue(entry.value), typeof entry.value, entry.confidence ?? "", entry.source ?? ""]);
      }
    }
  }
  return rows;
}

function flattenDerived(value, path = "", rows = []) {
  if (value === null || typeof value !== "object") {
    rows.push([path, cellValue(value), typeof value]);
    return rows;
  }
  if (Array.isArray(value)) {
    rows.push([path, JSON.stringify(value), "array"]);
    return rows;
  }
  for (const [key, child] of Object.entries(value)) {
    flattenDerived(child, path ? `${path}.${key}` : key, rows);
  }
  return rows;
}

const extractedRows = flattenExtracted(extracted.records[0]);
const derivedRows = flattenDerived(derived.control_results);
const adjudicationRows = flattenDerived({candidate: adjudication.candidate, result: adjudication.result});
const workbook = Workbook.create();
const readMe = workbook.worksheets.add("Read Me");
const extractedSheet = workbook.worksheets.add("Extracted Sample");
const derivedSheet = workbook.worksheets.add("Derived Controls");
const adjudicationSheet = workbook.worksheets.add("LLM Adjudication");
const schemaIndex = workbook.worksheets.add("Schema Index");
const crmExport = workbook.worksheets.add("CRM Export");
const crmWrites = workbook.worksheets.add("CRM Writes");
const apiMcp = workbook.worksheets.add("API and MCP");
const connectorAccess = workbook.worksheets.add("Connector Access");
const standardReports = workbook.worksheets.add("Standard Reports");
const queryExamples = workbook.worksheets.add("Query Examples");
const visualIntake = workbook.worksheets.add("Visual Intake");

for (const sheet of [readMe, extractedSheet, derivedSheet, adjudicationSheet, schemaIndex, crmExport, crmWrites, apiMcp, connectorAccess, standardReports, queryExamples, visualIntake]) {
  sheet.showGridLines = false;
  sheet.getRange("A1:F1").format.rowHeight = 28;
  if (sheet !== readMe) sheet.getRange("A2:F2").format.rowHeight = 44;
}

readMe.mergeCells("A1:D1");
readMe.getRange("A1").values = [["SAMPLE - FICTIONAL - NOT CLIENT DATA"]];
readMe.getRange("A1:D1").format = titleStyle;
readMe.getRange("A1:D1").format.rowHeight = 28;
readMe.mergeCells("A3:D4");
readMe.getRange("A3").values = [["This workbook demonstrates field shapes only. It contains invented values, no client evidence, no production output, and no accuracy or calibration result."]];
readMe.getRange("A3:D4").format = noteStyle;
readMe.getRange("A3:D4").format.rowHeight = 38;
readMe.getRange("A6:B18").values = [
  ["Item", "Where to find it"],
  ["Extracted source fields", "Extracted Sample; authoritative list: references/extraction-schema.md"],
  ["Derived and control fields", "Derived Controls; authoritative list: references/derived-field-schema.md"],
  ["LLM adjudication sample", "LLM Adjudication; audit-only proposal, never client approval"],
  ["CRM export contract", "CRM Export; 28 ordered canonical tables, package contents, and reconciliation controls"],
  ["CRM write readiness", "CRM Writes; 12 common import files, tenant mapping, sandbox, authorization, reconciliation, and rollback gates"],
  ["API and MCP surface", "API and MCP; shared approved-fact capabilities, exact route/tool mappings, and downloadable remote exports"],
  ["Client connector access", "Connector Access; local and remote options for Claude, ChatGPT, and API consumers"],
  ["Standard report examples", "Standard Reports; deterministic report catalog with API and MCP call examples"],
  ["Common query recipes", "Query Examples; record, account card, address/contact, sales slicing, aging, and export examples"],
  ["Optional image proposals", "Visual Intake; seven MCP/API operations, original-image retention, source-only handoff, limits, and unimplemented writes"],
  ["Operational final review", "references/artifact-contracts.md; it remains a separate eight-field queue"],
  ["Update rule", "Change schemas first, update these samples, rebuild this workbook, then run the quality gate."],
];
readMe.getRange("A6:B6").format = headerStyle;
readMe.getRange("A6:B18").format.borders = border;
readMe.getRange("A:A").format.columnWidth = 28;
readMe.getRange("B:B").format.columnWidth = 88;
readMe.getRange("B7:B18").format.wrapText = true;
readMe.tables.add("A6:B18", true, "SampleGuideTable");

extractedSheet.mergeCells("A1:F1");
extractedSheet.getRange("A1").values = [["SAMPLE - FICTIONAL - NOT CLIENT DATA: Extracted Record"]];
extractedSheet.getRange("A1:F1").format = titleStyle;
extractedSheet.mergeCells("A2:F2");
extractedSheet.getRange("A2").values = [["Evidence-bearing values are shown with their confidence and source. Every entry is invented and illustrates structure only."]];
extractedSheet.getRange("A2:F2").format = noteStyle;
const extractedHeader = [["Group", "JSON path", "Sample value", "Value type", "Confidence", "Source"]];
extractedSheet.getRange(`C5:C${4 + extractedRows.length}`).format.numberFormat = "@";
extractedSheet.getRange(`A4:F${4 + extractedRows.length}`).values = [...extractedHeader, ...extractedRows];
extractedSheet.getRange("A4:F4").format = headerStyle;
extractedSheet.getRange(`A4:F${4 + extractedRows.length}`).format.borders = border;
extractedSheet.getRange(`A4:F${4 + extractedRows.length}`).format.wrapText = true;
extractedSheet.tables.add(`A4:F${4 + extractedRows.length}`, true, "ExtractedSampleTable");
extractedSheet.freezePanes.freezeRows(4);
extractedSheet.getRange("A:A").format.columnWidth = 16;
extractedSheet.getRange("B:B").format.columnWidth = 34;
extractedSheet.getRange("C:C").format.columnWidth = 42;
extractedSheet.getRange("D:F").format.columnWidth = 16;

const derivedHeader = [["JSON path", "Sample value", "Value type"]];
derivedSheet.mergeCells("A1:C1");
derivedSheet.getRange("A1").values = [["SAMPLE - FICTIONAL - NOT CLIENT DATA: Derived Controls"]];
derivedSheet.getRange("A1:C1").format = titleStyle;
derivedSheet.mergeCells("A2:C2");
derivedSheet.getRange("A2").values = [["These controls are computed by pipeline stages; they never overwrite extracted source evidence."]];
derivedSheet.getRange("A2:C2").format = noteStyle;
derivedSheet.getRange(`A4:C${4 + derivedRows.length}`).values = [...derivedHeader, ...derivedRows];
derivedSheet.getRange("A4:C4").format = headerStyle;
derivedSheet.getRange(`A4:C${4 + derivedRows.length}`).format.borders = border;
derivedSheet.getRange(`A4:C${4 + derivedRows.length}`).format.wrapText = true;
derivedSheet.tables.add(`A4:C${4 + derivedRows.length}`, true, "DerivedControlsTable");
derivedSheet.freezePanes.freezeRows(4);
derivedSheet.getRange("A:A").format.columnWidth = 54;
derivedSheet.getRange("B:B").format.columnWidth = 64;
derivedSheet.getRange("C:C").format.columnWidth = 16;

const adjudicationHeader = [["JSON path", "Sample value", "Value type"]];
adjudicationSheet.mergeCells("A1:C1");
adjudicationSheet.getRange("A1").values = [["SAMPLE - FICTIONAL - NOT CLIENT DATA: LLM Adjudication"]];
adjudicationSheet.getRange("A1:C1").format = titleStyle;
adjudicationSheet.mergeCells("A2:C2");
adjudicationSheet.getRange("A2").values = [["This audit-only sample illustrates a model proposal that still requires final client review; it is never client-approved data."]];
adjudicationSheet.getRange("A2:C2").format = noteStyle;
adjudicationSheet.getRange(`A4:C${4 + adjudicationRows.length}`).values = [...adjudicationHeader, ...adjudicationRows];
adjudicationSheet.getRange("A4:C4").format = headerStyle;
adjudicationSheet.getRange(`A4:C${4 + adjudicationRows.length}`).format.borders = border;
adjudicationSheet.getRange(`A4:C${4 + adjudicationRows.length}`).format.wrapText = true;
adjudicationSheet.tables.add(`A4:C${4 + adjudicationRows.length}`, true, "LlmAdjudicationSampleTable");
adjudicationSheet.freezePanes.freezeRows(4);
adjudicationSheet.getRange("A:A").format.columnWidth = 52;
adjudicationSheet.getRange("B:B").format.columnWidth = 64;
adjudicationSheet.getRange("C:C").format.columnWidth = 16;

const schemaRows = [
  ["Schema", "Scope", "Source", "Sample workbook sheet"],
  ["Extraction", "Universal fields and type-specific headers, lines, and accessorials", "references/extraction-schema.md", "Extracted Sample"],
  ["Derived/control", "Consensus, arithmetic proof, entity resolution, attribution, sampling, and review controls", "references/derived-field-schema.md", "Derived Controls"],
  ["LLM adjudication", "Explicit candidate and audit-only amendment proposal", "references/artifact-contracts.md", "LLM Adjudication"],
  ["Canonical CRM", "Approved canonical tables, source provenance, idempotency, staging, and reconciliation", "references/canonical-deployment-retrieval.md", "CRM Export"],
  ["CRM writes", "No-send common-object import files, target mapping, sandbox proof, authorization, reconciliation, and rollback", "references/crm-write-readiness.md", "CRM Writes"],
  ["CRM API/MCP", "Read-only approved-fact discovery, schema, table export, and report functions", "references/canonical-deployment-retrieval.md", "API and MCP"],
  ["Connector access", "Local stdio and OAuth-protected remote Streamable HTTP deployment choices", "references/mcp-production-integration.md", "Connector Access"],
  ["Standard reports", "Seven deterministic operational and financial report shapes", "references/canonical-deployment-retrieval.md", "Standard Reports"],
  ["Query recipes", "Cross-table search, exact lookup, account cards, governed filtering, and multidimensional sales analysis", "references/canonical-deployment-retrieval.md", "Query Examples"],
  ["Visual intake", "Optional schema-guided image proposals and verified source-only export; never canonical approval", "references/visual-ingestion.md", "Visual Intake"],
  ["Final review", "Eight reviewer-queue fields only", "references/artifact-contracts.md", "Not represented as a production queue"],
];
schemaIndex.mergeCells("A1:D1");
schemaIndex.getRange("A1").values = [["SAMPLE - FICTIONAL - NOT CLIENT DATA: Schema Index"]];
schemaIndex.getRange("A1:D1").format = titleStyle;
schemaIndex.getRange("A3:D14").values = schemaRows;
schemaIndex.getRange("A3:D3").format = headerStyle;
schemaIndex.getRange("A3:D14").format.borders = border;
schemaIndex.getRange("A:A").format.columnWidth = 20;
schemaIndex.getRange("B:B").format.columnWidth = 66;
schemaIndex.getRange("C:C").format.columnWidth = 46;
schemaIndex.getRange("D:D").format.columnWidth = 42;
schemaIndex.getRange("B4:D14").format.wrapText = true;
schemaIndex.tables.add("A3:D14", true, "SchemaIndexTable");

const exportBoundary = crm.export_boundary;
crmExport.mergeCells("A1:E1");
crmExport.getRange("A1").values = [["SAMPLE - FICTIONAL - NOT CLIENT DATA: CRM Export"]];
crmExport.getRange("A1:E1").format = titleStyle;
crmExport.mergeCells("A2:E2");
crmExport.getRange("A2").values = [["The canonical JSON is authoritative. Each staged CSV row also carries authoritative canonical JSON and a row checksum; exact load topology is verified before atomic, no-send publication for a separately authorized CRM receiver."]];
crmExport.getRange("A2:E2").format = noteStyle;
crmExport.getRange("A4:B9").values = [
  ["Artifact", "Role"],
  [exportBoundary.canonical_source, "Approved canonical source envelope"],
  [exportBoundary.ordered_plan, "Strict ordered load plan with per-table checksums and idempotency keys"],
  [exportBoundary.offline_package, exportBoundary.mode],
  [exportBoundary.manifest, "Package inventory, hashes, counts, and atomic verification status"],
  [exportBoundary.api_envelope, "Receiver endpoint, rejection, rollback, decoding, and reconciliation contract"],
];
crmExport.getRange("A4:B4").format = headerStyle;
crmExport.getRange("A4:B9").format.borders = border;
crmExport.getRange("A4:B9").format.wrapText = true;
crmExport.tables.add("A4:B9", true, "CrmExportArtifactsTable");
const controlRows = exportBoundary.controls.map((control, index) => [index + 1, control]);
crmExport.getRange(`D4:E${4 + controlRows.length}`).values = [["#", "Required export control"], ...controlRows];
crmExport.getRange("D4:E4").format = headerStyle;
crmExport.getRange(`D4:E${4 + controlRows.length}`).format.borders = border;
crmExport.getRange(`E5:E${4 + controlRows.length}`).format.wrapText = true;
crmExport.tables.add(`D4:E${4 + controlRows.length}`, true, "CrmExportControlsTable");
const tableRows = crm.canonical_tables.map((item) => [item.step, item.table, item.idempotency_keys.join(" + "), item.purpose]);
const canonicalHeaderRow = Math.max(13, 6 + controlRows.length);
const canonicalLastRow = canonicalHeaderRow + tableRows.length;
crmExport.getRange(`A${canonicalHeaderRow}:D${canonicalLastRow}`).values = [["Load step", "Canonical table", "Idempotency key(s)", "Business purpose"], ...tableRows];
crmExport.getRange(`A${canonicalHeaderRow}:D${canonicalHeaderRow}`).format = headerStyle;
crmExport.getRange(`A${canonicalHeaderRow}:D${canonicalLastRow}`).format.borders = border;
crmExport.getRange(`B${canonicalHeaderRow + 1}:D${canonicalLastRow}`).format.wrapText = true;
crmExport.tables.add(`A${canonicalHeaderRow}:D${canonicalLastRow}`, true, "CanonicalCrmTablesTable");
crmExport.freezePanes.freezeRows(canonicalHeaderRow);
crmExport.getRange("A:A").format.columnWidth = 24;
crmExport.getRange("B:B").format.columnWidth = 30;
crmExport.getRange("C:C").format.columnWidth = 34;
crmExport.getRange("D:D").format.columnWidth = 46;
crmExport.getRange("E:E").format.columnWidth = 54;

const commonImport = crm.common_crm_import;
const importRows = commonImport.files.map((item) => [item.order, item.object, item.canonical_source, item.relationship_key]);
crmWrites.mergeCells("A1:E1");
crmWrites.getRange("A1").values = [["SAMPLE - FICTIONAL - NOT CLIENT DATA: CRM Write Readiness"]];
crmWrites.getRange("A1:E1").format = titleStyle;
crmWrites.mergeCells("A2:E2");
crmWrites.getRange("A2").values = [["This is a verified no-send preparation layer. Vendor suggestions are proposals until checked against the actual tenant metadata; this workbook and package never grant live CRM write authority."]];
crmWrites.getRange("A2:E2").format = noteStyle;
crmWrites.getRange("A4:B7").values = [
  ["Artifact", "Purpose"],
  [commonImport.manifest, "Binds source/load/package hashes, every file, complete canonical coverage, and live_write_permitted=false"],
  ["field_mapping_template.csv", "Target owner completes object, field, type, required, key, relationship, owner, currency, and enumeration mappings"],
  ["crm_write_plan.json", "Discovery, mapping approval, sandbox, separately authorized upsert, reconciliation, and acceptance/rollback sequence"],
];
crmWrites.getRange("A4:B4").format = headerStyle;
crmWrites.getRange("A4:B7").format.borders = border;
crmWrites.getRange("A4:B7").format.wrapText = true;
crmWrites.tables.add("A4:B7", true, "CrmWriteArtifactsTable");
const gateRows = commonImport.gates.map((gate, index) => [index + 1, gate]);
crmWrites.getRange(`D4:E${4 + gateRows.length}`).values = [["#", "Required gate"], ...gateRows];
crmWrites.getRange("D4:E4").format = headerStyle;
crmWrites.getRange(`D4:E${4 + gateRows.length}`).format.borders = border;
crmWrites.getRange(`E5:E${4 + gateRows.length}`).format.wrapText = true;
crmWrites.tables.add(`D4:E${4 + gateRows.length}`, true, "CrmWriteGatesTable");
crmWrites.getRange(`A13:D${13 + importRows.length}`).values = [["Order", "Common object file", "Canonical source", "Relationship/external key"], ...importRows];
crmWrites.getRange("A13:D13").format = headerStyle;
crmWrites.getRange(`A13:D${13 + importRows.length}`).format.borders = border;
crmWrites.getRange(`B14:D${13 + importRows.length}`).format.wrapText = true;
crmWrites.tables.add(`A13:D${13 + importRows.length}`, true, "CommonCrmImportFilesTable");
const vendorRows = commonImport.vendor_profiles.map((item) => [item.vendor, item.status, item.source]);
crmWrites.getRange(`A28:C${28 + vendorRows.length}`).values = [["Vendor", "Preset status", "Official source"], ...vendorRows];
crmWrites.getRange("A28:C28").format = headerStyle;
crmWrites.getRange(`A28:C${28 + vendorRows.length}`).format.borders = border;
crmWrites.getRange(`B29:C${28 + vendorRows.length}`).format.wrapText = true;
crmWrites.tables.add(`A28:C${28 + vendorRows.length}`, true, "CrmVendorSourcesTable");
crmWrites.freezePanes.freezeRows(13);
crmWrites.getRange("A:A").format.columnWidth = 30;
crmWrites.getRange("B:B").format.columnWidth = 62;
crmWrites.getRange("C:C").format.columnWidth = 72;
crmWrites.getRange("D:D").format.columnWidth = 30;
crmWrites.getRange("E:E").format.columnWidth = 72;

const capabilityRows = crm.api_mcp_capabilities.map((item) => [item.function, item.api, item.mcp, item.result, item.mcp === "create_crm_export" ? "Additive, non-idempotent report artifact; canonical facts unchanged; no CRM upload" : "Read-only; approved canonical facts only; no CRM upload"]);
apiMcp.mergeCells("A1:E1");
apiMcp.getRange("A1").values = [["SAMPLE - FICTIONAL - NOT CLIENT DATA: API and MCP"]];
apiMcp.getRange("A1:E1").format = titleStyle;
apiMcp.mergeCells("A2:E2");
apiMcp.getRange("A2").values = [["All query tools call the same integrity-checked service layer. Local MCP uses stdio; remote MCP uses OAuth-protected Streamable HTTP and adds owner-bound, expiring CSV/XLSX export jobs. The TLS/bearer REST API remains GET-only and is not an MCP endpoint."]];
apiMcp.getRange("A2:E2").format = noteStyle;
apiMcp.getRange("A2").values = [["Approved-data tools share one integrity-checked service. Remote MCP adds owner-bound expiring CSV/XLSX jobs. TLS/bearer REST stays GET-only. Optional source/proposal writes are separate: see Visual Intake."]];
apiMcp.getRange(`A4:E${4 + capabilityRows.length}`).values = [["Function", "HTTPS API", "MCP tool", "Returns", "Safety boundary"], ...capabilityRows];
apiMcp.getRange("A4:E4").format = headerStyle;
apiMcp.getRange(`A4:E${4 + capabilityRows.length}`).format.borders = border;
apiMcp.getRange(`A4:E${4 + capabilityRows.length}`).format.wrapText = true;
apiMcp.tables.add(`A4:E${4 + capabilityRows.length}`, true, "ApiMcpCapabilitiesTable");
apiMcp.freezePanes.freezeRows(4);
apiMcp.getRange("A:A").format.columnWidth = 24;
apiMcp.getRange("B:B").format.columnWidth = 56;
apiMcp.getRange("C:C").format.columnWidth = 34;
apiMcp.getRange("D:D").format.columnWidth = 54;
apiMcp.getRange("E:E").format.columnWidth = 42;

const accessRows = crm.client_access.map((item) => [item.client, item.transport, item.configuration, item.availability, item.security]);
connectorAccess.mergeCells("A1:E1");
connectorAccess.getRange("A1").values = [["SAMPLE - FICTIONAL - NOT CLIENT DATA: Connector Access"]];
connectorAccess.getRange("A1:E1").format = titleStyle;
connectorAccess.mergeCells("A2:E2");
connectorAccess.getRange("A2").values = [["A remote client cannot reach the local stdio process. Mobile and web access require the implemented remote server to be separately deployed on public HTTPS and connected to a client-approved OAuth authorization server. One deployment is bound to one tenant and immutable approved snapshot."]];
connectorAccess.getRange("A2:E2").format = noteStyle;
connectorAccess.getRange(`A4:E${4 + accessRows.length}`).values = [["Client", "Transport", "Configuration", "Repository readiness", "Security boundary"], ...accessRows];
connectorAccess.getRange("A4:E4").format = headerStyle;
connectorAccess.getRange(`A4:E${4 + accessRows.length}`).format.borders = border;
connectorAccess.getRange(`A4:E${4 + accessRows.length}`).format.wrapText = true;
connectorAccess.tables.add(`A4:E${4 + accessRows.length}`, true, "ConnectorAccessTable");
connectorAccess.freezePanes.freezeRows(4);
connectorAccess.getRange("A:A").format.columnWidth = 30;
connectorAccess.getRange("B:B").format.columnWidth = 42;
connectorAccess.getRange("C:C").format.columnWidth = 62;
connectorAccess.getRange("D:D").format.columnWidth = 64;
connectorAccess.getRange("E:E").format.columnWidth = 58;

const reportRows = crm.standard_reports.map((item) => [item.report, item.purpose, item.parameters, item.api_example, item.mcp_example, "Approved facts + source document IDs; deterministic report SHA-256"]);
standardReports.mergeCells("A1:F1");
standardReports.getRange("A1").values = [["SAMPLE - FICTIONAL - NOT CLIENT DATA: Standard CRM Reports"]];
standardReports.getRange("A1:F1").format = titleStyle;
standardReports.mergeCells("A2:F2");
standardReports.getRange("A2").values = [["These are deterministic reports, not model-generated answers. Amounts are never summed across currencies; aging buckets current approved balances against an ISO 8601 as_of date and excludes later invoices, but does not reconstruct historical balances."]];
standardReports.getRange("A2:F2").format = noteStyle;
standardReports.getRange(`A4:F${4 + reportRows.length}`).values = [["Report", "Purpose", "Parameters", "API example", "MCP arguments example", "Audit output"], ...reportRows];
standardReports.getRange("A4:F4").format = headerStyle;
standardReports.getRange(`A4:F${4 + reportRows.length}`).format.borders = border;
standardReports.getRange(`A4:F${4 + reportRows.length}`).format.wrapText = true;
standardReports.tables.add(`A4:F${4 + reportRows.length}`, true, "StandardCrmReportsTable");
standardReports.freezePanes.freezeRows(4);
standardReports.getRange("A:A").format.columnWidth = 30;
standardReports.getRange("B:B").format.columnWidth = 48;
standardReports.getRange("C:C").format.columnWidth = 32;
standardReports.getRange("D:D").format.columnWidth = 74;
standardReports.getRange("E:E").format.columnWidth = 82;
standardReports.getRange("F:F").format.columnWidth = 52;

const queryRows = crm.query_examples.map((item) => [item.client_question, item.recommended_function, item.example, item.why]);
queryExamples.mergeCells("A1:D1");
queryExamples.getRange("A1").values = [["SAMPLE - FICTIONAL - NOT CLIENT DATA: Query Recipes"]];
queryExamples.getRange("A1:D1").format = titleStyle;
queryExamples.mergeCells("A2:D2");
queryExamples.getRange("A2").values = [["Start with indexed search when the key is unknown; use exact record/account-card lookup when known; financial slices add a currency partition automatically so unlike currencies are never combined."]];
queryExamples.getRange("A2:D2").format = noteStyle;
queryExamples.getRange(`A4:D${4 + queryRows.length}`).values = [["Client question", "Recommended function", "Example MCP arguments", "Why this route"], ...queryRows];
queryExamples.getRange("A4:D4").format = headerStyle;
queryExamples.getRange(`A4:D${4 + queryRows.length}`).format.borders = border;
queryExamples.getRange(`A4:D${4 + queryRows.length}`).format.wrapText = true;
queryExamples.tables.add(`A4:D${4 + queryRows.length}`, true, "CrmQueryExamplesTable");
queryExamples.freezePanes.freezeRows(4);
queryExamples.getRange("A:A").format.columnWidth = 54;
queryExamples.getRange("B:B").format.columnWidth = 30;
queryExamples.getRange("C:C").format.columnWidth = 92;
queryExamples.getRange("D:D").format.columnWidth = 62;

visualIntake.mergeCells("A1:C1");
visualIntake.getRange("A1").values = [["SAMPLE - FICTIONAL - NOT CLIENT DATA: Visual Intake"]];
visualIntake.getRange("A1:C1").format = titleStyle;
visualIntake.mergeCells("A2:C2");
visualIntake.getRange("A2").values = [["Separate, optional proposal journal and source archive. No original or candidate becomes an approved CRM fact through intake. Existing reporting, canonical exports and CRM-import files remain approved-only."]];
visualIntake.getRange("A2:C2").format = noteStyle;
const intakeEnd = 4 + crm.visual_intake.length;
visualIntake.getRange(`A4:C${intakeEnd}`).values = [["Capability / decision", "Implemented behavior and limits", "Authority / required scope"], ...crm.visual_intake];
visualIntake.getRange("A4:C4").format = headerStyle;
visualIntake.getRange(`A4:C${intakeEnd}`).format.borders = border;
visualIntake.getRange(`A4:C${intakeEnd}`).format.wrapText = true;
visualIntake.getRange(`A5:C${intakeEnd}`).format.rowHeight = 54;
visualIntake.getRange("A:A").format.columnWidth = 26;
visualIntake.getRange("B:B").format.columnWidth = 86;
visualIntake.getRange("C:C").format.columnWidth = 58;
visualIntake.tables.add(`A4:C${intakeEnd}`, true, "VisualIntakeGuideTable");
visualIntake.freezePanes.freezeRows(4);

for (const [sheetName, range, label] of [
  ["Read Me", "A1:D18", "schema-samples-readme"],
  ["Extracted Sample", "A1:F14", "schema-samples-extracted"],
  ["Derived Controls", "A1:C14", "schema-samples-derived"],
  ["LLM Adjudication", "A1:C18", "schema-samples-llm-adjudication"],
  ["Schema Index", "A1:D14", "schema-samples-index"],
  ["CRM Export", `A1:E${canonicalLastRow}`, "schema-samples-crm-export"],
  ["CRM Writes", `A1:E${28 + vendorRows.length}`, "schema-samples-crm-writes"],
  ["API and MCP", `A1:E${4 + capabilityRows.length}`, "schema-samples-api-mcp"],
  ["Connector Access", `A1:E${4 + accessRows.length}`, "schema-samples-connector-access"],
  ["Standard Reports", "A1:F11", "schema-samples-standard-reports"],
  ["Query Examples", `A1:D${4 + queryRows.length}`, "schema-samples-query-examples"],
  ["Visual Intake", `A1:C${intakeEnd}`, "schema-samples-visual-intake"],
]) {
  const preview = await workbook.render({sheetName, range, scale: 1.25, format: "png"});
  await fs.writeFile(`/private/tmp/${label}.png`, new Uint8Array(await preview.arrayBuffer()));
}
const inspection = await workbook.inspect({
  kind: "sheet,table",
  maxChars: 6000,
  tableMaxRows: 3,
  tableMaxCols: 6,
});
console.log(inspection.ndjson);
const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: {useRegex: true, maxResults: 100},
  summary: "final formula error scan",
});
console.log(formulaErrors.ndjson);
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

await fs.unlink(`${outputPath}.inspect.ndjson`).catch(() => {});
