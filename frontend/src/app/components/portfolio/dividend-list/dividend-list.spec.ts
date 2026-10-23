import { ComponentFixture, TestBed } from '@angular/core/testing';
import { MessageService } from 'primeng/api';
import { of, Subject, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PortfolioService } from '../../../services/portfolio.service';
import { PortfolioDividendListComponent } from './dividend-list';
import { Dividend, DividendQuery, Paged } from '../../../models/portfolio.model';

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
    TestBed.configureTestingModule({ imports: [PortfolioDividendListComponent], providers: [{ provide: PortfolioService, useValue: service }] });
  });
  it('shows persistent pending and unknown legacy states without counting paid income', () => {
    const fixture = TestBed.createComponent(PortfolioDividendListComponent);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('待確認收款');
    expect(fixture.nativeElement.textContent).toContain('舊資料：收款未核實');
    expect(fixture.nativeElement.textContent).not.toContain('預定發放日 未知');
    expect(fixture.componentInstance.dividendTotal()).toBe(0);
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
      // Count visible text, so a duplicate presentation outside the timeline also fails.
      expect((root.textContent ?? '').split(row.symbol).length - 1).toBe(1);
      expect(root.querySelectorAll('[data-row-id="' + row.id + '"]')).toHaveLength(1);
    }
    expect(root.textContent).toContain('預定發放日 2026-10-01');
    expect(root.textContent).toContain('實際收款日 2026-10-02');
    expect(root.textContent).toContain('Synthetic review reason');
    expect(root.textContent).not.toContain('預定發放日 未知');
    expect(root.textContent).not.toContain('實際收款日 未確認');
    expect(root.textContent).toContain('舊資料帳面合計');
    expect(root.textContent).toContain('已確認收款合計');
    expect(root.textContent).toContain('符合篩選紀錄筆數');
    expect(fixture.componentInstance.dividendTotal()).toBe(300);
    expect(fixture.componentInstance.legacyTotal()).toBe(100);
    const summaries = Array.from(root.querySelectorAll('.summary-item')).map(el => el.textContent);
    expect(summaries[0]).toContain('$300');
    expect(summaries[1]).toContain('$100');
    expect(summaries[2]).toContain('70 筆');
    const buttons = Array.from(root.querySelectorAll('button')).filter(b => b.textContent?.includes('確認實際收款'));
    expect(buttons).toHaveLength(1);
    expect(buttons[0].closest('[data-row-id]')?.getAttribute('data-row-id')).toBe('102');
    buttons[0].click();
    expect(fixture.componentInstance.receiptTarget()?.id).toBe(102);
    fixture.destroy();
  });
  it('preserves the full ex-date for rows with the same day in different years', () => {
    service.getDividends.mockReturnValue(of({ items: [
      { ...pending, id: 201, ex_dividend_date: '2024-03-16' },
      { ...pending, id: 202, ex_dividend_date: '2025-03-16' },
    ], total: 2 }));
    const fixture = TestBed.createComponent(PortfolioDividendListComponent);
    fixture.detectChanges();
    const root: HTMLElement = fixture.nativeElement;
    expect(root.querySelector('[data-row-id="201"]')?.textContent).toContain('除息日 2024-03-16');
    expect(root.querySelector('[data-row-id="202"]')?.textContent).toContain('除息日 2025-03-16');
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
    expect(root.querySelector('[data-row-id="201"]')?.textContent).toContain('每股 0.042 × 1,000');
    expect(root.querySelector('[data-row-id="202"]')?.textContent).toContain('每股 0.0421 × 1,000');
    expect(root.querySelector('[data-row-id="203"]')?.textContent).toContain('每股 0.0001');
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
    const component = fixture.componentInstance;
    component.openReceipt(pending);
    component.confirmReceipt();
    expect(service.confirmDividendReceipt).not.toHaveBeenCalled();
    component.receiptDate = '2026-09-11'; component.receiptAccountId = 7;
    component.confirmReceipt(); component.confirmReceipt();
    expect(service.confirmDividendReceipt).toHaveBeenCalledTimes(1);
    expect(service.confirmDividendReceipt).toHaveBeenCalledWith(1, { receipt_date: '2026-09-11', account_id: 7, revision: 3 });
    response.next({ ...pending, receipt_status: 'confirmed' }); response.complete();
    expect(component.receiptBusy()).toBe(false);
    expect(component.receiptTarget()).toBeNull();
    fixture.destroy();
  });
  it('does not offer confirmation for legacy or confirmed rows', () => {
    const fixture = TestBed.createComponent(PortfolioDividendListComponent);
    fixture.componentInstance.openReceipt({ ...pending, receipt_status: 'legacy_unknown' });
    expect(fixture.componentInstance.receiptTarget()).toBeNull();
    expect(service.getAccounts).not.toHaveBeenCalled();
    fixture.destroy();
  });
});

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
  let service: Record<string, ReturnType<typeof vi.fn>>;

  const confirmed: Dividend = { id: 11, symbol: '00878', amount: 37578, ex_dividend_date: '2026-08-17', receipt_status: 'confirmed', receipt_date: '2026-09-10' };
  const legacy: Dividend = { id: 12, symbol: '2330', amount: 21342, ex_dividend_date: '2026-06-12', receipt_status: 'legacy_unknown' };
  const due: Dividend = { id: 13, symbol: '0056', amount: 14600, ex_dividend_date: '2026-10-17', receipt_status: 'pending', revision: 2 };

  beforeEach(() => {
    listCalls = [];
    summaryCalls = [];
    service = {
      getSymbolNames: vi.fn(() => of({})),
      getUpcomingExDividends: vi.fn(() => of([])),
      getDividends: vi.fn((query: DividendQuery) => {
        const response = new Subject<Paged<Dividend>>();
        // The list pages by 25 in these tests; the filter-wide set always pages by 100.
        (query.limit === 100 ? summaryCalls : listCalls).push({ query, response });
        return response;
      }),
      confirmDividendReceipt: vi.fn(),
      createDividend: vi.fn(() => of({})),
    };
    TestBed.configureTestingModule({ imports: [PortfolioDividendListComponent], providers: [{ provide: PortfolioService, useValue: service }] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function create(): ComponentFixture<PortfolioDividendListComponent> {
    const fixture = TestBed.createComponent(PortfolioDividendListComponent);
    fixture.detectChanges();
    return fixture;
  }

  function el(fixture: ComponentFixture<PortfolioDividendListComponent>): HTMLElement {
    return fixture.nativeElement as HTMLElement;
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
    expect(fixture.componentInstance.dividendTotal()).toBe(37578);
    expect(fixture.componentInstance.legacyTotal()).toBe(21342);
    const items = Array.from(el(fixture).querySelectorAll('.summary-item')).map(item => item.textContent);
    expect(items[0]).toContain('$37,578');
    expect(items[1]).toContain('$21,342');
    fixture.destroy();
  });

  it('shows — in the summary row while the filter-wide set loads', () => {
    const fixture = create();
    respond(listCalls[0], { items: [confirmed], total: 3 });
    fixture.detectChanges();

    expect(fixture.componentInstance.summary()).toBeNull();
    const items = Array.from(el(fixture).querySelectorAll('.summary-item')).map(item => item.textContent);
    expect(items[0]).toContain('—');
    expect(items[1]).toContain('—');
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

    respond(summaryCalls[1], { items: [due], total: 1 });
    respond(summaryCalls[0], { items: [confirmed, legacy, due], total: 3 });
    fixture.detectChanges();

    expect(fixture.componentInstance.summary()).toMatchObject({ confirmed: 0, pending: 14600, legacy: 0, recorded: 0 });
    expect(fixture.componentInstance.summaryRows()).toEqual([due]);
    fixture.destroy();
  });

  it('hides the summary row when the filter-wide load fails, keeps the list page and raises no toast', () => {
    const fixture = create();
    const toast = vi.spyOn(fixture.debugElement.injector.get(MessageService), 'add');
    respond(listCalls[0], { items: [confirmed], total: 3 });
    summaryCalls[0].response.error(new Error('Synthetic failure'));
    fixture.detectChanges();

    expect(fixture.componentInstance.summaryFailed()).toBe(true);
    expect(fixture.componentInstance.summary()).toBeNull();
    expect(fixture.componentInstance.summaryRows()).toBeNull();
    expect(el(fixture).querySelector('.summary-row')).toBeNull();
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
    service['getAccounts'] = vi.fn(() => of({ items: [] }));
    service['confirmDividendReceipt'].mockReturnValueOnce(of({ ...due, receipt_status: 'confirmed' }))
      .mockReturnValueOnce(throwError(() => ({ error: { detail: 'Synthetic stale revision' } })));

    component.openReceipt(due);
    component.receiptDate = '2026-11-18'; component.receiptAccountId = 7;
    component.confirmReceipt();
    expect(summaryCalls).toHaveLength(2);

    component.openReceipt(due);
    component.receiptDate = '2026-11-18'; component.receiptAccountId = 7;
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
});
