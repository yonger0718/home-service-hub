"""Historical reconciliation records pending entitlements, never cash receipts.

Use the existing historical parsers, but validate source envelopes and keep
errors. Payloads are shared only within this invocation, not process-cached:
transient outages (and current-year absence) must be retried on the next run.
All new TW dividend entry paths require explicit receipt confirmation before posting.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.portfolio import PositionSide, Transaction, TransactionType
from ..models.symbol_map import SymbolMap
from . import dividend_history_service as history, dividend_receipt_service as receipts

DEFERRED_REASON = 'entitlement recorded; explicit receipt confirmation required'
_TW = timezone(timedelta(hours=8))


def _envelope(payload, source: str) -> dict:
    if payload is None:
        raise ValueError('source unavailable')
    data = history._coerce_dict(payload)
    if source == 'TWT49U':
        if str(data.get('stat', '')).upper() != 'OK' or not isinstance(data.get('data'), list):
            raise ValueError('invalid historical JSON envelope (expected stat OK and data array)')
        if not isinstance(data.get('fields'), list):
            raise ValueError('missing historical field schema')
    else:
        tables = data.get('tables')
        if not isinstance(tables, list) or not tables or not isinstance(tables[0], dict) or not isinstance(tables[0].get('data'), list):
            raise ValueError('invalid TPEx historical JSON envelope')
        if 'stat' in data and str(data['stat']).upper() != 'OK':
            raise ValueError('TPEx returned unsuccessful status')
    return data


def _warrant(instrument: str | None) -> bool:
    return any(token in (instrument or '') for token in ('認購', '認售', '牛證', '熊證'))


def _eligible_qty(trades: list[Transaction], ex_date: date, mapped_types: list[str]) -> Decimal:
    cutoff = datetime.combine(ex_date, time.min, _TW)
    qty = Decimal(0)
    for tx in trades:
        # SQLite drops tzinfo; production TIMESTAMPTZ retains it. Treat naive
        # dates as TW local, matching the legacy before-ex-date convention.
        when = tx.trade_date if tx.trade_date.tzinfo else tx.trade_date.replace(tzinfo=_TW)
        instrument = tx.instrument_type
        ineligible = _warrant(instrument) if instrument is not None else any(_warrant(t) for t in mapped_types)
        if tx.position_side != PositionSide.LONG or ineligible or when >= cutoff:
            continue
        qty += Decimal(tx.quantity) * (1 if tx.type == TransactionType.BUY else -1)
    return qty


def reconcile(db: Session, symbols: set[str], start: date, end: date) -> dict:
    detail = {'events_processed': 0, 'cash_inserted': 0, 'stock_inserted': 0,
              'deferred_events': [], 'source_errors': [], 'event_errors': [], 'sources_succeeded': 0, 'entitlements': [], 'pending_count': 0}
    if start > end:
        raise ValueError('reconciliation start is after end')
    payloads = {}
    seen = set()

    def error(source, symbol, year, reason):
        detail['source_errors'].append({'source': source, 'symbol': symbol, 'year': year, 'reason': str(reason)})

    for symbol in sorted(symbols):
        trades = list(db.scalars(select(Transaction).where(Transaction.symbol == symbol, Transaction.market == 'TW')))
        if not trades:
            continue  # Do not query Taiwan sources for foreign-only symbols.
        maps = list(db.scalars(select(SymbolMap).where(SymbolMap.symbol == symbol, SymbolMap.market == 'TW')))
        exchange_aliases = {'上市': 'TWSE', '上櫃': 'TPEX'}
        exchanges = {exchange_aliases.get(str(m.exchange), str(m.exchange).upper()) for m in maps if m.exchange}
        sources = ['TWT49U'] if exchanges == {'TWSE'} else ['TPEX'] if exchanges == {'TPEX'} else ['TWT49U', 'TPEX']
        for year in range(start.year, end.year + 1):
            for source in sources:
                key = (source, year)
                if key not in payloads:
                    try:
                        raw = history._http_get(history.TWT49U_URL, {'startDate': f'{year}0101', 'endDate': f'{year}1231', 'response': 'json'}) if source == 'TWT49U' else history._http_post_form(history.TPEX_EX_DAILY_Q_URL, {'startDate': f'{year}/01/01', 'endDate': f'{year}/12/31', 'response': 'json'})
                        payloads[key] = (_envelope(raw, source), None)
                    except Exception as exc:  # isolate sources; failure is not absence
                        payloads[key] = (None, str(exc))
                payload, failure = payloads[key]
                if failure:
                    error(source, symbol, year, failure)
                    continue
                detail['sources_succeeded'] += 1
                try:
                    payments = {}
                    if source == 'TWT49U':
                        fields = payload['fields']
                        if payload['data'] and not {'資料日期', '股票代號', '權值+息值', '權/息'}.issubset(fields):
                            raise ValueError('unrecognized historical field schema')
                        for row in payload['data']:
                            if not isinstance(row, list) or len(row) < len(fields) or not history._roc_to_date(str(row[fields.index('資料日期')])):
                                error(source, symbol, year, 'malformed historical row')
                        rows = history.parse_twt49u_response(symbol, payload)
                        rows = [r for r in rows if start <= r[0] <= end]

                        def fetch_detail(sym, param):
                            try:
                                values = history._fetch_detail_raw(sym, param)
                                if values == (None, None):
                                    error(source, symbol, year, f'detail unavailable: {param}')
                                return values
                            except Exception as exc:
                                error(source, symbol, year, f'detail unavailable: {param}: {exc}')
                                return None, None

                        events = history.events_from_twse_rows(symbol, rows, detail_fetcher=fetch_detail)
                        if len(events) != len(rows):
                            error(source, symbol, year, 'event amount unresolved after detail/fallback')
                        # Only use an explicit source field. Never infer pay date
                        # from ex-date or inject the operator-supplied 9802 date.
                        if '現金股利發放日' in fields:
                            for row in payload['data']:
                                if isinstance(row, list) and len(row) >= len(fields) and str(row[fields.index('股票代號')]).strip() == symbol:
                                    ex = history._roc_to_date(str(row[fields.index('資料日期')]))
                                    payments[ex] = history._roc_to_date(str(row[fields.index('現金股利發放日')]))
                    else:
                        for row in payload['tables'][0]['data']:
                            if not isinstance(row, list) or len(row) < 15 or not history._roc_to_date(str(row[0])):
                                error(source, symbol, year, 'malformed TPEx historical row')
                            elif str(row[1]).strip() == symbol and start <= history._roc_to_date(str(row[0])) <= end and history._decimal_or_none(row[13]) is None and history._decimal_or_none(row[14]) is None:
                                error(source, symbol, year, 'TPEx event amount unresolved')
                        events = [history.HistoricalDividendEvent(sym, ex, cash, stock, None, None, source)
                                  for sym, ex, cash, stock in history._parse_tpex_history(payload) if sym == symbol and start <= ex <= end]
                    for event in events:
                        identity = (symbol, event.ex_date)
                        if identity in seen:
                            continue
                        qty = _eligible_qty(trades, event.ex_date, [m.type for m in maps])
                        if qty <= 0:
                            if receipts.invalidate_entitlement(db, symbol, event.ex_date):
                                error(source, symbol, year, 'position correction requires explicit resolution')
                            continue
                        seen.add(identity)
                        payment = payments.get(event.ex_date)
                        with db.begin_nested():
                            record, reason = receipts.persist_entitlement(db, event, qty, payment)
                        detail['entitlements'].append({'id': record.id if record else None, 'symbol': symbol, 'status': record.receipt_status if record else 'unresolved', 'reason': reason})
                        if record and record.receipt_status == 'pending':
                            detail['pending_count'] += 1
                        if reason:
                            error(source, symbol, year, reason)
                        if record and record.receipt_status == 'confirmed' and not reason:
                            continue
                        detail['deferred_events'].append({
                            'symbol': symbol, 'ex_date': event.ex_date.isoformat(),
                            'cash_dividend_per_share': str(event.cash_dividend_per_share) if event.cash_dividend_per_share is not None else None,
                            'stock_dividend_per_thousand': str(event.stock_dividend_per_thousand) if event.stock_dividend_per_thousand is not None else None,
                            'eligible_quantity': str(qty), 'payment_date': payment.isoformat() if payment else None,
                            'id': record.id if record else None, 'source': source, 'status': record.receipt_status if record else 'unresolved', 'reason': reason or DEFERRED_REASON,
                        })
                except Exception as exc:
                    error(source, symbol, year, exc)
    detail['events_processed'] = len(detail['entitlements'])
    return detail
