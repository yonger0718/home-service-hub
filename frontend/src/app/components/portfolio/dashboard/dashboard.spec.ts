import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { DeferBlockBehavior, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter, RouterLink } from '@angular/router';
import { of, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PortfolioSummary, StockHolding } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';
import { AppearanceService } from '../../../services/appearance.service';
import { DASHBOARD_CLOCK, PortfolioDashboardComponent } from './dashboard';
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
  chart = { update: vi.fn() };
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

  it('builds total assets and total market value datasets', () => {
    const fixture = createFixture();
    const chartData = fixture.componentInstance.chartData as any;
    const chartOptions = fixture.componentInstance.chartOptions as any;

    expect(chartData.datasets).toHaveLength(2);
    expect(chartData.datasets[0]).toMatchObject({
      label: '總資產',
      data: [140, 180],
      fill: '+1',
    });
    expect(chartData.datasets[1]).toMatchObject({
      label: '總市值',
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
    await deferBlock.render(2 /* DeferBlockState.Complete */);
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
    expect(root.querySelector('.stock-stats')?.textContent).toContain('4,690');
    expect(root.querySelector('.stock-stats')?.textContent).toContain('93.80%');
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
    expect(root.querySelector('.stock-stats')?.textContent).not.toContain('30.00%');
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
    expect(root.querySelector('.stock-stats')?.textContent).not.toContain('30.00%');
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
      expect(footnote(root, 2)).toBe('1Y');
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
      expect(footnote(root, 2)).toBe('1Y');
      fixture.componentInstance.selectRange('3M');
      fixture.detectChanges();
      expect(footnote(root, 2)).toBe('3M');
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

});
