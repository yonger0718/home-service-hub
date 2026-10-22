import { EMPTY, Observable, defer, expand, reduce } from 'rxjs';

import { Dividend, MarketCode, Paged, PortfolioSummary, StockHolding } from '../../../models/portfolio.model';

export const MARKET_LABELS: Record<MarketCode, string> = { TW: '台股', US: '美股', LSE: '英股' };

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

/** Hero bar: stocks vs cash. Pending dividends are legend-only, never part of the bar. */
export function composition(
  summary: Pick<PortfolioSummary, 'total_market_value' | 'total_cash_twd' | 'total_pending_dividends_net'>,
): { stocks: number; cash: number; pending: number; stocksPct: number; cashPct: number } {
  const stocks = num(summary.total_market_value);
  const cash = num(summary.total_cash_twd);
  const pending = num(summary.total_pending_dividends_net);
  const total = stocks + cash;
  return {
    stocks,
    cash,
    pending,
    stocksPct: total > 0 ? (stocks / total) * 100 : 0,
    cashPct: total > 0 ? (cash / total) * 100 : 0,
  };
}

const TAIPEI_PARTS = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Asia/Taipei',
  weekday: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

function taipeiParts(date: Date): { weekday: string; hour: number; minute: number; time: string } {
  const parts: Record<string, string> = {};
  for (const part of TAIPEI_PARTS.formatToParts(date)) parts[part.type] = part.value;
  return {
    weekday: parts['weekday'],
    hour: Number(parts['hour']),
    minute: Number(parts['minute']),
    time: `${parts['hour']}:${parts['minute']}`,
  };
}

const WEEKDAYS = new Set(['Mon', 'Tue', 'Wed', 'Thu', 'Fri']);

/** Mon–Fri 09:00 ≤ t < 13:30 Taipei, half-open like the backend's is_tw_market_session. */
function inTwSession(now: Date): boolean {
  const { weekday, hour, minute } = taipeiParts(now);
  const minutes = hour * 60 + minute;
  return WEEKDAYS.has(weekday) && minutes >= 9 * 60 && minutes < 13 * 60 + 30;
}

/** `行情更新於 HH:mm · 盤中|台股收盤`: the time from the quote run, the session tag from `now`; null when unknown. */
export function quoteTimeLabel(finishedAtIso: string | null | undefined, now: Date): string | null {
  if (!finishedAtIso) return null;
  const finished = new Date(finishedAtIso);
  if (Number.isNaN(finished.getTime())) return null;
  return `行情更新於 ${taipeiParts(finished).time} · ${inTwSession(now) ? '盤中' : '台股收盤'}`;
}

/** 持股配置 colours for the five largest holdings, resolved by CSS at render time. */
export const ALLOCATION_SERIES: readonly string[] = ['--app-primary', '--app-accent', '--app-dividend', '--app-success', '--app-warning'];
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

/** `YYYY-MM-DD` without going through `new Date(string)`, which would parse it as UTC. */
function dayString(value: string | Date | null | undefined): string {
  if (value == null || value === '') return '';
  if (value instanceof Date) {
    const pad = (n: number) => `${n}`.padStart(2, '0');
    return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
  }
  return value.slice(0, 10);
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
        parts.push(`每股 ${Number(d.cash_dividend_per_share).toLocaleString('zh-TW', { maximumFractionDigits: 4 })}`);
      }
      if (present(d.quantity_at_record_date)) {
        parts.push(`${Number(d.quantity_at_record_date).toLocaleString('zh-TW')} 股`);
      }
      if (!paid) parts.push('除息');
      return { id: d.id, month: `${Number(month)}月`, day: day ?? '', name: nameFor(d), note: parts.join(' · '), amount: num(d.amount), currency: d.currency || 'TWD' };
    });
}

const MAX_DIVIDEND_PAGES = 50;

/** Every dividend: pages at offset 0, pageSize, … until `Paged.total` is covered (at most 50 pages). */
export function allDividendPages(
  fetch: (offset: number) => Observable<Paged<Dividend>>,
  pageSize = 100,
): Observable<Dividend[]> {
  return defer(() => {
    let pages = 0;
    let offset = 0;
    return fetch(0).pipe(
      expand(page => {
        pages++;
        offset += pageSize;
        const more = page.items.length > 0 && offset < page.total && pages < MAX_DIVIDEND_PAGES;
        return more ? fetch(offset) : EMPTY;
      }, 1),
      reduce((all, page) => all.concat(page.items), [] as Dividend[]),
    );
  });
}
