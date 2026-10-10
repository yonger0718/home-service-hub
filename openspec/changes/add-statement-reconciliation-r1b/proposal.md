## Why

R1a stores statements, revisions, lines, events and lineage but compares nothing. This change is the second stacked backend PR (R1b) of the 對帳 design `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md` (v4): it matches statement lines to ledger entries, records what covers what, keeps that coverage honest when the ledger changes, and exposes the batch and the cases. Actions (R1c) follow.

## What Changes

- Pure matching (`services/statements/matching.py`): eligibility gates, representations, scoring, a fixed accept/margin/floor decision and case derivation.
- Coverage (`statement_coverage` claims): conserved sums, one active claim per ledger row, snapshots, release and the dirty-event sweep, made safe by a writer barrier (advisory lock `0x44495254`, design §4.6 erratum).
- Lineage transfer and quarantine when a revision becomes current (`statement_event.flag`, `parse_review` cases).
- Reconcile orchestration: one statement per transaction, deferral and cross-account hints, bank balance gap, counts, deferral link filling.
- Routes: `POST /accounts/{id}/statements/{statement_id}/reconcile`, `POST /reconciliation/sweep`, `GET /reconciliation/cases`; settings gain `rules` and `rules_version`; statement detail gains coverage, `matched` and relevance-based `stale_events_pending`.
- Two Alembic revisions after `d1f3a7c2e9b4`: `e2a9c4d1b7f0` (barrier in the dirty trigger functions) and `f3b1d2c4a9e7` (`statement_event.flag`).
- No actions, no frontend, no service hooks into ledger writes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `accounting-reconciliation`: adds eligibility gates, representations and scoring, conservation and unique claims, the dirty sweep, coverage transfer and quarantine, reconcile, and the reconcile/sweep/cases routes; modifies events and lineage and adds the statement read model.

`accounting-ledger` is NOT affected: no ledger column, constraint or behaviour changes (the barrier only changes trigger function bodies; the triggers and the `coverage_dirty` table are R1a's). No `accounting-ledger` delta is written.

## Impact

- **Code**: `services/accounting-service/app/services/statements/matching.py`, `coverage_service.py`, `reconciliation_service.py`, `statement_revision_service.py`, `statement_ingest_service.py`, `settings_service.py`, `routers/statements.py`, `schemas/statements.py`, Alembic revisions `e2a9c4d1b7f0` and `f3b1d2c4a9e7`.
- **Config**: none new; `rules` live in the reconciliation settings row.
- **Data**: two migrations (function bodies; one nullable column), no backfill.
- **Deploy**: a daily worker call to `POST /reconciliation/sweep`; deferral links appear only when the later statement is reconciled or that batch runs.
