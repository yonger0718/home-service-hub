import { HttpClient, HttpContext, HttpErrorResponse, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { MessageService } from 'primeng/api';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SILENT_HTTP_ERRORS, errorLoggingInterceptor } from './error-logging.interceptor';

describe('errorLoggingInterceptor', () => {
  let http: HttpClient;
  let backend: HttpTestingController;
  let add: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    add = vi.fn();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([errorLoggingInterceptor])),
        provideHttpClientTesting(),
        { provide: MessageService, useValue: { add } },
      ],
    });
    http = TestBed.inject(HttpClient);
    backend = TestBed.inject(HttpTestingController);
    for (const fn of ['group', 'error', 'warn', 'log', 'groupEnd'] as const) {
      vi.spyOn(console, fn).mockImplementation(() => {});
    }
  });

  afterEach(() => {
    backend.verify();
    vi.restoreAllMocks();
  });

  it('by default a failing request is retried once and toasts one error', () => {
    const errors: unknown[] = [];
    http.get('/api/accounting/x').subscribe({ error: e => errors.push(e) });
    backend.expectOne('/api/accounting/x').flush({ message: 'nope' }, { status: 404, statusText: 'Not Found' });
    backend.expectOne('/api/accounting/x').flush({ message: 'nope' }, { status: 404, statusText: 'Not Found' });
    backend.expectNone('/api/accounting/x');
    expect(add).toHaveBeenCalledTimes(1);
    expect(add).toHaveBeenCalledWith(expect.objectContaining({ severity: 'error', summary: '錯誤 (404)', detail: 'nope' }));
    expect(errors).toHaveLength(1);
    expect(errors[0]).toBeInstanceOf(HttpErrorResponse);
  });

  it('with SILENT_HTTP_ERRORS the request is sent once, nothing is toasted and the error still propagates', () => {
    const errors: unknown[] = [];
    const context = new HttpContext().set(SILENT_HTTP_ERRORS, true);
    http.get('/api/accounting/accounts/7/logo.png?v=abc', { context, responseType: 'blob' }).subscribe({ error: e => errors.push(e) });
    backend.expectOne(r => r.url.startsWith('/api/accounting/accounts/7/logo.png'))
      .flush(new Blob(['nope']), { status: 404, statusText: 'Not Found' });
    backend.expectNone(r => r.url.startsWith('/api/accounting/accounts/7/logo.png'));
    expect(add).not.toHaveBeenCalled();
    expect(errors).toHaveLength(1);
    expect((errors[0] as HttpErrorResponse).status).toBe(404);
    expect(console.group).not.toHaveBeenCalled();
  });

  it('with SILENT_HTTP_ERRORS a success passes through unchanged', () => {
    let body: unknown;
    const context = new HttpContext().set(SILENT_HTTP_ERRORS, true);
    http.get('/api/accounting/x', { context }).subscribe(b => (body = b));
    backend.expectOne('/api/accounting/x').flush({ ok: true });
    expect(body).toEqual({ ok: true });
    expect(add).not.toHaveBeenCalled();
  });
});
