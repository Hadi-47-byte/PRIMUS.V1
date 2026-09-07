"""Voice Conversation Orchestrator — the complete software pipeline.

Coordinates the end-to-end voice interaction:

    Audio file → STT → transcript → TutorEngine → TutorResponse
                                      ├→ TTS → audio (mp3 + robot pcm)
                                      └→ ExpressionMapper → RobotController

The orchestrator is a thin coordination layer — it does NOT contain
business logic from STT, TutorEngine, TTS, or robot components.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

import imageio_ffmpeg

from pydantic import BaseModel, Field

from primus.engine import TutorEngine
from primus.models import TutorResponse
from primus.provider import LLMProviderError
from primus.robot.controller import RobotController
from primus.robot.expressions import RobotOrchestrator, map_interaction_to_robot_state
from primus.voice.models import TTSProviderError
from primus.voice.service import TTSService
from primus.voice.stt.models import STTProviderError
from primus.voice.stt.service import STTService

logger = logging.getLogger(__name__)

# Sample rate the ESP32 speaker I2S is configured for (config.h
# AUDIO_SAMPLE_RATE).  PCM audio dispatched to the robot is resampled
# to this exact rate — a mismatch would play pitch-shifted/garbled.
ROBOT_AUDIO_SAMPLE_RATE = 16000


def _mp3_to_pcm16(mp3_path: str, sample_rate: int = ROBOT_AUDIO_SAMPLE_RATE) -> str:
    """Convert MP3 to headerless 16-bit mono PCM at the robot's expected rate."""
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    pcm_path = str(Path(mp3_path).with_suffix(".pcm"))
    subprocess.run(
        [ffmpeg_exe, "-y", "-i", mp3_path, "-ar", str(sample_rate),
         "-ac", "1", "-f", "s16le", pcm_path],
        check=True, capture_output=True,
    )
    return pcm_path


class VoiceConversationResult(BaseModel):
    """Typed result of a complete voice conversation turn."""

    session_id: str
    transcript: str = Field(..., description="STT transcribed text")
    transcript_language: Optional[str] = None
    tutor_response: TutorResponse
    audio_path: Optional[str] = Field(
        default=None, description="Generated TTS audio path (None if TTS failed)"
    )
    audio_format: Optional[str] = Field(default=None, description="Audio format (e.g. 'mp3')")
    robot_state: Optional[str] = Field(
        default=None, description="Intended robot semantic state"
    )
    stt_latency_ms: Optional[int] = None
    tutor_latency_ms: Optional[int] = None
    tts_latency_ms: Optional[int] = None
    robot_latency_ms: Optional[int] = None
    total_latency_ms: Optional[int] = None


class VoiceConversationService:
    """Orchestrates the complete voice-tutor pipeline.

    Coordinates STT → TutorEngine → TTS + RobotOrchestrator.
    Does NOT contain their internal logic — delegates to each service.
    """

    def __init__(
        self,
        stt_service: STTService,
        engine: TutorEngine,
        tts_service: TTSService,
        robot_orchestrator: RobotOrchestrator,
    ) -> None:
        self._stt = stt_service
        self._engine = engine
        self._tts = tts_service
        self._robot = robot_orchestrator

    async def process_audio(
        self,
        audio_path: str,
        session_id: str,
        language: Optional[str] = None,
    ) -> VoiceConversationResult:
        """Run the full voice conversation pipeline.

        Failure isolation:
        - STT failure → STTProviderError (pipeline cannot continue)
        - Tutor failure → LLMProviderError (no TTS/robot attempt)
        - TTS failure → non-fatal, audio_path = None
        - Robot failure → non-fatal, robot_state may still be set
        """
        t0 = time.time()

        # ── 1. STT (critical) ──────────────────────────────────────
        stt_t0 = time.time()
        stt_result = await self._stt.transcribe(audio_path, language=language)
        stt_ms = int((time.time() - stt_t0) * 1000)

        # ── 2. TutorEngine (critical) ──────────────────────────────
        tutor_t0 = time.time()
        try:
            tutor_response = self._engine.chat(
                stt_result.text, session_id=session_id,
            )
        except LLMProviderError:
            raise  # caller decides what to do
        tutor_ms = int((time.time() - tutor_t0) * 1000)

        # ── 3. TTS (non-critical) ──────────────────────────────────
        tts_ms = None
        audio_path_out = None
        audio_format = None
        pcm_filename = None
        try:
            tts_t0 = time.time()
            tts_result = await self._tts.synthesize(
                tutor_response.response_text,
                language=tutor_response.language,
            )
            tts_ms = int((time.time() - tts_t0) * 1000)
            audio_path_out = tts_result.audio_path
            audio_format = tts_result.format
            pcm_path = _mp3_to_pcm16(audio_path_out)
            pcm_filename = Path(pcm_path).name
        except TTSProviderError as exc:
            logger.warning("TTS failed in voice pipeline: %s", exc)
        except Exception as exc:
            logger.warning("MP3->PCM conversion failed: %s", exc)

        # ── 4. Robot (non-critical) ────────────────────────────────
        robot_ms = None
        robot_state = None
        try:
            robot_t0 = time.time()
            robot_state = map_interaction_to_robot_state(
                tutor_response.interaction_state.value,
            )
            await self._robot.dispatch(tutor_response)
            if pcm_filename:
                await self._robot.dispatch_audio(
                    f"/audio/{pcm_filename}", ROBOT_AUDIO_SAMPLE_RATE,
                )
            robot_ms = int((time.time() - robot_t0) * 1000)
        except Exception as exc:
            logger.warning("Robot dispatch failed in voice pipeline: %s", exc)

        total_ms = int((time.time() - t0) * 1000)

        return VoiceConversationResult(
            session_id=session_id,
            transcript=stt_result.text,
            transcript_language=stt_result.language,
            tutor_response=tutor_response,
            audio_path=audio_path_out,
            audio_format=audio_format,
            robot_state=robot_state,
            stt_latency_ms=stt_ms,
            tutor_latency_ms=tutor_ms,
            tts_latency_ms=tts_ms,
            robot_latency_ms=robot_ms,
            total_latency_ms=total_ms,
        )
