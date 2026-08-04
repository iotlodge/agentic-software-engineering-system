"""Provider-neutral model adapter.

Talks to Anthropic or OpenAI over plain HTTPS (no SDK dependency), returns
schema-validated JSON, and treats model output as untrusted until it passes
validation — with exactly one constrained repair attempt before escalating.
The deterministic MockProvider keeps every orchestration path testable in CI.

Keys come from the environment / .env (ANTHROPIC_API_KEY, OPENAI_API_KEY);
they are never logged, never passed to workload subprocesses (the evidence
runner strips credential-shaped variables), and never granted to workers.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

import httpx
from dotenv import load_dotenv

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_OPENAI_MODEL = "gpt-5.2"


class AdapterError(RuntimeError):
    pass


class SchemaViolation(AdapterError):
    pass


def validate_output(data: Any, schema: dict[str, Any]) -> None:
    """Small structural validator: required keys, primitive types, list item
    types. Deliberately minimal — outputs that pass still go through policy
    and deterministic verification downstream."""
    if not isinstance(data, dict):
        raise SchemaViolation(f"expected object, got {type(data).__name__}")
    for key, spec in schema.get("required", {}).items():
        if key not in data:
            raise SchemaViolation(f"missing required key: {key}")
        value = data[key]
        expected = spec if isinstance(spec, str) else spec.get("type", "any")
        checks = {"list": list, "str": str, "int": int, "dict": dict}
        if expected in checks and not isinstance(value, checks[expected]):
            raise SchemaViolation(
                f"key {key!r} should be {expected}, got {type(value).__name__}")
        if isinstance(spec, dict) and spec.get("items") == "dict" and isinstance(value, list):
            bad = [i for i, v in enumerate(value) if not isinstance(v, dict)]
            if bad:
                raise SchemaViolation(f"key {key!r} items {bad} are not objects")


class Provider(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str | None = None):
        self.api_key = api_key
        self.model = model or os.environ.get("ASE_ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL)

    def complete(self, system: str, user: str) -> str:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.api_key,
                     "anthropic-version": "2023-06-01"},
            json={"model": self.model, "max_tokens": 4096, "system": system,
                  "messages": [{"role": "user", "content": user}]},
            timeout=120,
        )
        if resp.status_code >= 500 or resp.status_code == 429:
            raise AdapterError(f"anthropic transient error {resp.status_code}")
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str | None = None):
        self.api_key = api_key
        self.model = model or os.environ.get("ASE_OPENAI_MODEL", DEFAULT_OPENAI_MODEL)

    def complete(self, system: str, user: str) -> str:
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
            timeout=120,
        )
        if resp.status_code >= 500 or resp.status_code == 429:
            raise AdapterError(f"openai transient error {resp.status_code}")
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


class MockProvider:
    """Deterministic provider for CI: returns queued canned responses."""

    name = "mock"

    def __init__(self, responses: list[str] | None = None):
        self.responses = list(responses or [])
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self.responses:
            raise AdapterError("mock provider has no queued responses")
        return self.responses.pop(0)


def _extract_json(text: str) -> Any:
    """Model output is untrusted: find the JSON payload, tolerate fences."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise SchemaViolation("no JSON object found in model output")
    return json.loads(text[start:end + 1])


class ModelAdapter:
    def __init__(self, provider: Provider):
        self.provider = provider

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict:
        """One completion plus at most one constrained repair attempt."""
        raw = self.provider.complete(system, user)
        try:
            data = _extract_json(raw)
            validate_output(data, schema)
            return data
        except (SchemaViolation, json.JSONDecodeError) as first_error:
            repair = self.provider.complete(
                system,
                f"{user}\n\nYour previous reply was invalid: {first_error}.\n"
                f"Previous reply:\n{raw[:2000]}\n\n"
                "Reply with ONLY the corrected JSON object.",
            )
            data = _extract_json(repair)
            validate_output(data, schema)
            return data


def from_env(env_file: str | None = ".env") -> ModelAdapter:
    """Build an adapter from the environment. Anthropic wins if both keys exist."""
    if env_file:
        load_dotenv(env_file)
    preferred = os.environ.get("ASE_LLM_PROVIDER", "").lower()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    if preferred == "anthropic" or (not preferred and anthropic_key):
        if not anthropic_key:
            raise AdapterError("ANTHROPIC_API_KEY not set")
        return ModelAdapter(AnthropicProvider(anthropic_key))
    if preferred == "openai" or (not preferred and openai_key):
        if not openai_key:
            raise AdapterError("OPENAI_API_KEY not set")
        return ModelAdapter(OpenAIProvider(openai_key))
    raise AdapterError(
        "no LLM credentials found (ANTHROPIC_API_KEY / OPENAI_API_KEY); "
        "run in mock mode or populate .env")
