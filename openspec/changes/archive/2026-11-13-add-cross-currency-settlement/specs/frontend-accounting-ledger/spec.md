## MODIFIED Requirements

### Requirement: Entry detail view

The SPA SHALL provide `/accounting/entries/:id` showing: a header in the category colour with the icon and category path; name and total (with "內含手續費 $N" when children exist); the FX line (original amount × rate = converted, source) when converted; a grid of account, merchant or counterparty, date | project, kind, time; tags and attached rule chips; a 回饋 section (requirement "Entry reward section") in place of separate 紅利回饋 and 預估回饋 sections; a 關聯記錄 section with fee and discount children, the transfer counterpart, group siblings, the entry it settles or refunds and the entries settling or refunding it; for receivables and payables the open amount and a 新增收款 / 新增還款 action; the note; and an action bar 編輯 / 複製 / 退款 / 收款 / 刪除. Delete SHALL confirm inside the page and explain what else is deleted (children, the other transfer leg). Actions SHALL be disabled with the lock banner on locked entries.

The 新增收款 / 新增還款 form SHALL offer every non-archived account, those in the entry's currency first and the others labelled with their currency code. For an account in another currency it SHALL ask for the amount in that account's currency and the amount it settles in the entry's currency (default and maximum the open amount), show the conversion in the FX line's format, and send the second amount as `original_amount`. The second amount SHALL work at the ledger's four decimals: prefilled with the open amount to four decimals, so leaving it settles the entry exactly, and refused when empty, invalid or above the open amount. Switching to an account in another currency SHALL clear the first amount; switching back SHALL restore the same-currency default. The 已收 / 已還 figure SHALL sum the settled values (requirement "Settlement endpoint") of the settling entries, in the entry's currency.

#### Scenario: Receivable detail
- **GIVEN** a receivable of `-420` for Alan with `200` collected
- **WHEN** its detail opens
- **THEN** it SHALL show `剩餘 $220`, the collection under 關聯記錄, and an enabled 新增收款 action

#### Scenario: Settle from detail
- **WHEN** 新增收款 is tapped, account `錢包` and amount `220` entered, and saved
- **THEN** the settle endpoint SHALL be called and the detail SHALL show `已結清`

#### Scenario: Collect in another currency
- **GIVEN** a receivable of `-20` USD for Bea
- **WHEN** 新增收款 is tapped, TWD account `Line Bank (TWD)` chosen, `640` entered as the amount received and the settled amount left at `20`
- **THEN** the form SHALL show `US$20 × 32 = $640`, the settle endpoint SHALL receive `amount = 640` and `original_amount = 20`, and the detail SHALL show `已收 US$20` and `已結清`

#### Scenario: Full collection keeps the ledger's precision
- **GIVEN** a receivable of `-20.0001` USD
- **WHEN** 新增收款 is tapped, a TWD account chosen, `640` entered and the settled amount left at its default
- **THEN** the settle endpoint SHALL receive `original_amount = 20.0001` and the detail SHALL show `已結清`

#### Scenario: Remainder below one display unit
- **GIVEN** a receivable of `-20` USD with `0.0001` open
- **WHEN** 新增收款 is tapped, a TWD account chosen and `1` entered as the amount received
- **THEN** the settled amount SHALL default to `0.0001` and the settle endpoint SHALL receive `original_amount = 0.0001`; with the settled amount cleared nothing SHALL be sent and `請輸入金額` SHALL show
