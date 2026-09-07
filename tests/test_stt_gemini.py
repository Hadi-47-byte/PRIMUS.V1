"""Tests for GeminiSTTProvider — Google Gemini-based speech-to-text.

All tests use ``unittest.mock.patch`` to replace the ``google.genai``
client components so no real API calls are made.

Covers:
- GeminiSTTProvider initialisation
- Successful transcription with mocked client
- Language parameter handling (auto, en, ur, None)
- Error propagation (API failure, client-init failure)
- MIME-type mapping
- Integration with STTService
- Model parameter forwarding
- No-API-key construction (SDK falls back to env)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from primus.voice.stt.models import STTProviderError, STTResult
from primus.voice.stt.service import STTService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_audio_file(
    tmp_path: Path,
    name: str = "test.wav",
    content: bytes = b"FAKE_AUDIO_DATA",
) -> str:
    path = tmp_path / name
    path.write_bytes(content)
    return str(path)


def _make_mock_response(text: str) -> MagicMock:
    """Build a mock that looks like a Gemini generate_content response."""
    resp = MagicMock()
    resp.text = text
    return resp


# ===========================================================================
# GeminiSTTProvider — direct tests
# ===========================================================================

class TestGeminiSTTProvider:
    """Unit tests for GeminiSTTProvider with google.genai mocked."""

    @patch("google.genai.types.Part.from_bytes")
    @patch("google.genai.Client")
    @pytest.mark.asyncio
    async def test_successful_transcription(self, MockClient, mock_from_bytes, tmp_path):
        """Basic transcription returns STTResult with correct fields."""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(
            "What is force?"
        )
        MockClient.return_value = mock_client
        mock_from_bytes.return_value = MagicMock()

        from primus.voice.stt.gemini_provider import GeminiSTTProvider

        provider = GeminiSTTProvider(api_key="fake-key")
        path = _create_audio_file(tmp_path)
        result = await provider.transcribe(path, language="en")

        assert isinstance(result, STTResult)
        assert result.text == "What is force?"
        assert result.language == "en"
        assert result.provider == "gemini"

    @patch("google.genai.types.Part.from_bytes")
    @patch("google.genai.Client")
    @pytest.mark.asyncio
    async def test_auto_language_detection(self, MockClient, mock_from_bytes, tmp_path):
        """language='auto' → provider omits language from prompt, returns None."""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(
            "Hello world"
        )
        MockClient.return_value = mock_client
        mock_from_bytes.return_value = MagicMock()

        from primus.voice.stt.gemini_provider import GeminiSTTProvider

        provider = GeminiSTTProvider(api_key="fake-key")
        path = _create_audio_file(tmp_path)
        result = await provider.transcribe(path, language="auto")

        assert result.language is None
        assert result.text == "Hello world"

    @patch("google.genai.types.Part.from_bytes")
    @patch("google.genai.Client")
    @pytest.mark.asyncio
    async def test_none_language_detection(self, MockClient, mock_from_bytes, tmp_path):
        """language=None → provider auto-detects, returns None."""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(
            "Auto detected text"
        )
        MockClient.return_value = mock_client
        mock_from_bytes.return_value = MagicMock()

        from primus.voice.stt.gemini_provider import GeminiSTTProvider

        provider = GeminiSTTProvider(api_key="fake-key")
        path = _create_audio_file(tmp_path)
        result = await provider.transcribe(path, language=None)

        assert result.language is None
        assert result.text == "Auto detected text"

    @patch("google.genai.types.Part.from_bytes")
    @patch("google.genai.Client")
    @pytest.mark.asyncio
    async def test_urdu_transcription(self, MockClient, mock_from_bytes, tmp_path):
        """Urdu language hint is passed through to result."""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(
            "نیوٹن کا قانون"
        )
        MockClient.return_value = mock_client
        mock_from_bytes.return_value = MagicMock()

        from primus.voice.stt.gemini_provider import GeminiSTTProvider

        provider = GeminiSTTProvider(api_key="fake-key")
        path = _create_audio_file(tmp_path)
        result = await provider.transcribe(path, language="ur")

        assert result.text == "نیوٹن کا قانون"
        assert result.language == "ur"

    @patch("google.genai.types.Part.from_bytes")
    @patch("google.genai.Client")
    @pytest.mark.asyncio
    async def test_api_failure_raises_stt_error(self, MockClient, mock_from_bytes, tmp_path):
        """Gemini API failure → STTProviderError."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("403 Forbidden")
        MockClient.return_value = mock_client
        mock_from_bytes.return_value = MagicMock()

        from primus.voice.stt.gemini_provider import GeminiSTTProvider

        provider = GeminiSTTProvider(api_key="fake-key")
        path = _create_audio_file(tmp_path)
        with pytest.raises(STTProviderError, match="Gemini transcription failed"):
            await provider.transcribe(path)

    @patch("google.genai.Client")
    @pytest.mark.asyncio
    async def test_client_init_failure_raises_stt_error(self, MockClient, tmp_path):
        """Client initialisation failure → STTProviderError."""
        MockClient.side_effect = ValueError("bad config")

        from primus.voice.stt.gemini_provider import GeminiSTTProvider

        provider = GeminiSTTProvider(api_key="fake-key")
        path = _create_audio_file(tmp_path)
        with pytest.raises(STTProviderError, match="Failed to initialise"):
            await provider.transcribe(path)

    @patch("google.genai.types.Part.from_bytes")
    @patch("google.genai.Client")
    @pytest.mark.asyncio
    async def test_empty_response_text(self, MockClient, mock_from_bytes, tmp_path):
        """Empty response text from Gemini → STTResult with empty text."""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response("")
        MockClient.return_value = mock_client
        mock_from_bytes.return_value = MagicMock()

        from primus.voice.stt.gemini_provider import GeminiSTTProvider

        provider = GeminiSTTProvider(api_key="fake-key")
        path = _create_audio_file(tmp_path)
        result = await provider.transcribe(path)

        assert result.text == ""
        # STTService would reject this, but the provider itself allows it

    @patch("google.genai.types.Part.from_bytes")
    @patch("google.genai.Client")
    @pytest.mark.asyncio
    async def test_mime_type_wav(self, MockClient, mock_from_bytes, tmp_path):
        """WAV file → mime_type starts with 'audio/'."""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response("ok")
        MockClient.return_value = mock_client
        mock_from_bytes.return_value = MagicMock()

        from primus.voice.stt.gemini_provider import GeminiSTTProvider

        provider = GeminiSTTProvider(api_key="fake-key")
        path = _create_audio_file(tmp_path, "test.wav")
        await provider.transcribe(path)

        call_kwargs = mock_from_bytes.call_args
        assert call_kwargs[1]["mime_type"].startswith("audio/")

    @patch("google.genai.types.Part.from_bytes")
    @patch("google.genai.Client")
    @pytest.mark.asyncio
    async def test_mime_type_mp3(self, MockClient, mock_from_bytes, tmp_path):
        """MP3 file → mime_type='audio/mpeg'."""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response("ok")
        MockClient.return_value = mock_client
        mock_from_bytes.return_value = MagicMock()

        from primus.voice.stt.gemini_provider import GeminiSTTProvider

        provider = GeminiSTTProvider(api_key="fake-key")
        path = _create_audio_file(tmp_path, "test.mp3")
        await provider.transcribe(path)

        call_kwargs = mock_from_bytes.call_args
        assert call_kwargs[1]["mime_type"] == "audio/mpeg"

    @patch("google.genai.types.Part.from_bytes")
    @patch("google.genai.Client")
    @pytest.mark.asyncio
    async def test_no_api_key_allowed(self, MockClient, mock_from_bytes, tmp_path):
        """Provider can be created without an explicit api_key."""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response("ok")
        MockClient.return_value = mock_client
        mock_from_bytes.return_value = MagicMock()

        from primus.voice.stt.gemini_provider import GeminiSTTProvider

        # No api_key — should still instantiate (SDK reads env)
        provider = GeminiSTTProvider()
        path = _create_audio_file(tmp_path)
        result = await provider.transcribe(path)

        assert result.text == "ok"
        # Client was created without explicit api_key kwarg
        MockClient.assert_called_once_with()

    @patch("google.genai.types.Part.from_bytes")
    @patch("google.genai.Client")
    @pytest.mark.asyncio
    async def test_model_parameter_forwarded(self, MockClient, mock_from_bytes, tmp_path):
        """Custom model name is passed to generate_content."""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response("ok")
        MockClient.return_value = mock_client
        mock_from_bytes.return_value = MagicMock()

        from primus.voice.stt.gemini_provider import GeminiSTTProvider

        provider = GeminiSTTProvider(
            model="gemini-3.5-transcribe",
            api_key="fake-key",
        )
        path = _create_audio_file(tmp_path)
        await provider.transcribe(path)

        call_kwargs = mock_client.models.generate_content.call_args
        assert call_kwargs[1]["model"] == "gemini-3.5-transcribe"

    @patch("google.genai.types.Part.from_bytes")
    @patch("google.genai.Client")
    @pytest.mark.asyncio
    async def test_api_key_passed_to_client(self, MockClient, mock_from_bytes, tmp_path):
        """When api_key is provided, it is forwarded to genai.Client."""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response("ok")
        MockClient.return_value = mock_client
        mock_from_bytes.return_value = MagicMock()

        from primus.voice.stt.gemini_provider import GeminiSTTProvider

        provider = GeminiSTTProvider(api_key="my-secret-key")
        path = _create_audio_file(tmp_path)
        await provider.transcribe(path)

        MockClient.assert_called_once_with(api_key="my-secret-key")


# ===========================================================================
# GeminiSTTProvider + STTService integration
# ===========================================================================

class TestGeminiSTTWithService:
    """Test GeminiSTTProvider through the STTService orchestration layer."""

    @patch("google.genai.types.Part.from_bytes")
    @patch("google.genai.Client")
    @pytest.mark.asyncio
    async def test_service_rejects_empty_transcript(self, MockClient, mock_from_bytes, tmp_path):
        """STTService rejects empty text from the provider."""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response("")
        MockClient.return_value = mock_client
        mock_from_bytes.return_value = MagicMock()

        from primus.voice.stt.gemini_provider import GeminiSTTProvider

        provider = GeminiSTTProvider(api_key="fake-key")
        service = STTService(provider=provider)
        path = _create_audio_file(tmp_path)
        with pytest.raises(STTProviderError, match="empty"):
            await service.transcribe(path)

    @patch("google.genai.types.Part.from_bytes")
    @patch("google.genai.Client")
    @pytest.mark.asyncio
    async def test_service_passes_language_auto(self, MockClient, mock_from_bytes, tmp_path):
        """STTService normalises 'auto' to None for the provider."""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(
            "What is energy?"
        )
        MockClient.return_value = mock_client
        mock_from_bytes.return_value = MagicMock()

        from primus.voice.stt.gemini_provider import GeminiSTTProvider

        provider = GeminiSTTProvider(api_key="fake-key")
        service = STTService(provider=provider)
        path = _create_audio_file(tmp_path)
        result = await service.transcribe(path, language="auto")

        assert result.text == "What is energy?"
        assert result.language is None  # auto → None

    @patch("google.genai.types.Part.from_bytes")
    @patch("google.genai.Client")
    @pytest.mark.asyncio
    async def test_service_validates_audio_before_provider(self, MockClient, mock_from_bytes, tmp_path):
        """STTService validates file existence before calling provider."""
        from primus.voice.stt.gemini_provider import GeminiSTTProvider

        provider = GeminiSTTProvider(api_key="fake-key")
        service = STTService(provider=provider)
        with pytest.raises(STTProviderError, match="not found"):
            await service.transcribe(str(tmp_path / "missing.wav"))

        # Client should never have been instantiated
        MockClient.assert_not_called()
