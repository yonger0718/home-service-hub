import { describe, expect, it } from 'vitest';

import { makeAccount } from '../testing/fixtures';
import {
  REMINDER_HORIZON_DAYS,
  billBalance,
  billingEvents,
  currentDues,
  reminderDues,
  dueCountdown,
  paymentWindowEnd,
  duePillText,
  upcomingDues,
} from './billing-math';

const card = (overrides: Parameters<typeof makeAccount>[0]) =>
  makeAccount({ is_credit: true, due_rule: null, due_value: null, ...overrides });

describe('billingEvents', () => {
  it('puts the 15th closing and the due day 20 days later in their months', () => {
    const uni = card({ id: 1, name: '玉山 UNI', closing_day: 15, due_rule: 'days_after_closing', due_value: 20 });
    expect(billingEvents([uni], '2026-10')).toEqual([
      { accountId: 1, name: '玉山 UNI', kind: 'due', date: '2026-10-05', period: { start: '2026-08-16', end: '2026-09-15' }, nextClosing: '2026-10-15' },
      { accountId: 1, name: '玉山 UNI', kind: 'closing', date: '2026-10-15', period: { start: '2026-09-16', end: '2026-10-15' }, nextClosing: '2026-11-15' },
    ]);
    // The October statement falls due in November.
    expect(billingEvents([uni], '2026-11')[0]).toEqual({
      accountId: 1,
      name: '玉山 UNI',
      kind: 'due',
      date: '2026-11-04',
      period: { start: '2026-09-16', end: '2026-10-15' },
      nextClosing: '2026-11-15',
    });
  });

  it('puts a fixed day 5 after a 25th closing in the next month', () => {
    const fubon = card({ id: 7, name: '富邦 J卡', closing_day: 25, due_rule: 'fixed_day', due_value: 5 });
    expect(billingEvents([fubon], '2026-10').map(event => [event.kind, event.date, event.period.end])).toEqual([
      ['due', '2026-10-05', '2026-09-25'],
      ['closing', '2026-10-25', '2026-10-25'],
    ]);
  });

  it('carries a days_after_closing due date from December into January', () => {
    const dec = card({ id: 4, closing_day: 20, due_rule: 'days_after_closing', due_value: 15 });
    expect(billingEvents([dec], '2027-01')).toEqual([
      { accountId: 4, name: '錢包', kind: 'due', date: '2027-01-04', period: { start: '2026-11-21', end: '2026-12-20' }, nextClosing: '2027-01-20' },
      { accountId: 4, name: '錢包', kind: 'closing', date: '2027-01-20', period: { start: '2026-12-21', end: '2027-01-20' }, nextClosing: '2027-02-20' },
    ]);
  });

  it('puts a fixed due day equal to the closing day in the next month', () => {
    const same = card({ id: 5, closing_day: 10, due_rule: 'fixed_day', due_value: 10 });
    expect(billingEvents([same], '2026-10').map(event => [event.kind, event.date, event.period.end])).toEqual([
      ['due', '2026-10-10', '2026-09-10'],
      ['closing', '2026-10-10', '2026-10-10'],
    ]);
  });

  it('clamps a 31st closing day to the end of February', () => {
    const late = card({ id: 2, closing_day: 31 });
    expect(billingEvents([late], '2026-02')).toEqual([
      { accountId: 2, name: '錢包', kind: 'closing', date: '2026-02-28', period: { start: '2026-02-01', end: '2026-02-28' }, nextClosing: '2026-03-31' },
    ]);
  });

  it('gives a card without a due rule closing events only', () => {
    const plain = card({ id: 3, closing_day: 10 });
    expect(billingEvents([plain], '2026-10').map(event => event.kind)).toEqual(['closing']);
  });

  it('ignores archived cards, non-credit accounts and cards without a closing day', () => {
    const rule = { closing_day: 15, due_rule: 'days_after_closing' as const, due_value: 20 };
    expect(
      billingEvents(
        [
          card({ id: 1, ...rule, is_archived: true }),
          makeAccount({ id: 2, ...rule, is_credit: false }),
          card({ id: 3, closing_day: null, due_rule: 'fixed_day', due_value: 5 }),
        ],
        '2026-10',
      ),
    ).toEqual([]);
  });

  it('lists two cards due on the same day as two events', () => {
    const a = card({ id: 1, name: 'A 卡', closing_day: 15, due_rule: 'days_after_closing', due_value: 20 });
    const b = card({ id: 2, name: 'B 卡', closing_day: 20, due_rule: 'fixed_day', due_value: 5 });
    const due = billingEvents([a, b], '2026-10').filter(event => event.kind === 'due');
    expect(due.map(event => [event.accountId, event.date])).toEqual([
      [1, '2026-10-05'],
      [2, '2026-10-05'],
    ]);
  });
});

describe('upcomingDues', () => {
  const uni = card({ id: 1, name: '玉山 UNI', closing_day: 15, due_rule: 'fixed_day', due_value: 3 });

  it('covers today through six days ahead, crossing into next month', () => {
    expect(upcomingDues([uni], '2026-10-28').map(event => event.date)).toEqual(['2026-11-03']);
    expect(upcomingDues([uni], '2026-11-03').map(event => event.date)).toEqual(['2026-11-03']);
    expect(upcomingDues([uni], '2026-10-27')).toEqual([]);
    expect(upcomingDues([uni], '2026-11-04')).toEqual([]);
  });

  it('never includes closing days', () => {
    expect(upcomingDues([uni], '2026-10-12')).toEqual([]);
  });
});

describe('billBalance', () => {
  it('turns the negative spend into a statement and subtracts the payments', () => {
    expect(billBalance('-12345.0000', ['5000.0000'])).toEqual({ statement: 12345, paid: 5000, remaining: 7345 });
  });

  it('leaves nothing remaining for a zero, refund-heavy or fully paid statement', () => {
    expect(billBalance('0.0000', []).remaining).toBe(0);
    expect(billBalance('300.0000', []).remaining).toBe(-300);
    expect(billBalance('-800.1000', ['500.0500', '300.0500']).remaining).toBe(0);
  });
});

describe('currentDues', () => {
  const uni = card({ id: 1, name: '玉山 UNI', closing_day: 15, due_rule: 'days_after_closing', due_value: 20 });

  it("is the last closed statement's due event, overdue or not", () => {
    expect(currentDues([uni], '2026-10-03')).toEqual([
      { accountId: 1, name: '玉山 UNI', kind: 'due', date: '2026-10-05', period: { start: '2026-08-16', end: '2026-09-15' }, nextClosing: '2026-10-15' },
    ]);
    expect(currentDues([uni], '2026-10-09')[0].date).toBe('2026-10-05');
    // On the closing day the cycle is still open; the day after, the new statement is the current one.
    expect(currentDues([uni], '2026-10-15')[0].date).toBe('2026-10-05');
    expect(currentDues([uni], '2026-10-16')[0].date).toBe('2026-11-04');
  });

  it('skips cards without a due rule, archived cards and non-credit accounts', () => {
    const noRule = card({ id: 2, closing_day: 10 });
    const archived = card({ id: 3, closing_day: 10, due_rule: 'fixed_day', due_value: 1, is_archived: true });
    const wallet = makeAccount({ id: 4, closing_day: 10, due_rule: 'fixed_day', due_value: 1 });
    expect(currentDues([noRule, archived, wallet, uni], '2026-10-03').map(event => event.accountId)).toEqual([1]);
  });

  it('keeps the reminder centre to statements due within 45 days (overdue ones included)', () => {
    expect(REMINDER_HORIZON_DAYS).toBe(45);
    const slow = card({ id: 5, closing_day: 30, due_rule: 'days_after_closing', due_value: 60 });
    const edge = card({ id: 6, closing_day: 30, due_rule: 'days_after_closing', due_value: 48 });
    const overdue = card({ id: 8, closing_day: 28, due_rule: 'days_after_closing', due_value: 3 });
    // 09/30 + 60 = 11/29 (57 days away) is left out; 09/30 + 48 = 11/17 is exactly 45 days away.
    expect(reminderDues([slow, edge, overdue, uni], '2026-10-03').map(event => [event.accountId, event.date])).toEqual([
      [6, '2026-11-17'],
      [8, '2026-10-01'],
      [1, '2026-10-05'],
    ]);
  });
});

describe('due wording', () => {
  it('counts down to the due day and past it', () => {
    expect(dueCountdown('2026-10-08', '2026-10-03')).toBe('還有 5 天');
    expect(dueCountdown('2026-10-05', '2026-10-03')).toBe('還有 2 天');
    expect(dueCountdown('2026-10-04', '2026-10-03')).toBe('明天');
    expect(dueCountdown('2026-10-03', '2026-10-03')).toBe('今天');
    expect(dueCountdown('2026-10-01', '2026-10-03')).toBe('已逾期 2 天');
  });

  it('labels the accounts-list pill', () => {
    expect(duePillText('2026-11-04', '2026-10-03')).toBe('11/04 繳費截止');
    expect(duePillText('2026-10-05', '2026-10-03')).toBe('10/05 繳費截止');
    expect(duePillText('2026-10-04', '2026-10-03')).toBe('明天繳費截止');
    expect(duePillText('2026-10-03', '2026-10-03')).toBe('今天繳費截止');
    expect(duePillText('2026-10-02', '2026-10-03')).toBe('已逾期');
  });
});

describe('paymentWindowEnd', () => {
  const event = {
    accountId: 1, name: '玉山 UNI', kind: 'due' as const, date: '2026-10-05',
    period: { start: '2026-08-16', end: '2026-09-15' }, nextClosing: '2026-10-15',
  };

  it('runs to the day before the next closing, capped at today', () => {
    expect(paymentWindowEnd(event, '2026-10-03')).toBe('2026-10-03');
    // Past the due date a late payment still counts, until the next statement closes.
    expect(paymentWindowEnd(event, '2026-10-09')).toBe('2026-10-09');
    expect(paymentWindowEnd(event, '2026-10-20')).toBe('2026-10-14');
  });

  it('reaches the due date when it falls after the next closing', () => {
    const slow = { ...event, date: '2026-11-10' };
    expect(paymentWindowEnd(slow, '2026-12-01')).toBe('2026-11-10');
    expect(paymentWindowEnd(slow, '2026-10-20')).toBe('2026-10-20');
  });

  it('never ends before the closing date (a statement not yet closed)', () => {
    expect(paymentWindowEnd(event, '2026-09-01')).toBe('2026-09-15');
  });
});
