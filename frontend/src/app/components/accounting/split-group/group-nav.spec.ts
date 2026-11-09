import { Location } from '@angular/common';
import { SpyLocation, provideLocationMocks } from '@angular/common/testing';
import { TestBed } from '@angular/core/testing';
import { Router, convertToParamMap, provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { closeKeepingDay, groupParentUrl } from './group-nav';

describe('groupParentUrl', () => {
  let router: Router;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideRouter([])] });
    router = TestBed.inject(Router);
  });

  it('carries the calendar month and day from the query to the timeline parent', () => {
    const query = convertToParamMap({ month: '2026-10', day: '2026-10-21', select: 'parent' });
    expect(groupParentUrl(router, {}, null, query)).toBe('/accounting?month=2026-10&day=2026-10-21');
    expect(groupParentUrl(router, { closeTo: 'list' }, null, query)).toBe('/accounting?month=2026-10&day=2026-10-21');
  });

  it('is the bare timeline without a query or calendar keys', () => {
    expect(groupParentUrl(router, {}, null)).toBe('/accounting');
    expect(groupParentUrl(router, {}, null, convertToParamMap({ select: 'parent' }))).toBe('/accounting');
  });

  it('keeps the passbook and the reminder centre as they are', () => {
    const query = convertToParamMap({ month: '2026-10', day: '2026-10-21' });
    expect(groupParentUrl(router, {}, 3, query)).toBe('/accounting/accounts/3');
    expect(groupParentUrl(router, { closeTo: 'reminders' }, null, query)).toBe('/accounting/reminders');
  });
});

/** Real router over a SpyLocation driven by popstate, as the app's initial navigation sets it up. */
describe('closeKeepingDay', () => {
  let router: Router;
  let location: SpyLocation;

  beforeEach(async () => {
    TestBed.configureTestingModule({ providers: [provideLocationMocks(), provideRouter([{ path: '**', children: [] }])] });
    router = TestBed.inject(Router);
    location = TestBed.inject(Location) as SpyLocation;
    router.setUpLocationChangeListener();
    await router.navigateByUrl('/accounting/reports');
  });

  /** The browser history entries after SpyLocation's initial blank one, and the current index among them. */
  function history(): { paths: string[]; index: number } {
    const stack = location as unknown as { _history: { path: string }[]; _historyIndex: number };
    return { paths: stack._history.slice(1).map(state => state.path), index: stack._historyIndex - 1 };
  }

  const DAY_23 = { month: '2026-10', day: '2026-10-23' };
  const ENTRY_23 = '/accounting/entries/7?month=2026-10&day=2026-10-23';

  /** 報表 → the calendar on 10/21 → an entry opened from it (push) → the grid moves the day to 10/23 (replace). */
  async function openedThenMoved(): Promise<void> {
    await router.navigateByUrl('/accounting?month=2026-10&day=2026-10-21');
    await router.navigateByUrl('/accounting/entries/7?month=2026-10&day=2026-10-21');
    await router.navigateByUrl(ENTRY_23, { replaceUrl: true });
  }

  it('goes back as before, then writes the day shown now in place: Back then leaves the calendar in one step', async () => {
    await openedThenMoved();
    closeKeepingDay(router, location, {}, '/accounting?month=2026-10&day=2026-10-23', DAY_23, false);
    await vi.waitFor(() => expect(router.url).toBe('/accounting?month=2026-10&day=2026-10-23'));
    expect(history()).toEqual({
      paths: ['/accounting/reports', '/accounting?month=2026-10&day=2026-10-23', ENTRY_23],
      index: 1,
    });

    location.back();
    await vi.waitFor(() => expect(router.url).toBe('/accounting/reports'));
  });

  it('goes back the group depth for a group page, then writes the day shown now in place', async () => {
    await router.navigateByUrl('/accounting?month=2026-10&day=2026-10-21');
    await router.navigateByUrl('/accounting/entries/7/group?month=2026-10&day=2026-10-21');
    await router.navigateByUrl('/accounting/entries/8?month=2026-10&day=2026-10-21');
    await router.navigateByUrl('/accounting/entries/8?month=2026-10&day=2026-10-23', { replaceUrl: true });
    closeKeepingDay(router, location, { groupDepth: 2, viaGroup: true }, '/accounting?month=2026-10&day=2026-10-23', DAY_23, false);
    await vi.waitFor(() => expect(router.url).toBe('/accounting?month=2026-10&day=2026-10-23'));
    expect(history().index).toBe(1);
    expect(history().paths[1]).toBe('/accounting?month=2026-10&day=2026-10-23');
  });

  it('adds no write when the page it lands on already shows that day', async () => {
    await router.navigateByUrl('/accounting?month=2026-10&day=2026-10-23');
    await router.navigateByUrl(ENTRY_23);
    const navigate = vi.spyOn(router, 'navigate');
    closeKeepingDay(router, location, {}, '/accounting?month=2026-10&day=2026-10-23', DAY_23, false);
    await vi.waitFor(() => expect(router.url).toBe('/accounting?month=2026-10&day=2026-10-23'));
    await new Promise(resolve => setTimeout(resolve));
    expect(navigate).not.toHaveBeenCalled();
  });

  it('writes nothing without a day (清單 with ?month) or with closeTo, and never onto a page off the timeline', async () => {
    await openedThenMoved();
    closeKeepingDay(router, location, {}, '/accounting?month=2026-10', { month: '2026-10', day: null }, false);
    await vi.waitFor(() => expect(router.url).toBe('/accounting?month=2026-10&day=2026-10-21'));

    await router.navigateByUrl(ENTRY_23);
    closeKeepingDay(router, location, { closeTo: 'reminders' }, '/accounting/reminders', DAY_23, false);
    await vi.waitFor(() => expect(router.url).toBe('/accounting/reminders'));

    // The previous in-app page is not the timeline (a deep link into the detail after 報表): back lands there untouched.
    await router.navigateByUrl('/accounting/reports');
    await router.navigateByUrl(ENTRY_23);
    closeKeepingDay(router, location, {}, '/accounting?month=2026-10&day=2026-10-23', DAY_23, false);
    await vi.waitFor(() => expect(router.url).toBe('/accounting/reports'));
    await new Promise(resolve => setTimeout(resolve));
    expect(router.url).toBe('/accounting/reports');
  });
});
