## ADDED Requirements

### Requirement: Service aggregates dividend events from multiple TWSE + TPEx feeds

The service SHALL pull dividend / ex-rights events from TWT48U, TWT49U, and the TPEx OTC daily-Q endpoint and merge them into a single deduped list filtered to the caller's holdings.

#### Scenario: All three sources contribute
- **WHEN** every source returns a non-empty payload and each contains a row for a different held symbol
- **THEN** the merged result SHALL contain one row per source, ordered ascending by `ex_dividend_date`

#### Scenario: Duplicate (symbol, ex_date) is collapsed by source priority
- **GIVEN** TWT48U and TWT49U both return a row for `(2330, 2026-06-15)`
- **WHEN** the orchestrator merges
- **THEN** the result SHALL contain exactly one row for that key and its `source` SHALL be `TWSE_TWT48U`

#### Scenario: Source failure does not abort
- **GIVEN** TPEx raises during fetch and the other two sources succeed
- **WHEN** the orchestrator runs
- **THEN** the result SHALL contain rows from TWT48U and TWT49U only and the orchestrator SHALL log the failure with the source name

#### Scenario: Non-held symbols are excluded
- **GIVEN** the caller passes `held_symbols={"2330"}`
- **WHEN** a source returns rows for `2330` and `0050`
- **THEN** the merged result SHALL contain only the `2330` row

### Requirement: New dividend-events endpoint

The service SHALL expose `GET /api/portfolio/dividend-events?year=YYYY` returning the merged rows for the caller's currently-held symbols.

#### Scenario: Default year is current TW year
- **WHEN** the client omits `year`
- **THEN** the service SHALL substitute the current `Asia/Taipei` calendar year

#### Scenario: Empty holdings yields empty response
- **WHEN** no active holdings exist for the requester
- **THEN** the response SHALL be `[]` with HTTP 200

### Requirement: TWT48U behaviour is preserved on the existing /upcoming endpoint

The existing `GET /api/portfolio/ex-dividends/upcoming` endpoint SHALL remain unchanged in shape, source, and filtering semantics.

#### Scenario: Upcoming endpoint output is byte-identical to pre-change
- **WHEN** the same TWT48U payload reaches both the old and new code paths
- **THEN** `/upcoming` SHALL return the exact same record set
