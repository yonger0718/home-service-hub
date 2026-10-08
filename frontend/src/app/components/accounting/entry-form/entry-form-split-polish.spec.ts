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
import { LayoutMode, LayoutModeService } from '../../../services/layout-mode.service';
import { SplitGroupComponent } from '../split-group/split-group';
import { makeAccount, makeAccountDetail, makeCategory, makeEntryDetail, makePreference } from '../testing/fixtures';
import { EntryFormComponent } from './entry-form';
import { MAX_CHILDREN } from './split-draft';
import { dissolveNotices } from './split-summary';

@Component({ selector: 'app-stub-page', template: '<p class="stub">stub</p>' })
class StubPage {}

const GROUP: EntryGroupSummary = { id: 4, kind: 'split', name: '聚餐', merchant: null, description: null, count: 2, total: '-15.0000', currency: 'TWD' };

/**
 * PR-10 (AGENT-78) split UX polish: a convert lands on the group view (the PR-9 parent-mode rule) with 整筆名稱 taken
 * from the single when empty; removal notices sit outside the scrolling fields, above the keypad dock; 最多 50 項 sits
 * outside the scrolling bubble row. (Pixel positions at 390 / 760 / 1280 are checked against a real build.)
 */
describe('entry form split polish', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let router: Router;
  let location: Location;
  const details = new Map<number, EntryDetail>();

  async function setUp(mode: LayoutMode): Promise<void> {
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
    TestBed.inject(LayoutModeService).set(mode);
    http = TestBed.inject(HttpTestingController);
    router = TestBed.inject(Router);
    location = TestBed.inject(Location);
    harness = await RouterTestingHarness.create();
    router.setUpLocationChangeListener();
    await harness.navigateByUrl('/accounting');
  }

  beforeEach(() => {
    // Workers share modules across spec files: never inherit another spec's fake timers (settle() waits on real ones).
    vi.useRealTimers();
    details.clear();
    details.set(7, makeEntryDetail({ id: 7, name: '午餐', amount: '-10.0000', category_id: 12 }));
  });

  afterEach(() => localStorage.clear());

  /** Answers every GET from fixtures, on later tasks too. */
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
        else if (/^\/entries\/\d+$/.test(path)) req.flush(details.get(Number(path.split('/').pop())) ?? null);
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

  async function openSingle(): Promise<void> {
    await router.navigate(['/accounting/entries', 7]);
    await router.navigate(['/accounting/entries', 7, 'edit']);
    await settle();
    expect(form().children()).toHaveLength(1);
  }

  /** ＋ and a second child (category 12, 5). */
  async function addSecond(): Promise<void> {
    form().addChild();
    await settle();
    form().onCategoryPicked(makeCategory({ id: 12 }));
    form().onAmountInput('5');
    await settle();
  }

  /** Answers the convert PUT with the anchor kept (7) and the new child as 9; the group view then loads entry 7. */
  function answerConvert(): Record<string, unknown> {
    const put = http.expectOne(r => r.method === 'PUT' && r.url.endsWith('/entries/7/split'));
    const members = [7, 9].map((id, index) =>
      ({ ...makeEntryDetail({ id, amount: index ? '-5.0000' : '-10.0000', group: GROUP }), protected: false, protected_reason: null }),
    );
    for (const entry of members) {
      details.set(entry.id, { ...entry, group_members: members });
    }
    put.flush({ group_id: 4, member_ids: [7, 9], members: form().children().map((child, index) => ({ id: [7, 9][index], client_key: child.key })) });
    return put.request.body;
  }

  describe.each([
    ['phone', 390],
    ['sheet', 760],
    ['panes', 1280],
  ] as const)('convert (%s, %ipx)', (mode, _width) => {
    beforeEach(() => setUp(mode));

    it('single → ＋ → ✓ lands on the group view anchored on the kept id, not on the new child', async () => {
      await openSingle();
      await addSecond();
      form().save(false);
      answerConvert();
      await settle();
      expect(router.url).toBe('/accounting/entries/7/group');
      expect(root().querySelector('.split-group')).not.toBeNull();
      // Replaces the form (no stacking); ✕ goes to the list (`closeTo`, as after any split save).
      expect(location.getState()).toMatchObject({ closeTo: 'list' });
      const history = (location as unknown as { _history: { path: string }[] })._history.slice(1).map(state => state.path);
      expect(history).toEqual(['/accounting', '/accounting/entries/7', '/accounting/entries/7/group']);
    });
  });

  describe('整筆名稱 at ＋', () => {
    beforeEach(() => setUp('phone'));

    it('an empty parent name takes the single name, which child 1 keeps too', async () => {
      await openSingle();
      await addSecond();
      expect(form().parent().name).toBe('午餐');
      expect(form().children()[0].name).toBe('午餐');
      form().save(false);
      const body = answerConvert();
      expect(body).toMatchObject({ name: '午餐' });
      expect((body['members'] as Record<string, unknown>[])[0]).toMatchObject({ id: 7, name: '午餐' });
      await settle();
    });

    it('a parent name already set is not overwritten (a later ＋, or one set before the first ＋)', async () => {
      await openSingle();
      await addSecond();
      form().selectBubble('parent');
      await settle();
      const input = root().querySelector<HTMLInputElement>('input[aria-label="整筆名稱"]')!;
      expect(input.value).toBe('午餐');
      input.value = '週末聚餐';
      input.dispatchEvent(new Event('input'));
      form().addChild();
      await settle();
      expect(form().children()).toHaveLength(3);
      expect(form().parent().name).toBe('週末聚餐');
    });

    it('only an empty parent name is prefilled at the single → split ＋', async () => {
      await openSingle();
      form().parent.update(parent => ({ ...parent, name: '既有名稱' }));
      form().addChild();
      await settle();
      expect(form().parent().name).toBe('既有名稱');
      expect(form().children()[0].name).toBe('午餐');
    });

    it('a single without a name leaves the parent name empty', async () => {
      details.set(7, makeEntryDetail({ id: 7, name: null, amount: '-10.0000', category_id: 12 }));
      await openSingle();
      await addSecond();
      expect(form().parent().name).toBe('');
    });
  });

  describe.each(['phone', 'sheet'] as const)('dissolve notice (%s)', mode => {
    beforeEach(() => setUp(mode));

    it('keeps the dissolve text and renders outside the scrolling fields, right above the keypad dock', async () => {
      const members = [7, 8].map(id => ({
        ...makeEntryDetail({ id, amount: '-5.0000', name: `子項${id}`, group: GROUP }), protected: false, protected_reason: null,
      }));
      for (const entry of members) {
        details.set(entry.id, { ...entry, group_members: members });
      }
      await router.navigate(['/accounting/entries', 8, 'edit']);
      await settle();
      form().removeChild();
      await settle();
      const expected = dissolveNotices(form().parent(), form().children()[0]);
      expect(expected).toContain('整筆名稱「聚餐」將不保留');
      const notices = Array.from(root().querySelectorAll('.removal-notice'));
      expect(notices.map(node => node.textContent?.trim())).toEqual(expected);
      const box = root().querySelector('.removal-notices')!;
      expect(box.closest('.content')).toBeNull();
      expect(box.nextElementSibling?.classList).toContain('entrybottom');
      if (mode === 'phone') {
        expect(box.nextElementSibling?.querySelector('app-amount-keypad')).not.toBeNull();
      }
    });
  });

  describe('最多 50 項', () => {
    beforeEach(() => setUp('phone'));

    it('shows the hint outside the scrolling bubble row, next to the disabled ＋', async () => {
      await openSingle();
      while (form().children().length < MAX_CHILDREN) {
        form().addChild();
        harness.detectChanges();
      }
      await settle();
      const add = root().querySelector<HTMLButtonElement>('.strip.bubbles .add')!;
      expect(add.disabled).toBe(true);
      const hint = root().querySelector('.bubble-hint')!;
      expect(hint.textContent?.trim()).toBe('最多 50 項');
      expect(hint.closest('.strip')).toBeNull();
      expect(hint.previousElementSibling?.classList).toContain('bubbles');
    });
  });
});
