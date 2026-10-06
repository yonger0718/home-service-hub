import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Location } from '@angular/common';
import { provideLocationMocks } from '@angular/common/testing';
import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, Routes, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { MockInstance, afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Counterparty, Project } from '../../../models/accounting.model';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { AccountingToastService } from '../accounting-toast';
import { FxValue } from '../fx-sheet/fx-sheet';
import { makeAccount, makeAccountDetail, makeCategory, makeDefinition, makePreference } from '../testing/fixtures';
import { DirtyFormRegistry } from '../dirty-form.service';
import { EntryFormComponent } from './entry-form';

@Component({ template: '' })
class AccountingStubComponent {}

const ROUTES: Routes = [
  { path: 'accounting', component: AccountingStubComponent },
  { path: 'accounting/entry', component: EntryFormComponent },
];

const ACCOUNTS = [
  makeAccount({ id: 1, name: '薪轉' }),
  makeAccount({ id: 2, name: '範例卡', is_credit: true }),
  makeAccount({ id: 3, name: '交割' }),
  makeAccount({ id: 4, name: '舊卡', is_archived: true }),
];
const PROJECTS: Project[] = [];
const NETFLIX = makeCategory({ id: 41, parent_id: 40, name: 'Netflix' });
const STREAMING = makeCategory({ id: 40, name: '娛樂', icon: '🎬', children: [NETFLIX] });
const LOANS = makeCategory({ id: 50, kind: 'payable', name: '借款' });
const MOVE = makeCategory({ id: 60, kind: 'transfer_out', name: '轉帳' });
const LENDER: Counterparty = { id: 7, name: '範例銀行', open_amounts: [] };

describe('EntryFormComponent schedules', () => {
  let httpMock: HttpTestingController;
  let harness: RouterTestingHarness;
  let navigate: MockInstance<Router['navigateByUrl']> | undefined;

  beforeEach(() => {
    navigate = undefined;
    localStorage.clear();
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 3, 9, 0));
    TestBed.configureTestingModule({
      providers: [provideRouter(ROUTES), provideHttpClient(), provideHttpClientTesting(), provideLocationMocks()],
    });
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(async () => {
    await Promise.all(navigate?.mock.results.map(result => result.value) ?? []);
    httpMock.verify();
    vi.useRealTimers();
    vi.restoreAllMocks();
    localStorage.clear();
  });

  function settle(): void {
    for (let i = 0; i < 3; i++) {
      harness.detectChanges();
    }
  }

  function flushAll(url: string, body: object): number {
    const requests = httpMock.match(r => r.url === url && r.method === 'GET').filter(request => !request.cancelled);
    requests.forEach(request => request.flush(body));
    settle();
    return requests.length;
  }

  function respond(url: string, body: object): void {
    expect(flushAll(url, body), `GET ${url}`).toBeGreaterThan(0);
  }

  async function open(url: string, counterparties: Counterparty[] = []) {
    TestBed.inject(LayoutModeService).set('phone');
    harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    settle();
    respond('/api/accounting/accounts', ACCOUNTS);
    respond('/api/accounting/projects', PROJECTS);
    respond('/api/accounting/counterparties', counterparties);
    respond('/api/accounting/preference', makePreference());
    const spy = vi.spyOn(TestBed.inject(Router), 'navigateByUrl');
    navigate = spy;
    const back = vi.spyOn(TestBed.inject(Location), 'back').mockImplementation(() => undefined);
    const el = harness.routeNativeElement as HTMLElement;
    const left = () => spy.mock.calls.some(([target]) => target === '/accounting') || back.mock.calls.length > 0;
    return { el, left };
  }

  function text(node: Element | null | undefined): string {
    return node?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  }

  function tap(el: HTMLElement, selector: string, label: string): void {
    const target = Array.from(el.querySelectorAll<HTMLElement>(selector)).find(node => text(node).includes(label));
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

  function set(el: HTMLElement, selector: string, value: string, event = 'input'): void {
    const field = el.querySelector<HTMLInputElement | HTMLSelectElement>(selector)!;
    field.value = value;
    field.dispatchEvent(new Event(event));
    settle();
  }

  async function netflix(start: string | null) {
    const opened = await open('/accounting/entry');
    const { el } = opened;
    respond('/api/accounting/categories', [STREAMING]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '娛樂');
    tap(el, '.cat', 'Netflix');
    (el.querySelector('.account-picker .acct-trigger') as HTMLButtonElement).click();
    settle();
    (el.querySelector('.account-picker .acct-option[data-account-id="2"]') as HTMLElement).click();
    settle();

    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2, name: '範例卡' }));
    keys(el, '3', '9', '0');
    tap(el, '.schedule-tab', '週期');
    if (start) {
      set(el, '.sched-start', start, 'change');
    }
    return opened;
  }


  function pendingClose(): () => void {
    const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
    expect(form.isDirty()).toBe(true);
    const registry = TestBed.inject(DirtyFormRegistry);
    const run = vi.fn(); registry.requestClose(run); settle();
    expect(registry.promptOpen()).toBe(true);
    return () => { expect(registry.promptOpen()).toBe(false); registry.confirmDiscard();
      expect(run).not.toHaveBeenCalled(); expect(form.isDirty()).toBe(false); };
  }

  function definitionRequest() {
    return httpMock.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions');
  }

  function catchUpRequest() {
    return httpMock.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions/9/catch-up');
  }

  it('creates a monthly Netflix definition without an entry or a catch-up', async () => {
    // Spec "Monthly Netflix".
    const { el, left } = await netflix('2026-10-22');
    keys(el, '✓');
    const req = definitionRequest();
    expect(req.request.body).toMatchObject({
      kind: 'recurring', name: 'Netflix', interval_unit: 'month', interval_n: 1, anchor_date: '2026-10-22', times: null,
      end_date: null, posting_mode: 'auto', loan: null,
      template: { lines: [{ kind: 'expense', account_id: 2, amount: '390', currency: 'TWD', category_id: 41 }] },
    });
    const cleared = pendingClose();
    req.flush(makeDefinition({ id: 9 }));
    settle();
    cleared();
    httpMock.expectNone(r => r.url.endsWith('/catch-up'));
    httpMock.expectNone(r => r.method === 'POST' && r.url === '/api/accounting/entries');
    expect(left()).toBe(true);
  });

  it('catches up when the start is today and posting is automatic', async () => {
    const { el, left } = await netflix(null);
    keys(el, '✓');
    const cleared = pendingClose();
    definitionRequest().flush(makeDefinition({ id: 9, anchor_date: '2026-10-03' }));
    settle();
    cleared();
    httpMock
      .expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions/9/catch-up')
      .flush({ posted: [31], failed: null, definition: makeDefinition({ id: 9 }) });
    settle();
    expect(left()).toBe(true);
  });

  it('retries only the catch-up after it failed: exactly one create request', async () => {
    // Multica R-F2: the create succeeded, the catch-up hit 409 import_running; ✓ again must not create a second
    // schedule (or a second payable for a loan) — it retries the catch-up for the kept id.
    const { el, left } = await netflix(null);
    keys(el, '✓');
    definitionRequest().flush(makeDefinition({ id: 9, anchor_date: '2026-10-03' }));
    settle();
    catchUpRequest().flush(
      { code: 'conflict', message: 'import_running' }, { status: 409, statusText: 'Conflict' },
    );
    settle();
    expect(left()).toBe(false);
    expect(TestBed.inject(AccountingToastService).message()).toBe('匯入進行中，請稍後再試');
    expect(text(el.querySelector('.form-error'))).toBe('排程已建立，入帳未完成；按 ✓ 重試入帳');

    keys(el, '✓');
    httpMock.expectNone(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions');
    catchUpRequest().flush({ posted: [31], failed: null, definition: makeDefinition({ id: 9 }) });
    settle();
    expect(left()).toBe(true);
  });

  it('keeps the form disabled after a failed catch-up and retries it after a network error', async () => {
    const { el, left } = await netflix(null);
    keys(el, '✓');
    definitionRequest().flush(makeDefinition({ id: 9, anchor_date: '2026-10-03' }));
    settle();
    catchUpRequest().error(new ProgressEvent('error'));
    settle();
    expect(left()).toBe(false);
    expect(TestBed.inject(AccountingToastService).message()).toBeNull();
    expect(text(el.querySelector('.form-error'))).toBe('排程已建立，入帳未完成；按 ✓ 重試入帳');
    expect((el.querySelector('fieldset.content') as HTMLFieldSetElement).disabled).toBe(true);
    expect((el.querySelector('button.save') as HTMLButtonElement).disabled).toBe(false);

    (el.querySelector('button.save') as HTMLButtonElement).click();
    settle();
    httpMock.expectNone(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions');
    catchUpRequest().flush({ posted: [31], failed: null, definition: makeDefinition({ id: 9 }) });
    settle();
    expect(left()).toBe(true);
  });

  it('surfaces a catch-up answered 200 with a failed period', async () => {
    // Multica R-F2: CatchUpOut.failed is shown, never discarded; the schedule exists, the period waits in 待完成交易.
    const { el, left } = await netflix(null);
    keys(el, '✓');
    definitionRequest().flush(makeDefinition({ id: 9, anchor_date: '2026-10-03' }));
    settle();
    catchUpRequest().flush({
      posted: [], failed: { instance_id: 31, error: 'lines[0].account_id: 帳戶已封存' }, definition: makeDefinition({ id: 9 }),
    });
    settle();
    expect(TestBed.inject(AccountingToastService).message()).toBe('排程已建立；這一期入帳失敗，請到待完成交易處理');
    expect(left()).toBe(true);
    httpMock.expectNone(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions');
  });

  it('leaves a back-dated start to 待完成交易', async () => {
    // Spec "Back-dated start waits for the owner".
    const { el } = await netflix('2026-09-15');
    keys(el, '✓');
    expect(definitionRequest().request.body.anchor_date).toBe('2026-09-15');
    httpMock.expectNone(r => r.url.endsWith('/catch-up'));
  });

  it('hides the fields a schedule cannot carry', async () => {
    // Spec "Unsupported fields hidden".
    const { el } = await open('/accounting/entry');
    respond('/api/accounting/categories', [STREAMING]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '娛樂');
    tap(el, '.cat', 'Netflix');
    expect(el.querySelector('button.cur')).not.toBeNull();
    expect(el.querySelector('.fee-open')).not.toBeNull();
    expect(el.querySelector('.invoice-tile')).not.toBeNull();
    expect(el.querySelector('app-split-lines')).not.toBeNull();

    tap(el, '.schedule-tab', '週期');

    expect(el.querySelector('button.cur')).toBeNull();
    expect(el.querySelector('.fee-open')).toBeNull();
    expect(el.querySelector('.invoice-tile')).toBeNull();
    expect(el.querySelector('app-split-lines')).toBeNull();
    const unsupported = el.querySelector('.tile.wide.schedule-unsupported')!;
    expect(text(unsupported)).toBe('週期／分期不含：手續費、拆帳、外幣、發票');
    expect(unsupported.getAttribute('aria-live')).toBe('polite');
    tap(el, '.event-tab', '單次');
    expect(el.querySelector('.schedule-unsupported')).toBeNull();
  });

  it('keeps the currency pill while a rate is set, so the owner can remove it on 週期', async () => {
    // Otherwise 排程不支援外幣，請先移除匯率 would be a dead end: the pill that removes the rate would be hidden.
    const { el } = await open('/accounting/entry');
    respond('/api/accounting/categories', [STREAMING]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '娛樂');
    tap(el, '.cat', 'Netflix');
    const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
    form.fx.set({ original_currency: 'JPY' } as unknown as FxValue);
    settle();
    tap(el, '.schedule-tab', '週期');
    expect(text(el.querySelector('button.cur'))).toBe('JPY');
    form.fx.set(null);
    settle();
    expect(el.querySelector('button.cur')).toBeNull();
  });

  it('saves a recurring transfer and offers no 分期 for 轉帳', async () => {
    // Spec "Recurring transfer from the form".
    const { el } = await open('/accounting/entry?kind=transfer');
    respond('/api/accounting/categories', [MOVE]);
    flushAll('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    (el.querySelector('.to-picker .acct-trigger') as HTMLButtonElement).click();
    settle();
    (el.querySelector('.to-picker .acct-option[data-account-id="3"]') as HTMLElement).click();
    settle();

    set(el, '.out-amount', '15000');
    expect(Array.from(el.querySelectorAll('.schedule-tab')).map(text)).toEqual(['單次', '週期', '分期']);
    expect(eventTab(el, '分期').getAttribute('aria-disabled')).toBe('true');
    tap(el, '.schedule-tab', '週期');
    expect(el.querySelector('app-transfer-panel .plus')).toBeNull();
    set(el, '.sched-start', '2026-11-05', 'change');
    (el.querySelector('button.save') as HTMLButtonElement).click();
    settle();
    const req = definitionRequest();
    expect(req.request.body.template.lines[0]).toMatchObject({
      kind: 'transfer', account_id: 1, to_account_id: 3, amount: '15000', to_amount: null, category_id: 60,
    });
    expect(req.request.body.anchor_date).toBe('2026-11-05');
  });

  it('creates a new loan with interest in one request', async () => {
    // Spec "New loan with interest".
    const { el } = await open('/accounting/entry?kind=payable', [LENDER]);
    respond('/api/accounting/categories', [LOANS]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '借款');
    set(el, '.name-input', '信貸');
    set(el, '.counterparty-input', '範例銀行');
    keys(el, '3', '0', '0', '0', '0', '0');
    tap(el, '.schedule-tab', '分期');
    set(el, '.sched-periods', '36');
    set(el, '.sched-first', '2026-11-09', 'change');
    expect((el.querySelector('.sched-per') as HTMLInputElement).value).toBe('8,333');
    set(el, '.sched-interest', '620');
    expect((el.querySelector('.sched-repay') as HTMLSelectElement).value).toBe('1');
    keys(el, '✓');
    const req = definitionRequest();
    expect(req.request.body).toMatchObject({
      kind: 'installment', name: '信貸', interval_unit: 'month', anchor_date: '2026-11-09', times: 36,
      total_amount: '300000', posting_mode: 'auto',
      loan: { account_id: 1, counterparty_id: 7, category_id: 50, amount: '300000', entry_date: '2026-10-03', name: '信貸' },
    });
    expect(req.request.body.template.lines.map((line: { kind: string; amount: string }) => [line.kind, line.amount])).toEqual([
      ['repayment', '8333'], ['interest', '620'],
    ]);
    req.flush(makeDefinition({ id: 9, kind: 'installment' }));
    settle();
    httpMock.expectNone(r => r.url.endsWith('/catch-up'));
  });

  it('shows the server field error beside its field', async () => {
    const { el } = await open('/accounting/entry');
    respond('/api/accounting/categories', [STREAMING]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '娛樂');
    tap(el, '.cat', 'Netflix');
    keys(el, '9', '0', '0', '0');
    tap(el, '.schedule-tab', '分期');
    keys(el, '✓');
    definitionRequest().flush(
      { detail: [{ loc: ['body', 'times'], msg: '分期至少 2 期', type: 'value_error' }] },
      { status: 422, statusText: 'Unprocessable Entity' },
    );
    settle();
    expect(text(el.querySelector('.field-error[data-field="times"]'))).toBe('分期至少 2 期');
    expect(text(el.querySelector('.form-error'))).toBe('分期至少 2 期');
  });

  it('edits the whole schedule in definition mode with PUT', async () => {
    const { el, left } = await open('/accounting/entry?schedule=5');
    respond('/api/accounting/schedules/definitions/5', { ...makeDefinition({ id: 5 }), instances: [] });
    respond('/api/accounting/categories', [STREAMING]);
    flushAll('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    expect(text(el.querySelector('.topbar h2'))).toBe('編輯排程');
    expect(text(el.querySelector('.schedule-tab.on'))).toBe('週期');
    expect((el.querySelector('.name-input') as HTMLInputElement).value).toBe('Netflix');
    set(el, '.name-input', 'Netflix 家庭');
    (el.querySelector('button.save') as HTMLButtonElement).click();
    settle();
    const req = httpMock.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/schedules/definitions/5');
    expect(req.request.body).toMatchObject({
      name: 'Netflix 家庭', interval_unit: 'month', anchor_date: '2026-10-22', posting_mode: 'auto',
      template: { lines: [{ kind: 'expense', account_id: 2, amount: '390', category_id: 41 }] },
    });
    expect(req.request.body).not.toHaveProperty('kind');
    expect(req.request.body).not.toHaveProperty('loan');
    const cleared = pendingClose();
    req.flush(makeDefinition({ id: 5, name: 'Netflix 家庭' }));
    settle();
    cleared();
    expect(left()).toBe(true);
  });

  it('keeps an installment cutoff and the interest line metadata on an unchanged PUT (R1)', async () => {
    const line = makeDefinition().template.lines[0];
    const definition = makeDefinition({
      id: 6, kind: 'installment', name: '手機分期', anchor_date: '2026-11-03', times: 3, end_date: '2026-12-03', total_amount: '27000',
      template: {
        lines: [
          { ...line, name: '手機分期', amount: '9000' },
          { ...line, kind: 'interest', amount: '120', category_id: 61, project_id: 7, merchant: '範例銀行', name: '分期利息' },
        ],
        description: null,
        tags: [],
      },
    });
    const { el } = await open('/accounting/entry?schedule=6');
    respond('/api/accounting/schedules/definitions/6', { ...definition, instances: [] });
    respond('/api/accounting/categories', [STREAMING]);
    flushAll('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    (el.querySelector('button.save') as HTMLButtonElement).click();
    settle();
    const req = httpMock.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/schedules/definitions/6');
    expect(req.request.body).toMatchObject({
      end_date: '2026-12-03', times: 3, total_amount: '27000',
      template: {
        lines: [
          { kind: 'expense', account_id: 2, amount: '9000', category_id: 41 },
          { kind: 'interest', account_id: 2, amount: '120', category_id: 61, project_id: 7, merchant: '範例銀行', name: '分期利息' },
        ],
      },
    });
    req.flush(definition);
    settle();
  });

  it('refuses to save when the definition failed to load', async () => {
    const { el } = await open('/accounting/entry?schedule=5');
    httpMock
      .expectOne('/api/accounting/schedules/definitions/5')
      .flush({ detail: 'not_found' }, { status: 404, statusText: 'Not Found' });
    settle();
    flushAll('/api/accounting/categories', [STREAMING]);
    flushAll('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    expect(text(el.querySelector('.form-error'))).toBe('排程讀取失敗，請稍後再試。');
    expect((el.querySelector('button.save') as HTMLButtonElement).disabled).toBe(true);
  });

  it('opens a definition the form cannot represent read-only', async () => {
    const { el, left } = await open('/accounting/entry?schedule=5');
    const line = makeDefinition().template.lines[0];
    respond('/api/accounting/schedules/definitions/5', {
      ...makeDefinition({ id: 5, imported: true, template: { lines: [line, { ...line, amount: '120', category_id: 41 }], description: null, tags: [] } }),
      instances: [],
    });
    flushAll('/api/accounting/categories', [STREAMING]);
    flushAll('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    flushAll('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    expect(text(el.querySelector('.form-error'))).toBe('此排程的格式無法在表單編輯，請改用排程管理');
    expect((el.querySelector('fieldset.content') as HTMLFieldSetElement).disabled).toBe(true);
    const save = el.querySelector('button.save') as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    el.querySelector('.entry-form')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    settle();
    httpMock.expectNone(r => r.method === 'PUT');
    expect(left()).toBe(false);
  });

  it('keeps an archived account of the definition selected', async () => {
    const { el } = await open('/accounting/entry?schedule=5');
    const line = { ...makeDefinition().template.lines[0], account_id: 4 };
    respond('/api/accounting/schedules/definitions/5', {
      ...makeDefinition({ id: 5, template: { lines: [line], description: null, tags: [] } }),
      instances: [],
    });
    flushAll('/api/accounting/categories', [STREAMING]);
    flushAll('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    flushAll('/api/accounting/accounts/4', makeAccountDetail({ id: 4 }));
    const trigger = el.querySelector('.account-picker .acct-trigger')!;
    expect(trigger.getAttribute('data-value')).toBe('4');
    expect(text(trigger)).toContain('舊卡');
  });

  it('shows the lock banner for an imported definition before cutover', async () => {
    const { el } = await open('/accounting/entry?schedule=5');
    respond('/api/accounting/schedules/definitions/5', { ...makeDefinition({ id: 5, imported: true, locked: true }), instances: [] });
    flushAll('/api/accounting/categories', [STREAMING]);
    flushAll('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    flushAll('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    expect(text(el.querySelector('app-lock-banner'))).toContain('MOZE 匯入資料，切換後可編輯');
    expect((el.querySelector('button.save') as HTMLButtonElement).disabled).toBe(true);
  });

  it('starts the next record on 單次 after 連續記帳', async () => {
    const { el, left } = await netflix('2026-10-22');
    el.querySelector('.entry-form')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true }));
    settle();
    definitionRequest().flush(makeDefinition({ id: 9 }));
    settle();
    expect(left()).toBe(false);
    expect(text(el.querySelector('.schedule-tab.on'))).toBe('單次');
    expect(el.querySelector('.saved-flash')).not.toBeNull();
  });
  function eventTab(el: HTMLElement, label: string): HTMLButtonElement {
    return Array.from(el.querySelectorAll<HTMLButtonElement>('.event-type .event-tab')).find(tab => text(tab) === label)!;
  }

  it('renders 事件類型 as a full-width tab list between 時間 and the invoice, without a 進階 fold', async () => {
    const { el } = await open('/accounting/entry');
    respond('/api/accounting/categories', [STREAMING]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    const tile = el.querySelector('.tile.wide.event-type')!;
    const list = tile.querySelector('[role="tablist"]')!;
    expect(list.getAttribute('aria-label')).toBe('事件類型');
    expect(Array.from(list.querySelectorAll('[role="tab"]')).map(text)).toEqual(['單次', '週期', '分期']);
    expect(eventTab(el, '單次').getAttribute('aria-selected')).toBe('true');
    expect(el.querySelector('.advanced')).toBeNull();
    expect(el.querySelector('app-schedule-tabs [role="tablist"]')).toBeNull();
    const tiles = Array.from(el.querySelector('.tiles')!.children);
    expect(tiles.indexOf(tile)).toBeGreaterThan(tiles.indexOf(el.querySelector('.time-input')!.closest('.tile')!));
    expect(tiles.indexOf(tile)).toBeLessThan(tiles.indexOf(el.querySelector('.invoice-tile')!));
  });

  it('keeps 分期 visible but disabled for 轉帳 and ignores a click on it', async () => {
    const { el } = await open('/accounting/entry?kind=transfer');
    respond('/api/accounting/categories', [MOVE]);
    flushAll('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    const installment = eventTab(el, '分期');
    expect(installment.getAttribute('aria-disabled')).toBe('true');
    expect(installment.getAttribute('tabindex')).toBe('-1');
    installment.click();
    settle();
    expect(eventTab(el, '單次').getAttribute('aria-selected')).toBe('true');
  });

  it('moves between enabled tabs with ← / → and never saves on ⏎ there', async () => {
    const { el, left } = await open('/accounting/entry?kind=transfer');
    respond('/api/accounting/categories', [MOVE]);
    flushAll('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    const single = eventTab(el, '單次');
    single.focus();
    single.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
    expect(document.activeElement).toBe(eventTab(el, '週期'));
    eventTab(el, '週期').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
    expect(document.activeElement).toBe(single); // 分期 is skipped
    single.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    settle();
    httpMock.expectNone(r => r.method === 'POST');
    expect(left()).toBe(false);
  });

  it('restores the hidden tiles and clears schedule errors when 分期 goes back to 單次', async () => {
    const { el } = await open('/accounting/entry');
    respond('/api/accounting/categories', [STREAMING]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '娛樂');
    tap(el, '.cat', 'Netflix');
    tap(el, '.event-tab', '分期');
    expect(el.querySelector('.fee-open')).toBeNull();
    expect(el.querySelector('.invoice-tile')).toBeNull();
    const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
    form.fieldErrors.set({ times: '分期至少 2 期' });
    form.error.set('分期至少 2 期');
    settle();
    expect(el.querySelector('.field-error[data-field="times"]')).not.toBeNull();

    tap(el, '.event-tab', '單次');
    expect(el.querySelector('.fee-open')).not.toBeNull();
    expect(el.querySelector('.invoice-tile')).not.toBeNull();
    expect(el.querySelector('.field-error')).toBeNull();
    expect(el.querySelector('.form-error')).toBeNull();
    expect(form.fieldErrors()).toEqual({});
  });

  it('shows the definition kind selected with the other tabs disabled in definition mode', async () => {
    const { el } = await open('/accounting/entry?schedule=5');
    respond('/api/accounting/schedules/definitions/5', { ...makeDefinition({ id: 5 }), instances: [] });
    respond('/api/accounting/categories', [STREAMING]);
    flushAll('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    flushAll('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    expect(eventTab(el, '週期').getAttribute('aria-selected')).toBe('true');
    expect(eventTab(el, '單次').getAttribute('aria-disabled')).toBe('true');
    expect(eventTab(el, '分期').getAttribute('aria-disabled')).toBe('true');
  });

});
