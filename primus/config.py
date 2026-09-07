"""Application configuration — loaded from environment / .env file."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


class Settings:
    """Runtime settings for PRIMUS V1.

    All values are read from environment variables (or a ``.env`` file)
    at construction time.  No secrets are hard-coded.
    """

    def __init__(self) -> None:
        # Attempt to load .env from the project root
        env_path = Path(__file__).resolve().parent.parent / ".env"
        load_dotenv(env_path)

        self.openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
        self.openai_base_url: Optional[str] = os.getenv("OPENAI_BASE_URL") or None
        self.openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

        # Gemini STT — uses a dedicated credential, never OPENAI_API_KEY
        self.gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
        self.gemini_stt_model: str = os.getenv("GEMINI_STT_MODEL", "gemini-3.5-transcribe")


def create_provider(settings: Settings):
    """Factory: build an ``OpenAICompatibleProvider`` from *settings*."""
    from primus.provider import OpenAICompatibleProvider

    return OpenAICompatibleProvider(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        model=settings.openai_model,
    )
