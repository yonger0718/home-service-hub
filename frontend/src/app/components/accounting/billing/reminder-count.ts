import { Counterparty, ScheduleInstance } from '../../../models/accounting.model';
import { BillingEvent } from './billing-math';
import { BillState } from './billing.service';

/**
 * 🔔 count (the timeline's and the nav badge's): cards with something left to pay, counterparties with open debt
 * rows, 待完成交易 items due today or earlier or partial (paused and ended definitions are not in the queue), and
 * 待處理 reconciliation inbox cards waiting for the owner's answer (`inboxAsk`, the inbox's `counts.ask`; 0 if unknown).
 */
export function countReminders(input: {
  dues: readonly BillingEvent[];
  bill: (event: BillingEvent) => BillState | null;
  counterparties: readonly Counterparty[];
  queue: readonly ScheduleInstance[];
  today: string;
  inboxAsk?: number;
}): number {
  return (
    input.dues.filter(event => (input.bill(event)?.remaining ?? 0) > 0).length +
    input.counterparties.filter(counterparty => (counterparty.open_count ?? 0) > 0).length +
    input.queue.filter(item => item.is_partial || item.due_date <= input.today).length +
    (input.inboxAsk ?? 0)
  );
}
