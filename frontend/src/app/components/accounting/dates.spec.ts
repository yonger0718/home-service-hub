import { describe, expect, it } from 'vitest';

import { addDays, shortDate, slashDate, todayIso } from './dates';

describe('date helpers', () => {
  it('formats the local date as ISO', () => {
    expect(todayIso(new Date(2026, 9, 2, 23, 59))).toBe('2026-10-02');
  });

  it('formats short and slash dates', () => {
    expect(shortDate('2026-11-04')).toBe('11/04');
    expect(slashDate('2026-10-02')).toBe('2026/10/02');
  });

  it('adds days across month and year ends', () => {
    expect(addDays('2026-10-15', 20)).toBe('2026-11-04');
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01');
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28');
  });
});
