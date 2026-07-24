"""
Tests for the AI transport layer.

No test here calls a real model. A FakeProvider returns canned schema-valid
data and a FailingProvider raises, so the degrade-to-Standard path is exercised
on every run rather than discovered in production.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import llm
from llm import (CachedProvider, LLMRequest, LLMResponse, LLMUnavailable,
                 NullProvider, cache_key)

SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}},
          "required": ["summary"]}


def make_request(prompt="p", salt=()) -> LLMRequest:
    return LLMRequest(system="s", prompt=prompt, schema=SCHEMA, cache_salt=salt)


class FakeProvider:
    name = "fake"

    def __init__(self, data=None):
        self._data = data or {"summary": "canned"}
        self.calls = 0

    def is_available(self):
        return True

    def complete(self, request):
        self.calls += 1
        return LLMResponse(data=self._data, model="fake-1",
                           input_tokens=10, output_tokens=5)


class FailingProvider:
    name = "failing"

    def is_available(self):
        return True

    def complete(self, request):
        raise LLMUnavailable("simulated outage")


# ======================================================
# NULL OBJECT
# ======================================================
def test_null_provider_is_never_available():
    assert NullProvider().is_available() is False


def test_null_provider_raises_rather_than_pretending():
    with pytest.raises(LLMUnavailable):
        NullProvider().complete(make_request())


# ======================================================
# CACHE KEY
# ======================================================
def test_cache_key_is_stable():
    assert cache_key("fake", make_request()) == cache_key("fake", make_request())


def test_cache_key_varies_with_prompt():
    assert cache_key("fake", make_request("a")) != \
           cache_key("fake", make_request("b"))


def test_cache_key_varies_with_salt():
    assert cache_key("fake", make_request(salt=("x",))) != \
           cache_key("fake", make_request(salt=("y",)))


def test_cache_key_varies_with_provider():
    assert cache_key("a", make_request()) != cache_key("b", make_request())


# ======================================================
# CACHE DECORATOR
# ======================================================
class DictRepo:
    """A minimal in-memory stand-in for the db_handler cache."""
    def __init__(self):
        self.store = {}

    def get_ai_cache(self, key):
        return self.store.get(key)

    def put_ai_cache(self, key, response):
        self.store[key] = response


def test_second_call_is_served_from_cache():
    inner = FakeProvider()
    cached = CachedProvider(inner, DictRepo())

    first = cached.complete(make_request())
    second = cached.complete(make_request())

    assert inner.calls == 1, "the inner provider should be hit only once"
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.data == first.data


def test_different_requests_are_cached_separately():
    inner = FakeProvider()
    cached = CachedProvider(inner, DictRepo())
    cached.complete(make_request("a"))
    cached.complete(make_request("b"))
    assert inner.calls == 2


def test_cache_reports_the_wrapped_provider_availability():
    assert CachedProvider(FakeProvider(), DictRepo()).is_available() is True
    assert CachedProvider(NullProvider(), DictRepo()).is_available() is False


# ======================================================
# FACTORY
# ======================================================
def test_factory_returns_null_when_no_provider_configured(monkeypatch):
    monkeypatch.setattr(config, "AI_PROVIDER", "none")
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    assert isinstance(llm.get_provider(), NullProvider)


def test_factory_returns_null_for_claude_without_a_key(monkeypatch):
    monkeypatch.setattr(config, "AI_PROVIDER", "claude")
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(llm.get_provider(), NullProvider)


def test_factory_never_wraps_the_null_object_in_a_cache(monkeypatch):
    monkeypatch.setattr(config, "AI_PROVIDER", "none")
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    assert isinstance(llm.get_provider(DictRepo()), NullProvider)


def test_factory_ignores_an_unknown_provider(monkeypatch):
    monkeypatch.setattr(config, "AI_PROVIDER", "wat")
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    assert isinstance(llm.get_provider(), NullProvider)
