import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PortfolioSummary } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';
import { NetworthChartComponent } from '../networth-chart/networth-chart';
import { PortfolioDashboardComponent } from './dashboard';

describe('PortfolioDashboardComponent', () => {
  const summary: PortfolioSummary = {
    total_market_value: 1000,
    total_cost: 900,
    total_unrealized_pnl: 100,
    total_unrealized_pnl_percent: 11.11,
    total_day_pnl: 10,
    total_dividends: 20,
    total_realized_pnl: 0,
    holdings: [],
  };

  let portfolioService: {
    refreshQuotes: ReturnType<typeof vi.fn>;
    getRecalcStatus: ReturnType<typeof vi.fn>;
    getSummary: ReturnType<typeof vi.fn>;
    getUpcomingExDividends: ReturnType<typeof vi.fn>;
  };

  beforeEach(async () => {
    vi.useFakeTimers();
    portfolioService = {
      refreshQuotes: vi.fn(),
      getRecalcStatus: vi.fn(),
      getSummary: vi.fn().mockReturnValue(of(summary)),
      getUpcomingExDividends: vi.fn().mockReturnValue(of([])),
    };

    await TestBed.configureTestingModule({
      imports: [PortfolioDashboardComponent],
      providers: [{ provide: PortfolioService, useValue: portfolioService }],
    })
      .overrideComponent(PortfolioDashboardComponent, {
        set: {
          template: '<button class="refresh-trigger" (click)="loadSummary()">刷新行情</button>',
          imports: [],
        },
      })
      .compileComponents();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function createFixture() {
    const fixture = TestBed.createComponent(PortfolioDashboardComponent);
    const component = fixture.componentInstance;
    const chartReload = vi.fn();
    portfolioService.refreshQuotes.mockReturnValueOnce(of(null));
    fixture.detectChanges();
    component.chart = { reload: chartReload } as unknown as NetworthChartComponent;
    portfolioService.refreshQuotes.mockClear();
    portfolioService.getRecalcStatus.mockClear();
    portfolioService.getSummary.mockClear();
    chartReload.mockClear();

    return { fixture, chartReload };
  }

  function clickRefresh(fixture: ReturnType<typeof TestBed.createComponent<PortfolioDashboardComponent>>) {
    const button = fixture.nativeElement.querySelector('.refresh-trigger') as HTMLButtonElement;
    button.click();
  }

  it('refreshes quotes, polls until completed, then reloads summary and chart', () => {
    portfolioService.refreshQuotes.mockReturnValue(
      of({
        refresh_scheduled: true,
        date: '2026-05-18',
        touched_symbols: ['2330'],
      }),
    );
    portfolioService.getRecalcStatus.mockReturnValue(of({ state: 'completed' }));
    const { fixture, chartReload } = createFixture();

    clickRefresh(fixture);

    expect(portfolioService.refreshQuotes).toHaveBeenCalledTimes(1);
    expect(portfolioService.getSummary).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1000);

    expect(portfolioService.getRecalcStatus).toHaveBeenCalledTimes(1);
    expect(portfolioService.getSummary).toHaveBeenCalledTimes(1);
    expect(chartReload).toHaveBeenCalledTimes(1);
  });

  it('warns and still reloads summary when refresh quotes is already running', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    portfolioService.refreshQuotes.mockReturnValue(throwError(() => ({ status: 409 })));
    const { fixture, chartReload } = createFixture();

    clickRefresh(fixture);

    expect(warnSpy).toHaveBeenCalledWith('另一筆重算進行中, 稍候再試');
    expect(portfolioService.getSummary).toHaveBeenCalledTimes(1);
    expect(chartReload).toHaveBeenCalledTimes(1);
  });

  it('reloads summary and chart when polling times out after 30 seconds', () => {
    portfolioService.refreshQuotes.mockReturnValue(
      of({
        refresh_scheduled: true,
        date: '2026-05-18',
        touched_symbols: ['2330'],
      }),
    );
    portfolioService.getRecalcStatus.mockReturnValue(of({ state: 'running' }));
    const { fixture, chartReload } = createFixture();

    clickRefresh(fixture);
    vi.advanceTimersByTime(30_000);

    expect(portfolioService.getSummary).toHaveBeenCalledTimes(1);
    expect(chartReload).toHaveBeenCalledTimes(1);
  });
});
