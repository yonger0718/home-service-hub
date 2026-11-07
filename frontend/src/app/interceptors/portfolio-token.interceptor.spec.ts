import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, describe, expect, it } from 'vitest';

import { PortfolioService } from '../services/portfolio.service';
import { accountingTokenInterceptor, ACCOUNTING_API_TOKEN, ACCOUNTING_ENVIRONMENT } from './accounting-token.interceptor';
import {
  PORTFOLIO_API_TOKEN,
  PORTFOLIO_ENVIRONMENT,
  PortfolioTokenEnvironment,
  portfolioTokenInterceptor,
} from './portfolio-token.interceptor';

// The environment is replaced through TestBed: no spec reads the generated environment.ts (it may hold a real token).
function setup(token: string | null, env: PortfolioTokenEnvironment = {}) {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(withInterceptors([accountingTokenInterceptor, portfolioTokenInterceptor])),
      provideHttpClientTesting(),
      { provide: PORTFOLIO_ENVIRONMENT, useValue: env },
      { provide: ACCOUNTING_ENVIRONMENT, useValue: {} },
      { provide: ACCOUNTING_API_TOKEN, useValue: 'accounting-token' },
      ...(token === null ? [] : [{ provide: PORTFOLIO_API_TOKEN, useValue: token }]),
    ],
  });
  return {
    http: TestBed.inject(HttpClient),
    service: TestBed.inject(PortfolioService),
    httpMock: TestBed.inject(HttpTestingController),
  };
}

describe('portfolioTokenInterceptor', () => {
  afterEach(() => TestBed.inject(HttpTestingController).verify());

  it('adds the portfolio bearer header to PortfolioService calls', () => {
    const { service, httpMock } = setup('portfolio-token');
    service.getTransactions().subscribe();
    const req = httpMock.expectOne(r => r.url.startsWith('/api/portfolio/transactions'));
    expect(req.request.headers.get('Authorization')).toBe('Bearer portfolio-token');
    req.flush({ items: [], total: 0 });
  });

  it('adds it to writes and the bare prefix; each API gets its own token', () => {
    const { http, httpMock } = setup('portfolio-token');
    http.post('/api/portfolio/fx/refresh', {}).subscribe();
    http.get('/api/portfolio').subscribe();
    http.get('/api/accounting/accounts').subscribe();
    for (const url of ['/api/portfolio/fx/refresh', '/api/portfolio']) {
      const req = httpMock.expectOne(url);
      expect(req.request.headers.get('Authorization')).toBe('Bearer portfolio-token');
      req.flush({});
    }
    const accounting = httpMock.expectOne('/api/accounting/accounts');
    expect(accounting.request.headers.get('Authorization')).toBe('Bearer accounting-token');
    accounting.flush([]);
  });

  it('leaves other URLs alone', () => {
    const { http, httpMock } = setup('portfolio-token');
    for (const url of ['/api/items', '/api/portfoliox/y', '/other/api/portfolio/z']) {
      http.get(url).subscribe();
      const req = httpMock.expectOne(url);
      expect(req.request.headers.has('Authorization')).toBe(false);
      req.flush({});
    }
  });

  it('defaults to environment.portfolioToken, and sends nothing when it is empty or absent', () => {
    const { http, httpMock } = setup(null, { portfolioToken: 'env-token' });
    expect(TestBed.inject(PORTFOLIO_API_TOKEN)).toBe('env-token');
    http.get('/api/portfolio/summary').subscribe();
    const req = httpMock.expectOne('/api/portfolio/summary');
    expect(req.request.headers.get('Authorization')).toBe('Bearer env-token');
    req.flush({});
  });

  for (const env of [{ portfolioToken: '' }, {}]) {
    it(`sends no header for ${JSON.stringify(env)}`, () => {
      const { http, httpMock } = setup(null, env);
      http.get('/api/portfolio/summary').subscribe();
      const req = httpMock.expectOne('/api/portfolio/summary');
      expect(req.request.headers.has('Authorization')).toBe(false);
      req.flush({});
    });
  }

  it('keeps an Authorization header the caller set itself', () => {
    const { http, httpMock } = setup('portfolio-token');
    http.get('/api/portfolio/summary', { headers: { Authorization: 'Bearer explicit' } }).subscribe();
    const req = httpMock.expectOne('/api/portfolio/summary');
    expect(req.request.headers.get('Authorization')).toBe('Bearer explicit');
    req.flush({});
  });
});
