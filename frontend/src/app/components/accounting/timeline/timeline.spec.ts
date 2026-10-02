import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { Provider, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LedgerEntry, MonthSummary, Preference } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutMode, LayoutModeService } from '../../../services/layout-mode.service';
import { AccountingLayoutComponent } from '../accounting-layout/accounting-layout';
import { makeAccount, makeEntry, makePreference } from '../testing/fixtures';
import { LedgerTimelineComponent } from './timeline';

const ACCOUNTS = [makeAccount({ id: 1, name: '玉山 UNI' }), makeAccount({ id: 7, name: '富邦 J卡' })];

describe('LedgerTimelineComponent', () => {
  let httpMock: HttpTestingController | null = null;

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 2, 12, 0, 0));
  });

  afterEach(() => {
    httpMock?.verify();
    httpMock = null;
    vi.useRealTimers();
  });

  function render(mode: LayoutMode = 'phone', preference: Preference = makePreference(), providers: Provider[] = []) {
    TestBed.configureTestingModule({
      imports: [LedgerTimelineComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([]), ...providers],
    });
    httpMock = TestBed.inject(HttpTestingController);
    TestBed.inject(LayoutModeService).set(mode);
    const fixture = TestBed.createComponent(LedgerTimelineComponent);
    fixture.detectChanges();
    httpMock.expectOne('/api/accounting/preference').flush(preference);
    httpMock.expectOne('/api/accounting/accounts').flush(ACCOUNTS);
    fixture.detectChanges();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  function flushSummary(month: string, body?: Partial<MonthSummary>): void {
    const req = httpMock!.expectOne(r => r.url === '/api/accounting/entries/summary');
    expect(req.request.params.get('month')).toBe(month);
    req.flush({ month, currency: 'TWD', expense: '0', income: '0', net: '0', missing_rates: [], ...body });
  }

  function flushEntries(items: LedgerEntry[], total = items.length): TestRequest {
    const req = httpMock!.expectOne(r => r.url === '/api/accounting/entries');
    req.flush({ items, total, limit: 50, offset: Number(req.request.params.get('offset')) });
    return req;
  }

  function text(node: Element | null | undefined): string {
    return node?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  }

  it('shows the converted amount with the original ¥ amount and rate beneath it at 390px', () => {
    const { fixture, el } = render('phone');
    flushSummary('2026-10');
    flushEntries([
      makeEntry({
        id: 31,
        name: 'Uniqlo 外套',
        merchant: 'Uniqlo 心齋橋',
        amount: '-1166.0000',
        original_amount: '-5390.0000',
        original_currency: 'JPY',
        fx_rate: '0.2163000000',
        fx_source: 'manual',
        project: '2026 大阪',
        account_id: 7,
        account_name: '富邦 J卡',
        category: '購物/衣物',
        category_icon: '🛍️',
        category_color: '#cc7676',
      }),
    ]);
    fixture.detectChanges();

    const row = el.querySelector('.row')!;
    const amount = row.querySelector('.amt')!;
    expect(text(amount)).toContain('−$1,166');
    expect(text(amount.querySelector('small.fx'))).toBe('¥5,390 @ 0.2163');
    expect(amount.hasAttribute('title')).toBe(false);
    expect(amount.classList).toContain('neg');
    expect(text(row.querySelector('.name'))).toBe('Uniqlo 外套');
    expect(text(row.querySelector('.sub'))).toBe('Uniqlo 心齋橋');
    expect(text(row.querySelector('.ico'))).toBe('🛍️');
    expect(Array.from(row.querySelectorAll('.pill')).map(text)).toEqual(['2026 大阪', '富邦 J卡']);
  });

  it('moves to September and shows its totals from the summary endpoint', () => {
    const { fixture, el } = render('phone');
    flushSummary('2026-10');
    flushEntries([]);
    fixture.detectChanges();

    (el.querySelector('.month-prev') as HTMLButtonElement).click();
    fixture.detectChanges();
    flushSummary('2026-09', { expense: '-18420', income: '62000', net: '43580' });
    const req = flushEntries([makeEntry({ id: 5, entry_date: '2026-09-30', name: '電費', amount: '-2569.0000' })]);
    fixture.detectChanges();

    expect(req.request.params.get('date_from')).toBe('2026-09-01');
    expect(req.request.params.get('date_to')).toBe('2026-09-30');
    expect(text(el.querySelector('.month-label'))).toBe('2026 年 9 月');
    expect(text(el.querySelector('.sum-expense'))).toBe('−$18,420');
    expect(text(el.querySelector('.sum-income'))).toBe('+$62,000');
    expect(text(el.querySelector('.sum-net'))).toBe('+$43,580');
    expect(text(el.querySelector('.day span'))).toBe('09/30 週三');
  });

  it('shows a split group as one row with its count badge and total', () => {
    const { fixture, el } = render();
    flushSummary('2026-10');
    const group = { id: 4, kind: 'split' as const, name: '聚餐', count: 2, total: '-410.0000', currency: 'TWD' };
    flushEntries([
      makeEntry({ id: 40, name: '午餐', amount: '-230.0000', group }),
      makeEntry({ id: 41, kind: 'receivable', name: '代付', amount: '-180.0000', counterparty: 'Alan', group }),
      makeEntry({ id: 42, name: '捷運', amount: '-20.0000' }),
    ]);
    fixture.detectChanges();

    const rows = Array.from(el.querySelectorAll('.row'));
    expect(rows.length).toBe(2);
    expect(text(rows[0].querySelector('.name'))).toBe('聚餐2');
    expect(text(rows[0].querySelector('.badge'))).toBe('2');
    expect(text(rows[0].querySelector('.amt'))).toBe('−$410');
  });

  it('nets each day in the main currency and shows a transfer pair as one neutral row', () => {
    const { fixture, el } = render();
    flushSummary('2026-10');
    flushEntries([
      makeEntry({ id: 1, amount: '-170.0000' }),
      makeEntry({ id: 2, amount: '-1166.0000' }),
      makeEntry({ id: 3, kind: 'transfer_out', amount: '-10000.0000', account_name: '國泰主帳戶', transfer_group_id: 'g-1', category: null, category_icon: null, category_color: null }),
      makeEntry({ id: 4, kind: 'transfer_in', amount: '10000.0000', account_name: 'Line Bank', transfer_group_id: 'g-1', category: null, category_icon: null, category_color: null }),
      makeEntry({ id: 5, kind: 'expense', amount: '-500.0000', currency: 'JPY', account_name: '日幣現金' }),
    ]);
    fixture.detectChanges();

    expect(text(el.querySelector('.day b'))).toBe('−$1,336');
    const transfer = Array.from(el.querySelectorAll('.row')).find(row => text(row.querySelector('.sub')).includes('→'))!;
    expect(text(transfer.querySelector('.sub'))).toBe('國泰主帳戶 → Line Bank');
    expect(text(transfer.querySelector('.amt'))).toBe('$10,000');
    expect(transfer.querySelector('.amt')!.classList.contains('neg')).toBe(false);
    expect(Array.from(transfer.querySelectorAll('.pill')).map(text)).toEqual(['轉帳']);
    expect(el.querySelectorAll('.row').length).toBe(4);
  });

  it('asks the server to hide rewards and drops any that still arrive', () => {
    const { fixture, el } = render('phone', makePreference({ hide_rewards_on_timeline: true }));
    flushSummary('2026-10');
    const req = flushEntries([
      makeEntry({ id: 1, name: '午餐' }),
      makeEntry({ id: 2, kind: 'reward', name: '紅利回饋 · 網購 3%', amount: '124.0000' }),
    ]);
    fixture.detectChanges();

    expect(req.request.params.get('hide_rewards')).toBe('true');
    expect(Array.from(el.querySelectorAll('.row .name')).map(text)).toEqual(['午餐']);
  });

  it('opens the entry detail on tap and highlights the selected entry', () => {
    const layout = { selectedEntryId: signal<number | null>(2) };
    const { fixture, el } = render('panes', makePreference(), [{ provide: AccountingLayoutComponent, useValue: layout }]);
    flushSummary('2026-10');
    flushEntries([makeEntry({ id: 1, name: '午餐' }), makeEntry({ id: 2, name: '捷運' })]);
    fixture.detectChanges();
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);

    const rows = Array.from(el.querySelectorAll<HTMLButtonElement>('.row'));
    expect(rows.map(row => row.classList.contains('sel'))).toEqual([false, true]);
    expect(rows.map(row => row.getAttribute('aria-current'))).toEqual([null, 'true']);
    rows[0].click();

    expect(navigate).toHaveBeenCalledWith(['/accounting/entries', 1]);
  });

  it('loads the next page from the 載入更多 fallback', () => {
    const { fixture, el } = render();
    flushSummary('2026-10');
    flushEntries(Array.from({ length: 50 }, (_, i) => makeEntry({ id: i + 1 })), 60);
    fixture.detectChanges();

    (el.querySelector('button.more') as HTMLButtonElement).click();
    const req = flushEntries(Array.from({ length: 10 }, (_, i) => makeEntry({ id: 51 + i })), 60);
    fixture.detectChanges();

    expect(req.request.params.get('offset')).toBe('50');
    expect(el.querySelector('button.more')).toBeNull();
  });

  it('keeps the filters behind ⌕ on a phone and filters by account', () => {
    const { fixture, el } = render('phone');
    flushSummary('2026-10');
    flushEntries([]);
    fixture.detectChanges();
    expect(el.querySelector('.filters')).toBeNull();

    (el.querySelector('.search-toggle') as HTMLButtonElement).click();
    fixture.detectChanges();
    const select = el.querySelector('.filter-account') as HTMLSelectElement;
    select.value = '7';
    select.dispatchEvent(new Event('change'));
    fixture.detectChanges();

    const req = flushEntries([]);
    expect(req.request.params.getAll('account_id')).toEqual(['7']);
  });
  it('notes the currencies left out of the month totals', () => {
    const { fixture, el } = render();
    flushSummary('2026-10', { missing_rates: ['JPY', 'USD'] });
    flushEntries([]);
    fixture.detectChanges();
    expect(text(el.querySelector('.missing-rates'))).toBe('部分外幣未換算 (JPY, USD)');

    (el.querySelector('.month-prev') as HTMLButtonElement).click();
    fixture.detectChanges();
    flushSummary('2026-09');
    flushEntries([]);
    fixture.detectChanges();
    expect(el.querySelector('.missing-rates')).toBeNull();
  });

  it('re-reads the preference after it is saved and reloads with the new reward setting', () => {
    const { fixture } = render();
    flushSummary('2026-10');
    expect(flushEntries([]).request.params.get('hide_rewards')).toBe('false');

    TestBed.inject(AccountingService).updatePreference(makePreference({ hide_rewards_on_timeline: true })).subscribe();
    httpMock!.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/preference').flush(makePreference({ hide_rewards_on_timeline: true }));
    fixture.detectChanges();

    flushSummary('2026-10');
    expect(flushEntries([]).request.params.get('hide_rewards')).toBe('true');
  });

  it('keeps the current rows on screen while a refresh is in flight', () => {
    const { fixture, el } = render();
    flushSummary('2026-10');
    flushEntries([makeEntry({ id: 1, name: '午餐' })]);
    fixture.detectChanges();

    (el.querySelector('.month-next') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(Array.from(el.querySelectorAll('.row .name')).map(text)).toEqual(['午餐']);

    flushSummary('2026-11');
    flushEntries([makeEntry({ id: 2, entry_date: '2026-11-01', name: '捷運' })]);
    fixture.detectChanges();
    expect(Array.from(el.querySelectorAll('.row .name')).map(text)).toEqual(['捷運']);
  });
});
