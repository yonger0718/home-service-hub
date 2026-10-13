import { HttpErrorResponse } from '@angular/common/http';

import { AccountPeriodSummary, LedgerEntry } from '../../../models/accounting.model';
import { slashDate } from '../dates';
import { currencyDecimals } from '../format';
import { writeErrorMessage } from '../http-errors';
import {
  ReconciliationCase,
  StatementDetail,
  StatementLine,
  StatementSummary,
} from './statement.model';

export interface StatusChip {
  label: string;
  tone: 'neutral' | 'ok' | 'warn';
}

export interface HeaderComparison {
  statementLabel: string;
  ledgerLabel: string;
  statementAmount: number;
  ledgerAmount: number;
  /** Statement figure minus the ledger figure. Non-zero is a warning. */
  delta: number;
}

export interface CoveredRow {
  entryId: number | null;
  groupId: number | null;
  role: string;
  flow: string | null;
  matchRule: string;
  title: string;
}

/** One row in 已對帳 / 僅帳單有 / 僅帳本有. Case rows stay read-only (the action sheet is R3b). */
export interface SegmentRow {
  key: string;
  title: string;
  sub: string;
  amount: string;
  pills: string[];
  textChanged: boolean;
  quarantined: boolean;
  matchRules: string[];
  covered: CoveredRow[];
  caseId: number | null;
  caseKind: string | null;
}

export interface StatementSegments {
  matched: SegmentRow[];
  statementOnly: SegmentRow[];
  ledgerOnly: SegmentRow[];
}

export interface ReconProgress {
  /** Lines with active coverage. */
  k: number;
  /** Matched lines plus still-open cases. */
  n: number;
}

export interface FailedCheck {
  key: string;
  label: string;
}

const OPEN_CASE = new Set(['open', 'proposed']);

const CHECK_LABELS: Record<string, string> = {
  current_revision: '沒有目前版本',
  guardrail_ok: '帳單數字未通過檢查',
  conflict_open: '有未解決的新版本',
  needs_recheck: '需重新檢查',
  open_cases: '仍有未處理項目',
  pending_proposals: '仍有待套用建議',
  conserved: '金額未守恆',
};

const CASE_KIND_LABELS: Record<string, string> = {
  line_unmatched: '僅帳單有',
  entry_unmatched: '僅帳本有',
  amount_delta: '金額不符',
  ambiguous: '多筆可能',
  duplicate_claim: '重複認列',
  balance_gap: '餘額差異',
  statement_conflict: '版本衝突',
  parse_review: '資料已變更，待確認',
  recheck: '需重新檢查',
};

const ACTION_ERRORS: Record<string, string> = {
  confirm_retry: '遞延連結剛更新，請再試一次',
  sweep_barrier_busy: '其他作業進行中，請稍後再試',
  reconcile_busy: '對帳進行中，請稍後再試',
  import_running: '匯入進行中，請稍後再試',
  note_required: '強制確認需要填寫備註',
};

const ROLE_LABELS: Record<string, string> = {
  principal: '本金',
  child: '子項',
  member: '分帳',
};

const RULE_LABELS: Record<string, string> = {
  exact: '金額與日期相符',
  group: '分帳',
  owner: '手動',
  near: '日期接近',
  foreign: '外幣',
  agent: '建議',
};

/** Mode chip, then the conflict / recheck / stale chips the header stacks. */
export function statementChips(
  statement: Pick<StatementSummary, 'mode' | 'status' | 'conflict_open' | 'needs_recheck'>,
  stale = false,
): StatusChip[] {
  const chips: StatusChip[] = [];
  if (statement.mode === 'historical') {
    chips.push({ label: '歷史', tone: 'neutral' });
    if (statement.conflict_open) {
      chips.push({ label: '已確認（有新版本）', tone: 'warn' });
    }
  } else if (statement.conflict_open) {
    chips.push({ label: '已確認（有新版本）', tone: 'warn' });
  } else if (statement.status === 'reconciled') {
    chips.push({ label: '已確認', tone: 'ok' });
  } else {
    chips.push({ label: '對帳中', tone: 'warn' });
  }
  if (statement.needs_recheck) {
    chips.push({ label: '需重新檢查', tone: 'warn' });
  }
  if (stale) {
    chips.push({ label: '需重新同步', tone: 'warn' });
  }
  return chips;
}

/** `2026/08/21 – 2026/09/20`. */
export function periodText(start: string, end: string): string {
  return `${slashDate(start)} – ${slashDate(end)}`;
}

/** 對帳 k/n: matched lines over matched lines plus open cases. */
export function reconProgress(
  statement: Pick<StatementSummary, 'matched_count' | 'open_case_count'>,
): ReconProgress {
  return { k: statement.matched_count, n: statement.matched_count + statement.open_case_count };
}

/** The imported statement for a billing cycle, matched on `period_end` (the account's unique statement identity). */
export function statementForPeriod(
  statements: readonly StatementSummary[],
  periodEnd: string,
): StatementSummary | null {
  return statements.find((statement) => statement.period_end === periodEnd) ?? null;
}

/**
 * Card: 帳單應繳 (`statement_total`) vs 帳本本期 (−period spend, the cycle's bill, not today's remaining payment).
 * Bank: 期末餘額 (`statement_total`) vs 帳本餘額 (balance as of `period_end`).
 */
export function headerComparison(
  statement: Pick<StatementSummary, 'kind' | 'statement_total'>,
  summary: Pick<AccountPeriodSummary, 'spend' | 'end_balance'>,
): HeaderComparison {
  const statementAmount = Number(statement.statement_total);
  if (statement.kind === 'bank') {
    const ledgerAmount = Number(summary.end_balance);
    return {
      statementLabel: '期末餘額',
      ledgerLabel: '帳本餘額',
      statementAmount,
      ledgerAmount,
      delta: round4(statementAmount - ledgerAmount),
    };
  }
  const ledgerAmount = round4(-Number(summary.spend));
  return {
    statementLabel: '帳單應繳',
    ledgerLabel: '帳本本期',
    statementAmount,
    ledgerAmount,
    delta: round4(statementAmount - ledgerAmount),
  };
}

/** True when `delta` is visible in this currency (TWD/JPY whole units, otherwise cents). */
export function deltaWarns(delta: number, currency: string): boolean {
  const factor = 10 ** currencyDecimals(currency);
  return Math.round(Math.abs(delta) * factor) !== 0;
}

/** Historical periods are a summary table: no reconcile and no confirm. */
export function actionsAvailable(statement: Pick<StatementSummary, 'mode'>): boolean {
  return statement.mode !== 'historical';
}

/** 全部確認 stays disabled while cases are open; 強制確認 is the separate keyboard path. */
export function confirmBlockedReason(
  statement: Pick<StatementSummary, 'open_case_count'>,
): string | null {
  return statement.open_case_count > 0 ? '仍有未處理項目' : null;
}

/** Historical summary: ✓ when nothing is open, conflicting or waiting for a recheck. */
export function periodConsistent(
  statement: Pick<StatementSummary, 'open_case_count' | 'conflict_open' | 'needs_recheck'>,
): boolean {
  return statement.open_case_count === 0 && !statement.conflict_open && !statement.needs_recheck;
}

export function matchRuleLabel(rule: string): string {
  return RULE_LABELS[rule] ?? rule;
}

export function caseKindLabel(kind: string): string {
  return CASE_KIND_LABELS[kind] ?? kind;
}

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

/** The business code and the text after the first colon (`code` or `code: detail`). */
export function splitApiCode(text: string): { code: string; detail: string | null } {
  const sep = text.indexOf(':');
  if (sep === -1) {
    return { code: text.trim(), detail: null };
  }
  const detail = text.slice(sep + 1).trim();
  return { code: text.slice(0, sep).trim(), detail: detail || null };
}

/** A failed reconcile or confirm, in the owner's words. Confirm refusals are rendered as checks instead. */
export function actionErrorMessage(err: unknown): string {
  const text = apiErrorText(err);
  if (text) {
    const { code } = splitApiCode(text);
    return ACTION_ERRORS[code] ?? text;
  }
  return writeErrorMessage(err);
}

/** 409 `message` (shared_lib) or string `detail` (FastAPI). */
export function apiErrorText(err: unknown): string | null {
  if (!(err instanceof HttpErrorResponse)) {
    return null;
  }
  const body = err.error as { detail?: unknown; message?: unknown } | null;
  if (typeof body?.message === 'string' && body.message) {
    return body.message;
  }
  if (typeof body?.detail === 'string' && body.detail) {
    return body.detail;
  }
  return null;
}

/**
 * Confirm refusal: 409 `confirm_refused: <checks JSON>`. A check is present when it failed (`false` means it
 * did not pass).
 */
export function confirmRefusal(err: unknown): FailedCheck[] | null {
  const text = apiErrorText(err);
  if (!text) {
    return null;
  }
  const { code, detail } = splitApiCode(text);
  if (code !== 'confirm_refused' || !detail) {
    return null;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(detail);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object') {
    return null;
  }
  const failed: FailedCheck[] = [];
  for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
    if (value === false) {
      failed.push({ key, label: CHECK_LABELS[key] ?? key });
    }
  }
  return failed;
}

/** Open `parse_review` events the detail can attach 資料已變更，待確認 to. */
export function quarantinedEventIds(cases: readonly ReconciliationCase[]): Set<number> {
  const ids = new Set<number>();
  for (const item of cases) {
    if (item.kind !== 'parse_review' || !OPEN_CASE.has(item.status)) {
      continue;
    }
    const marked = item.context?.['quarantined_event_id'];
    if (typeof marked === 'number') {
      ids.add(marked);
    }
    if (
      item.event_id != null &&
      (marked != null || typeof item.context?.['equivalence'] === 'string')
    ) {
      ids.add(item.event_id);
    }
  }
  return ids;
}

export function lineTextChanged(line: StatementLine): boolean {
  return line.flag === 'text_changed';
}

export function buildSegments(
  detail: StatementDetail,
  entries: ReadonlyMap<number, LedgerEntry>,
): StatementSegments {
  const open = detail.cases.filter((item) => OPEN_CASE.has(item.status));
  const quarantined = quarantinedEventIds(detail.cases);
  const lineIds = new Set(detail.lines.map((line) => line.id));
  const eventIds = new Set(
    detail.lines.map((line) => line.event_id).filter((id): id is number => id != null),
  );
  const matched: SegmentRow[] = [];
  const statementOnly: SegmentRow[] = [];
  for (const line of detail.lines) {
    const row = lineRow(line, open, quarantined, entries);
    (line.matched ? matched : statementOnly).push(row);
  }
  for (const item of open) {
    if (item.line_id != null && lineIds.has(item.line_id)) {
      continue;
    }
    if (
      item.event_id != null &&
      eventIds.has(item.event_id) &&
      item.kind !== 'entry_unmatched' &&
      item.kind !== 'balance_gap'
    ) {
      continue;
    }
    if (item.kind === 'entry_unmatched' || item.kind === 'balance_gap') {
      continue;
    }
    statementOnly.push(caseRow(item, entries, quarantined));
  }
  const ledgerOnly: SegmentRow[] = [];
  for (const item of open) {
    if (item.kind === 'entry_unmatched' || item.kind === 'balance_gap') {
      ledgerOnly.push(caseRow(item, entries, quarantined));
    }
  }
  return { matched, statementOnly, ledgerOnly };
}

function lineRow(
  line: StatementLine,
  open: ReconciliationCase[],
  quarantined: Set<number>,
  entries: ReadonlyMap<number, LedgerEntry>,
): SegmentRow {
  const related = open.filter(
    (item) =>
      item.line_id === line.id ||
      (line.event_id != null && item.event_id === line.event_id && item.kind !== 'entry_unmatched'),
  );
  const textChanged = lineTextChanged(line);
  const isQuarantined = line.event_id != null && quarantined.has(line.event_id);
  const pills = rowPills(textChanged, isQuarantined, related);
  const covered = line.coverage.map((coverage) => coveredRow(coverage, entries));
  return {
    key: `line-${line.id}`,
    title: line.merchant_raw,
    sub: slashDate(line.posted_date),
    amount: line.flow_amount,
    pills,
    textChanged,
    quarantined: isQuarantined,
    matchRules: [...new Set(line.coverage.map((coverage) => coverage.match_rule))],
    covered,
    caseId: related[0]?.id ?? null,
    caseKind: related[0]?.kind ?? null,
  };
}

function caseRow(
  item: ReconciliationCase,
  entries: ReadonlyMap<number, LedgerEntry>,
  quarantined: Set<number>,
): SegmentRow {
  const entry = item.entry_id != null ? entries.get(item.entry_id) : undefined;
  const quarantinedRow =
    item.kind === 'parse_review' || (item.event_id != null && quarantined.has(item.event_id));
  const gap = item.kind === 'balance_gap' ? stringField(item.context, 'gap') : null;
  return {
    key: `case-${item.id}`,
    title:
      item.kind === 'balance_gap'
        ? '餘額差異'
        : entry
          ? entryTitle(entry)
          : caseKindLabel(item.kind),
    sub:
      item.kind === 'entry_unmatched' && entry
        ? slashDate(entry.posted_date)
        : caseKindLabel(item.kind),
    amount: gap ?? entry?.amount ?? '0',
    pills: rowPills(false, quarantinedRow, [item]),
    textChanged: false,
    quarantined: quarantinedRow,
    matchRules: [],
    covered: [],
    caseId: item.id,
    caseKind: item.kind,
  };
}

function coveredRow(
  coverage: StatementLine['coverage'][number],
  entries: ReadonlyMap<number, LedgerEntry>,
): CoveredRow {
  const entry = coverage.entry_id != null ? entries.get(coverage.entry_id) : undefined;
  const title = entry
    ? entryTitle(entry)
    : coverage.group_id != null
      ? `群組 #${coverage.group_id}`
      : coverage.entry_id != null
        ? `記錄 #${coverage.entry_id}`
        : '已刪除的記錄';
  return {
    entryId: coverage.entry_id,
    groupId: coverage.group_id,
    role: coverage.role,
    flow: coverage.flow,
    matchRule: coverage.match_rule,
    title,
  };
}

function rowPills(
  textChanged: boolean,
  quarantined: boolean,
  cases: ReconciliationCase[],
): string[] {
  const pills: string[] = [];
  if (textChanged) {
    pills.push('文字已變更');
  }
  if (quarantined) {
    pills.push('資料已變更，待確認');
  }
  for (const item of cases) {
    if (item.kind === 'parse_review') {
      continue;
    }
    const label = caseKindLabel(item.kind);
    if (!pills.includes(label)) {
      pills.push(label);
    }
  }
  return pills;
}

function entryTitle(entry: LedgerEntry): string {
  return entry.name || entry.merchant || entry.category || `記錄 #${entry.id}`;
}

function stringField(context: Record<string, unknown>, key: string): string | null {
  const value = context[key];
  return typeof value === 'string' ? value : null;
}

function round4(value: number): number {
  return Math.round(value * 1e4) / 1e4 || 0;
}
