"""CLI entry-point for the complete voice conversation pipeline.

Usage::

    python -m primus.voice.conversation audio.wav --session demo --language en
    python -m primus.voice.conversation audio.mp3 -s my-session -l ur
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from primus.config import Settings, create_provider
from primus.engine import TutorEngine
from primus.models import TutorResponse
from primus.provider import LLMProviderError
from primus.robot.controller import RobotController
from primus.robot.expressions import RobotOrchestrator
from primus.voice.edge_tts_provider import EdgeTTSProvider
from primus.voice.models import TTSProviderError
from primus.voice.orchestrator import VoiceConversationService, VoiceConversationResult
from primus.voice.service import TTSService
from primus.voice.stt.models import STTProviderError
from primus.voice.stt.service import STTService
from primus.voice.stt.gemini_provider import GeminiSTTProvider


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m primus.voice.conversation",
        description="PRIMUS V1 — Voice Conversation Pipeline CLI",
    )
    parser.add_argument("audio", help="Path to an audio file")
    parser.add_argument(
        "--session", "-s", default="cli-session",
        help="Session ID (default: cli-session)",
    )
    parser.add_argument(
        "--language", "-l", default=None,
        help="Language hint: en, ur, auto (default: auto-detect)",
    )
    return parser


async def _run(args: argparse.Namespace) -> None:
    # Build services from config (uses .env for API keys)
    settings = Settings()
    llm_provider = create_provider(settings)
    engine = TutorEngine(provider=llm_provider)
    stt_service = STTService(provider=GeminiSTTProvider(
        api_key=settings.gemini_api_key or None,
    ))
    tts_service = TTSService(provider=EdgeTTSProvider())
    robot_controller = RobotController()
    robot_orchestrator = RobotOrchestrator(robot_controller)

    voice_service = VoiceConversationService(
        stt_service=stt_service,
        engine=engine,
        tts_service=tts_service,
        robot_orchestrator=robot_orchestrator,
    )

    try:
        result = await voice_service.process_audio(
            audio_path=args.audio,
            session_id=args.session,
            language=args.language,
        )
    except STTProviderError as exc:
        print(f"\nSTT Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except LLMProviderError as exc:
        print(f"\nLLM Error: Provider unavailable — {exc}", file=sys.stderr)
        sys.exit(2)

    _print_result(result)


def _print_result(result: VoiceConversationResult) -> None:
    print(f"Session:    {result.session_id}")
    print(f"\n── STT ──")
    print(f"Transcript: {result.transcript}")
    print(f"Language:   {result.transcript_language}")
    print(f"\n── Tutor ──")
    print(f"Response:   {result.tutor_response.response_text}")
    print(f"Language:   {result.tutor_response.language}")
    print(f"State:      {result.tutor_response.interaction_state.value}")
    if result.tutor_response.topic:
        print(f"Topic:      {result.tutor_response.topic}")
    print(f"\n── TTS ──")
    if result.audio_path:
        print(f"Audio:      {result.audio_path}")
        print(f"Format:     {result.audio_format}")
    else:
        print(f"Audio:      unavailable (TTS failed or skipped)")
    print(f"\n── Robot ──")
    if result.robot_state:
        print(f"State:      {result.robot_state}")
    else:
        print(f"State:      unavailable")
    print(f"Connected:  no (no ESP32 in CLI mode)")
    print(f"\n── Timing ──")
    print(f"STT:        {result.stt_latency_ms} ms" if result.stt_latency_ms is not None else "STT:        n/a")
    print(f"LLM:        {result.tutor_latency_ms} ms" if result.tutor_latency_ms is not None else "LLM:        n/a")
    print(f"TTS:        {result.tts_latency_ms} ms" if result.tts_latency_ms is not None else "TTS:        n/a")
    print(f"Robot:      {result.robot_latency_ms} ms" if result.robot_latency_ms is not None else "Robot:      n/a")
    print(f"Total:      {result.total_latency_ms} ms" if result.total_latency_ms is not None else "Total:      n/a")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
