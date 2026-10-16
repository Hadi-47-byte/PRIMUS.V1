"""WebSocket endpoint handler for robot communication.

The ESP32 connects as a WebSocket **client** to ``/ws/robot``.
The backend accepts the connection, registers it with the
``RobotController``, and listens for status/ack messages until
the ESP32 disconnects.
"""

from __future__ import annotations

import json
import logging

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from primus.robot.controller import RobotController
from primus.robot.protocol import parse_message

logger = logging.getLogger(__name__)


async def robot_ws_handler(websocket: WebSocket, controller: RobotController) -> None:
    """Accept an ESP32 WebSocket connection and manage its lifecycle."""
    await websocket.accept()
    await controller.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Invalid JSON from robot: %s", data[:200])
                continue

            parsed = parse_message(msg)
            if parsed:
                logger.debug("Robot ack/status: %s", parsed)
            else:
                logger.warning("Unrecognised robot message: %s", str(msg)[:200])
    except WebSocketDisconnect:
        logger.info("Robot WebSocket disconnected.")
    except Exception as exc:
        logger.error("Robot WebSocket error: %s", exc)
    finally:
        await controller.disconnect(websocket) 