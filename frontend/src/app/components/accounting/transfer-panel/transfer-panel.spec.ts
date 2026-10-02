import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { CategoryNode, EntryDetail, LedgerAccount } from '../../../models/accounting.model';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { TransferCommon } from '../entry-form/transfer-math';
import { TransferPanelComponent } from './transfer-panel';

function account(id: number, name: string, currency: string, balance: string): LedgerAccount {
  return { id, name, currency, balance, balance_main: balance, is_archived: false, icon: null } as unknown as LedgerAccount;
}

function category(id: number, name: string, icon: string): CategoryNode {
  return {
    id, kind: 'transfer_out', parent_id: null, name, icon, color: '#d4823b', sort_order: id, is_hidden: false,
    default_account_id: null, default_project_id: null, children: [],
  } as unknown as CategoryNode;
}

const COMMON: TransferCommon = {
  entry_date: '2026-10-02', entry_time: '09:05', posted_date: null, name: null, merchant: null,
  description: null, project_id: null, tags: [], reward_rule_ids: [],
};

describe('TransferPanelComponent', () => {
  let http: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TransferPanelComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: LayoutModeService, useValue: { mode: signal('panes') } },
      ],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  async function render(accounts: LedgerAccount[]): Promise<ComponentFixture<TransferPanelComponent>> {
    const fixture = TestBed.createComponent(TransferPanelComponent);
    fixture.componentRef.setInput('accounts', accounts);
    fixture.detectChanges();
    http
      .expectOne(r => r.url.startsWith('/api/accounting/categories') && r.urlWithParams.includes('kind=transfer_out'))
      .flush([category(30, '轉帳', '⇄'), category(31, '提款', '🏧'), category(32, '存款', '🏦'), category(33, '兌換', '💱'), category(34, '儲值', '🔋')]);
    await fixture.whenStable();
    fixture.detectChanges();
    return fixture;
  }

  function type(fixture: ComponentFixture<TransferPanelComponent>, selector: string, value: string): void {
    const input = (fixture.nativeElement as HTMLElement).querySelector<HTMLInputElement>(selector)!;
    input.value = value;
    input.dispatchEvent(new Event('input'));
    input.dispatchEvent(new Event('blur'));
    fixture.detectChanges();
  }

  it('shows the transfer categories and both account tiles with balances', async () => {
    const fixture = await render([account(1, '國泰主帳戶', 'TWD', '398071'), account(2, '日幣現金', 'JPY', '53635')]);
    const el = fixture.nativeElement as HTMLElement;

    expect(Array.from(el.querySelectorAll('.cat')).map(b => b.textContent?.trim().slice(-2))).toEqual(['轉帳', '提款', '存款', '兌換', '儲值']);
    expect(el.querySelector('.cat.on')?.textContent).toContain('轉帳');
    expect(el.querySelector<HTMLSelectElement>('.from-select')!.value).toBe('1');
    expect(el.querySelector<HTMLSelectElement>('.to-select')!.value).toBe('2');
    expect(el.querySelector('.acc-from .acc-balance')?.textContent).toContain('398,071');
  });

  it('derives the rate from both amounts and posts a cross-currency transfer', async () => {
    const fixture = await render([account(1, '國泰主帳戶', 'TWD', '398071'), account(2, '日幣現金', 'JPY', '53635')]);
    const el = fixture.nativeElement as HTMLElement;

    type(fixture, '.out-amount', '10000');
    type(fixture, '.in-amount', '46200');
    expect(el.querySelector('.rate-value')?.textContent?.trim()).toBe('4.620000');

    fixture.componentInstance.submit(COMMON, null)!.subscribe();
    const req = http.expectOne('/api/accounting/transfers');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toMatchObject({ from_account_id: 1, to_account_id: 2, out_amount: '10000', in_amount: '46200', category_id: 30 });
    req.flush({ transfer_group_id: 'g-1', out_entry_id: 1, in_entry_id: 2 });
  });

  it('mirrors the out amount and shows 1.00 for the same currency', async () => {
    const fixture = await render([account(1, '國泰主帳戶', 'TWD', '398071'), account(3, '玉山銀行', 'TWD', '702730')]);
    const el = fixture.nativeElement as HTMLElement;

    type(fixture, '.out-amount', '3000');
    expect(el.querySelector('.rate-value')?.textContent?.trim()).toBe('1.00');
    expect(el.querySelector<HTMLInputElement>('.in-amount')!.value).toBe('3000');
    expect(el.querySelector<HTMLInputElement>('.in-amount')!.readOnly).toBe(true);

    fixture.componentInstance.submit(COMMON, null)!.subscribe();
    const req = http.expectOne('/api/accounting/transfers');
    expect(req.request.body.in_amount).toBe('3000');
    req.flush({});
  });

  it('swaps the accounts and amounts with ⇄', async () => {
    const fixture = await render([account(1, '國泰主帳戶', 'TWD', '398071'), account(2, '日幣現金', 'JPY', '53635')]);
    const el = fixture.nativeElement as HTMLElement;
    type(fixture, '.out-amount', '10000');
    type(fixture, '.in-amount', '46200');

    el.querySelector<HTMLButtonElement>('.swap')!.click();
    fixture.detectChanges();

    expect(el.querySelector<HTMLSelectElement>('.from-select')!.value).toBe('2');
    expect(el.querySelector<HTMLInputElement>('.out-amount')!.value).toBe('46200');
    expect(el.querySelector('.rate-value')?.textContent?.trim()).toBe('0.216450');
  });

  it('adds an out-leg fee from its own sheet', async () => {
    const fixture = await render([account(1, '國泰主帳戶', 'TWD', '398071'), account(3, '玉山銀行', 'TWD', '702730')]);
    const el = fixture.nativeElement as HTMLElement;
    type(fixture, '.out-amount', '3000');

    el.querySelector<HTMLButtonElement>('.out-tile .plus')!.click();
    fixture.detectChanges();
    type(fixture, '.fee-input', '15');
    el.querySelector<HTMLButtonElement>('.sheet .pri')!.click();
    fixture.detectChanges();

    fixture.componentInstance.submit(COMMON, null)!.subscribe();
    const req = http.expectOne('/api/accounting/transfers');
    expect(req.request.body.out_fee).toEqual({ amount: '15', name: '手續費' });
    expect(req.request.body.in_fee).toBeNull();
    req.flush({});
  });

  it('loads both legs for editing and saves with PUT', async () => {
    const fixture = await render([account(1, '國泰主帳戶', 'TWD', '398071'), account(2, '日幣現金', 'JPY', '53635')]);
    const out = { id: 7, kind: 'transfer_out', account_id: 1, amount: '-10000.0000', category_id: 33, children: [] } as unknown as EntryDetail;
    const inn = { id: 8, kind: 'transfer_in', account_id: 2, amount: '46200.0000', category_id: 33, children: [] } as unknown as EntryDetail;
    fixture.componentRef.setInput('edit', { groupId: 'g-9', out, in: inn });
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector<HTMLInputElement>('.out-amount')!.value).toBe('10000');
    expect(el.querySelector('.cat.on')?.textContent).toContain('兌換');

    fixture.componentInstance.submit(COMMON, 'g-9')!.subscribe();
    const req = http.expectOne('/api/accounting/transfers/g-9');
    expect(req.request.method).toBe('PUT');
    req.flush({});
  });

  it('refuses the same account on both sides', async () => {
    const fixture = await render([account(1, '國泰主帳戶', 'TWD', '398071'), account(2, '日幣現金', 'JPY', '53635')]);
    const select = (fixture.nativeElement as HTMLElement).querySelector<HTMLSelectElement>('.to-select')!;
    select.value = '1';
    select.dispatchEvent(new Event('change'));
    type(fixture, '.out-amount', '100');

    expect(fixture.componentInstance.submit(COMMON, null)).toBeNull();
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).querySelector('.transfer-error')?.textContent).toContain('不可相同');
  });
  function key(target: Element, keyName: string, init: KeyboardEventInit = {}): KeyboardEvent {
    const event = new KeyboardEvent('keydown', { key: keyName, bubbles: true, cancelable: true, ...init });
    target.dispatchEvent(event);
    return event;
  }

  it('handles ⏎ in an amount itself: normalizes, marks it handled and asks the form to save', async () => {
    const fixture = await render([account(1, '國泰主帳戶', 'TWD', '398071'), account(2, '日幣現金', 'JPY', '53635')]);
    const el = fixture.nativeElement as HTMLElement;
    const requested: boolean[] = [];
    fixture.componentInstance.saveRequested.subscribe(value => requested.push(value));
    const out = el.querySelector<HTMLInputElement>('.out-amount')!;
    out.value = '1000+234';
    out.dispatchEvent(new Event('input'));

    const event = key(out, 'Enter', { shiftKey: true });
    fixture.detectChanges();

    expect(event.defaultPrevented).toBe(true);
    expect(out.value).toBe('1234');
    expect(requested).toEqual([true]);
  });

  it('does not ask to save when ⏎ meets an invalid amount', async () => {
    const fixture = await render([account(1, '國泰主帳戶', 'TWD', '398071'), account(2, '日幣現金', 'JPY', '53635')]);
    const el = fixture.nativeElement as HTMLElement;
    const requested: boolean[] = [];
    fixture.componentInstance.saveRequested.subscribe(value => requested.push(value));
    const out = el.querySelector<HTMLInputElement>('.out-amount')!;
    out.value = '5-9';
    out.dispatchEvent(new Event('input'));

    expect(key(out, 'Enter').defaultPrevented).toBe(true);
    fixture.detectChanges();
    expect(requested).toEqual([]);
    expect(el.querySelector('.transfer-error')?.textContent).toContain('金額格式錯誤');
  });

  it('closes its fee sheet on ⏎ and Esc without letting the form see them', async () => {
    const fixture = await render([account(1, '國泰主帳戶', 'TWD', '398071'), account(3, '玉山銀行', 'TWD', '702730')]);
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('.out-tile .plus')!.click();
    fixture.detectChanges();
    expect(key(el.querySelector('.fee-input')!, 'Enter').defaultPrevented).toBe(true);
    fixture.detectChanges();
    expect(el.querySelector('.sheet')).toBeNull();

    el.querySelector<HTMLButtonElement>('.in-tile .plus')!.click();
    fixture.detectChanges();
    expect(key(el.querySelector('.discount-input')!, 'Escape').defaultPrevented).toBe(true);
    fixture.detectChanges();
    expect(el.querySelector('.sheet')).toBeNull();
  });
});
