import {
  BreakdownQuery,
  BreakdownResponse,
  BreakdownRow,
  BreakdownSplit,
  BudgetInput,
  BudgetStatus,
  BudgetStatusResponse,
  SavedReport,
  SavedReportInput,
  TREND_ALL,
  TrendQuery,
  TrendResponse,
} from '../../../models/reports.model';
import { compareRange, daysInMonth, elapsedDays, monthOf, monthPeriod, num, shiftMonth } from './report-math';

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

/** 帳戶 and 對象 rows are keyed by id (their position in the sample list); 商家 and 標籤 by the name itself. */
const ID_KEYED = new Set<string>(['account', 'counterparty']);

function dimensionRow([label, amount, count, refund]: [string | null, number, number, number?], index: number, query: BreakdownQuery): BreakdownRow {
  const isCoffee = label === '路易莎';
  return {
    key: label === null || label === '__other__' ? label : ID_KEYED.has(query.group_by) ? String(index + 1) : label,
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

/** The two sample projects (專案與預算): 東京旅行 is 不計入報表 and outside the month's figures. */
interface SampleProject {
  id: number;
  name: string;
  count: number;
  first: string;
  /** null = still running (the query's last day). */
  last: string | null;
  budget: number;
  segments: [string, number][];
}

const PROJECTS: SampleProject[] = [
  { id: 1, name: '東京旅行', count: 23, first: '2026-10-10', last: '2026-10-15', budget: 25000, segments: [['機票', 9800], ['住宿', 8400], ['餐飲', 3960], ['交通', 1420], ['購物', 1280]] },
  { id: 2, name: '客廳整理', count: 9, first: '2026-09-01', last: null, budget: 20000, segments: [['家具', 12800], ['收納', 2350], ['燈具', 1690]] },
];

const projectTotal = (p: SampleProject) => sum(p.segments.map(([, amount]) => amount));

function projectRow(p: SampleProject, query: BreakdownQuery): BreakdownRow {
  const total = projectTotal(p);
  return {
    key: String(p.id),
    label: p.name,
    color: null,
    amount: text(total),
    refund: '0',
    count: p.count,
    avg: text(total / p.count),
    median: null,
    min: null,
    max: null,
    first_date: p.first,
    last_date: p.last ?? query.date_to,
    avg_interval_days: null,
    compare_amount: query.compare ? '0' : undefined,
    splits: query.split_by ? p.segments.map(([label, amount], index) => ({ key: String(p.id * 100 + index + 1), label, amount: text(amount), count: Math.max(1, Math.round(p.count / p.segments.length)) })) : undefined,
  };
}

/** The sample answer to a breakdown: the same month whatever the dates, which are echoed back. */
export function sampleBreakdown(query: BreakdownQuery): BreakdownResponse {
  const metric = query.metric ?? 'expense';
  const income = metric === 'income';
  const categories = income ? INCOME : EXPENSE;
  const dimension = DIMENSIONS[query.group_by];
  const saved = savedReportRows(query);
  const rows = saved
    ? saved
    : query.group_by === 'project'
      ? [
          ...PROJECTS.filter(p => query.include_excluded_projects || p.id !== 1).map(p => projectRow(p, query)),
          { ...categoryRow({ id: 0, name: '', count: 78, prev: 0, ly: 0, refund: 490, subs: [['（無專案）', 25540, 78]] }, { ...query, split_by: undefined }), key: null, label: '' },
        ]
      : query.group_by === 'category' || query.group_by === 'subcategory' || !dimension
        ? categories.map(c => categoryRow(c, query))
        : dimension.rows.map((row, index) => dimensionRow(row, index, query));
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
  const rowTotal = saved ? sum(saved.map(row => num(row.amount))) : null;
  const rowCompare = saved ? sum(saved.map(row => num(row.compare_amount))) : null;
  return {
    currency: 'TWD',
    date_from: query.date_from,
    date_to: query.date_to,
    totals: saved
      ? { expense: text(rowTotal!), income: '0', net: text(-rowTotal!), count: sum(saved.map(row => row.count)), refund: '0' }
      : {
      expense: text(SAMPLE_EXPENSE),
      income: text(SAMPLE_INCOME),
      net: text(SAMPLE_INCOME - SAMPLE_EXPENSE),
      count: sum(categories.map(c => c.count)),
      refund: text(SAMPLE_REFUND),
    },
    compare: compare && saved ? { ...compare, totals: { expense: text(rowCompare!) } } : compare,
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

/** The six-month shape the canvas draws for a ranked row, around its month amount. */
const ROW_SHAPE = [0.85, 1.08, 0.91, 0.98, 0.93, 1];

/** A ranked row's trend (the 排行 panel): the row's amount under the canvas shape, in the last buckets. */
function rowSeries(query: TrendQuery): number[] | null {
  const dimension = query.merchant ? 'merchant' : query.tag?.length ? 'tag' : query.counterparty_id != null ? 'counterparty' : query.account_id?.length ? 'account' : null;
  if (!dimension) return null;
  const rows = DIMENSIONS[dimension]?.rows ?? [];
  const label = query.merchant ?? query.tag?.[0] ?? null;
  const id = query.counterparty_id ?? query.account_id?.[0] ?? null;
  const row = label !== null ? rows.find(([name]) => name === label) : id !== null ? rows[id - 1] : undefined;
  const amount = row ? row[1] : 0;
  const shape = TREND_SERIES[TREND_ALL].map((_, index, list) => ROW_SHAPE[index - (list.length - ROW_SHAPE.length)] ?? 0.9);
  return shape.map(factor => Math.round(amount * factor));
}

/** The sample trend: twelve monthly buckets ending at the query's last month (shorter ranges keep the tail). */
export function sampleTrend(query: TrendQuery): TrendResponse {
  const last = monthOf(query.date_to);
  const keys = query.series?.length ? query.series.map(String) : [TREND_ALL];
  const filtered = rowSeries(query);
  const series = Object.fromEntries(keys.map(key => [key, filtered ?? TREND_SERIES[key] ?? derivedSeries(Number(key))]));
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

// ---- budgets ------------------------------------------------------------------------------------------------------

interface SampleBudget {
  id: number;
  scope: BudgetStatus['scope'];
  category_id: number | null;
  project_id: number | null;
  label: string;
  amount: number;
  effective_from: string;
}

/** The canvas's October budgets: four categories, 全部支出 and one per project. */
const BUDGET_SEEDS: SampleBudget[] = [
  { id: 1, scope: 'category', category_id: 2, project_id: null, label: '餐飲', amount: 10000, effective_from: '2026-01' },
  { id: 2, scope: 'category', category_id: 3, project_id: null, label: '交通', amount: 5000, effective_from: '2026-01' },
  { id: 3, scope: 'category', category_id: 4, project_id: null, label: '購物', amount: 3000, effective_from: '2026-09' },
  { id: 4, scope: 'category', category_id: 5, project_id: null, label: '娛樂', amount: 2500, effective_from: '2026-01' },
  { id: 5, scope: 'total', category_id: null, project_id: null, label: '全部支出', amount: 45000, effective_from: '2026-01' },
  { id: 6, scope: 'project', category_id: null, project_id: 1, label: '東京旅行', amount: 25000, effective_from: '2026-10' },
  { id: 7, scope: 'project', category_id: null, project_id: 2, label: '客廳整理', amount: 20000, effective_from: '2026-09' },
];

/** What the sample month spent against a budget: the category's net, the month's expense, or the project's life. */
function sampleUsed(budget: SampleBudget): number {
  if (budget.scope === 'total') return SAMPLE_EXPENSE;
  if (budget.scope === 'category') {
    const category = EXPENSE.find(c => c.id === budget.category_id);
    return category ? net(category) : 0;
  }
  const project = PROJECTS.find(p => p.id === budget.project_id);
  return project ? projectTotal(project) : 0;
}

/**
 * The budget endpoints on the sample: status for a month, and writes kept in memory for the session so 編輯預算
 * can be tried before the API exists.
 */
export class SampleBudgetStore {
  private budgets: SampleBudget[] = BUDGET_SEEDS.map(b => ({ ...b }));
  private nextId = BUDGET_SEEDS.length + 1;

  status(month: string, today: string): BudgetStatusResponse {
    const period = monthPeriod(month);
    // 0 before the month, 1 once it is over, the share of its days gone (today included) in between.
    const progress = today < period.from ? 0 : today > period.to ? 1 : elapsedDays(period, today).days / daysInMonth(month);
    return {
      month,
      currency: 'TWD',
      time_progress: Math.round(progress * 1000) / 1000,
      budgets: this.budgets.filter(b => b.effective_from <= month).map(b => this.toStatus(b)),
    };
  }

  create(input: BudgetInput): BudgetStatus {
    const label =
      input.scope === 'total'
        ? '全部支出'
        : input.scope === 'category'
          ? (EXPENSE.find(c => c.id === input.category_id)?.name ?? `類別 ${input.category_id}`)
          : (PROJECTS.find(p => p.id === input.project_id)?.name ?? `專案 ${input.project_id}`);
    const budget: SampleBudget = {
      id: this.nextId++,
      scope: input.scope,
      category_id: input.category_id ?? null,
      project_id: input.project_id ?? null,
      label,
      amount: Number(input.amount),
      effective_from: input.effective_from,
    };
    this.budgets.push(budget);
    return this.toStatus(budget);
  }

  update(id: number, input: Pick<BudgetInput, 'amount' | 'effective_from'>): BudgetStatus {
    const budget = this.budgets.find(b => b.id === id);
    if (!budget) throw new Error(`no sample budget ${id}`);
    budget.amount = Number(input.amount);
    budget.effective_from = input.effective_from;
    return this.toStatus(budget);
  }

  remove(id: number): void {
    this.budgets = this.budgets.filter(b => b.id !== id);
  }

  private toStatus(budget: SampleBudget): BudgetStatus {
    const used = sampleUsed(budget);
    return {
      id: budget.id,
      scope: budget.scope,
      category_id: budget.category_id,
      project_id: budget.project_id,
      label: budget.label,
      color: null,
      limit: text(budget.amount),
      used: text(used),
      remaining: text(budget.amount - used),
      ratio: budget.amount > 0 ? Math.round((used / budget.amount) * 10000) / 10000 : 0,
      over: used > budget.amount,
      effective_from: budget.effective_from,
    };
  }
}

// ---- saved reports ---------------------------------------------------------------------------------------------------

/** The canvas's three saved reports (ReportBuilder): two pinned, one not. */
const SAVED_SEEDS: SavedReport[] = [
  {
    id: 1, name: '週末外食', chart: 'bar', pinned: true, sort_order: 0,
    config: { v: 1, period: { preset: 'last_6_months' }, metric: 'expense', group_by: 'month', split_by: 'subcategory', filters: { category_id: [2], weekday: [6, 7] }, compare: 'last_year' },
  },
  {
    id: 2, name: '訂閱月費', chart: 'bar', pinned: true, sort_order: 1,
    config: { v: 1, period: { preset: 'this_year' }, metric: 'expense', group_by: 'merchant', filters: { category_id: [6] }, compare: 'last_year' },
  },
  {
    id: 3, name: '日本旅行比較', chart: 'bar', pinned: false, sort_order: 2,
    config: { v: 1, period: { preset: 'all' }, metric: 'expense', group_by: 'project', split_by: 'category', filters: { project_id: [3, 4, 1] } },
  },
];

/** `/reports/saved` on the sample: the three reports, writes kept in memory for the session. */
export class SampleSavedReportStore {
  private reports: SavedReport[] = SAVED_SEEDS.map(r => ({ ...r, config: structuredClone(r.config) }));
  private nextId = SAVED_SEEDS.length + 1;

  list(): SavedReport[] {
    return [...this.reports].sort((a, b) => a.sort_order - b.sort_order).map(r => structuredClone(r));
  }

  get(id: number): SavedReport {
    const report = this.reports.find(r => r.id === id);
    if (!report) throw new Error(`no sample saved report ${id}`);
    return structuredClone(report);
  }

  create(input: SavedReportInput): SavedReport {
    const report: SavedReport = { id: this.nextId++, sort_order: this.reports.length, ...input, config: structuredClone(input.config) };
    this.reports.push(report);
    return structuredClone(report);
  }

  update(id: number, input: SavedReportInput): SavedReport {
    const report = this.get(id);
    const next = { ...report, ...input, config: structuredClone(input.config) };
    this.reports = this.reports.map(r => (r.id === id ? next : r));
    return structuredClone(next);
  }

  remove(id: number): void {
    this.reports = this.reports.filter(r => r.id !== id);
  }
}

// ---- breakdowns the saved reports ask for -----------------------------------------------------------------------------

/** 週末外食: six months of 晚餐 / 午餐 / 宵夜 on weekends, and last year's totals. */
const WEEKEND = {
  keys: ['晚餐', '午餐', '宵夜'],
  data: [[1820, 980, 320], [1690, 870, 300], [2140, 1050, 350], [1760, 920, 300], [1410, 780, 220], [1880, 910, 260]],
  compare: [2610, 2930, 3380, 3120, 2550, 3290],
  counts: [9, 8, 11, 9, 7, 9],
};
/** 訂閱月費: the year's subscriptions by merchant, with last year's. */
const SUBSCRIPTIONS: [string, number, number][] = [['Netflix', 3900, 3900], ['Notion', 2500, 0], ['YouTube', 1990, 1990], ['Spotify', 1490, 1490], ['iCloud', 900, 900]];
/** 日本旅行比較: three trips split by category. */
const JAPAN: { id: number; name: string; count: number; first: string; last: string; segments: [string, number][] }[] = [
  { id: 3, name: '京都 2024', count: 31, first: '2024-11-02', last: '2024-11-07', segments: [['機票', 8900], ['住宿', 7200], ['餐飲', 3100], ['交通', 1100], ['購物', 2600]] },
  { id: 4, name: '大阪 2025', count: 36, first: '2025-04-12', last: '2025-04-18', segments: [['機票', 9400], ['住宿', 6800], ['餐飲', 3700], ['交通', 1300], ['購物', 3900]] },
  { id: 1, name: '東京旅行', count: 38, first: '2026-10-10', last: '2026-10-15', segments: [['機票', 9800], ['住宿', 8400], ['餐飲', 3960], ['交通', 1420], ['購物', 1280]] },
];

function plainRow(key: string | null, label: string, amount: number, count: number, query: BreakdownQuery, extra: Partial<BreakdownRow> = {}): BreakdownRow {
  return {
    key, label, color: null, amount: text(amount), refund: '0', count, avg: text(count ? amount / count : 0),
    median: null, min: null, max: null, first_date: query.date_from, last_date: query.date_to, avg_interval_days: null, ...extra,
  };
}

/**
 * The breakdowns behind the three sample saved reports (and any custom report grouped by month): months carry the
 * weekend meal data, a merchant breakdown filtered to 訂閱 the subscriptions, a project breakdown filtered to
 * projects the three trips. Returns null for anything else, so `sampleBreakdown` falls through to its own rows.
 */
function savedReportRows(query: BreakdownQuery): BreakdownRow[] | null {
  if (query.group_by === 'month') {
    const last = monthOf(query.date_to);
    const first = monthOf(query.date_from);
    const months: string[] = [];
    for (let month = first; month <= last && months.length < 24; month = shiftMonth(month, 1)) months.push(month);
    const tail = months.slice(-WEEKEND.data.length);
    return months.map(month => {
      const index = tail.indexOf(month);
      const data = index === -1 ? [0, 0, 0] : WEEKEND.data[index];
      const amount = sum(data);
      return plainRow(`${month}-01`, `${Number(month.slice(5, 7))}月`, amount, index === -1 ? 0 : WEEKEND.counts[index], query, {
        first_date: `${month}-01`, last_date: monthPeriod(month).to,
        compare_amount: query.compare ? text(index === -1 ? 0 : WEEKEND.compare[index]) : undefined,
        splits: query.split_by ? WEEKEND.keys.map((label, k) => ({ key: String(200 + k), label, amount: text(data[k]), count: Math.round((index === -1 ? 0 : WEEKEND.counts[index]) / 3) })) : undefined,
      });
    });
  }
  if (query.group_by === 'merchant' && query.category_id?.includes(6)) {
    return SUBSCRIPTIONS.map(([name, amount, lastYear]) => plainRow(name, name, amount, 10, query, { compare_amount: query.compare ? text(lastYear) : undefined }));
  }
  if (query.group_by === 'project' && query.project_id?.length) {
    return JAPAN.filter(p => query.project_id!.includes(p.id)).map(p =>
      plainRow(String(p.id), p.name, sum(p.segments.map(([, amount]) => amount)), p.count, query, {
        first_date: p.first, last_date: p.last,
        splits: query.split_by ? p.segments.map(([label, amount], index) => ({ key: String(index + 1), label, amount: text(amount), count: Math.round(p.count / 5) })) : undefined,
      }),
    );
  }
  return null;
}
