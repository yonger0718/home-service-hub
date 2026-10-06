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

// ---- header text contrast -------------------------------------------------------------------------------------

const DARK_TEXT = '#1d1c1a';
const LIGHT_TEXT = '#ffffff';

function channel(value: number): number {
  const s = value / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

/** WCAG relative luminance of `#rgb` / `#rrggbb`; null for anything else (CSS variables, names). */
function luminance(color: string): number | null {
  const match = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color.trim());
  if (!match) {
    return null;
  }
  const hex = match[1].length === 3 ? [...match[1]].map(digit => digit + digit).join('') : match[1];
  const [r, g, b] = [0, 2, 4].map(index => parseInt(hex.slice(index, index + 2), 16));
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** WCAG contrast ratio of two hex colours; null when either is not a hex colour. */
export function contrastRatio(a: string, b: string): number | null {
  const la = luminance(a);
  const lb = luminance(b);
  if (la === null || lb === null) {
    return null;
  }
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/** Text colour for a category-coloured header: the higher-contrast of dark / white (ties → dark); null → theme. */
export function textOn(bg: string | null): string | null {
  if (!bg) {
    return null;
  }
  const dark = contrastRatio(bg, DARK_TEXT);
  const light = contrastRatio(bg, LIGHT_TEXT);
  if (dark === null || light === null) {
    return null;
  }
  return dark >= light ? DARK_TEXT : LIGHT_TEXT;
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

// ---- focus containment ----------------------------------------------------------------------------------------

/** Root attribute of an overlay (picker, fee / fx sheet, 新增拆帳行): it traps its own Tab; outer traps skip it. */
export const OVERLAY_ATTR = 'data-overlay';

export const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]',
].join(',');

/** CSS visibility is inherited but can be overridden; display:none hides every descendant. */
function renderedForFocus(element: HTMLElement): boolean {
  const view = element.ownerDocument.defaultView;
  const visibility = view?.getComputedStyle(element).visibility;
  if (visibility === 'hidden' || visibility === 'collapse') return false;
  for (let ancestor: Element | null = element; ancestor; ancestor = ancestor.parentElement) {
    if (ancestor.hasAttribute('hidden') || view?.getComputedStyle(ancestor).display === 'none') return false;
  }
  return true;
}

/** Elements Tab can reach inside `container`, in DOM order (no negative tabindex, nothing hidden, inert or disabled). */
export function focusables(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    element => element.tabIndex >= 0 && !element.hidden && !element.closest('[inert]') &&
      !element.matches(':disabled') && renderedForFocus(element),
  );
}

/**
 * Tab / Shift+Tab wraps between the first and last focusable element of `container` (focus outside it is pulled in;
 * an empty container keeps focus on itself). Returns true, with the event default-prevented, when it moved focus;
 * a Tab between two inner elements is left to the browser. Handled keys and IME commits are ignored.
 */
export function trapFocus(container: HTMLElement, event: KeyboardEvent): boolean {
  if (event.key !== 'Tab' || isHandledKey(event)) {
    return false;
  }
  const items = focusables(container);
  if (items.length === 0) {
    event.preventDefault();
    container.focus();
    return true;
  }
  const first = items[0];
  const last = items[items.length - 1];
  const active = container.ownerDocument.activeElement as HTMLElement | null;
  const inside = !!active && container.contains(active);
  const atEdge = event.shiftKey ? active === first || active === container : active === last;
  if (!inside || atEdge) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
    return true;
  }
  return false;
}

/** Restore an overlay's opener, or the first form control when route/render changes removed it. */
export function restoreOverlayFocus(opener: HTMLElement | null, host: HTMLElement): void {
  if (opener?.isConnected && opener !== host.ownerDocument.body) {
    opener.focus();
    return;
  }
  const form = host.closest<HTMLElement>('.entry-form') ?? host;
  focusables(form)[0]?.focus();
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
