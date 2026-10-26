import { describe, expect, it } from 'vitest';

import { BusinessCalendar, WEEKENDS_ONLY, calendarFrom, isBusinessDay, nextBusinessDay } from './business-days';

/** October 2026 as the server lists it: 國慶日 make-up (Fri 10-09) and 光復節 make-up (Mon 10-26). */
const OCT_2026: BusinessCalendar = calendarFrom({ holidays: ['2026-10-09', '2026-10-26'], workdays: [], last_known_day: '2027-12-31' });

describe('isBusinessDay', () => {
  it('closes weekends and listed holidays, and lets a listed workday win', () => {
    expect(isBusinessDay('2026-10-23', OCT_2026)).toBe(true); // Fri
    expect(isBusinessDay('2026-10-24', OCT_2026)).toBe(false); // Sat
    expect(isBusinessDay('2026-10-25', OCT_2026)).toBe(false); // Sun
    expect(isBusinessDay('2026-10-26', OCT_2026)).toBe(false); // Mon, 光復節補假
    expect(isBusinessDay('2026-10-26', WEEKENDS_ONLY)).toBe(true);
    const makeUp = calendarFrom({ holidays: ['2026-10-26'], workdays: ['2026-10-24'], last_known_day: '2027-12-31' });
    expect(isBusinessDay('2026-10-24', makeUp)).toBe(true); // Saturday make-up workday wins
  });
});

describe('nextBusinessDay', () => {
  it('keeps a business day and moves a weekend or holiday due date to the next open day', () => {
    expect(nextBusinessDay('2026-10-23', OCT_2026)).toBe('2026-10-23');
    expect(nextBusinessDay('2026-10-24', OCT_2026)).toBe('2026-10-27'); // Sat → Tue over 光復節補假
    expect(nextBusinessDay('2026-10-24', WEEKENDS_ONLY)).toBe('2026-10-26'); // without the table: Monday
    expect(nextBusinessDay('2026-10-09', OCT_2026)).toBe('2026-10-12'); // Fri holiday → Mon
  });
});
