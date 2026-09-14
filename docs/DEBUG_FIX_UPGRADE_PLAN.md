# Debug, Fix, and Upgrade Plan

Independent review of `business-doc-ingestion` at `main` / `32a21dd`, performed
2026-08-26 in four passes, **followed by remediation of every finding**, merged
to `main` as `857baf4`. A later repository-wide consistency pass added **F35-F38**
(§4A) and completed the operating skill.

## Status

**All 44 findings and every structural item are fixed.** 26 came from the four
review passes; **F27-F28** were found while fixing them; **F29-F34** came from a
fifth pass; **F35-F38** came from a sixth, repository-wide consistency pass; **F39-F42** came from a seventh pass reviewing a real operator configuration for
a production trial; and **F43-F44** from extending OpenRouter to scanned pages. One item remains open by design: the §3.1 branch-coverage
blind spot, because mutmut 3.x cannot produce a machine-readable count
non-interactively.

| | Findings | State |
|---|---|---|
| Critical | 2 | Fixed, with regression tests |
| High | 9 | Fixed, with regression tests |
| Medium | 16 | Fixed, with regression tests |
| Low | 5 | Fixed |
| Found while fixing (F27-F28) | 2 | Fixed |
| Found in the fifth pass (F29-F34) | 6 | Fixed |
| Found in the sixth pass (F35-F38) | 4 | Fixed, with regression tests |
| Found in the seventh pass (F39-F42) | 4 | Fixed, with regression tests |
| Found extending OpenRouter (F43-F44) | 2 | Fixed, with regression tests |

Verified on `main` after the sixth pass:

| Check | Result |
|---|---|
| Test suite | **763 passed** (was 565 before the review) |
| Statement coverage | **13,876 / 13,876 (100%)** |
| Branch coverage | **5,418 / 5,418 (100%)** |
| `ruff check` / `ruff format --check` | clean, 139 files, **with the `S` (bandit) ruleset enabled** |
| Tracked document triple | Regenerated; all 19 PDF pages inspected |
| `scripts/release_check.py` | `"status": "ready"`, 0 errors |
| Standard acceptance packet | PASS |
| Operations acceptance | PASS |
| Public stress corpus | PASS |
| Real two-page document acceptance | PASS |

**Three corrections change control verdicts** and are not backward-comparable
with artifacts produced before them. See §0 before treating any earlier run as
comparable.

This document is the review record plus the remediation log. Every finding keeps
its original evidence and reproduction so the fix can be audited against the
defect it closes. Line references in the *finding* text are to `32a21dd`
(pre-fix); the **Fixed** block under each finding names what changed.

---

## 0. Corrections that change control verdicts

These corrections tighten controls that previously passed. Rerun the affected lanes on
the frozen acceptance corpora and diff the before/after artifacts before
comparing to any earlier run.

| Change | Previously | Now |
|---|---|---|
| **F7** header arithmetic tolerance | A 500-line document received a $5.00 tolerance on `subtotal_plus_charges_equals_total` | Base tolerance ($0.01). A long document that passed inside the widened tolerance can now fail |
| **F7** unread lines | Header identities tying reported `proved` and entered the sampling frame | Reports `proved_with_unproved_lines`, stays in review, leaves the sampling frame |
| **F3** consensus grouping | Greedy first match; argument order chose the accepted value and the flag | Complete-linkage over a deterministic order. Reruns are reproducible; earlier runs were argument-order dependent |
| **F22** commission rate | A 1000% effective rate classified as approved sales credit | Critical review exception above a `0.5` plausibility ceiling |
| **F24** rate units | `0.75` silently read as 75% | `allocation_rate_unit_ambiguous` review exception |

Two shipped defaults were lowered in `.env.example` because the per-minute
safety budget made the old values unreachable (**F19**):
`GOOGLE_VERTEX_AI_MAX_REQUEST_TOKENS` 200000 → 55000 and
`OPENROUTER_MAX_REQUEST_TOKENS` 80000 → 75000. A local `.env` still carrying the
old values is not rejected; the extra headroom was never reachable.

---

## 1. Verified baseline (pre-fix, at `32a21dd`)

The repository's own quality claims were re-measured, not assumed.

| Check | Command | Result |
|---|---|---|
| Test suite | `pytest -q` | **565 passed**, exit 0 |
| Statement coverage | `coverage run --branch -m pytest` | **13,043 / 13,043 (100%)** |
| Branch coverage | same run | **5,140 / 5,140 (100%)** |
| Lint | `ruff check .` | All checks passed |
| Format | `ruff format --check scripts tests` | 126 files already formatted |
| Release gate | `scripts/release_check.py` | `"status": "ready"`, 0 errors, 37 Markdown files, 4 tracked PDFs |
| Secret scan | `git ls-files \| xargs grep -E '<key patterns>'` | no matches in tracked files |
| Import graph | AST scan of `scripts/` | 67 modules, **no cycles**, no stdlib shadowing |
| Doc/code refs | AST + regex sweep of 37 Markdown files | **0** broken script paths, **0** undocumented flags |

The gate is genuinely green, and the counts match `HANDOFF.md` exactly. That is
the single most important context for this review: **every finding below survives
a fully passing strict gate.** Several of them survive it for a structural reason
established in §3.1.

The existing `docs/REPOSITORY_AUDIT.md` (August 25) is a serious piece of work
and its 30 findings are real. This review deliberately targets what that audit
did not reach. **No finding below duplicates an entry in that table.**

Pass one surveyed the repository and measured the baseline. Pass two line-audited
the control modules. Pass three verified every claim and citation. Pass four
audited modules the earlier passes had treated as structure-only — which produced
**F22**, the most severe finding in this review, and is the reason §5 recommends
a further pass rather than declaring the code covered.

---

## 2. Findings

Severity reflects impact on the repository's own stated invariants — evidence
preservation, exception completeness, independence of consensus, and the
proposal/authorization boundary — not general code quality.

### Summary

**26 findings: 2 Critical, 7 High, 13 Medium, 4 Low — all fixed.** Every one was
reproduced against `32a21dd` before being corrected. **F27** and **F28** were
found during remediation and are recorded at the end of §2.

| # | Severity | Area | Finding | Fixed in |
|---|---|---|---|---|
| **F1** | **Critical** | Evidence graph | Node collision aborts the whole build instead of emitting an exception | `evidence_graph.py` |
| **F2** | **Critical** | Completeness | GL rows with unparseable periods or amounts are silently dropped, shrinking the reported variance | `completeness.py` |
| **F21** | **High** | Corroboration | Reconciliation and graph build report clean after processing nothing | `independent_table_reconcile.py`, `evidence_graph.py` |
| **F3** | **High** | Consensus | Field clustering is order-dependent; identical readings yield 2-of-3 or 3-of-3 by argument order | `consensus.py` |
| **F4** | **High** | Client intake | Returned-workbook XML is parsed with entity expansion enabled (XML bomb) | `client_review_package.py` |
| **F5** | **High** | Independence | `independence_group` derives from the transport, so an OpenRouter-routed OpenAI model counts as independent of OpenAI | `runtime_config.py`, `consensus.py`, `handwriting_review.py` |
| **F6** | **High** | Evidence graph | Provenance `json_pointer` is fabricated for list-shaped and `results`/`records`-shaped artifacts | `evidence_graph.py` |
| **F7** | **High** | Arithmetic | Line-count-scaled tolerance is applied to header-only checks, loosening the strongest control up to 500× | `arithmetic_check.py` |
| **F22** | **High** | Allocation | A 1000% computed commission rate classifies as approved sales credit with no exception and no review | `allocation_policy.py` |
| **F23** | Medium | Allocation | `sales_credit_eligible: true` is emitted alongside `allocation_formula_not_proved`, violating the documented contract | `allocation_policy.py` |
| **F24** | Medium | Allocation | `percent()` silently infers percent-vs-decimal from `> 1`, misreading sub-1% rates by 100× | `allocation_policy.py` |
| **F25** | Medium | Scan profiling | A failed bit-depth probe yields a definitive non-degraded Branch B verdict with no note | `scan_profile.py` |
| **F8** | Medium | Arithmetic | `ARITHMETIC_DEFER_UNASSIGNED_PAGES` is documented but unreachable (dead ternary arm) | `arithmetic_check.py` |
| **F9** | Medium | Provenance | Cached provider responses are replayed into retained raw artifacts with no cache marker | `llm_runtime.py`, `openai_adapter.py`, `google_genai_adapter.py` |
| **F10** | Medium | Evidence graph | Reference detection uses naive substring matching (`"po"` matches `postal_code`) | `evidence_graph.py` |
| **F11** | Medium | Completeness | Sequence and calendar series below threshold vanish with no "not analyzed" record | `completeness.py` |
| **F12** | Medium | Attribution | Reference-export rows without a recognized key column are dropped with no ingestion register | `attribution.py` |
| **F20** | Medium | Entity resolution | Blocking buckets over 400 mentions are skipped silently — the highest-cardinality parties | `entity_resolve.py` |
| **F13** | Medium | Review reduction | LLM self-reported confidence is decisional in five lanes with inconsistent guards, contradicting a stated invariant | `client_review_llm.py`, `safe_review_consolidation.py` |
| **F14** | Medium | Throughput | The rate-limit file lock is held across `sleep()`, serializing all configured concurrency | `llm_runtime.py` |
| **F15** | Medium | Preflight | `estimate_tokens` uses chars÷4, underestimating in the unsafe direction | `llm_runtime.py` |
| **F19** | Medium | Configuration | `GOOGLE_VERTEX_AI_MAX_REQUEST_TOKENS=200000` is unreachable; the real ceiling is 55,808 | `llm_runtime.py`, `.env.example`, `references/runtime-configuration.md` |
| **F26** | Low | Document AI | `retry_raw_path` contains a validity check whose two branches are identical | `google_document_ai_adapter.py` |
| **F27** | **High** | Evidence graph | *Found while fixing F1* — duplicate edge IDs make a multi-artifact graph fail its own integrity check | `evidence_graph.py` |
| **F28** | Medium | Completeness | *Found while fixing F2* — a non-ISO period string became a month bucket and inflated the baseline | `completeness.py` |
| **F29** | **High** | Address | A country contradicting its own postal code passes clean and the postal code lands in the city field | `address_normalize.py` |
| **F30** | **High** | Address | A region in its own comma segment becomes the city; Canadian provinces were never recognized at all | `address_normalize.py` |
| **F31** | Medium | Allocation | The plausibility ceiling had no lower end — a negative commission classified clean | `allocation_policy.py` |
| **F32** | Medium | Completeness | *Regression from the F2 fix* — a blank `amount` column stopped falling through to `total` | `completeness.py` |
| **F33** | Medium | Retrieval | An export with no approved rows builds a silently empty index and reports clean | `retrieval_store.py` |
| **F34** | Low | Tooling | `render_docs.sh` reports a false document defect when ripgrep is absent | `scripts/render_docs.sh` |
| **F16** | Low | Docs | `HANDOFF.md` states committed work is "not yet committed" | `HANDOFF.md` |
| **F17** | Low | Provenance | Two of the last three `main` commits carry no PR reference | `BRANCHING.md` |
| **F18** | Low | Hygiene | 117 stale branches whose merge state cannot be determined; stray `private/tmp/*.py` working copies | `.gitignore`, `BRANCHING.md` |

---

### F1 — Critical: evidence-graph node collision aborts the build

**Location:** `scripts/evidence_graph.py:186` (`_add_node`), reached from `_adapt` → `build`.

`_add_node` raises `ValueError("graph node collision")` when the same `node_id`
arrives with different `properties`. `build()` does not catch it, so a single
conflicting record **aborts the entire graph build** and writes no graph and no
exception file.

This is not a theoretical shape. The two most obviously co-manifested artifact
types collide by construction: `consensus_records` and `validated_records` both
create `document:<id>` nodes carrying `review_status`, and the whole point of
validation is that the status changes.

**Reproduced:**

```
manifest: a1=consensus_records (d1, review_status "open")
          a2=validated_records (d1, review_status "validated")
result:   BUILD FAILED: ValueError graph node collision
```

**Why it matters:** it violates hard constraint #2 — "Emit a record or explicit
exception for every input and failed stage." An abort emits neither. The operator
sees a stack trace, not a `client_review_required` finding, and has no artifact
recording which two artifacts disagreed. A disagreement between consensus and
validation is *exactly the signal the graph exists to surface*, and it is the one
input that destroys the graph.

`build()` already handles the analogous edge case correctly: a missing edge
endpoint becomes a `graph_edge_endpoint_missing` exception rather than a crash
(`:504`). Node collision should follow it.

**Fix:** convert the collision into an exception. Retain both property sets and
both provenance entries so the disagreement *is* the evidence:

```python
exceptions.append(
    {
        "reason": "graph_node_property_conflict",
        "node_id": value["node_id"],
        "existing_properties": existing["properties"],
        "conflicting_properties": value["properties"],
        "existing_provenance": existing["provenance"],
        "conflicting_provenance": value["provenance"],
        "disposition": "client_review_required",
    }
)
```

Keep the first-seen node, mark it `protected: True`, and let `contradictions`
surface it. This requires threading `exceptions` into `_add_node`, and updating
`tests/test_evidence_graph.py:599`, which currently asserts the crash is correct
behavior.


**Fixed** in `evidence_graph.py`. `_add_node` registers a `graph_node_property_conflict` exception, marks the node `protected`, retains both property sets and both provenance entries, and the build continues.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F2 — Critical: GL rows are silently dropped from the completeness baseline

**Location:** `scripts/completeness.py:257` and `:262` (`load_gl`).

```python
m = month_of(period) or (str(period)[:7] if period else None)
if not m:
    continue  # row vanishes
out[m][vendor] += num(lower.get("amount") or lower.get("total"))  # default 0.0
```

Two silent losses on the **authoritative** side of the comparison:

1. A GL row whose period matches neither of `month_of`'s two accepted formats is
   dropped entirely. No count, no exception, no note.
2. `num(..., default=0.0)` converts an unparseable amount to zero, so the row
   survives but contributes nothing.

`gl_variance` then reports `gl_total` as the sum of whatever survived, and
`periods_compared` counts periods rather than source rows — so the loss is
invisible in the output.

**Why it matters:** this is the exact failure the function's own return value
prohibits:

> "Variance is reported, never plugged. A difference means documents are missing
> or extraction is incomplete."

Dropping GL rows *shrinks* `gl_total`, which *shrinks* the variance, which makes
the corpus look **more complete than it is**. The control silently plugs the gap
in the one direction its own rule forbids — and does so on input the client
supplied as ground truth.

Compounding it, `month_of` (`:96`) resolves
`05/03/2023` as **May** with no locale declaration and no exception. A non-US GL
export is silently re-bucketed by month rather than rejected.

**Fix (three parts):**

1. `load_gl` returns `(buckets, rejected_rows)`. Every dropped or zero-coerced row
   is retained with its raw period/amount, row index, and a reason code.
2. `gl_variance` reports `gl_rows_ingested` / `gl_rows_rejected` and refuses to
   emit `within_tolerance: True` for any period while rejected rows exist.
3. `month_of` gains an explicit `date_order` parameter
   (`iso_only` | `month_first` | `day_first`) from a new documented environment
   setting, defaulting to `iso_only`. An ambiguous date under `iso_only` becomes
   an exception, not a guess.


**Fixed** in `completeness.py`. `load_gl` returns `(buckets, rejected_rows)`; every unbucketable or unparseable row is retained with its raw content and a reason code. `gl_variance` reports `gl_rows_rejected`, sets every `within_tolerance` to `None` while any row is rejected, and the gate gains a `gl_rows_rejected` reason. The `[:7]` period fallback now requires a literal ISO `YYYY-MM`, so `Q1-2026` no longer becomes a month bucket.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F21 — High: controls report clean after processing nothing

Two independent instances of the same fail-open class, both on paths that
downstream work trusts.

**(a) `independent_table_reconcile.py`** — `:103`, `:110`, `:115`, `:120`

The module correctly `raise`s for malformed *record*-level provenance
(`:98`:
`"Document AI record lacks independent-provider provenance"`), then silently
`continue`s past malformed **tables**, **rows**, and **cells** — and past every
cell whose `source_label` has no approved registry rule. Nothing counts them.

`reconcile()` builds its `exceptions` list only from outcomes that were actually
reconciled (`:201`), so work that
never entered `candidates` produces no exception at all.

**Reproduced:** `run()` on an empty/unusable corroboration input reports
`{"reconciliations": 0, "review_items": 0}` — a clean result from a control that
corroborated nothing.

**(b) `evidence_graph.py build`**

**Reproduced:** a valid manifest whose artifact contains `{"documents": []}`
produces `nodes=0 edges=0 exceptions=0`. The resulting graph passes
`load_graph`'s full integrity check — content hash, schema, unique IDs, edge
endpoints — and every read command then answers truthfully and uselessly: no
contradictions, no candidates, no paths.

**Why it matters:** the document-path controls already get this right.
`arithmetic_check.py:386`, `attribution.py:455`, `completeness.py:534`,
`sampling.py:455`, and `consensus.py:589` all `sys.exit("No records found…")`.
The **corroboration** path and the **graph** path — the two newest subsystems,
and the two the cross-packet lane depends on — do not. A silently-empty upstream
artifact therefore yields an "independent corroboration completed" and a
"verified evidence graph" that each rest on zero records.

**Fix:**

1. `independent_table_reconcile`: count `tables_seen` / `tables_rejected`,
   `rows_seen` / `rows_rejected`, `cells_seen` / `cells_unmapped`, emit them in
   the summary, and emit an explicit exception for each rejection reason. Refuse a
   clean status when `reconciliations == 0` while handoffs were supplied.
   `cells_unmapped` is separately valuable — it is a direct schema-discovery
   signal that is currently discarded.
2. `evidence_graph build`: emit a `graph_empty_source_artifact` exception per
   artifact that contributed zero nodes, and add a `--require-nodes` flag (default
   on) that fails the build when the whole graph is empty.


**Fixed** in `independent_table_reconcile.py`, `evidence_graph.py`. Reconciliation counts table/row/cell rejections and unmapped cells, reports `coverage` and `corroborated`, and emits `independent_reconciliation_produced_no_comparison` when handoffs were supplied but nothing was compared. `evidence_graph build` emits `graph_source_artifact_contributed_no_nodes` per empty artifact and refuses an all-empty graph unless `--allow-empty-graph` is passed.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F3 — High: consensus field clustering is order-dependent

**Location:** `scripts/consensus.py:170` (greedy first-match clustering in `reconcile_field`).

`values_agree` uses an absolute `NUMERIC_TOLERANCE = 0.005`. Tolerance-based
agreement is **not transitive**, and the clustering loop assigns each engine to
the first cluster it agrees with — so the outcome depends on which engine is
processed first.

**Reproduced** — the same three readings, two orderings:

| Engine order | Flag | Accepted | Value |
|---|---|---|---|
| `100.000, 100.004, 100.008` | `consensus_2of3` | True | `100.0` |
| `100.004, 100.000, 100.008` | `consensus_3of3` | True | `100.004` |

Same evidence, different consensus strength *and* a different accepted dollar
value, decided by argument order on the command line.

**Why it matters:** `consensus_2of3` is queued for review; `consensus_3of3` is
not. Argument order therefore determines whether a human ever looks at the field.
For a control layer whose defensibility rests on reproducibility, a
CLI-argument-order-dependent accepted value is a material defect — and one that
no amount of test coverage over a fixed argument order will surface.

**Fix:** replace greedy first-match with a deterministic complete-linkage
clustering — sort candidate values, then require every member of a cluster to
agree with every other member, not merely with the cluster's first member. Where
complete-linkage fails but single-linkage succeeds, that *is* a borderline
reading and should be flagged as `no_consensus` rather than resolved by ordering.

**Related, same function:** a 2-engine handwritten numeric field reports
`consensus_flag: "consensus_2of2"` while `accepted: False` and
`rule: "handwritten_numeric_3of3"` (reproduced). `accepted` and
`queue_for_review` are correct, but the flag string reads as a pass, and it is
carried forward by `evidence_graph.py:914` and
`client_review_cross_record.py:200`. Emit
`consensus_2of2_below_required_3`, or add an explicit `meets_required_rule`
boolean beside it.


**Fixed** in `consensus.py`. `cluster_readings` groups by complete-linkage agreement over a deterministic sort, so no engine ordering changes the outcome. A reading matching two clusters withholds consensus. An agreeing set below its governing rule is labelled `consensus_<n>of<m>_below_required_<r>` instead of reading as a pass.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F4 — High: returned client workbooks are parsed with XML entity expansion

**Location:** `scripts/client_review_package.py:745-756` (`_xlsx_cell_values`).

`_validate_workbook_archive` (`:914`) is
a genuinely good ZIP preflight — member count, per-member size, total
uncompressed size, required parts. It stops exactly one layer short. The XML
inside those members is then handed to `xml.etree.ElementTree.fromstring`, which
**expands internal entities**.

**Reproduced:** a five-level entity bomb of ~500 bytes expands to 300,000
characters under this repository's own interpreter. Each additional level
multiplies by ten. A nine-level bomb is still a few hundred bytes of XML —
comfortably inside every one of the ZIP limits — and expands to gigabytes.

**Why it matters:** the returned workbook is, by the repository's own framing,
**untrusted client input** (`docs/REPOSITORY_AUDIT.md`: "returned client
workbooks are treated as untrusted input"). Both `import-decisions` and
`preserve-responses` reach this parser. The impact is memory exhaustion of the
operator's machine, not data corruption — but it is a real denial of service on
the one input path explicitly designated as untrusted, and the ZIP preflight
sitting immediately above it shows the threat was considered.

**Fix:** reject DTDs in the same preflight that already bounds the ZIP, so the
rejection shares an error surface with the existing checks:

```python
# inside _validate_workbook_archive, per XML member
head = archive.read(name)[:4096]
if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
    raise ValueError("workbook XML declares a DTD or entity")
```

A legitimate `.xlsx` from Excel, LibreOffice, or `XlsxWriter` contains neither.
(A hardened `ET.XMLParser` with `EntityDeclHandler` rejecting declarations is
equivalent but harder to audit.)

While there, fix two adjacent crashes on malformed input:
`next(s for s in workbook.find("m:sheets", ns) if …)`
(`:748`) raises an uncaught
`StopIteration` when the sheet is absent, and `archive.read(target)`
(`:752`) raises an uncaught `KeyError`
when `workbook.xml.rels` names a non-existent member. Both should be `ValueError`
with the same message style as the rest of the importer.


**Fixed** in `client_review_package.py`. `_validate_workbook_archive` scans every XML member and rejects `<!DOCTYPE`/`<!ENTITY` before any parsing. Malformed relationship targets, missing worksheet parts, absent `<sheets>`, and unknown shared-string indexes now raise the importer's normal `ValueError` instead of `KeyError`, `StopIteration`, or `TypeError`.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F5 — High: independence groups derive from the transport, not the model vendor

**Location:** `scripts/handwriting_review.py:54` (`provider_group`), `scripts/consensus.py:481`, and every lane's `primary != buddy` check.

Every independence check in the repository reduces to comparing two provider
strings drawn from `{"openai", "google", "openrouter"}`:

- `consensus.py:482` — `if group in seen_groups: raise`
- `client_review_iterative.py:148` — `if primary_provider == buddy_provider: raise`
- `schema_discovery.py:950`, `client_review_cross_packet.py`, and the exception
  resolution lane — same shape.

OpenRouter is a **router**, not a model vendor. `OPENROUTER_MODEL` is free-form
(`.env.example:110` ships `nvidia/nemotron-3-super-120b-a12b:free`, but nothing
constrains it). Setting `OPENROUTER_MODEL=openai/gpt-5.6-terra` alongside an
OpenAI lane running `gpt-5.6-terra` produces two handoffs whose
`independence_group` values are `"openrouter"` and `"openai"` — which every check
accepts as independent — while both are the same weights answering the same
prompt.

`provider_group("openrouter/openai/gpt-5.6")` returns `"openrouter"`
(reproduced; also asserted by `tests/test_precision_controls.py:112`).

**Why it matters:** this is the invariant the repository states most forcefully,
in `SKILL.md`, `AGENTS.md`, `README.md`, and half the reference files:
"Same-model roles and provider confidence are not independent proof." The
enforcement mechanism cannot see through the router that the configuration
explicitly supports — and it is enforced at *consumption* time, so an operator
discovers the problem only after paying for both lanes.

**Fix:**

1. Derive independence from a **model-vendor** identity, not the transport. Parse
   the OpenRouter slug's vendor prefix (`openai/`, `google/`, `anthropic/`,
   `meta-llama/`, `nvidia/`, …) and normalize
   `independence_group` to that vendor.
2. Reject at handoff-load time two lanes whose resolved model-vendor matches, even
   when the transport differs.
3. **Fail closed** on an unrecognized slug shape: treat the group as unknown and
   refuse to count it toward independence, rather than defaulting to the router
   name.
4. Add a **configuration preflight** so `lane_configuration("consensus_primary")`
   and `("consensus_secondary")` are compared *before* extraction runs, not after.
5. Document the rule in `references/runtime-configuration.md` beside
   `OPENROUTER_MODEL`, which today says nothing about it.


**Fixed** in `runtime_config.py`, `consensus.py`, `handwriting_review.py`. New `model_vendor()` resolves the vendor behind a router. `consensus.load_records` rejects two lanes resolving to the same vendor and refuses a routed slug with no resolvable vendor. `provider_group` resolves routed engines the same way. The resolved vendor is retained in the handoff identity.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F6 — High: fabricated provenance pointers for two artifact shapes

**Location:** `scripts/evidence_graph.py:214-216`.

```python
"json_pointer": item.get(
    "_graph_json_pointer",
    f"/{'documents' if 'documents' in data else 'candidates' if 'candidates' in data else 'decisions'}/{index}",
)
```

`_records()` (`:119`) accepts six container
shapes — a top-level list, `documents`, `results`, `records`, `candidates`,
`decisions`. The pointer fallback knows three, and its `'documents' in data` test
performs a **membership check on a list** when the artifact is a top-level array.

**Reproduced:** a `consensus_records` artifact supplied as a top-level JSON array
produced every node's provenance as `"/decisions/0"`. The correct pointer is
`"/0"`. An artifact using the `results` or `records` key produces the same wrong
`/decisions/N`.

**Why it matters:** `json_pointer` is the graph's provenance primitive — it is how
a reviewer walks from a node back to the exact source record. A pointer that does
not resolve in the cited artifact is worse than no pointer, because it looks
authoritative and will be cited as such. The module elsewhere takes pointer
correctness very seriously — `_pointer_token`
(`:656`) correctly RFC-6901-escapes `~` and `/` —
which makes this fallback an outlier rather than a considered trade-off.

**Fix:** have `_records()` return `(records, container_pointer_prefix)` so the
pointer is derived by the same code that chose the container; a top-level list
yields `""`. Add a build-time assertion that every emitted pointer resolves
against the parsed artifact — cheap, and it converts this whole class of bug into
a test failure.


**Fixed** in `evidence_graph.py`. `_records` returns the container's JSON Pointer prefix alongside the records, so a top-level array cites `/0` and a `results` container cites `/results/0`.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F7 — High: line-count tolerance is applied to header-only arithmetic checks

**Location:** `scripts/arithmetic_check.py:192`, applied at `:232` and `:266`.

```python
rollup_tol = tol + PER_LINE_ROUNDING_ALLOWANCE * max(line_count - 1, 0)
```

The rationale is sound for `lines_sum_to_subtotal`: summing N independently
rounded line extensions accumulates up to N×$0.01 of rounding. It is then reused
for two checks where it has no basis:

- **`subtotal_plus_charges_equals_total`** compares five *header* scalars
  (`subtotal + tax + freight + accessorials − discount` vs `total`). No line
  rounding participates. Its tolerance should be `tol`.
- **`accessorial_lines_sum_to_total`** sums *accessorial* rows, so if it scales
  with anything it should be `len(accessorials)`, not `len(lines)`.

**Why it matters:** a 500-line commission statement gets a **$5.00** tolerance on
its header total — 500× the configured `DEFAULT_TOLERANCE` of $0.01. The module
docstring calls `subtotal_plus_charges_equals_total` "the single most effective
check in the pipeline"; on exactly the documents where it matters most, a genuine
misread in the dollars column passes.

**Fix:** use `tol` for `subtotal_plus_charges_equals_total`; use
`tol + PER_LINE_ROUNDING_ALLOWANCE * max(len(acc_lines) - 1, 0)` for the
accessorial rollup. `tolerance_used` is already emitted per check, so the change
is visible in the artifact and diffable across a rerun.

**Related, same module:** `check_lines` (`:87`) increments `computed_subtotal` at
`:124` only
for lines that have an `extended_amount`, but still compares that partial sum
against the stated subtotal. `lines_missing_fields` records the count but never
influences `arithmetic_status`. A document whose lines are all unreadable but
whose header happens to tie is reported `"proved"` with the disposition
"Passes self-proof. Eligible for the sampled population." Make `missing_lines > 0`
force `queue_for_review` at minimum, and block `proved` when
`lines_sum_to_subtotal` was the passing check.


**Fixed** in `arithmetic_check.py`. New `rollup_tolerance(tol, summed_row_count)`. `subtotal_plus_charges_equals_total` uses the base tolerance; the accessorial rollup scales on accessorial rows; only `lines_sum_to_subtotal` keeps the line-count allowance. `missing_lines > 0` now yields `proved_with_unproved_lines`, which leaves the sampling frame and stays in review.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F22 — High: no upper bound on the computed effective commission rate

**Location:** `scripts/allocation_policy.py:285` and `:302-312` (`classify`).

```python
effective = commission / base
...
if explicit and policy.get("explicit_ship_to_precedence") is True: ...
elif effective <= threshold:                     # administrative
elif policy.get("allow_sales_generated") is True:  # sales credit
```

The policy schema constrains the *threshold* to a sane range
(`"effective_rate_threshold": {"minimum": 0, "maximum": 0.1}` at `:56`). The
*computed* rate has no bound at all. Anything above the threshold falls straight
into the `allow_sales_generated` arm.

**Reproduced** — `commission_amount 5000.00` on `commissionable_amount 500.00`:

```
effective_commission_rate : 10.0000        (1000%)
classification            : sales_generated
sales_credit_eligible     : True
client_review_required    : False
exception emitted         : None
```

A 100×-misplaced decimal point in the commission column produces **approved sales
credit with no exception, no review flag, and no note**. `base <= 0` is rejected
at `:282`, so the author clearly considered degenerate inputs — the check simply
does not extend to the other end of the range.

**Why it matters:** this is a financial fact passing clean. Every other control in
the repository routes an implausible value to review; this one classifies it as
approved. `arithmetic_check.py` would not catch it either, because
`check_document` returns `not_applicable` for `commission_statement` /
`commission_report` document types (`:165`) and explicitly defers formula
proof to this lane.

**Fix:** add a plausibility ceiling before classification and route anything above
it to review, never to `sales_generated`:

```python
ceiling = Decimal(str(policy.get("effective_rate_ceiling", "0.5")))
if effective > ceiling:
    output["reason"] = "allocation_effective_rate_exceeds_plausibility_ceiling"
    return output, review(leg, "allocation.rate", output["reason"], "critical")
```

Add `effective_rate_ceiling` to the policy JSON schema with a documented default,
and state the rule in `references/allocation-policy.md`, which today documents
`effective_commission_rate = commission_amount / commissionable_amount` (`:36`)
with no range constraint.


**Fixed** in `allocation_policy.py`. A plausibility ceiling (default `0.5`, max `1`) is checked before classification; an effective rate above it returns `allocation_effective_rate_exceeds_plausibility_ceiling` as a critical exception. An out-of-range configured ceiling fails closed.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F23 — Medium: `sales_credit_eligible` contradicts its own documented contract

**Location:** `scripts/allocation_policy.py:313-325` (`classify`).

The function sets the classification block first and checks the formula proof
afterwards:

```python
output.update(
    {
        "classification": classification,
        "sales_credit_eligible": classification == "sales_generated",
        "client_review_required": False,
        "reason": reason,
    }
)
if "formula_delta" in output["derived"] and Decimal(output["derived"]["formula_delta"]) != 0:
    output["client_review_required"] = True
    output["reason"] = "allocation_formula_not_proved"
    return output, review(leg, "allocation.formula", output["reason"], "critical")
```

The failure path flips `client_review_required` back to `True` but leaves
`classification` and `sales_credit_eligible` at their optimistic values.

**Reproduced** — `base 1000.00`, `commission 50.00`, `stated_rate 5`,
`allocation_share 0.5` (expected 25.00, delta 25.00):

```
classification         : sales_generated
sales_credit_eligible  : True
client_review_required : True
reason                 : allocation_formula_not_proved
formula_delta          : 25.00
```

**Why it matters:** `references/derived-field-schema.md:146` defines the field as
`true` for **approved** sales-generated work, null when unresolved. Here it is
`true` for work the same record declares unproved. This is a direct
code-versus-contract violation of exactly the kind
`references/artifact-contracts.md` exists to prevent, and a downstream consumer
filtering on `sales_credit_eligible` — the field the schema designates as the
eligibility answer — picks up unproved commission credit. A consumer filtering on
`client_review_required` is safe; nothing in the contract says which to prefer.

**Fix:** set `sales_credit_eligible = None` and
`classification = "review_required"` on the failure path, matching the schema's
"null when unresolved." Add a contract test asserting the invariant directly:
`sales_credit_eligible is not True whenever client_review_required is True`.


**Fixed** in `allocation_policy.py`. A failed formula proof retracts the classification to `review_required` with `sales_credit_eligible: None`, matching the derived-field contract.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F24 — Medium: percent-versus-decimal is inferred silently from the value

**Location:** `scripts/allocation_policy.py:92-97` (`percent`), reached from `classify`.

```python
def percent(value):
    """Interpret a displayed percentage or decimal rate as a decimal fraction."""
    parsed = number(value)
    ...
    return parsed / 100 if parsed > 1 else parsed
```

The unit is guessed from magnitude. **Reproduced:**

| Input | Returned | Reading |
|---|---|---|
| `"5"` | `0.05` | 5% — correct |
| `"100"` | `1` | 100% — correct |
| `"0.75"` | `0.75` | **75%** — a 0.75% override commission read 100× high |
| `"0.5"` | `0.5` | **50%** — a 0.5% rate read 100× high |
| `"1"` | `1` | 100% — or 1%; genuinely ambiguous, resolved silently |

**Why it matters:** sub-1% override and split commissions are ordinary in this
document family, and they are exactly the values the heuristic gets wrong. The
module's own reviewer instructions (`:65`) insist that "a nominal rate, allocation
share, effective rate, commission amount, and sales credit are different facts" —
but the parser conflates two different *units* with no exception.

The blast radius is partly contained: `stated` and `share` feed only
`formula_expected_commission_amount`, so a misread usually produces a nonzero
`formula_delta` and routes to review (the safe direction). It is not contained
when both values are misread consistently, or when only one of them is supplied.
And `value == 1` is truly ambiguous with no way to tell.

**Fix:** stop inferring. Take the unit from the source label or the approved
template rule — the registry already carries per-template mappings — and emit
`allocation_rate_unit_ambiguous` as a review exception when the unit cannot be
established from evidence. At minimum, flag `0 < value <= 1` as ambiguous rather
than silently choosing the decimal reading.


**Fixed** in `allocation_policy.py`. `percent()` no longer infers the unit from magnitude. A value above 1 is a percentage, `0` is unambiguous, and anything else returns `AMBIGUOUS_RATE`, which `classify` registers as `allocation_rate_unit_ambiguous` review work.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F8 — Medium: a documented environment setting is unreachable

**Location:** `scripts/arithmetic_check.py:366-370`.

```python
ap.add_argument("--defer-unassigned-pages", action="store_true")
...
defer_unassigned_pages = (
    env_bool("ARITHMETIC_DEFER_UNASSIGNED_PAGES", False)
    if args.defer_unassigned_pages is None  # never None: store_true gives False
    else args.defer_unassigned_pages
)
```

`action="store_true"` yields `False`, never `None`, so the `env_bool` arm is dead
and `ARITHMETIC_DEFER_UNASSIGNED_PAGES` has no effect.

**Reproduced:** with `ARITHMETIC_DEFER_UNASSIGNED_PAGES=true` in the environment
and no CLI flag, an unassigned page returns `arithmetic_status: "not_provable"`.
The documented behavior is `"deferred_reassembly"`.

The setting is documented at `references/runtime-configuration.md:53` and shipped
at `.env.example:139`, and `release_check.py` validates that documentation matches
`.env.example` — so **the release gate confirms the documentation is consistent
while the behavior is absent.** An AST sweep confirmed this is the only instance
of the pattern in `scripts/`.

**Fix:** `ap.add_argument("--defer-unassigned-pages", action="store_true", default=None)`,
which is the shape the surrounding code already assumes. Add a test that sets the
environment variable and asserts `deferred_reassembly`.


**Fixed** in `arithmetic_check.py`. `--defer-unassigned-pages` gains `default=None`, so `ARITHMETIC_DEFER_UNASSIGNED_PAGES` is reachable.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F9 — Medium: cached provider responses are replayed without a cache marker

**Location:** `scripts/openai_adapter.py:945-970`, and the parallel path at `google_genai_adapter.py:445`.

```python
cached = cache.read(key)
if cached:
    parsed, payload = cached["parsed"], cached["payload"]
...
if cached:
    write_raw(raw_path, request, response=payload)  # identical shape to a live call
```

`write_raw` (`:855`) emits
`{"request": …, "response": …}` whether the response came from the provider or
from `.llm-cache`. Nothing in the retained artifact distinguishes them.

**Why it matters:** "raw responses are retained" is load-bearing across the whole
repository, and `AGENTS.md` requires reruns of affected controls after an
authorized amendment. If the amendment did not change the page hash, model,
prompt, or input mode, the "rerun" is a cache replay — and the retained raw
artifact asserts a provider call that did not occur. The cache is
content-addressed, so this is not *wrong*; it is not *auditable*, and
auditability is the artifact's entire purpose. The cache is also **on by default**
(`LLM_CACHE_DIR=.llm-cache`), and the working tree currently holds 4,499 cached
responses.

**Fix (small, high value):** add to the retained `request` block:

```python
"cache_hit": bool(cached),
"cache_key": key,
"cache_dir": str(cache.directory) if cache.directory else None,
"cache_written_at": cached.get("written_at") if cached else None,
```

and stamp `written_at` in `ResponseCache.write`. Update
`references/runtime-configuration.md:141` to state plainly that a cache hit
replays a retained response and that a genuinely fresh provider call requires an
empty `LLM_CACHE_DIR`. Consider defaulting the cache off for the
`consensus_primary` / `consensus_secondary` lanes specifically, where "an
independent second reading" is the whole point.


**Fixed** in `llm_runtime.py`, `openai_adapter.py`, `google_genai_adapter.py`. `ResponseCache.write` stamps `written_at`; both adapters record `cache_hit`, `cache_key`, `cache_dir`, and `cache_written_at` in the retained request block.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F10 — Medium: reference detection matches on naive substrings

**Location:** `scripts/evidence_graph.py:273-276`.

```python
if any(term in field.casefold()
       for term in ("po", "ack", "job", "project", "payment", "check", "remit")):
```

`"po"` and `"ack"` are two- and three-character substrings tested against
arbitrary field names.

**Reproduced** — a record with fields `shipping_postal_code`, `package_tracking`,
and `report_date` produced three `reference:` nodes and three
`document_references` edges:

```
reference:90210        <- shipping_postal_code   ("po")
reference:TRK1         <- package_tracking       ("ack")
reference:2026-01-01   <- report_date            ("po")
```

Other everyday collisions: `point_of_contact`, `shipping_point`, `position`,
`purpose`, `component`, `export`, `support`, `backorder`, `feedback`,
`check_digit`, `checked_by`.

**Why it matters:** spurious `reference` nodes pollute `path`, `candidates`, and
`contradictions` — the exact commands the cross-packet lane uses to choose which
neighborhoods to inspect, which means wasted provider spend on fabricated links.
It also interacts with **F1**: two documents whose `postal_code` and
`payment_ref` share a value collide on `reference:<value>` with different `field`
properties and abort the build.

**Fix:** the module already has the right machinery one screen away.
`_discovery_topics` (`:661`) normalizes with
`re.sub(r"[^a-z0-9]+", "_", …)` and matches a curated `DISCOVERY_TOPIC_TERMS` map.
Do the same here: normalize to underscore-delimited tokens, then match **whole
tokens** against an explicit set (`po`, `po_number`, `ack`, `ack_number`, `job`,
`job_number`, `project`, `payment`, `check_number`, `remittance`, …). Add every
collision above as a negative test.


**Fixed** in `evidence_graph.py`. `_reference_field` matches whole tokens against an explicit `REFERENCE_FIELD_TOKENS` set, so `postal_code`, `package_tracking`, `report_date`, and `backorder` no longer become references.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F11 — Medium: below-threshold completeness series vanish without a record

**Location:** `scripts/completeness.py:127`, `:133`, `:193`, `:202`.

Four silent `continue`s in the two inference functions:

- `if n is None: continue` — an invoice number with no trailing digits is excluded
  from sequence analysis.
- `if len(entries) < MIN_FOR_SEQUENCE: continue` — a vendor with 4 invoices is
  never analyzed.
- `if not m: continue` — a document whose date does not parse is excluded from
  calendar analysis.
- `if len(keys) < MIN_MONTHS_FOR_CALENDAR: continue` — a vendor with 3 active
  months is never analyzed.

None emits a record. The completeness report says nothing about the population it
did not examine, so **"no sequence gaps found" is indistinguishable from "no
sequence analysis was possible."**

Compounding it, `by_series` keys on `(vendor, prefix, width)`
(`:129`), so a vendor rolling over from `INV-999`
to `INV-1000` splits into two series — each of which may then fall below
`MIN_FOR_SEQUENCE` and disappear. A numbering rollover is exactly the boundary at
which a scan batch is likely to be missing.

**Fix:** emit a `coverage` block alongside `sequence_gaps` / `calendar_gaps`
listing every vendor/series not analyzed and why, with document counts and total
value. Group series by `(vendor, prefix)` and reconcile zero-padding widths within
the group rather than treating width as an identity component.
`missing_runs[:50]` (`:172`) should also record
`runs_truncated: N`.


**Fixed** in `completeness.py`. `sequence_gaps` and `calendar_gaps` accumulate an `inference_coverage` register naming every series and vendor not analyzed and why. Series group by `(vendor, prefix)` rather than zero-padding width, so a `999`→`1000` rollover stays one series. `missing_runs_truncated` records list truncation. The unanalyzed population is stated as a limitation in `findings`.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F12 — Medium: reference-export rows are dropped with no ingestion register

**Location:** `scripts/attribution.py:162`, in `load_reference`.

```python
key = (
    low.get("ack_number")
    or low.get("ack")
    or low.get("job_number")
    or low.get("job")
    or low.get("number")
)
if not key:
    continue
```

`attribution.py`'s document-side contract is exemplary — every unattributed
document reaches the `unattributable` register with a reason code and a dollar
value, and the module docstring is explicit that
`"97% attributed" fails unless the other 3%` is enumerated
(`:21`). The **reference-side** input has no
equivalent. A client ACK export using `acknowledgement_no` or `job_id` loses those
rows silently, attribution rates collapse, and nothing in the output explains why
— the operator sees a low attribution rate and no cause.

**This is the same shape as F2**, and together they name the structural gap
developed in §3.3:

> Document-side inputs have complete exception registers. Client-supplied
> side-channel inputs — GL exports, payment files, reference exports — do not.

**Fix:** a shared `load_side_channel(path, schema, *, on_reject)` helper used by
`attribution.load_reference`, `completeness.load_gl`, and the payments loader,
returning `(rows, rejected)` with every caller writing `rejected` into its
artifact. Then extend the gate: a completeness or attribution run with a non-empty
rejection register cannot report a clear status.


**Fixed** in `attribution.py`. `load_reference` registers a row with no recognized key column, and the summary reports `reference_rows_ingested`, `reference_rows_rejected`, and `rejected_reference_rows`.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F20 — Medium: entity resolution silently skips its largest buckets

**Location:** `scripts/entity_resolve.py:318`.

```python
for idxs in buckets.values():
    if len(idxs) > 400:
        # Pathological block (usually a placeholder name). Comparing it
        # exhaustively is both slow and meaningless.
        continue
```

`resolve()` returns `(groups, evidence, ambiguous)` — none of which records the
skip. The comment's "usually a placeholder name" is a guess about the data, not a
property of it.

**Why it matters:** the cost is O(n²) pair comparisons, so the cap is
understandable. But the excluded population is precisely the **highest-cardinality
party names** — a major supplier appearing on 500+ documents is the single most
valuable entity to resolve correctly, and it is the one guaranteed to be skipped.
The result is silently absent from `evidence` and `ambiguous`, so downstream
attribution and completeness treat the un-merged variants as distinct vendors,
which in turn fragments `by_series` in `sequence_gaps` (**F11**).

**Fix:** two changes, neither large.

1. Record the skip: append to `ambiguous` (or a new `deferred` list) one entry per
   skipped bucket with its blocking key, mention count, and
   `reason: "bucket_exceeds_pairwise_comparison_cap"`, disposition
   `client_review_required`.
2. Replace the hard skip with a bounded strategy for large buckets — sort by
   normalized name and compare within a sliding window, or add a second blocking
   pass on postal code — so large buckets are *partially* resolved rather than
   wholly abandoned. Make the cap a documented environment setting.


**Fixed** in `entity_resolve.py`. An over-cap blocking key is appended to `ambiguous` with its key, mention count, and reason instead of being skipped silently. The cap is the named `MAX_BLOCK_PAIRWISE` constant.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F25 — Medium: a failed bit-depth probe yields a confident scan verdict

**Location:** `scripts/scan_profile.py:238-241` (`profile_page`) and `:300`/`:319-322` (`classify`).

`profile_page` is deliberately total — "Returns a dict; never raises on a bad
page" — and swallows each metadata probe into a default:

```python
try:
    rec["bit_depth"] = int(xobj.get("/BitsPerComponent", 0)) or None
except Exception:
    pass
```

`classify` then reads `depth`:

```python
elif depth and depth >= 8:          # None falls through
    ...
    if chroma is None:
        ...
        notes.append("chroma_unverified")     # <- the right pattern
else:
    rec["bucket"] = "G"
    rec["branch"] = "B"
    rec["degraded"] = False                   # <- no note
```

A page whose bit-depth probe failed is therefore classified as **clean grayscale,
Branch B, not degraded**, indistinguishable in the artifact from a page that was
successfully measured at 2 or 4 bits.

**Why it matters:** scan profiling is Phase 0.5 and drives branch routing for the
whole corpus. The module already solves this exact problem correctly one branch
above — `chroma is None` appends `chroma_unverified`, and the accompanying
comment states the principle precisely:

> "Could not read pixels. Trust the declared colourspace but flag it, because an
> unverified Branch A assignment is exactly the mistake false-colour detection
> exists to prevent."

The identical reasoning applies to an unverified depth, and is not applied. The
`rec["notes"]` channel exists, is already used for `no_raster_image`,
`jbig2_compressed`, `false_colour`, and `chroma_unverified`, and costs one line.

**Fix:** append a note in each `except` (`probe_failed:bit_depth`,
`probe_failed:colorspace`, `probe_failed:has_text_layer`, `probe_failed:mediabox`)
and add `depth_unverified` in the `classify` fallthrough when `depth is None`.
Note that `has_text_layer` defaults to `False` on probe failure, which routes the
page to image extraction rather than text — the more expensive direction, but
still a silent routing decision.

`profile_pdf` handles the analogous case correctly at `:337`: a PDF that cannot be
opened returns an explicit `open_failed:<ExceptionType>` note with `bucket: None`.
Per-page probes should match that standard.


**Fixed** in `scan_profile.py`. Each unread probe is named in a new `probe_failures` field, and `classify` appends `depth_unverified` when it assigns a bucket without a measured bit depth — matching the adjacent `chroma_unverified` handling. `notes` semantics are unchanged for existing consumers.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F13 — Medium: model self-reported confidence is decisional in five lanes

**Locations:**

| Site | What the confidence term decides | Additional guards |
|---|---|---|
| `client_review_llm.py:506` | `llm_auto_accept_status: "auto_accepted"` | `not protected`, valid field-bound proposal |
| `safe_review_consolidation.py:187` | `client_review_required: False` | `not protected`, valid proposal |
| `client_review_cross_record.py:1240` | `auto_accept_status: "auto_accepted"` | type / bool / finite / range checks, **plus retained-evidence resolution** |
| `schema_discovery.py:845, 848, 879` | `inferred_high_confidence` | floor **0.99**, buddy `confirmed`, `source_evidenced(template)` |
| `llm_adjudication.py:395` | `propose_amendment` is emitted | `decision == "propose_amendment"` |

In every case the number is `model_decision.get("confidence")` — **the model's own
self-report**, not a measured quantity:

```python
item["reviewer_confidence"] = model_decision.get("confidence")
...
can_auto_accept = (
    auto_accept
    and not protected(item)
    and model_decision.get("decision") == "propose_resolution"
    and valid_proposed_update(proposal, item)
    and isinstance(confidence, (int, float))
    and confidence >= auto_accept_threshold
)
```

`SKILL.md` and `AGENTS.md` state: *"Provider confidence is non-decisional
provenance"* and *"self-reported confidence [is] not independent proof."* In these
five places it is decisional — it is a term without which the outcome changes.

**The mitigations are real and should be stated.** `protection_reasons()` fails
closed on unknown reasons and `SAFE_REASON_CODES` is a **nine-entry** allowlist of
low-stakes reasons (document-type proposals, formatting review).
`valid_proposed_update` binds the proposal to the reviewed field. Nothing
downstream treats any of these as authorized. The blast radius is genuinely small.

**But the guards are inconsistent, and the inconsistency is the finding.**
`schema_discovery` is well defended — a 0.99 floor, independent buddy
confirmation, and retained source evidence — though note that `source_evidenced`
(`:630`) only verifies that `document_id` and `page_id` *strings are present*, not
that they support the mapping. `client_review_cross_record` is next best: it
validates the confidence value's type, finiteness, and range before comparing it,
then requires the cited evidence to resolve. The two review-reduction paths are
the weakest — `safe_review_consolidation.carry_forward_items` accepts any numeric
`>= threshold` and stamps `client_review_required: False` on that basis.

Two acceptable resolutions — pick one; do not leave both statements standing:

- **Preferred:** drop the confidence term from the two review-reduction paths.
  `not protected(item)` + `decision == "propose_resolution"` +
  `valid_proposed_update(…)` already carry the decision. Keep
  `reviewer_confidence` in the artifact as provenance, which is exactly what the
  invariant describes. Adopt `client_review_cross_record`'s validation
  (type/finite/range/evidence) as the shared floor wherever a threshold is kept.
- **Alternative:** narrow the doc language to "provider confidence is
  non-decisional for any protected category, and may threshold only items whose
  reason appears in `SAFE_REASON_CODES`," cross-reference the allowlist from
  `references/automation-controls.md`, and enumerate the five sites so the
  exception surface is auditable rather than discovered by grep.


**Fixed** in `client_review_llm.py`, `safe_review_consolidation.py`. Self-reported confidence is removed as a decision term in both review-reduction lanes. It is still retained on the item as provenance, which is what the invariant describes. The decision now rests on the fail-closed protection taxonomy, an explicit reviewer decision, and a field-bound proposal citing retained evidence.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F14 — Medium: the rate-limit lock is held across the sleep

**Location:** `scripts/llm_runtime.py:274-293` (`RequestThrottle.acquire`).

```python
fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
...
delay = max(0.0, max(intervals) - (now - last))
if delay:
    self.sleep(delay)  # <- still holding LOCK_EX
...
fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
```

Every worker thread and every concurrent process blocks on `flock` for the full
duration of another worker's back-off. Whenever any configured interval is
non-zero, `LLM_MAX_WORKERS` / `OPENAI_MAX_WORKERS=8` yields **no parallelism at
all** — requests execute strictly one at a time.

**Measured from the shipped `.env.example` defaults** (Vertex: 80,000 TPM,
safety ratio 0.8, 8,192-token reserve):

| Prompt tokens | Computed spacing interval |
|---|---|
| 10,000 | 17.1 s |
| 40,000 | 45.2 s |
| 55,000 | 59.2 s |

Eight workers serialize behind one 45-second lock. OpenAI's shipped defaults
(2,000,000 TPM) produce 0.5–2.2 s intervals, so the effect is Vertex-specific and
severe there.

**Fix:** reserve the slot under the lock, release, then sleep:

```python
with lock_path.open("a+") as stream:
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    now, last = time.time(), _read(stream)
    start_at = max(now, last + max(intervals))
    _write(stream, start_at)  # reserve the slot
    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
self.sleep(max(0.0, start_at - time.time()))
```

This preserves the global spacing guarantee — the timestamp now records the
*reserved* slot rather than the last completed one — while letting N workers
occupy N consecutive slots concurrently. It also fixes a related inaccuracy: the
current code writes `now` *after* sleeping, so a slow request's spacing is
measured from the wrong instant.

Note `fcntl` is POSIX-only; the Windows path
(`:270`) returns `0.0` — **no throttling at all**.
That is pragma-documented in the source but absent from
`references/runtime-configuration.md`, and it should be stated there.


**Fixed** in `llm_runtime.py`. `acquire` reserves the slot under the lock, writes the reserved instant, releases the lock, and only then sleeps. Configured concurrency is no longer serialized, and spacing is measured from the slot rather than from when a slow request finished.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F15 — Medium: token preflight underestimates in the unsafe direction

**Location:** `scripts/llm_runtime.py:320-327`.

```python
return max(1, (len(text) + 3) // 4)
```

Characters ÷ 4 is a reasonable average for English prose. It is wrong by 2–4× for
the content this pipeline actually handles: CJK text (~1 token per character),
dense JSON punctuation, base64 fragments, and long identifier strings.

The docstring says "conservatively," but the error runs in the **permissive**
direction — an underestimate lets an oversized request through
`max_request_tokens`, the throttle's safety check, and the adaptive batch packer,
and the provider rejects it. That converts a preflight-catchable configuration
error into a retained provider exception requiring a no-clobber recovery run — the
most expensive possible outcome in this design, and one `AGENTS.md` devotes
several paragraphs to handling.

**Fix:** estimate on UTF-8 bytes with a wide-character correction:

```python
data = text.encode("utf-8")
wide = sum(1 for ch in text if ord(ch) > 0x2E80)
return max(1, (len(data) + 3) // 4 + wide)
```

Whatever the formula, add a documented `LLM_TOKEN_ESTIMATE_SAFETY_FACTOR`
(default ≥ 1.15) so an operator can tighten it without a source edit — the pattern
`references/runtime-configuration.md` already uses for the Gemini output reserve.


**Fixed** in `llm_runtime.py`. `estimate_tokens` counts UTF-8 bytes with a wide-character correction and applies a documented `LLM_TOKEN_ESTIMATE_SAFETY_FACTOR` (default 1.15, range 1.0–4.0), so the preflight errs high.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F19 — Medium: `GOOGLE_VERTEX_AI_MAX_REQUEST_TOKENS` is unreachable

**Location:** `scripts/llm_runtime.py:257-269` (`RequestThrottle.acquire`) and `:243-248` (`__init__` validation).

`acquire` applies two independent ceilings:

```python
if self.max_request_tokens and estimated > self.max_request_tokens:
    raise
if safe_tokens and estimated > safe_tokens:
    raise
```

where `safe_tokens = tokens_per_minute * safety_ratio`.

**Measured from the shipped defaults:**

| Setting | Value |
|---|---|
| `GOOGLE_VERTEX_AI_MAX_REQUEST_TOKENS` | 200,000 |
| `GOOGLE_VERTEX_AI_RATE_LIMIT_TOKENS_PER_MINUTE` | 80,000 |
| `LLM_RATE_LIMIT_SAFETY_RATIO` | 0.8 |
| `GOOGLE_VERTEX_AI_OUTPUT_TOKEN_RESERVE` | 8,192 |
| **Effective maximum prompt tokens** | **55,808** |

The documented 200,000 setting can never bind. A request between 55,808 and
200,000 prompt tokens raises `RateLimitConfigurationError` citing the *safe token
budget* — a number the operator never configured and cannot find in
`.env.example` — rather than the setting they did configure.

`__init__` already validates one cross-setting invariant
(`max_request_tokens + output_reserve > context_tokens`) but not the one that
actually binds. This matters because `HANDOFF.md` explicitly discusses tuning
these values and warns against raising packet limits to compensate for
shared-capacity failures — an operator following that guidance will change a
setting that does nothing.

**Fix:**

1. Validate the real constraint in `__init__` and raise at construction:
   `max_request_tokens + output_reserve > tokens_per_minute * safety_ratio`.
2. Make the `acquire` error name **both** ceilings and which one bound.
3. Add a row to `references/runtime-configuration.md` stating that the effective
   per-request ceiling is
   `min(max_request_tokens, tokens_per_minute × safety_ratio) − output_reserve`,
   with the Vertex worked example above.


**Fixed** in `llm_runtime.py`, `.env.example`, `references/runtime-configuration.md`. New `effective_prompt_ceiling()`; the rejection names both bounds and the ceiling that governs. The shipped Vertex and OpenRouter ceilings are lowered to reachable values, and the formula is documented. A construction-time hard error was tried and reverted — it would have refused many valid small-request configurations for a clarity defect.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F26 — Low: a retry-path validity check whose branches are identical

**Location:** `scripts/google_document_ai_adapter.py:370-382` (`retry_raw_path`).

```python
if not base.exists():
    return base
try:
    json.loads(base.read_text())
except (OSError, UnicodeDecodeError, json.JSONDecodeError):
    pass
suffix = 1
while True:
    ...
```

The parse result is discarded and both the success and failure paths fall through
to the same suffix loop. The check has no effect on the returned path.

The apparent intent — "a complete existing file means this is a genuine retry; a
truncated one means the previous attempt was interrupted" — is never acted on.
Behavior is safe either way (a new path is always chosen, so nothing is
clobbered), but the code reads as a control and is not one, and the docstring
("Choose a new raw path when an interrupted attempt left a partial file") claims a
distinction the code does not make.

Ruff does not flag this: the call has a side effect (file read), so it is not an
unused expression. It is the kind of thing only a reader catches.

**Fix:** either delete the try/except and simplify the docstring, or act on it —
distinguish `__retry` from `__partial` in the chosen filename so the retained raw
directory records which attempts were interrupted.


**Fixed** in `google_document_ai_adapter.py`. The parse whose two branches were identical is removed.

---

### F16 — Low: `HANDOFF.md` describes committed work as uncommitted

`HANDOFF.md:27`: "These changes are not yet committed."
They are — `32a21dd` (2026-08-26), on `main`, working tree clean.

`HANDOFF.md:267`: "After this correction branch passes protected
PR CI and reaches `main`…" — `4dee763` and `34ec01f` are already on `main`. The
frontmatter `next_external_event: merge-run-safety-then-bounded-retry-overlay`
describes a merge that has happened.

`HANDOFF.md` is explicitly scoped to "current state only" and is the first file an
incoming agent reads, which makes staleness here more costly than elsewhere.

**Fix:** update to state the correction is merged and the next action is the
bounded retry overlay (`HANDOFF.md:267`, item 3) directly. Then add the mechanical
check described in §3.6 so this class does not recur.


**Fixed** in `HANDOFF.md`. Frontmatter and the stale “not yet committed” and “reaches `main`” statements corrected; a section describing this correction branch added.

---

### F17 — Low: main commits without PR references

`34ec01f` carries `(#35)`; `4dee763` and `32a21dd` do not, though `main` is
documented as requiring a pull request and all three are single-parent (linear
history, as required).

The likely cause is a rebase-merge, which does not append the PR number, versus
the squash-merge that produced `34ec01f`. The effect is that two commits on a
protected, audit-tracked branch cannot be traced to their reviewed PR from the log
alone.

**Fix:** standardize on squash-merge in the repository's GitHub settings so every
`main` commit names its PR, and state the requirement in `BRANCHING.md` alongside
the existing linear-history rule.


**Fixed** in `BRANCHING.md`. A “Determining merge state” section records that squash/rebase merges make `git branch --merged` unusable here and requires squash-merge so every `main` commit names its PR. **The GitHub repository setting itself still needs to be changed by the operator.**

---

### F18 — Low: branch state is undeterminable, plus working-directory strays

**Measured:** 60 local branches (including `main`) and 58 remote branches.
`git branch --merged main` reports **24** merged; `--no-merged` reports **35**.

That second number is not evidence of 35 branches of unmerged work, and this is
the actual finding. The repository requires linear history and merges by
squash/rebase, so a fully merged branch is **not** an ancestor of `main` and shows
up under `--no-merged` regardless. Verified on one case:
`bug/provider-quota-fail-fast` tip `f303558` and `main` commit `60fe8ce` are the
same change — same message, same 8 files, same `161 insertions(+), 4 deletions(-)`
— yet the branch reports as unmerged.

So `git branch --merged` is unusable here, and `BRANCHING.md`'s instruction to
"preserve branch history until its merged state and requested remote retention are
confirmed" has **no mechanism behind it**. An operator cannot tell a
squash-merged branch from real unmerged work without diffing each one by hand,
which is why 117 refs have accumulated.

**Fix:** this is the operational half of **F17**. Once every `main` commit names
its PR (squash-merge policy), merge state is answerable from the log. Add to
`BRANCHING.md`: delete the branch on merge, and for the existing backlog, confirm
by matching each branch tip's change against a `main` commit rather than by
ancestry. `git log --oneline main --format='%s'` versus each branch tip's subject
resolves most of the 35 mechanically.

**Also:**

- **`private/tmp/address_normalize.py`** and **`address_normalize.fixed.py`** are
  untracked working copies of a production script inside the repository. Only
  `tmp/` is ignored, not `private/`, so they appear in `git status` and are one
  `git add -A` away from being committed. A file named `*.fixed.py` beside a
  tracked control script is also a review hazard in its own right — nothing
  records what it fixes or whether that fix reached `scripts/`. Add `private/` to
  `.gitignore`, and reconcile or delete the pair.
- The working tree holds `.llm-cache/` (4,499 files) and `.llm-rate-limit/`
  (10 files). Both are correctly gitignored; both are also live cross-run state
  that the "use a new output directory for every run" discipline does not cover.
  See **F9**.


**Fixed** in `.gitignore`, `BRANCHING.md`. `private/` is ignored, so the stray `address_normalize.py` copies cannot be committed accidentally. Branch-backlog resolution is documented in the same `BRANCHING.md` section.

---

### F27 — High: duplicate edge IDs make a multi-artifact graph unloadable

**Found while fixing F1.** With the node-collision abort removed, a build that
now completes produced a graph that `load_graph` rejected outright:

```
ValueError: evidence graph edge ids must be unique
```

`_edge` derives `edge_id` from the edge type and endpoints only, so two
artifacts asserting the same relationship emitted two edges with one identifier.
The F1 abort had been masking this: the build never got far enough to write a
graph. Any manifest pairing artifacts that share a relationship — the ordinary
case — would have hit it.

**Fixed** in `evidence_graph.py`. New `_add_edge` mirrors `_add_node`: a
content-addressed edge seen twice is one edge with both provenance entries.
Differing approval states degrade to `proposed` and `protected`. The edge
container is keyed by `edge_id` rather than appended to a list.

Regression test: `tests/test_evidence_graph.py`.

---

### F28 — Medium: a non-ISO period string became a month bucket

**Found while fixing F2.** `load_gl`'s fallback accepted any seven-character
prefix as a period, so a GL export using `Q1-2026` created a `Q1-2026` month
bucket. That is worse than the silent drop F2 describes: it inflates the
baseline with a period that does not exist, and the bogus bucket then appears in
`by_period` as a real comparison.

**Fixed** in `completeness.py`. New `iso_month_prefix` accepts only a literal
`YYYY-MM` with a valid month; anything else becomes a `gl_row_period_unparseable`
rejection.

Regression test: `tests/test_critical_high_corrections.py`.

---

### F29 — High: an address contradicting its own country passes clean

**Found in the fifth pass**, by comparing the tracked normalizer against the
superseded `private/tmp/address_normalize.fixed.py` draft (see §6). The draft
carried an `address_postal_format_invalid` check the tracked version had dropped.

`postal_from(last, declared_country)` scopes pattern matching to the declared
country. When the declared country's pattern does not match, the postal code is
simply not recognized — and nothing notices.

**Reproduced** — `10 Downing Street, London, SW1A 1AA, UNITED STATES`:

```
derived city    : SW1A 1AA          <- the postcode, sitting in the city field
derived postal  : None              <- silently dropped
format status   : format_valid
exceptions      : []
record status   : clear
```

**Why it matters:** a self-evidently contradictory address produced a clean
verdict and a garbage city, and `city` feeds entity resolution and CRM staging.

**Fixed** in `address_normalize.py`. When the declared country's pattern misses
but a *different* supported country's pattern matches, the address is registered
as `address_country_postal_mismatch`. Only `US`, `CA`, `GB`, `SG`, `CN`, and `TH`
have patterns and can be judged — an implementation that flagged every country
produced **false positives on France and Germany**, whose five-digit codes have
the same shape as a US ZIP, so a country without a pattern here is never judged.

Regression test: `tests/test_address_and_boundary_corrections.py`.

---

### F30 — High: a region in its own comma segment becomes the city

**Found in the fifth pass** while testing F29. `1 Main St, Springfield, IL, 62704`
— an entirely ordinary US address format — parsed as:

```
address_line1 : 1 Main St
address_line2 : Springfield
city          : IL          <- the state
state_or_region: None
reasons       : []          <- no review work
```

The postal consumed its whole comma segment, leaving nothing to match a state
against, so the next segment was taken as the city unconditionally.

Canada was worse: the module has a Canadian postal pattern and country code but
**no province codes at all**, so every Canadian province landed in `city` and
`state_or_region` was always null.

**Why it matters:** same as F29 — a garbage `city` flows into entity resolution,
attribution, and CRM staging, with no review reason attached.

**Fixed** in `address_normalize.py`. After the postal segment is consumed, a bare
region code in the preceding segment is recognized as the region before the city
is taken. `CA_PROVINCE_CODES` was added and is consulted only once the country
already resolves to `CA` — recognizing "ON" as a province in an unqualified
address would be inference, not parsing. US territories that use the ZIP system
(`PR`, `VI`, `GU`, `AS`, `MP`) were added to `US_STATE_CODES`.

Regression test: `tests/test_address_and_boundary_corrections.py`.

---

### F31 — Medium: the plausibility ceiling had no lower end

**Found in the fifth pass** while re-auditing F22. The ceiling guards the top of
the range; the bottom was open. A `commission_amount` of `-50` on a base of
`1000` gives an effective rate of `-0.05`, which falls *below* the threshold and
classifies as `administrative_ship_to` with `client_review_required: False` and
no exception. A clawback or reversal is not an administrative allocation.

**Fixed** in `allocation_policy.py`. A negative effective rate returns
`allocation_effective_rate_negative` as a critical exception. A zero commission
remains an ordinary administrative allocation.

Regression test: `tests/test_address_and_boundary_corrections.py`.

---

### F32 — Medium: a regression introduced by the F2 fix

**Found by re-auditing my own changes.** F2 replaced
`lower.get("amount") or lower.get("total")` with an `is not None` test so a
legitimate zero amount would survive. That broke the other direction: an export
carrying both columns with `amount` *blank* stopped falling through to `total`
and became a rejected row.

**Fixed** in `completeness.py`. A `first_populated` helper returns the first value
that is present and non-blank, so a genuine `0` is kept and a blank column still
falls through.

Regression test: `tests/test_address_and_boundary_corrections.py`.

---

### F33 — Medium: an empty retrieval index builds and reports clean

**Found in the fifth pass**, sweeping the remaining controls for the F21 pattern.
A canonical export whose rows all fail `allowed()` produces zero chunks, and
`build_database` returned `{"chunks": 0, "factual_rows_only": True}`. The index
then answers "no results" to every question — indistinguishable from the facts
not being present.

`golden_set_evaluate` was checked for the same pattern and is **sound**: an empty
golden set yields null precision/recall/coverage and three critical acceptance
failures. It fails closed already.

**Fixed** in `retrieval_store.py`. The build refuses unless `--allow-empty` is
passed, matching `evidence_graph build --allow-empty-graph`.

Regression test: `tests/test_address_and_boundary_corrections.py`.

---

### F34 — Low: a safety guard reports the wrong failure when its tool is missing

**Found while regenerating the tracked documents.** `scripts/render_docs.sh`
checks LaTeX geometry and table-width safety with `rg`. Ripgrep is installed in
CI but not necessarily locally, and the script has no guard for its absence — so
a missing tool surfaced as:

```
scripts/render_docs.sh: line 25: rg: command not found
Missing required 1-inch Letter geometry declaration: docs/TECHNICAL_DOCUMENTATION.tex
```

The second line is false. The declaration is present; the search tool was not.
An operator would look for a document defect that does not exist. Note the
adjacent Tectonic check does this correctly, with an explicit install message.

**Fixed** in `scripts/render_docs.sh`. Both guards now go through small wrappers
that use `rg` when present and `grep -F`/`grep -E` otherwise.

---

## 3. Structural and architectural assessment

*Written pre-fix. §3.3's specific instances (F2, F11, F12, F20, F21, F28) are
fixed; the shared abstraction it recommends is not. Everything else in this
section remains open and is carried forward in §4.*


### 3.1 The 100% branch-coverage gate has a large, quantified blind spot

This is the most important structural finding, because it explains how **F8** — a
documented setting that provably does nothing — passes a gate requiring 100%
statement *and* 100% branch coverage across 13,043 statements and 5,140 branches
(the counts as of that review pass).

`coverage.py` computes branch coverage from **arcs between statements**. A
conditional *expression* and a boolean short-circuit operator live inside a single
statement, so neither contributes an arc.

**Demonstrated on this repository's own interpreter and `coverage` version:**

```python
# m.py
def f(flag):
    value = "env" if flag is None else flag
    return value
```

A test calling only `f(False)` — never taking the `"env"` arm — reports:

```
Name    Stmts   Miss Branch BrPart  Cover
m.py        3      0      0      0   100%
```

The same holds for `and` / `or`: a test calling `g(False, True, True)` against
`a and b and c` never evaluates `b` or `c`, and still reports 100% with **zero
branches counted**.

**Scale in this repository** (AST count over `scripts/`):

| Construct | Count | Arms/operands invisible to the branch gate |
|---|---|---|
| Conditional expressions (`IfExp`) | **432** | ≥ 864 |
| Boolean operators (`BoolOp`) | **1,143** | ≥ 2,286 |

Concentrated in exactly the modules that make decisions: `schema_discovery.py`
(28 ternaries), `llm_adapter.py` (27), `client_review_cross_record.py`
(23 ternaries / 70 boolops), `allocation_policy.py` (22), `client_review_llm.py`
(15 / 42).

So the gate's real guarantee is: *every statement ran, and every `if`/`for`/`while`
went both ways.* It is **not**: *every decision was exercised both ways.*
`evidence_graph.py:314` — a nested ternary whose first arm alone conjoins seven
conditions to decide whether a proposal becomes `inferred_high_confidence`, the
single most consequential classification in the graph — is one statement. The gate proves it executed. It
proves nothing about which of its arms did.

To be clear about what is *not* wrong: coverage pragmas are used sparingly and
honestly (10 in total, each with a stated reason), and the suite itself is
substantial. The blind spot is a property of the tool, not of the discipline. But
the repository treats "100% branch coverage" as evidence of thoroughness in
`AGENTS.md`, `HANDOFF.md`, and `docs/REPOSITORY_AUDIT.md`, and that inference does
not hold at this ternary density.

> **Still open.** F8 — the dead ternary arm this section explains — is fixed, but
> the gate that failed to catch it is unchanged.

**Recommendation (ordered by value per unit of effort):**

1. **Mutation-test the ten highest-stakes modules.** Add `mutmut` or
   `cosmic-ray` scoped to `consensus.py`, `arithmetic_check.py`,
   `review_protection.py`, `completeness.py`, `sampling.py`, `attribution.py`,
   `evidence_graph.py`, `client_review_package.py`, `llm_runtime.py`,
   `runtime_config.py`. Run it as a separate non-blocking CI job with a
   surviving-mutant budget that ratchets down. This measures directly what the
   coverage gate cannot.
2. **Rewrite decision-bearing ternaries as `if`/`elif` statements** in those same
   modules, bringing them under the existing branch gate at zero new tooling cost.
   The `evidence_graph.py:314` state ladder and the
   `client_review_llm.py:508` `llm_auto_accept_status` ladder are the two clearest
   candidates.
3. **Add a repository check** against nested conditional expressions. Ruff has no
   such rule today, but `release_check.py` already performs custom AST-adjacent
   repository checks and is the natural home.
4. **State the limitation in `AGENTS.md`,** so no future operator or agent reads
   "100% branch coverage" as "every decision tested." It is a strong floor, not a
   proof.

### 3.2 `openai_adapter.py` is the repository's de-facto shared-utility module

AST import analysis of `scripts/`:

| Symbol imported from `openai_adapter` | Importing modules |
|---|---|
| `response_json`, `response_payload` | 12 each |
| `empty_output_directory`, `empty_output_path` | 12 each |
| `REASONING_EFFORTS` | 6 |
| `load_manifest` | 5 |
| `resolve_page`, `sha256` | 4 each |
| `choose_input_mode`, `resolve_text`, `safe_label`, `HEADER_FIELDS`, `LINE_FIELDS` | 2 each |

`openai_adapter` is the second-most-imported internal module in the repository
(18 importers, behind only `runtime_config` at 33). **`google_genai_adapter.py`
imports from `openai_adapter.py`** — a Google adapter depending on the OpenAI
adapter's namespace. `llm_provider.build_client` imports it unconditionally for
every provider.

This is not a runtime bug: the `openai` SDK itself is lazily imported inside
`build_client` (`openai_adapter.py:1057`), the
import graph is acyclic, and every test passes. It is an **ownership** problem,
and it contradicts `SKILL.md`'s own rule: *"Extend shared provider,
review-protection, no-clobber, and validation logic rather than forking it."* The
no-clobber contract — `empty_output_directory` / `empty_output_path`, which
enforce the "new output directory for every run" invariant across twelve modules —
currently lives inside a vendor namespace.

Concretely:

- A provider-neutral invariant is versioned, reviewed, and tested as part of an
  OpenAI-specific file.
- `response_json` / `response_payload` parse *provider response shapes*, and twelve
  callers reach for the OpenAI implementation regardless of which provider produced
  the response.
- Adding a fourth provider means importing from `openai_adapter` again, or
  forking — which the same rule prohibits.

**Recommended refactor** (mechanical, low risk, high clarity):

```
scripts/
  run_io.py             empty_output_directory, empty_output_path, sha256,
                        load_manifest, resolve_page, resolve_text, safe_label
  llm_response.py       response_json, response_payload, REASONING_EFFORTS,
                        choose_input_mode
  extraction_schema.py  EXTRACTION_SCHEMA, HEADER_FIELDS, LINE_FIELDS,
                        extraction_instructions, normalized_source_labelled_fields
  openai_adapter.py     OpenAI transport and OpenAI response shape only
```

Sequence it as: create the new modules re-exporting from `openai_adapter` (no
behavior change, gate stays green) → move importers file by file → move the
definitions and delete the re-exports. Each step is independently gate-clean,
which matters given the repository's stable-release discipline.

### 3.3 Exception discipline is asymmetric — and the asymmetry is systematic

The repository's central promise — hard constraint #2, "emit a record or explicit
exception for every input and failed stage" — is honored rigorously on the
**document** path and inconsistently everywhere else. Three independent
measurements converge on the same boundary.

**(a) Guard-`continue` density.** An AST sweep for `if <cond>: continue` with no
recording found **110 sites** in `scripts/`. The concentration is diagnostic:

| Module | Sites | Its own stated contract |
|---|---|---|
| `completeness.py` | 11 | "Every unresolved item becomes an explicit exception" |
| `attribution.py` | 10 | "No dollar is unaccounted for silently" |
| `independent_table_reconcile.py` | 7 | independent corroboration |
| `table_comprehension_corpus.py` | 7 | |
| `evidence_graph.py` | 6 | |
| `entity_resolve.py`, `allocation_policy.py`, `llm_usage_report.py` | 5 each | |

Not all 110 are defects — many skip genuinely inapplicable cases recorded
elsewhere. But the two modules making the strongest completeness promises are the
top two, and every instance examined in detail (**F2**, **F11**, **F12**, **F20**)
turned out to be a real loss.

**(b) Empty-input handling.** The document-path controls fail closed:
`arithmetic_check.py:386`, `attribution.py:455`, `completeness.py:534`,
`sampling.py:455`, and `consensus.py:589` all `sys.exit("No records found…")`. The
corroboration path and the graph path do not — see **F21**.

**(c) Input provenance.** The pattern behind (a) and (b):

> **Document-side inputs get registers and empty-input guards. Side-channel inputs
> — GL exports, payment files, reference exports, Document AI corroboration —
> get `continue`.**

`attribution.py` builds a complete `unattributable` register for every document
and enforces that "'97% attributed' fails unless the other 3% is enumerated."
Fifty lines earlier, `load_reference` drops unrecognized reference rows with no
record at all. The discipline exists; it simply was not extended to inputs that
arrive from the client rather than from the scanner. That is a plausible
historical accident — the document path was built first and audited hardest — but
it means the completeness *of the completeness controls* depends on which side of
the pipeline an input entered from.

> **Since this was written:** the specific losses are closed — GL rows, reference
> rows, unanalyzed series, over-cap entity blocks, and empty corroboration runs
> all carry registers now. The shared abstraction below was **not** extracted, so
> each was fixed in its own module and the next side-channel input can repeat the
> pattern.

**Recommended upgrade — one shared ingestion contract:**

```python
# scripts/side_channel_input.py
def load_rows(path, *, extractor, source_kind) -> tuple[list, list]:
    """Return (accepted, rejected). Every rejected row retains its raw content,
    row index, source file hash, and a reason code. Callers must write
    `rejected` into their artifact; no caller may discard it."""
```

Then:

1. `attribution.load_reference`, `completeness.load_gl`, the payments loader, and
   `client_input_comments.py` all use it.
2. Each artifact gains `<input>_rows_ingested` / `<input>_rows_rejected` plus the
   rejection list.
3. **Gate change:** a non-empty rejection register blocks a clear completeness or
   attribution status, exactly as unresolved aging already does.
4. Add a `release_check.py` rule flagging any new `if …: continue` inside a
   function named `load_*` — the same style of custom repository check it already
   performs for environment settings and CLI entry points.

### 3.4 The evidence graph is the right idea and is under-defended

The graph is the most architecturally interesting component and the one that
future work — cross-packet discovery, schema inventory, contradiction surfacing —
depends on. It gets several hard things right: content-hash verification on load,
full schema and endpoint validation before any query, deterministic node and edge
IDs, correct RFC-6901 pointer escaping in `_pointer_token`, and a fail-closed
`load_graph`.

Five findings land on it (**F1** crash, **F6** fabricated pointers, **F10**
substring matching, **F21b** empty-graph fail-open, and the **F3** flag it
inherits). That is not a coincidence — it is the newest major subsystem and has
had the least adversarial input. Beyond the fixes already stated:

- **`candidates()` returns `"score": 0` for every candidate**
  (`:590`). It is honest —
  `production_approval_permitted` is `False` — but a constant score means the
  command cannot rank, so a caller must fetch all `MAX_LIMIT`. Either implement a
  provenance-count / edge-type ranking or remove the field, rather than shipping a
  placeholder in a documented contract.
- **`candidates()` traverses only outgoing edges** while `path()` treats the graph
  as undirected. Two commands, two graph semantics, no note in
  `references/artifact-contracts.md`.
- **`path()` returns a single shortest path** but the key is plural (`"paths"`).
  Either return all shortest paths up to a bound, or rename the key.
- **No build-mode control.** Once collisions become exceptions (**F1**), add an
  explicit `--fail-on-exception` so an operator can choose between "build the
  overlay and register the conflicts" and "refuse to build a conflicted graph."

### 3.5 Supply chain and CI

CI is well constructed: actions pinned by commit SHA, `persist-credentials: false`,
`permissions: contents: read`, job timeouts, a separate acceptance job, and
workflow regression tests protecting the pins and the coverage gate. Four gaps:

1. **No hash pinning.** `requirements.txt` pins versions but not hashes, and CI
   runs plain `pip install -r`. For a repository whose thesis is provenance, add
   `--require-hashes` with a compiled lock file.
2. **No dependency vulnerability scan.** Add `pip-audit` as a non-blocking job,
   then promote it once clean.
3. **Single Python version.** CI tests only 3.12; local development runs 3.14 and
   already surfaces a `google-genai` deprecation warning CI cannot see. Add 3.13
   and 3.14 as allowed-to-fail matrix entries so drift is visible before it breaks.
4. **No static security analysis.** Ruff's `select` list is `["E4","E7","E9","F","I","B","UP"]`
   — no `S` (bandit) rules. `S314` (`suspicious-xml-element-tree-usage`) targets exactly the
   `ET.fromstring`-on-untrusted-input pattern in **F4** and would have flagged it
   automatically. (Verified: the rule exists in the pinned Ruff 0.16.3.) Adding `S` with a targeted per-file ignore list is the
   single highest-value CI change available.

### 3.6 Documentation

The documentation is unusually good, and the ownership model in `README.md:33-43`
is why: one owner per concern, mechanically enforced by `release_check.py` for
environment settings and CLI entry points. A programmatic sweep of 37 Markdown
files found **zero** broken script references and **zero** undocumented flags —
a rare result at this size.

Two structural observations:

- **The normative rule list exists in three places with three cardinalities.**
  `SKILL.md` has 8 "Invariants," `AGENTS.md` has 8 "Hard constraints,"
  `README.md` has 6 "trust model" rules. They agree in substance, but they are
  maintained by hand — and **F13** is precisely what drift in this list looks
  like. Make one file normative (`AGENTS.md` is the natural choice), have the
  others link to it, or add a `release_check.py` rule comparing them.
- **`HANDOFF.md` has no freshness check.** It is scoped to current state and
  carries `last_verified` frontmatter, but nothing validates it. Add a release
  check: fail if `HANDOFF.md` contains "not yet committed" while the working tree
  is clean, or if `last_verified` predates `HEAD`'s commit date. That would have
  caught **F16** at the gate — and this is the same mechanical-enforcement pattern
  the repository already uses successfully for `.env.example` and CLI coverage.

---

## 4. What remains

**One item: the branch-coverage blind spot in §3.1.** Every finding in §2, every
structural item in §3, and every item in §4A is closed. §3.1 remains open by
design — mutmut 3.x cannot produce a machine-readable count non-interactively, so
the check exists and is strict but is deliberately not in CI. It is the largest
outstanding verification item in the repository.

### Closed since the plan was first written

| Item | Resolution |
|---|---|
| **F17** squash-merge policy | **Done.** The repository now allows squash merges only (`allow_merge_commit=false`, `allow_rebase_merge=false`), sets `squash_merge_commit_title=PR_TITLE`, and enables `delete_branch_on_merge`, so every `main` commit records its PR and the branch backlog cannot rebuild. |
| **F18** branch backlog | **Done locally.** `scripts/branch_state.py` resolves each branch by ancestry, patch identity, or subject match — the three forms of evidence a squash/rebase history leaves. It reported 24 `merged_by_ancestry`, 34 `merged_by_patch`, and 2 unresolved; the 58 provably merged local branches were deleted after their tips were recorded. |
| **F2** date-order policy | **Done.** `COMPLETENESS_DATE_ORDER` (`month_first` \| `day_first` \| `iso_only`) makes the reading of an ambiguous numeric date an explicit, documented decision. The default preserves the historical month-first behavior, so no existing corpus reparses silently. |
| **F11** gate blocking | **Done.** `COMPLETENESS_BLOCK_ON_UNANALYZED_POPULATION` lets an operator gate on an unanalyzed population. It is opt-in because a corpus too small to infer from is a scoping decision. |
| §3.1 mutation testing | **Partly done, and deliberately not wired into CI.** `scripts/mutation_check.py` exists and fails closed. The tool does not: mutmut 3.x is TUI-oriented and, in a non-interactive runner, stalls at "Running stats" without emitting a machine-readable summary. **The first version of this check defaulted to zero surviving mutants and passed CI green while mutmut had not run at all** — the exact failure this repository exists to prevent, produced by this review's own tooling. The runner now refuses rather than reporting a count it did not receive, and the CI job was removed. Run it manually on a terminal, or replace the tool; do not wire it in until it produces a count non-interactively. The 100% branch gate's blind spot over 432 ternaries and 1,143 boolean operands therefore **remains open** and is the largest outstanding item in the repository. |
| §3.1 de-ternary the ladders | **Done.** `evidence_graph.py`'s `inferred_high_confidence` ladder and `client_review_llm.py`'s `llm_auto_accept_status` ladder are `if`/`elif` statements, so the existing branch gate now sees every arm. |
| §3.2 module extraction | **Done.** `run_io.py`, `llm_response.py`, and `extraction_schema.py` hold the provider-neutral primitives. `openai_adapter` went from 18 importers to 4, and the remaining coupling is only `build_client` — genuine OpenAI transport. `google_genai_adapter` no longer imports it at all. Every moved definition was verified byte-identical to what it replaced. |
| §3.3 shared ingestion contract | **Done.** `scripts/side_channel_input.py` holds one `(accepted, rejected)` contract for client-supplied inputs, used by the GL and reference loaders. |
| §3.3 `release_check` rule | **Done.** `side_channel_loader_errors` fails the release gate when a `load_*` function skips a row without recording it. |
| §3.4 graph contract | **Done.** `candidates()` ranks by distinct retained provenance and declares `edge_direction: outgoing`; `path()` declares `paths_returned` and `edge_direction: undirected`, so the plural key cannot be read as exhaustive. |
| §3.5 supply chain | **Done.** `requirements.lock` is hash-pinned and both gating jobs install with `--require-hashes`; a `supply-chain` job runs `pip-audit` and re-compiles the lock to catch drift; a `runtime-matrix` job runs 3.13 and 3.14 advisory. Ruff's `S` (bandit) ruleset is enabled — it independently flagged the **F4** pattern, confirming the finding. |
| §3.6 normative rule list | **Done.** `AGENTS.md` is normative; `release_check.normative_rule_errors` compares the rule counts in `SKILL.md` and the technical guide against it and requires `README.md` to name it. |
| §3.6 handoff freshness | **Done.** `release_check.handoff_freshness_errors` rejects a stale pending-work claim and requires `last_verified`. It distinguishes a claim about the current tree from the standing instruction that uses the same words. |
| §6 `private/tmp` strays | **Done.** Both superseded drafts were compared, discarded, and the directory removed; `private/` is ignored. The one check they held that the tracked code lacked became **F29**. |
| `docs/inferred-control-lane-record` | **Done.** The branch held a genuine, never-merged verification record for an implemented lane. Every behavioral claim was re-verified against the current code, its historical coverage figures were dated so they cannot be read as current, and it is now `docs/INFERRED_CONTROL_LANE_RECORD.md`. |

### Branch backlog — closed

The remote cleanup is done. `branch_state.py` reported 24 `merged_by_ancestry`
and 34 `merged_by_patch`; every provably merged local and remote branch was
deleted after its tip was recorded, and `docs/inferred-control-lane-record` was
merged as `docs/INFERRED_CONTROL_LANE_RECORD.md` before deletion. The backlog
went from 117 refs to `main` only, and `delete_branch_on_merge` keeps it there.

```bash
python scripts/branch_state.py --out branch_state.json
```

## 4A. Sixth pass — repository-wide consistency review

A later pass audited the whole repository for staleness, superfluity, and
documentation accuracy rather than for control defects. Four findings, all fixed.

| Finding | Severity | Evidence | Resolution |
|---|---|---|---|
| **F35** — 407 of 584 command-line arguments carried no help text | High | `references/command-line-reference.md` states "the executable parser is the exact contract" and routes operators to `--help`; 54 of 65 commands rendered bare flag names. An agent told to discover options through `--help` learned nothing about 70% of them. | Recurring wording centralised in `scripts/cli_help.py` and attached by `apply_shared_help`; command-specific options given their own. New `release_check.cli_help_errors` fails when an argument neither carries inline help nor uses the shared vocabulary, and a per-command test proves all 65 parsers build. |
| **F36** — four documented "hard limits" were read by nothing | Medium | `GOOGLE_VERTEX_AI_MODEL_MAX_FILE_BYTES`, `_MAX_FILES_PER_REQUEST`, `_MAX_INPUT_BYTES`, and `_MAX_PAGES_PER_FILE` shipped in `.env.example` and were documented as Vertex hard limits, but `PROVIDER_LIMITS["google"]` read only `context_tokens` and `model_max_output_tokens`. Names shadowed the enforced operator caps (`GOOGLE_VERTEX_AI_MAX_PAGES`, `_MAX_PDF_BYTES`), so an operator could cap the wrong setting and believe the run was bounded. Same class as **F8**. | `runtime_config.google_capability_errors` compares each operator cap against the capability that bounds it and refuses before work begins. `_MAX_PAGES_PER_FILE` is documented as recorded-only, because intake bursts every source into one-page masters and the ceiling is satisfied by construction. |
| **F37** — `HANDOFF.md` described merged work as pending | Medium | The frontmatter read `critical-and-high-corrections-applied-on-branch`, a section headed "Active correction branch" said "It is not yet merged", and the gate figures were three passes stale. The freshness rule added in §3.6 matched only "not yet committed", so it did not fire. | Section rewritten to current state; `STALE_PENDING_PATTERN` widened to "merged", "applied", and "pushed", with a lookbehind that still exempts the standing "do not overwrite … merely because it is not yet committed" instruction. Regression tests cover both directions. |
| **F38** — dead function retained as "compatibility" | Low | `review_agent.evidence_packet` was reachable only from its own two tests; its docstring claimed use by "compatibility tests/tools" and no tool used it. It contributed covered statements without protecting behaviour. | Removed with its test references. The empty untracked `tools/` directory was removed at the same time. |

The skill folder was also completed in this pass: it covered roughly fifteen
commands against a repository of sixty-five, and omitted every cross-checking
lane. See the changelog entry for the full list of lanes added.

## 4B. Seventh pass — production-readiness review of a live configuration

A readiness review of an operator's real `.env` for a first production trial
(OpenAI primary, OpenRouter secondary, Google for Document AI and handwriting).
Four findings, all fixed.

| Finding | Severity | Evidence | Resolution |
|---|---|---|---|
| **F39** — buddy independence compared provider names, not model vendors | **Critical** | `client_review_iterative.validate_reviewer_roles` refused only `primary_provider == buddy_provider`. An OpenAI primary confirmed by an OpenRouter lane routing `openai/gpt-oss-120b` passed as independent: one set of weights, two names, and a buddy that cannot disagree for any reason the primary would not have reached itself. `consensus.py` had resolved this to the vendor since **F5**; the iterative, cross-packet, and exception-resolution lanes had not. The reviewed `.env` was configured exactly this way. | Resolves both sides through `model_vendor`, refuses a routed slug with no vendor prefix, and names the vendor in the refusal. Five regression tests. |
| **F40** — the test suite read the operator's private `.env` | **High** | There was no `tests/conftest.py`. Every test that invoked a CLI resolved `--enable` defaults from whatever `.env` the machine had. Enabling a disabled-by-default lane locally turned a passing suite into a failing one, while CI — which has no `.env` — stayed green. A gate whose verdict depends on a gitignored file is not a control, and it fails in the direction that hides problems locally. | An autouse fixture points `project_env_path` at a nonexistent file and clears every project-prefixed variable, so each test starts from the environment CI runs in. A test asserts the real resolver stays repository-relative. **The first version of this fixture was itself incomplete**: it cleared the environment but did not block the gcloud subprocess behind **F41**'s token minting, so the suite quietly minted real Google access tokens on a machine with working ADC and passed, while CI — which has no gcloud — failed. The same local-versus-CI divergence, one layer down. The fixture now refuses any external command from inside the suite; a test that exercises minting injects its own runner. |
| **F41** — a short-lived token had a permanent home in `.env` | Medium | `.env` carried a live `ya29.` OAuth token, directly under a comment saying not to store one there. Those tokens live about an hour, so the stored value is stale before it is useful, and the field's existence is what invites pasting. Handwriting OCR named a second variable that was never set at all, so the lane could not have run. | The token settings are gone from `.env` and `.env.example`. `reauthorize_google.access_token` mints one from Application Default Credentials at run time, non-interactively; an exported value still wins for CI and secret managers. Seven regression tests. |
| **F42** — installed packages had drifted from the declared pins | Low | The venv held `openai==3.0.0` against a pinned `3.2.0`. `pip check` reports broken dependency graphs, not pin drift, so the gate passed while local runs used an SDK the repository did not pin. | Direct dependencies are declared without pins; `requirements.lock` remains the hash-pinned resolution both gating jobs install with `--require-hashes`. Upgraded `openai` to 3.3.1, `google-genai` to 2.20.0, `pikepdf` to 10.12.0, `ruff` to 0.16.4, and verified the full gate on them. |

| **F43** — alias and auto-routing slugs escaped vendor resolution | **High** | `model_vendor` split a routed slug on `/` and compared the prefix. OpenRouter's catalogue also publishes alias slugs (`~openai/gpt-mini-latest`, resolving to `~openai`) and auto-routing slugs (`openrouter/auto`, resolving to `openrouter`). Neither equals `openai`, so either would pass an independence check against an OpenAI primary — the alias *is* OpenAI, and auto-routing selects an unknown model at request time that may be. This affected `consensus.py` and every buddy lane, since both use the same function. | The alias prefix is stripped before comparison and an auto-routing slug returns the unresolved sentinel. Regression tests cover both, at the `model_vendor` level and through `validate_reviewer_roles`. |
| **F44** — OpenRouter could not read a scanned page at all | Medium | The adapter refused every non-text content part, so on a scanned corpus an OpenRouter lane returned nothing and consensus fell to `single_engine`. The naive fix — sending the PDF — is worse than the limitation: OpenRouter's default `file-parser` OCRs with a *separate vendor's* engine (`mistral-ocr`) and passes text to the routed model, so the recorded vendor would not be the vendor that read the glyphs, and two lanes on different models could share one OCR pass. | Page PDFs are submitted only to models OpenRouter declares as accepting `file` input, verified against its live catalogue before any page is sent, with the engine pinned to `native`. An unreadable catalogue or an unlisted model fails closed. |

### Configuration findings reported to the operator, not code defects

- **OpenRouter cannot read a scanned page.** Its adapter accepts native text
  only, by design and in every input mode. On a scanned corpus an OpenRouter
  secondary lane returns no reading, consensus sees one engine, and every field
  is queued — correct behaviour, and an unusable trial. It is a viable secondary
  only for a native-text corpus; otherwise it belongs on the reasoning and
  review lanes, which send JSON packets.
- `table_comprehension.py`, `layout_aware_extract.py`, and `llm_adjudication.py`
  submit PDFs, so OpenRouter can never serve them.

## 5. What this review did and did not cover

*Coverage of the **review**. The remediation touched 28 scripts and 11 test
files across five passes; see §4 for what was deliberately left undone, and §6
for the disposition of the untracked working copies.*

**Line-audited across four passes:** `evidence_graph.py`, `consensus.py`,
`arithmetic_check.py`, `completeness.py`, `attribution.py`, `sampling.py`,
`review_protection.py`, `llm_runtime.py`, `runtime_config.py`, `llm_provider.py`,
`retrieval_store.py`, `retrieval_https.py`, `client_review_package.py` (intake
paths), `independent_table_reconcile.py`, `entity_resolve.py`,
`allocation_policy.py`, `scan_profile.py`, plus targeted reads of
`client_review_llm.py`, `safe_review_consolidation.py`, `schema_discovery.py`
(confidence gating), `client_review_cross_record.py` (confidence gating),
`llm_adjudication.py`, `openai_adapter.py`, `google_genai_adapter.py`
(cache path), `google_document_ai_adapter.py`, `handwriting_review.py`, and
`client_review_iterative.py`.

Pass four also ran repository-wide AST sweeps for four defect classes — dead
`store_true`/`is None` config branches, `except: pass` handlers, bare excepts, and
guard-`continue` sites — across every module in `scripts/`, so those classes are
covered even where a module was not read line by line.

**Explicitly not covered:**

- **No live provider calls.** Every LLM lane was reviewed statically. Prompt
  quality, schema-adherence rates, and real provider error taxonomies are
  unassessed.
- **No acceptance runners executed.** `run_acceptance_packet.sh`,
  `run_operations_acceptance.sh`, and the public stress corpus were read, not run.
  CI runs them on every PR and reports them green.
- **No PDF visual verification.** The four tracked PDFs and their 62 rendered pages
  were not re-inspected; `release_check.py` confirms they are current relative to
  their Markdown sources.
- **Structure-only review** of `google_handwriting_ocr.py`,
  `address_normalize.py`, `table_comprehension.py`,
  `table_comprehension_corpus.py`, `client_review_cross_packet.py`,
  `ai_simulated_client_review.py`, `client_review_exception_resolution.py`,
  `review_agent.py`, `adjudicate.py`, `validate_extraction.py`,
  `golden_set_evaluate.py`, `reassemble_pages.py`, `canonical_load.py`,
  `csv_api_staging.py`, and `template_drift.py`.

  **A further pass is warranted, and pass four is the evidence for that.** Pass
  three treated `allocation_policy.py` and `scan_profile.py` as structure-only;
  reading them produced F22 (a 1000% commission rate passing clean), F23 (a
  contract violation), F24, and F25 — including the single most severe control
  gap in this review. The remaining structure-only modules should be assumed to
  hold comparable findings. Highest-yield targets by size and decision density:
  `schema_discovery.py` (1,328 lines, 28 ternaries — only its confidence gating
  was read), `client_review_cross_record.py` (1,665 lines, 70 boolops — same),
  `table_comprehension.py` (1,049 lines), and `ai_simulated_client_review.py`
  (1,128 lines). §3.1 explains why their 100%-coverage status is weaker evidence
  than it appears.
- **Threat modeling beyond the untrusted-workbook path.** `retrieval_https.py` was
  reviewed and its authentication is correct (`hmac.compare_digest`, 401 returned
  before any parsing, TLS 1.2 floor, POST rejected). It has no rate limiting, no
  auth-failure delay, and `ThreadingHTTPServer` spawns unbounded threads. It is
  disabled by default and binds `127.0.0.1`, so this is a hardening note rather
  than a finding — but `--bind-host 0.0.0.0` is accepted with no warning, and
  `retrieval_store.fts_query` passes user tokens into an FTS5 `MATCH` where an
  uppercase `AND`/`OR`/`NOT`/`NEAR` in the question becomes query syntax rather
  than a search term (it fails closed to a 400, but the behavior is undocumented).

---

## 6. Disposition of the untracked working copies

`private/tmp/` held two untracked working copies of a production script:
`address_normalize.py` (476 lines) and `address_normalize.fixed.py` (250 lines),
both dated 2026-08-13. Neither was referenced by any script, test, or document.

**Both were discarded.** The tracked `scripts/address_normalize.py` (644 lines)
supersedes both:

| | `fixed.py` | `private/tmp/address_normalize.py` | tracked |
|---|---|---|---|
| Functions absent from tracked | none | none | — |
| Google Address Validation | absent | present, without retry or error categorization | present, with bounded retry, `google_error_category`, and request counting |
| Postal patterns | US, CA, GB | US, CA, GB | + SG, CN, TH, country-scoped |
| Postal slice | `last.rfind(postal)` | `last.rfind(postal)` | `match.start()` |

The name `fixed.py` is misleading: it is the **earlier** draft, and it carries a
truncation bug the tracked version already corrected. `postal_from` uppercases
and strips spaces from the matched postal, so `rfind` on the original text can
miss and return `-1`, silently dropping the last character:

```
'500 King St W, Toronto, ON, M5V 1L9, CANADA'
  fixed.py -> city 'M5V 1L'          (lost the trailing 9)
  tracked  -> city 'Toronto'
'10 Downing Street, London SW1A 1AA, UNITED KINGDOM'
  fixed.py -> city 'London SW1A 1A'  (lost the trailing A)
  tracked  -> city 'London'
```

**One thing was worth keeping.** `fixed.py` had an
`address_postal_format_invalid` check the tracked version dropped when it moved
to country-scoped matching. That check was the only thing in either draft that
the tracked code lacked, and its absence is **F29**. Its intent was incorporated
— as an explicit `address_country_postal_mismatch` exception rather than as a
side effect of unscoped matching — and testing that fix surfaced **F30**.

So the correct disposition was: discard the files, keep the idea. `private/` is
now in `.gitignore` (**F18**) and the directory has been removed.

