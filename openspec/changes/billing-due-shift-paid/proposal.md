## Why

Two things made the card-bill reminders say 已逾期 while the owner owed nothing (#196, #197). The billing pill and the reminder centre add the spend of a master card and its combined cards (副卡) but count payments on the master only, so a payment the owner recorded on a 副卡 never cleared the bill. And the due date is the printed one: when it falls on a weekend or a Taiwan holiday the bank debits on the next business day, so the views showed overdue for one to three days before any money moved — while the auto-pay job (PR #189) already books on the shifted day.

## What Changes

- The billing round reads `transfer_in` entries for the master **and** each combined card over the same payment window and sums them into `paid`.
- A read-only calendar endpoint, `GET /calendar/business-days?from=&to=`, returns the built-in Taiwan holiday table merged with the owner's `auto_pay.extra_holidays` / `extra_workdays`, so the SPA shifts due dates exactly as the server does.
- Due events carry a `payDate` (the first business day on or after the printed due date) when it differs; countdowns, the accounts-list pill and the overdue tone count to it. The printed due date stays as the label and the calendar-grid position; the reminder card adds ` · MM/DD 扣款` when the two differ.

## Impact

- accounting-service: new router `calendar` (read scope), no schema change.
- SPA: `BusinessCalendarService` (one read per session, weekends-only fallback), `billing-math`, `BillingService`, the accounts list, the reminder centre, the timeline's due rows.
