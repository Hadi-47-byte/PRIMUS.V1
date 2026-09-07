"""Edge TTS provider — uses the ``edge-tts`` library (no API key required).

edge-tts provides free access to Microsoft Edge's online TTS service.
It supports many languages including English and Urdu.

This module wraps edge-tts behind the ``TTSProvider`` ABC so it can be
replaced later with OpenAI TTS, Google Cloud TTS, or a local model.
"""

from __future__ import annotations

import logging
import time

from primus.voice.models import TTSProviderError, TTSResult

logger = logging.getLogger(__name__)


class EdgeTTSProvider:
    """Concrete TTS provider backed by ``edge_tts.Communicate``."""

    async def synthesize(
        self,
        text: str,
        voice: str,
        output_path: str,
    ) -> TTSResult:
        try:
            import edge_tts
        except ImportError as exc:
            raise TTSProviderError(
                "edge-tts package is not installed. "
                "Install it with: pip install edge-tts"
            ) from exc

        try:
            communicate = edge_tts.Communicate(text, voice)
            t0 = time.time()
            await communicate.save(output_path)
            duration_ms = int((time.time() - t0) * 1000)

            return TTSResult(
                audio_path=output_path,
                format="mp3",
                duration_ms=duration_ms,
                language=voice.split("-")[0],
                voice=voice,
            )
        except TTSProviderError:
            raise
        except Exception as exc:
            raise TTSProviderError(f"Edge TTS synthesis failed: {exc}") from exc
