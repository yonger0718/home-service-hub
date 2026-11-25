import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { holdingKey, PortfolioSummary, StockHolding } from '../models/portfolio.model';
import { PortfolioService } from './portfolio.service';

describe('PortfolioService', () => {
  let service: PortfolioService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), PortfolioService],
    });
    service = TestBed.inject(PortfolioService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  it('reads the 國泰 pending days and latest runs', () => {
    service.getCathayPending().subscribe();
    const pending = httpMock.expectOne('/api/portfolio/imports/cathay-statement/pending');
    expect(pending.request.method).toBe('GET');
    pending.flush({ items: [], total: 0, truncated: false });
    service.getCathayRuns().subscribe();
    const runs = httpMock.expectOne('/api/portfolio/imports/cathay-statement/runs/latest');
    expect(runs.request.method).toBe('GET');
    runs.flush({ latest: null, latest_ok: null });
  });

  it('reads the futures summary, sending as_of only when given', () => {
    service.getFuturesSummary().subscribe();
    const plain = httpMock.expectOne(r => r.url === '/api/portfolio/futures/summary');
    expect(plain.request.params.has('as_of')).toBe(false);
    plain.flush({ as_of: '2026-11-23', value: null, latest_daily: null, month: null });
    service.getFuturesSummary('2026-10-31').subscribe();
    const dated = httpMock.expectOne(r => r.url === '/api/portfolio/futures/summary');
    expect(dated.request.params.get('as_of')).toBe('2026-10-31');
    dated.flush({ as_of: '2026-10-31', value: null, latest_daily: null, month: null });
  });

  it('acks a 國泰 day with a body-less POST', () => {
    service.ackCathayDay('2026-10-28').subscribe();
    const req = httpMock.expectOne('/api/portfolio/imports/cathay-statement/days/2026-10-28/ack');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toBeNull();
    req.flush({ trade_date: '2026-10-28', ack_status: 'acked' });
  });

  it('caches same-symbol holdings by composite key', () => {
    const tw = buildHolding({ symbol: 'AAPL', market: 'TW' });
    const us = buildHolding({ symbol: 'AAPL', market: 'US' });

    service.getSummary().subscribe();
    const req = httpMock.expectOne('/api/portfolio/summary');
    req.flush(buildSummary([tw, us]));

    expect(service.getCachedHolding(holdingKey(tw))).toEqual(tw);
    expect(service.getCachedHolding(holdingKey(us))).toEqual(us);
    expect(service.getCachedHoldings()).toHaveLength(2);
    expect(service.getCachedHolding('AAPL' as any)).toBeUndefined();
  });
});

function buildSummary(holdings: StockHolding[]): PortfolioSummary {
  return {
    total_market_value: 1000,
    total_cash_twd: '0',
    total_assets_twd: '1000',
    total_cost: 900,
    total_unrealized_pnl: 100,
    total_unrealized_pnl_percent: 11.11,
    total_day_pnl: 10,
    total_dividends: 20,
    total_realized_pnl: 0,
    portfolio_xirr: 0.5,
    portfolio_xirr_1m: 0.01,
    portfolio_xirr_3m: 0.0321,
    portfolio_xirr_1y: 0.1234,
    portfolio_xirr_ytd: 0.04,
    holdings,
  };
}

function buildHolding(overrides: Partial<StockHolding> = {}): StockHolding {
  return {
    symbol: '2330',
    market: 'TW',
    name: '台積電',
    total_quantity: 10,
    avg_cost: 500,
    current_price: 650,
    market_value: 6500,
    unrealized_pnl: 1500,
    unrealized_pnl_percent: 30,
    day_change_amount: 5,
    day_change_percent: 0.77,
    day_pnl: 50,
    total_dividends: 100,
    total_pnl_with_dividend: 1600,
    native_close: 650,
    native_currency: 'TWD',
    live_fx_rate_to_twd: null,
    avg_cost_native: null,
    market_value_native: null,
    unrealized_pnl_native: null,
    unrealized_pnl_percent_native: null,
    total_dividends_native: null,
    total_pnl_with_dividend_native: null,
    xirr: 0.5,
    xirr_1m: 0.01,
    xirr_3m: 0.0321,
    xirr_1y: 0.1234,
    xirr_ytd: 0.04,
    ...overrides,
  };
}
