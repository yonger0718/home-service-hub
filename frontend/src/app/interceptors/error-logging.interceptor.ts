import { HttpContextToken, HttpInterceptorFn, HttpErrorResponse } from '@angular/common/http';
import { catchError, identity, throwError, retry } from 'rxjs';
import { trace, context } from '@opentelemetry/api';

/**
 * Request context flag: when true the request bypasses this interceptor entirely — no retry, no console group; the error still
 * reaches the subscriber. For background fetches whose failure has its own fallback (e.g. account logo images).
 */
export const SILENT_HTTP_ERRORS = new HttpContextToken<boolean>(() => false);

/** Only reads are re-sent automatically. A write may have committed before its response was lost, so sending it again is the caller's (or the user's) decision. */
const AUTO_RETRIED_METHODS = new Set(['GET', 'HEAD']);

export const errorLoggingInterceptor: HttpInterceptorFn = (req, next) => {
  if (req.context.get(SILENT_HTTP_ERRORS)) {
    return next(req);
  }
  return next(req).pipe(
    AUTO_RETRIED_METHODS.has(req.method) ? retry(1) : identity,
    catchError((error: HttpErrorResponse) => {
      // 獲取當前 OpenTelemetry 的 Context 與 Span
      const activeSpan = trace.getSpan(context.active());
      const traceId = activeSpan?.spanContext().traceId;

      console.group('%c API Error Identified ', 'color: white; background: #eb4034; font-weight: bold;');
      console.error(`URL: ${req.url}`);
      console.error(`Status: ${error.status} (${error.statusText})`);
      if (traceId) {
        console.warn(`TraceID: ${traceId}`);
        console.log(`%c👉 Search this TraceID in Grafana Tempo to see the full backend flow.`, 'color: #3498db; font-style: italic;');
      } else {
        console.warn('TraceID: Not available (check if OTel is initialized)');
      }
      console.groupEnd();

      return throwError(() => error);
    })
  );
};
