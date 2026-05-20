import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PortfolioService } from '../../../services/portfolio.service';
import { PortfolioRealizedPnlComponent } from './realized-pnl';

describe('PortfolioRealizedPnlComponent', () => {
  const events = [
    {
      trade_date: '2026-05-01',
      symbol: '2330',
      name: '台積電',
      quantity: 1000,
      sell_price: '800',
      avg_cost_at_sale: '700',
      fee: '100',
      tax: '2400',
      proceeds_gross: '800000',
      proceeds_net: '797500',
      cost_out: '700000',
      realized_pnl: '97500',
      is_day_trade: true,
      position_side: 'LONG',
      note: null,
    },
    {
      trade_date: '2025-03-03',
      symbol: '2317',
      name: '鴻海',
      quantity: 500,
      sell_price: '180',
      avg_cost_at_sale: '200',
      fee: '50',
      tax: '270',
      proceeds_gross: '90000',
      proceeds_net: '89680',
      cost_out: '100000',
      realized_pnl: '-10320',
      is_day_trade: false,
      position_side: 'LONG',
      note: 'no_long_inventory',
    },
  ];

  let portfolioService: {
    getRealizedPnl: ReturnType<typeof vi.fn>;
    getSymbolNames: ReturnType<typeof vi.fn>;
  };

  beforeEach(async () => {
    vi.useFakeTimers();
    localStorage.clear();
    portfolioService = {
      getRealizedPnl: vi.fn().mockReturnValue(
        of({
          items: events,
          total: events.length,
          summary: {
            filter_scope_total: '87180',
            filter_scope_count: 2,
            ytd_total: '97500',
            ytd_count: 1,
          },
        }),
      ),
      getSymbolNames: vi.fn().mockReturnValue(of({ '2330': '台積電', '2317': '鴻海' })),
    };

    await TestBed.configureTestingModule({
      imports: [PortfolioRealizedPnlComponent],
      providers: [{ provide: PortfolioService, useValue: portfolioService }],
    })
      .overrideComponent(PortfolioRealizedPnlComponent, {
        set: {
          template: `
            @for (event of events(); track eventKey(event)) {
              <button class="event-row" type="button" (click)="toggleExpanded(event)">
                {{ event.symbol }}
              </button>
              @if (expandedKey() === eventKey(event)) {
                <div class="expanded-panel">
                  <span class="expanded-symbol">{{ event.symbol }}</span>
                  <span class="expanded-net">{{ event.proceeds_net }}</span>
                </div>
              }
            }
          `,
          imports: [],
        },
      })
      .compileComponents();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    localStorage.clear();
  });

  function createFixture() {
    const fixture = TestBed.createComponent(PortfolioRealizedPnlComponent);
    fixture.detectChanges();
    portfolioService.getRealizedPnl.mockClear();
    return fixture;
  }

  it('debounces filter input before fetching', () => {
    const fixture = createFixture();

    fixture.componentInstance.onSearchInput('台積電');

    expect(portfolioService.getRealizedPnl).not.toHaveBeenCalled();

    vi.advanceTimersByTime(299);
    expect(portfolioService.getRealizedPnl).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(portfolioService.getRealizedPnl).toHaveBeenCalledWith(
      expect.objectContaining({ symbol: '2330', offset: 0 }),
    );
  });

  it('clears manual date range when choosing a year preset', () => {
    const fixture = createFixture();
    const component = fixture.componentInstance;
    component.dateRange = [new Date('2024-01-01'), new Date('2024-12-31')];
    component.onDateRangeChange(component.dateRange);
    portfolioService.getRealizedPnl.mockClear();

    component.onYearPresetChange(2025);

    expect(component.dateRange).toBeNull();
    expect(component.selectedYear()).toBe(2025);
    expect(component.query()).toEqual(
      expect.objectContaining({
        year: 2025,
        date_from: null,
        date_to: null,
        offset: 0,
      }),
    );
    expect(portfolioService.getRealizedPnl).toHaveBeenCalledWith(
      expect.objectContaining({ year: 2025, date_from: null, date_to: null }),
    );
  });

  it('toggles one expanded row at a time', () => {
    const fixture = createFixture();
    const rows = fixture.nativeElement.querySelectorAll('.event-row') as NodeListOf<HTMLButtonElement>;

    rows[0].click();
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.expanded-symbol')?.textContent).toContain('2330');

    rows[1].click();
    fixture.detectChanges();

    const panels = fixture.nativeElement.querySelectorAll('.expanded-panel');
    expect(panels.length).toBe(1);
    expect(fixture.nativeElement.querySelector('.expanded-symbol')?.textContent).toContain('2317');
  });

  it('fetches with new offset and limit when pagination changes', () => {
    const fixture = createFixture();

    fixture.componentInstance.onPageChange({ first: 50, rows: 50 });

    expect(localStorage.getItem('portfolio.realizedPnl.pageSize')).toBe('50');
    expect(portfolioService.getRealizedPnl).toHaveBeenCalledWith(
      expect.objectContaining({ offset: 50, limit: 50 }),
    );
  });
});
