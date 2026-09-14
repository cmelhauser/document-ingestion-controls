# Client review and return ingestion

The returned workbook is **untrusted input**. It is validated against the issued
copy before anything is read from it, and even a valid return produces proposals
rather than facts.

## Issuing

Issuing consumes the **reduction**, not the raw queue. Before this step run the
gate, then card review, the full-dataset agent, grouping, safe consolidation, and
the simulated client comments -- see
[`review-reduction.md`](review-reduction.md). `client_review_package.py create`
reads `safe_review_consolidation.json`, so a run that skipped reduction has
nothing for it to read, and a run that skipped only the simulated pass issues
questions nobody has tried to answer.

The workbook is built from the safe-consolidation artifact, so run the reduction
lanes first — see [`review-reduction.md`](review-reduction.md).

```bash
python scripts/client_review_package.py create RUN/review/safe_review_consolidation.json \
  --out-dir RUN/client/issued
```

Keep the issued workbook exactly as sent. It is the reference the return is
validated against; if it changes, the return can no longer be checked.

## When the workbook comes back

Preserve both copies, separately and unchanged, in a new directory.

```bash
# 1. Preserve the editable responses without touching either workbook
python scripts/client_review_package.py preserve-responses RETURNED.xlsx \
  --issued-workbook ISSUED.xlsx --out RUN/client/responses_preserved.json

# 2. Validate the return against the issued copy
python scripts/client_review_package.py import-decisions RETURNED.xlsx \
  --issued-workbook ISSUED.xlsx --out RUN/client/decisions.json
```

The importer checks the exact worksheet and header contract, the complete group
set and order, fixed cells, controlled choices, protected decisions, editable
columns, ZIP safety bounds, XML entity declarations, and both workbook hashes.

A successful import proves only that the response conforms to the decision
surface that was issued. It proves nothing about the facts.

## Applying decisions

`compile` takes three retained inputs, in this order: the imported decisions,
the safe-consolidation artifact they were grouped from, and the explicit
append-only change catalog. `authorize` takes the compiled plan and the
authorization file an operator filled in — not the plan alone.

```bash
python scripts/client_decision_compile.py compile RUN/client/decisions.json \
  RUN/review/consolidation/safe_review_consolidation.json \
  registry/change_catalog.json \
  --authorization-template RUN/client/authorization_template.json \
  --out RUN/client/impact_preview.json
python scripts/client_decision_compile.py authorize RUN/client/impact_preview.json \
  RUN/client/authorization_completed.json \
  --out RUN/client/authorized_changes.json
```

Read the impact preview before authorizing. Authorization defaults to defer for
every item; an operator has to choose each change deliberately, in the
authorization file `compile` wrote as a template.

### Most change types still have no consumer

`compile` accepts six change types and `authorize` will sign any of them, but
**only two of them are carried into an artifact a later lane reads**:

| change type | applied by |
|---|---|
| `template_registry_rule` | `template_drift.py registry-update` |
| `document_type_rule` | `classification_amend.py` |
| `mapping_rule` | *nothing reads it from an authorized plan* |
| `amendment` | *nothing reads it from an authorized plan* |
| `allocation_policy_rule` | *nothing reads it from an authorized plan* |
| `source_quality_action` | *nothing reads it from an authorized plan* |

`exception_resolve.py` is the exception to that table: it reads an authorized
plan of **any** change type, because it does not apply the change at all. It
keys on each authorized patch's `impact.fields` and asks a narrower question --
does this authorization answer every finding still queued on the document? A
`mapping_rule` patch nobody applies can still resolve the finding that produced
it. That clears the document's `review_status`; it does not write the mapping.
See [`deliver.md`](deliver.md).

Beyond those, `template_drift.py` and `client_decision_compile.py` are the only
scripts that read an authorization artifact. Choosing an unconsumed type
produces a plan that compiles, an authorization that binds, and an apply step
that refuses:

```
Template drift control failed: authorization contains no template_registry_rule patches
```

That refusal arrives *after* an operator has signed, which is the wrong order to
discover it. **Pick the change type against its consumer before you compile.**

The registry commands that look adjacent do not help here:
`schema_discovery.py registry-update` and `allocation_policy.py registry-update`
both take `registry discovery decisions` — a discovery artifact plus decisions,
never an authorized plan — and `apply_mappings.py` applies an approved *mapping
registry* to consensus records. None of them reads the authorization.

A generalised `propose -> validate -> preview -> authorize -> apply -> reconcile`
loop is on the [roadmap](../../../references/business-data-platform-roadmap.md),
not in the pipeline.

### Carrying the decision into classification

A registered template does not reclassify anything. `classification_consensus.py`
reads only the provider handoffs — it has no registry input — so an approved
template registry is read by `template_drift.py analyze` and by nothing else. On
one run the client's answer was compiled, authorized, and written into the
registry, and not one document's family changed.

`classification_amend.py` is the step that closes it, and it applies both
`template_registry_rule` and `document_type_rule` patches. The two answer
different questions: a layout rule carries a `layout_signature` and names the
family inside it, while a document-type rule names the family in
`proposed_value` and carries no fingerprint -- because a client confirming what
a document *is* was never asked about its layout. Requiring a signature of the
document-type decision made the only client-answerable decision in one pack
unappliable: it compiled, the authorization bound, and the apply step refused
after the operator had signed 506 documents.

`classification_amend.py` is the step that closes it:

```bash
python scripts/classification_amend.py RUN/controls/classification_consensus.json \
  RUN/client_decision_authorized.json --out RUN/controls/classification_consensus_02.json \
  --exceptions RUN/controls/classification_exceptions.json \
  --exceptions-out RUN/controls/classification_exceptions_02.json \
  --records RUN/controls/records_with_mappings.json
python scripts/template_observations.py RUN/controls/consensus.json \
  --classifications RUN/controls/classification_consensus_02.json \
  --out RUN/controls/templates_02.json --exceptions RUN/controls/templates_02_exceptions.json
```

It appends a classification for each document the authorized
`template_registry_rule` patches name, and refuses three things: an
authorization that is not `operator_authorized`, a document accepted on
independent vendor agreement — that is evidence, and a client assertion does not
outrank it — and any document outside the authorized patches' own impact lists.
Amended entries carry `evidence: operator_authorized_client_decision` and an
empty `agreeing_vendors`, so they can never be read as vendor agreement.

**A document already typed by an earlier client decision is a different case,
and needs `--supersede-prior-client-decisions`.** It was being refused with the
same message, which was untrue about it: nothing there is vendor agreement, and
the same authority is correcting itself, which is the whole point of an
append-only amendment chain. On the commission run 136 of the 686 accepted types
came from the client's own round-1 decision, and their round-3 answer retyped
those same documents `payment_record` and `weekly_order_status_report`. The
chain compiled, the operator signed, and the amendment then refused — the
failure landing after the signature rather than before it. Without the flag the
refusal now names the whole set, the prior authorization, and the retyping it
would perform, so the decision is made once instead of once per document. With
it, the earlier acceptance is retained unedited and the correction beside it
carries `supersedes_authorization_id`. A repeated answer appends nothing and is
counted as `reaffirmed_prior_client_decisions`; reaffirming and correcting are
not the same event. Vendor agreement is never superseded, with or without the
flag.

**Pass `--records` too, or one answer types documents it was never about.** A
client answers one question for a whole group after seeing a few worked
examples. On the commission run a group of 30 was answered "Blank Page. Can be
ignored."; three of its members carried 46, 129 and 190 populated fields, two of
them commission lines with amounts. Nothing in this step looked, so all 30 would
have been typed from that answer. With `--records`, a member carrying more than
`--outlier-factor` times its group's median populated-field count is withheld
from the amendment and retained in `withheld_contradicted_by_content` — it is
not retyped and not guessed at, it stays an open question.

Only the richer side is withheld, and the asymmetry is deliberate: reading fewer
fields off a payment record does not make it something else, while a full
document typed from an answer given about empty ones is contradicted by its own
extraction. Withholding both tails flagged 33 of one real group's 128 members
and would have made the check noise.

Without `--records` the check does not run, and the summary says
`content_contradiction_checked: false` rather than reporting zero — a control
that examined nothing has not passed.

**Pass `--exceptions` too, or the client is asked again.** Appending an
acceptance does not close the classification exception that sent the document to
the client, and `final_review_queue.py` builds the gate from those exception
artifacts. `--exceptions-out` writes a copy in which each resolved entry is
marked `client_review_required: false` — the flag the gate already honours —
carrying the authorization that answered it. Nothing is deleted: the exception
stays in the artifact, because it was true.

On the run above this moved 136 documents out of `unknown` and `purchase_order`:
accepted rose 550 → 686, unresolved fell 166 → 30, all 166 exception entries were
retained with 136 marked answered, and regenerating templates collapsed five
observed layouts into three.

**Amend before you register.** A template's identity and fingerprint are derived
from its family and label set, so amending classification changes both. Registry
entries written from the *pre*-amendment layouts are orphaned by it — they cite
template IDs that no longer exist. Amend classification, regenerate templates,
then register the layout that results.

### Then rerun

Apply the authorized `template_registry_rule` entries — and **rerun**:

```
affected lane → consensus → arithmetic → validation
  → applicable business controls → completeness → final review → package
```

Nothing is applied until that rerun passes. Saying "the client approved it" does
not change an artifact.

### Where a correction to a client's wording belongs

`compile` rejects a catalog whose `decision_comment` differs from the client's
note, so a typo cannot be quietly tidied away on the way through. That is
deliberate, and it tells you where an operator correction goes:

* `decision_comment` carries the client's words **byte-exact**, typo included;
* `proposed_value` carries the operator's canonical value.

One run received `Commision Statement` on two groups and `Commission Statement`
on a third. `preserve-responses` retains all three spellings verbatim, both
catalog changes kept the misspelling in `decision_comment`, and only
`proposed_value` was normalised — with the reason and the retained spellings
recorded in the catalog and an amendment. Never edit the returned workbook or
the preserved responses to make them agree.

### Validate the return with the issued copy, not by hand

Pass `--issued-workbook` to `preserve-responses` and `import-decisions`. It
reports `lineage_status` and every `fixed_cell_differences` entry, which is the
check that matters: it proves the client edited only the editable columns.

Do not try to verify the binding by hashing files yourself. The workbook's
embedded `safe_consolidation_sha256` is computed over canonical content, **not**
over the artifact's bytes, so a `shasum` of `safe_review_consolidation.json` will
never match it and will look like tampering when nothing is wrong.

### An authorization is bound to one plan

`authorize` binds `compiled_plan_sha256`. Re-cutting the catalog produces a
different plan, and the previous authorization does not carry over — it needs a
fresh operator signature. Get the change type right before asking anyone to sign.

## What a batch decision can never clear

Arithmetic, provider/schema, reassembly, handwriting, identity, review-flag,
missing-field, and unresolved-evidence findings. Every one of these needs
evidence or a human, and `scripts/client_review/protection.py` fails closed on any
reason it does not recognize. If a client decision appears to clear one of these,
something is wrong — stop and read the protection reasons.
