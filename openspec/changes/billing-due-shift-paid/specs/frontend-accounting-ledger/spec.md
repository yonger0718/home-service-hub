## ADDED Requirements

### Requirement: Combined-card payments count toward the master's bill

A card's current statement (`paid`, `remaining`) SHALL sum the `transfer_in` entries of the master card and of every non-archived card combined into it, over the same payment window, so a payment recorded on a 副卡 clears the master's bill.

#### Scenario: Payment on a 副卡 clears the master

- **GIVEN** a master card with one combined card, period spend 1,000 on the master and 2,800 on the 副卡
- **AND** a `transfer_in` of 2,800 recorded on the 副卡 within the payment window
- **THEN** the statement SHALL show 應繳 $3,800, 已繳 $2,800, 剩餘 $1,000

### Requirement: Card bills count to the debit day

Each due event SHALL carry the debit day: the first business day on or after the printed due date, from `GET /api/accounting/calendar/business-days` (read once per session; until it lands or when it fails, only weekends shift). The accounts-list pill, the reminder countdown, the overview's due rows and the overdue tone SHALL count to the debit day; the printed due date stays the sort key and the calendar-grid position, and the reminder card SHALL read `MM/DD 截止 · MM/DD 扣款` when the days differ.

#### Scenario: Weekend due date over a holiday

- **GIVEN** a statement due Sat 2026-10-24, the calendar listing Mon 2026-10-26 as a holiday, and today 2026-10-26
- **THEN** the reminder SHALL read `10/24 截止 · 10/27 扣款` with the countdown `明天`, not `已逾期`
- **AND** the accounts-list pill SHALL read `明天繳費截止`

#### Scenario: Business-day due date is unchanged

- **GIVEN** a statement due Mon 2026-10-05
- **THEN** the reminder SHALL read `10/05 截止` and count to 2026-10-05
