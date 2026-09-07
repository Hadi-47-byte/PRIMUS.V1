"""STT result model and typed exception."""

from __future__ import annotations

from pydantic import BaseModel, Field


class STTProviderError(Exception):
    """Raised when an STT provider cannot complete transcription."""


class STTResult(BaseModel):
    """Typed result returned after successful speech-to-text."""

    text: str = Field(..., description="Transcribed text")
    language: str | None = Field(
        default=None, description="Detected or hinted language code"
    )
    duration_ms: int | None = Field(
        default=None, description="Audio duration in milliseconds (if reported)"
    )
    confidence: float | None = Field(
        default=None, description="Confidence score 0–1 (if reported by provider)"
    )
    provider: str | None = Field(
        default=None, description="Provider identifier that produced this result"
    )
