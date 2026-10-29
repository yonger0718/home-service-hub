"""Scheduler wiring and CLI parsing of the auto-pay job (auto-pay spec §4.4); no database."""

from datetime import date

import pytest
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.services import auto_pay_job
from tests.unit.test_schedule_job_scheduler import FakeScheduler


def test_build_scheduler_registers_daily_and_startup_runs():
    jobs = {j.id: j for j in auto_pay_job.build_scheduler(engine=object()).get_jobs()}
    assert set(jobs) == {"auto_pay_daily", "auto_pay_startup"}
    daily = jobs["auto_pay_daily"]
    assert str(daily.trigger) == "cron[hour='6', minute='0']" and str(daily.trigger.timezone) == "Asia/Taipei"
    assert (daily.coalesce, daily.max_instances, daily.misfire_grace_time) == (True, 1, 3600)
    assert daily.kwargs["trigger"] == "cron"
    startup = jobs["auto_pay_startup"]
    assert isinstance(startup.trigger, DateTrigger) and startup.kwargs["trigger"] == "startup"


def test_constants():
    assert (auto_pay_job.DAILY_JOB_ID, auto_pay_job.STARTUP_JOB_ID, auto_pay_job.STARTUP_DELAY_SEC) == (
        "auto_pay_daily", "auto_pay_startup", 20)
    assert auto_pay_job.TRIGGERS == ("cron", "startup", "retry", "manual")
    assert (auto_pay_job.RETRY_JOB_ID, auto_pay_job.RETRY_MINUTES, auto_pay_job.RETRY_STATUSES) == (
        "auto_pay_retry", 10, ("busy", "import_running", "crashed"))


@pytest.mark.parametrize("status", ["busy", "import_running", "crashed"])
def test_after_run_adds_the_retry_and_any_other_end_clears_it(status, monkeypatch):
    monkeypatch.setattr(auto_pay_job.ledger_service, "_today", lambda: date(2026, 10, 27))
    fake = FakeScheduler()
    auto_pay_job._after_run(object(), {"status": status, "today": "2026-10-27"}, scheduler=fake)
    retry = fake.jobs["auto_pay_retry"]
    assert retry["func"] is auto_pay_job._retry and retry["kwargs"]["day"] == "2026-10-27"
    assert isinstance(retry["trigger"], IntervalTrigger) and retry["trigger"].interval.total_seconds() == 600
    assert (retry["coalesce"], retry["max_instances"]) == (True, 1)
    auto_pay_job._after_run(object(), {"status": "disabled", "today": "2026-10-27"}, scheduler=fake)
    assert fake.removed == ["auto_pay_retry"] and fake.jobs == {}


def test_after_run_without_a_scheduler_does_nothing(monkeypatch):
    monkeypatch.setattr(auto_pay_job, "_scheduler", None)
    auto_pay_job._after_run(object(), {"status": "busy", "today": "2026-10-27"})  # the CLI path: no retry


def test_a_crashed_scheduled_run_schedules_the_retry(monkeypatch):
    fake = FakeScheduler()
    monkeypatch.setattr(auto_pay_job, "_scheduler", fake)
    monkeypatch.setattr(auto_pay_job.ledger_service, "_today", lambda: date(2026, 10, 27))

    def boom(engine, trigger):
        raise RuntimeError("fictional")
    monkeypatch.setattr(auto_pay_job, "run", boom)
    auto_pay_job._job(object(), "cron")
    assert fake.jobs["auto_pay_retry"]["kwargs"]["day"] == "2026-10-27"


def test_after_run_never_schedules_for_a_day_that_is_over(monkeypatch):
    """A run that began before midnight and ended busy after it: no retry for the new day, an old one is removed."""
    monkeypatch.setattr(auto_pay_job.ledger_service, "_today", lambda: date(2026, 10, 28))
    fake = FakeScheduler()
    fake.jobs["auto_pay_retry"] = {}
    auto_pay_job._after_run(object(), {"status": "busy", "today": "2026-10-27"}, scheduler=fake)
    assert fake.jobs == {} and fake.removed == ["auto_pay_retry"]


def test_a_retry_that_crashes_after_midnight_does_not_carry_into_the_new_day(monkeypatch):
    """AGENT-325: the crash report keeps the day the run began, so a 23:5x retry failing at 00:0x stops retrying."""
    days = iter([date(2026, 10, 27), date(2026, 10, 27), date(2026, 10, 28)])  # _retry check, _job start, _after_run
    monkeypatch.setattr(auto_pay_job.ledger_service, "_today", lambda: next(days))
    fake = FakeScheduler()
    fake.jobs["auto_pay_retry"] = {}
    monkeypatch.setattr(auto_pay_job, "_scheduler", fake)

    def boom(engine, trigger):
        raise RuntimeError("fictional")
    monkeypatch.setattr(auto_pay_job, "run", boom)
    auto_pay_job._retry(object(), "2026-10-27")
    assert fake.jobs == {} and fake.removed == ["auto_pay_retry"]


def test_retry_stops_on_a_new_taipei_day(monkeypatch):
    fake = FakeScheduler()
    fake.jobs["auto_pay_retry"] = {}
    monkeypatch.setattr(auto_pay_job, "_scheduler", fake)
    monkeypatch.setattr(auto_pay_job.ledger_service, "_today", lambda: date(2026, 10, 28))
    monkeypatch.setattr(auto_pay_job, "run", lambda *args, **kwargs: pytest.fail("no run on a new day"))
    auto_pay_job._retry(object(), "2026-10-27")
    assert fake.removed == ["auto_pay_retry"]


def test_retry_on_the_same_day_runs_with_the_retry_trigger(monkeypatch):
    fake, seen = FakeScheduler(), []
    fake.jobs["auto_pay_retry"] = {}
    monkeypatch.setattr(auto_pay_job, "_scheduler", fake)
    monkeypatch.setattr(auto_pay_job.ledger_service, "_today", lambda: date(2026, 10, 27))
    monkeypatch.setattr(auto_pay_job, "run", lambda engine, trigger: seen.append(trigger) or {
        "status": "completed", "today": "2026-10-27"})
    auto_pay_job._retry(object(), "2026-10-27")
    assert seen == ["retry"] and fake.removed == ["auto_pay_retry"]


def test_main_parses_as_of_and_dry_run(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(auto_pay_job, "run", lambda engine, trigger, **kw: seen.update(kw, trigger=trigger) or {
        "status": "dry_run", "decisions": []})
    assert auto_pay_job.main(["--dry-run", "--as-of", "2026-10-27"], engine=object()) == 0
    assert seen == {"trigger": "manual", "today": date(2026, 10, 27), "dry_run": True}


def test_main_refuses_a_future_as_of_unless_dry_run(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(auto_pay_job.ledger_service, "_today", lambda: date(2026, 10, 27))
    monkeypatch.setattr(auto_pay_job, "run", lambda engine, trigger, **kw: calls.append(kw) or {"status": "completed"})
    capsys.readouterr()
    assert auto_pay_job.main(["--as-of", "2026-10-28"], engine=object()) == 2
    out, err = capsys.readouterr()
    assert calls == [] and out == ""
    assert len(err.strip().splitlines()) == 1 and "2026-10-28" in err
    assert auto_pay_job.main(["--as-of", "2026-10-28", "--dry-run"], engine=object()) == 0  # a dry run may look ahead
    assert auto_pay_job.main(["--as-of", "2026-10-27"], engine=object()) == 0  # today itself is fine
    assert [kw["today"] for kw in calls] == [date(2026, 10, 28), date(2026, 10, 27)]


def test_main_exit_codes(monkeypatch, capsys):
    for status, code in (("completed", 0), ("dry_run", 0), ("disabled", 0), ("busy", 1), ("import_running", 1)):
        monkeypatch.setattr(auto_pay_job, "run", lambda engine, trigger, status=status, **kw: {"status": status})
        assert auto_pay_job.main([], engine=object()) == code, status


def test_start_does_nothing_while_the_switch_is_off(monkeypatch):
    monkeypatch.setenv("ACCOUNTING_SCHEDULER_ENABLED", "false")
    auto_pay_job.start(object())
    assert auto_pay_job._scheduler is None
    auto_pay_job.stop()  # a no-op without a scheduler
