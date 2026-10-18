import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { StatementsService } from './statements.service';

describe('StatementsService', () => {
  let http: HttpTestingController;
  let service: StatementsService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
    service = TestBed.inject(StatementsService);
  });

  afterEach(() => http.verify());

  it('lists and reads a statement', () => {
    let listed = false;
    service.list(6).subscribe((rows) => {
      listed = rows.length === 1 && rows[0].id === 1;
    });
    http.expectOne('/api/accounting/accounts/6/statements').flush([{ id: 1 }]);
    expect(listed).toBe(true);

    let detailId = 0;
    service.get(6, 1).subscribe((detail) => {
      detailId = detail.id;
    });
    const req = http.expectOne('/api/accounting/accounts/6/statements/1');
    expect(req.request.method).toBe('GET');
    req.flush({ id: 1 });
    expect(detailId).toBe(1);
  });

  it('posts reconcile and confirm and bumps changed only after success', () => {
    expect(service.changed()).toBe(0);
    service.reconcile(6, 1).subscribe();
    const reconcile = http.expectOne('/api/accounting/accounts/6/statements/1/reconcile');
    expect(reconcile.request.method).toBe('POST');
    expect(reconcile.request.body).toEqual({});
    expect(service.changed()).toBe(0);
    reconcile.flush({
      claims: 1,
      cases_opened: [],
      explained: 0,
      unmatched_entries: 0,
      skipped: null,
      policy: { applied: 0, proposed: 0 },
    });
    expect(service.changed()).toBe(1);

    service.confirm(6, 1, { force: true, note: '已核對' }).subscribe();
    const confirm = http.expectOne('/api/accounting/accounts/6/statements/1/confirm');
    expect(confirm.request.body).toEqual({ force: true, note: '已核對' });
    confirm.flush({ status: 'reconciled', checks: {}, reopened_cases: [], forced: true });
    expect(service.changed()).toBe(2);
  });

  it('leaves changed alone when confirm is refused', () => {
    let failed = false;
    service.confirm(6, 1, { force: false }).subscribe({ error: () => (failed = true) });
    http
      .expectOne('/api/accounting/accounts/6/statements/1/confirm')
      .flush(
        { message: 'confirm_refused: {"open_cases": false}' },
        { status: 409, statusText: 'Conflict' },
      );
    expect(failed).toBe(true);
    expect(service.changed()).toBe(0);
  });
});
