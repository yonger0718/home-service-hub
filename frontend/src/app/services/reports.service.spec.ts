import { HttpErrorResponse, provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ReportsService } from './reports.service';

const QUERY = { from: '2026-10-01', to: '2026-10-31' } as never;
const MISSING = { detail: 'Not Found' };
const STALE = { code: 'not_found', message: 'report not found', trace_id: 't' };

describe('ReportsService 404 handling', () => {
  let service: ReportsService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    service = TestBed.inject(ReportsService);
    http = TestBed.inject(HttpTestingController);
  });
  afterEach(() => http.verify());

  it('falls back to sample data when the route is missing (FastAPI detail body)', () => {
    let result: unknown;
    service.getBreakdown(QUERY).subscribe((r) => (result = r));
    http.expectOne((r) => r.url === '/api/accounting/reports/breakdown').flush(MISSING, { status: 404, statusText: 'Not Found' });
    expect(result).toBeTruthy();
    expect(service.sample()).toBe(true);
  });

  it('falls back when the 404 body is empty', () => {
    let result: unknown;
    service.getBreakdown(QUERY).subscribe((r) => (result = r));
    http.expectOne((r) => r.url === '/api/accounting/reports/breakdown').flush(null, { status: 404, statusText: 'Not Found' });
    expect(result).toBeTruthy();
    expect(service.sample()).toBe(true);
  });

  it('rethrows an app-envelope 404 and stays out of sample mode', () => {
    let error: unknown;
    service.getBreakdown(QUERY).subscribe({ error: (e) => (error = e) });
    http.expectOne((r) => r.url === '/api/accounting/reports/breakdown').flush(STALE, { status: 404, statusText: 'Not Found' });
    expect(error).toBeInstanceOf(HttpErrorResponse);
    expect((error as HttpErrorResponse).status).toBe(404);
    expect(service.sample()).toBe(false);
  });

  it('never falls back for a request addressed to an id', () => {
    const errors: unknown[] = [];
    service.deleteBudget(3).subscribe({ error: (e) => errors.push(e) });
    http.expectOne('/api/accounting/reports/budgets/3').flush(MISSING, { status: 404, statusText: 'Not Found' });
    service.updateBudget(3, { amount: '1', effective_from: '2026-10' } as never).subscribe({ error: (e) => errors.push(e) });
    http.expectOne('/api/accounting/reports/budgets/3').flush(MISSING, { status: 404, statusText: 'Not Found' });
    service.getSaved(4).subscribe({ error: (e) => errors.push(e) });
    http.expectOne('/api/accounting/reports/saved/4').flush(MISSING, { status: 404, statusText: 'Not Found' });
    service.updateSaved(4, {} as never).subscribe({ error: (e) => errors.push(e) });
    http.expectOne('/api/accounting/reports/saved/4').flush(MISSING, { status: 404, statusText: 'Not Found' });
    service.deleteSaved(4).subscribe({ error: (e) => errors.push(e) });
    http.expectOne('/api/accounting/reports/saved/4').flush(MISSING, { status: 404, statusText: 'Not Found' });
    expect(errors).toHaveLength(5);
    expect(service.sample()).toBe(false);
  });

  it('propagates a non-404 error', () => {
    let error: unknown;
    service.getBreakdown(QUERY).subscribe({ error: (e) => (error = e) });
    http.expectOne((r) => r.url === '/api/accounting/reports/breakdown').flush(STALE, { status: 500, statusText: 'Server Error' });
    expect((error as HttpErrorResponse).status).toBe(500);
    expect(service.sample()).toBe(false);
  });
});
