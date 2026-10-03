import { ENTRY_KIND_LABELS, LedgerEntry } from '../../models/accounting.model';

/** Currencies shown as whole units, the way cash amounts are written day to day. */
const WHOLE_UNIT_CURRENCIES = new Set(['TWD', 'JPY']);

const WHOLE_FORMAT = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });

const DECIMAL_FORMAT = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

const DECIMAL_ONE = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });

const CURRENCY_SYMBOLS: Record<string, string> = {
  TWD: '$',
  JPY: '¥',
  USD: 'US$',
  EUR: '€',
  GBP: '£',
  CNY: 'CN¥',
  HKD: 'HK$',
  KRW: '₩',
};

const MINUS = '−';

function toNumber(value: string | number | null | undefined): number {
  const amount = Number(value ?? 0);
  return Number.isFinite(amount) ? amount : 0;
}

function numberFormat(currency: string): Intl.NumberFormat {
  return WHOLE_UNIT_CURRENCIES.has(currency) ? WHOLE_FORMAT : DECIMAL_FORMAT;
}

/** Decimals shown and typed for a currency: 0 for TWD and JPY, 2 otherwise (display rounding only). */
export function currencyDecimals(currency: string): number {
  return WHOLE_UNIT_CURRENCIES.has(currency) ? 0 : 2;
}

/** `$` for TWD (the main currency), `¥` for JPY, `US$` …; unknown codes such as USDT as `USDT `. */
export function currencySymbol(currency: string): string {
  return CURRENCY_SYMBOLS[currency] ?? `${currency} `;
}

/**
 * Format a ledger amount in its own currency: code prefix and grouping separators.
 * TWD and JPY are rounded to whole units (half away from zero); other currencies keep up to 2 decimals.
 * Display only — stored amounts keep 4 decimals.
 * Uses the code as text (not Intl currency style) because MOZE codes such as USDT are not ISO-4217.
 */
export function formatAmount(value: string | number | null | undefined, currency: string): string {
  return `${currency} ${numberFormat(currency).format(toNumber(value))}`;
}

/** Grouped number with the currency's decimals and `−` for negatives, no symbol (amount tiles, inputs). */
export function formatNumber(value: string | number | null | undefined, currency: string): string {
  const amount = toNumber(value);
  const body = numberFormat(currency).format(Math.abs(amount));
  return amount < 0 && body !== '0' ? `${MINUS}${body}` : body;
}

/**
 * `−$1,166`, `¥5,390`, `US$12.5`, `USDT 3`: symbol, grouping, `−` only for negatives;
 * `{ sign: true }` adds `+` to positive amounts (zero after display rounding never gets a sign).
 */
export function formatMoney(
  value: string | number | null | undefined,
  currency: string,
  options: { sign?: boolean } = {},
): string {
  const amount = toNumber(value);
  const body = numberFormat(currency).format(Math.abs(amount));
  if (body === '0') {
    return `${currencySymbol(currency)}${body}`;
  }
  const sign = amount < 0 ? MINUS : options.sign ? '+' : '';
  return `${sign}${currencySymbol(currency)}${body}`;
}

/** `+$62,000` / `−$170`; zero (after display rounding) has no sign. */
export function formatSigned(value: string | number | null | undefined, currency: string): string {
  const amount = toNumber(value);
  const body = numberFormat(currency).format(Math.abs(amount));
  if (body === '0') {
    return `${currencySymbol(currency)}0`;
  }
  return `${amount < 0 ? MINUS : '+'}${currencySymbol(currency)}${body}`;
}

/**
 * Compact figure for tight spaces (calendar cells): whole units, no symbol, `−` for negatives;
 * from 10,000 `1.2萬`, from 100,000,000 `1.2億` (one decimal, trailing `.0` trimmed).
 * Every currency (JPY, USD …) is whole units here; `currency` keeps the call shape of `formatMoney`.
 */
export function compactMoney(value: string | number | null | undefined, currency: string): string {
  const raw = toNumber(value);
  const amount = Math.round(Math.abs(raw));
  const sign = raw < 0 && amount !== 0 ? MINUS : '';
  const scaled = (unit: number) => Math.round(amount / (unit / 10)) / 10;
  let body: string;
  if (amount >= 1e8 || scaled(1e4) >= 1e4) {
    body = `${DECIMAL_ONE.format(scaled(1e8))}億`;
  } else if (amount >= 1e4) {
    body = `${DECIMAL_ONE.format(scaled(1e4))}萬`;
  } else {
    body = WHOLE_FORMAT.format(amount);
  }
  return `${sign}${body}`;
}

export function isNegative(value: string | number | null | undefined): boolean {
  return Number(value ?? 0) < 0;
}

/** Category path for headers and pills: `購物 › 衣物`; the kind label when uncategorised. */
export function displayCategory(entry: Pick<LedgerEntry, 'category' | 'kind'>): string {
  return entry.category ? entry.category.split('/').join(' › ') : ENTRY_KIND_LABELS[entry.kind];
}

/** Row title: the entry's name, else its (sub-)category, else the kind label. */
export function displayTitle(entry: Pick<LedgerEntry, 'name' | 'category' | 'kind'>): string {
  const name = entry.name?.trim();
  if (name) {
    return name;
  }
  if (entry.category) {
    return entry.category.split('/').at(-1)!;
  }
  return ENTRY_KIND_LABELS[entry.kind];
}
