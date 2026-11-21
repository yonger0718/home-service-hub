import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MATCH_MEDIA } from '../../services/layout-mode.service';
import { NotificationCenterService } from '../../services/notification-center.service';
import { ToastService } from '../../services/toast.service';
import { makeInbox } from '../accounting/inbox/testing';
import { makeAccount, makeInstance } from '../accounting/testing/fixtures';
import { NOTIF_PRIMARY_CONTROL } from './notif-row/notif-row';
import { NotificationsComponent } from './notifications';

/**
 * The page over the REAL NotificationCenterService, BillingService, AccountingService, InboxService, StatementsService
 * and ToastService, with HttpTestingController: the write / read interleavings of the plan's Review Focus. The app's
 * error-logging interceptor (which resends a failed request once, #411) is not installed: one flush is one answer.
 */

/** Saturday 2026-10-24. */
const TODAY = '2026-10-24';
/** 測試銀行卡 closes on the 5th; its statement fell due Tue 2026-10-20. */
const CARD = makeAccount({ id: 11, name: '測試銀行卡', is_credit: true, closing_day: 5, due_rule: 'fixed_day', due_value: 20 });
/** 測試房租 (definition 1), instance 7, due today: one 入帳 row, id `sched:1`. */
const RENT = makeInstance({ id: 7, definition_id: 1, definition_name: '測試房租', due_date: TODAY, rule_date: TODAY });
const API = '/api/accounting';
const RENT_ROW = 'app-notif-row[data-task-id="sched:1"]';

interface RoundRequests {
  accounts: TestRequest;
  counterparties: TestRequest;
  queue: TestRequest;
  inbox: TestRequest;
  latest: TestRequest;
  actions: TestRequest;
}

async function microtasks(): Promise<void> {
  for (let i = 0; i < 10; i++) {
    await Promise.resolve();
  }
}

describe('NotificationsComponent with the real services (入帳 interleavings)', () => {
  let http: HttpTestingController;
  let center: NotificationCenterService;
  let toasts: ToastService;
  let fixture: ComponentFixture<NotificationsComponent>;
  let root: HTMLElement;

  const get = (url: string) => (req: { method: string; url: string }) => req.method === 'GET' && req.url === url;
  const isPost = (req: { method: string; url: string }) => req.method === 'POST' && req.url === `${API}/schedules/instances/7/post`;

  async function settle(): Promise<void> {
    TestBed.tick();
    await microtasks();
    TestBed.tick();
    await fixture?.whenStable();
  }

  /** The task-source reads of the round just started. */
  function take(): RoundRequests {
    const queue = http.expectOne(get(`${API}/schedules/instances`));
    expect(queue.request.params.get('queue')).toBe('true');
    return {
      accounts: http.expectOne(get(`${API}/accounts`)),
      counterparties: http.expectOne(get(`${API}/counterparties`)),
      queue,
      inbox: http.expectOne(get(`${API}/reconciliation/inbox`)),
      latest: http.expectOne(get(`${API}/imports/latest`)),
      actions: http.expectOne(get(`${API}/reconciliation/actions`)),
    };
  }

  /** Answers any open card-statement reads (1,000 spent, no payment) and the recalc status. */
  async function flushSide(): Promise<void> {
    await settle();
    for (const req of http.match(get(`${API}/accounts/11/summary`))) {
      req.flush({
        account_id: 11, currency: 'TWD', date_from: '', date_to: '', spend: '-1000.0000', income: '0', rewards: '0',
        net: '-1000.0000', end_balance: '-1000.0000', count: 1,
      });
    }
    for (const req of http.match(get(`${API}/accounts/11/entries`))) {
      req.flush({ items: [], total: 0, limit: 100, offset: 0 });
    }
    for (const req of http.match(get('/api/portfolio/imports/recalc/status'))) {
      req.flush({ state: 'idle' });
    }
    await settle();
  }

  /** Flushes every read of `round` except `skip`, with `queue` as the schedule queue, then the card reads. */
  async function flushRound(round: RoundRequests, queue: unknown[], skip: readonly (keyof RoundRequests)[] = []): Promise<void> {
    const open = (key: keyof RoundRequests) => !skip.includes(key);
    if (open('accounts')) round.accounts.flush([CARD]);
    if (open('counterparties')) round.counterparties.flush([]);
    if (open('queue')) round.queue.flush(queue);
    if (open('inbox')) round.inbox.flush(makeInbox([]));
    if (open('latest')) round.latest.flush('', { status: 404, statusText: 'Not Found' });
    if (open('actions')) round.actions.flush([]);
    await flushSide();
  }

  const rentRow = () => root.querySelector<HTMLElement>(RENT_ROW);
  const rentButton = () => rentRow()?.querySelector<HTMLButtonElement>(NOTIF_PRIMARY_CONTROL) ?? null;

  /** Page open after round 1 has settled with the rent row; then a window focus 31 s later starts round 2. */
  async function openThenFocus(): Promise<RoundRequests> {
    center.start(); // the shell's call
    TestBed.tick();
    http.expectOne(get(`${API}/calendar/business-days`)).flush({ holidays: [], workdays: [], last_known_day: '2027-12-31' });
    fixture = TestBed.createComponent(NotificationsComponent);
    fixture.autoDetectChanges();
    root = fixture.nativeElement as HTMLElement;
    await flushRound(take(), [RENT]);
    expect(center.settledRound()).toBe(1);
    expect(rentButton()?.tagName).toBe('BUTTON');
    expect(rentButton()!.disabled).toBe(false);

    vi.setSystemTime(new Date(2026, 9, 24, 10, 0, 31));
    window.dispatchEvent(new Event('focus'));
    TestBed.tick();
    expect(center.round()).toBe(2);
    return take();
  }

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
    vi.setSystemTime(new Date(2026, 9, 24, 10, 0, 0));
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        {
          provide: MATCH_MEDIA,
          useValue: (q: string) =>
            ({ matches: false, media: q, addEventListener: () => undefined, removeEventListener: () => undefined }) as unknown as MediaQueryList,
        },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    center = TestBed.inject(NotificationCenterService);
    toasts = TestBed.inject(ToastService);
  });

  afterEach(() => {
    http.verify();
    TestBed.resetTestingModule();
    vi.useRealTimers();
  });

  it("the reviewer's interleaving: old queue GET in flight, POST commits but answers 500", async () => {
    const round2 = await openThenFocus();
    // Round 2 waits on its queue GET (A) only.
    await flushRound(round2, [], ['queue']);
    const a = round2.queue;
    expect(center.settledRound()).toBe(1);

    rentButton()!.click();
    await settle();
    const post = http.expectOne(isPost);
    expect(rentButton()!.disabled).toBe(true);
    // The server committed, then the answer was lost as a 500.
    post.flush({ message: 'Internal Server Error' }, { status: 500, statusText: 'Internal Server Error' });
    await settle();
    expect(toasts.current()).toEqual(
      expect.objectContaining({ kind: 'error', text: '測試房租 入帳失敗：Internal Server Error', action: expect.objectContaining({ label: '重試' }) }),
    );
    // The failure re-reads at once: round 3 supersedes round 2.
    expect(center.round()).toBe(3);
    const round3 = take(); // B among them
    expect(rentButton()!.disabled).toBe(true);

    // A answers with the pre-write list: dropped, nothing settles, the row stays busy.
    a.flush([RENT]);
    await settle();
    expect(center.settledRound()).toBe(1);
    expect(rentRow()).not.toBeNull();
    expect(rentButton()!.disabled).toBe(true);

    // B answers without the instance (it was posted) and the rest of round 3 lands.
    await flushRound(round3, []);
    expect(center.settledRound()).toBe(3);
    expect(rentRow()).toBeNull();
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'error', action: expect.objectContaining({ label: '重試' }) }));
  });

  it('重試 after the 500 that in fact committed resends instance 7; the server refuses it (409 already_posted), no duplicate and no 重試', async () => {
    const round2 = await openThenFocus();
    await flushRound(round2, [], ['queue']);
    rentButton()!.click();
    await settle();
    http.expectOne(isPost).flush({ message: 'Internal Server Error' }, { status: 500, statusText: 'Internal Server Error' });
    await settle();
    const round3 = take();
    round2.queue.flush([RENT]);
    await flushRound(round3, []);
    expect(rentRow()).toBeNull();

    // The brief's rule: 重試 re-sends the captured instance even though the row left the list.
    toasts.runAction(toasts.current()!.id);
    await settle();
    const again = http.expectOne(isPost);
    // The server posts an instance once: a second post of 7 is refused, and no entry is written.
    again.flush({ code: 'CONFLICT', message: 'already_posted' }, { status: 409, statusText: 'Conflict' });
    await settle();
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'error', text: '測試房租 入帳失敗：此期已入帳', action: null }));
    // That failure re-reads too (round 4); nothing else is sent.
    expect(center.round()).toBe(4);
    await flushRound(take(), []);
    http.expectNone(isPost);
    expect(rentRow()).toBeNull();
  });

  it("the same with a 200: the effect's round replaces the old GET's answer and the toast is 「已入帳：測試房租」", async () => {
    const round2 = await openThenFocus();
    await flushRound(round2, [], ['queue']);
    const a = round2.queue;

    rentButton()!.click();
    await settle();
    http.expectOne(isPost).flush({ ...RENT, status: 'posted' });
    await settle();
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '已入帳：測試房租' }));
    // The write bumped entriesChanged: the service's effect started round 3 (the page called nothing).
    expect(center.round()).toBe(3);
    const round3 = take();
    expect(rentButton()!.disabled).toBe(true);

    // B answers first without the instance; then A, late, with the pre-write list: it does not bring the row back.
    round3.queue.flush([]);
    await settle();
    expect(rentRow()).toBeNull();
    a.flush([RENT]);
    await settle();
    expect(rentRow()).toBeNull();
    expect(center.settledRound()).toBe(1);

    await flushRound(round3, [], ['queue']);
    expect(center.settledRound()).toBe(3);
    expect(rentRow()).toBeNull();
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '已入帳：測試房租' }));
    http.expectNone(isPost);
  });

  it('a 500 that did not commit: after round 3 the row is back, enabled, and 重試 posts instance 7 again', async () => {
    const round2 = await openThenFocus();
    await flushRound(round2, [], ['queue']);
    rentButton()!.click();
    await settle();
    http.expectOne(isPost).flush({ message: 'Internal Server Error' }, { status: 500, statusText: 'Internal Server Error' });
    await settle();
    const round3 = take();
    round2.queue.flush([]); // even a stale answer that leaves it out is dropped
    await settle();
    expect(rentRow()).not.toBeNull();
    expect(rentButton()!.disabled).toBe(true);

    // 重試 while the re-read runs: the toast again, nothing sent.
    toasts.runAction(toasts.current()!.id);
    await settle();
    http.expectNone(isPost);
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'error', action: expect.objectContaining({ label: '重試' }) }));

    await flushRound(round3, [RENT]);
    expect(center.settledRound()).toBe(3);
    expect(rentRow()).not.toBeNull();
    expect(rentButton()!.disabled).toBe(false);

    toasts.runAction(toasts.current()!.id);
    await settle();
    const again = http.expectOne(isPost);
    expect(rentButton()!.disabled).toBe(true);
    again.flush({ ...RENT, status: 'posted' });
    await settle();
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '已入帳：測試房租' }));
    expect(center.round()).toBe(4);
    await flushRound(take(), []);
    expect(rentRow()).toBeNull();
  });

  it('a re-read that fails after a successful write: the row leaves (its source is dropped), the done toast stays', async () => {
    const round2 = await openThenFocus();
    await flushRound(round2, [RENT]);
    expect(center.settledRound()).toBe(2);
    rentButton()!.click();
    await settle();
    http.expectOne(isPost).flush({ ...RENT, status: 'posted' });
    await settle();
    const round3 = take();
    round3.queue.flush({ message: 'boom' }, { status: 500, statusText: 'Internal Server Error' });
    await flushRound(round3, [], ['queue']);
    expect(center.settledRound()).toBe(3);
    expect(center.failedSources()).toEqual(['schedules']);
    expect(rentRow()).toBeNull();
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '已入帳：測試房租' }));
  });

  it('the page destroyed mid-write: the write still completes, the toast shows without an action, the count re-reads', async () => {
    const round2 = await openThenFocus();
    await flushRound(round2, [RENT]);
    rentButton()!.click();
    await settle();
    const post = http.expectOne(isPost);
    fixture.destroy();
    post.flush({ message: 'Internal Server Error' }, { status: 500, statusText: 'Internal Server Error' });
    await settle();
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'error', text: '測試房租 入帳失敗：Internal Server Error', action: null }));
    expect(center.round()).toBe(3);
    await flushRound(take(), []);
    expect(center.settledRound()).toBe(3);
    expect(center.tasks().some(task => task.id === 'sched:1')).toBe(false);
  });
});
