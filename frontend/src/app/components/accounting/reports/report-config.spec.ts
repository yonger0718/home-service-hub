import { describe, expect, it } from 'vitest';

import { ReportConfig } from '../../../models/reports.model';
import { chartOptions, configQuery, describeConfig, filterChips, fittingChart, resolvePeriod, withoutFilter } from './report-config';

const REFS = {
  categories: [
    { id: 2, kind: 'expense', parent_id: null, name: '餐飲', icon: null, color: null, sort_order: 0, is_hidden: false, default_account_id: null, default_project_id: null, children: [
      { id: 201, kind: 'expense', parent_id: 2, name: '午餐', icon: null, color: null, sort_order: 0, is_hidden: false, default_account_id: null, default_project_id: null, children: [] },
    ] },
  ],
  accounts: [],
  projects: [{ id: 1, name: '東京旅行', is_archived: true, sort_order: 0, moze_id: null }],
};

describe('saved report periods', () => {
  it('rolls relative presets forward on every open', () => {
    // 週末外食 saved with 近 6 個月 on 2026-10-31, opened on 2026-12-02: July to December 2026.
    expect(resolvePeriod({ preset: 'last_6_months' }, '2026-12-02')).toEqual({ unit: 'custom', from: '2026-07-01', to: '2026-12-31' });
    expect(resolvePeriod({ preset: 'last_6_months' }, '2026-10-31')).toEqual({ unit: 'custom', from: '2026-05-01', to: '2026-10-31' });
    expect(resolvePeriod({ preset: 'this_month' }, '2026-10-31')).toEqual({ unit: 'month', from: '2026-10-01', to: '2026-10-31' });
    expect(resolvePeriod({ preset: 'last_month' }, '2026-01-15')).toEqual({ unit: 'month', from: '2025-12-01', to: '2025-12-31' });
    expect(resolvePeriod({ preset: 'last_12_months' }, '2026-10-31').from).toBe('2025-11-01');
    expect(resolvePeriod({ preset: 'this_year' }, '2026-10-31')).toEqual({ unit: 'year', from: '2026-01-01', to: '2026-12-31' });
    expect(resolvePeriod({ preset: 'last_year' }, '2026-10-31')).toEqual({ unit: 'year', from: '2025-01-01', to: '2025-12-31' });
    expect(resolvePeriod({ preset: 'all' }, '2026-10-31')).toEqual({ unit: 'custom', from: '2000-01-01', to: '2026-10-31' });
    expect(resolvePeriod({ from: '2026-03-01', to: '2026-03-15' }, '2026-10-31')).toEqual({ unit: 'custom', from: '2026-03-01', to: '2026-03-15' });
  });
});

describe('chart rules', () => {
  it('disables 圓環 with a split, and on time dimensions; 折線 off time dimensions', () => {
    const split = chartOptions({ group_by: 'month', split_by: 'subcategory' });
    expect(split.find(o => o.chart === 'donut')?.reason).toBe('有細分時不能用圓環');
    expect(split.find(o => o.chart === 'line')?.reason).toBeNull();
    expect(chartOptions({ group_by: 'month' }).find(o => o.chart === 'donut')?.reason).toBe('依月份、週、日分組時不能用圓環');
    const merchant = chartOptions({ group_by: 'merchant' });
    expect(merchant.find(o => o.chart === 'donut')?.reason).toBeNull();
    expect(merchant.find(o => o.chart === 'line')?.reason).toBe('只有依月份、週、日分組才能用折線');
    expect(merchant.filter(o => !o.reason).map(o => o.chart)).toEqual(['bar', 'donut', 'table']);
  });

  it('falls back to the first fitting chart', () => {
    expect(fittingChart({ group_by: 'merchant' }, 'donut')).toBe('donut');
    expect(fittingChart({ group_by: 'merchant', split_by: 'category' }, 'donut')).toBe('bar');
    expect(fittingChart({ group_by: 'month' }, 'line')).toBe('line');
  });
});

describe('config sentence and query', () => {
  const config: ReportConfig = {
    v: 1, period: { preset: 'last_6_months' }, metric: 'expense', group_by: 'month', split_by: 'subcategory',
    filters: { category_id: [2, 201], weekday: [6, 7] }, compare: 'last_year',
  };

  it('reads the config back as one sentence with named ids', () => {
    expect(describeConfig(config, REFS)).toBe('近 6 個月 · 支出 · 依月份 · 細分到子類別 · 類別 餐飲、餐飲 › 午餐 且 星期 六、日 · 和去年同期比較');
    expect(filterChips({ project_id: [1, 9], merchant: '全聯' }, REFS)).toEqual([
      { key: 'project_id', label: '專案', value: '東京旅行、#9' },
      { key: 'merchant', label: '商家', value: '全聯' },
    ]);
  });

  it('turns the config into the breakdown query as of today', () => {
    expect(configQuery(config, '2026-10-31')).toEqual({
      date_from: '2026-05-01', date_to: '2026-10-31', metric: 'expense', group_by: 'month', split_by: 'subcategory', compare: 'last_year',
      category_id: [2, 201], weekday: [6, 7], include_excluded_projects: undefined,
    });
    expect(configQuery({ ...config, filters: { project_id: [1] } }, '2026-10-31').include_excluded_projects).toBe(true);
  });

  it('removes a filter, both amount bounds together', () => {
    expect(withoutFilter({ category_id: [2], weekday: [6] }, 'weekday')).toEqual({ category_id: [2] });
    expect(withoutFilter({ amount_min: '100', amount_max: '500', tag: ['x'] }, 'amount_min')).toEqual({ tag: ['x'] });
  });
});
