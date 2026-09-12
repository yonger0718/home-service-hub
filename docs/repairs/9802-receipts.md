# Historical dividend entitlements and explicit receipt

This candidate implements the approved replacement for acceptance A: eligible
historical events persist as pending Dividend entitlements, not premature cash.
It extends reviewed candidate `eaa4b02777eabffa95a00ef7d7bca954ea8e4df2` on the
separate deployed materialization `c01ce5d43fccc20eef36f9499b174ebdfc6c6e65`.
Independent review and lead validation of this expanded candidate remain pending.

## Financial contract

- New TW manual, CSV and automatic dividend entries are pending. Historical
  reconciliation uses source-independent `TW:symbol:ex-date:dividend` identity.
  It records eligible LONG quantity and the existing fee/tax/stock formulas.
  SHORT, foreign, on/after-ex-date buys and warrants remain ineligible for this
  historical path. Small positive net payouts retain the existing 0.01 minimum.
- Source payment dates are nullable and attributed; missing later data does not
  erase an already sourced payment date. An ex-date, CSV received_date or scheduled
  date is never receipt confirmation. Pending rows have no actual receipt date.
- `GET /api/portfolio/dividends` includes receipt_status, payment_date/source,
  receipt_date/account, revision and review/correction detail. The dividend page
  shows pending versus confirmed versus unknown legacy records, independent of
  process-local orchestration status. Pending rows create neither cash nor stock
  transactions and are excluded from summary income/XIRR and snapshot replay.
- `POST /api/portfolio/dividends/{id}/confirm-receipt` requires receipt_date,
  account_id and the displayed revision. Receipt date must be on/after ex-date
  and not in the future; account must be active TWD. The UI starts with blank
  date/account, asks the user to verify actual receipt, and prevents duplicate
  submits. This is an explicit user action, not a scheduled job or flag change.
- Confirmation locks the row on PostgreSQL and conditionally changes pending
  state at the expected revision. Receipt metadata, the cash leg and any
  zero-cost stock BUY commit atomically. Any posting failure rolls them all back.
  Repeating the same confirmation returns the same record without further writes;
  conflicting date/account or stale pending revision returns 409. SQLite's
  concurrent write contention also returns 409 with reload/retry guidance.
- The confirmed cash leg uses actual receipt date and the chosen account. Stock
  shares are posted once on that receipt date. Neither requires an automatic
  cash-sync feature flag to turn an explicit confirmation into a posting.
- Changed source values update pending automatic records and increment revision.
  Confirmed amounts, fees, taxes, quantity and ledger entries are immutable:
  later source/position corrections are retained for review without reposting.
  Conflicting manual/CSV versus provider values become unresolved rather than
  silently overwriting manual amounts. Lost eligibility blocks confirmation.
- Confirmed, legacy and unresolved TW records cannot be edited/deleted through
  ordinary CRUD. Pending identity cannot be changed; pending value edits and
  deletes use conditional state/revision writes to avoid racing confirmation.
  Deleting an unposted pending row remains supported. Explicit reversal or
  resolution of legacy ambiguity is outside this candidate's ordinary CRUD.

## Legacy and duplicate protection

Migration labels existing records `legacy_unknown` without interpreting their
old received_date, rewriting amounts or touching cash. Existing legacy income
and balances retain their previous calculations for compatibility; that does
not assert that receipt has been verified. The UI labels this distinction.

A historical event matching an existing legacy/manual identity cannot create
another entitlement. Legacy confirmation is blocked even when no cash leg is
present. Pre-existing linked cash, legacy stock transactions, or an unlinked
cash dividend with the same account/date/amount require explicit resolution;
confirmation never adds a second credit. Ambiguity is persisted as unresolved
for new pending rows, and reconciliation does not clear it automatically.
Coincidentally equal unrelated unlinked cash may therefore require human review.

The cash replay tool skips TW dividend posting entirely: it cannot infer legacy
receipt or recreate pending cash. Transaction and foreign cash behavior remains
unchanged. Foreign dividend entry/revaluation stays on the existing contract.
The older automatic backfill endpoint now reports `pending_recorded`; its
`cash_inserted`/`stock_inserted` stay zero until the separate confirmation action.

## Migration and rollback notes — operator review only

Migration `a5receipt` follows `z3o4p5q6r7s8`. It adds receipt fields, revision,
source correction, unique entitlement identity, account FK and receipt-state
checks. It permits amount zero only for a positive stock entitlement. No
production migration, replay, backfill or service restart was executed.

For a later authorized deployment, review the cumulative fix-only diff against
the deployed materialization and plan a coordinated code/schema rollout. Do not
run older code against new pending records: older code does not understand the
receipt boundary. The new code requires the migration's columns before startup.
Migration preparation here is not deployment authorization.

The synthetic SQLite upgrade test preserves an existing dividend/cash leg and
verifies unknown receipt state. Downgrade is allowed only before any new
entitlement/receipt exists; otherwise it raises before dropping fields. Reverting
code/schema after recording new rows needs a separately reviewed export and
resolution plan, never deletion of pending records or inference of payment.

## Validation and remaining limits

Attached evidence gives exact signed hashes, commands, outputs, Python/package
versions, red/green tests, cumulative fix-only patch and portable Git bundle.
Tests exercise real import/background/HTTP paths and SQLite, concurrent separate
sessions, failure-after-cash rollback, repeated confirmation, source corrections,
legacy and unlinked cash, scheduled/unknown dates, pending and confirmed returns,
stock shares, CSV/manual entry, persistent reads after orchestration reset, and
synthetic migration upgrade/guarded downgrade. No live provider or PostgreSQL
instance was contacted; PostgreSQL row-lock behavior still needs its deployment
validation. Source fixture tests do not establish current provider availability.

Financial records persist in the DB. Orchestration status remains the last
in-process import and quote snapshots: it resets on restart and is not a live
receipt ledger. A past partial run is not retroactively rewritten by confirmation.
Historical portfolio snapshots reflect confirmed receipt on their next ordinary
recalculation; this candidate does not launch an automatic production replay.
Legacy ambiguity and confirmed-source corrections require explicit human
resolution; no automatic reconciliation or reversal policy is inferred.
