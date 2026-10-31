import {
  BreakdownQuery,
  BreakdownResponse,
  BreakdownRow,
  BreakdownSplit,
  TREND_ALL,
  TrendQuery,
  TrendResponse,
} from '../../../models/reports.model';
import { compareRange, monthOf, shiftMonth } from './report-math';

/**
 * The handoff's sample month (`design_handoff_v3/README.md`, "Sample data"), shaped like the `/reports/*` answers.
 * Shown while the accounting service has no report endpoints yet (`ReportsService` falls back to it on 404) so the
 * page can be reviewed against the canvas; every figure is invented and internally consistent (gross expense 42,380,
 * refund 490, expense 41,890; income 68,126; previous period 39,608; same period last year 44,050).
 */

interface SampleCategory {
  id: number;
  name: string;
  count: number;
  /** Net amount in the previous period / the same period last year. */
  prev: number;
  ly: number;
  refund: number;
  subs: [string, number, number][];
}

const EXPENSE: SampleCategory[] = [
  { id: 1, name: '居家', count: 3, prev: 20180, ly: 19650, refund: 0, subs: [['房租', 18000, 1], ['電費', 1830, 1], ['水費', 420, 1]] },
  { id: 2, name: '餐飲', count: 46, prev: 8640, ly: 10320, refund: 0, subs: [['晚餐', 4280, 11], ['午餐', 3120, 19], ['咖啡', 1035, 12], ['早餐', 845, 9], ['宵夜', 580, 5]] },
  { id: 3, name: '交通', count: 15, prev: 3350, ly: 4880, refund: 0, subs: [['加油', 2480, 2], ['Uber', 940, 4], ['捷運', 690, 9]] },
  { id: 4, name: '購物', count: 9, prev: 3410, ly: 5230, refund: 490, subs: [['日用品', 2390, 7], ['衣服', 1590, 2]] },
  { id: 5, name: '娛樂', count: 5, prev: 1620, ly: 1940, refund: 0, subs: [['KTV', 1330, 2], ['電影', 720, 3]] },
  { id: 6, name: '訂閱', count: 5, prev: 1078, ly: 880, refund: 0, subs: [['Netflix', 390, 1], ['Notion', 250, 1], ['YouTube', 199, 1], ['Spotify', 149, 1], ['iCloud', 90, 1]] },
  { id: 7, name: '其他', count: 4, prev: 1330, ly: 1150, refund: 0, subs: [['學習', 532, 2], ['醫療', 520, 2]] },
];

const INCOME: SampleCategory[] = [
  { id: 21, name: '薪資', count: 1, prev: 68000, ly: 65000, refund: 0, subs: [['月薪', 68000, 1]] },
  { id: 22, name: '回饋', count: 3, prev: 98, ly: 210, refund: 0, subs: [['現金回饋', 126, 3]] },
];

/** Other dimensions (排行): `[label, amount, count, refund?]`; a null label is the （無…）row. */
const DIMENSIONS: Partial<Record<string, { overlapping: boolean; rows: [string | null, number, number, number?][] }>> = {
  merchant: {
    overlapping: false,
    rows: [['房東', 18000, 1], ['鼎泰豐', 2940, 3], ['中油', 2480, 2], ['全聯', 2390, 7], ['台電', 1830, 1], [null, 1240, 9], ['UNIQLO', 1100, 3, 490], ['路易莎', 1035, 12], ['__other__', 10875, 49]],
  },
  tag: { overlapping: true, rows: [[null, 31370, 62], ['家用', 6420, 14], ['朋友', 2860, 6], ['出差', 1240, 3], ['生日', 980, 2]] },
  counterparty: { overlapping: false, rows: [[null, 39210, 84], ['媽媽', 1880, 2], ['陳小明', 800, 1]] },
  account: { overlapping: false, rows: [['玉山銀行', 20250, 4], ['國泰 CUBE 卡', 13740, 41, 490], ['錢包', 3410, 25], ['玉山 Pi 卡', 2390, 7], ['LINE Pay', 2100, 10]] },
};

/** Twelve months ending at the sample month, for everything and for the categories the canvas draws. */
const TREND_SERIES: Record<string, number[]> = {
  [TREND_ALL]: [38900, 41700, 45300, 36800, 39500, 40200, 38100, 43600, 41000, 40700, 39608, 41890],
  2: [9200, 11800, 12400, 8100, 9300, 8900, 9700, 10100, 9400, 9050, 8640, 9860],
  3: [3900, 4200, 3600, 3100, 3800, 4600, 3300, 3700, 3500, 3900, 3350, 4110],
  4: [4100, 6900, 7800, 2600, 3900, 3500, 2900, 5800, 4300, 3700, 3410, 3490],
};

const gross = (c: SampleCategory) => c.subs.reduce((sum, [, amount]) => sum + amount, 0);
const net = (c: SampleCategory) => gross(c) - c.refund;
const sum = (list: readonly number[]) => list.reduce((a, b) => a + b, 0);

export const SAMPLE_EXPENSE = sum(EXPENSE.map(net));
export const SAMPLE_INCOME = sum(INCOME.map(net));
export const SAMPLE_REFUND = sum(EXPENSE.map(c => c.refund));
const PREV_EXPENSE = sum(EXPENSE.map(c => c.prev));
const LY_EXPENSE = sum(EXPENSE.map(c => c.ly));
const PREV_INCOME = sum(INCOME.map(c => c.prev));
const LY_INCOME = sum(INCOME.map(c => c.ly));

const text = (value: number) => String(Math.round(value));

function categoryRow(c: SampleCategory, query: BreakdownQuery): BreakdownRow {
  const amount = net(c);
  const splits: BreakdownSplit[] | undefined =
    query.split_by === 'subcategory'
      ? c.subs.map(([label, value, count], index) => ({ key: String(c.id * 100 + index + 1), label, amount: text(value), count }))
      : undefined;
  const amounts = c.subs.map(([, value]) => value);
  return {
    key: String(c.id),
    label: c.name,
    color: null,
    amount: text(amount),
    refund: text(c.refund),
    count: c.count,
    avg: text(amount / c.count),
    median: text(amounts.sort((a, b) => a - b)[Math.floor(amounts.length / 2)]),
    min: text(Math.min(...amounts)),
    max: text(Math.max(...amounts)),
    first_date: query.date_from,
    last_date: query.date_to,
    avg_interval_days: c.count > 1 ? Math.round((30 / c.count) * 10) / 10 : null,
    compare_amount: query.compare ? text(query.compare === 'previous' ? c.prev : c.ly) : undefined,
    splits,
  };
}

function dimensionRow([label, amount, count, refund]: [string | null, number, number, number?], query: BreakdownQuery): BreakdownRow {
  const isCoffee = label === '路易莎';
  return {
    key: label,
    label: label ?? '',
    color: null,
    amount: text(amount),
    refund: text(refund ?? 0),
    count,
    avg: text(amount / count),
    median: isCoffee ? '85' : text(amount / count),
    min: isCoffee ? '65' : text(amount / count),
    max: isCoffee ? '120' : text(amount / count),
    first_date: query.date_from,
    last_date: query.date_to,
    avg_interval_days: isCoffee ? 2.5 : count > 1 ? Math.round((30 / count) * 10) / 10 : null,
    compare_amount: query.compare ? text(amount * (query.compare === 'previous' ? PREV_EXPENSE : LY_EXPENSE) / SAMPLE_EXPENSE) : undefined,
  };
}

/** The sample answer to a breakdown: the same month whatever the dates, which are echoed back. */
export function sampleBreakdown(query: BreakdownQuery): BreakdownResponse {
  const metric = query.metric ?? 'expense';
  const income = metric === 'income';
  const categories = income ? INCOME : EXPENSE;
  const dimension = DIMENSIONS[query.group_by];
  const rows =
    query.group_by === 'category' || query.group_by === 'subcategory' || !dimension
      ? categories.map(c => categoryRow(c, query))
      : dimension.rows.map(row => dimensionRow(row, query));
  const window = query.compare ? compareRange(query.date_from, query.date_to, query.compare) : null;
  const compare = window
    ? {
        date_from: window.from,
        date_to: window.to,
        totals: {
          expense: text(query.compare === 'previous' ? PREV_EXPENSE : LY_EXPENSE),
          income: text(query.compare === 'previous' ? PREV_INCOME : LY_INCOME),
        },
      }
    : null;
  return {
    currency: 'TWD',
    date_from: query.date_from,
    date_to: query.date_to,
    totals: {
      expense: text(SAMPLE_EXPENSE),
      income: text(SAMPLE_INCOME),
      net: text(SAMPLE_INCOME - SAMPLE_EXPENSE),
      count: sum(categories.map(c => c.count)),
      refund: text(SAMPLE_REFUND),
    },
    compare,
    rows,
    overlapping: dimension?.overlapping ?? false,
    missing_rates: [],
  };
}

/** A series for a category the canvas does not draw: everything scaled by the category's share, last two months exact. */
function derivedSeries(id: number): number[] {
  const category = EXPENSE.find(c => c.id === id);
  if (!category) return TREND_SERIES[TREND_ALL].map(() => 0);
  const share = net(category) / SAMPLE_EXPENSE;
  const series = TREND_SERIES[TREND_ALL].map(value => Math.round(value * share));
  series[series.length - 2] = category.prev;
  series[series.length - 1] = net(category);
  return series;
}

/** The sample trend: twelve monthly buckets ending at the query's last month (shorter ranges keep the tail). */
export function sampleTrend(query: TrendQuery): TrendResponse {
  const last = monthOf(query.date_to);
  const keys = query.series?.length ? query.series.map(String) : [TREND_ALL];
  const series = Object.fromEntries(keys.map(key => [key, TREND_SERIES[key] ?? derivedSeries(Number(key))]));
  const months = TREND_SERIES[TREND_ALL].map((_, index, list) => shiftMonth(last, index - (list.length - 1)));
  const first = monthOf(query.date_from);
  const buckets = months
    .map((month, index) => ({
      start: `${month}-01`,
      values: Object.fromEntries(keys.map(key => [key, text(series[key][index])])),
    }))
    .filter(bucket => monthOf(bucket.start) >= first);
  const average = Object.fromEntries(
    keys.map(key => {
      const values = buckets.map(bucket => Number(bucket.values[key]));
      return [key, text(values.length ? sum(values) / values.length : 0)];
    }),
  );
  return { currency: 'TWD', granularity: 'month', buckets, average };
}
