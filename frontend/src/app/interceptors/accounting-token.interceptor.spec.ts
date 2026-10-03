import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, describe, expect, it } from 'vitest';

import { AccountingService } from '../services/accounting.service';
import { ACCOUNTING_API_TOKEN, accountingTokenInterceptor } from './accounting-token.interceptor';

function setup(token: string | null) {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(withInterceptors([accountingTokenInterceptor])),
      provideHttpClientTesting(),
      ...(token === null ? [] : [{ provide: ACCOUNTING_API_TOKEN, useValue: token }]),
    ],
  });
  return {
    http: TestBed.inject(HttpClient),
    service: TestBed.inject(AccountingService),
    httpMock: TestBed.inject(HttpTestingController),
  };
}

describe('accountingTokenInterceptor', () => {
  afterEach(() => TestBed.inject(HttpTestingController).verify());

  it('adds the bearer header to accounting API calls when a token is configured', () => {
    const { service, httpMock } = setup('spa-token');
    service.getAccounts().subscribe();
    const req = httpMock.expectOne(r => r.url === '/api/accounting/accounts');
    expect(req.request.headers.get('Authorization')).toBe('Bearer spa-token');
    req.flush([]);
  });

  it('adds the header to writes and to the bare prefix too', () => {
    const { http, httpMock } = setup('spa-token');
    http.post('/api/accounting/entries', {}).subscribe();
    http.get('/api/accounting').subscribe();
    for (const url of ['/api/accounting/entries', '/api/accounting']) {
      const req = httpMock.expectOne(url);
      expect(req.request.headers.get('Authorization')).toBe('Bearer spa-token');
      req.flush({});
    }
  });

  it('leaves other URLs alone even when a token is configured', () => {
    const { http, httpMock } = setup('spa-token');
    for (const url of ['/api/items', '/api/portfolio/holdings', '/api/accountingx/y', '/other/api/accounting/z']) {
      http.get(url).subscribe();
      const req = httpMock.expectOne(url);
      expect(req.request.headers.has('Authorization')).toBe(false);
      req.flush({});
    }
  });

  it('sends no header when the token is empty', () => {
    const { service, httpMock } = setup('');
    service.getAccounts().subscribe();
    const req = httpMock.expectOne(r => r.url === '/api/accounting/accounts');
    expect(req.request.headers.has('Authorization')).toBe(false);
    req.flush([]);
  });

  it('defaults to the environment token, which is empty unless ACCOUNTING_SPA_TOKEN is set', () => {
    const { service, httpMock } = setup(null);
    const configured = TestBed.inject(ACCOUNTING_API_TOKEN);
    expect(typeof configured).toBe('string');
    service.getAccounts().subscribe();
    const req = httpMock.expectOne(r => r.url === '/api/accounting/accounts');
    expect(req.request.headers.get('Authorization')).toBe(configured ? `Bearer ${configured}` : null);
    req.flush([]);
  });

  it('keeps an Authorization header the caller set itself', () => {
    const { http, httpMock } = setup('spa-token');
    http.get('/api/accounting/accounts', { headers: { Authorization: 'Bearer explicit' } }).subscribe();
    const req = httpMock.expectOne('/api/accounting/accounts');
    expect(req.request.headers.get('Authorization')).toBe('Bearer explicit');
    req.flush([]);
  });
});
