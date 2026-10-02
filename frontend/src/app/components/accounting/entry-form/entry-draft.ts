import { HttpErrorResponse } from '@angular/common/http';

import { CategoryNode, RewardRule, WritableEntryKind } from '../../../models/accounting.model';
import { evaluateAmount } from '../amount-math';

/** Tabs of the entry page; `transfer` is wired by Task 24, `system` is 餘額調整. */
export type FormKind = WritableEntryKind | 'transfer' | 'system';

export const FORM_KINDS: readonly { kind: FormKind; label: string }[] = [
  { kind: 'expense', label: '支出' },
  { kind: 'income', label: '收入' },
  { kind: 'transfer', label: '轉帳' },
  { kind: 'receivable', label: '應收款項' },
  { kind: 'payable', label: '應付款項' },
  { kind: 'system', label: '系統' },
];

const WRITABLE_KINDS = new Set<string>(['expense', 'income', 'receivable', 'payable']);

export function isWritableKind(kind: string): kind is WritableEntryKind {
  return WRITABLE_KINDS.has(kind);
}

export function isFormKind(value: string | null): value is FormKind {
  return value !== null && FORM_KINDS.some(tab => tab.kind === value);
}

/** Category tree to load for a tab (`GET /categories?kind=`); none for 系統. */
export function categoryKindFor(kind: FormKind): string | null {
  if (kind === 'system') {
    return null;
  }
  return kind === 'transfer' ? 'transfer_out' : kind;
}

/** The sign the server applies: expense and receivable decrease the account, income and payable increase it. */
export function signFor(kind: WritableEntryKind): 1 | -1 {
  return kind === 'expense' || kind === 'receivable' ? -1 : 1;
}

export const LAST_USE_PREFIX = 'hh.accounting.lastUse.';
export const AMOUNT_HISTORY_PREFIX = 'hh.accounting.amounts.';
const QUICK_AMOUNT_COUNT = 6;

export interface LastUse {
  account_id: number | null;
  project_id: number | null;
}

function store(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}

export function readLastUse(categoryId: number): LastUse | null {
  try {
    const raw = store()?.getItem(LAST_USE_PREFIX + categoryId);
    if (!raw) {
      return null;
    }
    const value = JSON.parse(raw) as Partial<LastUse>;
    return {
      account_id: typeof value.account_id === 'number' ? value.account_id : null,
      project_id: typeof value.project_id === 'number' ? value.project_id : null,
    };
  } catch {
    return null;
  }
}

export function writeLastUse(categoryId: number, value: LastUse): void {
  try {
    store()?.setItem(LAST_USE_PREFIX + categoryId, JSON.stringify(value));
  } catch {
    // Storage blocked or full: the server-side category defaults still apply.
  }
}

function readCounts(categoryId: number): Record<string, number> {
  try {
    const raw = store()?.getItem(AMOUNT_HISTORY_PREFIX + categoryId);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return {};
    }
    // Keep only finite non-negative integer counts; anything else on this device is dropped.
    return Object.fromEntries(
      Object.entries(parsed as Record<string, unknown>).filter(
        (pair): pair is [string, number] => Number.isInteger(pair[1]) && (pair[1] as number) >= 0,
      ),
    );
  } catch {
    return {};
  }
}

/** The six amounts saved most often for a category on this device, most frequent first. */
export function quickAmounts(categoryId: number): number[] {
  return Object.entries(readCounts(categoryId))
    .filter(([amount, count]) => Number.isFinite(Number(amount)) && typeof count === 'number')
    .sort(([left, leftCount], [right, rightCount]) => rightCount - leftCount || Number(left) - Number(right))
    .slice(0, QUICK_AMOUNT_COUNT)
    .map(([amount]) => Number(amount));
}

export function recordAmount(categoryId: number, amount: number): void {
  const counts = readCounts(categoryId);
  const key = String(amount);
  counts[key] = (counts[key] ?? 0) + 1;
  try {
    store()?.setItem(AMOUNT_HISTORY_PREFIX + categoryId, JSON.stringify(counts));
  } catch {
    // Quick amounts are a convenience only.
  }
}

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

/** Local calendar date `YYYY-MM-DD` (not UTC: entries are dated in the owner's time zone). */
export function todayIso(now = new Date()): string {
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export function nowTime(now = new Date()): string {
  return `${pad(now.getHours())}:${pad(now.getMinutes())}`;
}

export function findCategory(nodes: CategoryNode[], id: number | null): CategoryNode | null {
  if (id === null) {
    return null;
  }
  for (const node of nodes) {
    if (node.id === id) {
      return node;
    }
    const child = node.children.find(candidate => candidate.id === id);
    if (child) {
      return child;
    }
  }
  return null;
}

/** Rules the chips field offers: enabled and covering the entry date. */
export function rulesForDate(rules: RewardRule[], date: string): RewardRule[] {
  return rules.filter(
    rule => rule.is_enabled && (!rule.starts_on || rule.starts_on <= date) && (!rule.ends_on || rule.ends_on >= date),
  );
}

export function ruleLabel(rule: RewardRule): string {
  if (rule.method === 'percent' && rule.rate !== null) {
    return `${rule.name} (${Number(rule.rate)}%)`;
  }
  if (rule.method === 'fixed' && rule.fixed_amount !== null) {
    return `${rule.name} ($${Number(rule.fixed_amount)})`;
  }
  return rule.name;
}

/** `#午餐 #公司, LinePay` → `['午餐', '公司', 'LinePay']` (deduplicated, leading # removed). */
export function parseTags(text: string): string[] {
  return [
    ...new Set(
      text
        .split(/[\s,，、]+/)
        .map(tag => tag.replace(/^[#＃]+/, '').trim())
        .filter(Boolean),
    ),
  ];
}

export function evalOrNull(expression: string, decimals: number): number | null {
  if (!expression.trim()) {
    return null;
  }
  const result = evaluateAmount(expression, decimals);
  return result.ok ? result.value : null;
}

/** Server error → one line for the form: the lock message, the 422 field message, or a generic failure. */
export function saveErrorMessage(error: unknown): string {
  if (error instanceof HttpErrorResponse) {
    const body = error.error as { detail?: unknown; message?: unknown } | null;
    const detail = body?.detail;
    if (error.status === 409 && (detail === 'locked_until_cutover' || body?.message === 'locked_until_cutover')) {
      return 'MOZE 匯入資料，切換後可編輯';
    }
    if (Array.isArray(detail) && detail.length) {
      const first = detail[0] as { loc?: unknown[]; msg?: string };
      const field = Array.isArray(first.loc) ? String(first.loc.at(-1) ?? '') : '';
      return `${field} ${first.msg ?? ''}`.trim();
    }
    if (typeof detail === 'string') {
      return detail;
    }
    // 404 / 409 bodies are rewritten by shared_lib to {"code", "message", "trace_id"}.
    if (typeof body?.message === 'string' && body.message) {
      return body.message;
    }
  }
  return '儲存失敗，請稍後再試。';
}
