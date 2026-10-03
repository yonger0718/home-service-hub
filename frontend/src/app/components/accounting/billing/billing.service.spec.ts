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
