import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { LedgerAccount, Project, RewardRule } from '../../../models/accounting.model';
import { RewardRuleSheetComponent } from './reward-rule-sheet';

const BASE = '/api/accounting/accounts/11/reward-rules';

function rule(fields: Partial<RewardRule>): RewardRule {
  return {
    id: 0, account_id: 11, name: '', method: 'percent', rate: null, fixed_amount: null, unit_spend: null, window: 'statement_cycle',
    posting: 'after_window', delay_days: 0, post_month_offset: 0, post_day: 1, txn_rounding: 'keep',
    total_rounding: 'keep', total_cap: null, shared_cap_id: null, is_basic: false, reward_account_id: null,
    reward_project_id: null, starts_on: null, ends_on: null, is_enabled: true, description: null, sort_order: 0,
    moze_id: null, ...fields,
  };
}

const ACCOUNTS = [
  { id: 11, name: '華航卡', is_archived: false, is_credit: true },
  { id: 20, name: '華航哩程', is_archived: false, is_credit: false },
  { id: 21, name: '舊點數', is_archived: true, is_credit: false },
] as unknown as LedgerAccount[];

const PROJECTS = [{ id: 5, name: '旅行', is_archived: false, sort_order: 0, moze_id: null }] as Project[];

const BASIC = rule({
  id: 1, name: '一般', rate: '1.0000', posting: 'after_window', post_month_offset: 1, post_day: 15,
  txn_rounding: 'floor', total_cap: '300.0000', shared_cap_id: 'cap-a', is_basic: true, starts_on: '2026-01-01',
});
const DINING = rule({ id: 2, name: '餐飲', rate: '5.2000', shared_cap_id: 'cap-a', sort_order: 1 });
const OVERSEAS = rule({ id: 3, name: '海外', method: 'fixed', fixed_amount: '50.0000', sort_order: 2 });

describe('RewardRuleSheetComponent', () => {
  let http: HttpTestingController;

  afterEach(() => {
    http.verify();
    document.body.innerHTML = '';
  });

  function render(current: RewardRule | null, siblings: RewardRule[] = [], startDelete = false) {
    TestBed.configureTestingModule({
      imports: [RewardRuleSheetComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(RewardRuleSheetComponent);
    fixture.componentRef.setInput('accountId', 11);
    fixture.componentRef.setInput('currency', 'TWD');
    fixture.componentRef.setInput('rule', current);
    if (startDelete) {
      fixture.componentRef.setInput('startDelete', true);
    }
    fixture.componentRef.setInput('siblings', siblings);
    fixture.componentRef.setInput('accounts', ACCOUNTS);
    fixture.componentRef.setInput('projects', PROJECTS);
    document.body.appendChild(fixture.nativeElement);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const q = <T extends HTMLElement>(selector: string) => el.querySelector(selector) as T | null;
    const type = (selector: string, text: string) => {
      const input = q<HTMLInputElement>(selector)!;
      input.value = text;
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();
    };
    const pick = (selector: string, value: string) => {
      const select = q<HTMLSelectElement>(selector)!;
      select.value = value;
      select.dispatchEvent(new Event('change'));
      fixture.detectChanges();
    };
    const click = (selector: string) => {
      q<HTMLButtonElement>(selector)!.click();
      fixture.detectChanges();
    };
    const saved = vi.fn();
    const deleted = vi.fn();
    const closed = vi.fn();
    fixture.componentInstance.saved.subscribe(saved);
    fixture.componentInstance.deleted.subscribe(deleted);
    fixture.componentInstance.closed.subscribe(closed);
    return { fixture, el, q, type, pick, click, saved, deleted, closed };
  }

  it('(a) creates a rule without share_cap_with or sort_order and emits saved', () => {
    const { el, q, type, click, saved } = render(null, [DINING]);

    expect(Array.from(el.querySelectorAll('fieldset > legend')).map(l => l.textContent?.trim()))
      .toEqual(['基本', '入帳', '上限', '期間']);
    expect(el.querySelector('.sheet')!.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).toBe(q('.rule-name'));
    const accountOptions = Array.from(q<HTMLSelectElement>('.rule-reward-account')!.options).map(o => o.textContent?.trim());
    expect(accountOptions).toEqual(['華航卡', '華航哩程']);
    expect(q<HTMLSelectElement>('.rule-reward-account')!.value).toBe('11');
    expect(Array.from(q<HTMLSelectElement>('.rule-project')!.options).map(o => o.textContent?.trim())).toEqual(['無', '旅行']);
    expect(q('.rule-delete')).toBeNull();

    type('.rule-name', ' 一般 ');
    type('.rule-rate', '1.5');
    type('.rule-cap', '');
    click('.sheet-confirm');

    const req = http.expectOne(BASE);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({
      name: '一般', method: 'percent', rate: '1.5', fixed_amount: null, unit_spend: null, window: 'statement_cycle', posting: 'after_window', delay_days: 0,
      post_month_offset: 0, post_day: 1, txn_rounding: 'keep', total_rounding: 'keep', total_cap: null,
      is_basic: false, reward_account_id: 11, reward_project_id: null, starts_on: null, ends_on: null,
      is_enabled: true, description: null,
    });
    expect('share_cap_with' in req.request.body).toBe(false);
    expect('sort_order' in req.request.body).toBe(false);
    const created = rule({ id: 9, name: '一般', rate: '1.5000' });
    req.flush(created);
    expect(saved).toHaveBeenCalledWith(created);
  });

  it('(b) preloads an edit and toggles the dependent posting fields', () => {
    const { q, pick, type, click } = render(BASIC, [DINING, OVERSEAS]);

    expect(q<HTMLInputElement>('.rule-name')!.value).toBe('一般');
    expect(q<HTMLSelectElement>('.rule-method')!.value).toBe('percent');
    expect(q<HTMLInputElement>('.rule-rate')!.value).toBe('1');
    expect(q('.rule-fixed')).toBeNull();
    expect(q<HTMLInputElement>('.rule-offset')!.value).toBe('1');
    expect(q<HTMLInputElement>('.rule-day')!.value).toBe('15');
    expect(q('.rule-delay')).toBeNull();
    expect(q<HTMLSelectElement>('.rule-txn-rounding')!.value).toBe('floor');
    expect(q<HTMLInputElement>('.rule-cap')!.value).toBe('300');
    expect(q<HTMLSelectElement>('.rule-share')!.value).toBe('2');
    expect(q('.rule-basic')!.getAttribute('aria-checked')).toBe('true');
    expect(q<HTMLSelectElement>('.rule-reward-account')!.value).toBe('11');
    expect(q<HTMLInputElement>('.rule-starts')!.value).toBe('2026-01-01');
    expect(q('.rule-enabled')!.getAttribute('aria-checked')).toBe('true');

    pick('.rule-posting', 'after_transaction');
    expect(q('.rule-offset')).toBeNull();
    expect(q('.rule-day')).toBeNull();
    type('.rule-delay', '3');
    click('.sheet-confirm');

    const req = http.expectOne(`${BASE}/1`);
    expect(req.request.method).toBe('PUT');
    expect(req.request.body).toMatchObject({
      posting: 'after_transaction', delay_days: 3, post_month_offset: 0, post_day: 1, rate: '1', total_cap: '300',
      is_basic: true, reward_account_id: 11, starts_on: '2026-01-01',
    });
    expect('share_cap_with' in req.request.body).toBe(false);
    req.flush(BASIC);
  });

  it('(c) sends the picked sibling, and none when leaving an existing cap', () => {
    const first = render(OVERSEAS, [BASIC, DINING]);
    expect(first.q<HTMLSelectElement>('.rule-share')!.value).toBe('');
    first.pick('.rule-share', '2');
    first.click('.sheet-confirm');
    const join = http.expectOne(`${BASE}/3`);
    expect(join.request.body.share_cap_with).toBe(2);
    expect(join.request.body).toMatchObject({ method: 'fixed', rate: null, fixed_amount: '50' });
    join.flush(OVERSEAS);
    TestBed.resetTestingModule();
    document.body.innerHTML = '';

    const second = render(BASIC, [DINING, OVERSEAS]);
    second.pick('.rule-share', '');
    second.click('.sheet-confirm');
    const leave = http.expectOne(`${BASE}/1`);
    expect(leave.request.body.share_cap_with).toBe('none');
    leave.flush(BASIC);
  });

  it('(d) renders a 422 under its field and validates locally first', () => {
    const { fixture, el, q, type, click, saved } = render(null);

    click('.sheet-confirm');
    expect(q('small.err[data-field="name"]')?.textContent?.trim()).toBe('請輸入名稱');
    expect(q('small.err[data-field="rate"]')?.textContent?.trim()).toBe('比例須大於 0 且不超過 100');
    type('.rule-name', '一般');
    type('.rule-rate', '120');
    click('.sheet-confirm');
    expect(q('small.err[data-field="name"]')).toBeNull();
    expect(q('small.err[data-field="rate"]')).not.toBeNull();
    type('.rule-rate', '1.5');
    type('.rule-starts', '2026-05-01');
    type('.rule-ends', '2026-04-30');
    click('.sheet-confirm');
    expect(q('small.err[data-field="ends_on"]')?.textContent?.trim()).toBe('迄日不可早於起日');
    http.expectNone(BASE);

    type('.rule-ends', '');
    click('.sheet-confirm');
    http.expectOne(BASE).flush(
      { detail: [{ loc: ['body', 'rate'], msg: '最多 4 位小數', type: 'value_error' }] },
      { status: 422, statusText: 'Unprocessable Entity' },
    );
    fixture.detectChanges();
    expect(el.querySelector('small.err[data-field="rate"]')?.textContent?.trim()).toBe('最多 4 位小數');
    expect(saved).not.toHaveBeenCalled();
  });

  it('(d2) refuses non-integer 月 / 日 / 天 locally, only for the visible fields', () => {
    const { q, type, pick, click } = render(OVERSEAS);

    expect(q('.rule-fixed')?.getAttribute('aria-label')).toBe('固定金額');
    type('.rule-offset', '1.5');
    type('.rule-day', 'abc');
    click('.sheet-confirm');
    expect(q('small.err[data-field="post_month_offset"]')?.textContent?.trim()).toBe('請輸入整數');
    expect(q('small.err[data-field="post_day"]')?.textContent?.trim()).toBe('請輸入整數');
    http.expectNone(`${BASE}/3`);

    pick('.rule-posting', 'after_transaction');
    type('.rule-delay', '');
    click('.sheet-confirm');
    expect(q('small.err[data-field="post_month_offset"]')).toBeNull();
    expect(q('small.err[data-field="post_day"]')).toBeNull();
    expect(q('small.err[data-field="delay_days"]')?.textContent?.trim()).toBe('請輸入整數');
    http.expectNone(`${BASE}/3`);

    type('.rule-delay', ' 7 ');
    click('.sheet-confirm');
    const req = http.expectOne(`${BASE}/3`);
    expect(req.request.body).toMatchObject({ posting: 'after_transaction', delay_days: 7, post_month_offset: 0, post_day: 1 });
    req.flush(OVERSEAS);
  });

  it('(e) names the sibling of a 409 basic_rule_overlap', () => {
    const { fixture, q, click, saved } = render(rule({ id: 4, name: '新基本', rate: '0.5000', is_basic: true }), [BASIC]);

    click('.sheet-confirm');
    http.expectOne(`${BASE}/4`).flush(
      { code: 409, message: 'basic_rule_overlap: rule 1', trace_id: 't' },
      { status: 409, statusText: 'Conflict' },
    );
    fixture.detectChanges();
    expect(q('.rule-message span')?.textContent?.trim()).toBe('與「一般」的基本回饋期間重疊');
    expect(q('.rule-disable')).toBeNull();
    expect(saved).not.toHaveBeenCalled();
  });

  it('words an accrual-only rule_referenced without 0 counts (#294)', () => {
    const { fixture, q, click } = render(BASIC, [DINING]);
    click('.rule-delete');
    click('.rule-delete');
    http.expectOne(`${BASE}/1`).flush(
      { code: 409, message: 'rule_referenced: {"entries": 0, "rewards": 0, "accruals": 4}', trace_id: 't' },
      { status: 409, statusText: 'Conflict' },
    );
    fixture.detectChanges();
    expect(q('.rule-message span')?.textContent?.trim()).toBe('此規則已有 4 筆預估回饋引用，改為停用？');
  });

  it('(f) offers 停用 when delete hits rule_referenced', () => {
    const { fixture, q, click, saved, deleted } = render(BASIC, [DINING]);

    click('.rule-delete');
    expect(q('.rule-delete')?.textContent?.trim()).toBe('確定刪除？');
    http.expectNone(`${BASE}/1`);
    click('.rule-delete');
    const del = http.expectOne(`${BASE}/1`);
    expect(del.request.method).toBe('DELETE');
    del.flush(
      { code: 409, message: 'rule_referenced: {"entries": 3, "rewards": 2}', trace_id: 't' },
      { status: 409, statusText: 'Conflict' },
    );
    fixture.detectChanges();
    expect(q('.rule-message span')?.textContent?.trim()).toBe('此規則已有 3 筆交易 / 2 筆回饋引用，改為停用？');
    expect(q('.rule-disable')?.textContent?.trim()).toBe('停用');
    expect(deleted).not.toHaveBeenCalled();

    click('.rule-disable');
    const put = http.expectOne(`${BASE}/1`);
    expect(put.request.method).toBe('PUT');
    expect(put.request.body).toEqual({
      name: '一般', method: 'percent', rate: '1', fixed_amount: null, unit_spend: null, window: 'statement_cycle', posting: 'after_window', delay_days: 0,
      post_month_offset: 1, post_day: 15, txn_rounding: 'floor', total_rounding: 'keep', total_cap: '300',
      is_basic: true, reward_account_id: null, reward_project_id: null, starts_on: '2026-01-01', ends_on: null,
      is_enabled: false, description: null,
    });
    const disabled = { ...BASIC, is_enabled: false };
    put.flush(disabled);
    expect(saved).toHaveBeenCalledWith(disabled);
  });

  it('per_unit shows unit_spend and submits it', () => {
    const { q, pick, type, click, fixture } = render(null);
    expect(q('.rule-unit-spend')).toBeNull();
    pick('.rule-method', 'per_unit');
    expect(q('.rule-unit-spend')).not.toBeNull();
    expect(q('.rule-rate')).toBeNull();
    expect(q('.rule-fixed')).toBeNull();
    type('.rule-name', '測試卡點數');
    type('.rule-unit-spend', '30');
    click('.sheet-confirm');
    const req = http.expectOne(BASE);
    expect(req.request.body).toMatchObject({ method: 'per_unit', unit_spend: '30', rate: null, fixed_amount: null });
    req.flush(rule({ id: 9, method: 'per_unit', unit_spend: '30.0000' }));
    fixture.detectChanges();
  });

  it('per_unit requires a positive unit_spend and other methods send none', () => {
    const { q, pick, type, click } = render(null);
    pick('.rule-method', 'per_unit');
    type('.rule-name', '測試卡點數');
    click('.sheet-confirm');
    expect(q('[data-field="unit_spend"]')).not.toBeNull();
    pick('.rule-method', 'percent');
    type('.rule-rate', '2');
    click('.sheet-confirm');
    expect(http.expectOne(BASE).request.body.unit_spend).toBeNull();
  });

  it('calendar_month window round-trips', () => {
    const { q, pick, click } = render(rule({ id: 4, name: '月回饋', rate: '2.0000', window: 'calendar_month' }));
    expect(q<HTMLSelectElement>('.rule-window')!.value).toBe('calendar_month');
    expect(Array.from(q<HTMLSelectElement>('.rule-window')!.options).map(o => o.textContent?.trim())).toEqual(['帳單週期', '自然月']);
    click('.sheet-confirm');
    const put = http.expectOne(`${BASE}/4`);
    expect(put.request.body.window).toBe('calendar_month');
    put.flush(rule({ id: 4, window: 'calendar_month' }));
    pick('.rule-window', 'statement_cycle');
  });

  it('(f2) deletes an unreferenced rule', () => {
    const { click, deleted } = render(BASIC);
    click('.rule-delete');
    click('.rule-delete');
    http.expectOne(`${BASE}/1`).flush(null, { status: 204, statusText: 'No Content' });
    expect(deleted).toHaveBeenCalledWith(1);
  });

  it('startDelete opens with the delete confirmation showing and focuses it', () => {
    const { q, click, deleted } = render(BASIC, [DINING], true);

    const del = q<HTMLButtonElement>('.rule-delete')!;
    expect(del.classList).toContain('armed');
    expect(del.textContent?.trim()).toBe('確定刪除？');
    expect(document.activeElement).toBe(del);

    // One more press deletes, as after the 刪除 button's first press.
    click('.rule-delete');
    const req = http.expectOne(`${BASE}/1`);
    expect(req.request.method).toBe('DELETE');
    req.flush(null, { status: 204, statusText: 'No Content' });
    expect(deleted).toHaveBeenCalledWith(1);
  });

  it('startDelete with rule === null is ignored', () => {
    const { q, fixture } = render(null, [DINING], true);

    expect(q('.rule-delete')).toBeNull();
    expect(fixture.componentInstance.confirmingDelete()).toBe(false);
    expect(document.activeElement).toBe(q('.rule-name'));
  });

  it('after a rule_referenced error the delete button is not re-armed', () => {
    const { fixture, q, click } = render(BASIC, [DINING], true);

    click('.rule-delete');
    http.expectOne(`${BASE}/1`).flush(
      { code: 409, message: 'rule_referenced: {"entries": 3, "rewards": 2}', trace_id: 't' },
      { status: 409, statusText: 'Conflict' },
    );
    fixture.detectChanges();

    const del = q<HTMLButtonElement>('.rule-delete')!;
    expect(del.classList).not.toContain('armed');
    expect(del.textContent?.trim()).toBe('刪除');
    expect(q('.rule-message span')?.textContent?.trim()).toBe('此規則已有 3 筆交易 / 2 筆回饋引用，改為停用？');
    expect(q('.rule-disable')?.textContent?.trim()).toBe('停用');
  });

  it('(g) Esc on a dirty form asks first; 放棄 emits closed', () => {
    const { fixture, el, q, type, closed } = render(BASIC, [DINING]);
    const esc = () => {
      const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
      q('.rule-name')!.dispatchEvent(event);
      fixture.detectChanges();
      return event;
    };

    expect(esc().defaultPrevented).toBe(true);
    expect(closed).toHaveBeenCalledTimes(1);
    closed.mockClear();

    type('.rule-name', '一般回饋');
    esc();
    expect(closed).not.toHaveBeenCalled();
    expect(q('.discard')?.textContent).toContain('放棄未儲存的內容？');
    (Array.from(el.querySelectorAll<HTMLButtonElement>('.discard button')).find(b => b.textContent?.trim() === '留下'))!.click();
    fixture.detectChanges();
    expect(q('.discard')).toBeNull();
    type('.rule-name', '一般回饋!');
    expect(closed).not.toHaveBeenCalled();
    (el.querySelector('.overlay') as HTMLElement).click();
    fixture.detectChanges();
    expect(q('.discard')).not.toBeNull();
    (Array.from(el.querySelectorAll<HTMLButtonElement>('.discard button')).find(b => b.textContent?.trim() === '放棄'))!.click();
    expect(closed).toHaveBeenCalledTimes(1);
  });

  it('(g2) ignores 取消 / Esc / overlay while a save is in flight; the response still emits saved', () => {
    const { fixture, q, type, click, saved, closed } = render(null);

    type('.rule-name', '一般');
    type('.rule-rate', '1');
    click('.sheet-confirm');
    const req = http.expectOne(BASE);
    expect(q<HTMLButtonElement>('.sheet-cancel')!.disabled).toBe(true);
    q('.rule-name')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    (fixture.nativeElement.querySelector('.overlay') as HTMLElement).click();
    fixture.componentInstance.cancel();
    fixture.detectChanges();
    expect(q('.discard')).toBeNull();
    expect(closed).not.toHaveBeenCalled();

    const created = rule({ id: 9, name: '一般', rate: '1.0000' });
    req.flush(created);
    expect(saved).toHaveBeenCalledWith(created);
    expect(closed).not.toHaveBeenCalled();
  });

  it('keeps Tab inside the sheet', () => {
    const { q } = render(BASIC);
    const dialog = q('.sheet')!;
    const buttons = dialog.querySelectorAll<HTMLButtonElement>('button');
    buttons[buttons.length - 1].focus();
    const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    buttons[buttons.length - 1].dispatchEvent(tab);
    expect(tab.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(q('.rule-name'));
  });
});
