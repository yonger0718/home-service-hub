## 1. Model + key helper

- [x] 1.1 Extend `StockHolding` in `frontend/src/app/models/portfolio.model.ts` with `market: 'TW' | 'US' | 'LSE'`, `native_close: number | string | null`, `native_currency: string | null`, `live_fx_rate_to_twd: number | string | null`
- [x] 1.2 Extend `Transaction` in the same file with `market?: 'TW' | 'US' | 'LSE'`, `currency?: string`, `fx_rate_to_twd?: number | string`
- [x] 1.3 Add a `RealizedPnlEvent`-shaped type (or extend existing) with `market`, `native_proceeds`, `native_cost`, `native_currency` mirroring the Phase 2 backend response
- [x] 1.4 Add exported helper `holdingKey(h: { symbol: string; market: string }): string` returning `\`${symbol}|${market}\`` in `portfolio.model.ts`
- [x] 1.5 Add unit test `tests/unit/holding-key.spec.ts` covering: same symbol two markets yield two keys; bare-symbol input rejected at type level

## 2. nativeAmount pipe

- [x] 2.1 Create pure pipe `NativeAmountPipe` at `frontend/src/app/pipes/native-amount.pipe.ts` accepting `(value, currency)`, 4 dp for `GBp`, 2 dp otherwise, currency suffix when non-TWD and non-null, dash for null
- [x] 2.2 Export pipe from a standalone module / standalone-pipe declaration so it can be imported by dashboard, holdings table, realized-PnL list
- [x] 2.3 Unit test `native-amount.pipe.spec.ts` covering the 4 scenarios in the spec (GBp 4dp, USD 2dp, TWD no suffix, null dash)

## 3. PortfolioService composite-key migration

- [x] 3.1 Refactor `frontend/src/app/services/portfolio.service.ts` so any internal `Map`/cache keyed on `symbol` switches to `holdingKey(holding)`
- [x] 3.2 Update every public lookup that accepted a bare symbol to accept `{ symbol; market }` or the composite key string; deprecate / remove bare-symbol overloads
- [x] 3.3 Grep `frontend/src/app/components/portfolio/**` for `holding.symbol` used as a key (Map key, object key, trackBy return) and migrate each callsite to `holdingKey()`
- [x] 3.4 Unit test in `portfolio.service.spec.ts`: two holdings `{AAPL,TW}` and `{AAPL,US}` coexist in cache, are returned as two distinct entries

## 4. Holdings table — Market, Native Price, FX tooltip, grouping

- [x] 4.1 Add `Market` column (PrimeNG badge) to the dashboard holdings table component template
- [x] 4.2 Add `Native Price` column rendered via `nativeAmount` pipe with `native_close` + `native_currency`
- [x] 4.3 Wrap the existing TWD `market_value` cell so foreign rows with `live_fx_rate_to_twd != null` render a `pTooltip` info icon with text `Revalued at 1 ${native_currency} = ${live_fx_rate_to_twd} TWD`
- [x] 4.4 Add PrimeNG row-group keyed on `market` with fixed order `TW, US, LSE`; TW expanded by default; skip groups with zero rows
- [x] 4.5 Update `trackBy` to use `holdingKey()`
- [x] 4.6 Unit test `holdings-table.grouping.spec.ts`: TW-only → single TW group expanded, mixed → 3 groups in fixed order, sort by `unrealized_pnl` keeps grouping
- [x] 4.7 Update existing dashboard summary test fixture to include `market`, `native_close`, `native_currency`, `live_fx_rate_to_twd`; verify existing TW assertions still pass

## 5. Transaction form — market picker + conditional FX inputs

- [x] 5.1 Add `market` dropdown (`TW` / `US` / `LSE`, default `TW`) to the transaction create/edit form template
- [x] 5.2 Bind a reactive form `valueChanges` subscription so non-TW selection reveals `currency` + `fx_rate_to_twd` inputs and TW selection hides them
- [x] 5.3 Pre-fill `currency` from market choice (`US`→`USD`, `LSE`→`GBP`) while keeping the input editable so `GBp` can be entered manually
- [x] 5.4 Add client-side validator on `fx_rate_to_twd`: required + `> 0` when `market !== 'TW'`
- [x] 5.5 Strip `currency` / `fx_rate_to_twd` from the request body when `market === 'TW'` (or send `currency: 'TWD'` only); include them otherwise
- [x] 5.6 Unit test `transaction-form.market-picker.spec.ts`: TW hides inputs, US pre-fills USD, LSE allows GBp override, `fx_rate_to_twd <= 0` blocks submit

## 6. Transaction list — market badge for non-TW rows

- [x] 6.1 Render a market badge in the transaction list timeline row meta area when `transaction.market && transaction.market !== 'TW'`
- [x] 6.2 Verify TW rows render with no badge and pixel-match the pre-Phase-3 layout via existing snapshot or rendered DOM check

## 7. Realized P&L list — market + native amount columns

- [x] 7.1 Add `Market` badge column to the realized-PnL list component
- [x] 7.2 Add `Native Amount` column rendered via `nativeAmount` pipe using the event's native cost / proceeds + `native_currency`
- [x] 7.3 Implement column-visibility flag derived from the dataset: both new columns hide when every row has `market === 'TW'`
- [x] 7.4 Unit test `realized-pnl-list.foreign-columns.spec.ts`: mixed dataset shows both columns, TW-only dataset hides them

## 8. Verify and ship

- [x] 8.1 Run `npm test` (Vitest) in `frontend/` — all new and existing tests pass
- [x] 8.2 Run `npm run build` in `frontend/` — production build succeeds, no new TypeScript errors
- [ ] 8.3 Start `npm start` against a dev backend that contains at least one TW + one US + one LSE holding (and one matching realized-PnL event each); visually verify per spec scenarios (deferred: no seeded dev backend with TW + US + LSE holdings/events is available in this workspace)
- [x] 8.4 Run `openspec validate add-foreign-markets-ui` and address any reported issues
- [ ] 8.5 Mark every task above complete (`- [x]`) before opening the PR (deferred: 8.3 remains a manual seeded-backend visual verification)
