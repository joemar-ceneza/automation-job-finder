"""
llm.py
The transport layer for AI mode: one protocol every capability talks to, a
cache in front of it, a null object when nothing is configured, and a factory
that assembles the right provider from config and .env.

Nothing above this file knows which vendor is in use, or whether a vendor is
in use at all. That is the whole point — a capability asks the factory for a
provider and calls .complete(); if no key is set it gets the null object and
degrades to Standard mode, and if a call fails it catches LLMUnavailable and
falls back to the deterministic answer.
"""
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field, replace
from typing import Protocol

import config


class LLMUnavailable(Exception):
    """
    Raised when a completion cannot be produced — no provider, transport
    failure, a refusal, or output that would not validate. Capabilities catch
    this and fall back to Standard mode, so it must never escape to the user.
    """


@dataclass(frozen=True)
class LLMRequest:
    """One provider-agnostic request for a JSON object matching `schema`."""
    system: str
    prompt: str
    schema: dict                      # JSON Schema the reply must satisfy
    max_tokens: int = 4096
    effort: str = "high"              # low | medium | high | xhigh | max
    # Extra strings folded into the cache key, for callers that need to vary
    # the cache beyond system+prompt (e.g. a resume revision).
    cache_salt: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class LLMResponse:
    """The parsed result plus what it cost."""
    data: dict
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    from_cache: bool = False


class LLMProvider(Protocol):
    """Implemented once per vendor. The only surface capabilities depend on."""

    name: str

    def is_available(self) -> bool:
        """True when this provider could actually serve a request."""
        ...

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Return a JSON object satisfying request.schema, or raise
        LLMUnavailable."""
        ...


# ======================================================
# NULL OBJECT
# ======================================================
class NullProvider:
    """
    Stands in when no provider is configured. Never pretends to work: it
    reports unavailable and raises if actually called, so a caller that
    forgets to check is_available() fails loudly in tests rather than silently
    in production.
    """
    name = "none"

    def is_available(self) -> bool:
        return False

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise LLMUnavailable(
            "No AI provider is configured. Set one in .env "
            "(see .env.example) to enable AI mode.")


# ======================================================
# CACHE DECORATOR
# ======================================================
def cache_key(provider_name: str, request: LLMRequest) -> str:
    """A stable key for one (provider, request). A job description does not
    change between runs, so the same analysis need only be paid for once."""
    material = json.dumps({
        "provider": provider_name,
        "system": request.system,
        "prompt": request.prompt,
        "schema": request.schema,
        "salt": list(request.cache_salt),
    }, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class CachedProvider:
    """
    Wraps any provider with a SQLite-backed memo. Same protocol, so callers
    cannot tell the difference — which is what lets the cache be transparent.
    Also makes development free: the tenth test run replays the first.
    """

    def __init__(self, inner: LLMProvider, repo) -> None:
        self._inner = inner
        self._repo = repo               # db_handler-shaped: get/put by key
        self.name = f"{inner.name}+cache"

    def is_available(self) -> bool:
        return self._inner.is_available()

    def complete(self, request: LLMRequest) -> LLMResponse:
        key = cache_key(self._inner.name, request)
        hit = self._repo.get_ai_cache(key)
        if hit is not None:
            return replace(hit, from_cache=True)
        result = self._inner.complete(request)
        self._repo.put_ai_cache(key, result)
        return result


# ======================================================
# FACTORY
# ======================================================
def _build_base_provider() -> LLMProvider:
    """Constructs the configured provider, or the null object."""
    provider = (os.getenv("AI_PROVIDER") or config.AI_PROVIDER or "").lower()

    if provider in ("", "none", "off"):
        return NullProvider()

    if provider == "claude":
        from llm_providers import ClaudeProvider
        key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not key:
            logging.warning("AI_PROVIDER=claude but ANTHROPIC_API_KEY is "
                            "unset — staying in Standard mode.")
            return NullProvider()
        return ClaudeProvider(api_key=key, model=config.AI_MODEL)

    if provider in ("openai", "ollama", "lmstudio", "openai_compatible"):
        from llm_providers import OpenAICompatibleProvider
        return OpenAICompatibleProvider(
            base_url=os.getenv("AI_BASE_URL") or config.AI_BASE_URL,
            model=os.getenv("AI_MODEL") or config.AI_MODEL,
            api_key=os.getenv("AI_API_KEY") or os.getenv("OPENAI_API_KEY", ""))

    logging.warning("Unknown AI_PROVIDER '%s' — staying in Standard mode.",
                    provider)
    return NullProvider()


def get_provider(repo=None) -> LLMProvider:
    """
    The provider AI capabilities should use: the configured vendor wrapped in
    the cache, or the null object. `repo` is the cache backend (db_handler);
    omit it to skip caching (tests pass a fake).
    """
    from dotenv import load_dotenv
    load_dotenv(os.path.join(config.BASE_DIR, ".env"))

    base = _build_base_provider()
    if repo is None or isinstance(base, NullProvider):
        return base
    return CachedProvider(base, repo)
