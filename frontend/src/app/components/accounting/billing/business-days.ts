import { BusinessDays } from '../../../models/accounting.model';
import { addDays } from '../dates';

/** Closed days beyond weekends and make-up workdays (`YYYY-MM-DD`), as `GET /calendar/business-days` lists them. */
export interface BusinessCalendar {
  holidays: ReadonlySet<string>;
  workdays: ReadonlySet<string>;
}

/** The calendar before the read lands (or after it failed): only Saturdays and Sundays are closed. */
export const WEEKENDS_ONLY: BusinessCalendar = { holidays: new Set(), workdays: new Set() };

export function calendarFrom(days: BusinessDays): BusinessCalendar {
  return { holidays: new Set(days.holidays), workdays: new Set(days.workdays) };
}

/** True when banks are open on `iso`: a listed workday wins; otherwise a weekday that is not a listed holiday. */
export function isBusinessDay(iso: string, calendar: BusinessCalendar): boolean {
  if (calendar.workdays.has(iso)) {
    return true;
  }
  const [year, month, day] = iso.split('-').map(Number);
  const weekday = new Date(Date.UTC(year, month - 1, day)).getUTCDay();
  return weekday !== 0 && weekday !== 6 && !calendar.holidays.has(iso);
}

/** The first business day on or after `iso`: the day the bank debits a payment due on `iso`. */
export function nextBusinessDay(iso: string, calendar: BusinessCalendar): string {
  let day = iso;
  while (!isBusinessDay(day, calendar)) {
    day = addDays(day, 1);
  }
  return day;
}
