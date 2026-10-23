import { Transaction, TransactionType, brokerLabel } from '../../../models/portfolio.model';
import { TimelineRow } from '../../ui/timeline/timeline';
import { ALLOCATION_SERIES, abbrInk, holdingAbbr } from '../dashboard/dashboard-math';
import { formatNativeAmount, isTwdCurrency, twdAmount } from '../money';
import { allInTotal, feeLine, groupedInteger } from './transaction-math';

/** Typographic minus (U+2212), as in the spec and the canvas (`−110,407`). */
const MINUS = '−';

/**
 * Square fill for a symbol: a djb2-style hash of the symbol's UTF-16 code units mod the series length.
 * Deterministic and page-independent (the same symbol always gets the same fill); unlike the dashboard,
 * which assigns the series by allocation rank, so a symbol's colour may differ between the two pages.
 */
export function squareToken(symbol: string): string {
  let hash = 5381;
  for (let i = 0; i < symbol.length; i++) hash = (hash * 33 + symbol.charCodeAt(i)) >>> 0;
  return ALLOCATION_SERIES[hash % ALLOCATION_SERIES.length];
}

/** A foreign trade's all-in total in its own currency via the shared formatter: `2,385.00 USD`, `1,234.5000 GBp`. */
export function nativeTotal(t: Transaction): string {
  return formatNativeAmount(allInTotal(t), t.currency);
}

/** `5,000 股 @ 22.05` (TWD price 2–4 decimals, grouped); foreign `10 股 @ 238.40 USD`. */
export function quantityLine(t: Transaction): string {
  const qty = Number(t.quantity).toLocaleString('en-US');
  const price = isTwdCurrency(t.currency)
    ? Number(t.price).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 })
    : formatNativeAmount(t.price, t.currency);
  return `${qty} 股 @ ${price}`;
}

/**
 * Amount main line and note. Buys carry `−` (total paid), sells `+` (net received); an amount that shows as
 * zero has no sign. TWD: whole TWD + 總付出 / 淨收入. Foreign with a usable fx: whole TWD + the native total.
 * Foreign without one: the native total as the main line and `—` as the note.
 */
export function rowAmount(t: Transaction): { amount: string; amountNote: string } {
  // Every shown figure is digits, separators and an optional currency suffix (letters only), so "no 1–9" means zero.
  const signed = (text: string) => (/[1-9]/.test(text) ? `${t.type === TransactionType.BUY ? MINUS : '+'}${text}` : text);
  if (isTwdCurrency(t.currency)) {
    return { amount: signed(groupedInteger(allInTotal(t))), amountNote: t.type === TransactionType.BUY ? '總付出' : '淨收入' };
  }
  const twd = twdAmount(allInTotal(t), t.fx_rate_to_twd, t.currency);
  if (twd == null) return { amount: signed(nativeTotal(t)), amountNote: '—' };
  return { amount: signed(groupedInteger(twd)), amountNote: nativeTotal(t) };
}

/** One timeline row for a trade; `name` is the page's display name for the symbol. */
export function transactionRow(t: Transaction, name: string): TimelineRow {
  const isBuy = t.type === TransactionType.BUY;
  const token = squareToken(t.symbol);
  const broker = brokerLabel(t.broker);
  return {
    id: t.id,
    date: t.trade_date ?? '',
    side: isBuy ? 'buy' : 'sell',
    sideLabel: isBuy ? '買' : '賣',
    primary: `${name} ${t.symbol}`,
    square: { text: holdingAbbr(t.symbol, name), token, ink: abbrInk(token) },
    tags: t.is_day_trade === true ? ['當沖'] : undefined,
    meta: quantityLine(t),
    sub: feeLine(t),
    broker: broker || undefined,
    ...rowAmount(t),
    amountVariant: 'neutral',
  };
}
