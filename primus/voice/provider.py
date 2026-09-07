"""TTS provider abstraction — ABC and voice registry.

Architecture:

    TTSService
      ↓ depends on
    TTSProvider (ABC)
      ↓ implemented by
    EdgeTTSProvider

The VoiceRegistry keeps language-to-voice mapping out of the provider
implementation so that voice names can be changed via configuration
without touching synthesis code.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Optional

from primus.voice.models import TTSResult


# ---------------------------------------------------------------------------
# Voice registry
# ---------------------------------------------------------------------------

_DEFAULT_VOICES: dict[str, str] = {
    "en": "en-US-ChristopherNeural",
    "ur": "ur-PK-AsadNeural",
    "roman_urdu": "ur-PK-AsadNeural",
}


class VoiceRegistry:
    """Maps language codes to provider voice identifiers.

    Defaults can be overridden by environment variables of the form
    ``PRIMUS_TTS_VOICE_<LANG>`` (e.g. ``PRIMUS_TTS_VOICE_EN``).
    """

    def __init__(self, overrides: Optional[dict[str, str]] = None) -> None:
        self._voices: dict[str, str] = dict(_DEFAULT_VOICES)
        if overrides:
            self._voices.update(overrides)
        # Also pick up env-var overrides
        for lang in ("en", "ur", "roman_urdu"):
            env_key = f"PRIMUS_TTS_VOICE_{lang.upper()}"
            val = os.environ.get(env_key)
            if val:
                self._voices[lang] = val

    def get_voice(self, language: str) -> str:
        """Return the voice identifier for *language*.

        Falls back to the English voice for unknown language codes so
        that synthesis never fails purely because of a language mismatch.
        """
        return self._voices.get(language, self._voices["en"])


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class TTSProvider(ABC):
    """Interface for text-to-speech synthesis providers."""

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice: str,
        output_path: str,
    ) -> TTSResult:
        """Synthesise *text* using *voice* and write audio to *output_path*.

        Returns a ``TTSResult`` on success.
        Raises ``TTSProviderError`` on failure.
        """
