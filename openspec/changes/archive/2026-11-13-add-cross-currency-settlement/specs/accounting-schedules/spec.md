## MODIFIED Requirements

### Requirement: Loan summary

For a definition with a `repayment` or `collection` line, `remaining` SHALL be the loan entry's open amount as a positive magnitude (0 when `is_closed`) and `repaid` SHALL be the sum of the absolute settled values (requirement "Settlement endpoint") of all entries settling it. For an `installment` without such a line, `remaining` SHALL be `total_amount` minus the absolute amounts of the entries actually posted for its first line (found through `posted_entry_ids`; null without `total_amount`) and `repaid` null. For other definitions both SHALL be null.

The entry detail of a `payable` or `receivable` original that a definition's line references SHALL include `loan_schedule`: `{definition_id, name, status, posting_mode, posted_count, times, next_due_date, next_amount, remaining, repaid, needs_check}`; otherwise `loan_schedule` SHALL be null.

#### Scenario: Loan detail after three periods
- **GIVEN** the `300000` loan with 3 posted repayments of `8333` and the next instance due 2027-02-09
- **WHEN** `GET /api/accounting/entries/4021` is called
- **THEN** `loan_schedule` SHALL report `remaining = 275001`, `repaid = 24999`, `posted_count = 3`, `times = 36`, `next_due_date = 2027-02-09`

#### Scenario: Card installment remaining from posted entries
- **GIVEN** the `10000` installment of "Card installment remainder on the last period" whose first period posted `−3333` and whose entry the owner then edited to `−3300`
- **WHEN** the definition is listed
- **THEN** `remaining` SHALL be `6700`

#### Scenario: Hand repayment in another currency
- **GIVEN** a `1000` USD loan with 2 posted repayments of `100` USD and one hand repayment of `amount = -3200` TWD with `original_amount = -100`
- **WHEN** its detail is read
- **THEN** `loan_schedule` SHALL report `repaid = 300` and `remaining = 700`

#### Scenario: Legacy link across currencies does not count as repaid
- **GIVEN** a `1000` USD loan with 1 posted repayment of `100` USD and one linked repayment of `-3200` TWD without `original_amount`
- **WHEN** its detail is read
- **THEN** `loan_schedule` SHALL report `repaid = 100` and `remaining = 900`
