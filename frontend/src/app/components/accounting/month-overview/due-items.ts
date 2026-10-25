import { ScheduleInstance } from '../../../models/accounting.model';
import { dueCountdown } from '../billing/billing-math';
import { formatMoney } from '../format';
import { queueTitle } from '../reminders/schedule-queue';

/** A credit-card bill with a remaining statement, prepared by the timeline. */
export interface DueBill {
  key: string;
  name: string;
  date: string;
  amountText: string;
}

export interface DueItem {
  key: string;
  kind: 'bill' | 'schedule';
  name: string;
  date: string;
  amountText: string | null;
  dueText: string;
}

/** One convention with the bills (positive): an outflow shows as its absolute value, an inflow keeps its (plain) sign. */
function dueAmount(amount: string, currency: string): string {
  const value = Number(amount);
  return formatMoney(value < 0 ? Math.abs(value) : amount, currency);
}

/** The next dues across bills and pending 待完成交易, earliest first (bills before instances on a tie). */
export function nextDueItems(bills: readonly DueBill[], instances: readonly ScheduleInstance[], today: string, limit = 3): DueItem[] {
  const items: DueItem[] = [
    ...bills.map(bill => ({ ...bill, kind: 'bill' as const, dueText: dueCountdown(bill.date, today) })),
    ...instances
      .filter(item => item.status === 'pending')
      .map(item => ({
        key: `schedule:${item.id}`,
        kind: 'schedule' as const,
        name: queueTitle(item),
        date: item.due_date,
        amountText: item.totals[0] ? dueAmount(item.totals[0].amount, item.totals[0].currency) : null,
        dueText: dueCountdown(item.due_date, today),
      })),
  ];
  // Array.sort is stable, so bills (listed first) stay ahead on equal dates.
  return items.sort((a, b) => a.date.localeCompare(b.date)).slice(0, limit);
}
