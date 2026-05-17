## 1. Data asset — name_to_symbol map

- [x] 1.1 Add `services/stock-portfolio-service/app/data/name_to_symbol.json` by copying from `/home/opc/workspace/stonk/code_name_map.json` (mapping is `symbol -> name`; will be reverse-indexed at load time, not pre-reversed on disk so it stays diff-friendly with upstream).
- [x] 1.2 Add a one-liner `scripts/refresh_name_to_symbol.py` that re-pulls the JSON from stonk (or from a TWSE/TPEx list source if stonk is gone). Document refresh procedure in `services/stock-portfolio-service/README.md` (create if missing — short note only).
- [x] 1.3 Loader in `app/services/broker_cathay_service.py`: read JSON once at module import, build `NAME_TO_SYMBOL: dict[str, list[str]]` reverse index, expose `resolve_symbol(name: str) -> str` that raises `ValueError` on 0 or 2+ matches with a structured message (per spec scenarios).

## 2. CSV format dispatcher

- [x] 2.1 New helper `detect_csv_format(raw_bytes: bytes) -> Literal["generic", "cathay"]` in `app/services/import_service.py`. Decode utf-8-sig, inspect first non-empty line: `^根據您篩選的結果` → `"cathay"`, else `"generic"`. Unit test both branches plus an edge case with leading blank lines.
- [x] 2.2 Refactor the FastAPI handler in `app/routers/imports.py` (or wherever it lives) to call the dispatcher and route to either `parse_transactions_csv` (existing) or new `parse_cathay_transactions_csv` (to be built). No other behavior change in this task.

## 3. 國泰 parser

- [x] 3.1 New module `app/services/broker_cathay_service.py` exposing `parse_cathay_transactions_csv(raw_bytes: bytes, *, dry_run: bool, db: Session, name_overrides: dict[str, str] | None = None, confirmed_overrides: set[str] | None = None) -> ImportResult` (rehash auto-applied on the cathay dispatch path — no caller-facing toggle).
- [x] 3.2 Inside the parser: open with `csv.reader`, drop the preamble line (`readline()` once before constructing `DictReader`), then `DictReader` to enumerate data rows.
- [x] 3.3 Per-row pipeline: resolve `股名 → symbol` (task 1.3); map `買賣別 → type` via `CATHAY_SIDE_MAP` and split prefix into `broker_subtype`; parse `日期` as `YYYY/MM/DD` → `date`; strip comma thousands separators from `成交股數`, `成交價`, `手續費`, `交易稅`; carry `委託書號 → order_id` (empty string → `None`); ignore all margin/interest columns; populate `payload` dict including `broker_subtype`.
- [x] 3.4 Compute fingerprint via existing `_transaction_fingerprint` from `import_service` (re-export from there or import directly). DO NOT duplicate the hash function.

## 4. Rehash mode

- [x] 4.1 When `rehash=True`: for each row compute both `legacy_fp` (no `order_id`) and `new_fp` (with `order_id`). Lookup `Transaction` by `import_fingerprint=legacy_fp`. If found, set `existing.import_fingerprint = new_fp` and mark row as `rehashed`. If not found, attempt insert with `new_fp`; on `IntegrityError` (unique violation), mark as `skipped_duplicates`.
- [x] 4.2 Wrap whole batch in a single `db.begin()`/`commit()` block. Any row error (`ValueError` from name resolution, `IntegrityError`, CSV parse error) → `db.rollback()` and surface all collected errors in the response.
- [x] 4.3 In `dry_run=True` mode: same per-row classification but no writes — count `would_rehash`, `would_insert`, `would_skip_duplicate` by SELECTing existence of `legacy_fp` and `new_fp` without UPDATE/INSERT. Return counts in response shape per spec.
- [x] 4.4 ~~Router rejection for `rehash=true` on non-國泰 CSV~~ — N/A after design revision: the `rehash` query param was dropped entirely. Rehash logic is now auto-applied whenever `detect_csv_format` returns `"cathay"` (safe + idempotent per spec). No user-facing flag, no UI toggle, no rejection check needed.

## 5. Endpoint shape

- [x] 5.1 ~~Add `rehash` query param~~ — N/A after design revision: no query param added. Router auto-dispatches to cathay parser (always rehash-mode) when `detect_csv_format` returns `"cathay"`. Doc string updated to describe auto-routing.
- [x] 5.2 Extend `ImportResult` schema (in `app/schemas/imports.py` or wherever defined) with optional `rehashed: int = 0`, `would_rehash`, `would_insert`, `would_skip_duplicate` fields — non-breaking for existing generic-path consumers (default 0).
- [x] 5.3 Update existing generic-path callsites to populate `rehashed=0` explicitly so the field is always present in responses.

## 6. Unit tests (`tests/unit/test_broker_cathay_service.py`, NEW)

- [x] 6.1 `test_detect_csv_format_cathay_preamble` — first line `根據您篩選的結果...` → `"cathay"`.
- [x] 6.2 `test_detect_csv_format_generic_english_header` → `"generic"`.
- [x] 6.3 `test_detect_csv_format_blank_leading_lines` — preamble after blank lines still detected.
- [x] 6.4 `test_resolve_symbol_unique` — `晶宏` → expected single ticker.
- [x] 6.5 `test_resolve_symbol_unknown_raises_with_message` — error message contains `cannot resolve symbol for 股名='不存在'`.
- [x] 6.6 `test_resolve_symbol_ambiguous_raises_with_candidates` — fixture with 2 candidates → error lists both.
- [x] 6.7 `test_cathay_type_collapse_all_eight_variants` — parametrised over all 8 `買賣別` values; assert `type` ∈ {BUY, SELL} and `broker_subtype` ∈ {現, 資, 券, 沖}.
- [x] 6.8 `test_cathay_subtype_does_not_affect_fingerprint` — two rows identical except `現買` vs `資買` → same fingerprint.
- [x] 6.9 `test_cathay_parser_skips_preamble_and_reads_header` — feed a 3-line CSV (preamble + header + 1 data row) → 1 payload returned.
- [x] 6.10 `test_cathay_thousands_separator_in_quantity_and_price` — `"1,000"` and `"56,322"` parse correctly.
- [x] 6.11 `test_rehash_existing_legacy_row_updates_in_place_no_insert` — seed DB row with `legacy_fp`, run rehash, assert `rehashed=1, created=0`, assert row's `import_fingerprint` now equals `new_fp`.
- [x] 6.12 `test_rehash_recovers_same_day_collision_twin` — seed 1 row at `legacy_fp` representing collision, feed CSV with 2 rows + distinct `order_id`, assert `rehashed=1, created=1`, final DB has 2 rows.
- [x] 6.13 `test_rehash_idempotent_second_run_all_skipped` — run rehash twice, second run reports `skipped_duplicates == N`, `rehashed=0`, `created=0`.
- [x] 6.14 `test_unresolved_name_collected_not_raised` — CSV with one unknown 股名 → row skipped + accumulated into `unresolved_names`; resolvable rows continue and the batch is NOT rolled back. (Post-revision semantics from section 11.5 — unknown names surface to the override UX, only `ambiguous symbol` matches still hard-fail.)
- [x] 6.15 `test_dry_run_rehash_reports_counts_writes_nothing` — assert response counts non-zero, DB row count and hashes unchanged.
- [x] 6.16 ~~`test_rehash_on_generic_csv_rejected_with_400`~~ — N/A after design revision (no `rehash` query param exists; nothing to reject). Test removed.

## 7. Frontend — minimal surface

- [x] 7.1 ~~Checkbox `重算指紋…`~~ — N/A after design revision: no toggle. Rehash auto-applies on 國泰 uploads.
- [x] 7.2 ~~`uploadCsv()` `rehash` param~~ — N/A after design revision: signature unchanged from generic-path baseline.
- [x] 7.3 In the import result panel template, when `rehashed > 0` show a Chinese summary line: `重算指紋 N 筆` — kept; useful post-hoc feedback when auto-rehash ran.

## 8. Validation

- [x] 8.1 `openspec validate import-cathay-broker-csv --strict` passes.
- [x] 8.2 `cd services/stock-portfolio-service && pytest tests/unit/test_broker_cathay_service.py -v` all green (23/23).
- [x] 8.3 `cd services/stock-portfolio-service && pytest` full suite — 362/362, no regression in generic-import or PR #5 fingerprint tests.
- [x] 8.4 `cd frontend && npm test -- --watch=false` — 10/10, no regression (codex note: `--run` flag not supported by this repo's Angular 21/Vitest setup; `--watch=false` is the supported equivalent).

## 9. Manual smoke (deferred until broker comes back)

- [ ] 9.1 Upload real 1997-row CSV with `dry_run=true` → verify `would_rehash` + `would_insert` counts, errors=0.
- [ ] 9.2 Upload same CSV with `dry_run=false` → DB row count increases by `would_insert` only; spot-check a known `(symbol, date, qty, price)` triplet has its `import_fingerprint` now containing `|order_id=`.
- [ ] 9.3 Re-upload same CSV → `skipped_duplicates == 1997`, no DB changes (idempotency).

## 10. Enrichment — twstock master map

- [x] 10.1 Replace 13-entry stonk-derived `app/data/name_to_symbol.json` with the ~42K-entry full twstock bundle (covers TWSE listed + TPEx OTC + ETFs + ETNs + active warrants).
- [x] 10.2 Rewrite `scripts/refresh_name_to_symbol.py` to regenerate from `twstock.codes` instead of copying from stonk.
- [x] 10.3 Verified `twstock==1.5.1` already in `requirements.txt` (no dep change).
- [x] 10.4 Coverage check against real 1997-row CSV: 161/175 unique names resolved (92%). 14 misses are delisted warrants + `新光金` (post-merger), responsible for 104 rows (5.2% of total). Drives section 11 (manual override UX).

## 11. Manual name-override UX (option B per chat)

Goal: instead of rolling back on unresolved names, surface the unresolved list to the user and let them paste a name→ticker map. Re-preview applies the overrides.

### Backend

- [x] 11.1 `broker_cathay_service.parse_cathay_rows(raw, *, name_overrides: dict[str, str] | None = None)` — accept overrides; check overrides BEFORE static map.
- [x] 11.2 New dataclass `@dataclass UnresolvedName { name: str, occurrences: int, sample_dates: list[str] }` exported from `broker_cathay_service` (or `import_service` if shared).
- [x] 11.3 Extend `ParseResult` (shared with generic path) with `unresolved_names: list[UnresolvedName] = field(default_factory=list)`. Generic path always empty.
- [x] 11.4 Extend `ImportResult` with `skipped_unresolved: int = 0`. Generic path stays 0.
- [x] 11.5 Change cathay row pipeline: on `resolve_symbol` ValueError whose message contains "cannot resolve" (unknown name) OR "ambiguous symbol" (multiple candidates in `NAME_TO_SYMBOL`) → accumulate into `unresolved_names` and skip the row; do NOT roll back. Both flow into the same override UX so the user can pick the right ticker. Any other ValueError still propagates as a hard error.
- [x] 11.6 Router: accept multipart form field `name_overrides: str = Form(default="")` (JSON-encoded). Parse to `dict[str, str]` (or `{}` if empty). Pass to `parse_cathay_transactions_csv`. Generic path ignores it.
- [x] 11.7 `parse_cathay_transactions_csv(raw, *, dry_run, db, name_overrides)` — plumb through.
- [x] 11.8 `_serialize_result` in router includes `skipped_unresolved` + `unresolved_names`. Generic path defaults both to `0`/`[]`.

### Frontend

- [ ] 11.9 Models: extend `ImportResult` with `skipped_unresolved?: number` and `unresolved_names?: { name: string; occurrences: number; sample_dates: string[] }[]`.
- [ ] 11.10 `PortfolioService.uploadCsv` adds optional `nameOverrides?: Record<string, string>` param; when non-empty serialise to JSON and append `name_overrides` to FormData.
- [ ] 11.11 `import.ts`: new signal `nameOverrides = signal<Record<string, string>>({})`. After preview, if `result.unresolved_names?.length > 0` render override panel.
- [ ] 11.12 `import.html`: new panel "未識別股名（請手動填入代號）" — table of unresolved names with `<input>` per row bound to `nameOverrides[name]`. Show `occurrences` + `sample_dates`. New button "再次預覽（套用對應）" that runs `upload(true)` with current `nameOverrides`. Commit button gains tooltip when unresolved-names > 0 and overrides incomplete.
- [ ] 11.13 Result-panel summary line: when `skipped_unresolved > 0` show `⚠️ 未識別 {{ count }} 筆，將略過`.

### Tests (`tests/unit/test_broker_cathay_service.py`)

- [x] 11.14 `test_name_overrides_resolves_name_absent_from_static_map`.
- [x] 11.15 `test_name_overrides_wins_over_static_map`.
- [x] 11.16 `test_unresolved_name_collected_not_raised` — feed CSV with name not in map+overrides → row skipped, `unresolved_names` populated, no error, `skipped_unresolved` incremented.
- [x] 11.17 `test_unresolved_does_not_rollback_resolved_rows` — feed 3-row CSV (2 resolvable + 1 unresolvable) → 2 committed, 1 skipped, `unresolved_names` lists the 1.
- [x] 11.18 `test_ambiguous_name_still_rolls_back_batch` — confirm hard-error semantics retained for ambiguous (distinct from unknown).
- [x] 11.19 `test_endpoint_accepts_name_overrides_form_field` — full HTTP roundtrip via `client` fixture with form data.
- [x] 11.20 `test_generic_path_response_has_empty_unresolved_names_for_shape_consistency`.

### Validation

- [x] 11.21 `openspec validate import-cathay-broker-csv --strict` passes.
- [x] 11.22 `cd services/stock-portfolio-service && .venv/bin/pytest` full suite green.
- [ ] 11.23 `cd frontend && npx tsc --noEmit -p tsconfig.app.json && npm test -- --watch=false` green.

## 12. Per-date override verification (TWSE/TPEx historical fetch)

Goal: when user supplies a `name_overrides` entry, verify that the override code → name pair matches the broker CSV's `股名` on the row's trade date. Catches typos with high confidence. Mandatory dry-run flow.

### Backend

- [x] 12.1 New module `app/services/per_date_verify.py` with:
  - `@dataclass OverrideValidation { name: str, code: str, status: Literal["verified","name_mismatch","not_traded_on_date","fetch_failed","user_overridden"], expected_name: str | None, fetched_name: str | None }`
  - `fetch_name_for_date(code: str, trade_date: date) -> str | None` — TWSE for 4-digit codes, TPEx for 6-character. On error return None and tag the cache entry as `"error"` so callers can distinguish.
  - Module-level cache `_NAME_CACHE: dict[tuple[str, str], str | None | Literal["error"]]` keyed by `(code, "YYYYMMDD")`.
  - `verify_overrides(*, name_to_code: dict[str, str], name_to_earliest_date: dict[str, date], confirmed: set[str]) -> list[OverrideValidation]` — main entry point.
- [x] 12.2 Wire `verify_overrides` into `broker_cathay_service.parse_cathay_transactions_csv`:
  - After `parse_cathay_rows`, compute `name_to_earliest_date` from parsed.rows (group by `payload['name']`, min `trade_date.date()`).
  - Compute `name_to_code = name_overrides ∩ (names actually present in CSV)`.
  - Call `verify_overrides(...)`.
  - Filter parsed.rows: for each row whose `payload['name']` had override with status NOT in `{verified, user_overridden}` → move to a new `skipped_unverified_rows` bucket, do NOT pass to commit pipeline.
- [x] 12.3 Extend `ImportResult` with:
  - `skipped_unverified: int = 0`
  - `override_validations: list[OverrideValidation] = field(default_factory=list)`
- [x] 12.4 Router accepts new optional form field `confirmed_overrides: str = Form(default="")` — JSON list of names that user explicitly confirmed despite warnings. Parse to `set[str]`. Pass through to `parse_cathay_transactions_csv`.
- [x] 12.5 Add to `_serialize_result`: `skipped_unverified`, `override_validations` (as list of dicts).

### Tests (`tests/unit/test_per_date_verify.py`, NEW)

- [x] 12.6 `test_fetch_twse_returns_name_for_4digit_code` — mock `requests.get` returning canonical TWSE JSON, assert name parsed.
- [x] 12.7 `test_fetch_tpex_returns_name_for_6char_warrant_code` — mock TPEx response.
- [x] 12.8 `test_fetch_cache_hits_no_second_call` — patched HTTP, call twice, assert exactly 1 outbound call.
- [x] 12.9 `test_fetch_returns_none_on_http_error` — mock 500/HTML response.
- [x] 12.10 `test_verify_marks_matching_name_as_verified`.
- [x] 12.11 `test_verify_marks_different_name_as_name_mismatch` with expected/fetched populated.
- [x] 12.12 `test_verify_marks_empty_response_as_not_traded_on_date`.
- [x] 12.13 `test_verify_respects_confirmed_set_overrides_to_user_overridden`.

### Tests (`tests/unit/test_broker_cathay_service.py`, append)

- [x] 12.14 `test_override_with_name_mismatch_skips_rows_keeps_other_rows_committable` — 2 rows: 1 auto-resolved + 1 override-mismatch. Expect 1 row in commit pipeline, 1 in `skipped_unverified`. Mock per_date_verify.
- [x] 12.15 `test_confirmed_overrides_allow_mismatched_rows_to_commit` — same setup but `confirmed_overrides={'新光金'}` → both rows commit.
- [x] 12.16 `test_auto_resolved_rows_not_sent_to_per_date_verify` — patched verify; assert it's only called with override names, never auto-resolved ones.

### Frontend

- [ ] 12.17 `ImportResult` interface: add `skipped_unverified?: number` and `override_validations?: { name, code, status, expected_name?, fetched_name? }[]`.
- [ ] 12.18 `PortfolioService.uploadCsv` adds optional `confirmedOverrides?: string[]` param; non-empty → JSON-stringify and append `confirmed_overrides` to FormData.
- [ ] 12.19 `import.ts`: new signal `confirmedOverrides = signal<Set<string>>(new Set())`.
- [ ] 12.20 `import.html`: in unresolved-names table, add a status icon column bound to `validationStatus(u.name)` helper that reads from `result()?.override_validations`. Icon map:
  - `verified` / `user_overridden` → ✓ green
  - `name_mismatch` → ⚠️ yellow + tooltip showing `fetched_name`
  - `not_traded_on_date` → ⚠️ orange + tooltip
  - `fetch_failed` → ❓ grey + tooltip
- [ ] 12.21 For statuses requiring confirmation, render a checkbox `<p-checkbox [(ngModel)]="...">確認此代號`. Toggling adds/removes from `confirmedOverrides`.
- [ ] 12.22 Commit button: enabled always; show summary tooltip `已驗證 X 筆，將略過 Y 筆未驗證`. No hard block — per-row partial commit per the design.
- [ ] 12.23 After "套用對應並再次預覽": send overrides + confirmed-overrides via uploadCsv.

### Validation

- [ ] 12.24 `openspec validate import-cathay-broker-csv --strict` passes.
- [ ] 12.25 `cd services/stock-portfolio-service && .venv/bin/pytest` full suite green.
- [ ] 12.26 `cd frontend && npx tsc --noEmit -p tsconfig.app.json && npm test -- --watch=false` green.
