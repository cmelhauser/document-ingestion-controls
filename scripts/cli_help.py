#!/usr/bin/env python3
"""One canonical help vocabulary for the options that recur across the CLIs.

`references/command-line-reference.md` promises that "the executable parser is
the exact contract" and tells an operator to run `--help` before operating a
command. That promise only holds if every argument actually explains itself.
Thirty-two option names carry the same meaning in dozens of parsers, so their
wording belongs in one place: describing `--raw-dir` separately in fourteen
files is how fourteen descriptions drift apart.

Commands call :func:`apply_shared_help` immediately before ``parse_args``. It
only fills gaps, so a command whose option genuinely differs from the shared
meaning states its own ``help=`` inline and keeps it.

`scripts/release_check.py` rejects any argument that neither carries inline help
nor appears here, so a new option cannot ship undocumented.
"""

import argparse

# Artifact destinations. The no-clobber and evidence rules behind these are in
# references/artifact-contracts.md; the wording here states the invariant an
# operator needs at the point of use rather than restating that contract.
ARTIFACT_OPTION_HELP = {
    "--out": "Destination path for this command's primary retained artifact.",
    "--out-dir": "New directory for this command's retained artifacts. An existing directory is refused.",
    "--exceptions": (
        "Destination path for the exception artifact naming every input this command could not "
        "resolve. Absence of exceptions is a result, not a formality."
    ),
    "--exceptions-out": (
        "Destination path for the exception artifact naming every input this command could not "
        "resolve."
    ),
    "--raw-dir": (
        "Directory retaining the raw provider response for every request, as provenance. Raw "
        "responses stay with the operator and never enter a client package."
    ),
    "--adapter-out": (
        "Destination path for the versioned, hash-bound adapter handoff that downstream controls "
        "consume."
    ),
    "--handoff-out": (
        "Destination path for the versioned, hash-bound handoff this lane produces for downstream "
        "controls."
    ),
    "--report": "Destination path for this command's report artifact.",
    "--log": "Destination path for the reversible decision log this command appends.",
}

# Provider selection and request shaping. Every one of these is retained as run
# provenance; none of them is ever a term in a control decision.
PROVIDER_OPTION_HELP = {
    "--model": "Model identifier for the configured provider. Retained as run provenance.",
    "--provider": "Provider serving this lane. Independence is resolved to the model vendor behind any router.",
    "--final-provider": "Provider serving this lane's final pass.",
    "--final-model": "Model identifier for this lane's final pass.",
    "--buddy": "Second, independently configured provider that confirms the primary lane's proposals.",
    "--buddy-provider": "Provider for the buddy lane. It must differ from the primary provider.",
    "--buddy-model": "Model identifier for the buddy lane.",
    "--primary": "Primary configured provider for this lane.",
    "--reasoning-effort": (
        "Reasoning effort requested from the model. Retained as provenance; never a decision term."
    ),
    "--credential-env": (
        "Name of the environment variable holding the provider credential. The variable name is "
        "retained; its value is never copied into an artifact."
    ),
    "--project-id": "Google Cloud project billed for and serving the request.",
    "--location": "Google Cloud region serving the request.",
    "--timeout-seconds": "Per-request provider timeout, in seconds.",
    "--max-retries": "Maximum bounded retries per request after a transient provider failure.",
    "--retry-backoff-seconds": "Initial delay, in seconds, between bounded retries.",
    "--max-backoff-seconds": "Upper bound, in seconds, on retry backoff growth.",
    "--max-workers": "Maximum concurrent in-flight provider requests.",
}

# Hard resource bounds. These are safety limits, never accuracy guarantees:
# work that exceeds one is retained as an explicit exception, never truncated
# into a passing result.
LIMIT_OPTION_HELP = {
    "--max-pages": "Maximum retained pages this invocation may submit.",
    "--max-pdf-bytes": (
        "Maximum bytes accepted for one retained page PDF. A larger page is retained as an "
        "explicit exception rather than truncated."
    ),
    "--max-text-chars": "Maximum characters of native page text submitted in one request.",
    "--max-context-bytes": (
        "Maximum UTF-8 bytes of context in one request packet. Work that cannot fit is retained "
        "as an explicit exception."
    ),
    "--max-documents": "Maximum source documents this invocation may consider.",
    "--limit": "Maximum number of results to return.",
    "--iterations": "Maximum bounded passes this lane may run.",
    "--max-iterations": "Maximum bounded passes this lane may run.",
}

# Inputs whose meaning is fixed by an artifact contract rather than by the
# command that happens to read them.
INPUT_OPTION_HELP = {
    "--registry": (
        "Approved registry of client-authorized rules. Only an exact approved rule may map a "
        "source label."
    ),
    "--context": (
        "Hash-bound reasoning-only client context. It may explain terminology or priorities and "
        "is never source evidence, authorization, or independent consensus input."
    ),
    "--client-context": (
        "Hash-bound reasoning-only client context. It may explain terminology or priorities and "
        "is never source evidence, authorization, or independent consensus input."
    ),
    "--consensus": "Retained consensus artifact this command reads.",
    "--records": "Retained extracted-record artifact this command reads.",
    "--graph": "Retained deterministic evidence-graph artifact this command reads.",
}

# Behaviour switches that cross a boundary or change only the console.
CONTROL_OPTION_HELP = {
    "--enable": (
        "Explicitly enable this disabled-by-default lane. Without it the command refuses before "
        "performing any external work."
    ),
    "--quiet": (
        "Suppress the console summary only. Retained artifacts, findings, and exit status are "
        "unchanged."
    ),
    "--resume": (
        "Resume a previous run through its verified checkpoint. The checkpoint hashes must match "
        "the current inputs."
    ),
    "--retry-exceptions": (
        "Retry only the retained exceptions named by a previous run, as a separate no-clobber "
        "overlay. The original run is never overwritten."
    ),
}

# Positional arguments whose meaning is identical in every parser that takes
# them. A positional whose meaning varies by command states its own help.
POSITIONAL_HELP = {
    "manifest": "Retained run manifest identifying every page artifact and its hash.",
    "database": "Path to the approved-fact retrieval SQLite database.",
    "context": (
        "Hash-bound reasoning-only client context artifact. Never source evidence, authorization, "
        "or independent consensus input."
    ),
    "queue": "Retained final-review queue artifact.",
    "canonical_export": "Approved canonical export envelope.",
    "discovery": "Retained discovery-proposal artifact this command reads.",
    "layout": "Retained layout artifact this command reads.",
}

SHARED_OPTION_HELP = {
    **ARTIFACT_OPTION_HELP,
    **PROVIDER_OPTION_HELP,
    **LIMIT_OPTION_HELP,
    **INPUT_OPTION_HELP,
    **CONTROL_OPTION_HELP,
    **POSITIONAL_HELP,
}


def apply_shared_help(parser):
    """Fill in help for recurring arguments that did not state their own.

    Only empty help is filled, so a command that means something different by a
    shared option keeps its inline wording. Subparsers are walked too, because
    the subcommand parsers are where most repetition lives.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for subparser in action.choices.values():
                apply_shared_help(subparser)
        if action.help:
            continue
        name = action.option_strings[0] if action.option_strings else action.dest
        text = SHARED_OPTION_HELP.get(name)
        if text:
            action.help = text
    return parser
