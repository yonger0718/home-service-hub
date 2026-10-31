"""Task 8: valuation_source accounts display the portfolio total in the account read models; every
ledger-arithmetic reader keeps the ledger balance. Synthetic data only."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import ledger_service, portfolio_client, reconciliation_service, valuation_service
from app.services.portfolio_client import PortfolioUnavailable, Valuation
from tests.integration.statement_fixtures import _submit, run  # noqa: F401  (run is a fixture)


@pytest.fixture
def valuation_stub(monkeypatch):
    stub = SimpleNamespace(currency="TWD", total="0", complete=True, missing=[], down=False, seen=[])

    class _Client:
        def valuation(self, broker, as_of):
            stub.seen.append((broker, as_of))
            if stub.down:
                raise PortfolioUnavailable("connect")
            total = Decimal(stub.total)
            return Valuation(broker=broker, currency=stub.currency, as_of=as_of, priced_through=as_of, market_value=total,
                             cash=Decimal(0), total=total, complete=stub.complete, missing=tuple(stub.missing))

    monkeypatch.setattr(portfolio_client, "client_for", lambda: _Client())
    valuation_service.clear_cache()
    yield stub
    valuation_service.clear_cache()


def test_valuation_account_reads_the_portfolio_total_but_the_ledger_balance_stays(client, seed, db_session, valuation_stub):
    pos = seed.account("國泰持股", opening="0", valuation_source="portfolio:cathay")
    seed.entry(pos, "100000", kind="transfer_in", day=date(2026, 9, 1))
    valuation_stub.total = "123456"
    db_session.commit()
    body = client.get(f"/accounts/{pos.id}").json()
    assert (body["balance"], body["balance_main"], body["balance_kind"], body["stale"]) == ("123456.0000", "123456.0000", "valuation", False)
    assert ledger_service.account_balance(db_session, pos.id) == Decimal("100000")


def test_historical_as_of_asks_for_that_date(client, seed, db_session, valuation_stub):
    pos = seed.account("國泰持股", valuation_source="portfolio:cathay")
    valuation_stub.total = "99000"
    db_session.commit()
    body = client.get(f"/accounts/{pos.id}", params={"as_of": "2026-09-12"}).json()
    assert valuation_stub.seen == [("cathay", date(2026, 9, 12))] and body["valuation"]["as_of"] == "2026-09-12"


def test_cold_cache_with_portfolio_down_is_null_and_stale(client, seed, db_session, valuation_stub):
    pos = seed.account("國泰持股", valuation_source="portfolio:cathay")
    valuation_stub.down = True
    db_session.commit()
    body = client.get(f"/accounts/{pos.id}").json()
    assert (body["balance"], body["balance_main"], body["balance_kind"], body["stale"], body["valuation"]) == (None, None, "valuation", True, None)
    listed = next(a for a in client.get("/accounts").json() if a["id"] == pos.id)
    assert (listed["balance"], listed["stale"]) == (None, True)


def test_incomplete_valuation_is_never_a_balance(client, seed, db_session, valuation_stub):
    pos = seed.account("國泰持股", valuation_source="portfolio:cathay")
    valuation_stub.total, valuation_stub.complete, valuation_stub.missing = "100000", False, ["9998"]
    db_session.commit()
    body = client.get(f"/accounts/{pos.id}").json()
    assert (body["balance"], body["balance_main"], body["valuation"]["complete"], body["valuation"]["missing"]) == (None, None, False, ["9998"])


def test_ledger_only_readers_ignore_valuation(seed, db_session, valuation_stub, run):
    pos = seed.account("國泰持股", opening="0", valuation_source="portfolio:cathay")
    seed.entry(pos, "100000", kind="transfer_in", day=date(2026, 9, 1))
    valuation_stub.total = "123456"
    assert ledger_service.period_summary(db_session, pos.id, date(2026, 9, 1), date(2026, 9, 30))["end_balance"] == Decimal("100000")
    assert ledger_service.account_balance(db_session, pos.id) == Decimal("100000")
    stmt, _, _ = _submit(db_session, run, pos, [], kind="bank", period=(date(2026, 9, 1), date(2026, 9, 30)))
    assert reconciliation_service.held_balance(db_session, stmt, [pos.id]) == Decimal("100000")   # D2: the reconcile balance is a ledger figure


def test_warm_cache_with_portfolio_down_serves_the_stale_value(client, seed, db_session, valuation_stub):
    pos = seed.account("國泰持股", valuation_source="portfolio:cathay")
    valuation_stub.total = "123456"
    db_session.commit()
    client.get(f"/accounts/{pos.id}")                                                           # warms the cache
    valuation_stub.down = True; valuation_service.expire_cache()                                # expired, not cleared
    body = client.get(f"/accounts/{pos.id}").json()
    assert (body["balance"], body["stale"]) == ("123456.0000", True)                             # M15: a known value beats null


def test_down_portfolio_is_asked_once_per_negative_window(client, seed, db_session, valuation_stub):
    pos = seed.account("國泰持股", valuation_source="portfolio:cathay")
    valuation_stub.down = True
    db_session.commit()
    client.get(f"/accounts/{pos.id}")
    client.get(f"/accounts/{pos.id}")
    assert len(valuation_stub.seen) == 1
    valuation_service.expire_cache()
    client.get(f"/accounts/{pos.id}")
    assert len(valuation_stub.seen) == 2


def test_other_currency_is_unavailable_and_logs_only_the_code(client, seed, db_session, valuation_stub, caplog, monkeypatch):
    pos = seed.account("國泰持股", valuation_source="portfolio:cathay")
    valuation_stub.currency, valuation_stub.total = "USD", "424242"
    db_session.commit()
    monkeypatch.setattr(valuation_service.logger, "disabled", False)  # alembic fileConfig disables pre-existing loggers
    with caplog.at_level("DEBUG"):
        body = client.get(f"/accounts/{pos.id}").json()
    assert (body["balance"], body["stale"], body["valuation"]) == (None, True, None)
    mine = [r.getMessage() for r in caplog.records if r.name == valuation_service.__name__]
    assert mine and "code=currency" in mine[0] and not any("424242" in m for m in mine)


@pytest.mark.parametrize("source", ["portfolio", "portfolio:"])
def test_valuation_source_without_a_broker_is_unavailable(client, seed, db_session, valuation_stub, source):
    pos = seed.account("國泰持股", valuation_source=source)
    db_session.commit()
    body = client.get(f"/accounts/{pos.id}").json()
    assert (body["balance"], body["stale"], body["valuation"], valuation_stub.seen) == (None, True, None, [])
