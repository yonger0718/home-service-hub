## ADDED Requirements

### Requirement: Quote dispatcher routes by `(symbol, market)` to the correct fetcher

The service SHALL expose `quotes.dispatcher.refresh_daily_ohlc(db, items: list[tuple[str, str]]) -> RefreshResult` and `quotes.dispatcher.get_quotes(db, items: list[tuple[str, str]]) -> dict[tuple[str, str], Quote]`. Both SHALL group `items` by `market`, dispatch to the per-market fetcher, and aggregate results. The TW backend SHALL be the existing TWSE/TPEx path. The US and LSE backends SHALL be the new yfinance fetcher. Callers passing a bare `symbol` (no `market`) SHALL receive `market='TW'` semantics.

#### Scenario: TW items route to TWSE/TPEx fetcher

- **GIVEN** `items=[('2330', 'TW'), ('0050', 'TW')]`
- **WHEN** `refresh_daily_ohlc(db, items)` runs
- **THEN** the TWSE/TPEx fetcher SHALL be invoked with both symbols and the yfinance fetcher SHALL NOT be invoked

#### Scenario: US and LSE items route to yfinance fetcher

- **GIVEN** `items=[('AAPL', 'US'), ('VOD', 'LSE')]`
- **WHEN** `refresh_daily_ohlc(db, items)` runs
- **THEN** the yfinance fetcher SHALL be invoked with both items grouped by market and the TWSE/TPEx fetcher SHALL NOT be invoked

#### Scenario: Mixed batch dispatches per market

- **GIVEN** `items=[('2330', 'TW'), ('AAPL', 'US')]`
- **WHEN** `refresh_daily_ohlc(db, items)` runs
- **THEN** the TWSE/TPEx fetcher SHALL receive `['2330']` and the yfinance fetcher SHALL receive `[('AAPL', 'US')]`
- **AND** the returned `RefreshResult.ok_count` SHALL equal the sum of both fetchers' ok counts

#### Scenario: Unknown market is skipped with a warning

- **GIVEN** `items=[('XYZ', 'JP')]` where `JP` has no registered fetcher
- **WHEN** `refresh_daily_ohlc(db, items)` runs
- **THEN** the item SHALL be counted in `skipped_count` and an entry SHALL appear in `errors` naming the unsupported market
- **AND** no fetcher SHALL be invoked for that item

### Requirement: yfinance fetcher applies per-market symbol suffixes

The service SHALL expose `quotes.yfinance_fetcher.fetch(items: list[tuple[str, str]]) -> list[QuoteRow]` that maps each item's market to a yfinance suffix before requesting prices. Initial mapping SHALL be `{'US': '', 'LSE': '.L'}`. New markets SHALL be added by extending this mapping only.

#### Scenario: US ticker is requested without suffix

- **WHEN** `fetch([('AAPL', 'US')])` runs
- **THEN** the underlying yfinance request SHALL use the bare ticker string `'AAPL'`

#### Scenario: LSE ticker is requested with `.L` suffix

- **WHEN** `fetch([('VOD', 'LSE')])` runs
- **THEN** the underlying yfinance request SHALL use `'VOD.L'`

### Requirement: yfinance fetcher persists OHLC and native currency to `price_history`

For each ticker the yfinance fetcher SHALL upsert a `price_history` row keyed by `(symbol, market, date)` carrying daily `open`, `high`, `low`, `close`, `volume`, `source='yfinance'`, and the new `currency` column populated from yfinance `meta.currency`. The fetcher SHALL NOT transform the native price into TWD; FX conversion happens in the read path.

#### Scenario: Successful fetch writes native close and currency

- **GIVEN** yfinance returns `regularMarketPrice=190.50`, `currency='USD'` for `AAPL`
- **WHEN** `refresh_daily_ohlc(db, [('AAPL', 'US')])` runs
- **THEN** the persisted `price_history` row SHALL have `symbol='AAPL'`, `market='US'`, `close=Decimal('190.50')`, `currency='USD'`, `source='yfinance'`

#### Scenario: LSE GBp ticker stores native pence value with currency `'GBp'`

- **GIVEN** yfinance returns `regularMarketPrice=8050.0`, `currency='GBp'` for `VOD.L`
- **WHEN** `refresh_daily_ohlc(db, [('VOD', 'LSE')])` runs
- **THEN** the persisted row SHALL have `symbol='VOD'`, `market='LSE'`, `close=Decimal('8050.0')`, `currency='GBp'`
- **AND** no divide-by-100 normalization SHALL be applied at write time

### Requirement: yfinance fetcher isolates per-ticker failures

A single ticker raising or returning invalid data (missing `regularMarketPrice`, missing `currency`, non-numeric values) SHALL be skipped without aborting the batch. The fetcher SHALL log `event=quotes.yfinance.skip` with the failing symbol and reason, and SHALL include the entry in `RefreshResult.errors`.

#### Scenario: One bad ticker in a batch does not abort siblings

- **GIVEN** a batch `[('AAPL', 'US'), ('ZZZZ', 'US')]` where `AAPL` resolves cleanly and `ZZZZ` raises
- **WHEN** `refresh_daily_ohlc(db, items)` runs
- **THEN** the `AAPL` row SHALL be persisted, no `ZZZZ` row SHALL be written
- **AND** the result SHALL report `ok_count=1`, `skipped_count=1`, and one entry in `errors` naming `ZZZZ`

#### Scenario: Missing `meta.currency` is skipped

- **GIVEN** yfinance returns price data for a ticker but `meta.currency` is absent
- **WHEN** the fetcher processes the response
- **THEN** the ticker SHALL be skipped with reason `missing currency` and no `price_history` row SHALL be written

### Requirement: Daily scheduler job `foreign_price_refresh`

The service SHALL register an APScheduler job `foreign_price_refresh` running daily at `17:30 Asia/Taipei`, gated by `SCHEDULER_ENABLED=true`. The job SHALL select every distinct `(symbol, market)` pair from `transactions` where `market != 'TW'` and net open quantity is non-zero, then call `dispatcher.refresh_daily_ohlc` with that list. Failures SHALL log `event=foreign_price_refresh.{started,finished,failed}` and SHALL NOT crash the service.

#### Scenario: Job is registered at startup

- **WHEN** the service boots with `SCHEDULER_ENABLED=true`
- **THEN** the startup log line `event=scheduler.started` SHALL list a job with id `foreign_price_refresh` and trigger `cron[hour=17,minute=30,timezone=Asia/Taipei]`

#### Scenario: Job fetches only open foreign positions

- **GIVEN** the ledger contains an open US position in `AAPL`, a closed US position in `MSFT`, and an open TW position in `2330`
- **WHEN** the job fires
- **THEN** the dispatcher SHALL be invoked with `[('AAPL', 'US')]` only — `MSFT` (closed) and `2330` (TW) SHALL NOT appear in the call

#### Scenario: Empty ledger short-circuits

- **WHEN** no open foreign positions exist
- **THEN** the job SHALL exit without invoking the dispatcher and SHALL log `event=foreign_price_refresh.finished` with `ok_count=0, skipped_count=0`
