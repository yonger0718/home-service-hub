import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ImportRun } from '../models/accounting.model';
import { AccountingService } from './accounting.service';

describe('AccountingService', () => {
  let service: AccountingService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(AccountingService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('lists accounts', () => {
    let names: string[] = [];
    service.getAccounts().subscribe(accounts => (names = accounts.map(a => a.name)));

    httpMock.expectOne('/api/accounting/accounts').flush([
      { id: 1, name: '錢包', currency: 'TWD', opening_balance: '0', balance: '10', entry_count: 1 },
    ]);

    expect(names).toEqual(['錢包']);
  });

  it('sends only the set entry query parameters', () => {
    service.getEntries(7, { limit: 50, offset: 100, kind: 'expense', date_from: null }).subscribe();

    const req = httpMock.expectOne(r => r.url === '/api/accounting/accounts/7/entries');
    expect(req.request.params.keys().sort()).toEqual(['kind', 'limit', 'offset']);
    expect(req.request.params.get('offset')).toBe('100');
    req.flush({ items: [], total: 0, limit: 50, offset: 100 });
  });

  it('maps a 404 from imports/latest to null', () => {
    let latest: ImportRun | null | undefined;
    service.getLatestImport().subscribe(run => (latest = run));

    httpMock
      .expectOne('/api/accounting/imports/latest')
      .flush({ code: 404, message: 'no import has run yet' }, { status: 404, statusText: 'Not Found' });

    expect(latest).toBeNull();
  });

  it('propagates other errors from imports/latest', () => {
    let failed = false;
    service.getLatestImport().subscribe({ error: () => (failed = true) });

    httpMock.expectOne('/api/accounting/imports/latest').flush('boom', { status: 500, statusText: 'Error' });

    expect(failed).toBe(true);
  });
});
