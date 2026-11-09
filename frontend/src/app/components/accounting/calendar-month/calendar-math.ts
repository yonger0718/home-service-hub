import { CategoryNode, DailySummaryDay } from '../../../models/accounting.model';
import { formatMoney } from '../format';
import { FALLBACK_COLOR } from '../timeline/category-distribution';
import { addDays } from '../dates';

const MINUS = '−';
const WEEKDAYS = ['週日', '週一', '週二', '週三', '週四', '週五', '週六'];

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

/** `0`, `−250`, `+71`, `−3.1k`, `+71.7k`, `−12k` (one decimal for |v| ≥ 1000, trailing `.0` dropped). */
export function compactNet(value: number): string {
  if (value === 0) return '0';
  const sign = value < 0 ? MINUS : '+';
  const magnitude = Math.abs(value);
  if (magnitude < 1000) return `${sign}${magnitude}`;
  const thousands = (magnitude / 1000).toFixed(1).replace(/\.0$/, '');
  return `${sign}${thousands}k`;
}

/** `0`, `−3,096`, `+71,700`: signed, grouped (en-US), no decimals. */
export function wideNet(value: number): string {
  if (value === 0) return '0';
  const sign = value < 0 ? MINUS : '+';
  return `${sign}${Math.abs(value).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

/** Spend intensity of a day against the month's largest spend, 0 when there is nothing to scale. */
export function heatRatio(expense: number, maxExpense: number): number {
  return expense < 0 && maxExpense > 0 ? Math.min(1, -expense / maxExpense) : 0;
}

/** The largest daily spend magnitude (positive) across the month; 0 when no day has spend. */
export function maxDailyExpense(days: DailySummaryDay[]): number {
  let max = 0;
  for (const day of days) {
    const expense = Number(day.expense);
    if (expense < 0 && -expense > max) max = -expense;
  }
  return max;
}

/** Category id → colour, inheriting the nearest ancestor colour and falling back to `FALLBACK_COLOR`. */
export function categoryColorMap(roots: CategoryNode[]): ReadonlyMap<number, string> {
  const colors = new Map<number, string>();
  const walk = (nodes: CategoryNode[], inherited: string): void => {
    for (const node of nodes) {
      const color = node.color ?? inherited;
      colors.set(node.id, color);
      if (node.children) walk(node.children, color);
    }
  };
  walk(roots, FALLBACK_COLOR);
  return colors;
}

/** Screen-reader name of a month cell, e.g. `「10 月 21 日 週三」，3 筆，支出 $3,096，發票待對帳`. */
export function cellAriaLabel(parts: {
  date: string;
  count: number;
  expense: number;
  income: number;
  currency: string;
  bill: 'due' | 'paid' | null;
  dueCount: number;
  invoices: number;
}): string {
  const [year, month, day] = parts.date.split('-').map(Number);
  const weekday = WEEKDAYS[new Date(Date.UTC(year, month - 1, day)).getUTCDay()];
  const items = [`「${month} 月 ${day} 日 ${weekday}」`];
  if (parts.count > 0) items.push(`${parts.count} 筆`);
  if (parts.expense < 0) items.push(`支出 ${formatMoney(-parts.expense, parts.currency)}`);
  if (parts.income > 0) items.push(`收入 ${formatMoney(parts.income, parts.currency)}`);
  if (parts.bill === 'due') items.push(`${parts.dueCount} 張卡繳費到期`);
  if (parts.bill === 'paid') items.push('已繳');
  if (parts.invoices > 0) items.push('發票待對帳');
  return items.join('，');
}

