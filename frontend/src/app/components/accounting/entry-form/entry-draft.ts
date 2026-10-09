import { CategoryNode, RewardRule, WritableEntryKind } from '../../../models/accounting.model';
import { pad } from '../accounting-ui';
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
const QUICK_AMOUNT_COUNT = 8;

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

function historyKey(categoryId: number, currency: string): string {
  return `${AMOUNT_HISTORY_PREFIX}${categoryId}.${currency}`;
}

/** Pre-currency frequency maps (`hh.accounting.amounts.<category>`): removed once, on the first read; not migrated. */
const LEGACY_HISTORY_KEY = /^hh\.accounting\.amounts\.\d+$/;
let legacyHistoryCleared = false;

function clearLegacyHistory(storage: Storage | null): void {
  if (legacyHistoryCleared || !storage) {
    return;
  }
  legacyHistoryCleared = true;
  const legacy = Array.from({ length: storage.length }, (_, index) => storage.key(index)).filter(
    (key): key is string => key !== null && LEGACY_HISTORY_KEY.test(key),
  );
  legacy.forEach(key => storage.removeItem(key));
}

/** Test hook: let the next read clean up legacy keys again. */
export function resetLegacyHistoryCleanup(): void {
  legacyHistoryCleared = false;
}

function readHistory(categoryId: number, currency: string): number[] {
  try {
    clearLegacyHistory(store());
    const raw = store()?.getItem(historyKey(categoryId, currency));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) {
      return [];
    }
    // Keep only finite non-negative amounts, once each; anything else on this device is dropped.
    const amounts = parsed.filter((value): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0);
    return [...new Set(amounts)].slice(0, QUICK_AMOUNT_COUNT);
  } catch {
    return [];
  }
}

/**
 * The last eight distinct amounts saved for a category in one currency on this device, most recent first. Keyed by
 * currency so an original-currency amount (¥1200) is never offered as TWD.
 */
export function quickAmounts(categoryId: number, currency: string): number[] {
  return readHistory(categoryId, currency);
}

export function recordAmount(categoryId: number, currency: string, amount: number): void {
  if (!Number.isFinite(amount) || amount < 0) {
    return;
  }
  const history = [amount, ...readHistory(categoryId, currency).filter(other => other !== amount)].slice(0, QUICK_AMOUNT_COUNT);
  try {
    store()?.setItem(historyKey(categoryId, currency), JSON.stringify(history));
  } catch {
    // Quick amounts are a convenience only.
  }
}

/** Local calendar date `YYYY-MM-DD`; owned by `dates.ts` (Task 25), re-exported for the form's callers. */
export { todayIso } from '../dates';

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
