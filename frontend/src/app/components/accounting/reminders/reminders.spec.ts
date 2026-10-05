import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Counterparty, LedgerAccount, LedgerEntry, ScheduleDefinition, ScheduleInstance } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { AccountingToastService } from '../accounting-toast';
import { makeAccount, makeDefinition, makeEntry, makeInstance } from '../testing/fixtures';
import { AccountingRemindersComponent } from './reminders';

const credit = (overrides: Partial<LedgerAccount>) => makeAccount({ is_credit: true, icon: '💳', ...overrides });

// Today is 2026-10-03.
const SOON = credit({ id: 9, name: '玉山 UNI', closing_day: 15, due_rule: 'days_after_closing', due_value: 20 }); // 09/15 → 10/05
const TOMORROW = credit({ id: 11, name: '富邦 J卡', closing_day: 25, due_rule: 'fixed_day', due_value: 4 }); // 09/25 → 10/04
const LATE = credit({ id: 12, name: '台新 FlyGo', closing_day: 28, due_rule: 'days_after_closing', due_value: 3 }); // 09/28 → 10/01
const TODAY = credit({ id: 13, name: '國泰 CUBE', closing_day: 2, due_rule: 'days_after_closing', due_value: 1 }); // 10/02 → 10/03
const FAR = credit({ id: 14, name: '遠期卡', closing_day: 30, due_rule: 'days_after_closing', due_value: 60 }); // 09/30 → 11/29
const WALLET = makeAccount({ id: 1, name: '錢包' });

const CLOSINGS: Record<number, { end: string; due: string }> = {
  9: { end: '2026-09-15', due: '2026-10-05' },
  11: { end: '2026-09-25', due: '2026-10-04' },
  12: { end: '2026-09-28', due: '2026-10-01' },
  13: { end: '2026-10-02', due: '2026-10-03' },
};

const ALAN: Counterparty = { id: 1, name: 'Alan', open_amounts: [{ currency: 'TWD', amount: '220.0000' }] };
const BEA: Counterparty = { id: 2, name: 'Bea', open_amounts: [{ currency: 'TWD', amount: '-300.0000' }, { currency: 'JPY', amount: '5000.0000' }] };
const SETTLED: Counterparty = { id: 3, name: 'Cy', open_amounts: [] };

// `open_amount` is what is left on each original (74 had 250 collected, 71 had 200 repaid).
const OPEN_ENTRIES = [
  makeEntry({ id: 71, kind: 'payable', amount: '300.0000', open_amount: '100.0000', counterparty_id: 2, counterparty: 'Bea', entry_date: '2026-09-25' }),
  makeEntry({ id: 72, kind: 'receivable', amount: '-50.0000', open_amount: '50.0000', counterparty_id: 1, counterparty: 'Alan', entry_date: '2026-09-20' }),
  makeEntry({ id: 73, kind: 'receivable', amount: '-5000.0000', open_amount: '5000.0000', currency: 'JPY', counterparty_id: 2, counterparty: 'Bea', entry_date: '2026-09-10' }),
  makeEntry({ id: 74, kind: 'receivable', amount: '-420.0000', open_amount: '170.0000', counterparty_id: 1, counterparty: 'Alan', entry_date: '2026-09-01' }),
];

const ALAN_OPEN = OPEN_ENTRIES.filter(entry => entry.counterparty_id === 1);

describe('AccountingRemindersComponent', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 3, 12, 0, 0));
    TestBed.configureTestingModule({
      imports: [AccountingRemindersComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
    vi.useRealTimers();
  });

  function text(node: Element | null | undefined): string {
    return node?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  }

  function flushLoad(
    accounts: LedgerAccount[],
    counterparties: Counterparty[],
    open: LedgerEntry[],
    queue: ScheduleInstance[] = [],
    definitions: ScheduleDefinition[] = [],
  ): void {
    http.expectOne(r => r.url === '/api/accounting/accounts').flush(accounts);
    http.expectOne('/api/accounting/counterparties').flush(counterparties);
    const req = http.expectOne(r => r.url === '/api/accounting/entries' && !r.params.has('counterparty_id'));
    expect(req.request.params.get('open')).toBe('true');
    req.flush({ items: open, total: open.length, limit: 500, offset: 0 });
    const queueReq = http.expectOne(r => r.url === '/api/accounting/schedules/instances');
    expect(queueReq.request.params.get('queue')).toBe('true');
    queueReq.flush(queue);
    http.expectOne(r => r.url === '/api/accounting/schedules/definitions').flush(definitions);
  }

  function flushBill(accountId: number, spend: string, payments: string[] = [], paidTo = '2026-10-03'): void {
    const { end } = CLOSINGS[accountId];
    http
      .expectOne(r => r.url === `/api/accounting/accounts/${accountId}/summary` && r.params.get('date_to') === end)
      .flush({
        account_id: accountId, currency: 'TWD', date_from: '', date_to: end, spend, income: '0', rewards: '0', net: spend,
        end_balance: spend, count: 1,
      });
    const paymentsReq = http.expectOne(
      r => r.url === `/api/accounting/accounts/${accountId}/entries` && r.params.get('date_from') === end,
    );
    // Payments count up to today (before every card's next closing here), past the due date too.
    expect(paymentsReq.request.params.get('date_to')).toBe(paidTo);
    paymentsReq.flush({
      items: payments.map((amount, index) => makeEntry({ id: 90 + index, kind: 'transfer_in', amount })),
      total: payments.length,
      limit: 100,
      offset: 0,
    });
  }

  function render(
    accounts: LedgerAccount[] = [],
    counterparties: Counterparty[] = [],
    open: LedgerEntry[] = [],
    queue: ScheduleInstance[] = [],
    definitions: ScheduleDefinition[] = [],
  ) {
    const fixture = TestBed.createComponent(AccountingRemindersComponent);
    fixture.detectChanges();
    flushLoad(accounts, counterparties, open, queue, definitions);
    fixture.detectChanges();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  function tab(el: HTMLElement, label: string): HTMLButtonElement {
    return Array.from(el.querySelectorAll<HTMLButtonElement>('.tabs [role="radio"]')).find(button => text(button) === label)!;
  }

  it('lists cards with something left to pay, soonest due first, with the countdown wording', () => {
    const { fixture, el } = render([WALLET, SOON, TOMORROW, LATE, TODAY, FAR]);
    // FAR falls due 57 days away: never asked for.
    http.expectNone(r => r.url.startsWith('/api/accounting/accounts/14/'));
    flushBill(9, '-12345.0000', ['5000.0000']);
    flushBill(11, '-800.0000');
    flushBill(12, '-300.0000');
    flushBill(13, '-100.0000', ['100.0000']);
    fixture.detectChanges();

    const rows = Array.from(el.querySelectorAll('.card-row'));
    expect(rows.map(row => text(row.querySelector('.name')))).toEqual(['台新 FlyGo', '富邦 J卡', '玉山 UNI']);
    expect(rows.map(row => text(row.querySelector('.countdown')))).toEqual(['已逾期 2 天', '明天', '還有 2 天']);
    expect(rows[0].querySelector('.countdown')!.classList).toContain('overdue');
    expect(rows[1].querySelector('.countdown')!.classList).toContain('soon');
    expect(rows[2].querySelector('.countdown')!.classList).not.toContain('soon');
    expect(Array.from(rows[2].querySelectorAll('.figs .fig')).map(text)).toEqual(['應繳 $12,345', '已繳 $5,000', '剩餘 $7,345']);
    expect(text(rows[2].querySelector('.due-date'))).toBe('10/05 截止');
  });

  it('clears an overdue bill paid late, before the next closing', () => {
    vi.setSystemTime(new Date(2026, 9, 9, 12, 0, 0));
    const { fixture, el } = render([SOON]);
    // 10/05 was the due date; the payment window runs to today (10/09), the next closing being 10/15.
    flushBill(9, '-100.0000', ['100.0000'], '2026-10-09');
    fixture.detectChanges();
    expect(el.querySelector('.card-row')).toBeNull();
    expect(text(el.querySelector('.empty'))).toBe('目前沒有待處理項目');
  });

  it('keeps an unpaid bill overdue until the next closing', () => {
    vi.setSystemTime(new Date(2026, 9, 14, 12, 0, 0));
    const { fixture, el } = render([SOON]);
    flushBill(9, '-100.0000', [], '2026-10-14');
    fixture.detectChanges();
    expect(text(el.querySelector('.card-row .countdown'))).toBe('已逾期 9 天');
  });

  it('lists a card whose statement is the calendar month', () => {
    const monthly = credit({ id: 15, name: '月結卡', closing_day: null, due_rule: 'fixed_day', due_value: 5 });
    const { fixture, el } = render([monthly]);
    const summary = http.expectOne(r => r.url === '/api/accounting/accounts/15/summary');
    expect([summary.request.params.get('date_from'), summary.request.params.get('date_to')]).toEqual(['2026-09-01', '2026-09-30']);
    summary.flush({
      account_id: 15, currency: 'TWD', date_from: '', date_to: '', spend: '-900.0000', income: '0', rewards: '0', net: '0',
      end_balance: '0', count: 1,
    });
    const payments = http.expectOne(r => r.url === '/api/accounting/accounts/15/entries');
    expect([payments.request.params.get('date_from'), payments.request.params.get('date_to')]).toEqual(['2026-09-30', '2026-10-03']);
    payments.flush({ items: [], total: 0, limit: 100, offset: 0 });
    fixture.detectChanges();

    expect(text(el.querySelector('.card-row .name'))).toBe('月結卡');
    expect(text(el.querySelector('.card-row .countdown'))).toBe('還有 2 天');
  });

  it("lists a master card once, with its combined card's spend, and never the combined card", () => {
    const child = credit({ id: 16, name: '玉山 UNI 副卡', closing_day: 15, due_rule: 'days_after_closing', due_value: 20, combined_account_id: 9 });
    const { fixture, el } = render([SOON, child]);
    http
      .expectOne(r => r.url === '/api/accounting/accounts/16/summary')
      .flush({
        account_id: 16, currency: 'TWD', date_from: '', date_to: '', spend: '-345.0000', income: '0', rewards: '0',
        net: '0', end_balance: '0', count: 1,
      });
    http.expectNone(r => r.url === '/api/accounting/accounts/16/entries');
    flushBill(9, '-1000.0000');
    fixture.detectChanges();

    const rows = Array.from(el.querySelectorAll('.card-row'));
    expect(rows.map(row => text(row.querySelector('.name')))).toEqual(['玉山 UNI']);
    expect(text(rows[0].querySelector('.fig'))).toBe('應繳 $1,345');
  });

  it('shows 帳單讀取失敗 with 重試 instead of the empty state when a statement cannot be read', () => {
    const { fixture, el } = render([SOON]);
    http
      .expectOne(r => r.url === '/api/accounting/accounts/9/summary')
      .flush({
        account_id: 9, currency: 'TWD', date_from: '', date_to: '', spend: '-100.0000', income: '0', rewards: '0',
        net: '0', end_balance: '0', count: 1,
      });
    http.expectOne(r => r.url === '/api/accounting/accounts/9/entries').flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    expect(el.querySelector('.empty')).toBeNull();
    expect(el.querySelector('.card-row')).toBeNull();
    expect(text(el.querySelector('.bill-error .msg'))).toBe('帳單讀取失敗');
    tab(el, '信用卡帳單').click();
    fixture.detectChanges();
    expect(el.querySelector('.bill-error')).not.toBeNull();
    tab(el, '借還款追蹤').click();
    fixture.detectChanges();
    expect(el.querySelector('.bill-error')).toBeNull();
    expect(text(el.querySelector('.empty'))).toBe('目前沒有待處理項目');

    tab(el, '全部').click();
    fixture.detectChanges();
    el.querySelector<HTMLButtonElement>('.bill-error .retry')!.click();
    fixture.detectChanges();
    flushBill(9, '-100.0000');
    fixture.detectChanges();
    expect(el.querySelector('.bill-error')).toBeNull();
    expect(text(el.querySelector('.card-row .name'))).toBe('玉山 UNI');
  });

  it("shows the reason when a combined card's currency differs from the master's", () => {
    const child = credit({ id: 16, name: '美金副卡', currency: 'USD', closing_day: 15, due_rule: 'days_after_closing', due_value: 20, combined_account_id: 9 });
    const { fixture, el } = render([SOON, child]);
    http
      .expectOne(r => r.url === '/api/accounting/accounts/16/summary')
      .flush({
        account_id: 16, currency: 'USD', date_from: '', date_to: '', spend: '-30.0000', income: '0', rewards: '0',
        net: '0', end_balance: '0', count: 1,
      });
    flushBill(9, '-1000.0000');
    fixture.detectChanges();

    expect(el.querySelector('.card-row')).toBeNull();
    expect(el.querySelector('.empty')).toBeNull();
    expect(text(el.querySelector('.bill-error .msg'))).toBe('帳單讀取失敗（幣別不同）');
  });

  it('says 今天 on the due day', () => {
    const { fixture, el } = render([TODAY]);
    flushBill(13, '-100.0000');
    fixture.detectChanges();
    expect(text(el.querySelector('.card-row .countdown'))).toBe('今天');
  });

  it("opens the card's passbook on tap", () => {
    const { fixture, el } = render([SOON]);
    flushBill(9, '-100.0000');
    fixture.detectChanges();
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);

    el.querySelector<HTMLButtonElement>('.card-row')!.click();

    expect(navigate).toHaveBeenCalledWith(['/accounting/accounts', 9]);
  });

  it('lists counterparties with open rows, by name, with 應收 and 應付 totals and counts but no last date', () => {
    const { el } = render([], [ALAN, SETTLED, BEA], OPEN_ENTRIES);

    const rows = Array.from(el.querySelectorAll('.debt-row'));
    expect(rows.map(row => text(row.querySelector('.name')))).toEqual(['Alan', 'Bea']);
    expect(el.querySelector('.debt-row .last')).toBeNull();
    const beaSides = Array.from(rows[1].querySelectorAll('.side'));
    expect(beaSides.map(side => text(side.querySelector('.side-total')))).toEqual(['應收 +¥5,000', '應付 −$100']);
    expect(beaSides.map(side => text(side.querySelector('.side-count')))).toEqual(['1 筆應收款項', '1 筆應付款項']);
    expect(beaSides[0].querySelector('.side-total')!.classList).toContain('pos');
    expect(beaSides[1].querySelector('.side-total')!.classList).toContain('neg');
    // Remaining amounts, not the originals: 50 + (420 − 250).
    const alanSides = Array.from(rows[0].querySelectorAll('.side'));
    expect(alanSides.map(side => [text(side.querySelector('.side-total')), text(side.querySelector('.side-count'))])).toEqual([
      ['應收 +$220', '2 筆應收款項'],
    ]);
  });

  it('keeps both sides of a counterparty whose receivables and payables net to zero', () => {
    const dee: Counterparty = { id: 4, name: 'Dee', open_amounts: [] };
    const { el } = render([], [dee], [
      makeEntry({ id: 81, kind: 'receivable', amount: '-100.0000', open_amount: '100.0000', counterparty_id: 4, entry_date: '2026-09-02' }),
      makeEntry({ id: 82, kind: 'payable', amount: '100.0000', open_amount: '100.0000', counterparty_id: 4, entry_date: '2026-09-03' }),
    ]);
    expect(Array.from(el.querySelectorAll('.debt-row .side-total')).map(text)).toEqual(['應收 +$100', '應付 −$100']);
  });

  it("expands a counterparty's open entries as timeline rows that open the detail", () => {
    const { fixture, el } = render([], [ALAN], ALAN_OPEN);
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    const toggle = el.querySelector<HTMLButtonElement>('.debt-row')!;
    expect(toggle.getAttribute('aria-expanded')).toBe('false');

    toggle.click();
    fixture.detectChanges();
    const req = http.expectOne(r => r.url === '/api/accounting/entries' && r.params.get('counterparty_id') === '1');
    expect(req.request.params.get('open')).toBe('true');
    req.flush({ items: OPEN_ENTRIES.filter(entry => entry.counterparty_id === 1), total: 2, limit: 500, offset: 0 });
    fixture.detectChanges();

    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    const rows = Array.from(el.querySelectorAll<HTMLButtonElement>('.debt-entries .row'));
    expect(rows.map(row => row.dataset['entryId'])).toEqual(['72', '74']);
    // The signed remaining under the row's own date, with the original and what came back once something was collected.
    expect(text(rows[0].querySelector('.amt'))).toBe('+$50 2026/09/20');
    expect(rows[0].querySelector('.amt')!.classList).toContain('pos');
    expect(text(rows[0].querySelector('.amt .remaining-detail'))).toBe('2026/09/20');
    expect(text(rows[1].querySelector('.amt'))).toBe('+$170 2026/09/01 · 原始 $420 · 已收 $250');
    expect(text(rows[1].querySelector('.amt .remaining-detail'))).toBe('2026/09/01 · 原始 $420 · 已收 $250');
    rows[1].click();
    // ✕ on the detail comes back here.
    expect(navigate).toHaveBeenCalledWith(['/accounting/entries', 74], { state: { closeTo: 'reminders' } });

    toggle.click();
    fixture.detectChanges();
    expect(el.querySelector('.debt-entries')).toBeNull();
  });

  it('shows a payable as −remaining with what was repaid, and a closed original as 已結清', () => {
    const { fixture, el } = render([], [BEA], OPEN_ENTRIES.filter(entry => entry.counterparty_id === 2));
    el.querySelector<HTMLButtonElement>('.debt-row')!.click();
    fixture.detectChanges();
    const closed = makeEntry({
      id: 75, kind: 'payable', amount: '80.0000', open_amount: '0.0000', is_closed: true, counterparty_id: 2, entry_date: '2026-09-05',
    });
    http
      .expectOne(r => r.params.get('counterparty_id') === '2')
      .flush({ items: [OPEN_ENTRIES[0], closed], total: 2, limit: 500, offset: 0 });
    fixture.detectChanges();

    const rows = Array.from(el.querySelectorAll<HTMLElement>('.debt-entries .row'));
    expect(text(rows[0].querySelector('.amt'))).toBe('−$100 2026/09/25 · 原始 $300 · 已還 $200');
    expect(rows[0].querySelector('.amt')!.classList).toContain('neg');
    expect(text(rows[1].querySelector('.amt'))).toBe('已結清 2026/09/05');
    expect(rows[1].querySelector('.amt')!.classList).toContain('muted');
    expect(rows[1].querySelector('.amt')!.classList).not.toContain('neg');
    expect(rows[1].dataset['entryId']).toBe('75');
  });

  it('switches between 全部, 信用卡帳單 and 借還款追蹤, cards first in 全部', () => {
    const { fixture, el } = render([SOON], [ALAN], ALAN_OPEN);
    flushBill(9, '-100.0000');
    fixture.detectChanges();

    expect(tab(el, '全部').getAttribute('aria-checked')).toBe('true');
    const card = el.querySelector('.card-row')!;
    const debt = el.querySelector('.debt-row')!;
    expect(card.compareDocumentPosition(debt) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    tab(el, '信用卡帳單').click();
    fixture.detectChanges();
    expect(tab(el, '信用卡帳單').getAttribute('aria-checked')).toBe('true');
    expect(el.querySelectorAll('.card-row').length).toBe(1);
    expect(el.querySelector('.debt-row')).toBeNull();

    tab(el, '借還款追蹤').click();
    fixture.detectChanges();
    expect(el.querySelector('.card-row')).toBeNull();
    expect(el.querySelectorAll('.debt-row').length).toBe(1);
  });

  describe('借還款追蹤 filters', () => {
    function select(el: HTMLElement, label: string): HTMLSelectElement {
      return el.querySelector<HTMLSelectElement>(`.debt-filters select[aria-label="${label}"]`)!;
    }

    function choose(fixture: { detectChanges(): void }, control: HTMLSelectElement, value: string): void {
      control.value = value;
      control.dispatchEvent(new Event('change'));
      fixture.detectChanges();
    }

    function renderDebts() {
      const view = render([], [ALAN, SETTLED, BEA], OPEN_ENTRIES);
      tab(view.el, '借還款追蹤').click();
      view.fixture.detectChanges();
      return view;
    }

    const names = (el: HTMLElement) => Array.from(el.querySelectorAll('.debt-row .name')).map(text);

    it('shows only on 借還款追蹤, with every 對象 that has open rows and 全部 / 應收 / 應付', () => {
      const { fixture, el } = render([], [ALAN, SETTLED, BEA], OPEN_ENTRIES);
      expect(el.querySelector('.debt-filters')).toBeNull();
      tab(el, '借還款追蹤').click();
      fixture.detectChanges();

      const filters = el.querySelector('.debt-filters')!;
      expect(filters.classList).toContain('filters');
      expect(Array.from(filters.querySelectorAll('select')).map(control => control.getAttribute('aria-label'))).toEqual(['對象', '借／還']);
      expect(filters.querySelector('input')).toBeNull();
      expect(Array.from(select(el, '對象').options).map(text)).toEqual(['全部對象', 'Alan', 'Bea']);
      expect(Array.from(select(el, '借／還').options).map(text)).toEqual(['全部', '應收', '應付']);
    });

    it('借／還 = 應付 hides receivable-only 對象 and keeps only the payable side', () => {
      const { fixture, el } = renderDebts();
      choose(fixture, select(el, '借／還'), 'payable');

      expect(names(el)).toEqual(['Bea']);
      expect(Array.from(el.querySelectorAll('.debt-row .side-total')).map(text)).toEqual(['應付 −$100']);
      expect(Array.from(el.querySelectorAll('.debt-row .side-count')).map(text)).toEqual(['1 筆應付款項']);
      // The 對象 select still offers every 對象 with open rows.
      expect(Array.from(select(el, '對象').options).map(text)).toEqual(['全部對象', 'Alan', 'Bea']);

      choose(fixture, select(el, '借／還'), 'receivable');
      expect(names(el)).toEqual(['Alan', 'Bea']);
      expect(Array.from(el.querySelectorAll('.debt-row .side-total')).map(text)).toEqual(['應收 +$220', '應收 +¥5,000']);
    });

    it('對象 narrows the list to one, and a mismatch says so instead of the empty state', () => {
      const { fixture, el } = renderDebts();
      choose(fixture, select(el, '對象'), '2');
      expect(names(el)).toEqual(['Bea']);

      choose(fixture, select(el, '借／還'), 'receivable');
      expect(names(el)).toEqual(['Bea']);
      choose(fixture, select(el, '對象'), '1');
      choose(fixture, select(el, '借／還'), 'payable');
      expect(names(el)).toEqual([]);
      expect(el.querySelector('.empty')).toBeNull();
      expect(text(el.querySelector('.filtered-out'))).toBe('沒有符合篩選的項目');

      choose(fixture, select(el, '對象'), '');
      expect(names(el)).toEqual(['Bea']);
    });

    it('shows only matching rows under an expanded 對象', () => {
      const { fixture, el } = renderDebts();
      el.querySelectorAll<HTMLButtonElement>('.debt-row')[1].click();
      fixture.detectChanges();
      http
        .expectOne(r => r.params.get('counterparty_id') === '2')
        .flush({ items: OPEN_ENTRIES.filter(entry => entry.counterparty_id === 2), total: 2, limit: 500, offset: 0 });
      fixture.detectChanges();
      const ids = () => Array.from(el.querySelectorAll<HTMLElement>('.debt-entries .row')).map(row => row.dataset['entryId']);
      expect(ids()).toEqual(['71', '73']);

      choose(fixture, select(el, '借／還'), 'receivable');
      expect(ids()).toEqual(['73']);
    });

    it('filters client-side, keep their value across tabs, and leave the 全部 tab (and its count source) alone', () => {
      const { fixture, el } = renderDebts();
      choose(fixture, select(el, '借／還'), 'payable');
      // No new read: the bell's open rows are untouched.
      http.expectNone(r => r.url === '/api/accounting/entries');
      expect(fixture.componentInstance.openEntries().length).toBe(4);

      tab(el, '全部').click();
      fixture.detectChanges();
      expect(el.querySelector('.debt-filters')).toBeNull();
      expect(names(el)).toEqual(['Alan', 'Bea']);

      tab(el, '借還款追蹤').click();
      fixture.detectChanges();
      expect(select(el, '借／還').value).toBe('payable');
      expect(names(el)).toEqual(['Bea']);
    });
  });

  it('shows the empty state once nothing is pending, not while a statement is still being read', () => {
    const { fixture, el } = render([SOON], [SETTLED], []);
    expect(el.querySelector('.empty')).toBeNull();

    flushBill(9, '-100.0000', ['100.0000']);
    fixture.detectChanges();

    expect(text(el.querySelector('.empty'))).toBe('目前沒有待處理項目');
    tab(el, '借還款追蹤').click();
    fixture.detectChanges();
    expect(text(el.querySelector('.empty'))).toBe('目前沒有待處理項目');
  });

  it('reloads after an entry write and keeps an expanded counterparty open with fresh rows', () => {
    const { fixture, el } = render([SOON], [ALAN], ALAN_OPEN);
    flushBill(9, '-100.0000');
    el.querySelector<HTMLButtonElement>('.debt-row')!.click();
    fixture.detectChanges();
    http.expectOne(r => r.params.get('counterparty_id') === '1').flush({ items: [], total: 0, limit: 500, offset: 0 });

    TestBed.inject(AccountingService).deleteEntry(72).subscribe();
    http.expectOne(r => r.method === 'DELETE').flush(null);
    fixture.detectChanges();

    flushLoad([SOON], [ALAN], OPEN_ENTRIES.slice(3));
    http.expectOne(r => r.params.get('counterparty_id') === '1').flush({ items: OPEN_ENTRIES.slice(3), total: 1, limit: 500, offset: 0 });
    fixture.detectChanges();
    flushBill(9, '-100.0000', ['100.0000']);
    fixture.detectChanges();

    expect(el.querySelector('.card-row')).toBeNull();
    expect(text(el.querySelector('.debt-row .side-count'))).toBe('1 筆應收款項');
    expect(Array.from(el.querySelectorAll<HTMLElement>('.debt-entries .row')).map(row => row.dataset['entryId'])).toEqual(['74']);
  });

  it('reloads after an account write, re-reading the statement with the new due rule', () => {
    const { fixture, el } = render([SOON]);
    flushBill(9, '-100.0000');
    fixture.detectChanges();
    expect(text(el.querySelector('.card-row .countdown'))).toBe('還有 2 天');

    TestBed.inject(AccountingService).updateAccount(9, {} as never).subscribe();
    http.expectOne(r => r.method === 'PUT').flush({});
    fixture.detectChanges();
    // Due day moved from 10/05 (20 days after closing) to 10/04 (19 days).
    flushLoad([{ ...SOON, due_value: 19 }], [], []);
    fixture.detectChanges();
    // The write starts a new round at once (the old 10/05 statement is re-read) and the reloaded card adds 10/04's.
    const summaries = http.match(r => r.url === '/api/accounting/accounts/9/summary');
    const payments = http.match(r => r.url === '/api/accounting/accounts/9/entries');
    expect(payments.map(req => req.request.params.get('date_from'))).toEqual(['2026-09-15', '2026-09-15']);
    for (const summary of summaries) {
      summary.flush({
        account_id: 9, currency: 'TWD', date_from: '', date_to: '', spend: '-100.0000', income: '0', rewards: '0',
        net: '0', end_balance: '0', count: 1,
      });
    }
    payments.forEach(req => req.flush({ items: [], total: 0, limit: 100, offset: 0 }));
    fixture.detectChanges();

    expect(text(el.querySelector('.card-row .countdown'))).toBe('明天');
  });

  it('drops a superseded load', () => {
    const fixture = TestBed.createComponent(AccountingRemindersComponent);
    fixture.detectChanges();
    const first = {
      accounts: http.expectOne(r => r.url === '/api/accounting/accounts'),
      counterparties: http.expectOne('/api/accounting/counterparties'),
      open: http.expectOne(r => r.url === '/api/accounting/entries'),
      queue: http.expectOne(r => r.url === '/api/accounting/schedules/instances'),
      definitions: http.expectOne(r => r.url === '/api/accounting/schedules/definitions'),
    };
    TestBed.inject(AccountingService).deleteEntry(1).subscribe();
    http.expectOne(r => r.method === 'DELETE').flush(null);
    fixture.detectChanges();
    flushLoad([], [ALAN], ALAN_OPEN);
    first.accounts.flush([]);
    first.counterparties.flush([]);
    first.open.flush({ items: [], total: 0, limit: 500, offset: 0 });
    first.queue.flush([]);
    first.definitions.flush([]);
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelectorAll('.debt-row').length).toBe(1);
  });

  it('shows a load error', () => {
    const fixture = TestBed.createComponent(AccountingRemindersComponent);
    fixture.detectChanges();
    http.expectOne('/api/accounting/counterparties').flush([]);
    http.expectOne(r => r.url === '/api/accounting/entries').flush({ items: [], total: 0, limit: 500, offset: 0 });
    http.expectOne(r => r.url === '/api/accounting/schedules/instances').flush([]);
    http.expectOne(r => r.url === '/api/accounting/schedules/definitions').flush([]);
    http.expectOne(r => r.url === '/api/accounting/accounts').flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();
    expect(text((fixture.nativeElement as HTMLElement).querySelector('.load-error'))).toBe('提醒讀取失敗，請稍後再試。');
  });

  describe('待完成交易', () => {
    const RENT = makeInstance({
      id: 31, definition_id: 7, definition_name: '房租', seq: 4, times: 12, posting_mode: 'confirm',
      due_date: '2026-09-30', overdue_days: 3, amounts: ['18000'],
      lines: [{ kind: 'expense', account_id: 1, account_name: '薪轉', to_account_id: null, to_account_name: null,
        category: '居家/房租', counterparty: null, amount: '-18000.0000', currency: 'TWD' }],
      totals: [{ currency: 'TWD', amount: '-18000.0000' }],
    });

    function item(el: HTMLElement, id: number): HTMLElement {
      return el.querySelector(`.queue-item[data-instance-id="${id}"]`) as HTMLElement;
    }

    function button(scope: HTMLElement, label: string): HTMLButtonElement {
      return Array.from(scope.querySelectorAll<HTMLButtonElement>('button')).find(node => text(node) === label)!;
    }

    function sections(el: HTMLElement): string[] {
      return Array.from(el.querySelectorAll('h3.section')).map(text);
    }

    it('has the four tabs and opens 待完成交易 from ?tab=pending', async () => {
      await TestBed.inject(Router).navigateByUrl('/?tab=pending');
      const { el } = render([], [], [], [RENT]);
      expect(Array.from(el.querySelectorAll('.tabs [role="radio"]')).map(text)).toEqual(['全部', '信用卡帳單', '借還款追蹤', '待完成交易']);
      expect(text(el.querySelector('.tabs [aria-checked="true"]'))).toBe('待完成交易');
    });

    it('shows an overdue 提醒入帳 period under 已到期 in the warning tone', () => {
      // Spec "Overdue confirm item".
      const { fixture, el } = render([], [], [], [RENT]);
      tab(el, '待完成交易').click();
      fixture.detectChanges();
      expect(sections(el)).toEqual(['已到期']);
      const row = item(el, 31);
      expect(text(row.querySelector('.name'))).toBe('房租 #4/12');
      expect(text(row.querySelector('.due'))).toBe('已逾期 3 天');
      expect(row.querySelector('.due')!.classList).toContain('overdue');
      expect(text(row.querySelector('.total'))).toBe('−$18,000');
      expect(button(row, '入帳')).toBeTruthy();
      expect(button(row, '略過')).toBeTruthy();
    });

    it('asks once before skipping and keeps the remaining', () => {
      // Spec "Skip prompt keeps the remaining".
      const { fixture, el } = render([], [], [], [RENT]);
      tab(el, '待完成交易').click();
      fixture.detectChanges();
      button(item(el, 31), '略過').click();
      fixture.detectChanges();
      http.expectNone(r => r.url.endsWith('/skip'));
      expect(text(item(el, 31).querySelector('.skip-confirm'))).toBe('略過這一期？剩餘不變');
      (item(el, 31).querySelector('.skip-yes') as HTMLButtonElement).click();
      http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/instances/31/skip').flush(makeInstance({ id: 31, status: 'skipped' }));
      fixture.detectChanges();
      flushLoad([], [], [], []);
    });

    it('shows the error of a failing period with 重試 instead of 入帳, and 重試 posts it again', () => {
      // Spec "待完成交易 tab": the last_error text with 重試 when set.
      const failing = makeInstance({
        id: 32, definition_id: 10, due_date: '2026-10-01', overdue_days: 2, last_error: 'lines[0].account_id: 帳戶已封存',
      });
      const { fixture, el } = render([], [], [], [failing]);
      tab(el, '待完成交易').click();
      fixture.detectChanges();
      const row = item(el, 32);
      expect(text(row.querySelector('.queue-error'))).toBe('lines[0].account_id: 帳戶已封存');
      expect(button(row, '入帳')).toBeUndefined();
      button(row, '重試').click();
      http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/instances/32/post')
        .flush(makeInstance({ id: 32, status: 'posted' }));
      fixture.detectChanges();
      flushLoad([], [], [], []);
    });

    it('moves focus into the skip prompt, closes it on Esc from there and returns focus to 入帳', async () => {
      // The 略過 button is replaced by the prompt, so focus must move into it or Esc never reaches the component.
      const { fixture, el } = render([], [], [], [RENT]);
      tab(el, '待完成交易').click();
      fixture.detectChanges();
      button(item(el, 31), '略過').focus();
      button(item(el, 31), '略過').click();
      fixture.detectChanges();
      await fixture.whenStable();
      const prompt = item(el, 31).querySelector('.queue-actions')!;
      expect(item(el, 31).querySelector('.skip-confirm')).not.toBeNull();
      expect(document.activeElement).toBe(item(el, 31).querySelector('.skip-no'));
      expect(prompt.contains(document.activeElement)).toBe(true);

      const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
      document.activeElement!.dispatchEvent(escape);
      fixture.detectChanges();
      await fixture.whenStable();
      // Marked handled, so the layout's document-level Esc leaves the pane open.
      expect(escape.defaultPrevented).toBe(true);
      expect(item(el, 31)).not.toBeNull();
      expect(item(el, 31).querySelector('.skip-confirm')).toBeNull();
      expect(document.activeElement).toBe(button(item(el, 31), '入帳'));
    });

    it('keeps cards and counterparties when the queue load fails', () => {
      const fixture = TestBed.createComponent(AccountingRemindersComponent);
      fixture.detectChanges();
      http.expectOne(r => r.url === '/api/accounting/accounts').flush([SOON]);
      http.expectOne('/api/accounting/counterparties').flush([ALAN]);
      http
        .expectOne(r => r.url === '/api/accounting/entries' && !r.params.has('counterparty_id'))
        .flush({ items: ALAN_OPEN, total: ALAN_OPEN.length, limit: 500, offset: 0 });
      http
        .expectOne(r => r.url === '/api/accounting/schedules/instances' && r.params.get('queue') === 'true')
        .flush('boom', { status: 500, statusText: 'Server Error' });
      http.expectOne(r => r.url === '/api/accounting/schedules/definitions').flush([]);
      fixture.detectChanges();
      flushBill(9, '-12345.0000', ['5000.0000']);
      fixture.detectChanges();
      const el = fixture.nativeElement as HTMLElement;
      expect(text(el.querySelector('.queue-load-error'))).toBe('待完成交易讀取失敗，請稍後再試。');
      expect(el.querySelectorAll('.card-row').length).toBe(1);
      expect(el.querySelectorAll('.debt-row').length).toBe(1);
      tab(el, '待完成交易').click();
      fixture.detectChanges();
      expect(text(el.querySelector('.queue-load-error'))).toBe('待完成交易讀取失敗，請稍後再試。');
      expect(el.querySelector('.empty')).toBeNull();
    });

    it('posts from the queue and the item leaves the list', () => {
      // Spec "Post from the queue".
      const { fixture, el } = render([], [], [], [RENT]);
      tab(el, '待完成交易').click();
      fixture.detectChanges();
      button(item(el, 31), '入帳').click();
      http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/instances/31/post').flush(makeInstance({ id: 31, status: 'posted' }));
      fixture.detectChanges();
      flushLoad([], [], [], []);
      fixture.detectChanges();
      expect(item(el, 31)).toBeNull();
    });

    it('keeps a second item busy while the first action finishes', () => {
      const other = makeInstance({ id: 33, definition_id: 11, due_date: '2026-10-02', overdue_days: 1 });
      const { fixture, el } = render([], [], [], [RENT, other]);
      tab(el, '待完成交易').click();
      fixture.detectChanges();
      button(item(el, 31), '入帳').click();
      button(item(el, 33), '入帳').click();
      fixture.detectChanges();
      expect(button(item(el, 31), '入帳').disabled).toBe(true);
      expect(button(item(el, 33), '入帳').disabled).toBe(true);
      http.expectOne(r => r.url === '/api/accounting/schedules/instances/31/post').flush('boom', { status: 500, statusText: 'Server Error' });
      fixture.detectChanges();
      expect(button(item(el, 31), '入帳').disabled).toBe(false);
      expect(button(item(el, 33), '入帳').disabled).toBe(true);
      http.expectOne(r => r.url === '/api/accounting/schedules/instances/33/post').flush(makeInstance({ id: 33, status: 'posted' }));
      fixture.detectChanges();
      flushLoad([], [], [], [RENT]);
      fixture.detectChanges();
      // A reload clears the previous action's error line.
      expect(el.querySelector('.action-error')).toBeNull();
    });

    it('offers 補入帳至今天 for a backlog', () => {
      // Spec "Catch-up offered for a backlog".
      const first = makeInstance({ id: 41, definition_id: 8, due_date: '2026-09-22', overdue_days: 11 });
      const second = makeInstance({ id: 42, definition_id: 8, due_date: '2026-10-01', overdue_days: 2 });
      const { fixture, el } = render([], [], [], [first, second]);
      tab(el, '待完成交易').click();
      fixture.detectChanges();
      expect(button(item(el, 41), '補入帳至今天')).toBeTruthy();
      expect(button(item(el, 42), '補入帳至今天')).toBeUndefined();
      button(item(el, 41), '補入帳至今天').click();
      http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions/8/catch-up')
        .flush({ posted: [41, 42], failed: null, definition: {} });
      fixture.detectChanges();
      flushLoad([], [], [], []);
    });

    it('lists a partial period under 已到期 with 重新入帳 and 保留部分', () => {
      const partial = makeInstance({ id: 51, status: 'posted', is_partial: true, due_date: '2026-11-09', amounts: ['8333', '620'] });
      const { fixture, el } = render([], [], [], [partial]);
      tab(el, '待完成交易').click();
      fixture.detectChanges();
      const row = item(el, 51);
      expect(sections(el)).toEqual(['已到期']);
      expect(text(row.querySelector('.badge.partial'))).toBe('部分入帳');
      expect(button(row, '入帳')).toBeUndefined();
      button(row, '重新入帳').click();
      const repost = http.expectOne(r => r.url === '/api/accounting/schedules/instances/51/repost');
      expect(repost.request.body).toEqual({ amounts: ['8333', '620'] });
      repost.flush(makeInstance({ id: 51, status: 'posted' }));
      fixture.detectChanges();
      flushLoad([], [], [], [partial]);
      fixture.detectChanges();
      button(item(el, 51), '保留部分').click();
      http.expectOne(r => r.url === '/api/accounting/schedules/instances/51/accept-partial').flush(makeInstance({ id: 51, status: 'posted' }));
      fixture.detectChanges();
      flushLoad([], [], [], []);
    });

    it('shows the toast and keeps the item while an import runs', () => {
      // Spec "Posting during an import".
      const { fixture, el } = render([], [], [], [RENT]);
      tab(el, '待完成交易').click();
      fixture.detectChanges();
      button(item(el, 31), '入帳').click();
      http.expectOne(r => r.url.endsWith('/instances/31/post')).flush(
        { detail: 'import_running' }, { status: 409, statusText: 'Conflict' },
      );
      fixture.detectChanges();
      expect(TestBed.inject(AccountingToastService).message()).toBe('匯入進行中，請稍後再試');
      expect(item(el, 31)).not.toBeNull();
      expect(el.querySelector('.action-error')).toBeNull();
    });

    it('全部 shows the due periods as a 待完成交易 section after the counterparties', () => {
      const later = makeInstance({ id: 61, definition_id: 9, due_date: '2026-10-20' });
      const { el } = render([], [ALAN], ALAN_OPEN, [RENT, later]);
      expect(sections(el)).toEqual(['借還款追蹤', '待完成交易']);
      expect(item(el, 31)).not.toBeNull();
      expect(item(el, 61)).toBeNull();
    });
  });

  describe('週期／分期', () => {
    const LOAN = makeDefinition({
      id: 12, kind: 'installment', name: '信貸 每月還款', times: 36, posted_count: 3, next_due_date: '2027-02-09',
      remaining: '-275001.0000', repaid: '24999.0000',
    });

    function row(el: HTMLElement, id: number): HTMLElement {
      return el.querySelector(`.schedule-row[data-definition-id="${id}"]`) as HTMLElement;
    }

    it('words the loan row on 借還款追蹤 only', () => {
      // Spec "Loan row".
      const { fixture, el } = render([], [], [], [], [LOAN]);
      expect(row(el, 12)).toBeNull();
      tab(el, '借還款追蹤').click();
      fixture.detectChanges();
      const loan = row(el, 12);
      expect(text(loan.querySelector('.name'))).toBe('信貸 每月還款');
      expect(text(loan.querySelector('.next'))).toBe('下期 02/09');
      expect(text(loan.querySelector('.progress'))).toBe('已入帳 3 / 36');
      expect(text(loan.querySelector('.remaining'))).toBe('剩餘 −$275,001');
      expect(text(loan.querySelector('.badge.mode'))).toBe('自動');
    });

    it('marks an ended loan that is still open under 已結束', () => {
      // Spec "Ended loan still open needs a check".
      const ended = makeDefinition({ id: 13, name: '舊貸款', status: 'ended', needs_check: true, next_due_date: null });
      const { fixture, el } = render([], [], [], [], [LOAN, ended]);
      tab(el, '借還款追蹤').click();
      fixture.detectChanges();
      expect(row(el, 13)?.closest('details.ended-schedules')).not.toBeNull();
      expect(text(row(el, 13).querySelector('.badge.check'))).toBe('需檢查');
    });

    it('retries a failing schedule with catch-up', () => {
      const failing = makeDefinition({ id: 14, failing: { instance_id: 5, due_date: '2026-10-01', last_error: 'lines[0].account_id: 帳戶已封存' } });
      const { fixture, el } = render([], [], [], [], [failing]);
      tab(el, '借還款追蹤').click();
      fixture.detectChanges();
      expect(text(el.querySelector('.schedule-failing .msg'))).toBe('lines[0].account_id: 帳戶已封存');
      (el.querySelector('.schedule-failing .retry') as HTMLButtonElement).click();
      http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions/14/catch-up')
        .flush({ posted: [5], failed: null, definition: failing });
      fixture.detectChanges();
      flushLoad([], [], [], [], []);
    });

    it('sends one catch-up for a double click on 重試', () => {
      const failing = makeDefinition({ id: 14, failing: { instance_id: 5, due_date: '2026-10-01', last_error: 'lines[0].account_id: 帳戶已封存' } });
      const { fixture, el } = render([], [], [], [], [failing]);
      tab(el, '借還款追蹤').click();
      fixture.detectChanges();
      const retry = el.querySelector('.schedule-failing .retry') as HTMLButtonElement;
      retry.click();
      fixture.detectChanges();
      expect(retry.disabled).toBe(true);
      retry.click();
      const reqs = http.match(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions/14/catch-up');
      expect(reqs.length).toBe(1);
      reqs[0].flush({ posted: [5], failed: null, definition: failing });
      fixture.detectChanges();
      flushLoad([], [], [], [], []);
    });

    it('opens the manage sheet from a row and shows 已暫停 after a pause', () => {
      // Spec "Pause from the sheet".
      const { fixture, el } = render([], [], [], [], [LOAN]);
      tab(el, '借還款追蹤').click();
      fixture.detectChanges();
      row(el, 12).click();
      fixture.detectChanges();
      http.expectOne('/api/accounting/schedules/definitions/12').flush({ ...LOAN, instances: [] });
      fixture.detectChanges();
      (el.querySelector('app-schedule-sheet .sheet-pause') as HTMLButtonElement).click();
      http.expectOne('/api/accounting/schedules/definitions/12/pause').flush({ ...LOAN, status: 'paused' });
      fixture.detectChanges();
      http.expectOne('/api/accounting/schedules/definitions/12').flush({ ...LOAN, status: 'paused', instances: [] });
      flushLoad([], [], [], [], [{ ...LOAN, status: 'paused' }]);
      fixture.detectChanges();
      expect(text(row(el, 12).querySelector('.badge.paused'))).toBe('已暫停');
    });

    it('does not say 目前沒有待處理項目 on 借還款追蹤 while schedules are listed', () => {
      // No open counterparty, one live schedule: the 週期／分期 section is the page's content.
      const { fixture, el } = render([], [], [], [], [LOAN]);
      tab(el, '借還款追蹤').click();
      fixture.detectChanges();
      expect(row(el, 12)).not.toBeNull();
      expect(el.querySelector('.empty')).toBeNull();
    });

    it('opens the sheet from ?schedule= on 借還款追蹤', async () => {
      await TestBed.inject(Router).navigateByUrl('/?tab=debts&schedule=12');
      const { fixture, el } = render([], [], [], [], [LOAN]);
      http.expectOne('/api/accounting/schedules/definitions/12').flush({ ...LOAN, instances: [] });
      fixture.detectChanges();
      expect(text(el.querySelector('.tabs [aria-checked="true"]'))).toBe('借還款追蹤');
      expect(text(el.querySelector('app-schedule-sheet h3'))).toBe('信貸 每月還款');
    });
  });
});
