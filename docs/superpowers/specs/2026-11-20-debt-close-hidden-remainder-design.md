# Debts: a close must not hide a remainder that appears later (#403)

Status: short design. Owner decision 2026-11-20: do B and A (below), B first; C and D are not done.

## Why

#401 lists a close that waits for the owner by the remainder **at the close** (`debt_state_event.open_amount > 0`). Two paths end with a closed debt that has a remainder and is not listed:

1. A caller closes a debt its settlements already cleared (remainder 0, not listed). Later a settlement of it is deleted or changed. The debt stays closed, now with a remainder, in no list.
2. A caller with `write` takes its own waiting close off the list: reopen → settle in full → close (remainder 0) → delete the settlement.

The web app never offers 標記結清 on a debt with nothing left (`canClose` needs `open_amount > 0`); only API callers reach a close at zero.

## Approaches

- **A. The list also shows the current remainder** (second PR). With B in place every new close has a remainder, so which debts are listed needs no change; what is left is the amount: after a close with a remainder, a deleted or changed settlement moves the remainder while the row still shows the one at the close, and the owner would confirm a wrong number. The list keeps its condition (the remainder at the close): reading only the current remainder would let a caller take a waiting close off the list by editing the debt's amount down.
- **B. Refuse a close with nothing left** (picked first). A debt its settlements cleared is already out of the open views and needs no flag; if a settlement later goes, it is open again and visible. One check in `set_closed`; no SPA change.
- C. Tie the owner's confirmation to the amount confirmed (a later, larger remainder is listed again). Not done: needs a new column, and the case needs someone to change a settlement of a debt the owner already confirmed.
- D. Freeze the settlements of a closed debt (refuse their delete / edit until a reopen). Not done: it changes how the owner corrects old rows and reaches imported closed debts.

## Design (B)

`settlement_service.set_closed`, when the call would really close the debt (`closed` and not yet `is_closed`): if `open_amount(db, target)` is 0 it raises `ValidationError("open_amount", …)` → `422`, field `open_amount`, before the live-schedule check. Nothing is written (no flag, no event).

- A repeated close of a debt that is already closed stays `200` and changes nothing, whatever its remainder.
- Reopen is unchanged (a reopen may bring back 0).
- A debt collected beyond its amount has a remainder (`|amount + Σ|`) and can be closed, as before.
- A close event therefore always has `open_amount > 0`; a reopen event may have 0.

With B the chain in 2 stops at "close": the debt stays open and cleared, and deleting the settlement shows it as open again.

## Design (A)

- `GET /debt-events/to-review`: each item also answers `current_open_amount`: `settlement_service.open_amount` of the entry now, or null for a deleted debt. `open_amount` stays the remainder at the close. The condition of the list and `debt_events` on the entry detail do not change.
- SPA reminders row: `當時剩餘 <amount>` as before; when `current_open_amount` is there and differs, a second line `目前剩餘 <amount>` in the warning colour (also when it is now 0). A deleted debt and an answer without the field (the API before this change) show the first line only.

## Tests

API (A): a waiting close whose settlement is deleted afterwards answers `open_amount` as at the close and `current_open_amount` as now; an untouched one answers both equal; a deleted debt answers null. SPA (A): the second line shows only when the two amounts differ; not for a deleted debt or a missing field.

API (B): a close of a cleared debt answers `422` `open_amount`, leaves `is_closed` false and writes no event (by a `write`-only label and without auth); a repeated close of a closed debt whose settlements clear it stays `200`; the chain reopen → settle in full → close is refused at the close and, after the settlement is deleted, the debt is in `GET /entries?open=true`; a debt collected beyond its amount can still be closed.

## Rollout

B: API only (no migration, no SPA). Then the assistant's skill line: a close of a debt with `open_amount` 0 is refused. A: API first, then the SPA (the new SPA against the older API shows the first line only).

## Known limits (accepted)

- A close the owner confirmed stays confirmed when the remainder later changes (C).
- The current remainder is formatted in the event's currency; a waiting debt moved to an account in another currency shows the wrong symbol.
- Debts the import closed have no event and are not listed, whatever their remainder.
