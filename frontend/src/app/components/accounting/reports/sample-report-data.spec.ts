import { describe, expect, it } from 'vitest';

import { gross, num } from './report-math';
import { SAMPLE_EXPENSE, SAMPLE_INCOME, SAMPLE_REFUND, SampleBudgetStore, sampleBreakdown, sampleTrend } from './sample-report-data';

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

  it('shapes a ranked row\'s six months around its amount', () => {
    const coffee = sampleTrend({ date_from: '2026-05-01', date_to: '2026-10-31', granularity: 'month', merchant: '路易莎' });
    expect(coffee.buckets.map(bucket => bucket.values['all'])).toEqual(['880', '1118', '942', '1014', '963', '1035']);
    expect(coffee.average['all']).toBe('992');
    const tag = sampleTrend({ date_from: '2026-05-01', date_to: '2026-10-31', granularity: 'month', tag: ['家用'] });
    expect(tag.buckets.at(-1)?.values['all']).toBe('6420');
  });
});

describe('sample budgets and projects', () => {
  it('reports the canvas budgets for October with 購物 over', () => {
    const status = new SampleBudgetStore().status('2026-10', '2026-10-30');
    expect(status.time_progress).toBeCloseTo(30 / 31, 3);
    const shopping = status.budgets.find(b => b.label === '購物')!;
    expect(shopping).toMatchObject({ scope: 'category', category_id: 4, limit: '3000', used: '3490', remaining: '-490', over: true });
    expect(shopping.ratio).toBeCloseTo(1.1633, 3);
    expect(status.budgets.find(b => b.scope === 'total')).toMatchObject({ limit: '45000', used: '41890', over: false });
    // 購物's budget starts in September: August has no row for it.
    expect(new SampleBudgetStore().status('2026-08', '2026-10-30').budgets.map(b => b.label)).not.toContain('購物');
  });

  it('keeps writes in memory: a new amount from the month on, a new budget, a removal', () => {
    const store = new SampleBudgetStore();
    store.update(1, { amount: '12000', effective_from: '2026-11' });
    expect(store.status('2026-11', '2026-11-30').budgets.find(b => b.id === 1)?.limit).toBe('12000');
    const created = store.create({ scope: 'category', category_id: 1, amount: '20000', effective_from: '2026-10' });
    expect(created).toMatchObject({ label: '居家', used: '20250', over: true });
    store.remove(created.id);
    expect(store.status('2026-10', '2026-10-31').budgets.some(b => b.id === created.id)).toBe(false);
  });

  it('lists the projects over their life, 東京旅行 only when excluded projects are asked for', () => {
    const query = { date_from: '2000-01-01', date_to: '2026-10-31', group_by: 'project' as const, split_by: 'category' as const };
    expect(sampleBreakdown(query).rows.map(row => row.label)).toEqual(['客廳整理', '']);
    const all = sampleBreakdown({ ...query, include_excluded_projects: true });
    expect(all.rows.map(row => [row.key, row.label])).toEqual([['1', '東京旅行'], ['2', '客廳整理'], [null, '']]);
    expect(all.rows[0]).toMatchObject({ amount: '24860', first_date: '2026-10-10', last_date: '2026-10-15' });
    expect(all.rows[0].splits!.reduce((sum, split) => sum + Number(split.amount), 0)).toBe(24860);
    expect(all.rows[1].last_date).toBe('2026-10-31');
  });
});
