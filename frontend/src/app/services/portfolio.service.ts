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
  RecalcStatus,
  RecalcTriggerResponse,
  CorporateAction,
  UpcomingEvent,
  Paged,
  TransactionQuery,
  DividendQuery,
  RealizedPnlPaged,
  RealizedPnlQuery,
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

  refreshQuotes(): Observable<{
    refresh_scheduled: boolean;
    date: string;
    touched_symbols: string[];
  } | null> {
    return this.http.post<{
      refresh_scheduled: boolean;
      date: string;
      touched_symbols: string[];
    } | null>('/api/portfolio/imports/refresh-quotes', null);
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

  getRealizedPnl(query: RealizedPnlQuery = {}): Observable<RealizedPnlPaged> {
    return this.http.get<RealizedPnlPaged>('/api/portfolio/realized-pnl', {
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

  verifyOverrideSymbol(
    name: string,
    code: string,
    tradeDate: string,
  ): Observable<{
    name: string;
    code: string;
    status: 'verified' | 'name_mismatch' | 'not_traded_on_date' | 'fetch_failed' | 'user_overridden';
    expected_name: string | null;
    fetched_name: string | null;
  }> {
    return this.http.post<{
      name: string;
      code: string;
      status:
        | 'verified'
        | 'name_mismatch'
        | 'not_traded_on_date'
        | 'fetch_failed'
        | 'user_overridden';
      expected_name: string | null;
      fetched_name: string | null;
    }>('/api/portfolio/imports/verify-symbol', {
      name,
      code,
      trade_date: tradeDate,
    });
  }

  uploadCsv(
    kind: ImportKind,
    file: File,
    dryRun: boolean,
    hasHeader: boolean = true,
    nameOverrides?: Record<string, string>,
    confirmedOverrides?: string[],
  ): Observable<ImportResult> {
    const form = new FormData();
    form.append('file', file, file.name);
    if (nameOverrides && Object.keys(nameOverrides).length > 0) {
      form.append('name_overrides', JSON.stringify(nameOverrides));
    }
    if (confirmedOverrides && confirmedOverrides.length > 0) {
      form.append('confirmed_overrides', JSON.stringify(confirmedOverrides));
    }
    const url =
      `/api/portfolio/imports/${kind}` +
      `?dry_run=${dryRun ? 'true' : 'false'}` +
      `&has_header=${hasHeader ? 'true' : 'false'}`;
    return this.http.post<ImportResult>(url, form);
  }

  getRecalcStatus(): Observable<RecalcStatus> {
    return this.http.get<RecalcStatus>('/api/portfolio/imports/recalc/status');
  }

  triggerRecalc(range?: { start_date?: string; end_date?: string }): Observable<RecalcTriggerResponse> {
    return this.http.post<RecalcTriggerResponse>('/api/portfolio/imports/recalc', range ?? {});
  }

  getNetworthHistory(from?: string, to?: string, interval: 'day' | 'week' | 'month' = 'day'): Observable<NetworthPoint[]> {
    let params: HttpParams | undefined;

    if (from) {
      params = (params ?? new HttpParams()).set('from', from);
    }

    if (to) {
      params = (params ?? new HttpParams()).set('to', to);
    }

    if (interval !== 'day') {
      params = (params ?? new HttpParams()).set('interval', interval);
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
