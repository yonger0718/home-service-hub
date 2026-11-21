import { DefaultUrlSerializer, Router, convertToParamMap } from '@angular/router';
import { describe, expect, it, vi } from 'vitest';
import {
  calendarParams,
  calendarWrite,
  isTimelineUrl,
  readCalendarQuery,
  timelineCalendarParams,
  writeCalendarQuery,
} from './calendar-url';

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

describe('isTimelineUrl / timelineCalendarParams', () => {
  const serializer = new DefaultUrlSerializer();
  const router = { parseUrl: (url: string) => serializer.parse(url) } as unknown as Router;

  it('is the timeline and the pages it opens beside it, whatever the query', () => {
    for (const url of [
      '/accounting',
      '/accounting?month=2026-10&day=2026-10-21',
      '/accounting/entry?month=2026-10',
      '/accounting/reminders',
      '/accounting/entries/7?day=2026-10-21',
      '/accounting/entries/7/group',
      '/accounting/entries/7/edit#top',
    ]) {
      expect(isTimelineUrl(url)).toBe(true);
    }
    for (const url of ['/accounting/reports?month=2026-08', '/accounting/accounts/3', '/accounting/accounts/3/entries/7', '/portfolio', '/']) {
      expect(isTimelineUrl(url)).toBe(false);
    }
  });

  it("reads the calendar month / day only from a timeline URL (a 報表 ?month is not the calendar's)", () => {
    expect(timelineCalendarParams(router, '/accounting/entries/7?month=2026-10&day=2026-10-21&select=parent')).toEqual({
      month: '2026-10',
      day: '2026-10-21',
    });
    expect(timelineCalendarParams(router, '/accounting?month=2026-11')).toEqual({ month: '2026-11' });
    expect(timelineCalendarParams(router, '/accounting/reports?month=2026-08')).toEqual({});
    expect(timelineCalendarParams(router, '/portfolio?tab=dividends')).toEqual({});
  });
});

describe('writeCalendarQuery', () => {
  it('merges month and day into the URL and replaces history', async () => {
    const navigate = vi.fn().mockResolvedValue(true);
    await writeCalendarQuery({ navigate } as unknown as Router, '2026-10', '2026-10-21');
    expect(navigate).toHaveBeenCalledWith([], {
      queryParams: { month: '2026-10', day: '2026-10-21' },
      queryParamsHandling: 'merge',
      replaceUrl: true,
    });
  });
  it('writes day: null to drop the day', async () => {
    const navigate = vi.fn().mockResolvedValue(true);
    await writeCalendarQuery({ navigate } as unknown as Router, '2026-10', null);
    expect(navigate).toHaveBeenCalledWith([], {
      queryParams: { month: '2026-10', day: null },
      queryParamsHandling: 'merge',
      replaceUrl: true,
    });
  });
});
