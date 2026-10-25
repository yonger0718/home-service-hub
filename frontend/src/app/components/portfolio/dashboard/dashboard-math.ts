import { Observable, map } from 'rxjs';

import { Dividend, MarketCode, NetworthPoint, Paged, PortfolioSummary, StockHolding } from '../../../models/portfolio.model';
import { MARKET_LABELS } from '../market';
import { allPages } from '../paging';

export { MARKET_LABELS };

function num(value: number | string | null | undefined): number {
  const n = Number(value ?? 0);
  return Number.isFinite(n) ? n : 0;
}

/** 今日 pill: the day P&L against the assets before today; no percent when that base is not positive. */
export function todayChange(
  summary: Pick<PortfolioSummary, 'total_day_pnl' | 'total_assets_twd'>,
): { amount: number; percent: number | null } {
  const amount = num(summary.total_day_pnl);
  const base = num(summary.total_assets_twd) - amount;
  return { amount, percent: base > 0 ? (amount / base) * 100 : null };
}

/** Hero bar: stocks vs cash, each clamped at zero (a negative cash balance takes no share). Pending dividends are legend-only. */
export function composition(
  summary: Pick<PortfolioSummary, 'total_market_value' | 'total_cash_twd' | 'total_pending_dividends_net'>,
): { stocks: number; cash: number; pending: number; stocksPct: number; cashPct: number } {
  const stocks = num(summary.total_market_value);
  const cash = num(summary.total_cash_twd);
  const pending = num(summary.total_pending_dividends_net);
  const barStocks = Math.max(0, stocks);
  const barCash = Math.max(0, cash);
  const total = barStocks + barCash;
  return {
    stocks,
    cash,
    pending,
    stocksPct: total > 0 ? (barStocks / total) * 100 : 0,
    cashPct: total > 0 ? (barCash / total) * 100 : 0,
  };
}

const TAIPEI_PARTS = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Asia/Taipei',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  weekday: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

function taipeiParts(date: Date): { weekday: string; hour: number; minute: number; time: string; day: string; monthDay: string } {
  const parts: Record<string, string> = {};
  for (const part of TAIPEI_PARTS.formatToParts(date)) parts[part.type] = part.value;
  return {
    weekday: parts['weekday'],
    hour: Number(parts['hour']),
    minute: Number(parts['minute']),
    time: `${parts['hour']}:${parts['minute']}`,
    day: `${parts['year']}-${parts['month']}-${parts['day']}`,
    monthDay: `${parts['month']}/${parts['day']}`,
  };
}

const WEEKDAYS = new Set(['Mon', 'Tue', 'Wed', 'Thu', 'Fri']);

/** Mon–Fri 09:00 ≤ t < 13:30 Taipei, half-open like the backend's is_tw_market_session. */
function inTwSession(now: Date): boolean {
  const { weekday, hour, minute } = taipeiParts(now);
  const minutes = hour * 60 + minute;
  return WEEKDAYS.has(weekday) && minutes >= 9 * 60 && minutes < 13 * 60 + 30;
}

/**
 * `行情更新於 [MM/DD ]HH:mm · 盤中|台股收盤`: the time from the quote run (dated when it is not today in Taipei), the
 * session tag from `now`; null when unknown.
 */
export function quoteTimeLabel(finishedAtIso: string | null | undefined, now: Date): string | null {
  if (!finishedAtIso) return null;
  const finished = new Date(finishedAtIso);
  if (Number.isNaN(finished.getTime())) return null;
  const at = taipeiParts(finished);
  const when = at.day === taipeiParts(now).day ? at.time : `${at.monthDay} ${at.time}`;
  return `行情更新於 ${when} · ${inTwSession(now) ? '盤中' : '台股收盤'}`;
}

/** 持股配置 colours for the five largest holdings, resolved by CSS at render time. */
export const ALLOCATION_SERIES: readonly string[] = ['--app-primary', '--app-accent', '--app-dividend', '--app-success', '--app-warning'];

/** Fills that fail WCAG AA with white initials in light mode (4.09, 4.22, 3.58:1). */
const DARK_INK_TOKENS: ReadonlySet<string> = new Set(['--app-dividend', '--app-success', '--app-warning']);

/** Initials ink for an allocation fill. 其他 (`--app-text-muted`) keeps light ink: white is 4.8:1 there, `--app-text` only 3.7:1. */
export function abbrInk(token: string): 'light' | 'dark' {
  return DARK_INK_TOKENS.has(token) ? 'dark' : 'light';
}
const OTHER_TOKEN = '--app-text-muted';

export interface AllocationSlice {
  name: string;
  symbol: string;
  market: MarketCode | null;
  pct: number;
  token: string;
}

/** Share of market value: five largest holdings plus 其他, one decimal, summing to exactly 100.0. */
export function allocation(holdings: readonly StockHolding[]): AllocationSlice[] {
  const ranked = holdings
    .map(h => ({ name: h.name || h.symbol, symbol: h.symbol, market: h.market as MarketCode | null, value: num(h.market_value) }))
    .filter(h => h.value > 0)
    .sort((a, b) => b.value - a.value);
  const total = ranked.reduce((sum, h) => sum + h.value, 0);
  if (total <= 0) return [];

  const top = ranked.slice(0, ALLOCATION_SERIES.length).map((h, i) => ({ ...h, token: ALLOCATION_SERIES[i] }));
  const rest = ranked.slice(ALLOCATION_SERIES.length);
  if (rest.length) {
    top.push({ name: '其他', symbol: '', market: null, value: rest.reduce((sum, h) => sum + h.value, 0), token: OTHER_TOKEN });
  }

  // Largest remainder in tenths of a percent; ties go to the earlier (larger) slice.
  const raw = top.map(s => (s.value / total) * 1000);
  const tenths = raw.map(Math.floor);
  const order = raw.map((_, i) => i).sort((a, b) => raw[b] - tenths[b] - (raw[a] - tenths[a]) || a - b);
  const missing = 1000 - tenths.reduce((sum, t) => sum + t, 0);
  for (let k = 0; k < missing; k++) tenths[order[k]]++;

  return top.map((s, i) => ({ name: s.name, symbol: s.symbol, market: s.market, pct: tenths[i] / 10, token: s.token }));
}

/** Row square label: last two digits of a numeric symbol, else the first character of the name. */
export function holdingAbbr(symbol: string, name?: string | null): string {
  if (/^\d+$/.test(symbol)) return symbol.slice(-2);
  return Array.from(name || symbol)[0] ?? '';
}

export interface PendingRow {
  id: number;
  month: string;
  day: string;
  name: string;
  note: string;
  amount: number;
  currency: string;
}

/**
 * `YYYY-MM-DD` without going through `new Date(string)`, which would parse it as UTC. The API sends ISO strings; the
 * model's `Date` is never produced at runtime, so anything else counts as missing.
 */
function dayString(value: string | Date | null | undefined): string {
  return typeof value === 'string' ? value.slice(0, 10) : '';
}

function present(value: number | string | null | undefined): boolean {
  return value != null && value !== '' && Number.isFinite(Number(value));
}

/** 待收股利 list: pending rows with a payment date first (soonest first), then undated ones by ex date. */
export function pendingRows(dividends: readonly Dividend[], nameFor: (d: Dividend) => string, limit = 3): PendingRow[] {
  return dividends
    .filter(d => d.receipt_status === 'pending')
    .map(d => ({ d, paid: dayString(d.payment_date), ex: dayString(d.ex_dividend_date) }))
    .sort((a, b) => {
      if (!!a.paid !== !!b.paid) return a.paid ? -1 : 1;
      return (a.paid || a.ex).localeCompare(b.paid || b.ex) || a.d.id - b.d.id;
    })
    .slice(0, limit)
    .map(({ d, paid, ex }) => {
      const [, month, day] = (paid || ex).split('-');
      const parts: string[] = [];
      if (present(d.cash_dividend_per_share)) {
        const perShare = Number(d.cash_dividend_per_share).toLocaleString('zh-TW', { minimumFractionDigits: 2, maximumFractionDigits: 4 });
        parts.push(`每股 ${perShare}`);
      }
      if (present(d.quantity_at_record_date)) {
        parts.push(`${Number(d.quantity_at_record_date).toLocaleString('zh-TW')} 股`);
      }
      if (!paid) parts.push('除息');
      return { id: d.id, month: `${Number(month)}月`, day: day ?? '', name: nameFor(d), note: parts.join(' · '), amount: num(d.amount), currency: d.currency || 'TWD' };
    });
}

/** Every dividend: pages at offset 0, pageSize, … until `Paged.total` is covered (at most 50 pages). */
export function allDividendPages(
  fetch: (offset: number) => Observable<Paged<Dividend>>,
  pageSize = 100,
): Observable<Dividend[]> {
  return allPages(fetch, pageSize).pipe(map(result => result.items));
}

/** D8: a snapshot row with no stock data at all (all five stock columns 0, no XIRR). */
export function isCashOnlyPoint(point: NetworthPoint): boolean {
  return (
    num(point.total_market_value) === 0 &&
    num(point.total_cost) === 0 &&
    num(point.total_unrealized_pnl) === 0 &&
    num(point.total_dividends) === 0 &&
    num(point.total_realized_pnl) === 0 &&
    point.portfolio_xirr === null
  );
}

/** D8: cash-only runs after a holdings point take that point's five stock columns; 帳戶總值 = carried market + own cash. */
export function carryHoldings(points: readonly NetworthPoint[]): NetworthPoint[] {
  let last: NetworthPoint | null = null;
  return points.map(point => {
    if (!isCashOnlyPoint(point)) {
      last = point;
      return point;
    }
    if (!last) return point;
    const carried: NetworthPoint = last;
    return {
      ...point,
      total_market_value: carried.total_market_value,
      total_cost: carried.total_cost,
      total_unrealized_pnl: carried.total_unrealized_pnl,
      total_dividends: carried.total_dividends,
      total_realized_pnl: carried.total_realized_pnl,
      total_assets_twd: String(num(carried.total_market_value) + num(point.total_cash_twd)),
    };
  });
}

/** y-axis tick label in millions; decimals follow the tick step so narrow ranges stop repeating "3.4M". */
export function millionsTick(value: number, ticks: readonly { value: number }[]): string {
  const step = ticks.length > 1 ? Math.abs(ticks[1].value - ticks[0].value) : 100_000;
  const decimals = Math.min(3, Math.max(1, Math.ceil(-Math.log10(step / 1_000_000) - 1e-9)));
  return `${(value / 1_000_000).toFixed(decimals)}M`;
}
