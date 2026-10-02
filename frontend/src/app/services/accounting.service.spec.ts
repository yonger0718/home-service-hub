import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Observable } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  AccountInput,
  BalanceAdjustmentInput,
  EntryInput,
  ImportRun,
  Preference,
  RefundInput,
  SettleInput,
  SplitInput,
  TransferInput,
} from '../models/accounting.model';
import { makeAccount, makeEntry, makePreference } from '../components/accounting/testing/fixtures';
import { AccountingService } from './accounting.service';

const ENTRY_INPUT: EntryInput = {
  account_id: 2,
  kind: 'expense',
  amount: '170',
  original_amount: null,
  original_currency: null,
  fx_rate: null,
  entry_date: '2026-10-02',
  entry_time: '12:31',
  posted_date: null,
  category_id: 12,
  project_id: 5,
  name: '午餐',
  merchant: '麥當勞',
  counterparty_id: null,
  description: null,
  tags: ['LinePay'],
  invoice_number: null,
  invoice_random: null,
  fee: null,
  discount: null,
  reward_rule_ids: [],
};

const TRANSFER_INPUT: TransferInput = {
  from_account_id: 1,
  to_account_id: 2,
  out_amount: '10000',
  in_amount: null,
  entry_date: '2026-10-01',
  entry_time: null,
  posted_date: null,
  category_id: null,
  name: null,
  merchant: null,
  description: null,
  project_id: null,
  tags: [],
  out_fee: { amount: '15', name: '手續費' },
  out_discount: null,
  in_fee: null,
  in_discount: null,
  reward_rule_ids: [],
};

const SPLIT_INPUT: SplitInput = {
  name: '午餐',
  merchant: null,
  description: null,
  entry_date: '2026-10-02',
  entry_time: null,
  posted_date: null,
  project_id: null,
  tags: [],
  members: [{ ...ENTRY_INPUT }, { ...ENTRY_INPUT, kind: 'receivable', amount: '180', counterparty_id: 4 }],
};

const SETTLE_INPUT: SettleInput = { account_id: 1, amount: '200', entry_date: '2026-10-02', entry_time: null, description: null };
const REFUND_INPUT: RefundInput = { account_id: null, amount: '570', entry_date: '2026-10-02', entry_time: null, description: null };
const ADJUST_INPUT: BalanceAdjustmentInput = {
  account_id: 1,
  target_balance: '24798',
  entry_date: '2026-10-02',
  entry_time: null,
  description: null,
};
const ACCOUNT_INPUT: AccountInput = {
  name: '玉山 Only',
  currency: 'TWD',
  opening_balance: '0',
  is_archived: false,
  group_id: 3,
  icon: '💳',
  color: null,
  note: null,
  sort_order: 0,
  include_in_total: true,
  is_credit: true,
  closing_day: 15,
  due_rule: 'days_after_closing',
  due_value: 20,
  credit_limit: '300000',
  combined_account_id: null,
  credit_sharing_id: null,
  auto_pay_account_id: null,
  fx_fee_pct: '1.5',
  fx_fee_rounding: 'floor',
  fx_fee_refundable: false,
};

interface Case {
  name: string;
  call: (service: AccountingService) => Observable<unknown>;
  method: 'GET' | 'POST' | 'PUT' | 'DELETE';
  url: string;
  body?: unknown;
  params?: Record<string, string | string[]>;
}

const API = '/api/accounting';

const CASES: Case[] = [
  { name: 'getAccount', call: s => s.getAccount(3), method: 'GET', url: `${API}/accounts/3` },
  { name: 'getAccount as of', call: s => s.getAccount(3, '2026-09-30'), method: 'GET', url: `${API}/accounts/3`, params: { as_of: '2026-09-30' } },
  { name: 'getEntries with text', call: s => s.getEntries(3, { q: '午餐', limit: 20 }), method: 'GET', url: `${API}/accounts/3/entries`, params: { q: '午餐', limit: '20' } },
  {
    name: 'getAllEntries',
    call: s => s.getAllEntries({ limit: 50, offset: 0, account_id: [1, 2], hide_rewards: true, date_from: '2026-10-01' }),
    method: 'GET',
    url: `${API}/entries`,
    params: { limit: '50', offset: '0', account_id: ['1', '2'], hide_rewards: 'true', date_from: '2026-10-01' },
  },
  { name: 'getMonthSummary', call: s => s.getMonthSummary('2026-09'), method: 'GET', url: `${API}/entries/summary`, params: { month: '2026-09' } },
  { name: 'getEntry', call: s => s.getEntry(9), method: 'GET', url: `${API}/entries/9` },
  { name: 'createEntry', call: s => s.createEntry(ENTRY_INPUT), method: 'POST', url: `${API}/entries`, body: ENTRY_INPUT },
  { name: 'updateEntry', call: s => s.updateEntry(9, ENTRY_INPUT), method: 'PUT', url: `${API}/entries/9`, body: ENTRY_INPUT },
  { name: 'deleteEntry', call: s => s.deleteEntry(9), method: 'DELETE', url: `${API}/entries/9` },
  { name: 'createTransfer', call: s => s.createTransfer(TRANSFER_INPUT), method: 'POST', url: `${API}/transfers`, body: TRANSFER_INPUT },
  {
    name: 'updateTransfer',
    call: s => s.updateTransfer('6f1c3a52-0000-4000-8000-000000000001', TRANSFER_INPUT),
    method: 'PUT',
    url: `${API}/transfers/6f1c3a52-0000-4000-8000-000000000001`,
    body: TRANSFER_INPUT,
  },
  { name: 'createSplit', call: s => s.createSplit(SPLIT_INPUT), method: 'POST', url: `${API}/splits`, body: SPLIT_INPUT },
  { name: 'updateSplit', call: s => s.updateSplit(4, SPLIT_INPUT), method: 'PUT', url: `${API}/splits/4`, body: SPLIT_INPUT },
  { name: 'deleteSplit', call: s => s.deleteSplit(4), method: 'DELETE', url: `${API}/splits/4` },
  { name: 'settleEntry', call: s => s.settleEntry(9, SETTLE_INPUT), method: 'POST', url: `${API}/entries/9/settle`, body: SETTLE_INPUT },
  { name: 'refundEntry', call: s => s.refundEntry(9, REFUND_INPUT), method: 'POST', url: `${API}/entries/9/refund`, body: REFUND_INPUT },
  { name: 'createBalanceAdjustment', call: s => s.createBalanceAdjustment(ADJUST_INPUT), method: 'POST', url: `${API}/balance-adjustments`, body: ADJUST_INPUT },
  { name: 'createAccount', call: s => s.createAccount(ACCOUNT_INPUT), method: 'POST', url: `${API}/accounts`, body: ACCOUNT_INPUT },
  { name: 'updateAccount', call: s => s.updateAccount(3, ACCOUNT_INPUT), method: 'PUT', url: `${API}/accounts/3`, body: ACCOUNT_INPUT },
  { name: 'deleteAccount', call: s => s.deleteAccount(3), method: 'DELETE', url: `${API}/accounts/3` },
  { name: 'resetSettingsFlag', call: s => s.resetSettingsFlag(3), method: 'POST', url: `${API}/accounts/3/reset-settings-flag`, body: {} },
  { name: 'getAccountGroups', call: s => s.getAccountGroups(), method: 'GET', url: `${API}/account-groups` },
  { name: 'createAccountGroup', call: s => s.createAccountGroup({ name: '信用卡', sort_order: 2 }), method: 'POST', url: `${API}/account-groups`, body: { name: '信用卡', sort_order: 2 } },
  { name: 'updateAccountGroup', call: s => s.updateAccountGroup(2, { name: '卡片', sort_order: 2 }), method: 'PUT', url: `${API}/account-groups/2`, body: { name: '卡片', sort_order: 2 } },
  { name: 'deleteAccountGroup', call: s => s.deleteAccountGroup(2), method: 'DELETE', url: `${API}/account-groups/2` },
  { name: 'reorderAccountGroups', call: s => s.reorderAccountGroups([3, 1, 2]), method: 'PUT', url: `${API}/account-groups/order`, body: { ids: [3, 1, 2] } },
  { name: 'getCategories', call: s => s.getCategories('expense'), method: 'GET', url: `${API}/categories`, params: { kind: 'expense' } },
  {
    name: 'createCategory',
    call: s => s.createCategory({ kind: 'expense', parent_id: null, name: '飲食', icon: '🍜', color: '#f0cd92', sort_order: 0, is_hidden: false }),
    method: 'POST',
    url: `${API}/categories`,
    body: { kind: 'expense', parent_id: null, name: '飲食', icon: '🍜', color: '#f0cd92', sort_order: 0, is_hidden: false },
  },
  {
    name: 'updateCategory',
    call: s => s.updateCategory(12, { kind: 'expense', parent_id: 1, name: '午餐', icon: null, color: null, sort_order: 1, is_hidden: true }),
    method: 'PUT',
    url: `${API}/categories/12`,
    body: { kind: 'expense', parent_id: 1, name: '午餐', icon: null, color: null, sort_order: 1, is_hidden: true },
  },
  { name: 'deleteCategory', call: s => s.deleteCategory(12), method: 'DELETE', url: `${API}/categories/12` },
  { name: 'reorderCategories', call: s => s.reorderCategories([12, 11]), method: 'PUT', url: `${API}/categories/order`, body: { ids: [12, 11] } },
  { name: 'getProjects', call: s => s.getProjects(), method: 'GET', url: `${API}/projects` },
  { name: 'createProject', call: s => s.createProject({ name: '生活' }), method: 'POST', url: `${API}/projects`, body: { name: '生活' } },
  { name: 'updateProject', call: s => s.updateProject(5, { name: '日常', is_archived: true }), method: 'PUT', url: `${API}/projects/5`, body: { name: '日常', is_archived: true } },
  { name: 'deleteProject', call: s => s.deleteProject(5), method: 'DELETE', url: `${API}/projects/5` },
  { name: 'getCounterparties', call: s => s.getCounterparties(), method: 'GET', url: `${API}/counterparties` },
  { name: 'createCounterparty', call: s => s.createCounterparty({ name: 'Alan' }), method: 'POST', url: `${API}/counterparties`, body: { name: 'Alan' } },
  { name: 'updateCounterparty', call: s => s.updateCounterparty(4, { name: 'Alan Chen' }), method: 'PUT', url: `${API}/counterparties/4`, body: { name: 'Alan Chen' } },
  { name: 'deleteCounterparty', call: s => s.deleteCounterparty(4), method: 'DELETE', url: `${API}/counterparties/4` },
  { name: 'updatePreference', call: s => s.updatePreference(makePreference({ keypad_layout: 'phone' })), method: 'PUT', url: `${API}/preference`, body: makePreference({ keypad_layout: 'phone' }) },
  {
    name: 'getFxRate',
    call: s => s.getFxRate('2026-10-02', 'JPY', 'TWD'),
    method: 'GET',
    url: `${API}/fx-rate`,
    params: { date: '2026-10-02', base: 'JPY', quote: 'TWD' },
  },
  { name: 'getSchedules', call: s => s.getSchedules('installment'), method: 'GET', url: `${API}/imports/schedules`, params: { kind: 'installment' } },
  {
    name: 'getAccountSummary',
    call: s => s.getAccountSummary(3, '2026-09-16', '2026-10-15'),
    method: 'GET',
    url: `${API}/accounts/3/summary`,
    params: { date_from: '2026-09-16', date_to: '2026-10-15' },
  },
];

describe('AccountingService', () => {
  let service: AccountingService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(AccountingService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('lists accounts without the archived flag by default', () => {
    let names: string[] = [];
    service.getAccounts().subscribe(accounts => (names = accounts.map(a => a.name)));

    httpMock.expectOne('/api/accounting/accounts').flush([{ id: 1, name: '錢包' }]);

    expect(names).toEqual(['錢包']);
  });

  it('passes through a null main-currency balance and a null mixed-currency group total', () => {
    let balances: (string | null)[] = [];
    let totals: (string | null)[] = [];
    service.getAccounts().subscribe(accounts => (balances = accounts.map(a => a.balance_main)));
    service.getAllEntries().subscribe(page => (totals = page.items.map(e => (e.group ? e.group.total : 'no group'))));

    httpMock.expectOne('/api/accounting/accounts').flush([makeAccount({ currency: 'JPY', balance_main: null })]);
    httpMock.expectOne('/api/accounting/entries').flush({
      items: [makeEntry({ group: { id: 4, kind: 'split', name: '旅行', count: 2, total: null, currency: 'JPY' } })],
      total: 1,
      limit: 50,
      offset: 0,
    });

    expect(balances).toEqual([null]);
    expect(totals).toEqual([null]);
  });

  it('asks for archived accounts only when requested', () => {
    service.getAccounts(true).subscribe();

    const req = httpMock.expectOne(r => r.url === '/api/accounting/accounts');
    expect(req.request.params.get('include_archived')).toBe('true');
    req.flush([]);
  });

  it('sends as_of only when a balance date is given', () => {
    service.getAccounts(false, '2026-09-30').subscribe();
    service.getAccount(3).subscribe();

    const list = httpMock.expectOne(r => r.url === '/api/accounting/accounts');
    expect(list.request.params.keys()).toEqual(['as_of']);
    expect(list.request.params.get('as_of')).toBe('2026-09-30');
    list.flush([]);
    const detail = httpMock.expectOne(r => r.url === '/api/accounting/accounts/3');
    expect(detail.request.params.has('as_of')).toBe(false);
    detail.flush({});
  });

  it('sends only the set entry query parameters', () => {
    service.getEntries(7, { limit: 50, offset: 100, kind: 'expense', date_from: null }).subscribe();

    const req = httpMock.expectOne(r => r.url === '/api/accounting/accounts/7/entries');
    expect(req.request.params.keys().sort()).toEqual(['kind', 'limit', 'offset']);
    expect(req.request.params.get('offset')).toBe('100');
    req.flush({ items: [], total: 0, limit: 50, offset: 100 });
  });

  it.each(CASES)('$name calls $method $url', ({ call, method, url, body, params }) => {
    call(service).subscribe();

    const req = httpMock.expectOne(r => r.url === url && r.method === method);
    if (body !== undefined) {
      expect(req.request.body).toEqual(body);
    }
    for (const [key, value] of Object.entries(params ?? {})) {
      if (Array.isArray(value)) {
        expect(req.request.params.getAll(key)).toEqual(value);
      } else {
        expect(req.request.params.get(key)).toBe(value);
      }
    }
    req.flush(method === 'DELETE' ? null : {});
  });

  it('posts the backup zip as multipart form data with the run options as query parameters', () => {
    const file = new File(['zip'], 'MOZE_4.0.zip', { type: 'application/zip' });
    service.importBackup(file, { dryRun: true, strict: false }).subscribe();

    const req = httpMock.expectOne(r => r.url === '/api/accounting/imports/moze-backup');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toBeInstanceOf(FormData);
    const form = req.request.body as FormData;
    expect((form.get('file') as File).name).toBe('MOZE_4.0.zip');
    expect(form.get('renames')).toBe('');
    expect(req.request.params.get('dry_run')).toBe('true');
    expect(req.request.params.get('strict')).toBe('false');
    expect(req.request.params.has('allow_fx_outliers')).toBe(false);
    req.flush({ id: null, kind: 'moze_backup', status: 'dry_run' });
  });

  it('reads the preference once per session', () => {
    const seen: Preference[] = [];
    service.getPreference().subscribe(p => seen.push(p));
    httpMock.expectOne('/api/accounting/preference').flush(makePreference({ main_currency: 'TWD' }));
    service.getPreference().subscribe(p => seen.push(p));

    httpMock.expectNone('/api/accounting/preference');
    expect(seen.map(p => p.main_currency)).toEqual(['TWD', 'TWD']);
  });

  it('serves the updated preference after a write', () => {
    service.updatePreference(makePreference({ keypad_layout: 'phone' })).subscribe();
    httpMock.expectOne('/api/accounting/preference').flush(makePreference({ keypad_layout: 'phone' }));

    let layout = '';
    service.getPreference().subscribe(p => (layout = p.keypad_layout));
    httpMock.expectNone('/api/accounting/preference');
    expect(layout).toBe('phone');
  });

  it('bumps entriesChanged after an entry write succeeds, not before', () => {
    const before = service.entriesChanged();
    service.createEntry(ENTRY_INPUT).subscribe();
    expect(service.entriesChanged()).toBe(before);

    httpMock.expectOne('/api/accounting/entries').flush({ id: 1 });

    expect(service.entriesChanged()).toBe(before + 1);
  });

  it('bumps accountsChanged after an account write', () => {
    const before = service.accountsChanged();
    service.updateAccount(3, ACCOUNT_INPUT).subscribe();
    expect(service.accountsChanged()).toBe(before);

    httpMock.expectOne('/api/accounting/accounts/3').flush({ id: 3 });
    expect(service.accountsChanged()).toBe(before + 1);

    service.deleteAccountGroup(2).subscribe();
    httpMock.expectOne('/api/accounting/account-groups/2').flush(null);
    expect(service.accountsChanged()).toBe(before + 2);

    service.importBackup(new File(['zip'], 'b.zip'), { dryRun: true }).subscribe();
    httpMock.expectOne(r => r.url === '/api/accounting/imports/moze-backup').flush({ id: null, status: 'dry_run' });
    expect(service.accountsChanged()).toBe(before + 2);

    service.importBackup(new File(['zip'], 'b.zip'), { dryRun: false }).subscribe();
    httpMock.expectOne(r => r.url === '/api/accounting/imports/moze-backup').flush({ id: 4, status: 'succeeded' });
    expect(service.accountsChanged()).toBe(before + 3);
  });

  it('maps a 404 from imports/latest to null', () => {
    let latest: ImportRun | null | undefined;
    service.getLatestImport().subscribe(run => (latest = run));

    httpMock
      .expectOne('/api/accounting/imports/latest')
      .flush({ code: 404, message: 'no import has run yet' }, { status: 404, statusText: 'Not Found' });

    expect(latest).toBeNull();
  });

  it('propagates other errors from imports/latest', () => {
    let failed = false;
    service.getLatestImport().subscribe({ error: () => (failed = true) });

    httpMock.expectOne('/api/accounting/imports/latest').flush('boom', { status: 500, statusText: 'Error' });

    expect(failed).toBe(true);
  });
});
