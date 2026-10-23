import { CashTransaction, CashTransactionQuery, CashTransactionType, Dividend } from '../../../models/portfolio.model';
import { deductionLine, perShareLine } from '../dividend-list/dividend-rows';

/** One row of the 現金交易 table (Decision 11). */
export interface CashRow {
  id: number;
  /** 日期 `YYYY/MM/DD` from the `txn_date` slice. */
  date: string;
  /** PrimeIcons name without the `pi ` class (`pi-wallet`). */
  icon: string;
  title: string;
  subtitle: string | null;
  source: '手動' | '交易' | '股利' | '匯入';
  /** Signed amount, no currency prefix: `+20,000`, `−110,407`, `0`. */
  amount: string;
  amountClass: 'up' | 'down' | '';
  hasLegs: boolean;
}

export const CASH_TYPE_ICONS: Record<CashTransactionType, string> = {
  deposit: 'pi-arrow-down',
  withdraw: 'pi-arrow-up',
  trade: 'pi-arrow-right-arrow-left',
  buy_settle: 'pi-shopping-cart',
  sell_settle: 'pi-tag',
  fee: 'pi-receipt',
  tax: 'pi-building-columns',
  dividend_cash: 'pi-gift',
  interest_in: 'pi-percentage',
  margin_interest: 'pi-percentage',
  wire_fee: 'pi-send',
  fx_convert: 'pi-sync',
};

const TITLES: Record<CashTransactionType, string> = {
  deposit: '入金',
  withdraw: '出金',
  trade: '交割',
  buy_settle: '交割 · 買進',
  sell_settle: '交割 · 賣出',
  fee: '手續費',
  tax: '交易稅',
  dividend_cash: '股利',
  interest_in: '利息收入',
  margin_interest: '融資利息',
  wire_fee: '匯費',
  fx_convert: '換匯',
};

/** Types whose title carries the related symbol, and the separator before it (`交割 · 買進 00878`, `股利 · 2317`). */
const SYMBOL_SEPARATORS: Partial<Record<CashTransactionType, string>> = {
  trade: ' · ',
  buy_settle: ' ',
  sell_settle: ' ',
  dividend_cash: ' · ',
};
/** Trade legs whose subtitle is `{qty} 股 · {MM/DD} 成交`. */
const TRADE_LEGS = new Set<CashTransactionType>(['buy_settle', 'sell_settle', 'fee', 'tax']);

function fractionDigits(currency: string): number {
  const c = (currency || 'TWD').toUpperCase();
  return c === 'TWD' || c === 'JPY' ? 0 : 2;
}

/** The value rounded to the currency's digits; `null` when not numeric. */
function rounded(value: string | number, currency: string): { value: number; digits: number } | null {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  const digits = fractionDigits(currency);
  const scale = 10 ** digits;
  return { value: Math.round(n * scale) / scale || 0, digits };
}

/**
 * Cash amount / 餘額 text without a currency prefix: grouped, 0 dp for TWD / JPY and 2 dp otherwise,
 * negatives with `−` (U+2212); positives and zero unsigned (`438,360`, `−2,379.54`). `—` when not numeric.
 */
export function formatCash(value: string | number, currency: string): string {
  const r = rounded(value, currency);
  if (!r) return '—';
  const text = Math.abs(r.value).toLocaleString('en-US', { minimumFractionDigits: r.digits, maximumFractionDigits: r.digits });
  return r.value < 0 ? `−${text}` : text;
}

function signedAmount(value: string, currency: string): { amount: string; amountClass: CashRow['amountClass'] } {
  const r = rounded(value, currency);
  const text = formatCash(value, currency);
  if (!r || r.value === 0) return { amount: text, amountClass: '' };
  return r.value > 0 ? { amount: `+${text}`, amountClass: 'up' } : { amount: text, amountClass: 'down' };
}

function noteText(t: CashTransaction): string | null {
  const note = t.note?.trim();
  return note ? note : null;
}

/** `MM/DD` from a `YYYY-MM-DD` slice. */
function monthDay(date: string): string {
  return `${date.slice(5, 7)}/${date.slice(8, 10)}`;
}

/** `{qty} 股 · {MM/DD} 成交` with the parts present; null when neither is. */
function tradeLine(t: CashTransaction): string | null {
  const parts: string[] = [];
  if (t.related_quantity != null && t.related_quantity !== '') {
    const qty = Number(t.related_quantity);
    if (Number.isFinite(qty)) parts.push(`${qty.toLocaleString('en-US', { maximumFractionDigits: 4 })} 股`);
  }
  if (t.related_trade_date) parts.push(`${monthDay(t.related_trade_date)} 成交`);
  return parts.length ? parts.join(' · ') : null;
}

/** `0.48 × 20,000 · 扣匯費 10 · 補充保費 x` via the dividend list's helpers; null without dividend data. */
function dividendLine(t: CashTransaction): string | null {
  if (t.related_per_share == null && t.related_quantity == null) return null;
  const d = {
    currency: t.currency,
    cash_dividend_per_share: t.related_per_share == null ? undefined : Number(t.related_per_share),
    quantity_at_record_date: t.related_quantity == null ? undefined : Number(t.related_quantity),
    fee: Number(t.related_fee || 0),
    tax: Number(t.related_tax || 0),
  } as Dividend;
  // The canvas labels a dividend's handling fee 扣匯費 (PfCash.dc.html); the list helper says 手續費.
  const deductions = deductionLine(d)?.replace(/^手續費 /, '扣匯費 ') ?? null;
  return [perShareLine(d), deductions].filter(Boolean).join(' · ');
}

function subtitle(t: CashTransaction): string | null {
  if (t.type === 'trade') {
    const n = t.child_legs?.length ?? 0;
    if (n > 0) return t.related_trade_date ? `已合併買賣 ${n} 筆 · ${monthDay(t.related_trade_date)} 成交` : `已合併買賣 ${n} 筆`;
  } else if (TRADE_LEGS.has(t.type)) {
    const line = tradeLine(t);
    if (line) return line;
  } else if (t.type === 'dividend_cash') {
    const line = dividendLine(t);
    if (line) return line;
  }
  return noteText(t);
}

function sourceLabel(t: CashTransaction): CashRow['source'] {
  if (t.source === 'manual') return '手動';
  if (t.source === 'csv_import') return '匯入';
  return t.related_dividend_id != null ? '股利' : '交易';
}

export function cashRow(t: CashTransaction): CashRow {
  const separator = SYMBOL_SEPARATORS[t.type];
  const symbol = separator != null ? t.related_symbol?.trim() : null;
  const base = TITLES[t.type] ?? t.type;
  return {
    id: t.id,
    date: t.txn_date.slice(0, 10).replaceAll('-', '/'),
    icon: CASH_TYPE_ICONS[t.type] ?? 'pi-circle',
    title: symbol ? `${base}${separator}${symbol}` : base,
    subtitle: subtitle(t),
    source: sourceLabel(t),
    ...signedAmount(t.amount, t.currency),
    hasLegs: (t.child_legs?.length ?? 0) > 0,
  };
}

/**
 * Whether the 餘額 column can be computed (Decision 12): the first page, newest first, no date or type filter,
 * and no row dated after `today` (`YYYY-MM-DD` string compare). `merge_related` does not matter.
 */
export function balanceColumnShown(q: CashTransactionQuery, rows: readonly CashTransaction[], today: string): boolean {
  return (
    (q.offset ?? 0) === 0 &&
    (q.sort ?? 'txn_date:desc') === 'txn_date:desc' &&
    !q.date_from &&
    !q.date_to &&
    !q.type &&
    rows.every((r) => r.txn_date.slice(0, 10) <= today)
  );
}

/**
 * 餘額 after each row, newest first: `[0] = current`, `[i] = [i-1] − amount[i-1]` with each row's own amount
 * (a merged parent's net, never its legs). `null` when {@link balanceColumnShown} is false.
 */
export function runningBalances(
  rows: readonly CashTransaction[],
  current: string | number,
  q: CashTransactionQuery,
  today: string,
): number[] | null {
  if (!balanceColumnShown(q, rows, today)) return null;
  const balances: number[] = [];
  let balance = Number(current);
  for (let i = 0; i < rows.length; i++) {
    if (i > 0) balance -= Number(rows[i - 1].amount);
    balances.push(balance);
  }
  return balances;
}
