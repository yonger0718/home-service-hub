import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountsList, BrokerAccount } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';
import { CashFlowFormComponent } from '../cash-flow-form/cash-flow-form';
import { PortfolioAccountsListComponent, cashTotal } from './accounts-list';

describe('PortfolioAccountsListComponent', () => {
  let httpMock: HttpTestingController;

  const base: BrokerAccount = {
    id: 1,
    broker: 'cathay',
    nickname: '國泰交割戶',
    currency: 'TWD',
    opening_balance: '300000',
    opening_date: '2026-01-02',
    is_active: true,
    created_at: '2026-01-02T00:00:00Z',
    native_balance: '100000',
    target_balance: '100000',
    target_currency: 'TWD',
  };
  const accounts: BrokerAccount[] = [
    base,
    // Overdraft with a TWD value: in the server sum, taken back out.
    { ...base, id: 2, broker: 'firstrade', nickname: 'Firstrade', currency: 'USD', opening_balance: '0', native_balance: '-1500.5', target_balance: '-50000' },
    // Overdraft without a TWD value: already under 未換算, not counted.
    { ...base, id: 4, broker: 'sinopac', nickname: '永豐日圓', currency: 'JPY', native_balance: '-300', target_balance: null, target_currency: null, is_active: false },
  ];

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    });
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  /** Renders without the `master` input (the default is the master markup); `selectedId` only when given. */
  function render(selectedId?: number | null) {
    const fixture = TestBed.createComponent(PortfolioAccountsListComponent);
    if (selectedId !== undefined) fixture.componentRef.setInput('selectedId', selectedId);
    const emitted: BrokerAccount[][] = [];
    fixture.componentInstance.loaded.subscribe(items => emitted.push(items));
    fixture.detectChanges();
    httpMock.expectOne(req => req.url === '/api/portfolio/accounts/' && req.params.get('in_currency') === 'TWD').flush({
      items: accounts,
      target_currency: 'TWD',
      total_target_balance: '100000',
      skipped_currencies: ['JPY'],
    });
    fixture.detectChanges();
    return { fixture, el: fixture.nativeElement as HTMLElement, emitted };
  }

  it('emits loaded with the accounts after each fetch', () => {
    const { fixture, emitted } = render();
    expect(emitted).toEqual([accounts]);

    fixture.componentInstance.fetchAccounts();
    httpMock.expectOne('/api/portfolio/accounts/?in_currency=TWD').flush({
      items: [], target_currency: 'TWD', total_target_balance: '0', skipped_currencies: [],
    });
    expect(emitted).toEqual([accounts, []]);
  });

  it('renders button cards with aria-pressed and the selected ring, without a page header', () => {
    const { el } = render(2);

    expect(el.querySelector('app-page-header')).toBeNull();
    const cards = Array.from(el.querySelectorAll<HTMLButtonElement>('button.acct-card'));
    expect(cards.length).toBe(3);
    expect(cards.map(card => card.type)).toEqual(['button', 'button', 'button']);
    expect(cards.map(card => card.getAttribute('aria-pressed'))).toEqual(['false', 'true', 'false']);
    expect(cards[1].classList).toContain('selected');
    expect(el.querySelectorAll('.acct-card.selected').length).toBe(1);
  });

  it('renders the master markup by default, without the master input', () => {
    const { fixture, el } = render();

    expect(fixture.componentInstance.master()).toBe(true);
    expect(el.querySelector('app-page-header')).toBeNull();
    expect(el.querySelector('.acct-master .acct-total')).not.toBeNull();
    expect(el.querySelectorAll('button.acct-card').length).toBe(3);
    expect(el.querySelector('app-cash-flow-form')).not.toBeNull();
    expect(el.querySelector('p-dialog')).not.toBeNull();
  });

  it('leaves aria-pressed and the selected ring off every card without a selectedId (navigation buttons)', () => {
    for (const selectedId of [undefined, null]) {
      const { fixture, el } = render(selectedId);
      const cards = Array.from(el.querySelectorAll<HTMLButtonElement>('button.acct-card'));

      expect(cards.length).toBe(3);
      expect(cards.map(card => card.hasAttribute('aria-pressed'))).toEqual([false, false, false]);
      expect(el.querySelectorAll('.acct-card.selected').length).toBe(0);
      fixture.destroy();
    }
  });

  it('a card click navigates to the account detail', () => {
    const { el } = render();
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);

    el.querySelectorAll<HTMLButtonElement>('button.acct-card')[1].click();

    expect(navigate).toHaveBeenCalledWith(['/portfolio/accounts', 2]);
  });

  it('opens the create dialog, submits the form and refetches the list', () => {
    const { fixture, emitted } = render();
    const component = fixture.componentInstance;

    component.openCreateDialog();
    expect(component.createDialogVisible()).toBe(true);
    component.createForm.patchValue({
      broker: 'sinopac',
      nickname: ' 永豐台幣 ',
      currency: 'TWD',
      opening_balance: '5000',
      opening_date: '2026-06-01',
    });
    component.submitCreate();

    const post = httpMock.expectOne(req => req.method === 'POST' && req.url === '/api/portfolio/accounts/');
    expect(post.request.body).toEqual({
      broker: 'sinopac',
      nickname: '永豐台幣',
      currency: 'TWD',
      opening_balance: '5000',
      opening_date: '2026-06-01',
      is_active: true,
    });
    post.flush({ ...base, id: 3, broker: 'sinopac', nickname: '永豐台幣', opening_balance: '5000', opening_date: '2026-06-01' });

    httpMock.expectOne('/api/portfolio/accounts/?in_currency=TWD').flush({
      items: accounts, target_currency: 'TWD', total_target_balance: '100000', skipped_currencies: [],
    });
    expect(component.createDialogVisible()).toBe(false);
    expect(emitted.length).toBe(2);
  });

  it('shows the broker · currency line, with the opening balance on the selected card only', () => {
    const { el } = render(1);
    const metas = Array.from(el.querySelectorAll('.acct-card-meta')).map(meta => meta.textContent?.trim());

    expect(metas).toEqual(['國泰證券 · TWD · 期初 300,000（2026/01/02）', 'Firstrade · USD', '永豐證券 · JPY']);
    expect(el.querySelectorAll('.acct-card')[0].textContent).toContain('國泰交割戶');
    expect(el.querySelectorAll('.acct-square .pi-wallet').length).toBe(3);
  });

  it('marks negative balances and tags 啟用中 / 已停用 / 未記錄入金', () => {
    const { el } = render();
    const balances = Array.from(el.querySelectorAll('.acct-card-balance'));
    const tags = Array.from(el.querySelectorAll('.acct-card')).map(card =>
      Array.from(card.querySelectorAll('app-tag')).map(tag => tag.textContent?.trim()));

    expect(balances.map(b => b.textContent?.trim())).toEqual(['100,000', '−1,500.50', '−300']);
    expect(balances.map(b => b.classList.contains('negative'))).toEqual([false, true, true]);
    expect(tags).toEqual([['啟用中'], ['未記錄入金', '啟用中'], ['未記錄入金', '已停用']]);
    expect(el.querySelector('.acct-card app-tag .tag-warning')?.textContent).toContain('未記錄入金');
  });

  it('total card leaves out overdraft accounts that have a TWD value', () => {
    const { fixture, el } = render();
    const total = el.querySelector('.acct-total') as HTMLElement;

    expect(total.querySelector('.acct-total-label')?.textContent).toBe('現金帳戶總額 · TWD');
    expect(total.querySelector('.acct-total-value')?.textContent).toBe('$150,000');
    expect(total.querySelector('.pending-note')?.textContent?.trim()).toBe('1 個帳戶待補入金紀錄，未計入');
    expect(total.textContent).toContain('未換算: JPY');
    expect(fixture.componentInstance.displayedTotal()).toBe(150000);
    expect(fixture.componentInstance.overdraftCount()).toBe(1);
  });

  it('refetches on every cash-ledger change until destroyed', () => {
    const { fixture, emitted } = render();
    const service = TestBed.inject(PortfolioService);

    service.notifyCashLedgerChanged();
    httpMock.expectOne('/api/portfolio/accounts/?in_currency=TWD').flush({
      items: accounts.slice(0, 1), target_currency: 'TWD', total_target_balance: '100000', skipped_currencies: [],
    });
    expect(emitted).toEqual([accounts, accounts.slice(0, 1)]);

    fixture.destroy();
    service.notifyCashLedgerChanged();
    httpMock.expectNone('/api/portfolio/accounts/?in_currency=TWD');
  });

  it('destroy mid-fetch cancels the fetch: no loaded emit, no state change', () => {
    const { fixture, emitted } = render();
    const component = fixture.componentInstance;

    component.fetchAccounts();
    const req = httpMock.expectOne('/api/portfolio/accounts/?in_currency=TWD');
    fixture.destroy();

    expect(req.cancelled).toBe(true);
    expect(emitted).toEqual([accounts]);
    expect(component.accounts()).toEqual(accounts);
  });

  it('a second fetch cancels the first, so an older response cannot land last', () => {
    const { fixture, emitted } = render();
    const component = fixture.componentInstance;

    component.fetchAccounts();
    component.fetchAccounts();
    const [first, second] = httpMock.match('/api/portfolio/accounts/?in_currency=TWD');

    expect(first.cancelled).toBe(true);
    expect(second.cancelled).toBe(false);
    second.flush({ items: accounts.slice(0, 1), target_currency: 'TWD', total_target_balance: '100000', skipped_currencies: [] });
    expect(emitted).toEqual([accounts, accounts.slice(0, 1)]);
    expect(component.accounts()).toEqual(accounts.slice(0, 1));
  });

  it('a cash flow recorded in the form notifies the ledger change, which refetches once', () => {
    const { fixture } = render();
    const service = TestBed.inject(PortfolioService);
    const notify = vi.spyOn(service, 'notifyCashLedgerChanged');
    const form = fixture.debugElement.query(d => d.componentInstance instanceof CashFlowFormComponent)
      .componentInstance as CashFlowFormComponent;

    form.created.emit({ account_id: 1 } as never);

    expect(notify).toHaveBeenCalledTimes(1);
    const reqs = httpMock.match('/api/portfolio/accounts/?in_currency=TWD');
    expect(reqs.length).toBe(1);
    reqs[0].flush({ items: accounts, target_currency: 'TWD', total_target_balance: '100000', skipped_currencies: [] });
  });

});

describe('cashTotal', () => {
  const list = (items: Partial<BrokerAccount>[], total: string | null = '100000'): AccountsList => ({
    items: items as BrokerAccount[],
    target_currency: 'TWD',
    total_target_balance: total,
    skipped_currencies: [],
  });

  it('subtracts the TWD value of each overdraft account', () => {
    expect(cashTotal(list([
      { native_balance: '100000', target_balance: '100000' },
      { native_balance: '-1500', target_balance: '-50000' },
      { native_balance: '-10', target_balance: '-300' },
    ], '49700'))).toEqual({ total: 100000, pendingCount: 2 });
  });

  it('ignores overdrafts without a TWD value and positive balances', () => {
    expect(cashTotal(list([
      { native_balance: '-300', target_balance: null },
      { native_balance: '-300' },
      { native_balance: '0', target_balance: '0' },
    ]))).toEqual({ total: 100000, pendingCount: 0 });
  });

  it('treats a missing server total as 0', () => {
    expect(cashTotal(list([], null))).toEqual({ total: 0, pendingCount: 0 });
  });
});
