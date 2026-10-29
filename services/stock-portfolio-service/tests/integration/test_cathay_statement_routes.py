from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models import portfolio as models
from app.models.broker_account import BrokerAccount, BrokerEnum
from app.models.cathay_statement import CathayStatementDay
from app.services import broker_cathay_service

BASE = "/api/portfolio/imports/cathay-statement"
D = date(2026, 3, 9)


def _line(name, category, quantity, price, fee, tax=0, order_no="XY12300"):
    amount = int(Decimal(price) * quantity)
    net = -(amount + fee) if category in ("集買", "沖買") else amount - fee - tax
    return {"name": name, "category": category, "quantity": quantity, "price": price,
            "amount": str(amount), "fee": str(fee), "tax": str(tax), "net": str(net),
            "order_no": order_no, "points": False}


GOOD_LINES = [_line("範例電子", "集買", 1000, "12.3", 5)]
GOOD_HOLDINGS = [{"code": "9999", "name": "範例電子", "shares": 1000}]
OTHER_LINES = [_line("範例電子", "集買", 3000, "12.3", 15, order_no="XY12500")]
OTHER_HOLDINGS = [{"code": "9999", "name": "範例電子", "shares": 3000}]
HELD_LINES = [_line("未知公司", "集買", 1000, "10", 5, order_no="XY12600")]


def _body(*, lines=GOOD_LINES, holdings=GOOD_HOLDINGS, sha="a" * 64, trade_date=D):
    totals = {k: str(sum(Decimal(line[k]) for line in lines)) for k in ("amount", "fee", "tax", "net")}
    return {
        "source": {"drive_file_id": "drive-file-1", "sha256": sha, "file_name": f"{trade_date}.pdf"},
        "trade_date": trade_date.isoformat(), "settlement_date": "2026-03-11",
        "net_amount": totals["net"], "lines": copy.deepcopy(lines), "totals": totals,
        "holdings": copy.deepcopy(holdings), "warnings": [],
    }


def _held(sha="a" * 64, trade_date=D):
    return _body(lines=HELD_LINES, holdings=[], sha=sha, trade_date=trade_date)


def _post(client, body, **params):
    return client.post(BASE, json=body, params=params)


def _pending(client):
    return client.get(f"{BASE}/pending").json()


def _ack(client, trade_date=D):
    return client.post(f"{BASE}/days/{trade_date.isoformat()}/ack")


def _add_row(db, *, symbol="9999", name="範例電子", quantity=1000, price="12.3", fee="5",
             trade_date=None, type_="BUY"):
    trade_date = trade_date or datetime(D.year, D.month, D.day, tzinfo=timezone.utc)
    db.add(models.Transaction(
        symbol=symbol, market="TW", name=name, type=models.TransactionType(type_),
        quantity=quantity, price=Decimal(price), trade_date=trade_date, fee=Decimal(fee),
        tax=Decimal("0"),
    ))
    db.commit()


def _day(db, trade_date=D) -> CathayStatementDay | None:
    db.expire_all()
    return db.get(CathayStatementDay, trade_date)


@pytest.fixture(autouse=True)
def _env(monkeypatch, db_session):
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    monkeypatch.setitem(broker_cathay_service.NAME_TO_SYMBOL, "範例電子", ["9999"])
    db_session.add(BrokerAccount(
        broker=BrokerEnum.CATHAY, nickname="Cathay TWD", currency="TWD",
        opening_balance=Decimal("0"), opening_date=date(2026, 1, 1), is_active=True,
    ))
    db_session.commit()


def test_post_statement_imports(client):
    response = _post(client, _body())
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "imported"
    assert body["trade_date"] == "2026-03-09"
    assert body["errors"] == []
    assert body["counts"] == {"lines": 1, "created": 1}


def test_post_statement_422_buy_tax(client, db_session):
    body = _body()
    body["lines"][0]["tax"] = "5"
    assert _post(client, body).status_code == 422
    assert _day(db_session) is None


def test_post_statement_500_when_commit_rolled_back(client, db_session, monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("insert failed")

    monkeypatch.setattr(broker_cathay_service, "_insert_transaction", boom)
    response = _post(client, _body())
    assert response.status_code == 500
    body = response.json()
    assert body["errors"] == ["insert failed"]
    assert body["outcome"] == "imported"
    assert _day(db_session) is None


def test_pending_envelope_and_cap(client):
    for offset in range(51):
        day = date(2026, 1, 1) + timedelta(days=offset)
        assert _post(client, _held(trade_date=day)).json()["outcome"] == "held"
    data = _pending(client)
    assert data["total"] == 51
    assert data["truncated"] is True
    assert len(data["items"]) == 50
    dates = [item["trade_date"] for item in data["items"]]
    assert dates == sorted(dates)
    assert dates[0] == "2026-01-01"
    first = data["items"][0]
    assert set(first) == {"trade_date", "status", "reason", "since", "last_at"}
    assert first["status"] == "held"
    datetime.fromisoformat(first["since"])
    datetime.fromisoformat(first["last_at"])


def test_pending_reason_cut_to_40_chars(client, db_session):
    _post(client, _held())
    day = _day(db_session)
    day.last_reason = "x" * 80
    db_session.commit()
    assert _pending(client)["items"][0]["reason"] == "x" * 40


def test_pending_lists_any_date(client):
    old = date(2026, 1, 5)
    assert _post(client, _held(trade_date=old)).json()["outcome"] == "held"
    data = _pending(client)
    assert [i["trade_date"] for i in data["items"]] == ["2026-01-05"]
    assert data["truncated"] is False


def test_ack_identical_retry_stays_acked(client):
    assert _post(client, _held()).json()["outcome"] == "held"
    assert _ack(client).status_code == 200
    assert _pending(client)["total"] == 0
    assert _post(client, _held()).json()["outcome"] == "held"
    assert _pending(client)["total"] == 0
    assert _post(client, _held(sha="b" * 64)).json()["outcome"] == "held"
    assert [i["trade_date"] for i in _pending(client)["items"]] == [D.isoformat()]


def test_ack_conflict_keeps_acceptance(client, db_session):
    assert _post(client, _body()).json()["outcome"] == "imported"
    assert _post(client, _body(lines=OTHER_LINES, holdings=OTHER_HOLDINGS, sha="c" * 64)).json()["outcome"] == "conflict"
    assert [i["status"] for i in _pending(client)["items"]] == ["conflict"]
    response = _ack(client)
    assert response.status_code == 200
    assert response.json() == {"trade_date": "2026-03-09", "ack_status": "conflict"}
    assert _pending(client)["total"] == 0
    day = _day(db_session)
    assert day.accepted_kind == "imported"
    assert day.accepted_sha256 == "a" * 64


def test_pending_status_matrix(client, db_session):
    d_held, d_mismatch, d_conflict = date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)
    d_dup, d_imported, d_verified = date(2026, 3, 5), date(2026, 3, 6), date(2026, 3, 9)
    _post(client, _held(trade_date=d_held))
    _add_row(db_session, quantity=500, trade_date=datetime(2026, 3, 3, tzinfo=timezone.utc))
    assert _post(client, _body(trade_date=d_mismatch)).json()["outcome"] == "mismatch"
    _post(client, _body(trade_date=d_conflict))
    assert _post(client, _body(lines=OTHER_LINES, holdings=OTHER_HOLDINGS, sha="c" * 64,
                               trade_date=d_conflict)).json()["outcome"] == "conflict"
    _post(client, _body(trade_date=d_dup))
    assert _post(client, _body(trade_date=d_dup)).json()["outcome"] == "duplicate"
    assert _post(client, _body(trade_date=d_imported)).json()["outcome"] == "imported"
    _add_row(db_session, trade_date=datetime(2026, 3, 9, tzinfo=timezone.utc))
    assert _post(client, _body(trade_date=d_verified)).json()["outcome"] == "verified"

    items = _pending(client)["items"]
    assert [(i["trade_date"], i["status"]) for i in items] == [
        ("2026-03-02", "held"), ("2026-03-03", "mismatch"), ("2026-03-04", "conflict"),
    ]


def test_ack_same_sha_new_status_reappears(client, db_session):
    name = "未知公司"
    assert _post(client, _held()).json()["outcome"] == "held"
    assert _ack(client).status_code == 200
    assert _pending(client)["total"] == 0
    # The name becomes resolvable (a stored row on another date) and a differing CSV row sits on D.
    _add_row(db_session, symbol="9997", name=name, quantity=1,
             trade_date=datetime(2026, 2, 2, tzinfo=timezone.utc))
    _add_row(db_session, symbol="9997", name=name, quantity=500, price="10")
    assert _post(client, _held()).json()["outcome"] == "mismatch"
    assert [(i["trade_date"], i["status"]) for i in _pending(client)["items"]] == [("2026-03-09", "mismatch")]


def test_ack_held_then_import(client, db_session):
    assert _post(client, _held()).json()["outcome"] == "held"
    assert _ack(client).status_code == 200
    assert _post(client, _body(sha="d" * 64)).json()["outcome"] == "imported"
    assert _pending(client)["total"] == 0
    assert _day(db_session).accepted_kind == "imported"


def test_ack_404(client):
    assert _ack(client).status_code == 404  # no row
    _post(client, _body())
    assert _ack(client).status_code == 404  # row, nothing pending
    _post(client, _held(trade_date=date(2026, 3, 10)))
    assert _ack(client, date(2026, 3, 10)).status_code == 200
    assert _ack(client, date(2026, 3, 10)).status_code == 404  # already acked


def test_runs_latest(client):
    assert client.get(f"{BASE}/runs/latest").json() == {"latest": None, "latest_ok": None}

    def run(result, run_at, code=None, files_seen=1, posted=1):
        return client.post(f"{BASE}/runs", json={
            "run_at": run_at, "result": result, "code": code,
            "files_seen": files_seen, "posted": posted})

    assert run("failed", "2026-03-09T10:00:00+08:00", code="http_5xx").status_code == 204
    assert run("ok", "2026-03-09T11:00:00+08:00", files_seen=3, posted=2).status_code == 204
    data = client.get(f"{BASE}/runs/latest").json()
    assert data["latest"]["result"] == "ok"
    assert data["latest_ok"] == data["latest"]
    assert data["latest"]["files_seen"] == 3 and data["latest"]["posted"] == 2

    assert run("latched", "2026-03-09T12:00:00+08:00", code="decrypt").status_code == 204
    data = client.get(f"{BASE}/runs/latest").json()
    assert data["latest"]["result"] == "latched"
    assert data["latest"]["code"] == "decrypt"
    assert data["latest_ok"]["result"] == "ok"
    assert data["latest_ok"]["run_at"] != data["latest"]["run_at"]


@pytest.mark.parametrize("patch", [
    {"result": "weird"}, {"files_seen": -1}, {"posted": -1}, {"code": "x" * 65}, {"run_at": "not-a-time"},
    {"run_at": "2026-03-09T10:00:00"},
])
def test_runs_invalid_422(client, patch):
    body = {"run_at": "2026-03-09T10:00:00+08:00", "result": "ok", "code": None,
            "files_seen": 0, "posted": 0, **patch}
    assert client.post(f"{BASE}/runs", json=body).status_code == 422


@pytest.mark.parametrize("source", [
    {"sha256": "a" * 63}, {"sha256": "A" * 64}, {"sha256": "a" * 65}, {"drive_file_id": "d" * 129},
])
def test_post_statement_422_bad_source(client, source):
    body = _body()
    body["source"].update(source)
    assert _post(client, body).status_code == 422


def _conflict(client):
    assert _post(client, _body()).json()["outcome"] == "imported"
    other = _body(lines=OTHER_LINES, holdings=OTHER_HOLDINGS, sha="c" * 64)
    assert _post(client, other).json()["outcome"] == "conflict"


def _dup_new_drive_id(client):
    body = _body()
    body["source"]["drive_file_id"] = "drive-file-2"
    return _post(client, body).json()["outcome"]


def test_duplicate_does_not_clear_unacked_conflict(client, db_session):
    _conflict(client)
    assert _dup_new_drive_id(client) == "duplicate"
    day = _day(db_session)
    db_session.refresh(day)
    assert day.last_status == "conflict"
    assert day.last_sha256 == "c" * 64
    assert day.drive_file_id == "drive-file-1"
    assert [i["status"] for i in _pending(client)["items"]] == ["conflict"]


def test_duplicate_after_acked_conflict_records(client, db_session):
    _conflict(client)
    assert _ack(client).status_code == 200
    assert _dup_new_drive_id(client) == "duplicate"
    day = _day(db_session)
    db_session.refresh(day)
    assert day.last_status == "duplicate"
    assert _pending(client)["total"] == 0
