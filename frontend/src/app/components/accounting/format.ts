/** Currencies shown as whole units, the way cash amounts are written day to day. */
const WHOLE_UNIT_CURRENCIES = new Set(['TWD', 'JPY']);

const WHOLE_FORMAT = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });

const DECIMAL_FORMAT = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

/**
 * Format a ledger amount in its own currency: code prefix and grouping separators.
 * TWD and JPY are rounded to whole units (half away from zero); other currencies keep up to 2 decimals.
 * Display only — stored amounts keep 4 decimals.
 * Uses the code as text (not Intl currency style) because MOZE codes such as USDT are not ISO-4217.
 */
export function formatAmount(value: string | number | null | undefined, currency: string): string {
  const amount = Number(value ?? 0);
  const format = WHOLE_UNIT_CURRENCIES.has(currency) ? WHOLE_FORMAT : DECIMAL_FORMAT;
  return `${currency} ${format.format(Number.isFinite(amount) ? amount : 0)}`;
}

export function isNegative(value: string | number | null | undefined): boolean {
  return Number(value ?? 0) < 0;
}
