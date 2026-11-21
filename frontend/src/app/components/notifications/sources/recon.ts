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

/** The one place that knows where a statement task goes (PR D changes it to /notifications/recon/:statementId). */
export function reconTaskRoute(accountId: number, statementId: number): string {
  return `/accounting/accounts/${accountId}/statements/${statementId}`;
}

/** Ids of the accounts that have an unfolded ask item (whose statement lists carry the progress). */
export function askAccountIds(inbox: InboxOut): number[] {
  return inbox.accounts
    .filter((account) => account.items.some((item) => !item.folded && isAskQuestion(item.question)))
    .map((account) => account.id);
}

/** One task per statement with unfolded ask items; `progress` by statement id (absent → no bar, no meta). */
export function reconTasks(inbox: InboxOut, progress: ReadonlyMap<number, ReconProgress>): NotifTask[] {
  const tasks: NotifTask[] = [];
  for (const account of inbox.accounts) {
    const byStatement = new Map<number, { periodEnd: string; n: number }>();
    for (const item of account.items) {
      if (item.folded || !isAskQuestion(item.question)) continue;
      const entry = byStatement.get(item.statement.id);
      if (entry) entry.n += 1;
      else byStatement.set(item.statement.id, { periodEnd: item.statement.period_end, n: 1 });
    }
    for (const [statementId, { periodEnd, n }] of byStatement) {
      const known = progress.get(statementId) ?? null;
      tasks.push({
        id: `recon:${statementId}`,
        source: 'ledger',
        from: 'recon',
        tier: 'confirm',
        icon: 'pi-check-square',
        title: `對帳 · ${account.name}（至 ${shortDate(periodEnd)}）`,
        when: `${n} 筆要你決定`,
        meta: known ? `已對上 ${known.k} / ${known.n} 行` : null,
        amount: null,
        progress: known,
        primary: { label: '對帳', route: reconTaskRoute(account.id, statementId) },
        secondary: [],
        sortKey: periodEnd,
        fingerprint: `${n}`,
      });
    }
  }
  return tasks;
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
    meta: '到提醒中心的待處理回答',
    amount: null,
    progress: null,
    primary: REMINDERS_TODO,
    secondary: [],
    sortKey: '9999-12-31',
    fingerprint: `${n}`,
  };
}

/** R25: `counts.ask_items` minus the unfolded items received; 0 when everything is listed (never negative). */
export function inboxMissing(inbox: InboxOut): number {
  const received = inbox.accounts.reduce(
    (sum, account) =>
      sum + account.items.filter((item) => !item.folded && isAskQuestion(item.question)).length,
    0,
  );
  return Math.max(0, (inbox.counts.ask_items ?? 0) - received);
}

/** R25: the catch-all task for `missing > 0`, else null. */
export function reconOverflowTask(missing: number): NotifTask | null {
  if (missing <= 0) return null;
  return {
    id: 'recon:more',
    source: 'ledger',
    from: 'recon',
    tier: 'confirm',
    icon: 'pi-list',
    title: '還有對帳項目未列出',
    when: `還有 ${missing} 筆要你決定`,
    meta: '到提醒中心的待處理查看',
    amount: null,
    progress: null,
    primary: REMINDERS_TODO,
    secondary: [],
    sortKey: '9999-12-30',
    fingerprint: `${missing}`,
  };
}
