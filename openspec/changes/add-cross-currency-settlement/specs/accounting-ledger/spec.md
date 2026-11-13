## MODIFIED Requirements

### Requirement: Counterparty model

The system SHALL persist counterparties in `counterparty (id, name UNIQUE NOT NULL, moze_id UNIQUE nullable)`. `GET /api/accounting/counterparties` SHALL return each counterparty with `open_amounts`: one item per currency, each `−Σ amount` over that counterparty's `receivable` and `payable` entries in that currency (settling entries are `receivable` / `payable` entries too, so they are included). A settlement linked to an entry in another currency SHALL instead count in that entry's currency with its settled value (requirement "Settlement endpoint"), and not at all when it has none. A positive value means others owe the owner; a negative value means the owner owes.

#### Scenario: Open amount per counterparty
- **GIVEN** a receivable of `-420 TWD` (代付 for Alan) and a collection of `+200 TWD` from Alan
- **WHEN** counterparties are listed
- **THEN** Alan's `open_amounts` SHALL be `[{currency: TWD, amount: 220}]`

#### Scenario: Currencies kept apart
- **GIVEN** Alan also has a payable of `+1000 JPY`
- **WHEN** counterparties are listed
- **THEN** Alan's `open_amounts` SHALL contain `TWD 220` and `JPY -1000` as separate items

#### Scenario: Collection in another currency nets in the debt's currency
- **GIVEN** a receivable of `-20 USD` for Bea and its collection of `+640 TWD` with `original_amount = 20` (`USD`)
- **WHEN** counterparties are listed
- **THEN** Bea's `open_amounts` SHALL be empty

#### Scenario: Imported link across currencies without an original amount
- **GIVEN** a receivable of `-100 TWD` for Cai and a linked collection of `+100 JPY` without `original_amount`
- **WHEN** counterparties are listed
- **THEN** Cai's `open_amounts` SHALL be `[{currency: TWD, amount: 100}]`

### Requirement: Settlement endpoint

`POST /api/accounting/entries/{id}/settle` SHALL take `account_id` (the account receiving or paying), `amount` (unsigned, in that account's currency), optional `original_amount` (unsigned, in the target entry's currency), `entry_date`, `entry_time`, `description` and create an entry on that account with `settles_entry_id = {id}` and `is_settlement = true`: a `receivable` entry of `+amount` when the target is a `receivable` (收款), a `payable` entry of `-amount` when the target is a `payable` (還款), copying the target's `counterparty_id`.

An `original_amount` of JSON `null` SHALL be treated as absent. When the account's currency equals the target's, `original_amount` MUST be absent, else HTTP 422 naming `original_amount`. When it differs, `original_amount` MUST be given, else HTTP 422 naming `original_amount`; a pair whose rate rounded half-up to 10 decimals is `0` or not below `10^10` SHALL be refused with HTTP 422 naming `original_amount`. Otherwise the entry SHALL store `original_amount` (carrying the entry's sign), `original_currency` = the target's currency, `fx_rate = |amount| / |original_amount|` rounded half-up to 10 decimals, and `fx_source = 'manual'`.

A settlement's settled value, in the target's currency, is its `amount` when its currency is the target's, otherwise its `original_amount` when its `original_currency` is the target's, and nothing otherwise (an imported link across currencies without an original amount stays linked but never nets). The target's open amount is `|amount + Σ settled values of the entries settling it|`, in the target's currency. A request whose settled value exceeds the open amount SHALL be refused. The target entry SHALL be locked (`SELECT … FOR UPDATE`) while the open amount is computed and the settlement inserted, so concurrent settlements cannot exceed it; refunds SHALL lock the original the same way. The detail of a `receivable` or `payable` SHALL report `open_amount` and `is_settled`.

#### Scenario: Partial collection
- **GIVEN** a receivable of `-420` for Alan on card C
- **WHEN** a settlement of `200` is posted to account W
- **THEN** W SHALL gain `+200`, the receivable's `open_amount` SHALL be `220` and `is_settled` false

#### Scenario: Over-settlement refused
- **GIVEN** the same receivable with `220` open
- **WHEN** a settlement of `300` is posted
- **THEN** the response SHALL be HTTP 422

#### Scenario: Settlement in another currency refused
- **GIVEN** a TWD receivable
- **WHEN** a settlement is posted to a JPY account without `original_amount`
- **THEN** the response SHALL be HTTP 422 naming `original_amount`

#### Scenario: Collection in another currency
- **GIVEN** a receivable of `-20` USD for Bea on a USD card
- **WHEN** a settlement of `amount = 640`, `original_amount = 20` is posted to TWD account W
- **THEN** W SHALL gain `+640`, the settlement SHALL carry `original_amount = 20`, `original_currency = USD`, `fx_rate = 32`, `fx_source = manual`, and the receivable's `open_amount` SHALL be `0` and `is_settled` true

#### Scenario: Repayment in another currency
- **GIVEN** a payable of `+50` USD to Bea
- **WHEN** a settlement of `amount = 1600`, `original_amount = 30` is posted to TWD account W
- **THEN** W SHALL lose `1600`, the settlement SHALL be `-1600` with `original_amount = -30`, and the payable's `open_amount` SHALL be `20`

#### Scenario: Cross-currency over-settlement refused
- **GIVEN** the receivable of `-20` USD with `20` open
- **WHEN** a settlement of `amount = 800`, `original_amount = 25` is posted to TWD account W
- **THEN** the response SHALL be HTTP 422 naming `amount`

#### Scenario: Original amount refused in the same currency
- **GIVEN** a TWD receivable
- **WHEN** a settlement with `original_amount` is posted to a TWD account
- **THEN** the response SHALL be HTTP 422 naming `original_amount`

#### Scenario: Null original amount in the same currency
- **GIVEN** a TWD receivable with `200` open
- **WHEN** a settlement of `amount = 200` with `original_amount = null` is posted to a TWD account
- **THEN** it SHALL be accepted as a same-currency settlement and the receivable SHALL be settled

#### Scenario: Rate out of range refused
- **GIVEN** a receivable of `-20` USD
- **WHEN** a settlement of `amount = 1000000000000`, `original_amount = 20` is posted to TWD account W (rate `5 × 10^10`)
- **THEN** the response SHALL be HTTP 422 naming `original_amount` and nothing SHALL be written
