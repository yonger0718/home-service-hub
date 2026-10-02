import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, ParamMap, Router, convertToParamMap, provideRouter } from '@angular/router';
import { BehaviorSubject } from 'rxjs';
import { MockInstance, afterEach, describe, expect, it, vi } from 'vitest';

import { AccountDetail, LedgerAccount } from '../../../models/accounting.model';
import { AccountSettingsComponent } from './account-settings';

function account(fields: Record<string, unknown>): LedgerAccount {
  return {
    id: 0, name: '', currency: 'TWD', opening_balance: '0', balance: '0', balance_main: '0', entry_count: 0,
    group_id: null, group_name: null, icon: null, color: null, is_archived: false, include_in_total: true,
    is_credit: false, closing_day: null, due_rule: null, due_value: null, credit_limit: null, combined_account_id: null,
    credit_sharing_id: null, auto_pay_account_id: null, fx_fee_pct: null, fx_fee_rounding: null, fx_fee_refundable: false,
    sort_order: 0, moze_id: null, available_credit: null, rule_summaries: [], settings_locally_edited: false,
    ...fields,
  } as unknown as LedgerAccount;
}

const LIST = [
  account({ id: 2, name: '玉山銀行' }),
  account({ id: 10, name: '玉山信用卡', is_credit: true }),
  account({ id: 11, name: '玉山 Only', is_credit: true }),
  account({ id: 12, name: '玉山 UNI', is_credit: true }),
  account({ id: 13, name: '玉山 U Bear', is_credit: true }),
  account({ id: 30, name: '舊卡', is_credit: true, is_archived: true }),
  account({ id: 31, name: '舊帳戶', is_archived: true }),
];

const DETAIL = {
  ...account({
    id: 11, name: '玉山 Only', opening_balance: '-3693.0000', balance: '-4905.0000', balance_main: '-4905.0000',
    entry_count: 3, group_id: 3, group_name: '信用卡', icon: '💳', is_credit: true, closing_day: 15,
    due_rule: 'days_after_closing', due_value: 20, credit_limit: '300000.0000', combined_account_id: 10,
    auto_pay_account_id: 2, fx_fee_pct: '1.500', fx_fee_rounding: 'floor', moze_id: 'A-11', settings_locally_edited: true,
  }),
  note: '主要餐飲卡',
  credit_sharing_members: [],
  reward_rules: [
    { id: 1, name: '一般', method: 'percent', rate: '1.0000', fixed_amount: null, is_enabled: true },
    { id: 2, name: '餐飲', method: 'percent', rate: '5.2000', fixed_amount: null, is_enabled: false },
  ],
} as unknown as AccountDetail;

const GROUPS = [{ id: 1, name: '現金', sort_order: 0 }, { id: 3, name: '信用卡', sort_order: 2 }];

describe('AccountSettingsComponent', () => {
  let http: HttpTestingController;
  let navigate: MockInstance<Router['navigate']>;
  let params: BehaviorSubject<ParamMap>;

  async function setup(id: string | null): Promise<void> {
    params = new BehaviorSubject(convertToParamMap(id ? { id } : {}));
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 2, 10, 0));
    await TestBed.configureTestingModule({
      imports: [AccountSettingsComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        // Pane components are reused across :id changes (Task 21), so the page reads `paramMap` as an observable.
        { provide: ActivatedRoute, useValue: { paramMap: params.asObservable() } },
      ],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
    navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
  }

  /** The pane reuses the component when only `:id` changes (Task 21). */
  function navigateTo(id: string): void {
    params.next(convertToParamMap({ id }));
  }

  afterEach(() => {
    http.verify();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function render(detail: AccountDetail | null): ComponentFixture<AccountSettingsComponent> {
    const fixture = TestBed.createComponent(AccountSettingsComponent);
    fixture.detectChanges();
    if (detail) {
      http.expectOne(`/api/accounting/accounts/${detail.id}`).flush(detail);
    }
    const list = http.expectOne(r => r.url === '/api/accounting/accounts');
    // Archived accounts too, so an archived shared-limit member can be shown and removed.
    expect(list.request.params.get('include_archived')).toBe('true');
    list.flush(LIST);
    http.expectOne('/api/accounting/account-groups').flush(GROUPS);
    fixture.detectChanges();
    return fixture;
  }

  function el(fixture: ComponentFixture<AccountSettingsComponent>): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  function childLabels(fixture: ComponentFixture<AccountSettingsComponent>): string[] {
    return Array.from(el(fixture).querySelectorAll('.rw.child .k')).map(k => k.textContent?.trim() ?? '');
  }

  it('reveals the card rows when 信用帳戶 is switched on', async () => {
    await setup(null);
    const fixture = render(null);
    expect(childLabels(fixture).filter(label => label !== '手續費 %')).toEqual([]);

    el(fixture).querySelector<HTMLButtonElement>('.credit-toggle')!.click();
    fixture.detectChanges();

    expect(childLabels(fixture)).toEqual(['繳款期限', '信用額度', '額度共用', '主帳戶', '自動扣繳']);
  });

  it('previews the current cycle and the due date', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    expect(el(fixture).querySelector('.cycle-preview')?.textContent?.trim()).toBe('09/16 – 10/15');
    expect(el(fixture).querySelector('.due-preview')?.textContent?.trim()).toBe('結帳日後 20 天 → 11/04');

    const rule = el(fixture).querySelector<HTMLSelectElement>('.due-rule')!;
    rule.value = 'fixed_day';
    rule.dispatchEvent(new Event('change'));
    const value = el(fixture).querySelector<HTMLInputElement>('.due-value')!;
    value.value = '5';
    value.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    expect(el(fixture).querySelector('.due-preview')?.textContent?.trim()).toBe('每月 5 日 → 11/05');
  });

  it('makes 主幣種 read-only once the account has entries', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    expect(el(fixture).querySelector<HTMLInputElement>('.currency-input')!.disabled).toBe(true);
    expect(el(fixture).querySelector('.currency-note')?.textContent).toContain('已有記錄');
    expect(el(fixture).querySelector<HTMLButtonElement>('.delete-account')!.disabled).toBe(true);
  });

  it('offers only other non-archived credit accounts as 主帳戶 and lists the rules read-only', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    const masters = Array.from(el(fixture).querySelectorAll('.master-select option')).map(o => o.textContent?.trim());
    expect(masters).toEqual(['無', '玉山信用卡', '玉山 UNI', '玉山 U Bear']);
    expect(el(fixture).querySelector<HTMLSelectElement>('.master-select')!.value).toBe('10');
    const rules = Array.from(el(fixture).querySelectorAll('.rule-item')).map(r => r.textContent?.replace(/\s+/g, ' ').trim());
    expect(rules).toEqual(['一般 1%', '餐飲 5.2%（停用）']);
  });

  it('keeps an archived 主帳戶 and 自動扣繳 selectable, labelled （已封存）, and saves them unchanged', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, combined_account_id: 30, auto_pay_account_id: 31 } as AccountDetail);

    const master = el(fixture).querySelector<HTMLSelectElement>('.master-select')!;
    const autopay = el(fixture).querySelector<HTMLSelectElement>('.autopay-select')!;
    expect(master.value).toBe('30');
    expect(master.selectedOptions[0].textContent?.trim()).toBe('舊卡（已封存）');
    expect(autopay.value).toBe('31');
    expect(autopay.selectedOptions[0].textContent?.trim()).toBe('舊帳戶（已封存）');

    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    const req = http.expectOne('/api/accounting/accounts/11');
    expect(req.request.body).toMatchObject({ combined_account_id: 30, auto_pay_account_id: 31 });
    req.flush(DETAIL);
  });

  it('saves every field with PUT and returns to the passbook', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    const limit = el(fixture).querySelector<HTMLInputElement>('.credit-limit')!;
    limit.value = '350000';
    limit.dispatchEvent(new Event('input'));
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();

    const req = http.expectOne('/api/accounting/accounts/11');
    expect(req.request.method).toBe('PUT');
    expect(req.request.body).toEqual({
      name: '玉山 Only', currency: 'TWD', opening_balance: '-3693', is_archived: false, group_id: 3, icon: '💳',
      color: null, note: '主要餐飲卡', sort_order: 0, include_in_total: true, is_credit: true, closing_day: 15,
      due_rule: 'days_after_closing', due_value: 20, credit_limit: '350000', combined_account_id: 10,
      credit_sharing_id: null, credit_sharing_members: [], auto_pay_account_id: 2, fx_fee_pct: '1.5', fx_fee_rounding: 'floor',
      fx_fee_refundable: false,
    });
    req.flush(DETAIL);
    expect(navigate).toHaveBeenCalledWith(['/accounting/accounts', 11]);
  });

  it('clears the credit-only fields when 信用帳戶 is switched off', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, entry_count: 0 } as AccountDetail);
    el(fixture).querySelector<HTMLButtonElement>('.credit-toggle')!.click();
    fixture.detectChanges();
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();

    const req = http.expectOne('/api/accounting/accounts/11');
    expect(req.request.body).toMatchObject({ is_credit: false, due_rule: null, due_value: null, credit_limit: null, combined_account_id: null, auto_pay_account_id: null, credit_sharing_id: null, credit_sharing_members: [] });
    expect(req.request.body.closing_day).toBe(15);
    req.flush(DETAIL);
  });

  it('adds a chosen card to credit_sharing_members in the one PUT of this account', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    expect(el(fixture).querySelector('.sharing-current')).toBeNull();
    const uni = el(fixture).querySelector<HTMLInputElement>('.sharing-option input[data-account-id="12"]')!;
    uni.checked = true;
    uni.dispatchEvent(new Event('change'));
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();

    const own = http.expectOne('/api/accounting/accounts/11');
    expect(own.request.method).toBe('PUT');
    expect(own.request.body).toMatchObject({ credit_sharing_id: null, credit_sharing_members: [12] });
    own.flush({ ...DETAIL, credit_sharing_id: 'srv-1', credit_sharing_members: [12] });
    // No GET/PUT of account 12: the backend applies the membership (http.verify() in afterEach).
    expect(navigate).toHaveBeenCalledWith(['/accounting/accounts', 11]);
  });

  it('sends the full credit_sharing_members list on save', async () => {
    await setup('11');
    // Account A (11) shares its limit with B (12) and C (13).
    const fixture = render({ ...DETAIL, credit_sharing_id: 'share-1', credit_sharing_members: [11, 12, 13] } as AccountDetail);
    expect(el(fixture).querySelector('.sharing-current')?.textContent?.trim()).toBe('與 玉山 UNI、玉山 U Bear 共用額度');
    const checked = Array.from(el(fixture).querySelectorAll<HTMLInputElement>('.sharing-option input'))
      .filter(input => input.checked)
      .map(input => input.dataset['accountId']);
    expect(checked).toEqual(['12', '13']);

    const bear = el(fixture).querySelector<HTMLInputElement>('.sharing-option input[data-account-id="13"]')!;
    bear.checked = false;
    bear.dispatchEvent(new Event('change'));
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();

    const req = http.expectOne('/api/accounting/accounts/11');
    expect(req.request.method).toBe('PUT');
    expect(req.request.body.credit_sharing_members).toEqual([12]);
    expect(req.request.body.credit_sharing_id).toBe('share-1');
    req.flush({ ...DETAIL, credit_sharing_id: 'share-1', credit_sharing_members: [11, 12] });
    expect(navigate).toHaveBeenCalledWith(['/accounting/accounts', 11]);
  });

  it('lets an archived shared-limit member be removed', async () => {
    await setup('11');
    // 舊卡 (30) is archived but still shares the limit with A (11) and B (12).
    const fixture = render({ ...DETAIL, credit_sharing_id: 'share-2', credit_sharing_members: [11, 12, 30] } as AccountDetail);
    const labels = () => Array.from(el(fixture).querySelectorAll('.sharing-option')).map(o => o.textContent?.trim());
    expect(labels()).toEqual(['玉山信用卡', '玉山 UNI', '玉山 U Bear', '舊卡（已封存）']);
    const old = el(fixture).querySelector<HTMLInputElement>('.sharing-option input[data-account-id="30"]')!;
    expect(old.checked).toBe(true);

    old.checked = false;
    old.dispatchEvent(new Event('change'));
    fixture.detectChanges();
    // Still listed (unchecked) so the removal can be undone before saving.
    expect(labels()).toContain('舊卡（已封存）');
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();

    const req = http.expectOne('/api/accounting/accounts/11');
    expect(req.request.method).toBe('PUT');
    expect(req.request.body.credit_sharing_members).toEqual([12]);
    req.flush({ ...DETAIL, credit_sharing_id: 'share-2', credit_sharing_members: [11, 12] });
  });

  it('does not offer an archived card that is not a member', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    expect(el(fixture).querySelector('.sharing-option input[data-account-id="30"]')).toBeNull();
  });

  it('ignores a stale account response after switching accounts', async () => {
    await setup('3');
    const fixture = TestBed.createComponent(AccountSettingsComponent);
    fixture.detectChanges();
    const first = http.expectOne('/api/accounting/accounts/3');
    http.expectOne(r => r.url === '/api/accounting/accounts').flush(LIST);
    http.expectOne('/api/accounting/account-groups').flush(GROUPS);
    fixture.detectChanges();
    expect(el(fixture).querySelector<HTMLButtonElement>('.save')!.disabled).toBe(true);

    navigateTo('5');
    fixture.detectChanges();
    const second = http.expectOne('/api/accounting/accounts/5');
    second.flush({ ...DETAIL, id: 5, name: '帳戶五', moze_id: null });
    // switchMap cancels the request for 3; if it were still live (e.g. the switchMap is dropped), its late
    // response must still be ignored because its loadId is no longer the latest.
    if (!first.cancelled) {
      first.flush({ ...DETAIL, id: 3, name: '帳戶三', moze_id: null });
    }
    fixture.detectChanges();

    expect(el(fixture).querySelector<HTMLInputElement>('.name-input')!.value).toBe('帳戶五');
    expect(el(fixture).querySelector<HTMLButtonElement>('.save')!.disabled).toBe(false);
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    const put = http.expectOne(r => r.method === 'PUT');
    expect(put.request.url).toBe('/api/accounting/accounts/5');
    expect(put.request.body.name).toBe('帳戶五');
    put.flush({ ...DETAIL, id: 5, name: '帳戶五' });
    expect(navigate).toHaveBeenCalledWith(['/accounting/accounts', 5]);
  });

  it('shows validation errors beside the field', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    http.expectOne('/api/accounting/accounts/11').flush(
      { detail: [{ loc: ['body', 'combined_account_id'], msg: 'master cycle' }] },
      { status: 422, statusText: 'Unprocessable Entity' },
    );
    fixture.detectChanges();

    expect(el(fixture).querySelector('.field-error[data-field="combined_account_id"]')?.textContent?.trim()).toBe('master cycle');
    expect(navigate).not.toHaveBeenCalled();
  });

  it('suggests archiving when the delete is refused', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, entry_count: 0 } as AccountDetail);
    const button = el(fixture).querySelector<HTMLButtonElement>('.delete-account')!;
    button.click();
    fixture.detectChanges();
    expect(button.textContent?.trim()).toBe('確定刪除？');
    button.click();

    const req = http.expectOne('/api/accounting/accounts/11');
    expect(req.request.method).toBe('DELETE');
    req.flush({ detail: 'account has entries' }, { status: 409, statusText: 'Conflict' });
    fixture.detectChanges();
    expect(el(fixture).querySelector('.form-message')?.textContent).toContain('封存帳戶');
  });

  it('restores MOZE settings for a locally edited imported account', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    expect(el(fixture).querySelector('.custom-note')?.textContent).toContain('已自訂，匯入不再覆寫');

    el(fixture).querySelector<HTMLButtonElement>('.reset-flag')!.click();
    http.expectOne('/api/accounting/accounts/11/reset-settings-flag').flush({});
    http.expectOne('/api/accounting/accounts/11').flush({ ...DETAIL, settings_locally_edited: false });
    fixture.detectChanges();
    expect(el(fixture).querySelector('.custom-note')).toBeNull();
  });

  it('ignores a reset-settings response that arrives after switching accounts', async () => {
    await setup('3');
    const fixture = render({ ...DETAIL, id: 3, name: '帳戶三', moze_id: 'A-3', settings_locally_edited: true } as AccountDetail);
    el(fixture).querySelector<HTMLButtonElement>('.reset-flag')!.click();
    const reset = http.expectOne('/api/accounting/accounts/3/reset-settings-flag');

    navigateTo('5');
    fixture.detectChanges();
    http
      .expectOne('/api/accounting/accounts/5')
      .flush({ ...DETAIL, id: 5, name: '帳戶五', moze_id: null, credit_limit: '80000.0000', settings_locally_edited: false });
    fixture.detectChanges();
    const fields = () =>
      Array.from(el(fixture).querySelectorAll<HTMLInputElement>('input, select, textarea')).map(f => `${f.value}|${f.checked}`);
    const shown = fields();

    // Account 3's reset and its refetch answer late. The navigation cancelled the chain; if it were still live,
    // the loadId check skips the refetch and apply() drops any answer.
    if (!reset.cancelled) {
      reset.flush({});
    }
    http
      .match('/api/accounting/accounts/3')
      .filter(request => !request.cancelled)
      .forEach(request => request.flush({ ...DETAIL, id: 3, name: '帳戶三', settings_locally_edited: false }));
    fixture.detectChanges();

    expect(el(fixture).querySelector<HTMLInputElement>('.name-input')!.value).toBe('帳戶五');
    expect(fields()).toEqual(shown);
    expect(el(fixture).querySelector<HTMLButtonElement>('.save')!.disabled).toBe(false);
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    const put = http.expectOne(r => r.method === 'PUT');
    expect(put.request.url).toBe('/api/accounting/accounts/5');
    expect(put.request.body).toMatchObject({ name: '帳戶五', credit_limit: '80000' });
    put.flush({ ...DETAIL, id: 5, name: '帳戶五' });
  });

  it('creates a new account and opens its passbook', async () => {
    await setup(null);
    const fixture = render(null);
    const name = el(fixture).querySelector<HTMLInputElement>('.name-input')!;
    name.value = '悠遊卡';
    name.dispatchEvent(new Event('input'));
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();

    const req = http.expectOne('/api/accounting/accounts');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toMatchObject({ name: '悠遊卡', currency: 'TWD', opening_balance: '0', is_credit: false });
    req.flush({ ...DETAIL, id: 40, name: '悠遊卡' });
    expect(navigate).toHaveBeenCalledWith(['/accounting/accounts', 40]);
  });
  // ---- T27 leftovers --------------------------------------------------------------------------------------------

  it('refuses to save or delete after the account failed to load', async () => {
    await setup('11');
    const fixture = TestBed.createComponent(AccountSettingsComponent);
    fixture.detectChanges();
    http.expectOne('/api/accounting/accounts/11').flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    http.expectOne(r => r.url === '/api/accounting/accounts').flush(LIST);
    http.expectOne('/api/accounting/account-groups').flush(GROUPS);
    fixture.detectChanges();

    expect(el(fixture).querySelector<HTMLButtonElement>('.save')!.disabled).toBe(true);
    expect(el(fixture).querySelector<HTMLButtonElement>('.delete-account')!.disabled).toBe(true);
    fixture.componentInstance.save();
    fixture.componentInstance.remove();
    http.expectNone(r => r.method === 'PUT' || r.method === 'DELETE' || r.method === 'POST');
  });

  it('validates the closing day and the due value inline, without a garbage preview', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    const type = (selector: string, value: string) => {
      const input = el(fixture).querySelector<HTMLInputElement>(selector)!;
      input.value = value;
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();
    };

    type('.closing-day', '0');
    expect(el(fixture).querySelector('.field-error[data-field="closing_day"]')?.textContent?.trim()).toBe('結帳日須為 1–31');
    expect(el(fixture).querySelector('.cycle-preview')?.textContent?.trim()).toBe('');
    type('.closing-day', '15');
    type('.due-value', '0');
    expect(el(fixture).querySelector('.field-error[data-field="due_value"]')?.textContent?.trim()).toBe('請輸入大於 0 的天數');
    expect(el(fixture).querySelector('.due-preview')?.textContent?.trim()).toBe('');

    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    http.expectNone('/api/accounting/accounts/11');
    expect(navigate).not.toHaveBeenCalled();
  });

  it('drops a save answer once another account is shown, and the new form is not left saving', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    const put = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/accounts/11');

    navigateTo('12');
    fixture.detectChanges();
    http.expectOne('/api/accounting/accounts/12').flush({ ...DETAIL, id: 12, name: '玉山 UNI', moze_id: null });
    fixture.detectChanges();
    expect(fixture.componentInstance.saving()).toBe(false);
    expect(el(fixture).querySelector<HTMLButtonElement>('.save')!.disabled).toBe(false);

    put.flush(DETAIL);
    expect(navigate).not.toHaveBeenCalled();
  });
});
