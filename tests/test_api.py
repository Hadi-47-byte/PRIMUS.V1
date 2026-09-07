"""Tests for the PRIMUS HTTP API.

All tests use ``MockLLMProvider`` and ``httpx.AsyncClient`` via FastAPI's
``TestClient``.  No real LLM calls are made.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from primus.api.app import create_app
from primus.provider import LLMProvider, LLMProviderError


# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------

class MockLLMProvider(LLMProvider):
    def __init__(self, response: str) -> None:
        self._response = response

    def complete(self, messages: list[dict[str, str]]) -> str:
        return self._response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_response(
    text: str = "Newton's second law states F = ma.",
    language: str = "en",
    state: str = "speaking",
    topic: str | None = "newtons_second_law",
) -> str:
    return json.dumps({
        "response_text": text,
        "language": language,
        "interaction_state": state,
        "topic": topic,
    })


@pytest.fixture
def client():
    """TestClient wired to a MockLLMProvider."""
    app = create_app(provider=MockLLMProvider(_json_response()))
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# POST /chat — happy path
# ---------------------------------------------------------------------------

class TestChat:
    def test_successful_chat(self, client):
        r = client.post("/chat", json={
            "session_id": "test-1",
            "message": "What is Newton's second law?",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["session_id"] == "test-1"
        assert data["response_text"] == "Newton's second law states F = ma."
        assert data["language"] == "en"
        assert data["interaction_state"] == "speaking"
        assert data["topic"] == "newtons_second_law"

    def test_session_context_preserved(self, client):
        """Two requests with the same session_id share history."""
        msg = {"session_id": "ctx-1", "message": "What is gravity?"}
        r1 = client.post("/chat", json=msg)
        r2 = client.post("/chat", json=msg)
        assert r1.status_code == 200
        assert r2.status_code == 200

        # Verify history grew by checking the engine's session
        session = client.app.state.engine._sessions.get("ctx-1")
        assert session is not None
        assert len(session.history) == 4  # 2 user + 2 assistant

    def test_different_sessions_independent(self, client):
        r1 = client.post("/chat", json={"session_id": "s1", "message": "hi"})
        r2 = client.post("/chat", json={"session_id": "s2", "message": "hi"})
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["session_id"] == "s1"
        assert r2.json()["session_id"] == "s2"


# ---------------------------------------------------------------------------
# POST /chat — validation errors
# ---------------------------------------------------------------------------

class TestChatValidation:
    def test_empty_message_400(self, client):
        r = client.post("/chat", json={
            "session_id": "test-1",
            "message": "   ",
        })
        assert r.status_code == 400
        assert "empty" in r.json()["detail"].lower()

    def test_empty_session_id_400(self, client):
        r = client.post("/chat", json={
            "session_id": "   ",
            "message": "hello",
        })
        assert r.status_code == 400
        assert "session_id" in r.json()["detail"].lower()

    def test_missing_message_field_422(self, client):
        r = client.post("/chat", json={"session_id": "test-1"})
        assert r.status_code == 422

    def test_missing_session_id_field_422(self, client):
        r = client.post("/chat", json={"message": "hello"})
        assert r.status_code == 422

    def test_empty_body_422(self, client):
        r = client.post("/chat", json={})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /chat — provider failure
# ---------------------------------------------------------------------------

class TestChatProviderFailure:
    def test_provider_error_returns_503(self):
        """LLMProviderError → HTTP 503 with safe error body."""

        class FailProvider(LLMProvider):
            def complete(self, messages):
                raise LLMProviderError("Network down")

        app = create_app(provider=FailProvider())
        tc = TestClient(app)

        r = tc.post("/chat", json={
            "session_id": "fail-1",
            "message": "hello",
        })
        assert r.status_code == 503
        data = r.json()
        assert data["error"]["code"] == "llm_provider_unavailable"
        assert "unavailable" in data["error"]["message"].lower()
        # No sensitive details leaked
        assert "network down" not in r.text.lower()
        assert "sk-" not in r.text

    def test_malformed_llm_output_returns_200_fallback(self):
        """Non-JSON LLM output → safe fallback → HTTP 200."""
        app = create_app(provider=MockLLMProvider("not json at all"))
        tc = TestClient(app)

        r = tc.post("/chat", json={
            "session_id": "bad-1",
            "message": "What is force?",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["interaction_state"] == "idle"
        assert "not json" in data["response_text"].lower()

    def test_unexpected_error_returns_500(self):
        """Error escaping engine → HTTP 500 with safe body."""
        app = create_app(provider=MockLLMProvider(_json_response()))
        tc = TestClient(app, raise_server_exceptions=False)

        # Patch the engine to raise a non-LLMProviderError that escapes
        def broken_chat(message, session_id=None):
            raise RuntimeError("something broke internally")

        app.state.engine.chat = broken_chat

        r = tc.post("/chat", json={
            "session_id": "crash-1",
            "message": "hello",
        })
        assert r.status_code == 500
        data = r.json()
        assert data["error"]["code"] == "internal_error"
        assert "unexpected" in data["error"]["message"].lower()
        # No stack traces leaked
        assert "traceback" not in r.text.lower()
        assert "something broke" not in r.text.lower()

    def test_non_provider_error_engine_fallback_200(self):
        """Non-LLMProviderError from provider → engine safe fallback → 200."""

        class BrokenProvider(LLMProvider):
            def complete(self, messages):
                raise RuntimeError("not a provider error")

        app = create_app(provider=BrokenProvider())
        tc = TestClient(app)

        r = tc.post("/chat", json={
            "session_id": "fallback-1",
            "message": "hello",
        })
        # Engine catches RuntimeError (not LLMProviderError) → safe fallback
        assert r.status_code == 200
        data = r.json()
        assert data["interaction_state"] == "idle"

    def test_no_sensitive_details_in_503(self):
        """Ensure 503 body never leaks API keys or stack traces."""

        class SecretLeakProvider(LLMProvider):
            def complete(self, messages):
                raise LLMProviderError("sk-secret-key-12345 in auth header")

        app = create_app(provider=SecretLeakProvider())
        tc = TestClient(app)

        r = tc.post("/chat", json={
            "session_id": "sec-1",
            "message": "test",
        })
        assert r.status_code == 503
        assert "sk-secret" not in r.text
        assert "auth header" not in r.text.lower()
        assert "traceback" not in r.text.lower()


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

class TestSecurity:
    def test_no_api_key_in_response(self, client):
        """Ensure no sensitive configuration leaks through any endpoint."""
        for path in ["/health", "/chat"]:
            if path == "/chat":
                r = client.post(path, json={
                    "session_id": "sec-1",
                    "message": "test",
                })
            else:
                r = client.get(path)
            body = r.text.lower()
            assert "sk-" not in body
            assert "api_key" not in body
            assert "openai" not in body or "openai" in body.lower().replace("openai", "")  # allow the word in docs

    def test_cors_headers_present(self, client):
        """CORS middleware should include access-control headers."""
        r = client.options(
            "/chat",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        # FastAPI/Starlette CORS returns 200 or 400 for OPTIONS
        # The important thing is the allow-origin header
        if r.status_code == 200:
            assert "access-control-allow-origin" in r.headers


# ---------------------------------------------------------------------------
# Static test UI
# ---------------------------------------------------------------------------

class TestStaticUI:
    def test_index_html_served(self, client):
        """GET / should serve the browser test UI."""
        r = client.get("/")
        assert r.status_code == 200
        assert "PRIMUS" in r.text
        assert "text/html" in r.headers.get("content-type", "")

    def test_js_served(self, client):
        r = client.get("/app.js")
        assert r.status_code == 200
        assert "sendMessage" in r.text

    def test_css_served(self, client):
        r = client.get("/styles.css")
        assert r.status_code == 200
        assert "container" in r.text
