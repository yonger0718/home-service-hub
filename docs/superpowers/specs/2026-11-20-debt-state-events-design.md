# Debts: a record of close / reopen that an edit cannot erase (#401)

Status: short design. Owner decisions 2026-11-20: the record outlives a deleted debt ("記錄留下"); a list of debts closed with a remainder on the reminders page ("做，放在提醒頁"); the description line of #391 is no longer written ("停止寫"); the list holds only the closes the owner did not make in the app, each until the owner confirms it there ("只列 Hermes 做的，可按「已確認」"; the assistant cannot confirm).

## Why

#391 (PR #400) added `POST /entries/{id}/close` and `/reopen`, open to a caller with `write` (the assistant). A close with a remainder books nothing and takes the debt out of the open-debt views. Its only trace is a line appended to the entry's `description`:

- an edit of the description removes it, and so does a `PUT` that leaves `description` out;
- it does not say who (which token label) did it;
- a reopen leaves nothing;
- nothing lists the debts closed with a remainder, so a mistaken close is found only by someone who already knows the entry.

What the code offers today:

- The auth middleware puts the caller's label on `request.state.client_label` (`app/auth.py`); the reconciliation routes already store it with each action (`_label(request)`).
- `settlement_service.set_closed` holds the entry `FOR UPDATE` when it changes the flag; `settlement_service.open_amount` gives `|amount + Σ settled values|` whatever the flag says.
- Open debts are listed on the reminders page (`GET /entries?open=true`), in its 借還款追蹤 section.

## Goals

- Every change of `is_closed` made through the two routes writes one event that no entry edit and no entry delete removes.
- The event says: which entry, close or reopen, when, the caller's label, and the remainder the action took out or brought back.
- The entry detail shows the events of its debt.
- The reminders page lists the closes that wait for the owner: the debts whose last event is a close with a remainder made by a caller without the owner's scope, the deleted ones included, until the owner confirms each or reopens the debt. The list is empty on a normal day.
- Only the owner confirms: a caller holding only `write` cannot take its own close off the list.
- The description line of #391 is not written any more. Lines already written stay.

## Non-goals / anti-goals

- No API to edit or delete an event. The one thing that changes on an event is the owner's confirmation.
- No general entry-change log; only the debt flag.
- No event for a flag the import sets, and no backfill for debts closed before this change: "has a close event" means "closed in the app".
- No change to who may call the two routes (`write`, owner decision in #391).
- No write-off entry for a closed remainder.

## Approaches

1. **A table of its own, no foreign key to the entry** (picked). `debt_state_event` keeps the entry id as a plain integer and a snapshot of the entry (kind, name, counterparty, amount, date). Deleting the entry touches nothing: the delete path, its lock order and its FK actions stay as they are, and the events of one deleted debt still belong together by its id (ledger ids are never reused).
2. A foreign key with `ON DELETE SET NULL`. Rejected: after a delete the events of one debt can no longer be told apart from another's (close, reopen, delete would read as "closed with a remainder"), and it adds an FK action to `delete_entries_cascade`, whose lock order is documented and tested.
3. Reuse `reconciliation_audit`. Rejected: its foreign keys and readers are about statements; a debt event has none of them.

## Design

### Data (`services/accounting-service`)

New table `debt_state_event` (model `DebtStateEvent` in `app/models/ledger.py`), one migration on top of `c4e8a1d7b3f9`:

| column | type | note |
| --- | --- | --- |
| `id` | integer, PK | |
| `entry_id` | integer, not null | the debt's id; **no foreign key** on purpose |
| `action` | varchar(8), not null | `close` or `reopen` (check constraint `ck_debt_state_event_action`) |
| `actor` | varchar(64), null | the caller's token label; null without auth or for a bare token |
| `open_amount` | numeric(20,4), not null | `settlement_service.open_amount` at that moment: the remainder a close took out or a reopen brought back; may be 0 |
| `currency` | varchar(8), not null | the entry's |
| `entry_kind` | varchar(16), not null | `receivable` / `payable` |
| `entry_name` | varchar(128), null | snapshot |
| `counterparty` | varchar(128), null | snapshot of the counterparty's name |
| `entry_amount` | numeric(20,4), not null | snapshot, signed as stored |
| `entry_date` | date, not null | snapshot |
| `reviewed_at` | timestamptz, null | when the owner confirmed the event; set at once when the caller itself holds the owner's scope |
| `created_at` | timestamptz, not null, default now() | |

Index `ix_debt_state_event_entry_id` on (`entry_id`, `id`).

### API

- `settlement_service.set_closed(db, entry_id, closed, *, actor=None, by_owner=True)`: when the flag really changes it inserts one event in the same transaction, under the entry's row lock. A repeated call, a refusal (kind, cutover lock, live schedule) and an unknown id write nothing. It no longer touches `description` (`_note_closed_remainder` goes).
- `POST /entries/{id}/close` and `/reopen` pass `request.state.client_label` as the actor and `by_owner` = the caller would pass `require("admin")` (`admin` or `legacy`, or scopes not configured; new helper `auth.holds_admin(request)`). An event written `by_owner` gets `reviewed_at = created_at`. Production: the SPA label holds `legacy`, the assistant's label `read, propose, write`.
- `GET /entries/{id}`: `debt_events`, oldest first: `{id, action, actor, open_amount, currency, event_date, needs_review}`. `event_date` is `created_at` as a date in the ledger time zone. `needs_review` is true only on the entry's latest event, when that is a `close` with `open_amount > 0` and no `reviewed_at`. Empty for a row that has none.
- `GET /debt-events/to-review?limit=50` (new router, `read` scope; `limit` 1 to 200): the debts whose **latest** event is a `close` with `open_amount > 0` and no `reviewed_at`, newest first, when the entry is either gone or still closed. Answer `{items, total}`; an item is `{event_id, entry_id, deleted, kind, name, counterparty, entry_date, amount, currency, open_amount, actor, event_date}`. A live entry answers its current name, counterparty, amount and date; a deleted one its snapshot.
- `POST /debt-events/{event_id}/review` (`admin` scope: `require("admin")`): sets `reviewed_at` on a `close` event that has none and answers `204`. Repeating it changes nothing (`204`). Unknown id: `404`. A `reopen` event: `422`, field `action`. A caller holding only `write`: `403`, nothing changes. It is not a ledger write: the cutover lock does not apply.

### SPA (`frontend`)

Entry detail, under the debt state line, when the debt has events: a short list, oldest first.

- close: `<date> 標記結清 · <actor> · 當時剩餘 <amount>`
- reopen: `<date> 重新開啟 · <actor> · 回到剩餘 <amount>`
- the amount part is left out when it is 0; the actor part when there is no label. Actor wording: `spa` → `網頁`, `hermes` → `Hermes`, any other label as it is.
- an event with `needs_review` ends with ` · 待確認`.

Close confirm: `剩餘 <amount> 不再列入未結清，會留下結清記錄；之後可以重新開啟。`

Reminders page, wherever the open debts show (全部 and 借還款追蹤), after them: a section `待確認的結清`, shown when the list is not empty.

- A row: counterparty and name, `當時剩餘 <amount>`, `<date> · <actor>`. It opens the entry detail (where 重新開啟 is). Beside it a button `已確認`: it posts the review and the row leaves the list. A refusal or a failure shows `確認失敗，請稍後再試。` and the row stays.
- A deleted debt: the same row with a `已刪除` mark, not a link; `已確認` works the same.
- More than the 50 loaded: a line `僅顯示最近 50 筆`. A failed read: `結清記錄讀取失敗，請稍後再試。`; the rest of the page is not affected.
- The SPA treats a missing `debt_events` as empty and a failed list read as above, so it also works against the API of #400.
- The bell count is unchanged (see the limits).

### Assistant skill (after the deploy)

- The description rule of #391 goes: the API records each close / reopen with the caller's label; `debt_events` on the entry and `GET /debt-events/to-review` read it. After a close with a remainder the assistant tells the owner that it waits for 已確認 on the reminders page; the review route is refused for its token (`403`) and must not be tried. The rest of the #391 rule stays (only on the owner's request; report `open_amount` before and after).

## Tests

API: a close writes one event (action, actor from the token label, remainder, currency, snapshot) and leaves `description` alone; a reopen writes one with the remainder it brings back; a repeated close / reopen and each refusal write nothing; no label → actor null; the detail lists the events oldest first; the events stay after the entry is deleted; `to-review` (closes by a `write`-only label): a close with a remainder is listed, a close with nothing left is not, a reopened debt is not, a deleted debt is listed with `deleted` and its snapshot, close → reopen → delete is not, newest first, `limit` and `total`; a close by an `admin` label, a `legacy` label or without configured scopes is never listed; `read` scope may read the list. Review: `admin` and `legacy` confirm and the debt leaves the list, the event stays in the detail without `needs_review`; a `write`-only label gets `403` and the list is unchanged; repeat `204`; unknown `404`; a reopen event `422`. The route is in the write inventory of `test_edit_lock.py`. Migration: head schema matches the models; round trip of the new revision.

SPA: the service call; the detail lines (close, reopen, zero amount, no actor, the three actor wordings, no block without events, a detail without `debt_events`); the close confirm text; the 待確認 mark; the reminders section (hidden when empty, a live row links to the entry, a deleted row does not, 已確認 posts and reloads, a refused 已確認 keeps the row and says so, the 50 line, the failure line, shown on 全部 and 借還款追蹤 only).

## Rollout

- A migration (one new table, no data change). API first with `alembic upgrade head`, then the SPA.
- At the deploy: count the entries whose description carries a #391 line (they have no event). None is expected (the routes are one day old); if there are any they are reported to the owner, not converted.
- Then the skill lines.

## Known limits (accepted)

- The actor is a token label (`spa`, `hermes`), not a person.
- A debt deleted and recorded again is a new entry: the old events show as a deleted debt, the new entry starts without events.
- "The owner" is a scope, not a person: a close through any token that holds `admin` or `legacy` needs no confirmation, and with scopes not configured no close does.
- A close with a remainder that waits for confirmation does not ring the bell; it shows on the reminders page only.
- The list shows the newest 50.
- A row changed directly in the database is not recorded.
- The remainder of an event is the one at that moment; later edits of the debt do not change it.

## Left to the builder

- Module and helper names; the revision id; the exact markup and styles.
