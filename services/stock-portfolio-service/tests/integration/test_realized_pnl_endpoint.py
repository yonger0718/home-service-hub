from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from app.models import portfolio as models


def _tx(
    *,
    symbol: str,
    side: models.TransactionType,
    quantity: int,
    price: str,
    trade_date: datetime,
    fee: str = "0.00",
    tax: str = "0.00",
    is_day_trade: bool = False,
    position_side: models.PositionSide = models.PositionSide.LONG,
) -> models.Transaction:
    return models.Transaction(
        symbol=symbol,
        type=side,
        position_side=position_side,
        quantity=quantity,
        price=Decimal(price),
        fee=Decimal(fee),
        tax=Decimal(tax),
        trade_date=trade_date,
        is_day_trade=is_day_trade,
    )


def _seed_endpoint_portfolio(db_session) -> None:
    rows = [
        _tx(
            symbol="2330",
            side=models.TransactionType.BUY,
            quantity=100,
            price="100.00",
            trade_date=datetime(2025, 1, 1, 9, 0),
        ),
        _tx(
            symbol="6488",
            side=models.TransactionType.BUY,
            quantity=100,
            price="50.00",
            trade_date=datetime(2025, 1, 1, 9, 0),
        ),
        _tx(
            symbol="2330",
            side=models.TransactionType.SELL,
            quantity=10,
            price="120.00",
            trade_date=datetime(2025, 1, 2, 9, 0),
        ),
        _tx(
            symbol="6488",
            side=models.TransactionType.SELL,
            quantity=10,
            price="70.00",
            trade_date=datetime(2025, 1, 3, 9, 0),
            is_day_trade=True,
        ),
    ]
    base_date = datetime(2026, 1, 1, 9, 0)
    for idx in range(60):
        rows.append(
            _tx(
                symbol="2330",
                side=models.TransactionType.SELL,
                quantity=1,
                price="101.00",
                trade_date=base_date + timedelta(days=idx),
            )
        )
    db_session.add_all(rows)
    db_session.commit()


def test_realized_pnl_endpoint_happy_path_200(client, db_session) -> None:
    _seed_endpoint_portfolio(db_session)

    response = client.get("/api/portfolio/realized-pnl")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "total", "summary"}
    assert body["total"] == 62
    assert len(body["items"]) == 25
    assert set(body["summary"]) == {
        "filter_scope_total",
        "filter_scope_count",
        "ytd_total",
        "ytd_count",
    }


def test_realized_pnl_endpoint_pagination_boundary(client, db_session) -> None:
    _seed_endpoint_portfolio(db_session)

    response = client.get(
        "/api/portfolio/realized-pnl",
        params={"symbol": "2330", "year": 2026, "offset": 50, "limit": 25},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 60
    assert len(body["items"]) == 10


def test_realized_pnl_endpoint_filters(client, db_session) -> None:
    _seed_endpoint_portfolio(db_session)

    symbol_body = client.get(
        "/api/portfolio/realized-pnl", params={"symbol": "6488"}
    ).json()
    assert symbol_body["total"] == 1
    assert symbol_body["items"][0]["symbol"] == "6488"

    range_body = client.get(
        "/api/portfolio/realized-pnl",
        params={"date_from": "2025-01-03", "date_to": "2025-01-03"},
    ).json()
    assert range_body["total"] == 1
    assert range_body["items"][0]["trade_date"] == "2025-01-03"

    year_body = client.get("/api/portfolio/realized-pnl", params={"year": 2026}).json()
    assert year_body["total"] == 60
    assert all(item["trade_date"].startswith("2026-") for item in year_body["items"])

    day_trade_body = client.get(
        "/api/portfolio/realized-pnl", params={"day_trade_only": "true"}
    ).json()
    assert day_trade_body["total"] == 1
    assert day_trade_body["items"][0]["is_day_trade"] is True


def test_realized_pnl_endpoint_sort_and_summary(client, db_session) -> None:
    _seed_endpoint_portfolio(db_session)

    response = client.get(
        "/api/portfolio/realized-pnl",
        params={"symbol": "6488", "sort": "realized_pnl:desc"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["realized_pnl"] for item in body["items"]] == ["200.00"]
    assert body["summary"]["filter_scope_total"] == "200.00"
    assert body["summary"]["filter_scope_count"] == 1
    assert body["summary"]["ytd_total"] == "60.00"
    assert body["summary"]["ytd_count"] == 60


def test_realized_pnl_endpoint_emits_position_side(client, db_session) -> None:
    _seed_endpoint_portfolio(db_session)

    response = client.get("/api/portfolio/realized-pnl")

    assert response.status_code == 200
    body = response.json()
    assert all(
        item["position_side"] == "LONG" for item in body["items"]
    ), "long-only fixture should produce only LONG events"


def test_realized_pnl_endpoint_returns_short_cover_event(client, db_session) -> None:
    db_session.add_all(
        [
            _tx(
                symbol="6488",
                side=models.TransactionType.SELL,
                position_side=models.PositionSide.SHORT,
                quantity=1000,
                price="100.00",
                trade_date=datetime(2025, 5, 1, 9, 0),
            ),
            _tx(
                symbol="6488",
                side=models.TransactionType.BUY,
                position_side=models.PositionSide.SHORT,
                quantity=1000,
                price="80.00",
                trade_date=datetime(2025, 5, 5, 9, 0),
            ),
        ]
    )
    db_session.commit()

    response = client.get(
        "/api/portfolio/realized-pnl", params={"symbol": "6488"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    [event] = body["items"]
    assert event["position_side"] == "SHORT"
    assert event["realized_pnl"] == "20000.00"
