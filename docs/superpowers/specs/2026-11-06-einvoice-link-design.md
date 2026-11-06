# E-invoice link (電子發票連結) — design

Status: draft rev 3.1, 2026-11-06 (builder check + Multica AGENT-400 round 1 and re-check folded). Owner decisions are marked **(owner)**; calls made by the author
are marked **(author)** and can be overridden in review.

## 1. Why

The owner's phone carrier (手機條碼載具) was registered in 2026-09. The Ministry of Finance mails a
monthly summary CSV (「手機條碼發票彙整」), and an existing mail automation already saves it to
Google Drive under `財務對帳單/電子發票/財政部/`. Nothing reads those files today: the statement
reconciliation spec lists `電子發票/` as out of scope, and the worker only lists `.pdf` files.

The owner wants to know **what they bought and where the money went**: open an entry and see the
invoice's line items, and search item names ("衛生紙") across the ledger **(owner)**.

## 2. Goals

- G1. Ingest every CSV in the Drive folder; store each invoice once with its line items.
- G2. Link invoices to existing ledger entries (or split groups) automatically when exactly one
  candidate matches **(owner: auto-link a unique candidate without asking)**.
- G3. Put ambiguous and near-miss invoices in the existing inbox so the owner can pick the entry.
- G4. Remind about invoices with no entry, as a dismissible reminder only. Never create entries
  from invoices: the owner sometimes scans the carrier while someone else pays **(owner)**.
  Reminders can be turned off per invoice, per seller and globally (§7).
- G5. Show the linked invoice (number, seller, date, items) on the entry detail page.
- G6. The timeline search box also matches item names of **linked** invoices only **(owner)**.

## 3. Non-goals

- 兌獎 / lottery checking, including the cloud-invoice special prizes **(owner: later)**.
- Category suggestions from item names.
- Creating ledger entries from invoices (G4).
- The MOF e-invoice API (needs an app id; the monthly CSV is enough).
- A designed UI for the inbox invoice section, and invoice lines in the weekly digest (later).
- History before 2026-09: the carrier did not exist **(owner)**.

Anti-goals: an auto-link must never change an entry's amount, date, account, category or kind.
A wrong link must be undoable in one click.

## 4. Source file facts

- Pipe-separated UTF-8 with a BOM. Two header rows, `表頭=M|…` and `明細=D|…`, then data rows.
- `M` row: `M|載具名稱|載具號碼|發票日期(YYYYMMDD)|商店統編|商店店名|發票號碼|總金額|發票狀態|`.
- `D` row: `D|發票號碼|小計|品項名稱|`, following its `M` row.
- File names start with the month the MOF sent them (`YYYY-MM_財政部_手機條碼發票彙整_…csv`).
  **Files overlap**: a later file repeats invoices from earlier months. Dedupe by invoice identity.
- `總金額` has no currency column. Foreign sellers (for example a US SaaS) are issued in the
  foreign currency with decimals, while the ledger holds the TWD card amount.
- Status seen so far: `開立`. A later file can carry the same invoice with another status.
  Only `作廢` means void. Any other status (for example `折讓`, an allowance) is stored in `status_raw`
  and keeps the invoice's state and link. An unknown status is counted in the job summary and does
  not block the file **(author)**.
- Volume: tens of invoices per month.

**Privacy (author):** carrier name and carrier number are dropped by the worker while parsing. They
never reach the API, the database or a log line. The file name also carries identifiers, so logs,
errors and API payloads name files by Drive id only (§6).

## 5. Data model (accounting_db)

- `einvoice`
  - `id`, `number` (VARCHAR 10), `issue_date` (DATE), `period` (CHAR 5, `YYYMM` ROC year plus the
    odd month that starts the two-month period).
  - Unique key: `(number, period)`, because invoice numbers repeat across periods **(author)**.
  - `seller_tax_id` (may be empty), `seller_name`, `total_amount` (NUMERIC(20,4), as printed),
    `status_raw`, and `is_void` (true once a `作廢` status is seen; never cleared).
  - `state`: `unmatched | ambiguous | near | linked | dismissed | void`.
  - `dismiss_reason`: `owner | seller_muted | runbook`, nullable, set only with `state = dismissed`.
  - `first_seen_at`, `updated_at`, `source_drive_id`.
- `einvoice_item`: `(invoice_id, seq)` primary key, `name`, `subtotal`.
- `einvoice_link`
  - `invoice_id` (unique), plus exactly one of `entry_id` / `group_id`.
  - Partial unique indexes on `entry_id` and on `group_id`: one invoice per entry and per group.
  - `linked_by` (`auto | owner`), `linked_at`.
  - FK to `ledger_entry` / `entry_group` with ON DELETE CASCADE.
  - An AFTER DELETE trigger on `einvoice_link` sets the invoice to `state = unmatched` **only when
    its `state` is still `linked`**. That is exactly the cascade case: entry delete
    (`delete_entries_cascade`), group dissolve (`dissolve_group`) and the MOZE full replace.
  - Code paths that remove a link on purpose (unlink, dismiss of a linked invoice, void) set the
    invoice's new state first, then delete the row, so the trigger leaves it alone.
  - Re-link never deletes: it updates the existing `einvoice_link` row in place (`entry_id` /
    `group_id`, `linked_by`, `linked_at`).
- `einvoice_seller_mute`: `seller_tax_id` primary key (non-empty), `created_at`.

On a link to a single entry whose `invoice_number` is NULL, the API sets `invoice_number` to the
invoice number. When the link goes away (unlink, void or re-link), the API clears the field only if
it still holds that number. Group links do not write the column **(author)**. The link table is the
source of truth.

## 6. Ingest

- New worker subcommand `einvoice`, using the cathay job's structure (Drive listing, `copyid`
  download with its size and time limits, `ApiClient`).
  - It lists `<drive_root>/電子發票/財政部` with an allowed extension of `.csv`. `_list_root` keeps
    `.pdf` only today, so it needs an extension parameter.
  - **Privacy:** unlike the cathay helpers, the einvoice path never puts a file name or path on the
    wire or in a log or error. `ListingError` messages built from `row["Path"]` (`drive.py:65`)
    and the cathay `file_name` fields (`cathay.py:198`, `cathay.py:279`) are not copied. Files are
    named by Drive id only.
  - It parses the file and posts JSON to a new `POST /einvoices/import` (`ingest` scope). The
    import schema forbids extra fields, so a carrier column cannot be stored even if the worker
    slips.
- Own user timer `homehub-einvoice.timer`, daily at 04:10 Asia/Taipei, after statements-daily
  (03:30) **(author)**.
- Import is idempotent. Upsert by `(number, period)`:
  - A new invoice is inserted as `unmatched`.
  - On conflict, update `seller_tax_id`, `seller_name`, `total_amount`, `status_raw`,
    `issue_date` and `source_drive_id`. Replace the items when the set differs.
  - State and links change only on the void transition: a `作廢` status sets `is_void`, sets
    `state = void`, then deletes any link (clearing `invoice_number` as in §5).
  - Any other status keeps state and link. A later `折讓` therefore shows the new total and keeps the
    link (§4). `is_void` is never cleared, so file order cannot resurrect a voided invoice.
  - Re-importing an unchanged file changes nothing.
- Parse failures are counted per file and reported in the job's summary log line. One bad file
  never stops the others.
- After the import, the API runs the matcher (§7). The same matcher also runs at the end of the
  daily job even when no file changed, because the owner may record the entry later.

## 7. Matching

### 7.1 Which invoices the matcher looks at

- Processes `unmatched`, `ambiguous`, `near`, and `dismissed` with reason `seller_muted`.
- Skips `linked`, `void`, and `dismissed` with reason `owner` or `runbook`.

### 7.2 Eligible targets

- An **entry** is eligible when all of these hold:
  - `kind = expense`, top level (`parent_entry_id IS NULL`), any account.
  - Not linked, and not a member of a linked group.
  - Its `invoice_number` is NULL or equals this invoice's number.
- A **group** is eligible when all of these hold:
  - `entry_group.kind = 'split'`. Installment and `reward_claim` groups are out of this phase.
  - Unlinked, with no linked member.
  - Every `expense` / `receivable` member shares one currency.
- The **net amount** of an entry is the parent plus its fee and discount children. Fees are stored
  negative and discounts positive, so a plain sum is right. A group's net is the sum of its
  expense and receivable members' nets.

The same eligibility applies to auto-link candidates, near candidates, the candidate lists on
ambiguous and near cards, and the entry-detail picker (§10).

### 7.3 Candidate rules

Let `d` be the invoice date and `A` its amount. The window is ±`date_window_days` (default 3)
around `d`. "Integral" means `A == A.to_integral_value()`, so `100.00` counts **(author)**.

1. **Exact number:** an eligible entry whose `invoice_number` equals the invoice number, and whose
   `entry_date` falls inside the invoice's two-month period.
2. **TWD rule (integral `A` only):** an eligible entry in the window whose `currency` is the main
   currency (TWD), with `original_currency IS NULL` and net amount `= −A`. Also an eligible group
   in the window (earliest member date), in TWD, with net `= −A`.
3. **Foreign rule (non-integral `A` only):** an eligible entry in the window with a non-TWD
   `original_currency` and `abs(original_amount) = A`.

There is no union across currencies: an integral `A` never uses the foreign rule. A whole-number
foreign invoice (JPY, KRW) therefore never auto-links. It ends up `near` or `unmatched`.

### 7.4 Outcomes, decided in one pass

The matcher computes candidates for every processed invoice first, then decides. Exact-number
candidates win over amount candidates for that invoice.

- An invoice with exactly one candidate, where no other processed invoice has that same target as
  its only candidate, is auto-linked (`linked_by = auto`, `state = linked`). This holds for
  entries and groups alike, and for exact-number matches too.
- Two or more candidates, or one candidate contested by another invoice in the same pass:
  `state = ambiguous`, plus an inbox card. No first-come wins.
- No candidates, but a near candidate exists: `state = near`, plus an inbox card. A near candidate
  is an eligible target in the window that meets either condition:
  - its net differs from `−A` by at most `max(50, 20 % of A)`, or
  - its `merchant` or `name` shares a seller token with `seller_name`. Tokens are maximal
    Latin-letter or digit runs (case-insensitive) and, for CJK text, any substring of length ≥ 2
    **(author)**.
- Nothing at all:
  - An invoice not under a mute becomes `state = unmatched`, with a reminder card unless the global
    switch is off (§8).
  - A muted seller's invoice stays `dismissed (seller_muted)`, with no card.

### 7.5 Owner actions (write scope)

- `POST /einvoices/{id}/link {entry_id | group_id}`
  - Allowed in any state except void. The target must pass the eligibility rules of §7.2, or
    already be this invoice's own target.
  - Replaces an existing link by updating that link row in place (§5).
- `POST /einvoices/{id}/unlink` sets `state = dismissed` and `dismiss_reason = owner`, overwriting
  `seller_muted`. Without this, the next run would re-link the same wrong candidate.
- `POST /einvoices/{id}/dismiss` (「都不是」, 「不用記帳」) sets `state = dismissed` and
  `dismiss_reason = owner`. If the invoice is linked, the link row is deleted after the state is
  set, as for unlink, and `invoice_number` is cleared as in §5.
- `POST /einvoices/{id}/undismiss` returns the invoice to `unmatched` for the next run. It is
  rejected (409) while the seller's mute exists: removing the mute is the way back for those rows.

Edits to a linked entry never unlink it **(author)**.

### 7.6 Splits (author)

A link targets either one entry or one group, never both inside the same group.

- Converting a linked entry into a split keeps the link on that entry. The new group is then not
  eligible, because it has a linked member.
- Dissolving or deleting a linked group cascades the link away. The trigger in §5 returns the
  invoice to `unmatched`.
- Entry detail shows the entry's own invoice. If the entry has none, it shows the group's invoice.
- On a split member, 「連結發票…」 asks whether to link 「這筆」 or 「整組」.

### 7.7 State table

| Event | From | To |
|---|---|---|
| import (new) | — | `unmatched` |
| match: unique uncontested | unmatched / ambiguous / near / dismissed(seller_muted) | `linked` (auto) |
| match: contested or several | same | `ambiguous` |
| match: near only | same | `near` |
| match: nothing | unmatched / ambiguous / near | `unmatched` |
| match: nothing | dismissed(seller_muted) | unchanged |
| owner link / re-link | any but void | `linked` (owner); an existing link row is updated in place |
| owner unlink / dismiss | any but void | `dismissed(owner)`, then the link row is deleted |
| undismiss | dismissed(owner / runbook), seller not muted | `unmatched` |
| link row deleted while state is still `linked` (cascade) | linked | `unmatched` (trigger) |
| `作廢` seen | any | `void` (link deleted after) |
| mute created | unmatched | `dismissed(seller_muted)` |
| mute removed | dismissed(seller_muted) | `unmatched` |

## 8. Reminders and mutes

- An `unmatched` invoice shows as a reminder card in the inbox: 「有發票沒有帳」, with seller, date,
  amount and items. Buttons: 「不用記帳」 (dismiss) and 「選帳…」 (manual link).
- The mute prompt counts `dismiss_reason = owner` invoices only, and only for a non-empty
  `seller_tax_id`. When a seller reaches `mute_prompt_after` (default 3), the next dismiss card for
  that seller also offers 「這家以後都不提醒」, which creates a seller mute **(author)**.
- A mute suppresses `unmatched` reminder cards only. `ambiguous` and `near` cards still show.
- A muted seller's unmatched invoices are `dismissed (seller_muted)` with no card. Creating a mute
  converts the seller's existing `unmatched` invoices the same way. The matcher still processes them
  (§7.1), so they still auto-link when a unique candidate appears.
- Removing a mute turns that seller's `dismissed (seller_muted)` invoices back into `unmatched`.
- Global switch: `einvoice.remind_unmatched` hides every reminder card. Matching and the ambiguous
  and near cards stay.
- Management UI **(author)**: a 「發票」 section on the existing accounting settings page, with the
  global switch and the mute list (remove only).
  - It is backed by write-scope endpoints `GET/DELETE /einvoices/mutes[/{tax_id}]` and
    `PATCH /einvoices/preferences {remind_unmatched}`.
  - The PATCH merges `remind_unmatched` into the stored settings JSON and bumps `version` itself.
    It must not call `update_reconciliation_settings`, which replaces the whole document
    (`settings_service.py:545`). The SPA therefore never needs the admin-scope settings PUT.

## 9. Inbox integration

- The inbox is built from statement cases today, and every item needs a `statement_id`. Invoices
  have none.
- Add a separate list `einvoices` to the inbox response, with its own item schema, and render it as
  its own section in the SPA inbox panel **(author)**. The case engine and
  `/reconciliation/inbox/apply` are not touched.
- The section is titled 「發票待對帳」 **(owner)**. This phase ships a minimal, functional layout that
  reuses the existing card styles. A dedicated UI design pass comes later **(owner)**.
- Counts **(author, owner agreed to a notification-center section)**:
  - Add `einvoice_ask` to `InboxCounts`, beside `ask`: the number of `ambiguous` + `near`
    invoices. `ask` stays the reconciliation count.
  - Compute it over the full set, before the `limit` slice (the bell calls the inbox with
    `limit: 1`).
  - The bell is `ask + einvoice_ask` at all three call sites: `nav-badge.service.ts:183`,
    `timeline.ts:970`, and the 「要你決定」 head text in `inbox-panel.ts:200-203`.
  - `unmatched` reminders are listed in the section but not counted, so monthly small-receipt
    reminders do not keep the badge lit.
  - The weekly digest is unchanged in this phase.
- Cards are `ambiguous`, `near` and `unmatched`, each with the invoice summary and items.
  - `ambiguous` and `near` cards list the eligible candidates (§7.2) with account, date, amount,
    category and name. Buttons: one-tap link per candidate, 「都不是」 (dismiss) and 「選其他帳…」.
  - `unmatched` cards: see §8.

## 10. Entry detail and search

- `EntryDetailOut` gains `einvoice`, nullable: number, issue date, seller name, total, `linked_by`,
  and items as name and subtotal. An entry that belongs to a linked group shows the group's invoice.
- SPA `entry-detail` shows a 「發票」 section with the items and an 「解除連結」 button. When nothing
  is linked, it shows 「連結發票…」, a picker of non-void, unlinked invoices within ±7 days of the
  entry date, sorted by amount closeness. It is offered only when the entry is eligible (§7.2).
- Search: `_text_filter` in `ledger_service` gains an opt-in flag `include_invoice_items`. When it
  is set, an `EXISTS` over `einvoice_link` → `einvoice_item.name ILIKE` is added, for the entry and
  for its group.
  - Only the entries list behind the timeline search sets the flag. Report filters
    (`report_rules.entry_filters`) keep their current behaviour, so saved reports do not change
    **(author)**.
  - Unlinked invoices never match **(owner)**.
- When `q` is set, list rows carry `invoice_item_hits` (up to 3 matching item names) so the
  timeline can show which item matched **(author)**.

## 11. Settings

Add a new block `einvoice` to the reconciliation settings document:

```
einvoice: {
  enabled: false,          # ingest + matching
  remind_unmatched: true,
  date_window_days: 3,     # 0..7
  mute_prompt_after: 3     # 1..10
}
```

- Add `EINVOICE_DEFAULTS` merged in `_reconciliation_dict`, a `_check_einvoice` validator, a
  `read_einvoice_settings` accessor, and the field on both `ReconciliationSettingsIn` and
  `ReconciliationSettingsOut`.
- PUT replaces the whole document: runbooks must start from the current version.
- When `enabled` is false, the import endpoint still stores invoices but the matcher does not run,
  and the inbox section is hidden.

## 12. Interactions with existing features

- **MOZE backup import (full replace):** deleted `moze_backup` entries cascade-delete their links.
  Those invoices return to matching and normally re-link to the re-imported rows. Owner-made links
  on those rows are lost. This is accepted, because HomeHub has been the primary ledger since the
  2026-10 cutover and MOZE re-imports are not routine **(author)**.
- **Statement coverage:** writing `invoice_number` is not a structural coverage key, so it never
  releases statement lines.
- **Rewards and splits:** linking never touches entry rows other than `invoice_number`.
- **Hermes:** no change in this phase. Documenting a read endpoint for the skill is a follow-up.

## 13. Rollout

1. Migration (new tables). API and worker deploy with `einvoice.enabled = false`.
2. Install the worker timer. Run `worker einvoice` once: it imports the existing files, and no
   matching runs yet.
3. Runbook step: dismiss the invoices the owner has already ruled on (`dismiss_reason = runbook`).
   The list is given at rollout time, keyed by invoice number and issue date. It lives in the
   release record, never in the repo.
4. Settings PUT `enabled = true`, run the matcher, and review the inbox with the owner.
5. SPA release (entry detail section, inbox section, search hits).

## 14. Review focus (inputs the tests must pin)

- The same invoice in two overlapping files, with a changed status in the later one → one row,
  voided, link removed.
- A foreign decimal invoice against a TWD entry that carries `original_amount`, and against one
  that does not (→ `near` via a seller token, never an auto-link).
- Two invoices with the same amount on the same day in the same window, and one candidate entry →
  both `ambiguous`.
- A split group whose members sum to the invoice, alongside a single member with the same amount
  as the invoice → `ambiguous`, not an auto-link to the member.
- An unlinked invoice must not be re-linked by the next run, including a muted seller's invoice.
  Undismiss returns it to matching. Undismiss while the seller is muted is rejected.
- A whole-number invoice against a same-window USD charge whose `original_amount` equals it → no
  auto-link. A TWD invoice against an expense on a non-TWD account with the same amount → no
  auto-link.
- Invoice number reuse: an entry stamped with a number in one period is not an exact match for the
  same number in the next period.
- Deleting a linked entry, dissolving a linked group and a MOZE full replace each return the invoice
  to `unmatched` (trigger). Void then cascade leaves it `void`.
- Owner re-link of a linked invoice keeps `state = linked` with `linked_by = owner`, and the next
  matcher run leaves it alone. Dismiss of a linked invoice removes the link and the entry detail no
  longer shows it.
- The bell count includes ambiguous + near invoices with `limit: 1`. Unmatched reminders do not.
- Carrier fields never appear in API payloads, DB rows or log lines. Use a fixture with fake
  carrier values and assert their absence.

## 15. Left to the builder

Exact migration revision ids, JSON field names beyond those named here, SPA layout of the inbox
section within the existing panel conventions, and parser structure inside the worker.
