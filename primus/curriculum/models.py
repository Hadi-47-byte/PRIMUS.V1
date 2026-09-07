"""Curriculum data model — a structured Physics concept entry."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CurriculumConcept(BaseModel):
    """A single Physics curriculum concept suitable for tutoring.

    The schema is intentionally simple.  It is designed to be
    serialised to/from JSON and consumed by the retriever and
    context formatter.
    """

    id: str = Field(..., description="Unique snake_case identifier")
    grade: int = Field(..., ge=1, le=12, description="Target grade level")
    topic: str = Field(..., description="Broad topic area (snake_case)")
    concept: str = Field(..., description="Human-readable concept name")
    definition: str = Field(..., description="Clear student-level definition")
    formulas: list[str] = Field(default_factory=list, description="Relevant formulas")
    examples: list[str] = Field(default_factory=list, description="Worked or illustrative examples")
    common_misconceptions: list[str] = Field(
        default_factory=list, description="Frequent student misunderstandings"
    )
    keywords: list[str] = Field(default_factory=list, description="Search/match keywords")
