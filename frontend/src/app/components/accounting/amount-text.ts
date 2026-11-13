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

/** Ledger precision: amounts that settle a debt are kept at four decimals whatever the currency's display decimals. */
const LEDGER_DECIMALS = 4;

const SCALE = 10_000n;

/** Plain decimal literal (as the keypad normalises it: full-width digits and dot, commas and spaces dropped). */
function literalParts(text: string): { whole: string; fraction: string } | null {
  let out = '';
  for (const ch of text) {
    const code = ch.charCodeAt(0);
    out += code >= 0xff10 && code <= 0xff19 ? String.fromCharCode(code - 0xfee0) : ch === '．' ? '.' : ch === '，' ? ',' : ch;
  }
  const match = /^(\d*)(?:\.(\d*))?$/.exec(out.replace(/[\s,]/g, ''));
  if (!match || (match[1] === '' && !match[2])) {
    return null;
  }
  return { whole: match[1] || '0', fraction: match[2] ?? '' };
}

/** Scaled units (x10^4) to a normalised decimal string without trailing zeros. */
function unitsToString(units: bigint): string {
  const whole = units / SCALE;
  const fraction = (units % SCALE).toString().padStart(4, '0').replace(/0+$/, '');
  return fraction ? `${whole}.${fraction}` : `${whole}`;
}

/** Money's server-side bound (`|amount| < 10^16`): a float result at or beyond it is refused, never rounded. */
const MONEY_LIMIT = 1e16;

/** Scaled units (x10^4) of an unsigned decimal string; extra decimals are truncated. Throws on any other format. */
export function moneyUnits(value: string): bigint {
  const match = /^[+-]?(\d*)(?:\.(\d*))?$/.exec(value.trim());
  if (!match || (match[1] === '' && !match[2])) {
    throw new Error(`not a decimal amount: ${value}`);
  }
  return BigInt(match[1] || '0') * SCALE + BigInt((match[2] ?? '').slice(0, 4).padEnd(4, '0'));
}

/**
 * Exact settlement amount: a plain decimal literal is parsed with BigInt (more than 4 decimals round half-up at the
 * 4th); an expression falls back to float evaluation at 4 decimals. Returns a normalised unsigned 4-dp string, or
 * null when empty, invalid or not positive.
 */
export function parseMoneyExact(text: string): string | null {
  const trimmed = text.trim();
  if (!trimmed) {
    return null;
  }
  const parts = literalParts(trimmed);
  let units: bigint;
  if (parts) {
    units = BigInt(parts.whole) * SCALE + BigInt(parts.fraction.slice(0, 4).padEnd(4, '0'));
    if (parts.fraction.length > 4 && parts.fraction.charCodeAt(4) >= 53) {
      units += 1n;
    }
  } else {
    const result = evaluateAmount(trimmed, LEDGER_DECIMALS);
    if (!result.ok || !(result.value > 0) || !(result.value < MONEY_LIMIT)) {
      return null;
    }
    units = moneyUnits(result.value.toFixed(LEDGER_DECIMALS));
  }
  return units > 0n ? unitsToString(units) : null;
}

/** Display/serialize form: at most 4 decimals, trailing zeros stripped (`'20.0000'` becomes `'20'`). */
export function formatMoneyExact(value: string): string {
  return unitsToString(moneyUnits(value));
}

/** Float evaluation of a settlement's debt-currency field, for the display-only conversion preview. */
export function parseMoneyText(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) {
    return null;
  }
  const result = evaluateAmount(trimmed, LEDGER_DECIMALS);
  return result.ok && result.value > 0 ? result.value : null;
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
