## ADDED Requirements

### Requirement: TWSE requests use verified TLS with controlled fallback

The service SHALL attempt verified TLS for TWSE HTTP requests and SHALL only use insecure TLS fallback according to explicit `TWSE_TLS_MODE` behavior.

#### Scenario: Default fallback mode verifies first
- **WHEN** `TWSE_TLS_MODE` is unset or set to `fallback`
- **THEN** the TWSE client SHALL attempt the request with `verify=True` before any insecure fallback

#### Scenario: Fallback only on TLS verification error
- **WHEN** the verified TWSE request fails with `requests.exceptions.SSLError` in fallback mode
- **THEN** the TWSE client SHALL log a warning, tag tracing with `tls.fallback=true`, and retry once with `verify=False`

#### Scenario: Non-TLS failures do not trigger insecure fallback
- **WHEN** the verified TWSE request fails with timeout, connection error, HTTP error, or parse error
- **THEN** the TWSE client SHALL NOT retry with `verify=False` because of that failure

#### Scenario: Verify mode never falls back
- **WHEN** `TWSE_TLS_MODE=verify` and a TLS verification error occurs
- **THEN** the TWSE client SHALL return the safe failure result without an insecure retry

#### Scenario: Insecure mode is explicit
- **WHEN** `TWSE_TLS_MODE=insecure`
- **THEN** the TWSE client SHALL call TWSE with `verify=False` and SHALL emit a warning that insecure mode is active

### Requirement: TWSE client uses OS trust store for certificate validation

The stock portfolio service SHALL use `truststore` for TWSE outbound requests so verified TLS can rely on the operating system trust store.

#### Scenario: Truststore bootstrap is scoped to stock service market-data client
- **WHEN** the stock portfolio service initializes TWSE request handling
- **THEN** `truststore` SHALL be injected for that service's TWSE client path without requiring changes to shared app factory behavior for unrelated services

#### Scenario: Truststore dependency is declared
- **WHEN** the service dependencies are installed from `requirements.txt`
- **THEN** `truststore` SHALL be available to the stock portfolio service runtime

### Requirement: TWSE quote and ex-dividend requests share request policy

TWSE quote and ex-dividend fetches SHALL use a shared request policy for timeout, retry/backoff, TLS mode, logging, tracing, and cache metadata.

#### Scenario: Quote fetch uses shared client
- **WHEN** portfolio summary fetches live quotes
- **THEN** the request SHALL use the shared TWSE client behavior for timeout, retry, TLS mode, and tracing

#### Scenario: Ex-dividend fetch uses shared client
- **WHEN** upcoming ex-dividend records are fetched
- **THEN** the request SHALL use the shared TWSE client behavior for timeout, retry, TLS mode, and tracing

#### Scenario: Request metadata is observable
- **WHEN** a TWSE request completes or fails
- **THEN** tracing/logging SHALL include useful metadata such as HTTP status when available, TLS fallback state, and cache hit state

### Requirement: Market data is cached with short TTLs

The service SHALL cache TWSE quote and ex-dividend responses in-process for configurable short durations to reduce repeated dashboard request pressure.

#### Scenario: Quote cache hit avoids repeated network call
- **WHEN** the same normalized symbol set is requested again within the quote TTL
- **THEN** the service SHALL return cached quote data without issuing another TWSE network request

#### Scenario: Quote cache expires
- **WHEN** the quote TTL has elapsed
- **THEN** the next quote request SHALL issue a fresh TWSE network request

#### Scenario: Ex-dividend cache hit avoids repeated network call
- **WHEN** upcoming ex-dividend data is requested again within the ex-dividend TTL
- **THEN** the service SHALL return cached ex-dividend source data without issuing another TWSE network request

### Requirement: Portfolio summary reports market-data availability

Portfolio summary SHALL expose an additive market-data status field so clients can distinguish available, partial, and unavailable quote data.

#### Scenario: All active holdings have quotes
- **WHEN** all active holdings receive quote data
- **THEN** `PortfolioSummary` SHALL report quote status as `ok`

#### Scenario: Some active holdings lack quotes
- **WHEN** at least one but not all active holdings receive quote data
- **THEN** `PortfolioSummary` SHALL report quote status as `partial`

#### Scenario: No active holding has quote data
- **WHEN** active holdings exist but quote fetch returns no quote data
- **THEN** `PortfolioSummary` SHALL report quote status as `unavailable` and SHALL keep the safe numeric fallback behavior

### Requirement: Market-data logging remains useful without excessive volume

The service SHALL avoid INFO-level log lines per quoted symbol during normal polling.

#### Scenario: Per-symbol quote logs are debug
- **WHEN** TWSE quote parsing processes individual symbols
- **THEN** per-symbol details SHALL be logged at DEBUG level

#### Scenario: Aggregate quote result is logged
- **WHEN** TWSE quote parsing completes
- **THEN** the service SHALL expose aggregate parsed-symbol count through INFO logging or tracing metadata
