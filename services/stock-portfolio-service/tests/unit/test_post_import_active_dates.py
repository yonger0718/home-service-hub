"""Orchestrator wiring for active-date optimization."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Any, Iterator
from unittest.mock import MagicMock

from app.services import networth_backfill_service as nbs
from app.services import post_import_orchestrator as orch


@contextmanager
def _factory_cm(session: Any) -> Iterator[Any]:
    yield session


def _session_factory(session: Any):
    def factory():
        return _factory_cm(session)

    return factory


def test_networth_step_passes_computed_active_dates(db_session: Any, monkeypatch: Any) -> None:
    active_dates = {date(2026, 5, 15)}
    outcome = nbs.NetworthBackfillResult()
    compute = MagicMock(return_value=active_dates)
    run = MagicMock(return_value=outcome)
    monkeypatch.setattr(nbs, "compute_active_dates", compute)
    monkeypatch.setattr(nbs, "run_backfill", run)

    step = orch._step_networth_backfill(
        _session_factory(db_session), date(2026, 5, 14), date(2026, 5, 15)
    )

    assert step.status == "ok"
    compute.assert_called_once_with(db_session, date(2026, 5, 14), date(2026, 5, 15))
    run.assert_called_once_with(
        db_session,
        date(2026, 5, 14),
        date(2026, 5, 15),
        phase="both",
        active_dates=active_dates,
    )


def test_networth_step_short_circuits_empty_active_dates(
    db_session: Any, monkeypatch: Any
) -> None:
    monkeypatch.setattr(nbs, "compute_active_dates", MagicMock(return_value=set()))
    run = MagicMock()
    monkeypatch.setattr(nbs, "run_backfill", run)

    step = orch._step_networth_backfill(
        _session_factory(db_session), date(2026, 5, 11), date(2026, 5, 15)
    )

    run.assert_not_called()
    assert step.status == "ok"
    assert step.detail == {
        "dates_processed": 0,
        "dates_skipped": 0,
        "dates_inactive": 5,
        "rows_written": 0,
        "snapshots_written": 0,
        "stale_rows_deleted": 0,
        "errors": [],
    }
