import { DestroyRef, Injectable, Signal, computed, effect, inject, signal, untracked } from '@angular/core';
import { DOCUMENT } from '@angular/common';
import { Observable, catchError, forkJoin, of } from 'rxjs';

import { BillingEvent, reminderDues } from '../components/accounting/billing/billing-math';
import { BillingService } from '../components/accounting/billing/billing.service';
import { countReminders } from '../components/accounting/billing/reminder-count';
import { todayIso } from '../components/accounting/dates';
import { importReviewCount } from '../components/accounting/import-review-count';
import { NavBadge } from '../components/shell/navigation';
import { Counterparty, ImportRun, ScheduleInstance } from '../models/accounting.model';
import { RecalcStatus } from '../models/portfolio.model';
import { AccountingService } from './accounting.service';
import { PortfolioService } from './portfolio.service';

/** A refresh (window focus, navigation) starting sooner than this after the previous one is skipped. */
export const BADGE_REFRESH_MIN_MS = 30_000;

interface ReminderInputs {
  dues: BillingEvent[];
  counterparties: Counterparty[];
  queue: ScheduleInstance[];
  today: string;
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
  private readonly document = inject(DOCUMENT);
  private readonly destroyRef = inject(DestroyRef);

  private readonly reminders = signal<ReminderInputs | null>(null);
  private readonly latestImport = signal<ImportRun | null>(null);
  private readonly recalc = signal<RecalcStatus | null>(null);
  private readonly generations: Record<Source, number> = { reminders: 0, import: 0, recalc: 0 };
  private started = false;
  private lastRefreshAt: number | null = null;

  readonly badges: Signal<Readonly<Record<string, NavBadge>>> = computed(() => {
    const badges: Record<string, NavBadge> = {};
    const reminders = this.reminders();
    if (reminders) {
      const count = countReminders({ ...reminders, bill: event => this.bills.bill(event) });
      if (count > 0) {
        badges['accounting-timeline'] = { count, label: `提醒中心 ${count} 項` };
      }
    }
    const review = importReviewCount(this.latestImport());
    if (review > 0) {
      badges['accounting'] = { count: review, label: `${review} 筆待確認` };
    }
    const recalc = this.recalc();
    if (recalc && recalc.kind !== 'quotes' && recalc.state === 'failed') {
      badges['import'] = { dot: true, label: '最近一次匯入失敗' };
    }
    return badges;
  });

  constructor() {
    // Statements of the latest accepted accounts snapshot, again on each change round (BillingService dedupes).
    effect(() => {
      const reminders = this.reminders();
      this.bills.generation();
      if (reminders) {
        untracked(() => this.bills.ensure(reminders.dues, reminders.today));
      }
    });
  }

  /** First load and a refresh on every window focus. Idempotent. */
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
    this.refresh();
  }

  refresh(): void {
    const now = Date.now();
    if (this.lastRefreshAt !== null && now - this.lastRefreshAt < BADGE_REFRESH_MIN_MS) {
      return;
    }
    this.lastRefreshAt = now;
    const today = todayIso();
    this.load(
      'reminders',
      forkJoin({
        accounts: this.accounting.getAccounts(),
        counterparties: this.accounting.getCounterparties(),
        queue: this.accounting.getScheduleInstances({ queue: true }),
      }),
      inputs =>
        this.reminders.set(
          inputs && {
            dues: reminderDues(inputs.accounts, today).filter(event => event.kind === 'due'),
            counterparties: inputs.counterparties,
            queue: inputs.queue,
            today,
          },
        ),
    );
    // `getLatestImport` already answers a 404 (no import yet) with null.
    this.load('import', this.accounting.getLatestImport(), run => this.latestImport.set(run));
    this.load('recalc', this.portfolio.getRecalcStatus(), status => this.recalc.set(status));
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
