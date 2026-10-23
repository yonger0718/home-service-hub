import { WritableSignal, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Observable, Subject, of } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { reminderDues } from '../components/accounting/billing/billing-math';
import { BillingService } from '../components/accounting/billing/billing.service';
import { countReminders } from '../components/accounting/billing/reminder-count';
import { makeAccount, makeEntry, makeInstance } from '../components/accounting/testing/fixtures';
import { Counterparty, ImportRun, LedgerAccount, ScheduleInstance } from '../models/accounting.model';
import { RecalcStatus } from '../models/portfolio.model';
import { AccountingService } from './accounting.service';
import { ImportHistoryService } from './import-history.service';
import { BADGE_REFRESH_MIN_MS, NavBadgeService } from './nav-badge.service';
import { PortfolioService } from './portfolio.service';

const TODAY = '2026-10-22';

/** Every call returns a fresh Subject the test answers by hand, so response order is under the test's control. */
function calls<T>(): { subjects: Subject<T>[]; fn: () => Observable<T> } {
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

function land<T>(subject: Subject<T>, value: T): void {
  subject.next(value);
  subject.complete();
}

function fail<T>(subject: Subject<T>): void {
  subject.error(new Error('boom'));
}

function importRun(needsReview: number): ImportRun {
  return {
    id: 1,
    status: 'succeeded',
    started_at: '2026-10-22T01:00:00Z',
    finished_at: '2026-10-22T01:01:00Z',
    file_name: 'backup.zip',
    file_sha256: 'abc',
    row_count: 10,
    summary: { needs_review: { count: needsReview, reasons: {} } } as unknown as ImportRun['summary'],
  };
}

/** Card 9: closes on the 15th, due on the 5th → the 2026-09-16..10-15 statement falls due 2026-11-05. */
function card(id = 9): LedgerAccount {
  return makeAccount({ id, name: `卡 ${id}`, is_credit: true, closing_day: 15, due_rule: 'fixed_day', due_value: 5 });
}

function counterparty(id: number, openCount: number): Counterparty {
  return { id, name: `對象 ${id}`, open_amounts: [], open_count: openCount };
}

describe('NavBadgeService', () => {
  let accounts: ReturnType<typeof calls<LedgerAccount[]>>;
  let counterparties: ReturnType<typeof calls<Counterparty[]>>;
  let queue: ReturnType<typeof calls<ScheduleInstance[]>>;
  let latest: ReturnType<typeof calls<ImportRun | null>>;
  let recalc: ReturnType<typeof calls<RecalcStatus>>;
  let accountingStub: Record<string, unknown>;
  let getRecalcStatus: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 9, 22, 10, 0, 0));
    accounts = calls();
    counterparties = calls();
    queue = calls();
    latest = calls();
    recalc = calls();
    getRecalcStatus = vi.fn(recalc.fn);
    accountingStub = {
      entriesChanged: signal(0),
      accountsChanged: signal(0),
      getAccounts: vi.fn(accounts.fn),
      getCounterparties: vi.fn(counterparties.fn),
      getScheduleInstances: vi.fn(queue.fn),
      getLatestImport: vi.fn(latest.fn),
      // The statement of card 9: 1,000 spent, nothing paid yet.
      getAccountSummary: vi.fn((accountId: number) =>
        of({
          account_id: accountId, currency: 'TWD', date_from: '', date_to: '', spend: '-1000', income: '0',
          rewards: '0', net: '-1000', end_balance: '-1000', count: 1,
        }),
      ),
      getEntries: vi.fn(() => of({ items: [], total: 0, limit: 100, offset: 0 })),
    };
    TestBed.configureTestingModule({
      providers: [
        { provide: AccountingService, useValue: accountingStub },
        { provide: PortfolioService, useValue: { getRecalcStatus } },
      ],
    });
  });

  afterEach(() => {
    TestBed.resetTestingModule();
    localStorage.clear();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function service(): NavBadgeService {
    return TestBed.inject(NavBadgeService);
  }

  /** Answers refresh `round` (0-based) of every source; pass `'error'` to fail one. */
  function answer(
    round: number,
    values: {
      accounts?: LedgerAccount[] | 'error';
      counterparties?: Counterparty[] | 'error';
      queue?: ScheduleInstance[] | 'error';
      latest?: ImportRun | null | 'error';
      recalc?: RecalcStatus | 'error';
    } = {},
  ): void {
    const settle = <T>(subject: Subject<T>, value: T | 'error' | undefined, fallback: T) =>
      value === 'error' ? fail(subject) : land(subject, value === undefined ? fallback : value);
    settle(accounts.subjects[round], values.accounts, []);
    settle(counterparties.subjects[round], values.counterparties, []);
    settle(queue.subjects[round], values.queue, []);
    settle(latest.subjects[round], values.latest, null);
    settle(recalc.subjects[round], values.recalc, { state: 'idle' } as RecalcStatus);
    TestBed.tick();
  }

  it('failed recalc chain → import dot', () => {
    const svc = service();
    svc.refresh();
    answer(0, { recalc: { kind: 'import', state: 'failed' } });
    expect(svc.badges()['import']).toEqual({ dot: true, label: '最近一次匯入失敗' });
  });

  it('a completed recalc chain shows no import dot', () => {
    const svc = service();
    svc.refresh();
    answer(0, { recalc: { kind: 'import', state: 'completed' } });
    expect('import' in svc.badges()).toBe(false);
  });

  it('the device\'s last failed commit → import dot even with a healthy recalc chain', () => {
    TestBed.inject(ImportHistoryService).record({ at: '2026-10-22T01:00:00Z', file: 'a.csv', type: 'transactions', ok: false, created: 0, errors: 1 });
    const svc = service();
    svc.refresh();
    answer(0, { recalc: { kind: 'import', state: 'completed' } });
    expect(svc.badges()['import']).toEqual({ dot: true, label: '最近一次匯入失敗' });
  });

  it('a successful commit after the failure clears the dot while the recalc chain is not failed', () => {
    const history = TestBed.inject(ImportHistoryService);
    history.record({ at: '2026-10-22T01:00:00Z', file: 'a.csv', type: 'transactions', ok: false, created: 0, errors: 1 });
    const svc = service();
    svc.refresh();
    answer(0, { recalc: { kind: 'import', state: 'completed' } });
    expect(svc.badges()['import']).toBeDefined();
    history.record({ at: '2026-10-22T02:00:00Z', file: 'a.csv', type: 'transactions', ok: true, created: 3 });
    expect('import' in svc.badges()).toBe(false);
  });

  it('a successful head attempt keeps the dot while the recalc chain failed', () => {
    TestBed.inject(ImportHistoryService).record({ at: '2026-10-22T02:00:00Z', file: 'a.csv', type: 'transactions', ok: true, created: 3 });
    const svc = service();
    svc.refresh();
    answer(0, { recalc: { kind: 'import', state: 'failed' } });
    expect(svc.badges()['import']).toEqual({ dot: true, label: '最近一次匯入失敗' });
  });

  it('a failed quote refresh shows no import dot', () => {
    const svc = service();
    svc.refresh();
    answer(0, { recalc: { kind: 'quotes', state: 'failed' } });
    expect('import' in svc.badges()).toBe(false);
  });

  it('latest import with 1 needs_review → accounting count', () => {
    const svc = service();
    svc.refresh();
    answer(0, { latest: importRun(1) });
    expect(svc.badges()['accounting']).toEqual({ count: 1, label: '1 筆待確認' });
  });

  it('latest import 404 (null) → no accounting key', () => {
    const svc = service();
    svc.refresh();
    answer(0, { latest: null });
    expect('accounting' in svc.badges()).toBe(false);
  });

  it('a 0 count is no entry', () => {
    const svc = service();
    svc.refresh();
    answer(0, { latest: importRun(0) });
    expect(svc.badges()).toEqual({});
  });

  it('counts the reminders: queue items due today', () => {
    const svc = service();
    svc.refresh();
    answer(0, { queue: [makeInstance({ id: 1, due_date: TODAY }), makeInstance({ id: 2, due_date: '2026-10-25' })] });
    expect(svc.badges()['accounting-timeline']).toEqual({ count: 1, label: '提醒中心 1 項' });
  });

  it('a failing source hides only its own badge', () => {
    const svc = service();
    svc.refresh();
    answer(0, { queue: [makeInstance({ due_date: TODAY })], latest: importRun(2) });
    expect(svc.badges()['accounting-timeline']).toEqual({ count: 1, label: '提醒中心 1 項' });

    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
    svc.refresh();
    expect(() => answer(1, { queue: [makeInstance({ due_date: TODAY })], counterparties: 'error', latest: importRun(2) })).not.toThrow();
    expect('accounting-timeline' in svc.badges()).toBe(false);
    expect(svc.badges()['accounting']).toEqual({ count: 2, label: '2 筆待確認' });
  });

  it('refresh within 30 s is skipped, after 30 s refires', () => {
    const svc = service();
    svc.refresh();
    expect(getRecalcStatus).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(10_000);
    svc.refresh();
    expect(getRecalcStatus).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS - 10_000 - 1);
    svc.refresh();
    expect(getRecalcStatus).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1);
    svc.refresh();
    expect(getRecalcStatus).toHaveBeenCalledTimes(2);
  });

  it('start() twice registers one focus listener', () => {
    const addListener = vi.spyOn(window, 'addEventListener');
    const svc = service();
    svc.start();
    svc.start();
    expect(addListener.mock.calls.filter(([type]) => type === 'focus')).toHaveLength(1);
    expect(getRecalcStatus).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
    window.dispatchEvent(new Event('focus'));
    expect(getRecalcStatus).toHaveBeenCalledTimes(2);
  });

  it('an entry or account write reloads the reminders and the latest import at once, not the recalc status', () => {
    const svc = service();
    svc.start();
    TestBed.tick();
    answer(0);
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
    expect(svc.badges()['accounting']).toEqual({ count: 3, label: '3 筆待確認' });

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

    it('refreshes on visibilitychange to visible, throttled like focus', () => {
      service().start();
      expect(getRecalcStatus).toHaveBeenCalledTimes(1);

      vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
      becomes('hidden');
      expect(getRecalcStatus).toHaveBeenCalledTimes(1);
      becomes('visible');
      expect(getRecalcStatus).toHaveBeenCalledTimes(2);

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

  it('older response landing after a newer one is dropped', () => {
    const svc = service();
    svc.refresh();
    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
    svc.refresh();
    land(recalc.subjects[1], { kind: 'import', state: 'completed' });
    land(recalc.subjects[0], { kind: 'import', state: 'failed' });
    expect('import' in svc.badges()).toBe(false);
  });

  it('an older success landing after a newer failure is dropped', () => {
    const svc = service();
    svc.refresh();
    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
    svc.refresh();
    fail(recalc.subjects[1]);
    land(recalc.subjects[0], { kind: 'import', state: 'failed' });
    expect('import' in svc.badges()).toBe(false);
  });

  it('slow first load across the 30 s window: one consistent result from the newer round', () => {
    const svc = service();
    svc.start();
    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
    window.dispatchEvent(new Event('focus'));
    expect(getRecalcStatus).toHaveBeenCalledTimes(2);

    answer(0, {
      queue: [makeInstance({ due_date: TODAY })],
      latest: importRun(5),
      recalc: { kind: 'import', state: 'failed' },
    });
    expect(svc.badges()).toEqual({});
    answer(1, { latest: importRun(1) });
    expect(svc.badges()).toEqual({ accounting: { count: 1, label: '1 筆待確認' } });
  });

  it('stale accounts snapshot never reaches the statements (ensure or refresh)', () => {
    const bills = TestBed.inject(BillingService);
    const ensure = vi.spyOn(bills, 'ensure');
    const reread = vi.spyOn(bills, 'refresh');
    const svc = service();
    svc.refresh();
    vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
    svc.refresh();

    answer(1, { accounts: [card(9)] });
    answer(0, { accounts: [card(7)] });

    expect(reread).toHaveBeenCalled();
    const ids = [...ensure.mock.calls, ...reread.mock.calls].flatMap(([dues]) => dues.map(event => event.accountId));
    expect(ids).toContain(9);
    expect(ids).not.toContain(7);
  });

  it('timeline and badge agree', () => {
    const svc = service();
    const bills = TestBed.inject(BillingService);
    svc.refresh();
    const cards = [card(9)];
    const parties = [counterparty(1, 1), counterparty(2, 0)];
    answer(0, { accounts: cards, counterparties: parties });

    const expected = countReminders({
      dues: reminderDues(cards, TODAY),
      bill: event => bills.bill(event),
      counterparties: parties,
      queue: [],
      today: TODAY,
    });
    expect(expected).toBe(2);
    expect(svc.badges()['accounting-timeline']).toEqual({ count: expected, label: `提醒中心 ${expected} 項` });
  });
  describe('card statements on a refresh (payments made elsewhere, no local write)', () => {
    function paidInFull(): void {
      (accountingStub['getEntries'] as ReturnType<typeof vi.fn>).mockImplementation(() =>
        of({ items: [makeEntry({ id: 70, kind: 'transfer_in', amount: '1000' })], total: 1, limit: 100, offset: 0 }),
      );
    }

    it('a focus refresh after 30 s re-reads the cached statement and updates the count', () => {
      const svc = service();
      svc.start();
      answer(0, { accounts: [card(9)] });
      expect(svc.badges()['accounting-timeline']).toEqual({ count: 1, label: '提醒中心 1 項' });
      const reads = (accountingStub['getEntries'] as ReturnType<typeof vi.fn>).mock.calls.length;

      paidInFull();
      vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
      window.dispatchEvent(new Event('focus'));
      answer(1, { accounts: [card(9)] });

      expect((accountingStub['getEntries'] as ReturnType<typeof vi.fn>).mock.calls.length).toBe(reads + 1);
      expect('accounting-timeline' in svc.badges()).toBe(false);
    });

    it('a focus within 30 s does not re-read the statement', () => {
      const svc = service();
      svc.start();
      answer(0, { accounts: [card(9)] });
      const reads = (accountingStub['getEntries'] as ReturnType<typeof vi.fn>).mock.calls.length;

      paidInFull();
      vi.advanceTimersByTime(10_000);
      window.dispatchEvent(new Event('focus'));
      TestBed.tick();

      expect(accounts.subjects).toHaveLength(1);
      expect((accountingStub['getEntries'] as ReturnType<typeof vi.fn>).mock.calls.length).toBe(reads);
      expect(svc.badges()['accounting-timeline']).toEqual({ count: 1, label: '提醒中心 1 項' });
    });

    it('re-reads once per accepted refresh, not on every later run of the badge effect', () => {
      const svc = service();
      const refresh = vi.spyOn(TestBed.inject(BillingService), 'refresh');
      svc.start();
      answer(0, { accounts: [card(9)] });
      expect(refresh).toHaveBeenCalledTimes(1);

      // A write reloads the reminders (a new change round re-reads through ensure): no extra forced round.
      (accountingStub['entriesChanged'] as WritableSignal<number>).set(1);
      TestBed.tick();
      land(accounts.subjects[1], [card(9)]);
      land(counterparties.subjects[1], []);
      land(queue.subjects[1], []);
      TestBed.tick();
      expect(refresh).toHaveBeenCalledTimes(1);

      vi.advanceTimersByTime(BADGE_REFRESH_MIN_MS + 1_000);
      window.dispatchEvent(new Event('focus'));
      land(accounts.subjects[2], [card(9)]);
      land(counterparties.subjects[2], []);
      land(queue.subjects[2], []);
      TestBed.tick();
      expect(refresh).toHaveBeenCalledTimes(2);
    });
  });
});
