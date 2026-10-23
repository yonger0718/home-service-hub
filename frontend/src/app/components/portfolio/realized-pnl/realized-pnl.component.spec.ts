import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { MessageService } from 'primeng/api';
import { Subject, of } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { RealizedPnlEvent, RealizedPnlPaged, RealizedPnlQuery } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';
import { REALIZED_FIXTURE } from './realized-fixture.spec-data';
import { PortfolioRealizedPnlComponent } from './realized-pnl';

describe('PortfolioRealizedPnlComponent', () => {
  const events = [
    {
      trade_date: '2026-05-01',
      symbol: '2330',
      market: 'TW',
      broker: 'TW_MANUAL',
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
      native_proceeds: '797500',
      native_cost: '700000',
      native_currency: 'TWD',
      is_day_trade: true,
      position_side: 'LONG',
      note: null,
    },
    {
      trade_date: '2025-03-03',
      symbol: '2317',
      market: 'TW',
      broker: 'TW_MANUAL',
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
      native_proceeds: '89680',
      native_cost: '100000',
      native_currency: 'TWD',
      is_day_trade: false,
      position_side: 'LONG',
      note: 'no_long_inventory',
    },
  ];

  let portfolioService: {
    getRealizedPnl: ReturnType<typeof vi.fn>;
    getSymbolNames: ReturnType<typeof vi.fn>;
    getTransactionBrokers: ReturnType<typeof vi.fn>;
  };

  beforeEach(async () => {
    vi.useFakeTimers();
    localStorage.clear();
    const page = {
      items: events,
      total: events.length,
      summary: {
        filter_scope_total: '87180',
        filter_scope_count: 2,
        ytd_total: '97500',
        ytd_count: 1,
      },
    };
    portfolioService = {
      // Both the filter-wide load (limit 200) and the list page get these two rows; `listQueries()` splits the calls.
      getRealizedPnl: vi.fn((_query: RealizedPnlQuery) => of(page)),
      getSymbolNames: vi.fn().mockReturnValue(of({ '2330': '台積電', '2317': '鴻海' })),
      getTransactionBrokers: vi.fn().mockReturnValue(of([])),
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

  function listQueries(): RealizedPnlQuery[] {
    return portfolioService.getRealizedPnl.mock.calls.map(([query]) => query as RealizedPnlQuery).filter(query => query.limit !== 200);
  }

  it('debounces filter input before fetching', () => {
    const fixture = createFixture();

    fixture.componentInstance.onSearchInput('台積電');

    expect(portfolioService.getRealizedPnl).not.toHaveBeenCalled();

    vi.advanceTimersByTime(299);
    expect(portfolioService.getRealizedPnl).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(listQueries()).toEqual([expect.objectContaining({ symbol: '2330', offset: 0 })]);
    expect(portfolioService.getRealizedPnl).toHaveBeenCalledWith(
      expect.objectContaining({ symbol: '2330', limit: 200, offset: 0 }),
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
    expect(portfolioService.getRealizedPnl).toHaveBeenCalledTimes(1);
    expect(listQueries()).toEqual([expect.objectContaining({ offset: 50, limit: 50 })]);
  });
});

interface Call {
  query: RealizedPnlQuery;
  response: Subject<RealizedPnlPaged>;
}

function paged(items: RealizedPnlEvent[], total = items.length): RealizedPnlPaged {
  return { items, total, summary: { filter_scope_total: '21046', filter_scope_count: total, ytd_total: '21046', ytd_count: total } };
}

function respond(call: Call, value: RealizedPnlPaged): void {
  call.response.next(value);
  call.response.complete();
}

describe('PortfolioRealizedPnlComponent filter-wide set', () => {
  let listCalls: Call[];
  let allCalls: Call[];
  let portfolioService: Record<string, ReturnType<typeof vi.fn>>;

  beforeEach(() => {
    localStorage.clear();
    listCalls = [];
    allCalls = [];
    portfolioService = {
      getSymbolNames: vi.fn(() => of({})),
      getTransactionBrokers: vi.fn(() => of([])),
      getRealizedPnl: vi.fn((query: RealizedPnlQuery) => {
        const response = new Subject<RealizedPnlPaged>();
        // The list pages by 25 here; the filter-wide set always pages by 200.
        (query.limit === 200 ? allCalls : listCalls).push({ query, response });
        return response;
      }),
    };
    TestBed.configureTestingModule({
      providers: [provideRouter([]), { provide: PortfolioService, useValue: portfolioService }],
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  function create(): ComponentFixture<PortfolioRealizedPnlComponent> {
    const fixture = TestBed.createComponent(PortfolioRealizedPnlComponent);
    fixture.detectChanges();
    return fixture;
  }

  /** Answers the initial list page (the first two sells, server total 5) and the filter-wide set. */
  function settle(fixture: ComponentFixture<PortfolioRealizedPnlComponent>): void {
    respond(listCalls.at(-1)!, paged(REALIZED_FIXTURE.slice(0, 2), 5));
    respond(allCalls.at(-1)!, paged(REALIZED_FIXTURE));
    fixture.detectChanges();
  }

  function el(fixture: ComponentFixture<PortfolioRealizedPnlComponent>): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  it('loads the filter-wide set (200 per page, active sort) and the list page on init', () => {
    const fixture = create();
    settle(fixture);

    expect(allCalls.map(call => call.query)).toEqual([
      expect.objectContaining({ sort: 'trade_date:desc', limit: 200, offset: 0 }),
    ]);
    expect(listCalls.map(call => call.query)).toEqual([expect.objectContaining({ limit: 25, offset: 0 })]);
    expect(fixture.componentInstance.allEvents()).toEqual(REALIZED_FIXTURE);
    expect(fixture.componentInstance.marketEvents()).toEqual(REALIZED_FIXTURE);
  });

  it('reloads the set and the list once when 僅當沖 turns on', () => {
    const fixture = create();
    settle(fixture);

    fixture.componentInstance.onDayTradeOnlyChange(true);

    expect(allCalls).toHaveLength(2);
    expect(allCalls[1].query).toEqual(expect.objectContaining({ day_trade_only: true, limit: 200, offset: 0 }));
    expect(listCalls).toHaveLength(2);
    expect(listCalls[1].query).toEqual(expect.objectContaining({ day_trade_only: true, offset: 0 }));
  });

  it('reloads the set on a sort change', () => {
    const fixture = create();
    settle(fixture);

    fixture.componentInstance.onSortChange('realized_pnl:desc');

    expect(allCalls).toHaveLength(2);
    expect(allCalls[1].query).toEqual(expect.objectContaining({ sort: 'realized_pnl:desc', limit: 200 }));
  });

  it('only fetches the list page on a page change under 全部', () => {
    const fixture = create();
    settle(fixture);

    fixture.componentInstance.onPageChange({ first: 25, rows: 25 });

    expect(allCalls).toHaveLength(1);
    expect(listCalls).toHaveLength(2);
    expect(listCalls[1].query).toEqual(expect.objectContaining({ offset: 25, limit: 25 }));
  });

  it('filters the loaded set by market without an API call', () => {
    const fixture = create();
    settle(fixture);
    portfolioService['getRealizedPnl'].mockClear();
    const component = fixture.componentInstance;

    component.onMarketChange('TW');
    expect(component.marketEvents()?.map(e => e.symbol)).toEqual(['2317', '2330', '00878', '0050']);
    component.onMarketChange('US');
    expect(component.marketEvents()?.map(e => e.symbol)).toEqual(['AAPL']);

    expect(portfolioService['getRealizedPnl']).not.toHaveBeenCalled();
  });

  it('pages the market set client-side under 台股 without an API call', () => {
    const fixture = create();
    settle(fixture);
    const component = fixture.componentInstance;
    component.onMarketChange('TW');
    portfolioService['getRealizedPnl'].mockClear();

    component.onPageChange({ first: 25, rows: 25 });

    expect(portfolioService['getRealizedPnl']).not.toHaveBeenCalled();
    expect(component.clientOffset()).toBe(25);
    expect(component.query().offset).toBe(0);
  });

  it('resets the client offset on a market or filter change', () => {
    const fixture = create();
    settle(fixture);
    const component = fixture.componentInstance;
    component.onMarketChange('TW');
    component.onPageChange({ first: 25, rows: 25 });

    component.onMarketChange('US');
    expect(component.clientOffset()).toBe(0);

    component.onPageChange({ first: 25, rows: 25 });
    component.onDayTradeOnlyChange(true);
    expect(component.clientOffset()).toBe(0);
  });

  it('defers the list fetch of a filter change under 台股 until 全部 is back', () => {
    const fixture = create();
    settle(fixture);
    const component = fixture.componentInstance;
    component.onMarketChange('TW');

    component.onDayTradeOnlyChange(true);
    expect(allCalls).toHaveLength(2);
    expect(listCalls).toHaveLength(1);

    component.onMarketChange('ALL');
    expect(listCalls).toHaveLength(2);
    expect(listCalls[1].query).toEqual(expect.objectContaining({ day_trade_only: true, offset: 0 }));

    component.onMarketChange('TW');
    component.onMarketChange('ALL');
    expect(listCalls).toHaveLength(2);
  });

  it('cancels a superseded set load and drops its late response', () => {
    const fixture = create();
    const older = allCalls[0].response;

    fixture.componentInstance.onDayTradeOnlyChange(true);
    respond(allCalls[1], paged([REALIZED_FIXTURE[1]]));
    older.next(paged(REALIZED_FIXTURE));
    older.complete();

    expect(older.observed).toBe(false);
    expect(allCalls).toHaveLength(2);
    expect(fixture.componentInstance.allEvents()).toEqual([REALIZED_FIXTURE[1]]);
  });

  it('hides the set silently on a load error and keeps the list page', () => {
    const fixture = create();
    const add = vi.spyOn(fixture.debugElement.injector.get(MessageService), 'add');
    respond(listCalls[0], paged(REALIZED_FIXTURE.slice(0, 2), 5));

    allCalls[0].response.error(new Error('boom'));
    fixture.detectChanges();

    const component = fixture.componentInstance;
    expect(component.allFailed()).toBe(true);
    expect(component.allEvents()).toBeNull();
    expect(component.marketEvents()).toBeNull();
    expect(component.events()).toEqual(REALIZED_FIXTURE.slice(0, 2));
    expect(component.total()).toBe(5);
    expect(add).not.toHaveBeenCalled();
    expect(el(fixture).querySelector('.meta-loading')).toBeNull();
  });

  it('flags the set as capped after 50 pages of 200', () => {
    const fixture = create();
    respond(listCalls[0], paged(REALIZED_FIXTURE.slice(0, 2), 10_001));
    for (let page = 0; page < 50; page++) {
      respond(allCalls[page], paged(Array.from({ length: 200 }, () => REALIZED_FIXTURE[0]), 10_001));
    }

    expect(allCalls).toHaveLength(50);
    expect(allCalls[49].query.offset).toBe(9_800);
    expect(fixture.componentInstance.allCapped()).toBe(true);
    expect(fixture.componentInstance.allEvents()).toHaveLength(10_000);
  });

  it('cancels both the set load and the list request on destroy', () => {
    const fixture = create();
    const all = allCalls[0].response;
    const list = listCalls[0].response;
    expect(all.observed).toBe(true);
    expect(list.observed).toBe(true);

    fixture.destroy();

    expect(all.observed).toBe(false);
    expect(list.observed).toBe(false);
  });

  it('shows no card values and the loading line until the set lands', () => {
    const fixture = create();
    respond(listCalls[0], paged(REALIZED_FIXTURE.slice(0, 2), 5));
    fixture.detectChanges();

    const values = () => Array.from(el(fixture).querySelectorAll('.realized-summary-card .value')).map(v => v.textContent?.trim());
    expect(values()).toEqual(['', '']);
    expect(el(fixture).querySelector('.meta-loading')?.textContent).toContain('查詢中');

    respond(allCalls[0], paged(REALIZED_FIXTURE));
    fixture.detectChanges();

    expect(values().every(value => !!value)).toBe(true);
    expect(el(fixture).querySelector('.meta-loading')).toBeNull();
  });
});
