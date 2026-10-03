import { describe, expect, it } from 'vitest';

import { amountString, parseAmountText } from './amount-text';

describe('amount text helpers', () => {
  it('evaluates an expression in the currency decimals', () => {
    expect(parseAmountText('1200+35', 'TWD')).toBe(1235);
    expect(parseAmountText(' 12.346 ', 'USD')).toBe(12.35);
  });

  it('returns null for empty, invalid, zero or negative input', () => {
    expect(parseAmountText('', 'TWD')).toBeNull();
    expect(parseAmountText('abc', 'TWD')).toBeNull();
    expect(parseAmountText('0', 'TWD')).toBeNull();
    expect(parseAmountText('5-9', 'TWD')).toBeNull();
  });

  it('formats an unsigned API amount without trailing zeros', () => {
    expect(amountString(10000, 'TWD')).toBe('10000');
    expect(amountString(46200, 'JPY')).toBe('46200');
    expect(amountString(12.5, 'USD')).toBe('12.5');
    expect(amountString(12, 'USD')).toBe('12');
  });
});
