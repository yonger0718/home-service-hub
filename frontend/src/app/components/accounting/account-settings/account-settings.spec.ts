import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, ParamMap, Router, RouterLink, UrlTree, convertToParamMap, provideRouter } from '@angular/router';
import { By } from '@angular/platform-browser';
import { BehaviorSubject } from 'rxjs';
import { AccountingToastService } from '../accounting-toast';
import { MockInstance, afterEach, describe, expect, it, onTestFinished, vi } from 'vitest';

import { AccountDetail, LedgerAccount, RewardRule, RewardUsage } from '../../../models/accounting.model';
import { ACCOUNT_LOGOS } from '../../../theme/account-logos';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { AccountingService } from '../../../services/accounting.service';
import { focusables } from '../accounting-ui';
import { DirtyFormRegistry } from '../dirty-form.service';
import { RewardRuleSheetComponent } from '../reward-rule-sheet/reward-rule-sheet';
import { pullGrab } from '../sheet-pull.testing';
import { AccountSettingsComponent, accountToInput, decimalText, safeBack } from './account-settings';

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
    id: 0, account_id: 11, name: '', method: 'percent', rate: null, fixed_amount: null, unit_spend: null, window: 'statement_cycle',
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
    // The usage lines are secondary to what these specs assert; settle any still-open usage request.
    const stray = http.match(r => r.url.endsWith('/reward-usage'));
    expect(stray.length).toBeLessThanOrEqual(1);
    for (const req of stray) {
      req.flush([]);
    }
    http.verify();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
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

  function render(detail: AccountDetail | null, usage: RewardUsage[] = []): ComponentFixture<AccountSettingsComponent> {
    const fixture = TestBed.createComponent(AccountSettingsComponent);
    fixture.detectChanges();
    if (detail) {
      http.expectOne(`/api/accounting/accounts/${detail.id}`).flush(detail);
      if (detail.reward_rules.length > 0) {
        http.expectOne(`/api/accounting/accounts/${detail.id}/reward-usage`).flush(usage);
      }
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

  /** Opens a 外觀 source tab (上傳圖片 / 內建 logo / 文字＋顏色) via its seg radio. */
  function openSource(fixture: ComponentFixture<AccountSettingsComponent>, label: string): void {
    Array.from(el(fixture).querySelectorAll<HTMLButtonElement>('#s-look .seg-toggle [role=radio]'))
      .find(b => b.textContent?.trim() === label)!
      .click();
    fixture.detectChanges();
  }

  function texts(fixture: ComponentFixture<AccountSettingsComponent>, selector: string): string[] {
    return Array.from(el(fixture).querySelectorAll(selector)).map(n => n.textContent?.replace(/\s+/g, ' ').trim() ?? '');
  }

  function childLabels(fixture: ComponentFixture<AccountSettingsComponent>): string[] {
    return Array.from(el(fixture).querySelectorAll('.rw.child .k')).map(k => k.textContent?.trim() ?? '');
  }

  /** Clicks the inner button of the `app-switch` carrying `cls`. */
  function flip(fixture: ComponentFixture<AccountSettingsComponent>, cls: string): void {
    el(fixture).querySelector<HTMLButtonElement>(`app-switch.${cls} button`)!.click();
    fixture.detectChanges();
  }

  /** The value button text of the `app-stepper` carrying `cls` (e.g. `15 號`, `月底`). */
  function stepperText(fixture: ComponentFixture<AccountSettingsComponent>, cls: string): string {
    return el(fixture).querySelector(`app-stepper.${cls} .val`)?.textContent?.trim() ?? '';
  }

  function stepperClick(fixture: ComponentFixture<AccountSettingsComponent>, cls: string, button: 'inc' | 'dec'): void {
    el(fixture).querySelector<HTMLButtonElement>(`app-stepper.${cls} .${button}`)!.click();
    fixture.detectChanges();
  }

  /** Tap-to-type: opens the stepper's input, types `text` and commits with Enter. */
  function stepperType(fixture: ComponentFixture<AccountSettingsComponent>, cls: string, text: string): void {
    el(fixture).querySelector<HTMLButtonElement>(`app-stepper.${cls} .val`)!.click();
    fixture.detectChanges();
    const input = el(fixture).querySelector<HTMLInputElement>(`app-stepper.${cls} .val-input`)!;
    input.value = text;
    input.dispatchEvent(new Event('input'));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    fixture.detectChanges();
  }

  /** Opens the `app-picker` carrying `cls` and returns its options. */
  function pickerOptions(fixture: ComponentFixture<AccountSettingsComponent>, cls: string): HTMLElement[] {
    el(fixture).querySelector<HTMLButtonElement>(`app-picker.${cls} .pk-trigger`)!.click();
    fixture.detectChanges();
    return Array.from(el(fixture).querySelectorAll<HTMLElement>(`app-picker.${cls} [role=option]`));
  }

  function pickerTrigger(fixture: ComponentFixture<AccountSettingsComponent>, cls: string): HTMLButtonElement {
    return el(fixture).querySelector<HTMLButtonElement>(`app-picker.${cls} .pk-trigger`)!;
  }

  function segPick(fixture: ComponentFixture<AccountSettingsComponent>, cls: string, label: string): void {
    const radios = Array.from(el(fixture).querySelectorAll<HTMLButtonElement>(`app-seg-toggle.${cls} [role=radio]`));
    radios.find(r => r.textContent?.trim() === label)!.click();
    fixture.detectChanges();
  }

  function amount(fixture: ComponentFixture<AccountSettingsComponent>, cls: string): HTMLInputElement {
    return el(fixture).querySelector<HTMLInputElement>(`app-amount-field.${cls} .amt`)!;
  }

  it('reveals the card rows when 信用帳戶 is switched on', async () => {
    await setup(null);
    const fixture = render(null);
    expect(childLabels(fixture).filter(label => label !== '手續費 %')).toEqual([]);

    flip(fixture, 'credit-toggle');

    expect(childLabels(fixture)).toEqual(['繳款期限', '信用額度', '額度共用', '主帳戶', '自動扣繳']);
  });

  it('due preview: cycle 本期 09/16–10/15 and 結帳日後 20 天 → 11/04', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    expect(el(fixture).querySelector('.cycle-preview')?.textContent?.trim()).toBe('本期 09/16–10/15');
    expect(el(fixture).querySelector('.due-preview')?.textContent?.trim()).toBe('結帳日後 20 天 → 11/04');
    expect(texts(fixture, 'app-seg-toggle.due-rule [role=radio]')).toEqual(['未設定', '每月固定日', '結帳後']);
    expect(texts(fixture, 'app-seg-toggle.due-rule [aria-checked=true]')).toEqual(['結帳後']);
    expect(stepperText(fixture, 'due-value')).toBe('20 天');

    segPick(fixture, 'due-rule', '每月固定日');
    expect(stepperText(fixture, 'due-value')).toBe('20 號');
    stepperType(fixture, 'due-value', '5');
    expect(stepperText(fixture, 'due-value')).toBe('5 號');
    expect(el(fixture).querySelector('.due-preview')?.textContent?.trim()).toBe('每月 5 日 → 11/05');
  });

  it('closing day 25 on 2026-11-07, 加一 → stepper reads 26 號 and cycle line reads 本期 10/27–11/26', async () => {
    await setup('11');
    vi.setSystemTime(new Date(2026, 10, 7, 10, 0));
    const fixture = render({ ...DETAIL, closing_day: 25 } as AccountDetail);
    expect(stepperText(fixture, 'closing-day')).toBe('25 號');
    expect(el(fixture).querySelector('.cycle-preview')?.textContent?.trim()).toBe('本期 10/26–11/25');

    expect(el(fixture).querySelector('app-stepper.closing-day .inc')?.getAttribute('aria-label')).toBe('加一天');
    stepperClick(fixture, 'closing-day', 'inc');
    expect(stepperText(fixture, 'closing-day')).toBe('26 號');
    expect(el(fixture).querySelector('.cycle-preview')?.textContent?.trim()).toBe('本期 10/27–11/26');
  });

  it('account with closing_day null shows 月底 and 每月 1 日 – 月底, saves closing_day null', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, closing_day: null } as AccountDetail);
    expect(stepperText(fixture, 'closing-day')).toBe('月底');
    expect(el(fixture).querySelector('.cycle-preview')?.textContent?.trim()).toBe('每月 1 日 – 月底');

    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    const req = http.expectOne('/api/accounting/accounts/11');
    expect(req.request.body.closing_day).toBeNull();
    req.flush(DETAIL);
  });

  it('account with closing_day 31 shows 31 號 and saves 31', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, closing_day: 31 } as AccountDetail);
    expect(stepperText(fixture, 'closing-day')).toBe('31 號');
    expect(el(fixture).querySelector<HTMLButtonElement>('app-stepper.closing-day .inc')!.disabled).toBe(true);
    expect(el(fixture).querySelector('.cycle-preview')?.textContent?.trim()).toBe('本期 10/01–10/31');

    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    const req = http.expectOne('/api/accounting/accounts/11');
    expect(req.request.body.closing_day).toBe(31);
    req.flush(DETAIL);
  });

  it('未設定 saves due_rule null and due_value null', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    segPick(fixture, 'due-rule', '未設定');
    expect(el(fixture).querySelector('app-stepper.due-value')).toBeNull();
    expect(el(fixture).querySelector('.due-preview')?.textContent?.trim()).toBe('');

    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    const req = http.expectOne('/api/accounting/accounts/11');
    expect(req.request.body).toMatchObject({ due_rule: null, due_value: null });
    req.flush(DETAIL);
  });

  it('結帳後 allows up to 120 days and 每月固定日 up to 31', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, due_value: 119 } as AccountDetail);
    stepperClick(fixture, 'due-value', 'inc');
    expect(stepperText(fixture, 'due-value')).toBe('120 天');
    expect(el(fixture).querySelector<HTMLButtonElement>('app-stepper.due-value .inc')!.disabled).toBe(true);

    segPick(fixture, 'due-rule', '每月固定日');
    stepperType(fixture, 'due-value', '45');
    expect(stepperText(fixture, 'due-value')).toBe('31 號');
  });

  it('信用帳戶 off then on restores 繳款期限 and 信用額度', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    flip(fixture, 'credit-toggle');
    expect(el(fixture).querySelector('app-seg-toggle.due-rule')).toBeNull();
    expect(el(fixture).querySelector('app-amount-field.credit-limit')).toBeNull();

    flip(fixture, 'credit-toggle');
    expect(texts(fixture, 'app-seg-toggle.due-rule [aria-checked=true]')).toEqual(['結帳後']);
    expect(stepperText(fixture, 'due-value')).toBe('20 天');
    expect(amount(fixture, 'credit-limit').value).toBe('300,000');
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

    expect(pickerTrigger(fixture, 'master-select').getAttribute('data-value')).toBe('10');
    const masters = pickerOptions(fixture, 'master-select');
    expect(masters.map(o => o.textContent?.trim())).toEqual(['無', '玉山信用卡', '玉山 UNI', '玉山 U Bear']);
    expect(masters.map(o => o.getAttribute('data-value'))).toEqual(['', '10', '12', '13']);
    masters[2].click();
    fixture.detectChanges();
    expect(pickerTrigger(fixture, 'master-select').getAttribute('data-value')).toBe('12');
    expect(fixture.componentInstance.form().combined_account_id).toBe(12);
    expect(texts(fixture, '.rule-row .rule-label')).toEqual(['一般', '餐飲']);
    expect(texts(fixture, '.rule-row .rule-rate')).toEqual(['1%', '5.2%']);
    // Only a non-active rule carries a state badge.
    expect(texts(fixture, '.rule-row .rule-state')).toEqual(['已停用']);
    expect(el(fixture).textContent).not.toContain('（停用）');
  });

  it('keeps an archived 主帳戶 and 自動扣繳 selectable, labelled （已封存）, and saves them unchanged', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, combined_account_id: 30, auto_pay_account_id: 31 } as AccountDetail);

    expect(pickerTrigger(fixture, 'master-select').getAttribute('data-value')).toBe('30');
    expect(pickerTrigger(fixture, 'master-select').textContent?.trim()).toBe('舊卡（已封存）');
    expect(pickerTrigger(fixture, 'autopay-select').getAttribute('data-value')).toBe('31');
    expect(pickerTrigger(fixture, 'autopay-select').textContent?.trim()).toBe('舊帳戶（已封存）');
    const autopay = pickerOptions(fixture, 'autopay-select');
    expect(autopay.map(o => o.textContent?.trim())).toEqual(['無', '玉山銀行', '舊帳戶（已封存）']);
    expect(autopay.find(o => o.getAttribute('aria-selected') === 'true')?.getAttribute('data-value')).toBe('31');

    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    const req = http.expectOne('/api/accounting/accounts/11');
    expect(req.request.body).toMatchObject({ combined_account_id: 30, auto_pay_account_id: 31 });
    req.flush(DETAIL);
  });

  it('帳戶分組 picker lists 未分組 and the groups, and saves the chosen group', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    expect(pickerTrigger(fixture, 'group-select').getAttribute('data-value')).toBe('3');
    expect(pickerTrigger(fixture, 'group-select').textContent?.trim()).toBe('信用卡');
    const groups = pickerOptions(fixture, 'group-select');
    expect(groups.map(o => o.textContent?.trim())).toEqual(['未分組', '現金', '信用卡']);
    expect(groups.map(o => o.getAttribute('data-value'))).toEqual(['', '1', '3']);
    groups[0].click();
    fixture.detectChanges();
    expect(pickerTrigger(fixture, 'group-select').getAttribute('data-value')).toBe('');

    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    const req = http.expectOne('/api/accounting/accounts/11');
    expect(req.request.body.group_id).toBeNull();
    req.flush(DETAIL);
  });

  it('fx rows hidden until the fee switch is on', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, fx_fee_pct: null, fx_fee_rounding: null } as AccountDetail);
    expect(el(fixture).querySelector('app-amount-field.fx-pct')).toBeNull();
    expect(el(fixture).querySelector('app-picker.fx-rounding')).toBeNull();
    expect(el(fixture).querySelector('app-switch.fx-refund-toggle')).toBeNull();
    flip(fixture, 'fx-toggle');
    expect(el(fixture).querySelector('app-amount-field.fx-pct')).not.toBeNull();
    expect(el(fixture).querySelector('app-picker.fx-rounding')).not.toBeNull();
    expect(el(fixture).querySelector('app-switch.fx-refund-toggle')).not.toBeNull();
    flip(fixture, 'fx-refund-toggle');
    expect(el(fixture).querySelector('app-switch.fx-refund-toggle button')!.getAttribute('aria-checked')).toBe('true');
  });

  it('fee percent typed 1.5 saves fx_fee_pct \'1.5\'', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, fx_fee_pct: '2.000' } as AccountDetail);
    expect(amount(fixture, 'fx-pct').value).toBe('2');
    typeInto(fixture, '.fx-pct .amt', '1.5');
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    const req = http.expectOne('/api/accounting/accounts/11');
    expect(req.request.body.fx_fee_pct).toBe('1.5');
    req.flush(DETAIL);
  });

  it('計算方式 picker lists the four rounding labels and saves the chosen mode', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    const options = pickerOptions(fixture, 'fx-rounding');
    expect(options.map(o => o.textContent?.trim())).toEqual(['不處理', '四捨五入', '無條件捨去', '無條件進位']);
    options.find(o => o.getAttribute('data-value') === 'ceil')!.click();
    fixture.detectChanges();
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    const req = http.expectOne('/api/accounting/accounts/11');
    expect(req.request.body.fx_fee_rounding).toBe('ceil');
    req.flush(DETAIL);
  });

  it('其他 holds 備註, 還原 MOZE 設定 (when flagged), 封存 switch and 刪除 button', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    expect(el(fixture).querySelector('#s-more input.note-input')).not.toBeNull();
    expect(el(fixture).querySelector('#s-more .reset-flag')).not.toBeNull();
    expect(el(fixture).querySelector('#s-more app-switch.archive-toggle')).not.toBeNull();
    expect(el(fixture).querySelector('#s-more .delete-account')).not.toBeNull();
    flip(fixture, 'archive-toggle');
    expect(fixture.componentInstance.form().is_archived).toBe(true);
    const unflagged = render({ ...DETAIL, settings_locally_edited: false } as AccountDetail);
    expect(el(unflagged).querySelector('#s-more .reset-flag')).toBeNull();
  });

  it('saves every field with PUT and returns to the passbook', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    typeInto(fixture, '.credit-limit .amt', '350,000');
    expect(amount(fixture, 'credit-limit').value).toBe('350,000');
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
    openSource(fixture, '文字＋顏色');
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
    expect(el(fixture).querySelector('.look-current')!.textContent?.trim()).toBe('目前顯示：內建 logo · 國泰世華');
    const cells = Array.from(el(fixture).querySelectorAll<HTMLButtonElement>('.logo-grid .logo-opt[role=radio]'));
    expect(cells[0].textContent?.trim()).toBe('無');
    expect(cells.length).toBe(ACCOUNT_LOGOS.length + 1);
    expect(cells.find(c => c.getAttribute('aria-label') === '國泰世華')!.getAttribute('aria-checked')).toBe('true');
    expect(cells[0].getAttribute('aria-checked')).toBe('false');
  });

  it('state line reads 無 and shows the account icon when no logo is set', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, icon: '🍱', logo: null } as AccountDetail);
    expect(el(fixture).querySelector('.look-current')!.textContent?.trim()).toBe('目前顯示：文字 🍱');
    expect(el(fixture).querySelector('.look-tile app-account-icon .emoji')!.textContent).toBe('🍱');
  });

  it('labels a stored slug the set does not know as 未知 logo, with 無 not pressed', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, logo: 'china-airlines' } as AccountDetail);
    expect(el(fixture).querySelector('.look-current')!.textContent?.trim()).toBe('目前顯示：內建 logo · 未知 logo');
    const cells = Array.from(el(fixture).querySelectorAll<HTMLButtonElement>('.logo-grid .logo-opt[role=radio]'));
    expect(cells[0].textContent?.trim()).toBe('無');
    expect(cells[0].getAttribute('aria-checked')).toBe('false');
    expect(cells.filter(c => c.getAttribute('aria-checked') === 'true')).toEqual([]);
  });

  it('picking a logo updates the big icon and the saved payload', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    openSource(fixture, '內建 logo');
    const target = ACCOUNT_LOGOS[1];
    el(fixture).querySelector<HTMLButtonElement>(`.logo-grid .logo-opt[aria-label="${target.label}"]`)!.click();
    fixture.detectChanges();
    expect(el(fixture).querySelector('.logo-grid')).not.toBeNull();
    expect(el(fixture).querySelector<HTMLImageElement>('.bigicon app-account-icon img')!.getAttribute('src')).toMatch(new RegExp(`${target.file}$`));
    expect(el(fixture).querySelector<HTMLImageElement>('.look-tile app-account-icon img')!.getAttribute('src')).toMatch(new RegExp(`${target.file}$`));
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    const req = http.expectOne('/api/accounting/accounts/11');
    expect(req.request.body.logo).toBe(target.slug);
    req.flush(DETAIL);
  });

  it('無 clears the logo and keeps the emoji', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, logo: ACCOUNT_LOGOS[0].slug } as AccountDetail);
    el(fixture).querySelector<HTMLButtonElement>('.logo-grid .logo-opt')!.click();
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

  it('picking a swatch dirties the form and saves the colour', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    openSource(fixture, '文字＋顏色');
    el(fixture).querySelector<HTMLButtonElement>('.swatch[aria-label="靛紫"]')!.click();
    fixture.detectChanges();
    expect(texts(fixture, '.topbar .dirty-count')).toEqual(['有 1 項未儲存']);
    el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
    const req = http.expectOne('/api/accounting/accounts/11');
    expect(req.request.body.color).toBe('#533afd');
    req.flush(DETAIL);
  });

  it('a MOZE uppercase colour marks its swatch', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, color: '#4F8F8A' } as AccountDetail);
    const checked = Array.from(el(fixture).querySelectorAll<HTMLButtonElement>('.swatch[role=radio]'))
      .filter(b => b.getAttribute('aria-checked') === 'true')
      .map(b => b.getAttribute('aria-label'));
    expect(checked).toEqual(['青綠']);
  });

  it('switching to another account re-derives the tab', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    expect(el(fixture).querySelector('.icon-input')).not.toBeNull();
    expect(el(fixture).querySelector('.logo-grid')).toBeNull();
    navigateTo('12');
    http.expectOne('/api/accounting/accounts/12').flush({ ...DETAIL, id: 12, logo: 'cathay-united' });
    fixture.detectChanges();
    expect(el(fixture).querySelector('.logo-grid')).not.toBeNull();
    expect(el(fixture).querySelector('.icon-input')).toBeNull();
    expect(texts(fixture, '#s-look .seg-toggle [role=radio][aria-checked=true]')).toEqual(['內建 logo']);
  });

  it('keeps every digit of balances ≥ 1e12 on the way into the form and back', async () => {
    await setup('11');
    const big = { ...DETAIL, opening_balance: '-1234567890123.4500', credit_limit: '9007199254740993.0000' } as AccountDetail;
    const fixture = render(big);
    expect(amount(fixture, 'opening-input').value).toBe('-1,234,567,890,123.45');
    expect(amount(fixture, 'credit-limit').value).toBe('9,007,199,254,740,993');
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
    expect(el(fixture).querySelector('.cycle-preview')?.textContent?.trim()).toBe('本期 09/16–10/15');

    // The reused pane stays alive across midnight; the next open reads the new date.
    vi.setSystemTime(new Date(2026, 9, 20, 9, 0));
    navigateTo('12');
    http.expectOne('/api/accounting/accounts/12').flush({ ...DETAIL, id: 12 });
    fixture.detectChanges();
    expect(el(fixture).querySelector('.cycle-preview')?.textContent?.trim()).toBe('本期 10/16–11/15');
  });

  it('信用帳戶 off keeps 帳單週期 in 基本 and PUTs closing_day 15', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, entry_count: 0 } as AccountDetail);
    flip(fixture, 'credit-toggle');
    expect(el(fixture).querySelector('#s-basic app-stepper.closing-day')).not.toBeNull();
    expect(stepperText(fixture, 'closing-day')).toBe('15 號');
    expect(el(fixture).querySelector('#s-basic .cycle-preview')?.textContent?.trim()).toBe('本期 09/16–10/15');
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

  it('a typed 0 in the 結帳日 and 繳款期限 steppers clamps to 1, without an error', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    stepperType(fixture, 'closing-day', '0');
    expect(stepperText(fixture, 'closing-day')).toBe('1 號');
    expect(fixture.componentInstance.form().closing_day).toBe('1');
    stepperType(fixture, 'due-value', '0');
    expect(stepperText(fixture, 'due-value')).toBe('1 天');
    expect(fixture.componentInstance.form().due_value).toBe('1');
    expect(el(fixture).querySelector('.field-error[data-field="closing_day"]')).toBeNull();
    expect(el(fixture).querySelector('.field-error[data-field="due_value"]')).toBeNull();
    expect(el(fixture).querySelector('.cycle-preview')?.textContent?.trim()).toBe('本期 10/02–11/01');
  });

  it('validates the closing day and the due value inline, without a garbage preview', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    const component = fixture.componentInstance;

    component.set('closing_day', '0');
    fixture.detectChanges();
    expect(el(fixture).querySelector('.field-error[data-field="closing_day"]')?.textContent?.trim()).toBe('結帳日須為 1–31');
    expect(el(fixture).querySelector('.cycle-preview')?.textContent?.trim()).toBe('');
    component.set('closing_day', '15');
    component.set('due_value', '0');
    fixture.detectChanges();
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

    flip(fixture, 'include-toggle');
    expect(fixture.componentInstance.isDirty()).toBe(true);
    flip(fixture, 'include-toggle');
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
    typeInto(fixture, '.credit-limit .amt', '350000');
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
    typeInto(fixture, '.credit-limit .amt', '350000');
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

  it('回饋規則 header shows the current window', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    expect(texts(fixture, '.rules-head .k')).toEqual(['本期 09/16–10/15']);
    expect(texts(fixture, '.rules-head .rules-all')).toEqual(['所有卡片的規則 ›']);
  });

  it('回饋規則 header reads 每月 1 日 – 月底 without a closing day', async () => {
    await setup('11');
    const fixture = render({ ...DETAIL, closing_day: null } as AccountDetail);
    expect(texts(fixture, '.rules-head .k')).toEqual(['每月 1 日 – 月底']);
  });

  it('renders one .rule-row per rule with label, state, 基本 and 來自 MOZE badges under the 回饋規則 header', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    expect(texts(fixture, '.rules-head .k')).toEqual(['本期 09/16–10/15']);
    const all = el(fixture).querySelector<HTMLAnchorElement>('.rules-head a')!;
    expect(all.textContent?.trim()).toBe('所有卡片的規則 ›');
    expect(all.getAttribute('href')).toBe('/accounting/reward-rules?account_id=11');
    expect(el(fixture).querySelectorAll('.rule-row').length).toBe(2);
    expect(texts(fixture, '.rule-row .rule-label')).toEqual(['一般', '餐飲']);
    expect(texts(fixture, '.rule-row .rule-rate')).toEqual(['1%', '5.2%']);
    expect(texts(fixture, '.rule-row .rule-posting')).toEqual(['· 帳單結帳後第 1 月 15 日', '· 交易後 3 天']);
    const states = Array.from(el(fixture).querySelectorAll('.rule-row .rule-state'));
    expect(states.map(s => [s.getAttribute('data-state'), s.textContent?.trim()])).toEqual([['disabled', '已停用']]);
    const rows = Array.from(el(fixture).querySelectorAll('.rule-row'));
    expect(rows.map(r => r.querySelector('.rule-basic')?.textContent?.trim() ?? null)).toEqual(['基本', null]);
    expect(rows.map(r => r.querySelector('.rule-moze')?.textContent?.trim() ?? null)).toEqual([null, '來自 MOZE']);
    expect(rows.map(r => r.querySelector('app-switch.rule-enabled button')?.getAttribute('aria-checked'))).toEqual(['true', 'false']);
    expect(rows.map(r => r.querySelector('.rule-handle')?.getAttribute('aria-label'))).toEqual(['拖曳排序 一般', '拖曳排序 餐飲']);
    expect(rows.map(r => r.querySelector('.rule-more')?.getAttribute('aria-label'))).toEqual(['一般 更多動作', '餐飲 更多動作']);
    // The old per-row text lines and buttons are gone.
    for (const gone of ['.rule-period', '.rule-window', '.rule-usage', '.rule-edit', '.move-up', '.move-down']) {
      expect(el(fixture).querySelector(gone)).toBeNull();
    }
    expect(texts(fixture, '.rule-add')).toEqual(['新增規則']);
  });

  it('回饋規則 rows render through app-account-rules with 基本 and 來自 MOZE kept', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    const block = el(fixture).querySelector('#s-reward .rules-block')!;
    const list = block.querySelector('app-account-rules')!;
    expect(list).not.toBeNull();
    expect(list.previousElementSibling?.classList).toContain('rules-head');
    expect(block.querySelectorAll('.rule-row').length).toBe(2);
    expect(list.querySelectorAll('.rule-row').length).toBe(2);
    expect(texts(fixture, 'app-account-rules .rule-basic')).toEqual(['基本']);
    expect(texts(fixture, 'app-account-rules .rule-moze')).toEqual(['來自 MOZE']);
    expect(list.querySelector('.rule-basic')!.closest('.rule-row')!.getAttribute('data-rule-id')).toBe('1');
    expect(list.querySelector('.rule-moze')!.closest('.rule-row')!.getAttribute('data-rule-id')).toBe('2');
    // 新增規則 and the message line stay the page's own.
    expect(list.querySelector('.rule-add')).toBeNull();
    expect(block.querySelector(':scope > .rule-add')).not.toBeNull();
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
      name: '一般', method: 'percent', rate: '1', fixed_amount: null, unit_spend: null, window: 'statement_cycle', posting: 'after_window', delay_days: 0,
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
    const usage = http.expectOne('/api/accounting/accounts/11/reward-usage');
    expect(usage.request.method).toBe('GET');
    usage.flush([]);
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
    // Rule 1 is back to active: no state badge; rule 2 is still 已停用.
    expect(el(fixture).querySelector('.rule-row[data-rule-id="1"] .rule-state')).toBeNull();
    expect(texts(fixture, '.rule-row .rule-state')).toEqual(['已停用']);
  });

  it('a 409 basic_rule_overlap on the 啟用 switch names the clashing rule', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    click(fixture, '.rule-row app-switch.rule-enabled button', 1);
    http.expectOne('/api/accounting/accounts/11/reward-rules/2')
      .flush({ code: 409, message: 'basic_rule_overlap: rule 1' }, { status: 409, statusText: 'Conflict' });
    http.expectOne('/api/accounting/accounts/11').flush(DETAIL);
    fixture.detectChanges();

    expect(el(fixture).querySelector('.rule-message')?.textContent?.trim()).toBe('與「一般」的基本回饋期間重疊');
  });

  const ORDER_URL = '/api/accounting/accounts/11/reward-rules/order';
  /** DETAIL plus a third, active rule (網購 3%): A = 一般, B = 餐飲, C = 網購. */
  const THREE = {
    ...DETAIL,
    reward_rules: [...DETAIL.reward_rules, rewardRule({ id: 3, name: '網購', rate: '3.0000', sort_order: 2 })],
  } as AccountDetail;

  function rulesIn(detail: AccountDetail, ids: number[]): RewardRule[] {
    return ids.map((id, i) => ({ ...detail.reward_rules.find(r => r.id === id)!, sort_order: i }));
  }

  /** Opens a rule's ⋯ menu with a real press (pointerdown → pointerup → click) and returns its items. */
  function openRuleMenu(fixture: ComponentFixture<AccountSettingsComponent>, ruleId: number): HTMLButtonElement[] {
    const more = el(fixture).querySelector<HTMLButtonElement>(`.rule-row[data-rule-id="${ruleId}"] .rule-more`)!;
    more.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }));
    more.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }));
    more.click();
    fixture.detectChanges();
    return Array.from(el(fixture).querySelectorAll<HTMLButtonElement>(`.rule-row[data-rule-id="${ruleId}"] .rule-menu [role="menuitem"]`));
  }

  /** Runs the ⋯ menu item named `label` on a rule's row. */
  function ruleMenu(fixture: ComponentFixture<AccountSettingsComponent>, ruleId: number, label: string): void {
    openRuleMenu(fixture, ruleId).find(b => b.textContent?.trim() === label)!.click();
    fixture.detectChanges();
  }

  /** Rows 60 px tall at tops 0 / 60 / 120…, by their place in the list (jsdom has no layout). */
  function stubRowRects(fixture: ComponentFixture<AccountSettingsComponent>): void {
    const rows = () => Array.from(el(fixture).querySelectorAll<HTMLElement>('app-account-rules > .rule-row'));
    for (const row of rows()) {
      row.getBoundingClientRect = () => {
        const top = rows().indexOf(row) * 60;
        return { top, bottom: top + 60, height: 60, left: 0, right: 320, width: 320, x: 0, y: top, toJSON: () => ({}) } as DOMRect;
      };
    }
  }

  /** jsdom may lack PointerEvent: a MouseEvent with a pointerId. */
  function pointer(fixture: ComponentFixture<AccountSettingsComponent>, target: Element, type: string, clientY: number): void {
    const event = new MouseEvent(type, { clientY, button: 0, bubbles: true, cancelable: true });
    Object.defineProperty(event, 'pointerId', { value: 1 });
    target.dispatchEvent(event);
    fixture.detectChanges();
  }

  /** Presses a rule's handle at `ys[0]`, moves through the rest and releases at the last. */
  function dragRule(fixture: ComponentFixture<AccountSettingsComponent>, ruleId: number, ...ys: number[]): void {
    stubRowRects(fixture);
    const handle = el(fixture).querySelector(`.rule-row[data-rule-id="${ruleId}"] .rule-handle`)!;
    pointer(fixture, handle, 'pointerdown', ys[0]);
    for (const y of ys.slice(1)) {
      pointer(fixture, handle, 'pointermove', y);
    }
    pointer(fixture, handle, 'pointerup', ys[ys.length - 1]);
  }

  it('dragging the third rule above the first sends one order request with C, A, B and shows that order before the response', async () => {
    await setup('11');
    const fixture = render(THREE);
    const reorder = vi.spyOn(TestBed.inject(AccountingService), 'reorderRewardRules');

    dragRule(fixture, 3, 150, 10);
    expect(reorder).toHaveBeenCalledTimes(1);
    expect(reorder).toHaveBeenCalledWith(11, [3, 1, 2]);
    const put = http.expectOne(ORDER_URL);
    expect(put.request.method).toBe('PUT');
    expect(put.request.body).toEqual({ ids: [3, 1, 2] });
    // Optimistic: the dropped order shows while the request is in flight.
    expect(texts(fixture, '.rule-row .rule-label')).toEqual(['網購', '一般', '餐飲']);
    expect(fixture.componentInstance.ruleBusy()).toBe(true);

    put.flush(rulesIn(THREE, [3, 1, 2]));
    fixture.detectChanges();
    expect(texts(fixture, '.rule-row .rule-label')).toEqual(['網購', '一般', '餐飲']);
    expect(fixture.componentInstance.ruleBusy()).toBe(false);
    expect(fixture.componentInstance.isDirty()).toBe(false);
  });

  it('a failed order request shows the message and restores the saved order', async () => {
    await setup('11');
    const fixture = render(THREE);

    dragRule(fixture, 3, 150, 10);
    http.expectOne(ORDER_URL).flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();
    // The saved order is back before the re-read answers.
    expect(texts(fixture, '.rule-row .rule-label')).toEqual(['一般', '餐飲', '網購']);
    expect(el(fixture).querySelector('.rule-message')?.textContent?.trim()).toBeTruthy();

    http.expectOne('/api/accounting/accounts/11').flush(THREE);
    http.expectOne('/api/accounting/accounts/11/reward-usage').flush([]);
    fixture.detectChanges();
    expect(texts(fixture, '.rule-row .rule-label')).toEqual(['一般', '餐飲', '網購']);
    expect(el(fixture).querySelector('.rule-message')?.textContent?.trim()).toBeTruthy();
    expect(fixture.componentInstance.ruleBusy()).toBe(false);
  });

  it('a failed order request whose re-read also fails still shows the saved order', async () => {
    await setup('11');
    const fixture = render(THREE);

    dragRule(fixture, 3, 150, 10);
    http.expectOne(ORDER_URL).flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    http.expectOne('/api/accounting/accounts/11').flush({ detail: 'down' }, { status: 503, statusText: 'Unavailable' });
    fixture.detectChanges();

    expect(texts(fixture, '.rule-row .rule-label')).toEqual(['一般', '餐飲', '網購']);
    expect(el(fixture).querySelector('.rule-message')?.textContent?.trim()).toBeTruthy();
    expect(fixture.componentInstance.ruleBusy()).toBe(false);
  });

  it('a new reorder clears the previous rule message', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    click(fixture, '.rule-row app-switch.rule-enabled button', 1);
    http.expectOne('/api/accounting/accounts/11/reward-rules/2').flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    http.expectOne('/api/accounting/accounts/11').flush(DETAIL);
    http.expectOne('/api/accounting/accounts/11/reward-usage').flush([]);
    fixture.detectChanges();
    expect(el(fixture).querySelector('.rule-message')).not.toBeNull();

    ruleMenu(fixture, 1, '下移');
    expect(el(fixture).querySelector('.rule-message')).toBeNull();
    http.expectOne(ORDER_URL).flush(rulesIn(DETAIL, [2, 1]));
    fixture.detectChanges();
    expect(el(fixture).querySelector('.rule-message')).toBeNull();
  });

  it('reorder is ignored while a rule write is in flight', async () => {
    await setup('11');
    const fixture = render(THREE);
    const page = fixture.componentInstance;

    click(fixture, '.rule-row app-switch.rule-enabled button', 0);
    const put = http.expectOne('/api/accounting/accounts/11/reward-rules/1');
    expect(page.ruleBusy()).toBe(true);

    page.reorderRules([3, 1, 2]);
    fixture.detectChanges();
    dragRule(fixture, 3, 150, 10);
    const items = openRuleMenu(fixture, 2);
    expect(items.filter(b => ['上移', '下移'].includes(b.textContent?.trim() ?? '')).every(b => b.disabled)).toBe(true);
    http.expectNone(ORDER_URL);
    expect(texts(fixture, '.rule-row .rule-label')).toEqual(['一般', '餐飲', '網購']);

    put.flush({ ...THREE.reward_rules[0], is_enabled: false });
    http.expectOne('/api/accounting/accounts/11').flush(THREE);
    http.expectOne('/api/accounting/accounts/11/reward-usage').flush([]);
    fixture.detectChanges();
    expect(page.ruleBusy()).toBe(false);

    // Once the write settles, the next reorder starts from the saved order.
    page.reorderRules([2, 1, 3]);
    http.expectOne(ORDER_URL).flush(rulesIn(THREE, [2, 1, 3]));
  });

  it('reorder with the current order or a list that is not a permutation of the rules sends nothing', async () => {
    await setup('11');
    const fixture = render(THREE);
    const page = fixture.componentInstance;

    for (const ids of [[1, 2, 3], [1, 2], [1, 2, 4], [1, 1, 2], [1, 2, 3, 3]]) {
      page.reorderRules(ids);
    }
    http.expectNone(ORDER_URL);
    expect(page.ruleBusy()).toBe(false);
    fixture.detectChanges();
    expect(texts(fixture, '.rule-row .rule-label')).toEqual(['一般', '餐飲', '網購']);
  });

  it('⋯ 下移 sends the swapped order once', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    ruleMenu(fixture, 1, '下移');
    const put = http.expectOne(ORDER_URL);
    expect(put.request.method).toBe('PUT');
    expect(put.request.body).toEqual({ ids: [2, 1] });
    expect(texts(fixture, '.rule-row .rule-label')).toEqual(['餐飲', '一般']);
    put.flush(rulesIn(DETAIL, [2, 1]));
    fixture.detectChanges();

    expect(texts(fixture, '.rule-row .rule-label')).toEqual(['餐飲', '一般']);
    const items = openRuleMenu(fixture, 2);
    expect(items.find(b => b.textContent?.trim() === '上移')!.disabled).toBe(true);
  });

  it('a failed ⋯ 上移 shows the error and re-reads the account', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    ruleMenu(fixture, 2, '上移');
    const put = http.expectOne(ORDER_URL);
    expect(put.request.body).toEqual({ ids: [2, 1] });
    put.flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    http.expectOne('/api/accounting/accounts/11').flush(DETAIL);
    fixture.detectChanges();

    expect(el(fixture).querySelector('.rule-message')?.textContent?.trim()).toBeTruthy();
    expect(texts(fixture, '.rule-row .rule-label')).toEqual(['一般', '餐飲']);
  });

  it('tapping a row opens the sheet with that rule; ⋯ 編輯 does too; 新增規則 opens it with rule === null', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    expect(sheet(fixture)).toBeNull();

    click(fixture, '.rule-row .rule-open', 1);
    const editing = sheet(fixture)!;
    expect(editing.rule()?.id).toBe(2);
    expect(editing.startDelete()).toBe(false);
    expect(editing.accountId()).toBe(11);
    expect(editing.currency()).toBe('TWD');
    expect(editing.siblings().map(r => r.id)).toEqual([1, 2]);
    expect(editing.accounts()).toEqual(LIST);
    expect(editing.projects()).toEqual(PROJECTS);
    expect(el(fixture).querySelector('app-reward-rule-sheet .rule-delete')!.classList).not.toContain('armed');

    editing.closed.emit();
    fixture.detectChanges();
    expect(sheet(fixture)).toBeNull();

    ruleMenu(fixture, 1, '編輯');
    expect(sheet(fixture)!.rule()?.id).toBe(1);
    expect(sheet(fixture)!.startDelete()).toBe(false);
    sheet(fixture)!.closed.emit();
    fixture.detectChanges();

    click(fixture, '.rule-add');
    expect(sheet(fixture)!.rule()).toBeNull();
    expect(sheet(fixture)!.startDelete()).toBe(false);
  });

  it('⋯ 刪除 opens the sheet with the delete confirmation showing', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    ruleMenu(fixture, 2, '刪除');
    expect(sheet(fixture)!.rule()?.id).toBe(2);
    expect(sheet(fixture)!.startDelete()).toBe(true);
    const del = el(fixture).querySelector<HTMLButtonElement>('app-reward-rule-sheet .rule-delete')!;
    expect(del.classList).toContain('armed');
    expect(del.textContent?.trim()).toBe('確定刪除？');
    http.expectNone('/api/accounting/accounts/11/reward-rules/2');

    // One press confirms: the sheet's own delete flow runs from here.
    click(fixture, 'app-reward-rule-sheet .rule-delete');
    const req = http.expectOne('/api/accounting/accounts/11/reward-rules/2');
    expect(req.request.method).toBe('DELETE');
    req.flush(null, { status: 204, statusText: 'No Content' });
    fixture.detectChanges();
    expect(sheet(fixture)).toBeNull();
    http.expectOne('/api/accounting/accounts/11').flush({ ...DETAIL, reward_rules: [DETAIL.reward_rules[0]] });
    http.expectOne('/api/accounting/accounts/11/reward-usage').flush([]);
    fixture.detectChanges();
    expect(el(fixture).querySelectorAll('.rule-row').length).toBe(1);
  });

  /** Waits for afterNextRender hooks (the rows' and the sheet's focus moves). */
  async function settle(fixture: ComponentFixture<AccountSettingsComponent>): Promise<void> {
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise<void>(r => setTimeout(r));
    fixture.detectChanges();
  }

  it('deleting a rule through the sheet leaves focus on 新增規則, not <body>', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    openRuleMenu(fixture, 2).find(b => b.textContent?.trim() === '刪除')!.click();
    await settle(fixture);
    expect(document.activeElement).toBe(el(fixture).querySelector('app-reward-rule-sheet .rule-delete'));

    click(fixture, 'app-reward-rule-sheet .rule-delete');
    http.expectOne('/api/accounting/accounts/11/reward-rules/2').flush(null, { status: 204, statusText: 'No Content' });
    await settle(fixture);
    http.expectOne('/api/accounting/accounts/11').flush({ ...DETAIL, reward_rules: [DETAIL.reward_rules[0]] });
    http.expectOne('/api/accounting/accounts/11/reward-usage').flush([]);
    await settle(fixture);

    expect(el(fixture).querySelector('.rule-row[data-rule-id="2"]')).toBeNull();
    expect(document.activeElement).toBe(el(fixture).querySelector('.rule-add'));
  });

  it('closing the sheet opened from ⋯ 編輯 or ⋯ 刪除 returns focus to that row\'s ⋯; a row tap after a cancelled 刪除 is not armed', async () => {
    await setup('11');
    const fixture = render(DETAIL);
    const more = () => el(fixture).querySelector<HTMLButtonElement>('.rule-row[data-rule-id="2"] .rule-more');

    openRuleMenu(fixture, 2).find(b => b.textContent?.trim() === '編輯')!.click();
    await settle(fixture);
    expect(sheet(fixture)!.rule()?.id).toBe(2);
    click(fixture, 'app-reward-rule-sheet .sheet-cancel');
    await settle(fixture);
    expect(sheet(fixture)).toBeNull();
    expect(document.activeElement).toBe(more());

    openRuleMenu(fixture, 2).find(b => b.textContent?.trim() === '刪除')!.click();
    await settle(fixture);
    expect(el(fixture).querySelector('app-reward-rule-sheet .rule-delete')!.classList).toContain('armed');
    click(fixture, 'app-reward-rule-sheet .sheet-cancel');
    await settle(fixture);
    expect(sheet(fixture)).toBeNull();
    http.expectNone('/api/accounting/accounts/11/reward-rules/2');
    expect(document.activeElement).toBe(more());

    click(fixture, '.rule-row[data-rule-id="2"] .rule-open');
    await settle(fixture);
    expect(sheet(fixture)!.rule()?.id).toBe(2);
    expect(sheet(fixture)!.startDelete()).toBe(false);
    expect(el(fixture).querySelector('app-reward-rule-sheet .rule-delete')!.classList).not.toContain('armed');
  });

  describe('本期 usage', () => {
    const usage = (fields: Partial<RewardUsage>): RewardUsage => ({
      rule_ids: [1], shared_cap_id: null, name: '一般', window_start: '2026-09-16', window_end: '2026-10-15',
      used: '30.0000', estimated: '20.0000', cap: '100.0000', remaining: '50.0000',
      reward_account_id: 1, reward_account_name: '測試卡', reward_is_money: true, ...fields,
    });

    const bars = (fixture: ComponentFixture<AccountSettingsComponent>) =>
      Array.from(el(fixture).querySelectorAll('.rule-row')).map(r => r.querySelector('app-cap-bar .cap-text')?.textContent?.trim() ?? null);
    /** Both DETAIL rules active (餐飲 enabled), so a bar on one and not the other is the usage map's doing. */
    const BOTH_ACTIVE = {
      ...DETAIL,
      reward_rules: [DETAIL.reward_rules[0], { ...DETAIL.reward_rules[1], is_enabled: true }],
    } as AccountDetail;

    it('shows the 本期 cap bar from reward usage', async () => {
      await setup('11');
      const fixture = render(DETAIL, [usage({})]);
      expect(bars(fixture)).toEqual(['$50 / $100 · 剩 $50', null]);
      expect(el(fixture).querySelector('.rule-row[data-rule-id="1"] app-cap-bar [role="img"]')!.getAttribute('aria-label'))
        .toBe('本期已入帳 $30，預估 $20，上限 $100');
    });

    it('uncapped shows the 無上限 line without a track', async () => {
      await setup('11');
      const fixture = render(DETAIL, [usage({ cap: null, remaining: null })]);
      expect(bars(fixture)).toEqual(['無上限 · 本期 $30 已入帳 + $20 預估', null]);
      expect(el(fixture).querySelector('app-cap-bar [role="img"]')).toBeNull();
    });

    it('shared group shown once, on the first listed rule it covers', async () => {
      await setup('11');
      const fixture = render(BOTH_ACTIVE, [usage({ rule_ids: [2, 1, 99], shared_cap_id: 'cap-a', name: '共用' })]);
      expect(bars(fixture)).toEqual(['$50 / $100 · 剩 $50', null]);
    });

    it('shared group whose first listed member is disabled shows its bar on the first active member', async () => {
      await setup('11');
      // 餐飲 (disabled) is listed before 一般 (active); both are in the group.
      const detail = { ...DETAIL, reward_rules: [DETAIL.reward_rules[1], DETAIL.reward_rules[0]] } as AccountDetail;
      const fixture = render(detail, [usage({ rule_ids: [2, 1], shared_cap_id: 'cap-a', name: '共用' })]);
      expect(el(fixture).querySelector('.rule-row[data-rule-id="2"] app-cap-bar')).toBeNull();
      expect(el(fixture).querySelector('.rule-row[data-rule-id="1"] app-cap-bar')).not.toBeNull();
      expect(bars(fixture)).toEqual([null, '$50 / $100 · 剩 $50']);
      expect(el(fixture).querySelectorAll('app-cap-bar').length).toBe(1);
    });

    it('shared group with no active member shows no bar', async () => {
      await setup('11');
      // 一般 expired (today 2026-10-02), 餐飲 disabled.
      const detail = {
        ...DETAIL,
        reward_rules: [{ ...DETAIL.reward_rules[0], ends_on: '2026-09-30' }, DETAIL.reward_rules[1]],
      } as AccountDetail;
      const fixture = render(detail, [usage({ rule_ids: [1, 2], shared_cap_id: 'cap-a', name: '共用' })]);
      expect(el(fixture).querySelectorAll('app-cap-bar').length).toBe(0);
    });

    it('a failed usage request hides the bars only', async () => {
      await setup('11');
      const fixture = TestBed.createComponent(AccountSettingsComponent);
      fixture.detectChanges();
      http.expectOne('/api/accounting/accounts/11').flush(DETAIL);
      http.expectOne('/api/accounting/accounts/11/reward-usage').flush(null, { status: 500, statusText: 'x' });
      http.expectOne(r => r.url === '/api/accounting/accounts').flush(LIST);
      http.expectOne('/api/accounting/account-groups').flush(GROUPS);
      http.expectOne('/api/accounting/projects').flush(PROJECTS);
      fixture.detectChanges();
      expect(el(fixture).querySelectorAll('app-cap-bar').length).toBe(0);
      expect(el(fixture).querySelectorAll('.rule-row').length).toBe(2);
    });

    it('usage: the capped rule shows a cap bar; a shared group shows it once on the first listed member; a failed usage request hides the bars only', async () => {
      await setup('11');
      const detail = {
        ...BOTH_ACTIVE,
        reward_rules: [...BOTH_ACTIVE.reward_rules, rewardRule({ id: 3, name: '網購', rate: '3.0000', total_cap: '200.0000', sort_order: 2 })],
      } as AccountDetail;
      const fixture = render(detail, [
        usage({ rule_ids: [3], name: '網購', used: '100.0000', estimated: '20.0000', cap: '200.0000', remaining: '80.0000' }),
        usage({ rule_ids: [2, 1, 99], shared_cap_id: 'cap-a', name: '共用' }),
      ]);
      expect(bars(fixture)).toEqual(['$50 / $100 · 剩 $50', null, '$120 / $200 · 剩 $80']);
      expect(el(fixture).querySelectorAll('app-cap-bar').length).toBe(2);

      // A re-read whose usage request fails: the rows stay, the bars go, and no message shows.
      click(fixture, '.rule-row app-switch.rule-enabled button', 2);
      http.expectOne('/api/accounting/accounts/11/reward-rules/3').flush(detail.reward_rules[2]);
      http.expectOne('/api/accounting/accounts/11').flush(detail);
      http.expectOne('/api/accounting/accounts/11/reward-usage').flush(null, { status: 500, statusText: 'x' });
      fixture.detectChanges();
      expect(el(fixture).querySelectorAll('app-cap-bar').length).toBe(0);
      expect(el(fixture).querySelectorAll('.rule-row').length).toBe(3);
      expect(el(fixture).querySelector('.rule-message')).toBeNull();
    });

    it('a disabled rule\'s row is dimmed with 已停用 and 停用中，不估算回饋 and has no cap bar', async () => {
      await setup('11');
      const fixture = render(DETAIL, [usage({ rule_ids: [2], name: '餐飲' })]);
      const row = el(fixture).querySelector('.rule-row[data-rule-id="2"]')!;
      expect(row.classList).toContain('inactive');
      expect(row.querySelector('.rule-state')?.textContent?.trim()).toBe('已停用');
      expect(row.querySelector('.rule-muted')?.textContent?.trim()).toBe('停用中，不估算回饋');
      expect(row.querySelector('app-cap-bar')).toBeNull();
      expect(el(fixture).querySelector('.rule-row[data-rule-id="1"]')!.classList).not.toContain('inactive');
    });
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
    http.expectOne('/api/accounting/accounts/11/reward-usage').flush([]);
    fixture.detectChanges();
    expect(el(fixture).querySelectorAll('.rule-row').length).toBe(3);
    expect(texts(fixture, 'nav.sec-chips button')).toContain('回饋規則 (3)');

    click(fixture, '.rule-row .rule-open', 2);
    sheet(fixture)!.deleted.emit(3);
    fixture.detectChanges();
    expect(sheet(fixture)).toBeNull();
    http.expectOne('/api/accounting/accounts/11').flush(DETAIL);
    fixture.detectChanges();
    expect(el(fixture).querySelectorAll('.rule-row').length).toBe(2);
    expect(texts(fixture, 'nav.sec-chips button')).toContain('回饋規則 (2)');
  });

  it('an open rule sheet with unsaved input makes the page dirty; closing it does not', async () => {
    await setup('11');
    const fixture = render(DETAIL);

    click(fixture, '.rule-row .rule-open', 0);
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

    click(fixture, '.rule-row .rule-open', 0);
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

    click(fixture, '.rule-row .rule-open', 0);
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
    click(fixture, '.rule-row .rule-open', 0);
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

  it('?rule=new opens an empty rule sheet once and clears the param (#323 PR B)', async () => {
    await setup('11', { rule: 'new' });
    const fixture = render(DETAIL);

    expect(sheet(fixture)).not.toBeNull();
    expect(sheet(fixture)?.rule()).toBeNull();
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate.mock.calls[0][1]?.queryParams).toEqual({ rule: null });
  });

  it('?rule=new dropped by a navigation before the detail loads opens nothing on the next account (AGENT-477)', async () => {
    await setup('11', { rule: 'new' });
    const fixture = TestBed.createComponent(AccountSettingsComponent);
    fixture.detectChanges();
    http.expectOne('/api/accounting/accounts/11');

    // The user leaves for another account while 11 is still loading; the new URL carries no `?rule`.
    navigateTo('12');
    queryParams.next(convertToParamMap({}));
    http.expectOne('/api/accounting/accounts/12').flush({ ...DETAIL, id: 12 });
    http.expectOne('/api/accounting/accounts/12/reward-usage').flush([]);
    http.expectOne(r => r.url === '/api/accounting/accounts').flush(LIST);
    http.expectOne('/api/accounting/account-groups').flush(GROUPS);
    http.expectOne('/api/accounting/projects').flush(PROJECTS);
    fixture.detectChanges();

    expect(sheet(fixture)).toBeNull();
    expect(navigate).not.toHaveBeenCalled();
  });

  it('?rule= naming no rule of the account is ignored and cleared', async () => {
    await setup('11', { rule: '99' });
    const fixture = render(DETAIL);

    expect(sheet(fixture)).toBeNull();
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate.mock.calls[0][1]?.queryParams).toEqual({ rule: null });
  });

  // ---- back to 回饋規則總覽 (#378 Task 1) ------------------------------------------------------------------------

  describe('back to the reward rules overview', () => {
    const BACK = '/accounting/reward-rules?archived=1';
    const D8 = { ...DETAIL, id: 8 } as AccountDetail;

    it('safeBack accepts only the overview path with an optional query and no fragment', () => {
      expect(safeBack('/accounting/reward-rules')).toBe('/accounting/reward-rules');
      expect(safeBack('/accounting/reward-rules?archived=1&account_id=3')).toBe('/accounting/reward-rules?archived=1&account_id=3');
      for (const raw of [
        null, '', '//evil.example', 'https://evil.example/accounting/reward-rules', '/accounting/accounts',
        '/accounting/reward-rules/x', '/accounting/reward-rules#x', '/accounting/reward-rules?a=1#x',
      ]) {
        expect(safeBack(raw)).toBeNull();
      }
    });

    /** `router.navigateByUrl`, stubbed; `navigate` is already. */
    function spyNavigateByUrl(): MockInstance<Router['navigateByUrl']> {
      return vi.spyOn(TestBed.inject(Router), 'navigateByUrl').mockResolvedValue(true);
    }

    /** The serialised targets `navigateByUrl` was called with. */
    function byUrlTargets(spy: MockInstance<Router['navigateByUrl']>): string[] {
      const router = TestBed.inject(Router);
      return spy.mock.calls.map(([url]) => (typeof url === 'string' ? url : router.serializeUrl(url)));
    }

    /** The overview return: one `navigateByUrl` with the overview tree, replacing settings in history. */
    function expectOverviewReturn(spy: MockInstance<Router['navigateByUrl']>): void {
      expect(byUrlTargets(spy)).toEqual(['/accounting/reward-rules?archived=1#card-8']);
      expect(spy.mock.calls[0][0]).toBeInstanceOf(UrlTree);
      expect(spy.mock.calls[0][1]).toEqual({ replaceUrl: true });
    }

    const closeHref = (fixture: ComponentFixture<AccountSettingsComponent>) =>
      el(fixture).querySelector('a.ib[aria-label="取消"]')!.getAttribute('href');

    /** The ✕ link's RouterLink instance. */
    const closeLink = (fixture: ComponentFixture<AccountSettingsComponent>): RouterLink =>
      fixture.debugElement.query(By.css('a.ib[aria-label="取消"]')).injector.get(RouterLink);

    it('✕ goes to the passbook without back', async () => {
      await setup('8');
      const fixture = render(D8);
      expect(closeHref(fixture)).toBe('/accounting/accounts/8');
    });

    it('✕ goes back to the overview at this card with back', async () => {
      await setup('8', { back: BACK });
      const fixture = render(D8);
      expect(closeHref(fixture)).toBe('/accounting/reward-rules?archived=1#card-8');
    });

    it('✕ replaces settings in history with back', async () => {
      await setup('8', { back: BACK });
      expect(closeLink(render(D8)).replaceUrl).toBe(true);
    });

    it('✕ pushes (no replaceUrl) without back', async () => {
      await setup('8');
      expect(closeLink(render(D8)).replaceUrl).toBe(false);
    });

    it('an invalid back is ignored', async () => {
      await setup('8', { back: '//evil.example' });
      const fixture = render(D8);
      expect(closeHref(fixture)).toBe('/accounting/accounts/8');
    });

    it('saving with back navigates to the overview at this card', async () => {
      await setup('8', { back: BACK });
      const byUrl = spyNavigateByUrl();
      const fixture = render(D8);
      el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
      http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/accounts/8').flush(D8);

      expect(byUrl).toHaveBeenCalledTimes(1);
      expectOverviewReturn(byUrl);
      expect(navigate).not.toHaveBeenCalled();
    });

    it('saving without back navigates to the passbook', async () => {
      await setup('8');
      const byUrl = spyNavigateByUrl();
      const fixture = render(D8);
      el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
      http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/accounts/8').flush(D8);

      expect(navigate).toHaveBeenCalledWith(['/accounting/accounts', 8]);
      expect(byUrl).not.toHaveBeenCalled();
    });

    it('the fragment follows the account shown', async () => {
      await setup('8', { back: BACK });
      const fixture = render(D8);
      expect(closeHref(fixture)).toBe('/accounting/reward-rules?archived=1#card-8');

      navigateTo('9');
      http.expectOne('/api/accounting/accounts/9').flush({ ...DETAIL, id: 9 });
      http.expectOne('/api/accounting/accounts/9/reward-usage').flush([]);
      fixture.detectChanges();
      expect(closeHref(fixture)?.endsWith('#card-9')).toBe(true);
    });

    describe('closing the rule sheet opened by ?rule= with back returns to the overview', () => {
      it('取消', async () => {
        await setup('8', { rule: '2', back: BACK });
        const byUrl = spyNavigateByUrl();
        const fixture = render(D8);
        expect(sheet(fixture)?.rule()?.id).toBe(2);

        click(fixture, 'app-reward-rule-sheet .sheet-cancel');
        expect(sheet(fixture)).toBeNull();
        expectOverviewReturn(byUrl);
      });

      it('saved', async () => {
        await setup('8', { rule: '2', back: BACK });
        const byUrl = spyNavigateByUrl();
        const fixture = render(D8);

        sheet(fixture)!.saved.emit(DETAIL.reward_rules[1]);
        fixture.detectChanges();
        expect(sheet(fixture)).toBeNull();
        expectOverviewReturn(byUrl);
        http.expectOne('/api/accounting/accounts/8').flush(D8);
      });

      it('deleted', async () => {
        await setup('8', { rule: '2', back: BACK });
        const byUrl = spyNavigateByUrl();
        const fixture = render(D8);

        sheet(fixture)!.deleted.emit(2);
        fixture.detectChanges();
        expect(sheet(fixture)).toBeNull();
        expectOverviewReturn(byUrl);
        http.expectOne('/api/accounting/accounts/8').flush({ ...D8, reward_rules: [DETAIL.reward_rules[0]] });
      });
    });

    it('closing that sheet stays on settings when the account form is dirty', async () => {
      await setup('8', { rule: '2', back: BACK });
      const byUrl = spyNavigateByUrl();
      const fixture = render(D8);
      typeInto(fixture, '.name-input', '測試帳戶改');

      click(fixture, 'app-reward-rule-sheet .sheet-cancel');
      expect(sheet(fixture)).toBeNull();
      expect(byUrl).not.toHaveBeenCalled();
    });

    it('a rule sheet opened from the settings page itself (row tap) never navigates on close, even with back', async () => {
      await setup('8', { back: BACK });
      const byUrl = spyNavigateByUrl();
      const fixture = render(D8);

      click(fixture, '.rule-row .rule-open', 0);
      click(fixture, 'app-reward-rule-sheet .sheet-cancel');
      expect(sheet(fixture)).toBeNull();
      expect(byUrl).not.toHaveBeenCalled();
    });

    it('a row tap after a link-opened sheet was closed over a dirty account form never navigates on close', async () => {
      await setup('8', { rule: '2', back: BACK });
      const byUrl = spyNavigateByUrl();
      const fixture = render(D8);
      typeInto(fixture, '.name-input', '測試帳戶改');
      click(fixture, 'app-reward-rule-sheet .sheet-cancel');
      expect(byUrl).not.toHaveBeenCalled();
      typeInto(fixture, '.name-input', D8.name); // clean again: only the flag could still send the owner away

      click(fixture, '.rule-row .rule-open', 0);
      expect(sheet(fixture)?.rule()?.id).toBe(1);
      click(fixture, 'app-reward-rule-sheet .sheet-cancel');
      expect(sheet(fixture)).toBeNull();
      expect(byUrl).not.toHaveBeenCalled();
    });

    it('link-opened sheet, rule draft dirty, account form clean, 放棄 on the sheet\'s own strip → overview', async () => {
      await setup('8', { rule: '2', back: BACK });
      const byUrl = spyNavigateByUrl();
      const fixture = render(D8);
      typeInto(fixture, '.rule-name', '餐飲改');

      click(fixture, 'app-reward-rule-sheet .sheet-cancel');
      expect(byUrl).not.toHaveBeenCalled();
      click(fixture, 'app-reward-rule-sheet .discard-leave');
      expect(sheet(fixture)).toBeNull();
      expectOverviewReturn(byUrl);
    });

    describe('delegated route-leave prompt on a link-opened sheet', () => {
      it('放棄 with a clean account form runs the pending leave once and does not navigate to the overview', async () => {
        await setup('8', { rule: '2', back: BACK });
        const byUrl = spyNavigateByUrl();
        const fixture = render(D8);
        const registry = TestBed.inject(DirtyFormRegistry);
        const run = vi.fn();
        typeInto(fixture, '.rule-name', '餐飲改');
        registry.requestClose(run);
        fixture.detectChanges();

        click(fixture, 'app-reward-rule-sheet .discard-leave');
        expect(sheet(fixture)).toBeNull();
        expect(run).toHaveBeenCalledTimes(1);
        expect(byUrl).not.toHaveBeenCalled();
      });

      it('放棄 with a dirty account form shows the page strip; run not called; no overview navigation', async () => {
        await setup('8', { rule: '2', back: BACK });
        const byUrl = spyNavigateByUrl();
        const fixture = render(D8);
        const registry = TestBed.inject(DirtyFormRegistry);
        const run = vi.fn();
        typeInto(fixture, '.name-input', '測試帳戶改');
        typeInto(fixture, '.rule-name', '餐飲改');
        registry.requestClose(run);
        fixture.detectChanges();

        click(fixture, 'app-reward-rule-sheet .discard-leave');
        expect(sheet(fixture)).toBeNull();
        expect(strips(fixture).map(s => s.classList.contains('discard-strip'))).toEqual([true]);
        expect(run).not.toHaveBeenCalled();
        expect(byUrl).not.toHaveBeenCalled();
      });

      it('儲存 keeps the owner on settings: run not called, no overview navigation', async () => {
        await setup('8', { rule: '2', back: BACK });
        const byUrl = spyNavigateByUrl();
        const fixture = render(D8);
        const registry = TestBed.inject(DirtyFormRegistry);
        const run = vi.fn();
        typeInto(fixture, '.rule-name', '餐飲改');
        registry.requestClose(run);
        fixture.detectChanges();

        sheet(fixture)!.saved.emit({ ...DETAIL.reward_rules[1], name: '餐飲改' });
        fixture.detectChanges();
        expect(sheet(fixture)).toBeNull();
        expect(run).not.toHaveBeenCalled();
        expect(registry.promptOpen()).toBe(false);
        expect(byUrl).not.toHaveBeenCalled();
        http.expectOne('/api/accounting/accounts/8').flush(D8);
      });
    });

    it('back survives the ?rule= cleanup', async () => {
      await setup('8', { rule: '2', back: BACK });
      const fixture = render(D8);

      expect(navigate).toHaveBeenCalledTimes(1);
      const extras = navigate.mock.calls[0][1];
      expect(extras?.queryParams).toEqual({ rule: null });
      expect(extras?.queryParamsHandling).toBe('merge');
      // What the merge leaves in the URL: `back` without `rule`.
      queryParams.next(convertToParamMap({ back: BACK }));
      fixture.detectChanges();
      expect(closeHref(fixture)).toBe('/accounting/reward-rules?archived=1#card-8');
    });
  });

  it('an expired rule shows the 已過期 badge in the warning colour', async () => {
    await setup('11');
    const expired = rewardRule({ id: 3, name: '夏季', rate: '2.0000', starts_on: '2026-06-01', ends_on: '2026-09-30', sort_order: 2 });
    const fixture = render({ ...DETAIL, reward_rules: [...DETAIL.reward_rules, expired] } as AccountDetail);

    const badge = el(fixture).querySelector('.rule-row[data-rule-id="3"] .rule-state')!;
    expect(badge.textContent?.trim()).toBe('已過期');
    expect(badge.getAttribute('data-state')).toBe('expired');
    expect(badge.classList.contains('warn')).toBe(true);
    expect(el(fixture).querySelectorAll('.rule-row .rule-state.warn').length).toBe(1);
  });
  describe('Logo block', () => {
    const URL = '/api/accounting/accounts/11/logo';

    function file(): File {
      return new File([new Uint8Array(4)], 'a.png', { type: 'image/png' });
    }
    function choose(fixture: ComponentFixture<AccountSettingsComponent>, f: File): void {
      const input = el(fixture).querySelector<HTMLInputElement>('input[type=file]')!;
      Object.defineProperty(input, 'files', { configurable: true, value: [f] });
      input.dispatchEvent(new Event('change'));
    }

    it('shows 上傳的圖片 and 移除圖片 without the hint when the detail has a logo_hash', async () => {
      await setup('11');
      const fixture = render({ ...DETAIL, logo_hash: 'h1', logo: 'cathay-united' } as AccountDetail);
      http.expectOne('/api/accounting/accounts/11/logo.png?v=h1').flush(new Blob([new Uint8Array(1)], { type: 'image/png' }));
      expect(el(fixture).querySelector('.look-current')!.textContent?.trim()).toBe('目前顯示：上傳的圖片');
      const upload = el(fixture).querySelector<HTMLButtonElement>('.logo-upload')!;
      expect(upload.textContent?.trim()).toBe('選擇檔案');
      expect(upload.disabled).toBe(false);
      expect(el(fixture).querySelector('.logo-remove')).not.toBeNull();
      expect(el(fixture).querySelector('.logo-hint')).toBeNull();
    });

    it('hides 移除圖片 without an uploaded image and opens the grid from 內建 logo', async () => {
      await setup('11');
      const fixture = render(DETAIL);
      openSource(fixture, '上傳圖片');
      expect(el(fixture).querySelector('.logo-upload')).not.toBeNull();
      expect(el(fixture).querySelector('.logo-remove')).toBeNull();
      openSource(fixture, '內建 logo');
      expect(el(fixture).querySelector('.logo-grid')).not.toBeNull();
    });

    it('new account route: 上傳圖片 shows 選擇檔案 disabled with 先建立帳戶，再上傳 logo', async () => {
      await setup(null);
      const fixture = render(null);
      expect(el(fixture).querySelector('.icon-input')).not.toBeNull();
      openSource(fixture, '上傳圖片');
      const upload = el(fixture).querySelector<HTMLButtonElement>('.logo-upload')!;
      expect(upload.textContent?.trim()).toBe('選擇檔案');
      expect(upload.disabled).toBe(true);
      expect(el(fixture).querySelector('.logo-hint')!.textContent?.trim()).toBe('先建立帳戶，再上傳 logo');
    });

    it('upload keeps the dirty form: name stays edited, 儲存 stays enabled', async () => {
      await setup('11');
      const fixture = render(DETAIL);
      const name = el(fixture).querySelector<HTMLInputElement>('.name-input')!;
      name.value = '改過的名稱';
      name.dispatchEvent(new Event('input'));
      fixture.detectChanges();
      expect(el(fixture).querySelector<HTMLButtonElement>('.save')!.disabled).toBe(false);
      openSource(fixture, '上傳圖片');
      choose(fixture, file());
      const req = http.expectOne(URL);
      expect(req.request.method).toBe('PUT');
      req.flush({ ...DETAIL, name: '伺服器名稱', logo_hash: 'abc' });
      fixture.detectChanges();
      http.expectOne('/api/accounting/accounts/11/logo.png?v=abc').flush(new Blob([new Uint8Array(1)], { type: 'image/png' }));
      expect(fixture.componentInstance.logoHash()).toBe('abc');
      expect(el(fixture).querySelector('.look-current')!.textContent?.trim()).toBe('目前顯示：上傳的圖片');
      expect(fixture.componentInstance.isDirty()).toBe(true);
      expect(el(fixture).querySelector<HTMLInputElement>('.name-input')!.value).toBe('改過的名稱');
      expect(el(fixture).querySelector<HTMLButtonElement>('.save')!.disabled).toBe(false);
    });

    it('remove keeps the dirty form and falls back to the slug label', async () => {
      await setup('11');
      const fixture = render({ ...DETAIL, logo_hash: 'h1', logo: 'cathay-united' } as AccountDetail);
      http.expectOne('/api/accounting/accounts/11/logo.png?v=h1').flush(new Blob([new Uint8Array(1)], { type: 'image/png' }));
      const name = el(fixture).querySelector<HTMLInputElement>('.name-input')!;
      name.value = '改過的名稱';
      name.dispatchEvent(new Event('input'));
      fixture.detectChanges();
      el(fixture).querySelector<HTMLButtonElement>('.logo-remove')!.click();
      const req = http.expectOne(URL);
      expect(req.request.method).toBe('DELETE');
      req.flush(null, { status: 204, statusText: 'No Content' });
      fixture.detectChanges();
      expect(fixture.componentInstance.logoHash()).toBeNull();
      expect(el(fixture).querySelector('.look-current')!.textContent?.trim()).toBe('目前顯示：內建 logo · 國泰世華');
      expect(fixture.componentInstance.isDirty()).toBe(true);
      expect(el(fixture).querySelector<HTMLInputElement>('.name-input')!.value).toBe('改過的名稱');
      expect(el(fixture).querySelector<HTMLButtonElement>('.save')!.disabled).toBe(false);
    });

    it('refuses an oversize file without a request and toasts', async () => {
      await setup('11');
      const fixture = render(DETAIL);
      const show = vi.spyOn(TestBed.inject(AccountingToastService), 'show');
      openSource(fixture, '上傳圖片');
      choose(fixture, { size: 6 * 1024 * 1024, type: 'image/png' } as unknown as File);
      expect(el(fixture).querySelector<HTMLInputElement>('input[type=file]')!.value).toBe('');
      http.expectNone(URL);
      expect(show).toHaveBeenCalledWith('圖片太大（上限 5 MB）');
    });

    it('navigating away clears logoHash and the busy flag before the next account loads', async () => {
      await setup('11');
      const fixture = render({ ...DETAIL, logo_hash: 'h1' } as AccountDetail);
      http.expectOne('/api/accounting/accounts/11/logo.png?v=h1').flush(new Blob([new Uint8Array(1)], { type: 'image/png' }));
      choose(fixture, file());
      const pending = http.expectOne(URL);
      expect(fixture.componentInstance.uploadingLogo()).toBe(true);
      navigateTo('12');
      fixture.detectChanges();
      expect(fixture.componentInstance.logoHash()).toBeNull();
      expect(fixture.componentInstance.uploadingLogo()).toBe(false);
      pending.flush({ ...DETAIL, logo_hash: 'late' });
      expect(fixture.componentInstance.logoHash()).toBeNull();
      http.match(() => true);
    });

    it('toasts the mapped message on a 422 and clears the busy state', async () => {
      await setup('11');
      const fixture = render(DETAIL);
      const show = vi.spyOn(TestBed.inject(AccountingToastService), 'show');
      openSource(fixture, '上傳圖片');
      choose(fixture, file());
      fixture.detectChanges();
      expect(fixture.componentInstance.uploadingLogo()).toBe(true);
      http.expectOne(URL).flush({ message: 'logo_format' }, { status: 422, statusText: 'Unprocessable Entity' });
      expect(show).toHaveBeenCalledWith('只接受 PNG、JPEG、WebP');
      expect(fixture.componentInstance.uploadingLogo()).toBe(false);
    });
  });

  describe('1 點 = 1 元 switch', () => {
    const CASH = { ...DETAIL, is_credit: false, closing_day: null, due_rule: null, due_value: null, credit_limit: null } as AccountDetail;

    it('shows the 1 點 = 1 元 switch and helper on a non-credit account', async () => {
      await setup('11');
      const fixture = render(CASH);
      const sw = el(fixture).querySelector<HTMLElement>('app-switch.cash-equivalent');
      expect(sw).not.toBeNull();
      expect(sw!.querySelector('button')!.getAttribute('aria-label')).toBe('1 點 = 1 元');
      expect(el(fixture).textContent).toContain('回饋入這個帳戶時當成金額計算');
    });

    it('hides it on a credit account', async () => {
      await setup('11');
      const fixture = render(DETAIL);
      expect(el(fixture).querySelector('app-switch.cash-equivalent')).toBeNull();
      expect(el(fixture).textContent).not.toContain('回饋入這個帳戶時當成金額計算');
    });

    it('toggling it on sends cash_equivalent true', async () => {
      await setup('11');
      const fixture = render(CASH);
      el(fixture).querySelector<HTMLButtonElement>('app-switch.cash-equivalent button')!.click();
      el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
      const req = http.expectOne('/api/accounting/accounts/11');
      expect(req.request.body.cash_equivalent).toBe(true);
      req.flush(CASH);
    });

    it('untouched switch omits cash_equivalent from the PUT body', async () => {
      await setup('11');
      const fixture = render(CASH);
      typeInto(fixture, '.name-input', '玉山 Only 2');
      el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
      const req = http.expectOne('/api/accounting/accounts/11');
      expect('cash_equivalent' in req.request.body).toBe(false);
      req.flush(CASH);
    });
  });

  describe('sections, chips and save state', () => {
    /** Captures the scroll-spy callback; jsdom has no IntersectionObserver of its own. */
    class FakeObserver {
      static instances: FakeObserver[] = [];
      readonly observe = vi.fn();
      readonly unobserve = vi.fn();
      readonly disconnect = vi.fn();
      constructor(readonly callback: IntersectionObserverCallback, readonly options?: IntersectionObserverInit) {
        FakeObserver.instances.push(this);
      }

      /** Reports sections entering (true) or leaving (false) the band, as the browser would. */
      fire(fixture: ComponentFixture<AccountSettingsComponent>, changes: [string, boolean][]): void {
        const entries = changes.map(([id, isIntersecting]) => ({ target: el(fixture).querySelector(`#s-${id}`)!, isIntersecting }));
        this.callback(entries as unknown as IntersectionObserverEntry[], this as unknown as IntersectionObserver);
        fixture.detectChanges();
      }
    }

    function stubObserver(): void {
      FakeObserver.instances = [];
      vi.stubGlobal('IntersectionObserver', FakeObserver);
    }

    function chips(fixture: ComponentFixture<AccountSettingsComponent>): HTMLButtonElement[] {
      return Array.from(el(fixture).querySelectorAll<HTMLButtonElement>('nav.sec-chips button'));
    }

    function current(fixture: ComponentFixture<AccountSettingsComponent>): string[] {
      return chips(fixture).filter(c => c.getAttribute('aria-current') === 'true').map(c => c.textContent?.trim() ?? '');
    }

    function rowLabels(fixture: ComponentFixture<AccountSettingsComponent>, id: string): string[] {
      return texts(fixture, `#s-${id} .rw > .k`);
    }

    it('renders six section headings in order', async () => {
      await setup('11');
      const fixture = render(DETAIL);
      const sections = Array.from(el(fixture).querySelectorAll('section.sec'));
      expect(sections.map(s => s.id)).toEqual(['s-basic', 's-look', 's-card', 's-reward', 's-fx', 's-more']);
      expect(sections.map(s => s.querySelector('h3')?.textContent?.trim())).toEqual(['基本', '外觀', '信用卡', '回饋規則', '國外交易', '其他']);
      expect(sections.map(s => s.getAttribute('aria-labelledby'))).toEqual(['h-basic', 'h-look', 'h-card', 'h-reward', 'h-fx', 'h-more']);
      expect(sections.map(s => s.querySelector('h3')?.id)).toEqual(['h-basic', 'h-look', 'h-card', 'h-reward', 'h-fx', 'h-more']);
    });

    it('回饋規則 chip shows the rule count', async () => {
      await setup('11');
      const fixture = render(DETAIL);
      expect(el(fixture).querySelector('nav.sec-chips')?.getAttribute('aria-label')).toBe('設定區塊');
      expect(chips(fixture).map(c => c.textContent?.trim())).toEqual(['基本', '外觀', '信用卡', '回饋規則 (2)', '國外交易', '其他']);
      expect(chips(fixture).every(c => c.type === 'button')).toBe(true);
      expect(current(fixture)).toEqual(['基本']);
    });

    it('clicking a chip marks it current and scrolls', async () => {
      await setup('11');
      const fixture = render(DETAIL);
      const target = el(fixture).querySelector<HTMLElement>('#s-fx')!;
      const scroll = vi.fn();
      target.scrollIntoView = scroll;

      chips(fixture)[4].click();
      fixture.detectChanges();

      expect(scroll).toHaveBeenCalledTimes(1);
      expect(fixture.componentInstance.activeSection()).toBe('fx');
      expect(current(fixture)).toEqual(['國外交易']);
    });

    it('scroll-spy marks the section reported in view', async () => {
      await setup('11');
      stubObserver();
      const fixture = render(DETAIL);

      expect(FakeObserver.instances.length).toBe(1);
      const observer = FakeObserver.instances[0];
      const observed = observer.observe.mock.calls.map(call => (call[0] as Element).id);
      expect(observed).toEqual(['s-basic', 's-look', 's-card', 's-reward', 's-fx', 's-more']);

      observer.fire(fixture, [['card', true]]);
      expect(current(fixture)).toEqual(['信用卡']);
    });

    it('scroll-spy band starts below the sticky chips', async () => {
      await setup('11');
      stubObserver();
      vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(44);
      render(DETAIL);

      expect(FakeObserver.instances[0].options?.rootMargin).toBe('-44px 0px -60% 0px');
    });

    it('scroll-spy drops a section that leaves the band', async () => {
      await setup('11');
      stubObserver();
      const fixture = render(DETAIL);
      const observer = FakeObserver.instances[0];

      observer.fire(fixture, [['look', true], ['card', true]]);
      expect(current(fixture)).toEqual(['外觀']);
      observer.fire(fixture, [['look', false]]);
      expect(current(fixture)).toEqual(['信用卡']);
    });

    it('after a chip jump the spy keeps the chosen section until the owner scrolls', async () => {
      await setup('11');
      stubObserver();
      const fixture = render(DETAIL);
      const observer = FakeObserver.instances[0];
      el(fixture).querySelector<HTMLElement>('#s-fx')!.scrollIntoView = vi.fn();

      chips(fixture)[4].click();
      fixture.detectChanges();
      observer.fire(fixture, [['reward', true], ['fx', true]]);
      expect(current(fixture)).toEqual(['國外交易']);

      el(fixture).querySelector('.account-settings')!.dispatchEvent(new Event('wheel', { bubbles: true }));
      observer.fire(fixture, [['card', true]]);
      expect(current(fixture)).toEqual(['信用卡']);
    });

    it('a chip jump that reaches its section hands the chip back to the spy', async () => {
      await setup('11');
      stubObserver();
      const fixture = render(DETAIL);
      const observer = FakeObserver.instances[0];
      el(fixture).querySelector<HTMLElement>('#s-fx')!.scrollIntoView = vi.fn();

      chips(fixture)[4].click();
      fixture.detectChanges();
      observer.fire(fixture, [['fx', true]]);
      expect(current(fixture)).toEqual(['國外交易']);

      // No wheel/touch/key input: the next intersection moves the chip on its own.
      observer.fire(fixture, [['fx', false], ['more', true]]);
      expect(current(fixture)).toEqual(['其他']);
    });

    it('scrollend on the pane releases a chip jump and marks the section now on top', async () => {
      document.body.style.overflowY = 'auto';
      onTestFinished(() => {
        document.body.style.overflowY = '';
      });
      await setup('11');
      stubObserver();
      const fixture = render(DETAIL);
      const observer = FakeObserver.instances[0];
      expect(observer.options?.root).toBe(document.body);
      el(fixture).querySelector<HTMLElement>('#s-fx')!.scrollIntoView = vi.fn();

      chips(fixture)[4].click();
      fixture.detectChanges();
      observer.fire(fixture, [['reward', true], ['fx', true]]);
      expect(current(fixture)).toEqual(['國外交易']);

      // The jump's own smooth scroll ends here too: the target is still in view, so its chip stays…
      document.body.dispatchEvent(new Event('scrollend'));
      fixture.detectChanges();
      expect(current(fixture)).toEqual(['國外交易']);

      // …but the lock is gone: the next intersection moves the chip without any wheel/touch/key input.
      observer.fire(fixture, [['card', true]]);
      expect(current(fixture)).toEqual(['信用卡']);
    });

    it('scrollend after a scrollbar drag that left the target behind marks the section now on top', async () => {
      document.body.style.overflowY = 'auto';
      onTestFinished(() => {
        document.body.style.overflowY = '';
      });
      await setup('11');
      stubObserver();
      const fixture = render(DETAIL);
      const observer = FakeObserver.instances[0];
      el(fixture).querySelector<HTMLElement>('#s-fx')!.scrollIntoView = vi.fn();

      chips(fixture)[4].click();
      fixture.detectChanges();
      observer.fire(fixture, [['reward', true], ['fx', true]]);
      observer.fire(fixture, [['reward', false], ['fx', false], ['look', true], ['card', true]]);
      expect(current(fixture)).toEqual(['國外交易']);

      document.body.dispatchEvent(new Event('scrollend'));
      fixture.detectChanges();
      expect(current(fixture)).toEqual(['外觀']);
    });

    /** A pane that has scrolled as far as it can (1440 × 900: 2243 px of content, 768 px tall), with #s-fx on screen below the band. */
    function paneAtEnd(fixture: ComponentFixture<AccountSettingsComponent>, scrollTop: number): void {
      const pane = document.body;
      for (const [key, value] of Object.entries({ scrollTop, clientHeight: 768, scrollHeight: 2243 })) {
        Object.defineProperty(pane, key, { value, configurable: true });
      }
      pane.getBoundingClientRect = () => ({ top: 81, bottom: 852, left: 0, right: 1440, width: 1440, height: 771, x: 0, y: 81, toJSON: () => ({}) });
      const fx = el(fixture).querySelector<HTMLElement>('#s-fx')!;
      fx.getBoundingClientRect = () => ({ top: 470, bottom: 662, left: 0, right: 900, width: 900, height: 192, x: 0, y: 470, toJSON: () => ({}) });
      onTestFinished(() => {
        for (const key of ['scrollTop', 'clientHeight', 'scrollHeight']) {
          delete (pane as unknown as Record<string, unknown>)[key];
        }
        delete (pane as unknown as Record<string, unknown>)['getBoundingClientRect'];
      });
    }

    it('a jump whose target cannot reach the band keeps its chip once the pane has scrolled to its end', async () => {
      document.body.style.overflowY = 'auto';
      onTestFinished(() => {
        document.body.style.overflowY = '';
      });
      await setup('11');
      stubObserver();
      const fixture = render(DETAIL);
      const observer = FakeObserver.instances[0];
      el(fixture).querySelector<HTMLElement>('#s-fx')!.scrollIntoView = vi.fn();
      paneAtEnd(fixture, 2243 - 768);

      chips(fixture)[4].click();
      fixture.detectChanges();
      // 國外交易 + 其他 together are shorter than the pane's lower 60 %: the band only ever holds 回饋規則.
      observer.fire(fixture, [['basic', false], ['look', false], ['card', false], ['reward', true]]);
      expect(current(fixture)).toEqual(['國外交易']);

      document.body.dispatchEvent(new Event('scrollend'));
      fixture.detectChanges();
      expect(current(fixture)).toEqual(['國外交易']);

      // The lock is gone all the same: the owner's next scroll re-marks the top section.
      observer.fire(fixture, [['reward', false], ['card', true]]);
      expect(current(fixture)).toEqual(['信用卡']);
    });

    it('a scrollbar drag that stops short of the end with the target off the band marks the section on top', async () => {
      document.body.style.overflowY = 'auto';
      onTestFinished(() => {
        document.body.style.overflowY = '';
      });
      await setup('11');
      stubObserver();
      const fixture = render(DETAIL);
      const observer = FakeObserver.instances[0];
      el(fixture).querySelector<HTMLElement>('#s-fx')!.scrollIntoView = vi.fn();
      paneAtEnd(fixture, 1000);

      chips(fixture)[4].click();
      fixture.detectChanges();
      observer.fire(fixture, [['basic', false], ['look', false], ['card', true], ['reward', true]]);
      expect(current(fixture)).toEqual(['國外交易']);

      document.body.dispatchEvent(new Event('scrollend'));
      fixture.detectChanges();
      expect(current(fixture)).toEqual(['信用卡']);
    });

    it('scrollend on the viewport releases a chip jump when the page has no pane', async () => {
      await setup('11');
      stubObserver();
      const fixture = render(DETAIL);
      const observer = FakeObserver.instances[0];
      expect(observer.options?.root).toBeNull();
      el(fixture).querySelector<HTMLElement>('#s-fx')!.scrollIntoView = vi.fn();

      chips(fixture)[4].click();
      fixture.detectChanges();
      observer.fire(fixture, [['card', true], ['fx', true]]);
      observer.fire(fixture, [['fx', false]]);
      expect(current(fixture)).toEqual(['國外交易']);

      window.dispatchEvent(new Event('scrollend'));
      fixture.detectChanges();
      expect(current(fixture)).toEqual(['信用卡']);
    });

    it('a jump to the short last section keeps 其他 current', async () => {
      await setup('11');
      stubObserver();
      const fixture = render(DETAIL);
      const observer = FakeObserver.instances[0];
      el(fixture).querySelector<HTMLElement>('#s-more')!.scrollIntoView = vi.fn();

      chips(fixture)[5].click();
      fixture.detectChanges();
      observer.fire(fixture, [['basic', false], ['fx', true], ['more', true]]);
      expect(current(fixture)).toEqual(['其他']);

      // 其他 is too short to reach the top of the band, so the jump's scrollend must not snap the chip back to 國外交易.
      window.dispatchEvent(new Event('scrollend'));
      fixture.detectChanges();
      expect(current(fixture)).toEqual(['其他']);
    });

    it('disconnects the observer on destroy', async () => {
      await setup('11');
      stubObserver();
      const fixture = render(DETAIL);
      const observer = FakeObserver.instances[0];
      expect(observer.disconnect).not.toHaveBeenCalled();

      fixture.destroy();
      expect(observer.disconnect).toHaveBeenCalledTimes(1);
    });

    it('editing the name shows 有 1 項未儲存; a second field shows 有 2 項未儲存', async () => {
      await setup('11');
      const fixture = render(DETAIL);
      expect(el(fixture).querySelector('.dirty-count')).toBeNull();
      expect(fixture.componentInstance.dirtyCount()).toBe(0);

      typeInto(fixture, '.name-input', '玉山 Only 2');
      expect(texts(fixture, '.topbar .dirty-count')).toEqual(['有 1 項未儲存']);

      typeInto(fixture, '.note-input', '新備註');
      expect(texts(fixture, '.topbar .dirty-count')).toEqual(['有 2 項未儲存']);
    });

    it('an open dirty rule sheet keeps the count at least 1', async () => {
      await setup('11');
      const fixture = render(DETAIL);

      click(fixture, '.rule-row .rule-open', 0);
      typeInto(fixture, '.rule-name', '一般改');

      expect(fixture.componentInstance.isDirty()).toBe(true);
      expect(fixture.componentInstance.dirtyCount()).toBeGreaterThanOrEqual(1);
      expect(texts(fixture, '.topbar .dirty-count')).toEqual(['有 1 項未儲存']);
    });

    it('successful save clears the count', async () => {
      await setup('11');
      const fixture = render(DETAIL);
      typeInto(fixture, '.name-input', '玉山 Only 2');
      expect(texts(fixture, '.topbar .dirty-count')).toEqual(['有 1 項未儲存']);

      el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
      http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/accounts/11').flush({ ...DETAIL, name: '玉山 Only 2' });
      fixture.detectChanges();

      expect(fixture.componentInstance.dirtyCount()).toBe(0);
      expect(el(fixture).querySelector('.dirty-count')).toBeNull();
    });

    it('基本 holds 名稱 … 納入總餘額 and 其他 holds 備註', async () => {
      await setup('11');
      const fixture = render({ ...DETAIL, is_credit: false } as AccountDetail);

      expect(rowLabels(fixture, 'basic')).toEqual([
        '名稱', '主幣種', '帳戶分組', '初始金額', '帳單週期結帳日', '納入總餘額', '1 點 = 1 元',
      ]);
      expect(el(fixture).querySelector('#s-basic .closing-day')).not.toBeNull();
      expect(el(fixture).querySelector('#s-basic .include-toggle')).not.toBeNull();
      expect(el(fixture).querySelector('#s-look app-account-look')).not.toBeNull();
      expect(el(fixture).querySelector('#s-look app-account-look')!.classList).not.toContain('stacked');
      expect(rowLabels(fixture, 'look')).toEqual([]);
      expect(el(fixture).querySelector('#s-card .sec-head .credit-toggle')).not.toBeNull();
      expect(rowLabels(fixture, 'card')).toEqual([]);
      expect(texts(fixture, '#s-card .card-hint')).toEqual(['打開「信用帳戶」才會出現繳款期限、額度與自動扣繳。']);
      expect(el(fixture).querySelector('#s-reward .rules-block')).not.toBeNull();
      expect(texts(fixture, '#s-reward .rules-all')).toEqual(['所有卡片的規則 ›']);
      expect(el(fixture).querySelector('#s-fx .sec-head .fx-toggle')).not.toBeNull();
      expect(rowLabels(fixture, 'fx')).toEqual(['手續費 %', '計算方式', '退款時退回手續費']);
      expect(rowLabels(fixture, 'more')).toEqual(['備註', '封存帳戶', '刪除帳戶']);
      expect(el(fixture).querySelector('#s-more .reset-flag')).not.toBeNull();
      expect(el(fixture).querySelector('#s-more .delete-account')).not.toBeNull();

      flip(fixture, 'credit-toggle');
      expect(rowLabels(fixture, 'card')).toEqual(['繳款期限', '信用額度', '額度共用', '主帳戶', '自動扣繳']);
      expect(el(fixture).querySelector('#s-card .card-hint')).toBeNull();
    });
  });

  describe('phone layout, save bar and error jump', () => {
    afterEach(() => {
      TestBed.inject(LayoutModeService).set(null);
    });

    async function phoneSetup(): Promise<void> {
      await setup('11');
      TestBed.inject(LayoutModeService).set('phone');
    }

    function sheet(fixture: ComponentFixture<AccountSettingsComponent>): HTMLElement | null {
      return el(fixture).ownerDocument.querySelector<HTMLElement>('.overlay[data-overlay] > .sheet[role=dialog]');
    }

    /** Replaces `scrollIntoView` (jsdom has none) for the test; the returned spy records the scrolled elements. */
    function spyScroll(): MockInstance<(arg?: boolean | ScrollIntoViewOptions) => void> {
      const proto = Element.prototype as { scrollIntoView?: unknown };
      const original = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollIntoView');
      proto.scrollIntoView = () => undefined;
      const spy = vi.spyOn(Element.prototype, 'scrollIntoView');
      onTestFinished(() => {
        if (original) {
          Object.defineProperty(Element.prototype, 'scrollIntoView', original);
        } else {
          delete proto.scrollIntoView;
        }
      });
      return spy;
    }

    it('phone: 基本 renders label/value rows and the 結帳日 row opens a sheet with the stepper', async () => {
      await phoneSetup();
      const fixture = render(DETAIL);

      expect(el(fixture).querySelector('#s-basic .rw')).toBeNull();
      expect(texts(fixture, '#s-basic .set-card .set-row .row-label')).toEqual(['名稱', '主幣種', '帳戶分組', '初始金額', '帳單週期', '納入總餘額']);
      expect(texts(fixture, '#s-basic .row-closing_day .row-v')).toEqual(['15 號']);
      expect(texts(fixture, '#s-basic .row-closing_day .row-hint')).toEqual(['本期 09/16–10/15']);
      expect(texts(fixture, '#s-basic .row-opening_balance .row-v')).toEqual(['-3,693']);
      const row = el(fixture).querySelector<HTMLButtonElement>('#s-basic button.set-row.row-closing_day')!;
      expect(row.type).toBe('button');
      expect(row.querySelector('svg[aria-hidden="true"] path')?.getAttribute('d')).toBe('M9 18l6-6-6-6');
      // Switch rows keep the switch inline; picker rows show the picker trigger.
      expect(el(fixture).querySelector('#s-basic .set-row app-switch.include-toggle')).not.toBeNull();
      expect(el(fixture).querySelector('#s-basic .set-row app-picker.group-select .pk-trigger')).not.toBeNull();
      expect(el(fixture).querySelector('app-stepper.closing-day')).toBeNull();
      expect(sheet(fixture)).toBeNull();

      row.click();
      fixture.detectChanges();

      const open = sheet(fixture)!;
      expect(open).not.toBeNull();
      expect(open.getAttribute('aria-modal')).toBe('true');
      expect(open.getAttribute('aria-label')).toBe('結帳日');
      expect(open.querySelector('app-stepper.closing-day')).not.toBeNull();
      expect(open.querySelectorAll('app-stepper, app-amount-field').length).toBe(1);
      expect(open.querySelector('[role=dialog] [role=dialog]')).toBeNull();
      expect(stepperText(fixture, 'closing-day')).toBe('15 號');
      expect(open.contains(el(fixture).ownerDocument.activeElement)).toBe(true);

      Array.from(open.querySelectorAll<HTMLButtonElement>('button')).find(b => b.textContent?.trim() === '完成')!.click();
      fixture.detectChanges();
      expect(sheet(fixture)).toBeNull();
    });

    it('phone sheet: clicking 加一 inside the sheet keeps it open', async () => {
      await phoneSetup();
      const fixture = render(DETAIL);
      click(fixture, '.row-closing_day');

      stepperClick(fixture, 'closing-day', 'inc');

      expect(sheet(fixture)).not.toBeNull();
      expect(stepperText(fixture, 'closing-day')).toBe('16 號');
      expect(fixture.componentInstance.form().closing_day).toBe('16');

      // A tap on the dimmed backdrop closes it; the row shows the new value.
      el(fixture).ownerDocument.querySelector<HTMLElement>('.overlay[data-overlay]')!.click();
      fixture.detectChanges();
      expect(sheet(fixture)).toBeNull();
      expect(texts(fixture, '.row-closing_day .row-v')).toEqual(['16 號']);
    });

    it('phone sheet: a pull on the grab bar closes it and keeps the edited value', async () => {
      await phoneSetup();
      const fixture = render(DETAIL);
      click(fixture, '.row-closing_day');
      stepperClick(fixture, 'closing-day', 'inc');

      pullGrab(sheet(fixture)!.querySelector<HTMLElement>('.grab')!);
      fixture.detectChanges();
      expect(sheet(fixture)).toBeNull();
      expect(texts(fixture, '.row-closing_day .row-v')).toEqual(['16 號']);
    });

    it('phone sheet: tap-to-type Enter keeps focus inside the 結帳日 sheet; Escape then closes it', async () => {
      await phoneSetup();
      const fixture = render(DETAIL);
      click(fixture, '.row-closing_day');
      const open = sheet(fixture)!;
      const doc = el(fixture).ownerDocument;

      open.querySelector<HTMLButtonElement>('app-stepper.closing-day .val')!.click();
      fixture.detectChanges();
      const input = open.querySelector<HTMLInputElement>('app-stepper.closing-day .val-input')!;
      expect(doc.activeElement).toBe(input);
      input.value = '20';
      input.dispatchEvent(new Event('input'));
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
      fixture.detectChanges();

      expect(fixture.componentInstance.form().closing_day).toBe('20');
      expect(sheet(fixture)).toBe(open);
      expect(open.contains(doc.activeElement)).toBe(true);
      expect(doc.activeElement).toBe(open.querySelector('app-stepper.closing-day .val'));

      doc.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      fixture.detectChanges();
      expect(sheet(fixture)).toBeNull();
      expect(doc.activeElement).toBe(el(fixture).querySelector('button.row-closing_day'));
    });

    it('phone sheet: Tab is trapped, Escape closes and refocuses the row', async () => {
      await phoneSetup();
      const fixture = render(DETAIL);
      click(fixture, '.row-credit_limit');
      const open = sheet(fixture)!;
      expect(open.getAttribute('aria-label')).toBe('信用額度');
      expect(open.querySelector('app-amount-field.credit-limit')).not.toBeNull();

      const buttons = Array.from(open.querySelectorAll<HTMLElement>('button, input'));
      const first = buttons[0];
      const last = buttons[buttons.length - 1];
      expect(last.textContent?.trim()).toBe('完成');
      last.focus();
      const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
      last.dispatchEvent(tab);
      expect(tab.defaultPrevented).toBe(true);
      expect(el(fixture).ownerDocument.activeElement).toBe(first);

      const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
      first.dispatchEvent(escape);
      fixture.detectChanges();

      expect(escape.defaultPrevented).toBe(true);
      expect(sheet(fixture)).toBeNull();
      expect(el(fixture).ownerDocument.activeElement).toBe(el(fixture).querySelector('button.row-credit_limit'));
    });

    /** Opens a 外觀 source tab inside the look sheet via its seg radio. */
    function sheetSource(fixture: ComponentFixture<AccountSettingsComponent>, label: string): void {
      Array.from(sheet(fixture)!.querySelectorAll<HTMLButtonElement>('.seg-toggle [role=radio]'))
        .find(b => b.textContent?.trim() === label)!
        .click();
      fixture.detectChanges();
    }

    it('phone 外觀 is one 圖示 row with the summary and hint', async () => {
      await phoneSetup();
      const fixture = render(DETAIL);

      expect(el(fixture).querySelector('#s-look app-account-look')).toBeNull();
      expect(texts(fixture, '#s-look .set-card .set-row .row-label')).toEqual(['圖示']);
      const row = el(fixture).querySelector<HTMLButtonElement>('#s-look .set-card button.set-row.row-look')!;
      expect(row.type).toBe('button');
      expect(texts(fixture, '.row-look .row-label')).toEqual(['圖示']);
      expect(texts(fixture, '.row-look .row-hint')).toEqual(['上傳圖片、內建 logo 或文字＋顏色']);
      expect(texts(fixture, '.row-look .row-v')).toEqual(['文字 💳']);
      expect(row.querySelector('svg[aria-hidden="true"] path')?.getAttribute('d')).toBe('M9 18l6-6-6-6');
      expect(sheet(fixture)).toBeNull();
    });

    it('the 圖示 row opens the sheet with the look block, the title 圖示 and focus on the checked source radio', async () => {
      await phoneSetup();
      const fixture = render(DETAIL);
      const doc = el(fixture).ownerDocument;
      click(fixture, 'button.row-look');

      const open = sheet(fixture)!;
      expect(open.getAttribute('aria-label')).toBe('圖示');
      expect(open.querySelector('h3')?.textContent?.trim()).toBe('圖示');
      expect(open.querySelector('app-account-look')).not.toBeNull();
      expect(open.querySelectorAll('app-account-look').length).toBe(1);
      expect(open.querySelector('app-account-look')!.classList).toContain('stacked');
      expect(open.querySelector('.field-error')).toBeNull();
      const checked = open.querySelector<HTMLElement>('.seg-toggle [aria-checked="true"]')!;
      expect(checked.textContent?.trim()).toBe('文字＋顏色');
      expect(doc.activeElement).toBe(checked);
      expect((doc.activeElement as HTMLElement).matches('input[type=file], .color-input')).toBe(false);
    });

    it('the 圖示 row focuses the 上傳圖片 radio, not the file input, for an uploaded image', async () => {
      await phoneSetup();
      const fixture = render({ ...DETAIL, logo_hash: 'h1' } as AccountDetail);
      const doc = el(fixture).ownerDocument;
      expect(texts(fixture, '.row-look .row-v')).toEqual(['上傳的圖片']);
      click(fixture, 'button.row-look');
      // The sheet's preview tile loads the uploaded image.
      http.expectOne('/api/accounting/accounts/11/logo.png?v=h1').flush(new Blob([new Uint8Array(1)], { type: 'image/png' }));

      const open = sheet(fixture)!;
      expect(open.querySelector('input[type=file]')).not.toBeNull();
      const checked = open.querySelector<HTMLElement>('.seg-toggle [aria-checked="true"]')!;
      expect(checked.textContent?.trim()).toBe('上傳圖片');
      expect(doc.activeElement).toBe(checked);
      expect(doc.activeElement).not.toBe(open.querySelector('input[type=file]'));
    });

    it('移除圖片 inside the sheet keeps focus in the sheet and Tab still wraps', async () => {
      await phoneSetup();
      const fixture = render({ ...DETAIL, logo_hash: 'h1' } as AccountDetail);
      const doc = el(fixture).ownerDocument;
      click(fixture, 'button.row-look');
      http.expectOne('/api/accounting/accounts/11/logo.png?v=h1').flush(new Blob([new Uint8Array(1)], { type: 'image/png' }));
      const open = sheet(fixture)!;

      const remove = open.querySelector<HTMLButtonElement>('.logo-remove')!;
      remove.focus();
      remove.click();
      fixture.detectChanges();
      const req = http.expectOne('/api/accounting/accounts/11/logo');
      expect(req.request.method).toBe('DELETE');
      req.flush(null, { status: 204, statusText: 'No Content' });
      fixture.detectChanges();

      expect(open.querySelector('.logo-remove')).toBeNull();
      expect(open.contains(doc.activeElement)).toBe(true);
      expect(doc.activeElement).toBe(open.querySelector('.seg-toggle [aria-checked="true"]'));

      const items = focusables(open);
      const last = items[items.length - 1];
      expect(last).toBe(open.querySelector('.sheet-done'));
      last.focus();
      const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
      last.dispatchEvent(tab);
      expect(tab.defaultPrevented).toBe(true);
      expect(doc.activeElement).toBe(items[0]);
    });

    it('picking a logo inside the sheet updates the row value and the payload', async () => {
      await phoneSetup();
      const fixture = render(DETAIL);
      click(fixture, 'button.row-look');

      sheetSource(fixture, '內建 logo');
      sheet(fixture)!.querySelector<HTMLButtonElement>('.logo-grid .logo-opt[aria-label="國泰世華"]')!.click();
      fixture.detectChanges();
      click(fixture, '.sheet .sheet-done');

      expect(sheet(fixture)).toBeNull();
      expect(texts(fixture, '.row-look .row-v')).toEqual(['內建 logo · 國泰世華']);
      click(fixture, '.save-bar .save-bar-save');
      const put = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/accounts/11');
      expect(put.request.body.logo).toBe('cathay-united');
      put.flush({ ...DETAIL, logo: 'cathay-united' });
      fixture.detectChanges();
    });

    it('Tab wraps inside the look sheet and Escape returns focus to the 圖示 row', async () => {
      await phoneSetup();
      const fixture = render(DETAIL);
      const doc = el(fixture).ownerDocument;
      click(fixture, 'button.row-look');
      const open = sheet(fixture)!;

      const items = focusables(open);
      const first = items[0];
      const last = items[items.length - 1];
      expect(first).toBe(open.querySelector('.seg-toggle [aria-checked="true"]'));
      expect(last).toBe(open.querySelector('.sheet-done'));
      expect(last.textContent?.trim()).toBe('完成');
      last.focus();
      const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
      last.dispatchEvent(tab);
      expect(tab.defaultPrevented).toBe(true);
      expect(doc.activeElement).toBe(first);

      // One tab stop per radio group on every source tab; the hidden file / colour inputs are never reachable.
      const reachable = (selector: string) => focusables(open).filter(e => e.matches(selector));
      expect(reachable('.seg-toggle [role=radio]').length).toBe(1);
      expect(reachable('.swatch').length).toBe(1);
      expect(reachable('input[type=file], .color-input')).toEqual([]);

      sheetSource(fixture, '內建 logo');
      expect(reachable('.seg-toggle [role=radio]').length).toBe(1);
      const logos = reachable('.logo-opt');
      expect(logos.length).toBe(1);
      expect(logos[0].getAttribute('tabindex')).toBe('0');
      expect(reachable('input[type=file], .color-input')).toEqual([]);

      sheetSource(fixture, '上傳圖片');
      expect(reachable('.seg-toggle [role=radio]').length).toBe(1);
      expect(open.querySelector('input[type=file]')).not.toBeNull();
      expect(reachable('input[type=file], .color-input')).toEqual([]);

      sheetSource(fixture, '文字＋顏色');
      expect(reachable('.seg-toggle [role=radio]').length).toBe(1);
      expect(reachable('.swatch').length).toBe(1);
      expect(open.querySelector('.color-input')).not.toBeNull();
      expect(reachable('input[type=file], .color-input')).toEqual([]);

      const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
      (doc.activeElement as HTMLElement).dispatchEvent(escape);
      fixture.detectChanges();

      expect(escape.defaultPrevented).toBe(true);
      expect(sheet(fixture)).toBeNull();
      expect(doc.activeElement).toBe(el(fixture).querySelector('button.row-look'));
    });

    it('at 390 px each rule row keeps the handle, the switch and ⋯', async () => {
      await phoneSetup();
      const fixture = render(DETAIL);

      const rows = Array.from(el(fixture).querySelectorAll('#s-reward app-account-rules .rule-row'));
      expect(rows.length).toBe(2);
      for (const row of rows) {
        expect(row.querySelector('.rule-handle')).not.toBeNull();
        expect(row.querySelector('app-switch.rule-enabled button[role="switch"]')).not.toBeNull();
        expect(row.querySelector('.rule-more')).not.toBeNull();
        expect(row.querySelector('.rule-open')).not.toBeNull();
      }
      // ⋯ carries 上移 / 下移 on a phone too.
      expect(openRuleMenu(fixture, 1).map(b => b.textContent?.trim())).toEqual(['編輯', '上移', '下移', '刪除']);
    });

    it('phone: 放棄/儲存 bar appears only when dirty', async () => {
      await phoneSetup();
      const fixture = render(DETAIL);
      expect(el(fixture).querySelector('.save-bar')).toBeNull();

      typeInto(fixture, '.name-input', '玉山 Only 2');

      expect(texts(fixture, '.save-bar > span')).toEqual(['有 1 項未儲存']);
      expect(texts(fixture, '.save-bar .discard')).toEqual(['放棄']);
      expect(texts(fixture, '.save-bar .save-bar-save')).toEqual(['儲存']);
      // The bar carries the count on a phone; the header line is for ≥ 760 px.
      expect(el(fixture).querySelector('.topbar .dirty-count')).toBeNull();

      TestBed.inject(LayoutModeService).set('panes');
      fixture.detectChanges();
      expect(el(fixture).querySelector('.save-bar')).toBeNull();
      expect(texts(fixture, '.topbar .dirty-count')).toEqual(['有 1 項未儲存']);
    });

    it('phone: the save bar sticks above the 64 px tab bar, not at the viewport bottom', async () => {
      await phoneSetup();
      const fixture = render(DETAIL);
      typeInto(fixture, '.name-input', '玉山 Only 2');

      const bar = el(fixture).querySelector<HTMLElement>('.save-bar')!;
      const style = getComputedStyle(bar);
      expect(style.position).toBe('sticky');
      expect(style.bottom).not.toBe('0px');
      expect(style.bottom).toContain('64px');
    });

    it('放棄 restores loaded values and hides the bar', async () => {
      await phoneSetup();
      const fixture = render(DETAIL);
      typeInto(fixture, '.name-input', '玉山 Only 2');
      click(fixture, '.row-closing_day');
      stepperClick(fixture, 'closing-day', 'inc');
      click(fixture, '.sheet .sheet-done');
      click(fixture, 'input[data-account-id="12"]');
      expect(texts(fixture, '.save-bar > span')).toEqual(['有 3 項未儲存']);

      click(fixture, '.save-bar .discard');

      expect(el(fixture).querySelector('.save-bar')).toBeNull();
      expect(fixture.componentInstance.dirtyCount()).toBe(0);
      expect(el(fixture).querySelector<HTMLInputElement>('.name-input')!.value).toBe('玉山 Only');
      expect(texts(fixture, '.row-closing_day .row-v')).toEqual(['15 號']);
      expect(el(fixture).querySelector<HTMLInputElement>('input[data-account-id="12"]')!.checked).toBe(false);
      expect(navigate).not.toHaveBeenCalled();
    });

    it('bar hides after a successful save', async () => {
      await phoneSetup();
      const fixture = render(DETAIL);
      typeInto(fixture, '.name-input', '玉山 Only 2');

      click(fixture, '.save-bar .save-bar-save');
      const put = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/accounts/11');
      expect(put.request.body.name).toBe('玉山 Only 2');
      put.flush({ ...DETAIL, name: '玉山 Only 2' });
      fixture.detectChanges();

      expect(el(fixture).querySelector('.save-bar')).toBeNull();
      expect(navigate).toHaveBeenCalledWith(['/accounting/accounts', 11]);
    });

    it('422 on combined_account_id jumps to 信用卡 and marks its chip invalid', async () => {
      await setup('11');
      const fixture = render(DETAIL);
      const scroll = spyScroll();

      el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
      http.expectOne('/api/accounting/accounts/11').flush(
        { detail: [{ loc: ['body', 'combined_account_id'], msg: 'master cycle' }] },
        { status: 422, statusText: 'Unprocessable Entity' },
      );
      fixture.detectChanges();

      expect(scroll).toHaveBeenCalledTimes(1);
      expect((scroll.mock.contexts[0] as Element).id).toBe('s-card');
      expect(fixture.componentInstance.invalidSections()).toEqual(new Set(['card']));
      const chips = Array.from(el(fixture).querySelectorAll<HTMLButtonElement>('nav.sec-chips button'));
      expect(chips.filter(c => c.classList.contains('invalid')).map(c => c.textContent?.trim())).toEqual(['信用卡']);
      expect(fixture.componentInstance.activeSection()).toBe('card');
      expect(el(fixture).querySelector('app-picker.master-select')?.contains(el(fixture).ownerDocument.activeElement)).toBe(true);

      // Changing the offending field clears the chip.
      pickerOptions(fixture, 'master-select').find(o => o.textContent?.includes('無'))!.click();
      fixture.detectChanges();
      expect(fixture.componentInstance.invalidSections().size).toBe(0);
    });

    it('422 on auto_pay_account_id shows the server text beside 自動扣繳 (desktop)', async () => {
      await setup('11');
      TestBed.inject(LayoutModeService).set('panes');
      const fixture = render(DETAIL);
      spyScroll();

      el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
      http.expectOne('/api/accounting/accounts/11').flush(
        { detail: [{ loc: ['body', 'auto_pay_account_id'], msg: 'autopay account archived' }] },
        { status: 422, statusText: 'Unprocessable Entity' },
      );
      fixture.detectChanges();

      expect(texts(fixture, '#s-card .field-error[data-field="auto_pay_account_id"]')).toEqual(['autopay account archived']);
      const chips = Array.from(el(fixture).querySelectorAll<HTMLButtonElement>('nav.sec-chips button'));
      expect(chips.filter(c => c.classList.contains('invalid')).map(c => c.textContent?.trim())).toEqual(['信用卡']);
      expect(el(fixture).querySelector('.form-message')).toBeNull();
    });

    it('local 結帳日 error jumps to 基本', async () => {
      await setup('11');
      const fixture = render(DETAIL);
      const scroll = spyScroll();
      fixture.componentInstance.set('closing_day', '40');
      fixture.detectChanges();

      el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
      fixture.detectChanges();

      expect(scroll).toHaveBeenCalledTimes(1);
      expect((scroll.mock.contexts[0] as Element).id).toBe('s-basic');
      expect(fixture.componentInstance.invalidSections()).toEqual(new Set(['basic']));
      expect(el(fixture).querySelector('app-stepper.closing-day')?.contains(el(fixture).ownerDocument.activeElement)).toBe(true);
      expect(navigate).not.toHaveBeenCalled();

      // A successful save clears the mark.
      fixture.componentInstance.set('closing_day', '20');
      fixture.componentInstance.invalidSections.set(new Set(['basic']));
      el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
      http.expectOne(r => r.method === 'PUT').flush(DETAIL);
      expect(fixture.componentInstance.invalidSections().size).toBe(0);
    });

    it('結帳後 over 120 days is stopped locally and marks 信用卡 (desktop)', async () => {
      await setup('11');
      TestBed.inject(LayoutModeService).set('panes');
      const fixture = render(DETAIL);
      spyScroll();
      fixture.componentInstance.set('due_rule', 'days_after_closing');
      fixture.componentInstance.set('due_value', '121');
      fixture.detectChanges();

      el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
      fixture.detectChanges();

      http.expectNone('/api/accounting/accounts/11');
      expect(texts(fixture, '#s-card .field-error[data-field="due_value"]')).toEqual(['請輸入大於 0 的天數']);
      expect(fixture.componentInstance.invalidSections()).toEqual(new Set(['card']));
      const chips = Array.from(el(fixture).querySelectorAll<HTMLButtonElement>('nav.sec-chips button'));
      expect(chips.filter(c => c.classList.contains('invalid')).map(c => c.textContent?.trim())).toEqual(['信用卡']);
      expect(navigate).not.toHaveBeenCalled();
    });

    it('a blank due value under 每月固定日 is stopped locally with 請填繳款日或天數 (desktop)', async () => {
      await setup('11');
      TestBed.inject(LayoutModeService).set('panes');
      const fixture = render(DETAIL);
      spyScroll();
      fixture.componentInstance.set('due_rule', 'fixed_day');
      fixture.componentInstance.set('due_value', '');
      fixture.detectChanges();

      el(fixture).querySelector<HTMLButtonElement>('.save')!.click();
      fixture.detectChanges();

      http.expectNone('/api/accounting/accounts/11');
      expect(texts(fixture, '#s-card .field-error[data-field="due_value"]')).toEqual(['請填繳款日或天數']);
      expect(fixture.componentInstance.invalidSections()).toEqual(new Set(['card']));
      expect(navigate).not.toHaveBeenCalled();
    });

    it('empty name jumps to 基本', async () => {
      await phoneSetup();
      const fixture = render(DETAIL);
      const scroll = spyScroll();
      typeInto(fixture, '.name-input', '  ');

      click(fixture, '.save-bar .save-bar-save');

      expect((scroll.mock.contexts[0] as Element).id).toBe('s-basic');
      expect(fixture.componentInstance.invalidSections()).toEqual(new Set(['basic']));
      const name = el(fixture).querySelector<HTMLInputElement>('.name-input')!;
      expect(name.getAttribute('aria-invalid')).toBe('true');
      expect(el(fixture).ownerDocument.activeElement).toBe(name);
      expect(texts(fixture, '.field-error[data-field="name"]')).toEqual(['請輸入名稱']);

      typeInto(fixture, '.name-input', '玉山 Only');
      expect(fixture.componentInstance.invalidSections().size).toBe(0);
    });
  });
});
