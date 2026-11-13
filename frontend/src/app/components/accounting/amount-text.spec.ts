import { describe, expect, it } from 'vitest';

import { amountString, formatMoneyExact, moneyUnits, parseAmountFields, parseAmountText, parseMoneyExact, parseMoneyText } from './amount-text';

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

  it('parses ledger amounts exactly at four decimals', () => {
    expect(parseMoneyExact('0.0001')).toBe('0.0001');
    expect(parseMoneyExact('20.0001')).toBe('20.0001');
    expect(parseMoneyExact('100000000000.0001')).toBe('100000000000.0001');
    expect(parseMoneyExact('100000000000.0006')).toBe('100000000000.0006');
    expect(parseMoneyExact('1,000.5')).toBe('1000.5');
    expect(parseMoneyExact('１２．５０')).toBe('12.5');
    expect(parseMoneyExact('20.00005')).toBe('20.0001');
    expect(parseMoneyExact('20.00004')).toBe('20');
    expect(parseMoneyExact('10+5')).toBe('15');
    expect(parseMoneyExact('')).toBeNull();
    expect(parseMoneyExact('abc')).toBeNull();
    expect(parseMoneyExact('0')).toBeNull();
    expect(parseMoneyExact('0.00004')).toBeNull();
    // An expression whose float result leaves the Money range (or prints in exponent form) is refused, not truncated.
    expect(parseMoneyExact('1000000000000000*1000000')).toBeNull();
    expect(parseMoneyExact('9999999999999999*10')).toBeNull();
  });

  it('keeps scaled units and formats without float', () => {
    expect(moneyUnits('100000000000.0001')).toBeGreaterThan(moneyUnits('100000000000'));
    expect(moneyUnits('100000000000.0007')).toBeGreaterThan(moneyUnits('100000000000.0006'));
    expect(moneyUnits('20')).toBe(200000n);
    expect(() => moneyUnits('1e+21')).toThrow();
    expect(() => moneyUnits('abc')).toThrow();
    expect(formatMoneyExact('20.0000')).toBe('20');
    expect(formatMoneyExact('100000000000.0001')).toBe('100000000000.0001');
    expect(formatMoneyExact('0.1000')).toBe('0.1');
    expect(parseMoneyText('20.0001')).toBe(20.0001);
  });
});
