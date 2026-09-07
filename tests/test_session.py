"""Tests for primus.session — SessionManager."""

from __future__ import annotations

import pytest

from primus.session import SessionManager


class TestSessionManager:
    def test_create_session_no_id(self):
        mgr = SessionManager()
        s = mgr.get_or_create()
        assert s.session_id
        assert s.language == "en"
        assert s.history == []

    def test_create_session_with_id(self):
        mgr = SessionManager()
        s = mgr.get_or_create(session_id="test-1")
        assert s.session_id == "test-1"

    def test_get_existing_session(self):
        mgr = SessionManager()
        s1 = mgr.get_or_create(session_id="s1")
        s2 = mgr.get_or_create(session_id="s1")
        assert s1 is s2

    def test_different_sessions_independent(self):
        mgr = SessionManager()
        s1 = mgr.get_or_create(session_id="s1")
        s2 = mgr.get_or_create(session_id="s2")
        assert s1 is not s2
        assert s1.session_id != s2.session_id

    def test_get_nonexistent_returns_none(self):
        mgr = SessionManager()
        assert mgr.get("missing") is None

    def test_get_existing(self):
        mgr = SessionManager()
        created = mgr.get_or_create(session_id="s1")
        fetched = mgr.get("s1")
        assert fetched is created

    def test_update_session(self):
        mgr = SessionManager()
        mgr.get_or_create(session_id="s1")
        updated = mgr.update("s1", language="ur", grade=9)
        assert updated.language == "ur"
        assert updated.grade == 9

    def test_update_nonexistent_raises(self):
        mgr = SessionManager()
        with pytest.raises(KeyError):
            mgr.update("missing", language="ur")

    def test_add_message(self):
        mgr = SessionManager()
        mgr.get_or_create(session_id="s1")
        mgr.add_message("s1", "user", "hello")
        mgr.add_message("s1", "assistant", "hi there")
        s = mgr.get("s1")
        assert len(s.history) == 2
        assert s.history[0] == {"role": "user", "content": "hello"}
        assert s.history[1] == {"role": "assistant", "content": "hi there"}

    def test_add_message_nonexistent_silent(self):
        mgr = SessionManager()
        mgr.add_message("missing", "user", "hello")  # should not raise

    def test_delete_session(self):
        mgr = SessionManager()
        mgr.get_or_create(session_id="s1")
        assert mgr.delete("s1") is True
        assert mgr.get("s1") is None

    def test_delete_nonexistent(self):
        mgr = SessionManager()
        assert mgr.delete("missing") is False
