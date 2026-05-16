## ADDED Requirements

### Requirement: Active holdings aggregation is shared

The service SHALL use one shared active holdings aggregation path for portfolio summary and upcoming ex-dividend symbol filtering.

#### Scenario: Summary and ex-dividend agree on active symbols
- **WHEN** a sequence of BUY and SELL transactions leaves a symbol with positive quantity
- **THEN** both portfolio summary and upcoming ex-dividend filtering SHALL treat that symbol as active

#### Scenario: Closed positions are excluded from active holdings
- **WHEN** a sequence of BUY and SELL transactions leaves a symbol with zero quantity
- **THEN** both portfolio summary and upcoming ex-dividend filtering SHALL exclude that symbol from active holdings

### Requirement: List endpoints use service layer with additive pagination

Transaction and dividend list endpoints SHALL delegate query behavior to the portfolio service layer and SHALL support bounded pagination parameters.

#### Scenario: List transactions with default pagination
- **WHEN** a client calls `GET /api/portfolio/transactions` without pagination parameters
- **THEN** the router SHALL delegate to the service layer and return transactions ordered by trade date descending with a bounded default limit

#### Scenario: List dividends with default pagination
- **WHEN** a client calls `GET /api/portfolio/dividends` without pagination parameters
- **THEN** the router SHALL delegate to the service layer and return dividends ordered by ex-dividend date descending with a bounded default limit

#### Scenario: List endpoints accept limit and offset
- **WHEN** a client calls transaction or dividend list endpoints with `limit` and `offset`
- **THEN** the service SHALL apply bounded pagination and reject or clamp values outside the documented range

#### Scenario: List endpoints optionally filter by symbol
- **WHEN** a client provides a `symbol` query parameter to a transaction or dividend list endpoint
- **THEN** the service SHALL normalize the symbol and return only matching records

### Requirement: Unused local health router is removed

The stock portfolio service SHALL rely on shared-lib health route registration and SHALL NOT keep an unused local health router file.

#### Scenario: Health routes remain registered once
- **WHEN** the FastAPI app is created
- **THEN** `/health` and `/health/ready` SHALL be registered exactly once

#### Scenario: Local health router is absent
- **WHEN** maintainers inspect stock portfolio routers
- **THEN** there SHALL NOT be an unused `app/routers/health.py` implementation in the service

### Requirement: Dividend semantics are documented before response changes

The service SHALL document whether portfolio-level dividend totals represent lifetime dividends or active-holdings dividends before changing response semantics.

#### Scenario: Existing total dividends meaning is documented
- **WHEN** maintainers inspect `SPEC.md`
- **THEN** the meaning of `total_dividends` SHALL be explicitly documented as lifetime or active-holdings scoped

#### Scenario: New dividend fields are additive
- **WHEN** the service introduces both lifetime and active dividend totals
- **THEN** any new response fields SHALL be additive and SHALL preserve the existing `total_dividends` field until frontend consumers are migrated

### Requirement: Low-value API behavior changes are deferred unless confirmed

The service SHALL NOT change DELETE endpoints from HTTP 200 with JSON body to HTTP 204 No Content unless frontend usage has been checked and tests are updated.

#### Scenario: DELETE behavior remains stable by default
- **WHEN** this change is implemented without explicit frontend confirmation
- **THEN** DELETE endpoints SHALL keep their existing status/body behavior

#### Scenario: DELETE 204 is implemented only with confirmation
- **WHEN** frontend code is confirmed not to depend on DELETE response bodies
- **THEN** any implementation that changes DELETE endpoints to 204 SHALL update backend and frontend-facing tests accordingly

### Requirement: Misleading test naming is corrected without disabling mocked tests

The TWSE unit tests SHALL remain in the default test run when network access is mocked.

#### Scenario: Mocked TWSE tests run by default
- **WHEN** the stock portfolio service test suite runs normally
- **THEN** tests that mock `requests.get` SHALL remain enabled and SHALL NOT be marked as live e2e tests

#### Scenario: Misleading e2e filename is corrected
- **WHEN** a TWSE test file contains only mocked network tests
- **THEN** it SHALL be renamed or clarified so maintainers do not mistake it for a live external integration test
