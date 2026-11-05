"""Scheduler wiring of the reward posting job (spec 5.1; plan P7); no database."""

from datetime import date

import pytest
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.services import reward_post_job, schedule_locks
from tests.unit.test_schedule_job_scheduler import FakeScheduler


def test_scheduler_triggers():
    jobs = {j.id: j for j in reward_post_job.build_scheduler(engine=object()).get_jobs()}
    assert set(jobs) == {"reward_post_daily", "reward_post_startup"}
    daily = jobs["reward_post_daily"]
    assert str(daily.trigger) == "cron[hour='6', minute='15']" and str(daily.trigger.timezone) == "Asia/Taipei"
    assert (daily.coalesce, daily.max_instances, daily.misfire_grace_time) == (True, 1, 3600)
    assert daily.kwargs["trigger"] == "cron"
    startup = jobs["reward_post_startup"]
    assert isinstance(startup.trigger, DateTrigger) and startup.kwargs["trigger"] == "startup"
    assert reward_post_job.STARTUP_DELAY_SEC == 30


def test_constants():
    assert schedule_locks.REWARD_POST_JOB_LOCK_KEY == 0x52574450
    assert reward_post_job.TRIGGERS == ("cron", "startup", "retry", "manual")
    assert (reward_post_job.RETRY_JOB_ID, reward_post_job.RETRY_MINUTES, reward_post_job.RETRY_STATUSES) == (
        "reward_post_retry", 10, ("busy", "crashed"))


def test_run_refuses_an_unknown_trigger():
    with pytest.raises(ValueError):
        reward_post_job.run(object(), "nightly")


@pytest.mark.parametrize("status", ["busy", "crashed"])
def test_after_run_adds_the_retry_and_any_other_end_clears_it(status, monkeypatch):
    monkeypatch.setattr(reward_post_job.ledger_service, "_today", lambda: date(2027, 1, 5))
    fake = FakeScheduler()
    reward_post_job._after_run(object(), {"status": status, "today": "2027-01-05"}, scheduler=fake)
    retry = fake.jobs["reward_post_retry"]
    assert retry["func"] is reward_post_job._retry and retry["kwargs"]["day"] == "2027-01-05"
    assert isinstance(retry["trigger"], IntervalTrigger) and retry["trigger"].interval.total_seconds() == 600
    assert (retry["coalesce"], retry["max_instances"]) == (True, 1)
    reward_post_job._after_run(object(), {"status": "ok", "today": "2027-01-05"}, scheduler=fake)
    assert fake.removed == ["reward_post_retry"] and fake.jobs == {}


def test_after_run_never_schedules_for_a_day_that_is_over(monkeypatch):
    monkeypatch.setattr(reward_post_job.ledger_service, "_today", lambda: date(2027, 1, 6))
    fake = FakeScheduler()
    fake.jobs["reward_post_retry"] = {}
    reward_post_job._after_run(object(), {"status": "busy", "today": "2027-01-05"}, scheduler=fake)
    assert fake.jobs == {} and fake.removed == ["reward_post_retry"]


def test_a_crashed_scheduled_run_schedules_the_retry(monkeypatch):
    fake = FakeScheduler()
    monkeypatch.setattr(reward_post_job, "_scheduler", fake)
    monkeypatch.setattr(reward_post_job.ledger_service, "_today", lambda: date(2027, 1, 5))

    def boom(engine, trigger):
        raise RuntimeError("fictional")
    monkeypatch.setattr(reward_post_job, "run", boom)
    reward_post_job._job(object(), "cron")
    assert fake.jobs["reward_post_retry"]["kwargs"]["day"] == "2027-01-05"


def test_retry_stops_on_a_new_taipei_day(monkeypatch):
    fake = FakeScheduler()
    fake.jobs["reward_post_retry"] = {}
    monkeypatch.setattr(reward_post_job, "_scheduler", fake)
    monkeypatch.setattr(reward_post_job.ledger_service, "_today", lambda: date(2027, 1, 6))
    monkeypatch.setattr(reward_post_job, "run", lambda *args, **kwargs: pytest.fail("no run on a new day"))
    reward_post_job._retry(object(), "2027-01-05")
    assert fake.removed == ["reward_post_retry"]


def test_retry_on_the_same_day_runs_with_the_retry_trigger(monkeypatch):
    fake, seen = FakeScheduler(), []
    fake.jobs["reward_post_retry"] = {}
    monkeypatch.setattr(reward_post_job, "_scheduler", fake)
    monkeypatch.setattr(reward_post_job.ledger_service, "_today", lambda: date(2027, 1, 5))
    monkeypatch.setattr(reward_post_job, "run", lambda engine, trigger: seen.append(trigger) or {
        "status": "ok", "today": "2027-01-05"})
    reward_post_job._retry(object(), "2027-01-05")
    assert seen == ["retry"] and fake.removed == ["reward_post_retry"]


def test_start_does_nothing_while_the_switch_is_off(monkeypatch):
    monkeypatch.setenv("ACCOUNTING_SCHEDULER_ENABLED", "false")
    reward_post_job.start(object())
    assert reward_post_job._scheduler is None
    reward_post_job.stop()  # a no-op without a scheduler
