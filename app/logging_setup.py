from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

REDACTION_PATTERNS = (
    re.compile(r"(?i)((?:TELEGRAM_BOT_TOKEN|BOT_TOKEN)=)[^\s\"']+"),
    re.compile(r"(?i)(/bot)[0-9]+:[A-Za-z0-9_-]+"),
)


def redact_secrets(value: str) -> str:
    redacted = value
    for pattern in REDACTION_PATTERNS:
        redacted = pattern.sub(r"\1[REDACTED]", redacted)
    return redacted


class RedactingTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "event",
            "flight_id",
            "subscription_id",
            "endpoint_name",
            "http_status",
            "api_error_code",
            "duration_ms",
            "tracking_state",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return redact_secrets(json.dumps(payload, ensure_ascii=False, default=str))


def configure_logging(level: str, output_format: str) -> None:
    handler = logging.StreamHandler()
    if output_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            RedactingTextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    # httpx logs complete request URLs, including query-string credentials.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
