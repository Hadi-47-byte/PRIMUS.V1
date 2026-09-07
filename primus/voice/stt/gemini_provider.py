"""Google Gemini STT provider — uses the ``google-genai`` SDK.

Transcribes audio using Gemini's multimodal ``generate_content`` endpoint
with a transcription prompt.  Supports many languages including English
and Urdu.

Language handling:
- ``"en"`` → prompt requests English transcription
- ``"ur"`` → prompt requests Urdu transcription
- ``None`` / ``"auto"`` → automatic language detection (Gemini infers)

Requires:
- ``google-genai`` Python package (``pip install google-genai``)
- ``GEMINI_API_KEY`` environment variable
"""

from __future__ import annotations

import logging
import mimetypes
from typing import Optional

from primus.voice.stt.models import STTProviderError, STTResult

logger = logging.getLogger(__name__)

# Prompt templates ----------------------------------------------------------
_BASE_PROMPT = (
    "Transcribe the following audio accurately. "
    "Return ONLY the transcribed text, nothing else."
)
_LANG_PROMPT = (
    "Transcribe the following audio accurately in {language}. "
    "Return ONLY the transcribed text, nothing else."
)


class GeminiSTTProvider:
    """Concrete STT provider backed by Google Gemini's multimodal API."""

    def __init__(
        self,
        model: str = "gemini-3.5-transcribe",
        api_key: str | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key

    async def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
    ) -> STTResult:
        """Transcribe *audio_path* using the Gemini API.

        Parameters
        ----------
        audio_path:
            Path to a local audio file.
        language:
            ISO-639-1 code (``"en"``, ``"ur"``) or ``None`` / ``"auto"``
            for automatic detection.

        Returns
        -------
        STTResult
            Typed transcription result.

        Raises
        ------
        STTProviderError
            On any provider-level failure.
        """
        # -- deferred import so tests can mock without installing ----------
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise STTProviderError(
                "google-genai package is not installed. "
                "Install with: pip install google-genai"
            ) from exc

        # -- build client --------------------------------------------------
        try:
            kwargs: dict = {}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            client = genai.Client(**kwargs)
        except Exception as exc:
            raise STTProviderError(
                "Failed to initialise Gemini client for STT."
            ) from exc

        # -- read audio bytes ---------------------------------------------
        try:
            with open(audio_path, "rb") as f:
                audio_data = f.read()
        except OSError as exc:
            raise STTProviderError(
                f"Failed to read audio file: {exc}"
            ) from exc

        # -- determine MIME type ------------------------------------------
        mime_type = mimetypes.guess_type(audio_path)[0] or "audio/wav"

        # -- build prompt --------------------------------------------------
        if language and language != "auto":
            prompt = _LANG_PROMPT.format(language=language)
        else:
            prompt = _BASE_PROMPT

        # -- call Gemini API -----------------------------------------------
        try:
            response = client.models.generate_content(
                model=self._model,
                contents=[
                    prompt,
                    types.Part.from_bytes(
                        data=audio_data,
                        mime_type=mime_type,
                    ),
                ],
            )

            text = response.text if response.text else ""

            # gemini-3.5-transcribe returns transcription in a structured
            # audio_transcription Part, not in response.text.  Extract it.
            if not text and hasattr(response, "candidates") and response.candidates:
                for part in response.candidates[0].content.parts:
                    transcript = getattr(part, "audio_transcription", None)
                    if transcript and hasattr(transcript, "text") and transcript.text:
                        text = transcript.text
                        break
            return STTResult(
                text=text.strip(),
                language=language if (language and language != "auto") else None,
                duration_ms=None,  # Gemini does not report duration inline
                provider="gemini",
            )

        except STTProviderError:
            raise
        except Exception as exc:
            raise STTProviderError(
                f"Gemini transcription failed: {exc}"
            ) from exc
