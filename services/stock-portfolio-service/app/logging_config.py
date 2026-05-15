"""structlog-based logging configuration for stock-portfolio-service.

Installs a structlog processor chain that:
- emits one JSON object per record by default (set ``LOG_FORMAT=console``
  for human-readable output during local development);
- injects OpenTelemetry ``trace_id`` / ``span_id`` from the current span
  so log lines correlate with traces in Tempo/Grafana;
- bridges stdlib ``logging`` so legacy ``logging.getLogger(__name__)``
  calls route through the same processors as ``structlog.get_logger(...)``.

``configure_logging()`` is idempotent: a sentinel guards against double
handler registration when the FastAPI app is imported more than once
(e.g. by ``TestClient`` plus pytest collection).
"""

from __future__ import annotations

import logging
import os
import sys

import structlog
from opentelemetry import trace

_CONFIGURED = False


def _otel_trace_processor(logger, method_name, event_dict):
    """Pull trace_id / span_id from the current OTel span, if any."""
    span = trace.get_current_span()
    context = span.get_span_context() if span else None
    if context is None or not context.is_valid:
        return event_dict
    event_dict["trace_id"] = format(context.trace_id, "032x")
    event_dict["span_id"] = format(context.span_id, "016x")
    return event_dict


def _renderer():
    fmt = os.getenv("LOG_FORMAT", "json").lower()
    if fmt == "console":
        return structlog.dev.ConsoleRenderer(colors=False)
    return structlog.processors.JSONRenderer()


def _processor_chain():
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _otel_trace_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]


def configure_logging() -> None:
    """Install the structlog processor chain. Safe to call repeatedly."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    pre_chain = _processor_chain()

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_chain,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _renderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        if isinstance(existing, logging.StreamHandler) and isinstance(
            existing.formatter, structlog.stdlib.ProcessorFormatter
        ):
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    structlog.configure(
        processors=[
            *pre_chain,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    _CONFIGURED = True


def reset_for_tests() -> None:
    """Drop the idempotency guard so tests can re-exercise configuration."""
    global _CONFIGURED
    _CONFIGURED = False
    structlog.reset_defaults()
