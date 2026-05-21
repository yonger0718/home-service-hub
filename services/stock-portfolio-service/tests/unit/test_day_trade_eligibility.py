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


def _payload(
    *, symbol: str, tx_type: str, trade_date: datetime, quantity: int = 1000
) -> schemas.TransactionCreate:
    return schemas.TransactionCreate(
        symbol=symbol,
        type=schemas.TransactionType(tx_type),
        quantity=quantity,
        price=Decimal("50.00"),
        trade_date=trade_date,
        fee=Decimal("0.00"),
        tax=Decimal("0.00"),
    )


def _set_marker_and_recompute(db_session, tx, marker: str) -> None:
    tx.broker_day_trade_marker = marker
    svc._recompute_day_trade_flags(
        db_session,
        tx.symbol,
        svc._trade_calendar_date(tx.trade_date),
    )
    db_session.commit()


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


def test_marker_pair_same_day_flips_day_trade(db_session):
    db_session.add(
        SymbolMap(name="台積電", symbol="2330", market="TWSE", type="股票")
    )
    db_session.commit()

    trade_day = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    buy = svc.create_transaction(db_session, _payload(symbol="2330", tx_type="BUY", trade_date=trade_day))
    sell = svc.create_transaction(db_session, _payload(symbol="2330", tx_type="SELL", trade_date=trade_day))
    buy.broker_day_trade_marker = "沖買"
    sell.broker_day_trade_marker = "沖賣"
    svc._recompute_day_trade_flags(db_session, "2330", trade_day.date())
    db_session.commit()

    db_session.refresh(buy)
    db_session.refresh(sell)
    assert buy.is_day_trade is True
    assert sell.is_day_trade is True


def test_marker_only_on_buy_still_flips_bucket(db_session):
    db_session.add(
        SymbolMap(name="台積電", symbol="2330", market="TWSE", type="股票")
    )
    db_session.commit()

    trade_day = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    buy = svc.create_transaction(db_session, _payload(symbol="2330", tx_type="BUY", trade_date=trade_day))
    _set_marker_and_recompute(db_session, buy, "沖買")

    db_session.refresh(buy)
    assert buy.is_day_trade is True


def test_marker_on_warrant_rejected_by_eligibility_gate(db_session):
    db_session.add(
        SymbolMap(name="warrant", symbol="045378", market="TWSE", type="上市認購(售)權證")
    )
    db_session.commit()

    trade_day = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    buy = svc.create_transaction(db_session, _payload(symbol="045378", tx_type="BUY", trade_date=trade_day))
    sell = svc.create_transaction(db_session, _payload(symbol="045378", tx_type="SELL", trade_date=trade_day))
    buy.broker_day_trade_marker = "沖買"
    sell.broker_day_trade_marker = "沖賣"
    svc._recompute_day_trade_flags(db_session, "045378", trade_day.date())
    db_session.commit()

    db_session.refresh(buy)
    db_session.refresh(sell)
    assert buy.is_day_trade is False
    assert sell.is_day_trade is False


def test_odd_lot_pair_with_marker_stays_false(db_session):
    db_session.add(
        SymbolMap(name="力積電", symbol="6491", market="TWSE", type="股票")
    )
    db_session.commit()

    trade_day = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    buy = svc.create_transaction(
        db_session,
        _payload(symbol="6491", tx_type="BUY", trade_date=trade_day, quantity=25),
    )
    sell = svc.create_transaction(
        db_session,
        _payload(symbol="6491", tx_type="SELL", trade_date=trade_day, quantity=25),
    )
    buy.broker_day_trade_marker = "沖買"
    sell.broker_day_trade_marker = "沖賣"
    svc._recompute_day_trade_flags(db_session, "6491", trade_day.date())
    db_session.commit()

    db_session.refresh(buy)
    db_session.refresh(sell)
    assert buy.is_day_trade is False
    assert sell.is_day_trade is False


def test_mixed_odd_lot_and_board_lot_bucket_only_board_flips(db_session):
    db_session.add(
        SymbolMap(name="台積電", symbol="2330", market="TWSE", type="股票")
    )
    db_session.commit()

    trade_day = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    board_buy = svc.create_transaction(
        db_session,
        _payload(symbol="2330", tx_type="BUY", trade_date=trade_day, quantity=1000),
    )
    board_sell = svc.create_transaction(
        db_session,
        _payload(symbol="2330", tx_type="SELL", trade_date=trade_day, quantity=1000),
    )
    odd_buy = svc.create_transaction(
        db_session,
        _payload(symbol="2330", tx_type="BUY", trade_date=trade_day, quantity=42),
    )

    db_session.refresh(board_buy)
    db_session.refresh(board_sell)
    db_session.refresh(odd_buy)
    assert board_buy.is_day_trade is True
    assert board_sell.is_day_trade is True
    assert odd_buy.is_day_trade is False


def test_board_lot_alone_with_odd_lot_opposing_side_stays_false(db_session):
    db_session.add(
        SymbolMap(name="台積電", symbol="2330", market="TWSE", type="股票")
    )
    db_session.commit()

    trade_day = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    board_buy = svc.create_transaction(
        db_session,
        _payload(symbol="2330", tx_type="BUY", trade_date=trade_day, quantity=1000),
    )
    odd_sell = svc.create_transaction(
        db_session,
        _payload(symbol="2330", tx_type="SELL", trade_date=trade_day, quantity=42),
    )

    db_session.refresh(board_buy)
    db_session.refresh(odd_sell)
    assert board_buy.is_day_trade is False
    assert odd_sell.is_day_trade is False


def test_no_marker_equity_pair_falls_back_to_heuristic(db_session):
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


def test_no_marker_no_pair_stays_false(db_session):
    db_session.add(
        SymbolMap(name="台積電", symbol="2330", market="TWSE", type="股票")
    )
    db_session.commit()

    trade_day = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    buy = svc.create_transaction(db_session, _payload(symbol="2330", tx_type="BUY", trade_date=trade_day))

    db_session.refresh(buy)
    assert buy.is_day_trade is False


def test_unmapped_symbol_pair_same_day_flips_day_trade_fail_open(db_session):
    # No symbol_map row — fail-open preserves legacy behavior.
    trade_day = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    buy = svc.create_transaction(db_session, _payload(symbol="9999", tx_type="BUY", trade_date=trade_day))
    sell = svc.create_transaction(db_session, _payload(symbol="9999", tx_type="SELL", trade_date=trade_day))

    db_session.refresh(buy)
    db_session.refresh(sell)
    assert buy.is_day_trade is True
    assert sell.is_day_trade is True
