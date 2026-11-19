# Updates: a stored fee / discount is not dropped without a confirmation (#396)

Status: short design (small change). Approach and scope picked by the owner 2026-11-19 ("refuse, delete only when confirmed"; "all edit routes"; "make the confirmation a lock, #390 included").

## Why

An update replaces the entry's fee / discount children with the ones in the payload (`entry_write_service.sync_children`, #313). A payload without a `fee` deletes the stored fee. The answer is the updated entry, and nothing in it says a row went away.

- The SPA form is not affected: it always sends the stored fee and discount back.
- An API caller (the assistant) that reads an entry, changes one field and sends the body back without the fee loses the fee. The balance of the account moves, and a statement line the fee was matched to comes back unmatched.
- Same family as #390 (delete). #390 added a report and a refusal to `DELETE /entries/{id}`; the update routes have neither.

What the code and the data say today:

- Four routes reach `sync_children`: `PUT /entries/{id}`, `PUT /transfers/{group_id}` (both legs), `PUT /splits/{group_id}` (a member whose money fields changed) and `PUT /entries/{id}/split` (the anchor). No other code calls the update functions with children at stake: the reconciliation "split entry" action refuses an entry that has children.
- Production (read only, counts): 278 children; 260 under a plain entry, 13 under a transfer leg, 5 under a split member. No parent has two children of one kind.

## Goals

- An update that would delete a stored fee / discount child, with nothing of that kind in the payload, is refused until the caller confirms it.
- The owner's own edit in the SPA (clearing the fee in the form and saving) needs no extra step.
- One rule for all four routes.
- The confirmation is the owner's: a caller without the `admin` scope (the assistant) cannot give it. The same holds for the delete confirmation of #390.

## Non-goals / anti-goals

- No change to what an update does when it is allowed: the children are still reconciled as #313 defined.
- Changing a child (another amount or name) is not a drop and is not refused.
- Removing a member from a split deletes that member with its children, as before. `DELETE /splits/{id}` is unchanged.
- No "absent means keep" contract: every other absent field of a PUT body means "empty", and two fields with another meaning would be a trap of its own.
- No data pass.

## Approaches

1. **Refuse the drop until confirmed** (picked). `409` with a code; `?drop_children=true` confirms. The same pattern as `?cascade=true` in #390.
   - As first designed, any caller with `write` could add the parameter and repeat the request, and the refusal text said how. The only thing between the assistant and the delete was a line in its skill file. The owner chose to make it a lock: the confirming request needs `admin`.
2. **Absent means keep, explicit null deletes.** No SPA change (the form sends an explicit null). Rejected: a caller that serialises empty fields as null still deletes silently, and the PUT body would mix two meanings of "absent".
3. **Refuse only a covered or foreign child.** Smallest. Rejected: 87 of the 278 children are not covered by a statement line; losing one of those stays silent, and it is the case no statement will ever point out.

## Design

### API (`services/accounting-service`)

- The four routes take a query parameter `drop_children: bool = False`.
- **Refusal.** When an update would delete a stored child of an entry it rewrites and the payload carries no child of that kind for that entry, and `drop_children` is not true, the answer is `409` with the code `children_unconfirmed`. The detail names each such child (`entry <id> (<kind>) of entry <parent id>`) and says to send the fee / discount back, or that the owner confirms the drop in the app. It does not name the parameter. Nothing is written.
  - The check sits in `sync_children`, before it writes or deletes a child, so one rule serves the four routes. The routers commit only after the service returns, so the refused request rolls back whole. (The FX rate cache may commit earlier, in the prepare step, before any ledger write; that is unchanged.)
  - A stored child of a kind the payload does carry is never a drop, also when several stored rows become one (#313).
  - With `drop_children=true` the update proceeds as today.
- `drop_children` confirms every drop of the request (both legs of a transfer, every member of a split).
- **The lock.** A request with `drop_children=true` needs the `admin` scope (`auth.require("admin")`: `admin` or `legacy`, or scopes not configured). Without it the answer is `403 scope`, before the service runs, whether or not a child would be dropped. Production, read 2026-11-19 with the service's own loader as the API user (which scopes each label holds, nothing else): the SPA label holds `legacy`, so it passes; the assistant's label holds `read, propose, write`, so it is refused (also seen on the live API: `403 scope` on an admin-only route). The development `.env` differs from production and is no evidence.
- **#390, same lock.** `DELETE /entries/{id}?cascade=true` needs `admin` in the same way, and the `cascade_unconfirmed` detail no longer says `repeat with ?cascade=true`; it says the owner confirms the delete in the app.
- The responses are unchanged; the updated entry already lists its `children`.
- Creating (`POST`) is unchanged.

### SPA (`frontend`)

- `AccountingService.updateEntry`, `updateTransfer`, `updateSplit` and `convertEntryToSplit` take `dropChildren = false` and add `?drop_children=true` when asked.
- The entry form and the transfer panel remember, per loaded entry, which child kinds it had. A save sends `dropChildren = true` only when the form drops a kind it had loaded (the owner cleared the fee, or changed the account, which clears fee and discount with a notice).
- A child added by someone else after the form loaded is therefore refused: the SPA shows `children_unconfirmed` as 這筆記錄的手續費或折扣是在你開啟後才加上的；請重新載入後再編輯.
- The fee proposal save (it adds a fee) never sets the flag.

### Assistant skill (after the deploy)

- `PUT` of an entry, a transfer or a split: send the stored fee / discount back. On `409 children_unconfirmed` or `409 cascade_unconfirmed` stop and tell the owner; the confirmation (`?drop_children=true`, `?cascade=true`) is refused for the assistant's token (`403 scope`), so the owner does it in the app.

## Tests

API (`tests/integration`):

- `PUT /entries/{id}` without `fee` on an entry with a stored fee: `409 children_unconfirmed` naming the child; the entry is unchanged (also the field the payload changed) and the fee still exists; with `?drop_children=true` the fee is deleted and the entry updated;
- the same payload with the fee sent back: `200`, the fee row keeps its id;
- a payload with another fee amount: `200` without the flag;
- an entry with a fee and a discount, payload with only the fee: refused, naming the discount only;
- an entry without children, payload without children: `200` without the flag;
- `PUT /transfers/{group_id}` without `out_fee` on a transfer whose out leg has a fee: refused, both legs unchanged; confirmed with the flag;
- `PUT /splits/{group_id}` changing a member's amount without its stored fee: refused, the group unchanged; a member removed from the payload is still deleted with its fee without the flag;
- `PUT /entries/{id}/split` whose anchor member omits the anchor's stored fee: refused, no group created;
- the lock, with scopes configured: a `write`-only label gets `403` on each of the four routes with `drop_children=true` and on `DELETE /entries/{id}?cascade=true`, and nothing changes; the same label without the parameter still updates and deletes; an `admin` label and a `legacy` label pass;
- the `cascade_unconfirmed` and `children_unconfirmed` details do not contain `=true`.

SPA:

- each of the four service methods adds `?drop_children=true` only when asked;
- the entry form: fee loaded and kept → no flag; fee loaded and cleared → flag; no fee loaded → no flag;
- the transfer panel: the same three cases for one side;
- the wording of `children_unconfirmed`.

Existing tests that drop a child through an update gain `?drop_children=true`.

## Rollout

- The new SPA works against the old API (an unknown query parameter is ignored). The old SPA against the new API could not clear a fee (it would show the English refusal). So: publish the SPA first, then deploy the API. No migration.
- Then the skill lines.

## Known limits (accepted)

- `drop_children=true` confirms every drop of the request, not a named child.
- The assistant cannot drop a fee or delete a foreign child even when the owner tells it to in chat; the owner does it in the app. (It can still delete the child row itself with `DELETE /entries/{child id}`, which lists what it removed; that is a named, single-row delete.)
- The parameters stay visible in the OpenAPI document.
- An open SPA tab from before the release cannot clear a fee until it is reloaded.
- A caller that sends a fee back with a wrong amount changes the fee; that is an edit, not a drop.

## Left to the builder

- Where the SPA keeps the loaded child kinds, and the names of the helper types.
- The exact refusal wording after the code.
