"""#190: the app.* loggers reach stderr with their extra= fields, at a level the environment sets."""

import io
import logging

import pytest

from app import logging_setup


@pytest.fixture
def app_logger(monkeypatch):
    # In a full run alembic's fileConfig (the integration session's in-process upgrade) has disabled the loggers that
    # existed before it; the API process never runs it, so re-enable the ones these tests log through.
    for name in (logging_setup.APP_LOGGER, "app.services.auto_pay_job", "app.services.schedule_job"):
        monkeypatch.setattr(logging.getLogger(name), "disabled", False)
    logger = logging.getLogger(logging_setup.APP_LOGGER)
    saved_level, saved_handlers = logger.level, list(logger.handlers)
    logger.handlers = [h for h in logger.handlers if h.get_name() != logging_setup.HANDLER_NAME]
    yield logger
    logger.handlers = saved_handlers
    logger.setLevel(saved_level)


def _ours(logger):
    return [h for h in logger.handlers if h.get_name() == logging_setup.HANDLER_NAME]


def _capture(logger):
    stream = io.StringIO()
    _ours(logger)[0].setStream(stream)
    return stream


def test_one_handler_however_often_it_is_configured(app_logger, monkeypatch):
    monkeypatch.delenv(logging_setup.LOG_LEVEL_ENV, raising=False)
    logging_setup.configure_logging()
    logging_setup.configure_logging()
    assert len(_ours(app_logger)) == 1 and app_logger.level == logging.INFO


@pytest.mark.parametrize(("value", "level"), [("debug", logging.DEBUG), ("WARNING", logging.WARNING),
                                              (" error ", logging.ERROR), ("", logging.INFO)])
def test_level_comes_from_the_environment(app_logger, monkeypatch, value, level):
    monkeypatch.setenv(logging_setup.LOG_LEVEL_ENV, value)
    logging_setup.configure_logging()
    assert app_logger.level == level


def test_unknown_level_falls_back_to_info_and_says_so(app_logger, monkeypatch):
    monkeypatch.setenv(logging_setup.LOG_LEVEL_ENV, "chatty")
    logging_setup.configure_logging()
    assert app_logger.level == logging.INFO
    stream = _capture(app_logger)
    logging_setup.configure_logging()
    line = stream.getvalue()
    assert "WARNING app logging.unknown_level" in line and "value=chatty" in line and "using=INFO" in line


def test_child_logger_info_reaches_stderr_with_its_extras(app_logger, monkeypatch):
    monkeypatch.delenv(logging_setup.LOG_LEVEL_ENV, raising=False)
    logging_setup.configure_logging()
    stream = _capture(app_logger)
    logging.getLogger("app.services.auto_pay_job").info(
        "auto_pay_job.run",
        extra={"trigger": "startup", "run_status": "ok", "decisions": {"booked": [7, 9]}, "note": "two words"})
    logging.getLogger("app.services.auto_pay_job").debug("auto_pay_job.hidden")
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    assert lines[0].endswith(' INFO app.services.auto_pay_job auto_pay_job.run decisions={"booked": [7, 9]} '
                             'note="two words" run_status=ok trigger=startup')


def test_multi_line_values_stay_on_one_line_and_otel_fields_are_dropped():
    record = logging.LogRecord("app.x", logging.ERROR, __file__, 1, "x.crashed", None, None)
    record.frames = 'File "a.py", line 1\n  boom()\n'
    record.otelTraceID = "0" * 32
    line = logging_setup.ExtraFormatter().format(record)
    assert "\n" not in line and "otelTraceID" not in line
    assert line.endswith('x.crashed frames="File \\"a.py\\", line 1\\n  boom()\\n"')


def test_records_still_propagate_to_root_handlers(app_logger, monkeypatch):
    """An OpenTelemetry handler sits on the root logger; configure_logging must not cut it off."""
    monkeypatch.delenv(logging_setup.LOG_LEVEL_ENV, raising=False)
    logging_setup.configure_logging()
    _capture(app_logger)
    seen = []

    class Collect(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())
    root_handler = Collect()
    logging.getLogger().addHandler(root_handler)
    try:
        logging.getLogger("app.services.schedule_job").info("schedule_job.run")
    finally:
        logging.getLogger().removeHandler(root_handler)
    assert seen == ["schedule_job.run"]
