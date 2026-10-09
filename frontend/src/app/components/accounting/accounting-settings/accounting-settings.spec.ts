import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountingService } from '../../../services/accounting.service';
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
    el.querySelector<HTMLElement>('h2')!.click();
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
});
