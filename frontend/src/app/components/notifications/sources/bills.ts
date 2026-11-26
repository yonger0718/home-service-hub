import { BillingEvent } from '../../accounting/billing/billing-math';
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
  /** Whether card-statement auto-pay will pay this bill (`autoPayPending`): it is then tracked, not a to-do (E3b). */
  readonly autoPay?: (event: BillingEvent, state: BillState) => boolean;
}

/** The figures beside the row's own amount (the remaining): the statement total when it differs, and what was paid. Null when it would only repeat. */
function billMeta(state: BillState, currency: string, amount: string): string | null {
  const parts: string[] = [];
  const statement = formatMoney(state.statement, currency);
  if (statement !== amount) parts.push(`帳單 ${statement}`);
  if (state.paid > 0) parts.push(`已繳 ${formatMoney(state.paid, currency)}`);
  return parts.length ? parts.join(' · ') : null;
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
    const payDate = state.payDate;
    if (payDate > horizon) continue;
    const auto = input.autoPay?.(event, state) ?? false;
    const tier: NotifTier = auto ? 'tracking' : payDate < today ? 'overdue' : payDate === today ? 'today' : 'tracking';
    const when = auto
      ? `${payDate === today ? '今天' : `${shortDate(payDate)} `}自動扣繳`
      : tier === 'overdue'
        ? `${shortDate(payDate)} 截止 · 逾期 ${daysBetween(payDate, today)} 天`
        : tier === 'today'
          ? '今天截止'
          : `${shortDate(payDate)} 截止`;
    const { currency } = state;
    const amount = formatMoney(state.remaining, currency);
    tasks.push({
      id: `bill:${state.accountId}:${state.due}`,
      source: 'ledger',
      from: 'bills',
      tier,
      icon: 'pi-credit-card',
      title: `${state.name}帳單`,
      when,
      meta: billMeta(state, currency, amount),
      amount,
      progress: null,
      primary: { label: '記繳款', route: `/accounting/accounts/${state.accountId}` },
      secondary: [],
      sortKey: payDate,
      // Remaining amount and debit day; the tier is not part of it (the snooze compares tiers itself).
      wake: { amount: `${state.remaining}`, due: payDate },
    });
  }
  return tasks;
}
