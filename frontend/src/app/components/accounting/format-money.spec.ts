import { describe, expect, it } from 'vitest';

import { formatMoney } from './format';

describe('formatMoney', () => {
  it('uses a currency symbol and whole units for TWD and JPY', () => {
    expect(formatMoney('220.0000', 'TWD')).toBe('$220');
    expect(formatMoney('-1183', 'TWD')).toBe('−$1,183');
    expect(formatMoney('5390', 'JPY')).toBe('¥5,390');
    expect(formatMoney('-0.4', 'TWD')).toBe('$0');
  });

  it('keeps up to 2 decimals elsewhere and falls back to the code', () => {
    expect(formatMoney('12.5', 'USD')).toBe('US$12.5');
    expect(formatMoney('3', 'USDT')).toBe('USDT 3');
  });

  it('adds a plus sign on request', () => {
    expect(formatMoney('41', 'TWD', { sign: true })).toBe('+$41');
    expect(formatMoney(0, 'TWD', { sign: true })).toBe('$0');
  });
});
