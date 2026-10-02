import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, catchError, of, throwError } from 'rxjs';

import { EntryPage, EntryQuery, ImportRun, LedgerAccount } from '../models/accounting.model';

@Injectable({
  providedIn: 'root',
})
export class AccountingService {
  private http = inject(HttpClient);
  private apiUrl = '/api/accounting';

  getAccounts(): Observable<LedgerAccount[]> {
    return this.http.get<LedgerAccount[]>(`${this.apiUrl}/accounts`);
  }

  getEntries(accountId: number, query: EntryQuery = {}): Observable<EntryPage> {
    let params = new HttpParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== null && value !== undefined && value !== '') {
        params = params.set(key, String(value));
      }
    }
    return this.http.get<EntryPage>(`${this.apiUrl}/accounts/${accountId}/entries`, { params });
  }

  /** Latest import run, or null when no import has run yet (HTTP 404). */
  getLatestImport(): Observable<ImportRun | null> {
    return this.http.get<ImportRun>(`${this.apiUrl}/imports/latest`).pipe(
      catchError((error: HttpErrorResponse) =>
        error.status === 404 ? of(null) : throwError(() => error),
      ),
    );
  }
}
