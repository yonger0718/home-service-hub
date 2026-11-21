import { pad } from './accounting-ui';

/** The local calendar date as `YYYY-MM-DD`. */
export function todayIso(now: Date = new Date()): string {
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

const WEEKDAYS = ['週日', '週一', '週二', '週三', '週四', '週五', '週六'];

/** `2026-10-21` → `週三` (local calendar day). */
export function weekdayLabel(iso: string): string {
  const [year, month, day] = iso.split('-').map(Number);
  return WEEKDAYS[new Date(year, month - 1, day).getDay()];
}

/** `2026-10-21` → `10/21 週三` (the timeline's day header). */
export function dayLabel(date: string): string {
  const [, month, day] = date.split('-').map(Number);
  return `${pad(month)}/${pad(day)} ${weekdayLabel(date)}`;
}

/** `2026-11-04` → `11/04`. */
export function shortDate(iso: string): string {
  return `${iso.slice(5, 7)}/${iso.slice(8, 10)}`;
}

/** `2026-10-02` → `2026/10/02`. */
export function slashDate(iso: string): string {
  return iso.slice(0, 10).replaceAll('-', '/');
}

/** Calendar arithmetic on ISO dates (UTC, so no DST effects). */
export function addDays(iso: string, days: number): string {
  const [year, month, day] = iso.split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day + days));
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
}

/** Calendar days from `from` to `to` (negative when `to` is earlier); UTC, so no DST effects. */
export function daysBetween(from: string, to: string): number {
  const utc = (iso: string) => {
    const [year, month, day] = iso.split('-').map(Number);
    return Date.UTC(year, month - 1, day);
  };
  return Math.round((utc(to) - utc(from)) / 86_400_000);
}

/** The local calendar month as `YYYY-MM`. */
export function currentMonth(today = new Date()): string {
  return `${today.getFullYear()}-${pad(today.getMonth() + 1)}`;
}

/** `2026-10` → `2026 年 10 月`. */
export function monthLabel(month: string): string {
  const [year, monthNumber] = month.split('-').map(Number);
  return `${year} 年 ${monthNumber} 月`;
}

/** `2026-10` → `{ from: '2026-10-01', to: '2026-10-31' }`. */
export function monthRange(month: string): { from: string; to: string } {
  const [year, monthNumber] = month.split('-').map(Number);
  const lastDay = new Date(year, monthNumber, 0).getDate();
  return { from: `${month}-01`, to: `${month}-${pad(lastDay)}` };
}
