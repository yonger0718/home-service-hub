import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { ReactiveFormsModule } from '@angular/forms';
import { provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { AccountsList, BrokerAccount } from '../../../models/portfolio.model';
import { PortfolioAccountsListComponent, cashTotal } from './accounts-list';

describe('PortfolioAccountsListComponent', () => {
  let httpMock: HttpTestingController;

  const activeAccounts: BrokerAccount[] = [
    {
      id: 1,
      broker: 'cathay',
      nickname: '國泰台幣',
      currency: 'TWD',
      opening_balance: '10000',
      opening_date: '2026-01-01',
      is_active: true,
      created_at: '2026-01-01T00:00:00Z',
      native_balance: '100000',
      target_balance: '100000',
      target_currency: 'TWD',
    },
    {
      id: 2,
      broker: 'firstrade',
      nickname: 'Firstrade USD',
      currency: 'USD',
      opening_balance: '1000',
      opening_date: '2026-01-02',
      is_active: true,
      created_at: '2026-01-02T00:00:00Z',
      native_balance: '1500',
      target_balance: '50000',
      target_currency: 'TWD',
    },
  ];

  beforeEach(async () => {
    localStorage.clear();

    await TestBed.configureTestingModule({
      imports: [PortfolioAccountsListComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    })
      .overrideComponent(PortfolioAccountsListComponent, {
        set: {
          template: `
            <section class="summary">{{ formatCurrency(accountsList().total_target_balance, 'TWD') }}</section>
            @if (accountsList().skipped_currencies.length > 0) {
              <small class="skipped">未換算: {{ accountsList().skipped_currencies.join(', ') }}</small>
            }
            @for (account of accounts(); track account.id) {
              <button type="button" class="account-card" (click)="goToDetail(account)">
                <span>{{ brokerLabel(account.broker) }}</span>
                <span>{{ account.nickname }}</span>
                <span>{{ account.currency }}</span>
                <span>{{ formatCurrency(account.native_balance, account.currency) }}</span>
              </button>
            }
            <button type="button" class="open-create" (click)="openCreateDialog()">新增帳戶</button>
            @if (createDialogVisible()) {
              <form [formGroup]="createForm" (ngSubmit)="submitCreate()" class="create-form">
                <input formControlName="broker" />
                <input formControlName="nickname" />
                <input formControlName="currency" />
                <input formControlName="opening_balance" />
                <input formControlName="opening_date" />
                <button type="submit">submit</button>
              </form>
            }
          `,
          imports: [ReactiveFormsModule],
        },
      })
      .compileComponents();

    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
    localStorage.clear();
  });

  function createFixture() {
    const fixture = TestBed.createComponent(PortfolioAccountsListComponent);
    fixture.detectChanges();
    return fixture;
  }

  function flushAccounts(accounts = activeAccounts, skippedCurrencies: string[] = []) {
    const req = httpMock.expectOne(request =>
      request.method === 'GET'
      && request.url === '/api/portfolio/accounts/'
      && request.params.get('in_currency') === 'TWD',
    );
    req.flush({
      items: accounts,
      target_currency: 'TWD',
      total_target_balance: '150000',
      skipped_currencies: skippedCurrencies,
    });
  }

  it('renders one card per active account from the accounts response', () => {
    const fixture = createFixture();

    flushAccounts();
    fixture.detectChanges();

    const cards = fixture.nativeElement.querySelectorAll('.account-card') as NodeListOf<HTMLButtonElement>;
    expect(cards.length).toBe(2);
    expect(cards[0].textContent).toContain('國泰證券');
    expect(cards[0].textContent).toContain('國泰台幣');
    expect(cards[0].textContent).toContain('TWD');
    expect(cards[0].textContent).toContain('NT$100,000');
    expect(cards[1].textContent).toContain('Firstrade');
  });

  it('shows the TWD converted total summary', () => {
    const fixture = createFixture();

    flushAccounts();
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.summary')?.textContent).toContain('NT$150,000');
  });

  it('renders skipped currency footnote', () => {
    const fixture = createFixture();

    flushAccounts(activeAccounts, ['JPY']);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.skipped')?.textContent).toContain('未換算: JPY');
  });

  it('submits create account form and refetches the list', () => {
    const fixture = createFixture();
    flushAccounts();
    fixture.detectChanges();

    (fixture.nativeElement.querySelector('.open-create') as HTMLButtonElement).click();
    fixture.detectChanges();

    fixture.componentInstance.createForm.patchValue({
      broker: 'sinopac',
      nickname: '永豐台幣',
      currency: 'TWD',
      opening_balance: '5000',
      opening_date: '2026-06-01',
    });
    (fixture.nativeElement.querySelector('.create-form') as HTMLFormElement)
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));

    const post = httpMock.expectOne('/api/portfolio/accounts/');
    expect(post.request.method).toBe('POST');
    expect(post.request.body).toEqual({
      broker: 'sinopac',
      nickname: '永豐台幣',
      currency: 'TWD',
      opening_balance: '5000',
      opening_date: '2026-06-01',
      is_active: true,
    });
    post.flush({
      id: 3,
      broker: 'sinopac',
      nickname: '永豐台幣',
      currency: 'TWD',
      opening_balance: '5000',
      opening_date: '2026-06-01',
      is_active: true,
      created_at: '2026-06-01T00:00:00Z',
      native_balance: '5000',
      target_balance: '5000',
      target_currency: 'TWD',
    });

    flushAccounts([...activeAccounts]);
    expect(fixture.componentInstance.createDialogVisible()).toBe(false);
  });
});

describe('PortfolioAccountsListComponent master mode', () => {
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

  function render(master: boolean, selectedId: number | null = null) {
    const fixture = TestBed.createComponent(PortfolioAccountsListComponent);
    fixture.componentRef.setInput('master', master);
    fixture.componentRef.setInput('selectedId', selectedId);
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
    const { fixture, emitted } = render(true);
    expect(emitted).toEqual([accounts]);

    fixture.componentInstance.fetchAccounts();
    httpMock.expectOne('/api/portfolio/accounts/?in_currency=TWD').flush({
      items: [], target_currency: 'TWD', total_target_balance: '0', skipped_currencies: [],
    });
    expect(emitted).toEqual([accounts, []]);
  });

  it('renders button cards with aria-pressed and the selected ring, without a page header', () => {
    const { el } = render(true, 2);

    expect(el.querySelector('app-page-header')).toBeNull();
    const cards = Array.from(el.querySelectorAll<HTMLButtonElement>('button.acct-card'));
    expect(cards.length).toBe(3);
    expect(cards.map(card => card.type)).toEqual(['button', 'button', 'button']);
    expect(cards.map(card => card.getAttribute('aria-pressed'))).toEqual(['false', 'true', 'false']);
    expect(cards[1].classList).toContain('selected');
    expect(el.querySelectorAll('.acct-card.selected').length).toBe(1);
  });

  it('shows the broker · currency line, with the opening balance on the selected card only', () => {
    const { el } = render(true, 1);
    const metas = Array.from(el.querySelectorAll('.acct-card-meta')).map(meta => meta.textContent?.trim());

    expect(metas).toEqual(['國泰證券 · TWD · 期初 300,000（2026/01/02）', 'Firstrade · USD', '永豐證券 · JPY']);
    expect(el.querySelectorAll('.acct-card')[0].textContent).toContain('國泰交割戶');
    expect(el.querySelectorAll('.acct-square .pi-wallet').length).toBe(3);
  });

  it('marks negative balances and tags 啟用中 / 已停用 / 未記錄入金', () => {
    const { el } = render(true);
    const balances = Array.from(el.querySelectorAll('.acct-card-balance'));
    const tags = Array.from(el.querySelectorAll('.acct-card')).map(card =>
      Array.from(card.querySelectorAll('app-tag')).map(tag => tag.textContent?.trim()));

    expect(balances.map(b => b.textContent?.trim())).toEqual(['100,000', '−1,500.50', '−300']);
    expect(balances.map(b => b.classList.contains('negative'))).toEqual([false, true, true]);
    expect(tags).toEqual([['啟用中'], ['未記錄入金', '啟用中'], ['未記錄入金', '已停用']]);
    expect(el.querySelector('.acct-card app-tag .tag-warning')?.textContent).toContain('未記錄入金');
  });

  it('total card leaves out overdraft accounts that have a TWD value', () => {
    const { fixture, el } = render(true);
    const total = el.querySelector('.acct-total') as HTMLElement;

    expect(total.querySelector('.acct-total-label')?.textContent).toBe('現金帳戶總額 · TWD');
    expect(total.querySelector('.acct-total-value')?.textContent).toBe('$150,000');
    expect(total.querySelector('.pending-note')?.textContent?.trim()).toBe('1 個帳戶待補入金紀錄，未計入');
    expect(total.textContent).toContain('未換算: JPY');
    expect(fixture.componentInstance.displayedTotal()).toBe(150000);
    expect(fixture.componentInstance.overdraftCount()).toBe(1);
  });

  it('keeps today\'s header and cards outside master mode', () => {
    const { el } = render(false);

    expect(el.querySelector('app-page-header')).not.toBeNull();
    expect(el.querySelector('.acct-master')).toBeNull();
    expect(el.querySelectorAll('.account-card').length).toBe(3);
    expect(el.querySelector('.summary-value')?.textContent).toContain('NT$100,000');
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
