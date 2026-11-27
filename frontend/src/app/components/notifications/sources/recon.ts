import { shortDate } from '../../accounting/dates';
import { InboxCounts, InboxOut } from '../../accounting/inbox/inbox.model';
import { isAskQuestion } from '../../accounting/inbox/inbox-view';
import { ReconProgress } from '../../accounting/statements/statement-view';
import { NotifLink, NotifTask, NotifTrackingRow } from '../notif-task';

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

/** The e-invoice flow's route (D10): beside the list from 1024 px, full screen below. */
export const EINVOICE_FLOW_ROUTE = '/notifications/einvoices';

/** How many sellers the task's meta names. */
const EINVOICE_META_SELLERS = 3;

/**
 * Every ambiguous / near invoice as ONE 要你確認 task (D10), counting 1. N is `counts.einvoice_ask` (the whole set,
 * not the listed page); the meta names up to three sellers and `when` the newest date of the listed ones. A snoozed
 * task wakes when an invoice is new (members), not when one is answered. The count rides along: a snooze saved
 * before members existed (`{count}` only) still wakes on growth. When the page cut the list (fewer listed than N),
 * members are left out: an invoice moving into the page is not new.
 */
export function einvoiceTask(inbox: Pick<InboxOut, 'counts' | 'einvoices'>): NotifTask | null {
  const n = inbox.counts.einvoice_ask ?? 0;
  if (n <= 0) return null;
  const asking = (inbox.einvoices ?? []).filter((invoice) => invoice.state !== 'unmatched');
  const sellers = [...new Set(asking.map((invoice) => invoice.seller_name))].slice(0, EINVOICE_META_SELLERS);
  const newest = asking.reduce<string | null>((max, i) => (max === null || i.issue_date > max ? i.issue_date : max), null);
  return {
    id: 'einvoice',
    source: 'ledger',
    from: 'recon',
    tier: 'confirm',
    icon: 'pi-receipt',
    title: `電子發票 · ${n} 張要確認`,
    when: newest ? `最新 ${shortDate(newest)}` : '',
    meta: sellers.length ? sellers.join('、') : null,
    amount: null,
    progress: null,
    primary: { label: '確認', route: EINVOICE_FLOW_ROUTE },
    secondary: [],
    sortKey: '9999-12-31',
    wake: asking.length >= n ? { count: n, members: asking.map((invoice) => String(invoice.id)) } : { count: n },
  };
}

/** The page for invoices without an entry (D11). */
export const EINVOICE_UNMATCHED_LINK: NotifLink = {
  label: '查看',
  route: EINVOICE_FLOW_ROUTE,
  queryParams: { state: 'unmatched' },
};

/**
 * D11: the 追蹤中 row for invoices without an entry, never counted. N is `counts.einvoice_unmatched` (the whole set;
 * 0 while reminders are off, absent on an older API: no row either way). The meta names the top three sellers among
 * the listed invoices by count, then by total, then first listed, with 「等 K 家」 for K sellers in all past three.
 */
export function einvoiceUnmatchedRow(inbox: Pick<InboxOut, 'counts' | 'einvoices'>): NotifTrackingRow | null {
  const n = inbox.counts.einvoice_unmatched ?? 0;
  if (n <= 0) return null;
  const sellers = new Map<string, { name: string; count: number; total: number }>();
  for (const invoice of (inbox.einvoices ?? []).filter((i) => i.state === 'unmatched')) {
    const key = invoice.seller_tax_id || invoice.seller_name;
    const seller = sellers.get(key) ?? { name: invoice.seller_name, count: 0, total: 0 };
    sellers.set(key, { ...seller, count: seller.count + 1, total: seller.total + Number(invoice.total_amount) });
  }
  const ranked = [...sellers.values()].sort((a, b) => b.count - a.count || b.total - a.total);
  const parts = ranked.slice(0, EINVOICE_META_SELLERS).map((s) => `${s.name} ${s.count}`);
  const tail = ranked.length > EINVOICE_META_SELLERS ? ` 等 ${ranked.length} 家` : '';
  return {
    id: 'einvoices',
    source: 'ledger',
    title: `有發票沒有帳 · ${n} 張`,
    meta: parts.join('、') + tail,
    count: n,
    link: EINVOICE_UNMATCHED_LINK,
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
