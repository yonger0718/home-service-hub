/**
 * `GET /reports/*` of the accounting service (openspec `add-accounting-reports`, design D1). The endpoints land in
 * their own PR; the shapes here follow the design's response examples so the page needs no change when they do.
 */

export type ReportMetric = 'expense' | 'income' | 'net' | 'count';

export type ReportDimension =
  | 'category'
  | 'subcategory'
  | 'account'
  | 'project'
  | 'merchant'
  | 'tag'
  | 'counterparty'
  | 'month'
  | 'week'
  | 'weekday'
  | 'day';

/** `previous`: the same length immediately before; `last_year`: the same dates a year earlier. */
export type ReportCompare = 'previous' | 'last_year';

export type TrendGranularity = 'month' | 'week' | 'day';

/** The `/entries` filters plus `weekday[]` (1 = Monday) and several `project_id[]`. */
export interface ReportFilters {
  account_id?: number[];
  category_id?: number[];
  project_id?: number[];
  tag?: string[];
  merchant?: string | null;
  counterparty_id?: number | null;
  amount_min?: string | null;
  amount_max?: string | null;
  q?: string | null;
  weekday?: number[];
}

export interface BreakdownQuery extends ReportFilters {
  /** Inclusive, by `entry_date`. */
  date_from: string;
  date_to: string;
  /** Default `expense`. */
  metric?: ReportMetric;
  group_by: ReportDimension;
  /** Any dimension but `group_by`. */
  split_by?: ReportDimension;
  compare?: ReportCompare;
  /** Set when the report filters on projects, so 不計入報表 projects still answer. */
  include_excluded_projects?: boolean;
}

export interface BreakdownSplit {
  key: string | null;
  label: string;
  amount: string;
  count: number;
}

export interface BreakdownRow {
  /** `null` = entries without the dimension (no merchant …); `__other__` = the fold of rows past 200. */
  key: string | null;
  label: string;
  /** The category's own colour where the dimension has one. */
  color: string | null;
  /** Positive magnitude in the main currency; for `expense` net of the refunds in the row. */
  amount: string;
  /** The refunds inside the row (positive), so gross = `amount + refund`. */
  refund: string;
  count: number;
  avg: string;
  /** Statistics are computed only when the group count is ≤ 50; null otherwise. */
  median: string | null;
  min: string | null;
  max: string | null;
  first_date: string | null;
  last_date: string | null;
  avg_interval_days: number | null;
  /** The same row in the comparison period, when `compare` was sent. */
  compare_amount?: string | null;
  splits?: BreakdownSplit[];
}

export interface BreakdownTotals {
  expense: string;
  income: string;
  net: string;
  count: number;
  refund: string;
}

export interface BreakdownCompare {
  date_from: string;
  date_to: string;
  totals: Partial<BreakdownTotals>;
}

export interface BreakdownResponse {
  currency: string;
  date_from: string;
  date_to: string;
  totals: BreakdownTotals;
  compare?: BreakdownCompare | null;
  /** Sorted by `amount` descending (time dimensions by key), at most 200 plus `__other__`. */
  rows: BreakdownRow[];
  /** `tag` grouping: an entry counts once per tag, so the rows exceed the period. */
  overlapping: boolean;
  missing_rates: string[];
}

export interface TrendQuery extends ReportFilters {
  date_from: string;
  date_to: string;
  metric?: ReportMetric;
  granularity: TrendGranularity;
  /** Top-level category ids, one series each; without it one series for everything (`TREND_ALL`). */
  series?: number[];
}

/**
 * The key of the single series a trend returns without `series`. Not spelled out in design D1; agreed as `all` for
 * the API PR — change it here if the server picks another name.
 */
export const TREND_ALL = 'all';

export interface TrendBucket {
  /** The bucket's first date (`YYYY-MM-DD`). */
  start: string;
  /** Series key (`TREND_ALL` or a category id as text) → amount; empty buckets are present with `"0"`. */
  values: Record<string, string>;
}

export interface TrendResponse {
  currency: string;
  granularity: TrendGranularity;
  buckets: TrendBucket[];
  average: Record<string, string>;
}

// ---- budgets (design D3) -------------------------------------------------------------------------------------------

export type BudgetScope = 'total' | 'category' | 'project';

/** One budget as `GET /reports/budgets/status?month=` reports it for that month. */
export interface BudgetStatus {
  id: number;
  scope: BudgetScope;
  /** Top-level category of a `category` budget. */
  category_id: number | null;
  project_id: number | null;
  /** 全部支出, the category's or the project's name. */
  label: string;
  color: string | null;
  /** The month's limit (main currency, positive). */
  limit: string;
  /** Expense rule (refunds reduce it); a project budget's used covers the project's whole life. */
  used: string;
  remaining: string;
  /** `used / limit`. */
  ratio: number;
  over: boolean;
  /** `YYYY-MM` the limit applies from (monthly scopes). */
  effective_from: string | null;
}

export interface BudgetStatusResponse {
  month: string;
  currency: string;
  /** Elapsed days / days in the month; 1.0 for past months. */
  time_progress: number;
  budgets: BudgetStatus[];
}

/** `POST /reports/budgets`; `PUT /reports/budgets/{id}` takes `amount` and `effective_from` (a new row from that month). */
export interface BudgetInput {
  scope: BudgetScope;
  category_id?: number | null;
  project_id?: number | null;
  amount: string;
  effective_from: string;
}

// ---- saved reports (design D4) ------------------------------------------------------------------------------------

export type ReportChart = 'bar' | 'line' | 'donut' | 'table';

export type PeriodPreset = 'this_month' | 'last_month' | 'last_6_months' | 'last_12_months' | 'this_year' | 'last_year' | 'all';

/** A preset resolved on every open (relative periods roll forward), or fixed dates. */
export type ReportPeriodConfig = { preset: PeriodPreset } | { from: string; to: string };

/** The versioned `saved_report.config`. */
export interface ReportConfig {
  v: 1;
  period: ReportPeriodConfig;
  metric: ReportMetric;
  group_by: ReportDimension;
  split_by?: ReportDimension;
  filters: ReportFilters;
  compare?: ReportCompare;
}

export interface SavedReport {
  id: number;
  name: string;
  config: ReportConfig;
  chart: ReportChart;
  pinned: boolean;
  sort_order: number;
  /** Ids in the saved filters that no longer exist; the server drops them from `config` on read. */
  stale_refs?: string[];
  created_at?: string;
  updated_at?: string;
}

export interface SavedReportInput {
  name: string;
  config: ReportConfig;
  chart: ReportChart;
  pinned: boolean;
  sort_order?: number;
}
