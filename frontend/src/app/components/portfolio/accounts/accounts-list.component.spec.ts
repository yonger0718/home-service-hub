import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { ReactiveFormsModule } from '@angular/forms';
import { provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { BrokerAccount } from '../../../models/portfolio.model';
import { PortfolioAccountsListComponent } from './accounts-list';

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
