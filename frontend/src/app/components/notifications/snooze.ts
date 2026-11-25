import { NotifSourceKey, NotifTask, NotifTier, NotifWake, SOURCE_NAMES, TIER_LABELS } from './notif-task';

/** 稍後提醒's choices (spec "Snooze on this device"). */
export type SnoozeOption = '3h' | 'tomorrow' | 'monday';

export const SNOOZE_OPTIONS: readonly { readonly value: SnoozeOption; readonly label: string }[] = [
  { value: '3h', label: '3 小時後' },
  { value: 'tomorrow', label: '明天 09:00' },
  { value: 'monday', label: '下週一 09:00' },
];

/** The `localStorage` key (device-local; never sent to the server). */
export const SNOOZE_KEY = 'hh-notif-snooze';

/** One snoozed task: until when, and what it looked like when snoozed (the wake rule compares against it). */
export interface SnoozeEntry {
  readonly until: string;
  readonly tier: NotifTier;
  readonly from: NotifSourceKey;
  readonly wake: NotifWake;
}

export type SnoozeMap = Readonly<Record<string, SnoozeEntry>>;

const HOUR = 3_600_000;

/**
 * The option's end in the device's local time: 3 小時後 = now + 3 h; 明天 09:00 = the next calendar day at 09:00;
 * 下週一 09:00 = the first Monday strictly after today, 09:00 (from a Monday: the next week's).
 */
export function snoozeUntil(option: SnoozeOption, now: Date): Date {
  if (option === '3h') {
    return new Date(now.getTime() + 3 * HOUR);
  }
  const days = option === 'tomorrow' ? 1 : (8 - now.getDay()) % 7 || 7;
  return new Date(now.getFullYear(), now.getMonth(), now.getDate() + days, 9, 0);
}

const pad = (n: number) => String(n).padStart(2, '0');

/** `10/24 13:00` (local time). */
export function untilText(until: Date): string {
  return `${pad(until.getMonth() + 1)}/${pad(until.getDate())} ${pad(until.getHours())}:${pad(until.getMinutes())}`;
}

export function snoozeEntry(task: NotifTask, until: Date): SnoozeEntry {
  return { until: until.toISOString(), tier: task.tier, from: task.from, wake: task.wake };
}

/**
 * Whether a snoozed task comes back now: its time has passed; it became 已逾期 (owner ruling 2026-11-25); its count
 * grew (a lower count does not wake); its amount or due date changed; one of its members is new; or its key changed.
 * A value absent on either side never wakes.
 */
export function wakes(entry: SnoozeEntry, task: NotifTask, now: Date): boolean {
  if (now.getTime() >= Date.parse(entry.until)) {
    return true;
  }
  if (task.tier === 'overdue' && entry.tier !== 'overdue') {
    return true;
  }
  const was = entry.wake;
  const is = task.wake;
  const both = <K extends keyof NotifWake>(key: K) => was[key] !== undefined && is[key] !== undefined;
  return (
    (both('count') && is.count! > was.count!) ||
    (both('amount') && is.amount !== was.amount) ||
    (both('due') && is.due !== was.due) ||
    (both('members') && is.members!.some((id) => !was.members!.includes(id))) ||
    (both('key') && is.key !== was.key)
  );
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

function isWake(v: unknown): v is NotifWake {
  if (!isRecord(v)) return false;
  const { count, amount, due, members, key } = v;
  return (
    (count === undefined || (typeof count === 'number' && Number.isFinite(count))) &&
    (amount === undefined || typeof amount === 'string') &&
    (due === undefined || typeof due === 'string') &&
    (key === undefined || typeof key === 'string') &&
    (members === undefined || (Array.isArray(members) && members.every((m) => typeof m === 'string')))
  );
}

function isEntry(v: unknown): v is SnoozeEntry {
  return (
    isRecord(v) &&
    typeof v['until'] === 'string' &&
    Number.isFinite(Date.parse(v['until'])) &&
    typeof v['tier'] === 'string' &&
    Object.hasOwn(TIER_LABELS, v['tier']) &&
    typeof v['from'] === 'string' &&
    Object.hasOwn(SOURCE_NAMES, v['from']) &&
    isWake(v['wake'])
  );
}

/** The stored map: a corrupt value reads as `{}`; malformed and expired entries are dropped. */
export function parseSnoozes(raw: string | null, now: Date): SnoozeMap {
  if (!raw) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return {};
  }
  if (!isRecord(parsed)) return {};
  const out: Record<string, SnoozeEntry> = {};
  for (const [id, value] of Object.entries(parsed)) {
    if (isEntry(value) && Date.parse(value.until) > now.getTime()) {
      out[id] = value;
    }
  }
  return out;
}
