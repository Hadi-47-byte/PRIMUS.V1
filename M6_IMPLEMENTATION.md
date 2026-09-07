# PRIMUS V1 — Milestone 6: Physics Curriculum Layer

> **Status:** Complete  
> **Date:** 2026-09-02  
> **Scope:** Deterministic Physics curriculum retrieval for grade 6–10 tutoring  
> **Depends on:** M1 (engine), M2 (API), M3 (error handling), M4 (robot), M5 (expression mapping)

---

## Purpose

Introduce a modular, deterministic Physics curriculum retrieval layer that provides relevant context to the TutorEngine **before** the LLM call — without using embeddings, vector databases, or RAG frameworks.

```
Student message
    ↓
TutorEngine
    ↓
CurriculumRetriever (keyword matching)
    ↓
Relevant Physics context (0–3 concepts)
    ↓
Prompt builder (system prompt + context)
    ↓
Single LLM call
    ↓
TutorResponse
    ↓
ExpressionMapper → RobotController
```

---

## Architecture

### New Files

```
primus/curriculum/
├── __init__.py          # Package marker
├── models.py            # CurriculumConcept (Pydantic)
├── retriever.py         # CurriculumRetriever ABC + DeterministicCurriculumRetriever
├── formatter.py         # format_curriculum_context()
└── data/
    └── physics.json     # 15 representative Physics concepts
```

### Modified Files

| File | Change |
|------|--------|
| `primus/engine.py` | Added `curriculum_retriever` parameter + `_retrieve_curriculum()` |
| `primus/prompts.py` | Added curriculum context to system prompt + `build_messages()` |

---

## Curriculum Schema

### `CurriculumConcept` (Pydantic)

```python
class CurriculumConcept(BaseModel):
    id: str              # "newtons_second_law"
    grade: int           # 8
    topic: str           # "force_and_motion"
    concept: str         # "Newton's Second Law"
    definition: str      # "The acceleration of an object is..."
    formulas: list[str]  # ["F = ma"]
    examples: list[str]
    common_misconceptions: list[str]
    keywords: list[str]  # ["newton second law", "f=ma", ...]
```

---

## Initial Knowledge Base (15 concepts)

| # | ID | Grade | Topic |
|---|----|-------|-------|
| 1 | `motion_basics` | 6 | motion |
| 2 | `speed` | 6 | motion |
| 3 | `velocity` | 7 | motion |
| 4 | `acceleration` | 8 | motion |
| 5 | `force_basics` | 7 | force_and_motion |
| 6 | `newtons_first_law` | 8 | force_and_motion |
| 7 | `newtons_second_law` | 8 | force_and_motion |
| 8 | `newtons_third_law` | 8 | force_and_motion |
| 9 | `gravity` | 7 | force_and_motion |
| 10 | `momentum` | 9 | force_and_motion |
| 11 | `work` | 9 | energy |
| 12 | `energy` | 8 | energy |
| 13 | `power` | 9 | energy |
| 14 | `sound_waves` | 9 | waves |
| 15 | `frequency_wavelength` | 10 | waves |

Covers Mechanics, Energy, and Waves across grades 6–10.

---

## Retriever Design

### Abstract Base

```python
class CurriculumRetriever(ABC):
    def retrieve(query, grade=None, topic=None, limit=3) -> list[CurriculumConcept]
```

The ABC allows:
- `DeterministicCurriculumRetriever` for production
- `MockRetriever` for testing
- Future `EmbeddingRetriever` or `VectorRetriever` for benchmarking

### `DeterministicCurriculumRetriever`

**Matching strategy** (keyword-based scoring):

| Signal | Weight |
|--------|--------|
| Query token matches concept keyword | +1.0 |
| Query token matches concept name | +2.0 |
| Query token matches topic name | +1.0 |
| Topic hint matches concept topic | +2.0 |
| Query token matches definition | +0.5 |

- Queries are tokenised and stopwords removed.
- Matching is case-insensitive.
- Grade filter is **hard** (excludes non-matching grades).
- Only concepts with score > 0 are returned.
- Results sorted by score descending, capped at `limit` (default 3).

### Example Retrievals

| Query | Grade | Results |
|-------|-------|---------|
| "what is newton's second law" | None | `newtons_second_law`, `newtons_first_law`, `newtons_third_law` |
| "velocity" | 7 | `velocity` |
| "force mass acceleration" | 8 | `newtons_second_law` |
| "energy" | None | `energy`, `power`, `work` |
| "banana pancake" | None | `[]` (empty) |

---

## Context Formatting

Retrieved concepts are formatted into a structured text block:

```
[PHYSICS CURRICULUM CONTEXT]
Grade: 8
Topic: Force And Motion
Concept: Newton's Second Law

Definition:
The acceleration of an object is directly proportional to...

Formula:
  F = ma

Example:
  - Pushing a 2 kg box with 10 N...

Common misconceptions:
  - F = ma means force equals mass plus acceleration...
[/PHYSICS CURRICULUM CONTEXT]
```

When multiple concepts are returned, they are separated by `---`. When no concepts are retrieved, the formatter returns an empty string and no context is injected.

---

## TutorEngine Integration

The retriever is **optional and injectable**:

```python
engine = TutorEngine(
    provider=provider,
    curriculum_retriever=DeterministicCurriculumRetriever(),  # optional
)
```

In `engine.chat()`:

```python
# 1. Retrieve curriculum context (non-fatal)
curriculum_context = self._retrieve_curriculum(
    message, session.grade, session.current_topic
)

# 2. Build message list (with or without curriculum)
messages = build_messages(session, message, curriculum_context)
```

The `_retrieve_curriculum()` method catches ALL exceptions — if the retriever fails, tutoring continues without context:

```python
def _retrieve_curriculum(self, message, grade, topic):
    if self._curriculum is None:
        return None
    try:
        concepts = self._curriculum.retrieve(query=message, grade=grade, topic=topic)
        context = format_curriculum_context(concepts)
        return context or None
    except Exception as exc:
        logger.warning("Curriculum retrieval failed: %s", exc)
        return None
```

---

## Prompt Integration

When curriculum context is present, a separate system message is injected into the message list:

```
[system]  You are PRIMUS, an AI Physics tutor...
          ## Curriculum context
          When curriculum context is provided below, use it to...

[system]  [PHYSICS CURRICULUM CONTEXT]
          Grade: 8
          Concept: Newton's Second Law
          ...
          [/PHYSICS CURRICULUM CONTEXT]

[history] (previous turns)

[user]    What is Newton's second law?
```

The system prompt instructs the model to:
- Ground explanations in provided Physics facts
- Reference formulas when appropriate
- Address listed misconceptions
- Adapt to the student's grade level
- Fall back to general knowledge when no context is provided

---

## Grade Handling

`SessionState.grade` is `Optional[int]` with default `None`.

- **Grade known** → hard-filter concepts to matching grade
- **Grade unknown** (`None`) → retrieve across all grades

The API does not require a grade. The system works normally without one.

---

## Failure / Fallback Behaviour

| Scenario | Behaviour |
|----------|-----------|
| No retriever configured | Engine skips retrieval, calls LLM normally |
| Retriever returns empty | No context injected, LLM uses general knowledge |
| Retriever raises exception | Logged as warning, engine continues without context |
| Data file missing | Retriever loads empty list, returns empty results |
| Data file malformed | Retriever logs error, returns empty results |

**Critical guarantee:** Curriculum retrieval is NEVER a critical failure point. AI tutoring continues regardless.

---

## Testing

### Test Results

```
144 passed in 7.12s
├── test_api.py         19 passed  (M2 + M3)
├── test_curriculum.py  26 passed  ← M6
│   ├── TestCurriculumData           3
│   ├── TestDeterministicRetriever  12
│   ├── TestFormatter                4
│   └── TestEngineCurriculumIntegration 7
├── test_engine.py      15 passed  (M1 + M3)
├── test_expressions.py 22 passed  (M5)
├── test_models.py      12 passed  (M1)
├── test_provider.py    11 passed  (M1)
├── test_robot.py       27 passed  (M4)
└── test_session.py     12 passed  (M1)
```

### Test Coverage

| Test Class | Count | What's Verified |
|-----------|-------|----------------|
| `TestCurriculumData` | 3 | JSON loads, concept count ≥10, schema validation |
| `TestDeterministicRetriever` | 12 | Keyword match, case-insensitive, grade filter, topic boost, limit, empty/irrelevant queries, custom concepts, missing file |
| `TestFormatter` | 4 | Empty input, single concept, multiple concepts, determinism |
| `TestEngineCurriculumIntegration` | 7 | No retriever (compat), mock retriever, empty retrieval, broken retriever, context in messages, no context when empty, ABC enforcement |

---

## Why Vector/RAG Was Deferred

This milestone intentionally uses deterministic keyword matching instead of embeddings or vector search. Reasons:

1. **Baseline first** — We need a measurable baseline before introducing complexity.
2. **Benchmarking plan** — Later we will compare:
   - Prompt-only (no curriculum)
   - Deterministic keyword grounding (this milestone)
   - Actual RAG (embeddings + vector search)
3. **Hackathon constraints** — No GPU, no external vector DB, no training time.
4. **Determinism** — Keyword matching is fully reproducible and inspectable.
5. **Curriculum is small** — 15 concepts don't need ANN search; keyword overlap works well.

The `CurriculumRetriever` ABC makes it trivial to swap in an embedding-based retriever later without touching the engine.

---

## Known Limitations

1. **Small dataset** — 15 concepts cover representative areas but not the full Grade 6–10 curriculum.
2. **No fuzzy matching** — Exact keyword overlap only; synonyms and paraphrases are not matched.
3. **No multi-word phrase matching** — "Newton's second law" is matched via individual keyword tokens, not as a phrase.
4. **No relevance threshold tuning** — Any score > 0 is returned; threshold tuning would require benchmark data.
5. **No cross-topic retrieval** — A question about energy might not retrieve related force concepts unless keywords overlap.
6. **Static data** — `physics.json` is loaded once at startup; changes require a restart.

---

## Assumptions

| # | Assumption | Rationale |
|---|-----------|-----------|
| 1 | Curriculum data fits in memory | 15 concepts is trivially small |
| 2 | Keyword matching is sufficient for the initial dataset | Concepts have well-defined keyword lists |
| 3 | Grade filtering should be hard (not soft) | Students should not see advanced-grade material |
| 4 | 3 concepts max per retrieval | Enough context without overwhelming the prompt |
| 5 | Stopwords improve precision | Common English stopwords add noise to matching |

---

## Files Added/Modified

### New Files (6)

| File | Purpose |
|------|---------|
| `primus/curriculum/__init__.py` | Package marker |
| `primus/curriculum/models.py` | `CurriculumConcept` Pydantic model |
| `primus/curriculum/retriever.py` | `CurriculumRetriever` ABC + `DeterministicCurriculumRetriever` |
| `primus/curriculum/formatter.py` | `format_curriculum_context()` |
| `primus/curriculum/data/physics.json` | 15 Physics concepts |
| `tests/test_curriculum.py` | 26 automated tests |

### Modified Files (2)

| File | Change |
|------|--------|
| `primus/engine.py` | +`curriculum_retriever` parameter, +`_retrieve_curriculum()` method |
| `primus/prompts.py` | +`curriculum_context` parameter to `build_messages()` and `get_system_prompt()` |

---

## Next Milestones

- **M7:** Benchmark prompt-only vs deterministic grounding vs RAG.
- **M8:** Expand physics.json to cover full Grade 6–10 curriculum.
- **M9:** Add curriculum-aware quiz generation.
- **M10:** STT/TTS integration with curriculum-grounded responses.
