import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { MessageService } from 'primeng/api';
import { Paginator } from 'primeng/paginator';
import { of, Subject, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { PortfolioService } from '../../../services/portfolio.service';
import { PortfolioDividendListComponent } from './dividend-list';
import { Dividend, DividendQuery, Paged } from '../../../models/portfolio.model';
import { abbrInk, holdingAbbr } from '../dashboard/dashboard-math';
import { squareToken } from '../square';

const pending: Dividend = { id: 1, symbol: '9802', amount: 3090, ex_dividend_date: '2026-09-10',
  receipt_status: 'pending', revision: 3, cash_dividend_per_share: 3.1, payment_date: null };
describe('Dividend receipt confirmation', () => {
  const service = { getDividends: vi.fn(), getSymbolNames: vi.fn(), getUpcomingExDividends: vi.fn(), getAccounts: vi.fn(), confirmDividendReceipt: vi.fn() };
  beforeEach(() => {
    vi.clearAllMocks();
    service.getDividends.mockReturnValue(of({ items: [pending, { ...pending, id: 2, receipt_status: 'legacy_unknown' }], total: 2 }));
    service.getSymbolNames.mockReturnValue(of({}));
    service.getUpcomingExDividends.mockReturnValue(of([]));
    service.getAccounts.mockReturnValue(of({ items: [{ id: 7, nickname: 'Synthetic TWD', currency: 'TWD', is_active: true }] }));
    TestBed.configureTestingModule({ imports: [PortfolioDividendListComponent], providers: [provideRouter([]), { provide: PortfolioService, useValue: service }] });
  });
  it('shows persistent pending and unknown legacy states without counting paid income', () => {
    const fixture = TestBed.createComponent(PortfolioDividendListComponent);
    fixture.detectChanges();
    const tags = Array.from((fixture.nativeElement as HTMLElement).querySelectorAll('.div-row app-tag')).map(t => t.textContent?.trim());
    expect(tags).toEqual(['待收', '舊資料 · 未核實']);
    expect(fixture.nativeElement.textContent).not.toContain('未知');
    expect(fixture.componentInstance.summary()?.confirmed).toBe(0);
    fixture.destroy();
  });
  it('renders each mixed receipt fixture once with accurate filter-wide totals and pending-only action', () => {
    const rows: Dividend[] = [
      { ...pending, id: 101, symbol: 'SYNLEGACY', amount: 100, receipt_status: 'legacy_unknown' },
      { ...pending, id: 102, symbol: 'SYNPENDING', amount: 200, payment_date: '2026-10-01' },
      { ...pending, id: 103, symbol: 'SYNCONFIRMED', amount: 300, receipt_status: 'confirmed', receipt_date: '2026-10-02' },
      { ...pending, id: 104, symbol: 'SYNUNRESOLVED', amount: 400, receipt_status: 'unresolved', review_reason: 'Synthetic review reason' },
    ];
    service.getDividends.mockReturnValue(of({ items: rows, total: 70 }));
    const fixture = TestBed.createComponent(PortfolioDividendListComponent);
    fixture.detectChanges();
    const root: HTMLElement = fixture.nativeElement;
    for (const row of rows) {
      // Count visible text, so a duplicate presentation outside the list also fails.
      expect((root.textContent ?? '').split(row.symbol).length - 1).toBe(1);
      expect(root.querySelectorAll('[data-row-id="' + row.id + '"]')).toHaveLength(1);
    }
    expect(root.querySelector('[data-row-id="102"]')?.textContent).toContain('預定 10/01');
    expect(root.querySelector('[data-row-id="103"]')?.textContent).toContain('收款 10/02');
    expect(root.querySelector('[data-row-id="104"] .div-notes')?.textContent).toContain('Synthetic review reason');
    expect(root.textContent).not.toContain('未知');
    expect(root.textContent).not.toContain('未確認');
    expect(root.textContent).not.toContain('符合篩選紀錄筆數');
    expect(root.querySelector('.page-subtitle')?.textContent?.trim()).toBe('全部期間 · 共 70 筆');
    const values = Array.from(root.querySelectorAll('.kpi-card .value')).map(el => el.textContent?.trim());
    expect(values).toEqual(['$300', '$200', '$100', '$400']);
    const buttons = Array.from(root.querySelectorAll('button')).filter(b => b.textContent?.trim() === '確認收款');
    expect(buttons).toHaveLength(1);
    expect(buttons[0].closest('[data-row-id]')?.getAttribute('data-row-id')).toBe('102');
    buttons[0].click();
    expect(fixture.componentInstance.openReceiptId()).toBe(102);
    fixture.destroy();
  });
  it('preserves the full ex-date and a payment date in another year for rows with the same day in different years', () => {
    service.getDividends.mockReturnValue(of({ items: [
      { ...pending, id: 201, ex_dividend_date: '2024-03-16', payment_date: '2025-04-10' },
      { ...pending, id: 202, ex_dividend_date: '2025-03-16', payment_date: '2025-04-10' },
    ], total: 2 }));
    const fixture = TestBed.createComponent(PortfolioDividendListComponent);
    fixture.detectChanges();
    const root: HTMLElement = fixture.nativeElement;
    expect(root.querySelector('[data-row-id="201"] .c-ex')?.textContent?.trim()).toBe('2024/03/16');
    expect(root.querySelector('[data-row-id="202"] .c-ex')?.textContent?.trim()).toBe('2025/03/16');
    expect(root.querySelector('[data-row-id="201"] .c-pay')?.textContent).toContain('預定 2025/04/10');
    expect(root.querySelector('[data-row-id="202"] .c-pay')?.textContent).toContain('預定 04/10');
    fixture.destroy();
  });
  it('preserves meaningful four-decimal per-share precision, including without quantity', () => {
    service.getDividends.mockReturnValue(of({ items: [
      { ...pending, id: 201, cash_dividend_per_share: 0.0420, quantity_at_record_date: 1000 },
      { ...pending, id: 202, cash_dividend_per_share: 0.0421, quantity_at_record_date: 1000 },
      { ...pending, id: 203, cash_dividend_per_share: 0.0001, quantity_at_record_date: null },
    ], total: 3 }));
    const fixture = TestBed.createComponent(PortfolioDividendListComponent);
    fixture.detectChanges();
    const root: HTMLElement = fixture.nativeElement;
    const qty = (id: number) => root.querySelector(`[data-row-id="${id}"] .c-qty div`)?.textContent?.trim();
    expect(qty(201)).toBe('0.042 × 1,000');
    expect(qty(202)).toBe('0.0421 × 1,000');
    expect(qty(203)).toBe('0.0001');
    fixture.destroy();
  });
  it('preserves query filters, sorting and pagination', () => {
    const fixture = TestBed.createComponent(PortfolioDividendListComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.onSourceChange('csv');
    component.onDateRangeChange([new Date(2026, 0, 1), new Date(2026, 11, 31)]);
    component.onSortChange('symbol:asc');
    component.onPageChange({ first: 25, rows: 25 });
    expect(service.getDividends).toHaveBeenLastCalledWith({
      offset: 25, limit: 25, sort: 'symbol:asc', source: 'csv',
      date_from: '2026-01-01', date_to: '2026-12-31',
    });
    component.onSearchInput('synthetic');
    expect(component.query()).toMatchObject({ symbol: 'SYNTHETIC', offset: 0 });
    component.clearFilters();
    expect(service.getDividends).toHaveBeenLastCalledWith({ offset: 0, limit: 25, sort: 'ex_dividend_date:desc' });
    fixture.destroy();
  });
  it('keeps loading, empty and error feedback visible', () => {
    const response = new Subject<{ items: Dividend[]; total: number }>();
    service.getDividends.mockReturnValue(response);
    const fixture = TestBed.createComponent(PortfolioDividendListComponent);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('查詢中');
    expect(fixture.nativeElement.textContent).not.toContain('無股利紀錄');
    response.next({ items: [], total: 0 });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('無股利紀錄');
    service.getDividends.mockReturnValue(throwError(() => new Error('Synthetic failure')));
    fixture.componentInstance.fetch();
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('查詢失敗');
    fixture.destroy();
  });
  it('requires explicit date and account, sends revision, and guards duplicate submits', () => {
    const response = new Subject<Dividend>();
    service.confirmDividendReceipt.mockReturnValue(response);
    const fixture = TestBed.createComponent(PortfolioDividendListComponent);
    fixture.detectChanges();
    const root: HTMLElement = fixture.nativeElement;
    root.querySelector<HTMLButtonElement>('[data-row-id="1"] .c-actions app-btn button')!.click();
    fixture.detectChanges();
    const submit = () => Array.from(root.querySelectorAll<HTMLButtonElement>('.receipt-form button')).find(b => b.textContent?.trim() === '確認已收到並入帳')!;
    expect(submit().disabled).toBe(true);
    fixture.componentInstance.confirmReceipt();
    expect(service.confirmDividendReceipt).not.toHaveBeenCalled();

    const date = root.querySelector<HTMLInputElement>('.receipt-form input[aria-label="實際收款日"]')!;
    date.value = '2026-09-11';
    date.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    expect(submit().disabled).toBe(true);
    const account = root.querySelector<HTMLSelectElement>('.receipt-form select[aria-label="收款帳戶"]')!;
    account.value = '7';
    account.dispatchEvent(new Event('change'));
    fixture.detectChanges();
    expect(submit().disabled).toBe(false);

    submit().click(); submit().click();
    expect(service.confirmDividendReceipt).toHaveBeenCalledTimes(1);
    expect(service.confirmDividendReceipt).toHaveBeenCalledWith(1, { receipt_date: '2026-09-11', account_id: 7, revision: 3 });
    fixture.detectChanges();
    expect(submit().disabled).toBe(true);
    response.next({ ...pending, receipt_status: 'confirmed' }); response.complete();
    expect(fixture.componentInstance.receiptBusy()).toBe(false);
    expect(fixture.componentInstance.openReceiptId()).toBeNull();
    fixture.destroy();
  });
  it('does not offer confirmation for legacy or confirmed rows', () => {
    service.getDividends.mockReturnValue(of({ items: [
      { ...pending, id: 2, receipt_status: 'legacy_unknown' },
      { ...pending, id: 3, receipt_status: 'confirmed', receipt_date: '2026-09-12' },
    ], total: 2 }));
    const fixture = TestBed.createComponent(PortfolioDividendListComponent);
    fixture.detectChanges();
    const root: HTMLElement = fixture.nativeElement;
    expect(Array.from(root.querySelectorAll('button')).filter(b => b.textContent?.trim() === '確認收款')).toHaveLength(0);
    fixture.componentInstance.openReceipt({ ...pending, receipt_status: 'legacy_unknown' });
    fixture.componentInstance.openReceipt({ ...pending, receipt_status: 'confirmed' });
    fixture.detectChanges();
    expect(fixture.componentInstance.openReceiptId()).toBeNull();
    expect(root.querySelector('.receipt-form')).toBeNull();
    // Only the one load on init.
    expect(service.getAccounts).toHaveBeenCalledTimes(1);
    fixture.destroy();
  });
});

/** An element's child texts joined by one space (the template drops whitespace between elements), or its own text. */
function lines(node: Element | null): string {
  if (!node) return '';
  const parts = node.children.length ? Array.from(node.children).map(child => child.textContent ?? '') : [node.textContent ?? ''];
  return parts.map(part => part.replace(/\s+/g, ' ').trim()).filter(Boolean).join(' ');
}

interface Call {
  query: DividendQuery;
  response: Subject<Paged<Dividend>>;
}

function respond(call: Call, value: Paged<Dividend>): void {
  call.response.next(value);
  call.response.complete();
}

describe('Dividend filter-wide summary', () => {
  let listCalls: Call[];
  let summaryCalls: Call[];
  let inFetch: boolean;
  let service: Record<string, ReturnType<typeof vi.fn>>;

  const confirmed: Dividend = { id: 11, symbol: '00878', amount: 37578, ex_dividend_date: '2026-08-17', receipt_status: 'confirmed', receipt_date: '2026-09-10' };
  const legacy: Dividend = { id: 12, symbol: '2330', amount: 21342, ex_dividend_date: '2026-06-12', receipt_status: 'legacy_unknown' };
  const due: Dividend = { id: 13, symbol: '0056', amount: 14600, ex_dividend_date: '2026-10-17', receipt_status: 'pending', revision: 2 };

  beforeEach(() => {
    listCalls = [];
    summaryCalls = [];
    inFetch = false;
    service = {
      getSymbolNames: vi.fn(() => of({ '00878': '國泰永續高股息' })),
      getUpcomingExDividends: vi.fn(() => of([])),
      getAccounts: vi.fn(() => of({ items: [{ id: 7, nickname: '元大交割戶', currency: 'TWD', is_active: true }] })),
      getDividends: vi.fn((query: DividendQuery) => {
        const response = new Subject<Paged<Dividend>>();
        // The list page is the one request `fetch()` makes synchronously; everything else is the filter-wide set,
        // so a list page of 100 is still told apart from the set's pages of 100.
        (inFetch ? listCalls : summaryCalls).push({ query, response });
        return response;
      }),
      confirmDividendReceipt: vi.fn(),
      createDividend: vi.fn(() => of({})),
    };
    TestBed.configureTestingModule({ imports: [PortfolioDividendListComponent], providers: [provideRouter([]), { provide: PortfolioService, useValue: service }] });
  });

  afterEach(() => {
    vi.useRealTimers();
    TestBed.inject(LayoutModeService).set(null);
  });

  function create(): ComponentFixture<PortfolioDividendListComponent> {
    const fixture = TestBed.createComponent(PortfolioDividendListComponent);
    const component = fixture.componentInstance;
    const fetch = component.fetch.bind(component);
    vi.spyOn(component, 'fetch').mockImplementation(() => {
      inFetch = true;
      try {
        fetch();
      } finally {
        inFetch = false;
      }
    });
    fixture.detectChanges();
    return fixture;
  }

  function el(fixture: ComponentFixture<PortfolioDividendListComponent>): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  function cards(fixture: ComponentFixture<PortfolioDividendListComponent>): HTMLElement[] {
    return Array.from(el(fixture).querySelectorAll<HTMLElement>('.kpi-card'));
  }

  function rowIds(fixture: ComponentFixture<PortfolioDividendListComponent>): number[] {
    return Array.from(el(fixture).querySelectorAll('[data-row-id]')).map(row => Number(row.getAttribute('data-row-id')));
  }

  function paginator(fixture: ComponentFixture<PortfolioDividendListComponent>): Paginator | null {
    return fixture.debugElement.query(By.directive(Paginator))?.componentInstance ?? null;
  }

  function selectStatus(fixture: ComponentFixture<PortfolioDividendListComponent>, label: string): void {
    const button = Array.from(el(fixture).querySelectorAll<HTMLButtonElement>('app-seg-toggle button')).find(b => b.textContent?.trim() === label);
    button!.click();
    fixture.detectChanges();
  }

  /** `count` pending rows with ids from `firstId`, for client paging. */
  function pendingRows(count: number, firstId = 500): Dividend[] {
    return Array.from({ length: count }, (_, i) => ({ ...due, id: firstId + i }));
  }

  it('loads every row of the active filter by 100 and totals it, not the list page', () => {
    const fixture = create();
    expect(summaryCalls).toHaveLength(1);
    expect(summaryCalls[0].query).toEqual({ sort: 'ex_dividend_date:desc', limit: 100, offset: 0 });
    respond(listCalls[0], { items: [confirmed], total: 3 });
    respond(summaryCalls[0], { items: [confirmed, legacy, due], total: 3 });
    fixture.detectChanges();

    const summary = fixture.componentInstance.summary();
    expect(summary).toMatchObject({ confirmed: 37578, pending: 14600, legacy: 21342, recorded: 58920, foreignWithoutFx: 0 });
    expect(fixture.componentInstance.summaryRows()).toEqual([confirmed, legacy, due]);
    const values = cards(fixture).map(card => card.querySelector('.value')?.textContent?.trim());
    expect(values).toEqual(['$37,578', '$14,600', '$21,342', '$58,920']);
    fixture.destroy();
  });

  it('shows — in the cards while the filter-wide set loads', () => {
    const fixture = create();
    respond(listCalls[0], { items: [confirmed], total: 3 });
    fixture.detectChanges();

    expect(fixture.componentInstance.summary()).toBeNull();
    for (const card of cards(fixture)) {
      expect(card.querySelector('.value')?.textContent?.trim()).toBe('—');
      expect(card.querySelector('.meta')?.textContent?.trim()).toBe('—');
    }
    fixture.destroy();
  });

  it('reloads the filter-wide set once per filter change, with the filter', () => {
    const fixture = create();
    const component = fixture.componentInstance;

    component.onSourceChange('csv');
    expect(summaryCalls).toHaveLength(2);
    expect(summaryCalls[1].query).toEqual({ source: 'csv', sort: 'ex_dividend_date:desc', limit: 100, offset: 0 });

    component.onDateRangeChange([new Date(2026, 0, 1), new Date(2026, 11, 31)]);
    expect(summaryCalls).toHaveLength(3);
    expect(summaryCalls[2].query).toEqual({ source: 'csv', date_from: '2026-01-01', date_to: '2026-12-31', sort: 'ex_dividend_date:desc', limit: 100, offset: 0 });
    fixture.destroy();
  });

  it('reloads the filter-wide set after the debounced search, not on every keystroke', () => {
    vi.useFakeTimers();
    const fixture = create();

    fixture.componentInstance.onSearchInput('005');
    fixture.componentInstance.onSearchInput('0056');
    expect(summaryCalls).toHaveLength(1);

    vi.advanceTimersByTime(300);
    expect(summaryCalls).toHaveLength(2);
    expect(summaryCalls[1].query.symbol).toBe('0056');
    fixture.destroy();
  });

  it('reloads the filter-wide set with the new sort on a sort change', () => {
    const fixture = create();

    fixture.componentInstance.onSortChange('symbol:asc');

    expect(summaryCalls).toHaveLength(2);
    expect(summaryCalls[1].query).toEqual({ sort: 'symbol:asc', limit: 100, offset: 0 });
    fixture.destroy();
  });

  it('does not reload the filter-wide set on a page change', () => {
    const fixture = create();
    respond(listCalls[0], { items: [confirmed], total: 60 });
    respond(summaryCalls[0], { items: [confirmed, legacy, due], total: 3 });

    fixture.componentInstance.onPageChange({ first: 25, rows: 25 });
    fixture.componentInstance.onPageChange({ first: 0, rows: 50 });

    expect(listCalls).toHaveLength(3);
    expect(service['getDividends']).toHaveBeenCalledTimes(4);
    expect(summaryCalls).toHaveLength(1);
    fixture.destroy();
  });

  it('drops an older filter-wide response that lands after a newer one', () => {
    const fixture = create();
    fixture.componentInstance.onSourceChange('csv');
    expect(summaryCalls[0].response.observed).toBe(false);

    respond(summaryCalls[1], { items: [due], total: 1 });
    respond(summaryCalls[0], { items: [confirmed, legacy, due], total: 3 });
    fixture.detectChanges();

    expect(fixture.componentInstance.summary()).toMatchObject({ confirmed: 0, pending: 14600, legacy: 0, recorded: 0 });
    expect(fixture.componentInstance.summaryRows()).toEqual([due]);
    fixture.destroy();
  });

  it('hides the cards when the filter-wide load fails, keeps the list page and raises no toast', () => {
    const fixture = create();
    const toast = vi.spyOn(fixture.debugElement.injector.get(MessageService), 'add');
    respond(listCalls[0], { items: [confirmed], total: 3 });
    summaryCalls[0].response.error(new Error('Synthetic failure'));
    fixture.detectChanges();

    expect(fixture.componentInstance.summaryFailed()).toBe(true);
    expect(fixture.componentInstance.summary()).toBeNull();
    expect(fixture.componentInstance.summaryRows()).toBeNull();
    expect(el(fixture).querySelector('.kpi-row')).toBeNull();
    expect(cards(fixture)).toHaveLength(0);
    expect(el(fixture).querySelector('[data-row-id="11"]')).not.toBeNull();
    expect(toast).not.toHaveBeenCalled();
    fixture.destroy();
  });

  it('notes the 5,000-row cap and foreign rows without fx', () => {
    const fixture = create();
    respond(listCalls[0], { items: [confirmed], total: 6000 });
    for (let page = 0; page < 50; page++) {
      const row: Dividend = page === 0
        ? { ...due, id: 1000, currency: 'USD', fx_rate_to_twd: null }
        : { ...confirmed, id: 1000 + page };
      respond(summaryCalls[page], { items: [row], total: 6000 });
    }
    fixture.detectChanges();

    expect(summaryCalls).toHaveLength(50);
    expect(fixture.componentInstance.summaryCapped()).toBe(true);
    expect(el(fixture).querySelector('.summary-note')?.textContent?.trim()).toBe('不含外幣 1 筆 · ⋯ 僅載入前 5,000 筆');
    fixture.destroy();
  });

  it('reloads the filter-wide set after a receipt confirmation, on success and on error', () => {
    const fixture = create();
    const component = fixture.componentInstance;
    respond(listCalls[0], { items: [due], total: 1 });
    service['confirmDividendReceipt'].mockReturnValueOnce(of({ ...due, receipt_status: 'confirmed' }))
      .mockReturnValueOnce(throwError(() => ({ error: { detail: 'Synthetic stale revision' } })));

    component.openReceipt(due);
    component.receiptDate.set('2026-11-18'); component.receiptAccountId.set(7);
    component.confirmReceipt();
    expect(summaryCalls).toHaveLength(2);

    component.openReceipt(due);
    component.receiptDate.set('2026-11-18'); component.receiptAccountId.set(7);
    component.confirmReceipt();
    expect(summaryCalls).toHaveLength(3);
    fixture.destroy();
  });

  it('reloads the filter-wide set after a dividend is saved', () => {
    const fixture = create();

    fixture.componentInstance.openNew();
    fixture.componentInstance.saveDividend();

    expect(summaryCalls).toHaveLength(2);
    fixture.destroy();
  });

  it('cancels a running filter-wide load on destroy', () => {
    const fixture = create();
    respond(listCalls[0], { items: [confirmed], total: 3 });
    fixture.destroy();

    expect(summaryCalls[0].response.observed).toBe(false);
  });

  const NOTE = '有效待收股利會納入「預估含息損益」，但不計入可用現金；待釐清的金額不納入預估。舊資料的收款未經核實。';

  describe('cards, note and header', () => {
    it('renders the four cards in order with values, footnotes, colours and the dashed swatch', () => {
      const fixture = create();
      respond(listCalls[0], { items: [confirmed], total: 3 });
      respond(summaryCalls[0], { items: [confirmed, legacy, due], total: 3 });
      fixture.detectChanges();

      const all = cards(fixture);
      expect(all.map(card => card.querySelector('.label')?.textContent?.trim())).toEqual(['已確認收款', '有效待收', '舊資料帳面', '已記錄股利']);
      expect(all.map(card => card.querySelector('.value')?.textContent?.trim())).toEqual(['$37,578', '$14,600', '$21,342', '$58,920']);
      expect(all.map(card => card.querySelector('.meta')?.textContent?.trim()))
        .toEqual(['1 筆 · 已寫入現金帳', '1 筆 · 納入預估含息損益', '1 筆 · 收款未核實', '已確認 ＋ 舊資料']);
      expect(all[1].querySelector('.value')?.classList).toContain('primary');
      expect(all[3].querySelector('.value')?.classList).toContain('dividend');
      expect(all[2].querySelector('.label .swatch.dashed')).not.toBeNull();
      expect(all[0].querySelector('.swatch')).toBeNull();
      fixture.destroy();
    });

    it('renders the explanation note verbatim instead of the interim summary row', () => {
      const fixture = create();
      respond(listCalls[0], { items: [confirmed], total: 3 });
      respond(summaryCalls[0], { items: [confirmed], total: 1 });
      fixture.detectChanges();

      expect(el(fixture).querySelector('.div-note')?.textContent?.trim()).toBe(NOTE);
      expect(el(fixture).querySelector('.summary-row')).toBeNull();
      expect(el(fixture).querySelector('.receipt-note')).toBeNull();
      expect(el(fixture).textContent).not.toContain('符合篩選紀錄筆數');
      fixture.destroy();
    });

    it('shows the period and the server total in the subtitle, with 新增股利 as the only, soft action', () => {
      const fixture = create();
      respond(listCalls[0], { items: [confirmed], total: 3 });
      fixture.detectChanges();
      expect(el(fixture).querySelector('.page-subtitle')?.textContent?.trim()).toBe('全部期間 · 共 3 筆');

      fixture.componentInstance.onDateRangeChange([new Date(2026, 0, 1), new Date(2026, 11, 31)]);
      respond(listCalls[1], { items: [confirmed], total: 2 });
      fixture.detectChanges();
      expect(el(fixture).querySelector('.page-subtitle')?.textContent?.trim()).toBe('2026/01/01 – 2026/12/31 · 共 2 筆');

      const actions = Array.from(el(fixture).querySelectorAll('app-page-header .page-actions button'));
      expect(actions.map(b => b.textContent?.trim())).toEqual(['新增股利']);
      expect(actions[0].classList).toContain('btn-soft');
      expect(actions[0].querySelector('i.pi-plus')).not.toBeNull();
      fixture.destroy();
    });

    it('offers 清除篩選 in the filter bar only while a filter, including a status, is active', () => {
      const fixture = create();
      const component = fixture.componentInstance;
      respond(summaryCalls[0], { items: [confirmed, due], total: 2 });
      fixture.detectChanges();
      const clear = () => el(fixture).querySelector<HTMLButtonElement>('.filter-bar .clear-filters');
      expect(clear()).toBeNull();

      component.onSourceChange('csv');
      fixture.detectChanges();
      clear()!.click();
      fixture.detectChanges();
      expect(component.query().source).toBeUndefined();
      expect(clear()).toBeNull();

      respond(summaryCalls[2], { items: [confirmed, due], total: 2 });
      fixture.detectChanges();
      selectStatus(fixture, '待收');
      expect(clear()?.textContent?.trim()).toBe('清除篩選');
      clear()!.click();
      fixture.detectChanges();
      expect(component.status()).toBe('all');
      expect(clear()).toBeNull();
      fixture.destroy();
    });
  });

  describe('status filter', () => {
    const due2: Dividend = { ...due, id: 14, symbol: '00919' };
    const due3: Dividend = { ...due, id: 15, symbol: '0050' };
    const missing: Dividend = { ...legacy, id: 16, receipt_status: undefined };
    const unresolved: Dividend = { ...due, id: 17, receipt_status: 'unresolved' };
    const set = [confirmed, legacy, due, due2, due3, missing, unresolved];

    it('offers 全部 / 待收 / 已收款 / 舊資料 with 全部 selected', () => {
      const fixture = create();
      const buttons = Array.from(el(fixture).querySelectorAll('app-seg-toggle button'));
      expect(buttons.map(b => b.textContent?.trim())).toEqual(['全部', '待收', '已收款', '舊資料']);
      expect(buttons[0].getAttribute('aria-checked')).toBe('true');
      fixture.destroy();
    });

    it('lists only the pending rows of the filter-wide set with their count, without calling the API', () => {
      const fixture = create();
      respond(listCalls[0], { items: [due], total: 7 });
      respond(summaryCalls[0], { items: set, total: 7 });
      fixture.detectChanges();
      expect(rowIds(fixture)).toEqual([13]);
      const calls = service['getDividends'].mock.calls.length;

      selectStatus(fixture, '待收');
      expect(rowIds(fixture)).toEqual([13, 14, 15]);
      expect(el(fixture).querySelector('.page-subtitle')?.textContent?.trim()).toBe('全部期間 · 共 3 筆');
      expect(service['getDividends'].mock.calls.length).toBe(calls);
      fixture.destroy();
    });

    it('lists 已收款 as confirmed rows and 舊資料 as legacy or missing-status rows; unresolved rows only under 全部', () => {
      const fixture = create();
      respond(listCalls[0], { items: set, total: 7 });
      respond(summaryCalls[0], { items: set, total: 7 });
      fixture.detectChanges();
      expect(rowIds(fixture)).toContain(17);

      selectStatus(fixture, '已收款');
      expect(rowIds(fixture)).toEqual([11]);
      selectStatus(fixture, '舊資料');
      expect(rowIds(fixture)).toEqual([12, 16]);
      expect(el(fixture).querySelector('.page-subtitle')?.textContent?.trim()).toBe('全部期間 · 共 2 筆');
      fixture.destroy();
    });

    it('pages the status set client-side honouring the limit, and never calls the API on a page change', () => {
      const fixture = create();
      respond(listCalls[0], { items: [], total: 0 });
      respond(summaryCalls[0], { items: pendingRows(30), total: 30 });
      fixture.detectChanges();
      selectStatus(fixture, '待收');
      const calls = service['getDividends'].mock.calls.length;

      expect(rowIds(fixture)).toEqual(pendingRows(25).map(d => d.id));
      expect(paginator(fixture)).toMatchObject({ totalRecords: 30, rows: 25, first: 0 });

      fixture.componentInstance.onPageChange({ first: 25, rows: 25 });
      fixture.detectChanges();
      expect(rowIds(fixture)).toEqual([525, 526, 527, 528, 529]);
      expect(paginator(fixture)).toMatchObject({ totalRecords: 30, first: 25 });

      fixture.componentInstance.onPageChange({ first: 0, rows: 50 });
      fixture.detectChanges();
      expect(rowIds(fixture)).toHaveLength(30);
      expect(paginator(fixture)).toBeNull();
      expect(service['getDividends'].mock.calls.length).toBe(calls);
      fixture.destroy();
    });

    it('resets the status page to the start on a status or filter change', () => {
      const fixture = create();
      const component = fixture.componentInstance;
      respond(summaryCalls[0], { items: pendingRows(30), total: 30 });
      fixture.detectChanges();
      selectStatus(fixture, '待收');
      component.onPageChange({ first: 25, rows: 25 });
      selectStatus(fixture, '已收款');
      selectStatus(fixture, '待收');
      expect(rowIds(fixture)[0]).toBe(500);

      component.onPageChange({ first: 25, rows: 25 });
      component.onSourceChange('csv');
      respond(summaryCalls[1], { items: pendingRows(30), total: 30 });
      fixture.detectChanges();
      expect(rowIds(fixture)[0]).toBe(500);
      expect(paginator(fixture)).toMatchObject({ first: 0 });
      fixture.destroy();
    });

    it('restarts the status list at its first page whenever the filter-wide set reloads, also when it shrinks then grows', () => {
      const fixture = create();
      const component = fixture.componentInstance;
      respond(summaryCalls[0], { items: pendingRows(30), total: 30 });
      fixture.detectChanges();
      selectStatus(fixture, '待收');
      component.onPageChange({ first: 25, rows: 25 });

      component.openNew();
      component.saveDividend();
      respond(summaryCalls[1], { items: pendingRows(20), total: 20 });
      fixture.detectChanges();
      expect(rowIds(fixture)).toEqual(pendingRows(20).map(d => d.id));
      expect(el(fixture).textContent).not.toContain('無股利紀錄');

      component.saveDividend();
      respond(summaryCalls[2], { items: pendingRows(30), total: 30 });
      fixture.detectChanges();
      expect(rowIds(fixture)).toEqual(pendingRows(25).map(d => d.id));
      expect(paginator(fixture)).toMatchObject({ first: 0, totalRecords: 30 });
      fixture.destroy();
    });

    it('waits for the filter-wide set instead of listing the unfiltered page', () => {
      const fixture = create();
      respond(listCalls[0], { items: [confirmed], total: 1 });
      fixture.detectChanges();
      selectStatus(fixture, '待收');

      expect(rowIds(fixture)).toEqual([]);
      expect(el(fixture).textContent).toContain('查詢中...');
      expect(el(fixture).textContent).not.toContain('無股利紀錄');
      expect(el(fixture).querySelector('.page-subtitle')?.textContent?.trim()).toBe('全部期間 · 共 — 筆');
      fixture.destroy();
    });

    it('notes the 5,000-row cap on the list in status mode', () => {
      const fixture = create();
      respond(listCalls[0], { items: [], total: 6000 });
      for (let page = 0; page < 50; page++) respond(summaryCalls[page], { items: [{ ...due, id: 1000 + page }], total: 6000 });
      fixture.detectChanges();
      expect(el(fixture).querySelector('.list-note')).toBeNull();

      selectStatus(fixture, '待收');
      expect(el(fixture).querySelector('.list-note')?.textContent?.trim()).toBe('⋯ 僅載入前 5,000 筆');
      fixture.destroy();
    });

    it('keeps the server page with the list error when the filter-wide set fails in status mode', () => {
      const fixture = create();
      respond(listCalls[0], { items: [confirmed], total: 30 });
      fixture.detectChanges();
      selectStatus(fixture, '待收');
      summaryCalls[0].response.error(new Error('Synthetic failure'));
      fixture.detectChanges();

      expect(rowIds(fixture)).toEqual([11]);
      expect(el(fixture).querySelector('.list-error')?.textContent?.trim()).toBe('查詢失敗，請檢查篩選條件');
      expect(el(fixture).textContent).not.toContain('無股利紀錄');
      expect(paginator(fixture)).toMatchObject({ totalRecords: 30 });
      expect(el(fixture).querySelector('.page-subtitle')?.textContent?.trim()).toBe('全部期間 · 共 30 筆');

      selectStatus(fixture, '全部');
      expect(el(fixture).querySelector('.list-error')).toBeNull();
      fixture.destroy();
    });

    it('keeps the previous status list while the set reloads and drops it when the reload fails', () => {
      const fixture = create();
      respond(listCalls[0], { items: [confirmed], total: 30 });
      respond(summaryCalls[0], { items: set, total: 7 });
      fixture.detectChanges();
      selectStatus(fixture, '待收');
      fixture.componentInstance.openNew();
      fixture.componentInstance.saveDividend();
      fixture.detectChanges();
      expect(rowIds(fixture)).toEqual([13, 14, 15]);
      expect(el(fixture).querySelector('.page-subtitle')?.textContent?.trim()).toBe('全部期間 · 共 3 筆');

      summaryCalls[1].response.error(new Error('Synthetic failure'));
      respond(listCalls[1], { items: [confirmed], total: 30 });
      fixture.detectChanges();
      expect(fixture.componentInstance.summaryRows()).toBeNull();
      expect(rowIds(fixture)).toEqual([11]);
      expect(el(fixture).querySelector('.list-error')).not.toBeNull();
      fixture.destroy();
    });

    it('clamps the status page to the last page when a reloaded set is shorter', () => {
      const fixture = create();
      const component = fixture.componentInstance;
      respond(summaryCalls[0], { items: pendingRows(26), total: 26 });
      fixture.detectChanges();
      selectStatus(fixture, '待收');
      component.openNew();
      component.saveDividend();
      component.onPageChange({ first: 25, rows: 25 });
      fixture.detectChanges();
      expect(rowIds(fixture)).toEqual([525]);

      respond(summaryCalls[1], { items: pendingRows(25), total: 25 });
      fixture.detectChanges();
      expect(rowIds(fixture)).toEqual(pendingRows(25).map(d => d.id));
      expect(el(fixture).textContent).not.toContain('無股利紀錄');

      component.onPageChange({ first: 0, rows: 10 });
      component.saveDividend();
      component.onPageChange({ first: 20, rows: 10 });
      respond(summaryCalls[2], { items: pendingRows(15), total: 15 });
      fixture.detectChanges();
      expect(rowIds(fixture)).toEqual([510, 511, 512, 513, 514]);
      expect(paginator(fixture)).toMatchObject({ first: 10, totalRecords: 15 });
      fixture.destroy();
    });

    it('returns to server paging at offset 0 with the server total on 全部', () => {
      const fixture = create();
      const component = fixture.componentInstance;
      respond(listCalls[0], { items: [due], total: 60 });
      respond(summaryCalls[0], { items: pendingRows(30), total: 30 });
      component.onPageChange({ first: 25, rows: 25 });
      respond(listCalls[1], { items: [due], total: 60 });
      fixture.detectChanges();
      selectStatus(fixture, '待收');
      component.onPageChange({ first: 25, rows: 25 });
      expect(listCalls).toHaveLength(2);

      selectStatus(fixture, '全部');
      expect(listCalls).toHaveLength(3);
      expect(listCalls[2].query).toMatchObject({ offset: 0, limit: 25 });
      respond(listCalls[2], { items: [due], total: 60 });
      fixture.detectChanges();
      expect(paginator(fixture)).toMatchObject({ totalRecords: 60, first: 0 });
      expect(el(fixture).querySelector('.page-subtitle')?.textContent?.trim()).toBe('全部期間 · 共 60 筆');
      expect(summaryCalls).toHaveLength(1);
      fixture.destroy();
    });
  });

  describe('rows', () => {
    const tw: Dividend = { id: 21, symbol: '00878', amount: 9600, ex_dividend_date: '2026-10-17', receipt_status: 'pending', revision: 1,
      cash_dividend_per_share: 0.48, quantity_at_record_date: 20000, fee: 10, tax: 202, payment_date: '2026-11-18', payment_date_source: 'TWSE 公告' };
    const usd: Dividend = { id: 22, symbol: 'AAPL', amount: 7.8, currency: 'USD', fx_rate_to_twd: '30.80', ex_dividend_date: '2026-08-11',
      receipt_status: 'confirmed', receipt_date: '2026-08-14', receipt_account_id: 7, cash_dividend_per_share: 0.26, quantity_at_record_date: 30 };
    const usdNoFx: Dividend = { id: 23, symbol: 'MSFT', amount: 7.8, currency: 'USD', fx_rate_to_twd: null, ex_dividend_date: '2026-05-14', receipt_status: 'legacy_unknown' };
    const review: Dividend = { id: 24, symbol: '2330', amount: 4000, ex_dividend_date: '2026-06-12', receipt_status: 'unresolved',
      review_reason: 'Synthetic review reason', source_correction: { amount: 1 } };

    function rendered(rows: Dividend[]): ComponentFixture<PortfolioDividendListComponent> {
      const fixture = create();
      respond(listCalls[0], { items: rows, total: rows.length });
      respond(summaryCalls[0], { items: rows, total: rows.length });
      fixture.detectChanges();
      return fixture;
    }

    function cell(fixture: ComponentFixture<PortfolioDividendListComponent>, id: number, selector: string): string {
      return lines(el(fixture).querySelector(`[data-row-id="${id}"] ${selector}`));
    }

    it('renders the column heads and a grid row: square, name + code, dates, per-share × qty with deductions, amount, tag', () => {
      const fixture = rendered([tw]);
      expect(Array.from(el(fixture).querySelectorAll('.div-head span')).map(h => h.textContent?.trim()))
        .toEqual(['股票', '除息日', '發放 / 收款', '每股 × 股數', '淨額', '狀態']);
      expect(el(fixture).querySelector('app-timeline')).toBeNull();
      expect(el(fixture).querySelector('.div-head')?.getAttribute('aria-hidden')).toBe('true');
      expect(el(fixture).querySelector('.div-list > .visually-hidden')?.textContent?.trim())
        .toBe('股利紀錄，欄位：股票、除息日、發放 / 收款、每股 × 股數、淨額、狀態');

      const square = el(fixture).querySelector<HTMLElement>('[data-row-id="21"] .div-square')!;
      const token = squareToken('00878');
      expect(square.textContent?.trim()).toBe(holdingAbbr('00878', '國泰永續高股息'));
      expect(square.style.background).toBe(`var(${token})`);
      expect(square.classList).toContain(abbrInk(token) === 'dark' ? 'ink-dark' : 'ink-light');
      expect(cell(fixture, 21, '.primary-name')).toBe('國泰永續高股息 00878');
      expect(cell(fixture, 21, '.c-ex')).toBe('2026/10/17');
      expect(cell(fixture, 21, '.c-pay')).toBe('預定 11/18 TWSE 公告');
      expect(cell(fixture, 21, '.c-qty')).toBe('0.48 × 20,000 手續費 10 · 補充保費 202');
      expect(cell(fixture, 21, '.c-net .net')).toBe('+9,600');
      expect(cell(fixture, 21, '.c-status')).toBe('待收');
      expect(el(fixture).querySelector('[data-row-id="21"] .c-status .tag')?.classList).toContain('tag-primary');
      fixture.destroy();
    });

    it('colours pending amounts primary and the others dividend, with every status tag', () => {
      const fixture = rendered([tw, usd, usdNoFx, review]);
      const net = (id: number) => el(fixture).querySelector(`[data-row-id="${id}"] .net`)!.classList;
      expect(net(21)).toContain('primary');
      expect(net(21)).not.toContain('dividend');
      for (const id of [22, 23, 24]) {
        expect(net(id)).toContain('dividend');
        expect(net(id)).not.toContain('primary');
      }
      expect([21, 22, 23, 24].map(id => cell(fixture, id, '.c-status'))).toEqual(['待收', '已收款', '舊資料 · 未核實', '待人工釐清']);
      fixture.destroy();
    });

    it('shows a foreign row in TWD over its native amount, and the native amount over — without fx', () => {
      const fixture = rendered([usd, usdNoFx]);
      expect(cell(fixture, 22, '.c-net')).toBe('+240 7.80 USD · 匯率 30.80');
      expect(cell(fixture, 22, '.c-pay')).toBe('收款 08/14 元大交割戶');
      expect(cell(fixture, 22, '.c-qty')).toBe('0.26 USD × 30');
      expect(cell(fixture, 23, '.c-net')).toBe('+7.80 USD —');
      expect(cell(fixture, 23, '.c-pay')).toBe('—');
      fixture.destroy();
    });

    it('keeps the review reason and the source-correction sentence under the row', () => {
      const fixture = rendered([review, tw]);
      expect(cell(fixture, 24, '.div-notes')).toBe('Synthetic review reason 來源資料已變更；已確認金額及現金帳不變，需人工釐清。');
      expect(el(fixture).querySelector('[data-row-id="21"] .div-notes')).toBeNull();
      fixture.destroy();
    });

    it('offers 確認收款 on pending rows only and the ⋯ menu on every row, also in status mode', () => {
      const fixture = create();
      const component = fixture.componentInstance;
      const menu = vi.spyOn(component, 'showMenu').mockImplementation(() => undefined);
      respond(listCalls[0], { items: [usd], total: 2 });
      respond(summaryCalls[0], { items: [tw, usd], total: 2 });
      fixture.detectChanges();
      selectStatus(fixture, '待收');

      const confirm = Array.from(el(fixture).querySelectorAll('button')).filter(b => b.textContent?.trim() === '確認收款');
      expect(confirm.map(b => b.closest('[data-row-id]')?.getAttribute('data-row-id'))).toEqual(['21']);
      expect(confirm[0].getAttribute('aria-label')).toBe('確認收款 國泰永續高股息 00878');
      confirm[0].click();
      expect(component.openReceiptId()).toBe(21);

      el(fixture).querySelector<HTMLButtonElement>('[data-row-id="21"] .row-action')!.click();
      expect(menu).toHaveBeenCalledWith(expect.any(MouseEvent), tw);
      fixture.destroy();
    });

    it('keeps the upcoming ex-dividend strip above the list', () => {
      service['getUpcomingExDividends'].mockReturnValue(of([{ symbol: '0056', name: '元大高股息', ex_dividend_date: '2026-10-21', cash_dividend: '0.866' }]));
      const fixture = rendered([tw]);
      const strip = el(fixture).querySelector('.upcoming-strip');
      expect(strip?.textContent).toContain('即將除權息提醒');
      expect(strip?.textContent).toContain('元大高股息');
      expect(strip!.compareDocumentPosition(el(fixture).querySelector('.div-list')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      fixture.destroy();
    });
  });

  describe('inline receipt form', () => {
    const WARNING = '只在核對過實際入帳後確認。預定發放日不是收款證明；確認後會寫入現金帳，無法直接編輯撤銷。';
    const tw: Dividend = { id: 21, symbol: '00878', amount: 9600, ex_dividend_date: '2026-10-17', receipt_status: 'pending', revision: 4,
      cash_dividend_per_share: 0.48, quantity_at_record_date: 20000, payment_date: '2026-11-18' };
    const twPaid: Dividend = { ...tw, receipt_status: 'confirmed', receipt_date: '2026-11-18', receipt_account_id: 7, revision: 5 };
    const noRevision: Dividend = { id: 31, symbol: '0050', amount: 500, ex_dividend_date: '2026-07-16', receipt_status: 'pending' };
    const accounts = [
      { id: 7, nickname: '元大交割戶', currency: 'TWD', is_active: true },
      { id: 8, nickname: 'Synthetic closed TWD', currency: 'TWD', is_active: false },
      { id: 9, nickname: 'Synthetic USD', currency: 'USD', is_active: true },
    ];

    beforeEach(() => {
      service['getAccounts'].mockImplementation(() => of({ items: accounts }));
    });

    function rendered(rows: Dividend[]): ComponentFixture<PortfolioDividendListComponent> {
      const fixture = create();
      respond(listCalls[0], { items: rows, total: rows.length });
      respond(summaryCalls[0], { items: rows, total: rows.length });
      fixture.detectChanges();
      return fixture;
    }

    function forms(fixture: ComponentFixture<PortfolioDividendListComponent>): HTMLElement[] {
      return Array.from(el(fixture).querySelectorAll<HTMLElement>('.receipt-form'));
    }

    function open(fixture: ComponentFixture<PortfolioDividendListComponent>, id: number): void {
      el(fixture).querySelector<HTMLButtonElement>(`[data-row-id="${id}"] .c-actions app-btn button`)!.click();
      fixture.detectChanges();
    }

    function formButton(fixture: ComponentFixture<PortfolioDividendListComponent>, label: string): HTMLButtonElement {
      return Array.from(el(fixture).querySelectorAll<HTMLButtonElement>('.receipt-form button')).find(b => b.textContent?.trim() === label)!;
    }

    function fill(fixture: ComponentFixture<PortfolioDividendListComponent>, date: string | null, accountId: string | null): void {
      if (date !== null) {
        const input = el(fixture).querySelector<HTMLInputElement>('.receipt-form input[aria-label="實際收款日"]')!;
        input.value = date;
        input.dispatchEvent(new Event('input'));
      }
      if (accountId !== null) {
        const select = el(fixture).querySelector<HTMLSelectElement>('.receipt-form select[aria-label="收款帳戶"]')!;
        select.value = accountId;
        select.dispatchEvent(new Event('change'));
      }
      fixture.detectChanges();
    }

    function pendingCard(fixture: ComponentFixture<PortfolioDividendListComponent>): string | undefined {
      return cards(fixture)[1].querySelector('.value')?.textContent?.trim();
    }

    it('opens a form under the pending row: title, verbatim warning, date input, active TWD accounts, 取消 and a disabled 確認已收到並入帳', () => {
      const fixture = rendered([tw, legacy]);
      expect(forms(fixture)).toHaveLength(0);
      expect(el(fixture).querySelector('[data-row-id="12"] .c-actions app-btn')).toBeNull();

      open(fixture, 21);
      const [form] = forms(fixture);
      expect(forms(fixture)).toHaveLength(1);
      expect(form.previousElementSibling?.getAttribute('data-row-id')).toBe('21');
      expect(form.querySelector('.receipt-title')?.textContent?.trim()).toBe('確認實際收款 · 國泰永續高股息 +9,600');
      expect(form.getAttribute('aria-label')).toBe('確認實際收款 · 國泰永續高股息 +9,600');
      expect(form.querySelector('.receipt-warning')?.textContent?.trim()).toBe(WARNING);
      const labels = Array.from(form.querySelectorAll('label')).map(label => label.firstChild?.textContent?.trim());
      expect(labels).toEqual(['實際收款日', '收款帳戶']);
      expect(form.querySelector('input[aria-label="實際收款日"]')?.getAttribute('type')).toBe('date');
      const options = Array.from(form.querySelectorAll<HTMLOptionElement>('select[aria-label="收款帳戶"] option'));
      expect(options.map(o => [o.value, o.textContent?.trim()])).toEqual([['', '請選擇帳戶'], ['7', '元大交割戶']]);
      expect(Array.from(form.querySelectorAll('app-btn button')).map(b => b.textContent?.trim())).toEqual(['取消', '確認已收到並入帳']);
      expect(formButton(fixture, '確認已收到並入帳').disabled).toBe(true);
      // The one load made on init serves both the 收款 names and the form.
      expect(service['getAccounts']).toHaveBeenCalledTimes(1);
      expect(service['getAccounts']).toHaveBeenCalledWith({ include_inactive: true });
      fixture.destroy();
    });

    it('enables 確認已收到並入帳 only once both fields are set; 取消 closes and a reopened form starts empty', () => {
      const fixture = rendered([tw]);
      open(fixture, 21);
      fill(fixture, '2026-11-18', null);
      expect(formButton(fixture, '確認已收到並入帳').disabled).toBe(true);
      fill(fixture, '', '7');
      expect(formButton(fixture, '確認已收到並入帳').disabled).toBe(true);
      fill(fixture, '2026-11-18', null);
      expect(formButton(fixture, '確認已收到並入帳').disabled).toBe(false);

      formButton(fixture, '取消').click();
      fixture.detectChanges();
      expect(forms(fixture)).toHaveLength(0);
      expect(fixture.componentInstance.openReceiptId()).toBeNull();

      open(fixture, 21);
      expect(el(fixture).querySelector<HTMLInputElement>('.receipt-form input[aria-label="實際收款日"]')!.value).toBe('');
      expect(formButton(fixture, '確認已收到並入帳').disabled).toBe(true);
      expect(service['confirmDividendReceipt']).not.toHaveBeenCalled();
      fixture.destroy();
    });

    it('keeps one form open: 確認收款 on another row closes the first', () => {
      const fixture = rendered([tw, due]);
      open(fixture, 21);
      fill(fixture, '2026-11-18', '7');
      open(fixture, 13);

      expect(forms(fixture)).toHaveLength(1);
      expect(forms(fixture)[0].previousElementSibling?.getAttribute('data-row-id')).toBe('13');
      expect(forms(fixture)[0].querySelector('.receipt-title')?.textContent?.trim()).toBe('確認實際收款 · 0056 +14,600');
      expect(formButton(fixture, '確認已收到並入帳').disabled).toBe(true);
      fixture.destroy();
    });

    it('submits the date, account and revision (1 for a row without one) once, ignoring a second press and another row while busy', () => {
      const response = new Subject<Dividend>();
      service['confirmDividendReceipt'].mockReturnValue(response);
      const fixture = rendered([tw, noRevision]);
      open(fixture, 31);
      fill(fixture, '2026-11-18', '7');

      formButton(fixture, '確認已收到並入帳').click();
      formButton(fixture, '確認已收到並入帳').click();
      expect(service['confirmDividendReceipt']).toHaveBeenCalledTimes(1);
      expect(service['confirmDividendReceipt']).toHaveBeenCalledWith(31, { receipt_date: '2026-11-18', account_id: 7, revision: 1 });
      fixture.detectChanges();
      expect(fixture.componentInstance.receiptBusy()).toBe(true);
      expect(formButton(fixture, '確認已收到並入帳').disabled).toBe(true);
      open(fixture, 21);
      expect(fixture.componentInstance.openReceiptId()).toBe(31);
      fixture.destroy();
    });

    it('on success closes the form, reloads the page and the filter-wide set, shows 已收款 and drops 有效待收 by 9,600', () => {
      service['confirmDividendReceipt'].mockReturnValue(of(twPaid));
      const fixture = rendered([tw, due]);
      expect(pendingCard(fixture)).toBe('$24,200');
      open(fixture, 21);
      fill(fixture, '2026-11-18', '7');

      formButton(fixture, '確認已收到並入帳').click();
      fixture.detectChanges();
      expect(service['confirmDividendReceipt']).toHaveBeenCalledWith(21, { receipt_date: '2026-11-18', account_id: 7, revision: 4 });
      expect(forms(fixture)).toHaveLength(0);
      expect(fixture.componentInstance.receiptBusy()).toBe(false);
      expect(listCalls).toHaveLength(2);
      expect(summaryCalls).toHaveLength(2);

      respond(listCalls[1], { items: [twPaid, due], total: 2 });
      respond(summaryCalls[1], { items: [twPaid, due], total: 2 });
      fixture.detectChanges();
      expect(lines(el(fixture).querySelector('[data-row-id="21"] .c-status'))).toBe('已收款');
      expect(el(fixture).querySelector('[data-row-id="21"] .c-actions app-btn')).toBeNull();
      expect(pendingCard(fixture)).toBe('$14,600');
      fixture.destroy();
    });

    it('on error closes the form, toasts the server detail (or the fallback), reloads both sets and keeps the row pending', () => {
      service['confirmDividendReceipt']
        .mockReturnValueOnce(throwError(() => ({ status: 409, error: { detail: 'Synthetic stale revision' } })))
        .mockReturnValueOnce(throwError(() => ({ status: 500 })));
      const fixture = rendered([tw]);
      const toast = vi.spyOn(fixture.debugElement.injector.get(MessageService), 'add');
      open(fixture, 21);
      fill(fixture, '2026-11-18', '7');

      formButton(fixture, '確認已收到並入帳').click();
      fixture.detectChanges();
      expect(toast).toHaveBeenCalledWith({ severity: 'error', summary: '收款確認未完成', detail: 'Synthetic stale revision' });
      expect(forms(fixture)).toHaveLength(0);
      expect(fixture.componentInstance.receiptBusy()).toBe(false);
      expect(listCalls).toHaveLength(2);
      expect(summaryCalls).toHaveLength(2);
      respond(listCalls[1], { items: [tw], total: 1 });
      respond(summaryCalls[1], { items: [tw], total: 1 });
      fixture.detectChanges();
      expect(lines(el(fixture).querySelector('[data-row-id="21"] .c-status'))).toBe('待收');
      expect(el(fixture).querySelector('[data-row-id="21"] .c-actions app-btn')).not.toBeNull();

      open(fixture, 21);
      fill(fixture, '2026-11-18', '7');
      formButton(fixture, '確認已收到並入帳').click();
      expect(toast).toHaveBeenLastCalledWith({ severity: 'error', summary: '收款確認未完成', detail: '請重新載入後核對' });
      expect(listCalls).toHaveLength(3);
      expect(summaryCalls).toHaveLength(3);
      fixture.destroy();
    });

    it('with 待收 selected keeps the previous list while the set reloads, then drops the row that came back confirmed', () => {
      service['confirmDividendReceipt'].mockReturnValue(of(twPaid));
      const fixture = rendered([tw, due]);
      selectStatus(fixture, '待收');
      expect(rowIds(fixture)).toEqual([21, 13]);
      open(fixture, 21);
      fill(fixture, '2026-11-18', '7');
      formButton(fixture, '確認已收到並入帳').click();
      fixture.detectChanges();

      expect(rowIds(fixture)).toEqual([21, 13]);
      expect(forms(fixture)).toHaveLength(0);
      expect(el(fixture).textContent).toContain('查詢中...');
      expect(el(fixture).querySelector('.page-subtitle')?.textContent?.trim()).toBe('全部期間 · 共 2 筆');

      respond(listCalls[1], { items: [twPaid, due], total: 2 });
      respond(summaryCalls[1], { items: [twPaid, due], total: 2 });
      fixture.detectChanges();
      expect(rowIds(fixture)).toEqual([13]);
      expect(el(fixture).textContent).not.toContain('查詢中...');
      expect(el(fixture).querySelector('.page-subtitle')?.textContent?.trim()).toBe('全部期間 · 共 1 筆');
      expect(pendingCard(fixture)).toBe('$14,600');
      fixture.destroy();
    });

    it('closes the form, clearing its fields, on a filter, sort, status, page or clear change', () => {
      const fixture = rendered([tw, due]);
      const component = fixture.componentInstance;
      const changes: [string, () => void][] = [
        ['source', () => component.onSourceChange('csv')],
        ['sort', () => component.onSortChange('symbol:asc')],
        ['status', () => selectStatus(fixture, '待收')],
        ['page', () => component.onPageChange({ first: 0, rows: 50 })],
        ['clear', () => component.clearFilters()],
      ];
      for (const [name, change] of changes) {
        open(fixture, 21);
        fill(fixture, '2026-11-18', '7');
        expect(forms(fixture), name).toHaveLength(1);
        change();
        fixture.detectChanges();
        expect(forms(fixture), name).toHaveLength(0);
        expect(component.openReceiptId(), name).toBeNull();
        expect(component.receiptDate(), name).toBe('');
        expect(component.receiptAccountId(), name).toBeNull();
        expect(rowIds(fixture), name).toContain(21);
      }
      fixture.destroy();
    });

    it('submits the live row with its reloaded revision', () => {
      service['confirmDividendReceipt'].mockReturnValue(new Subject<Dividend>());
      const fixture = rendered([tw, due]);
      const component = fixture.componentInstance;
      open(fixture, 21);
      fill(fixture, '2026-11-18', '7');
      component.openNew();
      component.saveDividend();
      respond(listCalls[1], { items: [{ ...tw, revision: 6 }, due], total: 2 });
      respond(summaryCalls[1], { items: [{ ...tw, revision: 6 }, due], total: 2 });
      fixture.detectChanges();
      expect(forms(fixture)).toHaveLength(1);
      formButton(fixture, '確認已收到並入帳').click();
      expect(service['confirmDividendReceipt']).toHaveBeenCalledWith(21, { receipt_date: '2026-11-18', account_id: 7, revision: 6 });
      fixture.destroy();
    });

    it('submits nothing and closes the form once its row is no longer pending', () => {
      const gone = rendered([tw, due]);
      open(gone, 13);
      fill(gone, '2026-11-18', '7');
      gone.componentInstance.saveDividend();
      respond(listCalls[1], { items: [tw, { ...due, receipt_status: 'confirmed', receipt_date: '2026-10-20' }], total: 2 });
      gone.detectChanges();
      expect(forms(gone)).toHaveLength(0);
      gone.componentInstance.confirmReceipt();
      expect(service['confirmDividendReceipt']).not.toHaveBeenCalled();
      expect(gone.componentInstance.openReceiptId()).toBeNull();
      gone.destroy();
    });

    it('raises a late accounts failure only while the form is rendered', () => {
      const accountsResponse = new Subject<{ items: typeof accounts }>();
      service['getAccounts'].mockImplementation(() => accountsResponse);
      const fixture = rendered([tw, due]);
      const toast = vi.spyOn(fixture.debugElement.injector.get(MessageService), 'add');
      open(fixture, 21);
      fixture.componentInstance.saveDividend();
      respond(listCalls[1], { items: [due], total: 1 });
      fixture.detectChanges();
      expect(forms(fixture)).toHaveLength(0);
      accountsResponse.error(new Error('Synthetic failure'));
      expect(toast).not.toHaveBeenCalledWith({ severity: 'error', summary: '無法取得收款帳戶' });

      service['getAccounts'].mockImplementation(() => throwError(() => new Error('Synthetic failure')));
      open(fixture, 13);
      expect(toast).toHaveBeenCalledWith({ severity: 'error', summary: '無法取得收款帳戶' });
      fixture.destroy();
    });

    it('focuses 實際收款日 on open and returns focus to the row button on 取消 and after a submit settles, with aria-expanded', () => {
      service['confirmDividendReceipt'].mockReturnValue(throwError(() => ({ error: { detail: 'Synthetic stale revision' } })));
      const fixture = rendered([tw, due]);
      const button = (id: number) => el(fixture).querySelector<HTMLButtonElement>(`[data-row-id="${id}"] .c-actions app-btn button`);
      expect(button(21)?.getAttribute('aria-expanded')).toBe('false');

      open(fixture, 21);
      expect(document.activeElement).toBe(el(fixture).querySelector('.receipt-form input[aria-label="實際收款日"]'));
      expect(button(21)?.getAttribute('aria-expanded')).toBe('true');
      expect(button(13)?.getAttribute('aria-expanded')).toBe('false');

      formButton(fixture, '取消').click();
      fixture.detectChanges();
      expect(document.activeElement).toBe(button(21));
      expect(button(21)?.getAttribute('aria-expanded')).toBe('false');

      open(fixture, 13);
      fill(fixture, '2026-11-18', '7');
      formButton(fixture, '確認已收到並入帳').click();
      fixture.detectChanges();
      expect(forms(fixture)).toHaveLength(0);
      expect(document.activeElement).toBe(button(13));
      fixture.destroy();
    });

    it('cancels the receipt request on destroy: a late response neither throws nor reloads nor toasts', () => {
      const toast = vi.spyOn(MessageService.prototype, 'add');
      try {
        const response = new Subject<Dividend>();
        service['confirmDividendReceipt'].mockReturnValue(response);
        const fixture = rendered([tw]);
        open(fixture, 21);
        fill(fixture, '2026-11-18', '7');
        formButton(fixture, '確認已收到並入帳').click();
        expect(response.observed).toBe(true);
        const calls = service['getDividends'].mock.calls.length;
        toast.mockClear();

        fixture.destroy();
        expect(response.observed).toBe(false);
        expect(() => response.next(twPaid)).not.toThrow();
        expect(() => response.error({ error: { detail: 'Synthetic late failure' } })).not.toThrow();
        expect(service['getDividends'].mock.calls.length).toBe(calls);
        expect(toast).not.toHaveBeenCalled();
      } finally {
        toast.mockRestore();
      }
    });

    it('moves no focus once destroyed (取消 after destroy does not throw NG0911)', () => {
      const fixture = rendered([tw]);
      open(fixture, 21);
      const component = fixture.componentInstance;
      fixture.destroy();
      expect(() => component.cancelReceipt()).not.toThrow();
      expect(() => component.openReceipt(tw)).not.toThrow();
    });

    it('toasts 無法取得收款帳戶 when the accounts cannot be loaded, retrying the init load when the form opens', () => {
      const toast = vi.spyOn(MessageService.prototype, 'add');
      try {
        service['getAccounts'].mockImplementation(() => throwError(() => new Error('Synthetic failure')));
        const fixture = rendered([tw]);
        expect(toast).not.toHaveBeenCalled();

        open(fixture, 21);
        expect(service['getAccounts']).toHaveBeenCalledTimes(2);
        expect(toast).toHaveBeenCalledWith({ severity: 'error', summary: '無法取得收款帳戶' });
        expect(Array.from(el(fixture).querySelectorAll('.receipt-form option')).map(o => o.textContent?.trim())).toEqual(['請選擇帳戶']);

        toast.mockClear();
        service['getAccounts'].mockImplementation(() => of({ items: accounts }));
        formButton(fixture, '取消').click();
        open(fixture, 21);
        expect(service['getAccounts']).toHaveBeenCalledTimes(3);
        expect(toast).not.toHaveBeenCalled();
        expect(Array.from(el(fixture).querySelectorAll('.receipt-form option')).map(o => o.textContent?.trim())).toEqual(['請選擇帳戶', '元大交割戶']);
        open(fixture, 21);
        expect(service['getAccounts']).toHaveBeenCalledTimes(3);
        fixture.destroy();
      } finally {
        toast.mockRestore();
      }
    });
  });

  describe('phone and sheet widths', () => {
    it('shows only the 已確認收款 and 有效待收 cards', () => {
      TestBed.inject(LayoutModeService).set('phone');
      const fixture = create();
      respond(summaryCalls[0], { items: [confirmed, legacy, due], total: 3 });
      fixture.detectChanges();
      expect(cards(fixture).map(card => card.querySelector('.label')?.textContent?.trim())).toEqual(['已確認收款', '有效待收']);
      fixture.destroy();
    });

    it('collapses rows: square, name + tag, per-share line, the amount with the 除息 / 發放 line beneath, no column heads', () => {
      TestBed.inject(LayoutModeService).set('phone');
      const fixture = create();
      const tw: Dividend = { ...due, id: 21, symbol: '00878', amount: 9600, cash_dividend_per_share: 0.48, quantity_at_record_date: 20000, payment_date: '2026-11-18' };
      const paid: Dividend = { ...confirmed, id: 25, ex_dividend_date: '2026-07-16', receipt_date: '2026-08-12' };
      respond(listCalls[0], { items: [tw, paid], total: 2 });
      fixture.detectChanges();
      const row = el(fixture).querySelector('[data-row-id="21"]')!;
      const text = (selector: string) => lines(row.querySelector(selector));

      expect(el(fixture).querySelector('.div-list.compact')).not.toBeNull();
      expect(el(fixture).querySelector('.div-head')).toBeNull();
      expect(row.querySelector('.div-square')).not.toBeNull();
      expect(text('.name-line')).toBe('國泰永續高股息 00878 待收');
      expect(text('.c-stock .sub')).toBe('0.48 × 20,000');
      expect(text('.c-net')).toBe('+9,600 除息 2026/10/17 · 預定 11/18');
      expect(lines(el(fixture).querySelector('[data-row-id="25"] .c-net'))).toBe('+37,578 除息 2026/07/16 · 收款 08/12');
      expect(row.querySelector('.c-ex')).toBeNull();
      expect(row.querySelector('.c-pay')).toBeNull();
      expect(row.querySelector('.c-status')).toBeNull();
      fixture.destroy();
    });

    it('collapses rows at sheet width too, keeping all four cards', () => {
      TestBed.inject(LayoutModeService).set('sheet');
      const fixture = create();
      respond(listCalls[0], { items: [due], total: 1 });
      respond(summaryCalls[0], { items: [confirmed, legacy, due], total: 3 });
      fixture.detectChanges();
      const row = el(fixture).querySelector('[data-row-id="13"]')!;

      expect(el(fixture).querySelector('.div-list.compact')).not.toBeNull();
      expect(el(fixture).querySelector('.div-head')).toBeNull();
      expect(row.querySelector('.name-line app-tag')?.textContent?.trim()).toBe('待收');
      expect(row.querySelector('.c-ex')).toBeNull();
      expect(row.querySelector('.c-status')).toBeNull();
      expect(cards(fixture).map(card => card.querySelector('.label')?.textContent?.trim())).toEqual(['已確認收款', '有效待收', '舊資料帳面', '已記錄股利']);
      fixture.destroy();
    });

    it('keeps the column grid at panes width', () => {
      TestBed.inject(LayoutModeService).set('panes');
      const fixture = create();
      respond(listCalls[0], { items: [due], total: 1 });
      respond(summaryCalls[0], { items: [confirmed, legacy, due], total: 3 });
      fixture.detectChanges();
      const row = el(fixture).querySelector('[data-row-id="13"]')!;

      expect(el(fixture).querySelector('.div-list.compact')).toBeNull();
      expect(el(fixture).querySelector('.div-head')).not.toBeNull();
      expect(row.querySelector('.name-line app-tag')).toBeNull();
      expect(row.querySelector('.c-ex')).not.toBeNull();
      expect(row.querySelector('.c-status app-tag')?.textContent?.trim()).toBe('待收');
      expect(cards(fixture)).toHaveLength(4);
      fixture.destroy();
    });

    it('keeps the receipt form fields under the row in the compact list', () => {
      TestBed.inject(LayoutModeService).set('phone');
      const fixture = create();
      respond(listCalls[0], { items: [due], total: 1 });
      fixture.detectChanges();
      el(fixture).querySelector<HTMLButtonElement>('[data-row-id="13"] .c-actions app-btn button')!.click();
      fixture.detectChanges();

      const form = el(fixture).querySelector('.div-list.compact .receipt-form');
      expect(form?.previousElementSibling?.getAttribute('data-row-id')).toBe('13');
      expect(form?.querySelector('input[type="date"][aria-label="實際收款日"]')).not.toBeNull();
      expect(form?.querySelector('select[aria-label="收款帳戶"]')).not.toBeNull();
      expect(Array.from(form!.querySelectorAll('app-btn button')).map(b => b.textContent?.trim())).toEqual(['取消', '確認已收到並入帳']);
      fixture.destroy();
    });
  });
});
