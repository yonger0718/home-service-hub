from datetime import date
from decimal import Decimal

from app.models import portfolio as models
from app.services import cash_flow_service


def test_single_deposit_balance(db_session) -> None:
    cash_flow_service.write_cash_flows(
        db_session,
        [
            cash_flow_service.CashFlowRow(
                broker=models.Broker.SCHWAB.value,
                date=date(2026, 6, 4),
                type=models.BrokerCashFlowType.DEPOSIT.value,
                amount=Decimal("1500.00"),
                currency="USD",
                fx_rate_to_twd=Decimal("31.50000000"),
                note="wire",
                import_fingerprint="fp-1",
            )
        ],
    )

    assert cash_flow_service.get_broker_balance(
        db_session, models.Broker.SCHWAB.value, date(2026, 6, 5)
    ) == Decimal("1500.0000")


def test_balance_sums_mixed_flows_up_to_as_of_date(db_session) -> None:
    cash_flow_service.write_cash_flows(
        db_session,
        [
            cash_flow_service.CashFlowRow(
                broker=models.Broker.SCHWAB.value,
                date=date(2026, 6, 4),
                type=models.BrokerCashFlowType.DEPOSIT.value,
                amount=Decimal("1500"),
                currency="USD",
                fx_rate_to_twd=Decimal("31"),
                note=None,
                import_fingerprint="fp-1",
            ),
            cash_flow_service.CashFlowRow(
                broker=models.Broker.SCHWAB.value,
                date=date(2026, 6, 5),
                type=models.BrokerCashFlowType.WITHDRAWAL.value,
                amount=Decimal("-500"),
                currency="USD",
                fx_rate_to_twd=Decimal("31"),
                note=None,
                import_fingerprint="fp-2",
            ),
            cash_flow_service.CashFlowRow(
                broker=models.Broker.SCHWAB.value,
                date=date(2026, 6, 10),
                type=models.BrokerCashFlowType.DEPOSIT.value,
                amount=Decimal("200"),
                currency="USD",
                fx_rate_to_twd=Decimal("31"),
                note=None,
                import_fingerprint="fp-3",
            ),
        ],
    )

    assert cash_flow_service.get_broker_balance(
        db_session, models.Broker.SCHWAB.value, date(2026, 6, 6)
    ) == Decimal("1000.0000")


def test_balance_includes_trade_settlements(db_session) -> None:
    from datetime import datetime

    cash_flow_service.write_cash_flows(
        db_session,
        [
            cash_flow_service.CashFlowRow(
                broker=models.Broker.FIRSTRADE.value,
                date=date(2026, 6, 5),
                type=models.BrokerCashFlowType.DEPOSIT.value,
                amount=Decimal("2500"),
                currency="USD",
                fx_rate_to_twd=Decimal("31.3"),
                note="wire",
                import_fingerprint="fp-wire",
            ),
        ],
    )
    db_session.add_all([
        models.Transaction(
            symbol="UUUU",
            name="Energy Fuels",
            broker=models.Broker.FIRSTRADE.value,
            market="US",
            currency="USD",
            type=models.TransactionType.BUY,
            quantity=Decimal("10"),
            price=Decimal("15.00"),
            fee=Decimal("0.50"),
            tax=Decimal("0.00"),
            trade_date=datetime(2026, 6, 5, 9, 0),
        ),
        models.Transaction(
            symbol="UUUU",
            name="Energy Fuels",
            broker=models.Broker.FIRSTRADE.value,
            market="US",
            currency="USD",
            type=models.TransactionType.SELL,
            quantity=Decimal("5"),
            price=Decimal("18.00"),
            fee=Decimal("0.25"),
            tax=Decimal("0.00"),
            trade_date=datetime(2026, 6, 6, 9, 0),
        ),
    ])
    db_session.commit()

    # 2500 wire − (10*15 + 0.50) + (5*18 − 0.25) = 2500 − 150.50 + 89.75 = 2439.25
    balances = cash_flow_service.list_balances(db_session, as_of_date=date(2026, 6, 6))
    firstrade = [b for b in balances if b["broker"] == models.Broker.FIRSTRADE.value][0]
    assert firstrade["currency"] == "USD"
    assert firstrade["balance"] == Decimal("2439.2500")


def test_balance_excludes_tw_manual_trade_rows(db_session) -> None:
    from datetime import datetime

    # Pre-Phase-4 TW row that got backfilled to TW_MANUAL must NOT influence
    # any per-broker cash balance; the legacy TWD cash accounting path owns it.
    db_session.add(
        models.Transaction(
            symbol="2330",
            broker=models.Broker.TW_MANUAL.value,
            market="TW",
            currency="TWD",
            type=models.TransactionType.BUY,
            quantity=Decimal("1000"),
            price=Decimal("600"),
            fee=Decimal("100"),
            tax=Decimal("0"),
            trade_date=datetime(2026, 6, 5, 9, 0),
        )
    )
    db_session.commit()
    balances = cash_flow_service.list_balances(db_session, as_of_date=date(2026, 6, 6))
    assert balances == []


def test_duplicate_fingerprint_skips(db_session) -> None:
    row = cash_flow_service.CashFlowRow(
        broker=models.Broker.IB.value,
        date=date(2026, 6, 1),
        type=models.BrokerCashFlowType.DEPOSIT.value,
        amount=Decimal("3000"),
        currency="USD",
        fx_rate_to_twd=Decimal("31"),
        note="wire",
        import_fingerprint="same-fp",
    )

    first = cash_flow_service.write_cash_flows(db_session, [row])
    second = cash_flow_service.write_cash_flows(db_session, [row])

    assert first.created == 1
    assert second.created == 0
    assert second.skipped_duplicates == 1
    assert db_session.query(models.BrokerCashFlow).count() == 1
