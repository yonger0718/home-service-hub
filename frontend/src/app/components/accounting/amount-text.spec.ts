import { describe, expect, it } from 'vitest';

import { amountString, parseAmountFields, parseAmountText } from './amount-text';

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

  it('reads one amount field per line, or null when one is empty or invalid', () => {
    // Final review F5: the repost panel and the sheet's period editor send nothing for a bad field.
    expect(parseAmountFields(['8,333', '620', '0'], ['TWD', 'TWD', 'TWD'])).toEqual(['8333', '620', '0']);
    expect(parseAmountFields(['12.5'], ['USD'])).toEqual(['12.5']);
    expect(parseAmountFields(['8333', ''], ['TWD', 'TWD'])).toBeNull();
    expect(parseAmountFields(['abc'], ['TWD'])).toBeNull();
    expect(parseAmountFields(['-5'], ['TWD'])).toBeNull();
  });
});
