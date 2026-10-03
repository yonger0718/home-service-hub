import type { Location } from '@angular/common';
import { Injector, afterNextRender } from '@angular/core';
import type { Router } from '@angular/router';

import { EntryKind, LedgerAccount, LedgerEntry, defaultCategoryIcon } from '../../models/accounting.model';
import { formatMoney } from './format';

/**
 * Shared accounting UI rules (final review M1–M3): one copy of each helper the pages display or enforce alike —
 * category icons and colours, FX lines, kind pills, date padding, the handled-key guard, archived-account labels,
 * the nested-sheet key handling and the back rule.
 */

// ---- category icons and colours -------------------------------------------------------------------------------

/** A list row or detail (the entry's category columns). */
type EntryIconSource = Pick<LedgerEntry, 'category_icon' | 'category_color' | 'category' | 'kind'>;
/** A category option: its own (or inherited) icon and colour, the main category's name and the kind of its tree. */
export interface CategoryIconSource {
  icon: string | null;
  color: string | null;
  mainName: string | null;
  kind: string;
}

function isEntry(source: EntryIconSource | CategoryIconSource): source is EntryIconSource {
  return 'category_icon' in source;
}

/** The category's icon, else the default icon table (by main-category name, then by kind). */
export function iconOf(source: EntryIconSource | CategoryIconSource): string {
  if (isEntry(source)) {
    return source.category_icon ?? defaultCategoryIcon(source.category?.split('/')[0], source.kind);
  }
  return source.icon ?? defaultCategoryIcon(source.mainName, source.kind);
}

/** The category's colour, else the neutral surface tone. */
export function colorOf(source: EntryIconSource | CategoryIconSource): string {
  return (isEntry(source) ? source.category_color : source.color) ?? 'var(--app-surface-soft)';
}

// ---- foreign-currency lines -----------------------------------------------------------------------------------

type FxSource = Pick<LedgerEntry, 'amount' | 'currency' | 'original_amount' | 'original_currency' | 'fx_rate'>;

function foreign(entry: FxSource): boolean {
  return !!entry.original_currency && entry.original_amount !== null && entry.original_currency !== entry.currency;
}

/** Row line `¥5,390 @ 0.2163`; null for an entry in its account currency; the rate is left out when unknown. */
export function fxLine(entry: FxSource): string | null {
  if (!foreign(entry)) {
    return null;
  }
  const original = formatMoney(Math.abs(Number(entry.original_amount)), entry.original_currency!);
  return entry.fx_rate === null ? original : `${original} @ ${Number(entry.fx_rate)}`;
}

/** Detail line `¥5,390 × 0.2163 = $1,166`; same rules as `fxLine` (`¥5,390 = $1,166` without a rate). */
export function fxConversionLine(entry: FxSource): string | null {
  if (!foreign(entry)) {
    return null;
  }
  const original = formatMoney(Math.abs(Number(entry.original_amount)), entry.original_currency!);
  const converted = formatMoney(Math.abs(Number(entry.amount)), entry.currency);
  return entry.fx_rate === null ? `${original} = ${converted}` : `${original} × ${Number(entry.fx_rate)} = ${converted}`;
}

// ---- kind pills -----------------------------------------------------------------------------------------------

export interface KindPill {
  label: string;
  tone: '' | 'rw' | 'rv';
}

/**
 * Kind pills of list rows. The 應收 / 應付 pill follows `kind` only, so settlement rows (`is_settlement`) get it too.
 * 退款 is neutral everywhere; only 回饋 uses the reward tone.
 */
export const KIND_PILLS: Partial<Record<EntryKind, KindPill>> = {
  reward: { label: '回饋', tone: 'rw' },
  receivable: { label: '應收', tone: 'rv' },
  payable: { label: '應付', tone: 'rv' },
  transfer_out: { label: '轉帳', tone: '' },
  transfer_in: { label: '轉帳', tone: '' },
  refund: { label: '退款', tone: '' },
};

// ---- dates ----------------------------------------------------------------------------------------------------

/** Two-digit day / month / hour / minute. */
export function pad(value: number): string {
  return String(value).padStart(2, '0');
}

/** `[year, month]` (month 1–12) moved by `delta` months. */
export function shiftMonth(year: number, month: number, delta: number): [number, number] {
  const index = year * 12 + (month - 1) + delta;
  return [Math.floor(index / 12), (index % 12) + 1];
}

// ---- keys -----------------------------------------------------------------------------------------------------

/**
 * True when a key event is not ours to act on: another handler already handled it (`defaultPrevented`), or it
 * commits an IME candidate (Zhuyin, …: `isComposing`, or Safari's keyCode 229 after compositionend).
 */
export function isHandledKey(event: KeyboardEvent): boolean {
  return event.defaultPrevented || event.isComposing || event.keyCode === 229;
}

/** Focused elements on which ⏎ keeps its own meaning (choose, follow, press, new line, toggle), never "save". */
export const NO_ENTER_SAVE_TAGS: ReadonlySet<string> = new Set(['SELECT', 'A', 'BUTTON', 'TEXTAREA', 'SUMMARY']);

/**
 * ⏎ / Esc on the host of a component whose bottom sheet (`sheet`) is open. Handled at the host, so a key from anywhere
 * in the component (the trigger button included) never reaches the entry form, which would save or leave.
 * Esc → 'close'. ⏎ in a sheet field → 'confirm'. ⏎ on a sheet button is left to its native click. ⏎ outside the sheet
 * is only marked handled. IME commits and keys already handled are left alone.
 */
export function sheetKeyAction(event: KeyboardEvent, sheet: Element | null): 'close' | 'confirm' | null {
  if (!sheet || isHandledKey(event)) {
    return null;
  }
  if (event.key === 'Escape') {
    event.preventDefault();
    return 'close';
  }
  if (event.key !== 'Enter') {
    return null;
  }
  const target = event.target as HTMLElement | null;
  if (target && sheet.contains(target) && target.tagName === 'BUTTON') {
    return null;
  }
  event.preventDefault();
  return target && sheet.contains(target) ? 'confirm' : null;
}

/** Moves focus into a sheet once it has rendered (its first field), so keys start inside it. */
export function focusSheetField(host: HTMLElement, selector: string, injector: Injector): void {
  afterNextRender(() => host.querySelector<HTMLElement>(selector)?.focus(), { injector });
}

// ---- accounts and navigation ----------------------------------------------------------------------------------

/** Select label of an account; an archived one (kept selectable for the record being edited) says so. */
export function accountLabel(account: Pick<LedgerAccount, 'name' | 'is_archived'>): string {
  return account.is_archived ? `${account.name}（已封存）` : account.name;
}

/**
 * Back to where the owner came from: `location.back()` when the router has in-app history, else `fallback`
 * (the parent list of the page, e.g. the passbook or `/accounting`).
 */
export function leavePage(router: Router, location: Location, fallback: string): void {
  if (router.lastSuccessfulNavigation()?.previousNavigation) {
    location.back();
  } else {
    void router.navigateByUrl(fallback);
  }
}
