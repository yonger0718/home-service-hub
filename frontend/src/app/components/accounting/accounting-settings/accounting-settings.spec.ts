import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { OwnerRule } from '../inbox/inbox.model';
import { AccountingService } from '../../../services/accounting.service';
import { ToastService } from '../../../services/toast.service';
import { AccountingSettingsComponent } from './accounting-settings';

const PREFERENCE = {
  expense_income_colors: 'red_green', keypad_layout: 'calculator', week_start: 0, main_currency: 'TWD',
  hide_rewards_on_timeline: false, abbreviate_totals: true,
};
const GROUPS = [{ id: 1, name: '現金', sort_order: 0 }, { id: 3, name: '信用卡', sort_order: 1 }];
const EXPENSE_TREE = [
  {
    id: 1, kind: 'expense', parent_id: null, name: '飲食', icon: '🍜', color: '#f0cd92', sort_order: 0, is_hidden: false,
    default_account_id: null, default_project_id: null,
    children: [
      { id: 11, kind: 'expense', parent_id: 1, name: '午餐', icon: '🍜', color: '#f0cd92', sort_order: 0, is_hidden: false, default_account_id: null, default_project_id: null, children: [] },
      { id: 12, kind: 'expense', parent_id: 1, name: '晚餐', icon: '🍜', color: '#f0cd92', sort_order: 1, is_hidden: false, default_account_id: null, default_project_id: null, children: [] },
    ],
  },
  { id: 2, kind: 'expense', parent_id: null, name: '交通', icon: '🚌', color: '#4a90e2', sort_order: 1, is_hidden: false, default_account_id: null, default_project_id: null, children: [] },
];
const INCOME_TREE = [
  { id: 20, kind: 'income', parent_id: null, name: '薪水', icon: null, color: null, sort_order: 0, is_hidden: false, default_account_id: null, default_project_id: null, children: [] },
];
const PROJECTS = [{ id: 4, name: '生活', is_archived: false, sort_order: 0 }, { id: 5, name: '2026 大阪', is_archived: false, sort_order: 1 }];
const COUNTERPARTIES = [
  { id: 5, name: 'Alan', open_amounts: [{ currency: 'TWD', amount: '220.0000' }, { currency: 'JPY', amount: '-1000.0000' }] },
  { id: 6, name: 'Bob', open_amounts: [] },
];

const RULES_URL = '/api/accounting/reconciliation/owner-rules';
const ACCOUNTS = [
  { id: 7, name: '國泰卡', currency: 'TWD' },
  { id: 8, name: '日圓戶', currency: 'JPY' },
];
const RULES: OwnerRule[] = [
  {
    id: 21, account_id: 7, enabled: true, line_kind: 'card', merchant_pattern: '全家', flow_sign: '-', amount: '-85.0000',
    action: 'record', params: { category_id: 2 }, created_from_case_id: 5, created_at: '2026-10-01T00:00:00Z',
    last_applied_at: '2026-10-12T03:00:00Z', applied_count: 3, note: null,
  },
  {
    id: 22, account_id: 8, enabled: false, line_kind: 'card', merchant_pattern: 'Uber', flow_sign: '-', amount: null,
    action: 'dismiss', params: {}, created_from_case_id: null, created_at: '2026-10-01T00:00:00Z',
    last_applied_at: null, applied_count: 0, note: null,
  },
];

const EINVOICE_URL = '/api/accounting/einvoices';
const EINVOICE_PREFS = {
  enabled: true, remind_unmatched: true, date_window_days: 3, mute_prompt_after: 3, last_import_at: '2026-11-06T04:10:00Z',
  auto_linked_count: 42, version: 3,
};
const MUTES = [
  { seller_tax_id: '00000001', seller_name: '測試超市', created_at: '2026-11-01T00:00:00Z' },
  { seller_tax_id: '00000002', seller_name: null, created_at: '2026-11-02T00:00:00Z' },
];
const DISMISSED = [
  {
    id: 71, number: 'AB12345678', issue_date: '2026-11-03', seller_name: '測試書店', seller_tax_id: '00000003', total_amount: '350',
    status_raw: '', state: 'dismissed', dismiss_reason: 'owner', items: [{ name: '筆記本', subtotal: '200' }, { name: '原子筆', subtotal: '150' }],
  },
  {
    id: 72, number: 'CD87654321', issue_date: '2026-11-04', seller_name: '測試咖啡', seller_tax_id: '00000004', total_amount: '12.50',
    status_raw: '', state: 'dismissed', dismiss_reason: 'runbook', items: [],
  },
];
/** What the three 發票 reads answer at page start; a body, 'error' for a 500 or 'notfound' for a 404. Reset after each test. */
let einvoiceInit: { prefs: unknown; mutes: unknown; dismissed: unknown } = { prefs: { ...EINVOICE_PREFS, enabled: false }, mutes: [], dismissed: [] };
const DEFAULT_EINVOICE_INIT = einvoiceInit;
const RECON_SETTINGS_URL = '/api/accounting/settings/reconciliation';
const AUTO_PAY_URL = '/api/accounting/settings/reconciliation/auto-pay';
/** What the reconciliation settings read answers at page start (the 對帳 block); reset after each test. */
let autoPayInit: unknown = { auto_pay: { enabled: false, enabled_from: null } };
const DEFAULT_AUTO_PAY_INIT = autoPayInit;
/** The URL the page opens at (a fragment deep-links a section); reset after each test that sets it. */
let openUrl: string | null = null;

function answer(http: HttpTestingController, url: string, body: unknown): void {
  const req = http.expectOne(r => r.method === 'GET' && r.url === url);
  if (body === 'error') {
    req.flush({ detail: 'x' }, { status: 500, statusText: 'Server Error' });
  } else if (body === 'notfound') {
    req.flush({ detail: 'Not Found' }, { status: 404, statusText: 'Not Found' });
  } else {
    req.flush(body as object);
  }
}

describe('AccountingSettingsComponent', () => {
  let http: HttpTestingController;
  let fixture: ComponentFixture<AccountingSettingsComponent>;
  let el: HTMLElement;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AccountingSettingsComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
    if (openUrl) {
      await TestBed.inject(Router).navigateByUrl(openUrl);
    }
    fixture = TestBed.createComponent(AccountingSettingsComponent);
    fixture.detectChanges();
    http.expectOne('/api/accounting/preference').flush(PREFERENCE);
    http.expectOne('/api/accounting/account-groups').flush(GROUPS);
    // The tab-driven tree and the rules' name map both ask for expense categories.
    const expense = http.match(r => r.url.startsWith('/api/accounting/categories') && r.urlWithParams.includes('kind=expense'));
    expect(expense.length).toBe(2);
    expense.forEach(req => req.flush(EXPENSE_TREE));
    http.expectOne(r => r.url.startsWith('/api/accounting/categories') && r.urlWithParams.includes('kind=income')).flush(INCOME_TREE);
    http.expectOne('/api/accounting/projects').flush(PROJECTS);
    http.expectOne('/api/accounting/counterparties').flush(COUNTERPARTIES);
    http.expectOne('/api/accounting/imports/latest').flush({ detail: 'none' }, { status: 404, statusText: 'Not Found' });
    flushAccountsAndRules();
    answer(http, RECON_SETTINGS_URL, autoPayInit);
    answer(http, `${EINVOICE_URL}/preferences`, einvoiceInit.prefs);
    if ((einvoiceInit.prefs as { enabled?: boolean }).enabled) {
      answer(http, `${EINVOICE_URL}/mutes`, einvoiceInit.mutes);
      answer(http, `${EINVOICE_URL}/dismissed`, einvoiceInit.dismissed);
    }
    fixture.detectChanges();
    el = fixture.nativeElement as HTMLElement;
  });

  afterEach(() => {
    http.verify();
  });

  function flushAccountsAndRules(rules: OwnerRule[] = RULES): void {
    http.expectOne(r => r.method === 'GET' && r.url === '/api/accounting/accounts').flush(ACCOUNTS);
    http.expectOne(RULES_URL).flush(rules);
  }

  function change(selector: string, value: string, root: ParentNode = el): void {
    const field = root.querySelector<HTMLInputElement | HTMLSelectElement>(selector)!;
    field.value = value;
    field.dispatchEvent(new Event('change'));
    fixture.detectChanges();
  }

  function chooseFile(name = 'MOZE_4.0.zip'): File {
    const file = new File(['zip'], name, { type: 'application/zip' });
    const input = el.querySelector<HTMLInputElement>('.backup-file')!;
    Object.defineProperty(input, 'files', { value: [file], configurable: true });
    input.dispatchEvent(new Event('change'));
    fixture.detectChanges();
    return file;
  }

  it('links 回饋規則 › 總覽 to the reward rules overview after 專案', () => {
    const link = el.querySelector<HTMLAnchorElement>('a.reward-rules-link')!;
    expect(link.getAttribute('href')).toBe('/accounting/reward-rules');
    expect(link.textContent?.replace(/\s+/g, '')).toBe('回饋規則總覽');
    expect(link.previousElementSibling?.classList.contains('projects-section')).toBe(true);
  });

  it('renames a counterparty with PUT /counterparties/{id} and shows open amounts per currency', () => {
    const alan = el.querySelector('.cp-row')!;
    expect(alan.querySelector('.cp-open')?.textContent?.trim()).toBe('$220 · −¥1,000');

    change('.cp-name', 'Alan Chen', alan);
    const req = http.expectOne('/api/accounting/counterparties/5');
    expect(req.request.method).toBe('PUT');
    expect(req.request.body).toEqual({ name: 'Alan Chen' });
    req.flush({ id: 5, name: 'Alan Chen', open_amounts: [] });
    http.expectOne('/api/accounting/counterparties').flush([{ ...COUNTERPARTIES[0], name: 'Alan Chen' }, COUNTERPARTIES[1]]);
    fixture.detectChanges();
    expect(el.querySelector<HTMLInputElement>('.cp-name')!.value).toBe('Alan Chen');
  });

  it('runs a dry run, shows the report and only then enables 匯入', () => {
    const real = el.querySelector<HTMLButtonElement>('.real-run')!;
    expect(real.disabled).toBe(true);
    const file = chooseFile();

    el.querySelector<HTMLButtonElement>('.dry-run')!.click();
    const req = http.expectOne(r => r.url.startsWith('/api/accounting/imports/moze-backup'));
    expect(req.request.method).toBe('POST');
    expect(req.request.urlWithParams).toContain('dry_run=true');
    expect((req.request.body as FormData).get('file')).toEqual(file);
    req.flush({
      status: 'dry_run',
      summary: {
        kind: 'moze_backup',
        kind_counts: { expense: 2520, income: 216 },
        skipped_future: { '6': 240 },
        compared_accounts: { compared: 0, total: 60 },
        accounts: [{ name: '玉山 Only', currency: 'TWD', balance: '-4905.0000', moze_part: '-4905.0000', previous_moze_part: '-4800.0000', moze_balance: null, difference: null, compared: false }],
        settings_skipped: [{ name: '玉山 Only', differences: ['closing_day: 15 → kept 20'] }],
        needs_review: { count: 1, reasons: { unpaired_transfer: 1 } },
        fx_outliers: [],
      },
    });
    fixture.detectChanges();

    const report = el.querySelector('.import-report')!.textContent ?? '';
    expect(report).toContain('試算結果');
    expect(report).toContain('支出 2,520');
    expect(report).toContain('未來記錄（略過）');
    expect(report).toContain('應付款項 240');
    expect(report).toContain('已比對 0 / 60 個帳戶');
    expect(report).toContain('玉山 Only');
    expect(report).toContain('−$4,800 → −$4,905');
    expect(report).toContain('待確認 1 筆');
    expect(real.disabled).toBe(false);
  });

  it('imports for real after the dry run and refreshes the latest import', () => {
    chooseFile();
    el.querySelector<HTMLButtonElement>('.dry-run')!.click();
    http.expectOne(r => r.url.startsWith('/api/accounting/imports/moze-backup')).flush({ status: 'dry_run', summary: {} });
    fixture.detectChanges();

    el.querySelector<HTMLButtonElement>('.real-run')!.click();
    const req = http.expectOne(r => r.url.startsWith('/api/accounting/imports/moze-backup'));
    expect(req.request.urlWithParams).not.toContain('dry_run=true');
    req.flush({ status: 'succeeded', summary: { kind_counts: { expense: 2520 } } });
    http.expectOne('/api/accounting/imports/latest').flush({
      id: 9, status: 'succeeded', started_at: '2026-10-02T03:33:00+08:00', finished_at: null, file_name: 'MOZE_4.0.zip',
      file_sha256: 'a'.repeat(64), row_count: 7553, summary: {},
    });
    fixture.detectChanges();

    expect(el.querySelector('.import-report')?.textContent).toContain('匯入完成');
    expect(el.querySelector('.latest-import')?.textContent).toContain('7,553 筆');
  });

  it('saves a display preference with PUT /preference', () => {
    change('.pref-keypad', 'phone');
    const req = http.expectOne('/api/accounting/preference');
    expect(req.request.method).toBe('PUT');
    expect(req.request.body).toEqual({ ...PREFERENCE, keypad_layout: 'phone' });
    req.flush({ ...PREFERENCE, keypad_layout: 'phone' });

    el.querySelector<HTMLButtonElement>('.pref-hide-rewards')!.click();
    const toggle = http.expectOne('/api/accounting/preference');
    expect(toggle.request.body.hide_rewards_on_timeline).toBe(true);
    toggle.flush({ ...PREFERENCE, keypad_layout: 'phone', hide_rewards_on_timeline: true });
  });

  it('keeps both of two quick preference toggles: one PUT at a time, each from the latest state', () => {
    el.querySelector<HTMLButtonElement>('.pref-hide-rewards')!.click();
    fixture.detectChanges();
    change('.pref-colors', 'green_red');
    // Applied at once, before any answer.
    expect(el.querySelector('.pref-hide-rewards')?.getAttribute('aria-checked')).toBe('true');

    const first = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/preference');
    expect(first.request.body).toEqual({ ...PREFERENCE, hide_rewards_on_timeline: true });
    first.flush({ ...PREFERENCE, hide_rewards_on_timeline: true });
    const second = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/preference');
    expect(second.request.body).toEqual({ ...PREFERENCE, hide_rewards_on_timeline: true, expense_income_colors: 'green_red' });
    second.flush({ ...PREFERENCE, hide_rewards_on_timeline: true, expense_income_colors: 'green_red' });
    fixture.detectChanges();

    expect(el.querySelector('.pref-hide-rewards')?.getAttribute('aria-checked')).toBe('true');
    expect(el.querySelector<HTMLSelectElement>('.pref-colors')!.value).toBe('green_red');
  });

  it('lets in-flight and queued preference saves finish after the page is left', () => {
    const service = TestBed.inject(AccountingService);
    const changesBefore = service.preferenceChanged();
    el.querySelector<HTMLButtonElement>('.pref-hide-rewards')!.click();
    fixture.detectChanges();
    change('.pref-colors', 'green_red');

    fixture.destroy();

    const first = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/preference');
    expect(first.cancelled).toBe(false);
    expect(() => first.flush({ ...PREFERENCE, hide_rewards_on_timeline: true })).not.toThrow();
    const second = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/preference');
    expect(second.request.body).toEqual({ ...PREFERENCE, hide_rewards_on_timeline: true, expense_income_colors: 'green_red' });
    expect(() => second.flush({ ...PREFERENCE, hide_rewards_on_timeline: true, expense_income_colors: 'green_red' })).not.toThrow();
    expect(service.preferenceChanged()).toBe(changesBefore + 2);
  });

  it('reverts a preference toggle whose save fails and says why', () => {
    el.querySelector<HTMLButtonElement>('.pref-hide-rewards')!.click();
    fixture.detectChanges();
    http.expectOne(r => r.method === 'PUT').flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    expect(el.querySelector('.pref-hide-rewards')?.getAttribute('aria-checked')).toBe('false');
    expect(el.querySelector('.data-message')).not.toBeNull();
  });

  it('has no 總額縮寫 toggle until a page uses it (2b)', () => {
    expect(el.querySelector('.pref-abbreviate')).toBeNull();
    expect(el.textContent).not.toContain('總額縮寫');
  });

  it('shows the two-level category tree per kind and reorders siblings', () => {
    const names = Array.from(el.querySelectorAll<HTMLInputElement>('.cat-row .cat-name')).map(input => input.value);
    expect(names).toEqual(['飲食', '午餐', '晚餐', '交通']);
    expect(el.querySelectorAll('.cat-row.sub').length).toBe(2);

    el.querySelectorAll<HTMLButtonElement>('.cat-row.sub .move-down')[0].click();
    const req = http.expectOne('/api/accounting/categories/order');
    expect(req.request.method).toBe('PUT');
    expect(req.request.body).toEqual({ ids: [12, 11] });
    req.flush(null);
    http.expectOne(r => r.urlWithParams.includes('kind=expense')).flush(EXPENSE_TREE);

    el.querySelectorAll<HTMLButtonElement>('.kind-tab')[1].click();
    http.expectOne(r => r.url.startsWith('/api/accounting/categories') && r.urlWithParams.includes('kind=income')).flush([]);
  });

  it('hides a category and edits its colour', () => {
    const lunch = el.querySelectorAll('.cat-row')[1];
    (lunch.querySelector<HTMLButtonElement>('.cat-hide'))!.click();
    const hide = http.expectOne('/api/accounting/categories/11');
    expect(hide.request.method).toBe('PUT');
    expect(hide.request.body).toMatchObject({ kind: 'expense', parent_id: 1, name: '午餐', is_hidden: true });
    hide.flush({});
    http.expectOne(r => r.urlWithParams.includes('kind=expense')).flush(EXPENSE_TREE);
    fixture.detectChanges();

    change('.cat-color', '#112233', el.querySelectorAll('.cat-row')[0]);
    const color = http.expectOne('/api/accounting/categories/1');
    expect(color.request.body).toMatchObject({ color: '#112233', parent_id: null });
    color.flush({});
    http.expectOne(r => r.urlWithParams.includes('kind=expense')).flush(EXPENSE_TREE);
  });

  it('reorders account groups and reports a refused delete', () => {
    el.querySelectorAll<HTMLButtonElement>('.group-row .move-up')[1].click();
    const order = http.expectOne('/api/accounting/account-groups/order');
    expect(order.request.body).toEqual({ ids: [3, 1] });
    order.flush(null);
    http.expectOne('/api/accounting/account-groups').flush(GROUPS);
    fixture.detectChanges();

    el.querySelector<HTMLButtonElement>('.group-row .row-delete')!.click();
    fixture.detectChanges();
    el.querySelector<HTMLButtonElement>('.group-row .row-delete')!.click();
    http.expectOne('/api/accounting/account-groups/1').flush({ detail: 'in use' }, { status: 409, statusText: 'Conflict' });
    fixture.detectChanges();
    expect(el.querySelector('.data-message')?.textContent).toContain('仍有帳戶');
  });

  it('renders the schedules block of the import report: counts, warning and one line per item', () => {
    chooseFile();
    el.querySelector<HTMLButtonElement>('.dry-run')!.click();
    const zero = { created: 0, updated: 0, ended: 0, deleted: 0 };
    http.expectOne(r => r.url.startsWith('/api/accounting/imports/moze-backup')).flush({
      status: 'dry_run',
      summary: {
        schedules: {
          definitions: { recurring: { ...zero, created: 11 }, installment: { ...zero, created: 14 }, single: zero },
          records_mapped: 534, rewards_ignored: 63, past_records_already_posted: 2, past_records_already_skipped: 1,
          past_records_amount_differs: {
            count: 1, instance_ids: [77],
            lines: [{ definition_id: 5, name: 'iPhone 分期', seq: 2, date: '2026-09-15', line: 0, kind: 'expense', amount: '1500', moze_amount: '1400' }],
          },
          past_records_owner_pending: [{ definition_id: 7, seq: 4, date: '2026-09-05' }],
          review: [{ moze_id: 'P-1', reason: 'same_date' }],
          amount_differs: [{ definition_id: 7, name: 'Netflix', seq: 3, date: '2026-10-05', line: 0, kind: 'expense', amount: '120', moze_amount: '100' }],
          dependants_suppressed: { count: 1, records: [{ moze_id: 'D-1', parent_moze_id: 'P-9', type: 12 }] },
        },
      },
    });
    fixture.detectChanges();

    const text = (selector: string) => Array.from(el.querySelectorAll(selector)).map(li => li.textContent?.trim());
    const line = el.querySelector('.schedules-report')?.textContent?.replace(/\s+/g, ' ') ?? '';
    expect(line).toContain('週期 11、分期 14、單筆 0；對應 534 筆、略過回饋 63 筆');
    expect(line).toContain('HomeHub 已入帳 2 筆、已略過 1 筆');
    expect(line).toContain('金額不同 1 期');
    expect(line).toContain('待入帳金額與 MOZE 不同 1 期');
    expect(line).toContain('保留待入帳 1 期');
    expect(line).toContain('未匯入附屬記錄 1 筆');
    expect(line).toContain('待檢查 1 筆');
    expect(el.querySelectorAll('.schedules-report .import-error').length).toBe(3);
    expect(text('.schedule-amount-lines li')).toEqual([
      'iPhone 分期 第 2 期 2026-09-15：HomeHub $1500 / MOZE $1400',
      'Netflix 第 3 期 2026-10-05：HomeHub $120 / MOZE $100',
    ]);
    expect(text('.schedule-pending-lines li')).toEqual(['排程 #7 第 4 期 2026-09-05：保留待入帳']);
    expect(text('.schedule-dependant-lines li')).toEqual(['附屬記錄 D-1（屬於 P-9，類型 12）未匯入']);
    expect(text('.schedule-review-lines li')).toEqual(['P-1：同日已有期別']);
    expect(el.querySelector('.import-report')?.textContent).not.toContain('same_date');
  });

  it('shows the static lock state and links 週期／分期 to the reminder centre', () => {
    // Spec "Schedule list moved to 提醒中心".
    expect(el.querySelector('.lock-row .r')?.textContent?.trim()).toBe('未鎖定（切換後鎖定）');
    expect(el.querySelector('.lock-note')?.textContent).toContain('ACCOUNTING_IMPORT_LOCKED');
    expect(el.querySelector('.schedule-row')).toBeNull();
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    (el.querySelector('.schedules-link') as HTMLButtonElement).click();
    expect(navigate).toHaveBeenCalledWith(['/accounting/reminders'], { queryParams: { tab: 'debts' }, fragment: 'schedules' });
    http.expectNone(r => r.url.includes('/imports/schedules'));
  });

  it('adds a counterparty on ⏎ but not on the ⏎ that commits an IME candidate', () => {
    const input = el.querySelector<HTMLInputElement>('.new-counterparty')!;
    input.value = '陳';
    input.dispatchEvent(new Event('input'));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true }));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 229 }));
    http.expectNone('/api/accounting/counterparties');

    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', cancelable: true }));
    const req = http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/counterparties');
    expect(req.request.body).toEqual({ name: '陳' });
    req.flush({ id: 7, name: '陳', open_amounts: [] });
    http.expectOne(r => r.method === 'GET' && r.url === '/api/accounting/counterparties').flush(COUNTERPARTIES);
  });

  it('keeps the latest category tab when an earlier tree arrives late', () => {
    const tabs = el.querySelectorAll<HTMLButtonElement>('.kind-tab');
    tabs[1].click();
    tabs[2].click();
    const income = http.expectOne(r => r.urlWithParams.includes('kind=income'));
    const transfer = http.expectOne(r => r.urlWithParams.includes('kind=transfer_out'));
    transfer.flush([{ ...EXPENSE_TREE[1], id: 30, kind: 'transfer_out', name: '轉帳' }]);
    income.flush([{ ...EXPENSE_TREE[1], id: 20, kind: 'income', name: '薪水' }]);
    fixture.detectChanges();

    const names = Array.from(el.querySelectorAll<HTMLInputElement>('.cat-row .cat-name')).map(input => input.value);
    expect(names).toEqual(['轉帳']);
  });

  it('ignores a dry run result once another file has been chosen', () => {
    chooseFile();
    el.querySelector<HTMLButtonElement>('.dry-run')!.click();
    const req = http.expectOne(r => r.url.startsWith('/api/accounting/imports/moze-backup'));

    chooseFile('MOZE_4.1.zip');
    req.flush({ status: 'dry_run', summary: {} });
    fixture.detectChanges();

    expect(el.querySelector('.import-report')).toBeNull();
    expect(el.querySelector<HTMLButtonElement>('.real-run')!.disabled).toBe(true);
  });

  it('shows the server refusal when an import is locked', () => {
    chooseFile();
    el.querySelector<HTMLButtonElement>('.dry-run')!.click();
    http.expectOne(r => r.url.startsWith('/api/accounting/imports/moze-backup')).flush(
      { code: 'locked', message: '匯入已鎖定', trace_id: 't' },
      { status: 423, statusText: 'Locked' },
    );
    fixture.detectChanges();
    expect(el.querySelector('.import-error')?.textContent).toContain('匯入已鎖定');
    expect(el.querySelector<HTMLButtonElement>('.real-run')!.disabled).toBe(true);
  });

  it('asks for a second tap before deleting, one row at a time', () => {
    const deleteButton = (row: number) => el.querySelectorAll('.cp-row')[row].querySelector<HTMLButtonElement>('.row-delete')!;

    deleteButton(0).click();
    fixture.detectChanges();
    http.expectNone(r => r.method === 'DELETE');
    expect(deleteButton(0).textContent?.trim()).toBe('確定刪除');
    expect(deleteButton(0).classList).toContain('danger');

    deleteButton(1).click();
    fixture.detectChanges();
    http.expectNone(r => r.method === 'DELETE');
    expect(deleteButton(0).textContent?.trim()).not.toBe('確定刪除');
    expect(deleteButton(1).textContent?.trim()).toBe('確定刪除');
    expect(el.querySelectorAll('.row-delete.danger').length).toBe(1);

    el.querySelector<HTMLButtonElement>('.cp-row .row-cancel')!.click();
    fixture.detectChanges();
    expect(el.querySelectorAll('.row-delete.danger').length).toBe(0);

    deleteButton(0).click();
    fixture.detectChanges();
    el.querySelector<HTMLElement>('h1')!.click();
    fixture.detectChanges();
    expect(el.querySelectorAll('.row-delete.danger').length).toBe(0);

    deleteButton(1).click();
    fixture.detectChanges();
    deleteButton(1).click();
    const req = http.expectOne('/api/accounting/counterparties/6');
    expect(req.request.method).toBe('DELETE');
    req.flush(null);
    http.expectOne('/api/accounting/counterparties').flush([COUNTERPARTIES[0]]);
  });
  it('trims renamed category and project names before saving', () => {
    change('.cat-row .cat-name', '  早餐  ');
    const cat = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/categories/1');
    expect(cat.request.body.name).toBe('早餐');
    cat.flush({});
    http.expectOne(r => r.url.startsWith('/api/accounting/categories')).flush(EXPENSE_TREE);

    change('.project-row .project-name', ' 生活費 ');
    const project = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/projects/4');
    expect(project.request.body.name).toBe('生活費');
    project.flush({});
    http.expectOne('/api/accounting/projects').flush(PROJECTS);
  });

  it('swaps two projects with two PUTs in order and re-reads the list', () => {
    el.querySelectorAll<HTMLButtonElement>('.project-row .move-down')[0].click();
    const first = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/projects/4');
    expect(first.request.body).toEqual({ name: '生活', is_archived: false, sort_order: 1 });
    // The second PUT waits for the first.
    http.expectNone(r => r.method === 'PUT' && r.url === '/api/accounting/projects/5');
    first.flush({});
    const second = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/projects/5');
    expect(second.request.body).toEqual({ name: '2026 大阪', is_archived: false, sort_order: 0 });
    second.flush({});
    http.expectOne('/api/accounting/projects').flush([PROJECTS[1], PROJECTS[0]]);
  });

  it('changes nothing when the first PUT of a reorder fails: no revert, no re-read', () => {
    el.querySelectorAll<HTMLButtonElement>('.project-row .move-down')[0].click();
    http
      .expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/projects/4')
      .flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();
    http.expectNone(r => r.method === 'PUT');
    http.expectNone('/api/accounting/projects');
    expect(el.querySelector('.data-message')).not.toBeNull();
    expect(Array.from(el.querySelectorAll<HTMLInputElement>('.project-row .project-name')).map(input => input.value)).toEqual(['生活', '2026 大阪']);
  });

  it('puts the first project back when the second PUT of a reorder is refused, then re-reads the list', () => {
    el.querySelectorAll<HTMLButtonElement>('.project-row .move-down')[0].click();
    http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/projects/4').flush({});
    http
      .expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/projects/5')
      .flush({ detail: [{ loc: ['body', 'sort_order'], msg: 'bad' }] }, { status: 422, statusText: 'Unprocessable Entity' });
    const revert = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/projects/4');
    expect(revert.request.body).toEqual({ name: '生活', is_archived: false, sort_order: 0 });
    revert.flush({});
    http.expectOne('/api/accounting/projects').flush(PROJECTS);
    fixture.detectChanges();
    expect(el.querySelector('.data-message')).not.toBeNull();
    expect(Array.from(el.querySelectorAll<HTMLInputElement>('.project-row .project-name')).map(input => input.value)).toEqual(['生活', '2026 大阪']);
  });

  it('re-reads the projects when even the revert of a failed reorder fails', () => {
    el.querySelectorAll<HTMLButtonElement>('.project-row .move-down')[0].click();
    http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/projects/4').flush({});
    http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/projects/5').error(new ProgressEvent('error'));
    http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/projects/4').error(new ProgressEvent('error'));
    http.expectOne('/api/accounting/projects').flush([{ ...PROJECTS[0], sort_order: 1 }, PROJECTS[1]]);
  });

  it('clears the previous report when a re-run fails, and a failed 試算 no longer allows 匯入', () => {
    chooseFile();
    el.querySelector<HTMLButtonElement>('.dry-run')!.click();
    http.expectOne(r => r.url.startsWith('/api/accounting/imports/moze-backup')).flush({ status: 'dry_run', summary: {} });
    fixture.detectChanges();
    expect(el.querySelector('.import-report')).not.toBeNull();
    expect(el.querySelector<HTMLButtonElement>('.real-run')!.disabled).toBe(false);

    // A failed real run: the dry run's report is gone; 匯入 may be retried.
    el.querySelector<HTMLButtonElement>('.real-run')!.click();
    http.expectOne(r => r.url.startsWith('/api/accounting/imports/moze-backup')).flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();
    expect(el.querySelector('.import-report')).toBeNull();
    expect(el.querySelector('.import-error')).not.toBeNull();
    expect(el.querySelector<HTMLButtonElement>('.real-run')!.disabled).toBe(false);

    // A successful 試算, then a failed re-run of it: no stale report, and 匯入 waits for a good 試算.
    el.querySelector<HTMLButtonElement>('.dry-run')!.click();
    http.expectOne(r => r.url.startsWith('/api/accounting/imports/moze-backup')).flush({ status: 'dry_run', summary: {} });
    fixture.detectChanges();
    expect(el.querySelector('.import-report')).not.toBeNull();
    el.querySelector<HTMLButtonElement>('.dry-run')!.click();
    http.expectOne(r => r.url.startsWith('/api/accounting/imports/moze-backup')).flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();
    expect(el.querySelector('.import-report')).toBeNull();
    expect(el.querySelector<HTMLButtonElement>('.real-run')!.disabled).toBe(true);
  });

  it('puts 取消 after 確定刪除 and labels every add field', () => {
    const row = el.querySelector('.cp-row')!;
    row.querySelector<HTMLButtonElement>('.row-delete')!.click();
    fixture.detectChanges();
    const buttons = Array.from(row.querySelectorAll('button')).map(button => button.textContent?.trim());
    expect(buttons.slice(-2)).toEqual(['確定刪除', '取消']);

    const unlabelled = Array.from(el.querySelectorAll<HTMLInputElement>('.add-row input')).filter(
      input => !input.getAttribute('aria-label'),
    );
    expect(unlabelled.map(input => input.className)).toEqual([]);
  });

  describe('對帳規則', () => {
    it('lists rules with the answer text and applied count', () => {
      const section = el.querySelector('details.rules-section')!;
      expect(section.querySelector('summary')?.textContent).toContain('對帳規則 (2)');
      const rows = Array.from(section.querySelectorAll('.rule-text')).map(node => node.textContent?.trim());
      expect(rows).toEqual([
        '國泰卡 · 「全家」 −$85 · 記為 交通 · 用過 3 次 · 2026/10/12',
        '日圓戶 · 「Uber」 任何金額 · 忽略 · 用過 0 次',
      ]);
    });

    it('names income record rules and keeps names after the category tab changes', () => {
      const income: OwnerRule = { ...RULES[0], id: 23, amount: '5000.0000', params: { category_id: 20 } };
      fixture.componentInstance.setCategoryKind('income');
      http.expectOne(r => r.url.startsWith('/api/accounting/categories') && r.urlWithParams.includes('kind=income')).flush([]);
      fixture.componentInstance.rules.set([RULES[0], income]);
      fixture.detectChanges();
      const rows = Array.from(el.querySelectorAll('.rule-text')).map(node => node.textContent?.trim());
      expect(rows[0]).toContain('記為 交通');
      expect(rows[1]).toBe('國泰卡 · 「全家」 +$5,000 · 記為 薪水 · 用過 3 次 · 2026/10/12');
    });

    it('two-tap delete removes the row', () => {
      const button = () => el.querySelector<HTMLButtonElement>('.rule-row .row-delete')!;
      button().click();
      fixture.detectChanges();
      http.expectNone(r => r.method === 'DELETE');
      expect(button().textContent?.trim()).toBe('確定刪除');
      button().click();
      const req = http.expectOne(r => r.method === 'DELETE' && r.url === `${RULES_URL}/21`);
      req.flush(null);
      fixture.detectChanges();
      http.expectOne(RULES_URL).flush([RULES[1]]);
      fixture.detectChanges();
      expect(el.querySelectorAll('.rule-row').length).toBe(1);
      expect(el.querySelector('details.rules-section summary')?.textContent).toContain('對帳規則 (1)');
    });

    it('toggle patches enabled', () => {
      const toggles = Array.from(el.querySelectorAll<HTMLButtonElement>('.rule-toggle'));
      expect(toggles.map(button => button.textContent?.trim())).toEqual(['停用', '啟用']);
      toggles[1].click();
      const req = http.expectOne(r => r.method === 'PATCH' && r.url === `${RULES_URL}/22`);
      expect(req.request.body).toEqual({ enabled: true });
      req.flush({ ...RULES[1], enabled: true });
      fixture.detectChanges();
      http.expectOne(RULES_URL).flush([RULES[0], { ...RULES[1], enabled: true }]);
      fixture.detectChanges();
      expect(Array.from(el.querySelectorAll('.rule-toggle')).map(button => button.textContent?.trim())).toEqual(['停用', '停用']);
    });
  });
  describe('對帳 › 自動扣繳 (E3b)', () => {
    const toggle = () => el.querySelector<HTMLButtonElement>('.autopay-toggle')!;
    const from = () => el.querySelector<HTMLInputElement>('.autopay-from')!;
    const patch = () => http.expectOne(r => r.method === 'PATCH' && r.url === AUTO_PAY_URL);
    const labels = () => Array.from(el.querySelectorAll('.lbl')).map(node => node.textContent?.trim());

    it('a 對帳 block after 顯示 with the switch off, an empty 起始日 and the note', () => {
      expect(labels().indexOf('對帳')).toBe(labels().indexOf('顯示') + 1);
      expect(toggle().getAttribute('role')).toBe('switch');
      expect(toggle().getAttribute('aria-label')).toBe('自動扣繳');
      expect(toggle().getAttribute('aria-checked')).toBe('false');
      expect(from().value).toBe('');
      expect(el.querySelector('label[for="autopay-from"]')?.textContent?.trim()).toBe('起始日');
      expect(el.querySelector('.autopay-note')?.textContent).toContain('扣款帳戶');
    });

    it('on without a 起始日: no request, the date is asked for; choosing one turns it on', () => {
      toggle().click();
      fixture.detectChanges();
      http.expectNone(r => r.method === 'PATCH');
      expect(toggle().getAttribute('aria-checked')).toBe('true');
      expect(from().getAttribute('aria-invalid')).toBe('true');
      expect(el.querySelector('.autopay-hint')?.textContent?.trim()).toBe('選好起始日才會開啟自動扣繳');
      expect(document.activeElement).toBe(from());

      change('.autopay-from', '2026-12-01');
      const req = patch();
      expect(req.request.body).toEqual({ enabled: true, enabled_from: '2026-12-01' });
      req.flush({ enabled: true, enabled_from: '2026-12-01', version: 6 });
      fixture.detectChanges();
      expect(toggle().getAttribute('aria-checked')).toBe('true');
      expect(from().value).toBe('2026-12-01');
      expect(from().hasAttribute('aria-invalid')).toBe(false);
      expect(el.querySelector('.autopay-hint')).toBeNull();
    });

    it('switching back off before choosing a date sends nothing', () => {
      toggle().click();
      fixture.detectChanges();
      toggle().click();
      fixture.detectChanges();
      http.expectNone(r => r.method === 'PATCH');
      expect(toggle().getAttribute('aria-checked')).toBe('false');
      expect(el.querySelector('.autopay-hint')).toBeNull();
    });

    it('a failed save says so and keeps the stored state', () => {
      change('.autopay-from', '2026-12-01');
      patch().flush({ detail: 'x' }, { status: 500, statusText: 'Server Error' });
      fixture.detectChanges();
      expect(el.querySelector('.autopay-message')?.textContent?.trim()).toBe('自動扣繳設定儲存失敗，請稍後再試');
      expect(from().value).toBe('');
    });

    describe('when on', () => {
      beforeAll(() => {
        autoPayInit = { auto_pay: { enabled: true, enabled_from: '2026-10-01' } };
      });
      afterAll(() => {
        autoPayInit = DEFAULT_AUTO_PAY_INIT;
      });

      it('off sends enabled: false and keeps the date', () => {
        expect(toggle().getAttribute('aria-checked')).toBe('true');
        expect(from().value).toBe('2026-10-01');
        toggle().click();
        const req = patch();
        expect(req.request.body).toEqual({ enabled: false });
        req.flush({ enabled: false, enabled_from: '2026-10-01', version: 7 });
        fixture.detectChanges();
        expect(toggle().getAttribute('aria-checked')).toBe('false');
        expect(from().value).toBe('2026-10-01');
      });

      it('a new date sends only the date; clearing it while on is refused here', () => {
        change('.autopay-from', '2026-11-01');
        const req = patch();
        expect(req.request.body).toEqual({ enabled_from: '2026-11-01' });
        req.flush({ enabled: true, enabled_from: '2026-11-01', version: 7 });
        fixture.detectChanges();
        change('.autopay-from', '2026-11-01');
        http.expectNone(r => r.method === 'PATCH'); // the stored date again: nothing to save
        change('.autopay-from', '');
        http.expectNone(r => r.method === 'PATCH');
        expect(el.querySelector('.autopay-message')?.textContent?.trim()).toBe('自動扣繳開啟時需要起始日');
        expect(from().value).toBe('2026-11-01');
      });
    });

  });

  describe('對帳 read refused', () => {
    beforeAll(() => {
      autoPayInit = 'notfound';
    });
    afterAll(() => {
      autoPayInit = DEFAULT_AUTO_PAY_INIT;
    });
    it('a 404 (reconciliation off): no 對帳 block, no error', () => {
      expect(Array.from(el.querySelectorAll('.lbl')).map(node => node.textContent?.trim())).not.toContain('對帳');
      expect(el.querySelector('.autopay-error')).toBeNull();
    });
  });

  describe('對帳 read failed', () => {
    beforeAll(() => {
      autoPayInit = 'error';
    });
    afterAll(() => {
      autoPayInit = DEFAULT_AUTO_PAY_INIT;
    });
    it('a 500: the block says 自動扣繳設定讀取失敗', () => {
      expect(el.querySelector('.autopay-error')?.textContent?.trim()).toBe('自動扣繳設定讀取失敗');
      expect(el.querySelector('.autopay-toggle')).toBeNull();
    });
  });

  describe('發票', () => {
    beforeAll(() => {
      einvoiceInit = { prefs: EINVOICE_PREFS, mutes: MUTES, dismissed: DISMISSED };
    });
    afterAll(() => {
      einvoiceInit = DEFAULT_EINVOICE_INIT;
    });

    const block = () => el.querySelector('.einvoice-section')!;
    const texts = (selector: string) => Array.from(el.querySelectorAll(selector)).map(node => node.textContent?.replace(/\s+/g, ' ').trim());

    it('shows the block between 顯示 and 匯入 when enabled', () => {
      const labels = Array.from(el.querySelectorAll('.lbl')).map(node => node.textContent?.trim());
      expect(labels.indexOf('發票')).toBe(labels.indexOf('對帳') + 1);
      expect(labels.indexOf('匯入')).toBe(labels.indexOf('發票') + 1);
      expect(block()).not.toBeNull();
    });

    it('switch toggles remind_unmatched', () => {
      const toggle = el.querySelector<HTMLButtonElement>('.pref-remind-unmatched')!;
      expect(toggle.getAttribute('role')).toBe('switch');
      expect(toggle.getAttribute('aria-checked')).toBe('true');
      expect(el.textContent).toContain('提醒沒有帳的發票');
      expect(el.textContent).toContain('關掉後「有發票沒有帳」不再出現；對不太上的發票照樣會問');
      toggle.click();
      const req = http.expectOne(r => r.method === 'PATCH' && r.url === `${EINVOICE_URL}/preferences`);
      expect(req.request.body).toEqual({ remind_unmatched: false });
      req.flush({ ...EINVOICE_PREFS, remind_unmatched: false, version: 4 });
      fixture.detectChanges();
      expect(el.querySelector('.pref-remind-unmatched')!.getAttribute('aria-checked')).toBe('false');
    });

    it('lists muted sellers by name, else by tax id', () => {
      expect(texts('.einvoice-head')).toContain('不提醒的商家 · 2');
      expect(texts('.mute-row .mute-name')).toEqual(['測試超市', '統編 00000002']);
      expect(texts('.mute-row .mute-meta')[0]).toMatch(/^統編 00000001 · 11\/0[12] 起不提醒$/);
      expect(texts('.mute-row .mute-meta')[1]).toMatch(/^11\/0[23] 起不提醒$/);
    });

    it('shows the link status line with the last import and the auto-linked count', () => {
      expect(texts('.einvoice-status')).toEqual(['最近匯入 11/06 · 已自動連結 42 張']);
      expect(el.querySelector('#einvoice-settings')!.textContent).toContain('發票');
    });

    it('steppers show ±N 天 and N 次 with their bounds and hints', () => {
      expect(el.querySelector('.pref-window .val')!.textContent?.trim()).toBe('±3 天');
      expect(el.querySelector('.pref-prompt .val')!.textContent?.trim()).toBe('3 次');
      expect(el.textContent).toContain('發票日期前後幾天內的帳都算候選（0–7）');
      expect(el.textContent).toContain('標「不用記帳」到這個次數，下次就會出現「這家以後都不提醒」（1–10）');
      expect(el.textContent).toContain('同一家標幾次後，建議以後都不提醒');
      expect(el.textContent).not.toContain('同一家標幾次後問要不要不提醒');
    });

    it('widening 對帳日期範圍 from ±3 to ±5 sends date_window_days: 5 (spec scenario), one PATCH in flight', () => {
      const inc = () => el.querySelector<HTMLButtonElement>('.pref-window .inc')!;
      inc().click();
      fixture.detectChanges();
      const first = http.expectOne(r => r.method === 'PATCH' && r.url === `${EINVOICE_URL}/preferences`);
      expect(first.request.body).toEqual({ date_window_days: 4 });
      inc().click();
      fixture.detectChanges();
      expect(el.querySelector('.pref-window .val')!.textContent?.trim()).toBe('±5 天');
      http.expectNone(r => r.method === 'PATCH');
      first.flush({ ...EINVOICE_PREFS, date_window_days: 4, version: 4 });
      const second = http.expectOne(r => r.method === 'PATCH' && r.url === `${EINVOICE_URL}/preferences`);
      expect(second.request.body).toEqual({ date_window_days: 5 });
      second.flush({ ...EINVOICE_PREFS, date_window_days: 5, version: 5 });
      fixture.detectChanges();
      http.expectNone(r => r.method === 'PATCH');
      expect(el.querySelector('.pref-window .val')!.textContent?.trim()).toBe('±5 天');
    });

    it('同一家標幾次 sends mute_prompt_after', () => {
      el.querySelector<HTMLButtonElement>('.pref-prompt .dec')!.click();
      fixture.detectChanges();
      const req = http.expectOne(r => r.method === 'PATCH' && r.url === `${EINVOICE_URL}/preferences`);
      expect(req.request.body).toEqual({ mute_prompt_after: 2 });
      req.flush({ ...EINVOICE_PREFS, mute_prompt_after: 2, version: 4 });
      fixture.detectChanges();
      expect(el.querySelector('.pref-prompt .val')!.textContent?.trim()).toBe('2 次');
    });

    it('answers arriving out of order across fields keep each field\'s own saved value', () => {
      el.querySelector<HTMLButtonElement>('.pref-window .inc')!.click();
      fixture.detectChanges();
      el.querySelector<HTMLButtonElement>('.pref-prompt .dec')!.click();
      fixture.detectChanges();
      const [window, prompt] = http.match(r => r.method === 'PATCH' && r.url === `${EINVOICE_URL}/preferences`);
      expect(window.request.body).toEqual({ date_window_days: 4 });
      expect(prompt.request.body).toEqual({ mute_prompt_after: 2 });
      prompt.flush({ ...EINVOICE_PREFS, date_window_days: 4, mute_prompt_after: 2, version: 5 });
      window.flush({ ...EINVOICE_PREFS, date_window_days: 4, mute_prompt_after: 3, version: 4 }); // older snapshot, later
      fixture.detectChanges();
      http.expectNone(r => r.method === 'PATCH');
      expect(el.querySelector('.pref-window .val')!.textContent?.trim()).toBe('±4 天');
      expect(el.querySelector('.pref-prompt .val')!.textContent?.trim()).toBe('2 次');
    });

    it('an older answer arriving last does not roll the import status back', () => {
      el.querySelector<HTMLButtonElement>('.pref-window .inc')!.click();
      fixture.detectChanges();
      el.querySelector<HTMLButtonElement>('.pref-prompt .dec')!.click();
      fixture.detectChanges();
      const [window, prompt] = http.match(r => r.method === 'PATCH' && r.url === `${EINVOICE_URL}/preferences`);
      prompt.flush({ ...EINVOICE_PREFS, mute_prompt_after: 2, last_import_at: '2026-11-07T04:10:00Z', auto_linked_count: 50, version: 5 });
      window.flush({ ...EINVOICE_PREFS, date_window_days: 4, version: 4 });
      fixture.detectChanges();
      expect(el.querySelector('.einvoice-status')!.textContent?.trim()).toBe('最近匯入 11/07 · 已自動連結 50 張');
      expect(el.querySelector('.pref-window .val')!.textContent?.trim()).toBe('±4 天');
    });

    it('one field failing reverts that field only; the other keeps its saved value', () => {
      el.querySelector<HTMLButtonElement>('.pref-window .inc')!.click();
      fixture.detectChanges();
      el.querySelector<HTMLButtonElement>('.pref-prompt .dec')!.click();
      fixture.detectChanges();
      const [window, prompt] = http.match(r => r.method === 'PATCH' && r.url === `${EINVOICE_URL}/preferences`);
      prompt.flush({ ...EINVOICE_PREFS, mute_prompt_after: 2, version: 4 });
      window.flush({ detail: 'x' }, { status: 500, statusText: 'err' });
      fixture.detectChanges();
      expect(el.querySelector('.pref-window .val')!.textContent?.trim()).toBe('±3 天');
      expect(el.querySelector('.pref-prompt .val')!.textContent?.trim()).toBe('2 次');
      expect(texts('.pref-failed .pref-failed-text')).toEqual(['沒存到，已改回 ±3 天']);
    });

    it('a failed PATCH reverts the stepper and says so right under that row, with 重試', () => {
      el.querySelector<HTMLButtonElement>('.pref-window .inc')!.click();
      fixture.detectChanges();
      http.expectOne(r => r.method === 'PATCH').flush({ detail: 'x' }, { status: 500, statusText: 'err' });
      fixture.detectChanges();
      expect(el.querySelector('.pref-window .val')!.textContent?.trim()).toBe('±3 天');
      const failed = el.querySelector('.pref-failed')!;
      expect(failed.previousElementSibling?.querySelector('.pref-window')).not.toBeNull();
      expect(failed.querySelector('.pref-failed-text')!.textContent?.trim()).toBe('沒存到，已改回 ±3 天');
      expect(failed.getAttribute('role')).toBe('alert');
      failed.querySelector<HTMLButtonElement>('.pref-retry')!.click();
      fixture.detectChanges();
      const again = http.expectOne(r => r.method === 'PATCH');
      expect(again.request.body).toEqual({ date_window_days: 4 });
      expect(el.querySelector('.pref-failed')).toBeNull();
      again.flush({ ...EINVOICE_PREFS, date_window_days: 4, version: 4 });
      fixture.detectChanges();
      expect(el.querySelector('.pref-window .val')!.textContent?.trim()).toBe('±4 天');
    });

    it('a failed 同一家標幾次 says 已改回 N 次 under its own row', () => {
      el.querySelector<HTMLButtonElement>('.pref-prompt .dec')!.click();
      fixture.detectChanges();
      http.expectOne(r => r.method === 'PATCH').flush({ detail: 'x' }, { status: 500, statusText: 'err' });
      fixture.detectChanges();
      const failed = el.querySelector('.pref-failed')!;
      expect(failed.previousElementSibling?.querySelector('.pref-prompt')).not.toBeNull();
      expect(failed.textContent).toContain('沒存到，已改回 3 次');
    });

    it('恢復提醒 in one tap, says so with 復原; 復原 mutes the seller again', () => {
      const toast = () => TestBed.inject(ToastService).current();
      const button = el.querySelector<HTMLButtonElement>('.mute-row .mute-unmute')!;
      expect(button.textContent?.trim()).toBe('恢復提醒');
      expect(el.querySelector('.mute-row .row-delete')).toBeNull();
      button.click();
      http.expectOne(r => r.method === 'DELETE' && r.url === `${EINVOICE_URL}/mutes/00000001`).flush(null, { status: 204, statusText: 'No Content' });
      answer(http, `${EINVOICE_URL}/mutes`, [MUTES[1]]);
      fixture.detectChanges();
      expect(texts('.mute-row .mute-name')).toEqual(['統編 00000002']);
      expect(toast()).toMatchObject({ kind: 'done', text: '已恢復提醒：測試超市', action: { label: '復原' } });
      toast()!.action!.run();
      http.expectOne(r => r.method === 'POST' && r.url === `${EINVOICE_URL}/mutes/00000001`).flush(null, { status: 204, statusText: 'No Content' });
      answer(http, `${EINVOICE_URL}/mutes`, MUTES);
      fixture.detectChanges();
      expect(texts('.mute-row .mute-name')).toEqual(['測試超市', '統編 00000002']);
      expect(toast()).toMatchObject({ kind: 'done', text: '已復原：測試超市 不提醒' });
    });

    it('a failed 復原 of 恢復提醒 says why', () => {
      const toast = () => TestBed.inject(ToastService).current();
      el.querySelector<HTMLButtonElement>('.mute-row .mute-unmute')!.click();
      http.expectOne(r => r.method === 'DELETE').flush(null, { status: 204, statusText: 'No Content' });
      answer(http, `${EINVOICE_URL}/mutes`, [MUTES[1]]);
      toast()!.action!.run();
      http.expectOne(r => r.method === 'POST' && r.url === `${EINVOICE_URL}/mutes/00000001`).flush({ detail: 'x' }, { status: 500, statusText: 'err' });
      expect(toast()?.kind).toBe('error');
      expect(toast()?.text).toMatch(/^復原沒成功/);
    });

    it('lists dismissed invoices and restores one', () => {
      expect(texts('.einvoice-head')).toContain('已標成不用記帳 · 2');
      expect(texts('.dismissed-row .dismissed-name')).toEqual(['測試書店 · $350', '測試咖啡 · 12.50']);
      expect(texts('.dismissed-row .dismissed-meta')).toEqual(['11/03 · 筆記本、原子筆 · 你標的', '11/04 · 規則自動標記']);
      expect(el.querySelector('.dismissed-all')).toBeNull();
      const restore = el.querySelector<HTMLButtonElement>('.dismissed-row .restore')!;
      expect(restore.textContent?.trim()).toBe('放回');
      restore.click();
      http.expectOne(r => r.method === 'POST' && r.url === `${EINVOICE_URL}/71/undismiss`).flush({ id: 71, state: 'unmatched', dismiss_reason: null, linked_by: null });
      answer(http, `${EINVOICE_URL}/dismissed`, [DISMISSED[1]]);
      fixture.detectChanges();
      expect(el.querySelectorAll('.dismissed-row').length).toBe(1);
      expect(el.textContent).not.toContain('測試書店');
      const toast = () => TestBed.inject(ToastService).current();
      expect(toast()).toMatchObject({ kind: 'done', text: '已放回：測試書店 $350', action: { label: '復原' } });
      toast()!.action!.run();
      const again = http.expectOne(r => r.method === 'POST' && r.url === `${EINVOICE_URL}/71/dismiss`);
      expect(again.request.body).toEqual({ mute_seller: false });
      again.flush({ id: 71, state: 'dismissed', dismiss_reason: 'owner', linked_by: null });
      answer(http, `${EINVOICE_URL}/dismissed`, DISMISSED);
      fixture.detectChanges();
      expect(el.querySelectorAll('.dismissed-row').length).toBe(2);
      expect(toast()).toMatchObject({ kind: 'done', text: '已復原：測試書店 $350' });
    });

    it('shows the server reason when 復原 is refused', () => {
      el.querySelector<HTMLButtonElement>('.dismissed-row .restore')!.click();
      http.expectOne(r => r.url === `${EINVOICE_URL}/71/undismiss`).flush({ detail: 'seller_muted' }, { status: 409, statusText: 'Conflict' });
      fixture.detectChanges();
      expect(el.querySelector('.einvoice-message')!.textContent).toContain('沒成功：這家已設為不提醒');
      expect(el.querySelectorAll('.dismissed-row').length).toBe(2);
    });
  });

  describe('發票 with many dismissed, at the stepper bounds, never imported', () => {
    const many = Array.from({ length: 5 }, (_, i) => ({
      ...DISMISSED[0], id: 80 + i, seller_name: `測試店${i + 1}`, issue_date: `2026-11-0${9 - i}`,
    }));
    beforeAll(() => {
      einvoiceInit = {
        prefs: { ...EINVOICE_PREFS, date_window_days: 7, mute_prompt_after: 1, last_import_at: null, auto_linked_count: 0 },
        mutes: [], dismissed: many,
      };
    });
    afterAll(() => {
      einvoiceInit = DEFAULT_EINVOICE_INIT;
    });
    const names = () => Array.from(el.querySelectorAll('.dismissed-row .dismissed-name')).map(n => n.textContent?.trim());

    it('shows the newest three, then 看全部 N 張 expands in place', () => {
      expect(names()).toEqual(['測試店1 · $350', '測試店2 · $350', '測試店3 · $350']);
      const all = el.querySelector<HTMLButtonElement>('.dismissed-all')!;
      expect(all.textContent?.replace(/\s+/g, ' ').trim()).toBe('看全部 5 張 ›');
      all.click();
      fixture.detectChanges();
      expect(names().length).toBe(5);
      expect(el.querySelector('.dismissed-all')).toBeNull();
      expect(el.querySelector('.dismissed-cap')).toBeNull();
      const fold = el.querySelector<HTMLButtonElement>('.dismissed-fold')!;
      expect(fold.textContent?.trim()).toBe('收起');
      fold.click();
      fixture.detectChanges();
      expect(names().length).toBe(3);
      expect(el.querySelector('.dismissed-fold')).toBeNull();
    });

    it('disables + at the top bound and − at the bottom bound', () => {
      expect(el.querySelector<HTMLButtonElement>('.pref-window .inc')!.disabled).toBe(true);
      expect(el.querySelector<HTMLButtonElement>('.pref-prompt .dec')!.disabled).toBe(true);
    });

    it('says 還沒有匯入 before the first import', () => {
      expect(el.querySelector('.einvoice-status')!.textContent?.trim()).toBe('還沒有匯入');
    });
  });

  describe('發票 with 100 dismissed (the API cap)', () => {
    beforeAll(() => {
      einvoiceInit = {
        prefs: EINVOICE_PREFS, mutes: [],
        dismissed: Array.from({ length: 100 }, (_, i) => ({ ...DISMISSED[0], id: 1000 + i, seller_name: `測試店${i + 1}` })),
      };
    });
    afterAll(() => {
      einvoiceInit = DEFAULT_EINVOICE_INIT;
    });

    it('看全部 lists them and says 只列最近 100 張', () => {
      el.querySelector<HTMLButtonElement>('.dismissed-all')!.click();
      fixture.detectChanges();
      expect(el.querySelectorAll('.dismissed-row').length).toBe(100);
      expect(el.querySelector('.dismissed-cap')!.textContent?.trim()).toBe('只列最近 100 張');
    });
  });

  describe('發票 deep link (#einvoice-settings)', () => {
    const scroll = vi.fn();
    let original: typeof Element.prototype.scrollIntoView;
    beforeAll(() => {
      einvoiceInit = { prefs: EINVOICE_PREFS, mutes: [], dismissed: [] };
      openUrl = '/#einvoice-settings';
      original = Element.prototype.scrollIntoView;
      Element.prototype.scrollIntoView = scroll;
    });
    afterAll(() => {
      einvoiceInit = DEFAULT_EINVOICE_INIT;
      openUrl = null;
      Element.prototype.scrollIntoView = original;
    });

    it('scrolls the 發票 section into view once it renders', () => {
      const target = scroll.mock.contexts.find(ctx => (ctx as Element).id === 'einvoice-settings');
      expect(target).toBeTruthy();
    });
  });

  describe('發票 without data', () => {
    beforeAll(() => {
      einvoiceInit = { prefs: EINVOICE_PREFS, mutes: [], dismissed: [] };
    });
    afterAll(() => {
      einvoiceInit = DEFAULT_EINVOICE_INIT;
    });

    it('says so for an empty dismissed list', () => {
      expect(el.querySelector('.dismissed-empty')!.textContent).toContain('還沒有標成不用記帳的發票');
      expect(el.querySelectorAll('.dismissed-row').length).toBe(0);
    });
  });

  describe('發票 loads failing', () => {
    beforeAll(() => {
      einvoiceInit = { prefs: EINVOICE_PREFS, mutes: 'error', dismissed: [] };
    });
    afterAll(() => {
      einvoiceInit = DEFAULT_EINVOICE_INIT;
    });

    it('shows 發票設定讀取失敗 for the failed list only', () => {
      expect(el.querySelector('.mutes-error')!.textContent).toContain('發票設定讀取失敗');
      expect(el.querySelector('.dismissed-error')).toBeNull();
      expect(el.querySelector('.dismissed-empty')).not.toBeNull();
    });
  });

  describe('發票 preferences failing', () => {
    beforeAll(() => {
      einvoiceInit = { prefs: 'error', mutes: [], dismissed: [] };
    });
    afterAll(() => {
      einvoiceInit = DEFAULT_EINVOICE_INIT;
    });

    it('shows 發票設定讀取失敗 and no lists', () => {
      expect(el.querySelector('.einvoice-error')!.textContent).toContain('發票設定讀取失敗');
      expect(el.querySelector('.pref-remind-unmatched')).toBeNull();
    });
  });

  describe('發票 preferences 404 (reconciliation flag off)', () => {
    beforeAll(() => {
      einvoiceInit = { prefs: 'notfound', mutes: [], dismissed: [] };
    });
    afterAll(() => {
      einvoiceInit = DEFAULT_EINVOICE_INIT;
    });

    it('treats it as disabled: no block, no 發票設定讀取失敗', () => {
      expect(el.querySelector('.einvoice-error')).toBeNull();
      expect(el.querySelector('.einvoice-section')).toBeNull();
      expect(Array.from(el.querySelectorAll('.lbl')).some(node => node.textContent?.trim() === '發票')).toBe(false);
      expect(el.textContent).not.toContain('發票設定讀取失敗');
    });
  });

  it('hides the 發票 block while disabled', () => {
    expect(el.querySelector('.einvoice-section')).toBeNull();
    expect(Array.from(el.querySelectorAll('.lbl')).some(node => node.textContent?.trim() === '發票')).toBe(false);
  });
});

describe('AccountingSettingsComponent 專案 › 不計入報表', () => {
  it('flips exclude_from_reports with a PUT that keeps the other fields', async () => {
    await TestBed.configureTestingModule({
      imports: [AccountingSettingsComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    }).compileComponents();
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(AccountingSettingsComponent);
    fixture.detectChanges();
    http.expectOne('/api/accounting/preference').flush(PREFERENCE);
    http.expectOne('/api/accounting/account-groups').flush(GROUPS);
    for (const req of http.match(r => r.url === '/api/accounting/categories')) req.flush(req.request.params.get('kind') === 'income' ? INCOME_TREE : EXPENSE_TREE);
    http.expectOne('/api/accounting/projects').flush([{ ...PROJECTS[0], exclude_from_reports: false }, { ...PROJECTS[1], exclude_from_reports: true }]);
    http.expectOne('/api/accounting/counterparties').flush(COUNTERPARTIES);
    for (const req of http.match(() => true)) req.flush([]);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    const switches = Array.from(el.querySelectorAll<HTMLButtonElement>('.project-row .project-exclude button'));
    expect(switches.map(s => s.getAttribute('aria-checked'))).toEqual(['false', 'true']);
    expect(switches[0].getAttribute('aria-label')).toBe('生活 不計入報表');
    switches[0].click();
    const put = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/projects/4');
    expect(put.request.body).toEqual({ name: '生活', is_archived: false, sort_order: 0, exclude_from_reports: true });
    put.flush({});
    http.expectOne('/api/accounting/projects').flush([]);
  });

  it('keeps exclude_from_reports on both reorder PUTs and on the rollback of a failed reorder', async () => {
    await TestBed.configureTestingModule({
      imports: [AccountingSettingsComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    }).compileComponents();
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(AccountingSettingsComponent);
    fixture.detectChanges();
    http.expectOne('/api/accounting/preference').flush(PREFERENCE);
    http.expectOne('/api/accounting/account-groups').flush(GROUPS);
    for (const req of http.match(r => r.url === '/api/accounting/categories')) req.flush(req.request.params.get('kind') === 'income' ? INCOME_TREE : EXPENSE_TREE);
    http.expectOne('/api/accounting/projects').flush([{ ...PROJECTS[0], exclude_from_reports: true }, { ...PROJECTS[1], exclude_from_reports: false }]);
    http.expectOne('/api/accounting/counterparties').flush(COUNTERPARTIES);
    for (const req of http.match(() => true)) req.flush([]);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelectorAll<HTMLButtonElement>('.project-row .move-down')[0].click();
    const first = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/projects/4');
    expect(first.request.body).toEqual({ name: '生活', is_archived: false, sort_order: 1, exclude_from_reports: true });
    first.flush({});
    const second = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/projects/5');
    expect(second.request.body).toEqual({ name: '2026 大阪', is_archived: false, sort_order: 0, exclude_from_reports: false });
    second.flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    const revert = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/projects/4');
    expect(revert.request.body).toEqual({ name: '生活', is_archived: false, sort_order: 0, exclude_from_reports: true });
    revert.flush({});
    http.expectOne('/api/accounting/projects').flush([]);
  });

});

