import { RealizedPnlEvent, RealizedPnlQuery } from '../../../models/portfolio.model';
import { groupedInteger } from '../money';
import { periodLabel } from '../period';

/** The market toggle: 全部 / 台股 / 美股, where 美股 is every market but TW (LSE included). */
export type MarketFilter = 'ALL' | 'TW' | 'US';

export function matchesMarket(e: RealizedPnlEvent, m: MarketFilter): boolean {
  if (m === 'ALL') return true;
  return m === 'TW' ? e.market === 'TW' : e.market !== 'TW';
}

/** Card figures of a set of sells, whole TWD. A sell of exactly 0 counts in `count` but in neither gains nor losses. */
export interface RealizedTotals {
  total: number;
  count: number;
  gains: number;
  losses: number;
  month: number;
  monthCount: number;
  dayTrade: number;
  dayTradeCount: number;
  costs: number;
}

/** Decimal string from the API as a number; anything unparseable counts as 0. */
function amount(value: string | number | null | undefined): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function yearMonth(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

export function realizedTotals(rows: readonly RealizedPnlEvent[], today: Date): RealizedTotals {
  const thisMonth = yearMonth(today);
  const totals: RealizedTotals = { total: 0, count: 0, gains: 0, losses: 0, month: 0, monthCount: 0, dayTrade: 0, dayTradeCount: 0, costs: 0 };
  for (const e of rows) {
    const pnl = amount(e.realized_pnl);
    totals.total += pnl;
    totals.count++;
    if (pnl > 0) totals.gains++;
    else if (pnl < 0) totals.losses++;
    if (e.trade_date.slice(0, 7) === thisMonth) {
      totals.month += pnl;
      totals.monthCount++;
    }
    if (e.is_day_trade) {
      totals.dayTrade += pnl;
      totals.dayTradeCount++;
    }
    totals.costs += amount(e.fee) + amount(e.tax);
  }
  return totals;
}

/** One month column: `ratio` = |total| / the year's largest |total| (0 when that is 0). */
export interface MonthBar {
  month: number;
  total: number;
  count: number;
  ratio: number;
  future: boolean;
}

/** Twelve columns for `year`; rows of other years are left out; `future` only for months after today's in today's year. */
export function monthlyBars(rows: readonly RealizedPnlEvent[], year: number, today: Date): MonthBar[] {
  const prefix = `${year}-`;
  const bars = Array.from({ length: 12 }, (_, i) => ({ month: i + 1, total: 0, count: 0, ratio: 0, future: false }));
  for (const e of rows) {
    if (!e.trade_date.startsWith(prefix)) continue;
    const bar = bars[Number(e.trade_date.slice(5, 7)) - 1];
    if (!bar) continue;
    bar.total += amount(e.realized_pnl);
    bar.count++;
  }
  const max = Math.max(...bars.map(bar => Math.abs(bar.total)));
  const isCurrentYear = year === today.getFullYear();
  const currentMonth = today.getMonth() + 1;
  for (const bar of bars) {
    bar.ratio = max > 0 ? Math.abs(bar.total) / max : 0;
    bar.future = isCurrentYear && bar.month > currentMonth;
  }
  return bars;
}

/** The year the bars draw: the year filter, else the year of `date_to`, else today's. */
export function barsYear(q: RealizedPnlQuery, today: Date): number {
  if (q.year != null) return q.year;
  const toYear = q.date_to ? Number(q.date_to.slice(0, 4)) : NaN;
  return Number.isFinite(toYear) ? toYear : today.getFullYear();
}

/** Screen-reader text of the bars: the months whose whole-TWD total is non-zero, in order, signed (`−` U+2212). */
export function barsAriaText(bars: readonly MonthBar[], year: number): string {
  const parts = bars
    .filter(bar => Math.round(bar.total) !== 0)
    .map(bar => `${bar.month} 月 ${bar.total > 0 ? '+' : '−'}${groupedInteger(Math.abs(bar.total))}`);
  return `${year} 年每月已實現損益：${parts.length ? parts.join('、') : '無'}`;
}

/** Range text of the header and the first card: `{year} 年` for a year filter, else the period. */
export function rangeLabel(q: RealizedPnlQuery): string {
  return q.year != null ? `${q.year} 年` : periodLabel(q);
}

/** A client page offset past the set's end moves to the last page's start (0 for an empty set). */
export function clampOffset(offset: number, total: number, limit: number): number {
  if (offset < total) return offset;
  return Math.max(0, Math.floor((total - 1) / limit) * limit);
}
