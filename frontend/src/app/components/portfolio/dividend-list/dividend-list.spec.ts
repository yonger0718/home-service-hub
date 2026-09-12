import { TestBed } from '@angular/core/testing';
import { of, Subject } from 'rxjs';
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
    expect(fixture.nativeElement.textContent).toContain('預定發放日 未知');
    expect(fixture.componentInstance.dividendTotal()).toBe(0);
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
