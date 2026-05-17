## ADDED Requirements

### Requirement: 國泰證券 CSV format is recognised and parsed without manual pre-processing

The CSV import path SHALL recognise 國泰證券's `證券對帳單` export by sniffing the first non-empty line for the preamble pattern `^根據您篩選的結果` and routing such files to a 國泰-specific parser. The 國泰 parser SHALL skip the preamble row, read the next row as headers, and accept these columns: `股名`, `日期`, `成交股數`, `淨收付金額`, `買賣別`, `成交價`, `成本`, `手續費`, `交易稅`, `融資金額/券擔保品`, `資自備款/券保證金`, `利息`, `稅款`, `券手續費/標借費`, `委託書號`.

Non-國泰 CSVs (no preamble) SHALL continue to be routed to the existing generic parser unchanged.

#### Scenario: 國泰 CSV with preamble is parsed end-to-end

- **GIVEN** a CSV file whose first line begins with `根據您篩選的結果` and whose second line is the 國泰 header row
- **WHEN** uploaded to `POST /api/portfolio/imports/transactions`
- **THEN** the preamble row SHALL be skipped, the header row SHALL be read, and each data row SHALL be parsed into a transaction payload with `symbol`, `type`, `quantity`, `price`, `trade_date`, `fee`, `tax`, `order_id`, and `broker_subtype` keys

#### Scenario: Non-國泰 CSV is unaffected

- **GIVEN** a CSV file with English headers (e.g. `symbol,type,quantity,...`) and no preamble
- **WHEN** uploaded to the same endpoint
- **THEN** the file SHALL be routed to the existing generic parser and produce identical results to the pre-feature implementation

### Requirement: Chinese stock name resolves to ticker symbol via bundled reverse map plus optional caller-supplied overrides

The 國泰 parser SHALL resolve `股名` (Chinese stock name) to a ticker symbol via a two-tier lookup, in order:

1. **Caller-supplied `name_overrides`** — an optional `dict[name, symbol]` passed through the endpoint as a JSON-encoded multipart form field `name_overrides`. If a name is present in the overrides dict, the override SHALL be used unconditionally (overrides win over the static map).
2. **Bundled `name_to_symbol` map** — shipped under `app/data/name_to_symbol.json`, generated from the `twstock` package's bundled code table (~42K entries covering TWSE listed, TPEx OTC, ETFs, ETNs, warrants). Loaded once at module-import and reverse-indexed so a name maps to a list of candidate symbols.

Unresolved names (missing from both overrides and static map) SHALL NOT roll back the batch. Instead they SHALL be collected into `ParseResult.unresolved_names` and the corresponding rows SHALL be skipped from the commit pipeline (reported as `skipped_unresolved` in the result, not `errors`).

#### Scenario: Unique name resolves cleanly via static map

- **GIVEN** a CSV row with `股名='晶宏'` where `name_to_symbol.json` maps `晶宏` to a single symbol and no override is supplied
- **WHEN** the row is parsed
- **THEN** the resulting payload SHALL contain `symbol` equal to that single ticker

#### Scenario: Override wins over static map

- **GIVEN** a CSV row with `股名='台積電'` (static map: `[2330]`) AND `name_overrides={"台積電": "2330B"}` is supplied
- **WHEN** the row is parsed
- **THEN** the resulting payload SHALL contain `symbol='2330B'`

#### Scenario: Override resolves a name absent from the static map

- **GIVEN** a CSV row with `股名='新光金'` not present in the static map AND `name_overrides={"新光金": "2888"}` is supplied
- **WHEN** the row is parsed
- **THEN** the resulting payload SHALL contain `symbol='2888'`

#### Scenario: Unknown name without override is collected, not raised

- **GIVEN** a CSV row with `股名='元太元大18售19'` (a delisted warrant) absent from both overrides and the static map
- **WHEN** the row is parsed
- **THEN** the row SHALL NOT appear in `ParseResult.rows`
- **AND** the name SHALL appear in `ParseResult.unresolved_names` with its occurrence count and up to 3 sample trade dates
- **AND** no `errors` entry SHALL be created for this name (it is a soft-skip, not a hard error)

#### Scenario: Ambiguous name produces a row-level hard error (still rolls back)

- **GIVEN** a CSV row with `股名='元大23購15'` where the static map yields 2 or more candidates AND no override resolves the ambiguity
- **WHEN** the row is parsed
- **THEN** the row SHALL be reported in `errors` with message containing `ambiguous symbol for 股名='元大23購15'` followed by the candidate list
- **AND** the whole batch SHALL roll back (ambiguous mapping is an operator-intervention case, distinct from "name simply unknown")

### Requirement: 國泰 type vocabulary collapses to BUY / SELL

The 國泰 parser SHALL translate `買賣別` values to the home-hub `transactions.type` enum as follows:

| `買賣別` | `type` |
|---|---|
| `現買`, `資買`, `券買`, `沖買` | `BUY` |
| `現賣`, `資賣`, `券賣`, `沖賣` | `SELL` |

The two-character prefix (`現`, `資`, `券`, `沖`) SHALL be preserved in the parsed payload as `broker_subtype` for frontend display only and SHALL NOT be included in the `import_fingerprint` canonical string nor persisted to the database.

#### Scenario: Margin BUY collapses to BUY with subtype 資

- **GIVEN** a CSV row with `買賣別='資買'`
- **WHEN** the row is parsed
- **THEN** the payload SHALL have `type='BUY'` and `broker_subtype='資'`

#### Scenario: Day-trade SELL collapses to SELL with subtype 沖

- **GIVEN** a CSV row with `買賣別='沖賣'`
- **WHEN** the row is parsed
- **THEN** the payload SHALL have `type='SELL'` and `broker_subtype='沖'`

#### Scenario: Subtype does not affect fingerprint

- **GIVEN** two CSV rows identical in every fingerprint field but one with `買賣別='現買'` and one with `買賣別='資買'`
- **WHEN** both are parsed (both become `type='BUY'`)
- **THEN** their `import_fingerprint` values SHALL be equal (the `broker_subtype` segment is NOT in the hash)

### Requirement: Smart rehash backfill rewrites existing rows' fingerprints in place

Whenever a CSV is dispatched to the 國泰 parser (i.e. `detect_csv_format` returned `cathay`), the parser SHALL run in rehash mode unconditionally — there is no opt-in flag. For each CSV row:

1. Compute the **legacy** fingerprint by calling `_transaction_fingerprint` WITHOUT `order_id`.
2. Look up an existing `transactions` row by `import_fingerprint = legacy_fingerprint`.
3. If a row is found, recompute the **new** fingerprint WITH `order_id` and `UPDATE` the row's `import_fingerprint` in place; report this row as `rehashed`.
4. If no row is found, insert a new transaction with the **new** fingerprint; report this row as `created` (or `skipped_duplicates` if the new fingerprint already exists in the DB).

The whole batch SHALL run inside a single database transaction. Any row error (name resolution, parse failure, integrity violation) SHALL roll back all rehashes from the batch.

#### Scenario: Existing legacy-hash row is rehashed in place, no duplicate inserted

- **GIVEN** a `transactions` row exists with the legacy fingerprint `H_legacy` for `(symbol=0050, type=BUY, qty=1000, price=50, date=2026-05-08, fee=22, tax=0)`
- **AND** a 國泰 CSV contains exactly one row matching those fields plus `order_id='aT532'`
- **WHEN** the CSV is uploaded with `dry_run=false`
- **THEN** the existing row's `import_fingerprint` SHALL be updated to `H_with_order_id` (the new hash including `|order_id=aT532`)
- **AND** the import result SHALL report `rehashed=1`, `created=0`, `skipped_duplicates=0`
- **AND** no new `transactions` row SHALL be inserted

#### Scenario: Same-day collision twin previously dropped is recovered

- **GIVEN** a single `transactions` row exists with `H_legacy` representing two identical same-day fills that collided pre-fix (one of two real fills silently dropped)
- **AND** a 國泰 CSV contains both fills with distinct `order_id` values `aT532` and `aT699`
- **WHEN** the CSV is uploaded
- **THEN** the existing DB row SHALL be rehashed to `H_with_order_id=aT532` (first match wins)
- **AND** the second CSV row SHALL find no row at `H_legacy` (just rewritten), fall through to insert with `H_with_order_id=aT699`
- **AND** the import result SHALL report `rehashed=1`, `created=1` — twin recovered

#### Scenario: Rehash of a CSV with no DB matches is equivalent to a normal import

- **GIVEN** a `transactions` table empty of any rows matching a 國泰 CSV
- **WHEN** the CSV is uploaded
- **THEN** every row SHALL fall through to the insert path and report as `created`
- **AND** `rehashed=0`

#### Scenario: Re-running rehash on already-rehashed data is idempotent

- **GIVEN** a 國泰 CSV has already been successfully uploaded
- **WHEN** the same CSV is uploaded again
- **THEN** for each row, no DB row will match `H_legacy` (all are now keyed by `H_with_order_id`)
- **AND** every row SHALL try the insert path, hit `UNIQUE` on `H_with_order_id`, and be reported as `skipped_duplicates`
- **AND** no DB row SHALL be modified

#### Scenario: Hard row error rolls back the entire batch

- **GIVEN** a 國泰 CSV where row 500 of 1997 contains an **ambiguous** `股名` (or a malformed date, malformed quantity, etc.) — a *hard* error, not an unresolvable name
- **WHEN** the CSV is uploaded with `dry_run=false`
- **THEN** the transaction SHALL be rolled back
- **AND** no `import_fingerprint` updates SHALL persist
- **AND** the response SHALL surface the row-500 error along with any other hard errors detected during parsing

#### Scenario: Unresolved names do not roll back the batch

- **GIVEN** a 國泰 CSV of 1997 rows where 104 rows reference 14 unique names absent from both static map and overrides
- **WHEN** the CSV is uploaded with `dry_run=false` and no other errors are present
- **THEN** the 1893 resolvable rows SHALL be committed (rehashed or inserted per the normal rules)
- **AND** the 104 unresolved rows SHALL be skipped, reported as `skipped_unresolved=104`
- **AND** `unresolved_names` SHALL list the 14 names with occurrence counts and sample dates

### Requirement: Endpoint exposes name_overrides and reports unresolved names

The `POST /api/portfolio/imports/transactions` endpoint SHALL accept an optional multipart form field `name_overrides`: a JSON-encoded `dict[str, str]` mapping Chinese stock names to ticker symbols. The field is unconditionally accepted by both generic and 國泰 paths but only the 國泰 path consumes it.

The response shape SHALL include:
- `skipped_unresolved: int = 0` — count of rows skipped because their `股名` was unresolvable
- `unresolved_names: list[{name: str, occurrences: int, sample_dates: list[str]}]` — empty in the generic path; populated by the 國泰 path

The generic-path response SHALL always set `skipped_unresolved=0` and `unresolved_names=[]` so the wire shape is consistent across format paths.

#### Scenario: Endpoint accepts name_overrides as a JSON form field

- **GIVEN** a multipart POST to `/api/portfolio/imports/transactions` with a 國泰 CSV file AND form field `name_overrides='{"新光金":"2888"}'`
- **WHEN** the request is processed
- **THEN** rows where `股名='新光金'` SHALL be resolved to symbol `2888` and processed normally

#### Scenario: Endpoint returns unresolved_names in response

- **GIVEN** a 國泰 CSV with 14 unique names absent from both overrides and static map (104 rows)
- **WHEN** the CSV is uploaded with `dry_run=true`
- **THEN** the response SHALL include `skipped_unresolved=104`
- **AND** `unresolved_names` SHALL contain 14 entries, each with `name`, `occurrences`, and `sample_dates` (up to 3 most recent dates per name)

#### Scenario: Endpoint generic-path response preserves consistent shape

- **GIVEN** a generic English CSV (no preamble) uploaded to `/api/portfolio/imports/transactions`
- **WHEN** the request is processed
- **THEN** the response SHALL include `skipped_unresolved=0` and `unresolved_names=[]` (no removal from wire shape)

### Requirement: User-supplied overrides are per-row verified against TWSE/TPEx historical data

When the 國泰 parser is invoked with `name_overrides` and any rows reference an override, the parser SHALL verify each unique `(override_name, override_code)` pair against historical market data on the earliest trade date appearing in the CSV for that name. Verification SHALL use:
- **TWSE** `https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date=YYYYMMDD&stockNo=CODE` for 4-digit numeric codes
- **TPEx** `https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?date=YYYY/MM/DD&code=CODE&response=json` for 6-character codes (warrants, etc.)
- For codes that don't match either pattern, both endpoints SHALL be attempted in TWSE → TPEx order

Verification results SHALL be cached in-process per `(code, yyyymmdd)` so re-running dry-run with the same overrides incurs zero additional network calls.

The response SHALL include `override_validations: list[OverrideValidation]` where each entry has `{name, code, status, expected_name?, fetched_name?}` with `status` ∈ `{verified, name_mismatch, not_traded_on_date, fetch_failed, user_overridden}`. The response SHALL include `skipped_unverified: int` — count of rows whose override resolved to a non-verified, non-confirmed status.

`auto_resolved` rows (resolved via static twstock map without an override) SHALL NOT be verified. Verification applies only to user-supplied overrides.

#### Scenario: Override with matching name on trade date is verified

- **GIVEN** a 國泰 CSV row with `股名='新光金'` on `2021/07/30` AND override `name_overrides={'新光金':'2888'}`
- **AND** TWSE historical query for `(2888, 20210730)` returns `title: '109 年7月 2888 新光金 個股日成交資訊'`
- **WHEN** the dry-run runs
- **THEN** `override_validations` SHALL include `{name:'新光金', code:'2888', status:'verified', fetched_name:'新光金'}`
- **AND** the row SHALL be eligible for commit

#### Scenario: Override code returns different name → name_mismatch, row skipped

- **GIVEN** override `name_overrides={'新光金':'2887'}` and CSV row with 股名='新光金'
- **AND** TWSE returns name `'台新新光金'` for code `2887` on that date
- **WHEN** dry-run runs
- **THEN** `override_validations[0].status == 'name_mismatch'` with `expected_name='新光金', fetched_name='台新新光金'`
- **AND** rows for `股名='新光金'` SHALL be counted in `skipped_unverified` and NOT committed unless the user explicitly re-submits with `confirmed_overrides=['新光金']`

#### Scenario: Override code wasn't traded on that date → not_traded_on_date, row skipped

- **GIVEN** override that doesn't appear in TWSE/TPEx response for the trade date
- **WHEN** dry-run runs
- **THEN** `override_validations[0].status == 'not_traded_on_date'`
- **AND** rows are skipped unless confirmed

#### Scenario: TWSE/TPEx returns HTML/error → fetch_failed, soft warning

- **GIVEN** the historical endpoint returns non-JSON or 5xx
- **WHEN** dry-run runs
- **THEN** `override_validations[0].status == 'fetch_failed'`
- **AND** rows are skipped by default; user can confirm-anyway via `confirmed_overrides`

#### Scenario: User-confirmed override commits despite mismatch

- **GIVEN** prior dry-run returned `name_mismatch` for `'新光金' → '2887'`
- **AND** user re-submits with `name_overrides={'新光金':'2887'}` AND `confirmed_overrides=['新光金']`
- **WHEN** verification re-runs (cache-hit, same result)
- **THEN** `override_validations[0].status == 'user_overridden'`
- **AND** rows commit normally

#### Scenario: Re-dry-run with same overrides hits cache, no network

- **GIVEN** an earlier dry-run already verified `(2888, 20210730)`
- **WHEN** the same CSV+overrides are uploaded again
- **THEN** no outbound HTTP request SHALL be made for that `(code, date)` pair
- **AND** the cached `verified` status SHALL be returned

#### Scenario: Atomicity is per-row, not per-batch

- **GIVEN** a CSV resulting in `1893 auto_resolved + 11 verified + 90 name_mismatch + 3 not_traded_on_date` rows
- **WHEN** commit runs without any confirmations
- **THEN** `1904` rows SHALL be committed (1893 + 11 verified)
- **AND** `93` rows SHALL be reported in `skipped_unverified` (90 + 3)
- **AND** no other rows SHALL be rolled back

### Requirement: Dry-run mode previews rehash counts without writing

When a 國泰 CSV is uploaded with `dry_run=true`, the parser SHALL compute the same per-row classification (rehash candidate vs. new insert vs. error) but SHALL NOT write any changes to the database. The response SHALL include `would_rehash`, `would_insert`, `would_skip_duplicate`, and the full `errors` list.

#### Scenario: Dry-run on 國泰 CSV reports exact counts and writes nothing

- **GIVEN** a 國泰 CSV that, if committed, would rehash 1995 rows, insert 2 new same-day-collision twins, and surface 0 errors
- **WHEN** the CSV is uploaded with `dry_run=true`
- **THEN** the response SHALL report `would_rehash=1995`, `would_insert=2`, `would_skip_duplicate=0`, `errors=[]`
- **AND** no `transactions` row SHALL be modified or inserted
