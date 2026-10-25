import { Injectable, signal } from '@angular/core';

import { MonthSummary } from '../../../models/accounting.model';
import { currentMonth } from '../dates';
import { DueItem } from './due-items';

/** What the timeline publishes for the accounting layout's overview pane. */
@Injectable({ providedIn: 'root' })
export class AccountingOverviewService {
  readonly month = signal<string>(currentMonth());
  readonly summary = signal<MonthSummary | null>(null);
  readonly dueItems = signal<DueItem[]>([]);

  clear(): void {
    this.month.set(currentMonth());
    this.summary.set(null);
    this.dueItems.set([]);
  }
}
