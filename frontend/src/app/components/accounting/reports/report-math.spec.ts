import { describe, expect, it } from 'vitest';

import {
  afterTodayLine,
  afterTodayText,
  dollars,
  runningCompareTitle,
  totalChange,
  asOfText,
  axisParts,
  forecastNote,
  hatchLines,
  runningForecast,
  bucketText,
  monthAverage,
  runningBucket,
  runningSuffix,
  barGeometry,
  baseChange,
  change,
  compareLabels,
  compareRange,
  comparesCoincide,
  compareTitle,
  customPeriod,
  donutArcs,
  drillText,
  noProjectDrillText,
  elapsedDays,
  isRunning,
  md,
  monthPeriod,
  n0,
  niceCeiling,
  pct1,
  runningCompareLabels,
  periodFromParams,
  periodLabel,
  periodNouns,
  periodParams,
  periodWithUnit,
  seriesColor,
  shiftPeriod,
  tickLabel,
  toDateNouns,
  trendRange,
  viewRange,
  windowEmpty,
  yearPeriod,
} from './report-math';
import { BreakdownResponse } from '../../../models/reports.model';

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
    expect(compareLabels(monthPeriod('2026-01'))).toEqual({ previous: '比 2025 年 12 月', lastYear: '比去年 1 月', single: false });
    expect(compareTitle(monthPeriod('2026-10'))).toBe('10 月 對 9 月、對去年 10 月');
  });

  it('keeps two comparisons for a month (上月 / 去年同月) and one, named 去年, when both windows are the same year', () => {
    expect(comparesCoincide(monthPeriod('2026-10'))).toBe(false);
    expect(compareLabels(monthPeriod('2026-10'))).toEqual({ previous: '比 9 月', lastYear: '比去年 10 月', single: false });
    expect(periodNouns(monthPeriod('2026-10'))).toEqual({ current: '本月', previous: '上月', lastYear: '去年同月', single: false });
    // A calendar year: previous = whole months before = last year, so there is only one comparison.
    expect(comparesCoincide(yearPeriod(2026))).toBe(true);
    expect(compareLabels(yearPeriod(2026))).toEqual({ previous: '比去年', lastYear: '比去年', single: true });
    expect(periodNouns(yearPeriod(2026))).toEqual({ current: '今年', previous: '去年', lastYear: '去年', single: true });
    expect(compareTitle(yearPeriod(2026))).toBe('2026 年 對去年');
    // Twelve whole months that are not a calendar year coincide too; a shorter custom range does not.
    const twelve = { unit: 'custom' as const, from: '2026-03-01', to: '2027-02-28' };
    expect(comparesCoincide(twelve)).toBe(true);
    expect(compareLabels(twelve)).toEqual({ previous: '比去年同期', lastYear: '比去年同期', single: true });
    expect(periodNouns(twelve)).toEqual({ current: '本期', previous: '去年同期', lastYear: '去年同期', single: true });
    expect(compareTitle(twelve)).toBe('本期 對去年同期');
    const short = { unit: 'custom' as const, from: '2026-10-05', to: '2026-10-14' };
    expect(comparesCoincide(short)).toBe(false);
    expect(compareLabels(short)).toEqual({ previous: '比前一期', lastYear: '比去年同期', single: false });
    expect(compareTitle(short)).toBe('本期 對前一期、對去年同期');
  });

  it('words the timeline link: N entries for a month, the month that opens for any other period', () => {
    expect(drillText(monthPeriod('2026-10'), 46)).toBe('在記帳記錄看這 46 筆 ›');
    expect(drillText(yearPeriod(2026), 460)).toBe('在記帳記錄看 2026-01（期間第一個月） ›');
    expect(drillText({ unit: 'custom', from: '2026-09-15', to: '2026-10-14' }, 12)).toBe('在記帳記錄看 2026-09（期間第一個月） ›');
  });

  it('words the 無專案 link without a count, naming the month that opens outside a month period (#261)', () => {
    expect(noProjectDrillText(monthPeriod('2026-10'))).toBe('沒掛專案的支出 · 在記帳記錄看 ›');
    expect(noProjectDrillText(yearPeriod(2026))).toBe('沒掛專案的支出 · 在記帳記錄看 2026-01（期間第一個月） ›');
    expect(noProjectDrillText({ unit: 'custom', from: '2026-09-15', to: '2026-10-14' })).toBe('沒掛專案的支出 · 在記帳記錄看 2026-09（期間第一個月） ›');
  });

  it('computes the comparison windows: whole months before, the same dates a year earlier', () => {
    expect(compareRange('2026-10-01', '2026-10-31', 'previous')).toEqual({ from: '2026-09-01', to: '2026-09-30' });
    expect(compareRange('2026-01-01', '2026-12-31', 'previous')).toEqual({ from: '2025-01-01', to: '2025-12-31' });
    expect(compareRange('2026-10-05', '2026-10-14', 'previous')).toEqual({ from: '2026-09-25', to: '2026-10-04' });
    expect(compareRange('2026-10-01', '2026-10-31', 'last_year')).toEqual({ from: '2025-10-01', to: '2025-10-31' });
    expect(compareRange('2024-02-01', '2024-02-29', 'last_year')).toEqual({ from: '2023-02-01', to: '2023-02-28' });
  });

  it('clips the previous window of a one-day running range', () =>
    expect(compareRange('2026-11-01', '2026-11-01', 'previous')).toEqual({ from: '2026-10-01', to: '2026-10-01' }));

  it('compares a running month with the same days of the previous month', () =>
    expect(compareRange('2026-11-01', '2026-11-07', 'previous')).toEqual({ from: '2026-10-01', to: '2026-10-07' }));
  it('clips a 1st-anchored range to the shorter month', () =>
    expect(compareRange('2026-03-01', '2026-03-30', 'previous')).toEqual({ from: '2026-02-01', to: '2026-02-28' }));
  it('clips to 29 February in a leap year', () =>
    expect(compareRange('2028-03-01', '2028-03-30', 'previous')).toEqual({ from: '2028-02-01', to: '2028-02-29' }));
  it('moves a multi-month running range back by the months it touches', () =>
    expect(compareRange('2026-06-01', '2026-11-07', 'previous')).toEqual({ from: '2025-12-01', to: '2026-05-07' }));
  it('keeps the day-count rule for a range not starting on the 1st', () =>
    expect(compareRange('2026-11-02', '2026-11-07', 'previous')).toEqual({ from: '2026-10-27', to: '2026-11-01' }));

  it('counts elapsed days for 日均支出', () => {
    expect(elapsedDays(monthPeriod('2026-10'), '2026-10-30')).toEqual({ days: 30, running: true });
    expect(elapsedDays(monthPeriod('2026-10'), '2026-11-02')).toEqual({ days: 31, running: false });
    expect(elapsedDays(monthPeriod('2026-10'), '2026-09-02')).toEqual({ days: 1, running: true });
  });

  it('reads 上期無資料 without an arrow against an empty window', () =>
    expect(baseChange(4233, 0, true, '上期無資料')).toEqual({ text: '上期無資料', direction: 'same', kind: 'none' }));
  it('marks a key new to a non-empty window as 新增, muted', () =>
    expect(baseChange(1200, 0, false, '上期無資料')).toEqual({ text: '新增', direction: 'same', kind: 'new' }));
  it('does not mark a key new when its current figure rounds to 0', () =>
    expect(baseChange(0.3, 0, false, '上期無資料').kind).not.toBe('new'));
  it('marks a key gone from the current window as — 停止', () =>
    expect(baseChange(0, 800, false, '去年無資料')).toEqual({ text: '— 停止', direction: 'same', kind: 'gone' }));
  it('falls back to the arrow change', () =>
    expect(baseChange(4233, 6910, false, '上期無資料')).toEqual({ text: '▼ 2,677', direction: 'less', kind: 'delta' }));
  it('detects an empty comparison window from its count', () => {
    expect(windowEmpty({ compare: { totals: { count: 0 } } } as BreakdownResponse)).toBe(true);
    expect(windowEmpty({ compare: { totals: { count: 4 } } } as BreakdownResponse)).toBe(false);
    expect(windowEmpty({ compare: null } as BreakdownResponse)).toBe(false);
    expect(windowEmpty(null)).toBe(false);
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
    expect(change(41890, 39608)).toEqual({ text: '▲ 2,282', direction: 'more', kind: 'delta' });
    expect(change(41890, 44050)).toEqual({ text: '▼ 2,160', direction: 'less', kind: 'delta' });
    expect(change(1078, 1078)).toEqual({ text: '—', direction: 'same', kind: 'delta' });
    expect(change(9860, 8640, true)).toEqual({ text: '▲ 1,220 (14.1%)', direction: 'more', kind: 'delta' });
    expect(change(500, 0, true)).toEqual({ text: '▲ 500', direction: 'more', kind: 'delta' });
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

describe('running periods', () => {
  const nov = monthPeriod('2026-11');
  it('runs from the first day until the day before the last', () => {
    expect(isRunning(nov, '2026-11-01')).toBe(true);
    expect(isRunning(nov, '2026-11-07')).toBe(true);
    expect(isRunning(nov, '2026-11-30')).toBe(false); // last day = finished
    expect(isRunning(nov, '2026-10-31')).toBe(false); // not started
    expect(isRunning(customPeriod('2026-11-01', '2026-11-07'), '2026-11-07')).toBe(false);
  });
  it('cuts the view at today only while running and not whole', () => {
    expect(viewRange(nov, '2026-11-07', false)).toEqual({ from: '2026-11-01', to: '2026-11-07' });
    expect(viewRange(nov, '2026-11-07', true)).toEqual({ from: '2026-11-01', to: '2026-11-30' });
    expect(viewRange(nov, '2026-11-30', false)).toEqual({ from: '2026-11-01', to: '2026-11-30' });
  });
  it('writes dates as M/D, with the year when it differs from today', () => {
    expect(md('2026-11-07', '2026-11-07')).toBe('11/7');
    expect(md('2025-12-01', '2026-11-07')).toBe('2025/12/1');
  });
  it('names the to-date columns', () => {
    expect(toDateNouns(nov, '2026-11-07')).toEqual({ current: '本月至今（11/1–11/7）', previous: '上月同期', lastYear: '去年同期', single: false });
    expect(toDateNouns(nov, '2026-11-01').current).toBe('本月至今（11/1–11/1）');
    expect(toDateNouns({ unit: 'custom', from: '2025-12-01', to: '2026-05-31' }, '2026-03-04').current).toBe('本期至今（2025/12/1–3/4）');
  });
  it('names the to-date columns of other units, and one column when the windows coincide', () => {
    expect(toDateNouns({ unit: 'custom', from: '2026-06-01', to: '2026-12-31' }, '2026-11-07')).toMatchObject({ previous: '上期同期', lastYear: '去年同期', single: false });
    expect(toDateNouns(yearPeriod(2026), '2026-11-07')).toEqual({ current: '本期至今（1/1–11/7）', previous: '去年同期', lastYear: '去年同期', single: true });
  });
  it('is single when the cut range windows coincide, though the whole period does not', () => {
    const year = yearPeriod(2026);
    expect(toDateNouns(year, '2026-11-07')).toMatchObject({ lastYear: '去年同期', single: true });
    expect(runningCompareLabels(year, '2026-11-07')).toEqual({ previous: '比去年同期', lastYear: '比去年同期', single: true });
    const custom = customPeriod('2025-11-01', '2026-11-30');
    expect(toDateNouns(custom, '2026-10-31')).toMatchObject({ previous: '去年同期', single: true });
    expect(runningCompareLabels(custom, '2026-10-31')).toEqual({ previous: '比去年同期', lastYear: '比去年同期', single: true });
  });
  it('is not single for a running month', () => {
    expect(toDateNouns(nov, '2026-11-07').single).toBe(false);
    expect(runningCompareLabels(nov, '2026-11-07').single).toBe(false);
  });
  it('labels the header comparisons of a running month', () =>
    expect(runningCompareLabels(nov, '2026-11-07')).toEqual({ previous: '比 10 月同期', lastYear: '比去年同期', single: false }));
  it('labels the header comparisons of a running year and custom range', () => {
    expect(runningCompareLabels(yearPeriod(2026), '2026-11-07')).toEqual({ previous: '比去年同期', lastYear: '比去年同期', single: true });
    expect(runningCompareLabels(customPeriod('2026-06-01', '2026-12-31'), '2026-11-07')).toEqual({ previous: '比前一期同期', lastYear: '比去年同期', single: false });
  });
  it('words the rows-after-today line', () => {
    expect(asOfText('2026-11-07')).toBe('截至 11/7');
    expect(afterTodayText('2026-11-07', 2, 13420, false)).toBe('另有 11/7 之後已記的 2 筆 $13,420 未計入');
    expect(afterTodayText('2026-11-07', 3, null, false)).toBe('另有 11/7 之後已記的 3 筆未計入');
    expect(afterTodayText('2026-11-07', 2, 13420, true)).toBe('改回截至 11/7');
  });
  it('signs a negative amount before the dollar sign', () => {
    expect(dollars(-50)).toBe('−$50');
    expect(dollars(1250)).toBe('$1,250');
    expect(dollars(-0.4)).toBe('$0');
    expect(afterTodayText('2026-11-07', 1, -300, false)).toBe('另有 11/7 之後已記的 1 筆 −$300 未計入');
  });

  describe('afterTodayLine (R3)', () => {
    const row = (count: number) => ({ key: '1', label: '餐飲', color: null, amount: '0', refund: '0', count, avg: '0', median: null, min: null, max: null, first_date: null, last_date: null, avg_interval_days: null });
    const resp = (counts: number[], totals: Partial<{ expense: string; income: string; net: string; count: number }>): BreakdownResponse => ({
      currency: 'TWD', date_from: '2026-11-01', date_to: '2026-11-30',
      totals: { expense: '0', income: '0', net: '0', count: 0, refund: '0', ...totals },
      rows: counts.map(row), overlapping: false, missing_rates: [],
    });
    const cut = resp([9], { expense: '4233', income: '500', count: 10 });
    const whole = resp([9, 2], { expense: '17653', income: '500', count: 99 });

    it('counts rows(whole) − rows(to date) and the metric total difference', () => {
      expect(afterTodayLine(cut, whole, 'expense', '2026-11-07', false)).toBe('另有 11/7 之後已記的 2 筆 $13,420 未計入');
    });
    it('leaves the amount out for the count metric', () => {
      expect(afterTodayLine(cut, whole, 'count', '2026-11-07', false)).toBe('另有 11/7 之後已記的 2 筆未計入');
    });
    it('signs a negative difference', () => {
      expect(afterTodayLine(cut, resp([9, 1], { expense: '4000' }), 'expense', '2026-11-07', false)).toBe('另有 11/7 之後已記的 1 筆 −$233 未計入');
    });
    it('shows nothing without later rows or without the whole read', () => {
      expect(afterTodayLine(cut, cut, 'expense', '2026-11-07', false)).toBeNull();
      expect(afterTodayLine(cut, null, 'expense', '2026-11-07', false)).toBeNull();
      expect(afterTodayLine(null, whole, 'expense', '2026-11-07', false)).toBeNull();
    });
    it('offers 改回截至 in the whole view whatever N is', () => {
      expect(afterTodayLine(cut, whole, 'expense', '2026-11-07', true)).toBe('改回截至 11/7');
      expect(afterTodayLine(cut, cut, 'income', '2026-11-07', true)).toBe('改回截至 11/7');
      expect(afterTodayLine(null, null, 'expense', '2026-11-07', true)).toBe('改回截至 11/7');
    });
  });

  it('compares totals as a delta, never 新增 / 停止', () => {
    expect(totalChange(0, 500, false, '上期無資料')).toEqual({ text: '▼ 500', direction: 'less', kind: 'delta' });
    expect(totalChange(0, 500, false, '上期無資料', true)).toEqual({ text: '▼ 500 (100.0%)', direction: 'less', kind: 'delta' });
    expect(totalChange(300, 0, false, '去年無資料')).toEqual({ text: '▲ 300', direction: 'more', kind: 'delta' });
    expect(totalChange(0, 0, true, '去年無資料')).toEqual({ text: '去年無資料', direction: 'same', kind: 'none' });
  });

  it('titles the to-date comparison card after the columns', () => {
    expect(runningCompareTitle(nov, '2026-11-07')).toBe('本月至今 對 上月同期、對 去年同期');
    expect(runningCompareTitle(yearPeriod(2026), '2026-11-07')).toBe('本期至今 對 去年同期');
    expect(runningCompareTitle(customPeriod('2026-06-01', '2026-12-31'), '2026-11-07')).toBe('本期至今 對 上期同期、對 去年同期');
  });
});

describe('month average and bucket labels (D4, D7)', () => {
  it('averages the months with data through the last completed month', () => {
    const series = ['2025-12', '2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09', '2026-10', '2026-11']
      .map((m, i) => ({ start: `${m}-01`, value: [0, 0, 0, 0, 0, 0, 0, 0, 31200, 39608, 41890, 4233][i] }));
    expect(monthAverage(series, '2026-11-07')).toEqual({ months: 3, average: 37566 });
  });

  it('counts a month on its last day as completed', () => {
    const series = ['2025-12', '2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09', '2026-10', '2026-11']
      .map((m, i) => ({ start: `${m}-01`, value: [0, 0, 0, 0, 0, 0, 0, 0, 31200, 39608, 41890, 4233][i] }));
    expect(monthAverage(series, '2026-11-30')).toEqual({ months: 4, average: Math.round((31200 + 39608 + 41890 + 4233) / 4) });
  });

  it('has no average when every month is zero', () =>
    expect(monthAverage([{ start: '2026-09-01', value: 0 }, { start: '2026-10-01', value: 0 }], '2026-11-07')).toBeNull());

  it('has no average for a ledger younger than one completed month', () =>
    expect(monthAverage([{ start: '2026-11-01', value: 4233 }], '2026-11-07')).toBeNull());

  it('formats bucket keys', () => {
    expect(bucketText('2026-09', 'month', false)).toBe('9 月');
    expect(bucketText('2025-09-01', 'month', true)).toBe('2025 年 9 月');
    expect(bucketText('2026-10-05', 'week', false)).toBe('10/5 週');
    expect(bucketText('2025-10-05', 'day', true)).toBe('2025/10/5');
    expect(bucketText('2025-10-05', 'week', true)).toBe('2025/10/5 週');
    expect(bucketText('2026-10-05', 'day', false)).toBe('10/5');
  });

  it('puts the year on the first axis label and each January only (R1)', () => {
    expect(axisParts('2025-11-01', 0)).toEqual({ month: '11 月', year: '2025' });
    expect(axisParts('2025-12-01', 1)).toEqual({ month: '12 月', year: null });
    expect(axisParts('2026-01-01', 2)).toEqual({ month: '1 月', year: '2026' });
    expect(axisParts('2026-09-01', 10)).toEqual({ month: '9 月', year: null });
  });

  it('marks only a bucket that has started and ends after today as running', () => {
    expect(runningBucket('2026-11', 'month', '2026-11-07')).toBe(true);
    expect(runningBucket('2026-11', 'month', '2026-11-30')).toBe(false);
    expect(runningBucket('2026-11-02', 'week', '2026-11-07')).toBe(true);
    expect(runningBucket('2026-11-02', 'week', '2026-11-08')).toBe(false);
    expect(runningBucket('2026-11-07', 'day', '2026-11-07')).toBe(false);
    expect(`11 月 ${runningSuffix('2026-11-07')}`).toBe('11 月 進行中（至 11/7）');
  });
});

describe('hatch stripes (no url(#id) paint under <base href>)', () => {
  it('returns nothing for an empty rectangle', () => {
    expect(hatchLines(10, 10, 0, 50)).toEqual([]);
    expect(hatchLines(10, 10, 20, 0)).toEqual([]);
  });

  it('clips every stripe to the rectangle and spaces the stripes gap apart', () => {
    const lines = hatchLines(20, 30, 12, 100);
    expect(lines).toHaveLength(19); // multiples of 6 strictly inside (-110, 2)
    for (const l of lines) {
      for (const [px, py] of [[l.x1, l.y1], [l.x2, l.y2]]) {
        expect(px).toBeGreaterThanOrEqual(20 - 1e-9);
        expect(px).toBeLessThanOrEqual(32 + 1e-9);
        expect(py).toBeGreaterThanOrEqual(30 - 1e-9);
        expect(py).toBeLessThanOrEqual(130 + 1e-9);
      }
      expect(l.x2 - l.x1).toBeCloseTo(l.y2 - l.y1, 9);
      expect(l.x2).toBeGreaterThan(l.x1);
    }
    const offsets = lines.map(l => l.x1 - l.y1);
    for (let i = 1; i < offsets.length; i++) expect(offsets[i] - offsets[i - 1]).toBeCloseTo(6, 9);
    expect(hatchLines(0, 0, 12, 100)).toHaveLength(18);
    expect(hatchLines(0, 0, 12, 100, 12)).toHaveLength(9);
  });
});

describe('running forecast (solid to date, dashed 預估)', () => {
  const NOV = { start: '2026-11-01', end: '2026-11-30' };

  it('adds the typical figure times the share of days still ahead', () => {
    const forecast = runningForecast({ toDate: 12300, typical: 36000, ...NOV, today: '2026-11-08' });
    expect(forecast).toEqual({ value: 38700, toDate: 12300, typical: 36000, after: 22, days: 30, floored: false });
  });

  it('never drops below what is already recorded for the bucket (entries dated after today)', () => {
    const forecast = runningForecast({ toDate: 12300, typical: 36000, ...NOV, today: '2026-11-08', recorded: 45000 });
    expect(forecast).toMatchObject({ value: 45000, floored: true });
    expect(runningForecast({ toDate: 12300, typical: 36000, ...NOV, today: '2026-11-08', recorded: 20000 })).toMatchObject({ value: 38700, floored: false });
  });

  it('counts the days of a bucket clipped to the period', () => {
    // A week 11/2–11/8 cut at a period ending 11/5: on 11/3, 2 of 4 days are ahead.
    expect(runningForecast({ toDate: 100, typical: 400, start: '2026-11-02', end: '2026-11-05', today: '2026-11-03' })).toMatchObject({ value: 300, after: 2, days: 4 });
  });

  it('has no forecast without a positive typical figure or without days ahead', () => {
    expect(runningForecast({ toDate: 100, typical: null, ...NOV, today: '2026-11-08' })).toBeNull();
    expect(runningForecast({ toDate: 100, typical: 0, ...NOV, today: '2026-11-08' })).toBeNull();
    expect(runningForecast({ toDate: 100, typical: -50, ...NOV, today: '2026-11-08' })).toBeNull();
    expect(runningForecast({ toDate: 100, typical: 400, ...NOV, today: '2026-11-30' })).toBeNull();
  });

  it('explains the forecast in one muted line', () => {
    const paced = runningForecast({ toDate: 12300, typical: 36000, ...NOV, today: '2026-11-08' })!;
    expect(forecastNote('11 月', paced, '2026-11-08', dollars)).toBe('11 月預估 $38,700 ＝ 至 11/8 $12,300 ＋ 平均 $36,000 × 剩 22/30 天');
    const floored = runningForecast({ toDate: 12300, typical: 36000, ...NOV, today: '2026-11-08', recorded: 45000 })!;
    expect(forecastNote('11 月', floored, '2026-11-08', dollars)).toBe('11 月預估 $45,000（含 11/8 之後已記的帳）');
    const counted = runningForecast({ toDate: 12, typical: 30, ...NOV, today: '2026-11-08' })!;
    expect(forecastNote('11 月', counted, '2026-11-08', (n: number) => `${n0(n)} 筆`)).toBe('11 月預估 34 筆 ＝ 至 11/8 12 筆 ＋ 平均 30 筆 × 剩 22/30 天');
  });
});
