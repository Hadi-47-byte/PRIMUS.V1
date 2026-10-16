"""Deterministic mapping from TutorResponse interaction states to robot states.

Architecture:

    LLM
     ↓  semantic interaction_state
    ExpressionMapper.map()
     ↓  validated robot state string
    RobotController.send_state()
     ↓  WebSocket
    ESP32

The mapper is a **pure function** — no network I/O, no side-effects.
The orchestrator ties the mapper to the controller and swallows
delivery failures so that the tutor response is never disrupted.
"""

from __future__ import annotations

import logging

from primus.models import InteractionState
from primus.robot.protocol import ALLOWED_STATES
from primus.robot.controller import RobotController

logger = logging.getLogger(__name__)

# Explicit one-to-one mapping.
# InteractionState.CONFUSED has no direct robot equivalent,
# so it maps to "thinking" (closest physical expression).
_STATE_MAP: dict[str, str] = {
    "idle":          "idle",
    "listening":     "listening",
    "thinking":      "thinking",
    "speaking":      "speaking",
    "happy":         "happy",
    "encouraging":   "encouraging",
    "confused":      "thinking",
    "excited":       "excited",
}


def map_interaction_to_robot_state(interaction_state) -> str:
    """Map an ``InteractionState`` (or its string value) to a robot state.

    Returns a validated robot-state string that is guaranteed to be in
    ``ALLOWED_STATES``.

    Raises ``ValueError`` for unrecognised interaction states.
    """
    value = interaction_state.value if hasattr(interaction_state, "value") else str(interaction_state)

    robot_state = _STATE_MAP.get(value)
    if robot_state is None:
        raise ValueError(
            f"Cannot map unknown interaction state '{value}' to a robot state."
        )
    return robot_state


class RobotOrchestrator:
    """Bridges TutorResponse → ExpressionMapper → RobotController.

    Robot delivery failures are silently logged and never propagated,
    ensuring the tutor response always succeeds regardless of hardware
    connectivity.
    """

    def __init__(self, controller: RobotController) -> None:
        self._controller = controller

    async def dispatch(self, tutor_response) -> bool:
        """Send the robot state implied by *tutor_response*.

        Returns ``True`` if the state was delivered, ``False`` otherwise
        (robot offline, send error, unmappable state).

        Never raises — all failures are caught and logged.
        """
        try:
            interaction_value = tutor_response.interaction_state.value
            robot_state = map_interaction_to_robot_state(interaction_value)
            logger.debug(
                "Mapped interaction state '%s' → robot state '%s'.",
                interaction_value,
                robot_state,
            )
            sent = await self._controller.send_state(robot_state)
            if sent:
                logger.info(
                    "Robot state dispatched: '%s' → '%s'",
                    interaction_value, robot_state,
                )
            else:
                logger.info("Robot offline; skipping physical expression.")
            return sent
        except Exception as exc:
            logger.warning("Robot dispatch failed: %s", exc)
            return False

    async def dispatch_audio(self, audio_url: str, sample_rate: int) -> bool:
        """Send a TTS audio reference to the robot for speaker playback.

        Fire-and-forget, same non-fatal pattern as ``dispatch()`` —
        failures are logged and never propagated.
        """
        try:
            sent = await self._controller.send_audio(audio_url, sample_rate)
            if not sent:
                logger.info("Robot offline; skipping audio delivery.")
            return sent
        except Exception as exc:
            logger.warning("Robot audio dispatch failed: %s", exc)
            return False
