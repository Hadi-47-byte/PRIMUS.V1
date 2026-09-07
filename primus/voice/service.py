"""TTSService — orchestration layer for text-to-speech.

Responsibilities:
- Validate input text.
- Preprocess text (strip markdown, normalise whitespace).
- Resolve language → voice via VoiceRegistry.
- Generate safe unique output filenames.
- Delegate synthesis to the TTSProvider.
- Return TTSResult or raise TTSProviderError.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path

from primus.voice.models import TTSProviderError, TTSResult
from primus.voice.provider import TTSProvider, VoiceRegistry

logger = logging.getLogger(__name__)


def preprocess_text(text: str) -> str:
    """Minimal deterministic preprocessing for speech.

    - Strips leading/trailing whitespace.
    - Removes markdown bold/italic markers.
    - Removes inline code backticks.
    - Removes markdown headers.
    - Collapses excessive whitespace.
    - Preserves scientific notation and formulas (F = ma, E = mc²).

    Does NOT rewrite semantics or alter formulas.
    """
    text = text.strip()
    if not text:
        return text
    # Markdown headings
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Bold/italic markers
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)
    # Inline code backticks
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Collapse whitespace (preserve newlines)
    text = re.sub(r"[^\S\n]+", " ", text)
    # Collapse multiple newlines to two
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class TTSService:
    """High-level TTS orchestration."""

    def __init__(
        self,
        provider: TTSProvider,
        voice_registry: VoiceRegistry | None = None,
        output_dir: str | Path = "data/audio",
    ) -> None:
        self._provider = provider
        self._voices = voice_registry or VoiceRegistry()
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    async def synthesize(self, text: str, language: str = "en") -> TTSResult:
        """Validate, preprocess, and synthesise text to audio.

        Raises ``TTSProviderError`` on empty text or synthesis failure.
        """
        if not text or not text.strip():
            raise TTSProviderError("Cannot synthesise empty text.")

        cleaned = preprocess_text(text)
        if not cleaned:
            raise TTSProviderError("Text is empty after preprocessing.")

        voice = self._voices.get_voice(language)
        output_path = str(self._output_dir / f"tts_{uuid.uuid4().hex[:12]}.mp3")

        logger.info("TTS: lang=%s voice=%s → %s", language, voice, output_path)
        result = await self._provider.synthesize(cleaned, voice, output_path)
        return result
