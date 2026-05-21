"""Integration test for the warrant day-trade flag backfill migration.

Targets the live dev Postgres because the migration relies on PG-specific
SQL (``AT TIME ZONE 'UTC'``, ``BOOL_OR``, ``IS DISTINCT FROM``). Skips
when ``SQLALCHEMY_DATABASE_URL`` is unset or unreachable. All test writes
happen inside a savepoint that ROLLBACKs at teardown — the dev DB stays
clean.
"""
from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "p3e4f5g6h7i8_backfill_day_trade_flags.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("warrant_backfill_migration", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def pg_engine():
    try:
        from app.database import SQLALCHEMY_DATABASE_URL
    except Exception as exc:  # pragma: no cover — defensive only
        pytest.skip(f"app.database unavailable: {exc}")

    if not SQLALCHEMY_DATABASE_URL.startswith("postgresql"):
        pytest.skip("warrant backfill migration requires Postgres-only SQL")

    engine = create_engine(SQLALCHEMY_DATABASE_URL)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"dev Postgres unreachable: {exc}")

    yield engine
    engine.dispose()


def _insert_tx(
    conn,
    *,
    symbol: str,
    side: str,
    is_day_trade: bool,
    trade_date: datetime,
):
    conn.execute(
        text(
            "INSERT INTO transactions "
            "(symbol, name, type, quantity, price, fee, tax, trade_date, "
            " is_day_trade, position_side) "
            "VALUES (:symbol, :name, :type, :quantity, :price, :fee, :tax, "
            "        :trade_date, :is_day_trade, 'LONG')"
        ),
        {
            "symbol": symbol,
            "name": symbol,
            "type": side,
            "quantity": 10,
            "price": Decimal("50.00"),
            "fee": Decimal("0.00"),
            "tax": Decimal("0.00"),
            "trade_date": trade_date,
            "is_day_trade": is_day_trade,
        },
    )


def test_backfill_flips_warrant_pair_false_and_leaves_equity_unchanged(pg_engine):
    migration = _load_migration_module()

    warrant_symbol = f"W{uuid4().hex[:8]}"
    equity_symbol = f"E{uuid4().hex[:8]}"
    trade_day = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)

    with pg_engine.connect() as conn:
        outer = conn.begin()
        try:
            conn.execute(
                text(
                    "INSERT INTO symbol_map (name, symbol, market, type) "
                    "VALUES (:name, :sym, 'TWSE', :type)"
                ),
                {"name": warrant_symbol, "sym": warrant_symbol, "type": "上市認購(售)權證"},
            )
            conn.execute(
                text(
                    "INSERT INTO symbol_map (name, symbol, market, type) "
                    "VALUES (:name, :sym, 'TWSE', :type)"
                ),
                {"name": equity_symbol, "sym": equity_symbol, "type": "股票"},
            )

            _insert_tx(conn, symbol=warrant_symbol, side="BUY", is_day_trade=True, trade_date=trade_day)
            _insert_tx(conn, symbol=warrant_symbol, side="SELL", is_day_trade=True, trade_date=trade_day)
            _insert_tx(conn, symbol=equity_symbol, side="BUY", is_day_trade=True, trade_date=trade_day)
            _insert_tx(conn, symbol=equity_symbol, side="SELL", is_day_trade=True, trade_date=trade_day)

            stats = migration.clear_warrant_day_trade_flags(conn)

            warrant_flags = [
                row[0]
                for row in conn.execute(
                    text("SELECT is_day_trade FROM transactions WHERE symbol = :sym"),
                    {"sym": warrant_symbol},
                )
            ]
            equity_flags = [
                row[0]
                for row in conn.execute(
                    text("SELECT is_day_trade FROM transactions WHERE symbol = :sym"),
                    {"sym": equity_symbol},
                )
            ]

            assert warrant_flags == [False, False], "warrant BUY+SELL pair must flip to False"
            assert equity_flags == [True, True], "equity pair must stay True (narrow migration leaves equities untouched)"
            assert stats["rows_flipped_to_false"] >= 2
            assert stats["symbols_ineligible"] >= 1
        finally:
            outer.rollback()
