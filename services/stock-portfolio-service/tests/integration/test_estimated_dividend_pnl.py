"""Synthetic HTTP + SQLite contract, with deterministic quote/source providers."""
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from unittest.mock import patch

import pytest
from sqlalchemy import event

from app.models.portfolio import Dividend, Transaction, TransactionType, PositionSide
from app.models.cash_transaction import CashTransaction
from app.services import portfolio_service as portfolio
from app.services.dividend_receipt_service import TW, entitlement_key
from test_dividend_receipts import seed, reconcile, confirm, BASE


@pytest.fixture(autouse=True)
def providers():
    with patch.object(portfolio, 'get_stock_quotes', return_value={'9802': {'current_price': D(110), 'yesterday_close': D(100)}}), patch.object(portfolio, '_lookup_window_open_price', return_value=None):
        yield


def summary(client):
    response = client.get(BASE + '/summary')
    assert response.status_code == 200, response.text
    return response.json()


def pending(db, **changes):
    values = dict(symbol='9802', market='TW', currency='TWD', amount=D('3090'),
                  ex_dividend_date=datetime(2026, 9, 10), receipt_status='pending',
                  source='auto:TWT49U', quantity_at_record_date=D(1000),
                  entitlement_key=entitlement_key('9802', date(2026, 9, 10)))
    values.update(changes)
    row = Dividend(**values)
    db.add(row)
    db.commit()
    return row


def test_formula_transition_read_only_and_reconciliation(client, db_session):
    account = seed(db_session)
    before = summary(client)
    reconcile(db_session)
    writes = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'}:
            writes.append(statement)
    event.listen(db_session.bind, 'before_cursor_execute', capture)
    try:
        waiting = summary(client)
        assert summary(client) == waiting
    finally:
        event.remove(db_session.bind, 'before_cursor_execute', capture)
    assert writes == []
    assert D(waiting['total_pending_dividends_net']) == 3090
    assert D(waiting['estimated_pnl_with_dividends']) == D(before['total_unrealized_pnl']) + 3090
    assert D(waiting['estimated_pnl_percent']) == (D(waiting['estimated_pnl_with_dividends']) / D(waiting['total_cost']) * 100).quantize(D('.01'))
    h = waiting['holdings'][0]
    assert D(h['pending_dividends_net']) == 3090
    assert D(h['estimated_pnl_with_dividends']) == D(h['unrealized_pnl']) + 3090
    for field in ['total_unrealized_pnl', 'total_market_value', 'total_assets_twd', 'total_cash_twd', 'portfolio_xirr']:
        assert waiting[field] == before[field]
    reconcile(db_session)
    assert summary(client) == waiting
    row = client.get(BASE + '/dividends').json()['items'][0]
    for _ in range(2):
        assert confirm(client, row, account).status_code == 200
    after = summary(client)
    assert after['estimated_pnl_with_dividends'] == waiting['estimated_pnl_with_dividends']
    assert after['estimated_pnl_percent'] == waiting['estimated_pnl_percent']
    assert D(after['total_pending_dividends_net']) == 0
    assert D(after['total_dividends']) == 3090
    assert db_session.query(CashTransaction).count() == 1


@pytest.mark.parametrize('changes', [
    {'receipt_status': 'unresolved'}, {'review_reason': 'review required'},
    {'source_correction': {'amount': '4000'}},
    {'ex_dividend_date': datetime.now(TW) + timedelta(days=2)},
    {'amount': 0, 'stock_dividend_shares': 100},
    {'quantity_at_record_date': D(999)}, {'quantity_at_record_date': None},
    {'market': 'US', 'currency': 'USD'}, {'currency': 'USD'},
])
def test_exclusions(client, db_session, changes):
    seed(db_session)
    pending(db_session, **changes)
    assert D(summary(client)['total_pending_dividends_net']) == 0


@pytest.mark.parametrize('peer_status', ['pending', 'unresolved', 'legacy_unknown', 'confirmed'])
def test_duplicate_event_excludes_pending_preserves_booked(client, db_session, peer_status):
    account = seed(db_session)
    pending(db_session)
    pending(db_session, entitlement_key=None, receipt_status=peer_status, amount=10,
            receipt_date=date(2026, 9, 11) if peer_status == 'confirmed' else None,
            receipt_account_id=account if peer_status == 'confirmed' else None,
            receipt_confirmed_at=datetime.now(TW) if peer_status == 'confirmed' else None)
    s = summary(client)
    assert D(s['total_pending_dividends_net']) == 0
    assert D(s['total_dividends']) == (10 if peer_status in {'legacy_unknown', 'confirmed'} else 0)


@pytest.mark.parametrize('sold', [400, 1000])
def test_post_ex_sale_keeps_full_entitlement_without_phantom_holding(client, db_session, sold):
    seed(db_session)
    pending(db_session)
    db_session.add(Transaction(symbol='9802', type=TransactionType.SELL, position_side=PositionSide.LONG,
        quantity=sold, price=110, fee=0, tax=0, trade_date=datetime(2026, 9, 10, 9)))
    db_session.commit()
    s = summary(client)
    assert D(s['total_pending_dividends_net']) == 3090
    assert D(s['market_totals']['TW']['pending_dividends_net']) == 3090
    assert len(s['holdings']) == (0 if sold == 1000 else 1)
    if sold == 1000:
        assert D(s['estimated_pnl_with_dividends']) == 3090
        assert s['estimated_pnl_percent'] is None


@pytest.mark.parametrize('when', [datetime(2026, 9, 10), datetime(2026, 9, 11)])
def test_buy_on_or_after_ex_date_not_eligible(client, db_session, when):
    seed(db_session)
    db_session.query(Transaction).one().trade_date = when
    db_session.commit()
    pending(db_session)
    assert D(summary(client)['total_pending_dividends_net']) == 0


@pytest.mark.parametrize('source', ['manual', 'csv'])
def test_declared_pending_needs_no_automatic_position_proof(client, db_session, source):
    pending(db_session, source=source, quantity_at_record_date=None)
    s = summary(client)
    assert D(s['total_pending_dividends_net']) == 3090
    assert s['holdings'] == []
    assert db_session.query(CashTransaction).count() == 0


def test_missing_quote_and_zero_cost_do_not_produce_reliable_ratio(client, db_session):
    seed(db_session)
    pending(db_session)
    with patch.object(portfolio, 'get_stock_quotes', return_value={}):
        s = summary(client)
    assert s['estimated_pnl_with_dividends'] is None
    assert s['estimated_pnl_percent'] is None
    assert s['holdings'][0]['estimated_pnl_with_dividends'] is None
    db_session.query(Transaction).one().price = 0
    db_session.commit()
    s = summary(client)
    assert s['estimated_pnl_percent'] is None
    assert s['holdings'][0]['estimated_pnl_percent'] is None


@pytest.mark.parametrize('correction', ['partial_sale', 'short', 'warrant'])
def test_invalid_automatic_entitlement_without_reconcile_is_excluded(client, db_session, correction):
    seed(db_session)
    pending(db_session)
    buy = db_session.query(Transaction).one()
    if correction == 'partial_sale':
        db_session.add(Transaction(symbol='9802', type=TransactionType.SELL, position_side=PositionSide.LONG,
            quantity=1, price=100, fee=0, tax=0, trade_date=datetime(2026, 9, 9)))
    elif correction == 'short':
        buy.position_side = PositionSide.SHORT
    else:
        buy.instrument_type = '認購權證'
    db_session.commit()
    assert D(summary(client)['total_pending_dividends_net']) == 0
    assert db_session.query(Dividend).one().receipt_status == 'pending'


@pytest.mark.parametrize('via_csv', [False, True])
def test_http_manual_csv_declaration_counts_without_receipt(client, db_session, via_csv):
    if via_csv:
        response = client.post(BASE + '/imports/dividends?dry_run=false', files={'file': (
            'synthetic.csv', b'symbol,amount,ex_dividend_date,received_date\n9802,3090,2026-09-10,2026-09-11\n', 'text/csv')})
        assert response.status_code == 200 and response.json()['created'] == 1
    else:
        response = client.post(BASE + '/dividends', json={'symbol': '9802', 'amount': '3090',
            'ex_dividend_date': '2026-09-10T00:00:00+08:00', 'received_date': '2026-09-11T00:00:00+08:00'})
        assert response.status_code == 200
    assert D(summary(client)['estimated_pnl_with_dividends']) == 3090
    assert db_session.query(Dividend).one().receipt_status == 'pending'
    assert db_session.query(CashTransaction).count() == 0


def test_tw_calendar_cutoff_with_aware_instants(db_session):
    from sqlalchemy.orm.attributes import set_committed_value
    seed(db_session)
    row = pending(db_session)
    buy = db_session.query(Transaction).one()
    set_committed_value(row, 'ex_dividend_date', datetime.fromisoformat('2026-09-09T16:00:00+00:00'))
    for instant, expected in [('2026-09-09T15:59:59+00:00', 3090), ('2026-09-09T16:00:00+00:00', 0)]:
        set_committed_value(buy, 'trade_date', datetime.fromisoformat(instant))
        assert portfolio._pending_dividend_totals(db_session, [row], [buy]).get(('9802', 'TW'), 0) == expected


@pytest.mark.parametrize('missing', [None, 'quote', 'fx', 'currency_mismatch'])
def test_same_symbol_foreign_native_totals_never_include_tw_pending(client, db_session, monkeypatch, missing):
    from app.models.price_history import PriceHistory
    from app.models.fx_rate import FXRate
    class FrozenDate(date):
        @classmethod
        def today(cls):
            return date(2026, 9, 12)
    monkeypatch.setattr(portfolio, 'date_type', FrozenDate)
    seed(db_session)
    pending(db_session)
    for market, currency, fx in [('US', 'USD', 32), ('LSE', 'GBP', 40)]:
        db_session.add(Transaction(symbol='9802', market=market, currency=currency,
            fx_rate_to_twd=fx, type=TransactionType.BUY, position_side=PositionSide.LONG,
            quantity=10, price=100, fee=0, tax=0, trade_date=datetime(2026, 1, 1)))
        if missing != 'quote':
            db_session.add(PriceHistory(symbol='9802', market=market, date=date(2026, 9, 12),
                close=110, currency='EUR' if missing == 'currency_mismatch' else currency, source='synthetic'))
        if missing != 'fx':
            db_session.add(FXRate(currency=currency, date=date(2026, 9, 12), rate_to_twd=fx, source='synthetic'))
    db_session.commit()
    s = summary(client)
    assert D(s['total_pending_dividends_net']) == 3090
    for market in ['US', 'LSE']:
        h = next(h for h in s['holdings'] if h['market'] == market)
        m = s['market_totals'][market]
        assert D(h['pending_dividends_net']) == D(m['pending_dividends_net']) == 0
        if missing in {'quote', 'currency_mismatch'}:
            assert h['estimated_pnl_with_dividends_native'] is None
            assert m['estimated_pnl_percent'] is None
        else:
            assert D(h['estimated_pnl_with_dividends_native']) == D(m['estimated_pnl_with_dividends']) == 100
            assert D(h['estimated_pnl_percent_native']) == D(m['estimated_pnl_percent']) == 10
        if missing in {'quote', 'fx', 'currency_mismatch'}:
            assert s['estimated_pnl_with_dividends'] is None


def test_pending_with_existing_cash_leg_is_not_estimated_again(client, db_session):
    from app.models.cash_transaction import CashTxnType, CashTxnSource
    account = seed(db_session)
    row = pending(db_session)
    db_session.add(CashTransaction(account_id=account, txn_date=date(2026, 9, 10),
        type=CashTxnType.DIVIDEND_CASH, amount=3090, currency='TWD', related_dividend_id=row.id,
        source=CashTxnSource.AUTO_DERIVE, import_fingerprint='synthetic-conflict'))
    db_session.commit()
    assert D(summary(client)['total_pending_dividends_net']) == 0
    assert db_session.query(CashTransaction).count() == 1
    assert db_session.query(Dividend).one().receipt_status == 'pending'


def test_no_dividends_is_pure_price_with_matching_ratio(client, db_session):
    seed(db_session)
    s = summary(client)
    assert D(s['total_pending_dividends_net']) == D(s['total_dividends']) == 0
    assert s['estimated_pnl_with_dividends'] == s['total_unrealized_pnl']
    assert s['estimated_pnl_percent'] == s['total_unrealized_pnl_percent']
    h = s['holdings'][0]
    assert h['estimated_pnl_with_dividends'] == h['unrealized_pnl']
    assert h['estimated_pnl_percent'] == h['unrealized_pnl_percent']
