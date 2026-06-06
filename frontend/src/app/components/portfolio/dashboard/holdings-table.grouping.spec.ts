import { CommonModule } from '@angular/common';
import { Component, Input } from '@angular/core';
import { DeferBlockBehavior, TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { NativeAmountPipe } from '../../../pipes/native-amount.pipe';
import { PortfolioSummary, StockHolding } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';
import { PortfolioDashboardComponent } from './dashboard';

@Component({
  selector: 'p-chart',
  standalone: true,
  template: '',
})
class ChartStubComponent {
  @Input() type = '';
  @Input() data: unknown;
  @Input() options: unknown;
  chart = { update: vi.fn() };
}

describe('PortfolioDashboardComponent holding groups', () => {
  let portfolioService: {
    refreshQuotes: ReturnType<typeof vi.fn>;
    getRecalcStatus: ReturnType<typeof vi.fn>;
    getSummary: ReturnType<typeof vi.fn>;
    getUpcomingExDividends: ReturnType<typeof vi.fn>;
    getNetworthHistory: ReturnType<typeof vi.fn>;
    cashLedgerChanged$: import('rxjs').Observable<void>;
  };

  beforeEach(async () => {
    vi.useFakeTimers();
    const { NEVER } = await import('rxjs');
    portfolioService = {
      refreshQuotes: vi.fn().mockReturnValue(of(null)),
      getRecalcStatus: vi.fn(),
      getSummary: vi.fn().mockReturnValue(of(buildSummary([buildHolding()]))),
      getUpcomingExDividends: vi.fn().mockReturnValue(of([])),
      getNetworthHistory: vi.fn().mockReturnValue(of([])),
      cashLedgerChanged$: NEVER,
    };

    await TestBed.configureTestingModule({
      imports: [PortfolioDashboardComponent],
      providers: [{ provide: PortfolioService, useValue: portfolioService }],
      deferBlockBehavior: DeferBlockBehavior.Manual,
    })
      .overrideComponent(PortfolioDashboardComponent, {
        set: {
          imports: [CommonModule, ChartStubComponent, NativeAmountPipe],
          template: `
            @if (showMarketGroups()) {
              @for (group of holdingGroups(); track group.market) {
                <section class="market-group">
                  <h4>{{ group.market }}</h4>
                  @for (stock of group.holdings; track holdingTrackKey(stock)) {
                    <article class="stock-row" [attr.data-key]="holdingTrackKey(stock)">
                      <span class="symbol">{{ stock.symbol }}</span>
                      @if (stock.market !== 'TW') {
                        <span class="market-badge">{{ stock.market }}</span>
                        <span class="native-price">{{ stock.native_close | nativeAmount: stock.native_currency }}</span>
                      }
                      <span class="market-value">{{ marketValueDisplay(stock) }}</span>
                      @if (fxTooltip(stock)) {
                        <i class="fx-info" [attr.title]="fxTooltip(stock)"></i>
                      }
                    </article>
                  }
                </section>
              }
            } @else {
              @for (stock of flatHoldings(); track holdingTrackKey(stock)) {
                <article class="stock-row" [attr.data-key]="holdingTrackKey(stock)">
                  <span class="symbol">{{ stock.symbol }}</span>
                </article>
              }
            }
          `,
        },
      })
      .compileComponents();
  });

  it('renders TW-only holdings without group chrome', () => {
    const fixture = TestBed.createComponent(PortfolioDashboardComponent);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelectorAll('.stock-row')).toHaveLength(1);
    expect(fixture.nativeElement.querySelector('.market-group')).toBeNull();
    expect(fixture.nativeElement.querySelector('.market-badge')).toBeNull();
  });

  it('renders mixed markets in fixed order with native price and FX tooltip', () => {
    portfolioService.getSummary.mockReturnValue(of(buildSummary([
      buildHolding({ symbol: 'VOD', market: 'LSE', native_close: 8050, native_currency: 'GBp', live_fx_rate_to_twd: 0.41, unrealized_pnl: -5 }),
      buildHolding({ symbol: 'AAPL', market: 'US', native_close: 190.5, native_currency: 'USD', live_fx_rate_to_twd: 31.45, unrealized_pnl: 20 }),
      buildHolding({ symbol: '2330', market: 'TW', unrealized_pnl: 10 }),
    ])));
    const fixture = TestBed.createComponent(PortfolioDashboardComponent);
    fixture.detectChanges();

    const root = fixture.nativeElement as HTMLElement;
    const headers = Array.from(root.querySelectorAll('h4')).map(
      element => element.textContent?.trim(),
    );
    expect(headers).toEqual(['TW', 'US', 'LSE']);
    expect(fixture.nativeElement.textContent).toContain('190.50 USD');
    expect(fixture.nativeElement.textContent).toContain('8050.0000 GBp');
    expect(fixture.nativeElement.querySelector('.fx-info')?.getAttribute('title')).toBe(
      'Revalued at 1 USD = 31.45 TWD',
    );
  });

  it('exposes one filter option per present market plus All, and filters rows on selection', () => {
    portfolioService.getSummary.mockReturnValue(of(buildSummary([
      buildHolding({ symbol: '2330', market: 'TW' }),
      buildHolding({ symbol: 'AAPL', market: 'US', native_close: 190.5, native_currency: 'USD' }),
      buildHolding({ symbol: 'VOD', market: 'LSE', native_close: 8050, native_currency: 'GBp' }),
    ])));
    const fixture = TestBed.createComponent(PortfolioDashboardComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    expect(component.marketFilterOptions().map(o => o.value)).toEqual(['ALL', 'TW', 'US', 'LSE']);
    expect(component.filteredHoldings()).toHaveLength(3);
    expect(component.showMarketGroups()).toBe(true);

    component.selectMarketFilter('US');
    expect(component.filteredHoldings().map(h => h.symbol)).toEqual(['AAPL']);
    expect(component.showMarketGroups()).toBe(false);
  });

  it('recomputes KPI totals from filtered holdings when a market is selected', () => {
    portfolioService.getSummary.mockReturnValue(of(buildSummary([
      buildHolding({ symbol: '2330', market: 'TW', market_value: 6500, unrealized_pnl: 1500, total_dividends: 100, avg_cost: 500, total_quantity: 10 }),
      buildHolding({ symbol: 'AAPL', market: 'US', market_value: 5994, unrealized_pnl: 200, total_dividends: 40, avg_cost: 30, total_quantity: 10 }),
    ])));
    const fixture = TestBed.createComponent(PortfolioDashboardComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    expect(component.isMarketFiltered()).toBe(false);

    component.selectMarketFilter('US');
    expect(component.isMarketFiltered()).toBe(true);
    const us = component.filteredTotals()!;
    expect(us.total_market_value).toBeCloseTo(5994, 1);
    expect(us.total_unrealized_pnl).toBeCloseTo(200, 1);
    expect(us.total_dividends).toBeCloseTo(40, 1);
    expect(us.total_cost).toBeCloseTo(300, 1);
  });

  it('keeps market grouping when rows are sorted by unrealized PnL', () => {
    const component = TestBed.createComponent(PortfolioDashboardComponent).componentInstance;
    const grouped = component.groupHoldingsByMarket([
      buildHolding({ symbol: 'US2', market: 'US', unrealized_pnl: 10 }),
      buildHolding({ symbol: 'TW1', market: 'TW', unrealized_pnl: 5 }),
      buildHolding({ symbol: 'US1', market: 'US', unrealized_pnl: 30 }),
      buildHolding({ symbol: 'LSE1', market: 'LSE', unrealized_pnl: 20 }),
    ], 'unrealized_pnl', 'desc');

    expect(grouped.map(group => group.market)).toEqual(['TW', 'US', 'LSE']);
    expect(grouped[1].holdings.map(holding => holding.symbol)).toEqual(['US1', 'US2']);
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
    xirr: 0.5,
    xirr_1m: 0.01,
    xirr_3m: 0.0321,
    xirr_1y: 0.1234,
    xirr_ytd: 0.04,
    ...overrides,
  };
}
