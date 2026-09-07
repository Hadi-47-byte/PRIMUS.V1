"""TTS result model and typed exception."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TTSProviderError(Exception):
    """Raised when a TTS provider cannot complete synthesis."""


class TTSResult(BaseModel):
    """Typed result returned after successful speech synthesis."""

    audio_path: str = Field(..., description="Path to the generated audio file")
    format: str = Field(default="mp3", description="Audio file format")
    duration_ms: int | None = Field(
        default=None, description="Approximate duration in milliseconds"
    )
    language: str = Field(..., description="Language code used for synthesis")
    voice: str = Field(..., description="Voice identifier used")
