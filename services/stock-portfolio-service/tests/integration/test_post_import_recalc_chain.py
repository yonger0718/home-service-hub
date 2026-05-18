"""Integration coverage for active-date post-import recalculation."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterator

from app.models import portfolio as portfolio_models
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.price_history import PriceHistory
from app.services import networth_backfill_service as nbs
from app.services import post_import_orchestrator as orch


@contextmanager
def _factory_cm(session: Any) -> Iterator[Any]:
    yield session


def _session_factory(session: Any):
    def factory():
        return _factory_cm(session)

    return factory


def _seed_tx(
    db: Any,
    *,
    symbol: str,
    side: portfolio_models.TransactionType,
    qty: int,
    trade_date: date,
) -> None:
    db.add(
        portfolio_models.Transaction(
            symbol=symbol,
            type=side,
            quantity=qty,
            price=Decimal("10"),
            trade_date=datetime.combine(
                trade_date, datetime.min.time(), tzinfo=timezone.utc
            ),
            fee=Decimal("0"),
            tax=Decimal("0"),
        )
    )
    db.flush()


def _row(symbol: str, d: date, source: str) -> Any:
    return type(
        "_Row",
        (),
        {
            "symbol": symbol,
            "date": d,
            "open": None,
            "high": None,
            "low": None,
            "close": Decimal("10"),
            "volume": None,
            "turnover": None,
            "source": source,
        },
    )()


def _run_chain_with_fake_fetchers(
    db_session: Any,
    monkeypatch: Any,
    *,
    recalc_from: date,
    recalc_to: date,
    touched_symbols: set[str],
    closed_dates: set[date] | None = None,
) -> tuple[list[date], list[date]]:
    twse_calls: list[date] = []
    tpex_calls: list[date] = []
    real_backfill = nbs.backfill_prices_range
    closed_dates = closed_dates or set()

    def _twse(d: date) -> list[Any]:
        twse_calls.append(d)
        if d in closed_dates:
            return []
        return [_row("2330", d, "TWSE")]

    def _tpex(d: date) -> list[Any]:
        tpex_calls.append(d)
        if d in closed_dates:
            return []
        return [_row("6488", d, "TPEx")]

    def _backfill_with_fakes(
        db: Any,
        from_d: date,
        to_d: date,
        **kwargs: Any,
    ) -> nbs.PriceBackfillResult:
        return real_backfill(
            db,
            from_d,
            to_d,
            throttle_sec=0,
            sleep=lambda _s: None,
            twse_fetcher=_twse,
            tpex_fetcher=_tpex,
            active_dates=kwargs.get("active_dates"),
        )

    monkeypatch.setattr(nbs, "backfill_prices_range", _backfill_with_fakes)
    monkeypatch.setattr(
        orch,
        "_step_symbol_map_backfill",
        lambda _factory: orch.StepResult("symbol_map_backfill", "ok"),
    )
    monkeypatch.setattr(
        orch,
        "_step_dividends",
        lambda _factory, _symbols, _from_d, _to_d: orch.StepResult(
            "dividend_auto_record", "ok"
        ),
    )

    asyncio.run(
        orch.run_chain(
            _session_factory(db_session),
            recalc_from=recalc_from,
            recalc_to=recalc_to,
            touched_symbols=touched_symbols,
        )
    )
    return twse_calls, tpex_calls


def test_closed_position_chain_only_replays_held_weekdays(
    db_session: Any, monkeypatch: Any
) -> None:
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.BUY,
        qty=100,
        trade_date=date(2022, 1, 3),
    )
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.SELL,
        qty=100,
        trade_date=date(2022, 1, 5),
    )
    db_session.commit()

    twse_calls, tpex_calls = _run_chain_with_fake_fetchers(
        db_session,
        monkeypatch,
        recalc_from=date(2022, 1, 3),
        recalc_to=date(2026, 5, 18),
        touched_symbols={"2330"},
    )

    expected = {date(2022, 1, 3), date(2022, 1, 4), date(2022, 1, 5)}
    assert set(twse_calls) == expected
    assert set(tpex_calls) == expected
    assert len(twse_calls) == 3
    assert len(tpex_calls) == 3
    assert {row.date for row in db_session.query(PortfolioSnapshot).all()} == expected


def test_open_position_chain_replays_every_held_weekday(
    db_session: Any, monkeypatch: Any
) -> None:
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.BUY,
        qty=100,
        trade_date=date(2024, 6, 3),
    )
    db_session.commit()

    twse_calls, tpex_calls = _run_chain_with_fake_fetchers(
        db_session,
        monkeypatch,
        recalc_from=date(2024, 6, 3),
        recalc_to=date(2024, 6, 7),
        touched_symbols={"2330"},
    )

    expected = {
        date(2024, 6, 3),
        date(2024, 6, 4),
        date(2024, 6, 5),
        date(2024, 6, 6),
        date(2024, 6, 7),
    }
    assert set(twse_calls) == expected
    assert set(tpex_calls) == expected
    assert {row.date for row in db_session.query(PriceHistory).all()} == expected
    assert {row.date for row in db_session.query(PortfolioSnapshot).all()} == expected


def test_chain_forward_fills_lny_cluster_and_replaces_stale_row(
    db_session: Any, monkeypatch: Any
) -> None:
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.BUY,
        qty=100,
        trade_date=date(2022, 1, 26),
    )
    db_session.add(
        PortfolioSnapshot(
            date=date(2022, 1, 27),
            total_market_value=Decimal("0"),
            total_cost=Decimal("209065.625"),
            total_unrealized_pnl=Decimal("-209065.625"),
            total_dividends=Decimal("0"),
            total_realized_pnl=Decimal("0"),
            portfolio_xirr=None,
        )
    )
    db_session.commit()

    cluster = {date(2022, 1, day) for day in range(27, 32)} | {
        date(2022, 2, day) for day in range(1, 5)
    }
    _run_chain_with_fake_fetchers(
        db_session,
        monkeypatch,
        recalc_from=date(2022, 1, 26),
        recalc_to=date(2022, 2, 7),
        touched_symbols={"2330"},
        closed_dates=cluster,
    )

    snaps = {row.date: row for row in db_session.query(PortfolioSnapshot).all()}
    pre_cluster_mv = snaps[date(2022, 1, 26)].total_market_value
    assert cluster <= set(snaps)
    for d in cluster:
        assert snaps[d].total_market_value == pre_cluster_mv
        assert snaps[d].total_cost == snaps[date(2022, 1, 26)].total_cost
    assert snaps[date(2022, 1, 27)].total_market_value != Decimal("0")
    assert snaps[date(2022, 2, 7)].total_market_value == Decimal("1000")
