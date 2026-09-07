"""Tests for PRIMUS V1 — Robot communication layer.

Covers:
- Protocol validation (allowed states, message building, parsing)
- RobotController (connect, disconnect, send, failure paths)
- WebSocket endpoint lifecycle
- POST /robot/state (connected, offline, invalid state)

All tests use mocks / fakes — no real ESP32 hardware required.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from primus.api.app import create_app
from primus.provider import LLMProvider
from primus.robot.controller import RobotController
from primus.robot.protocol import (
    ALLOWED_STATES,
    PROTOCOL_VERSION,
    build_state_message,
    parse_message,
    validate_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockLLMProvider(LLMProvider):
    def complete(self, messages):
        return json.dumps({
            "response_text": "ok",
            "language": "en",
            "interaction_state": "idle",
        })


@pytest.fixture
def app():
    return create_app(provider=MockLLMProvider())


@pytest.fixture
def client(app):
    return TestClient(app)


# ===========================================================================
# Protocol tests
# ===========================================================================

class TestProtocol:
    def test_all_expected_states(self):
        expected = {"idle", "listening", "thinking", "speaking",
                    "happy", "encouraging", "excited"}
        assert ALLOWED_STATES == expected

    def test_validate_known_states(self):
        for s in ALLOWED_STATES:
            assert validate_state(s) is True

    def test_validate_unknown_state(self):
        assert validate_state("dance_party") is False
        assert validate_state("") is False
        assert validate_state("IDLE") is False  # case-sensitive

    def test_build_state_message(self):
        msg = build_state_message("thinking")
        assert msg == {
            "version": PROTOCOL_VERSION,
            "type": "robot_state",
            "state": "thinking",
        }

    def test_build_message_invalid_state_raises(self):
        with pytest.raises(ValueError, match="Unknown robot state"):
            build_state_message("flying")

    def test_parse_valid_message(self):
        msg = {"version": 1, "type": "robot_state", "state": "idle"}
        assert parse_message(msg) == msg

    def test_parse_wrong_version(self):
        msg = {"version": 99, "type": "robot_state", "state": "idle"}
        assert parse_message(msg) is None

    def test_parse_wrong_type(self):
        msg = {"version": 1, "type": "motor_command", "state": "idle"}
        assert parse_message(msg) is None

    def test_parse_not_a_dict(self):
        assert parse_message("hello") is None
        assert parse_message([1, 2]) is None
        assert parse_message(None) is None

    def test_parse_empty_dict(self):
        assert parse_message({}) is None


# ===========================================================================
# RobotController tests
# ===========================================================================

class FakeWebSocket:
    """Minimal async fake for unit tests."""

    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed = True


class BrokenWebSocket(FakeWebSocket):
    async def send_json(self, data):
        raise RuntimeError("connection lost")


class TestRobotController:
    @pytest.mark.asyncio
    async def test_initial_state(self):
        ctrl = RobotController()
        assert ctrl.is_connected() is False

    @pytest.mark.asyncio
    async def test_connect(self):
        ctrl = RobotController()
        ws = FakeWebSocket()
        await ctrl.connect(ws)
        assert ctrl.is_connected() is True

    @pytest.mark.asyncio
    async def test_disconnect(self):
        ctrl = RobotController()
        ws = FakeWebSocket()
        await ctrl.connect(ws)
        await ctrl.disconnect()
        assert ctrl.is_connected() is False

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self):
        ctrl = RobotController()
        await ctrl.disconnect()  # should not raise
        assert ctrl.is_connected() is False

    @pytest.mark.asyncio
    async def test_send_state_connected(self):
        ctrl = RobotController()
        ws = FakeWebSocket()
        await ctrl.connect(ws)
        result = await ctrl.send_state("thinking")
        assert result is True
        assert len(ws.sent) == 1
        assert ws.sent[0]["state"] == "thinking"
        assert ws.sent[0]["version"] == PROTOCOL_VERSION

    @pytest.mark.asyncio
    async def test_send_state_not_connected(self):
        ctrl = RobotController()
        result = await ctrl.send_state("idle")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_unknown_state_raises(self):
        ctrl = RobotController()
        ws = FakeWebSocket()
        await ctrl.connect(ws)
        with pytest.raises(ValueError):
            await ctrl.send_state("fly_away")

    @pytest.mark.asyncio
    async def test_send_failure_clears_connection(self):
        ctrl = RobotController()
        ws = BrokenWebSocket()
        await ctrl.connect(ws)
        result = await ctrl.send_state("idle")
        assert result is False
        assert ctrl.is_connected() is False

    @pytest.mark.asyncio
    async def test_connect_replaces_existing(self):
        ctrl = RobotController()
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await ctrl.connect(ws1)
        await ctrl.connect(ws2)
        assert ws1.closed is True   # old connection was closed
        assert ctrl.is_connected() is True

    @pytest.mark.asyncio
    async def test_all_allowed_states_sendable(self):
        ctrl = RobotController()
        ws = FakeWebSocket()
        await ctrl.connect(ws)
        for state in ALLOWED_STATES:
            result = await ctrl.send_state(state)
            assert result is True
        assert len(ws.sent) == len(ALLOWED_STATES)


# ===========================================================================
# WebSocket endpoint tests
# ===========================================================================

class TestWebSocketEndpoint:
    def test_ws_connect_disconnect(self, client):
        """Robot can connect and disconnect via /ws/robot."""
        ctrl = client.app.state.robot_controller
        assert ctrl.is_connected() is False

        with client.websocket_connect("/ws/robot") as ws:
            assert ctrl.is_connected() is True

        # After context manager exits, disconnect should have fired
        assert ctrl.is_connected() is False

    def test_ws_receives_state(self, client):
        """Backend can send a state to the connected robot."""
        with client.websocket_connect("/ws/robot") as ws:
            # Send a state via the controller
            ctrl = client.app.state.robot_controller
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(ctrl.send_state("thinking"))
            finally:
                loop.close()
            assert result is True

            # The robot (test client) should receive the message
            data = ws.receive_json()
            assert data["state"] == "thinking"
            assert data["version"] == PROTOCOL_VERSION
            assert data["type"] == "robot_state"


# ===========================================================================
# POST /robot/state tests
# ===========================================================================

class TestRobotStateEndpoint:
    def test_robot_offline_503(self, client):
        """No ESP32 connected → 503."""
        r = client.post("/robot/state", json={"state": "thinking"})
        assert r.status_code == 503
        assert r.json()["status"] == "robot_offline"

    def test_invalid_state_400(self, client):
        """Unknown state → 400 with allowed states list."""
        r = client.post("/robot/state", json={"state": "fly_away"})
        assert r.status_code == 400
        data = r.json()
        assert data["status"] == "invalid_state"
        assert "idle" in data["allowed_states"]

    def test_successful_send(self, client):
        """Robot connected → state sent → 200."""
        with client.websocket_connect("/ws/robot"):
            r = client.post("/robot/state", json={"state": "thinking"})
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "sent"
            assert data["state"] == "thinking"

    def test_all_states_accepted(self, client):
        """Every allowed state is accepted when robot is connected."""
        with client.websocket_connect("/ws/robot"):
            for state in sorted(ALLOWED_STATES):
                r = client.post("/robot/state", json={"state": state})
                assert r.status_code == 200, f"State '{state}' rejected"
                assert r.json()["status"] == "sent"

    def test_missing_state_field_422(self, client):
        """Missing 'state' field → FastAPI 422."""
        r = client.post("/robot/state", json={})
        assert r.status_code == 422


# ===========================================================================
# POST /robot/audio tests
# ===========================================================================

@pytest.fixture
def app_with_pcm(tmp_path):
    """App whose TTS output dir (tmp) contains a fake PCM file."""
    application = create_app(provider=MockLLMProvider())
    from primus.voice.service import TTSService
    application.state.tts_service = TTSService(
        provider=application.state.tts_service._provider,
        output_dir=tmp_path / "audio",
    )
    pcm_file = tmp_path / "audio" / "tts_test123.pcm"
    pcm_file.parent.mkdir(parents=True, exist_ok=True)
    pcm_file.write_bytes(b"\x00\x01" * 100)   # 200 bytes of fake PCM16
    return application


class TestRobotAudioEndpoint:
    def test_robot_offline_503(self, app_with_pcm):
        """No ESP32 connected → 503 (file existence checked first)."""
        client = TestClient(app_with_pcm)
        r = client.post("/robot/audio", json={"filename": "tts_test123.pcm"})
        assert r.status_code == 503
        assert r.json()["status"] == "robot_offline"

    def test_path_traversal_400(self, app_with_pcm):
        """Filename with '..', '/', or '\\' → 400."""
        client = TestClient(app_with_pcm)
        for bad in ["../secret.pcm", "a/b.pcm", "a\\b.pcm"]:
            r = client.post("/robot/audio", json={"filename": bad})
            assert r.status_code == 400, f"'{bad}' should be rejected"
            assert r.json()["status"] == "invalid_filename"

    def test_invalid_sample_rate_400(self, app_with_pcm):
        """Non-positive sample rate → 400."""
        client = TestClient(app_with_pcm)
        r = client.post("/robot/audio", json={
            "filename": "tts_test123.pcm", "sample_rate": 0,
        })
        assert r.status_code == 400
        assert r.json()["status"] == "invalid_sample_rate"

    def test_file_not_found_404(self, app_with_pcm):
        """Filename not in TTS output dir → 404."""
        client = TestClient(app_with_pcm)
        r = client.post("/robot/audio", json={"filename": "missing.pcm"})
        assert r.status_code == 404
        assert r.json()["status"] == "file_not_found"

    def test_missing_filename_422(self, app_with_pcm):
        """Missing 'filename' field → FastAPI 422."""
        client = TestClient(app_with_pcm)
        r = client.post("/robot/audio", json={})
        assert r.status_code == 422

    def test_successful_send_default_rate(self, app_with_pcm):
        """Robot connected + file exists → 200, default 16 kHz rate."""
        client = TestClient(app_with_pcm)
        with client.websocket_connect("/ws/robot"):
            r = client.post("/robot/audio", json={"filename": "tts_test123.pcm"})
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "sent"
            assert data["audio_url"] == "/audio/tts_test123.pcm"
            assert data["sample_rate"] == 16000

    def test_successful_send_custom_rate(self, app_with_pcm):
        """Explicit sample rate overrides the default."""
        client = TestClient(app_with_pcm)
        with client.websocket_connect("/ws/robot"):
            r = client.post("/robot/audio", json={
                "filename": "tts_test123.pcm", "sample_rate": 44100,
            })
            assert r.status_code == 200
            assert r.json()["sample_rate"] == 44100

    def test_robot_receives_audio_message(self, app_with_pcm):
        """The ESP32 receives a well-formed robot_audio protocol message."""
        client = TestClient(app_with_pcm)
        with client.websocket_connect("/ws/robot") as ws:
            r = client.post("/robot/audio", json={
                "filename": "tts_test123.pcm", "sample_rate": 16000,
            })
            assert r.status_code == 200

            msg = ws.receive_json()
            assert msg["version"] == PROTOCOL_VERSION
            assert msg["type"] == "robot_audio"
            assert msg["audio_url"] == "/audio/tts_test123.pcm"
            assert msg["sample_rate"] == 16000
            assert msg["format"] == "pcm16"
