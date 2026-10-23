import { Transaction, TransactionType } from '../../../models/portfolio.model';
import { formatFxRate, formatNativeAmount, groupedInteger, isTwdCurrency, twdAmount } from '../money';

export { groupedInteger };

export interface TradeTotals {
  buyTotal: number;
  buyCount: number;
  sellTotal: number;
  sellCount: number;
  fees: number;
  turnover: number;
  feeShare: number | null;
  foreignWithoutFx: number;
}

function isTwd(t: Transaction): boolean {
  return isTwdCurrency(t.currency);
}

/** TWD per unit of the trade's currency: 1 for TWD (or no currency), `null` when a foreign row has no finite positive fx. */
function rateToTwd(t: Transaction): number | null {
  return twdAmount(1, t.fx_rate_to_twd, t.currency);
}

/** Buys: price × quantity + fee + tax (total paid). Sells: price × quantity − fee − tax (net received). In the trade's currency. */
export function allInTotal(t: Transaction): number {
  const gross = Number(t.price) * Number(t.quantity);
  const fee = Number(t.fee || 0);
  const tax = Number(t.tax || 0);
  return t.type === TransactionType.BUY ? gross + fee + tax : gross - fee - tax;
}

/**
 * Card figures in TWD all-in: foreign rows are converted by `fx_rate_to_twd`; a foreign row without a
 * finite positive fx is left out of every sum and count and counted once in `foreignWithoutFx`.
 */
export function tradeTotals(rows: readonly Transaction[]): TradeTotals {
  const totals: TradeTotals = { buyTotal: 0, buyCount: 0, sellTotal: 0, sellCount: 0, fees: 0, turnover: 0, feeShare: null, foreignWithoutFx: 0 };
  for (const t of rows) {
    const rate = rateToTwd(t);
    if (rate == null) {
      totals.foreignWithoutFx++;
      continue;
    }
    const allIn = allInTotal(t) * rate;
    if (t.type === TransactionType.BUY) {
      totals.buyTotal += allIn;
      totals.buyCount++;
    } else {
      totals.sellTotal += allIn;
      totals.sellCount++;
    }
    totals.turnover += Number(t.price) * Number(t.quantity) * rate;
    totals.fees += (Number(t.fee || 0) + Number(t.tax || 0)) * rate;
  }
  totals.feeShare = totals.turnover !== 0 ? totals.fees / totals.turnover : null;
  return totals;
}

/** 手續費＋交易稅 card footnote: '佔成交額 0.27%', or '佔成交額 —' when there is no turnover. */
export function feeShareLabel(feeShare: number | null): string {
  return `佔成交額 ${feeShare == null ? '—' : `${(feeShare * 100).toFixed(2)}%`}`;
}

/** 'YYYY-MM': a string's own date part (no Date parsing), a Date's local calendar month. */
export function monthKey(date: string | Date): string {
  if (typeof date === 'string') return date.slice(0, 7);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
}

/** '2026-10' → '2026 年 10 月'. */
export function monthLabel(key: string): string {
  const [year, month] = key.split('-');
  return `${year} 年 ${Number(month)} 月`;
}

/** Right-side month heading text: the month's TWD all-in totals, '買 x · 賣 y', a zero side omitted. */
export function monthHeading(key: string, rows: readonly Transaction[]): string {
  const totals = tradeTotals(rows.filter(t => t.trade_date != null && monthKey(t.trade_date) === key));
  const parts: string[] = [];
  if (totals.buyTotal !== 0) parts.push(`買 ${groupedInteger(totals.buyTotal)}`);
  if (totals.sellTotal !== 0) parts.push(`賣 ${groupedInteger(totals.sellTotal)}`);
  return parts.join(' · ');
}

/** '手續費 x' + ' · 交易稅 y' when tax > 0, in the trade's currency; foreign rows with an fx append ' · 匯率 z'. */
export function feeLine(t: Transaction): string {
  const amount = (value: number) => (isTwd(t) ? groupedInteger(value) : formatNativeAmount(value, t.currency));
  const fee = Number(t.fee || 0);
  const tax = Number(t.tax || 0);
  const parts = [`手續費 ${amount(fee)}`];
  if (tax > 0) parts.push(`交易稅 ${amount(tax)}`);
  if (!isTwd(t)) {
    const fx = rateToTwd(t);
    if (fx != null) parts.push(`匯率 ${formatFxRate(fx)}`);
  }
  return parts.join(' · ');
}

function slashDate(date: string): string {
  return date.slice(0, 10).replaceAll('-', '/');
}

/** Header period: 'YYYY/MM/DD – YYYY/MM/DD', 'YYYY/MM/DD 起', '至 YYYY/MM/DD', or '全部期間'. */
export function periodLabel(q: { date_from?: string | null; date_to?: string | null }): string {
  const from = q.date_from ? slashDate(q.date_from) : null;
  const to = q.date_to ? slashDate(q.date_to) : null;
  if (from && to) return `${from} – ${to}`;
  if (from) return `${from} 起`;
  if (to) return `至 ${to}`;
  return '全部期間';
}
