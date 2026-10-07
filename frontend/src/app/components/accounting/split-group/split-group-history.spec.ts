import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Location } from '@angular/common';
import { SpyLocation, provideLocationMocks } from '@angular/common/testing';
import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EntryDetail, EntryGroupSummary } from '../../../models/accounting.model';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { EntryDetailComponent } from '../entry-detail/entry-detail';
import { makeEntryDetail, makeGroupMember } from '../testing/fixtures';
import { FROM_LIST } from './group-nav';
import { SplitGroupComponent } from './split-group';

@Component({ selector: 'app-list-stub', template: '<p class="list">list</p>' })
class ListStub {}

const GROUP: EntryGroupSummary = { id: 4, kind: 'split', name: null, merchant: null, description: null, count: 3, total: '-600.0000', currency: 'TWD' };
const IDS = [42, 43, 44];
const MEMBERS = IDS.map(id => makeGroupMember({ id, amount: '-200.0000', group: GROUP }));

/** Member 43 settles member 44 of the same split: a sibling link in its related lines. */
function memberDetail(id: number): EntryDetail {
  return makeEntryDetail({
    id, amount: '-200.0000', group: GROUP, group_members: MEMBERS,
    settles: id === 43 ? makeGroupMember({ id: 44, amount: '-200.0000', group: GROUP }) : null,
  });
}

/**
 * PR-9 "no page stacking" end to end on the phone outlets: real router, real group view and entry detail, and the
 * router's SpyLocation as browser history (`_history` / `_historyIndex`).
 */
describe('多類別 group view history (no page stacking)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let router: Router;
  let location: SpyLocation;

  beforeEach(async () => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideLocationMocks(),
        provideRouter([
          { path: 'accounting', component: ListStub },
          { path: 'accounting/entries/:id/group', component: SplitGroupComponent },
          { path: 'accounting/entries/:id', component: EntryDetailComponent },
        ]),
      ],
    });
    TestBed.inject(LayoutModeService).set('phone');
    http = TestBed.inject(HttpTestingController);
    router = TestBed.inject(Router);
    location = TestBed.inject(Location) as SpyLocation;
    harness = await RouterTestingHarness.create();
    // As the app's initial navigation does: popstate (browser back / forward, historyGo) drives the router.
    router.setUpLocationChangeListener();
    await harness.navigateByUrl('/accounting');
  });

  afterEach(() => http.verify());

  /** Answers the entry reads (and the detail's account list) until the page has settled. */
  async function settle(): Promise<void> {
    for (let round = 0; round < 5; round++) {
      // The router handles a popstate on a later task.
      await new Promise(resolve => setTimeout(resolve));
      await harness.fixture.whenStable();
      harness.detectChanges();
      for (const req of http.match(() => true)) {
        const match = /\/entries\/(\d+)$/.exec(req.request.url);
        req.flush(match ? memberDetail(Number(match[1])) : []);
      }
    }
    harness.detectChanges();
  }

  function root(): HTMLElement {
    return harness.routeNativeElement as HTMLElement;
  }

  /** SpyLocation's history stack (private fields: the only view of entries behind and ahead of the current one). */
  function stack(): { _history: { path: string }[]; _historyIndex: number } {
    return location as unknown as { _history: { path: string }[]; _historyIndex: number };
  }

  /** The browser history entries from the list on (the SpyLocation starts with one blank entry). */
  function history(): string[] {
    return stack()._history.slice(1).map(state => state.path);
  }

  async function click(selector: string): Promise<void> {
    const target = await vi.waitFor(() => {
      harness.detectChanges();
      const found = root().querySelector<HTMLElement>(selector);
      if (!found) {
        throw new Error(`no ${selector}`);
      }
      return found;
    });
    target.click();
    await settle();
  }

  /** A split row on the list: as the timeline's `open(row)` does. */
  async function openGroupFromList(): Promise<void> {
    await router.navigate(['/accounting/entries', 42, 'group'], { state: FROM_LIST });
    await settle();
    expect(root().querySelector('.split-group')).not.toBeNull();
  }

  it('list → group (push) → child (push) → ✕ lands on the list without growing history', async () => {
    await openGroupFromList();
    await click('.split-child[data-member-id="43"]');
    expect(location.path()).toBe('/accounting/entries/43');
    expect(history()).toEqual(['/accounting', '/accounting/entries/42/group', '/accounting/entries/43']);

    await click('.dhead .close');
    await vi.waitFor(() => expect(location.path()).toBe('/accounting'));
    await settle();
    expect(router.url).toBe('/accounting');
    expect(root().querySelector('.list')).not.toBeNull();
    // Back past both pages: no entry added, the list is the current one.
    expect(history()).toEqual(['/accounting', '/accounting/entries/42/group', '/accounting/entries/43']);
    expect(stack()._historyIndex).toBe(1);
  });

  it('child → 「← 多類別」 replaces the child, so ✕ on the group view goes to the list, not to the group view twice', async () => {
    await openGroupFromList();
    await click('.split-child[data-member-id="43"]');
    await click('.group-back');
    expect(location.path()).toBe('/accounting/entries/43/group');
    expect(location.urlChanges).toContain('replace: /accounting/entries/43/group');
    // Replaced in place: no entry added by the child ↔ group hops.
    expect(history()).toEqual(['/accounting', '/accounting/entries/42/group', '/accounting/entries/43/group']);

    // Into a child again (in place of this group view) and back to the group view: still three entries.
    await click('.split-child[data-member-id="44"]');
    expect(location.urlChanges).toContain('replace: /accounting/entries/44');
    expect(history()).toEqual(['/accounting', '/accounting/entries/42/group', '/accounting/entries/44']);
    await click('.group-back');
    expect(history()).toEqual(['/accounting', '/accounting/entries/42/group', '/accounting/entries/44/group']);

    await click('.dhead .close');
    await vi.waitFor(() => expect(location.path()).toBe('/accounting'));
    await settle();
    expect(router.url).toBe('/accounting');
    expect(stack()._historyIndex).toBe(1);
    expect(history()).toEqual(['/accounting', '/accounting/entries/42/group', '/accounting/entries/44/group']);
  });

  it('child → sibling replaces the child; ✕ from the sibling still lands on the list', async () => {
    await openGroupFromList();
    await click('.split-child[data-member-id="43"]');
    await click('.related a.dline');
    expect(location.path()).toBe('/accounting/entries/44');
    expect(location.urlChanges).toContain('replace: /accounting/entries/44');
    expect(history()).toEqual(['/accounting', '/accounting/entries/42/group', '/accounting/entries/44']);

    await click('.dhead .close');
    await vi.waitFor(() => expect(location.path()).toBe('/accounting'));
    expect(stack()._historyIndex).toBe(1);
  });

  it('browser back: child → group view → list; ✕ on the group view goes back to the list', async () => {
    await openGroupFromList();
    await click('.split-child[data-member-id="43"]');

    location.back();
    await settle();
    expect(router.url).toBe('/accounting/entries/42/group');
    expect(root().querySelector('.split-group')).not.toBeNull();

    location.back();
    await settle();
    expect(router.url).toBe('/accounting');

    // Forward to the group view again (its history state kept), then ✕: one step back to the list.
    location.forward();
    await settle();
    expect(router.url).toBe('/accounting/entries/42/group');
    await click('.dhead .close');
    await vi.waitFor(() => expect(location.path()).toBe('/accounting'));
    expect(stack()._historyIndex).toBe(1);
  });
});
