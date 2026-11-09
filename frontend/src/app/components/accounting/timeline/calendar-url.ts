import { ParamMap, Params, Router } from '@angular/router';

import { addDays } from '../dates';

export const MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/;
export const DAY_RE = /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/;

/** A well-formed day that is also a real date (rejects 2026-02-31). */
export function isRealDay(day: string): boolean {
  return DAY_RE.test(day) && addDays(day, 0) === day;
}

export interface CalendarQuery {
  month: string | null;
  day: string | null;
}

export function readCalendarQuery(query: ParamMap): CalendarQuery {
  const day = query.get('day');
  if (day !== null && isRealDay(day)) {
    return { month: day.slice(0, 7), day };
  }
  const month = query.get('month');
  return { month: month !== null && MONTH_RE.test(month) ? month : null, day: null };
}

export function calendarParams(query: ParamMap): Params {
  const { month, day } = readCalendarQuery(query);
  const params: Params = {};
  if (month !== null) params['month'] = month;
  if (day !== null) params['day'] = day;
  return params;
}

/**
 * `/accounting` and the pages the timeline opens beside it (an entry, its group view or edit form, the new-entry form,
 * the reminder centre): the only URLs whose `month` / `day` are the calendar's (a 報表 `?month` is not).
 */
const TIMELINE_PATH = /^\/accounting(?:\/(?:entry|reminders|entries\/\d+(?:\/(?:group|edit))?))?$/;

/** `url` (path, query and fragment) is the timeline or a page it opens beside it. */
export function isTimelineUrl(url: string): boolean {
  return TIMELINE_PATH.test(url.split(/[?#]/)[0]);
}

/** The calendar month / day of `url` when it is a timeline page (`isTimelineUrl`), else `{}`. */
export function timelineCalendarParams(router: Router, url: string): Params {
  return isTimelineUrl(url) ? calendarParams(router.parseUrl(url).queryParamMap) : {};
}

export function calendarWrite(month: string, day: string | null): Params {
  return { month, day };
}

/** Writes month / day into the URL (merged with the other query keys, history replaced); the one writer for every calendar surface. */
export function writeCalendarQuery(router: Router, month: string, day: string | null): Promise<boolean> {
  return router.navigate([], { queryParams: calendarWrite(month, day), queryParamsHandling: 'merge', replaceUrl: true });
}
