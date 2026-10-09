## Why

Credit-card and bank statements are the issuer's authoritative record of what happened, but the ledger has no way to hold them or compare against them. The design `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md` (v4) describes the 對帳 feature; this change is its first stacked backend PR (R1a). Matching (R1b) and actions (R1c) follow.

## What Changes

- Fail-closed token scopes (`ACCOUNTING_TOKEN_SCOPES`, `ACCOUNTING_RESTRICTED_LABELS`) enforced on every route; restricted tokens (hermes, worker) can no longer reach legacy mutation routes. Startup refuses unsafe configurations.
- Feature gate `ACCOUNTING_RECONCILIATION_ENABLED`: the new routes answer 404 while it is not true.
- New schema (one Alembic revision): ingest runs with leases, statement files and sources, statements, immutable revisions, lines, events, lineage, cases, actions, `coverage_dirty` with append-only triggers on `ledger_entry`, `entry_group` and `account`; `entry_source` gains `statement`; accounts gain `statement_password_rule`, `statement_live_from`, `statement_source_root`.
- Worker-facing ingest API (`/statements/...`) with server-side flow-sign derivation, line keys and guardrails; statement reads and `/settings/reconciliation`.
- No matching, no actions, no frontend in this change.

## Capabilities

### New Capabilities

- `accounting-reconciliation`: scopes, feature gate, ingest runs and leases, files and sources, flow derivation, keys, guardrails, statement identity and revisions, events and lineage, modes, dirty events, ingest API.

### Modified Capabilities

- `accounting-ledger`: entry `source` gains `statement`; account model gains the three statement columns.

## Impact

- **Code**: `services/accounting-service/app/auth.py`, `models/statements.py`, `models/ledger.py`, `services/statements/`, `services/statement_ingest_service.py`, `services/statement_revision_service.py`, `routers/statements.py`, Alembic revision after `c4e8b2f1a7d3`.
- **Config**: three new environment variables (see the service README); existing SPA/ops labels must be mapped to `legacy` before enabling scopes.
- **Data**: one migration, no backfill.
