export type NotifTier = 'overdue' | 'today' | 'confirm' | 'system' | 'tracking';
export type NotifSource = 'ledger' | 'portfolio';
export type NotifSourceKey = 'bills' | 'schedules' | 'recon' | 'importReview' | 'debts' | 'dividends' | 'imports' | 'cathay';

export interface NotifLink {
  readonly label: string;
  readonly route: string;
  readonly queryParams?: Readonly<Record<string, string>>;
  /** A `#fragment` on the route (never sent to the server). */
  readonly fragment?: string;
}

export type NotifCommand =
  | { readonly kind: 'postInstance'; readonly instanceId: number; readonly name: string }
  | { readonly kind: 'catchUp'; readonly definitionId: number; readonly name: string }
  | { readonly kind: 'ackCathay'; readonly tradeDate: string; readonly name: string };

export interface NotifCommandAction {
  readonly label: string;
  readonly command: NotifCommand;
}

export interface NotifTask {
  /** bill:<accountId>:<due> | sched:<definitionId> | recon:<statementId> | import:<runId|started_at> | einvoice */
  readonly id: string;
  readonly source: NotifSource;
  readonly from: NotifSourceKey;
  readonly tier: NotifTier;
  /** A `pi-…` class or an emoji (schedule category icon). */
  readonly icon: string;
  readonly title: string;
  /** Bold part of line 2; red in 已逾期. */
  readonly when: string;
  /** Muted part of line 2. */
  readonly meta: string | null;
  /** Already formatted. */
  readonly amount: string | null;
  readonly progress: { readonly k: number; readonly n: number } | null;
  readonly primary: NotifLink | NotifCommandAction;
  readonly secondary: readonly NotifLink[];
  /** ISO date or datetime; ascending = oldest first. */
  readonly sortKey: string;
  /** What a snooze compares to wake the task early (R10, PR E2): see `snooze.ts` `wakes`. */
  readonly wake: NotifWake;
}

/**
 * A task's wake values. A snoozed task wakes early when `count` grows (a lower count does not wake), `amount` or `due`
 * changes, a `members` id is new, or `key` changes; an absent field never wakes. Tier escalation to 已逾期 is checked
 * separately (owner rulings 2026-11-25).
 */
export interface NotifWake {
  readonly count?: number;
  readonly amount?: string;
  readonly due?: string;
  readonly members?: readonly string[];
  readonly key?: string;
}

export interface NotifTrackingRow {
  readonly id: 'debts' | 'upcoming' | 'dividends';
  readonly source: NotifSource;
  readonly title: string;
  readonly meta: string;
  readonly count: number;
  readonly link: NotifLink;
}

export interface NotifSummary {
  readonly total: number;
  readonly overdue: number;
  readonly tone: 'red' | 'primary';
  readonly label: string;
}

export type NotifChip = 'all' | NotifSource;

export const COUNTED_TIERS: readonly NotifTier[] = ['overdue', 'today', 'confirm', 'system'];
export const TIER_LABELS: Record<NotifTier, string> = {
  overdue: '已逾期',
  today: '今天',
  confirm: '要你確認',
  system: '系統',
  tracking: '追蹤中',
};
export const SOURCE_TAGS: Record<NotifSource, string> = { ledger: '財務', portfolio: '投資' };
export const SOURCE_NAMES: Record<NotifSourceKey, string> = {
  bills: '帳單',
  schedules: '週期',
  recon: '對帳',
  importReview: '匯入檢查',
  debts: '借還款',
  dividends: '股利',
  imports: '匯入',
  cathay: '國泰對帳單',
};
export const NO_TASKS: NotifSummary = { total: 0, overdue: 0, tone: 'primary', label: '通知' };

const TIER_ORDER: readonly NotifTier[] = [...COUNTED_TIERS, 'tracking'];

export function isCommand(action: NotifLink | NotifCommandAction): action is NotifCommandAction {
  return 'command' in action;
}

function counted(tasks: readonly NotifTask[]): NotifTask[] {
  return tasks.filter((t) => COUNTED_TIERS.includes(t.tier));
}

/** Tier order of COUNTED_TIERS then tracking; inside a tier by sortKey, then id. Returns a new array. */
export function sortTasks(tasks: readonly NotifTask[]): NotifTask[] {
  const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
  return [...tasks].sort(
    (a, b) =>
      TIER_ORDER.indexOf(a.tier) - TIER_ORDER.indexOf(b.tier) ||
      cmp(a.sortKey, b.sortKey) ||
      cmp(a.id, b.id),
  );
}

/** Counted tasks only. overdue = tasks in 已逾期; tone red when any is 已逾期 or 系統. */
export function summarize(tasks: readonly NotifTask[]): NotifSummary {
  const c = counted(tasks);
  if (c.length === 0) return NO_TASKS;
  const overdue = c.filter((t) => t.tier === 'overdue').length;
  const red = c.some((t) => t.tier === 'overdue' || t.tier === 'system');
  return {
    total: c.length,
    overdue,
    tone: red ? 'red' : 'primary',
    label: notifLabel(c.length, overdue),
  };
}

/** `通知` at 0; `通知，N 件要處理`; `通知，N 件要處理，M 件已逾期` when M > 0. */
export function notifLabel(total: number, overdue: number): string {
  if (total <= 0) return '通知';
  return `通知，${total} 件要處理${overdue > 0 ? `，${overdue} 件已逾期` : ''}`;
}

/** Counted tasks per chip. */
export function chipCounts(tasks: readonly NotifTask[]): Record<NotifChip, number> {
  const c = counted(tasks);
  return {
    all: c.length,
    ledger: c.filter((t) => t.source === 'ledger').length,
    portfolio: c.filter((t) => t.source === 'portfolio').length,
  };
}

/** `無法讀取：帳單、對帳` in SOURCE_NAMES key order; null when empty. */
export function failureLine(keys: readonly NotifSourceKey[]): string | null {
  const names = (Object.keys(SOURCE_NAMES) as NotifSourceKey[])
    .filter((k) => keys.includes(k))
    .map((k) => SOURCE_NAMES[k]);
  return names.length ? `無法讀取：${names.join('、')}` : null;
}
