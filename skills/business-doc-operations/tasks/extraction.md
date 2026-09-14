# Extraction: reading the documents

Three ways to read a page, for three different kinds of document. They are not
interchangeable, and only the first produces consensus-eligible readings.

| Reading | Command | Produces |
|---|---|---|
| Whole-document fields | `llm_adapter.py` (or `openai_adapter.py` / `google_genai_adapter.py`) | Consensus-eligible extraction lanes |
| Source-native tables | `table_comprehension.py`, `table_comprehension_corpus.py` | Row proposals bound to the source layout |
| Display layout | `layout_aware_extract.py`, `layout_dedup.py` | Review-only rows. Never canonical. |

## The two consensus lanes

```bash
python scripts/llm_adapter.py --lane consensus_primary MANIFEST \
  --out RUN/providers/primary.json --adapter-out RUN/providers/primary_handoff.json \
  --exceptions RUN/providers/primary_exceptions.json --raw-dir RUN/providers/raw/primary

python scripts/llm_adapter.py --lane consensus_secondary MANIFEST \
  --out RUN/providers/secondary.json --adapter-out RUN/providers/secondary_handoff.json \
  --exceptions RUN/providers/secondary_exceptions.json --raw-dir RUN/providers/raw/secondary
```

`--lane` resolves the provider and model from the environment, so the lane
setting — not the command line — is where independence is decided. Confirm it
before spending anything:

```bash
python scripts/operations.py manifest RUN/pages/ingestion_manifest.json \
  --config-from-env --out RUN/manifest.json
```

Options worth a deliberate decision:

| Option | Decide because |
|---|---|
| `--input-mode` | `auto` uses native page text only on safely rule-classified pages, PDF everywhere else. Forcing `text` on a scan reads nothing; forcing `pdf` on a clean native page costs more for no gain. `image` renders the page locally and submits pixels — the only mode that reaches a vendor which reads documents but does not accept a PDF. `auto` never selects it. |
| `--provider-only` | Comma-separated OpenRouter provider slugs permitted to serve the lane. OpenRouter picks the serving host on its own and the record names only the model, so a host with poor uptime returns truncated streams that look like a model failing to read the page rather than infrastructure failing to deliver it. Name hosts when a run's exceptions concentrate on one of them. |
| `--image-dpi` | Only with `--input-mode image`. It decides what the model can read, and it is retained per page with the image digest, so a re-read at a different resolution is a new reading rather than a repeat of the old one. |
| `--max-pages`, `--max-pdf-bytes` | Hard bounds. Work that exceeds one is an explicit exception, never a truncated reading. |
| `--cache-dir` | A hit replays a retained response instead of calling the provider. Correct for a resumed run; wrong when you need a genuinely fresh reading. |
| `--max-workers` | Concurrency only. It does not change what is read. |
| `--reasoning-effort` | Retained as provenance. It is never a term in a decision. |

`openai_adapter.py` and `google_genai_adapter.py` are the direct provider
adapters. Use them when you need one provider's exact behaviour; use
`llm_adapter.py` when you want the lane configuration to decide. OpenRouter is
reached through `llm_adapter.py` — and a routed model counts as **its vendor**,
not as "openrouter", when independence is resolved. An alias slug (`~openai/...`)
resolves to the real vendor, and an auto-routing slug (`openrouter/auto`) is
refused because it picks an unknown model at request time.

### OpenRouter and scanned pages

OpenRouter may submit a retained page PDF, but only to a model that reads files
**natively**. The adapter checks OpenRouter's own catalogue
(`architecture.input_modalities` contains `file`) before sending anything, pins
the `file-parser` engine to `native`, and refuses otherwise.

That refusal is the point. Left to its default, OpenRouter OCRs the PDF with a
separate vendor's engine and passes the text to your model — so the vendor
recorded as having read the page would not be the vendor that read it, and two
lanes on different models could sit on one shared OCR pass. Corroboration that
is not corroboration is exactly what consensus exists to prevent.

Practical consequences:

- A text-only model (most `:free` slugs) cannot be your consensus secondary on a
  scanned corpus. It is fine for the reasoning and review lanes, which send JSON.
- `OPENROUTER_INPUT_MODE=text` refuses any page without a native text layer.
  Use `auto` with a file-capable model for a scanned corpus.
- Every lane sends a strict JSON schema, so the model must also support
  structured outputs. Check `supported_parameters` in the same catalogue.
- An unreachable catalogue fails closed. Unproven support is precisely when a
  reader gets substituted.

## Source-native tables

Prefer this over whole-document extraction for tabular documents. The stages run
in order, each consuming the last:

```bash
python scripts/table_comprehension.py profile MANIFEST --page-id PAGE \
  --out RUN/tables/profile.json --raw-dir RUN/tables/raw
python scripts/table_comprehension.py rows MANIFEST --page-id PAGE \
  --context RUN/tables/profile.json --out RUN/tables/rows.json --raw-dir RUN/tables/raw
python scripts/table_comprehension.py audit MANIFEST --page-id PAGE \
  --context RUN/tables/rows.json --independent-evidence RUN/providers/docai_handoff.json \
  --out RUN/tables/audit.json --raw-dir RUN/tables/raw
python scripts/table_comprehension.py mappings RUN/tables/profile.json \
  --registry registry/mappings.json --out RUN/tables/mappings.json \
  --exceptions RUN/tables/mapping_exceptions.json \
  --handoff-out RUN/tables/mapping_handoff.json --raw-dir RUN/tables/raw/mappings
python scripts/table_comprehension.py assemble RUN/tables/profile.json RUN/tables/rows.json \
  RUN/tables/audit.json --registry registry/mappings.json \
  --out RUN/tables/assembled.json --decision-cards RUN/tables/cards.json \
  --quality-summary RUN/tables/quality.json --exceptions RUN/tables/exceptions.json \
  --adapter-out RUN/tables/handoff.json
```

`profile`, `rows`, and `audit` share one `--raw-dir` because each writes its own
`{role}_response.json` there. `mappings` does not: it delegates to the semantic
discovery boundary, whose raw files are named by template hash, so it takes a
directory of its own. Re-running a stage against a raw directory that already
holds its response is refused rather than overwritten.

`--independent-evidence` is accepted **only by `audit`**, and only after page-hash
verification. That ordering is the control: an audit that could choose its own
evidence would not be auditing anything.

`mappings` proposes; it does not map. Only an exact client-approved registry rule
maps a source label. `--propose-mappings` opts into LLM proposals and still
requires approval afterwards.

For the whole corpus:

```bash
python scripts/table_comprehension_corpus.py MANIFEST --registry registry/mappings.json \
  --client-context RUN/client/context.json --out RUN/tables/corpus.json
```

`--refinement-passes` bounds the rereads and `--refinement-tolerance` decides how
many changed rereads a pass tolerates. **A changed reread is an amendment
proposal**, not a correction — it does not overwrite the original reading.
`--client-context` carries hash-bound reasoning-only client comments into every
forward and refinement packet; comments explain terminology and priorities and
are never evidence, authorization, or clearance. `--resume` continues a stopped
corpus pass through its verified state.

## Display layout — review only

```bash
python scripts/layout_aware_extract.py page MANIFEST RUN/layout/layout.json --page-id PAGE \
  --out RUN/layout/page.json --raw-dir RUN/layout/raw
python scripts/layout_aware_extract.py combine RUN/layout/layout.json RUN/layout/page*.json \
  --out RUN/layout/combined.json --exceptions RUN/layout/exceptions.json \
  --adapter-out RUN/layout/combined_handoff.json
python scripts/layout_dedup.py RUN/layout/combined.json RUN/layout/layout.json \
  --unique-out RUN/layout/unique.json --duplicates-out RUN/layout/duplicates.json
```

This produces a display for reviewers. Do not promote its client layout to a
canonical mapping, and do not feed it to consensus. `layout_dedup.py` retains
duplicates in their own file rather than dropping them, because a repeated
document in a multi-year batch is review work, not noise.

## Semantic mappings

`discover` reads a **source-template artifact**, not a run artifact:

```json
{"templates": [{"template_id": "commission-statement-v1",
                "headers": [{"source_label": "Total Commissions to date"}]}]}
```

`template_observations.py` produces this artifact from completed consensus
records; `schema_discovery.py`, `allocation_policy.py`, and
`template_drift.py analyze` consume it. Pointing `discover` directly at a
consensus run still fails with "Input must contain templates with template_id
and non-empty header source_label values".

```bash
python scripts/schema_discovery.py discover SOURCE_TEMPLATES.json --enable \
  --registry registry/mappings.json --out RUN/controls/discovery.json \
  --exceptions RUN/controls/discovery_exceptions.json \
  --handoff-out RUN/controls/discovery_handoff.json \
  --raw-dir RUN/controls/raw/discovery
python scripts/schema_discovery.py registry-update registry/mappings.json \
  RUN/controls/discovery.json RUN/controls/decisions.json --out registry/mappings.v2.json
```

`discover` uses independently configured primary and buddy providers, and
`--no-buddy` turns the confirmation off — which also removes the only thing
making the proposal more than one model's opinion. `--graph-schema-inventory`
accepts bounded `evidence_graph.py schema` output as corpus context; it is
context, never mapping evidence. `--google-places` adds candidate lookups, and a
candidate is a proposal, not an approved mapping.

## Slot equivalence — after consensus

Two engines reading one printed column can file it under two different
controlled fields, because a vocabulary broad enough for unfamiliar client
schemas offers more than one plausible home for a fact. Consensus refuses those
correctly and cannot resolve them: it compares one slot at a time.

```bash
python scripts/schema_discovery.py slot-equivalence \
  RUN/controls/consensus.json RUN/controls/consensus_exceptions.json --enable \
  --registry registry/mappings.json --out RUN/controls/slot_equivalence.json \
  --exceptions RUN/controls/slot_exceptions.json \
  --handoff-out RUN/controls/slot_handoff.json \
  --raw-dir RUN/controls/raw/slots
```

Read the `evidence_classes` on every proposal before doing anything with it:

| Class | What it is | Weight |
|---|---|---|
| `cross_engine_placement` | Two engines filed one value under two names | The real signal |
| `intra_engine_duplicate` | One engine wrote the value into both slots | An engine declining to choose, not agreement |

A pair supported only by `intra_engine_duplicate` is retained and is **never**
approval-eligible. `--min-distinct-values` is what separates a real equivalence
from one repeated coincidence: a quantity of 1 meeting a line number of 1
repeats a single value, while a shared column carries many different ones.

Nothing here collapses a field. `registry-update` writes a `slot_equivalence`
rule only from a proposal the client approved *and* an independent verifier
confirmed, and it records every decision it refused and why.

## Before trusting a new engine

Changing a lane's model or its transport is not a configuration change; it is a
new reader, and it has to be qualified on values before a corpus is committed to
it. `engine_agreement.py` reports value recall and field-exact agreement against
an engine already trusted -- see the qualification section in
[`cross-checking.md`](cross-checking.md), which records what this run measured
and what it cost to learn.

Two transport-specific traps this run hit, both silent:

* A response schema translated for one transport is not translated for another.
  Vertex's native API understands `nullable`; an OpenAI-compatible bridge drops
  it, and the model then answers a strict string with the text `null`. The
  adapters now pick the dialect from the transport and discard a cell whose
  value is that text, but a new provider path needs the same check made afresh.
* Reading the retained text layer is not automatically better than reading the
  page. On this corpus it was much worse -- 30% value recall against 56% -- 
  because the extracted text loses the column grid the table depends on.

## One corpus, several forms — check before trusting a field

A corpus from one vendor is not one layout. This engagement's 716 pages hold at
least five Marlow/Bramwell forms alone, and the difference between them decides
what a field can legitimately hold:

| form | commission columns | commissionable base |
|---|---|---|
| `Commissions Due` (commdue) | Furn/Table/Lea/Fab/Masq/Fixed + **Total** | **Billed** column |
| `Weekly Order Status Report - With Commission` (COMMREP) | one **Commission Total** | none printed |
| `Weekly Order Status Report - ALL` (COMMREP) | FURN/LEATH/FABRIC/MASQ + **TOT** | **none printed** |
| `Weekly Order Status Report` (COMMREP2) | none | none |

The third one broke. With nothing said about a commission split across category
columns, both engines read the leftmost column as the commissionable amount and
a middle category as the commission — so 24 pages carried a base the page never
printed, the pair reconciled to nothing, and one page of eight real lines came
back as a hundred rows with a single row repeated ninety-two times. The schema
now names the rule: **the total column is `commission_amount`, a column under a
Commission heading is never the base, and a page that pays commission without
printing what it was computed from has no `commissionable_amount` at all.**

**How to catch the next one without reading 716 pages.** These signals found it
from the export alone, before any page was opened:

* `commission_amount` equal to `commissionable_amount` on many lines — a 100%
  rate is almost always two columns read from the same block.
* a document's line count far exceeding what a page of that size can hold, with
  few distinct values — check `duplicate_of_line` in the field extract.
* `commissionable_amount` populated on a form whose siblings never populate it.

Then classify the corpus by the form's own printed title before deciding what to
re-extract. The text layer here is corrupt — `MARTIN` reads `MARTTN`, `Report`
reads `Repoft` — but the corruption is in the glyphs and the title line still
separates the forms, so `pdftotext -layout` over the first few lines of each page
groups a corpus in a minute and costs no provider calls. Re-extract the form,
not the vendor: of 111 pages naming Marlow/Bramwell, 24 were the broken layout
and 60 were a sibling form that was already reading correctly at 17 of 17 lines.

**Qualify the second reader on the corrected schema too.** A rule only helps if
the engine follows it. On the re-read, the primary emitted zero commissionable
amounts as instructed and the incumbent secondary emitted 93, still reading the
FURN column as the commission — so the pair agreed on 33% of line commissions
and settled almost nothing. An independent third vendor followed the rule and
agreed with the primary line for line, taking the same pages to 41% overall and
100% on the pages checked by hand. See
[Before trusting a new engine](#before-trusting-a-new-engine).

## A field the schema never defines is a field engines guess at

`FIELD_DEFINITIONS` is what reaches an engine through `field_glossary()`. A
field that is named in `LINE_FIELDS` but absent from that map carries no
meaning into the prompt, and two vendors will confidently agree on the same
wrong reading -- agreement measures whether they read alike, never whether the
field means what they assumed.

This bit hardest on the field the corpus leans on most. `stated_commission_rate`
was undefined while 5,088 line rows carried it, and one vendor's emails print a
**Commission Split** column holding `1.00`, `0.20`, `20-80`. Both engines read
that into the rate. Zero of those rows reconcile as a percentage: `5,880.00`
at a "rate" of `1.00` pays `588.00`, which is ten percent, not one. The pages
are transcribed perfectly -- the reading is right and the field is wrong, which
is why no amount of re-extraction or vendor agreement would have found it.

Before trusting a numeric line field, check two things:

1. **Is it defined?** `[f for f in LINE_FIELDS if f not in FIELD_DEFINITIONS]`
   answered 35 of 48 the first time it was asked.
2. **Does it reconcile?** For a commission corpus, `commissionable_amount x
   stated_commission_rate` must equal `commission_amount`. Group the failures
   by cause before calling any of them an extraction error: on this corpus
   96.3% of provable rows reconcile, and of the rest the largest groups were a
   source document whose own arithmetic disagrees, a rate printed rounded to
   two decimals, and a territory split where the commission is taken on the
   full job. Only the split-column class was ours.

Defining a field changes `schema_fingerprint()`, which is deliberate: the
response cache keys on that hash, so old responses are never replayed against a
new definition. Expect to re-extract, and expect prior template approvals to be
orphaned.

## A printed column with no field goes somewhere it does not belong

A schema that names no home for a column does not make the engine skip it. The
value lands in whichever field looks closest, and both vendors pick the same
one, so it arrives accepted.

145 pages of this corpus print a **SPECIFIER** column -- the design firm that
chose the product. The schema had no specifier field at all. The result was not
one defect but two: on some rows the firm was written into `brand_name`
(`NORCROSS - TAMPA` and `BSQ - FLORIDA` are architects, not manufacturers), and
on the rest a real counterparty was simply discarded. Nothing reached
`source_labelled_fields` either, so it was not recoverable afterwards.

The fix has to be both halves. Forbidding the wrong field alone just moves the
value to the next-closest one; the column needs somewhere to go:

1. Add the field to `HEADER_FIELDS` and `LINE_FIELDS` and define it.
2. Say in the *neighbouring* definitions what it is not, naming the new field.
   `brand_name` now says a SPECIFIER column belongs in `specifier_name`, and
   that a value already in `dealer_name` or `customer_name` is not a brand.
3. Give it a role in `entity_resolve.PARTY_ROLE_FIELDS` if it names a party, or
   it resolves to nothing. The coverage test catches this: adding a `*_name`
   field to the schema fails `test_the_party_role_list_covers_the_schema` until
   the role exists.

**Before re-extracting to capture it, check whether the run already holds it.**
The independent non-LLM extractor reads the whole page, so a column the LLM
schema could not name is usually still in `RUN/providers/raw/docai/` with its
layout intact, joinable by a printed key the extracted line already carries.
That is how the SPECIFIER column was recovered here without a provider call --
see [`SKILL.md`](../SKILL.md#reconcile-from-what-the-run-already-holds-before-re-reading-anything).
A schema fix serves the next run; a recovery serves the one already paid for.

To find the next one, compare the column headings a form prints against the
fields it emits. `pdftotext -layout` reads a heading well enough even where the
text layer is corrupt, and a heading the schema cannot name is the next value
about to be misfiled.

## After extraction

`consensus.py` → `arithmetic_check.py` → `adjudicate.py` →
`validate_extraction.py`, then the identity controls. See
[`run-pipeline.md`](run-pipeline.md) for the exact sequence, and
[`cross-checking.md`](cross-checking.md) for the corroboration lanes that attach
here.
