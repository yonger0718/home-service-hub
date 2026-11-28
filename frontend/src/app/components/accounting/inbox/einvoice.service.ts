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
  EinvoicePreferencesChange,
  EinvoiceReplaceOut,
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

  /** 換一張發票: the link moves from `id` to `withId`; `id` goes back to 沒對到. 復原 is the same call reversed. */
  replace(id: number, withId: number): Observable<EinvoiceReplaceOut> {
    return this.http
      .post<EinvoiceReplaceOut>(`${this.apiUrl}/${id}/replace`, { with_invoice_id: withId })
      .pipe(this.changed());
  }

  undismiss(id: number): Observable<EinvoiceStateOut> {
    return this.http.post<EinvoiceStateOut>(`${this.apiUrl}/${id}/undismiss`, {}).pipe(this.changed());
  }

  targets(id: number, days = 14): Observable<EinvoiceTarget[]> {
    return this.http.get<EinvoiceTarget[]>(`${this.apiUrl}/${id}/targets`, { params: new HttpParams().set('days', days) });
  }

  /** `replace`: the invoices that could take the place of the one linked to this entry or its group (換一張發票). */
  picker(entryId: number, options: { readonly replace?: boolean } = {}): Observable<EinvoicePicker> {
    let params = new HttpParams().set('entry_id', entryId);
    if (options.replace) {
      params = params.set('replace', true);
    }
    return this.http.get<EinvoicePicker>(`${this.apiUrl}/picker`, { params });
  }

  dismissed(): Observable<EinvoiceDismissed[]> {
    return this.http.get<EinvoiceDismissed[]>(`${this.apiUrl}/dismissed`);
  }

  mutes(): Observable<EinvoiceMute[]> {
    return this.http.get<EinvoiceMute[]>(`${this.apiUrl}/mutes`);
  }

  /** The 復原 of 恢復提醒 (idempotent). */
  createMute(taxId: string): Observable<void> {
    return this.http.post<void>(`${this.apiUrl}/mutes/${encodeURIComponent(taxId)}`, null).pipe(this.changed());
  }

  deleteMute(taxId: string): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/mutes/${encodeURIComponent(taxId)}`).pipe(this.changed());
  }

  preferences(): Observable<EinvoicePreferences> {
    return this.http.get<EinvoicePreferences>(`${this.apiUrl}/preferences`);
  }

  patchPreferences(change: EinvoicePreferencesChange): Observable<EinvoicePreferences> {
    return this.http.patch<EinvoicePreferences>(`${this.apiUrl}/preferences`, change).pipe(this.changed());
  }

  private changed<T>() {
    return tap<T>(() => this.accounting.markEntriesChanged());
  }
}
