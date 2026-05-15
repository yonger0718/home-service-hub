"""Paged + filtered list endpoints for /transactions and /dividends."""

from datetime import datetime
from decimal import Decimal


def _seed_transactions(db_session):
    from app.models import portfolio as models

    rows = [
        models.Transaction(
            symbol="0050",
            name="元大台灣50",
            type=models.TransactionType.BUY,
            quantity=10,
            price=Decimal("100.00"),
            fee=Decimal("0.00"),
            tax=Decimal("0.00"),
            trade_date=datetime(2026, 1, 1, 9, 0),
        ),
        models.Transaction(
            symbol="0050",
            name="元大台灣50",
            type=models.TransactionType.BUY,
            quantity=12,
            price=Decimal("101.00"),
            fee=Decimal("0.00"),
            tax=Decimal("0.00"),
            trade_date=datetime(2026, 1, 2, 9, 0),
        ),
        models.Transaction(
            symbol="0050",
            name="元大台灣50",
            type=models.TransactionType.SELL,
            quantity=5,
            price=Decimal("105.00"),
            fee=Decimal("0.00"),
            tax=Decimal("0.00"),
            trade_date=datetime(2026, 2, 15, 9, 0),
        ),
        models.Transaction(
            symbol="0056",
            name="元大高股息",
            type=models.TransactionType.BUY,
            quantity=5,
            price=Decimal("30.00"),
            fee=Decimal("0.00"),
            tax=Decimal("0.00"),
            trade_date=datetime(2026, 1, 3, 9, 0),
        ),
        models.Transaction(
            symbol="2330",
            name="台積電",
            type=models.TransactionType.BUY,
            quantity=2,
            price=Decimal("600.00"),
            fee=Decimal("0.00"),
            tax=Decimal("0.00"),
            trade_date=datetime(2025, 12, 20, 9, 0),
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()


def _seed_dividends(db_session):
    from app.models import portfolio as models

    rows = [
        models.Dividend(
            symbol="0050",
            amount=Decimal("100.00"),
            ex_dividend_date=datetime(2026, 2, 1, 9, 0),
            received_date=datetime(2026, 2, 10, 9, 0),
            source="manual",
        ),
        models.Dividend(
            symbol="0050",
            amount=Decimal("110.00"),
            ex_dividend_date=datetime(2026, 3, 1, 9, 0),
            received_date=datetime(2026, 3, 10, 9, 0),
            source="auto:TWT49U",
        ),
        models.Dividend(
            symbol="0056",
            amount=Decimal("50.00"),
            ex_dividend_date=datetime(2026, 4, 1, 9, 0),
            received_date=datetime(2026, 4, 10, 9, 0),
            source="csv",
        ),
        models.Dividend(
            symbol="2330",
            amount=Decimal("200.00"),
            ex_dividend_date=datetime(2025, 11, 1, 9, 0),
            received_date=datetime(2025, 11, 20, 9, 0),
            source="manual",
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()


# ---------- transactions ----------


def test_transactions_default_response_is_paged_wrapper(client, db_session):
    _seed_transactions(db_session)

    response = client.get("/api/portfolio/transactions")
    assert response.status_code == 200
    body = response.json()
    assert "items" in body and "total" in body
    assert body["total"] == 5
    # default sort trade_date:desc → 2026-02-15, 2026-01-03, 2026-01-02, 2026-01-01, 2025-12-20
    assert [row["trade_date"][:10] for row in body["items"]] == [
        "2026-02-15",
        "2026-01-03",
        "2026-01-02",
        "2026-01-01",
        "2025-12-20",
    ]


def test_transactions_empty_filter_still_includes_total(client, db_session):
    _seed_transactions(db_session)
    response = client.get("/api/portfolio/transactions", params={"symbol": "NOMATCH"})
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


def test_transactions_total_reflects_filter_not_page(client, db_session):
    _seed_transactions(db_session)
    response = client.get(
        "/api/portfolio/transactions", params={"symbol": "0050", "limit": 1, "offset": 0}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 1


def test_transactions_offset_pagination_no_overlap(client, db_session):
    _seed_transactions(db_session)
    page1 = client.get("/api/portfolio/transactions", params={"limit": 2, "offset": 0}).json()
    page2 = client.get("/api/portfolio/transactions", params={"limit": 2, "offset": 2}).json()
    page3 = client.get("/api/portfolio/transactions", params={"limit": 2, "offset": 4}).json()

    ids = [r["id"] for r in page1["items"]] + [r["id"] for r in page2["items"]] + [r["id"] for r in page3["items"]]
    assert len(ids) == 5
    assert len(set(ids)) == 5  # no overlap, no skip


def test_transactions_default_limit_is_25(client, db_session):
    from app.models import portfolio as models

    rows = [
        models.Transaction(
            symbol="0050",
            type=models.TransactionType.BUY,
            quantity=1,
            price=Decimal("100.00"),
            fee=Decimal("0.00"),
            tax=Decimal("0.00"),
            trade_date=datetime(2026, 1, i + 1, 9, 0),
        )
        for i in range(30)
    ]
    db_session.add_all(rows)
    db_session.commit()

    body = client.get("/api/portfolio/transactions").json()
    assert body["total"] == 30
    assert len(body["items"]) == 25


def test_transactions_limit_above_max_rejected(client):
    assert client.get("/api/portfolio/transactions", params={"limit": 500}).status_code == 422


def test_transactions_limit_zero_rejected(client):
    assert client.get("/api/portfolio/transactions", params={"limit": 0}).status_code == 422


def test_transactions_negative_offset_rejected(client):
    assert client.get("/api/portfolio/transactions", params={"offset": -1}).status_code == 422


def test_transactions_sort_by_symbol_asc(client, db_session):
    _seed_transactions(db_session)
    body = client.get("/api/portfolio/transactions", params={"sort": "symbol:asc"}).json()
    symbols = [row["symbol"] for row in body["items"]]
    # 0050 (3 rows) before 0056 (1 row) before 2330 (1 row), tie-break id desc
    assert symbols == ["0050", "0050", "0050", "0056", "2330"]


def test_transactions_unknown_sort_field_rejected(client):
    assert client.get("/api/portfolio/transactions", params={"sort": "memo:asc"}).status_code == 422


def test_transactions_malformed_sort_rejected(client):
    assert client.get("/api/portfolio/transactions", params={"sort": "trade_date"}).status_code == 422
    assert client.get("/api/portfolio/transactions", params={"sort": "trade_date:sideways"}).status_code == 422


def test_transactions_side_filter(client, db_session):
    _seed_transactions(db_session)
    buys = client.get("/api/portfolio/transactions", params={"side": "BUY"}).json()
    sells = client.get("/api/portfolio/transactions", params={"side": "SELL"}).json()
    assert buys["total"] == 4
    assert all(r["type"] == "BUY" for r in buys["items"])
    assert sells["total"] == 1
    assert all(r["type"] == "SELL" for r in sells["items"])


def test_transactions_invalid_side_rejected(client):
    assert client.get("/api/portfolio/transactions", params={"side": "HOLD"}).status_code == 422


def test_transactions_date_range_filter(client, db_session):
    _seed_transactions(db_session)
    body = client.get(
        "/api/portfolio/transactions",
        params={"date_from": "2026-01-01", "date_to": "2026-12-31"},
    ).json()
    assert body["total"] == 4  # excludes 2025-12-20


def test_transactions_only_date_from(client, db_session):
    _seed_transactions(db_session)
    body = client.get(
        "/api/portfolio/transactions", params={"date_from": "2026-02-01"}
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["trade_date"].startswith("2026-02-15")


def test_transactions_bad_date_format_rejected(client):
    assert (
        client.get("/api/portfolio/transactions", params={"date_from": "2025/01/01"}).status_code
        == 422
    )


def test_transactions_symbol_filter_normalises_input(client, db_session):
    _seed_transactions(db_session)
    body = client.get("/api/portfolio/transactions", params={"symbol": "0050.tw"}).json()
    assert body["total"] == 3
    assert all(r["symbol"] == "0050" for r in body["items"])


# ---------- dividends ----------


def test_dividends_default_response_is_paged_wrapper(client, db_session):
    _seed_dividends(db_session)
    body = client.get("/api/portfolio/dividends").json()
    assert "items" in body and "total" in body
    assert body["total"] == 4
    assert [row["ex_dividend_date"][:10] for row in body["items"]] == [
        "2026-04-01",
        "2026-03-01",
        "2026-02-01",
        "2025-11-01",
    ]


def test_dividends_sort_by_amount_desc(client, db_session):
    _seed_dividends(db_session)
    body = client.get("/api/portfolio/dividends", params={"sort": "amount:desc"}).json()
    amounts = [float(r["amount"]) for r in body["items"]]
    assert amounts == sorted(amounts, reverse=True)


def test_dividends_source_filter(client, db_session):
    _seed_dividends(db_session)
    body = client.get(
        "/api/portfolio/dividends", params={"source": "auto:TWT49U"}
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["source"] == "auto:TWT49U"


def test_dividends_date_range_filter(client, db_session):
    _seed_dividends(db_session)
    body = client.get(
        "/api/portfolio/dividends",
        params={"date_from": "2026-01-01", "date_to": "2026-12-31"},
    ).json()
    assert body["total"] == 3  # excludes 2025-11-01


def test_dividends_unknown_sort_field_rejected(client):
    assert client.get("/api/portfolio/dividends", params={"sort": "memo:asc"}).status_code == 422


def test_dividends_symbol_filter(client, db_session):
    _seed_dividends(db_session)
    body = client.get("/api/portfolio/dividends", params={"symbol": "0050"}).json()
    assert body["total"] == 2
    assert all(r["symbol"] == "0050" for r in body["items"])
