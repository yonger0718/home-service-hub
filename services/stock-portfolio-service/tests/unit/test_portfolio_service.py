from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models import portfolio as models
from app.schemas import portfolio as schemas
from app.services import portfolio_service


class TestEstimateSellCosts:
    def test_zero_gross_returns_zero(self):
        assert portfolio_service._estimate_sell_costs(Decimal("0")) == Decimal("0")

    def test_small_position_floors_fee_to_one_ntd(self):
        # 100 NTD * 0.001425 * 0.28 = 0.0399 → floors to 0 without the min,
        # but Cathay charges at least 1 NTD per electronic order.
        cost = portfolio_service._estimate_sell_costs(Decimal("100"))
        # tax 100 * 0.001 = 0.1 → floor 0; total = fee(1) + tax(0) = 1
        assert cost == Decimal("1")

    def test_normal_position_keeps_calculated_fee(self):
        # 100,000 NTD * 0.001425 * 0.28 = 39.9 → floor 39 (above 1, unchanged)
        # tax 100,000 * 0.001 = 100 → floor 100
        # total = 39 + 100 = 139
        assert portfolio_service._estimate_sell_costs(Decimal("100000")) == Decimal("139")

    def test_min_fee_overridable_via_env(self, monkeypatch):
        monkeypatch.setenv("PORTFOLIO_SELL_MIN_FEE", "20")
        # 100 NTD position: calculated fee floors to 0, env raises floor to 20.
        cost = portfolio_service._estimate_sell_costs(Decimal("100"))
        assert cost == Decimal("20")

    def test_min_fee_disabled_when_set_to_zero(self, monkeypatch):
        monkeypatch.setenv("PORTFOLIO_SELL_MIN_FEE", "0")
        # 100 NTD position: 0 fee + 0 tax = 0
        cost = portfolio_service._estimate_sell_costs(Decimal("100"))
        assert cost == Decimal("0")


class TestPortfolioService:
    def test_sanitize_symbol(self):
        assert portfolio_service.sanitize_symbol("0050.TW") == "0050"
        assert portfolio_service.sanitize_symbol("00919.two") == "00919"
        assert portfolio_service.sanitize_symbol(" 2330 ") == "2330"

    def test_resolve_sort_trade_date_normalizes_aware_datetimes_to_utc_naive(self):
        aware_trade_date = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)
        naive_trade_date = datetime(2026, 5, 1, 9, 0)

        resolved_aware = portfolio_service._resolve_sort_trade_date(aware_trade_date)
        resolved_naive = portfolio_service._resolve_sort_trade_date(naive_trade_date)

        assert resolved_aware == naive_trade_date
        assert resolved_aware.tzinfo is None
        assert resolved_naive == naive_trade_date

    @patch("app.services.portfolio_service.get_stock_quotes")
    def test_get_portfolio_summary_with_holdings(self, mock_get_quotes, db_session):
        db_session.add(
            models.Transaction(
                symbol="0050.TW",
                name="元大台灣50",
                type=models.TransactionType.BUY,
                quantity=100,
                price=Decimal("100"),
                fee=Decimal("10"),
                tax=Decimal("0"),
                trade_date=datetime(2026, 1, 1),
            )
        )
        db_session.add(
            models.Transaction(
                symbol="0050",
                name="元大台灣50",
                type=models.TransactionType.SELL,
                quantity=20,
                price=Decimal("110"),
                fee=Decimal("5"),
                tax=Decimal("0"),
                trade_date=datetime(2026, 1, 2),
            )
        )
        db_session.add(
            models.Dividend(
                symbol="0050",
                amount=Decimal("120"),
                ex_dividend_date=datetime(2026, 1, 15),
                received_date=datetime(2026, 1, 20),
            )
        )
        db_session.commit()

        mock_get_quotes.return_value = {
            "0050": {
                "symbol": "0050",
                "name": "元大台灣50",
                "current_price": Decimal("120"),
                "yesterday_close": Decimal("119"),
                "time": "13:30:00",
            }
        }

        summary = portfolio_service.get_portfolio_summary(db_session)
        assert isinstance(summary, schemas.PortfolioSummary)
        assert len(summary.holdings) == 1
        assert summary.holdings[0].symbol == "0050"
        assert summary.holdings[0].total_quantity == 80
        assert summary.total_dividends == Decimal("120.00")
        assert summary.total_market_value > Decimal("0")
        assert summary.quotes_status == "ok"

    @patch("app.services.portfolio_service.get_stock_quotes")
    def test_get_portfolio_summary_reports_partial_quote_status(self, mock_get_quotes, db_session):
        db_session.add_all(
            [
                models.Transaction(
                    symbol="0050",
                    name="元大台灣50",
                    type=models.TransactionType.BUY,
                    quantity=10,
                    price=Decimal("100"),
                    fee=Decimal("0"),
                    tax=Decimal("0"),
                    trade_date=datetime(2026, 1, 1),
                ),
                models.Transaction(
                    symbol="00919",
                    name="群益台灣精選高息",
                    type=models.TransactionType.BUY,
                    quantity=10,
                    price=Decimal("20"),
                    fee=Decimal("0"),
                    tax=Decimal("0"),
                    trade_date=datetime(2026, 1, 2),
                ),
            ]
        )
        db_session.commit()

        mock_get_quotes.return_value = {
            "0050": {
                "symbol": "0050",
                "name": "元大台灣50",
                "current_price": Decimal("102"),
                "yesterday_close": Decimal("101"),
                "time": "13:30:00",
            }
        }

        summary = portfolio_service.get_portfolio_summary(db_session)

        assert summary.quotes_status == "partial"

    @patch("app.services.portfolio_service.get_stock_quotes")
    def test_get_portfolio_summary_reports_unavailable_quote_status(self, mock_get_quotes, db_session):
        db_session.add(
            models.Transaction(
                symbol="0050",
                name="元大台灣50",
                type=models.TransactionType.BUY,
                quantity=10,
                price=Decimal("100"),
                fee=Decimal("0"),
                tax=Decimal("0"),
                trade_date=datetime(2026, 1, 1),
            )
        )
        db_session.commit()

        mock_get_quotes.return_value = {}

        summary = portfolio_service.get_portfolio_summary(db_session)

        assert summary.quotes_status == "unavailable"
        assert summary.total_market_value == Decimal("0.00")
        assert summary.total_unrealized_pnl == Decimal("0.00")

    def test_create_update_delete_transaction_and_dividend(self, db_session):
        created_tx = portfolio_service.create_transaction(
            db_session,
            schemas.TransactionCreate(
                symbol="00919.tw",
                name="群益台灣精選高息",
                type=schemas.TransactionType.BUY,
                quantity=10,
                price=Decimal("20"),
                fee=Decimal("0"),
                tax=Decimal("0"),
            ),
        )
        assert created_tx.symbol == "00919"

        updated_tx = portfolio_service.update_transaction(
            db_session,
            created_tx.id,
            schemas.TransactionCreate(
                symbol="00919.TWO",
                name="群益台灣精選高息",
                type=schemas.TransactionType.BUY,
                quantity=12,
                price=Decimal("21"),
                fee=Decimal("0"),
                tax=Decimal("0"),
            ),
        )
        assert updated_tx is not None
        assert updated_tx.symbol == "00919"
        assert updated_tx.quantity == 12

        created_div = portfolio_service.create_dividend(
            db_session,
            schemas.DividendCreate(
                symbol="00919.tw",
                amount=Decimal("25"),
                ex_dividend_date=datetime(2026, 2, 1),
                received_date=datetime(2026, 2, 10),
            ),
        )
        assert created_div.symbol == "00919"

        updated_div = portfolio_service.update_dividend(
            db_session,
            created_div.id,
            schemas.DividendCreate(
                symbol="00919.two",
                amount=Decimal("30"),
                ex_dividend_date=datetime(2026, 2, 1),
                received_date=datetime(2026, 2, 10),
            ),
        )
        assert updated_div is not None
        assert updated_div.symbol == "00919"
        assert updated_div.amount == Decimal("30")

        assert portfolio_service.delete_transaction(db_session, created_tx.id) is True
        assert portfolio_service.delete_dividend(db_session, created_div.id) is True

    def test_update_transaction_preserves_trade_date_when_omitted(self, db_session):
        original_trade_date = datetime(2026, 3, 1, 9, 0)
        created_tx = portfolio_service.create_transaction(
            db_session,
            schemas.TransactionCreate(
                symbol="0050",
                name="元大台灣50",
                type=schemas.TransactionType.BUY,
                quantity=10,
                price=Decimal("100"),
                fee=Decimal("1"),
                tax=Decimal("0"),
                trade_date=original_trade_date,
            ),
        )

        updated_tx = portfolio_service.update_transaction(
            db_session,
            created_tx.id,
            schemas.TransactionCreate(
                symbol="0050",
                name="元大台灣50",
                type=schemas.TransactionType.BUY,
                quantity=12,
                price=Decimal("101"),
                fee=Decimal("1"),
                tax=Decimal("0"),
            ),
        )

        assert updated_tx is not None
        assert updated_tx.trade_date == original_trade_date

    def test_create_transaction_persists_resolved_trade_date_when_omitted(self, db_session):
        before_create = datetime.now(timezone.utc).replace(tzinfo=None)

        created_tx = portfolio_service.create_transaction(
            db_session,
            schemas.TransactionCreate(
                symbol="0050",
                name="元大台灣50",
                type=schemas.TransactionType.BUY,
                quantity=10,
                price=Decimal("100"),
                fee=Decimal("1"),
                tax=Decimal("0"),
            ),
        )

        after_create = datetime.now(timezone.utc).replace(tzinfo=None)
        assert created_tx.trade_date is not None
        assert before_create <= created_tx.trade_date.replace(tzinfo=None) <= after_create

    def test_update_transaction_preserves_trade_date_when_explicit_null(self, db_session):
        original_trade_date = datetime(2026, 3, 1, 9, 0)
        created_tx = portfolio_service.create_transaction(
            db_session,
            schemas.TransactionCreate(
                symbol="0050",
                name="元大台灣50",
                type=schemas.TransactionType.BUY,
                quantity=10,
                price=Decimal("100"),
                fee=Decimal("1"),
                tax=Decimal("0"),
                trade_date=original_trade_date,
            ),
        )

        updated_tx = portfolio_service.update_transaction(
            db_session,
            created_tx.id,
            schemas.TransactionCreate(
                symbol="0050",
                name="元大台灣50",
                type=schemas.TransactionType.BUY,
                quantity=12,
                price=Decimal("101"),
                fee=Decimal("1"),
                tax=Decimal("0"),
                trade_date=None,
            ),
        )

        assert updated_tx is not None
        assert updated_tx.trade_date == original_trade_date

    def test_update_dividend_preserves_received_date_when_omitted(self, db_session):
        original_received_date = datetime(2026, 4, 10, 9, 0)
        created_dividend = portfolio_service.create_dividend(
            db_session,
            schemas.DividendCreate(
                symbol="0056",
                amount=Decimal("25"),
                ex_dividend_date=datetime(2026, 4, 1, 9, 0),
                received_date=original_received_date,
            ),
        )

        updated_dividend = portfolio_service.update_dividend(
            db_session,
            created_dividend.id,
            schemas.DividendCreate(
                symbol="0056",
                amount=Decimal("30"),
                ex_dividend_date=datetime(2026, 4, 1, 9, 0),
            ),
        )

        assert updated_dividend is not None
        assert updated_dividend.received_date == original_received_date

    def test_create_transaction_rejects_sell_without_holdings(self, db_session):
        with pytest.raises(ValueError, match="without holdings"):
            portfolio_service.create_transaction(
                db_session,
                schemas.TransactionCreate(
                    symbol="2330",
                    name="台積電",
                    type=schemas.TransactionType.SELL,
                    quantity=1,
                    price=Decimal("900"),
                    fee=Decimal("0"),
                    tax=Decimal("0"),
                    trade_date=datetime(2026, 5, 1, 9, 0),
                ),
            )

        assert db_session.query(models.Transaction).count() == 0

    def test_create_transaction_rejects_oversell(self, db_session):
        portfolio_service.create_transaction(
            db_session,
            schemas.TransactionCreate(
                symbol="0050",
                name="元大台灣50",
                type=schemas.TransactionType.BUY,
                quantity=10,
                price=Decimal("100"),
                fee=Decimal("0"),
                tax=Decimal("0"),
                trade_date=datetime(2026, 5, 1, 9, 0),
            ),
        )

        with pytest.raises(ValueError, match="only 10 available"):
            portfolio_service.create_transaction(
                db_session,
                schemas.TransactionCreate(
                    symbol="0050",
                    name="元大台灣50",
                    type=schemas.TransactionType.SELL,
                    quantity=11,
                    price=Decimal("101"),
                    fee=Decimal("0"),
                    tax=Decimal("0"),
                    trade_date=datetime(2026, 5, 2, 9, 0),
                ),
            )

        assert db_session.query(models.Transaction).count() == 1

    @patch("app.services.portfolio_service.get_stock_quotes")
    def test_create_transaction_allows_partial_sell(self, mock_get_quotes, db_session):
        mock_get_quotes.return_value = {
            "0050": {
                "symbol": "0050",
                "name": "元大台灣50",
                "current_price": Decimal("102"),
                "yesterday_close": Decimal("101"),
                "time": "13:30:00",
            }
        }

        portfolio_service.create_transaction(
            db_session,
            schemas.TransactionCreate(
                symbol="0050",
                name="元大台灣50",
                type=schemas.TransactionType.BUY,
                quantity=10,
                price=Decimal("100"),
                fee=Decimal("0"),
                tax=Decimal("0"),
                trade_date=datetime(2026, 5, 1, 9, 0),
            ),
        )

        created_sell = portfolio_service.create_transaction(
            db_session,
            schemas.TransactionCreate(
                symbol="0050",
                name="元大台灣50",
                type=schemas.TransactionType.SELL,
                quantity=4,
                price=Decimal("101"),
                fee=Decimal("0"),
                tax=Decimal("0"),
                trade_date=datetime(2026, 5, 2, 9, 0),
            ),
        )

        assert created_sell.type == models.TransactionType.SELL
        summary = portfolio_service.get_portfolio_summary(db_session)
        assert summary.holdings[0].total_quantity == 6

    def test_update_transaction_rejects_oversell_and_preserves_original_row(self, db_session):
        original_buy = portfolio_service.create_transaction(
            db_session,
            schemas.TransactionCreate(
                symbol="0050",
                name="元大台灣50",
                type=schemas.TransactionType.BUY,
                quantity=10,
                price=Decimal("100"),
                fee=Decimal("0"),
                tax=Decimal("0"),
                trade_date=datetime(2026, 5, 1, 9, 0),
            ),
        )
        portfolio_service.create_transaction(
            db_session,
            schemas.TransactionCreate(
                symbol="0050",
                name="元大台灣50",
                type=schemas.TransactionType.SELL,
                quantity=8,
                price=Decimal("101"),
                fee=Decimal("0"),
                tax=Decimal("0"),
                trade_date=datetime(2026, 5, 2, 9, 0),
            ),
        )

        with pytest.raises(ValueError, match="only 5 available"):
            portfolio_service.update_transaction(
                db_session,
                original_buy.id,
                schemas.TransactionCreate(
                    symbol="0050",
                    name="元大台灣50",
                    type=schemas.TransactionType.BUY,
                    quantity=5,
                    price=Decimal("100"),
                    fee=Decimal("0"),
                    tax=Decimal("0"),
                    trade_date=datetime(2026, 5, 1, 9, 0),
                ),
            )

        db_session.refresh(original_buy)
        assert original_buy.quantity == 10

    def test_create_transaction_uses_deterministic_same_day_ordering(self, db_session):
        portfolio_service.create_transaction(
            db_session,
            schemas.TransactionCreate(
                symbol="0050",
                name="元大台灣50",
                type=schemas.TransactionType.BUY,
                quantity=10,
                price=Decimal("100"),
                fee=Decimal("0"),
                tax=Decimal("0"),
                trade_date=datetime(2026, 5, 1, 9, 0),
            ),
        )
        portfolio_service.create_transaction(
            db_session,
            schemas.TransactionCreate(
                symbol="0050",
                name="元大台灣50",
                type=schemas.TransactionType.BUY,
                quantity=5,
                price=Decimal("101"),
                fee=Decimal("0"),
                tax=Decimal("0"),
                trade_date=datetime(2026, 5, 1, 9, 0),
            ),
        )

        created_sell = portfolio_service.create_transaction(
            db_session,
            schemas.TransactionCreate(
                symbol="0050",
                name="元大台灣50",
                type=schemas.TransactionType.SELL,
                quantity=12,
                price=Decimal("102"),
                fee=Decimal("0"),
                tax=Decimal("0"),
                trade_date=datetime(2026, 5, 1, 9, 0),
            ),
        )

        assert created_sell.type == models.TransactionType.SELL


def test_create_sell_without_holdings_returns_http_400(client, db_session):
    response = client.post(
        "/api/portfolio/transactions",
        json={
            "symbol": "2330",
            "name": "台積電",
            "type": "SELL",
            "quantity": 1,
            "price": "900.00",
            "trade_date": "2026-05-01T09:00:00",
            "fee": "0.00",
            "tax": "0.00",
        },
    )

    assert response.status_code == 400
    assert db_session.query(models.Transaction).count() == 0


def test_update_transaction_oversell_returns_http_400_and_preserves_row(client, db_session):
    buy_transaction = models.Transaction(
        symbol="0050",
        name="元大台灣50",
        type=models.TransactionType.BUY,
        quantity=10,
        price=Decimal("100.00"),
        fee=Decimal("0.00"),
        tax=Decimal("0.00"),
        trade_date=datetime(2026, 5, 1, 9, 0),
    )
    sell_transaction = models.Transaction(
        symbol="0050",
        name="元大台灣50",
        type=models.TransactionType.SELL,
        quantity=8,
        price=Decimal("101.00"),
        fee=Decimal("0.00"),
        tax=Decimal("0.00"),
        trade_date=datetime(2026, 5, 2, 9, 0),
    )
    db_session.add_all([buy_transaction, sell_transaction])
    db_session.commit()

    response = client.put(
        f"/api/portfolio/transactions/{buy_transaction.id}",
        json={
            "symbol": "0050",
            "name": "元大台灣50",
            "type": "BUY",
            "quantity": 5,
            "price": "100.00",
            "trade_date": "2026-05-01T09:00:00",
            "fee": "0.00",
            "tax": "0.00",
        },
    )

    assert response.status_code == 400
    db_session.refresh(buy_transaction)
    assert buy_transaction.quantity == 10


@patch("app.services.portfolio_service.get_stock_quotes")
def test_portfolio_summary_keeps_lifetime_dividends_for_closed_positions(mock_get_quotes, db_session):
    mock_get_quotes.return_value = {}

    db_session.add_all(
        [
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
                type=models.TransactionType.SELL,
                quantity=10,
                price=Decimal("105.00"),
                fee=Decimal("0.00"),
                tax=Decimal("0.00"),
                trade_date=datetime(2026, 3, 1, 9, 0),
            ),
            models.Dividend(
                symbol="0050",
                amount=Decimal("100.00"),
                ex_dividend_date=datetime(2026, 2, 1, 9, 0),
                received_date=datetime(2026, 2, 10, 9, 0),
            ),
        ]
    )
    db_session.commit()

    summary = portfolio_service.get_portfolio_summary(db_session)

    assert summary.holdings == []
    assert summary.total_dividends == Decimal("100.00")


@patch.object(portfolio_service, "get_stock_quotes")
def test_portfolio_summary_applies_corporate_action_factor(mock_get_quotes, db_session):
    """A 1→10 face-value change adjusts pre-event qty x10 and price /10."""
    from app.models.corporate_action import CorporateAction

    mock_get_quotes.return_value = {
        "2330": {
            "current_price": Decimal("60"),
            "yesterday_close": Decimal("60"),
            "day_change_amount": Decimal("0"),
            "day_change_percent": Decimal("0"),
        }
    }

    db_session.add_all([
        models.Transaction(
            symbol="2330", name="台積電",
            type=models.TransactionType.BUY,
            quantity=1, price=Decimal("600.00"),
            fee=Decimal("0.00"), tax=Decimal("0.00"),
            trade_date=datetime(2026, 1, 1, 9, 0),
        ),
        CorporateAction(
            symbol="2330", effective_date=datetime(2026, 2, 1).date(),
            ratio=Decimal("10"), source="TWSE",
            source_event_key="2330_2026-02-01",
        ),
    ])
    db_session.commit()

    summary = portfolio_service.get_portfolio_summary(db_session)

    assert len(summary.holdings) == 1
    holding = summary.holdings[0]
    assert holding.symbol == "2330"
    assert holding.total_quantity == 10
    # cost basis preserved: 1 * 600 = 600 = 10 * 60
    assert holding.avg_cost == Decimal("60.00")


@patch.object(portfolio_service, "get_stock_quotes")
def test_portfolio_summary_unchanged_without_corp_actions(mock_get_quotes, db_session):
    """Sanity: with no corporate actions, output identical to pre-feature."""
    mock_get_quotes.return_value = {
        "2330": {
            "current_price": Decimal("650"),
            "yesterday_close": Decimal("650"),
            "day_change_amount": Decimal("0"),
            "day_change_percent": Decimal("0"),
        }
    }
    db_session.add(models.Transaction(
        symbol="2330", name="台積電",
        type=models.TransactionType.BUY,
        quantity=2, price=Decimal("600.00"),
        fee=Decimal("0.00"), tax=Decimal("0.00"),
        trade_date=datetime(2026, 1, 1, 9, 0),
    ))
    db_session.commit()
    summary = portfolio_service.get_portfolio_summary(db_session)
    holding = summary.holdings[0]
    assert holding.total_quantity == 2
    assert holding.avg_cost == Decimal("600.00")


@patch.object(portfolio_service, "get_stock_quotes")
def test_portfolio_summary_post_event_transaction_not_adjusted(mock_get_quotes, db_session):
    """A transaction after the corp action stays at its nominal qty/price."""
    from app.models.corporate_action import CorporateAction

    mock_get_quotes.return_value = {
        "2330": {
            "current_price": Decimal("12"),
            "yesterday_close": Decimal("12"),
            "day_change_amount": Decimal("0"),
            "day_change_percent": Decimal("0"),
        }
    }
    db_session.add_all([
        CorporateAction(
            symbol="2330", effective_date=datetime(2026, 2, 1).date(),
            ratio=Decimal("10"), source="TWSE",
            source_event_key="2330_2026-02-01",
        ),
        models.Transaction(
            symbol="2330", name="台積電",
            type=models.TransactionType.BUY,
            quantity=5, price=Decimal("12.00"),
            fee=Decimal("0.00"), tax=Decimal("0.00"),
            trade_date=datetime(2026, 3, 1, 9, 0),
        ),
    ])
    db_session.commit()
    summary = portfolio_service.get_portfolio_summary(db_session)
    holding = summary.holdings[0]
    assert holding.total_quantity == 5
    assert holding.avg_cost == Decimal("12.00")
