import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { MessageService } from 'primeng/api';
import { Subject, of } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { RealizedPnlEvent, RealizedPnlPaged, RealizedPnlQuery } from '../../../models/portfolio.model';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { PortfolioService } from '../../../services/portfolio.service';
import { REALIZED_FIXTURE, TODAY } from './realized-fixture.spec-data';
import { barsAriaText, monthlyBars } from './realized-math';
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
    expect(listQueries()).toEqual([expect.objectContaining({ year: 2025, date_from: null, date_to: null, offset: 0 })]);
    expect(portfolioService.getRealizedPnl).toHaveBeenCalledWith(
      expect.objectContaining({ year: 2025, date_from: null, date_to: null, limit: 200, offset: 0 }),
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
    // Only the clock is faked (today = 2026-10-22): the Subjects answer synchronously, the debounce keeps real timers.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(TODAY);
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
    TestBed.inject(LayoutModeService).set(null);
    vi.useRealTimers();
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

  it('keeps a page-size change under 台股 client-side and fetches the new size once back on 全部', () => {
    const fixture = create();
    settle(fixture);
    const component = fixture.componentInstance;
    component.onPageChange({ first: 25, rows: 25 });
    respond(listCalls[1], paged(REALIZED_FIXTURE.slice(2), 5));
    component.onMarketChange('TW');

    component.onPageChange({ first: 0, rows: 50 });

    expect(listCalls).toHaveLength(2);
    expect(localStorage.getItem('portfolio.realizedPnl.pageSize')).toBe('50');
    expect(component.query()).toEqual(expect.objectContaining({ offset: 0, limit: 50 }));
    expect(component.clientOffset()).toBe(0);

    component.onMarketChange('ALL');
    expect(listCalls).toHaveLength(3);
    expect(listCalls[2].query).toEqual(expect.objectContaining({ offset: 0, limit: 50 }));
    expect(allCalls).toHaveLength(1);
  });

  it('cancels an in-flight list page when a filter change under 台股 skips the fetch', () => {
    const fixture = create();
    respond(allCalls[0], paged(REALIZED_FIXTURE));
    const component = fixture.componentInstance;
    const pending = listCalls[0].response;
    component.onMarketChange('TW');

    component.onDayTradeOnlyChange(true);

    expect(pending.observed).toBe(false);
    expect(component.loading()).toBe(false);
    pending.next(paged(REALIZED_FIXTURE, 5));
    expect(component.events()).toEqual([]);
    expect(component.total()).toBe(0);
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

    const values = () => Array.from(el(fixture).querySelectorAll('app-bento.kpi-card .value')).map(v => v.textContent?.trim());
    expect(values()).toEqual(['', '', '', '']);
    expect(el(fixture).querySelector('.meta-loading')?.textContent).toContain('查詢中');

    respond(allCalls[0], paged(REALIZED_FIXTURE));
    fixture.detectChanges();

    expect(values().every(value => !!value)).toBe(true);
    expect(el(fixture).querySelector('.meta-loading')).toBeNull();
  });

  /** The page under the 2026 year filter with the fixture: the list page holds the first two sells of 5. */
  function create2026(): ComponentFixture<PortfolioRealizedPnlComponent> {
    const fixture = create();
    fixture.componentInstance.onYearPresetChange(2026);
    settle(fixture);
    return fixture;
  }

  function subtitle(fixture: ComponentFixture<PortfolioRealizedPnlComponent>): string | undefined {
    return el(fixture).querySelector('app-page-header .page-subtitle')?.textContent?.trim();
  }

  function cards(fixture: ComponentFixture<PortfolioRealizedPnlComponent>) {
    return Array.from(el(fixture).querySelectorAll('app-bento.kpi-card')).map(card => ({
      label: card.querySelector('.label')?.textContent?.trim(),
      value: card.querySelector('.value')?.textContent?.trim(),
      up: card.querySelector('.value')?.classList.contains('up'),
      down: card.querySelector('.value')?.classList.contains('down'),
      meta: card.querySelector('.meta')?.textContent?.trim(),
    }));
  }

  function barCols(fixture: ComponentFixture<PortfolioRealizedPnlComponent>): HTMLElement[] {
    return Array.from(el(fixture).querySelectorAll<HTMLElement>('.bar-col'));
  }

  function clearButton(fixture: ComponentFixture<PortfolioRealizedPnlComponent>): HTMLButtonElement | null {
    return Array.from(el(fixture).querySelectorAll<HTMLButtonElement>('button.clear-filters')).find(b => b.textContent?.trim() === '清除篩選') ?? null;
  }

  it('shows the range and the server count in the header before the set lands, then the set count', () => {
    const fixture = create();
    fixture.componentInstance.onYearPresetChange(2026);
    respond(listCalls.at(-1)!, paged(REALIZED_FIXTURE.slice(0, 2), 7));
    fixture.detectChanges();
    expect(subtitle(fixture)).toBe('2026 年 · 7 筆賣出');

    respond(allCalls.at(-1)!, paged(REALIZED_FIXTURE));
    fixture.detectChanges();
    expect(subtitle(fixture)).toBe('2026 年 · 5 筆賣出');
  });

  it('renders the header without actions', () => {
    const fixture = create2026();
    const actions = el(fixture).querySelector('app-page-header .page-actions');
    expect(actions).not.toBeNull();
    expect(actions!.children).toHaveLength(0);
    expect(actions!.textContent?.trim()).toBe('');
  });

  it('renders the four summary cards in order', () => {
    const fixture = create2026();

    expect(cards(fixture)).toEqual([
      { label: '2026 年已實現', value: '+21,046', up: true, down: false, meta: '5 筆 · 4 筆獲利、1 筆虧損' },
      { label: '本月（10 月）', value: '+13,498', up: true, down: false, meta: '1 筆' },
      { label: '當沖', value: '+2,030', up: true, down: false, meta: '1 筆 · 交易稅減半' },
      { label: '賣出成本費用', value: '$1,764', up: true, down: false, meta: '賣出手續費＋交易稅' },
    ]);
    expect(el(fixture).querySelector('.summary-note')).toBeNull();
  });

  it('colours a negative card with .down and a U+2212 minus', () => {
    const fixture = create();
    fixture.componentInstance.onYearPresetChange(2026);
    respond(listCalls.at(-1)!, { ...paged([REALIZED_FIXTURE[3]]), summary: { filter_scope_total: '-4897', filter_scope_count: 1, ytd_total: '-4897', ytd_count: 1 } });
    respond(allCalls.at(-1)!, paged([REALIZED_FIXTURE[3]]));
    fixture.detectChanges();

    expect(cards(fixture)[0]).toEqual(expect.objectContaining({ value: '−4,897', up: false, down: true, meta: '1 筆 · 0 筆獲利、1 筆虧損' }));
  });

  it('recomputes the cards, the header count and the bars from the 台股 set', () => {
    const fixture = create2026();

    fixture.componentInstance.onMarketChange('TW');
    fixture.detectChanges();

    expect(cards(fixture)[0]).toEqual(expect.objectContaining({ value: '+15,345', up: true, meta: '4 筆 · 3 筆獲利、1 筆虧損' }));
    expect(subtitle(fixture)).toBe('2026 年 · 4 筆賣出');
    expect(barCols(fixture)[5].querySelector('.bar')).toBeNull();
    expect(barCols(fixture)[3].querySelector('.bar')).not.toBeNull();
  });

  it('hides the cards and the bars after a set failure and keeps the list', () => {
    const fixture = create();
    fixture.componentInstance.onYearPresetChange(2026);
    respond(listCalls.at(-1)!, paged(REALIZED_FIXTURE.slice(0, 2), 5));
    allCalls.at(-1)!.response.error(new Error('boom'));
    fixture.detectChanges();

    expect(el(fixture).querySelector('app-bento.kpi-card')).toBeNull();
    expect(el(fixture).querySelector('.bar-col')).toBeNull();
    expect(el(fixture).querySelector('.bars-card')).toBeNull();
    expect(fixture.componentInstance.filteredEvents()).toEqual(REALIZED_FIXTURE.slice(0, 2));
    expect(el(fixture).textContent).toContain('鴻海');
    expect(subtitle(fixture)).toBe('2026 年 · 5 筆賣出');
  });

  it('notes the 10,000-row cap and keeps the server total as the headline', () => {
    const fixture = create();
    respond(listCalls[0], paged(REALIZED_FIXTURE.slice(0, 2), 10_001));
    for (let page = 0; page < 50; page++) {
      respond(allCalls[page], paged(Array.from({ length: 200 }, () => REALIZED_FIXTURE[0]), 10_001));
    }
    fixture.detectChanges();

    expect(el(fixture).querySelector('.summary-note')?.textContent?.trim()).toBe('⋯ 僅載入前 10,000 筆；筆數與本月／當沖／費用為已載入部分');
    expect(cards(fixture)[0].value).toBe('+21,046');
  });

  it('draws twelve monthly columns for the year', () => {
    const fixture = create2026();
    const card = el(fixture).querySelector('app-bento.bars-card');
    expect(card?.querySelector('.card-title')?.textContent?.trim()).toBe('每月已實現 · 2026');
    expect(card?.textContent).toContain('柱上數字為當月合計');

    const cols = barCols(fixture);
    expect(cols).toHaveLength(12);
    const april = cols[3];
    const bar = april.querySelector<HTMLElement>('.bar');
    expect(bar?.classList.contains('down')).toBe(true);
    expect(bar?.classList.contains('up')).toBe(false);
    expect(Number(bar?.dataset['ratio'])).toBeCloseTo(4897 / 13498, 4);
    expect(parseFloat(bar!.style.getPropertyValue('--r'))).toBeCloseTo(0.3628, 4);
    expect(april.querySelector('.bar-amount')?.textContent?.trim()).toBe('−4,897');
    expect(april.querySelector('.bar-label')?.textContent?.trim()).toBe('4月');
    expect(cols[9].querySelector('.bar')?.classList.contains('up')).toBe(true);
    expect(cols[9].querySelector('.bar-amount')?.textContent?.trim()).toBe('+13,498');
    expect(cols[0].querySelector('.bar')).toBeNull();
    expect(cols.map((col, i) => (col.classList.contains('future') ? i + 1 : null)).filter(Boolean)).toEqual([11, 12]);

    const hidden = card?.querySelector('.visually-hidden')?.textContent?.trim();
    expect(hidden).toBe(barsAriaText(monthlyBars(REALIZED_FIXTURE, 2026, TODAY), 2026));
  });

  it('sizes every bar track to its own ratio (no full-length track hanging out of its half)', () => {
    const fixture = create2026();
    const bars = Array.from(el(fixture).querySelectorAll<HTMLElement>('.bar'));
    expect(bars).toHaveLength(5);
    for (const bar of bars) {
      const ratio = Number(bar.dataset['ratio']);
      expect(ratio).toBeGreaterThan(0);
      expect(ratio).toBeLessThanOrEqual(1);
      expect(Number(bar.style.getPropertyValue('--r'))).toBeCloseTo(ratio, 6);
      expect(bar.style.getPropertyValue('--h')).toBe('');
      expect(bar.style.height).toBe('');
    }
    const plot = el(fixture).querySelector<HTMLElement>('.bars')!;
    expect(Number(plot.style.getPropertyValue('--pos'))).toBe(1);
    expect(Number(plot.style.getPropertyValue('--neg'))).toBeCloseTo(4897 / 13498, 6);
  });

  it('shows the loading line in the bars card until the set lands', () => {
    const fixture = create();
    fixture.componentInstance.onYearPresetChange(2026);
    respond(listCalls.at(-1)!, paged(REALIZED_FIXTURE.slice(0, 2), 5));
    fixture.detectChanges();
    const card = () => el(fixture).querySelector('app-bento.bars-card');

    expect(card()).not.toBeNull();
    expect(card()!.querySelector('.bars-loading')?.textContent).toContain('查詢中');
    expect(card()!.querySelector('.bar-col')).toBeNull();
    expect(card()!.querySelector('.visually-hidden')).toBeNull();

    respond(allCalls.at(-1)!, paged(REALIZED_FIXTURE));
    fixture.detectChanges();
    expect(card()!.querySelector('.bars-loading')).toBeNull();
    expect(card()!.querySelectorAll('.bar-col')).toHaveLength(12);
  });

  it('shows — as the headline while a reload under 全部 waits for the server total', () => {
    const fixture = create2026();
    fixture.componentInstance.onDayTradeOnlyChange(true);
    respond(allCalls.at(-1)!, paged([REALIZED_FIXTURE[1]]));
    fixture.detectChanges();
    expect(cards(fixture)[0]).toEqual(expect.objectContaining({ value: '—', up: false, down: false, meta: '1 筆 · 1 筆獲利、0 筆虧損' }));

    respond(listCalls.at(-1)!, { ...paged([REALIZED_FIXTURE[1]]), summary: { filter_scope_total: '2030', filter_scope_count: 1, ytd_total: '2030', ytd_count: 1 } });
    fixture.detectChanges();
    expect(cards(fixture)[0].value).toBe('+2,030');
  });

  it('shows — after 台股 → filter change → 全部 until the refetched list lands', () => {
    const fixture = create2026();
    const component = fixture.componentInstance;
    component.onMarketChange('TW');
    component.onDayTradeOnlyChange(true);
    respond(allCalls.at(-1)!, paged([REALIZED_FIXTURE[1]]));
    fixture.detectChanges();
    expect(cards(fixture)[0].value).toBe('+2,030');

    component.onMarketChange('ALL');
    fixture.detectChanges();
    expect(cards(fixture)[0].value).toBe('—');

    respond(listCalls.at(-1)!, { ...paged([REALIZED_FIXTURE[1]]), summary: { filter_scope_total: '2030', filter_scope_count: 1, ytd_total: '2030', ytd_count: 1 } });
    fixture.detectChanges();
    expect(cards(fixture)[0].value).toBe('+2,030');
  });

  it('falls back to the set total after a list failure under 全部, and to — when the set is capped', () => {
    const fixture = create();
    listCalls[0].response.error(new Error('boom'));
    respond(allCalls[0], paged(REALIZED_FIXTURE));
    fixture.detectChanges();
    expect(cards(fixture)[0].value).toBe('+21,046');

    const capped = TestBed.createComponent(PortfolioRealizedPnlComponent);
    capped.detectChanges();
    listCalls.at(-1)!.response.error(new Error('boom'));
    const first = allCalls.length - 1;
    for (let page = 0; page < 50; page++) {
      respond(allCalls[first + page], paged(Array.from({ length: 200 }, () => REALIZED_FIXTURE[0]), 10_001));
    }
    capped.detectChanges();
    expect(capped.componentInstance.allCapped()).toBe(true);
    expect(cards(capped)[0].value).toBe('—');
  });

  it('renders the filter card controls', () => {
    const fixture = create2026();
    fixture.componentInstance.brokerCatalog.set(['TW_CATHAY', 'FIRSTRADE']);
    fixture.detectChanges();
    const card = Array.from(el(fixture).querySelectorAll('app-bento')).find(b => b.querySelector('.card-title')?.textContent?.trim() === '篩選');
    expect(card).toBeDefined();

    expect(card!.querySelector('.search-wrap input[type="text"]')).not.toBeNull();
    const market = card!.querySelector('[role="radiogroup"][aria-label="市場"]');
    expect(Array.from(market?.querySelectorAll('button') ?? []).map(b => b.textContent?.trim())).toEqual(['全部', '台股', '美股']);
    const dayTrade = card!.querySelector<HTMLButtonElement>('button[role="switch"][aria-label="僅當沖"]');
    expect(dayTrade?.getAttribute('aria-checked')).toBe('false');
    expect(card!.querySelector('p-datepicker')).not.toBeNull();
    const broker = card!.querySelector('[role="radiogroup"][aria-label="券商篩選"]');
    expect(broker).not.toBeNull();
    expect(broker!.closest('.filter-field')?.querySelector('.field-label')?.textContent?.trim()).toBe('券商');
    expect(el(fixture).querySelector('.hub-filter-bar')).toBeNull();
    expect(el(fixture).querySelector('.realized-summary-row')).toBeNull();
  });

  it('wires the market toggle and the 僅當沖 switch', () => {
    const fixture = create2026();
    const component = fixture.componentInstance;

    el(fixture).querySelector<HTMLButtonElement>('[aria-label="市場"] button[aria-label="美股"]')!.click();
    expect(component.market()).toBe('US');

    el(fixture).querySelector<HTMLButtonElement>('button[role="switch"][aria-label="僅當沖"]')!.click();
    fixture.detectChanges();
    expect(component.query().day_trade_only).toBe(true);
    expect(el(fixture).querySelector('button[role="switch"][aria-label="僅當沖"]')?.getAttribute('aria-checked')).toBe('true');
  });

  it('toggles 僅當沖 from its visible label text', () => {
    const fixture = create2026();
    const label = el(fixture).querySelector<HTMLLabelElement>('label.switch-field');
    expect(label?.querySelector('button[role="switch"]')).not.toBeNull();
    const text = Array.from(label!.querySelectorAll('span')).find(span => span.textContent?.trim() === '僅當沖')!;

    text.click();
    fixture.detectChanges();

    expect(fixture.componentInstance.query().day_trade_only).toBe(true);
  });

  it('offers 清除篩選 with only 僅當沖 on and resets it', () => {
    const fixture = create();
    settle(fixture);
    expect(clearButton(fixture)).toBeNull();

    fixture.componentInstance.onDayTradeOnlyChange(true);
    settle(fixture);
    expect(clearButton(fixture)).not.toBeNull();

    clearButton(fixture)!.click();
    settle(fixture);
    expect(fixture.componentInstance.query().day_trade_only).toBeFalsy();
    expect(clearButton(fixture)).toBeNull();
  });

  it('offers 清除篩選 with only 台股 selected and resets the market with a 全部 fetch', () => {
    const fixture = create();
    settle(fixture);
    const component = fixture.componentInstance;

    component.onMarketChange('TW');
    fixture.detectChanges();
    expect(clearButton(fixture)).not.toBeNull();
    const lists = listCalls.length;
    const sets = allCalls.length;

    clearButton(fixture)!.click();
    expect(component.market()).toBe('ALL');
    expect(listCalls).toHaveLength(lists + 1);
    expect(allCalls).toHaveLength(sets + 1);
    settle(fixture);
    expect(clearButton(fixture)).toBeNull();
    expect(el(fixture).querySelector('[aria-label="市場"] button[aria-checked="true"]')?.textContent?.trim()).toBe('全部');
  });

  it('shows only the first two cards on a phone', () => {
    TestBed.inject(LayoutModeService).set('phone');
    const fixture = create2026();

    expect(cards(fixture).map(card => card.label)).toEqual(['2026 年已實現', '本月（10 月）']);
  });
});
