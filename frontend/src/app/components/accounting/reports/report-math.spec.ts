import { describe, expect, it } from 'vitest';

import {
  barGeometry,
  change,
  compareLabels,
  compareRange,
  compareTitle,
  donutArcs,
  elapsedDays,
  monthPeriod,
  n0,
  niceCeiling,
  pct1,
  periodFromParams,
  periodLabel,
  periodParams,
  periodWithUnit,
  seriesColor,
  shiftPeriod,
  tickLabel,
  trendRange,
  yearPeriod,
} from './report-math';

const params = (record: Record<string, string>) => ({ get: (name: string) => record[name] ?? null });

describe('report period', () => {
  it('reads month / year / custom from the query and falls back to the current month', () => {
    expect(periodFromParams(params({ month: '2026-10' }), '2026-10-31')).toEqual({ unit: 'month', from: '2026-10-01', to: '2026-10-31' });
    expect(periodFromParams(params({ year: '2026' }), '2026-10-31')).toEqual({ unit: 'year', from: '2026-01-01', to: '2026-12-31' });
    expect(periodFromParams(params({ from: '2026-10-15', to: '2026-10-03' }), '2026-10-31')).toEqual({ unit: 'custom', from: '2026-10-03', to: '2026-10-15' });
    expect(periodFromParams(params({ month: 'nope' }), '2026-02-10')).toEqual(monthPeriod('2026-02'));
  });

  it('writes one set of params per unit and clears the others', () => {
    expect(periodParams(monthPeriod('2026-10'))).toEqual({ month: '2026-10', year: null, from: null, to: null });
    expect(periodParams(yearPeriod(2026))).toEqual({ month: null, year: '2026', from: null, to: null });
    expect(periodParams({ unit: 'custom', from: '2026-10-03', to: '2026-10-15' })).toEqual({ month: null, year: null, from: '2026-10-03', to: '2026-10-15' });
  });

  it('steps months across the year and custom ranges by their own length', () => {
    expect(shiftPeriod(monthPeriod('2026-01'), -1)).toEqual(monthPeriod('2025-12'));
    expect(shiftPeriod(monthPeriod('2026-12'), 1)).toEqual(monthPeriod('2027-01'));
    expect(shiftPeriod(yearPeriod(2026), -1)).toEqual(yearPeriod(2025));
    expect(shiftPeriod({ unit: 'custom', from: '2026-10-01', to: '2026-10-10' }, -1)).toEqual({ unit: 'custom', from: '2026-09-21', to: '2026-09-30' });
  });

  it('keeps the first month / year when the unit switches', () => {
    expect(periodWithUnit(monthPeriod('2026-10'), 'year')).toEqual(yearPeriod(2026));
    expect(periodWithUnit(yearPeriod(2026), 'month')).toEqual(monthPeriod('2026-01'));
    expect(periodWithUnit(monthPeriod('2026-10'), 'custom')).toEqual({ unit: 'custom', from: '2026-10-01', to: '2026-10-31' });
  });

  it('labels the period and the comparisons', () => {
    expect(periodLabel(monthPeriod('2026-10'))).toBe('2026 年 10 月');
    expect(periodLabel(yearPeriod(2026))).toBe('2026 年');
    expect(periodLabel({ unit: 'custom', from: '2026-10-03', to: '2026-11-15' })).toBe('2026/10/03 – 2026/11/15');
    expect(compareLabels(monthPeriod('2026-10'))).toEqual({ previous: '比 9 月', lastYear: '比去年 10 月' });
    expect(compareLabels(monthPeriod('2026-01'))).toEqual({ previous: '比 2025 年 12 月', lastYear: '比去年 1 月' });
    expect(compareLabels(yearPeriod(2026))).toEqual({ previous: '比去年', lastYear: '比前年' });
    expect(compareTitle(monthPeriod('2026-10'))).toBe('10 月 對 9 月、對去年 10 月');
  });

  it('computes the comparison windows: whole months before, the same dates a year earlier', () => {
    expect(compareRange('2026-10-01', '2026-10-31', 'previous')).toEqual({ from: '2026-09-01', to: '2026-09-30' });
    expect(compareRange('2026-01-01', '2026-12-31', 'previous')).toEqual({ from: '2025-01-01', to: '2025-12-31' });
    expect(compareRange('2026-10-05', '2026-10-14', 'previous')).toEqual({ from: '2026-09-25', to: '2026-10-04' });
    expect(compareRange('2026-10-01', '2026-10-31', 'last_year')).toEqual({ from: '2025-10-01', to: '2025-10-31' });
    expect(compareRange('2024-02-01', '2024-02-29', 'last_year')).toEqual({ from: '2023-02-01', to: '2023-02-28' });
  });

  it('counts elapsed days for 日均支出', () => {
    expect(elapsedDays(monthPeriod('2026-10'), '2026-10-30')).toEqual({ days: 30, running: true });
    expect(elapsedDays(monthPeriod('2026-10'), '2026-11-02')).toEqual({ days: 31, running: false });
    expect(elapsedDays(monthPeriod('2026-10'), '2026-09-02')).toEqual({ days: 1, running: true });
  });

  it('spans the trend over the twelve months ending at the period', () => {
    expect(trendRange(monthPeriod('2026-10'))).toEqual({ from: '2025-11-01', to: '2026-10-31' });
    expect(trendRange(yearPeriod(2026))).toEqual({ from: '2026-01-01', to: '2026-12-31' });
  });
});

describe('report figures', () => {
  it('formats grouped integers and shares', () => {
    expect(n0('41890')).toBe('41,890');
    expect(n0(-490)).toBe('−490');
    expect(pct1(26236, 68126)).toBe('38.5%');
    expect(pct1(5, 0)).toBe('—');
  });

  it('marks more / less spending with ▲ / ▼ and an equal figure with —', () => {
    expect(change(41890, 39608)).toEqual({ text: '▲ 2,282', direction: 'more' });
    expect(change(41890, 44050)).toEqual({ text: '▼ 2,160', direction: 'less' });
    expect(change(1078, 1078)).toEqual({ text: '—', direction: 'same' });
    expect(change(9860, 8640, true)).toEqual({ text: '▲ 1,220 (14.1%)', direction: 'more' });
    expect(change(500, 0, true)).toEqual({ text: '▲ 500', direction: 'more' });
  });
});

describe('report charts', () => {
  it('draws donut arcs that together fill the ring, with a gap each', () => {
    const arcs = donutArcs([
      { value: 75, color: 'a' },
      { value: 25, color: 'b' },
    ]);
    const circumference = 2 * Math.PI * 74;
    expect(arcs).toHaveLength(2);
    expect(arcs[0].dashoffset).toBe('0.0');
    expect(Number(arcs[1].dashoffset)).toBeCloseTo(-circumference * 0.75, 0);
    expect(Number(arcs[0].dasharray.split(' ')[0])).toBeCloseTo(circumference * 0.75 - 2, 0);
    expect(donutArcs([{ value: 0, color: 'a' }])).toEqual([]);
  });

  it('rounds the axis top to a nice number and labels ticks in k', () => {
    expect(niceCeiling(45300)).toBe(50000);
    expect(niceCeiling(12400)).toBe(20000);
    expect(niceCeiling(2300)).toBe(2500);
    expect(niceCeiling(0)).toBe(1000);
    expect(tickLabel(50000)).toBe('50k');
    expect(tickLabel(2500)).toBe('2.5k');
    expect(tickLabel(0)).toBe('0');
  });

  it('places bars in the plot area', () => {
    const [first, second] = barGeometry([50, 100], { left: 0, width: 200, top: 0, height: 100 }, 100);
    expect(first).toEqual({ x: '20.0', y: '50.0', width: '60.0', height: '50.0', cx: '50.0' });
    expect(second.y).toBe('0.0');
    expect(second.height).toBe('100.0');
  });

  it('hands out the series palette then the 其他 tone', () => {
    expect(seriesColor(0)).toBe('var(--app-series-1)');
    expect(seriesColor(6)).toBe('var(--app-series-7)');
    expect(seriesColor(7)).toBe('var(--app-category-other)');
  });
});
