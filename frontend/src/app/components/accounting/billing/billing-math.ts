import { LedgerAccount } from '../../../models/accounting.model';
import { pad, shiftMonth } from '../accounting-ui';
import { Period, dueDate, shiftPeriod, statementPeriod } from '../cycle';
import { addDays, daysBetween, shortDate } from '../dates';

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

/** The reminder centre leaves out cards whose current statement falls due further away than this. */
export const REMINDER_HORIZON_DAYS = 45;

/**
 * Each card's current statement as a due event: the last closed cycle (the one before the cycle containing `today`;
 * the closing day itself still belongs to the open cycle), whether its due date is ahead or already past. Cards
 * without a due rule have no due event and are left out.
 */
export function currentDues(cards: BillingCard[], today: string): BillingEvent[] {
  const events: BillingEvent[] = [];
  for (const card of billingCards(cards)) {
    const closingDay = card.closing_day as number;
    const period = shiftPeriod(closingDay, statementPeriod(closingDay, today), -1);
    const due = dueDate(period.end, card.due_rule, card.due_value);
    if (due !== null) {
      events.push({ accountId: card.id, name: card.name, kind: 'due', date: due, period });
    }
  }
  return events;
}

/** The reminder centre's statements: `currentDues` falling due at most `REMINDER_HORIZON_DAYS` from `today`. */
export function reminderDues(cards: BillingCard[], today: string): BillingEvent[] {
  return currentDues(cards, today).filter(event => daysBetween(today, event.date) <= REMINDER_HORIZON_DAYS);
}

/** Reminder centre countdown: `還有 N 天` (N ≥ 2), `明天`, `今天`, `已逾期 N 天`. */
export function dueCountdown(due: string, today: string): string {
  const days = daysBetween(today, due);
  if (days < 0) {
    return `已逾期 ${-days} 天`;
  }
  return days === 0 ? '今天' : days === 1 ? '明天' : `還有 ${days} 天`;
}

/** Accounts-list pill: `MM/DD 繳費截止`, `明天繳費截止`, `今天繳費截止`, `已逾期`. */
export function duePillText(due: string, today: string): string {
  const days = daysBetween(today, due);
  if (days < 0) {
    return '已逾期';
  }
  return days === 0 ? '今天繳費截止' : days === 1 ? '明天繳費截止' : `${shortDate(due)} 繳費截止`;
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
