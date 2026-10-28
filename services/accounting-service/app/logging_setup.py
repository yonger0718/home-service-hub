"""Accounting-service log output (#190).

The `app.*` loggers (auto_pay_job, schedule_job, the services) used to reach nowhere: the shared tracing setup only
attaches an OpenTelemetry handler, and that one is absent when the collector endpoint is unset. `configure_logging()`
gives the `app` logger one stderr handler — stderr is the journal under the systemd unit — and a formatter that keeps
the `extra=` fields the jobs log their run summaries with. Records still propagate, so an OTel handler on the root
keeps receiving them; uvicorn's own loggers are left alone.
"""

import json
import logging
import os
import sys

LOG_LEVEL_ENV = "ACCOUNTING_LOG_LEVEL"
DEFAULT_LEVEL = logging.INFO
HANDLER_NAME = "accounting-stderr"
APP_LOGGER = "app"

# Attributes every LogRecord carries (plus what Formatter.format adds); anything else came from `extra=`.
_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


def _render(value: object) -> str:
    if isinstance(value, str) and value and not any(ch.isspace() or ch in '"=' for ch in value):
        return value
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


class ExtraFormatter(logging.Formatter):
    """`<time> <LEVEL> <logger> <message> key=value …`, one line per record: the `extra=` fields in sorted order,
    each value bare when it is a plain token and JSON otherwise (so a multi-line value stays on one line).
    OpenTelemetry's injected `otel*` attributes are left out."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        extras = sorted(
            (key, value) for key, value in vars(record).items()
            if key not in _STANDARD_ATTRS and not key.startswith("otel") and not key.startswith("_")
        )
        if extras:
            line += " " + " ".join(f"{key}={_render(value)}" for key, value in extras)
        return line.replace("\n", "\\n")


def _level_from_env() -> tuple[int, str | None]:
    """The level from ACCOUNTING_LOG_LEVEL (a level name, any case); an unknown name falls back to INFO and is
    returned so the caller can warn about it."""
    raw = os.getenv(LOG_LEVEL_ENV, "").strip()
    if not raw:
        return DEFAULT_LEVEL, None
    level = logging.getLevelNamesMapping().get(raw.upper())
    return (level, None) if level is not None else (DEFAULT_LEVEL, raw)


def configure_logging() -> logging.Logger:
    """Idempotent: sets the `app` logger's level from the environment and attaches the stderr handler once."""
    logger = logging.getLogger(APP_LOGGER)
    level, unknown = _level_from_env()
    logger.setLevel(level)
    if not any(handler.get_name() == HANDLER_NAME for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.set_name(HANDLER_NAME)
        handler.setFormatter(ExtraFormatter())
        logger.addHandler(handler)
    if unknown is not None:
        logger.warning("logging.unknown_level", extra={"env": LOG_LEVEL_ENV, "value": unknown,
                                                        "using": logging.getLevelName(DEFAULT_LEVEL)})
    return logger
