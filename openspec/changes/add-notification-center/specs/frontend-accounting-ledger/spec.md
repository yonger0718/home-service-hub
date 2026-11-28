## REMOVED Requirements

### Requirement: Reminder bell count

**Reason**: The timeline's 🔔 count added every overdue instance and every inbox question one by one and repeated the same number in three places. Pending work now has one site-wide count on 通知.

**Migration**: The timeline header links to `/notifications`. The count rules move to `frontend-notification-center` (Notification count): a schedule definition, a card bill, a statement and the e-invoice task each count once; debts and invoices without an entry are tracked, not counted. The `ask + einvoice_ask` sums on the timeline and the reminders page go with it. `countReminders` may stay for 提醒中心's internal use.

## ADDED Requirements

### Requirement: Auto-pay in the bill pill and settings

For a card with `auto_pay_account_id` while card-statement auto-pay is enabled, the statement is on or after its start date and not over 單筆上限, the bill pill SHALL read 自動扣繳 MM/DD (the business-day-shifted due date) until paid and 已自動扣繳 after the booking. 記帳設定 SHALL offer a switch 自動扣繳 and a required 起始日 when on, saved through `PATCH /settings/reconciliation/auto-pay` (write scope; `enabled`, `enabled_from`), not the admin-only whole-document `PUT /settings/reconciliation`.

#### Scenario: Linked card before the due date
- **GIVEN** auto-pay is on from 2026-10-01 and 國泰 CUBE 卡 links to 玉山銀行 with a due date of Saturday 10/24
- **WHEN** the accounts list renders on 10/22
- **THEN** the pill SHALL read 自動扣繳 10/26

#### Scenario: Bill over 單筆上限
- **GIVEN** the same card with a 25,000 bill and 單筆上限 20,000
- **WHEN** the accounts list renders on 10/22
- **THEN** the pill SHALL read the plain due countdown, not 自動扣繳, because auto-pay will skip it on the debit day

#### Scenario: Turning auto-pay on
- **WHEN** the owner switches 自動扣繳 on without a start date
- **THEN** the form SHALL require 起始日 before saving

### Requirement: Entry invoice card

The entry detail SHALL show its invoice (the entry's own, else its group's) as a card after the 回饋 section and before 關聯記錄. The card header SHALL carry:

- a match chip: ✓ 金額相符, or 差 $N when the invoice total differs from the entry's or group's net;
- 自動連結 or 手動連結;
- a ⋯ menu with 換一張發票 and 解除連結, explained as 「之後不會再自動連回」. 解除連結 SHALL confirm with a toast 「已解除連結 <number>」 whose 復原 links the same invoice back to the same entry or group.

#### Scenario: Group invoice off by a little
- **GIVEN** a split group of 2,460 manually linked to a 2,463 invoice
- **THEN** the card SHALL be titled 發票 · 整組 and show 差 $3 and 手動連結

### Requirement: Entry invoice card body

The body of the entry invoice card SHALL show the seller, date, number, and the items: the first three, then 「… 另外 N 項」, and a total row. A group invoice SHALL be titled 發票 · 整組.

#### Scenario: Auto-linked receipt
- **GIVEN** 全聯 採買 −1,286 auto-linked to AB-12345678 with 7 items totalling 1,286
- **WHEN** its detail opens
- **THEN** the card SHALL show ✓ 金額相符, 自動連結, three items, 「… 另外 4 項」 and 合計 $1,286

### Requirement: Entry detail invoice link row

An entry without an invoice that may be a link target SHALL show, on the full entry page as in the drawer, the dashed 連結發票… row at the invoice card's width and corner radius, not a plain button. The ±7-day hint SHALL stay in the drawer only.

#### Scenario: Full page after 解除連結
- **WHEN** the owner unlinks the invoice on the full entry page
- **THEN** the card's place SHALL show the dashed 連結發票… row

### Requirement: Replace an entry's invoice

換一張發票 SHALL first list the invoices that could take the linked one's place (never the linked one itself; 「等 N 項」 counts all its items), laid out as the 選帳 sheet (seller, date · items, the total without a sign, 金額差 $N / 日期差 N 天 against this entry), and change nothing until one is picked. A pick SHALL swap in one call; the old invoice SHALL return to 沒對到, not 不用記帳. A toast 「已換成 <number>」 SHALL offer 復原, which swaps back.

#### Scenario: Cancel changes nothing
- **WHEN** the owner opens 換一張發票 and taps 取消
- **THEN** no write SHALL be sent and the card SHALL still show the old invoice

#### Scenario: Pick another invoice
- **GIVEN** an entry linked to AB00000301
- **WHEN** the owner picks AB00000312 from the list
- **THEN** the entry SHALL be linked to AB00000312, AB00000301 SHALL be 沒對到, and the toast SHALL read 「已換成 AB00000312」 with 復原

### Requirement: E-invoice settings section

記帳設定 SHALL show a 發票 section containing:

- the link status from `GET /einvoices/preferences`: 「最近匯入 MM/DD · 已自動連結 N 張」, or 還沒有匯入;
- 提醒沒有帳的發票;
- 對帳日期範圍, a ±0–7 day stepper;
- 同一家標幾次後，建議以後都不提醒, a 1–10 stepper;
- 不提醒的商家 (titled 統編 X when the seller has no name), each with 恢復提醒;
- 已標成不用記帳: the newest three, then 看全部 N 張 (with 收起, and 只列最近 100 張 at the API cap), each with 放回, and runbook dismissals labelled 規則自動標記.

The switch and the steppers SHALL save through `PATCH /einvoices/preferences`.

#### Scenario: Widen the date window
- **WHEN** the owner raises 對帳日期範圍 from ±3 to ±5 天
- **THEN** `PATCH /einvoices/preferences` SHALL be sent with `date_window_days: 5`

### Requirement: E-invoice settings feedback

A failed stepper save in 記帳設定 › 發票 SHALL say 「沒存到，已改回 <value>」 right under its row, with 重試. 恢復提醒 and 放回 SHALL each confirm with a toast whose 復原 undoes it. Every 復原 on the card and in settings SHALL refuse, with 「復原沒成功：這張發票已經變動」, once the invoice changed since the toast. While the 換一張發票 list loads, focus SHALL be on its 取消, so Esc cancels it.

#### Scenario: Undo 放回
- **WHEN** the owner taps 放回 on a 不用記帳 invoice and then the toast's 復原
- **THEN** the invoice SHALL be 不用記帳 again and back in the list

#### Scenario: Stepper save fails
- **WHEN** raising 對帳日期範圍 from ±3 to ±4 天 fails
- **THEN** the stepper SHALL show ±3 天 and its row SHALL be followed by 「沒存到，已改回 ±3 天」 and 重試
