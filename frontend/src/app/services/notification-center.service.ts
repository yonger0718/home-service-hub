import { DestroyRef, Injectable, Injector, Signal, computed, effect, inject, signal, untracked } from '@angular/core';
import { DOCUMENT } from '@angular/common';
import { Observable, Subject, catchError, forkJoin, from, map, of, takeUntil, timeout } from 'rxjs';

import { BillingEvent, reminderDues } from '../components/accounting/billing/billing-math';
import { WEEKENDS_ONLY } from '../components/accounting/billing/business-days';
import { BillingService } from '../components/accounting/billing/billing.service';
import { todayIso } from '../components/accounting/dates';
import { InboxOut } from '../components/accounting/inbox/inbox.model';
import { InboxService } from '../components/accounting/inbox/inbox.service';
import { ReconProgress, reconProgress } from '../components/accounting/statements/statement-view';
import { StatementsService } from '../components/accounting/statements/statements.service';
import {
  COUNTED_TIERS,
  NO_TASKS,
  NotifSourceKey,
  NotifSummary,
  NotifTask,
  NotifTrackingRow,
  SOURCE_NAMES,
  sortTasks,
  summarize,
} from '../components/notifications/notif-task';
import { billTasks } from '../components/notifications/sources/bills';
import { CathayView, cathayMore, cathayTasks, cathayView } from '../components/notifications/sources/cathay';
import { debtsRow, handledText, importReviewTask, upcomingRow } from '../components/notifications/sources/ledger-misc';
import {
  INBOX_READ_LIMIT,
  askAccountIds,
  einvoiceTask,
  inboxMissing,
  reconOverflowTask,
  reconTasks,
} from '../components/notifications/sources/recon';
import {
  dividendBatch,
  dividendsBadge,
  dividendsRow,
  importFailureTask,
  recalcFailed,
  splitPendingDividends,
} from '../components/notifications/sources/portfolio';
import { SnoozeMap, SnoozeOption, snoozeEntry, snoozeUntil, wakes } from '../components/notifications/snooze';
import { scheduleTasks } from '../components/notifications/sources/schedules';
import { allPages } from '../components/portfolio/paging';
import { NavBadge } from '../components/shell/navigation';
import { Counterparty, ImportRun, LedgerAccount, ScheduleInstance } from '../models/accounting.model';
import { CathayPending, CathayPendingDay, CathayRun, CathayRuns, Dividend, Paged, RecalcStatus } from '../models/portfolio.model';
import { AccountingService } from './accounting.service';
import { BusinessCalendarService } from './business-calendar.service';
import { ImportHistoryService } from './import-history.service';
import { NOTIF_CLOCK } from './notif-clock';
import { NotifSnoozeStore } from './notif-snooze.store';
import { PortfolioService } from './portfolio.service';

/** The round's clock (also the snooze clock); tests provide a fixed one. */
export { NOTIF_CLOCK };

/** The snooze timer never waits longer than this before re-reading the clock (a sleeping device, a changed clock). */
const SNOOZE_TIMER_MAX_MS = 24 * 3_600_000;

/** The dividend scan's page cap (`allPages`' default, named so the tests can cite it): beyond it the source fails (C12). */
export const DIVIDEND_MAX_PAGES = 50;

/** The dividend scan's page size (as the 股利 page reads it). */
const DIVIDEND_PAGE_SIZE = 100;

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null;

/** A dividends page with an array of row objects and a numeric total; else an error, so the source fails. */
function checkedPage(page: unknown): Paged<Dividend> {
  if (!isObject(page) || !Array.isArray(page['items']) || typeof page['total'] !== 'number' || !page['items'].every(isObject)) {
    throw new Error('dividends page');
  }
  return page as unknown as Paged<Dividend>;
}

const PENDING_STATUSES: readonly unknown[] = ['held', 'mismatch', 'conflict'] satisfies CathayPendingDay['status'][];
/** Null, absent or a string (`reason` / `since` / `last_at`). */
const optionalText = (value: unknown): boolean => value === null || value === undefined || typeof value === 'string';

/**
 * 國泰 pending: dates with a string `trade_date`, a known `status`, and `reason` / `since` / `last_at` each null or a
 * string, plus a numeric total; else an error, so the source fails (a non-string reason would throw in a computed).
 */
function checkedPending(pending: unknown): CathayPending {
  const day = (d: unknown) =>
    isObject(d) &&
    typeof d['trade_date'] === 'string' &&
    PENDING_STATUSES.includes(d['status']) &&
    optionalText(d['reason']) &&
    optionalText(d['since']) &&
    optionalText(d['last_at']);
  if (!isObject(pending) || !Array.isArray(pending['items']) || typeof pending['total'] !== 'number' || !pending['items'].every(day)) {
    throw new Error('cathay pending');
  }
  return pending as unknown as CathayPending;
}

/**
 * 國泰 runs: `latest` / `latest_ok` each null or a run with a `run_at` that parses to a finite time and a string
 * `result`; else an error (source fails): an unparseable run_at would render 「上次執行 NaN/NaN NaN:NaN」.
 */
function checkedRuns(runs: unknown): CathayRuns {
  const run = (r: unknown): r is CathayRun | null =>
    r === null ||
    (isObject(r) &&
      typeof r['run_at'] === 'string' &&
      Number.isFinite(new Date(r['run_at']).getTime()) &&
      typeof r['result'] === 'string');
  if (!isObject(runs) || !('latest' in runs) || !('latest_ok' in runs) || !run(runs['latest']) || !run(runs['latest_ok'])) {
    throw new Error('cathay runs');
  }
  return runs as unknown as CathayRuns;
}

/** A focus / visible / timer refresh starting sooner than this after the last started round is skipped. */
export const BADGE_REFRESH_MIN_MS = 30_000;

/**
 * A read of a round that has not answered after this long counts as failed for that round (the HTTP request is
 * aborted), and so does a round's card-statement wait: every round settles, `failureLine` names the source, and the
 * next open / focus / timer starts a fresh round (amends R26: an unanswered read would otherwise block `refresh()`).
 *
 * Worst case a round settles after ~2 × this (~40 s): the card-statement wait starts only when the accounts answer
 * lands, so accounts answering just inside the limit followed by a hanging card statement takes both windows.
 */
export const READ_TIMEOUT_MS = 20_000;

/**
 * 'open' (the notifications page being shown) is skipped when the newest round started less than this long ago: on a
 * direct load the shell's startup round has just begun and a second one would read every source twice.
 */
export const OPEN_FRESH_MS = 5_000;

/** `open`: the 通知 page opened (skipped only inside OPEN_FRESH_MS of a round start); `focus`: window focus or page visible again; `timer`: a periodic caller. */
export type NotifRefreshReason = 'open' | 'focus' | 'timer';

/** The reconciliation actions page read for the handled row (P8: the feed's default page). */
const HANDLED_LIMIT = 50;

/** The task sources a round waits for, in `SOURCE_NAMES` order. */
const TASK_SOURCES = Object.keys(SOURCE_NAMES) as NotifSourceKey[];

/** A task source's last applied answer: the round that set it and whether it was read. */
interface SourceStatus {
  readonly round: number;
  readonly state: 'idle' | 'ok' | 'failed';
}

const IDLE: SourceStatus = { round: 0, state: 'idle' };

/**
 * The notification centre's state (R26, R27). A round has an id (`round()`); it reads every task source once (accounts
 * → card statements, schedule queue, reconciliation inbox, latest import, counterparties, every dividend page, the
 * recalc status, the 國泰 pending dates + latest run) plus the silent reads (statement progress, handled actions), with
 * `now` taken once from `NOTIF_CLOCK` at its start. An answer is applied only while its round is the newest started
 * one, so a superseded round changes nothing and never settles. `settledRound()` is the newest round whose eight task
 * sources — the card statements included — have all answered, ok or failed.
 *
 * Each source fails on its own: its tasks and its dot leave, `failedSources()` names it, the others stay. No read
 * raises a toast. Every source is read whatever groups are hidden (R13).
 */
@Injectable({ providedIn: 'root' })
export class NotificationCenterService {
  private readonly accounting = inject(AccountingService);
  private readonly portfolio = inject(PortfolioService);
  private readonly bills = inject(BillingService);
  private readonly injector = inject(Injector);
  private readonly inbox = inject(InboxService);
  private readonly statements = inject(StatementsService);
  private readonly importHistory = inject(ImportHistoryService);
  private readonly document = inject(DOCUMENT);
  private readonly destroyRef = inject(DestroyRef);
  private readonly clock = inject(NOTIF_CLOCK);

  private readonly snoozes = inject(NotifSnoozeStore);
  /** The snooze clock: the round clock at a round start, at a snooze, and when the earliest snooze ends. */
  private readonly snoozeNow = signal<Date>(this.clock());

  private readonly roundId = signal(0);
  private readonly settledId = signal(0);
  private readonly status = signal<Readonly<Record<NotifSourceKey, SourceStatus>>>({
    bills: IDLE, schedules: IDLE, recon: IDLE, importReview: IDLE, debts: IDLE, dividends: IDLE, imports: IDLE, cathay: IDLE,
  });
  /** The clock as of the newest started round (P3: read once per round). */
  private readonly now = signal<Date>(this.clock());
  /** Today as of the newest started round. */
  private readonly today = computed(() => todayIso(this.now()));

  /** The accepted accounts snapshot (null before the first answer and after a failed read). */
  private readonly accountList = signal<LedgerAccount[] | null>(null);
  private readonly queue = signal<ScheduleInstance[] | null>(null);
  private readonly inboxData = signal<InboxOut | null>(null);
  /** Per statement id: 已對上 k / n, from the statement lists of the accounts with ask items. */
  private readonly progress = signal<ReadonlyMap<number, ReconProgress>>(new Map());
  private readonly latestImport = signal<ImportRun | null>(null);
  private readonly counterparties = signal<Counterparty[] | null>(null);
  private readonly actionsText = signal<string | null>(null);
  /**
   * Every dividend row with the `today` of the round that read them (null before the first answer, after a failed read
   * and after a capped scan): a round started on a later day does not re-split the previous answer before its own lands.
   */
  private readonly dividendData = signal<{ readonly items: Dividend[]; readonly today: string } | null>(null);
  /** The applied recalc status answer (its `status` may be null: an empty body); null when unread or failed. */
  private readonly recalcAnswer = signal<{ readonly status: RecalcStatus | null } | null>(null);
  /**
   * The applied 國泰 answers, both reads or none (P1), with the clock of the round that read them: the run verdict is
   * always the answer judged at its own round's `now`, never an older answer re-judged by a newer round still in flight.
   */
  private readonly cathayData = signal<{ readonly pending: CathayPending; readonly runs: CathayRuns; readonly now: Date } | null>(null);
  /** Emits when a round starts: the previous round's dividend scan stops requesting pages. */
  private readonly roundStarted = new Subject<void>();

  /**
   * The business-day calendar, first asked for on the first round (not at construction: the calendar service reads
   * the server once when created, and this service is created with the shell). `WEEKENDS_ONLY` before that.
   */
  private readonly calendar = signal<BusinessCalendarService | null>(null);
  /**
   * The cards' current statements as due events, with the debit day from the calendar: recomputed when the calendar
   * lands, so the rounds `BillingService` keeps carry the right `payDate` (its round fingerprint includes it).
   */
  private readonly dues: Signal<BillingEvent[]> = computed(() => {
    const accounts = this.accountList();
    if (!accounts) {
      return [];
    }
    const calendar = this.calendar()?.calendar() ?? WEEKENDS_ONLY;
    return reminderDues(accounts, this.today(), calendar).filter(event => event.kind === 'due');
  });

  private started = false;
  private lastStartedAt: number | null = null;
  /** `BillingService.generation()` at the previous round's statements call; null before the first one. */
  private lastBillsGeneration: number | null = null;

  /** The pending dividends split by payment date (null while the source is unread). */
  private readonly dividendSplit = computed(() => {
    const data = this.dividendData();
    return data ? splitPendingDividends(data.items, data.today) : null;
  });

  /** The 系統 import-failure task (the recalc chain or this device's last commit), while the recalc status is read. */
  private readonly importFailure: Signal<NotifTask | null> = computed(() => {
    const answer = this.recalcAnswer();
    if (!answer || !(recalcFailed(answer.status) || this.importHistory.lastFailed())) {
      return null;
    }
    return importFailureTask(answer.status, this.importHistory.attempts()[0]);
  });

  /** Every task of the applied answers, counted and tracking, in `sortTasks` order. */
  private readonly allTasks: Signal<NotifTask[]> = computed(() => {
    const today = this.today();
    const tasks: NotifTask[] = [];
    if (this.accountList()) {
      tasks.push(...billTasks({ dues: this.dues(), bill: event => this.bills.bill(event), today }));
    }
    const queue = this.queue();
    if (queue) {
      tasks.push(...scheduleTasks(queue, today));
    }
    const inbox = this.inboxData();
    if (inbox) {
      tasks.push(...reconTasks(inbox, this.progress()));
      const einvoice = einvoiceTask(inbox.counts);
      if (einvoice) {
        tasks.push(einvoice);
      }
    }
    const review = importReviewTask(this.latestImport());
    if (review) {
      tasks.push(review);
    }
    const dividends = this.dividendSplit();
    const batch = dividends ? dividendBatch(dividends.due) : null;
    if (batch) {
      tasks.push(batch);
    }
    const importFailure = this.importFailure();
    if (importFailure) {
      tasks.push(importFailure);
    }
    const cathay = this.cathayData();
    if (cathay) {
      tasks.push(...cathayTasks(cathay.pending, cathay.runs, cathay.now));
    }
    return sortTasks(tasks);
  });

  private readonly trackingBills: Signal<NotifTask[]> = computed(() =>
    this.allTasks().filter(task => task.tier === 'tracking' && task.from === 'bills'),
  );

  /**
   * Id of the newest started round; 0 before the first. Ids only grow. Read inside a write's own callback it still
   * names the pre-write round (the write triggers start the next round in an effect, later): a caller that must see
   * post-write data calls `invalidate()` and waits for `settledRound() >= ` the id it returns.
   */
  readonly round: Signal<number> = this.roundId.asReadonly();
  /** Id of the newest round whose every task source has settled (ok or failed); a superseded round never becomes it. */
  readonly settledRound: Signal<number> = this.settledId.asReadonly();
  /** A round is in flight (started, not settled); false before the first round. */
  readonly loading: Signal<boolean> = computed(() => this.round() > this.settledRound());
  /**
   * Some round has settled: the page's loading gate (before it, show 讀取中…, never 都處理好了). `summary()` is
   * `NO_TASKS` until then too, so `NO_TASKS` alone does not mean "nothing to do".
   */
  readonly loaded: Signal<boolean> = computed(() => this.settledRound() > 0);

  /** Counted tasks (追蹤中 excluded), snoozed ones included, in `sortTasks` order. */
  readonly countedTasks: Signal<readonly NotifTask[]> = computed(() =>
    this.allTasks().filter(task => COUNTED_TIERS.includes(task.tier)),
  );
  /** Ids of the counted tasks this device has snoozed and that have not woken (D4). */
  private readonly hiddenIds: Signal<ReadonlySet<string>> = computed(() => {
    const entries = this.snoozes.entries();
    const now = this.snoozeNow();
    return new Set(
      this.countedTasks()
        .filter(task => entries[task.id] !== undefined && !wakes(entries[task.id], task, now))
        .map(task => task.id),
    );
  });
  /**
   * Counted tasks (追蹤中 excluded) minus the snoozed ones, in `sortTasks` order: the list, the count, the chips and
   * the badge all follow it.
   */
  readonly tasks: Signal<readonly NotifTask[]> = computed(() => {
    const hidden = this.hiddenIds();
    return this.countedTasks().filter(task => !hidden.has(task.id));
  });
  /** Counted tasks hidden by a snooze on this device. */
  readonly snoozedCount: Signal<number> = computed(() => this.hiddenIds().size);
  /** When the first hidden task comes back by the clock (earliest `until`), or null with nothing hidden. */
  readonly snoozedEarliest: Signal<Date | null> = computed(() => {
    const entries = this.snoozes.entries();
    let earliest: number | null = null;
    for (const id of this.hiddenIds()) {
      const t = Date.parse(entries[id].until);
      earliest = earliest === null || t < earliest ? t : earliest;
    }
    return earliest === null ? null : new Date(earliest);
  });
  /** The debts row, the upcoming row, then the dividends waiting for their payment date; each absent when it has nothing. */
  readonly trackingRows: Signal<readonly NotifTrackingRow[]> = computed(() => {
    const counterparties = this.counterparties();
    const dividends = this.dividendSplit();
    return [
      counterparties ? debtsRow(counterparties) : null,
      upcomingRow(this.trackingBills()),
      dividends ? dividendsRow(dividends.waiting) : null,
    ].filter((row): row is NotifTrackingRow => row !== null);
  });
  /** The nearest tracking bill (the empty state's 下一件). */
  readonly nextUpcoming: Signal<NotifTask | null> = computed(() => this.trackingBills()[0] ?? null);
  /**
   * The count, its colour and label (not gated by the badges setting: `NavigationStateService` does that). `NO_TASKS`
   * until the first round has settled, so the entry points never show a partial count that then grows.
   */
  readonly summary: Signal<NotifSummary> = computed(() => (this.loaded() ? summarize(this.tasks()) : NO_TASKS));
  /** The task sources whose last applied answer failed, in `SOURCE_NAMES` key order. */
  readonly failedSources: Signal<readonly NotifSourceKey[]> = computed(() => {
    const status = this.status();
    return TASK_SOURCES.filter(key => status[key].state === 'failed');
  });
  /** `handledText()` of the last applied actions read; null hides the row (none, or the read failed). */
  readonly handled: Signal<string | null> = this.actionsText.asReadonly();
  /**
   * R25: the catch-all for ask items the inbox page cut off, or null. NOT a task and never counted: the API gives only a
   * global `counts.ask_items` (no per-statement total), so the missing items cannot be attributed to a statement and
   * each statement counts once from what was listed. The page shows it as a plain note.
   */
  readonly overflow: Signal<NotifTask | null> = computed(() => {
    const inbox = this.inboxData();
    return inbox ? reconOverflowTask(inboxMissing(inbox, INBOX_READ_LIMIT)) : null;
  });
  /** R25: the applied inbox answer left ask items out (`inboxMissing()` > 0 or unknown). */
  readonly truncated: Signal<boolean> = computed(() => {
    const inbox = this.inboxData();
    return !!inbox && inboxMissing(inbox, INBOX_READ_LIMIT) !== 0;
  });

  /** The 國泰 view of the last applied answers; null before the first one and while the source failed. */
  readonly cathay: Signal<CathayView | null> = computed(() => {
    const data = this.cathayData();
    return data ? cathayView(data.pending, data.runs, data.now) : null;
  });
  /** Pending dates the API did not list (0 when none or unread): the uncounted note. */
  readonly cathayMore: Signal<number> = computed(() => {
    const data = this.cathayData();
    return data ? cathayMore(data.pending) : 0;
  });

  /** Nav dots (R9); no badge carries a count. */
  readonly badges: Signal<Readonly<Record<string, NavBadge>>> = computed(() => {
    const badges: Record<string, NavBadge> = {};
    if (this.allTasks().some(task => task.from === 'bills' && task.tier === 'overdue')) {
      badges['accounting'] = { dot: true, tone: 'red', label: '有逾期的卡費' };
    }
    if (importReviewTask(this.latestImport())) {
      badges['accounting-timeline'] = { dot: true, tone: 'primary', label: '有待確認的匯入紀錄' };
    }
    const dividends = this.dividendSplit();
    const dividendsDot = dividends ? dividendsBadge(dividends.due) : null;
    if (dividendsDot) {
      badges['dividends'] = dividendsDot;
    }
    // O2 / P6: an import failure (the recalc chain, or this device's last commit until its next successful one) and a
    // bad 國泰 run share the red 匯入 dot. Pending 國泰 dates alone light it primary (as 股利); red wins over them.
    const cathay = this.cathay();
    const importLabels = [this.importFailure() ? '最近一次匯入失敗' : null, cathay?.dotLabel ?? null].filter(
      (label): label is string => label !== null,
    );
    if (importLabels.length > 0) {
      badges['import'] = { dot: true, tone: 'red', label: importLabels.join('、') };
    } else if (cathay?.pendingLabel) {
      badges['import'] = { dot: true, tone: 'primary', label: cathay.pendingLabel };
    }
    return badges;
  });

  constructor() {
    this.watchSnoozes();

    // The card statements outside a round: a new change round (a write; `BillingService` dedupes) and the dues
    // changing when the business-day calendar lands. A round's own call is made when its accounts answer lands.
    effect(() => {
      const accounts = this.accountList();
      const dues = this.dues();
      this.bills.generation();
      if (accounts) {
        untracked(() => this.bills.ensure(dues, this.today()));
      }
    });

    // An in-app write (entry, schedule, account, backup import, a reconciliation inbox action, a statement-page
    // reconcile / confirm) supersedes any round in flight: its answers may predate the write. Triggers bumped in the
    // same callback (an inbox answer bumps `inbox.changed` and then `entriesChanged`) coalesce into one effect run,
    // so one round.
    let firstRun = true;
    effect(() => {
      this.accounting.entriesChanged();
      this.accounting.accountsChanged();
      this.inbox.changed();
      this.statements.changed();
      if (firstRun) {
        firstRun = false;
        return;
      }
      if (this.started) {
        untracked(() => this.invalidate());
      }
    });
  }

  /** First round, then a refresh on every window focus and whenever the page becomes visible again. Idempotent. */
  start(): void {
    if (this.started) {
      return;
    }
    this.started = true;
    const view = this.document.defaultView;
    if (view) {
      const onFocus = () => this.refresh({ reason: 'focus' });
      view.addEventListener('focus', onFocus);
      this.destroyRef.onDestroy(() => view.removeEventListener('focus', onFocus));
    }
    // An installed PWA brought back from the background is not always focused.
    const onVisible = () => {
      if (this.document.visibilityState === 'visible') {
        this.refresh({ reason: 'focus' });
      }
    };
    this.document.addEventListener('visibilitychange', onVisible);
    this.destroyRef.onDestroy(() => this.document.removeEventListener('visibilitychange', onVisible));
    this.refresh({ reason: 'open' });
  }

  /**
   * A round unless one is in flight (round() > settledRound()): then nothing. 'focus' and 'timer' are also skipped
   * inside BADGE_REFRESH_MIN_MS of the last started round; 'open' inside OPEN_FRESH_MS of it (the startup round has
   * just begun). `invalidate()` is never skipped.
   */
  refresh(options: { readonly reason: NotifRefreshReason }): void {
    if (untracked(this.roundId) > untracked(this.settledId)) {
      return;
    }
    const window = options.reason === 'open' ? OPEN_FRESH_MS : BADGE_REFRESH_MIN_MS;
    if (this.lastStartedAt !== null && Date.now() - this.lastStartedAt < window) {
      return;
    }
    this.startRound();
  }

  /**
   * Starts a round now, superseding any round in flight (its answers are dropped), and returns that round's id: a
   * caller (a write that failed, 重試) waits for `settledRound() >= id` to know it sees data read after the call.
   */
  invalidate(): number {
    return this.startRound();
  }

  private startRound(): number {
    const round = untracked(this.roundId) + 1;
    this.roundStarted.next();
    this.roundId.set(round);
    this.lastStartedAt = Date.now();
    const now = this.clock();
    this.now.set(now);
    this.snoozeNow.set(now);
    const today = untracked(this.today);
    if (untracked(this.calendar) === null) {
      this.calendar.set(this.injector.get(BusinessCalendarService));
    }

    this.read(round, this.accounting.getAccounts(), accounts => {
      this.accountList.set(accounts);
      if (accounts) {
        this.readBills(round, today);
      } else {
        this.mark('bills', round, 'failed');
      }
    });
    this.read(round, this.accounting.getScheduleInstances({ queue: true }), queue => {
      this.queue.set(queue);
      this.mark('schedules', round, queue ? 'ok' : 'failed');
    });
    this.read(round, this.inbox.get({ limit: INBOX_READ_LIMIT }), inbox => {
      this.inboxData.set(inbox);
      this.mark('recon', round, inbox ? 'ok' : 'failed');
      if (inbox) {
        this.readProgress(round, inbox);
      }
    });
    // `getLatestImport` already answers a 404 (no import yet) with null: only an error is a failed read here.
    this.read(round, this.accounting.getLatestImport().pipe(map(run => ({ run }))), answer => {
      this.latestImport.set(answer?.run ?? null);
      this.mark('importReview', round, answer ? 'ok' : 'failed');
    });
    this.read(round, this.accounting.getCounterparties(), counterparties => {
      this.counterparties.set(counterparties);
      this.mark('debts', round, counterparties ? 'ok' : 'failed');
    });
    this.read(round, this.inbox.actions(HANDLED_LIMIT), actions => {
      this.actionsText.set(actions ? handledText(actions, now.toISOString(), HANDLED_LIMIT) : null);
    });
    // Every page through one read: its READ_TIMEOUT_MS is for the whole stream (C13). A scan the page cap stopped is
    // a failed read, never a partial all-clear (C12).
    // A superseded round's scan stops (its open page request is cancelled); its single-request reads simply land and
    // are dropped by `read()`.
    const dividendPages = allPages(
      offset => this.portfolio.getDividends({ limit: DIVIDEND_PAGE_SIZE, offset }).pipe(map(checkedPage)),
      DIVIDEND_PAGE_SIZE,
      DIVIDEND_MAX_PAGES,
    ).pipe(takeUntil(this.roundStarted));
    this.read(round, dividendPages, answer => {
      const complete = answer && !answer.capped ? { items: answer.items, today } : null;
      this.dividendData.set(complete);
      this.mark('dividends', round, complete ? 'ok' : 'failed');
    });
    this.read(round, this.portfolio.getRecalcStatus().pipe(map(status => ({ status }))), answer => {
      this.recalcAnswer.set(answer);
      this.mark('imports', round, answer ? 'ok' : 'failed');
    });
    // One source over two reads (P1): either failing fails both.
    const cathay = forkJoin({
      pending: this.portfolio.getCathayPending().pipe(map(checkedPending)),
      runs: this.portfolio.getCathayRuns().pipe(map(checkedRuns)),
    });
    this.read(round, cathay, answer => {
      this.cathayData.set(answer ? { ...answer, now } : null);
      this.mark('cathay', round, answer ? 'ok' : 'failed');
    });
    return round;
  }

  /**
   * The round's card statements, after its accounts answer: a forced re-read (`refresh`), or `ensure` when the change
   * round moved since the previous round's call (the statements effect has then already started those reads). The
   * source settles when every statement of the dues has landed, ok or failed.
   */
  private readBills(round: number, today: string): void {
    const dues = untracked(this.dues);
    const generation = untracked(this.bills.generation);
    const moved = this.lastBillsGeneration !== null && generation !== this.lastBillsGeneration;
    this.lastBillsGeneration = generation;
    const settled = moved ? this.bills.ensure(dues, today) : this.bills.refresh(dues, today);
    if (dues.length === 0) {
      this.mark('bills', round, 'ok');
      return;
    }
    // Raced against READ_TIMEOUT_MS: a statement still unanswered then fails the source for this round. The read
    // itself belongs to the shared BillingService and is not aborted; its late answer cannot change this round's
    // verdict (`mark` keeps the first answer of a source per round).
    from(settled)
      .pipe(
        timeout(READ_TIMEOUT_MS),
        // Only a failed read marks the source: a card whose bill cannot be computed (`failureReason` set, e.g. 幣別不同)
        // is skipped (it has no task), because a retry could never succeed.
        map(() => untracked(this.dues).some(event => this.bills.failed(event) && this.bills.failureReason(event) === null)),
        catchError(() => of(true)),
      )
      .subscribe(failed => this.mark('bills', round, failed ? 'failed' : 'ok'));
  }

  /** Statement progress of the accounts with ask items (silent: a failed list leaves its statements without a bar). */
  private readProgress(round: number, inbox: InboxOut): void {
    for (const accountId of askAccountIds(inbox)) {
      const statementIds = new Set(
        inbox.accounts.filter(account => account.id === accountId).flatMap(account => account.items.map(item => item.statement.id)),
      );
      this.read(round, this.statements.list(accountId), list => {
        this.progress.update(progress => {
          const next = new Map(progress);
          for (const id of statementIds) {
            next.delete(id);
          }
          for (const statement of list ?? []) {
            next.set(statement.id, reconProgress(statement));
          }
          return next;
        });
      });
    }
  }

  /**
   * Applies `request`'s answer (null on any error, and after READ_TIMEOUT_MS without one: the request is then
   * unsubscribed, so no late answer arrives) while `round` is the newest started round.
   */
  private read<T>(round: number, request: Observable<T>, apply: (value: T | null) => void): void {
    request.pipe(timeout(READ_TIMEOUT_MS), catchError(() => of(null))).subscribe(value => {
      if (round === untracked(this.roundId)) {
        apply(value);
      }
    });
  }

  /**
   * Snoozes `task` on this device until the option's time (D4); returns that time. The task leaves `tasks()` and the
   * count until then, or until it wakes early (`wakes`).
   */
  snooze(task: NotifTask, option: SnoozeOption): Date {
    const now = this.clock();
    const until = snoozeUntil(option, now);
    this.snoozes.set(task.id, snoozeEntry(task, until));
    this.snoozeNow.set(now);
    return until;
  }

  /** Brings one snoozed task back (the snooze toast's 復原). */
  unsnooze(id: string): void {
    this.snoozes.remove(id);
  }

  /** Cancels every snooze (現在顯示) and returns what was stored, for 復原. */
  showSnoozed(): SnoozeMap {
    const saved = this.snoozes.entries();
    this.snoozes.clear();
    return saved;
  }

  /** 復原 after 現在顯示: the cancelled snoozes come back (the wake rule still applies to each). */
  restoreSnoozes(saved: SnoozeMap): void {
    this.snoozes.restore(saved);
  }

  /**
   * The snooze upkeep: a woken or expired entry is removed (so it never re-hides once the wake condition passes); after
   * a settled round, an entry whose task is gone while its source read ok is removed (new work later starts fresh; a
   * failed source keeps it); one timer to the earliest end (at most a day ahead, re-armed) moves the snooze clock.
   */
  private watchSnoozes(): void {
    effect(() => {
      const entries = this.snoozes.entries();
      const now = this.snoozeNow();
      const ids = Object.keys(entries);
      if (ids.length === 0) return;
      const counted = new Map(this.countedTasks().map(task => [task.id, task]));
      const done = ids.filter(id => {
        const task = counted.get(id);
        return Date.parse(entries[id].until) <= now.getTime() || (task !== undefined && wakes(entries[id], task, now));
      });
      if (done.length) untracked(() => this.snoozes.remove(...done));
    });

    effect(() => {
      const round = this.settledRound();
      if (round === 0) return;
      untracked(() => {
        const status = this.status();
        const present = new Set(this.allTasks().map(task => task.id));
        const entries = this.snoozes.entries();
        const gone = Object.keys(entries).filter(id => {
          const source = status[entries[id].from];
          return source.round === round && source.state === 'ok' && !present.has(id);
        });
        if (gone.length) this.snoozes.remove(...gone);
      });
    });

    effect(onCleanup => {
      const entries = this.snoozes.entries();
      const now = this.snoozeNow().getTime();
      const ends = Object.values(entries).map(entry => Date.parse(entry.until)).filter(end => end > now);
      if (ends.length === 0) return;
      const delay = Math.min(Math.min(...ends) - now, SNOOZE_TIMER_MAX_MS);
      const handle = setTimeout(() => this.snoozeNow.set(this.clock()), delay);
      onCleanup(() => clearTimeout(handle));
    });
  }

  /**
   * Records a task source's answer for `round`; the round settles once all eight have answered for it. The first
   * answer of a source in a round is final (a late one, e.g. a statement landing after the bills timeout, is ignored).
   */
  private mark(source: NotifSourceKey, round: number, state: 'ok' | 'failed'): void {
    const current = untracked(this.status);
    if (round !== untracked(this.roundId) || current[source].round === round) {
      return;
    }
    const status = { ...current, [source]: { round, state } };
    this.status.set(status);
    if (TASK_SOURCES.every(key => status[key].round === round)) {
      this.settledId.set(round);
    }
  }
}
