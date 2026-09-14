# Repository, Methodology, and Documentation Audit

Audit performed August 25, 2026. Amended August 28, 2026 -- see
[what this audit could not see](#what-this-audit-could-not-see).

This is a historical audit record for the repository state reviewed on those
dates, not the current release or run status. Use `HANDOFF.md` for current state
and rerun the checked-in release and quality gates for current evidence.

## Outcome

The repository was reviewed as one control system: implementation, tests,
command-line contracts, generated artifacts, methodology, operating guidance,
agent instructions, CI, and release checks. The audit corrected material drift
that a green test suite alone could not reveal. The resulting boundaries are
conservative: unknown review reasons are protected, incomplete reconciliation
blocks analytics, returned client workbooks are treated as untrusted input, and
approved-fact retrieval has no open-review bypass.

This audit does not claim production accuracy or client approval. Those claims
still require the client-authorized evidence, mappings or amendments, reruns,
reconciliation, completeness checks, and final review gate described below.

## Scope reviewed

- Every tracked production script and its command-line entry point.
- Unit, integration, workflow, release, acceptance, and public-corpus tests.
- Repository instructions, `SKILL.md`, agent entry points, handoff material,
  contribution and release controls, and CI configuration.
- README, technical documentation, client guide, analytics plan, references,
  artifact contracts, runtime settings, fixtures, and generated documentation.
- Client-review grouping, package generation, workbook return/import, proposal
  validation, protected-item policy, and post-review rerun boundary.
- Arithmetic, completeness, sampling, attribution, canonical loading, staging,
  and approved-fact retrieval methodology.
- Credential, client-data, generated-artifact, no-clobber, reproducibility, and
  operator-path release checks.
- All four PDFs tracked at the time of this audit and their 62 rendered pages
  for margins, page boundaries, headers, tables, wrapping, and legibility.

## Findings and disposition

| Priority | Finding | Disposition |
|---|---|---|
| Critical | Protected review categories were defined differently in several scripts, and unknown reasons could be treated as safe. | Added one shared fail-closed taxonomy. Arithmetic, provider/schema, reassembly, review-flag, handwriting, identity, and unresolved-evidence families remain protected; only an exact safe-reason allowlist is unprotected. |
| Critical | A returned client workbook could be imported without proving that its fixed content matched the issued workbook. | Import now requires the separate immutable issued workbook, validates the exact worksheet/header/group/order/fixed-cell contract, compares both packages, rejects same-path input, records both hashes, and emits proposals only. |
| Critical | Approved-fact retrieval exposed an option that could include open-review records. | Removed the retrieval escape hatch. The retrieval store always rejects non-approved facts; controlled canonical load planning remains a separate boundary. |
| High | The completeness gate could pass when general-ledger input was missing, aging reconciliation was partial or ambiguous, payments were missing, or periods were out of tolerance. | Added explicit run statuses and gate reasons. Missing or unmeasurable inputs, reconciliation exceptions, sequence/calendar gaps, undated documents, and unresolved or ambiguous aging now block. |
| High | Payment matching could cross-apply the same invoice number between vendors. | Matching now uses vendor, invoice, and currency; ambiguous candidates remain unresolved instead of being silently applied. |
| High | Sampling mixed design assumptions, permitted ineligible records, and described two-sided error as projected overstatement. | Eligibility is fail-closed on review and arithmetic status; the certainty stratum is document-based; projection is one-sided overstatement; malformed, duplicate, or off-sample findings block; exhausted confidence tables report no upper bound. |
| High | The client workbook contract relied on human-readable content and could accept unexpected editable columns or unsafe alternatives. | The importer enforces the exact 14-column contract, permits edits only in the two client-response columns, rejects protected decisions and invalid choices, and requires a note for client-supplied alternatives. |
| High | XLSX intake did not bound ZIP member count or decompressed content. | Added package/member size, member-count, total-uncompressed-size, and required-part preflight checks before workbook parsing. |
| High | Proposed updates were not consistently bound to the field under review. | Proposal validation now requires the update field to match the reviewed field and retains proposal-only semantics. |
| High | Cross-record and full-dataset proposal reducers did not consistently prove that a cited reference existed or that the proposal targeted the reviewed field. | Both reducers now reject missing/foreign evidence references and cross-field proposals before any review-reduction policy can consume them. |
| High | Consensus could numerically coerce identifiers, silently replace a duplicate engine record, and expose the first engine's document type even when the vote disagreed. | Identifiers now compare without numeric coercion, duplicate engine/document identities fail closed, and downstream document type comes from the vote or remains `unknown`. |
| Critical | Self-labelled engine files could count as independent consensus, and an empty sampling findings file could produce a completed accuracy bound. | Consensus now requires distinct versioned provider/lane handoffs with record and retained-response hashes. Sampling requires one explicit hash-bound outcome for every selected item; incomplete review reports no bound. |
| High | Golden-set aggregate failures, stale reviewer resumes, inconsistent template fingerprints, and unbound client decision catalogs could appear clear or reusable. | Aggregate failures now emit exceptions; resume binds queue/context/config/card/raw evidence; template fingerprints are recomputed with exact authorization partitions; returned decisions are bound to the issued workbook, consolidation, selected semantics, and client note. |
| High | Python metadata permitted versions incompatible with the pinned numerical dependency. | Raised the supported floor to Python 3.12 and aligned Ruff/CI configuration and installation documentation. |
| High | CI used floating action tags and broader default credentials than required. | Pinned actions by commit, disabled checkout credential persistence, restricted permissions to read-only contents, and added a job timeout and pinned document renderer version. |
| Medium | Client package artifacts were not fully reproducible, and preflight could leave a partial directory when a sibling ZIP already existed. | Normalized workbook/document/ZIP metadata and timestamps and preflighted all output targets before creation. |
| Medium | Package summaries contained historical corpus counts that could become false. | Replaced fixed counts with values derived from the current consolidation artifact. |
| Medium | Generated root artifacts and absolute operator paths were not comprehensively rejected at release time. | Release checks now reject common generated deliverables at repository root and scan controlled text for operator-specific absolute paths while tolerating invalid UTF-8 binary inputs safely. |
| Medium | Environment guidance implied that a short-lived Document AI token could be kept in repository configuration. | Removed it from `.env.example`; documentation consistently requires a user-selected `0600` file outside the repository. |
| Medium | Provider, review, retrieval, sampling, completeness, and remaining-integration descriptions had drifted across README, agent files, handoff, plans, references, and client documentation. | Synchronized the language, command examples, implemented-provider inventory, proposal-only boundary, protected categories, exact rerun sequence, and explicit residual work. |
| Medium | The handoff contained a brittle static branch inventory and duplicated old priorities. | Replaced it with durable operating state and current client-return instructions. |
| Medium | Fixture documentation still described implemented adapters as absent. | Corrected the fixture and acceptance descriptions without upgrading any client-dependent integration claim. |
| Medium | Runtime and CLI documentation named settings but did not mechanically prevent grouped, duplicated, default-drifted, or newly unindexed controls. | Made runtime configuration one-row-per-setting, added a complete command-line entry-point reference, and extended the release gate to compare all 157 `.env.example` settings/defaults and every argparse CLI against their canonical documentation. |
| Medium | README, technical guidance, skill instructions, agent entry points, and handoff repeated large implementation inventories with different audiences and high drift risk. | Assigned each document one owner role; rewrote the README as a gateway, the technical guide as the self-contained operator manual, the skill as a progressive-disclosure router, and the handoff as current state only. Added failure recovery, troubleshooting, extension guidance, glossary, and an exact returned-workbook rerun procedure. |
| Critical | A capped adaptive packet run returned deferred batches as nested lists while the exception lane consumed individual work items. | Flattened the shared deferred-work contract and added branch-complete regression tests so every capped item retains its exact source IDs. |
| Critical | Exception findings with missing source records and malformed or out-of-packet primary proposals could disappear from normalized output. | The generic exception lane now counts every input finding, rejects duplicate record IDs, retains unmatched findings and rejected proposals explicitly, validates packet-bound related IDs and source-visible quotes, and links each proposal to its source exception IDs. |
| High | The buddy exception reviewer did not receive the same named findings and reasoning-only client context described by its contract. | Both independent reviewers now receive the same findings, compact records, and reasoning-only context; the buddy additionally receives the primary proposals. |
| High | A client-review resume could reuse decisions after the protected-final-run instruction policy changed. | Upgraded the resume contract and bound it to the exact reviewer-instruction hash. |
| Medium | The disabled exception-lane CLI could create one output before discovering that a sibling target already existed. | Preflight now validates all output paths and the raw directory before writing any disabled-run artifact. |
| Medium | Email-integration guidance described a matched attachment as a replacement and implied deferred execution had no cost. | Clarified append-only source handling and separated no-redesign/no-reprocessing benefits from the real later execution cost. |
| High | The optional real-document acceptance runner still supplied bare extraction arrays after consensus began requiring hash-bound independent handoffs; its failure was easy to miss when several shell commands were invoked without fail-fast orchestration. | The runner now creates two explicit, hash-bound fixture handoffs with distinct provider/lane identities and retained raw evidence. A workflow regression test protects the handoff contract, and the real two-page acceptance chain passes end to end. |

## Client-return boundary

The returned workbook is never authoritative production data. Preserve it
unchanged in a new output directory and import it beside the exact issued
workbook. A successful import proves only that the response conforms to the
issued decision surface and produces valid proposals. An authorized operator
must compile an exact impact/rerun preview, separately authorize the selected
patches, apply them as append-only mappings or amendments,
rerun only the affected lane, and then rerun consensus, arithmetic,
reconciliation, completeness, attribution/entity checks as applicable, final
review, safe consolidation, and package generation. Protected items cannot be
cleared through batch decisions.

## Methodology interpretation

- Arithmetic proof and completeness are separate requirements; neither is
  inferred from an empty review queue.
- Sampling accepts only eligible, approved records with proved or explicitly
  non-applicable arithmetic status. The reported monetary projection is an
  overstatement bound, not a generic accuracy estimate.
- Client review, LLM review, grouping, mapping discovery, cross-record search,
  and full-dataset reasoning remain proposal-only until authorized evidence is
  applied and the affected controls are rerun.
- Provider confidence is retained as provenance and remains non-decisional.
- Approved-fact retrieval excludes open review, raw model responses, and
  unapproved suggestions without an override.

## Deliberate residual integration work

The following remain client-dependent work, not stale omissions or implemented
claims:

- Selection and authorization of genuinely independent extraction and HTR
  providers where independent consensus is required.
- Evaluation on a representative client-approved golden set before any
  production accuracy, auto-acceptance, or reduced-review claim.
- Client-specific privacy/retention decisions, mappings, amendments, and target
  CRM receiver/load reconciliation.
- Client-specific visual detectors, additional OCR/HTR adapters, and any public
  tenant-isolated retrieval deployment.
- Production application of the client's eventual decisions followed by the
  complete rerun and final-review sequence.

## Prioritized improvements to reduce client review

The safe route to less review is better evidence, narrower deterministic rules,
and measured abstention—not a lower global confidence threshold. The first four
recommendations and the bounded control-exception proposal lane are implemented;
the remaining upgrades are prioritized next:

1. **Implemented — golden-set evaluation harness.** Measure precision, recall, false
   auto-accepts, abstention, exception volume, review minutes, cost, and latency
   by template, field, risk category, and provider. No review-reduction policy
   should advance without protected-field precision and false-accept evidence.
2. **Implemented — template fingerprint and drift registry.** Bind each approved source layout
   to an exact fingerprint and mapping version. Reuse deterministic mappings for
   exact matches; turn a changed layout into one grouped client decision instead
   of repeated row-level questions.
3. **Implemented — client-decision compiler with impact preview.** Convert an approved
   proposal into a human-reviewable append-only registry/amendment patch that
   shows affected documents, fields, dollars, and required rerun lanes. Keep the
   final write operator-authorized, but remove manual transcription and scope
   ambiguity.
4. **Implemented — deterministic JSON evidence graph and iterative proposal overlay.** Index
   retained document claims and reference proposals with exact, hash-bound
   provenance. Reusable `build`, `query`, `path`, `candidates`, and
   `contradictions` commands preserve conflicts and unresolved endpoints. The
   configured-primary/configured-buddy lane adds bounded immutable proposal overlays;
   inferred states never substitute for authoritative GL, payment-system, or
   client-authorization evidence.
5. **Constraint-backed proposal solver.** Expand unique arithmetic, date-range,
   currency, identifier-format, and relationship proofs. Emit an amendment only
   when one source-backed solution is possible; otherwise explain the remaining
   candidates in one review card.
6. **Risk- and field-specific provider routing.** Use deterministic/native-text
   paths for known low-risk templates, independent providers for material or
   conflicting fields, and specialist HTR only for detected handwriting regions.
   Preserve independence and protected-item gates.
7. **Calibrated per-family carry-forward.** After golden-set acceptance, define
   narrow thresholds by reason, field, template, and evidence combination rather
   than one model-confidence score. Unknown categories stay protected.
8. **Review ergonomics and telemetry.** Show representative evidence, affected
   count/value, conflict family, proposed rule, dependencies, and expected rerun
   impact. Track decisions per hour and recurring root causes so engineering
   effort targets the review families that cost the client most.
9. **Target connector and load reconciliation.** Once a target CRM is selected,
   implement idempotent receive/reject/rollback/reconcile evidence. This removes
   the final manual transfer without weakening the approved-fact boundary.

The implemented controls are ready for client-authorized inputs. Their value can
be claimed only after the representative golden set, template approvals, and
operator decisions exist and the required reruns pass.

## What this audit could not see

Amended August 28, 2026, after a complete trial run.

This audit read the repository. Two classes of defect survived it, and both
needed the pipeline to be *run* rather than *reviewed*:

- **Lanes that were never invoked.** The audit checked that every command
  exists, is indexed, and carries help text. It could not check whether a run
  used them, because nothing measured that. A full trial reached CRM staging
  with fourteen lane families never invoked. `run_lane_coverage.py` now measures
  it, and `agent_surface_check.py` fails when a command is neither a catalogued
  lane nor explicitly exempt.

- **Documented commands nobody executed.** The audit verified that operating
  guidance named real scripts and that every argument carried help text. It did
  not run the commands. Eleven invocations in the operating skill were rejected
  by the parsers they described -- wrong input artifacts, missing required
  positionals, options that do not exist -- and two of those wrong inputs were
  also written into the commands' own `--help`. `agent_surface_check.py` now
  parses every documented invocation.

The general lesson holds for the next audit: a control surface that is only read
is verified against its own description. Both checks above exist so that reading
the repository and running it converge.

## Verification

Current measured verification belongs in `HANDOFF.md` after the strict quality
and acceptance gates complete. This audit deliberately avoids treating its
historical counts as current: a later code change must rerun the gate and update
the handoff evidence. The four PDFs tracked at audit time were regenerated; all
62 pages were rendered to images and visually inspected for clipping,
overlap, margins, tables, headers, wrapping, and legibility. No client data,
provider responses, credentials, or generated run outputs are part of this
audit change.
