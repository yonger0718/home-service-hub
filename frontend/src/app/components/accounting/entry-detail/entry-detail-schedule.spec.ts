import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EntryDetail, EntryScheduleLink } from '../../../models/accounting.model';
import { AccountingToastService } from '../accounting-toast';
import { makeAccount, makeDefinition, makeEntry, makeEntryDetail, makeInstance } from '../testing/fixtures';
import { EntryDetailComponent } from './entry-detail';

const ACCOUNTS = [makeAccount({ id: 1, name: '薪轉' })];

function link(overrides: Partial<EntryScheduleLink> = {}): EntryScheduleLink {
  return {
    definition_id: 12, instance_id: 77, kind: 'installment', seq: 1, times: 36, name: '信貸 每月還款', is_partial: false,
    acted_by: 'auto', posted_entry_ids: [42, 43], ...overrides,
  };
}

const REPAYMENT = makeEntryDetail({
  id: 42, kind: 'payable', amount: '-8333.0000', is_settlement: true, name: '信貸 每月還款', account_id: 1, account_name: '薪轉',
  schedule: link(), group: { id: 5, kind: 'installment', name: '信貸 每月還款 #1/36', merchant: null, description: null, count: 2, total: '-8953.0000', currency: 'TWD' },
  group_members: [
    makeEntry({ id: 42, kind: 'payable', amount: '-8333.0000' }),
    makeEntry({ id: 43, kind: 'interest', amount: '-620.0000' }),
  ],
});

const DEFINITION = {
  ...makeDefinition({
    id: 12, kind: 'installment', name: '信貸 每月還款', times: 36,
    template: {
      lines: [
        { ...makeDefinition().template.lines[0], kind: 'repayment', account_id: 1, amount: '8333', loan_entry_id: 4021 },
        { ...makeDefinition().template.lines[0], kind: 'interest', account_id: 1, amount: '620' },
      ],
      description: null,
      tags: [],
    },
  }),
  instances: [makeInstance({ id: 77, definition_id: 12, status: 'posted', amounts: ['8333', '620'], posted_entry_ids: [42, 43] })],
};

describe('EntryDetailComponent schedules', () => {
  let http: HttpTestingController;
  let navigate: ReturnType<typeof vi.fn>;

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
    navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true) as unknown as ReturnType<typeof vi.fn>;
  });

  afterEach(() => http.verify());

  function render(body: EntryDetail): { fixture: ComponentFixture<EntryDetailComponent>; el: HTMLElement } {
    const fixture = TestBed.createComponent(EntryDetailComponent);
    fixture.detectChanges();
    http.expectOne('/api/accounting/entries/42').flush(body);
    http.expectOne(r => r.url === '/api/accounting/accounts').flush(ACCOUNTS);
    fixture.detectChanges();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  function text(node: Element | null | undefined): string {
    return node?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  }

  function click(fixture: ComponentFixture<EntryDetailComponent>, selector: string): void {
    ((fixture.nativeElement as HTMLElement).querySelector(selector) as HTMLButtonElement).click();
    fixture.detectChanges();
  }

  it('shows the loan line and links to its schedule', () => {
    // Spec "Loan detail line".
    const loan = makeEntryDetail({
      id: 42, kind: 'payable', amount: '300000.0000', open_amount: '275001.0000', is_settled: false, name: '信貸',
      settled_by: [makeEntry({ id: 50, kind: 'payable', amount: '-24999.0000', is_settlement: true })],
      loan_schedule: {
        definition_id: 12, name: '信貸 每月還款', status: 'active', posting_mode: 'auto', posted_count: 3, times: 36,
        next_due_date: '2027-02-09', next_amount: [{ currency: 'TWD', amount: '-8953.0000' }], remaining: '-275001.0000',
        repaid: '24999.0000', needs_check: false,
      },
    });
    const { fixture, el } = render(loan);
    expect(text(el.querySelector('.loan-line'))).toBe('剩餘 −$275,001 · 已還 $24,999 · 下期 02/09');
    expect(text(el.querySelector('.loan-link'))).toBe('信貸 每月還款 · 已入帳 3 / 36');
    expect(el.querySelector('.breakdown')).toBeNull();
    expect(el.querySelector('.open-amount')).toBeNull(); // 剩餘 once, signed, in the loan line
    click(fixture, '.loan-link');
    expect(navigate).toHaveBeenCalledWith(['/accounting/reminders'], { queryParams: { tab: 'debts', schedule: 12 } });
  });

  it('words a lent-money loan with 已收', () => {
    const lent = makeEntryDetail({
      id: 42, kind: 'receivable', amount: '-50000.0000', open_amount: '30000.0000', is_settled: false, name: '借款',
      loan_schedule: {
        definition_id: 13, name: '借款 每月收回', status: 'active', posting_mode: 'auto', posted_count: 4, times: 10,
        next_due_date: null, next_amount: [], remaining: '30000.0000', repaid: '20000.0000', needs_check: false,
      },
    });
    const { el } = render(lent);
    expect(text(el.querySelector('.loan-line'))).toBe('剩餘 $30,000 · 已收 $20,000');
  });

  it('sends a member of a scheduled split group to the repost panel, never to the entry form', () => {
    // Two plain lines post an entry_group of kind split; the entry form would save a member with PUT /splits/{id},
    // which assert_group_not_scheduled refuses (409, D29), so 編輯這一筆 goes to the repost panel.
    const member = makeEntryDetail({
      id: 42, kind: 'expense', amount: '-390.0000', name: '串流組合', account_id: 1, account_name: '薪轉',
      schedule: link({ kind: 'recurring', times: null, name: '串流組合', posted_entry_ids: [42, 44] }),
      group: { id: 6, kind: 'split', name: '串流組合 #1', merchant: null, description: null, count: 2, total: '-539.0000', currency: 'TWD' },
      group_members: [makeEntry({ id: 42, amount: '-390.0000' }), makeEntry({ id: 44, amount: '-149.0000' })],
    });
    const { fixture, el } = render(member);
    click(fixture, '.action-edit');
    http.expectOne('/api/accounting/schedules/definitions/12').flush({ ...DEFINITION, kind: 'recurring' });
    fixture.detectChanges();
    click(fixture, '.scope-one');
    expect(navigate).not.toHaveBeenCalledWith(['/accounting/entries', 42, 'edit']);
    expect(el.querySelectorAll('.repost-amount').length).toBe(2);
  });

  it('shows the 分期 pill and opens its schedule', () => {
    const { fixture, el } = render(REPAYMENT);
    expect(text(el.querySelector('.schedule-pill'))).toBe('分期 #1/36');
    click(fixture, '.schedule-pill');
    expect(navigate).toHaveBeenCalledWith(['/accounting/reminders'], { queryParams: { tab: 'debts', schedule: 12 } });
  });

  it('offers 重新入帳 and 保留部分 on a partial period', () => {
    // Spec "Partial period pill".
    const { fixture, el } = render({ ...REPAYMENT, schedule: link({ is_partial: true, posted_entry_ids: [42] }) });
    expect(text(el.querySelector('.schedule-pill .badge.partial'))).toBe('部分');
    expect(el.querySelector('.partial-repost')).not.toBeNull();
    click(fixture, '.partial-accept');
    http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/instances/77/accept-partial').flush(makeInstance({ id: 77 }));
    fixture.detectChanges();
    http.expectOne('/api/accounting/entries/42').flush(REPAYMENT);
  });

  it("corrects one period's interest with 編輯這一筆", () => {
    // Spec "Correct one period's interest".
    const { fixture, el } = render(REPAYMENT);
    expect((el.querySelector('.action-edit') as HTMLButtonElement).disabled).toBe(false);
    click(fixture, '.action-edit');
    http.expectOne('/api/accounting/schedules/definitions/12').flush(DEFINITION);
    fixture.detectChanges();
    click(fixture, '.scope-one');
    const inputs = Array.from(el.querySelectorAll<HTMLInputElement>('.repost-amount'));
    expect(inputs.map(input => input.value)).toEqual(['8333', '620']);
    inputs[1].value = '598';
    inputs[1].dispatchEvent(new Event('input'));
    fixture.detectChanges();
    click(fixture, '.repost-submit');
    const req = http.expectOne('/api/accounting/schedules/instances/77/repost');
    expect(req.request.body).toEqual({ amounts: ['8333', '598'] });
    req.flush(makeInstance({ id: 77, status: 'posted', posted_entry_ids: [201, 202] }));
    fixture.detectChanges();
    expect(navigate).toHaveBeenCalledWith(['/accounting/entries', 201], { replaceUrl: true });
  });

  it('opens the whole schedule in the entry form, or shows the lock for an imported one', () => {
    const { fixture } = render(REPAYMENT);
    click(fixture, '.action-edit');
    http.expectOne('/api/accounting/schedules/definitions/12').flush(DEFINITION);
    fixture.detectChanges();
    click(fixture, '.scope-all');
    expect(navigate).toHaveBeenCalledWith(['/accounting/entry'], { queryParams: { schedule: 12 } });

    const locked = render(REPAYMENT);
    click(locked.fixture, '.action-edit');
    http.expectOne('/api/accounting/schedules/definitions/12').flush({ ...DEFINITION, imported: true, locked: true });
    locked.fixture.detectChanges();
    expect(locked.el.querySelector('.scope-all')).toBeNull();
    expect(text(locked.el.querySelector('.scope-locked'))).toBe('MOZE 匯入資料，切換後可編輯');
  });

  it('offers no 編輯整個排程 for an ended schedule', () => {
    // Final review F3: an ended definition cannot be edited (PUT answers 409 definition_ended).
    const { fixture, el } = render(REPAYMENT);
    click(fixture, '.action-edit');
    http.expectOne('/api/accounting/schedules/definitions/12').flush({ ...DEFINITION, status: 'ended' });
    fixture.detectChanges();
    const all = el.querySelector('.scope-all') as HTMLButtonElement | null;
    expect(all === null || all.disabled).toBe(true);
    fixture.componentInstance.editSchedule();
    expect(navigate).not.toHaveBeenCalledWith(['/accounting/entry'], { queryParams: { schedule: 12 } });
  });

  for (const bad of ['', 'abc']) {
    it(`refuses a repost with an ${bad ? 'invalid' : 'empty'} amount and sends nothing`, () => {
      // Final review F5: the repost panel validates every amount before sending.
      const { fixture, el } = render(REPAYMENT);
      click(fixture, '.action-edit');
      http.expectOne('/api/accounting/schedules/definitions/12').flush(DEFINITION);
      fixture.detectChanges();
      click(fixture, '.scope-one');
      const input = el.querySelectorAll<HTMLInputElement>('.repost-amount')[1];
      input.value = bad;
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();
      click(fixture, '.repost-submit');
      http.expectNone('/api/accounting/schedules/instances/77/repost');
      expect(text(el.querySelector('.repost-form .form-error'))).toBe('金額格式不正確');
    });
  }

  it("words what deleting does to the entry's period", () => {
    // Spec "Deleting a MOZE-booked period's last entry" and the 部分入帳 wording.
    const partial = render(REPAYMENT);
    click(partial.fixture, '.action-delete');
    expect(text(partial.el.querySelector('.schedule-delete-note'))).toBe('同期的 利息 −$620 會保留，此期標示為部分入帳');
    expect(partial.el.querySelector('.delete-group')).toBeNull();

    const booked = render(makeEntryDetail({ id: 42, amount: '-390.0000', schedule: link({ kind: 'recurring', acted_by: 'import', posted_entry_ids: [42] }) }));
    click(booked.fixture, '.action-delete');
    expect(text(booked.el.querySelector('.schedule-delete-note'))).toBe('此期將標示為略過');

    const homehub = render(makeEntryDetail({ id: 42, amount: '-390.0000', schedule: link({ kind: 'recurring', posted_entry_ids: [42] }) }));
    click(homehub.fixture, '.action-delete');
    expect(text(homehub.el.querySelector('.schedule-delete-note'))).toBe('此期將回到待完成交易');
  });

  it('shows the toast when an import holds the period', () => {
    const { fixture, el } = render(makeEntryDetail({ id: 42, amount: '-390.0000', schedule: link({ kind: 'recurring', posted_entry_ids: [42] }) }));
    click(fixture, '.action-delete');
    click(fixture, '.delete-entry');
    http.expectOne(r => r.method === 'DELETE' && r.url === '/api/accounting/entries/42').flush(
      { code: 409, message: 'import_running', trace_id: 't' }, { status: 409, statusText: 'Conflict' },
    );
    fixture.detectChanges();
    expect(TestBed.inject(AccountingToastService).message()).toBe('匯入進行中，請稍後再試');
    expect(el.querySelector('.form-error')).toBeNull();
    expect(el.querySelector('.detail-name')).not.toBeNull();
  });

  function key(target: Element, init: KeyboardEventInit): KeyboardEvent {
    const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
    target.dispatchEvent(event);
    return event;
  }

  it('moves focus into the scope and repost panels, Esc closes them back to 編輯, ⏎ in a field reposts', async () => {
    const { fixture, el } = render(REPAYMENT);
    document.body.appendChild(el);
    const edit = el.querySelector('.action-edit') as HTMLButtonElement;
    edit.focus();
    click(fixture, '.action-edit');
    http.expectOne('/api/accounting/schedules/definitions/12').flush(DEFINITION);
    fixture.detectChanges();
    await fixture.whenStable();
    expect(document.activeElement).toBe(el.querySelector('.scope-one'));

    const esc = key(el.querySelector('.scope-one')!, { key: 'Escape' });
    fixture.detectChanges();
    await fixture.whenStable();
    expect(esc.defaultPrevented).toBe(true);
    expect(el.querySelector('.scope-choice')).toBeNull();
    expect(document.activeElement).toBe(edit);

    click(fixture, '.action-edit');
    http.expectOne('/api/accounting/schedules/definitions/12').flush(DEFINITION);
    fixture.detectChanges();
    click(fixture, '.scope-one');
    await fixture.whenStable();
    const first = el.querySelector('.repost-amount') as HTMLInputElement;
    expect(document.activeElement).toBe(first);
    key(first, { key: 'Enter', keyCode: 229 });
    http.expectNone('/api/accounting/schedules/instances/77/repost');
    const enter = key(first, { key: 'Enter' });
    expect(enter.defaultPrevented).toBe(true);
    http.expectOne('/api/accounting/schedules/instances/77/repost').flush(makeInstance({ id: 77, posted_entry_ids: [201, 202] }));
    expect(navigate).toHaveBeenCalledWith(['/accounting/entries', 201], { replaceUrl: true });
    el.remove();
  });

  it("drops an earlier definition read's answer", () => {
    const { fixture, el } = render(REPAYMENT);
    click(fixture, '.action-edit');
    const stale = http.expectOne('/api/accounting/schedules/definitions/12');
    key(el.querySelector('.scope-one')!, { key: 'Escape' });
    fixture.detectChanges();
    click(fixture, '.action-edit');
    const fresh = http.expectOne('/api/accounting/schedules/definitions/12');
    fresh.flush(DEFINITION);
    stale.flush({ ...DEFINITION, imported: true, locked: true });
    fixture.detectChanges();
    expect(el.querySelector('.scope-locked')).toBeNull();
    expect((el.querySelector('.scope-all') as HTMLButtonElement).disabled).toBe(false);
  });
});
