from __future__ import annotations

import contextvars
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

# Context variable for request ID correlation
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def set_request_id(request_id: str) -> None:
    """Set the request ID for the current context."""
    request_id_var.set(request_id)


def get_request_id() -> str | None:
    """Get the request ID for the current context."""
    return request_id_var.get()


class RequestIdFilter(logging.Filter):
    """Filter to add request ID to log records from context variable."""

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = get_request_id()
        if request_id is not None:
            record.request_id = request_id
        return True


# Patterns for sensitive data that should never be logged
_SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(password|passwd|pwd|secret|token|jwt|authorization|api[_-]?key|access[_-]?key|private[_-]?key)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(connection[_-]?string|dsn|jdbc|postgresql://|mysql://|mongodb://|redis://|amqp://)\S+"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+"),
    re.compile(r"(?i)secret[_-]?key\s*[:=]\s*\S+"),
]


class SensitiveDataFilter(logging.Filter):
    """Filter to redact sensitive data from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Redact sensitive data in the message
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)
        # Redact sensitive data in args
        if record.args:
            record.args = tuple(self._redact(arg) if isinstance(arg, str) else arg for arg in record.args)
        return True

    def _redact(self, text: str) -> str:
        for pattern in _SENSITIVE_PATTERNS:
            text = pattern.sub(lambda m: f"{m.group().split('=')[0]}=***REDACTED***" if "=" in m.group() else f"{m.group().split(':')[0]}:***REDACTED***", text)
        return text


class JsonFormatter(logging.Formatter):
    """Dependency-free structured logger which never serializes request bodies."""

    def format(self, record: logging.LogRecord) -> str:
        event: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "method", "path", "status_code", "duration_ms", "event"):
            value = getattr(record, key, None)
            if value is not None:
                event[key] = value
        if record.exc_info:
            event["exception"] = self.formatException(record.exc_info)
        return json.dumps(event, default=str, separators=(",", ":"))


def configure_logging(level: str) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    formatter = JsonFormatter()
    for handler in root.handlers:
        handler.setFormatter(formatter)
        # Add sensitive data filter to all handlers
        handler.addFilter(SensitiveDataFilter())
