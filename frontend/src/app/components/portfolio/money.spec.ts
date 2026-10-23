import { describe, expect, it } from 'vitest';

import { formatFxRate, formatNativeAmount, groupedInteger, isTwdCurrency, twdAmount } from './money';

describe('money', () => {
  describe('formatNativeAmount', () => {
    it('renders foreign amounts with 2 decimals and the currency as a suffix', () => {
      expect(formatNativeAmount(7.8, 'USD')).toBe('7.80 USD');
      expect(formatNativeAmount('238.4', 'USD')).toBe('238.40 USD');
    });

    it('renders GBp with 4 decimals', () => {
      expect(formatNativeAmount(1.2345, 'GBp')).toBe('1.2345 GBp');
      expect(formatNativeAmount(12, 'GBp')).toBe('12.0000 GBp');
    });

    it('renders TWD and a missing currency with the TWD currency format by default', () => {
      const twd = new Intl.NumberFormat('zh-TW', { style: 'currency', currency: 'TWD', minimumFractionDigits: 0 }).format(1500);
      expect(formatNativeAmount(1500, 'TWD')).toBe(twd);
      expect(formatNativeAmount(1500, null)).toBe(twd);
      expect(formatNativeAmount(1500, undefined)).toBe(twd);
      expect(formatNativeAmount(1500, 'twd')).toBe(twd);
    });

    it('uses a supplied TWD formatter', () => {
      expect(formatNativeAmount('1500', 'TWD', v => `NT ${v}`)).toBe('NT 1500');
      expect(formatNativeAmount(1500, 'USD', v => `NT ${v}`)).toBe('1,500.00 USD');
    });

    it('renders an em dash for missing or non-numeric values', () => {
      expect(formatNativeAmount(null, 'USD')).toBe('—');
      expect(formatNativeAmount(undefined, 'TWD')).toBe('—');
      expect(formatNativeAmount('', 'USD')).toBe('—');
      expect(formatNativeAmount('abc', 'USD')).toBe('—');
    });
  });

  describe('isTwdCurrency', () => {
    it('is true for a missing currency and any case of TWD', () => {
      expect(isTwdCurrency(undefined)).toBe(true);
      expect(isTwdCurrency(null)).toBe(true);
      expect(isTwdCurrency('')).toBe(true);
      expect(isTwdCurrency('TWD')).toBe(true);
      expect(isTwdCurrency('twd')).toBe(true);
      expect(isTwdCurrency('USD')).toBe(false);
      expect(isTwdCurrency('GBp')).toBe(false);
    });
  });

  describe('twdAmount', () => {
    it('returns the value itself for TWD (any case) or a missing currency, ignoring fx', () => {
      expect(twdAmount(1500, undefined, 'TWD')).toBe(1500);
      expect(twdAmount(1500, 30.7, 'twd')).toBe(1500);
      expect(twdAmount('1500', null, undefined)).toBe(1500);
      expect(twdAmount(1500, 30.7, null)).toBe(1500);
    });

    it('multiplies a foreign value by a finite positive fx, numeric or string', () => {
      expect(twdAmount(7.8, 30.8, 'USD')).toBeCloseTo(240.24, 10);
      expect(twdAmount('2385', '30.70', 'USD')).toBeCloseTo(73219.5, 10);
    });

    it('returns null for a foreign value without a usable fx', () => {
      expect(twdAmount(7.8, undefined, 'USD')).toBeNull();
      expect(twdAmount(7.8, null, 'USD')).toBeNull();
      expect(twdAmount(7.8, '', 'USD')).toBeNull();
      expect(twdAmount(7.8, 'abc', 'USD')).toBeNull();
      expect(twdAmount(7.8, 0, 'USD')).toBeNull();
      expect(twdAmount(7.8, -1, 'USD')).toBeNull();
      expect(twdAmount(7.8, Infinity, 'USD')).toBeNull();
    });

    it('returns null for a non-numeric value', () => {
      expect(twdAmount('abc', 30.7, 'USD')).toBeNull();
      expect(twdAmount('abc', null, 'TWD')).toBeNull();
    });
  });

  describe('groupedInteger', () => {
    it('rounds to a whole number with thousands separators and never renders -0', () => {
      expect(groupedInteger(9840.24)).toBe('9,840');
      expect(groupedInteger(58920)).toBe('58,920');
      expect(groupedInteger(-0.2)).toBe('0');
    });
  });

  describe('formatFxRate', () => {
    it('renders 2 to 4 fraction digits', () => {
      expect(formatFxRate(30.8)).toBe('30.80');
      expect(formatFxRate(31.2345)).toBe('31.2345');
      expect(formatFxRate(31.23456)).toBe('31.2346');
    });
  });
});
