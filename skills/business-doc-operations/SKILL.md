---
name: business-doc-operations
description: Operate a business-document ingestion run end to end — set up the run and its settings, execute every pipeline lane including the independent cross-checking lanes, monitor progress and spend, build and query the evidence graph, issue and re-ingest the client review workbook, generate the final documents and CRM files, and assemble the clean client delivery folder. Use when running the pipeline against real documents rather than changing repository code.
---

# Operating a run

`SKILL.md` at the repository root governs **changing the repository**. This skill
governs **running it**. The two never conflict: every invariant in the root skill
applies here, and this one adds nothing that could weaken them.

If you are modifying code, tests, or contracts, stop and use the root
[`SKILL.md`](../../SKILL.md) instead. For keeping the repository itself healthy —
gates, release checks, branches — use [`tasks/maintenance.md`](tasks/maintenance.md).

## The one rule that decides most questions

**A control that processed nothing has not passed, and a proposal is not a fact.**
Every refusal in this pipeline follows from one of those two. When a command
refuses, it is telling you something about the evidence — not asking to be
overridden.

## Reconcile from what the run already holds before re-reading anything

**A re-extraction is the last option, not the first.** Before sending a single
page back to a provider, establish that the value is genuinely absent from the
run rather than merely absent from the field you looked in. It usually is not.

The run retains every provider response under `RUN/providers/raw/`, and that
includes the independent non-LLM extractor, which reads the **whole page** and
not just the fields the schema named. A column the schema had no home for is
routinely still on disk in full, with layout, long after the pipeline dropped
it.

Work through this before proposing a provider call:

1. **Grep the retained raw responses** for the value or its column heading.
   `RUN/providers/raw/<lane>/*.json` is plain JSON.
2. **Check the independent extractor's layout.** Document AI retains
   `document.text`, `pages[].lines`, `pages[].tables` and token positions for
   every page. A heading the LLM schema could not name is still in there.
3. **Find a join key the retained reading and the records share.** A printed
   purchase-order number, an ACK number, a job number -- something already on
   the extracted line.
4. **Only if all three fail**, re-extract, and then only the affected pages,
   with the amendment recorded *before* the call.

On this corpus that distinction was 145 pages of provider spend against zero: a
SPECIFIER column the schema had no field for was recovered whole from retained
Document AI layout, joined to lines by the printed P.O. number they already
carried. The schema fix tells the *next* run where to put the column; the
recovery gets it for the run already paid for. Do both, in that order.

Two things this does not license. A recovered value is independent-extractor
evidence, not vendor agreement, and must carry the weaker status. And recovery
never invents a reading: where the join is ambiguous or the value absent, the
document keeps an explicit exception instead.

## Full-run lock: follow this before any provider call

For a full run, create `RUN/logs/run_authorization.md` before the first external
provider invocation. Record only non-secret operational scope: run identifier,
source-set identifiers/hashes, timestamp, authorized provider families, and
whether the operator authorized every in-scope source item to be sent. Do not
put credentials or raw client content in this record.

That authorization covers sending the named run's in-scope source data to the
named configured providers. It does **not** authorize skipping pages, ignoring
exceptions, auto-accepting/carrying forward proposals, applying amendments,
clearing controls, canonical deployment, CRM execution, or delivery.

Run phases as a strict state machine. Before a dependent lane, complete its
upstream artifacts, read their exception results, and meet the documented exit
condition. Run only genuinely independent lanes from the same phase in parallel.
Never start a downstream review, canonical, or delivery lane as an unstated
preliminary pass while upstream work remains active. A schema mismatch, refusal,
provider failure, missing prerequisite, or zero-coverage result is a retained
integration finding: repair it or record its explicit blocker, then use the
documented no-clobber recovery path. Never skip inputs, hand-create a
passing-shaped output, or guess a substitute result.

Before starting a recovery extraction invocation, verify that the original
lane's process is terminal and read its handoff/exception artifacts to identify
the exact retained missing items. Do not infer termination from a partial raw
directory. A contained `llm_adapter.py` invocation takes an exclusive per-lane
run lock and refuses a concurrent duplicate before constructing a provider
client; that guard complements, but does not replace, this verification.

At closure, run `run_workspace.py audit` and `run_lane_coverage.py` with
`--require` for every lane selected for this engagement. Reconcile every absent
or blocked lane to retained evidence before building the final queue; include
review-bearing base artifacts, exceptions, and recovery overlays in that queue.

## Pick the task

| You are… | Read |
|---|---|
| Starting a new engagement | [`tasks/setup.md`](tasks/setup.md) |
| Receiving photographed pages or handing a retained intake session into a run | [`../record-intake/SKILL.md`](../record-intake/SKILL.md), then [`tasks/setup.md`](tasks/setup.md) |
| Running the pipeline end to end | [`tasks/run-pipeline.md`](tasks/run-pipeline.md) |
| Working the lanes in order, with every option and trap | [`tasks/lane-checklist.md`](tasks/lane-checklist.md) |
| Reading documents — extraction, tables, layout | [`tasks/extraction.md`](tasks/extraction.md) |
| Corroborating or independently verifying a reading | [`tasks/cross-checking.md`](tasks/cross-checking.md) |
| Working handwriting | [`tasks/handwriting.md`](tasks/handwriting.md) |
| Attribution, allocation, completeness, sampling | [`tasks/business-controls.md`](tasks/business-controls.md) |
| Building or querying the evidence graph | [`tasks/evidence-graph.md`](tasks/evidence-graph.md) |
| Turning any blocked control into a client-answerable pack | [`tasks/client-review-lane.md`](tasks/client-review-lane.md) |
| Reducing the review queue before the client sees it | [`tasks/review-reduction.md`](tasks/review-reduction.md) |
| Issuing or re-ingesting the client workbook | [`tasks/client-review.md`](tasks/client-review.md) |
| Watching a long run | [`tasks/monitor.md`](tasks/monitor.md) |
| Producing the final hand-off | [`tasks/deliver.md`](tasks/deliver.md) |
| Deploying or using the approved-fact MCP/API after Phase 6 | [`../mcp-api-operations/SKILL.md`](../mcp-api-operations/SKILL.md) |
| Wondering why something refused | [`tasks/troubleshooting.md`](tasks/troubleshooting.md) |
| Keeping the repository green | [`tasks/maintenance.md`](tasks/maintenance.md) |
| Deciding whether to delegate work | [`tasks/subagents.md`](tasks/subagents.md) |

## Every lane, by phase

The optional visual-intake journal and `visual_ingestion_export.py` source-only
handoff precede the pipeline and do not add an extraction lane to this table.
They retain unapproved sources and candidates. Use the receipt-verified,
session-named derivative for normal profiling and intake, preserve the full
package, and supply its exceptions to final review. All 76 workflow-lane
commands and their existing prerequisites remain unchanged.

This is the complete operating surface. A lane marked **optional** is not run by
default; a lane marked **disabled** additionally refuses until it is explicitly
enabled. Nothing here can be skipped silently — a phase you did not run is a
stated limitation, not a pass.

| Phase | Lane | Command | Notes |
|---|---|---|---|
| 0.5 | Scan profiling | `scan_profile.py` | Full corpus. Sets the handwriting branch and the rescan decision. |
| 1 | Intake | `ingest_pages.py` | Byte-verified source copy, one immutable master per page. |
| 1 | Reassembly | `reassemble_pages.py` | Grouping and duplicate **proposals**. Never force-fit. |
| 1 | Image variants | `preprocess_pages.py` | Grayscale masters kept; binarized siblings created beside them. |
| 1 | Manifest and state | `operations.py manifest`, `operations.py stage` | Binds every later artifact to a hash. |
| 2 | Template fingerprints | `template_drift.py analyze` | Fails closed on a changed or unknown layout. |
| 2 | Detector thresholds | `detection_calibration.py` | *Optional.* Needs labeled truth. |
| 3 | Extraction lanes | `llm_adapter.py --lane consensus_primary` / `--lane consensus_secondary` | Two genuinely independent providers, separate invocations. |
| 3 | Provider adapters | `openai_adapter.py`, `google_genai_adapter.py`, `openrouter_adapter.py`, `anthropic_adapter.py` | Direct alternatives to `llm_adapter.py`. OpenRouter and Anthropic read scanned pages natively. |
| 3 | Source-native tables | `table_comprehension.py profile\|rows\|audit\|mappings\|assemble` | Preferred for tabular documents. |
| 3 | Corpus tables | `table_comprehension_corpus.py` | Sequential corpus pass with bounded rereads. |
| 3 | Display layout | `layout_aware_extract.py page\|combine`, `layout_dedup.py` | *Optional.* Review-only; never a canonical mapping. |
| 3 | Independent OCR/tables | `google_document_ai_adapter.py` | *Disabled.* Corroboration evidence. See cross-checking. |
| 3 | Table reconciliation | `independent_table_reconcile.py` | Cross-check: mapped rows vs independent cells. |
| 2 | Classification consensus | `classification_consensus.py` | Accepts a document type only where two independent model vendors agreed. Run it after extraction when the intake rules classified little; feed it to `template_observations.py`. |
| 3 | Context-aware update | `extraction_context.py` | **Required when consensus reports an exception rate above 50% with no context.** Builds the corpus context from approved mapping rules and agreed families; re-extract **both** lanes with `LLM_CORPUS_CONTEXT` set to it. `run_lane_coverage.py` fails until this exists. |
| 3 | Source templates | `template_observations.py` | Builds the template artifact the mapping lanes consume, from a consensus run. Pass `--classifications` or a corpus the rules could not classify becomes one template that is not a layout. |
| 3 | Semantic mappings | `schema_discovery.py discover\|registry-update` | *Optional.* Proposals; client approval required. |
| 3 | Applied mappings | `apply_mappings.py` | Carries an **approved** mapping rule into the controlled field on the document record. Discovery and approval alone change nothing the document controls read. `--independent-evidence` with `--authorization` lets an independent extractor's reading stand in for a second engine, but only where it read **both the caption and the value**; flagged `corroborated_by_independent_extractor`, never `consensus_*`. |
| 3 | Slot equivalence | `schema_discovery.py slot-equivalence` | *Optional, disabled.* Runs after consensus; proposes that two schema slots are one fact. |
| 3 | Consensus | `consensus.py` | Refuses two lanes that resolve to one model vendor, and two lanes on one transport unless each declares it read the source itself. |
| 3 | Engine qualification | `engine_agreement.py` | Optional, before committing a corpus to a new second reader. Reports value recall and field-exact agreement; a matching row count is not corroboration. |
| 3 | Independent corroboration | `independent_corroboration.py` | Optional. Weighs `single_engine` findings and two-way disagreements against a non-LLM extractor's reading of the same page. Not vendor agreement; resolves nothing; pass every retained extractor artifact including recovery passes. |
| 3 | Applied corroboration | `apply_corroboration.py` | Optional, and the only lane that accepts a value on weaker-than-consensus evidence. Runs **only** under a named client decision (`--authorization`); never overwrites a consensus-accepted field and never relabels an acceptance `consensus_*`. Page-scoped evidence defeats a misread, not a misattribution. |
| 3 | Arithmetic | `arithmetic_check.py` | Self-proof against source-visible arithmetic. |
| 3 | Amendment proposals | `adjudicate.py` | Uniquely arithmetic-supported amendments only. |
| 3 | Audit-only LLM amendments | `llm_adjudication.py` | *Disabled.* Proposals, never a clearance. |
| 3 | Validation | `validate_extraction.py` | Format, provenance, plausibility. |
| 3 | Addresses | `address_normalize.py` | Raw preserved; provider validation optional. |
| 3 | Entities | `entity_resolve.py` | Reversible merge log; ambiguity goes to review. `--party-decisions` carries a person's authorized decisions about named pairs -- same party, branch of a parent, kept apart, not a party -- and reports any it cannot apply. |
| 3 | Dealer locations | `party_locations.py` | *Optional, disabled.* Asks Google Places where each dealer and brand trades, one request per party within the cap, and accepts a candidate only on a close name match, as a probable public fact for `augment_export.py --external`; every candidate is kept for client review. Never sends a customer, a person or an address. |
| 5 | CRM input validation | `crm_input_validate.py` | Runs **after** the export, against the canonical schema. Nothing is repaired: every finding is a mapping decision. |
| 3 | Brand recovery | `brand_recover.py` | Optional. Recovers the manufacturer from the page letterhead when `brand_name` held the client, a dealer or nothing, and from a continuation page's layout: the words only one maker's pages print, or the maker its five most alike pages all name. A page whose top names an unknown company stays a question naming it. Independent-extractor evidence, never vendor agreement. |
| 3 | Specifier recovery | `specifier_recover.py` | Optional. Reads a printed column the schema once had no field for, out of retained extractor layout. Independent-extractor evidence, never vendor agreement. |
| 3H | Handwriting OCR | `google_handwriting_ocr.py` | *Disabled.* One provider-group vote. |
| 3H | HTR reconciliation | `handwriting_review.py` | Independent readings reconciled or escalated. |
| 3A | Attribution | `attribution.py` | Every in-scope dollar attributed or registered. |
| 3A | Allocation policy | `allocation_policy.py apply\|discover\|decision-template\|registry-update` | Effective-dated, client-approved rules only. |
| 3A | Inferred controls | `inferred_controls.py` | *Optional.* Proposal-only; never clears Phase 4. |
| 4 | Completeness | `completeness.py` | GL and payment evidence. Report variance; never plug it. |
| 4Q | Sampling | `sampling.py` | MUS bound over the clean population only. |
| 4Q | Accuracy sample | `accuracy_sample.py` | Draws the pages to read against their own image from the whole population, stratified by layout and reproducible from the document ids, because `sampling.py` covers only the auto-accepted frame and that is empty when nothing is accepted. Reports what one read page stands for; accepts nothing. |
| 4Q | Golden set | `golden_set_evaluate.py` | *Optional.* Needs client-authorized truth. |
| 4Q | Final review queue | `final_review_queue.py` | The gate. Every review-bearing artifact goes in. |
| 4Q | Simulated client comments | `ai_simulated_client_review.py` | *Disabled.* Never client authorization. Cards run concurrently (`--workers`); the resume checkpoint is lock-guarded. |
| — | Evidence graph | `evidence_graph.py manifest\|build\|query\|path\|candidates\|contradictions\|schema\|schema-surface\|schema-recovery-scope` | `manifest` first: it binds the artifacts `build` reads. Deterministic overlays; see evidence-graph. |
| — | Client context | `client_review_context.py`, `client_input_comments.py` | Hash-bound reasoning-only context. Pass `--records` or the relationship lanes get none. |
| — | Reference discovery | `client_review_inference.py` | *Disabled.* Bounded, evidence-cited. Batches run concurrently (`--workers`). |
| — | Iterative relationships | `client_review_iterative.py` | *Disabled.* Primary/buddy, capped at three passes. Batches run concurrently within a pass; passes stay sequential. |
| — | Cross-packet verification | `client_review_cross_packet.py` | *Disabled.* Cross-check across packets. |
| — | Cross-record search | `client_review_cross_record.py` | *Disabled.* Cross-check within retained evidence. Batches run concurrently (`--workers`). |
| — | Exception resolution | `client_review_exception_resolution.py` | *Disabled.* Never clears its originating control. Batches run concurrently (`--workers`); primary precedes buddy within a batch. |
| — | Legacy replay | `client_review_consensus.py` | Deprecated compatibility path. |
| 4Q | Card review | `client_review_llm.py` | *Disabled.* Proposal-only. One call per card, not per item. Runs cards concurrently (`--workers`); the artifact is identical at any worker count. |
| 4Q | Full-dataset agent | `review_agent.py` | *Disabled.* Bounded slices. Slices run concurrently (`--workers`); iterations within a slice stay sequential. |
| any | Client review pack | `client_review_lane.py build\|read-answers` | Queue, group, question, evidence and render in one command. Pass `--classifications` alongside `--consensus` for the same reason grouping needs it, and `--resolved-by` or the pack re-asks every question the gate already reconciled; pass `--scan-profile` or a blank page can become the worked example a client answers about; `read-answers` re-ingests the returned workbook. Threshold-gated per control. |
| 4Q | Root-cause grouping | `review_grouping.py` | Retains every underlying item. Pass `--classifications` or the groups are labelled `unclassified_document_family`: the extraction consensus says `unknown` wherever its lanes could not agree a type, which is most of a corpus, and the classification consensus is the artifact that knows. |
| 4Q | Safe consolidation | `safe_review_consolidation.py` | Orchestrates the proposal-only reduction. `--enable` **re-runs** cross-record and the full-dataset agent into its own `--output-dir` rather than reusing earlier artifacts, so budget a second cross-record pass. Give `--run-dir` an absolute path: sub-lanes run from the repository root. `--disable-client-package` stops it short of issuing the workbook. |
| — | Client package | `client_review_package.py create\|preserve-responses\|import-decisions` | Issue and re-ingest the workbook. |
| — | Classification amendment | `classification_amend.py` | Appends an operator-authorized client decision to classification where consensus abstained. Never replaces vendor agreement, and refuses a plan that is not `operator_authorized`. Retyping a document an earlier client decision typed needs `--supersede-prior-client-decisions`, which retains the earlier acceptance. Regenerate templates afterwards. |
| — | Decision compile | `client_decision_compile.py compile\|authorize` | Impact preview, then default-defer authorization. Six change types compile and authorize; only `template_registry_rule` can be applied, by `template_drift.py registry-update`. Choose the type against its consumer before an operator signs. |
| — | Approval projection | `simulate_client_approval.py` | Stress tests only; never a client decision. |
| 5 | Analytics | `analytics.py` | Respects gate state and completeness limits. |
| — | Multi-engine vote | `multi_engine_vote.py` | Counts every reading the run retained, one vote per vendor, to settle fields consensus left open because it only ever compares two lanes. Run it before building the review queue: on one corpus it settled 6,951 fields for nothing, measured at 96.3% (two vendors) and 100% (three) against held-out truth. |
| — | Arithmetic reconciliation | `arithmetic_reconcile.py` | Resolves a cell two vendors disagreed on by the line's own arithmetic, where exactly one candidate satisfies it. The only control here that defeats a misplaced value rather than a misread one. Optional; run it after consensus and before the review queue is built. Acceptances are labelled `reconciled_by_document_arithmetic`, never `consensus_*`. |
| — | Verify pages against their images | `page_review.py` | Checks the export against the source page rather than against another model. `--packet` hands a reviewer one page's image and the rows the export claims for it; the reviewer returns the money the **page** prints, and the lane compares that mechanically -- so the verdict never depends on the reviewer's prose. Names lost money (printed, held nowhere) and invented money (counted, never printed), and separates a misreading from an over-extraction: a page emitting 44 records for 11 printed rows may have misread nothing, and counting those as wrong cells moved one run's reported accuracy five points. A document header is compared on the page's own money column and may restate the page's printed total; a line row may not, because that is the page's money counted twice. `--proposals` turns the findings into proposals an operator can authorize, each graded by what stands beside the reviewer -- the independent extractor's text of the page, a retained engine reading -- and a page the extractor never read is named as such, never counted as a page that prints nothing. `--second-read` reads the same pages through another vendor, never the reviewers' own: a correction the reviewer alone supported becomes `second_vendor` where that reading holds the figure, and a row the extractor prints as often as the export becomes `second_vendor_prints_it_fewer_times` only where the reading holds every other figure in the column -- Gemini read 66% of the primary engine's values on the 47 pages it re-read, so what it lacks proves little. Found the defect no control could: a template whose COMMISSION column is split into components was read from a sub-column, and because those pages state no base and no rate there was no arithmetic to fail. Optional; accepts nothing, clears no control, and no Phase 6 command reads its output. |
| — | Fill what the export leaves empty | `augment_export.py` | Carries the run's own evidence into empty cells, labelled and never over a reading: `<column>__inferred` beside the cell, `__inferred_by` the method, `__inferred_evidence` what supports it. On one run it filled 17,311 cells -- a document type two vendors agree on, a line's brand from its own letterhead, a value the same job carries unanimously on other statements, a branch or state a party's name prints, an ISO country code, an E.164 phone, and public-source identities with their URLs -- and 96.6% of the values it carried were found on their own page by the independent extractor. Dates are never carried: one field here is filled from six printed columns. With `--parties` it also writes the accounts and contacts a CRM should load rather than build from the raw columns -- accounts joined on `<field>__party_key`, never from the client's fields, a job or a refused reading; a contact's company only where an email naming the person proves it, so murbrook's main line printed beside the client's rep is never hers. With `--product-rules` each line's product is read by its manufacturer's own rule and `--out-products` writes the products a CRM should load, rather than building them from `description`, which on this corpus holds projects, project clients and specifiers; `--extractor-raw` checks each code against the row its line's amounts are printed on, because Linden World's code column slips a row on some pages. An inferred party carries its account's key in `<column>__inferred_party_key`, and `same_key_on_other_lines` fills a line's customer or dealer from the same maker's lines printing its order, job or project number beside one party. It is scored first by hiding the party on lines that print it -- 97.4% of customers and 96.0% of dealers on the commission run, against 28.6% and 30.3% by chance -- and a field under 95% fills nothing. Optional; run after the export and before validation; accepts nothing and clears no control. |
| — | Amend what the page review proved | `page_review_amend.py` | Turns the page review's proposals into append-only amendments under a named operator authorization, for corrections an independent source backs; a correction the reviewer alone supports cannot be selected, while one another vendor's reading bears out (`second_vendor`) can. A correction whose cell has changed since the review -- a re-read, a re-file or another amendment -- is refused (`the_field_changed_since_the_review`). A figure moved between lines is applied only with both halves proved: applying the backed half of one shifted row alone counted $1,404.13 twice on one page, and a correction that would break a line whose base x rate = commission holds is refused, because the evidence proves a figure is on the page, not on which line. Unguarded, 455 amendments raised the pages that reconcile with their image from 630 to 653 of 716 while breaking 14 lines' arithmetic; guarded, 434 apply (19 refused, 29 moves held) and 650 reconcile with no line broken. The export registers the lane (`accepted_page_review_amendment`, read as `corroborated`). Optional; needs the operator's authorization; clears no control. |
| — | Re-file a column filed under the wrong field | `column_refile.py` | Moves a reading an engine filed under the nearest field to the field the page's own heading names, under a named operator authorization, and removes it from the field it left, because an empty dealer claims the page printed none. The reading travels whole and carries `refiled_from` -- the field, the authorization, the evidence. Lumen Weft's reports print a Specifier column and no dealer column, and the commission run filed 81 of its values, architects among them, as dealers: two engines agreeing on a filing is the failure vendor agreement cannot see. A reading -- a settled value, or the candidates the engines left unsettled -- moves with its column and is never moved onto a field that already holds one, a page the rules name but the records lack is reported, and a run that moves nothing fails. Optional; needs the operator's authorization; clears no control. |
| — | Bring records into step | `records_in_step.py` | A record carries each reading twice: flat in `fields`, which the export reads, and nested in `header` and `lines`, which attribution, arithmetic, valuation, entity resolution and the canonical export read. On the commission run the vote and arithmetic lanes had put 4,099 accepted values in the fields alone and the mapping lane 393 approved mappings in the header alone, so the controls and the export each decided without what the other held. Every writer now keeps both in step (`consensus.refresh_views`); this brings an existing record artifact into step -- each approved mapping a view holds carried into its empty field, every view brought up to its fields, nothing overwritten -- and `--check` fails while any document is out of step. Run it on a record artifact written before the fix, then rebuild the controls and the export from its output. Optional; changes no reading, review status or control. |
| — | Complete field extract | `field_extract_export.py` | Writes every extracted field of every document with the status that governs it — `accepted_vendor_agreement`, `accepted_independent_corroboration`, `accepted_same_document`, `accepted_extractor_tiebreak`, `accepted_page_review_amendment`, `accepted_document_arithmetic`, `accepted_derived`, `single_reading`, `contested` — and a one-word `confidence` (`confirmed`, `corroborated`, `computed`, `derived`, `unconfirmed`) for a reader acting on the cell rather than auditing it, plus intake provenance and the findings blocking it, and pivots to the record grain a CRM import loads with `row_confidence` and `unconfirmed_fields` on each record. The pivoted cell carries the value consensus accepted; a contested cell is empty and named in `contested_fields` rather than holding both candidates, which put text in a money column on 48.7% of one run's line rows. Beside every money, count and date column sits a typed `__amount` / `__currency` / `__iso` companion that refuses what it cannot decide — an undecidable thousands convention, a slash date in a document that never proved its own order, or a year the cell cut short; a month named without a day types as `__month`. Line flags mark rows a monetary total must account for, in two groups that must not be treated alike: rows that are not a line of this document -- `duplicate_of_line` (an engine padded eight real lines to a hundred), `amount_without_line_identity` (money the page attaches to no product, job, project or purchase order), `repeats_a_job_and_amount_above` (a page read in two passes emits each line again with different columns filled, so no two rows are equal and the duplicate check clears them) -- and `repeats_a_line_in`, a real line that also appears on another statement, which is a decision for the importer rather than a defect. Excluding the first group reproduces a hand-checked page's printed total to the cent; excluding both returned 26% of the run's money. `--rows-not-printed` labels -- never drops -- a line the page review says the page does not print (`page_review_row_not_printed`), under a named authorization. Each acceptance lane is registered by the `accepted_by` it writes; an unregistered one is named `accepted_by_an_unregistered_lane` rather than promoted to vendor agreement, which is what silently overstated 3,566 fields on the commission run. Takes the run's **current record artifact**, not its consensus artifact: acceptances are appended after consensus, and reading one step early reported 26,300 accepted where the run held 73,878. Optional; use it to read the run's own evidence when the canonical export admits nothing. It is not a canonical load, clears no control, and no Phase 6 command reads it. |
| — | Exception resolution | `exception_resolve.py` | Writes `exception_resolved`, the review-clear status `canonical_export.py` admits and nothing else produced. `consensus.py` computes a document's `review_status` once, from the readings; every later control copies it forward, so a document that entered review as `open_exception` stayed there however much was later established about it — which is why one run exported 0 of 716 after the arithmetic vote reported 620 documents promoted. Clears a document only where an operator-authorized plan resolves **every** finding still queued on it; a partial authorization leaves the document open and names the findings nobody answered. Optional; run it after the authorized changes are applied and before the canonical export. It approves nothing and never relabels a document that is already clear. |
| 6 | Canonical export | `canonical_export.py` | Builds the export every later Phase 6 command reads. Review-clear, provenance-linked documents only; everything else is a retained exclusion. |
| 6 | Canonical DDL | `canonical_deploy.py` | Plan by default; `--execute` crosses a boundary. |
| 6 | Canonical load plan | `canonical_load.py` | Review-clear, provenance-linked rows only. |
| 6 | Target staging | `csv_api_staging.py` | No-send package. |
| 6 | Common CRM import package | `crm_import_package.py` | Optional no-send common-object CSVs and target mapping/write plan. |
| 6 | Retrieval | `retrieval_store.py build\|query\|get`, `retrieval_mcp.py`, `retrieval_remote_mcp.py`, `retrieval_https.py` | Approved facts only; deployment and client connection use the MCP/API operations skill. |
| 6 | Delivery folder | `client_delivery_package.py` | The only supported way to build a client folder. |
| — | Monitoring | `run_monitor.py`, `llm_usage_report.py` | Read retained artifacts; contact no provider. |
| — | Run workspace | `run_workspace.py init\|run\|audit` | **Use for every live run.** Creates the fresh contained layout, routes cache/throttle/logs under it, rejects declared output escapes, and audits the retained command ledger. |
| — | Pipeline selection | `pipeline_plan.py` | **Read this before spending on the optional lanes.** Measures the run's own retained artifacts and proposes which optional lanes this corpus needs, which it does not, and which client questions the plan cannot avoid. A proposal: it authorizes nothing, clears nothing, and reports an unmeasured lane as undecided rather than defaulting it. See [`../pipeline-selection/SKILL.md`](../pipeline-selection/SKILL.md). |
| — | Lane coverage | `run_lane_coverage.py` | **Run this before you call a run complete.** Names every lane that produced nothing. |
| — | Generation manifest | `run_generation.py template\|verify` | **After a restarted run.** Declares which retained artifact each lane stands behind; `verify` names declared artifacts that do not exist, undeclared ones that would credit a lane, and paths declared both authoritative and superseded. `run_lane_coverage.py --generation` reads it. |
| — | Run sentinel | `run_sentinel.py preflight\|watch` | **`preflight` before you spend; `watch` while you run.** Credentials up front, retained provider failures as they happen. |
| — | Credentials | `reauthorize_google.py` | Before any Google-backed lane. |

## Non-negotiable, in operating terms

1. **Never reuse an output path.** Every run and every retry gets a new
   directory. The commands enforce this; do not work around a `no-clobber`
   refusal by deleting the previous directory.
2. **Never edit an artifact by hand.** Corrections are append-only amendments
   applied through the authorized path, followed by rerunning the affected
   controls.
3. **Never hand a client anything but the delivery folder.** Raw provider
   responses and credentials stay on the operator's machine. Use
   `client_delivery_package.py`; do not assemble a folder by copying files.
4. **Never claim a gate passed without its artifact.** Read the JSON. A clear
   gate says so explicitly.
5. **Never let a same-vendor pair stand in for independence.** Two lanes, two
   model vendors — a router serving another vendor's model is that vendor.
   Conditioning is part of independence: both lanes read the same corpus context
   or none, and `consensus.py` refuses a pair whose context hashes differ.
6. **Never split one run across sibling folders.** Initialize it with
   `run_workspace.py init` and execute every writing command through
   `run_workspace.py run`; its runtime cache, throttle, logs, and retry overlays
   stay beneath the same run root.
7. **Never silently omit a selected lane or item.** Full-run authorization sends
   every in-scope item to each selected provider lane. A provider or prerequisite
   limitation is an explicit retained result, not a reason to shrink the input.

## Where authority lives

This skill is a router, not a source of truth. When it and a reference disagree,
the reference wins:

- Every environment setting: [`references/runtime-configuration.md`](../../references/runtime-configuration.md)
- Every CLI and its exact options: [`references/command-line-reference.md`](../../references/command-line-reference.md), the complete parser-derived [`cli-help-catalogue.md`](../../references/cli-help-catalogue.md), plus `--help`
- Every file shape: [`references/artifact-contracts.md`](../../references/artifact-contracts.md)
- Phase entry and exit criteria: [`references/workflow-gates.md`](../../references/workflow-gates.md)
- The complete runbook: [`docs/TECHNICAL_DOCUMENTATION.md`](../../docs/TECHNICAL_DOCUMENTATION.md)
- Client-facing preparation: [`docs/CLIENT_USER_GUIDE.md`](../../docs/CLIENT_USER_GUIDE.md)

Every argument of every command carries help text, so `--help` is a complete
answer to "what does this option do". `scripts/release_check.py` fails if any
argument stops explaining itself.

## Keeping this skill honest

`scripts/agent_surface_check.py` verifies that every command named anywhere in
this folder still exists and is indexed in the command-line reference. It runs in
the release gate and in the repository's Claude Code hook, so a renamed or
removed script cannot leave stale instructions here.
