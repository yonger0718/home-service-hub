import { isTwdCurrency } from '../portfolio/money';

/** A raw amount as the API sends it: a number, a numeric string, or missing. */
export type Amount = number | string | null | undefined;

export interface MoneyOptions {
  currency?: string | null;
  sign?: 'change';
  symbol?: boolean;
}

/** The minus sign (U+2212) for every negative amount and percentage. */
export const MINUS = '−';

const MISSING = '—';

/**
 * The one formatter for amounts and percentages, applying the D2 rule table (polish-layout-v2).
 * Not `components/accounting/format.ts#formatMoney`, the ledger's own, different function.
 * Rounding is Intl's half away from zero; a value that rounds to zero never carries a sign.
 */
export function formatMoney(value: Amount, options: MoneyOptions = {}): string {
  const n = toNumber(value);
  if (n === null) return MISSING;
  const twd = isTwdCurrency(options.currency);
  const digits = twd || options.currency?.toUpperCase() === 'JPY' ? 0 : 2;
  const text = body(Math.abs(n), digits);
  const sign = prefix(n, text, options.sign === 'change');
  if (twd) return `${sign}${options.symbol === false ? '' : '$'}${text}`;
  return `${sign}${text} ${options.currency}`;
}

/**
 * A percentage per the D2 rule table: 1 decimal, or 0 decimals once the 1-decimal value reaches 100;
 * `digits` overrides both. Same sign rule as `formatMoney`.
 */
export function formatPct(value: Amount, options: { sign?: 'change'; digits?: number } = {}): string {
  const n = toNumber(value);
  if (n === null) return MISSING;
  const abs = Math.abs(n);
  let text = body(abs, options.digits ?? 1);
  if (options.digits === undefined && plain(text) >= 100) text = body(abs, 0);
  return `${prefix(n, text, options.sign === 'change')}${text}%`;
}

function toNumber(value: Amount): number | null {
  if (value == null || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function body(abs: number, digits: number): string {
  return new Intl.NumberFormat('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(abs);
}

function plain(text: string): number {
  return Number(text.replace(/,/g, ''));
}

function prefix(value: number, text: string, change: boolean): string {
  if (plain(text) === 0) return '';
  if (value < 0) return MINUS;
  return change ? '+' : '';
}
