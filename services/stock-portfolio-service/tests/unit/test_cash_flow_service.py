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
