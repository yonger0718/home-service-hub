import { HttpContextToken, HttpInterceptorFn, HttpErrorResponse } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, throwError, retry } from 'rxjs';
import { trace, context } from '@opentelemetry/api';
import { MessageService } from 'primeng/api';

/**
 * Request context flag: when true the request bypasses this interceptor entirely — no retry, no console group,
 * no toast; the error still reaches the subscriber. For background fetches whose failure has its own fallback
 * (e.g. account logo images).
 */
export const SILENT_HTTP_ERRORS = new HttpContextToken<boolean>(() => false);

export const errorLoggingInterceptor: HttpInterceptorFn = (req, next) => {
  const messageService = inject(MessageService);
  if (req.context.get(SILENT_HTTP_ERRORS)) {
    return next(req);
  }
  return next(req).pipe(
    retry(1),
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

      let errorMsg = '發生未知的網路錯誤，請稍後再試。';
      if (error.status === 0) {
        errorMsg = '無法連線至伺服器，請檢查您的網路狀態。';
      } else if (error.status >= 500) {
        errorMsg = '伺服器發生異常，請稍後再試。';
      } else if (error.error?.message) {
        errorMsg = error.error.message;
      }

      messageService.add({ severity: 'error', summary: `錯誤 (${error.status})`, detail: errorMsg, life: 5000 });

      return throwError(() => error);
    })
  );
};
