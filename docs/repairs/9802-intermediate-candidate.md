# Historical dividend reconciliation: intermediate candidate

Acceptance A's recording requirement is **UNMET**. This candidate retrieves and
reports eligible historical events, but deliberately creates no Dividend,
Transaction or cash entry at the post-import reconciliation boundary. It is not
a complete dividend-backfill repair and must not be accepted as such.

## Changed behavior

- Historical TWSE/TPEx year requests replace snapshot feeds in the import chain.
  The supplied date range filters events; exchange metadata routes listed/OTC
  symbols (including twstock 上市/上櫃). Unknown exchange queries both historical
  sources and reports either source's failure. Foreign-only symbols are skipped.
- TW LONG trades strictly before ex-date establish eligibility. Sells subtract;
  SHORT, foreign and warrant positions do not acquire a deferred event.
- Existing historical parsers and conversion are reused. Requests are shared
  within one reconciliation, never cached as absence across runs. Transport,
  malformed envelope/row and unavailable detail errors survive in step detail.
  A fallback amount may remain visible alongside its detail-source error.
- Eligible events expose amount/share, ex-date, quantity, source and explicit
  `retrieved; recording deferred—payment accounting unsupported` reason.
  `payment_date` is null unless an explicit source field supplies it. The
  operator's October 15 payment date is not hardcoded into retrieval.
- Deferred events keep step/chain partial. Independent networth work continues.
  The existing status route and step name remain. Additive `kind` and
  `quote_refresh` distinguish runs; the top level prefers the latest import.
  Quote-only callers with no import still receive the legacy top-level shape.
- Latest result per kind remains visible until replaced or process restart,
  instead of expiring after ten minutes. Storage is bounded to two results.
  The import view shows retrieval/deferment and source errors; dashboard polling
  follows quote status independently.

## Accounting boundary and smallest follow-up proposal

No existing recorder, cash-sync policy, fee/tax computation, manual dividend
route, scheduler or persisted schema is changed. The shared historical
conversion extraction preserves the legacy fetcher's behavior.

Human approval is needed to complete A. Smallest proposed contract extension:

1. Add nullable, source-attributed payment date and explicit pending/posted state
   to the existing dividend representation, retaining ex-date for eligibility.
   A missing date remains pending. A date alone never means cash was received.
2. Separate entitlement creation from cash posting at the recorder boundary.
   Post only after an approved payment/receipt criterion; use payment date for
   the cash leg and a deterministic dividend identity for idempotency. Retain
   existing fee/tax calculations, with an explicit approved calculation time.
3. Extend dividend API/schema/UI and cash synchronization to distinguish pending
   from received dividends. Audit cash balances, networth, performance totals,
   dividend CSV/manual entry and existing backfill callers so pending amounts
   never count as paid cash or realized income.
4. Specify compatibility for existing rows (do not infer payment from ex-date),
   source-correction behavior and duplicate identity across historical/snapshot
   providers. Any migration/reconciliation plan needs separate authorization;
   this candidate performs neither.

Affected contracts: `HistoricalDividendEvent`, `Dividend`, dividend API schemas,
`dividend_auto_record_service`, `cash_account_service.sync_dividend_cash_leg`,
legacy dividend backfill/manual/CSV callers, and dividend/networth/return reads.
This is a proposal, not implemented policy or deployment approval.

## Evidence and limitations

The attached worker evidence contains commands, versions, red/green outputs,
fix-only patch and a Git bundle with signed baseline/fix commits. The isolated
suite exercises actual HTTP/background callers, SQLite eligibility/persistence,
the supplied public 9802 historical fixture and real historical parsers.

132 relevant backend tests pass; 16 import/dashboard frontend tests pass; Angular
development build succeeds. Initial new regression suite on deployed baseline:
11 failed, 6 passed. Two existing tests were updated where the authorized
intermediate semantics intentionally replace import recording and quote-status
overwrite; legacy recorder/cash-sync tests remain passing.

No production connectivity, financial backfill, migration, merge or deploy.
Source live availability is not established by fixture tests. Status is still
single-process and is lost on restart; no durable entitlement store exists.
Legacy snapshot/manual historical endpoints retain their previous best-effort
behavior; truthful source status here is scoped to post-import reconciliation.
All new historical events, including stock legs, are deferred. Independent
review and lead validation remain pending, and the parent stays in_progress.
