"""Tests for PRIMUS V1 — Milestone 6: Physics Curriculum Layer.

Covers:
- Curriculum data loading and schema validation
- DeterministicCurriculumRetriever keyword matching
- Grade and topic filtering
- Context formatter
- TutorEngine integration (with mock + real retriever)
- Failure / fallback behaviour

All tests use mocks — no real LLM calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from primus.curriculum.formatter import format_curriculum_context
from primus.curriculum.models import CurriculumConcept
from primus.curriculum.retriever import (
    CurriculumRetriever,
    DeterministicCurriculumRetriever,
)
from primus.engine import TutorEngine
from primus.models import TutorResponse
from primus.provider import LLMProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DATA_PATH = Path(__file__).resolve().parent.parent / "primus" / "curriculum" / "data" / "physics.json"


class MockLLMProvider(LLMProvider):
    def complete(self, messages):
        return json.dumps({
            "response_text": "test response",
            "language": "en",
            "interaction_state": "speaking",
            "topic": "test_topic",
        })


def _make_concept(**overrides) -> CurriculumConcept:
    defaults = {
        "id": "test_concept",
        "grade": 8,
        "topic": "force_and_motion",
        "concept": "Test Concept",
        "definition": "A test definition.",
        "formulas": ["F = ma"],
        "examples": ["Example one."],
        "common_misconceptions": ["Misconception one."],
        "keywords": ["test", "concept", "force"],
    }
    defaults.update(overrides)
    return CurriculumConcept(**defaults)


class MockRetriever(CurriculumRetriever):
    """Injectable mock for engine integration tests."""

    def __init__(self, concepts=None):
        self._concepts = concepts or []
        self.call_count = 0

    def retrieve(self, query, grade=None, topic=None, limit=3):
        self.call_count += 1
        return self._concepts[:limit]


class BrokenRetriever(CurriculumRetriever):
    """Always raises."""

    def retrieve(self, query, grade=None, topic=None, limit=3):
        raise RuntimeError("data store exploded")


# ===========================================================================
# Data loading
# ===========================================================================

class TestCurriculumData:
    def test_physics_json_loads(self):
        """The bundled physics.json loads without error."""
        r = DeterministicCurriculumRetriever()
        assert len(r.concepts) > 0

    def test_physics_json_count(self):
        """We expect at least 10 representative concepts."""
        r = DeterministicCurriculumRetriever()
        assert len(r.concepts) >= 10

    def test_concept_schema_valid(self):
        """Every concept in physics.json passes Pydantic validation."""
        r = DeterministicCurriculumRetriever()
        for c in r.concepts:
            assert isinstance(c, CurriculumConcept)
            assert c.id
            assert 1 <= c.grade <= 12
            assert c.topic
            assert c.concept
            assert c.definition
            assert isinstance(c.keywords, list)


# ===========================================================================
# Retriever matching
# ===========================================================================

class TestDeterministicRetriever:
    @pytest.fixture
    def retriever(self):
        return DeterministicCurriculumRetriever()

    def test_exact_keyword_match(self, retriever):
        """'newton second law' retrieves the right concept."""
        results = retriever.retrieve("newton second law")
        ids = [c.id for c in results]
        assert "newtons_second_law" in ids

    def test_case_insensitive(self, retriever):
        """Matching is case-insensitive."""
        r1 = retriever.retrieve("VELOCITY")
        r2 = retriever.retrieve("velocity")
        assert [c.id for c in r1] == [c.id for c in r2]

    def test_grade_filter(self, retriever):
        """Grade filter restricts results to the specified grade."""
        results = retriever.retrieve("force", grade=7)
        for c in results:
            assert c.grade == 7

    def test_grade_filter_excludes_other(self, retriever):
        """Results for grade 6 should not include grade 10 concepts."""
        results = retriever.retrieve("frequency wavelength", grade=6)
        for c in results:
            assert c.grade == 6

    def test_topic_filter_boost(self, retriever):
        """Topic hint boosts concepts in that topic."""
        results = retriever.retrieve("force", topic="force_and_motion")
        assert len(results) > 0
        # Top result should be from force_and_motion
        assert results[0].topic == "force_and_motion"

    def test_limit_respected(self, retriever):
        """Limit caps the number of results."""
        results = retriever.retrieve("energy", limit=1)
        assert len(results) <= 1

    def test_irrelevant_query_returns_empty(self, retriever):
        """Completely unrelated queries return no results."""
        results = retriever.retrieve("banana pancake recipe")
        assert results == []

    def test_empty_query_returns_empty(self, retriever):
        results = retriever.retrieve("")
        assert results == []

    def test_stopwords_only_returns_empty(self, retriever):
        """Queries with only stopwords return nothing."""
        results = retriever.retrieve("what is the")
        assert results == []

    def test_concept_with_formulas(self, retriever):
        """F = ma concept has formulas."""
        results = retriever.retrieve("F = ma force mass acceleration")
        assert len(results) > 0
        found = next((c for c in results if c.id == "newtons_second_law"), None)
        assert found is not None
        assert any("F = ma" in f for f in found.formulas)

    def test_custom_concepts_injectable(self):
        """Retriever can be constructed with custom concepts."""
        c = _make_concept(id="custom", keywords=["custom"])
        r = DeterministicCurriculumRetriever(concepts=[c])
        results = r.retrieve("custom")
        assert len(results) == 1
        assert results[0].id == "custom"

    def test_missing_data_file_returns_empty(self):
        """Missing JSON file → empty concept list, no crash."""
        r = DeterministicCurriculumRetriever(data_path=Path("/nonexistent.json"))
        assert r.concepts == []


# ===========================================================================
# Formatter
# ===========================================================================

class TestFormatter:
    def test_empty_concepts(self):
        assert format_curriculum_context([]) == ""

    def test_single_concept(self):
        c = _make_concept()
        text = format_curriculum_context([c])
        assert "[PHYSICS CURRICULUM CONTEXT]" in text
        assert "[/PHYSICS CURRICULUM CONTEXT]" in text
        assert "Test Concept" in text
        assert "F = ma" in text
        assert "Example one" in text
        assert "Misconception one" in text

    def test_multiple_concepts(self):
        c1 = _make_concept(id="a", concept="Alpha")
        c2 = _make_concept(id="b", concept="Beta")
        text = format_curriculum_context([c1, c2])
        assert "Alpha" in text
        assert "Beta" in text
        assert "---" in text  # separator

    def test_deterministic_output(self):
        """Same input → same output every time."""
        c = _make_concept()
        t1 = format_curriculum_context([c])
        t2 = format_curriculum_context([c])
        assert t1 == t2


# ===========================================================================
# Engine integration
# ===========================================================================

class TestEngineCurriculumIntegration:
    def test_engine_without_retriever(self):
        """Engine works fine with no curriculum retriever (backward compat)."""
        engine = TutorEngine(provider=MockLLMProvider())
        resp = engine.chat("What is force?", session_id="s1")
        assert isinstance(resp, TutorResponse)

    def test_engine_with_mock_retriever(self):
        """Engine calls the retriever and still returns a valid response."""
        concept = _make_concept(keywords=["force"])
        mock_r = MockRetriever(concepts=[concept])
        engine = TutorEngine(
            provider=MockLLMProvider(),
            curriculum_retriever=mock_r,
        )
        resp = engine.chat("What is force?", session_id="s1")
        assert isinstance(resp, TutorResponse)
        assert mock_r.call_count == 1

    def test_engine_with_empty_retrieval(self):
        """Retriever returns nothing → engine continues normally."""
        mock_r = MockRetriever(concepts=[])
        engine = TutorEngine(
            provider=MockLLMProvider(),
            curriculum_retriever=mock_r,
        )
        resp = engine.chat("hello", session_id="s1")
        assert isinstance(resp, TutorResponse)

    def test_broken_retriever_does_not_crash(self):
        """Retriever raises → engine catches and continues."""
        engine = TutorEngine(
            provider=MockLLMProvider(),
            curriculum_retriever=BrokenRetriever(),
        )
        resp = engine.chat("What is energy?", session_id="s1")
        assert isinstance(resp, TutorResponse)
        assert resp.response_text  # has actual content

    def test_curriculum_context_in_messages(self):
        """When retriever returns concepts, context appears in the prompt."""
        concept = _make_concept(
            id="force_test",
            concept="Force Test",
            keywords=["force"],
        )

        class InspectingProvider(LLMProvider):
            last_messages = None

            def complete(self, messages):
                InspectingProvider.last_messages = messages
                return json.dumps({
                    "response_text": "Force is a push or pull.",
                    "language": "en",
                    "interaction_state": "speaking",
                })

        mock_r = MockRetriever(concepts=[concept])
        engine = TutorEngine(
            provider=InspectingProvider(),
            curriculum_retriever=mock_r,
        )
        engine.chat("What is force?", session_id="s1")

        msgs = InspectingProvider.last_messages
        # Should have at least: system, curriculum context, user
        roles = [m["role"] for m in msgs]
        assert roles.count("system") >= 2  # prompt + curriculum context
        # Curriculum context should appear
        context_msg = next(m for m in msgs if "PHYSICS CURRICULUM CONTEXT" in m["content"])
        assert "Force Test" in context_msg["content"]

    def test_no_curriculum_context_when_empty(self):
        """When retriever returns nothing, no curriculum context in prompt."""
        class InspectingProvider(LLMProvider):
            last_messages = None

            def complete(self, messages):
                InspectingProvider.last_messages = messages
                return json.dumps({
                    "response_text": "Hello!",
                    "language": "en",
                    "interaction_state": "idle",
                })

        mock_r = MockRetriever(concepts=[])
        engine = TutorEngine(
            provider=InspectingProvider(),
            curriculum_retriever=mock_r,
        )
        engine.chat("hello", session_id="s1")

        msgs = InspectingProvider.last_messages
        has_curriculum = any("PHYSICS CURRICULUM CONTEXT" in m["content"] for m in msgs)
        assert not has_curriculum

    def test_abc_cannot_be_instantiated(self):
        """CurriculumRetriever ABC cannot be instantiated directly."""
        with pytest.raises(TypeError):
            CurriculumRetriever()  # type: ignore[abstract]
