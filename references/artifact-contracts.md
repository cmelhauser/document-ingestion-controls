# Artifact Contracts and Runbook

## Optional pre-pipeline visual intake journal

The separate `visual_ingestion_v1` input/proposal contract is specified fully in
[Visual Intake](visual-ingestion.md). Its private `intake.sqlite` retains original
PNG/JPEG bytes, source SHA-256 receipts, source-set-bound proposals, validation
findings, reviewer grants, and request-key history as append-only entries. The
journal is bound to one tenant and each session to one local/authenticated owner.
Bounded rejected inputs remain retained; malformed/oversized envelopes and quota
refusals are explicitly unaccepted. Exact retries return the original receipt;
changed inputs require new keys. Every declared page must have a record or
explicit exception. Accessible-session discovery and review-summary responses are
derived read models over those retained entries, never approval artifacts or
mutable workflow state.

This is not a canonical export, pipeline extraction handoff, source manifest,
consensus vote, amendment, or approval artifact. No intake result is eligible for
retrieval or CRM staging, and no automatic proposal promotion is implemented.
The current contract ends at `rejected` or `pending_review` with
`canonical_write_permitted: false` and `published: false`. Reviewer access is
explicit, additive, and read-only; owner transfer and admin moderation are out
of scope for this contract version. Existing run and output contracts below are
unchanged; never coerce a journal proposal into a passing artifact to bypass
their prerequisites.

The optional local `visual_ingestion_export.py export` utility now emits a
`visual_intake_source_package_v1` source-only package. It retains exact original
bytes for every upload attempt, all journal payload/receipt versions and
integrity metadata, an existing-contract `exceptions[]` artifact, and (only for
a complete usable source set) a derived image PDF. Its final manifest binds all
file hashes, source-set identity, page/proposal counts, original-to-PDF/intake
page mapping and explicit transformations. The export receipt pins that
manifest's exact SHA-256. `verify` rechecks files, safe paths and entry integrity.
The full flags, size limits, failure states, transformation semantics and
source-profiling/intake sequence are in [Visual Intake](visual-ingestion.md).
The existing `ingest_pages.py`, not the exporter or LLM, creates the pipeline
manifest. Preserve both the original-source package and derivative provenance;
include its exceptions in final review. No source preparation result is a
consensus vote, authorization or clearance. Incomplete/failing preparation
retains exceptions and exits nonzero without a usable source-PDF declaration.

## Existing pipeline artifact contracts

This is the definitive, versioned interface contract for a Business Document
Ingestion run. It names the supported inputs, run stages, output formats, and
review headers. It does not authorize a script to change retained evidence.

## Universal rules

- All JSON and CSV files are UTF-8. JSON is the authoritative machine-readable
  record; CSV, XLSX, and HTML are reviewer views generated from it.
- Paths stored in an intake manifest are relative to that manifest. Absolute
  paths and paths escaping its directory are rejected.
- A controlled run is initialized with `run_workspace.py init`. Every generated
  artifact, cache, throttle timestamp, log, and retry overlay is a descendant
  of that run root; source inputs and credentials remain external read-only
  boundaries. `run_workspace.py run` rejects declared output-path escapes and
  records a non-secret command ledger.
- A source PDF and every retained one-page PDF are immutable evidence. An
  amendment or decision is a new artifact, never an in-place replacement.
- Every uncertainty, failed validation, provider failure, or unresolved value
  belongs in an exception/review artifact. A missing item is not a pass.
- Dates use ISO 8601 (`YYYY-MM-DD` or an ISO 8601 UTC timestamp). Decimal
  values remain strings until the deterministic arithmetic stage.

## Input interfaces

| Input | Format and required shape | Producer / use |
|---|---|---|
| Source delivery | Original PDF, one or many pages | Client supplies; `ingest_pages.py` retains a byte-identical, SHA-256-verified copy under the intake run's `source/` directory. |
| Intake manifest | JSON object with a `pages` array | `ingest_pages.py`; shared immutable page provenance. |
| Retained page | One-page PDF named with a zero-padded source sort key | `ingest_pages.py`; page master for all extraction. |
| Retained native text | UTF-8 `.txt` sibling named by manifest `text_file` | Optional `pdftotext` result; never replaces page PDF. |
| Engine extraction | JSON array of page records. Each retained `raw_response` path is relative to the handoff directory, or (for a contained workspace handoff) run-root-relative and must begin with the handoff's declared `raw_response_directory`. | An independent OCR/HTR/LLM adapter; consumed by consensus. |
| Google Document AI evidence | JSON array of page-matched independent OCR/table readings with raw response paths, token/cell evidence, and source-native table rows. | `google_document_ai_adapter.py`; corroboration evidence only, never an automatic canonical mapping or final financial/identity decision. |
| Google handwriting OCR | `google_cloud_vision_handwriting_htr_v1` plus a page-complete adapter handoff, raw-response directory, retained rendered-page directory, and explicit exception artifact. Each bound annotation uses a separately detected `page_id`, `region_id`, normalized box, and source-page hash. | `google_handwriting_ocr.py`; Google Cloud Vision `DOCUMENT_TEXT_DETECTION` is one proposal-only HTR vote, not a detector verdict, independent consensus, or approval. |
| HTR annotations | JSON object with `engine`, `independence_group`, and `annotations`; every annotation binds `document_id`, `page_id`, and stable `region_id`. A retained visual-extraction record list with stable `handwriting_regions[].region_id` and matching `handwriting_readings[].region_id` is also accepted directly. | Independent handwriting providers; consumed by `handwriting_review.py`. Provider families, not model names or roles, determine independence. All Google services count as one `google` group. Extraction-lane handwriting observations remain proposal-only unless the explicit HTR reconciliation and amendment controls run. |
| LLM adjudication candidates | JSON object with a `candidates` array; every candidate carries IDs, original/candidate values, printed provenance, evidence text, clear deterministic-validation status, an agreeing independent extractor, and explicit handwriting/reassembly/disagreement flags. The `amendments` array emitted by `adjudicate.py` is also accepted as a retained input envelope, but each amendment is marked and fails closed as an explicit-candidate-evidence blocker before provider I/O. | `llm_adjudication.py`; only explicit, fully evidenced candidates are queried. |
| Evidence-graph manifest | `schema_version` `1.0`, a non-empty `run_id`, and `artifacts[]` each naming an `artifact_type` from the supported set, a path inside the manifest's own directory, and its `sha256`. | `evidence_graph.py manifest` writes it; `evidence_graph.py build` verifies every hash before reading. Types are supplied explicitly rather than inferred, because a misclassified artifact would enter the graph under correct-looking provenance. |
| Source-template observations | JSON object with a non-empty `templates` array; each template has ordered exact `headers[].source_label` and optional observed entity/relationship evidence. Produced by `template_observations.py` from a consensus run's retained printed labels: one template per document family, labels unioned across its documents with the `observed_in_documents` count each carries, ordered deterministically rather than in layout order, and marked `observation_only`. A label is copied exactly as printed and never renamed here. | `template_observations.py` writes it; `schema_discovery.py`, `allocation_policy.py`, and `template_drift.py analyze` consume it. LLM and location outputs remain client-review proposals. |
| Table-comprehension profile | JSON object from `table_comprehension.py profile` naming one immutable page, its table regions, exact source headers, normalized boundaries, sections, totals, handwriting regions, diagnostics, raw response, and page hash. | `table_comprehension.py`; source-native proposal input only. |
| Table-comprehension source rows | JSON object from `table_comprehension.py rows` with visible source-label cells, visible values, normalized cell/page evidence, and retained diagnostics. | `table_comprehension.py assemble`; no canonical mapping is implicit. |
| Business references | JSON arrays (PO/jobs/GL/payments) using client-approved keys | `attribution.py` and `completeness.py`. |
| Inferred control proposals | New output directory containing proposal-only `attributed.json`, `gl.csv`, `payments.json`, `exceptions.json`, and hash-bound `manifest.json`; every derived row names its source document and inference status. | `inferred_controls.py`; review prioritization only, never a completeness pass or canonical-load authorization. |
| Client review context | `client_review_context_v1` object preserving every returned or separately supplied client comment, source hashes, an optional deterministic pilot record set, and an explicit `independent_consensus_input=false` policy. General input files may be JSON, CSV, TSV, TXT, Markdown, or PDF and retain their source fields and hashes. A delimited file naming a comment column keeps every source column; one that does not is preserved as freeform notes, each non-empty row becoming a comment with its original cells retained, because a client's notes typed into a spreadsheet are still the client's words. | `client_review_context.py` or `client_input_comments.py`; post-review/corpus reasoning context only, never consensus, source evidence, authorization, or control clearance. |
| Reference discovery proposals | `client_review_reference_discovery_v1` with bounded batches, provider/model, discovery and self-check raw-response paths, evidence-validated candidates, and exceptions. | `client_review_inference.py`; proposals only, never GL/payment facts, attribution approval, or gate clearance. Same-provider self-checks are quality filters, not independent consensus. |
| Legacy reference adjudication proposals | `client_review_reference_adjudication_v1` comparing older primary/secondary Gemini artifacts with OpenAI statuses (`matched`, `close`, `conflict`, `unsupported`). | `client_review_consensus.py`; compatibility replay only. New runs use `client_review_iterative.py`; neither path can clear evidence or completeness gates. |
| Iterative client-review proposals | `client_review_iterative_primary_v1`, `client_review_iterative_buddy_v1`, and `client_review_iterative_exceptions_v1`; the configured primary provider's proposals are checked by a configured, genuinely independent buddy provider. Per-round new-material candidate identifiers, requested/completed iteration counts, convergence threshold and termination reason, raw-response, source-context, selected reasoning effort, and proposal-only metadata are retained. Every exception identifies its exact `source_document_ids`, so it can be recovered without guessing a batch membership. | `client_review_iterative.py`; a material candidate is new, source-backed, and buddy-`confirmed`. Matching or unsupported providers fail before a provider call. `confirmed` and `close_needs_review` are inference proposals only and never authorize canonical facts or clear GL/payment/completeness gates. |
| Cross-packet relationship proposals and verification | `client_review_cross_packet_v1` and `client_review_cross_packet_exceptions_v1`; documents lacking an in-packet buddy candidate are grouped for discovery, while documents with an in-packet buddy candidate are independently verified against related packets sharing source-visible business identifiers. Configured, genuinely independent primary and buddy providers are retained with their selected models and settings. The artifact retains separate candidate and verification arrays, per-round worklists/results, new-material candidate identifiers, requested/completed iteration counts, convergence threshold/termination reason, both provider statuses/rationales, caps/deferred coverage exceptions, and binds context, records, prior primary/buddy artifacts, graph, raw provider responses, and exact exception document IDs by SHA-256. Later discovery passes contain only newly exposed source-visible unresolved neighborhoods. | `client_review_cross_packet.py`; a material candidate is new, source-backed, and buddy-`confirmed`; a verification can confirm, flag close review, conflict, or unsupported evidence but never modifies the existing candidate. Matching or unsupported providers fail before a provider call. Graph-guided outputs remain inference proposals and cannot create authoritative GL/payment facts, approval, or gate clearance. |
| Control-exception resolution proposals | `client_review_exception_resolution_v1` and companion exception artifact. Records plus named reassembly, validation, or attribution findings become bounded source packets for configured independent primary/buddy reviewers. Both reviewers receive the same exception, source-record, and reasoning-context packet; the buddy also receives the primary proposals. A supplied `client_review_context_v1` is hash-bound reasoning-only context, never source evidence or consensus input. Every input finding is counted and assigned an exception ID; each retained proposal records the source exception IDs it addresses. Missing source IDs, oversized or batch-deferred work, provider/schema failures, and primary proposals that fail the packet/evidence contract remain explicit. Retryable batch, oversized, and cap-deferred exceptions retain the exact original findings, kinds, and exception IDs in `retry_findings`, `retry_kinds`, and `source_exception_ids`; a later no-clobber retry reconstructs the named control context instead of inferring it from document IDs. Proposals are append-only and source-cited; they cannot rewrite records, clear a control, authorize a fact, or substitute for GL/payment evidence. | `client_review_exception_resolution.py`; deterministic reassembly, arithmetic, validation, attribution, completeness, and final-review reruns remain required. |
| AI simulated-client review comments | `ai_simulated_client_review_v1`, companion `ai_simulated_client_review_exceptions_v1`, atomic `ai_simulated_client_review_checkpoint_v1`, raw per-packet response directory, and derived CSV/XLSX views. Every queue card receives source/context-bound model input and every in-scope review item receives a labelled comment or an explicit exception. A card too large for one request is split by the shared byte-adaptive packet utility into `.partNN.json` packets that each carry the same card and context; only a review item too large to fit alone is retained as an exception, and each concluded packet is checkpointed so a resume never re-sends it. Source records occur once per packet; each item names only its assigned artifact-scoped references. A retained quote must occur within one scalar value of a cited item source and records its exact JSON pointer. Each comment records the resolved non-secret configuration hash, selected provider/model, raw-response SHA-256, protection reasons, and `client_authorization=false`. Optional context is hash-bound reasoning-only context, never evidence. A matching checkpoint may resume an interrupted run. `--retry-exceptions` creates a separate no-clobber recovery overlay for retained provider, size, cap, or missing-source cards. Output labels must visibly state that the review is simulated and not client authorization. | `ai_simulated_client_review.py`; decision support only. It cannot impersonate a client, authorize facts, apply amendments, clear a protected item/control, satisfy GL/payment requirements, or permit canonical/CRM staging. |
| Run configuration | JSON object supplied to `operations.py manifest` | Non-secret settings captured by hash in the immutable, versioned run manifest. |
| Allocation legs | JSON object with `allocation_legs[]`; each leg retains source amounts, source rate/share/descriptor when present, `template_fingerprint`, and optional approved dimensions/effective date. | `allocation_policy.py`; creates derived classification only. |
| Allocation-policy template | JSON object with `templates[]`, source labels, and source evidence. | `allocation_policy.py discover`; strict LLM proposal only. |
| Allocation-policy client decisions | JSON object generated by `allocation_policy.py decision-template`; client edits only `decisions[]`. | `allocation_policy.py registry-update`; creates next registry snapshot. |
| Golden truth and predictions | Separate JSON objects containing unique `items[]`; truth carries exact expected values/group dimensions/review flags, while predictions carry status, proposed value, review routing, auto-acceptance, and optional arithmetic/completeness statuses. | `golden_set_evaluate.py`; acceptance evidence only. |
| Extraction corpus context | A UTF-8 text file naming, for each canonical field, the printed labels the engagement has **approved** as belonging to it, plus the document families two independent vendors agreed on and any operator prose; and a JSON sidecar with `artifact_type: extraction_corpus_context_v1`, the context `sha256`, byte count, named sources, and `reasoning_only`/`clears_no_control` both true. Bounded by `MAX_CORPUS_CONTEXT_BYTES`; an empty render is refused rather than written. | `extraction_context.py`; consumed by every extraction adapter through `LLM_CORPUS_CONTEXT`, which appends it to the prompt under a preamble stating that the page wins and no value may come from the notes. The hash is recorded in each provider handoff as `corpus_context`, and `consensus.py` refuses lanes whose hashes differ. It carries no value, authorizes nothing, and clears no control. |
| Applied source-label mappings | JSON object with `artifact_type: applied_source_label_mappings_v1`, `minimum_engines`, and `promotions[]` carrying `document_id`, `canonical_field`, `value`, the `source_label` and `registry_rule_id` behind it, the `approval_authority`/`approved_by` of that rule, `agreeing_engines`, and a `consensus_flag`. Its companion record set (`records_with_applied_mappings_v1`) is the consensus documents with accepted promotions merged into `header`, each retaining `mapped_from_source_label` and its rule. Every approved label that could not be promoted is an explicit exception naming whether one engine read it or the engines disagreed. | `apply_mappings.py`; append-only. The consensus artifact is read-only input and is never edited, an already-populated controlled field is never overwritten, and the lane clears no control. Under `--independent-evidence` with a named `--authorization`, a label only one engine read promotes only when the independent extractor read **both the caption and the value** on that page, recorded as `caption_corroborated`/`value_corroborated` in an `independent_evidence` block carrying `vendor_agreement: false` and `consensus_flag: corroborated_by_independent_extractor`. Requiring the value is not optional: on one run 183 of 742 caption-only promotions carried the whole column as a single value, and one entered attribution at $600,013,074 against its own printed statement total of $8,673; it is never relabelled `consensus_*`, and the artifact records the evidence sources and the authorization. |
| Accepted classification consensus | JSON object with `artifact_type: classification_consensus_v1`, `minimum_vendors`, hashed `source_handoffs`, and `accepted[]` entries carrying `document_id`, `document_type`, and the `agreeing_vendors` that proposed it. Every unresolved document is an explicit exception naming whether no engine answered, only one vendor answered, or the vendors disagreed. | `classification_consensus.py`; consumed by `template_observations.py --classifications` to decide a document's family. Append-only: it never edits an extraction record, manifest, or page, and it clears no control. |
| Independent corroboration | JSON object with `artifact_type: independent_corroboration_v1`, the `evidence_sources[]` it read, `evidence_pages`, `corroborations[]` and `tie_breaks[]` each carrying `document_id`, `field`, the value, `match_scope: page`, `match_mode` (`substring` or `token`), and `occurrences`, plus a `limits` string that travels with the artifact. Every finding it could not weigh is an explicit exception naming the reason. | `independent_corroboration.py`; it asks whether a value an engine read appears in an independent extractor's own reading of the same page. Matching is page-scoped, so it defeats a misread and not a misattribution, and `occurrences` says how located the match is. It is **not** independent model-vendor agreement, must never be recorded or presented as consensus, and clears no control: accepting a value on it is a separate authorized step. |
| Applied corroboration | JSON object with `artifact_type: applied_corroboration_v1`, the `authorization` it was applied under, `unique_occurrence_only`, and `acceptances[]` each naming `document_id`, `field`, `value`, `kind` (`corroborated_single_reading` or `independent_evidence_tie_break`), `match_mode`, `occurrences`, and the `prior_consensus_flag`. Its companion record set (`records_with_corroborated_values_v1`) is the consensus documents with accepted values merged in, each accepted field carrying an `acceptance` block (`accepted_by`, `authorization`, `match_scope: page`, `vendor_agreement: false`) and a `superseded_consensus` block holding what it replaced. Its exception artifact is every consensus finding the authorization does not cover. | `apply_corroboration.py`; append-only. This is the only lane that accepts a value on weaker-than-consensus evidence and it runs only under a named client decision -- without `--authorization` it refuses. A consensus-accepted field is never overwritten, an acceptance is never relabelled `consensus_*`, and an authorization covering no acceptance is refused under rule 9. Page-scoped evidence defeats a misread and not a misattribution, and the `limits` string saying so travels with both artifacts. |
| Template-layout observations and registry | Observations contain unique `template_id`, `document_family`, non-empty ordered headers, and optional source-layout sections/totals/regions/boundaries. Registry uses `source_layout_v1` and append-only client-approved entries. | `template_drift.py`; exact matching or review-required drift classification. |
| Client decision change catalog | JSON object with explicit `changes[]`: unique change/decision IDs, supported change type, append-only target/value, exact affected item/document/field lists, evidence references, and optional exact decimal amount. | `client_decision_compile.py`; impact preview and authorization template. |
| Canonical export envelope | JSON object with `batch_id` and supported canonical `tables`; batch-managed rows preserve a unique primary/composite idempotency key, matching batch lineage, a required review-clear status (`auto_accepted`, `sampled_verified`, or `exception_resolved`), and applicable evidence provenance. A source-independent row may omit `review_status`, but any row that supplies it must use one of those three values. | `canonical_export.py` builds it from retained run artifacts and `canonical_load.py` validates the complete ordered load plan, including selling location, ACK/job, attribution, and handwriting-region tables; neither load planning, no-send staging, nor `retrieval_store.py` has an open-review override. |

Canonical load provenance is table-driven. A `document` row requires non-empty
`source_file`, `source_page_range`, and `source_sha256`. Transactional rows
either name a provenance-valid document directly or follow the declared parent
chain: address/contact through a source-linked party when they lack their own
document key; invoice line/accessorial through invoice header; payment
application through payment and invoice; and exception event through document,
shipment, or invoice. Shipment, invoice header, payment, attribution,
handwriting region, amendment, and financial event use their explicit document
evidence keys. Every populated declared link must resolve inside the same
export; an absent, malformed, or broken link rejects the whole plan.

The only source-independent tables are static vocabularies (`currency`,
`unit_of_measure`, `country`, `charge_code`, and `document_type`), approved
reference/master tables (`selling_location`, `party`, `carrier`, `item`,
`lane`, `acknowledgement`, and `job`), and the party-name/role association
history. If one of those rows supplies a declared source or parent link, that
link must still resolve. Source-independent status means the approved master
source, not a fabricated document citation; it never permits an open review
status on a batch-managed row.

### Intake manifest schema

`ingestion_manifest.json` is an object containing `pages`. Each item requires
`page_id` and `page_pdf`; paths are manifest-relative. `text_file` is optional.
The complete intake script also records a manifest-relative `source_file`, the
original basename, and `source_sha256`; this prevents operator filesystem paths
from leaking into the handoff while keeping the delivered source verifiable.

```json
{
  "pages": [
    {
      "page_id": "000001_invoice",
      "page_pdf": "pages/000001_invoice.pdf",
      "text_file": "text/000001_invoice.txt",
      "source_file": "source/client_delivery.pdf",
      "source_original_name": "client_delivery.pdf",
      "source_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "source_page_number": 1,
      "document_type": "commercial_invoice",
      "classification_status": "rule_classified"
    }
  ]
}
```

### Independent extraction record schema

An engine produces an array; each record must preserve the immutable page ID and
must identify the engine. Values are proposals with value, confidence when the
provider supplies one, and provenance. OpenAI's normalized output uses the same
shape and never treats a provider confidence score as proof.

```json
[
  {
    "engine": "provider/version",
    "document_id": "000001_invoice",
    "page_id": "000001_invoice",
    "header": {
      "invoice_number": {"value": "INV-100", "confidence": 0.98, "source": "printed", "model_confidence": 0.87}
    },
    "lines": [{"line_number": 1, "description": {"value": "Service", "source": "printed"}}]
  }
]
```

The shared extraction vocabulary covers invoices, quotations and estimates,
purchase/sales orders and acknowledgements, work and service records, time and
expense reports, packing and transport documents, remittance/payment and account
statements, credit/debit documents, customs records, certificates, contracts,
returns, and `unknown`. Header and line field names are defined in
[extraction-schema.md](extraction-schema.md). The `unknown` family is deliberate:
it preserves source-visible fields through the extension channel rather than
forcing an unfamiliar business document into an inaccurate family.

When a source-visible field does not fit the controlled header or line
vocabulary, the adapter also retains it under `source_labelled_fields`. Each
normalized entry has an opaque stable key and separate provenance-linked
`source_label` and `observed_value` field objects. The exact printed label is
never renamed or discarded. The key identifies an entry inside one engine's
reading only, because it is built from the label text and an occurrence index;
consensus retains these entries per engine under
`source_labelled_field_proposals` with a `source_labelled_field_proposal_count`
rather than reconciling them, and a document whose engines returned nothing else
is an explicit `no_consensus_eligible_field` exception rather than a clean
result. They reach schema-surface inventorying through `schema_discovery.py` and
remain mapping proposals throughout; they are not a canonical field or
authorization.

### LLM extraction adapter configuration

`scripts/llm_adapter.py` first loads the root `.env` and selects one provider for
the entire invocation. The optional `LLM_EXTRACT_PROVIDER` setting controls only the
default `extraction` lane: `openai` uses the OpenAI Python SDK Responses API;
`google` uses Gemini through the Google Gen AI SDK with
`vertexai=True`, the configured GCP project and location, and Application Default
Credentials; `openrouter` uses its separate Chat Completions adapter and key
namespace. Named consensus and reasoning lanes use their explicit lane provider
settings. A shell, CI, or secret-manager value takes precedence over `.env`.
See [runtime-configuration.md](runtime-configuration.md). Both use strict JSON
Schema output and preserve the same normalized extraction record contract.
Provider `model_confidence` is optional retained provenance and is excluded
from consensus and approval decisions; it is not proof of correctness or
independence.

An OpenAI handoff has this non-secret configuration:

```json
{
  "engine": "openai/selected-model",
  "model_configuration": {"model": "selected-model", "reasoning_effort": "medium"},
  "credential_reference": "OPENAI_API_KEY",
  "run_limits": {"max_pages": 500, "max_pdf_bytes": 10000000, "max_text_chars": 100000},
  "transport": {"timeout_seconds": 120.0, "max_retries": 2}
}
```

`--reasoning-effort` accepts `none`, `low`, `medium`, `high`, `xhigh`, or `max`; the explicit
default is `medium`. Set the model and reasoning level once per approved run and
record them in the manifest; do not vary them per page without a measured,
client-authorized evaluation. Raw provider responses are retained separately and
can contain sensitive content, so they follow the client retention policy.

Google validates the same schema behind two transports that do not agree on
how to spell "or null", and the disagreement is silent. Vertex's native API
understands `nullable: true`. An OpenAI-compatible bridge -- which is how a
`google/...` slug reaches Google through OpenRouter -- drops that flag, leaves
`type: "string"` standing alone, and the model then satisfies a strict string by
writing the four characters `null`. Nothing raises. The lane returns the right
number of rows, every absent cell holds a string that is not `None`, and every
one reaches consensus as a value the engine claims to have read. Measured on one
run: Vertex direct produced zero of them; the same schema through OpenRouter
produced 222,190 across 716 pages.

So the translation takes the dialect of the transport that will read it --
`NULL_STYLE_FLAG` for Vertex's native API, `NULL_STYLE_UNION` for anything
speaking the OpenAI shape -- and both adapters drop a cell whose value is the
text `null` rather than recording it as a reading. An empty cell is empty
however the transport made the model spell it.

Dropping alone would be silent, which is how the defect reached 222,190 cells
before anyone saw it, so every record carries `cells_spelled_null` and the
exception artifact carries the run total in
`summary.cells_spelled_null`. Above `SPELLED_NULL_SHARE_LIMIT` of emitted cells
the lane raises a blocking `response_schema_nullability_not_honoured` exception:
a lane whose cells are largely that text has a broken schema dialect, not empty
pages, and must not report a clean run.

Every provider handoff declares `source_read_by`: who read the source bytes.
`model` means the named model read them itself, so its reading is its own. Any
other value names an engine interposed between the source and the model.

That declaration is what lets `consensus.py` tell two lanes sharing a transport
apart from two lanes sharing a *reading*. Independence belongs to the model
vendor, so two different vendors reached through one router are two readings --
x-ai's weights and Google's are not one reading because one company billed for
both. What would collapse them into one is a shared reader: with OpenRouter's
default PDF handling a single `mistral-ocr` pass feeds both models, and they
then inherit that pass's errors identically. So a shared transport is admitted
only where every lane on it declares `source_read_by: model`. An absent
declaration is unknown and fails closed, which means a handoff produced before
this field existed cannot pair with another on the same transport until its
lane is re-run. The same vendor twice is still refused however it is reached.

A page read as an image carries `page_image` on its record: the media type, the
render resolution, the byte count, and the digest of the exact bytes submitted.
The retained page PDF is unchanged and remains the source; the render is a
derived sibling that exists only for the duration of the request, and the
repository renders it with the same Poppler call the handwriting lane uses, so
the page range and resolution cannot drift between the two.

This mode exists because the file-native vendor pool is exhausted. Measured
against OpenRouter's catalogue, the vendors declaring `file` input alongside
structured outputs are OpenAI, Google, Anthropic, Mistral, x-AI, Meta and
Sakana: the first five are already lanes on this corpus, and the last two are
served only by endpoints whose terms require permitting training on the
submitted page, which a client corpus cannot do. The vendors that read
documents without accepting a PDF -- Qwen, Moonshot, Z.ai, DeepSeek, MiniMax,
StepFun and others -- are the only untapped independent readers left.

Rasterizing strengthens the provenance rather than weakening it. The file lane
must pin OpenRouter's `file-parser` engine to `native` and still take the
router's word for who read the glyphs; an image lane needs no plugin and no
pinned engine, because pixels cannot be silently re-read by a third party's
OCR. What the raster does introduce is a resolution, and a page rendered at a
different resolution is a different reading rather than a repeat of the same
one -- which is why the DPI and the image digest are both part of the cache key
and both retained on the record. `auto` never selects this mode, and a provider
adapter whose request translation has not been verified for it refuses it
rather than quietly sending a PDF to a vendor that cannot open one.

A Google handoff identifies the Vertex routing and ADC boundary without storing
a credential value or path:

```json
{
  "engine": "google_genai_vertex/gemini-2.5-pro",
  "model_configuration": {
    "provider": "vertex_ai",
    "model": "gemini-2.5-pro",
    "project_id": "client-approved-project",
    "location": "us-central1",
    "max_output_tokens": 65536
  },
  "response_schema": {
    "dialect": "vertex_structured_output",
    "translated_from_sha256": "<shared extraction schema>",
    "sent_sha256": "<schema Vertex was actually served>",
    "enum_limit": 40,
    "relaxed_enum_paths": ["/properties/header/items/properties/name"]
  },
  "credential_reference": "GOOGLE_APPLICATION_CREDENTIALS",
  "authentication": "application_default_credentials",
  "run_limits": {"max_pages": 500, "max_pdf_bytes": 10000000, "max_text_chars": 100000},
  "transport": {"timeout_seconds": 120.0, "max_retries": 2}
}
```

`response_schema` records that Vertex was served a translated schema rather than
the shared one, because Vertex refuses a schema that branches as widely as the
extraction schema does. `sent_sha256` fingerprints what the provider actually
received, and `relaxed_enum_paths` names every field whose enum was relaxed into
a description to get under the branching limit. That relaxation is a real loss
of enforcement for those fields on this provider only: read the paths before
treating a Google reading of a listed field as schema-constrained. An empty list
means the translation changed no constraint.

### Google Document AI adapter and LLM-audit input

The optional `google_document_ai_adapter.py` calls a client-selected Document AI
processor only with `--enable` (or `GOOGLE_DOCUMENT_AI_ENABLED=true`). It uses a
one-page PDF `rawDocument`, records the complete provider response in a new raw
directory, and returns source text, token geometry, and table-cell evidence. It
does not output canonical headers or final field decisions.

`table_comprehension.py audit --independent-evidence document_ai_adapter.json`
accepts only that adapter handoff. The corpus runner accepts an ordered list of
such handoffs, normally one per intake manifest. Before the LLM sees the evidence, the audit
stage requires exactly one matching page record, the retained page SHA-256, an
explicit `google_document_ai` independence group, and a raw-response path. The
first LLM audit does not receive it. Only an LLM-reported finding or diagnostic
triggers a second, bounded buddy-audit call with the Document AI evidence. Both
raw LLM responses and their combined findings are retained. The prompt calls the
evidence corroborating rather than truth and directs the model to report
conflicts. It may not lower a review requirement or approve facts,
mappings, or amendments. Financial and identity facts remain deterministic
reconciliation plus final client review even when both readings agree.

### HTR annotation schema

```json
{
  "engine": "provider-name-and-version",
  "independence_group": "provider-family",
  "annotations": [{
    "document_id": "000001_invoice",
    "page_id": "000001_invoice",
    "region_id": "REGION-01",
    "semantic_type": "quantity_correction",
    "content_class": "numeric",
    "value": "38",
    "confidence": 0.99,
    "iteration": 1,
    "financial_amendment": true
  }]
}
```

Keep the original bounding box, page reference, raw result, model/service
version, and `independence_group` beside this normalized proposal. Three
genuinely independent provider groups must unanimously agree to clear
handwritten numeric data; otherwise it is client review. Two models or roles
inside one provider group count once.

## Runbook and stage outputs

The run manifest contains `manifest_schema_version`, `pipeline_version`,
`generated_at`, `configuration_sha256`, and `artifacts[]`. Each artifact entry
contains a stable run-local `artifact_id`, its basename in `name`, byte count,
and SHA-256. It deliberately excludes absolute operator paths. Create manifests,
adapter contracts, privacy inventories, and reviewer exports at new paths; the
operations CLI refuses to replace them. Only resumable stage state is atomically
updated in place.

| Order | Command | Principal inputs | Principal outputs / gate |
|---:|---|---|---|
| 1 | `scan_profile.py` | Source PDF | Profile and scan-quality findings. |
| 2 | `ingest_pages.py` | Source PDF | Manifest, ordered one-page PDF masters, native text when available, classification exceptions. |
| 3 | `reassemble_pages.py` | Intake manifest | Grouping proposals and exceptions; no automatic merge. |
| 4 | `preprocess_pages.py` | Page-master directory | Grayscale and binarized sibling variants plus preprocessing manifest. |
| 5 | `operations.py manifest` / `stage` | Immutable input artifacts and non-secret config | SHA-256 run manifest and ordered resumable state. |
| 6 | `llm_adapter.py` (selected provider) | Intake manifest | Engine records, raw per-page responses, provider handoff, exceptions. |
| 7 | `operations.py adapter` | Provider handoff | Validated non-secret adapter contract. |
| 7A | `llm_adjudication.py` (optional) | Explicit candidates + immutable intake manifest | Audit-marked, review-required LLM amendment proposals, exceptions, raw responses, and a non-secret handoff. |
| 7B | `schema_discovery.py` (optional) | Observed source templates and approved mapping registry | Versioned mapping/entity/relationship/location proposals, review items, raw responses, and a non-secret handoff; no canonical change. |
| 7B2 | `schema_discovery.py slot-equivalence` (optional) | Completed consensus run and its exception queue, plus the approved registry | Slot-equivalence proposals labelled by evidence class, retained candidates, review items, raw responses, and a non-secret handoff; no field is collapsed and no canonical change is made. |
| 7A2 | `final_review_queue.py` | Any review-bearing artifacts | The exhaustive de-duplicated queue and document cards. Refuses when no supplied artifact carries review-bearing content, because a clear gate over nothing reads as authorization; an artifact whose exception list is genuinely empty is a clean result and is accepted. |
| 7B3 | `client_review_lane.py build` | Any blocked control's exception or proposal artifacts, plus optional consensus and intake manifest | A pack directory holding the answering workbook, the instruction PDF with one rendered source page per question, the pack JSON with every queue item retained, and an exception artifact naming each example page it could not produce. Threshold-gated per control; a pack asks for decisions and approves nothing. |
| 7B4 | `client_review_lane.py read-answers` | The returned workbook, the immutable issued workbook, and the pack JSON | `client_review_answers_v1`: each answer with its note, whether it matched an offered option, and whether it closes its group or informs an item-by-item recheck, plus the unanswered questions. The returned copy is refused whole unless its question rows match the issued copy in order and every cell outside the two answer columns is unchanged. Answers are client decision proposals and are never applied. |
| 7C | `allocation_policy.py` (optional) | Allocation report templates, approved policy registry, and normalized allocation legs | Strict policy proposals; explicit client decision return file; effective-dated scoped rules; derived sales-credit classifications or review exceptions. |
| 7D | `table_comprehension.py` (optional) | Immutable page plus profile/row/audit proposals and approved mapping registry | Source-row proposals, an adapter handoff, concise material decision cards/final-review exceptions, and a separate quality summary. Same-model roles are not independent consensus. |
| 7E | `table_comprehension_corpus.py` (optional) | One or more immutable intake manifests and approved mapping registry | Sequential page artifacts, retained source-layout context snapshots, resumable state, bounded refinement registers/amendment proposals, and aggregated corpus outputs. |
| 7F | `review_agent.py` (optional) | Complete supplied JSON artifact set, including validated and outstanding outputs | Complete artifact inventory, cross-record evidence index, contradiction register, bounded iteration log, proposal-only hypotheses/item updates/process findings, and non-blocking handwriting comments. No canonical change or production approval. |
| 7G | `template_drift.py analyze` | Source-layout observations and optional approved template registry | Exact fingerprints, compatibility hashes, deterministic similarity candidates, mapping-reuse decision, and fail-closed review items. |
| 7H | `google_handwriting_ocr.py` (optional) | Intake manifest plus one or more separately produced handwriting-region artifacts | Page-complete Cloud Vision handwriting OCR, region-bound HTR proposals, raw responses, retained page renders, adapter handoff, and explicit exceptions. No self-validation or source overwrite. |
| 8 | `consensus.py` | At least two versioned, hash-bound handoffs from distinct provider groups and consensus lanes | Consensus proposals with retained engine identities and disagreement exceptions. |
| 9 | `arithmetic_check.py` | Consensus records | Arithmetic proof; failures remain review work. |
| 10 | `adjudicate.py` | Proofed records and engines | Amendment proposals and exceptions. |
| 11 | `validate_extraction.py` | Proofed records | Original records and deterministic validation exceptions. |
| 12 | `address_normalize.py` | Validated records | Raw addresses retained; local CRM components plus explicitly enabled, capped Google Address Validation evidence, and address exceptions. |
| 13 | `handwriting_review.py` | Three HTR annotation files from independent provider groups | Decisions and unresolved final-review items; same-provider models count once. |
| 14 | `entity_resolve.py`, `attribution.py`, `completeness.py`, `sampling.py` | Address-normalized records and supplied references | Entity proposal log, attribution/register, reconciliation gate, sample plan. |
| 15 | `operations.py privacy` | Retained text artifacts | Privacy inventory for policy review; no redaction. |
| 16 | `final_review_queue.py` | Every review-bearing artifact, including LLM adjudication output/exceptions when enabled | Canonical `final_client_review.json` and final gate. |
| 17 | `operations.py review-export` | Canonical final-review JSON | CSV, XLSX, and HTML convenience views. |
| `document_value.py` (shared library) | Any extraction or consensus record | One document's monetary value plus the basis it rests on: `printed_header_total`, `summed_line_items` (signed net of the document's own lines, labelled derived), or `unavailable`. `attribution.py`, `completeness.py`, and `sampling.py` all value through it, and attribution and sampling report a `value_basis` breakdown so a derived corpus total is never mistaken for a stated one. |
| `schema_discovery.py registry-update` | Current approved registry + discovery or slot-equivalence output + explicit client decisions | New effective-dated registry snapshot; no source or entity evidence is overwritten. A `slot_equivalence` rule is written only from a client-approved proposal an independent verifier confirmed, and every refused decision is retained with its reason in `refused_decisions`. |
| `golden_set_evaluate.py` | Client-authorized truth + pipeline predictions | No-clobber metric/breakdown report and standard exceptions; never automation authorization. |
| `client_decision_compile.py compile` / `authorize` | Complete workbook import + safe consolidation + explicit catalog / unchanged compiled plan + operator decisions | Hashed proposal-only patch/impact/rerun plan and default-defer template / bound operator authorization; neither changes production facts. |
| `template_drift.py registry-update` | Prior registry + operator-authorized compiled plan | New append-only registry snapshot containing only authorized template rules; prior registry remains unchanged. |
| 17A | `exception_resolve.py` | Retained consensus record artifact + operator-authorized compiled plan | New consensus-shaped artifact carrying `exception_resolved` for each document whose every queued finding the authorization resolved, plus an `exception_resolution_report_v1` naming every authorized document it did not clear and the findings that held it. `consensus.py` is the only control that computes `review_status`, and it computes it once from the readings, so no later repair could reach a review-clear status before this. A document is cleared only on a complete authorization: a partial one leaves it open rather than burying the unanswered findings. Optionally amends one or more retained exception artifacts in place-by-copy, marking each answered finding as no longer requiring client review so the final gate stops asking it; the finding is retained and carries the authorization. The prior status and the authorization id are retained on every cleared document, a document already clear is never relabelled, and the report is written whether or not anything cleared. It approves nothing and applies no value. |
| 17A2 | `specifier_recover.py` | Retained record artifact + the independent extractor's retained raw responses | Records carrying `specifier_name` on every line whose printed purchase-order number matches a specifier printed in the same group header, plus an exception per page that prints a specifier no line claims. The schema named no specifier field for most of this repository's life, so on 145 pages of the commission run the design firm was written into `brand_name` -- `NORCROSS TAMPA` and `BSQ - FLORIDA` are architects -- or discarded, with nothing reaching `source_labelled_fields` either. It needed no provider call: the independent non-LLM extractor retains the whole page rather than the fields the schema asked for, so the column was still on disk with its layout. 898 lines over 174 documents on that run. A recovered reading is one extractor with no model behind it, so it is written unaccepted with `consensus_flag: single_engine` and never carries the corroboration status that pairs a model with an extractor; a line already stating a specifier keeps its own, and an ambiguous join yields an exception rather than a guess. Each page is read through `run_io.retained_responses`, from the response that read it: indexed by the first file per page, the 222 pages read only in the extractor's retry were empty here, and reading them finds a specifier on 204 documents rather than 174. |
| 17A3 | `brand_recover.py` | Retained record artifact + the independent extractor's retained raw responses | Records carrying a recovered `brand_name` (header, or the record itself when flat, and `header.brand_name` in the field map the export pivots) with `recovery_method` one of `letterhead`, `letterhead_one_letter_off`, `opening`, `layout_signature`, `layout_likeness`, and `displaced_reading` keeping what the column held; a `brand_recovery_v1` summary counting `brands_recovered_by` method and listing the `vocabulary`; and an exception per page it cannot settle -- `no_retained_extractor_layout`, `extractor_layout_unreadable`, `no_text_in_the_retained_response`, `letterhead_names_several_brands`, `letterhead_names_a_company_never_called_a_brand` (with the company as `candidates`) or `no_known_brand_in_the_letterhead`. The vocabulary is the corpus's own: a name the pages call a brand more often than anything else and that heads at least two retained pages, never the client or a territory code. A stated brand counts only when it is a vocabulary name, read from the header or, where disagreement left the header empty, from the field map the export prints -- which held the client on Marlow/Bramwell's order reports. A page naming no maker in its letterhead takes, in order, the maker its opening shares with that maker's pages, the maker only whose settled pages print at least three of its words (`MIN_SIGNATURE_WORDS`), or the maker all five of its most alike settled pages name (`NEIGHBOURS`, `MIN_LIKENESS`), counting words on five pages or more and at most half. Leave-one-out over the commission run's 641 settled pages named no page's maker wrongly; a page whose top names a company by its legal form is kept as a question instead, because that test cannot see a maker the vocabulary lacks. A recovered reading is one extractor with no model behind it: unaccepted, `consensus_flag: single_engine`. |
| 17B | `canonical_export.py` | Intake manifest, consensus, arithmetic, and the final-review queue | Canonical export envelope plus a retained exclusion artifact; a document enters only with intake provenance, a review-clear status, non-blocking arithmetic, and no open review item, and an agreed field with no canonical column is withheld rather than mapped. It approves nothing. |
| 18 | `canonical_deploy.py` / `canonical_load.py` / `csv_api_staging.py` / `crm_import_package.py` | PostgreSQL DDL / approved canonical export / exact matching load plan | Explicit deployment plan, ordered checksummed idempotent load plan, atomically published no-send canonical staging package, and optional atomically published no-send common CRM import package. Canonical staging preserves every ordered table and may carry optional `review_context` metadata linking the package to a reviewed visual-intake session without claiming proposal publication. The common package reshapes twelve familiar objects, accounts for all 28 canonical tables as mapped or explicitly outside the profile, and includes a tenant-completion mapping template, official-source catalog, and approval-gated write plan. Each data CSV row includes formula-safe display cells plus authoritative compact canonical JSON, exact canonical key/table controls, and its row SHA-256. Verification reconstructs exact canonical rows, binds source/load/file/package hashes, and retains zero-row findings. Both packages set live upload/write permission false; no target adapter or upload is assumed; target-specific receivers belong on the destination system side rather than in this repository. |
| 19 | `retrieval_store.py` / `retrieval_mcp.py` / `retrieval_https.py` / `retrieval_remote_mcp.py` / `retrieval_sidecar.py` / `crm_export_jobs.py` | Approved canonical export / local SQLite factual and CRM snapshot | Citation-preserving document retrieval plus a hash-bound copy of every validated canonical row, per-row and per-table integrity manifests, unique idempotency-key lookup, and cross-table FTS5 joined back to authoritative stored rows. The SQLite file is built and integrity-checked before no-replace publication. Local MCP, OAuth-protected remote MCP, HTTPS REST, and the private internal FastAPI sidecar expose the same read-only summary, schema, search, exact-record, governed query, table-export, account-card, currency-partitioned sales-analysis, and standard-report functions. The sidecar packages that semantic layer as `GET /health`, `GET /capabilities`, and POST JSON routes that return `summary`, `records`, `citations`, and optional `metrics`. The remote MCP adds bounded owner-bound CSV/XLSX jobs plus optional ZIP package jobs for approved staging and common-import artifacts: each immutable manifest binds the tenant snapshot, requester hash, request arguments, any review-link metadata, file checksum, expiry, and random download-token hash; formula-leading display values are neutralized. They never upload to a CRM. |

`scripts/run_mcp_production_acceptance.sh` is the deterministic predeployment
acceptance for this boundary. It consumes only the clearly labelled fictional
fixture under `fixtures/mcp_production_acceptance/`, writes a new output
directory, and produces `mcp_production_acceptance_summary.json` with
`schema_version: mcp_production_acceptance_v1`. The summary binds the fictional
source-export hash, canonical load plan, retrieval database, CSV/XLSX artifacts,
tool/report counts, six repository-controlled pass/fail gates, and the remaining
external deployment checks. It is acceptance evidence for code integration,
not client evidence, an approved production snapshot, or proof of a particular
DNS, proxy, identity provider, client workspace, or incident-response setup.

Do not run analytics, reporting, or an external-system load when the final gate
is `blocked_pending_client_review`. A `clear` status is only exhaustive for the
artifacts supplied to the final-review command.

## Address-validation provider contract

The address stage is local by default. Its root `.env` settings are defined in [runtime-configuration.md](runtime-configuration.md). An external call requires `GOOGLE_ADDRESS_VALIDATION_ENABLED=true` in `.env` or `--google-address-validation`, a non-secret `GOOGLE_MAPS_API_KEY`, client approval under the privacy policy, and a limit from 1 through 5,000. The provider is Google Maps Address Validation, not a general Geocoding call: it can return a standardized address, deliverability verdict, and geocode in one evidence object. The stage sends each unique raw address once per run, counts every provider attempt toward the cap, enables USPS CASS only for US/PR by default, and never stores the API key. It does not call the separate Geocoding API because Address Validation returns geocode evidence. Coverage varies by country; provider errors are retained as non-secret `unsupported_region`, `permission_denied`, `request_rejected`, or `failed` categories. A non-premise or uncertain verdict, provider failure, cap exhaustion, or conflicting existing provider proposal becomes final-review work.

## LLM adjudication artifacts

`llm_adjudication.json` contains a versioned summary, an `amendment_proposals` array, and a decision record for every supplied candidate. A candidate must name `document_id`, `page_id`, `field`, `original_value`, `candidate_value`, `original_source`, `evidence_text`, `deterministic_validation_status`, `independent_extractor`, `has_handwriting`, `is_reassembly`, and `has_disagreement`. The command refuses to query the model unless the candidate is a nonfinancial, non-address, printed, non-reassembly field; deterministic validation is clear; and the candidate exactly agrees with a named independent extractor.

A successful entry is always `decision=llm_generated_amendment_proposal`, `decision_source=llm`, `client_review_required=true`, and `disposition=llm_generated_amendment_requires_client_review`. Its audit object records the model, reasoning effort, prompt version, raw-response path/hash, model decision/confidence, configured minimum confidence, deterministic status, and independent-extractor evidence. It is not client approval and belongs in `final_review_queue.py` with `llm_adjudication_exceptions.json`. Financial fields, address corrections, handwriting, multi-page grouping, disagreements, provider failures, and below-threshold/abstaining model outcomes remain exceptions.

The companion non-secret handoff records the model, reasoning effort, credential variable name, raw-response directory, threshold, sampling rate, candidate/page limits, timeout, retries, and the hard `client_approval_permitted=false` policy.

## Output schemas

### Canonical final client-review JSON

`final_client_review.json` is an object with `summary` and `items`. This is the
only authoritative review artifact. `items` is the exhaustive unresolved queue
for the named source artifacts.

| JSON path | Type | Required | Meaning |
|---|---|:---:|---|
| `summary.schema_version` | string | Yes | Versioned output contract marker. |
| `summary.generated_at` | ISO 8601 timestamp | Yes | UTC generation time. |
| `summary.source_artifacts` | array of strings | Yes | Basenames of review-bearing artifacts included in the gate; no absolute operator paths. |
| `summary.client_review_items` | integer | Yes | Count of unresolved review items. |
| `summary.gate_status` | enum | Yes | `clear` or `blocked_pending_client_review`. |
| `summary.findings` | array of strings | Yes | Scope and gate statements. |
| `items` | array of ReviewItem | Yes | Unresolved review items; may be empty only when clear. |

A `ReviewItem` uses the following headers. The same eight fields, in the same
order, form the CSV header and the `Client Review` worksheet table.

The JSON final-review artifact also includes `review_cards[]`. A card groups
the exhaustive item indexes for one document or page, summarizes distinct
reasons and fields, and retains the highest priority. Cards reduce client task
count without deleting or resolving any underlying review item; the full
`items[]` list remains the audit record.

The optional `safe_review_consolidation.json` is a presentation-only derivative
written after the card-level reviewer. It records the source queue, visible
items, covered dependency items, exact duplicate count, cross-record proposals,
full-dataset proposals, and any explicitly enabled low-risk carry-forward
proposals. `all_source_items_retained` must be true and
`production_approval_permitted` must be false. It is never a canonical export
input and does not clear the final client-review gate. Its `review_clusters[]`
are homogeneous presentation groups for batch discussion; each records whether
the group is protected and whether a client rule decision is still required.
When grouping is enabled, `decision_groups[]` adds a broader proposal-only
surface based on document family and finding family. It is a presentation aid,
not an approval: each group retains source item indexes/IDs, records whether it
is protected, and requires an explicit client rule before any batch treatment.

A finding family is a bounded token, never a reason sentence. `audit:` reasons
were bounded after an unmatched one became its own key and produced 4,455 groups
of one; every other unrecognized reason kept that route until it started doing
the same, splitting a single cause into five families because the sentence named
the vendors and document types involved. An unrecognized reason longer than the
longest genuine family now groups as `unclassified_finding`, which has its own
question wording so those items are asked about rather than falling into the
generic prompt. A grouping artifact built before this bound carries the raw
sentences and cannot be compared against one built after it.

The optional cross-record artifact retains the provider's complete `matches`
array and separates any opt-in low-risk proposal view into
`auto_accepted_updates` and `remaining_matches`. Its admissible evidence input
is a strict producer-bound envelope:

```json
{
  "schema_version": "cross_record_evidence_v1",
  "producer": {
    "kind": "intake_manifest",
    "schema_version": "intake_evidence_producer_v1",
    "path": "ingestion_manifest.json",
    "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "operations_manifest": {
      "path": "run_manifest.json",
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    },
    "classification_exceptions": {
      "path": "classification_exceptions.json",
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  },
  "entries": []
}
```

The envelope has exactly those three fields. Both producer variants require an
exact kind-specific versioned schema, non-escaping paths, computed SHA-256
digests, and a generated operations manifest that binds every required
artifact by basename, size, and digest. `intake_evidence_producer_v1` requires
the exact complete `ingestion_manifest.json` plus
`classification_exceptions.json`. The reducer revalidates every page/state
field, retained source/page/text artifact, source hash and PDF page count, and
reruns the retained deterministic text extraction and high-signal intake
classifier. Only `pages[].document_type` with `classification_status` equal to
`rule_classified` is eligible; arbitrary page fields are rejected.

`canonical_evidence_producer_v1` requires the exact canonical export,
generated canonical load plan, clear final-review JSON, and operations
manifest. The load plan must checksum the exact export, the final-review
artifact must name that export and contain no unresolved item/card, and every
source PDF is hash/page validated. Only the documented
`tables.document[].document_type` mapping is eligible. Extra document fields,
an unrecognized handoff, or a merely self-supplied `review_status` is not
authority. Thus a tuple cannot self-attest its evidence class or review
clearance.

Every item declared in `entries[]` is evaluated and produces exactly one
eligible or ineligible count. Each eligible entry has exactly these fields:

| Field | Requirement |
|---|---|
| `record_id` | Non-empty exact producer-record identity with no surrounding whitespace. It must differ from the target review item's non-empty exact identity. |
| `field` | Non-empty source field. |
| `value` | JSON scalar fact; provider `proposed_value` is never indexed. |
| `evidence` | Exact derived locator `SOURCE_FILE#page=PAGE_RANGE#field=FIELD`. |
| `source_sha256` | Exactly 64 hexadecimal characters matching the retained source artifact. |
| `source_page_range` | Non-empty string or positive integer matching the producer row and an existing one-based page or ordered page range in the retained source PDF. |

The reducer validates each producer once, builds its identity index once,
resolves exactly one producer row, matches the row's field/value/source
hash/page, and derives the evidence locator. Retained source hashes, PDF
validity, and page counts are cached by stable resolved-file identity and
expected digest so repeated entries read and open a source at most once per
invocation. It
records both envelope and producer hashes/JSON paths plus source provenance in
`resolved_evidence_reference`. An arbitrary or unversioned JSON object, an
unbound shaped tuple, raw provider response, provider update field,
proposal-only or unapproved suggestion, unresolved review item, non-clear
canonical fact, malformed collection entry, or extra tuple field is never
indexed as proof. Every declared failure remains retained and is reported under
`evidence_context.ineligible_entries` with an immutable artifact reference and
machine-readable `eligibility_reason`.

Local evidence preflight occurs before any provider request. Each envelope and
every required producer/source artifact is limited to 10,000,000 bytes by
default, and all supplied envelopes may declare at most 10,000 total entries by
default. `--max-evidence-artifact-bytes` /
`CLIENT_REVIEW_LLM_CROSS_RECORD_MAX_EVIDENCE_ARTIFACT_BYTES` and
`--max-evidence-entries` /
`CLIENT_REVIEW_LLM_CROSS_RECORD_MAX_EVIDENCE_ENTRIES` may set positive integer
limits. A breach emits `cross_record_evidence_artifact_bytes_limit_exceeded` or
`cross_record_evidence_entries_limit_exceeded` and makes no provider call. The
reducer size-bounds, parses, and validates every outer envelope and totals every
declared `entries[]` item before it loads or hashes a producer or opens a PDF.
Every required producer, handoff, source PDF, page PDF, and retained text file
is size-checked before its first hash, open, or read. An invalid producer emits
one producer-level ineligible reference even when `entries` is empty. For a
non-empty invalid producer, every otherwise valid declared item is also retained
at its exact JSON path with `producer_not_authenticated`; malformed declared
items retain their more specific item-level reason. The producer-level finding
is additional to those item counts.

Eligibility additionally requires the shared fail-closed
`review_protection.protected()` result to be false and one exact, unique tuple
of source record ID, source field, proposed value, and evidence text. Confidence
is checked locally before threshold comparison: it must be numeric, finite,
non-Boolean, and within `[0, 1]`. The accepted view records
`proposal_only=true`; it is not production approval. Every rejected match
carries `retained_review_reason`, including distinct missing/malformed
target/source identity, same-record, confidence, protection, source-resolution,
and ambiguity reasons. Target, source, index, and resumed identities use the
same exact no-whitespace rule. A non-object provider entry is retained as
`malformed_match`. Free text that merely describes a source is not evidence.

When grouping is enabled, `safe_review_consolidation.py` also creates a small
`client_review_package/` directory beside the consolidation JSON. The client
package contains `client_review_package.xlsx` (Executive Summary, Decisions
Needed, and Representative Cards), `client_review_guide.docx`, and
`decision_pages.docx`, plus a same-named ZIP beside the directory. The workbook
has one controlled choice column for each non-protected decision group; the
decision pages provide one plain-language page per choice and repeat the exact
representative source-page reference. The exhaustive queue, covered dependency
items, raw provider evidence, and detailed drill-down remain internal in
`safe_review_consolidation.json`; no appendix or drill-down is generated for
client delivery. The package is proposal-only and importing the returned
workbook creates a new proposal artifact; it never changes source evidence or
approves a canonical fact. The issued workbook is immutable evidence and must
be preserved separately from the returned copy. `import-decisions` requires
`--issued-workbook`; it rejects a same-path workbook, missing/reordered/duplicate
groups, changed headers or fixed cells, uncontrolled choices, protected-group
decisions, alternatives without a note, and an existing output. The proposal
artifact records both SHA-256 hashes and never applies a decision directly.
Both XLSX files pass compressed/member/uncompressed-size and required-package
preflight limits before any worksheet XML is parsed.

| Header | Type | Required | Definition |
|---|---|:---:|---|
| `priority` | enum | Yes | `critical`, `high`, or `normal` review urgency. |
| `document_id` | string | Yes | Stable document/page identifier. |
| `page_id` | string | Yes | Immutable one-page PDF identifier. |
| `region_id` | string | Yes | Handwriting/detected-region identifier; blank when not region-specific. |
| `field` | string | Yes | Canonical field path or review target. |
| `reason` | string | Yes | Machine-readable reason; never a silent correction. |
| `review_source` | string | Yes | Source collection such as `exceptions` or `documents`. |
| `disposition` | enum | Yes | `client_review_required` for an open final package. |

### CSV format

`final_client_review.csv` is UTF-8, comma-delimited RFC 4180-style text with
one header row and one row per JSON `items` entry. Quote fields containing a
comma, quote, or newline. It is regenerated from JSON and must not be edited as
authoritative evidence.

```csv
priority,document_id,page_id,region_id,field,reason,review_source,disposition
critical,000001_invoice,000001_invoice,,header.total_amount,arithmetic_failed,documents,client_review_required
```

### XLSX format

`final_client_review.xlsx` is a reviewer aid. It includes a filterable review
sheet and, for self-contained delivery, a header-definition sheet, a schema
definition sheet, and a runbook sheet. Operational reviewer workbooks use these four sheets:

| Worksheet | Purpose |
|---|---|
| `Client Review` | Filterable flat view using the eight headers above. |
| `Header Definitions` | Header name, type, required status, and business definition. |
| `Schema Definitions` | JSON paths/sheet locations, types, requirement status, and definitions. |
| `Runbook` | Review and return steps plus evidence rules. |

Update workflow: edit or replace the canonical final-review JSON, then run the export command for operational output. Do not edit a reviewer workbook to close a gate.

## Clearly labelled schema samples

The schema-summary workbook also contains a **Visual Intake** sheet describing
the optional MCP/JSON API proposal operations, limits, receipts, and local
source-only handoff. It is orientation material, not a journal export or valid
proposal payload. Its approved-data API/report/CRM-import sheets retain their
separate eligibility rules; no intake row is added to their data populations.

[`examples/`](../examples/) contains clearly labelled fictional samples for the evidence-bearing extracted record and the pipeline-derived control fields. Every artifact states **SAMPLE - FICTIONAL - NOT CLIENT DATA**. They demonstrate field shapes only; they are not client evidence, calibration, accuracy evidence, or operational review output. See [`extraction-schema.md`](extraction-schema.md) and [`derived-field-schema.md`](derived-field-schema.md) for the authoritative field lists.


## Semantic schema discovery artifacts

A source-template input is an object with a non-empty `templates` list. Each
template requires a stable `template_id`, non-empty `headers` carrying the exact
`source_label`, and page/region evidence when available. Optional `entities`
may name only observed dealer, brand, customer, payer, or location candidates;
optional relationships retain the source association. `schema_discovery.py`
returns original-label mapping proposals, Dealer/Brand proposals, temporal
relationship candidates, Google Places candidate sets, a schema-change queue,
and `review_items`. All non-registry outcomes are client-review-required.

An optional `evidence_graph_schema_inventory_v1` input may be carried in the
schema-discovery request packet as bounded corpus context. A
`evidence_graph_schema_surface_inventory_v1` may instead carry both the observed
claim inventory and a separate `retained_candidate_fields` inventory built from
the exact named consensus artifact. Candidate entries retain document IDs, JSON
pointers as bounded examples plus complete counts, consensus status, and discovery topics; any email, phone, attention,
contact, or representative signals are source-retained discovery indicators,
not factual role assignments. Neither inventory creates a graph node or
canonical fact for a non-consensus candidate. Both must declare
`proposal_only=true` and `canonical_mapping_permitted=false`; its graph and
artifact hashes are retained in the output and handoff. An observed-claim
inventory over 65,536 bytes is rejected. A schema-surface artifact may retain up
to 10 MB of audit context, but only an explicit, count-preserving, 65,536-byte
or smaller reduction is admitted to a provider packet. It never substitutes
for source-template/page evidence or permits a mapping, approval, or canonical
fact.

`evidence_graph_schema_recovery_scope_v1` is a separate, source-hash-bound
selection artifact. It records operator-selected generic discovery topics
(`address`, `contact`, `party`, or `sales_representative`) and/or signal types,
the exact selected document IDs, retained candidate JSON pointers, selected raw
signals, and malformed-source findings. It is a bounded worklist for a fresh,
independently checked extraction overlay—not provider evidence, authorization,
or a graph/canonical update. Any later retry must retain its own raw responses
and reconcile append-only with the scope.

When enabled, schema discovery retains a configured-primary/configured-buddy decision
for each primary mapping proposal. `inferred_high_confidence` requires exact
template/page evidence, the configured primary confidence floor, and a buddy
`confirmed` decision. It remains `client_review_required=true`; no registry or
canonical update is created without an explicit client decision.

A reusable registry rule contains its `template_fingerprint`, exact
`source_label`, canonical field, semantic type, effective timestamp, and the
approved proposal ID. `registry-update` writes a new snapshot instead of
changing a rule in place.

Every registry rule also records **who approved it**. A decision artifact may
declare `approval_authority` (`client`, the default, or `engagement_owner`) and
`approved_by`; an `engagement_owner` decision must name `approved_by` and is
written as `status: engagement_owner_approved` rather than `client_approved`.
Both statuses are applied identically by every downstream control -- an
engagement-owner approval is a legitimate decision, not a weaker one -- but the
artifact never records the client as having made a decision they did not make,
and a `canonical_mapping` inherits the matched rule's authority. Google Places and LLM outputs are never direct CRM
loads or RAG facts; only a client-approved registry/canonical record is eligible
for those uses.

## Golden-set evaluation artifacts

The golden truth file requires unique `items[]`. Every item contains `item_id`,
`template_fingerprint`, `document_family`, `field`, `risk_category`, exact
`expected_value`, and Boolean `expected_review_required`, `protected`, and
`baseline_review_required`. `expected_arithmetic_status` and
`expected_completeness_status` are optional. The prediction file uses the same
IDs with `status` (`predicted`, `abstained`, or `failed`), Boolean
`review_required` and `auto_accepted`, and `predicted_value` for predicted
items. Optional `provider`, `latency_seconds`, `estimated_cost_usd`, and
`review_minutes` support provider and operating-effort breakdowns. Auto-accepted
items must be predicted and cannot be review-required.

The report contains thresholds, overall metrics, per-dimension breakdowns,
unexpected IDs, individual acceptance checks,
`eligible_for_client_acceptance_review`, and the hard-coded
`automation_authorized=false`. Every failed acceptance check emits a standard
blocking review item, including failures caused only by an aggregate threshold.
Both output paths must be distinct and new.

## Template drift and decision compilation artifacts

`template_drift.py` hashes canonical source-layout JSON using
`source_layout_v1`: normalized document family, ordered headers, and supplied
sections, totals, table regions, and column boundaries. IDs, provider values,
and inferred canonical mappings are excluded. Only one exact client-approved
fingerprint permits mapping reuse. Registry load and update recompute each
fingerprint from its canonical layout signature, require unique IDs and
fingerprints, and require authorized/deferred patch IDs to partition the plan.
Drift, new layouts, or ambiguous duplicates
produce standard review items; similarity is diagnostic only. Registry updates
accept only an unchanged, hash-verified operator authorization and write a new
snapshot.

`client_decision_compile.py compile` requires a complete proposal-only workbook
import, its safe-consolidation decision groups, and an explicit change catalog.
The workbook carries hidden immutable package lineage; import retains the exact
safe-consolidation hash, and compilation rejects a different consolidation,
missing group or workbook hash, choice/change-type mismatch, or an alternative
whose catalog comment differs from the client's note.
Supported append-only change types are `mapping_rule`,
`template_registry_rule`, `document_type_rule`, `amendment`,
`allocation_policy_rule`, and `source_quality_action`. All six compile and
authorize; only `template_registry_rule` has an implemented consumer
(`template_drift.py registry-update`). The other five bind a real operator
signature and then fail at apply, so choose the change type against its consumer
before compiling. A catalog change cannot
name items or documents outside its decision group; protected groups cannot
compile to batch changes. The plan records each patch, exact affected items,
documents, fields and optional amount, plus the minimum rerun lanes. It is
content-hashed, retains input and workbook lineage hashes, and is paired with a
separate default-defer authorization template.
`authorize` requires an operator ID, offset-bearing ISO-8601 timestamp, exact
plan hash, and one `authorize` or `defer` choice per patch. Its output still
sets `production_changes_applied=false`; an operator must apply the authorized
append-only configuration and complete every listed rerun and final gate.

## Layout-aware row proposal interface

A client layout contract is a JSON object containing `layout_id` and ordered `columns[]`, each with a machine-safe `key` and the exact `display_label` visible in the client layout. `layout_aware_extract.py page` consumes one immutable intake `page_id` and emits a no-clobber packet with a row-proposal record, review items, raw response, and `requires_independent_consensus=true`. `combine` joins only retained checkpoint packets into an OCR-shaped proposal handoff, a review exception artifact, and records. The layout is a display target only: it does not approve a canonical mapping, correct a source value, or permit a client review gate to close.

## Table-comprehension proposal interface

`table_comprehension.py` emits three page-bound, no-clobber role packets:
`profile`, `rows`, and `audit`. Each contains `schema_version`, `role`, `status`,
engine/model configuration, `page_id`, manifest-relative `page_pdf`, page hash,
raw response path, `same_model_roles_not_independent=true`, and a strict payload.
The profile payload identifies source-native tables; the rows payload retains only
visible source cells and their evidence; the audit payload records concerns but
does not provide replacement values. `assemble` produces:

- `source_rows`: original source cells plus a `canonical_mapping` whose status is
  only `client_approved`, `engagement_owner_approved`, or `unresolved`, carrying
  the matched rule's own `approval_authority` and `approved_by`;
- `decision_cards` and the equivalent `exceptions` array: aggregated material
  review items, never one task per row diagnostic;
- a `table_comprehension_quality_summary`: retained provider, image, and
  row-boundary diagnostics that `final_review_queue.py` must not turn into a
  client task; and
- a proposal-only adapter handoff declaring that canonical mapping is not
  automatic and independent consensus is required.

`mappings --propose-mappings` routes exact observed headers through the existing
semantic-schema-discovery proposal contract. Its output may be useful evidence
on a decision card, but it cannot be applied by `assemble`; only a later explicit
client approval and append-only registry update enables an effective mapping.

`table_comprehension_corpus.py` accepts multiple intake manifests in explicit
source order. Its mutable `corpus_state.json` is limited to resumable control
state and validates every manifest hash, model configuration, and refinement
configuration. Every source-layout context snapshot and page artifact is retained
as a new artifact. The context contains observed source layout only, never
previous model cell values or unapproved mapping proposals. Refinement output is
linked to its initial page artifact through `refinement_register.json`; a changed
cell becomes an `amendment_proposals` entry with both values and a context
snapshot reference. It cannot overwrite the original proposal, canonical mapping,
or a client decision. `corpus_exceptions.json` contains both aggregated decision
cards and refinement amendment proposals for final review.

## Control corrections applied to the critical and high findings

These contract additions accompany the corrections recorded in
`docs/DEBUG_FIX_UPGRADE_PLAN.md`. Each one exists so a control cannot report a
clean result for work it did not do.

| Artifact | Addition | Meaning |
|---|---|---|
| Evidence graph exceptions | `graph_node_property_conflict` | Two artifacts described the same node differently. The first reading is retained, the node is marked `protected`, both property sets and both provenance entries are kept, and the build continues. A disagreement between a consensus record and its validated counterpart is the signal the graph exists to surface, so it must never abort the build. |
| Evidence graph exceptions | `graph_source_artifact_contributed_no_nodes` | A hash-verified artifact yielded no nodes. `build` refuses a graph with no nodes at all unless `--allow-empty-graph` is passed, so "no contradictions found" stays distinguishable from "nothing was loaded". |
| Evidence graph edges | Content-addressed merge | Two artifacts asserting the same relationship produce one edge with both provenance entries. Differing approval states degrade to `proposed` and `protected`. Duplicate edge IDs would otherwise make the graph fail its own `load_graph` integrity check. |
| Evidence graph provenance | Container-derived `json_pointer` | The pointer comes from the code that selected the record container, so a top-level array cites `/0` and a `results` container cites `/results/0`. A pointer that does not resolve in the cited artifact is worse than none. |
| Consensus field | `consensus_<n>of<m>_below_required_<r>` | The readings agreed but fell short of the governing rule, such as a unanimous pair against the handwritten-numeric 3-of-3 requirement. `accepted` was already correct; the flag no longer reads as a pass. |
| Consensus field | `derived_ordinal` / `derived_row_position` | A row's `line_number` is its position in the table, counting from 1, and nothing reads it off the page. Putting it to a vendor vote produced 4,086 blocking gate findings on one run -- 5% of the whole client gate -- none of which protected a value, and most of which were an engine that had filled the field with the row's invoice number instead. It is derived from the row index, accepted, non-blocking, and never labelled `consensus_*`, because no vendor agreed to anything. Both engines' readings are retained under `engine_readings` as provenance, so an engine answering a different question is still visible. |
| Consensus field | punctuation-insensitive text agreement | Two engines reading the same printed prose differ on separators constantly: `Northgate & Co` against `Northgate Co`, `murb rook` against `murbrook`. On one run 303 blocking findings were exactly that. For a non-numeric, non-identifier field the readings agree once punctuation and spacing are removed. It is **not** applied to numbers, and not to anything matching `IDENTIFIER_HINTS`: `001` against `1` is a real disagreement about an invoice number, and a hyphen inside a reference code can be load-bearing. |
| Shared monetary parsing | `separator_convention_unclear` | `16 430,72` is sixteen thousand four hundred and thirty. Stripping the space and the comma the way a US-format reading does makes it 1,643,072 -- a hundred times the money, and on one corpus it produced the largest value in the run at $4.25M against a median of $2,604. A comma followed by one or two digits at the end is a decimal comma, and whitespace between two digits is a group separator no US-format number uses. Either signal means the convention cannot be decided from the string, so the parse is refused rather than guessed, in line with rule 10: never infer a unit or a convention from magnitude. The export's typed companion (`field_extract_export.py` `loadable_amount`) reads a decimal comma only where the document settles it -- see *A document settles its decimal separator* -- and this shared parser still refuses. |
| Consensus handoff identity | `model_vendor` | The vendor resolved from the model slug, seeing through a router. Two lanes reaching the same weights by different transports are one reading; a routed slug with no resolvable vendor is refused. |
| Completeness `gl_variance` | `gl_rows_rejected`, `rejected_gl_rows`, status `completed_with_rejected_gl_rows`, gate reason `gl_rows_rejected` | A GL row that cannot be bucketed or whose amount cannot be parsed is retained with its raw content and a reason code. While any row is rejected the baseline is incomplete, so no period is declared within tolerance. A dropped GL row shrinks the authoritative total and therefore the reported variance -- the one direction this control must never move on its own. |
| Completeness report | `inference_coverage` | Every vendor series and vendor-month not analyzed for gaps, with its reason. "No gaps found" does not cover an unanalyzed population. Series group by `(vendor, prefix)` rather than zero-padding width, so a rollover from `INV-999` to `INV-1000` stays one series. |
| Attribution summary | `reference_rows_ingested`, `reference_rows_rejected`, `rejected_reference_rows` | A reference-export row with no recognized key column is registered rather than dropped. The document side of this phase already enumerates every unattributed dollar; the reference side now matches. |
| Handwriting financial escalation | `financial_amendment_supplied` on each annotation; `financial_basis` on each amendment proposal | `financial_amendment` is copied from the supplied region and never derived -- this lane reads where a detector pointed and has no basis to decide what a mark modifies. No detector on the commission run set it, and `semantic_type` came back `handwritten_text` for all 3,319 annotations, so both signals were always false and 501 numeric handwritten readings were treated as ordinary text against a recommended review threshold of $0. An untyped numeric reading now routes to review on its own, recorded as `financial_basis: untyped_numeric_reading` -- it is not thereby an amendment, it is escalated because it could be one and nothing establishes that it is not. `financial_amendment_supplied` separates "no financial handwriting" from "nobody told us". The escalation is downstream of the independence gate: with one provider group every reading fails that gate first, so a second HTR independence group is required before it can fire at all. |
| Resolved party canonical name | `--canonical-name NAME` (repeatable); `spacing_variants`, `canonical_name_declared` | The survivor is chosen by frequency, and frequency chose an OCR artefact: a supplier appeared as `murb rook` on 147 documents against 136 spellings of `Murbrook`. The obvious repair -- prefer the spelling without the internal space -- is worse, because `Lumen Weft` appears 321 times against 11 of `LumenWeft` and `Marlow / Bramwell` 671 against 67 of `Marlow/Bramwell`: it would corrupt two names to fix one. A space wrongly inserted and one wrongly dropped are both OCR damage and are indistinguishable from inside the corpus, so the code keeps frequency and reports every cluster where the question arises. A declared name wins outright, matched ignoring case and whitespace. |
| Engagement party as supplier | `--engagement-party NAME` (repeatable); finding `engagement_party_named_as_supplier` | A representative agency appears on every page of its own commission statements, and an extractor with no notion of whose engagement this is reads that name into `brand_name` as readily as the manufacturer's. On the commission corpus the agency sat in a supplier field on 142 documents and was silently the dataset's largest brand. The finding is raised, never removed: the extractor read what was on the page, and which name is the supplier is the client's statement rather than this control's inference. |
| Client review worked examples | `--scan-profile`; `examples`, numbered example sheets | A page `scan_profile.py` flags `near_blank` is never chosen as a question's worked example, and never as the one that leads. A client shown a blank page and asked to confirm a layout answers about the blank page: one such question covered 285 documents, of which 4 were blank and 194 carried more than fifty populated fields, and the answer was given and reaffirmed twice. A group that is genuinely all blank still shows one page, because a question with no evidence is worse. `near_blank` is a quality warning and not a disposition -- its 0.002 floor also flags a rotated commission report with a full table. |
| Table-audit grouping | `grouping.AUDIT_CONCERNS`; families prefixed `audit_` | The audit lane writes a sentence per cell and every sentence differs: 11,251 findings carried 11,242 distinct reason strings, so grouping on the reason produced 4,463 families of which 4,455 held one item, and a third of the review queue arrived as prose to be read one at a time. Audit findings are classified by concern instead, into nine families and a fallback, and the concern is tested before any rule that merely looks for a word like "provider" in the prose -- 6,355 findings had been grouped as provider exceptions on that basis alone. On the commission corpus the whole decision surface falls from 4,560 questions to 137, and two of the nine concerns are questions the client had already answered. |
| Client review question identity | `what_we_think_these_are`, `fields_in_question`, `documents_in_question`, `documents_in_question_total` | A question named neither its documents nor its fields. One covering 360 documents and 4,393 findings read "Confirm what the columns on these documents mean" and gave a single example id -- while the group it came from already carried the document type, the column captions the mapping lanes derived, and every document id. All three are now rendered in the workbook and the instructions PDF; a question spanning more than a dozen documents names the first twelve and points at the queue tab for the rest. |
| Lane answers to the authorization chain | `client_review_lane.py compile-inputs`; `safe_review_consolidation_v1` and `client_decision_import_v1` written from the pack and its answers | There were two ways into client review and only one reached authorization. `client_decision_compile.py` takes a safe-consolidation artifact and a workbook-import proposal keyed to its hash, both from the consolidation path; the review lane produced neither, so a client could answer every question correctly and none of it could be compiled, authorized or applied -- 23 answers over 27,855 items with no route. The groups emitted are the pack's own, the decisions the client's own, and the lineage hashes those of the exact workbooks issued and returned. Every group carries a decision because the compiler requires the counts to match, so an unanswered group is an explicit empty decision rather than an omission. `protected` is copied from the group, never asserted. This authorizes nothing: the compiler still validates and an operator still signs. |
| A client correcting their own earlier answer | `--supersede-prior-client-decisions`; `superseded_prior_client_decisions`; `summary.superseded_prior_client_decisions`; `summary.reaffirmed_prior_client_decisions`; `accepted[].supersedes_authorization_id` | `classification_amend.py` refused any document already accepted, saying consensus had accepted a type. For 136 documents on the commission run that was untrue: their type came from the client's own round-1 decision, `agreeing_vendors` empty and evidence `operator_authorized_client_decision`. The client's round-3 answer retyped them `payment_record` and `weekly_order_status_report`, and the chain refused the correction it exists to carry, after the operator had signed. The two cases are now separated: independent vendor agreement is never superseded, with or without the flag; an earlier client decision is superseded only with it. The refusal without the flag names the whole set, the prior authorization, and the retyping it would perform, so an operator decides once rather than once per document. The earlier acceptance is retained unedited and the correction beside it names the authorization it supersedes, so `summary.accepted` counts documents while the entry list keeps both. A repeated answer appends nothing and is counted separately -- reaffirming and correcting are different events. |
| Every acceptance lane is registered by name | `field_extract_export.py` `ACCEPTANCE_STATUS`, `CONFIDENCE`, `UNREGISTERED_ACCEPTANCE`; `ACCEPTED_STATUSES` derived from `CONFIDENCE`; `summary.fields_accepted_by_an_unregistered_lane` | `field_status` tested for one `accepted_by` and returned `accepted_vendor_agreement` for every other acceptance, so each lane added after it reported its acceptances as two independent model vendors reading the same value. Document arithmetic, same-document settlement and the extractor tiebreak were all added after it: on the commission run that claimed 38,446 vendor agreements where 34,880 existed, overstating 3,566 fields in the one direction a client must never be misled. Acceptance is now a table keyed by the `accepted_by` each lane writes, `ACCEPTED_STATUSES` is derived from it rather than maintained beside it, and an `accepted_by` absent from the table is named `accepted_by_an_unregistered_lane` and counted in the summary instead of being folded into the strongest bucket. Registering a lane is one line; forgetting to costs visibility rather than truthfulness. |
| Corroborated acceptance is not vendor agreement | `field_extract_export.py`; `accepted_independent_corroboration`; `rows[].accepted_by`; `fields_corroborated`; `corroborated_fields` | An acceptance carries how it was earned, and the two ways are not the same claim. `apply_corroboration.py` accepts a value one model read where an independent non-LLM extractor read the same value on the same page -- 47,578 fields on the commission run, under a named client decision. Reading only `accepted` reported every one of them as two model vendors agreeing, which is the single thing the corroboration artifact says of itself that it is not: presence on the page defeats a misread and not a misattribution. They now carry their own status and `accepted_by`, and a pivoted record counts `fields_corroborated` beside `fields_vendor_agreed` rather than merging them, because a merged column would let a page-presence match read as agreement. |
| A rare spelling absorbed into a common one | `entity_resolve.py --absorb-ocr-variants RATIO`; `absorb_ocr_variants`, `kept_apart_by`, `FRAGMENT_OF_A_COMMON_NAME`, `MIN_ONE_EDIT_NAME`; `ocr_variants_absorbed`; `summary.absorb_ocr_variants_ratio` | The standing rule reports a one-character difference rather than merging it, because merging on a single character is how a separate subsidiary is absorbed into its parent. That holds where the names are comparably common and reads badly at 60 mentions against 1. The merge is therefore gated on frequency as well as similarity: one character apart, or every word of the rare name appearing in the common one, **and** a mention ratio at or above RATIO. Direction matters -- `murbrook ESPACE DE JOUR` carries words `murbrook` does not and is a product line, not a misreading. Off unless asked for, since it is a judgement about one corpus. Three narrowings came from the commission run, where 10:1 first absorbed 30 names. A fragment is absorbed only when no other party's name also carries every word of it: `The Design`, `Furniture (` and `Workplace` sit inside several names, and each had gone into whichever was most common. One letter apart counts only in names of six characters or more: `XTK` went into `XTB, LLC`, a different firm. And it runs after `--party-decisions`, never joining a pair a decision keeps apart or a branch to its parent: run first, it folded `Office Quarters Inc` into its own New York branch and left the decision linking them nothing to link. So narrowed, and with the run's decisions applied first, it absorbs ten -- `Lumen Wefy` and `Weft` into `Lumen Weft`, `Bramwell` into `Marlow / Bramwell`, `Lindew World` into `Linden World`, and cut-off spellings such as `AVANNA BUSINESS INT`. Each is retained with both counts and the ratio that justified it, and the surviving party keeps the absorbed spelling in `name_variants`, so every merge is auditable and reversible. |
| A person's decision about a pair outranks a score | `entity_resolve.py --party-decisions PATH`; `PARTY_DECISIONS`, `load_party_decisions`, `apply_party_decisions`, `settle_pending`; `party_decisions_applied`, `party_decisions_unmatched`, `removed_as_not_a_party`; `parties[].parent_party_key`, `parent_name`, `branch_party_keys`; merge log `settled_by_decision`; `summary.party_decisions_authorization`, `party_decisions_supplied`, `party_decisions_applied`, `party_decisions_unmatched`, `parties_removed_as_not_a_party`, `pending_settled_by_decision` | Similarity can only propose, and the pairs it holds between the review band and the threshold need knowledge the corpus does not carry. On the commission run 45 sat there: a company and its branch, two firms in one cell, a job site with a PAID stamp, a firm a public record identifies. A decision file names the authorization it rests on and each decision its evidence. `same_party` folds the less-mentioned party into the other and keeps every spelling; its name may be declared only as a spelling one side prints, so a public identity sits beside the reading rather than replacing it. `branch_of` keeps both accounts and links the branch to its parent -- merging a branch loses which site bought what, and leaving it unrelated loses the company's total. `different_parties` keeps two apart; `not_a_party` removes a project or site printed in a party column and retains it. Decisions apply merges first, whatever the file's order, so a branch hangs from the party that survives. Anything the resolver cannot apply is reported with its reason, never guessed: a name that resolved to no party, a declared name neither side prints, or a pair clustering already joined -- which is reported rather than split, because splitting needs the mentions and a person should see it first. On that run 147 decisions (96 same party, 43 branches, 3 kept apart, 5 not a party) took the master from 880 parties to 774 and pending adjudication from 45 to 3: the adjudicated pairs, and duplicates clustering never paired -- names cut off with one completion, split notes and order references appended to a name, branches by city, placeholders and column labels. The first file listed three merges before the ones that gave their declared name a printed spelling, and the refusal caught one that would have folded a branch into its parent. |
| Resolved party on the exported row | `field_extract_export.py --parties`; `rows[].resolved_party`; `rows[].party_key`; `<column>__resolved_party` and `<column>__party_key` on a pivoted record; `summary.party_master_consulted`; `summary.rows_with_a_resolved_party`; `entity_resolve.py` `refused_not_a_name`; `summary.mentions_refused_not_a_name` | `entity_resolve.py` collapses spelling variants into one party, but writes a master *beside* the records rather than into them, so an export reading the records alone still showed `NORTHGATE`, `NORTHGATE CO LLC` and `Northgate Co.` as three suppliers -- 69 raw brand strings on the commission run's document rows, resolving to 12 parties. The reading is never overwritten: the resolved name sits beside it, because the cell is what the page said and grouping by the raw column is the mistake resolution exists to prevent. Only the party fields resolve; a description matching a variant string is not a supplier. Separately, a mention carrying no letter is refused as a name: `7144`, `25556` and `0` each became a party, and then a CRM account with its own concentration figures, because a field that should hold a supplier held an account number. Refused mentions are retained with the field they came from -- a number in a name field is a mapping finding, not a supplier -- and both chains on that run carried them (current 44, three-vendor 62). The master's key sits beside the name on a pivoted record as `<column>__party_key`, because a name is not an identity: accounts built by matching the resolved name against the master matched nothing. |
| A heading, a placeholder or an address is not a party | `entity_resolve.py` `NOT_A_PARTY_NAME`, `TOTAL_LABEL`, `SIGNED_PERCENT_FIRST`, `is_a_label`, `is_a_street_address`; `refused_not_a_name[].reason` one of `party_name_carries_no_letter`, `party_name_is_a_label_or_placeholder`, `party_name_is_a_street_address`; `summary.mentions_refused_by_reason` | A CRM built from the commission run's export carried `COMPANY` as an account -- the first column heading of Marlow/Bramwell's weekly order report, read as the dealer on 11 lines -- with `INSTALL REP`, `None`, `n/a` and `TBD`, and the client's own street address, read as a payee. Each had resolved to a party because the only test was that a name carry a letter. A whole name that is a heading, a role or a placeholder, and a name that starts with a street number and carries a street word, are now refused like a number: retained with the field they came from and the rule that refused them, never resolved. A total row's label -- `Total Commissions to date:`, `Total :` -- and an adjustment that starts with a signed percentage -- `-70% Specify Territory`, a territory split Marlow/Bramwell prints as a line of its own -- are refused the same way, as is the heading `DEALER OF`; each had resolved to a dealer. A colon alone makes no label, so `THE COVE:` is still a customer. A name that only contains such a word -- `LEBNER+COMPANY` -- is still a name, and a location key is still lifted, so `7144 KD Frost` is an account at location 7144, not an address. Territory codes are deliberately not refused: `170-NJM` and `010-PDR` are real rep firms, and `150-NGC` is the client, which is declared with `--client-name` rather than guessed. |
| A row says which of its fields are not what their column claims | `field_extract_export.py` `mark_party_fields_not_a_party`, `mark_person_fields_not_one_person`, `person_reading_problem`, `PERSON_FIELDS`, `ROLE_WORDS`, `add_email_columns`; `party_fields_not_a_party`; `person_fields_not_one_person`; `<column>__email`; the party master's `removed_as_not_a_party`; `summary.<grain>_records_with_a_party_field_not_a_party`, `summary.<grain>_records_with_a_person_field_not_one_person` | The resolver keeps its refusals beside the records, and a mapping that builds records from the raw columns never sees them. On the commission run a CRM loaded 50 accounts that included the client five times, a column heading, a code and an address, and 37 contacts that were mostly a HALVOR territory code, a report's name and form date from its footer (`COMMREP`, `Sept 97`), a role, brands and the client. The row now says so. `party_fields_not_a_party` names each company field whose reading the resolver refused, and why, matched on the document, the header or line it read, the field and the reading; the client keeps its own flag, `fields_naming_the_client`. A name a party decision removed -- `Unknown Specifier`, on 84 lines -- is named too, as `removed_as_not_a_party_by_decision`, matched on the document and the reading, because the decision is about the name rather than the field. `person_fields_not_one_person` names each person field that carries a number, a single word, a role or report word, the client, a brand from the party master, or several people -- a brand only, so a person printed in a dealer field elsewhere still reads as a person. `<column>__email` holds the reading only when it is an email address: six HALVOR pages print the company's website where a contact's email goes. Every reading stays; these are flags and a typed column for the importer, never corrections. The JSON is written after every CSV, so its `summary` carries each grain's counts; written first, as it was, it carried none of them. |
| A dealer's location is a proposal from its name | `party_locations.py` `select`, `address_like`, `cut_off_names`, `headers`, `search`, `accounts_for`, `acceptable`, `best`, `rescore`, `MATCH_THRESHOLD`, `MIN_SINGLE_WORD`, `LOOKUP_ROLES`; `party_public_sources_v1` with `provider: google_places`; `lookups[].candidates`, `lookups[].raw`; `rescored_from`; `countries`; `summary.requests_sent`, `summary.not_asked_over_the_limit`, `summary.passed_over` | Most dealer names on the commission corpus print no address, and a territory question asks where each dealer trades. Places can answer from a business name alone, which is also why the answer is only probable: a name match is not proof of a legal entity, a trading name, a relationship or the site that bought. The lane asks about dealer and brand names only, once each within a per-run cap, and keeps every candidate Google returned with the raw response. It sends no name that reads as a place -- one starting with a number, pairing a number with a street or suite word, or only a city and a state -- because a project's building is not a business and the lane sends no address; the commission run's first request set sent seven such names, among them `One Bayfront Suite 540`, before this rule existed, and the second accepted `Miami, FL` as a furniture warehouse before the city-and-state form was added. A name the page cut off -- another name continues it mid-word, or it is one word several firms begin with -- is not asked about either, since Google would describe whichever business it ranked first. Name similarity alone decides badly in both directions: it scored `KD Frost` at 0.38 against `KD Frost Architectural Interior Supply` and `BGE Contract Furniture de Puer` at 0.81 against the New York firm. So a candidate is accepted only when it accounts for every word of the party's name -- the first word, the business, in the candidate's own name, and each other word there, as the start of one of its words, or as the place it trades -- with a one-letter word needing a whole word, a lone word needing six letters and a close match, and no place-like name on either side: the project `Alhambra Plaza` is not a shop whose address is on Alhambra plaza Blvd. `--countries` keeps a candidate elsewhere on record without accepting it. Because every candidate is kept, `--from-lookups` decides again without a request: that run's first 150 requests yielded 37 matches under the similarity rule and 53 under this one; 310 more gave 77, of which the operator left four out as doubtful, and `augment_export.py --external` placed the 125 beside the readings as 5,563 identity cells, never over them. Credentials are the restricted key or Application Default Credentials, and neither is written to the artifact. |
| A value the source never rendered is out of range, not empty | `field_extract_export.py` `UNREADABLE_SENTINEL`, `unreadable(...)`, `mark_unreadable_in_source`; `unreadable_in_source`; `-99999` in `<column>__amount` | Part of the commission corpus is a spreadsheet printed too narrow, so Excel rendered `########`, collapsed a number into scientific notation, or cut a value off. The engines read those marks correctly and the date or amount is simply absent from the page, so no re-extraction recovers it. An empty cell asks a loader to notice an absence and a zero is a plausible amount; `-99999` is neither, so a load that ignores the flag still fails loudly. 728 line rows on that run. It is deliberately not written into a date companion: a column typed as a date cannot hold it, and that trades a detectable gap for an unparseable one, so `unreadable_in_source` names the date column instead. The reading the page printed is retained beside it, unchanged. |
| A count is typed like money, and a year the cell cut short is refused | `field_extract_export.py` `COUNT_COLUMNS`, `loadable_count`, `FOUR_DIGIT_YEAR`, `loadable_date`, `UNREADABLE_IN_SOURCE`; `quantity__amount` | `crm_input_validate.py` expects every numeric target column to arrive as `<column>__amount`, and `quantity` -- a count on 2,774 line rows of the commission run -- had none, so a loader either read printed text into a number column or dropped it. The count now loads as a number beside the reading, with thousands separators removed and nothing else guessed: a value that is not a plain count stays empty, and one the page masked carries `-99999` like a masked amount. Four dates carried a year of fewer than four digits -- `0122-05-24`, `03/17/202` -- because the cell cut the century off, and the ISO companion loaded them as the second century. A year below 1000 is now refused, leaving the companion empty and the reading unchanged, and a run of asterisks counts as an unrendered cell like a run of hashes. Validation findings on the run's export fell from 90 to 85: the four impossible years and the untyped count. |
| A month named without a day loads as a month, and a weekday or a time of day does not hide a date | `field_extract_export.py` `date_text`, `month_number`, `loadable_month`, `WEEKDAY`, `TIME_OF_DAY`, `MONTH_LABEL`, `ISO_DATE_RUN_TOGETHER`; `<column>__month`; `crm_input_validate.py` `date_year_issues`, `DATE_COMPANIONS` | Of 953 date cells left without an ISO companion on the commission run's export 50, 428 named a month with a four-digit year and no day -- `December 2025`, `Mar 2024`, 282 of them transaction dates -- and a statement's period is exactly that. They now load as `<column>__month` (`YYYY-MM`) beside the reading, with the ISO companion still empty, because a month is not a day. A two-digit year stays empty: `December-24` is as much the 24th as 2024, and nothing in the cell says which -- unless an operator's named authorization (`--two-digit-year-months`, `MONTH_LABEL_TWO_DIGIT`, `summary.<grain>_months_from_two_digit_years`) reads Excel's `mmm-yy` as a month of this century. `May 31` has no hyphen and is a day, and a year after this one is implausible for a statement; both stay empty. A weekday before a date and a time of day after it are set aside before the date is read, since neither states an order: `Fri, Nov 4, 2022, 9:26 AM` is 2022-11-04, and `9/23/22, 8:50 AM` now counts toward its document's own order the way `11/16/2022` does, while `7/12/2023 2:44 PM` stays as ambiguous as `7/12/2023`. `2023-0430` is the ISO order with its second hyphen lost; `2026-02-29` is no date. `crm_input_validate.py` holds a typed month to the same year test as a typed date: the check had matched the ISO companion by its suffix, so the month companion escaped it. |
| A document settles its decimal separator, and a rate is typed only in its proven unit | `field_extract_export.py` `DECIMAL_COMMA`, `DECIMAL_POINT`, `bare_amount`, `loadable_amount(raw, convention)`, `separator_convention`, `separator_convention_by_document`, `PERCENT_COLUMNS`, `loadable_percent`, `add_percent_columns`; `<column>__percent` | murbrook prints its two Canadian commission statements `2 199,03`, `14 460,05`, `219,90` and never a decimal point, and all 40 of those amounts loaded as nothing, because a decimal comma was refused everywhere. A document that prints only decimal commas has stated its convention the way one printing `11/16/2022` has stated its date order, so there the shape that decides it -- exactly two digits after the comma, any grouping before it in threes -- is typed, and any other comma is refused, since `1,446` could then be either. Elsewhere it is refused as before: `12,34` among decimal points is a cell Excel cut short. On that run the 40 amounts type and 16 more lines reconcile, with the page review and the validation findings unchanged. A rate is typed only in the unit the document proves: a `%` on the page, or, for the commission rate, a line whose own base x rate / 100 reconciles. `<column>__percent` holds the number printed, never converted to a fraction, and a bare `1.00` on a line that does not reconcile stays empty: 4,903 of 5,068 line rates type, and `tax_rate`, which has no arithmetic here, types only with its `%`. `exchange_rate` is a factor and is left alone. |
| A line is labelled by its own arithmetic and carried either way | `field_extract_export.py` `commission_reconciles`, `mark_commission_arithmetic`; `commission_arithmetic` one of `reconciles`, `does_not_reconcile`, empty | `commissionable_amount x stated_commission_rate` must be `commission_amount`, allowing for a rate the document printed rounded -- `2.3751%` prints as `2.38%` and an exact comparison calls a correct line wrong. 96.7% of the 4,831 provable rows on the commission run reconcile. The 158 that do not are carried unchanged and labelled: most are the document contradicting itself, and page 6 prints `7,646.16 x 8% = 2,038.98` while every other row on it is right. Correcting a printed number to satisfy a check would be inventing evidence, and dropping the row would hide a real reading; a row missing a term proves nothing and is left unlabelled rather than called a failure. |
| The document's own totals do not become line items twice | `field_extract_export.py` `SUBTOTAL_LABEL`, `TOTAL_BY_POSITION`, `SUBTOTAL_TEXT_COLUMNS`, `SUBTOTAL_PARTY_COLUMNS`, `TOTAL_ROW_IDENTITY_COLUMNS`, `names_its_own_line`, `mark_subtotal_rows`; `restates_a_total` | A statement prints `P.O. Total:` and `Total Commissions to date:` as rows of the same table, so they extract as lines, correctly, and carry a commission like every other row. Summing the line grain then counts that money twice. A total row need not name its total: a bare `Total :`, `Total - Acoustics`, `Northgate Co LLC Total` and `Purchase Order Totals` are known by where the word sits -- alone, opening the cell on a separator, or closing it -- while a cell that merely begins with it (`Total Station Cabinet`) is a name. Each cell is read on its own, including the customer and dealer columns, where the label lands when the row's first cell is blank. A label known only by position, or found in a party column, counts only on a row that names no job, order or item of its own: the same shapes turn up written into a real line -- `Total - Other` as two sales orders' category, a `Total Commissions to date:` pasted from the next row onto a real job -- and flagging that line hides money the page prints. The phrase list alone labelled 204 rows carrying $429,484.61 on the commission run and missed 90 carrying $1,226,621.88, among them a statement's grand total of $148,247.29 on a page whose own lines come to $25,415.42; 294 of 9,144 line rows are now labelled, and over all 716 pages checked by `page_review.py` eleven that disagreed with their image reconcile and none newly disagrees. The row is a true reading and stays; a document's total must exclude it with the other not-a-line flags. `repeats_a_line_in` is not one of them: that row is a real line, and keeping it is the importer's decision. |
| A page review becomes proposals, never a correction | `page_review.py` `--proposals`, `proposals`, `propose`, `cell_proposal`, `correction_evidence`, `not_printed_evidence`, `lost_evidence`; `page_review_proposals_v1`; `decision_mode` always `proposal_only`; `corrections[].evidence` one of `extractor_text_and_engine`, `extractor_text`, `engine`, `second_vendor`, `reviewer_only`; `rows_not_printed[].evidence` one of `extractor_prints_it_fewer_times`, `second_vendor_prints_it_fewer_times`, `extractor_prints_it_as_often`, `no_extractor_response`, `no_money_on_row`; `lost_values[].evidence` one of `extractor_saw_it`, `extractor_missed_it`, `no_extractor_response`; `--second-read`, `--reviewer-vendor`, `load_second_read`, `second_read_correction`, `second_read_row`; `corrections[].second_read`, `rows_not_printed[].second_read`; `second_read` (`handoff`, `engine`, `vendor`, `reviewer_vendor`, `pages_read`, `pages_with_no_line`) | A reviewer reading a page image is one model reading it, so what it finds is a proposal until an operator authorizes it, and each proposal carries the evidence beside it. `extractor_text` means the figure appears somewhere in the independent extractor's text of the page -- not necessarily in that cell; `engine` means a retained engine read the cell that way while consensus chose otherwise. Each page's text comes from the response that read it, a retry keyed to its own page: filed under a page called `retry1`, the 222 pages read only in the extractor's retry had no extractor evidence at all. A page the extractor never read is `no_extractor_response`, never a page that printed nothing: counting it as zero backed 78 exclusions on the commission run with evidence that existed for 11. Only money columns are proposed, because a reviewer's value for a text column is prose as often as a reading. A second read is another vendor's reading of the same page, never the reviewers' own vendor, and it regrades only what the reviewer alone supported and the rows the extractor prints as often as the export. A correction becomes `second_vendor` where the reading holds the figure in its column more often than the export's other lines. A row becomes `second_vendor_prints_it_fewer_times` only where the reading holds every other figure in the column as often and this one fewer times: on the commission run Gemini read 66% of the primary engine's values over the 47 pages it read, so a figure it lacks is as likely missed as absent. Nothing is applied, and no downstream command reads the artifact. |
| An amendment from the page review keeps what it replaced, never moves half a row, and never breaks a line's own arithmetic | `page_review_amend.py` `amend`, `plan`, `line_arithmetic`, `amend_field`, `INDEPENDENT_GRADES`, `ARITHMETIC_COLUMNS`; `page_review.py` `reviewer_value`, `lines_holding`, `corrections[].moves_from_lines`; `page_review_amendments_v1`; `amendments_applied`; `skipped.breaks_the_line_arithmetic`; `changed_since_the_review`, `skipped.the_field_changed_since_the_review`; `INDEPENDENT_GRADES` including `second_vendor`; `accepted_by: amended_from_page_review` → `accepted_page_review_amendment` → `corroborated`; `superseded_consensus` | A reviewer's reading becomes a change only under a named authorization and only with evidence independent of the reviewer; `reviewer_only` is not a selectable grade. Unlike corroboration, an amendment does overwrite an accepted value -- a misread the engines agreed on, or a figure from the wrong column, is what reading the page catches -- and the replaced value, the lane that had accepted it and every candidate stay on the field. A reviewer's fix written as a figure and a note (`799.67 (1,404.13 is job 13046's)`) is read for the figure; a token counts only with cents or a thousands separator, so `2 rows: ...` never reads as 2. A correction whose figure another countable line still holds names that line in `moves_from_lines` and is applied only when that line is corrected in the same run: applying one half of a shifted row counted $1,404.13 twice on one page. And a line whose own base x rate = commission holds is never amended into one that does not: the independent evidence proves a figure is printed on the page, not on which line. The first run on the commission corpus applied 455 amendments and the page-level score rose from 630 to 653 of 716, but 14 lines that had reconciled no longer did -- on p0430 a row the engines read twice put every later correction one line off -- and the score compared each page's amounts as a set, so the shifted figures still matched. With the guard, 434 amendments apply, 19 are refused for the arithmetic they would break and 29 moves wait for their other half; 650 pages reconcile, and no line that reconciled before fails after. A correction names the value the reviewer saw in the export (`export_value`); a cell that now holds neither that nor the page's value has been changed since, and on a re-read page its index may name a different row, so the correction is refused rather than written over what the cell holds now. |
| A row the page review says the page does not print is labelled, never dropped | `field_extract_export.py` `--rows-not-printed`, `--rows-not-printed-authorization`, `label_rows_not_printed`, `ROW_NOT_PRINTED_GRADES`; line column `page_review_row_not_printed`; `summary.rows_not_printed_labels` (by grade, `row_changed_since_the_review`, `no_such_line`); `summary.rows_not_printed_authorization` | A reviewer saying a row is not on the page is a claim, and a row is evidence of money someone was paid, so the row stays and a label carries the claim with its grade: the extractor prints the figure fewer times than the export counts it, the row carries no money, or a second vendor's reading holds every other figure in the column and this one fewer times. Where the extractor prints it as often, two sources disagree and nothing is labelled. A proposal names a line by its index in the export the reviewer read, so a line no longer holding the figure the review named is counted and left unlabelled. It runs under a named authorization, refuses proposals naming no row at a labelled grade -- a label lane that reads nothing has not run -- and needs the line grain. |
| A column filed under the wrong field is re-filed whole, and never onto another reading | `column_refile.py` `load_rules`, `refile`, `build_report`; `column_refile_report_v1`; `readings_refiled`; `refiled_from.field`, `refiled_from.authorization`, `refiled_from.evidence`, `refiled_from.target_was`; `moved[].candidate_values`; `refused[].reason: the_target_field_holds_a_reading`; `pages_not_in_records`; `summary.column_refile` | Two engines agreeing on a filing is not evidence about the field. Lumen Weft's quote and sales reports print a Specifier column and no dealer column, and the commission run's export filed 81 of its values -- Norcross, Haskins & Hill and Glade Architects among them -- as the dealer. A re-file is a mapping amendment: under a named authorization and with its evidence cited, the reading moves to the field the page's heading names and keeps everything it carried, and the field it left is removed rather than emptied, because an empty dealer claims the page printed none. An empty field object it replaces is kept in `target_was`. A reading is a settled value or the candidates the engines left unsettled: either moves from the source, and either on the target refuses the move -- one engine alone read 22 of the Lumen cells, and a test on the value left them all under the dealer. A target already holding a reading is refused, since two readings of one cell is review; a named page the records lack is reported; a run that moves nothing fails rather than reading as clean. |
| A record's fields and its views stay in step | `consensus.py` `refresh_views(document, retired)`, `views_out_of_step`, `view_slot`, `view_cells`; `records_in_step.py` `reconcile`, `measure`, `carry_mappings`, `--check`; `records_in_step_report_v1`; `documents_out_of_step`, `cells_brought_into_step`, `behind_by_acceptance`, `mappings_carried_into_fields`, `mappings_by_column`, `view_entries_kept`; `carried_from_view`; `summary.records_in_step`; `apply_mappings.py` `mapped_field`, `MAPPING_ACCEPTED_BY` | A record carries each reading flat in `fields`, which the export reads, and nested in `header`, `lines` and `accessorials`, which attribution, arithmetic, valuation, entity resolution and the canonical export read. On the commission run `multi_engine_vote.py` and `arithmetic_reconcile.py` wrote 4,099 accepted values to the fields alone and `apply_mappings.py` 393 approved mappings to the header alone: rebuilt over the fields alone, attribution fell from 202 documents to 173, and the export carried none of the mappings. Every writer now calls `refresh_views`, which replaces each view cell a field names and keeps every other -- a mapping or a flag written to a view alone is not lost when another field changes -- unless the lane that removed a field names its path in `retired`; the three lanes that rebuilt the views wholesale had been dropping such entries. The mapping lane writes the field too. `records_in_step.py` brings an artifact written before this into step without overwriting either side, and its `--check` fails while anything is out of step. |
| An operator's answer settles an arithmetic status, and the gate and the export read one list | `review_status.py` `ARITHMETIC_CLEAR`; `arithmetic_check.py` `relabel_not_provable`, `--not-provable-as-not-applicable`; `prior_arithmetic_status`, `arithmetic_status_authorization`; `summary.not_provable_relabelled`, `summary.not_provable_relabel_authorization`; `client_review/queue.py` `review_items` | `not_provable` -- no base, rate and commission printed together on any line -- is a policy question, so only a named operator authorization counts it as `not_applicable`, and the status the document reached on its own readings stays beside it. A failed document is never relabelled. The canonical export admitted `not_applicable` while the queue filed an item for every status but `proved`, so a document whose arithmetic was established as inapplicable was refused for the item it held; both now read `ARITHMETIC_CLEAR`. |
| A re-read governs its own pages, and an answered register entry is not a question | `client_review/queue.py` `governed_pages`, `consolidate(paths, accepted, governed)`, `--supersede ARTIFACT MANIFEST`; `reconciled[].disposition` one of `already_resolved`, `superseded_by_re_read`; `reconciled[].governed_by`; `summary.superseded_items`, `summary.superseded_by`; register entries with `is_failure: false`; `attribution.py` `DISPOSITION_CODES` `payment_record`, `blank_page` | A subset re-read replaces its pages' readings in the record, but the earlier readings' findings stay in the artifacts the gate is handed, so the queue asked about readings the record no longer held. `--supersede` names the earlier artifact and the re-read's manifest: its findings on those pages move to `reconciled` with the manifest that governs them, never deleted, and the re-read's own findings are supplied as inputs. A supersession naming an artifact that is not an input, or a manifest naming no page, is refused. An attribution register entry carrying a Phase 0 reason code (`is_failure: false`) is an answer; queued, it held its document out of canonical by its own answer. `payment_record` and `blank_page` are assigned only where the page image shows it: of 92 documents the client typed payments and 29 typed blank, the images bore out 45 and 5. |
| An inferred value sits beside the reading, never over it | `augment_export.py` `augment`, `infer`, `compared_fields`, `place_in_name`, `plausible_branch`, `phone_numbers`, `fill_identities`, `account_rows`, `contact_rows`, `person_readings`, `one_person_groups`, `company_of`, `keys_not_in_the_master`, `load_product_rules`, `assign_products`, `product_code_in`, `product_name_in`, `product_code_key`, `line_brand_key`, `printed_row_codes`, `read_product`, `product_rows`; `--parties`, `--out-accounts`, `--out-contacts`, `--product-rules`, `--out-products`, `--extractor-raw`; `product_rules_v1`; `product__code`, `product__key`, `product__name`, `product__variant`, `product__brand`, `product__brand_key`, `product__rule`, `product__printed_row_code`, `product__printed_row_check`; `summary.products`; `summary.accounts`, `summary.contacts`, `summary.person_readings`, `summary.contacts_with_a_company`, `summary.contacts_belonging_to_the_client`, `summary.party_keys_not_in_the_master`; `export_augmentation_v1`; `cells_inferred`; `<column>__inferred`, `<column>__inferred_by`, `<column>__inferred_evidence`; methods `two_vendors_agree`, `document_letterhead`, `same_line_on_other_statements`, `same_key_on_other_lines`, `printed_in_the_name`, `iso_3166_1_alpha2`, `e164`, `public_source`; `carried_party_key`, `names_a_party`, `usable_key`, `line_maker`, `shared_key_index`, `party_by_shared_key`, `score_shared_keys`, `fill_parties_from_shared_keys`, `SHARED_KEY_COLUMNS`, `SHARED_KEY_FIELDS`, `MIN_SHARED_KEY_ACCURACY`; `<column>__inferred_party_key`; `summary.parties_from_shared_keys` | A value the page never printed in that cell is not a reading of it, so it gets columns of its own, and the first method to fill a cell keeps it. Only `two_vendors_agree` rests on two vendors. The same line is carried only where every statement carrying it agrees and at least two do; dates are excluded because `transaction_date` is filled from six different printed columns and a carried date matched its own page three times in four. A state is read only when punctuation sets it off -- `MARLOW/BRAMWELL IN` is `INC` cut short, not Indiana -- and `LA` is never read as one; the text after a name's dash is a branch label, not a city. Checked against the independent extractor's text of each page, 96.6% of carried values were printed there. A public-source entry retrieved after its file was compiled carries its own `retrieved` date into the evidence. With `--parties` it writes the two dimensions a CRM loads beside the grains, because a CRM that built its own from export 44's raw columns loaded 50 accounts that included the client five times, a heading and an address, and 37 contacts that were mostly codes, a footer, brands and the client. An account is one party the grains name as a party, joined on `<field>__party_key` and never from a field the row says holds the client, the job or a refused reading; it carries every spelling, the roles printed, a branch's parent, the public identity beside its readings and its documents' dates. The first build joined on `__resolved_party`, which holds the name, and wrote no accounts at all; `party_keys_not_in_the_master` now counts any key the master lacks, so a wrong master is named rather than read as a corpus with no accounts. A contact is one person the person fields name: spellings join on the same letters, a middle initial or the same email, never on one letter apart; an email or phone is kept only where an email naming the person is printed with the name, and a company only where that email's domain names one of the document's parties or the client. On the commission run that is 681 accounts, and 12 contacts from 56 readings, 7 with a company: Jana's three spellings are one contact, the client's own, and murbrook's main line, printed beside her name on seven pages, sits on murbrook's account from its own website rather than on her. With `--product-rules` each line's product is read by its manufacturer's own rule, from an operator file naming the authorization it rests on, where every rule cites its pages and either gives the code's pattern, the columns it may be printed in and where the name is, or says the manufacturer lists no products; a CRM that built products from `description` and `item_code` loaded murbrook projects, HALVOR project clients and territories and Linden World payer numbers. A candidate the rule calls no product, or one naming a party, gives way to the next column; a row that is not a line, and a page whose lines name several manufacturers, carry no product; `product__rule` says why for every line, and `summary.products.lines` counts each reason. With `--extractor-raw` a rule marked `check_the_printed_row` reads each line's code off the row an amount the line carries is printed on, where the page prints it once, one code per row and one row per code: Linden World's code column slips a row against its amounts on some pages, every row checked against five page images bore out the printed row, and on the commission run it took the printed row's code over the engines' on 111 lines and read one the engines never did on 414. The engines' reading stays in its column. `--out-products` writes one product per manufacturer and code, keyed with the code's letter positions read as letters (`S01610` is SO1610). An inferred party carries the master's key of the readings it came from in `<column>__inferred_party_key`, and only where every carrier names a party under one key: on export 50, 1,576 inferred customers, dealers and specifiers had no key, so a CRM joining on keys dropped every one. `same_key_on_other_lines` fills a line's customer or dealer from the same maker's lines printing its purchase order, sales order, job, project or transaction number, where every such line names one keyed party; a key whose lines name two parties, or two keys naming different parties, fill nothing, and only readings speak, never an inference. It is scored before it fills: each line printing its party is hidden and the method asked for it. On export 50 it named the printed customer on 97.4% of 2,419 held-out lines and the dealer on 96.0% of 1,745, where naming each maker's most common party did so on 28.6% and 30.3%; asked only of other documents it scored 93.8% and 94.9%. A field scoring under `MIN_SHARED_KEY_ACCURACY` (95%) fills nothing, and `summary.parties_from_shared_keys` reports each field's score beside its chance baseline. Writes new files only. |
| A reading the page cut off is flagged, and completed only where the corpus is unambiguous | `field_extract_export.py` `truncation_candidates`, `mark_truncated_text`; `text_truncated_in_source`, `completion_proposed`; `entity_resolve.py` `completed_by` | Dates and numbers announce a cut column; text does not. Page 254 prints `NORTHGATE C(`, `CHAIR SI0` and cuts its own headings. It matters because a cut name is a new CRM account: `NORTHGATE C` appears on 219 line rows where the party is `NORTHGATE CO LLC`, and 1,654 text cells are cut mid-word. With no marker to key on the evidence is the corpus itself -- a strict prefix of a longer reading in the same column, continuing into the same word. The completion must continue with a *letter*, because a prefix of a number is not a truncation and `AP0494` beside `AP04944` is two item codes. Nothing is rewritten: 645 rows carry a single proposed completion and 613 are flagged without one, because choosing between `LOUNGE CHAIR` and `LOUNGE COUCH` is a question for a person. `entity_resolve.py` applies the same containment rule to party names, uniting 55 pairs similarity could never reach. |
| Complete field-level extract | `field_extract_export.py`; `field_extract_v1`; `rows[].status` one of `accepted_vendor_agreement`, `accepted_independent_corroboration`, `accepted_same_document`, `accepted_extractor_tiebreak`, `accepted_page_review_amendment`, `accepted_approved_mapping`, `accepted_document_arithmetic`, `accepted_derived`, `single_reading`, `contested`, `withheld_<flag>`, `accepted_by_an_unregistered_lane`; `rows[].confidence` one of `confirmed`, `corroborated`, `computed`, `derived`, `unconfirmed`; `summary.fields_by_status`; `summary.fields_by_confidence`; `summary.fields_accepted_by_an_unregistered_lane`; `summary.unregistered_acceptance_lanes`; `summary.documents_without_intake_provenance`; `summary.final_review_queue_consulted`; `summary.canonical_load_permitted` always false | `canonical_export.py` admits only review-clear documents and has no override. On the commission run that is 0 of 716, while 73,878 of 83,453 extracted fields are already accepted. The positional input is the run's **current record artifact**, not its consensus artifact: acceptances are appended after consensus by `apply_corroboration.py` and `apply_mappings.py`, and reading one step too early reported 26,300 accepted against 73,878 over the same documents and the same fields. Those readings had no way out of the artifacts. This writes every field of every document exactly once with the evidence that accepted it or the reason it is withheld. `single_reading` is kept distinct from `contested` because one engine returning a value is not a disagreement -- there is no second reading to dispute -- and merging them either invents corroboration or asks a question with no answer. It is not a canonical load, clears no control, and the CRM load chain still reads `canonical_export_v1`. `--accepted-csv` writes the rows under any status in `ACCEPTED_STATUSES`, which is not vendor agreement alone: on the commission run's export 43 its 88,320 rows held seven statuses, with vendor agreement 35,545 of them and independent corroboration 40,124. Its help called the whole subset two vendors agreeing and is now built from that registry; a row accepted by an unregistered lane is left out. `--lines-csv` and `--documents-csv` pivot the rows back into the record grain a CRM import loads -- 7,900 commission lines and 686 document headers on that run -- and because a pivoted cell cannot show its own status, every record carries `row_confidence`, `unconfirmed_fields`, `fields_vendor_agreed`, `fields_corroborated`, `fields_computed`, `fields_derived`, `fields_single_reading`, `fields_contested`, `fields_unattributed`, and the contested, single-reading and corroborated column names. `row_confidence` is the weakest cell in the row, because a row with nine corroborated cells and one contested cell is not a confirmed row and must not display as one, and `unconfirmed_fields` names the cells that made it so. The counters account for every cell in the record: the version that shipped counted five statuses and fell through for the rest, leaving 2,885 accepted cells on the commission run in no column at all. `fields_unattributed` is the final bucket that makes the identity hold unconditionally -- a withheld flag or an unregistered acceptance is counted and named rather than dropped -- and it is expected to read zero. A pivot with no records is refused rather than written empty. Provenance joins on `page_id`, which is what the intake manifest keys; `document_id` is null there until reassembly, and joining on it empties every provenance column in a way that reads as a corpus without sources. |
| Every retained reading gets a vote | `multi_engine_vote.py`; `multi_engine_vote_v1`; `acceptance.accepted_by` one of `agreed_by_independent_vendors`, `matches_a_settled_value_in_the_same_document`, `tie_broken_by_independent_extractor`; `agreeing_vendors`; `summary.unresolved_reasons` | `consensus.py` compares exactly two lanes, so a run that read its corpus with four vendors judged every field on one pair and never consulted the other two readings -- which were already paid for and sitting in its own provider artifacts. On the commission run that left 13,423 fields open while 81,460 cells had two or more vendors on them. Scored against 293 cells an independent arithmetic proof had settled and the lane never sees: **three vendors agreeing 100%, two agreeing 96.3%**. Re-reading the same pages with a model and asking it to adjudicate scored 87.1% and cost money. One vote per **vendor**, resolved through `model_vendor` -- two Google lanes are one reading, and counting lanes would manufacture the independence consensus exists to protect. A tie is retained as a finding, then offered to two narrower tests in order: a value the same document already states in a settled cell (measured 55 of 55; the same test over the whole *corpus* scored 58% because a field misfiled a hundred times looks authoritative, so it is deliberately document-local), then a non-LLM extractor that read exactly one of the tied values off that page. Each carries its own label and none is called vendor agreement. Iterating to convergence took the corpus from 86.2% to 93.4% accepted, 95.4% of business fields, at no cost. |
| A line's own arithmetic chooses between two readings | `arithmetic_reconcile.py`; `arithmetic_reconciliation_v1`; `acceptance.accepted_by: reconciled_by_document_arithmetic`; `summary.checkable` / `not_checkable` / `resolved`; `--relative-tolerance` | Where two vendors disagree, consensus records `no_consensus` and nothing downstream can settle it: both readings are equally attested, and page-presence corroboration proves a string is on the page rather than which row owns it. A commission line states its own arithmetic -- `commission_amount` is `commissionable_amount` times `stated_commission_rate` -- so where two of the three are known the third is not a matter of opinion. On the commission run this resolved 527 of 585 checkable cells. It is the only control here that defeats a **misplacement**: two engines offered `(662.89)` and `4,098.49` for one line, and `4,098.49` was also a candidate on a different line of the same page, because one engine read the table a row off. A sibling lends a value only when accepted or when it is open with exactly one candidate -- two competing siblings would make the expected value itself a choice. Exactly one candidate must match; two is an ambiguity and zero is a contradiction, both reported rather than resolved. The tolerance is proportional because rounding is: a flat two cents is 0.0025% of a $799 line and rejected `799.87` against `799.671`, which was 102 of 144 rejections. Swept on the corpus, 0.02% resolved 509 with 1 ambiguous, 0.05% resolved 527 with 2, and 1% resolved 538 with 7; the default is the knee, not the ceiling. A resolution is labelled `reconciled_by_document_arithmetic` and never `consensus_*`: an arithmetic proof is not two vendors agreeing. |
| Adjudication outcomes separated | `adjudicate.py`; `arithmetic_inputs_absent`; `no_candidate_matches_the_document_arithmetic`; `adjudication_not_unique`; `unconsulted_key_fields`; `matching_candidate_values`; `summary.exceptions_by_reason`; `summary.documents_without_the_adjudicated_field` | Three outcomes were reported as one, and it queued more documents than any other control in the run: 706 of 716, every one reading `adjudication_not_unique`. 638 of them do not carry `header.total_amount` at all -- it exists on 78 -- so a client was asked to choose between readings of a total the document never had. The remaining 68 were never adjudicated either: `expected_total` needs `subtotal`, present on 1 document of the corpus and valued on none, so it returned None everywhere and the exception recorded `expected_total: null` and `candidate_values: []` while claiming a non-unique adjudication. A document without the field is now skipped and counted, not queued; a document whose inputs are absent is `arithmetic_inputs_absent` and names them in `unconsulted_key_fields`; a computed total that no candidate matches is distinguished from several that do, because only the second is an ambiguity to resolve. Re-run on that corpus: 706 exceptions became 68. |
| Group answer contradicted by content | `--records` (repeatable), `--outlier-factor`; `withheld_contradicted_by_content`; `summary.content_contradiction_checked` | A client answers one question for a whole group after seeing a few worked examples, and every member is then typed from that one answer. On the commission run a group of 30 was answered "Blank Page. Can be ignored." -- 22 were flagged `near_blank`, and three carried 46, 129 and 190 populated fields, two of them commission lines with amounts. `classification_amend.py` looked at none of that. A member carrying more than `--outlier-factor` times its group's median populated-field count is now withheld from the amendment and retained with its count: it is not retyped, not guessed at, and stays an open question. Only the richer side is withheld -- reading fewer fields off a payment record does not make it something else, and withholding both tails flagged 33 of one real group's 128 members. A group with fewer than two known members, or a zero median, is not judged at all. Without `--records` the check does not run and the summary says so; zero contradictions found and nothing examined are different results. |
| Protected answer deferral | `client_decision` set to `Defer` with `client_decision_as_answered` and `deferred_because_protected` beside it | Rule 7 says a protected finding informs an item-by-item recheck and is never batch-cleared, and `client_decision_compile.py` enforces it by raising -- which ends the whole compilation, not just that group. Passing a protected group's dropdown choice through verbatim therefore let one answered protected group block every unprotected decision in the same run: on the commission round, 20 answered protected groups blocked the 3 unprotected document-type answers the client had also given, so the lane's answers still could not reach authorization. The answer is recorded as deferred rather than dropped or weakened -- the choice the client actually made is retained beside it and the comment is untouched -- because what a protected answer authorizes is a recheck, not a change. An unanswered group is already the empty choice and is never marked deferred by this rule: it was not deferred, it was never made. |
| Final queue item identity | `review_item_id` on every queue item, derived from `review_key`; `source_item_ids` per decision group | A finding a client can be asked about must be a finding an authorization can name. Queue items carried no identity at all -- 0 of 35,745 on the commission run -- so `review_grouping.py` built an empty `source_item_ids` for every group, and `client_decision_compile.py`, which requires a change's `affected_review_item_ids` to be non-empty *and* a subset of its group's, could not compile a single client answer into a change. The lane asked, the client answered three rounds, and nothing downstream could bind those answers to anything. The id is a digest of `(document_id, page_id, region_id, field, reason)` -- the same key that already deduplicates the queue -- and never a position: rebuilding with one more input artifact renumbers every later item, which would silently repoint an authorized change at a different finding. A finding that actually changed gets a new id instead of inheriting the old approval. An absent key part and an explicitly null one are the same absence, or one finding would hold two names. |
| Client review pack reconciliation | `--resolved-by`; `summary.reconciled_items`, `summary.resolution_artifacts`, `reconciled` | `final_review_queue.py` retains a document-type proposal as reconciled where classification already accepted the same type. The pack lane builds the same queue by a second path and had no such option, so a pack issued from it re-asked 1,121 questions the run had answered. Two paths to one queue must reconcile the same way. |
| Review grouping document family | `--classifications` (repeatable); `source_classifications`, `documents_with_a_resolved_type` | Two artifacts answer what a document is and only one was read. The extraction consensus writes `document_type: "unknown"` wherever its lanes could not agree — 691 of 718 on the commission run, which is consensus correctly declining to claim a type and the reason `classification_consensus.py` exists. That lane had accepted 686 of the same documents on two independent model vendors agreeing. Reading only the first labelled 691 documents `unclassified_document_family` and put one undifferentiated question to the client about a corpus whose type was settled; its text fallback also invented 442 `purchase_order`, `invoice_like` and `statement_like` groups out of words in reason strings. Classification artifacts are read after the consensus and an unresolved type never overwrites a resolved one. |
| Client review question wording | `generic_wording` per question; `generic_wording_question_count`, `items_behind_generic_wording`, `families_without_wording` | `disagreement_family` falls back to the raw reason, so a finding family with no template is asked about as "We need your confirmation on these items" — a page shown with no question attached. Restoring the handwriting findings to the gate put 3,958 items behind that prompt. The pack now names what it could only phrase generically, so wording is added before a client sees it rather than after. |
| Record field aliases | `extraction_schema.NON_SCHEMA_RECORD_ALIASES` | Every literal field name a control reads must be a schema field or a registered alias with the reason it cannot come from extraction. A lookup that can never match is indistinguishable, in the control's own output, from a corpus with nothing to find: rank 1 reported 26 of 137, selling location 0 of 716, and completeness ran aging closure on permanently empty credits and status. |
| Final queue handwriting suppression | `comment_only` applies to a finding whose **field path** is a handwriting observation, never to one whose prose mentions handwriting | The gate matched the word anywhere in `field`, `reason`, or `category`. On the commission run that silently dropped 2,590 findings: 838 `google_handwriting_region_unreadable` lane failures carrying no field at all, 1,068 findings a control had already marked `blocking: true` (consensus reports these with `is_handwritten: false`, so its own policy branch never applied and it meant them to block), and ~684 findings on `commission_amount`, `brand_name`, `sales_representative_name` and similar whose reason described a **printed** value obscured by a mark. `comment_only` never overrides a control that said blocking, and never suppresses a finding that names no field. |
| Pipeline plan already-run | `already_run`, `summary.lanes_with_retained_output` | Every entry names what the run already retained for that command, from the same catalogue `run_lane_coverage.py` uses. The verdict still follows the measurement -- a lane can genuinely need rerunning -- but a plan that reads as untouched work on a lane that has already executed asks an operator to spend on retained evidence. Discovery also skips this command's own artifact: a plan quotes the keys discovery matches on, and being newest it otherwise wins every slot it touches. |
| Pipeline plan | `pipeline_plan_v1`; `proposal_only`, `clears_no_control`, `summary.unscanned`, per-entry `decision`/`because`/`measured_from`/`then` | Every lane verdict names the measurement that produced it and the artifact the measurement came from. There are exactly three verdicts and no fourth meaning "probably skip": an unmeasured lane is `undecided`, because defaulting it to skip is how a control goes unrun and defaulting it to run is how a corpus is charged for a lane its own evidence says is pointless. `summary.unscanned` names an artifact too large or too malformed to read, which is a different finding from a lane that never ran. The plan authorizes nothing and clears nothing; a disabled lane stays disabled and the entry names the setting. |
| Attribution explicit keys | `explicit_key_fields`, `unconsulted_key_fields` | Rank 1 records the header names it asked for, and counts every populated key-shaped header field it was not told to read. This lane spent a full corpus looking for `ack_number`, which is not an `extraction_schema.HEADER_FIELDS` name -- the schema calls it `acknowledgement_number` -- so 111 documents that printed their key were read correctly, accepted, and then registered as unresolved. A blind rank and an absent key are indistinguishable in the attribution rate alone, so the artifact now separates them. `--key-field` overrides the order for an engagement whose key has another name. |
| Independent table reconciliation | `coverage`, `corroborated`, `independent_reconciliation_produced_no_comparison` | Table, row, and cell rejections and unmapped cells are counted. A run supplied with handoffs that produced no comparison corroborated nothing and says so. |
| Scan profile page record | `probe_failures`, note `depth_unverified` | Metadata probes that could not be read are named, and a bucket assigned without a measured bit depth is flagged the same way an unverified chroma reading already was. |
| Retained provider raw artifact | `cache_hit`, `cache_key`, `cache_dir`, `cache_written_at` | A cache hit replays a retained response instead of calling the provider. The artifact records which of the two occurred; a genuinely fresh call requires an empty or unset cache directory. |
| Returned-workbook preflight | DTD and entity rejection | Every XML member is scanned before parsing. `xml.etree.ElementTree` expands internal entities, so a few hundred bytes inside a member that satisfies every ZIP bound could still exhaust memory. A workbook from Excel, LibreOffice, or XlsxWriter declares neither. Malformed relationship targets, missing worksheet parts, and unknown shared-string indexes now raise the importer's normal `ValueError` rather than `KeyError`, `StopIteration`, or `TypeError`. |
| Allocation policy | `allocation_effective_rate_exceeds_plausibility_ceiling`, `allocation_effective_rate_negative`, `allocation_rate_unit_ambiguous`, `allocation_policy_effective_rate_ceiling_invalid` | A computed commission rate above the ceiling or below zero, a rate whose unit cannot be established, or an out-of-range configured ceiling all reach review as critical exceptions instead of an approved classification. A negative rate is a clawback or reversal, not an administrative allocation, and would otherwise fall below the threshold and classify clean. |
| Address normalization | `address_country_postal_mismatch` | The address declares a country whose postal pattern does not match while a different supported country's pattern does. Country-scoped matching would otherwise drop the postal code silently and leave it in the city field with a `format_valid` verdict. Only `US`, `CA`, `GB`, `SG`, `CN`, and `TH` have patterns here and can be judged; a country without one is never flagged, because a five-digit French or German code is not evidence of a mismatch. |
| Address normalization | Segmented region parsing | A region in its own comma segment (`1 Main St, Springfield, IL, 62704`) is recognized as `state_or_region` rather than becoming the city. Canadian province codes are recognized once the country already resolves to `CA`; US territories that use the ZIP system (`PR`, `VI`, `GU`, `AS`, `MP`) parse as US states. |
| Retrieval store | Empty-index refusal | A canonical export that yields no approved chunks refuses to build unless `--allow-empty` is passed. A zero-chunk index answers "no results" for every question, which is indistinguishable from the facts not being present. |
