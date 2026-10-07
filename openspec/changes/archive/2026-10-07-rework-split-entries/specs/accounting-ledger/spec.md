## MODIFIED Requirements

### Requirement: Split endpoint

`POST /api/accounting/splits` SHALL take group fields (`name`, `merchant`, `description`), shared `entry_date`, `entry_time`, `posted_date`, `project_id` and `tags`, and `members`: 2 to 50 entry payloads as in "Entry write endpoints" (each with its own kind, account, category, amount, counterparty, fee and discount children and rule ids, plus an optional `client_key` of 1 to 64 characters that is echoed and never stored). The shared fields SHALL apply to every member that omits them. No `POST` member SHALL carry `id` or `keep` (HTTP 422 naming `members.{i}.id` / `members.{i}.keep`); fewer than 2 or more than 50 members SHALL be refused with HTTP 422 naming `members`. Duplicate member ids or duplicate non-null client keys in any split body SHALL be refused with HTTP 422. `POST` SHALL create one `entry_group` of kind `split` and all members in one transaction. `PUT /api/accounting/splits/{group_id}` SHALL follow "Split upsert by member id" and "Dissolving a split"; `PUT /api/accounting/entries/{id}/split` SHALL follow "Converting an entry into a split". Every split write SHALL answer `{group_id, member_ids, members: [{id, client_key}]}` with `member_ids` and `members` in request order, `group_id` being null after a dissolve. `DELETE /api/accounting/splits/{group_id}` SHALL lock the schedule rows of a posted period that lists the members, then the `entry_group` row, then the members, SHALL refuse groups holding transfer legs or non-editable kinds (HTTP 422 naming `members`), and SHALL delete the group and its members.

#### Scenario: Friend paid for lunch
- **WHEN** a split is posted with members `payable +100 (借入, Alan)` on account A and `expense -100 (早餐)` on account A
- **THEN** A's balance SHALL be unchanged and both entries SHALL share one group of kind `split`

#### Scenario: A one-member split is refused on create
- **WHEN** `POST /api/accounting/splits` is called with a single member
- **THEN** the response SHALL be HTTP 422 naming `members` and nothing SHALL be written

#### Scenario: Client keys echoed in request order
- **WHEN** a split is posted with members carrying `client_key` `k-a` and `k-b`
- **THEN** the response `members` SHALL be `[{id: <first>, client_key: "k-a"}, {id: <second>, client_key: "k-b"}]` and `member_ids` SHALL list the same ids in the same order

## ADDED Requirements

### Requirement: Protected split members

A split member SHALL be protected when it is a settlement (`is_settlement`, with or without `settles_entry_id`), a `refund` (with or without `refunds_entry_id`), a transfer leg (`transfer_group_id` set or kind `transfer_out` / `transfer_in`), of a kind outside the editable kinds (`fee`, `discount`, `reward`, `interest`, `balance_adjustment`), an original that other entries settle or refund, or a `receivable` / `payable` that a schedule definition which is not `ended` references as `loan_entry_id`. One predicate SHALL decide both the read flag and the write guard. `GET /api/accounting/entries/{id}` SHALL return, for every row of `group_members`, `protected` (boolean) and `protected_reason` (`settlement`, `refund`, `transfer`, `system`, `settled_original`, `scheduled_loan`, or null).

#### Scenario: System kinds are protected by their real kind
- **GIVEN** a split group holding an expense and a `reward` row
- **WHEN** the expense's detail is read
- **THEN** the reward row SHALL carry `protected = true` and `protected_reason = "system"` and the expense `protected = false`

#### Scenario: An orphan refund is protected
- **GIVEN** a split member of kind `refund` whose `refunds_entry_id` is null
- **WHEN** the group is read
- **THEN** that member SHALL carry `protected_reason = "refund"`

### Requirement: Split upsert by member id

`PUT /api/accounting/splits/{group_id}` SHALL accept 1 to max(50, the group's current member count) members, each either a full member (an entry payload, optionally with `id`) or a keep member `{id, keep: true, name?, project_id?, tags?, description?, client_key?}` that SHALL refuse every other field (HTTP 422). Every `id` SHALL belong to the group (else HTTP 404 with a message starting `member_not_found`). The server SHALL partition the members into keep_full (full with `id`), keep_meta (keep form), new (no `id`) and drop (current members absent from the body). A protected member sent as a full member or dropped SHALL be refused with HTTP 409 `member_locked` naming the reason. A dropped member that a schedule definition references as `loan_entry_id` SHALL additionally be refused as a single delete is (HTTP 409), whatever its kind. Each keep_full member SHALL be compared with its stored row on unsigned inputs (an online-FX payload with `amount` and `fx_rate` null equals a stored row with the same `original_amount` / `original_currency` and `fx_source = 'fx_api'`): when kind, amount and FX inputs, account, category, counterparty, fee, discount, rule ids, invoice and dates are all equal and name, merchant, project, tags and description too, the member SHALL be left untouched; when only the latter differ, only they SHALL be written (no FX request, no child or rule-link rebuild); otherwise the member SHALL be updated in place like `PUT /api/accounting/entries/{id}`, keeping its id, with its current rule links accepted even if the rule was disabled or expired since. keep_meta members SHALL change only the fields sent (`null` and `[]` included). New members SHALL be inserted into the group; drop members SHALL be deleted with their children. The group's `name`, `merchant` and `description` SHALL be set from the body. Reward ledger rows SHALL never be touched. Category defaults SHALL be remembered for new and financially changed members only.

#### Scenario: Ids stay stable
- **GIVEN** a split with members 午餐 −100, 飲料 −50 and 甜點 −30
- **WHEN** the PUT sends 午餐 with `id` and amount 120, `{id: 飲料, keep: true, name: 珍奶}` and a new member 點心 40
- **THEN** 午餐 and 飲料 SHALL keep their ids, 甜點 SHALL be deleted, 點心 SHALL be inserted and `member_ids` SHALL follow the request order

#### Scenario: Protected member as keep
- **GIVEN** a split holding an expense, a second expense and a settlement member
- **WHEN** the PUT sends the settlement as `{id, keep: true, name: 改名}`, the first expense changed and omits the second expense
- **THEN** the response SHALL be HTTP 200, the second expense SHALL be deleted and the settlement's amount, sign, links and dates SHALL be unchanged

#### Scenario: Protected member as full refused
- **WHEN** the same settlement is sent as a full member
- **THEN** the response SHALL be HTTP 409 `member_locked` and nothing SHALL be written

#### Scenario: Members keep their own dates
- **GIVEN** an imported split whose members are dated 2026-09-01 09:15:30 and 2026-09-03 20:00
- **WHEN** the PUT re-sends both with their stored values and changes only the second amount
- **THEN** each member SHALL keep its own date and time and the first SHALL not be written

### Requirement: Dissolving a split

A `PUT /api/accounting/splits/{group_id}` with exactly one member SHALL dissolve the split: the member SHALL be an existing member of the group (a member without `id` SHALL be refused with HTTP 422 naming `members.0.id`), every other member SHALL be dropped under the upsert rules, and after the member update the body's `name`, `merchant` and `description` SHALL be copied onto the member field by field only where the member's value is null or whitespace; the member SHALL then be detached (`group_id = NULL`) and the group row deleted in the same transaction, and the response SHALL carry `group_id: null`. Only groups of kind `split` SHALL dissolve.

#### Scenario: Copy into blank fields only
- **GIVEN** a split whose surviving member has merchant `自己的店` and a whitespace description
- **WHEN** a one-member PUT sends that member with group fields `新名` / `新店` / `新備註`
- **THEN** the member SHALL end with name `新名`, merchant `自己的店`, description `新備註` and no group

### Requirement: Converting an entry into a split

`PUT /api/accounting/entries/{id}/split` SHALL take a split body with 2 to 50 full members of which exactly one carries `id` equal to the path id (the anchor) and no other carries `id` or `keep` (HTTP 422). It SHALL refuse, before and again inside the anchor's row lock: an anchor already in any group (HTTP 409 `already_grouped`), a protected anchor or one of a non-editable kind (HTTP 409 `entry_locked`), an anchor posted by a schedule (`source = 'schedule'`, HTTP 409 `kind_not_splittable`), and an imported anchor before cutover (HTTP 409 `locked_until_cutover`). It SHALL then create an `entry_group` of kind `split` with the body's group fields, attach and update the anchor in place (its id is stable) and insert the other members, answering the split envelope. A failed member validation or FX resolution SHALL leave the anchor unchanged and create no group. A repeated convert after success SHALL answer HTTP 409 `already_grouped`; of two concurrent converts of one entry exactly one SHALL succeed.

#### Scenario: Anchor keeps its id
- **GIVEN** an expense `晚餐 −100`
- **WHEN** it is converted with members anchor 70 and a new member 30
- **THEN** the anchor SHALL keep its id with amount −70 in a new split group together with the new member

#### Scenario: Invalid second member
- **WHEN** the second member is a `receivable` without `counterparty_id`
- **THEN** the response SHALL be HTTP 422 naming `members.1.counterparty_id`, the anchor SHALL be unchanged and no group SHALL exist

### Requirement: Split write phases, errors and lock order

Every split write (create, upsert, dissolve, convert) SHALL run: (1) an unlocked read and classification; (2) validation and FX resolution, which may commit the FX cache and therefore SHALL precede every lock and ledger write; (3) locks in the ledger's order — `entry_group` rows by ascending id, then the entries in one `SELECT … FOR UPDATE` by ascending id, then category defaults (a split `PUT` and a convert take no schedule lock; convert locks only its ungrouped anchor); (4) a re-read inside the locks, answering HTTP 409 `retry` when membership, the protected set or a compared member changed since (1); (5) the writes with one commit. Errors SHALL follow the phase order: request schema (HTTP 422) → group missing (404) → group listed by a posted schedule instance (409 `group_scheduled`, naming the instance; checked before the group-kind check so a scheduled installment group answers 409) → not a split (404) → cutover lock (409 `locked_until_cutover`) → member cardinality (422) → `member_not_found` (404) → `member_locked` / `already_grouped` / `entry_locked` / `kind_not_splittable` (409) → member validation (422 naming `members.{i}.{field}`) → `retry` (409). A 409 message SHALL start with its code. No ledger row SHALL be written on any error.

#### Scenario: Membership changed before the lock
- **GIVEN** a split PUT has classified the group's two members
- **WHEN** another request adds a member before the PUT takes the group lock
- **THEN** the PUT SHALL answer HTTP 409 `retry` and write nothing

#### Scenario: Unknown member before member validation
- **WHEN** a PUT names a member id of another group together with an unknown account
- **THEN** the response SHALL be HTTP 404 `member_not_found`, not HTTP 422

### Requirement: Deleting a member dissolves a one-member split

`DELETE /api/accounting/entries/{id}` SHALL, before taking any group or entry lock, read the affected set: the entry, its transfer legs, every `split` group containing any of them and every member of those groups. After locking the schedule rows of a posted period that lists the entry (unchanged), it SHALL lock every affected `entry_group` row by ascending id, then every affected entry in one statement by ascending id, and re-read the affected set; any difference SHALL answer HTTP 409 `retry` and a target already deleted HTTP 404. The existing refusals (referenced loan, reward rows, cutover lock) and the scheduled-period semantics SHALL be unchanged. After the delete, every affected group of any kind left without rows SHALL be deleted, and every affected `split` group left with exactly one top-level member SHALL be dissolved as in "Dissolving a split" using the group's own `name`, `merchant` and `description`. Groups of other kinds SHALL never be dissolved.

#### Scenario: Survivor takes the group name
- **GIVEN** a split `聚餐` of two members, the survivor without a name
- **WHEN** the other member is deleted
- **THEN** the survivor SHALL be named `聚餐`, belong to no group, and keep its amount, children, links and rule links

#### Scenario: Transfer legs in two splits
- **GIVEN** a transfer whose legs belong to two different split groups, each with one other member
- **WHEN** one leg is deleted
- **THEN** both legs SHALL be deleted and both groups dissolved into their remaining members

#### Scenario: Installment group left with one member
- **GIVEN** an `installment` group of two members
- **WHEN** one member is deleted
- **THEN** the group SHALL keep the other member
