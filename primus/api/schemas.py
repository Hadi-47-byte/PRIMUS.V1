"""Pydantic schemas for the PRIMUS HTTP API.

These schemas define the request/response contracts for external clients.
The core ``TutorResponse`` model (primus.models) is NOT modified — the API
layer wraps it with an additional ``session_id`` field.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    """Inbound chat message from a student."""

    session_id: str = Field(
        ..., min_length=1, description="Unique session identifier."
    )
    message: str = Field(
        ..., min_length=1, description="The student's message."
    )


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    """Health-check response."""

    status: str = "ok"


class ChatResponse(BaseModel):
    """Outbound tutor response — wraps TutorResponse + session_id."""

    session_id: str
    response_text: str
    language: str = "en"
    interaction_state: str = "idle"
    topic: Optional[str] = None


class ErrorResponse(BaseModel):
    """Standard error envelope."""

    detail: str


class ErrorDetail(BaseModel):
    """Structured error for typed API failures (503, 500)."""

    code: str
    message: str


class APIErrorResponse(BaseModel):
    """Top-level wrapper for structured error responses."""

    error: ErrorDetail
