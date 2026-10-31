"""Structured JSON logging with secret scrubbing.

Every log line passes through ``scrub`` before it is written, so API keys, bearer
tokens, session cookies and password fields never reach the log output even if a
caller logs them by mistake.
"""

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_SCRUB_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Traceforge API keys: keep the public prefix so keys remain identifiable.
    (re.compile(r"\b(tf_[0-9a-f]{8}_)[A-Za-z0-9_\-]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/\-]+=*"), r"\1[REDACTED]"),
    # LLM provider keys (Anthropic "sk-ant-..." and other "sk-..." shapes).
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "[REDACTED]"),
    (re.compile(r"(?i)\b(tf_session|tf_csrf)=[^;\s]+"), r"\1=[REDACTED]"),
    (
        re.compile(r"(?i)([\"']?(?:password|secret|api_key|authorization|token)[\"']?\s*[:=]\s*[\"']?)[^\"',\s}]+"),
        r"\1[REDACTED]",
    ),
]


def scrub(text: str) -> str:
    for pattern, replacement in _SCRUB_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        for key in ("method", "path", "status", "duration_ms"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return scrub(json.dumps(payload, default=str))


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        if isinstance(existing.formatter, JsonFormatter):
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())
