import {
  AccountDetail,
  CategoryNode,
  DEFAULT_PREFERENCE,
  EntryDetail,
  LedgerAccount,
  LedgerEntry,
  Preference,
  RewardRule,
} from '../../../models/accounting.model';

/** Synthetic DTO factories for specs. Every field has a neutral default; pass only what the test is about. */
export function makeAccount(overrides: Partial<LedgerAccount> = {}): LedgerAccount {
  return {
    id: 1,
    name: '錢包',
    currency: 'TWD',
    opening_balance: '0',
    balance: '0',
    balance_main: '0',
    entry_count: 0,
    group_id: null,
    group_name: null,
    icon: null,
    color: null,
    is_archived: false,
    include_in_total: true,
    is_credit: false,
    closing_day: null,
    due_rule: null,
    due_value: null,
    credit_limit: null,
    combined_account_id: null,
    credit_sharing_id: null,
    auto_pay_account_id: null,
    fx_fee_pct: null,
    fx_fee_rounding: null,
    fx_fee_refundable: false,
    sort_order: 0,
    moze_id: null,
    available_credit: null,
    rule_summaries: [],
    settings_locally_edited: false,
    ...overrides,
  };
}

export function makeRule(overrides: Partial<RewardRule> = {}): RewardRule {
  return {
    id: 1,
    account_id: 1,
    name: '一般',
    method: 'percent',
    rate: '1.0000',
    fixed_amount: null,
    window: 'statement_cycle',
    posting: 'after_window',
    delay_days: 0,
    post_month_offset: 1,
    post_day: 15,
    txn_rounding: 'keep',
    total_rounding: 'keep',
    total_cap: null,
    shared_cap_id: null,
    is_basic: false,
    reward_account_id: null,
    reward_project_id: null,
    starts_on: null,
    ends_on: null,
    is_enabled: true,
    description: null,
    sort_order: 0,
    moze_id: null,
    ...overrides,
  };
}

export function makeAccountDetail(overrides: Partial<AccountDetail> = {}): AccountDetail {
  return { ...makeAccount(), note: null, reward_rules: [], credit_sharing_members: [], ...overrides };
}

export function makeEntry(overrides: Partial<LedgerEntry> = {}): LedgerEntry {
  return {
    id: 1,
    kind: 'expense',
    amount: '-120.0000',
    currency: 'TWD',
    original_amount: null,
    original_currency: null,
    fx_rate: null,
    fx_source: null,
    entry_date: '2026-10-02',
    entry_time: '12:30:00',
    posted_date: '2026-10-02',
    account_id: 1,
    account_name: '錢包',
    category: '飲食/午餐',
    category_id: 12,
    category_icon: '🍜',
    category_color: '#f0cd92',
    project_id: null,
    project: null,
    name: null,
    merchant: null,
    counterparty: null,
    counterparty_id: null,
    description: null,
    tags: [],
    parent_entry_id: null,
    transfer_group_id: null,
    group: null,
    rule_names: [],
    invoice_number: null,
    needs_review: false,
    is_settlement: false,
    running_balance: null,
    source: 'manual',
    moze_id: null,
    locked: false,
    ...overrides,
  };
}

export function makeEntryDetail(overrides: Partial<EntryDetail> = {}): EntryDetail {
  return {
    ...makeEntry(),
    invoice_random: null,
    children: [],
    group_members: [],
    transfer_counterpart: null,
    settles: null,
    settled_by: [],
    refunds: null,
    refunded_by: [],
    rules: [],
    rewards: [],
    open_amount: null,
    is_settled: null,
    refunded_amount: '0',
    proposed_fee: null,
    ...overrides,
  };
}

export function makeCategory(overrides: Partial<CategoryNode> = {}): CategoryNode {
  return {
    id: 1,
    kind: 'expense',
    parent_id: null,
    name: '飲食',
    icon: null,
    color: null,
    sort_order: 0,
    is_hidden: false,
    default_account_id: null,
    default_project_id: null,
    children: [],
    ...overrides,
  };
}

export function makePreference(overrides: Partial<Preference> = {}): Preference {
  return { ...DEFAULT_PREFERENCE, ...overrides };
}
