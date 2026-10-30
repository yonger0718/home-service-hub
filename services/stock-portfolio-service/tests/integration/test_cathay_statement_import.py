from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models import portfolio as models
from app.models.broker_account import BrokerAccount, BrokerEnum
from app.models.cash_transaction import CashTransaction
from app.models.cathay_statement import CathayStatementDay
from app.schemas.cathay_statement import StatementPayload
from app.services import broker_cathay_service, broker_cathay_statement_service, cash_account_service
from app.services.broker_cathay_statement_service import import_statement

D = date(2026, 3, 9)
D_START = datetime(2026, 3, 9, tzinfo=timezone.utc)


def _line(name, category, quantity, price, fee, tax=0, order_no="XY12300"):
    amount = int(Decimal(price) * quantity)
    if category in ("集買", "沖買"):
        net = -(amount + fee)
    else:
        net = amount - fee - tax
    return {"name": name, "category": category, "quantity": quantity, "price": price,
            "amount": str(amount), "fee": str(fee), "tax": str(tax), "net": str(net),
            "order_no": order_no, "points": False}


DEFAULT_LINES = [
    _line("範例電子", "集買", 1000, "12.3", 5, order_no="XY12300"),
    _line("範例化學", "集買", 2000, "25", 21, order_no="XY12400"),
]
DEFAULT_HOLDINGS = [
    {"code": "9999", "name": "範例電子", "shares": 1000},
    {"code": "9998", "name": "範例化學", "shares": 2000},
]


def _payload(*, lines=None, holdings=None, sha="a" * 64, warnings=None) -> StatementPayload:
    lines = DEFAULT_LINES if lines is None else lines
    totals = {
        key: str(sum(Decimal(line[key]) for line in lines))
        for key in ("amount", "fee", "tax", "net")
    }
    return StatementPayload(
        source={"drive_file_id": "drive-file-1", "sha256": sha, "file_name": "2026-03-09.pdf"},
        trade_date=D.isoformat(),
        settlement_date="2026-03-11",
        net_amount=totals["net"],
        lines=lines,
        totals=totals,
        holdings=DEFAULT_HOLDINGS if holdings is None else holdings,
        warnings=warnings or [],
    )


@pytest.fixture(autouse=True)
def _env(monkeypatch, db_session) -> None:
    # 範例電子 resolves only through the holdings table; 範例化學 through the name map.
    monkeypatch.setitem(broker_cathay_service.NAME_TO_SYMBOL, "範例化學", ["9998"])
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    db_session.add(BrokerAccount(
        broker=BrokerEnum.CATHAY, nickname="Cathay TWD", currency="TWD",
        opening_balance=Decimal("0"), opening_date=date(2026, 1, 1), is_active=True,
    ))
    db_session.commit()


def _add_row(db, *, symbol="9999", name="範例電子", type_="BUY", quantity=1000, price="12.3",
             fee="5", tax="0", marker=None, broker=None, trade_date=D_START) -> models.Transaction:
    tx = models.Transaction(
        symbol=symbol, market="TW", name=name, type=models.TransactionType(type_),
        quantity=quantity, price=Decimal(price), trade_date=trade_date, fee=Decimal(fee),
        tax=Decimal(tax), broker=broker, broker_day_trade_marker=marker,
    )
    db.add(tx)
    db.commit()
    return tx


def _day(db) -> CathayStatementDay | None:
    db.expire_all()
    return db.get(CathayStatementDay, D)


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None)


def test_case4_inserts_and_owns(db_session):
    result = import_statement(db_session, _payload(), dry_run=False)

    assert result.outcome == "imported"
    assert result.counts == {"lines": 2, "created": 2}
    assert result.errors == []
    rows = db_session.query(models.Transaction).order_by(models.Transaction.id).all()
    assert [r.id for r in rows] == result.created_ids
    mapped = broker_cathay_statement_service.map_statement(db_session, _payload())
    assert [r.import_fingerprint for r in rows] == [r.fingerprint for r in mapped.rows]
    assert all(r.broker is None for r in rows)
    for r in rows:
        assert db_session.query(CashTransaction).filter_by(related_transaction_id=r.id).count() == 2
    day = _day(db_session)
    assert day.accepted_kind == "imported"
    assert day.accepted_sha256 == "a" * 64
    assert day.last_status == "imported"
    assert day.drive_file_id == "drive-file-1"
    assert len(day.canonical_lines) == 2


def test_case1_same_sha_duplicate(db_session):
    import_statement(db_session, _payload(), dry_run=False)
    result = import_statement(db_session, _payload(), dry_run=False)

    assert result.outcome == "duplicate"
    assert result.counts["created"] == 0
    assert db_session.query(models.Transaction).count() == 2
    day = _day(db_session)
    assert day.accepted_kind == "imported"
    assert day.last_status == "duplicate"


def test_case2_corrected_sha_conflict_keeps_rows(db_session):
    import_statement(db_session, _payload(), dry_run=False)
    before = [(r.id, r.import_fingerprint, r.fee) for r in db_session.query(models.Transaction).all()]
    accepted_lines = _day(db_session).canonical_lines
    changed = [_line("範例電子", "集買", 1000, "12.3", 6, order_no="XY12300"), DEFAULT_LINES[1]]

    result = import_statement(db_session, _payload(lines=changed, sha="b" * 64), dry_run=False)

    assert result.outcome == "conflict"
    db_session.expire_all()
    after = [(r.id, r.import_fingerprint, r.fee) for r in db_session.query(models.Transaction).all()]
    assert after == before
    day = _day(db_session)
    assert day.accepted_kind == "imported"
    assert day.accepted_sha256 == "a" * 64
    assert day.canonical_lines == accepted_lines
    assert day.last_status == "conflict"
    assert day.last_sha256 == "b" * 64
    assert day.last_detail["accepted_lines"] == accepted_lines
    assert [line["fee"] for line in day.last_detail["statement_lines"]] == ["6", "21"]


def test_case3_held_unresolved_name(db_session):
    result = import_statement(db_session, _payload(holdings=[]), dry_run=False)

    assert result.outcome == "held"
    assert result.reason == "unresolved_symbol:範例電子"
    assert result.holdings_diff == []
    assert db_session.query(models.Transaction).count() == 0
    day = _day(db_session)
    assert day.accepted_kind is None
    assert day.last_status == "held"
    assert day.last_reason == "unresolved_symbol:範例電子"


def test_case3_unsupported_holds(db_session):
    result = import_statement(
        db_session, _payload(lines=[], warnings=["unsupported_category"]), dry_run=False
    )

    assert result.outcome == "held"
    assert result.reason == "unsupported_category"
    assert result.counts == {"lines": 0, "created": 0}
    assert db_session.query(models.Transaction).count() == 0
    assert db_session.query(CashTransaction).count() == 0
    day = _day(db_session)
    assert day.accepted_kind is None
    assert day.last_reason == "unsupported_category"


def test_case3_held_then_fixed_imports(db_session):
    assert import_statement(db_session, _payload(holdings=[]), dry_run=False).outcome == "held"
    _add_row(db_session, trade_date=datetime(2026, 3, 2, tzinfo=timezone.utc), broker="TW_MANUAL")

    result = import_statement(db_session, _payload(holdings=[]), dry_run=False)

    assert result.outcome == "imported"
    assert _day(db_session).accepted_kind == "imported"
    assert db_session.query(models.Transaction).count() == 3


def test_case5_csv_first_verifies(db_session):
    _add_row(db_session, quantity=600, fee="3")
    _add_row(db_session, quantity=400, fee="2", broker="TW_CATHAY")
    _add_row(db_session, symbol="9998", name="範例化學", quantity=2000, price="25", fee="21")

    result = import_statement(db_session, _payload(), dry_run=False)

    assert result.outcome == "verified"
    assert result.counts == {"lines": 2, "created": 0}
    assert result.holdings_diff == []
    assert db_session.query(models.Transaction).count() == 3
    day = _day(db_session)
    assert day.accepted_kind == "verified"
    assert len(day.canonical_lines) == 2


DAY_TRADE_LINES = [
    _line("範例電子", "沖買", 1000, "12.3", 5, order_no="XY12300"),
    _line("範例電子", "沖賣", 1000, "12.5", 5, tax=18, order_no="XY12400"),
]
DAY_TRADE_HOLDINGS = [{"code": "9999", "name": "範例電子", "shares": 0}]


def test_case5_marker_differs_mismatch(db_session):
    buy = _add_row(db_session)
    sell = _add_row(db_session, type_="SELL", price="12.5", tax="18")
    payload = _payload(lines=DAY_TRADE_LINES, holdings=DAY_TRADE_HOLDINGS)

    result = import_statement(db_session, payload, dry_run=False)

    assert result.outcome == "mismatch"
    day = _day(db_session)
    assert day.accepted_kind is None
    assert day.last_status == "mismatch"
    assert {g["marker"] for g in day.last_detail["portfolio_groups"]} == {None}
    assert {g["marker"] for g in day.last_detail["statement_groups"]} == {"沖買", "沖賣"}

    buy = db_session.get(models.Transaction, buy.id)
    sell = db_session.get(models.Transaction, sell.id)
    buy.broker_day_trade_marker = "沖買"
    sell.broker_day_trade_marker = "沖賣"
    db_session.commit()

    again = import_statement(db_session, payload, dry_run=False)

    assert again.outcome == "verified"
    assert _day(db_session).accepted_kind == "verified"
    assert db_session.query(models.Transaction).count() == 2


def test_tw_manual_row_ignored(db_session):
    _add_row(db_session, broker="TW_MANUAL")

    result = import_statement(db_session, _payload(), dry_run=False)

    assert result.outcome == "imported"
    assert db_session.query(models.Transaction).count() == 3


def test_dry_run_writes_nothing(db_session):
    result = import_statement(db_session, _payload(), dry_run=True)

    assert result.outcome == "imported"
    assert result.created_ids == []
    assert result.holdings_diff == []
    assert db_session.query(models.Transaction).count() == 0
    assert db_session.query(CashTransaction).count() == 0
    assert _day(db_session) is None


def test_failure_rolls_back_ownership(db_session, monkeypatch):
    manual = _add_row(db_session, broker="TW_MANUAL")
    assert manual.is_day_trade is False
    real_sync = cash_account_service.sync_transaction_cash_legs
    calls = []

    def flaky_sync(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise ValueError("cash leg failed")
        return real_sync(*args, **kwargs)

    monkeypatch.setattr(cash_account_service, "sync_transaction_cash_legs", flaky_sync)
    lines = [
        _line("範例電子", "沖賣", 1000, "12.5", 5, tax=18, order_no="XY12400"),
        _line("範例化學", "集買", 2000, "25", 21, order_no="XY12500"),
    ]

    result = import_statement(db_session, _payload(lines=lines), dry_run=False)

    assert len(calls) == 2
    assert result.errors == ["cash leg failed"]
    assert result.created_ids == []
    assert result.counts["created"] == 0
    db_session.expire_all()
    assert db_session.query(models.Transaction).count() == 1
    assert db_session.get(models.Transaction, manual.id).is_day_trade is False
    assert db_session.query(CashTransaction).count() == 0
    assert _day(db_session) is None


def test_verified_then_same_sha_duplicate(db_session):
    _add_row(db_session)
    _add_row(db_session, symbol="9998", name="範例化學", quantity=2000, price="25", fee="21")
    assert import_statement(db_session, _payload(), dry_run=False).outcome == "verified"
    accepted_at = _day(db_session).accepted_at

    result = import_statement(db_session, _payload(), dry_run=False)

    assert result.outcome == "duplicate"
    assert db_session.query(models.Transaction).count() == 2
    assert db_session.query(CashTransaction).count() == 0
    day = _day(db_session)
    assert day.accepted_kind == "verified"
    assert day.accepted_at == accepted_at


def test_since_kept_on_repeat_and_reset_on_change(db_session, monkeypatch):
    t1 = datetime(2026, 3, 10, 1, tzinfo=timezone.utc)
    clock = iter([t1, t1 + timedelta(hours=1), t1 + timedelta(hours=2)])
    monkeypatch.setattr(broker_cathay_statement_service, "_now", lambda: next(clock))

    import_statement(db_session, _payload(holdings=[]), dry_run=False)
    import_statement(db_session, _payload(holdings=[]), dry_run=False)
    day = _day(db_session)
    assert day.last_status == "held"
    assert _naive(day.since) == _naive(t1)
    assert _naive(day.last_at) == _naive(t1 + timedelta(hours=1))

    _add_row(db_session, quantity=999)
    import_statement(db_session, _payload(), dry_run=False)
    day = _day(db_session)
    assert day.last_status == "mismatch"
    assert _naive(day.since) == _naive(t1 + timedelta(hours=2))
    assert _naive(day.last_at) == _naive(t1 + timedelta(hours=2))


def test_holdings_diff_in_response_and_detail(db_session):
    holdings = [DEFAULT_HOLDINGS[0], {"code": "9998", "name": "範例化學", "shares": 1500}]

    result = import_statement(db_session, _payload(holdings=holdings), dry_run=False)

    assert result.outcome == "imported"
    expected = [{"symbol": "9998", "statement_shares": 1500, "portfolio_shares": 2000}]
    assert result.holdings_diff == expected
    assert _day(db_session).last_detail["holdings_diff"] == expected

    held = import_statement(
        db_session,
        _payload(lines=[], warnings=["unsupported_category"]).model_copy(
            update={"trade_date": date(2026, 3, 10)}
        ),
        dry_run=False,
    )
    assert held.outcome == "held"
    assert held.holdings_diff == []


def test_holdings_diff_empty_table(db_session):
    _add_row(db_session, symbol="9997", name="範例航運", quantity=3000, price="10",
             trade_date=datetime(2026, 3, 2, tzinfo=timezone.utc))
    lines = [DEFAULT_LINES[1]]

    result = import_statement(db_session, _payload(lines=lines, holdings=[]), dry_run=False)

    assert result.outcome == "imported"
    assert result.holdings_diff == [
        {"symbol": "9997", "statement_shares": 0, "portfolio_shares": 3000},
        {"symbol": "9998", "statement_shares": 0, "portfolio_shares": 2000},
    ]


def _points_lines():
    points_line = _line("範例電子", "集買", 1000, "12.3", 5, order_no="XY12300")
    points_line["points"] = True
    return [points_line, DEFAULT_LINES[1]]


def test_points_line_imports_without_cash_legs(db_session):
    result = import_statement(db_session, _payload(lines=_points_lines()), dry_run=False)

    assert result.outcome == "imported"
    points_row, cash_row = (
        db_session.query(models.Transaction).order_by(models.Transaction.id).all()
    )
    assert points_row.paid_with_points is True
    assert points_row.fee == Decimal("5")
    assert db_session.query(CashTransaction).filter_by(related_transaction_id=points_row.id).count() == 0
    assert cash_row.paid_with_points is False
    assert db_session.query(CashTransaction).filter_by(related_transaction_id=cash_row.id).count() == 2


def test_points_line_verifies_against_csv_rows(db_session):
    _add_row(db_session, quantity=1000, fee="5")
    _add_row(db_session, symbol="9998", name="範例化學", quantity=2000, price="25", fee="21")

    result = import_statement(db_session, _payload(lines=_points_lines()), dry_run=False)

    assert result.outcome == "verified"
    assert db_session.query(models.Transaction).count() == 2
