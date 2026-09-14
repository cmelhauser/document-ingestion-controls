# Approved registries

Four documented lanes read a registry from this directory, and three of them
require one. It did not exist, so every command in the operating skill that
named `registry/templates.json`, `registry/mappings.json`, or
`registry/allocation.json` failed a first run with
`[Errno 2] No such file or directory` — including the drift analysis, table
reconciliation, and slot equivalence.

The files here are the empty first-run baseline, byte-for-byte what each loader
already builds when no `--registry` is given:

| File | Read by | Holds |
|---|---|---|
| `templates.json` | `template_drift.py analyze` | Approved source layouts |
| `mappings.json` | `schema_discovery.py`, `table_comprehension*.py`, `independent_table_reconcile.py` | Approved label→field mappings |
| `allocation.json` | `allocation_policy.py` | Approved commission allocation rules |

**Empty is the correct first-run state, not a gap.** With no approved template
every observed layout is new, and drift fails closed on all of them — that is
the control working, not a misconfiguration. With no approved mapping rule,
nothing is mapped without a client decision.

**Nothing here is authorized by being written here.** A rule enters a registry
only through the documented `registry-update` verb, which takes the discovery
artifact *and* the client's decisions and writes a **new** version to a fresh
path (`registry/mappings.v2.json`). Registries are versioned forward, never
edited in place, and `registry_version` must increase. Promote a new version by
pointing `--registry` at it once the client has approved its contents.
