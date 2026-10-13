/** Read models for `GET /accounts/{id}/statements` and the detail route (R1a–R1c). Amounts are decimal strings. */

export interface StatementSummary {
  id: number;
  account_id: number;
  kind: string;
  currency: string;
  period_start: string;
  period_end: string;
  closing_date: string | null;
  due_date: string | null;
  opening_balance: string | null;
  statement_total: string;
  minimum_payment: string | null;
  origin: string;
  /** `historical` (summary only, no actions) or `live`. */
  mode: string;
  /** `open` or `reconciled`. */
  status: string;
  conflict_open: boolean;
  needs_recheck: boolean;
  current_revision_id: number | null;
  matched_count: number;
  explained_count: number;
  open_case_count: number;
  incoming_deferrals_pending: number;
}

export interface StatementCoverage {
  entry_id: number | null;
  group_id: number | null;
  role: string;
  flow: string | null;
  match_rule: string;
  match_kind: string;
  status: string;
}

export interface StatementLine {
  id: number;
  event_id: number | null;
  seq: number;
  txn_date: string | null;
  posted_date: string;
  merchant_raw: string;
  merchant_norm: string;
  printed_amount: string;
  flow_amount: string;
  foreign_amount: string | null;
  foreign_currency: string | null;
  line_kind: string;
  installment_seq: number | null;
  installment_total: number | null;
  matched: boolean;
  coverage: StatementCoverage[];
  /**
   * `statement_event.flag`. The current read model does not send it; the row shows 文字已變更 when it is
   * `text_changed`.
   */
  flag?: string | null;
}

export interface ReconciliationCase {
  id: number;
  kind: string;
  status: string;
  event_id: number | null;
  line_id: number | null;
  entry_id: number | null;
  explanation: string | null;
  candidates: unknown[];
  context: Record<string, unknown>;
  version: number;
  created_at: string;
}

export interface StatementRevision {
  id: number;
  revision: number;
  guardrail_ok: boolean;
  conflict: boolean;
  rejected: boolean;
}

export interface StatementDetail extends StatementSummary {
  lines: StatementLine[];
  cases: ReconciliationCase[];
  stale_events_pending: boolean;
  revisions: StatementRevision[];
}

export interface ReconcileResult {
  claims: number;
  cases_opened: number[];
  explained: number;
  unmatched_entries: number;
  skipped: string | null;
  policy: { applied: number; proposed: number };
  replayed?: boolean;
}

export interface ConfirmResult {
  status: string;
  checks: Record<string, boolean>;
  reopened_cases: number[];
  forced: boolean;
  replayed?: boolean;
}

export interface ConfirmBody {
  force: boolean;
  note?: string | null;
}
