import { TestBed } from '@angular/core/testing';
import { of, Subject, throwError } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PortfolioService } from '../../../services/portfolio.service';
import { PortfolioDividendListComponent } from './dividend-list';
import { Dividend } from '../../../models/portfolio.model';

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
  it('renders each mixed receipt fixture once with accurate page totals and pending-only action', () => {
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
    expect(root.textContent).toContain('本頁舊資料帳面合計');
    expect(root.textContent).toContain('本頁已確認收款合計');
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
