import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';

import {
  InboxApplyBody,
  InboxApplyOut,
  InboxCandidate,
  InboxOut,
  OwnerRule,
  ReconciliationAction,
} from './inbox.model';

/** Reconciliation inbox, 已處理 list and owner-taught rules. Writes bump `changed` so open pages reload. */
@Injectable({ providedIn: 'root' })
export class InboxService {
  private readonly http = inject(HttpClient);
  private readonly apiUrl = '/api/accounting/reconciliation';
  private readonly changeCount = signal(0);

  /** Bumped after every write so the inbox, reminder chip and 已處理 list re-read. */
  readonly changed = this.changeCount.asReadonly();

  get(opts: { accountId?: number; limit?: number } = {}): Observable<InboxOut> {
    let params = new HttpParams();
    if (opts.accountId !== undefined) {
      params = params.set('account_id', opts.accountId);
    }
    if (opts.limit !== undefined) {
      params = params.set('limit', opts.limit);
    }
    return this.http.get<InboxOut>(`${this.apiUrl}/inbox`, { params });
  }

  search(caseId: number, days = 14): Observable<InboxCandidate[]> {
    return this.http.get<InboxCandidate[]>(`${this.apiUrl}/inbox/cases/${caseId}/search`, {
      params: new HttpParams().set('days', days),
    });
  }

  apply(body: InboxApplyBody): Observable<InboxApplyOut> {
    return this.bump(this.http.post<InboxApplyOut>(`${this.apiUrl}/inbox/apply`, body));
  }

  applyProposal(id: number): Observable<unknown> {
    return this.bump(this.http.post<unknown>(`${this.apiUrl}/proposals/${id}/apply`, {}));
  }

  rejectProposal(id: number): Observable<unknown> {
    return this.bump(this.http.post<unknown>(`${this.apiUrl}/proposals/${id}/reject`, {}));
  }

  actions(limit = 50): Observable<ReconciliationAction[]> {
    return this.http.get<ReconciliationAction[]>(`${this.apiUrl}/actions`, {
      params: new HttpParams().set('limit', limit),
    });
  }

  revert(actionId: number): Observable<unknown> {
    return this.bump(this.http.post<unknown>(`${this.apiUrl}/actions/${actionId}/revert`, {}));
  }

  rules(): Observable<OwnerRule[]> {
    return this.http.get<OwnerRule[]>(`${this.apiUrl}/owner-rules`);
  }

  patchRule(id: number, enabled: boolean): Observable<OwnerRule> {
    return this.bump(this.http.patch<OwnerRule>(`${this.apiUrl}/owner-rules/${id}`, { enabled }));
  }

  deleteRule(id: number): Observable<unknown> {
    return this.bump(this.http.delete<unknown>(`${this.apiUrl}/owner-rules/${id}`));
  }

  private bump<T>(source: Observable<T>): Observable<T> {
    return source.pipe(tap(() => this.changeCount.update((count) => count + 1)));
  }
}
