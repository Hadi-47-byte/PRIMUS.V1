# PRIMUS V1 — Milestone 1: Implementation Report

> **Status:** Complete  
> **Date:** 2026-09-01  
> **Scope:** Text input → Session → Tutor prompt → Single LLM call → Structured TutorResponse → Text output

---

## Table of Contents

1. [Objective](#1-objective)
2. [Final Architecture](#2-final-architecture)
3. [Project Structure](#3-project-structure)
4. [Data Models](#4-data-models)
5. [TutorEngine Flow](#5-tutorengine-flow)
6. [LLM Provider Abstraction](#6-llm-provider-abstraction)
7. [Session Management](#7-session-management)
8. [Prompt Architecture](#8-prompt-architecture)
9. [Error / Fallback Behavior](#9-error--fallback-behavior)
10. [Testing Strategy](#10-testing-strategy)
11. [Test Results](#11-test-results)
12. [REPL Verification](#12-repl-verification)
13. [Configuration / Environment Variables](#13-configuration--environment-variables)
14. [Architectural Decisions](#14-architectural-decisions)
15. [Assumptions](#15-assumptions)
16. [Known Limitations](#16-known-limitations)
17. [M2 Starting Point](#17-m2-starting-point)

---

## 1. Objective

Build the smallest runnable core of PRIMUS: a **text-first, terminal-testable AI physics tutor** that takes a student message, consults an LLM with a carefully engineered system prompt, and returns a **guaranteed-valid structured response**.

This milestone proves the fundamental pipeline:

```
student text
  → session context
    → prompt assembly
      → single LLM call
        → structured TutorResponse
          → text output
```

**No voice, no hardware, no RAG, no HTTP, no WebSocket.**  
Everything added in later milestones layers onto this foundation.

---

## 2. Final Architecture

```
┌─────────────────────────────────────────┐
│              STUDENT (terminal)          │
└──────────────────┬──────────────────────┘
                   │ text
                   ▼
┌──────────────────────────────────────────┐
│          TutorEngine.chat()              │
│                                          │
│  SessionManager ──► SessionState         │
│        │                    │            │
│        │           build_messages()      │
│        │                    │            │
│        ▼                    ▼            │
│   LLMProvider ──► OpenAICompatibleProvider│
│        │                                 │
│        ▼  (raw text)                     │
│   _parse_response()                      │
│        │                                 │
│        ├─ success → TutorResponse        │
│        └─ failure → _safe_fallback()     │
│                                          │
│  session.current_topic ← response.topic  │
│  session.language      ← response.lang   │
│  session.history       ← user + assistant│
└──────────────────────────────────────────┘
```

**Single process. Single LLM call per turn. Deterministic parsing. Guaranteed output contract.**

---

## 3. Project Structure

```
PRIMUS.V1/
├── .gitignore                  # Python, venv, IDE, OneDrive, .env
├── .env.example                # API key + base URL + model template
├── pyproject.toml              # PEP 621 project metadata + pytest config
├── requirements.txt            # Pip-installable dependency list
│
├── primus/                     # Core package
│   ├── __init__.py             # Package marker + __version__
│   ├── models.py               # Pydantic schemas (InteractionState, TutorResponse, SessionState)
│   ├── session.py              # In-memory SessionManager
│   ├── prompts.py              # System prompt + build_messages()
│   ├── provider.py             # LLMProvider ABC + OpenAICompatibleProvider
│   ├── engine.py               # TutorEngine (orchestrator)
│   ├── config.py               # Settings from .env + create_provider() factory
│   └── main.py                 # Terminal REPL entry point
│
└── tests/                      # pytest test suite
    ├── __init__.py
    ├── test_models.py           # 12 tests
    ├── test_session.py          # 12 tests
    ├── test_engine.py           # 15 tests (with MockLLMProvider)
    └── test_provider.py         # 11 tests (with unittest.mock)
```

**Dependency counts:**

| Package          | Version     | Purpose                        |
|------------------|-------------|--------------------------------|
| `openai`         | `>=1.0.0`   | OpenAI-compatible LLM SDK      |
| `pydantic`       | `>=2.0.0`   | Data validation + schema        |
| `python-dotenv`  | `>=1.0.0`   | `.env` file loading            |
| `pytest`         | `>=8.0.0`   | Test runner                    |

---

## 4. Data Models

All models live in `primus/models.py` and use **Pydantic v2**.

### InteractionState

```python
class InteractionState(str, Enum):
    IDLE          = "idle"
    LISTENING     = "listening"
    THINKING      = "thinking"
    SPEAKING      = "speaking"
    HAPPY         = "happy"
    ENCOURAGING   = "encouraging"
    CONFUSED      = "confused"
    EXCITED       = "excited"
```

- `str` mixin enables direct JSON serialisation/deserialisation by value.
- Unknown values are rejected at construction time (`ValueError`).
- The engine maps any unrecognised LLM state to `IDLE` before validation.

### TutorResponse

```python
class TutorResponse(BaseModel):
    response_text: str                        # Required — the tutor's reply
    language: str = "en"                      # "en" | "ur" | "roman_urdu"
    interaction_state: InteractionState = IDLE # Semantic robot expression
    topic: Optional[str] = None               # e.g. "newtons_laws"
    raw_response: Optional[str] = None        # Original LLM text (debug)
```

**Guarantee:** Every `TutorResponse` returned by the engine — including every error and fallback path — is fully valid against this schema.

### SessionState

```python
class SessionState(BaseModel):
    session_id: str
    grade: Optional[int] = None
    current_topic: Optional[str] = None
    language: str = "en"
    explanation_depth: int = 1
    mode: str = "tutor"
    history: list[dict[str, str]] = []
```

- `history` uses `default_factory=list` to prevent shared mutable state across sessions.
- `explanation_depth` and `mode` are reserved for M2+.

---

## 5. TutorEngine Flow

`primus/engine.py` — `TutorEngine.chat(message, session_id) → TutorResponse`

```
1. session = SessionManager.get_or_create(session_id)
2. messages = build_messages(session, message)       # system + history + user
3. raw = provider.complete(messages)                  # single LLM call
   ├─ Exception → _safe_fallback(error message)
4. response = _parse_response(raw, session)
   ├─ JSON parse failure → _safe_fallback(raw text)
   ├─ Unknown interaction_state → mapped to IDLE
5. session.current_topic = response.topic  (only if non-null)
   session.language      = response.language
6. history.append(user message)
   history.append(assistant response_text)
7. return response
```

**Key invariant:** `chat()` **never raises** for LLM or parsing errors. The safe-fallback path always produces a valid `TutorResponse`.

### JSON Parsing Strategy

```python
start = raw.index("{")
end   = raw.rindex("}") + 1
data  = json.loads(raw[start:end])
```

This locates the outermost `{ ... }` in the response, tolerating:
- Prose before/after the JSON
- Markdown code fences (` ```json ... ``` `)
- Extra whitespace

---

## 6. LLM Provider Abstraction

`primus/provider.py`

### LLMProvider (ABC)

```python
class LLMProvider(ABC):
    @abstractmethod
    def complete(self, messages: list[dict[str, str]]) -> str: ...
```

- Single method. Provider-agnostic.
- Accepts OpenAI-format message list.
- Returns raw response text.

### OpenAICompatibleProvider

```python
class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, api_key, base_url=None, model="gpt-4o-mini", temperature=0.7)
```

**`complete()` strategy:**

1. **Attempt 1:** Call with `response_format={"type": "json_object"}` to hint structured output.
2. **Attempt 2:** If the endpoint rejects `response_format`, retry without it.
3. Return whatever text the model produced.

**Supported providers (via `base_url`):**

| Provider              | `base_url`                                                      |
|-----------------------|-----------------------------------------------------------------|
| OpenAI                | *(default)* `https://api.openai.com/v1`                         |
| Qwen (DashScope)      | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`       |
| Ollama (local)        | `http://localhost:11434/v1`                                     |
| vLLM / LiteLLM        | Custom                                                          |

The `openai.OpenAI` client is imported at module level (not deferred), enabling clean `@patch("primus.provider.OpenAI")` in tests.

---

## 7. Session Management

`primus/session.py` — `SessionManager`

| Method                                    | Behavior                                      |
|-------------------------------------------|-----------------------------------------------|
| `get_or_create(session_id=None) → Session` | Returns existing or creates new               |
| `get(session_id) → Optional[Session]`      | Lookup without creation                     |
| `update(session_id, **kwargs) → Session`   | Patches fields; raises `KeyError` if missing  |
| `add_message(session_id, role, content)`    | Appends `{role, content}` to history          |
| `delete(session_id) → bool`                | Removes session                               |

**Storage:** In-memory Python `dict`. No persistence. No database. This is intentional for V1.

**UUID generation:** `uuid4()` when no explicit `session_id` is provided.

---

## 8. Prompt Architecture

`primus/prompts.py`

### System Prompt

A single `SYSTEM_PROMPT` constant defines the PRIMUS tutor persona:

- **Teaching style:** Clear explanations, intuitive examples, encourages reasoning, simplifies on confusion, patient and friendly.
- **Language support:** English, Urdu, Roman Urdu with code-switching tolerance.
- **Response format:** Mandates JSON output with the exact `TutorResponse` schema fields.
- **Interaction state enum:** All 8 allowed values listed explicitly.
- **Field rules:** `response_text` required, `topic` is snake_case or null.

### build_messages()

```python
def build_messages(session: SessionState, user_message: str) -> list[dict]:
    return [
        {"role": "system", "content": get_system_prompt(session)},
        *session.history,
        {"role": "user", "content": user_message},
    ]
```

**Order:** system → history → current user message.  
This is directly compatible with all OpenAI-compatible chat-completions APIs.

### Extensibility

`get_system_prompt(session)` is the designated injection point for M2+ features:
- Curriculum context
- Explanation-depth hints
- Quiz state information
- Language-preference overrides

---

## 9. Error / Fallback Behavior

Every error path produces a **fully valid `TutorResponse`** — the schema contract is never broken.

| Failure Mode                       | Fallback Behavior                                                                                   |
|------------------------------------|-----------------------------------------------------------------------------------------------------|
| LLM provider raises exception     | `response_text` = "I'm having trouble connecting right now. Could you try again?", `state` = idle   |
| Response is not valid JSON         | `response_text` = raw text (code fences stripped), `state` = idle, `raw_response` preserved         |
| JSON is missing required fields    | Same as above                                                                                       |
| Unknown `interaction_state` value  | Mapped to `idle` before Pydantic validation                                                         |
| `topic` is null/missing            | Existing session topic is **preserved** (not cleared)                                               |
| Empty response from LLM            | Generic fallback message, `state` = idle                                                            |

### `_safe_fallback()` Contract

Per the project specification:
- `response_text`: usable/raw response text when possible
- `language`: current session language (default `"en"`)
- `interaction_state`: `idle`
- `topic`: current session topic when available
- `raw_response`: original LLM text preserved for debugging

---

## 10. Testing Strategy

**50 tests total** across 4 test files. No real API calls. All tests use mocks or direct construction.

### test_models.py (12 tests)

| Area              | What's Tested                                                          |
|-------------------|------------------------------------------------------------------------|
| InteractionState  | All 8 enum values exist, string construction, invalid value rejection  |
| TutorResponse     | Full construction, defaults, string coercion, unknown state rejection  |
| SessionState      | Defaults, custom values, history independence (no shared mutable state)|

### test_session.py (12 tests)

| Area           | What's Tested                                                                  |
|----------------|--------------------------------------------------------------------------------|
| Create         | Auto-generated ID, explicit ID, get-or-create idempotency                      |
| Read           | Existing session retrieval, nonexistent returns None                           |
| Update         | Field patching, KeyError on missing session                                    |
| History        | Message append, silent no-op for nonexistent session                           |
| Delete         | Successful deletion, nonexistent returns False                                 |

### test_engine.py (15 tests)

Uses a `MockLLMProvider` (concrete `LLMProvider` subclass returning predetermined strings).

| Area                  | What's Tested                                                              |
|-----------------------|----------------------------------------------------------------------------|
| Happy path            | Valid JSON → correct TutorResponse fields                                  |
| Session updates       | Topic updated, language updated, null topic preserves existing             |
| History               | Correct growth across multiple turns, messages in LLM context              |
| Fallback: malformed   | Non-JSON text → safe fallback with raw text preserved                      |
| Fallback: partial     | Truncated JSON → safe fallback                                             |
| Fallback: unknown state| Invalid interaction_state → mapped to idle                                |
| Fallback: missing fields| Only `response_text` present → valid response with defaults              |
| Fallback: empty       | Empty string → generic fallback message                                    |
| Fallback: provider error| `ConnectionError` from provider → safe fallback, no exception raised      |
| Prompt integrity      | System prompt present, conversation history included in messages            |
| Debug support         | `raw_response` preserved on success and failure                            |
| Code fences           | JSON wrapped in ` ```json ``` ` parsed correctly                           |

### test_provider.py (11 tests)

Uses `unittest.mock.patch` to mock the `openai.OpenAI` client.

| Area          | What's Tested                                                                |
|---------------|------------------------------------------------------------------------------|
| ABC           | Cannot instantiate `LLMProvider`, subclass must implement `complete`         |
| Init guards   | Empty API key rejected, default/custom params, base_url forwarding           |
| complete()    | JSON content returned, `response_format` fallback on unsupported endpoint,   |
|               | empty response → empty string, messages/model passed correctly               |

---

## 11. Test Results

```
============================= test session starts =============================
platform win32 -- Python 3.14.5, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\Pc\OneDrive\Documents\PRIMUS.V1
configfile: pyproject.toml
collected 50 items

tests/test_engine.py   15 passed
tests/test_models.py   12 passed
tests/test_provider.py 11 passed
tests/test_session.py  12 passed

============================= 50 passed in 5.81s ==============================
```

**50/50 passing.** Zero warnings. Zero skipped.

---

## 12. REPL Verification

### Smoke Test (Mock Provider)

A full pipeline integration test was executed with a `FakeProvider` returning predetermined JSON:

```
=======================================================
  PRIMUS V1 — REPL Smoke Test
=======================================================

  You: What is gravity?
  [speaking] PRIMUS: Gravity pulls objects toward Earth. It's why apples fall!
  (topic: gravity, language: en)

  You: Give me an example.
  [speaking] PRIMUS: Gravity pulls objects toward Earth. It's why apples fall!

  Session state:
    current_topic : gravity
    language      : en
    history       : 4 messages

  ALL CHECKS PASSED
=======================================================
```

Verified:
- Structured `TutorResponse` produced with correct fields
- Session `current_topic` updated from `None` → `"gravity"`
- Session `language` updated
- History correctly accumulated (2 user + 2 assistant = 4 messages)
- Follow-up messages include conversation history in LLM context

### REPL Startup (No API Key)

```
  ERROR: OPENAI_API_KEY is not set.
  Copy .env.example → .env and add your key.
```

The REPL exits cleanly with exit code 1 and a clear instruction — no crash, no traceback.

### Running the Live REPL

```bash
# 1. Copy the template
cp .env.example .env

# 2. Add your API key to .env
#    OPENAI_API_KEY=sk-...

# 3. Launch
python -m primus.main
```

REPL commands: `/new` (new session), `/state` (inspect session), `/quit` (exit).

---

## 13. Configuration / Environment Variables

Defined in `.env.example`:

| Variable          | Required | Default                  | Description                                      |
|-------------------|----------|--------------------------|--------------------------------------------------|
| `OPENAI_API_KEY`  | **Yes**  | *(none)*                 | Provider API key                                 |
| `OPENAI_BASE_URL` | No       | `https://api.openai.com/v1` | Override for Qwen, Ollama, etc.              |
| `OPENAI_MODEL`    | No       | `gpt-4o-mini`            | Model identifier                                 |

**Security:** API keys are loaded from `.env` via `python-dotenv`. The `.env` file is in `.gitignore` and is **never committed**.

`primus/config.py` provides:
- `Settings` class — reads all env vars at construction time.
- `create_provider(settings)` — factory that builds an `OpenAICompatibleProvider` from settings.

---

## 14. Architectural Decisions

| #  | Decision                                                                 | Rationale                                                                                      |
|----|--------------------------------------------------------------------------|------------------------------------------------------------------------------------------------|
| 1  | **One LLM call per turn**                                               | Minimises latency, cost, and failure surface. No multi-agent overhead for V1.                  |
| 2  | **LLMProvider ABC** (not Protocol)                                      | `abc.ABC` is simpler for sync interfaces; avoids `@runtime_checkable` complications.           |
| 3  | **Provider-agnostic naming** (`openai_compatible.py` → `provider.py`)   | Explicitly supports benchmarking OpenAI vs Qwen vs others without code changes.                |
| 4  | **`response_format` with graceful fallback**                            | Hints JSON mode to providers that support it; retries plain text for those that don't.          |
| 5  | **JSON parsing via `index`/`rindex`** (not regex)                        | Simpler, faster, and handles code-fenced or prose-wrapped JSON.                                |
| 6  | **`raw_response` field on `TutorResponse`**                              | Preserves original LLM text for debugging — not in original spec but critical for fallback paths.|
| 7  | **Null topic does NOT clear session topic**                              | Prevents topic loss during follow-up questions where the LLM omits the topic field.             |
| 8  | **History as flat `list[dict]`**                                         | Directly compatible with OpenAI chat-completions message format — no conversion needed.         |
| 9  | **In-memory sessions, no persistence**                                   | V1 is single-session, demo-focused. Database adds complexity without demo value.                |
| 10 | **`openai.OpenAI` import at module level**                               | Enables clean `@patch("primus.provider.OpenAI")` in tests. Package is always installed.         |
| 11 | **InteractionState as `str, Enum`**                                      | Enables JSON serialisation by value while maintaining type safety.                              |

---

## 15. Assumptions

| #  | Assumption                                                                                                 | Risk     |
|----|------------------------------------------------------------------------------------------------------------|----------|
| 1  | The LLM will usually return valid JSON matching our schema.                                                 | Medium   |
| 2  | `response_format={"type": "json_object"}` is accepted by most OpenAI-compatible endpoints.                  | Low      |
| 3  | Session history fits in memory for demo-length conversations (~20–50 turns).                                | Low      |
| 4  | The `openai` Python SDK (≥1.0) is the stable interface for all compatible providers.                       | Low      |
| 5  | Python 3.14 is available in the demo and development environments.                                         | Low      |
| 6  | `str.index("{")` / `rindex("}")` is sufficient to locate JSON in well-formed and semi-malformed responses.   | Low      |

---

## 16. Known Limitations

1. **No real API integration test** — all tests use mocks. The first live LLM call may reveal prompt/response issues requiring system prompt tuning.
2. **No conversation-length management** — history grows unbounded within a session. No truncation, summarisation, or token counting.
3. **No streaming** — `complete()` is synchronous and blocking. The REPL waits for the full response before displaying anything.
4. **No async support** — `LLMProvider.complete()` is synchronous. Async can be added later without breaking the interface (add `acomplete()`).
5. **No HTTP/API layer** — the tutor is only accessible via the terminal REPL.
6. **System prompt is static** — does not yet incorporate session context (grade, depth, language preference) into the prompt body.
7. **No curriculum/RAG** — LLM relies entirely on its training knowledge for physics answers.
8. **Single-user assumption** — `SessionManager` is process-local with no concurrency controls.

---

## 17. M2 Starting Point

Milestone 2 adds **session context enrichment** — making the tutor adapt its behavior based on accumulated session state.

### Expected Scope

- **Adaptive explanation depth:** When the student says "I don't understand", increment `explanation_depth` and inject depth-appropriate instructions into the system prompt.
- **Language switching:** Detect and honour explicit language requests ("Explain in Urdu"). Propagate language preference into the prompt.
- **Topic-aware prompts:** Inject the current topic into the system prompt to improve follow-up coherence.
- **Prompt enrichment in `get_system_prompt(session)`:** The function currently returns a static string. M2 adds dynamic sections based on `SessionState`.

### What M2 Does NOT Include

- Curriculum retrieval / RAG (M4+)
- Quiz engine (M4)
- HTTP API / web UI (M8)
- Voice (M9+)
- Robot control (M12+)

### Files Likely Modified in M2

| File               | Change                                                  |
|--------------------|---------------------------------------------------------|
| `primus/prompts.py` | Dynamic prompt sections based on session state          |
| `primus/engine.py`  | Explanation-depth increment logic on confusion signals  |
| `primus/session.py` | Possibly add helper methods for depth/language updates  |
| `tests/test_engine.py` | New tests for adaptive behavior                    |
| `tests/test_prompts.py` | New test file for prompt enrichment               |
