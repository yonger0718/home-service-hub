# E-invoice link (電子發票連結) — design

Status: draft rev 2, 2026-11-06 (builder check folded). Owner decisions are marked **(owner)**; calls made by the author
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
never reach the API, the database or a log line. The file name also carries identifiers, so logs
name files by Drive id only.

## 5. Data model (accounting_db)

- `einvoice`
  - `id`, `number` (VARCHAR 10), `issue_date` (DATE), `period` (CHAR 5, `YYYMM` ROC year plus the
    odd month that starts the two-month period).
  - Unique key: `(number, period)`, because invoice numbers repeat across periods **(author)**.
  - `seller_tax_id`, `seller_name`, `total_amount` (NUMERIC(20,4), as printed), `status_raw`, and
    `is_void` (true when the status is `作廢`).
  - `state`: `unmatched | ambiguous | near | linked | dismissed | void`.
  - `dismiss_reason`: `owner | seller_muted | runbook`, nullable.
  - `first_seen_at`, `updated_at`, `source_drive_id`.
- `einvoice_item`: `(invoice_id, seq)` primary key, `name`, `subtotal`.
- `einvoice_link`
  - `invoice_id` (unique), plus exactly one of `entry_id` / `group_id`.
  - `linked_by` (`auto | owner`), `linked_at`.
  - FK to `ledger_entry` / `entry_group` with ON DELETE CASCADE. Deleting the entry deletes the
    link and the invoice returns to matching.
- `einvoice_seller_mute`: `seller_tax_id` primary key, `created_at`.

On a link to a single entry whose `invoice_number` is NULL, the API sets `invoice_number` to the
invoice number. On unlink it clears the field only if it still holds that number. Group links do
not write the column **(author)**. The link table is the source of truth.

## 6. Ingest

- New worker subcommand `einvoice`, modelled on `cathay`.
  - It lists `<drive_root>/電子發票/財政部` with an allowed extension of `.csv`. `_list_root` keeps
    `.pdf` only today, so it needs an extension parameter.
  - It downloads files with the existing `copyid` path and its size and time limits.
  - It parses the file and posts JSON to a new `POST /einvoices/import` (`ingest` scope).
- Own user timer `homehub-einvoice.timer`, daily at 04:10 Asia/Taipei, after statements-daily **(author)**.
- Import is idempotent:
  - Upsert by `(number, period)`. Items are replaced when the set differs.
  - A void status sets `is_void` and `state = void` and deletes any link (setting
    `invoice_number` back as in §5).
  - Re-importing an unchanged file changes nothing.
- Parse failures are counted per file and reported in the job's summary log line. One bad file
  never stops the others.
- After the import, the API runs the matcher (§7). The same matcher also runs at the end of the
  daily job even when no file changed, because the owner may record the entry later.

## 7. Matching

Candidates for an invoice with date `d` and amount `A`, within a window of ±3 days of `d` (setting):

1. A single entry with `kind = expense` (top-level, `parent_entry_id IS NULL`; any account),
   `entry_date` in the window, not linked, not a member of a linked group, and whose
   `invoice_number` is NULL or equals this invoice's number. Its amount is the **net** amount:
   the parent plus its fee and discount children **(author)**. It matches when either:
   - the net amount `= −A` and `A` is a whole number (TWD rule), or
   - `original_amount` absolute value `= A` with a non-TWD `original_currency` (foreign rule).

   Both rules run on every invoice, and the candidates are the union. A decimal `A` can only match
   via the foreign rule, because TWD invoices are whole numbers **(author)**.
2. A split group whose `expense + receivable` members (net of their fee/discount children) sum to
   `−A`, with the earliest member date in the window. The group must be unlinked and have no linked
   member. A group counts as one candidate.

Before the amount rules, an **exact number match** wins: an entry whose `invoice_number` equals the
invoice number (a manual entry or an old MOZE scan) is linked directly when exactly one such entry
exists **(author)**.

Outcomes:

- Exactly one candidate: auto-link (`linked_by = auto`, `state = linked`).
- Two or more candidates: `state = ambiguous`, plus an inbox card listing them.
- None, but a near candidate exists: `state = near`, plus an inbox card. Near means a same-window
  expense whose amount differs from `A` by at most `max(50, 20 % of A)`, or, for an invoice with a
  decimal amount, any same-window expense whose `merchant`/`name` shares a token with
  `seller_name` (author).
- Nothing at all: `state = unmatched`, plus a reminder card, unless reminders are off or the seller
  is muted (§8).

The matcher never touches `linked`, `dismissed` or `void` invoices. An entry or group already
linked is not a candidate. Two invoices that would both auto-link to the same single candidate
both become `ambiguous`: no first-come wins.

Owner actions, all write scope:

- `POST /einvoices/{id}/link {entry_id | group_id}` — any state except void; replaces an existing link.
- `POST /einvoices/{id}/unlink` — the invoice goes to `dismissed`, not back to matching. Otherwise
  the next run would re-link the same wrong candidate.
- `POST /einvoices/{id}/dismiss` and `/undismiss`. Undismiss returns the invoice to matching.

Edits to a linked entry never unlink it **(author)**.

Splits **(author)**: a link targets either one entry or one group, never both inside the same group.
- Converting a linked entry into a split keeps the link on that entry. The new group is then not a
  candidate, because it has a linked member.
- Dissolving or deleting a linked group cascades the link away, and the invoice returns to matching.
- Entry detail shows the entry's own invoice. If the entry has none, it shows the group's invoice.
- On a split member, 「連結發票…」 asks whether to link 「這筆」 or 「整組」.

## 8. Reminders and mutes

- An `unmatched` invoice shows as a reminder card in the inbox: 「有發票沒有帳」, with seller, date,
  amount and items. Buttons: 「不用記帳」 (dismiss) and 「選帳…」 (manual link).
- When the owner has dismissed 3 invoices from the same `seller_tax_id`, the next dismiss card for
  that seller also offers 「這家以後都不提醒」, which creates a seller mute **(author)**.
- A mute suppresses `unmatched` reminder cards only. `ambiguous` and `near` cards still show.
- A muted seller's unmatched invoices are stored as `dismissed (seller_muted)` with no card. Creating
  a mute converts the seller's existing `unmatched` invoices the same way.
- The matcher still processes `seller_muted` invoices, so they still auto-link when a unique
  candidate appears. Owner dismissals (`dismiss_reason = owner`) stay untouched.
- Removing a mute turns that seller's `seller_muted` invoices back into `unmatched`.
- Global switch: `einvoice.remind_unmatched` hides every reminder card. Matching and the ambiguous
  and near cards stay.
- Management UI **(author)**: a 「發票」 section on the existing accounting settings page, with the
  global switch and the mute list (remove only).
  - It is backed by write-scope endpoints `GET/DELETE /einvoices/mutes[/{tax_id}]` and
    `PATCH /einvoices/preferences {remind_unmatched}`.
  - The PATCH updates only that field of the settings document and bumps its version, so the SPA
    never needs the admin-scope settings PUT.

## 9. Inbox integration

- The inbox is built from statement cases today, and every item needs a `statement_id`. Invoices
  have none.
- Add a separate list `einvoices` to the inbox response, with its own item schema, and render it as
  its own 「發票」 section in the SPA inbox panel **(author)**. The case engine and
  `/reconciliation/inbox/apply` are not touched.
- The section is titled 「發票待對帳」 **(owner)**. This phase ships a minimal, functional layout that
  reuses the existing card styles. A dedicated UI design pass comes later **(owner)**.
- Counts **(author, owner agreed to a notification-center section)**:
  - `ambiguous` and `near` cards add to `InboxCounts` and therefore to the 🔔 nav badge.
  - `unmatched` reminders are listed in the section but not counted, so monthly small-receipt
    reminders do not keep the badge lit.
  - The weekly digest is unchanged in this phase.
- Cards are `ambiguous`, `near` and `unmatched`, each with the invoice summary and items.
  - `ambiguous` and `near` cards list the candidates with account, date, amount, category and
    name. Buttons: one-tap link per candidate, 「都不是」 (dismiss) and 「選其他帳…」.
  - `unmatched` cards: see §8.

## 10. Entry detail and search

- `EntryDetailOut` gains `einvoice`, nullable: number, issue date, seller name, total, `linked_by`,
  and items as name and subtotal. An entry that belongs to a linked group shows the group's invoice.
- SPA `entry-detail` shows a 「發票」 section with the items and an 「解除連結」 button. When nothing
  is linked, it shows 「連結發票…」, a picker of non-void, unlinked invoices within ±7 days of the
  entry date, sorted by amount closeness.
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
- An unlinked invoice must not be re-linked by the next run. Undismiss returns it to matching.
- Carrier fields never appear in API payloads, DB rows or log lines. Use a fixture with fake
  carrier values and assert their absence.

## 15. Left to the builder

Exact migration revision ids, JSON field names beyond those named here, SPA layout of the inbox
section within the existing panel conventions, and parser structure inside the worker.
