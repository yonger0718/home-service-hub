import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { errorLoggingInterceptor } from '../../interceptors/error-logging.interceptor';
import { MATCH_MEDIA } from '../../services/layout-mode.service';
import { NavBadgeService } from '../../services/nav-badge.service';
import { NotificationCenterService } from '../../services/notification-center.service';
import { ToastService } from '../../services/toast.service';
import { makeInbox, makeInboxAccount, makeItem } from '../accounting/inbox/testing';
import { InboxOut } from '../accounting/inbox/inbox.model';
import { makeAccount, makeInstance } from '../accounting/testing/fixtures';
import { NOTIF_PRIMARY_CONTROL } from './notif-row/notif-row';
import { NotificationsComponent } from './notifications';

/**
 * The page over the REAL NotificationCenterService, BillingService, AccountingService, InboxService, StatementsService
 * and ToastService, with HttpTestingController: the write / read interleavings of the plan's Review Focus. The app's
 * error-logging interceptor (which re-sends a failed read once, never a write, #411) is installed only in its own describe
 * block.
 */

/** Saturday 2026-10-24. */
const TODAY = '2026-10-24';
/** 測試銀行卡 closes on the 5th; its statement fell due Tue 2026-10-20. */
const CARD = makeAccount({ id: 11, name: '測試銀行卡', is_credit: true, closing_day: 5, due_rule: 'fixed_day', due_value: 20 });
/** 測試房租 (definition 1), instance 7, due today: one 入帳 row, id `sched:1`. */
const RENT = makeInstance({ id: 7, definition_id: 1, definition_name: '測試房租', due_date: TODAY, rule_date: TODAY });
const API = '/api/accounting';
const RENT_ROW = 'app-notif-row[data-task-id="sched:1"]';
/** 測試月費 (definition 3), two overdue instances: one 全部入帳 row, id `sched:3`. */
const FEE = [
  makeInstance({ id: 31, definition_id: 3, definition_name: '測試月費', seq: 1, due_date: '2026-10-20', rule_date: '2026-10-20' }),
  makeInstance({ id: 32, definition_id: 3, definition_name: '測試月費', seq: 2, due_date: '2026-10-22', rule_date: '2026-10-22' }),
];
const FEE_ROW = 'app-notif-row[data-task-id="sched:3"]';

interface RoundRequests {
  accounts: TestRequest;
  counterparties: TestRequest;
  queue: TestRequest;
  inbox: TestRequest;
  latest: TestRequest;
  actions: TestRequest;
  /** The portfolio and system reads (股利 first page, 國泰 pending + latest run, recalc status): see `flushPortfolio`. */
  dividends: TestRequest;
  cathayPending: TestRequest;
  cathayRuns: TestRequest;
  recalc: TestRequest;
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
  let quiet: { mockRestore(): void };

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
    const dividends = http.expectOne(get('/api/portfolio/dividends'));
    expect(dividends.request.params.get('limit')).toBe('100');
    expect(dividends.request.params.get('offset')).toBe('0');
    return {
      accounts: http.expectOne(get(`${API}/accounts`)),
      counterparties: http.expectOne(get(`${API}/counterparties`)),
      queue,
      inbox: http.expectOne(get(`${API}/reconciliation/inbox`)),
      latest: http.expectOne(get(`${API}/imports/latest`)),
      actions: http.expectOne(get(`${API}/reconciliation/actions`)),
      dividends,
      cathayPending: http.expectOne(get('/api/portfolio/imports/cathay-statement/pending')),
      cathayRuns: http.expectOne(get('/api/portfolio/imports/cathay-statement/runs/latest')),
      recalc: http.expectOne(get('/api/portfolio/imports/recalc/status')),
    };
  }

  const flushedPortfolio = new WeakSet<RoundRequests>();

  /**
   * The round's portfolio and system reads with the quiet answers (no dividend, no pending 國泰 date, a healthy run 5
   * minutes before the clock, an idle recalc chain), once per round; a superseded round's are answered too.
   */
  function flushPortfolio(round: RoundRequests): void {
    if (flushedPortfolio.has(round)) {
      return;
    }
    flushedPortfolio.add(round);
    const latest = { run_at: new Date(Date.now() - 5 * 60_000).toISOString(), result: 'ok', code: null, files_seen: 1, posted: 1 };
    round.dividends.flush({ items: [], total: 0 });
    round.cathayPending.flush({ items: [], total: 0, truncated: false });
    round.cathayRuns.flush({ latest, latest_ok: latest });
    round.recalc.flush({ state: 'idle' });
  }

  /** Answers any open card-statement reads (1,000 spent, no payment). */
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
    await settle();
  }

  /** Flushes every read of `round` except `skip`, with `queue` as the schedule queue, then the card reads. */
  async function flushRound(round: RoundRequests, queue: unknown[], skip: readonly (keyof RoundRequests)[] = []): Promise<void> {
    const open = (key: keyof RoundRequests) => !skip.includes(key);
    if (open('accounts')) round.accounts.flush([CARD]);
    if (open('counterparties')) round.counterparties.flush([]);
    if (open('queue')) round.queue.flush(queue);
    if (open('inbox')) round.inbox.flush(makeInbox([]));
    if (open('latest')) {
      round.latest.flush('', { status: 404, statusText: 'Not Found' });
      // With the interceptor the 404 is sent once more.
      for (const again of http.match(get(`${API}/imports/latest`))) {
        again.flush('', { status: 404, statusText: 'Not Found' });
      }
    }
    if (open('actions')) round.actions.flush([]);
    flushPortfolio(round);
    await flushSide();
  }

  const rentRow = () => root.querySelector<HTMLElement>(RENT_ROW);
  const rentButton = () => rentRow()?.querySelector<HTMLButtonElement>(NOTIF_PRIMARY_CONTROL) ?? null;

  /** The 國泰 date 10/28 held, `cathay:2026-10-28`, and its 確認 POST. */
  const HELD = {
    items: [{ trade_date: '2026-10-28', status: 'held', reason: null, since: '2026-10-28T22:50:00Z', last_at: '2026-10-28T22:50:00Z' }],
    total: 1,
    truncated: false,
  };
  const isAck = (req: { method: string; url: string }) =>
    req.method === 'POST' && req.url === '/api/portfolio/imports/cathay-statement/days/2026-10-28/ack';
  const cathayRow = () => root.querySelector<HTMLElement>('app-notif-row[data-task-id="cathay:2026-10-28"]');
  const cathayButton = () => cathayRow()?.querySelector<HTMLButtonElement>(NOTIF_PRIMARY_CONTROL) ?? null;

  /** The round's portfolio and system reads with `pending` as the 國泰 pending answer (the rest quiet). */
  function flushPortfolioWith(round: RoundRequests, pending: typeof HELD): void {
    flushedPortfolio.add(round);
    const latest = { run_at: new Date(Date.now() - 5 * 60_000).toISOString(), result: 'ok', code: null, files_seen: 1, posted: 1 };
    round.dividends.flush({ items: [], total: 0 });
    round.cathayPending.flush(pending);
    round.cathayRuns.flush({ latest, latest_ok: latest });
    round.recalc.flush({ state: 'idle' });
  }

  /** Page open at Thu 2026-10-29 07:00 Taipei (the 06:40 run ok today) with round 1 settled and 10/28 held. */
  async function openWithCathay(interceptor = false): Promise<void> {
    vi.setSystemTime(new Date('2026-10-29T07:00:00+08:00'));
    boot(interceptor);
    center.start();
    TestBed.tick();
    http.expectOne(get(`${API}/calendar/business-days`)).flush({ holidays: [], workdays: [], last_known_day: '2027-12-31' });
    fixture = TestBed.createComponent(NotificationsComponent);
    fixture.autoDetectChanges();
    root = fixture.nativeElement as HTMLElement;
    const round1 = take();
    flushPortfolioWith(round1, HELD);
    await flushRound(round1, []);
    expect(center.settledRound()).toBe(1);
    expect(cathayButton()?.getAttribute('aria-label')).toBe('確認：國泰對帳單 10/28');
    expect(cathayButton()!.disabled).toBe(false);
  }

  /** Page open after round 1 has settled with the rent row; then a window focus 31 s later starts round 2. */
  async function openThenFocus(interceptor = false): Promise<RoundRequests> {
    boot(interceptor);
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

  /** The app's own HTTP setup when `interceptor` (the error-logging interceptor re-sends a failed read once, never a write, #411). */
  function boot(interceptor: boolean): void {
    TestBed.configureTestingModule({
      providers: [
        interceptor ? provideHttpClient(withInterceptors([errorLoggingInterceptor])) : provideHttpClient(),
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
  }

  beforeEach(() => {
    // Failed writes and reads log with console.error on purpose; keep the run quiet.
    quiet = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
    vi.setSystemTime(new Date(2026, 9, 24, 10, 0, 0));
  });

  afterEach(() => {
    try {
      http.verify();
    } finally {
      // A failed test must not leave the module instantiated for the next one.
      TestBed.resetTestingModule();
      vi.useRealTimers();
      quiet.mockRestore();
    }
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
      expect.objectContaining({ kind: 'error', text: '測試房租 入帳失敗', action: expect.objectContaining({ label: '重試' }) }),
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

  it('重試 after the 500 that in fact committed resends instance 7; the server refuses it (409 already_posted): 此期已入帳 as done, no duplicate', async () => {
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
    // The server posts an instance once: a second post of 7 is refused, and no entry is written. The entry exists,
    // so it reads as done.
    again.flush({ detail: 'already_posted' }, { status: 409, statusText: 'Conflict' });
    await settle();
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '測試房租：此期已入帳', action: null }));
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
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'error', text: '測試房租 入帳失敗', action: null }));
    expect(center.round()).toBe(3);
    await flushRound(take(), []);
    expect(center.settledRound()).toBe(3);
    expect(center.tasks().some(task => task.id === 'sched:1')).toBe(false);
  });

  it('spec 3.4: 確認 posts the ack and the task disappears', async () => {
    await openWithCathay();
    const before = center.summary().total;
    expect(before).toBeGreaterThan(0);
    cathayButton()!.click();
    await settle();
    const post = http.expectOne(isAck);
    expect(cathayButton()!.disabled).toBe(true);
    post.flush({ trade_date: '2026-10-28', ack_status: 'held' });
    await settle();
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '已確認：國泰對帳單 10/28', action: null }));
    // No accounting write bumped a change signal: the round is the page's own invalidate().
    expect(center.round()).toBe(2);
    const round2 = take();
    expect(cathayRow()).not.toBeNull();
    expect(cathayButton()!.disabled).toBe(true);
    await flushRound(round2, []); // pending { items: [], total: 0, truncated: false }
    expect(center.settledRound()).toBe(2);
    expect(cathayRow()).toBeNull();
    expect(center.summary().total).toBe(before - 1);
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '已確認：國泰對帳單 10/28' }));
    http.expectNone(isAck);
  });

  it('a 500 that committed, then 重試 answered 404: done toast, the row leaves, two POSTs in total (one per press)', async () => {
    await openWithCathay();
    let sent = 0;
    cathayButton()!.click();
    await settle();
    http.expectOne(isAck).flush({ message: 'Internal Server Error' }, { status: 500, statusText: 'Internal Server Error' });
    sent++;
    await settle();
    http.expectNone(isAck);
    const failure = toasts.current()!;
    expect(failure).toEqual(
      expect.objectContaining({ kind: 'error', text: '國泰對帳單 10/28 確認失敗', action: expect.objectContaining({ label: '重試' }) }),
    );
    // The failure re-reads; the ack had committed, so the date is gone.
    expect(center.round()).toBe(2);
    await flushRound(take(), []);
    expect(cathayRow()).toBeNull();

    toasts.runAction(failure.id);
    await settle();
    http.expectOne(isAck).flush({ detail: 'Not Found' }, { status: 404, statusText: 'Not Found' });
    sent++;
    await settle();
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '國泰對帳單 10/28：已經不需要確認', action: null }));
    expect(center.round()).toBe(3);
    await flushRound(take(), []);
    expect(cathayRow()).toBeNull();
    http.expectNone(isAck);
    expect(sent).toBe(2);
  });

  it('portfolio reads all fail: 無法讀取：股利、匯入、國泰對帳單 with the ledger rows and their count intact', async () => {
    boot(false);
    center.start();
    TestBed.tick();
    http.expectOne(get(`${API}/calendar/business-days`)).flush({ holidays: [], workdays: [], last_known_day: '2027-12-31' });
    fixture = TestBed.createComponent(NotificationsComponent);
    fixture.autoDetectChanges();
    root = fixture.nativeElement as HTMLElement;
    await flushRound(take(), [RENT]);
    expect(center.settledRound()).toBe(1);
    const rowIds = () => Array.from(root.querySelectorAll('app-notif-row')).map(r => r.getAttribute('data-task-id'));
    const ledgerIds = rowIds();
    const total = center.summary().total;
    expect(ledgerIds).toContain('sched:1');
    expect(total).toBe(ledgerIds.length);

    center.invalidate();
    TestBed.tick();
    const round2 = take();
    flushedPortfolio.add(round2);
    const boom = { status: 500, statusText: 'Server Error' };
    round2.dividends.flush('boom', boom);
    round2.cathayPending.flush('boom', boom);
    // One source over two reads: the failed pending read cancels its sibling.
    if (!round2.cathayRuns.cancelled) {
      round2.cathayRuns.flush('boom', boom);
    }
    round2.recalc.flush('boom', boom);
    await flushRound(round2, [RENT]);
    expect(center.settledRound()).toBe(2);
    expect(center.failedSources()).toEqual(['dividends', 'imports', 'cathay']);
    expect(root.querySelector('.notif-failed')?.textContent).toContain('無法讀取：股利、匯入、國泰對帳單');
    expect(rowIds()).toEqual(ledgerIds);
    expect(center.summary().total).toBe(total);
    expect(TestBed.inject(NavBadgeService).summary().total).toBe(total);
    expect(root.querySelector('.page-subtitle')?.textContent).toContain(`${total} 件要處理`);
  });

  describe('with the app\'s error-logging interceptor (a failed write is sent once, #411)', () => {
    const posts: TestRequest[] = [];
    const takePost = () => {
      const req = http.expectOne(isPost);
      posts.push(req);
      return req;
    };

    beforeEach(() => {
      posts.length = 0;
      for (const method of ['group', 'groupEnd', 'error', 'warn', 'log'] as const) {
        vi.spyOn(console, method).mockImplementation(() => undefined);
      }
    });
    afterEach(() => vi.restoreAllMocks());

    it('POST 500 is not re-sent: one POST, 重試 offered; 重試 answered 409 already_posted (the first had committed) shows one done toast, two POSTs in all', async () => {
      await flushRound(await openThenFocus(true), [RENT]);
      expect(center.settledRound()).toBe(2);
      rentButton()!.click();
      await settle();
      takePost().flush({ message: 'Internal Server Error' }, { status: 500, statusText: 'Internal Server Error' });
      await settle();
      http.expectNone(isPost);
      expect(posts.length).toBe(1);
      const failure = toasts.current()!;
      expect(failure).toEqual(expect.objectContaining({ kind: 'error', text: '測試房租 入帳失敗', action: expect.objectContaining({ label: '重試' }) }));
      // An error bumps nothing: the page's own re-read (round 3).
      expect(center.round()).toBe(3);
      await flushRound(take(), [RENT]);
      expect(rentButton()!.disabled).toBe(false);

      toasts.runAction(failure.id);
      await settle();
      takePost().flush({ detail: 'already_posted' }, { status: 409, statusText: 'Conflict' });
      await settle();
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '測試房租：此期已入帳', action: null }));
      const reread = take();
      expect(rentButton()!.disabled).toBe(true);
      reread.queue.flush([RENT]); // even a list that still has it: the row waits for the round to settle
      await settle();
      expect(rentButton()!.disabled).toBe(true);
      await flushRound(reread, [], ['queue']);
      expect(rentButton()!.disabled).toBe(false);
      http.expectNone(isPost);
      expect(posts.length).toBe(2);
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '測試房租：此期已入帳' }));
    });

    it('catch-up 500 is not re-sent: one POST, 重試 offered; 重試 answered { posted: [], failed: null } shows 「測試月費：沒有待入帳的期數」', async () => {
      const isCatchUp = (req: { method: string; url: string }) =>
        req.method === 'POST' && req.url === `${API}/schedules/definitions/3/catch-up`;
      await flushRound(await openThenFocus(true), FEE);
      const button = () => root.querySelector(FEE_ROW)?.querySelector<HTMLButtonElement>(NOTIF_PRIMARY_CONTROL) ?? null;
      expect(button()?.textContent).toContain('全部入帳');
      button()!.click();
      await settle();
      http.expectOne(isCatchUp).flush({ message: 'Internal Server Error' }, { status: 500, statusText: 'Internal Server Error' });
      await settle();
      http.expectNone(isCatchUp);
      const failure = toasts.current()!;
      expect(failure).toEqual(expect.objectContaining({ kind: 'error', action: expect.objectContaining({ label: '重試' }) }));
      await flushRound(take(), FEE);

      toasts.runAction(failure.id);
      await settle();
      http.expectOne(isCatchUp).flush({ posted: [], failed: null, definition: {} });
      await settle();
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '測試月費：沒有待入帳的期數', action: null }));
      expect(button()!.disabled).toBe(true);
      await flushRound(take(), []);
      expect(root.querySelector(FEE_ROW)).toBeNull();
      http.expectNone(isCatchUp);
    });

    it("with the app's error-logging interceptor a failed ack is sent once", async () => {
      await openWithCathay(true);
      const acks: TestRequest[] = [];
      cathayButton()!.click();
      await settle();
      acks.push(http.expectOne(isAck));
      acks[0].flush({ message: 'Internal Server Error' }, { status: 500, statusText: 'Internal Server Error' });
      await settle();
      http.expectNone(isAck);
      expect(acks.length).toBe(1);
      const failure = toasts.current()!;
      expect(failure).toEqual(
        expect.objectContaining({ kind: 'error', text: '國泰對帳單 10/28 確認失敗', action: expect.objectContaining({ label: '重試' }) }),
      );
      // Not committed: the re-read still lists the date, and the row is pressable again once it settles.
      expect(center.round()).toBe(2);
      const round2 = take();
      flushPortfolioWith(round2, HELD);
      await flushRound(round2, []);
      expect(cathayButton()!.disabled).toBe(false);

      toasts.runAction(failure.id);
      await settle();
      acks.push(http.expectOne(isAck));
      http.expectNone(isAck);
      acks[1].flush({ trade_date: '2026-10-28', ack_status: 'held' });
      await settle();
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '已確認：國泰對帳單 10/28', action: null }));
      await flushRound(take(), []);
      expect(cathayRow()).toBeNull();
      http.expectNone(isAck);
      expect(acks.length).toBe(2);
    });

    it('重試 pressed twice sends one write', async () => {
      await flushRound(await openThenFocus(true), [RENT]);
      rentButton()!.click();
      await settle();
      takePost().flush({ message: 'Internal Server Error' }, { status: 500, statusText: 'Internal Server Error' });
      await settle();
      http.expectNone(isPost);
      const failure = toasts.current()!;
      expect(failure).toEqual(expect.objectContaining({ kind: 'error', action: expect.objectContaining({ label: '重試' }) }));
      await flushRound(take(), [RENT]);
      expect(rentButton()!.disabled).toBe(false);

      toasts.runAction(failure.id);
      toasts.runAction(failure.id);
      await settle();
      rentButton()!.click(); // the row is busy again
      await settle();
      const second = takePost();
      http.expectNone(isPost);
      second.flush({ ...RENT, status: 'posted' });
      await settle();
      expect(posts.length).toBe(2);
      expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '已入帳：測試房租' }));
      await flushRound(take(), []);
      expect(rentRow()).toBeNull();
    });
  });
  describe('states over the real services', () => {
    /** The page opened with nothing yet answered (round 1). */
    async function openFresh(): Promise<RoundRequests> {
      boot(false);
      center.start();
      TestBed.tick();
      http.expectOne(get(`${API}/calendar/business-days`)).flush({ holidays: [], workdays: [], last_known_day: '2027-12-31' });
      fixture = TestBed.createComponent(NotificationsComponent);
      fixture.autoDetectChanges();
      root = fixture.nativeElement as HTMLElement;
      return take();
    }

    /** Every account-level read of the round, with `inbox`; the card statements are not answered. */
    async function answerReads(round: RoundRequests, inbox: InboxOut): Promise<void> {
      round.accounts.flush([CARD]);
      round.counterparties.flush([]);
      round.queue.flush([]);
      round.inbox.flush(inbox);
      round.latest.flush('', { status: 404, statusText: 'Not Found' });
      round.actions.flush([]);
      flushPortfolio(round);
      await settle();
    }

    /** The card's summary and, unless `holdPayments`, its entries (the payments read). */
    async function answerCard(spend: string, holdPayments = false): Promise<TestRequest[]> {
      for (const req of http.match(get(`${API}/accounts/11/summary`))) {
        req.flush({
          account_id: 11, currency: 'TWD', date_from: '', date_to: '', spend, income: '0', rewards: '0',
          net: spend, end_balance: spend, count: 1,
        });
      }
      await settle();
      const held = holdPayments ? [] : http.match(get(`${API}/accounts/11/entries`));
      for (const req of held) {
        req.flush({ items: [], total: 0, limit: 100, offset: 0 });
      }
      await settle();
      return held;
    }

    const loadingRow = () => root.querySelector('.notif-loading');
    const emptyBlock = () => root.querySelector('.notif-empty');
    const billRow = () => root.querySelector('app-notif-row[data-task-id^="bill:"]');

    it("first open with a slow card statement: 讀取中… until the card's last read lands, then the bill row, 都處理好了 never in between", async () => {
      const round = await openFresh();
      await answerReads(round, makeInbox([]));
      await answerCard('-1000.0000', true);
      expect(center.loaded()).toBe(false);
      expect(loadingRow()).not.toBeNull();
      expect(emptyBlock()).toBeNull();
      http.match(get(`${API}/accounts/11/entries`)).forEach(req => req.flush({ items: [], total: 0, limit: 100, offset: 0 }));
      await settle();
      expect(center.loaded()).toBe(true);
      expect(billRow()).not.toBeNull();
      expect(loadingRow()).toBeNull();
      expect(emptyBlock()).toBeNull();
    });

    it('first open with nothing to do: 都處理好了 only after the card statements answered', async () => {
      const round = await openFresh();
      await answerReads(round, makeInbox([]));
      await answerCard('0', true);
      expect(emptyBlock()).toBeNull();
      expect(loadingRow()).not.toBeNull();
      http.match(get(`${API}/accounts/11/entries`)).forEach(req => req.flush({ items: [], total: 0, limit: 100, offset: 0 }));
      await settle();
      expect(center.loaded()).toBe(true);
      expect(emptyBlock()?.querySelector('h2')?.textContent).toContain('都處理好了');
      expect(loadingRow()).toBeNull();
    });

    it('a later round that finds a new due card: no all-clear, zero chips while its statement reads are held; then the bill', async () => {
      const round1 = await openFresh();
      round1.accounts.flush([]);
      round1.counterparties.flush([]);
      round1.queue.flush([]);
      round1.inbox.flush(makeInbox([]));
      round1.latest.flush('', { status: 404, statusText: 'Not Found' });
      round1.actions.flush([]);
      flushPortfolio(round1);
      await settle();
      expect(center.settledRound()).toBe(1);
      expect(emptyBlock()).not.toBeNull();
      const chipText = () => [...root.querySelectorAll('.notif-chips button')].map(b => b.textContent?.trim()).join('|');
      const sub = () => root.querySelector('.page-subtitle')?.textContent?.trim();
      expect(sub()).toBe('沒有要處理的事');

      center.invalidate();
      TestBed.tick();
      expect(center.round()).toBe(2);
      const round2 = take();
      round2.accounts.flush([CARD]);
      round2.counterparties.flush([]);
      round2.queue.flush([]);
      round2.inbox.flush(makeInbox([]));
      round2.latest.flush('', { status: 404, statusText: 'Not Found' });
      round2.actions.flush([]);
      flushPortfolio(round2);
      await settle();
      await answerCard('-1000.0000', true);
      expect(center.loading()).toBe(true);
      expect(emptyBlock()).toBeNull();
      expect(root.textContent).not.toContain('都處理好了');
      expect(sub()).toBe('讀取中…');
      expect(chipText()).not.toMatch(/\d/);
      expect(root.querySelectorAll('p.notif-loading[role="status"]').length).toBe(1);

      http.match(get(`${API}/accounts/11/entries`)).forEach(req => req.flush({ items: [], total: 0, limit: 100, offset: 0 }));
      await settle();
      expect(center.loading()).toBe(false);
      expect(billRow()).not.toBeNull();
      expect(emptyBlock()).toBeNull();
      expect(loadingRow()).toBeNull();
      expect(chipText()).toContain('1');
      expect(sub()).toContain('1 件');
    });

    it('an inbox answering counts.ask_items 3 with 1000 folded items: the uncounted catch-all note and the partial line, no 都處理好了', async () => {
      const round = await openFresh();
      const folded = Array.from({ length: 1000 }, (_, i) => makeItem({ case_id: 5000 + i, folded: true, question: 'proposed' }));
      await answerReads(round, makeInbox([makeInboxAccount({ id: 12, name: '測試帳戶', items: folded })], { ask_items: 3, ask: 3 }));
      await answerCard('0');
      expect(center.loaded()).toBe(true);
      expect(emptyBlock()).toBeNull();
      // The catch-all is a plain note, not a counted row: nothing to do is counted, the all-clear still does not show.
      expect(root.querySelector('p.notif-overflow')?.textContent).toContain('還有 3 筆對帳項目未列出');
      expect(root.querySelectorAll('app-notif-row').length).toBe(0);
      expect(center.summary().total).toBe(0);
      expect(root.querySelector('.page-subtitle')?.textContent?.trim()).toBe('還有對帳項目未列出');
    });

    it('a truncated inbox counts one per statement: page, section heading, chips and the sidebar facade all say 1 while the catch-all note shows', async () => {
      const round = await openFresh();
      const listed = Array.from({ length: 6 }, (_, i) =>
        makeItem({ case_id: 7000 + i, question: 'bank_only', statement: { id: 31, period_end: '2026-10-05' } }),
      );
      await answerReads(round, makeInbox([makeInboxAccount({ id: 12, name: '測試銀行卡', items: listed })], { ask_items: 10, ask: 10 }));
      for (const req of http.match(get(`${API}/accounts/12/statements`))) {
        req.flush([]);
      }
      await answerCard('0');
      const nav = TestBed.inject(NavBadgeService);
      expect(center.truncated()).toBe(true);
      expect(root.querySelectorAll('app-notif-row').length).toBe(1);
      expect(root.querySelector('app-notif-row')?.textContent).toContain('6 筆要你決定');
      expect(root.querySelector('section.notif-section h2')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('要你確認 1');
      expect(root.querySelector('.notif-chips button')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('全部1');
      expect(root.querySelector('.page-subtitle')?.textContent).toContain('1 件要處理');
      expect(center.summary().total).toBe(1);
      expect(nav.summary().total).toBe(1);
      expect(nav.summary().label).toBe('通知，1 件要處理');
      const note = root.querySelector('p.notif-overflow');
      expect(note?.textContent?.replace(/\s+/g, ' ').trim()).toBe('還有 4 筆對帳項目未列出 · 到提醒中心查看 ›');
      expect(note?.closest('section')).toBeNull();
    });

    it('a truncated inbox with counts.einvoice_ask 2: the 2 張發票要確認 task is counted (statement rows + 1) on page and facade, the overflow note stays uncounted', async () => {
      const round = await openFresh();
      const listed = Array.from({ length: 6 }, (_, i) =>
        makeItem({ case_id: 7100 + i, question: 'bank_only', statement: { id: 31, period_end: '2026-10-05' } }),
      );
      await answerReads(round, makeInbox([makeInboxAccount({ id: 12, name: '測試銀行卡', items: listed })], { ask_items: 10, ask: 10, einvoice_ask: 2 }));
      for (const req of http.match(get(`${API}/accounts/12/statements`))) {
        req.flush([]);
      }
      await answerCard('0');
      const nav = TestBed.inject(NavBadgeService);
      expect(center.truncated()).toBe(true);
      const rows = Array.from(root.querySelectorAll('app-notif-row'));
      expect(rows.length).toBe(2);
      expect(rows.some((row) => row.textContent?.includes('2 張發票要確認'))).toBe(true);
      expect(root.querySelector('.notif-chips button')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('全部2');
      expect(root.querySelector('.page-subtitle')?.textContent).toContain('2 件要處理');
      expect(center.summary().total).toBe(2);
      expect(nav.summary().total).toBe(2);
      const note = root.querySelector('p.notif-overflow');
      expect(note?.textContent?.replace(/\s+/g, ' ').trim()).toBe('還有 4 筆對帳項目未列出 · 到提醒中心查看 ›');
      expect(note?.closest('section')).toBeNull();
    });

    it('ONE failed source: page subtitle, chips, section heading and the sidebar facade all say 1', async () => {
      const round = await openFresh();
      round.accounts.flush([CARD]);
      round.counterparties.flush('boom', { status: 500, statusText: 'Server Error' });
      round.queue.flush([RENT]);
      round.inbox.flush(makeInbox([]));
      round.latest.flush('', { status: 404, statusText: 'Not Found' });
      round.actions.flush([]);
      flushPortfolio(round);
      await settle();
      await answerCard('0');
      expect(center.failedSources()).toEqual(['debts']);
      const nav = TestBed.inject(NavBadgeService);
      expect(nav.summary().total).toBe(1);
      expect(root.querySelector('.page-subtitle')?.textContent?.trim()).toBe('部分資料無法讀取 · 1 件要處理');
      expect(root.querySelector('.notif-chips button')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('全部1');
      expect(root.querySelector('section.notif-section h2')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('今天 1');
      expect(root.querySelectorAll('app-notif-row').length).toBe(nav.summary().total);
      expect(root.querySelector('.notif-failed')?.textContent).toContain('無法讀取：借還款');
    });

    it('FIVE failed sources with one bill row counted: the row shows with its numbers (page chips, heading, subtitle) equal to the facade count', async () => {
      const round = await openFresh();
      const cardB = makeAccount({ id: 12, name: '測試二號卡', is_credit: true, closing_day: 5, due_rule: 'fixed_day', due_value: 20 });
      round.accounts.flush([CARD, cardB]);
      round.counterparties.flush('boom', { status: 500, statusText: 'Server Error' });
      round.queue.flush('boom', { status: 500, statusText: 'Server Error' });
      round.inbox.flush('boom', { status: 500, statusText: 'Server Error' });
      round.latest.flush('boom', { status: 500, statusText: 'Server Error' });
      round.actions.flush([]);
      flushPortfolio(round);
      await settle();
      for (const req of http.match(get(`${API}/accounts/11/summary`))) {
        req.flush('boom', { status: 500, statusText: 'Server Error' });
      }
      for (const req of http.match(get(`${API}/accounts/12/summary`))) {
        req.flush({
          account_id: 12, currency: 'TWD', date_from: '', date_to: '', spend: '-1000.0000', income: '0', rewards: '0',
          net: '-1000.0000', end_balance: '-1000.0000', count: 1,
        });
      }
      await settle();
      for (const req of http.match(get(`${API}/accounts/12/entries`))) {
        req.flush({ items: [], total: 0, limit: 100, offset: 0 });
      }
      for (const req of http.match(get(`${API}/accounts/11/entries`))) {
        req.flush('boom', { status: 500, statusText: 'Server Error' });
      }
      await settle();
      const nav = TestBed.inject(NavBadgeService);
      expect(center.failedSources().length).toBe(5);
      expect(nav.summary().total).toBe(1);
      expect(root.querySelectorAll('app-notif-row').length).toBe(1);
      const chips = Array.from(root.querySelectorAll<HTMLElement>('.notif-chips button'));
      expect(Array.from(root.querySelectorAll('.chip-count')).map(c => c.textContent?.trim())[0]).toBe(String(nav.summary().total));
      expect(chips[0].getAttribute('aria-label')).toBe('全部，1 件');
      expect(root.querySelector('section.notif-section h2 .sec-count')?.textContent?.trim()).toBe('1');
      expect(root.querySelector('.page-subtitle')?.textContent?.trim()).toBe('部分資料無法讀取 · 1 件要處理 · 1 件已逾期');
    });

    it('a failed inbox with nothing else: the failure line and no 都處理好了; 重試 re-reads and the all-clear returns when it answers', async () => {
      const round = await openFresh();
      round.accounts.flush([CARD]);
      round.counterparties.flush([]);
      round.queue.flush([]);
      round.inbox.flush('boom', { status: 500, statusText: 'Server Error' });
      round.latest.flush('', { status: 404, statusText: 'Not Found' });
      round.actions.flush([]);
      flushPortfolio(round);
      await settle();
      await answerCard('0');
      expect(center.loaded()).toBe(true);
      expect(emptyBlock()).toBeNull();
      expect(root.querySelector('.notif-failed')?.textContent).toContain('無法讀取：對帳');
      root.querySelector<HTMLButtonElement>('.notif-failed .banner-action')!.click();
      await settle();
      await answerReads(take(), makeInbox([]));
      await answerCard('0');
      expect(root.querySelector('.notif-failed')).toBeNull();
      expect(emptyBlock()).not.toBeNull();
    });
  });
});
