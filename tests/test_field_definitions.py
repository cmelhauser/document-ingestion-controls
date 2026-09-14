"""Hold the canonical field definitions to the job they were added for.

Two independent vendors read the same 18-page corpus and filed the same printed
string under different canonical fields 397 times -- 39% of every consensus
exception the run produced, and the reason arithmetic could prove 0 of 18
documents. The schema named 50 line fields and defined none of them, so the only
thing either engine had to choose between `sales_amount` and
`commissionable_amount` was the field name.

These tests assert the definitions reach both engines by the strongest route
each API offers, and that they say what the rest of the repository already
means by those names. They deliberately do not assert prose.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

extraction_schema = importlib.import_module("extraction_schema")


def line_properties():
    """Return the line-item properties every adapter sends."""
    return extraction_schema.EXTRACTION_SCHEMA["properties"]["lines"]["items"]["properties"]


def test_every_field_that_collided_on_a_real_corpus_is_defined():
    """The fields two engines disagreed about placing are the ones defined."""
    # Measured from the trial run's exception artifact: the left field is where
    # one engine put a value, the right is where the other put the same string.
    collided = {
        "sales_amount",
        "commissionable_amount",
        "commission_amount",
        "applied_amount",
        "transaction_id",
        "item_code",
        "job_number",
        "project_number",
        "description",
        "customer_name",
        "dealer_name",
        "brand_name",
        "transaction_date",
    }
    assert collided <= set(extraction_schema.FIELD_DEFINITIONS)
    # line_number did not collide -- it broke. An engine told to stop repeating a
    # printed identifier across fields put the row's invoice number here instead
    # of its position, which silently renumbers every line in the run.
    assert "line_number" in extraction_schema.FIELD_DEFINITIONS
    assert "position" in extraction_schema.FIELD_DEFINITIONS["line_number"]
    # A definition is only worth carrying if it distinguishes the field from its
    # neighbours, so every one names something other than itself.
    for name, text in extraction_schema.FIELD_DEFINITIONS.items():
        assert text.strip() and text.strip()[0].isupper(), name
        assert len(text) > 60, name


def test_a_definition_travels_on_the_line_field_itself():
    """The schema is the strongest signal a structured-output API offers."""
    properties = line_properties()
    for name, text in extraction_schema.FIELD_DEFINITIONS.items():
        if name in extraction_schema.LINE_FIELDS:
            assert properties[name]["description"] == text
            # A description is added to the field, never in place of its type.
            assert properties[name]["anyOf"] == [{"type": "string"}, {"type": "null"}]

    # A field with no definition carries no description rather than filler text
    # that would dilute the ones that matter.
    assert "description" not in properties["upc"]
    # line_number is a line property but not a member of LINE_FIELDS, so a loop
    # over that tuple would silently skip the one field that needed defining.
    assert "line_number" not in extraction_schema.LINE_FIELDS
    assert (
        properties["line_number"]["description"]
        == (extraction_schema.FIELD_DEFINITIONS["line_number"])
    )
    assert properties["line_number"]["anyOf"] == [{"type": "integer"}, {"type": "null"}]

    # FIELD_DEFINITIONS is shared with the header glossary, and the header has
    # fields the line schema does not. A definition for one of those is carried
    # by the prompt and skipped here rather than crashing the schema build.
    extraction_schema.FIELD_DEFINITIONS["statement_total"] = "A header-only field."
    try:
        assert "statement_total" not in extraction_schema.line_schema()["properties"]
        assert "- statement_total: A header-only field." in extraction_schema.field_glossary()
    finally:
        del extraction_schema.FIELD_DEFINITIONS["statement_total"]
    assert set(properties) == {
        "line_number",
        "line_model_confidence",
        *extraction_schema.LINE_FIELDS,
    }


def test_the_header_gets_the_same_definitions_through_the_prompt(monkeypatch):
    """Header is a name/value array over an enum, with nowhere to hang one."""
    monkeypatch.delenv("LLM_DOCUMENT_FAMILY", raising=False)
    glossary = extraction_schema.field_glossary()
    for name, text in extraction_schema.FIELD_DEFINITIONS.items():
        assert f"- {name}: {text}" in glossary
    # Sorted, so two runs send byte-identical prompts and a cached response stays
    # valid for the request that produced it.
    names = [line.split(":")[0][2:] for line in glossary.splitlines()[1:]]
    assert names == sorted(extraction_schema.FIELD_DEFINITIONS)

    # The glossary defines fields; it does not instruct an engine on how to
    # allocate across fields it was given no definition for. That clause cost
    # more than it bought -- see the note above FIELD_DEFINITIONS.
    assert "exactly one field" not in glossary

    prompt = extraction_schema.extraction_instructions()
    assert prompt.startswith(extraction_schema.INSTRUCTIONS)
    assert glossary in prompt
    # A header field defined here is defined for its line twin too: one meaning,
    # one source.
    assert "dealer_name" in extraction_schema.HEADER_FIELDS
    assert "dealer_name" in extraction_schema.LINE_FIELDS

    # The family hint still appends after the glossary rather than replacing it.
    monkeypatch.setenv("LLM_DOCUMENT_FAMILY", "commission_statement")
    hinted = extraction_schema.extraction_instructions()
    assert glossary in hinted and "commission_statement'" in hinted


def test_the_definitions_match_what_the_rest_of_the_repository_already_means():
    """These write down an existing contract; they do not invent a new one."""
    definitions = extraction_schema.FIELD_DEFINITIONS
    # allocation_policy.py reads commissionable_amount as the base and
    # references/allocation-policy.md makes it the denominator of the effective
    # rate. The definition has to send the base there or the control stays dark.
    assert "commission_amount" in definitions["commissionable_amount"]
    allocation_policy = importlib.import_module("allocation_policy")
    assert "commissionable_amount" in allocation_policy.ROLES

    # document_value.py reads an accounting negative as a negative, so the
    # definition must not invite an engine to drop the sign.
    document_value = importlib.import_module("document_value")
    assert document_value.numeric("(662.89)") == -662.89
    assert "chargeback" in definitions["commission_amount"]

    # Nothing here authorizes inferring a date order from magnitude.
    assert "review flag" in definitions["transaction_date"]


def test_a_changed_schema_cannot_replay_a_response_shaped_by_the_old_one():
    """The cache keyed on the schema's name, which never changes when it does."""
    openai_adapter = importlib.import_module("openai_adapter")
    google_genai_adapter = importlib.import_module("google_genai_adapter")
    llm_runtime = importlib.import_module("llm_runtime")

    baseline = extraction_schema.schema_fingerprint()
    assert len(baseline) == 64
    assert baseline == extraction_schema.schema_fingerprint()

    # A change anywhere in the schema changes the fingerprint, and therefore the
    # cache key -- including a definition, which alters no field and no type.
    properties = line_properties()
    original = properties["sales_amount"].get("description")
    properties["sales_amount"]["description"] = "changed"
    try:
        changed = extraction_schema.schema_fingerprint()
        assert changed != baseline
        assert llm_runtime.cache_key({"schema_sha256": changed}) != llm_runtime.cache_key(
            {"schema_sha256": baseline}
        )
    finally:
        properties["sales_amount"]["description"] = original
    assert extraction_schema.schema_fingerprint() == baseline

    # Both adapters that cache a page extraction carry it.
    for module in (openai_adapter, google_genai_adapter):
        assert module.schema_fingerprint is extraction_schema.schema_fingerprint
        source = Path(module.__file__).read_text()
        assert '"schema_sha256": schema_fingerprint(),' in source


def test_a_split_commission_names_its_total_and_leaves_the_base_null():
    """One Commission heading can span several columns, and none of them is a base.

    A Marlow/Bramwell "Weekly Order Status Report - ALL" prints commission as
    FURN / LEATH / FABRIC / MASQ / TOT under a single COMMISSION heading and
    prints no billed amount anywhere. With nothing said about that shape, both
    engines read the leftmost column as the commissionable amount and a middle
    category as the commission -- so 305 lines across 24 pages carried a
    commissionable base the page never stated, and the pair reconciled to
    nothing. The definitions now say which column is the total and that a
    commission column is never a base.
    """
    lines = line_properties()
    base = lines["commissionable_amount"]["description"]
    commission = lines["commission_amount"]["description"]
    assert "under a Commission heading is never this field" in base
    assert "Leave this null" in base
    assert "this field is the total column, not the first" in commission
    assert "source_labelled_fields" in commission


def test_the_instructions_carry_the_split_commission_rule():
    """The rule reaches an engine that reads the instructions and not the schema."""
    assert "read the total column" in extraction_schema.INSTRUCTIONS
    assert "has no commissionable_amount" in extraction_schema.INSTRUCTIONS
