## 1. Script

- [x] 1.1 Create `services/stock-portfolio-service/scripts/__init__.py` if `scripts/` does not yet exist as a package.
- [x] 1.2 Create `services/stock-portfolio-service/scripts/cleanup_historical_partial_dates.py` with a module docstring that names the incident, the partial-fetch gate (`detect-partial-phase1-fetch`), and an explicit warning not to repurpose this script for other dates.
- [x] 1.3 Define module-level constants `TARGET_DATES = (date(2026,4,3), date(2026,4,6), date(2026,5,1))` and `TARGET_SOURCES = ("TWSE", "TPEx")`.
- [x] 1.4 Use the service's existing SQLAlchemy session factory (import from `app.database`) so DB URL/env resolution is identical to the running service.
- [x] 1.5 Add an `argparse` CLI with a single `--apply` boolean flag (default = dry-run).
- [x] 1.6 Implement the dry-run path: SELECT `(date, source, symbol, close)` for the target pairs, print one row per match plus a total count, exit 0 without committing.
- [x] 1.7 Implement the apply path: same SELECT for preview, then DELETE only the target `(date, source)` pairs, commit, print the deleted count.
- [x] 1.8 Implement idempotent re-run: zero matches in dry-run or apply mode exits 0 with a clear "nothing to delete" message.
- [x] 1.9 Implement a post-delete verification: re-SELECT the target pairs after commit, assert count == 0, raise + rollback if not.

## 2. Verification

- [x] 2.1 Run `python -m scripts.cleanup_historical_partial_dates` (dry-run) against the dev DB; confirm it lists the 6 known rows (3 dates × 2 sources, all close=10.0000) and exits without committing.
- [x] 2.2 Run `python -m scripts.cleanup_historical_partial_dates --apply` against the dev DB; confirm the script reports 6 rows deleted and the post-delete verification passes.
- [x] 2.3 Re-run `python -m scripts.cleanup_historical_partial_dates --apply`; confirm it exits 0 with "nothing to delete".
- [x] 2.4 Query `price_history` directly to confirm only the 6 target rows are gone and unrelated rows on adjacent trading days are untouched.
- [x] 2.5 Run the full stock-portfolio-service test suite (`pytest`) and confirm no regression.

## 3. OpenSpec

- [x] 3.1 Run `openspec validate cleanup-historical-partial-dates --strict` and confirm it passes.
