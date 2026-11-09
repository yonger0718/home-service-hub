import { Injectable, Signal, computed, effect, inject, signal, untracked } from '@angular/core';

import { BreakdownRow, BreakdownSplit } from '../../../models/reports.model';
import { AccountingService } from '../../../services/accounting.service';
import { ReportsService } from '../../../services/reports.service';
import { monthRange } from '../dates';
import { InboxService } from '../inbox/inbox.service';

export type CalendarPartState = 'loading' | 'ready' | 'error';

export interface CalendarMonthData {
  /** `YYYY-MM-DD` → that day's expense splits by category id (`key`), largest first. */
  dots: ReadonlyMap<string, BreakdownSplit[]>;
  dotsState: CalendarPartState;
  /** Top five merchants of the month (rows with a null key dropped), as the API orders them. */
  merchants: BreakdownRow[];
  merchantsState: CalendarPartState;
  /** `issue_date` → number of inbox e-invoices awaiting a match that day. */
  invoiceDays: ReadonlyMap<string, number>;
  invoiceState: CalendarPartState;
}

export const EMPTY_CALENDAR_DATA: CalendarMonthData = {
  dots: new Map(),
  dotsState: 'loading',
  merchants: [],
  merchantsState: 'loading',
  invoiceDays: new Map(),
  invoiceState: 'loading',
};

type Part = 'dots' | 'merchants' | 'invoices';

/**
 * Per-month data of the 日曆 view, cached per month. An entry change clears the cache;
 * an inbox change re-reads only the e-invoice part. Each read carries a request id kept per
 * (part, month), so an answer that was superseded or cleared meanwhile is dropped.
 */
@Injectable({ providedIn: 'root' })
export class CalendarDataService {
  private readonly reports = inject(ReportsService);
  private readonly inbox = inject(InboxService);
  private readonly accounting = inject(AccountingService);

  /** The month the 日曆 view shows; null when no calendar is on screen (nothing is read then). */
  readonly month = signal<string | null>(null);

  private readonly cache = signal<ReadonlyMap<string, CalendarMonthData>>(new Map());
  private readonly latest = new Map<string, number>();
  private nextId = 0;
  private seenEntries: number | null = null;
  private seenInbox: number | null = null;

  /** The data for `month()`; EMPTY_CALENDAR_DATA while idle. */
  readonly data: Signal<CalendarMonthData> = computed(() => {
    const month = this.month();
    if (month === null) return EMPTY_CALENDAR_DATA;
    return this.cache().get(month) ?? EMPTY_CALENDAR_DATA;
  });

  constructor() {
    effect(() => {
      const month = this.month();
      const entries = this.accounting.entriesChanged();
      const inbox = this.inbox.changed();
      untracked(() => this.sync(month, entries, inbox));
    });
  }

  private sync(month: string | null, entries: number, inbox: number): void {
    const entriesMoved = this.seenEntries !== null && entries !== this.seenEntries;
    const inboxMoved = this.seenInbox !== null && inbox !== this.seenInbox;
    this.seenEntries = entries;
    this.seenInbox = inbox;

    if (entriesMoved) {
      this.latest.clear();
      this.cache.set(new Map());
    } else if (inboxMoved) {
      const next = new Map<string, CalendarMonthData>();
      for (const [key, rec] of this.cache()) {
        next.set(key, { ...rec, invoiceDays: new Map(), invoiceState: 'loading' });
        this.latest.delete(`invoices|${key}`);
      }
      this.cache.set(next);
    }

    if (month === null) return;
    const rec = this.cache().get(month);
    if (!rec) {
      this.patch(month, {});
      this.readDots(month);
      this.readMerchants(month);
      this.readInvoices(month);
    } else if (rec.invoiceState === 'loading' && !this.latest.has(`invoices|${month}`)) {
      // an inbox change reset this month's invoices while it was not shown
      this.readInvoices(month);
    }
  }

  private patch(month: string, change: Partial<CalendarMonthData>): void {
    this.cache.update((m) => {
      const next = new Map(m);
      next.set(month, { ...(m.get(month) ?? EMPTY_CALENDAR_DATA), ...change });
      return next;
    });
  }

  /** Starts a read; the returned check says whether its answer is still the current one. */
  private begin(part: Part, month: string): () => boolean {
    const id = ++this.nextId;
    const key = `${part}|${month}`;
    this.latest.set(key, id);
    return () => this.latest.get(key) === id && this.cache().has(month);
  }

  private readDots(month: string): void {
    const current = this.begin('dots', month);
    const { from, to } = monthRange(month);
    this.reports
      .getBreakdown({
        group_by: 'day',
        split_by: 'category',
        metric: 'expense',
        include_excluded_projects: true,
        date_from: from,
        date_to: to,
      })
      .subscribe({
        next: (res) => {
          if (!current()) return;
          const dots = new Map<string, BreakdownSplit[]>();
          for (const row of res.rows) {
            dots.set(
              row.key!,
              (row.splits ?? [])
                .filter((s) => s.key !== null && Number(s.amount) > 0)
                .sort((a, b) => Number(b.amount) - Number(a.amount)),
            );
          }
          this.patch(month, { dots, dotsState: 'ready' });
        },
        error: () => {
          if (current()) this.patch(month, { dots: new Map(), dotsState: 'error' });
        },
      });
  }

  private readMerchants(month: string): void {
    const current = this.begin('merchants', month);
    const { from, to } = monthRange(month);
    this.reports
      .getBreakdown({
        group_by: 'merchant',
        metric: 'expense',
        include_excluded_projects: true,
        date_from: from,
        date_to: to,
      })
      .subscribe({
        next: (res) => {
          if (!current()) return;
          const merchants = res.rows.filter((r) => r.key !== null).slice(0, 5);
          this.patch(month, { merchants, merchantsState: 'ready' });
        },
        error: () => {
          if (current()) this.patch(month, { merchants: [], merchantsState: 'error' });
        },
      });
  }

  private readInvoices(month: string): void {
    const current = this.begin('invoices', month);
    this.inbox.get().subscribe({
      next: (out) => {
        if (!current()) return;
        const invoiceDays = new Map<string, number>();
        for (const e of out.einvoices ?? []) {
          if (!e.issue_date.startsWith(month)) continue;
          invoiceDays.set(e.issue_date, (invoiceDays.get(e.issue_date) ?? 0) + 1);
        }
        this.patch(month, { invoiceDays, invoiceState: 'ready' });
      },
      error: () => {
        if (current()) this.patch(month, { invoiceDays: new Map(), invoiceState: 'error' });
      },
    });
  }
}
