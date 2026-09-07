"""Tests for PRIMUS V1 — Milestone 9: Voice Conversation Orchestration.

Covers:
- Successful end-to-end pipeline with mocks
- STT → TutorEngine data flow
- TutorResponse → TTS data flow
- TutorResponse → ExpressionMapper data flow
- Session ID preservation
- Multi-turn session continuity
- Failure isolation (STT/Tutor/TTS/Robot)
- Timing instrumentation
- POST /voice endpoint
- Full software integration with fake providers

All tests use mocks — no real STT/LLM/TTS/ESP32 calls.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from primus.api.app import create_app
from primus.engine import TutorEngine
from primus.models import InteractionState, TutorResponse
from primus.provider import LLMProvider, LLMProviderError
from primus.robot.controller import RobotController
from primus.robot.expressions import RobotOrchestrator
from primus.voice.models import TTSProviderError, TTSResult
from primus.voice.orchestrator import VoiceConversationResult, VoiceConversationService
from primus.voice.provider import TTSProvider
from primus.voice.service import TTSService
from primus.voice.stt.models import STTProviderError, STTResult
from primus.voice.stt.provider import STTProvider
from primus.voice.stt.service import STTService


# ---------------------------------------------------------------------------
# Fake providers
# ---------------------------------------------------------------------------

class FakeSTTProvider(STTProvider):
    def __init__(self, text: str = "What is force?", language: str = "en"):
        self._text = text
        self._language = language
        self.calls: list[tuple[str, str | None]] = []

    async def transcribe(self, audio_path: str, language: str | None = None) -> STTResult:
        self.calls.append((audio_path, language))
        return STTResult(text=self._text, language=self._language, duration_ms=2000, provider="fake")


class FakeTTSProvider(TTSProvider):
    def __init__(self, fail: bool = False):
        self._fail = fail
        self.calls: list[tuple[str, str, str]] = []

    async def synthesize(self, text: str, voice: str, output_path: str) -> TTSResult:
        self.calls.append((text, voice, output_path))
        if self._fail:
            raise TTSProviderError("Fake TTS failure")
        # Write a tiny fake file so path validation works downstream
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"FAKE_AUDIO")
        return TTSResult(
            audio_path=output_path, format="mp3",
            duration_ms=3000, language="en", voice=voice,
        )


class MockLLMProvider(LLMProvider):
    def __init__(self, response_text: str = "Force is a push or pull.",
                 interaction_state: str = "speaking", language: str = "en"):
        self._response_text = response_text
        self._interaction_state = interaction_state
        self._language = language
        self.calls: list[list[dict]] = []

    def complete(self, messages):
        self.calls.append(messages)
        return json.dumps({
            "response_text": self._response_text,
            "language": self._language,
            "interaction_state": self._interaction_state,
        })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_service(
    stt_text: str = "What is force?",
    stt_language: str = "en",
    llm_response: str = "Force is a push or pull.",
    llm_state: str = "speaking",
    tts_fail: bool = False,
    tmp_path: Path | None = None,
) -> tuple[VoiceConversationService, FakeSTTProvider, MockLLMProvider, FakeTTSProvider, RobotController]:
    stt_provider = FakeSTTProvider(text=stt_text, language=stt_language)
    stt_service = STTService(provider=stt_provider)

    llm_provider = MockLLMProvider(response_text=llm_response, interaction_state=llm_state)
    engine = TutorEngine(provider=llm_provider)

    tts_provider = FakeTTSProvider(fail=tts_fail)
    out_dir = tmp_path / "audio" if tmp_path else "data/audio"
    tts_service = TTSService(provider=tts_provider, output_dir=out_dir)

    robot_controller = RobotController()
    robot_orchestrator = RobotOrchestrator(robot_controller)

    service = VoiceConversationService(
        stt_service=stt_service,
        engine=engine,
        tts_service=tts_service,
        robot_orchestrator=robot_orchestrator,
    )
    return service, stt_provider, llm_provider, tts_provider, robot_controller


@pytest.fixture
def audio_file(tmp_path: Path) -> str:
    """Create a fake audio file for testing."""
    p = tmp_path / "test.wav"
    p.write_bytes(b"FAKE_AUDIO_DATA")
    return str(p)


@pytest.fixture
def app_with_voice(tmp_path):
    """Build an app with fake providers for endpoint testing."""
    llm = MockLLMProvider()
    application = create_app(provider=llm)

    # Replace STT service with fake
    application.state.stt_service = STTService(provider=FakeSTTProvider())

    # Replace TTS service with fake
    application.state.tts_service = TTSService(
        provider=FakeTTSProvider(), output_dir=tmp_path / "audio",
    )

    # Rebuild voice service with fakes
    application.state.voice_service = VoiceConversationService(
        stt_service=application.state.stt_service,
        engine=application.state.engine,
        tts_service=application.state.tts_service,
        robot_orchestrator=application.state.orchestrator,
    )
    return application


@pytest.fixture
def client(app_with_voice):
    return TestClient(app_with_voice)


# ===========================================================================
# Successful end-to-end pipeline
# ===========================================================================

class TestEndToEndPipeline:
    @pytest.mark.asyncio
    async def test_full_pipeline_success(self, audio_file, tmp_path):
        service, stt, llm, tts, robot = _build_service(tmp_path=tmp_path)
        result = await service.process_audio(audio_file, session_id="e2e-1")

        assert isinstance(result, VoiceConversationResult)
        assert result.transcript == "What is force?"
        assert result.transcript_language == "en"
        assert result.tutor_response.response_text == "Force is a push or pull."
        assert result.audio_path is not None
        assert result.audio_format == "mp3"
        assert result.robot_state == "speaking"  # interaction_state=speaking → robot=speaking

    @pytest.mark.asyncio
    async def test_stt_receives_correct_transcript(self, audio_file, tmp_path):
        """STT output is passed to TutorEngine.chat()."""
        service, stt, llm, tts, robot = _build_service(
            stt_text="Explain acceleration", tmp_path=tmp_path,
        )
        result = await service.process_audio(audio_file, session_id="flow-1")

        assert result.transcript == "Explain acceleration"
        # Verify engine received the transcript (check the LLM was called)
        assert len(llm.calls) == 1
        # The last user message in the conversation should contain the transcript
        user_messages = [m for m in llm.calls[0] if m.get("role") == "user"]
        assert any("Explain acceleration" in m["content"] for m in user_messages)

    @pytest.mark.asyncio
    async def test_tts_receives_tutor_response_text(self, audio_file, tmp_path):
        """TTS receives TutorResponse.response_text, not the STT transcript."""
        service, stt, llm, tts, robot = _build_service(
            stt_text="What is force?",
            llm_response="Force causes acceleration.",
            tmp_path=tmp_path,
        )
        await service.process_audio(audio_file, session_id="tts-1")

        assert len(tts.calls) == 1
        assert tts.calls[0][0] == "Force causes acceleration."  # text arg

    @pytest.mark.asyncio
    async def test_robot_state_mapped_deterministically(self, audio_file, tmp_path):
        """Robot state is deterministically mapped from interaction_state."""
        service, _, llm, _, _ = _build_service(
            llm_state="happy", tmp_path=tmp_path,
        )
        result = await service.process_audio(audio_file, session_id="robot-1")
        assert result.robot_state == "happy"

    @pytest.mark.asyncio
    async def test_session_id_preserved(self, audio_file, tmp_path):
        """The same session_id flows through the pipeline."""
        service, _, _, _, _ = _build_service(tmp_path=tmp_path)
        result = await service.process_audio(audio_file, session_id="session-abc")
        assert result.session_id == "session-abc"


# ===========================================================================
# Multi-turn session continuity
# ===========================================================================

class TestMultiTurn:
    @pytest.mark.asyncio
    async def test_multi_turn_preserves_context(self, tmp_path):
        """Second turn uses existing session context from first turn."""
        service, stt, llm, tts, _ = _build_service(tmp_path=tmp_path)

        audio1 = tmp_path / "q1.wav"
        audio1.write_bytes(b"AUDIO_1")
        audio2 = tmp_path / "q2.wav"
        audio2.write_bytes(b"AUDIO_2")

        # Turn 1
        stt._text = "What is force?"
        result1 = await service.process_audio(str(audio1), session_id="multi-1")
        assert result1.transcript == "What is force?"

        # Turn 2 — same session
        stt._text = "Explain it more simply."
        result2 = await service.process_audio(str(audio2), session_id="multi-1")
        assert result2.transcript == "Explain it more simply."

        # Both turns used the same session_id
        assert result1.session_id == result2.session_id == "multi-1"

        # Second LLM call should have context from first turn
        assert len(llm.calls) == 2


# ===========================================================================
# Failure isolation
# ===========================================================================

class TestFailureIsolation:
    @pytest.mark.asyncio
    async def test_stt_failure_stops_pipeline(self, tmp_path):
        """STT failure → STTProviderError, no TutorEngine call."""
        # Build with a failing STT provider
        class FailSTT(STTProvider):
            async def transcribe(self, audio_path, language=None):
                raise STTProviderError("STT broke")

        stt_service = STTService(provider=FailSTT())
        llm = MockLLMProvider()
        engine = TutorEngine(provider=llm)
        tts_service = TTSService(provider=FakeTTSProvider(), output_dir=tmp_path / "audio")
        robot_orch = RobotOrchestrator(RobotController())

        service = VoiceConversationService(
            stt_service=stt_service, engine=engine,
            tts_service=tts_service, robot_orchestrator=robot_orch,
        )

        audio = tmp_path / "fail.wav"
        audio.write_bytes(b"DATA")

        with pytest.raises(STTProviderError):
            await service.process_audio(str(audio), session_id="fail-1")

        # TutorEngine was never called
        assert len(llm.calls) == 0

    @pytest.mark.asyncio
    async def test_tutor_failure_stops_downstream(self, audio_file, tmp_path):
        """LLMProviderError → no TTS or robot attempt."""
        class FailLLM(LLMProvider):
            def complete(self, messages):
                raise LLMProviderError("LLM offline")

        engine = TutorEngine(provider=FailLLM())
        stt_service = STTService(provider=FakeSTTProvider())
        tts_provider = FakeTTSProvider()
        tts_service = TTSService(provider=tts_provider, output_dir=tmp_path / "audio")
        robot_orch = RobotOrchestrator(RobotController())

        service = VoiceConversationService(
            stt_service=stt_service, engine=engine,
            tts_service=tts_service, robot_orchestrator=robot_orch,
        )

        with pytest.raises(LLMProviderError):
            await service.process_audio(audio_file, session_id="tutor-fail")

        # TTS was never called
        assert len(tts_provider.calls) == 0

    @pytest.mark.asyncio
    async def test_tts_failure_preserves_tutor_response(self, audio_file, tmp_path):
        """TTS failure → TutorResponse still succeeds, audio_path is None."""
        service, stt, llm, tts, _ = _build_service(
            tts_fail=True, tmp_path=tmp_path,
        )
        result = await service.process_audio(audio_file, session_id="tts-fail")

        # Tutor response is valid
        assert result.tutor_response.response_text == "Force is a push or pull."
        # Audio is None
        assert result.audio_path is None
        assert result.audio_format is None
        # Robot state may still be set (robot dispatch happens after TTS)
        assert result.robot_state == "speaking"

    @pytest.mark.asyncio
    async def test_robot_offline_preserves_response(self, audio_file, tmp_path):
        """Robot offline → TutorResponse and audio still succeed."""
        service, _, _, _, robot = _build_service(tmp_path=tmp_path)
        # Robot is not connected (default state)

        result = await service.process_audio(audio_file, session_id="robot-offline")

        assert result.tutor_response.response_text == "Force is a push or pull."
        assert result.audio_path is not None
        # Robot state is still mapped (intended state), even though delivery failed
        assert result.robot_state == "speaking"

    @pytest.mark.asyncio
    async def test_robot_send_failure_preserves_response(self, audio_file, tmp_path):
        """Robot send error → TutorResponse still succeeds."""
        service, _, _, _, robot = _build_service(tmp_path=tmp_path)

        # Mock send_state to fail
        robot.send_state = AsyncMock(return_value=False)

        result = await service.process_audio(audio_file, session_id="robot-send-fail")

        assert result.tutor_response.response_text == "Force is a push or pull."
        assert result.audio_path is not None
        assert result.robot_state == "speaking"  # intended state still recorded


# ===========================================================================
# Timing instrumentation
# ===========================================================================

class TestTiming:
    @pytest.mark.asyncio
    async def test_timings_recorded(self, audio_file, tmp_path):
        service, _, _, _, _ = _build_service(tmp_path=tmp_path)
        result = await service.process_audio(audio_file, session_id="timing-1")

        assert result.stt_latency_ms is not None
        assert result.stt_latency_ms >= 0
        assert result.tutor_latency_ms is not None
        assert result.tutor_latency_ms >= 0
        assert result.tts_latency_ms is not None
        assert result.tts_latency_ms >= 0
        assert result.total_latency_ms is not None
        assert result.total_latency_ms >= 0

    @pytest.mark.asyncio
    async def test_total_timing_ge_parts(self, audio_file, tmp_path):
        """Total timing should be at least as large as the individual parts."""
        service, _, _, _, _ = _build_service(tmp_path=tmp_path)
        result = await service.process_audio(audio_file, session_id="timing-2")

        parts = sum(filter(None, [
            result.stt_latency_ms,
            result.tutor_latency_ms,
            result.tts_latency_ms,
        ]))
        # Allow 5ms tolerance for timing imprecision
        assert result.total_latency_ms >= parts - 5


# ===========================================================================
# POST /voice endpoint
# ===========================================================================

class TestVoiceEndpoint:
    def test_successful_voice_conversation(self, client):
        audio = io.BytesIO(b"FAKE_AUDIO_DATA")
        r = client.post(
            "/voice",
            files={"audio": ("test.wav", audio, "audio/wav")},
            data={"session_id": "voice-ep-1", "language": "en"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["transcript"] == "What is force?"
        assert "response_text" in data["tutor_response"]
        assert data["timings"]["total_ms"] is not None

    def test_unsupported_format_400(self, client):
        audio = io.BytesIO(b"not audio")
        r = client.post(
            "/voice",
            files={"audio": ("test.txt", audio, "text/plain")},
            data={"session_id": "voice-ep-2"},
        )
        assert r.status_code == 400

    def test_chat_still_works_independently(self, client):
        """The existing /chat endpoint is not affected by /voice."""
        r = client.post("/chat", json={
            "session_id": "chat-ind",
            "message": "What is energy?",
        })
        assert r.status_code == 200
        assert "response_text" in r.json()


# ===========================================================================
# Full software integration (all fake providers)
# ===========================================================================

class TestFullIntegration:
    @pytest.mark.asyncio
    async def test_full_fake_pipeline(self, tmp_path):
        """Prove all software pieces connect with fake providers."""
        audio = tmp_path / "integration.wav"
        audio.write_bytes(b"INTEGRATION_AUDIO")

        service, stt, llm, tts, robot = _build_service(
            stt_text="What is Newton's second law?",
            llm_response="F equals m times a.",
            llm_state="speaking",
            tmp_path=tmp_path,
        )

        result = await service.process_audio(str(audio), session_id="integration")

        # Complete pipeline executed
        assert result.transcript == "What is Newton's second law?"
        assert result.tutor_response.response_text == "F equals m times a."
        assert result.audio_path is not None
        assert result.audio_format == "mp3"
        assert result.robot_state == "speaking"
        assert result.total_latency_ms >= 0

        # Verify TTS received tutor response text
        assert tts.calls[0][0] == "F equals m times a."

        # Verify STT was called
        assert len(stt.calls) == 1

    @pytest.mark.asyncio
    async def test_stt_result_feeds_tutor_correctly(self, tmp_path):
        """Verify the exact data flow: STT text → TutorEngine."""
        audio = tmp_path / "flow.wav"
        audio.write_bytes(b"FLOW_AUDIO")

        service, stt, llm, tts, _ = _build_service(
            stt_text="Explain momentum",
            llm_response="Momentum is mass times velocity.",
            tmp_path=tmp_path,
        )

        result = await service.process_audio(str(audio), session_id="flow")

        # STT provider received the file
        assert stt.calls[0][0] == str(audio)

        # Engine received the transcript in the user messages
        user_msgs = [m for m in llm.calls[0] if m["role"] == "user"]
        assert any("Explain momentum" in m["content"] for m in user_msgs)

        # TTS received the tutor response
        assert tts.calls[0][0] == "Momentum is mass times velocity."

    @pytest.mark.asyncio
    async def test_confused_interaction_maps_to_thinking(self, audio_file, tmp_path):
        """CONFUSED interaction_state → 'thinking' robot state."""
        service, _, _, _, _ = _build_service(
            llm_state="confused", tmp_path=tmp_path,
        )
        result = await service.process_audio(audio_file, session_id="confused-1")
        assert result.robot_state == "thinking"
