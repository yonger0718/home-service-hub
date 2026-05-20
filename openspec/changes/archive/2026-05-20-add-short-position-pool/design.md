## Context

`iter_realized_events` in `realized_pnl_service.py` carries a single inventory pool per symbol that only models long-side BUYs. When a SELL arrives with zero inventory it falls through a `note="no_inventory"` branch that records `cost_out=0`, `realized_pnl=proceeds_net` — correct for a data anomaly, wrong for a deliberate 券賣 (short open: no realized gain at open, gain materializes on cover). The same blind spot exists in the legacy `_step_transactions` SELL branch in `portfolio_service.py` that the helper was extracted from.

Cathay CSV import already parses `買賣別` into 8 values and computes `broker_subtype` (`現`/`資`/`券`/`沖`), but discards it before insert (`broker_cathay_service.py:175`). DB columns end at `(type=BUY|SELL)` with no side qualifier, so existing 2150 rows cannot be retro-classified without re-importing source CSVs.

Sample CSV (`證券對帳單 20260508230843.csv`, 1997 rows): 1505 cash long (75%), 454 day-trade (23%), 34 margin long (1.7%), **4 short rows total** (技嘉 + 漢磊, 2022). Short row volume is tiny but P&L correctness demands they're handled rather than papered over.

CodeRabbit flagged the oversell branch in PR #12 review (#12); the realized P&L PR shipped with the legacy behavior preserved on the explicit understanding that this follow-up would fix it.

## Goals / Non-Goals

**Goals:**

- Add a `position_side` discriminator (LONG/SHORT) on `transactions` so 資/券/沖 classification survives import.
- Compute realized P&L correctly for the 4 cases: long open, long close, short open, short close.
- Wire Cathay parser to populate `position_side` from `買賣別`; fold 利息 + 券手續費/標借費 into existing `fee` column at parse.
- Distinguish data anomalies (no inventory + no opposing side intent) from intentional shorts in the no-inventory note taxonomy.
- Preserve full bit-for-bit equivalence for long-only fixtures (no regression for 99.8% of existing rows).
- Surface 融券 rows visually in the realized-pnl page and transaction list.

**Non-Goals:**

- Itemized cost columns. `borrow_fee`, `interest`, `collateral` stay folded into `fee`. Reasoning: realized P&L only needs aggregate cost per side; itemization is a tax-report concern out of scope.
- Day-trade short-side classification (沖賣→沖買 short-day-trade vs 沖買→沖賣 long-day-trade). 沖 always maps to LONG. Reasoning: 沖 day-trades cumulatively net to zero, so position-side doesn't change P&L total; only per-row attribution. TW broker convention treats 現股當沖 (sell-first long-side) as the default 沖 semantic.
- Maintenance ratio, margin call thresholds, collateral tracking. Not needed for realized P&L.
- Multi-broker parsers (SinoPac, Yuanta). Cathay only.
- Re-import wizard or automated backfill for legacy 短 rows. User manually re-imports CSV.
- Unrealized short P&L tracking (open short position mark-to-market). Only realized events for shorts in this change.

## Decisions

### D1 — Single `position_side` column instead of expanding `TransactionType`

**Decision**: Add `position_side ENUM('LONG', 'SHORT') NOT NULL DEFAULT 'LONG'` column. Keep `TransactionType` as `BUY`/`SELL`.

**Alternative considered**: Expand `TransactionType` to 4 values: `BUY_LONG`, `SELL_LONG`, `BUY_COVER`, `SELL_SHORT`.

**Why**: Two orthogonal axes (direction × side) compose cleanly as two columns. Collapsing them into a single 4-value enum forces every consumer (queries, schemas, services, frontend filters, tests) to re-key on a new vocabulary. The Cathay parser, generic CSV parser, manual entry router, all existing pagination/filter logic, and downstream aggregation already key on `type`. A new column is additive and defaults gracefully for legacy rows.

### D2 — Default `LONG` for legacy rows, operator SQL-patches known 短 rows

**Decision**: Alembic migration sets `position_side='LONG'` for every existing row. Operator runs a targeted SQL `UPDATE ... SET position_side='SHORT' WHERE id IN (...)` for the small set of legacy 短 rows. **CSV re-import is NOT a safe migration path for legacy rows** — see Risks for why.

**Alternative considered**: (a) Automated CSV replay via the rehash path. (b) Nullable column to mark "unknown" legacy state. (c) One-shot Python script that walks broker CSV archives and reclassifies.

**Why**: 99.8% of legacy rows are LONG; the 4 SHORT rows in the existing data are easy to identify by hand. Re-import via rehash *would* be elegant if fees matched, but the fee-folding change in D5 means the new parser produces a different fee value than what's stored in DB for legacy rows (rows that came from an older import path or manual entry). The rehash path keys on fee equality, so it fails to match and falls through to insert — creating duplicate rows instead of correcting in place. SQL patch is surgical and obviously reversible.

### D3 — `iter_realized_events` carries two pools per symbol; route by `position_side`

**Decision**: Generalize the single `pools[symbol]` dict into `pools[symbol] = {'LONG': {…}, 'SHORT': {…}}`. Route each transaction by `position_side`:

| `position_side` | `type` | Action |
|---|---|---|
| LONG | BUY | Add to long pool, update MA cost |
| LONG | SELL | Close long, realize gain = (proceeds_net) - (cost_out via long MA) |
| SHORT | SELL | Open short, add to short pool with avg "short price" = sell_price (proceeds_net banked but NOT realized) |
| SHORT | BUY | Close short, realize gain = (avg_short_sell_proceeds_net_per_share × qty) - (cover_cost_gross + cover_fee) |

Short-pool entry per share = `proceeds_net / qty` at open time (treat fees as upfront cost of opening). Cover cost per share = `price + fee/qty + tax/qty` (TW: BUY-side trades have zero securities tax, so tax is 0 for SHORT BUY rows by construction; carried for API symmetry).

**Alternative considered**: Treat short rows as long-pool entries with negative quantity. P&L math then comes out arithmetically equivalent.

**Why**: Negative-quantity hack obscures intent and breaks invariants downstream (`current_qty >= 0` assumption in summary calc, frontend display of "held shares"). Explicit dual-pool model documents the two distinct holding types, lets summary/holding queries gate by side trivially, and avoids surprising sign behavior for future contributors.

### D4 — Edge-case note taxonomy: differentiate intent vs anomaly

**Decision**: Three notes possible on a realized event:
- `"no_long_inventory"` — LONG SELL with empty long pool. Data anomaly. `cost_out=0`, `realized_pnl=proceeds_net` (preserves current behavior under explicit flag).
- `"no_short_inventory"` — SHORT BUY with empty short pool. Data anomaly. `cost_out=cover_cost_gross+fee`, `realized_pnl=-cost_out` (treated as a pure cost; no phantom gain).
- (none) — normal SHORT SELL doesn't emit a realized event at all (it opens a position; nothing realized).

**Alternative considered**: Emit a SHORT SELL "open" event with `realized_pnl=0` for visibility.

**Why**: Mixing position-opening events with realized events confuses the YTD/total aggregates downstream and forces every aggregator and the frontend list to filter on `event.is_open` or similar. The realized-pnl page name is literal: only realized events appear. Position-opening events belong in the transaction list (which already shows every row), augmented with the new 融券 badge — no double display.

### D5 — Cost-column folding: 利息 + 券手續費 join `fee`, not new columns

**Decision**: At parse time in `broker_cathay_service.parse_cathay_rows`:
```
fee_total = 手續費 + 利息 + 券手續費/標借費
```

Sample inspection: 利息 appears on 資賣/券買 only (close-side); 券手續費 appears on 券賣 only (open-side). Their semantics differ (interest accrued vs upfront borrow fee), but for realized-pnl purposes both reduce net proceeds at the moment they appear on a row — exactly what `fee` does. Folding preserves the existing summary math (`proceeds_net = qty*price - fee - tax`) and avoids a schema change for 4 rows.

**Alternative considered**: Add `borrow_fee` + `interest` columns; surface separately.

**Why**: 8 itemized rows total in the entire CSV. Schema migration + 4 downstream consumers (parser, schema, frontend display, dividend summary) + test surface for itemization that benefits no current user-facing requirement. Defer to a follow-up if/when tax-form export becomes a need.

### D6 — UI: `position_side` badge on realized-pnl rows and transaction-list rows

**Decision**: Add a "融券" pill badge to rows where `position_side === 'SHORT'`, styled like the existing 當沖 badge. Reuse the badge slot used by `is_day_trade`. No new filter chip in this PR (filter by position_side deferred — user can already filter by symbol if they want to inspect 短 rows in isolation).

**Why**: Visual disambiguation is the minimum needed for users to validate the classification. Filter UI is a follow-up if requested.

### D7 — `_step_transactions` legacy mirror in `portfolio_service`

**Decision**: Apply dual-pool logic to the parallel SELL handler in `portfolio_service.py:_step_transactions` so the summary's `total_realized_pnl` matches the per-event sum invariant for fixtures including shorts. Single source of truth via `iter_realized_events` is the long-term goal but out of scope here — the legacy path is already kept in sync with `iter_realized_events` per the realized-pnl PR's D2.

**Why**: The invariant test `test_realized_pnl_invariant.py` enforces equivalence; if we update one path without the other, the test breaks. Mirror change is mechanical.

## Risks / Trade-offs

- **[Risk]** Backfill defaults all 2150 legacy rows to LONG, including any 4 真實 SHORT rows in user's already-imported CSV.
  **Mitigation**: User re-imports CSV after migration. Rehash path is idempotent. Worst case if user never re-imports: 4 rows mis-classified, ~NT$ 150 P&L mis-attributed in 2022 history — acknowledged in proposal.

- **[Risk]** SHORT BUY with `current_short_qty < quantity` (partial cover) handling needs spec-level scenario.
  **Mitigation**: Mirror long-side semantics: `sold_qty = min(quantity, current_qty)`, residual SHORT BUY beyond pool falls into `no_short_inventory` for the excess. Explicit scenario in spec.

- **[Risk]** Folding 利息 into `fee` makes per-row `fee` larger than `成交價 × 0.001425` upper bound (the broker's actual fee). Tests or downstream sanity-checks that key off this might break.
  **Mitigation**: Grep confirms no such check exists in service. Document folding in spec so future contributors don't add one.

- **[Trade-off]** No realized event for SHORT SELL (open) means the realized-pnl page silently omits short-open rows. User looking for "what did I do today" sees nothing on that page for a short open day.
  **Acceptance**: Transaction list shows everything (with 融券 badge added). Realized-pnl page is by-definition realized only.

- **[Trade-off]** 沖 always LONG misclassifies the rare 券資沖 (same-day short-side day-trade). With 0 historical 券資沖 in user data, this is theoretical. If user starts running them, follow-up needed.

## Migration Plan

1. Alembic migration:
   - `CREATE TYPE position_side_enum AS ENUM ('LONG', 'SHORT');`
   - `ALTER TABLE transactions ADD COLUMN position_side position_side_enum NOT NULL DEFAULT 'LONG';`
   - Downgrade: `DROP COLUMN position_side; DROP TYPE position_side_enum;`
2. Deploy backend with column + dual-pool logic. All existing rows = LONG, realized-pnl math unchanged for them.
3. Deploy frontend with badge support (no-op rendering for legacy LONG rows).
4. SQL-patch known legacy 短 rows directly. **Do NOT re-import broker CSV for legacy data** — see Risks.

```sql
UPDATE transactions SET position_side='SHORT' WHERE id IN (<known short row ids>);
```

5. Going forward: NEW broker CSV imports correctly classify 券買/券賣 via the parser changes in §3 of tasks. Only legacy rows already in DB need the SQL patch.

Rollback: backend deploys are stateless. Migration downgrade drops the column (loses SHORT classification but no data corruption). Frontend rollback is independent. The SQL patch is trivially reversible via `UPDATE ... SET position_side='LONG' WHERE id IN (...)`.

### Why re-import is unsafe for legacy 短 rows

The fee-folding change (§D5) means a re-parsed 券賣 row computes `fee = 手續費 + 利息 + 券手續費` (e.g. 漢磊 2022-07-25: `63 + 0 + 78 = 141`). But legacy DB rows for the same trade were imported via the older pipeline that stored only `手續費` (or some other historical fee value — `39` in the漢磊 case, not even matching the CSV's `63`). Two rehash strategies fail:

1. **`_legacy_fingerprint`** hashes `(symbol, type, qty, price, date, fee, tax)`. New fee (141) ≠ legacy DB fee (39) → fingerprint differs → no match.
2. **`_business_key_match`** filters DB by `(symbol, type, qty, price, fee, tax, date)`. Same fee mismatch → no match.

Both fall through to `_insert_transaction`, which creates a NEW row with `position_side=SHORT` while the legacy LONG row stays orphaned. Result: duplicate transaction in DB, double-counting in realized P&L.

The SQL patch sidesteps the rehash machinery entirely. It mutates the existing legacy row's `position_side` in place; fees stay at their legacy values (which is correct — those WERE the fees the user paid). No risk of duplication.

## Open Questions

- None — all design decisions locked from prior session brainstorm + sample CSV inspection.
