/** Mirrors the reconciliation inbox JSON (accounting-service `schemas/inbox.py`); amounts are decimal strings. */

export type InboxQuestion =
  | 'bank_only'
  | 'transfer'
  | 'ledger_only'
  | 'amount_differs'
  | 'date_differs'
  | 'ambiguous'
  | 'other'
  | 'balance_gap'
  | 'settlement'
  | 'proposed';

export type InboxButton =
  | 'record'
  | 'match_candidate'
  | 'accept_bank_amount'
  | 'defer'
  | 'dismiss'
  | 'anchor'
  | 'apply_proposal'
  | 'reject_proposal';

export interface InboxLine {
  id: number;
  date: string;
  merchant: string;
  amount: string;
  line_kind: string;
}

export interface InboxEntry {
  id: number;
  date: string;
  title: string;
  amount: string;
  account_id: number;
}

export interface InboxCandidate {
  entry_id: number;
  date: string;
  title: string;
  amount: string;
  reason: string | null;
  residual: string | null;
  date_distance: number | null;
}

export interface InboxProposal {
  id: number;
  action: string;
  params: Record<string, unknown>;
  author: string;
  rationale: string;
}

export interface InboxRuleHint {
  merchant_pattern: string;
  line_kind: string;
  flow_sign: string;
  amount: string;
  default_category_id: number | null;
}

export interface InboxItem {
  case_id: number;
  case_version: number;
  kind: string;
  status: string;
  question: InboxQuestion;
  folded: boolean;
  group_key: string | null;
  statement: { id: number; period_end: string };
  line: InboxLine | null;
  entry: InboxEntry | null;
  candidates: InboxCandidate[];
  proposal: InboxProposal | null;
  actions: InboxButton[];
  rule_hint: InboxRuleHint | null;
}

export interface InboxAccount {
  id: number;
  name: string;
  currency: string;
  is_credit: boolean;
  items: InboxItem[];
}

export interface InboxCounts {
  ask: number;
  ask_items: number;
  proposed: number;
  balance_gap: number;
  settlement: number;
  other: number;
}

export interface InboxOut {
  generated_at: string;
  counts: InboxCounts;
  accounts: InboxAccount[];
}

export type InboxLearn = { amount_exact: boolean } | null;

export interface InboxApplyBody {
  cases: { case_id: number; case_version: number }[];
  action: Exclude<InboxButton, 'apply_proposal' | 'reject_proposal'>;
  params: Record<string, unknown>;
  answer: string;
  learn?: InboxLearn;
}

export interface InboxApplyResult {
  case_id: number;
  status: 'applied' | 'replayed' | 'refused';
  action_id: number | null;
  code: string | null;
}

export interface InboxApplyOut {
  results: InboxApplyResult[];
  rule_id: number | null;
  rule_error: string | null;
}

export interface OwnerRule {
  id: number;
  account_id: number;
  enabled: boolean;
  line_kind: string;
  merchant_pattern: string;
  flow_sign: string;
  amount: string | null;
  action: string;
  params: Record<string, unknown>;
  created_from_case_id: number | null;
  created_at: string;
  last_applied_at: string | null;
  applied_count: number;
  note: string | null;
}

export interface ReconciliationAction {
  id: number;
  case_id: number;
  proposal_id: number | null;
  action: string;
  params: Record<string, unknown>;
  actor: string;
  status: string;
  effect_slot: string;
  created_entry_ids: number[];
  deleted_entry_ids: number[];
  touched_entry_ids: number[];
  touched_group_ids: number[];
  label: string | null;
  answer: string | null;
  created_at: string;
  reverted_by_action_id: number | null;
}
