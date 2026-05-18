"""Partial-fetch gate coverage for Phase 1 whole-market backfills."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.price_history import PriceHistory
from app.services import networth_backfill_service as nbs


def _seed_count(db, *, source: str, d: date, count: int) -> None:
    db.bulk_save_objects(
        [
            PriceHistory(
                symbol=f"{source}-{d.isoformat()}-{idx:04d}",
                date=d,
                close=Decimal("10"),
                source=source,
            )
            for idx in range(count)
        ]
    )
    db.flush()


def test_recent_row_counts_filters_source_excludes_today_and_caps_window(db_session):
    today = date(2026, 5, 18)
    for offset in range(1, 36):
        _seed_count(
            db_session,
            source="TWSE",
            d=today - timedelta(days=offset),
            count=offset,
        )
    _seed_count(db_session, source="TWSE", d=today, count=99)
    _seed_count(db_session, source="TPEx", d=today - timedelta(days=1), count=77)
    db_session.commit()

    counts = nbs._recent_row_counts(db_session, source="TWSE", today=today)

    assert len(counts) == nbs.PARTIAL_FETCH_BASELINE_WINDOW_DAYS
    assert counts == list(range(1, 31))


def test_is_partial_response_empty_rows_skips_baseline_query_and_logging(
    monkeypatch, caplog
):
    today = date(2026, 5, 18)

    def _unexpected_query(*_args, **_kwargs):
        raise AssertionError("baseline should not be queried for empty fetches")

    monkeypatch.setattr(nbs, "_recent_row_counts", _unexpected_query)
    caplog.set_level(logging.INFO, logger=nbs.__name__)

    assert (
        nbs._is_partial_response(
            None,
            source="TWSE",
            date=today,
            fetched_rows=0,
        )
        is False
    )
    assert caplog.records == []


def test_is_partial_response_accepts_full_response(monkeypatch):
    monkeypatch.setattr(nbs, "_recent_row_counts", lambda *_args, **_kwargs: [1000] * 30)

    assert (
        nbs._is_partial_response(
            None,
            source="TWSE",
            date=date(2026, 5, 18),
            fetched_rows=850,
        )
        is False
    )


def test_is_partial_response_rejects_under_baseline_response(monkeypatch, caplog):
    today = date(2026, 5, 18)
    monkeypatch.setattr(nbs, "_recent_row_counts", lambda *_args, **_kwargs: [1000] * 30)
    caplog.set_level(logging.WARNING, logger=nbs.__name__)

    assert (
        nbs._is_partial_response(
            None,
            source="TWSE",
            date=today,
            fetched_rows=400,
        )
        is True
    )

    [record] = [
        record
        for record in caplog.records
        if record.message == "phase1.partial_fetch_skipped"
    ]
    assert record.source == "TWSE"
    assert record.date == today.isoformat()
    assert record.fetched_rows == 400
    assert record.baseline_median == 1000
    assert record.ratio == pytest.approx(0.4)


def test_is_partial_response_cold_start_logs_and_allows_response(monkeypatch, caplog):
    today = date(2026, 5, 18)
    monkeypatch.setattr(nbs, "_recent_row_counts", lambda *_args, **_kwargs: [1000] * 9)
    caplog.set_level(logging.INFO, logger=nbs.__name__)

    assert (
        nbs._is_partial_response(
            None,
            source="TWSE",
            date=today,
            fetched_rows=400,
        )
        is False
    )

    [record] = [
        record
        for record in caplog.records
        if record.message == "phase1.partial_check_skipped_cold_start"
    ]
    assert record.source == "TWSE"
    assert record.date == today.isoformat()
    assert record.baseline_days == 9


def test_is_partial_response_uses_independent_source_baselines(monkeypatch):
    baselines = {"TWSE": [1000] * 30, "TPEx": [5000] * 30}

    def _recent_row_counts(_session, *, source, today):
        del today
        return baselines[source]

    monkeypatch.setattr(nbs, "_recent_row_counts", _recent_row_counts)

    assert (
        nbs._is_partial_response(
            None,
            source="TWSE",
            date=date(2026, 5, 18),
            fetched_rows=850,
        )
        is False
    )
    assert (
        nbs._is_partial_response(
            None,
            source="TPEx",
            date=date(2026, 5, 18),
            fetched_rows=1000,
        )
        is True
    )
