## Why

The split (多類別) editor is being reworked (design `docs/superpowers/specs/2026-10-06-split-entry-rework-design.md`, owner option B). Today `PUT /api/accounting/splits/{group_id}` deletes and re-inserts every member, so ids change, per-member dates and merchants are lost unless re-sent, reward rows and links are orphaned, and groups holding settlements, refunds, transfer legs or system rows cannot be edited at all. The SPA also cannot turn an existing single entry into a split without deleting it, and a split deleted down to one member keeps a one-member group.

## What Changes

- `PUT /api/accounting/splits/{group_id}` upserts by member id: full members with `id` are updated in place (or skipped / metadata-only when unchanged), `{id, keep: true}` members change metadata only, members without `id` are inserted, missing members are deleted.
- Protected members (settlements, refunds, transfer legs, system kinds, settled or refunded originals, loans a live schedule references) only take the keep form; `GET /api/accounting/entries/{id}` returns `protected` / `protected_reason` per group member.
- A one-member `PUT` dissolves the split into that member; `DELETE /api/accounting/entries/{id}` dissolves a split left with one member.
- New `PUT /api/accounting/entries/{id}/split` converts a single entry into a split keeping its id.
- All split writes answer `{group_id, member_ids, members: [{id, client_key}]}`; `group_id` is nullable (**API change**).
- Writes run in fixed phases (unlocked classification → prepare/FX → locks in the D32 order → re-check, 409 `retry` → write).

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `accounting-ledger`: split endpoint semantics, protected members, dissolve, convert, error codes and lock order of split writes, entry delete auto-dissolve.

## Impact

- **Code**: `services/accounting-service/app/schemas/writes.py`, `schemas/ledger.py`, `services/split_service.py`, `services/entry_write_service.py`, `services/ledger_service.py`, new `services/split_protection.py` and `services/split_compare.py`, `routers/splits.py`, `routers/entries.py`, `services/errors.py`.
- **APIs**: `SplitOut.group_id` nullable and gains `members`; new route; new 409 codes (`member_locked`, `retry`, `already_grouped`, `entry_locked`, `kind_not_splittable`, `group_scheduled`) and the 404 `member_not_found`.
- **Data**: no migration.
- **Frontend**: none in this change (PR-6 consumes the API).
