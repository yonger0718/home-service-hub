"""Post-import recalc chain.

Runs after a successful CSV commit (or on demand via the manual endpoint).
Sequentially: symbol-name backfill → pending historical dividend recording for touched
symbols → networth backfill across the affected date range. Each step is
isolated so a single TWSE outage cannot block the rest of the chain.

State is kept in-process: a module-level ``_LATEST_RESULTS`` dict keyed
by start timestamp, plus a ``threading.Lock`` that serializes concurrent
invocations. This fits the single-instance deployment; a worker queue
would be overkill and adds an operational surface for one user.

The lock is a thread lock (not asyncio.Lock) because FastAPI ``BackgroundTasks``
runs sync callables in worker threads, each of which spins up its own event loop
via ``asyncio.run``. An asyncio.Lock bound to a different loop would raise
``RuntimeError`` under concurrent invocations.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import date as dt_date, datetime, timedelta, timezone
from typing import Callable, ContextManager, Optional

from . import (
    dividend_reconciliation_service,
    networth_backfill_service,
    symbol_map_service,
)

logger = logging.getLogger(__name__)

_TW_OFFSET = timezone(timedelta(hours=8))
_RECALC_LOCK = threading.Lock()
_RESULTS_LOCK = threading.Lock()
_LATEST_RESULTS: dict[str, "ChainResult"] = {}
_FLAG_ENV = "POST_IMPORT_RECALC_ENABLED"


# ---------- Public types ----------


@dataclass
class StepResult:
    name: str
    status: str  # "ok" | "failed" | "skipped" | "partial"
    detail: dict = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class ChainResult:
    state: str  # "running" | "completed" | "partial" | "failed"
    started_at: str
    finished_at: Optional[str] = None
    recalc_from: Optional[str] = None
    recalc_to: Optional[str] = None
    touched_symbols: list[str] = field(default_factory=list)
    steps: list[StepResult] = field(default_factory=list)
    current_step: Optional[str] = None
    kind: str = "import"


# ---------- Module helpers ----------


def is_enabled() -> bool:
    """Feature flag — `POST_IMPORT_RECALC_ENABLED=false` disables auto-scheduling."""
    return os.getenv(_FLAG_ENV, "true").strip().lower() not in {"false", "0", "no"}


def today_tw() -> dt_date:
    return datetime.now(_TW_OFFSET).date()


def _prune_results_locked() -> None:
    """Caller must hold _RESULTS_LOCK."""
    # Keep the newest result of each kind until superseded (or process restart).
    # A TTL must not turn unresolved import work into idle after quote activity.
    newest = {}
    for key, result in _LATEST_RESULTS.items():
        newest[result.kind] = max(key, newest.get(result.kind, key))
    for key, result in list(_LATEST_RESULTS.items()):
        if key != newest[result.kind]:
            _LATEST_RESULTS.pop(key, None)


def _store(result: ChainResult) -> None:
    with _RESULTS_LOCK:
        _LATEST_RESULTS[result.started_at] = result
        _prune_results_locked()


def latest_status() -> dict:
    """Return the latest import plus separate quote result; bounded in-process state."""
    with _RESULTS_LOCK:
        _prune_results_locked()
        if not _LATEST_RESULTS:
            return {"state": "idle"}
        imports = [r for r in _LATEST_RESULTS.values() if r.kind == "import"]
        quotes = [r for r in _LATEST_RESULTS.values() if r.kind == "quotes"]
        result = max(imports or quotes, key=lambda r: r.started_at)
        status = _serialize(result)
        status["quote_refresh"] = _serialize(max(quotes, key=lambda r: r.started_at)) if quotes else {"state": "idle"}
        return status


def _serialize(result: ChainResult) -> dict:
    return {
        "state": result.state,
        "kind": result.kind,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "recalc_from": result.recalc_from,
        "recalc_to": result.recalc_to,
        "touched_symbols": result.touched_symbols,
        "current_step": result.current_step,
        "steps": [
            {
                "name": step.name,
                "status": step.status,
                "detail": step.detail,
                "error": step.error,
            }
            for step in result.steps
        ],
    }


def reset_state_for_tests() -> None:
    """Drop in-memory state. Tests only."""
    with _RESULTS_LOCK:
        _LATEST_RESULTS.clear()


# ---------- Chain steps ----------


def _step_symbol_map_backfill(session_factory: Callable[[], ContextManager]) -> StepResult:
    try:
        with session_factory() as db:
            outcome = symbol_map_service.backfill_transactions(db, dry_run=False)
        return StepResult(name="symbol_map_backfill", status="ok", detail=outcome)
    except Exception as exc:  # noqa: BLE001 — chain must continue
        logger.exception(
            "post_import.step_failed",
            extra={"step": "symbol_map_backfill", "error": str(exc)},
        )
        return StepResult(
            name="symbol_map_backfill", status="failed", error=str(exc)
        )


def _step_dividends(
    session_factory: Callable[[], ContextManager],
    touched_symbols: set[str],
    recalc_from: dt_date,
    recalc_to: dt_date,
) -> StepResult:
    if not touched_symbols:
        return StepResult(
            name="dividend_auto_record", status="skipped",
            detail={"reason": "no touched symbols"},
        )
    try:
        with session_factory() as db:
            detail = dividend_reconciliation_service.reconcile(db, touched_symbols, recalc_from, recalc_to)
            db.commit()
        errors = detail["source_errors"]
        deferred = detail["deferred_events"]
        status = "partial" if errors or deferred else "ok"
        if errors and not detail["sources_succeeded"]:
            status = "failed"
        return StepResult(
            name="dividend_auto_record", status=status, detail=detail,
            error="historical sources incomplete" if errors else (
                dividend_reconciliation_service.DEFERRED_REASON if deferred else None
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "post_import.step_failed",
            extra={"step": "dividend_auto_record", "error": str(exc)},
        )
        return StepResult(
            name="dividend_auto_record", status="failed", error=str(exc)
        )


def _step_networth_backfill(
    session_factory: Callable[[], ContextManager],
    recalc_from: dt_date,
    recalc_to: dt_date,
) -> StepResult:
    try:
        with session_factory() as db:
            active_dates = networth_backfill_service.compute_active_dates(
                db, recalc_from, recalc_to
            )
            if not active_dates:
                inactive_weekdays = networth_backfill_service.count_trading_days(
                    recalc_from, recalc_to
                )
                return StepResult(
                    name="networth_backfill",
                    status="ok",
                    detail={
                        "dates_processed": 0,
                        "dates_skipped": 0,
                        "dates_inactive": inactive_weekdays,
                        "rows_written": 0,
                        "snapshots_written": 0,
                        "stale_rows_deleted": 0,
                        "errors": [],
                    },
                )
            outcome = networth_backfill_service.run_backfill(
                db,
                recalc_from,
                recalc_to,
                phase="both",
                active_dates=active_dates,
            )
        detail = {
            "dates_processed": outcome.dates_processed,
            "dates_skipped": outcome.dates_skipped,
            "dates_inactive": outcome.dates_inactive,
            "rows_written": outcome.rows_written,
            "snapshots_written": outcome.snapshots_written,
            "stale_rows_deleted": outcome.stale_rows_deleted,
            "errors": [
                {"date": err.date.isoformat(), "reason": err.reason}
                for err in outcome.errors
            ],
        }
        status = "ok" if not outcome.errors else "partial"
        return StepResult(name="networth_backfill", status=status, detail=detail)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "post_import.step_failed",
            extra={"step": "networth_backfill", "error": str(exc)},
        )
        return StepResult(name="networth_backfill", status="failed", error=str(exc))


# ---------- Orchestrator ----------


def _final_state(steps: list[StepResult]) -> str:
    statuses = {step.status for step in steps}
    if statuses == {"ok"} or statuses <= {"ok", "skipped"}:
        return "completed"
    if "failed" in statuses and not (statuses & {"ok", "partial"}):
        return "failed"
    return "partial"


async def run_chain(
    session_factory: Callable[[], ContextManager],
    *,
    recalc_from: dt_date,
    recalc_to: dt_date,
    touched_symbols: set[str],
) -> ChainResult:
    """Run the full recalc chain. Caller (``schedule_chain_sync``) holds
    ``_RECALC_LOCK`` for the duration so this coroutine does not touch the lock
    itself — see module docstring for why the lock is ``threading.Lock``."""
    started = datetime.now(timezone.utc)
    result = ChainResult(
        state="running",
        started_at=started.isoformat(),
        recalc_from=recalc_from.isoformat(),
        recalc_to=recalc_to.isoformat(),
        touched_symbols=sorted(touched_symbols),
        current_step="awaiting_lock",
    )
    _store(result)

    loop = asyncio.get_running_loop()

    for step_name, runner in (
        ("symbol_map_backfill", lambda: _step_symbol_map_backfill(session_factory)),
        (
            "dividend_auto_record",
            lambda: _step_dividends(
                session_factory, touched_symbols, recalc_from, recalc_to
            ),
        ),
        (
            "networth_backfill",
            lambda: _step_networth_backfill(session_factory, recalc_from, recalc_to),
        ),
    ):
        result.current_step = step_name
        _store(result)
        step_result = await loop.run_in_executor(None, runner)
        result.steps.append(step_result)
        _store(result)

    result.current_step = None
    result.finished_at = datetime.now(timezone.utc).isoformat()
    result.state = _final_state(result.steps)
    _store(result)
    logger.info(
        "post_import.chain_done",
        extra={
            "state": result.state,
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "touched_symbols": result.touched_symbols,
        },
    )
    return result


async def run_chain_quotes_only(
    session_factory: Callable[[], ContextManager],
    *,
    today: dt_date,
    touched_symbols: set[str],
) -> ChainResult:
    started = datetime.now(timezone.utc)
    result = ChainResult(
        state="running",
        kind="quotes",
        started_at=started.isoformat(),
        recalc_from=today.isoformat(),
        recalc_to=today.isoformat(),
        touched_symbols=sorted(touched_symbols),
        current_step="awaiting_lock",
    )
    _store(result)

    loop = asyncio.get_running_loop()
    result.current_step = "networth_backfill"
    _store(result)
    step_result = await loop.run_in_executor(
        None,
        lambda: _step_networth_backfill(session_factory, today, today),
    )
    result.steps.append(step_result)
    _store(result)

    result.current_step = None
    result.finished_at = datetime.now(timezone.utc).isoformat()
    result.state = _final_state(result.steps)
    _store(result)
    logger.info(
        "post_import.quotes_refresh_done",
        extra={
            "state": result.state,
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "touched_symbols": result.touched_symbols,
        },
    )
    return result


def schedule_chain_sync(
    session_factory: Callable[[], ContextManager],
    *,
    recalc_from: dt_date,
    recalc_to: dt_date,
    touched_symbols: set[str],
) -> ChainResult:
    """Sync entrypoint for FastAPI ``BackgroundTasks`` (which calls regular callables).

    Serializes concurrent chain runs via ``_RECALC_LOCK``. The lock is acquired
    here (sync, before spawning the event loop) so each ``asyncio.run`` below
    operates on its own loop without ever touching the shared lock primitive.
    """
    with _RECALC_LOCK:
        return asyncio.run(
            run_chain(
                session_factory,
                recalc_from=recalc_from,
                recalc_to=recalc_to,
                touched_symbols=touched_symbols,
            )
        )


def schedule_quotes_refresh_sync(
    session_factory: Callable[[], ContextManager],
    *,
    touched_symbols: set[str],
) -> ChainResult:
    today = today_tw()
    with _RECALC_LOCK:
        return asyncio.run(
            run_chain_quotes_only(
                session_factory,
                today=today,
                touched_symbols=touched_symbols,
            )
        )
