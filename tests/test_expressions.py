"""Tests for PRIMUS V5 — ExpressionMapper and RobotOrchestrator.

Covers:
- map_interaction_to_robot_state (pure function, no I/O)
- RobotOrchestrator.dispatch (fault-tolerant delivery)
- /chat integration (robot online and offline paths)

All tests use mocks/fakes — no real ESP32 or WebSocket required.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from primus.api.app import create_app
from primus.models import InteractionState, TutorResponse
from primus.provider import LLMProvider
from primus.robot.controller import RobotController
from primus.robot.expressions import (
    RobotOrchestrator,
    map_interaction_to_robot_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockLLMProvider(LLMProvider):
    """Returns a fixed interaction_state via JSON."""

    def __init__(self, interaction_state: str = "speaking"):
        self._state = interaction_state

    def complete(self, messages):
        return json.dumps({
            "response_text": "test response",
            "language": "en",
            "interaction_state": self._state,
            "topic": "test_topic",
        })


@pytest.fixture
def app():
    return create_app(provider=MockLLMProvider())


@pytest.fixture
def client(app):
    return TestClient(app)


# ===========================================================================
# ExpressionMapper tests (pure function)
# ===========================================================================

class TestExpressionMapper:
    """map_interaction_to_robot_state is a pure function with no I/O."""

    @pytest.mark.parametrize("state", [
        InteractionState.IDLE,
        InteractionState.LISTENING,
        InteractionState.THINKING,
        InteractionState.SPEAKING,
        InteractionState.HAPPY,
        InteractionState.ENCOURAGING,
        InteractionState.EXCITED,
    ])
    def test_direct_mapping(self, state):
        """Each InteractionState maps to its same-named robot state."""
        result = map_interaction_to_robot_state(state)
        assert result == state.value

    def test_confused_maps_to_thinking(self):
        """CONFUSED has no robot equivalent → maps to 'thinking'."""
        result = map_interaction_to_robot_state(InteractionState.CONFUSED)
        assert result == "thinking"

    def test_string_input(self):
        """Accepts raw strings as well as enum values."""
        assert map_interaction_to_robot_state("idle") == "idle"
        assert map_interaction_to_robot_state("confused") == "thinking"

    def test_unknown_state_raises(self):
        """Unknown strings raise ValueError."""
        with pytest.raises(ValueError, match="Cannot map unknown"):
            map_interaction_to_robot_state("dance_party")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            map_interaction_to_robot_state("")

    def test_returns_allowed_state(self):
        """Every mapping result is a valid robot protocol state."""
        from primus.robot.protocol import ALLOWED_STATES
        for state in InteractionState:
            result = map_interaction_to_robot_state(state)
            assert result in ALLOWED_STATES


# ===========================================================================
# RobotOrchestrator tests
# ===========================================================================

class FakeController(RobotController):
    """Minimal async fake for orchestrator tests."""

    def __init__(self, connected: bool = True, send_result: bool = True):
        super().__init__()
        self._fake_connected = connected
        self._send_result = send_result
        self.sent_states: list[str] = []

    def is_connected(self) -> bool:
        return self._fake_connected

    async def send_state(self, state: str) -> bool:
        from primus.robot.protocol import build_state_message
        build_state_message(state)          # validates
        if not self._fake_connected:
            return False
        self.sent_states.append(state)
        return self._send_result


class BrokenController(RobotController):
    """Controller that raises on every send."""

    async def send_state(self, state: str) -> bool:
        raise RuntimeError("WebSocket exploded")


class TestRobotOrchestrator:
    def _make_response(self, interaction_state: str = "thinking") -> TutorResponse:
        return TutorResponse(
            response_text="hello",
            interaction_state=interaction_state,
        )

    @pytest.mark.asyncio
    async def test_dispatch_online(self):
        """Robot connected → state delivered → returns True."""
        ctrl = FakeController(connected=True)
        orch = RobotOrchestrator(ctrl)
        resp = self._make_response("thinking")
        result = await orch.dispatch(resp)
        assert result is True
        assert ctrl.sent_states == ["thinking"]

    @pytest.mark.asyncio
    async def test_dispatch_offline(self):
        """Robot offline → returns False, no error raised."""
        ctrl = FakeController(connected=False)
        orch = RobotOrchestrator(ctrl)
        resp = self._make_response("speaking")
        result = await orch.dispatch(resp)
        assert result is False

    @pytest.mark.asyncio
    async def test_dispatch_send_failure(self):
        """Controller raises → orchestrator catches → returns False."""
        ctrl = BrokenController()
        orch = RobotOrchestrator(ctrl)
        resp = self._make_response("happy")
        result = await orch.dispatch(resp)
        assert result is False

    @pytest.mark.asyncio
    async def test_dispatch_confused_maps_to_thinking(self):
        ctrl = FakeController()
        orch = RobotOrchestrator(ctrl)
        resp = self._make_response("confused")
        result = await orch.dispatch(resp)
        assert result is True
        assert ctrl.sent_states == ["thinking"]

    @pytest.mark.asyncio
    async def test_dispatch_all_interaction_states(self):
        """Every InteractionState produces a valid dispatch."""
        for state in InteractionState:
            ctrl = FakeController()
            orch = RobotOrchestrator(ctrl)
            resp = self._make_response(state.value)
            result = await orch.dispatch(resp)
            assert result is True
            assert len(ctrl.sent_states) == 1

    @pytest.mark.asyncio
    async def test_dispatch_never_raises(self):
        """dispatch() never propagates, even with a broken controller."""
        ctrl = BrokenController()
        orch = RobotOrchestrator(ctrl)
        for state in InteractionState:
            resp = self._make_response(state.value)
            result = await orch.dispatch(resp)
            assert result is False


# ===========================================================================
# /chat integration tests
# ===========================================================================

class TestChatRobotIntegration:
    def test_chat_works_with_robot_offline(self, client):
        """Robot not connected → /chat still returns 200."""
        r = client.post("/chat", json={
            "session_id": "m5-test",
            "message": "What is force?",
        })
        assert r.status_code == 200
        data = r.json()
        assert "response_text" in data
        assert "interaction_state" in data

    def test_chat_dispatches_when_robot_online(self, client):
        """Robot connected → state is delivered via orchestrator."""
        with client.websocket_connect("/ws/robot") as ws:
            r = client.post("/chat", json={
                "session_id": "m5-test",
                "message": "What is force?",
            })
            assert r.status_code == 200

            # Robot should have received a state message
            msg = ws.receive_json()
            assert msg["type"] == "robot_state"
            assert msg["state"] in {"idle", "listening", "thinking",
                                     "speaking", "happy", "encouraging",
                                     "excited"}
            assert msg["version"] == 1

    def test_chat_response_unchanged_with_robot(self, client):
        """Response schema is identical whether robot is on or off."""
        # Without robot
        r1 = client.post("/chat", json={
            "session_id": "s1", "message": "test",
        })
        assert r1.status_code == 200
        keys_offline = set(r1.json().keys())

        # With robot
        with client.websocket_connect("/ws/robot"):
            r2 = client.post("/chat", json={
                "session_id": "s2", "message": "test",
            })
            assert r2.status_code == 200
            keys_online = set(r2.json().keys())

        assert keys_offline == keys_online
        # No hardware fields leaked
        assert "servo" not in keys_online
        assert "gpio" not in keys_online
        assert "led" not in keys_online
        assert "robot_state" not in keys_online

    def test_provider_error_still_returns_503(self, client):
        """LLM provider failure is unaffected by robot layer."""
        from primus.provider import LLMProviderError

        def broken(*a, **kw):
            raise LLMProviderError("down")

        client.app.state.engine.chat = broken

        with client.websocket_connect("/ws/robot"):
            r = client.post("/chat", json={
                "session_id": "s", "message": "test",
            })
            assert r.status_code == 503
