from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from app.models import portfolio as models
from app.services import portfolio_service, realized_pnl_service


def _tx(
    symbol: str,
    side: models.TransactionType,
    quantity: int,
    price: str,
    trade_date: datetime,
    position_side: models.PositionSide = models.PositionSide.LONG,
) -> models.Transaction:
    return models.Transaction(
        symbol=symbol,
        type=side,
        position_side=position_side,
        quantity=quantity,
        price=Decimal(price),
        fee=Decimal("0.00"),
        tax=Decimal("0.00"),
        trade_date=trade_date,
    )


@patch.object(portfolio_service, "get_stock_quotes")
def test_unfiltered_realized_events_sum_matches_portfolio_summary(
    mock_get_quotes, db_session
) -> None:
    db_session.add_all(
        [
            _tx("2330", models.TransactionType.BUY, 100, "100.00", datetime(2025, 1, 1, 9, 0)),
            _tx("2330", models.TransactionType.BUY, 50, "130.00", datetime(2025, 1, 2, 9, 0)),
            _tx("2330", models.TransactionType.SELL, 40, "140.00", datetime(2025, 1, 3, 9, 0)),
            _tx("6488", models.TransactionType.BUY, 100, "50.00", datetime(2025, 2, 1, 9, 0)),
            _tx("6488", models.TransactionType.SELL, 60, "45.00", datetime(2025, 2, 2, 9, 0)),
            _tx("2330", models.TransactionType.SELL, 30, "160.00", datetime(2025, 3, 1, 9, 0)),
        ]
    )
    db_session.commit()
    mock_get_quotes.return_value = {
        "2330": {"current_price": Decimal("150.00"), "yesterday_close": Decimal("150.00")},
        "6488": {"current_price": Decimal("45.00"), "yesterday_close": Decimal("45.00")},
    }

    events_total = sum(
        (event.realized_pnl for event in realized_pnl_service.compute_events(db_session)),
        Decimal("0.00"),
    )
    summary = portfolio_service.get_portfolio_summary(db_session)

    assert events_total.quantize(Decimal("0.01")) == summary.total_realized_pnl


@patch.object(portfolio_service, "get_stock_quotes")
def test_unfiltered_realized_events_sum_matches_summary_mixed_long_and_short(
    mock_get_quotes, db_session
) -> None:
    db_session.add_all(
        [
            _tx("2330", models.TransactionType.BUY, 1000, "100.00", datetime(2025, 1, 1, 9, 0)),
            _tx("2330", models.TransactionType.SELL, 600, "140.00", datetime(2025, 2, 1, 9, 0)),
            _tx(
                "2330",
                models.TransactionType.SELL,
                500,
                "150.00",
                datetime(2025, 3, 1, 9, 0),
                position_side=models.PositionSide.SHORT,
            ),
            _tx(
                "2330",
                models.TransactionType.BUY,
                500,
                "120.00",
                datetime(2025, 4, 1, 9, 0),
                position_side=models.PositionSide.SHORT,
            ),
        ]
    )
    db_session.commit()
    mock_get_quotes.return_value = {
        "2330": {"current_price": Decimal("130.00"), "yesterday_close": Decimal("130.00")},
    }

    events_total = sum(
        (event.realized_pnl for event in realized_pnl_service.compute_events(db_session)),
        Decimal("0.00"),
    )
    summary = portfolio_service.get_portfolio_summary(db_session)

    assert events_total.quantize(Decimal("0.01")) == summary.total_realized_pnl
