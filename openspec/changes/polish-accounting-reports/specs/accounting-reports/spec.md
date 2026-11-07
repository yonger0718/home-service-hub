## MODIFIED Requirements

### Requirement: Comparison windows

With `compare`, the breakdown SHALL return `compare` with the comparison window's dates and its five totals, and each row's `compare_amount` SHALL be the same key's amount there (for `month`, `week` and `day`, the bucket at the same position) or null when there is none. `previous` and `last_year` SHALL resolve the window as the scenarios below state; a `previous` range that starts on the 1st but does not end on a month's last day SHALL compare with the same days of the earlier month or months.

#### Scenario: Previous for whole calendar months
- **WHEN** `compare=previous` is sent with a range starting on the 1st and ending on the last day of a month
- **THEN** the window SHALL move back by the range's number of calendar months and end the day before `date_from`: `2026-10-01..2026-10-31` → `2026-09-01..2026-09-30`, `2026-05-01..2026-10-31` → `2025-11-01..2026-04-30`

#### Scenario: Previous for a range starting on the 1st
- **WHEN** `compare=previous` is sent with a range starting on the 1st and ending before its month's last day
- **THEN** both dates SHALL move back by the number of calendar months the range touches, with the end day clipped to the target month's length: `2026-11-01..2026-11-07` → `2026-10-01..2026-10-07`, `2026-03-01..2026-03-30` → `2026-02-01..2026-02-28`, `2026-06-01..2026-11-07` → `2025-12-01..2026-05-07`

#### Scenario: Previous for any other range
- **WHEN** `compare=previous` is sent with `2026-10-05..2026-10-14`
- **THEN** the window SHALL have the same inclusive day count and end the day before `date_from`: `2026-09-25..2026-10-04`

#### Scenario: Last year
- **WHEN** `compare=last_year` is sent
- **THEN** both dates SHALL move back one year with Feb 29 becoming Feb 28: `2024-01-01..2024-12-31` → `2023-01-01..2023-12-31`, `2024-02-29` → `2023-02-28`

#### Scenario: Time buckets pair by position
- **WHEN** `compare` is sent with `group_by` `month`, `week` or `day`
- **THEN** each window's buckets SHALL be enumerated from its start (months from `date_from`'s month, weeks from the Monday of `date_from`'s week, every day) and the i-th comparison bucket's amount SHALL be the `compare_amount` of the i-th current bucket, null when the comparison window has no such bucket or it has no amount: `2026-09-01..2026-10-31` by month with `compare=previous` pairs 2026-09 with 2026-07 and 2026-10 with 2026-08
- **AND** `weekday` and the non-time dimensions SHALL keep matching the same key
