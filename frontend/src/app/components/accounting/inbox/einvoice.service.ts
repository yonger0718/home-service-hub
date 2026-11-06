import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, tap } from 'rxjs';

import { AccountingService } from '../../../services/accounting.service';
import {
  EinvoiceDismissed,
  EinvoiceLinkTarget,
  EinvoiceMute,
  EinvoicePicker,
  EinvoicePreferences,
  EinvoiceStateOut,
  EinvoiceTarget,
} from './einvoice.model';

/** E-invoice owner actions and reads. Every write bumps `entriesChanged` so the reminders page, timeline and nav badge re-read. */
@Injectable({ providedIn: 'root' })
export class EinvoiceService {
  private readonly http = inject(HttpClient);
  private readonly accounting = inject(AccountingService);
  private readonly apiUrl = '/api/accounting/einvoices';

  link(id: number, target: EinvoiceLinkTarget): Observable<EinvoiceStateOut> {
    return this.http.post<EinvoiceStateOut>(`${this.apiUrl}/${id}/link`, target).pipe(this.changed());
  }

  unlink(id: number): Observable<EinvoiceStateOut> {
    return this.http.post<EinvoiceStateOut>(`${this.apiUrl}/${id}/unlink`, {}).pipe(this.changed());
  }

  dismiss(id: number, muteSeller = false): Observable<EinvoiceStateOut> {
    return this.http.post<EinvoiceStateOut>(`${this.apiUrl}/${id}/dismiss`, { mute_seller: muteSeller }).pipe(this.changed());
  }

  undismiss(id: number): Observable<EinvoiceStateOut> {
    return this.http.post<EinvoiceStateOut>(`${this.apiUrl}/${id}/undismiss`, {}).pipe(this.changed());
  }

  targets(id: number, days = 14): Observable<EinvoiceTarget[]> {
    return this.http.get<EinvoiceTarget[]>(`${this.apiUrl}/${id}/targets`, { params: new HttpParams().set('days', days) });
  }

  picker(entryId: number): Observable<EinvoicePicker> {
    return this.http.get<EinvoicePicker>(`${this.apiUrl}/picker`, { params: new HttpParams().set('entry_id', entryId) });
  }

  dismissed(): Observable<EinvoiceDismissed[]> {
    return this.http.get<EinvoiceDismissed[]>(`${this.apiUrl}/dismissed`);
  }

  mutes(): Observable<EinvoiceMute[]> {
    return this.http.get<EinvoiceMute[]>(`${this.apiUrl}/mutes`);
  }

  deleteMute(taxId: string): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/mutes/${encodeURIComponent(taxId)}`).pipe(this.changed());
  }

  preferences(): Observable<EinvoicePreferences> {
    return this.http.get<EinvoicePreferences>(`${this.apiUrl}/preferences`);
  }

  setRemindUnmatched(on: boolean): Observable<EinvoicePreferences> {
    return this.http.patch<EinvoicePreferences>(`${this.apiUrl}/preferences`, { remind_unmatched: on }).pipe(this.changed());
  }

  private changed<T>() {
    return tap<T>(() => this.accounting.markEntriesChanged());
  }
}
