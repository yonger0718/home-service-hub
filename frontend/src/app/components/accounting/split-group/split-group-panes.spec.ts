import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideLocationMocks } from '@angular/common/testing';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { routes } from '../../../app.routes';
import { EntryDetail, EntryGroupSummary } from '../../../models/accounting.model';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { makeEntryDetail, makeGroupMember } from '../testing/fixtures';
import { LedgerTimelineComponent, TimelineRow } from '../timeline/timeline';

const SPLIT_A: EntryGroupSummary = { id: 4, kind: 'split', name: 'A', merchant: null, description: null, count: 2, total: '-200.0000', currency: 'TWD' };
const SPLIT_B: EntryGroupSummary = { ...SPLIT_A, id: 5, name: 'B' };
const MEMBERS: Record<number, EntryGroupSummary> = { 42: SPLIT_A, 43: SPLIT_A, 52: SPLIT_B, 53: SPLIT_B };

function detail(id: number): EntryDetail {
  const group = MEMBERS[id] ?? null;
  const members = group ? Object.keys(MEMBERS).map(Number).filter(other => MEMBERS[other] === group) : [];
  return makeEntryDetail({
    id, amount: '-100.0000', group,
    group_members: members.map(member => makeGroupMember({ id: member, amount: '-100.0000', group: group! })),
  });
}

/** A timeline row standing for a split (what `buildDays` makes of it). */
function splitRow(entryId: number): TimelineRow {
  return {
    key: `g${entryId}`, entryId, memberIds: [entryId], icon: '', color: '', title: '多類別', sub: '', amountText: '',
    tone: 'out', fx: null, pills: [], groupCount: 2, folder: { icons: [], count: 2 },
  };
}

/**
 * PR-9 correction (panes ✕ on a stale page): at ≥ 1024 px the timeline stays clickable while a page is open on the right,
 * so a split row clicked from there must not claim the list is below the new group view. Real routes, layout and
 * timeline; SpyLocation as history.
 */
describe('多類別 group view opened beside an open page (panes)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let router: Router;

  beforeEach(async () => {
    // Workers share modules across spec files: never inherit another spec's fake timers (settle() waits on real ones).
    vi.useRealTimers();
    TestBed.configureTestingModule({
      providers: [provideRouter(routes), provideHttpClient(), provideHttpClientTesting(), provideLocationMocks()],
    });
    TestBed.inject(LayoutModeService).set('panes');
    http = TestBed.inject(HttpTestingController);
    router = TestBed.inject(Router);
    harness = await RouterTestingHarness.create();
    router.setUpLocationChangeListener();
  });

  /** Answers the entry reads of the pane pages; the timeline's own reads stay unanswered (not needed here). */
  async function settle(): Promise<void> {
    for (let round = 0; round < 6; round++) {
      await new Promise(resolve => setTimeout(resolve));
      await harness.fixture.whenStable();
      harness.detectChanges();
      for (const req of http.match(r => /\/entries\/\d+$/.test(r.url))) {
        req.flush(detail(Number(/(\d+)$/.exec(req.request.url)![1])));
      }
      http.match(r => r.url === '/api/accounting/accounts').forEach(req => req.flush([]));
    }
  }

  async function timeline(): Promise<LedgerTimelineComponent> {
    return vi.waitFor(() => {
      harness.detectChanges();
      const found = harness.routeDebugElement!.query(By.directive(LedgerTimelineComponent));
      if (!found) {
        throw new Error('no timeline yet');
      }
      return found.componentInstance as LedgerTimelineComponent;
    });
  }

  async function click(selector: string): Promise<void> {
    const target = await vi.waitFor(() => {
      harness.detectChanges();
      const found = harness.routeNativeElement!.querySelector<HTMLElement>(selector);
      if (!found) {
        throw new Error(`no ${selector}`);
      }
      return found;
    });
    target.click();
    await settle();
  }

  it('group A → child A1 → split row B → ✕ ends on the timeline, not on A1', async () => {
    await harness.navigateByUrl('/accounting');
    const list = await timeline();

    list.open(splitRow(42));
    await settle();
    expect(router.url).toBe('/accounting/entries/42/group');
    await click('.detail-pane .split-child[data-member-id="43"]');
    expect(router.url).toBe('/accounting/entries/43');

    list.open(splitRow(52));
    await settle();
    expect(router.url).toBe('/accounting/entries/52/group');
    await click('.detail-pane app-split-group .dhead .close');
    await vi.waitFor(() => expect(router.url).toBe('/accounting'));
  });

  it('entry detail open → split row → ✕ ends on the timeline', async () => {
    await harness.navigateByUrl('/accounting/entries/9');
    await settle();
    const list = await timeline();

    list.open(splitRow(52));
    await settle();
    expect(router.url).toBe('/accounting/entries/52/group');
    await click('.detail-pane app-split-group .dhead .close');
    await vi.waitFor(() => expect(router.url).toBe('/accounting'));
  });

  it('a split row clicked while the timeline is the page still goes back one step on ✕', async () => {
    await harness.navigateByUrl('/accounting');
    const list = await timeline();
    list.open(splitRow(42));
    await settle();
    await click('.detail-pane app-split-group .dhead .close');
    await vi.waitFor(() => expect(router.url).toBe('/accounting'));
  });
});
