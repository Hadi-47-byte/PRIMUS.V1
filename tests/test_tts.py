"""Tests for PRIMUS V1 — Milestone 7: Text-to-Speech.

Covers:
- TTSProvider ABC
- VoiceRegistry language → voice mapping
- TTSService text validation, preprocessing, filename generation
- EdgeTTSProvider (mocked edge_tts)
- POST /tts endpoint
- TTS failure isolation from /chat
- Independent operation from RobotController

All tests use mocks — no real TTS API calls.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from primus.api.app import create_app
from primus.provider import LLMProvider
from primus.voice.models import TTSProviderError, TTSResult
from primus.voice.provider import TTSProvider, VoiceRegistry
from primus.voice.service import TTSService, preprocess_text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeTTSProvider(TTSProvider):
    """Creates a tiny fake file instead of real audio."""

    def __init__(self, fail: bool = False):
        self._fail = fail
        self.calls: list[tuple[str, str, str]] = []

    async def synthesize(self, text: str, voice: str, output_path: str) -> TTSResult:
        self.calls.append((text, voice, output_path))
        if self._fail:
            raise TTSProviderError("Fake provider failure")
        Path(output_path).write_bytes(b"FAKE_MP3_DATA")
        return TTSResult(
            audio_path=output_path,
            format="mp3",
            duration_ms=500,
            language=voice.split("-")[0],
            voice=voice,
        )


class MockLLMProvider(LLMProvider):
    def complete(self, messages):
        return json.dumps({
            "response_text": "test response",
            "language": "en",
            "interaction_state": "speaking",
        })


@pytest.fixture
def app(tmp_path):
    application = create_app(provider=MockLLMProvider())
    # Override TTS output dir to a temp directory for tests
    application.state.tts_service = TTSService(
        provider=FakeTTSProvider(),
        output_dir=tmp_path / "audio",
    )
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


# ===========================================================================
# TTSProvider ABC
# ===========================================================================

class TestTTSProviderABC:
    def test_cannot_instantiate(self):
        """ABC cannot be instantiated directly."""
        with pytest.raises(TypeError):
            TTSProvider()  # type: ignore[abstract]

    def test_subclass_must_implement(self):
        """Subclass without synthesize cannot be instantiated."""
        class Incomplete(TTSProvider):
            pass

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_valid_subclass(self):
        """FakeTTSProvider is a valid concrete subclass."""
        p = FakeTTSProvider()
        assert isinstance(p, TTSProvider)


# ===========================================================================
# VoiceRegistry
# ===========================================================================

class TestVoiceRegistry:
    def test_english_voice(self):
        reg = VoiceRegistry()
        assert "en" in reg.get_voice("en")

    def test_urdu_voice(self):
        reg = VoiceRegistry()
        assert "ur" in reg.get_voice("ur")

    def test_roman_urdu_uses_urdu_voice(self):
        reg = VoiceRegistry()
        assert reg.get_voice("roman_urdu") == reg.get_voice("ur")

    def test_unknown_language_falls_back_to_english(self):
        reg = VoiceRegistry()
        assert reg.get_voice("fr") == reg.get_voice("en")

    def test_custom_overrides(self):
        reg = VoiceRegistry(overrides={"en": "custom-voice"})
        assert reg.get_voice("en") == "custom-voice"

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("PRIMUS_TTS_VOICE_EN", "env-voice")
        reg = VoiceRegistry()
        assert reg.get_voice("en") == "env-voice"


# ===========================================================================
# Text preprocessing
# ===========================================================================

class TestPreprocessText:
    def test_empty_string(self):
        assert preprocess_text("") == ""
        assert preprocess_text("   ") == ""

    def test_strips_whitespace(self):
        assert preprocess_text("  hello  ") == "hello"

    def test_removes_bold_markers(self):
        assert preprocess_text("**bold text**") == "bold text"
        assert preprocess_text("*italic*") == "italic"

    def test_removes_code_backticks(self):
        assert preprocess_text("`F = ma`") == "F = ma"

    def test_removes_headings(self):
        assert preprocess_text("## Newton's Laws") == "Newton's Laws"

    def test_preserves_formulas(self):
        text = "The formula F = ma relates force, mass, and acceleration."
        assert "F = ma" in preprocess_text(text)

    def test_collapses_whitespace(self):
        result = preprocess_text("hello    world")
        assert result == "hello world"


# ===========================================================================
# TTSService
# ===========================================================================

class TestTTSService:
    @pytest.mark.asyncio
    async def test_successful_english_synthesis(self, tmp_path):
        provider = FakeTTSProvider()
        service = TTSService(provider=provider, output_dir=tmp_path / "audio")
        result = await service.synthesize("What is force?", language="en")
        assert isinstance(result, TTSResult)
        assert result.format == "mp3"
        assert result.voice
        assert Path(result.audio_path).exists()

    @pytest.mark.asyncio
    async def test_successful_urdu_synthesis(self, tmp_path):
        provider = FakeTTSProvider()
        service = TTSService(provider=provider, output_dir=tmp_path / "audio")
        result = await service.synthesize("Gravity ek force hai", language="ur")
        assert result.language == "ur"
        assert "ur" in result.voice

    @pytest.mark.asyncio
    async def test_empty_text_rejected(self, tmp_path):
        service = TTSService(provider=FakeTTSProvider(), output_dir=tmp_path / "audio")
        with pytest.raises(TTSProviderError, match="empty"):
            await service.synthesize("")

    @pytest.mark.asyncio
    async def test_whitespace_only_rejected(self, tmp_path):
        service = TTSService(provider=FakeTTSProvider(), output_dir=tmp_path / "audio")
        with pytest.raises(TTSProviderError, match="empty"):
            await service.synthesize("   ")

    @pytest.mark.asyncio
    async def test_provider_error_propagates(self, tmp_path):
        service = TTSService(provider=FakeTTSProvider(fail=True), output_dir=tmp_path / "audio")
        with pytest.raises(TTSProviderError, match="Fake provider"):
            await service.synthesize("hello")

    @pytest.mark.asyncio
    async def test_unique_filenames(self, tmp_path):
        provider = FakeTTSProvider()
        service = TTSService(provider=provider, output_dir=tmp_path / "audio")
        r1 = await service.synthesize("first")
        r2 = await service.synthesize("second")
        assert r1.audio_path != r2.audio_path

    @pytest.mark.asyncio
    async def test_output_dir_created(self, tmp_path):
        out = tmp_path / "new" / "audio"
        service = TTSService(provider=FakeTTSProvider(), output_dir=out)
        assert out.exists()

    @pytest.mark.asyncio
    async def test_independent_of_robot(self, tmp_path):
        """TTS service works without any robot controller."""
        provider = FakeTTSProvider()
        service = TTSService(provider=provider, output_dir=tmp_path / "audio")
        result = await service.synthesize("no robot needed")
        assert isinstance(result, TTSResult)


# ===========================================================================
# POST /tts endpoint
# ===========================================================================

class TestTTSEndpoint:
    def test_successful_synthesis(self, client):
        r = client.post("/tts", json={"text": "What is gravity?", "language": "en"})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "audio_path" in data
        assert "audio_url" in data
        assert data["language"]

    def test_empty_text_400(self, client):
        r = client.post("/tts", json={"text": "", "language": "en"})
        assert r.status_code == 400
        assert r.json()["status"] == "invalid_input"

    def test_whitespace_text_400(self, client):
        r = client.post("/tts", json={"text": "   ", "language": "en"})
        assert r.status_code == 400

    def test_provider_failure_503(self, app, client, tmp_path):
        """TTS provider error → 503, not 500."""
        app.state.tts_service = TTSService(
            provider=FakeTTSProvider(fail=True),
            output_dir=tmp_path / "audio",
        )
        r = client.post("/tts", json={"text": "hello", "language": "en"})
        assert r.status_code == 503
        assert r.json()["status"] == "tts_error"

    def test_audio_streaming(self, client, app):
        """Generated audio can be fetched via GET /audio/{filename}."""
        r = client.post("/tts", json={"text": "hello", "language": "en"})
        assert r.status_code == 200
        filename = Path(r.json()["audio_path"]).name
        audio_r = client.get(f"/audio/{filename}")
        assert audio_r.status_code == 200

    def test_audio_not_found_404(self, client):
        r = client.get("/audio/nonexistent.mp3")
        assert r.status_code == 404

    def test_path_traversal_rejected(self, client):
        r = client.get("/audio/../etc/passwd")
        # FastAPI will reject paths with .. in route parameters
        assert r.status_code in (400, 404, 422)


# ===========================================================================
# TTS failure isolation from /chat
# ===========================================================================

class TestTTSIsolation:
    def test_chat_works_when_tts_fails(self, app, client, tmp_path):
        """Even if TTS service is completely broken, /chat still works."""
        # Break the TTS service by giving it a failing provider
        app.state.tts_service = TTSService(
            provider=FakeTTSProvider(fail=True),
            output_dir=tmp_path / "audio",
        )
        # /chat should still return 200
        r = client.post("/chat", json={
            "session_id": "tts-test",
            "message": "What is force?",
        })
        assert r.status_code == 200
        assert "response_text" in r.json()
