import { DestroyRef, Injectable, Injector, Signal, computed, effect, inject, signal, untracked } from '@angular/core';
import { DOCUMENT } from '@angular/common';
import { Observable, catchError, forkJoin, map, of } from 'rxjs';

import { BillingEvent, reminderDues } from '../components/accounting/billing/billing-math';
import { WEEKENDS_ONLY } from '../components/accounting/billing/business-days';
import { BillingService } from '../components/accounting/billing/billing.service';
import { countReminders } from '../components/accounting/billing/reminder-count';
import { todayIso } from '../components/accounting/dates';
import { importReviewCount } from '../components/accounting/import-review-count';
import { InboxService } from '../components/accounting/inbox/inbox.service';
import { NavBadge } from '../components/shell/navigation';
import { Counterparty, ImportRun, LedgerAccount, ScheduleInstance } from '../models/accounting.model';
import { RecalcStatus } from '../models/portfolio.model';
import { AccountingService } from './accounting.service';
import { BusinessCalendarService } from './business-calendar.service';
import { ImportHistoryService } from './import-history.service';
import { PortfolioService } from './portfolio.service';

/** A refresh (start, window focus, page visible again) starting sooner than this after the previous one is skipped. */
export const BADGE_REFRESH_MIN_MS = 30_000;

interface ReminderInputs {
  /** The accepted accounts snapshot; the dues derive from it and the business-day calendar (`dues`). */
  accounts: LedgerAccount[];
  counterparties: Counterparty[];
  queue: ScheduleInstance[];
  today: string;
  /** 待處理 reconciliation inbox cards (`counts.ask`), as the timeline's 🔔 counts them. */
  inboxAsk: number;
}

type Source = 'reminders' | 'import' | 'recalc';

/**
 * Nav badges read from existing endpoints. Each source fails on its own (its badge is hidden, never an error), and
 * only the latest started request of a source is applied: an older answer landing late is dropped.
 */
@Injectable({ providedIn: 'root' })
export class NavBadgeService {
  private readonly accounting = inject(AccountingService);
  private readonly portfolio = inject(PortfolioService);
  private readonly bills = inject(BillingService);
  private readonly injector = inject(Injector);
  private readonly inbox = inject(InboxService);
  private readonly importHistory = inject(ImportHistoryService);
  private readonly document = inject(DOCUMENT);
  private readonly destroyRef = inject(DestroyRef);

  private readonly reminders = signal<ReminderInputs | null>(null);
  /**
   * The business-day calendar, first asked for when the reminders load (not at construction: the calendar service
   * reads the server once when created, and this service is created with the shell). `WEEKENDS_ONLY` before that.
   */
  private readonly calendar = signal<BusinessCalendarService | null>(null);
  /**
   * The cards' current statements as due events, with the debit day from the calendar: recomputed when the calendar
   * lands, so the rounds `BillingService` keeps carry the right `payDate` (its round key includes it).
   */
  private readonly dues: Signal<BillingEvent[]> = computed(() => {
    const reminders = this.reminders();
    if (!reminders) {
      return [];
    }
    const calendar = this.calendar()?.calendar() ?? WEEKENDS_ONLY;
    return reminderDues(reminders.accounts, reminders.today, calendar).filter(event => event.kind === 'due');
  });
  private readonly latestImport = signal<ImportRun | null>(null);
  private readonly recalc = signal<RecalcStatus | null>(null);
  private readonly generations: Record<Source, number> = { reminders: 0, import: 0, recalc: 0 };
  private started = false;
  private lastRefreshAt: number | null = null;
  /**
   * Set when the reminders of an accepted refresh (start / focus / visible) land: the next run of the statements effect
   * re-reads them (`BillingService.refresh`) instead of reusing this change round's cached figures, once.
   */
  private rereadBills = false;

  readonly badges: Signal<Readonly<Record<string, NavBadge>>> = computed(() => {
    const badges: Record<string, NavBadge> = {};
    const reminders = this.reminders();
    if (reminders) {
      const count = countReminders({ ...reminders, dues: this.dues(), bill: event => this.bills.bill(event) });
      if (count > 0) {
        badges['accounting-timeline'] = { count, label: `提醒中心 ${count} 項` };
      }
    }
    const review = importReviewCount(this.latestImport());
    if (review > 0) {
      badges['accounting'] = { count: review, label: `${review} 筆待確認` };
    }
    const recalc = this.recalc();
    const recalcFailed = !!recalc && recalc.kind !== 'quotes' && recalc.state === 'failed';
    // The device's last commit failing raises the dot too; it clears on the next successful commit here.
    if (recalcFailed || this.importHistory.lastFailed()) {
      badges['import'] = { dot: true, label: '最近一次匯入失敗' };
    }
    return badges;
  });

  constructor() {
    // Statements of the latest accepted accounts snapshot, again on each change round (BillingService dedupes), and
    // re-read once after an accepted refresh: payments made on another device, or a failed read, then show up too.
    effect(() => {
      const reminders = this.reminders();
      const dues = this.dues();
      this.bills.generation();
      if (reminders) {
        untracked(() => {
          if (this.rereadBills) {
            this.rereadBills = false;
            this.bills.refresh(dues, reminders.today);
          } else {
            this.bills.ensure(dues, reminders.today);
          }
        });
      }
    });

    // An in-app write (entry, schedule, account, backup import) moves the reminders and the review count: reload them
    // at once, as the timeline's 🔔 does, so the bell and the sidebar agree. Not throttled; the generations keep it safe.
    let firstRun = true;
    effect(() => {
      this.accounting.entriesChanged();
      this.accounting.accountsChanged();
      if (firstRun) {
        firstRun = false;
        return;
      }
      if (this.started) {
        untracked(() => this.loadAccounting());
      }
    });
  }

  /** First load and a refresh on every window focus and whenever the page becomes visible again. Idempotent. */
  start(): void {
    if (this.started) {
      return;
    }
    this.started = true;
    const view = this.document.defaultView;
    if (view) {
      const onFocus = () => this.refresh();
      view.addEventListener('focus', onFocus);
      this.destroyRef.onDestroy(() => view.removeEventListener('focus', onFocus));
    }
    // An installed PWA brought back from the background is not always focused.
    const onVisible = () => {
      if (this.document.visibilityState === 'visible') {
        this.refresh();
      }
    };
    this.document.addEventListener('visibilitychange', onVisible);
    this.destroyRef.onDestroy(() => this.document.removeEventListener('visibilitychange', onVisible));
    this.refresh();
  }

  refresh(): void {
    const now = Date.now();
    if (this.lastRefreshAt !== null && now - this.lastRefreshAt < BADGE_REFRESH_MIN_MS) {
      return;
    }
    this.lastRefreshAt = now;
    this.loadAccounting(true);
    this.load('recalc', this.portfolio.getRecalcStatus(), status => this.recalc.set(status));
  }

  /** The reminders and the latest import (the accounting badges); `rereadBills`: an accepted refresh (see the field). */
  private loadAccounting(rereadBills = false): void {
    const today = todayIso();
    if (untracked(this.calendar) === null) {
      this.calendar.set(this.injector.get(BusinessCalendarService));
    }
    this.load(
      'reminders',
      forkJoin({
        accounts: this.accounting.getAccounts(),
        counterparties: this.accounting.getCounterparties(),
        queue: this.accounting.getScheduleInstances({ queue: true }),
        // A failed inbox read counts 0 rather than hiding the other reminders (the 🔔 swallows it the same way).
        inboxAsk: this.inbox.get({ limit: 1 }).pipe(
          map(data => data.counts.ask),
          catchError(() => of(0)),
        ),
      }),
      inputs => {
        if (inputs && rereadBills) {
          this.rereadBills = true;
        }
        this.reminders.set(
          inputs && {
            accounts: inputs.accounts,
            counterparties: inputs.counterparties,
            queue: inputs.queue,
            today,
            inboxAsk: inputs.inboxAsk,
          },
        );
      },
    );
    // `getLatestImport` already answers a 404 (no import yet) with null.
    this.load('import', this.accounting.getLatestImport(), run => this.latestImport.set(run));
  }

  /** Applies `source`'s answer (null on any error) unless a newer request of that source has started since. */
  private load<T>(source: Source, request: Observable<T>, apply: (value: T | null) => void): void {
    const generation = ++this.generations[source];
    request.pipe(catchError(() => of(null))).subscribe(value => {
      if (generation === this.generations[source]) {
        apply(value);
      }
    });
  }
}
