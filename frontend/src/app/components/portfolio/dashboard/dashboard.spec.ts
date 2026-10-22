import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { DeferBlockBehavior, DeferBlockState, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter, RouterLink } from '@angular/router';
import { of, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Dividend, PortfolioSummary, StockHolding } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';
import { AppearanceService } from '../../../services/appearance.service';
import { DASHBOARD_CLOCK, PortfolioDashboardComponent } from './dashboard';
import { hoverGuidePlugin } from './chart-guide.plugin';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { BtnComponent } from '../../ui/btn/btn';
import { PageHeaderComponent } from '../../shell/page-header/page-header';
import { SegToggleComponent } from '../../ui/seg-toggle/seg-toggle';
import { BentoComponent } from '../../ui/bento/bento';
import { PctBadgeComponent } from '../../ui/pct-badge/pct-badge';
import { TooltipModule } from 'primeng/tooltip';
import { SkeletonModule } from 'primeng/skeleton';
import { NativeAmountPipe } from '../../../pipes/native-amount.pipe';

@Component({
  selector: 'p-chart',
  standalone: true,
  template: '',
})
class ChartStubComponent {
  @Input() type = '';
  @Input() data: unknown;
  @Input() options: unknown;
  @Input() plugins: unknown;
  chart = {
    update: vi.fn(),
    setActiveElements: vi.fn(),
    tooltip: { setActiveElements: vi.fn() },
    getDatasetMeta: vi.fn(() => ({ data: [{ x: 10, y: 20 }, { x: 30, y: 40 }] })),
  };
}

describe('PortfolioDashboardComponent', () => {
  function buildHolding(): StockHolding {
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
    };
  }

  function buildSummary(overrides: Partial<PortfolioSummary> = {}): PortfolioSummary {
    return {
      total_market_value: 1000,
      total_cash_twd: '250',
      total_assets_twd: '1250',
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
      holdings: [buildHolding()],
      ...overrides,
    };
  }

  let portfolioService: {
    refreshQuotes: ReturnType<typeof vi.fn>;
    getRecalcStatus: ReturnType<typeof vi.fn>;
    getSummary: ReturnType<typeof vi.fn>;
    getUpcomingExDividends: ReturnType<typeof vi.fn>;
    getNetworthHistory: ReturnType<typeof vi.fn>;
    getDividends: ReturnType<typeof vi.fn>;
    getSymbolNames: ReturnType<typeof vi.fn>;
    cashLedgerChanged$: import('rxjs').Observable<void>;
  };
  let appearance: AppearanceService;

  beforeEach(async () => {
    vi.useFakeTimers();
    const { NEVER } = await import('rxjs');
    portfolioService = {
      refreshQuotes: vi.fn().mockReturnValue(of(null)),
      getRecalcStatus: vi.fn().mockReturnValue(of(null)),
      getSummary: vi.fn().mockReturnValue(of(buildSummary())),
      getUpcomingExDividends: vi.fn().mockReturnValue(of([])),
      getNetworthHistory: vi.fn().mockReturnValue(of([
        { date: '2026-01-01', total_market_value: '100', total_cash_twd: '40', total_assets_twd: '140', total_cost: '80', total_unrealized_pnl: '20', total_dividends: '0', total_realized_pnl: '0', portfolio_xirr: null },
        { date: '2026-05-01', total_market_value: '120', total_cash_twd: '60', total_assets_twd: '180', total_cost: '80', total_unrealized_pnl: '40', total_dividends: '0', total_realized_pnl: '0', portfolio_xirr: null },
      ])),
      getDividends: vi.fn().mockReturnValue(of({ items: [], total: 0 })),
      getSymbolNames: vi.fn().mockReturnValue(of({})),
      cashLedgerChanged$: NEVER,
    };

    await TestBed.configureTestingModule({
      imports: [PortfolioDashboardComponent],
      providers: [
        { provide: PortfolioService, useValue: portfolioService },
        provideRouter([]),
        // Thu 13:00 Taipei: inside the TW session.
        { provide: DASHBOARD_CLOCK, useValue: () => new Date('2026-10-22T05:00:00Z') },
      ],
      deferBlockBehavior: DeferBlockBehavior.Manual,
    })
      .overrideComponent(PortfolioDashboardComponent, {
        set: {
          imports: [
            CommonModule,
            RouterLink,
            ChartStubComponent,
            BtnComponent,
            PageHeaderComponent,
            SegToggleComponent,
            BentoComponent,
            PctBadgeComponent,
            TooltipModule,
            SkeletonModule,
            NativeAmountPipe,
          ],
        },
      })
      .compileComponents();

    appearance = TestBed.inject(AppearanceService);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function footnote(root: HTMLElement, index: number): string {
    return root.querySelectorAll('.kpi-card')[index].querySelector('.meta')?.textContent?.trim() ?? '';
  }

  function createFixture() {
    const fixture = TestBed.createComponent(PortfolioDashboardComponent);
    fixture.detectChanges();
    vi.advanceTimersByTime(2000);
    fixture.detectChanges();
    portfolioService.refreshQuotes.mockClear();
    portfolioService.getRecalcStatus.mockClear();
    portfolioService.getSummary.mockClear();
    portfolioService.getNetworthHistory.mockClear();
    return fixture;
  }

  it('refreshes quotes, polls until completed, then reloads summary', () => {
    portfolioService.refreshQuotes.mockReturnValue(of({
      refresh_scheduled: true,
      date: '2026-05-18',
      touched_symbols: ['2330'],
    }));
    portfolioService.getRecalcStatus.mockReturnValue(of({ state: 'completed' }));
    const fixture = createFixture();

    fixture.nativeElement.querySelector('button').click();
    vi.advanceTimersByTime(1000);

    expect(portfolioService.getRecalcStatus).toHaveBeenCalled();
    expect(portfolioService.getSummary).toHaveBeenCalled();
  });

  it('continues quote polling while the separate import remains partial', () => {
    portfolioService.refreshQuotes.mockReturnValue(of({ refresh_scheduled: true }));
    portfolioService.getRecalcStatus.mockReturnValue(of({ state: 'partial', quote_refresh: { state: 'completed' } }));
    const fixture = createFixture();
    portfolioService.getRecalcStatus.mockReturnValue(of({ state: 'partial', quote_refresh: { state: 'running' } }));
    fixture.nativeElement.querySelector('button').click();
    vi.advanceTimersByTime(1000);
    expect(portfolioService.getSummary).not.toHaveBeenCalled();
    portfolioService.getRecalcStatus.mockReturnValue(of({ state: 'partial', quote_refresh: { state: 'completed' } }));
    vi.advanceTimersByTime(1000);
    expect(portfolioService.getSummary).toHaveBeenCalledTimes(1);
  });

  it('warns and still reloads summary when refresh quotes is already running', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    portfolioService.refreshQuotes.mockReturnValue(throwError(() => ({ status: 409 })));
    const fixture = createFixture();

    fixture.nativeElement.querySelector('button').click();

    expect(warnSpy).toHaveBeenCalledWith('另一筆重算進行中, 稍候再試');
    expect(portfolioService.getSummary).toHaveBeenCalled();
  });

  it('uses 1Y as the default range and updates XIRR when 3M is selected', () => {
    const fixture = createFixture();

    expect(fixture.componentInstance.range()).toBe('1Y');
    expect(fixture.nativeElement.textContent).toContain('12.34%');

    const rangeButtons = Array.from(fixture.nativeElement.querySelectorAll('.range-tabs button')) as HTMLButtonElement[];
    rangeButtons.find(button => button.textContent?.trim() === '3M')!.click();
    fixture.detectChanges();

    expect(fixture.componentInstance.range()).toBe('3M');
    expect(fixture.nativeElement.textContent).toContain('3.21%');
  });

  describe('page header', () => {
    function header(fixture: ReturnType<typeof createFixture>): HTMLElement {
      return (fixture.nativeElement as HTMLElement).querySelector('app-page-header')!;
    }

    it('shows the quote time line from the quote refresh status', () => {
      portfolioService.getRecalcStatus.mockReturnValue(of({
        state: 'idle',
        quote_refresh: { state: 'completed', finished_at: '2026-10-22T05:30:00Z' },
      }));
      const fixture = createFixture();

      expect(header(fixture).querySelector('.page-subtitle')?.textContent?.trim()).toBe('行情更新於 13:30 · 盤中');
    });

    it('shows no quote time line when the status request fails', () => {
      portfolioService.getRecalcStatus.mockReturnValue(throwError(() => ({ status: 500 })));
      const fixture = createFixture();

      expect(header(fixture).querySelector('.page-subtitle')).toBeNull();
      expect(header(fixture).querySelector('h1')).not.toBeNull();
    });

    it('reads the status again after a later summary reload', () => {
      const fixture = createFixture();
      expect(header(fixture).querySelector('.page-subtitle')).toBeNull();

      portfolioService.getRecalcStatus.mockReturnValue(of({
        state: 'idle',
        quote_refresh: { state: 'completed', finished_at: '2026-10-22T09:00:00Z' },
      }));
      fixture.componentInstance.loadSummary();
      fixture.detectChanges();

      expect(header(fixture).querySelector('.page-subtitle')?.textContent?.trim()).toBe('行情更新於 17:00 · 盤中');
    });

    it('puts the market toggle in the header with Chinese labels', () => {
      portfolioService.getSummary.mockReturnValue(of(buildSummary({
        holdings: [buildHolding(), { ...buildHolding(), symbol: 'AAPL', market: 'US', native_currency: 'USD' }],
      })));
      const fixture = createFixture();
      const toggle = header(fixture).querySelector('.market-toggle');

      expect(toggle).not.toBeNull();
      expect(toggle!.querySelector('[role="radiogroup"]')?.getAttribute('aria-label')).toBe('市場');
      expect(Array.from(toggle!.querySelectorAll('button')).map(b => b.textContent?.trim())).toEqual(['全部', '台股', '美股']);
      expect((fixture.nativeElement as HTMLElement).querySelector('.holdings .market-tabs, .holdings app-seg-toggle')).toBeNull();
    });

    it('hides the market toggle when holdings and market totals hold one market', () => {
      portfolioService.getSummary.mockReturnValue(of(buildSummary({
        market_totals: {
          TW: { currency: 'TWD', cost: 900, market_value: 1000, unrealized_pnl: 100, recorded_dividends: 20,
            pending_dividends_net: 0, estimated_pnl_with_dividends: 120, estimated_pnl_percent: 13.33 },
        },
      })));
      const fixture = createFixture();

      expect(header(fixture).querySelector('.market-toggle')).toBeNull();
    });
  });

  describe('總資產 hero', () => {
    function hero(fixture: ReturnType<typeof createFixture>): HTMLElement | null {
      return (fixture.nativeElement as HTMLElement).querySelector('.hero');
    }

    it('shows the total, today and the composition bar before the KPI cards', () => {
      portfolioService.getSummary.mockReturnValue(of(buildSummary({ total_pending_dividends_net: 14600 })));
      const fixture = createFixture();
      const root = fixture.nativeElement as HTMLElement;
      const el = hero(fixture)!;

      expect(el).not.toBeNull();
      expect(el.compareDocumentPosition(root.querySelector('.kpi-card')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(el.querySelector('.hero-label')?.textContent?.trim()).toBe('總資產 · TWD');
      expect(el.querySelector('.hero-total')?.textContent?.trim()).toBe(fixture.componentInstance.formatCurrency(1250));

      const today = el.querySelector('.hero-today')!;
      expect(today.querySelector('.hero-today-label')?.textContent?.trim()).toBe('今日');
      expect(today.querySelector('.hero-today-amount')?.textContent?.trim()).toBe('+$10');
      expect(today.querySelector('.hero-today-amount')?.classList).toContain('up');
      expect(today.querySelector('app-pct-badge')?.textContent?.trim()).toBe('+0.81%');

      const bar = el.querySelector('.composition-bar')!;
      expect(bar.getAttribute('role')).toBe('img');
      expect(bar.getAttribute('aria-label')).toBe('資產組成：股票 80.0%，現金 20.0%');
      expect(Array.from(bar.querySelectorAll<HTMLElement>('span')).map(s => s.style.flexGrow)).toEqual(['80', '20']);

      const legend = Array.from(el.querySelectorAll<HTMLElement>('.legend-item')).map(item => [
        item.querySelector('.legend-label')?.textContent?.trim(),
        item.querySelector('.legend-value')?.textContent?.trim(),
      ]);
      expect(legend).toEqual([['股票市值', '1,000'], ['交割戶現金', '250'], ['待收股利', '14,600']]);
      expect(el.querySelectorAll('.legend-item')[2].querySelector('.swatch.dashed')).not.toBeNull();
    });

    it('shows a falling day in the down colour', () => {
      portfolioService.getSummary.mockReturnValue(of(buildSummary({ total_day_pnl: -10 })));
      const fixture = createFixture();
      const today = hero(fixture)!.querySelector('.hero-today')!;

      expect(today.querySelector('.hero-today-amount')?.textContent?.trim()).toBe('-$10');
      expect(today.querySelector('.hero-today-amount')?.classList).toContain('down');
      expect(today.querySelector('app-pct-badge')?.textContent?.trim()).toBe('-0.79%');
    });

    it('shows a dash for the day percent when the assets before today are not positive', () => {
      portfolioService.getSummary.mockReturnValue(of(buildSummary({ total_day_pnl: 10, total_assets_twd: '10',
        total_market_value: 10, total_cash_twd: '0' })));
      const fixture = createFixture();
      const today = hero(fixture)!.querySelector('.hero-today')!;

      expect(today.querySelector('.hero-today-amount')?.textContent?.trim()).toBe('+$10');
      expect(today.querySelector('app-pct-badge')?.textContent?.trim()).toBe('—');
    });

    it('is hidden while a market filter is active', () => {
      portfolioService.getSummary.mockReturnValue(of(buildSummary({
        holdings: [buildHolding(), { ...buildHolding(), symbol: 'AAPL', market: 'US', native_currency: 'USD' }],
      })));
      const fixture = createFixture();
      expect(hero(fixture)).not.toBeNull();

      fixture.componentInstance.selectMarketFilter('US');
      fixture.detectChanges();

      expect(hero(fixture)).toBeNull();
    });
  });

  it('renders total assets equal to market value when cash is zero', () => {
    portfolioService.getSummary.mockReturnValue(of(buildSummary({
      total_market_value: 500000,
      total_cash_twd: '0',
      total_assets_twd: '500000',
    })));
    const fixture = createFixture();
    const root = fixture.nativeElement as HTMLElement;

    const totalAssetsCard = Array.from(root.querySelectorAll<HTMLElement>('app-bento')).find(card =>
      card.textContent?.includes('總資產'),
    );

    expect(totalAssetsCard?.textContent).toContain(fixture.componentInstance.formatCurrency(500000));
  });

  it('builds 帳戶總值 and 持股淨值 datasets', () => {
    const fixture = createFixture();
    const chartData = fixture.componentInstance.chartData as any;
    const chartOptions = fixture.componentInstance.chartOptions as any;

    expect(chartData.datasets).toHaveLength(2);
    expect(chartData.datasets[0]).toMatchObject({
      label: '帳戶總值',
      data: [140, 180],
      fill: '+1',
    });
    expect(chartData.datasets[1]).toMatchObject({
      label: '持股淨值',
      data: [100, 120],
      fill: true,
    });
    expect(chartOptions.scales.y.stacked).toBe(false);
  });

  it('collapses total assets onto total market value when backfill has not populated cash', () => {
    portfolioService.getNetworthHistory.mockReturnValue(of([
      { date: '2026-01-01', total_market_value: '100', total_cash_twd: '0', total_assets_twd: '100', total_cost: '80', total_unrealized_pnl: '20', total_dividends: '0', total_realized_pnl: '0', portfolio_xirr: null },
      { date: '2026-05-01', total_market_value: '120', total_cash_twd: '0', total_assets_twd: '120', total_cost: '80', total_unrealized_pnl: '40', total_dividends: '0', total_realized_pnl: '0', portfolio_xirr: null },
    ]));
    const fixture = createFixture();
    const chartData = fixture.componentInstance.chartData as any;

    expect(chartData.datasets).toHaveLength(2);
    expect(chartData.datasets[0].data).toEqual([100, 120]);
    expect(chartData.datasets[1].data).toEqual([100, 120]);
  });

  it('preserves the two-line chart layout when switching ranges', () => {
    const fixture = createFixture();

    const rangeButtons = Array.from(fixture.nativeElement.querySelectorAll('.range-tabs button')) as HTMLButtonElement[];
    rangeButtons.find(button => button.textContent?.trim() === '3M')!.click();
    fixture.detectChanges();

    expect((fixture.componentInstance.chartData as any).datasets).toHaveLength(2);
    expect((fixture.componentInstance.chartOptions as any).scales.y.stacked).toBe(false);
  });

  it('sets chart animation false and updates the chart on convention changes', async () => {
    const fixture = createFixture();
    const [deferBlock] = await fixture.getDeferBlocks();
    await deferBlock.render(DeferBlockState.Complete);
    fixture.detectChanges();
    const chart = fixture.debugElement.query(By.directive(ChartStubComponent)).componentInstance as ChartStubComponent;

    expect((fixture.componentInstance.chartOptions as any).animation).toBe(false);

    appearance.setGainLoss('western');
    fixture.detectChanges();

    expect(chart.chart.update).toHaveBeenCalledWith('none');
  });
  it('renders estimated main/holding amounts and breakdown from the summary response', () => {
    const holding = { ...buildHolding(), pending_dividends_net: 3090,
      estimated_pnl_with_dividends: 4690, estimated_pnl_percent: 93.8 };
    portfolioService.getSummary.mockReturnValue(of(buildSummary({
      holdings: [holding], estimated_pnl_with_dividends: 4690,
      estimated_pnl_percent: 93.8, total_unrealized_pnl: 1500, total_dividends: 100,
      total_pending_dividends_net: 3090,
    })));
    const fixture = createFixture();
    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelector('.estimated-total')?.textContent).toContain('4,690');
    expect(root.querySelector('.stock-pnl')?.textContent).toContain('4,690');
    expect(root.querySelector('.stock-pnl')?.textContent).toContain('93.80%');
    expect(root.querySelector('details')).toBeNull();
    const f = fixture.componentInstance;
    expect(footnote(root, 1)).toBe(`價差 ${f.formatNative(1500, 'TWD')} · 股利 ${f.formatNative(100, 'TWD')} · 待收 ${f.formatNative(3090, 'TWD')}`);
    (root.querySelector('.stock-row') as HTMLElement).click();
    fixture.detectChanges();
    expect(root.querySelector('.detail-panel')?.textContent).toContain('待收股利');
    expect(root.querySelector('.detail-panel')?.textContent).toContain('3,090');
    expect(root.textContent).toContain('已記錄股利');
    expect(root.textContent).not.toContain('已入帳現金');
  });

  it('keeps closed TW receivables in TW filter and out of same-symbol US totals', () => {
    const us = { ...buildHolding(), market: 'US' as const, native_currency: 'USD',
      estimated_pnl_with_dividends_native: 25, estimated_pnl_percent_native: 5 };
    portfolioService.getSummary.mockReturnValue(of(buildSummary({ holdings: [us], market_totals: {
      TW: {currency: 'TWD', cost: 0, market_value: 0, unrealized_pnl: 0, recorded_dividends: 100,
        pending_dividends_net: 3090, estimated_pnl_with_dividends: 3190, estimated_pnl_percent: null},
      US: {currency: 'USD', cost: 500, market_value: 520, unrealized_pnl: 20, recorded_dividends: 5,
        pending_dividends_net: 0, estimated_pnl_with_dividends: 25, estimated_pnl_percent: 5},
    }})));
    const fixture = createFixture();
    const root = fixture.nativeElement as HTMLElement;
    for (const [market, amount] of [['TW', '3,190'], ['US', '25.00 USD']]) {
      const label = market === 'TW' ? '台股' : '美股';
      const button = Array.from(root.querySelectorAll<HTMLButtonElement>('app-page-header .market-toggle button'))
        .find(b => b.textContent?.trim() === label)!;
      button.click(); fixture.detectChanges();
      expect(root.querySelector('.estimated-total')?.textContent).toContain(amount);
      expect(root.querySelectorAll('.stock-row')).toHaveLength(market === 'TW' ? 0 : 1);
    }
    expect(footnote(root, 1)).not.toContain('3,090');
    expect(footnote(root, 1)).toContain('待收 ' + fixture.componentInstance.formatNative(0, 'USD'));
  });

  it('shows unavailable estimated valuation/ratio instead of zero or pending-only profit', () => {
    portfolioService.getSummary.mockReturnValue(of(buildSummary({
      estimated_pnl_with_dividends: null, estimated_pnl_percent: null,
      total_pending_dividends_net: 3090,
      holdings: [{...buildHolding(), estimated_pnl_with_dividends: null, estimated_pnl_percent: null}],
    })));
    const fixture = createFixture();
    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelector('.estimated-total')?.textContent?.trim()).toBe('—');
    expect(root.querySelector('.stock-pnl')?.textContent).not.toContain('30.00%');
    expect(footnote(root, 1)).toContain('待收 ' + fixture.componentInstance.formatNative(3090, 'TWD'));
  });

  it('shows a dash when pending dividends are unavailable', () => {
    portfolioService.getSummary.mockReturnValue(of(buildSummary({
      total_pending_dividends_net: undefined,
    })));
    const fixture = createFixture();
    const root = fixture.nativeElement as HTMLElement;
    expect(footnote(root, 1)).toMatch(/待收 —$/);
  });

  it.each([
    [false, 1500], [true, 1500], [false, 0], [true, 0], [false, -1500], [true, -1500],
  ])('preserves price-only values with null estimates (grouped=%s, price=%s)', (grouped, price) => {
    const tw = { ...buildHolding(), unrealized_pnl: Number(price),
      estimated_pnl_with_dividends: null, estimated_pnl_percent: null };
    const holdings = grouped
      ? [tw, { ...buildHolding(), market: 'US' as const, native_currency: 'USD' }]
      : [tw];
    portfolioService.getSummary.mockReturnValue(of(buildSummary({
      total_unrealized_pnl: Number(price), estimated_pnl_with_dividends: null,
      estimated_pnl_percent: null, holdings,
    })));
    const fixture = createFixture();
    const root = fixture.nativeElement as HTMLElement;
    const formatted = fixture.componentInstance.formatNative(Number(price), 'TWD');
    expect(footnote(root, 1)).toContain('價差 ' + formatted);
    expect(root.querySelector('.estimated-total')?.textContent?.trim()).toBe('—');
    expect(root.querySelector('[aria-label="預估含息損益率無法計算"]')).not.toBeNull();
    (root.querySelector('.stock-row') as HTMLElement).click();
    fixture.detectChanges();
    const priceDetail = Array.from(root.querySelectorAll('.detail-panel .d'))
      .find(element => element.querySelector('small')?.textContent === '未實現價差');
    const estimateDetail = Array.from(root.querySelectorAll('.detail-panel .d'))
      .find(element => element.querySelector('small')?.textContent === '預估含息損益');
    expect(priceDetail?.querySelector('div')?.textContent?.trim()).toBe(formatted);
    expect(estimateDetail?.querySelector('div')?.textContent?.trim()).toBe('—');
    expect(root.querySelector('.stock-pnl')?.textContent).not.toContain('30.00%');
  });

  describe('KPI row', () => {
    const kpi = (root: HTMLElement) => Array.from(root.querySelectorAll<HTMLElement>('.kpi-card'));
    const label = (card: HTMLElement) => card.querySelector('.label')?.textContent?.trim();

    it('renders the four KPI labels in order with spec footnotes', () => {
      const fixture = createFixture();
      const root = fixture.nativeElement as HTMLElement;
      const f = fixture.componentInstance;
      expect(kpi(root).map(label)).toEqual(['總市值', '預估含息損益', '年化報酬率 XIRR', '已記錄股利']);
      expect(footnote(root, 0)).toBe(`總成本 ${f.formatCurrency(900)}`);
      expect(footnote(root, 1)).toBe(`價差 ${f.formatNative(100, 'TWD')} · 股利 ${f.formatNative(20, 'TWD')} · 待收 —`);
      expect(footnote(root, 2)).toBe('1Y · 全市場');
      expect(footnote(root, 3)).toBe('含舊資料 · 不代表已核實收款');
    });

    it('marks the estimated card as an estimate and shows a pct badge', () => {
      portfolioService.getSummary.mockReturnValue(of(buildSummary({
        estimated_pnl_with_dividends: 125, estimated_pnl_percent: 12.5, total_pending_dividends_net: 5,
      })));
      const fixture = createFixture();
      const root = fixture.nativeElement as HTMLElement;
      const f = fixture.componentInstance;
      const card = kpi(root)[1];
      expect(card.querySelector('[title="估算・非可用現金"]')).not.toBeNull();
      expect(card.querySelector('.value')?.classList.contains('up')).toBe(true);
      expect(card.querySelector('app-pct-badge')).not.toBeNull();
      expect(footnote(root, 1)).toBe(`價差 ${f.formatNative(100, 'TWD')} · 股利 ${f.formatNative(20, 'TWD')} · 待收 ${f.formatNative(5, 'TWD')}`);
      expect(kpi(root)[3].querySelector('.value')?.classList.contains('dividend')).toBe(true);
    });

    it('keeps XIRR portfolio-wide and updates its range footnote', () => {
      const fixture = createFixture();
      const root = fixture.nativeElement as HTMLElement;
      expect(footnote(root, 2)).toBe('1Y · 全市場');
      fixture.componentInstance.selectRange('3M');
      fixture.detectChanges();
      expect(footnote(root, 2)).toBe('3M · 全市場');
    });

    it('suffixes market on scoped cards but not XIRR, which is marked 全市場', () => {
      const us = { ...buildHolding(), market: 'US' as const, native_currency: 'USD' };
      portfolioService.getSummary.mockReturnValue(of(buildSummary({ holdings: [us], market_totals: {
        US: {currency: 'USD', cost: 500, market_value: 520, unrealized_pnl: 20, recorded_dividends: 5,
          pending_dividends_net: 2, estimated_pnl_with_dividends: 27, estimated_pnl_percent: 5},
      }})));
      const fixture = createFixture();
      const root = fixture.nativeElement as HTMLElement;
      const f = fixture.componentInstance;
      f.selectMarketFilter('US');
      fixture.detectChanges();
      expect(kpi(root).map(label)).toEqual(['總市值 · US', '預估含息損益 · US', '年化報酬率 XIRR', '已記錄股利 · US']);
      expect(footnote(root, 0)).toBe(`總成本 ${f.formatNative(500, 'USD')}`);
      expect(footnote(root, 1)).toBe(`價差 ${f.formatNative(20, 'USD')} · 股利 ${f.formatNative(5, 'USD')} · 待收 ${f.formatNative(2, 'USD')}`);
      expect(footnote(root, 2)).toBe('1Y · 全市場');
      f.selectRange('3M');
      fixture.detectChanges();
      expect(footnote(root, 2)).toBe('3M · 全市場');
      expect(root.querySelector('.chart-head .card-title')?.textContent?.trim()).toBe('淨值走勢 · 全市場');
    });
  });

  describe('net-worth chart', () => {
    function point(date: string, market: string, cash: string, total: string) {
      return { date, total_market_value: market, total_cash_twd: cash, total_assets_twd: total, total_cost: '0',
        total_unrealized_pnl: '0', total_dividends: '0', total_realized_pnl: '0', portfolio_xirr: null };
    }

    function chartCard(root: HTMLElement): HTMLElement {
      return root.querySelector('.chart-head')!.closest('app-bento') as HTMLElement;
    }

    function legend(root: HTMLElement): string[][] {
      return Array.from(chartCard(root).querySelectorAll<HTMLElement>('.chart-legend-item')).map(item => [
        item.querySelector('.chart-legend-label')?.textContent?.trim() ?? '',
        item.querySelector('.chart-legend-value')?.textContent?.trim() ?? '',
      ]);
    }

    beforeEach(() => {
      portfolioService.getNetworthHistory.mockReturnValue(of([
        point('2026-09-01', '800', '200', '1000'),
        point('2026-10-22', '1000', '250', '1250'),
      ]));
    });

    afterEach(() => {
      TestBed.inject(LayoutModeService).set(null);
      document.documentElement.style.removeProperty('--app-accent');
      document.documentElement.style.removeProperty('--app-accent-soft');
    });

    it('colours 帳戶總值 with the accent line and soft band down to 持股淨值', () => {
      document.documentElement.style.setProperty('--app-accent', ' #112233');
      document.documentElement.style.setProperty('--app-accent-soft', ' #445566');
      const fixture = createFixture();
      const [total, holdings] = (fixture.componentInstance.chartData as any).datasets;

      expect(total).toMatchObject({ label: '帳戶總值', borderColor: '#112233', backgroundColor: '#445566', fill: '+1', data: [1000, 1250] });
      expect(holdings).toMatchObject({ label: '持股淨值', data: [800, 1000] });
    });

    it('shows the three legend entries with the latest values and swatches', () => {
      const fixture = createFixture();
      const card = chartCard(fixture.nativeElement);

      expect(legend(fixture.nativeElement)).toEqual([
        ['帳戶總值', '$1,250'],
        ['持股淨值', '$1,000'],
        ['兩線差距 = 交割戶現金', '$250'],
      ]);
      const items = card.querySelectorAll('.chart-legend-item');
      expect(items[0].querySelector('.chart-swatch.total')).not.toBeNull();
      expect(items[1].querySelector('.chart-swatch.holdings')).not.toBeNull();
      expect(items[2].querySelector('.chart-swatch.band')).not.toBeNull();
      expect(card.querySelector('.chart-legend')?.classList).not.toContain('two-col');
      expect(card.querySelector('.chart-caption')).toBeNull();
    });

    it('formats the tooltip date, series values and cash for the hovered point', () => {
      const fixture = createFixture();
      const datasets = (fixture.componentInstance.chartData as any).datasets;
      const callbacks = (fixture.componentInstance.chartOptions as any).plugins.tooltip.callbacks;
      const items = [
        { dataIndex: 1, datasetIndex: 0, dataset: datasets[0], parsed: { y: 1250 } },
        { dataIndex: 1, datasetIndex: 1, dataset: datasets[1], parsed: { y: 1000 } },
      ];

      expect(callbacks.title(items)).toBe('2026/10/22');
      expect(callbacks.label(items[0])).toBe('帳戶總值 $1,250');
      expect(callbacks.label(items[1])).toBe('持股淨值 $1,000');
      expect(callbacks.afterBody(items)).toBe('現金 $250');
      expect(callbacks.afterBody([{ ...items[0], dataIndex: 0 }])).toBe('現金 $200');
      expect((fixture.componentInstance.chartOptions as any).interaction.mode).toBe('index');
    });

    it('passes the hover guide plugin to p-chart', async () => {
      const fixture = createFixture();
      const [deferBlock] = await fixture.getDeferBlocks();
      await deferBlock.render(DeferBlockState.Complete);
      fixture.detectChanges();
      const chart = fixture.debugElement.query(By.directive(ChartStubComponent)).componentInstance as ChartStubComponent;

      expect(chart.plugins).toContain(hoverGuidePlugin);
    });

    it('shows zero cash and equal series when the latest cash is zero', () => {
      portfolioService.getNetworthHistory.mockReturnValue(of([
        point('2026-09-01', '800', '200', '1000'),
        point('2026-10-22', '1000', '0', '1000'),
      ]));
      const fixture = createFixture();
      const [total, holdings] = (fixture.componentInstance.chartData as any).datasets;

      expect(legend(fixture.nativeElement)[2]).toEqual(['兩線差距 = 交割戶現金', '$0']);
      expect(total.data[1]).toBe(holdings.data[1]);
    });

    it('falls back to market + cash when the latest total is zero', () => {
      portfolioService.getNetworthHistory.mockReturnValue(of([
        point('2026-10-22', '1000', '250', '0'),
      ]));
      const fixture = createFixture();

      expect(legend(fixture.nativeElement)).toEqual([
        ['帳戶總值', '$1,250'],
        ['持股淨值', '$1,000'],
        ['兩線差距 = 交割戶現金', '$250'],
      ]);
      expect((fixture.componentInstance.chartOptions as any).plugins.tooltip.callbacks.afterBody([{ dataIndex: 0 }])).toBe('現金 $250');
    });

    it('labels the period badge 帳戶總值 beside the title', () => {
      const fixture = createFixture();
      const head = (fixture.nativeElement as HTMLElement).querySelector('.chart-head-left')!;
      const ret = head.querySelector('.chart-return')!;

      expect(head.querySelector('.card-title')?.textContent?.trim()).toBe('淨值走勢');
      expect(ret.querySelector('.chart-return-label')?.textContent?.trim()).toBe('帳戶總值');
      expect(ret.querySelector('app-pct-badge')?.textContent?.trim()).toBe('+25.00%');
    });

    it('titles the chart 全市場 while a market filter is active', () => {
      portfolioService.getSummary.mockReturnValue(of(buildSummary({
        holdings: [buildHolding(), { ...buildHolding(), symbol: 'AAPL', market: 'US', native_currency: 'USD' }],
      })));
      const fixture = createFixture();
      fixture.componentInstance.selectMarketFilter('US');
      fixture.detectChanges();

      expect((fixture.nativeElement as HTMLElement).querySelector('.chart-head .card-title')?.textContent?.trim()).toBe('淨值走勢 · 全市場');
    });

    describe('keyboard focus', () => {
      async function renderChart() {
        const fixture = createFixture();
        const [deferBlock] = await fixture.getDeferBlocks();
        await deferBlock.render(DeferBlockState.Complete);
        fixture.detectChanges();
        const chart = (fixture.debugElement.query(By.directive(ChartStubComponent)).componentInstance as ChartStubComponent).chart;
        const wrapper = (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('.networth-chart')!;
        return { fixture, chart, wrapper };
      }

      const both = (index: number) => [{ datasetIndex: 0, index }, { datasetIndex: 1, index }];

      it('makes the chart wrapper a focusable, labelled image', async () => {
        const { wrapper } = await renderChart();

        expect(wrapper.getAttribute('tabindex')).toBe('0');
        expect(wrapper.getAttribute('role')).toBe('img');
        expect(wrapper.getAttribute('aria-label')).toBe('淨值走勢圖，使用左右鍵檢視各日數值');
      });

      it('shows the last point on focus, moves with the arrow keys and clears on blur', async () => {
        const { chart, wrapper } = await renderChart();

        wrapper.dispatchEvent(new FocusEvent('focus'));
        expect(chart.setActiveElements).toHaveBeenLastCalledWith(both(1));
        expect(chart.tooltip.setActiveElements).toHaveBeenLastCalledWith(both(1), { x: 30, y: 40 });
        expect(chart.update).toHaveBeenLastCalledWith('none');

        wrapper.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft' }));
        expect(chart.setActiveElements).toHaveBeenLastCalledWith(both(0));
        expect(chart.tooltip.setActiveElements).toHaveBeenLastCalledWith(both(0), { x: 10, y: 20 });

        wrapper.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft' }));
        expect(chart.setActiveElements).toHaveBeenLastCalledWith(both(0));

        wrapper.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }));
        wrapper.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }));
        expect(chart.setActiveElements).toHaveBeenLastCalledWith(both(1));

        chart.update.mockClear();
        wrapper.dispatchEvent(new FocusEvent('blur'));
        expect(chart.setActiveElements).toHaveBeenLastCalledWith([]);
        expect(chart.tooltip.setActiveElements).toHaveBeenLastCalledWith([], { x: 0, y: 0 });
        expect(chart.update).toHaveBeenCalledWith('none');
      });

      it('does nothing before the chart has rendered', () => {
        const fixture = createFixture();
        const wrapper = (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('.networth-chart')!;

        expect(() => {
          wrapper.dispatchEvent(new FocusEvent('focus'));
          wrapper.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft' }));
          wrapper.dispatchEvent(new FocusEvent('blur'));
        }).not.toThrow();
      });
    });

    it('uses a two-column legend and a cash caption on a phone', () => {
      TestBed.inject(LayoutModeService).set('phone');
      const fixture = createFixture();
      const card = chartCard(fixture.nativeElement);

      expect(card.querySelector('.chart-legend')?.classList).toContain('two-col');
      expect(legend(fixture.nativeElement)).toEqual([
        ['帳戶總值', '$1,250'],
        ['持股淨值', '$1,000'],
      ]);
      expect(card.querySelector('.chart-caption')?.textContent?.trim()).toBe('兩線之間是交割戶現金 $250');
    });
  });

  describe('持股配置 card', () => {
    function pendingDividend(id: number, overrides: Partial<Dividend> = {}): Dividend {
      return {
        id,
        symbol: '00878',
        amount: 9600,
        ex_dividend_date: '2026-10-17',
        payment_date: '2026-11-18',
        receipt_status: 'pending',
        cash_dividend_per_share: 0.48,
        quantity_at_record_date: 20000,
        ...overrides,
      };
    }

    function card(root: HTMLElement): HTMLElement | null {
      return root.querySelector<HTMLElement>('.allocation-card');
    }

    function text(el: Element | null | undefined): string {
      return el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
    }

    function allocationRows(root: HTMLElement): string[] {
      return Array.from(card(root)!.querySelectorAll('.allocation-row')).map(
        row => `${text(row.querySelector('.allocation-name'))} ${text(row.querySelector('.allocation-pct'))}`,
      );
    }

    function pendingBlock(root: HTMLElement): HTMLElement | null {
      return card(root)?.querySelector<HTMLElement>('.pending-block') ?? null;
    }

    beforeEach(() => {
      portfolioService.getSummary.mockReturnValue(of(buildSummary({
        holdings: [
          { ...buildHolding(), symbol: '0050', name: '元大台灣50', market_value: 3500 },
          buildHolding(),
        ],
      })));
    });

    it('sits beside the chart with a bar segment and a row per slice', () => {
      const fixture = createFixture();
      const root = fixture.nativeElement as HTMLElement;
      const row = root.querySelector('.chart-row')!;

      expect(row.querySelector('.chart-head')).not.toBeNull();
      expect(row.contains(card(root))).toBe(true);
      expect(text(card(root)!.querySelector('h2'))).toBe('持股配置');
      expect(card(root)!.querySelectorAll('.allocation-bar > span').length).toBe(2);
      expect(allocationRows(root)).toEqual([
        '台積電 2330 65.0%',
        '元大台灣50 0050 35.0%',
      ]);
      const [first] = Array.from(card(root)!.querySelectorAll<HTMLElement>('.allocation-bar > span'));
      expect(first.style.background).toBe('var(--app-primary)');
    });

    it('lists at most three pending dividends with names from the symbol map and a link to all', () => {
      portfolioService.getSymbolNames.mockReturnValue(of({ '00878': '國泰永續高股息', '2330': '台積電' }));
      portfolioService.getDividends.mockReturnValue(of({
        total: 5,
        items: [
          pendingDividend(1, { payment_date: '2027-01-08', symbol: '2330', amount: 5000, cash_dividend_per_share: 5, quantity_at_record_date: 1000 }),
          pendingDividend(2),
          pendingDividend(3, { payment_date: '2027-02-01', symbol: '9999' }),
          pendingDividend(4, { payment_date: '2027-03-01' }),
          pendingDividend(5, { receipt_status: 'confirmed', payment_date: '2026-10-01' }),
        ],
      }));
      const fixture = createFixture();
      const block = pendingBlock(fixture.nativeElement)!;
      const rows = Array.from(block.querySelectorAll('.pending-row'));

      expect(text(block.querySelector('h3'))).toBe('待收股利');
      const link = block.querySelector<HTMLAnchorElement>('a')!;
      expect(text(link)).toBe('全部');
      expect(link.getAttribute('href')).toBe('/portfolio/dividends');
      expect(rows.length).toBe(3);
      expect(rows.map(r => [text(r.querySelector('.pending-tile span')), text(r.querySelector('.pending-tile b'))])).toEqual([
        ['11月', '18'],
        ['1月', '08'],
        ['2月', '01'],
      ]);
      expect(rows.map(r => text(r.querySelector('.pending-name')))).toEqual(['國泰永續高股息', '台積電', '9999']);
      expect(text(rows[1].querySelector('.pending-note'))).toBe('每股 5 · 1,000 股');
      expect(rows[0].querySelector('.pending-amount')!.classList).toContain('dividend');
      expect(text(rows[0].querySelector('.pending-amount'))).toBe('+9,600');
    });

    it('shows a foreign-currency pending amount with its currency', () => {
      portfolioService.getDividends.mockReturnValue(of({
        total: 2,
        items: [
          pendingDividend(1),
          pendingDividend(2, { symbol: 'AAPL', amount: 12.35, currency: 'USD', payment_date: '2026-11-20' }),
        ],
      }));
      const fixture = createFixture();
      const amounts = Array.from(pendingBlock(fixture.nativeElement)!.querySelectorAll('.pending-amount')).map(text);

      expect(amounts).toEqual(['+9,600', '+12.35 USD']);
    });

    it('requests every page, newest ex date first, and lists a row found only on page 2', () => {
      portfolioService.getDividends.mockImplementation(({ offset }: { offset: number }) => of({
        total: 150,
        items: offset === 0
          ? Array.from({ length: 100 }, (_, i) => pendingDividend(i + 1, { receipt_status: 'confirmed' }))
          : [pendingDividend(101, { symbol: '2330' })],
      }));
      const fixture = createFixture();

      expect(portfolioService.getDividends.mock.calls[0][0]).toEqual({ limit: 100, offset: 0, sort: 'ex_dividend_date:desc' });
      expect(portfolioService.getDividends.mock.calls[1][0]).toEqual({ limit: 100, offset: 100, sort: 'ex_dividend_date:desc' });
      const rows = pendingBlock(fixture.nativeElement)!.querySelectorAll('.pending-row');
      expect(rows.length).toBe(1);
      expect(text(rows[0].querySelector('.pending-name'))).toBe('2330');
    });

    it('omits the 待收股利 block but keeps the card when a later dividend page fails', () => {
      portfolioService.getDividends.mockImplementation(({ offset }: { offset: number }) => offset === 0
        ? of({ total: 150, items: [pendingDividend(1)] })
        : throwError(() => new Error('boom')));
      const fixture = createFixture();

      expect(card(fixture.nativeElement)).not.toBeNull();
      expect(card(fixture.nativeElement)!.querySelectorAll('.allocation-row').length).toBe(2);
      expect(pendingBlock(fixture.nativeElement)).toBeNull();
    });

    it('omits the 待收股利 block when the symbol names fail', () => {
      portfolioService.getDividends.mockReturnValue(of({ total: 1, items: [pendingDividend(1)] }));
      portfolioService.getSymbolNames.mockReturnValue(throwError(() => new Error('boom')));
      const fixture = createFixture();

      expect(card(fixture.nativeElement)).not.toBeNull();
      expect(pendingBlock(fixture.nativeElement)).toBeNull();
    });

    it('allocates over the filtered holdings', () => {
      portfolioService.getSummary.mockReturnValue(of(buildSummary({
        holdings: [
          buildHolding(),
          { ...buildHolding(), symbol: 'AAPL', name: 'Apple', market: 'US', native_currency: 'USD', market_value: 3000 },
          { ...buildHolding(), symbol: 'MSFT', name: 'Microsoft', market: 'US', native_currency: 'USD', market_value: 1000 },
        ],
      })));
      const fixture = createFixture();
      fixture.componentInstance.selectMarketFilter('US');
      fixture.detectChanges();

      expect(allocationRows(fixture.nativeElement)).toEqual([
        'Apple AAPL 75.0%',
        'Microsoft MSFT 25.0%',
      ]);
    });
  });

  describe('持股明細 list', () => {
    function text(el: Element | null | undefined): string {
      return el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
    }

    function twHolding(symbol: string, name: string, market_value: number): StockHolding {
      return { ...buildHolding(), symbol, name, market_value };
    }

    function usHolding(symbol: string, name: string, market_value: number): StockHolding {
      return {
        ...buildHolding(), symbol, name, market: 'US', market_value, native_close: 190.5, native_currency: 'USD',
        live_fx_rate_to_twd: 31.45,
      };
    }

    function render(holdings: StockHolding[]) {
      portfolioService.getSummary.mockReturnValue(of(buildSummary({ holdings })));
      const fixture = createFixture();
      return { fixture, root: fixture.nativeElement as HTMLElement };
    }

    const four = [
      twHolding('2330', '台積電', 1_000_000),
      twHolding('0050', '元大台灣50', 1_500_000),
      twHolding('00878', '國泰永續高股息', 100_000),
      twHolding('2317', '鴻海', 12_900),
    ];

    it('shows 持股明細, the count line and the column header row', () => {
      const { root } = render(four);
      const block = root.querySelector('.holdings-block')!;
      expect(text(block.querySelector('.card-title'))).toBe('持股明細');
      expect(text(block.querySelector('.holdings-sub'))).toBe('4 檔 · 依市值排序');
      const cols = Array.from(block.querySelectorAll('.holdings-head > span')).map(text);
      expect(cols).toEqual(['名稱', '股數', '現價', '今日', '市值 (TWD)', '預估含息損益']);
    });

    it('orders rows by market value descending', () => {
      const { root } = render(four);
      const symbols = Array.from(root.querySelectorAll('.stock-row .n')).map(text);
      expect(symbols).toEqual(['0050', '2330', '00878', '2317']);
    });

    it('sorts within each market group and shows the group header with count and subtotal', () => {
      const { root } = render([
        twHolding('2330', '台積電', 1_000_000),
        twHolding('0050', '元大台灣50', 1_500_000),
        usHolding('MSFT', 'Microsoft', 100_000),
        usHolding('AAPL', 'Apple', 12_900),
      ]);
      const headers = Array.from(root.querySelectorAll('.market-group-header')).map(text);
      expect(headers).toEqual(['台股 · 2 檔 · $2,500,000', '美股 · 2 檔 · $112,900']);
      const groups = Array.from(root.querySelectorAll('.market-group')).map(
        g => Array.from(g.querySelectorAll('.stock-row .n')).map(text),
      );
      expect(groups).toEqual([['0050', '2330'], ['MSFT', 'AAPL']]);
    });

    it('keeps TW row data and the US native branch unchanged', () => {
      const { fixture, root } = render([twHolding('2330', '台積電', 1_000_000), usHolding('AAPL', 'Apple', 12_900)]);
      const rows = Array.from(root.querySelectorAll<HTMLElement>('.stock-row'));
      const tw = rows.find(r => text(r.querySelector('.n')) === '2330')!;
      const us = rows.find(r => text(r.querySelector('.n')) === 'AAPL')!;
      expect(tw.getAttribute('role')).toBe('button');
      expect(text(tw)).toContain('650.00');
      expect(text(tw)).toContain('+5.00 (+0.77%)');
      expect(text(tw.querySelector('.stock-qty'))).toBe('10');
      expect(text(us)).toContain('190.50 USD');
      expect(text(us.querySelector('.market-badge'))).toBe('US');
      tw.click();
      us.click();
      fixture.detectChanges();
      expect(text(us.querySelector('.detail-panel'))).toContain('換算後市值');
      expect(tw.querySelector('.detail-panel')).toBeNull();
    });

    it('toggles the detail panel on keyboard Enter', () => {
      const { fixture, root } = render(four);
      const row = root.querySelector<HTMLElement>('.stock-row')!;
      row.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      fixture.detectChanges();
      expect(row.getAttribute('aria-expanded')).toBe('true');
      expect(row.querySelector('.detail-panel')).not.toBeNull();
    });

    it('colours the abbr square from the allocation series and mutes it past the fifth', () => {
      const six = [
        twHolding('2330', '台積電', 600), twHolding('0050', '元大台灣50', 500), twHolding('00878', '國泰', 400),
        twHolding('2317', '鴻海', 300), twHolding('2412', '中華電', 200), twHolding('1101', '台泥', 100),
      ];
      const { root } = render(six);
      const abbrs = Array.from(root.querySelectorAll<HTMLElement>('.stock-abbr'));
      expect(abbrs.map(text)).toEqual(['30', '50', '78', '17', '12', '01']);
      expect(abbrs.map(a => a.style.background)).toEqual([
        'var(--app-primary)', 'var(--app-accent)', 'var(--app-dividend)',
        'var(--app-success)', 'var(--app-warning)', 'var(--app-text-muted)',
      ]);
    });

    it('uses the first character of the name for non-numeric symbols', () => {
      const { root } = render([usHolding('AAPL', 'Apple', 100)]);
      expect(text(root.querySelector('.stock-abbr'))).toBe('A');
    });
  });

});
