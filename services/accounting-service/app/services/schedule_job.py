"""The daily schedule job (design D34) and its in-process scheduler.

run() takes the job advisory lock on a dedicated connection (a second runner returns busy; the lock, not the
scheduler, guarantees a single runner, so the CLI or a second process is safe), generates instances for every
definition in its own transaction, then posts each due auto instance in its own transaction (acted_by auto).
Logs carry ids, counts and error classes only.

CLI: python -m app.services.schedule_job [--dry-run]
"""

import argparse
import json
import logging
import os
import sys
import traceback
from datetime import date, datetime, timedelta
from typing import Sequence
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..models import ScheduleDefinition, ScheduleInstance
from . import ledger_service
from . import schedule_generation as generation
from . import schedule_posting as posting
from .errors import ConflictError, NotFoundError, ValidationError
from .schedule_locks import SCHEDULE_JOB_LOCK_KEY, ImportRunningError, take_import_key_shared

logger = logging.getLogger(__name__)

TZ_NAME = "Asia/Taipei"
TRIGGERS = ("cron", "startup", "retry", "import", "manual")
DAILY_JOB_ID = "schedule_daily"
STARTUP_JOB_ID = "schedule_startup"
RETRY_JOB_ID = "schedule_retry"
IMPORT_JOB_ID = "schedule_import"
STARTUP_DELAY_SEC = 10
RETRY_MINUTES = 10
RETRY_STATUSES = ("busy", "import_running", "crashed")
DEFINITION_CHANGED = "definition_changed"  # posting.lock_definition_for_post: retried once, as post_sequence does

_scheduler: BackgroundScheduler | None = None


def is_enabled() -> bool:
    return os.getenv("ACCOUNTING_SCHEDULER_ENABLED", "true").strip().lower() not in {"false", "0", "no"}


def due_instances(db: Session, today: date) -> list[tuple[int, int]]:
    """(instance id, definition id) the job may post today, in posting order: per definition, in seq order (D31,
    R-F6) — a period the owner moved later is still attempted before the next seq."""
    rows = db.execute(
        select(ScheduleInstance.id, ScheduleInstance.definition_id)
        .join(ScheduleDefinition, ScheduleDefinition.id == ScheduleInstance.definition_id)
        .where(
            ScheduleInstance.status == "pending",
            ScheduleInstance.due_date <= today,
            ScheduleInstance.due_date >= ScheduleDefinition.auto_post_from,
            ScheduleInstance.reopened_at.is_(None),
            ScheduleDefinition.status == "active",
            ScheduleDefinition.posting_mode == "auto",
        )
        .order_by(ScheduleInstance.definition_id, ScheduleInstance.seq)
    )
    return [(instance_id, definition_id) for instance_id, definition_id in rows]


def blocked_by_earlier(db: Session, definition: ScheduleDefinition, instance_id: int, today: date) -> bool:
    """D31 / Multica R-F6: within one definition periods are attempted in seq order. A period waits while an earlier
    seq of the same definition is pending and either due for the job (auto_eligible) or failed (last_error) — also
    when the owner moved that earlier period's due_date past this one's. Pre-auto_post_from and reopened periods are
    the owner's (D34) and do not hold the series ONLY while they carry no last_error: the exception (ruling R4) is
    such a period with last_error (an owner post of it failed) — it holds every later period until the owner posts
    or skips it, as test_pre_auto_post_from_period_with_error_blocks_the_series and
    test_a_failed_earlier_period_the_job_does_not_own_still_holds_the_series pin. Called under the definition lock (lock_definition_for_post): an
    owner action on the earlier period is either committed (seen) or not yet (seen as pending: this one waits)."""
    instance = db.get(ScheduleInstance, instance_id)
    if instance is None:
        return False  # deleted meanwhile: post_instance answers NotFoundError
    earlier = db.scalars(
        select(ScheduleInstance).where(
            ScheduleInstance.definition_id == definition.id,
            ScheduleInstance.status == "pending",
            ScheduleInstance.seq < instance.seq,
        )
    )
    return any(posting.auto_eligible(definition, row, today) or row.last_error is not None for row in earlier)


def _post_job_one(db: Session, instance_id: int, definition_id: int, today: date) -> posting.PostResult:
    """One job posting transaction: shared key → definition (lock_definition_for_post, the strength post_instance
    takes next) → the sequence barrier → post_instance (instance FOR UPDATE SKIP LOCKED, eligibility re-checked)."""
    take_import_key_shared(db)
    definition = posting.lock_definition_for_post(db, definition_id)
    if blocked_by_earlier(db, definition, instance_id, today):
        return posting.PostResult(instance_id, "blocked")
    return posting.post_instance(db, instance_id, actor="auto", job=True, today=today)


def _new_report(trigger: str, today: date) -> dict:
    return {
        "trigger": trigger, "today": today.isoformat(), "status": "completed", "generated": 0,
        "generation_failed": [], "posted": [], "failed": [], "stopped_definitions": [],
    }


def _log_error(event: str, exc: BaseException, **ids) -> None:
    """ERROR with ids, the error class, the traceback frames and the classes of the cause / context chain — never the
    message: a driver's text (e.g. DETAIL "Failing row contains (...)") can carry amounts and names. The full exception
    goes to DEBUG only, for local debugging."""
    chain: list[str] = []
    seen = {id(exc)}
    link = exc.__cause__ or exc.__context__
    while link is not None and id(link) not in seen:
        seen.add(id(link))
        chain.append(link.__class__.__name__)
        link = link.__cause__ or link.__context__
    logger.error(
        event,
        extra={
            **ids, "error_class": exc.__class__.__name__, "error_chain": chain,
            "frames": "".join(traceback.format_tb(exc.__traceback__)),
        },
    )
    logger.debug(event, exc_info=exc)


def _definition_ids(db: Session) -> list[int]:
    return list(
        db.scalars(select(ScheduleDefinition.id).where(ScheduleDefinition.status != "ended").order_by(ScheduleDefinition.id))
    )


def _generate_all(factory, today: date, report: dict) -> bool:
    """One transaction per definition; False when an import stopped the run. Any other error rolls back that
    definition only (generation_failed, logged); the run goes on with the next one and then posts."""
    with factory() as db:
        definition_ids = _definition_ids(db)
    for definition_id in definition_ids:
        with factory() as db:
            try:
                report["generated"] += generation.generate_locked(db, definition_id, today)
                db.commit()
            except ImportRunningError:
                db.rollback()
                report["status"] = "import_running"
                return False
            except NotFoundError:
                db.rollback()  # deleted since the id list was read
            except Exception as exc:  # noqa: BLE001 — one broken definition must not stop the other schedules
                db.rollback()
                _log_error("schedule_job.generation_error", exc, definition_id=definition_id)
                report["generation_failed"].append(definition_id)
    return True


def _post_due(factory, today: date, report: dict) -> None:
    with factory() as db:
        due = due_instances(db, today)
    stopped: set[int] = set()
    for instance_id, definition_id in due:
        if definition_id in stopped:
            continue  # a loan's periods post in order: a failure stops the later ones for this run
        with factory() as db:
            try:
                try:
                    result = _post_job_one(db, instance_id, definition_id, today)
                except ConflictError as exc:
                    if str(exc) != DEFINITION_CHANGED:
                        raise
                    db.rollback()  # the template gained a loan line meanwhile: once more, now FOR UPDATE (as Task 11)
                    result = _post_job_one(db, instance_id, definition_id, today)
                db.commit()
            except ImportRunningError:
                db.rollback()
                report["status"] = "import_running"
                break
            except ValidationError as exc:
                posting.record_failure(db, instance_id, posting.failure_message(exc))
                report["failed"].append(instance_id)
                stopped.add(definition_id)
                continue
            except (ConflictError, NotFoundError):
                db.rollback()  # the owner posted or skipped it meanwhile, or end / a definition PUT deleted it
                continue
            except Exception as exc:  # noqa: BLE001 — one broken period must not stop the other schedules
                # EditLockedError lands here on purpose: the job posts with check_cutover_lock=False, so a cutover
                # refusal means a period entry is a MOZE row — recorded as a failure that stops the definition.
                db.rollback()
                _log_error("schedule_job.post_error", exc, instance_id=instance_id)
                posting.record_failure(db, instance_id, posting.failure_message(exc))
                report["failed"].append(instance_id)
                stopped.add(definition_id)
                continue
            if result.outcome == "posted":
                report["posted"].append(instance_id)
            elif result.outcome == "blocked":
                stopped.add(definition_id)  # an earlier seq is pending and due, or failed: nothing recorded
    report["stopped_definitions"] = sorted(stopped)


def _dry_run(factory, today: date, report: dict) -> None:
    """The same steps inside savepoints of one session, rolled back at the end: nothing is written."""
    with factory() as db:
        try:
            for definition_id in _definition_ids(db):
                savepoint = db.begin_nested()
                try:
                    report["generated"] += generation.generate_locked(db, definition_id, today)
                    savepoint.commit()
                except ImportRunningError:
                    savepoint.rollback()
                    report["status"] = "import_running"
                    return
                except Exception:  # noqa: BLE001 — as _generate_all: that definition only
                    savepoint.rollback()
                    report["generation_failed"].append(definition_id)
            stopped: set[int] = set()
            for instance_id, definition_id in due_instances(db, today):
                if definition_id in stopped:
                    continue
                savepoint = db.begin_nested()
                try:
                    result = _post_job_one(db, instance_id, definition_id, today)
                    savepoint.commit()
                except ImportRunningError:
                    savepoint.rollback()
                    report["status"] = "import_running"
                    break
                except (ValidationError, ConflictError):
                    savepoint.rollback()
                    report["failed"].append(instance_id)
                    stopped.add(definition_id)
                    continue
                if result.outcome == "posted":
                    report["posted"].append(instance_id)
                elif result.outcome == "blocked":
                    stopped.add(definition_id)
            report["stopped_definitions"] = sorted(stopped)
        finally:
            db.rollback()
    if report["status"] == "completed":
        report["status"] = "dry_run"


def run(engine: Engine, trigger: str, *, today: date | None = None, dry_run: bool = False, post: bool = True) -> dict:
    """post=False (R-F4, the importer CLI with the scheduler switched off): generation only, then the count of due
    but unposted instances (report["due_unposted"], logged)."""
    if trigger not in TRIGGERS:
        raise ValueError(f"unknown trigger {trigger!r}")
    today = today or ledger_service._today()
    report = _new_report(trigger, today)
    with engine.connect() as lock_conn:
        acquired = lock_conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": SCHEDULE_JOB_LOCK_KEY}).scalar_one()
        lock_conn.commit()
        if not acquired:
            report["status"] = "busy"
        else:
            try:
                factory = sessionmaker(bind=engine, autoflush=False)
                if dry_run:
                    _dry_run(factory, today, report)
                elif _generate_all(factory, today, report):
                    if post:
                        _post_due(factory, today, report)
                    else:
                        with factory() as db:
                            report["due_unposted"] = len(due_instances(db, today))
                        logger.info("schedule_job.due_unposted", extra={"count": report["due_unposted"]})
            finally:
                lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": SCHEDULE_JOB_LOCK_KEY})
                lock_conn.commit()
    logger.info(
        "schedule_job.run",
        extra={
            "trigger": trigger, "run_status": report["status"], "generated": report["generated"],
            "generation_failed": report["generation_failed"],
            "posted_ids": report["posted"], "failed_ids": report["failed"],
            "stopped_definitions": report["stopped_definitions"],
        },
    )
    return report


def _job(engine: Engine, trigger: str) -> None:
    try:
        report = run(engine, trigger)
    except Exception as exc:  # noqa: BLE001 — the scheduler thread must survive
        _log_error("schedule_job.crashed", exc, trigger=trigger)
        report = {"status": "crashed", "today": ledger_service._today().isoformat()}
    _after_run(engine, report)


def _after_run(engine: Engine, report: dict, scheduler=None) -> None:
    """busy / import_running / crashed → retry every 10 minutes that Taipei day; a completed run clears the retry."""
    scheduler = scheduler if scheduler is not None else _scheduler
    if scheduler is None:
        return
    if report["status"] in RETRY_STATUSES:
        scheduler.add_job(
            _retry, IntervalTrigger(minutes=RETRY_MINUTES, timezone=TZ_NAME), id=RETRY_JOB_ID, replace_existing=True,
            kwargs={"engine": engine, "day": report["today"]}, coalesce=True, max_instances=1,
        )
    elif scheduler.get_job(RETRY_JOB_ID) is not None:
        scheduler.remove_job(RETRY_JOB_ID)


def _retry(engine: Engine, day: str) -> None:
    if ledger_service._today().isoformat() != day:  # a new Taipei day: the 00:05 run takes over
        if _scheduler is not None and _scheduler.get_job(RETRY_JOB_ID) is not None:
            _scheduler.remove_job(RETRY_JOB_ID)
        return
    _job(engine, "retry")


def build_scheduler(engine: Engine) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=TZ_NAME)
    scheduler.add_job(
        _job, CronTrigger(hour=0, minute=5, timezone=TZ_NAME), id=DAILY_JOB_ID, replace_existing=True,
        kwargs={"engine": engine, "trigger": "cron"}, coalesce=True, max_instances=1, misfire_grace_time=3600,
    )
    scheduler.add_job(
        _job, DateTrigger(run_date=datetime.now(ZoneInfo(TZ_NAME)) + timedelta(seconds=STARTUP_DELAY_SEC)),
        id=STARTUP_JOB_ID, replace_existing=True, kwargs={"engine": engine, "trigger": "startup"},
    )
    return scheduler


def start(engine: Engine) -> None:
    global _scheduler
    if not is_enabled():
        logger.info("schedule_job.scheduler_disabled")
        return
    if _scheduler is not None:
        return
    try:
        _scheduler = build_scheduler(engine)
        _scheduler.start()
    except Exception as exc:  # noqa: BLE001 — the API stays up without the job; run-now still works
        _scheduler = None
        logger.exception("schedule_job.scheduler_failed", extra={"error_class": exc.__class__.__name__})
        return
    logger.info("schedule_job.scheduler_started", extra={"jobs": [job.id for job in _scheduler.get_jobs()]})


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("schedule_job.scheduler_stopped")


def trigger_after_import(engine: Engine) -> None:
    """A run right after a real backup import, once the import has released its advisory lock (D34)."""
    if _scheduler is None:
        return
    _scheduler.add_job(
        _job, DateTrigger(run_date=datetime.now(ZoneInfo(TZ_NAME))), id=IMPORT_JOB_ID, replace_existing=True,
        kwargs={"engine": engine, "trigger": "import"},
    )


def run_after_cli_import(engine: Engine, *, today: date | None = None) -> dict:
    """Multica R-F4: the standalone importer CLI has no scheduler, so trigger_after_import would do nothing. Once the
    CLI import has released the import lock it runs the job inline in its own process: generation always; posting
    only when ACCOUNTING_SCHEDULER_ENABLED is truthy (is_enabled, the scheduler's own parsing); otherwise the run logs
    how many instances are due but unposted. The API path keeps trigger_after_import."""
    return run(engine, "import", today=today, post=is_enabled())


def main(argv: Sequence[str] | None = None, *, engine: Engine | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.services.schedule_job")
    parser.add_argument("--dry-run", action="store_true", help="print what would be generated and posted; write nothing")
    args = parser.parse_args(argv)
    if engine is None:
        from ..database import engine as default_engine

        engine = default_engine
    report = run(engine, "manual", dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in ("completed", "dry_run") else 1


if __name__ == "__main__":
    sys.exit(main())
