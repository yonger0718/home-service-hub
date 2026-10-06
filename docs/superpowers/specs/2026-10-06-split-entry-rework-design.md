# Split (多類別) entry rework — design (v3)

Date: 2026-10-06 · Base: `main` @ 4e301e2 · v2 folds in the AGENT-66 spec review (R1–R13); v3 folds in the v2 re-review (V2-1…V2-8). Owner decisions: option B (MOZE-style parent bubble; everything per child except what the parent must own), convert an existing single entry into a split in this round.

Sources: MOZE docs §2.1/§2.9/§6.2 (feature digest), the owner's seven-point review, AGENT-66 review (`split-spec-review-d33104c.md`), fact sheet of the current split implementation.

## Goals

1. One input method for every line of a record: the same category grid, keypad, tiles and pickers edit whichever child bubble is selected. No separate "新增拆帳行" modal, no `<select>`.
2. A split is a parent with N equal children. Children own category, kind, amount, account, counterparty, name, project, tags, fee/discount, FX, reward rules and notes; the parent owns merchant, date/time/posted date, group name and group notes.
3. Saving is atomic and id-stable: an existing single entry becomes a split at save time without losing its id; editing a split never recreates untouched members; protected members (settlements, refunds, transfer legs, system rows, originals referenced by settlements/refunds, loans referenced by a live schedule) can only change metadata.
4. Old splits round-trip without silent loss: per-member dates, times, merchants, notes and attached rules stay as stored unless the user changes that field.

Non-goals: parent-to-child sync preferences (MOZE §2.9), e-invoice import to splits, reordering children, per-child dates, 多筆收還款, card art; widening `EditableKind`.

## Global constraints

- Frontend: Angular 21 standalone + signals, Vitest; backend: FastAPI + SQLAlchemy 2.0 + Alembic. **No schema migration** in this design; `SplitOut.group_id` becomes nullable (API change, listed in §1.6).
- Accepted contracts untouched: schedules (`openspec/specs/accounting-schedules`, incl. D29/D32/D33 lock order and loan references), the dirty-form registry and overlay Esc contract (UX refinement PR-2), the account picker (PR-3), the category drill-in grid incl. its Esc contract (PR-5: child grid → main grid; reopened main with a selection → strip + focus; unselected main → form).
- Backend changes ship first in their own PR (owner's session; Multica cannot reach Postgres); the frontend PR (Multica) targets the merged API.
- Every rule below has a test (§1.8, §2.11). `pytest` (incl. Postgres integration), `cd frontend && npm test -- --watch=false`, `npx ng build` green. Checked at 390×844, 760×820, 1280×800.
- Terms: **anchor** = the existing entry being converted; **protected member** = §1.2; **empty** (for copy rules) = `null` or whitespace-only after trim; **editable kinds** = `EditableKind` (expense, income, receivable, payable).

## 1. Data and API contract (backend, PR-B)

### 1.1 Storage (unchanged schema)

- `EntryGroup(kind='split')` holds `name`, `merchant`, `description` — the parent's 整筆名稱／商家／整筆備註.
- Every member is a `ledger_entry` with `ledger_entry.group_id` (`models/ledger.py:263`). Dates, times, posted dates, merchant, description, invoice and reward links live on members; the parent date is "the value written to every member". Legacy per-member `merchant` values are preserved verbatim unless that member is re-sent with a different value.
- No parent invoice.

### 1.2 Protected members

A current member is **protected** when any of (DB predicates, shared by the read flag and the locked-write guard): `is_settlement` (a 收還款 row, including imported/orphan ones without `settles_entry_id`); `kind == 'refund'` (whether or not `refunds_entry_id` is still set — the link is nulled when the source is deleted); a transfer leg (`transfer_group_id` not null or `kind == 'transfer'`); `kind in SYSTEM_KINDS` = `fee`, `discount`, `reward`, `interest`, `balance_adjustment` (there is no `kind='system'` in the DB; the UI label 系統 maps to these) — equivalently any kind outside `EditableKind`; `has_settlements_or_refunds(entry)` (an original referenced by a settlement/refund); a loan referenced by a live schedule definition (`schedule_entry_hooks.assert_not_referenced` would refuse its delete). The `protected` flag and the reason code are returned on read (`GET /entries/{id}` already exposes kind/settlement flags; the group member list in `EntryDetailOut.group_members` gains `protected: bool`, `protected_reason: str | null`).

### 1.3 Payload (`schemas/writes.py`)

- `SplitMemberIn(EntryIn)` gains `id: int | None = None`, `client_key: str | None = None` (≤ 64 chars, echoed, not stored). Editable kinds only, as today.
- New `SplitKeepIn`: `{id: int, keep: Literal[true], name?, project_id?, tags?, description?, client_key?}` — a metadata-only reference to an existing member. No financial, kind, account, category, counterparty, FX, fee/discount, rule or date fields. Fields omitted are left unchanged; fields sent (incl. `null`/`[]`) are applied.
- `SplitIn.members: list[SplitMemberIn | SplitKeepIn]` (discriminated by `keep`), min 1; max 50 for `POST` and convert, and for `PUT` max(50, current member count) so an imported group larger than 50 can still be edited but not grown. Group fields as today.
- Rules enforced at schema/validation level (422 unless noted): duplicate `id` → 422; duplicate non-null `client_key` → 422; `POST /splits`: ≥ 2 members and no member may carry `id` or `keep`; `PUT /splits/{gid}`: ≥ 1; `PUT /entries/{id}/split`: exactly one member with `id == entry_id` (the anchor, a full `SplitMemberIn`), every other member without `id`, total ≥ 2.
- Members send every per-member field explicitly: `project_id` (nullable), `tags` (list, may be `[]`), `description`, `entry_date`, `entry_time`, `posted_date`. `null`/`[]` are values. Server-side inheritance in `member_payloads()` stays only for callers that omit fields; this frontend never omits them.

### 1.4 Transaction order (applies to create, upsert, convert, dissolve)

1. **Preliminary read and classification (no locks, no FX)**: group exists and is editable / not scheduled / not import-locked; every `id` belongs to the group (else 404 `member_not_found`); protected set (§1.2) → 409 `member_locked` violations; cardinality. For each `keep_full` member compute **unchanged** = the canonical payload (kind, amount, account, category, counterparty, FX inputs `original_amount/original_currency/fx_rate`, fee, discount, rule ids, invoice, dates, name, project, tags, description) equals the stored row; unchanged members are not prepared at all.
2. **Prepare** (may commit the session through the FX cache, `fx_rate_service.py:159-162, :208`, so it runs **before any lock or ledger write**): changed `keep_full` members with `check_rules(..., attached=<their current rule links>)`, `new` members fresh, the convert anchor with its attached links. A member validation error here → 422 `members.{i}.{field}`; FX cache rows written by this step may remain after a later rollback (they are a cache, not ledger state).
3. **Lock** in the accepted order: (a) schedule hook rows when the operation touches a scheduled period — shared import key → definition → instance (`lock_for_entry_delete` / the D32 helpers), **before** any group or entry lock; a plain split `PUT`/`POST`/convert takes no schedule write locks (a scheduled group is refused in step 1 and again in step 4); (b) every affected `entry_group` row by id ascending FOR UPDATE; (c) every affected top-level entry by id ascending FOR UPDATE (convert: the ungrouped anchor is the only entry); defaults/`remember_all_defaults` last. Never acquire a row of an earlier class while holding a later one.
4. **Re-read and re-validate inside the lock**: group state; membership and protected set recomputed from locked rows; convert refusals (§1.6) on the locked anchor; the stored values used for the `unchanged` decision. If anything differs from step 1 (membership changed, an `unchanged` member changed under us, a new settlement appeared) → rollback and 409 `retry` (the client re-reads and re-submits); no re-preparation inside the lock.
5. **Write** (delete/update/insert) with no helper that commits; one commit at the end. Any failure → rollback; no ledger row persisted (FX cache excepted).

### 1.5 `PUT /splits/{group_id}` — upsert by member id

Partition after step 3: `keep_full` (full members with `id`), `keep_meta` (`SplitKeepIn`), `new` (no `id`), `drop` (current members absent from the payload).

- A protected member must be in `keep_meta`; sent as a full member or dropped → 409 `member_locked` (reason in `detail`). A non-protected member may be sent either way.
- `drop`: for each, `assert_not_referenced(loan_entry_id=id)` (same refusal as single delete) then `delete_entries_cascade`.
- `keep_full`: update in place like a single-entry update (`_apply` + fee/discount/rule-link rebuild, `check_rules(..., attached=<its current links>)` so a rule disabled/expired after import still round-trips). Reward **ledger rows** are never touched by this endpoint. A `keep_full` member whose financial fields, FX inputs, category, counterparty and dates are all unchanged is a no-op for FX/fee/discount (no re-resolution).
- `keep_meta`: apply only the sent metadata fields.
- `new`: `insert_prepared(..., group_id)`.
- Dates: only full members carry `entry_date/entry_time/posted_date`; `SplitKeepIn` never does, so a protected member's dates are never changed by this endpoint (the frontend disables the parent date tiles when any child is protected, §2.5).
- Group fields from the payload. `remember_all_defaults` for `new` and changed `keep_full`.
- Scheduled-group guard (`assert_group_not_scheduled`) and `assert_editable` as today; a referenced loan in `keep_full` may not change kind, currency or account (409 `member_locked`).
- Response §1.6.

### 1.6 Responses, cardinality, errors, dissolve, convert

**Response (all four operations)**: `{group_id: int | null, member_ids: int[], members: [{id: int, client_key: str | null}]}` — `member_ids`/`members` in **request order**; `SplitOut.group_id` becomes nullable.

**Dissolve** = `PUT /splits/{gid}` with exactly one member, which must be an existing member of the group (`id` required; full or keep form): after the member update, copy the **payload's** group `name`, `merchant`, `description` onto the member **field by field where the member's resulting value is empty**; detach (`group_id = NULL`, flush); delete the group row. `group_id: null` in the response. Only `kind='split'` groups dissolve; installment/transfer groups never do.

**Convert** = `PUT /entries/{entry_id}/split` with a `SplitIn` per §1.3. Refusals (checked before and again inside the anchor lock): anchor already in any group → 409 `already_grouped`; anchor protected (§1.2) or not an editable kind → 409 `entry_locked`; anchor schedule-generated (`source='schedule'`) → 409 `kind_not_splittable`; ledger import-locked → 409 `import_running`. Then: create `EntryGroup(kind='split', …)`, attach and update the anchor in place (its id is stable), insert the others. A retry after success → 409 `already_grouped`; two concurrent converts of the same entry: one succeeds, the other 409.

**Error precedence** (follows §1.4's phase order): schema/cardinality 422 → group/member 404 → state 409 (`already_grouped`, `entry_locked`, `member_locked`, `import_running`, `group_scheduled`, `group_locked`) → member validation 422 keyed `members.{i}.{field}` → post-lock 409 `retry`. One HTTP error per request; no ledger row written on error.

**`DELETE /splits/{gid}`** unchanged (whole group; refuses as today).

### 1.7 `DELETE /entries/{id}` auto-dissolve

Before locking anything, read the **affected set**: the entry, its transfer pair (if any), every `split` group that contains any of those entries, and every member of each such group (both legs' groups expanded up front, not leg by leg). Then lock in the §1.4 order: schedule hook rows for the period(s) (`lock_for_entry_delete`, today's call) → all affected group rows by id → all affected top-level entries by id. Re-read membership and the affected set under the lock; if it differs → rollback + 409 `retry` (never take an earlier-class row while holding a later one). Run today's refusals (referenced loan, reward rows, scheduled-period semantics incl. `is_partial`/reopen unchanged). After the delete, for each affected `split` group left with exactly one member: dissolve copy (group name/merchant/description → survivor's empty fields), detach the survivor (`group_id = NULL`, flush), delete the group — same transaction. Groups of other kinds are untouched.

### 1.8 Backend tests (`tests/integration/test_splits.py`, new `test_split_convert.py`, `test_split_dissolve.py`)

- Upsert: keep_full/keep_meta/new/drop in one call; ids stable; `member_ids` order; unknown/foreign id → 404; duplicate id / duplicate client_key → 422; protected member as full → 409; protected dropped → 409; protected via `SplitKeepIn` with name change → 200 and signed amount/flags/links/counterpart/balances unchanged (imported transfer, system, refund, settlement incl. orphan); referenced loan drop → 409, its kind/currency change → 409; attached disabled/expired rule round-trips on keep_full, same rule rejected on new; untouched keep_full does not re-resolve FX; reward ledger rows untouched; cold-cache FX + concurrent PUT/PUT and PUT/settle serialize correctly (locks taken after FX commit).
- Convert: happy path keeps anchor id; invalid second member or FX failure → anchor unchanged, no group; already_grouped / entry_locked (transfer, system, settlement, refunded original, orphan settlement) / kind_not_splittable / import_running; retry → 409 with no duplicate group; concurrent converts → one wins; convert vs delete/update of the anchor interleaving.
- Dissolve: via PUT single member (full and keep forms); copy only into empty fields, field by field, from the payload's parent values; `group_id: null`; survivor protected → §1.5 rules apply.
- DELETE auto-dissolve: survivor fields/links intact; race with survivor PUT, another DELETE, group PUT — no deadlock, no dangling FK; scheduled multi-member period delete keeps partial/reopen semantics; installment group not dissolved.
- POST: single member → 422; member with id → 422; response envelope incl. `response_model` serialization for all four operations.
- Protected by real kind: one case per `SYSTEM_KINDS` value (fee, discount, reward, interest, balance_adjustment), an orphan refund (`kind='refund'`, `refunds_entry_id` NULL), a transfer leg — read flag true, keep_meta OK, full/drop → 409, signed amounts/links/rows unchanged.
- Phases/precedence: foreign id + bad account → 404 (not 422); expired attached rule on an unchanged keep_full → not prepared, 200; only another member changed with a cold FX cache / unavailable provider → the untouched member makes no FX request and keeps fx_source/rate/amount/children; membership changed between read and lock → 409 `retry`.
- Lock order: deterministic DELETE-dissolve vs group PUT in both orders, DELETE vs schedule repost/skip, transfer legs in two different split groups (both survivors in the first lock set) — no deadlock, no dangling group.
- Mixed protected + editable group: parent-date change only reaches full members; dissolve onto a protected survivor leaves its dates.

## 2. Frontend model and flow (PR-6, Multica)

### 2.1 State (`entry-form`)

```ts
interface ChildDraft {
  key: string;                 // uuid, UI identity; never sent except as client_key
  id: number | null;           // persisted member id
  protected: boolean; protectedReason: string | null;
  // editable (editable kinds only; protected children expose only name/project/tags/description)
  kind; categoryId; amountExpr; accountId; counterpartyName; name; projectId; tags; description;
  fee; discount; fx; ruleIds; invoice;
  // per-child provenance (loaded values, never merged across children)
  loaded: { entryDate; entryTime /* raw string as received */; postedDate; merchant; attachedRuleIds; originalAccountId; source;
            signedAmount /* server signed amount in the account currency */; signedBase /* in the main currency, null when unknown */; isSettlement; kind } | null;
  rulesTouched: boolean; pendingCategoryId: number | null; generation: number; // async ownership
}
children = signal<ChildDraft[]>([])             // length ≥ 1
selected = signal<string | 'parent'>(key)
parent = { name; merchant; description; entryDate; entryTime; postedDate; dateTouched: boolean }
```

- Single child = today's single-entry form (no parent bubble; merchant/date/time tiles inline; saves as a plain entry). `isSplit = children().length ≥ 2`.
- The existing per-entry signals (`amountExpr`, `accountId`, `category`, `fx`, `fee`, `discount`, `ruleIds`, `pendingCategoryId`, `rulesTouched`, `originalAccountId`, …) become **views over the selected child**; in parent mode they are inert (no writes; the keypad is hidden and `↵`/pending commits are flushed to the previously selected child before the switch).
- **Async ownership**: every callback that writes a draft (account detail → rule defaults, category prefill, FX resolution, fee/fx sheets, keypad commit, counterparty create) carries the child's `key` and the child's `generation` at request time; a result whose key no longer exists or whose generation is stale is dropped. Children sharing account/kind are never disambiguated by value.
- **Dirty snapshot** (`DirtyFormRegistry`): `{parent, children (without key/generation/selection), scheduleDraftKey, transferPanel.draftKey, targetExpr}` — the mode-specific parts of today's snapshot stay; only the single-entry/members part is replaced. Baseline/pending-discard clearing rules unchanged (load complete, successful save, save-and-continue). Switching bubbles does not dirty; async defaults do not dirty.

### 2.2 Mode matrix

| Form mode | ＋ (add child) | Kind tabs | Event type row |
|---|---|---|---|
| New record, single, editable kind, 單次 | enabled | all | shown |
| New record, 週期／分期 selected | hidden | all | shown |
| Definition edit (`?schedule=`) | hidden | per today | shown (today's rules) |
| Edit existing single: editable kind, not protected, not schedule-generated, not import-locked | enabled | per today | shown |
| Edit existing single: transfer / 系統 / schedule-generated / protected / import-locked | hidden, hint on the strip 「此記錄不能拆帳」 | per today | per today |
| Split (≥2 children), child selected | enabled | act on **and show** the selected child's kind; 轉帳 and 系統 disabled | hidden |
| Split, parent selected | enabled (new child inherits the last child's kind/account) | hidden | hidden |
| Split, protected child selected | enabled | shown disabled (reason hint) | hidden |

Switching kind on a child whose category belongs to another kind clears its category and reopens the grid for it.

### 2.3 Bubble strip (`category-picker` strip, extended)

- Single child: `[selected category bubble] [＋]` (today's strip; `splittable`/`addLine` removed in favour of this ＋).
- Split: `[多類別 <net> (N)] [child bubbles…] [＋]`, horizontally scrollable, selected bubble outlined. Child bubble = category icon/colour, category name (or kind label), signed amount; no category yet → dashed 「未選類別」; protected → lock glyph.
- ＋ → `addChild()`: pushes `{kind, accountId, everything else empty}` where `prev` = the selected child, or the last child in parent mode; `kind` = `prev.kind` when it is an editable kind, else the kind of the nearest preceding editable child, else `expense`; `accountId` = `prev.accountId` unless that account is archived, then the first active account; a new child's `merchant` is `null`; selects it; opens the category grid (drill-in) for it; after the pick the grid collapses and focus goes to the amount tile (keypad on phone). The category last-use prefill (`hh.accounting.lastUse.<categoryId>`) is **not applied to children**; it keeps driving a single-mode record only.
- Tapping a child bubble selects it; tapping the parent bubble selects `'parent'`.
- ＋ is disabled with the hint 「最多 50 項」 when `children().length ≥ 50`; a loaded group larger than 50 shows the hint and keeps per-child editing (PUT accepts the existing count, §1.3).
- Removing a child: 「移除此項」in the child's tile area. Disabled (with hint) when it is the last child, the child is protected, the child is the **anchor** of a convert (editing an existing single entry: that child cannot be removed or replaced), or removing it would leave an existing group with no persisted member (at least one child with `id` must remain while `groupId` is set). After removal the previous child is selected (the first when none).
- A split that drops to one child renders single mode; if `groupId` is set the save dissolves (§2.7) and the single layout shows the per-field notice (§2.8).

### 2.3a Single ↔ split field map (client side)

| Event | parent.name | parent.merchant | parent.description | parent dates | child fields |
|---|---|---|---|---|---|
| Single → ＋ (first split) | `''` (group title optional) | the single's merchant (moved; child 1 `merchant` → `loaded.merchant` for a persisted anchor, `null` for a new record) | `''` | the single's date/time/posted date; `dateTouched` = true for a new record, false for an existing anchor | child 1 keeps its own name/description/project/tags/fee/fx/rules/invoice |
| Split → 1 child, no `groupId` (unsaved draft) | dropped (notice 「整筆名稱「X」不會保留」 if non-empty) | becomes the single's merchant when non-empty, else the child's `loaded.merchant` | dropped (notice if non-empty) | become the single's dates | unchanged |
| Split → 1 child, `groupId` set | server copy per §1.6 (field by field into the survivor's empty fields); per-field notices per §2.8 | same | same | unchanged (§2.7 date rule) | unchanged |
| Single mode bindings | 名稱 tile = child 1 `name`; 備註 tile = child 1 `description`; 商家 tile = `parent.merchant` (saved as the entry's merchant) | | | parent dates = the entry's dates | |

Notices for dropped non-empty parent fields stay until saved or the second child is re-added.

### 2.4 Child mode

Tiles: 金額 (keypad), 名稱, 帳戶 (picker), 專案, 對象 (應收／應付 only), 發票, 標籤 chips, 備註 (this child), fee/discount chips, FX, reward rules — today's single-entry tiles minus merchant/date/time. A protected child shows its financial tiles read-only with the reason (e.g. 「已有收還款，金額與帳戶不可更改」) and editable 名稱／專案／標籤／備註 only.

### 2.5 Parent mode

- Parent card (read-only): net per currency over children's amounts — editable drafts contribute `signed(kind, converted base amount)`; protected/non-editable children (settlements, refunds, system kinds, transfer legs) contribute their server `loaded.signedAmount` as stored (never re-signed by kind); fee/discount/reward estimates excluded; one line per currency, no summing of FX `original_currency` inputs; `N 項 · M 個帳戶`; 「預估回饋」 per currency when any child has rule rewards (estimate from rules; existing reward ledger rows are not part of this estimate).
- Tiles: 商家, 日期, 時間, 入帳日, 整筆名稱, 整筆備註; 刪除整組 when editing. Keypad hidden. `dateTouched` turns true on the first change to 日期／時間／入帳日. When any child is protected the three date tiles are disabled with the hint 「含受保護子項，日期不可更改」 (keep payloads never carry dates, §1.5).

### 2.6 Load (edit an existing split)

- Fetch all members; `children` in `group_members` order with `id`, `protected`/reason, `loaded` provenance (dates, raw `entry_time`, merchant, attached rule ids, original account, source); the opened member selected; `rulesTouched=true` for loaded children (as today's edit).
- Parent tiles from the group (name/merchant/description) and from the **first member's** dates; `dateTouched=false`. If members' dates/times differ: caption 「子項日期不一致」 on the parent date tile.
- Per-child merchant: shown read-only under 備註 as 「商家（此項）」 only when non-empty; sent back unchanged.

### 2.7 Save plan (`planEntrySave` → `toSplitInput`)

| State | Call |
|---|---|
| 1 child, no `groupId`, new or existing single | today's create/update entry |
| 1 child, `groupId` set (dropped to one) | `PUT /splits/{gid}` with that member (dissolve); never reachable without a persisted member (§2.3) |
| ≥2 children, new record | `POST /splits` (no ids) |
| ≥2 children, editing existing single (`entryId`) | `PUT /entries/{entryId}/split`; the anchor is the child with `id === entryId` (always present, §2.3) |
| ≥2 children, `groupId` set | `PUT /splits/{gid}`: protected children as `SplitKeepIn` (metadata only), others full with their `id`, new ones without |

Member payload: every per-member field explicit; `entry_date/entry_time/posted_date` = parent values when `dateTouched` or the child is new, else the child's `loaded` raw values (the raw `entry_time` string is re-sent unchanged; no minute truncation); `merchant` = `loaded.merchant` for persisted children, `null` for new ones (children never edit it); `client_key` = `key`. Group fields from `parent`.

After save: ids mapped back by `client_key`; navigate to the selected child's id, or the first member when the parent was selected (dissolve → the survivor id). `rememberEntryUse` only on the single-entry path; recent accounts updated with every child's account. Failed save keeps draft + dirty; `members.{i}.{field}` → child by index → `key`, select it, show the field error.

### 2.8 Notices and guards

- Dissolve notice (single layout, above the tiles, one line per field): 「整筆名稱「X」將不保留」／「整筆商家「X」將不保留」／「整筆備註將不保留」 when the payload's parent value is non-empty **and** the surviving child's own value is non-empty (copy would be skipped). Until saved or undone.
- ＋ availability per §2.2 with hints.
- Esc contract unchanged (PR-2, PR-5): picker child grid → main grid; reopened main with a selection → strip; sheets/overlays → form `cancel()` → discard prompt. Parent mode behaves like child mode.

### 2.9 Removal of old code

`split-lines/*` deleted; `category-picker` `splittable`/`addLine` replaced by §2.3; `entry-save.ts` `toSplitInput` rewritten to the explicit-field contract (no `?? shared.project_id`); `members`/`splitGroup` signals replaced by `children`/`parent`; `entryInputFromDetail` extended to fill `loaded` provenance.

### 2.10 Timeline and detail

- Timeline: one row per group (unchanged); title = group name, else 「多類別」; badge N.
- Entry detail of a member: parent card at the top (多類別 · net per currency · N 項 · M 個帳戶 · 商家 · 日期) + child list (icon, name/category, amount, account, lock glyph when protected; the open one highlighted); existing per-member actions; 編輯 opens the form with that child selected. `group_members` carries `protected`/`protected_reason` (§1.2).

### 2.11 Frontend tests

entry-form: ＋ creates a child with previous kind/account and opens the grid; picking selects the new child and focuses the amount; keypad writes only the selected child, pending `↵`/commit flushed before a switch; bubble switch swaps tile values and does not dirty; parent mode: per-currency net, N, M, keypad hidden, kind tabs hidden; remove: re-selects previous; disabled on last / protected / anchor / last persisted member; drop-to-one shows per-field notices; save plan for all five rows incl. anchor present in convert, protected sent as keep; explicit `project_id: null`, `tags: []`; untouched parent date re-sends each child's raw date/time, touched unifies, one touched field only; `members.1.amount` → child 2 selected; mode matrix: ＋ hidden for recurring/installment draft, definition edit, transfer, system, schedule-generated, protected, import-locked; split → 轉帳/系統 tabs disabled; tabs show the selected child's kind with mixed kinds; async: stale account/category/FX responses after switch or removal are dropped (same account/kind children, interleaved responses, parent-mode pending commit), changing child A's account does not clear B's FX/fee/rules; two archived originals; dirty: schedule-only / transfer-leg-only / target-only edits still prompt; save failure stays dirty; success and save-and-continue clear. category-picker: strip bubbles, outline, dashed 未選類別, lock glyph; PR-5 Esc contract regression (child grid → main; reopened main → strip + focus; unselected main → form). account-picker regression: archived/exclude/focus-return unchanged. entry-detail: parent card, child list, 編輯 selects the child. timeline: title fallback. Field map: new single with name/merchant/note → ＋ → edit parent fields → remove child 2 → single save keeps name/note/merchant, no undefined merchant; same with an existing anchor; ＋ on a protected transfer/refund/reward child and in parent mode with a non-editable last child produces an editable kind; ＋ disabled at 50 and for an over-50 loaded group; parent card signs: receivable original + collection, payable original + repayment, ± balance adjustments, cross-currency FX; parent date tiles disabled with a protected child.

## 3. Delivery

- **PR-B (backend, owner's session)**: §1 with §1.8 tests; `openspec/specs/accounting-ledger` updated for upsert/dissolve/convert/protected rules and the nullable `group_id`; no frontend change.
- **PR-6 (frontend, Multica via lead-astra)**: §2 after PR-B is merged; branch from main; same rules as AGENT-61/62/64.
- Release: backend restart (new routes, no migration) + frontend publish; demo preview and 4300 refresh first.

## Review focus (each pinned to a test above)

1. Old split with differing member dates, change one child's amount only → every member keeps its own date/time (§1.8 keep_full no-op, §2.11 untouched dates).
2. `PUT /entries/{id}/split` with an invalid second member, or an FX failure → anchor unchanged, no group (§1.8 convert).
3. Upsert with a protected member as `SplitKeepIn` and another member dropped → 200; the same protected member sent as a full member → 409 and nothing written (§1.8). Protected must be detected by real kinds (fee/discount/reward/interest/balance_adjustment, orphan refund), not a label (§1.2).
4. Keypad after a bubble switch targets the new child; pending commit flushed to the old one (§2.11 async).
5. A category/account response for a removed or replaced child is dropped by key + generation (§2.11 async).
