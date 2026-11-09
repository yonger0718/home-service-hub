import { ParamMap, Params } from '@angular/router';

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

export function calendarWrite(month: string, day: string | null): Params {
  return { month, day };
}
