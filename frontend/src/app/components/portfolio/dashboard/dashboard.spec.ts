import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { of, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PortfolioSummary } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';
import { NetworthChartComponent } from '../networth-chart/networth-chart';
import { PortfolioDashboardComponent } from './dashboard';
import { CardModule } from 'primeng/card';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';
import { ButtonModule } from 'primeng/button';
import { Tooltip, TooltipModule } from 'primeng/tooltip';
import { AccordionModule } from 'primeng/accordion';
import { SkeletonModule } from 'primeng/skeleton';
import { SelectButtonModule } from 'primeng/selectbutton';

@Component({
  selector: 'app-corporate-actions-panel',
  standalone: true,
  template: '',
})
class CorporateActionsPanelStubComponent {}

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
      xirr_3m: 0.03,
      xirr_1y: 0.1234,
      xirr_ytd: 0.04,
    };
  }

  function buildSummary(overrides: Partial<PortfolioSummary> = {}): PortfolioSummary {
    return {
      total_market_value: 1000,
      total_cost: 900,
      total_unrealized_pnl: 100,
      total_unrealized_pnl_percent: 11.11,
      total_day_pnl: 10,
      total_dividends: 20,
      total_realized_pnl: 0,
      portfolio_xirr: 0.5,
      portfolio_xirr_1m: 0.01,
      portfolio_xirr_3m: 0.03,
      portfolio_xirr_1y: 0.1234,
      portfolio_xirr_ytd: 0.04,
      holdings: [],
      ...overrides,
    };
  }

  const summary = buildSummary();

  let portfolioService: {
    refreshQuotes: ReturnType<typeof vi.fn>;
    getRecalcStatus: ReturnType<typeof vi.fn>;
    getSummary: ReturnType<typeof vi.fn>;
    getUpcomingExDividends: ReturnType<typeof vi.fn>;
    getNetworthHistory: ReturnType<typeof vi.fn>;
  };

  beforeEach(async () => {
    vi.useFakeTimers();
    portfolioService = {
      refreshQuotes: vi.fn(),
      getRecalcStatus: vi.fn(),
      getSummary: vi.fn().mockReturnValue(of(summary)),
      getUpcomingExDividends: vi.fn().mockReturnValue(of([])),
      getNetworthHistory: vi.fn().mockReturnValue(of([])),
    };

    await TestBed.configureTestingModule({
      imports: [PortfolioDashboardComponent],
      providers: [{ provide: PortfolioService, useValue: portfolioService }],
    })
      .overrideComponent(NetworthChartComponent, {
        set: {
          template: '',
          imports: [],
        },
      })
      .overrideComponent(PortfolioDashboardComponent, {
        set: {
          imports: [
            CommonModule,
            FormsModule,
            CardModule,
            TableModule,
            TagModule,
            ButtonModule,
            TooltipModule,
            AccordionModule,
            SkeletonModule,
            SelectButtonModule,
            NetworthChartComponent,
            CorporateActionsPanelStubComponent,
          ],
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
    portfolioService.getSummary.mockReturnValue(of(buildSummary({ holdings: [buildHolding()] })));
    portfolioService.refreshQuotes.mockReturnValueOnce(of(null));
    fixture.detectChanges();
    const chartReload = vi.fn();
    Object.defineProperty(component, 'chart', {
      configurable: true,
      get: () => ({ reload: chartReload }),
      set: () => undefined,
    });
    portfolioService.refreshQuotes.mockClear();
    portfolioService.getRecalcStatus.mockClear();
    portfolioService.getSummary.mockClear();
    chartReload.mockClear();

    return { fixture, chartReload };
  }

  function clickRefresh(fixture: ReturnType<typeof TestBed.createComponent<PortfolioDashboardComponent>>) {
    const button = fixture.nativeElement.querySelector('.hub-refresh-btn') as HTMLButtonElement;
    button.click();
  }

  function createDashboardFixture(summaryResponse: PortfolioSummary) {
    portfolioService.getSummary.mockReturnValue(of(summaryResponse));
    portfolioService.refreshQuotes.mockReturnValueOnce(of(null));
    const fixture = TestBed.createComponent(PortfolioDashboardComponent);
    fixture.detectChanges();
    fixture.detectChanges();
    return fixture;
  }

  function clickXirrChip(fixture: ReturnType<typeof TestBed.createComponent<PortfolioDashboardComponent>>, label: string) {
    const buttons = Array.from(
      fixture.nativeElement.querySelectorAll('.xirr-window-control p-togglebutton'),
    ) as HTMLElement[];
    const button = buttons.find((candidate) => candidate.textContent?.trim() === label);
    expect(button).toBeTruthy();
    button!.click();
    fixture.detectChanges();
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

  it('selects the 1Y XIRR chip by default', () => {
    const fixture = createDashboardFixture(
      buildSummary({
        holdings: [
          buildHolding(),
        ],
      }),
    );
    const component = fixture.componentInstance;

    expect(component.xirrWindow()).toBe('1y');
    expect(fixture.nativeElement.querySelector('.xirr-window-control')?.textContent).toContain('1Y');
    expect(fixture.nativeElement.querySelector('.xirr-card .value')?.textContent).toContain('12.34%');
  });

  it('changes the rendered XIRR card value when switching chips', () => {
    const fixture = createDashboardFixture(
      buildSummary({
        portfolio_xirr_3m: 0.0321,
        holdings: [
          {
            ...buildHolding(),
            xirr_3m: 0.0321,
          },
        ],
      }),
    );

    clickXirrChip(fixture, '3M');

    expect(fixture.componentInstance.xirrWindow()).toBe('3m');
    expect(fixture.nativeElement.querySelector('.xirr-card .value')?.textContent).toContain('3.21%');
  });

  it('renders a dash with tooltip when the selected XIRR field is null', () => {
    const fixture = createDashboardFixture(
      buildSummary({
        portfolio_xirr_1m: null,
        holdings: [
          {
            ...buildHolding(),
            xirr_1m: null,
            xirr_3m: 0.0321,
          },
        ],
      }),
    );

    clickXirrChip(fixture, '1M');

    const placeholder = fixture.debugElement.query(By.css('.xirr-card .xirr-placeholder'));
    expect(placeholder.nativeElement.textContent.trim()).toBe('—');
    expect(placeholder.injector.get(Tooltip, null)).toBeTruthy();
  });
});
