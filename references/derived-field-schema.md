# Derived and Control Field Schema

## Status

**This schema describes pipeline-derived or control fields, not values read
directly from a client document.** It must travel with
[`extraction-schema.md`](extraction-schema.md). The corresponding fictional
sample is [`examples/derived_control_sample.json`](../examples/derived_control_sample.json),
which is labelled **SAMPLE - FICTIONAL - NOT CLIENT DATA** throughout and must
not be used for calibration, accuracy claims, client decisions, or production
loading.

The input/extracted record preserves values as `{value, confidence, source}`
objects. A derived result preserves the document ID and stage-specific evidence;
it does not replace a source value.

## Consensus fields

`scripts/consensus.py` emits a document summary and an entry for every
reconciled field under `fields.<dotted_path>`.

| Field | Type | Meaning |
|---|---|---|
| `review_status` | enum | `auto_accepted`, `review_queued`, or `open_exception`. |
| `consensus_flag` | enum | Document-level result, including `consensus_2of2`, `consensus_3of3`, or `no_consensus`. |
| `field_count` / `accepted_field_count` / `hard_exception_count` / `handwritten_field_count` | integer | Counts of considered, accepted, no-consensus, and handwritten fields. |
| `fields.<path>.value` | scalar or null | Accepted value; null when no reading is accepted. |
| `fields.<path>.candidate_values` | array or null | Candidate readings when a value was not accepted. |
| `fields.<path>.confidence` | number or null | Mean provider confidence only; never proof. |
| `fields.<path>.source` | enum | `printed`, `handwritten`, `manual`, or `system`. |
| `fields.<path>.consensus_flag` | enum | Field-level agreement result. |
| `fields.<path>.agreeing_engines` / `engine_count` | array / integer | Engines supporting the reading and count that supplied it. |
| `fields.<path>.rule` / `accepted` / `is_handwritten` / `queue_for_review` | string / boolean | Reconciliation method and control disposition. |

## Arithmetic-proof fields

`scripts/arithmetic_check.py` proves relationships without modifying source
values.

| Field | Type | Meaning |
|---|---|---|
| `arithmetic_status` | enum | `proved`, `proved_with_unproved_lines`, `failed`, `not_provable`, `not_applicable`, or `deferred_reassembly`. `proved_with_unproved_lines` means the header identities balanced while at least one line lacked quantity, unit price, or extended amount: header arithmetic cannot prove rows that were never read, so the document stays in review and outside the sampling frame. |
| `checks_run` / `checks_failed` | integer | Applicable arithmetic checks and failures. |
| `largest_discrepancy` | decimal number | Largest absolute failed delta. |
| `handwriting_involved` | boolean | A handwritten field or amendment affected proof. |
| `lines_missing_fields` | array | Line identifiers lacking fields required for proof. |
| `checks[]` | array | Each check records `check`, `status`, `expected`, `stated`, and `delta`. |
| `disposition` | string | Explicit pass or exception instruction. |

## Address-normalization fields

`scripts/address_normalize.py` derives CRM-ready components after deterministic validation. It preserves source address evidence and records local parsing. Optional Google Address Validation is an explicit client-approved, capped provider call; it supplies separate deliverability and geocode proposals, never source replacements.

| Field | Type | Meaning |
|---|---|---|
| `address_normalizations[].source_field` / `raw_value` | string | Original address field and unchanged extracted value. |
| `derived_fields.<role>_address_line1` / `address_line2` | object | Primary and secondary address lines; `source` is always `system`. |
| `derived_fields.<role>_city` / `state_or_region` / `postal_code` / `country_code` | object | Parsed locality; country uses ISO 3166-1 alpha-2 where recognized. |
| `format_validation_status` | enum | `format_valid` or `review_required`; it does not assert deliverability. |
| `external_validation_status` | enum | `not_requested`, `validated`, `review_required`, `unsupported_region`, `permission_denied`, `request_rejected`, `failed`, or `not_requested_limit_reached`. Only `validated` can add separately named provider proposals. |
| `google_address_validation.provider_error_category` | enum or null | Non-secret failure category for a failed provider call; its value is mirrored in the review reason. |
| `google_address_validation` | object | Optional provider evidence: response ID, verdict flags, formatted address, CASS-data presence, and separately named proposed components/geocode. It is absent unless explicitly requested. |
| `google_component_conflicts` | array | Existing provider-derived values that differed; neither value is overwritten. |
| `conflicting_existing_fields` | array | Existing local component values that differed; neither value is overwritten. |
| `address_validation_status` | enum | `clear` or `client_review_required` for the record. |

An incomplete local parse, a non-premise or uncertain provider verdict, provider failure (including an unsupported country coverage result), limit exhaustion, or a component conflict becomes a final-review exception. The Google call is disabled by default and must be explicitly enabled with a client-approved key and a run cap of at most 5,000.

## Entity-resolution fields

`scripts/entity_resolve.py` produces reversible party-match evidence, not an
automatic overwrite of a party name.

| Field | Type | Meaning |
|---|---|---|
| `raw_name` / `raw_address` | string or null | Observed source-party evidence. |
| `role` | enum | Party role such as `biller`, `payer`, `consignee`, `shipper`, or `carrier`. |
| `document_id` / `field` | string | Source trace for the mention. |
| `norm_name` / `norm_address` / `postal` | string | Normalized comparison keys. |
| `left` / `right` / `score` / `disposition` | values | Candidate merge evidence and proposal outcome. |

## Attribution fields

`scripts/attribution.py` accounts for every monetary amount against the
client-approved reference table or registers why it cannot be attributed.

| Field | Type | Meaning |
|---|---|---|
| `amount` | decimal number | Absolute financial amount considered. |
| `ack_number` | string or null | Resolved configured business reference. |
| `rank` | integer or null | Matching rank in the documented hierarchy. |
| `attribution_method` | string | Recorded method; `unattributable` when unresolved. |
| `attribution_confidence` | number or null | Method confidence, where applicable. |
| `evidence_chain` | array[string] | IDs supporting inherited or matched attribution. |
| `reason_code` / `is_failure` | string / boolean | Why an unattributed amount was registered and whether it blocks the gate. |
| `selling_location` / `method` | string or null | Resolved selling location and its method. |

## LLM-adjudication fields

`scripts/llm_adjudication.py` is an optional, disabled-by-default bounded lane. It consumes explicit evidence-linked candidates and can write only review-required amendment proposals.

| Field | Type | Meaning |
|---|---|---|
| `candidate_id` / `amendment_id` | string | Stable candidate/proposal trace IDs. |
| `original_value` / `candidate_value` / `original_source` | scalar / scalar / enum | Original retained value, proposed value, and source; originals are never replaced. |
| `decision` / `decision_source` / `amendment_source` | enum | `llm_generated_amendment_proposal` and `llm` for a model proposal. |
| `client_review_required` / `disposition` | boolean / enum | Always `true` and `llm_generated_amendment_requires_client_review`. |
| `sampling_required` | boolean | Deterministic continuous-QA sample marker. |
| `audit.model` / `reasoning_effort` / `prompt_version` | string | Model execution identity and policy version. |
| `audit.model_confidence` / `minimum_confidence` | number | Self-reported score and configured floor (0.99–1.0); neither overrides protected categories. |
| `audit.raw_response` / `raw_response_sha256` | string | Retained raw response location and integrity hash. |
| `audit.deterministic_validation_status` / `independent_extractor` | values | Prerequisite evidence for a queried candidate. |

## Sampling and final-review fields

Sampling selects only eligible clean records; it does not close exceptions.
`scripts/final_review_queue.py` consolidates unresolved work.

| Field | Type | Meaning |
|---|---|---|
| `stratum` | string | Reproducible sampling stratum. |
| `eligible` | boolean | Whether the record is eligible for sampling. |
| `selection` | string or null | Sample selection method, if selected. |
| `sample_seed` | integer | Reproducibility seed for the plan. |
| `priority` | enum or null | `critical`, `high`, or `normal` for open review. |
| `field` / `reason` / `review_source` | string or null | Exact review target and cause. |
| `disposition` | enum | `client_review_required` when queued; `not_queued` is sample-only notation, not pipeline output. |

The final-review operational contract remains the eight-field `ReviewItem`
defined in [`artifact-contracts.md`](artifact-contracts.md). Do not add derived
fields to that reviewer queue without versioning the operational contract.


## Allocation-policy fields

`scripts/allocation_policy.py` preserves the allocation source fields and creates
a derived, effective-dated sales-credit result only when an exact
client-approved policy applies.

| Field | Type | Meaning |
|---|---|---|
| `raw_source_fields` | object | Original commissionable amount, commission amount, stated rate, share, and source indicators; never overwritten. |
| `derived.effective_commission_rate` | decimal string | `commission_amount / commissionable_amount`; distinct from a stated nominal rate. |
| `derived.effective_rate_ceiling` | decimal string | Present when the computed rate exceeded the plausibility ceiling. A rate above the ceiling is a misread figure, not a policy question, and reaches review as `allocation_effective_rate_exceeds_plausibility_ceiling` rather than any approved classification. Defaults to `0.5` and may be lowered per policy; it cannot exceed `1`. |
| `derived.ambiguous_rate_fields` | array | Rate fields whose unit could not be established from the value. A displayed `0.75` is equally 0.75% and 75%, so it is registered as `allocation_rate_unit_ambiguous` review work instead of being resolved by magnitude. |
| `derived.formula_expected_commission_amount` / `formula_delta` | decimal strings | Optional evidence proof from reported stated rate × allocation share. A nonzero delta is review work. |
| `classification` | enum | `administrative_ship_to`, `sales_generated`, or `review_required`. |
| `sales_credit_eligible` | boolean/null | `false` for an approved administrative allocation, `true` for approved sales-generated work, null when unresolved. It is never `true` while `client_review_required` is `true`; a failed formula proof, an ambiguous rate unit, or an effective rate above the plausibility ceiling all retract the classification to `review_required` with a null eligibility. |
| `policy_rule_id` / `policy_version` / `policy_scope` | values | Exact immutable policy snapshot and the matched dimensions/date window. |
| `client_review_required` / `reason` | boolean/string | Whether a missing rule, formula, evidence, or policy condition blocks the result. |

Use any client-approved source concept under `dimensions` to scope a policy by
brand, dealer, product, client, location, or a later discovered layer. The
policy registry stores effective dates rather than overwriting a historical rate.
