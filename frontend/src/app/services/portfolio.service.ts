import { Injectable } from '@angular/core';
import { HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { BaseApiService } from './base-api.service';
import {
  PortfolioSummary,
  Transaction,
  Dividend,
  ExDividendRecord,
  ImportKind,
  ImportResult,
  NetworthPoint,
  CorporateAction,
  UpcomingEvent,
  Paged,
  TransactionQuery,
  DividendQuery,
} from '../models/portfolio.model';

function buildParams(query: Record<string, unknown>): HttpParams {
  let params = new HttpParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === '') continue;
    params = params.set(key, String(value));
  }
  return params;
}

@Injectable({
  providedIn: 'root'
})
export class PortfolioService extends BaseApiService<Transaction> {
  protected override baseUrl = '/api/portfolio/transactions';

  getSummary(): Observable<PortfolioSummary> {
    return this.http.get<PortfolioSummary>('/api/portfolio/summary');
  }

  getTransactions(query: TransactionQuery = {}): Observable<Paged<Transaction>> {
    return this.http.get<Paged<Transaction>>('/api/portfolio/transactions', {
      params: buildParams(query as Record<string, unknown>),
    });
  }

  createTransaction(transaction: Partial<Transaction>): Observable<Transaction> {
    return this.create(transaction);
  }

  updateTransaction(id: number, transaction: Partial<Transaction>): Observable<Transaction> {
    return this.update(id, transaction);
  }

  deleteTransaction(id: number): Observable<void> {
    return this.remove(id);
  }

  getDividends(query: DividendQuery = {}): Observable<Paged<Dividend>> {
    return this.http.get<Paged<Dividend>>('/api/portfolio/dividends', {
      params: buildParams(query as Record<string, unknown>),
    });
  }

  createDividend(dividend: Partial<Dividend>): Observable<Dividend> {
    return this.http.post<Dividend>('/api/portfolio/dividends', dividend);
  }

  updateDividend(id: number, dividend: Partial<Dividend>): Observable<Dividend> {
    return this.http.put<Dividend>(`/api/portfolio/dividends/${id}`, dividend);
  }

  deleteDividend(id: number): Observable<void> {
    return this.http.delete<void>(`/api/portfolio/dividends/${id}`);
  }

  getUpcomingExDividends(): Observable<ExDividendRecord[]> {
    return this.http.get<ExDividendRecord[]>('/api/portfolio/ex-dividends/upcoming');
  }

  uploadCsv(kind: ImportKind, file: File, dryRun: boolean): Observable<ImportResult> {
    const form = new FormData();
    form.append('file', file, file.name);
    const url = `/api/portfolio/imports/${kind}?dry_run=${dryRun ? 'true' : 'false'}`;
    return this.http.post<ImportResult>(url, form);
  }

  getNetworthHistory(from?: string, to?: string): Observable<NetworthPoint[]> {
    let params: HttpParams | undefined;

    if (from) {
      params = (params ?? new HttpParams()).set('from', from);
    }

    if (to) {
      params = (params ?? new HttpParams()).set('to', to);
    }

    return this.http.get<NetworthPoint[]>('/api/portfolio/history', params ? { params } : {});
  }

  getCorporateActions(symbol?: string, from?: string, to?: string): Observable<CorporateAction[]> {
    let params: HttpParams | undefined;

    if (symbol) {
      params = (params ?? new HttpParams()).set('symbol', symbol);
    }
    if (from) {
      params = (params ?? new HttpParams()).set('from', from);
    }
    if (to) {
      params = (params ?? new HttpParams()).set('to', to);
    }

    return this.http.get<CorporateAction[]>(
      '/api/portfolio/corporate-actions',
      params ? { params } : {},
    );
  }

  getUpcomingEvents(from?: string): Observable<UpcomingEvent[]> {
    const params = from ? new HttpParams().set('from', from) : undefined;
    return this.http.get<UpcomingEvent[]>(
      '/api/portfolio/upcoming-events',
      params ? { params } : {},
    );
  }

  getSymbolNames(): Observable<Record<string, string>> {
    return this.http.get<Record<string, string>>('/api/portfolio/symbol-map/names');
  }

  triggerDividendBackfill(): Observable<{
    symbols_scanned: number;
    events_seen: number;
    cash_inserted: number;
    stock_inserted: number;
    skipped_no_holding: number;
  }> {
    return this.http.post<{
      symbols_scanned: number;
      events_seen: number;
      cash_inserted: number;
      stock_inserted: number;
      skipped_no_holding: number;
    }>('/api/portfolio/dividends/backfill', {});
  }
}
