import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Location } from '@angular/common';
import { provideLocationMocks } from '@angular/common/testing';
import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, Routes, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { Observable, Subject } from 'rxjs';
import { MockInstance, afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EntryDetail, Project } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutMode, LayoutModeService } from '../../../services/layout-mode.service';
import { makeAccount, makeAccountDetail, makeCategory, makeEntry, makeEntryDetail, makePreference } from '../testing/fixtures';
import { EntryDetailComponent } from '../entry-detail/entry-detail';
import { EntryFormComponent, NO_RELATED, RelatedLoad } from './entry-form';

/** Stands in for the timeline so the form's real `navigateByUrl('/accounting')` (leave()) has somewhere to land. */
@Component({ template: '' })
class AccountingStubComponent {}

const ROUTES: Routes = [
  { path: 'accounting', component: AccountingStubComponent },
  { path: 'accounting/entry', component: EntryFormComponent },
  { path: 'accounting/entries/:id/edit', component: EntryFormComponent },
  { path: 'accounting/entries/:id', component: AccountingStubComponent },
];

const ACCOUNTS = [
  makeAccount({ id: 1, name: '錢包' }),
  makeAccount({ id: 2, name: '玉山 UNI', is_credit: true, balance: '-27218.0000' }),
];
const PROJECTS: Project[] = [{ id: 5, name: '生活', is_archived: false, sort_order: 0, moze_id: null }];
const LUNCH = makeCategory({ id: 12, parent_id: 1, name: '午餐', default_account_id: 2, default_project_id: 5 });
const FOOD = makeCategory({ id: 1, name: '飲食', icon: '🍜', color: '#f0cd92', children: [LUNCH] });
const SALARY = makeCategory({ id: 30, kind: 'income', name: '薪水', icon: '💰', default_account_id: 2 });
const OLD_CARD = makeAccount({ id: 3, name: '舊卡', is_archived: true });
const YEN_WALLET = makeAccount({ id: 4, name: '日幣現金', currency: 'JPY' });
const USD_CARD = makeAccount({ id: 5, name: '美元卡', currency: 'USD' });
const FX_ACCOUNTS = [...ACCOUNTS, YEN_WALLET, USD_CARD];
const RAMEN = makeCategory({ id: 13, parent_id: 1, name: '拉麵', default_account_id: 4 });
const STEAK = makeCategory({ id: 14, parent_id: 1, name: '牛排', default_account_id: 5 });
const SPLIT_GROUP = { id: 4, kind: 'split' as const, name: null, merchant: null, description: null, count: 2, total: '-300.0000', currency: 'TWD' };
const SPLIT_SEVEN = makeEntryDetail({
  id: 7,
  account_id: 2,
  category_id: 12,
  name: '聚餐',
  amount: '-200.0000',
  group: SPLIT_GROUP,
  group_members: [makeEntry({ id: 7 }), makeEntry({ id: 8 })],
});
const SPLIT_EIGHT = makeEntryDetail({ id: 8, account_id: 2, category_id: 12, name: '代墊', amount: '-100.0000', group: SPLIT_GROUP });

describe('EntryFormComponent', () => {
  let httpMock: HttpTestingController;
  let harness: RouterTestingHarness;
  /** Call-through spy on `Router.navigateByUrl`, installed by `open()`. */
  let navigate: MockInstance<Router['navigateByUrl']> | undefined;

  beforeEach(() => {
    navigate = undefined;
    localStorage.clear();
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 2, 14, 42));
    TestBed.configureTestingModule({
      providers: [provideRouter(ROUTES), provideHttpClient(), provideHttpClientTesting(), provideLocationMocks()],
    });
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(async () => {
    // leave()'s `void router.navigateByUrl('/accounting')` is real now: let it finish (and surface a rejection)
    // before the module is torn down.
    await Promise.all(navigate?.mock.results.map(result => result.value) ?? []);
    httpMock.verify();
    vi.useRealTimers();
    vi.restoreAllMocks();
    // Saves write this device's last use / quick amounts; leave no state for other spec files.
    localStorage.clear();
  });

  /** Holds the `loadRelated` stage open: each call gets a Subject (latest per entry id) the test answers itself. */
  function holdRelated(): Map<number, Subject<RelatedLoad>> {
    const pending = new Map<number, Subject<RelatedLoad>>();
    const proto = EntryFormComponent.prototype as unknown as { loadRelated(detail: EntryDetail): Observable<RelatedLoad> };
    vi.spyOn(proto, 'loadRelated').mockImplementation(detail => {
      const related = new Subject<RelatedLoad>();
      pending.set(detail.id, related);
      return related;
    });
    return pending;
  }

  function release(related: Subject<RelatedLoad> | undefined): void {
    related?.next(NO_RELATED);
    related?.complete();
    settle();
  }

  /** Runs change detection a few times so signal → observable hops (toObservable) settle. */
  function settle(): void {
    for (let i = 0; i < 3; i++) {
      harness.detectChanges();
    }
  }

  /** Flushes every live GET to `url`; requests cancelled by a switchMap (e.g. the first tab's categories) are dropped. */
  function respond(url: string, body: object): void {
    const requests = httpMock.match(r => r.url === url && r.method === 'GET').filter(request => !request.cancelled);
    expect(requests.length, `GET ${url}`).toBeGreaterThan(0);
    requests.forEach(request => request.flush(body));
    settle();
  }

  async function open(url: string, mode: LayoutMode = 'phone', accounts = ACCOUNTS) {
    TestBed.inject(LayoutModeService).set(mode);
    harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    settle();
    const accountList = httpMock.expectOne(r => r.method === 'GET' && r.url === '/api/accounting/accounts');
    expect(accountList.request.params.get('include_archived')).toBe('true');
    accountList.flush(accounts);
    settle();
    respond('/api/accounting/projects', PROJECTS);
    respond('/api/accounting/counterparties', []);
    respond('/api/accounting/preference', makePreference());
    // The spy MUST call through: RouterTestingHarness.navigateByUrl() goes through Router.navigateByUrl() and then waits
    // for the real NavigationEnd, so a stubbed (mockResolvedValue) router would hang every later harness navigation
    // (entry 7 → entry 9, edit → new) and never reach the cancellation path. leave()'s '/accounting' lands on the stub.
    const spy = vi.spyOn(TestBed.inject(Router), 'navigateByUrl');
    navigate = spy;
    // back() alone is stubbed: a real SpyLocation.back() would pop to the previous form URL and start a router
    // navigation (and a fresh GET of that entry) after the test has finished asserting.
    const back = vi.spyOn(TestBed.inject(Location), 'back').mockImplementation(() => undefined);
    const el = harness.routeNativeElement as HTMLElement;
    const left = () => spy.mock.calls.some(([target]) => target === '/accounting') || back.mock.calls.length > 0;
    return { el, left };
  }

  function tap(el: HTMLElement, selector: string, label: string): void {
    const target = Array.from(el.querySelectorAll<HTMLElement>(selector)).find(node => node.textContent?.includes(label));
    if (!target) {
      throw new Error(`no ${selector} with ${label}`);
    }
    target.click();
    settle();
  }

  function keys(el: HTMLElement, ...pressed: string[]): void {
    for (const key of pressed) {
      (el.querySelector(`app-amount-keypad button[data-key="${key}"]`) as HTMLButtonElement).click();
      settle();
    }
  }

  function text(node: Element | null): string {
    return node?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  }

  it('creates an expense in three taps: 飲食, 午餐, 170, ✓', async () => {
    const { el, left } = await open('/accounting/entry');
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));

    tap(el, '.cat', '飲食');
    tap(el, '.cat', '午餐');
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2, name: '玉山 UNI' }));
    keys(el, '1', '7', '0', '✓');

    const req = httpMock.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/entries');
    expect(req.request.body).toMatchObject({
      account_id: 2,
      kind: 'expense',
      amount: '170',
      original_amount: null,
      category_id: 12,
      project_id: 5,
      entry_date: '2026-10-02',
      entry_time: '14:42',
      fee: null,
      discount: null,
      tags: [],
      reward_rule_ids: [],
    });
    req.flush(makeEntryDetail({ id: 99, amount: '-170.0000', account_id: 2, category_id: 12 }));
    settle();

    expect(left()).toBe(true);
    expect(JSON.parse(localStorage.getItem('hh.accounting.lastUse.12')!)).toEqual({ account_id: 2, project_id: 5 });
  });

  it("prefers this device's last use of the category over the server defaults", async () => {
    localStorage.setItem('hh.accounting.lastUse.12', JSON.stringify({ account_id: 1, project_id: null }));
    const { el } = await open('/accounting/entry');
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));

    tap(el, '.cat', '飲食');
    tap(el, '.cat', '午餐');

    expect((el.querySelector('.account-select') as HTMLSelectElement).value).toBe('1');
    expect((el.querySelector('.project-select') as HTMLSelectElement).value).toBe('');
  });

  it('shows the lock banner and read-only fields for a locked imported entry', async () => {
    const { el } = await open('/accounting/entries/9/edit');
    respond(
      '/api/accounting/entries/9',
      makeEntryDetail({
        id: 9,
        source: 'moze_backup',
        moze_id: 'R-9',
        locked: true,
        account_id: 2,
        category_id: 12,
        name: '午餐',
        amount: '-170.0000',
      }),
    );
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));

    expect(text(el.querySelector('app-lock-banner'))).toContain('MOZE 匯入資料，切換後可編輯');
    expect((el.querySelector('fieldset.content') as HTMLFieldSetElement).disabled).toBe(true);
    expect((el.querySelector('button.save') as HTMLButtonElement).disabled).toBe(true);
    expect(el.querySelector('app-amount-keypad')).toBeNull();
    expect((el.querySelector('.name-input') as HTMLInputElement).value).toBe('午餐');
    expect(text(el.querySelector('.amount-value'))).toBe('170');
    expect(text(el.querySelector('.strip .sel b'))).toBe('午餐');
  });

  it('saves with ⇧⏎ and starts a new record keeping kind, account and date', async () => {
    const { el, left } = await open('/accounting/entry?kind=income');
    respond('/api/accounting/categories', [SALARY]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));

    tap(el, '.cat', '薪水');
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    const date = el.querySelector('.date-input') as HTMLInputElement;
    date.value = '2026-09-30';
    date.dispatchEvent(new Event('change'));
    settle();
    keys(el, '5', '0', '0', '0');
    el.querySelector('.entry-form')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true }));

    const req = httpMock.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/entries');
    expect(req.request.body).toMatchObject({ kind: 'income', amount: '5000', account_id: 2, entry_date: '2026-09-30', category_id: 30 });
    req.flush(makeEntryDetail({ id: 77, kind: 'income', amount: '5000.0000' }));
    settle();

    expect(left()).toBe(false);
    expect(el.querySelector('.strip')).toBeNull();
    expect(text(el.querySelector('.grid .cat'))).toContain('薪水');
    expect(text(el.querySelector('.amount-value'))).toBe('0');
    expect(text(el.querySelector('.kind-tab.on'))).toBe('收入');
    expect((el.querySelector('.account-select') as HTMLSelectElement).value).toBe('2');
    expect((el.querySelector('.date-input') as HTMLInputElement).value).toBe('2026-09-30');
    expect(el.querySelector('.saved-flash')).not.toBeNull();
  });

  it('evaluates the amount expression on blur at 1280px and blocks saving when it is invalid', async () => {
    const { el } = await open('/accounting/entry', 'panes');
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    expect(el.querySelector('app-amount-keypad')).toBeNull();
    const input = el.querySelector('.amount-input') as HTMLInputElement;

    input.value = '1200+35';
    input.dispatchEvent(new Event('input'));
    input.dispatchEvent(new Event('blur'));
    settle();
    expect(input.value).toBe('1,235');

    input.value = '1/0';
    input.dispatchEvent(new Event('input'));
    input.dispatchEvent(new Event('blur'));
    settle();
    (el.querySelector('button.save') as HTMLButtonElement).click();
    settle();

    expect(el.querySelector('.tile.amount.invalid')).not.toBeNull();
    expect(text(el.querySelector('.form-error'))).toBe('金額算式有誤');
    httpMock.expectNone(r => r.method === 'POST');
  });

  it('offers the proposed foreign-transaction fee after saving and adds it as a fee child', async () => {
    const { el, left } = await open('/accounting/entry');
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '飲食');
    tap(el, '.cat', '午餐');
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    keys(el, '1', '1', '6', '6', '✓');

    httpMock
      .expectOne(r => r.method === 'POST' && r.url === '/api/accounting/entries')
      .flush(makeEntryDetail({ id: 99, amount: '-1166.0000', proposed_fee: '17.0000' }));
    settle();
    expect(left()).toBe(false);
    expect(text(el.querySelector('.fee-prompt .add-fee'))).toBe('＋ 國外交易手續費 −$17');

    (el.querySelector('.fee-prompt .add-fee') as HTMLButtonElement).click();
    const put = httpMock.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/entries/99');
    expect(put.request.body).toMatchObject({ amount: '1166', fee: { amount: '17.0000', name: '國外交易手續費' } });
    put.flush(makeEntryDetail({ id: 99 }));
    settle();

    expect(left()).toBe(true);
  });

  it('ignores stale detail, related-stage and refetch responses after navigating to another entry', async () => {
    // Request order under real routing:
    //   open(): GET accounts → projects → counterparties → preference (categories 'expense' already pending) →
    //           ready → GET entries/7.
    //   entries/7 answers → loadRelated(7) held open (no account GET: entry 7 is never applied).
    //   harness → /entries/9/edit: same route config, component reused; paramMap emits during activation → load()
    //           switchMaps away from entry 7's related stage → GET entries/9 (issued before NavigationEnd).
    //   entries/9 answers → loadRelated(9) held → released → applied → GET accounts/2.
    //   categories (the open() one) and accounts/2 answered; reload() ×2 → GET entries/9 twice, first cancelled.
    //   save → PUT entries/9 → leave(): second navigation, so navigationId 2 → location.back() (stubbed).
    const related = holdRelated();
    const { el, left } = await open('/accounting/entries/7/edit');
    // Entry 7's detail answers, so its load waits in the related stage when the owner moves on.
    respond('/api/accounting/entries/7', makeEntryDetail({ id: 7, account_id: 1, category_id: 12, name: '午餐', amount: '-120.0000' }));
    const sevenRelated = related.get(7);
    expect(sevenRelated).toBeDefined();
    httpMock.expectNone(r => r.url.startsWith('/api/accounting/accounts/'));

    // Real navigation (the navigateByUrl spy calls through), so this resolves on NavigationEnd.
    await harness.navigateByUrl('/accounting/entries/9/edit');
    settle();
    expect(related.get(9)).toBeUndefined();
    respond(
      '/api/accounting/entries/9',
      makeEntryDetail({ id: 9, account_id: 2, category_id: 12, name: '晚餐', amount: '-250.0000' }),
    );
    expect(related.get(9)).toBeDefined();
    release(related.get(9));
    // Entry 7's related answer arrives last. switchMap has unsubscribed it; if it were still live, the loadId guard drops it.
    release(sevenRelated);
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));

    expect((el.querySelector('.name-input') as HTMLInputElement).value).toBe('晚餐');
    expect(text(el.querySelector('.amount-value'))).toBe('250');
    expect((el.querySelector('.account-select') as HTMLSelectElement).value).toBe('2');

    // Two refetches through load(): the first one's late answer is ignored.
    const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
    form.reload();
    const firstRefetch = httpMock.match(r => r.method === 'GET' && r.url === '/api/accounting/entries/9');
    expect(firstRefetch.length).toBe(1);
    form.reload();
    // The second load() switchMaps away from the first GET before it answers (an already-cancelled request cannot be flushed).
    expect(firstRefetch[0].cancelled).toBe(true);
    settle();
    respond('/api/accounting/entries/9', makeEntryDetail({ id: 9, account_id: 2, category_id: 12, name: '晚餐', amount: '-250.0000' }));
    release(related.get(9));
    firstRefetch.filter(request => !request.cancelled).forEach(request => request.flush(makeEntryDetail({ id: 9, account_id: 2, category_id: 12, name: '宵夜', amount: '-90.0000' })));
    settle();
    expect((el.querySelector('.name-input') as HTMLInputElement).value).toBe('晚餐');

    (el.querySelector('button.save') as HTMLButtonElement).click();
    settle();
    const put = httpMock.expectOne(r => r.method === 'PUT');
    expect(put.request.url).toBe('/api/accounting/entries/9');
    expect(put.request.body).toMatchObject({ account_id: 2, name: '晚餐', amount: '250' });
    put.flush(makeEntryDetail({ id: 9 }));
    settle();

    expect(left()).toBe(true);
  });

  it('offers only 單次 when editing an entry and saves it with PUT, never as a schedule', async () => {
    // Final review F1: /entries/:id/edit (also 編輯這一筆 of a schedule entry) edits that one record.
    const { el, left } = await open('/accounting/entries/9/edit');
    respond('/api/accounting/entries/9', makeEntryDetail({ id: 9, account_id: 2, category_id: 12, amount: '-170.0000' }));
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));

    const tabs = Array.from(el.querySelectorAll('.schedule-tab')).map(node => text(node));
    expect(tabs).not.toContain('週期');
    expect(tabs).not.toContain('分期');
    const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
    form.scheduleDraft.update(draft => ({ ...draft, tab: 'recurring' }));
    form.save(false);
    settle();

    httpMock.expectNone(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions');
    httpMock.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/entries/9').flush(makeEntryDetail({ id: 9 }));
    settle();
    expect(left()).toBe(true);
  });

  it('keeps loading until related entries have loaded', async () => {
    // Request order (no navigation after open()): open() as above → GET entries/9 → answered → loadRelated(9) held;
    // ⏎ and save() issue nothing → release → applied → GET accounts/2 → categories and accounts/2 answered.
    const related = holdRelated();
    const { el } = await open('/accounting/entries/9/edit');
    respond('/api/accounting/entries/9', makeEntryDetail({ id: 9, account_id: 2, category_id: 12, amount: '-170.0000' }));

    const save = el.querySelector('button.save') as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    el.querySelector('.entry-form')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    settle();
    (harness.routeDebugElement!.componentInstance as EntryFormComponent).save(false);
    settle();
    httpMock.expectNone(r => r.method === 'PUT' || r.method === 'POST');

    release(related.get(9));
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));

    expect(save.disabled).toBe(false);
    expect(text(el.querySelector('.amount-value'))).toBe('170');
  });

  it('does not save while the entry is loading', async () => {
    const { el } = await open('/accounting/entries/9/edit');
    const save = el.querySelector('button.save') as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect((el.querySelector('fieldset.content') as HTMLFieldSetElement).disabled).toBe(true);

    el.querySelector('.entry-form')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    settle();
    keys(el, '✓');

    httpMock.expectNone(r => r.method === 'PUT' || r.method === 'POST');
    expect(el.querySelector('.form-error')).toBeNull();

    respond('/api/accounting/entries/9', makeEntryDetail({ id: 9, account_id: 2, category_id: 12, amount: '-170.0000' }));
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));

    expect(save.disabled).toBe(false);
    expect((el.querySelector('fieldset.content') as HTMLFieldSetElement).disabled).toBe(false);
  });

  it('keeps an archived account selectable when editing its entry', async () => {
    const { el } = await open('/accounting/entries/9/edit', 'phone', [...ACCOUNTS, OLD_CARD]);
    respond('/api/accounting/entries/9', makeEntryDetail({ id: 9, account_id: 3, account_name: '舊卡', category_id: 12, amount: '-80.0000' }));
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/3', makeAccountDetail({ id: 3, name: '舊卡', is_archived: true }));

    const select = el.querySelector('.account-select') as HTMLSelectElement;
    expect(Array.from(select.options).map(option => option.textContent?.trim())).toEqual(['錢包', '玉山 UNI', '舊卡（已封存）']);
    expect(select.value).toBe('3');

    // A new record never offers the archived account (the preference is cached, so it is not requested again).
    // Real navigation to a different route config: the edit form is destroyed and a fresh form issues accounts,
    // projects, counterparties and categories; once ready, start(null) picks account 1 → GET accounts/1.
    await harness.navigateByUrl('/accounting/entry');
    settle();
    respond('/api/accounting/accounts', [...ACCOUNTS, OLD_CARD]);
    respond('/api/accounting/projects', PROJECTS);
    respond('/api/accounting/counterparties', []);
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));

    const fresh = harness.routeNativeElement as HTMLElement;
    const options = Array.from((fresh.querySelector('.account-select') as HTMLSelectElement).options);
    expect(options.map(option => option.textContent?.trim())).toEqual(['錢包', '玉山 UNI']);
  });

  it('cancels on Esc unless the Esc was already handled', async () => {
    const { el, left } = await open('/accounting/entry');
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    const form = el.querySelector('.entry-form')!;

    const handled = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    handled.preventDefault();
    form.dispatchEvent(handled);
    settle();
    expect(left()).toBe(false);

    const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    form.dispatchEvent(escape);
    settle();
    expect(escape.defaultPrevented).toBe(true);
    expect(left()).toBe(true);
  });

  it('stays loading until the related stage completes, not on its first value', async () => {
    const related = holdRelated();
    const { el } = await open('/accounting/entries/9/edit');
    respond('/api/accounting/entries/9', makeEntryDetail({ id: 9, account_id: 2, category_id: 12, amount: '-170.0000' }));
    const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;

    related.get(9)!.next(NO_RELATED);
    settle();
    expect(form.loading()).toBe(true);
    expect((el.querySelector('button.save') as HTMLButtonElement).disabled).toBe(true);

    related.get(9)!.complete();
    settle();
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    expect(form.loading()).toBe(false);
    expect(text(el.querySelector('.amount-value'))).toBe('170');
  });

  it('re-reads the preference when it is saved elsewhere', async () => {
    await open('/accounting/entry');
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
    expect(form.preference().keypad_layout).toBe(makePreference().keypad_layout);

    const changed = makePreference({ keypad_layout: makePreference().keypad_layout === 'phone' ? 'calculator' : 'phone' });
    TestBed.inject(AccountingService).updatePreference(changed).subscribe();
    httpMock.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/preference').flush(changed);
    settle();

    expect(form.preference().keypad_layout).toBe(changed.keypad_layout);
  });

  it('does not save on ⏎ while an IME candidate is committed (keyCode 229) or on the 進階 summary', async () => {
    const { el, left } = await open('/accounting/entry');
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '飲食');
    tap(el, '.cat', '午餐');
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    keys(el, '1', '7', '0');

    // Safari: the committing Enter arrives after compositionend with isComposing false and keyCode 229.
    el.querySelector('.name-input')!.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', keyCode: 229, bubbles: true, cancelable: true }),
    );
    settle();
    el.querySelector('.advanced summary')!.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }),
    );
    settle();

    httpMock.expectNone(r => r.method === 'POST');
    expect(left()).toBe(false);
  });

  it('does not save on ⏎ from a <select> (it opens / picks the option)', async () => {
    const { el, left } = await open('/accounting/entry');
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '飲食');
    tap(el, '.cat', '午餐');
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    keys(el, '1', '7', '0');

    const enter = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
    el.querySelector('.project-select')!.dispatchEvent(enter);
    settle();

    expect(enter.defaultPrevented).toBe(false);
    httpMock.expectNone(r => r.method === 'POST');
    expect(left()).toBe(false);
  });

  it('adds no tag on ⏎ during IME composition in the tag input', async () => {
    const { el } = await open('/accounting/entry');
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    const input = el.querySelector('.chip-input') as HTMLInputElement;
    input.value = '#午餐';

    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true, cancelable: true }));
    settle();
    expect(el.querySelector('.chip.tag')).toBeNull();
    expect(input.value).toBe('#午餐');

    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    settle();
    expect(text(el.querySelector('.chip.tag'))).toContain('#午餐');
    httpMock.expectNone(r => r.method === 'POST');
  });

  it('refuses to save an edit whose record failed to load', async () => {
    const { el } = await open('/accounting/entries/9/edit');
    httpMock
      .expectOne(r => r.method === 'GET' && r.url === '/api/accounting/entries/9')
      .flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    settle();
    respond('/api/accounting/categories', [FOOD]);

    expect((el.querySelector('button.save') as HTMLButtonElement).disabled).toBe(true);
    (harness.routeDebugElement!.componentInstance as EntryFormComponent).save(false);
    settle();

    httpMock.expectNone(r => r.method === 'POST' || r.method === 'PUT');
    expect(text(el.querySelector('.form-error'))).toBe('記錄未載入，無法儲存');
  });

  it('does not save a split until its members have loaded', async () => {
    const { el, left } = await open('/accounting/entries/7/edit');
    const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
    const save = el.querySelector('button.save') as HTMLButtonElement;
    respond('/api/accounting/entries/7', SPLIT_SEVEN);

    // The detail is in, its other member (entry 8) is not: the record is still loading.
    expect(save.disabled).toBe(true);
    form.save(false);
    settle();
    httpMock.expectNone(r => r.method === 'PUT' || r.method === 'POST');

    respond('/api/accounting/entries/8', SPLIT_EIGHT);
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));

    expect(save.disabled).toBe(false);
    expect(form.members().length).toBe(1);
    expect(el.querySelectorAll('app-split-lines .member').length).toBe(1);
    save.click();
    settle();
    const put = httpMock.expectOne(r => r.method === 'PUT');
    expect(put.request.url).toBe('/api/accounting/splits/4');
    expect(put.request.body.members.length).toBe(2);
    put.flush({});
    settle();
    expect(left()).toBe(true);
  });

  it('drops member responses that belong to a previous entry', async () => {
    const { el } = await open('/accounting/entries/7/edit');
    const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
    httpMock.expectOne(r => r.method === 'GET' && r.url === '/api/accounting/entries/7').flush(SPLIT_SEVEN);
    settle();
    const memberRequests = httpMock.match(r => r.method === 'GET' && r.url === '/api/accounting/entries/8');
    expect(memberRequests.length).toBe(1);

    await harness.navigateByUrl('/accounting/entries/9/edit');
    settle();
    respond(
      '/api/accounting/entries/9',
      makeEntryDetail({ id: 9, account_id: 2, category_id: 12, name: '晚餐', amount: '-250.0000' }),
    );
    // Entry 7's member answers last. switchMap has unsubscribed it; if it were still live, the loadId guard drops it.
    memberRequests.filter(request => !request.cancelled).forEach(request => request.flush(SPLIT_EIGHT));
    settle();
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));

    expect((el.querySelector('.name-input') as HTMLInputElement).value).toBe('晚餐');
    expect(form.members()).toEqual([]);
    expect(form.groupId()).toBeNull();
    expect(el.querySelector('app-split-lines .member')).toBeNull();

    (el.querySelector('button.save') as HTMLButtonElement).click();
    settle();
    const put = httpMock.expectOne(r => r.method === 'PUT');
    expect(put.request.url).toBe('/api/accounting/entries/9');
    put.flush(makeEntryDetail({ id: 9 }));
    settle();
  });
  /** The transfer panel's own category request carries `kind=transfer_out`; the form asks for the same tree. */
  function respondTransferCategories(): void {
    respond('/api/accounting/categories', [makeCategory({ id: 40, kind: 'transfer_out', name: '轉帳', icon: '⇄' })]);
  }

  function typeInto(el: HTMLElement, selector: string, value: string): void {
    const input = el.querySelector(selector) as HTMLInputElement;
    input.value = value;
    input.dispatchEvent(new Event('input'));
    input.dispatchEvent(new Event('blur'));
    settle();
  }

  it('records a transfer from the 轉帳 tab with the shared name and date tiles', async () => {
    const { el, left } = await open('/accounting/entry?kind=transfer');
    respondTransferCategories();
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));

    expect(el.querySelector('app-category-picker')).toBeNull();
    expect(el.querySelector('.account-select')).toBeNull();
    typeInto(el, 'app-transfer-panel .out-amount', '3000');
    typeInto(el, '.name-input', ' 繳卡費 ');
    expect((el.querySelector('app-transfer-panel .in-amount') as HTMLInputElement).value).toBe('3000');

    (el.querySelector('button.save') as HTMLButtonElement).click();
    settle();
    const req = httpMock.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/transfers');
    expect(req.request.body).toMatchObject({
      from_account_id: 1,
      to_account_id: 2,
      out_amount: '3000',
      in_amount: '3000',
      category_id: 40,
      name: '繳卡費',
      entry_date: '2026-10-02',
      entry_time: '14:42',
      reward_rule_ids: [],
    });
    req.flush({ transfer_group_id: 'g-1', out_entry_id: 1, in_entry_id: 2 });
    settle();
    expect(left()).toBe(true);
  });

  it('saves once on ⏎ in the transfer amount and keeps the form on a panel validation error', async () => {
    const { el } = await open('/accounting/entry?kind=transfer');
    respondTransferCategories();
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));

    const out = el.querySelector('app-transfer-panel .out-amount') as HTMLInputElement;
    out.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    settle();
    httpMock.expectNone(r => r.method === 'POST');
    expect(el.querySelector('.transfer-error')?.textContent).toContain('請輸入轉出金額');
    expect((el.querySelector('button.save') as HTMLButtonElement).disabled).toBe(false);

    out.value = '500';
    out.dispatchEvent(new Event('input'));
    out.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    settle();
    httpMock.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/transfers').flush({});
    settle();
  });

  it('starts the next transfer blank after ⇧⏎ (連續記帳)', async () => {
    const { el, left } = await open('/accounting/entry?kind=transfer');
    respondTransferCategories();
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    typeInto(el, 'app-transfer-panel .out-amount', '800');

    const out = el.querySelector('app-transfer-panel .out-amount') as HTMLInputElement;
    out.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true, cancelable: true }));
    settle();
    httpMock.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/transfers').flush({});
    settle();

    expect(left()).toBe(false);
    expect(text(el.querySelector('.kind-tab.on'))).toBe('轉帳');
    expect((el.querySelector('app-transfer-panel .out-amount') as HTMLInputElement).value).toBe('');
  });

  it('edits both legs of a loaded transfer and saves them with PUT', async () => {
    const { el, left } = await open('/accounting/entries/8/edit');
    const inLeg = makeEntryDetail({
      id: 8,
      kind: 'transfer_in',
      account_id: 2,
      amount: '5000.0000',
      category_id: 40,
      name: '繳卡費',
      transfer_group_id: 'g-7',
      transfer_counterpart: makeEntry({ id: 7, kind: 'transfer_out', account_id: 1 }),
    });
    respond('/api/accounting/entries/8', inLeg);
    respond('/api/accounting/entries/7', { ...inLeg, id: 7, kind: 'transfer_out', account_id: 1, amount: '-5000.0000' });
    respondTransferCategories();

    expect(text(el.querySelector('.kind-tab.on'))).toBe('轉帳');
    const tabs = Array.from(el.querySelectorAll<HTMLButtonElement>('.kind-tab'));
    expect(tabs.filter(tab => tab.disabled).map(tab => text(tab))).toEqual(['支出', '收入', '應收款項', '應付款項', '系統']);
    expect((el.querySelector('app-transfer-panel .from-select') as HTMLSelectElement).value).toBe('1');
    expect((el.querySelector('app-transfer-panel .out-amount') as HTMLInputElement).value).toBe('5000');
    expect((el.querySelector('.name-input') as HTMLInputElement).value).toBe('繳卡費');

    (el.querySelector('button.save') as HTMLButtonElement).click();
    settle();
    const put = httpMock.expectOne(r => r.method === 'PUT');
    expect(put.request.url).toBe('/api/accounting/transfers/g-7');
    expect(put.request.body).toMatchObject({ from_account_id: 1, to_account_id: 2, out_amount: '5000', category_id: 40 });
    put.flush({});
    settle();
    expect(left()).toBe(true);
  });

  it('refuses to save an edited transfer leg whose counterpart is missing', async () => {
    const { el } = await open('/accounting/entries/8/edit');
    respond(
      '/api/accounting/entries/8',
      makeEntryDetail({ id: 8, kind: 'transfer_in', account_id: 2, amount: '5000.0000', transfer_group_id: 'g-7' }),
    );
    respondTransferCategories();

    (el.querySelector('button.save') as HTMLButtonElement).click();
    settle();
    httpMock.expectNone(r => r.method === 'POST' || r.method === 'PUT');
    expect(el.querySelector('.form-error')?.textContent).toContain('找不到轉帳的另一筆');
  });

  it('does not offer split lines when editing a single entry', async () => {
    const { el } = await open('/accounting/entries/9/edit');
    respond('/api/accounting/entries/9', makeEntryDetail({ id: 9, account_id: 2, category_id: 12, amount: '-250.0000' }));
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));

    expect((el.querySelector('app-split-lines .add') as HTMLButtonElement).disabled).toBe(true);
  });
  it('closes the split sheet on Esc from its + button without leaving the form', async () => {
    const { el, left } = await open('/accounting/entry');
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '飲食');
    tap(el, '.cat', '午餐');
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));

    const add = el.querySelector('app-split-lines .add') as HTMLButtonElement;
    add.click();
    settle();
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/counterparties', []);
    expect(el.querySelector('app-split-lines .sheet')).not.toBeNull();

    add.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    settle();
    expect(el.querySelector('app-split-lines .sheet')).toBeNull();
    expect(left()).toBe(false);
    expect(harness.routeNativeElement?.querySelector('.entry-form')).not.toBeNull();
  });

  it('forgets the split group when a reload finds the entry no longer in one', async () => {
    const { el } = await open('/accounting/entries/7/edit');
    const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
    respond('/api/accounting/entries/7', SPLIT_SEVEN);
    respond('/api/accounting/entries/8', SPLIT_EIGHT);
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    expect(form.groupId()).toBe(4);

    form.reload();
    settle();
    respond('/api/accounting/entries/7', { ...SPLIT_SEVEN, group: null, group_members: [] });
    expect(form.groupId()).toBeNull();
    expect(form.members()).toEqual([]);

    (el.querySelector('button.save') as HTMLButtonElement).click();
    settle();
    const put = httpMock.expectOne(r => r.method === 'PUT');
    expect(put.request.url).toBe('/api/accounting/entries/7');
    put.flush(makeEntryDetail({ id: 7 }));
    settle();
  });
  // ---- C1: the FX state follows the account currency ------------------------------------------------------------

  /** Opens the 幣種 sheet on the current account, picks JPY (online rate flushed), types ¥5,390 and `field`. */
  function convertYen(el: HTMLElement, field: '.fx-converted' | '.fx-rate', value: string): void {
    (el.querySelector('button.cur') as HTMLButtonElement).click();
    settle();
    const currency = el.querySelector('.fx-currency') as HTMLSelectElement;
    currency.value = 'JPY';
    currency.dispatchEvent(new Event('change'));
    settle();
    httpMock
      .expectOne(r => r.url === '/api/accounting/fx-rate')
      .flush({ date: '2026-10-02', base: 'JPY', quote: 'TWD', rate: '0.2163000000', source: 'cache' });
    settle();
    for (const [selector, text] of [['.fx-original', '5390'], [field, value]]) {
      const input = el.querySelector(selector) as HTMLInputElement;
      input.value = text;
      input.dispatchEvent(new Event('input'));
      settle();
    }
    (el.querySelector('.fx-confirm') as HTMLButtonElement).click();
    settle();
  }

  function chooseAccount(el: HTMLElement, id: number): void {
    const select = el.querySelector('.account-select') as HTMLSelectElement;
    select.value = String(id);
    select.dispatchEvent(new Event('change'));
    settle();
    respond(`/api/accounting/accounts/${id}`, makeAccountDetail({ id }));
  }

  it('drops a fixed FX conversion (and the fee) when the account moves to the original currency', async () => {
    const { el } = await open('/accounting/entry', 'phone', FX_ACCOUNTS);
    const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '飲食');
    tap(el, '.cat', '午餐');
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    convertYen(el, '.fx-converted', '1166');
    form.fee.set({ amount: '30', name: null });
    settle();

    chooseAccount(el, 4);

    expect(form.fx()).toBeNull();
    expect(text(el.querySelector('.amount-value'))).toBe('5,390');
    expect(text(el.querySelector('.form-notice'))).toBe('已清除手續費與折扣（帳戶幣別變更）');
    keys(el, '✓');
    const req = httpMock.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/entries');
    expect(req.request.body).toMatchObject({
      account_id: 4,
      amount: '5390',
      original_amount: null,
      original_currency: null,
      fx_rate: null,
      fee: null,
      discount: null,
    });
    req.flush(makeEntryDetail({ id: 99 }));
    settle();
  });

  it('resets a manual FX rate to the online rate when the account moves to a third currency', async () => {
    const { el } = await open('/accounting/entry', 'phone', FX_ACCOUNTS);
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '飲食');
    tap(el, '.cat', '午餐');
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    convertYen(el, '.fx-rate', '0.25');

    chooseAccount(el, 5);

    expect(text(el.querySelector('.form-notice'))).toBe('已重設匯率（帳戶幣別變更）');
    expect(text(el.querySelector('button.cur'))).toBe('JPY');
    keys(el, '✓');
    const req = httpMock.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/entries');
    expect(req.request.body).toMatchObject({
      account_id: 5,
      amount: null,
      original_amount: '5390',
      original_currency: 'JPY',
      fx_rate: null,
    });
    req.flush(makeEntryDetail({ id: 99 }));
    settle();
  });

  it('applies the same FX reset when a category pick moves the account', async () => {
    const { el } = await open('/accounting/entry', 'phone', FX_ACCOUNTS);
    const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
    respond('/api/accounting/categories', [{ ...FOOD, children: [LUNCH, STEAK] }]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    convertYen(el, '.fx-converted', '1166');

    tap(el, '.cat', '飲食');
    tap(el, '.cat', '牛排');
    respond('/api/accounting/accounts/5', makeAccountDetail({ id: 5 }));

    expect(form.accountId()).toBe(5);
    expect(text(el.querySelector('.form-notice'))).toBe('已重設匯率（帳戶幣別變更）');
    expect(text(el.querySelector('button.cur'))).toBe('JPY');
    keys(el, '✓');
    const req = httpMock.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/entries');
    expect(req.request.body).toMatchObject({
      account_id: 5,
      category_id: 14,
      amount: null,
      original_amount: '5390',
      original_currency: 'JPY',
      fx_rate: null,
    });
    req.flush(makeEntryDetail({ id: 99 }));
    settle();
  });

  it('drops the conversion when a category pick moves the account to the original currency', async () => {
    const { el } = await open('/accounting/entry', 'phone', FX_ACCOUNTS);
    const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
    respond('/api/accounting/categories', [{ ...FOOD, children: [LUNCH, RAMEN] }]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    convertYen(el, '.fx-converted', '1166');

    tap(el, '.cat', '飲食');
    tap(el, '.cat', '拉麵');
    respond('/api/accounting/accounts/4', makeAccountDetail({ id: 4 }));

    expect(form.accountId()).toBe(4);
    expect(form.fx()).toBeNull();
    keys(el, '✓');
    const req = httpMock.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/entries');
    expect(req.request.body).toMatchObject({
      account_id: 4,
      category_id: 13,
      amount: '5390',
      original_amount: null,
      original_currency: null,
      fx_rate: null,
    });
    req.flush(makeEntryDetail({ id: 99 }));
    settle();
  });
  // ---- C2: editing a split member -------------------------------------------------------------------------------

  it('edits split member 2 keeping the group fields and member order, then lands on its new id', async () => {
    const group = { ...SPLIT_GROUP, name: '聚餐', merchant: '鼎泰豐', description: '週五聚餐' };
    const members = [makeEntry({ id: 7 }), makeEntry({ id: 8 })];
    const shared = { entry_date: '2026-09-30', entry_time: '19:00:00', posted_date: '2026-09-30', group, group_members: members };
    const seven = { ...SPLIT_SEVEN, ...shared, merchant: '鼎泰豐' };
    const eight = makeEntryDetail({
      ...shared,
      id: 8,
      kind: 'receivable',
      account_id: 2,
      category_id: 12,
      name: null,
      amount: '-100.0000',
      counterparty: 'Alan',
      counterparty_id: 5,
      description: '代墊 Alan',
    });
    const { el } = await open('/accounting/entries/8/edit');
    respond('/api/accounting/entries/8', eight);
    respond('/api/accounting/entries/7', seven);
    respond('/api/accounting/categories', [makeCategory({ id: 50, kind: 'receivable', name: '應收款項' })]);
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
    form.counterparties.set([{ id: 5, name: 'Alan', open_amounts: [], moze_id: null } as never]);
    keys(el, '⌫', '⌫', '⌫', '1', '2', '0');

    (el.querySelector('button.save') as HTMLButtonElement).click();
    settle();
    const put = httpMock.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/splits/4');
    expect(put.request.body).toMatchObject({
      name: '聚餐',
      merchant: '鼎泰豐',
      description: '週五聚餐',
      entry_date: '2026-09-30',
      entry_time: '19:00',
    });
    const body = put.request.body.members;
    expect(body.map((line: { amount: string }) => line.amount)).toEqual(['200', '120']);
    expect(body[1]).toMatchObject({ kind: 'receivable', counterparty_id: 5, description: '代墊 Alan', entry_date: '2026-09-30' });
    put.flush({ group_id: 4, member_ids: [21, 22] });
    settle();
    await Promise.all(navigate!.mock.results.map(result => result.value));

    expect(navigate).toHaveBeenCalledWith('/accounting/entries/22', { replaceUrl: true, state: { closeTo: 'list' } });
    expect(TestBed.inject(Router).url).toBe('/accounting/entries/22');
    expect(TestBed.inject(Location).back).not.toHaveBeenCalled();
  });

  it('closes the new split member detail to the timeline, not back to the deleted old id', async () => {
    TestBed.inject(Router).resetConfig(
      ROUTES.map(route => (route.path === 'accounting/entries/:id' ? { ...route, component: EntryDetailComponent } : route)),
    );
    const group = { ...SPLIT_GROUP, name: '聚餐' };
    const members = [makeEntry({ id: 7 }), makeEntry({ id: 8 })];
    const { el } = await open('/accounting/entries/8/edit');
    respond('/api/accounting/entries/8', { ...SPLIT_EIGHT, group, group_members: members });
    respond('/api/accounting/entries/7', { ...SPLIT_SEVEN, group, group_members: members });
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));

    (el.querySelector('button.save') as HTMLButtonElement).click();
    settle();
    httpMock.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/splits/4').flush({ group_id: 4, member_ids: [21, 22] });
    settle();
    await Promise.all(navigate!.mock.results.map(result => result.value));
    expect(TestBed.inject(Router).url).toBe('/accounting/entries/22');
    settle();
    respond('/api/accounting/entries/22', { ...SPLIT_EIGHT, id: 22, group, group_members: [makeEntry({ id: 21 }), makeEntry({ id: 22 })] });
    httpMock.match(r => r.method === 'GET' && r.url === '/api/accounting/accounts').forEach(request => request.flush(ACCOUNTS));
    settle();

    (harness.routeNativeElement!.querySelector('.close') as HTMLButtonElement).click();
    await Promise.all(navigate!.mock.results.map(result => result.value));

    expect(TestBed.inject(Location).back).not.toHaveBeenCalled();
    expect(TestBed.inject(Router).url).toBe('/accounting');
  });

  it("uses the edited line's date for the whole split when the owner changed it", async () => {
    const group = { ...SPLIT_GROUP, name: '聚餐', merchant: null, description: null };
    const members = [makeEntry({ id: 7 }), makeEntry({ id: 8 })];
    const shared = { entry_date: '2026-09-30', entry_time: null, posted_date: '2026-09-30', group, group_members: members };
    const { el } = await open('/accounting/entries/8/edit');
    respond('/api/accounting/entries/8', { ...SPLIT_EIGHT, ...shared });
    respond('/api/accounting/entries/7', { ...SPLIT_SEVEN, ...shared });
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    const date = el.querySelector('.date-input') as HTMLInputElement;
    date.value = '2026-09-29';
    date.dispatchEvent(new Event('change'));
    settle();

    (el.querySelector('button.save') as HTMLButtonElement).click();
    settle();
    const put = httpMock.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/splits/4');
    expect(put.request.body.entry_date).toBe('2026-09-29');
    expect(put.request.body.members.map((line: { entry_date: string }) => line.entry_date)).toEqual(['2026-09-29', '2026-09-29']);
    expect(put.request.body.members.map((line: { amount: string }) => line.amount)).toEqual(['200', '100']);
    put.flush({ group_id: 4, member_ids: [21, 22] });
    settle();
  });
  it('shows the effective rate and 固定 for a fixed converted amount, and 重新換算 goes back online', async () => {
    const { el } = await open('/accounting/entries/9/edit');
    respond(
      '/api/accounting/entries/9',
      makeEntryDetail({
        id: 9,
        account_id: 2,
        category_id: 12,
        amount: '-1166.0000',
        original_amount: '-5390.0000',
        original_currency: 'JPY',
        fx_rate: '0.2163265306',
        fx_source: 'manual',
      }),
    );
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    expect(text(el.querySelector('.foot'))).toBe('¥5,390 × 0.2163 = $1,166');
    expect(el.querySelector('.fx-fixed')).toBeNull();

    keys(el, 'C', '6', '0', '0', '0');
    expect(text(el.querySelector('.foot'))).toBe('¥6,000 × 0.1943 = $1,166 固定重新換算');
    expect(text(el.querySelector('.fx-fixed'))).toBe('固定');

    (el.querySelector('.fx-recompute') as HTMLButtonElement).click();
    settle();
    const rate = httpMock.expectOne(r => r.url === '/api/accounting/fx-rate');
    expect(rate.request.params.get('base')).toBe('JPY');
    rate.flush({ date: '2026-10-02', base: 'JPY', quote: 'TWD', rate: '0.2163000000', source: 'cache' });
    settle();
    expect(text(el.querySelector('.foot'))).toBe('¥6,000 × 0.2163 = $1,298');

    (el.querySelector('button.save') as HTMLButtonElement).click();
    settle();
    const put = httpMock.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/entries/9');
    expect(put.request.body).toMatchObject({ amount: null, original_amount: '6000', original_currency: 'JPY', fx_rate: null });
    put.flush(makeEntryDetail({ id: 9 }));
    settle();
  });
  it('shows fee and discount in the account currency on the chips and in the footer', async () => {
    const { el } = await open('/accounting/entry');
    const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '飲食');
    tap(el, '.cat', '午餐');
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    keys(el, '1', '1', '6', '6');
    form.fee.set({ amount: '30', name: null });
    form.discount.set({ amount: '1000', name: '折價券' });
    settle();

    const chips = Array.from(el.querySelectorAll('.chips .chip:not(.rule-chip)')).map(chip => text(chip));
    expect(chips).toEqual(['手續費 −$30', '折價券 +$1,000']);
    expect(text(el.querySelector('.foot'))).toBe('手續費 −$30 · 折扣 +$1,000 · 總額 −$196');
  });
  it("hides its own ✕ inside the layout's 760–1023 px sheet", async () => {
    const { el } = await open('/accounting/entry', 'sheet');
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    expect(el.querySelector('.topbar .cancel')).toBeNull();
    expect(el.querySelector('.topbar .save')).not.toBeNull();
  });
});
