## ADDED Requirements

### Requirement: Merge related cash transaction legs

The system SHALL support an optional `merge_related` query parameter on `GET /api/portfolio/accounts/{account_id}/cash-transactions` that collapses cash rows sharing a `related_transaction_id` into one synthetic group row per trade. When `merge_related=true`, pagination, sorting, and filtering operate on the merged virtual list.

The response item schema (`CashTransactionOut`) SHALL include an optional `child_legs: list[CashTransactionOut] | None` field populated only on synthetic group rows when merge is on.

#### Scenario: Merge off returns rows unchanged

- **WHEN** the client calls `GET /api/portfolio/accounts/1/cash-transactions` without `merge_related` or with `merge_related=false`
- **THEN** the response contains each `cash_transaction` row as a discrete item with `child_legs` omitted or null
- **AND** `total` equals the count of underlying rows matching the filter

#### Scenario: Merge on groups settle + fee + tax legs of a BUY

- **GIVEN** a BUY transaction with id 42 emitted 3 cash legs: settle (-100000), fee (-285), tax (-300), all sharing `related_transaction_id=42`
- **WHEN** the client calls `GET /api/portfolio/accounts/1/cash-transactions?merge_related=true`
- **THEN** the items list contains one synthetic group row with:
  - `id` = -42 (negative sentinel)
  - `type` = "trade"
  - `amount` = -100585
  - `txn_date` = settle leg's txn_date
  - `related_transaction_id` = 42
  - `child_legs` = the 3 original leg rows ordered settle → fee → tax
- **AND** the 3 underlying leg rows do NOT appear as separate items

#### Scenario: Dividend and manual rows stay individual when merge is on

- **GIVEN** the account has 2 dividend cash rows (`related_dividend_id` set) and 3 manual deposit rows (both relation FKs null)
- **WHEN** the client calls `GET /api/portfolio/accounts/1/cash-transactions?merge_related=true`
- **THEN** the dividend and manual rows appear individually with `child_legs` omitted or null

#### Scenario: Pagination total reflects merged count

- **GIVEN** the filtered set contains 60 BUY/SELL leg rows across 20 trades, 10 dividend rows, 5 manual rows
- **WHEN** the client calls the endpoint with `merge_related=true&limit=25&offset=0`
- **THEN** `total` = 35 (20 trade groups + 10 dividend + 5 manual)
- **AND** `items.length` = 25
- **AND** calling again with `offset=25` returns the remaining 10 items

#### Scenario: Type filter "fee" with merge on includes trade groups containing fee legs

- **GIVEN** 10 BUY trades each with a fee leg, plus 5 standalone fee rows from corrections
- **WHEN** the client calls the endpoint with `merge_related=true&type=fee`
- **THEN** the response includes 10 synthetic trade groups (each exposing its fee leg in `child_legs`) plus the 5 standalone fee rows
- **AND** `total` = 15

#### Scenario: Sort by amount uses summed group amount

- **WHEN** the client calls the endpoint with `merge_related=true&sort=amount:asc`
- **THEN** synthetic group rows are sorted by the sum of their legs' amounts, and individual rows by their own amount, intermixed
