import { HttpErrorResponse } from '@angular/common/http';

import { EntryScheduleLink, ScheduleDefinition, ScheduleIntervalUnit } from '../../models/accounting.model';
import { pad } from './accounting-ui';
import { addDays, slashDate } from './dates';
import { currencyDecimals, formatMoney } from './format';

/** Pure schedule helpers (design D43). The server stays the source of truth for instances; these only preview. */

export const IMPORT_RUNNING_TOAST = '匯入進行中，請稍後再試';
export const WEEKDAY_LABELS = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'];
const UNIT_WORDS: Record<ScheduleIntervalUnit, string> = { day: '天', week: '週', month: '月', year: '年' };

export interface ScheduleRule {
  interval_unit: ScheduleIntervalUnit;
  interval_n: number;
  anchor_date: string;
  day_of_month: number | null;
}

/** A 409 whose body names `import_running` (a backup import holds the advisory lock for a few seconds). */
export function isImportRunning(err: unknown): boolean {
  if (!(err instanceof HttpErrorResponse) || err.status !== 409) {
    return false;
  }
  const body = err.error as { message?: unknown; detail?: unknown } | null;
  return body?.message === 'import_running' || body?.detail === 'import_running';
}

function parts(iso: string): [number, number, number] {
  const [year, month, day] = iso.slice(0, 10).split('-').map(Number);
  return [year, month, day];
}

function lastDay(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/** `iso` moved by whole months, on `dayOfMonth` (default: its own day), clamped to the month's length. */
export function addMonthsIso(iso: string, months: number, dayOfMonth: number | null = null): string {
  const [year, month, day] = parts(iso);
  const index = year * 12 + (month - 1) + months;
  const nextYear = Math.floor(index / 12);
  const nextMonth = (index % 12) + 1;
  return `${nextYear}-${pad(nextMonth)}-${pad(Math.min(dayOfMonth ?? day, lastDay(nextYear, nextMonth)))}`;
}

/** Occurrence k of a rule, always computed from the anchor (a 31st clamps in February and returns in March). */
export function occurrenceDate(rule: ScheduleRule, k: number): string {
  const step = k * rule.interval_n;
  const anchorDay = parts(rule.anchor_date)[2];
  switch (rule.interval_unit) {
    case 'day':
      return addDays(rule.anchor_date, step);
    case 'week':
      return addDays(rule.anchor_date, 7 * step);
    case 'month':
      return addMonthsIso(rule.anchor_date, step, rule.day_of_month ?? anchorDay);
    default:
      return addMonthsIso(rule.anchor_date, 12 * step, rule.day_of_month ?? anchorDay);
  }
}

/** The first `count` occurrences strictly after `after` (from occurrence 0 when omitted). */
export function nextOccurrences(rule: ScheduleRule, count: number, after?: string): string[] {
  const dates: string[] = [];
  for (let k = 0; dates.length < count && k < 10000; k++) {
    const date = occurrenceDate(rule, k);
    if (after === undefined || date > after) {
      dates.push(date);
    }
  }
  return dates;
}

/** `每月`, `每 2 週`, `每天`, `每年`. */
export function intervalLabel(unit: ScheduleIntervalUnit, n: number): string {
  return n === 1 ? `每${UNIT_WORDS[unit]}` : `每 ${n} ${UNIT_WORDS[unit]}`;
}

/** The day part of a rule: `22號` (monthly), `星期一` (weekly), `10月22號` (yearly), '' (daily). */
export function ruleDayLabel(rule: ScheduleRule): string {
  const [year, month, day] = parts(rule.anchor_date);
  switch (rule.interval_unit) {
    case 'month':
      return `${rule.day_of_month ?? day}號`;
    case 'week':
      return WEEKDAY_LABELS[new Date(Date.UTC(year, month - 1, day)).getUTCDay()];
    case 'year':
      return `${month}月${rule.day_of_month ?? day}號`;
    default:
      return '';
  }
}

/** MOZE's 週期 footer: `週期：#1 / 無限期（每月 / 22號）`, `週期：#1 / 12（每月 / 22號）`. */
export function recurringFooter(rule: ScheduleRule, times: number | null): string {
  const label = intervalLabel(rule.interval_unit, rule.interval_n);
  const day = ruleDayLabel(rule);
  return `週期：#1 / ${times ?? '無限期'}（${day ? `${label} / ${day}` : label}）`;
}

/** floor(total ÷ times) in the currency's decimals; the remainder goes to the last period (分期餘額納入 末期). */
export function splitInstallment(total: number, times: number, currency: string): { perPeriod: number; last: number } {
  const factor = 10 ** currencyDecimals(currency);
  const perPeriod = Math.floor((total * factor) / times + 1e-9) / factor;
  const last = Math.round((total - perPeriod * (times - 1)) * factor) / factor;
  return { perPeriod, last };
}

/** MOZE's 分期 footer: `分期：#1 / 3（$10,000） 首次還款日將從 2026/11/03 開始進行（3 期）`. */
export function installmentFooter(total: number, times: number, firstDate: string, currency: string): string {
  return `分期：#1 / ${times}（${formatMoney(total, currency)}） 首次還款日將從 ${slashDate(firstDate)} 開始進行（${times} 期）`;
}

type RuleWithTimes = ScheduleRule & Pick<ScheduleDefinition, 'times'>;

/** Manage-sheet summary: `每月 9 號 · 36 期 · 自 2026/11/09`. */
export function ruleSummary(rule: RuleWithTimes): string {
  const label = intervalLabel(rule.interval_unit, rule.interval_n);
  const [, month, day] = parts(rule.anchor_date);
  const when =
    rule.interval_unit === 'month'
      ? `${label} ${rule.day_of_month ?? day} 號`
      : rule.interval_unit === 'week'
        ? `${label} ${ruleDayLabel(rule)}`
        : rule.interval_unit === 'year'
          ? `${label} ${month} 月 ${rule.day_of_month ?? day} 號`
          : label;
  return `${when} · ${rule.times === null ? '無限期' : `${rule.times} 期`} · 自 ${slashDate(rule.anchor_date)}`;
}

/** `已入帳 3 / 36`, or the interval (`每月`, `每 2 週`) for an unlimited schedule. */
export function scheduleProgress(rule: RuleWithTimes & Pick<ScheduleDefinition, 'posted_count'>): string {
  return rule.times === null
    ? intervalLabel(rule.interval_unit, rule.interval_n)
    : `已入帳 ${rule.posted_count} / ${rule.times}`;
}

/** `分期 #5/36`, `週期 #25` (unlimited), in MOZE's wording. */
export function schedulePill(link: Pick<EntryScheduleLink, 'kind' | 'seq' | 'times'>): string {
  return `${link.kind === 'installment' ? '分期' : '週期'} #${link.seq}${link.times === null ? '' : `/${link.times}`}`;
}
