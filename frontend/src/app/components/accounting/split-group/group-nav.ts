import { Location } from '@angular/common';
import { NavigationCancel, NavigationEnd, NavigationError, NavigationSkipped, ParamMap, Router } from '@angular/router';
import { filter, take } from 'rxjs';

import { leavePage } from '../accounting-ui';
import { CalendarQuery, calendarParams, isTimelineUrl, readCalendarQuery, writeCalendarQuery } from '../timeline/calendar-url';

/**
 * History state carried between a list, the 多類別 group view and its child details (PR-9, no page stacking):
 * - `closeTo`: the entry-detail rule, ✕ goes to that fixed page (`'list'` after a split save, `'reminders'`).
 * - `groupDepth`: set when the group view was opened from a list row; how many history entries this page sits above
 *   that list (the group view 1, a child 2; a sibling or 「← 多類別」 replaces in place and keeps it). ✕ goes back
 *   exactly that far, so it lands on the list and never grows history.
 * - `viaGroup`: the page sits one entry above the group view the user opened: a child, a sibling that replaced it, or
 *   a group view that 「← 多類別」 put in a child's place. Such a group view opens its children in place too, so
 *   history stays `list, group view, page` however often the owner goes child ↔ group.
 */
export interface GroupNavState {
  closeTo?: 'list' | 'reminders';
  groupDepth?: number;
  viaGroup?: boolean;
}

/** The navigation's state, else the restored history entry's (reload, browser back / forward). */
export function readGroupNavState(router: Router, location: Location): GroupNavState {
  const state = router.currentNavigation()?.extras.state ?? (location.getState() as Record<string, unknown> | null);
  const closeTo = state?.['closeTo'];
  const depth = state?.['groupDepth'];
  return {
    closeTo: closeTo === 'list' || closeTo === 'reminders' ? closeTo : undefined,
    groupDepth: typeof depth === 'number' && Number.isInteger(depth) && depth > 0 ? depth : undefined,
    viaGroup: state?.['viaGroup'] === true,
  };
}

/** The state a list row opens a group view with: the list is the history entry right below it. */
export const FROM_LIST: GroupNavState = { groupDepth: 1 };

/** A group view's path, from the timeline or a passbook; group 1 is the member it is routed by. */
export const GROUP_PATH = /^\/accounting(?:\/accounts\/\d+)?\/entries\/(\d+)\/group$/;

/**
 * The state a list row opens a group view with, given the path on screen when it is clicked: `FROM_LIST` only when the
 * list itself is the current page. In the two-pane layout the list stays clickable while a detail, a child or another
 * group view is open on the right; the entry below the new group view is then that page, not the list, so no depth is
 * claimed and ✕ goes to the parent list instead of back.
 */
export function listOpenState(currentPath: string, listPath: string): GroupNavState | undefined {
  return currentPath.split(/[?#]/)[0] === listPath ? FROM_LIST : undefined;
}

/** The group view of the split `entryId` belongs to; under a passbook it keeps the passbook in its URL. */
export function groupCommands(entryId: number, passbookId: number | null): (string | number)[] {
  return passbookId === null ? ['/accounting/entries', entryId, 'group'] : ['/accounting/accounts', passbookId, 'entries', entryId, 'group'];
}

/** An entry's detail; under a passbook it keeps the passbook in its URL. */
export function entryCommands(entryId: number, passbookId: number | null): (string | number)[] {
  return passbookId === null ? ['/accounting/entries', entryId] : ['/accounting/accounts', passbookId, 'entries', entryId];
}

/**
 * The list a group view or child detail belongs to: the reminder centre, the passbook, else the timeline, which keeps
 * the calendar month / day of `query` (the page's own query).
 */
export function groupParentUrl(router: Router, state: GroupNavState, passbookId: number | null, query: ParamMap | null = null): string {
  if (state.closeTo === 'reminders') {
    return '/accounting/reminders';
  }
  if (passbookId !== null) {
    return `/accounting/accounts/${passbookId}`;
  }
  const queryParams = query ? calendarParams(query) : {};
  return router.serializeUrl(router.createUrlTree(['/accounting'], { queryParams }));
}

/** A navigation that replaces the current page (sibling, 「← 多類別」, a dissolved split): same depth, no `viaGroup`. */
export function carriedState(state: GroupNavState): GroupNavState {
  const carried: GroupNavState = {};
  if (state.closeTo) {
    carried.closeTo = state.closeTo;
  }
  if (state.groupDepth) {
    carried.groupDepth = state.groupDepth;
  }
  return carried;
}

/** A page replaced in place one entry above the group view (sibling, 「← 多類別」): same depth, still `viaGroup`. */
export function inPlaceState(state: GroupNavState): GroupNavState {
  return state.viaGroup ? { ...carriedState(state), viaGroup: true } : carriedState(state);
}

/** Group view → child: one history entry deeper (a push), or in place from a group view that replaced a child. */
export function childState(state: GroupNavState): GroupNavState {
  if (state.viaGroup) {
    return inPlaceState(state);
  }
  const next = carriedState(state);
  if (next.groupDepth) {
    next.groupDepth += 1;
  }
  return { ...next, viaGroup: true };
}

/**
 * The state a drawer step (↑ / ↓ on the calendar) opens a neighbouring row's page with, in place of the page on screen
 * (`replaceUrl`), given that page's state `current`: the new page closes where the replaced one would. `closeTo` and the
 * depth above the list are kept. A group view (`groupView`) without a depth sits one entry above the list, as a detail
 * opened from it does; a detail deeper than one entry goes back that far as a group view's child does (`viaGroup`).
 */
export function stepState(current: GroupNavState, groupView: boolean): GroupNavState {
  const next = carriedState(current);
  if (groupView) {
    next.groupDepth ??= 1;
  } else if ((next.groupDepth ?? 0) > 1) {
    next.viaGroup = true;
  } else {
    delete next.groupDepth;
  }
  return next;
}

/**
 * ✕ on a group view or a child opened from it: `closeTo` → that page; opened from a list → back exactly to that list
 * (history not grown, never through a child or a second group view); else (deep link) → the parent list.
 */
export function closeGroupPage(router: Router, location: Location, state: GroupNavState, parentUrl: string): void {
  if (!state.closeTo && state.groupDepth && router.lastSuccessfulNavigation()?.previousNavigation) {
    location.historyGo(-state.groupDepth);
    return;
  }
  void router.navigateByUrl(parentUrl);
}

/**
 * ✕ on an entry detail, a group view (`groupPage`) or a child of one, at every width. First the history move as before:
 * a group view or a child → `closeGroupPage`; `closeTo` → that page; else `leavePage` (back, else `parentUrl`). Then,
 * when the closing page's URL carries a calendar `day` (`target`, the day it shows now: a grid click may have moved it
 * while the page was open) and no `closeTo`, the timeline page it lands on gets that month / day written in place
 * (`writeCalendarQuery`, history replaced) unless it already shows them. History stays as it was before the page
 * opened, so the next Back leaves the calendar in one step. Without a `day` (清單, a `?month` from elsewhere) nothing is
 * written.
 */
export function closeKeepingDay(
  router: Router,
  location: Location,
  state: GroupNavState,
  parentUrl: string,
  target: CalendarQuery,
  groupPage: boolean,
): void {
  if (target.day !== null && !state.closeTo) {
    writeDayOnLanding(router, target.day);
  }
  if (groupPage || state.viaGroup) {
    closeGroupPage(router, location, state, parentUrl);
  } else if (state.closeTo) {
    void router.navigateByUrl(parentUrl);
  } else {
    leavePage(router, location, parentUrl);
  }
}

/**
 * Once the close navigation settles (popstate or `navigateByUrl`), writes `day` onto the timeline page it landed on.
 * Not tied to the closing page's lifetime: that page is destroyed by the very navigation this waits for.
 */
function writeDayOnLanding(router: Router, day: string): void {
  router.events
    .pipe(
      filter(
        event =>
          event instanceof NavigationEnd ||
          event instanceof NavigationCancel ||
          event instanceof NavigationError ||
          event instanceof NavigationSkipped,
      ),
      take(1),
    )
    .subscribe(event => {
      if (!(event instanceof NavigationEnd) || !isTimelineUrl(event.urlAfterRedirects)) {
        return;
      }
      const landed = readCalendarQuery(router.parseUrl(event.urlAfterRedirects).queryParamMap);
      if (landed.day !== day) {
        void writeCalendarQuery(router, day.slice(0, 7), day);
      }
    });
}
