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
    xirr: 0.5,
    xirr_1m: 0.01,
    xirr_3m: 0.0321,
    xirr_1y: 0.1234,
    xirr_ytd: 0.04,
    ...overrides,
  };
}
