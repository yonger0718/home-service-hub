import { describe, expect, it } from 'vitest';

import { RealizedPnlEvent } from '../../../models/portfolio.model';
import { REALIZED_FIXTURE, TODAY } from './realized-fixture.spec-data';
import {
  barsAriaText,
  barsYear,
  clampOffset,
  matchesMarket,
  monthlyBars,
  rangeLabel,
  realizedTotals,
} from './realized-math';

const row = (overrides: Partial<RealizedPnlEvent>): RealizedPnlEvent => ({ ...REALIZED_FIXTURE[0], ...overrides });

describe('realizedTotals', () => {
  it('sums the fixture into total, gains/losses, this month, 當沖 and fees + tax', () => {
    expect(realizedTotals(REALIZED_FIXTURE, TODAY)).toEqual({
      total: 21046,
      count: 5,
      gains: 4,
      losses: 1,
      month: 13498,
      monthCount: 1,
      dayTrade: 2030,
      dayTradeCount: 1,
      costs: 1764,
    });
  });

  it('counts a zero realized sell in count but in neither gains nor losses', () => {
    const totals = realizedTotals([...REALIZED_FIXTURE, row({ trade_date: '2026-01-05', realized_pnl: '0', fee: '0', tax: '0' })], TODAY);

    expect(totals.count).toBe(6);
    expect(totals.gains).toBe(4);
    expect(totals.losses).toBe(1);
    expect(totals.total).toBe(21046);
  });

  it('is all zero for no rows', () => {
    expect(realizedTotals([], TODAY)).toEqual({
      total: 0, count: 0, gains: 0, losses: 0, month: 0, monthCount: 0, dayTrade: 0, dayTradeCount: 0, costs: 0,
    });
  });

  it('matches this month by the YYYY-MM slice, not by the year alone', () => {
    const totals = realizedTotals([row({ trade_date: '2025-10-02' })], TODAY);

    expect(totals.monthCount).toBe(0);
    expect(totals.month).toBe(0);
  });
});

describe('monthlyBars', () => {
  const bars = monthlyBars(REALIZED_FIXTURE, 2026, TODAY);

  it('has one entry per month', () => {
    expect(bars).toHaveLength(12);
    expect(bars.map(bar => bar.month)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
  });

  it('scales each month by the largest |total|', () => {
    const april = bars[3];
    expect(april).toEqual(expect.objectContaining({ total: -4897, count: 1, future: false }));
    expect(april.ratio).toBeCloseTo(4897 / 13498, 6);
    expect(bars[9]).toEqual(expect.objectContaining({ total: 13498, count: 1, ratio: 1 }));
  });

  it('marks only the months after the current one as future', () => {
    expect(bars.filter(bar => bar.future).map(bar => bar.month)).toEqual([11, 12]);
  });

  it('keeps an empty month at zero', () => {
    expect(bars[1]).toEqual({ month: 2, total: 0, count: 0, ratio: 0, future: false });
  });

  it('has all ratios 0 (no NaN) for no rows', () => {
    expect(monthlyBars([], 2026, TODAY).map(bar => bar.ratio)).toEqual(Array(12).fill(0));
  });

  it('marks no month as future for a past year and leaves rows of other years out', () => {
    const past = monthlyBars(REALIZED_FIXTURE, 2025, TODAY);

    expect(past.some(bar => bar.future)).toBe(false);
    expect(past.every(bar => bar.total === 0 && bar.count === 0)).toBe(true);
  });
});

describe('barsYear', () => {
  it('takes the year filter, else the year of date_to, else the current year', () => {
    expect(barsYear({ year: 2025 }, TODAY)).toBe(2025);
    expect(barsYear({ date_to: '2025-06-30' }, TODAY)).toBe(2025);
    expect(barsYear({}, TODAY)).toBe(2026);
  });
});

describe('barsAriaText', () => {
  it('lists the non-zero months in order with signed amounts', () => {
    expect(barsAriaText(monthlyBars(REALIZED_FIXTURE, 2026, TODAY), 2026)).toBe(
      '2026 年每月已實現損益：3 月 +4,714、4 月 −4,897、6 月 +5,701、8 月 +2,030、10 月 +13,498',
    );
  });

  it('says 無 when every month is zero', () => {
    expect(barsAriaText(monthlyBars([], 2026, TODAY), 2026)).toBe('2026 年每月已實現損益：無');
  });
});

describe('rangeLabel', () => {
  it('names the year filter', () => {
    expect(rangeLabel({ year: 2026 })).toBe('2026 年');
  });

  it('falls back to the period label without a year', () => {
    expect(rangeLabel({ date_from: '2026-01-01', date_to: '2026-06-30' })).toBe('2026/01/01 – 2026/06/30');
    expect(rangeLabel({})).toBe('全部期間');
  });
});

describe('matchesMarket', () => {
  const tw = row({ market: 'TW' });
  const us = row({ market: 'US' });
  const lse = row({ market: 'LSE' });

  it('keeps everything under ALL', () => {
    expect([tw, us, lse].every(e => matchesMarket(e, 'ALL'))).toBe(true);
  });

  it('keeps only TW under TW', () => {
    expect([tw, us, lse].map(e => matchesMarket(e, 'TW'))).toEqual([true, false, false]);
  });

  it('keeps every non-TW market (LSE included) under US', () => {
    expect([tw, us, lse].map(e => matchesMarket(e, 'US'))).toEqual([false, true, true]);
  });
});

describe('clampOffset', () => {
  it('moves an offset past the end to the last page start', () => {
    expect(clampOffset(50, 3, 25)).toBe(0);
    expect(clampOffset(75, 30, 25)).toBe(25);
  });

  it('keeps an offset inside the set', () => {
    expect(clampOffset(25, 30, 25)).toBe(25);
  });

  it('is 0 for an empty set', () => {
    expect(clampOffset(25, 0, 25)).toBe(0);
  });
});
