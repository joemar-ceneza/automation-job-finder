"""
Tests for the vendor adapters.

No real endpoint is contacted: the JSON parsing/validation helpers are pure,
and the one network test points at a dead local port to prove a missing model
degrades to LLMUnavailable rather than crashing.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm import LLMRequest, LLMUnavailable
from llm_providers import (OpenAICompatibleProvider, _coerce_to_schema,
                           _parse_json_object)

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "items": {"type": "array"},
    },
    "required": ["summary"],
}


# ======================================================
# JSON PARSING
# ======================================================
def test_clean_json_is_parsed():
    data = _parse_json_object('{"summary": "hi"}', SCHEMA)
    assert data["summary"] == "hi"


def test_json_wrapped_in_a_code_fence_is_recovered():
    """Local models often wrap the object in ```json ... ```."""
    text = '```json\n{"summary": "hi"}\n```'
    assert _parse_json_object(text, SCHEMA)["summary"] == "hi"


def test_json_with_surrounding_prose_is_recovered():
    text = 'Here is your answer:\n{"summary": "hi"}\nHope that helps!'
    assert _parse_json_object(text, SCHEMA)["summary"] == "hi"


def test_non_json_raises_unavailable():
    with pytest.raises(LLMUnavailable):
        _parse_json_object("I cannot help with that.", SCHEMA)


def test_empty_reply_raises_unavailable():
    with pytest.raises(LLMUnavailable):
        _parse_json_object("", SCHEMA)


# ======================================================
# SCHEMA COERCION
# ======================================================
def test_missing_required_field_is_rejected():
    with pytest.raises(LLMUnavailable, match="missing required"):
        _coerce_to_schema({"items": []}, SCHEMA)


def test_missing_optional_array_is_filled():
    data = _coerce_to_schema({"summary": "hi"}, SCHEMA)
    assert data["items"] == []


def test_a_non_object_is_rejected():
    with pytest.raises(LLMUnavailable):
        _coerce_to_schema(["not", "an", "object"], SCHEMA)


# ======================================================
# OPENAI-COMPATIBLE AVAILABILITY
# ======================================================
def test_a_local_endpoint_needs_no_key():
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:11434/v1", model="llama3.1")
    assert provider.is_available() is True


def test_a_hosted_endpoint_without_a_key_is_unavailable():
    provider = OpenAICompatibleProvider(
        base_url="https://api.openai.com/v1", model="gpt-4o", api_key="")
    assert provider.is_available() is False


def test_a_dead_local_endpoint_degrades_not_crashes():
    """The whole point of AI mode being optional: a stopped Ollama is fine."""
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:9", model="llama3.1")   # nothing listening
    request = LLMRequest(system="s", prompt="p", schema=SCHEMA, max_tokens=64)
    with pytest.raises(LLMUnavailable):
        provider.complete(request)


# ======================================================
# CLAUDE CALL SHAPE (mocked — no key, no network)
# ======================================================
class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Usage:
    input_tokens, output_tokens = 12, 8


class _Message:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason
        self.model = "claude-opus-4-8"
        self.usage = _Usage()


def _claude_with_mock(monkeypatch, message):
    """A ClaudeProvider whose SDK client is a mock capturing create() kwargs."""
    import types
    from unittest.mock import MagicMock

    captured = {}
    fake_anthropic = types.SimpleNamespace(
        Anthropic=MagicMock(),
        APIStatusError=type("APIStatusError", (Exception,), {}),
        APIConnectionError=type("APIConnectionError", (Exception,), {}),
    )

    def fake_create(**kwargs):
        captured.update(kwargs)
        return message

    client = MagicMock()
    client.api_key = "sk-test"
    client.messages.create.side_effect = fake_create
    fake_anthropic.Anthropic.return_value = client

    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
    from llm_providers import ClaudeProvider
    return ClaudeProvider(api_key="sk-test", model="claude-opus-4-8"), captured


def test_claude_call_uses_the_documented_shape(monkeypatch):
    message = _Message([_Block('{"summary": "ok"}')])
    provider, captured = _claude_with_mock(monkeypatch, message)

    request = LLMRequest(system="sys", prompt="hello", schema=SCHEMA,
                         max_tokens=1500, effort="high")
    result = provider.complete(request)

    assert captured["model"] == "claude-opus-4-8"
    assert captured["max_tokens"] == 1500
    assert captured["system"] == "sys"
    assert captured["messages"] == [{"role": "user", "content": "hello"}]
    assert captured["thinking"] == {"type": "adaptive"}
    # effort and the JSON schema both live inside output_config
    assert captured["output_config"]["effort"] == "high"
    assert captured["output_config"]["format"] == {
        "type": "json_schema", "schema": SCHEMA}
    assert result.data["summary"] == "ok"
    assert result.data["items"] == []      # optional array filled by coercion
    assert result.input_tokens == 12


def test_claude_refusal_becomes_unavailable(monkeypatch):
    message = _Message([_Block("")], stop_reason="refusal")
    provider, _ = _claude_with_mock(monkeypatch, message)
    with pytest.raises(LLMUnavailable, match="declined"):
        provider.complete(LLMRequest(system="s", prompt="p", schema=SCHEMA))
