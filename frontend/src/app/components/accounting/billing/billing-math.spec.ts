import { describe, expect, it } from 'vitest';

import { makeAccount } from '../testing/fixtures';
import { billingEvents, upcomingDues } from './billing-math';

const card = (overrides: Parameters<typeof makeAccount>[0]) =>
  makeAccount({ is_credit: true, due_rule: null, due_value: null, ...overrides });

describe('billingEvents', () => {
  it('puts the 15th closing and the due day 20 days later in their months', () => {
    const uni = card({ id: 1, name: '玉山 UNI', closing_day: 15, due_rule: 'days_after_closing', due_value: 20 });
    expect(billingEvents([uni], '2026-10')).toEqual([
      { accountId: 1, name: '玉山 UNI', kind: 'due', date: '2026-10-05', period: { start: '2026-08-16', end: '2026-09-15' } },
      { accountId: 1, name: '玉山 UNI', kind: 'closing', date: '2026-10-15', period: { start: '2026-09-16', end: '2026-10-15' } },
    ]);
    // The October statement falls due in November.
    expect(billingEvents([uni], '2026-11')[0]).toEqual({
      accountId: 1,
      name: '玉山 UNI',
      kind: 'due',
      date: '2026-11-04',
      period: { start: '2026-09-16', end: '2026-10-15' },
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
      { accountId: 4, name: '錢包', kind: 'due', date: '2027-01-04', period: { start: '2026-11-21', end: '2026-12-20' } },
      { accountId: 4, name: '錢包', kind: 'closing', date: '2027-01-20', period: { start: '2026-12-21', end: '2027-01-20' } },
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
      { accountId: 2, name: '錢包', kind: 'closing', date: '2026-02-28', period: { start: '2026-02-01', end: '2026-02-28' } },
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
