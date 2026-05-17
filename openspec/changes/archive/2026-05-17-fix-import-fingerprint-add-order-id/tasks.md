## 1. Backend — fingerprint + parser

- [x] 1.1 Add `"order_id"` to `TRANSACTION_HEADER_SYNONYMS` (English passthrough handled by `_normalize_header`'s `key in expected` check — but since `order_id` is not in `TRANSACTION_FIELDS`, add explicit synonyms map entries: `"order_id": "order_id"`, `"委託書號": "order_id"`, `"訂單編號": "order_id"`, `"委託編號": "order_id"`).
- [x] 1.2 In `_normalize_header`, ensure `order_id` is **not** added to the required-columns set — i.e. it stays optional. Verify the existing `required` lookup behaviour does the right thing (it keys on `expected`, so unless we add `order_id` to `TRANSACTION_FIELDS`, it's automatically optional).
- [x] 1.3 Extend `_transaction_fingerprint` signature: accept `order_id: str | None = None`. If `order_id` truthy after `.strip()`, append `f"|order_id={order_id}"` to canonical; else leave canonical unchanged (byte-for-byte legacy hash).
- [x] 1.4 In `parse_transactions_csv`, extract `order_id = (raw_row.get("order_id") or "").strip() or None`. Pass to `_transaction_fingerprint`. Also stash into `payload` for downstream visibility (consumed by frontend preview only; not persisted).
- [x] 1.5 Decide whether `TRANSACTION_FIELDS` should include `"order_id"`. **Decision**: leave it OUT — `TRANSACTION_FIELDS` is used both as the "canonical English aliases" set and as the no-header prepend list. Adding `order_id` there would force every header-less CSV to claim an `order_id` column, breaking back-compat. Instead, handle `order_id` purely through the synonyms map (which `_normalize_header` already treats as additive over `expected`). Add a comment in code explaining why `order_id` is synonyms-only.

## 2. Unit tests (`tests/unit/test_import_service.py`)

- [x] 2.1 `test_transaction_fingerprint_without_order_id_matches_legacy_hash` — call `_transaction_fingerprint` with no `order_id` and assert hash equals a known-value baseline (re-compute with the legacy canonical string inline to lock the format).
- [x] 2.2 `test_transaction_fingerprint_with_order_id_differs_from_baseline` — same inputs but `order_id="OD-1"` → different hash.
- [x] 2.3 `test_transaction_fingerprint_with_different_order_ids_are_distinct` — `order_id="A"` vs `order_id="B"`, all other fields equal → distinct hashes.
- [x] 2.4 `test_parse_transactions_identical_same_day_fills_distinct_with_order_ids` — feed CSV with two identical fills + distinct `order_id` values → `len(parsed.rows) == 2` and `parsed.rows[0].fingerprint != parsed.rows[1].fingerprint`.
- [x] 2.5 `test_parse_transactions_identical_same_day_fills_collide_without_order_ids` — same CSV without `order_id` column → both parse but `commit_transactions` reports `skipped_duplicates >= 1`.
- [x] 2.6 `test_parse_transactions_accepts_委託書號_synonym` — Chinese header `委託書號` maps to `order_id`, fingerprint includes the segment.
- [x] 2.7 `test_parse_transactions_whitespace_order_id_treated_as_empty` — `order_id="   "` → fingerprint matches legacy (no-order-id) hash.
- [x] 2.8 `test_parse_transactions_mixed_with_and_without_order_id` — two rows, same canonical inputs, one with `order_id` and one without → distinct fingerprints, both insertable.
- [x] 2.9 `test_commit_transactions_reimport_with_order_ids_is_noop` — commit CSV with order_ids; re-commit; `created == 0`, `skipped_duplicates == n`.

## 3. Frontend — user copy

- [x] 3.1 In `frontend/src/app/components/portfolio/import/import.ts`, extend the `transactions` `kindOptions[].hint` string to mention `order_id (委託書號, 訂單編號) — 選填，可區分同日同價同量交易`.
- [x] 3.2 No other frontend changes required; the upload endpoint signature and result types are unchanged.

## 4. Validation

- [x] 4.1 `openspec validate fix-import-fingerprint-add-order-id --strict` passes.
- [x] 4.2 `cd services/stock-portfolio-service && pytest tests/unit/test_import_service.py -v` all green.
- [x] 4.3 `cd services/stock-portfolio-service && pytest` full suite — no regressions in import-orchestration tests from PR #4 either.

## 5. Manual smoke (deferred until broker comes back)

- [ ] 5.1 Export real broker CSV with `委託書號` populated → upload → verify duplicate fills both import.
- [ ] 5.2 Re-upload same CSV → all duplicates, zero new rows.
- [ ] 5.3 Upload an old pre-feature CSV (no `order_id`) over an existing dataset → no spurious re-imports.
