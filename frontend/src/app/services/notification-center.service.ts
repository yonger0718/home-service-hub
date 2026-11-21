import { DestroyRef, Injectable, Injector, Signal, computed, effect, inject, signal, untracked } from '@angular/core';
import { DOCUMENT } from '@angular/common';
import { Observable, catchError, from, map, of, timeout } from 'rxjs';

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
import { debtsRow, handledText, importReviewTask, upcomingRow } from '../components/notifications/sources/ledger-misc';
import {
  INBOX_READ_LIMIT,
  askAccountIds,
  einvoiceTask,
  inboxMissing,
  reconOverflowTask,
  reconTasks,
} from '../components/notifications/sources/recon';
import { scheduleTasks } from '../components/notifications/sources/schedules';
import { NavBadge } from '../components/shell/navigation';
import { Counterparty, ImportRun, LedgerAccount, ScheduleInstance } from '../models/accounting.model';
import { RecalcStatus } from '../models/portfolio.model';
import { AccountingService } from './accounting.service';
import { BusinessCalendarService } from './business-calendar.service';
import { ImportHistoryService } from './import-history.service';
import { PortfolioService } from './portfolio.service';

/** A focus / visible / timer refresh starting sooner than this after the last started round is skipped. */
export const BADGE_REFRESH_MIN_MS = 30_000;

/**
 * A read of a round that has not answered after this long counts as failed for that round (the HTTP request is
 * aborted), and so does a round's card-statement wait: every round settles, `failureLine` names the source, and the
 * next open / focus / timer starts a fresh round (amends R26: an unanswered read would otherwise block `refresh()`).
 */
export const READ_TIMEOUT_MS = 20_000;

/** `open`: the 通知 page opened (not throttled); `focus`: window focus or page visible again; `timer`: a periodic caller. */
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
 * → card statements, schedule queue, reconciliation inbox, latest import, counterparties) plus the silent reads
 * (statement progress, handled actions, and on `refresh` rounds the recalc status). An answer is applied only while
 * its round is the newest started one, so a superseded round changes nothing and never settles. `settledRound()` is
 * the newest round whose five task sources — the card statements included — have all answered, ok or failed.
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

  private readonly roundId = signal(0);
  private readonly settledId = signal(0);
  private readonly status = signal<Readonly<Record<NotifSourceKey, SourceStatus>>>({
    bills: IDLE, schedules: IDLE, recon: IDLE, importReview: IDLE, debts: IDLE,
  });
  /** Today as of the newest started round. */
  private readonly today = signal(todayIso());

  /** The accepted accounts snapshot (null before the first answer and after a failed read). */
  private readonly accountList = signal<LedgerAccount[] | null>(null);
  private readonly queue = signal<ScheduleInstance[] | null>(null);
  private readonly inboxData = signal<InboxOut | null>(null);
  /** Per statement id: 已對上 k / n, from the statement lists of the accounts with ask items. */
  private readonly progress = signal<ReadonlyMap<number, ReconProgress>>(new Map());
  private readonly latestImport = signal<ImportRun | null>(null);
  private readonly counterparties = signal<Counterparty[] | null>(null);
  private readonly actionsText = signal<string | null>(null);
  private readonly recalc = signal<RecalcStatus | null>(null);

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
  private recalcGeneration = 0;

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
      const overflow = reconOverflowTask(inboxMissing(inbox, INBOX_READ_LIMIT));
      if (overflow) {
        tasks.push(overflow);
      }
      const einvoice = einvoiceTask(inbox.counts);
      if (einvoice) {
        tasks.push(einvoice);
      }
    }
    const review = importReviewTask(this.latestImport());
    if (review) {
      tasks.push(review);
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

  /** Counted tasks (追蹤中 excluded), in `sortTasks` order. */
  readonly tasks: Signal<readonly NotifTask[]> = computed(() =>
    this.allTasks().filter(task => COUNTED_TIERS.includes(task.tier)),
  );
  /** The debts row, then the upcoming row; either absent when it has nothing. */
  readonly trackingRows: Signal<readonly NotifTrackingRow[]> = computed(() => {
    const counterparties = this.counterparties();
    return [counterparties ? debtsRow(counterparties) : null, upcomingRow(this.trackingBills())].filter(
      (row): row is NotifTrackingRow => row !== null,
    );
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
  /** R25: the applied inbox answer left ask items out (`inboxMissing()` > 0 or unknown). */
  readonly truncated: Signal<boolean> = computed(() => {
    const inbox = this.inboxData();
    return !!inbox && inboxMissing(inbox, INBOX_READ_LIMIT) !== 0;
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
    const recalc = this.recalc();
    const recalcFailed = !!recalc && recalc.kind !== 'quotes' && recalc.state === 'failed';
    // The device's last commit failing raises the dot too; it clears on the next successful commit here.
    if (recalcFailed || this.importHistory.lastFailed()) {
      badges['import'] = { dot: true, tone: 'red', label: '最近一次匯入失敗' };
    }
    return badges;
  });

  constructor() {
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

    // An in-app write (entry, schedule, account, backup import, a reconciliation inbox action) supersedes any round
    // in flight: its answers may predate the write.
    let firstRun = true;
    effect(() => {
      this.accounting.entriesChanged();
      this.accounting.accountsChanged();
      this.inbox.changed();
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
   * inside BADGE_REFRESH_MIN_MS of the last started round; 'open' is not.
   */
  refresh(options: { readonly reason: NotifRefreshReason }): void {
    if (untracked(this.roundId) > untracked(this.settledId)) {
      return;
    }
    if (
      options.reason !== 'open' &&
      this.lastStartedAt !== null &&
      Date.now() - this.lastStartedAt < BADGE_REFRESH_MIN_MS
    ) {
      return;
    }
    this.startRound();
    this.readRecalc();
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
    this.roundId.set(round);
    this.lastStartedAt = Date.now();
    const today = todayIso();
    this.today.set(today);
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
      this.actionsText.set(actions ? handledText(actions, new Date().toISOString(), HANDLED_LIMIT) : null);
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
        map(() => untracked(this.dues).some(event => this.bills.failed(event))),
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

  /** The recalc status (the 匯入 dot), on `refresh` rounds only, guarded by its own generation. */
  private readRecalc(): void {
    const generation = ++this.recalcGeneration;
    this.portfolio
      .getRecalcStatus()
      .pipe(catchError(() => of(null)))
      .subscribe(status => {
        if (generation === this.recalcGeneration) {
          this.recalc.set(status);
        }
      });
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
   * Records a task source's answer for `round`; the round settles once all five have answered for it. The first
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
