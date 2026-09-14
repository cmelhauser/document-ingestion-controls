# Delegating work to subagents

The repository override for orchestration cost is
[`references/agent-cost-controls.md`](../../../references/agent-cost-controls.md).
Read it before spawning anything. This page is the operating summary.

## The default is not to delegate

Bounded, tightly coupled work stays with the main agent. A subagent starts cold
and re-derives context the main agent already has, so delegation pays only when
the work is genuinely separable and the context cost is smaller than the work.

Batch related changes rather than spawning one agent per change, and run full
gates at stable batch boundaries rather than after every edit.

## When delegation does pay

| Situation | Why it separates cleanly |
|---|---|
| Sweeping many files to find where something lives | The answer is a short list; the file dumps are not needed by the caller. |
| Auditing a completed branch independently | Independence is the point, and a cold start is a feature rather than a cost. |
| Reproducing a failure in isolation | The reproduction is self-contained and the transcript is noise to the main task. |
| Long-running verification with a narrow result | The caller needs the verdict, not the intermediate output. |

Give each subagent isolated context and explicit model and effort routing —
choose the least costly model adequate to the task. Reserve one high-capability
independent review for the completed branch.

## Orchestrating a run across agents

Everything above is about delegating *repository* work. Running the pipeline is a
different question, and the honest answer is that most of it cannot be
parallelised: the phase gates are a chain, and consensus cannot start until every
extraction lane it reads has finished.

What does parallelise is the expensive part. The Phase 3 provider lanes write to
separate paths and share no state beyond read-only pages:

| Lane | Writes |
|---|---|
| `llm_adapter.py --lane consensus_primary` | `RUN/providers/primary*` |
| `llm_adapter.py --lane consensus_secondary` | `RUN/providers/secondary*` |
| `google_document_ai_adapter.py` | `RUN/providers/docai*` |
| `google_handwriting_ocr.py` | `RUN/htr/*` |
| `table_comprehension_corpus.py` | `RUN/tables/*` |

Running these concurrently is safe on rate limits, and the reason is worth
knowing: `RequestThrottle` coordinates request and token budgets **across local
processes** through `LLM_THROTTLE_DIR`, keyed by provider. Two lanes on one
vendor share that vendor's budget rather than each assuming it owns the whole
thing. Point every concurrent lane at the same `LLM_THROTTLE_DIR` or the
coordination does not happen.

One thing does not coordinate. The provider quota circuit opened by
`LLM_FAIL_FAST_ON_PROVIDER_QUOTA` is process-local, so each concurrent lane
discovers an exhausted quota separately and spends one failed call learning it.
That is a small, acceptable cost, but it means "one lane reported a quota
failure" does not mean the others have stopped.

### What an agent adds over `&`

Backgrounding five commands already gets the wall-clock win. An agent per lane
earns its cost only when there is judgement in the loop: watching a long lane,
triaging a provider failure, and re-running the exception subset rather than the
whole corpus. Give it one lane, its own output paths, and a narrow brief.

### What an orchestrating agent must never do

- **Enable a lane.** Provider-calling lanes are disabled by default because
  enabling one spends the operator's money. A lane runs because the operator
  asked for it, never because an agent judged it useful.
- **Start a downstream phase early.** Consensus reads every extraction lane. An
  agent that starts it when "most" lanes are done produces a consensus over a
  partial corpus, which is exactly the silent under-count rule 9 exists to stop.
- **Substitute for independence.** Two agents driving the same model are one
  reading. Independence is a property of the model vendor, not of how many
  processes or agents were involved.
- **Reconcile lanes itself.** Merging two lanes' outputs is `consensus.py`'s job
  and it refuses same-vendor pairs by design. An agent that summarises agreement
  between lanes has produced a claim with no control behind it.

### A workable shape

Run the independent Phase 3 lanes concurrently, each with its own agent, its own
output paths, and a shared `LLM_THROTTLE_DIR`. Have every agent report back the
artifact paths, its exception count, and anything that refused. Then run
consensus and everything after it sequentially, in the main agent, reading the
artifacts rather than the reports.

## What a subagent must never be trusted to do

A subagent's report is a **proposal**, exactly like a provider response:

- It cannot approve a fact, clear a control, or close a review item.
- It cannot substitute for independent consensus. Two subagents on the same model
  are one reading, for the same reason two prompt roles are.
- Its summary is not evidence. If it says a gate passed, read the artifact.

Do not accept "the subagent confirmed it" as a verification step. Re-run the
deterministic control and read its output.

## Running commands from a subagent

Anything a subagent runs is subject to every rule in this skill: a new output
directory per run, no hand-edited artifacts, no client folder assembled by
copying files. A subagent that hits a `no-clobber` refusal must report it, not
work around it.

Provider-calling lanes are disabled by default for a reason. Do not have a
subagent pass `--enable` on a lane the operator did not ask for — that spends
money and crosses an external boundary on the operator's account.

## Reporting back

The caller sees only what is relayed. Relay the verdict, the artifact paths, the
exception counts, and anything that refused. Do not relay a summary that reads as
success when the underlying artifact reports open findings.
