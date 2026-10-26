import { Injectable, Signal, computed, inject, signal, untracked } from '@angular/core';

import { AccountingService } from '../../../services/accounting.service';
import { Period } from '../cycle';
import { BillingEvent, billBalance, payDateOf, paymentWindowEnd } from './billing-math';

/**
 * Payments to a card read per payment window (closing → `paymentWindowEnd`), one page per card (the master and each
 * combined card), no paging. Far more than happen in practice; a card with more than this many `transfer_in` rows in
 * the window would understate `paid` (and overstate `remaining`).
 */
export const BILL_PAYMENT_LIMIT = 100;

/** A due event's resolved statement (the same figures for the calendar, the reminder centre and the accounts list). */
export interface BillState {
  accountId: number;
  name: string;
  period: Period;
  /** The payment due date (`YYYY-MM-DD`). */
  due: string;
  /** The bank's debit day: the first business day on or after `due` (equal to `due` when nothing shifts). */
  payDate: string;
  currency: string;
  /** −spend of the cycle: positive for a bill, ≤ 0 for a zero or refund-heavy statement. */
  statement: number;
  /**
   * Σ `transfer_in` to the master and its combined cards (副卡) from the closing date through `paymentWindowEnd` (the
   * day before the next closing, capped at the day of the read): a late payment still clears the bill, and a payment
   * recorded on a 副卡 counts as much as one on the master.
   */
  paid: number;
  remaining: number;
}

/**
 * One round's reads: per card of the statement (the master and its combined cards) a period summary and a page of
 * payments. The state is committed once all have landed; `null` = that read failed.
 */
interface Pending {
  generation: number;
  /** The round's inputs beyond its key (combined cards, period start): a change starts a new round at once. */
  fingerprint: string;
  id: number;
  /** Per account id: its period spend and currency. */
  spends: Map<number, { spend: string; currency: string } | null>;
  /** Per account id: the amounts of its `transfer_in` entries in the payment window. */
  payments: Map<number, string[] | null>;
}

/** Cache key of a due event's statement: the card, the cycle and the due date (a changed due rule is a new key). */
export function billKey(event: Pick<BillingEvent, 'accountId' | 'period' | 'date'>): string {
  return `${event.accountId}:${event.period.end}:${event.date}`;
}

/** A completed round's failure: undefined when none, null for a failed read, `幣別不同` for a currency mismatch. */
function failureOf(round: Pending, cards: number[]): string | null | undefined {
  const spends = cards.map(id => round.spends.get(id));
  if (cards.some(id => round.payments.get(id) === null) || spends.some(spend => spend === null)) {
    return null;
  }
  const currency = spends[0]?.currency;
  return spends.some(spend => spend?.currency !== currency) ? '幣別不同' : undefined;
}

/** A round's inputs beyond its key: the combined cards (sorted) and the period start. */
function fingerprint(event: BillingEvent): string {
  return `${[...(event.childIds ?? [])].sort((a, b) => a - b).join(',')}|${event.period.start}`;
}

/**
 * The single source of statement figures: one summary read + one payments read per card of a due event and change
 * round, shared by every consumer. A round ends on `entriesChanged` or `accountsChanged`; the previous figures stay shown until
 * the new round has fully landed, and the answers of a superseded round are dropped.
 */
@Injectable({ providedIn: 'root' })
export class BillingService {
  private readonly accounting = inject(AccountingService);
  private readonly states = signal<ReadonlyMap<string, BillState | null>>(new Map());
  /**
   * Keys whose latest round failed: a failed read (reason null) or a combined card in another currency than the
   * master's (`幣別不同`; the spends cannot be added without a conversion).
   */
  private readonly failures = signal<ReadonlyMap<string, string | null>>(new Map());
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
      const round = this.rounds.get(billKey(event));
      if (round?.generation !== generation || round.fingerprint !== fingerprint(event)) {
        this.start(event, today, generation);
      }
    }
  }

  /** Reads the statement again in a new round (the reminder centre's 重試 after a failed read). */
  retry(event: BillingEvent, today: string): void {
    this.start(event, today, untracked(this.generation));
  }

  /**
   * Reads every statement of `dues` again in a new round, even one already read in the current change round: payments
   * made on another device, or a failed read, show up without a local write. As with any round, the previous figures
   * stay shown until the new answers have fully landed, and an older round's answers are dropped.
   */
  refresh(dues: readonly BillingEvent[], today: string): void {
    const generation = untracked(this.generation);
    for (const event of dues) {
      this.start(event, today, generation);
    }
  }

  private start(event: BillingEvent, today: string, generation: number): void {
    const key = billKey(event);
    const round: Pending = {
      generation, fingerprint: fingerprint(event), id: ++this.requestId, spends: new Map(), payments: new Map(),
    };
    this.rounds.set(key, round);
    const cards = [event.accountId, ...(event.childIds ?? [])];
    const commit = () => {
      if (cards.some(id => !round.spends.has(id) || !round.payments.has(id))) {
        return;
      }
      const failure = failureOf(round, cards);
      this.failures.update(failures => {
        const next = new Map(failures);
        if (failure === undefined) {
          next.delete(key);
        } else {
          next.set(key, failure);
        }
        return next;
      });
      this.states.update(states => new Map(states).set(key, this.resolve(event, round, cards)));
    };
    const current = () => this.rounds.get(key)?.id === round.id;
    // Combined cards (副卡) are read with the master, over the same period and window, as part of the same round.
    const paidTo = paymentWindowEnd(event, today);
    for (const accountId of cards) {
      this.accounting.getAccountSummary(accountId, event.period.start, event.period.end).subscribe({
        next: summary => {
          if (current()) {
            round.spends.set(accountId, { spend: summary.spend, currency: summary.currency });
            commit();
          }
        },
        error: () => {
          if (current()) {
            round.spends.set(accountId, null);
            commit();
          }
        },
      });
      this.accounting
        .getEntries(accountId, { kind: 'transfer_in', date_from: event.period.end, date_to: paidTo, limit: BILL_PAYMENT_LIMIT })
        .subscribe({
          next: page => {
            if (current()) {
              round.payments.set(accountId, page.items.map(entry => entry.amount));
              commit();
            }
          },
          error: () => {
            if (current()) {
              round.payments.set(accountId, null);
              commit();
            }
          },
        });
    }
  }

  /** The due event's statement once resolved (reactive); null while unresolved or when a read failed. */
  bill(event: BillingEvent): BillState | null {
    return this.states().get(billKey(event)) ?? null;
  }

  /** True when the due event's latest round failed (it then has no figures: never "all clear"). */
  failed(event: BillingEvent): boolean {
    return this.failures().has(billKey(event));
  }

  /** Why the latest round failed beyond a failed read (`幣別不同`); null for a read error or when it did not fail. */
  failureReason(event: BillingEvent): string | null {
    return this.failures().get(billKey(event)) ?? null;
  }

  /** True once the due event's statement has landed or failed (at least once); false while it is first being read. */
  isSettled(event: BillingEvent): boolean {
    return this.states().has(billKey(event));
  }

  /**
   * The statement: Σ period spend of the master and its combined cards, less the payments to any of them. Unresolved
   * (and failed, see `failureOf`) when any read failed or a combined card's currency differs from the master's.
   */
  private resolve(event: BillingEvent, round: Pending, cards: number[]): BillState | null {
    const spends = cards.map(id => round.spends.get(id));
    const payments = cards.map(id => round.payments.get(id));
    const master = spends[0];
    if (!master || payments.some(page => !Array.isArray(page)) || spends.some(spend => !spend || spend.currency !== master.currency)) {
      return null;
    }
    const total = spends.reduce((sum, spend) => sum + Number(spend!.spend), 0);
    return {
      accountId: event.accountId,
      name: event.name,
      period: event.period,
      due: event.date,
      payDate: payDateOf(event),
      currency: master.currency,
      ...billBalance(String(total), payments.flatMap(page => page!)),
    };
  }
}
