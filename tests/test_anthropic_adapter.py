"""Hold the Anthropic adapter to the same contract as every other provider.

Anthropic is a separate vendor for consensus independence, so what matters here
is that a lane configured for it reads the page itself and returns output the
strict schema can be applied to -- not that a call succeeded.
"""

from __future__ import annotations

import base64
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

adapter = importlib.import_module("anthropic_adapter")
runtime_config = importlib.import_module("runtime_config")
llm_provider = importlib.import_module("llm_provider")

SCHEMA = {"name": "extracted_record", "strict": True, "schema": {"type": "object"}}


class Block:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class Response:
    def __init__(self, content):
        self.content = content

    def model_dump(self, mode="json"):
        return {"content": "dumped", "mode": mode}


class Messages:
    def __init__(self, response):
        self._response = response
        self.request = None

    def create(self, **request):
        self.request = request
        return self._response


class Client:
    def __init__(self, response):
        self.messages = Messages(response)


def create(client, *, reasoning=None, content=None):
    """Issue one structured request through the adapter's Responses contract."""
    responses = adapter._Responses(client, 16384)
    return responses.create(
        model="claude-haiku-4-5-20251001",
        instructions="Extract the record.",
        input=[{"role": "user", "content": content or [{"type": "input_text", "text": "page"}]}],
        text={"format": SCHEMA},
        reasoning=reasoning,
    )


def test_the_schema_becomes_a_required_tool_call(monkeypatch):
    """The Messages API has no strict response_format; a forced tool is the contract."""
    client = Client(Response([Block(type="tool_use", name="extracted_record", input={"a": 1})]))
    result = create(client)
    request = client.messages.request
    assert request["tools"][0]["input_schema"] == SCHEMA["schema"]
    assert request["tools"][0]["name"] == "extracted_record"
    assert request["tool_choice"] == {"type": "tool", "name": "extracted_record"}
    assert request["system"] == "Extract the record."
    assert request["max_tokens"] == 16384
    # The caller decodes `output_text`, so the tool input must arrive as JSON.
    assert json.loads(result.output_text) == {"a": 1}
    assert result.model_dump() == {"content": "dumped", "mode": "json"}


def test_the_raw_response_is_retained_whatever_the_sdk_offers():
    """Provenance must survive an SDK that exposes a different serializer."""
    block = Block(type="tool_use", name="extracted_record", input={})

    class ToDict:
        content = [block]

        def to_dict(self):
            return {"shape": "to_dict"}

    class Plain:
        content = [block]

    assert create(Client(ToDict())).model_dump() == {"shape": "to_dict"}
    plain = Plain()
    assert create(Client(plain)).model_dump() is plain


def test_a_retained_page_is_read_by_the_model_itself():
    """A lane whose recorded vendor did not read the page is not independent evidence."""
    data = base64.b64encode(b"%PDF-1.7 fake").decode()
    client = Client(Response([Block(type="tool_use", name="extracted_record", input={})]))
    create(
        client,
        content=[
            {"type": "input_text", "text": "read it"},
            {
                "type": "input_file",
                "filename": "p1.pdf",
                "file_data": f"data:application/pdf;base64,{data}",
            },
        ],
    )
    blocks = client.messages.request["messages"][0]["content"]
    document = next(item for item in blocks if item["type"] == "document")
    assert document["source"] == {
        "type": "base64",
        "media_type": "application/pdf",
        "data": data,
    }, "the data URL wrapper must be stripped, and the PDF sent as itself"

    # A bare base64 payload is passed through unchanged.
    create(client, content=[{"type": "input_file", "file_data": data}])
    assert client.messages.request["messages"][0]["content"][0]["source"]["data"] == data

    with pytest.raises(ValueError, match="cannot submit content part 'input_audio'"):
        create(client, content=[{"type": "input_audio"}])


def test_the_thinking_contract_follows_the_model_version():
    """Asking the wrong way is a hard 400, not a silent downgrade.

    Claude 4.5 and earlier take a token budget; 4.7 and later reject that and
    take adaptive thinking with `output_config.effort`. Sonnet 5 failed all 18
    pages of a real corpus on the budget form before this existed.
    """
    assert adapter.model_version("claude-haiku-4-5-20251001") == 4.5
    assert adapter.model_version("claude-sonnet-5") == 5.0
    assert adapter.model_version("claude-opus-4-8") == 4.8
    assert adapter.model_version("not-a-claude-slug") is None
    assert adapter.thinking_mode("claude-haiku-4-5-20251001") == "budget"
    assert adapter.thinking_mode("claude-sonnet-4-6") == "budget"
    assert adapter.thinking_mode("claude-sonnet-5") == "adaptive"
    # An unreadable slug gets the forward-looking form every current model takes.
    assert adapter.thinking_mode("mystery-model") == "adaptive"
    # An explicit setting overrides the version rule in either direction.
    assert adapter.thinking_mode("claude-sonnet-5", "budget") == "budget"
    assert adapter.thinking_mode("claude-haiku-4-5-20251001", "ADAPTIVE") == "adaptive"
    assert adapter.thinking_mode("claude-sonnet-5", "none") == "none"
    assert adapter.thinking_mode("claude-sonnet-5", "nonsense") == "adaptive"


def test_thinking_is_requested_correctly_for_each_contract():
    """Thinking and a forced tool choice are mutually exclusive in both modes."""
    client = Client(Response([Block(type="tool_use", name="extracted_record", input={"b": 2})]))

    # 4.5-era: a token budget.
    create(client, reasoning={"effort": "medium"})
    request = client.messages.request
    assert request["thinking"] == {"type": "enabled", "budget_tokens": 4096}
    assert "output_config" not in request
    assert request["tool_choice"] == {"type": "auto"}, "a forced tool is rejected while thinking"

    # `none` asks for no thinking, so the tool stays required.
    create(client, reasoning={"effort": "none"})
    assert "thinking" not in client.messages.request
    assert client.messages.request["tool_choice"]["type"] == "tool"
    # A budget that would not fit inside the output ceiling is not requested.
    create(client, reasoning={"effort": "max"})
    assert "thinking" not in client.messages.request
    # A bare effort string is accepted as well as the mapping form.
    create(client, reasoning="low")
    assert client.messages.request["thinking"]["budget_tokens"] == 1024

    # 4.7 and later: adaptive thinking with an effort, and no budget at all.
    responses = adapter._Responses(client, 16384, "adaptive")
    responses.create(
        model="claude-sonnet-5",
        instructions="Extract.",
        input=[{"role": "user", "content": [{"type": "input_text", "text": "page"}]}],
        text={"format": SCHEMA},
        reasoning={"effort": "medium"},
    )
    request = client.messages.request
    assert request["thinking"] == {"type": "adaptive"}
    assert request["output_config"] == {"effort": "medium"}
    assert "budget_tokens" not in json.dumps(request["thinking"])
    # The finer levels collapse upward rather than being dropped.
    for effort in ("xhigh", "max"):
        responses.create(
            model="claude-sonnet-5",
            instructions="Extract.",
            input=[{"role": "user", "content": [{"type": "input_text", "text": "p"}]}],
            text={"format": SCHEMA},
            reasoning={"effort": effort},
        )
        assert client.messages.request["output_config"] == {"effort": "high"}

    # A lane told not to think sends neither form.
    off = adapter._Responses(client, 16384, "none")
    off.create(
        model="claude-sonnet-5",
        instructions="Extract.",
        input=[{"role": "user", "content": [{"type": "input_text", "text": "p"}]}],
        text={"format": SCHEMA},
        reasoning={"effort": "high"},
    )
    assert "thinking" not in client.messages.request
    assert client.messages.request["tool_choice"]["type"] == "tool"


def test_prose_instead_of_a_tool_call_is_still_offered_to_the_schema():
    """A fenced object is unwrapped; nothing else is guessed at."""
    fenced = Client(Response([Block(type="text", text='```json\n{"c": 3}\n```')]))
    assert json.loads(create(fenced).output_text) == {"c": 3}

    # A different tool's output is not this tool's answer.
    other = Client(
        Response(
            [
                Block(type="tool_use", name="something_else", input={"d": 4}),
                Block(type="text", text='{"e": 5}'),
            ]
        )
    )
    assert json.loads(create(other).output_text) == {"e": 5}

    empty = Client(Response([]))
    with pytest.raises(ValueError, match="neither the required tool call nor any text"):
        create(empty)


def test_a_client_needs_its_own_credential_and_bounded_transport(monkeypatch):
    """Anthropic carries its own key; it never borrows another provider's."""
    monkeypatch.setattr(runtime_config, "load_project_env", lambda: {})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MAX_OUTPUT_TOKENS", raising=False)

    with pytest.raises(ValueError, match="uppercase with underscores"):
        adapter.build_client("lower_case")
    with pytest.raises(ValueError, match="timeout seconds must be positive"):
        adapter.build_client("ANTHROPIC_API_KEY", 0)
    with pytest.raises(ValueError, match="retries non-negative"):
        adapter.build_client("ANTHROPIC_API_KEY", 30.0, -1)
    with pytest.raises(ValueError, match="not set: ANTHROPIC_API_KEY"):
        adapter.build_client("ANTHROPIC_API_KEY")

    captured = {}

    class FakeAnthropic:
        def __init__(self, **options):
            captured.update(options)
            self.messages = None

    monkeypatch.setitem(sys.modules, "anthropic", type(sys)("anthropic"))
    sys.modules["anthropic"].Anthropic = FakeAnthropic
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-value")
    client = adapter.build_client("ANTHROPIC_API_KEY", 45.0, 3)
    assert captured == {"api_key": "key-value", "timeout": 45.0, "max_retries": 3}
    assert client.responses.max_output_tokens == adapter.DEFAULT_MAX_OUTPUT_TOKENS

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("ANTHROPIC_MAX_OUTPUT_TOKENS", "2048")
    client = adapter.build_client("ANTHROPIC_API_KEY")
    assert captured["base_url"] == "https://example.invalid"
    assert client.responses.max_output_tokens == 2048

    monkeypatch.setenv("ANTHROPIC_MAX_OUTPUT_TOKENS", "0")
    with pytest.raises(ValueError, match="must be a positive integer"):
        adapter.build_client("ANTHROPIC_API_KEY")

    # An identity-linked key is scoped to a workspace, and the API refuses a
    # request that does not name one: "anthropic-workspace-id is required when
    # authenticating with an identity-linked API key". The id is an identifier,
    # not a secret, so it travels as an ordinary setting.
    monkeypatch.setenv("ANTHROPIC_MAX_OUTPUT_TOKENS", "4096")
    captured.clear()
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_example")
    adapter.build_client("ANTHROPIC_API_KEY")
    assert captured["default_headers"] == {"anthropic-workspace-id": "wrkspc_example"}
    captured.clear()
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "")
    adapter.build_client("ANTHROPIC_API_KEY")
    assert "default_headers" not in captured, "a key that is not identity-linked sends no header"


def test_anthropic_is_a_provider_everywhere_a_provider_is_named(monkeypatch):
    """Usable exactly like the other three, and its own vendor for independence."""
    assert "anthropic" in runtime_config.LLM_PROVIDERS
    monkeypatch.setattr(runtime_config, "load_project_env", lambda: {})
    monkeypatch.setenv("ANTHROPIC_CREDENTIAL_ENV", "")
    assert runtime_config.provider_credential_env("anthropic") == "ANTHROPIC_API_KEY"
    monkeypatch.setenv("ANTHROPIC_MODEL", "")
    assert runtime_config.llm_model("anthropic") == "claude-haiku-4-5-20251001"
    monkeypatch.setenv("LLM_CONSENSUS_SEC_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_CONSENSUS_MODEL", "claude-haiku-4-5-20251001")
    assert runtime_config.lane_provider("consensus_secondary") == "anthropic"
    assert runtime_config.lane_model("consensus_secondary") == "claude-haiku-4-5-20251001"

    # Its own vendor: nothing to route through, so it is independent of the rest.
    assert runtime_config.model_vendor("anthropic", "claude-haiku-4-5-20251001") == "anthropic"
    assert runtime_config.model_vendor("openai", "gpt-5.6") != "anthropic"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-value")
    monkeypatch.setitem(sys.modules, "anthropic", type(sys)("anthropic"))
    sys.modules["anthropic"].Anthropic = lambda **options: type("A", (), {"messages": None})()
    built = llm_provider.build_client("ANTHROPIC_API_KEY", 30.0, 1, provider="anthropic")
    assert isinstance(built, adapter._Client)


def test_the_shared_evidence_loop_is_reused_under_the_anthropic_identity(monkeypatch):
    """Retention, retries, exceptions, and manifest order are not reimplemented.

    Only the provider identity and the client differ, so a run through Anthropic
    carries exactly the guarantees the other providers' runs do.
    """
    seen = {}

    def fake(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return {"pages": 18, "engine": "anthropic/claude-haiku-4-5-20251001"}

    monkeypatch.setattr(adapter.openai_adapter, "run_adapter", fake)
    result = adapter.run_adapter("manifest", "out", lane="consensus_secondary")
    assert result["pages"] == 18
    assert seen["args"] == ("manifest", "out")
    assert seen["kwargs"]["provider_name"] == "anthropic"
    assert seen["kwargs"]["lane"] == "consensus_secondary"
