import { describe, expect, it } from 'vitest';

import { dueDate, periodLabel, shiftPeriod, statementPeriod } from './cycle';

describe('statement cycle', () => {
  it('computes 09/16 – 10/15 for closing day 15 on 2026-10-02', () => {
    const period = statementPeriod(15, '2026-10-02');
    expect(period).toEqual({ start: '2026-09-16', end: '2026-10-15' });
    expect(periodLabel(period)).toBe('09/16 – 10/15');
  });

  it('moves to the next cycle after the closing day', () => {
    expect(statementPeriod(15, '2026-10-16')).toEqual({ start: '2026-10-16', end: '2026-11-15' });
    expect(statementPeriod(15, '2026-10-15')).toEqual({ start: '2026-09-16', end: '2026-10-15' });
  });

  it('clamps a closing day past the month end', () => {
    expect(statementPeriod(31, '2026-02-10')).toEqual({ start: '2026-02-01', end: '2026-02-28' });
    expect(statementPeriod(30, '2026-03-05')).toEqual({ start: '2026-03-01', end: '2026-03-30' });
  });

  it('uses the calendar month without a closing day', () => {
    expect(statementPeriod(null, '2026-10-02')).toEqual({ start: '2026-10-01', end: '2026-10-31' });
  });

  it('steps backwards and forwards', () => {
    const current = statementPeriod(15, '2026-10-02');
    expect(shiftPeriod(15, current, -1)).toEqual({ start: '2026-08-16', end: '2026-09-15' });
    expect(shiftPeriod(15, current, 3)).toEqual({ start: '2026-12-16', end: '2027-01-15' });
    expect(shiftPeriod(null, { start: '2026-12-01', end: '2026-12-31' }, 1)).toEqual({ start: '2027-01-01', end: '2027-01-31' });
  });

  it('computes the due date for both rules', () => {
    expect(dueDate('2026-10-15', 'days_after_closing', 20)).toBe('2026-11-04');
    expect(dueDate('2026-10-15', 'fixed_day', 5)).toBe('2026-11-05');
    expect(dueDate('2026-10-15', 'fixed_day', 25)).toBe('2026-10-25');
    expect(dueDate('2026-01-31', 'fixed_day', 31)).toBe('2026-02-28');
    expect(dueDate('2026-10-15', null, null)).toBeNull();
  });
});
