"""CLI entry-point for TTS testing without the robot or FastAPI.

Usage::

    python -m primus.voice "What is gravity?" --language en
    python -m primus.voice "Gravity ek force hai" --language ur
    python -m primus.voice "F = ma" --language en --voice en-US-JennyNeural
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from primus.voice.edge_tts_provider import EdgeTTSProvider
from primus.voice.models import TTSProviderError
from primus.voice.provider import VoiceRegistry
from primus.voice.service import TTSService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m primus.voice",
        description="PRIMUS V1 — Text-to-Speech CLI",
    )
    parser.add_argument("text", help="Text to synthesise")
    parser.add_argument(
        "--language", "-l", default="en",
        help="Language code: en, ur, roman_urdu (default: en)",
    )
    parser.add_argument(
        "--voice", "-v", default=None,
        help="Override the voice (e.g. en-US-JennyNeural)",
    )
    parser.add_argument(
        "--output-dir", "-o", default="data/audio",
        help="Output directory (default: data/audio)",
    )
    return parser


async def _run(args: argparse.Namespace) -> None:
    overrides = {}
    if args.voice:
        overrides[args.language] = args.voice

    registry = VoiceRegistry(overrides=overrides)
    provider = EdgeTTSProvider()
    service = TTSService(provider=provider, voice_registry=registry, output_dir=args.output_dir)

    t0 = time.time()
    try:
        result = await service.synthesize(args.text, language=args.language)
    except TTSProviderError as exc:
        print(f"TTS Error: {exc}", file=sys.stderr)
        sys.exit(1)

    elapsed = int((time.time() - t0) * 1000)
    print(f"Audio file: {result.audio_path}")
    print(f"Voice:      {result.voice}")
    print(f"Language:   {result.language}")
    print(f"Format:     {result.format}")
    print(f"Synthesis:  {elapsed} ms")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
