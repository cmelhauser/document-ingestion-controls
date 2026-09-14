# When something refuses

Most refusals in this pipeline are a control telling you about the evidence.
The fix is almost never to relax the control.

| Message | Cause | Do this |
|---|---|---|
| `duplicate independence group … a router does not create independence` | Both consensus lanes resolve to the same model vendor. | Reconfigure one lane and rerun that extraction. Independence is resolved from the model behind a router. |
| `routed model vendor cannot be resolved` | An OpenRouter model slug has no vendor prefix. | Use a vendor-prefixed slug. An unresolvable slug cannot be proven independent, so it fails closed. |
| `evidence graph would contain no nodes` | Every source artifact contributed nothing. | Find the upstream artifact that is empty. Pass `--allow-empty-graph` only to retain a deliberately empty overlay. |
| `independent_reconciliation_produced_no_comparison` | Handoffs were supplied but nothing could be compared. | Check `coverage` in the exception artifact for rejected tables, rows, and cells. |
| `gl_rows_rejected` blocks completeness | GL rows could not be bucketed or parsed. | Read `rejected_gl_rows`. Fix the export and rerun. A dropped GL row shrinks the variance, which is the one direction this control must never move. |
| `allocation_effective_rate_exceeds_plausibility_ceiling` | A commission rate above the ceiling — usually a displaced decimal point. | Check the source figure. Do not raise the ceiling to make it classify. |
| `allocation_rate_unit_ambiguous` | A rate at or below 1 could be a percentage or a decimal. | Establish the unit from the source or the approved template rule. |
| `address_country_postal_mismatch` | The declared country contradicts the postal code. | Confirm the country against the source page; do not accept either half. |
| `proved_with_unproved_lines` | The header balanced but a line could not be read. | Not a pass. The document leaves the sampling frame until the lines are read. |
| `Output already exists` | A run or retry is reusing a path. | Use a new directory. Never delete the previous one to make room. |
| `workbook XML declares a DTD or entity` | The returned workbook contains entity declarations. | Ask for a clean export. Excel, LibreOffice, and XlsxWriter produce none. |
| `GOOGLE_VERTEX_AI_MAX_PDF_BYTES=… exceeds GOOGLE_VERTEX_AI_MODEL_MAX_FILE_BYTES=…` | An operator cap is set above what the served model accepts. | Lower the cap. It is refused before work begins rather than failing mid-run at the provider. |
| `canonical export produced no approved retrieval chunks` | Every row in the export is unapproved. | Find the open review. `--allow-empty` only when a deliberately empty index is what you want. |
| `primary and buddy providers must differ` | A relationship lane is configured with one provider on both sides. | Reconfigure in `.env`. Checked before any call, so nothing has been spent. |
| `checkpoint hashes do not match` | A `--resume` was attempted against changed inputs. | Do not force it. Start a new run, or retry the retained exceptions as an overlay. |
| `Manifest artifact path escapes the manifest directory` | An artifact path points outside the run. | Treat as a malformed manifest, not a path to fix by hand. |
| `stage state manifest hash mismatch` | A stage transition is being recorded against a different run. | Confirm which manifest this run is bound to. |

## A lane that returns rows but reads nothing

Three failures on one run looked like clean runs and were not. Each is now
caught, and each is worth recognising by its symptom.

**Every empty cell holds the text `null`.** A response schema translated for one
transport was sent through another that drops its nullability flag, so the model
satisfied a strict string by writing four characters. The lane returned the right
number of rows and 222,190 cells that were not readings. The adapters now drop
that text, count it on each record as `cells_spelled_null`, and raise a blocking
`response_schema_nullability_not_honoured` exception once it passes
`SPELLED_NULL_SHARE_LIMIT` of emitted cells. If you see that exception, the
schema dialect is wrong for the transport, not the pages.

**The lane is far slower than its configured concurrency.** A setting can be
documented, defaulted, exported, and never read. `GOOGLE_VERTEX_AI_MAX_WORKERS`
was all four while the adapter's CLI passed no execution arguments, so 716 pages
ran single-threaded. `release_check.py` now refuses a release where a documented
setting is read by no script; if you add one, read it where you documented it.

**A candidate engine matches on row counts and disagrees on values.** Row parity
is not corroboration. Run `engine_agreement.py` before committing a corpus, and
read value recall and field-exact agreement rather than the row-count ratio.

## The general rule

For optional image intake, consult
[`Visual Intake`](../../../references/visual-ingestion.md), not pipeline
clearance workarounds. Missing tools mean disabled intake or insufficient
scopes; an HTTP 200 can contain a retained `rejected` receipt. A chat attachment
does not prove that the server received bytes. On quota, owner/tenant, integrity,
or unsupported-transfer failures, retain the original and receipt and stop.
Exact uncertain retries reuse the same key/input; corrections use new keys.
Source export exit 2 means blocked preparation even when verification says the
archive is byte-intact. Never use a partial PDF, overwrite a package, or promote
a journal proposal into extraction/CRM data.

If a control reports no findings, check that it processed something. A
reconciliation with `corroborated: false`, a refused graph build, or a non-empty
rejection register all mean the clean-looking result covers nothing.

## Deeper

The failure recovery matrix in
[`docs/TECHNICAL_DOCUMENTATION.md`](../../../docs/TECHNICAL_DOCUMENTATION.md)
covers source, provider, manifest, workbook, and exposure failures with the
required response for each.

## "Another <lane> extraction invocation is already active in this run"

The lane lock is a POSIX advisory `flock`, so the kernel releases it when the
holder dies. This refusal means a process really is alive.

The usual cause is an **orphaned adapter**. `run_workspace.py run` does not
propagate signals to its child, so killing the wrapper leaves `llm_adapter.py`
running -- reparented to init, still calling the provider, still spending. It will
not appear under the wrapper you killed.

```bash
ps -eo pid,ppid,etime,command | grep "[l]lm_adapter.py"
pkill -f "llm_adapter.py.*raw/<lane-dir>"
```

Confirm no process remains **and** that the lock has released before relaunching.
Treat a "stale lock file" diagnosis with suspicion: the lock cannot outlive its
holder.

## "Corpus state belongs to a different model configuration"

The retained state records the model and reasoning effort, and a resume compares
them. This refusal is the control working: resuming across a changed
configuration would blend two reading regimes into one artifact with no way to
tell which page got which.

Do not force it. Either restore the configuration the state was created under and
resume, or start a fresh pass under the current one. The partial is retained
either way.

## A table page shows `conditional_buddy_check.available: false`

The buddy check was **unavailable, not unnecessary**. `available` is set from
whether independent evidence was supplied, and the accompanying
`reason: no_llm_reported_issue` is a default written before the check runs -- so a
page with no corroboration channel looks identical to one where corroboration
found nothing.

Pass `--independent-evidence` with the independent OCR handoff and rerun. Every
page produced without it ran with that control silently disabled.

## A control ran and promoted nothing

Check fingerprint overlap before concluding the data is at fault. A registry rule
is keyed to a template fingerprint derived from the emitted label set, so
re-extraction orphans every prior approval. `apply_mappings.py` and the table
lanes will run to completion against an orphaned registry and promote nothing.

That is a control that processed nothing. Retain it as a failure; do not read it
as a clean pass.

## A number looks alarming

Before acting on any measurement you computed yourself, check it against
[`lane-checklist.md`](lane-checklist.md#measurement-traps). The recurring failures
are scanning a retained record that contains the echoed prompt, comparing lanes
that have processed different pages, and reimplementing a helper the lane already
provides. Each produces a confident wrong number, and each has driven a real
decision to spend money on the wrong remedy.

## A date field holds something that is not a date

`validate_extraction.py` reports four separate reasons here, because the same
symptom has four different causes and only one of them is an extraction fault.
Read the retained value before deciding anything:

| Reason | What the cell holds | What to do |
| --- | --- | --- |
| `date_not_rendered_in_source` | `####`, `5.21E+08`, `01/21/20...` | Nothing to re-extract. The page never showed the date. |
| `date_field_holds_a_non_date` | `TBD`, `N/A`, `128975`, `2023-0219` | A real fault: an identifier or a placeholder reached a date field. |
| `date_lacks_a_day` | `Jun 2023`, `December-24`, `2025` | A true reading of a partial date. The CRM cannot use it as a day. |
| `invalid_or_ambiguous_iso_date` | `12/9/22` | Legible, but the document never proves its own order. |

The first row is the one that surprises people. This corpus is an Excel
print-to-PDF, and a column narrower than its contents renders as `####`, drops
into scientific notation, or is cut off mid-value. The engines read those marks
correctly; the date is absent from the page, so no re-extraction and no better
model recovers it. Confirm it with `pdftotext -layout` on the page before
spending anything on a re-read.

The last row is scoped by evidence, not by convention. A date part above twelve
can only be a day, so one such part settles the order for every slash date in
that document; only a document that proves nothing keeps the finding. Never
resolve it by picking a locale.

Two failure modes to avoid when you extend this: hand-keeping the date field
list — it is derived from `extraction_schema.py` precisely because a hand-kept
copy fell six fields behind and left `transaction_date` unvalidated — and
folding all four reasons back into one, which buries the twenty-nine real faults
under the eight hundred readings that are working as intended.
## The entity lane resolved far fewer parties than the corpus has

Read `party_mentions` in the entities summary first, not `resolved_parties`. On
one run it read 728 mentions across 716 documents -- almost exactly one each --
and resolved 31 parties from a corpus carrying 836 distinct customer spellings.
Both numbers looked plausible; neither was.

Two causes, and both are the same mistake in different clothes:

- **A field the lane never looked at.** The role list is `PARTY_ROLE_FIELDS`,
  and it had fallen twelve fields behind the schema -- `customer_name` and
  `dealer_name` among them, which is where this corpus keeps the counterparty.
- **A level the lane never looked at.** It read the header only. A commission
  statement names its customer on every line and never in the header, so the
  entire party population was invisible.

A third failure hides behind the first two once they are fixed. The pairwise
comparison is capped per blocking key, and repetitions of one name count
against that cap, so the highest-cardinality parties -- the ones most worth
resolving -- were the first to be deferred. The lane now compares distinct
readings and unites the repetitions, which is why a name printed 400 times
resolves instead of being set aside.

When you widen it, check the same three things: the field list against the
schema, header against lines, and whether the cap is being spent on
repetitions rather than on distinct readings.
## A name or description stops mid-word

Part of this corpus is a wide spreadsheet printed to a narrow page, so cells
are cut off where the column ends. Dates and numbers announce it -- `####`,
`5.21E+08`, a trailing `...` -- but text does not. Page 254 prints
`NORTHGATE C(`, `CHAIR SI0` and `Sofa Ragla`, and even its own column headings
are cut (`Material D`, `Commissi`). The engines read all of that correctly.

It matters because a cut name is a new party. `NORTHGATE C` appears on 219 line
rows where the party is `NORTHGATE CO LLC`, and left alone it loads as its own
CRM account. Across the line grain, 1,654 cells in the text columns are cut
mid-word.

`field_extract_export.py` flags them in `text_truncated_in_source` and, where
the corpus offers exactly one completion, proposes it in
`completion_proposed`. Nothing is rewritten: the cut reading is what the page
shows. The evidence is the corpus itself -- a reading that is a strict prefix
of a longer reading in the same column, continuing into the same word.

Two things this must not do, and does not:

- Complete across a word break. `Ponte Verra` and `Ponte Verra; as per an
  email` are two readings, not one cut short.
- Choose between several completions. `LOUNGE C` under both `LOUNGE CHAIR` and
  `LOUNGE COUCH` is flagged with no proposal, because that is a question for a
  person. On this corpus 116 of the 203 cut values have one completion and 87
  have several.

Before re-extracting a page that looks badly read, render it and check whether
the page itself is cut. No model recovers a character the page does not carry.

## The line grain sums to more than the documents do

A statement prints its totals as rows of the same table -- `P.O. Total:`,
`Total Commissions to date:`, or simply `Total :` -- so they extract as lines
and carry a commission like every other row. On this corpus 294 such rows carry
$1,656,106.49, and one bare `Total :` row held a statement's grand total of
$148,247.29 on a page whose own lines come to $25,415.42.

`field_extract_export.py` labels them in `restates_a_total`, knowing a label by
its words or by where the word sits: alone, opening the cell on a separator, or
closing it -- in the description, or in the customer or dealer column where a
blank first cell pushes it. A label known only by position, or found in a party
column, counts only on a row naming no job, order or item of its own, because
the same shapes turn up written into a real line. The row is a true reading of
a printed line and stays; a
document's total must exclude it with the other not-a-line-of-this-document
flags. `repeats_a_line_in` is not one of them: that row is a real line, and
keeping it is the importer's decision.

Check this before quoting any figure from the line grain. The symptom is a
line-grain total larger than the document-grain one, and the cause is always
the same: the document's own subtotals were read as lines, correctly.

A fourth cause survives all of those. Where a page is printed too narrow, a
party arrives cut mid-word with nothing marking it -- `holden` for `holdens
business environments`, `wb latha` for `wb latham` -- and similarity scoring
cannot rescue a name with a third of it missing. Containment settles those
instead: a strict prefix that stops inside a word, completed by exactly one
longer name, is the same party. The completion has to continue with a letter,
because `Supply Co 40` and `Supply Co 400` are two accounts and neither is cut.

## A field is empty and re-extraction looks like the answer

It rarely is. Check what the run already holds before spending anything:

```bash
grep -ril "COLUMN HEADING" RUN/providers/raw/ | head
```

Every provider response is retained, and the independent non-LLM extractor kept
the whole page rather than the fields the schema named. A value the pipeline
dropped is usually still there, with enough layout to place it.

The full procedure, and what a recovered value may and may not be treated as,
is in [`SKILL.md`](../SKILL.md#reconcile-from-what-the-run-already-holds-before-re-reading-anything).
Re-extract only when the value is genuinely absent from every retained
artifact, and then only the affected pages.

## A sharded lane is slower than the single pass it replaced

Check the page counts across shards before assuming the corpus is hard. An even
spread means the configuration fits; an uneven one across identically configured
shards means some are caught in a timeout-retry loop.

The cause is a timeout sized for a sequential pass. Concurrency raises per-call
latency past it, calls are abandoned and retried, and the provider bills each
abandoned call it already completed. Raise `--timeout-seconds` past the observed
per-call latency and lower `--max-retries`.

## A queued job never starts although the job it waits for has finished

A `while pgrep -f other_job.sh` gate matches **any** command line containing that
string -- including a monitor whose own command line greps for it. The gate then
waits on a watcher rather than on the job, and the watcher never exits.

Check what the pattern actually matches before assuming the job is still alive:

```bash
pgrep -f other_job.sh | while read p; do ps -p $p -o command= | head -c 100; echo; done
```

Prefer waiting on the artifact the job writes over waiting on the process.
