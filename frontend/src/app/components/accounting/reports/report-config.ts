import { CategoryNode, LedgerAccount, Project } from '../../../models/accounting.model';
import {
  BreakdownQuery,
  PeriodPreset,
  ReportChart,
  ReportCompare,
  ReportConfig,
  ReportDimension,
  ReportFilters,
  ReportMetric,
  ReportPeriodConfig,
} from '../../../models/reports.model';
import { customPeriod, monthOf, monthPeriod, shiftMonth, yearPeriod, ReportPeriod } from './report-math';

/**
 * Rules of a saved report's `config` (openspec `add-accounting-reports` D4 / D6): presets resolve to dates on every
 * open, chart types fit the grouping or are disabled with a reason, and a config reads back as one sentence.
 */

export const METRIC_LABELS: Record<ReportMetric, string> = { expense: '支出', income: '收入', net: '結餘', count: '筆數' };

export const DIMENSION_LABELS: Record<ReportDimension, string> = {
  category: '類別',
  subcategory: '子類別',
  account: '帳戶',
  project: '專案',
  merchant: '商家',
  tag: '標籤',
  counterparty: '對象',
  month: '月份',
  week: '週',
  weekday: '星期',
  day: '日',
};

export const PRESET_LABELS: Record<PeriodPreset, string> = {
  this_month: '本月',
  last_month: '上月',
  last_6_months: '近 6 個月',
  last_12_months: '近 12 個月',
  this_year: '今年',
  last_year: '去年',
  all: '全部',
};

export const PRESETS: readonly PeriodPreset[] = ['this_month', 'last_month', 'last_6_months', 'last_12_months', 'this_year', 'last_year', 'all'];

export const CHART_LABELS: Record<ReportChart, string> = { bar: '長條', line: '折線', donut: '圓環', table: '只看表格' };

export const COMPARE_LABELS: Record<ReportCompare, string> = { previous: '上一期', last_year: '去年同期' };

export const WEEKDAY_LABELS = ['一', '二', '三', '四', '五', '六', '日'];

/** The dimensions a report may group or split by (the time dimensions last). */
export const GROUP_DIMENSIONS: readonly ReportDimension[] = ['category', 'subcategory', 'account', 'project', 'merchant', 'tag', 'counterparty', 'month', 'week', 'weekday', 'day'];

const TIME_DIMENSIONS = new Set<ReportDimension>(['month', 'week', 'day']);

/** The first day the ledger could hold: `all` starts here. */
const BEGINNING = '2000-01-01';

/** An empty config: nothing chosen yet (the editor's empty state). */
export function emptyConfig(): ReportConfig {
  return { v: 1, period: { preset: 'this_month' }, metric: 'expense', group_by: 'category', filters: {} };
}

export function isPreset(period: ReportPeriodConfig): period is { preset: PeriodPreset } {
  return 'preset' in period;
}

/** The dates a period config covers as of `today`: presets roll forward, fixed dates stay. */
export function resolvePeriod(period: ReportPeriodConfig, today: string): ReportPeriod {
  if (!isPreset(period)) return customPeriod(period.from, period.to);
  const month = monthOf(today);
  const year = Number(today.slice(0, 4));
  switch (period.preset) {
    case 'this_month':
      return monthPeriod(month);
    case 'last_month':
      return monthPeriod(shiftMonth(month, -1));
    case 'last_6_months':
      return { unit: 'custom', from: `${shiftMonth(month, -5)}-01`, to: monthPeriod(month).to };
    case 'last_12_months':
      return { unit: 'custom', from: `${shiftMonth(month, -11)}-01`, to: monthPeriod(month).to };
    case 'this_year':
      return yearPeriod(year);
    case 'last_year':
      return yearPeriod(year - 1);
    case 'all':
      return { unit: 'custom', from: BEGINNING, to: today };
  }
}

export interface ChartOption {
  chart: ReportChart;
  label: string;
  /** Why the chart does not fit the grouping; null when it does. */
  reason: string | null;
}

/** Design D6: 圓環 only with one dimension, no split and not a time dimension; 折線 only for time dimensions. */
export function chartOptions(config: Pick<ReportConfig, 'group_by' | 'split_by'>): ChartOption[] {
  const time = TIME_DIMENSIONS.has(config.group_by);
  return (['bar', 'line', 'donut', 'table'] as const).map(chart => {
    let reason: string | null = null;
    if (chart === 'donut' && config.split_by) reason = '有細分時不能用圓環';
    else if (chart === 'donut' && time) reason = '依月份、週、日分組時不能用圓環';
    else if (chart === 'line' && !time) reason = '只有依月份、週、日分組才能用折線';
    return { chart, label: CHART_LABELS[chart], reason };
  });
}

/** The chart to show: the saved one when it fits, else the first that does. */
export function fittingChart(config: Pick<ReportConfig, 'group_by' | 'split_by'>, chart: ReportChart): ReportChart {
  const options = chartOptions(config);
  return options.find(option => option.chart === chart && !option.reason)?.chart ?? options.find(option => !option.reason)!.chart;
}

/** Reference lists the filter chips name ids with. */
export interface ReferenceLists {
  categories: CategoryNode[];
  accounts: LedgerAccount[];
  projects: Project[];
}

export interface FilterChipView {
  /** The `ReportFilters` key the chip removes. */
  key: keyof ReportFilters;
  label: string;
  value: string;
}

function categoryName(categories: CategoryNode[], id: number): string {
  for (const root of categories) {
    if (root.id === id) return root.name;
    const child = root.children.find(c => c.id === id);
    if (child) return `${root.name} › ${child.name}`;
  }
  return `#${id}`;
}

/** `類別：餐飲 › 午餐、晚餐` … one chip per filter key, ids named through the reference lists. */
export function filterChips(filters: ReportFilters, refs: ReferenceLists): FilterChipView[] {
  const chips: FilterChipView[] = [];
  if (filters.account_id?.length) chips.push({ key: 'account_id', label: '帳戶', value: filters.account_id.map(id => refs.accounts.find(a => a.id === id)?.name ?? `#${id}`).join('、') });
  if (filters.category_id?.length) chips.push({ key: 'category_id', label: '類別', value: filters.category_id.map(id => categoryName(refs.categories, id)).join('、') });
  if (filters.project_id?.length) chips.push({ key: 'project_id', label: '專案', value: filters.project_id.map(id => refs.projects.find(p => p.id === id)?.name ?? `#${id}`).join('、') });
  if (filters.tag?.length) chips.push({ key: 'tag', label: '標籤', value: filters.tag.join('、') });
  if (filters.merchant) chips.push({ key: 'merchant', label: '商家', value: filters.merchant });
  if (filters.counterparty_id != null) chips.push({ key: 'counterparty_id', label: '對象', value: `#${filters.counterparty_id}` });
  if (filters.weekday?.length) chips.push({ key: 'weekday', label: '星期', value: filters.weekday.map(d => WEEKDAY_LABELS[d - 1] ?? String(d)).join('、') });
  if (filters.amount_min || filters.amount_max) chips.push({ key: 'amount_min', label: '金額', value: `${filters.amount_min ?? ''} – ${filters.amount_max ?? ''}` });
  if (filters.q) chips.push({ key: 'q', label: '關鍵字', value: filters.q });
  return chips;
}

/** `近 6 個月 · 支出 · 依月份 · 細分到子類別 · 類別 餐飲 › 午餐、晚餐 且 星期 六、日 · 和去年同期比較`. */
export function describeConfig(config: ReportConfig, refs: ReferenceLists): string {
  const period = isPreset(config.period) ? PRESET_LABELS[config.period.preset] : `${config.period.from.replaceAll('-', '/')} – ${config.period.to.replaceAll('-', '/')}`;
  const parts = [period, METRIC_LABELS[config.metric], `依${DIMENSION_LABELS[config.group_by]}`];
  if (config.split_by) parts.push(`細分到${DIMENSION_LABELS[config.split_by]}`);
  const chips = filterChips(config.filters, refs);
  if (chips.length) parts.push(chips.map(chip => `${chip.label} ${chip.value}`).join(' 且 '));
  if (config.compare) parts.push(`和${COMPARE_LABELS[config.compare]}比較`);
  return parts.join(' · ');
}

/** The breakdown a config asks for as of `today`; a report that filters on projects also sees 不計入報表 ones. */
export function configQuery(config: ReportConfig, today: string): BreakdownQuery {
  const period = resolvePeriod(config.period, today);
  return {
    date_from: period.from,
    date_to: period.to,
    metric: config.metric,
    group_by: config.group_by,
    split_by: config.split_by,
    compare: config.compare,
    ...config.filters,
    include_excluded_projects: config.filters.project_id?.length ? true : undefined,
  };
}

/** A config with a filter key removed (amount removes both bounds). */
export function withoutFilter(filters: ReportFilters, key: keyof ReportFilters): ReportFilters {
  const next = { ...filters };
  delete next[key];
  if (key === 'amount_min') delete next.amount_max;
  return next;
}

/** The config is complete enough to run: a grouping that differs from the split. */
export function configValid(config: ReportConfig): boolean {
  return !!config.group_by && config.split_by !== config.group_by;
}

/** The editor's three templates (the canvas's saved reports), as configs the owner can start from. */
export const TEMPLATES: readonly { name: string; config: ReportConfig; chart: ReportChart }[] = [
  {
    name: '週末外食',
    chart: 'bar',
    config: { v: 1, period: { preset: 'last_6_months' }, metric: 'expense', group_by: 'month', split_by: 'subcategory', filters: { category_id: [2], weekday: [6, 7] }, compare: 'last_year' },
  },
  {
    name: '訂閱月費',
    chart: 'bar',
    config: { v: 1, period: { preset: 'this_year' }, metric: 'expense', group_by: 'merchant', filters: { category_id: [6] }, compare: 'last_year' },
  },
  {
    name: '日本旅行比較',
    chart: 'bar',
    config: { v: 1, period: { preset: 'all' }, metric: 'expense', group_by: 'project', split_by: 'category', filters: { project_id: [3, 4, 1] } },
  },
];
