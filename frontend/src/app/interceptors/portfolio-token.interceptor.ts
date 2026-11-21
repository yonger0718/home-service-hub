import { HttpInterceptorFn } from '@angular/common/http';
import { InjectionToken, inject } from '@angular/core';

import { environment } from '../../environments/environment';
import { bearerTokenInterceptor } from './bearer-token';

/** The part of the generated environment this interceptor reads; a token so specs inject a fake instead. */
export interface PortfolioTokenEnvironment {
  portfolioToken?: string;
}

export const PORTFOLIO_ENVIRONMENT = new InjectionToken<PortfolioTokenEnvironment>('PORTFOLIO_ENVIRONMENT', {
  providedIn: 'root',
  factory: () => environment as PortfolioTokenEnvironment,
});

/**
 * Bearer token for the portfolio API (`PORTFOLIO_SPA_TOKEN` in the root `.env`, written by `set-env.js`; issue #117).
 * Empty means the backend runs without auth and no header is sent. The field is optional so an environment.ts
 * generated before it existed still compiles and simply sends nothing.
 */
export const PORTFOLIO_API_TOKEN = new InjectionToken<string>('PORTFOLIO_API_TOKEN', {
  providedIn: 'root',
  factory: () => inject(PORTFOLIO_ENVIRONMENT).portfolioToken ?? '',
});

/** Adds `Authorization: Bearer <token>` to `/api/portfolio` requests only, and only when a token is configured. */
export const portfolioTokenInterceptor: HttpInterceptorFn = bearerTokenInterceptor('/api/portfolio', PORTFOLIO_API_TOKEN);
