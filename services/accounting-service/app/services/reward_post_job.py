"""The reward posting daily job (reward engine spec 5.1; plan P7) and its in-process scheduler.

Lock: run() takes REWARD_POST_JOB_LOCK_KEY (session-level, on a dedicated connection) as auto_pay_job.run does — a
second runner (another process, an overlapping scheduler fire) returns `busy` and writes nothing.

Under the lock: the ids of the `estimated` accruals with `post_on <= today` are read once, in id order; each is then
posted in its own session and transaction (`reward_posting.post_due_accrual`, which re-checks the due date under the
window lock) and committed. One accrual that raises is rolled back, logged and counted `failed`; the run goes on.
An error outside the per-accrual step ends the run `crashed`.
Scheduler: 06:15 Asia/Taipei (after auto-pay's 06:00) and once 30 s after startup. A scheduled run that ends `busy` or
`crashed` adds a retry every RETRY_MINUTES for the rest of that Taipei day; the next run that ends otherwise removes
it, and a retry firing on a new day removes itself (the 06:15 run takes over).
Logs carry ids, counts and error classes only — never amounts or names.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from ..models import RewardAccrual
from . import ledger_service, reward_posting
from .auto_pay_job import _log_error
from .schedule_job import TZ_NAME, is_enabled
from .schedule_locks import REWARD_POST_JOB_LOCK_KEY

logger = logging.getLogger(__name__)

TRIGGERS = ("cron", "startup", "retry", "manual")
DAILY_JOB_ID = "reward_post_daily"
STARTUP_JOB_ID = "reward_post_startup"
RETRY_JOB_ID = "reward_post_retry"
RETRY_MINUTES = 10
RETRY_STATUSES = ("busy", "crashed")
STARTUP_DELAY_SEC = 30
COUNTS = (*reward_posting.RESULTS, "failed")

_scheduler: BackgroundScheduler | None = None


def _due_ids(factory, today: date) -> list[int]:
    with factory() as db:
        return list(db.scalars(
            select(RewardAccrual.id)
            .where(RewardAccrual.state == "estimated", RewardAccrual.post_on <= today)
            .order_by(RewardAccrual.id)
        ))


def _post_all(factory, today: date, report: dict) -> None:
    for accrual_id in _due_ids(factory, today):
        with factory() as db:
            try:
                result = reward_posting.post_due_accrual(db, accrual_id, today)
                db.commit()
            except Exception as exc:  # noqa: BLE001 — one broken accrual must not stop the others
                db.rollback()
                _log_error("reward_post_job.post_failed", exc, accrual_id=accrual_id)
                report["failed"] += 1
                continue
        report[result] += 1


def run(engine: Engine, trigger: str, *, today: date | None = None) -> dict:
    """Post every due accrual; returns {"status": "ok" | "busy" | "crashed", "posted", "forfeited", "skipped",
    "failed", "trigger", "today"}."""
    if trigger not in TRIGGERS:
        raise ValueError(f"unknown trigger {trigger!r}")
    today = today or ledger_service._today()
    report = {"trigger": trigger, "today": today.isoformat(), "status": "ok", **dict.fromkeys(COUNTS, 0)}
    with engine.connect() as lock_conn:
        acquired = lock_conn.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": REWARD_POST_JOB_LOCK_KEY}).scalar_one()
        lock_conn.commit()
        if not acquired:
            report["status"] = "busy"
        else:
            try:
                _post_all(sessionmaker(bind=engine, autoflush=False), today, report)
            except Exception as exc:  # noqa: BLE001 — reported as crashed; the scheduler retries
                _log_error("reward_post_job.crashed", exc, trigger=trigger)
                report["status"] = "crashed"
            finally:
                lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": REWARD_POST_JOB_LOCK_KEY})
                lock_conn.commit()
    logger.info("reward_post_job.run", extra={"trigger": trigger, "run_status": report["status"],
                                              **{key: report[key] for key in COUNTS}})
    return report


def _job(engine: Engine, trigger: str) -> None:
    started = ledger_service._today().isoformat()  # a crash keeps the day the run began, not the day it failed on
    try:
        report = run(engine, trigger)
    except Exception as exc:  # noqa: BLE001 — the scheduler thread must survive
        _log_error("reward_post_job.crashed", exc, trigger=trigger)
        report = {"status": "crashed", "today": started}
    _after_run(engine, report)


def _after_run(engine: Engine, report: dict, scheduler=None) -> None:
    """busy / crashed → retry every RETRY_MINUTES that Taipei day; any other end — or a run whose day is already over
    — clears the retry."""
    scheduler = scheduler if scheduler is not None else _scheduler
    if scheduler is None:
        return
    if report["status"] in RETRY_STATUSES and report["today"] == ledger_service._today().isoformat():
        scheduler.add_job(
            _retry, IntervalTrigger(minutes=RETRY_MINUTES, timezone=TZ_NAME), id=RETRY_JOB_ID, replace_existing=True,
            kwargs={"engine": engine, "day": report["today"]}, coalesce=True, max_instances=1,
        )
        logger.info("reward_post_job.retry_scheduled", extra={"run_status": report["status"], "day": report["today"],
                                                              "minutes": RETRY_MINUTES})
    elif scheduler.get_job(RETRY_JOB_ID) is not None:
        scheduler.remove_job(RETRY_JOB_ID)


def _retry(engine: Engine, day: str) -> None:
    if ledger_service._today().isoformat() != day:  # a new Taipei day: the 06:15 run takes over
        if _scheduler is not None and _scheduler.get_job(RETRY_JOB_ID) is not None:
            _scheduler.remove_job(RETRY_JOB_ID)
        return
    _job(engine, "retry")


def build_scheduler(engine: Engine) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=TZ_NAME)
    scheduler.add_job(
        _job, CronTrigger(hour=6, minute=15, timezone=TZ_NAME), id=DAILY_JOB_ID, replace_existing=True,
        kwargs={"engine": engine, "trigger": "cron"}, coalesce=True, max_instances=1, misfire_grace_time=3600,
    )
    scheduler.add_job(
        _job, DateTrigger(run_date=datetime.now(ZoneInfo(TZ_NAME)) + timedelta(seconds=STARTUP_DELAY_SEC)),
        id=STARTUP_JOB_ID, replace_existing=True, kwargs={"engine": engine, "trigger": "startup"},
    )
    return scheduler


def start(engine: Engine) -> None:
    """Start the scheduler unless ACCOUNTING_SCHEDULER_ENABLED (schedule_job.is_enabled) switches it off."""
    global _scheduler
    if not is_enabled():
        logger.info("reward_post_job.scheduler_disabled")
        return
    if _scheduler is not None:
        return
    try:
        _scheduler = build_scheduler(engine)
        _scheduler.start()
    except Exception as exc:  # noqa: BLE001 — the API stays up without the job
        _scheduler = None
        logger.exception("reward_post_job.scheduler_failed", extra={"error_class": exc.__class__.__name__})
        return
    logger.info("reward_post_job.scheduler_started", extra={"jobs": [job.id for job in _scheduler.get_jobs()]})


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("reward_post_job.scheduler_stopped")
