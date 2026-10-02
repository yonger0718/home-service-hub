import { Location } from '@angular/common';
import { Router } from '@angular/router';

/**
 * Shared accounting UI helpers (final review M1–M3): one copy of each rule the pages display or enforce alike.
 * Pure functions apart from the DOM / router helpers at the end; no component imports.
 */

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

/** Focused elements on which ⏎ keeps its own meaning (choose, follow, press, new line, toggle), never "save". */
export const NO_ENTER_SAVE_TAGS: ReadonlySet<string> = new Set(['SELECT', 'A', 'BUTTON', 'TEXTAREA', 'SUMMARY']);

/**
 * True when a key event is not ours to act on: another handler already handled it (`defaultPrevented`), or it
 * commits an IME candidate (Zhuyin, …: `isComposing`, or Safari's keyCode 229 after compositionend).
 */
export function isHandledKey(event: KeyboardEvent): boolean {
  return event.defaultPrevented || event.isComposing || event.keyCode === 229;
}
