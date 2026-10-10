from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

_SECRET_PATTERNS = (
    re.compile(r"(?i)((?:api[_-]?key|token|password)=)([^\s&,]+)"),
    re.compile(r"(postgres(?:ql)?(?:\+\w+)?://[^:\s]+:)([^@\s]+)(@)"),
)


def pseudonymize(value: int | str | None) -> str | None:
    """Return a stable, non-reversible identifier suitable for logs."""

    if value is None:
        return None
    return hashlib.sha256(str(value).encode()).hexdigest()[:12]


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if key.casefold() in {"token", "password", "api_key"}
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if not isinstance(value, str):
        return value
    result = value
    for pattern in _SECRET_PATTERNS:
        if pattern.groups == 2:
            result = pattern.sub(r"\1[REDACTED]", result)
        else:
            result = pattern.sub(r"\1[REDACTED]\3", result)
    return result


class JsonFormatter(logging.Formatter):
    """One JSON object per line, without message bodies or exception values."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": _redact(record.getMessage()),
        }
        event_data = getattr(record, "event_data", None)
        if isinstance(event_data, dict):
            payload.update(_redact(event_data))
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)


__all__ = ["JsonFormatter", "configure_logging", "pseudonymize"]
