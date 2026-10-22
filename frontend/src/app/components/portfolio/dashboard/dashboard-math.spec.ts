import { describe, expect, it } from 'vitest';

import { MARKET_LABELS, composition, quoteTimeLabel, todayChange } from './dashboard-math';

describe('dashboard math', () => {
  describe('todayChange', () => {
    it('divides the day P&L by the assets before today', () => {
      const change = todayChange({ total_day_pnl: 10, total_assets_twd: '1250' });

      expect(change.amount).toBe(10);
      expect(change.percent).toBeCloseTo((10 / 1240) * 100, 10);
      expect(change.percent).toBeCloseTo(0.806, 3);
    });

    it('has no percent when the assets before today are not positive', () => {
      expect(todayChange({ total_day_pnl: 10, total_assets_twd: '10' }).percent).toBeNull();
      expect(todayChange({ total_day_pnl: 10, total_assets_twd: '0' }).percent).toBeNull();
      expect(todayChange({ total_day_pnl: 0, total_assets_twd: '0' })).toEqual({ amount: 0, percent: null });
    });
  });

  describe('composition', () => {
    it('splits stocks and cash and keeps pending dividends out of the bar', () => {
      expect(
        composition({ total_market_value: 1000, total_cash_twd: '250', total_pending_dividends_net: 14600 }),
      ).toEqual({ stocks: 1000, cash: 250, pending: 14600, stocksPct: 80, cashPct: 20 });
    });

    it('returns zero percents when everything is zero', () => {
      expect(
        composition({ total_market_value: 0, total_cash_twd: '0', total_pending_dividends_net: undefined }),
      ).toEqual({ stocks: 0, cash: 0, pending: 0, stocksPct: 0, cashPct: 0 });
    });
  });

  describe('quoteTimeLabel', () => {
    it('tags 盤中 inside the half-open Taipei session', () => {
      expect(quoteTimeLabel('2026-10-22T05:29:00Z', new Date('2026-10-22T05:29:30Z'))).toBe('行情更新於 13:29 · 盤中');
    });

    it('tags 台股收盤 at 13:30 Taipei (the window is closed)', () => {
      expect(quoteTimeLabel('2026-10-22T05:30:00Z', new Date('2026-10-22T05:30:30Z'))).toBe(
        '行情更新於 13:30 · 台股收盤',
      );
    });

    it('tags 台股收盤 after hours', () => {
      expect(quoteTimeLabel('2026-10-22T09:00:00Z', new Date('2026-10-22T09:00:30Z'))).toBe(
        '行情更新於 17:00 · 台股收盤',
      );
    });

    it('tags 台股收盤 before 09:00 Taipei', () => {
      expect(quoteTimeLabel('2026-10-22T00:59:00Z', new Date('2026-10-22T00:59:30Z'))).toBe(
        '行情更新於 08:59 · 台股收盤',
      );
    });

    it('tags 台股收盤 on a Saturday morning', () => {
      // 2026-10-24 is a Saturday; 02:00Z is 10:00 Taipei.
      expect(quoteTimeLabel('2026-10-24T02:00:00Z', new Date('2026-10-24T02:00:00Z'))).toBe(
        '行情更新於 10:00 · 台股收盤',
      );
    });

    it('takes the time from finished_at and the tag from now', () => {
      expect(quoteTimeLabel('2026-10-21T09:00:00Z', new Date('2026-10-22T02:00:00Z'))).toBe('行情更新於 17:00 · 盤中');
    });

    it('returns null without a parseable time', () => {
      expect(quoteTimeLabel(null, new Date('2026-10-22T02:00:00Z'))).toBeNull();
      expect(quoteTimeLabel(undefined, new Date('2026-10-22T02:00:00Z'))).toBeNull();
      expect(quoteTimeLabel('not a date', new Date('2026-10-22T02:00:00Z'))).toBeNull();
    });
  });

  it('labels the markets in Chinese', () => {
    expect(MARKET_LABELS).toEqual({ TW: '台股', US: '美股', LSE: '英股' });
  });
});
