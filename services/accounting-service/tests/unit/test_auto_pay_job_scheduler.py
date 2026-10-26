"""Scheduler wiring and CLI parsing of the auto-pay job (auto-pay spec §4.4); no database."""

from datetime import date

from apscheduler.triggers.date import DateTrigger

from app.services import auto_pay_job


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
    assert auto_pay_job.TRIGGERS == ("cron", "startup", "manual")


def test_main_parses_as_of_and_dry_run(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(auto_pay_job, "run", lambda engine, trigger, **kw: seen.update(kw, trigger=trigger) or {
        "status": "dry_run", "decisions": []})
    assert auto_pay_job.main(["--dry-run", "--as-of", "2026-10-27"], engine=object()) == 0
    assert seen == {"trigger": "manual", "today": date(2026, 10, 27), "dry_run": True}


def test_main_exit_codes(monkeypatch, capsys):
    for status, code in (("completed", 0), ("dry_run", 0), ("disabled", 0), ("busy", 1), ("import_running", 1)):
        monkeypatch.setattr(auto_pay_job, "run", lambda engine, trigger, status=status, **kw: {"status": status})
        assert auto_pay_job.main([], engine=object()) == code, status


def test_start_does_nothing_while_the_switch_is_off(monkeypatch):
    monkeypatch.setenv("ACCOUNTING_SCHEDULER_ENABLED", "false")
    auto_pay_job.start(object())
    assert auto_pay_job._scheduler is None
    auto_pay_job.stop()  # a no-op without a scheduler
