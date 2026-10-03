import { DailySummaryDay } from '../../../models/accounting.model';
import { addDays } from '../dates';

export interface CalendarCell {
  date: string;
  day: number;
  /** A leading / trailing day of the adjacent month: shown greyed, never carries figures. */
  outside: boolean;
  /** Negative (spend net of refunds) in the main currency; 0 for days without counted rows. */
  expense: number;
  income: number;
  count: number;
}

export const CALENDAR_ROWS = 6;

/**
 * Month grid of 6 rows × 7 columns. `weekStart` is `Preference.week_start` (0 = Sunday, 1 = Monday first).
 * The first row starts on the week-start day on or before day 1 (so a month beginning on that day has no
 * leading cells); every cell after the month's last day is a trailing next-month cell, always 42 in all.
 */
export function buildCalendar(month: string, days: DailySummaryDay[], weekStart: number): CalendarCell[][] {
  const [year, monthNumber] = month.split('-').map(Number);
  const firstWeekday = new Date(Date.UTC(year, monthNumber - 1, 1)).getUTCDay();
  const lead = (firstWeekday - weekStart + 7) % 7;
  const start = addDays(`${month}-01`, -lead);
  const byDate = new Map(days.map(day => [day.date, day]));

  const rows: CalendarCell[][] = [];
  for (let row = 0; row < CALENDAR_ROWS; row++) {
    const cells: CalendarCell[] = [];
    for (let column = 0; column < 7; column++) {
      const date = addDays(start, row * 7 + column);
      const outside = date.slice(0, 7) !== month;
      const figures = outside ? undefined : byDate.get(date);
      cells.push({
        date,
        day: Number(date.slice(8, 10)),
        outside,
        expense: figures ? Number(figures.expense) : 0,
        income: figures ? Number(figures.income) : 0,
        count: figures?.count ?? 0,
      });
    }
    rows.push(cells);
  }
  return rows;
}
