import { HttpInterceptorFn } from '@angular/common/http';
import { InjectionToken, inject } from '@angular/core';

import { environment } from '../../environments/environment';

const ACCOUNTING_PREFIX = '/api/accounting';

/** The part of the generated environment this interceptor reads; a token so specs inject a fake instead. */
export interface AccountingTokenEnvironment {
  accountingToken?: string;
}

export const ACCOUNTING_ENVIRONMENT = new InjectionToken<AccountingTokenEnvironment>('ACCOUNTING_ENVIRONMENT', {
  providedIn: 'root',
  factory: () => environment as AccountingTokenEnvironment,
});

/**
 * Bearer token for the accounting API (`ACCOUNTING_SPA_TOKEN` in the root `.env`, written by `set-env.js`).
 * Empty means the backend runs without auth and no header is sent. The field is optional so an environment.ts
 * generated before it existed still compiles and simply sends nothing.
 */
export const ACCOUNTING_API_TOKEN = new InjectionToken<string>('ACCOUNTING_API_TOKEN', {
  providedIn: 'root',
  factory: () => inject(ACCOUNTING_ENVIRONMENT).accountingToken ?? '',
});

function isAccountingUrl(url: string): boolean {
  if (!url.startsWith(ACCOUNTING_PREFIX)) {
    return false;
  }
  const next = url.charAt(ACCOUNTING_PREFIX.length);
  return next === '' || next === '/' || next === '?';
}

/** Adds `Authorization: Bearer <token>` to `/api/accounting` requests only, and only when a token is configured. */
export const accountingTokenInterceptor: HttpInterceptorFn = (req, next) => {
  const token = inject(ACCOUNTING_API_TOKEN);
  if (!token || !isAccountingUrl(req.url) || req.headers.has('Authorization')) {
    return next(req);
  }
  return next(req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }));
};
