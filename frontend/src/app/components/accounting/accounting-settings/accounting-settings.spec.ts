import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

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
const PROJECTS = [{ id: 4, name: '生活', is_archived: false, sort_order: 0 }, { id: 5, name: '2026 大阪', is_archived: false, sort_order: 1 }];
const COUNTERPARTIES = [
  { id: 5, name: 'Alan', open_amounts: [{ currency: 'TWD', amount: '220.0000' }, { currency: 'JPY', amount: '-1000.0000' }] },
  { id: 6, name: 'Bob', open_amounts: [] },
];
const SCHEDULES = [
  { id: 1, kind: 'installment', moze_id: 'I-1', name: 'iPhone 分期', next_date: '2026-10-15', amount: '-1500.0000', currency: 'TWD' },
];

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
    fixture = TestBed.createComponent(AccountingSettingsComponent);
    fixture.detectChanges();
    http.expectOne('/api/accounting/preference').flush(PREFERENCE);
    http.expectOne('/api/accounting/account-groups').flush(GROUPS);
    http.expectOne(r => r.url.startsWith('/api/accounting/categories') && r.urlWithParams.includes('kind=expense')).flush(EXPENSE_TREE);
    http.expectOne('/api/accounting/projects').flush(PROJECTS);
    http.expectOne('/api/accounting/counterparties').flush(COUNTERPARTIES);
    http.expectOne('/api/accounting/imports/latest').flush({ detail: 'none' }, { status: 404, statusText: 'Not Found' });
    http.expectOne(r => r.url.startsWith('/api/accounting/imports/schedules')).flush(SCHEDULES);
    fixture.detectChanges();
    el = fixture.nativeElement as HTMLElement;
  });

  afterEach(() => http.verify());

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

  it('imports for real after the dry run and refreshes the latest import and schedules', () => {
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
    http.expectOne(r => r.url.startsWith('/api/accounting/imports/schedules')).flush([]);
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
    http.expectOne('/api/accounting/account-groups/1').flush({ detail: 'in use' }, { status: 409, statusText: 'Conflict' });
    fixture.detectChanges();
    expect(el.querySelector('.data-message')?.textContent).toContain('仍有帳戶');
  });

  it('shows the static lock state and the schedules list', () => {
    expect(el.querySelector('.lock-row .r')?.textContent?.trim()).toBe('未鎖定（切換後鎖定）');
    expect(el.querySelector('.lock-note')?.textContent).toContain('ACCOUNTING_IMPORT_LOCKED');
    const row = el.querySelector('.schedule-row')!;
    expect(row.textContent).toContain('分期');
    expect(row.textContent).toContain('iPhone 分期');
    expect(row.textContent).toContain('2026/10/15');
    expect(row.textContent).toContain('−$1,500');
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
});
