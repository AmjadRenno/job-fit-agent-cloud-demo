"""Small, secret-safe runtime configuration boundary."""
from __future__ import annotations

import os

DEFAULT_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def cors_origins() -> list[str]:
    raw = os.environ.get("CORS_ORIGINS")
    origins = [value.strip().rstrip("/") for value in raw.split(",")] if raw else list(DEFAULT_CORS_ORIGINS)
    if not origins or any(not value or value == "*" for value in origins):
        raise RuntimeError("CORS_ORIGINS must contain explicit origins; wildcard origins are not allowed")
    return origins
