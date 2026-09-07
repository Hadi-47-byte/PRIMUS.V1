"""HTTP route definitions for the PRIMUS API.

Routes are defined on an ``APIRouter`` and included by the app factory
(``primus.api.app.create_app``).  The engine is accessed via
``request.app.state.engine`` — this keeps routes testable without
global singletons.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from primus.api.schemas import ChatRequest, ChatResponse, HealthResponse
from primus.provider import LLMProviderError
from primus.robot.protocol import ALLOWED_STATES, validate_state
from primus.voice.models import TTSProviderError
from primus.voice.stt.models import STTProviderError
from primus.voice.stt.service import SUPPORTED_EXTENSIONS
from primus.voice.orchestrator import ROBOT_AUDIO_SAMPLE_RATE, VoiceConversationService

logger = logging.getLogger(__name__)

router = APIRouter()

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Lightweight health check — no LLM call."""
    return HealthResponse(status="ok")


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------

@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        400: {"description": "Invalid request (empty message / session_id)"},
        502: {"description": "LLM provider error"},
        500: {"description": "Unexpected internal error"},
    },
    tags=["tutor"],
)
async def chat(body: ChatRequest, request: Request) -> ChatResponse | JSONResponse:
    """Send a student message to the tutor engine and return a structured response.

    The route delegates **all** tutoring logic to ``TutorEngine.chat()``.
    It performs only:

    1. Input validation (empty/whitespace checks).
    2. Engine delegation.
    3. Response wrapping with ``session_id``.
    """
    engine = request.app.state.engine

    # -- input validation ----------------------------------------------------
    if not body.message.strip():
        return JSONResponse(
            status_code=400,
            content={"detail": "Message must not be empty."},
        )

    if not body.session_id.strip():
        return JSONResponse(
            status_code=400,
            content={"detail": "session_id must not be empty."},
        )

    # -- delegate to engine --------------------------------------------------
    try:
        tutor_response = engine.chat(
            message=body.message,
            session_id=body.session_id,
        )
    except LLMProviderError as exc:
        # Local-diagnostic log only: redact credential-like substrings
        # before logging so the API key is never written to the log.
        sanitized = re.sub(r"sk-[A-Za-z0-9_\-]+", "sk-***", str(exc))
        logger.error("LLM provider unavailable during /chat: %s", sanitized)
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "llm_provider_unavailable",
                    "message": "PRIMUS AI service is temporarily unavailable.",
                }
            },
        )
    except Exception as exc:
        logger.error("Unexpected error in /chat: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred. Please try again.",
                }
            },
        )

    # -- wrap with session_id ------------------------------------------------
    # -- dispatch robot state (fire-and-forget, non-fatal) --------------------
    orchestrator = request.app.state.orchestrator
    await orchestrator.dispatch(tutor_response)

    return ChatResponse(
        session_id=body.session_id,
        response_text=tutor_response.response_text,
        language=tutor_response.language,
        interaction_state=tutor_response.interaction_state.value,
        topic=tutor_response.topic,
    )


# ---------------------------------------------------------------------------
# Static test UI
# ---------------------------------------------------------------------------

@router.get("/", include_in_schema=False)
async def serve_index():
    """Serve the browser test UI."""
    return FileResponse(_WEB_DIR / "index.html")


@router.get("/app.js", include_in_schema=False)
async def serve_js():
    return FileResponse(_WEB_DIR / "app.js")


@router.get("/styles.css", include_in_schema=False)
async def serve_css():
    return FileResponse(_WEB_DIR / "styles.css")


# ---------------------------------------------------------------------------
# Robot test endpoint (development only)
# ---------------------------------------------------------------------------

class RobotStateRequest(BaseModel):
    """Manual robot-state command for testing."""
    state: str


@router.post(
    "/robot/state",
    tags=["robot"],
    responses={
        400: {"description": "Unknown or invalid state"},
        503: {"description": "No robot connected"},
    },
)
async def set_robot_state(body: RobotStateRequest, request: Request):
    """Send a semantic state to the connected ESP32 (dev/test only).

    Returns the delivery status.  Does **not** involve the LLM.
    """
    controller = request.app.state.robot_controller

    if not validate_state(body.state):
        return JSONResponse(
            status_code=400,
            content={
                "status": "invalid_state",
                "allowed_states": sorted(ALLOWED_STATES),
            },
        )

    if not controller.is_connected():
        return JSONResponse(
            status_code=503,
            content={"status": "robot_offline"},
        )

    sent = await controller.send_state(body.state)
    if sent:
        return {"status": "sent", "state": body.state}
    else:
        return JSONResponse(
            status_code=503,
            content={"status": "send_failed"},
        )


class RobotAudioRequest(BaseModel):
    """Manual robot-audio command for testing."""
    filename: str
    sample_rate: int = ROBOT_AUDIO_SAMPLE_RATE


@router.post(
    "/robot/audio",
    tags=["robot"],
    responses={
        400: {"description": "Invalid filename or sample rate"},
        404: {"description": "Audio file not found in TTS output dir"},
        503: {"description": "No robot connected"},
    },
)
async def set_robot_audio(body: RobotAudioRequest, request: Request):
    """Send a TTS audio file to the connected ESP32 (dev/test only).

    Takes a filename already present in the TTS output directory and
    dispatches it to the robot via ``RobotOrchestrator.dispatch_audio()``
    — bypassing STT/chat/TTS entirely, so firmware playback can be
    re-tested against an existing ``.pcm`` file without spending LLM
    quota on repeated ``/voice`` calls.
    """
    controller = request.app.state.robot_controller
    orchestrator = request.app.state.orchestrator
    tts_service = request.app.state.tts_service

    # Path traversal guard (same rules as GET /audio/{filename})
    if ".." in body.filename or "/" in body.filename or "\\" in body.filename:
        return JSONResponse(
            status_code=400,
            content={"status": "invalid_filename"},
        )

    if body.sample_rate <= 0:
        return JSONResponse(
            status_code=400,
            content={"status": "invalid_sample_rate"},
        )

    file_path = tts_service.output_dir / body.filename
    if not file_path.exists() or not file_path.is_file():
        return JSONResponse(
            status_code=404,
            content={"status": "file_not_found"},
        )

    if not controller.is_connected():
        return JSONResponse(
            status_code=503,
            content={"status": "robot_offline"},
        )

    sent = await orchestrator.dispatch_audio(
        f"/audio/{body.filename}", body.sample_rate,
    )
    if sent:
        return {
            "status": "sent",
            "audio_url": f"/audio/{body.filename}",
            "sample_rate": body.sample_rate,
        }
    else:
        return JSONResponse(
            status_code=503,
            content={"status": "send_failed"},
        )


# ---------------------------------------------------------------------------
# TTS endpoint (development / testing)
# ---------------------------------------------------------------------------

class TTSRequest(BaseModel):
    """Text-to-speech request."""
    text: str
    language: str = "en"


@router.post(
    "/tts",
    tags=["tts"],
    responses={
        400: {"description": "Invalid input (empty text)"},
        503: {"description": "TTS service unavailable"},
    },
)
async def synthesize_speech(body: TTSRequest, request: Request):
    """Synthesise text to an audio file (dev/test endpoint).

    Returns metadata about the generated file.  Audio can be streamed
    via ``GET /audio/{filename}``.
    """
    service = request.app.state.tts_service

    if not body.text.strip():
        return JSONResponse(
            status_code=400,
            content={"status": "invalid_input", "detail": "Text must not be empty."},
        )

    try:
        result = await service.synthesize(body.text, language=body.language)
    except TTSProviderError as exc:
        logger.warning("TTS synthesis failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "tts_error", "detail": "TTS synthesis failed."},
        )

    filename = Path(result.audio_path).name
    return {
        "status": "ok",
        "audio_path": result.audio_path,
        "audio_url": f"/audio/{filename}",
        "language": result.language,
        "voice": result.voice,
        "format": result.format,
        "duration_ms": result.duration_ms,
    }


@router.get("/audio/{filename}", include_in_schema=False)
async def serve_audio(filename: str, request: Request):
    """Stream a generated audio file."""
    service = request.app.state.tts_service
    file_path = service.output_dir / filename
    if not file_path.exists() or not file_path.is_file():
        return JSONResponse(status_code=404, content={"detail": "Audio file not found."})
    # Prevent path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        return JSONResponse(status_code=400, content={"detail": "Invalid filename."})
    return FileResponse(file_path, media_type="audio/mpeg")


# ---------------------------------------------------------------------------
# STT endpoint (development / testing)
# ---------------------------------------------------------------------------

@router.post(
    "/stt",
    tags=["stt"],
    responses={
        400: {"description": "Invalid audio file"},
        503: {"description": "STT service unavailable"},
    },
)
async def transcribe_audio(
    audio: UploadFile = File(...),
    language: str | None = Form(None),
    request: Request = None,
):
    """Transcribe an uploaded audio file to text (dev/test endpoint).

    Accepts multipart/form-data with an ``audio`` file and optional
    ``language`` hint.
    """
    service = request.app.state.stt_service

    # Validate uploaded file extension
    filename = audio.filename or "upload.bin"
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        return JSONResponse(
            status_code=400,
            content={
                "status": "invalid_format",
                "detail": f"Unsupported format '{suffix}'. "
                          f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            },
        )

    # Save to temporary file
    import tempfile, os
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            content = await audio.read()
            f.write(content)

        result = await service.transcribe(tmp_path, language=language)
    except STTProviderError as exc:
        logger.warning("STT transcription failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "stt_error", "detail": "Transcription failed."},
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return {
        "status": "ok",
        "text": result.text,
        "language": result.language,
        "confidence": result.confidence,
        "duration_ms": result.duration_ms,
        "provider": result.provider,
    }


# ---------------------------------------------------------------------------
# Voice conversation endpoint (full pipeline)
# ---------------------------------------------------------------------------

@router.post(
    "/voice",
    tags=["voice"],
    responses={
        400: {"description": "Invalid audio file"},
        503: {"description": "Service unavailable (STT or LLM)"},
    },
)
async def voice_conversation(
    audio: UploadFile = File(...),
    session_id: str = Form(...),
    language: str | None = Form(None),
    request: Request = None,
):
    """Run the complete voice pipeline: audio → STT → Tutor → TTS + Robot.

    Accepts multipart/form-data with ``audio``, ``session_id``, and
    optional ``language`` hint.
    """
    service: VoiceConversationService = request.app.state.voice_service

    # Validate uploaded file extension
    filename = audio.filename or "upload.bin"
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        return JSONResponse(
            status_code=400,
            content={
                "status": "invalid_format",
                "detail": f"Unsupported format '{suffix}'. "
                          f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            },
        )

    # Save to temporary file
    import tempfile, os
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            content = await audio.read()
            f.write(content)

        result = await service.process_audio(
            audio_path=tmp_path,
            session_id=session_id,
            language=language,
        )
    except STTProviderError as exc:
        logger.warning("Voice pipeline STT failure: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "stt_error", "detail": "Transcription failed."},
        )
    except LLMProviderError as exc:
        logger.warning("Voice pipeline LLM failure: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "llm_provider_unavailable", "detail": "LLM provider unavailable."},
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # Build response
    audio_url = None
    if result.audio_path:
        fname = Path(result.audio_path).name
        audio_url = f"/audio/{fname}"

    return {
        "status": "ok",
        "session_id": result.session_id,
        "transcript": result.transcript,
        "transcript_language": result.transcript_language,
        "tutor_response": {
            "response_text": result.tutor_response.response_text,
            "language": result.tutor_response.language,
            "interaction_state": result.tutor_response.interaction_state.value,
            "topic": result.tutor_response.topic,
        },
        "audio_path": result.audio_path,
        "audio_url": audio_url,
        "audio_format": result.audio_format,
        "robot_state": result.robot_state,
        "timings": {
            "stt_ms": result.stt_latency_ms,
            "tutor_ms": result.tutor_latency_ms,
            "tts_ms": result.tts_latency_ms,
            "robot_ms": result.robot_latency_ms,
            "total_ms": result.total_latency_ms,
        },
    }
