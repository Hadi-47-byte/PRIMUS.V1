"""Tests for primus.provider — LLMProvider ABC and OpenAICompatibleProvider.

These tests do NOT call real APIs.  They verify initialization guards and
the response-handling logic using ``unittest.mock``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from primus.provider import LLMProvider, OpenAICompatibleProvider


# ---------------------------------------------------------------------------
# LLMProvider ABC
# ---------------------------------------------------------------------------

class TestLLMProvider:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            LLMProvider()  # type: ignore[abstract]

    def test_subclass_must_implement_complete(self):
        class Incomplete(LLMProvider):
            pass

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_valid_subclass(self):
        class Echo(LLMProvider):
            def complete(self, messages):
                return "echo"

        assert Echo().complete([]) == "echo"


# ---------------------------------------------------------------------------
# OpenAICompatibleProvider — initialization
# ---------------------------------------------------------------------------

class TestOpenAICompatibleProviderInit:
    def test_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key is required"):
            OpenAICompatibleProvider(api_key="")

    @patch("primus.provider.OpenAI")
    def test_default_initialization(self, mock_openai_cls):
        p = OpenAICompatibleProvider(api_key="sk-test")
        mock_openai_cls.assert_called_once_with(api_key="sk-test")
        assert p._model == "gpt-4o-mini"
        assert p._temperature == 0.7

    @patch("primus.provider.OpenAI")
    def test_custom_base_url(self, mock_openai_cls):
        OpenAICompatibleProvider(
            api_key="sk-test",
            base_url="https://custom.api.com/v1",
        )
        mock_openai_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://custom.api.com/v1",
        )

    @patch("primus.provider.OpenAI")
    def test_custom_model(self, mock_openai_cls):
        p = OpenAICompatibleProvider(api_key="sk-test", model="qwen-plus")
        assert p._model == "qwen-plus"


# ---------------------------------------------------------------------------
# OpenAICompatibleProvider — complete()
# ---------------------------------------------------------------------------

def _fake_completion(content: str) -> SimpleNamespace:
    """Build a minimal mock that mimics the OpenAI ChatCompletion response."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class TestOpenAICompatibleProviderComplete:
    @patch("primus.provider.OpenAI")
    def test_returns_json_content(self, mock_openai_cls):
        payload = json.dumps({"response_text": "hello"})
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_completion(payload)
        mock_openai_cls.return_value = mock_client

        p = OpenAICompatibleProvider(api_key="sk-test")
        result = p.complete([{"role": "user", "content": "hi"}])

        assert result == payload

    @patch("primus.provider.OpenAI")
    def test_fallback_when_json_mode_unsupported(self, mock_openai_cls):
        payload = json.dumps({"response_text": "fallback"})
        mock_client = MagicMock()

        # First call (with response_format) fails; second call succeeds.
        def side_effect(**kwargs):
            if "response_format" in kwargs:
                raise TypeError("response_format not supported")
            return _fake_completion(payload)

        mock_client.chat.completions.create.side_effect = side_effect
        mock_openai_cls.return_value = mock_client

        p = OpenAICompatibleProvider(api_key="sk-test")
        result = p.complete([{"role": "user", "content": "hi"}])

        assert result == payload
        assert mock_client.chat.completions.create.call_count == 2

    @patch("primus.provider.OpenAI")
    def test_empty_response_returns_empty_string(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_completion("")
        mock_openai_cls.return_value = mock_client

        p = OpenAICompatibleProvider(api_key="sk-test")
        result = p.complete([{"role": "user", "content": "hi"}])

        assert result == ""

    @patch("primus.provider.OpenAI")
    def test_messages_passed_correctly(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_completion("{}")
        mock_openai_cls.return_value = mock_client

        p = OpenAICompatibleProvider(api_key="sk-test", model="my-model")
        msgs = [
            {"role": "system", "content": "You are PRIMUS."},
            {"role": "user", "content": "What is gravity?"},
        ]
        p.complete(msgs)

        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == "my-model"
        sent_msgs = call_kwargs.kwargs["messages"]
        assert sent_msgs[0]["role"] == "system"
        assert sent_msgs[1]["content"] == "What is gravity?"
