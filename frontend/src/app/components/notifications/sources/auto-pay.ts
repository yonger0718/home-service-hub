import { AUTO_PAY_REASON_TEXT, AUTO_PAY_TASK_REASONS, AutoPayRow } from '../../accounting/billing/auto-pay';
import { addDays, shortDate } from '../../accounting/dates';
import { formatMoney } from '../../accounting/format';
import { LedgerAccount } from '../../../models/accounting.model';
import { NotifLink, NotifTask } from '../notif-task';

/** Skipped auto-pay stays a task this many days after its debit day (spec: the last 30 days). */
export const AUTO_PAY_TASK_DAYS = 30;

/** The reasons fixed in the card's own settings: the task also links there. */
const LINK_REASONS: ReadonlySet<string> = new Set(['no_link', 'link_ineligible']);

export interface AutoPayTaskInput {
  readonly rows: readonly AutoPayRow[];
  readonly accounts: readonly LedgerAccount[];
  /**
   * Whether the skip no longer needs the owner: its statement's bill is paid, or the card has closed a later cycle (the
   * center follows only each card's current bill, so the skip ends with its cycle like the bill does). False when unknown.
   */
  readonly settled: (row: AutoPayRow) => boolean;
  readonly today: string;
}

/**
 * One 系統 task per statement whose auto-pay row is `skipped` with a task reason (D9: the transient reasons and
 * `expired`), its debit day within the last AUTO_PAY_TASK_DAYS days, and not `settled`.
 */
export function autoPayTasks(input: AutoPayTaskInput): NotifTask[] {
  const { rows, accounts, settled, today } = input;
  const oldest = addDays(today, -AUTO_PAY_TASK_DAYS);
  const tasks: NotifTask[] = [];
  for (const row of rows) {
    const reason = row.reason ?? '';
    if (row.status !== 'skipped' || !AUTO_PAY_TASK_REASONS.has(reason) || row.pay_date < oldest) continue;
    if (settled(row)) continue;
    const card = accounts.find(a => a.id === row.card_account_id);
    const secondary: NotifLink[] = LINK_REASONS.has(reason)
      ? [{ label: '設定扣款帳戶', route: `/accounting/accounts/${row.card_account_id}/settings` }]
      : [];
    tasks.push({
      id: `autopay:${row.statement_id}`,
      source: 'ledger',
      from: 'autopay',
      tier: 'system',
      icon: 'pi-credit-card',
      title: `自動扣繳未執行：${AUTO_PAY_REASON_TEXT[reason]}`,
      when: `${shortDate(row.pay_date)} 扣款`,
      meta: card?.name ?? null,
      amount: formatMoney(row.amount, card?.currency ?? 'TWD'),
      progress: null,
      primary: { label: '查看帳單', route: `/accounting/accounts/${row.card_account_id}/statements/${row.statement_id}` },
      secondary,
      sortKey: row.pay_date,
      // A new reason wakes a snoozed task (the same statement skipped again for another reason).
      wake: { key: reason },
    });
  }
  return tasks;
}
