## Context

Verified DB state (2026-05-19) on `stock_portfolio_db.price_history`:

```
 source |    date    | count | symbol | close
--------+------------+-------+--------+-------
 TPEx   | 2026-04-03 |     1 | 6488   | 10.0000
 TWSE   | 2026-04-03 |     1 | 2330   | 10.0000
 TPEx   | 2026-04-06 |     1 | 6488   | 10.0000
 TWSE   | 2026-04-06 |     1 | 2330   | 10.0000
 TPEx   | 2026-05-01 |     1 | 6488   | 10.0000
 TWSE   | 2026-05-01 |     1 | 2330   | 10.0000
```

All three dates are confirmed TW market holidays:
- 2026-04-03: Children's Day substitute (Apr 4 = Sat)
- 2026-04-06: Tomb Sweeping substitute (Apr 5 = Sun)
- 2026-05-01: Labour Day (Fri)

`close = 10.0000` is sentinel data — not a real partial fetch, but the partial-fetch gate (`PARTIAL_FETCH_MIN_BASELINE_DAYS = 10`, `PARTIAL_FETCH_RATIO = 0.8`) now treats these rows the same way: their presence in `_existing_price_dates()` permanently shortcuts any retry attempt for those dates.

Downstream check: `portfolio_snapshot` has zero rows on these dates; no other table FKs `price_history(symbol, date)`.

## Goals / Non-Goals

**Goals:**
- Remove the 6 known sentinel rows so the partial-fetch gate sees a clean state going forward.
- Provide a reviewable, idempotent operational script (not an Alembic migration — this is a one-shot data fix, not a schema change).
- Keep the deletion narrowly scoped: only `(date, source)` pairs explicitly listed; no glob, no inference.

**Non-Goals:**
- No code changes to `networth_backfill_service.py` or any application module.
- No refetch — these are real TW holidays with no upstream OHLC, and the partial-fetch gate (after this cleanup) will correctly classify an empty fetch as legitimate (no rows persisted, no poisoning).
- No schema change.
- No general "find all suspicious rows" sweep. Only the three known dates.

## Decisions

### D1 — One-shot Python script over raw SQL or Alembic migration

Choose `scripts/cleanup_historical_partial_dates.py` invoked via `python -m`.

- Alembic migration: rejected — `alembic upgrade` should describe schema state, not transactional data fixes. Re-running migrations on a fresh DB would silently no-op (rows aren't there) but adds permanent overhead.
- Raw `.sql` file: workable, but a Python script lets us print a pre-delete count and a post-delete verification in the same execution, with the same DB session config as the service.
- Decision: Python script using the service's SQLAlchemy session factory, hardcoded date/source lists, `--dry-run` default.

### D2 — Dry-run default, explicit `--apply` flag

The script runs in `--dry-run` mode by default: prints the SELECT count and shows the rows that would be deleted, then exits without committing. Operator must pass `--apply` to commit.

Rationale: matches the careful-by-default posture in CLAUDE.md ("Executing actions with care"); destructive ops should require an explicit go-ahead.

### D3 — Idempotent: zero rows is success, not failure

Re-running `--apply` after the rows are already gone must exit 0 with "nothing to delete". Lets the script live in version control without becoming a foot-gun on rerun.

### D4 — Hardcoded target list, no CLI date inputs

Dates and sources are constants inside the script — no `--date` CLI flag. Reasoning: this script exists for one specific cleanup; making it parameterizable invites accidental "let me just delete this one too" usage, which is exactly what the partial-fetch gate was designed to prevent.

If future cleanups are needed, write a new script (or add a new constant list with a separate `--target` selector).

## Risks / Trade-offs

- **Risk:** Operator runs `--apply` against the wrong DB.
  → **Mitigation:** Script reads DB URL from the same `.env` as the service; pre-delete SELECT prints exact row contents so operator can confirm before re-running with `--apply`.

- **Risk:** Future operator assumes the script can be re-targeted for other dates.
  → **Mitigation:** Module docstring + tight scope (D4); script name explicitly names the historical incident.

- **Risk:** Hidden FK or materialized view depends on these rows.
  → **Mitigation:** Pre-delete check confirmed `portfolio_snapshot` empty for those dates; no other table references `price_history` rows. Re-verified at apply time by post-delete row count assertion.

- **Trade-off:** Script lives in `scripts/` outside the test suite. Acceptable — it is a one-shot operational tool, not application code; behavior is verifiable by `--dry-run` itself.
