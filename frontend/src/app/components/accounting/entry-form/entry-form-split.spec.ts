import { Component } from '@angular/core';
import { HttpErrorResponse, provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { Subject, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountDetail, CategoryNode, EntryDetail, FxRateOut, LedgerAccount } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { DirtyFormRegistry } from '../dirty-form.service';
import { makeAccount, makeAccountDetail, makeCategory, makeEntryDetail, makePreference, makeRule } from '../testing/fixtures';
import { LAST_USE_PREFIX } from './entry-draft';
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
          // A convert lands on the group view (PR-10).
          { path: 'accounting/entries/:id/group', component: Destination },
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

  // ---- Task 9: bubbles, ownership and async interleavings -------------------------------------------------------

  it('switches loaded bubbles cleanly and flushes keypad to the old child', async () => {
    seedGroup(makeEntryDetail({ id: 7, amount: '-10' }), makeEntryDetail({ id: 8, amount: '-5' }));
    await open('/accounting/entries/7/edit');
    expect(form.isDirty()).toBe(false);
    form.selectBubble(form.children()[1].key);
    settle();
    expect(form.amountExpr()).toBe('5');
    expect(form.isDirty()).toBe(false);
    form.selectBubble(form.children()[0].key);
    settle();
    expect(form.isDirty()).toBe(false);
    clickKey('+');
    clickKey('2');
    const second = harness.routeNativeElement!.querySelector<HTMLButtonElement>(`[data-bubble="${form.children()[1].key}"]`)!;
    second.click();
    settle(); // flush pending 10+2 before changing selected owner
    expect(form.children().map(c => c.amountExpr)).toEqual(['12', '5']);
    clickKey('C');
    clickKey('9');
    clickKey('↵');
    expect(form.children().map(c => c.amountExpr)).toEqual(['12', '9']);
    form.selectBubble('parent');
    settle();
    expect(harness.routeNativeElement!.querySelector('app-amount-keypad')).toBeNull();
    expect(form.children().map(c => c.amountExpr)).toEqual(['12', '9']);
  });

  it('opens in parent mode with ?select=parent (the group view 編輯, E on a split row) and stays clean', async () => {
    seedGroup(makeEntryDetail({ id: 7, amount: '-10' }), makeEntryDetail({ id: 8, amount: '-5' }));
    await open('/accounting/entries/7/edit?select=parent');
    const root = harness.routeNativeElement!;
    expect(form.parentMode()).toBe(true);
    expect(root.querySelector<HTMLButtonElement>('[data-bubble="parent"]')!.getAttribute('aria-pressed')).toBe('true');
    expect(root.querySelector('.split-parent .split-totals')).not.toBeNull();
    expect(root.querySelector('app-amount-keypad')).toBeNull();
    expect(form.children().map(c => c.id)).toEqual([7, 8]);
    expect(form.isDirty()).toBe(false);

    // Without it the opened child is selected, as before.
    await harness.navigateByUrl('/accounting/entries/8/edit', EntryFormComponent);
    settle();
    expect(form.parentMode()).toBe(false);
    expect(form.selected()).toBe(form.children().find(c => c.id === 8)!.key);
  });

  it('on the phone, a tap on the 多類別 folder tile enters parent mode with the ring on the tile', async () => {
    expect(TestBed.inject(LayoutModeService).mode()).toBe('phone');
    seedGroup(makeEntryDetail({ id: 7, amount: '-10' }), makeEntryDetail({ id: 8, amount: '-5' }));
    await open('/accounting/entries/8/edit');
    const root = harness.routeNativeElement!;
    expect(root.querySelector('app-amount-keypad')).not.toBeNull();
    clickKey('+');
    clickKey('2');
    const parent = root.querySelector<HTMLButtonElement>('[data-bubble="parent"]')!;
    expect(parent.classList).not.toContain('on');
    // The tap lands on the aria-hidden folder tile inside the bubble; it is still the bubble's click.
    parent.querySelector('app-split-folder')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    settle();

    expect(form.parentMode()).toBe(true);
    expect(parent.getAttribute('aria-pressed')).toBe('true');
    expect(parent.classList).toContain('on');
    expect(root.querySelector('.split-parent .split-totals')).not.toBeNull();
    const labels = Array.from(root.querySelectorAll('.split-parent [aria-label]')).map(field => field.getAttribute('aria-label'));
    expect(labels).toEqual(expect.arrayContaining(['整筆商家', '整筆日期', '整筆名稱', '整筆備註']));
    expect(root.querySelector('app-amount-keypad')).toBeNull();
    // The pending keypad expression was committed to the child that owned it.
    expect(form.children().map(c => c.amountExpr)).toEqual(['10', '7']);
  });

  it('parent date controls reject changes whenever any child is protected', async () => {
    await open();
    fill();
    form.addChild();
    settle();
    form.children.update(rows => rows.map((c, i) => (i ? { ...c, id: 8, protected: true } : c)));
    form.selectBubble('parent');
    settle();
    const before = structuredClone(form.parent());
    form.setParentDate('entryDate', '2030-01-01');
    expect(form.parent()).toEqual(before);
    const date = harness.routeNativeElement!.querySelector<HTMLInputElement>('[aria-label="整筆日期"]')!;
    expect(date.disabled).toBe(true);
    expect(harness.routeNativeElement!.textContent).toContain('含受保護子項，日期不可更改');
  });

  it('retains outgoing defaults, isolates equal-value siblings and drops removed owners', async () => {
    await open();
    fill();
    const pending: Subject<AccountDetail>[] = [];
    const spy = vi.spyOn(TestBed.inject(AccountingService), 'getAccount').mockImplementation(() => {
      const response = new Subject<AccountDetail>();
      pending.push(response);
      return response;
    });
    form.chooseAccount(2);
    settle();
    expect(pending).toHaveLength(1);
    form.addChild();
    settle();
    // The new child has the same account and kind; its request is its own.
    expect(form.children()[1]).toMatchObject({ accountId: 2, kind: 'expense' });
    expect(pending).toHaveLength(2);
    pending[0].next(makeAccountDetail({ id: 2, reward_rules: [makeRule({ id: 99, account_id: 2, is_basic: true })] }));
    pending[0].complete();
    settle();
    // Applied to the outgoing (unselected) child only.
    expect(form.children()[0].ruleIds).toEqual([99]);
    expect(form.children()[1].ruleIds).toEqual([]);
    form.removeChild();
    settle();
    pending[1].next(makeAccountDetail({ id: 2, reward_rules: [makeRule({ id: 6, account_id: 2, is_basic: true })] }));
    pending[1].complete();
    settle();
    expect(form.children()).toHaveLength(1);
    expect(form.children()[0].ruleIds).toEqual([99]);
    expect(form.draftLoadsPending()).toBe(false);
    spy.mockRestore();
  });

  it('drops an in-flight account answer once the same child moved to another account', async () => {
    await open();
    fill();
    const pending: Subject<AccountDetail>[] = [];
    vi.spyOn(TestBed.inject(AccountingService), 'getAccount').mockImplementation(() => {
      const response = new Subject<AccountDetail>();
      pending.push(response);
      return response;
    });
    form.chooseAccount(2);
    settle();
    expect(pending).toHaveLength(1);
    // Back to account 1 (already read): a new generation for the same key, answered from this navigation's cache.
    form.chooseAccount(1);
    settle();
    pending[0].next(makeAccountDetail({ id: 2, reward_rules: [makeRule({ id: 99, account_id: 2, is_basic: true })] }));
    pending[0].complete();
    settle();
    expect(form.children()[0]).toMatchObject({ accountId: 1, ruleIds: [] });
    expect(form.accountDetail()?.id).toBe(1);
  });

  it('a failed child settings read blocks the split save until 重新載入子項設定 succeeds', async () => {
    await open();
    fill();
    form.addChild();
    settle();
    vi.spyOn(TestBed.inject(AccountingService), 'getAccount')
      .mockReturnValueOnce(throwError(() => new HttpErrorResponse({ status: 500, statusText: 'Server Error' })));
    form.chooseAccount(2);
    form.onCategoryPicked(makeCategory({ id: 12 }));
    form.onAmountInput('20');
    settle();
    const root = harness.routeNativeElement!;
    expect(form.draftLoadsFailed()).toBe(true);
    expect(root.querySelector<HTMLButtonElement>('button.save')!.disabled).toBe(true);
    form.save(false);
    http.expectNone(r => r.method === 'POST');
    expect(form.error()).toBe('子項設定讀取失敗，請重新載入子項設定');
    root.querySelector<HTMLButtonElement>('.retry-children')!.click();
    settle();
    expect(form.draftLoadsFailed()).toBe(false);
    expect(form.children()[1].amountExpr).toBe('20');
    form.save(false);
    const req = http.expectOne(r => r.method === 'POST' && r.url.endsWith('/splits'));
    expect(req.request.body.members.map((m: { account_id: number }) => m.account_id)).toEqual([1, 2]);
    req.flush({ detail: [{ loc: ['members.1.amount'], msg: 'test rejection' }] }, { status: 422, statusText: 'Unprocessable Entity' });
  });

  it('parent Escape uses the existing discard prompt', async () => {
    await open();
    fill();
    form.addChild();
    settle();
    form.selectBubble('parent');
    settle();
    harness.routeNativeElement!.querySelector('.entry-form')!.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
    );
    settle();
    expect(TestBed.inject(DirtyFormRegistry).promptOpen()).toBe(true);
  });

  // ---- R1: single originals, validation, defaults, field map ----------------------------------------------------

  it.each(['settled', 'refunded', 'loan'] as const)('keeps %s single editing but hides convert plus', async marker => {
    const d = makeEntryDetail({ id: 7 });
    if (marker === 'settled') d.settled_by = [makeEntryDetail({ id: 8, is_settlement: true })];
    if (marker === 'refunded') d.refunded_by = [makeEntryDetail({ id: 8, kind: 'refund' })];
    if (marker === 'loan') {
      d.kind = 'payable';
      d.counterparty = 'Alan';
      d.counterparty_id = 4;
      d.loan_schedule = {
        definition_id: 12, name: '信貸 每月還款', status: 'active', posting_mode: 'auto', posted_count: 3, times: 36,
        next_due_date: '2027-02-09', next_amount: [{ currency: 'TWD', amount: '-8953.0000' }],
        remaining: '-275001.0000', repaid: '24999.0000', needs_check: false,
      };
    }
    entries.set(7, d);
    await open('/accounting/entries/7/edit');
    if (marker === 'loan') form.counterparties.set([{ id: 4, name: 'Alan', open_amounts: [], moze_id: null }]);
    expect(form.children()[0].protected).toBe(false);
    expect(form.addState().visible).toBe(false);
    form.name.set('changed');
    form.save(false);
    const req = http.expectOne(r => r.method === 'PUT' && r.url.endsWith('/entries/7'));
    expect(req.request.body.name).toBe('changed');
    if (marker === 'loan') expect(req.request.body.counterparty_id).toBe(4);
    req.flush(makeEntryDetail({ id: 7, name: 'changed' }));
    await harness.fixture.whenStable();
  });

  it.each(['installment', 'reward_claim'] as const)('keeps a %s group member single editing but hides convert plus', async kind => {
    const group = { id: 9, kind, name: null, merchant: null, description: null, count: 3, total: null, currency: 'TWD' };
    entries.set(7, makeEntryDetail({ id: 7, group }));
    await open('/accounting/entries/7/edit');
    expect(form.children()).toHaveLength(1);
    expect(form.children()[0].protected).toBe(false);
    expect(form.groupId()).toBeNull();
    expect(form.addState().visible).toBe(false);
    form.name.set('changed');
    form.save(false);
    const req = http.expectOne(r => r.method === 'PUT' && r.url.endsWith('/entries/7'));
    expect(req.request.body.name).toBe('changed');
    req.flush(makeEntryDetail({ id: 7, name: 'changed', group }));
    await harness.fixture.whenStable();
  });

  it('a failed category fetch does not replace a loaded tree of the same kind and is retried', async () => {
    await open();
    fill();
    const first = new Subject<CategoryNode[]>();
    const failures = new Subject<CategoryNode[]>();
    const spy = vi.spyOn(TestBed.inject(AccountingService), 'getCategories');
    spy.mockReturnValueOnce(first).mockReturnValueOnce(failures);
    form.addChild();
    settle();
    form.selectKind('income');
    settle();
    const firstKey = form.selected();
    // The new child inherits income while the first income fetch is still pending: a second fetch starts.
    form.addChild();
    settle();
    expect(form.kind()).toBe('income');
    expect(spy).toHaveBeenCalledTimes(2);
    first.next([makeCategory({ id: 31, kind: 'income' })]);
    first.complete();
    failures.error(new Error('offline'));
    settle();
    expect(form.categories().map(c => c.id)).toEqual([31]);
    form.selectBubble(firstKey);
    settle();
    expect(form.categories().map(c => c.id)).toEqual([31]);
    spy.mockRestore();
  });

  it('a failed category fetch caches nothing, so the next load asks again', async () => {
    await open();
    fill();
    const spy = vi.spyOn(TestBed.inject(AccountingService), 'getCategories');
    spy.mockReturnValueOnce(throwError(() => new Error('offline')));
    form.addChild();
    settle();
    form.selectKind('income');
    settle();
    expect(form.categories()).toEqual([]);
    spy.mockRestore();
    form.addChild();
    settle();
    expect(form.kind()).toBe('income');
    expect(form.categories().map(c => c.id)).toEqual([31]);
  });

  it('routes a 422 under the full / keep union tags to the child field', async () => {
    seedGroup(makeEntryDetail({ id: 7 }), makeEntryDetail({ id: 8, amount: '-20.0000' }));
    await open('/accounting/entries/7/edit');
    for (const [tag, field] of [['full', 'amount'], ['keep', 'name']] as const) {
      form.selectBubble(form.children()[0].key);
      settle();
      form.name.set(`changed ${tag}`);
      form.save(false);
      const req = http.expectOne(r => r.method === 'PUT' && r.url.endsWith('/splits/4'));
      req.flush(
        { detail: [{ loc: ['body', 'members', 1, tag, field], msg: `bad ${field}` }] },
        { status: 422, statusText: 'Unprocessable Entity' },
      );
      settle();
      expect(form.selected()).toBe(form.children()[1].key);
      expect(form.fieldErrors()).toEqual({ [field]: `bad ${field}` });
      expect(form.isDirty()).toBe(true);
    }
  });

  it('an untouched copy whose rule is out of window stays clean; an edit still dirties it', async () => {
    const expired = makeRule({ id: 5, ends_on: '2020-01-31' });
    entries.set(7, makeEntryDetail({ id: 7, rules: [expired] }));
    // The account's rules arrive after the copy has rendered and taken its clean baseline.
    const account = new Subject<AccountDetail>();
    vi.spyOn(TestBed.inject(AccountingService), 'getAccount').mockReturnValue(account);
    await open('/accounting/entry?copy=7');
    expect(form.entryId()).toBeNull();
    expect(form.children()[0].ruleIds).toEqual([5]);
    expect(form.isDirty()).toBe(false);
    account.next(makeAccountDetail({ ...fixtureAccounts[0], reward_rules: [expired] }));
    account.complete();
    settle();
    expect(form.children()[0].ruleIds).toEqual([]);
    expect(form.isDirty()).toBe(false);
    form.name.set('edited');
    settle();
    expect(form.isDirty()).toBe(true);
  });

  it('parent validates each child and blocks missing manual FX before HTTP', async () => {
    await open();
    fill();
    form.addChild();
    settle();
    fill('20');
    form.fx.set({
      original_amount: '20', original_currency: 'USD', account_currency: 'TWD',
      use_online: false, manual: 'rate', fx_rate: null, amount: null, rate_date: null,
    });
    const invalidKey = form.selected();
    form.selectBubble('parent');
    settle();
    form.save(false);
    http.expectNone(r => r.method === 'POST' || r.method === 'PUT');
    expect(form.selected()).toBe(invalidKey);
    expect(form.error()).toBe('請輸入匯率或轉換後金額');
  });

  it('plus opens main grid; picking focuses phone amount and keeps inherited account', async () => {
    await open();
    fill();
    localStorage.setItem(LAST_USE_PREFIX + '12', JSON.stringify({ account_id: 2, project_id: 9 }));
    form.addChild();
    settle();
    const root = harness.routeNativeElement!;
    expect(root.querySelector('app-category-picker .grid')).not.toBeNull();
    root.querySelector<HTMLButtonElement>('app-category-picker .grid .cat')!.click();
    settle();
    expect(root.querySelector('app-category-picker .grid')).toBeNull();
    expect(document.activeElement).toBe(root.querySelector('.amount-value'));
    expect(form.accountId()).toBe(1);
    expect(form.projectId()).toBeNull();
  });

  it('mixed kinds select their own tabs, with transfer and system disabled', async () => {
    seedGroup(makeEntryDetail({ id: 7, kind: 'expense' }), makeEntryDetail({ id: 8, kind: 'income', amount: '30' }));
    await open('/accounting/entries/7/edit');
    expect(form.kind()).toBe('expense');
    form.selectBubble(form.children()[1].key);
    settle();
    expect(form.kind()).toBe('income');
    const tabs = [...harness.routeNativeElement!.querySelectorAll<HTMLButtonElement>('.kind-tab')];
    expect(tabs.find(t => t.getAttribute('aria-selected') === 'true')?.textContent).toContain('收入');
    for (const word of ['轉帳', '系統']) expect(tabs.find(t => t.textContent?.includes(word))?.disabled).toBe(true);
  });

  it('shows a protected system child with its real kind label and disabled financial fields', async () => {
    const group = { id: 4, kind: 'split' as const, name: null, merchant: null, description: null, count: 2, total: '-5', currency: 'TWD' };
    const a = makeEntryDetail({ id: 7, group });
    const b = makeEntryDetail({ id: 8, kind: 'reward', amount: '5', category_id: null, group });
    const members = [{ ...a, protected: false, protected_reason: null }, { ...b, protected: true, protected_reason: 'system' }];
    entries.set(7, { ...a, group_members: members });
    entries.set(8, { ...b, group_members: members });
    await open('/accounting/entries/8/edit');
    const root = harness.routeNativeElement!;
    const tabs = [...root.querySelectorAll<HTMLButtonElement>('.kind-tab')];
    expect(tabs.map(t => t.textContent?.trim())).toEqual(['紅利回饋']);
    expect(tabs[0].disabled).toBe(true);
    expect(root.querySelector('.protected-note')?.textContent).toContain('系統記錄');
    expect(root.querySelector('app-amount-keypad')).toBeNull();
    expect(root.querySelector('app-category-picker .grid')).toBeNull();
    expect(root.querySelector<HTMLButtonElement>('.fee-open')!.disabled).toBe(true);
    expect(root.querySelector<HTMLButtonElement>('button.cur')!.disabled).toBe(true);
    expect(root.querySelector<HTMLInputElement>('.name-input')!.disabled).toBe(false);
    expect(root.querySelector('.remove-child')?.hasAttribute('disabled')).toBe(true);
    expect(root.textContent).toContain('受保護子項不可移除');
  });

  it('changing A account does not clear B FX, fee or rules', async () => {
    await open();
    fill();
    form.addChild();
    settle();
    fill('20');
    form.fx.set({
      original_amount: '20', original_currency: 'USD', account_currency: 'TWD', use_online: false,
      manual: 'amount', amount: '600', fx_rate: null, rate_date: null,
    });
    form.fee.set({ amount: '2', name: null });
    form.ruleIds.set([99]);
    form.rulesTouched.set(true);
    const b = structuredClone(form.children()[1]);
    form.selectBubble(form.children()[0].key);
    form.chooseAccount(2);
    settle();
    expect(form.children()[0].accountId).toBe(2);
    expect(form.children()[1]).toEqual(b);
  });

  it('each of two archived originals retains only its own archived option', async () => {
    fixtureAccounts = [makeAccount(), makeAccount({ id: 3, is_archived: true }), makeAccount({ id: 4, is_archived: true })];
    seedGroup(makeEntryDetail({ id: 7, account_id: 3 }), makeEntryDetail({ id: 8, account_id: 4 }));
    await open('/accounting/entries/7/edit');
    expect(form.accountOptions().map(a => a.id)).toEqual([1, 3]);
    form.selectBubble(form.children()[1].key);
    settle();
    expect(form.accountOptions().map(a => a.id)).toEqual([1, 4]);
  });

  it('existing anchor round-trips metadata and all edited parent dates through plus', async () => {
    entries.set(7, makeEntryDetail({
      id: 7, name: 'child', description: 'note', merchant: 'old shop',
      entry_date: '2026-01-01', entry_time: '01:02:03.456', posted_date: '2026-01-01',
    }));
    await open('/accounting/entries/7/edit');
    form.setParentDate('entryDate', '2026-02-01');
    form.setParentDate('entryTime', '11:12');
    form.setParentDate('postedDate', '2026-02-03');
    settle();
    form.addChild();
    settle();
    fill('20');
    form.save(false);
    const req = http.expectOne(r => r.method === 'PUT' && r.url.endsWith('/entries/7/split'));
    expect(req.request.body.members[0]).toMatchObject({ id: 7, name: 'child', description: 'note', merchant: 'old shop' });
    for (const m of req.request.body.members) {
      expect(m).toMatchObject({ entry_date: '2026-02-01', entry_time: '11:12', posted_date: '2026-02-03' });
    }
    req.flush({ detail: [{ loc: ['body', 'members.1.amount'], msg: 'test rejection' }] }, { status: 422, statusText: 'Unprocessable Entity' });
    settle();
    expect(form.selected()).toBe(form.children()[1].key);
    form.removeChild();
    settle();
    form.merchant.set('new shop');
    form.save(false);
    const single = http.expectOne(r => r.method === 'PUT' && r.url.endsWith('/entries/7'));
    expect(single.request.body).toMatchObject({
      name: 'child', description: 'note', merchant: 'new shop', posted_date: '2026-02-03', entry_time: '11:12',
    });
    single.flush(makeEntryDetail({ id: 7 }));
    await harness.fixture.whenStable();
  });

  it('untouched single keeps time precision and derives posted date after changing date', async () => {
    entries.set(7, makeEntryDetail({ id: 7, entry_date: '2026-01-01', posted_date: '2026-01-01', entry_time: '12:31:09.123' }));
    await open('/accounting/entries/7/edit');
    expect(form.parent().postedDate).toBeNull();
    form.setParentDate('entryDate', '2026-02-01');
    settle();
    form.save(false);
    const req = http.expectOne(r => r.method === 'PUT');
    expect(req.request.body).toMatchObject({ entry_date: '2026-02-01', posted_date: null, entry_time: '12:31:09.123' });
    req.flush(makeEntryDetail({ id: 7 }));
    await harness.fixture.whenStable();
  });

  it('a removed child cannot apply its category response to the survivor', async () => {
    await open();
    fill();
    const pending = new Subject<CategoryNode[]>();
    const spy = vi.spyOn(TestBed.inject(AccountingService), 'getCategories').mockReturnValue(pending);
    form.addChild();
    settle();
    form.selectKind('income');
    settle();
    const removed = form.selected();
    form.removeChild();
    settle();
    pending.next([makeCategory({ id: 99, kind: 'income' })]);
    pending.complete();
    settle();
    expect(form.children().some(c => c.key === removed)).toBe(false);
    expect(form.category()?.id).toBe(12);
    expect(form.kind()).toBe('expense');
    expect(form.categories().some(c => c.id === 99)).toBe(false);
    spy.mockRestore();
  });

  it('a removed child cannot apply its FX response to the survivor', async () => {
    await open();
    fill();
    form.addChild();
    settle();
    fill('20');
    const pending = new Subject<FxRateOut>();
    const spy = vi.spyOn(TestBed.inject(AccountingService), 'getFxRate').mockReturnValue(pending);
    form.fx.set({
      original_amount: '20', original_currency: 'USD', account_currency: 'TWD', use_online: true,
      manual: null, amount: null, fx_rate: null, rate_date: null,
    });
    form.drafts.invalidate(form.selected());
    settle();
    expect(spy).toHaveBeenCalledTimes(1);
    form.removeChild();
    settle();
    pending.next({ date: '2026-10-06', base: 'USD', quote: 'TWD', rate: '999', source: 'test' });
    pending.complete();
    settle();
    expect(form.children()).toHaveLength(1);
    expect(form.fx()).toBeNull();
    spy.mockRestore();
  });

  it('an unselected child still receives its own online quote', async () => {
    await open();
    fill();
    const pending = new Subject<FxRateOut>();
    vi.spyOn(TestBed.inject(AccountingService), 'getFxRate').mockReturnValue(pending);
    form.fx.set({
      original_amount: '10', original_currency: 'USD', account_currency: 'TWD', use_online: true,
      manual: null, amount: null, fx_rate: null, rate_date: null,
    });
    form.drafts.invalidate(form.selected());
    settle();
    form.addChild();
    settle();
    pending.next({ date: '2026-10-06', base: 'USD', quote: 'TWD', rate: '31.5', source: 'test' });
    pending.complete();
    settle();
    expect(form.children()[0].fx).toMatchObject({ fx_rate: '31.5', use_online: true });
    expect(form.children()[1].fx).toBeNull();
  });

  it('shows inconsistent dates and read-only child merchant', async () => {
    seedGroup(makeEntryDetail({ id: 7, entry_date: '2026-01-01', merchant: 'member shop' }), makeEntryDetail({ id: 8, entry_date: '2026-02-01' }));
    await open('/accounting/entries/7/edit');
    expect(harness.routeNativeElement!.textContent).toContain('商家（此項）');
    expect(harness.routeNativeElement!.textContent).toContain('member shop');
    form.selectBubble('parent');
    settle();
    expect(harness.routeNativeElement!.textContent).toContain('子項日期不一致');
  });

  it('reselecting a bubble keeps its caches and invalid draft removal is allowed', async () => {
    await open();
    fill();
    const categories = form.categories();
    form.selectBubble(form.selected());
    expect(form.categories()).toBe(categories);
    form.addChild();
    settle();
    form.onAmountInput('10+');
    form.removeChild();
    settle();
    expect(form.children()).toHaveLength(1);
  });

  it('creates a duplicate new counterparty name once per submission', async () => {
    await open();
    form.selectKind('receivable');
    settle();
    fill();
    form.counterpartyName.set('Alan');
    form.addChild();
    settle();
    fill('20');
    form.counterpartyName.set(' Alan ');
    form.save(false);
    const party = http.expectOne(r => r.method === 'POST' && r.url.endsWith('/counterparties'));
    party.flush({ id: 4, name: 'Alan', open_amounts: [], moze_id: null });
    const split = http.expectOne(r => r.method === 'POST' && r.url.endsWith('/splits'));
    expect(split.request.body.members.map((c: { counterparty_id: number }) => c.counterparty_id)).toEqual([4, 4]);
    split.flush({ detail: [{ loc: ['body', 'members.1.amount'], msg: 'test rejection' }] }, { status: 422, statusText: 'Unprocessable Entity' });
    settle();
    // The failed write must not leave the inline-created 對象 behind; it leaves the typeahead before the DELETE.
    const cleanup = http.expectOne(r => r.method === 'DELETE' && r.url.endsWith('/counterparties/4'));
    expect(form.counterparties()).toEqual([]);
    cleanup.flush(null);
    settle();
    expect(form.counterparties()).toEqual([]);
  });

  it('deletes the first new counterparty when the second one cannot be created', async () => {
    await open();
    form.selectKind('receivable');
    settle();
    fill();
    form.counterpartyName.set('Alan');
    form.addChild();
    settle();
    fill('20');
    form.counterpartyName.set('Bob');
    form.save(false);
    const parties = http.match(r => r.method === 'POST' && r.url.endsWith('/counterparties'));
    expect(parties.map(r => r.request.body.name)).toEqual(['Alan', 'Bob']);
    parties[0].flush({ id: 4, name: 'Alan', open_amounts: [], moze_id: null });
    parties[1].flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    settle();
    http.expectNone(r => r.method === 'POST');
    http.expectOne(r => r.method === 'DELETE' && r.url.endsWith('/counterparties/4')).flush(null);
    settle();
    expect(form.counterparties()).toEqual([]);
  });

  it('deletes the new counterparty when the page is left mid-save (the write never answered)', async () => {
    await open();
    form.selectKind('receivable');
    settle();
    fill();
    form.counterpartyName.set('Alan');
    form.save(false);
    http.expectOne(r => r.method === 'POST' && r.url.endsWith('/counterparties')).flush({ id: 4, name: 'Alan', open_amounts: [], moze_id: null });
    const entry = http.expectOne(r => r.method === 'POST' && r.url.endsWith('/entries'));
    harness.fixture.destroy();
    expect(entry.cancelled).toBe(true);
    http.expectOne(r => r.method === 'DELETE' && r.url.endsWith('/counterparties/4')).flush(null);
  });

  it('⏎ on a <select> in the split form neither adds a line nor saves', async () => {
    await open();
    fill();
    form.addChild();
    settle();
    fill('20');
    const select = harness.routeNativeElement!.querySelector<HTMLSelectElement>('select');
    expect(select).not.toBeNull();
    select!.focus();
    const enter = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
    select!.dispatchEvent(enter);
    settle();
    expect(enter.defaultPrevented).toBe(false);
    expect(form.children()).toHaveLength(2);
    http.expectNone(r => r.method === 'POST');
  });

  it('keeps an inline-created counterparty when the write succeeds', async () => {
    await open();
    form.selectKind('receivable');
    settle();
    fill();
    form.counterpartyName.set('Alan');
    form.save(false);
    http.expectOne(r => r.method === 'POST' && r.url.endsWith('/counterparties')).flush({ id: 4, name: 'Alan', open_amounts: [], moze_id: null });
    const entry = http.expectOne(r => r.method === 'POST' && r.url.endsWith('/entries'));
    expect(entry.request.body.counterparty_id).toBe(4);
    entry.flush(makeEntryDetail({ id: 9, kind: 'receivable', counterparty_id: 4 }));
    settle();
    http.expectNone(r => r.method === 'DELETE');
    expect(form.counterparties().map(p => p.id)).toEqual([4]);
  });

  it('keeps the counterparty when the server refuses its cleanup (the write may have committed)', async () => {
    await open();
    form.selectKind('receivable');
    settle();
    fill();
    form.counterpartyName.set('Alan');
    form.save(false);
    http.expectOne(r => r.method === 'POST' && r.url.endsWith('/counterparties')).flush({ id: 4, name: 'Alan', open_amounts: [], moze_id: null });
    http.expectOne(r => r.method === 'POST' && r.url.endsWith('/entries')).error(new ProgressEvent('error'));
    settle();
    const cleanup = http.expectOne(r => r.method === 'DELETE' && r.url.endsWith('/counterparties/4'));
    // Out of the typeahead while the DELETE is in flight: a retry now creates afresh, never reuses id 4.
    expect(form.counterparties()).toEqual([]);
    cleanup.flush({ detail: 'counterparty is used by 1 entries' }, { status: 409, statusText: 'Conflict' });
    settle();
    // Put back: the server kept it.
    expect(form.counterparties().map(p => p.id)).toEqual([4]);
  });

  it('falls back from archived last-use account and retains project without a default', async () => {
    fixtureAccounts.push(makeAccount({ id: 3, is_archived: true }));
    await open();
    localStorage.setItem(LAST_USE_PREFIX + '12', JSON.stringify({ account_id: 3, project_id: null }));
    form.onCategoryPicked(makeCategory({ id: 12, default_account_id: 2 }));
    settle();
    expect(form.accountId()).toBe(2);
    localStorage.removeItem(LAST_USE_PREFIX + '12');
    form.projectId.set(9);
    form.onCategoryPicked(makeCategory({ id: 12, default_account_id: null, default_project_id: null }));
    settle();
    expect(form.projectId()).toBe(9);
  });

  it('delete-group dialog traps focus, restores opener, then Escape asks about dirty parent', async () => {
    seedGroup(makeEntryDetail({ id: 7 }), makeEntryDetail({ id: 8 }));
    await open('/accounting/entries/7/edit');
    form.selectBubble('parent');
    form.setParentText('name', 'dirty');
    settle();
    const root = harness.routeNativeElement!;
    const opener = root.querySelector<HTMLButtonElement>('.delete-group')!;
    opener.focus();
    opener.click();
    settle();
    const dialog = root.querySelector<HTMLElement>('.delete-group-dialog')!;
    expect(dialog.hasAttribute('data-overlay')).toBe(true);
    const buttons = [...dialog.querySelectorAll<HTMLButtonElement>('button')];
    expect(document.activeElement).toBe(buttons[0]);
    buttons.at(-1)!.focus();
    buttons.at(-1)!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }));
    expect(document.activeElement).toBe(buttons[0]);
    buttons[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    settle();
    expect(root.querySelector('.delete-group-dialog')).toBeNull();
    expect(document.activeElement).toBe(opener);
    expect(TestBed.inject(DirtyFormRegistry).promptOpen()).toBe(false);
    opener.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    settle();
    expect(TestBed.inject(DirtyFormRegistry).promptOpen()).toBe(true);
  });

  it('delete-group confirmation deletes the whole group and leaves', async () => {
    seedGroup(makeEntryDetail({ id: 7 }), makeEntryDetail({ id: 8 }));
    await open('/accounting/entries/7/edit');
    form.selectBubble('parent');
    settle();
    harness.routeNativeElement!.querySelector<HTMLButtonElement>('.delete-group')!.click();
    settle();
    harness.routeNativeElement!.querySelector<HTMLButtonElement>('.delete-group-confirm')!.click();
    const req = http.expectOne(r => r.method === 'DELETE' && r.url.endsWith('/splits/4'));
    req.flush(null);
    await harness.fixture.whenStable();
    expect(form.deleteGroupPrompt()).toBe(false);
  });

  it('child account picker Escape restores trigger; next Escape prompts the dirty form', async () => {
    await open();
    fill();
    form.addChild();
    settle();
    fill('20');
    const root = harness.routeNativeElement!;
    const trigger = root.querySelector<HTMLButtonElement>('.account-tile .acct-trigger')!;
    trigger.focus();
    trigger.click();
    settle();
    const panel = root.querySelector('.account-tile .acct-panel')!;
    panel.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    settle();
    expect(root.querySelector('.account-tile .acct-panel')).toBeNull();
    expect(document.activeElement).toBe(trigger);
    expect(TestBed.inject(DirtyFormRegistry).promptOpen()).toBe(false);
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    settle();
    expect(TestBed.inject(DirtyFormRegistry).promptOpen()).toBe(true);
  });

  // ---- PR-B contract: unchanged payload, posted-period refusal ---------------------------------------------------

  it('metadata PUT retains online wire mode and exact original quantity/time', async () => {
    seedGroup(
      makeEntryDetail({
        id: 7, amount: '-370.3680', original_amount: '-12.3456', original_currency: 'USD', fx_source: 'fx_api',
        fx_rate: '30', entry_time: '19:00:37.125',
      }),
      makeEntryDetail({ id: 8 }),
    );
    await open('/accounting/entries/7/edit');
    form.name.set('metadata only');
    form.save(false);
    const req = http.expectOne(r => r.method === 'PUT' && r.url.endsWith('/splits/4'));
    expect(req.request.body.members[0]).toMatchObject({
      id: 7, entry_time: '19:00:37.125', amount: null, fx_rate: null, original_amount: '12.3456', original_currency: 'USD',
    });
    req.flush({ code: 'CONFLICT', message: 'retry: member changed before lock', trace_id: 'test' }, { status: 409, statusText: 'Conflict' });
    settle();
    expect(form.isDirty()).toBe(true);
    expect(form.error()).toBe('記錄剛被更新，請重新載入後再試');
    http.expectNone(r => r.method !== 'GET');
  });

  it('hides convert for an import-booked posted period without protecting single editing', async () => {
    entries.set(7, makeEntryDetail({
      id: 7, source: 'moze_backup', locked: false,
      schedule: {
        definition_id: 12, instance_id: 77, kind: 'recurring', seq: 1, times: null,
        name: '每月支出', is_partial: false, acted_by: 'import', posted_entry_ids: [7],
      },
    }));
    await open('/accounting/entries/7/edit');
    expect(form.children()[0].protected).toBe(false);
    expect(form.addState().visible).toBe(false);
    form.addChild();
    expect(form.children()).toHaveLength(1);
  });

  it('keeps convert draft on authoritative posted-period refusal and does not retry', async () => {
    entries.set(7, makeEntryDetail({ id: 7, source: 'manual', schedule: null }));
    await open('/accounting/entries/7/edit');
    form.addChild();
    settle();
    fill('20');
    form.save(false);
    http.expectOne(r => r.method === 'PUT' && r.url.endsWith('/entries/7/split')).flush({
      code: 'CONFLICT', message: 'kind_not_splittable: entry 7 is listed by a posted schedule period', trace_id: 'test',
    }, { status: 409, statusText: 'Conflict' });
    settle();
    expect(form.children()).toHaveLength(2);
    expect(form.isDirty()).toBe(true);
    expect(form.error()).toBe('此記錄類型不能拆帳');
    http.expectNone(r => r.method === 'POST' || r.method === 'PUT');
  });
});
