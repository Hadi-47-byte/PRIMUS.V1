"""Terminal REPL for PRIMUS V1 — the first runnable milestone.

Usage::

    python -m primus.main

or, once installed::

    primus

The REPL exercises the full text pipeline:

    text input → session → tutor prompt → LLM call → TutorResponse → text output
"""

from __future__ import annotations

import logging
import sys

from primus.config import Settings, create_provider
from primus.engine import TutorEngine
from primus.session import SessionManager

BANNER = """\
╔══════════════════════════════════════════════════════╗
║         PRIMUS V1 — AI Physics Tutor                 ║
║                                                      ║
║  Text-first milestone.  Type your question below.    ║
║                                                      ║
║  Commands:                                           ║
║    /new    — start a new session                     ║
║    /state  — show current session state              ║
║    /quit   — exit                                    ║
╚══════════════════════════════════════════════════════╝
"""


def _print_response(response, session) -> None:
    """Pretty-print a ``TutorResponse`` in the terminal."""
    print()
    print(f"  [{response.interaction_state.value}]")
    print(f"  PRIMUS: {response.response_text}")
    if response.topic:
        print(f"  (topic: {response.topic})")
    print(f"  (language: {response.language})")
    print()


def repl() -> None:
    """Interactive terminal loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    settings = Settings()
    if not settings.openai_api_key:
        print(
            "\n  ERROR: OPENAI_API_KEY is not set.\n"
            "  Copy .env.example → .env and add your key.\n",
            file=sys.stderr,
        )
        sys.exit(1)

    provider = create_provider(settings)
    sessions = SessionManager()
    engine = TutorEngine(provider=provider, session_manager=sessions)

    session = sessions.get_or_create()
    print(BANNER)
    print(f"  Session: {session.session_id}\n")

    while True:
        try:
            user_input = input("  You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Goodbye!")
            break

        if not user_input:
            continue

        # -- REPL commands ---------------------------------------------------
        if user_input.lower() in {"/quit", "/exit", "quit", "exit"}:
            print("\n  Goodbye!")
            break

        if user_input.lower() == "/new":
            session = sessions.get_or_create()
            print(f"\n  New session: {session.session_id}\n")
            continue

        if user_input.lower() == "/state":
            print(f"\n  Session : {session.session_id}")
            print(f"  Topic   : {session.current_topic}")
            print(f"  Language: {session.language}")
            print(f"  Depth   : {session.explanation_depth}")
            print(f"  History : {len(session.history)} messages\n")
            continue

        # -- Tutor interaction -----------------------------------------------
        response = engine.chat(user_input, session_id=session.session_id)
        _print_response(response, session)


def main() -> None:
    """Entry point."""
    repl()


if __name__ == "__main__":
    main()
