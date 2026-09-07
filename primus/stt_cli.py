"""CLI entry-point for STT testing without the robot or FastAPI.

Usage::

    python -m primus.stt_cli audio.wav --language en
    python -m primus.stt_cli audio.mp3
    python -m primus.stt_cli audio.wav --language ur
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from primus.voice.stt.models import STTProviderError
from primus.voice.stt.service import STTService
from primus.config import Settings
from primus.voice.stt.gemini_provider import GeminiSTTProvider


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m primus.stt_cli",
        description="PRIMUS V1 — Speech-to-Text CLI",
    )
    parser.add_argument("audio", help="Path to an audio file")
    parser.add_argument(
        "--language", "-l", default=None,
        help="Language hint: en, ur, auto (default: auto-detect)",
    )
    return parser


async def _run(args: argparse.Namespace) -> None:
    settings = Settings()
    provider = GeminiSTTProvider(
        api_key=settings.gemini_api_key or None,
    )
    service = STTService(provider=provider)

    t0 = time.time()
    try:
        result = await service.transcribe(args.audio, language=args.language)
    except STTProviderError as exc:
        print(f"STT Error: {exc}", file=sys.stderr)
        sys.exit(1)

    elapsed = int((time.time() - t0) * 1000)
    print(f"Transcript: {result.text}")
    print(f"Language:   {result.language}")
    print(f"Duration:   {result.duration_ms} ms" if result.duration_ms else "Duration:   unknown")
    print(f"Provider:   {result.provider}")
    print(f"Latency:    {elapsed} ms")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
