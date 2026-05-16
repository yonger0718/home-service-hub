"""structlog + OTel trace correlation."""

import io
import json
import logging
import os
from unittest.mock import patch

import pytest
import structlog
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from app import logging_config


@pytest.fixture(autouse=True)
def _reset_logging():
    logging_config.reset_for_tests()
    yield
    logging_config.reset_for_tests()


def _capture(monkeypatch) -> io.StringIO:
    """Install a stdout-capturing handler routed through the configured formatter."""
    buf = io.StringIO()
    logging_config.configure_logging()
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(buf)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=logging_config._processor_chain(),
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                logging_config._renderer(),
            ],
        )
    )
    root.addHandler(handler)
    return buf


def test_json_renderer_is_default(monkeypatch):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    buf = _capture(monkeypatch)
    log = structlog.get_logger("svc.test")
    log.info("hello", extra_field=42)
    payload = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert payload["event"] == "hello"
    assert payload["level"] == "info"
    assert payload["logger"] == "svc.test"
    assert payload["extra_field"] == 42
    assert "timestamp" in payload


def test_console_renderer_when_log_format_console(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "console")
    buf = _capture(monkeypatch)
    structlog.get_logger("svc.test").info("hi-console")
    line = buf.getvalue().strip().splitlines()[-1]
    assert "hi-console" in line
    with pytest.raises(json.JSONDecodeError):
        json.loads(line)


def test_stdlib_logger_routes_through_formatter(monkeypatch):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    buf = _capture(monkeypatch)
    logging.getLogger("svc.legacy").warning("legacy-msg")
    payload = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert payload["event"] == "legacy-msg"
    assert payload["logger"] == "svc.legacy"
    assert payload["level"] == "warning"


def test_trace_id_injected_when_span_active(monkeypatch):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    buf = _capture(monkeypatch)
    trace.set_tracer_provider(TracerProvider())
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("unit-span"):
        structlog.get_logger("svc.trace").info("traced")
    payload = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert "trace_id" in payload and len(payload["trace_id"]) == 32
    assert "span_id" in payload and len(payload["span_id"]) == 16


def test_trace_id_omitted_when_no_active_span(monkeypatch):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    buf = _capture(monkeypatch)
    trace.set_tracer_provider(TracerProvider())  # ensure default no-span tracer
    structlog.get_logger("svc.notrace").info("plain")
    payload = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert "trace_id" not in payload
    assert "span_id" not in payload


def test_configure_logging_is_idempotent(monkeypatch):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    logging_config.configure_logging()
    before = list(logging.getLogger().handlers)
    logging_config.configure_logging()
    after = list(logging.getLogger().handlers)
    structlog_handlers_before = [
        h for h in before
        if isinstance(h, logging.StreamHandler)
        and isinstance(h.formatter, structlog.stdlib.ProcessorFormatter)
    ]
    structlog_handlers_after = [
        h for h in after
        if isinstance(h, logging.StreamHandler)
        and isinstance(h.formatter, structlog.stdlib.ProcessorFormatter)
    ]
    assert len(structlog_handlers_before) == 1
    assert len(structlog_handlers_after) == 1
