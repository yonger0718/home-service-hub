import { HttpClient } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';

import {
  ConfirmBody,
  ConfirmResult,
  ReconcileResult,
  StatementDetail,
  StatementSummary,
} from './statement.model';

/** Statement list, detail, reconcile and confirm. Writes bump `changed` so the reminder chip reloads. */
@Injectable({ providedIn: 'root' })
export class StatementsService {
  private readonly http = inject(HttpClient);
  private readonly apiUrl = '/api/accounting';
  private readonly changeCount = signal(0);

  /** Bumped after a reconcile or confirm so open pages re-read statement counts. */
  readonly changed = this.changeCount.asReadonly();

  list(accountId: number): Observable<StatementSummary[]> {
    return this.http.get<StatementSummary[]>(`${this.apiUrl}/accounts/${accountId}/statements`);
  }

  get(accountId: number, statementId: number): Observable<StatementDetail> {
    return this.http.get<StatementDetail>(
      `${this.apiUrl}/accounts/${accountId}/statements/${statementId}`,
    );
  }

  reconcile(accountId: number, statementId: number): Observable<ReconcileResult> {
    return this.bump(
      this.http.post<ReconcileResult>(
        `${this.apiUrl}/accounts/${accountId}/statements/${statementId}/reconcile`,
        {},
      ),
    );
  }

  confirm(accountId: number, statementId: number, body: ConfirmBody): Observable<ConfirmResult> {
    return this.bump(
      this.http.post<ConfirmResult>(
        `${this.apiUrl}/accounts/${accountId}/statements/${statementId}/confirm`,
        body,
      ),
    );
  }

  private bump<T>(source: Observable<T>): Observable<T> {
    return source.pipe(tap(() => this.changeCount.update((count) => count + 1)));
  }
}
