"""STTService — orchestration layer for speech-to-text.

Responsibilities:
- Validate audio input (exists, supported format, non-empty, size limit).
- Normalise the language parameter.
- Delegate transcription to the STTProvider.
- Return STTResult or raise STTProviderError.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from primus.voice.stt.models import STTProviderError, STTResult
from primus.voice.stt.provider import STTProvider

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({
    ".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm",
})

MAX_FILE_SIZE: int = 25 * 1024 * 1024  # 25 MB


def validate_audio(audio_path: str) -> None:
    """Validate that *audio_path* is a usable audio file.

    Raises ``STTProviderError`` with a clear message on failure.
    """
    path = Path(audio_path)

    if not path.exists():
        raise STTProviderError(f"Audio file not found: {audio_path}")
    if not path.is_file():
        raise STTProviderError(f"Path is not a file: {audio_path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise STTProviderError(
            f"Unsupported audio format: '{path.suffix}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    size = path.stat().st_size
    if size == 0:
        raise STTProviderError("Audio file is empty.")
    if size > MAX_FILE_SIZE:
        raise STTProviderError(
            f"Audio file too large: {size / 1024 / 1024:.1f} MB "
            f"(max {MAX_FILE_SIZE / 1024 / 1024:.0f} MB)."
        )


class STTService:
    """High-level STT orchestration."""

    def __init__(self, provider: STTProvider) -> None:
        self._provider = provider

    async def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
    ) -> STTResult:
        """Validate input and transcribe audio to text.

        Raises ``STTProviderError`` on validation or transcription failure.
        """
        validate_audio(audio_path)

        # Normalise language hint
        lang = language
        if lang is not None:
            lang = lang.strip().lower() or None
            if lang == "auto":
                lang = None  # let provider auto-detect

        logger.info("STT: file=%s language=%s", audio_path, lang)
        result = await self._provider.transcribe(audio_path, language=lang)

        # Post-validate: ensure we got non-empty text
        if not result.text or not result.text.strip():
            raise STTProviderError("Transcription returned empty text.")

        return result
