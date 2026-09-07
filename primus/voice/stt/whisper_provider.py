"""OpenAI Whisper STT provider — uses the OpenAI SDK ``audio.transcriptions``.

Supports many languages including English and Urdu.
Uses the ``openai`` package already installed for the LLM provider.

Language handling:
- ``"en"`` → English transcription
- ``"ur"`` → Urdu transcription (Whisper supports Urdu)
- ``None`` / ``"auto"`` → automatic language detection
- Roman Urdu has no dedicated Whisper language code; it is best
  handled via automatic detection or as ``"ur"`` with the caveat
  that Whisper may transcribe Roman Urdu as English text.
"""

from __future__ import annotations

import logging

from primus.voice.stt.models import STTProviderError, STTResult

logger = logging.getLogger(__name__)


class WhisperSTTProvider:
    """Concrete STT provider backed by OpenAI's Whisper API."""

    def __init__(self, model: str = "whisper-1", api_key: str | None = None) -> None:
        self._model = model
        self._api_key = api_key

    async def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
    ) -> STTResult:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise STTProviderError(
                "openai package is not installed."
            ) from exc

        try:
            kwargs: dict = {}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            client = OpenAI(**kwargs)
        except Exception as exc:
            raise STTProviderError(
                "Failed to initialise OpenAI client for STT."
            ) from exc

        try:
            with open(audio_path, "rb") as audio_file:
                params: dict = {
                    "model": self._model,
                    "file": audio_file,
                    "response_format": "verbose_json",
                }
                # Whisper accepts ISO-639-1 language codes; "auto" is
                # not a valid code so we omit the parameter entirely.
                if language and language != "auto":
                    params["language"] = language

                response = client.audio.transcriptions.create(**params)

            text = response.text if hasattr(response, "text") else str(response)
            detected_lang = getattr(response, "language", None)
            duration = getattr(response, "duration", None)

            duration_ms = None
            if duration is not None:
                try:
                    duration_ms = int(float(duration) * 1000)
                except (TypeError, ValueError):
                    pass

            return STTResult(
                text=text.strip(),
                language=detected_lang,
                duration_ms=duration_ms,
                provider="whisper",
            )

        except STTProviderError:
            raise
        except Exception as exc:
            raise STTProviderError(
                f"Whisper transcription failed: {exc}"
            ) from exc
