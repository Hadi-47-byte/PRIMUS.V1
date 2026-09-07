"""RobotController — backend abstraction for robot communication.

Responsibilities:
- Track the currently connected robot WebSocket.
- Validate and send semantic state commands.
- Handle connect / disconnect lifecycle.

The controller is deliberately independent of the TutorEngine.
Coupling ``TutorResponse.interaction_state`` → ``RobotController.send_state``
will happen in a later milestone after the communication layer is proven.
"""

from __future__ import annotations

import asyncio
import logging

from primus.robot.protocol import build_audio_message, build_state_message

logger = logging.getLogger(__name__)


class RobotController:
    """Manages the single active robot WebSocket connection."""

    def __init__(self) -> None:
        self._ws = None          # active WebSocket (or None)
        self._lock = asyncio.Lock()

    # -- connection lifecycle -----------------------------------------------

    async def connect(self, websocket) -> None:
        """Register *websocket* as the active robot connection.

        If a previous connection exists, it is closed first.
        """
        async with self._lock:
            if self._ws is not None:
                logger.warning("Replacing existing robot connection.")
                try:
                    await self._ws.close()
                except Exception:
                    pass
            self._ws = websocket
            logger.info("Robot connected.")

    async def disconnect(self) -> None:
        """Clear the active robot connection."""
        async with self._lock:
            if self._ws is not None:
                self._ws = None
                logger.info("Robot disconnected.")

    # -- queries ------------------------------------------------------------

    def is_connected(self) -> bool:
        """Return ``True`` if a robot WebSocket is currently active."""
        return self._ws is not None

    # -- commands -----------------------------------------------------------

    async def send_state(self, state: str) -> bool:
        """Send a validated semantic state to the robot.

        Returns ``True`` if the message was delivered to the socket,
        ``False`` if no robot is connected or the send failed.

        Raises ``ValueError`` for unknown states (before any I/O).
        """
        message = build_state_message(state)   # validates; may raise
        async with self._lock:
            if self._ws is None:
                return False
            try:
                await self._ws.send_json(message)
                return True
            except Exception as exc:
                logger.error("Failed to send robot state: %s", exc)
                self._ws = None
                return False

    async def send_audio(self, audio_url: str, sample_rate: int, format: str = "pcm16") -> bool:
        """Send a robot-audio message (raw PCM reference) to the robot.

        Returns ``True`` if the message was delivered to the socket,
        ``False`` if no robot is connected or the send failed.
        """
        message = build_audio_message(audio_url, sample_rate, format)
        async with self._lock:
            if self._ws is None:
                return False
            try:
                await self._ws.send_json(message)
                return True
            except Exception as exc:
                logger.error("Failed to send robot audio: %s", exc)
                self._ws = None
                return False
