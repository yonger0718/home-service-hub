function pad(value: number): string {
  return String(value).padStart(2, '0');
}

/** The local calendar date as `YYYY-MM-DD`. */
export function todayIso(now: Date = new Date()): string {
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
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
