"""Core data models for PRIMUS V1."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class InteractionState(str, Enum):
    """Constrained set of semantic interaction states.

    The LLM must return one of these values. Unknown values are mapped
    to IDLE by the engine's safe-fallback path.
    """

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    HAPPY = "happy"
    ENCOURAGING = "encouraging"
    CONFUSED = "confused"
    EXCITED = "excited"


class TutorResponse(BaseModel):
    """Structured output contract for every tutor turn.

    Every response from the engine — including error/fallback paths —
    is guaranteed to satisfy this schema.
    """

    response_text: str = Field(
        ..., description="The tutor's reply to the student."
    )
    language: str = Field(
        default="en", description="ISO-639-1 code: 'en', 'ur', or 'roman_urdu'."
    )
    interaction_state: InteractionState = Field(
        default=InteractionState.IDLE,
        description="Semantic expression state for the robot.",
    )
    topic: Optional[str] = Field(
        default=None, description="Current physics topic, if detected."
    )
    raw_response: Optional[str] = Field(
        default=None,
        description="Original LLM text (kept for debugging / fallback).",
    )


class SessionState(BaseModel):
    """Per-session mutable state owned by the backend."""

    session_id: str
    grade: Optional[int] = None
    current_topic: Optional[str] = None
    language: str = "en"
    explanation_depth: int = 1
    mode: str = "tutor"
    history: list[dict[str, str]] = Field(default_factory=list)
