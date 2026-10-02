import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting, TestRequest } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { EntryPage, LedgerEntry } from '../../../models/accounting.model';
import { makeEntry } from '../testing/fixtures';
import { AccountingAccountEntriesComponent } from './account-entries';

function entry(id: number, overrides: Partial<LedgerEntry> = {}): LedgerEntry {
  return makeEntry({
    id,
    entry_date: '2026-09-01',
    posted_date: '2026-09-01',
    entry_time: '12:30:00',
    category: '飲食/午餐',
    project: '日本行',
    name: '便當',
    merchant: '池上',
    running_balance: '1880.0000',
    ...overrides,
  });
}

describe('AccountingAccountEntriesComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AccountingAccountEntriesComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { snapshot: { paramMap: convertToParamMap({ id: '7' }) } } },
      ],
    }).compileComponents();
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  function expectEntries(): TestRequest {
    return httpMock.expectOne(r => r.url === '/api/accounting/accounts/7/entries');
  }

  function page(items: LedgerEntry[], total: number, offset = 0): EntryPage {
    return { items, total, limit: 50, offset };
  }

  function render(first: EntryPage) {
    const fixture = TestBed.createComponent(AccountingAccountEntriesComponent);
    fixture.detectChanges();
    httpMock.expectOne('/api/accounting/accounts').flush([
      { id: 7, name: '錢包', currency: 'TWD', opening_balance: '2000', balance: '1880.0000', entry_count: 1 },
    ]);
    const req = expectEntries();
    expect(req.request.params.get('offset')).toBe('0');
    expect(req.request.params.get('limit')).toBe('50');
    req.flush(first);
    fixture.detectChanges();
    return fixture;
  }

  it('shows each entry with date, kind, category, name, project, amount and running balance', () => {
    const fixture = render(page([entry(1)], 1));
    const row = (fixture.nativeElement as HTMLElement).querySelector('.entry')!;

    expect(row.querySelector('.entry-date')?.textContent).toContain('2026-09-01 12:30');
    expect(row.querySelector('.entry-kind')?.textContent?.trim()).toBe('支出');
    expect(row.querySelector('.entry-category')?.textContent?.trim()).toBe('飲食/午餐');
    expect(row.querySelector('.entry-name')?.textContent?.trim()).toBe('便當 · 池上');
    expect(row.querySelector('.entry-project')?.textContent?.trim()).toBe('日本行');
    expect(row.querySelector('.entry-amount')?.textContent?.trim()).toBe('TWD -120');
    expect(row.querySelector('.entry-running')?.textContent?.trim()).toBe('TWD 1,880');
    expect((fixture.nativeElement as HTMLElement).querySelector('h2')?.textContent).toContain('錢包');
  });

  it('loads more entries with the next offset', () => {
    const fixture = render(page([entry(1)], 2));
    const button = (fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>('.load-more')!;

    button.click();
    const req = expectEntries();
    expect(req.request.params.get('offset')).toBe('1');
    req.flush(page([entry(2)], 2, 1));
    fixture.detectChanges();

    const rows = (fixture.nativeElement as HTMLElement).querySelectorAll('.entry');
    expect(rows.length).toBe(2);
    expect((fixture.nativeElement as HTMLElement).querySelector('.load-more')).toBeNull();
  });

  it('reloads from offset 0 when the kind or date filter changes', () => {
    const fixture = render(page([entry(1)], 1));
    const el = fixture.nativeElement as HTMLElement;

    const kind = el.querySelector<HTMLSelectElement>('.filter-kind')!;
    kind.value = 'transfer_out';
    kind.dispatchEvent(new Event('change'));
    let req = expectEntries();
    expect(req.request.params.get('kind')).toBe('transfer_out');
    expect(req.request.params.get('offset')).toBe('0');
    req.flush(page([], 0));

    const from = el.querySelector<HTMLInputElement>('.filter-from')!;
    from.value = '2026-09-01';
    from.dispatchEvent(new Event('change'));
    req = expectEntries();
    expect(req.request.params.get('date_from')).toBe('2026-09-01');
    expect(req.request.params.get('kind')).toBe('transfer_out');
    req.flush(page([], 0));

    const to = el.querySelector<HTMLInputElement>('.filter-to')!;
    to.value = '2026-09-30';
    to.dispatchEvent(new Event('change'));
    req = expectEntries();
    expect(req.request.params.get('date_to')).toBe('2026-09-30');
    req.flush(page([], 0));
  });

  it('shows only the latest filter results when responses arrive out of order', () => {
    const fixture = render(page([entry(1)], 1));
    const el = fixture.nativeElement as HTMLElement;
    const kind = el.querySelector<HTMLSelectElement>('.filter-kind')!;

    kind.value = 'transfer_out';
    kind.dispatchEvent(new Event('change'));
    kind.value = 'income';
    kind.dispatchEvent(new Event('change'));
    const [first, second] = httpMock.match(r => r.url === '/api/accounting/accounts/7/entries');
    expect(first.request.params.get('kind')).toBe('transfer_out');
    expect(second.request.params.get('kind')).toBe('income');

    second.flush(page([entry(20, { kind: 'income', name: '薪水' })], 1));
    first.flush(page([entry(10, { kind: 'transfer_out', name: '轉帳' })], 1));
    fixture.detectChanges();

    const rows = el.querySelectorAll('.entry');
    expect(rows.length).toBe(1);
    expect(rows[0].querySelector('.entry-name')?.textContent).toContain('薪水');
  });

  it('does not append a stale load-more page after the filter changes', () => {
    const fixture = render(page([entry(1)], 2));
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('.load-more')!.click();
    const more = expectEntries();
    expect(more.request.params.get('offset')).toBe('1');

    const kind = el.querySelector<HTMLSelectElement>('.filter-kind')!;
    kind.value = 'income';
    kind.dispatchEvent(new Event('change'));
    const filtered = expectEntries();
    expect(filtered.request.params.get('offset')).toBe('0');

    more.flush(page([entry(2)], 2, 1));
    filtered.flush(page([entry(30, { kind: 'income', name: '薪水' })], 1));
    fixture.detectChanges();

    const rows = el.querySelectorAll('.entry');
    expect(rows.length).toBe(1);
    expect(rows[0].querySelector('.entry-name')?.textContent).toContain('薪水');
  });

  it('clears the previous rows when a filter change fails to load', () => {
    const fixture = render(page([entry(1)], 1));
    const el = fixture.nativeElement as HTMLElement;
    const kind = el.querySelector<HTMLSelectElement>('.filter-kind')!;

    kind.value = 'income';
    kind.dispatchEvent(new Event('change'));
    expectEntries().flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    expect(el.querySelectorAll('.entry').length).toBe(0);
  });

  it('marks entries that need review', () => {
    const fixture = render(page([entry(1, { kind: 'transfer_out', needs_review: true }), entry(2)], 2));
    const rows = (fixture.nativeElement as HTMLElement).querySelectorAll('.entry');

    expect(rows[0].classList).toContain('entry--review');
    expect(rows[0].querySelector('.review-marker')?.textContent).toContain('待確認');
    expect(rows[1].querySelector('.review-marker')).toBeNull();
  });

  it('shows an untimed entry with its date only', () => {
    const fixture = render(page([entry(1, { entry_time: null })], 1));
    const date = (fixture.nativeElement as HTMLElement).querySelector('.entry-date')?.textContent?.trim();
    expect(date).toBe('2026-09-01');
  });

  it('shows the original amount and rate of a converted entry as a tooltip', () => {
    const fixture = render(
      page([
        entry(1, { amount: '-360.0000', original_amount: '-1800.0000', original_currency: 'JPY', fx_rate: '0.2000000000', fx_source: 'fx_api' }),
        entry(2),
      ], 2),
    );
    const amounts = (fixture.nativeElement as HTMLElement).querySelectorAll('.entry-amount');

    expect(amounts[0].getAttribute('title')).toBe('原幣 JPY -1,800 · 匯率 0.2');
    expect(amounts[1].hasAttribute('title')).toBe(false);
  });
});
