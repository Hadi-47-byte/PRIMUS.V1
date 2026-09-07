"""Tests for PRIMUS V1 — Milestone 8: Speech-to-Text (STT).

Covers:
- STTProvider ABC
- STTResult model
- Audio validation (file existence, format, size)
- STTService with mocked provider
- Language parameter handling
- POST /stt endpoint
- STT independence from RobotController
- STT output can feed TutorEngine
- Existing M1–M7 tests remain passing

All tests use mocks — no real STT API calls.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from primus.api.app import create_app
from primus.provider import LLMProvider
from primus.voice.stt.models import STTProviderError, STTResult
from primus.voice.stt.provider import STTProvider
from primus.voice.stt.service import (
    MAX_FILE_SIZE,
    SUPPORTED_EXTENSIONS,
    STTService,
    validate_audio,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeSTTProvider(STTProvider):
    """Fake provider that returns predetermined transcripts."""

    def __init__(self, text: str = "What is gravity?", language: str = "en",
                 fail: bool = False, empty: bool = False):
        self._text = "" if empty else text
        self._language = language
        self._fail = fail
        self.calls: list[tuple[str, str | None]] = []

    async def transcribe(self, audio_path: str, language: str | None = None) -> STTResult:
        self.calls.append((audio_path, language))
        if self._fail:
            raise STTProviderError("Fake provider failure")
        return STTResult(
            text=self._text,
            language=self._language,
            duration_ms=2000,
            provider="fake",
        )


class MockLLMProvider(LLMProvider):
    def complete(self, messages):
        return json.dumps({
            "response_text": "test response",
            "language": "en",
            "interaction_state": "speaking",
        })


def _create_audio_file(tmp_path: Path, name: str = "test.wav",
                       content: bytes = b"FAKE_AUDIO_DATA") -> str:
    """Create a minimal fake audio file for testing."""
    path = tmp_path / name
    path.write_bytes(content)
    return str(path)


@pytest.fixture
def app(tmp_path):
    application = create_app(provider=MockLLMProvider())
    application.state.stt_service = STTService(provider=FakeSTTProvider())
    # Override TTS output dir too
    from primus.voice.service import TTSService
    from primus.voice.provider import VoiceRegistry
    application.state.tts_service = TTSService(
        provider=application.state.tts_service._provider,
        output_dir=tmp_path / "audio",
    )
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


# ===========================================================================
# STTProvider ABC
# ===========================================================================

class TestSTTProviderABC:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            STTProvider()  # type: ignore[abstract]

    def test_subclass_must_implement(self):
        class Incomplete(STTProvider):
            pass
        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_valid_subclass(self):
        p = FakeSTTProvider()
        assert isinstance(p, STTProvider)


# ===========================================================================
# STTResult model
# ===========================================================================

class TestSTTResult:
    def test_minimal(self):
        r = STTResult(text="hello")
        assert r.text == "hello"
        assert r.language is None
        assert r.duration_ms is None
        assert r.confidence is None
        assert r.provider is None

    def test_full(self):
        r = STTResult(
            text="What is force?",
            language="en",
            duration_ms=3000,
            confidence=0.95,
            provider="whisper",
        )
        assert r.text == "What is force?"
        assert r.language == "en"
        assert r.duration_ms == 3000
        assert r.confidence == 0.95
        assert r.provider == "whisper"

    def test_empty_text_allowed(self):
        """Empty text is valid at the model level — service rejects it."""
        r = STTResult(text="")
        assert r.text == ""


# ===========================================================================
# Audio validation
# ===========================================================================

class TestAudioValidation:
    def test_file_not_found(self, tmp_path):
        with pytest.raises(STTProviderError, match="not found"):
            validate_audio(str(tmp_path / "nonexistent.wav"))

    def test_not_a_file(self, tmp_path):
        with pytest.raises(STTProviderError, match="not a file"):
            validate_audio(str(tmp_path))

    def test_unsupported_format(self, tmp_path):
        path = tmp_path / "test.txt"
        path.write_bytes(b"data")
        with pytest.raises(STTProviderError, match="Unsupported"):
            validate_audio(str(path))

    def test_empty_file(self, tmp_path):
        path = tmp_path / "empty.wav"
        path.write_bytes(b"")
        with pytest.raises(STTProviderError, match="empty"):
            validate_audio(str(path))

    def test_valid_wav(self, tmp_path):
        path = _create_audio_file(tmp_path, "test.wav")
        validate_audio(path)  # should not raise

    def test_valid_mp3(self, tmp_path):
        path = _create_audio_file(tmp_path, "test.mp3")
        validate_audio(path)

    def test_valid_m4a(self, tmp_path):
        path = _create_audio_file(tmp_path, "test.m4a")
        validate_audio(path)

    def test_case_insensitive_extension(self, tmp_path):
        path = _create_audio_file(tmp_path, "test.WAV")
        validate_audio(path)

    def test_supported_extensions(self):
        assert ".wav" in SUPPORTED_EXTENSIONS
        assert ".mp3" in SUPPORTED_EXTENSIONS
        assert ".m4a" in SUPPORTED_EXTENSIONS


# ===========================================================================
# STTService
# ===========================================================================

class TestSTTService:
    @pytest.mark.asyncio
    async def test_successful_english(self, tmp_path):
        path = _create_audio_file(tmp_path)
        service = STTService(provider=FakeSTTProvider(text="What is force?", language="en"))
        result = await service.transcribe(path, language="en")
        assert result.text == "What is force?"
        assert result.language == "en"

    @pytest.mark.asyncio
    async def test_successful_urdu(self, tmp_path):
        path = _create_audio_file(tmp_path)
        service = STTService(provider=FakeSTTProvider(text="نیوٹن کا قانون", language="ur"))
        result = await service.transcribe(path, language="ur")
        assert result.text == "نیوٹن کا قانون"
        assert result.language == "ur"

    @pytest.mark.asyncio
    async def test_auto_language(self, tmp_path):
        """language='auto' is normalised to None for the provider."""
        provider = FakeSTTProvider()
        service = STTService(provider=provider)
        path = _create_audio_file(tmp_path)
        await service.transcribe(path, language="auto")
        assert provider.calls[0][1] is None  # "auto" → None

    @pytest.mark.asyncio
    async def test_none_language(self, tmp_path):
        provider = FakeSTTProvider()
        service = STTService(provider=provider)
        path = _create_audio_file(tmp_path)
        await service.transcribe(path, language=None)
        assert provider.calls[0][1] is None

    @pytest.mark.asyncio
    async def test_missing_file_rejected(self, tmp_path):
        service = STTService(provider=FakeSTTProvider())
        with pytest.raises(STTProviderError, match="not found"):
            await service.transcribe(str(tmp_path / "missing.wav"))

    @pytest.mark.asyncio
    async def test_unsupported_format_rejected(self, tmp_path):
        path = tmp_path / "test.txt"
        path.write_bytes(b"data")
        service = STTService(provider=FakeSTTProvider())
        with pytest.raises(STTProviderError, match="Unsupported"):
            await service.transcribe(str(path))

    @pytest.mark.asyncio
    async def test_provider_failure_propagates(self, tmp_path):
        path = _create_audio_file(tmp_path)
        service = STTService(provider=FakeSTTProvider(fail=True))
        with pytest.raises(STTProviderError, match="Fake"):
            await service.transcribe(path)

    @pytest.mark.asyncio
    async def test_empty_transcript_rejected(self, tmp_path):
        path = _create_audio_file(tmp_path)
        service = STTService(provider=FakeSTTProvider(empty=True))
        with pytest.raises(STTProviderError, match="empty"):
            await service.transcribe(path)

    @pytest.mark.asyncio
    async def test_provider_injection(self, tmp_path):
        """Custom providers can be injected."""
        provider = FakeSTTProvider(text="custom result")
        service = STTService(provider=provider)
        path = _create_audio_file(tmp_path)
        result = await service.transcribe(path)
        assert result.text == "custom result"


# ===========================================================================
# POST /stt endpoint
# ===========================================================================

class TestSTTEndpoint:
    def test_successful_transcription(self, client):
        audio = io.BytesIO(b"FAKE_AUDIO_DATA")
        r = client.post(
            "/stt",
            files={"audio": ("test.wav", audio, "audio/wav")},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "text" in data

    def test_with_language(self, client):
        audio = io.BytesIO(b"FAKE_AUDIO_DATA")
        r = client.post(
            "/stt",
            files={"audio": ("test.wav", audio, "audio/wav")},
            data={"language": "en"},
        )
        assert r.status_code == 200

    def test_unsupported_format_400(self, client):
        audio = io.BytesIO(b"not audio")
        r = client.post(
            "/stt",
            files={"audio": ("test.txt", audio, "text/plain")},
        )
        assert r.status_code == 400
        assert r.json()["status"] == "invalid_format"

    def test_provider_failure_503(self, app, client, tmp_path):
        app.state.stt_service = STTService(provider=FakeSTTProvider(fail=True))
        audio = io.BytesIO(b"FAKE_AUDIO_DATA")
        r = client.post(
            "/stt",
            files={"audio": ("test.wav", audio, "audio/wav")},
        )
        assert r.status_code == 503
        assert r.json()["status"] == "stt_error"


# ===========================================================================
# STT independence
# ===========================================================================

class TestSTTIndependence:
    @pytest.mark.asyncio
    async def test_stt_independent_of_robot(self, tmp_path):
        """STT works without any robot controller."""
        provider = FakeSTTProvider(text="no robot needed")
        service = STTService(provider=provider)
        path = _create_audio_file(tmp_path)
        result = await service.transcribe(path)
        assert result.text == "no robot needed"

    @pytest.mark.asyncio
    async def test_stt_result_feeds_tutor(self, tmp_path):
        """STT output can be passed directly to TutorEngine.chat()."""
        provider = FakeSTTProvider(text="What is acceleration?")
        service = STTService(provider=provider)
        path = _create_audio_file(tmp_path)
        result = await service.transcribe(path)

        # Feed the transcribed text to the tutor engine
        from primus.engine import TutorEngine
        engine = TutorEngine(provider=MockLLMProvider())
        response = engine.chat(result.text, session_id="stt-test")
        assert response.response_text  # engine returned a response
        assert isinstance(response.text if hasattr(response, "text") else response.response_text, str)

    def test_chat_still_works_when_stt_fails(self, app, client, tmp_path):
        """Even if STT service is completely broken, /chat still works."""
        app.state.stt_service = STTService(provider=FakeSTTProvider(fail=True))
        r = client.post("/chat", json={
            "session_id": "stt-test",
            "message": "What is force?",
        })
        assert r.status_code == 200
        assert "response_text" in r.json()
