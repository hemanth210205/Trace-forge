"""Turn arbitrary user values into bounded, JSON-safe, redacted data."""

import dataclasses
import json
import math
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

# Keys whose values are always replaced, compared after lowercasing and dropping "-" and "_".
DEFAULT_REDACT_KEYS = frozenset(
    {
        "password", "passwd", "secret", "clientsecret", "apikey", "xapikey", "authorization", "proxyauthorization",
        "token", "accesstoken", "refreshtoken", "idtoken", "sessiontoken", "cookie", "setcookie", "privatekey",
    }
)
DEFAULT_REDACT_PATTERNS = (
    r"\btf_[0-9a-f]{8}_[A-Za-z0-9_\-]{20,}",  # Traceforge API keys
    r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{8,}=*",
    r"\bsk-[A-Za-z0-9_\-]{20,}",  # common LLM provider key shape
    r"\bAKIA[0-9A-Z]{16}\b",  # AWS access key id
)
REDACTED = "[REDACTED]"
MAX_DEPTH = 12
MAX_ITEMS = 500  # per container


def _norm_key(key: str) -> str:
    return key.lower().replace("-", "").replace("_", "")


class Sanitizer:
    def __init__(
        self,
        redact_keys: Iterable[str] = (),
        redact_patterns: Iterable[str] = (),
        max_field_bytes: int = 32 * 1024,
        max_metadata_bytes: int = 8 * 1024,
    ) -> None:
        self.redact_keys = DEFAULT_REDACT_KEYS | {_norm_key(k) for k in redact_keys}
        self.patterns = [re.compile(p) for p in (*DEFAULT_REDACT_PATTERNS, *redact_patterns)]
        self.max_field_bytes = max_field_bytes
        self.max_metadata_bytes = max_metadata_bytes

    def redact_text(self, text: str) -> str:
        # NUL cannot be stored by the server's database (text or JSON); one would make the whole batch fail.
        text = text.replace("\x00", "")
        for pattern in self.patterns:
            text = pattern.sub(REDACTED, text)
        return text

    def clean(self, value: Any, depth: int = 0) -> Any:
        """JSON-safe copy with sensitive keys and secret-looking strings redacted."""
        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, float):
            # NaN and infinities are not JSON; sent as-is they would make the server reject the whole batch.
            return value if math.isfinite(value) else ("NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity")
        if isinstance(value, str):
            return self.redact_text(value)
        if depth >= MAX_DEPTH:
            return "[max depth reached]"
        if isinstance(value, Mapping):
            out: dict[str, Any] = {}
            for i, (k, v) in enumerate(value.items()):
                if i >= MAX_ITEMS:
                    out["[truncated]"] = f"{len(value) - MAX_ITEMS} more keys"
                    break
                key = self.redact_text(str(k))
                out[key] = REDACTED if _norm_key(str(k)) in self.redact_keys else self.clean(v, depth + 1)
            return out
        if isinstance(value, (list, tuple, set, frozenset)):
            items = list(value)
            cleaned = [self.clean(v, depth + 1) for v in items[:MAX_ITEMS]]
            if len(items) > MAX_ITEMS:
                cleaned.append(f"[{len(items) - MAX_ITEMS} more items]")
            return cleaned
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, (UUID, Decimal)):
            return str(value)
        if isinstance(value, Enum):
            return self.clean(value.value, depth + 1)
        if isinstance(value, (bytes, bytearray)):
            return f"<{len(value)} bytes>"
        model_dump = getattr(value, "model_dump", None)  # pydantic v2
        if callable(model_dump):
            try:
                return self.clean(model_dump(), depth + 1)
            except Exception:
                pass
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return self.clean(dataclasses.asdict(value), depth + 1)
        return self.redact_text(_safe_repr(value))

    def bounded(self, value: Any, limit: int | None = None) -> Any:
        """``clean`` plus a serialized-size cap: oversized values become a truncated preview."""
        limit = limit or self.max_field_bytes
        cleaned = self.clean(value)
        encoded = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))
        size = len(encoded.encode())
        if size <= limit:
            return cleaned
        preview_chars = max(0, limit // 2 - 200)  # leave room for the wrapper and multibyte characters
        return {"_truncated": True, "original_bytes": size, "preview": encoded[:preview_chars]}


def _safe_repr(value: Any, limit: int = 2000) -> str:
    try:
        text = repr(value)
    except Exception:
        text = f"<unrepresentable {type(value).__name__}>"
    return text if len(text) <= limit else text[:limit] + "..."
