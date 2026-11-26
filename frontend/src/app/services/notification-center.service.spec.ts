import { WritableSignal, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Observable, Subject, of, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BillingService } from '../components/accounting/billing/billing.service';
import { InboxOut, InboxItem, ReconciliationAction } from '../components/accounting/inbox/inbox.model';
import { InboxService } from '../components/accounting/inbox/inbox.service';
import { makeInbox, makeInboxAccount, makeItem } from '../components/accounting/inbox/testing';
import { StatementSummary } from '../components/accounting/statements/statement.model';
import { StatementsService } from '../components/accounting/statements/statements.service';
import { makeAccount, makeEntry, makeInstance } from '../components/accounting/testing/fixtures';
import { NO_TASKS } from '../components/notifications/notif-task';
import { INBOX_READ_LIMIT } from '../components/notifications/sources/recon';
import { BusinessDays, Counterparty, ImportRun, LedgerAccount, ScheduleInstance } from '../models/accounting.model';
import {
  CathayPending,
  CathayPendingDay,
  CathayRun,
  CathayRuns,
  Dividend,
  FuturesPending,
  FuturesPendingItem,
  FuturesRuns,
  Paged,
  RecalcStatus,
} from '../models/portfolio.model';
import { AccountingService } from './accounting.service';
import { ImportHistoryService } from './import-history.service';
import { NavigationPreferencesService } from './navigation-preferences.service';
import {
  BADGE_REFRESH_MIN_MS,
  DIVIDEND_MAX_PAGES,
  NOTIF_CLOCK,
  NotificationCenterService,
  READ_TIMEOUT_MS,
} from './notification-center.service';
import { SNOOZE_KEY } from '../components/notifications/snooze';
import { PortfolioService } from './portfolio.service';

/** Today in this spec: Saturday 2026-10-24. */
const TODAY = '2026-10-24';

/** Every call returns a fresh Subject the test answers by hand, so response order is under the test's control. */
function calls<T>(): { subjects: Subject<T>[]; fn: (...args: unknown[]) => Observable<T> } {
  const subjects: Subject<T>[] = [];
  return {
    subjects,
    fn: () => {
      const subject = new Subject<T>();
      subjects.push(subject);
      return subject;
    },
  };
}

const answered = new WeakSet<Subject<unknown>>();

function land<T>(subject: Subject<T>, value: T): void {
  answered.add(subject as Subject<unknown>);
  subject.next(value);
  subject.complete();
}

function fail<T>(subject: Subject<T>): void {
  answered.add(subject as Subject<unknown>);
  subject.error(new Error('boom'));
}

/** Lets pending promise callbacks (the card statements' settle promise) run. */
async function microtasks(): Promise<void> {
  for (let i = 0; i < 10; i++) {
    await Promise.resolve();
  }
}

function importRun(needsReview: number): ImportRun {
  return {
    id: 1,
    status: 'succeeded',
    started_at: '2026-10-24T01:00:00Z',
    finished_at: '2026-10-24T01:01:00Z',
    file_name: 'backup.zip',
    file_sha256: 'abc',
    row_count: 10,
    summary: { needs_review: { count: needsReview, reasons: {} } } as unknown as ImportRun['summary'],
  };
}

/** A card closing on the 5th: its current statement (2026-09-06..10-05) falls due on `dueDay` of October. */
function card(id: number, dueDay: number, name = `測試銀行卡${id}`): LedgerAccount {
  return makeAccount({ id, name, is_credit: true, closing_day: 5, due_rule: 'fixed_day', due_value: dueDay });
}

/** Card 9: closes on the 15th, due on the 5th → the 2026-09-16..10-15 statement falls due Thu 2026-11-05. */
function lateCard(id = 9): LedgerAccount {
  return makeAccount({ id, name: `卡 ${id}`, is_credit: true, closing_day: 15, due_rule: 'fixed_day', due_value: 5 });
}

function person(id: number, name: string, openCount = 1): Counterparty {
  return { id, name, open_amounts: [{ currency: 'TWD', amount: '-500.0000' }], open_count: openCount } as Counterparty;
}

function instance(id: number, definitionId: number, name: string, due: string): ScheduleInstance {
  return makeInstance({ id, definition_id: definitionId, definition_name: name, due_date: due, rule_date: due });
}

function askItem(caseId: number, statementId: number, overrides: Partial<InboxItem> = {}): InboxItem {
  return makeItem({ case_id: caseId, statement: { id: statementId, period_end: '2026-09-30' }, ...overrides });
}

/** One account (測試帳戶) whose statement 10 has `n` unfolded ask items; `askItems` defaults to `n`. */
function inboxWith(n: number, counts: Partial<InboxOut['counts']> = {}): InboxOut {
  const items = Array.from({ length: n }, (_, i) => askItem(i + 1, 10));
  return makeInbox(n > 0 ? [makeInboxAccount({ id: 1, name: '測試帳戶', items })] : [], { ask: n, ask_items: n, ...counts });
}

function statement(id: number, matched: number, open: number): StatementSummary {
  return { id, account_id: 1, period_end: '2026-09-30', matched_count: matched, open_case_count: open } as StatementSummary;
}

function action(id: number, createdAt: string, revertedBy: number | null = null): ReconciliationAction {
  return { id, created_at: createdAt, reverted_by_action_id: revertedBy } as ReconciliationAction;
}

/** A dividend row (fictional symbols 9901–9903); `receipt_status` pending unless given. */
function dividend(id: number, symbol: string, paymentDate: string | null, receiptStatus: Dividend['receipt_status'] = 'pending'): Dividend {
  return { id, symbol, amount: 100, ex_dividend_date: '2026-09-01', payment_date: paymentDate, receipt_status: receiptStatus };
}

/** `n` pending dividends paid before today, ids from `firstId`. */
function dueDividends(n: number, firstId = 1): Dividend[] {
  return Array.from({ length: n }, (_, i) => dividend(firstId + i, '9901', '2026-10-20'));
}

/** A 國泰 run `minutesAgo` before `now`. */
function run(now: Date, result: CathayRun['result'] = 'ok', minutesAgo = 5): CathayRun {
  return { run_at: new Date(now.getTime() - minutesAgo * 60_000).toISOString(), result, code: null, files_seen: 1, posted: 1 };
}

function pendingDay(tradeDate: string, overrides: Partial<CathayPendingDay> = {}): CathayPendingDay {
  return { trade_date: tradeDate, status: 'held', reason: null, since: null, last_at: null, ...overrides };
}

function futuresItem(key: string, overrides: Partial<FuturesPendingItem> = {}): FuturesPendingItem {
  return { key, kind: 'day', status: 'findings', reason: 'gap:1', since: null, last_at: null, ...overrides };
}

/** 50 pending dates from 2026-07-01 on (the API's listing limit). */
function fiftyDays(): CathayPendingDay[] {
  return Array.from({ length: 50 }, (_, i) => pendingDay(new Date(Date.UTC(2026, 6, 1 + i)).toISOString().slice(0, 10)));
}

describe('NotificationCenterService', () => {
  let accounts: ReturnType<typeof calls<LedgerAccount[]>>;
  let counterparties: ReturnType<typeof calls<Counterparty[]>>;
  let queue: ReturnType<typeof calls<ScheduleInstance[]>>;
  let latest: ReturnType<typeof calls<ImportRun | null>>;
  let recalc: ReturnType<typeof calls<RecalcStatus>>;
  let inbox: ReturnType<typeof calls<InboxOut>>;
  let actions: ReturnType<typeof calls<ReconciliationAction[]>>;
  let statements: ReturnType<typeof calls<StatementSummary[]>>;
  let dividends: ReturnType<typeof calls<Paged<Dividend>>>;
  let cathayPending: ReturnType<typeof calls<CathayPending>>;
  let cathayRuns: ReturnType<typeof calls<CathayRuns>>;
  let futuresPending: ReturnType<typeof calls<FuturesPending>>;
  let futuresRuns: ReturnType<typeof calls<FuturesRuns>>;
  let accountingStub: Record<string, unknown>;
  let getRecalcStatus: ReturnType<typeof vi.fn>;
  let getDividends: ReturnType<typeof vi.fn>;
  let getCathayPending: ReturnType<typeof vi.fn>;
  let getCathayRuns: ReturnType<typeof vi.fn>;
  let getFuturesPending: ReturnType<typeof vi.fn>;
  let getFuturesRuns: ReturnType<typeof vi.fn>;
  /** The round clock (NOTIF_CLOCK); a test moves it by assigning `clockNow`. */
  let clockNow: Date;
  let clock: ReturnType<typeof vi.fn<() => Date>>;
  let getInbox: ReturnType<typeof vi.fn>;
  let getActions: ReturnType<typeof vi.fn>;
  let listStatements: ReturnType<typeof vi.fn>;
  let inboxChanged: WritableSignal<number>;

  beforeEach(() => {
    localStorage.clear();
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 9, 24, 10, 0, 0));
    accounts = calls();
    counterparties = calls();
    queue = calls();
    latest = calls();
    recalc = calls();
    inbox = calls();
    actions = calls();
    statements = calls();
    dividends = calls();
    cathayPending = calls();
    cathayRuns = calls();
    getRecalcStatus = vi.fn(recalc.fn);
    getDividends = vi.fn(dividends.fn);
    getCathayPending = vi.fn(cathayPending.fn);
    getCathayRuns = vi.fn(cathayRuns.fn);
    futuresPending = calls();
    futuresRuns = calls();
    getFuturesPending = vi.fn(futuresPending.fn);
    getFuturesRuns = vi.fn(futuresRuns.fn);
    clockNow = new Date(2026, 9, 24, 10, 0, 0);
    clock = vi.fn(() => clockNow);
    getInbox = vi.fn(inbox.fn);
    getActions = vi.fn(actions.fn);
    listStatements = vi.fn(statements.fn);
    inboxChanged = signal(0);
    accountingStub = {
      entriesChanged: signal(0),
      accountsChanged: signal(0),
      getAccounts: vi.fn(accounts.fn),
      getCounterparties: vi.fn(counterparties.fn),
      getScheduleInstances: vi.fn(queue.fn),
      getLatestImport: vi.fn(latest.fn),
      // Every card's statement: 1,000 spent, nothing paid yet.
      getAccountSummary: vi.fn((accountId: number) =>
        of({
          account_id: accountId, currency: 'TWD', date_from: '', date_to: '', spend: '-1000', income: '0',
          rewards: '0', net: '-1000', end_balance: '-1000', count: 1,
        }),
      ),
      getEntries: vi.fn(() => of({ items: [], total: 0, limit: 100, offset: 0 })),
      getBusinessDays: vi.fn((): Observable<BusinessDays> => of({ holidays: [], workdays: [], last_known_day: '2027-12-31' })),
    };
    configure();
  });

  function configure(): void {
    TestBed.configureTestingModule({
      providers: [
        { provide: AccountingService, useValue: accountingStub },
        { provide: PortfolioService, useValue: { getRecalcStatus, getDividends, getCathayPending, getCathayRuns, getFuturesPending, getFuturesRuns } },
        { provide: InboxService, useValue: { get: getInbox, actions: getActions, changed: inboxChanged } },
        { provide: StatementsService, useValue: { list: listStatements, changed: signal(0) } },
        { provide: NOTIF_CLOCK, useValue: clock },
      ],
    });
  }

  afterEach(() => {
    TestBed.resetTestingModule();
    localStorage.clear();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function service(): NotificationCenterService {
    return TestBed.inject(NotificationCenterService);
  }

  type Answer<T> = T | 'error' | 'skip';

  /**
   * Answers round `index` (0-based: the index of each source's call) of every source still open; `'error'` fails one,
   * `'skip'` leaves it open. The dividends answer is the round's first page (index-based: a test that reads more than
   * one page in a round lands the later pages itself). The quiet defaults: no dividend, no pending 國泰 date, a healthy
   * 國泰 run 5 minutes before the clock, an idle recalc chain.
   */
  async function answer(
    index: number,
    values: {
      accounts?: Answer<LedgerAccount[]>;
      counterparties?: Answer<Counterparty[]>;
      queue?: Answer<ScheduleInstance[]>;
      latest?: Answer<ImportRun | null>;
      inbox?: Answer<InboxOut>;
      actions?: Answer<ReconciliationAction[]>;
      recalc?: Answer<RecalcStatus>;
      dividends?: Answer<Paged<Dividend>>;
      cathayPending?: Answer<CathayPending>;
      cathayRuns?: Answer<CathayRuns>;
      futuresPending?: Answer<FuturesPending>;
      futuresRuns?: Answer<FuturesRuns>;
    } = {},
  ): Promise<void> {
    const settle = <T>(subject: Subject<T> | undefined, value: Answer<T> | undefined, fallback: T) => {
      if (!subject || answered.has(subject as Subject<unknown>) || value === 'skip') {
        return;
      }
      if (value === 'error') {
        fail(subject);
      } else {
        land(subject, value === undefined ? fallback : value);
      }
    };
    settle(accounts.subjects[index], values.accounts, []);
    settle(counterparties.subjects[index], values.counterparties, []);
    settle(queue.subjects[index], values.queue, []);
    settle(latest.subjects[index], values.latest, null);
    settle(inbox.subjects[index], values.inbox, makeInbox([]));
    settle(actions.subjects[index], values.actions, []);
    settle(recalc.subjects[index], values.recalc, { state: 'idle' } as RecalcStatus);
    settle(dividends.subjects[index], values.dividends, { items: [], total: 0 });
    settle(cathayPending.subjects[index], values.cathayPending, { items: [], total: 0, truncated: false });
    const healthy = run(clockNow);
    settle(cathayRuns.subjects[index], values.cathayRuns, { latest: healthy, latest_ok: healthy });
    settle(futuresPending.subjects[index], values.futuresPending, { items: [], total: 0, truncated: false });
    settle(futuresRuns.subjects[index], values.futuresRuns, { latest: healthy, latest_ok: healthy });
    TestBed.tick();
    await microtasks();
    TestBed.tick();
  }

  const ids = (svc: NotificationCenterService) => svc.tasks().map(task => task.id);

  // ---- snooze (PR E2) ----------------------------------------------------------------------------------------------

  describe('snooze (PR E2)', () => {
    const at = (d: number, h: number) => new Date(2026, 9, d, h, 0, 0);
    const task = (svc: NotificationCenterService, id: string) => svc.countedTasks().find(t => t.id === id)!;
    const stored = () => JSON.parse(localStorage.getItem(SNOOZE_KEY) ?? '{}') as Record<string, unknown>;
    const months = (n: number) => Array.from({ length: n }, (_, i) => instance(i + 1, 1, '測試月費', `2026-10-${20 + i}`));

    it('a snoozed task leaves tasks, the count and the badge summary; countedTasks keeps it; this device stores it', async () => {
      const svc = service();
      svc.start();
      await answer(0, { queue: [instance(1, 1, '測試房租', TODAY), instance(2, 2, '測試月費', TODAY)] });
      expect(svc.summary().total).toBe(2);
      const until = svc.snooze(task(svc, 'sched:1'), 'tomorrow');
      TestBed.tick();
      expect(until).toEqual(at(25, 9));
      expect(ids(svc)).toEqual(['sched:2']);
      expect(svc.summary()).toEqual({ total: 1, overdue: 0, tone: 'primary', label: '通知，1 件要處理' });
      expect(svc.snoozedCount()).toBe(1);
      expect(svc.countedTasks().map(t => t.id)).toEqual(['sched:1', 'sched:2']);
      expect(stored()).toEqual({ 'sched:1': { until: at(25, 9).toISOString(), tier: 'today', from: 'schedules', wake: { count: 1 } } });
    });

    it('snoozedEarliest is the earliest end among the hidden tasks; showSnoozed clears them and returns them; restoreSnoozes hides them again', async () => {
      const svc = service();
      svc.start();
      await answer(0, { queue: [instance(1, 1, '測試房租', TODAY), instance(2, 2, '測試月費', TODAY)] });
      expect(svc.snoozedEarliest()).toBeNull();
      svc.snooze(task(svc, 'sched:1'), 'monday');
      svc.snooze(task(svc, 'sched:2'), '3h');
      TestBed.tick();
      expect(svc.snoozedEarliest()).toEqual(at(24, 13));
      const saved = svc.showSnoozed();
      TestBed.tick();
      expect(Object.keys(saved).sort()).toEqual(['sched:1', 'sched:2']);
      expect(ids(svc)).toEqual(['sched:1', 'sched:2']);
      expect(svc.snoozedCount()).toBe(0);
      expect(svc.snoozedEarliest()).toBeNull();
      expect(localStorage.getItem(SNOOZE_KEY)).toBeNull();
      svc.restoreSnoozes(saved);
      TestBed.tick();
      expect(ids(svc)).toEqual([]);
      expect(svc.snoozedCount()).toBe(2);
      expect(svc.snoozedEarliest()).toEqual(at(24, 13));
    });

    it('fewer open instances keep the snooze; a new instance wakes the task and drops the entry', async () => {
      const svc = service();
      svc.start();
      await answer(0, { queue: months(3) });
      svc.snooze(task(svc, 'sched:1'), '3h');
      TestBed.tick();
      expect(ids(svc)).toEqual([]);

      svc.invalidate();
      await answer(1, { queue: months(3).slice(1) });
      expect(ids(svc)).toEqual([]);
      expect(svc.snoozedCount()).toBe(1);

      svc.invalidate();
      await answer(2, { queue: months(4) });
      expect(ids(svc)).toEqual(['sched:1']);
      expect(svc.tasks()[0].title).toBe('測試月費 · 4 期未入帳');
      expect(stored()).toEqual({});
      expect(svc.snoozedCount()).toBe(0);
    });

    it('a task due today that becomes 已逾期 while snoozed comes back (owner ruling)', async () => {
      const svc = service();
      svc.start();
      await answer(0, { queue: [instance(1, 1, '測試房租', TODAY)] });
      svc.snooze(task(svc, 'sched:1'), 'monday');
      TestBed.tick();
      expect(ids(svc)).toEqual([]);

      clockNow = at(25, 10);
      svc.invalidate();
      await answer(1, { queue: [instance(1, 1, '測試房租', TODAY)] });
      expect(svc.tasks().map(t => [t.id, t.tier])).toEqual([['sched:1', 'overdue']]);
      expect(stored()).toEqual({});
    });

    it('the time passing brings the task back without a new round', async () => {
      const svc = service();
      svc.start();
      await answer(0, { queue: [instance(1, 1, '測試房租', TODAY)] });
      svc.snooze(task(svc, 'sched:1'), '3h');
      TestBed.tick();
      expect(ids(svc)).toEqual([]);

      clockNow = at(24, 12);
      vi.advanceTimersByTime(2 * 3_600_000);
      TestBed.tick();
      expect(ids(svc)).toEqual([]);

      clockNow = at(24, 13);
      vi.advanceTimersByTime(3_600_000);
      TestBed.tick();
      // Back by the timer alone, still 今天 (not the 已逾期 rule).
      expect(svc.tasks().map(t => [t.id, t.tier])).toEqual([['sched:1', 'today']]);
      expect(stored()).toEqual({});
    });

    it('a snooze longer than a day survives the daily timer tick (the timer re-arms)', async () => {
      const svc = service();
      svc.start();
      await answer(0, { inbox: inboxWith(2) });
      svc.snooze(task(svc, 'recon:10'), 'monday');
      TestBed.tick();
      clockNow = at(25, 10);
      vi.advanceTimersByTime(24 * 3_600_000);
      TestBed.tick();
      expect(ids(svc)).toEqual([]);
      clockNow = at(26, 9);
      vi.advanceTimersByTime(23 * 3_600_000);
      TestBed.tick();
      expect(ids(svc)).toEqual(['recon:10']);
    });

    it('a snoozed task gone from a source that read ok drops its entry; a failed source keeps it', async () => {
      const svc = service();
      svc.start();
      await answer(0, { inbox: inboxWith(2) });
      svc.snooze(task(svc, 'recon:10'), 'tomorrow');
      TestBed.tick();

      svc.invalidate();
      await answer(1, { inbox: 'error' });
      expect(Object.keys(stored())).toEqual(['recon:10']);

      svc.invalidate();
      await answer(2, { inbox: inboxWith(0) });
      expect(stored()).toEqual({});

      // New questions later are not hidden by the old snooze.
      svc.invalidate();
      await answer(3, { inbox: inboxWith(1) });
      expect(ids(svc)).toEqual(['recon:10']);
    });

    it('unsnooze brings one back; showSnoozed brings every one back', async () => {
      const svc = service();
      svc.start();
      await answer(0, { queue: [instance(1, 1, '測試房租', TODAY), instance(2, 2, '測試月費', TODAY)] });
      svc.snooze(task(svc, 'sched:1'), '3h');
      svc.snooze(task(svc, 'sched:2'), '3h');
      TestBed.tick();
      expect(svc.snoozedCount()).toBe(2);
      svc.unsnooze('sched:2');
      TestBed.tick();
      expect(ids(svc)).toEqual(['sched:2']);
      svc.showSnoozed();
      TestBed.tick();
      expect(ids(svc)).toEqual(['sched:1', 'sched:2']);
      expect(localStorage.getItem(SNOOZE_KEY)).toBeNull();
    });

    it('a snooze stored by an earlier visit hides the task on the first settled round', async () => {
      localStorage.setItem(SNOOZE_KEY, JSON.stringify({ 'sched:1': { until: at(25, 9).toISOString(), tier: 'today', from: 'schedules', wake: { count: 1 } } }));
      const svc = service();
      svc.start();
      await answer(0, { queue: [instance(1, 1, '測試房租', TODAY)] });
      expect(ids(svc)).toEqual([]);
      expect(svc.summary().total).toBe(0);
      expect(svc.snoozedCount()).toBe(1);
    });
  });

  // ---- carried over from nav-badge.service.spec.ts ---------------------------------------------------------------

  it('a failed recalc chain → a 系統 task import:fail:… and the red 匯入 dot 最近一次匯入失敗', async () => {
    const svc = service();
    svc.refresh({ reason: 'focus' });
    await answer(0, { recalc: { kind: 'import', state: 'failed', started_at: '2026-10-24T01:00:00Z' } });
    expect(svc.tasks().map(task => [task.id, task.tier, task.title])).toEqual([
      ['import:fail:2026-10-24T01:00:00Z', 'system', '最近一次匯入的重算失敗'],
    ]);
    expect(svc.summary().tone).toBe('red');
    expect(svc.badges()['import']).toEqual({ dot: true, tone: 'red', label: '最近一次匯入失敗' });
  });

  it('a completed recalc chain shows no import dot', async () => {
    const svc = service();
    svc.refresh({ reason: 'focus' });
    await answer(0, { recalc: { kind: 'import', state: 'completed' } });
    expect('import' in svc.badges()).toBe(false);
  });

  it('the device\'s last failed commit → the same task and dot with a healthy recalc chain', async () => {
    TestBed.inject(ImportHistoryService).record({ at: '2026-10-24T01:00:00Z', file: '測試匯入.csv', type: 'transactions', ok: false, created: 0, errors: 1 });
    const svc = service();
    svc.refresh({ reason: 'focus' });
    await answer(0, { recalc: { kind: 'import', state: 'completed' } });
    expect(svc.tasks().map(task => [task.id, task.tier, task.title, task.meta])).toEqual([
      ['import:fail:2026-10-24T01:00:00Z', 'system', '最近一次匯入失敗', '測試匯入.csv'],
    ]);
    expect(svc.badges()['import']).toEqual({ dot: true, tone: 'red', label: '最近一次匯入失敗' });
  });

  it('a successful commit after the failure clears the dot while the recalc chain is not failed', async () => {
    const history = TestBed.inject(ImportHistoryService);
    history.record({ at: '2026-10-24T01:00:00Z', file: 'a.csv', type: 'transactions', ok: false, created: 0, errors: 1 });
    const svc = service();
    svc.refresh({ reason: 'focus' });
    await answer(0, { recalc: { kind: 'import', state: 'completed' } });
    expect(svc.badges()['import']).toBeDefined();
    history.record({ at: '2026-10-24T02:00:00Z', file: 'a.csv', type: 'transactions', ok: true, created: 3 });
    expect('import' in svc.badges()).toBe(false);
  });

  it('a successful head attempt keeps the dot while the recalc chain failed', async () => {
    TestBed.inject(ImportHistoryService).record({ at: '2026-10-24T02:00:00Z', file: 'a.csv', type: 'transactions', ok: true, created: 3 });
    const svc = service();
    svc.refresh({ reason: 'focus' });
    await answer(0, { recalc: { kind: 'import', state: 'failed' } });
    expect(svc.badges()['import']).toEqual({ dot: true, tone: 'red', label: '最近一次匯入失敗' });
  });

  it('a failed quote refresh shows no import dot', async () => {
    const svc = service();
    svc.refresh({ reason: 'focus' });
    await answer(0, { recalc: { kind: 'quotes', state: 'failed' } });
    expect('import' in svc.badges()).toBe(false);
  });

  it('refresh within 30 s is skipped, after 30 s refires', async () => {
    const svc = service();
    svc.refresh({ reason: 'focus' });
    expect(getRecalcStatus).toHaveBeenCalledTimes(1);
    await answer(0);
    vi.advanceTimersByTime(10_000);
    svc.refresh({ reason: 'focus' });
    expect(getRecalcStatus).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS - 10_000 - 1);
    svc.refresh({ reason: 'focus' });
    expect(getRecalcStatus).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1);
    svc.refresh({ reason: 'focus' });
    expect(getRecalcStatus).toHaveBeenCalledTimes(2);
  });

  it('start() twice registers one focus listener', async () => {
    const addListener = vi.spyOn(window, 'addEventListener');
    const svc = service();
    svc.start();
    svc.start();
    expect(addListener.mock.calls.filter(([type]) => type === 'focus')).toHaveLength(1);
    expect(getRecalcStatus).toHaveBeenCalledTimes(1);
    await answer(0);

    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
    window.dispatchEvent(new Event('focus'));
    expect(getRecalcStatus).toHaveBeenCalledTimes(2);
  });

  it('an entry or account write reloads every task source at once', async () => {
    const svc = service();
    svc.start();
    TestBed.tick();
    await answer(0);
    expect(accountingStub['getCounterparties']).toHaveBeenCalledTimes(1);
    expect(accountingStub['getLatestImport']).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(1_000);
    (accountingStub['entriesChanged'] as WritableSignal<number>).set(1);
    TestBed.tick();
    expect(accountingStub['getCounterparties']).toHaveBeenCalledTimes(2);
    expect(accountingStub['getScheduleInstances']).toHaveBeenCalledTimes(2);
    expect(accountingStub['getLatestImport']).toHaveBeenCalledTimes(2);
    // P2: the portfolio and system reads are round reads too, so a write re-reads them with the rest.
    for (const read of [getRecalcStatus, getDividends, getCathayPending, getCathayRuns]) {
      expect(read).toHaveBeenCalledTimes(2);
    }

    land(latest.subjects[1], importRun(3));
    TestBed.tick();
    expect(svc.badges()['accounting-timeline']).toEqual({ dot: true, tone: 'primary', label: '有待確認的匯入紀錄' });

    (accountingStub['accountsChanged'] as WritableSignal<number>).set(1);
    TestBed.tick();
    expect(accountingStub['getCounterparties']).toHaveBeenCalledTimes(3);
    expect(accountingStub['getLatestImport']).toHaveBeenCalledTimes(3);
    for (const read of [getRecalcStatus, getDividends, getCathayPending, getCathayRuns]) {
      expect(read).toHaveBeenCalledTimes(3);
    }
  });

  it('writes before start() load nothing', () => {
    service();
    TestBed.tick();
    (accountingStub['entriesChanged'] as WritableSignal<number>).set(1);
    TestBed.tick();
    expect(accountingStub['getLatestImport']).not.toHaveBeenCalled();
  });

  describe('returning to a visible page (installed PWA from the background)', () => {
    let visibility: DocumentVisibilityState;

    beforeEach(() => {
      visibility = 'visible';
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility });
    });

    afterEach(() => {
      delete (document as unknown as Record<string, unknown>)['visibilityState'];
    });

    function becomes(state: DocumentVisibilityState): void {
      visibility = state;
      document.dispatchEvent(new Event('visibilitychange'));
    }

    it('refreshes on visibilitychange to visible, throttled like focus', async () => {
      service().start();
      expect(getRecalcStatus).toHaveBeenCalledTimes(1);
      await answer(0);

      vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
      becomes('hidden');
      expect(getRecalcStatus).toHaveBeenCalledTimes(1);
      becomes('visible');
      expect(getRecalcStatus).toHaveBeenCalledTimes(2);
      await answer(1);

      vi.advanceTimersByTime(5_000);
      becomes('hidden');
      becomes('visible');
      expect(getRecalcStatus).toHaveBeenCalledTimes(2);
    });

    it('removes the visibilitychange listener when the injector is destroyed', () => {
      service().start();
      TestBed.resetTestingModule();
      vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
      becomes('visible');
      expect(getRecalcStatus).toHaveBeenCalledTimes(1);
    });
  });

  it('removes the focus listener when the injector is destroyed', () => {
    service().start();
    TestBed.resetTestingModule();
    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
    window.dispatchEvent(new Event('focus'));
    expect(getRecalcStatus).toHaveBeenCalledTimes(1);
  });

  it('a superseded round\'s recalc answer is dropped', async () => {
    const svc = service();
    svc.refresh({ reason: 'focus' });
    // Round 1's recalc read is still open when round 2 supersedes it.
    await answer(0, { recalc: 'skip' });
    expect(svc.settledRound()).toBe(0);
    svc.invalidate();
    // An older failure landing after the newer success changes nothing.
    await answer(1, { recalc: { kind: 'import', state: 'completed' } });
    land(recalc.subjects[0], { kind: 'import', state: 'failed' });
    TestBed.tick();
    expect('import' in svc.badges()).toBe(false);
    expect(svc.tasks()).toEqual([]);
    expect(svc.settledRound()).toBe(2);
    expect(svc.failedSources()).toEqual([]);

    // And an older success landing after a newer failure.
    svc.invalidate();
    await answer(2, { recalc: 'skip' });
    const before = svc.settledRound();
    svc.invalidate();
    await answer(3, { recalc: 'error' });
    land(recalc.subjects[2], { kind: 'import', state: 'completed' });
    TestBed.tick();
    expect(before).toBe(2);
    expect(svc.settledRound()).toBe(4);
    expect(svc.failedSources()).toEqual(['imports']);
    expect('import' in svc.badges()).toBe(false);
  });

  it('slow first load across the 30 s window: the unanswered round fails at 20 s; a focus at 31 s starts a fresh one', async () => {
    const svc = service();
    svc.start();
    vi.advanceTimersByTime(READ_TIMEOUT_MS);
    TestBed.tick();
    expect(svc.settledRound()).toBe(1);
    expect(svc.failedSources()).toEqual(['bills', 'schedules', 'recon', 'importReview', 'debts', 'dividends', 'imports', 'cathay', 'sinopac_futures']);
    // Timed out reads are unsubscribed: round 1 can no longer answer.
    expect(latest.subjects[0].observed).toBe(false);

    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000 - READ_TIMEOUT_MS);
    window.dispatchEvent(new Event('focus'));
    expect(svc.round()).toBe(2);
    expect(getRecalcStatus).toHaveBeenCalledTimes(2);

    await answer(1, { latest: importRun(5), recalc: { kind: 'import', state: 'failed' } });
    expect(svc.badges()).toEqual({
      'accounting-timeline': { dot: true, tone: 'primary', label: '有待確認的匯入紀錄' },
      import: { dot: true, tone: 'red', label: '最近一次匯入失敗' },
    });
    expect(svc.settledRound()).toBe(2);
    expect(svc.failedSources()).toEqual([]);
  });

  it('stale accounts snapshot never reaches the statements (ensure or refresh)', async () => {
    const bills = TestBed.inject(BillingService);
    const ensure = vi.spyOn(bills, 'ensure');
    const reread = vi.spyOn(bills, 'refresh');
    const svc = service();
    svc.refresh({ reason: 'focus' });
    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
    svc.invalidate();

    await answer(1, { accounts: [lateCard(9)] });
    await answer(0, { accounts: [lateCard(7)] });

    expect(reread).toHaveBeenCalled();
    const accountIds = [...ensure.mock.calls, ...reread.mock.calls].flatMap(([dues]) => dues.map(event => event.accountId));
    expect(accountIds).toContain(9);
    expect(accountIds).not.toContain(7);
  });

  it('re-asks the statements with the debit day once the business-day calendar lands (#197)', async () => {
    // Card 9 falls due Thu 2026-11-05; the calendar read is still open when the accounts land.
    const calendar = new Subject<BusinessDays>();
    accountingStub['getBusinessDays'] = vi.fn(() => calendar);
    const bills = TestBed.inject(BillingService);
    const asked = vi.spyOn(bills, 'ensure');
    const reread = vi.spyOn(bills, 'refresh');
    // The latest ensure/refresh call, whichever came last.
    const lastDues = () =>
      [asked, reread]
        .flatMap(spy => spy.mock.calls.map((call, index) => ({ order: spy.mock.invocationCallOrder[index], dues: call[0] })))
        .sort((a, b) => a.order - b.order)
        .at(-1)!.dues;
    const svc = service();
    svc.refresh({ reason: 'focus' });
    await answer(0, { accounts: [lateCard(9)] });
    expect(lastDues()[0]).toMatchObject({ accountId: 9, date: '2026-11-05' });
    expect(lastDues()[0].payDate).toBeUndefined();

    // The calendar lists the due day as a holiday: the same statement is asked for again with the Friday debit.
    calendar.next({ holidays: ['2026-11-05'], workdays: [], last_known_day: '2027-12-31' });
    TestBed.tick();
    expect(lastDues()[0]).toMatchObject({ accountId: 9, date: '2026-11-05', payDate: '2026-11-06' });
    expect(bills.bill(lastDues()[0])).toMatchObject({ payDate: '2026-11-06' });
  });

  describe('card statements on a refresh (payments made elsewhere, no local write)', () => {
    function paidInFull(): void {
      (accountingStub['getEntries'] as ReturnType<typeof vi.fn>).mockImplementation(() =>
        of({ items: [makeEntry({ id: 70, kind: 'transfer_in', amount: '1000' })], total: 1, limit: 100, offset: 0 }),
      );
    }

    it('a focus refresh after 30 s re-reads the cached statement and updates the bill task', async () => {
      const svc = service();
      svc.start();
      await answer(0, { accounts: [card(11, 20)] });
      expect(ids(svc)).toEqual(['bill:11:2026-10-20']);
      const reads = (accountingStub['getEntries'] as ReturnType<typeof vi.fn>).mock.calls.length;

      paidInFull();
      vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
      window.dispatchEvent(new Event('focus'));
      await answer(1, { accounts: [card(11, 20)] });

      expect((accountingStub['getEntries'] as ReturnType<typeof vi.fn>).mock.calls.length).toBe(reads + 1);
      expect(ids(svc)).toEqual([]);
    });

    it('a focus within 30 s does not re-read the statement', async () => {
      const svc = service();
      svc.start();
      await answer(0, { accounts: [card(11, 20)] });
      const reads = (accountingStub['getEntries'] as ReturnType<typeof vi.fn>).mock.calls.length;

      paidInFull();
      vi.advanceTimersByTime(10_000);
      window.dispatchEvent(new Event('focus'));
      TestBed.tick();

      expect(accounts.subjects).toHaveLength(1);
      expect((accountingStub['getEntries'] as ReturnType<typeof vi.fn>).mock.calls.length).toBe(reads);
      expect(ids(svc)).toEqual(['bill:11:2026-10-20']);
    });

    it('re-reads once per accepted refresh, not on every later run of the statements effect', async () => {
      const svc = service();
      const refresh = vi.spyOn(TestBed.inject(BillingService), 'refresh');
      svc.start();
      await answer(0, { accounts: [card(11, 20)] });
      expect(refresh).toHaveBeenCalledTimes(1);

      // A write starts a round in a new change round: the statements effect re-reads through ensure, no forced re-read.
      (accountingStub['entriesChanged'] as WritableSignal<number>).set(1);
      TestBed.tick();
      await answer(1, { accounts: [card(11, 20)] });
      expect(refresh).toHaveBeenCalledTimes(1);
      expect(svc.settledRound()).toBe(2);

      vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
      window.dispatchEvent(new Event('focus'));
      await answer(2, { accounts: [card(11, 20)] });
      expect(refresh).toHaveBeenCalledTimes(2);
    });
  });

  // ---- tasks and count -------------------------------------------------------------------------------------------

  it('three overdue instances of one definition count once', async () => {
    const svc = service();
    svc.start();
    await answer(0, {
      queue: [instance(1, 1, '測試月費', '2026-10-20'), instance(2, 1, '測試月費', '2026-10-21'), instance(3, 1, '測試月費', '2026-10-22')],
    });
    expect(svc.summary().total).toBe(1);
    expect(svc.tasks()[0].title).toBe('測試月費 · 3 期未入帳');
  });

  it('count on review data: 7, red, 「通知，7 件要處理，3 件已逾期」; three people owing do not count', async () => {
    const svc = service();
    svc.start();
    await answer(0, {
      accounts: [card(11, 20, '測試銀行卡')],
      queue: [
        instance(1, 1, '測試月費', '2026-10-22'),
        instance(2, 2, '測試分期', '2026-10-23'),
        instance(3, 3, '測試房租', TODAY),
      ],
      inbox: inboxWith(1, { einvoice_ask: 2 }),
      latest: importRun(2),
      counterparties: [person(1, '甲'), person(2, '乙'), person(3, '丙')],
    });
    expect(svc.summary()).toEqual({ total: 7, overdue: 3, tone: 'red', label: '通知，7 件要處理，3 件已逾期' });
    expect(svc.trackingRows().map(row => row.id)).toEqual(['debts']);
  });

  it('reads every source once per round', async () => {
    const svc = service();
    svc.start();
    TestBed.tick();
    for (const name of ['getAccounts', 'getCounterparties', 'getScheduleInstances', 'getLatestImport', 'getBusinessDays']) {
      expect(accountingStub[name], name).toHaveBeenCalledTimes(1);
    }
    expect(accountingStub['getScheduleInstances']).toHaveBeenCalledWith({ queue: true });
    expect(getInbox).toHaveBeenCalledTimes(1);
    expect(getInbox).toHaveBeenCalledWith({ limit: INBOX_READ_LIMIT });
    expect(INBOX_READ_LIMIT).toBe(1000);
    expect(getActions).toHaveBeenCalledTimes(1);
    expect(getActions).toHaveBeenCalledWith(50);
    expect(getRecalcStatus).toHaveBeenCalledTimes(1);
    expect(getDividends).toHaveBeenCalledTimes(1);
    expect(getDividends).toHaveBeenCalledWith({ limit: 100, offset: 0 });
    expect(getCathayPending).toHaveBeenCalledTimes(1);
    expect(getCathayRuns).toHaveBeenCalledTimes(1);
    expect(svc.round()).toBe(1);
  });

  it('a failed inbox read: no statement task, no e-invoice task, failedSources [recon], the other tasks stay', async () => {
    const svc = service();
    svc.start();
    await answer(0, { inbox: inboxWith(2, { einvoice_ask: 1 }), queue: [instance(1, 1, '測試房租', TODAY)] });
    expect(ids(svc)).toEqual(['sched:1', 'recon:10', 'einvoice']);

    svc.invalidate();
    await answer(1, { inbox: 'error', queue: [instance(1, 1, '測試房租', TODAY)] });
    expect(ids(svc)).toEqual(['sched:1']);
    expect(svc.failedSources()).toEqual(['recon']);
    expect(svc.settledRound()).toBe(2);
  });

  it('a failed queue read names 週期 and keeps the bills', async () => {
    const svc = service();
    svc.start();
    await answer(0, { accounts: [card(11, 20)], queue: 'error' });
    expect(svc.failedSources()).toEqual(['schedules']);
    expect(ids(svc)).toEqual(['bill:11:2026-10-20']);
  });

  it('a failed accounts read names 帳單 and hides the 帳戶 dot', async () => {
    const svc = service();
    svc.start();
    await answer(0, { accounts: [card(11, 20)] });
    expect(svc.badges()['accounting']).toEqual({ dot: true, tone: 'red', label: '有逾期的卡費' });

    svc.invalidate();
    await answer(1, { accounts: 'error' });
    expect(svc.failedSources()).toEqual(['bills']);
    expect('accounting' in svc.badges()).toBe(false);
    expect(ids(svc)).toEqual([]);
  });

  it('one card whose statement read failed: that card has no task, the other card keeps its task, 帳單 is named', async () => {
    (accountingStub['getAccountSummary'] as ReturnType<typeof vi.fn>).mockImplementation((accountId: number) =>
      accountId === 12
        ? throwError(() => new Error('boom'))
        : of({
            account_id: accountId, currency: 'TWD', date_from: '', date_to: '', spend: '-1000', income: '0',
            rewards: '0', net: '-1000', end_balance: '-1000', count: 1,
          }),
    );
    const svc = service();
    svc.start();
    await answer(0, { accounts: [card(11, 20), card(12, 21)] });
    expect(ids(svc)).toEqual(['bill:11:2026-10-20']);
    expect(svc.failedSources()).toEqual(['bills']);
    expect(svc.settledRound()).toBe(1);
  });

  it('a failed counterparties read names 借還款 and changes no count', async () => {
    const svc = service();
    svc.start();
    await answer(0, { queue: [instance(1, 1, '測試房租', TODAY)], counterparties: [person(1, '甲')] });
    const before = svc.summary();
    expect(before.total).toBe(1);

    svc.invalidate();
    await answer(1, { queue: [instance(1, 1, '測試房租', TODAY)], counterparties: 'error' });
    expect(svc.failedSources()).toEqual(['debts']);
    expect(svc.summary()).toEqual(before);
    expect(svc.trackingRows()).toEqual([]);
  });

  it('a failed latest-import read names 匯入檢查 and hides the 記帳記錄 dot; a 404 (null) names nothing', async () => {
    const svc = service();
    svc.start();
    await answer(0, { latest: importRun(2) });
    expect(svc.badges()['accounting-timeline']).toBeDefined();

    svc.invalidate();
    await answer(1, { latest: 'error' });
    expect(svc.failedSources()).toEqual(['importReview']);
    expect('accounting-timeline' in svc.badges()).toBe(false);
    expect(ids(svc)).toEqual([]);

    svc.invalidate();
    await answer(2, { latest: null });
    expect(svc.failedSources()).toEqual([]);
  });

  it('a source that fails after it succeeded drops its tasks', async () => {
    const svc = service();
    svc.start();
    await answer(0, { queue: [instance(1, 1, '測試房租', TODAY)], latest: importRun(1) });
    expect(svc.summary().total).toBe(2);

    svc.invalidate();
    await answer(1, { queue: 'error', latest: importRun(1) });
    expect(ids(svc)).toEqual(['import:1']);
    expect(svc.summary().total).toBe(1);
    expect(svc.failedSources()).toEqual(['schedules']);
  });

  it('an older inbox answer landing after a newer one does not bring its statement back', async () => {
    const svc = service();
    svc.start();
    svc.invalidate();
    await answer(1);
    await answer(0, { inbox: inboxWith(2) });
    expect(ids(svc)).toEqual([]);
    expect(listStatements).not.toHaveBeenCalled();
  });

  it('statement progress: one statements read per account with ask items; a failed one leaves the task without progress', async () => {
    const svc = service();
    svc.start();
    await answer(0, {
      inbox: makeInbox(
        [
          makeInboxAccount({ id: 1, name: '測試帳戶', items: [askItem(1, 10), askItem(2, 10)] }),
          makeInboxAccount({ id: 2, name: '測試銀行卡', items: [askItem(3, 20)] }),
          makeInboxAccount({ id: 3, name: '測試錢包', items: [askItem(4, 30, { folded: true, question: 'proposed' })] }),
        ],
        { ask: 2, ask_items: 3 },
      ),
    });
    expect(listStatements.mock.calls.map(([id]) => id)).toEqual([1, 2]);

    land(statements.subjects[0], [statement(10, 3, 2)]);
    fail(statements.subjects[1]);
    TestBed.tick();
    const byId = new Map(svc.tasks().map(task => [task.id, task]));
    expect(byId.get('recon:10')?.progress).toEqual({ k: 3, n: 5 });
    expect(byId.get('recon:20')?.progress).toBeNull();
  });

  it('a statements answer for a superseded inbox read is dropped', async () => {
    const svc = service();
    svc.start();
    await answer(0, { inbox: inboxWith(1) });
    expect(listStatements).toHaveBeenCalledTimes(1);

    svc.invalidate();
    land(statements.subjects[0], [statement(10, 4, 1)]);
    TestBed.tick();
    expect(svc.tasks().find(task => task.id === 'recon:10')?.progress).toBeNull();
  });

  // ---- rounds ----------------------------------------------------------------------------------------------------

  it('refresh open inside the 30 s window starts a round; focus and timer do not', async () => {
    const svc = service();
    svc.refresh({ reason: 'open' });
    await answer(0);
    vi.advanceTimersByTime(5_000);
    svc.refresh({ reason: 'focus' });
    svc.refresh({ reason: 'timer' });
    expect(svc.round()).toBe(1);
    svc.refresh({ reason: 'open' });
    expect(svc.round()).toBe(2);
    expect(accountingStub['getAccounts']).toHaveBeenCalledTimes(2);
  });

  it('refresh (any reason) while a round is in flight starts nothing', async () => {
    // In flight past the 30 s throttle: the accounts answer at 19 s, the card statement then has its own 20 s.
    const entries = calls<{ items: never[]; total: number; limit: number; offset: number }>();
    accountingStub['getEntries'] = vi.fn(entries.fn);
    const svc = service();
    svc.start();
    vi.advanceTimersByTime(19_000);
    await answer(0, { accounts: [card(11, 20)] });
    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000 - 19_000);
    expect(svc.loading()).toBe(true);
    svc.refresh({ reason: 'open' });
    svc.refresh({ reason: 'focus' });
    svc.refresh({ reason: 'timer' });
    expect(svc.round()).toBe(1);
    for (const name of ['getAccounts', 'getCounterparties', 'getScheduleInstances', 'getLatestImport']) {
      expect(accountingStub[name], name).toHaveBeenCalledTimes(1);
    }
    expect(getInbox).toHaveBeenCalledTimes(1);
    expect(getActions).toHaveBeenCalledTimes(1);
    expect(getRecalcStatus).toHaveBeenCalledTimes(1);
    expect(getDividends).toHaveBeenCalledTimes(1);
    expect(getCathayPending).toHaveBeenCalledTimes(1);
    expect(getCathayRuns).toHaveBeenCalledTimes(1);
  });

  it('invalidate() while a round is in flight starts a new round and returns its id', () => {
    const svc = service();
    svc.start();
    const id = svc.invalidate();
    expect(id).toBe(2);
    expect(svc.round()).toBe(2);
    for (const name of ['getAccounts', 'getCounterparties', 'getScheduleInstances', 'getLatestImport']) {
      expect(accountingStub[name], name).toHaveBeenCalledTimes(2);
    }
    expect(getInbox).toHaveBeenCalledTimes(2);
  });

  it('a superseded round never settles: its answers change nothing and settledRound stays', async () => {
    const svc = service();
    svc.start();
    svc.invalidate();
    await answer(0, { queue: [instance(1, 1, '測試房租', TODAY)], latest: importRun(1), inbox: inboxWith(1) });
    expect(svc.tasks()).toEqual([]);
    expect(svc.settledRound()).toBe(0);
    expect(svc.loading()).toBe(true);

    await answer(1);
    expect(svc.settledRound()).toBe(2);
    expect(svc.loading()).toBe(false);
  });

  it('settledRound waits for every task source, a failed one included', async () => {
    const svc = service();
    svc.start();
    await answer(0, { counterparties: 'skip' });
    expect(svc.settledRound()).toBe(0);
    expect(svc.loaded()).toBe(false);

    fail(counterparties.subjects[0]);
    TestBed.tick();
    expect(svc.settledRound()).toBe(1);
    expect(svc.loaded()).toBe(true);
  });

  it('settledRound waits for the card statements, not just the accounts read', async () => {
    const entries = calls<{ items: never[]; total: number; limit: number; offset: number }>();
    accountingStub['getEntries'] = vi.fn(entries.fn);
    const svc = service();
    svc.start();
    await answer(0, { accounts: [card(11, 20)] });
    expect(svc.settledRound()).toBe(0);
    expect(svc.loading()).toBe(true);
    // The bill itself is still unresolved (not just hidden by the summary's loaded gate).
    expect(ids(svc)).toEqual([]);

    land(entries.subjects[0], { items: [], total: 0, limit: 100, offset: 0 });
    await microtasks();
    expect(svc.settledRound()).toBe(1);
    expect(ids(svc)).toEqual(['bill:11:2026-10-20']);
    expect(svc.summary().total).toBe(1);
  });

  it('an entry write after start() invalidates (a new round id); before start() it does nothing', async () => {
    const svc = service();
    TestBed.tick();
    (accountingStub['entriesChanged'] as WritableSignal<number>).set(1);
    TestBed.tick();
    expect(svc.round()).toBe(0);

    svc.start();
    expect(svc.round()).toBe(1);
    await answer(0);
    (accountingStub['entriesChanged'] as WritableSignal<number>).set(2);
    TestBed.tick();
    expect(svc.round()).toBe(2);
  });

  it('invalidate() reads the recalc status with every other source', () => {
    const svc = service();
    svc.invalidate();
    expect(svc.round()).toBe(1);
    for (const read of [accountingStub['getAccounts'], getRecalcStatus, getDividends, getCathayPending, getCathayRuns]) {
      expect(read).toHaveBeenCalledTimes(1);
    }
    svc.invalidate();
    for (const read of [accountingStub['getAccounts'], getRecalcStatus, getDividends, getCathayPending, getCathayRuns]) {
      expect(read).toHaveBeenCalledTimes(2);
    }
  });

  // ---- R25: a truncated inbox ------------------------------------------------------------------------------------

  it('truncated inbox: counts.ask_items 10 with 6 unfolded received → one statement task counted; the catch-all is `overflow()`, not a task, so total is 1; truncated() true', async () => {
    const svc = service();
    svc.start();
    await answer(0, { inbox: inboxWith(6, { ask_items: 10 }) });
    expect(ids(svc)).toEqual(['recon:10']);
    expect(svc.tasks()[0].when).toBe('6 筆要你決定');
    expect(svc.summary().total).toBe(1);
    expect(svc.overflow()).toMatchObject({ id: 'recon:more', title: '還有 4 筆對帳項目未列出' });
    expect(svc.truncated()).toBe(true);
  });

  it('truncated inbox with no statement received: no counted task, summary().total 0, overflow() still names the rest', async () => {
    const svc = service();
    svc.start();
    await answer(0, { inbox: inboxWith(0, { ask: 3, ask_items: 3 }) });
    expect(ids(svc)).toEqual([]);
    expect(svc.summary().total).toBe(0);
    expect(svc.overflow()).toMatchObject({ id: 'recon:more' });
    expect(svc.truncated()).toBe(true);
  });

  it('a complete inbox: no recon:more, truncated() false', async () => {
    const svc = service();
    svc.start();
    await answer(0, { inbox: inboxWith(3) });
    expect(ids(svc)).toEqual(['recon:10']);
    expect(svc.truncated()).toBe(false);
    expect(svc.overflow()).toBeNull();
  });

  it('a failed inbox read clears truncated', async () => {
    const svc = service();
    svc.start();
    await answer(0, { inbox: inboxWith(6, { ask_items: 10 }) });
    expect(svc.truncated()).toBe(true);

    svc.invalidate();
    await answer(1, { inbox: 'error' });
    expect(svc.truncated()).toBe(false);
    expect(svc.overflow()).toBeNull();
    expect(ids(svc)).toEqual([]);
  });

  // ---- dots, handled, tracking -----------------------------------------------------------------------------------

  it('badges: overdue bill → accounting red dot 有逾期的卡費; a bill due today → no dot', async () => {
    const svc = service();
    svc.start();
    await answer(0, { accounts: [card(11, 20)] });
    expect(svc.badges()['accounting']).toEqual({ dot: true, tone: 'red', label: '有逾期的卡費' });

    // Saturday 10-24 is a make-up workday: a bill due today is debited today.
    TestBed.resetTestingModule();
    accountingStub['getBusinessDays'] = vi.fn(() => of({ holidays: [], workdays: [TODAY], last_known_day: '2027-12-31' }));
    configure();
    const fresh = service();
    fresh.start();
    await answer(1, { accounts: [card(12, 24)] });
    expect(fresh.tasks().map(task => [task.id, task.tier])).toEqual([['bill:12:2026-10-24', 'today']]);
    expect('accounting' in fresh.badges()).toBe(false);
  });

  it('badges: review count 2 → accounting-timeline primary dot 有待確認的匯入紀錄; no badge carries a count', async () => {
    TestBed.inject(ImportHistoryService).record({ at: '2026-10-24T01:00:00Z', file: 'a.csv', type: 'transactions', ok: false, created: 0, errors: 1 });
    const svc = service();
    svc.start();
    await answer(0, { latest: importRun(2), accounts: [card(11, 20)] });
    expect(svc.badges()['accounting-timeline']).toEqual({ dot: true, tone: 'primary', label: '有待確認的匯入紀錄' });
    expect(Object.keys(svc.badges()).sort()).toEqual(['accounting', 'accounting-timeline', 'import']);
    for (const badge of Object.values(svc.badges())) {
      expect('count' in badge).toBe(false);
    }
  });

  it('handled: 「3 件」 from the actions read; null when it fails', async () => {
    const svc = service();
    svc.start();
    await answer(0, {
      actions: [
        action(1, '2026-10-23T08:00:00Z'),
        action(2, '2026-10-22T08:00:00Z'),
        action(3, '2026-10-20T08:00:00Z'),
        action(4, '2026-10-21T08:00:00Z', 9),
        action(5, '2026-10-01T08:00:00Z'),
      ],
    });
    expect(svc.handled()).toBe('3 件');

    svc.invalidate();
    await answer(1, { actions: 'error' });
    expect(svc.handled()).toBeNull();
  });

  it('nextUpcoming is the tracking bill with the earliest date; trackingRows = [debts, upcoming]', async () => {
    const svc = service();
    svc.start();
    await answer(0, { accounts: [card(13, 28), card(14, 27)], counterparties: [person(1, '甲')] });
    expect(svc.tasks()).toEqual([]);
    expect(svc.nextUpcoming()?.id).toBe('bill:14:2026-10-27');
    expect(svc.trackingRows().map(row => row.id)).toEqual(['debts', 'upcoming']);
    expect(svc.trackingRows()[1].count).toBe(2);
    expect(svc.summary().total).toBe(0);
  });

  it('hidden groups change nothing', async () => {
    const svc = service();
    svc.start();
    await answer(0, { accounts: [card(11, 20)], queue: [instance(1, 1, '測試房租', TODAY)], latest: importRun(1) });
    const before = svc.summary();
    expect(before.total).toBe(3);

    TestBed.inject(NavigationPreferencesService).setHidden('accounting', true);
    TestBed.tick();
    expect(svc.summary()).toEqual(before);
    expect(svc.tasks()).toHaveLength(3);
  });

  // ---- follow-up: more interleavings -----------------------------------------------------------------------------

  it('before any round: round 0, not loading, not loaded, NO_TASKS, nothing failed', () => {
    const svc = service();
    expect(svc.round()).toBe(0);
    expect(svc.settledRound()).toBe(0);
    expect(svc.loading()).toBe(false);
    expect(svc.loaded()).toBe(false);
    expect(svc.summary()).toEqual(NO_TASKS);
    expect(svc.tasks()).toEqual([]);
    expect(svc.trackingRows()).toEqual([]);
    expect(svc.failedSources()).toEqual([]);
    expect(svc.truncated()).toBe(false);
    expect(svc.handled()).toBeNull();
  });

  it("a superseded round's bills promise resolving late (after its accounts answer) changes nothing", async () => {
    const entries = calls<{ items: never[]; total: number; limit: number; offset: number }>();
    accountingStub['getEntries'] = vi.fn(entries.fn);
    const svc = service();
    svc.start();
    // Round 1: accounts answered, the card's payments read still open → its bills promise is pending.
    await answer(0, { accounts: [card(11, 20)] });
    expect(svc.settledRound()).toBe(0);

    const id = svc.invalidate();
    await answer(1, { accounts: 'error' });
    expect(svc.settledRound()).toBe(id);
    expect(svc.failedSources()).toEqual(['bills']);

    // Round 1's statement lands and its promise resolves: round 1 stays unsettled, round 2's failure stands.
    land(entries.subjects[0], { items: [], total: 0, limit: 100, offset: 0 });
    await microtasks();
    TestBed.tick();
    expect(svc.settledRound()).toBe(id);
    expect(svc.failedSources()).toEqual(['bills']);
    expect(ids(svc)).toEqual([]);
  });

  it('invalidate() twice in a row: only the newest round applies, each source read once per round', async () => {
    const svc = service();
    svc.start();
    expect(svc.invalidate()).toBe(2);
    expect(svc.invalidate()).toBe(3);
    for (const name of ['getAccounts', 'getCounterparties', 'getScheduleInstances', 'getLatestImport']) {
      expect(accountingStub[name], name).toHaveBeenCalledTimes(3);
    }
    expect(getInbox).toHaveBeenCalledTimes(3);
    expect(getActions).toHaveBeenCalledTimes(3);
    expect(getRecalcStatus).toHaveBeenCalledTimes(3);
    expect(accountingStub['getBusinessDays']).toHaveBeenCalledTimes(1);

    await answer(2, { queue: [instance(3, 3, '測試房租', TODAY)] });
    expect(svc.settledRound()).toBe(3);
    await answer(1, { queue: [instance(2, 2, '測試分期', TODAY)] });
    await answer(0, { queue: [instance(1, 1, '測試月費', TODAY)] });
    expect(ids(svc)).toEqual(['sched:3']);
    expect(svc.settledRound()).toBe(3);
  });

  it('open while a round is in flight starts nothing; invalidate() then supersedes it', async () => {
    const svc = service();
    svc.start();
    svc.refresh({ reason: 'open' });
    expect(svc.round()).toBe(1);
    expect(svc.invalidate()).toBe(2);
    expect(accountingStub['getAccounts']).toHaveBeenCalledTimes(2);
    expect(getRecalcStatus).toHaveBeenCalledTimes(2);

    await answer(0, { latest: importRun(4) });
    expect(svc.settledRound()).toBe(0);
    await answer(1);
    expect(svc.settledRound()).toBe(2);
    expect(ids(svc)).toEqual([]);
  });

  it('a focus within 30 s after an invalidate() is skipped', async () => {
    const svc = service();
    svc.start();
    await answer(0);
    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
    svc.invalidate();
    await answer(1);
    vi.advanceTimersByTime(5_000);
    window.dispatchEvent(new Event('focus'));
    expect(svc.round()).toBe(2);
    expect(accountingStub['getAccounts']).toHaveBeenCalledTimes(2);
    expect(getRecalcStatus).toHaveBeenCalledTimes(2);
  });

  it('a source that errors synchronously fails only itself and the round still settles', async () => {
    accountingStub['getCounterparties'] = vi.fn(() => throwError(() => new Error('boom')));
    const svc = service();
    svc.start();
    expect(svc.failedSources()).toEqual(['debts']);
    expect(svc.settledRound()).toBe(0);
    await answer(0, { queue: [instance(1, 1, '測試房租', TODAY)] });
    expect(svc.settledRound()).toBe(1);
    expect(svc.failedSources()).toEqual(['debts']);
    expect(ids(svc)).toEqual(['sched:1']);
  });

  it('an inbox write after start() starts a round; before start() it does nothing', async () => {
    const svc = service();
    TestBed.tick();
    inboxChanged.set(1);
    TestBed.tick();
    expect(svc.round()).toBe(0);

    svc.start();
    await answer(0);
    inboxChanged.set(2);
    TestBed.tick();
    expect(svc.round()).toBe(2);
    expect(getInbox).toHaveBeenCalledTimes(2);
  });

  // ---- follow-up: a hung read fails after READ_TIMEOUT_MS ----------------------------------------------------------

  it('a read that never answers fails its source after 20 s; the round settles and the next refresh starts a new one', async () => {
    expect(READ_TIMEOUT_MS).toBe(20_000);
    const svc = service();
    svc.start();
    await answer(0, { queue: 'skip', latest: importRun(1) });
    vi.advanceTimersByTime(READ_TIMEOUT_MS - 1);
    TestBed.tick();
    expect(svc.settledRound()).toBe(0);

    vi.advanceTimersByTime(1);
    TestBed.tick();
    expect(svc.settledRound()).toBe(1);
    expect(svc.failedSources()).toEqual(['schedules']);
    expect(ids(svc)).toEqual(['import:1']);

    svc.refresh({ reason: 'open' });
    expect(svc.round()).toBe(2);
  });

  it('a hung statement-progress read times out silently: the task has no bar', async () => {
    const svc = service();
    svc.start();
    await answer(0, { inbox: inboxWith(1) });
    expect(svc.settledRound()).toBe(1);
    expect(statements.subjects[0].observed).toBe(true);
    vi.advanceTimersByTime(READ_TIMEOUT_MS);
    expect(statements.subjects[0].observed).toBe(false);
    expect(svc.failedSources()).toEqual([]);
    expect(svc.tasks()[0].progress).toBeNull();
  });
  // ---- PR C: dividends, imports, 國泰對帳單 -----------------------------------------------------------------------

  describe('a round waits for every one of the nine task sources', () => {
    it.each(['dividends', 'cathayPending', 'cathayRuns', 'futuresPending', 'futuresRuns', 'recalc'] as const)('%s open → not settled; answered → settled', async key => {
      const svc = service();
      svc.start();
      await answer(0, { [key]: 'skip' });
      expect(svc.settledRound()).toBe(0);
      expect(svc.loaded()).toBe(false);
      await answer(0);
      expect(svc.settledRound()).toBe(1);
      expect(svc.failedSources()).toEqual([]);
    });
  });

  it('two dividends past their payment date: one counted task, the 股利 dot labelled 2 筆待確認收款, tone primary', async () => {
    const svc = service();
    svc.start();
    await answer(0, {
      dividends: { items: [dividend(1, '9901', '2026-10-20'), dividend(2, '9902', TODAY), dividend(3, '9903', '2026-10-01', 'confirmed')], total: 3 },
    });
    expect(ids(svc)).toEqual(['div:batch']);
    expect(svc.tasks()[0].title).toBe('股利待確認收款 · 2 筆');
    expect(svc.summary().total).toBe(1);
    expect(svc.summary().tone).toBe('primary');
    expect(svc.badges()['dividends']).toEqual({ dot: true, tone: 'primary', label: '2 筆待確認收款' });
    expect('import' in svc.badges()).toBe(false);
  });

  it('pending dividends with a later or no date are a 追蹤中 row after the ledger rows and are not counted', async () => {
    const svc = service();
    svc.start();
    await answer(0, {
      counterparties: [person(1, '甲')],
      dividends: { items: [dividend(4, '9901', '2026-10-30'), dividend(5, '9902', null)], total: 2 },
    });
    expect(ids(svc)).toEqual([]);
    expect(svc.summary().total).toBe(0);
    expect(svc.trackingRows().map(row => [row.id, row.source])).toEqual([['debts', 'ledger'], ['dividends', 'portfolio']]);
    expect(svc.trackingRows()[1].title).toBe('股利待發放 · 2 筆');
    expect('dividends' in svc.badges()).toBe(false);
  });

  it('101 dividends are read in two requests (offset 0 and 100) and both pages count', async () => {
    const svc = service();
    svc.start();
    await answer(0, { dividends: { items: dueDividends(100), total: 101 } });
    expect(getDividends.mock.calls).toEqual([[{ limit: 100, offset: 0 }], [{ limit: 100, offset: 100 }]]);
    expect(svc.settledRound()).toBe(0);
    land(dividends.subjects[1], { items: dueDividends(1, 101), total: 101 });
    TestBed.tick();
    expect(svc.settledRound()).toBe(1);
    expect(svc.tasks().map(task => task.title)).toEqual(['股利待確認收款 · 101 筆']);
    expect(svc.badges()['dividends']?.label).toBe('101 筆待確認收款');
  });

  it('cap boundary: DIVIDEND_MAX_PAGES full pages with total 5001 → no further request, 股利 failed, no div:batch, no row, no 股利 dot, the round settles', async () => {
    expect(DIVIDEND_MAX_PAGES).toBe(50);
    const svc = service();
    svc.start();
    await answer(0, { dividends: 'skip' });
    for (let page = 0; page < DIVIDEND_MAX_PAGES; page++) {
      expect(svc.settledRound()).toBe(0);
      land(dividends.subjects[page], { items: dueDividends(100, page * 100 + 1), total: 5001 });
    }
    TestBed.tick();
    expect(getDividends).toHaveBeenCalledTimes(DIVIDEND_MAX_PAGES);
    expect(svc.settledRound()).toBe(1);
    expect(svc.failedSources()).toEqual(['dividends']);
    expect(ids(svc)).toEqual([]);
    expect(svc.trackingRows()).toEqual([]);
    expect('dividends' in svc.badges()).toBe(false);
  });

  it('exactly DIVIDEND_MAX_PAGES pages (total 5000) is a complete scan: source ok, one batch of 5000', async () => {
    const svc = service();
    svc.start();
    await answer(0, { dividends: 'skip' });
    for (let page = 0; page < DIVIDEND_MAX_PAGES; page++) {
      land(dividends.subjects[page], { items: dueDividends(100, page * 100 + 1), total: 5000 });
    }
    TestBed.tick();
    expect(getDividends).toHaveBeenCalledTimes(DIVIDEND_MAX_PAGES);
    expect(svc.settledRound()).toBe(1);
    expect(svc.failedSources()).toEqual([]);
    expect(svc.tasks().map(task => task.title)).toEqual(['股利待確認收款 · 5000 筆']);
  });

  it('settlement: page 1 of the dividends answers, page 2 is still open at READ_TIMEOUT_MS → 股利 failed for the round, the round settles, page 1\'s rows make no task', async () => {
    const svc = service();
    svc.start();
    await answer(0, { dividends: 'skip' });
    // Page 1 lands 15 s into the round; the limit still counts from the round's start (one read for the whole stream).
    vi.advanceTimersByTime(15_000);
    land(dividends.subjects[0], { items: dueDividends(100), total: 150 });
    TestBed.tick();
    expect(dividends.subjects[1].observed).toBe(true);
    vi.advanceTimersByTime(READ_TIMEOUT_MS - 15_000 - 1);
    TestBed.tick();
    expect(svc.settledRound()).toBe(0);
    vi.advanceTimersByTime(1);
    TestBed.tick();
    expect(svc.settledRound()).toBe(1);
    expect(svc.failedSources()).toEqual(['dividends']);
    expect(dividends.subjects[1].observed).toBe(false);
    expect(ids(svc)).toEqual([]);
    expect('dividends' in svc.badges()).toBe(false);
  });

  it('settlement: a round does not settle while a dividend page is open before the limit, and settles when the last page lands', async () => {
    const svc = service();
    svc.start();
    await answer(0, { dividends: { items: dueDividends(100), total: 150 } });
    vi.advanceTimersByTime(READ_TIMEOUT_MS - 1);
    TestBed.tick();
    expect(svc.settledRound()).toBe(0);
    land(dividends.subjects[1], { items: dueDividends(50, 101), total: 150 });
    TestBed.tick();
    expect(svc.settledRound()).toBe(1);
    expect(svc.failedSources()).toEqual([]);
    expect(svc.tasks().map(task => task.title)).toEqual(['股利待確認收款 · 150 筆']);
  });

  it('a pending 國泰 date is a counted 系統 task (red count) and lights the 匯入 dot primary: 1 天的國泰對帳單待處理', async () => {
    const svc = service();
    svc.start();
    await answer(0, { cathayPending: { items: [pendingDay('2026-10-23')], total: 1, truncated: false } });
    expect(svc.tasks().map(task => [task.id, task.tier, task.title])).toEqual([['cathay:2026-10-23', 'system', '國泰對帳單 10/23']]);
    expect(svc.summary()).toMatchObject({ total: 1, tone: 'red' });
    expect(svc.badges()['import']).toEqual({ dot: true, tone: 'primary', label: '1 天的國泰對帳單待處理' });
    expect(svc.cathay()?.days.map(day => day.tradeDate)).toEqual(['2026-10-23']);
  });

  it('pending dates truncated by the server: the primary dot counts the server total (73), not the 50 listed', async () => {
    const svc = service();
    svc.start();
    const items = Array.from({ length: 50 }, (_, i) => pendingDay(`2026-08-${String((i % 28) + 1).padStart(2, '0')}`));
    await answer(0, { cathayPending: { items, total: 73, truncated: true } });
    expect(svc.badges()['import']).toEqual({ dot: true, tone: 'primary', label: '73 天的國泰對帳單待處理' });
  });

  it('pending dates and a bad run: red wins, the label is the run title alone', async () => {
    const svc = service();
    svc.start();
    await answer(0, {
      cathayPending: { items: [pendingDay('2026-10-23')], total: 1, truncated: false },
      cathayRuns: { latest: run(clockNow, 'latched'), latest_ok: null },
    });
    expect(svc.badges()['import']).toEqual({ dot: true, tone: 'red', label: '國泰對帳單匯入已停用' });
  });

  it('pending dates and an import failure: red wins with 最近一次匯入失敗', async () => {
    const svc = service();
    svc.start();
    await answer(0, {
      recalc: { kind: 'import', state: 'failed', started_at: '2026-10-24T01:00:00Z' },
      cathayPending: { items: [pendingDay('2026-10-23')], total: 1, truncated: false },
    });
    expect(svc.badges()['import']).toEqual({ dot: true, tone: 'red', label: '最近一次匯入失敗' });
  });

  it.each([
    ['failed', '國泰對帳單匯入失敗'],
    ['latched', '國泰對帳單匯入已停用'],
  ] as const)('a %s run lights the 匯入 dot red with %s', async (result, label) => {
    const svc = service();
    svc.start();
    await answer(0, { cathayRuns: { latest: run(clockNow, result), latest_ok: null } });
    expect(svc.tasks().map(task => [task.id, task.title])).toEqual([['cathay:run', label]]);
    expect(svc.badges()['import']).toEqual({ dot: true, tone: 'red', label });
    expect(svc.cathay()?.state).toBe(result);
  });

  it('a missing latest run (latest null) is stale: cathay:run with 還沒有執行紀錄', async () => {
    const svc = service();
    svc.start();
    await answer(0, { cathayRuns: { latest: null, latest_ok: null } });
    expect(svc.tasks().map(task => [task.id, task.title, task.meta])).toEqual([['cathay:run', '國泰對帳單今天沒有執行', '還沒有執行紀錄']]);
    expect(svc.badges()['import']).toEqual({ dot: true, tone: 'red', label: '國泰對帳單今天沒有執行' });
  });

  it('a stale run (clock 2026-10-28T07:00+08:00, latest 2026-10-27T06:41+08:00) creates cathay:run and the dot 國泰對帳單今天沒有執行', async () => {
    clockNow = new Date('2026-10-28T07:00:00+08:00');
    const latestRun: CathayRun = { run_at: '2026-10-27T06:41:00+08:00', result: 'ok', code: null, files_seen: 1, posted: 1 };
    const svc = service();
    svc.start();
    await answer(0, { cathayRuns: { latest: latestRun, latest_ok: latestRun } });
    expect(svc.tasks().map(task => [task.id, task.title, task.meta])).toEqual([['cathay:run', '國泰對帳單今天沒有執行', '上次執行 昨天 06:41']]);
    expect(svc.badges()['import']).toEqual({ dot: true, tone: 'red', label: '國泰對帳單今天沒有執行' });
  });

  it('an import failure and a bad run share one dot: 最近一次匯入失敗、國泰對帳單匯入失敗', async () => {
    const svc = service();
    svc.start();
    await answer(0, {
      recalc: { kind: 'import', state: 'failed', started_at: '2026-10-24T01:00:00Z' },
      cathayRuns: { latest: run(clockNow, 'failed'), latest_ok: null },
    });
    expect(ids(svc).sort()).toEqual(['cathay:run', 'import:fail:2026-10-24T01:00:00Z']);
    expect(svc.badges()['import']).toEqual({ dot: true, tone: 'red', label: '最近一次匯入失敗、國泰對帳單匯入失敗' });
  });

  it('the run verdict uses the round\'s clock: ok at 06:54, stale after the next round at 06:55', async () => {
    const latestRun: CathayRun = { run_at: '2026-10-27T06:41:00+08:00', result: 'ok', code: null, files_seen: 1, posted: 1 };
    const runs: CathayRuns = { latest: latestRun, latest_ok: latestRun };
    clockNow = new Date('2026-10-28T06:54:00+08:00');
    const svc = service();
    const before = clock.mock.calls.length;
    svc.start();
    expect(clock.mock.calls.length - before).toBe(1);
    await answer(0, { cathayRuns: runs });
    expect(ids(svc)).toEqual([]);
    expect(svc.cathay()?.state).toBe('ok');

    // The clock passing 06:55 changes nothing until a round reads it (P3).
    clockNow = new Date('2026-10-28T06:55:00+08:00');
    TestBed.tick();
    expect(ids(svc)).toEqual([]);
    expect(clock.mock.calls.length - before).toBe(1);

    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
    svc.refresh({ reason: 'focus' });
    expect(clock.mock.calls.length - before).toBe(2);
    await answer(1, { cathayRuns: runs });
    expect(ids(svc)).toEqual(['cathay:run']);
    expect(svc.cathay()?.state).toBe('stale');
    expect(svc.badges()['import']?.label).toBe('國泰對帳單今天沒有執行');
  });

  it('portfolio API down: 股利, 匯入 and 國泰對帳單 are named, their dots and tasks leave (the device failure too), ledger tasks stay, the round settles', async () => {
    TestBed.inject(ImportHistoryService).record({ at: '2026-10-24T01:00:00Z', file: '測試匯入.csv', type: 'transactions', ok: false, created: 0, errors: 1 });
    const svc = service();
    svc.start();
    const ledger = { accounts: [card(11, 20)], queue: [instance(1, 1, '測試房租', TODAY)] };
    await answer(0, {
      ...ledger,
      dividends: { items: dueDividends(2), total: 2 },
      cathayPending: { items: [pendingDay('2026-10-23')], total: 1, truncated: false },
      cathayRuns: { latest: run(clockNow, 'failed'), latest_ok: null },
    });
    expect(ids(svc).sort()).toEqual(['bill:11:2026-10-20', 'cathay:2026-10-23', 'cathay:run', 'div:batch', 'import:fail:2026-10-24T01:00:00Z', 'sched:1']);
    expect(Object.keys(svc.badges()).sort()).toEqual(['accounting', 'dividends', 'import']);

    svc.invalidate();
    await answer(1, { ...ledger, dividends: 'error', cathayPending: 'error', cathayRuns: 'error', recalc: 'error' });
    expect(svc.settledRound()).toBe(2);
    expect(svc.failedSources()).toEqual(['dividends', 'imports', 'cathay']);
    expect(ids(svc)).toEqual(['bill:11:2026-10-20', 'sched:1']);
    expect(svc.summary()).toMatchObject({ total: 2, overdue: 1 });
    expect(Object.keys(svc.badges())).toEqual(['accounting']);
    expect(svc.trackingRows()).toEqual([]);
    expect(svc.cathay()).toBeNull();
    expect(svc.cathayMore()).toBe(0);
  });

  it('pending answers but runs fails: the 國泰 source fails as a whole — no cathay task, cathay() null', async () => {
    const svc = service();
    svc.start();
    await answer(0, { cathayPending: { items: [pendingDay('2026-10-23')], total: 1, truncated: false }, cathayRuns: 'error' });
    expect(svc.settledRound()).toBe(1);
    expect(svc.failedSources()).toEqual(['cathay']);
    expect(ids(svc)).toEqual([]);
    expect(svc.cathay()).toBeNull();
    expect('import' in svc.badges()).toBe(false);
  });

  it('a 國泰 source that fails after it succeeded drops its tasks, its dot and cathayMore', async () => {
    const svc = service();
    svc.start();
    await answer(0, { cathayPending: { items: fiftyDays(), total: 73, truncated: true }, cathayRuns: { latest: run(clockNow, 'failed'), latest_ok: null } });
    expect(svc.tasks().filter(task => task.from === 'cathay')).toHaveLength(51);
    expect(svc.badges()['import']).toBeDefined();
    expect(svc.cathayMore()).toBe(23);

    svc.invalidate();
    await answer(1, { cathayPending: 'error' });
    expect(svc.failedSources()).toEqual(['cathay']);
    expect(svc.tasks().filter(task => task.from === 'cathay')).toEqual([]);
    expect('import' in svc.badges()).toBe(false);
    expect(svc.cathayMore()).toBe(0);
    expect(svc.cathay()).toBeNull();
  });

  it('cathayMore is total − listed (73, 50 → 23) and 0 when the source is unread', async () => {
    const svc = service();
    expect(svc.cathayMore()).toBe(0);
    expect(svc.cathay()).toBeNull();
    svc.start();
    expect(svc.cathayMore()).toBe(0);
    await answer(0, { cathayPending: { items: fiftyDays(), total: 73, truncated: true } });
    expect(svc.cathayMore()).toBe(23);
    expect(svc.cathay()?.more).toBe(23);
    // The note is not a task: 50 counted dates.
    expect(svc.summary().total).toBe(50);
  });
  // ---- PR C fix round 1 -------------------------------------------------------------------------------------------

  it('a round started on a later clock does not re-judge the previous 國泰 answer: no stale task until its own answer lands', async () => {
    const latestRun: CathayRun = { run_at: '2026-10-27T06:41:00+08:00', result: 'ok', code: null, files_seen: 1, posted: 1 };
    const runs: CathayRuns = { latest: latestRun, latest_ok: latestRun };
    clockNow = new Date('2026-10-27T22:00:00+08:00');
    const svc = service();
    svc.start();
    await answer(0, { cathayRuns: runs });
    expect(ids(svc)).toEqual([]);
    expect(svc.cathay()?.state).toBe('ok');
    const view = svc.cathay();

    // The page stays open overnight; a focus the next morning at 07:30 starts round 2, nothing answered yet.
    clockNow = new Date('2026-10-28T07:30:00+08:00');
    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
    svc.refresh({ reason: 'focus' });
    TestBed.tick();
    expect(svc.round()).toBe(2);
    expect(ids(svc)).toEqual([]);
    expect(svc.summary().total).toBe(0);
    expect(svc.summary().tone).toBe('primary');
    expect('import' in svc.badges()).toBe(false);
    expect(svc.cathay()).toEqual(view);

    // Round 2's answer (still yesterday's run) is judged with round 2's clock: stale.
    await answer(1, { cathayRuns: runs });
    expect(ids(svc)).toEqual(['cathay:run']);
    expect(svc.cathay()?.state).toBe('stale');
    expect(svc.badges()['import']?.label).toBe('國泰對帳單今天沒有執行');
  });

  it('a round started on the next day does not re-split the previous dividends answer until its own answer lands', async () => {
    clockNow = new Date(2026, 9, 24, 23, 0, 0);
    const svc = service();
    svc.start();
    const rows = { items: [dividend(1, '9901', '2026-10-25')], total: 1 };
    await answer(0, { dividends: rows });
    expect(ids(svc)).toEqual([]);
    expect(svc.trackingRows().map(row => row.id)).toEqual(['dividends']);

    clockNow = new Date(2026, 9, 25, 8, 0, 0);
    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
    svc.refresh({ reason: 'focus' });
    TestBed.tick();
    expect(ids(svc)).toEqual([]);
    expect(svc.trackingRows().map(row => row.id)).toEqual(['dividends']);
    expect('dividends' in svc.badges()).toBe(false);

    await answer(1, { dividends: rows });
    expect(ids(svc)).toEqual(['div:batch']);
    expect(svc.badges()['dividends']?.label).toBe('1 筆待確認收款');
  });

  it('a superseded round stops its dividend scan: no further page is requested, the new round settles on its own reads', async () => {
    const svc = service();
    svc.start();
    await answer(0, { dividends: { items: dueDividends(100), total: 350 } });
    expect(getDividends).toHaveBeenCalledTimes(2);
    expect(dividends.subjects[1].observed).toBe(true);

    svc.invalidate();
    // Round 1's open page is unsubscribed; landing it starts no page 3.
    expect(dividends.subjects[1].observed).toBe(false);
    land(dividends.subjects[1], { items: dueDividends(100, 101), total: 350 });
    expect(getDividends.mock.calls.map(([query]) => query.offset)).toEqual([0, 100, 0]);

    await answer(1, { dividends: 'skip' });
    expect(svc.settledRound()).toBe(0);
    land(dividends.subjects[2], { items: dueDividends(2), total: 2 });
    TestBed.tick();
    expect(svc.settledRound()).toBe(2);
    expect(svc.failedSources()).toEqual([]);
    expect(svc.tasks().map(task => task.title)).toEqual(['股利待確認收款 · 2 筆']);
  });

  describe('a malformed 200 body fails its source (never throws in a computed)', () => {
    const bad: readonly [string, unknown][] = [
      ['null', null],
      ['{}', {}],
      ['items not an array', { items: {}, total: 1, truncated: false }],
    ];

    const day = { trade_date: '2026-10-27', status: 'held', reason: null, since: null, last_at: null };
    const badPending: readonly [string, unknown][] = [
      ...bad,
      ['a numeric trade_date', { items: [{ ...day, trade_date: 20261027 }], total: 1, truncated: false }],
      ['an unknown status', { items: [{ ...day, status: 'weird' }], total: 1, truncated: false }],
      ['a numeric reason', { items: [{ ...day, reason: 42 }], total: 1, truncated: false }],
      ['an object reason', { items: [{ ...day, reason: { code: 'x' } }], total: 1, truncated: false }],
      ['a numeric since', { items: [{ ...day, since: 1 }], total: 1, truncated: false }],
      ['an array last_at', { items: [{ ...day, last_at: [] }], total: 1, truncated: false }],
    ];

    it.each(badPending)('國泰 pending %s → cathay failed, the round settles', async (_name, body) => {
      const svc = service();
      svc.start();
      await answer(0, { cathayPending: body as CathayPending });
      expect(svc.settledRound()).toBe(1);
      expect(svc.failedSources()).toEqual(['cathay']);
      expect(svc.cathay()).toBeNull();
      expect(svc.cathayMore()).toBe(0);
      expect(ids(svc)).toEqual([]);
      expect(svc.badges()).toEqual({});
    });

    it.each([
      ['null', null],
      ['{}', {}],
      ['latest without run_at', { latest: { result: 'ok' }, latest_ok: null }],
      ['latest with an unparseable run_at', { latest: { run_at: 'not a time', result: 'failed', code: null, files_seen: 0, posted: 0 }, latest_ok: null }],
      ['latest_ok with an unparseable run_at', {
        latest: { run_at: '2026-10-28T06:41:00+08:00', result: 'failed', code: null, files_seen: 0, posted: 0 },
        latest_ok: { run_at: '', result: 'ok', code: null, files_seen: 0, posted: 0 },
      }],
    ] as const)('國泰 runs %s → cathay failed, the round settles', async (_name, body) => {
      const svc = service();
      svc.start();
      await answer(0, { cathayRuns: body as unknown as CathayRuns });
      expect(svc.settledRound()).toBe(1);
      expect(svc.failedSources()).toEqual(['cathay']);
      expect(svc.cathay()).toBeNull();
      expect(ids(svc)).toEqual([]);
      expect(svc.badges()).toEqual({});
    });

    it.each(bad)('a dividends page %s → dividends failed, the round settles', async (_name, body) => {
      const svc = service();
      svc.start();
      await answer(0, { dividends: body as Paged<Dividend> });
      expect(svc.settledRound()).toBe(1);
      expect(svc.failedSources()).toEqual(['dividends']);
      expect(ids(svc)).toEqual([]);
      expect(svc.trackingRows()).toEqual([]);
      expect(svc.badges()).toEqual({});
    });

    it('a dividends page whose items hold a null row → dividends failed', async () => {
      const svc = service();
      svc.start();
      await answer(0, { dividends: { items: [null as unknown as Dividend], total: 1 } });
      expect(svc.failedSources()).toEqual(['dividends']);
      expect(ids(svc)).toEqual([]);
    });
  });

  it('runs fails while pending is still open: the 國泰 source fails, the pending read is dropped, the round settles', async () => {
    const svc = service();
    svc.start();
    await answer(0, { cathayPending: 'skip', cathayRuns: 'error' });
    expect(cathayPending.subjects[0].observed).toBe(false);
    expect(svc.settledRound()).toBe(1);
    expect(svc.failedSources()).toEqual(['cathay']);
    expect(svc.cathay()).toBeNull();
  });

  // ---- 永豐期貨對帳單 (sinopac_futures) -----------------------------------------------------------------------------

  describe('永豐期貨對帳單 source', () => {
    it('a pending item is a counted 系統 task sinopac_futures:<key>:<status> and lights the 匯入 dot primary', async () => {
      const svc = service();
      svc.start();
      await answer(0, { futuresPending: { items: [futuresItem('2026-10-23'), futuresItem('2026-10-23', { status: 'conflict', reason: null })], total: 2, truncated: false } });
      expect(svc.tasks().map(task => [task.id, task.tier, task.from])).toEqual([
        ['sinopac_futures:2026-10-23:conflict', 'system', 'sinopac_futures'],
        ['sinopac_futures:2026-10-23:findings', 'system', 'sinopac_futures'],
      ]);
      expect(svc.badges()['import']).toEqual({ dot: true, tone: 'primary', label: '2 件永豐期貨對帳單待處理' });
      expect(svc.futures()?.items.map(item => item.id)).toEqual(['2026-10-23:conflict', '2026-10-23:findings']);
    });

    it('pending 國泰 dates and futures items share the primary dot: both labels', async () => {
      const svc = service();
      svc.start();
      await answer(0, {
        cathayPending: { items: [pendingDay('2026-10-23')], total: 1, truncated: false },
        futuresPending: { items: [futuresItem('2026-10-23')], total: 1, truncated: false },
      });
      expect(svc.badges()['import']).toEqual({ dot: true, tone: 'primary', label: '1 天的國泰對帳單待處理、1 件永豐期貨對帳單待處理' });
    });

    it('a failed futures run: sinopac_futures:run and the red 匯入 dot, red winning over pending items', async () => {
      const svc = service();
      svc.start();
      await answer(0, {
        cathayPending: { items: [pendingDay('2026-10-23')], total: 1, truncated: false },
        futuresRuns: { latest: run(clockNow, 'failed'), latest_ok: null },
      });
      expect(ids(svc)).toContain('sinopac_futures:run');
      expect(svc.badges()['import']).toEqual({ dot: true, tone: 'red', label: '永豐期貨對帳單匯入失敗' });
      expect(svc.futures()?.state).toBe('failed');
    });

    it('never run (latest and latest_ok null): automatic import not enabled yet — no run task, no 匯入 dot, nothing counted', async () => {
      const svc = service();
      svc.start();
      await answer(0, { futuresRuns: { latest: null, latest_ok: null } });
      expect(svc.failedSources()).toEqual([]);
      expect(ids(svc)).toEqual([]);
      expect(svc.summary()).toEqual(NO_TASKS);
      expect('import' in svc.badges()).toBe(false);
      expect(svc.futures()).toMatchObject({ state: 'never', runLine: '尚未啟用自動匯入', banner: null, dotLabel: null });
    });

    it('a month key and a day key both pass; the first run (stale since yesterday) raises the run task and the red dot', async () => {
      const svc = service();
      svc.start();
      const yesterday = run(clockNow, 'ok', 24 * 60);
      await answer(0, {
        futuresPending: { items: [futuresItem('2026-09', { kind: 'month' }), futuresItem('2026-10-23')], total: 2, truncated: false },
        futuresRuns: { latest: yesterday, latest_ok: yesterday },
      });
      expect(svc.failedSources()).toEqual([]);
      expect(ids(svc)).toEqual(['sinopac_futures:2026-09:findings', 'sinopac_futures:2026-10-23:findings', 'sinopac_futures:run']);
      expect(svc.badges()['import']).toEqual({ dot: true, tone: 'red', label: '永豐期貨對帳單今天沒有執行' });
    });

    it('futuresMore is total − listed and 0 while unread', async () => {
      const svc = service();
      expect(svc.futuresMore()).toBe(0);
      svc.start();
      const items = Array.from({ length: 50 }, (_, i) => futuresItem(new Date(Date.UTC(2026, 6, 1 + i)).toISOString().slice(0, 10)));
      await answer(0, { futuresPending: { items, total: 60, truncated: true } });
      expect(svc.futuresMore()).toBe(10);
    });

    it.each([
      ['a pending item without a key', { futuresPending: { items: [{ kind: 'day', status: 'findings', reason: null, since: null, last_at: null }], total: 1, truncated: false } }],
      ['an unknown status', { futuresPending: { items: [futuresItem('2026-10-23', { status: 'held' as never })], total: 1, truncated: false } }],
      ['an unknown kind', { futuresPending: { items: [futuresItem('2026-10-23', { kind: 'week' as never })], total: 1, truncated: false } }],
      ['a numeric reason', { futuresPending: { items: [futuresItem('2026-10-23', { reason: 5 as never })], total: 1, truncated: false } }],
      ['no total', { futuresPending: { items: [] } }],
      ['a garbage key', { futuresPending: { items: [futuresItem('2026-1-23')], total: 1, truncated: false } }],
      ['a month key on a day item', { futuresPending: { items: [futuresItem('2026-10')], total: 1, truncated: false } }],
      ['a date key on a month item', { futuresPending: { items: [futuresItem('2026-10-23', { kind: 'month' })], total: 1, truncated: false } }],
      ['an unreadable run_at', { futuresRuns: { latest: { run_at: 'nope', result: 'ok', code: null, files_seen: 1, posted: 1 }, latest_ok: null } }],
      ['runs without latest_ok', { futuresRuns: { latest: null } }],
      ['an unexpected result', { futuresRuns: { latest: { run_at: '2026-10-24T01:00:00Z', result: 'unexpected', code: null, files_seen: 1, posted: 1 }, latest_ok: null } }],
      ['a run without posted', { futuresRuns: { latest: { run_at: '2026-10-24T01:00:00Z', result: 'ok', code: null, files_seen: 1 }, latest_ok: null } }],
      ['an object code on a failed run', { futuresRuns: { latest: { run_at: '2026-10-24T01:00:00Z', result: 'failed', code: { toString: null }, files_seen: 0, posted: 0 }, latest_ok: null } }],
      ['a numeric code', { futuresRuns: { latest: { run_at: '2026-10-24T01:00:00Z', result: 'failed', code: 5, files_seen: 0, posted: 0 }, latest_ok: null } }],
      ['a negative files_seen', { futuresRuns: { latest: { run_at: '2026-10-24T01:00:00Z', result: 'ok', code: null, files_seen: -1, posted: 0 }, latest_ok: null } }],
      ['a fractional posted', { futuresRuns: { latest: { run_at: '2026-10-24T01:00:00Z', result: 'ok', code: null, files_seen: 2, posted: 1.5 }, latest_ok: null } }],
      ['a string posted', { futuresRuns: { latest: { run_at: '2026-10-24T01:00:00Z', result: 'ok', code: null, files_seen: 1, posted: '1' }, latest_ok: null } }],
      ['an invalid latest_ok', { futuresRuns: { latest: null, latest_ok: { run_at: '2026-10-24T01:00:00Z', result: 'ok', code: null, files_seen: Infinity, posted: 0 } } }],
      ['a run without code', { futuresRuns: { latest: { run_at: '2026-10-24T01:00:00Z', result: 'ok', files_seen: 1, posted: 1 }, latest_ok: null } }],
    ])('invalid payload marks the source unreadable without touching 國泰 (%s)', async (_name, values) => {
      const svc = service();
      svc.start();
      await answer(0, {
        cathayPending: { items: [pendingDay('2026-10-23')], total: 1, truncated: false },
        ...(values as object),
      });
      expect(svc.settledRound()).toBe(1);
      expect(svc.failedSources()).toEqual(['sinopac_futures']);
      expect(svc.futures()).toBeNull();
      expect(svc.futuresMore()).toBe(0);
      expect(ids(svc)).toEqual(['cathay:2026-10-23']);
      expect(svc.cathay()?.days.map(day => day.tradeDate)).toEqual(['2026-10-23']);
      expect(svc.badges()['import']).toEqual({ dot: true, tone: 'primary', label: '1 天的國泰對帳單待處理' });
    });

    it('a futures source that fails after it succeeded drops its tasks and its dot; 國泰 keeps its own', async () => {
      const svc = service();
      svc.start();
      await answer(0, { futuresPending: { items: [futuresItem('2026-10-23')], total: 1, truncated: false } });
      expect(ids(svc)).toEqual(['sinopac_futures:2026-10-23:findings']);
      svc.invalidate();
      await answer(1, { futuresRuns: 'error', cathayPending: { items: [pendingDay('2026-10-22')], total: 1, truncated: false } });
      expect(svc.failedSources()).toEqual(['sinopac_futures']);
      expect(ids(svc)).toEqual(['cathay:2026-10-22']);
      expect(svc.futures()).toBeNull();
    });
  });
});
