import { BreakdownResponse, BreakdownRow, BreakdownTotals, ReportCompare, ReportMetric } from '../../../models/reports.model';
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

/**
 * The previous period and the same period last year are one window (a calendar year, or any twelve whole months):
 * the page then shows a single comparison, named after last year, instead of the same figure twice.
 */
export function comparesCoincide(period: ReportPeriod): boolean {
  const previous = compareRange(period.from, period.to, 'previous');
  const lastYear = compareRange(period.from, period.to, 'last_year');
  return previous.from === lastYear.from && previous.to === lastYear.to;
}

/**
 * The comparison labels of the header and the tables: `比 9 月` / `比去年 10 月`, `比前一期` / `比去年同期`; `single`
 * when both windows coincide (`比去年` for a year), and then both labels name that one window.
 */
export function compareLabels(period: ReportPeriod): { previous: string; lastYear: string; single: boolean } {
  if (period.unit === 'month') {
    const month = monthOf(period.from);
    const [, m] = month.split('-').map(Number);
    return { previous: `比 ${shortMonth(shiftMonth(month, -1), month)}`, lastYear: `比去年 ${m} 月`, single: false };
  }
  const lastYear = period.unit === 'year' ? '比去年' : '比去年同期';
  return comparesCoincide(period) ? { previous: lastYear, lastYear, single: true } : { previous: '比前一期', lastYear, single: false };
}

/** `本月` / `上月` / `去年同月`, `本期` / `前一期` / `去年同期`; `今年` / `去年` alone for a year (table headings). */
export function periodNouns(period: ReportPeriod): { current: string; previous: string; lastYear: string; single: boolean } {
  if (period.unit === 'month') return { current: '本月', previous: '上月', lastYear: '去年同月', single: false };
  const current = period.unit === 'year' ? '今年' : '本期';
  const lastYear = period.unit === 'year' ? '去年' : '去年同期';
  return comparesCoincide(period) ? { current, previous: lastYear, lastYear, single: true } : { current, previous: '前一期', lastYear, single: false };
}

/** `10 月 對 9 月、對去年 10 月` for the comparison card title (`2026 年 對去年` when there is one comparison). */
export function compareTitle(period: ReportPeriod): string {
  if (period.unit === 'month') {
    const month = monthOf(period.from);
    const [, m] = month.split('-').map(Number);
    return `${m} 月 對 ${shortMonth(shiftMonth(month, -1), month)}、對去年 ${m} 月`;
  }
  const subject = period.unit === 'year' ? `${period.from.slice(0, 4)} 年` : '本期';
  const lastYear = period.unit === 'year' ? '對去年' : '對去年同期';
  return comparesCoincide(period) ? `${subject} ${lastYear}` : `${subject} 對前一期、${lastYear}`;
}

/**
 * The link to the timeline under a row (design D7). The timeline opens one month, so only a 月 period can promise the
 * row's N entries; any other period names the month that opens.
 */
export function drillText(period: ReportPeriod, count: number): string {
  return period.unit === 'month' ? `在記帳記錄看這 ${count} 筆 ›` : `在記帳記錄看 ${monthOf(period.from)}（期間第一個月） ›`;
}

/**
 * The 專案與預算 link to the rows without a project (#261). It carries no count: the project breakdown behind it is
 * lifetime, while the link opens one month — named, as in `drillText`, when the period is not a month.
 */
export function noProjectDrillText(period: ReportPeriod): string {
  return period.unit === 'month' ? '沒掛專案的支出 · 在記帳記錄看 ›' : `沒掛專案的支出 · 在記帳記錄看 ${monthOf(period.from)}（期間第一個月） ›`;
}

/** `YYYY-MM-DD` is the last day of its month. */
function isMonthEnd(iso: string): boolean {
  return Number(iso.slice(8, 10)) === daysInMonth(monthOf(iso));
}

/** `iso` moved by `months` (negative = back), the day clipped to the target month (29 Feb − 12 → 28 Feb; 30 Mar − 1 → 28 Feb). */
export function shiftDate(iso: string, months: number): string {
  const target = shiftMonth(monthOf(iso), months);
  return `${target}-${pad(Math.min(Number(iso.slice(8, 10)), daysInMonth(target)))}`;
}

/**
 * The comparison window of a range (design D2, same as the API's `compare_window`): `last_year` is the same dates a
 * year earlier (29 Feb → 28 Feb). `previous` is, for a range starting on the 1st, the months it touches moved back
 * (whole months before a whole-month range; the same days of the previous month for a running one, the day clipped
 * to the shorter month); any other range moves back by its own length in days.
 */
export function compareRange(from: string, to: string, compare: ReportCompare): { from: string; to: string } {
  if (compare === 'last_year') return { from: shiftDate(from, -12), to: shiftDate(to, -12) };
  if (from.endsWith('-01')) {
    const months = (Number(to.slice(0, 4)) - Number(from.slice(0, 4))) * 12 + Number(to.slice(5, 7)) - Number(from.slice(5, 7)) + 1;
    if (isMonthEnd(to)) {
      return { from: `${shiftMonth(monthOf(from), -months)}-01`, to: monthPeriod(shiftMonth(monthOf(from), -1)).to };
    }
    return { from: shiftDate(from, -months), to: shiftDate(to, -months) };
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

// ---- running periods (polish D1) ---------------------------------------------------------------------------------

/** Running: it has started and its last day (`to`, inclusive) is still ahead; on the last day it counts as finished. */
export function isRunning(period: ReportPeriod, today: string): boolean {
  return period.from <= today && today < period.to;
}

/** The range the figures cover: cut at `today` while the period runs, unless the whole period is asked for. */
export function viewRange(period: ReportPeriod, today: string, whole: boolean): { from: string; to: string } {
  return { from: period.from, to: isRunning(period, today) && !whole ? today : period.to };
}

/** `11/7`; `2025/12/1` when the year differs from `today`'s. */
export function md(iso: string, today: string): string {
  const [year, month, day] = iso.split('-').map(Number);
  return year === Number(today.slice(0, 4)) ? `${month}/${day}` : `${year}/${month}/${day}`;
}

/** `截至 11/7`. */
export function asOfText(today: string): string {
  return `截至 ${md(today, today)}`;
}

/**
 * The cut range's previous and last-year windows are one window: the cut range covers twelve whole months (a custom
 * 2025-11-01..2026-11-30 on 2026-10-31). The period itself is also checked (a running year): whichever coincides makes
 * the screen show one comparison.
 */
function cutCoincides(period: ReportPeriod, today: string): boolean {
  if (comparesCoincide(period)) return true;
  const to = isRunning(period, today) ? today : period.to;
  const previous = compareRange(period.from, to, 'previous');
  const lastYear = compareRange(period.from, to, 'last_year');
  return previous.from === lastYear.from && previous.to === lastYear.to;
}

/**
 * The column names while a period runs: `本月至今（11/1–11/7）` / `上月同期` / `去年同期` (one `去年同期` when the windows coincide).
 * `single` means the screen shows one comparison and reads it from the `compare=last_year` response.
 */
export function toDateNouns(period: ReportPeriod, today: string): { current: string; previous: string; lastYear: string; single: boolean } {
  const month = period.unit === 'month';
  const current = `${month ? '本月' : '本期'}至今（${md(period.from, today)}–${md(today, today)}）`;
  if (cutCoincides(period, today)) return { current, previous: '去年同期', lastYear: '去年同期', single: true };
  return { current, previous: month ? '上月同期' : '上期同期', lastYear: '去年同期', single: false };
}

/** The header comparison labels while a period runs: `比 10 月同期` / `比去年同期`; a year (or coinciding windows) has one.
 * `single` means the screen shows one comparison and reads it from the `compare=last_year` response.
 */
export function runningCompareLabels(period: ReportPeriod, today: string): { previous: string; lastYear: string; single: boolean } {
  if (period.unit === 'month') {
    const month = monthOf(period.from);
    return { previous: `比 ${shortMonth(shiftMonth(month, -1), month)}同期`, lastYear: '比去年同期', single: false };
  }
  if (period.unit === 'year' || cutCoincides(period, today)) return { previous: '比去年同期', lastYear: '比去年同期', single: true };
  return { previous: '比前一期同期', lastYear: '比去年同期', single: false };
}

/** The line about rows dated after today: `改回截至 11/7` once the whole period shows, else how many (and how much) are left out. */
export function afterTodayText(today: string, count: number, amount: number | null, whole: boolean): string {
  const date = md(today, today);
  if (whole) return `改回截至 ${date}`;
  return amount === null ? `另有 ${date} 之後已記的 ${count} 筆未計入` : `另有 ${date} 之後已記的 ${count} 筆 ${dollars(amount)} 未計入`;
}

/** The comparison card title while a period runs, after its columns: `本月至今 對 上月同期、對 去年同期` (`本期至今 對 去年同期` when single). */
export function runningCompareTitle(period: ReportPeriod, today: string): string {
  const nouns = toDateNouns(period, today);
  const current = `${period.unit === 'month' ? '本月' : '本期'}至今`;
  return nouns.single ? `${current} 對 ${nouns.lastYear}` : `${current} 對 ${nouns.previous}、對 ${nouns.lastYear}`;
}

/** A metric's figure in a response's `totals` (`count` → the entry count). */
export function metricTotal(totals: Partial<BreakdownTotals> | undefined, metric: ReportMetric): number {
  return metric === 'count' ? num(totals?.count) : num(totals?.[metric]);
}

/**
 * R3, the one 「另有」 / 「改回」 line of every screen: `改回截至 11/7` whenever the whole view is on (whatever N is, so the
 * view can always be left); else, with both reads, N = Σ rows.count (whole) − Σ rows.count (to date) and $X the metric
 * total difference (no amount for `count`); null when N is not above 0 or a read is missing.
 */
export function afterTodayLine(
  cut: BreakdownResponse | null | undefined,
  whole: BreakdownResponse | null | undefined,
  metric: ReportMetric,
  today: string,
  wholeOn: boolean,
): string | null {
  if (wholeOn) return afterTodayText(today, 0, null, true);
  if (!cut || !whole) return null;
  const rows = (response: BreakdownResponse) => response.rows.reduce((sum, row) => sum + row.count, 0);
  const later = rows(whole) - rows(cut);
  if (!(later > 0)) return null;
  const amount = metric === 'count' ? null : metricTotal(whole.totals, metric) - metricTotal(cut.totals, metric);
  return afterTodayText(today, later, amount, false);
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

/** `$1,250`, `−$50`: whole units with the dollar sign, the minus before it. */
export function dollars(value: string | number | null | undefined): string {
  const n = Math.round(num(value)) || 0;
  return n < 0 ? `−$${GROUPED.format(-n)}` : `$${GROUPED.format(n)}`;
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
  /** `none`: the base window is empty; `new` / `gone`: the key has no base / no current figure; `delta`: a plain change. */
  kind: 'none' | 'new' | 'gone' | 'delta';
}

/**
 * A spending comparison (design D6): ▲ when more was spent than in the base period, ▼ when less, `—` when equal
 * (whole units). `withPct` adds the change against the base in brackets, left out when the base is 0.
 */
export function change(current: number, base: number, withPct = false): Change {
  const diff = Math.round(current) - Math.round(base);
  if (diff === 0) return { text: '—', direction: 'same', kind: 'delta' };
  const arrow = diff > 0 ? '▲' : '▼';
  const pct = withPct && Math.round(base) !== 0 ? ` (${pct1(Math.abs(diff), Math.abs(base))})` : '';
  return { text: `${arrow} ${n0(Math.abs(diff))}${pct}`, direction: diff > 0 ? 'more' : 'less', kind: 'delta' };
}

/**
 * A change that knows its base (design D3): an empty comparison window reads `上期無資料` / `去年無資料` (no arrow), a key
 * with no base is `新增`, a key with no current figure is `— 停止`; otherwise the arrow change. The first three are muted.
 */
export function baseChange(current: number, base: number, windowEmpty: boolean, emptyText: '上期無資料' | '去年無資料', withPct = false): Change {
  if (windowEmpty) return { text: emptyText, direction: 'same', kind: 'none' };
  if (Math.round(base) === 0 && Math.round(current) > 0) return { text: '新增', direction: 'same', kind: 'new' };
  if (Math.round(current) === 0 && Math.round(base) > 0) return { text: '— 停止', direction: 'same', kind: 'gone' };
  return change(current, base, withPct);
}

/**
 * A period total against its base (polish F4): an empty comparison window reads `上期無資料` / `去年無資料`; otherwise the
 * arrow change, even from or to 0. A total is never 新增 or 停止: those are for keys (D3).
 */
export function totalChange(current: number, base: number, windowEmpty: boolean, emptyText: '上期無資料' | '去年無資料', withPct = false): Change {
  if (windowEmpty) return { text: emptyText, direction: 'same', kind: 'none' };
  return change(current, base, withPct);
}

/** The comparison window has no entries at all (`compare.totals.count` is 0). */
export function windowEmpty(response: BreakdownResponse | null | undefined): boolean {
  return response?.compare?.totals.count === 0;
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

export type BucketGranularity = 'month' | 'week' | 'day';

/**
 * The average month (design D4): from the first bucket with data through the last month that is over. `null` when that
 * leaves no month (a ledger younger than one completed month).
 */
export function monthAverage(buckets: readonly { start: string; value: number }[], today: string): { months: number; average: number } | null {
  const first = buckets.findIndex(bucket => bucket.value !== 0);
  if (first < 0) return null;
  const counted = buckets.slice(first).filter(bucket => monthPeriod(monthOf(bucket.start)).to <= today);
  if (counted.length === 0) return null;
  const total = counted.reduce((sum, bucket) => sum + bucket.value, 0);
  return { months: counted.length, average: Math.round(total / counted.length) };
}

/** A bucket key as text (design D7): `9 月` / `2025 年 9 月`, `10/5 週` / `2025/10/5 週`, `10/5` / `2025/10/5`. */
export function bucketText(key: string, granularity: BucketGranularity, spansYears: boolean): string {
  if (granularity === 'month') {
    const [year, month] = key.slice(0, 7).split('-').map(Number);
    return spansYears ? `${year} 年 ${month} 月` : `${month} 月`;
  }
  const [year, month, day] = key.split('-').map(Number);
  const date = spansYears ? `${year}/${month}/${day}` : `${month}/${day}`;
  return granularity === 'week' ? `${date} 週` : date;
}

/**
 * A trend / rank axis label (R1) in two lines: the month (`9 月`) and, only on the first bar and each January, the year
 * (`2025`) as a second, smaller line. Accessible names use `bucketText` in full.
 */
export function axisParts(start: string, index: number): { month: string; year: string | null } {
  return { month: bucketText(start, 'month', false), year: index === 0 || start.slice(5, 7) === '01' ? start.slice(0, 4) : null };
}

/**
 * 45° stripes (running-bucket hatch) clipped to a rectangle, as plain segments: each lies on a line x - y = c, with c
 * a multiple of `gap` so neighbouring bars share one stripe grid. Drawn as `<line>`s because a `url(#pattern)` paint
 * does not resolve under `<base href>` in WebKit. An empty rectangle has none.
 */
export function hatchLines(x: number, y: number, width: number, height: number, gap = 6): { x1: number; y1: number; x2: number; y2: number }[] {
  if (!(width > 0) || !(height > 0) || !(gap > 0)) return [];
  const lines: { x1: number; y1: number; x2: number; y2: number }[] = [];
  const first = Math.floor((x - (y + height)) / gap) * gap;
  for (let c = first; c <= x + width - y; c += gap) {
    const lo = Math.max(x, y + c);
    const hi = Math.min(x + width, y + height + c);
    if (hi - lo > 1e-9) lines.push({ x1: lo, y1: lo - c, x2: hi, y2: hi - c });
  }
  return lines;
}

/** A running bucket's 預估: the figure to date plus the typical bucket's share of the days still ahead. */
export interface RunningForecast {
  value: number;
  toDate: number;
  typical: number;
  /** Days after `today` in the bucket, and the bucket's days (both ends inclusive, clipped to the period). */
  after: number;
  days: number;
  /** The entries already recorded for the whole bucket (dated after today too) came out above the paced figure. */
  floored: boolean;
}

/**
 * 預估 for the running bucket `start..end` (inclusive, already clipped to the period): `toDate + typical × after / days`,
 * never below `recorded` (what the bucket holds with entries dated after today). Null without a positive typical figure
 * (a young ledger, a net metric) or without days ahead.
 */
export function runningForecast(input: { toDate: number; typical: number | null; start: string; end: string; today: string; recorded?: number | null }): RunningForecast | null {
  const { toDate, typical, start, end, today } = input;
  if (typical === null || !(typical > 0)) return null;
  const days = daysBetween(start, end) + 1;
  const after = Math.min(daysBetween(today, end), days);
  if (!(days > 0) || !(after > 0)) return null;
  const paced = toDate + (typical * after) / days;
  const recorded = input.recorded ?? 0;
  const floored = recorded > paced;
  return { value: Math.round(floored ? recorded : paced), toDate, typical: Math.round(typical), after, days, floored };
}

/** A tapped bar's figures, floated over the chart: anchored at `left` / `top` (% of the drawing), hanging off its anchor. */
export interface BarTip {
  title: string;
  rows: { label: string | null; value: string }[];
  left: number;
  top: number;
  /** `start`: the tip's left edge sits on the anchor (first bars); `end`: its right edge (last bars); else centred. */
  align: 'start' | 'center' | 'end';
}

/** Where a tip sits over a `width` × `height` drawing for a bar centred at `cx` whose highest mark is at `top`. */
export function tipPlacement(cx: number, top: number, width: number, height: number): Pick<BarTip, 'left' | 'top' | 'align'> {
  const align = cx < width * 0.2 ? 'start' : cx > width * 0.8 ? 'end' : 'center';
  return { left: Math.round((cx / width) * 1000) / 10, top: Math.round((Math.max(top, 0) / height) * 1000) / 10, align };
}

/**
 * The bucket has started and ends after `today` (a month: its last day; a week: start + 6). A day never runs. It ignores
 * the period's end: callers gate on `isRunning(period, today)`.
 */
export function runningBucket(key: string, granularity: BucketGranularity, today: string): boolean {
  if (granularity === 'day') return false;
  const start = granularity === 'month' ? `${key.slice(0, 7)}-01` : key;
  const end = granularity === 'month' ? `${key.slice(0, 7)}-${pad(daysInMonth(key.slice(0, 7)))}` : addDays(key, 6);
  return start <= today && today < end;
}

/** `進行中（至 11/7）`. */
export function runningSuffix(today: string): string {
  return `進行中（至 ${md(today, today)}）`;
}
