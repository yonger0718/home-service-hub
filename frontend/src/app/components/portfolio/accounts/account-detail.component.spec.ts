import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { ReactiveFormsModule } from '@angular/forms';
import { provideRouter } from '@angular/router';
import { ActivatedRoute } from '@angular/router';
import { of } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConfirmationService, MessageService } from 'primeng/api';

import { PortfolioAccountDetailComponent } from './account-detail';
import { CashTransaction } from '../../../models/portfolio.model';

describe('PortfolioAccountDetailComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-06-02T12:00:00Z'));
    localStorage.clear();

    await TestBed.configureTestingModule({
      imports: [PortfolioAccountDetailComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { paramMap: of(new Map([['id', '1']])) } },
      ],
    })
      .overrideComponent(PortfolioAccountDetailComponent, {
        set: {
          template: `
            <p class="account-name">{{ account()?.nickname }}</p>
            <button type="button" class="window-1y" (click)="onWindowChange('1Y')">1Y</button>
            <button type="button" class="manual-open" (click)="openTransactionDialog()">新增交易</button>
            <label>
              合併同筆交易
              <input
                class="merge-toggle"
                type="checkbox"
                [checked]="mergeRelated()"
                (change)="onMergeRelatedChange($any($event.target).checked)"
              />
            </label>
            <section class="transaction-list">
              @for (txn of transactions(); track transactionKey(txn)) {
                <article class="txn-row">
                  @if (hasChildLegs(txn)) {
                    <button type="button" class="leg-toggle" (click)="toggleTransactionLegs(txn.id)">
                      {{ isTransactionExpanded(txn.id) ? '收合' : '展開' }}
                    </button>
                    <span class="leg-count">{{ txn.child_legs?.length }} 筆</span>
                  }
                  <span class="type">{{ typeLabel(txn.type) }}</span>
                  <span class="amount">{{ formatCurrency(txn.amount, txn.currency) }}</span>
                  @if (txn.source === 'manual') {
                    <button type="button" class="delete-transaction" (click)="confirmDelete(txn)">
                      delete
                    </button>
                  }
                  @if (isTransactionExpanded(txn.id)) {
                    <div class="child-legs">
                      @for (leg of txn.child_legs ?? []; track transactionKey(leg)) {
                        <div class="child-leg">
                          <span>{{ typeLabel(leg.type) }}</span>
                          <span>{{ formatCurrency(leg.amount, leg.currency) }}</span>
                        </div>
                      }
                    </div>
                  }
                </article>
              }
            </section>
            @if (transactionDialogVisible()) {
              <form [formGroup]="transactionForm" (ngSubmit)="submitTransaction()" class="transaction-form">
                <input formControlName="txn_date" />
                <input formControlName="type" />
                <input formControlName="amount" />
                <input formControlName="note" />
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
    vi.useRealTimers();
    localStorage.clear();
  });

  function createFixture() {
    const fixture = TestBed.createComponent(PortfolioAccountDetailComponent);
    fixture.detectChanges();
    return fixture;
  }

  function flushInitial(mergeRelated: string | null = null) {
    const accountsReq = httpMock.expectOne(request =>
      request.method === 'GET'
      && request.url === '/api/portfolio/accounts/'
      && request.params.get('include_inactive') === 'true',
    );
    accountsReq.flush({
      items: [{
        id: 1,
        broker: 'cathay',
        nickname: '國泰台幣',
        currency: 'TWD',
        opening_balance: '10000',
        opening_date: '2026-01-01',
        is_active: true,
        created_at: '2026-01-01T00:00:00Z',
        native_balance: '11000',
        target_balance: '11000',
        target_currency: 'TWD',
      }],
      target_currency: null,
      total_target_balance: null,
      skipped_currencies: [],
    });

    const historyReq = httpMock.expectOne(request =>
      request.method === 'GET'
      && request.url === '/api/portfolio/accounts/1/balance-history'
      && request.params.get('date_from') === '2026-03-04'
      && request.params.get('date_to') === '2026-06-02',
    );
    historyReq.flush({
      account_id: 1,
      currency: 'TWD',
      points: [{ date: '2026-06-02', balance: '11000' }],
    });

    const txnsReq = httpMock.expectOne(request =>
      request.method === 'GET'
      && request.url === '/api/portfolio/accounts/1/cash-transactions'
      && request.params.get('sort') === 'txn_date:desc'
      && request.params.get('offset') === '0'
      && request.params.get('limit') === '25'
      && request.params.get('merge_related') === mergeRelated,
    );
    txnsReq.flush({
      items: [],
      total: 0,
      offset: 0,
      limit: 25,
    });
  }

  function cashTxn(patch: Partial<CashTransaction>): CashTransaction {
    return {
      id: patch.id ?? 1,
      account_id: patch.account_id ?? 1,
      txn_date: patch.txn_date ?? '2026-06-01',
      type: patch.type ?? 'deposit',
      amount: patch.amount ?? '0',
      currency: patch.currency ?? 'TWD',
      note: patch.note ?? null,
      related_transaction_id: patch.related_transaction_id ?? null,
      related_dividend_id: patch.related_dividend_id ?? null,
      source: patch.source ?? 'auto_derive',
      import_fingerprint: patch.import_fingerprint ?? `fp-${patch.id ?? 1}`,
      created_at: patch.created_at ?? '2026-06-01T00:00:00Z',
      child_legs: patch.child_legs ?? null,
    };
  }

  it('loads account metadata, cash transactions, and default 3M balance history on init', () => {
    createFixture();

    flushInitial();
  });

  it('defaults merge related OFF for a new account', () => {
    const fixture = createFixture();

    flushInitial();

    expect(fixture.componentInstance.mergeRelated()).toBe(false);
    expect(localStorage.getItem('accounts.merge.1')).toBeNull();
    const toggle = fixture.debugElement.query(By.css('.merge-toggle')).nativeElement as HTMLInputElement;
    expect(toggle.checked).toBe(false);
  });

  it('initializes merge related from account-specific localStorage and sends the first merged query', () => {
    localStorage.setItem('accounts.merge.1', '1');

    const fixture = createFixture();

    flushInitial('true');

    expect(fixture.componentInstance.mergeRelated()).toBe(true);
    const toggle = fixture.debugElement.query(By.css('.merge-toggle')).nativeElement as HTMLInputElement;
    expect(toggle.checked).toBe(true);
  });

  it('refetches balance history when the selected window changes', () => {
    const fixture = createFixture();
    flushInitial();

    fixture.componentInstance.onWindowChange('1Y');

    const req = httpMock.expectOne(request =>
      request.method === 'GET'
      && request.url === '/api/portfolio/accounts/1/balance-history'
      && request.params.get('date_from') === '2025-06-02'
      && request.params.get('date_to') === '2026-06-02',
    );
    req.flush({ account_id: 1, currency: 'TWD', points: [] });
  });

  it('submits manual transaction with normalized body and refreshes list plus chart', () => {
    const fixture = createFixture();
    flushInitial();

    fixture.componentInstance.openTransactionDialog();
    fixture.componentInstance.transactionForm.patchValue({
      txn_date: '2026-06-01',
      type: 'deposit',
      amount: '5000',
      note: '入金',
    });
    fixture.componentInstance.submitTransaction();

    const post = httpMock.expectOne('/api/portfolio/accounts/1/cash-transactions');
    expect(post.request.method).toBe('POST');
    expect(post.request.body).toEqual({
      txn_date: '2026-06-01',
      type: 'deposit',
      amount: '5000',
      currency: 'TWD',
      note: '入金',
    });
    post.flush({
      id: 1,
      account_id: 1,
      txn_date: '2026-06-01',
      type: 'deposit',
      amount: '5000',
      currency: 'TWD',
      note: '入金',
      source: 'manual',
      import_fingerprint: 'fp',
      created_at: '2026-06-01T00:00:00Z',
    });

    httpMock.expectOne('/api/portfolio/accounts/1/balance-history?date_from=2026-03-04&date_to=2026-06-02')
      .flush({ account_id: 1, currency: 'TWD', points: [] });
    httpMock.expectOne(request =>
      request.method === 'GET'
      && request.url === '/api/portfolio/accounts/1/cash-transactions'
      && request.params.get('sort') === 'txn_date:desc'
      && request.params.get('offset') === '0'
      && request.params.get('limit') === '25',
    ).flush({ items: [], total: 0, offset: 0, limit: 25 });
  });

  it('refetches cash transactions with new offset and persisted limit on paginator change', () => {
    const fixture = createFixture();
    flushInitial();

    fixture.componentInstance.onPageChange({ first: 50, rows: 50 });

    expect(localStorage.getItem('portfolio.cashTxns.pageSize')).toBe('50');
    const req = httpMock.expectOne(request =>
      request.method === 'GET'
      && request.url === '/api/portfolio/accounts/1/cash-transactions'
      && request.params.get('offset') === '50'
      && request.params.get('limit') === '50',
    );
    req.flush({ items: [], total: 0, offset: 50, limit: 50 });
  });

  it('persists merge toggle, resets pagination, and refetches through the cash query stream', () => {
    const fixture = createFixture();
    flushInitial();

    fixture.componentInstance.onPageChange({ first: 50, rows: 50 });
    httpMock.expectOne(request =>
      request.method === 'GET'
      && request.url === '/api/portfolio/accounts/1/cash-transactions'
      && request.params.get('offset') === '50'
      && request.params.get('limit') === '50',
    ).flush({ items: [], total: 0, offset: 50, limit: 50 });

    fixture.componentInstance.onMergeRelatedChange(true);

    expect(localStorage.getItem('accounts.merge.1')).toBe('1');
    expect(fixture.componentInstance.query().offset).toBe(0);
    vi.advanceTimersByTime(300);
    const req = httpMock.expectOne(request =>
      request.method === 'GET'
      && request.url === '/api/portfolio/accounts/1/cash-transactions'
      && request.params.get('merge_related') === 'true'
      && request.params.get('offset') === '0'
      && request.params.get('limit') === '50',
    );
    req.flush({ items: [], total: 0, offset: 0, limit: 50 });
  });

  it('expands merged trade rows inline to show child legs', () => {
    const fixture = createFixture();
    flushInitial();

    fixture.componentInstance.transactions.set([
      cashTxn({
        id: -42,
        type: 'trade',
        amount: '-100585',
        related_transaction_id: 42,
        child_legs: [
          cashTxn({ id: 101, type: 'buy_settle', amount: '-100000' }),
          cashTxn({ id: 102, type: 'fee', amount: '-285' }),
          cashTxn({ id: 103, type: 'tax', amount: '-300' }),
        ],
      }),
    ]);
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('交易');
    expect(fixture.nativeElement.textContent).toContain('3 筆');
    expect(fixture.nativeElement.textContent).not.toContain('買進交割');

    fixture.debugElement.query(By.css('.leg-toggle')).nativeElement.click();
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('買進交割');
    expect(fixture.nativeElement.textContent).toContain('手續費');
    expect(fixture.nativeElement.textContent).toContain('交易稅');
    expect(fixture.nativeElement.textContent).toContain('-NT$100,000');
  });

  it('debounces cash transaction filters before refetching', () => {
    const fixture = createFixture();
    flushInitial();

    fixture.componentInstance.onTypeFilterChange(['deposit']);

    vi.advanceTimersByTime(299);
    httpMock.expectNone(request =>
      request.method === 'GET'
      && request.url === '/api/portfolio/accounts/1/cash-transactions'
      && request.params.get('type') === 'deposit',
    );

    vi.advanceTimersByTime(1);
    const req = httpMock.expectOne(request =>
      request.method === 'GET'
      && request.url === '/api/portfolio/accounts/1/cash-transactions'
      && request.params.get('type') === 'deposit',
    );
    req.flush({ items: [], total: 0, offset: 0, limit: 25 });
  });

  it('shows trash icon only on manual cash transactions', () => {
    const fixture = createFixture();
    flushInitial();

    fixture.componentInstance.transactions.set([
      cashTxn({ id: 41, source: 'auto_derive' }),
      cashTxn({ id: 42, source: 'manual' }),
    ]);
    fixture.detectChanges();

    const deleteButtons = fixture.debugElement.queryAll(By.css('.delete-transaction'));
    expect(deleteButtons).toHaveLength(1);
  });

  it('opens confirmation dialog with row context when trash icon is clicked', () => {
    const fixture = createFixture();
    flushInitial();
    const confirmationService = fixture.debugElement.injector.get(ConfirmationService);
    const confirmSpy = vi.spyOn(confirmationService, 'confirm');

    fixture.componentInstance.transactions.set([
      cashTxn({
        id: 42,
        txn_date: '2026-06-03',
        type: 'deposit',
        amount: '10000',
        currency: 'TWD',
        note: 'testing',
        source: 'manual',
      }),
    ]);
    fixture.detectChanges();
    fixture.debugElement.query(By.css('.delete-transaction')).nativeElement.click();

    expect(confirmSpy).toHaveBeenCalledWith(expect.objectContaining({
      header: '刪除交易',
      message: '入金 +10,000 TWD on 2026-06-03 — testing',
      icon: 'pi pi-exclamation-triangle',
      acceptLabel: '刪除',
      rejectLabel: '取消',
      acceptButtonStyleClass: 'p-button-danger',
      accept: expect.any(Function),
    }));
  });

  it('accepting confirmation deletes the cash transaction and refreshes list, history, and account summary', () => {
    const fixture = createFixture();
    flushInitial();
    const confirmationService = fixture.debugElement.injector.get(ConfirmationService);
    vi.spyOn(confirmationService, 'confirm').mockImplementation(options => options.accept?.());
    const messageService = fixture.debugElement.injector.get(MessageService);
    const toastSpy = vi.spyOn(messageService, 'add');

    fixture.componentInstance.query.set({ offset: 50, limit: 50, sort: 'amount:asc' });
    fixture.componentInstance.transactions.set([
      cashTxn({ id: 42, amount: '10000', source: 'manual' }),
    ]);
    fixture.detectChanges();
    fixture.debugElement.query(By.css('.delete-transaction')).nativeElement.click();

    const deleteReq = httpMock.expectOne('/api/portfolio/accounts/1/cash-transactions/42');
    expect(deleteReq.request.method).toBe('DELETE');
    deleteReq.flush({ deleted_id: 42 });

    httpMock.expectOne(request =>
      request.method === 'GET'
      && request.url === '/api/portfolio/accounts/1/cash-transactions'
      && request.params.get('offset') === '50'
      && request.params.get('limit') === '50'
      && request.params.get('sort') === 'amount:asc',
    ).flush({ items: [], total: 0, offset: 50, limit: 50 });
    httpMock.expectOne('/api/portfolio/accounts/1/balance-history?date_from=2026-03-04&date_to=2026-06-02')
      .flush({ account_id: 1, currency: 'TWD', points: [] });
    httpMock.expectOne(request =>
      request.method === 'GET'
      && request.url === '/api/portfolio/accounts/'
      && request.params.get('include_inactive') === 'true',
    ).flush({
      items: [{
        id: 1,
        broker: 'cathay',
        nickname: '國泰台幣',
        currency: 'TWD',
        opening_balance: '10000',
        opening_date: '2026-01-01',
        is_active: true,
        created_at: '2026-01-01T00:00:00Z',
        native_balance: '1000',
        target_balance: '1000',
        target_currency: 'TWD',
      }],
      target_currency: null,
      total_target_balance: null,
      skipped_currencies: [],
    });

    expect(toastSpy).toHaveBeenCalledWith(expect.objectContaining({ severity: 'success', detail: '已刪除' }));
  });

  it('surfaces delete errors and keeps the row visible', () => {
    const fixture = createFixture();
    flushInitial();
    const confirmationService = fixture.debugElement.injector.get(ConfirmationService);
    vi.spyOn(confirmationService, 'confirm').mockImplementation(options => options.accept?.());
    const messageService = fixture.debugElement.injector.get(MessageService);
    const toastSpy = vi.spyOn(messageService, 'add');
    const manualTxn = cashTxn({ id: 42, amount: '10000', source: 'manual' });

    fixture.componentInstance.transactions.set([manualTxn]);
    fixture.detectChanges();
    fixture.debugElement.query(By.css('.delete-transaction')).nativeElement.click();

    const deleteReq = httpMock.expectOne('/api/portfolio/accounts/1/cash-transactions/42');
    deleteReq.flush({ detail: 'only manual cash transactions can be deleted' }, { status: 403, statusText: 'Forbidden' });
    fixture.detectChanges();

    httpMock.expectNone('/api/portfolio/accounts/1/cash-transactions?sort=txn_date:desc&offset=0&limit=25');
    expect(fixture.componentInstance.transactions()).toEqual([manualTxn]);
    expect(fixture.debugElement.queryAll(By.css('.delete-transaction'))).toHaveLength(1);
    expect(toastSpy).toHaveBeenCalledWith(expect.objectContaining({
      severity: 'error',
      detail: '刪除失敗: only manual cash transactions can be deleted',
    }));
  });
});
