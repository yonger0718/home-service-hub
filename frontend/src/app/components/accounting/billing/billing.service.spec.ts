import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { AccountingService } from '../../../services/accounting.service';
import { makeEntry } from '../testing/fixtures';
import { BillingEvent } from './billing-math';
import { BILL_PAYMENT_LIMIT, BillingService } from './billing.service';

const SEPT: BillingEvent = {
  accountId: 9,
  name: '玉山 UNI',
  kind: 'due',
  date: '2026-10-05',
  period: { start: '2026-08-16', end: '2026-09-15' },
  nextClosing: '2026-10-15',
};
const OCT: BillingEvent = {
  ...SEPT,
  date: '2026-11-04',
  period: { start: '2026-09-16', end: '2026-10-15' },
  nextClosing: '2026-11-15',
};
/** Today in these specs. */
const TODAY = '2026-10-03';

describe('BillingService', () => {
  let http: HttpTestingController;
  let billing: BillingService;
  let accounting: AccountingService;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    http = TestBed.inject(HttpTestingController);
    billing = TestBed.inject(BillingService);
    accounting = TestBed.inject(AccountingService);
  });

  afterEach(() => http.verify());

  function requests(event = SEPT, paidTo = TODAY): { summary: TestRequest; payments: TestRequest } {
    const summary = http.expectOne(
      r => r.url === `/api/accounting/accounts/${event.accountId}/summary` && r.params.get('date_to') === event.period.end,
    );
    expect(summary.request.params.get('date_from')).toBe(event.period.start);
    const payments = http.expectOne(
      r => r.url === `/api/accounting/accounts/${event.accountId}/entries` && r.params.get('date_from') === event.period.end,
    );
    expect(payments.request.params.get('kind')).toBe('transfer_in');
    expect(payments.request.params.get('date_to')).toBe(paidTo);
    expect(payments.request.params.get('limit')).toBe(String(BILL_PAYMENT_LIMIT));
    return { summary, payments };
  }

  function answer(reqs: { summary: TestRequest; payments: TestRequest }, spend: string, payments: string[] = []): void {
    reqs.summary.flush({
      account_id: 9, currency: 'TWD', date_from: '', date_to: '', spend, income: '0', rewards: '0', net: spend,
      end_balance: spend, count: 1,
    });
    reqs.payments.flush({
      items: payments.map((amount, index) => makeEntry({ id: 50 + index, kind: 'transfer_in', amount })),
      total: payments.length,
      limit: BILL_PAYMENT_LIMIT,
      offset: 0,
    });
  }

  function writeEntry(): void {
    accounting.deleteEntry(3).subscribe();
    http.expectOne(r => r.method === 'DELETE').flush(null);
  }

  it('resolves a statement into amount, paid and remaining once both reads land', () => {
    billing.ensure([SEPT], TODAY);
    const reqs = requests();
    reqs.summary.flush({
      account_id: 9, currency: 'TWD', date_from: '', date_to: '', spend: '-12345.0000', income: '0', rewards: '0',
      net: '0', end_balance: '0', count: 1,
    });
    expect(billing.bill(SEPT)).toBeNull();
    reqs.payments.flush({ items: [makeEntry({ kind: 'transfer_in', amount: '5000.0000' })], total: 1, limit: 100, offset: 0 });

    expect(billing.bill(SEPT)).toEqual({
      accountId: 9,
      name: '玉山 UNI',
      period: SEPT.period,
      due: '2026-10-05',
      currency: 'TWD',
      statement: 12345,
      paid: 5000,
      remaining: 7345,
    });
  });

  it('asks once per statement and change round, however many consumers ask', () => {
    billing.ensure([SEPT, OCT], TODAY);
    billing.ensure([SEPT], TODAY);
    answer(requests(SEPT), '-100.0000');
    answer(requests(OCT, '2026-10-15'), '-200.0000');
    billing.ensure([OCT, SEPT], TODAY);
    http.expectNone(r => r.url.startsWith('/api/accounting/accounts/'));
    expect(billing.bill(OCT)?.remaining).toBe(200);
  });

  it('re-reads after an entry write and after an account write, keeping the old figures until the new ones land', () => {
    billing.ensure([SEPT], TODAY);
    answer(requests(), '-100.0000');
    const before = billing.generation();

    writeEntry();
    expect(billing.generation()).toBeGreaterThan(before);
    billing.ensure([SEPT], TODAY);
    const reqs = requests();
    expect(billing.bill(SEPT)?.remaining).toBe(100);
    reqs.summary.flush({
      account_id: 9, currency: 'TWD', date_from: '', date_to: '', spend: '-100.0000', income: '0', rewards: '0',
      net: '0', end_balance: '0', count: 1,
    });
    // Half an answer never mixes with the previous round's other half.
    expect(billing.bill(SEPT)?.remaining).toBe(100);
    reqs.payments.flush({ items: [makeEntry({ kind: 'transfer_in', amount: '100.0000' })], total: 1, limit: 100, offset: 0 });
    expect(billing.bill(SEPT)?.remaining).toBe(0);

    accounting.updateAccount(9, {} as never).subscribe();
    http.expectOne(r => r.method === 'PUT').flush({});
    billing.ensure([SEPT], TODAY);
    answer(requests(), '-300.0000');
    expect(billing.bill(SEPT)?.remaining).toBe(300);
  });

  it("drops a superseded round's answers: the state keeps the latest round's result, never an earlier one", () => {
    billing.ensure([SEPT], TODAY);
    const first = requests();
    writeEntry();
    billing.ensure([SEPT], TODAY);
    const second = requests();

    answer(first, '-111.0000');
    expect(billing.bill(SEPT)).toBeNull();

    answer(second, '-222.0000');
    expect(billing.bill(SEPT)?.statement).toBe(222);
  });

  it('drops a superseded answer that lands after the latest one', () => {
    billing.ensure([SEPT], TODAY);
    const first = requests();
    writeEntry();
    billing.ensure([SEPT], TODAY);
    const second = requests();

    answer(second, '-222.0000');
    answer(first, '-111.0000');
    expect(billing.bill(SEPT)?.statement).toBe(222);
  });

  it('counts a payment made after the due date until the next closing', () => {
    // 10/09: four days past the 10/05 due date, before the 10/15 closing.
    billing.ensure([SEPT], '2026-10-09');
    answer(requests(SEPT, '2026-10-09'), '-100.0000', ['100.0000']);
    expect(billing.bill(SEPT)?.remaining).toBe(0);
  });

  it('stops counting payments the day before the next closing', () => {
    // A payment dated on/after 10/15 belongs to the next statement: the window ends on 10/14.
    billing.ensure([SEPT], '2026-10-20');
    answer(requests(SEPT, '2026-10-14'), '-100.0000');
    expect(billing.bill(SEPT)?.remaining).toBe(100);
  });

  function summaryFor(accountId: number, event = SEPT): TestRequest {
    const req = http.expectOne(
      r => r.url === `/api/accounting/accounts/${accountId}/summary` && r.params.get('date_to') === event.period.end,
    );
    expect(req.request.params.get('date_from')).toBe(event.period.start);
    return req;
  }

  const spendBody = (accountId: number, spend: string, currency = 'TWD') => ({
    account_id: accountId, currency, date_from: '', date_to: '', spend, income: '0', rewards: '0', net: spend,
    end_balance: spend, count: 1,
  });

  it("adds the combined cards' spend to the master's statement, reading payments on the master only", () => {
    const combined: BillingEvent = { ...SEPT, childIds: [11, 12] };
    billing.ensure([combined], TODAY);
    const master = requests(combined);
    const first = summaryFor(11);
    const second = summaryFor(12);
    http.expectNone(r => r.url.endsWith('/11/entries') || r.url.endsWith('/12/entries'));

    master.summary.flush(spendBody(9, '-1000.0000'));
    first.flush(spendBody(11, '-200.0000'));
    master.payments.flush({ items: [makeEntry({ kind: 'transfer_in', amount: '300.0000' })], total: 1, limit: 100, offset: 0 });
    // Not committed until every card of the statement has landed.
    expect(billing.bill(combined)).toBeNull();
    second.flush(spendBody(12, '-50.0000'));

    expect(billing.bill(combined)).toMatchObject({ accountId: 9, statement: 1250, paid: 300, remaining: 950 });
    billing.ensure([combined], TODAY);
    http.expectNone(r => r.url.startsWith('/api/accounting/accounts/'));
  });

  it("leaves the master's statement unresolved when a combined card's read fails", () => {
    const combined: BillingEvent = { ...SEPT, childIds: [11] };
    billing.ensure([combined], TODAY);
    const master = requests(combined);
    const child = summaryFor(11);
    answer(master, '-1000.0000');
    child.flush('boom', { status: 500, statusText: 'Server Error' });

    expect(billing.bill(combined)).toBeNull();
    expect(billing.isSettled(combined)).toBe(true);
  });

  it("leaves it unresolved when a combined card's currency differs from the master's", () => {
    const combined: BillingEvent = { ...SEPT, childIds: [11] };
    billing.ensure([combined], TODAY);
    const master = requests(combined);
    const child = summaryFor(11);
    answer(master, '-1000.0000');
    child.flush(spendBody(11, '-50.0000', 'USD'));

    expect(billing.bill(combined)).toBeNull();
  });

  it('starts a new round in the same change round when a combined card is attached to the master', () => {
    billing.ensure([SEPT], TODAY);
    answer(requests(), '-1000.0000');
    expect(billing.bill(SEPT)?.statement).toBe(1000);

    // A refreshed account list now has a 副卡 under the same master: same key, new inputs.
    const combined: BillingEvent = { ...SEPT, childIds: [11] };
    billing.ensure([combined], TODAY);
    const master = requests(combined);
    const child = summaryFor(11);
    expect(billing.bill(combined)?.statement).toBe(1000);
    answer(master, '-1000.0000');
    child.flush(spendBody(11, '-200.0000'));
    expect(billing.bill(combined)?.statement).toBe(1200);

    billing.ensure([combined], TODAY);
    http.expectNone(r => r.url.startsWith('/api/accounting/accounts/'));
  });

  it('marks a statement failed when a read fails, and retries it in a new round', () => {
    billing.ensure([SEPT], TODAY);
    const first = requests();
    first.summary.flush(spendBody(9, '-100.0000'));
    expect(billing.failed(SEPT)).toBe(false);
    first.payments.flush('boom', { status: 500, statusText: 'Server Error' });
    expect(billing.failed(SEPT)).toBe(true);
    expect(billing.bill(SEPT)).toBeNull();

    // The same round is not asked for again on its own…
    billing.ensure([SEPT], TODAY);
    http.expectNone(r => r.url.startsWith('/api/accounting/accounts/'));
    // …but a retry starts a new one.
    billing.retry(SEPT, TODAY);
    answer(requests(), '-100.0000');
    expect(billing.failed(SEPT)).toBe(false);
    expect(billing.bill(SEPT)?.remaining).toBe(100);
  });

  it('leaves a statement unresolved when either read fails', () => {
    billing.ensure([SEPT], TODAY);
    const reqs = requests();
    reqs.summary.flush('boom', { status: 500, statusText: 'Server Error' });
    expect(billing.isSettled(SEPT)).toBe(false);
    reqs.payments.flush({ items: [], total: 0, limit: 100, offset: 0 });
    expect(billing.bill(SEPT)).toBeNull();
    // Settled (nothing more is coming this round), unlike a statement still being read.
    expect(billing.isSettled(SEPT)).toBe(true);
    expect(billing.isSettled(OCT)).toBe(false);
  });

  it('keys a statement by its due date too, so a changed due rule is a different statement', () => {
    billing.ensure([SEPT], TODAY);
    answer(requests(), '-100.0000');
    const moved = { ...SEPT, date: '2026-10-07' };
    expect(billing.bill(moved)).toBeNull();
    billing.ensure([moved], TODAY);
    answer(requests(moved), '-100.0000');
    expect(billing.bill(moved)?.due).toBe('2026-10-07');
  });
});
