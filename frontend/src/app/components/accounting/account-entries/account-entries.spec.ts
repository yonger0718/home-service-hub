import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting, TestRequest } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { EntryPage, LedgerEntry } from '../../../models/accounting.model';
import { AccountingAccountEntriesComponent } from './account-entries';

function entry(id: number, overrides: Partial<LedgerEntry> = {}): LedgerEntry {
  return {
    id,
    kind: 'expense',
    amount: '-120.0000',
    currency: 'TWD',
    original_amount: null,
    original_currency: null,
    fx_rate: null,
    fx_source: null,
    entry_date: '2026-09-01',
    entry_time: '12:30:00',
    category: '飲食/午餐',
    project: '日本行',
    name: '便當',
    merchant: '池上',
    counterparty: null,
    description: null,
    tags: [],
    parent_entry_id: null,
    transfer_group_id: null,
    needs_review: false,
    running_balance: '1880.0000',
    ...overrides,
  };
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
