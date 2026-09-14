# The evidence graph and the relationship lanes

The graph is a **deterministic JSON overlay** built from retained artifacts. It
is not a database, it is never edited in place, and it holds no fact that its
source artifacts did not already hold. Every later pass builds a *new* overlay.

## Build

Build the typed manifest first. It is not the operations manifest -- the graph
needs an explicit artifact type per file, because a misclassified artifact would
put the wrong claims in the graph under right-looking provenance and no later
control could tell.

```bash
python scripts/evidence_graph.py manifest --run-id RUN_ID \
  --consensus-records RUN/controls/consensus.json \
  --validated-records RUN/controls/validation.json \
  --out RUN/graph_manifest.json

python scripts/evidence_graph.py build RUN/graph_manifest.json \
  --out RUN/graph/graph.json --exceptions RUN/graph/exceptions.json
```

Every artifact must sit inside the manifest's own directory, so write the
manifest at the run root. `manifest --help` lists every supported type.

If every source artifact contributed nothing, the build **refuses** rather than
writing an empty graph that would answer "no results" to every question,
indistinguishably from the facts being absent. `--allow-empty-graph` retains a
deliberately empty overlay when that is genuinely what you want — which is rare,
and worth stating why.

Two nodes disagreeing about the same identity do not silently overwrite one
another: the conflict is retained as a `graph_node_property_conflict` exception
and the node is marked protected.

## Query

```bash
python scripts/evidence_graph.py query RUN/graph/graph.json --node-type invoice \
  --property vendor=ACME --limit 50 --out RUN/graph/vendor_invoices.json
python scripts/evidence_graph.py path RUN/graph/graph.json \
  --from invoice:INV-1001 --to payment:PAY-77 --max-depth 6 --out RUN/graph/path.json
python scripts/evidence_graph.py candidates RUN/graph/graph.json \
  --from invoice:INV-1001 --relationship settles --limit 20 --out RUN/graph/candidates.json
python scripts/evidence_graph.py contradictions RUN/graph/graph.json \
  --out RUN/graph/contradictions.json
```

`--property` is repeatable and all supplied properties must match.
`candidates` ranks by **provenance count** — how much retained evidence supports
the link — and declares its edge direction. It is a ranked proposal list, not an
answer. `contradictions` is the one to read before trusting anything else.

## Schema surfaces

```bash
python scripts/evidence_graph.py schema RUN/graph/graph.json --out RUN/graph/schema.json
python scripts/evidence_graph.py schema-surface RUN/graph/graph.json \
  --consensus RUN/controls/consensus.json --out RUN/graph/surface.json
python scripts/evidence_graph.py schema-recovery-scope RUN/graph/graph.json \
  --consensus RUN/controls/consensus.json --topic amounts --signal-type missing_field \
  --out RUN/graph/recovery_scope.json
```

`schema` emits observed claims. `schema-surface` inventories retained candidates
and signals. `schema-recovery-scope` creates a selected, source-hash-bound
worklist for a **separate** recovery overlay — it creates no graph facts itself.
`schema_discovery.py discover --graph-schema-inventory` accepts this output as
corpus context, never as mapping evidence.

## The relationship lanes

All are disabled by default and all produce proposals. They run in this order,
and later ones require the earlier artifacts to exist.

### 1. Preserve the client's words

```bash
python scripts/client_review_context.py RESPONSES.json --records RUN/controls/consensus.json \
  --max-documents 200 --out RUN/client/context.json
# or, for comments that did not come back in a workbook — freeform, and mixable
# in one call, because clients send whatever they already have:
python scripts/client_input_comments.py NOTES.csv CALL_NOTES.pdf EMAIL.txt \
  --records RUN/controls/consensus.json --out RUN/client/context.json
```

`client_input_comments.py` accepts **JSON, CSV, TSV, TXT, Markdown, and PDF**, and
several files at once. A CSV or TSV needs a comment column (`client_comment`,
`comment`, `comments`, `note`, or `text`) and every other column is preserved
beside it. A PDF is read from its **text layer only**: a scanned page yields
nothing and is refused rather than contributing an empty context, because a
scanned page is document evidence that belongs in intake with provenance, not
reasoning-only context. Each comment is bound to its source filename and that
file's SHA-256.

Both produce the same hash-bound `client_review_context_v1`. Every comment is
preserved verbatim. It is **reasoning-only context**: it may explain terminology,
priorities, or questions, and it is never source evidence, authorization,
independent consensus input, or control clearance.

### 2. Bounded reference discovery

```bash
python scripts/client_review_inference.py RUN/client/context.json --enable \
  --batch-size 10 --max-batches 20 \
  --out RUN/client/inference.json --exceptions RUN/client/inference_exceptions.json \
  --raw-dir RUN/client/raw/inference
```

Every candidate must cite a source document. `--self-check-rounds` re-reads with
the same model: that improves a proposal and is **not** independence.

### 3. Iterative primary/buddy relationships

```bash
python scripts/client_review_iterative.py RUN/client/context.json \
  --records RUN/controls/consensus.json \
  --primary-out RUN/relationships/primary.json --buddy-out RUN/relationships/buddy.json \
  --exceptions RUN/relationships/exceptions.json --raw-dir RUN/relationships/raw
```

Independent primary and buddy providers over the same source-bound packet,
configured through `.env`. Independence is resolved to the **model vendor**, the
same way consensus resolves it: an OpenAI primary confirmed by a router serving
`openai/...` is one set of weights wearing two names, and is refused before a
call is made. A routed slug with no vendor prefix is refused too, because it
cannot be proven independent.
Capped at three passes. A pass stops when it adds no new source-backed,
buddy-confirmed relationship — the documented material-yield convergence
threshold.

An exhausted provider batch retains its **exact source document IDs** so a
separate no-clobber recovery run can pick them up. `--finalize-existing`
reconstructs a stopped run's completed passes from retained raw packets without
making any provider call.

### 4. Cross-packet discovery and verification

See [`cross-checking.md`](cross-checking.md). It runs after the in-packet
artifacts and the graph exist, and rebuilds the graph as a new overlay.

### 5. Exception resolution

```bash
python scripts/client_review_exception_resolution.py RUN/controls/consensus.json --enable \
  --exceptions RUN/pages/reassembly_exceptions.json \
  --exceptions RUN/controls/validation_exceptions.json \
  --exceptions RUN/controls/attribution_exceptions.json \
  --client-context RUN/client/context.json \
  --out RUN/review/exception_proposals.json \
  --exceptions-out RUN/review/exception_resolution_exceptions.json \
  --raw-dir RUN/review/raw/exception_resolution
```

`--exceptions` is repeatable and takes reassembly, validation, and attribution
artifacts. It **never clears the originating control** — rerun that deterministic
control after reviewing any append-only overlay.

Confirm its summary accounts for **every** input finding. Unmatched, rejected,
capped, oversized, and provider-failed work must still be present in the
companion artifacts. A retryable exception retains the exact original named
findings, kinds, and exception IDs; a retry may not reconstruct control context
from document IDs alone.

### Legacy

`client_review_consensus.py` is a deprecated compatibility replay for older
two-pass artifacts. Use it only to re-read an existing historical run.

## What none of this can do

Create GL or payment evidence. Authorize anything. Substitute for independent
extraction consensus. Close a completeness gap. The graph and every lane on this
page produce immutable, provenance-linked proposals.
