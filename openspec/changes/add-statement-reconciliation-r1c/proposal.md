## Why

R1a stores statements, R1b matches and covers them, R2 feeds them. None of it lets anyone act on a case: the owner sees `line_unmatched`, `ambiguous` and `amount_delta` cases but cannot match, book, adjust or dismiss. The 2026-10-10 verify run over the owner's 510 statement PDFs also showed that the matcher itself is wrong in four places (34 % of lines matched; 147 bank payment lines had no gate at all; 0 % of the lines whose ledger side is a scheduled instance matched; the 綜合月結單 case spans sibling accounts). This change is the fourth stacked PR (R1c) of the 對帳 design `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md` (v4, section 17 "R1c implementation notes", plan `docs/superpowers/plans/2026-10-11-statement-reconciliation-r1c.md`): the owner action engine and its thirteen actions, agent proposals, the policy engine (shipped off), statement confirm, manual statements, and the matching-rule fixes.

## What Changes

- New package `app/services/reconciliation_actions/`: one idempotent, snapshot-checked, audited, revertible action engine (`engine.py`) and the actions `match`, `unmatch`, `dismiss`, `defer_next_period`, `link_installment_plan`, `create_entry`, `create_system_entry`, `create_refund`, `attach_fee`, `adjust_amount`, `move_account`, `split_entry`, `balance_adjustment_asof`, `resolve_conflict`, behind a registry that computes `actions_allowed` per case.
- New routes (`app/routers/reconciliation.py`, `app/routers/statements.py`): case detail, apply action, dismiss, action and audit listings, revert; proposals (create, list, apply, reject); manual statements (`POST /accounts/{id}/statements`); confirm (`POST /accounts/{id}/statements/{sid}/confirm`).
- Agent proposals (`proposal_service.py`): typed id scoping, supersession by case version, mismatch guard.
- Policy engine (`policy_service.py`), **off by default**: `create_system_entry` for bank-only lines, `attach_fee` for the expected FX fee, and agent `match` of a single admissible representation, under per-action flags, `max_amount`, per-statement caps and a monthly `policy_budget` per currency. Runs after the matching transaction committed (reconcile route, revision submit, manual statement, sweep).
- Confirm (`confirm_service.py`): seven checks, `force` with a note, resolution of incoming deferrals (resolved in the later statement or reopened), `confirm_retry`.
- Matching rules: bank `payment` and `purchase` gates, recurring schedule instances count as standalone, complete loan installment groups claimed by a bank `payment`/`withdrawal` line, sibling-account candidates on `line_unmatched` cases.
- Read model: `CaseDetailOut` with `actions_allowed` and `pending_proposals`, `incoming_deferrals_pending` on statements, `policy` counts on the reconcile route. `needs_recheck` keeps only its matching meaning; `_recount` is counts-only.
- Settings: new JSON keys `policy` and `account_siblings` in `reconciliation_settings.data` (validated by `PUT /settings/reconciliation`, `admin`).
- `settlement_service.refund` gained a keyword `source: str = "manual"`; the default is unchanged for every existing caller and `create_refund` passes `"statement"`.
- Docs: service README (routes, settings, procedure, error codes), spec section 17 R1c notes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `accounting-reconciliation`: adds the action engine, the owner actions, agent proposals, the policy engine and budget, statement confirm, manual statements and sibling-account candidates; modifies the eligibility gates, the reconcile behaviour (manual and agent coverage preserved, `needs_recheck` ownership) and the statement and case read models.

`accounting-ledger` is NOT affected. No `accounting-ledger` delta is written: the only ledger-service change is the optional `source` keyword on `settlement_service.refund` (default `"manual"`, behaviour for every existing caller identical), and the new entries the actions write use `source='statement'` through the existing write services.

## Impact

- **Code**: `services/accounting-service/app/services/reconciliation_actions/*`, `proposal_service.py`, `policy_service.py`, `confirm_service.py`, `reconciliation_service.py` (sibling candidates, counts-only recount, manual/agent coverage kept), `statements/matching.py`, `settings_service.py`, `statement_ingest_service.py`, `statement_revision_service.py` (`promote_revision`), `settlement_service.py` (`source` keyword), `schedule_locks.py` (`nowait` keywords, defaults unchanged), `coverage_service.py` (`take_barrier_xact`), routers `reconciliation.py` and `statements.py`, schemas; tests under `services/accounting-service/tests/`.
- **Config**: settings keys `policy` and `account_siblings` only; no environment variable changes.
- **Data**: none. No Alembic revision: every table and column exists since R1a.
- **Deploy**: nothing to install. The policy ships off (`enabled=false`, every per-action flag false); enabling it is an owner `PUT /settings/reconciliation` documented in the README.
