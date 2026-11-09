import { convertToParamMap } from '@angular/router';
import { describe, expect, it } from 'vitest';
import { calendarParams, calendarWrite, readCalendarQuery } from './calendar-url';

const q = (params: Record<string, string>) => convertToParamMap(params);

describe('readCalendarQuery', () => {
  it('reads a valid month and day', () => {
    expect(readCalendarQuery(q({ month: '2026-10', day: '2026-10-21' }))).toEqual({ month: '2026-10', day: '2026-10-21' });
  });
  it('lets a day outside month win', () => {
    expect(readCalendarQuery(q({ month: '2026-09', day: '2026-10-21' }))).toEqual({ month: '2026-10', day: '2026-10-21' });
  });
  it('reads a month alone', () => {
    expect(readCalendarQuery(q({ month: '2026-10' }))).toEqual({ month: '2026-10', day: null });
  });
  it('rejects impossible dates and accepts a leap day', () => {
    expect(readCalendarQuery(q({ month: '2026-02', day: '2026-02-31' }))).toEqual({ month: '2026-02', day: null });
    expect(readCalendarQuery(q({ day: '2026-02-31' }))).toEqual({ month: null, day: null });
    expect(readCalendarQuery(q({ day: '2024-02-29' }))).toEqual({ month: '2024-02', day: '2024-02-29' });
  });
  it('rejects malformed values', () => {
    expect(readCalendarQuery(q({ month: '2026-13', day: '2026-13-99' }))).toEqual({ month: null, day: null });
    expect(readCalendarQuery(q({ month: 'oct', day: 'today' }))).toEqual({ month: null, day: null });
  });
});

describe('calendarParams / calendarWrite', () => {
  it('keeps only the valid selection params', () => {
    expect(calendarParams(q({ month: '2026-10', day: '2026-10-21', category_id: '12' }))).toEqual({ month: '2026-10', day: '2026-10-21' });
    expect(calendarParams(q({ month: '2026-10' }))).toEqual({ month: '2026-10' });
    expect(calendarParams(q({ day: 'nope' }))).toEqual({});
  });
  it('writes day: null to clear it', () => {
    expect(calendarWrite('2026-11', null)).toEqual({ month: '2026-11', day: null });
  });
});
