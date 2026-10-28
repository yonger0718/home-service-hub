"""The card-statement auto-pay daily job (auto-pay spec §4.4) and its in-process scheduler.

Lock: run() takes AUTO_PAY_JOB_LOCK_KEY (session-level, on a dedicated connection) as schedule_job.run does — a
second runner (the CLI, another process, an overlapping scheduler fire) returns `busy` and writes nothing.

Order, under the lock:
1. the effective `auto_pay` settings (and the due-less statements, `no_due_date`) in one read;
2. housekeeping (§4.4 step 0; skipped in a dry run): every `booked` row in its own transaction, which begins with
   the shared import key — `import_running` stops the whole run (rows already checked stay done), `auto_pay_busy`
   (a leg locked by an owner write) counts `busy`, any other error rolls that row back, is logged and counts `failed`;
3. a dry run lists every candidate's would-be decision (`would_book` / `would_skip`) whatever `enabled` says, and
   writes nothing; otherwise `enabled` false ends the run `disabled` (housekeeping done);
4. each candidate in its own transaction, beginning with the shared import key — `import_running` stops the run,
   any other error rolls that statement back and is reported as a `failed` decision (logged, the run goes on);
5. a candidate due past business_days.LAST_KNOWN_DAY sets `calendar_stale`; a real run audits
   `auto_pay_calendar_stale` once per `today` (skipped when that day's audit row exists).
Scheduler (#191): a scheduled run that ends `busy` / `import_running` / `crashed` adds a retry every RETRY_MINUTES
for the rest of that Asia/Taipei day; the next run that ends otherwise removes it, and a retry firing on a new day
removes itself (the 06:00 run takes over). The CLI never schedules a retry.
Logs carry ids, counts and error classes only — never amounts or names.

CLI: python -m app.services.auto_pay_job [--dry-run] [--as-of YYYY-MM-DD]
Prints the report as JSON; exit 0 for completed / dry_run / disabled, else 1. An `--as-of` later than today
(Asia/Taipei) is refused (exit 2, one line on stderr) unless `--dry-run`.
"""

import argparse
import json
import logging
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
from sqlalchemy.orm import sessionmaker

from ..models import ReconciliationAudit, StatementAutoPay
from . import auto_pay_service as aps
from . import ledger_service, settings_service
from .business_days import LAST_KNOWN_DAY, calendar_covers
from .errors import CodedConflictError
from .reconciliation_actions import audit
from .schedule_job import TZ_NAME, is_enabled
from .schedule_locks import AUTO_PAY_JOB_LOCK_KEY, ImportRunningError, take_import_key_shared

logger = logging.getLogger(__name__)

TRIGGERS = ("cron", "startup", "retry", "manual")
DAILY_JOB_ID = "auto_pay_daily"
STARTUP_JOB_ID = "auto_pay_startup"
RETRY_JOB_ID = "auto_pay_retry"
RETRY_MINUTES = 10
RETRY_STATUSES = ("busy", "import_running", "crashed")
STARTUP_DELAY_SEC = 20
HOUSEKEEPING_KEYS = ("checked", "owner_deleted", "owner_edited", "retired", "updated", "header_changed", "busy",
                     "failed")
CALENDAR_STALE = "auto_pay_calendar_stale"

_scheduler: BackgroundScheduler | None = None


def _new_report(trigger: str, today: date) -> dict:
    return {
        "trigger": trigger, "today": today.isoformat(), "status": "completed", "enabled": False,
        "housekeeping": dict.fromkeys(HOUSEKEEPING_KEYS, 0), "decisions": [], "no_due_date": [],
        "calendar_stale": False,
    }


def _log_error(event: str, exc: BaseException, **ids) -> None:
    """ERROR with ids, the error class, the traceback frames and the classes of the cause / context chain — never the
    message (a driver's text can carry amounts and names); the full exception goes to DEBUG only."""
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


def _housekeeping(factory, today: date, settings: dict, report: dict) -> bool:
    """One transaction per booked row; False when an import stopped the run. `checked` counts the rows whose check
    ran and committed; `busy` and `failed` the others."""
    with factory() as db:
        row_ids = [row.id for row in aps.booked_rows(db)]
    counts = report["housekeeping"]
    for row_id in row_ids:
        with factory() as db:
            try:
                take_import_key_shared(db)
                row = db.get(StatementAutoPay, row_id)
                transition = None if row is None else aps.check_row(db, row, today, settings)
                db.commit()
            except ImportRunningError:
                db.rollback()
                report["status"] = "import_running"
                return False
            except CodedConflictError as exc:
                db.rollback()
                if exc.code == aps.PAIR_BUSY:
                    counts["busy"] += 1
                else:
                    _log_error("auto_pay_job.housekeeping_failed", exc, auto_pay_id=row_id)
                    counts["failed"] += 1
                continue
            except Exception as exc:  # noqa: BLE001 — one broken row must not stop the others
                db.rollback()
                _log_error("auto_pay_job.housekeeping_failed", exc, auto_pay_id=row_id)
                counts["failed"] += 1
                continue
        counts["checked"] += 1
        if transition is not None:
            counts[transition] += 1
    return True


def _failed(cand: aps.Candidate, exc: BaseException) -> dict:
    return {"statement_id": cand.statement_id, "card_account_id": cand.card_account_id,
            "pay_account_id": cand.pay_account_id, "amount": str(cand.amount), "pay_date": cand.pay_date.isoformat(),
            "decision": "failed", "reason": None, "error_class": exc.__class__.__name__}


def _decide_all(factory, today: date, settings: dict, report: dict, *, dry_run: bool) -> list[int]:
    """One transaction per candidate (rolled back in a dry run, which takes no import key); returns the ids of the
    candidates due past the holiday table."""
    with factory() as db:
        found = aps.candidates(db, today, settings)
    stale = [cand.statement_id for cand in found if not calendar_covers(cand.due_date)]
    report["calendar_stale"] = bool(stale)
    for cand in found:
        with factory() as db:
            try:
                if dry_run:
                    decision = aps.decide(db, cand, today, settings, dry_run=True)
                    db.rollback()
                else:
                    take_import_key_shared(db)
                    decision = aps.decide(db, cand, today, settings)
                    db.commit()
            except ImportRunningError:
                db.rollback()
                report["status"] = "import_running"
                break
            except Exception as exc:  # noqa: BLE001 — one broken statement must not stop the others
                db.rollback()
                _log_error("auto_pay_job.decision_failed", exc, statement_id=cand.statement_id)
                decision = _failed(cand, exc)
        report["decisions"].append(decision)
    return stale


def _audit_calendar_stale(factory, today: date, statement_ids: list[int]) -> None:
    """Audit the stale holiday table once per `today` (matched on the audit's detail.today)."""
    logger.warning("auto_pay_job.calendar_stale", extra={"statement_ids": statement_ids,
                                                         "last_known_day": LAST_KNOWN_DAY.isoformat()})
    with factory() as db:
        try:
            exists = db.scalar(select(ReconciliationAudit.id).where(
                ReconciliationAudit.event == CALENDAR_STALE,
                ReconciliationAudit.detail["today"].astext == today.isoformat()).limit(1))
            if exists is None:
                audit.record(db, event=CALENDAR_STALE, actor=aps.ACTOR,
                             detail={"today": today.isoformat(), "last_known_day": LAST_KNOWN_DAY.isoformat(),
                                     "statement_ids": statement_ids})
                db.commit()
        except Exception as exc:  # noqa: BLE001 — the warning is logged; the bookings are already committed
            db.rollback()
            _log_error("auto_pay_job.calendar_stale_audit_failed", exc)


def _run_locked(factory, today: date, dry_run: bool, report: dict) -> None:
    with factory() as db:
        settings = settings_service.read_auto_pay_settings(db)
        report["no_due_date"] = aps.no_due_date(db)
    report["enabled"] = settings["enabled"] is True
    if dry_run:
        _decide_all(factory, today, settings, report, dry_run=True)
        report["status"] = "dry_run"
        return
    if not _housekeeping(factory, today, settings, report):
        return
    if not report["enabled"]:
        report["status"] = "disabled"
        return
    stale = _decide_all(factory, today, settings, report, dry_run=False)
    if stale and report["status"] == "completed":
        _audit_calendar_stale(factory, today, stale)


def run(engine: Engine, trigger: str, *, today: date | None = None, dry_run: bool = False) -> dict:
    if trigger not in TRIGGERS:
        raise ValueError(f"unknown trigger {trigger!r}")
    today = today or ledger_service._today()
    report = _new_report(trigger, today)
    with engine.connect() as lock_conn:
        acquired = lock_conn.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": AUTO_PAY_JOB_LOCK_KEY}).scalar_one()
        lock_conn.commit()
        if not acquired:
            report["status"] = "busy"
        else:
            try:
                _run_locked(sessionmaker(bind=engine, autoflush=False), today, dry_run, report)
            finally:
                lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": AUTO_PAY_JOB_LOCK_KEY})
                lock_conn.commit()
    by_decision: dict[str, list[int]] = {}
    for decision in report["decisions"]:
        by_decision.setdefault(decision["decision"], []).append(decision["statement_id"])
    logger.info(
        "auto_pay_job.run",
        extra={
            "trigger": trigger, "run_status": report["status"], "enabled": report["enabled"],
            "housekeeping": report["housekeeping"], "decisions": by_decision,
            "no_due_date": report["no_due_date"], "calendar_stale": report["calendar_stale"],
        },
    )
    return report


def _job(engine: Engine, trigger: str) -> None:
    started = ledger_service._today().isoformat()  # a crash keeps the day the run began, not the day it failed on
    try:
        report = run(engine, trigger)
    except Exception as exc:  # noqa: BLE001 — the scheduler thread must survive
        _log_error("auto_pay_job.crashed", exc, trigger=trigger)
        report = {"status": "crashed", "today": started}
    _after_run(engine, report)


def _after_run(engine: Engine, report: dict, scheduler=None) -> None:
    """busy / import_running / crashed → retry every RETRY_MINUTES that Taipei day; any other end — or a run whose day
    is already over (it crossed midnight) — clears the retry."""
    scheduler = scheduler if scheduler is not None else _scheduler
    if scheduler is None:
        return
    if report["status"] in RETRY_STATUSES and report["today"] == ledger_service._today().isoformat():
        scheduler.add_job(
            _retry, IntervalTrigger(minutes=RETRY_MINUTES, timezone=TZ_NAME), id=RETRY_JOB_ID, replace_existing=True,
            kwargs={"engine": engine, "day": report["today"]}, coalesce=True, max_instances=1,
        )
        logger.info("auto_pay_job.retry_scheduled", extra={"run_status": report["status"], "day": report["today"],
                                                           "minutes": RETRY_MINUTES})
    elif scheduler.get_job(RETRY_JOB_ID) is not None:
        scheduler.remove_job(RETRY_JOB_ID)


def _retry(engine: Engine, day: str) -> None:
    if ledger_service._today().isoformat() != day:  # a new Taipei day: the 06:00 run takes over
        if _scheduler is not None and _scheduler.get_job(RETRY_JOB_ID) is not None:
            _scheduler.remove_job(RETRY_JOB_ID)
        return
    _job(engine, "retry")


def build_scheduler(engine: Engine) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=TZ_NAME)
    scheduler.add_job(
        _job, CronTrigger(hour=6, minute=0, timezone=TZ_NAME), id=DAILY_JOB_ID, replace_existing=True,
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
        logger.info("auto_pay_job.scheduler_disabled")
        return
    if _scheduler is not None:
        return
    try:
        _scheduler = build_scheduler(engine)
        _scheduler.start()
    except Exception as exc:  # noqa: BLE001 — the API stays up without the job; the CLI still works
        _scheduler = None
        logger.exception("auto_pay_job.scheduler_failed", extra={"error_class": exc.__class__.__name__})
        return
    logger.info("auto_pay_job.scheduler_started", extra={"jobs": [job.id for job in _scheduler.get_jobs()]})


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("auto_pay_job.scheduler_stopped")


def main(argv: Sequence[str] | None = None, *, engine: Engine | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.services.auto_pay_job")
    parser.add_argument("--dry-run", action="store_true", help="list what would be booked or skipped; write nothing")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD",
                        help="run as if today were this date (default: today in Asia/Taipei)")
    args = parser.parse_args(argv)
    if args.as_of is not None and not args.dry_run and args.as_of > ledger_service._today():
        # a real run books on the pay date; looking ahead would book payments that have not happened yet
        print(f"--as-of {args.as_of.isoformat()} is later than today (Asia/Taipei); use --dry-run to look ahead",
              file=sys.stderr)
        return 2
    if engine is None:
        from ..database import engine as default_engine

        engine = default_engine
    report = run(engine, "manual", today=args.as_of, dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in ("completed", "dry_run", "disabled") else 1


if __name__ == "__main__":
    sys.exit(main())
