## ADDED Requirements

### Requirement: Orchestrator computes the active-date set and passes it to networth backfill

Before invoking the networth backfill step, `run_chain` SHALL compute the active-date set over `[recalc_from, recalc_to]` from the current DB state (per Requirement "Holding-interval helper computes per-symbol active dates" in `stock-portfolio-networth-backfill`) and SHALL pass that set into both Phase 1 (`backfill_prices_range`) and Phase 2 (`replay_snapshots_range`) via the orchestrator's call to `networth_backfill_service.run_backfill`. The active-date computation SHALL run inside the same DB session lifecycle as the rest of the networth step.

#### Scenario: Active-date set passed through to both phases
- **WHEN** `run_chain` invokes the networth backfill step with `recalc_from = 2022-01-01` and `recalc_to = 2026-05-18`
- **THEN** the orchestrator SHALL compute `active_dates` once and pass the same set into both the price fetch phase and the snapshot replay phase

#### Scenario: Chain reports active vs total date counts
- **WHEN** the chain finishes a networth step with active-date filtering applied
- **THEN** the `StepResult` for `networth_backfill` SHALL include `dates_inactive` (count of weekday dates skipped because the user held nothing) alongside the existing `dates_processed`, `dates_skipped`, and `snapshots_written` counters

#### Scenario: Empty active set short-circuits the networth step
- **WHEN** the active-date set computed for `[recalc_from, recalc_to]` is empty (the user held nothing on any trading day in the range)
- **THEN** the networth step SHALL skip both phases entirely, return `StepResult(name="networth_backfill", status="ok", detail={"dates_processed": 0, "dates_inactive": <count>, ...})`, and the chain SHALL proceed to completion
