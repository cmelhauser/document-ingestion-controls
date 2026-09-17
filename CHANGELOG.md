# Changelog

All notable repository releases are recorded here. Dates use ISO 8601.

## Unreleased

- `canonical_export.py --classifications ARTIFACT` types a document from the
  classification consensus. The extraction consensus writes `unknown` wherever
  its lanes could not agree a type, and the export read only that. On the
  commission run all 76 admitted documents were exported as `unknown`, though
  the classification lane had accepted a type for each. With the classification
  consensus the final queue resolves against, they are 48 commission
  statements, 26 commission reports and 2 payment confirmations, and the
  exceptions are unchanged. A type the extraction consensus established is
  kept, and an artifact that is not a classification consensus is refused.
- Adds the client-configured business data platform, restored from a
  2026-09-13 work-in-progress stash and merged onto current `main`. One
  secret-free `business_data_platform_v1` YAML names an exact tenant and
  approved snapshot, and configures:
  - typed analytics, saved reports, and checksummed analytical CSV/XLSX
    export jobs;
  - the image intake and review portal;
  - writable-object mappings, and a file or HTTPS JSON target adapter.

  The entry points are `business_platform_deploy.py`,
  `business_platform_server.py` and `business_record_changes.py`. The
  contract is `references/business-data-platform.md`, changes follow
  `skills/record-maintenance/SKILL.md`, and deployment templates are in
  `deploy/business-platform/`.

  MCP stops at schema, proposals, previews and lifecycle status. Signed
  authorization, application and field reconciliation are operator-only API
  and CLI stages under separate scopes. No target change reaches retrieval
  until a new approved snapshot passes the ordinary controls.

  The merge made three choices:
  - the stash's own intake session listing and review gave way to `main`'s
    later `list_ingestion_sessions` and `get_ingestion_review_summary`, and
    the portal calls those;
  - analytics export jobs validate as tabular jobs beside `main`'s package
    jobs;
  - platform API routes sit beside `main`'s CRM-export routes.
- `final_review_queue.py --dispositions ARTIFACT` retains a finding an operator
  authorized a disposition for. Some findings no control will ever answer: a
  lane that returned no candidate, a provider's own review flag, a handwritten
  region under a comment-only policy. Each held its document out of canonical.
  - The artifact is an `operator_item_dispositions_v1`, and it must be
    operator-authorized.
  - It names each item by its `review_item_id`, the digest of the finding, with
    the rule and evidence that dispose of it.
  - The item moves to `reconciled` as `dispositioned_by_operator`, and is never
    deleted.
  - An artifact naming no item, or an entry without a rule, is refused, and a
    disposition matching no item is counted.

  `client_review_lane.py build` takes the same option.
- `attribution.py --credit-by-line AUTHORIZATION` applies a crediting rule to
  statements whose lines name several jobs. Each line carrying money is credited
  to the key it prints. The document is registered `credited_by_line` -- an
  answer, not a failure -- with each line's key and amount. Nothing is chosen
  among keys and no share is estimated, so a document with a money line
  printing no key stays unresolved. On the commission run the rule credits 215
  of the 468 unresolved documents, worth $28.06M of $65.73M: 140 name between
  two and ten jobs, and 75 more than ten.
- `agent_surface_check.py` no longer reports a stalled process launch as stale
  documentation. A catalogued command's `--help` returns in well under a
  second, but endpoint security can stall a launch for several seconds. On
  2026-09-16 the check failed seven times on the operator's Mac, and every
  failure whose errors were kept was one help timing out -- a help that takes
  under half a second alone. The release check and the quality gate both run
  this check. A timed-out help is now tried again with a longer limit (5, 15,
  then 30 seconds). Only a help that times out on every attempt is reported,
  naming the attempts, so a help that genuinely hangs still fails.
- A client review pack no longer asks what the final queue retains.
  `client_review_lane.py build` builds the same queue by its own path and took
  only `--resolved-by`, so a pack from the final queue's inputs asked every
  finding the queue retains as superseded by a re-read or settled by a vendor
  vote. On the commission run it asked 36,796 items where the queue asks
  35,742: 522 superseded findings and 532 settled ones. It now takes the
  queue's `--supersede ARTIFACT MANIFEST` and `--settled-by-vote VOTE ARTIFACT`,
  read by the queue's own functions, so it refuses what the queue refuses, and
  its summary counts `superseded_items` and `settled_by_vote_items` beside
  `reconciled_items`. Built from the same inputs with the same options, the
  pack asks exactly the queue's 35,742 items. A pack whose every finding is
  retained is refused with that count rather than as a pack built from nothing.
- A cut-off party name goes to the company, not to the branch its longer
  reading names. `entity_resolve.py`'s `completed_by` completes a cut name
  with the longest reading that finishes the cut word. So where a branch's
  spelling (`Cornerwise Design Services-Jacksonville`) nested over the
  company's own (`CORNERWISE DESIGN SERVICES`), the cut went to the branch.
  On the commission run five cut names did:
  - `CORNERWISE DESIGN SERVICE`;
  - `Merrow Off`;
  - two cut readings of Office Quarters;
  - one cut reading of Interior Initiatives.

  `branch_base` now reads a name, a dash and a label as that name. When the
  name is printed on its own and finishes the cut word, it takes the
  completion. Nothing else in the master moves.
- A vote over a subset re-read settles that re-read's findings in the queue.
  `multi_engine_vote.py --pages MANIFEST` votes only on the documents a subset
  read's manifest lists and passes every other document to `--records-out`
  unchanged, so the vote's counts describe the pages its handoffs read.
  `final_review_queue.py --settled-by-vote VOTE ARTIFACT` retains the named
  input's findings on a field the vote settled as `settled_by_vendor_vote`,
  naming the vote and the value it accepted, instead of queuing them: on the
  commission run every one of the 532 fields a vote over the 24 OSALL pages
  settles was still in the queue. Another artifact's finding on the same field
  is still asked, and an artifact that is not an input, or a file that is not a
  vote, is refused.
- A record's fields and its header and line views stay in step.
  `multi_engine_vote.py` and `arithmetic_reconcile.py` wrote accepted values to
  `fields` alone, so attribution, arithmetic, valuation, entity resolution and
  the canonical export -- which read the views -- decided without 4,099 of them
  on the commission run; `apply_mappings.py` wrote 393 approved mappings to the
  header view alone, so the export left them out. Every writer now calls
  `consensus.refresh_views`, which also stops `apply_corroboration.py`,
  `page_review_amend.py` and `column_refile.py` dropping a view entry no field
  names. The mapping lane writes the field too, under the new export status
  `accepted_approved_mapping` (confidence `corroborated`). New
  `records_in_step.py` brings an existing record artifact into step, and
  `--check` fails while any document is out of step.
- Another vendor's reading of a page can bear out what the page reviewer alone
  saw. `page_review.py --second-read HANDOFF --reviewer-vendor VENDOR` regrades
  a correction nothing else backed as `second_vendor` where that reading holds
  the figure in its column more often than the export's other lines, and a row
  the extractor prints as often as the export as
  `second_vendor_prints_it_fewer_times` only where the reading holds every
  other figure in the column as often and this one fewer times -- a reader that
  misses a third of a page misses rows the page prints. A second read by the
  reviewers' own vendor is refused. `page_review_amend.py` selects
  `second_vendor`, and refuses a correction whose cell has changed since the
  review (`the_field_changed_since_the_review`).
  `field_extract_export.py --rows-not-printed PROPOSALS
  --rows-not-printed-authorization NAME` labels each line the review says the
  page does not print in `page_review_row_not_printed`, and never drops it.
- `column_refile.py` moves a reading the engines left unsettled. A field with
  no value but a candidate one engine read is still a reading of that column,
  so it moves with it and, on the target, refuses the move. The check had
  looked at the value alone, and left 22 of Lumen Weft's Specifier cells under
  the dealer, including every one on pages 661 and 663. `moved[]` now names
  the candidates each unsettled reading carried.
- Personal data read from the commission run's pages is out of the tree. Test
  fixtures and docstrings that carried real contacts' names, emails and direct
  phone numbers, and the client's street address, carry synthetic ones
  (555-01xx numbers), with initials and letter case kept so every name-joining
  test still means what it did. The 36 provider-response cache files committed
  in #70 and #71 (`.llm-cache-anthropic/`, `.llm-cache-sonnet/`) are untracked
  and retained outside the repository, and `.gitignore` covers every
  `.llm-cache*/` and `.llm-rate-limit*/` directory. Git history still holds all
  of it; `SECURITY.md` says so.
- The controls an operator's decisions need, each by name.
  `arithmetic_check.py --not-provable-as-not-applicable AUTHORIZATION` counts an
  unprovable document as not applicable and keeps `prior_arithmetic_status`;
  the final queue and the canonical export now read one list of arithmetic
  statuses that do not block (`review_status.ARITHMETIC_CLEAR`), where the
  queue had filed an item even for `not_applicable`.
  `final_review_queue.py --supersede ARTIFACT MANIFEST` lets a subset re-read
  govern its own pages: the earlier readings' findings there are retained as
  superseded rather than queued. An attribution register entry carrying a
  Phase 0 reason code is no longer a queue item, and `attribution.py` gains
  `payment_record` and `blank_page`. `field_extract_export.py
  --two-digit-year-months AUTHORIZATION` types Excel's `mmm-yy`
  (`December-24`) as a month of this century. `augment_export.py` copies a
  project a party field holds into `project_name__inferred`. New
  `column_refile.py` re-files readings filed under the wrong field, whole and
  under a named authorization.
- `crm_input_validate.py` holds a typed month (`<column>__month`) to the same
  year test as a typed date: a month whose year has fewer than four digits is
  `date_year_is_not_a_year`. The check had matched the ISO companion by its
  suffix, so the month companion added beside it escaped it.
- `augment_export.py` carries the master's key with an inferred party, in
  `<column>__inferred_party_key`, so a CRM joining on keys no longer drops the
  1,576 customers, dealers and specifiers export 50 inferred. A new method,
  `same_key_on_other_lines`, fills a line's customer or dealer from the same
  maker's lines printing its purchase order, sales order, job, project or
  transaction number, where every such line names one keyed party. It is
  scored first on held-out lines -- 97.4% of customers and 96.0% of dealers
  came back right on export 50, against 28.6% and 30.3% by chance -- and a
  field under 95% fills nothing. `field_extract_export.py` reads a date printed
  with a weekday or a time of day and a year-first date missing a hyphen, and
  types a month named without a day as `<column>__month` (`YYYY-MM`).
- Every script the test suite starts as its own process reads what CI reads. A
  child resolves the repository-root `.env` in its own process, where the
  suite's isolation could not reach, so the control-layer workflow, the
  acceptance wrappers and every `--help` run read the operator's file. The new
  process-only `BUSINESS_DOCUMENT_IGNORE_PROJECT_ENV` switches the root `.env`
  off for a process and the children it starts, and the suite exports it for
  its session. `release_check.py` now also counts `os.environ["NAME"]` as a read
  of `NAME`. Only a narrower copy of that rule in the test suite matched the
  subscript, and it looked at `scripts/*.py` alone; that copy is removed.
- The operations skill's lane table has a row for every lane
  `run_lane_coverage.py` catalogues again, and the count three surfaces state is
  the catalogue's 74, not 69: `accuracy_sample.py` and `run_generation.py` had
  no row. `agent_surface_check.py` now refuses a catalogued lane with no row in
  that table's Command column, and a stated lane count that is not the
  catalogue's.
- `field_extract_export.py --accepted-csv` no longer calls its rows the subset
  two independent vendors agreed. It writes every row under an accepted status
  -- vendor agreement, independent corroboration, same-document settlement,
  the extractor tiebreak, a page-review amendment, document arithmetic, or a
  derived value -- and on the commission run's export 43 vendor agreement is
  35,545 of the 88,320 accepted rows, independent corroboration 40,124. The
  help now lists the statuses from `ACCEPTED_STATUSES`, the registry that
  selects the rows, so a lane registered later cannot leave it describing a
  subset it no longer is; it is still not a canonical load. The module
  description, the field-extract contract and the operations skill's lane
  table gain the page-review amendment their status lists lacked, and the
  command-line reference names the current record artifact, not the consensus
  artifact, as the input.
- `run_lane_coverage.py` credits brand recovery, specifier recovery and the
  accuracy sample. All three ran on the commission run and were reported as
  never run: brand recovery was catalogued as `brand_recovery`, a prefix of the
  artifact type it stamps, and both it and the accuracy sample stamp their type
  in their summary, where the report read no identity; specifier recovery was
  catalogued as the key it writes on each document, where the report does not
  read. The report now reads
  an identity a producer stamps in its summary as well as at its root, brand
  recovery and the accuracy sample are recognised by their artifact types
  (`brand_recovery_v1`, `accuracy_sample_v1`), and specifier recovery by a
  summary key only it writes. The test meant to catch a marker no lane writes
  searched the catalogue too, which quotes every marker, so it could not fail;
  it now leaves the catalogue out, and each recovery lane's own command writes
  an artifact the report must credit.
- `augment_export.py --product-rules` puts each line's product beside it, read
  by its manufacturer's own rule (`product__code`, `product__name`,
  `product__variant`, `product__brand`, `product__brand_key`, `product__rule`),
  and `--out-products` writes one product per manufacturer and code. A CRM that
  built products from `description` and `item_code` showed murbrook projects,
  HALVOR project clients and territories, Linden World payer numbers and
  Marlow/Bramwell's weekly-report projects as products. A rule, in an operator
  file resting on an authorization, names the columns a manufacturer prints its
  code and name in, or says it lists no products, and cites its pages; a party,
  a label, a row that is not a line and a page naming several manufacturers
  carry no product and say why. With `--extractor-raw`, a rule that checks the
  printed row reads each line's code off the row its own amounts are printed
  on: Linden World's code column slips a row against its amounts on some pages,
  and every row checked against five page images bore out the printed row. On
  the commission run 111 lines take the printed row's code over the engines',
  414 gain a code the engines never read, and 1,361 are confirmed. `S01610`
  and `SO1610` are one product.
- `brand_recover.py` prefers the name at the top of a letterhead naming two,
  matches a long name one letter off, counts a dealer in the brand column as
  no manufacturer, and settles a page printing no letterhead from its layout:
  its opening, the words only one maker's pages print, or the maker its five
  most alike settled pages all name. A page whose top names a company by its
  legal form, neither the client nor a known maker, stays a question naming it
  (Crestline Design, Vireo Seating). A header left empty by disagreement is
  read from the field map the export prints, so Marlow/Bramwell's order reports
  no longer carry the client as their brand. On the commission run it recovers
  173 brands and keeps 46 pages as exceptions.
- `run_io.retained_responses` indexes each page to the extractor response that
  read it. 222 of 716 pages of the commission run had text only in the retry,
  and brand recovery, specifier recovery and the page review's extractor
  evidence all read the first, empty file; specifier recovery now finds a
  specifier on 204 documents rather than 174. `retained_response_tokens` reads
  the positioned words a row join needs.
- The test suite withholds the operator's `.env` from collection as well as
  from each test, and withholds every setting `.env.example` documents rather
  than a hand-kept list of prefixes. `retrieval_sidecar.py` loaded `.env` when
  imported, collection imports it, and the list lacked `SLOT_EQUIVALENCE_` and
  `ANTHROPIC_`; from a checkout holding a `.env`, the operator's
  `SLOT_EQUIVALENCE_ENABLED` reached every later test and
  `test_slot_cli_runs_end_to_end` failed for want of a credential, in the full
  suite only. The sidecar now loads `.env` in `main`, before it parses its
  defaults, and a test refuses any script that reads configuration while it is
  being imported.
- `entity_resolve.py --absorb-ocr-variants` runs after `--party-decisions` and
  never joins a pair a decision keeps apart or a branch to its parent, absorbs a
  fragment only when no other party's name also carries it, and a one-letter
  variant only in names of six characters or more. At 10:1 on the commission
  run it had absorbed 30 names, among them a parent into its own branch, `The
  Design` into Cornerwise Design Services and `XTK` into `XTB, LLC`; it now
  absorbs ten, each a misreading.
- `field_extract_export.py` types a decimal comma where the document settles it
  -- decimal commas and no decimal point -- so murbrook's 40 Canadian amounts
  load (`2 199,03` is 2199.03), and writes `<column>__percent` beside a rate
  whose unit a `%` or the line's own arithmetic proves. Each pivoted party
  column carries the master's key beside the resolved name
  (`<column>__party_key`).
- `augment_export.py --parties` writes the accounts and contacts a CRM loads
  beside the grains (`--out-accounts`, `--out-contacts`). Accounts join on the
  party key and skip the client's fields, a job and a refused reading; one
  person's spellings become one contact on the same letters, a middle initial or
  the same email, with a company only where an email naming the person proves
  it. Keys the master lacks are counted (`party_keys_not_in_the_master`), and a
  public-source fact can carry its own `retrieved` date.
- `entity_resolve.py` refuses a party reading that is a column heading, a role
  or a placeholder (`COMPANY`, `INSTALL REP`, `None`, `N/A`, `TBD`), a total
  row's label (`Total Commissions to date:`), a signed adjustment (`-70% Specify
  Territory`) or a street address, as it already refused one with no letter,
  keeps each with its reason, and counts refusals by reason.
  `field_extract_export.py` says on each row which company fields hold a refused
  reading or a name a party decision removed (`party_fields_not_a_party`)
  and which person fields hold something other than one person's name
  (`person_fields_not_one_person`), and types each email column
  (`<column>__email`, empty when the reading is not an address). A CRM built
  from the commission run's export showed the client, a heading, a code and an
  address as accounts, and territory codes, a report's footer, a role and
  brands as contacts.
- `field_extract_export.py` writes its JSON after its CSVs, so the summary
  carries the counts each grain adds (`line_records`, `document_records`,
  `item_records`, the duplicate and control counts). It was written first, and
  every delivered summary lacked them.
- The README, the technical documentation, the lane checklist, the pipeline
  guide and the pipeline-selection skill now describe the page-review
  amendments and their line-arithmetic guard, `--party-decisions`, the Google
  Places dealer lane and the checks that fill and validate the CRM input. The
  lane checklist is renumbered to `run_lane_coverage.py`'s catalogue, which it
  had fallen behind, and gains the eight lanes it lacked between entities and
  handwriting.
- `run_lane_coverage.py` parses each retained file once per report, however
  many lanes look for a marker in it. The commission run holds 44,963 JSON
  files, and parsing every one again for each lane kept the closure report
  running for most of an hour without finishing.
- `field_extract_export.py` types a count like money: `quantity` gets a
  `quantity__amount` companion, as the validator expects of every numeric
  column. Thousands separators are removed and nothing else is guessed, and a
  count the page masked carries `-99999`. A year of fewer than four digits --
  a cell that cut the century off -- is refused rather than loaded into the
  second century, and a run of asterisks counts as an unrendered cell. On the
  commission run's export, validation findings fall from 90 to 85.
- `page_review_amend.py` refuses a correction that would break a line's own
  arithmetic: a line whose base x rate = commission held is never amended into
  one that does not, and a move that refusal leaves half-proved waits too. The
  independent evidence proves a figure is printed on the page, not on which
  line. On the commission run 14 reconciling lines had been amended into
  failing ones -- on p0430 a row the engines read twice put every later
  correction one line off -- and the page-level score still counted those
  pages as matching, because the same amounts were on them. Re-applied with
  the guard, 434 amendments land, 19 are refused, and no line that reconciled
  fails.
- Adds `party_locations.py`, which asks Google Places where each dealer and
  brand in the party master trades. It sends a business name and nothing
  else -- never one that reads as a place -- once per party within the
  per-run cap, keeps every candidate, and accepts one only when it accounts
  for every word of the party's name, as a probable public fact that
  `augment_export.py --external` places beside the reading. `--from-lookups`
  decides again from the retained candidates without a request, and
  `--countries` limits where an accepted business may trade. It is disabled
  unless enabled, and authenticates with the restricted key or, with the new
  `GOOGLE_PLACES_AUTH=adc`, Application Default Credentials. On the commission
  run 460 requests gave 125 accepted locations and 5,563 identity cells beside
  the readings.
- Adds `page_review_amend.py`, which turns the page review's proposals into
  append-only amendments under a named operator authorization, for corrections
  an independent source backs -- the independent extractor's text of the page,
  a retained engine reading, or both. A correction the reviewer alone supports
  cannot be selected. Each amended field keeps the value it replaced, the lane
  that had accepted it and every candidate reading in `superseded_consensus`,
  and the export counts it as `accepted_page_review_amendment`, read as
  `corroborated`.

  A shifted row is fixed by two corrections, and applying one alone counts the
  money twice: on a first pass one page carried $1,404.13 on two lines.
  `page_review.py` now reads a reviewer's figure written with a note
  (`799.67 (1,404.13 is job 13046's)`) and names in `moves_from_lines` any other
  countable line still holding a correction's figure; the applier moves a
  figure only when that line is corrected in the same run, and held back 27
  such moves.

  On the commission run 455 amendments took the pages that reconcile with their
  image from 630 to 653 of 716, with none newly disagreeing. Money the export
  lost fell from $482,626.90 to $107,784.53, and money it invented from
  $613,612.92 to $359,541.42.

- `entity_resolve.py --party-decisions` carries a person's decisions about
  named pairs into the party master, under the authorization the file names:
  `same_party`, `branch_of` (both accounts kept, the branch linked to its
  parent), `different_parties` and `not_a_party`. A decided pair leaves pending
  adjudication with its score kept, and a decision the resolver cannot apply
  is reported with its reason rather than guessed at. On the commission run
  147 decisions took the master from 880 parties to 774 and pending
  adjudication from 45 pairs to 3.
- `field_extract_export.py` reads a project number as project evidence, as it
  reads a job number, when it flags a company field that repeats the line's
  description (`party_field_holds_the_project`). Some statements print a
  `Project #` and no job number at all; on the commission run the flag now
  covers 490 lines, from 476.
- `attribution.py` now reads a key a document prints on every line when its
  header prints none. Lines are still ignored when they name several keys --
  that is an allocation question -- but a document whose lines all name one
  sales-order or project number has no such question, and 56 documents on the
  commission run were registered unresolved for want of it. The key is held to
  the same format check as a header key and recorded under its own method,
  `explicit_printed_on_lines`, so an attribution set still shows how much of it
  came off the lines. Attribution rises from 151 to 207 documents, and from
  2.4% to 7.9% of the money.

- Adds `augment_export.py`, which fills what the export leaves empty from what
  the run already knows -- and says so on the row. Every filled value goes in
  `<column>__inferred` beside the cell it fills, with the method and its
  evidence, never over what the page says. On the commission run it fills
  17,311 cells: 6,690 document types two vendors agree on, 4,689 line brands
  from their own document's letterhead, 3,278 cells the same job carries
  unanimously on at least two other statements, 1,694 location numbers, states
  and branches a party's name prints, 842 public-source identities with their
  URLs, 100 E.164 phones and 18 ISO country codes. It leaves 2,890 cells empty
  because the statements that carry them disagree.

  Checked against the independent extractor's text of each page, 96.6% of the
  values it carried were printed there. The check is what removed dates:
  carried dates matched three times in four, because one field here is filled
  from six printed columns. It also removed codes and cut words read as
  branches (`COOI`, `Re`), and `LA`, which names a city as often as a state.

  `crm_input_validate.py` now holds an inferred value to the width of the
  column it fills and never treats `__inferred_by` or `__inferred_evidence` as a
  load target. Checked as values, the method's own description of a country --
  `the ISO short name “United States of America (the)”` -- was reported as a
  country name where a code belongs, adding 36 findings that were not findings.

- `page_review.py --proposals` turns what the page reviews found into proposals
  an operator can authorize, each graded by independent evidence. A reviewer's
  reading is one model's, so beside every proposed correction stands whether
  the independent extractor's text of the page prints the figure, and whether
  a retained engine read the cell that way while consensus chose otherwise.
  Corrections, rows the page never prints, and values the export lost are
  proposed apart, because they ask different things of whoever authorizes
  them. Nothing is applied: `decision_mode` is always `proposal_only`.

  Over the 716-page review it proposes 428 money-cell corrections on 105
  pages, 361 of them with independent evidence; 137 rows the page never prints
  on 52 pages; and it separates the values the export lost into those a
  correction already covers, those the extractor's text holds, and 33 on 18
  pages held nowhere retained. A first draft counted a page the extractor never
  read as a page that prints nothing, and so backed 78 exclusions with evidence
  that existed for 11. A page with no extractor response is now named as such.

- A rule for a page read twice was measured and not shipped. Keyed on a
  trailing run of amounts repeating an earlier run, it flagged 18 rows of
  which reviewers named 4, and hid three figures one page really prints; those
  rows are proposed from the reviews instead.

- `restates_a_total` now recognises a total row that does not name its total.
  The phrase list -- `P.O. Total`, `Subtotal`, `Total Commissions to date` --
  missed a bare `Total :`, `TOTAL`, `Northgate Co LLC Total`, `Purchase Order
  Totals` and `Total - Acoustics`, and never looked in the customer or dealer
  column, where a total row's label lands when its first cell is blank. 90 line
  rows carrying $1,226,621.88 were counted as lines, among them a statement's
  grand total of $148,247.29 on a page whose own lines come to $25,415.42. A
  label is now known by where the word sits -- alone, opening the cell on a
  separator, or closing it -- and a cell that merely begins with it (`Total
  Station Cabinet`, `Total Wine & More`) is still a name.

  A label known only by that position, or found in a party column, counts only
  on a row that names no job, order or item of its own. The first version
  flagged four real lines -- `Total - Other` written into two sales orders as
  their category, and a `Total Commissions to date:` pasted from the next row
  onto two real jobs -- and so hid money the page prints. 294 of 9,144 line
  rows are labelled; no row labelled before loses its label, and no other cell
  of the export changes.

  `page_review.py` found it and measured the fix over all 716 pages: eleven
  pages whose export disagreed with the image now reconcile, none newly
  disagrees, and money the export counts that no page prints falls from
  $2,743,372.15 to $613,612.92.

- `page_review.py` no longer calls a correct header an invention. For a page
  with no line money it swept in every money-like header column -- the
  commissionable base, the amount due, a report's overall total -- and compared
  them with the page's data-row money, which by design names none of them. It
  now compares the page's own money column, and lets a header restate the
  page's printed total; a line row carrying that total is still reported,
  because that is the double count. It also reads `57.06 USD` as money:
  refusing the currency code made a page whose every figure the export held
  look as if it held none. Over all 716 reviewed pages, 20 it called wrong were
  right and it newly flags none; on the same export, the money it called
  invented falls from $6,115,782.22 to $2,743,372.15. Each delivery CSV is now
  read once, not once per page.

- Adds `page_review.py`, which checks the export against the source page
  instead of against another model, and turns page reading into a measurement
  rather than a reader's impression.

  A reviewer is handed one page's image and the rows the export claims for it,
  and returns the money **the page** prints. The lane compares that
  mechanically, so the verdict never depends on the reviewer's summary. It
  reports *lost* money -- printed on the page and held nowhere in the export --
  and *invented* money -- counted by the export and printed nowhere on the page
  -- measuring each against a different set, because a cheque prints two
  identical stubs and the export is right to count one.

  It exists because reading by hand found real defects and produced no rate.
  The first thing it found at scale was the defect no control could reach: a
  commission template whose COMMISSION column is split into FURN / TABLE /
  LEATH / FABRIC / MASQ / FIXED / TOTAL was read from **FABRIC**. One verified
  page prints 2,475.34 and the export carries 258.93; rows whose money sits in
  TABLE are absent entirely. Every control passed it, because those pages state
  no commissionable base and no rate, so `commission_arithmetic` had nothing to
  test -- 96 documents share that untestable shape.

  Three false alarms shaped the comparison and each has a test: a remittance
  states its money in the document row, not in lines; a flagged duplicate is
  not an invention; a value the export merely excluded is not lost. A check
  that is only strict reports correct pages as defects, and then nobody reads
  it.

- Adds `differs_from_the_same_line_elsewhere` and
  `missing_where_the_same_line_carries_it`, which read the corpus's own
  repetition as evidence about the fields no arithmetic can test. These
  statements are periodic and a job stays on them until it is paid, printing
  the same figures each month, so a project name or a date that reads one way
  on eighteen statements and another way on one is worth a reviewer's eye.
  2,091 rows disagree with the same line elsewhere and 1,011 are empty where
  the others carry a value.

  Found by reading the stratified sample. Three defects on three pages turned
  out to be one root cause: `transaction_date` taken from the ship column on a
  few pages where the rest take the invoice column (139 of 198 recurring lines
  disagreed); a cell the print cuts at `Warehous` arriving silently completed
  to `Warehouse)` on some pages with neither `text_truncated_in_source` nor
  `completion_proposed` set; and a `PROJECT CANCELLED` stamp that survived in
  `description` on twelve of twenty statements, landed in `charge_description`
  on one, and vanished on seven -- where a cancelled project carrying $28,950.92
  commissionable reads as live. All three are now named.

  Nothing is corrected and no row is excluded. A majority reading is the corpus
  repeating itself, which is weaker evidence than vendor agreement and stronger
  than the nothing these fields had. A line needs three documents before a
  majority is claimed, and a zero commission is refused as a key -- hundreds of
  distinct lines state 0.00, and grouping them reported every one as
  disagreeing. The commissionable amount stands in there, which is what brings
  the cancelled projects into scope at all.

- Teaches `amount_without_line_identity` that a job number names what money is
  for, and adds `repeats_a_job_and_amount_above` so widening it cannot start
  counting a line twice. A commission statement heads its rows `Job` and
  `Project` and often prints no product column at all; identity was read only
  from `description`, `item_code`, `style` and `product_name`, so the flag
  called **1,238 rows anonymous when 556 of them, carrying $3.7M, name their
  job on the page**. On 96 documents it fired on *every* money row, so a reader
  told to drop flagged rows lost the whole page -- $3.9M of correctly-read
  commission, 23.8% of the run.

  The second flag is what makes the first safe. `duplicate_of_line` compares
  every column, so it catches only a row emitted twice unchanged; a page read in
  two passes emits the same job and amount again with different columns filled
  -- the customer on one pass, the dealer on the other -- and those rows are
  equal nowhere. That double count was hidden behind the identity flag, and
  widening identity alone would have released it into the total.

  Delivery guidance was wrong in the same way and is corrected: the line flags
  answer two different questions, and summing only unflagged rows returned
  **$4,287,567 of the run's $16,674,109**. Excluding just the rows that are not
  a line of this document reproduces every hand-checked page's printed total to
  the cent -- p0010 at 39,300.42 over eight rows, p0515 at 38,898.17 over twelve
  -- and totals $12,868,180 corpus-wide, leaving 72 documents with $154,329
  uncountable rather than 151 documents with $3.9M.

- Adds `accuracy_sample.py`, which draws the pages to read against their own
  image and says what each one stands for. `sampling.py` draws a monetary-unit
  sample for projection and samples only the auto-accepted population, which is
  right for what it measures and empty here: **716 of 716 documents excluded**,
  so the accuracy of the extraction went unmeasured exactly where it mattered.
  Measuring accuracy is a question about the documents that were *not* accepted.

  It exists because the alternative is what happened on this run: 26 pages read
  by hunting -- the biggest page, the oddest form, the family nobody had opened.
  That finds a new class of defect efficiently and cannot say how common one is,
  and it yields no accuracy figure at all. Selection inside a stratum is random
  and reproducible from the document ids, so the pages are not the ones that
  looked interesting and there is no seed to lose or redraw.

  Strata are collapsed across spellings of one layout. Split across them the
  draw covered 26 "layouts" that are eight -- `murbrook` and `murb rook`, four
  spellings of Marlow/Bramwell, five of Lumen Weft -- and 128 pages measured
  less than 79 do. Each stratum reports `documents_per_page_read`: what one read
  page speaks for, so a finding weights back up and a rate is never quoted
  without it.

- Adds `brand_recover.py`, which reads the manufacturer out of the page
  letterhead in retained independent-extractor layout. 354 of 683 documents
  carried no manufacturer -- $5.42M of commission with no brand against it --
  and the cause is instructive: `brand_name` held the client, because a billing
  report prints the client as its agent of record. Refusing the client, which is
  right, left those documents with nothing. **154 recovered, no provider call.**

  The vocabulary is the corpus's own: a name the pages call a brand more often
  than they call it anything else. Three attempts were needed to get that right.
  A list from `brand_name` alone inherited every mis-filing -- the client,
  dealers, specifiers. Excluding any name ever seen in another role deleted
  HALVOR, Marlow/Bramwell and Lumen Weft over a handful of bad pages. Weighing
  the two counts the corpus supplies needs no threshold and survives both.

  Spellings that squash to one key are one manufacturer: before `murb rook` and
  `murbrook` were grouped, 100 pages reported a letterhead naming several brands
  and recovered nothing. Checked against nine pages read by eye: six recovered
  correctly, three recovered nothing because their letterhead is handwritten and
  a printed text layer does not carry pen. **None recovered a wrong
  manufacturer.** A page naming several, or none, is retained as an exception.

- Flags a commission equal to the amount it is a commission on. A rate of 100%
  is not a thing; it means one number was read into two columns, and it happens
  where the page prints no commission at all. The HALVOR open order reports have
  a single money column headed `Adjusted Net Sale` and a `Total Net Sale` at the
  foot, and **88 rows across 16 documents carried $3,250,576 of "commission"
  that is those net sales copied across**. 52 of those rows carried no other
  warning.

  Invisible to every other control by construction: the arithmetic check needs a
  base and a rate and those pages state neither, so every row is `not_provable`;
  the value is well-formed, in range, and genuinely printed on the page. Only
  the equality between two columns gives it away.

- Reports the commission money no arithmetic check can reach, beside the rate
  that excludes it. `commission_arithmetic` needs a base and a rate on the row;
  where a page states neither, every line is `not_provable` and drops silently
  out of the denominator. On this corpus 96.7% of provable rows reconcile while
  **35.8% of the money -- $5,928,613 over 2,111 rows on 181 documents -- is on
  documents where nothing is provable at all**. One of them prints no commission
  column and carries $1.67M. The repository's own rule is that a control which
  processed nothing is failed rather than clear; this applies that rule to an
  accuracy figure, so the denominator now travels with the rate.

- Flags a document whose own table was read twice. An engine that reads a page
  twice returns the table twice, and the copy arrives *less* complete than the
  original -- on one page the second pass dropped every sales-order number and
  every customer. `mark_duplicate_lines` compares whole rows, so the emptier
  copy never matched and the money was counted twice: page 297 prints eight
  lines totalling $4,966.50 and the export carried sixteen totalling $9,933.00.
  4 documents, 37 rows, $10,027 of doubled commission.

  Two guards keep it honest. Only a copy that begins after the original ends is
  a repeated table. And the original block has to hold more than one kind of
  row: one page in this corpus genuinely prints the same line eight times, which
  matches itself at half its own length, and calling that a second table would
  delete money the page really states. Nothing is dropped either way -- the copy
  is a real reading of a page that was read twice, and which copy to keep is a
  decision for whoever loads it.

- Gives a project somewhere to live, and says which rows are waiting for it. A
  commission statement heads a column `Project` and prints a building or a suite
  there -- `One Bayfront Suite 540`, `Meridiana Corporation Center` -- and with
  no field for it the value went to the nearest company name. 476 lines across
  68 documents carried a project as their dealer, customer or brand, where each
  one would have loaded as a company the client sells through. `project_name` is
  now a schema field, and `party_field_holds_the_project` marks the rows: the
  company field repeats the row's own description and the row carries a job
  number, which is what a project has and a trading account does not.

  Flagged rather than refused. 59 of the 69 names also appear as a dealer on
  rows where they are not the description, so refusing them outright would
  delete real dealers on ambiguous evidence.

- Flags a rate cell that is really the commission. One page shifted its money
  left by a column: the commission landed in `stated_commission_rate` and the
  rate fell off the row entirely. Eleven lines read as rates of 182.93 and
  961.65 percent while the commission column sat empty, so $5,060 was invisible
  to every total that sums commissions. Proved against the rates the document
  itself states on rows whose arithmetic reconciles, not against a rate table
  kept in the code -- that would be a guess about this corpus that goes stale
  the moment another arrives.

- Reports a date whose year cannot be a year. `05/24/0122` normalizes into
  `0122-05-24`: well-formed ISO, casts without complaint, and lands in the
  second century. Four cells, and the only date fault a format check cannot see.
  The raw reading travels with the finding, because `03/17/202` needs re-reading
  rather than arithmetic -- nothing can settle from the value which year `202`
  was meant to be.

- Names an account by its complete reading rather than its stump. `is_truncated`
  sees only an ellipsis, and this corpus mostly cuts a name with nothing marking
  it -- `Office Surr`, `The Furnis`, `QRC Busin` are printed looking exactly like
  whole names -- so frequency made the truncation canonical while the full
  spelling sat in the same party's own variants. 13 accounts renamed, including
  `Office Surr` to `Office Surroundings and Services` and `HOLDEN'S BUSINESS
  ENV.` to `Holdens Business Environments`.

  The containment test `completed_by` already uses for clustering now decides
  the canonical name too, so the two cannot disagree, with two corrections it
  needed to be right here. It compares the normalized form, because a page
  prints the same party as `The Furnis` and `THE FURNISHERS UNLIMITED`, and with
  a full stop the longer spelling lacks. And candidates count as different only
  when they normalize differently: `OFFICE SURROUNDINGS & SERVICES` and `Office
  Environments and Services` are one answer printed two ways, and reading them
  as a disagreement is what left that account named `Office Surr`.

  A completion restores words; it does not append an identifier. `KOK Interi`
  and `KOK Interic PO4705` differ by a purchase-order number, so that one is
  refused and the account keeps the stump -- a name plus a code is worse than a
  short name.
- Flags a money column read one row low, the quietest way money goes wrong here.
  The number in the cell is real -- it is printed on the page -- so no format
  check, no range check and no arithmetic check on that row can see it. Only the
  neighbours can: a cell equal to what a *different* money column held on the
  row above, while disagreeing with that column on its own row, on consecutive
  rows, is a column the extractor tracked one line low. 100 rows across 17
  documents, $849,161 of it sitting in `commission_amount`. On one of those
  pages the source prints a single money column, `Adjusted Net Sale`, and no
  commission at all, yet fourteen rows carried a commission -- each the previous
  row's net sale, with the page's own subtotal on the row below them. Two
  consecutive rows is the shortest run treated as evidence; one match on a page
  of round numbers is chance. Zero and the unreadable sentinel are excluded
  because both repeat down a column by design. Nothing is moved: the run says
  the column drifted, not where it started, so the row keeps its reading and
  gains the sentence an operator needs.

- Keeps the client out of its own CRM. The party whose records these are is
  printed on nearly every page -- as a statement's addressee, as the agent of
  record in a billing report, in a letterhead -- and none of that makes it a
  counterparty. Undeclared, it took 1,395 mentions across nine role fields on
  the commission run and became the largest account in the master, appearing as
  its own customer, its own dealer and its own brand. `entity_resolve.py` gains
  `--client-name`, matching the legal-suffix variants, the mid-word truncation a
  narrow column prints (`NORTHGATE C` for `NORTHGATE CO LLC`), and the name with a
  totals label or an annotator's initials appended. Refusals are retained under
  `refused_as_the_client`, and a zero with no client declared is reported as a
  check that never ran rather than as a clean result. The export flags the rows
  whose raw company columns still read the client, taking the name from the
  party master's own summary so the two lanes cannot disagree about who it is.

- Stops a location key splitting an account in two. A page prints the account
  beside the number that says which of its locations a row belongs to --
  `7144 KD Frost`, `7373 Empall`, `150-NGC` -- and with the number attached the
  same account loaded twice: `KD Frost` with 442 mentions and `7144 KD Frost`
  with 3, so neither row held its real volume. The key is lifted off before the
  name is compared and carried on the party as `location_identifiers`, which is
  what picks the address for a row. 36 account rows collapse into 18. A street
  number is not a location key: `801 Bayshore Avenue` and `2200 Bayshore Avenue`
  are two Miami buildings, and `4100 NE 20TH AVE ...` is an address with its city
  attached, so both keep their digits.

- Checks the exported grain against the target's **types**, not just its widths.
  A value can be the right length and the wrong member: `review_queued` is a
  status this pipeline writes and `review_status_t` does not admit, so 31 rows
  would have failed the load on the type. A foreign key onto a vocabulary
  nothing seeds is reported the same way -- `document_type` reads `unknown` on
  664 of 683 documents -- once per distinct value, with the rows waiting on it.
  The enum members, the columns declaring one, and which foreign keys are worth
  reporting are all read out of the DDL, because every defect found in this
  round began as a list kept by hand beside the schema it was mirroring. That
  derivation is also what keeps the report readable: a table the pipeline fills
  carries a `review_status` and a seeded vocabulary does not, so `job_number`'s
  699 legitimate business keys stay out and `document_type`'s six stay in.

- Corrects a label this repository got wrong. A bare number in `item_code` was
  called `a bare count`, which sent the reader looking for a quantity that is
  not on the page. The source form prints a column headed `Item` holding SAP
  line item numbers beside a separate `Order Position`, and prints no quantity
  column at all; the product code is the `Material` column. It is now
  `a line item number`.

  The `Material` value is on the page and absent from those rows, and it is left
  absent. Both retained readings of that column disagree -- the primary drifts a
  row partway down the page and reads `AP04435` as `APO4435` -- so there is no
  reading to promote, and a wrong product code in a CRM is worse than none.

- Makes the CRM grain loadable and honest about what each row is. Every money
  and date column gains a typed companion; a value the page never rendered
  (`####`, scientific notation, a cut-off cell) carries `-99999` there so a
  load fails loudly instead of accepting a plausible zero, and the flag names
  the column. Rows that a total must exclude say so: a duplicate line, a line
  repeating one in another document, an amount naming no item, and the
  document's own `P.O. Total:` row. Each line is labelled against its own
  arithmetic and carried either way -- a failure is usually the document
  contradicting itself, and correcting a printed number would invent evidence.
  Text the source cut off mid-word is flagged, with the single completion the
  corpus offers proposed beside it and never written over the reading.

- Fixes three classes of correct reading filed under the wrong field, none of
  which vendor agreement can detect because both engines read the page
  correctly and assumed the same thing about the column. A commission split
  across category columns is read as the total rather than a
  commissionable/commission pair; a "Commission Split" column is no longer read
  as a percentage rate; and a printed SPECIFIER column now has a field,
  `specifier_name`, instead of landing in `brand_name`. `FIELD_DEFINITIONS`
  gains the commission fields the corpus actually populates -- 35 of 48 line
  fields had no definition at all, including the rate carrying 5,088 rows.

- Adds `specifier_recover.py`, which reads a printed column the schema had no
  field for out of the independent extractor's retained page layout and joins
  it to lines by a printed key they already carry, with no provider call. A
  recovered value is single-extractor evidence and is written unaccepted, never
  as vendor agreement. The operating skill now leads its provider-call section
  with the general rule: reconcile from retained artifacts before re-reading
  anything.

- Resolves the parties the schema actually carries. The entity lane read only
  the header and only nine of twenty-one party fields, missing `customer_name`
  and `dealer_name` -- where this corpus keeps its counterparty -- so it saw one
  mention per document instead of thousands. It now reads lines as well as
  headers, compares distinct readings rather than repetitions (so the
  highest-cardinality parties stop exceeding the pairwise cap), and unites a
  name the page cut off with the one name that completes it.

- Adds `exception_resolve.py` and a shared `review_status`, so an operator
  answer can reach a review-clear status at all, and the four copies of "what
  clear means" become one. Reads the commission rate a document states when it
  is printed with a percent sign, which the parser had been discarding.
  Validates every date field the schema defines rather than six of eighteen,
  separating a value the source never rendered from an identifier in a date
  column, from a partial date, from a slash date whose order the document does
  not settle.

- Refreshed client/technical guides, README, agent/provider entry points and
  operating skills for optional visual intake and source-only export. Added a
  workbook intake guide; clarified tool counts/scopes, receipt verification,
  source versus CRM packages, deployment acceptance, and unimplemented writes
  and expanded analytics. Fixed the sample workbook's overlapping CRM control
  and canonical-table ranges and added regression checks for guide completeness.
  Historical release/audit evidence remains unchanged.

- Adds a read-only visual-journal export/verification utility. New private
  source packages preserve every upload attempt, original byte, proposal and
  rejection, with receipt-pinned hashes and existing-contract review exceptions.
  Complete usable sessions receive an explicitly transformed image PDF with
  collision-safe page identity for normal profiling/intake; incomplete or failed
  preparation refuses progression. No proposal is promoted into consensus,
  canonical facts or CRM writes.

- Adds optional schema-guided visual intake to local/remote MCP and the remote
  OAuth JSON API: seven shared operations retain original PNG/JPEG pages,
  expose the controlled extraction vocabulary, and append owner-scoped,
  source-cited record proposals with validation findings and idempotent receipts.
  The private tenant-bound journal is separate from approved retrieval and
  disabled by default. No canonical approval/write, automatic proposal promotion,
  or native mobile attachment relay is implied. Adds the record-intake skill,
  complete interface guide, and staged vendor-neutral write/analytics roadmap.

- Completes Anthropic coverage in the card-level reviewer client factory
  (PR #103) and aligns every operator-facing provider selection with the shared
  four-provider runtime contract. The release check now compares the provider
  declarations in `.env.example` and the runtime reference with the code's
  provider set, so adding a provider cannot silently leave those two primary
  configuration surfaces behind.

- Adds the self-contained client output, reporting, MCP/API, and CRM access
  guide plus the parser-derived complete CLI catalogue and repository-wide
  documentation-contract enforcement (PR #101). The current-state handoff is
  reduced to active continuation facts, stable 1.0.0 is distinguished from
  later Unreleased work, and dated audit counts are explicitly historical.

- Adds a verified no-send common CRM import package after the approved canonical
  load plan. Twelve dependency-ordered UTF-8 CSVs cover accounts, roles,
  addresses, contacts, products, locations, shipments, transactions, lines,
  charges, payments, and applications while retaining the exact canonical
  table, key, row JSON, and row hash. The package accounts for all 28 canonical
  tables, binds every file and companion artifact, supplies a tenant-completion
  mapping worksheet and official vendor-source catalog, and encodes sandbox,
  separate write authorization, reject accounting, reconciliation, and rollback
  gates. It never connects to a CRM or grants live-write authority.

- Renders every workflow diagram into the tracked PDFs, and makes a dropped
  diagram a build failure. The pandoc filter recognised a diagram by matching
  fixed phrases in its prose; two of the three phrases no longer matched any
  document, so those figures had silently stopped rendering and a reworded
  diagram would leave a client PDF with no picture and no error. Blocks now
  declare a `%% bdi-figure: <id>` identifier resolved against
  `assets/figures/<id>.tex`, and a missing identifier or file stops the build.

- Gives `docs/CLIENT_PROCESS_PLAIN_LANGUAGE.md` the LaTeX and PDF halves every
  other tracked document has. It was the one client-facing guide with no
  rendered form, so a sponsor could be handed the other three and not this one.
  It is now in `generate_docs.sh`, `render_docs.sh`, the release check's
  required-file and tracked-document lists, and the render workflow.

- Lets consensus reconcile an Anthropic lane. `consensus.py` keeps its own map
  of provider engine prefixes, and a provider absent from it is refused as
  "unsupported provider or engine" -- correct for an unknown engine, wrong for a
  configured one. An Anthropic secondary lane read all 18 pages of a corpus and
  then could not be reconciled with the primary at all. This is the third
  module-local provider vocabulary a single "wired everywhere" change had to
  find, and each was found by running rather than reading.

- Creates the output directory for source-template observations, like every
  other writer. A run directory is organised by phase, so the destination
  usually does not exist yet, and a bare `FileNotFoundError` made this the one
  command an operator had to `mkdir` for.

- Makes Anthropic reachable from `llm_adapter.py`, which is how every pipeline
  lane selects a provider. It was wired into the vocabulary, the dispatch, and
  every choice list, but `llm_adapter` keeps its own per-provider defaults and
  runner, so an Anthropic lane fell through to a fall-through `ValueError` whose
  message helpfully listed `anthropic` as valid. Found by running it.

- Sends thinking the way each model accepts it. Claude 4.5 and earlier take
  `thinking.type: "enabled"` with a token budget; 4.7 and later reject that and
  take `thinking.type: "adaptive"` with `output_config.effort`. Asking the wrong
  way is a hard 400, not a downgrade: Sonnet 5 failed all 18 pages of a real
  corpus before this existed, and the rule-9 guard made it visible as 18
  exceptions rather than an empty pass. The contract is chosen from the model
  version, with `ANTHROPIC_THINKING_MODE` to override it either way.

- Prices an Anthropic run and supports an identity-linked key. `MODEL_PRICES`
  had no Claude entry, so an Anthropic lane reported an estimated spend of zero,
  which reads as free rather than as unknown. Haiku 4.5 and Sonnet 5 list prices
  are recorded from Anthropic's published pricing, with the cache-read and
  5-minute cache-write multipliers asserted against the base input rate.

  An identity-linked API key is scoped to a workspace and the Messages API
  refuses a request that does not name one. `ANTHROPIC_WORKSPACE_ID` supplies
  the `anthropic-workspace-id` header when set; it is an identifier rather than
  a secret, so it is an ordinary setting, and a key that is not identity-linked
  sends no header.

- Adds Anthropic as a fourth provider, usable wherever the other three are. It
  carries its own credential and model settings and resolves to its own vendor,
  so an Anthropic lane is genuinely independent of an OpenAI, Google, or routed
  lane for consensus -- with no routing to see through, unlike OpenRouter.

  Two details of the Messages API shape the adapter. Structured output comes
  from a required tool call whose `input_schema` is the extraction schema,
  because the API has no strict `response_format`; a model that answers in prose
  is still offered to the schema rather than silently accepted. A retained page
  PDF is submitted as a document block, so the recorded vendor is the one that
  read the glyphs -- the same property the OpenRouter adapter pins its
  file-parser engine to protect. `ANTHROPIC_MAX_OUTPUT_TOKENS` is explicit
  because the API has no server default and a truncated response is
  indistinguishable from a model that read fewer rows than the page holds.

  Extended thinking and a forced tool choice are mutually exclusive in this API,
  so a lane that asks to reason gets a thinking budget and an `auto` tool choice,
  and the tool result is located in the response instead of guaranteed by the
  request.

- Validates every lane signature against real output. Five entries in the
  coverage catalogue named keys their producers never write -- `layout_rows`,
  `reconciled_rows`, `audit_marked_proposals`, `accuracy_statement`,
  `compiled_changes`. They were written while those five lanes had never
  produced anything, so nothing contradicted them, and each would have reported
  "not run" forever. Two artifact shapes also defeated the scan: a producer that
  writes a JSON array rather than an envelope, and one whose top-level keys are
  all generic but which names itself inside its summary.

  Correcting them exposed the opposite error twice: `amendment_proposals` is
  written by three lanes and `thresholds` by two, so both credited a lane that
  had not run. Signatures are now the ones each producer actually writes and
  no other does, verified against real artifacts from three runs. Every one of
  the 56 catalogued lanes has now executed.

- Reports corroboration only when something was actually compared. The
  independent table-reconciliation lane set `corroborated` from the number of
  outcome *records*, not comparisons, so a run where every single cell found no
  independent counterpart reported `corroborated: true` with zero review items.
  Reproduced on the real corpus: 300 reconciliations, all
  `independent_cell_missing`, reported as a completed corroboration. It now
  counts the outcomes that reached an agreement or a disagreement, reports that
  count, and raises the existing no-comparison exception when it is zero. This
  is the same rule-9 shape as an adapter exiting zero after every page failed.

- Says that lane coverage counts everything under a run directory. Smoke-test
  outputs written inside a real run raised its reported coverage by two lanes
  that had not run on that corpus. There is deliberately no exclusion
  mechanism -- one would let a missing lane be hidden, which is the failure the
  report exists to expose -- so the rule is that scratch lives outside the run
  directory, and it is now stated in the command, the operating guide, and a
  test.

- Bounds the client context by what a lane actually sends. The corpus table lane
  loads only the comments from a `client_review_context_v1` artifact and discards
  its `pilot_records`, but it measured the whole file, so a records-bearing
  context was refused for records that never reach a provider -- 1.5 MB of file
  carrying under 2 KB of comments. The comments are measured against the
  configured budget now, and the file keeps a separate, generous ceiling so
  nothing unbounded is read into memory. Refusing rather than truncating stays
  right: truncating a client's words would change what they said.

- Credits a lane only for its own artifact. `manifest.json` is a name several
  lanes use, and matching it by filename credited `operations.py manifest` with
  the inferred-controls manifest -- so a run that never bound its artifacts to a
  hash would have reported that it had. The run manifest is matched on
  `manifest_schema_version`, and a test now asserts that no two lanes claim the
  same filename or identifying key. The report also says plainly that a lane
  which produced output may still have read nothing, and points at
  `run_sentinel.py` for that question.

- Points the run sentinel at settings that exist. Two lanes in its catalogue
  named variables nothing reads -- `GOOGLE_ADDRESS_VALIDATION_API_KEY_ENV` and
  `CLIENT_REVIEW_LLM_PROVIDER` -- so preflight reported fully configured lanes as
  having no credential. A check that cries wolf once is a check an operator stops
  reading, which costs more than it ever saves. Every setting the catalogue names
  is now asserted against `.env.example`, which is what found the second one.

- Checks a run's providers before it spends, and while it runs.
  `run_sentinel.py preflight` resolves every enabled lane to its provider and
  credential variable and refuses when one has nothing behind it, separating a
  missing long-lived key from a Google token that has not been minted yet --
  different faults with different fixes. `run_sentinel.py watch` reads the
  retained raw responses beside a running run, where a failure states its own
  cause, and classifies each as credential, configuration, or transient;
  `--fail-fast` exits non-zero on the first two. A transient failure is what each
  lane's bounded retry already handles, and stopping for one would make the
  sentinel the outage; a credential or configuration fault repeats on every
  remaining page. Both are deterministic and contact no provider. This exists
  because a full trial spent a corpus before anyone noticed Document AI
  returning HTTP 404 on all 18 pages -- the evidence was on disk the whole time,
  and nothing read it until the run was over.

- Reports which pipeline lanes a run actually executed. Rule 9 holds that a
  control which processed nothing has not passed; nothing enforced the same rule
  one level up, so a lane nobody invoked left no artifact, no exception, and no
  trace. A full trial reached CRM staging with fourteen lane families never run
  -- source-native tables, both mapping lanes, template drift, every relationship
  lane, sampling, analytics -- and no command anywhere said so, because each was
  individually optional and none of them ran to report it.
  `run_lane_coverage.py` names every lane that produced nothing, and
  `--require LANE` turns a lane an engagement must include into a failure. The
  catalogue it checks against is verified by `agent_surface_check.py`, so a lane
  added and forgotten fails the release gate rather than going unmeasured.

- Runs every documented command through its own parser. Operating instructions
  were prose nobody executed, and eleven commands in the operating skill were
  rejected by the parsers they described: `schema_discovery.py discover` and
  `allocation_policy.py discover` were documented -- and their own `--help` was
  written -- against an extracted-record and an attributed-record artifact when
  both read the source-template observation; `template_drift.py analyze` was
  shown the ingestion manifest; `client_decision_compile.py` was missing three
  required positionals across two subcommands; and six others named options that
  do not exist or omitted ones that are required. Each would have failed at the
  point of use. `agent_surface_check.py` now parses all 130 documented
  invocations in the release gate and the repository hook.

- Fails the independent-OCR lane when every page failed. All 18 pages of a real
  corpus returned HTTP 404 from a misconfigured processor, every record was a
  retained failure, and the command still exited zero -- so the run continued
  believing it had a third independent vendor. The adapter now refuses when it
  produced no reading at all, names the concrete provider error rather than the
  generic "provider or schema failure", and retains that cause on every
  exception. `table_comprehension` likewise says a page's independent evidence is
  a retained failure instead of reporting missing provenance.

- Gives the relationship lanes something to reason over on a first run.
  `client_input_comments.py` builds the same `client_review_context_v1` artifact
  the relationship lanes take as their only input, but always with an empty
  `pilot_records`, so before any client workbook came back reference discovery,
  iterative proposals, and cross-packet verification had nothing to work on and
  reported success having read nothing. It now accepts `--records` and
  `--max-documents` like `client_review_context.py`; on the trial corpus that
  turned 0 batches and 0 candidates into 2 and 19, and the iterative lane into
  three passes of 108 primary and 108 buddy proposals. Those lanes now refuse an
  empty context rather than reporting success on it.

- Stops copying a database password into shared artifacts. The settings snapshot
  redacts by name -- `KEY`, `TOKEN`, `PASSWORD`, `SECRET` -- and
  `CANONICAL_DATABASE_URL` carries none of those words while embedding its
  credential in the authority: `postgresql://user:password@host/db`. The snapshot
  reaches `analytics.py` and the `operations.py` manifest, both of which are
  shared, and `runtime-configuration.md` promised the value was never recorded.
  Values are now redacted by shape as well as by name, keeping the host and
  database so an audit can still name the target.

- Checks every deliverable before creating the client folder. The directory was
  created first, so a missing or restricted file left a folder behind that then
  blocked the retry with "already exists" -- the no-clobber rule working against
  the operator over a mistake they had already been told about. One preflight
  now validates exactly the list that will be copied, and the copy-time re-checks
  it made unreachable are removed rather than left as dead code.

- Repairs the safe-consolidation lane, which the package move broke.
  `REPOSITORY_ROOT` was `parents[1]`, correct at `scripts/safe_review_consolidation.py`
  and silently `scripts/` once the module moved into `scripts/client_review/`. Every
  lane it orchestrates was then invoked as `scripts/scripts/<lane>.py` and failed
  with exit code 2. The suite never caught it because it does not run the
  subprocess, so the lane paths are now asserted directly. This is the regression
  the full refactor risked, and it took a real run to surface it.

- Lets an explicit flag beat the environment in the same lane. `--output-dir` and
  `--out` were accepted and then overridden by
  `CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_OUTPUT_DIR` and `..._OUTPUT_FILE`, read at
  use time, so the artifacts landed somewhere the operator had not asked for.
  Every setting now supplies an argparse default, matching the rest of the
  repository, and the parser is built by `build_parser()` so the precedence is
  testable.

- Counts only what a client changed as an answer. The workbook pre-fills the
  answer cell of a question needing no reply with "No answer needed", and
  `read-answers` read any non-empty cell as a reply -- turning that placeholder
  into a decision the client never made. Found by running the return path against
  a real pack for the first time and getting two answers from one filled row. An
  answer is now a cell that differs from the issued copy, which covers the
  placeholder and anything else a future workbook pre-fills.

- Produces the evidence-graph manifest, the last of three lanes documented with
  an input nothing wrote. `evidence_graph.py manifest` builds the typed,
  hash-bound artifact list `build` consumes, and it lives beside the loader that
  validates it so the two cannot drift. Types are named explicitly rather than
  inferred from filenames: a misclassified artifact would put the wrong claims in
  the graph under right-looking provenance, and no later control could tell. It
  refuses up front what `build` would refuse later -- an artifact outside the
  manifest's own directory, a missing file, an unsupported type -- while it is
  still cheap to fix. The skill guide showed `build RUN/manifest.json`, the
  operations manifest, which has none of the required keys.

- Gives three lanes the input they were documented with and nothing produced.
  `schema_discovery.py discover`, `allocation_policy.py`, and
  `template_drift.py analyze` all consume a source-template artifact; an operator
  had to hand-build it, which is how a lane ends up never run.
  `scripts/template_observations.py` builds it from a completed consensus run's
  retained printed labels -- 109 distinct labels across the trial corpus, kept
  verbatim by the extension-channel change.

  A template is one document *family*, not one document. Grouping on the exact
  set of labels a page happened to show produced 18 templates from 18 pages of a
  single statement layout, because capture varies page to page (2 to 47 labels).
  That variance is extraction noise, not layout difference, so a family's labels
  are the union of what its documents showed and each label carries the number of
  documents it appeared in -- a label in 2 of 18 is a different observation from
  one in all 18, and the artifact says which.

  Nothing here maps: a label is copied exactly as printed and never renamed, and
  labels are ordered deterministically rather than in layout order, so drift
  detects an added or removed label rather than a reordering. A document
  retaining no label is an explicit exception, and a corpus retaining none
  refuses rather than writing an artifact that reads as "this corpus has no
  layouts".
- Values a document by what it actually carries. `attribution.py`,
  `completeness.py`, and `sampling.py` each read a printed header total at six
  separate sites with no shared helper. That is right for an invoice and wrong
  for anything whose money lives in its lines: on a real commission-statement
  corpus `header.total_amount` was absent from 16 of 18 documents -- never read
  by either engine, because the documents do not carry one -- and attribution
  reported $17,120.50 as the corpus total against $335,822.26 of accepted line
  commission. A figure that is 5% of the money still looks like an answer.

  `scripts/document_value.py` now decides this once. A printed total wins; a
  document stating none is valued at the **signed** net of its own lines, because
  a statement carrying a chargeback is worth its net and summing absolute values
  would report money never earned; a document with neither is excluded with a
  reason rather than counted as zero. One monetary field is summed per line, so a
  commission and the base it was computed from are not counted twice. Attribution
  and sampling report a `value_basis` breakdown, so a corpus total that is mostly
  derived can be read as this pipeline's arithmetic rather than the client's own
  statement.

- Makes the sampling refusal name what emptied the frame. It said "Check that
  total_amount is populated" whatever the cause, which sent an operator to fix
  extraction when in fact every document had been excluded as an exception --
  correctly, since sampling covers the auto-accepted population only. It now
  reports the exclusion breakdown.

- Corrects the pipeline guide's `preprocess_pages` invocation. Intake writes its
  one-page masters to a `pages/` subdirectory, so the command takes
  `RUN/pages/pages`; the guide said `RUN/pages`, which refuses. The refusal names
  the fix, so this cost a reader one failed command rather than a wrong result,
  but the runbook should not have been the thing that was wrong.

- Names provider extraction findings by their lane. Exceptions from
  `primary_exceptions.json` and `secondary_exceptions.json` carry a generic
  collection name, so the review lane classified them as unclassified -- 115 of
  them on a real run. They always triggered a pack, since an unclassified finding
  is treated as one nobody owns, but the report said nothing about where they
  came from. A new `extraction` block classifies them, with its own
  `CLIENT_REVIEW_LANE_EXTRACTION_THRESHOLD`.

- Counts OpenRouter spend, which was reported as zero. `response_usage` read the
  Responses dialect (`input_tokens`/`output_tokens`) and the Google dialect, but
  a Chat Completions response -- what the OpenRouter lane returns -- names the
  same numbers `prompt_tokens`/`completion_tokens`. It matched the OpenAI branch,
  read absent keys, and reported the lane as free. On a real 18-page run the
  secondary lane was the larger of the two at 408,535 input tokens. A usage
  object naming neither dialect is now reported unknown rather than zero.

- Finds every lane's retained responses. The documented invocation writes one
  directory per lane beneath a shared `raw/` (`--raw-dir RUN/providers/raw/primary`),
  and only the shared parent was scanned -- so a run laid out exactly as the
  runbook says summarised one response instead of 54. Lane directories beneath a
  raw folder are discovered, and a directory literally named `raw` is labelled by
  its parent so the lane stays traceable.

- Closes the client review loop. The lane produced a workbook and the instruction
  PDF told the client to send it back, but nothing read it: the pack was a
  one-way document and an operator receiving a completed workbook had to retype
  its answers. `client_review_lane.py read-answers` validates a returned workbook
  against the immutable issued copy -- same question rows in the same order,
  every cell outside the two answer columns unchanged -- and extracts each answer
  with its note. An answer matching none of the offered options is retained
  exactly as written and reported unmatched rather than discarded or mapped to
  the nearest option, and an answer to a rule 7 finding is recorded as informing
  an item-by-item recheck. Nothing is applied.

  The lane now takes a subcommand: `build` for what it already did, and
  `read-answers` for the return path.

- Fixes silent loss of client answers in the existing workbook reader.
  `_xlsx_cell_values` read only `<v>` cells, so a returned workbook whose editor
  wrote text inline rather than into the shared-string table came back with its
  answer cells empty. A fixed cell would have failed loudly on comparison, but an
  *editable* one -- the client's actual decision -- was silently recorded as
  unanswered. This affected the existing `client_review_package import-decisions`
  path as well as the new one.

- The final review queue no longer reports a clear gate over nothing. Fed an
  artifact carrying no review-bearing content -- the wrong files, or artifacts
  from a stage that never ran -- it returned `gate_status: clear`, which is the
  one outcome the final gate must never produce, because a clean gate there
  reads as authorization. It now refuses and names the artifacts it was given.
  A genuinely empty exception list is still a real clean result and still
  passes, and one review-bearing artifact among several is enough.

- Registers every client payment row this pipeline cannot use, instead of
  dropping it. `aging_closure` returned early on a row with no invoice number,
  so a remittance keyed on a reference column, or one with a blank invoice
  cell, vanished with no record anywhere -- and the invoice it paid was then
  reported as open. That is an understatement of closure produced by the control
  rather than by the evidence, and it is exactly what rule 2 forbids. Two of
  three rows in a realistic export were disappearing. They are now retained with
  their raw content and original column names under
  `unusable_payment_applications`.

  `release_check` had a control for this class and it did not cover the case:
  it flagged a row skipped by `continue` but not one skipped by a bare `return`,
  and the payment consumer was never registered as a side-channel loader. Both
  are fixed, with the check scoped to the function that reads client rows rather
  than the document iteration around it.

- Accepts a client payment or remittance export as CSV, not JSON alone. The GL
  export beside it always accepted both; payments met a CSV with a raw JSON
  decoder traceback, and a remittance is usually whatever the finance system
  exports.

- Matches client column headings as printed. `Job #` -- the heading a client's
  own note called their ACK number -- was rejected as unrecognised, taking every
  row of an otherwise perfect reference export with it. Headings now normalize
  through one shared function, and only a heading that says it is a number
  (`Invoice No.`, `Job #`) expands to the number key. Expanding every one-word
  heading was tried and rejected: it invents `amount_number` from `Amount`, and
  would let a `Customer` column answer to `customer_number` and be read as the
  identifier when a real `Customer Number` column sits beside it.

  Rejection registers now report the client's own heading rather than the
  normalized key, because an operator diagnosing a refusal has to find that
  column in the file the client sent.

- Accepts genuinely unstructured client comment spreadsheets. `.txt`, `.md`, and
  `.pdf` comment files were already freeform -- one comment per non-empty line --
  but CSV and TSV alone required a header naming one of five columns, and a
  client who typed notes into Excel and exported them was refused. That is the
  format clients most often send. A delimited file with a recognised comment
  column still keeps every source column; one without is now read as what it is,
  each non-empty row becoming a comment with its original cells retained beside
  the joined text, and no row treated as a header to discard. `notes` joins the
  recognised column names alongside `note`.

  The guarantees are unchanged: comments stay hash-bound, `reasoning_only`,
  `independent_consensus_input=false`, and marked
  `untrusted_client_context_not_source_evidence`. They explain terminology and
  priorities and are never evidence, authorization, or clearance.

- Fixes a client-facing rendering defect: a stray trailing comma made the
  adjudication template's `why_it_matters` a single-element tuple, so that
  question would have rendered `('Picking one on your behalf...',)` verbatim in
  a PDF sent to a client. Line coverage cannot catch this -- the dict literal
  executes whichever type it holds -- so a test now asserts the shape of every
  template's prose fields rather than the few families a given corpus happens to
  produce. No corpus exercised so far contained an adjudication finding.

- Refreshes `HANDOFF.md`, which is the designated current-state document and had
  fallen six merges behind: it still described the handwriting lane as an
  unmerged working tree at `32a21dd` and carried August 26 gate figures. It now
  records `main` at `85718a1`, the client review lane, the review-package
  consolidation, the consensus extension-channel change, the slot-equivalence
  lane, and the OpenRouter corrections.

- Documents how a run parallelises, in `tasks/subagents.md`. The Phase 3 provider
  lanes write to separate paths and can run concurrently, and `RequestThrottle`
  coordinates request and token budgets across local processes through
  `LLM_THROTTLE_DIR` keyed by provider -- so two lanes on one vendor share that
  vendor's budget rather than each assuming it owns all of it. The guidance also
  names what does not coordinate: the provider quota circuit is process-local, so
  one lane reporting a quota failure does not mean the others have stopped. And
  it names what an orchestrating agent must never do -- enable a lane, start a
  downstream phase before every input lane finished, stand in for vendor
  independence, or reconcile lanes itself.

- Adds the client review lane to `README.md`, `CLAUDE.md`, `CHATGPT.md`, and the
  Cursor rule, and `scripts/client_review/` to the repository map.

- Completes the client-review consolidation begun in the previous change. Every
  review lane now lives in `scripts/client_review/`: the nine reasoning lanes
  (card, context, inference, iterative, consensus, exception-resolution,
  cross-packet, cross-record, simulated), the full-dataset agent, the safe
  consolidation orchestrator, and the client package builder, beside the
  protection, queue, grouping, question, evidence, and rendering modules they
  already depended on. A reviewer's safety depends on all of them agreeing about
  what a protected finding is and what a review packet looks like, and they
  cannot agree while each decides for itself.

  Migration ran in dependency order so no importer was rewritten twice:
  `client_review_llm` first, since eight modules import it, then the four-way
  dependents, then the leaves. Every command path is unchanged -- each lane keeps
  a thin entry point at the path it always had, and a test holds all fourteen
  migrated paths to resolving the same `main()` the package defines.

  This is a move, not a rewrite. No lane's independence rules, buddy
  verification, packet construction, or provider handling were touched, and each
  entry point preserves its original `__main__` behaviour exactly -- including
  the error handling that turns a bad input into a message rather than a
  traceback in the cross-record and full-dataset agent lanes, and the exit codes
  the consolidation and package builders return.

  `handwriting_review.py` deliberately stays outside: it reconciles handwriting
  readings against each other, which is a Phase 3H control rather than a
  client-review lane.

- Adds `scripts/client_review_lane.py` and the `scripts/client_review/` package:
  one path from any blocked control to a pack a client can answer. Before this,
  producing a client review meant chaining four commands with different inputs
  at one point in the pipeline, while every other block reached the client as
  raw exception rows or waited until delivery -- what a client received depended
  on which control stopped and who assembled the request. The lane consolidates
  whatever a blocked control produced, groups repeated causes, reduces them to
  the smallest question set that settles them, renders the source page behind
  each question, and writes a workbook to answer in and an instruction PDF to
  answer from. On a real corpus 881 consensus findings became 4 questions
  covering all of them.

  The instruction PDF is built directly with `pikepdf`, matching how this
  repository already writes OOXML without a rendering dependency: the text stays
  real text and each page image is embedded as its original JPEG, with a
  landscape scan given a landscape sheet so the column under question is legible
  rather than decorative.

  Each control carries its own `CLIENT_REVIEW_LANE_<CONTROL>_THRESHOLD`, so the
  lane can run after every stage without producing a pack nobody needed. A
  threshold decides whether to ask now, never what to keep: items below a floor
  stay in the exhaustive queue and the run's exceptions unchanged.

  Answering is kept strictly separate from clearing. Findings protected under
  rule 7 are still asked about -- the client's answer is what resolves them --
  but the pack states that the answer is applied and each affected item then
  re-checked individually, and `batch_clear_permitted` records which questions
  may close their group outright.

- Moves the deterministic review mechanics into `scripts/client_review/`:
  `protection.py`, `queue.py`, and `grouping.py` now sit beside the reduction and
  rendering that depend on them. A reviewer's safety depends on grouping,
  reduction, and rendering agreeing about what a protected finding is, and they
  cannot agree while each decides for itself. `review_grouping.py` and
  `final_review_queue.py` remain as entry points so existing runbooks and
  operator habits keep working, and a test holds those paths to it.
  `agent_surface_check.py` and `release_check.py` were taught about package
  modules and delegating entry points, so a module moved into a package cannot
  drop out of the documented surface unnoticed.

- Stops reconciling the source-label extension channel in `consensus.py` and
  retains it per engine instead. Its key is the printed label plus an occurrence
  index, so two engines share a key only when they spell a label identically
  *and* enumerate repeated labels in the same order. Measured on an 18-page
  corpus, 317 of 339 label slots resolved to a single engine and 147 carried a
  label repeating inside its own document: the channel was comparing list
  positions, not facts, and produced 41% of all consensus exceptions
  (1,547 to 908, a 58.6% exception rate to 46.3%). Every entry is retained under
  `source_labelled_field_proposals` with its provenance, nothing is dropped, and
  a document whose engines returned only extension entries is an explicit
  `no_consensus_eligible_field` exception rather than a clean result.

- Adds `schema_discovery.py slot-equivalence`, which reads a completed consensus
  run and proposes which controlled fields the client's documents treat as one
  business fact. Two engines reading one printed column can file it under two
  different fields, because a vocabulary wide enough to receive unfamiliar client
  documents necessarily offers more than one plausible home for a fact; consensus
  compares one slot at a time and cannot know the slots coincide. Narrowing the
  vocabulary would trade that cost for a worse one, so the equivalence is
  discovered per client. Detection pairs placements only inside one container and
  only on identical values, so engines that read *different* values remain the
  genuine disagreement consensus recorded. Each observation is labelled
  `cross_engine_placement` (two engines, two names) or `intra_engine_duplicate`
  (one engine using both names, which is an engine declining to choose rather
  than agreement); a pair supported only by the latter is retained and never
  approval-eligible. `registry-update` writes a `slot_equivalence` rule only from
  a client-approved proposal an independent verifier confirmed, and retains every
  refused decision with its reason.

- Lets OpenRouter read a retained page PDF, but only on a model that accepts
  files **natively**. Support is verified against OpenRouter's own model
  catalogue (`architecture.input_modalities` contains `file`) before any page is
  sent, and the `file-parser` engine is pinned to `native`. Left to its default
  OpenRouter OCRs a PDF with a separate vendor's engine and hands the text to the
  routed model, which would record the wrong vendor as having read the page and
  could put two "independent" lanes on one shared OCR pass. An unreadable
  catalogue or an unlisted model fails closed.

- Closes two vendor-resolution holes in `model_vendor`, which `consensus.py` and
  the buddy lanes both rely on. An alias slug (`~openai/gpt-mini-latest`)
  resolved to `~openai` and so compared unequal to `openai`, passing as
  independent of it; an auto-routing slug (`openrouter/auto`) resolved to the
  router itself while selecting an unknown model at request time. Aliases now
  resolve to the real vendor and auto-routing resolves to the unresolved
  sentinel.

- Routes every agent surface to the operating skill. The root `SKILL.md`,
  `AGENTS.md`, and the Cursor rule named no task guide at all; each now carries
  the phase-to-guide map and points at `scripts/agent_surface_check.py`, which
  keeps those instructions in step with the code.

- Resolves buddy-lane independence to the model vendor, matching `consensus.py`.
  `validate_reviewer_roles` compared provider *names*, so an OpenAI primary could
  be "independently confirmed" by an OpenRouter lane routing `openai/...` — one
  set of weights wearing two names, in the iterative, cross-packet, and
  exception-resolution lanes. A routed slug with no vendor prefix is now refused
  rather than treated as its own group.

- Removes the Google access-token setting from `.env` and `.env.example`
  entirely. Document AI and handwriting OCR mint a short-lived token from
  Application Default Credentials at run time through
  `reauthorize_google.access_token`; an exported token still takes precedence for
  CI and secret managers. A `ya29.` token lives about an hour, so a pasted one is
  stale before it is useful, and there is now no field in which to paste it.

- Isolates the test suite from the operator's private `.env`. There was no
  `conftest.py`, so every test that ran a CLI read whatever `.env` the machine
  had; enabling a disabled-by-default lane locally turned a passing suite into a
  failing one, while CI — which has no `.env` — stayed green. A quality gate
  whose verdict depends on a gitignored file is not a control.

- Declares dependencies without version pins. `requirements.txt` and
  `requirements-dev.txt` now name packages only; `requirements.lock` remains the
  hash-pinned resolution that both gating jobs install with `--require-hashes`.
  Updated with it: `openai` 3.2.0 → 3.3.1, `google-genai` 2.13.0 → 2.20.0,
  `pikepdf` 10.11.0 → 10.12.0, `ruff` 0.16.3 → 0.16.4.

- Gives every command-line argument help text. 407 of the 584 arguments across
  54 commands previously rendered as a bare flag name, while
  `references/command-line-reference.md` told operators the executable parser was
  the exact contract and routed them to `--help` to discover options. Recurring
  option wording now lives once in `scripts/cli_help.py` and is attached by
  `apply_shared_help`; command-specific options state their own. A new release
  check fails if any argument neither carries inline help nor uses the shared
  vocabulary, so an option cannot ship undocumented again.

- Validates the Google operator caps against the recorded model capabilities
  before a run starts. `GOOGLE_VERTEX_AI_MODEL_MAX_FILE_BYTES`,
  `_MAX_FILES_PER_REQUEST`, and `_MAX_INPUT_BYTES` were documented as hard limits
  but read by nothing, so a cap set above what the model accepts surfaced as a
  provider error partway through a paid run. `_MAX_PAGES_PER_FILE` is now
  documented as a recorded fact rather than a comparison, because intake bursts
  every source into one-page masters.

- Completes the operating skill. It now carries a lane-by-lane table of all 65
  commands with their phase and whether each is optional or disabled by default,
  and adds task guides for extraction, the independent cross-checking lanes,
  handwriting, the business controls, the evidence graph and relationship lanes,
  review reduction, repository maintenance, and subagent delegation. The lanes
  that were previously undocumented in the skill — Document AI corroboration,
  independent table reconciliation, cross-record search, cross-packet discovery
  and verification, the iterative relationship lane, exception resolution, the
  evidence-graph commands, inferred controls, allocation policy, template drift,
  schema discovery, golden-set evaluation, and the retrieval surfaces — are all
  covered. `scripts/agent_surface_check.py` now discovers skill files rather than
  reading a hand-maintained list, so a new guide cannot go unchecked.

- Widens the handoff freshness rule to catch work described as unmerged. A
  squash-merged branch stops existing while a sentence claiming it is pending
  still reads as current work; the rule previously matched only "not yet
  committed", and `HANDOFF.md` had gone stale in exactly that way.

- Removes `evidence_packet` from `scripts/review_agent.py`. It was reachable only
  from its own tests, and its docstring claimed compatibility tooling that does
  not exist.

- Adds `skills/business-doc-operations/`, an operating skill for running the
  pipeline rather than changing the repository. It routes to task guides for run
  setup and file placement, the pipeline sequence, progress and spend
  monitoring, issuing and re-ingesting the client workbook, generating the
  canonical export and CRM staging, and assembling the client folder, plus a
  troubleshooting table that explains what each refusal means.

- Adds `scripts/client_delivery_package.py`, which assembles the final
  client-ready folder and nothing more. It refuses raw provider responses,
  credentials, caches, and resume state even when they are named explicitly,
  delivers the evidence graph cleaned of proposal-state content while retaining
  the exact counts of what was withheld, refuses to build while the final review
  gate is open unless the package is deliberately marked provisional, and hashes
  every delivered file into a manifest.

- Adds `scripts/mutation_check.py`, which measures what the 100% branch gate
  cannot see. It is not wired into CI: mutmut 3.x emits no machine-readable
  summary in a non-interactive runner, and the runner refuses rather than
  reporting a count it did not receive. An earlier version defaulted to zero
  surviving mutants and passed green while the tool had not run.

- Adds `scripts/agent_surface_check.py` and a Claude Code `PostToolUse` hook, with
  the equivalent `after_code_change` step recorded for ChatGPT in
  `agents/openai.yaml`. Operating instructions that name a script are now checked
  against the live scripts and the command-line reference, so a rename cannot
  leave an agent following stale instructions. The release gate runs the same
  check.

- Corrects nine critical and high-severity control defects found by an
  independent review, each of which passed the strict gate before the fix.
  The evidence graph registers a node-property conflict as a protected exception
  instead of aborting the build, merges duplicate content-addressed edges, and
  derives every provenance pointer from the container that actually held the
  record. Completeness retains a rejection register for GL rows it cannot bucket
  or parse and refuses a tolerance verdict while the baseline is incomplete.
  Consensus groups readings by complete-linkage agreement over a deterministic
  order, so the engine argument order can no longer decide the accepted value,
  and independence is resolved to the model vendor behind a router. Arithmetic
  scales each rollup tolerance by the rows it actually sums, keeping header
  identities at the base tolerance, and no longer reports a document as proved
  when its lines could not be read. Allocation refuses an implausible effective
  commission rate. The returned-workbook preflight rejects DTD and entity
  declarations. Independent table reconciliation and the graph build report what
  they failed to process instead of a clean empty result.

- Corrects address normalization defects found by comparing the tracked
  normalizer against a superseded untracked working copy. An address whose
  declared country contradicts its postal code is registered as
  `address_country_postal_mismatch` instead of passing clean with the postal code
  sitting in the city field; a region in its own comma segment is recognized as
  the region rather than becoming the city; Canadian province codes and the US
  ZIP-using territories are recognized. Only countries with a postal pattern here
  are judged, so five-digit French and German codes are not false positives. The
  two superseded working copies were discarded and `private/` is now ignored.

- Closes the remaining instances of two control classes: a negative effective
  commission rate reaches critical review rather than classifying as an
  administrative allocation, and a canonical export with no approved rows refuses
  to build a silently empty retrieval index. `scripts/render_docs.sh` no longer
  reports a false document defect when ripgrep is absent.

- Applies the remaining medium and low findings from the same review. Retained
  provider artifacts record whether a response was replayed from cache; token
  estimation counts UTF-8 bytes with a wide-character correction and a
  configurable safety factor; the shared throttle reserves its slot and releases
  the lock before sleeping, so configured concurrency is no longer serialized;
  reference-export and entity-resolution work that cannot be processed is
  registered rather than dropped; evidence-graph reference detection matches
  whole tokens; scan profiling names unread probes; self-reported model
  confidence is no longer a term in either review-reduction decision; and
  `GOOGLE_VERTEX_AI_MAX_REQUEST_TOKENS` and `OPENROUTER_MAX_REQUEST_TOKENS` are
  lowered to values the per-minute safety budget can actually reach.

- Adds a disabled-by-default Google Cloud Vision handwriting OCR lane. It
  processes every retained page with `DOCUMENT_TEXT_DETECTION`, binds words only
  to separately detected normalized handwriting regions, retains page renders,
  raw responses, provider/page hashes, and every exception, and emits one
  proposal-only HTR vote. Handwriting reconciliation now counts provider
  independence groups so same-provider model roles cannot validate each other.

- Adds general client-input comment parsing for JSON, CSV, TSV, text, and
  Markdown. The parser preserves comments and source fields verbatim with file
  hashes in `client_review_context_v1`; table-comprehension corpus forward and
  refinement packets can receive that context for terminology and priorities,
  while prompts and resumable state explicitly exclude it from evidence,
  consensus, authorization, and control clearance.

- Adds a disabled-by-default, configurable AI simulated-client comment lane.
  It uses the selected provider/model (default OpenAI `gpt-5.6-luna`) to create
  a separate JSON/CSV/XLSX draft package with raw-response and evidence-quote
  provenance. Every comment visibly says it is simulated and not client
  authorization; protected items remain human review and every failure or cap
  remainder is retained explicitly.

- Hardens simulated-client and exception-resolution evidence binding so a
  proposal cannot borrow a quote from another item or unrelated document in the
  same packet. Simulated-client packets de-duplicate source records, measure
  UTF-8 byte limits accurately, retain exact evidence JSON pointers and complete
  resolved configuration, checkpoint atomically for hash-verified resume, and
  support separate no-clobber recovery overlays for retained exceptions.

- Preserves the exact original findings, control kinds, and exception IDs in
  control-exception provider, oversized, and cap-deferred artifacts so a later
  no-clobber retry reconstructs the named control packet instead of guessing
  from document membership.

- Adds a reusable, proposal-only control-exception resolution lane for
  reassembly, validation, and attribution findings. It binds preserved client
  feedback as reasoning-only context, requires configured independent primary
  and buddy providers over the same packet, validates packet-bound identifiers
  and evidence quotes, and records raw responses, rejected outputs, and every
  unmatched or residual exception.

- Binds client-review resume artifacts to the exact reviewer instructions so a
  protected-final-run policy change cannot reuse decisions made under a
  different prompt contract.

- Centralizes byte-adaptive packet packing in `llm_runtime.py`. The utility
  retains individually oversized items and batch-cap remainders explicitly,
  preventing silent corpus truncation in future provider/review lanes.

- Makes cross-packet cap, identifier, and provider-failure exceptions retain
  exact `source_document_ids`, so every deferred or failed neighborhood can be
  reconciled or retried without an artifact repair. Explicit CLI limits now
  preserve a supplied zero for the command's fail-closed range validation.

- Makes schema discovery and table-comprehension credential references resolve
  from their configured provider, including an independent OpenRouter schema
  buddy, instead of recording a stale OpenAI-specific credential reference.

- Adds a no-provider-call `client_review_iterative.py --finalize-existing`
  recovery path for intentionally stopped iterative runs. It reconstructs only
  valid completed raw primary/buddy packets and records any malformed or
  unmatched packet as an explicit exception.

- Adds a protected release lifecycle: short-lived semantic-version release
  branches, immutable annotated `vMAJOR.MINOR.PATCH` tags from the exact
  `main` tip, a tag-triggered verify-and-publish workflow, release-contract
  checks, and durable contributor/agent recovery instructions.

- Sets the future cross-packet proposal lane to a moderate 6-document,
  80,000-byte neighborhood by default, reducing provider pressure while keeping
  serialized context and exception boundaries explicit.

- Adds explicit, source-backed convergence controls to iterative and
  cross-packet client-review proposal lanes. Cross-packet follow-up is now
  automatic, limited to newly exposed unresolved neighborhoods, and terminates
  when a pass yields no material new independently buddy-confirmed relationship.

- Sets the default iterative client-review run to three bounded primary/buddy
  passes while retaining the hard implementation maximum of five; each run
  still preserves every raw response and remains proposal-only.

- Splits GitHub Actions verification into a least-privilege strict-quality job
  followed by a dependent acceptance-controls job, runs automatic CI on pull
  requests and `main` only, and adds regression contracts for full action pins,
  disabled checkout credentials, coverage-gate invocation, acceptance ordering,
  and the intentionally manual LaTeX render workflow.

- Adds a source-template-bound Gemini-primary/OpenAI-buddy schema-discovery
  contract. Buddy-confirmed primary mappings may be labelled
  `inferred_high_confidence` only with retained template/page evidence and the
  configured confidence floor; they remain client-review proposals.

- Makes adaptive client-review batching reserve space for the complete primary
  and buddy packets, and uses slower Vertex pacing plus bounded deadline/capacity
  retries so packet-boundary and transient-provider failures remain explicit
  rather than causing avoidable batch loss.

- Adds validated, bounded, hash-retained JSON evidence-graph inventory context
  to LLM schema discovery. Source-template/page evidence remains required for
  every proposal; the inventory cannot create mappings, approvals, or canonical
  facts.

- Makes the test-suite `scripts/` import path explicit in pytest configuration
  so local and CI quality gates do not depend on an ambient `PYTHONPATH`.
- Requires the iterative Gemini-primary/OpenAI-buddy lane to validate the same
  reasoning-only `client_review_context_v1` boundary as the legacy discovery
  lane before either provider can be called.
- Records Gemini-on-Vertex model capacity limits separately from conservative
  operational request caps, passes the configured structured-output reserve to
  the SDK, and fails closed when a configured reserve cannot fit the declared
  model context or output limit.
- Adds repeatable inferred attribution, GL-shaped, and payment-shaped proposal
  artifacts for runs without authoritative client exports; their explicit
  blocked state cannot clear completeness or authorize canonical facts.
- Adds preservation of returned client responses, bounded client-context
  reference discovery, a Gemini-primary/OpenAI-buddy iterative relationship
  proposal lane, and deterministic JSON evidence-graph build/query commands.
  Client comments and all inferred states remain outside independent consensus
  and authoritative GL/payment evidence.
- Makes the iterative relationship lane adaptive to serialized packet size,
  supports explicitly supplied full-corpus records, resumes verified work, and
  retains a record-level exception instead of dropping an oversized source item.
- Adds a full golden-set acceptance harness with exact-value, abstention-aware,
  review-routing, protected-escape, arithmetic, completeness, and review-volume
  metrics overall and by template, document family, field, and risk category.
  Passing evidence never authorizes automation.
- Adds `source_layout_v1` template fingerprinting, deterministic drift
  diagnostics, exact-match-only reuse, standard review exceptions, and an
  operator-authorized append-only template registry update.
- Adds a returned-client-decision compiler that validates change scope against
  safe decision groups, previews affected items/documents/fields/amounts and
  required rerun lanes, content-hashes the plan, and requires a separate
  default-defer operator authorization without applying production facts.
- Rewrites the README, technical operator manual, skill, agent entry points,
  and handoff around explicit documentation ownership and a newcomer-readable
  end-to-end runbook; adds architecture, failure recovery, troubleshooting,
  extension, glossary, and exact client-return guidance.
- Adds a complete command-line entry-point reference and makes runtime
  configuration one-row-per-setting. The release check now rejects missing,
  duplicate, unknown, or default-drifted `.env` documentation and any unindexed
  argparse CLI.
- Fails closed on duplicate consensus-engine identities, compares identifiers
  without numeric coercion, uses the voted document type rather than the first
  engine's raw type, and binds cross-record LLM proposals to the exact reviewed
  field.
- Verifies retained evidence references and reviewed-field identity before a
  full-dataset agent proposal can enter a later review-reduction policy.
- Completes a repository-wide consistency and methodology audit. Client-return
  workbooks are now validated against a separately preserved immutable issued
  workbook, including exact group/fixed-cell comparison and workbook hashes.
- Centralizes fail-closed protected-review classification across card review,
  grouping, safe consolidation, full-dataset review, and client packaging;
  unknown reason codes and sensitive fields remain protected.
- Hardens completeness reconciliation against cross-vendor duplicate invoice
  numbers and blocks the gate when GL/payment evidence is absent, ambiguous, or
  unresolved, or when sequence/calendar/date gaps remain.
- Corrects MUS reporting to a one-sided overstatement bound, creates an
  interval-derived certainty stratum, rejects invalid/duplicate/off-sample
  findings, and refuses an upper bound beyond the checked-in confidence-factor
  table. Attribute sampling is documented as a selection plan, not a computed
  observed field-accuracy result.
- Removes the factual-retrieval open-review escape, makes generated client
  packages byte-reproducible, rejects generated root data artifacts and
  operator-specific paths at release time, pins CI actions and Tectonic, and
  aligns the Python floor with dependency metadata.
- Adds `scripts/run_monitor.py`, a read-only live progress and estimated-spend
  monitor for retained LLM, Document AI, Address Validation, and Places
  artifacts. Snapshots are atomic, non-secret, and configurable by price file.
- Documents live monitoring and provider-billing reconciliation in the README,
  operations reference, and technical documentation.
- Adds explicit provider/model routing for the primary and secondary consensus
  extraction lanes and the proposal-only reasoning lane. `llm_adapter.py
  --lane` selects one lane per invocation, preserves provider/model handoff
  identity, and keeps future provider additions centralized in the adapter and
  lane resolver.
- Adds the optional full-dataset review agent. It inventories all supplied
  artifacts, reasons across validated and outstanding records, finds
  cross-record signals and contradictions, iterates bounded hypotheses, and
  emits process-improvement proposals without changing canonical facts.
  Handwriting is retained only as non-blocking secondary context in this lane.
- Adds the optional cross-record evidence phase and simulation-only all-approval
  stress gate, plus an invocation-only independent final reviewer model/provider
  override that inherits the active client-review settings. Protected review
  categories remain fail-closed and every original item remains retained.
- Hardens provider calls with bounded transient retries, capped backoff, strict
  server-directed `Retry-After` handling, and a cross-process provider throttle.
  Terminal reviewer rate limits open a circuit breaker, successful cards can be
  reused with `--resume-from`, and usage reports retain retry/rate-limit/circuit
  telemetry.
- Adds source-native table comprehension with evidence-linked profiles/rows,
  source-layout-only corpus context, capped stable-set refinement, and linked
  amendment proposals rather than value replacement.
- Adds optional Google Document AI OCR/table evidence, conditional buddy audit,
  deterministic exact-registry table reconciliation, and explicit protected-fact
  review routing.
- Adds no-send CSV/API staging, calibration evaluation for handwriting-region
  and party-role proposals, and an explicitly enabled TLS-only read-only HTTPS
  retrieval endpoint.
- Synchronizes the client, technical, runbook, agent, and lifecycle diagrams
  with the implemented controls and remaining acceptance boundaries.
- Adds checkpointed, layout-aware LLM row proposals for client-supplied display layouts. Each immutable page produces a raw-response-linked, review-only checkpoint; combined rows require independent consensus and final client review.
- Fixes the LLM-adjudication handoff so its top-level candidate count satisfies the operational adapter contract, including zero-candidate audit runs.

## 1.0.0 — 2026-08-13

First production release of the auditable business-document ingestion control
layer.

- Preserves a byte-verified source PDF, immutable one-page masters, retained raw
  provider responses, and amendment-only corrections.
- Provides conservative intake, reassembly proposals, extraction consensus,
  arithmetic proof, deterministic validation, address evidence, independent HTR
  reconciliation, attribution, completeness, sampling, and an exhaustive final
  client-review gate.
- Adds versioned PostgreSQL deployment planning, checksummed canonical load
  planning, approved-fact SQLite retrieval, and a dual-era read-only MCP stdio
  boundary supporting the 2026-07-28 protocol and legacy initialization.
- Expands the canonical schema for selling locations, acknowledgements, jobs,
  dollar attribution, handwriting regions, append-only amendments, and
  multi-entity load reconciliation.
- Makes operational control outputs no-clobber by default, emits portable
  versioned run manifests, and adds a fail-closed repository release check.
- Synchronizes the technical plan, client guide, client overview, runbooks,
  agent instructions, examples, acceptance controls, CI, and tracked PDFs.

The release does not include a live OCR/HTR vendor, calibrated local handwriting
detector, target-specific CRM connector, remote MCP transport, or a production
accuracy claim. Those require client-selected systems and representative,
authorized acceptance data.
