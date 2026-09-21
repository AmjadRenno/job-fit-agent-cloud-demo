"""Runtime mode boundary for the public V3 portfolio demo."""
from __future__ import annotations

import os

DEMO_MODE_READ_ONLY = "DEMO_MODE_READ_ONLY"
AGENTIC_SEARCH_DISABLED = "AGENTIC_SEARCH_DISABLED"


def app_mode() -> str:
    return os.environ.get("APP_MODE", "demo").strip().lower()


def is_demo_mode() -> bool:
    return app_mode() == "demo"


def require_mutable_mode() -> None:
    if is_demo_mode():
        raise RuntimeError(DEMO_MODE_READ_ONLY)


def agentic_search_enabled() -> bool:
    """The sole demo-mode OpenAI exception: a read-only, explicit fallback."""
    return (
        is_demo_mode()
        and os.environ.get("ENABLE_AGENTIC_SEARCH", "false").strip().lower() == "true"
        and bool(os.environ.get("OPENAI_API_KEY", "").strip())
    )


def require_agentic_search_mode() -> None:
    if not agentic_search_enabled():
        raise RuntimeError(AGENTIC_SEARCH_DISABLED)
