import { describe, expect, it } from 'vitest';

import { formatAmount, isNegative } from './format';

describe('formatAmount', () => {
  it('groups thousands and keeps up to 2 decimals for currencies with minor units', () => {
    expect(formatAmount('2299.5000', 'USD')).toBe('USD 2,299.5');
    expect(formatAmount('1234567.891', 'USD')).toBe('USD 1,234,567.89');
  });

  it('rounds TWD to whole dollars, as cash amounts are shown in Taiwan', () => {
    expect(formatAmount('-360.1234', 'TWD')).toBe('TWD -360');
    expect(formatAmount('1234567.891', 'TWD')).toBe('TWD 1,234,568');
    expect(formatAmount('2299.5000', 'TWD')).toBe('TWD 2,300');
    expect(formatAmount('-2299.5000', 'TWD')).toBe('TWD -2,300');
  });

  it('formats negative balances with a minus sign', () => {
    expect(formatAmount('-1500.0000', 'TWD')).toBe('TWD -1,500');
  });

  it('rounds JPY to whole yen', () => {
    expect(formatAmount('180000.0000', 'JPY')).toBe('JPY 180,000');
    expect(formatAmount('6844.8239', 'JPY')).toBe('JPY 6,845');
  });

  it('formats non-ISO MOZE currencies such as USDT without throwing', () => {
    expect(formatAmount('12.3400', 'USDT')).toBe('USDT 12.34');
  });
});

describe('isNegative', () => {
  it('detects negative decimal strings', () => {
    expect(isNegative('-0.0100')).toBe(true);
    expect(isNegative('0.0000')).toBe(false);
  });
});
