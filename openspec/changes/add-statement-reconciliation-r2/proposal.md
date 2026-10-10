## Why

R1a and R1b store, match and cover statements but nothing feeds them. This change is the third stacked PR (R2) of the 對帳 design `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md` (v4, section 17 "R2 implementation notes"): the ingest worker that turns the owner's Google Drive statement PDFs into statement revisions through the ingest API, and a read-only verify mode that reports match quality against the live ledger without writing anything.

## What Changes

- New package `services/accounting-service/worker/` (`python -m worker run | poll | backfill | gate | export-masked | verify`): Drive acquisition through `rclone` (complete listing, download by id, verified atomic publish), password unlock and masking before anything leaves the worker, a sandboxed model-as-a-function parser behind a mandatory, latched gate, run leases with crash resume and a retry table, and the in-process read-only verify.
- New ingest-scope route `GET /statements/account-map` (`{"account_map", "mapping_version"}`), because the worker token cannot read settings.
- `settings_service.read_reconciliation_settings` and `read_reconciliation_rules` read SELECT-only; `GET /settings/reconciliation` uses them.
- R1a validator fix: account-map keys are `mail/<folder>/<sub>` or `manual/<folder>`.
- Ops files under `services/accounting-service/deploy/statements/` (systemd user units, timers, `gate.sh`, README with the read-only role SQL and the section 3 checklist), a README section and spec section 17 notes.
- No Alembic revision, no frontend, no actions. The reconciliation feature flag stays off; the worker runs as `opc` only for `export-masked` and `verify` (accepted interim deviation from design section 3, expiry = the dedicated-user split).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `accounting-reconciliation`: adds Drive acquisition, unlock and masking, the parser sandbox and gate, the run lease usage of the worker, the account-map route with the read-only settings reader, and verify mode.

`accounting-ledger` is NOT affected. No `accounting-ledger` delta is written.

## Impact

- **Code**: `services/accounting-service/worker/*`, `app/routers/statements.py` (account-map route), `app/services/statements/statement_ingest_service.py` (map key validator), `app/services/settings_service.py`; tests in `services/accounting-service/tests/worker/`.
- **Config**: `STATEMENT_*` environment variables (table in the service README); `requirements-worker.txt`.
- **Data**: none (no migration).
- **Deploy**: two systemd user timers (poll every 5 minutes, daily run 03:30 Asia/Taipei), a read-only database role `accounting_ro` for verify.
