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
