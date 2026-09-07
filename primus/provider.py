"""LLM provider abstraction and OpenAI-compatible implementation.

Design
------
``LLMProvider`` is the abstract base class.  Every provider must implement
``complete(messages) → str``.

``OpenAICompatibleProvider`` wraps the ``openai`` Python SDK and works with
any provider that exposes an OpenAI-compatible chat-completions endpoint
(OpenAI, Gemini, Qwen/DashScope, Ollama, vLLM, etc.).

The provider attempts strict JSON-schema-constrained output first (forces
the exact TutorResponse field names/types), falls back to generic
``response_format={"type": "json_object"}`` if the endpoint rejects a
schema, and finally falls back to plain text extraction.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

from openai import OpenAI


# ---------------------------------------------------------------------------
# Typed exception
# ---------------------------------------------------------------------------

class LLMProviderError(Exception):
    """Raised when the LLM provider cannot complete a request.

    Wraps connection errors, timeouts, authentication failures,
    rate-limit responses, and other provider-level exceptions.
    """


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    """Minimal, provider-agnostic LLM interface."""

    @abstractmethod
    def complete(self, messages: list[dict[str, str]]) -> str:
        """Send *messages* to the LLM and return the raw response text."""
        ...


# ---------------------------------------------------------------------------
# TutorResponse JSON schema — must match primus/models.py's TutorResponse.
# Kept here (rather than imported) to avoid coupling the provider to the
# pydantic model; if TutorResponse's fields change, update this too.
# ---------------------------------------------------------------------------

_TUTOR_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "response_text": {
            "type": "string",
            "description": "The tutor's reply to the student.",
        },
        "language": {
            "type": "string",
            "enum": ["en", "ur", "roman_urdu"],
        },
        "interaction_state": {
            "type": "string",
            "enum": [
                "idle", "listening", "thinking", "speaking",
                "happy", "encouraging", "confused", "excited",
            ],
        },
        "topic": {
            "type": ["string", "null"],
            "description": "Short snake_case topic identifier, or null.",
        },
    },
    "required": ["response_text", "language", "interaction_state", "topic"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# OpenAI-compatible provider
# ---------------------------------------------------------------------------

class OpenAICompatibleProvider(LLMProvider):
    """Works with any OpenAI-compatible chat-completions endpoint.

    Parameters
    ----------
    api_key:
        Provider API key (required).
    base_url:
        Override the default OpenAI base URL for compatible providers
        (Gemini, Qwen DashScope, Ollama, vLLM, etc.).
    model:
        Model identifier to request.
    temperature:
        Sampling temperature.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        model: str = "gpt-4o-mini",
        temperature: float = 0.7,
    ) -> None:
        if not api_key:
            raise ValueError(
                "api_key is required — set the OPENAI_API_KEY environment variable."
            )

        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url

        self._client = OpenAI(**kwargs)
        self._model = model
        self._temperature = temperature

    # -- public API ----------------------------------------------------------

    def complete(self, messages: list[dict[str, str]]) -> str:
        """Call the chat-completions endpoint.

        Strategy:
        1. Try strict JSON-schema-constrained output — forces the exact
           TutorResponse fields/types (most reliable, when supported).
        2. If the endpoint rejects that, try generic JSON-object mode
           (valid JSON, but shape is prompt-driven only).
        3. If that's also rejected, fall back to plain completion.
        """
        built_messages = [
            {"role": m["role"], "content": m["content"]} for m in messages
        ]

        # --- attempt 1: strict JSON schema ----------------------------------
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=built_messages,  # type: ignore[arg-type]
                temperature=self._temperature,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "TutorResponse",
                        "strict": True,
                        "schema": _TUTOR_RESPONSE_SCHEMA,
                    },
                },
            )
            content = response.choices[0].message.content
            if content:
                return content.strip()
        except LLMProviderError:
            raise
        except Exception as exc:
            if not self._is_retryable_format_error(exc):
                raise LLMProviderError(str(exc)) from exc

        # --- attempt 2: generic JSON object mode ----------------------------
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=built_messages,  # type: ignore[arg-type]
                temperature=self._temperature,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            if content:
                return content.strip()
        except LLMProviderError:
            raise
        except Exception as exc:
            if not self._is_retryable_format_error(exc):
                raise LLMProviderError(str(exc)) from exc

        # --- attempt 3: plain completion -------------------------------------
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=built_messages,  # type: ignore[arg-type]
                temperature=self._temperature,
            )
            content = response.choices[0].message.content
            return content.strip() if content else ""
        except Exception as exc:
            raise LLMProviderError(str(exc)) from exc

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _is_retryable_format_error(exc: Exception) -> bool:
        """Return True if the exception looks like an unsupported-format error.

        Some providers reject ``response_format`` (or a schema shape) but
        succeed on retry with a simpler format.  Genuine API failures
        (auth, network, timeout, quota) should NOT be retried.
        """
        msg = str(exc).lower()
        return any(kw in msg for kw in (
            "response_format", "json_schema", "json_object", "json",
            "format", "not supported", "unsupported", "schema",
        ))