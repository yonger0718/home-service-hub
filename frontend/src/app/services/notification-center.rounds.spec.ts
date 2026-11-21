import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeInbox } from '../components/accounting/inbox/testing';
import { makeAccount, makeEntry, makeInstance } from '../components/accounting/testing/fixtures';
import { AccountingService } from './accounting.service';
import { InboxService } from '../components/accounting/inbox/inbox.service';
import { NotificationCenterService, READ_TIMEOUT_MS } from './notification-center.service';

/**
 * R26 / R27 with the real AccountingService, BillingService, InboxService, StatementsService and PortfolioService over
 * HttpTestingController: nothing about loading is stubbed.
 */

/** Saturday 2026-10-24. */
const TODAY = '2026-10-24';
/** 測試銀行卡 closes on the 5th: its 2026-09-06..10-05 statement fell due Tue 2026-10-20 (overdue today). */
const CARD = makeAccount({ id: 11, name: '測試銀行卡', is_credit: true, closing_day: 5, due_rule: 'fixed_day', due_value: 20 });
/** 測試房租 (definition 1), instance 7, due today. */
const RENT = makeInstance({ id: 7, definition_id: 1, definition_name: '測試房租', due_date: TODAY, rule_date: TODAY });
const API = '/api/accounting';

interface RoundRequests {
  accounts: TestRequest;
  counterparties: TestRequest;
  queue: TestRequest;
  inbox: TestRequest;
  latest: TestRequest;
  actions: TestRequest;
  recalc: TestRequest | null;
}

async function microtasks(): Promise<void> {
  for (let i = 0; i < 10; i++) {
    await Promise.resolve();
  }
}

describe('NotificationCenterService rounds (real services over HTTP)', () => {
  let http: HttpTestingController;
  let center: NotificationCenterService;
  let accounting: AccountingService;

  beforeEach(() => {
    // Timers too: the 20 s read timeout runs on them. Promises stay real.
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
    vi.setSystemTime(new Date(2026, 9, 24, 10, 0, 0));
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    http = TestBed.inject(HttpTestingController);
    center = TestBed.inject(NotificationCenterService);
    accounting = TestBed.inject(AccountingService);
  });

  afterEach(() => {
    http.verify();
    TestBed.resetTestingModule();
    vi.useRealTimers();
  });

  const get = (url: string) => (req: { method: string; url: string }) => req.method === 'GET' && req.url === url;

  /** The open reads of the round just started (one of each task source, the actions feed, the recalc status if any). */
  function take(): RoundRequests {
    const queue = http.expectOne(get(`${API}/schedules/instances`));
    expect(queue.request.params.get('queue')).toBe('true');
    const inbox = http.expectOne(get(`${API}/reconciliation/inbox`));
    expect(inbox.request.params.get('limit')).toBe('1000');
    const recalc = http.match(get('/api/portfolio/imports/recalc/status'));
    expect(recalc.length).toBeLessThanOrEqual(1);
    return {
      accounts: http.expectOne(get(`${API}/accounts`)),
      counterparties: http.expectOne(get(`${API}/counterparties`)),
      queue,
      inbox,
      latest: http.expectOne(get(`${API}/imports/latest`)),
      actions: http.expectOne(get(`${API}/reconciliation/actions`)),
      recalc: recalc[0] ?? null,
    };
  }

  /** The business-day calendar, read once on the first round. */
  function flushCalendar(): void {
    http.expectOne(get(`${API}/calendar/business-days`)).flush({ holidays: [], workdays: [], last_known_day: '2027-12-31' });
  }

  /** Flushes every read of `round` except the ones named in `skip`, then lets effects and promises run. */
  async function flushRound(
    round: RoundRequests,
    values: { accounts?: unknown[]; queue?: unknown[] } = {},
    skip: readonly (keyof RoundRequests)[] = [],
  ): Promise<void> {
    const open = (key: keyof RoundRequests) => !skip.includes(key) && round[key] !== null;
    if (open('accounts')) round.accounts.flush(values.accounts ?? []);
    if (open('counterparties')) round.counterparties.flush([]);
    if (open('queue')) round.queue.flush(values.queue ?? []);
    if (open('inbox')) round.inbox.flush(makeInbox([]));
    if (open('latest')) round.latest.flush('', { status: 404, statusText: 'Not Found' });
    if (open('actions')) round.actions.flush([]);
    if (open('recalc')) round.recalc!.flush({ state: 'idle' });
    await settle();
  }

  async function settle(): Promise<void> {
    TestBed.tick();
    await microtasks();
    TestBed.tick();
  }

  function cardReads(): { summary: TestRequest; payments: TestRequest } {
    return {
      summary: http.expectOne(get(`${API}/accounts/11/summary`)),
      payments: http.expectOne(get(`${API}/accounts/11/entries`)),
    };
  }

  const spend = (amount: string) => ({
    account_id: 11, currency: 'TWD', date_from: '', date_to: '', spend: amount, income: '0', rewards: '0', net: amount,
    end_balance: amount, count: 1,
  });
  const payments = (...amounts: string[]) => ({
    items: amounts.map((amount, index) => makeEntry({ id: 80 + index, account_id: 11, kind: 'transfer_in', amount })),
    total: amounts.length,
    limit: 100,
    offset: 0,
  });

  const bill = () => center.tasks().find(task => task.id === 'bill:11:2026-10-20') ?? null;

  /** Round 1 with the card (1,000 unpaid) and the rent instance, fully answered. */
  async function firstRound(queue: unknown[] = [RENT]): Promise<void> {
    center.start();
    TestBed.tick();
    flushCalendar();
    await flushRound(take(), { accounts: [CARD], queue });
    const reads = cardReads();
    reads.summary.flush(spend('-1000.0000'));
    reads.payments.flush(payments());
    await settle();
    expect(center.settledRound()).toBe(1);
  }

  it('first load: with the card\'s payments read still pending the round is not settled', async () => {
    center.start();
    TestBed.tick();
    flushCalendar();
    await flushRound(take(), { accounts: [CARD] });
    const reads = cardReads();
    reads.summary.flush(spend('-1000.0000'));
    await settle();
    expect(center.settledRound()).toBe(0);
    expect(center.loaded()).toBe(false);
    expect(center.loading()).toBe(true);

    reads.payments.flush(payments());
    await settle();
    expect(center.settledRound()).toBe(1);
    expect(center.loaded()).toBe(true);
    expect(center.tasks().map(task => task.id)).toContain('bill:11:2026-10-20');
    expect(center.summary().total).toBe(1);
  });

  it('re-read after a write: the old bill figure stays and the round is unsettled until both card reads land again', async () => {
    await firstRound([]);
    const before = bill()!.amount;

    // A payment recorded elsewhere in the app: the write bumps entriesChanged → a change round and a new round (2).
    accounting.postScheduleInstance(99).subscribe();
    http.expectOne(req => req.method === 'POST' && req.url === `${API}/schedules/instances/99/post`).flush({});
    TestBed.tick();
    expect(center.round()).toBe(2);
    const reads = cardReads();
    await flushRound(take(), { accounts: [CARD] });
    // No second pair of card reads: the round waits on the pair the change round already started.
    http.expectNone(get(`${API}/accounts/11/summary`));
    expect(center.settledRound()).toBe(1);
    expect(bill()?.amount).toBe(before);

    reads.summary.flush(spend('-1000.0000'));
    await settle();
    expect(center.settledRound()).toBe(1);
    expect(bill()?.amount).toBe(before);

    reads.payments.flush(payments('400.0000'));
    await settle();
    expect(center.settledRound()).toBe(2);
    expect(bill()?.amount).not.toBe(before);
    expect(bill()?.meta).toContain('已繳');
  });

  it('a re-read with no change round (invalidate after a failed write) reads the card again and waits for it', async () => {
    await firstRound([]);
    const id = center.invalidate();
    await flushRound(take(), { accounts: [CARD] });
    const reads = cardReads();
    expect(center.settledRound()).toBe(1);

    reads.summary.flush(spend('-1000.0000'));
    reads.payments.flush(payments());
    await settle();
    expect(center.settledRound()).toBe(id);
  });

  it('a real schedule post interleaved with the queue read', async () => {
    await firstRound();
    expect(center.tasks().some(task => task.id === 'sched:1')).toBe(true);

    center.refresh({ reason: 'open' });
    expect(center.round()).toBe(2);
    const second = take();

    accounting.postScheduleInstance(7).subscribe();
    http.expectOne(req => req.method === 'POST' && req.url === `${API}/schedules/instances/7/post`).flush({ ...RENT, status: 'posted' });
    TestBed.tick();
    expect(center.round()).toBe(3);
    // The change round re-reads the card through the statements effect.
    const reads = cardReads();
    const third = take();

    // The older queue GET (A) answers with the pre-write queue: dropped.
    await flushRound(second, { accounts: [CARD], queue: [RENT] });
    expect(center.settledRound()).toBe(1);

    await flushRound(third, { accounts: [CARD], queue: [] });
    reads.summary.flush(spend('-1000.0000'));
    reads.payments.flush(payments());
    await settle();
    expect(center.tasks().some(task => task.id === 'sched:1')).toBe(false);
    expect(center.settledRound()).toBe(3);
  });

  it('a write that commits but answers 500: invalidate() supersedes the in-flight round', async () => {
    await firstRound();
    center.refresh({ reason: 'open' });
    const second = take();

    let failed = false;
    accounting.postScheduleInstance(7).subscribe({ error: () => (failed = true) });
    http
      .expectOne(req => req.method === 'POST' && req.url === `${API}/schedules/instances/7/post`)
      .flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    TestBed.tick();
    expect(failed).toBe(true);
    expect(center.round()).toBe(2);

    const id = center.invalidate();
    expect(id).toBe(3);
    const third = take();

    // The old queue GET (A) answers with the pre-write row: ignored.
    await flushRound(second, { accounts: [CARD], queue: [RENT] });
    expect(center.settledRound()).toBe(1);

    await flushRound(third, { accounts: [CARD], queue: [] });
    const reads = cardReads();
    reads.summary.flush(spend('-1000.0000'));
    reads.payments.flush(payments());
    await settle();
    expect(center.tasks().some(task => task.id === 'sched:1')).toBe(false);
    expect(center.settledRound()).toBe(id);
  });

  // ---- follow-up: hung reads (READ_TIMEOUT_MS) and the inbox write trigger -------------------------------------------

  /** Advances the fake clock, then lets effects and promises run. */
  async function wait(ms: number): Promise<void> {
    vi.advanceTimersByTime(ms);
    await settle();
  }

  it('a GET never answered: after 20 s the round settles with that source failed, the others intact; open starts a new round', async () => {
    center.start();
    TestBed.tick();
    flushCalendar();
    const first = take();
    await flushRound(first, { accounts: [CARD], queue: [RENT] }, ['inbox']);
    const reads = cardReads();
    reads.summary.flush(spend('-1000.0000'));
    reads.payments.flush(payments());
    await settle();
    expect(center.settledRound()).toBe(0);

    await wait(READ_TIMEOUT_MS - 1);
    expect(center.settledRound()).toBe(0);
    await wait(1);
    expect(center.settledRound()).toBe(1);
    expect(center.failedSources()).toEqual(['recon']);
    expect(center.tasks().map(task => task.id)).toEqual(['bill:11:2026-10-20', 'sched:1']);
    // The timed-out read was unsubscribed: HttpClient aborted it, so no late answer can arrive.
    expect(first.inbox.cancelled).toBe(true);

    center.refresh({ reason: 'open' });
    expect(center.round()).toBe(2);
    await flushRound(take(), { accounts: [CARD], queue: [RENT] });
    const again = cardReads();
    again.summary.flush(spend('-1000.0000'));
    again.payments.flush(payments());
    await settle();
    expect(center.settledRound()).toBe(2);
    expect(center.failedSources()).toEqual([]);
  });

  it('a read answering at 19.9 s is applied normally', async () => {
    center.start();
    TestBed.tick();
    flushCalendar();
    const first = take();
    await flushRound(first, {}, ['queue']);
    await wait(19_900);
    expect(first.queue.cancelled).toBe(false);
    first.queue.flush([RENT]);
    await settle();
    expect(center.settledRound()).toBe(1);
    expect(center.failedSources()).toEqual([]);
    expect(center.tasks().map(task => task.id)).toEqual(['sched:1']);

    await wait(1_000);
    expect(center.failedSources()).toEqual([]);
  });

  it('a hung card statement: 20 s after the statements call the bills source fails; its late answer does not flip it back', async () => {
    center.start();
    TestBed.tick();
    flushCalendar();
    await flushRound(take(), { accounts: [CARD], queue: [RENT] });
    const reads = cardReads();
    reads.summary.flush(spend('-1000.0000'));
    await wait(READ_TIMEOUT_MS - 1);
    expect(center.settledRound()).toBe(0);
    await wait(1);
    expect(center.settledRound()).toBe(1);
    expect(center.failedSources()).toEqual(['bills']);
    expect(center.tasks().map(task => task.id)).toEqual(['sched:1']);

    // The statement read belongs to the shared BillingService (the timeline and reminders read it too): it is not
    // aborted, and its late answer lands there. The round's verdict stands: 帳單 stays failed until the next round.
    reads.payments.flush(payments());
    await settle();
    expect(center.settledRound()).toBe(1);
    expect(center.failedSources()).toEqual(['bills']);
  });

  it('an inbox write (the real apply) starts a round', async () => {
    await firstRound();
    const inbox = TestBed.inject(InboxService);
    inbox
      .apply({ cases: [{ case_id: 1, case_version: 1 }], action: 'dismiss', params: {}, answer: '測試' })
      .subscribe();
    http
      .expectOne(req => req.method === 'POST' && req.url === `${API}/reconciliation/inbox/apply`)
      .flush({ results: [], rule_id: null, rule_error: null });
    TestBed.tick();
    expect(center.round()).toBe(2);
    await flushRound(take(), { accounts: [CARD], queue: [RENT] });
    const reads = cardReads();
    reads.summary.flush(spend('-1000.0000'));
    reads.payments.flush(payments());
    await settle();
    expect(center.settledRound()).toBe(2);
  });
});
