import { Injectable, Signal, computed, inject, signal, untracked } from '@angular/core';

import { AccountingService } from '../../../services/accounting.service';
import { Period } from '../cycle';
import { BillingEvent, billBalance, paymentWindowEnd } from './billing-math';

/**
 * Payments to a card read per payment window (closing → `paymentWindowEnd`), one page, no paging. Far more than happen
 * in practice; a window with more than this many `transfer_in` rows would understate `paid` (and overstate `remaining`).
 */
export const BILL_PAYMENT_LIMIT = 100;

/** A due event's resolved statement (the same figures for the calendar, the reminder centre and the accounts list). */
export interface BillState {
  accountId: number;
  name: string;
  period: Period;
  /** The payment due date (`YYYY-MM-DD`). */
  due: string;
  currency: string;
  /** −spend of the cycle: positive for a bill, ≤ 0 for a zero or refund-heavy statement. */
  statement: number;
  /**
   * Σ `transfer_in` to the card from the closing date through `paymentWindowEnd` (the day before the next closing,
   * capped at the day of the read): a late payment still clears the bill.
   */
  paid: number;
  remaining: number;
}

/** One round's halves; the state is committed once both have landed (`null` = that read failed). */
interface Pending {
  generation: number;
  id: number;
  spend?: string | null;
  currency?: string;
  payments?: string[] | null;
}

/** Cache key of a due event's statement: the card, the cycle and the due date (a changed due rule is a new key). */
export function billKey(event: Pick<BillingEvent, 'accountId' | 'period' | 'date'>): string {
  return `${event.accountId}:${event.period.end}:${event.date}`;
}

/**
 * The single source of statement figures: one summary read + one payments read per due event and change round,
 * shared by every consumer. A round ends on `entriesChanged` or `accountsChanged`; the previous figures stay shown until
 * the new round has fully landed, and the answers of a superseded round are dropped.
 */
@Injectable({ providedIn: 'root' })
export class BillingService {
  private readonly accounting = inject(AccountingService);
  private readonly states = signal<ReadonlyMap<string, BillState | null>>(new Map());
  private readonly rounds = new Map<string, Pending>();
  private requestId = 0;

  /**
   * Monotonic change round (both counters only grow). Consumers read it in the effect that calls `ensure`, so a write
   * re-resolves what they show.
   */
  readonly generation: Signal<number> = computed(() => this.accounting.entriesChanged() + this.accounting.accountsChanged());

  /**
   * Requests the statements of `dues` not yet asked for in the current change round. The payment window is capped at
   * `today` when read; a day rollover alone does not re-read (a payment only appears through an entry write, which
   * starts a new round), so a future-dated payment is picked up on the next change round.
   */
  ensure(dues: readonly BillingEvent[], today: string): void {
    const generation = untracked(this.generation);
    for (const event of dues) {
      const key = billKey(event);
      if (this.rounds.get(key)?.generation === generation) {
        continue;
      }
      const round: Pending = { generation, id: ++this.requestId };
      this.rounds.set(key, round);
      const patch = (update: Partial<Pending>) => {
        if (this.rounds.get(key)?.id !== round.id) {
          return;
        }
        Object.assign(round, update);
        if (round.spend === undefined || round.payments === undefined) {
          return;
        }
        this.states.update(states => new Map(states).set(key, this.resolve(event, round)));
      };
      this.accounting.getAccountSummary(event.accountId, event.period.start, event.period.end).subscribe({
        next: summary => patch({ spend: summary.spend, currency: summary.currency }),
        error: () => patch({ spend: null }),
      });
      this.accounting
        .getEntries(event.accountId, {
          kind: 'transfer_in',
          date_from: event.period.end,
          date_to: paymentWindowEnd(event, today),
          limit: BILL_PAYMENT_LIMIT,
        })
        .subscribe({
          next: page => patch({ payments: page.items.map(entry => entry.amount) }),
          error: () => patch({ payments: null }),
        });
    }
  }

  /** The due event's statement once resolved (reactive); null while unresolved or when a read failed. */
  bill(event: BillingEvent): BillState | null {
    return this.states().get(billKey(event)) ?? null;
  }

  /** True once the due event's statement has landed or failed (at least once); false while it is first being read. */
  isSettled(event: BillingEvent): boolean {
    return this.states().has(billKey(event));
  }

  private resolve(event: BillingEvent, round: Pending): BillState | null {
    if (typeof round.spend !== 'string' || !Array.isArray(round.payments)) {
      return null;
    }
    return {
      accountId: event.accountId,
      name: event.name,
      period: event.period,
      due: event.date,
      currency: round.currency ?? 'TWD',
      ...billBalance(round.spend, round.payments),
    };
  }
}
