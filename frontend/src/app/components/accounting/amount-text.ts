import { evaluateAmount } from './amount-math';
import { currencyDecimals } from './format';

/** Evaluate an amount field (an expression) into a positive number in the currency's decimals; null when empty, invalid or not positive. */
export function parseAmountText(text: string, currency: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) {
    return null;
  }
  const result = evaluateAmount(trimmed, currencyDecimals(currency));
  return result.ok && result.value > 0 ? result.value : null;
}

/** Unsigned decimal string for the API, rounded to the currency's decimals, without trailing zeros. */
export function amountString(value: number, currency: string): string {
  const decimals = currencyDecimals(currency);
  const fixed = Math.abs(value).toFixed(decimals);
  return decimals > 0 ? fixed.replace(/\.?0+$/, '') : fixed;
}
