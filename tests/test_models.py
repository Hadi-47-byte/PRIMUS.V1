"""Tests for primus.models — InteractionState, TutorResponse, SessionState."""

from __future__ import annotations

import pytest

from primus.models import InteractionState, SessionState, TutorResponse


# ---------------------------------------------------------------------------
# InteractionState
# ---------------------------------------------------------------------------

class TestInteractionState:
    def test_all_values_exist(self):
        expected = {
            "idle", "listening", "thinking", "speaking",
            "happy", "encouraging", "confused", "excited",
        }
        actual = {s.value for s in InteractionState}
        assert actual == expected

    def test_construct_from_string(self):
        assert InteractionState("idle") is InteractionState.IDLE
        assert InteractionState("encouraging") is InteractionState.ENCOURAGING

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            InteractionState("invalid_state")


# ---------------------------------------------------------------------------
# TutorResponse
# ---------------------------------------------------------------------------

class TestTutorResponse:
    def test_valid_full(self):
        r = TutorResponse(
            response_text="Gravity is a force.",
            language="en",
            interaction_state=InteractionState.SPEAKING,
            topic="gravity",
        )
        assert r.response_text == "Gravity is a force."
        assert r.language == "en"
        assert r.interaction_state is InteractionState.SPEAKING
        assert r.topic == "gravity"
        assert r.raw_response is None

    def test_defaults(self):
        r = TutorResponse(response_text="Hello")
        assert r.language == "en"
        assert r.interaction_state is InteractionState.IDLE
        assert r.topic is None

    def test_interaction_state_from_string(self):
        r = TutorResponse(
            response_text="x",
            interaction_state="happy",
        )
        assert r.interaction_state is InteractionState.HAPPY

    def test_unknown_state_raises(self):
        with pytest.raises(Exception):
            TutorResponse(
                response_text="x",
                interaction_state="does_not_exist",
            )

    def test_missing_response_text_raises(self):
        with pytest.raises(Exception):
            TutorResponse()  # type: ignore[call-arg]

    def test_raw_response_stored(self):
        r = TutorResponse(
            response_text="ok",
            raw_response='{"response_text": "ok"}',
        )
        assert r.raw_response == '{"response_text": "ok"}'


# ---------------------------------------------------------------------------
# SessionState
# ---------------------------------------------------------------------------

class TestSessionState:
    def test_defaults(self):
        s = SessionState(session_id="s1")
        assert s.session_id == "s1"
        assert s.grade is None
        assert s.current_topic is None
        assert s.language == "en"
        assert s.explanation_depth == 1
        assert s.mode == "tutor"
        assert s.history == []

    def test_custom_values(self):
        s = SessionState(
            session_id="s2",
            grade=8,
            current_topic="velocity",
            language="ur",
            explanation_depth=2,
        )
        assert s.grade == 8
        assert s.current_topic == "velocity"
        assert s.language == "ur"
        assert s.explanation_depth == 2

    def test_history_independent(self):
        s1 = SessionState(session_id="a")
        s2 = SessionState(session_id="b")
        s1.history.append({"role": "user", "content": "hi"})
        assert len(s1.history) == 1
        assert len(s2.history) == 0
