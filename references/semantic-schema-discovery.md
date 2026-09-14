# Semantic Schema Discovery and Entity-Location Review

This runbook governs the optional audit-only discovery step for source layouts.
It is intentionally separate from extraction consensus and from the LLM
adjudication lane. It proposes a versioned vocabulary mapping; it never changes
source evidence, a canonical entity, an address, or a CRM target.

Mapping discovery is iterative evidence review. Each enabled iteration receives
the complete supplied template evidence and accumulated prior proposal context,
then records its iteration number and context-template count. This lets later
evidence challenge an earlier best guess without approving, overwriting, or
silently normalizing any source value.

## Preconditions

- Retain the source PDF, one-page masters, intake manifest, and any observed
  headers as immutable evidence.
- Supply a source-template JSON artifact with a `templates` array. Each template
  has `template_id`, ordered `headers[].source_label`, and, when available,
  evidence with `document_id`, `page_id`, and `region_id`.
- Include only observed dealer, brand, customer, payer, or location candidates
  in `entities`; relationships must retain their source association.
- Copy `.env.example` to the Git-ignored root `.env` before enabling an LLM or
  Google Places. Keep credentials outside every artifact.

## Controlled proposal contract

For each unknown label, the LLM must return only `source_label`,
`canonical_field`, `semantic_type`, `alternatives`, `confidence`, and
`rationale`. `canonical_field` is limited to the repository vocabulary;
unknown concepts belong in `schema_change_queue`. The proposal packet also
supports dealer/brand/customer/payer entity suggestions and observed
relationship suggestions. Every proposal carries `client_review_required=true`.

Exact, client-approved rules are reusable only where both the ordered
source-template fingerprint and source label match. The fingerprint prevents a
changed spreadsheet/page layout from inheriting an old mapping. A client
approval creates a new registry snapshot with `effective_from`; the prior
registry remains evidence. Rejections and deferrals create no rule.

Before mapping discovery or reuse, run `template_drift.py analyze`. Its
`source_layout_v1` fingerprint extends the compatibility header hash with the
document family and supplied source-layout structure. Only one exact approved
match permits reuse; diagnostic similarity never does. Use
`client_decision_compile.py` for an impact preview and operator authorization,
then `template_drift.py registry-update` to create a new template-registry
snapshot when the authorized plan contains a `template_registry_rule` patch.

After an evidence graph has been built, run `evidence_graph.py schema` to retain
a bounded, deterministic inventory of observed fields and graph-claim
provenance. When retained consensus has non-accepted candidate values, also run
`evidence_graph.py schema-surface --consensus CONSENSUS.json`. The schema surface
keeps observed claims separate from retained candidates, including candidate
field/document/source-pointer coverage and label-bound unstructured signals such
as email, phone, attention, contact, or sales-representative text. It is an
audit of what needs review, not a promotion of a candidate into a graph fact.
It also carries a bounded summary of signal counts and source-cited examples for
schema discovery. The raw signals remain in the surface artifact, while any LLM
receives only the bounded summary; neither is evidence of a person’s role or a
canonical mapping.

Either inventory may be passed through `--graph-schema-inventory` only with the
original source-template input. The command validates the proposal-only contract,
rejects an observed-claim inventory over 65,536 bytes. A schema-surface artifact
may be up to 10 MB because it is retained audit data; the loader creates and
records a source-hashed, explicitly counted context window of at most 65,536
bytes before any provider call. It records both the graph and artifact hashes in the discovery output and
handoff. The inventory is corpus context—not mapping evidence—so a source label
still needs its own retained template/page evidence and client approval.

When a contained retry is warranted, generate a source-hash-bound worklist
instead of reprocessing the full corpus:

```bash
python scripts/evidence_graph.py schema-recovery-scope evidence_graph.json \
  --consensus consensus.json --topic address \
  --out address_recovery_scope.json

python scripts/evidence_graph.py schema-recovery-scope evidence_graph.json \
  --consensus consensus.json --signal-type contact_or_representative_label \
  --signal-type email --out contact_recovery_scope.json
```

The scope is only a selection artifact. Send its exact source pages to new,
independently configured extraction lanes, retain all raw results/exceptions,
and reconcile the resulting overlay; do not treat the scope itself as evidence
or approval.

Enabled discovery uses the configured primary reviewer and an independent
configured buddy provider for every primary mapping proposal. The buddy sees the
same retained source-template packet and returns `confirmed`,
`close_needs_review`, `conflict`, or `unsupported`. A `confirmed` proposal may
be labelled `inferred_high_confidence` only when it also meets the configured
primary confidence floor and has retained document/page evidence. It remains a
client-review proposal: neither state changes a registry, a canonical mapping,
or an authorization.

## Slot equivalence

`discover` answers "what canonical field does this printed label mean?".
`slot-equivalence` answers a different question that only appears after
consensus has run: "do two canonical fields mean the same thing for this
client?".

They arise for the same reason. A controlled vocabulary wide enough to receive
unfamiliar client documents necessarily offers more than one plausible home for
a fact, so two independent engines can read one printed column and file it under
two different fields. Consensus compares one slot at a time and cannot know the
slots coincide, so it reports a vocabulary disagreement as a disagreement about
a fact. Narrowing the vocabulary would trade this cost for a worse one --
documents from the next client with no home at all -- so the equivalence is
discovered per client instead of being designed away.

Detection is deterministic and deliberately narrow. Placements are paired only
inside one container (the same line row, or the header), so a value shared by
two unrelated rows is never a candidate, and only an *identical* normalized
value forms a pair: engines that read different values disagree about a fact and
stay in the exception queue where consensus put them.

Each observation is classified by the evidence it actually carries:

| Class | Evidence | Standing |
|---|---|---|
| `cross_engine_placement` | Two engines filed one value under two names | Can support an approval-eligible proposal |
| `intra_engine_duplicate` | One engine wrote the value into both slots | Retained; never approval-eligible on its own |

The second class is a single engine declining to choose between two plausible
homes, usually with the other engine filling one of them. It is a real signal
about the vocabulary and a poor one about agreement, so it is retained, labelled,
and held short of eligibility rather than being counted as corroboration.

`--min-observations` and `--min-distinct-values` bound what is worth asking a
model about at all. The distinct-value threshold does the real work: a
coincidental collision repeats one trivial value, while a shared column carries
many different ones.

A proposal becomes a registry rule only when the client approves it **and** an
independent verifier confirmed it. `registry-update` refuses any other
combination and records each refusal with its reason, so a declined decision is
visible rather than silently absent. Approved rules carry
`rule_type: "slot_equivalence"` and can never be read as source-label mapping
rules.

## Location and city evidence

1. Treat the page itself as the primary source for a dealer, brand, city, and
   address.
2. Validate an extracted source address through `address_normalize.py` and its
   optional Google Address Validation call. This produces derived address and
   geocode evidence; it does not find an address from a name.
3. If only a dealer/brand name and city clue exist, enable Google Places (New)
   through `schema_discovery.py --google-places`. It emits a limited candidate
   set with `client_review_required=true`.
4. Require a client decision before linking a dealer/brand to a candidate city,
   address, or place. Do not infer a brand location from a dealer, or vice versa,
   without documentary/client evidence.
5. Retain aliases, candidate locations, and relationships with effective dates;
   never overwrite a prior name or location.

## CRM and RAG boundary

The approved registry, validated records, resolved parties, address evidence,
and relationship history are the vendor-neutral CRM staging interface. An
actual CRM load needs a chosen target schema, idempotency key, authentication,
load order, and reconciliation acceptance test.

The implemented local factual store indexes approved canonical document facts and linked invoice facts. Each result retains `document_id`, `source_file`, `source_page_range`, source hash, batch, registry version, and review status for citation. It excludes open review items, raw LLM responses, and unapproved suggestions with no override. See `canonical-deployment-retrieval.md`. A page-text corpus, hosted vector store, public/tenant-isolated query deployment, and query assistant require a client-approved provider, tenancy, retention, access, and redaction design; the repository's explicitly enabled TLS-only reader is only a local transport building block.

## Commands

`source_templates.json` is the source-template observation artifact.
`scripts/template_observations.py` builds it from a completed consensus run;
an extracted-record or consensus artifact passed here is refused.

```bash
python scripts/template_observations.py consensus.json \
  --out source_templates.json --exceptions source_template_exceptions.json

python scripts/schema_discovery.py discover source_templates.json \
  --registry approved_mapping_registry.json --enable --google-places \
  --graph-schema-inventory observed_schema_inventory.json \
  --out schema_discovery.json --exceptions schema_discovery_exceptions.json \
  --handoff-out schema_discovery_handoff.json --raw-dir schema_discovery_raw

python scripts/final_review_queue.py schema_discovery_exceptions.json \
  --out schema_discovery_final_review.json
python scripts/operations.py review-export schema_discovery_final_review.json \
  --csv schema_discovery_review.csv --html schema_discovery_review.html \
  --xlsx schema_discovery_review.xlsx

python scripts/schema_discovery.py slot-equivalence \
  consensus.json consensus_exceptions.json --enable \
  --registry approved_mapping_registry.json --out slot_equivalence.json \
  --exceptions slot_equivalence_exceptions.json \
  --handoff-out slot_equivalence_handoff.json --raw-dir slot_equivalence_raw

python scripts/schema_discovery.py registry-update approved_mapping_registry.json \
  schema_discovery.json client_mapping_decisions.json \
  --out approved_mapping_registry_next.json

python scripts/template_drift.py analyze source_templates.json \
  --registry approved_template_registry.json \
  --out template_drift_analysis.json
```

Do not pass `approved_mapping_registry_next.json` to a run until the client
approval decisions are retained with the run package.

## An approval does not survive re-extraction

A registry rule is keyed to a template fingerprint, and a fingerprint derives from
the **emitted label set**. Re-extracting the corpus changes that set, so every
approval keyed to the previous fingerprints is orphaned.

This has happened on a real engagement. A context-aware update over a 716-page
corpus improved capture substantially, moved every fingerprint, and left overlap
between 292 previously approved rules and the rebuilt templates at **exactly
zero**. Improving extraction invalidated every approval the client had given.

Two consequences to plan for:

- **Check overlap before asking for approval.** Compute the fingerprints in the
  current source-template artifact, intersect them with the fingerprints carried
  by the registry's approved rules, and report the count. Zero overlap means the
  superseded proposals must not be sent, and the approval cycle restarts.
- **Check overlap before running any mapping-dependent control.**
  `apply_mappings.py` and the table lanes will run to completion against an
  orphaned registry and promote nothing. That is a control that processed
  nothing, and it must be retained as a failure rather than read as a clean pass.

Sequence the approval cycle after extraction is settled. Re-extraction is cheap
relative to a client approval round; asking a client to approve twice is not.
