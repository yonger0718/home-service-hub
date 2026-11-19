# Debts: reopen a closed debt, close an open one (#391)

Status: short design (small change). Scope and permission picked by the owner 2026-11-19 ("both actions"; "the assistant may do both").

## Why

`is_closed` on a receivable / payable original is set only by the MOZE import (`moze_backup_import_service._close_settled_debts`: MOZE's `isSettle` is the authority, also where the settlements do not net). Nothing clears or sets it afterwards.

- `settlement_service.settle` refuses a closed debt (`422`, field `is_closed`, `已結清`). Deleting every settlement of the debt leaves it closed.
- Seen 2026-11-18: three imported receivables had one receipt each where the bank statement showed several transfers. The receipts were deleted to be recorded per transfer; every new settle call was refused and the flag had to be cleared by hand in the database.

Production, read only, counts (2026-11-19): 342 debt originals, 308 closed (all imported receivables). Of the closed ones 271 net to zero with their settlements, 37 do not (7 of those have no settlement at all). Reopening one of the 37 brings its old remainder back into every open-debt view.

## Goals

- The owner, in the SPA, and an API caller with `write` can reopen a closed debt and close an open one.
- Each action changes the flag only. Amounts, settlements and links are untouched; `open_amount` and `is_settled` follow from the flag and the settlements as they do today.
- The SPA says what the action does to the remainder before it is confirmed.

## Non-goals / anti-goals

- No automatic reopen when the last settlement is deleted: the 37 debts above would come back by accident.
- The flag is not a field of the entry write body. A `PUT` body means "absent = empty" for every field, so a caller that edits a closed debt and leaves the flag out would reopen it without saying so (the #396 trap).
- No write-off entry: closing a debt with a remainder books nothing. It only takes the debt out of the open-debt views, as MOZE does.
- No data pass.

## Approaches

1. **Two explicit actions** (picked): `POST /entries/{id}/reopen` and `POST /entries/{id}/close`.
2. Reopen only. Rejected: a mistaken reopen of a debt whose settlements do not net could not be undone without the database, which is the problem this change removes.
3. Automatic reopen on the last settlement delete. Rejected (see anti-goals).
4. `is_closed` in the `PUT` body. Rejected (see anti-goals).

Permission: both routes sit under the entry router's method scope, so they need `write`, like settle. The owner asked that the assistant can do both: the actions are reversible, change no amount, and the answer shows the result. (An `admin` lock, as on #396's confirming parameters, is for deletes.)

## Design

### API (`services/accounting-service`)

- `POST /entries/{id}/reopen` and `POST /entries/{id}/close`, no body, answer `200` with the entry's detail (`EntryDetailOut`).
- Both take the entry with `locked_entry` (FOR UPDATE, like settle) and apply the cutover lock (`assert_entry_editable`).
- Target: a receivable / payable original (`kind` receivable or payable, not `is_settlement`, no `settles_entry_id`). Anything else: `422`, field `kind`. Unknown id: `404`.
- Idempotent: reopening an open debt or closing a closed one answers `200` and changes nothing.
- `close` is refused (`422`, field `loan_schedule`) while a schedule that is not ended has a loan line on the entry: `schedule_posting` reads a closed loan as repaid and would end the schedule on its next period.
- Closing is allowed with or without a remainder. Reopening is allowed whatever the settlements sum to.

### SPA (`frontend`)

Entry detail, the debt state line:

- closed → `已結清` and a text button `重新開啟`;
- not closed and a remainder → `剩餘 …` and a text button `標記結清` (not for a debt a live schedule repays);
- not closed and fully settled → `已結清`, no button (unchanged).

Each button opens a confirm panel (the delete panel's pattern: Esc / 取消 closes, focus returns):

- reopen, remainder after reopening > 0: `重新開啟後，這筆會回到未結清：剩餘 <amount>。`
- reopen, remainder 0: `收款已收滿，重新開啟後仍是已結清；刪除收款後才會出現剩餘金額。`
- close: `剩餘 <amount> 不再列入未結清，之後可以重新開啟。`

The remainder after reopening is `|amount| − Σ settled values` from the detail already loaded (the breakdown's sum). After the action the detail reloads. Buttons are hidden while the entry is locked (cutover).

### Assistant skill (after the deploy)

- `POST /entries/{id}/reopen` / `close`: only when the owner asks; report the `open_amount` before and after. On `422 已結清` from settle, stop and ask instead of reopening on its own.

## Tests

API: reopen clears the flag, the remainder returns and settle then works; reopen of a debt whose settlements net stays settled; close sets the flag, `open_amount` 0, settle refused; both idempotent; non-debt, settlement row and unknown id refused; close refused under a live schedule's loan line and allowed once the schedule is ended; a `write`-only label may call both, a `read`-only label may not.

SPA: the service methods post to the two routes; the state line shows the right button per state (closed, open, settled by netting, live loan, locked); each confirm text; confirm posts and reloads; an API error shows in the panel.

## Rollout

- No migration (the column exists). API first, then the SPA: the old SPA never calls the routes; the new SPA against the old API would get `404` on the buttons.

## Known limits (accepted)

- No history of who closed or reopened a debt.
- Reopening one of the imported debts whose settlements do not net shows its old remainder until it is closed again.
- A closed debt with a remainder keeps its effect on the account balance; only the debt views change.

## Left to the builder

- Names of the service function and the SPA helpers; the exact wording of the refusals.
