import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ImportRun, LedgerAccount } from '../../../models/accounting.model';
import { AccountingAccountsComponent } from './accounts';

const ACCOUNTS: LedgerAccount[] = [
  { id: 3, name: '台新信用卡', currency: 'TWD', opening_balance: '0', balance: '-15230.5000', entry_count: 120 },
  { id: 9, name: '去日本的錢', currency: 'JPY', opening_balance: '180000', balance: '178500.0000', entry_count: 4 },
];

const LATEST: ImportRun = {
  id: 1,
  status: 'succeeded',
  started_at: '2026-10-01T09:00:00+00:00',
  finished_at: '2026-10-01T09:00:02+00:00',
  file_name: 'moze.csv',
  file_sha256: 'a'.repeat(64),
  row_count: 10,
  summary: {
    unpaired_transfers: [
      { row: 5, account: '台新信用卡', kind: 'transfer_out', date: '2026-09-01', time: '12:00', amount: '-500.0000', currency: 'TWD' },
    ],
  },
};

describe('AccountingAccountsComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AccountingAccountsComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    }).compileComponents();
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  function render(accounts: LedgerAccount[], latest: ImportRun | null) {
    const fixture = TestBed.createComponent(AccountingAccountsComponent);
    fixture.detectChanges();
    httpMock.expectOne('/api/accounting/accounts').flush(accounts);
    const latestReq = httpMock.expectOne('/api/accounting/imports/latest');
    if (latest) {
      latestReq.flush(latest);
    } else {
      latestReq.flush({ code: 404, message: 'no import has run yet' }, { status: 404, statusText: 'Not Found' });
    }
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('lists every account with currency, formatted balance and entry count', () => {
    const el = render(ACCOUNTS, LATEST);

    const rows = Array.from(el.querySelectorAll('.account-row'));
    expect(rows.map(r => r.querySelector('.account-name')?.textContent?.trim())).toEqual(['台新信用卡', '去日本的錢']);
    expect(rows[0].querySelector('.account-balance')?.textContent?.trim()).toBe('TWD -15,231');
    expect(rows[1].querySelector('.account-balance')?.textContent?.trim()).toBe('JPY 178,500');
    expect(rows[1].querySelector('.account-meta')?.textContent).toContain('4 筆');
    expect(rows[0].getAttribute('href')).toBe('/accounting/accounts/3');
  });

  it('marks negative balances', () => {
    const el = render(ACCOUNTS, LATEST);

    const balances = el.querySelectorAll('.account-balance');
    expect(balances[0].classList).toContain('amount--negative');
    expect(balances[1].classList).not.toContain('amount--negative');
  });

  it('shows the latest import status and entries needing review', () => {
    const el = render(ACCOUNTS, LATEST);

    const status = el.querySelector('.import-status')?.textContent ?? '';
    expect(status).toContain('成功');
    expect(status).toContain('待確認 1 筆');
  });

  it('shows an import-required empty state before any import', () => {
    const el = render([], null);

    expect(el.querySelector('.empty-state')?.textContent).toContain('尚未匯入 MOZE 資料');
    expect(el.querySelector('.import-status')).toBeNull();
  });
});
