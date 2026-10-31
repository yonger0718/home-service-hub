## REMOVED Requirements

### Requirement: Reminder bell count

**Reason**: The timeline's 🔔 count added every overdue instance and every inbox question one by one and repeated the same number in three places. Pending work now has one site-wide count on 通知.

**Migration**: The timeline header links to `/notifications`. The count rules move to `frontend-notification-center` (Notification count): a schedule definition, a card bill and a statement each count once; debts are tracked, not counted. `countReminders` may stay for 提醒中心's internal use.

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
