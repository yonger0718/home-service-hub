import { describe, expect, it } from 'vitest';

import { makeEntry } from './testing/fixtures';
import {
  currencyDecimals,
  displayCategory,
  displayTitle,
  formatAmount,
  formatMoney,
  formatNumber,
  formatSigned,
  isNegative,
} from './format';

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

describe('currencyDecimals', () => {
  it('uses whole units for TWD and JPY and 2 decimals otherwise', () => {
    expect(currencyDecimals('TWD')).toBe(0);
    expect(currencyDecimals('JPY')).toBe(0);
    expect(currencyDecimals('USD')).toBe(2);
    expect(currencyDecimals('USDT')).toBe(2);
  });
});

describe('formatSigned / formatMoney / formatNumber', () => {
  it('prefixes + or − and the currency symbol', () => {
    expect(formatSigned('-170.0000', 'TWD')).toBe('−$170');
    expect(formatSigned('62000', 'TWD')).toBe('+$62,000');
    expect(formatSigned('-5390', 'JPY')).toBe('−¥5,390');
    expect(formatSigned('12.5', 'USD')).toBe('+US$12.5');
    expect(formatSigned('3', 'USDT')).toBe('+USDT 3');
  });

  it('shows zero, and amounts that round to zero, without a sign', () => {
    expect(formatSigned('0', 'TWD')).toBe('$0');
    expect(formatSigned('-0.4', 'TWD')).toBe('$0');
  });

  it('formats money with a minus only for negatives', () => {
    expect(formatMoney('-1166.0000', 'TWD')).toBe('−$1,166');
    expect(formatMoney('5390', 'JPY')).toBe('¥5,390');
  });

  it('formats a bare number with the currency decimals', () => {
    expect(formatNumber('1235', 'TWD')).toBe('1,235');
    expect(formatNumber('33.333', 'USD')).toBe('33.33');
    expect(formatNumber(-8, 'TWD')).toBe('−8');
  });
});

describe('displayCategory / displayTitle', () => {
  it('shows the category path with › and falls back to the kind', () => {
    expect(displayCategory(makeEntry({ category: '購物/衣物' }))).toBe('購物 › 衣物');
    expect(displayCategory(makeEntry({ category: null, kind: 'transfer_out' }))).toBe('轉出');
  });

  it('prefers the name, then the sub-category, then the kind', () => {
    expect(displayTitle(makeEntry({ name: 'Uniqlo 外套', category: '購物/衣物' }))).toBe('Uniqlo 外套');
    expect(displayTitle(makeEntry({ name: '  ', category: '飲食/午餐' }))).toBe('午餐');
    expect(displayTitle(makeEntry({ name: null, category: null, kind: 'reward' }))).toBe('紅利回饋');
  });
});
