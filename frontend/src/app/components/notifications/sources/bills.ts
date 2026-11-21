import { BillingEvent, payDateOf } from '../../accounting/billing/billing-math';
import { BillState } from '../../accounting/billing/billing.service';
import { addDays, daysBetween, shortDate } from '../../accounting/dates';
import { formatMoney } from '../../accounting/format';
import { NotifTask, NotifTier } from '../notif-task';

/** A card bill enters 追蹤中 this many days before its debit day. */
export const BILL_TRACKING_DAYS = 7;

export interface BillTaskInput {
  readonly dues: readonly BillingEvent[];
  readonly bill: (event: BillingEvent) => BillState | null;
  readonly today: string;
}

/** One task per due event whose bill is resolved with remaining > 0 and whose payDate is ≤ today + 7. */
export function billTasks(input: BillTaskInput): NotifTask[] {
  const { dues, bill, today } = input;
  const horizon = addDays(today, BILL_TRACKING_DAYS);
  const tasks: NotifTask[] = [];
  for (const event of dues) {
    if (event.kind !== 'due') continue;
    const state = bill(event);
    if (!state || !(state.remaining > 0)) continue;
    const payDate = state.payDate ?? payDateOf(event);
    if (payDate > horizon) continue;
    const tier: NotifTier = payDate < today ? 'overdue' : payDate === today ? 'today' : 'tracking';
    const when =
      tier === 'overdue'
        ? `${shortDate(payDate)} 截止 · 逾期 ${daysBetween(payDate, today)} 天`
        : tier === 'today'
          ? '今天截止'
          : `${shortDate(payDate)} 截止`;
    const { currency } = state;
    tasks.push({
      id: `bill:${state.accountId}:${state.due}`,
      source: 'ledger',
      from: 'bills',
      tier,
      icon: 'pi-credit-card',
      title: `${state.name}帳單`,
      when,
      meta:
        `帳單 ${formatMoney(state.statement, currency)}` +
        (state.paid > 0 ? ` · 已繳 ${formatMoney(state.paid, currency)}` : ''),
      amount: formatMoney(state.remaining, currency),
      progress: null,
      primary: { label: '記繳款', route: `/accounting/accounts/${state.accountId}` },
      secondary: [],
      sortKey: payDate,
      fingerprint: `${state.remaining}|${payDate}`,
    });
  }
  return tasks;
}
