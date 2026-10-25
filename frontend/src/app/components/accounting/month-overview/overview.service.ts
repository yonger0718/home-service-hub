import { Injectable, signal } from '@angular/core';

import { MonthSummary } from '../../../models/accounting.model';
import { currentMonth } from '../dates';
import { DueItem } from './due-items';

export type OverviewState = 'loading' | 'error' | 'ready';

/** What the timeline publishes for the accounting layout's overview pane. */
@Injectable({ providedIn: 'root' })
export class AccountingOverviewService {
  readonly month = signal<string>(currentMonth());
  readonly summary = signal<MonthSummary | null>(null);
  readonly dueItems = signal<DueItem[]>([]);
  /** Whether `summary` / `dueItems` are still being read, failed, or final (the empty copies belong to `ready` only). */
  readonly summaryState = signal<OverviewState>('loading');
  readonly dueState = signal<OverviewState>('loading');

  clear(): void {
    this.month.set(currentMonth());
    this.summary.set(null);
    this.dueItems.set([]);
    this.summaryState.set('loading');
    this.dueState.set('loading');
  }
}
