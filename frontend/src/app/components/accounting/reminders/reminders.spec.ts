import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Counterparty, LedgerAccount, LedgerEntry } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { makeAccount, makeEntry } from '../testing/fixtures';
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

const OPEN_ENTRIES = [
  makeEntry({ id: 71, kind: 'payable', amount: '300.0000', counterparty_id: 2, counterparty: 'Bea', entry_date: '2026-09-25' }),
  makeEntry({ id: 72, kind: 'receivable', amount: '-50.0000', counterparty_id: 1, counterparty: 'Alan', entry_date: '2026-09-20' }),
  makeEntry({ id: 73, kind: 'receivable', amount: '-5000.0000', currency: 'JPY', counterparty_id: 2, counterparty: 'Bea', entry_date: '2026-09-10' }),
  makeEntry({ id: 74, kind: 'receivable', amount: '-420.0000', counterparty_id: 1, counterparty: 'Alan', entry_date: '2026-09-01' }),
];

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

  function flushLoad(accounts: LedgerAccount[], counterparties: Counterparty[], open: LedgerEntry[]): void {
    http.expectOne(r => r.url === '/api/accounting/accounts').flush(accounts);
    http.expectOne('/api/accounting/counterparties').flush(counterparties);
    const req = http.expectOne(r => r.url === '/api/accounting/entries' && !r.params.has('counterparty_id'));
    expect(req.request.params.get('open')).toBe('true');
    req.flush({ items: open, total: open.length, limit: 500, offset: 0 });
  }

  function flushBill(accountId: number, spend: string, payments: string[] = []): void {
    const { end, due } = CLOSINGS[accountId];
    http
      .expectOne(r => r.url === `/api/accounting/accounts/${accountId}/summary` && r.params.get('date_to') === end)
      .flush({
        account_id: accountId, currency: 'TWD', date_from: '', date_to: end, spend, income: '0', rewards: '0', net: spend,
        end_balance: spend, count: 1,
      });
    const paymentsReq = http.expectOne(
      r => r.url === `/api/accounting/accounts/${accountId}/entries` && r.params.get('date_from') === end,
    );
    expect(paymentsReq.request.params.get('date_to')).toBe(due);
    paymentsReq.flush({
      items: payments.map((amount, index) => makeEntry({ id: 90 + index, kind: 'transfer_in', amount })),
      total: payments.length,
      limit: 100,
      offset: 0,
    });
  }

  function render(accounts: LedgerAccount[] = [], counterparties: Counterparty[] = [], open: LedgerEntry[] = []) {
    const fixture = TestBed.createComponent(AccountingRemindersComponent);
    fixture.detectChanges();
    flushLoad(accounts, counterparties, open);
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

  it('lists counterparties with an open amount, most recent first, with totals, counts and last date', () => {
    const { el } = render([], [ALAN, SETTLED, BEA], OPEN_ENTRIES);

    const rows = Array.from(el.querySelectorAll('.debt-row'));
    expect(rows.map(row => text(row.querySelector('.name')))).toEqual(['Bea', 'Alan']);
    const beaTotals = Array.from(rows[0].querySelectorAll('.total'));
    expect(beaTotals.map(text)).toEqual(['−$300', '+¥5,000']);
    expect(beaTotals[0].classList).toContain('neg');
    expect(beaTotals[1].classList).toContain('pos');
    expect(text(rows[0].querySelector('.count'))).toBe('1 筆應收款項 · 1 筆應付款項');
    expect(text(rows[0].querySelector('.last'))).toBe('2026/09/25');
    expect(text(rows[1].querySelector('.total'))).toBe('+$220');
    expect(text(rows[1].querySelector('.count'))).toBe('2 筆應收款項');
    expect(text(rows[1].querySelector('.last'))).toBe('2026/09/20');
  });

  it("expands a counterparty's open entries as timeline rows that open the detail", () => {
    const { fixture, el } = render([], [ALAN], OPEN_ENTRIES);
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
    expect(text(rows[0].querySelector('.amt'))).toBe('−$50');
    rows[1].click();
    expect(navigate).toHaveBeenCalledWith(['/accounting/entries', 74]);

    toggle.click();
    fixture.detectChanges();
    expect(el.querySelector('.debt-entries')).toBeNull();
  });

  it('switches between 全部, 信用卡帳單 and 借還款追蹤, cards first in 全部', () => {
    const { fixture, el } = render([SOON], [ALAN], OPEN_ENTRIES);
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
    const { fixture, el } = render([SOON], [ALAN], OPEN_ENTRIES);
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
    expect(text(el.querySelector('.debt-row .count'))).toBe('1 筆應收款項');
    expect(Array.from(el.querySelectorAll<HTMLElement>('.debt-entries .row')).map(row => row.dataset['entryId'])).toEqual(['74']);
  });

  it('drops a superseded load', () => {
    const fixture = TestBed.createComponent(AccountingRemindersComponent);
    fixture.detectChanges();
    const first = {
      accounts: http.expectOne(r => r.url === '/api/accounting/accounts'),
      counterparties: http.expectOne('/api/accounting/counterparties'),
      open: http.expectOne(r => r.url === '/api/accounting/entries'),
    };
    TestBed.inject(AccountingService).deleteEntry(1).subscribe();
    http.expectOne(r => r.method === 'DELETE').flush(null);
    fixture.detectChanges();
    flushLoad([], [ALAN], OPEN_ENTRIES);
    first.accounts.flush([]);
    first.counterparties.flush([]);
    first.open.flush({ items: [], total: 0, limit: 500, offset: 0 });
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelectorAll('.debt-row').length).toBe(1);
  });

  it('shows a load error', () => {
    const fixture = TestBed.createComponent(AccountingRemindersComponent);
    fixture.detectChanges();
    http.expectOne('/api/accounting/counterparties').flush([]);
    http.expectOne(r => r.url === '/api/accounting/entries').flush({ items: [], total: 0, limit: 500, offset: 0 });
    http.expectOne(r => r.url === '/api/accounting/accounts').flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();
    expect(text((fixture.nativeElement as HTMLElement).querySelector('.load-error'))).toBe('提醒讀取失敗，請稍後再試。');
  });
});
