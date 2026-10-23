type Amount = number | string | null | undefined;

function formatTwdCurrency(value: number): string {
  return new Intl.NumberFormat('zh-TW', {
    style: 'currency',
    currency: 'TWD',
    minimumFractionDigits: 0,
  }).format(value);
}

/**
 * An amount in its own currency: TWD (or no currency) via `formatTwd`, GBp with 4 decimals,
 * anything else with 2 decimals, thousands grouped; the currency is a suffix (`2,385.00 USD`, `1,234.5000 GBp`). `—` when not numeric.
 */
export function formatNativeAmount(
  value: Amount,
  currency: string | null | undefined,
  formatTwd: (value: number) => string = formatTwdCurrency,
): string {
  if (value == null || value === '') return '—';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  if (isTwdCurrency(currency)) return formatTwd(numeric);
  const decimals = currency === 'GBp' ? 4 : 2;
  return `${numeric.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })} ${currency}`;
}

/** TWD when the currency is missing or any case of 'TWD'. */
export function isTwdCurrency(currency: string | null | undefined): boolean {
  return !currency || currency.toUpperCase() === 'TWD';
}

/**
 * The TWD value of an amount: itself when the currency is TWD (any case) or missing, else `value × fx`.
 * `null` when the value is not numeric or a foreign amount has no finite positive fx.
 */
export function twdAmount(value: Amount, fx: Amount, currency: string | null | undefined): number | null {
  if (value == null || value === '') return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  if (isTwdCurrency(currency)) return numeric;
  if (fx == null || fx === '') return null;
  const rate = Number(fx);
  return Number.isFinite(rate) && rate > 0 ? numeric * rate : null;
}

/** Whole number with thousands separators; a value that rounds to zero is `0`, never `-0`. */
export function groupedInteger(value: number): string {
  return (Math.round(value) || 0).toLocaleString('en-US', { maximumFractionDigits: 0 });
}

/** 匯率 text: 2–4 fraction digits (`30.80`, `31.2345`). */
export function formatFxRate(rate: number): string {
  return rate.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 });
}
