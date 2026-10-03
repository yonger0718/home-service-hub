import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { Provider, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DailySummary, LedgerEntry, MonthSummary, Preference } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutMode, LayoutModeService } from '../../../services/layout-mode.service';
import { AccountingLayoutComponent } from '../accounting-layout/accounting-layout';
import { makeAccount, makeEntry, makePreference } from '../testing/fixtures';
import { LedgerTimelineComponent, buildDays } from './timeline';

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

  it("clears the previous month's rows when the reset load fails, keeping the error", () => {
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
    expect(el.querySelector('.load-error')).not.toBeNull();
  });

  describe('calendar view', () => {
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
    const { fixture, el } = render('panes');
    flushSummary('2026-10');
    flushEntries([]);
    fixture.detectChanges();

    TestBed.inject(AccountingService).createAccount({ name: '新卡' } as never).subscribe();
    httpMock!.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/accounts').flush(makeAccount({ id: 9, name: '新卡' }));
    fixture.detectChanges();
    httpMock!.expectOne(r => r.method === 'GET' && r.url === '/api/accounting/accounts').flush([...ACCOUNTS, makeAccount({ id: 9, name: '新卡' })]);
    fixture.detectChanges();

    const options = Array.from(el.querySelectorAll('.filter-account option')).map(text);
    expect(options).toEqual(['全部帳戶', '玉山 UNI', '富邦 J卡', '新卡']);
  });
});

describe('buildDays', () => {
  const group = (total: string | null, currency = 'TWD') => ({
    id: 4, kind: 'split' as const, name: '旅行', merchant: null, description: null, count: 2, total, currency,
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
