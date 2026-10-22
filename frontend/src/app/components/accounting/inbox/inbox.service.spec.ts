import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { InboxService } from './inbox.service';

const BASE = '/api/accounting/reconciliation';

describe('InboxService', () => {
  let service: InboxService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    service = TestBed.inject(InboxService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('reads the inbox with its query', () => {
    service.get({ limit: 1 }).subscribe();
    const req = http.expectOne(`${BASE}/inbox?limit=1`);
    expect(req.request.method).toBe('GET');
    req.flush({});
    service.get({ accountId: 4 }).subscribe();
    http.expectOne(`${BASE}/inbox?account_id=4`).flush({});
    service.get().subscribe();
    http.expectOne(`${BASE}/inbox`).flush({});
  });

  it('searches candidates with the day window', () => {
    service.search(9).subscribe();
    const req = http.expectOne(`${BASE}/inbox/cases/9/search?days=14`);
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });

  it('posts apply and bumps changed', () => {
    const before = service.changed();
    const body = { cases: [{ case_id: 1, case_version: 2 }], action: 'defer' as const, params: {}, answer: '等下期' };
    service.apply(body).subscribe();
    const req = http.expectOne(`${BASE}/inbox/apply`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(body);
    req.flush({ results: [], rule_id: null, rule_error: null });
    expect(service.changed()).toBe(before + 1);
  });

  it('applies and rejects proposals, reads and reverts actions', () => {
    service.applyProposal(5).subscribe();
    const apply = http.expectOne(`${BASE}/proposals/5/apply`);
    expect(apply.request.method).toBe('POST');
    apply.flush({});
    service.rejectProposal(5).subscribe();
    http.expectOne(`${BASE}/proposals/5/reject`).flush({});
    service.actions().subscribe();
    const list = http.expectOne(`${BASE}/actions?limit=50`);
    expect(list.request.method).toBe('GET');
    list.flush([]);
    service.revert(7).subscribe();
    const revert = http.expectOne(`${BASE}/actions/7/revert`);
    expect(revert.request.method).toBe('POST');
    revert.flush({});
    expect(service.changed()).toBe(3);
  });

  it('lists, patches and deletes owner rules', () => {
    service.rules().subscribe();
    const list = http.expectOne(`${BASE}/owner-rules`);
    expect(list.request.method).toBe('GET');
    list.flush([]);
    service.patchRule(3, false).subscribe();
    const patch = http.expectOne(`${BASE}/owner-rules/3`);
    expect(patch.request.method).toBe('PATCH');
    expect(patch.request.body).toEqual({ enabled: false });
    patch.flush({});
    service.deleteRule(3).subscribe();
    const del = http.expectOne(`${BASE}/owner-rules/3`);
    expect(del.request.method).toBe('DELETE');
    del.flush(null);
    expect(service.changed()).toBe(2);
  });
});
