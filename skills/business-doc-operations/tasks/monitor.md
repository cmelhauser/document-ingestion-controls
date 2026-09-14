# Monitoring a run

## Live progress

```bash
python scripts/run_monitor.py RUN --out RUN/monitor.json --interval 15 \
  --expected-pages 4200 --required-service openai --required-service google \
  --stop-when-complete
```

It counts retained responses and service summaries and writes an atomic
snapshot. It never prints or copies credentials or response content, so the
snapshot is safe to share with the operator team.

`--once` takes a single snapshot and exits — use it in a script or a check.
`--required-service` is repeatable and names a service that must appear before
the run counts as complete, which is what makes `--stop-when-complete` mean
something. `--price-file` supplies a JSON price table; the resulting spend figure
is **indicative, not billing**.

## Provider health, while there is still a run to save

```bash
python scripts/run_sentinel.py watch RUN --interval 30 --fail-fast
```

Run this beside the run. It reads the retained raw responses, which is where a
failure states its own cause, and classifies what it finds: a **credential**
fault, a **configuration** fault, or a **transient** one. `--fail-fast` exits
non-zero on the first two, so a supervising script can stop the run.

This exists because a full trial spent a corpus before anyone noticed Document
AI returning HTTP 404 on all 18 pages. The evidence was on disk the whole time;
nothing read it until the run was over.

The distinction it draws is the useful part. A transient failure is what each
lane's own bounded retry already handles, and stopping for one would make the
sentinel the outage. A credential or configuration fault will repeat on every
remaining page, so every page you let it run is money spent on a known failure.

`--once` takes a single snapshot for a script or a check; add `--out` to retain
it. It costs nothing to run: no provider is contacted and no model is called.

It counts every retained failure in the directory you point it at, including
ones from an earlier pass. That is the append-only rule working: a failed
reading is kept, not deleted, so a lane you re-ran into a new output directory
leaves the old failures in place and they keep being counted. Point `watch` at
the run directory for a fresh run, and at the new pass's own directory when you
are checking a retry:

```bash
python scripts/run_sentinel.py watch RUN/providers/raw/docai2 --once
```

For spend and provider behaviour:

```bash
python scripts/llm_usage_report.py RUN --out RUN/usage.json
```

This reads retained artifacts only — it does not contact a provider. It reports
model selection, tokens where available, cache behaviour, attempts, rate-limit
events, delays, skipped work, and exceptions.

## What to watch for

| Signal | What it means |
|---|---|
| Retained-response count flat while the run continues | The provider circuit may be open. Check for `ProviderQuotaExhausted` in the exception artifact; with `LLM_FAIL_FAST_ON_PROVIDER_QUOTA` the remaining work is being retained for a separate retry rather than called. |
| `cache_hit: true` on responses you expected to be fresh | The run is replaying `LLM_CACHE_DIR`. Correct for a resumed run; wrong if you intended a fresh reading. |
| Rising delays with no rising throughput | The shared throttle is spacing requests. Check the effective per-request ceiling in `references/runtime-configuration.md` before changing any limit. |
| Exceptions climbing steadily | Normal. Read them — the distribution tells you what the corpus is like far earlier than the final queue does. |
| A service you required never appears | That lane never ran. A run that "completed" without it did less than you think. |
| Every page of one lane is an exception | Not review work — a broken lane. `run_sentinel.py watch` names the cause; a lane that read nothing corroborates nothing. |

## What monitoring cannot tell you

Progress is not correctness. A run that completes every stage with zero provider
failures can still be blocked at the final gate, and that is the system working.
Do not report a run as successful because it finished.

The monitor counts artifacts. It cannot tell you whether a control *compared*
anything — for that, read `coverage` and `corroborated` in the artifact itself.

Neither one can tell you about a lane that was never invoked, because a lane
that never runs writes nothing to count. That is
[`run_lane_coverage.py`](../../../scripts/run_lane_coverage.py), and it belongs
at the end of every run:

```bash
python scripts/run_lane_coverage.py RUN --out RUN/lane_coverage.json
```
