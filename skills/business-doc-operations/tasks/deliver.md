# Final documents, CRM files, and the client folder

## Order

Canonical export → load plan → CRM staging → delivery folder. Each step checks
the one before it; running them out of order produces artifacts that cannot be
verified.

```bash
python scripts/canonical_export.py \
  --manifest RUN/pages/ingestion_manifest.json \
  --consensus RUN/controls/consensus.json \
  --arithmetic RUN/controls/arithmetic.json \
  --final-review RUN/review/final_queue.json \
  --batch-id "$(uuidgen | tr 'A-Z' 'a-z')" \
  --out RUN/canonical/export.json \
  --exceptions RUN/canonical/export_exceptions.json
python scripts/canonical_load.py RUN/canonical/export.json --out RUN/canonical/load_plan.json
python scripts/csv_api_staging.py RUN/canonical/export.json RUN/canonical/load_plan.json \
  --out RUN/canonical/staging
python scripts/crm_import_package.py RUN/canonical/export.json RUN/canonical/load_plan.json \
  --out RUN/canonical/crm_import
```

`canonical_export.py` is the gate that decides what a batch may contain. A
document reaches the export only with retained intake provenance, a review-clear
consensus status, arithmetic that is proved or established as inapplicable, and
no open item in the final-review queue. Everything it withholds is named in
`export_exceptions.json` with the control that withheld it, so an empty export
reads as *nothing is approved yet*, never as *nothing was found*. It also
withholds any agreed field that no canonical column accepts, rather than
inventing one — those are a mapping question for the client.

**Expect an empty export before the review is worked.** That is the control
operating, not a failure. `canonical_load.py` and `csv_api_staging.py` will both
run over it and both say in their own artifacts that they moved zero rows;
`retrieval_store.py build` refuses outright unless you pass `--allow-empty`.

`canonical_load.py` admits only review-clear, provenance-linked rows; there is no
override for an open review. `csv_api_staging.py` requires the load plan to match
exactly, which is what makes the staging package checkable against it.

### Optional: PostgreSQL and retrieval

```bash
python scripts/canonical_deploy.py --schema assets/canonical_schema.sql \
  --out RUN/canonical/deploy_plan.json
python scripts/retrieval_store.py build RUN/canonical/export.json --out RUN/canonical/facts.sqlite
python scripts/retrieval_store.py query RUN/canonical/facts.sqlite "unpaid ACME invoices" --limit 5
```

`canonical_deploy.py` writes a plan by default; `--execute` applies it with psql
and crosses a real boundary. `retrieval_store.py build` refuses an export with no
approved chunks rather than building a database that answers "no results" to
every question — `--allow-empty` retains one deliberately. Serve it read-only
with `retrieval_mcp.py` over stdio, or `retrieval_https.py` with explicit TLS,
bearer auth, and `--enable`.

### Activate the MCP for a completed run

The approved-fact MCP is a Phase 6 consumer, not a way to inspect unfinished
pipeline extraction or review proposals. Before registration, require the selected-lane coverage
report, workspace audit, clear exhaustive final queue, approved canonical
export, exact load plan, and a non-empty retrieval snapshot built without
`--allow-empty`. Follow
[`references/mcp-production-integration.md`](../../../references/mcp-production-integration.md)
for the exact commands and acceptance sequence.
For setup, deployment, client registration, queries, exports, monitoring,
rebuild, and shutdown, follow the dedicated
[`mcp-api-operations` skill](../../mcp-api-operations/SKILL.md).

For Claude Code operating the completed run, register the local launcher with
absolute paths and private local scope:

```bash
claude mcp add --scope local business-document-retrieval -- \
  bash /ABSOLUTE/REPOSITORY/scripts/run_retrieval_mcp.sh \
  /ABSOLUTE/RUN/canonical/business_retrieval.sqlite
claude mcp get business-document-retrieval
```

Then use `/mcp` to verify the twelve baseline read-only tools. Start with capabilities
and export summary, confirm the snapshot checksum and schema, and only then run
search, exact lookup, account cards, sales analysis, reports, or bounded table
exports. Do not register the current run while `review/` or `canonical/` is
empty, while the final queue is open, or while a required lane is absent.

`csv_api_staging.py` builds a **no-send** package. It does not transmit anything
to a target system; delivering to a CRM is separate, client-authorized work.

`crm_import_package.py` is the optional common-object bridge for a selected CRM
team. It creates accounts, roles, addresses, contacts, products, locations,
shipments, sales transactions and lines, charges, payments, and payment
applications as formula-safe UTF-8 CSV files. It also creates a tenant-completion
mapping worksheet, official vendor-source list, complete canonical coverage
register, and approval-gated write plan. The vendor field suggestions are
proposals only: export the actual tenant metadata, complete and approve the
mapping, dry-run in a sandbox, and separately authorize any target mutation.

## Reports

The optional intake journal is not a delivery database. Its source package
contains sensitive original attempts and unapproved proposals; do not add it
wholesale to an approved-data package or interpret it as a common CRM import.
Keep it in the controlled evidence archive, bind the original-to-derivative
page map to pipeline provenance, and include its exceptions in final review.
Any separate source-archive disclosure needs an explicit retention/delivery
decision. The ordinary approved-fact export and delivery allowlists still apply.

Explain the output with `docs/CLIENT_OUTPUT_OVERVIEW.md` and its generated PDF.
It distinguishes approved queries/reports and CRM-ready files from optional
new-image proposals. Read `references/business-data-platform-roadmap.md` before
promising record maintenance, custom saved reports, or client capture features.

Regenerate any tracked document whose Markdown source changed, then render and
inspect it:

```bash
bash scripts/generate_docs.sh      # Markdown → LaTeX
bash scripts/render_docs.sh        # LaTeX → PDF
pdftoppm -png -r 80 docs/DOC.pdf /tmp/pages/page
```

Look at every changed page — margins, table boundaries, headers, wrapping,
legibility. A PDF that has not been looked at is not ready to hand over.

## The delivery folder

```bash
python scripts/client_delivery_package.py \
  --out-dir RUN/delivery/client_YYYYMMDD \
  --final-review RUN/review/final_queue.json \
  --canonical-export RUN/canonical/export.json \
  --evidence-graph RUN/canonical/graph.json \
  --review-file RUN/review/consolidated.xlsx \
  --crm-file RUN/canonical/staging/accounts.csv \
  --document docs/CLIENT_OVERVIEW.pdf \
  --document docs/CLIENT_PROCESS_PLAIN_LANGUAGE.pdf
```

It creates a new directory containing only the deliverables, refuses to copy raw
provider responses, credentials, caches, or resume state even if you name them
explicitly, and writes a manifest hashing every delivered file.

The evidence graph is delivered **cleaned**: proposal-state nodes and edges are
withheld, because a client folder is read as a statement of fact and a proposal
is not one. The exact count of what was withheld is retained in the graph file
and restated in the delivered README, so withholding is visible rather than
silent.

## The refusal you will meet

If the final review gate is not `clear`, the command refuses. That is correct —
you are about to state facts to a client while findings are open. Resolve the
queue, or deliver deliberately provisionally:

```bash
python scripts/client_delivery_package.py ... --allow-open-findings
```

The package is then marked `provisional: true` in its manifest and says so at the
top of its README. Use it when the client has asked for an interim view and
knows that is what they are getting.

### When the canonical export admits nothing

On a corpus where every document carries an open exception, that refusal returns
**zero rows** -- 0 of 716 on the commission run -- while the same run held 73,878
accepted fields of 83,453. Nothing was wrong with the gate; a per-document rule
cannot pass a document with one open finding, however much of it is settled.

`field_extract_export.py` is the way to see that evidence without weakening the
gate. It writes every field with the status that governs it, and
`--lines-csv` / `--documents-csv` pivot the rows into the record grain a CRM
import loads.

```bash
python scripts/field_extract_export.py RUN/controls/records_with_mappings_07.json \
  --manifest RUN/pages/ingestion_manifest.json \
  --final-review RUN/review/final_queue.json \
  --out RUN/delivery/field_extract.json \
  --lines-csv RUN/delivery/crm_commission_lines.csv \
  --documents-csv RUN/delivery/crm_documents.csv
```

Four things to get right, all of which were got wrong first:

* **Point it at the run's current record artifact, not its consensus artifact.**
  Acceptances are appended after consensus by `apply_corroboration.py` and
  `apply_mappings.py`. The same 716 documents read 26,300 accepted fields from
  `consensus_04.json` and 73,878 from `records_with_mappings_07.json`.
* **Sum the `__amount` companion, never the money column itself.** The page
  prints `$6,226.00`, `856.13 USD`, `(662.89)`; a loader reading those into a
  numeric column gets a string, a null, or a silent zero, and a naive parse of
  one run lost $2.8M to the dollar signs alone. Every money, count and date
  column has a typed companion -- `__amount`, `__currency`, `__iso`, and
  `__month` for a month named without a day -- beside the original, which is
  left exactly as the page said it. An empty companion
  means the value could not be resolved without guessing, and that is the
  point: `05/06/2024` is May 6th or June 5th, and `12,34` among decimal points
  is a cut cell rather than a French amount. A document settles its own
  convention instead. One that carries a date like `11/16/2022` has proved its
  order and all its slash dates resolve -- 362 of 403 documents settled it that
  way, none contradicting itself -- and one that prints `2 199,03` and no decimal
  point has proved its decimal comma, so its amounts type: murbrook's two
  Canadian statements carried 40 that had loaded as nothing. A rate carries
  `__percent` only where its unit is proven, by a `%` on the page or a line whose
  own base x rate reconciles, so a bare `1.00` beside a line that does not stays
  empty rather than loading as one percent or one hundred. A year of fewer than four digits --
  `0122-05-24`, a cell that cut the century off -- is refused rather than
  loaded into the second century, and a count the page masked carries `-99999`
  like a masked amount.
* **The line flags do not all mean "exclude", and treating them alike drops
  three quarters of the money.** Summing the rows that carry no flag at all
  returns $4,287,567 of this run's $16,674,109. That is not a measurement of
  what is trustworthy; it is two different questions answered as one. The flags
  fall into two groups and a total must ask them separately.

  *Not a line of this document* -- exclude these to total one page:
  `duplicate_of_line` (one engine padded a page of eight real lines out to a
  hundred by emitting one row ninety-two times, changing only the ordinal),
  `restates_a_total` (the report's own total rows -- `P.O. Total`, or a bare
  `Total :` -- which a loader adds on top of the lines they summarize),
  `repeats_an_earlier_block`,
  `repeats_a_job_and_amount_above` (a page read in two passes emits each line
  again with different columns filled -- the customer on one pass, the dealer on
  the other -- so no two rows are equal and the duplicate check clears them
  all), and `amount_without_line_identity` (money the page attaches to no
  product, job, project or purchase order).

  *A real line of this document that also appears elsewhere* -- a decision, not
  a defect: `repeats_a_line_in` names another document carrying the identical
  line. These statements are periodic and a project stays on them until it is
  paid. Filter it if you are loading projects, keep it if you are loading
  statements -- but do not filter it when totalling a single page, which is what
  reduced two hand-checked pages to one row each.

  Excluding only the first group reproduces the hand-read total of every page
  checked against its image: p0010 to 39,300.42 over eight rows and p0515 to
  38,898.17 over twelve, each to the cent. Over the corpus it totals
  $12,868,180 across 6,223 rows, and leaves 72 documents carrying $154,329 with
  no countable line -- down from 151 documents and $3.9M before
  `amount_without_line_identity` learned that a job number names what money is
  for.
* **Where a line recurs, the corpus is a witness to the fields no arithmetic
  can test.** `differs_from_the_same_line_elsewhere` and
  `missing_where_the_same_line_carries_it` name the columns where this row
  disagrees with the same line on other statements -- 2,091 and 1,011 rows
  here. Neither excludes a row and neither changes a value: the majority is not
  a vendor agreeing and not an acceptance, it is the corpus repeating itself.
  Use them to review, and read the field names: a disagreement in
  `transaction_date` or `description` is the common case, one in
  `commissionable_amount` is rare and worth looking at first.

  The evidence is real because these statements are periodic. A `PROJECT
  CANCELLED` stamp printed on twenty statements survived in `description` on
  twelve, landed in `charge_description` on one, and left no trace on seven,
  where a cancelled project with $28,950.92 commissionable reads as a live one.
  No arithmetic could have found that; the other nineteen statements did.
* **`confidence` and `commission_arithmetic` are carried, never excluded.** A
  row whose arithmetic reads `does_not_reconcile` is a row the page states and
  the document's own numbers contradict. It stays in the total and stays
  labelled; dropping it would hide the discrepancy rather than report it.
* **A row's status says how its acceptance was earned, and the ways are not
  interchangeable.** `accepted_independent_corroboration` is a value one model
  read that a non-LLM extractor also read on that page -- 47,578 fields here. It
  defeats a misread, not a misattribution, and presenting it as
  `accepted_vendor_agreement` is the one claim the corroboration artifact says of
  itself that it is not. Each lane is registered by the `accepted_by` it writes:
  document arithmetic, same-document settlement and the extractor tiebreak were
  all added after `field_status` and each reported as vendor agreement until
  they were registered, overstating 3,566 fields here. A lane whose `accepted_by`
  is not in the table reads `accepted_by_an_unregistered_lane`; if you see a
  non-zero `summary.fields_accepted_by_an_unregistered_lane`, register the lane
  before sending anything, because the count is telling you the export cannot
  describe evidence the pipeline acted on. `accepted_page_review_amendment` is a
  value read against the page image and backed by the independent extractor's
  text of the page or a retained engine reading; the value it replaced stays on
  the field in `superseded_consensus`, so a loader can always show what the page
  review changed.
* **`confidence` is the column a client reads; `status` is the column an auditor
  reads.** One word per cell -- `confirmed`, `corroborated`, `computed`,
  `derived`, `unconfirmed` -- and one per pivoted record in `row_confidence`,
  which takes the weakest cell in the row and names the cells responsible in
  `unconfirmed_fields`. A row with nine corroborated cells and one contested
  cell is not a confirmed row. The evidence counters add up to the cells in the
  record -- `fields_unattributed` is the final bucket that makes that hold, and
  it should read zero. A non-zero one means a cell reached the CRM that no
  column describes.

This artifact is not a canonical load, clears no control, and no Phase 6 command
reads it. It is how an operator sees what the run holds while the gate is
correctly refusing to publish it.

### Clearing a document the operator has actually answered

`field_extract_export.py` shows the evidence; it does not move a document. The
reason nothing moves is narrower than it looks: **`consensus.py` is the only
control that computes a document's `review_status`**, it computes it once from
the readings, and every later control -- the arithmetic vote, the corroboration
applier, the classification amendment -- copies that value forward untouched. So
a document that entered review as `open_exception` stays there no matter what is
later established about it. On the commission run the arithmetic vote reported
620 documents promoted while all 716 statuses stayed byte-identical, and the
export still admitted nothing.

`exception_resolved` is the review-clear status reserved for a document whose
findings were answered rather than re-read. `exception_resolve.py` is what writes
it.

```bash
python scripts/exception_resolve.py RUN/controls/records_final_02.json \
  RUN/client/authorized_plan_01.json \
  --out RUN/controls/records_exceptions_resolved_01.json \
  --report RUN/controls/exception_resolution_report_01.json
```

Hand it the run's **current** record artifact, not the generation an earlier
section happened to name. On the commission run the same measurement over
`records_with_mappings_07.json` and over `records_final_02.json` -- a day apart
-- reports 98 and 129 documents held open by metadata alone, and only the
second is this run's answer.

Pass `--exceptions IN OUT` once for **every** retained exception artifact that
carries one of the answered findings. Clearing the status is only half the job:
the final review gate is built from those artifacts and the canonical export
refuses any document the gate still holds an item against, so an answered
finding left standing there blocks the document you just cleared. On the
commission run the answered findings were spread across ten artifacts -- the
classification and consensus exceptions, both provider exception files, and a
`corpus_exceptions.json` in each of the six table directories. Miss one and the
document stays out.

Then rebuild the gate from the amended artifacts and hand the resolved records
to `canonical_export.py --consensus`, which reads them directly: the records
keep the consensus shape. Rerun `arithmetic_check.py` on the **resolved**
records before rebuilding the gate -- the arithmetic artifact carries each
document's review status forward, so running it first files a fresh
`document_status_open_exception` against every document you just cleared.

What it will and will not do:

* **A document clears only when the authorization resolves every field still
  queued on it.** Partial authorization leaves the document open and names the
  unanswered findings in the report. This is the whole point of the control --
  clearing on a partial authorization would bury exactly the findings nobody was
  asked about.
* **A patch resolves the fields its own `impact.fields` names, and no others.**
  The `scope` in the payload is deliberately not read as a licence to clear a
  document whole: every compiled patch on the commission run declared
  `scope: document` while naming the single field `document_type`, so honouring
  the scope would have cleared hundreds of findings on each document that the
  client never saw.
* **Expect zero on a first run, and read the report rather than the count.** On
  the commission run the signed authorization covers `document_type` while the
  median named document carries 9 other queued findings (mean 20.7, worst 200),
  so nothing clears and the report says so per document. That is the control
  working. Getting a non-zero count means widening what the client was actually
  asked, not widening the control. Read `unresolved_finding_count` rather than
  the length of `unresolved_fields`: the field list is a sample, capped at 12.
* Nothing is deleted. A cleared document keeps `prior_review_status` and gains a
  `resolution` block naming the authorization, the moment, and every finding it
  answered, so a cleared status always reads back to the signature that produced
  it. A document already `auto_accepted` or `sampled_verified` is never
  relabelled -- `exception_resolved` claims something weaker and must not
  overwrite it.

## Before you send

Read `manifest.json`. Confirm the file count is what you expect, that
`final_review_gate_status` is what you intend to be delivering, and that nothing
in the list surprises you.

## Telling a CRM what a row cannot say for itself

Three columns on the line grain exist so a loader is never asked to infer a
problem from an absence:

- `unreadable_in_source` names every column whose value the page did not
  render -- `####`, scientific notation, a cut-off ellipsis. The typed money
  companion carries `-99999`, an out-of-range constant chosen so a load fails
  loudly rather than accepting a plausible zero. It is deliberately **not**
  written into a date companion: a date column cannot hold `-99999`, and
  trading a detectable gap for an unparseable one helps nobody. The flag names
  the date column instead.
- `commission_arithmetic` says `reconciles`, `does_not_reconcile`, or nothing
  where the row lacks a term to judge. A failure here is usually the document
  disagreeing with itself: page 6 of the commission run prints
  `7,646.16 x 8% = 2,038.98`, which is wrong on the page while every other row
  on it is right. The row is carried unchanged and labelled -- correcting a
  printed number would be inventing evidence.
- `restates_a_total` marks the document's own subtotal rows.
- `repeats_an_earlier_block` marks a line belonging to a second copy of the
  document's own table, which `duplicate_of_line` misses because the copy
  arrives less complete than the original. **A total must exclude the flagged
  block.**
- `party_field_holds_the_project` marks a company field holding the job a line
  was sold into: the field repeats the line's description beside a job or
  project number. A building is never a dealer; `project_name` is where the
  value belongs. Flagged, not moved -- most of those names are also real dealers
  elsewhere in the corpus.
- `rate_column_holds_an_amount` marks a rate cell that is really the commission,
  proved against the rates the same document states on rows that reconcile.
- `column_read_one_row_low` marks a cell holding the value a sibling money
  column carried on the row above, for two or more consecutive rows -- a table
  read one line low. This is the quietest way money goes wrong: the number is
  real and printed on the page, so no format, range or arithmetic check on that
  row can see it, and only the neighbouring columns give it away. 100 rows over
  17 documents on the commission run, $849,161 of it in `commission_amount`. On
  one of those pages the source prints a single money column, `Adjusted Net
  Sale`, and no commission at all, yet fourteen rows carried one. **Do not load
  a flagged money cell without reading the page.** Nothing is moved: the run
  says the column drifted, not where it started.

- `commission_equals_its_own_base` marks a commission equal to the amount it is
  a commission on -- one number read into two columns, on pages that print no
  commission column at all. $3.25M on this corpus. **Never load a flagged row's
  commission.**

**Quote the reconciliation rate with its denominator.** `commission_arithmetic`
can only judge a row that states a base and a rate. On the commission run 96.7%
of provable rows reconcile, and 35.8% of the money is on documents where nothing
is provable -- `commission_no_control_can_test` in the export summary names that
figure. A rate quoted without it is not an accuracy statement about the corpus.

The rule behind all seven: never repair a reading to make it agree with
arithmetic, and never drop a row for failing a check. Carry it and say what is
wrong with it.

**Inferred values have columns of their own.** `augment_export.py` writes a new
pair of grains in which `<column>__inferred` sits beside the cell it fills,
`<column>__inferred_by` names the method, and `<column>__inferred_evidence` says
what supports it. Load a reading from its own column and an inference from its
`__inferred` companion, and keep the method with it: a CRM that cannot tell a
brand the page printed on the line from one carried down from the letterhead
has lost that difference for good. Only `two_vendors_agree` rests on two
vendors; every other method is the run's own evidence carried across, or a
cited public source.

An inferred party also carries its account's key in
`<column>__inferred_party_key`, so join it the way you join a reading, on the
key. `same_key_on_other_lines` is the one method scored before it fills: it
hides the party on each line that prints one and asks for it back, and a field
it names correctly on fewer than 95% of them fills nothing. Read each field's
score beside its chance baseline in `parties_from_shared_keys`.

## Validate the export against the schema it loads into

The export writes what the pages said. Whether that can survive the target is a
different question, and a CSV has no types, so nothing in the export answers it.
Run this after the export and before handing anything to a CRM team:

```bash
python scripts/crm_input_validate.py \
  --grain lines=RUN/delivery/crm_input_lines.csv \
  --grain documents=RUN/delivery/crm_input_documents.csv \
  --schema assets/canonical_schema.sql \
  --out RUN/delivery/crm_input_validation.json \
  --findings-csv RUN/delivery/crm_input_findings.csv
```

Seven faults, each of which reached a real CRM before this existed:

| Finding | What it means |
| --- | --- |
| `no_typed_companion` | A date or numeric column reaches the loader as text with nothing to cast. `DATE_COLUMNS` had drifted to seven of seventeen, leaving 362 populated cells untyped. |
| `multiple_values_in_a_single_valued_column` | `contact.phone` is one `VARCHAR(48)`; a page printing an office *and* a direct number puts both in one cell, and loading it truncates the value or splits the contact in two. |
| `longer_than_the_target_column` | A loader truncates silently. |
| `placeholder_standing_in_for_an_identity` | `payment_reference` reads `00` on eighteen documents. Keyed on, those collapse into one payment. |
| `address_shaped_value_in_a_product_column` | A street address in an item description -- a true reading of a project name, and still not a product. |
| `value_outside_the_target_enum` | The value is the right length and the wrong member. `review_queued` is a status this pipeline writes and `review_status_t` does not admit, so 31 rows fail on the type rather than the width. |
| `references_a_dimension_row_the_run_never_creates` | A foreign key whose dimension row has to be seeded first: `document_type` reads `unknown` on 664 of 683 documents, and `charge_code` reads `ZCO1`/`ZCO2`. Reported once per distinct value with the rows waiting on it. |

The last two read the enum members, the columns declaring one, and the foreign
keys out of the DDL rather than from a list kept here. That is deliberate: every
defect found in this round began as a hand-kept list drifting from the schema it
mirrored. The same derivation decides which foreign keys are worth reporting --
a table the pipeline fills carries a `review_status` and a seeded vocabulary does
not, which is why `job_number`'s 699 values stay out of the report and
`document_type`'s six stay in.
| `country_name_where_a_two_letter_code_belongs` | Every canonical country column is `CHAR(2)`; `United States of America (the)` truncates to `Un`. It needs a lookup, not a cast. |
| `more_decimal_places_than_the_target_keeps` | The **declared** scale decides. `unit_price` is `NUMERIC(18,4)`, so four places survive and five do not -- read the DDL rather than assuming two. |

**A repeated value is not automatically a fault.** An invoice number or
transaction id is a reference many lines legitimately share, because a statement
restates the same invoice until it is paid; only a column meant to identify one
record earns a collision finding. Flagging the rest buried the real ones under
417 false positives the first time this ran.

Nothing is repaired. Each finding names the row, the column and what the target
expects, because the answer is a mapping decision rather than a rewrite of a
reading.

## Products are a dimension, not a column

`--items-csv` writes the distinct products the line grain names. The line grain
repeats a product on every line that sells it -- 9,144 rows carrying 980
distinct items on the commission run -- and loading it as products creates one
product per line.

Each row carries the identity the line stated, every description seen with it,
and how many lines and documents it came from. Two signals show a reading that
is not a product: `not_a_product` names a shape that belongs elsewhere (a
territory code, a line item number, an address), and a high `description_count` says
the same thing from the data -- `10` appeared as an item code carrying
twenty-five different descriptions, which no real product does. Nothing is
dropped: the reading keeps its row and the label says what it is.

## Products come from each manufacturer's own rule

`--items-csv` lists what the line grain names. It cannot say which column holds
a product, because that depends on the manufacturer: on the commission run
`description` holds a murbrook project, a HALVOR project client, a Lumen Weft
specifier, the project of Marlow/Bramwell's weekly report and an Linden World
product name, and `item_code` holds codes, descriptions, billing item numbers,
payer numbers and HALVOR's territory. A CRM that built its products from those
two columns loaded projects and territories as products.

`augment_export.py --product-rules` reads each line by its manufacturer's own
rule, from an operator file (`product_rules_v1`) naming the authorization it
rests on. A rule gives the pattern of the manufacturer's code, the columns it
may be printed in, where its name is and what is never a product -- or says the
manufacturer lists no products, as murbrook, HALVOR, Lumen Weft, Tallis and
Norvena do -- and cites the pages it was read from:

```json
{"brand": "Linden World", "code": "[A-Z][A-Z0-9][0-9]{4,5}", "code_letters": 2,
 "code_from": ["product_sku", "item_code", "description"],
 "code_may_follow_a_name": true, "check_the_printed_row": true,
 "name_from": ["description", "item_code"],
 "evidence": "Material and Material Description on pages 238, 243 and 262 ..."}
```

Each line gets `product__code`, `product__name`, `product__variant`,
`product__brand`, `product__brand_key` and `product__rule`, which says why when
there is no product: the manufacturer lists none, the row is not a line of its
document, the candidate names a party or a label, the page names several
manufacturers (the client's own monthly report), or no code is printed.
`--out-products` writes one row per manufacturer and code, with every spelling
of the code and the name seen beside it. A code's letter positions are read as
letters for its key, so `S01610` and `SO1610` are one product. Load products
from that file, and a line's product from its `product__` columns -- never from
`description` and `item_code`.

**Check each code against its printed row.** With `--extractor-raw`, a rule
marked `check_the_printed_row` finds each line's row by an amount the line
carries and the page prints once, and reads the code printed on that row: one
code per row and one row per code, or nothing. Linden World's code column slips
a row against its amounts on some pages -- page 238 prints 17,010.00 beside
ME5282 where the engines put AP04720 -- and every row checked against five page
images bore out the printed row. On the commission run 111 lines take the
printed row's code over the engines', 414 gain a code the engines never read
and 1,361 are confirmed. The engines' reading stays in its own column, and
`product__printed_row_check` says `agrees`, `differs` or `recovered`.

## Accounts and contacts are dimensions too

`augment_export.py --parties` writes them beside the grains. Load them rather
than building accounts and contacts from the raw columns: a CRM that did made
50 accounts from the commission run's export 44 -- the client five times, a
column heading and the client's street address among them -- and 37 contacts,
most of them a territory code, a report's footer, brands and the client.

`--out-accounts` writes one row per party the grains name as a party, joined on
`<field>__party_key`, the master's key the export writes beside each resolved
name, because a name is not an identity. A field the row says holds the client,
the job or a refused reading makes no account. Each row carries every printed
spelling, the roles the party was printed in, a branch's parent, the public
identity beside its readings and the dates of the documents naming it. The
summary's `party_keys_not_in_the_master` must read zero: a master other than the
one the export resolved against joins nothing, and the first build of this
wrote no accounts at all because it joined on the name.

`--out-contacts` writes one row per person the person fields name. One person's
spellings join on the same letters, a middle initial or the same email, never
on one letter apart, because two people can be. An email or phone is kept only
where an email naming the person is printed beside the name, and a company only
where that email's domain names one of the document's parties or the client:
murbrook's main line, printed beside the client's rep on seven pages, is not
hers. A person printed only in a rep field with no email carries no company,
because who the rep-field people are is the client's to say.
