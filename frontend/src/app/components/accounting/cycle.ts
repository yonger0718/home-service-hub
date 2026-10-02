import { DueRule } from '../../models/accounting.model';
import { addDays, shortDate } from './dates';

/** Re-exported so callers of the cycle helpers need one import; the model owns the type. */
export type { DueRule };

export interface Period {
  start: string;
  end: string;
}

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

function lastDay(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

function shiftMonth(year: number, month: number, delta: number): [number, number] {
  const index = year * 12 + (month - 1) + delta;
  return [Math.floor(index / 12), (index % 12) + 1];
}

function dayIn(year: number, month: number, day: number): string {
  return `${year}-${pad(month)}-${pad(Math.min(day, lastDay(year, month)))}`;
}

function calendarMonth(year: number, month: number): Period {
  return { start: `${year}-${pad(month)}-01`, end: dayIn(year, month, 31) };
}

function cycleEndingIn(closingDay: number, year: number, month: number): Period {
  const [prevYear, prevMonth] = shiftMonth(year, month, -1);
  return { start: addDays(dayIn(prevYear, prevMonth, closingDay), 1), end: dayIn(year, month, closingDay) };
}

/** The statement cycle containing `today` (ISO). */
export function statementPeriod(closingDay: number | null, today: string): Period {
  const [year, month, day] = today.split('-').map(Number);
  if (closingDay === null) {
    return calendarMonth(year, month);
  }
  const close = Math.min(closingDay, lastDay(year, month));
  const [endYear, endMonth] = day <= close ? [year, month] : shiftMonth(year, month, 1);
  return cycleEndingIn(closingDay, endYear, endMonth);
}

/** The cycle `delta` cycles before (negative) or after (positive) `period`. */
export function shiftPeriod(closingDay: number | null, period: Period, delta: number): Period {
  const [year, month] = period.end.split('-').map(Number);
  const [nextYear, nextMonth] = shiftMonth(year, month, delta);
  return closingDay === null ? calendarMonth(nextYear, nextMonth) : cycleEndingIn(closingDay, nextYear, nextMonth);
}

export function periodLabel(period: Period): string {
  return `${shortDate(period.start)} – ${shortDate(period.end)}`;
}

/** Due date of the statement closing on `closingDate`; null without a rule. */
export function dueDate(closingDate: string, rule: DueRule | null, value: number | null): string | null {
  if (!rule || value === null || value === undefined) {
    return null;
  }
  if (rule === 'days_after_closing') {
    return addDays(closingDate, value);
  }
  const [year, month, day] = closingDate.split('-').map(Number);
  if (Math.min(value, lastDay(year, month)) > day) {
    return dayIn(year, month, value);
  }
  const [nextYear, nextMonth] = shiftMonth(year, month, 1);
  return dayIn(nextYear, nextMonth, value);
}
