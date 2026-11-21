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
import { INBOX_READ_LIMIT } from '../components/notifications/sources/recon';
import { BusinessDays, Counterparty, ImportRun, LedgerAccount, ScheduleInstance } from '../models/accounting.model';
import { RecalcStatus } from '../models/portfolio.model';
import { AccountingService } from './accounting.service';
import { ImportHistoryService } from './import-history.service';
import { NavigationPreferencesService } from './navigation-preferences.service';
import { BADGE_REFRESH_MIN_MS, NotificationCenterService } from './notification-center.service';
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

describe('NotificationCenterService', () => {
  let accounts: ReturnType<typeof calls<LedgerAccount[]>>;
  let counterparties: ReturnType<typeof calls<Counterparty[]>>;
  let queue: ReturnType<typeof calls<ScheduleInstance[]>>;
  let latest: ReturnType<typeof calls<ImportRun | null>>;
  let recalc: ReturnType<typeof calls<RecalcStatus>>;
  let inbox: ReturnType<typeof calls<InboxOut>>;
  let actions: ReturnType<typeof calls<ReconciliationAction[]>>;
  let statements: ReturnType<typeof calls<StatementSummary[]>>;
  let accountingStub: Record<string, unknown>;
  let getRecalcStatus: ReturnType<typeof vi.fn>;
  let getInbox: ReturnType<typeof vi.fn>;
  let getActions: ReturnType<typeof vi.fn>;
  let listStatements: ReturnType<typeof vi.fn>;

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
    getRecalcStatus = vi.fn(recalc.fn);
    getInbox = vi.fn(inbox.fn);
    getActions = vi.fn(actions.fn);
    listStatements = vi.fn(statements.fn);
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
    TestBed.configureTestingModule({
      providers: [
        { provide: AccountingService, useValue: accountingStub },
        { provide: PortfolioService, useValue: { getRecalcStatus } },
        { provide: InboxService, useValue: { get: getInbox, actions: getActions } },
        { provide: StatementsService, useValue: { list: listStatements } },
      ],
    });
  });

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
   * `'skip'` leaves it open. The recalc status is answered only when that call exists (refresh rounds).
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
    TestBed.tick();
    await microtasks();
    TestBed.tick();
  }

  const ids = (svc: NotificationCenterService) => svc.tasks().map(task => task.id);

  // ---- carried over from nav-badge.service.spec.ts ---------------------------------------------------------------

  it('failed recalc chain → import dot', async () => {
    const svc = service();
    svc.refresh({ reason: 'focus' });
    await answer(0, { recalc: { kind: 'import', state: 'failed' } });
    expect(svc.badges()['import']).toEqual({ dot: true, tone: 'red', label: '最近一次匯入失敗' });
  });

  it('a completed recalc chain shows no import dot', async () => {
    const svc = service();
    svc.refresh({ reason: 'focus' });
    await answer(0, { recalc: { kind: 'import', state: 'completed' } });
    expect('import' in svc.badges()).toBe(false);
  });

  it('the device\'s last failed commit → import dot even with a healthy recalc chain', async () => {
    TestBed.inject(ImportHistoryService).record({ at: '2026-10-24T01:00:00Z', file: 'a.csv', type: 'transactions', ok: false, created: 0, errors: 1 });
    const svc = service();
    svc.refresh({ reason: 'focus' });
    await answer(0, { recalc: { kind: 'import', state: 'completed' } });
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

  it('an entry or account write reloads every task source at once, not the recalc status', async () => {
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
    expect(getRecalcStatus).toHaveBeenCalledTimes(1);

    land(latest.subjects[1], importRun(3));
    TestBed.tick();
    expect(svc.badges()['accounting-timeline']).toEqual({ dot: true, tone: 'primary', label: '有待確認的匯入紀錄' });

    (accountingStub['accountsChanged'] as WritableSignal<number>).set(1);
    TestBed.tick();
    expect(accountingStub['getCounterparties']).toHaveBeenCalledTimes(3);
    expect(accountingStub['getLatestImport']).toHaveBeenCalledTimes(3);
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

  it('older response landing after a newer one is dropped', async () => {
    const svc = service();
    svc.refresh({ reason: 'focus' });
    await answer(0, { recalc: 'skip' });
    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
    svc.refresh({ reason: 'focus' });
    land(recalc.subjects[1], { kind: 'import', state: 'completed' });
    land(recalc.subjects[0], { kind: 'import', state: 'failed' });
    expect('import' in svc.badges()).toBe(false);
  });

  it('an older success landing after a newer failure is dropped', async () => {
    const svc = service();
    svc.refresh({ reason: 'focus' });
    await answer(0, { recalc: 'skip' });
    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
    svc.refresh({ reason: 'focus' });
    fail(recalc.subjects[1]);
    land(recalc.subjects[0], { kind: 'import', state: 'failed' });
    expect('import' in svc.badges()).toBe(false);
  });

  it('slow first load across the 30 s window: a focus starts nothing while it is in flight; its answers apply', async () => {
    const svc = service();
    svc.start();
    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
    window.dispatchEvent(new Event('focus'));
    expect(getRecalcStatus).toHaveBeenCalledTimes(1);
    expect(svc.round()).toBe(1);

    await answer(0, { latest: importRun(5), recalc: { kind: 'import', state: 'failed' } });
    expect(svc.badges()).toEqual({
      'accounting-timeline': { dot: true, tone: 'primary', label: '有待確認的匯入紀錄' },
      import: { dot: true, tone: 'red', label: '最近一次匯入失敗' },
    });
    expect(svc.settledRound()).toBe(1);
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

  it('refresh (any reason) while a round is in flight starts nothing', () => {
    const svc = service();
    svc.start();
    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
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
    expect(svc.summary().total).toBe(0);

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

  it('invalidate() does not read the recalc status', () => {
    const svc = service();
    svc.invalidate();
    expect(svc.round()).toBe(1);
    expect(accountingStub['getAccounts']).toHaveBeenCalledTimes(1);
    expect(getRecalcStatus).not.toHaveBeenCalled();
  });

  // ---- R25: a truncated inbox ------------------------------------------------------------------------------------

  it('truncated inbox: counts.ask_items 10 with 6 unfolded received → the statement task plus recon:more 「還有 4 筆要你決定」, total counts both, truncated() true', async () => {
    const svc = service();
    svc.start();
    await answer(0, { inbox: inboxWith(6, { ask_items: 10 }) });
    expect(ids(svc)).toEqual(['recon:10', 'recon:more']);
    expect(svc.tasks()[0].when).toBe('6 筆要你決定');
    expect(svc.tasks()[1].when).toBe('還有 4 筆要你決定');
    expect(svc.summary().total).toBe(2);
    expect(svc.truncated()).toBe(true);
  });

  it('truncated inbox with no statement received: recon:more alone, summary().total 1', async () => {
    const svc = service();
    svc.start();
    await answer(0, { inbox: inboxWith(0, { ask: 3, ask_items: 3 }) });
    expect(ids(svc)).toEqual(['recon:more']);
    expect(svc.summary().total).toBe(1);
    expect(svc.truncated()).toBe(true);
  });

  it('a complete inbox: no recon:more, truncated() false', async () => {
    const svc = service();
    svc.start();
    await answer(0, { inbox: inboxWith(3) });
    expect(ids(svc)).toEqual(['recon:10']);
    expect(svc.truncated()).toBe(false);
  });

  it('a failed inbox read clears truncated', async () => {
    const svc = service();
    svc.start();
    await answer(0, { inbox: inboxWith(6, { ask_items: 10 }) });
    expect(svc.truncated()).toBe(true);

    svc.invalidate();
    await answer(1, { inbox: 'error' });
    expect(svc.truncated()).toBe(false);
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
    TestBed.configureTestingModule({
      providers: [
        { provide: AccountingService, useValue: accountingStub },
        { provide: PortfolioService, useValue: { getRecalcStatus } },
        { provide: InboxService, useValue: { get: getInbox, actions: getActions } },
        { provide: StatementsService, useValue: { list: listStatements } },
      ],
    });
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
});
