# Entry delete: say what else goes, and hold a child on another account (#390)

Status: short design (small change). Approach picked by the owner 2026-11-19 ("refuse the odd shape and report").

## Why

`DELETE /entries/{id}` also deletes rows the caller did not name:

- the entry's fee / discount children (`entry_write_service.delete_entries_cascade`);
- for a transfer, the other leg, which sits on another account, and that leg's children.

The response is `204` with no body, so an API caller never learns that a second row went away. On 2026-11-18 a settlement receipt imported from MOZE carried a card FX-fee child on another account. Deleting the receipt, to re-record the repayment, deleted the fee as well. The card then showed a balance gap, and the fee had to be re-created and matched again.

What the code and the data say today:

- The SPA is not silent: its delete dialog lists the children and the other transfer leg (`entry-detail.ts` `deleteNotes`). It does not name a child's account. The silent path is the API (the assistant).
- Production (read only, counts): 278 child rows, none on another account than its parent. The one of 2026-11-18 was repaired by hand. No data pass is needed.
- Only the MOZE imports can create a child on another account. `write_children` and `sync_children` always put a child on its parent's account.
- 191 of the 278 children are covered by a statement line. That is the normal card purchase with its fee on one statement. "The child is covered" is therefore not a usable reason to refuse.

## Goals

- An API caller sees every row a delete removed.
- A delete that would remove a child on another account than its parent is refused until the caller confirms it.
- The ordinary delete (an entry, its own fee or discount, a transfer with its other leg) works as before, with no extra step.

## Non-goals / anti-goals

- No refusal because a child is covered by a statement line (see above).
- No change to `DELETE /splits/{id}`, to the reconciliation reverts or to any other caller of `delete_entries_cascade`. They keep deleting children without a report.
- No data pass.
- Entry PUT deleting a child that the payload leaves out: issue #396.
- The delete must not become a two-step operation for the ordinary case.

## Approaches

1. **Refuse the odd shape and report** (picked). The delete answers with the list of removed rows. It is refused only when a child sits on another account than its parent, unless `?cascade=true`.
2. **Report only.** No refusal. Smallest, but a new MOZE import could again create the odd shape, and the caller would learn of the loss only after it.
3. **Always confirm.** Any delete with a child or a transfer leg needs `?cascade=true`. Rejected: it adds a step to most card purchases and every transfer, for the assistant and the SPA alike.

## Design

### API (`services/accounting-service`)

- `DELETE /entries/{entry_id}` takes a query parameter `cascade: bool = False` and answers `200` with a body (it was `204` without one):

  ```json
  {"deleted": [
    {"id": 12, "role": "entry", "parent_entry_id": null, "kind": "expense", "name": "…", "amount": "-100.0000",
     "currency": "TWD", "account_id": 3, "account_name": "…", "entry_date": "2026-11-01"},
    {"id": 13, "role": "child", "parent_entry_id": 12, "kind": "fee", "…": "…"}
  ]}
  ```

  - `role` is `entry` for the row named in the URL, `transfer_leg` for another top-level leg of its transfer, `child` for a fee / discount child of either.
  - Order: the entry, then the other legs by id, then the children by id.
  - Rows whose links are only cleared (settlements, refunds, rewards) are not listed; they are not deleted.
- **Refusal.** When a child that the delete would remove has an `account_id` different from its own parent's and `cascade` is not true, the delete answers `409` with the code `cascade_unconfirmed`. The detail names each such child (id, kind, account id) and says to repeat the request with `?cascade=true`. Nothing is deleted.
  - The check runs in `entry_write_service.delete_entry`, under the target locks and after the existing refusals (not found, concurrent change, reward row, cutover lock), so those keep their answers.
  - The children are read once under the locks; that one read feeds both the check and the report.
  - With `cascade=true` the delete proceeds and reports as usual.
- `delete_entry` returns the report rows. `delete_entries_cascade` is unchanged.

### SPA (`frontend`)

- `AccountingService.deleteEntry(id, cascade = false)` adds `?cascade=true` when asked. The response body is ignored.
- Entry detail, delete dialog: a child on another account than the entry reads `手續費 $30（在 {account name}）`. Other notes are unchanged.
- `confirmDelete('entry')` passes `cascade = true` only when the dialog listed such a child. A `409` from the server shows in the dialog like any other delete error.

### Assistant skill (after the deploy)

- Before `DELETE /entries/{id}`: read `children` and `transfer_counterpart` of the entry. The answer lists `deleted`. On `409 cascade_unconfirmed`, stop and ask the owner.
- When editing an entry, send its fee and discount back unchanged (#396).

## Tests

API (`tests/integration`, beside the existing entry delete tests):

- a plain entry: `200`, `deleted` is the entry alone with `role` `entry`;
- an entry with a fee and a discount on its own account: deleted without `cascade`; `deleted` lists three rows with the right roles, parents and order;
- a transfer: `deleted` lists the entry and the other leg (`transfer_leg`), and a child of the other leg as `child` with that leg as its parent;
- a child on another account: `409 cascade_unconfirmed` naming the child; the entry and the child still exist; the same request with `?cascade=true` deletes both and reports them;
- a transfer whose other leg has a child on a third account: refused the same way;
- a child covered by a statement line on the parent's account: deleted without `cascade`;
- the existing refusals keep their answers when a foreign child is also present (reward row without an accrual: still its own `409`).

SPA:

- the service adds `?cascade=true` only when asked;
- the dialog names the account of a child on another account, and not of a child on the entry's account;
- the delete request carries `cascade=true` exactly when such a child was listed.

Existing tests that assert `204` on this route change to `200`.

## Rollout

- API deploy, no migration. An old SPA works against the new API (it ignores the body); a new SPA works against the old API (an unknown query parameter is ignored). Either order is safe.
- SPA build and publish; no new route.
- Then the skill lines.

## Known limits (accepted)

- A foreign child under the **other** transfer leg is not in the entry detail the SPA holds, so the dialog cannot list it. The server refuses, and the SPA shows the refusal text; the owner has to delete from the other leg or through the API. No such row exists today.
- `DELETE /splits/{id}` has neither the report nor the refusal.

## Left to the builder

- The schema names (`EntryDeleteOut`, `DeletedEntryOut` or similar) and where the report rows are built.
- The exact refusal wording after the code.
