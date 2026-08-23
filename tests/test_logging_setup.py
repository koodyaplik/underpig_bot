from __future__ import annotations

import logging

from app.logging_setup import JsonFormatter, configure_logging


def test_json_logging_keeps_aviationstack_url_and_redacts_telegram_token() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=(
            "GET https://example.test/flights?access_key=flight-secret&limit=1 "
            "BOT_TOKEN=123456:telegram-secret"
        ),
        args=(),
        exc_info=None,
    )

    result = JsonFormatter().format(record)

    assert "access_key=flight-secret" in result
    assert "telegram-secret" not in result
    assert result.count("[REDACTED]") == 1


def test_http_client_loggers_do_not_log_request_urls_at_info() -> None:
    configure_logging("INFO", "json")

    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() == logging.WARNING
