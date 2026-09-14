# Keeping the repository green

This is repository upkeep, not run operation. Changing code, tests, or contracts
is governed by the root [`SKILL.md`](../../../SKILL.md) and
[`AGENTS.md`](../../../AGENTS.md).

## The full gate

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHON_BIN=.venv/bin/python bash scripts/quality_gate.sh
```

Run it in a normal terminal, CI worker, or other process manager without an
interactive-output capture deadline. The script imposes no wall-clock cutoff; CI
has its own 45-minute job limit.

It requires dependency integrity, all tests, **100% statement and branch
coverage**, Ruff lint and format, and release validation. Do not report 100%
unless the strict report says so.

## The individual checks

```bash
python scripts/release_check.py                 # structure, docs, links, contracts
python scripts/agent_surface_check.py           # operating instructions vs the code
python scripts/branch_state.py --out branch_state.json
```

`release_check.py` enforces, among other rules:

- every `.env.example` setting is documented in `references/runtime-configuration.md`
  with a matching default, and nothing is documented that does not exist;
- every argparse entry point is indexed in `references/command-line-reference.md`;
- **every command-line argument explains itself**, either with inline `help=` or
  through the shared vocabulary in `scripts/cli_help.py`;
- `HANDOFF.md` does not describe finished work as pending;
- a side-channel loader returns its rejected rows rather than skipping them.

`agent_surface_check.py` checks every script named in an agent surface — the root
instruction files and **every file in this skill folder** — against the live
scripts and the CLI index. It runs in the release gate and in the repository's
Claude Code PostToolUse hook, so renaming a script cannot leave stale operating
instructions behind. The ChatGPT equivalent is the `after_code_change` step in
[`agents/openai.yaml`](../../../agents/openai.yaml).

## Branches

Read [`BRANCHING.md`](../../../BRANCHING.md) first. `main` requires linear
history, so merged work is squashed or rebased and is **not** an ancestor of
`main` — `git branch --merged` reports merged work as unmerged and cannot be used
to decide whether a branch is safe to delete.

```bash
python scripts/branch_state.py --out branch_state.json
```

Each branch resolves to `merged_by_ancestry`, `merged_by_patch`,
`merged_by_subject`, or `unresolved`. The first three are safe to delete; an
`unresolved` branch holds work that is not on `main` and must be inspected, never
deleted on the strength of its name. The report never deletes anything.

## Mutation testing

```bash
python scripts/mutation_check.py --max-surviving 0 --report mutation.json
```

The 100% branch gate has a real blind spot: coverage.py records no arcs for
ternary arms or boolean short-circuit operands, so those decisions are executed
without being *tested*. Mutation testing is what would close it.

`--max-surviving` is required rather than defaulted, and the runner reads its
module set from `[tool.mutmut]`, refuses a module that is not configured, treats
a non-result exit code as failure, and refuses to infer a count. That strictness
exists because an earlier version reported zero surviving mutants across twelve
modules while the tool had not run at all — a control reporting clean after
processing nothing, produced by the repository's own tooling.

It is **not** in CI: mutmut 3.x is TUI-oriented and stalls without emitting a
machine-readable summary in a non-interactive runner. Run it from a terminal. A
check that can only always-fail, or be made to pass by defaulting a count, is
worse than no check.

## Dependencies

`requirements.txt` and `requirements-dev.txt` declare direct dependencies
**without version pins**. The concrete, reproducible resolution lives in
`requirements.lock`, which `pip-compile` generates with hashes and which both
gating CI jobs install with `--require-hashes`.

Compile the lock with **Python 3.12**, the version CI uses; a lock compiled on a
different interpreter produces a different file and fails the lock-diff job.

```bash
python3.12 -m piptools compile --generate-hashes --upgrade \
  --output-file requirements.lock requirements-dev.txt
.venv/bin/python -m pip install --upgrade -r requirements-dev.txt
```

Then run the full gate: an upgrade is not verified until the suite, coverage,
Ruff, and the release check all pass on it. The `supply-chain` CI job recompiles
the lock and diffs it, so an uncommitted upgrade is caught; `pip-audit --strict`
runs against the lock.

`pip check` finds broken dependency graphs but **not** drift between the lock and
what is installed. Compare them directly when a run behaves unexpectedly:

```bash
.venv/bin/python -m pip freeze | grep -iE '^(openai|google-genai|pikepdf|pillow|numpy|xlsxwriter)='
```

## Generated documents

When a tracked Markdown source changes:

```bash
bash scripts/generate_docs.sh      # Markdown -> LaTeX
bash scripts/render_docs.sh        # LaTeX -> PDF
pdftoppm -png -r 80 docs/DOC.pdf OUTDIR/page
```

Then look at every changed page: margins, table boundaries, headers, wrapping,
legibility. A PDF that has not been looked at is not ready to hand over, and the
release check verifies that each `.tex` still declares its Markdown source hash.

## Before you claim it is done

Read the artifacts, not the console. `release_check.py` says `"status": "ready"`,
`agent_surface_check.py` says `"status": "ready"`, and the coverage report says
100% for both statements and branches. Anything less is a partial result and
should be reported as one.
