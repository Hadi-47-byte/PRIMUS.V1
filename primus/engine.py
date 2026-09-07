"""Core tutor engine — the brain of PRIMUS V1.

Responsibilities
----------------
1. Build the prompt from session state + conversation history.
2. Make exactly ONE LLM call per conversational turn.
3. Parse the structured response into a ``TutorResponse``.
4. Guarantee a valid ``TutorResponse`` on every code path (including errors).
5. Update session state (topic, language, history).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from primus.models import InteractionState, SessionState, TutorResponse
from primus.prompts import build_messages
from primus.provider import LLMProvider, LLMProviderError
from primus.session import SessionManager
from primus.curriculum.formatter import format_curriculum_context
from primus.curriculum.retriever import CurriculumRetriever

logger = logging.getLogger(__name__)


class TutorEngine:
    """Coordinates one conversational turn with the student."""

    def __init__(
        self,
        provider: LLMProvider,
        session_manager: Optional[SessionManager] = None,
        curriculum_retriever: Optional[CurriculumRetriever] = None,
    ) -> None:
        self._provider = provider
        self._sessions = session_manager or SessionManager()
        self._curriculum = curriculum_retriever

    # -- public API ----------------------------------------------------------

    def chat(
        self,
        message: str,
        session_id: Optional[str] = None,
    ) -> TutorResponse:
        """Process one student message and return a structured tutor response.

        This method **never raises** for LLM/parsing errors — it always
        returns a valid ``TutorResponse`` via the safe-fallback path.
        """
        session = self._sessions.get_or_create(session_id)
        session_id = session.session_id

        # 1. Retrieve curriculum context (non-fatal)
        curriculum_context = self._retrieve_curriculum(
            message, session.grade, session.current_topic
        )

        # 2. Build message list
        messages = build_messages(session, message, curriculum_context)

        # 3. Call LLM
        try:
            raw_response = self._provider.complete(messages)
        except LLMProviderError:
            # Provider-level failure — propagate to caller (API returns 503).
            logger.error("LLM provider unavailable for session %s", session_id)
            raise
        except Exception as exc:
            # Non-provider unexpected error — safe fallback (HTTP 200).
            logger.error("Unexpected error during LLM call: %s", exc)
            return self._safe_fallback(
                raw_text="I'm having trouble connecting right now. Could you try again?",
                session=session,
            )

        # 4. Parse structured response
        response = self._parse_response(raw_response, session)

        # 5. Update session
        if response.topic:
            session.current_topic = response.topic
        session.language = response.language

        # 6. Persist conversation history
        self._sessions.add_message(session_id, "user", message)
        self._sessions.add_message(session_id, "assistant", response.response_text)

        return response

    # -- internals -----------------------------------------------------------

    def _retrieve_curriculum(
        self,
        message: str,
        grade: Optional[int],
        topic: Optional[str],
    ) -> Optional[str]:
        """Retrieve and format curriculum context.

        Returns the formatted context string, or ``None`` if no context
        is available or the retriever fails.  Never raises.
        """
        if self._curriculum is None:
            return None
        try:
            concepts = self._curriculum.retrieve(
                query=message, grade=grade, topic=topic,
            )
            context = format_curriculum_context(concepts)
            return context or None
        except Exception as exc:
            logger.warning("Curriculum retrieval failed: %s", exc)
            return None

    def _parse_response(
        self,
        raw: str,
        session: SessionState,
    ) -> TutorResponse:
        """Attempt to parse *raw* LLM text into a ``TutorResponse``.

        Falls back to ``_safe_fallback`` on any failure.
        """
        try:
            # Locate the outermost JSON object
            start = raw.index("{")
            end = raw.rindex("}") + 1
            data = json.loads(raw[start:end])

            # Normalise interaction_state
            state_str = str(data.get("interaction_state", "idle")).lower()
            try:
                data["interaction_state"] = InteractionState(state_str)
            except ValueError:
                logger.warning("Unknown interaction_state '%s' → idle", state_str)
                data["interaction_state"] = InteractionState.IDLE

            # Store the original text for debugging
            data["raw_response"] = raw

            return TutorResponse(**data)

        except Exception as exc:
            logger.warning("Failed to parse LLM response: %s", exc)
            return self._safe_fallback(
                raw_text=raw,
                session=session,
            )

    @staticmethod
    def _safe_fallback(
        raw_text: Optional[str] = None,
        session: Optional[SessionState] = None,
    ) -> TutorResponse:
        """Produce a guaranteed-valid ``TutorResponse`` from incomplete data.

        Per the project spec:
        - ``response_text``: usable / raw text when available, else a generic message.
        - ``language``: current session language (default ``"en"``).
        - ``interaction_state``: ``idle``.
        - ``topic``: current session topic when available.
        """
        if raw_text:
            text = raw_text.strip()
            # Strip code-fence markers if present
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            text = text.strip()
        else:
            text = "I'm having a bit of trouble right now. Could you try asking again?"

        return TutorResponse(
            response_text=text,
            language=getattr(session, "language", "en"),
            interaction_state=InteractionState.IDLE,
            topic=getattr(session, "current_topic", None),
            raw_response=raw_text,
        )
