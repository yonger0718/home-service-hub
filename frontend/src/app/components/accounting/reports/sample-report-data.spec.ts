import { describe, expect, it } from 'vitest';

import { gross, num } from './report-math';
import { SAMPLE_EXPENSE, SAMPLE_INCOME, SAMPLE_REFUND, sampleBreakdown, sampleTrend } from './sample-report-data';

const OCTOBER = { date_from: '2026-10-01', date_to: '2026-10-31' } as const;

describe('sample report data', () => {
  it('matches the handoff figures and adds up', () => {
    expect(SAMPLE_EXPENSE).toBe(41890);
    expect(SAMPLE_REFUND).toBe(490);
    expect(SAMPLE_INCOME).toBe(68126);
    const expense = sampleBreakdown({ ...OCTOBER, group_by: 'category', split_by: 'subcategory', compare: 'previous' });
    expect(expense.totals).toEqual({ expense: '41890', income: '68126', net: '26236', count: 87, refund: '490' });
    expect(expense.rows.reduce((sum, row) => sum + gross(row), 0)).toBe(42380);
    expect(expense.rows.reduce((sum, row) => sum + num(row.amount), 0)).toBe(41890);
    for (const row of expense.rows) {
      expect(row.splits!.reduce((sum, split) => sum + num(split.amount), 0)).toBe(gross(row));
    }
    expect(expense.compare).toEqual({ date_from: '2026-09-01', date_to: '2026-09-30', totals: { expense: '39608', income: '68098' } });
    expect(sampleBreakdown({ ...OCTOBER, group_by: 'category', compare: 'last_year' }).compare?.totals.expense).toBe('44050');
  });

  it('answers the ranking dimensions, 標籤 overlapping', () => {
    const merchants = sampleBreakdown({ ...OCTOBER, group_by: 'merchant' });
    expect(merchants.rows.reduce((sum, row) => sum + num(row.amount), 0)).toBe(41890);
    expect(merchants.rows.find(row => row.key === null)?.count).toBe(9);
    expect(merchants.rows.find(row => row.label === '路易莎')).toMatchObject({ count: 12, median: '85', min: '65', max: '120', avg_interval_days: 2.5 });
    expect(sampleBreakdown({ ...OCTOBER, group_by: 'tag' }).overlapping).toBe(true);
    expect(sampleBreakdown({ ...OCTOBER, group_by: 'account' }).rows.reduce((sum, row) => sum + num(row.amount), 0)).toBe(41890);
  });

  it('returns twelve monthly buckets ending at the requested month, per series', () => {
    const all = sampleTrend({ date_from: '2025-11-01', date_to: '2026-10-31', granularity: 'month' });
    expect(all.buckets.map(bucket => bucket.start.slice(0, 7))).toEqual([
      '2025-11', '2025-12', '2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09', '2026-10',
    ]);
    expect(all.buckets.at(-1)?.values['all']).toBe('41890');
    expect(num(all.average['all'])).toBeCloseTo(40608, 0);

    const food = sampleTrend({ date_from: '2025-11-01', date_to: '2026-10-31', granularity: 'month', series: [2] });
    expect(food.buckets.at(-1)?.values['2']).toBe('9860');
    const home = sampleTrend({ date_from: '2026-05-01', date_to: '2026-10-31', granularity: 'month', series: [1] });
    expect(home.buckets).toHaveLength(6);
    expect(home.buckets.at(-1)?.values['1']).toBe('20250');
  });
});
