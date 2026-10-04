"""Scheduler wiring of the schedule job (design D34); no database."""

from datetime import date

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.services import ledger_service
from app.services import schedule_job


class FakeScheduler:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.removed: list[str] = []

    def add_job(self, func, trigger, id, replace_existing=False, **kwargs):  # noqa: A002 (APScheduler's keyword)
        self.jobs[id] = {"func": func, "trigger": trigger, **kwargs}

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def remove_job(self, job_id):
        self.removed.append(job_id)
        self.jobs.pop(job_id, None)


def test_scheduler_switch(monkeypatch):
    monkeypatch.delenv("ACCOUNTING_SCHEDULER_ENABLED", raising=False)
    assert schedule_job.is_enabled() is True
    for value in ("false", "0", "no", " FALSE "):
        monkeypatch.setenv("ACCOUNTING_SCHEDULER_ENABLED", value)
        assert schedule_job.is_enabled() is False


def test_build_scheduler_registers_the_daily_and_startup_runs():
    scheduler = schedule_job.build_scheduler(engine=object())
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {"schedule_daily", "schedule_startup"}
    daily = jobs["schedule_daily"]
    assert isinstance(daily.trigger, CronTrigger)
    assert str(daily.trigger) == "cron[hour='0', minute='5']"
    assert str(daily.trigger.timezone) == "Asia/Taipei"
    assert (daily.coalesce, daily.max_instances, daily.misfire_grace_time) == (True, 1, 3600)
    assert daily.kwargs["trigger"] == "cron" and jobs["schedule_startup"].kwargs["trigger"] == "startup"


def test_after_run_adds_and_clears_the_retry():
    fake = FakeScheduler()
    schedule_job._after_run(object(), {"status": "import_running", "today": "2026-10-09"}, scheduler=fake)
    retry = fake.jobs["schedule_retry"]
    assert isinstance(retry["trigger"], IntervalTrigger) and retry["trigger"].interval.total_seconds() == 600
    assert retry["kwargs"]["day"] == "2026-10-09"
    schedule_job._after_run(object(), {"status": "completed", "today": "2026-10-09"}, scheduler=fake)
    assert fake.removed == ["schedule_retry"]


def test_retry_stops_on_a_new_taipei_day(monkeypatch):
    fake = FakeScheduler()
    fake.jobs["schedule_retry"] = {}
    monkeypatch.setattr(schedule_job, "_scheduler", fake)
    monkeypatch.setattr(ledger_service, "_today", lambda: date(2026, 10, 10))
    monkeypatch.setattr(schedule_job, "run", lambda *args, **kwargs: pytest.fail("no run on a new day"))
    schedule_job._retry(object(), "2026-10-09")
    assert fake.removed == ["schedule_retry"]


def test_trigger_after_import_needs_a_running_scheduler(monkeypatch):
    monkeypatch.setattr(schedule_job, "_scheduler", None)
    schedule_job.trigger_after_import(object())  # no scheduler (tests, disabled): nothing happens
    fake = FakeScheduler()
    monkeypatch.setattr(schedule_job, "_scheduler", fake)
    schedule_job.trigger_after_import(object())
    assert fake.jobs["schedule_import"]["kwargs"]["trigger"] == "import"


@pytest.mark.parametrize(("value", "post"), [(None, True), ("true", True), ("false", False), ("0", False), ("no", False)])
def test_cli_import_job_posts_only_when_the_scheduler_switch_is_on(monkeypatch, value, post):
    # Multica R-F4: the importer CLI runs the job inline; posting follows ACCOUNTING_SCHEDULER_ENABLED (same parsing).
    if value is None:
        monkeypatch.delenv("ACCOUNTING_SCHEDULER_ENABLED", raising=False)
    else:
        monkeypatch.setenv("ACCOUNTING_SCHEDULER_ENABLED", value)
    calls = []
    monkeypatch.setattr(schedule_job, "run", lambda engine, trigger, **kwargs: calls.append((trigger, kwargs)) or {})
    schedule_job.run_after_cli_import(object(), today=date(2026, 10, 9))
    assert calls == [("import", {"today": date(2026, 10, 9), "post": post})]
