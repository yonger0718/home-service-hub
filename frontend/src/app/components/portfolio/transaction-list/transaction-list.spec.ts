import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Subject, of } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  Broker,
  Paged,
  RealizedPnlPaged,
  RealizedPnlQuery,
  Transaction,
  TransactionQuery,
  TransactionType,
} from '../../../models/portfolio.model';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { PortfolioService } from '../../../services/portfolio.service';
import { ALLOCATION_SERIES, abbrInk, holdingAbbr } from '../dashboard/dashboard-math';
import { PortfolioTransactionListComponent } from './transaction-list';
import { monthHeading } from './transaction-math';
import { squareToken } from './transaction-rows';

interface Call<Q, R> {
  query: Q;
  response: Subject<R>;
}

let nextId = 1;
function tx(overrides: Partial<Transaction>): Transaction {
  return {
    id: nextId++,
    symbol: '2330',
    type: TransactionType.BUY,
    quantity: 100,
    price: 1000,
    fee: 0,
    tax: 0,
    trade_date: '2026-10-15',
    ...overrides,
  };
}

/** Buy 100 140 all-in, sell 99 600 net; fees 540 of 200 000 turnover = 0.27 %. */
const buy = tx({ fee: 140 });
const sell = tx({ type: TransactionType.SELL, fee: 100, tax: 300 });

function respond<R>(call: Call<unknown, R>, value: R): void {
  call.response.next(value);
  call.response.complete();
}

function realizedPage(total: string): RealizedPnlPaged {
  return { items: [], total: 0, summary: { filter_scope_total: total, filter_scope_count: 0, ytd_total: '0', ytd_count: 0 } };
}

describe('PortfolioTransactionListComponent', () => {
  let listCalls: Call<TransactionQuery, Paged<Transaction>>[];
  let summaryCalls: Call<TransactionQuery, Paged<Transaction>>[];
  let realizedCalls: Call<RealizedPnlQuery, RealizedPnlPaged>[];
  let brokers: Broker[];
  let portfolioService: Record<string, ReturnType<typeof vi.fn>>;

  beforeEach(() => {
    listCalls = [];
    summaryCalls = [];
    realizedCalls = [];
    brokers = [];
    portfolioService = {
      getSymbolNames: vi.fn(() => of({})),
      getTransactionBrokers: vi.fn(() => of(brokers)),
      getTransactions: vi.fn((query: TransactionQuery) => {
        const response = new Subject<Paged<Transaction>>();
        // The list pages by 25 in these tests; the filter-wide summary always pages by 100.
        (query.limit === 100 ? summaryCalls : listCalls).push({ query, response });
        return response;
      }),
      getRealizedPnl: vi.fn((query: RealizedPnlQuery) => {
        const response = new Subject<RealizedPnlPaged>();
        realizedCalls.push({ query, response });
        return response;
      }),
      createTransaction: vi.fn(() => of({})),
    };
    TestBed.configureTestingModule({
      providers: [provideRouter([]), { provide: PortfolioService, useValue: portfolioService }],
    });
  });

  afterEach(() => {
    TestBed.inject(LayoutModeService).set(null);
    vi.useRealTimers();
  });

  function create(): ComponentFixture<PortfolioTransactionListComponent> {
    const fixture = TestBed.createComponent(PortfolioTransactionListComponent);
    fixture.detectChanges();
    return fixture;
  }

  function el(fixture: ComponentFixture<PortfolioTransactionListComponent>): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  function cards(fixture: ComponentFixture<PortfolioTransactionListComponent>) {
    return Array.from(el(fixture).querySelectorAll('.kpi-row .kpi-card')).map(card => ({
      label: card.querySelector('.label')?.textContent?.trim(),
      value: card.querySelector('.value')?.textContent?.trim(),
      meta: card.querySelector('.meta')?.textContent?.trim(),
      card,
    }));
  }

  /** Answers the initial list page, one summary page and the realized call. */
  function settle(
    fixture: ComponentFixture<PortfolioTransactionListComponent>,
    rows: Transaction[] = [buy, sell],
    realizedTotal = '8800.00',
    total = rows.length,
  ): void {
    respond(listCalls.at(-1)!, { items: rows, total });
    respond(summaryCalls.at(-1)!, { items: rows, total: rows.length });
    respond(realizedCalls.at(-1)!, realizedPage(realizedTotal));
    fixture.detectChanges();
  }

  describe('page header', () => {
    it('offers 新增交易 as a soft app-btn and has no 記一筆 button', () => {
      const fixture = create();

      const create_ = Array.from(el(fixture).querySelectorAll('app-page-header app-btn')).find(btn => btn.textContent?.includes('新增交易'));
      expect(create_?.querySelector('.btn-soft')).not.toBeNull();
      expect(el(fixture).textContent).not.toContain('記一筆');
    });

    it('links 匯入 CSV to /portfolio/import', () => {
      const fixture = create();

      const link = Array.from(el(fixture).querySelectorAll<HTMLAnchorElement>('app-page-header a')).find(a => a.textContent?.includes('匯入 CSV'));
      expect(link?.getAttribute('href')).toBe('/portfolio/import');
    });

    it('shows the period and the server total, then the date range after a date filter', () => {
      const fixture = create();
      respond(listCalls[0], { items: [buy, sell], total: 24 });
      fixture.detectChanges();
      expect(el(fixture).querySelector('app-page-header .page-subtitle')?.textContent?.trim()).toBe('全部期間 · 共 24 筆');

      fixture.componentInstance.onDateRangeChange([new Date(2026, 0, 1), new Date(2026, 9, 22)]);
      respond(listCalls[1], { items: [buy], total: 7 });
      fixture.detectChanges();
      expect(el(fixture).querySelector('app-page-header .page-subtitle')?.textContent?.trim()).toBe('2026/01/01 – 2026/10/22 · 共 7 筆');
    });

    it('no longer carries 清除篩選 among the header actions', () => {
      const fixture = create();
      fixture.componentInstance.onSideFilterChange('SELL');
      fixture.detectChanges();

      expect(el(fixture).querySelector('app-page-header')?.textContent).not.toContain('清除篩選');
    });
  });

  describe('清除篩選', () => {
    function clearButton(fixture: ComponentFixture<PortfolioTransactionListComponent>) {
      return Array.from(el(fixture).querySelectorAll('.filter-bar button')).find(b => b.textContent?.includes('清除篩選'));
    }

    it('appears in the filter bar only while a filter is active', () => {
      const fixture = create();
      expect(clearButton(fixture)).toBeUndefined();

      fixture.componentInstance.onSideFilterChange('SELL');
      fixture.detectChanges();
      expect(clearButton(fixture)).toBeDefined();

      (clearButton(fixture) as HTMLButtonElement).click();
      fixture.detectChanges();
      expect(clearButton(fixture)).toBeUndefined();
      expect(listCalls.at(-1)!.query.side).toBeUndefined();
    });

    it('appears with a broker-only filter too', () => {
      brokers = ['TW_CATHAY', 'IB'];
      const fixture = create();

      fixture.componentInstance.onBrokerFilterChange('IB');
      fixture.detectChanges();

      expect(fixture.componentInstance.hasActiveFilters()).toBe(true);
      expect(clearButton(fixture)).toBeDefined();
    });
  });

  describe('summary cards', () => {
    it('renders the four cards in order with TWD all-in values from tradeTotals', () => {
      const fixture = create();
      settle(fixture);

      const all = cards(fixture);
      expect(all.map(c => c.label)).toEqual(['買進金額', '賣出金額', '手續費＋交易稅', '已實現損益']);
      expect(all.map(c => c.value)).toEqual(['$100,140', '$99,600', '$540', '$8,800']);
      expect(all[0].meta).toBe('1 筆');
      expect(all[1].meta).toBe('1 筆');
      expect(all[2].meta).toBe('佔成交額 0.27%');
      expect(all[3].meta).toBe('查看明細 →');
    });

    it('shows — on every card while the summary loads', () => {
      const fixture = create();

      expect(cards(fixture).map(c => c.value)).toEqual(['—', '—', '—', '—']);
    });

    it('links the 已實現損益 card to /portfolio/realized-pnl, coloured by sign', () => {
      const fixture = create();
      settle(fixture, [buy, sell], '8800.00');

      const realized = cards(fixture)[3];
      const link = realized.card.closest('a');
      expect(link?.getAttribute('href')).toBe('/portfolio/realized-pnl');
      expect(realized.card.querySelector('.value')?.classList).toContain('up');

      fixture.componentInstance.onSideFilterChange('SELL');
      settle(fixture, [sell], '-1234.40');
      const down = cards(fixture)[3];
      expect(down.value).toBe('-$1,234');
      expect(down.card.querySelector('.value')?.classList).toContain('down');
    });

    it('parses the decimal-string filter_scope_total into a number', () => {
      const fixture = create();
      settle(fixture, [buy, sell], '1500.40');

      expect(fixture.componentInstance.realized()).toBe(1500.4);
    });

    it('loads the summary with every filter and reloads it with realized on a filter change', () => {
      brokers = ['TW_CATHAY', 'IB'];
      const fixture = create();
      settle(fixture);
      expect(summaryCalls).toHaveLength(1);
      expect(realizedCalls).toHaveLength(1);

      fixture.componentInstance.onBrokerFilterChange('IB');
      fixture.componentInstance.onSideFilterChange('SELL');
      expect(summaryCalls).toHaveLength(3);
      expect(realizedCalls).toHaveLength(3);
      expect(summaryCalls[2].query).toEqual({ symbol: undefined, date_from: undefined, date_to: undefined, side: 'SELL', broker: 'IB', sort: 'trade_date:desc', limit: 100, offset: 0 });
      expect(realizedCalls[2].query).toEqual({ symbol: undefined, date_from: undefined, date_to: undefined, broker: 'IB', limit: 1 });

      respond(listCalls.at(-1)!, { items: [sell], total: 1 });
      respond(summaryCalls[2], { items: [sell], total: 1 });
      respond(realizedCalls[2], realizedPage('10.00'));
      fixture.detectChanges();
      expect(cards(fixture).map(c => c.value)).toEqual(['$0', '$99,600', '$400', '$10']);
    });

    it('reloads the summary after the debounced search, not on every keystroke', () => {
      vi.useFakeTimers();
      const fixture = create();

      fixture.componentInstance.onSearchInput('233');
      fixture.componentInstance.onSearchInput('2330');
      expect(summaryCalls).toHaveLength(1);

      vi.advanceTimersByTime(300);
      expect(summaryCalls).toHaveLength(2);
      expect(summaryCalls[1].query.symbol).toBe('2330');
      expect(realizedCalls[1].query.symbol).toBe('2330');
    });

    it('does not reload the summary on a page change or a sort change', () => {
      const fixture = create();
      settle(fixture, [buy, sell], '8800.00', 60);

      fixture.componentInstance.onPageChange({ first: 25, rows: 25 });
      fixture.componentInstance.onSortChange('symbol:asc');

      expect(listCalls).toHaveLength(3);
      expect(summaryCalls).toHaveLength(1);
      expect(realizedCalls).toHaveLength(1);
    });

    it('reloads the summary after a trade is saved', () => {
      const fixture = create();
      settle(fixture);

      fixture.componentInstance.openNew();
      fixture.componentInstance.saveTransaction();

      expect(summaryCalls).toHaveLength(2);
      expect(realizedCalls).toHaveLength(2);
    });

    it('drops an older summary response that lands after a newer one', () => {
      const fixture = create();
      fixture.componentInstance.onSideFilterChange('SELL');

      respond(summaryCalls[1], { items: [sell], total: 1 });
      respond(realizedCalls[1], realizedPage('10.00'));
      respond(summaryCalls[0], { items: [buy, sell], total: 2 });
      respond(realizedCalls[0], realizedPage('8800.00'));
      fixture.detectChanges();

      expect(cards(fixture).map(c => c.value)).toEqual(['$0', '$99,600', '$400', '$10']);
    });

    it('hides the cards when the summary load fails and keeps the list', () => {
      const fixture = create();
      respond(listCalls[0], { items: [buy, sell], total: 2 });
      summaryCalls[0].response.error(new Error('boom'));
      respond(realizedCalls[0], realizedPage('8800.00'));
      fixture.detectChanges();

      expect(el(fixture).querySelector('.kpi-row')).toBeNull();
      expect(fixture.componentInstance.timelineRows()).toHaveLength(2);
      expect(el(fixture).textContent).not.toContain('無股票交易');
    });

    it('keeps the three trade cards and shows — for 已實現損益 when only the realized load fails', () => {
      const fixture = create();
      respond(listCalls[0], { items: [buy, sell], total: 2 });
      respond(summaryCalls[0], { items: [buy, sell], total: 2 });
      realizedCalls[0].response.error({ status: 422 });
      fixture.detectChanges();

      const all = cards(fixture);
      expect(all.map(c => c.value)).toEqual(['$100,140', '$99,600', '$540', '—']);
      expect(all[3].card.querySelector('.value')?.classList).not.toContain('up');
      expect(all[3].card.querySelector('.value')?.classList).not.toContain('down');
    });

    const usdWithoutFx = () => tx({ market: 'US', currency: 'USD', price: 10, quantity: 1 });

    it('notes foreign rows without an fx rate once under the cards, also on a phone', () => {
      TestBed.inject(LayoutModeService).set('phone');
      const fixture = create();
      settle(fixture, [buy, sell, usdWithoutFx()]);

      expect(cards(fixture).map(c => c.meta)).toEqual(['1 筆', '1 筆']);
      expect(el(fixture).querySelector('.summary-note')?.textContent?.trim()).toBe('不含外幣 1 筆');
    });

    it('shows — as the fee share when there is no turnover', () => {
      const fixture = create();
      settle(fixture, []);

      expect(cards(fixture)[2].meta).toBe('佔成交額 —');
    });

    it('notes the 5,000-row cap when the summary paging stopped early', () => {
      const fixture = create();
      for (let page = 0; page < 50; page++) {
        respond(summaryCalls[page], { items: Array.from({ length: 100 }, () => buy), total: 6000 });
      }
      fixture.detectChanges();

      expect(summaryCalls).toHaveLength(50);
      expect(fixture.componentInstance.summaryCapped()).toBe(true);
      expect(el(fixture).querySelector('.summary-note')?.textContent?.trim()).toBe('⋯ 僅載入前 5,000 筆');
    });

    it('combines the fx note and the cap note on one line', () => {
      const fixture = create();
      for (let page = 0; page < 50; page++) {
        const items = Array.from({ length: 100 }, () => buy);
        if (page === 0) items[0] = usdWithoutFx();
        respond(summaryCalls[page], { items, total: 6000 });
      }
      fixture.detectChanges();

      expect(el(fixture).querySelectorAll('.summary-note')).toHaveLength(1);
      expect(el(fixture).querySelector('.summary-note')?.textContent?.trim()).toBe('不含外幣 1 筆 · ⋯ 僅載入前 5,000 筆');
    });

    it('shows a realized value that rounds to zero as $0 without a trend colour', () => {
      const fixture = create();
      settle(fixture, [buy, sell], '-0.40');

      const realized = cards(fixture)[3];
      expect(realized.value).toBe('$0');
      expect(realized.card.querySelector('.value')?.classList).not.toContain('up');
      expect(realized.card.querySelector('.value')?.classList).not.toContain('down');
    });

    it('cancels a superseded summary load instead of paging on', () => {
      const fixture = create();
      const first = summaryCalls[0].response;
      const firstRealized = realizedCalls[0].response;

      fixture.componentInstance.onSideFilterChange('SELL');

      expect(first.observed).toBe(false);
      expect(firstRealized.observed).toBe(false);
      expect(summaryCalls[1].response.observed).toBe(true);
    });

    it('cancels the summary load on destroy', () => {
      const fixture = create();
      fixture.destroy();

      expect(summaryCalls[0].response.observed).toBe(false);
      expect(realizedCalls[0].response.observed).toBe(false);
    });

    it('shows only the 買進金額 and 賣出金額 cards on a phone', () => {
      TestBed.inject(LayoutModeService).set('phone');
      const fixture = create();
      settle(fixture);

      expect(cards(fixture).map(c => c.label)).toEqual(['買進金額', '賣出金額']);
    });
  });

  describe('rows', () => {
    const twBuy = tx({ symbol: '00878', name: '國泰永續高股息', quantity: 5000, price: 22.05, fee: 157, broker: 'TW_CATHAY', trade_date: '2026-10-15' });
    const twSell = tx({ symbol: '2317', name: '鴻海', type: TransactionType.SELL, quantity: 1000, price: 175, fee: 249, tax: 525, broker: 'TW_SINOPAC', trade_date: '2026-10-02' });
    const usBuy = tx({ symbol: 'AAPL', name: 'Apple', market: 'US', currency: 'USD', fx_rate_to_twd: 30.7, quantity: 10, price: 238.4, fee: 1, broker: 'IB', trade_date: '2026-09-18' });
    const usNoFx = tx({ symbol: 'MSFT', name: 'Microsoft', market: 'US', currency: 'USD', quantity: 2, price: 400, fee: 1, broker: 'IB', trade_date: '2026-09-10' });
    const dayBuy = tx({ symbol: '2330', name: '台積電', quantity: 200, price: 1105, fee: 315, is_day_trade: true, trade_date: '2026-08-21' });
    const daySell = tx({ symbol: '2330', name: '台積電', type: TransactionType.SELL, quantity: 200, price: 1120, fee: 319, tax: 336, is_day_trade: true, trade_date: '2026-08-21' });
    const all = [twBuy, twSell, usBuy, usNoFx, dayBuy, daySell];

    function card(fixture: ComponentFixture<PortfolioTransactionListComponent>, t: Transaction): HTMLElement {
      return el(fixture).querySelector(`[data-row-id="${t.id}"]`) as HTMLElement;
    }

    function text(root: Element | null, selector: string): string | undefined {
      return root?.querySelector(selector)?.textContent?.trim();
    }

    it('groups rows by month with the month label and the page rows’ 買 / 賣 totals', () => {
      const fixture = create();
      settle(fixture, all);

      const headings = Array.from(el(fixture).querySelectorAll('.tl-month'));
      expect(headings.map(h => text(h, '.tl-month-label'))).toEqual(['2026 年 10 月', '2026 年 9 月', '2026 年 8 月']);
      expect(headings.map(h => text(h, '.tl-month-sum'))).toEqual([
        monthHeading('2026-10', all),
        monthHeading('2026-09', all),
        monthHeading('2026-08', all),
      ]);
      expect(text(headings[0], '.tl-month-sum')).toBe('買 110,407 · 賣 174,226');
    });

    it('totals each month over the whole filter once the summary has loaded, the page before that', () => {
      const otherPageBuy = tx({ symbol: '0050', name: '元大台灣50', quantity: 2000, price: 68.9, fee: 196, trade_date: '2026-10-01' });
      const fixture = create();
      respond(listCalls[0], { items: [twBuy, twSell], total: 3 });
      fixture.detectChanges();

      const sum = () => text(el(fixture), '.tl-month-sum');
      expect(sum()).toBe('買 110,407 · 賣 174,226');

      respond(summaryCalls[0], { items: [twBuy, twSell, otherPageBuy], total: 3 });
      respond(realizedCalls[0], realizedPage('0'));
      fixture.detectChanges();

      expect(el(fixture).querySelectorAll('.tl-card').length).toBe(2);
      expect(sum()).toBe('買 248,403 · 賣 174,226');
    });

    it('falls back to the page rows when the summary load fails', () => {
      const fixture = create();
      respond(listCalls[0], { items: [twBuy], total: 2 });
      summaryCalls[0].response.error(new Error('boom'));
      respond(realizedCalls[0], realizedPage('0'));
      fixture.detectChanges();

      expect(text(el(fixture), '.tl-month-sum')).toBe('買 110,407');
    });

    it('shows the day and weekday on every row', () => {
      const fixture = create();
      settle(fixture, all);

      const days = Array.from(el(fixture).querySelectorAll('.tl-item')).map(item => `${text(item, '.tl-date .d')} ${text(item, '.tl-date .m')}`);
      expect(days).toEqual(['10/15 週四', '10/02 週五', '09/18 週五', '09/10 週四', '08/21 週五', '08/21 週五']);
    });

    it('renders the square from holdingAbbr with a token stable per symbol and its ink', () => {
      const fixture = create();
      settle(fixture, all);

      const square = (t: Transaction) => card(fixture, t).querySelector('.tl-square') as HTMLElement;
      expect(square(twBuy).textContent?.trim()).toBe(holdingAbbr('00878', '國泰永續高股息'));
      expect(square(twSell).textContent?.trim()).toBe('17');
      expect(square(usBuy).textContent?.trim()).toBe('A');
      for (const t of all) {
        const token = squareToken(t.symbol);
        expect(ALLOCATION_SERIES).toContain(token);
        expect(square(t).style.background).toBe(`var(${token})`);
        expect(square(t).classList).toContain(abbrInk(token) === 'dark' ? 'ink-dark' : 'ink-light');
      }
      expect(square(dayBuy).style.background).toBe(square(daySell).style.background);
      expect(squareToken('2330')).toBe(squareToken('2330'));
    });

    it('shows name + symbol, the 買 / 賣 SideTag and 當沖 only on day trades', () => {
      const fixture = create();
      settle(fixture, all);

      expect(text(card(fixture, twBuy), '.tl-primary')).toBe('國泰永續高股息 00878');
      const side = (t: Transaction) => card(fixture, t).querySelector('.tl-name app-side-tag .side-tag') as HTMLElement;
      expect(side(twBuy).textContent?.trim()).toBe('買');
      expect(side(twBuy).classList).toContain('buy');
      expect(side(twSell).textContent?.trim()).toBe('賣');
      expect(side(twSell).classList).toContain('sell');
      const tags = (t: Transaction) => Array.from(card(fixture, t).querySelectorAll('app-tag')).map(tag => tag.textContent?.trim());
      expect(tags(dayBuy)).toEqual(['當沖']);
      expect(tags(daySell)).toEqual(['當沖']);
      expect(tags(twBuy)).toEqual([]);
      expect(tags(usBuy)).toEqual([]);
    });

    it('shows quantity @ price with the fee line beneath and the broker', () => {
      const fixture = create();
      settle(fixture, all);

      expect(text(card(fixture, twBuy), '.tl-qty-main')).toBe('5,000 股 @ 22.05');
      expect(text(card(fixture, twBuy), '.tl-sub')).toBe('手續費 157');
      expect(text(card(fixture, twSell), '.tl-sub')).toBe('手續費 249 · 交易稅 525');
      expect(text(card(fixture, usBuy), '.tl-qty-main')).toBe('10 股 @ 238.40 USD');
      expect(text(card(fixture, usBuy), '.tl-sub')).toBe('手續費 1.00 USD · 匯率 30.70');
      expect(text(card(fixture, dayBuy), '.tl-qty-main')).toBe('200 股 @ 1,105.00');
      expect(text(card(fixture, twBuy), '.tl-broker')).toBe('國泰');
      expect(text(card(fixture, twSell), '.tl-broker')).toBe('永豐');
      expect(text(card(fixture, usBuy), '.tl-broker')).toBe('IB');
    });

    it('shows TWD all-in amounts with − / + and the 總付出 / 淨收入 or native note', () => {
      const fixture = create();
      settle(fixture, all);

      const amount = (t: Transaction) => [text(card(fixture, t), '.tl-amt'), text(card(fixture, t), '.tl-amt-note')];
      expect(amount(twBuy)).toEqual(['−110,407', '總付出']);
      expect(amount(twSell)).toEqual(['+174,226', '淨收入']);
      expect(amount(usBuy)).toEqual(['−73,220', '2,385.00 USD']);
      expect(amount(usNoFx)).toEqual(['−801.00 USD', '—']);
    });

    it('collapses rows on a phone: square, name + SideTag, quantity · broker, the date beneath the amount', () => {
      TestBed.inject(LayoutModeService).set('phone');
      const fixture = create();
      settle(fixture, all);

      expect(el(fixture).querySelector('.timeline')?.classList).toContain('tl-compact');
      const row = card(fixture, twBuy);
      expect(text(row, '.tl-square')).toBe('78');
      expect(text(row, '.tl-name app-side-tag')).toBe('買');
      expect(text(row, '.tl-meta')).toBe('5,000 股 @ 22.05 · 國泰');
      expect(text(row, '.tl-amt')).toBe('−110,407');
      expect(text(row, '.tl-amt-date')).toBe('10/15');
      expect(row.querySelector('.tl-sub')).toBeNull();
      expect(row.querySelector('.tl-broker')).toBeNull();
      expect(el(fixture).querySelector('.tl-date')).toBeNull();
    });

    it('opens the row menu from the row action button', () => {
      const fixture = create();
      settle(fixture, all);
      const toggle = vi.spyOn(fixture.componentInstance.menu, 'toggle').mockImplementation(() => undefined);

      (card(fixture, twSell).querySelector('.tl-action') as HTMLButtonElement).click();

      expect(toggle).toHaveBeenCalledTimes(1);
      expect(fixture.componentInstance.menuItems.map(item => item.label)).toEqual(['編輯', undefined, '刪除']);
      fixture.componentInstance.menuItems[0].command?.({});
      expect(fixture.componentInstance.isEdit()).toBe(true);
      expect(fixture.componentInstance.newTransaction.id).toBe(twSell.id);
    });
  });
});
