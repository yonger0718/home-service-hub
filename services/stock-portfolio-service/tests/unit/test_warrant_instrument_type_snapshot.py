from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models import portfolio as models
from app.models.symbol_map import SymbolMap
from app.schemas import portfolio as schemas
from app.services import (
    broker_cathay_service,
    import_service,
    portfolio_service,
    symbol_map_service,
)


CATHAY_HEADER = (
    "股名,日期,成交股數,淨收付金額,買賣別,成交價,成本,手續費,交易稅,"
    "融資金額/券擔保品,資自備款/券保證金,利息,稅款,券手續費/標借費,委託書號\n"
)
CATHAY_TRADE_DATE = datetime(2026, 5, 8, tzinfo=timezone.utc)


def _cathay_csv(name: str, *, order_id: str = "snap1") -> bytes:
    row = (
        f'{name},2026/05/08,"1,000","-56,322",現買,"56.3","56,300",'
        f"22,0,0,0,0,0,0,{order_id}"
    )
    return (
        "根據您篩選的結果，總計有1筆資料\n" + CATHAY_HEADER + row + "\n"
    ).encode("utf-8")


def _legacy_fingerprint(symbol: str) -> str:
    return import_service._transaction_fingerprint(
        symbol,
        "BUY",
        1000,
        Decimal("56.3"),
        CATHAY_TRADE_DATE,
        Decimal("22"),
        Decimal("0"),
    )


def _seed_cathay_match(
    db_session, *, symbol: str, branch: str
) -> models.Transaction | None:
    if branch == "insert":
        return None
    tx = models.Transaction(
        symbol=symbol,
        name="snapshot",
        type=models.TransactionType.BUY,
        position_side=models.PositionSide.LONG,
        quantity=1000,
        price=Decimal("56.3"),
        trade_date=(
            CATHAY_TRADE_DATE
            if branch == "legacy"
            else datetime(2026, 5, 8, 13, 30, tzinfo=timezone.utc)
        ),
        fee=Decimal("22"),
        tax=Decimal("0"),
        import_fingerprint=(
            _legacy_fingerprint(symbol) if branch == "legacy" else None
        ),
    )
    db_session.add(tx)
    db_session.commit()
    return tx


class _NoQuerySession:
    def query(self, *args, **kwargs):
        raise AssertionError("symbol_map query should not run for stamped types")


@pytest.mark.parametrize(
    ("type_value", "expected"),
    [
        ("上市認購(售)權證", "上市認購(售)權證"),
        ("上市ETF", None),
        (None, None),
    ],
)
def test_lookup_warrant_type_returns_only_warrant_types(
    db_session, type_value: str | None, expected: str | None
):
    db_session.add(
        SymbolMap(name="mapped", symbol="045378", market="TWSE", type=type_value)
    )
    db_session.commit()

    assert symbol_map_service.lookup_warrant_type(db_session, "045378") == expected


def test_lookup_warrant_type_unmapped_symbol_returns_none(db_session):
    assert symbol_map_service.lookup_warrant_type(db_session, "045378") is None


def test_is_day_trade_eligible_stamped_warrant_is_false_without_live_lookup():
    assert (
        symbol_map_service.is_day_trade_eligible(
            _NoQuerySession(), "045378", instrument_type="上市認購(售)權證"
        )
        is False
    )


def test_is_day_trade_eligible_stamped_non_warrant_is_true_without_live_lookup():
    assert (
        symbol_map_service.is_day_trade_eligible(
            _NoQuerySession(), "0050", instrument_type="上市ETF"
        )
        is True
    )


@pytest.mark.parametrize("instrument_type", [None, ""])
def test_is_day_trade_eligible_null_or_empty_stamp_falls_through_to_live_lookup(
    db_session, instrument_type: str | None
):
    db_session.add(
        SymbolMap(name="warrant", symbol="045378", market="TWSE", type="上市認購(售)權證")
    )
    db_session.commit()

    assert (
        symbol_map_service.is_day_trade_eligible(
            db_session, "045378", instrument_type=instrument_type
        )
        is False
    )


def test_is_day_trade_eligible_unmapped_null_stamp_fails_open(db_session):
    assert (
        symbol_map_service.is_day_trade_eligible(
            db_session, "999999", instrument_type=None
        )
        is True
    )


def test_recompute_keeps_stamped_warrant_ineligible_after_symbol_map_recycle(
    db_session,
):
    symbol_map = SymbolMap(
        name="warrant", symbol="045378", market="TWSE", type="上市認購(售)權證"
    )
    trade_day = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    buy = models.Transaction(
        symbol="045378",
        name="old warrant",
        instrument_type="上市認購(售)權證",
        type=models.TransactionType.BUY,
        quantity=1000,
        price=Decimal("50.00"),
        fee=Decimal("0.00"),
        tax=Decimal("0.00"),
        trade_date=trade_day,
    )
    sell = models.Transaction(
        symbol="045378",
        name="old warrant",
        instrument_type="上市認購(售)權證",
        type=models.TransactionType.SELL,
        quantity=1000,
        price=Decimal("50.00"),
        fee=Decimal("0.00"),
        tax=Decimal("0.00"),
        trade_date=trade_day,
    )
    db_session.add_all([symbol_map, buy, sell])
    db_session.commit()

    symbol_map.type = "上市ETF"
    portfolio_service._recompute_day_trade_flags(
        db_session,
        "045378",
        portfolio_service._trade_calendar_date(trade_day),
    )
    db_session.commit()

    db_session.refresh(buy)
    db_session.refresh(sell)
    assert buy.is_day_trade is False
    assert sell.is_day_trade is False


@pytest.mark.parametrize("branch", ["insert", "legacy", "business_key"])
@pytest.mark.parametrize(
    ("map_type", "expected"),
    [
        ("上市認購(售)權證", "上市認購(售)權證"),
        ("上市ETF", None),
        (None, None),
    ],
    ids=["warrant", "non_warrant", "unmapped"],
)
def test_cathay_insert_and_rehash_paths_snapshot_only_warrant_type(
    db_session,
    monkeypatch,
    branch: str,
    map_type: str | None,
    expected: str | None,
):
    name = f"snapshot-{branch}-{map_type or 'unmapped'}"
    symbol = f"S{abs(hash((branch, map_type))) % 100000:05d}"
    monkeypatch.setitem(broker_cathay_service.NAME_TO_SYMBOL, name, [symbol])
    if map_type is not None:
        db_session.add(
            SymbolMap(name=name, symbol=symbol, market="TWSE", type=map_type)
        )
        db_session.commit()
    _seed_cathay_match(db_session, symbol=symbol, branch=branch)

    result = broker_cathay_service.parse_cathay_transactions_csv(
        _cathay_csv(name), dry_run=False, db=db_session
    )

    if branch == "insert":
        assert result.created == 1
        assert result.rehashed == 0
    else:
        assert result.created == 0
        assert result.rehashed == 1
    tx = db_session.query(models.Transaction).one()
    assert tx.instrument_type == expected


def test_create_transaction_stamps_warrant_and_leaves_etf_null(db_session):
    db_session.add_all(
        [
            SymbolMap(
                name="warrant",
                symbol="045378",
                market="TWSE",
                type="上市認購(售)權證",
            ),
            SymbolMap(name="etf", symbol="0050", market="TWSE", type="上市ETF"),
        ]
    )
    db_session.commit()

    warrant = portfolio_service.create_transaction(
        db_session,
        schemas.TransactionCreate(
            symbol="045378",
            name="warrant",
            type=schemas.TransactionType.BUY,
            quantity=1000,
            price=Decimal("50.00"),
            fee=Decimal("0.00"),
            tax=Decimal("0.00"),
            trade_date=datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc),
        ),
    )
    etf = portfolio_service.create_transaction(
        db_session,
        schemas.TransactionCreate(
            symbol="0050",
            name="etf",
            type=schemas.TransactionType.BUY,
            quantity=1000,
            price=Decimal("50.00"),
            fee=Decimal("0.00"),
            tax=Decimal("0.00"),
            trade_date=datetime(2026, 5, 16, 1, 30, tzinfo=timezone.utc),
        ),
    )

    assert warrant.instrument_type == "上市認購(售)權證"
    assert etf.instrument_type is None


@pytest.mark.parametrize("branch", ["legacy", "business"])
def test_rehash_preserves_existing_instrument_type_after_recycle(
    db_session, monkeypatch, branch: str
):
    """If a row was already stamped historically, rehash must NOT clobber it
    even when ``symbol_map`` now resolves the symbol to a different (possibly
    non-warrant) instrument due to TWSE warrant-code recycle.
    """
    name = f"preserve-{branch}"
    symbol = f"R{abs(hash(branch)) % 100000:05d}"
    monkeypatch.setitem(broker_cathay_service.NAME_TO_SYMBOL, name, [symbol])

    # symbol_map currently reports the RECYCLED non-warrant instrument.
    db_session.add(
        SymbolMap(name=name, symbol=symbol, market="TWSE", type="上市ETF")
    )
    db_session.commit()

    # Pre-existing row carries the HISTORICAL (correct) warrant stamp.
    seeded = _seed_cathay_match(db_session, symbol=symbol, branch=branch)
    assert seeded is not None
    seeded.name = name  # match CSV name so rehash can find it
    seeded.instrument_type = "上櫃認購(售)權證"
    db_session.commit()

    result = broker_cathay_service.parse_cathay_transactions_csv(
        _cathay_csv(name), dry_run=False, db=db_session
    )
    assert result.rehashed == 1

    refreshed = db_session.query(models.Transaction).filter_by(symbol=symbol).one()
    assert refreshed.instrument_type == "上櫃認購(售)權證"
