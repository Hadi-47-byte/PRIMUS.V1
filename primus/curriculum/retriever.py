"""Curriculum retriever — abstraction and deterministic implementation.

The retriever is a **sync** in-memory keyword matcher.  No embeddings,
vector databases, or external services are used.

Architecture:

    TutorEngine
      ↓ depends on
    CurriculumRetriever (ABC)
      ↓ implemented by
    DeterministicCurriculumRetriever
      ↓ loads
    physics.json

The ABC allows test code to inject a mock retriever easily.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from primus.curriculum.models import CurriculumConcept

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent / "data"


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class CurriculumRetriever(ABC):
    """Interface for retrieving curriculum concepts relevant to a query."""

    @abstractmethod
    def retrieve(
        self,
        query: str,
        grade: Optional[int] = None,
        topic: Optional[str] = None,
        limit: int = 3,
    ) -> list[CurriculumConcept]:
        """Return up to *limit* concepts matching *query*.

        Parameters
        ----------
        query : str
            The student's message (or a search string).
        grade : int, optional
            Hard-filter to this grade.  ``None`` means all grades.
        topic : str, optional
            Hint — boost concepts in this topic.
        limit : int
            Maximum concepts to return (default 3).
        """


# ---------------------------------------------------------------------------
# Deterministic keyword implementation
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "what", "how", "why", "when", "where", "who", "which", "can",
    "do", "does", "did", "will", "would", "should", "could", "may",
    "might", "of", "in", "on", "at", "to", "for", "with", "by",
    "and", "or", "not", "it", "its", "this", "that", "i", "you",
    "my", "me", "we", "us", "so", "if", "as", "but", "from",
})

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenise(text: str) -> set[str]:
    """Lowercase tokenise, stripping stopwords."""
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}


class DeterministicCurriculumRetriever(CurriculumRetriever):
    """Keyword-matching retriever backed by a JSON concept file.

    Scoring:
    - Each keyword token that appears in a concept's keyword list → +1.
    - Tokens matching the concept *name* → +2 bonus.
    - Tokens matching the concept *topic* → +1 bonus.
    - Topic hint match → +2 bonus.

    Only concepts with score > 0 are returned.
    """

    def __init__(
        self,
        concepts: Optional[list[CurriculumConcept]] = None,
        data_path: Optional[Path] = None,
    ) -> None:
        if concepts is not None:
            self._concepts = list(concepts)
        else:
            path = data_path or (_DATA_DIR / "physics.json")
            self._concepts = self._load(path)

    # -- public API ---------------------------------------------------------

    def retrieve(
        self,
        query: str,
        grade: Optional[int] = None,
        topic: Optional[str] = None,
        limit: int = 3,
    ) -> list[CurriculumConcept]:
        query_tokens = _tokenise(query)
        if not query_tokens:
            return []

        candidates = self._concepts
        if grade is not None:
            candidates = [c for c in candidates if c.grade == grade]

        topic_norm = topic.lower() if topic else None
        scored: list[tuple[float, CurriculumConcept]] = []

        for concept in candidates:
            score = 0.0

            # keyword overlap
            concept_keywords = {kw.lower() for kw in concept.keywords}
            overlap = query_tokens & concept_keywords
            score += len(overlap)

            # concept-name bonus
            name_tokens = _tokenise(concept.concept)
            name_match = query_tokens & name_tokens
            score += len(name_match) * 2

            # topic match bonus
            if topic_norm and concept.topic.lower() == topic_norm:
                score += 2

            # topic token overlap
            topic_tokens = _tokenise(concept.topic)
            topic_match = query_tokens & topic_tokens
            score += len(topic_match)

            # definition token presence (weak signal)
            def_tokens = _tokenise(concept.definition)
            def_match = query_tokens & def_tokens
            score += len(def_match) * 0.5

            if score > 0:
                scored.append((score, concept))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:limit]]

    @property
    def concepts(self) -> list[CurriculumConcept]:
        """All loaded concepts (read-only)."""
        return list(self._concepts)

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _load(path: Path) -> list[CurriculumConcept]:
        if not path.exists():
            logger.warning("Curriculum data not found: %s", path)
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return [CurriculumConcept(**entry) for entry in raw]
        except Exception as exc:
            logger.error("Failed to load curriculum data from %s: %s", path, exc)
            return []
