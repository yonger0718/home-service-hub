import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EntryDetail, LedgerAccount, LedgerEntry } from '../../../models/accounting.model';
import { EntryDetailComponent } from './entry-detail';

function entry(fields: Record<string, unknown>): LedgerEntry {
  return {
    id: 1, kind: 'expense', amount: '-100.0000', currency: 'TWD', original_amount: null, original_currency: null,
    fx_rate: null, fx_source: null, entry_date: '2026-10-01', entry_time: null, posted_date: '2026-10-01',
    account_id: 3, account_name: '玉山 UNI', category: null, category_id: null, category_icon: null, category_color: null,
    project: null, name: null, merchant: null, counterparty: null, counterparty_id: null, description: null, tags: [],
    parent_entry_id: null, transfer_group_id: null, group: null, rule_names: [], invoice_number: null,
    needs_review: false, source: 'manual', moze_id: null, locked: false, running_balance: '0',
    ...fields,
  } as unknown as LedgerEntry;
}

function detail(fields: Record<string, unknown>): EntryDetail {
  return {
    ...entry({}),
    children: [], group_members: [], transfer_counterpart: null, settles: null, settled_by: [], refunds: null,
    refunded_by: [], rules: [], rewards: [], open_amount: null, is_settled: null, refunded_amount: '0',
    ...fields,
  } as unknown as EntryDetail;
}

const ACCOUNTS = [
  { id: 3, name: '玉山 UNI', currency: 'TWD', is_archived: false },
  { id: 7, name: '錢包', currency: 'TWD', is_archived: false },
  { id: 9, name: '日幣現金', currency: 'JPY', is_archived: false },
] as unknown as LedgerAccount[];

const RECEIVABLE = detail({
  id: 42, kind: 'receivable', amount: '-420.0000', name: '代付 晚餐', counterparty: 'Alan', counterparty_id: 5,
  category: '應收款項/代付', category_icon: '🤝', category_color: '#84dccf', open_amount: '220.0000', is_settled: false,
  settled_by: [entry({ id: 50, kind: 'receivable', amount: '200.0000', name: '收款', account_name: '錢包' })],
});

describe('EntryDetailComponent', () => {
  let http: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [EntryDetailComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { paramMap: of(convertToParamMap({ id: '42' })) } },
      ],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  function render(body: EntryDetail): ComponentFixture<EntryDetailComponent> {
    const fixture = TestBed.createComponent(EntryDetailComponent);
    fixture.detectChanges();
    http.expectOne('/api/accounting/entries/42').flush(body);
    http.expectOne(r => r.url === '/api/accounting/accounts').flush(ACCOUNTS);
    fixture.detectChanges();
    return fixture;
  }

  function el(fixture: ComponentFixture<EntryDetailComponent>): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  it('shows the open amount, the collection and an enabled 新增收款 on a receivable', () => {
    const fixture = render(RECEIVABLE);

    expect(el(fixture).querySelector('.open-amount')?.textContent?.trim()).toBe('剩餘 $220');
    expect(el(fixture).querySelector('.related')?.textContent).toContain('收款');
    const settle = el(fixture).querySelector<HTMLButtonElement>('.action-settle')!;
    expect(settle.textContent).toContain('新增收款');
    expect(settle.disabled).toBe(false);
    expect(el(fixture).querySelector('.dhead')?.getAttribute('style')).toContain('background');
    expect(el(fixture).querySelector('.dgrid')?.textContent).toContain('Alan');
  });

  it('settles from the detail and then shows 已結清', () => {
    const fixture = render(RECEIVABLE);
    el(fixture).querySelector<HTMLButtonElement>('.action-settle')!.click();
    fixture.detectChanges();

    const options = Array.from(el(fixture).querySelectorAll('.form-account option')).map(o => o.textContent?.trim());
    expect(options).toEqual(['玉山 UNI', '錢包']);
    const account = el(fixture).querySelector<HTMLSelectElement>('.form-account')!;
    account.value = '7';
    account.dispatchEvent(new Event('change'));
    const amount = el(fixture).querySelector<HTMLInputElement>('.form-amount')!;
    amount.value = '220';
    amount.dispatchEvent(new Event('input'));
    el(fixture).querySelector<HTMLButtonElement>('.form-submit')!.click();

    const req = http.expectOne('/api/accounting/entries/42/settle');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toMatchObject({ account_id: 7, amount: '220', entry_time: null, description: null });
    req.flush({});
    http.expectOne('/api/accounting/entries/42').flush({ ...RECEIVABLE, open_amount: '0.0000', is_settled: true });
    fixture.detectChanges();

    expect(el(fixture).querySelector('.settled')?.textContent?.trim()).toBe('已結清');
    expect(el(fixture).querySelector<HTMLButtonElement>('.action-settle')!.disabled).toBe(true);
  });

  it('refuses a settlement above the open amount without calling the API', () => {
    const fixture = render(RECEIVABLE);
    el(fixture).querySelector<HTMLButtonElement>('.action-settle')!.click();
    fixture.detectChanges();
    const amount = el(fixture).querySelector<HTMLInputElement>('.form-amount')!;
    amount.value = '300';
    amount.dispatchEvent(new Event('input'));
    el(fixture).querySelector<HTMLButtonElement>('.form-submit')!.click();
    fixture.detectChanges();

    expect(el(fixture).querySelector('.form-error')?.textContent).toContain('$220');
  });

  it('lists the other transfer leg in the delete confirmation and deletes', () => {
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    const fixture = render(detail({
      id: 42, kind: 'transfer_out', amount: '-10000.0000', transfer_group_id: 'g-1', account_name: '國泰主帳戶',
      transfer_counterpart: entry({ id: 43, kind: 'transfer_in', amount: '46200.0000', currency: 'JPY', account_name: '日幣現金' }),
    }));
    el(fixture).querySelector<HTMLButtonElement>('.action-delete')!.click();
    fixture.detectChanges();

    const confirm = el(fixture).querySelector('.delete-confirm')!;
    expect(confirm.textContent).toContain('將一併刪除');
    expect(confirm.textContent).toContain('日幣現金');
    expect(confirm.textContent).toContain('¥46,200');

    confirm.querySelector<HTMLButtonElement>('.delete-entry')!.click();
    const req = http.expectOne('/api/accounting/entries/42');
    expect(req.request.method).toBe('DELETE');
    req.flush(null, { status: 204, statusText: 'No Content' });
    expect(navigate).toHaveBeenCalledWith(['/accounting']);
  });

  it('shows the FX line and the fee inside the total', () => {
    const fixture = render(detail({
      id: 42, amount: '-1166.0000', original_amount: '-5390.0000', original_currency: 'JPY', fx_rate: '0.2163000000',
      fx_source: 'fx_api', name: 'Uniqlo 外套', category: '購物/衣物',
      children: [entry({ id: 44, kind: 'fee', amount: '-17.0000', name: '國外交易手續費' })],
    }));

    expect(el(fixture).querySelector('.detail-total')?.textContent?.trim()).toBe('−$1,183');
    expect(el(fixture).querySelector('.fee-note')?.textContent?.trim()).toBe('內含手續費 $17');
    expect(el(fixture).querySelector('.fx-line')?.textContent?.trim()).toBe('¥5,390 × 0.2163 = $1,166（線上匯率）');
    expect(el(fixture).querySelector('.dhead .cat')?.textContent?.trim()).toBe('購物 › 衣物');
    expect(el(fixture).querySelector('.related')?.textContent).toContain('手續費 · 國外交易手續費');
  });

  it('disables the write actions on a locked entry but keeps 複製', () => {
    const fixture = render(detail({ id: 42, source: 'moze_backup', moze_id: 'R-1', locked: true }));

    expect(el(fixture).querySelector('app-lock-banner')).not.toBeNull();
    expect(el(fixture).querySelector<HTMLButtonElement>('.action-edit')!.disabled).toBe(true);
    expect(el(fixture).querySelector<HTMLButtonElement>('.action-delete')!.disabled).toBe(true);
    expect(el(fixture).querySelector<HTMLButtonElement>('.action-refund')!.disabled).toBe(true);
    expect(el(fixture).querySelector<HTMLButtonElement>('.action-copy')!.disabled).toBe(false);
  });

  it('treats a 收款 row as a settlement: no 新增收款, 編輯 disabled, 對象 shown', () => {
    const fixture = render(detail({
      id: 42, kind: 'receivable', amount: '200.0000', is_settlement: true, counterparty: 'Alan', open_amount: null,
    }));

    expect(el(fixture).querySelector('.action-settle')).toBeNull();
    expect(el(fixture).querySelector('.open-state')).toBeNull();
    expect(el(fixture).querySelector<HTMLButtonElement>('.action-edit')!.disabled).toBe(true);
    expect(el(fixture).querySelector('.dgrid')?.textContent).toContain('Alan');
  });

  it('drops a stale load when a newer one has started', () => {
    const fixture = render(RECEIVABLE);
    fixture.componentInstance.load(43);
    fixture.componentInstance.load(44);
    http.expectOne('/api/accounting/entries/44').flush(detail({ id: 44, name: '新的' }));
    http.expectOne('/api/accounting/entries/43').flush(detail({ id: 43, name: '舊的' }));
    fixture.detectChanges();

    expect(el(fixture).querySelector('.detail-name')?.textContent?.trim()).toBe('新的');
  });
});
