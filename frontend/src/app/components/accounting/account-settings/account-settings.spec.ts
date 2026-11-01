import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, ParamMap, Router, convertToParamMap, provideRouter } from '@angular/router';
import { By } from '@angular/platform-browser';
import { BehaviorSubject } from 'rxjs';
import { MockInstance, afterEach, describe, expect, it, vi } from 'vitest';

import { AccountDetail, LedgerAccount, RewardRule } from '../../../models/accounting.model';
import { ACCOUNT_LOGOS } from '../../../theme/account-logos';
import { DirtyFormRegistry } from '../dirty-form.service';
import { RewardRuleSheetComponent } from '../reward-rule-sheet/reward-rule-sheet';
import { AccountSettingsComponent, accountToInput, decimalText } from './account-settings';

function account(fields: Record<string, unknown>): LedgerAccount {
  return {
    id: 0, name: '', currency: 'TWD', opening_balance: '0', balance: '0', balance_main: '0', balance_kind: 'ledger',
    stale: false, valuation: null, entry_count: 0,
    group_id: null, group_name: null, icon: null, color: null, is_archived: false, include_in_total: true,
    is_credit: false, closing_day: null, due_rule: null, due_value: null, credit_limit: null, combined_account_id: null,
    credit_sharing_id: null, auto_pay_account_id: null, fx_fee_pct: null, fx_fee_rounding: null, fx_fee_refundable: false,
    sort_order: 0, moze_id: null, available_credit: null, rule_summaries: [], settings_locally_edited: false,
    ...fields,
  } as unknown as LedgerAccount;
}

function rewardRule(fields: Partial<RewardRule>): RewardRule {
  return {
    id: 0, account_id: 11, name: '', method: 'percent', rate: null, fixed_amount: null, window: 'statement_cycle',
    posting: 'after_window', delay_days: 0, post_month_offset: 1, post_day: 15, txn_rounding: 'round',
    total_rounding: 'keep', total_cap: null, shared_cap_id: null, is_basic: false, reward_account_id: null,
    reward_project_id: null, starts_on: null, ends_on: null, is_enabled: true, description: null, sort_order: 0,
    moze_id: null,
    ...fields,
  };
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
    rewardRule({ id: 1, name: '一般', rate: '1.0000', is_basic: true, sort_order: 0 }),
    rewardRule({
      id: 2, name: '餐飲', rate: '5.2000', is_enabled: false, posting: 'after_transaction', delay_days: 3,
      starts_on: '2026-01-01', ends_on: '2026-12-31', moze_id: 'B-2', sort_order: 1,
    }),
  ],
} as unknown as AccountDetail;

const GROUPS = [{ id: 1, name: '現金', sort_order: 0 }, { id: 3, name: '信用卡', sort_order: 2 }];
const PROJECTS = [{ id: 7, name: '旅遊', sort_order: 0, is_archived: false, moze_id: null }];

describe('AccountSettingsComponent', () => {
  let http: HttpTestingController;
  let navigate: MockInstance<Router['navigate']>;
  let params: BehaviorSubject<ParamMap>;
  let queryParams: BehaviorSubject<ParamMap>;
  let route: Partial<ActivatedRoute>;

  async function setup(id: string | null, query: Record<string, string> = {}): Promise<void> {
    params = new BehaviorSubject(convertToParamMap(id ? { id } : {}));
    queryParams = new BehaviorSubject(convertToParamMap(query));
    route = { paramMap: params.asObservable(), queryParamMap: queryParams.asObservable() };
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 2, 10, 0));
    await TestBed.configureTestingModule({
      imports: [AccountSettingsComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        // Pane components are reused across :id changes (Task 21), so the page reads `paramMap` as an observable.
        { provide: ActivatedRoute, useValue: route },
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

  it('hero: a valuation balance is labelled 市值 (R7 §5)', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, balance: '514200', balance_main: '514200', balance_kind: 'valuation', is_credit: false } as AccountDetail);
    expect(el(fixture).querySelector('.bigicon b')?.textContent?.trim()).toBe('$514,200');
    expect(el(fixture).querySelector('.bigicon .kind-hint')?.textContent?.trim()).toBe('市值');
  });

  it('hero: an unavailable valuation reads 市值暫不可得 without the red tone (R7 §5)', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, balance: null, balance_main: null, balance_kind: 'valuation', stale: true, is_credit: false } as AccountDetail);
    const hero = el(fixture).querySelector('.bigicon b')!;
    expect(hero.textContent?.trim()).toBe('市值暫不可得');
    expect(hero.classList.contains('neg')).toBe(false);
    expect(hero.classList.contains('unavailable')).toBe(true);
    expect(el(fixture).querySelector('.bigicon .kind-hint')).toBeNull();
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
    http.expectOne('/api/accounting/projects').flush(PROJECTS);
    fixture.detectChanges();
    return fixture;
  }

  function el(fixture: ComponentFixture<AccountSettingsComponent>): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  function texts(fixture: ComponentFixture<AccountSettingsComponent>, selector: string): string[] {
    return Array.from(el(fixture).querySelectorAll(selector)).map(n => n.textContent?.replace(/\s+/g, ' ').trim() ?? '');
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

  it('offers only other non-archived credit accounts as 主帳戶 and lists the rules with a state badge', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    const masters = Array.from(el(fixture).querySelectorAll('.master-select option')).map(o => o.textContent?.trim());
    expect(masters).toEqual(['無', '玉山信用卡', '玉山 UNI', '玉山 U Bear']);
    expect(el(fixture).querySelector<HTMLSelectElement>('.master-select')!.value).toBe('10');
    expect(texts(fixture, '.rule-row .rule-label')).toEqual(['一般 (1%)', '餐飲 (5.2%)']);
    expect(texts(fixture, '.rule-row .rule-state')).toEqual(['啟用中', '已停用']);
    expect(el(fixture).textContent).not.toContain('（停用）');
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
      logo: null, color: null, note: '主要餐飲卡', sort_order: 0, include_in_total: true, is_credit: true, closing_day: 15,
      due_rule: 'days_after_closing', due_value: 20, credit_limit: '350000', combined_account_id: 10,
      credit_sharing_id: null, credit_sharing_members: [], auto_pay_account_id: 2, fx_fee_pct: '1.5', fx_fee_rounding: 'floor',
      fx_fee_refundable: false,
    });
    req.flush(DETAIL);
    expect(navigate).toHaveBeenCalledWith(['/accounting/accounts', 11]);
  });

  it('a 圖示-only edit keeps the stored logo', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, logo: ACCOUNT_LOGOS[0].slug } as AccountDetail);
    const icon = el(fixture).querySelector<HTMLInputElement>('.icon-input')!;
    icon.value = '🏦';
    icon.dispatchEvent(new Event('input'));
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();

    const req = http.expectOne('/api/accounting/accounts/11');
    expect(req.request.body.icon).toBe('🏦');
    expect(req.request.body.logo).toBe(ACCOUNT_LOGOS[0].slug);
    req.flush(DETAIL);
  });

  it('shows the current logo label and lists the set with 無 first', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, logo: 'cathay-united' } as AccountDetail);
    const current = el(fixture).querySelector<HTMLButtonElement>('.logo-current')!;
    expect(current.textContent).toContain('國泰世華');
    expect(el(fixture).querySelector('.logo-grid')).toBeNull();
    current.click();
    fixture.detectChanges();
    const cells = Array.from(el(fixture).querySelectorAll<HTMLButtonElement>('.logo-grid button'));
    expect(cells[0].textContent?.trim()).toBe('無');
    expect(cells.length).toBe(ACCOUNT_LOGOS.length + 1);
    expect(cells.find(c => c.getAttribute('aria-label') === '國泰世華')!.getAttribute('aria-pressed')).toBe('true');
    expect(cells[0].getAttribute('aria-pressed')).toBe('false');
  });

  it('names the current-logo button 品牌 logo：<label> and shows the account icon when no logo is set', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, icon: '🍱', logo: null } as AccountDetail);
    const current = el(fixture).querySelector<HTMLButtonElement>('.logo-current')!;
    expect(current.getAttribute('aria-label')).toBe('品牌 logo：無');
    expect(current.querySelector('app-account-icon .emoji')!.textContent).toBe('🍱');
  });

  it('labels a stored slug the set does not know as 未知 logo, with 無 not pressed', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, logo: 'china-airlines' } as AccountDetail);
    const current = el(fixture).querySelector<HTMLButtonElement>('.logo-current')!;
    expect(current.textContent).toContain('未知 logo');
    expect(current.getAttribute('aria-label')).toBe('品牌 logo：未知 logo');
    current.click();
    fixture.detectChanges();
    const cells = Array.from(el(fixture).querySelectorAll<HTMLButtonElement>('.logo-grid button'));
    expect(cells[0].textContent?.trim()).toBe('無');
    expect(cells[0].getAttribute('aria-pressed')).toBe('false');
    expect(cells.filter(c => c.getAttribute('aria-pressed') === 'true')).toEqual([]);
  });

  it('picking a logo updates the big icon and the saved payload', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    el(fixture).querySelector<HTMLButtonElement>('.logo-current')!.click();
    fixture.detectChanges();
    const target = ACCOUNT_LOGOS[1];
    el(fixture).querySelector<HTMLButtonElement>(`.logo-grid button[aria-label="${target.label}"]`)!.click();
    fixture.detectChanges();
    expect(el(fixture).querySelector('.logo-grid')).toBeNull();
    expect(el(fixture).querySelector<HTMLImageElement>('.bigicon app-account-icon img')!.getAttribute('src')).toMatch(new RegExp(`${target.file}$`));
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    const req = http.expectOne('/api/accounting/accounts/11');
    expect(req.request.body.logo).toBe(target.slug);
    req.flush(DETAIL);
  });

  it('無 clears the logo and keeps the emoji', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, logo: ACCOUNT_LOGOS[0].slug } as AccountDetail);
    el(fixture).querySelector<HTMLButtonElement>('.logo-current')!.click();
    fixture.detectChanges();
    el(fixture).querySelector<HTMLButtonElement>('.logo-grid button')!.click();
    fixture.detectChanges();
    expect(el(fixture).querySelector('.bigicon app-account-icon img')).toBeNull();
    expect(el(fixture).querySelector('.bigicon app-account-icon')!.textContent).toContain('💳');
    expect(el(fixture).querySelector('.bigicon app-account-icon .emoji')!.textContent?.trim()).toBe('💳');
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    const req = http.expectOne('/api/accounting/accounts/11');
    expect(req.request.body.logo).toBeNull();
    expect(req.request.body.icon).toBe('💳');
    req.flush(DETAIL);
  });

  it('keeps every digit of balances ≥ 1e12 on the way into the form and back', async () => {
    await setup('11');
    const big = { ...DETAIL, opening_balance: '-1234567890123.4500', credit_limit: '9007199254740993.0000' } as AccountDetail;
    const fixture = render(big);
    expect(el(fixture).querySelector<HTMLInputElement>('.opening-input')!.value).toBe('-1234567890123.45');
    expect(el(fixture).querySelector<HTMLInputElement>('.credit-limit')!.value).toBe('9007199254740993');
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    const req = http.expectOne('/api/accounting/accounts/11');
    expect(req.request.body).toMatchObject({ opening_balance: '-1234567890123.45', credit_limit: '9007199254740993' });
    req.flush(big);
    expect(accountToInput(big)).toMatchObject({ opening_balance: '-1234567890123.45', credit_limit: '9007199254740993' });
  });

  it('formats server decimals as text only', () => {
    expect(decimalText('1234567890123.4567')).toBe('1234567890123.4567');
    expect(decimalText('99999999999999999.0000')).toBe('99999999999999999');
    expect(decimalText('-3693.0000')).toBe('-3693');
    expect(decimalText('1.500')).toBe('1.5');
    expect(decimalText('0007.10')).toBe('7.1');
    expect(decimalText('-0.0000')).toBe('0');
    expect(decimalText('+12.30')).toBe('12.3');
    expect(decimalText('.5')).toBe('0.5');
    expect(decimalText('')).toBe('');
    expect(decimalText(null)).toBe('');
    expect(decimalText('abc')).toBe('abc');
  });

  it('reads today when the page opens, not when the component was built', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    expect(el(fixture).querySelector('.cycle-preview')?.textContent?.trim()).toBe('09/16 – 10/15');

    // The reused pane stays alive across midnight; the next open reads the new date.
    vi.setSystemTime(new Date(2026, 9, 20, 9, 0));
    navigateTo('12');
    http.expectOne('/api/accounting/accounts/12').flush({ ...DETAIL, id: 12 });
    fixture.detectChanges();
    expect(el(fixture).querySelector('.cycle-preview')?.textContent?.trim()).toBe('10/16 – 11/15');
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
    http.expectOne('/api/accounting/projects').flush(PROJECTS);
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
    http.expectOne('/api/accounting/projects').flush(PROJECTS);
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
  // ---- unsaved input (DirtyAware): N, 記一筆 and the layout close ask first --------------------------------------

  function typeInto(fixture: ComponentFixture<AccountSettingsComponent>, selector: string, value: string): void {
    const input = el(fixture).querySelector<HTMLInputElement>(selector)!;
    input.value = value;
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
  }

  it('a clean form is not dirty: a close requested over it runs at once', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    expect(fixture.componentInstance.isDirty()).toBe(false);

    const run = vi.fn();
    TestBed.inject(DirtyFormRegistry).requestClose(run);
    expect(run).toHaveBeenCalledTimes(1);
  });

  it('a new account form is clean until something is typed', async () => {
    await setup(null);
    const fixture = render(null);
    expect(fixture.componentInstance.isDirty()).toBe(false);

    typeInto(fixture, '.name-input', '悠遊卡');
    expect(fixture.componentInstance.isDirty()).toBe(true);
    typeInto(fixture, '.name-input', '');
    expect(fixture.componentInstance.isDirty()).toBe(false);
  });

  it('an edited name, switch or shared-limit choice makes the form dirty', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    typeInto(fixture, '.name-input', '玉山 Only 2');
    expect(fixture.componentInstance.isDirty()).toBe(true);
    typeInto(fixture, '.name-input', '玉山 Only');
    expect(fixture.componentInstance.isDirty()).toBe(false);

    el(fixture).querySelector<HTMLButtonElement>('.include-toggle')!.click();
    expect(fixture.componentInstance.isDirty()).toBe(true);
    el(fixture).querySelector<HTMLButtonElement>('.include-toggle')!.click();
    expect(fixture.componentInstance.isDirty()).toBe(false);

    fixture.componentInstance.toggleSharing(12, true);
    expect(fixture.componentInstance.isDirty()).toBe(true);
  });

  it('a dirty form shows 放棄未儲存的內容？: 留下 keeps it, 放棄 runs the close', async () => {
    await setup(null);
    const fixture = render(null);
    typeInto(fixture, '.name-input', '悠遊卡');
    const registry = TestBed.inject(DirtyFormRegistry);
    const run = vi.fn();

    registry.requestClose(run);
    fixture.detectChanges();
    const strip = el(fixture).querySelector<HTMLElement>('.discard-strip');
    expect(strip?.textContent).toContain('放棄未儲存的內容？');
    expect(run).not.toHaveBeenCalled();

    el(fixture).querySelector<HTMLButtonElement>('.discard-stay')!.click();
    fixture.detectChanges();
    expect(el(fixture).querySelector('.discard-strip')).toBeNull();
    expect(run).not.toHaveBeenCalled();
    expect(el(fixture).querySelector<HTMLInputElement>('.name-input')!.value).toBe('悠遊卡');

    registry.requestClose(run);
    fixture.detectChanges();
    el(fixture).querySelector<HTMLButtonElement>('.discard-leave')!.click();
    expect(run).toHaveBeenCalledTimes(1);
  });

  it('Esc on the 放棄 strip means 留下 (and is marked handled for the layout)', async () => {
    await setup(null);
    const fixture = render(null);
    typeInto(fixture, '.name-input', '悠遊卡');
    const registry = TestBed.inject(DirtyFormRegistry);
    const run = vi.fn();
    registry.requestClose(run);
    fixture.detectChanges();

    const esc = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    el(fixture).querySelector<HTMLElement>('.discard-stay')!.dispatchEvent(esc);
    fixture.detectChanges();

    expect(esc.defaultPrevented).toBe(true);
    expect(registry.promptOpen()).toBe(false);
    expect(run).not.toHaveBeenCalled();
  });

  it('a saved form is no longer dirty, and leaving the page forgets it', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    typeInto(fixture, '.credit-limit', '350000');
    expect(fixture.componentInstance.isDirty()).toBe(true);
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    http.expectOne('/api/accounting/accounts/11').flush(DETAIL);

    expect(fixture.componentInstance.isDirty()).toBe(false);
    const run = vi.fn();
    const registry = TestBed.inject(DirtyFormRegistry);
    registry.requestClose(run);
    expect(run).toHaveBeenCalledTimes(1);

    typeInto(fixture, '.name-input', '改名');
    fixture.destroy();
    registry.requestClose(run);
    expect(run).toHaveBeenCalledTimes(2);
  });

  it('a failed save keeps the form dirty', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    typeInto(fixture, '.credit-limit', '350000');
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    http.expectOne('/api/accounting/accounts/11').flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });

    expect(fixture.componentInstance.isDirty()).toBe(true);
  });

  // ---- 回饋規則 block (reward rules phase 1, Task 8) --------------------------------------------------------------

  function sheet(fixture: ComponentFixture<AccountSettingsComponent>): RewardRuleSheetComponent | null {
    const debug = fixture.debugElement.query(By.directive(RewardRuleSheetComponent));
    return debug ? (debug.componentInstance as RewardRuleSheetComponent) : null;
  }

  function click(fixture: ComponentFixture<AccountSettingsComponent>, selector: string, index = 0): void {
    el(fixture).querySelectorAll<HTMLElement>(selector)[index].click();
    fixture.detectChanges();
  }

  it('renders one .rule-row per rule with label, state, 基本 and 來自 MOZE badges under 回饋規則 · N 條', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    expect(texts(fixture, '.rules-head .k')).toEqual(['回饋規則 · 2 條']);
    const all = el(fixture).querySelector<HTMLAnchorElement>('.rules-head a')!;
    expect(all.textContent?.trim()).toBe('全部卡片');
    expect(all.getAttribute('href')).toBe('/accounting/reward-rules?account_id=11');
    expect(el(fixture).querySelectorAll('.rule-row').length).toBe(2);
    expect(texts(fixture, '.rule-row .rule-label')).toEqual(['一般 (1%)', '餐飲 (5.2%)']);
    expect(texts(fixture, '.rule-row .rule-posting')).toEqual(['帳單結帳後第 1 月 15 日', '交易後 3 天']);
    expect(texts(fixture, '.rule-row .rule-period')).toEqual(['無期限', '2026/01/01 – 2026/12/31']);
    const states = Array.from(el(fixture).querySelectorAll('.rule-row .rule-state'));
    expect(states.map(s => [s.getAttribute('data-state'), s.textContent?.trim()])).toEqual([
      ['active', '啟用中'],
      ['disabled', '已停用'],
    ]);
    const rows = Array.from(el(fixture).querySelectorAll('.rule-row'));
    expect(rows.map(r => r.querySelector('.rule-basic')?.textContent?.trim() ?? null)).toEqual(['基本', null]);
    expect(rows.map(r => r.querySelector('.rule-moze')?.textContent?.trim() ?? null)).toEqual([null, '來自 MOZE']);
    expect(rows.map(r => r.querySelector('app-switch.rule-enabled button')?.getAttribute('aria-checked'))).toEqual(['true', 'false']);
    expect(texts(fixture, '.rule-row .rule-edit')).toEqual(['編輯', '編輯']);
    expect(el(fixture).querySelectorAll<HTMLButtonElement>('.rule-row .move-up')[0].disabled).toBe(true);
    expect(el(fixture).querySelectorAll<HTMLButtonElement>('.rule-row .move-up')[1].disabled).toBe(false);
    expect(el(fixture).querySelectorAll<HTMLButtonElement>('.rule-row .move-down')[0].disabled).toBe(false);
    expect(el(fixture).querySelectorAll<HTMLButtonElement>('.rule-row .move-down')[1].disabled).toBe(true);
    expect(texts(fixture, '.rule-add')).toEqual(['新增規則']);
  });

  it('new-account mode shows only 儲存帳戶後即可新增回饋規則, without 新增規則', async () => {
    await setup(null);
    const fixture = render(null);

    expect(el(fixture).textContent).toContain('儲存帳戶後即可新增回饋規則');
    expect(el(fixture).querySelector('.rule-add')).toBeNull();
    expect(el(fixture).querySelector('.rule-row')).toBeNull();
    expect(el(fixture).querySelector('.rules-head')).toBeNull();
    expect(el(fixture).textContent).not.toContain('新增規則');
  });

  it('the 啟用 switch saves the full rule with is_enabled flipped, then re-reads the account', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    click(fixture, '.rule-row app-switch.rule-enabled button', 0);
    const put = http.expectOne('/api/accounting/accounts/11/reward-rules/1');
    expect(put.request.method).toBe('PUT');
    expect(put.request.body).toEqual({
      name: '一般', method: 'percent', rate: '1', fixed_amount: null, posting: 'after_window', delay_days: 0,
      post_month_offset: 1, post_day: 15, txn_rounding: 'round', total_rounding: 'keep', total_cap: null,
      is_basic: true, reward_account_id: null, reward_project_id: null, starts_on: null, ends_on: null,
      is_enabled: false, description: null,
    });
    put.flush({ ...DETAIL.reward_rules[0], is_enabled: false });
    const reread = http.expectOne('/api/accounting/accounts/11');
    expect(reread.request.method).toBe('GET');
    reread.flush({
      ...DETAIL,
      reward_rules: [{ ...DETAIL.reward_rules[0], is_enabled: false }, DETAIL.reward_rules[1]],
    });
    fixture.detectChanges();

    expect(texts(fixture, '.rule-row .rule-state')).toEqual(['已停用', '已停用']);
    expect(fixture.componentInstance.isDirty()).toBe(false);
  });

  it('a failed 啟用 write shows the error and re-reads the account', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    click(fixture, '.rule-row app-switch.rule-enabled button', 1);
    const put = http.expectOne('/api/accounting/accounts/11/reward-rules/2');
    expect(put.request.body).toMatchObject({ is_enabled: true, name: '餐飲' });
    put.flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    http.expectOne('/api/accounting/accounts/11').flush(DETAIL);
    fixture.detectChanges();

    expect(el(fixture).querySelector('.rule-message')?.textContent?.trim()).toBeTruthy();
    expect(texts(fixture, '.rule-row .rule-state')).toEqual(['啟用中', '已停用']);
  });

  it('下移 on the first row sends the swapped order and re-renders from the response', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    click(fixture, '.rule-row .move-down', 0);
    const put = http.expectOne('/api/accounting/accounts/11/reward-rules/order');
    expect(put.request.method).toBe('PUT');
    expect(put.request.body).toEqual({ ids: [2, 1] });
    put.flush([
      { ...DETAIL.reward_rules[1], sort_order: 0 },
      { ...DETAIL.reward_rules[0], sort_order: 1 },
    ]);
    fixture.detectChanges();

    expect(texts(fixture, '.rule-row .rule-label')).toEqual(['餐飲 (5.2%)', '一般 (1%)']);
    expect(el(fixture).querySelectorAll<HTMLButtonElement>('.rule-row .move-up')[0].disabled).toBe(true);
    expect(el(fixture).querySelectorAll<HTMLButtonElement>('.rule-row .move-down')[1].disabled).toBe(true);
  });

  it('a failed reorder shows the error and re-reads the account', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    click(fixture, '.rule-row .move-up', 1);
    const put = http.expectOne('/api/accounting/accounts/11/reward-rules/order');
    expect(put.request.body).toEqual({ ids: [2, 1] });
    put.flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    http.expectOne('/api/accounting/accounts/11').flush(DETAIL);
    fixture.detectChanges();

    expect(el(fixture).querySelector('.rule-message')?.textContent?.trim()).toBeTruthy();
    expect(texts(fixture, '.rule-row .rule-label')).toEqual(['一般 (1%)', '餐飲 (5.2%)']);
  });

  it('編輯 opens the sheet with the rule; 新增規則 opens it with rule === null', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    expect(sheet(fixture)).toBeNull();

    click(fixture, '.rule-row .rule-edit', 1);
    const editing = sheet(fixture)!;
    expect(editing.rule()?.id).toBe(2);
    expect(editing.accountId()).toBe(11);
    expect(editing.currency()).toBe('TWD');
    expect(editing.siblings().map(r => r.id)).toEqual([1, 2]);
    expect(editing.accounts()).toEqual(LIST);
    expect(editing.projects()).toEqual(PROJECTS);

    editing.closed.emit();
    fixture.detectChanges();
    expect(sheet(fixture)).toBeNull();

    click(fixture, '.rule-add');
    expect(sheet(fixture)!.rule()).toBeNull();
  });

  it('a saved or deleted rule closes the sheet and re-reads the account', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    click(fixture, '.rule-add');
    sheet(fixture)!.saved.emit(rewardRule({ id: 3, name: '網購', rate: '3.0000' }));
    fixture.detectChanges();
    expect(sheet(fixture)).toBeNull();
    http.expectOne('/api/accounting/accounts/11').flush({
      ...DETAIL,
      reward_rules: [...DETAIL.reward_rules, rewardRule({ id: 3, name: '網購', rate: '3.0000', sort_order: 2 })],
    });
    fixture.detectChanges();
    expect(texts(fixture, '.rules-head .k')).toEqual(['回饋規則 · 3 條']);

    click(fixture, '.rule-row .rule-edit', 2);
    sheet(fixture)!.deleted.emit(3);
    fixture.detectChanges();
    expect(sheet(fixture)).toBeNull();
    http.expectOne('/api/accounting/accounts/11').flush(DETAIL);
    fixture.detectChanges();
    expect(texts(fixture, '.rules-head .k')).toEqual(['回饋規則 · 2 條']);
  });

  it('an open rule sheet with unsaved input makes the page dirty; closing it does not', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    click(fixture, '.rule-row .rule-edit', 0);
    expect(fixture.componentInstance.isDirty()).toBe(false);
    typeInto(fixture, '.rule-name', '一般改');
    expect(fixture.componentInstance.isDirty()).toBe(true);

    sheet(fixture)!.closed.emit();
    fixture.detectChanges();
    expect(sheet(fixture)).toBeNull();
    expect(fixture.componentInstance.isDirty()).toBe(false);
  });

  /** Strips the owner can see: the page's .discard-strip and the sheet's .discard. */
  function strips(fixture: ComponentFixture<AccountSettingsComponent>): Element[] {
    return Array.from(el(fixture).querySelectorAll('.discard-strip, app-reward-rule-sheet .discard'));
  }

  it('leaving with a dirty rule sheet asks once, on the sheet; its 放棄 closes it and runs the leave', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    const registry = TestBed.inject(DirtyFormRegistry);
    const run = vi.fn();

    click(fixture, '.rule-row .rule-edit', 0);
    typeInto(fixture, '.rule-name', '一般改');
    registry.requestClose(run);
    fixture.detectChanges();

    const shown = strips(fixture);
    expect(shown.length).toBe(1);
    expect(shown[0].closest('app-reward-rule-sheet')).not.toBeNull();
    expect(shown[0].textContent).toContain('放棄未儲存的內容？');
    expect(run).not.toHaveBeenCalled();

    click(fixture, 'app-reward-rule-sheet .discard-leave');
    expect(sheet(fixture)).toBeNull();
    expect(fixture.componentInstance.isDirty()).toBe(false);
    expect(run).toHaveBeenCalledTimes(1);
    expect(registry.promptOpen()).toBe(false);
    expect(strips(fixture)).toEqual([]);
  });

  it('留下 on the delegated sheet strip keeps the sheet and its input and forgets the leave', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    const registry = TestBed.inject(DirtyFormRegistry);
    const run = vi.fn();

    click(fixture, '.rule-row .rule-edit', 0);
    typeInto(fixture, '.rule-name', '一般改');
    registry.requestClose(run);
    fixture.detectChanges();
    click(fixture, 'app-reward-rule-sheet .discard-stay');

    expect(sheet(fixture)).not.toBeNull();
    expect(el(fixture).querySelector<HTMLInputElement>('.rule-name')!.value).toBe('一般改');
    expect(registry.promptOpen()).toBe(false);
    expect(strips(fixture)).toEqual([]);
    expect(run).not.toHaveBeenCalled();
  });

  it('a dirty page form under a dirty sheet: the sheet asks first, then the page asks for its own input', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    const registry = TestBed.inject(DirtyFormRegistry);
    const run = vi.fn();

    typeInto(fixture, '.name-input', '玉山 Only 2');
    click(fixture, '.rule-row .rule-edit', 0);
    typeInto(fixture, '.rule-name', '一般改');
    registry.requestClose(run);
    fixture.detectChanges();
    click(fixture, 'app-reward-rule-sheet .discard-leave');

    expect(sheet(fixture)).toBeNull();
    expect(run).not.toHaveBeenCalled();
    const shown = strips(fixture);
    expect(shown.length).toBe(1);
    expect(shown[0].classList).toContain('discard-strip');

    click(fixture, '.discard-strip .discard-leave');
    expect(run).toHaveBeenCalledTimes(1);
  });

  it('?rule=2 opens the sheet for rule 2 once the detail loads and clears the param on the settings page', async () => {
    await setup('11', { rule: '2' });
    const fixture = render(DETAIL);

    expect(sheet(fixture)?.rule()?.id).toBe(2);
    expect(navigate).toHaveBeenCalledTimes(1);
    const [commands, extras] = navigate.mock.calls[0];
    expect(commands).toEqual([]);
    expect(extras?.relativeTo).toBe(TestBed.inject(ActivatedRoute));
    expect(extras?.queryParams).toEqual({ rule: null });
    expect(extras?.queryParamsHandling).toBe('merge');
    expect(extras?.replaceUrl).toBe(true);
  });

  it('?rule= naming no rule of the account is ignored and cleared', async () => {
    await setup('11', { rule: '99' });
    const fixture = render(DETAIL);

    expect(sheet(fixture)).toBeNull();
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate.mock.calls[0][1]?.queryParams).toEqual({ rule: null });
  });

  it('an expired rule shows the 已到期 badge in the warning colour', async () => {
    await setup('11');
    const expired = rewardRule({ id: 3, name: '夏季', rate: '2.0000', starts_on: '2026-06-01', ends_on: '2026-09-30', sort_order: 2 });
    const fixture = render({ ...DETAIL, reward_rules: [...DETAIL.reward_rules, expired] } as AccountDetail);

    const badge = el(fixture).querySelectorAll('.rule-row .rule-state')[2];
    expect(badge.textContent?.trim()).toBe('已到期');
    expect(badge.getAttribute('data-state')).toBe('expired');
    expect(badge.classList.contains('warn')).toBe(true);
    expect(el(fixture).querySelectorAll('.rule-row .rule-state.warn').length).toBe(1);
  });
});
