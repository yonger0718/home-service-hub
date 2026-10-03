export type EntryKind =
  | 'expense'
  | 'income'
  | 'transfer_out'
  | 'transfer_in'
  | 'receivable'
  | 'payable'
  | 'balance_adjustment'
  | 'fee'
  | 'discount'
  | 'reward'
  | 'interest'
  | 'refund';

/** Kinds created through `POST /entries`; the server applies the sign. */
export type WritableEntryKind = 'expense' | 'income' | 'receivable' | 'payable';
export type EntrySource = 'moze_import' | 'moze_backup' | 'manual' | 'hermes' | 'rule';
export type FxSource = 'fx_api' | 'moze_backup' | 'manual';
export type DueRule = 'fixed_day' | 'days_after_closing';
export type RoundingMode = 'keep' | 'round' | 'floor' | 'ceil';
export type EntryGroupKind = 'split' | 'reward_claim' | 'installment';
export type RewardMethod = 'percent' | 'fixed';
export type RewardPosting = 'after_window' | 'after_transaction' | 'manual';
export type ColorConvention = 'red_green' | 'green_red';
export type KeypadLayout = 'calculator' | 'phone';
export type ScheduleKind = 'period' | 'installment' | 'skipped_record';
export type ImportKind = 'moze_csv' | 'moze_backup';

/** Decimal values travel as strings (NUMERIC on the server); parse with Number() for display only. */
export interface RewardRule {
  id: number;
  account_id: number;
  name: string;
  method: RewardMethod;
  rate: string | null;
  fixed_amount: string | null;
  window: 'statement_cycle';
  posting: RewardPosting;
  delay_days: number;
  post_month_offset: number;
  post_day: number;
  txn_rounding: RoundingMode;
  total_rounding: RoundingMode;
  total_cap: string | null;
  shared_cap_id: string | null;
  is_basic: boolean;
  reward_account_id: number | null;
  reward_project_id: number | null;
  starts_on: string | null;
  ends_on: string | null;
  is_enabled: boolean;
  description: string | null;
  sort_order: number;
  moze_id: string | null;
}

/** `GET /accounts` row (`AccountOut`). */
export interface LedgerAccount {
  id: number;
  name: string;
  currency: string;
  opening_balance: string;
  balance: string;
  /** Balance in the main currency; null when no rate to the main currency is cached. */
  balance_main: string | null;
  entry_count: number;
  group_id: number | null;
  group_name: string | null;
  icon: string | null;
  color: string | null;
  is_archived: boolean;
  include_in_total: boolean;
  is_credit: boolean;
  closing_day: number | null;
  due_rule: DueRule | null;
  due_value: number | null;
  credit_limit: string | null;
  combined_account_id: number | null;
  credit_sharing_id: string | null;
  auto_pay_account_id: number | null;
  fx_fee_pct: string | null;
  fx_fee_rounding: RoundingMode | null;
  fx_fee_refundable: boolean;
  sort_order: number;
  moze_id: string | null;
  available_credit: string | null;
  rule_summaries: string[];
  settings_locally_edited: boolean;
}

/** `GET /accounts/{id}` (`AccountDetailOut`). */
export interface AccountDetail extends LedgerAccount {
  note: string | null;
  reward_rules: RewardRule[];
  /** Other accounts sharing this account's credit limit (same `credit_sharing_id`); empty when none. */
  credit_sharing_members: number[];
}

/** `GET /accounts/{id}/summary?date_from=&date_to=` (`AccountPeriodSummaryOut`, Task 4); dates filter on `posted_date`. */
export interface AccountPeriodSummary {
  account_id: number;
  currency: string;
  date_from: string;
  date_to: string;
  /** Σ negative expense / fee amounts (negative). */
  spend: string;
  /** Σ positive income / interest / discount amounts. */
  income: string;
  /** Σ reward amounts. */
  rewards: string;
  /** Σ all amounts in the period. */
  net: string;
  /** Balance as of `date_to` (posted-date based). */
  end_balance: string;
  count: number;
}

export interface EntryGroupSummary {
  id: number;
  kind: EntryGroupKind;
  name: string | null;
  /** The group row's own merchant / description, so a split edit can rebuild `PUT /splits` from them. */
  merchant: string | null;
  description: string | null;
  count: number;
  /** Sum of the members' amounts, in the first member's currency; null for a mixed-currency group without a cached rate. */
  total: string | null;
  /** Currency of `total` (the first member's currency; `EntryGroupSummaryOut.currency`, Task 4). */
  currency: string;
}

/** List row (`EntryOut`). */
export interface LedgerEntry {
  id: number;
  kind: EntryKind;
  amount: string;
  currency: string;
  original_amount: string | null;
  original_currency: string | null;
  fx_rate: string | null;
  fx_source: FxSource | null;
  entry_date: string;
  entry_time: string | null;
  posted_date: string;
  account_id: number;
  account_name: string;
  /** Category path `main/sub`. */
  category: string | null;
  category_id: number | null;
  category_icon: string | null;
  category_color: string | null;
  project_id: number | null;
  project: string | null;
  name: string | null;
  merchant: string | null;
  counterparty: string | null;
  counterparty_id: number | null;
  description: string | null;
  tags: string[];
  parent_entry_id: number | null;
  transfer_group_id: string | null;
  group: EntryGroupSummary | null;
  rule_names: string[];
  invoice_number: string | null;
  needs_review: boolean;
  /**
   * `EntryOut.is_settlement` (plan-review round 2): true for 收款 / 還款 rows written by `settle` or imported from
   * MOZE types 5 / 6, with or without a `settles_entry_id` link. Such rows cannot be edited (`PUT` answers 422 `kind`).
   */
  is_settlement: boolean;
  /** Running balance in canonical order (`EntryOut.running_balance`, Task 4); typed nullable so older payloads still parse. */
  running_balance: string | null;
  source: EntrySource;
  moze_id: string | null;
  locked: boolean;
}

/** `GET /entries/{id}` and every entry write response (`EntryDetailOut`). */
export interface EntryDetail extends LedgerEntry {
  invoice_random: string | null;
  children: LedgerEntry[];
  group_members: LedgerEntry[];
  transfer_counterpart: LedgerEntry | null;
  settles: LedgerEntry | null;
  settled_by: LedgerEntry[];
  refunds: LedgerEntry | null;
  refunded_by: LedgerEntry[];
  rules: RewardRule[];
  rewards: LedgerEntry[];
  open_amount: string | null;
  is_settled: boolean | null;
  refunded_amount: string;
  /** Write responses only: the foreign-transaction fee the account proposes, for the client to add. */
  proposed_fee?: string | null;
}

export interface EntryPage {
  items: LedgerEntry[];
  total: number;
  limit: number;
  offset: number;
}

export interface EntryQuery {
  limit?: number;
  offset?: number;
  kind?: EntryKind | null;
  date_from?: string | null;
  date_to?: string | null;
  q?: string | null;
}

export interface AllEntriesQuery extends EntryQuery {
  account_id?: number[];
  hide_rewards?: boolean;
}

export interface MonthSummary {
  month: string;
  currency: string;
  /** Negative (sum of negative amounts), as `MonthSummaryOut` sends it; `net = income + expense`. */
  expense: string;
  income: string;
  net: string;
  /** Currencies without a cached rate to the main currency (left out of the totals). */
  missing_rates: string[];
}

export interface ChildInput {
  /** Unsigned; the server stores fees negative and discounts positive. */
  amount: string;
  name: string | null;
}

export interface EntryInput {
  account_id: number;
  kind: WritableEntryKind;
  /** Unsigned, in the account currency; null when the server converts `original_amount`. */
  amount: string | null;
  original_amount: string | null;
  original_currency: string | null;
  fx_rate: string | null;
  entry_date: string;
  entry_time: string | null;
  posted_date: string | null;
  category_id: number | null;
  project_id: number | null;
  name: string | null;
  merchant: string | null;
  counterparty_id: number | null;
  description: string | null;
  tags: string[];
  invoice_number: string | null;
  invoice_random: string | null;
  fee: ChildInput | null;
  discount: ChildInput | null;
  reward_rule_ids: number[];
}

export interface TransferInput {
  from_account_id: number;
  to_account_id: number;
  out_amount: string;
  in_amount: string | null;
  entry_date: string;
  entry_time: string | null;
  posted_date: string | null;
  category_id: number | null;
  name: string | null;
  merchant: string | null;
  description: string | null;
  project_id: number | null;
  tags: string[];
  out_fee: ChildInput | null;
  out_discount: ChildInput | null;
  in_fee: ChildInput | null;
  in_discount: ChildInput | null;
  reward_rule_ids: number[];
}

export interface SplitInput {
  name: string | null;
  merchant: string | null;
  description: string | null;
  entry_date: string;
  entry_time: string | null;
  posted_date: string | null;
  project_id: number | null;
  tags: string[];
  /** Member date, time, posting date, project and tags default to the group's when omitted. */
  members: Array<Omit<EntryInput, 'entry_date'> & { entry_date?: string }>;
}

export interface SettleInput {
  account_id: number;
  amount: string;
  entry_date: string;
  entry_time: string | null;
  description: string | null;
}

export interface RefundInput {
  account_id: number | null;
  amount: string;
  entry_date: string;
  entry_time: string | null;
  description: string | null;
}

export interface BalanceAdjustmentInput {
  account_id: number;
  target_balance: string;
  entry_date: string;
  entry_time: string | null;
  description: string | null;
}

export interface AccountInput {
  name: string;
  currency: string;
  opening_balance: string;
  is_archived: boolean;
  group_id: number | null;
  icon: string | null;
  color: string | null;
  note: string | null;
  sort_order: number;
  include_in_total: boolean;
  is_credit: boolean;
  closing_day: number | null;
  due_rule: DueRule | null;
  due_value: number | null;
  credit_limit: string | null;
  combined_account_id: number | null;
  credit_sharing_id: string | null;
  auto_pay_account_id: number | null;
  fx_fee_pct: string | null;
  fx_fee_rounding: RoundingMode | null;
  fx_fee_refundable: boolean;
  /**
   * Full member list of the shared credit limit (other account ids). When present, the server gives
   * `{this account} ∪ members` one `credit_sharing_id` and clears it from accounts that left the set.
   */
  credit_sharing_members?: number[];
}

export interface AccountGroup {
  id: number;
  name: string;
  sort_order: number;
  moze_id: string | null;
}

export interface AccountGroupInput {
  name: string;
  sort_order?: number;
}

export interface CategoryNode {
  id: number;
  kind: string;
  parent_id: number | null;
  name: string;
  icon: string | null;
  color: string | null;
  sort_order: number;
  is_hidden: boolean;
  default_account_id: number | null;
  default_project_id: number | null;
  /** `CategoryOut.moze_id`; optional so hand-written test literals may omit it. */
  moze_id?: string | null;
  children: CategoryNode[];
}

export interface CategoryInput {
  kind: string;
  parent_id: number | null;
  name: string;
  icon: string | null;
  color: string | null;
  sort_order?: number;
  is_hidden?: boolean;
}

export interface Project {
  id: number;
  name: string;
  is_archived: boolean;
  sort_order: number;
  moze_id: string | null;
}

export interface ProjectInput {
  name: string;
  is_archived?: boolean;
  sort_order?: number;
}

export interface Counterparty {
  id: number;
  name: string;
  /** `CounterpartyOut.moze_id`; optional so hand-written test literals may omit it. */
  moze_id?: string | null;
  /** Non-zero currencies only; `amount = −Σ amount` of that counterparty's receivable / payable entries. */
  open_amounts: { currency: string; amount: string }[];
}

export interface CounterpartyInput {
  name: string;
}

export interface Preference {
  expense_income_colors: ColorConvention;
  keypad_layout: KeypadLayout;
  week_start: number;
  main_currency: string;
  hide_rewards_on_timeline: boolean;
  abbreviate_totals: boolean;
}

/** Used until `GET /preference` answers, and when it fails. Mirrors the server defaults. */
export const DEFAULT_PREFERENCE: Preference = {
  expense_income_colors: 'red_green',
  keypad_layout: 'calculator',
  week_start: 0,
  main_currency: 'TWD',
  hide_rewards_on_timeline: false,
  abbreviate_totals: true,
};

export interface FxRateOut {
  date: string;
  base: string;
  quote: string;
  rate: string;
  source: string;
}

/** `GET /imports/schedules` row (`ScheduleItemOut`, Task 10): a future MOZE period / installment / skipped record. */
export interface ScheduleItem {
  id: number;
  kind: ScheduleKind;
  moze_id: string;
  name: string | null;
  /** Next occurrence date `YYYY-MM-DD`, or null when unknown. */
  next_date: string | null;
  /** Signed decimal string in `currency`, or null. */
  amount: string | null;
  currency: string | null;
}

export interface BackupAccountReport {
  name: string;
  currency: string;
  balance: string;
  moze_part: string | null;
  previous_moze_part: string | null;
  moze_balance: string | null;
  difference: string | null;
  compared: boolean;
}

/** `import_run.summary` of a backup import (keys listed in the Interface Contract). */
export interface BackupImportReport {
  kind: ImportKind;
  exported_at: string | null;
  kind_counts: Record<string, number>;
  skipped_future: Record<string, number>;
  /** Future MOZE rows stored by kind; a kind with no rows is absent (`dict(Counter(...))` on the server). */
  schedules: Partial<Record<ScheduleKind, number>>;
  groups: number;
  transfers: number;
  /** Transfers whose two legs imply different FX rates. */
  transfer_rate_mismatches: number;
  rules: number;
  attachments: number;
  counterparties: number;
  needs_review: { count: number; reasons: Record<string, number> };
  unsupported_rules: unknown[];
  orphaned_rules: unknown[];
  settings_skipped: unknown[];
  accounts: BackupAccountReport[];
  compared_accounts: { compared: number; total: number };
  /** Names of the accounts whose balance could not be compared with MOZE. */
  not_compared: string[];
  fx_outliers: unknown[];
  confirmed_maps: Record<string, unknown>;
  accounts_created: string[];
  accounts_archived: string[];
  accounts_renamed: { from: string; to: string }[];
}

export interface BackupImportOptions {
  dryRun?: boolean;
  strict?: boolean;
  allowFxOutliers?: boolean;
  /** JSON object `{"old name": "new name"}` as a string (same as `POST /imports/moze`); empty means none. */
  renames?: string;
}

export interface UnpairedTransfer {
  row: number;
  account: string;
  kind: EntryKind;
  date: string;
  time: string | null;
  amount: string;
  currency: string;
}

/** CSV runs carry `unpaired_transfers`; backup runs carry the `BackupImportReport` keys. */
export interface ImportSummary extends Partial<BackupImportReport> {
  unpaired_transfers?: UnpairedTransfer[];
  error?: string;
}

export interface ImportRun {
  id: number | null;
  kind?: ImportKind;
  status: 'running' | 'succeeded' | 'failed' | 'dry_run';
  started_at: string;
  finished_at: string | null;
  file_name: string;
  file_sha256: string;
  row_count: number | null;
  summary: ImportSummary | null;
}

export const ENTRY_KIND_LABELS: Record<EntryKind, string> = {
  expense: '支出',
  income: '收入',
  transfer_out: '轉出',
  transfer_in: '轉入',
  receivable: '應收款項',
  payable: '應付款項',
  balance_adjustment: '餘額調整',
  fee: '手續費',
  discount: '折扣',
  reward: '紅利回饋',
  interest: '利息',
  refund: '退款',
};

/**
 * Design D25 default icons, applied client-side when a category has no icon:
 * keyed by the imported main-category name, then by record kind.
 */
export const DEFAULT_CATEGORY_ICONS: Record<string, string> = {
  飲食: '🍜',
  交通: '🚌',
  娛樂: '🎮',
  購物: '🛍️',
  個人: '💇',
  醫療: '🩺',
  家居: '🏠',
  家庭: '👨‍👩‍👧',
  生活: '🧾',
  學習: '📚',
  其他: '📦',
  expense: '📦',
  income: '💰',
  transfer_out: '⇄',
  transfer_in: '⇄',
  receivable: '🤝',
  payable: '🏦',
  balance_adjustment: '⚖️',
  fee: '🧾',
  discount: '🧾',
  reward: '🧾',
  interest: '🧾',
  refund: '🧾',
};

/** Icon for a category without its own: by main-category name, then by kind, then 📦. */
export function defaultCategoryIcon(mainName: string | null | undefined, kind: string): string {
  return (mainName ? DEFAULT_CATEGORY_ICONS[mainName] : undefined) ?? DEFAULT_CATEGORY_ICONS[kind] ?? '📦';
}
