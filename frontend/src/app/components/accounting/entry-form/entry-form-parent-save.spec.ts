import { Component } from '@angular/core';
import { Location } from '@angular/common';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideLocationMocks } from '@angular/common/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EntryDetail, EntryGroupSummary } from '../../../models/accounting.model';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { FROM_LIST } from '../split-group/group-nav';
import { SplitGroupComponent } from '../split-group/split-group';
import { makeAccount, makeAccountDetail, makeCategory, makeEntryDetail, makePreference } from '../testing/fixtures';
import { EntryFormComponent } from './entry-form';

@Component({ selector: 'app-stub-page', template: '<p class="stub">stub</p>' })
class StubPage {}

const GROUP: EntryGroupSummary = { id: 4, kind: 'split', name: '聚餐', merchant: null, description: null, count: 2, total: '-15.0000', currency: 'TWD' };

function member(id: number, amount: string): EntryDetail {
  return makeEntryDetail({ id, amount, group: GROUP });
}

const DETAILS = new Map<number, EntryDetail>();
const MEMBERS = [member(7, '-10.0000'), member(8, '-5.0000')].map(entry => ({ ...entry, protected: false, protected_reason: null }));
for (const entry of MEMBERS) {
  DETAILS.set(entry.id, { ...entry, group_members: MEMBERS });
}

/**
 * PR-9 correction: a save made in parent mode (the group view's 編輯, E on a group view, or the parent bubble selected)
 * returns to the group view, without growing history; a child-mode save is unchanged (entry-form.spec covers it).
 */
describe('entry form parent-mode save → group view', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let router: Router;
  let location: Location;

  beforeEach(async () => {
    // Workers share modules across spec files: never inherit another spec's fake timers (settle() waits on real ones).
    vi.useRealTimers();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideLocationMocks(),
        provideRouter([
          { path: 'accounting', component: StubPage },
          { path: 'accounting/entries/:id/group', component: SplitGroupComponent },
          { path: 'accounting/entries/:id/edit', component: EntryFormComponent },
          { path: 'accounting/entries/:id', component: StubPage },
        ]),
      ],
    });
    TestBed.inject(LayoutModeService).set('phone');
    http = TestBed.inject(HttpTestingController);
    router = TestBed.inject(Router);
    location = TestBed.inject(Location);
    harness = await RouterTestingHarness.create();
    router.setUpLocationChangeListener();
    await harness.navigateByUrl('/accounting');
  });

  afterEach(() => localStorage.clear());

  function stack(): { _history: { path: string; query: string }[]; _historyIndex: number } {
    return location as unknown as { _history: { path: string; query: string }[]; _historyIndex: number };
  }

  function history(): string[] {
    return stack()._history.slice(1).map(state => state.path + (state.query ? `?${state.query}` : ''));
  }

  /** Answers every GET from fixtures, on later tasks too (the router handles a popstate on a later task). */
  async function settle(): Promise<void> {
    for (let round = 0; round < 8; round++) {
      await new Promise(resolve => setTimeout(resolve));
      await harness.fixture.whenStable();
      harness.detectChanges();
      for (const req of http.match(r => r.method === 'GET')) {
        if (req.cancelled) continue;
        const path = req.request.url.replace('/api/accounting', '');
        if (path === '/accounts') req.flush([makeAccount()]);
        else if (path === '/projects' || path === '/counterparties') req.flush([]);
        else if (path === '/preference') req.flush(makePreference());
        else if (path === '/categories') req.flush([makeCategory({ id: 12 })]);
        else if (path === '/fx-rate') req.flush({ date: '2026-10-02', base: 'TWD', quote: 'TWD', rate: '1', source: 'test' });
        else if (/^\/accounts\/\d+$/.test(path)) req.flush(makeAccountDetail());
        else if (/^\/entries\/\d+$/.test(path)) req.flush(DETAILS.get(Number(path.split('/').pop())) ?? null);
        else req.flush([]);
      }
    }
    harness.detectChanges();
  }

  function root(): HTMLElement {
    return harness.routeNativeElement as HTMLElement;
  }

  function form(): EntryFormComponent {
    return harness.routeDebugElement!.componentInstance as EntryFormComponent;
  }

  /** Saves the open split form; answers the PUT with the members' ids. */
  async function save(): Promise<void> {
    const editing = form();
    editing.name.set('改名');
    editing.save(false);
    const put = http.expectOne(r => r.method === 'PUT' && r.url.endsWith('/splits/4'));
    put.flush({ group_id: 4, member_ids: [7, 8], members: editing.children().map(child => ({ id: child.id!, client_key: child.key })) });
    await settle();
  }

  async function openGroupFromList(): Promise<void> {
    await router.navigate(['/accounting/entries', 7, 'group'], { state: FROM_LIST });
    await settle();
    expect(root().querySelector('.split-group')).not.toBeNull();
  }

  it('list → group → 編輯 → save → group view → ✕ → list, history not grown', async () => {
    await openGroupFromList();
    root().querySelector<HTMLButtonElement>('.action-edit')!.click();
    await settle();
    expect(router.url).toBe('/accounting/entries/7/edit?select=parent');
    expect(form().parentMode()).toBe(true);

    await save();
    expect(router.url).toBe('/accounting/entries/7/group');
    expect(root().querySelector('.split-group')).not.toBeNull();
    expect(history()).toEqual(['/accounting', '/accounting/entries/7/group', '/accounting/entries/7/edit?select=parent']);
    expect(stack()._historyIndex).toBe(2);

    root().querySelector<HTMLButtonElement>('.dhead .close')!.click();
    await settle();
    expect(router.url).toBe('/accounting');
    expect(stack()._historyIndex).toBe(1);
    expect(history()).toHaveLength(3);
  });

  it('E on a group view (form with ?select=parent) → save → group view', async () => {
    await openGroupFromList();
    // What E resolves to on a group view (keyboard-shortcuts.spec, accounting-layout.spec).
    await router.navigate(['/accounting/entries', 7, 'edit'], { queryParams: { select: 'parent' } });
    await settle();
    await save();
    expect(router.url).toBe('/accounting/entries/7/group');
    expect(stack()._historyIndex).toBe(2);
  });

  it('the parent bubble selected in a child-mode form also returns to the group view', async () => {
    await openGroupFromList();
    await router.navigate(['/accounting/entries', 8, 'edit']);
    await settle();
    expect(form().parentMode()).toBe(false);
    form().selectBubble('parent');
    await settle();
    await save();
    // The group view before the form is the one the owner came from.
    expect(router.url).toBe('/accounting/entries/7/group');
  });

  it('a parent-mode form opened without the group view before it replaces itself with the group view (✕ → list)', async () => {
    await router.navigate(['/accounting/entries', 7, 'edit'], { queryParams: { select: 'parent' } });
    await settle();
    await save();
    expect(router.url).toBe('/accounting/entries/7/group');
    expect(location.getState()).toMatchObject({ closeTo: 'list' });
    expect(history()).toEqual(['/accounting', '/accounting/entries/7/group']);

    const navigate = vi.spyOn(router, 'navigateByUrl');
    root().querySelector<HTMLButtonElement>('.dhead .close')!.click();
    await settle();
    expect(navigate).toHaveBeenCalledWith('/accounting');
    expect(router.url).toBe('/accounting');
  });

  it('a child-mode save still lands on the saved child', async () => {
    await openGroupFromList();
    await router.navigate(['/accounting/entries', 8, 'edit']);
    await settle();
    await save();
    expect(router.url).toBe('/accounting/entries/8');
    expect(location.getState()).toMatchObject({ closeTo: 'list' });
  });
});
