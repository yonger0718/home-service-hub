import { HttpInterceptorFn } from '@angular/common/http';
import { InjectionToken, inject } from '@angular/core';

/** True for `prefix` itself and for anything under it (`prefix/…` or `prefix?…`), never for `prefixx` or a longer path. */
export function isUnderPrefix(url: string, prefix: string): boolean {
  if (!url.startsWith(prefix)) {
    return false;
  }
  const next = url.charAt(prefix.length);
  return next === '' || next === '/' || next === '?';
}

/**
 * An interceptor that adds `Authorization: Bearer <token>` to requests under `prefix` only, only when the token is
 * non-empty, and never over an Authorization header the caller set itself.
 */
export function bearerTokenInterceptor(prefix: string, token: InjectionToken<string>): HttpInterceptorFn {
  return (req, next) => {
    const value = inject(token);
    if (!value || !isUnderPrefix(req.url, prefix) || req.headers.has('Authorization')) {
      return next(req);
    }
    return next(req.clone({ setHeaders: { Authorization: `Bearer ${value}` } }));
  };
}
