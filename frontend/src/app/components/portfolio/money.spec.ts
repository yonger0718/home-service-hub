import { describe, expect, it } from 'vitest';

import { formatNativeAmount, twdAmount } from './money';

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
    });

    it('uses a supplied TWD formatter', () => {
      expect(formatNativeAmount('1500', 'TWD', v => `NT ${v}`)).toBe('NT 1500');
      expect(formatNativeAmount(1500, 'USD', v => `NT ${v}`)).toBe('1500.00 USD');
    });

    it('renders an em dash for missing or non-numeric values', () => {
      expect(formatNativeAmount(null, 'USD')).toBe('—');
      expect(formatNativeAmount(undefined, 'TWD')).toBe('—');
      expect(formatNativeAmount('', 'USD')).toBe('—');
      expect(formatNativeAmount('abc', 'USD')).toBe('—');
    });
  });

  describe('twdAmount', () => {
    it('returns the value itself for TWD or a missing currency, ignoring fx', () => {
      expect(twdAmount(1500, undefined, 'TWD')).toBe(1500);
      expect(twdAmount('1500', null)).toBe(1500);
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
});
