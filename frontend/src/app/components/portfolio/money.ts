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
 * anything else with 2 decimals; the currency is a suffix (`7.80 USD`, `1.2345 GBp`). `—` when not numeric.
 */
export function formatNativeAmount(
  value: Amount,
  currency: string | null | undefined,
  formatTwd: (value: number) => string = formatTwdCurrency,
): string {
  if (value == null || value === '') return '—';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  if (!currency || currency === 'TWD') return formatTwd(numeric);
  const decimals = currency === 'GBp' ? 4 : 2;
  return `${numeric.toFixed(decimals)} ${currency}`;
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
