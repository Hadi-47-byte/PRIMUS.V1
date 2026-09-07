"""Tests for primus.engine — TutorEngine with MockLLMProvider."""

from __future__ import annotations

import json

import pytest

from primus.engine import TutorEngine
from primus.models import InteractionState, TutorResponse
from primus.provider import LLMProvider, LLMProviderError
from primus.session import SessionManager


# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------

class MockLLMProvider(LLMProvider):
    """Deterministic mock for unit tests."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.last_messages: list[dict[str, str]] | None = None

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.last_messages = messages
        return self._response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_response(
    text: str = "Gravity is a fundamental force.",
    language: str = "en",
    state: str = "speaking",
    topic: str | None = "gravity",
) -> str:
    return json.dumps({
        "response_text": text,
        "language": language,
        "interaction_state": state,
        "topic": topic,
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTutorEngine:
    def test_valid_json_response(self):
        engine = TutorEngine(provider=MockLLMProvider(_json_response()))
        r = engine.chat("What is gravity?")
        assert isinstance(r, TutorResponse)
        assert r.response_text == "Gravity is a fundamental force."
        assert r.language == "en"
        assert r.interaction_state is InteractionState.SPEAKING
        assert r.topic == "gravity"

    def test_session_topic_updated(self):
        engine = TutorEngine(provider=MockLLMProvider(_json_response(topic="velocity")))
        sessions = engine._sessions
        s = sessions.get_or_create(session_id="s1")
        assert s.current_topic is None

        engine.chat("What is velocity?", session_id="s1")
        assert s.current_topic == "velocity"

    def test_session_language_updated(self):
        engine = TutorEngine(
            provider=MockLLMProvider(_json_response(language="ur"))
        )
        sessions = engine._sessions
        s = sessions.get_or_create(session_id="s1")

        engine.chat("Urdu question", session_id="s1")
        assert s.language == "ur"

    def test_history_maintained(self):
        engine = TutorEngine(provider=MockLLMProvider(_json_response()))
        sessions = engine._sessions
        s = sessions.get_or_create(session_id="s1")

        engine.chat("Question 1", session_id="s1")
        assert len(s.history) == 2  # user + assistant

        engine.chat("Question 2", session_id="s1")
        assert len(s.history) == 4

    def test_null_topic_preserves_existing(self):
        engine = TutorEngine(
            provider=MockLLMProvider(_json_response(topic="gravity"))
        )
        sessions = engine._sessions
        s = sessions.get_or_create(session_id="s1")

        engine.chat("Q1", session_id="s1")
        assert s.current_topic == "gravity"

        # Second call returns None topic — should NOT clear the existing one
        engine._provider = MockLLMProvider(_json_response(topic=None))  # type: ignore[assignment]
        engine.chat("Q2", session_id="s1")
        assert s.current_topic == "gravity"

    def test_malformed_json_safe_fallback(self):
        engine = TutorEngine(provider=MockLLMProvider("This is not JSON at all."))
        sessions = engine._sessions
        sessions.get_or_create(session_id="s1")

        r = engine.chat("Hello", session_id="s1")
        assert isinstance(r, TutorResponse)
        assert r.interaction_state is InteractionState.IDLE
        assert r.language == "en"
        # The raw text should survive into response_text
        assert "not JSON" in r.response_text

    def test_partial_json_safe_fallback(self):
        engine = TutorEngine(
            provider=MockLLMProvider('{"response_text": "hi"')  # no closing brace
        )
        r = engine.chat("Hello")
        assert isinstance(r, TutorResponse)
        assert r.interaction_state is InteractionState.IDLE

    def test_unknown_interaction_state_defaults_to_idle(self):
        payload = json.dumps({
            "response_text": "ok",
            "language": "en",
            "interaction_state": "super_dance_mode",
        })
        engine = TutorEngine(provider=MockLLMProvider(payload))
        r = engine.chat("Hello")
        assert r.interaction_state is InteractionState.IDLE

    def test_missing_optional_fields_ok(self):
        payload = json.dumps({"response_text": "minimal answer"})
        engine = TutorEngine(provider=MockLLMProvider(payload))
        r = engine.chat("Hello")
        assert r.response_text == "minimal answer"
        assert r.language == "en"
        assert r.interaction_state is InteractionState.IDLE
        assert r.topic is None

    def test_empty_response_fallback(self):
        engine = TutorEngine(provider=MockLLMProvider(""))
        r = engine.chat("Hello")
        assert isinstance(r, TutorResponse)
        assert r.interaction_state is InteractionState.IDLE
        assert len(r.response_text) > 0

    def test_provider_error_propagates(self):
        """LLMProviderError must propagate — engine does NOT swallow it."""
        class FailProvider(LLMProvider):
            def complete(self, messages):
                raise LLMProviderError("Network down")

        engine = TutorEngine(provider=FailProvider())
        with pytest.raises(LLMProviderError):
            engine.chat("Hello")

    def test_system_prompt_present_in_messages(self):
        mock = MockLLMProvider(_json_response())
        engine = TutorEngine(provider=mock)
        engine.chat("What is force?")

        assert mock.last_messages is not None
        assert mock.last_messages[0]["role"] == "system"
        assert "PRIMUS" in mock.last_messages[0]["content"]

    def test_conversation_history_in_messages(self):
        mock = MockLLMProvider(_json_response())
        engine = TutorEngine(provider=mock)
        sessions = engine._sessions
        sessions.get_or_create(session_id="s1")

        engine.chat("Q1", session_id="s1")
        engine.chat("Q2", session_id="s1")

        # On the second call, messages should contain: system + Q1 + A1 + Q2
        msgs = mock.last_messages
        assert msgs[0]["role"] == "system"
        assert msgs[1] == {"role": "user", "content": "Q1"}
        assert msgs[2]["role"] == "assistant"
        assert msgs[3] == {"role": "user", "content": "Q2"}

    def test_raw_response_preserved(self):
        raw = _json_response()
        engine = TutorEngine(provider=MockLLMProvider(raw))
        r = engine.chat("Hello")
        assert r.raw_response == raw

    def test_code_fenced_json_parsed(self):
        payload = '```json\n{"response_text": "fenced", "language": "en", "interaction_state": "happy"}\n```'
        engine = TutorEngine(provider=MockLLMProvider(payload))
        r = engine.chat("Hello")
        assert r.response_text == "fenced"
        assert r.interaction_state is InteractionState.HAPPY
