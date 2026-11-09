import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { BreakdownResponse, BreakdownRow, BreakdownSplit } from '../../../models/reports.model';
import { AccountingService } from '../../../services/accounting.service';
import { InboxService } from '../inbox/inbox.service';
import { makeInbox } from '../inbox/testing';
import { CalendarDataService, EMPTY_CALENDAR_DATA } from './calendar-data.service';

const BREAKDOWN = '/api/accounting/reports/breakdown';
const INBOX = '/api/accounting/reconciliation/inbox';

function split(key: string, amount: string): BreakdownSplit {
  return { key, label: `cat ${key}`, amount, count: 1 };
}

function row(key: string | null, splits?: BreakdownSplit[]): BreakdownRow {
  return {
    key,
    label: key ?? '無',
    color: null,
    amount: '100',
    refund: '0',
    count: 1,
    avg: '100',
    median: null,
    min: null,
    max: null,
    first_date: null,
    last_date: null,
    avg_interval_days: null,
    splits,
  };
}

function body(rows: BreakdownRow[]): BreakdownResponse {
  return {
    currency: 'TWD',
    date_from: '2026-10-01',
    date_to: '2026-10-31',
    totals: { expense: '0', income: '0', net: '0', count: 0, refund: '0' },
    rows,
    overlapping: false,
    missing_rates: [],
  };
}

describe('CalendarDataService', () => {
  let service: CalendarDataService;
  let httpMock: HttpTestingController;

  const breakdown = (groupBy: string) =>
    httpMock.expectOne((r) => r.url === BREAKDOWN && r.params.get('group_by') === groupBy);
  const noReads = () => {
    httpMock.expectNone((r) => r.url === BREAKDOWN);
    httpMock.expectNone(INBOX);
  };
  const settle = () => TestBed.tick();

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(CalendarDataService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  function loadMonth(month: string): void {
    service.month.set(month);
    settle();
    breakdown('day').flush(body([]));
    breakdown('merchant').flush(body([]));
    httpMock.expectOne(INBOX).flush(makeInbox([], {}));
  }

  it('is idle without a month', () => {
    settle();
    noReads();
    expect(service.data()).toBe(EMPTY_CALENDAR_DATA);
  });

  it('reads dots, merchants and invoice days for the month with the spec query', () => {
    service.month.set('2026-10');
    settle();
    const day = breakdown('day');
    expect(day.request.params.get('split_by')).toBe('category');
    expect(day.request.params.get('metric')).toBe('expense');
    expect(day.request.params.get('include_excluded_projects')).toBe('true');
    expect(day.request.params.get('date_from')).toBe('2026-10-01');
    expect(day.request.params.get('date_to')).toBe('2026-10-31');
    const merchant = breakdown('merchant');
    expect(merchant.request.params.get('date_from')).toBe('2026-10-01');
    expect(merchant.request.params.get('date_to')).toBe('2026-10-31');
    const inbox = httpMock.expectOne(INBOX);
    expect(inbox.request.params.has('limit')).toBe(false);

    day.flush(
      body([
        row(
          '2026-10-21',
          [split('3', '100'), split('7', '2996'), split('', '1'), split('9', '-50')].map((s) =>
            s.key === '' ? { ...s, key: null } : s,
          ),
        ),
      ]),
    );
    merchant.flush(body([row(null), ...['全聯', 'a', 'b', 'c', 'd', 'e', 'f'].map((k) => row(k))]));
    inbox.flush({
      ...makeInbox([], {}),
      einvoices: [
        { issue_date: '2026-10-22' },
        { issue_date: '2026-10-22' },
        { issue_date: '2026-09-30' },
      ] as never,
    });

    const data = service.data();
    expect(data.dotsState).toBe('ready');
    expect(data.dots.get('2026-10-21')?.map((s) => s.key)).toEqual(['7', '3']);
    expect(data.merchantsState).toBe('ready');
    expect(data.merchants.length).toBe(5);
    expect(data.merchants.every((m) => m.key !== null)).toBe(true);
    expect(data.invoiceState).toBe('ready');
    expect(data.invoiceDays.get('2026-10-22')).toBe(2);
    expect(data.invoiceDays.has('2026-09-30')).toBe(false);
  });

  it('leaves the other parts when one read fails', () => {
    service.month.set('2026-10');
    settle();
    breakdown('day').flush('boom', { status: 500, statusText: 'Server Error' });
    breakdown('merchant').flush(body([row('全聯')]));
    httpMock.expectOne(INBOX).flush(makeInbox([], {}));
    const data = service.data();
    expect(data.dotsState).toBe('error');
    expect(data.dots.size).toBe(0);
    expect(data.merchantsState).toBe('ready');
    expect(data.invoiceState).toBe('ready');
  });

  it('caches a month and re-reads it after entriesChanged', () => {
    loadMonth('2026-10');
    loadMonth('2026-11');
    service.month.set('2026-10');
    settle();
    noReads();
    expect(service.data().dotsState).toBe('ready');

    TestBed.inject(AccountingService).markEntriesChanged();
    settle();
    breakdown('day');
    breakdown('merchant');
    httpMock.expectOne(INBOX);
  });

  it('re-reads only the invoices on InboxService.changed', () => {
    loadMonth('2026-10');
    TestBed.inject(InboxService).applyProposal(1).subscribe();
    httpMock.expectOne('/api/accounting/reconciliation/proposals/1/apply').flush({});
    settle();
    expect(service.data().invoiceState).toBe('loading');
    expect(service.data().dotsState).toBe('ready');
    httpMock.expectNone((r) => r.url === BREAKDOWN);
    httpMock.expectOne(INBOX).flush(makeInbox([], {}));
    expect(service.data().invoiceState).toBe('ready');
  });

  it('drops a late answer for a month no longer shown', () => {
    service.month.set('2026-10');
    settle();
    const octDay = breakdown('day');
    const octMerchant = breakdown('merchant');
    const octInbox = httpMock.expectOne(INBOX);
    service.month.set('2026-11');
    settle();
    const novDay = breakdown('day');
    const novMerchant = breakdown('merchant');
    const novInbox = httpMock.expectOne(INBOX);
    octDay.flush(body([row('2026-10-21', [split('7', '5')])]));
    octMerchant.flush(body([row('全聯')]));
    octInbox.flush(makeInbox([], {}));
    const data = service.data();
    expect(data.dots.size).toBe(0);
    expect(data.dotsState).toBe('loading');
    expect(data.merchants.length).toBe(0);
    expect(data.invoiceState).toBe('loading');
    novDay.flush(body([]));
    novMerchant.flush(body([]));
    novInbox.flush(makeInbox([], {}));
    // the answers still land in October's own cache record
    service.month.set('2026-10');
    settle();
    noReads();
    expect(service.data().dots.get('2026-10-21')?.length).toBe(1);
  });

  it('loads a new month whose selection coalesces with an inbox change', () => {
    service.month.set('2026-10');
    TestBed.inject(InboxService).applyProposal(1).subscribe();
    httpMock.expectOne('/api/accounting/reconciliation/proposals/1/apply').flush({});
    settle();
    breakdown('day');
    breakdown('merchant');
    httpMock.expectOne(INBOX);
  });

  it('keeps other months dots and merchants on InboxService.changed and re-reads their invoices when shown', () => {
    loadMonth('2026-10');
    loadMonth('2026-11');
    TestBed.inject(InboxService).applyProposal(1).subscribe();
    httpMock.expectOne('/api/accounting/reconciliation/proposals/1/apply').flush({});
    settle();
    httpMock.expectOne(INBOX).flush(makeInbox([], {}));
    service.month.set('2026-10');
    settle();
    expect(service.data().dotsState).toBe('ready');
    expect(service.data().invoiceState).toBe('loading');
    httpMock.expectNone((r) => r.url === BREAKDOWN);
    httpMock.expectOne(INBOX).flush(makeInbox([], {}));
    expect(service.data().invoiceState).toBe('ready');
  });
});
