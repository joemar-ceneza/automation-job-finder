"""
llm_providers.py
The vendor adapters. Two of them cover six providers: Claude has its own SDK,
and everything that speaks the OpenAI chat-completions wire format — OpenAI,
Ollama, LM Studio, vLLM — shares one httpx client parameterised by base URL.

Each adapter's only job is to turn an LLMRequest into an LLMResponse or raise
LLMUnavailable. No retry policy, no fallback logic, no schema knowledge beyond
passing it through — those live above this file.
"""
import json
import logging
import re

import config
from llm import LLMRequest, LLMResponse, LLMUnavailable

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


# ======================================================
# SHARED VALIDATION
# ======================================================
def _coerce_to_schema(data: object, schema: dict) -> dict:
    """
    Confirms the model returned an object with the schema's required keys, and
    fills any missing optional array field with []. Raises LLMUnavailable when
    the shape is unusable, so the caller falls back to Standard mode rather
    than rendering half a result.
    """
    if not isinstance(data, dict):
        raise LLMUnavailable(f"Model returned {type(data).__name__}, not an "
                             "object.")
    for key in schema.get("required", []):
        if key not in data:
            raise LLMUnavailable(f"Model reply is missing required field "
                                 f"{key!r}.")
    for key, spec in schema.get("properties", {}).items():
        if key not in data and spec.get("type") == "array":
            data[key] = []
    return data


def _parse_json_object(text: str, schema: dict) -> dict:
    """Pulls a JSON object out of a model's text reply and validates it."""
    text = (text or "").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Local models sometimes wrap the object in prose or a code fence.
        match = _JSON_OBJECT.search(text)
        if not match:
            raise LLMUnavailable("Model reply was not JSON.")
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as error:
            raise LLMUnavailable(f"Model reply was not valid JSON: {error}")
    return _coerce_to_schema(data, schema)


# ======================================================
# CLAUDE
# ======================================================
class ClaudeProvider:
    """Anthropic's API via the official SDK, with structured JSON output."""
    name = "claude"

    def __init__(self, api_key: str, model: str = "claude-opus-4-8") -> None:
        import anthropic
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(
            api_key=api_key, timeout=float(config.AI_TIMEOUT_SECONDS),
            max_retries=1)
        self._model = model

    def is_available(self) -> bool:
        return bool(self._client.api_key)

    def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=[{"role": "user", "content": request.prompt}],
                thinking={"type": "adaptive"},
                output_config={
                    "effort": request.effort,
                    "format": {"type": "json_schema", "schema": request.schema},
                },
            )
        except self._anthropic.APIStatusError as error:
            raise LLMUnavailable(f"Claude API error {error.status_code}: "
                                 f"{error.message}")
        except self._anthropic.APIConnectionError as error:
            raise LLMUnavailable(f"Could not reach the Claude API: {error}")

        if response.stop_reason == "refusal":
            raise LLMUnavailable("Claude declined this request.")

        text = next((block.text for block in response.content
                     if block.type == "text"), "")
        return LLMResponse(
            data=_parse_json_object(text, request.schema),
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens)


# ======================================================
# OPENAI-COMPATIBLE (OpenAI, Ollama, LM Studio, vLLM)
# ======================================================
class OpenAICompatibleProvider:
    """
    Any endpoint speaking the OpenAI chat-completions format. The schema is
    described in the prompt and JSON mode is requested, which is the portable
    intersection of what OpenAI and the local runtimes support.
    """
    name = "openai-compatible"

    def __init__(self, base_url: str, model: str, api_key: str = "") -> None:
        import httpx
        self._model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(headers=headers,
                                    timeout=float(config.AI_TIMEOUT_SECONDS))
        self._local = "localhost" in base_url or "127.0.0.1" in base_url
        self._has_key = bool(api_key)

    def is_available(self) -> bool:
        # A local runtime needs no key; a hosted one does.
        return self._local or self._has_key

    def complete(self, request: LLMRequest) -> LLMResponse:
        import httpx
        system = (f"{request.system}\n\nRespond with ONLY a JSON object that "
                  f"matches this JSON Schema, no prose or code fence:\n"
                  f"{json.dumps(request.schema)}")
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": request.prompt},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": request.max_tokens,
        }
        try:
            reply = self._client.post(self._url, json=payload)
            reply.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise LLMUnavailable(f"{self.name} returned "
                                 f"{error.response.status_code}.")
        except httpx.HTTPError as error:
            raise LLMUnavailable(f"Could not reach {self._url}: {error}. "
                                 "Is the local model running?")

        body = reply.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise LLMUnavailable("Unexpected response shape from the model.")

        usage = body.get("usage", {})
        return LLMResponse(
            data=_parse_json_object(content, request.schema),
            model=body.get("model", self._model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0))
