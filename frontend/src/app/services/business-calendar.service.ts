import { Injectable, Signal, inject, signal } from '@angular/core';
import { catchError, of } from 'rxjs';

import { BusinessCalendar, WEEKENDS_ONLY, calendarFrom } from '../components/accounting/billing/business-days';
import { addDays, todayIso } from '../components/accounting/dates';
import { AccountingService } from './accounting.service';

/** Days either side of today the calendar is read for: covers every statement the billing views can show. */
export const CALENDAR_SPAN_DAYS = 400;

/**
 * The Taiwan business-day calendar (`GET /calendar/business-days`), read once per session when first injected. Until
 * it lands, and after a failed read, `calendar()` is `WEEKENDS_ONLY`: due dates then shift over weekends only.
 */
@Injectable({ providedIn: 'root' })
export class BusinessCalendarService {
  private readonly accounting = inject(AccountingService);
  private readonly state = signal<BusinessCalendar>(WEEKENDS_ONLY);
  readonly calendar: Signal<BusinessCalendar> = this.state.asReadonly();

  constructor() {
    const today = todayIso();
    this.accounting
      .getBusinessDays(addDays(today, -CALENDAR_SPAN_DAYS), addDays(today, CALENDAR_SPAN_DAYS))
      .pipe(catchError(() => of(null)))
      .subscribe(days => {
        if (days) {
          this.state.set(calendarFrom(days));
        }
      });
  }
}
