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

/**
 * A period's amount fields (one per template line, in each line's currency) → API strings, or null when any field is
 * empty or invalid (`金額格式不正確`). A literal zero stays allowed: a line of a period may be 0 (e.g. no interest).
 */
export function parseAmountFields(texts: readonly string[], currencies: readonly string[]): string[] | null {
  const result: string[] = [];
  for (const [index, text] of texts.entries()) {
    const currency = currencies[index] ?? 'TWD';
    if (/^0+(\.0+)?$/.test(text.trim())) {
      result.push('0');
      continue;
    }
    const value = parseAmountText(text, currency);
    if (value === null) {
      return null;
    }
    result.push(amountString(value, currency));
  }
  return result;
}
