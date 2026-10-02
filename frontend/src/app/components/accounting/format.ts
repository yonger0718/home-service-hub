const NUMBER_FORMAT = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

/**
 * Format a ledger amount in its own currency: code prefix, grouping separators, up to 2 decimals.
 * Uses the code as text (not Intl currency style) because MOZE codes such as USDT are not ISO-4217.
 */
export function formatAmount(value: string | number | null | undefined, currency: string): string {
  const amount = Number(value ?? 0);
  return `${currency} ${NUMBER_FORMAT.format(Number.isFinite(amount) ? amount : 0)}`;
}

export function isNegative(value: string | number | null | undefined): boolean {
  return Number(value ?? 0) < 0;
}
