## REMOVED Requirements

### Requirement: Reminder bell count

**Reason**: The timeline's 🔔 count added every overdue instance and every inbox question one by one and repeated the same number in three places. Pending work now has one site-wide count on 通知.

**Migration**: The timeline header links to `/notifications`. The count rules move to `frontend-notification-center` (Notification count): a schedule definition, a card bill, a statement and the e-invoice task each count once; debts and invoices without an entry are tracked, not counted. The `ask + einvoice_ask` sums on the timeline and the reminders page go with it. `countReminders` may stay for 提醒中心's internal use.

## ADDED Requirements

### Requirement: Auto-pay in the bill pill and settings

For a card with `auto_pay_account_id` while card-statement auto-pay is enabled and the statement is on or after its start date, the bill pill SHALL read 自動扣繳 MM/DD (the business-day-shifted due date) until paid and 已自動扣繳 after the booking. 記帳設定 SHALL offer a switch 自動扣繳 and a required 起始日 when on, saved through `PUT /settings/reconciliation` (`auto_pay.enabled`, `auto_pay.enabled_from`).

#### Scenario: Linked card before the due date
- **GIVEN** auto-pay is on from 2026-10-01 and 國泰 CUBE 卡 links to 玉山銀行 with a due date of Saturday 10/24
- **WHEN** the accounts list renders on 10/22
- **THEN** the pill SHALL read 自動扣繳 10/26

#### Scenario: Turning auto-pay on
- **WHEN** the owner switches 自動扣繳 on without a start date
- **THEN** the form SHALL require 起始日 before saving

### Requirement: Entry invoice card

The entry detail SHALL show its invoice (the entry's own, else its group's) as a card after the 回饋 section and before 關聯記錄. The card header SHALL carry:

- a match chip: ✓ 金額相符, or 差 $N when the invoice total differs from the entry's or group's net;
- 自動連結 or 手動連結;
- a ⋯ menu with 換一張發票 and 解除連結, explained as 「之後不會再自動連回」.

The body SHALL show the seller, date and time, number, and the items: the first three, then 「… 另外 N 項」, and a total row. A group invoice SHALL be titled 發票 · 整組.

An eligible entry without an invoice SHALL show a dashed 「連結發票…」 row with the number of invoices within ±7 days.

#### Scenario: Auto-linked receipt
- **GIVEN** 全聯 採買 −1,286 auto-linked to AB-12345678 with 7 items totalling 1,286
- **WHEN** its detail opens
- **THEN** the card SHALL show ✓ 金額相符, 自動連結, three items, 「… 另外 4 項」 and 合計 $1,286

#### Scenario: Group invoice off by a little
- **GIVEN** a split group of 2,460 manually linked to a 2,463 invoice
- **THEN** the card SHALL be titled 發票 · 整組 and show 差 $3 and 手動連結

### Requirement: E-invoice settings section

記帳設定 SHALL show a 發票 section containing:

- the link status: enabled, last import, auto-linked count;
- 提醒沒有帳的發票;
- 對帳日期範圍, a ±0–7 day stepper;
- 同一家標幾次後問要不要不提醒, a 1–10 stepper;
- 不提醒的商家, with 移除;
- 已標成不用記帳: the newest three, then a link to all, each with 復原, and runbook dismissals labelled 規則自動標記.

The switch and the steppers SHALL save through `PATCH /einvoices/preferences`.

#### Scenario: Widen the date window
- **WHEN** the owner raises 對帳日期範圍 from ±3 to ±5 天
- **THEN** `PATCH /einvoices/preferences` SHALL be sent with `date_window_days: 5`
