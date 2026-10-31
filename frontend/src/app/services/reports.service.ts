import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { Observable, OperatorFunction, catchError, of, throwError } from 'rxjs';

import { BreakdownQuery, BreakdownResponse, TrendQuery, TrendResponse } from '../models/reports.model';
import { sampleBreakdown, sampleTrend } from '../components/accounting/reports/sample-report-data';
import { toParams } from './accounting.service';

/**
 * `GET /reports/breakdown` and `/reports/trend` (openspec `add-accounting-reports`, design D1). Until the
 * accounting service ships them, a 404 answers with the handoff's sample month and `sample` turns on, so the page
 * can say it is showing 示範資料; every other error is the caller's.
 */
@Injectable({ providedIn: 'root' })
export class ReportsService {
  private readonly http = inject(HttpClient);
  private readonly apiUrl = '/api/accounting/reports';

  /** A report request met a 404: the figures on screen are the sample data, not the ledger. */
  readonly sample = signal(false);

  getBreakdown(query: BreakdownQuery): Observable<BreakdownResponse> {
    return this.http
      .get<BreakdownResponse>(`${this.apiUrl}/breakdown`, { params: toParams(query) })
      .pipe(this.sampleOn404(() => sampleBreakdown(query)));
  }

  getTrend(query: TrendQuery): Observable<TrendResponse> {
    return this.http
      .get<TrendResponse>(`${this.apiUrl}/trend`, { params: toParams(query) })
      .pipe(this.sampleOn404(() => sampleTrend(query)));
  }

  private sampleOn404<T>(make: () => T): OperatorFunction<T, T> {
    return catchError<T, Observable<T>>((error: unknown) => {
      if (error instanceof HttpErrorResponse && error.status === 404) {
        this.sample.set(true);
        return of(make());
      }
      return throwError(() => error);
    });
  }
}
