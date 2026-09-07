"""STT provider abstraction — ABC for speech-to-text providers.

Architecture:

    STTService
      ↓ depends on
    STTProvider (ABC)
      ↓ implemented by
    GeminiSTTProvider  (production — Google Gemini)
    WhisperSTTProvider (legacy — OpenAI Whisper)

The ABC allows the concrete provider to be replaced or mocked without
changing the service layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from primus.voice.stt.models import STTResult


class STTProvider(ABC):
    """Interface for speech-to-text transcription providers."""

    @abstractmethod
    async def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
    ) -> STTResult:
        """Transcribe the audio file at *audio_path*.

        Parameters
        ----------
        audio_path : str
            Path to a local audio file.
        language : str, optional
            Language hint (e.g. ``"en"``, ``"ur"``).  ``None`` or
            ``"auto"`` lets the provider detect the language.

        Returns
        -------
        STTResult
            Typed transcription result.

        Raises
        ------
        STTProviderError
            On any provider-level failure.
        """
