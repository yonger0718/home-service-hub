import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { Observable, OperatorFunction, catchError, of, throwError } from 'rxjs';

import {
  BreakdownQuery,
  BreakdownResponse,
  BudgetInput,
  BudgetStatus,
  BudgetStatusResponse,
  SavedReport,
  SavedReportInput,
  TrendQuery,
  TrendResponse,
} from '../models/reports.model';
import { SampleBudgetStore, SampleSavedReportStore, sampleBreakdown, sampleTrend } from '../components/accounting/reports/sample-report-data';
import { todayIso } from '../components/accounting/dates';
import { toParams } from './accounting.service';

/**
 * `GET /reports/breakdown`, `/reports/trend` and the budget endpoints (openspec `add-accounting-reports`, design
 * D1 / D3). Only for a backend that lacks the routes (a
 * 404 whose body is not the app's `{code, message, trace_id}` envelope, e.g. FastAPI's `{"detail": "Not Found"}`),
 * the list/status/breakdown/trend reads and the two creates answer with the handoff's sample month and `sample`
 * turns on, so the page can say it is showing 示範資料. Every other error is the caller's, and so is any 404 on a
 * request addressed to one id (`/budgets/{id}`, `/saved/{id}`) or carrying the app envelope: a stale id surfaces
 * to the caller and never flips the page into demo mode. Writes on the sample land in in-memory stores.
 */
@Injectable({ providedIn: 'root' })
export class ReportsService {
  private readonly http = inject(HttpClient);
  private readonly apiUrl = '/api/accounting/reports';
  private readonly sampleBudgets = new SampleBudgetStore();
  private readonly sampleSaved = new SampleSavedReportStore();

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

  getBudgetStatus(month: string): Observable<BudgetStatusResponse> {
    return this.http
      .get<BudgetStatusResponse>(`${this.apiUrl}/budgets/status`, { params: { month } })
      .pipe(this.sampleOn404(() => this.sampleBudgets.status(month, todayIso())));
  }

  createBudget(input: BudgetInput): Observable<BudgetStatus> {
    return this.http.post<BudgetStatus>(`${this.apiUrl}/budgets`, input).pipe(this.sampleOn404(() => this.sampleBudgets.create(input)));
  }

  /** A new amount from `effective_from` on; the server keeps earlier months on the old row. */
  updateBudget(id: number, input: Pick<BudgetInput, 'amount' | 'effective_from'>): Observable<BudgetStatus> {
    return this.http.put<BudgetStatus>(`${this.apiUrl}/budgets/${id}`, input);
  }

  deleteBudget(id: number): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/budgets/${id}`);
  }

  // ---- saved reports (design D4) ----------------------------------------------------------------------------------

  listSaved(): Observable<SavedReport[]> {
    return this.http.get<SavedReport[]>(`${this.apiUrl}/saved`).pipe(this.sampleOn404(() => this.sampleSaved.list()));
  }

  getSaved(id: number): Observable<SavedReport> {
    return this.http.get<SavedReport>(`${this.apiUrl}/saved/${id}`);
  }

  createSaved(input: SavedReportInput): Observable<SavedReport> {
    return this.http.post<SavedReport>(`${this.apiUrl}/saved`, input).pipe(this.sampleOn404(() => this.sampleSaved.create(input)));
  }

  updateSaved(id: number, input: SavedReportInput): Observable<SavedReport> {
    return this.http.put<SavedReport>(`${this.apiUrl}/saved/${id}`, input);
  }

  deleteSaved(id: number): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/saved/${id}`);
  }

  private sampleOn404<T>(make: () => T): OperatorFunction<T, T> {
    return catchError<T, Observable<T>>((error: unknown) => {
      if (error instanceof HttpErrorResponse && error.status === 404 && !isAppEnvelope(error.error)) {
        this.sample.set(true);
        return of(make());
      }
      return throwError(() => error);
    });
  }
}

/** The app's own error body (shared_lib `{code, message, trace_id}`): the route exists, so the 404 is a real answer. */
function isAppEnvelope(body: unknown): boolean {
  return typeof body === 'object' && body !== null && typeof (body as { code?: unknown }).code === 'string';
}
