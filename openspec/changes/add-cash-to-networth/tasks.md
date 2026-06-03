## 1. Backend — schema + migration

- [x] 1.1 Add `total_cash_twd = Column(Numeric(20, 4), nullable=False, server_default="0")` to `app/models/portfolio_snapshot.py`
- [x] 1.2 Generate Alembic migration `xxxx_add_total_cash_twd.py`: `op.add_column("portfolio_snapshot", sa.Column("total_cash_twd", sa.Numeric(20, 4), nullable=False, server_default="0"))`; downgrade drops the column; chain after the most recent head
- [x] 1.3 Run `alembic upgrade head` against dev DB, verify column exists; `alembic downgrade -1` then re-upgrade to confirm reversibility

## 2. Backend — snapshot service

- [x] 2.1 In `app/services/portfolio_snapshot_service.write_today_snapshot`, after `summary = portfolio_service.get_portfolio_summary(db)`, compute `cash_total_twd, skipped = cash_account_service.get_total_balance_in(db, "TWD", asof=target)` (or whatever the existing method's return shape is — adapt if it returns just a Decimal vs tuple)
- [x] 2.2 Write `total_cash_twd=cash_total_twd` into the `PortfolioSnapshot` row construction
- [x] 2.3 If `skipped` is non-empty, log WARN with the skipped currency list (do NOT persist)
- [x] 2.4 Unit tests `tests/unit/test_portfolio_snapshot_service.py`: snapshot row carries cash total; snapshot with skipped currency logs warning and still writes the convertible portion; snapshot with zero accounts writes total_cash_twd=0

## 3. Backend — backfill service

- [x] 3.1 In `app/services/networth_backfill_service`, locate the snapshot-write loop (per phase=snapshots / both). For each historical date, compute `cash_account_service.get_total_balance_in(db, "TWD", asof=date)` and write it to the snapshot row
- [x] 3.2 Ensure `--rebuild-all` updates existing rows (use upsert / db.merge, not insert-only)
- [x] 3.3 Unit tests `tests/unit/test_networth_backfill_service.py`: rebuild populates total_cash_twd on existing rows; rebuild handles missing FX rate by skipping the account; dry-run does not write

## 4. Backend — summary + history endpoints

- [x] 4.1 In `app/schemas/portfolio.py` (the PortfolioSummary response model), add `total_cash_twd: Decimal` and `total_assets_twd: Decimal`
- [x] 4.2 In `app/services/portfolio_service.get_portfolio_summary` (or wherever the summary is built), compute `total_cash_twd = cash_account_service.get_total_balance_in(db, "TWD")` and `total_assets_twd = total_market_value + total_cash_twd`; populate the response
- [x] 4.3 In `app/routers/history.py::_serialize_snapshot`, add `total_cash_twd: str(row.total_cash_twd)` and `total_assets_twd: str(row.total_market_value + row.total_cash_twd)` to the dict
- [x] 4.4 Integration tests `tests/integration/test_portfolio_summary.py` (or extend existing): summary response includes both new fields with correct values for single TWD account, mixed currencies, zero accounts
- [x] 4.5 Integration tests `tests/integration/test_history_endpoint.py`: history response items include total_cash_twd + total_assets_twd

## 5. Frontend — model + service

- [x] 5.1 In `frontend/src/app/models/portfolio.model.ts`, extend `NetworthPoint` with `total_cash_twd: string` and `total_assets_twd: string`; extend `PortfolioSummary` with the same two fields
- [x] 5.2 No service change needed — both `getSummary` and `getNetworthHistory` already return the typed shape

## 6. Frontend — dashboard tile

- [x] 6.1 In `components/portfolio/dashboard/dashboard.html`, add a new tile row above the existing tile row containing one large `總資產` card; bind to `summary().total_assets_twd` formatted as TWD
- [x] 6.2 In `dashboard.scss`, style the new row with prominent typography (larger than existing tile values)
- [x] 6.3 Dashboard spec `dashboard.spec.ts`: 總資產 tile renders the combined value; with zero cash, tile equals market value

## 7. Frontend — networth chart stacked

- [x] 7.1 In `dashboard.ts`, when transforming `chartPoints()` into chart data, produce TWO series: `{label: "持股市值", data: [point.total_market_value]}` and `{label: "現金", data: [point.total_cash_twd]}`. Configure the chart options with `stacked: true` on the y-axis and `fill: true` on each dataset
- [x] 7.2 Verify the existing window selector / cache logic still works with the new dataset shape
- [x] 7.3 Dashboard spec: chart datasets array has length 2 in stacked configuration; switching window preserves the layout

## 8. Operational rollout

- [ ] 8.1 Operator runs `alembic upgrade head` against the production DB
- [ ] 8.2 Operator runs `python -m app.services.networth_backfill_service --rebuild-all` to populate historical cash totals
- [ ] 8.3 Operator verifies dashboard chart shows the cash band and the tile shows the combined total

## 9. Verification

- [x] 9.1 `cd services/stock-portfolio-service && pytest tests/unit/` clean
- [x] 9.2 `pytest tests/integration/` clean
- [x] 9.3 `cd frontend && npm test` clean
- [x] 9.4 `npm run build` clean
