"""Decide, per control, when a block is worth putting in front of a client.

Every control in this pipeline can block, and each block is a different kind of
ask. A single unresolved arithmetic finding is worth a question because it means
a document does not add up. A single low-priority formatting note is not worth
interrupting a client for. Hard-coding that judgement would be wrong for the next
engagement, so each block carries its own configured threshold and the lane
produces a pack only when the block clears it.

A threshold is a floor on *blocking* findings, never a filter on the queue. Items
below the floor stay exactly where they are, in the exhaustive queue and in the
run's exceptions; the threshold decides whether to ask now, not whether the work
exists.
"""

from __future__ import annotations

from typing import Any

from runtime_config import env_bool, env_int

# Every control that can block, with the setting suffix that tunes it and the
# default floor. Defaults are 1 -- ask about anything that blocks -- because a
# control that blocked and was never raised with anyone is the failure this lane
# exists to prevent. An engagement that wants less noise raises the floor
# deliberately rather than discovering the lane silently skipped a block.
REVIEW_BLOCKS: tuple[tuple[str, str, int], ...] = (
    ("extraction", "EXTRACTION", 1),
    ("consensus", "CONSENSUS", 1),
    ("arithmetic", "ARITHMETIC", 1),
    ("validation", "VALIDATION", 1),
    ("adjudication", "ADJUDICATION", 1),
    ("reassembly", "REASSEMBLY", 1),
    ("tables", "TABLES", 1),
    ("handwriting", "HANDWRITING", 1),
    ("identity", "IDENTITY", 1),
    ("attribution", "ATTRIBUTION", 1),
    ("completeness", "COMPLETENESS", 1),
    ("sampling", "SAMPLING", 1),
    ("schema", "SCHEMA", 1),
    ("slot_equivalence", "SLOT_EQUIVALENCE", 1),
    ("allocation", "ALLOCATION", 1),
    ("relationships", "RELATIONSHIPS", 1),
    ("delivery", "DELIVERY", 1),
)
BLOCK_NAMES = tuple(name for name, _, _ in REVIEW_BLOCKS)
# Which control raised an item is recorded in its review_source, which each lane
# spells in its own way. Matching on a substring keeps this readable and means a
# new lane whose source contains its control name is classified without a code
# change; anything unrecognised is reported as such rather than guessed at.
BLOCK_SOURCE_TOKENS: dict[str, tuple[str, ...]] = {
    # A provider lane's own exceptions arrive from primary_exceptions.json or
    # secondary_exceptions.json with a generic collection name. Without this they
    # classified as unclassified -- still triggering, but telling an operator
    # nothing about which lane produced them.
    "extraction": ("primary_exception", "secondary_exception", "adapter", "extraction"),
    # Consensus states its findings in its own vocabulary rather than naming
    # itself, so its flags and rules are matched directly.
    "consensus": (
        "consensus",
        "partial_disagreement",
        "no_majority",
        "single_engine",
        "printed_majority",
        "provider_extraction_exception",
    ),
    "arithmetic": ("arithmetic",),
    "validation": ("validate", "validation"),
    "adjudication": ("adjudicat",),
    "reassembly": ("reassembly",),
    "tables": ("table",),
    "handwriting": ("handwriting", "htr"),
    "identity": ("identity", "entity", "address"),
    "attribution": ("attribution",),
    "completeness": ("completeness",),
    "sampling": ("sampling",),
    "schema": ("schema_discovery", "schema"),
    "slot_equivalence": ("slot_equivalence",),
    "allocation": ("allocation",),
    "relationships": ("relationship", "graph"),
    "delivery": ("delivery", "canonical"),
}
UNCLASSIFIED_BLOCK = "unclassified"


def lane_enabled() -> bool:
    """Return whether the client review lane may run.

    Unlike the provider-calling lanes this one is on by default: it spends
    nothing and crosses no external boundary, and the reason those lanes are
    disabled -- enabling one is a spending decision -- does not apply here.
    """
    return env_bool("CLIENT_REVIEW_LANE_ENABLED", True)


def threshold_for(block: str) -> int:
    """Return the configured blocking-finding floor for one control."""
    for name, suffix, default in REVIEW_BLOCKS:
        if name == block:
            value = env_int(f"CLIENT_REVIEW_LANE_{suffix}_THRESHOLD", default)
            if value < 0:
                raise ValueError(f"CLIENT_REVIEW_LANE_{suffix}_THRESHOLD must be zero or greater")
            return value
    raise ValueError(f"unknown review block: {block}")


def classify(item: dict[str, Any]) -> str:
    """Name the control a review item came from."""
    source = " ".join(
        str(item.get(key, ""))
        for key in ("source_artifact", "review_source", "category", "reason", "flag", "rule")
    ).casefold()
    for block in BLOCK_NAMES:
        if any(token in source for token in BLOCK_SOURCE_TOKENS[block]):
            return block
    return UNCLASSIFIED_BLOCK


def blocking(item: dict[str, Any]) -> bool:
    """Return whether an item is a block rather than a retained observation."""
    if item.get("blocking") is False:
        return False
    disposition = str(item.get("disposition", "")).casefold()
    return "no_action" not in disposition and "informational" not in disposition


def evaluate(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Report which controls blocked, how hard, and which clear their threshold.

    An unclassified item is always reported as triggering. A finding whose origin
    could not be named is precisely the one nobody owns, and defaulting it to
    silence would let a new lane's output disappear from client review without
    anyone noticing.
    """
    counts: dict[str, int] = {}
    for item in items:
        if not blocking(item):
            continue
        counts[classify(item)] = counts.get(classify(item), 0) + 1
    blocks = []
    for block in (*BLOCK_NAMES, UNCLASSIFIED_BLOCK):
        count = counts.get(block, 0)
        if not count:
            continue
        threshold = 1 if block == UNCLASSIFIED_BLOCK else threshold_for(block)
        blocks.append(
            {
                "block": block,
                "blocking_items": count,
                "threshold": threshold,
                "triggered": count >= threshold,
            }
        )
    triggered = [block for block in blocks if block["triggered"]]
    return {
        "lane_enabled": lane_enabled(),
        "blocks": blocks,
        "triggered_blocks": [block["block"] for block in triggered],
        "triggered": bool(triggered),
        "blocking_item_count": sum(counts.values()),
    }
