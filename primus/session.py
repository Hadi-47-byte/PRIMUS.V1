"""In-memory session management for PRIMUS V1."""

from __future__ import annotations

from typing import Optional
from uuid import uuid4

from primus.models import SessionState


class SessionManager:
    """Owns all active tutor sessions.

    V1 stores sessions in memory only — no persistence.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    # -- public API ----------------------------------------------------------

    def get_or_create(self, session_id: Optional[str] = None) -> SessionState:
        """Return an existing session or create a new one."""
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        return self._create(session_id)

    def get(self, session_id: str) -> Optional[SessionState]:
        """Return a session without creating one."""
        return self._sessions.get(session_id)

    def update(self, session_id: str, **kwargs) -> SessionState:
        """Patch session fields.  Raises ``KeyError`` if the session is missing."""
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"Session '{session_id}' not found")
        for key, value in kwargs.items():
            if hasattr(session, key):
                setattr(session, key, value)
        return session

    def add_message(self, session_id: str, role: str, content: str) -> None:
        """Append a ``{role, content}`` entry to the session history."""
        session = self._sessions.get(session_id)
        if session is not None:
            session.history.append({"role": role, "content": content})

    def delete(self, session_id: str) -> bool:
        """Remove a session.  Returns ``True`` if it existed."""
        return self._sessions.pop(session_id, None) is not None

    # -- internals -----------------------------------------------------------

    def _create(self, session_id: Optional[str] = None) -> SessionState:
        sid = session_id or str(uuid4())
        state = SessionState(session_id=sid)
        self._sessions[sid] = state
        return state
