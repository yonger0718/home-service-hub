from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from app.models.broker_account import BrokerAccount, BrokerEnum
from app.models.fx_rate import FxRate
from app.models import portfolio as models
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.price_history import PriceHistory
from app.services import portfolio_service


class FrozenDate(date):
    @classmethod
    def today(cls) -> date:
        return date(2026, 5, 29)


def _dt(value: date) -> datetime:
    return datetime.combine(value, datetime.min.time())


def _account(
    *,
    nickname: str,
    currency: str,
    opening_balance: str,
    broker: BrokerEnum = BrokerEnum.FIRSTRADE,
) -> BrokerAccount:
    return BrokerAccount(
        broker=broker,
        nickname=nickname,
        currency=currency,
        opening_balance=Decimal(opening_balance),
        opening_date=date(2026, 1, 1),
        is_active=True,
    )


def test_summary_endpoint_returns_windowed_xirr_fields(client, db_session, monkeypatch) -> None:
    monkeypatch.setattr(portfolio_service, "date_type", FrozenDate)
    db_session.add(
        models.Transaction(
            symbol="0050",
            name="0050",
            type=models.TransactionType.BUY,
            quantity=100,
            price=Decimal("10"),
            fee=Decimal("0"),
            tax=Decimal("0"),
            trade_date=_dt(date(2025, 1, 10)),
        )
    )
    db_session.add(
        models.Transaction(
            symbol="0050",
            name="0050",
            type=models.TransactionType.BUY,
            quantity=10,
            price=Decimal("10"),
            fee=Decimal("0"),
            tax=Decimal("0"),
            trade_date=_dt(date(2026, 4, 29)),
        )
    )
    for window_date in (
        date(2026, 4, 29),
        date(2026, 2, 28),
        date(2025, 5, 29),
        date(2026, 1, 1),
    ):
        db_session.add(
            PortfolioSnapshot(
                date=window_date,
                total_market_value=Decimal("1000"),
                total_cost=Decimal("900"),
                total_unrealized_pnl=Decimal("100"),
                total_dividends=Decimal("0"),
                total_realized_pnl=Decimal("0"),
                portfolio_xirr=None,
            )
        )
        db_session.add(
            PriceHistory(
                symbol="0050",
                date=window_date,
                close=Decimal("10"),
                source="TWSE",
            )
        )
    db_session.commit()

    with patch(
        "app.services.portfolio_service.get_stock_quotes",
        return_value={
            "0050": {
                "symbol": "0050",
                "name": "0050",
                "current_price": Decimal("12"),
                "yesterday_close": Decimal("11"),
                "time": "13:30:00",
            }
        },
    ):
        response = client.get("/api/portfolio/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["portfolio_xirr_1m"] is not None
    assert body["portfolio_xirr_3m"] is not None
    assert body["portfolio_xirr_1y"] is not None
    assert body["portfolio_xirr_ytd"] is not None
    holding = body["holdings"][0]
    assert holding["xirr_1m"] is not None
    assert holding["xirr_3m"] is not None
    assert holding["xirr_1y"] is not None
    assert holding["xirr_ytd"] is not None


def test_summary_endpoint_includes_cash_and_assets_single_twd_account(client, db_session) -> None:
    db_session.add(
        _account(
            broker=BrokerEnum.CATHAY,
            nickname="Cathay TWD",
            currency="TWD",
            opening_balance="100000",
        )
    )
    db_session.commit()

    response = client.get("/api/portfolio/summary")

    assert response.status_code == 200
    body = response.json()
    assert Decimal(body["total_market_value"]) == Decimal("0")
    assert Decimal(body["total_cash_twd"]) == Decimal("100000")
    assert Decimal(body["total_assets_twd"]) == Decimal("100000")


def test_summary_endpoint_includes_cash_and_assets_mixed_currencies(client, db_session) -> None:
    today = date.today()
    db_session.add_all(
        [
            _account(
                broker=BrokerEnum.CATHAY,
                nickname="Cathay TWD",
                currency="TWD",
                opening_balance="100000",
            ),
            _account(
                broker=BrokerEnum.FIRSTRADE,
                nickname="Firstrade USD",
                currency="USD",
                opening_balance="1000",
            ),
            _account(
                broker=BrokerEnum.IB,
                nickname="IB GBP",
                currency="GBP",
                opening_balance="500",
            ),
            FxRate(
                date=today,
                base_currency="USD",
                quote_currency="TWD",
                rate=Decimal("31"),
                source="test",
            ),
            FxRate(
                date=today,
                base_currency="GBP",
                quote_currency="TWD",
                rate=Decimal("39"),
                source="test",
            ),
        ]
    )
    db_session.commit()

    response = client.get("/api/portfolio/summary")

    assert response.status_code == 200
    body = response.json()
    assert Decimal(body["total_cash_twd"]) == Decimal("150500")
    assert Decimal(body["total_assets_twd"]) == Decimal("150500")


def test_summary_endpoint_includes_zero_cash_with_no_accounts(client) -> None:
    response = client.get("/api/portfolio/summary")

    assert response.status_code == 200
    body = response.json()
    assert Decimal(body["total_cash_twd"]) == Decimal("0")
    assert Decimal(body["total_assets_twd"]) == Decimal(body["total_market_value"])
