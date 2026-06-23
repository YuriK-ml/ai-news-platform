from __future__ import annotations

import logging
import re
from typing import Any


_TELEGRAM_BOT_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
_OPENAI_API_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._=-]+\b")

# Redact Telegram Bot API URLs: /bot<TOKEN>/... -> /bot***/...
_TELEGRAM_BOT_URL_RE = re.compile(r"(/bot)([^/\s]+)")

# Redact common env-var style leaks: FOO=secret
_ENV_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(TELEGRAM_BOT_TOKEN|OPENAI_API_KEY)\s*=\s*([^\s\"',]+)"
)


def redact_secrets(text: str) -> str:
    if not text:
        return text

    redacted = str(text)
    redacted = _ENV_ASSIGNMENT_RE.sub(r"\1=***", redacted)
    redacted = _TELEGRAM_BOT_URL_RE.sub(r"\1***", redacted)
    redacted = _TELEGRAM_BOT_TOKEN_RE.sub("***", redacted)
    redacted = _OPENAI_API_KEY_RE.sub("***", redacted)
    redacted = _BEARER_RE.sub("Bearer ***", redacted)
    return redacted


def redact_event(value: Any) -> Any:
    """
    Recursively redact secrets in structured event payloads.
    """

    if value is None:
        return None
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {k: redact_event(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        t = [redact_event(v) for v in value]
        return type(value)(t) if isinstance(value, tuple) else t
    return value


class RedactionFilter(logging.Filter):
    """
    Prevent secret leakage in stdlib logging (e.g. httpx INFO logs).
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            msg = record.getMessage()
        except Exception:
            return True

        safe = redact_secrets(msg)
        if safe != msg:
            record.msg = safe
            record.args = ()
        return True

