import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { RouterLink } from '@angular/router';

import { MonthSummary } from '../../../models/accounting.model';
import { BreakdownRow } from '../../../models/reports.model';
import type { CalendarPartState } from '../calendar-month/calendar-data.service';
import type { MonthDue, TodayCard } from '../calendar-month/calendar-pane.service';
import { monthLabel, slashDate } from '../dates';
import { formatMoney } from '../format';
import type { InboxEinvoice } from '../inbox/einvoice.model';
import { dayLabel } from '../timeline/timeline';
import { barSegments, categoryDistribution, distributionAriaText } from '../timeline/category-distribution';
import { DueItem } from './due-items';
import type { OverviewState } from './overview.service';

/**
 * The accounting layout's empty pane. 清單 (≥ 1280 px): the month's category distribution and the next dues (no links, D7).
 * `calendar`: the 日曆 view's 本月摘要 — 今天, distribution + 看報表, 花最多的商家 and 這個月要處理.
 */
@Component({
  selector: 'app-month-overview',
  imports: [RouterLink],
  templateUrl: './month-overview.html',
  styleUrl: './month-overview.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class MonthOverviewComponent {
  readonly month = input.required<string>();
  readonly summary = input<MonthSummary | null>(null);
  readonly dueItems = input<DueItem[]>([]);
  readonly summaryState = input<OverviewState>('ready');
  readonly dueState = input<OverviewState>('ready');

  /** 日曆 view's 本月摘要 instead of the 清單 overview. */
  readonly calendar = input(false);
  /** Today's card; `null` when today is not in the shown month. */
  readonly today = input<TodayCard | null>(null);
  /** Merchant ranking rows (null keys already filtered upstream). */
  readonly merchants = input<BreakdownRow[]>([]);
  readonly merchantsState = input<CalendarPartState>('loading');
  /** Invoices still to reconcile. */
  readonly invoices = input<InboxEinvoice[]>([]);
  /** Card due dates of the month (the layout may include a due that falls in the next month). */
  readonly dues = input<MonthDue[]>([]);
  /** A day to show (the 今天 card, a due row), as `YYYY-MM-DD`. */
  readonly pickDay = output<string>();
  /** An invoice row; the layout selects the day and resolves the target. */
  readonly openInvoice = output<InboxEinvoice>();

  protected readonly monthLabel = monthLabel;
  protected readonly distribution = computed(() => categoryDistribution(this.summary()));
  protected readonly bar = computed(() => barSegments(this.distribution()));
  protected readonly aria = computed(() => distributionAriaText(this.distribution()));

  protected readonly slashDate = slashDate;
  protected readonly formatMoney = formatMoney;
  protected readonly currency = computed(() => this.summary()?.currency ?? 'TWD');
  protected readonly topMerchants = computed(() => this.merchants().slice(0, 5));
  protected readonly todoEmpty = computed(() => !this.invoices().length && !this.dues().length);
  protected readonly sortedInvoices = computed(() => [...this.invoices()].sort((a, b) => a.issue_date.localeCompare(b.issue_date)));
  protected readonly sortedDues = computed(() => [...this.dues()].sort((a, b) => a.date.localeCompare(b.date)));

  /** `週X` of an ISO date (the timeline's `dayLabel` is `MM/DD 週X`). */
  protected weekday(date: string): string {
    return dayLabel(date).split(' ')[1];
  }

  protected abs(value: string): number {
    return Math.abs(Number(value));
  }
}
