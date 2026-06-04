from datetime import date
from decimal import Decimal

from app.models.portfolio_snapshot import PortfolioSnapshot


def test_history_endpoint_items_include_cash_and_total_assets(client, db_session) -> None:
    db_session.add(
        PortfolioSnapshot(
            date=date(2026, 1, 1),
            total_market_value=Decimal("500000"),
            total_cost=Decimal("400000"),
            total_unrealized_pnl=Decimal("100000"),
            total_dividends=Decimal("0"),
            total_realized_pnl=Decimal("0"),
            total_cash_twd=Decimal("100000"),
            portfolio_xirr=None,
        )
    )
    db_session.commit()

    response = client.get(
        "/api/portfolio/history",
        params={"from": "2026-01-01", "to": "2026-06-03"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert Decimal(body[0]["total_cash_twd"]) == Decimal("100000")
    assert Decimal(body[0]["total_assets_twd"]) == Decimal("600000")


def test_price_history_endpoint_rejects_unknown_market(client) -> None:
    response = client.get(
        "/api/portfolio/price-history",
        params={
            "symbol": "2330",
            "market": "XYZ",
            "from": "2026-01-01",
            "to": "2026-01-02",
        },
    )

    assert response.status_code == 422
