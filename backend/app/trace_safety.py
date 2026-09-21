"""Minimal, dependency-free sanitizers for recorded error/execution traces.

Phase 11 hardening: execution events and failure summaries should never leak
query strings or obvious secret/token material, while keeping useful
diagnostic information (exception type, message structure, URL host/path).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?P<key>(?:api[_-]?key|access[_-]?token|auth(?:orization)?|password|passwd|secret|client[_-]?secret|token|bearer))"
    r"(?P<sep>\s*[=:]\s*)(?P<quote>[\"']?)(?P<value>[^\s\"']+)(?P=quote)"
)
_CREDENTIAL_HEADER_RE = re.compile(
    r"(?i)((?:authorization|proxy-authorization)\s*[=:]\s*)"
    r"(?:(?:bearer|basic)\s+)?[A-Za-z0-9._~+/=%-]+"
)
_OPENAI_KEY_RE = re.compile(r"(?i)(sk-[A-Za-z0-9_-]{16,})")
_URL_RE = re.compile(r"https?://[^\s\"'<>)]+")

_MAX_URL_LENGTH = 4096


def strip_query_string(value: str) -> str:
    """Remove the query string from a URL, preserving scheme/host/path/fragment."""
    if not value:
        return value
    parts = urlsplit(value)
    if not parts.query:
        return value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment))


def _redact_url(match: re.Match[str]) -> str:
    return strip_query_string(match.group(0))


def sanitize_error_text(value: str | None) -> str | None:
    """Redact secrets/tokens and strip URL query strings from error text.

    Deterministic and dependency-free. Does not remove exception class names,
    HTTP status codes, or URL host/path information.
    """
    if not value:
        return value
    text = value[: _MAX_URL_LENGTH]
    text = _OPENAI_KEY_RE.sub("sk-REDACTED", text)
    text = _CREDENTIAL_HEADER_RE.sub(r"\1REDACTED", text)
    text = _SECRET_ASSIGNMENT_RE.sub(lambda m: f"{m.group('key')}{m.group('sep')}REDACTED", text)
    return _URL_RE.sub(_redact_url, text)


def sanitize_job_url(value: str | None) -> str | None:
    """Sanitize a recorded job URL for traces (query strings are not diagnostic)."""
    if not value:
        return value
    return strip_query_string(value)