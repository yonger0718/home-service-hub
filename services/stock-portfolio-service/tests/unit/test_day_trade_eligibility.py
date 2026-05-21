"""Day-trade flag gated on instrument eligibility.

TW FSC rules: 認購(售)權證 + 牛熊證 cannot be 現股當沖. Same-day BUY+SELL
pairs on those instruments must NOT flip to ``is_day_trade=True``, even
though the bucket pair-rule would otherwise match. Unmapped symbols
fail-open (eligible) to preserve legacy behavior on missing data.
"""
from datetime import datetime, timezone
from decimal import Decimal

from app.models import portfolio as models
from app.models.symbol_map import SymbolMap
from app.schemas import portfolio as schemas
from app.services import portfolio_service as svc


def _payload(*, symbol: str, tx_type: str, trade_date: datetime) -> schemas.TransactionCreate:
    return schemas.TransactionCreate(
        symbol=symbol,
        type=schemas.TransactionType(tx_type),
        quantity=10,
        price=Decimal("50.00"),
        trade_date=trade_date,
        fee=Decimal("0.00"),
        tax=Decimal("0.00"),
    )


def test_warrant_pair_same_day_stays_non_day_trade(db_session):
    db_session.add(
        SymbolMap(name="warrant", symbol="045378", market="TWSE", type="上市認購(售)權證")
    )
    db_session.commit()

    trade_day = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    buy = svc.create_transaction(db_session, _payload(symbol="045378", tx_type="BUY", trade_date=trade_day))
    sell = svc.create_transaction(db_session, _payload(symbol="045378", tx_type="SELL", trade_date=trade_day))

    db_session.refresh(buy)
    db_session.refresh(sell)
    assert buy.is_day_trade is False
    assert sell.is_day_trade is False


def test_otc_warrant_pair_same_day_stays_non_day_trade(db_session):
    db_session.add(
        SymbolMap(name="otc-warrant", symbol="738910", market="TPEX", type="上櫃認購(售)權證")
    )
    db_session.commit()

    trade_day = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    buy = svc.create_transaction(db_session, _payload(symbol="738910", tx_type="BUY", trade_date=trade_day))
    sell = svc.create_transaction(db_session, _payload(symbol="738910", tx_type="SELL", trade_date=trade_day))

    db_session.refresh(buy)
    db_session.refresh(sell)
    assert buy.is_day_trade is False
    assert sell.is_day_trade is False


def test_equity_pair_same_day_still_flips_day_trade(db_session):
    db_session.add(
        SymbolMap(name="台積電", symbol="2330", market="TWSE", type="股票")
    )
    db_session.commit()

    trade_day = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    buy = svc.create_transaction(db_session, _payload(symbol="2330", tx_type="BUY", trade_date=trade_day))
    sell = svc.create_transaction(db_session, _payload(symbol="2330", tx_type="SELL", trade_date=trade_day))

    db_session.refresh(buy)
    db_session.refresh(sell)
    assert buy.is_day_trade is True
    assert sell.is_day_trade is True


def test_unmapped_symbol_pair_same_day_flips_day_trade_fail_open(db_session):
    # No symbol_map row — fail-open preserves legacy behavior.
    trade_day = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    buy = svc.create_transaction(db_session, _payload(symbol="9999", tx_type="BUY", trade_date=trade_day))
    sell = svc.create_transaction(db_session, _payload(symbol="9999", tx_type="SELL", trade_date=trade_day))

    db_session.refresh(buy)
    db_session.refresh(sell)
    assert buy.is_day_trade is True
    assert sell.is_day_trade is True
