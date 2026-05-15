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
} from '../models/portfolio.model';

@Injectable({
  providedIn: 'root'
})
export class PortfolioService extends BaseApiService<Transaction> {
  protected override baseUrl = '/api/portfolio/transactions';

  getSummary(): Observable<PortfolioSummary> {
    return this.http.get<PortfolioSummary>('/api/portfolio/summary');
  }

  getTransactions(): Observable<Transaction[]> {
    return this.getAll();
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

  // Dividends — different resource, so use http directly
  getDividends(): Observable<Dividend[]> {
    return this.http.get<Dividend[]>('/api/portfolio/dividends');
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
}
