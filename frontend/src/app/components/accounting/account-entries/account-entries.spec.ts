import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting, TestRequest } from '@angular/common/http/testing';
import { signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, ParamMap, RouterLink, convertToParamMap, provideRouter } from '@angular/router';
import { By } from '@angular/platform-browser';
import { BehaviorSubject } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountDetail, AccountPeriodSummary, EntryPage, LedgerEntry } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { AccountingToastService } from '../accounting-toast';
import { MAX_LOGO_FILE_BYTES } from '../logo-upload-errors';
import { AccountingLayoutComponent } from '../accounting-layout/accounting-layout';
import { AccountingAccountEntriesComponent, PAGE_SIZE } from './account-entries';

const ACCOUNT = {
  id: 7, name: '玉山 Only', currency: 'TWD', opening_balance: '0', balance: '-4905.0000', balance_main: '-4905.0000',
  entry_count: 6, is_credit: true, closing_day: 15, icon: '💳', note: null, reward_rules: [],
} as unknown as AccountDetail;

function entry(id: number, fields: Record<string, unknown> = {}): LedgerEntry {
  return {
    id, kind: 'expense', amount: '-880.0000', currency: 'TWD', original_amount: null, original_currency: null,
    fx_rate: null, fx_source: null, entry_date: '2026-10-01', entry_time: '12:30:00', posted_date: '2026-10-01',
    account_id: 7, account_name: '玉山 Only', category: '飲食/午餐', category_id: 11, category_icon: '🍜',
    category_color: '#f0cd92', project: '生活', name: '午餐', merchant: '藏壽司', counterparty: null,
    counterparty_id: null, description: null, tags: [], parent_entry_id: null, transfer_group_id: null, group: null,
    rule_names: [], invoice_number: null, needs_review: false, source: 'manual', moze_id: null, locked: false,
    running_balance: '-1916.0000',
    ...fields,
  } as unknown as LedgerEntry;
}

function page(items: LedgerEntry[], total = items.length, offset = 0): EntryPage {
  return { items, total, limit: PAGE_SIZE, offset };
}

/** `GET /accounts/7/summary` for the 09/16 – 10/15 cycle (Task 4); the header shows only these values. */
const SUMMARY: AccountPeriodSummary = {
  account_id: 7, currency: 'TWD', date_from: '2026-09-16', date_to: '2026-10-15',
  spend: '-3869.0000', income: '0.0000', rewards: '41.0000', net: '-3828.0000', end_balance: '-4905.0000', count: 4,
};

describe('AccountingAccountEntriesComponent (passbook)', () => {
  let http: HttpTestingController;
  let params: BehaviorSubject<ParamMap>;
  /** The wide layout's routed entry (`/accounting/accounts/7/entries/:eid`, `…/group` for a group view). */
  const layout = { selectedEntryId: signal<number | null>(null), groupViewOpen: signal(false), currentPath: signal('/accounting/accounts/7') };

  beforeEach(async () => {
    layout.selectedEntryId.set(null);
    layout.groupViewOpen.set(false);
    layout.currentPath.set('/accounting/accounts/7');
    params = new BehaviorSubject(convertToParamMap({ id: '7' }));
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 2, 10, 0));
    await TestBed.configureTestingModule({
      imports: [AccountingAccountEntriesComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        // Pane components are reused across :id changes (Task 21), so the page reads `paramMap` as an observable.
        { provide: ActivatedRoute, useValue: { paramMap: params } },
        { provide: AccountingLayoutComponent, useValue: layout },
      ],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
    vi.useRealTimers();
  });

  function expectEntries(): TestRequest {
    return http.expectOne(r => r.url === '/api/accounting/accounts/7/entries');
  }

  function expectSummary(from: string, to: string): TestRequest {
    const req = http.expectOne(r => r.url === '/api/accounting/accounts/7/summary');
    expect(req.request.params.get('date_from')).toBe(from);
    expect(req.request.params.get('date_to')).toBe(to);
    return req;
  }

  function render(first: EntryPage, summary: AccountPeriodSummary = SUMMARY): ComponentFixture<AccountingAccountEntriesComponent> {
    const fixture = TestBed.createComponent(AccountingAccountEntriesComponent);
    fixture.detectChanges();
    http.expectOne('/api/accounting/accounts/7').flush(ACCOUNT);
    const req = expectEntries();
    expect(req.request.params.get('date_from')).toBe('2026-09-16');
    expect(req.request.params.get('date_to')).toBe('2026-10-15');
    expect(req.request.params.get('offset')).toBe('0');
    expect(req.request.params.get('limit')).toBe(String(PAGE_SIZE));
    req.flush(first);
    fixture.detectChanges();
    // The period effect runs on change detection and asks for the period totals exactly once.
    expectSummary('2026-09-16', '2026-10-15').flush(summary);
    fixture.detectChanges();
    return fixture;
  }

  function headerValues(el: HTMLElement): (string | undefined)[] {
    return Array.from(el.querySelectorAll('.sum b')).map(b => b.textContent?.trim());
  }

  const PERIOD = [
    entry(1, { name: '電費', merchant: '台灣電力', amount: '-2569.0000', running_balance: '-4905.0000', category_icon: '🏠', category_color: '#425e7f', project: '家居' }),
    entry(2, { kind: 'receivable', name: '代付 晚餐', merchant: null, counterparty: 'Alan', amount: '-420.0000', running_balance: '-2336.0000', project: null }),
    entry(3, { kind: 'reward', name: '紅利回饋 · 一般 1%', amount: '41.0000', running_balance: '-568.0000', project: null }),
    entry(4, { tags: ['LinePay'] }),
  ];

  it('shows the statement period of the closing day and moves between periods', () => {
    const fixture = render(page(PERIOD));
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('.period-label')?.textContent?.trim()).toBe('09/16 – 10/15');
    el.querySelector<HTMLButtonElement>('.period-prev')!.click();
    const req = expectEntries();
    expect(req.request.params.get('date_from')).toBe('2026-08-16');
    expect(req.request.params.get('date_to')).toBe('2026-09-15');
    req.flush(page([]));
    fixture.detectChanges();
    expectSummary('2026-08-16', '2026-09-15').flush({
      ...SUMMARY, date_from: '2026-08-16', date_to: '2026-09-15', spend: '-1200.0000', rewards: '12.0000', end_balance: '-1036.0000', count: 2,
    });
    fixture.detectChanges();
    expect(el.querySelector('.period-label')?.textContent?.trim()).toBe('08/16 – 09/15');
    expect(headerValues(el)).toEqual(['−$1,200', '+$12', '−$1,036']);
  });

  it('keeps a space between the period year and its range', () => {
    const el = render(page(PERIOD)).nativeElement as HTMLElement;
    const text = el.querySelector('.period-year')!.parentElement!.textContent!.replace(/\s+/g, ' ').trim();
    expect(text).toBe('2026 09/16 – 10/15');
  });

  it('summarises new spend, rewards and the period-end balance from the summary endpoint', () => {
    const el = render(page(PERIOD)).nativeElement as HTMLElement;
    expect(Array.from(el.querySelectorAll('.sum small')).map(s => s.textContent?.trim())).toEqual(['新增花費', '紅利回饋', '餘額']);
    expect(headerValues(el)).toEqual(['−$3,869', '+$41', '−$4,905']);
  });

  it('period totals come from the summary endpoint and do not change after loading more', () => {
    const fixture = render(page([entry(1)], 2), {
      ...SUMMARY, spend: '-12000.0000', rewards: '120.0000', end_balance: '-9000.0000', count: 2,
    });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelectorAll('.entry').length).toBe(1);
    expect(headerValues(el)).toEqual(['−$12,000', '+$120', '−$9,000']);

    el.querySelector<HTMLButtonElement>('.load-more')!.click();
    const more = expectEntries();
    expect(more.request.params.get('offset')).toBe('1');
    more.flush(page([entry(2, { amount: '-500.0000', running_balance: '-8500.0000' })], 2, 1));
    fixture.detectChanges();

    expect(el.querySelectorAll('.entry').length).toBe(2);
    expect(headerValues(el)).toEqual(['−$12,000', '+$120', '−$9,000']);
    // One summary request per period: render() consumed it, loading more asks for none.
    http.expectNone(r => r.url === '/api/accounting/accounts/7/summary');
  });

  it('shows the running balance above the amount on every row', () => {
    const el = render(page(PERIOD)).nativeElement as HTMLElement;
    const rows = Array.from(el.querySelectorAll<HTMLElement>('.entry'));

    expect(rows.length).toBe(4);
    for (const row of rows) {
      const amount = row.querySelector('.amt')!;
      const passbook = amount.firstElementChild as HTMLElement;
      expect(passbook.classList).toContain('passbook');
      expect(getComputedStyle(passbook).display).not.toBe('none');
    }
    expect(rows[0].querySelector('.passbook')?.textContent?.trim()).toBe('−$4,905');
    expect(rows[0].querySelector('.amount-value')?.textContent?.trim()).toBe('−$2,569');
  });

  it('marks the row of the entry open in the pane, the first row included', () => {
    const fixture = render(page(PERIOD));
    const el = fixture.nativeElement as HTMLElement;
    const current = () => Array.from(el.querySelectorAll('.entry')).map(row => row.getAttribute('aria-current'));

    expect(current()).toEqual([null, null, null, null]);
    layout.selectedEntryId.set(1);
    fixture.detectChanges();
    expect(current()).toEqual(['true', null, null, null]);
    expect(el.querySelector('.entry')!.classList).toContain('sel');
    layout.selectedEntryId.set(3);
    fixture.detectChanges();
    expect(current()).toEqual([null, null, 'true', null]);
  });

  it('opens the 多類別 group view of a split member, the passbook kept in the URL and noted as its origin', () => {
    const group = { id: 4, kind: 'split' as const, name: null, merchant: null, description: null, count: 2, total: '-300.0000', currency: 'TWD' };
    const installment = { ...group, id: 5, kind: 'installment' as const };
    const fixture = render(page([entry(1, { group }), entry(2, { group: installment }), entry(3)]));
    const el = fixture.nativeElement as HTMLElement;
    const rows = Array.from(el.querySelectorAll<HTMLElement>('.entry'));
    expect(rows.map(row => row.getAttribute('href'))).toEqual([
      '/accounting/accounts/7/entries/1/group',
      '/accounting/accounts/7/entries/2',
      '/accounting/accounts/7/entries/3',
    ]);
    expect(rows.map(row => row.hasAttribute('data-split'))).toEqual([true, false, false]);
    const links = fixture.debugElement.queryAll(By.directive(RouterLink)).map(debug => debug.injector.get(RouterLink));
    expect(links.find(link => link.href === '/accounting/accounts/7/entries/1/group')?.state).toEqual({ groupDepth: 1 });
    expect(links.find(link => link.href === '/accounting/accounts/7/entries/3')?.state).toBeUndefined();

    // A detail open beside the passbook (wide layout): the group view claims no depth, so its ✕ goes to the passbook.
    layout.currentPath.set('/accounting/accounts/7/entries/3');
    fixture.detectChanges();
    expect(links.find(link => link.href === '/accounting/accounts/7/entries/1/group')?.state).toBeUndefined();
  });

  it('marks every row of the split whose group view is routed, and only the routed row on a member detail', () => {
    const group = { id: 4, kind: 'split' as const, name: null, merchant: null, description: null, count: 2, total: '-300.0000', currency: 'TWD' };
    const fixture = render(page([entry(1, { group }), entry(2), entry(3, { group })]));
    const el = fixture.nativeElement as HTMLElement;
    const current = () => Array.from(el.querySelectorAll('.entry')).map(row => row.getAttribute('aria-current'));

    layout.selectedEntryId.set(3);
    layout.groupViewOpen.set(true);
    fixture.detectChanges();
    expect(current()).toEqual(['true', null, 'true']);
    layout.groupViewOpen.set(false);
    fixture.detectChanges();
    expect(current()).toEqual([null, null, 'true']);
  });

  it('renders icon, name, merchant and tags, pills and a link to the detail', () => {
    const el = render(page(PERIOD)).nativeElement as HTMLElement;
    const rows = Array.from(el.querySelectorAll<HTMLElement>('.entry'));

    expect(rows[0].querySelector('.ico')?.textContent?.trim()).toBe('🏠');
    expect(rows[0].querySelector('.sub')?.textContent?.trim()).toBe('台灣電力');
    expect(rows[0].getAttribute('href')).toBe('/accounting/accounts/7/entries/1');
    expect(rows[0].getAttribute('data-entry-id')).toBe('1');
    expect(rows[0].querySelector('.pills')?.textContent).toContain('家居');
    expect(rows[1].querySelector('.sub')?.textContent?.trim()).toBe('Alan');
    expect(rows[1].querySelector('.kind-pill')?.textContent?.trim()).toBe('應收');
    expect(rows[2].querySelector('.kind-pill')?.textContent?.trim()).toBe('回饋');
    expect(rows[3].querySelector('.sub')?.textContent?.trim()).toBe('藏壽司 · #LinePay');
    expect(el.querySelector('.statements-link')?.getAttribute('href')).toBe('/accounting/accounts/7/statements');
    expect(el.querySelector('.statements-link')?.textContent?.trim()).toBe('對帳');
    expect(el.querySelector('.settings-link')?.getAttribute('href')).toBe('/accounting/accounts/7/settings');
  });

  it('shows the original amount and rate inline for a converted entry', () => {
    const el = render(page([
      entry(1, { amount: '-360.0000', original_amount: '-1800.0000', original_currency: 'JPY', fx_rate: '0.2000000000', fx_source: 'fx_api' }),
    ])).nativeElement as HTMLElement;
    expect(el.querySelector('.entry .orig')?.textContent?.trim()).toBe('¥1,800 @ 0.2');
  });

  it('marks entries that need review', () => {
    const el = render(page([entry(1, { kind: 'transfer_out', needs_review: true }), entry(2)])).nativeElement as HTMLElement;
    const rows = el.querySelectorAll('.entry');
    expect(rows[0].classList).toContain('entry--review');
    expect(rows[0].querySelector('.review-marker')?.textContent).toContain('待確認');
    expect(rows[1].querySelector('.review-marker')).toBeNull();
  });

  it('filters by kind and text within the period and by a custom date range', () => {
    const fixture = render(page(PERIOD));
    const el = fixture.nativeElement as HTMLElement;

    const kind = el.querySelector<HTMLSelectElement>('.filter-kind')!;
    kind.value = 'reward';
    kind.dispatchEvent(new Event('change'));
    let req = expectEntries();
    expect(req.request.params.get('kind')).toBe('reward');
    expect(req.request.params.get('date_from')).toBe('2026-09-16');
    req.flush(page([]));

    const q = el.querySelector<HTMLInputElement>('.filter-q')!;
    q.value = '午餐';
    q.dispatchEvent(new Event('change'));
    req = expectEntries();
    expect(req.request.params.get('q')).toBe('午餐');
    expect(req.request.params.get('offset')).toBe('0');
    req.flush(page([]));

    const from = el.querySelector<HTMLInputElement>('.filter-from')!;
    from.value = '2026-01-01';
    from.dispatchEvent(new Event('change'));
    req = expectEntries();
    expect(req.request.params.get('date_from')).toBe('2026-01-01');
    expect(req.request.params.get('date_to')).toBe('2026-10-15');
    req.flush(page([]));
  });

  it('shows the reset filters after switching to another account', () => {
    const fixture = render(page(PERIOD));
    const el = fixture.nativeElement as HTMLElement;
    const kind = el.querySelector<HTMLSelectElement>('.filter-kind')!;
    kind.value = 'reward';
    kind.dispatchEvent(new Event('change'));
    expectEntries().flush(page([]));
    const q = el.querySelector<HTMLInputElement>('.filter-q')!;
    q.value = '午餐';
    q.dispatchEvent(new Event('change'));
    expectEntries().flush(page([]));
    fixture.detectChanges();

    params.next(convertToParamMap({ id: '8' }));
    fixture.detectChanges();
    http.expectOne('/api/accounting/accounts/8').flush({ ...ACCOUNT, id: 8 });
    http.expectOne(r => r.url === '/api/accounting/accounts/8/entries').flush(page([]));
    fixture.detectChanges();
    http.expectOne(r => r.url === '/api/accounting/accounts/8/summary').flush(SUMMARY);
    fixture.detectChanges();

    expect(el.querySelector<HTMLSelectElement>('.filter-kind')!.value).toBe('');
    expect(el.querySelector<HTMLInputElement>('.filter-q')!.value).toBe('');
  });

  it('drops a late period summary when the passbook switches account', () => {
    const fixture = TestBed.createComponent(AccountingAccountEntriesComponent);
    fixture.detectChanges();
    http.expectOne('/api/accounting/accounts/7').flush(ACCOUNT);
    expectEntries().flush(page(PERIOD));
    fixture.detectChanges();
    const lateSummary = expectSummary('2026-09-16', '2026-10-15');
    const el = fixture.nativeElement as HTMLElement;

    params.next(convertToParamMap({ id: '8' }));
    fixture.detectChanges();
    lateSummary.flush(SUMMARY);
    fixture.detectChanges();
    expect(fixture.componentInstance.periodSummary()).toBeNull();
    expect(headerValues(el)).not.toContain('−$4,905');

    http.expectOne('/api/accounting/accounts/8').flush({ ...ACCOUNT, id: 8 });
    http.expectOne(r => r.url === '/api/accounting/accounts/8/entries').flush(page([]));
    fixture.detectChanges();
    http.expectOne(r => r.url === '/api/accounting/accounts/8/summary')
      .flush({ ...SUMMARY, account_id: 8, end_balance: '-100.0000' });
    fixture.detectChanges();
    expect(fixture.componentInstance.periodSummary()?.account_id).toBe(8);
    expect(headerValues(el)).toContain('−$100');
  });

  it('loads more with the next offset and ignores a stale page after a filter change', () => {
    const fixture = render(page([entry(1)], 2));
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('.load-more')!.click();
    const more = expectEntries();
    expect(more.request.params.get('offset')).toBe('1');
    const kind = el.querySelector<HTMLSelectElement>('.filter-kind')!;
    kind.value = 'income';
    kind.dispatchEvent(new Event('change'));
    const filtered = expectEntries();

    more.flush(page([entry(2)], 2, 1));
    filtered.flush(page([entry(30, { kind: 'income', name: '薪水', amount: '50000.0000' })]));
    fixture.detectChanges();

    const rows = el.querySelectorAll('.entry');
    expect(rows.length).toBe(1);
    expect(rows[0].querySelector('.name')?.textContent).toContain('薪水');
  });

  it('asks for the period totals again after an entry write', () => {
    const fixture = render(page(PERIOD));
    const el = fixture.nativeElement as HTMLElement;
    const service = TestBed.inject(AccountingService);

    service.deleteEntry(4).subscribe();
    http.expectOne(r => r.method === 'DELETE' && r.url === '/api/accounting/entries/4').flush(null);
    fixture.detectChanges();
    // The rows reload too (see the next test); this one is about the header.
    expectEntries().flush(page(PERIOD.slice(0, 3)));
    expectSummary('2026-09-16', '2026-10-15').flush({ ...SUMMARY, spend: '-2989.0000', end_balance: '-4025.0000' });
    fixture.detectChanges();

    expect(headerValues(el)).toEqual(['−$2,989', '+$41', '−$4,025']);
  });

  it('reloads the rows of the current period and filters after an entry write, keeping the old rows meanwhile', () => {
    const fixture = render(page(PERIOD));
    const el = fixture.nativeElement as HTMLElement;
    const kind = el.querySelector<HTMLSelectElement>('.filter-kind')!;
    kind.value = 'expense';
    kind.dispatchEvent(new Event('change'));
    expectEntries().flush(page([entry(1), entry(4)]));
    fixture.detectChanges();

    const service = TestBed.inject(AccountingService);
    service.deleteEntry(99).subscribe();
    http.expectOne(r => r.method === 'DELETE' && r.url === '/api/accounting/entries/99').flush(null);
    fixture.detectChanges();

    const req = expectEntries();
    expect(req.request.params.get('offset')).toBe('0');
    expect(req.request.params.get('kind')).toBe('expense');
    expect(req.request.params.get('date_from')).toBe('2026-09-16');
    expect(req.request.params.get('date_to')).toBe('2026-10-15');
    expectSummary('2026-09-16', '2026-10-15').flush(SUMMARY);
    fixture.detectChanges();
    expect(el.querySelectorAll('.entry').length).toBe(2);

    req.flush(page([entry(50, { name: '新的一筆' }), entry(1), entry(4)]));
    fixture.detectChanges();
    const rows = el.querySelectorAll('.entry');
    expect(rows.length).toBe(3);
    expect(rows[0].getAttribute('data-entry-id')).toBe('50');
  });

  it('clears the custom date inputs when moving to another period', () => {
    const fixture = render(page(PERIOD));
    const el = fixture.nativeElement as HTMLElement;
    const from = el.querySelector<HTMLInputElement>('.filter-from')!;
    const to = el.querySelector<HTMLInputElement>('.filter-to')!;
    from.value = '2026-01-01';
    from.dispatchEvent(new Event('change'));
    expectEntries().flush(page([]));
    to.value = '2026-02-01';
    to.dispatchEvent(new Event('change'));
    expectEntries().flush(page([]));
    fixture.detectChanges();

    el.querySelector<HTMLButtonElement>('.period-next')!.click();
    expectEntries().flush(page([]));
    fixture.detectChanges();
    expectSummary('2026-10-16', '2026-11-15').flush(SUMMARY);
    fixture.detectChanges();

    expect(from.value).toBe('');
    expect(to.value).toBe('');
  });

  it('renders the four filter controls with aria-labels and keeps their bindings', () => {
    const fixture = render(page(PERIOD));
    const el = fixture.nativeElement as HTMLElement;
    const labels = Array.from(el.querySelectorAll('.entries-filters select, .entries-filters input')).map(c => c.getAttribute('aria-label'));
    expect(labels).toEqual(['類型', '搜尋', '起始日期', '結束日期']);
    expect(el.querySelector('.entries-filters label')).toBeNull();

    const kind = el.querySelector<HTMLSelectElement>('select[aria-label="類型"]')!;
    kind.value = 'reward';
    kind.dispatchEvent(new Event('change'));
    const req = expectEntries();
    expect(req.request.params.get('kind')).toBe('reward');
    req.flush(page([]));
    fixture.detectChanges();

    const from = el.querySelector<HTMLInputElement>('.filter-range .filter-from')!;
    const to = el.querySelector<HTMLInputElement>('.filter-range .filter-to')!;
    from.value = '2026-01-01';
    from.dispatchEvent(new Event('change'));
    expectEntries().flush(page([]));
    to.value = '2026-02-01';
    to.dispatchEvent(new Event('change'));
    expectEntries().flush(page([]));
    fixture.detectChanges();
    el.querySelector<HTMLButtonElement>('.period-next')!.click();
    expectEntries().flush(page([]));
    fixture.detectChanges();
    expectSummary('2026-10-16', '2026-11-15').flush(SUMMARY);
    fixture.detectChanges();
    expect(from.value).toBe('');
    expect(to.value).toBe('');
  });

  it('shows the schedule pill on a scheduled row', () => {
    const scheduled = entry(9, {
      schedule: { definition_id: 3, instance_id: 4, kind: 'recurring', seq: 25, times: null, name: 'Netflix', is_partial: false,
        acted_by: 'auto', posted_entry_ids: [9] },
    });
    const fixture = render({ items: [scheduled], total: 1, limit: PAGE_SIZE, offset: 0 });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.schedule-pill')?.textContent?.trim()).toBe('週期 #25');
  });
  it('shows the skeleton, then 記錄讀取失敗 with 重試 instead of rows and empty text, and retries page 1', () => {
    const fixture = TestBed.createComponent(AccountingAccountEntriesComponent);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('app-skeleton')).not.toBeNull();
    expect(el.querySelector('.lbl')?.textContent?.trim()).toBe('本期記錄');

    http.expectOne('/api/accounting/accounts/7').flush(ACCOUNT);
    fixture.detectChanges();
    expectSummary('2026-09-16', '2026-10-15').flush(SUMMARY);
    expectEntries().flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    expect(el.querySelector('.load-error')?.textContent?.trim()).toBe('記錄讀取失敗');
    expect(el.querySelector('.entries-empty')).toBeNull();
    expect(el.querySelector('app-skeleton')).toBeNull();
    expect(el.querySelector('.lbl')?.textContent?.trim()).toBe('本期記錄');

    el.querySelector<HTMLButtonElement>('.entries-retry')!.click();
    fixture.detectChanges();
    expectEntries().flush(page(PERIOD));
    fixture.detectChanges();
    expect(el.querySelector('.load-error')).toBeNull();
    expect(el.querySelectorAll('.entry').length).toBe(4);
    expect(el.querySelector('.lbl')?.textContent?.trim()).toBe('本期記錄 (4)');
  });

  it('retries the account itself when it failed to load', () => {
    const fixture = TestBed.createComponent(AccountingAccountEntriesComponent);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    http.expectOne('/api/accounting/accounts/7').flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();
    expect(el.querySelector('.load-error')).not.toBeNull();

    el.querySelector<HTMLButtonElement>('.entries-retry')!.click();
    fixture.detectChanges();
    http.expectOne('/api/accounting/accounts/7').flush(ACCOUNT);
    expectEntries().flush(page([]));
    fixture.detectChanges();
    expectSummary('2026-09-16', '2026-10-15').flush(SUMMARY);
    fixture.detectChanges();
    expect(el.querySelector('.entries-empty')?.textContent?.trim()).toBe('沒有符合條件的記錄');
  });

  describe('avatar logo upload', () => {
    const URL = '/api/accounting/accounts/7/logo';

    function setup(): { fixture: ComponentFixture<AccountingAccountEntriesComponent>; el: HTMLElement; input: HTMLInputElement; show: ReturnType<typeof vi.spyOn> } {
      const fixture = render(page([]));
      const el = fixture.nativeElement as HTMLElement;
      const show = vi.spyOn(TestBed.inject(AccountingToastService), 'show');
      return { fixture, el, input: el.querySelector('input[type=file]') as HTMLInputElement, show };
    }

    function choose(input: HTMLInputElement, file: File): void {
      Object.defineProperty(input, 'files', { configurable: true, value: [file] });
      input.dispatchEvent(new Event('change'));
    }

    it('wraps the big icon in a button that opens the hidden image input', () => {
      const { el, input } = setup();
      const button = el.querySelector('button.avatar[aria-label="更換 logo"]') as HTMLButtonElement;
      expect(button.querySelector('app-account-icon.big')).not.toBeNull();
      expect(input.getAttribute('accept')).toBe('image/png,image/jpeg,image/webp');
      const click = vi.spyOn(input, 'click');
      button.click();
      expect(click).toHaveBeenCalledTimes(1);
    });

    it('PUTs the chosen file, is busy while pending, then replaces the account', () => {
      const { fixture, el, input } = setup();
      const button = el.querySelector('button.avatar') as HTMLButtonElement;
      const file = new File([new Uint8Array(1)], 'a.png', { type: 'image/png' });
      choose(input, file);
      fixture.detectChanges();
      expect(button.getAttribute('aria-busy')).toBe('true');
      expect(input.value).toBe('');
      const req = http.expectOne(URL);
      expect(req.request.method).toBe('PUT');
      expect(req.request.body).toBe(file);
      expect(req.request.headers.get('Content-Type')).toBe('image/png');
      req.flush({ ...ACCOUNT, logo_hash: 'abc' });
      fixture.detectChanges();
      expect(fixture.componentInstance.account()?.logo_hash).toBe('abc');
      // The icon now fetches the uploaded image through the logo store.
      http.expectOne('/api/accounting/accounts/7/logo.png?v=abc').flush(new Blob([new Uint8Array(1)], { type: 'image/png' }));
      expect(button.hasAttribute('aria-busy')).toBe(false);
    });

    it('ignores a response for a different account but clears busy', () => {
      const { fixture, el, input } = setup();
      choose(input, new File([new Uint8Array(1)], 'a.png', { type: 'image/png' }));
      http.expectOne(URL).flush({ ...ACCOUNT, id: 8, logo_hash: 'zzz' });
      fixture.detectChanges();
      expect(fixture.componentInstance.account()).toEqual(ACCOUNT);
      expect((el.querySelector('button.avatar') as HTMLElement).hasAttribute('aria-busy')).toBe(false);
    });

    it('does not open the picker while an upload is pending', () => {
      const { fixture, el, input } = setup();
      choose(input, new File([new Uint8Array(1)], 'a.png', { type: 'image/png' }));
      fixture.detectChanges();
      const click = vi.spyOn(input, 'click');
      (el.querySelector('button.avatar') as HTMLButtonElement).click();
      fixture.componentInstance.pickLogoFile();
      expect(click).not.toHaveBeenCalled();
      http.expectOne(URL).flush({ ...ACCOUNT, logo_hash: null });
    });

    it('toasts the mapped message on a 422 logo_format and clears the busy state', () => {
      const { fixture, el, input, show } = setup();
      choose(input, new File([new Uint8Array(1)], 'a.png', { type: 'image/png' }));
      http.expectOne(URL).flush({ message: 'logo_format' }, { status: 422, statusText: 'Unprocessable Entity' });
      fixture.detectChanges();
      expect(show).toHaveBeenCalledWith('只接受 PNG、JPEG、WebP');
      expect((el.querySelector('button.avatar') as HTMLElement).hasAttribute('aria-busy')).toBe(false);
      expect(input.value).toBe('');
    });

    it('rejects an oversized file without a request', () => {
      const { input, show } = setup();
      choose(input, new File([new Uint8Array(MAX_LOGO_FILE_BYTES + 1)], 'big.png', { type: 'image/png' }));
      http.expectNone(URL);
      expect(show).toHaveBeenCalledWith('圖片太大（上限 5 MB）');
      expect(input.value).toBe('');
    });
  });


  it('folds a reward batch into one row with the balance after it, and expands its members in place (#304)', () => {
    const batch = {
      rule_id: 301, rule_name: '測試規則', window_start: '2026-09-16', window_end: '2026-10-15', entry_date: '2026-10-01',
      count: 3, total: '10.0000', currency: 'TWD',
    };
    const reward = (id: number, amount: string, running: string) =>
      entry(id, { kind: 'reward', name: `回饋 ${id}`, amount, running_balance: running, reward_batch: batch });
    const fixture = render(page([
      reward(1, '2.0000', '30.0000'), reward(2, '3.0000', '28.0000'), reward(3, '5.0000', '25.0000'),
      entry(4, { entry_date: '2026-09-30', amount: '-80.0000', running_balance: '20.0000' }),
    ]));
    const el = fixture.nativeElement as HTMLElement;

    const head = el.querySelector<HTMLButtonElement>('.entry-batch')!;
    expect(head.getAttribute('aria-expanded')).toBe('false');
    expect(head.querySelector('.name')?.textContent?.trim()).toBe('測試規則');
    expect(head.querySelector('.sub')?.textContent?.trim()).toBe('3 筆 · 9/16–10/15');
    expect(head.querySelector('.passbook')?.textContent?.trim()).toBe('$30');
    expect(head.querySelector('.amount-value')?.textContent?.trim()).toBe('+$10');
    expect(Array.from(el.querySelectorAll('.entry')).map(row => row.getAttribute('data-entry-id'))).toEqual(['4']);

    head.click();
    fixture.detectChanges();
    expect(head.getAttribute('aria-expanded')).toBe('true');
    const members = Array.from(el.querySelectorAll<HTMLElement>('.entry.batch-member'));
    expect(members.map(row => [row.querySelector('.name')?.textContent?.trim(), row.querySelector('.passbook')?.textContent?.trim()]))
      .toEqual([['回饋 1', '$30'], ['回饋 2', '$28'], ['回饋 3', '$25']]);
    head.click();
    fixture.detectChanges();
    expect(el.querySelectorAll('.entry.batch-member').length).toBe(0);

    // A search may keep only some of a batch's rows: the passbook lists them unfolded then.
    fixture.componentInstance.q.set('回饋');
    fixture.detectChanges();
    expect(el.querySelector('.entry-batch')).toBeNull();
    expect(el.querySelectorAll('.entry').length).toBe(4);
  });
});
