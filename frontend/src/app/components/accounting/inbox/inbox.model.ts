/** Mirrors the reconciliation inbox JSON (accounting-service `schemas/inbox.py`); amounts are decimal strings. */

import { InboxEinvoice } from './einvoice.model';

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
  | 'dividend'
  | 'proposed';

export type InboxButton =
  | 'record'
  | 'match_candidate'
  | 'accept_bank_amount'
  | 'defer'
  | 'dismiss'
  | 'anchor'
  | 'accept_early'
  | 'record_reward'
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

/** A bank line on another statement that may be the other leg of a transfer. */
export interface InboxPeerCandidate {
  line_id: number;
  statement_id: number;
  account_name: string;
  posted_date: string;
  amount: string;
}

/** What a transfer-shaped case can be answered with (absent on an older API). */
export interface InboxTransfer {
  adjust_transfer: {
    entry_id: number | null;
    line_id: number;
    candidate_entry_ids: number[];
    /** #433: one per `candidate_entry_ids`, same order (absent on an older payload). */
    legs?: InboxTransferLeg[];
  } | null;
  pair_transfer: { peer_candidates: InboxPeerCandidate[] } | null;
}

/**
 * #433: one `adjust_transfer` leg. 來源 → 目的 is account → counter for `out`, counter → account for `in`;
 * `counter_account_name` is null when the leg has no single other leg.
 */
export interface InboxTransferLeg {
  entry_id: number;
  account_name: string;
  counter_account_name: string | null;
  direction: 'out' | 'in';
}

/** Actions posted to `/cases/{id}/actions`. */
export type CaseAction = 'adjust_transfer' | 'pair_transfer' | 'adjust_amount';

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
  /** #392: settlement entries this bank line may be one part of (a repayment recorded as one entry for the sum).
   *  Non-empty only on a `bank_only` item (which then has no `group_key` and no `rule_hint`) or a `proposed` one
   *  whose proposal records the line. An older API omits it. */
  part_of?: InboxEntry[];
  /** Transfer answers for this case (adjust the entry to the statement's transfer, or pair with the other leg). */
  transfer?: InboxTransfer | null;
  /** The case's note (a bank-early reward line: how to fix the rule or accept the credit). */
  note?: string | null;
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
  dividend: number;
  other: number;
  /** Ambiguous + near e-invoices; separate from `ask`. 通知 counts them as one 要你確認 task (D10); the inbox does not. */
  einvoice_ask?: number;
  /** Every unmatched e-invoice (D11, never counted); 0 while reminders are off. Absent on an older API. */
  einvoice_unmatched?: number;
}

export interface InboxOut {
  generated_at: string;
  counts: InboxCounts;
  accounts: InboxAccount[];
  einvoices?: InboxEinvoice[];
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
  replayed: boolean;
  label: string | null;
  answer: string | null;
  created_at: string;
  reverted_by_action_id: number | null;
  /** The case's current bank line; only `GET /reconciliation/actions` fills it (null without a line). */
  line: ReconciliationActionLine | null;
  /** #433: the peer line's case an adjust_transfer / pair_transfer resolved; null otherwise (absent on older APIs). */
  peer_case_id?: number | null;
}

export interface ReconciliationActionLine {
  date: string;
  merchant: string;
  /** Flow amount as a decimal string (negative = money out). */
  amount: string;
}
