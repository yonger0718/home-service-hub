import { Component } from '@angular/core';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EntryDetail, LedgerAccount } from '../../../models/accounting.model';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { DirtyFormRegistry } from '../dirty-form.service';
import { makeAccount, makeAccountDetail, makeCategory, makeEntryDetail, makePreference } from '../testing/fixtures';
import { EntryFormComponent } from './entry-form';

@Component({ template: '' })
class Destination {}

/** Category id each kind's fixture tree answers with, so a stale tree is visible as a wrong id. */
const CATEGORY_IDS: Record<string, number> = { expense: 12, income: 31, receivable: 41, payable: 51 };

describe('split form integration', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let form: EntryFormComponent;
  const entries = new Map<number, EntryDetail>();
  let fixtureAccounts: LedgerAccount[];

  beforeEach(() => {
    entries.clear();
    localStorage.clear();
    fixtureAccounts = [makeAccount(), makeAccount({ id: 2 })];
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([
          { path: 'accounting', component: Destination },
          { path: 'accounting/entry', component: EntryFormComponent },
          { path: 'accounting/entries/:id/edit', component: EntryFormComponent },
          { path: 'accounting/entries/:id', component: Destination },
        ]),
      ],
    });
    http = TestBed.inject(HttpTestingController);
    TestBed.inject(LayoutModeService).set('phone');
  });

  afterEach(() => {
    http.verify();
    localStorage.clear();
    vi.restoreAllMocks();
  });

  /** Answers every GET from fixtures; an unexpected URL throws instead of masking an accidental dependency. */
  function settle(): void {
    for (let round = 0; round < 8; round++) {
      harness.detectChanges();
      for (const req of http.match(r => r.method === 'GET')) {
        if (req.cancelled) continue;
        const path = req.request.url.replace('/api/accounting', '');
        const params = req.request.params;
        if (path === '/accounts') req.flush(fixtureAccounts);
        else if (path === '/projects' || path === '/counterparties') req.flush([]);
        else if (path === '/preference') req.flush(makePreference());
        else if (path === '/fx-rate') {
          req.flush({ date: params.get('date'), base: params.get('base'), quote: params.get('quote'), rate: '30', source: 'test' });
        } else if (path === '/categories') {
          const kind = params.get('kind') ?? 'expense';
          req.flush([makeCategory({ id: CATEGORY_IDS[kind] ?? 12, kind })]);
        } else if (/^\/accounts\/\d+$/.test(path)) {
          req.flush(makeAccountDetail(fixtureAccounts.find(a => a.id === Number(path.split('/').pop()))));
        } else if (/^\/entries\/\d+$/.test(path)) {
          const detail = entries.get(Number(path.split('/').pop()));
          if (!detail) throw new Error(`missing fixture ${path}`);
          req.flush(detail);
        } else throw new Error(`unexpected GET ${path}`);
      }
    }
  }

  function seedGroup(a: EntryDetail, b: EntryDetail): void {
    const group = { id: 4, kind: 'split' as const, name: null, merchant: null, description: null, count: 2, total: null, currency: 'TWD' };
    const members = [a, b].map(c => ({ ...c, protected: false, protected_reason: null }));
    entries.set(a.id, { ...a, group, group_members: members });
    entries.set(b.id, { ...b, group, group_members: members });
  }

  function clickKey(key: string): void {
    const button = harness.routeNativeElement!.querySelector<HTMLButtonElement>(`app-amount-keypad [data-key="${key}"]`);
    expect(button).not.toBeNull();
    button!.click();
    settle();
  }

  async function open(path = '/accounting/entry'): Promise<void> {
    harness = await RouterTestingHarness.create();
    form = await harness.navigateByUrl(path, EntryFormComponent);
    settle();
  }

  /** Selected child: account 1, category 12, amount. */
  function fill(amount = '10'): void {
    form.chooseAccount(1);
    form.onCategoryPicked(makeCategory({ id: 12 }));
    form.onAmountInput(amount);
    settle();
  }

  function splitResult(firstId = 7) {
    return {
      group_id: 4,
      member_ids: form.children().map((_, i) => firstId + i),
      members: form.children().map((c, i) => ({ id: firstId + i, client_key: c.key })),
    };
  }

  // ---- Task 7: every save route with real HTTP bodies ------------------------------------------------------------

  it('POST new split sends explicit children; indexed 422 selects and stays dirty', async () => {
    await open();
    fill();
    form.name.set('first');
    form.merchant.set('shop');
    form.addChild();
    settle();
    fill('20');
    expect(form.children()).toHaveLength(2);
    form.selectBubble('parent');
    settle();
    expect(harness.routeNativeElement!.querySelector('app-amount-keypad')).toBeNull();
    expect(harness.routeNativeElement!.querySelector('.kinds')).toBeNull();
    form.save(false);
    const req = http.expectOne(r => r.method === 'POST' && r.url.endsWith('/splits'));
    expect(req.request.body).toMatchObject({ merchant: 'shop', name: null, project_id: null, tags: [] });
    expect(req.request.body.members).toHaveLength(2);
    expect(req.request.body.members.every((m: Record<string, unknown>) => !('id' in m))).toBe(true);
    expect(req.request.body.members[0]).toMatchObject({ project_id: null, tags: [], merchant: null, name: 'first' });
    expect(req.request.body.members.map((m: { client_key: string }) => m.client_key)).toEqual(form.children().map(c => c.key));
    req.flush({ detail: [{ loc: ['body', 'members', 1, 'amount'], msg: 'invalid' }] }, { status: 422, statusText: 'Unprocessable Entity' });
    settle();
    expect(form.selected()).toBe(form.children()[1].key);
    expect(form.isDirty()).toBe(true);
    expect(harness.routeNativeElement!.textContent).toContain('invalid');
    expect(form.children()).toHaveLength(2);
  });

  it('convert keeps anchor id and pre-split date edits, success clears pending discard', async () => {
    entries.set(7, makeEntryDetail({ id: 7, entry_time: '01:02:03.456' }));
    await open('/accounting/entries/7/edit');
    form.setParentDate('entryTime', '11:12');
    form.addChild();
    settle();
    fill('20');
    form.save(false);
    const req = http.expectOne(r => r.method === 'PUT' && r.url.endsWith('/entries/7/split'));
    expect(req.request.body.members.map((m: Record<string, unknown>) => m['id'])).toEqual([7, undefined]);
    expect(req.request.body.members.every((m: Record<string, unknown>) => m['entry_time'] === '11:12')).toBe(true);
    const discarded = vi.fn();
    const registry = TestBed.inject(DirtyFormRegistry);
    registry.requestClose(discarded);
    settle();
    expect(registry.promptOpen()).toBe(true);
    expect(discarded).not.toHaveBeenCalled();
    req.flush(splitResult());
    await harness.fixture.whenStable();
    expect(registry.promptOpen()).toBe(false);
    registry.confirmDiscard();
    expect(discarded).not.toHaveBeenCalled();
  });

  it('upserts a group with the protected member as keep metadata and the other as a full member', async () => {
    const group = { id: 4, kind: 'split' as const, name: 'Parent', merchant: null, description: null, count: 2, total: '-5', currency: 'TWD' };
    const a = makeEntryDetail({ id: 7, group, amount: '-15.0000' });
    const b = makeEntryDetail({ id: 8, kind: 'refund', amount: '10.0000', name: 'back', group });
    const members = [{ ...a, protected: false, protected_reason: null }, { ...b, protected: true, protected_reason: 'refund' }];
    entries.set(7, { ...a, group_members: members });
    entries.set(8, { ...b, group_members: members });
    await open('/accounting/entries/7/edit');
    form.onAmountInput('20');
    form.selectBubble(form.children()[1].key);
    settle();
    form.description.set('memo');
    form.amountExpr.set('999');
    form.save(false);
    const req = http.expectOne(r => r.method === 'PUT' && r.url.endsWith('/splits/4'));
    const [full, keep] = req.request.body.members;
    expect(full).toMatchObject({ id: 7, client_key: form.children()[0].key, amount: '20', kind: 'expense' });
    expect(keep).toEqual({
      id: 8, keep: true, client_key: form.children()[1].key, name: 'back', project_id: null, tags: [], description: 'memo',
    });
    req.flush(splitResult());
    await harness.fixture.whenStable();
  });

  it('dissolves with a persisted protected survivor using keep and parent values', async () => {
    const group = { id: 4, kind: 'split' as const, name: 'Parent', merchant: 'Shop', description: 'Note', count: 2, total: '0', currency: 'TWD' };
    const a = makeEntryDetail({ id: 7, kind: 'refund', amount: '10', group });
    const b = makeEntryDetail({ id: 8, group });
    a.group_members = [{ ...a, protected: true, protected_reason: 'refund' }, { ...b, protected: false, protected_reason: null }];
    entries.set(7, a);
    entries.set(8, { ...b, group_members: a.group_members });
    await open('/accounting/entries/7/edit');
    form.selectBubble(form.children()[1].key);
    form.removeChild();
    settle();
    form.save(false);
    const req = http.expectOne(r => r.method === 'PUT' && r.url.endsWith('/splits/4'));
    expect(req.request.body).toMatchObject({ name: 'Parent', merchant: 'Shop', description: 'Note' });
    expect(req.request.body.members).toEqual([
      { id: 7, keep: true, client_key: form.children()[0].key, name: null, project_id: null, tags: [], description: null },
    ]);
    req.flush({ group_id: null, member_ids: [7], members: [{ id: 7, client_key: form.children()[0].key }] });
    await harness.fixture.whenStable();
    expect(form.groupId()).toBeNull();
  });

  it('single→split→single preserves child name/note and chooses parent merchant', async () => {
    await open();
    fill();
    form.name.set('child');
    form.description.set('note');
    form.merchant.set('shop');
    form.addChild();
    settle();
    form.setParentText('name', 'group');
    form.setParentText('merchant', 'new shop');
    form.removeChild();
    settle();
    expect(form.drafts.droppedNotices()).toEqual(['整筆名稱「group」不會保留']);
    form.save(false);
    const req = http.expectOne(r => r.method === 'POST' && r.url.endsWith('/entries'));
    expect(req.request.body).toMatchObject({ name: 'child', description: 'note', merchant: 'new shop' });
    req.flush(makeEntryDetail({ id: 30 }));
    await harness.fixture.whenStable();
  });

  it('keeps failed save dirty and never automatically retries a 409', async () => {
    await open();
    fill();
    form.addChild();
    settle();
    fill('20');
    form.save(false);
    http.expectOne(r => r.method === 'POST')
      .flush({ code: 'conflict', message: 'retry', trace_id: 'test' }, { status: 409, statusText: 'Conflict' });
    settle();
    expect(form.isDirty()).toBe(true);
    expect(form.error()).toBe('記錄剛被更新，請重新載入後再試');
    http.expectNone(r => r.method !== 'GET');
  });

  it('retains each untouched raw date on group upsert', async () => {
    const group = { id: 4, kind: 'split' as const, name: null, merchant: null, description: null, count: 2, total: '-20', currency: 'TWD' };
    const a = makeEntryDetail({ id: 7, group, entry_date: '2026-01-01', entry_time: '01:02:03.456', posted_date: '2026-01-03' });
    const b = makeEntryDetail({ id: 8, group, entry_date: '2026-02-02', entry_time: '04:05:06', posted_date: '2026-02-02' });
    a.group_members = [{ ...a, protected: false, protected_reason: null }, { ...b, protected: false, protected_reason: null }];
    entries.set(7, a);
    entries.set(8, { ...b, group_members: a.group_members });
    await open('/accounting/entries/7/edit');
    form.onAmountInput('15');
    form.save(false);
    const req = http.expectOne(r => r.method === 'PUT');
    expect(req.request.body.members.map((m: Record<string, unknown>) => [m['entry_date'], m['entry_time'], m['posted_date']]))
      .toEqual([['2026-01-01', '01:02:03.456', '2026-01-03'], ['2026-02-02', '04:05:06', '2026-02-02']]);
    req.flush({ detail: 'test rejection' }, { status: 422, statusText: 'Unprocessable Entity' });
    settle();
    expect(form.isDirty()).toBe(true);
  });

  it('save-and-continue clears children, notices and dirty state', async () => {
    await open();
    fill();
    form.addChild();
    settle();
    fill('20');
    form.save(true);
    const req = http.expectOne(r => r.method === 'POST');
    req.flush(splitResult());
    settle();
    expect(form.children()).toHaveLength(1);
    expect(form.groupId()).toBeNull();
    expect(form.drafts.droppedNotices()).toEqual([]);
    expect(form.isDirty()).toBe(false);
    expect(form.savedFlash()).toBe(true);
  });

  it('an unmappable split answer keeps the draft and offers no automatic retry', async () => {
    await open();
    fill();
    form.addChild();
    settle();
    fill('20');
    form.save(false);
    http.expectOne(r => r.method === 'POST').flush({ group_id: 4, member_ids: [7, 8], members: [{ id: 7, client_key: null }] });
    settle();
    expect(form.saveResponseInvalid()).toBe(true);
    expect(form.error()).toBe('儲存回應缺少子項對應，請重新載入');
    form.save(false);
    http.expectNone(r => r.method !== 'GET');
  });

  // ---- N3: removal shows the survivor's own cached tree and account ---------------------------------------------

  it('removing a child shows the survivor its own category tree, never the removed child stale list', async () => {
    await open();
    fill();
    const survivor = form.children()[0].key;
    form.addChild();
    settle();
    form.selectKind('income');
    settle();
    expect(form.categories().map(c => c.id)).toEqual([31]);
    form.removeChild();
    settle();
    expect(form.selected()).toBe(survivor);
    expect(form.categories().map(c => c.id)).toEqual([12]);
    expect(form.category()?.id).toBe(12);
    expect(form.kind()).toBe('expense');
    expect(form.accountDetail()?.id).toBe(1);
  });
});
