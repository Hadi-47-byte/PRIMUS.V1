"""FastAPI application factory for PRIMUS V1.

Usage (production / development)::

    # From the project root (requires .env with OPENAI_API_KEY):
    uvicorn primus.api.app:create_app --factory --reload

    # Or with an explicit host/port:
    uvicorn primus.api.app:create_app --factory --host 0.0.0.0 --port 8000

The factory builds a fresh ``FastAPI`` instance, wires the engine, and
configures CORS for local development.

For **testing**, pass a mock provider::

    app = create_app(provider=MockLLMProvider("..."))
    client = TestClient(app)
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from primus.api.routes import router
from primus.config import Settings, create_provider
from primus.curriculum.retriever import DeterministicCurriculumRetriever
from primus.engine import TutorEngine
from primus.provider import LLMProvider
from primus.robot.controller import RobotController
from primus.robot.expressions import RobotOrchestrator
from primus.robot.ws import robot_ws_handler
from primus.session import SessionManager
from primus.voice.edge_tts_provider import EdgeTTSProvider
from primus.voice.service import TTSService
from primus.voice.stt.service import STTService
from primus.voice.stt.gemini_provider import GeminiSTTProvider
from primus.voice.orchestrator import VoiceConversationService


def create_app(provider: Optional[LLMProvider] = None) -> FastAPI:
    """Build and return a configured FastAPI application.

    Parameters
    ----------
    provider:
        An ``LLMProvider`` instance.  When *None* the provider is built from
        environment / ``.env`` via ``primus.config``.
    """
    # -- dependencies --------------------------------------------------------
    settings = Settings()  # always load .env for STT credentials
    if provider is None:
        provider = create_provider(settings)

    session_manager = SessionManager()
    curriculum_retriever = DeterministicCurriculumRetriever()
    engine = TutorEngine(
        provider=provider,
        session_manager=session_manager,
        curriculum_retriever=curriculum_retriever,
    )
    robot_controller = RobotController()
    orchestrator = RobotOrchestrator(robot_controller)
    tts_service = TTSService(provider=EdgeTTSProvider())
    stt_service = STTService(provider=GeminiSTTProvider(
        model=settings.gemini_stt_model,
        api_key=settings.gemini_api_key or None,
    ))
    voice_service = VoiceConversationService(
        stt_service=stt_service,
        engine=engine,
        tts_service=tts_service,
        robot_orchestrator=orchestrator,
    )

    # -- application ---------------------------------------------------------
    app = FastAPI(
        title="PRIMUS V1",
        description="AI Physics Tutor API — Grades 6–10",
        version="0.2.0",
    )

    app.state.engine = engine
    app.state.robot_controller = robot_controller
    app.state.orchestrator = orchestrator
    app.state.tts_service = tts_service
    app.state.stt_service = stt_service
    app.state.voice_service = voice_service

    # -- CORS (local development) -------------------------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://localhost:5173",
            "http://localhost:8000",
            "http://localhost:8080",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:8000",
            "http://127.0.0.1:8080",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- routes --------------------------------------------------------------
    app.include_router(router)

    # -- WebSocket endpoint --------------------------------------------------
    @app.websocket("/ws/robot")
    async def _ws_robot(websocket: WebSocket):
        await robot_ws_handler(websocket, robot_controller)

    return app
