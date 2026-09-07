"""Robot communication protocol — message schema and validation.

The protocol is intentionally minimal:

    {
        "version": 1,
        "type": "robot_state",
        "state": "<allowed_state>"
    }

    {
        "version": 1,
        "type": "robot_audio",
        "audio_url": "<relative-url>",
        "sample_rate": 16000,
        "format": "pcm16"
    }

The backend NEVER sends raw GPIO numbers, servo angles, LED values,
or arbitrary hardware commands.  Only semantic state names and audio
references are transmitted.  The ESP32 firmware maps these to
deterministic physical behaviour.
"""

from __future__ import annotations

PROTOCOL_VERSION: int = 1

ALLOWED_STATES: frozenset[str] = frozenset({
    "idle",
    "listening",
    "thinking",
    "speaking",
    "happy",
    "encouraging",
    "excited",
})


def validate_state(state: str) -> bool:
    """Return ``True`` if *state* is a recognised robot state."""
    return state in ALLOWED_STATES


def build_state_message(state: str) -> dict:
    """Build a validated outbound robot-state message.

    Raises ``ValueError`` for unknown states — the caller should never
    attempt to send an unvalidated state to the ESP32.
    """
    if not validate_state(state):
        raise ValueError(
            f"Unknown robot state: '{state}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_STATES))}"
        )
    return {
        "version": PROTOCOL_VERSION,
        "type": "robot_state",
        "state": state,
    }


def build_audio_message(audio_url: str, sample_rate: int, format: str = "pcm16") -> dict:
    """Build an outbound robot-audio message (raw PCM, not MP3)."""
    return {
        "version": PROTOCOL_VERSION,
        "type": "robot_audio",
        "audio_url": audio_url,
        "sample_rate": sample_rate,
        "format": format,
    }


def parse_message(data: dict) -> dict | None:
    """Parse an inbound message from the ESP32.

    Returns the validated dict if recognised, or ``None`` if the message
    is malformed or uses an unsupported protocol version / type.
    """
    if not isinstance(data, dict):
        return None
    if data.get("version") != PROTOCOL_VERSION:
        return None
    if data.get("type") != "robot_state":
        return None
    return data
