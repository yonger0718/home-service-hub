import { LedgerAccount } from '../../../models/accounting.model';
import { pad, shiftMonth } from '../accounting-ui';
import { Period, dueDate, shiftPeriod, statementPeriod } from '../cycle';
import { addDays } from '../dates';

export type BillingCard = Pick<
  LedgerAccount,
  'id' | 'name' | 'is_credit' | 'is_archived' | 'closing_day' | 'due_rule' | 'due_value'
>;

/** A card's statement closing day or payment due day; `period` is the statement cycle it belongs to. */
export interface BillingEvent {
  accountId: number;
  name: string;
  kind: 'closing' | 'due';
  date: string;
  period: Period;
}

/**
 * Cycles walked back from the one containing the month's 1st: a due day can follow its closing by up to ~3 months
 * (`days_after_closing` ≤ 90). That anchor cycle always closes inside the month, so no later cycle can contribute.
 */
const CYCLES_BEFORE = 3;
/** The 近 7 天 window: today and the six days after it. */
export const UPCOMING_DAYS = 7;

/** Non-archived credit accounts with a closing day: the only ones with billing events. */
export function billingCards<T extends BillingCard>(accounts: T[]): T[] {
  return accounts.filter(account => account.is_credit && !account.is_archived && account.closing_day !== null);
}

/**
 * Closing and due events falling inside `month` (`YYYY-MM`), sorted by date (due before closing on the same
 * day, then in card order). A card without a due rule has closing events only.
 */
export function billingEvents(cards: BillingCard[], month: string): BillingEvent[] {
  const events: BillingEvent[] = [];
  for (const card of billingCards(cards)) {
    const closingDay = card.closing_day as number;
    const anchor = statementPeriod(closingDay, `${month}-01`);
    for (let delta = -CYCLES_BEFORE; delta <= 0; delta++) {
      const period = shiftPeriod(closingDay, anchor, delta);
      const base = { accountId: card.id, name: card.name, period };
      if (period.end.slice(0, 7) === month) {
        events.push({ ...base, kind: 'closing', date: period.end });
      }
      const due = dueDate(period.end, card.due_rule, card.due_value);
      if (due !== null && due.slice(0, 7) === month) {
        events.push({ ...base, kind: 'due', date: due });
      }
    }
  }
  const kindOrder = { due: 0, closing: 1 };
  return events
    .map((event, index) => ({ event, index }))
    .sort(
      (a, b) =>
        a.event.date.localeCompare(b.event.date) || kindOrder[a.event.kind] - kindOrder[b.event.kind] || a.index - b.index,
    )
    .map(({ event }) => event);
}

/** Due events from `today` through `today + 6` (this month's and next month's). */
export function upcomingDues(cards: BillingCard[], today: string): BillingEvent[] {
  const [year, month] = today.split('-').map(Number);
  const [nextYear, nextMonth] = shiftMonth(year, month, 1);
  const last = addDays(today, UPCOMING_DAYS - 1);
  return [...billingEvents(cards, today.slice(0, 7)), ...billingEvents(cards, `${nextYear}-${pad(nextMonth)}`)].filter(
    event => event.kind === 'due' && event.date >= today && event.date <= last,
  );
}

/** A statement's figures: `remaining > 0` is the only case the billing hints show. */
export interface BillBalance {
  /** −spend of the cycle: positive for a bill, ≤ 0 for a zero or refund-heavy statement. */
  statement: number;
  /** Σ `transfer_in` to the card between the closing date and the due date (inclusive). */
  paid: number;
  remaining: number;
}

/** Amounts are 4-dp decimal strings: round the float sums back to 4 dp so a fully paid bill is exactly 0. */
const round4 = (value: number) => Math.round(value * 1e4) / 1e4 || 0;

export function billBalance(spend: string, payments: string[]): BillBalance {
  const statement = round4(-Number(spend));
  const paid = round4(payments.reduce((sum, amount) => sum + Number(amount), 0));
  return { statement, paid, remaining: round4(statement - paid) };
}
