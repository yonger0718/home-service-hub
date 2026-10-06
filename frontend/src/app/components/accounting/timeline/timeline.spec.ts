import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { Provider, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Counterparty, DailySummary, LedgerAccount, LedgerEntry, MonthSummary, Preference, ScheduleInstance } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutMode, LayoutModeService } from '../../../services/layout-mode.service';
import { AccountingLayoutComponent } from '../accounting-layout/accounting-layout';
import { makeAccount, makeEntry, makeInstance, makePreference } from '../testing/fixtures';
import { LedgerTimelineComponent, buildDays, schedulePills } from './timeline';

const VIEW_KEY = 'hh.accounting.timelineView';

const ACCOUNTS = [makeAccount({ id: 1, name: '玉山 UNI' }), makeAccount({ id: 7, name: '富邦 J卡' })];

describe('LedgerTimelineComponent', () => {
  let httpMock: HttpTestingController | null = null;

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 2, 12, 0, 0));
  });

  afterEach(() => {
    localStorage.removeItem(VIEW_KEY);
    httpMock?.verify();
    httpMock = null;
    vi.useRealTimers();
  });

  function render(
    mode: LayoutMode = 'phone',
    preference: Preference = makePreference(),
    providers: Provider[] = [],
    accounts: LedgerAccount[] = ACCOUNTS,
    counterparties: Counterparty[] = [],
    queue: ScheduleInstance[] = [],
  ) {
    TestBed.configureTestingModule({
      imports: [LedgerTimelineComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([]), ...providers],
    });
    httpMock = TestBed.inject(HttpTestingController);
    TestBed.inject(LayoutModeService).set(mode);
    const fixture = TestBed.createComponent(LedgerTimelineComponent);
    fixture.detectChanges();
    httpMock.expectOne('/api/accounting/preference').flush(preference);
    httpMock.expectOne('/api/accounting/accounts').flush(accounts);
    flushCounterparties(counterparties, queue);
    fixture.detectChanges();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  /** The 🔔's counterparties and 待完成交易 queue: read on init and again after every entry or account write. */
  function flushCounterparties(body: Counterparty[] = [], queue: ScheduleInstance[] = []): void {
    httpMock!.expectOne('/api/accounting/counterparties').flush(body);
    const req = httpMock!.expectOne(r => r.url === '/api/accounting/schedules/instances');
    expect(req.request.params.get('queue')).toBe('true');
    req.flush(queue);
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

  function flushDaily(month: string, body?: Partial<DailySummary>): TestRequest {
    const req = httpMock!.expectOne(r => r.url === '/api/accounting/entries/summary/daily');
    expect(req.request.params.get('month')).toBe(month);
    req.flush({ month, currency: 'TWD', days: [], missing_rates: [], ...body });
    return req;
  }

  function expectNoEntryPage(): void {
    httpMock!.expectNone(r => r.url === '/api/accounting/entries');
  }

  function viewButton(el: HTMLElement, label: string): HTMLButtonElement {
    return Array.from(el.querySelectorAll<HTMLButtonElement>('.view-toggle button')).find(button => text(button) === label)!;
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
    const group = { id: 4, kind: 'split' as const, name: '聚餐', merchant: null, description: null, count: 2, total: '-410.0000', currency: 'TWD' };
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
    localStorage.setItem(VIEW_KEY, 'list');
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

  it('replaces the rows with the skeleton while another month loads', () => {
    const { fixture, el } = render();
    flushSummary('2026-10');
    flushEntries([makeEntry({ id: 1, name: '午餐' })]);
    fixture.detectChanges();

    (el.querySelector('.month-next') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(el.querySelectorAll('.row').length).toBe(0);
    expect(el.querySelector('app-skeleton')).not.toBeNull();
    expect(el.querySelector('.empty')).toBeNull();

    flushSummary('2026-11');
    flushEntries([makeEntry({ id: 2, entry_date: '2026-11-01', name: '捷運' })]);
    fixture.detectChanges();
    expect(Array.from(el.querySelectorAll('.row .name')).map(text)).toEqual(['捷運']);
    expect(el.querySelector('app-skeleton')).toBeNull();
  });

  it('keeps the current rows on screen while the same month refreshes after an entry write', () => {
    const { fixture, el } = render();
    flushSummary('2026-10');
    flushEntries([makeEntry({ id: 1, name: '午餐' })]);
    fixture.detectChanges();

    TestBed.inject(AccountingService).deleteEntry(99).subscribe();
    httpMock!.expectOne(r => r.method === 'DELETE' && r.url === '/api/accounting/entries/99').flush(null);
    fixture.detectChanges();
    flushCounterparties();
    expect(Array.from(el.querySelectorAll('.row .name')).map(text)).toEqual(['午餐']);
    expect(el.querySelector('app-skeleton')).toBeNull();

    flushSummary('2026-10');
    flushEntries([makeEntry({ id: 3, name: '晚餐' })]);
    fixture.detectChanges();
    expect(Array.from(el.querySelectorAll('.row .name')).map(text)).toEqual(['晚餐']);
  });
  it('shows 記錄讀取失敗 with 重試 instead of rows or empty text, and 重試 reloads page 1', () => {
    const { fixture, el } = render();
    flushSummary('2026-10');
    flushEntries([makeEntry({ id: 1, name: '午餐' })]);
    fixture.detectChanges();

    (el.querySelector('.month-next') as HTMLButtonElement).click();
    fixture.detectChanges();
    flushSummary('2026-11');
    httpMock!.expectOne(r => r.url === '/api/accounting/entries').flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    expect(el.querySelectorAll('.row').length).toBe(0);
    expect(text(el.querySelector('.load-error'))).toBe('記錄讀取失敗');
    expect(el.querySelector('.empty')).toBeNull();
    expect(el.querySelector('app-skeleton')).toBeNull();

    (el.querySelector('.list-retry') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(el.querySelector('app-skeleton')).not.toBeNull();
    const req = flushEntries([makeEntry({ id: 2, entry_date: '2026-11-01', name: '捷運' })]);
    fixture.detectChanges();
    expect(req.request.params.get('offset')).toBe('0');
    expect(el.querySelector('.load-error')).toBeNull();
    expect(Array.from(el.querySelectorAll('.row .name')).map(text)).toEqual(['捷運']);
  });
  it('shows the skeleton, not the empty text, while the first page loads', () => {
    const { fixture, el } = render();
    const skeleton = el.querySelector('app-skeleton');
    expect(skeleton).not.toBeNull();
    expect(skeleton!.getAttribute('aria-busy')).toBe('true');
    expect(skeleton!.getAttribute('aria-label')).toBe('載入中');
    expect(el.querySelector('.empty')).toBeNull();

    flushSummary('2026-10');
    flushEntries([]);
    fixture.detectChanges();
    expect(el.querySelector('app-skeleton')).toBeNull();
    expect(text(el.querySelector('.empty'))).toBe('這個月沒有記錄');
  });

  it('says 沒有符合條件的記錄 with 清除篩選 when the filters match nothing', () => {
    const { fixture, el } = render('sheet');
    flushSummary('2026-10');
    flushEntries([makeEntry({ id: 1 })]);
    fixture.detectChanges();
    fixture.componentInstance.setKind('income');
    fixture.detectChanges();
    flushEntries([], 0);
    fixture.detectChanges();

    expect(text(el.querySelector('.empty'))).toBe('沒有符合條件的記錄');
    expect(el.querySelector('.empty-filtered .clear-filters')).not.toBeNull();
  });

  it('ignores a late page for the previous filters and shows the empty text only for the current ones', () => {
    const { fixture, el } = render('sheet');
    flushSummary('2026-10');
    flushEntries([makeEntry({ id: 1 })]);
    fixture.detectChanges();

    fixture.componentInstance.setKind('income');
    fixture.detectChanges();
    const stale = httpMock!.expectOne(r => r.url === '/api/accounting/entries');
    fixture.componentInstance.setKind('expense');
    fixture.detectChanges();
    const current = httpMock!.expectOne(r => r.url === '/api/accounting/entries');
    expect(current.request.params.get('kind')).toBe('expense');

    stale.flush({ items: [], total: 0, limit: 50, offset: 0 });
    fixture.detectChanges();
    expect(el.querySelector('.empty')).toBeNull();
    expect(el.querySelector('app-skeleton')).not.toBeNull();
    expect(text(el.querySelector('.match-count'))).toBe('符合條件 — 筆');

    current.flush({ items: [], total: 0, limit: 50, offset: 0 });
    fixture.detectChanges();
    expect(text(el.querySelector('.empty'))).toBe('沒有符合條件的記錄');
    expect(text(el.querySelector('.match-count'))).toBe('符合條件 0 筆');
  });
  it('shows an unknown match count when pagination fails', () => {
    const { fixture, el } = render('sheet');
    flushSummary('2026-10'); flushEntries([]); fixture.detectChanges();
    fixture.componentInstance.setKind('expense'); fixture.detectChanges();
    flushEntries([makeEntry({ id: 1 })], 100); fixture.detectChanges();
    fixture.componentInstance.loadMore();
    httpMock!.expectOne(r => r.url === '/api/accounting/entries').flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();
    expect(text(el.querySelector('.match-count'))).toBe('符合條件 — 筆');
    expect(el.querySelector('.row')).toBeNull();
  });

  describe('filters and month totals', () => {
    it('labels the totals 全月 · 未套篩選 and shows the filtered match count while a filter is active', () => {
      const { fixture, el } = render('sheet');
      flushSummary('2026-10', { expense: '-5000', net: '-5000' });
      flushEntries([makeEntry({ id: 1 })]);
      fixture.detectChanges();
      expect(el.querySelector('.sum-scope')).toBeNull();
      expect(el.querySelector('.match-row')).toBeNull();

      fixture.componentInstance.setKind('expense');
      fixture.detectChanges();
      expect(text(el.querySelector('.sum-scope'))).toBe('全月 · 未套篩選');
      // In flight: never the previous filters' count.
      expect(text(el.querySelector('.match-count'))).toBe('符合條件 — 筆');

      flushEntries([makeEntry({ id: 1 }), makeEntry({ id: 2 })], 12);
      fixture.detectChanges();
      expect(text(el.querySelector('.match-count'))).toBe('符合條件 12 筆');
      expect(text(el.querySelector('.sum-expense'))).toBe('−$5,000');
      expect(el.querySelector('.clear-filters')!.getAttribute('aria-label')).toBe('清除篩選');
    });

    it('shows — as the match count when the filtered page fails', () => {
      const { fixture, el } = render('sheet');
      flushSummary('2026-10');
      flushEntries([makeEntry({ id: 1 })], 1);
      fixture.detectChanges();

      fixture.componentInstance.setQuery('午餐');
      fixture.detectChanges();
      httpMock!.expectOne(r => r.url === '/api/accounting/entries').flush('boom', { status: 500, statusText: 'Server Error' });
      fixture.detectChanges();
      expect(text(el.querySelector('.match-count'))).toBe('符合條件 — 筆');
    });

    it('clears the account, kind and query filters from 清除篩選', () => {
      const { fixture, el } = render('sheet');
      flushSummary('2026-10');
      flushEntries([]);
      fixture.detectChanges();

      const timeline = fixture.componentInstance;
      timeline.setAccount('7');
      timeline.setKind('expense');
      timeline.setQuery('午餐');
      fixture.detectChanges();
      flushEntries([], 0);
      fixture.detectChanges();

      (el.querySelector('.clear-filters') as HTMLButtonElement).click();
      fixture.detectChanges();
      const req = flushEntries([]);
      expect(req.request.params.has('account_id')).toBe(false);
      expect(req.request.params.has('kind')).toBe(false);
      expect(req.request.params.has('q')).toBe(false);
      fixture.detectChanges();
      expect(timeline.filtersActive()).toBe(false);
      expect(el.querySelector('.match-row')).toBeNull();
      expect(el.querySelector('.sum-scope')).toBeNull();
    });

    it('shows the scope label and 清除篩選 without a count in the calendar view', () => {
      localStorage.setItem(VIEW_KEY, 'calendar');
      const { fixture, el } = render('phone');
      flushSummary('2026-10');
      flushDaily('2026-10');
      fixture.componentInstance.setKind('expense');
      fixture.detectChanges();

      expect(text(el.querySelector('.sum-scope'))).toBe('全月 · 未套篩選');
      expect(el.querySelector('.match-count')).toBeNull();
      expect(el.querySelector('.match-row--calendar .clear-filters')).not.toBeNull();
    });
  });

    it('cancels pending search at composition start and debounces only the committed composition', () => {
      vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
      const { fixture, el } = render('sheet');
      flushSummary('2026-10'); flushEntries([]); fixture.detectChanges();
      const input = el.querySelector<HTMLInputElement>('.filter-q')!;
      input.value = '午'; input.dispatchEvent(new InputEvent('input'));
      vi.advanceTimersByTime(200);
      input.dispatchEvent(new CompositionEvent('compositionstart'));
      vi.advanceTimersByTime(400); fixture.detectChanges();
      expectNoEntryPage();
      input.value = '午ㄘ';
      input.dispatchEvent(new InputEvent('input', { isComposing: true }));
      for (const key of ['Enter', 'Escape']) {
        input.dispatchEvent(new KeyboardEvent('keydown', { key, isComposing: true, bubbles: true, cancelable: true }));
      }
      vi.advanceTimersByTime(400); fixture.detectChanges();
      expectNoEntryPage();
      expect(input.value).toBe('午ㄘ');
      input.value = '午餐 ';
      input.dispatchEvent(new CompositionEvent('compositionend', { data: '餐' }));
      vi.advanceTimersByTime(299); fixture.detectChanges();
      expectNoEntryPage();
      vi.advanceTimersByTime(1); fixture.detectChanges();
      expect(flushEntries([]).request.params.get('q')).toBe('午餐');
      vi.advanceTimersByTime(300); fixture.detectChanges();
      expectNoEntryPage();
      expect(input.value).toBe('午餐 ');
    });

    it('cancels a pending timer when a composing input arrives without compositionstart', () => {
      vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
      const { fixture, el } = render('sheet');
      flushSummary('2026-10'); flushEntries([]); fixture.detectChanges();
      const input = el.querySelector<HTMLInputElement>('.filter-q')!;
      input.value = '午'; input.dispatchEvent(new InputEvent('input'));
      vi.advanceTimersByTime(200);
      input.value = '午ㄘ'; input.dispatchEvent(new InputEvent('input', { isComposing: true }));
      vi.advanceTimersByTime(400); fixture.detectChanges();
      expectNoEntryPage();
      expect(fixture.componentInstance.query()).toBe('');
    });

    it('preserves raw search text and caret after committing the trimmed query', () => {
      vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
      const { fixture, el } = render('sheet');
      flushSummary('2026-10'); flushEntries([]); fixture.detectChanges();
      const input = el.querySelector<HTMLInputElement>('.filter-q')!;
      input.value = ' 午餐 '; input.setSelectionRange(2, 2);
      input.dispatchEvent(new InputEvent('input'));
      vi.advanceTimersByTime(300); fixture.detectChanges();
      expect(flushEntries([]).request.params.get('q')).toBe('午餐');
      fixture.detectChanges();
      expect(input.value).toBe(' 午餐 ');
      expect(input.selectionStart).toBe(2);
      expect(input.selectionEnd).toBe(2);
      el.querySelector<HTMLButtonElement>('.clear-filters')!.click(); fixture.detectChanges();
      flushEntries([]); fixture.detectChanges();
      expect(input.value).toBe('');
    });

    it('searches 300 ms after the last keystroke and at once on Enter', () => {
      vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
      vi.setSystemTime(new Date(2026, 9, 2, 12, 0, 0));
      const { fixture, el } = render('sheet');
      flushSummary('2026-10');
      flushEntries([]);
      fixture.detectChanges();
      const input = el.querySelector('.filter-q') as HTMLInputElement;

      input.value = '午';
      input.dispatchEvent(new Event('input'));
      vi.advanceTimersByTime(200);
      input.value = '午餐 ';
      input.dispatchEvent(new Event('input'));
      vi.advanceTimersByTime(299);
      fixture.detectChanges();
      expectNoEntryPage();
      vi.advanceTimersByTime(1);
      fixture.detectChanges();
      expect(flushEntries([]).request.params.get('q')).toBe('午餐');

      input.value = '拉麵';
      input.dispatchEvent(new Event('input'));
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
      fixture.detectChanges();
      expect(flushEntries([]).request.params.get('q')).toBe('拉麵');
      vi.advanceTimersByTime(300);
      fixture.detectChanges();
      expectNoEntryPage();
    });

    it('clears a typed search on Esc without letting the key reach the page, and lets Esc through when empty', () => {
      const { fixture, el } = render('sheet');
      flushSummary('2026-10');
      flushEntries([]);
      fixture.detectChanges();
      fixture.componentInstance.setQuery('午餐');
      fixture.detectChanges();
      flushEntries([]);
      fixture.detectChanges();
      const outer = vi.fn();
      document.addEventListener('keydown', outer);
      const input = el.querySelector('.filter-q') as HTMLInputElement;
      input.value = '午餐';

      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      fixture.detectChanges();
      expect(outer).not.toHaveBeenCalled();
      expect(input.value).toBe('');
      expect(flushEntries([]).request.params.has('q')).toBe(false);

      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      expect(outer).toHaveBeenCalledTimes(1);
      document.removeEventListener('keydown', outer);
    });

  it('keeps the totals block with — while the summary loads and offers 重試 when it fails', () => {
    const { fixture, el } = render();
    flushEntries([]);
    fixture.detectChanges();
    const sum = el.querySelector('.sum')!;
    expect(sum.getAttribute('aria-busy')).toBe('true');
    expect([text(el.querySelector('.sum-expense')), text(el.querySelector('.sum-income')), text(el.querySelector('.sum-net'))]).toEqual(['—', '—', '—']);

    httpMock!.expectOne(r => r.url === '/api/accounting/entries/summary').flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();
    expect(el.querySelector('.sum')).not.toBeNull();
    expect(text(el.querySelector('.sum-error'))).toContain('摘要讀取失敗');
    expect(el.querySelector('.empty')).not.toBeNull();

    (el.querySelector('.sum-retry') as HTMLButtonElement).click();
    fixture.detectChanges();
    flushSummary('2026-10', { expense: '-100' });
    fixture.detectChanges();
    expect(el.querySelector('.sum-error')).toBeNull();
    expect(text(el.querySelector('.sum-expense'))).toBe('−$100');
  });

  it('never clips the three summary figures; they may wrap instead', () => {
    const { fixture, el } = render('phone');
    flushSummary('2026-10', { expense: '-1234567', income: '7654321', net: '6419754' });
    flushEntries([]);
    fixture.detectChanges();
    const figure = el.querySelector('.sum-expense')!;
    const style = getComputedStyle(figure);
    expect(style.whiteSpace).toBe('nowrap'); // the component stylesheet is applied
    expect(style.overflow).toBe('visible');
    expect(style.textOverflow).not.toBe('ellipsis');
    expect(getComputedStyle(el.querySelector('.sum small')!).fontSize).toBe('0.72rem');
  });

  describe('calendar view', () => {
    it('offers 重試 when the daily figures fail, without touching the day list', () => {
      localStorage.setItem(VIEW_KEY, 'calendar');
      const { fixture, el } = render('phone');
      flushSummary('2026-10');
      httpMock!.expectOne(r => r.url === '/api/accounting/entries/summary/daily').flush('boom', { status: 500, statusText: 'Server Error' });
      fixture.detectChanges();
      expect(text(el.querySelector('.daily-error'))).toContain('每日摘要讀取失敗');

      (el.querySelector('.daily-retry') as HTMLButtonElement).click();
      fixture.detectChanges();
      flushDaily('2026-10', OCT);
      fixture.detectChanges();
      expect(el.querySelector('.daily-error')).toBeNull();
      expect(text(el.querySelector('button.cell[data-date="2026-10-02"] .exp'))).toBe('1,234');
    });

    it('shows busy daily placeholders and drops previous month figures while ignoring late summary responses', () => {
      localStorage.setItem(VIEW_KEY, 'calendar');
      const { fixture, el } = render('phone');
      expect(el.querySelector('app-calendar-month')!.getAttribute('aria-busy')).toBe('true');
      expect(el.querySelectorAll('.daily-placeholder').length).toBeGreaterThan(0);
      flushSummary('2026-10'); flushDaily('2026-10', OCT); fixture.detectChanges();
      fixture.componentInstance.moveMonth(1); fixture.detectChanges();
      const oldSummary = httpMock!.expectOne(r => r.url === '/api/accounting/entries/summary');
      const oldDaily = httpMock!.expectOne(r => r.url === '/api/accounting/entries/summary/daily');
      expect(el.querySelector('.exp')).toBeNull();
      expect(el.querySelectorAll('.daily-placeholder').length).toBeGreaterThan(0);
      fixture.componentInstance.moveMonth(1); fixture.detectChanges();
      flushSummary('2026-12', { expense: '-900' });
      flushDaily('2026-12', { days: [{ date: '2026-12-02', expense: '-900', income: '0', count: 1 }] });
      oldSummary.flush('boom', { status: 500, statusText: 'Server Error' });
      oldDaily.flush('boom', { status: 500, statusText: 'Server Error' });
      fixture.detectChanges();
      expect(text(el.querySelector('.sum-expense'))).toBe('−$900');
      expect(text(el.querySelector('button.cell[data-date="2026-12-02"] .exp'))).toBe('900');
      expect(el.querySelector('.daily-error')).toBeNull();
      expect(el.querySelector('.sum-error')).toBeNull();
      expect(el.querySelector('.daily-placeholder')).toBeNull();
    });

    it('keeps same-month daily numbers with 更新中 until a fresh daily summary arrives', () => {
      const { fixture, el } = renderCalendar();
      TestBed.inject(AccountingService).deleteEntry(99).subscribe();
      httpMock!.expectOne(r => r.method === 'DELETE' && r.url === '/api/accounting/entries/99').flush(null);
      fixture.detectChanges(); flushCounterparties();
      expect(text(el.querySelector('button.cell[data-date="2026-10-02"] .exp'))).toBe('1,234');
      expect(text(el.querySelector('.daily-updating'))).toBe('更新中');
      expect(el.querySelector('app-calendar-month')!.getAttribute('aria-busy')).toBe('true');
      expect(el.querySelector('.daily-placeholder')).toBeNull();
      flushSummary('2026-10'); flushDaily('2026-10'); fixture.detectChanges();
      expect(el.querySelector('.daily-updating')).toBeNull();
      expect(el.querySelector('app-calendar-month')!.getAttribute('aria-busy')).toBeNull();
    });

    it('shows the day skeleton, then 這天沒有記錄 or 這天沒有符合條件的記錄', () => {
      const { fixture, el } = renderCalendar();
      (el.querySelector('button.cell[data-date="2026-10-05"]') as HTMLButtonElement).click();
      fixture.detectChanges();
      expect(el.querySelector('.day-entries app-skeleton')).not.toBeNull();
      flushEntries([]);
      fixture.detectChanges();
      expect(text(el.querySelector('.day-entries .empty'))).toBe('這天沒有記錄');

      fixture.componentInstance.setKind('expense');
      fixture.detectChanges();
      flushEntries([]);
      fixture.detectChanges();
      expect(text(el.querySelector('.day-entries .empty'))).toBe('這天沒有符合條件的記錄');
    });

    it('shows 記錄讀取失敗 with 重試 for a failed day and reloads that day', () => {
      const { fixture, el } = renderCalendar();
      (el.querySelector('button.cell[data-date="2026-10-05"]') as HTMLButtonElement).click();
      fixture.detectChanges();
      httpMock!.expectOne(r => r.url === '/api/accounting/entries').flush('boom', { status: 500, statusText: 'Server Error' });
      fixture.detectChanges();
      expect(text(el.querySelector('.day-entries .load-error'))).toBe('記錄讀取失敗');
      expect(el.querySelector('.day-entries .empty')).toBeNull();

      (el.querySelector('.day-retry') as HTMLButtonElement).click();
      fixture.detectChanges();
      const req = flushEntries([makeEntry({ id: 9, entry_date: '2026-10-05' })]);
      fixture.detectChanges();
      expect(req.request.params.get('date_from')).toBe('2026-10-05');
      expect(el.querySelectorAll('.day-entries .row').length).toBe(1);
    });

    const OCT: Partial<DailySummary> = {
      days: [
        { date: '2026-10-02', expense: '-1234.0000', income: '500.0000', count: 3 },
        { date: '2026-10-05', expense: '0.0000', income: '50042.0000', count: 2 },
      ],
    };

    function renderCalendar(preference: Preference = makePreference()) {
      localStorage.setItem(VIEW_KEY, 'calendar');
      const rendered = render('phone', preference);
      flushSummary('2026-10');
      flushDaily('2026-10', OCT);
      expectNoEntryPage();
      rendered.fixture.detectChanges();
      return rendered;
    }

    it('switches to 日曆 from the radio toggle and remembers it', () => {
      const { fixture, el } = render();
      flushSummary('2026-10');
      flushEntries([makeEntry({ id: 1, name: '午餐' })]);
      fixture.detectChanges();
      const group = el.querySelector('.view-toggle')!;
      expect(group.getAttribute('role')).toBe('radiogroup');
      expect(group.getAttribute('aria-label')).toBe('檢視模式');
      expect(viewButton(el, '清單').getAttribute('aria-checked')).toBe('true');

      viewButton(el, '日曆').click();
      fixture.detectChanges();
      flushDaily('2026-10', OCT);
      fixture.detectChanges();

      expect(localStorage.getItem(VIEW_KEY)).toBe('calendar');
      expect(viewButton(el, '日曆').getAttribute('aria-checked')).toBe('true');
      expect(viewButton(el, '清單').getAttribute('aria-checked')).toBe('false');
      expect(el.querySelector('app-calendar-month')).not.toBeNull();
      expect(el.querySelectorAll('.row').length).toBe(0);
      expect(el.querySelector('.day')).toBeNull();
    });

    it('restores 日曆 on init and loads the daily summary instead of an entry page', () => {
      const { el } = renderCalendar();
      expect(viewButton(el, '日曆').getAttribute('aria-checked')).toBe('true');
      expect(el.querySelectorAll('app-calendar-month button.cell').length).toBe(42);
      expect(text(el.querySelector('button.cell[data-date="2026-10-02"] .exp'))).toBe('1,234');
      expect(el.querySelector('.sentinel')).toBeNull();
      expect(el.querySelector('button.more')).toBeNull();
    });

    it('defaults to 日曆 in the two-pane layout when nothing is stored', () => {
      const { fixture, el } = render('panes');
      flushSummary('2026-10');
      flushDaily('2026-10');
      expectNoEntryPage();
      fixture.detectChanges();
      expect(viewButton(el, '日曆').getAttribute('aria-checked')).toBe('true');
      expect(el.querySelector('app-calendar-month')).not.toBeNull();
      expect(localStorage.getItem(VIEW_KEY)).toBeNull();
    });

    it('defaults to 清單 on a phone when nothing is stored', () => {
      const { fixture, el } = render('phone');
      flushSummary('2026-10');
      flushEntries([]);
      fixture.detectChanges();
      expect(viewButton(el, '清單').getAttribute('aria-checked')).toBe('true');
      expect(el.querySelector('app-calendar-month')).toBeNull();
    });

    it('keeps a stored 清單 in the two-pane layout and ignores later resizes', () => {
      localStorage.setItem(VIEW_KEY, 'list');
      const { fixture, el } = render('panes');
      flushSummary('2026-10');
      flushEntries([]);
      fixture.detectChanges();
      expect(viewButton(el, '清單').getAttribute('aria-checked')).toBe('true');

      TestBed.inject(LayoutModeService).set('phone');
      fixture.detectChanges();
      TestBed.inject(LayoutModeService).set('panes');
      fixture.detectChanges();
      expect(viewButton(el, '清單').getAttribute('aria-checked')).toBe('true');
    });

    it('falls back to the list for an unknown stored view', () => {
      localStorage.setItem(VIEW_KEY, 'agenda');
      const { fixture, el } = render();
      flushSummary('2026-10');
      flushEntries([]);
      fixture.detectChanges();
      expect(viewButton(el, '清單').getAttribute('aria-checked')).toBe('true');
    });

    it("loads a tapped day's entries under the grid with the list's row markup", () => {
      const { fixture, el } = renderCalendar(makePreference({ hide_rewards_on_timeline: true }));
      const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);

      (el.querySelector('button.cell[data-date="2026-10-02"]') as HTMLButtonElement).click();
      fixture.detectChanges();
      const req = httpMock!.expectOne(r => r.url === '/api/accounting/entries');
      expect(req.request.params.get('date_from')).toBe('2026-10-02');
      expect(req.request.params.get('date_to')).toBe('2026-10-02');
      expect(req.request.params.get('limit')).toBe('500');
      expect(req.request.params.get('offset')).toBe('0');
      expect(req.request.params.get('hide_rewards')).toBe('true');
      req.flush({
        items: [
          makeEntry({ id: 11, name: '午餐', amount: '-1234.0000' }),
          makeEntry({ id: 12, kind: 'income', name: '退款', amount: '500.0000' }),
        ],
        total: 2,
        limit: 500,
        offset: 0,
      });
      fixture.detectChanges();

      expect(el.querySelector('button.cell[data-date="2026-10-02"]')!.getAttribute('aria-pressed')).toBe('true');
      const net = el.querySelector('.day-net b')!;
      expect(text(net)).toBe('−$734');
      expect(net.classList).toContain('neg');
      const rows = Array.from(el.querySelectorAll<HTMLButtonElement>('.day-entries .row'));
      expect(rows.map(row => row.getAttribute('data-entry-id'))).toEqual(['11', '12']);
      expect(text(rows[0].querySelector('.amt'))).toBe('−$1,234');
      expect(rows[0].querySelector('.amt')!.classList).toContain('neg');
      rows[1].click();
      expect(navigate).toHaveBeenCalledWith(['/accounting/entries', 12]);

      (el.querySelector('button.cell[data-date="2026-10-02"]') as HTMLButtonElement).click();
      fixture.detectChanges();
      expect(el.querySelector('.day-entries')).toBeNull();
      expect(el.querySelector('button.cell[data-date="2026-10-02"]')!.getAttribute('aria-pressed')).toBe('false');
    });

    it('applies the account filter to the day entries', () => {
      const { fixture, el } = renderCalendar();
      (el.querySelector('button.cell[data-date="2026-10-05"]') as HTMLButtonElement).click();
      fixture.detectChanges();
      flushEntries([]);

      fixture.componentInstance.setAccount('7');
      fixture.detectChanges();
      const req = flushEntries([]);
      expect(req.request.params.getAll('account_id')).toEqual(['7']);
      expect(req.request.params.get('date_from')).toBe('2026-10-05');
    });

    it('clears the selected day when the month changes', () => {
      const { fixture, el } = renderCalendar();
      (el.querySelector('button.cell[data-date="2026-10-05"]') as HTMLButtonElement).click();
      fixture.detectChanges();
      flushEntries([makeEntry({ id: 1, entry_date: '2026-10-05' })]);
      fixture.detectChanges();

      (el.querySelector('.month-next') as HTMLButtonElement).click();
      fixture.detectChanges();
      flushSummary('2026-11');
      flushDaily('2026-11');
      expectNoEntryPage();
      fixture.detectChanges();
      expect(el.querySelector('.day-entries')).toBeNull();
      expect(el.querySelector('button.cell.sel')).toBeNull();
    });

    it('re-reads the daily summary and the open day after an entry write', () => {
      const { fixture, el } = renderCalendar();
      (el.querySelector('button.cell[data-date="2026-10-05"]') as HTMLButtonElement).click();
      fixture.detectChanges();
      flushEntries([]);

      TestBed.inject(AccountingService).deleteEntry(3).subscribe();
      httpMock!.expectOne(r => r.method === 'DELETE').flush(null);
      fixture.detectChanges();
      flushCounterparties();

      flushSummary('2026-10');
      flushDaily('2026-10');
      const req = flushEntries([]);
      expect(req.request.params.get('date_from')).toBe('2026-10-05');
    });

    it('notes the currencies the daily figures leave out', () => {
      localStorage.setItem(VIEW_KEY, 'calendar');
      const { fixture, el } = render();
      flushSummary('2026-10');
      flushDaily('2026-10', { missing_rates: ['JPY'] });
      fixture.detectChanges();
      expect(text(el.querySelector('.missing-rates'))).toBe('部分外幣未換算 (JPY)');
    });

    it('drops a late daily summary for the previous month', () => {
      const { fixture, el } = render();
      flushSummary('2026-10');
      flushEntries([]);
      viewButton(el, '日曆').click();
      fixture.detectChanges();
      const late = httpMock!.expectOne(r => r.url === '/api/accounting/entries/summary/daily');

      (el.querySelector('.month-next') as HTMLButtonElement).click();
      fixture.detectChanges();
      flushSummary('2026-11');
      flushDaily('2026-11');
      late.flush({ month: '2026-10', currency: 'TWD', days: OCT.days, missing_rates: [] });
      fixture.detectChanges();
      expect(el.querySelector('button.cell[data-date="2026-10-02"]')).toBeNull();
      expect(el.querySelectorAll('app-calendar-month .fig').length).toBe(0);
    });

    it('nets the selected day from the rows shown when a filter is active', () => {
      const { fixture, el } = renderCalendar();
      fixture.componentInstance.setKind('expense');
      fixture.detectChanges();
      (el.querySelector('button.cell[data-date="2026-10-02"]') as HTMLButtonElement).click();
      fixture.detectChanges();
      const req = flushEntries([
        makeEntry({ id: 11, name: '午餐', amount: '-200.0000' }),
        makeEntry({ id: 12, name: '咖啡', amount: '-80.0000' }),
        makeEntry({ id: 13, name: '車票', amount: '-900.0000', currency: 'JPY' }),
      ]);
      fixture.detectChanges();

      expect(req.request.params.get('kind')).toBe('expense');
      // The summary says −1,234 / +500 for the day; the filtered TWD rows sum to −280.
      expect(text(el.querySelector('.day-net b'))).toBe('−$280');
      expect(el.querySelector('.day-net b')!.classList).toContain('neg');
    });

    it('drops a late response for a previously tapped day', () => {
      const { fixture, el } = renderCalendar();
      (el.querySelector('button.cell[data-date="2026-10-02"]') as HTMLButtonElement).click();
      fixture.detectChanges();
      const first = httpMock!.expectOne(r => r.url === '/api/accounting/entries');
      (el.querySelector('button.cell[data-date="2026-10-05"]') as HTMLButtonElement).click();
      fixture.detectChanges();
      const second = httpMock!.expectOne(r => r.url === '/api/accounting/entries');
      expect(second.request.params.get('date_from')).toBe('2026-10-05');

      second.flush({ items: [makeEntry({ id: 5, entry_date: '2026-10-05', name: '薪水' })], total: 1, limit: 500, offset: 0 });
      first.flush({ items: [makeEntry({ id: 2, entry_date: '2026-10-02', name: '午餐' })], total: 1, limit: 500, offset: 0 });
      fixture.detectChanges();
      expect(Array.from(el.querySelectorAll('.day-entries .row .name')).map(text)).toEqual(['薪水']);
    });

    it('re-requests the daily summary after a preference save', () => {
      const { fixture } = renderCalendar();
      TestBed.inject(AccountingService).updatePreference(makePreference({ hide_rewards_on_timeline: true })).subscribe();
      httpMock!.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/preference').flush(makePreference({ hide_rewards_on_timeline: true }));
      fixture.detectChanges();

      flushSummary('2026-10');
      flushDaily('2026-10');
      expectNoEntryPage();
    });

    it("moves today's outline when the date changes and the tab becomes visible again", () => {
      const { fixture, el } = renderCalendar();
      const current = () => el.querySelector('button.cell[aria-current="date"]')?.getAttribute('data-date');
      expect(current()).toBe('2026-10-02');

      fixture.componentInstance.today.set('2026-10-05');
      fixture.detectChanges();
      expect(current()).toBe('2026-10-05');

      vi.setSystemTime(new Date(2026, 9, 3, 0, 5, 0));
      const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
      document.dispatchEvent(new Event('visibilitychange'));
      fixture.detectChanges();
      visibility.mockRestore();
      expect(current()).toBe('2026-10-03');
    });

    it('loads page 1 of the list when switching back to 清單', () => {
      const { fixture, el } = renderCalendar();
      viewButton(el, '清單').click();
      fixture.detectChanges();

      const req = flushEntries([makeEntry({ id: 1, name: '午餐' })]);
      fixture.detectChanges();
      expect(req.request.params.get('offset')).toBe('0');
      expect(req.request.params.get('limit')).toBe('50');
      expect(localStorage.getItem(VIEW_KEY)).toBe('list');
      expect(el.querySelector('app-calendar-month')).toBeNull();
      expect(Array.from(el.querySelectorAll('.row .name')).map(text)).toEqual(['午餐']);
    });
  });

  it('reloads the account filter options after an account write', () => {
    localStorage.setItem(VIEW_KEY, 'list');
    const { fixture, el } = render('panes');
    flushSummary('2026-10');
    flushEntries([]);
    fixture.detectChanges();

    TestBed.inject(AccountingService).createAccount({ name: '新卡' } as never).subscribe();
    httpMock!.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/accounts').flush(makeAccount({ id: 9, name: '新卡' }));
    fixture.detectChanges();
    httpMock!.expectOne(r => r.method === 'GET' && r.url === '/api/accounting/accounts').flush([...ACCOUNTS, makeAccount({ id: 9, name: '新卡' })]);
    flushCounterparties();
    fixture.detectChanges();

    const options = Array.from(el.querySelectorAll('.filter-account option')).map(text);
    expect(options).toEqual(['全部帳戶', '玉山 UNI', '富邦 J卡', '新卡']);
  });

  describe('billing hints', () => {
    // Closing day 15, due 20 days later: the 08/16 – 09/15 statement is due on 10/05, the 10/15 one on 11/04.
    const CARD = makeAccount({
      id: 9,
      name: '玉山 UNI',
      is_credit: true,
      closing_day: 15,
      due_rule: 'days_after_closing',
      due_value: 20,
    });
    // `paidTo`: payments count from the closing through the day before the next closing, capped at today (10/02).
    const SEPT = { start: '2026-08-16', end: '2026-09-15', due: '2026-10-05', paidTo: '2026-10-02' };
    const OCT_CYCLE = { start: '2026-09-16', end: '2026-10-15', due: '2026-11-04', paidTo: '2026-10-15' };

    function billRequests(cycle = SEPT, paidTo = cycle.paidTo): { summary: TestRequest; payments: TestRequest } {
      const summary = httpMock!.expectOne(
        r => r.url === '/api/accounting/accounts/9/summary' && r.params.get('date_to') === cycle.end,
      );
      expect(summary.request.params.get('date_from')).toBe(cycle.start);
      const payments = httpMock!.expectOne(
        r => r.url === '/api/accounting/accounts/9/entries' && r.params.get('date_from') === cycle.end,
      );
      expect(payments.request.params.get('kind')).toBe('transfer_in');
      expect(payments.request.params.get('date_to')).toBe(paidTo);
      return { summary, payments };
    }

    function answerBill(requests: { summary: TestRequest; payments: TestRequest }, spend: string, payments: string[] = []): void {
      requests.summary.flush({
        account_id: 9,
        currency: 'TWD',
        date_from: '',
        date_to: '',
        spend,
        income: '0',
        rewards: '0',
        net: spend,
        end_balance: spend,
        count: 4,
      });
      requests.payments.flush({
        items: payments.map((amount, index) => makeEntry({ id: 40 + index, kind: 'transfer_in', account_id: 9, amount })),
        total: payments.length,
        limit: 100,
        offset: 0,
      });
    }

    function flushBill(spend: string, payments: string[] = [], cycle = SEPT, paidTo = cycle.paidTo): void {
      answerBill(billRequests(cycle, paidTo), spend, payments);
    }

    function expectNoBillRequest(): void {
      httpMock!.expectNone(r => r.url.startsWith('/api/accounting/accounts/9/'));
    }

    function flushDayEntries(): void {
      httpMock!.expectOne(r => r.url === '/api/accounting/entries').flush({ items: [], total: 0, limit: 500, offset: 0 });
    }

    function renderCalendarWith(accounts = [...ACCOUNTS, CARD]) {
      localStorage.setItem(VIEW_KEY, 'calendar');
      const rendered = render('phone', makePreference(), [], accounts);
      flushSummary('2026-10');
      flushDaily('2026-10');
      rendered.fixture.detectChanges();
      return rendered;
    }

    function renderListWith(accounts = [...ACCOUNTS, CARD]) {
      const rendered = render('phone', makePreference(), [], accounts);
      flushSummary('2026-10');
      flushEntries([]);
      rendered.fixture.detectChanges();
      return rendered;
    }

    function selectDay(fixture: { detectChanges(): void }, el: HTMLElement, date: string): void {
      (el.querySelector(`button.cell[data-date="${date}"]`) as HTMLButtonElement).click();
      fixture.detectChanges();
      flushDayEntries();
      fixture.detectChanges();
    }

    const cell = (el: HTMLElement, date: string) => el.querySelector(`button.cell[data-date="${date}"]`)!;

    it('badges an unpaid statement only once it is resolved and shows its line on the due day', () => {
      const { fixture, el } = renderCalendarWith();
      const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
      const requests = billRequests();
      expect(cell(el, '2026-10-05').querySelector('.due-badge')).toBeNull();
      expect(cell(el, '2026-10-15').classList).toContain('closing');

      answerBill(requests, '-12345.0000');
      fixture.detectChanges();
      expect(text(cell(el, '2026-10-05').querySelector('.due-badge'))).toBe('💳');
      expect(cell(el, '2026-10-05').getAttribute('aria-label')).toBe('10月5日，1 張卡繳費到期');

      selectDay(fixture, el, '2026-10-05');
      expectNoBillRequest();
      const line = el.querySelector<HTMLButtonElement>('.day-entries .bill')!;
      expect(text(line)).toBe('💳 玉山 UNI 帳單 $12,345 · 到期');
      expect(line.compareDocumentPosition(el.querySelector('.day-entries .empty')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      line.click();
      expect(navigate).toHaveBeenCalledWith(['/accounting/accounts', 9]);
    });

    it('hides a zero statement everywhere and keeps it resolved across a view switch', () => {
      const { fixture, el } = renderListWith();
      flushBill('0.0000');
      fixture.detectChanges();
      expect(el.querySelector('.bill-banner')).toBeNull();

      viewButton(el, '日曆').click();
      fixture.detectChanges();
      flushDaily('2026-10');
      expectNoBillRequest();
      fixture.detectChanges();
      expect(el.querySelectorAll('.due-badge').length).toBe(0);
      expect(cell(el, '2026-10-15').classList).toContain('closing');

      selectDay(fixture, el, '2026-10-05');
      expect(el.querySelector('.bill')).toBeNull();
    });

    it('hides a fully paid statement', () => {
      const { fixture, el } = renderCalendarWith();
      flushBill('-800.0000', ['500.0000', '300.0000']);
      fixture.detectChanges();
      expect(el.querySelectorAll('.due-badge').length).toBe(0);
      selectDay(fixture, el, '2026-10-05');
      expect(el.querySelector('.bill')).toBeNull();
    });

    it('hides a statement that cannot be read', () => {
      const { fixture, el } = renderCalendarWith();
      const requests = billRequests();
      requests.summary.flush('boom', { status: 500, statusText: 'Server Error' });
      requests.payments.flush({ items: [], total: 0, limit: 100, offset: 0 });
      fixture.detectChanges();
      expect(el.querySelectorAll('.due-badge').length).toBe(0);
    });

    it('shows what was paid and what remains on a partly paid statement', () => {
      const { fixture, el } = renderCalendarWith();
      flushBill('-12345.0000', ['5000.0000']);
      fixture.detectChanges();
      expect(text(cell(el, '2026-10-05').querySelector('.due-badge'))).toBe('💳');

      selectDay(fixture, el, '2026-10-05');
      const line = el.querySelector('.day-entries .bill')!;
      expect(text(line)).toBe('💳 玉山 UNI 帳單 $12,345 · 到期 已繳 $5,000 · 剩餘 $7,345');
      expect(line.classList).toContain('partial');
    });

    it('re-resolves after an entry write, keeping the badge until the new figures land', () => {
      const { fixture, el } = renderCalendarWith();
      flushBill('-12345.0000');
      fixture.detectChanges();

      TestBed.inject(AccountingService).deleteEntry(3).subscribe();
      httpMock!.expectOne(r => r.method === 'DELETE').flush(null);
      fixture.detectChanges();
      flushCounterparties();
      flushSummary('2026-10');
      flushDaily('2026-10');
      const requests = billRequests();
      fixture.detectChanges();
      expect(cell(el, '2026-10-05').querySelector('.due-badge')).not.toBeNull();

      answerBill(requests, '-12345.0000', ['12345.0000']);
      fixture.detectChanges();
      expect(cell(el, '2026-10-05').querySelector('.due-badge')).toBeNull();
    });

    it('resolves each statement once while moving between months', () => {
      const { fixture, el } = renderCalendarWith();
      flushBill('-100.0000');
      fixture.detectChanges();

      (el.querySelector('.month-next') as HTMLButtonElement).click();
      fixture.detectChanges();
      flushSummary('2026-11');
      flushDaily('2026-11');
      flushBill('-200.0000', [], OCT_CYCLE);
      fixture.detectChanges();
      expect(cell(el, '2026-11-04').querySelector('.due-badge')).not.toBeNull();

      (el.querySelector('.month-prev') as HTMLButtonElement).click();
      fixture.detectChanges();
      flushSummary('2026-10');
      flushDaily('2026-10');
      expectNoBillRequest();
      fixture.detectChanges();
      expect(cell(el, '2026-10-05').querySelector('.due-badge')).not.toBeNull();
    });

    it('announces the unpaid due days of the next 7 days in the list and opens the first in the calendar', () => {
      const { fixture, el } = renderListWith();
      flushBill('-12345.0000');
      fixture.detectChanges();

      const banner = el.querySelector<HTMLButtonElement>('.bill-banner')!;
      expect(text(banner)).toBe('近 7 天有 1 筆繳費到期');
      banner.click();
      fixture.detectChanges();
      flushDaily('2026-10');
      flushDayEntries();
      expectNoBillRequest();
      fixture.detectChanges();

      expect(viewButton(el, '日曆').getAttribute('aria-checked')).toBe('true');
      expect(cell(el, '2026-10-05').getAttribute('aria-pressed')).toBe('true');
      expect(text(el.querySelector('.day-entries .bill'))).toBe('💳 玉山 UNI 帳單 $12,345 · 到期');
      expect(el.querySelector('.bill-banner')).toBeNull();
    });

    it("resolves next month's statement for the banner and moves there on tap", () => {
      vi.setSystemTime(new Date(2026, 9, 30, 12, 0, 0));
      const { fixture, el } = renderListWith();
      flushBill('-100.0000', ['100.0000'], SEPT, '2026-10-14');
      flushBill('-200.0000', [], OCT_CYCLE, '2026-10-30');
      fixture.detectChanges();
      expect(text(el.querySelector('.bill-banner'))).toBe('近 7 天有 1 筆繳費到期');

      el.querySelector<HTMLButtonElement>('.bill-banner')!.click();
      fixture.detectChanges();
      flushSummary('2026-11');
      flushDaily('2026-11');
      flushDayEntries();
      expectNoBillRequest();
      fixture.detectChanges();

      expect(text(el.querySelector('.month-label'))).toBe('2026 年 11 月');
      expect(cell(el, '2026-11-04').getAttribute('aria-pressed')).toBe('true');
      expect(text(el.querySelector('.day-entries .bill'))).toBe('💳 玉山 UNI 帳單 $200 · 到期');
    });

    const ALAN: Counterparty = { id: 1, name: 'Alan', open_amounts: [{ currency: 'TWD', amount: '220.0000' }], open_count: 2 };
    const SETTLED: Counterparty = { id: 2, name: 'Bea', open_amounts: [], open_count: 0 };
    const bell = (el: HTMLElement) => el.querySelector<HTMLButtonElement>('.bell')!;

    it('shows the 🔔 without a count when nothing is pending, in both views', () => {
      const { fixture, el } = renderListWith(ACCOUNTS);
      expect(text(bell(el))).toBe('🔔');
      expect(bell(el).getAttribute('aria-label')).toBe('提醒中心，0 項');
      expect(el.querySelector('.bell-count')).toBeNull();

      viewButton(el, '日曆').click();
      fixture.detectChanges();
      flushDaily('2026-10');
      fixture.detectChanges();
      expect(bell(el)).not.toBeNull();
    });

    it('counts unpaid cards and counterparties with an open amount, and opens the reminder centre', () => {
      const rendered = render('phone', makePreference(), [], [...ACCOUNTS, CARD], [ALAN, SETTLED]);
      flushSummary('2026-10');
      flushEntries([]);
      rendered.fixture.detectChanges();
      const { fixture, el } = rendered;
      // Before the statement lands only the counterparty counts.
      expect(text(el.querySelector('.bell-count'))).toBe('1');

      flushBill('-12345.0000');
      fixture.detectChanges();
      expect(text(el.querySelector('.bell-count'))).toBe('2');
      expect(bell(el).getAttribute('aria-label')).toBe('提醒中心，2 項');

      const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
      bell(el).click();
      expect(navigate).toHaveBeenCalledWith(['/accounting/reminders']);
    });

    it('counts 待完成交易 items due today or earlier', () => {
      // Spec "Bell includes due confirm items" (today here is 2026-10-02: 10-01 is due, 10-20 is not).
      const queue = [makeInstance({ id: 1, due_date: '2026-10-01' }), makeInstance({ id: 2, due_date: '2026-10-20' })];
      const rendered = render('phone', makePreference(), [], [...ACCOUNTS, CARD], [], queue);
      flushSummary('2026-10');
      flushEntries([]);
      rendered.fixture.detectChanges();
      flushBill('-12345.0000');
      rendered.fixture.detectChanges();
      expect(text(rendered.el.querySelector('.bell-count'))).toBe('2');
      expect(bell(rendered.el).getAttribute('aria-label')).toBe('提醒中心，2 項');
    });

    it('counts a partial period until it is accepted', () => {
      // Spec "Partial period counted until accepted".
      const partial = makeInstance({ id: 5, status: 'posted', is_partial: true, due_date: '2026-11-09' });
      const rendered = render('phone', makePreference(), [], ACCOUNTS, [], [partial]);
      flushSummary('2026-10');
      flushEntries([]);
      rendered.fixture.detectChanges();
      expect(text(rendered.el.querySelector('.bell-count'))).toBe('1');
      TestBed.inject(AccountingService).acceptPartialScheduleInstance(5).subscribe();
      httpMock!.expectOne(r => r.url.endsWith('/instances/5/accept-partial')).flush(partial);
      rendered.fixture.detectChanges();
      flushSummary('2026-10');
      flushEntries([]);
      flushCounterparties([], []);
      rendered.fixture.detectChanges();
      expect(rendered.el.querySelector('.bell-count')).toBeNull();
    });

    it('stops counting the item of a definition the owner paused', () => {
      // Spec "Paused definition not counted".
      const due = makeInstance({ id: 6, definition_id: 3, due_date: '2026-10-01' });
      const rendered = render('phone', makePreference(), [], ACCOUNTS, [], [due]);
      flushSummary('2026-10');
      flushEntries([]);
      rendered.fixture.detectChanges();
      expect(text(rendered.el.querySelector('.bell-count'))).toBe('1');
      TestBed.inject(AccountingService).pauseSchedule(3).subscribe();
      httpMock!.expectOne(r => r.url.endsWith('/definitions/3/pause')).flush({});
      rendered.fixture.detectChanges();
      flushSummary('2026-10');
      flushEntries([]);
      flushCounterparties([], []);
      rendered.fixture.detectChanges();
      expect(rendered.el.querySelector('.bell-count')).toBeNull();
    });

    it('re-reads the counterparties after an entry write', () => {
      const { fixture, el } = renderListWith(ACCOUNTS);
      TestBed.inject(AccountingService).deleteEntry(3).subscribe();
      httpMock!.expectOne(r => r.method === 'DELETE').flush(null);
      fixture.detectChanges();
      flushSummary('2026-10');
      flushEntries([]);
      flushCounterparties([ALAN]);
      fixture.detectChanges();
      expect(text(el.querySelector('.bell-count'))).toBe('1');
    });

    it('counts a counterparty whose receivable and payable net to zero, by its open rows', () => {
      const { fixture, el } = renderListWith(ACCOUNTS);
      TestBed.inject(AccountingService).deleteEntry(3).subscribe();
      httpMock!.expectOne(r => r.method === 'DELETE').flush(null);
      fixture.detectChanges();
      flushSummary('2026-10');
      flushEntries([]);
      flushCounterparties([{ id: 4, name: 'Dee', open_amounts: [], open_count: 2 }, SETTLED]);
      fixture.detectChanges();
      expect(text(el.querySelector('.bell-count'))).toBe('1');
    });

    it('leaves a card whose statement cannot be read out of the count', () => {
      const { fixture, el } = renderListWith();
      const requests = billRequests();
      requests.summary.flush({
        account_id: 9, currency: 'TWD', date_from: '', date_to: '', spend: '-100.0000', income: '0', rewards: '0',
        net: '0', end_balance: '0', count: 1,
      });
      requests.payments.flush('boom', { status: 500, statusText: 'Server Error' });
      fixture.detectChanges();
      expect(el.querySelector('.bell-count')).toBeNull();
    });

    it('leaves a paid card out of the count', () => {
      const { fixture, el } = renderListWith();
      flushBill('-800.0000', ['800.0000']);
      fixture.detectChanges();
      expect(el.querySelector('.bell-count')).toBeNull();
    });

    it('badges the due day of a card whose statement is the calendar month', () => {
      const monthly = makeAccount({ id: 9, name: '月結卡', is_credit: true, closing_day: null, due_rule: 'fixed_day', due_value: 5 });
      const { fixture, el } = renderCalendarWith([...ACCOUNTS, monthly]);
      flushBill('-500.0000', [], { start: '2026-09-01', end: '2026-09-30', due: '2026-10-05', paidTo: '2026-10-02' });
      fixture.detectChanges();
      expect(text(cell(el, '2026-10-05').querySelector('.due-badge'))).toBe('💳');
      expect(cell(el, '2026-10-31').classList).toContain('closing');
    });

    it("badges only the master card, with its combined cards' spend in the statement", () => {
      const child = makeAccount({ id: 21, name: '玉山 UNI 副卡', is_credit: true, closing_day: 15, due_rule: 'days_after_closing', due_value: 20, combined_account_id: 9 });
      const { fixture, el } = renderCalendarWith([...ACCOUNTS, CARD, child]);
      const requests = billRequests();
      const childSummary = httpMock!.expectOne(r => r.url === '/api/accounting/accounts/21/summary');
      expect(childSummary.request.params.get('date_to')).toBe(SEPT.end);
      httpMock!.expectNone(r => r.url === '/api/accounting/accounts/21/entries');
      answerBill(requests, '-1000.0000');
      childSummary.flush({
        account_id: 21, currency: 'TWD', date_from: '', date_to: '', spend: '-234.0000', income: '0', rewards: '0',
        net: '0', end_balance: '0', count: 1,
      });
      fixture.detectChanges();

      expect(el.querySelectorAll('.due-badge').length).toBe(1);
      expect(cell(el, '2026-10-05').getAttribute('aria-label')).toBe('10月5日，1 張卡繳費到期');
      selectDay(fixture, el, '2026-10-05');
      expect(Array.from(el.querySelectorAll('.day-entries .bill')).map(text)).toEqual(['💳 玉山 UNI 帳單 $1,234 · 到期']);
    });

    it('renders nothing and asks for no statement without credit cards', () => {
      const { fixture, el } = renderListWith(ACCOUNTS);
      httpMock!.expectNone(r => r.url.includes('/accounts/') && r.url.includes('/summary'));
      expect(el.querySelector('.bill-banner')).toBeNull();

      viewButton(el, '日曆').click();
      fixture.detectChanges();
      flushDaily('2026-10');
      fixture.detectChanges();
      expect(el.querySelectorAll('.due-badge, .cell.closing').length).toBe(0);
      selectDay(fixture, el, '2026-10-05');
      expect(el.querySelector('.bill')).toBeNull();
    });
  });
});

describe('buildDays', () => {
  const group = (total: string | null, currency = 'TWD') => ({
    id: 4, kind: 'split' as const, name: '旅行', merchant: null, description: null, count: 2, total, currency,
  });

  const link = (overrides: Record<string, unknown>) =>
    ({ definition_id: 3, instance_id: 9, kind: 'installment', seq: 5, times: 36, name: '分期', is_partial: false, acted_by: 'auto',
       posted_entry_ids: [1], ...overrides }) as LedgerEntry['schedule'];

  it('puts the schedule pill first, in MOZE wording', () => {
    // Spec "Installment pill", "Unlimited recurring pill".
    const [installment] = buildDays([makeEntry({ id: 1, schedule: link({}) })], 'TWD', false);
    expect(installment.rows[0].pills[0]).toEqual({ label: '分期 #5/36', tone: '' });
    expect(schedulePills(makeEntry({ schedule: link({ kind: 'recurring', seq: 25, times: null }) }))).toEqual([
      { label: '週期 #25', tone: '' },
    ]);
    const transfer = buildDays(
      [makeEntry({ id: 3, kind: 'transfer_out', amount: '-15000.0000', transfer_group_id: 't-9', schedule: link({ kind: 'recurring', seq: 2, times: 12 }) })],
      'TWD',
      false,
    );
    expect(transfer[0].rows[0].pills.map(pill => pill.label)).toEqual(['週期 #2/12', '轉帳']);
  });

  it('adds 部分 to a partial period', () => {
    expect(schedulePills(makeEntry({ schedule: link({ seq: 1, is_partial: true }) }))).toEqual([
      { label: '分期 #1/36', tone: '' },
      { label: '部分', tone: 'review' },
    ]);
  });

  it('shows — for a group without a total and leaves it out of the day net', () => {
    const days = buildDays(
      [
        makeEntry({ id: 1, amount: '-100.0000', group: group(null, 'JPY') }),
        makeEntry({ id: 2, amount: '-50.0000' }),
      ],
      'TWD',
      false,
    );
    expect(days[0].rows[0]).toMatchObject({ amountText: '—', tone: 'neutral', groupCount: 2 });
    expect(days[0].net).toBe(-50);
  });

  it('keeps one row per split group across pages and per transfer pair, leaving transfers out of the net', () => {
    const page1 = [
      makeEntry({ id: 1, amount: '-300.0000', group: group('-410.0000') }),
      makeEntry({ id: 3, kind: 'transfer_out', amount: '-1000.0000', transfer_group_id: 't-1', account_name: '錢包' }),
    ];
    const page2 = [
      makeEntry({ id: 2, amount: '-110.0000', group: group('-410.0000') }),
      makeEntry({ id: 4, kind: 'transfer_in', amount: '1000.0000', transfer_group_id: 't-1', account_name: '玉山' }),
    ];
    const days = buildDays([...page1, ...page2], 'TWD', false);

    expect(days[0].rows.map(row => row.key)).toEqual(['g4', 'tt-1']);
    expect(days[0].rows[1]).toMatchObject({ sub: '錢包 → 玉山', tone: 'neutral' });
    expect(days[0].net).toBe(-410);
  });

  it('drops rewards when hideRewards is on', () => {
    const entries = [makeEntry({ id: 1, kind: 'reward', amount: '12.0000' }), makeEntry({ id: 2, amount: '-80.0000' })];
    expect(buildDays(entries, 'TWD', true)[0].rows.map(row => row.entryId)).toEqual([2]);
    expect(buildDays(entries, 'TWD', false)[0].rows.map(row => row.entryId)).toEqual([1, 2]);
    expect(buildDays(entries, 'TWD', true)[0].net).toBe(-80);
  });
});
