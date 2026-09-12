"""Pending entitlement and explicit receipt contract; all data synthetic."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.portfolio import Dividend, Transaction, TransactionType, PositionSide
from app.models.broker_account import BrokerAccount, BrokerEnum
from app.models.cash_transaction import CashTransaction
from app.services import dividend_history_service as history, post_import_orchestrator as orch

BASE = '/api/portfolio'
FIXTURE = (Path(__file__).parents[1] / 'fixtures/twt49u-2026-09.json').read_bytes()


def seed(db):
    account = BrokerAccount(broker=BrokerEnum.CATHAY, nickname='synthetic', currency='TWD', opening_date=date(2026, 1, 1))
    db.add(account)
    db.add(Transaction(symbol='9802', type=TransactionType.BUY, position_side=PositionSide.LONG, quantity=1000, price=100, trade_date=datetime(2026, 9, 1, tzinfo=timezone.utc), fee=0, tax=0))
    db.commit()
    return account.id


def reconcile(db):
    @contextmanager
    def factory():
        yield db
    with patch.object(history, '_http_get', return_value=FIXTURE), patch.object(history, '_http_post_form', return_value={'tables': [{'data': []}]}), patch.object(history, '_fetch_detail_raw', return_value=(None, None)):
        return orch._step_dividends(factory, {'9802'}, date(2026, 9, 1), date(2026, 9, 12))


def test_fixture_persists_pending_without_cash_and_survives_restart(client, db_session):
    seed(db_session)
    reconcile(db_session)
    rows = client.get(BASE + '/dividends').json()['items']
    assert len(rows) == 1
    row = rows[0]
    assert row['receipt_status'] == 'pending'
    assert Decimal(row['cash_dividend_per_share']) == Decimal('3.1')
    assert row['payment_date'] is None and row['receipt_date'] is None
    assert Decimal(row['amount']) == Decimal('3090')
    assert db_session.query(CashTransaction).count() == 0
    assert Decimal(client.get(BASE + '/summary').json()['total_dividends']) == 0
    reconcile(db_session)
    orch.reset_state_for_tests()
    assert len(client.get(BASE + '/dividends').json()['items']) == 1


def test_manual_entry_does_not_imply_receipt(client, db_session):
    seed(db_session)
    response = client.post(BASE + '/dividends', json={'symbol': '9802', 'amount': '3090', 'ex_dividend_date': '2026-09-10T00:00:00+08:00', 'received_date': '2026-09-11T00:00:00+08:00'})
    assert response.status_code == 200
    assert response.json()['receipt_status'] == 'pending'
    assert response.json()['receipt_date'] is None
    assert db_session.query(CashTransaction).count() == 0


@pytest.fixture(autouse=True)
def no_quotes():
    with patch('app.services.portfolio_service.get_stock_quotes', return_value={}):
        yield


def confirm(client, row, account, **changes):
    return client.post(BASE + f"/dividends/{row['id']}/confirm-receipt", json={
        'account_id': account, 'receipt_date': '2026-09-11', 'revision': row['revision'], **changes})


def test_confirmation_posts_once_at_actual_date_and_counts_returns(client, db_session):
    account = seed(db_session)
    reconcile(db_session)
    row = client.get(BASE + '/dividends').json()['items'][0]
    for _ in range(2):
        response = confirm(client, row, account)
        assert response.status_code == 200, response.text
        assert response.json()['receipt_status'] == 'confirmed'
    leg = db_session.scalars(select(CashTransaction)).one()
    assert leg.amount == Decimal('3090') and leg.txn_date == date(2026, 9, 11)
    assert Decimal(client.get(BASE + '/summary').json()['total_dividends']) == 3090
    assert confirm(client, row, account, receipt_date='2026-09-12').status_code == 409
    assert client.delete(BASE + f"/dividends/{row['id']}").status_code == 409


def test_posting_failure_rolls_back_claim_and_all_cash(client, db_session):
    from app.services import dividend_receipt_service as receipts, cash_account_service as cash
    account = seed(db_session)
    reconcile(db_session)
    row = db_session.query(Dividend).one()
    original = cash.sync_dividend_cash_leg
    def fail_after_write(*args):
        original(*args)
        args[0].flush()
        raise RuntimeError('synthetic crash after posting')
    with patch.object(cash, 'sync_dividend_cash_leg', side_effect=fail_after_write):
        with pytest.raises(RuntimeError):
            receipts.confirm_receipt(db_session, row.id, date(2026, 9, 11), account, row.revision)
    db_session.expire_all()
    assert db_session.query(Dividend).one().receipt_status == 'pending'
    assert db_session.query(CashTransaction).count() == 0


def test_source_correction_pending_invalidates_revision_confirmed_freezes_ledger(client, db_session):
    from app.services.dividend_receipt_service import persist_entitlement
    from app.services.dividend_history_service import HistoricalDividendEvent
    account = seed(db_session)
    reconcile(db_session)
    old = client.get(BASE + '/dividends').json()['items'][0]
    event = HistoricalDividendEvent('9802', date(2026, 9, 10), Decimal('4'), None, None, None, 'TWT49U')
    persist_entitlement(db_session, event, Decimal(1000), date(2026, 10, 15))
    db_session.commit()
    assert confirm(client, old, account).status_code == 409
    fresh = client.get(BASE + '/dividends').json()['items'][0]
    assert fresh['payment_date'] == '2026-10-15'
    assert confirm(client, fresh, account).status_code == 200
    event = HistoricalDividendEvent('9802', date(2026, 9, 10), Decimal('5'), None, None, None, 'TPEX')
    row, reason = persist_entitlement(db_session, event, Decimal(1000))
    db_session.commit()
    assert reason and row.source_correction['amount'] == '4990.00'
    assert row.amount == Decimal('3990')
    assert db_session.query(CashTransaction).one().amount == Decimal('3990')
    assert db_session.query(Dividend).count() == 1


@pytest.mark.parametrize('with_cash', [False, True])
def test_legacy_rows_never_infer_receipt_or_duplicate(client, db_session, with_cash):
    from app.models.cash_transaction import CashTxnType, CashTxnSource
    account = seed(db_session)
    row = Dividend(symbol='9802', amount=3090, ex_dividend_date=datetime(2026, 9, 10), source='csv')
    db_session.add(row)
    db_session.flush()
    if with_cash:
        db_session.add(CashTransaction(account_id=account, txn_date=date(2026, 9, 10), type=CashTxnType.DIVIDEND_CASH, amount=3090, currency='TWD', related_dividend_id=row.id, source=CashTxnSource.AUTO_DERIVE, import_fingerprint='legacy-different-fingerprint'))
    db_session.commit()
    result = reconcile(db_session)
    assert result.status == 'partial'
    assert db_session.query(Dividend).count() == 1
    payload = client.get(BASE + '/dividends').json()['items'][0]
    assert payload['receipt_status'] == 'legacy_unknown'
    assert confirm(client, payload, account).status_code == 409
    assert db_session.query(CashTransaction).count() == int(with_cash)


def test_pending_with_preexisting_leg_blocks_confirmation(client, db_session):
    from app.models.cash_transaction import CashTxnType, CashTxnSource
    account = seed(db_session)
    reconcile(db_session)
    row = client.get(BASE + '/dividends').json()['items'][0]
    db_session.add(CashTransaction(account_id=account, txn_date=date(2026, 9, 10), type=CashTxnType.DIVIDEND_CASH, amount=3090, currency='TWD', related_dividend_id=row['id'], source=CashTxnSource.AUTO_DERIVE, import_fingerprint='unexpected-legacy-leg'))
    db_session.commit()
    assert confirm(client, row, account).status_code == 409
    assert db_session.query(CashTransaction).count() == 1


def test_two_concurrent_confirmations_one_posting(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.exc import OperationalError
    from app.database import Base as ModelBase
    from app.services.dividend_receipt_service import confirm_receipt, ReceiptConflict
    engine = create_engine('sqlite:///' + str(tmp_path / 'receipts.db'), connect_args={'timeout': 10})
    ModelBase.metadata.create_all(engine)
    with Session(engine) as db:
        account = seed(db)
        reconcile(db)
        row = db.query(Dividend).one()
        identity, revision = row.id, row.revision
    barrier = Barrier(2)
    def worker():
        with Session(engine) as db:
            barrier.wait()
            try:
                confirm_receipt(db, identity, date(2026, 9, 11), account, revision)
                return 'confirmed'
            except (ReceiptConflict, OperationalError):
                return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: worker(), range(2)))
    assert 'confirmed' in outcomes
    with Session(engine) as db:
        assert db.query(CashTransaction).count() == 1
        assert db.query(Dividend).one().receipt_status == 'confirmed'
        confirm_receipt(db, identity, date(2026, 9, 11), account, revision)
        assert db.query(CashTransaction).count() == 1
    engine.dispose()


def test_csv_received_date_does_not_confirm_and_cannot_duplicate_after_confirmation(client, db_session):
    account = seed(db_session)
    csv = b'symbol,amount,ex_dividend_date,received_date\n9802,3090,2026-09-10,2026-09-11\n'
    response = client.post(BASE + '/imports/dividends?dry_run=false', files={'file': ('synthetic.csv', csv, 'text/csv')})
    assert response.status_code == 200 and response.json()['created'] == 1
    row = client.get(BASE + '/dividends').json()['items'][0]
    assert row['receipt_status'] == 'pending' and row['received_date'] is None
    assert db_session.query(CashTransaction).count() == 0
    assert confirm(client, row, account).status_code == 200
    changed = csv.replace(b'3090', b'3990')
    response = client.post(BASE + '/imports/dividends?dry_run=false', files={'file': ('synthetic.csv', changed, 'text/csv')})
    assert response.status_code == 200 and response.json()['errors']
    assert db_session.query(Dividend).count() == db_session.query(CashTransaction).count() == 1


def test_snapshots_use_receipt_date_and_pending_does_not_increase_available_cash(client, db_session):
    from app.models.price_history import PriceHistory
    from app.models.portfolio_snapshot import PortfolioSnapshot
    from app.services import networth_backfill_service as snapshots
    account = seed(db_session)
    reconcile(db_session)
    for day in [date(2026, 9, 10), date(2026, 9, 11)]:
        db_session.add(PriceHistory(symbol='9802', market='TW', date=day, close=100, source='TWSE'))
    db_session.commit()
    snapshots.replay_snapshots_range(db_session, date(2026, 9, 10), date(2026, 9, 11))
    assert all(row.total_dividends == 0 for row in db_session.query(PortfolioSnapshot))
    assert Decimal(client.get(BASE + '/accounts/').json()['items'][0]['native_balance']) == 0
    row = client.get(BASE + '/dividends').json()['items'][0]
    assert confirm(client, row, account).status_code == 200
    snapshots.replay_snapshots_range(db_session, date(2026, 9, 10), date(2026, 9, 11))
    rows = {r.date: r for r in db_session.query(PortfolioSnapshot)}
    assert rows[date(2026, 9, 10)].total_dividends == 0
    assert rows[date(2026, 9, 11)].total_dividends == 3090
    assert Decimal(client.get(BASE + '/accounts/').json()['items'][0]['native_balance']) == 3090


def test_stock_pending_confirmation_and_repeat_has_one_zero_cost_buy(client, db_session):
    from app.services.dividend_receipt_service import persist_entitlement
    from app.services.dividend_history_service import HistoricalDividendEvent
    account = seed(db_session)
    event = HistoricalDividendEvent('9802', date(2026, 9, 10), None, Decimal(100), None, None, 'TWT49U')
    row, _ = persist_entitlement(db_session, event, Decimal(1000))
    db_session.commit()
    assert row.amount == 0 and row.stock_dividend_shares == 100
    assert db_session.query(Transaction).count() == 1
    payload = client.get(BASE + '/dividends').json()['items'][0]
    for _ in range(2):
        assert confirm(client, payload, account).status_code == 200
    assert db_session.query(Transaction).count() == 2
    assert db_session.query(CashTransaction).count() == 0
    persist_entitlement(db_session, event, Decimal(1000))
    db_session.commit()
    assert db_session.query(Dividend).one().receipt_status == 'confirmed'
    assert db_session.query(Transaction).count() == 2


def test_position_correction_invalidates_pending_confirmation(client, db_session):
    account = seed(db_session)
    reconcile(db_session)
    row = client.get(BASE + '/dividends').json()['items'][0]
    db_session.add(Transaction(symbol='9802', type=TransactionType.SELL, position_side=PositionSide.LONG, quantity=1000, price=100, trade_date=datetime(2026, 9, 9), fee=0, tax=0))
    db_session.commit()
    reconcile(db_session)
    assert confirm(client, row, account).status_code == 409
    assert db_session.query(Dividend).one().receipt_status == 'unresolved'
    assert db_session.query(CashTransaction).count() == 0


def test_unlinked_matching_cash_is_ambiguous_not_a_second_receipt(client, db_session):
    from app.models.cash_transaction import CashTxnType, CashTxnSource
    account = seed(db_session)
    reconcile(db_session)
    row = client.get(BASE + '/dividends').json()['items'][0]
    db_session.add(CashTransaction(account_id=account, txn_date=date(2026, 9, 11), type=CashTxnType.DIVIDEND_CASH, amount=3090, currency='TWD', source=CashTxnSource.CSV_IMPORT, import_fingerprint='unlinked-bank-import'))
    db_session.commit()
    assert confirm(client, row, account).status_code == 409
    assert db_session.query(CashTransaction).count() == 1
    assert db_session.query(Dividend).one().receipt_status == 'unresolved'
    reconcile(db_session)
    assert db_session.query(Dividend).one().receipt_status == 'unresolved'
    assert db_session.query(CashTransaction).count() == 1


@pytest.mark.parametrize('stored_date,incoming_date', [
    ('2026-09-10T00:00:00+08:00', '2026-09-09T16:00:00Z'),
    ('2026-09-09T16:00:00+00:00', '2026-09-10T00:00:00+08:00'),
])
@pytest.mark.parametrize('identity_change', [None, 'day', 'symbol', 'market'])
def test_pending_edit_uses_tw_calendar_identity(client, db_session, stored_date, incoming_date, identity_change):
    """Real HTTP/schema/service/CAS path, preserving the PG aware-load boundary.

    SQLite drops timezone offsets. Supply its ORM-loaded value as PostgreSQL
    would, without mocking the router, schema, service or update statement.
    Keep the object in the identity map so the service receives this aware value.
    """
    from sqlalchemy.orm.attributes import set_committed_value

    seed(db_session)
    response = client.post(BASE + '/dividends', json={
        'symbol': '9802', 'market': 'TW', 'amount': '100',
        'ex_dividend_date': '2026-09-10T00:00:00+08:00',
    })
    assert response.status_code == 200, response.text
    row = db_session.get(Dividend, response.json()['id'])
    aware = datetime.fromisoformat(stored_date)
    set_committed_value(row, 'ex_dividend_date', aware)
    assert row.ex_dividend_date.tzinfo is not None
    assert db_session.query(Dividend).filter(Dividend.id == row.id).first() is row
    revision = row.revision
    payload = {'symbol': '9802', 'market': 'TW', 'amount': '101', 'ex_dividend_date': incoming_date}
    if identity_change == 'day':
        # Same UTC date as the equivalent instant, but previous Taiwan ex-day.
        payload['ex_dividend_date'] = '2026-09-09T15:59:59Z'
    elif identity_change == 'symbol':
        payload['symbol'] = '2330'
    elif identity_change == 'market':
        payload['market'] = 'US'
    result = client.put(BASE + f'/dividends/{row.id}', json=payload)
    if identity_change is None:
        assert result.status_code == 200, result.text
        assert Decimal(result.json()['amount']) == 101
        assert result.json()['revision'] == revision + 1
    else:
        assert result.status_code == 409, result.text
        assert 'entitlement identity cannot be edited' in result.text
        db_session.refresh(row)
        assert row.amount == 100 and row.revision == revision
        assert row.symbol == '9802' and row.market == 'TW'
    assert db_session.query(CashTransaction).count() == 0
