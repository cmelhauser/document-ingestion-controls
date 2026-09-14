# Schema Samples

## SAMPLE - FICTIONAL - NOT CLIENT DATA

Everything in this folder is invented. It demonstrates supported field shapes
only. It is **not** client evidence, a production output, an accuracy claim, a
calibration set, or a client-review decision package.

| File | Purpose | Authority |
|---|---|---|
| `extracted_record_sample.json` | One fictional commercial-invoice record using evidence-bearing `{value, confidence, source}` fields. | `references/extraction-schema.md` |
| `derived_control_sample.json` | Fictional outputs from the control stages, including local and optional provider-backed address-normalization evidence; source evidence is never replaced. | `references/derived-field-schema.md` |
| `llm_adjudication_sample.json` | Fictional explicit candidate plus an audit-only, review-required LLM amendment proposal. | `references/artifact-contracts.md` |
| `schema_discovery_source_template_sample.json` | Fictional source-template packet for proposal-only field, dealer, brand, relationship, and location discovery. | `references/artifact-contracts.md` |
| `schema_discovery_client_decision_sample.json` | Fictional explicit approve/defer decision shape for creating a new registry snapshot; it is not a client decision. | `references/semantic-schema-discovery.md` |
| `crm_api_mcp_summary_sample.json` | Fictional, self-contained CRM export, common-import/write-readiness, API/MCP capability, query-recipe, and standard-report catalog, including currency-safe reports and v3 staging integrity controls, used to build the workbook. | `references/canonical-deployment-retrieval.md` and `references/crm-write-readiness.md` |
| `business_document_schema_samples.xlsx` | Filterable, self-contained schema, CRM export, common-import/write-readiness, API/MCP, local/remote connector-access, query-recipe, and standard-report guide. | JSON samples and the authoritative schema/retrieval references |
| `allocation_legs_sample.json` | Fictional evidence-bearing commission allocation legs before any sales-credit policy. | `references/allocation-policy.md` |
| `allocation_policy_template_sample.json` | Fictional report-layout observation for strict allocation-policy discovery. | `references/allocation-policy.md` |
| `allocation_policy_client_decision_sample.json` | Fictional example of the only client-editable allocation-policy return file. | `references/allocation-policy.md` |

The operational client-review package is intentionally separate. Its canonical
JSON, CSV, XLSX, and HTML outputs contain only unresolved review work using the
eight-field `ReviewItem` contract in `references/artifact-contracts.md`.

## Update rule

The workbook's **Visual Intake** sheet documents the optional seven-operation
MCP/OAuth API, source-image limits, retained proposal receipts, local source-only
export and verification, and the remaining create/update and analytics work.
It is separate from the approved-data **API and MCP**, **CRM Export**, and
**CRM Writes** sheets. A source archive is not a CRM import, and a pending
proposal never contributes to report totals. Use
[`Visual Intake`](../references/visual-ingestion.md) for the full executable
contract, not the fictional extraction sample as an upload payload.

1. Change the authoritative schema reference first.
2. Update the matching fictional JSON sample without using client values.
3. Rebuild the workbook with `node scripts/build_schema_samples_workbook.mjs` in
a workspace that provides `@oai/artifact-tool`.
4. Run the repository quality gate and render the LaTeX documents.

## Canonical deployment and local retrieval sample

`canonical_export_sample.json` and `crm_api_mcp_summary_sample.json` are
**SAMPLE - FICTIONAL - NOT CLIENT DATA**. They show the canonical-export
envelope and the matching no-send CRM export, API/MCP query, account-card,
sales-analysis, and standard-report surfaces. Their authority is
`references/canonical-deployment-retrieval.md`.
