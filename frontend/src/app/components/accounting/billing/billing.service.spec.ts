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
      payDate: '2026-10-05',
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

  /** A combined card's payments read (same window as the master's). */
  function paymentsFor(accountId: number): TestRequest {
    return http.expectOne(r => r.url === `/api/accounting/accounts/${accountId}/entries` && r.params.get('kind') === 'transfer_in');
  }

  const noPayments = { items: [], total: 0, limit: BILL_PAYMENT_LIMIT, offset: 0 };

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

  it("adds the combined cards' spend and payments to the master's statement, one read of each per card", () => {
    const combined: BillingEvent = { ...SEPT, childIds: [11, 12] };
    billing.ensure([combined], TODAY);
    const master = requests(combined);
    const first = summaryFor(11);
    const second = summaryFor(12);
    const firstPayments = paymentsFor(11);
    const secondPayments = paymentsFor(12);
    expect(firstPayments.request.params.get('date_to')).toBe(TODAY);

    master.summary.flush(spendBody(9, '-1000.0000'));
    first.flush(spendBody(11, '-200.0000'));
    master.payments.flush({ items: [makeEntry({ kind: 'transfer_in', amount: '300.0000' })], total: 1, limit: 100, offset: 0 });
    firstPayments.flush({ items: [makeEntry({ id: 61, kind: 'transfer_in', amount: '200.0000' })], total: 1, limit: 100, offset: 0 });
    second.flush(spendBody(12, '-50.0000'));
    // Not committed until every read of every card has landed.
    expect(billing.bill(combined)).toBeNull();
    secondPayments.flush(noPayments);

    expect(billing.bill(combined)).toMatchObject({ accountId: 9, statement: 1250, paid: 500, remaining: 750 });
    billing.ensure([combined], TODAY);
    http.expectNone(r => r.url.startsWith('/api/accounting/accounts/'));
  });

  it("leaves the master's statement unresolved when a combined card's read fails", () => {
    const combined: BillingEvent = { ...SEPT, childIds: [11] };
    billing.ensure([combined], TODAY);
    const master = requests(combined);
    const child = summaryFor(11);
    paymentsFor(11).flush(noPayments);
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
    paymentsFor(11).flush(noPayments);
    answer(master, '-1000.0000');
    child.flush(spendBody(11, '-50.0000', 'USD'));

    expect(billing.bill(combined)).toBeNull();
    // Not silently unresolved: failed, with the reason.
    expect(billing.failed(combined)).toBe(true);
    expect(billing.failureReason(combined)).toBe('幣別不同');
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
    paymentsFor(11).flush(noPayments);
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
    expect(billing.failureReason(SEPT)).toBeNull();
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
  // ---- refresh: a re-read of already cached statements (nav badges on focus / visibility) ------------------------

  it('refresh re-reads a cached statement in the same change round and replaces the figure once it lands', () => {
    billing.ensure([SEPT], TODAY);
    answer(requests(), '-100.0000');
    expect(billing.bill(SEPT)?.remaining).toBe(100);

    billing.refresh([SEPT], TODAY);
    const reqs = requests();
    // The previous figure stays until the new round has fully landed.
    expect(billing.bill(SEPT)?.remaining).toBe(100);
    answer(reqs, '-100.0000', ['60.0000']);
    expect(billing.bill(SEPT)?.remaining).toBe(40);

    // The refreshed round is the current one: a plain ensure does not ask again.
    billing.ensure([SEPT], TODAY);
    http.expectNone(r => r.url.startsWith('/api/accounting/accounts/'));
  });

  it('drops a refreshed round superseded by a newer one', () => {
    billing.ensure([SEPT], TODAY);
    answer(requests(), '-100.0000');
    billing.refresh([SEPT], TODAY);
    const first = requests();
    billing.refresh([SEPT], TODAY);
    const second = requests();

    answer(second, '-222.0000');
    answer(first, '-111.0000');
    expect(billing.bill(SEPT)?.statement).toBe(222);
  });

  it('recovers a failed first round on the next refresh', () => {
    billing.ensure([SEPT], TODAY);
    const first = requests();
    first.summary.flush('boom', { status: 500, statusText: 'Server Error' });
    first.payments.flush({ items: [], total: 0, limit: 100, offset: 0 });
    expect(billing.failed(SEPT)).toBe(true);

    billing.refresh([SEPT], TODAY);
    answer(requests(), '-100.0000');
    expect(billing.failed(SEPT)).toBe(false);
    expect(billing.bill(SEPT)?.remaining).toBe(100);
  });
  it('counts payments recorded on a combined card (副卡) toward the master (#196)', () => {
    const family: BillingEvent = { ...SEPT, childIds: [21] };
    billing.ensure([family], TODAY);
    const master = requests(family);
    const childSummary = http.expectOne(r => r.url === '/api/accounting/accounts/21/summary');
    const childPayments = http.expectOne(r => r.url === '/api/accounting/accounts/21/entries');
    expect(childPayments.request.params.get('kind')).toBe('transfer_in');
    expect(childPayments.request.params.get('date_from')).toBe(family.period.end);
    expect(childPayments.request.params.get('date_to')).toBe(TODAY);
    answer(master, '-1000.0000');
    childSummary.flush({
      account_id: 21, currency: 'TWD', date_from: '', date_to: '', spend: '-2800.0000', income: '0', rewards: '0',
      net: '0', end_balance: '0', count: 1,
    });
    expect(billing.bill(family)).toBeNull();
    childPayments.flush({ items: [makeEntry({ id: 77, kind: 'transfer_in', amount: '2800.0000' })], total: 1, limit: 100, offset: 0 });

    expect(billing.bill(family)).toMatchObject({ statement: 3800, paid: 2800, remaining: 1000 });
  });

  it('fails the round when a combined card\'s payments cannot be read', () => {
    const family: BillingEvent = { ...SEPT, childIds: [21] };
    billing.ensure([family], TODAY);
    answer(requests(family), '-1000.0000');
    http.expectOne(r => r.url === '/api/accounting/accounts/21/summary').flush({
      account_id: 21, currency: 'TWD', date_from: '', date_to: '', spend: '0', income: '0', rewards: '0', net: '0',
      end_balance: '0', count: 0,
    });
    http.expectOne(r => r.url === '/api/accounting/accounts/21/entries').flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });

    expect(billing.bill(family)).toBeNull();
    expect(billing.failed(family)).toBe(true);
    expect(billing.failureReason(family)).toBeNull();
  });

  it('carries the debit day and reads payments through it when the due date was shifted (#197)', () => {
    // Due Sat 2026-10-24, debited Tue 2026-10-27; today is the debit day, after the next closing's eve (10-14).
    const shifted: BillingEvent = { ...OCT, date: '2026-10-24', payDate: '2026-10-27' };
    billing.ensure([shifted], '2026-10-27');
    const reqs = requests(shifted, '2026-10-27');
    answer(reqs, '-500.0000', ['500.0000']);

    expect(billing.bill(shifted)).toMatchObject({ due: '2026-10-24', payDate: '2026-10-27', remaining: 0 });
  });
  it('starts a new round when the calendar lands and moves the debit day (#197)', () => {
    // Due Sat 2026-10-24: weekends-only gives Mon 10-26; the calendar then lists 10-26 as a holiday → Tue 10-27.
    const weekend: BillingEvent = { ...OCT, date: '2026-10-24', payDate: '2026-10-26' };
    billing.ensure([weekend], '2026-10-26');
    answer(requests(weekend, '2026-10-26'), '-500.0000');
    expect(billing.bill(weekend)).toMatchObject({ payDate: '2026-10-26', remaining: 500 });

    const shifted: BillingEvent = { ...weekend, payDate: '2026-10-27' };
    billing.ensure([shifted], '2026-10-26');
    const again = requests(shifted, '2026-10-26');
    // The previous figures stay shown until the new round lands.
    expect(billing.bill(shifted)).toMatchObject({ payDate: '2026-10-26' });
    answer(again, '-500.0000', ['500.0000']);
    expect(billing.bill(shifted)).toMatchObject({ payDate: '2026-10-27', remaining: 0 });

    billing.ensure([shifted], '2026-10-26');
    http.expectNone(r => r.url.startsWith('/api/accounting/accounts/'));
  });
});
