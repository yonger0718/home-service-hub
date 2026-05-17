## ADDED Requirements

### Requirement: CSV transaction import fingerprint disambiguates distinct fills via optional order_id

CSV-imported transactions SHALL be deduplicated via `transactions.import_fingerprint`, a SHA256 over a canonical representation of the row. The canonical representation SHALL include an optional per-order identifier (`order_id`) when supplied by the source CSV, so that two otherwise-identical fills on the same day with different `order_id` values produce different fingerprints and both rows are inserted.

The `order_id` source column SHALL be recognised under canonical English (`order_id`) and Traditional Chinese synonyms (`委託書號`, `訂單編號`, `委託編號`). The column SHALL be optional: rows without it SHALL produce the same fingerprint as the pre-feature implementation (no `order_id` segment included in the canonical string).

#### Scenario: Identical same-day fills with distinct order_ids both import

- **GIVEN** two transaction CSV rows with identical `symbol`, `type`, `quantity`, `price`, `trade_date`, `fee`, `tax` but distinct non-empty `order_id` values
- **WHEN** the CSV is committed (not dry-run)
- **THEN** both rows SHALL be inserted as separate transactions with different `import_fingerprint` values

#### Scenario: Identical same-day fills without order_id collide (documented limitation)

- **GIVEN** two transaction CSV rows with identical fingerprint-input columns and no `order_id` column or empty `order_id` cells
- **WHEN** the CSV is committed
- **THEN** the second row SHALL be reported as a duplicate and skipped
- **AND** the import result SHALL still surface `skipped_duplicates >= 1` so the user can detect the collision

#### Scenario: Re-uploading the same CSV with order_ids dedupes cleanly

- **GIVEN** a transaction CSV with `order_id` values has been committed successfully
- **WHEN** the same CSV file is uploaded again
- **THEN** every row SHALL be reported as a duplicate (`created == 0`, `skipped_duplicates == len(rows)`)

#### Scenario: Pre-feature CSVs without order_id produce hashes identical to legacy

- **GIVEN** a transaction CSV with no `order_id` column at all
- **WHEN** rows are parsed under the new code
- **THEN** the computed `import_fingerprint` for each row SHALL equal the fingerprint that the prior implementation would have produced for the same row

#### Scenario: Mixed rows — some with order_id, some without — each get their own hash

- **GIVEN** a transaction CSV where row A has `order_id='OD-1'` and row B has the same fingerprint-input columns but no `order_id`
- **WHEN** the CSV is committed
- **THEN** both rows SHALL be inserted, because row A's fingerprint includes the `order_id` segment and row B's fingerprint does not

#### Scenario: Chinese-named order-id column is recognised

- **GIVEN** a transaction CSV whose header includes `委託書號`
- **WHEN** the parser normalises the header
- **THEN** `委託書號` SHALL be mapped to the canonical `order_id` and used in fingerprint computation

#### Scenario: Whitespace-only order_id treated as empty

- **GIVEN** a CSV row with `order_id='   '` (whitespace only)
- **WHEN** the row is parsed
- **THEN** the fingerprint SHALL be computed as if `order_id` were absent (legacy hash format)
