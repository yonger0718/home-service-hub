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
export type EntrySource = 'moze_import' | 'moze_backup' | 'manual' | 'hermes' | 'rule' | 'schedule';
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
  /**
   * `EntryOut.is_closed`: a receivable / payable original MOZE marked settled (target isSettle), even when its
   * settlements do not net it to zero. The server then reports `open_amount` 0 and `is_settled` true.
   */
  is_closed: boolean;
  /**
   * `EntryOut.open_amount`: what is left on a receivable / payable original (|amount + Σ linked settlements|, 0 when
   * closed); null for every other row (settlements included).
   */
  open_amount: string | null;
  /** Running balance in canonical order (`EntryOut.running_balance`, Task 4); typed nullable so older payloads still parse. */
  running_balance: string | null;
  source: EntrySource;
  moze_id: string | null;
  locked: boolean;
  /** `EntryOut.schedule`: the posted schedule period that wrote this entry (週期 #k/N, 分期 #k/N pill); optional for older literals. */
  schedule?: EntryScheduleLink | null;
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
  is_settled: boolean | null;
  refunded_amount: string;
  /** Write responses only: the foreign-transaction fee the account proposes, for the client to add. */
  proposed_fee?: string | null;
  /** `EntryDetailOut.loan_schedule`: a payable / receivable original that a schedule repays (剩餘 · 已還 · 下期). */
  loan_schedule?: LoanSchedule | null;
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
  counterparty_id?: number;
  /** Only unsettled receivable / payable originals (settlements excluded). */
  open?: boolean;
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

/** One entry_date of `GET /entries/summary/daily`; same rules and currency as `MonthSummary`. */
export interface DailySummaryDay {
  date: string;
  /** Negative (spend net of refunds) in the main currency. */
  expense: string;
  income: string;
  /** Rows counted into the figures. */
  count: number;
}

/** `GET /entries/summary/daily?month=`: days without counted rows are omitted. */
export interface DailySummary {
  month: string;
  currency: string;
  days: DailySummaryDay[];
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
  /**
   * `CounterpartyOut.open_count`: open (unsettled, not closed) receivable / payable originals; unlike `open_amounts`
   * it never nets the two sides. Optional so hand-written test literals may omit it (counts as 0).
   */
  open_count?: number;
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

// ---- schedules (週期 / 分期 / 待完成交易) -----------------------------------------------------------------------

export type ScheduleDefinitionKind = 'recurring' | 'installment';
export type ScheduleIntervalUnit = 'day' | 'week' | 'month' | 'year';
export type SchedulePostingMode = 'auto' | 'confirm';
export type ScheduleStatus = 'active' | 'paused' | 'ended';
export type ScheduleInstanceStatus = 'pending' | 'posted' | 'skipped';
export type ScheduleActor = 'auto' | 'owner' | 'import';
export type ScheduleLineKind =
  | 'expense'
  | 'income'
  | 'receivable'
  | 'payable'
  | 'transfer'
  | 'repayment'
  | 'collection'
  | 'interest';

/** One template line; amounts are unsigned decimal strings, the server applies the sign. */
export interface ScheduleLineInput {
  kind: ScheduleLineKind;
  account_id: number;
  to_account_id: number | null;
  to_amount: string | null;
  counterparty_id: number | null;
  category_id: number | null;
  project_id: number | null;
  amount: string;
  currency: string;
  loan_entry_id: number | null;
  name: string | null;
  merchant: string | null;
}

export interface ScheduleLine extends ScheduleLineInput {
  account_name: string | null;
  to_account_name: string | null;
  category: string | null;
  counterparty: string | null;
}

export interface ScheduleTemplateInput {
  lines: ScheduleLineInput[];
  description: string | null;
  tags: string[];
}

export interface ScheduleTemplate {
  lines: ScheduleLine[];
  description: string | null;
  tags: string[];
}

export interface CurrencyAmount {
  currency: string;
  amount: string;
}

export interface ScheduleFailing {
  instance_id: number;
  due_date: string;
  last_error: string;
}

/** `GET /schedules/definitions` row (`DefinitionOut`). */
export interface ScheduleDefinition {
  id: number;
  kind: ScheduleDefinitionKind;
  name: string;
  status: ScheduleStatus;
  posting_mode: SchedulePostingMode;
  interval_unit: ScheduleIntervalUnit;
  interval_n: number;
  anchor_date: string;
  day_of_month: number | null;
  first_seq: number;
  times: number | null;
  end_date: string | null;
  total_amount: string | null;
  auto_post_from: string;
  template: ScheduleTemplate;
  created_locally: boolean;
  /** The owner edited the template (D27): a backup import keeps it and lists MOZE's differing amounts. */
  template_owner_edited: boolean;
  imported: boolean;
  /** Imported and before cutover: 編輯整個排程 and 刪除 answer 409 locked_until_cutover. */
  locked: boolean;
  review_reason: string | null;
  generated_until: string | null;
  posted_count: number;
  skipped_count: number;
  pending_count: number;
  next_due_date: string | null;
  next_amount: CurrencyAmount[];
  /** Loans: open amount signed like the loan (payable negative); card installments: what is left of the total. */
  remaining: string | null;
  repaid: string | null;
  loan_entry_id: number | null;
  needs_check: boolean;
  failing: ScheduleFailing | null;
  category_icon: string | null;
  category_color: string | null;
}

export interface ScheduleInstanceLine {
  kind: ScheduleLineKind;
  account_id: number;
  account_name: string | null;
  to_account_id: number | null;
  to_account_name: string | null;
  category: string | null;
  counterparty: string | null;
  /** Signed: money leaving the paying account is negative. */
  amount: string;
  currency: string;
}

/** `GET /schedules/instances` row (`InstanceOut`). */
export interface ScheduleInstance {
  id: number;
  definition_id: number;
  definition_name: string;
  kind: ScheduleDefinitionKind;
  posting_mode: SchedulePostingMode;
  seq: number;
  times: number | null;
  due_date: string;
  rule_date: string;
  status: ScheduleInstanceStatus;
  is_partial: boolean;
  overdue_days: number;
  lines: ScheduleInstanceLine[];
  totals: CurrencyAmount[];
  /** Unsigned amounts aligned with the template lines ("0" = not written), for repost bodies. */
  amounts: string[];
  last_error: string | null;
  reopened: boolean;
  edited_by_owner: boolean;
  note: string | null;
  posted_entry_ids: number[];
  acted_at: string | null;
  acted_by: ScheduleActor | null;
  category_icon: string | null;
  category_color: string | null;
}

export interface ScheduleDefinitionDetail extends ScheduleDefinition {
  instances: ScheduleInstance[];
}

export interface ScheduleLoanInput {
  account_id: number;
  counterparty_id: number;
  category_id: number | null;
  amount: string;
  entry_date: string;
  name: string | null;
}

export interface ScheduleDefinitionInput {
  kind: ScheduleDefinitionKind;
  name: string;
  template: ScheduleTemplateInput;
  interval_unit: ScheduleIntervalUnit;
  interval_n: number;
  anchor_date: string;
  day_of_month: number | null;
  times: number | null;
  end_date: string | null;
  total_amount: string | null;
  posting_mode: SchedulePostingMode;
  loan: ScheduleLoanInput | null;
}

/** `PUT /schedules/definitions/{id}`: the create fields except `kind` and `loan`; a left-out `posting_mode` keeps the stored one. */
export type ScheduleDefinitionUpdate = Omit<ScheduleDefinitionInput, 'kind' | 'loan' | 'posting_mode'> & {
  posting_mode?: SchedulePostingMode;
};

/** 套用範圍 of a period edit: 僅這一期 / 這一期與之後 / 全部週期. */
export type ScheduleInstanceScope = 'this' | 'following' | 'all';

/** `PUT /schedules/instances/{id}` `scope` (套用範圍): 僅這一期 / 這一期與之後 / 全部週期 — the name Task 24 uses. */
export type ScheduleAmountScope = ScheduleInstanceScope;

/** `PUT /schedules/instances/{id}` (`InstanceUpdateIn`). */
export interface ScheduleInstanceUpdate {
  due_date?: string;
  amounts?: string[];
  scope?: ScheduleInstanceScope;
}

export interface ScheduleInstanceQuery {
  from?: string;
  until?: string;
  status?: ScheduleInstanceStatus | 'all';
  definition_id?: number;
  queue?: boolean;
}

export interface ScheduleCatchUpResult {
  posted: number[];
  failed: { instance_id: number; error: string } | null;
  definition: ScheduleDefinition;
}

export interface ScheduleRunReport {
  trigger: string;
  today: string;
  status: string;
  generated: number;
  /** Ids of the definitions whose generation failed in this run. */
  generation_failed: number[];
  posted: number[];
  failed: number[];
  stopped_definitions: number[];
}

/** `EntryOut.schedule`. */
export interface EntryScheduleLink {
  definition_id: number;
  instance_id: number;
  kind: ScheduleDefinitionKind;
  seq: number;
  times: number | null;
  name: string;
  is_partial: boolean;
  acted_by: ScheduleActor | null;
  posted_entry_ids: number[];
}

/** `EntryDetailOut.loan_schedule`. */
export interface LoanSchedule {
  definition_id: number;
  name: string;
  status: ScheduleStatus;
  posting_mode: SchedulePostingMode;
  posted_count: number;
  times: number | null;
  next_due_date: string | null;
  next_amount: CurrencyAmount[];
  remaining: string | null;
  repaid: string | null;
  needs_check: boolean;
}

/** One differing line of a period (Multica R-F5): HomeHub's amount against MOZE's record; owner-facing, never logged. */
export interface ScheduleAmountDiffer {
  definition_id: number;
  name: string;
  seq: number;
  date: string;
  line: number;
  kind: ScheduleLineKind | 'transfer_in';
  amount: string;
  moze_amount: string;
}

/** `review[].reason` of a backup import's schedules block (`not_live` / `loan_missing` also land on `review_reason`). */
export type ScheduleReviewReason =
  | 'interval_mismatch'
  | 'same_date'
  | 'no_records'
  | 'event_missing'
  | 'seq_conflict'
  | 'account_missing'
  | 'loan_missing'
  | 'not_live';

/** `summary.schedules` of a backup import. */
export interface ScheduleImportReport {
  definitions: Record<'recurring' | 'installment' | 'single', { created: number; updated: number; ended: number; deleted: number }>;
  instances: Record<string, number>;
  records_mapped: number;
  rewards_ignored: number;
  unsupported_types: Record<string, number>;
  past_records_already_posted: number;
  past_records_already_skipped: number;
  past_records_amount_differs: { count: number; instance_ids: number[]; lines: ScheduleAmountDiffer[] };
  /** R-F1: owner-edited pending periods whose MOZE record turned past (the record was not imported). */
  past_records_owner_pending: { definition_id: number; seq: number; date: string }[];
  relinked: { templates: number; settlements: number };
  review: { moze_id: string; reason: ScheduleReviewReason }[];
  /** R-A3: pending periods whose kept amounts (owner price, owner edit) differ from MOZE's, per line. */
  amount_differs: ScheduleAmountDiffer[];
  loan_remainder_check: { definition: string; moze_remainder: string; open_amount: string; difference: string }[];
  /** MOZE records hanging off a skipped / owner-held period (fee, discount, …): not imported, MOZE type number kept. */
  dependants_suppressed: { count: number; records: { moze_id: string; parent_moze_id: string; type: number }[] };
}
