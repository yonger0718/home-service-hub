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
import { makeAccount, makeAccountDetail, makeCategory, makeEntryDetail, makePreference } from '../testing/fixtures';
import { EntryFormComponent, NO_RELATED, RelatedLoad } from './entry-form';

/** Stands in for the timeline so the form's real `navigateByUrl('/accounting')` (leave()) has somewhere to land. */
@Component({ template: '' })
class AccountingStubComponent {}

const ROUTES: Routes = [
  { path: 'accounting', component: AccountingStubComponent },
  { path: 'accounting/entry', component: EntryFormComponent },
  { path: 'accounting/entries/:id/edit', component: EntryFormComponent },
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
});
