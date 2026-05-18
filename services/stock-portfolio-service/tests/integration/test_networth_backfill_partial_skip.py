"""Integration coverage for skipping only the partial Phase 1 source."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from app.models.price_history import PriceHistory
from app.services import market_data_service
from app.services import networth_backfill_service as nbs


def _seed_history_for_date(db, *, source: str, d: date, count: int) -> None:
    db.bulk_insert_mappings(
        PriceHistory,
        [
            {
                "symbol": f"{source}-{idx:04d}",
                "date": d,
                "close": Decimal("10"),
                "source": source,
            }
            for idx in range(count)
        ],
    )


def _rows_for_date(*, source: str, d: date, count: int):
    return [
        market_data_service.DailyPriceRow(
            symbol=f"{source}-today-{idx:04d}",
            date=d,
            open=None,
            high=None,
            low=None,
            close=Decimal("10"),
            volume=None,
            turnover=None,
            source=source,
        )
        for idx in range(count)
    ]


def test_backfill_prices_range_skips_only_partial_source(
    db_session,
    monkeypatch,
    caplog,
):
    today = date(2026, 5, 18)
    for offset in range(1, 31):
        d = today - timedelta(days=offset)
        _seed_history_for_date(db_session, source="TWSE", d=d, count=1300)
        _seed_history_for_date(db_session, source="TPEx", d=d, count=5300)
    db_session.commit()

    def _twse(d: date):
        return _rows_for_date(source="TWSE", d=d, count=400)

    def _tpex(d: date):
        return _rows_for_date(source="TPEx", d=d, count=5300)

    monkeypatch.setattr(nbs.market_data_service, "fetch_twse_date", _twse)
    monkeypatch.setattr(nbs.market_data_service, "fetch_tpex_date", _tpex)
    caplog.set_level(logging.WARNING, logger=nbs.__name__)

    result = nbs.backfill_prices_range(
        db_session,
        today,
        today,
        throttle_sec=0,
        sleep=lambda _s: None,
        twse_fetcher=nbs.market_data_service.fetch_twse_date,
        tpex_fetcher=nbs.market_data_service.fetch_tpex_date,
    )

    twse_today = (
        db_session.query(PriceHistory)
        .filter(PriceHistory.source == "TWSE", PriceHistory.date == today)
        .count()
    )
    tpex_today = (
        db_session.query(PriceHistory)
        .filter(PriceHistory.source == "TPEx", PriceHistory.date == today)
        .count()
    )
    warnings = [
        record
        for record in caplog.records
        if record.message == "phase1.partial_fetch_skipped"
    ]

    assert twse_today == 0
    assert tpex_today == 5300
    assert len(warnings) == 1
    assert warnings[0].source == "TWSE"
    assert len(result.errors) == 1
    assert "TWSE partial" in result.errors[0].reason
