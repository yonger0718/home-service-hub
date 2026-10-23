import { Transaction, TransactionType, brokerLabel } from '../../../models/portfolio.model';
import { TimelineRow } from '../../ui/timeline/timeline';
import { formatNativeAmount, groupedInteger, isTwdCurrency, twdAmount } from '../money';
import { stockSquare } from '../square';
import { allInTotal, feeLine } from './transaction-math';

/** Typographic minus (U+2212), as in the spec and the canvas (`−110,407`). */
const MINUS = '−';

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
 * Amount main line and note. Buys carry `−` (total paid), sells `+` (net received); a sell whose fees exceed the
 * gross nets a payment and carries `−` too (`−1.00 USD`, never `+-1.00 USD`); an amount that shows as zero has no sign. TWD: whole TWD + 總付出 / 淨收入. Foreign with a usable fx: whole TWD + the native total.
 * Foreign without one: the native total as the main line and `—` as the note.
 */
export function rowAmount(t: Transaction): { amount: string; amountNote: string } {
  // Every shown figure is an optional leading '-', digits, separators and an optional currency suffix (letters
  // only), so "no 1–9" means zero. The formatted '-' of a negative total flips the side's sign.
  const signed = (text: string) => {
    const negative = text.startsWith('-');
    const magnitude = negative ? text.slice(1) : text;
    if (!/[1-9]/.test(magnitude)) return magnitude;
    return `${(t.type === TransactionType.BUY) !== negative ? MINUS : '+'}${magnitude}`;
  };
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
  const broker = brokerLabel(t.broker);
  return {
    id: t.id,
    date: t.trade_date ?? '',
    side: isBuy ? 'buy' : 'sell',
    sideLabel: isBuy ? '買' : '賣',
    primary: `${name} ${t.symbol}`,
    square: stockSquare(t.symbol, name),
    tags: t.is_day_trade === true ? ['當沖'] : undefined,
    meta: quantityLine(t),
    sub: feeLine(t),
    broker: broker || undefined,
    ...rowAmount(t),
    amountVariant: 'neutral',
  };
}
