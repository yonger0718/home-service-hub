import { MarketCode, PortfolioSummary } from '../../../models/portfolio.model';

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
