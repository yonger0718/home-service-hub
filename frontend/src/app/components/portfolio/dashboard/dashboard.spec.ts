import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { DeferBlockBehavior, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { of, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PortfolioSummary } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';
import { AppearanceService } from '../../../services/appearance.service';
import { PortfolioDashboardComponent } from './dashboard';
import { BtnComponent } from '../../ui/btn/btn';
import { SegToggleComponent } from '../../ui/seg-toggle/seg-toggle';
import { BentoComponent } from '../../ui/bento/bento';
import { PctBadgeComponent } from '../../ui/pct-badge/pct-badge';
import { TooltipModule } from 'primeng/tooltip';
import { SkeletonModule } from 'primeng/skeleton';

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
  function buildHolding() {
    return {
      symbol: '2330',
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
      getRecalcStatus: vi.fn(),
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
      providers: [{ provide: PortfolioService, useValue: portfolioService }],
      deferBlockBehavior: DeferBlockBehavior.Manual,
    })
      .overrideComponent(PortfolioDashboardComponent, {
        set: {
          imports: [
            CommonModule,
            ChartStubComponent,
            BtnComponent,
            SegToggleComponent,
            BentoComponent,
            PctBadgeComponent,
            TooltipModule,
            SkeletonModule,
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

  it('renders the total assets tile above the market value tile', () => {
    portfolioService.getSummary.mockReturnValue(of(buildSummary({
      total_market_value: 500000,
      total_cash_twd: '100000',
      total_assets_twd: '600000',
    })));
    const fixture = createFixture();
    const root = fixture.nativeElement as HTMLElement;

    const labels = Array.from(root.querySelectorAll<HTMLElement>('.label')).map(element =>
      element.textContent?.trim(),
    );
    const totalAssetsIndex = labels.indexOf('總資產');
    const marketValueIndex = labels.indexOf('總市值');

    expect(totalAssetsIndex).toBeGreaterThanOrEqual(0);
    expect(marketValueIndex).toBeGreaterThanOrEqual(0);
    expect(totalAssetsIndex).toBeLessThan(marketValueIndex);
    expect(fixture.nativeElement.textContent).toContain(fixture.componentInstance.formatCurrency(600000));
    expect(fixture.nativeElement.textContent).toContain(fixture.componentInstance.formatCurrency(500000));
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
      fill: true,
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
});
