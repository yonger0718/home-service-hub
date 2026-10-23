import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { ReactiveFormsModule } from '@angular/forms';
import { provideRouter } from '@angular/router';
import { ActivatedRoute } from '@angular/router';
import { BehaviorSubject, of } from 'rxjs';
import { ChartModule } from 'primeng/chart';
import { Component, Input } from '@angular/core';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConfirmationService, MessageService } from 'primeng/api';

import { PortfolioAccountDetailComponent } from './account-detail';
import { CashTransaction } from '../../../models/portfolio.model';
import { LayoutModeService } from '../../../services/layout-mode.service';

@Component({ selector: 'p-chart', template: '' })
class ChartStubComponent {
  @Input() type = '';
  @Input() data: unknown;
  @Input() options: unknown;
}

function mergeSwitch(root: HTMLElement): HTMLButtonElement {
  return root.querySelector<HTMLButtonElement>('button[role="switch"][aria-label="合併同筆交易"]')!;
}

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
        remove: { imports: [ChartModule] },
        add: { imports: [ChartStubComponent] },
      })
      .compileComponents();

    httpMock = TestBed.inject(HttpTestingController);
    TestBed.inject(LayoutModeService).set('panes');
  });

  afterEach(() => {
    httpMock.verify();
    TestBed.inject(LayoutModeService).set(null);
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
    fixture.detectChanges();
    expect(mergeSwitch(fixture.nativeElement).getAttribute('aria-checked')).toBe('false');
  });

  it('initializes merge related from account-specific localStorage and sends the first merged query', () => {
    localStorage.setItem('accounts.merge.1', '1');

    const fixture = createFixture();

    flushInitial('true');

    expect(fixture.componentInstance.mergeRelated()).toBe(true);
    fixture.detectChanges();
    expect(mergeSwitch(fixture.nativeElement).getAttribute('aria-checked')).toBe('true');
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
    httpMock.expectOne(request =>
      request.method === 'GET'
      && request.url === '/api/portfolio/accounts/'
      && request.params.get('include_inactive') === 'true',
    ).flush({ items: [], target_currency: null, total_target_balance: null, skipped_currencies: [] });
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

    fixture.detectChanges();
    mergeSwitch(fixture.nativeElement).click();

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

    const row = fixture.nativeElement.querySelector('.cash-row') as HTMLElement;
    expect(row.querySelector('.title')?.textContent).toContain('交割');
    expect(row.querySelector('.sub')?.textContent).toContain('已合併買賣 3 筆');
    expect(row.querySelectorAll('.cash-leg').length).toBe(0);
    const toggle = row.querySelector('.leg-toggle') as HTMLButtonElement;
    expect(toggle.getAttribute('aria-expanded')).toBe('false');

    toggle.click();
    fixture.detectChanges();

    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    const legs = Array.from(row.querySelectorAll('.cash-leg')).map(leg =>
      [leg.querySelector('.title')?.textContent?.trim(), leg.querySelector('.amt')?.textContent?.trim()]);
    expect(legs).toEqual([['交割 · 買進', '−100,000'], ['手續費', '−285'], ['交易稅', '−300']]);
    expect(row.textContent).not.toContain('NT$');
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

describe('PortfolioAccountDetailComponent accountId / embedded inputs', () => {
  let httpMock: HttpTestingController;
  let params: BehaviorSubject<Map<string, string>>;

  beforeEach(() => {
    localStorage.clear();
    params = new BehaviorSubject(new Map<string, string>());
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { paramMap: params } },
      ],
    }).overrideComponent(PortfolioAccountDetailComponent, {
      remove: { imports: [ChartModule] },
      add: { imports: [ChartStubComponent] },
    });
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
    localStorage.clear();
  });

  function render(inputs: { accountId?: number | null; embedded?: boolean } = {}) {
    const fixture = TestBed.createComponent(PortfolioAccountDetailComponent);
    if (inputs.accountId !== undefined) fixture.componentRef.setInput('accountId', inputs.accountId);
    if (inputs.embedded !== undefined) fixture.componentRef.setInput('embedded', inputs.embedded);
    fixture.detectChanges();
    return fixture;
  }

  /** Flushes the three loads for `id` and returns how many sets were sent; fails on a load for any other account. */
  function flushLoads(id: number): number {
    const summaries = httpMock.match(req => req.url === '/api/portfolio/accounts/' && req.params.get('include_inactive') === 'true');
    const histories = httpMock.match(req => req.url === `/api/portfolio/accounts/${id}/balance-history`);
    const txns = httpMock.match(req => req.url === `/api/portfolio/accounts/${id}/cash-transactions`);
    expect(summaries.length).toBe(histories.length);
    expect(txns.length).toBe(histories.length);
    summaries.forEach(req => req.flush({
      items: [{
        id, broker: 'cathay', nickname: `帳戶 ${id}`, currency: 'TWD', opening_balance: '0', opening_date: '2026-01-01',
        is_active: true, created_at: '2026-01-01T00:00:00Z', native_balance: '100', target_balance: '100', target_currency: 'TWD',
      }],
      target_currency: null,
      total_target_balance: null,
      skipped_currencies: [],
    }));
    histories.forEach(req => req.flush({ account_id: id, currency: 'TWD', points: [] }));
    txns.forEach(req => req.flush({ items: [], total: 0, offset: 0, limit: 25 }));
    return histories.length;
  }

  it('loads the accountId input without a route param and reloads once when it changes', () => {
    const fixture = render({ accountId: 7 });
    expect(flushLoads(7)).toBe(1);
    fixture.detectChanges();
    expect(fixture.componentInstance.account()?.nickname).toBe('帳戶 7');

    fixture.componentRef.setInput('accountId', 9);
    fixture.detectChanges();
    expect(flushLoads(9)).toBe(1);
    expect(flushLoads(7)).toBe(0);

    fixture.detectChanges();
    expect(flushLoads(9)).toBe(0);
  });

  it('prefers the accountId input over the route id', () => {
    params.next(new Map([['id', '3']]));
    render({ accountId: 7 });
    expect(flushLoads(7)).toBe(1);
    expect(flushLoads(3)).toBe(0);
  });

  it('loads nothing without an input or a route id', () => {
    render();
    expect(httpMock.match(() => true).length).toBe(0);
  });

  it('follows the route id when accountId is null, reloading once per id change', () => {
    params.next(new Map([['id', '1']]));
    const fixture = render();
    expect(flushLoads(1)).toBe(1);

    params.next(new Map([['id', '2']]));
    fixture.detectChanges();
    expect(flushLoads(2)).toBe(1);
    expect(flushLoads(1)).toBe(0);
  });

  it('hides the page header when embedded and keeps it otherwise', () => {
    const embedded = render({ accountId: 7, embedded: true });
    flushLoads(7);
    embedded.detectChanges();
    const el = embedded.nativeElement as HTMLElement;
    expect(el.querySelector('app-page-header')).toBeNull();
    expect(el.querySelector('h2')?.textContent).toContain('帳戶 7');
    expect(el.textContent).toContain('餘額走勢');

    const routed = render({ accountId: 7 });
    flushLoads(7);
    routed.detectChanges();
    expect((routed.nativeElement as HTMLElement).querySelector('app-page-header')).not.toBeNull();
  });
});

describe('PortfolioAccountDetailComponent cash rows and 餘額', () => {
  let httpMock: HttpTestingController;
  let layout: LayoutModeService;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-22T04:00:00Z'));
    localStorage.clear();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { paramMap: of(new Map([['id', '7']])) } },
      ],
    }).overrideComponent(PortfolioAccountDetailComponent, {
      remove: { imports: [ChartModule] },
      add: { imports: [ChartStubComponent] },
    });
    httpMock = TestBed.inject(HttpTestingController);
    layout = TestBed.inject(LayoutModeService);
    layout.set('panes');
  });

  afterEach(() => {
    httpMock.verify();
    layout.set(null);
    vi.useRealTimers();
    localStorage.clear();
  });

  function txn(patch: Partial<CashTransaction>): CashTransaction {
    return {
      id: 1, account_id: 7, txn_date: '2026-10-20', type: 'deposit', amount: '0', currency: 'TWD', note: null,
      related_transaction_id: null, related_dividend_id: null, child_legs: null, source: 'auto_derive',
      import_fingerprint: `fp-${patch.id ?? 1}`, created_at: '2026-10-20T00:00:00Z',
      ...patch,
    };
  }

  /** The spec scenario: current 438,360, newest row a +20,000 deposit. */
  const SCENARIO: CashTransaction[] = [
    txn({ id: 1, txn_date: '2026-10-20', type: 'deposit', amount: '20000.00', source: 'manual', note: '從台新 Richart 轉入' }),
    txn({
      id: 2, txn_date: '2026-10-16', type: 'buy_settle', amount: '-110407.00', related_transaction_id: 55,
      related_symbol: '00878', related_quantity: '5000.0000', related_trade_date: '2026-10-15',
    }),
    txn({
      id: 3, txn_date: '2026-10-08', type: 'dividend_cash', amount: '17390.00', related_dividend_id: 9,
      related_symbol: '2317', related_per_share: '5.8000', related_quantity: '3000.0000', related_fee: '10.00', related_tax: '0.00',
    }),
  ];

  function render() {
    const fixture = TestBed.createComponent(PortfolioAccountDetailComponent);
    fixture.detectChanges();
    return fixture;
  }

  function flushAccount() {
    httpMock.expectOne(req => req.url === '/api/portfolio/accounts/' && req.params.get('include_inactive') === 'true').flush({
      items: [{
        id: 7, broker: 'cathay', nickname: '元大交割戶', currency: 'TWD', opening_balance: '0', opening_date: '2026-01-01',
        is_active: true, created_at: '2026-01-01T00:00:00Z', native_balance: '438360', target_balance: '438360', target_currency: 'TWD',
      }],
      target_currency: null, total_target_balance: null, skipped_currencies: [],
    });
    httpMock.expectOne(req => req.url === '/api/portfolio/accounts/7/balance-history').flush({ account_id: 7, currency: 'TWD', points: [] });
  }

  function flushTxns(items: CashTransaction[], check: (params: URLSearchParams) => boolean = () => true) {
    const req = httpMock.expectOne(r => r.url === '/api/portfolio/accounts/7/cash-transactions'
      && check(new URLSearchParams(r.params.toString())));
    req.flush({ items, total: 60, offset: Number(req.request.params.get('offset') ?? 0), limit: 25 });
  }

  function load(items: CashTransaction[] = SCENARIO) {
    const fixture = render();
    flushAccount();
    flushTxns(items);
    fixture.detectChanges();
    return fixture;
  }

  const el = (fixture: { nativeElement: unknown }) => fixture.nativeElement as HTMLElement;
  const text = (node: Element | null | undefined) => node?.textContent?.trim() ?? null;
  const heads = (fixture: { nativeElement: unknown }) =>
    Array.from(el(fixture).querySelectorAll('.cash-head span')).map(span => text(span));
  const balanceCells = (fixture: { nativeElement: unknown }) =>
    Array.from(el(fixture).querySelectorAll('.cash-row .c-bal')).map(cell => text(cell));

  it('renders the heads and one row per transaction with date, icon, title / subtitle, source, amount and 餘額', () => {
    const fixture = load();
    const root = el(fixture);

    expect(heads(fixture)).toEqual(['日期', '項目', '來源', '金額', '餘額']);
    expect(root.querySelector('.cash-head')?.getAttribute('aria-hidden')).toBe('true');
    expect(text(root.querySelector('.cash-list .visually-hidden'))).toBe('現金交易，欄位：日期、項目、來源、金額、餘額');
    const rows = Array.from(root.querySelectorAll<HTMLElement>('.cash-row'));
    expect(rows.length).toBe(3);

    const [deposit, settle, dividend] = rows;
    expect(text(deposit.querySelector('.c-date'))).toBe('2026/10/20');
    expect(deposit.querySelector('.cash-icon i')?.classList).toContain('pi-arrow-down');
    expect(text(deposit.querySelector('.title'))).toBe('入金');
    expect(text(deposit.querySelector('.sub'))).toBe('從台新 Richart 轉入');
    expect(text(deposit.querySelector('.c-src app-tag'))).toBe('手動');
    expect(deposit.querySelector('.c-src .tag')?.classList).toContain('tag-neutral');
    expect(text(deposit.querySelector('.amt'))).toBe('+20,000');
    expect(deposit.querySelector('.amt')?.classList).toContain('up');

    expect(text(settle.querySelector('.c-date'))).toBe('2026/10/16');
    expect(settle.querySelector('.cash-icon i')?.classList).toContain('pi-shopping-cart');
    expect(text(settle.querySelector('.title'))).toBe('交割 · 買進 00878');
    expect(text(settle.querySelector('.sub'))).toBe('5,000 股 · 10/15 成交');
    expect(text(settle.querySelector('.c-src app-tag'))).toBe('交易');
    expect(settle.querySelector('.c-src .tag')?.classList).toContain('tag-primary');
    expect(text(settle.querySelector('.amt'))).toBe('−110,407');
    expect(settle.querySelector('.amt')?.classList).toContain('down');

    expect(text(dividend.querySelector('.title'))).toBe('股利 · 2317');
    expect(text(dividend.querySelector('.sub'))).toBe('5.80 × 3,000 · 扣匯費 10');
    expect(text(dividend.querySelector('.c-src app-tag'))).toBe('股利');
    expect(dividend.querySelector('.c-src .tag')?.classList).toContain('tag-dividend');

    expect(balanceCells(fixture)).toEqual(['438,360', '418,360', '528,767']);
    expect(text(root.querySelector('.cash-list'))).not.toContain('NT$');
    // Manual rows keep delete; derived rows do not.
    expect(deposit.querySelector('.delete-transaction')?.getAttribute('aria-label')).toBe('刪除交易 入金');
    expect(settle.querySelector('.delete-transaction')).toBeNull();
    expect(dividend.querySelector('.delete-transaction')).toBeNull();
  });

  it('keeps 合併同筆交易 (switch), the filters and 新增交易 in the header row', () => {
    const root = el(load());
    const header = root.querySelector('.transactions-header')!;
    const toggle = header.querySelector('button[role="switch"]');
    expect(toggle?.getAttribute('aria-label')).toBe('合併同筆交易');
    expect(header.textContent).toContain('合併同筆交易');
    expect(header.textContent).toContain('新增交易');
    expect(root.querySelector('.hub-filter-bar p-datepicker')).not.toBeNull();
    expect(root.querySelector('.hub-filter-bar p-multiselect')).not.toBeNull();
    expect(root.querySelector('.hub-filter-bar p-select')).not.toBeNull();
    expect(root.querySelector('p-toggleswitch')).toBeNull();
  });

  it('a merged parent moves the balance by its net, not its legs', () => {
    const fixture = load([
      txn({
        id: -42, txn_date: '2026-10-17', type: 'trade', amount: '-100585', related_transaction_id: 42, related_symbol: '2330',
        child_legs: [txn({ id: 101, type: 'buy_settle', amount: '-100000' }), txn({ id: 102, type: 'fee', amount: '-585' })],
      }),
      txn({ id: 5, txn_date: '2026-10-10', amount: '5000', source: 'manual' }),
    ]);
    expect(balanceCells(fixture)).toEqual(['438,360', '538,945']);
    const merged = el(fixture).querySelector('.cash-row')!;
    expect(text(merged.querySelector('.title'))).toBe('交割 · 2330');
    expect(merged.querySelector('.delete-transaction')).toBeNull();
    expect(merged.querySelector('.leg-toggle')).not.toBeNull();
  });

  it('page 2 (offset 25) has no 餘額 head or cells, and page 1 brings them back only when its rows arrive', () => {
    const fixture = load();
    fixture.componentInstance.onPageChange({ first: 25, rows: 25 });
    flushTxns(SCENARIO, params => params.get('offset') === '25');
    fixture.detectChanges();
    expect(heads(fixture)).toEqual(['日期', '項目', '來源', '金額']);
    expect(balanceCells(fixture)).toEqual([]);
    expect(el(fixture).querySelectorAll('.cash-row').length).toBe(3);

    fixture.componentInstance.onPageChange({ first: 0, rows: 25 });
    fixture.detectChanges();
    expect(balanceCells(fixture)).toEqual([]);
    flushTxns(SCENARIO, params => params.get('offset') === '0');
    fixture.detectChanges();
    expect(balanceCells(fixture)).toEqual(['438,360', '418,360', '528,767']);
  });

  it('a type filter hides the 餘額 column', () => {
    const fixture = load();
    fixture.componentInstance.onTypeFilterChange(['deposit']);
    vi.advanceTimersByTime(300);
    flushTxns([SCENARIO[0]], params => params.get('type') === 'deposit');
    fixture.detectChanges();
    expect(heads(fixture)).not.toContain('餘額');
    expect(balanceCells(fixture)).toEqual([]);
  });

  it('a date_to filter hides the 餘額 column', () => {
    const fixture = load();
    fixture.componentInstance.onDateRangeChange([new Date(2026, 9, 1), new Date(2026, 9, 20)]);
    vi.advanceTimersByTime(300);
    flushTxns(SCENARIO, params => params.get('date_to') === '2026-10-20');
    fixture.detectChanges();
    expect(heads(fixture)).not.toContain('餘額');
    expect(balanceCells(fixture)).toEqual([]);
  });

  it('a row dated after today hides the 餘額 column', () => {
    const fixture = load([txn({ id: 9, txn_date: '2026-10-23', amount: '100', source: 'manual' }), ...SCENARIO]);
    expect(heads(fixture)).toEqual(['日期', '項目', '來源', '金額']);
    expect(balanceCells(fixture)).toEqual([]);
  });

  it('phone: compact rows with the source tag after the title and 餘額 and the date beneath the amount', () => {
    layout.set('phone');
    const fixture = load();
    const root = el(fixture);
    expect(root.querySelector('.cash-list.compact')).not.toBeNull();
    expect(root.querySelector('.cash-head')).toBeNull();
    expect(root.querySelector('.c-src')).toBeNull();
    const first = root.querySelector('.cash-row')!;
    expect(text(first.querySelector('.title-line app-tag'))).toBe('手動');
    expect(text(first.querySelector('.c-amt .amt'))).toBe('+20,000');
    expect(text(first.querySelector('.c-amt .c-bal'))).toBe('餘額 438,360');
    expect(text(first.querySelector('.c-amt .c-date'))).toBe('2026/10/20');
    expect(first.querySelector('.delete-transaction')).not.toBeNull();
  });
});
