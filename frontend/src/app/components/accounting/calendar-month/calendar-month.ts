import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { DailySummaryDay } from '../../../models/accounting.model';
import { todayIso } from '../dates';
import { compactMoney, formatNumber } from '../format';
import { CalendarCell, buildCalendar } from './calendar-math';

const WEEKDAY_NAMES = ['日', '一', '二', '三', '四', '五', '六'];

interface CalendarCellView extends CalendarCell {
  /** Spend shown as a positive figure (a refund-only day shows a minus). */
  expenseText: string | null;
  incomeText: string | null;
  label: string;
}

/** Month grid for the timeline's 日曆 view: per-day spend / income from `GET /entries/summary/daily`. */
@Component({
  selector: 'app-calendar-month',
  standalone: true,
  templateUrl: './calendar-month.html',
  styleUrl: './calendar-month.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CalendarMonthComponent {
  /** `YYYY-MM`. */
  readonly month = input.required<string>();
  readonly days = input<DailySummaryDay[]>([]);
  readonly currency = input('TWD');
  /** `Preference.week_start`: 0 = Sunday first, 1 = Monday first. */
  readonly weekStart = input(0);
  readonly selected = input<string | null>(null);
  readonly today = input(todayIso());
  readonly daySelected = output<string>();

  readonly weekdays = computed(() => Array.from({ length: 7 }, (_, i) => WEEKDAY_NAMES[(this.weekStart() + i) % 7]));

  readonly rows = computed(() => {
    const currency = this.currency();
    return buildCalendar(this.month(), this.days(), this.weekStart()).map(row =>
      row.map((cell): CalendarCellView => {
        const spend = -cell.expense;
        const expenseText = cell.outside || spend === 0 ? null : compactMoney(spend, currency);
        const incomeText = cell.outside || cell.income === 0 ? null : compactMoney(cell.income, currency);
        const parts = [`${Number(cell.date.slice(5, 7))}月${cell.day}日`];
        if (expenseText !== null) {
          parts.push(`支出 ${formatNumber(spend, currency)}`);
        }
        if (incomeText !== null) {
          parts.push(`收入 ${formatNumber(cell.income, currency)}`);
        }
        return { ...cell, expenseText, incomeText, label: parts.join('，') };
      }),
    );
  });

  pick(cell: CalendarCell): void {
    if (!cell.outside) {
      this.daySelected.emit(cell.date);
    }
  }
}
