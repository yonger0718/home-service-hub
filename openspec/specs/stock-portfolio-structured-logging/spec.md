# stock-portfolio-structured-logging Specification

## Purpose
TBD - created by archiving change add-portfolio-scheduler-and-structlog. Update Purpose after archive.
## Requirements
### Requirement: Service emits structured JSON logs by default

The service SHALL emit one JSON log object per record on stdout with `event`, `level`, `logger`, and timestamp fields.

#### Scenario: Default JSON renderer is active
- **GIVEN** `LOG_FORMAT` is unset or `json`
- **WHEN** a log call is made
- **THEN** the rendered line SHALL be a single valid JSON object containing at minimum `event`, `level`, `logger`, and a timestamp field

#### Scenario: Console renderer for local dev
- **GIVEN** `LOG_FORMAT=console`
- **WHEN** a log call is made
- **THEN** the rendered line SHALL be human-readable plain text and SHALL NOT be JSON

#### Scenario: Stdlib loggers route through the same chain
- **WHEN** legacy code calls `logging.getLogger(__name__).info(...)`
- **THEN** the record SHALL be rendered by the same structlog processor chain as `structlog.get_logger(...)` calls

### Requirement: Logs carry OTel trace correlation

When an OpenTelemetry span is active, the service SHALL include `trace_id` and `span_id` fields on the rendered log record.

#### Scenario: Active span injects trace IDs
- **GIVEN** an OTel span is current
- **WHEN** a log call is made inside that span
- **THEN** the rendered record SHALL contain `trace_id` and `span_id` as hex strings

#### Scenario: No active span
- **WHEN** a log call is made with no current span
- **THEN** the rendered record SHALL omit `trace_id` and `span_id` (or set them to empty/null)

### Requirement: Logging configuration is idempotent and process-scoped

`configure_logging()` SHALL be safe to call multiple times in the same process without duplicating handlers.

#### Scenario: Repeat configuration does not duplicate handlers
- **WHEN** `configure_logging()` is called twice in the same process
- **THEN** the root logger SHALL have at most one structlog-bridge handler installed

