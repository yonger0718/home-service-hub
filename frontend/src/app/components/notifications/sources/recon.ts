import { shortDate } from '../../accounting/dates';
import { InboxCounts, InboxOut } from '../../accounting/inbox/inbox.model';
import { isAskQuestion } from '../../accounting/inbox/inbox-view';
import { ReconProgress } from '../../accounting/statements/statement-view';
import { NotifLink, NotifTask } from '../notif-task';

/** The API's maximum page (`routers/reconciliation.py:70`); the service reads the inbox with it. */
export const INBOX_READ_LIMIT = 1000;

const REMINDERS_TODO: NotifLink = {
  label: '查看',
  route: '/accounting/reminders',
  queryParams: { tab: 'todo' },
};

/**
 * The one place that knows where a statement task goes: the review flow on the notifications page (beside the list
 * from 1024 px, full screen below). The statement id alone names it; `_accountId` is kept for the callers' shape. The
 * statement page stays one tap away through the flow's 看整張帳單.
 */
export function reconTaskRoute(_accountId: number, statementId: number): string {
  return `/notifications/recon/${statementId}`;
}

/** Ids of the accounts that have an unfolded ask item (whose statement lists carry the progress). */
export function askAccountIds(inbox: InboxOut): number[] {
  return inbox.accounts
    .filter((account) => account.items.some((item) => !item.folded && isAskQuestion(item.question)))
    .map((account) => account.id);
}

/** One task per statement with unfolded ask items; `progress` by statement id (absent → no bar, no meta). */
export function reconTasks(inbox: InboxOut, progress: ReadonlyMap<number, ReconProgress>): NotifTask[] {
  // Keyed by statement id alone (the task id): a statement id seen under two accounts merges into the first one's task.
  const byStatement = new Map<
    number,
    { accountId: number; accountName: string; periodEnd: string; n: number }
  >();
  for (const account of inbox.accounts) {
    for (const item of account.items) {
      if (item.folded || !isAskQuestion(item.question)) continue;
      const entry = byStatement.get(item.statement.id);
      if (entry) entry.n += 1;
      else
        byStatement.set(item.statement.id, {
          accountId: account.id,
          accountName: account.name,
          periodEnd: item.statement.period_end,
          n: 1,
        });
    }
  }
  return [...byStatement].map(([statementId, { accountId, accountName, periodEnd, n }]) => {
    const known = progress.get(statementId) ?? null;
    return {
      id: `recon:${statementId}`,
      source: 'ledger',
      from: 'recon',
      tier: 'confirm',
      icon: 'pi-check-square',
      title: `對帳 · ${accountName}（至 ${shortDate(periodEnd)}）`,
      when: `${n} 筆要你決定`,
      meta: known ? `已對上 ${known.k} / ${known.n} 行` : null,
      amount: null,
      progress: known,
      primary: { label: '對帳', route: reconTaskRoute(accountId, statementId) },
      secondary: [],
      sortKey: periodEnd,
      wake: { count: n },
    } satisfies NotifTask;
  });
}

export function einvoiceTask(counts: Pick<InboxCounts, 'einvoice_ask'>): NotifTask | null {
  const n = counts.einvoice_ask ?? 0;
  if (n <= 0) return null;
  return {
    id: 'einvoice',
    source: 'ledger',
    from: 'recon',
    tier: 'confirm',
    icon: 'pi-receipt',
    title: `${n} 張發票要確認`,
    when: '電子發票',
    meta: '到提醒中心回答',
    amount: null,
    progress: null,
    primary: REMINDERS_TODO,
    secondary: [],
    sortKey: '9999-12-31',
    wake: { count: n },
  };
}

/**
 * R25: how many unfolded ask items the page cut off. `counts.ask_items` minus the unfolded items received, never
 * negative. When `counts` or `ask_items` is absent (an older API; the model types call them required) the total is
 * unknown: `'unknown'` if the response is full (all items received, folded included, >= `limit`), else 0.
 */
export function inboxMissing(inbox: InboxOut, limit: number = INBOX_READ_LIMIT): number | 'unknown' {
  const received = inbox.accounts.flatMap((account) => account.items);
  const total = (inbox.counts as Partial<InboxCounts> | undefined)?.ask_items;
  if (typeof total !== 'number') return received.length >= limit ? 'unknown' : 0;
  const listed = received.filter((item) => !item.folded && isAskQuestion(item.question)).length;
  return Math.max(0, total - listed);
}

/** R25: the catch-all task for `missing > 0` (no number when `'unknown'`), else null. */
export function reconOverflowTask(missing: number | 'unknown'): NotifTask | null {
  if (missing !== 'unknown' && missing <= 0) return null;
  const known = missing !== 'unknown';
  return {
    id: 'recon:more',
    source: 'ledger',
    from: 'recon',
    tier: 'confirm',
    icon: 'pi-list',
    title: known ? `還有 ${missing} 筆對帳項目未列出` : '還有對帳項目未列出',
    when: '',
    meta: '到提醒中心的待處理查看',
    amount: null,
    progress: null,
    primary: REMINDERS_TODO,
    secondary: [],
    sortKey: '9999-12-30',
    wake: known ? { count: missing } : { key: 'more' },
  };
}
