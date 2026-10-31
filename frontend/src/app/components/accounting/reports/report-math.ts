import { BreakdownRow, ReportCompare } from '../../../models/reports.model';
import { pad } from '../accounting-ui';
import { addDays, daysBetween } from '../dates';

/**
 * Pure rules of the 報表 page (openspec `add-accounting-reports`): the period control, the comparison copy,
 * the figures and the chart geometry. Nothing here touches the DOM or the API.
 */

// ---- period -----------------------------------------------------------------------------------------------------

export type PeriodUnit = 'month' | 'year' | 'custom';

/** An inclusive date range; `unit` decides the header copy and the ‹ › step. */
export interface ReportPeriod {
  unit: PeriodUnit;
  from: string;
  to: string;
}

const MONTH_RE = /^(\d{4})-(\d{2})$/;
const YEAR_RE = /^\d{4}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** Days in `YYYY-MM`. */
export function daysInMonth(month: string): number {
  const [year, m] = month.split('-').map(Number);
  return new Date(Date.UTC(year, m, 0)).getUTCDate();
}

/** `YYYY-MM` ± `delta` months. */
export function shiftMonth(month: string, delta: number): string {
  const [year, m] = month.split('-').map(Number);
  const date = new Date(Date.UTC(year, m - 1 + delta, 1));
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}`;
}

export function monthPeriod(month: string): ReportPeriod {
  return { unit: 'month', from: `${month}-01`, to: `${month}-${pad(daysInMonth(month))}` };
}

export function yearPeriod(year: number): ReportPeriod {
  return { unit: 'year', from: `${year}-01-01`, to: `${year}-12-31` };
}

export function customPeriod(from: string, to: string): ReportPeriod {
  return from <= to ? { unit: 'custom', from, to } : { unit: 'custom', from: to, to: from };
}

/** `YYYY-MM` of a date. */
export function monthOf(iso: string): string {
  return iso.slice(0, 7);
}

/**
 * The period from the page's query parameters (`month=2026-10`, `year=2026` or `from=…&to=…`); the current month when
 * nothing (or nonsense) is there.
 */
export function periodFromParams(params: { get(name: string): string | null }, today: string): ReportPeriod {
  const month = params.get('month');
  if (month && MONTH_RE.test(month)) return monthPeriod(month);
  const year = params.get('year');
  if (year && YEAR_RE.test(year)) return yearPeriod(Number(year));
  const from = params.get('from');
  const to = params.get('to');
  if (from && to && DATE_RE.test(from) && DATE_RE.test(to)) return customPeriod(from, to);
  return monthPeriod(monthOf(today));
}

/** The query parameters that name a period (the others are set to null so a switch clears them). */
export function periodParams(period: ReportPeriod): Record<string, string | null> {
  return {
    month: period.unit === 'month' ? monthOf(period.from) : null,
    year: period.unit === 'year' ? period.from.slice(0, 4) : null,
    from: period.unit === 'custom' ? period.from : null,
    to: period.unit === 'custom' ? period.to : null,
  };
}

/** ‹ › : the month or year before / after; a custom range moves by its own length. */
export function shiftPeriod(period: ReportPeriod, delta: 1 | -1): ReportPeriod {
  if (period.unit === 'month') return monthPeriod(shiftMonth(monthOf(period.from), delta));
  if (period.unit === 'year') return yearPeriod(Number(period.from.slice(0, 4)) + delta);
  const length = daysBetween(period.from, period.to) + 1;
  return customPeriod(addDays(period.from, delta * length), addDays(period.to, delta * length));
}

/** Switching the unit keeps the period's first month / year. */
export function periodWithUnit(period: ReportPeriod, unit: PeriodUnit): ReportPeriod {
  if (unit === period.unit) return period;
  if (unit === 'month') return monthPeriod(monthOf(period.from));
  if (unit === 'year') return yearPeriod(Number(period.from.slice(0, 4)));
  return customPeriod(period.from, period.to);
}

/** `2026 年 10 月`, `2026 年`, `2026/10/01 – 2026/11/15`. */
export function periodLabel(period: ReportPeriod): string {
  if (period.unit === 'month') {
    const [year, month] = period.from.split('-').map(Number);
    return `${year} 年 ${month} 月`;
  }
  if (period.unit === 'year') return `${period.from.slice(0, 4)} 年`;
  return `${period.from.replaceAll('-', '/')} – ${period.to.replaceAll('-', '/')}`;
}

/** A short name for a month (`9 月`, or `2025 年 10 月` when the year differs from `relativeTo`). */
function shortMonth(month: string, relativeTo: string): string {
  const [year, m] = month.split('-').map(Number);
  return year === Number(relativeTo.slice(0, 4)) ? `${m} 月` : `${year} 年 ${m} 月`;
}

/** The comparison labels of the header and the tables: `比 9 月` / `比去年 10 月`; `比去年` / `比前年` for a year. */
export function compareLabels(period: ReportPeriod): { previous: string; lastYear: string } {
  if (period.unit === 'month') {
    const month = monthOf(period.from);
    const [, m] = month.split('-').map(Number);
    return { previous: `比 ${shortMonth(shiftMonth(month, -1), month)}`, lastYear: `比去年 ${m} 月` };
  }
  if (period.unit === 'year') return { previous: '比去年', lastYear: '比前年' };
  return { previous: '比前一期', lastYear: '比去年同期' };
}

/** `本月` / `上月` / `去年同月`, `今年` / `去年` / `前年`, `本期` / `前一期` / `去年同期` (table headings). */
export function periodNouns(period: ReportPeriod): { current: string; previous: string; lastYear: string } {
  if (period.unit === 'month') return { current: '本月', previous: '上月', lastYear: '去年同月' };
  if (period.unit === 'year') return { current: '今年', previous: '去年', lastYear: '前年' };
  return { current: '本期', previous: '前一期', lastYear: '去年同期' };
}

/** `10 月 對 9 月、對去年 10 月` for the comparison card title. */
export function compareTitle(period: ReportPeriod): string {
  if (period.unit === 'month') {
    const month = monthOf(period.from);
    const [, m] = month.split('-').map(Number);
    return `${m} 月 對 ${shortMonth(shiftMonth(month, -1), month)}、對去年 ${m} 月`;
  }
  if (period.unit === 'year') return `${period.from.slice(0, 4)} 年 對去年、對前年`;
  return '本期 對前一期、對去年同期';
}

/** `YYYY-MM-DD` is the last day of its month. */
function isMonthEnd(iso: string): boolean {
  return Number(iso.slice(8, 10)) === daysInMonth(monthOf(iso));
}

/**
 * The comparison window of a range (design D1): `previous` is the same length immediately before — whole months when
 * the range is whole months — and `last_year` the same dates a year earlier (29 Feb → 28 Feb).
 */
export function compareRange(from: string, to: string, compare: ReportCompare): { from: string; to: string } {
  if (compare === 'last_year') {
    const back = (iso: string) => {
      const [year, month, day] = iso.split('-').map(Number);
      const target = `${year - 1}-${pad(month)}`;
      return `${target}-${pad(Math.min(day, daysInMonth(target)))}`;
    };
    return { from: back(from), to: back(to) };
  }
  if (from.endsWith('-01') && isMonthEnd(to)) {
    const months = (Number(to.slice(0, 4)) - Number(from.slice(0, 4))) * 12 + Number(to.slice(5, 7)) - Number(from.slice(5, 7)) + 1;
    const first = shiftMonth(monthOf(from), -months);
    return { from: `${first}-01`, to: monthPeriod(shiftMonth(monthOf(from), -1)).to };
  }
  const length = daysBetween(from, to) + 1;
  return { from: addDays(from, -length), to: addDays(to, -length) };
}

/**
 * Days of the period that have passed as of `today` (for 日均支出): the whole period once it is over, at least 1.
 * Returns the count and whether the period is still running.
 */
export function elapsedDays(period: ReportPeriod, today: string): { days: number; running: boolean } {
  const total = daysBetween(period.from, period.to) + 1;
  if (today >= period.to) return { days: total, running: false };
  if (today < period.from) return { days: 1, running: true };
  return { days: Math.max(1, daysBetween(period.from, today) + 1), running: true };
}

/** The twelve months ending at the period's last month (the trend's buckets). */
export function trendRange(period: ReportPeriod, months = 12): { from: string; to: string } {
  const last = monthOf(period.to);
  const lastPeriod = monthPeriod(last);
  return { from: `${shiftMonth(last, -(months - 1))}-01`, to: lastPeriod.to };
}

// ---- figures --------------------------------------------------------------------------------------------------

export function num(value: string | number | null | undefined): number {
  const n = Number(value ?? 0);
  return Number.isFinite(n) ? n : 0;
}

const GROUPED = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });

/** `41,890`: grouped whole units, `−` for negatives, no symbol (the tables and chart labels). */
export function n0(value: string | number | null | undefined): string {
  const n = Math.round(num(value));
  return n < 0 ? `−${GROUPED.format(-n)}` : GROUPED.format(n);
}

/** `23.5%`: one decimal; `—` when the base is 0. */
export function pct1(part: number, whole: number): string {
  if (!(whole > 0)) return '—';
  return `${(Math.round((part / whole) * 1000) / 10).toFixed(1)}%`;
}

export type ChangeDirection = 'more' | 'less' | 'same';

export interface Change {
  /** `▲ 2,282`, `▼ 2,160 (4.9%)`, `—` when equal. */
  text: string;
  direction: ChangeDirection;
}

/**
 * A spending comparison (design D6): ▲ when more was spent than in the base period, ▼ when less, `—` when equal
 * (whole units). `withPct` adds the change against the base in brackets, left out when the base is 0.
 */
export function change(current: number, base: number, withPct = false): Change {
  const diff = Math.round(current) - Math.round(base);
  if (diff === 0) return { text: '—', direction: 'same' };
  const arrow = diff > 0 ? '▲' : '▼';
  const pct = withPct && Math.round(base) !== 0 ? ` (${pct1(Math.abs(diff), Math.abs(base))})` : '';
  return { text: `${arrow} ${n0(Math.abs(diff))}${pct}`, direction: diff > 0 ? 'more' : 'less' };
}

/** Gross spend of an expense row: the amount before the refunds inside it. */
export function gross(row: Pick<BreakdownRow, 'amount' | 'refund'>): number {
  return num(row.amount) + num(row.refund);
}

// ---- charts -----------------------------------------------------------------------------------------------------

/** The first seven rows without a colour take `--app-series-1` … `-7`; the rest the 其他 tone. */
export function seriesColor(index: number): string {
  return index < 7 ? `var(--app-series-${index + 1})` : 'var(--app-category-other)';
}

export interface DonutArc {
  color: string;
  dasharray: string;
  dashoffset: string;
}

export const DONUT_RADIUS = 74;
const DONUT_GAP = 2;

/** Stroke arcs for a 200×200 donut (`r` = 74); each arc is `value / Σ` of the circumference minus a 2 px gap. */
export function donutArcs(values: readonly { value: number; color: string }[]): DonutArc[] {
  const total = values.reduce((sum, v) => sum + Math.max(v.value, 0), 0);
  const circumference = 2 * Math.PI * DONUT_RADIUS;
  if (!(total > 0)) return [];
  let offset = 0;
  return values.map(v => {
    const length = (Math.max(v.value, 0) / total) * circumference;
    const arc = {
      color: v.color,
      dasharray: `${Math.max(length - DONUT_GAP, 0.5).toFixed(1)} ${circumference.toFixed(1)}`,
      dashoffset: (offset === 0 ? 0 : -offset).toFixed(1),
    };
    offset += length;
    return arc;
  });
}

/** The axis top for a bar chart: the max rounded up to 1 / 2 / 2.5 / 5 × 10ⁿ (1,000 when everything is 0). */
export function niceCeiling(max: number): number {
  if (!(max > 0)) return 1000;
  const magnitude = 10 ** Math.floor(Math.log10(max));
  const unit = max / magnitude;
  const step = unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 2.5 ? 2.5 : unit <= 5 ? 5 : 10;
  return step * magnitude;
}

/** `50k`, `2.5k`, `800`, `0` for axis ticks. */
export function tickLabel(value: number): string {
  if (value === 0) return '0';
  if (value >= 1000) {
    const k = value / 1000;
    return `${Number.isInteger(k) ? k : k.toFixed(1)}k`;
  }
  return n0(value);
}

export interface BarGeometry {
  x: string;
  y: string;
  width: string;
  height: string;
  /** Centre x for the label. */
  cx: string;
}

/**
 * Bars in a plot area `left … left + width` wide and `top … top + height` tall; each slot keeps 60 % for the bar.
 * `max` is the value drawn at `top`.
 */
export function barGeometry(
  values: readonly number[],
  plot: { left: number; width: number; top: number; height: number },
  max: number,
): BarGeometry[] {
  const slot = plot.width / Math.max(values.length, 1);
  const bottom = plot.top + plot.height;
  return values.map((value, index) => {
    const x = plot.left + index * slot + slot * 0.2;
    const height = max > 0 ? (Math.max(value, 0) / max) * plot.height : 0;
    return {
      x: x.toFixed(1),
      y: (bottom - height).toFixed(1),
      width: (slot * 0.6).toFixed(1),
      height: height.toFixed(1),
      cx: (x + slot * 0.3).toFixed(1),
    };
  });
}

/** Y of a value in the same plot area. */
export function yOf(value: number, plot: { top: number; height: number }, max: number): number {
  const bottom = plot.top + plot.height;
  return max > 0 ? bottom - (Math.max(value, 0) / max) * plot.height : bottom;
}

/** `11月`, `1月` for a bucket start; the year is added when the bucket is a January and the chart spans two years. */
export function bucketLabel(start: string): string {
  const [, m] = start.split('-').map(Number);
  return `${m}月`;
}
