"""GET /api/portfolio/futures/months and /detail (期貨明細)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.models.sinopac_futures_statement import (
    DAY_AMOUNTS,
    MONTH_AMOUNTS,
    FuturesStatementDay,
    FuturesStatementMonth,
)
from app.schemas.futures_detail import CloseOut, DepositOut, FillOut, PositionOut

NOW = datetime(2026, 4, 1, tzinfo=timezone.utc)
FILL_KEYS = set(FillOut.model_fields)


def _day(db, d, *, deposits_net="0", realized="0", premium="0", fees="0", findings=None, acked=False,
         fills=None, fills_total=None, deposits=None, positions=None, open_positions=0):
    row = FuturesStatementDay(
        statement_date=d, accepted_sha256="a" * 64, accepted_at=NOW, open_positions=open_positions,
        findings=findings, fills=fills, fills_total=fills_total, deposits=deposits,
        positions=positions, **{k: Decimal("0") for k in DAY_AMOUNTS},
    )
    if acked:  # a findings ack is stored as ack_findings_sha == findings_sha
        row.findings_sha = row.ack_findings_sha = "c" * 64
    row.deposits_net = Decimal(deposits_net)
    row.futures_realized = Decimal(realized)
    row.premium_net = Decimal(premium)
    row.fees_and_tax = Decimal(fees)
    db.add(row)
    db.commit()


def _month(db, month_end, *, b="0", k="0", g="0", h="0", findings=None, activity_dates=None, open_positions=0):
    row = FuturesStatementMonth(
        month=month_end.replace(day=1), month_end=month_end, accepted_sha256="b" * 64, accepted_at=NOW,
        open_positions=open_positions, findings=findings, activity_dates=activity_dates,
        **{c: Decimal("0") for c in MONTH_AMOUNTS},
    )
    row.b, row.k, row.g, row.h = Decimal(b), Decimal(k), Decimal(g), Decimal(h)
    db.add(row)
    db.commit()


def _detail(client, month="2026-03"):
    response = client.get("/api/portfolio/futures/detail", params={"month": month})
    assert response.status_code == 200
    return response.json()


def _fill(**over):
    base = {
        "order_no": "sA1B2-01", "product": "範例期貨", "month": "2026/03", "strike": None, "right": None,
        "action": "平賣", "qty": 1, "price": "20600", "tax": "10", "fee": "5", "pnl": "100", "closes": [],
    }
    return {**base, **over}


def test_months_newest_first_union(client, db_session):
    assert client.get("/api/portfolio/futures/months").json() == {"months": []}
    _day(db_session, date(2026, 1, 15))
    _month(db_session, date(2026, 2, 27))
    _day(db_session, date(2026, 3, 5))
    assert client.get("/api/portfolio/futures/months").json() == {"months": ["2026-03", "2026-02", "2026-01"]}


def test_detail_monthly_source(client, db_session):
    _month(db_session, date(2026, 3, 31), k="50", g="-12", h="30", b="200",
           activity_dates=["2026-03-05", "2026-03-09"])
    _day(db_session, date(2026, 3, 5))
    assert _detail(client)["summary"] == {
        "futures_realized": "50.0000", "premium_net": "-12.0000", "fees_and_tax": "30.0000",
        "deposits_net": "200.0000", "source": "monthly", "complete": True, "reason": None, "missing_days": 1,
    }


def test_detail_monthly_only_no_daily(client, db_session):
    dates = ["2026-02-03", "2026-02-10", "2026-02-24"]
    _month(db_session, date(2026, 2, 27), activity_dates=dates)
    body = _detail(client, "2026-02")
    assert body["summary"]["source"] == "monthly"
    assert body["days"] == []
    assert body["summary"]["missing_days"] == len(dates)


def _two_days(db, findings=None, acked=False):
    _day(db, date(2026, 3, 5), realized="30", premium="-5", fees="4", deposits_net="200")
    _day(db, date(2026, 3, 9), realized="20", premium="0", fees="2", deposits_net="0",
         findings=findings, acked=acked)


def test_detail_daily_sum_complete(client, db_session):
    _two_days(db_session)
    assert _detail(client)["summary"] == {
        "futures_realized": "50.0000", "premium_net": "-5.0000", "fees_and_tax": "6.0000",
        "deposits_net": "200.0000", "source": "daily_sum", "complete": True, "reason": None, "missing_days": 0,
    }


def test_detail_daily_sum_prev_finding_incomplete(client, db_session):
    _two_days(db_session, findings=[{"code": "prev", "dates": ["2026-03-05"], "letters": []}], acked=True)
    summary = _detail(client)["summary"]
    assert summary["complete"] is False
    assert summary["reason"] == "findings"


def test_detail_days_newest_first_and_fill_mapping(client, db_session):
    _day(db_session, date(2026, 3, 9), fills=[
        _fill(closes=[{"date": "2026-03-02", "order_no": "sA1B2-00", "qty": 1, "price": "20500"}]),
        _fill(order_no="sA1B2-02", product="範例選", strike="21000", right="Call", action="新買",
              price="6", pnl="-300"),
    ], fills_total={"tax": "20", "fee": "10", "pnl": "-200"})
    _day(db_session, date(2026, 3, 5), fills=[], fills_total=None,
         deposits=[{"seq": "1", "direction": "in", "amount": "200"}])
    days = _detail(client)["days"]
    assert [d["date"] for d in days] == ["2026-03-09", "2026-03-05"]
    future, option = days[0]["fills"]
    assert (future["kind"], option["kind"]) == ("future", "option")
    assert future["strike"] is None
    assert option["pnl"] == "-300.0000"
    assert option["price"] == "6"
    assert option["strike"] == "21000"
    assert future["closes"] == [{"date": "2026-03-02", "qty": 1, "price": "20500"}]
    assert days[0]["fills_total"] == {"tax": "20.0000", "fee": "10.0000", "pnl": "-200.0000"}
    assert days[1] == {
        "date": "2026-03-05", "no_trade": True, "fills": [], "fills_total": None,
        "deposits": [{"direction": "in", "amount": "200.0000"}],
    }


def test_detail_drops_extra_json_keys(client, db_session):
    _day(db_session, date(2026, 3, 5),
         fills=[_fill(note="x", closes=[{"date": "2026-03-02", "order_no": "sA1B2-00", "qty": 1, "price": "1"}])],
         deposits=[{"seq": "9", "direction": "out", "amount": "5"}])
    day = _detail(client)["days"][0]
    assert set(day["fills"][0]) == FILL_KEYS
    assert set(day["fills"][0]["closes"][0]) == set(CloseOut.model_fields)
    assert set(day["deposits"][0]) == set(DepositOut.model_fields)


def test_detail_null_json_columns(client, db_session):
    _day(db_session, date(2026, 3, 5), fills=None, deposits=None, fills_total=None)
    day = _detail(client)["days"][0]
    assert day["no_trade"] is True
    assert day["fills"] == []
    assert day["deposits"] == []
    assert day["fills_total"] is None


def test_detail_404_and_422(client):
    url = "/api/portfolio/futures/detail"
    assert client.get(url, params={"month": "2025-01"}).status_code == 404
    assert client.get(url, params={"month": "2026-13"}).status_code == 422
    assert client.get(url, params={"month": "202603"}).status_code == 422


def _pos(**over):
    base = {
        "product": "範例期貨", "month": "2026/03", "strike": None, "right": None, "opened": "2026-03-02",
        "side": "買", "entry_price": "20500", "mark_price": "20600", "qty": 1, "initial_margin": "100000",
        "premium_value": "0", "unrealized": "100",
    }
    return {**base, **over}


def test_positions_from_last_daily_of_month(client, db_session):
    _day(db_session, date(2026, 3, 5), positions=[_pos()])
    _day(db_session, date(2026, 3, 20), positions=[
        _pos(), _pos(product="範例選", strike="21000", right="Call", side="賣", entry_price="6",
                     mark_price="4", premium_value="-200", unrealized="100"),
    ])
    positions = _detail(client)["positions"]
    assert positions["date"] == "2026-03-20"
    assert len(positions["items"]) == 2
    assert positions["month_end_may_differ"] is False
    future, option = positions["items"]
    assert set(future) == set(PositionOut.model_fields)
    assert future["strike"] is None
    assert (future["unrealized"], future["premium_value"]) == ("100.0000", "0.0000")
    assert (option["strike"], option["right"], option["premium_value"]) == ("21000", "Call", "-200.0000")
    assert option["entry_price"] == "6"


def test_positions_carried_from_earlier_month(client, db_session):
    _day(db_session, date(2026, 1, 28), positions=[_pos()])
    _month(db_session, date(2026, 2, 27), open_positions=1)
    positions = _detail(client, "2026-02")["positions"]
    assert positions["date"] == "2026-01-28"
    assert positions["month_end_may_differ"] is False


def test_positions_month_end_may_differ(client, db_session):
    _day(db_session, date(2026, 1, 28), positions=[_pos()])
    _month(db_session, date(2026, 2, 27), open_positions=0)
    assert _detail(client, "2026-02")["positions"]["month_end_may_differ"] is True


def test_positions_ignore_daily_after_month_end(client, db_session):
    _day(db_session, date(2026, 2, 25), positions=[_pos()])
    _day(db_session, date(2026, 2, 27), positions=[_pos(), _pos()])
    _month(db_session, date(2026, 2, 26), open_positions=1)
    assert _detail(client, "2026-02")["positions"]["date"] == "2026-02-25"


def test_positions_null_without_earlier_daily(client, db_session):
    _month(db_session, date(2026, 2, 27))
    assert _detail(client, "2026-02")["positions"] is None


def test_positions_null_json(client, db_session):
    _day(db_session, date(2026, 3, 5), positions=None)
    assert _detail(client)["positions"] == {"date": "2026-03-05", "month_end_may_differ": False, "items": []}
