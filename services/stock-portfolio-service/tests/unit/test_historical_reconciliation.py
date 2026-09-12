"""Real caller + historical parser, synthetic SQLite; HTTP boundaries only."""
import asyncio
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.portfolio import Transaction, TransactionType, PositionSide, Dividend
from app.models.cash_transaction import CashTransaction
from app.models.symbol_map import SymbolMap
from app.services import post_import_orchestrator as orch, dividend_history_service as history

TW = timezone(timedelta(hours=8))
FIXTURE = (Path(__file__).parents[1] / 'fixtures/twt49u-2026-09.json').read_bytes()


def trade(db, symbol='9802', when=date(2026, 9, 1), side=PositionSide.LONG, market='TW', type_=TransactionType.BUY, instrument=None):
    db.add(Transaction(symbol=symbol, type=type_, position_side=side, market=market,
                       quantity=1000, price=100, trade_date=datetime.combine(when, datetime.min.time(), TW),
                       fee=0, tax=0, instrument_type=instrument))
    db.flush()


@contextmanager
def factory(db):
    yield db


def step(db, start=date(2026, 9, 1), end=date(2026, 9, 12), symbols=None):
    return orch._step_dividends(lambda: factory(db), symbols or {'9802'}, start, end)


@pytest.fixture(autouse=True)
def sources():
    orch.reset_state_for_tests()
    with patch.object(history, '_http_get', return_value=FIXTURE) as get, \
         patch.object(history, '_http_post_form', return_value={'tables': [{'data': []}]}), \
         patch.object(history, '_fetch_detail_raw', return_value=(None, None)), \
         patch('app.services.dividend_event_service.fetch_for_holdings', return_value=[]):
        yield get
    orch.reset_state_for_tests()


def test_real_fixture_pending_no_cash_repeat(db_session):
    trade(db_session)
    with patch('app.services.dividend_auto_record_service.auto_record_for_event', side_effect=AssertionError('legacy recorder forbidden')):
        first, second = step(db_session), step(db_session)
    assert first.status == second.status == 'partial'
    assert first.detail['deferred_events'] == second.detail['deferred_events']
    row = first.detail['deferred_events'][0]
    assert row['symbol'] == '9802' and row['ex_date'] == '2026-09-10'
    assert row['cash_dividend_per_share'] == '3.100000'
    assert row['payment_date'] is None
    assert row['reason'] == 'entitlement recorded; explicit receipt confirmation required'
    assert len(db_session.scalars(select(Dividend)).all()) == 1
    assert db_session.query(Dividend).one().receipt_status == 'pending'
    assert not db_session.scalars(select(CashTransaction)).all()
    assert len(db_session.scalars(select(Transaction)).all()) == 1


@pytest.mark.parametrize('kwargs', [
    {'when': date(2026, 9, 10)}, {'when': date(2026, 9, 11)},
    {'side': PositionSide.SHORT}, {'market': 'US'}, {'instrument': '上市認購權證'},
])
def test_ineligible_positions_do_not_acquire_event(db_session, kwargs):
    trade(db_session, **kwargs)
    result = step(db_session)
    assert not result.detail.get('deferred_events')
    assert not db_session.scalars(select(Dividend)).all()


def test_sold_before_ex_date_and_outside_requested_range(db_session):
    trade(db_session)
    trade(db_session, when=date(2026, 9, 9), type_=TransactionType.SELL)
    assert not step(db_session).detail.get('deferred_events')
    assert not step(db_session, end=date(2026, 9, 9)).detail.get('deferred_events')


@pytest.mark.parametrize('payload', [None, b'<html>blocked</html>', {'stat': 'OK'}, {'stat': 'OK', 'data': 'bad'}, {'stat': 'OK', 'data': [['broken']], 'fields': []}])
def test_source_failure_is_not_healthy_empty(db_session, sources, payload):
    trade(db_session)
    sources.return_value = payload
    result = step(db_session)
    assert result.status in {'failed', 'partial'}
    assert result.detail['source_errors']


def test_valid_empty_is_success(db_session, sources):
    trade(db_session)
    sources.return_value = {'stat': 'OK', 'data': [], 'fields': []}
    result = step(db_session)
    assert result.status == 'ok' and result.detail['deferred_events'] == []


def test_failure_not_cached_across_runs(db_session, sources):
    trade(db_session)
    sources.return_value = b'<html>blocked</html>'
    assert step(db_session).detail['source_errors']
    sources.return_value = FIXTURE
    assert step(db_session).detail['deferred_events'][0]['cash_dividend_per_share'] == '3.100000'


def test_historical_year_and_market_routing(db_session, sources):
    trade(db_session, when=date(2020, 1, 1))
    db_session.add(SymbolMap(name='synthetic', symbol='9802', exchange='上市', market='TW'))
    db_session.flush()
    sources.return_value = {'stat': 'OK', 'fields': ['資料日期', '股票代號', '權值+息值', '權/息'], 'data': [['109/09/10', '9802', '3.1', '息']]}
    with patch.object(history, '_http_post_form', side_effect=AssertionError('wrong market')):
        result = step(db_session, date(2020, 1, 1), date(2020, 12, 31))
    assert result.detail['deferred_events'][0]['ex_date'] == '2020-09-10'
    assert sources.call_args.args[1]['startDate'] == '20200101'


def test_detail_failure_visible_with_fallback_amount(db_session):
    trade(db_session)
    with patch.object(history, '_fetch_detail_raw', return_value=(None, None)):
        result = step(db_session)
    assert result.status == 'partial'
    assert result.detail['source_errors']
    assert result.detail['deferred_events'][0]['cash_dividend_per_share'] == '3.100000'


def test_full_chain_failure_and_deferred_survive_quotes_and_ttl(db_session):
    trade(db_session)
    with patch.object(orch, '_step_symbol_map_backfill', return_value=orch.StepResult('symbol_map_backfill', 'ok')), \
         patch.object(orch, '_step_networth_backfill', return_value=orch.StepResult('networth_backfill', 'ok')):
        full = asyncio.run(orch.run_chain(lambda: factory(db_session), recalc_from=date(2026, 9, 1), recalc_to=date(2026, 9, 12), touched_symbols={'9802'}))
        assert full.state == 'partial'
        full.finished_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        asyncio.run(orch.run_chain_quotes_only(lambda: factory(db_session), today=date(2026, 9, 12), touched_symbols={'9802'}))
    status = orch.latest_status()
    assert status['state'] == 'partial'
    assert status['kind'] == 'import'
    assert status['quote_refresh']['state'] == 'completed'
    assert status['steps'][1]['detail']['deferred_events']
    assert status['steps'][2]['status'] == 'ok'


def test_tpex_historical_and_unknown_source_partial(db_session):
    trade(db_session, symbol='6488', when=date(2020, 1, 1))
    db_session.add(SymbolMap(name='OTC', symbol='6488', exchange='TPEX', market='TW'))
    db_session.flush()
    payload = {'tables': [{'data': [['109/09/10', '6488', 'fixture', 0, 0, 0, 0, 0, '息', 0, 0, 0, 0, '2.5', '0']]}]}
    with patch.object(history, '_http_get', side_effect=AssertionError('wrong market')), patch.object(history, '_http_post_form', return_value=payload) as post:
        result = step(db_session, date(2020, 1, 1), date(2020, 12, 31), {'6488'})
    assert result.detail['deferred_events'][0]['cash_dividend_per_share'] == '2.5'
    assert result.detail['deferred_events'][0]['source'] == 'TPEX'
    assert post.call_args.args[1]['startDate'] == '2020/01/01'


def test_one_source_outage_stays_visible_with_other_event(db_session, sources):
    trade(db_session)
    with patch.object(history, '_http_post_form', return_value=None):
        result = step(db_session)
    assert result.status == 'partial'
    assert result.detail['deferred_events']
    assert any(e['source'] == 'TPEX' for e in result.detail['source_errors'])


def test_explicit_source_payment_date_does_not_enable_recording(db_session, sources):
    trade(db_session)
    sources.return_value = {'stat': 'OK', 'fields': ['資料日期', '股票代號', '權值+息值', '權/息', '現金股利發放日'], 'data': [['115/09/10', '9802', '3.1', '息', '115/10/15']]}
    result = step(db_session)
    assert result.status == 'partial'
    assert result.detail['deferred_events'][0]['payment_date'] == '2026-10-15'
    assert len(db_session.scalars(select(Dividend)).all()) == 1
    assert db_session.query(Dividend).one().receipt_status == 'pending'
    assert not db_session.scalars(select(CashTransaction)).all()


def test_actual_http_html_response_propagates_to_chain_step(db_session, monkeypatch):
    from app.services import market_data_service
    from unittest.mock import MagicMock
    trade(db_session)
    response = MagicMock(content=b'<html>upstream block</html>')
    monkeypatch.setattr(history, '_http_get', market_data_service._http_get)
    with patch.object(market_data_service.requests, 'get', return_value=response), patch.object(history, '_http_post_form', return_value=None):
        result = step(db_session)
    assert result.status == 'failed'
    assert len(result.detail['source_errors']) == 2


def test_history_requests_shared_within_run_but_not_across_runs(db_session, sources):
    trade(db_session)
    trade(db_session, symbol='2330')
    db_session.add_all([SymbolMap(name=sym, symbol=sym, exchange='TWSE', market='TW') for sym in ['9802', '2330']])
    db_session.flush()
    step(db_session, symbols={'9802', '2330'})
    assert sources.call_count == 1
    step(db_session, symbols={'9802', '2330'})
    assert sources.call_count == 2


def test_foreign_quantity_not_counted_for_tw_event(db_session):
    trade(db_session)
    trade(db_session, market='US')
    result = step(db_session)
    assert result.detail['deferred_events'][0]['eligible_quantity'] == '1000.0000'


def test_status_storage_bounded_to_latest_per_kind():
    for n in range(10):
        for kind in ['import', 'quotes']:
            orch._store(orch.ChainResult(state='partial', started_at=f'2026-09-12T00:00:{n:02d}+00:00' + kind, kind=kind))
    assert len(orch._LATEST_RESULTS) == 2
    assert orch.latest_status()['kind'] == 'import'
