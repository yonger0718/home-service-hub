# Split upsert API (PR-B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement §1 of the split (多類別) entry rework in accounting-service: id-stable upsert `PUT /splits/{gid}`, dissolve through a one-member PUT, `PUT /entries/{id}/split` (convert a single entry without losing its id), protected members (read flag + locked write guard), DELETE auto-dissolve, one response envelope for all four writes, and the openspec ledger contract, with no schema migration and no frontend change.

**Architecture:** Every split write runs the spec's five phases: (1) unlocked read + classification (protected set, keep_full/keep_meta/new/drop, canonical "unchanged" comparison), (2) prepare (validation + FX, which may commit the session through the FX cache), (3) locks in the accepted D32 order (schedule rows → `entry_group` rows ascending → entries ascending in one statement → category defaults), (4) re-read under the locks with 409 `retry` on any drift, (5) writes through helpers that never commit. Two new modules carry the shared rules: `split_protection.py` (one predicate for the read flag and the write guard) and `split_compare.py` (canonical member signatures). `split_service.py` is rewritten around them; `entry_write_service.py` gains `apply_prepared_update`, `dissolve_group` and an affected-set `delete_entry`.

**Tech Stack:** Python 3.13, FastAPI 0.129, Pydantic 2.13 (callable `Discriminator` + `Tag`), SQLAlchemy 2.0 (`SELECT … FOR UPDATE`, READ COMMITTED), PostgreSQL integration tests (`tests/helpers.race`), OpenSpec CLI 1.3.1.

**Spec:** `/home/opc/workspace/home-hub-schedules/docs/superpowers/specs/2026-10-06-split-entry-rework-design.md` §1 (1.1–1.8) — binding. §2 (frontend, PR-6) is out of scope. Accepted contracts: `openspec/specs/accounting-ledger/spec.md`, `openspec/specs/accounting-schedules/spec.md` (D29/D32/D33).

---

## Global Constraints

Copied verbatim from the spec's "Global constraints":

- Frontend: Angular 21 standalone + signals, Vitest; backend: FastAPI + SQLAlchemy 2.0 + Alembic. **No schema migration** in this design; `SplitOut.group_id` becomes nullable (API change, listed in §1.6).
- Accepted contracts untouched: schedules (`openspec/specs/accounting-schedules`, incl. D29/D32/D33 lock order and loan references), the dirty-form registry and overlay Esc contract (UX refinement PR-2), the account picker (PR-3), the category drill-in grid incl. its Esc contract (PR-5: child grid → main grid; reopened main with a selection → strip + focus; unselected main → form).
- Backend changes ship first in their own PR (owner's session; Multica cannot reach Postgres); the frontend PR (Multica) targets the merged API.
- Every rule below has a test (§1.8, §2.11). `pytest` (incl. Postgres integration), `cd frontend && npm test -- --watch=false`, `npx ng build` green. Checked at 390×844, 760×820, 1280×800.
- Terms: **anchor** = the existing entry being converted; **protected member** = §1.2; **empty** (for copy rules) = `null` or whitespace-only after trim; **editable kinds** = `EditableKind` (expense, income, receivable, payable).

Added for this PR:

- No Alembic revision, no model change. No file under `frontend/` changes (Task 10 checks the diff).
- Work in the worktree `/home/opc/workspace/home-hub-splitapi` (branch `feat/split-upsert-api`, base `main` @ 4e301e2). The worktree has no `.venv`; run tests with the schedules worktree's interpreter, from the service directory of THIS worktree, e.g.
  `cd /home/opc/workspace/home-hub-splitapi/services/accounting-service && /home/opc/workspace/home-hub-schedules/services/accounting-service/.venv/bin/python -m pytest tests/integration/test_splits.py -q`
  (the root `.env` symlink gives the dev Postgres; `conftest.py` creates a throwaway database per session).
- Services never commit; routers commit once. Nothing between "prepare" and the end of a write may commit (FX lookups only happen in prepare).
- 409 messages for the new refusals are `<code>` or `<code>: <detail>` (`member_locked`, `retry`, `already_grouped`, `entry_locked`, `kind_not_splittable`, `group_scheduled`, `member_not_found` on the 404). The cutover lock keeps the accepted message `locked_until_cutover` (ledger requirement "Imported rows are read-only until cutover").
- Every commit message ends with:
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`

## Review Focus

1. **Old split, one amount changed → every member keeps its own date/time** (spec RF1). Pinned: Task 4 `test_old_split_keeps_each_member_date_when_one_amount_changes`.
2. **Convert with an invalid second member, or an FX failure → anchor unchanged, no group** (spec RF2). Pinned: Task 6 `test_invalid_second_member_leaves_the_anchor_alone`, `test_fx_failure_leaves_the_anchor_alone`.
3. **Protected member as `SplitKeepIn` + another dropped → 200; the same member as full → 409, nothing written; protection by real kinds** (spec RF3). Pinned: Task 4 `test_protected_member_takes_metadata_only[*]` (12 real shapes incl. every `SYSTEM_KINDS` value and the orphan refund), Task 2 `test_group_members_carry_the_protected_flag[*]`.
4. **Backend: no lock is held across an FX commit, and every path keeps the D32 order** (schedule rows → groups ascending → entries ascending in one statement). Pinned: Task 8 `test_cold_fx_cache_put_takes_its_locks_after_the_fx_commit`, Task 7 `test_transfer_legs_in_two_splits_dissolve_both`, `test_delete_dissolve_races_a_group_put[*]`, `test_delete_dissolve_races_a_schedule_repost[*]`.
5. **Backend: the canonical comparison never re-resolves FX for an untouched member and a fully unchanged member is a no-op.** Pinned: Task 3 `test_online_fx_echo_is_unchanged_only_against_an_online_row`, Task 4 `test_metadata_only_change_on_an_online_fx_member_makes_no_fx_request`, `test_fully_unchanged_payload_is_a_no_op`.

## File Map

| Action | File | Purpose |
|---|---|---|
| Modify | `services/accounting-service/app/schemas/writes.py` | `SplitMemberIn.id/client_key`, `SplitKeepIn`, discriminated `SplitIn.members`, distinct-id/key validator, `MAX_SPLIT_MEMBERS`, nullable `SplitOut` + `members` |
| Modify | `services/accounting-service/app/services/errors.py` | error codes + `CodedConflictError` |
| Modify | `services/accounting-service/app/schemas/ledger.py` | `GroupMemberOut` (`protected`, `protected_reason`) |
| Modify | `services/accounting-service/app/services/ledger_service.py` | fill the protected flag in `get_entry_detail` |
| Create | `services/accounting-service/app/services/split_protection.py` | §1.2 predicate |
| Create | `services/accounting-service/app/services/split_compare.py` | §1.4 canonical comparison |
| Modify | `services/accounting-service/app/services/split_service.py` | phases, create/upsert/dissolve/convert |
| Modify | `services/accounting-service/app/services/entry_write_service.py` | `apply_prepared_update`, `is_blank`, `dissolve_group`, `AffectedSet`, `affected_set`, `locked_with_legs(also_ids)`, new `delete_entry` |
| Modify | `services/accounting-service/app/routers/splits.py` | envelope responses |
| Modify | `services/accounting-service/app/routers/entries.py` | `PUT /entries/{entry_id}/split` |
| Modify | `services/accounting-service/tests/helpers.py` | `PROTECTED_REASONS`, `make_protected_member` |
| Create | `services/accounting-service/tests/unit/test_split_schemas.py` | schema rules |
| Create | `services/accounting-service/tests/integration/test_split_protection.py` | read flag |
| Create | `services/accounting-service/tests/integration/test_split_compare.py` | canonical comparison |
| Modify | `services/accounting-service/tests/integration/test_splits.py` | POST/upsert/precedence/concurrency; existing tests adapted |
| Create | `services/accounting-service/tests/integration/test_split_dissolve.py` | PUT dissolve + DELETE auto-dissolve |
| Create | `services/accounting-service/tests/integration/test_split_convert.py` | convert |
| Modify | `services/accounting-service/tests/integration/test_edit_lock.py` | `put_split` body, convert case, route inventory |
| Modify | `services/accounting-service/tests/integration/test_entry_writes.py` | barrier spy accepts `also_ids` |
| Create→archive | `openspec/changes/rework-split-entries/**` | proposal, tasks, ledger delta; archived into `openspec/specs/accounting-ledger/spec.md` |

Existing tests whose expectations change because the contract changes (each edited in the task that changes the behaviour): `test_splits.py::test_update_replaces_members_and_group_fields`, `::test_split_update_refuses_locked_group` (after-cutover tail), `::test_update_split_refuses_group_with_settlement_member`, `::test_update_split_refuses_group_with_settled_member`, `::test_update_split_refuses_group_with_transfer_leg` (422 → 409 `member_locked`), the four race tests, `test_edit_lock.py` (`put_split` body, new route), `test_entry_writes.py::test_concurrent_deletes_of_both_transfer_legs_reach_the_lock_together` (spy signature). `test_schedule_entry_hooks.py` is NOT edited: its single-member bodies still answer 409 because group-state checks precede the membership checks (see Task 4 precedence).

---

### Task 1: Schemas, error codes and the response envelope (POST)

**Files:**
- Modify: `services/accounting-service/app/schemas/writes.py` (line 8 import; lines 72–75 `SplitMemberIn`; lines 99–108 `SplitIn`; lines 214–216 `SplitOut`)
- Modify: `services/accounting-service/app/services/errors.py` (append after line 18)
- Modify: `services/accounting-service/app/services/split_service.py` (lines 7–24 imports; lines 29–43 `member_payloads`; after line 53; lines 130–138 `create_split`)
- Modify: `services/accounting-service/app/routers/splits.py` (whole file, 33 lines)
- Create: `services/accounting-service/tests/unit/test_split_schemas.py`
- Test: `services/accounting-service/tests/integration/test_splits.py` (new tests; line 156 assertion)

**Interfaces:**
- Consumes: `EntryIn`, `Int32`, `ShortText` (writes.py); `prepare_entry(db, payload, *, http_get=None, attached_rules=()) -> PreparedEntry`, `attached_rule_ids(db, entry_id) -> set[int]`, `check_project(db, project_id) -> None`, `insert_prepared(db, prepared, *, group_id=None, remember=True, source="manual") -> int`, `remember_all_defaults(db, prepared) -> None` (entry_write_service).
- Produces:
  - `writes.MAX_SPLIT_MEMBERS: int = 50`; `writes.ClientKey`; `class SplitMemberIn(EntryIn)` with `id: Int32 | None`, `client_key: ClientKey | None`; `class SplitKeepIn(BaseModel)` (`id: Int32`, `keep: Literal[True]`, `client_key`, `name`, `project_id`, `tags: list[str] | None`, `description`; `extra="forbid"`); `SplitMemberItem`; `SplitIn.members: list[SplitMemberItem]`; `class SplitMemberRefOut(BaseModel)` (`id: int`, `client_key: str | None`); `SplitOut(group_id: int | None, member_ids: list[int], members: list[SplitMemberRefOut])`.
  - `errors.MEMBER_NOT_FOUND, MEMBER_LOCKED, ALREADY_GROUPED, ENTRY_LOCKED, KIND_NOT_SPLITTABLE, GROUP_SCHEDULED, RETRY: str`; `class CodedConflictError(ConflictError)` with `__init__(self, code: str, detail: str | None = None)` and `.code`.
  - `split_service.member_payload(payload: SplitIn, member: SplitMemberIn) -> EntryIn`; `member_payloads(payload: SplitIn) -> list[EntryIn]`; `@dataclass(frozen=True) class SplitResult(group_id: int | None, members: list[tuple[int, str | None]])` with `.out() -> dict`; `@dataclass class _Member(index, item, entry_id, action, payload=None, stored=None, change=None, prepared=None)`; `_prepare(db, members: list[_Member], http_get) -> None`; `_check_create_cardinality(payload: SplitIn) -> None`; `create_split_result(db, payload, *, http_get=None) -> SplitResult`; `create_split(db, payload, *, http_get=None) -> int`.

- [ ] **Step 1: Write the failing tests**

Create `services/accounting-service/tests/unit/test_split_schemas.py`:

```python
"""SplitIn / SplitKeepIn / SplitOut (split rework §1.3, §1.6): schema-level rules, no database."""

import pytest
from pydantic import ValidationError

from app.schemas.writes import SplitIn, SplitKeepIn, SplitMemberIn, SplitOut

BASE = {"entry_date": "2026-09-01"}
FULL = {"account_id": 1, "kind": "expense", "amount": "100"}


def test_keep_and_full_members_are_told_apart_by_keep():
    payload = SplitIn(**BASE, members=[{"id": 7, "keep": True, "name": "改名"}, {**FULL, "id": 8, "client_key": "b"}, FULL])

    assert [type(member) for member in payload.members] == [SplitKeepIn, SplitMemberIn, SplitMemberIn]
    assert payload.members[0].model_fields_set == {"id", "keep", "name"}
    assert (payload.members[1].id, payload.members[1].client_key, payload.members[2].id) == (8, "b", None)


@pytest.mark.parametrize(
    "field",
    ["amount", "kind", "account_id", "category_id", "counterparty_id", "original_currency", "fx_rate", "fee",
     "discount", "reward_rule_ids", "entry_date", "entry_time", "posted_date", "merchant", "invoice_number"],
)
def test_a_keep_member_refuses_every_non_metadata_field(field):
    with pytest.raises(ValidationError):
        SplitIn(**BASE, members=[{"id": 7, "keep": True, field: None}])


@pytest.mark.parametrize(
    "members",
    [
        [{"id": 7, "keep": True}, {**FULL, "id": 7}],  # duplicate id across forms
        [{**FULL, "client_key": "x"}, {**FULL, "client_key": "x"}],  # duplicate client_key
        [{**FULL, "client_key": "x" * 65}],  # client_key longer than 64
        [{**FULL, "client_key": ""}],
        [],  # min 1
    ],
    ids=["duplicate-id", "duplicate-client-key", "long-client-key", "empty-client-key", "no-members"],
)
def test_schema_refusals(members):
    with pytest.raises(ValidationError):
        SplitIn(**BASE, members=members)


def test_null_and_empty_list_count_as_sent_on_a_keep_member():
    keep = SplitIn(**BASE, members=[{"id": 7, "keep": True, "project_id": None, "tags": []}]).members[0]

    assert keep.model_fields_set == {"id", "keep", "project_id", "tags"}


def test_split_out_allows_a_dissolved_group():
    out = SplitOut(group_id=None, member_ids=[5], members=[{"id": 5, "client_key": None}])

    assert out.model_dump() == {"group_id": None, "member_ids": [5], "members": [{"id": 5, "client_key": None}]}
```

Append to `services/accounting-service/tests/integration/test_splits.py`:

```python
def test_post_needs_two_new_members(client, db_session, seed):
    wallet = seed.account()
    db_session.commit()

    single = client.post("/splits", json=_split(_member(wallet)))
    with_id = client.post("/splits", json=_split(_member(wallet, id=1), _member(wallet)))
    with_keep = client.post("/splits", json=_split(_member(wallet), {"id": 1, "keep": True}))
    too_many = client.post("/splits", json=_split(*[_member(wallet) for _ in range(51)]))

    assert [(r.status_code, r.json()["detail"][0]["loc"]) for r in (single, with_id, with_keep, too_many)] == [
        (422, ["members"]), (422, ["members.0.id"]), (422, ["members.1.keep"]), (422, ["members"]),
    ]
    db_session.expire_all()
    assert db_session.scalars(select(EntryGroup)).all() == []
    assert db_session.scalars(select(LedgerEntry)).all() == []


def test_post_echoes_client_keys_in_request_order(client, db_session, seed):
    wallet = seed.account()
    db_session.commit()

    response = client.post(
        "/splits", json=_split(_member(wallet, client_key="k-a"), _member(wallet, amount="50", client_key="k-b"))
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"group_id", "member_ids", "members"}
    assert body["members"] == [
        {"id": body["member_ids"][0], "client_key": "k-a"}, {"id": body["member_ids"][1], "client_key": "k-b"},
    ]
    assert [m.id for m in _members(db_session, body["group_id"])] == body["member_ids"]
```

In `test_update_replaces_members_and_group_fields`, replace line 156:

```python
    assert response.json() == {"group_id": group_id, "member_ids": [m.id for m in members]}
```

with:

```python
    assert response.json() == {
        "group_id": group_id, "member_ids": [m.id for m in members],
        "members": [{"id": m.id, "client_key": None} for m in members],
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/opc/workspace/home-hub-splitapi/services/accounting-service && /home/opc/workspace/home-hub-schedules/services/accounting-service/.venv/bin/python -m pytest tests/unit/test_split_schemas.py tests/integration/test_splits.py -q`
Expected: FAIL — `ImportError: cannot import name 'SplitKeepIn'` (unit), and in the integration file `test_post_needs_two_new_members` (single member answers 201), `test_post_echoes_client_keys_in_request_order` and `test_update_replaces_members_and_group_fields` (no `members` key).

- [ ] **Step 3: Implement the schemas**

In `app/schemas/writes.py`, replace line 8:

```python
from pydantic import AfterValidator, BaseModel, ConfigDict, Field
```

with:

```python
from pydantic import AfterValidator, BaseModel, ConfigDict, Discriminator, Field, Tag, model_validator
```

Replace lines 72–75 (`class SplitMemberIn` … `entry_date: date | None = None`) with:

```python
MAX_SPLIT_MEMBERS = 50  # POST /splits and convert; PUT /splits/{id} allows max(50, the group's current member count)
ClientKey = Annotated[str, Field(min_length=1, max_length=64)]


class SplitMemberIn(EntryIn):
    """A full split member. `id` names an existing member (PUT /splits/{id} upsert, the convert anchor); without
    it the member is new. `client_key` is echoed in the response and never stored. entry_date, entry_time,
    posted_date, project_id and tags default to the group's when omitted (split_service.member_payload)."""

    id: Int32 | None = None
    client_key: ClientKey | None = None
    entry_date: date | None = None


class SplitKeepIn(BaseModel):
    """A metadata-only reference to an existing member (the only form a protected member accepts). Omitted fields
    stay as stored; sent fields, `null` and `[]` included, are applied. Amount, kind, account, category,
    counterparty, FX, fee / discount, rule, merchant, invoice and date fields are refused (extra="forbid")."""

    model_config = ConfigDict(extra="forbid")

    id: Int32
    keep: Literal[True]
    client_key: ClientKey | None = None
    name: ShortText | None = None
    project_id: Int32 | None = None
    tags: list[str] | None = None
    description: str | None = None


def _member_form(value) -> str:
    """Discriminator of SplitIn.members: `keep: true` selects SplitKeepIn, anything else a full member."""
    keep = value.get("keep") if isinstance(value, dict) else getattr(value, "keep", None)
    return "keep" if keep is True else "full"


SplitMemberItem = Annotated[
    Annotated[SplitMemberIn, Tag("full")] | Annotated[SplitKeepIn, Tag("keep")],
    Discriminator(_member_form),
]
```

Replace lines 99–108 (`class SplitIn` …) with:

```python
class SplitIn(BaseModel):
    """Body of POST /splits, PUT /splits/{id} and PUT /entries/{id}/split. Group fields plus 1+ members; the
    per-operation cardinality (POST ≥ 2 without ids, PUT ≤ max(50, current), convert: exactly the anchor with an
    id) is checked by split_service before any database read that could 404."""

    name: ShortText | None = None
    merchant: ShortText | None = None
    description: str | None = None
    entry_date: date
    entry_time: time | None = None
    posted_date: date | None = None
    project_id: Int32 | None = None
    tags: list[str] = []
    members: list[SplitMemberItem] = Field(min_length=1)

    @model_validator(mode="after")
    def _distinct_ids_and_client_keys(self) -> "SplitIn":
        ids = [member.id for member in self.members if member.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("members: duplicate id")
        keys = [member.client_key for member in self.members if member.client_key is not None]
        if len(keys) != len(set(keys)):
            raise ValueError("members: duplicate client_key")
        return self
```

Replace lines 214–216 (`class SplitOut` …) with:

```python
class SplitMemberRefOut(BaseModel):
    id: int
    client_key: str | None


class SplitOut(BaseModel):
    """Every split write (POST /splits, PUT /splits/{id} incl. dissolve, PUT /entries/{id}/split): ids and client
    keys in request order; group_id is null after a dissolve."""

    group_id: int | None
    member_ids: list[int]
    members: list[SplitMemberRefOut]
```

- [ ] **Step 4: Add the error codes**

Append to `app/services/errors.py`:

```python


# Codes of the split rework (§1.6). A 409 message is "<code>" or "<code>: <detail>"; the SPA matches the prefix.
MEMBER_NOT_FOUND = "member_not_found"  # the 404 of an id that is not a member of the group
MEMBER_LOCKED = "member_locked"
ALREADY_GROUPED = "already_grouped"
ENTRY_LOCKED = "entry_locked"
KIND_NOT_SPLITTABLE = "kind_not_splittable"
GROUP_SCHEDULED = "group_scheduled"
RETRY = "retry"


class CodedConflictError(ConflictError):
    """A 409 whose message starts with a stable code: `<code>` or `<code>: <detail>`."""

    def __init__(self, code: str, detail: str | None = None):
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.code = code
```

- [ ] **Step 5: Add the envelope, member_payload and the POST cardinality to split_service**

In `app/services/split_service.py`, replace lines 7–24 (imports) with:

```python
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import EntryGroup, LedgerEntry
from ..schemas.writes import MAX_SPLIT_MEMBERS, EntryIn, SplitIn, SplitKeepIn, SplitMemberIn
from . import schedule_entry_hooks
from .edit_lock import assert_editable
from .entry_write_service import (
    EDITABLE_KINDS,
    PreparedEntry,
    attached_rule_ids,
    check_project,
    delete_entries_cascade,
    has_settlements_or_refunds,
    insert_prepared,
    lock_group,
    prepare_entry,
    remember_all_defaults,
)
from .errors import NotFoundError, ValidationError
```

Replace lines 29–43 (`def member_payloads` …) with:

```python
def member_payload(payload: SplitIn, member: SplitMemberIn) -> EntryIn:
    """One full member as an EntryIn; fields the member did not send come from the group, except posted_date,
    which defaults to the member's own entry_date when the member sent one. The rework's client sends every
    per-member field; this inheritance stays for callers that omit them (§1.3)."""
    data = member.model_dump(exclude={"id", "client_key"})
    own_date = data["entry_date"]  # captured before the loop fills entry_date from the group
    for field in SHARED_FIELDS:
        if field not in member.model_fields_set or (field == "entry_date" and data[field] is None):
            if field == "posted_date" and own_date is not None:
                data[field] = own_date  # a member with its own entry_date posts on that date, not the group's
            else:
                data[field] = getattr(payload, field)
    return EntryIn(**data)


def member_payloads(payload: SplitIn) -> list[EntryIn]:
    """member_payload for every full member, in request order (keep members carry no entry payload)."""
    return [member_payload(payload, member) for member in payload.members if isinstance(member, SplitMemberIn)]
```

Insert after `_prepare_all` (after line 53):

```python


@dataclass(frozen=True)
class SplitResult:
    """What every split write answers (§1.6): member ids with their client keys, in request order."""

    group_id: int | None
    members: list[tuple[int, str | None]]

    def out(self) -> dict:
        return {
            "group_id": self.group_id,
            "member_ids": [entry_id for entry_id, _ in self.members],
            "members": [{"id": entry_id, "client_key": key} for entry_id, key in self.members],
        }


@dataclass
class _Member:
    """One payload member through the phases. action: "new" (no id), "full" (SplitMemberIn with an id) or "keep"
    (SplitKeepIn). For "full", `stored` is the split_compare signature the decision was taken on and `change`
    its verdict ("unchanged" / "meta" / "financial"); `prepared` is set for "new" and financially changed
    "full" members."""

    index: int
    item: SplitMemberIn | SplitKeepIn
    entry_id: int | None
    action: str
    payload: EntryIn | None = None
    stored: object | None = None
    change: str | None = None
    prepared: PreparedEntry | None = None


def _prepare(db: Session, members: list[_Member], http_get) -> None:
    """Phase 2: prepare_entry for new members and financially changed full ones (a full member passes its current
    rule links as `attached`, so a rule disabled or expired after import still round-trips); a metadata-only
    member only has its project checked. Never called with a lock held: FX resolution may commit the session.
    Errors are renamed members.{i}.{field}."""
    for member in members:
        try:
            if member.action == "new" or member.change == "financial":
                attached = attached_rule_ids(db, member.entry_id) if member.entry_id is not None else ()
                member.prepared = prepare_entry(db, member.payload, http_get=http_get, attached_rules=attached)
            elif member.action == "full" and member.change == "meta":
                check_project(db, member.payload.project_id)
            elif member.action == "keep" and "project_id" in member.item.model_fields_set:
                check_project(db, member.item.project_id)
        except ValidationError as exc:
            raise ValidationError(f"members.{member.index}.{exc.field}", exc.message) from exc


def _check_create_cardinality(payload: SplitIn) -> None:
    """POST /splits: 2 to MAX_SPLIT_MEMBERS members, none of them existing (§1.3)."""
    if not 2 <= len(payload.members) <= MAX_SPLIT_MEMBERS:
        raise ValidationError("members", f"a new split has 2 to {MAX_SPLIT_MEMBERS} members")
    for index, item in enumerate(payload.members):
        if isinstance(item, SplitKeepIn):
            raise ValidationError(f"members.{index}.keep", "a new split has no existing members")
        if item.id is not None:
            raise ValidationError(f"members.{index}.id", "a new split has no existing members")
```

Replace lines 130–138 (`def create_split` …) with:

```python
def create_split_result(db: Session, payload: SplitIn, *, http_get=None) -> SplitResult:
    """POST /splits: cardinality, then every member prepared (FX may commit), then the group and members written."""
    _check_create_cardinality(payload)
    members = [
        _Member(index, item, None, "new", payload=member_payload(payload, item))
        for index, item in enumerate(payload.members)
    ]
    _prepare(db, members, http_get)
    group = EntryGroup(kind="split", name=payload.name, merchant=payload.merchant, description=payload.description)
    db.add(group)
    db.flush()
    for member in members:
        member.entry_id = insert_prepared(db, member.prepared, group_id=group.id, remember=False)
    remember_all_defaults(db, [member.prepared for member in members])
    return SplitResult(group.id, [(member.entry_id, member.item.client_key) for member in members])


def create_split(db: Session, payload: SplitIn, *, http_get=None) -> int:
    return create_split_result(db, payload, http_get=http_get).group_id
```

- [ ] **Step 6: Return the envelope from the router**

Replace `app/routers/splits.py` with:

```python
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.writes import SplitIn, SplitOut
from ..services import split_service
from .errors import service_errors

router = APIRouter(prefix="/splits", tags=["Splits"])


@router.post("", response_model=SplitOut, status_code=201)
def post_split(payload: SplitIn, db: Session = Depends(get_db)):
    with service_errors():
        result = split_service.create_split_result(db, payload)
    db.commit()
    return result.out()


@router.put("/{group_id}", response_model=SplitOut)
def put_split(group_id: int, payload: SplitIn, db: Session = Depends(get_db)):
    with service_errors():
        split_service.update_split(db, group_id, payload)
    db.commit()
    ids = split_service.member_ids(db, group_id)  # replaced by the upsert's own result in Task 4
    return split_service.SplitResult(group_id, list(zip(ids, [m.client_key for m in payload.members]))).out()


@router.delete("/{group_id}", status_code=204)
def remove_split(group_id: int, db: Session = Depends(get_db)):
    with service_errors():
        split_service.delete_split(db, group_id)
    db.commit()
    return Response(status_code=204)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd /home/opc/workspace/home-hub-splitapi/services/accounting-service && /home/opc/workspace/home-hub-schedules/services/accounting-service/.venv/bin/python -m pytest tests/unit/test_split_schemas.py tests/integration/test_splits.py tests/integration/test_settlements.py -q --deselect tests/integration/test_splits.py::test_concurrent_split_updates_do_not_double_post --deselect tests/integration/test_splits.py::test_delete_split_racing_update_leaves_one_outcome`
Expected: PASS. The two deselected race tests create one-member splits through `create_split`, which POST cardinality now refuses (`ValidationError: members: a new split has 2 to 50 members`); Task 4 Step 1 replaces both with upsert-era versions (they would be rewritten there anyway, so they are not patched twice).

- [ ] **Step 8: Commit**

```bash
cd /home/opc/workspace/home-hub-splitapi
git add services/accounting-service/app/schemas/writes.py services/accounting-service/app/services/errors.py \
  services/accounting-service/app/services/split_service.py services/accounting-service/app/routers/splits.py \
  services/accounting-service/tests/unit/test_split_schemas.py services/accounting-service/tests/integration/test_splits.py
git commit -m "$(cat <<'EOF'
feat(accounting): split member ids, keep form and response envelope

SplitMemberIn gains id/client_key, SplitKeepIn is the metadata-only form
(discriminated by keep), SplitIn refuses duplicate ids and client keys,
SplitOut becomes {group_id|null, member_ids, members} for every split
write. POST /splits needs 2-50 members without ids.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---
### Task 2: Protected members — predicate and read flag

**Files:**
- Create: `services/accounting-service/app/services/split_protection.py`
- Modify: `services/accounting-service/app/schemas/ledger.py` (lines 176–179, `EntryDetailOut`)
- Modify: `services/accounting-service/app/services/ledger_service.py` (line 632 in `get_entry_detail`)
- Modify: `services/accounting-service/tests/helpers.py` (imports lines 1–16; append at end)
- Create: `services/accounting-service/tests/integration/test_split_protection.py`

**Interfaces:**
- Consumes: `entry_write_service.EDITABLE_KINDS`, `entry_write_service.has_settlements_or_refunds(db, entry_id) -> bool`, `schedule_entry_hooks.definitions_referencing(db, *, loan_entry_id=...) -> list[ScheduleDefinition]`; `Seed` builders (`seed.entry`, `seed.account`, `seed.counterparty`, `seed.definition`, `seed.line`).
- Produces:
  - `split_protection.SETTLEMENT, REFUND, TRANSFER, SYSTEM, SETTLED_ORIGINAL, SCHEDULED_LOAN: str`; `protected_reason(db: Session, entry: LedgerEntry) -> str | None`; `protected_reasons(db: Session, entries: Iterable[LedgerEntry]) -> dict[int, str | None]`.
  - `schemas.ledger.ProtectedReason` (Literal) and `class GroupMemberOut(EntryOut)` with `protected: bool`, `protected_reason: ProtectedReason | None`; `EntryDetailOut.group_members: list[GroupMemberOut]`.
  - `tests.helpers.PROTECTED_REASONS: dict[str, str]`; `tests.helpers.make_protected_member(seed, case: str, account, group_id: int | None) -> LedgerEntry`.

- [ ] **Step 1: Write the failing test and the shared builder**

In `tests/helpers.py`, add `import uuid` after line 3 (`import time`), then append at the end of the file:

```python


# Split rework §1.2: every shape of protected member, keyed by test case → expected protected_reason.
PROTECTED_REASONS = {
    "settlement": "settlement",
    "orphan_settlement": "settlement",
    "orphan_refund": "refund",
    "transfer": "transfer",
    "fee": "system",
    "discount": "system",
    "reward": "system",
    "interest": "system",
    "balance_adjustment": "system",
    "settled_original": "settled_original",
    "refunded_original": "settled_original",
    "scheduled_loan": "scheduled_loan",
}
SYSTEM_AMOUNTS = {"fee": "-15", "discount": "5", "reward": "3", "interest": "-20", "balance_adjustment": "-8"}


def make_protected_member(seed, case: str, account: Account, group_id: int | None) -> LedgerEntry:
    """One protected row of the named shape on `account`, in group `group_id` (None: ungrouped, a convert anchor).
    Synthetic rows only; the rows it links to (originals, counterparts, settlements, refunds, the definition) are
    outside the group."""
    party = seed.counterparty(f"對象-{case}")
    if case == "settlement":
        lent = seed.entry(account, "-300", kind="receivable", counterparty_id=party.id)
        return seed.entry(account, "200", kind="receivable", counterparty_id=party.id, is_settlement=True,
                          settles_entry_id=lent.id, group_id=group_id)
    if case == "orphan_settlement":  # imported collection whose original is gone: no settles_entry_id
        return seed.entry(account, "200", kind="receivable", counterparty_id=party.id, is_settlement=True,
                          group_id=group_id)
    if case == "orphan_refund":  # the refunded source was deleted: refunds_entry_id is NULL
        return seed.entry(account, "50", kind="refund", group_id=group_id)
    if case == "transfer":
        other = seed.account(f"轉入-{case}")
        pair = uuid.uuid4()
        leg = seed.entry(account, "-100", kind="transfer_out", transfer_group_id=pair, group_id=group_id)
        seed.entry(other, "100", kind="transfer_in", transfer_group_id=pair)
        return leg
    if case in SYSTEM_AMOUNTS:
        return seed.entry(account, SYSTEM_AMOUNTS[case], kind=case, group_id=group_id)
    if case == "settled_original":
        original = seed.entry(account, "-300", kind="receivable", counterparty_id=party.id, group_id=group_id)
        seed.entry(account, "100", kind="receivable", counterparty_id=party.id, is_settlement=True,
                   settles_entry_id=original.id)
        return original
    if case == "refunded_original":
        original = seed.entry(account, "-80", group_id=group_id)
        seed.entry(account, "30", kind="refund", refunds_entry_id=original.id)
        return original
    if case == "scheduled_loan":
        loan = seed.entry(account, "1000", kind="payable", counterparty_id=party.id, group_id=group_id)
        seed.definition([seed.line("repayment", account, "100", loan_entry_id=loan.id)], kind="installment",
                        name=f"分期-{case}", times=10)
        return loan
    raise ValueError(f"unknown protected case {case!r}")
```

Create `tests/integration/test_split_protection.py`:

```python
"""Protected split members (split rework §1.2): the read flag on GET /entries/{id}.group_members."""

import pytest

from tests.helpers import PROTECTED_REASONS, make_protected_member


@pytest.mark.parametrize("case", sorted(PROTECTED_REASONS))
def test_group_members_carry_the_protected_flag(client, db_session, seed, case):
    # Review Focus 3: real kinds and links decide, never a label.
    wallet = seed.account()
    group = seed.group(name="聚餐")
    plain = seed.entry(wallet, "-100", group_id=group.id)
    member = make_protected_member(seed, case, wallet, group.id)
    db_session.commit()
    plain_id, member_id = plain.id, member.id

    rows = {row["id"]: row for row in client.get(f"/entries/{plain_id}").json()["group_members"]}

    assert (rows[plain_id]["protected"], rows[plain_id]["protected_reason"]) == (False, None)
    assert (rows[member_id]["protected"], rows[member_id]["protected_reason"]) == (True, PROTECTED_REASONS[case])


def test_an_ungrouped_entry_has_no_group_members(client, db_session, seed):
    wallet = seed.account()
    entry = seed.entry(wallet, "-100")
    db_session.commit()

    assert client.get(f"/entries/{entry.id}").json()["group_members"] == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/opc/workspace/home-hub-splitapi/services/accounting-service && /home/opc/workspace/home-hub-schedules/services/accounting-service/.venv/bin/python -m pytest tests/integration/test_split_protection.py -q`
Expected: FAIL — `KeyError: 'protected'` in every parametrized case.

- [ ] **Step 3: Implement the predicate**

Create `app/services/split_protection.py`:

```python
"""Protected split members (split rework §1.2).

One predicate serves the read flag (EntryDetailOut.group_members[].protected) and the locked write guard
(split_service), so the SPA's lock glyph and the 409 member_locked never disagree. A protected member may only
change metadata (SplitKeepIn) and is never dropped or recreated by a split write."""

from typing import Iterable

from sqlalchemy.orm import Session

from ..models import LedgerEntry
from .entry_write_service import EDITABLE_KINDS, has_settlements_or_refunds
from .schedule_entry_hooks import definitions_referencing

SETTLEMENT = "settlement"  # is_settlement, linked or orphan (imported collections may have no settles_entry_id)
REFUND = "refund"  # kind refund, whether or not refunds_entry_id is still set
TRANSFER = "transfer"  # a transfer leg (transfer_group_id set or a transfer kind)
SYSTEM = "system"  # any kind outside EditableKind: fee, discount, reward, interest, balance_adjustment
SETTLED_ORIGINAL = "settled_original"  # an original other entries settle or refund
SCHEDULED_LOAN = "scheduled_loan"  # a loan a live schedule definition references (D29)
TRANSFER_KINDS = ("transfer_out", "transfer_in")
LOAN_KINDS = ("receivable", "payable")


def protected_reason(db: Session, entry: LedgerEntry) -> str | None:
    """The first matching reason in the order above, or None for an editable member."""
    if entry.is_settlement:
        return SETTLEMENT
    if entry.kind == "refund":
        return REFUND
    if entry.transfer_group_id is not None or entry.kind in TRANSFER_KINDS:
        return TRANSFER
    if entry.kind not in EDITABLE_KINDS:
        return SYSTEM
    if has_settlements_or_refunds(db, entry.id):
        return SETTLED_ORIGINAL
    if entry.kind in LOAN_KINDS and definitions_referencing(db, loan_entry_id=entry.id):
        return SCHEDULED_LOAN
    return None


def protected_reasons(db: Session, entries: Iterable[LedgerEntry]) -> dict[int, str | None]:
    """protected_reason per entry id (a split has at most a few dozen members; one query each is fine)."""
    return {entry.id: protected_reason(db, entry) for entry in entries}
```

- [ ] **Step 4: Expose the flag on read**

In `app/schemas/ledger.py`, replace lines 176–179:

```python
class EntryDetailOut(EntryOut):
    invoice_random: str | None
    children: list[EntryOut]
    group_members: list[EntryOut]
```

with:

```python
ProtectedReason = Literal["settlement", "refund", "transfer", "system", "settled_original", "scheduled_loan"]


class GroupMemberOut(EntryOut):
    """A member of the entry's group with the split rework's protection flag (§1.2): a protected member only takes
    metadata (name, project, tags, description) in a split write."""

    protected: bool
    protected_reason: ProtectedReason | None


class EntryDetailOut(EntryOut):
    invoice_random: str | None
    children: list[EntryOut]
    group_members: list[GroupMemberOut]
```

In `app/services/ledger_service.py`, replace line 632:

```python
    detail["group_members"] = rows(LedgerEntry.group_id == entry.group_id) if entry.group_id else []
```

with:

```python
    detail["group_members"] = rows(LedgerEntry.group_id == entry.group_id) if entry.group_id else []
    if detail["group_members"]:
        from .split_protection import protected_reasons  # lazy: split_protection → entry_write_service → this module

        member_rows = db.scalars(
            select(LedgerEntry).where(LedgerEntry.id.in_([member["id"] for member in detail["group_members"]]))
        )
        reasons = protected_reasons(db, member_rows)
        for member in detail["group_members"]:
            member["protected_reason"] = reasons[member["id"]]
            member["protected"] = reasons[member["id"]] is not None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /home/opc/workspace/home-hub-splitapi/services/accounting-service && /home/opc/workspace/home-hub-schedules/services/accounting-service/.venv/bin/python -m pytest tests/integration/test_split_protection.py tests/integration/test_entries_api.py tests/integration/test_settlements.py -q`
Expected: PASS (13 protection cases; the entry-detail and settlement suites still serialize).

- [ ] **Step 6: Commit**

```bash
cd /home/opc/workspace/home-hub-splitapi
git add services/accounting-service/app/services/split_protection.py services/accounting-service/app/schemas/ledger.py \
  services/accounting-service/app/services/ledger_service.py services/accounting-service/tests/helpers.py \
  services/accounting-service/tests/integration/test_split_protection.py
git commit -m "$(cat <<'EOF'
feat(accounting): protected split members on entry detail

split_protection.protected_reason is the single predicate for
settlements, refunds, transfer legs, system kinds, settled/refunded
originals and loans a live schedule references; group_members carries
protected/protected_reason.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Canonical comparison (unchanged / metadata-only / financial)

**Files:**
- Create: `services/accounting-service/app/services/split_compare.py`
- Create: `services/accounting-service/tests/integration/test_split_compare.py`

**Interfaces:**
- Consumes: `entry_write_service.AMOUNT_QUANTUM`, `RATE_QUANTUM`, `CHILD_DEFAULT_NAMES`, `attached_rule_ids(db, entry_id) -> set[int]`; `EntryIn`.
- Produces: `split_compare.Change = Literal["unchanged", "meta", "financial"]`; `ONLINE_FX_SOURCE = "fx_api"`; `@dataclass(frozen=True) class MemberSignature(financial: tuple, meta: tuple)`; `stored_signature(db: Session, entry: LedgerEntry) -> MemberSignature`; `payload_signature(payload: EntryIn, account_currency: str) -> MemberSignature`; `classify(stored: MemberSignature, sent: MemberSignature) -> Change`.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_split_compare.py`:

```python
"""split_compare: the canonical comparison that sorts a full member into unchanged / meta / financial (§1.4)."""

from datetime import date
from decimal import Decimal

import pytest

from app.models import LedgerEntry
from app.schemas.writes import EntryIn
from app.services import entry_write_service as ews
from app.services.split_compare import classify, payload_signature, stored_signature

DAY = date(2026, 9, 1)


def _body(account, **fields) -> dict:
    body = {
        "account_id": account.id, "kind": "expense", "amount": "100", "entry_date": DAY.isoformat(),
        "entry_time": "12:30:15", "name": "午餐", "tags": ["公司"], "fee": {"amount": "5"},
    }
    body.update(fields)
    return body


def _verdict(db, entry_id: int, payload: EntryIn) -> str:
    db.expire_all()
    entry = db.get(LedgerEntry, entry_id)
    return classify(stored_signature(db, entry), payload_signature(payload, entry.currency))


@pytest.fixture()
def stored(db_session, seed):
    card = seed.account("卡")
    rule = seed.rule(card)
    entry_id = ews.create_entry(db_session, EntryIn(**_body(card, reward_rule_ids=[rule.id])))
    db_session.commit()
    return card, rule, entry_id


def test_the_same_payload_is_unchanged(db_session, stored):
    card, rule, entry_id = stored

    assert _verdict(db_session, entry_id, EntryIn(**_body(card, reward_rule_ids=[rule.id]))) == "unchanged"
    # an omitted posted_date means entry_date, exactly as prepare_entry stores it
    same_posting = EntryIn(**_body(card, reward_rule_ids=[rule.id], posted_date=DAY.isoformat()))
    assert _verdict(db_session, entry_id, same_posting) == "unchanged"


@pytest.mark.parametrize(
    "change", [{"name": "晚餐"}, {"merchant": "全家"}, {"tags": []}, {"description": "備註"}],
    ids=["name", "merchant", "tags", "description"],
)
def test_metadata_fields_are_a_metadata_change(db_session, stored, change):
    card, rule, entry_id = stored

    assert _verdict(db_session, entry_id, EntryIn(**_body(card, reward_rule_ids=[rule.id], **change))) == "meta"


def test_a_project_is_metadata(db_session, seed, stored):
    card, rule, entry_id = stored
    trip = seed.project("日本行")
    db_session.commit()

    assert _verdict(db_session, entry_id, EntryIn(**_body(card, reward_rule_ids=[rule.id], project_id=trip.id))) == "meta"


@pytest.mark.parametrize(
    "change",
    [
        {"amount": "101"}, {"kind": "income"}, {"entry_date": "2026-09-02"}, {"entry_time": "12:30"},
        {"posted_date": "2026-09-05"}, {"fee": None}, {"fee": {"amount": "5", "name": "運費"}},
        {"discount": {"amount": "1"}}, {"reward_rule_ids": []}, {"invoice_number": "AB12345678"},
    ],
    ids=["amount", "kind", "date", "time-precision", "posted", "fee-removed", "fee-renamed", "discount", "rules",
         "invoice"],
)
def test_financial_fields_are_a_financial_change(db_session, stored, change):
    card, rule, entry_id = stored
    body = _body(card, reward_rule_ids=[rule.id])
    body.update(change)

    assert _verdict(db_session, entry_id, EntryIn(**body)) == "financial"


def test_account_and_category_are_financial(db_session, seed, stored):
    card, rule, entry_id = stored
    other = seed.account("別張卡")
    food = seed.category("餐飲")
    db_session.commit()

    assert _verdict(db_session, entry_id, EntryIn(**_body(other, reward_rule_ids=[rule.id]))) == "financial"
    assert _verdict(db_session, entry_id, EntryIn(**_body(card, reward_rule_ids=[rule.id], category_id=food.id))) == "financial"


def test_online_fx_echo_is_unchanged_only_against_an_online_row(db_session, seed):
    # Review Focus 5: amount=None / fx_rate=None (online FX) equals a stored fx_api row with the same original.
    card = seed.account("華航卡")
    fx = {"original_amount": Decimal("-1800"), "original_currency": "JPY", "fx_rate": Decimal("0.2163")}
    online = seed.entry(card, "-389.34", fx_source="fx_api", **fx)
    manual = seed.entry(card, "-389.34", fx_source="manual", **fx)
    db_session.commit()
    echo = {"account_id": card.id, "kind": "expense", "amount": None, "original_amount": "1800",
            "original_currency": "JPY", "entry_date": DAY.isoformat()}

    assert _verdict(db_session, online.id, EntryIn(**echo)) == "unchanged"
    assert _verdict(db_session, manual.id, EntryIn(**echo)) == "financial"
    assert _verdict(db_session, online.id, EntryIn(**{**echo, "original_amount": "1900"})) == "financial"
    assert _verdict(db_session, manual.id, EntryIn(**{**echo, "fx_rate": "0.2163"})) == "unchanged"
    assert _verdict(db_session, manual.id, EntryIn(**{**echo, "amount": "389.34"})) == "unchanged"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/opc/workspace/home-hub-splitapi/services/accounting-service && /home/opc/workspace/home-hub-schedules/services/accounting-service/.venv/bin/python -m pytest tests/integration/test_split_compare.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.split_compare'`.

- [ ] **Step 3: Implement the comparison**

Create `app/services/split_compare.py`:

```python
"""Canonical comparison of a full split member against its stored row (split rework §1.4 step 1).

Inputs are compared unsigned (the payload's amounts) against `abs()` of the stored signed values. An online-FX
payload (amount and fx_rate both null) equals a stored row with the same original amount and currency whose
fx_source is the online source (`fx_api`; the spec's "online"). The verdict decides the write: "unchanged" →
skipped; "meta" → name / merchant / project / tags / description applied with no FX, no child or rule rebuild;
"financial" → prepared and rewritten like a single-entry update."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import LedgerEntry
from ..schemas.writes import EntryIn
from .entry_write_service import AMOUNT_QUANTUM, CHILD_DEFAULT_NAMES, RATE_QUANTUM, attached_rule_ids

Change = Literal["unchanged", "meta", "financial"]
ONLINE_FX_SOURCE = "fx_api"


@dataclass(frozen=True)
class MemberSignature:
    financial: tuple  # kind, account, category, counterparty, FX inputs, fee/discount, rule ids, invoice, dates
    meta: tuple  # name, merchant, project, tags, description


def _abs(value) -> Decimal | None:
    return None if value is None else abs(Decimal(value))


def _children_key(rows) -> tuple:
    return tuple(sorted((kind, _abs(amount), name) for kind, amount, name in rows))


def _stored_fx(entry: LedgerEntry) -> tuple:
    if entry.original_currency is None:
        return ("plain", _abs(entry.amount))
    if entry.fx_source == ONLINE_FX_SOURCE:
        return ("online", _abs(entry.original_amount), entry.original_currency)
    rate = None if entry.fx_rate is None else Decimal(entry.fx_rate)
    return ("manual", _abs(entry.amount), _abs(entry.original_amount), entry.original_currency, rate)


def _payload_fx(payload: EntryIn, account_currency: str) -> tuple:
    """The FX inputs as resolve_fx would store them, without any rate lookup."""
    if payload.original_currency is None or payload.original_currency == account_currency:
        return ("plain", payload.amount)
    if payload.original_amount is None:
        return ("incomplete",)  # prepare_entry answers the 422
    if payload.fx_rate is not None:
        rate = Decimal(payload.fx_rate).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
        amount = (payload.original_amount * rate).quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)
        return ("manual", amount, payload.original_amount, payload.original_currency, rate)
    if payload.amount is not None:
        rate = (payload.amount / payload.original_amount).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
        return ("manual", payload.amount, payload.original_amount, payload.original_currency, rate)
    return ("online", payload.original_amount, payload.original_currency)


def stored_signature(db: Session, entry: LedgerEntry) -> MemberSignature:
    children = db.execute(
        select(LedgerEntry.kind, LedgerEntry.amount, LedgerEntry.name).where(LedgerEntry.parent_entry_id == entry.id)
    ).all()
    financial = (
        entry.kind, entry.account_id, entry.category_id, entry.counterparty_id, _stored_fx(entry),
        _children_key(children), frozenset(attached_rule_ids(db, entry.id)), entry.invoice_number,
        entry.invoice_random, entry.entry_date, entry.entry_time, entry.posted_date,
    )
    meta = (entry.name, entry.merchant, entry.project_id, tuple(entry.tags or ()), entry.description)
    return MemberSignature(financial, meta)


def payload_signature(payload: EntryIn, account_currency: str) -> MemberSignature:
    """`account_currency`: the stored row's currency (when the account differs the verdict is financial anyway)."""
    children = [
        (kind, child.amount, child.name or CHILD_DEFAULT_NAMES[kind])
        for kind, child in (("fee", payload.fee), ("discount", payload.discount))
        if child is not None
    ]
    financial = (
        payload.kind, payload.account_id, payload.category_id, payload.counterparty_id,
        _payload_fx(payload, account_currency), _children_key(children), frozenset(payload.reward_rule_ids),
        payload.invoice_number, payload.invoice_random, payload.entry_date, payload.entry_time,
        payload.posted_date or payload.entry_date,
    )
    meta = (payload.name, payload.merchant, payload.project_id, tuple(payload.tags), payload.description)
    return MemberSignature(financial, meta)


def classify(stored: MemberSignature, sent: MemberSignature) -> Change:
    if stored.financial != sent.financial:
        return "financial"
    if stored.meta != sent.meta:
        return "meta"
    return "unchanged"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/opc/workspace/home-hub-splitapi/services/accounting-service && /home/opc/workspace/home-hub-schedules/services/accounting-service/.venv/bin/python -m pytest tests/integration/test_split_compare.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/opc/workspace/home-hub-splitapi
git add services/accounting-service/app/services/split_compare.py services/accounting-service/tests/integration/test_split_compare.py
git commit -m "$(cat <<'EOF'
feat(accounting): canonical split member comparison

stored_signature/payload_signature compare unsigned inputs with the
stored row (online FX equals an fx_api row with the same original), so
a split PUT can skip untouched members and apply metadata-only changes
without an FX request.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---
### Task 4: Upsert `PUT /splits/{group_id}` by member id

**Files:**
- Modify: `services/accounting-service/app/services/entry_write_service.py` (insert after `insert_prepared`, line 319; `update_entry` lines 463–468)
- Modify: `services/accounting-service/app/services/split_service.py` (whole file replaced)
- Modify: `services/accounting-service/app/routers/splits.py` (`put_split`)
- Modify: `services/accounting-service/tests/integration/test_splits.py` (imports lines 1–18; helpers after line 48; tests at lines 140–161, 179–208, 211–270, 291–342, 364–407; new tests appended)
- Modify: `services/accounting-service/tests/integration/test_edit_lock.py` (line 58)

**Interfaces:**
- Consumes: Task 1 (`SplitResult`, `_Member`, `_prepare`, `member_payload`, `create_split_result`, codes, `CodedConflictError`), Task 2 (`protected_reasons`), Task 3 (`MemberSignature`, `stored_signature`, `payload_signature`, `classify`); `entry_write_service._apply`, `write_children`, `write_rule_links`, `lock_group`, `delete_entries_cascade`; `schedule_entry_hooks.assert_group_not_scheduled`.
- Produces:
  - `entry_write_service.apply_prepared_update(db: Session, entry: LedgerEntry, prepared: PreparedEntry) -> None`.
  - `split_service.update_split(db: Session, group_id: int, payload: SplitIn, *, http_get=None) -> SplitResult` plus the module helpers `_assert_not_scheduled(db, group_id) -> None`, `_readable_split(db, group_id) -> EntryGroup`, `_current_members(db, group_id) -> list[LedgerEntry]`, `_check_put_cardinality(payload, current_count) -> None`, `_plan_members(payload, current) -> list[_Member]`, `_refuse_locked(members, drop_ids, protected) -> None`, `_classify_full(db, members, by_id) -> None`, `_revalidate(db, group, group_id, locked, before_ids, protected, members) -> None`, `_apply_meta(entry, payload) -> None`, `_apply_keep(entry, item) -> None`, `KEEP_FIELDS`.

Error precedence implemented here (resolves §1.4 step 1 vs §1.6 "Error precedence", see the self-review): Pydantic 422 → 404 group missing → 409 `group_scheduled` → 404 not a split → 409 `locked_until_cutover` → 422 cardinality (`members` over max(50, current); `members.0.id` for a one-member PUT without id) → 404 `member_not_found` → 409 `member_locked` → 422 `members.{i}.{field}` (prepare) → 409 `retry` (inside the locks).

- [ ] **Step 1: Write the failing tests and adapt the existing ones**

In `tests/integration/test_splits.py` replace the import block (lines 1–18) with:

```python
"""Splits: one entry_group of kind split; upsert by member id, protected members, defaults from the group."""

import uuid
from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.models import EntryGroup, EntryRewardRule, LedgerEntry
from app.schemas.writes import SettleIn, SplitIn
from app.services import entry_write_service as ews
from app.services import ledger_service
from app.services import settlement_service as st
from app.services import split_service as ss
from app.services.edit_lock import EditLockedError
from app.services.errors import RETRY, CodedConflictError, NotFoundError, ValidationError
from tests.helpers import PROTECTED_REASONS, make_protected_member, race
```

Insert after `_balance` (after line 48):

```python


def _keep(entry_id: int, **fields) -> dict:
    return {"id": entry_id, "keep": True, **fields}


def _echo(entry: LedgerEntry, **fields) -> dict:
    """A full member re-sending the stored row the way the rework's client does: every per-member field explicit.
    Fee / discount / rule ids / FX inputs are passed in `fields` when the row has them."""
    body = {
        "id": entry.id, "account_id": entry.account_id, "kind": entry.kind, "amount": str(abs(entry.amount)),
        "entry_date": entry.entry_date.isoformat(),
        "entry_time": entry.entry_time.isoformat() if entry.entry_time else None,
        "posted_date": entry.posted_date.isoformat(), "category_id": entry.category_id,
        "project_id": entry.project_id, "name": entry.name, "merchant": entry.merchant,
        "counterparty_id": entry.counterparty_id, "description": entry.description, "tags": list(entry.tags),
    }
    body.update(fields)
    return body


def _ledger(db) -> dict[int, tuple]:
    """Every row's money-relevant columns (name and tags left out on purpose)."""
    db.expire_all()
    return {
        e.id: (e.amount, e.kind, e.account_id, e.is_settlement, e.settles_entry_id, e.refunds_entry_id,
               e.transfer_group_id, e.reward_source_entry_id, e.entry_date, e.entry_time, e.posted_date, e.group_id)
        for e in db.scalars(select(LedgerEntry))
    }


def _spy_prepare(monkeypatch) -> list:
    """Record the amount of every payload split_service prepares."""
    calls = []
    original = ss.prepare_entry

    def spy(db, payload, **kwargs):
        calls.append(payload.amount)
        return original(db, payload, **kwargs)

    monkeypatch.setattr(ss, "prepare_entry", spy)
    return calls
```

Replace `test_update_replaces_members_and_group_fields` (lines 140–161) with:

```python
def test_update_with_only_new_members_replaces_the_old_ones(client, db_session, seed):
    wallet, card = seed.account("錢包"), seed.account("卡")
    group_id = ss.create_split(
        db_session, SplitIn(**_split(_member(wallet, fee={"amount": "5"}), _member(wallet), name="舊"))
    )
    db_session.commit()
    old_ids = [m.id for m in _members(db_session, group_id)]
    wallet_id, card_id = wallet.id, card.id

    response = client.put(
        f"/splits/{group_id}",
        json=_split(_member(card, amount="70"), _member(card, amount="30"), name="新", merchant="全家",
                    entry_date="2026-09-05"),
    )

    assert response.status_code == 200, response.text
    members = _members(db_session, group_id)
    assert response.json() == {
        "group_id": group_id, "member_ids": [m.id for m in members],
        "members": [{"id": m.id, "client_key": None} for m in members],
    }
    assert not set(old_ids) & {m.id for m in members}
    assert [(m.account_id, m.amount, m.entry_date) for m in members] == [
        (card_id, Decimal("-70"), date(2026, 9, 5)), (card_id, Decimal("-30"), date(2026, 9, 5)),
    ]
    group = db_session.get(EntryGroup, group_id)
    assert (group.name, group.merchant) == ("新", "全家")
    assert db_session.scalars(select(LedgerEntry).where(LedgerEntry.account_id == wallet_id)).all() == []
```

In `test_split_update_refuses_locked_group`, replace lines 205–208:

```python
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    assert client.put(f"/splits/{hidden_id}", json=body).status_code == 200
    [member] = _members(db_session, hidden_id)
    assert (member.amount, member.source, member.moze_id) == (Decimal("-10"), "manual", None)
```

with:

```python
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    two = _split(_member(wallet, amount="10"), _member(wallet, amount="5"))
    assert client.put(f"/splits/{hidden_id}", json=two).status_code == 200
    assert [(m.amount, m.source, m.moze_id) for m in _members(db_session, hidden_id)] == [
        (Decimal("-10"), "manual", None), (Decimal("-5"), "manual", None),
    ]
```

In `test_update_split_refuses_group_with_settlement_member` (lines 224–227), `test_update_split_refuses_group_with_settled_member` (lines 245–248) and `test_update_split_refuses_group_with_transfer_leg` (lines 265–268) — the same edit in all three; the account variable is `wallet` in the first two and `a` in the third. Replace:

```python
    response = client.put(f"/splits/{group_id}", json=_split(_member(wallet, amount="10")))

    assert response.status_code == 422
    assert [error["loc"] for error in response.json()["detail"]] == [["members"]]
```

with (third test: `a` instead of `wallet`):

```python
    response = client.put(f"/splits/{group_id}", json=_split(_member(wallet, amount="10"), _member(wallet, amount="5")))

    assert response.status_code == 409  # the protected member would be dropped
    assert response.json()["message"].startswith("member_locked")
```

Replace `test_concurrent_split_updates_do_not_double_post` and `test_delete_split_racing_update_leaves_one_outcome` (lines 291–341) with:

```python
def test_concurrent_split_updates_serialise_and_the_second_retries(pg_engine, db_session, seed):
    # Plan review round 4 P1 (never both amounts) under upsert: both PUTs change one member; the group row lock
    # makes the second wait, and its re-read inside the lock finds the member changed under it → 409 retry.
    wallet_id, group_id, [first_id, second_id] = _two_member_split(db_session, seed)

    def put(amount):
        body = _split({"id": first_id, "account_id": wallet_id, "kind": "expense", "amount": amount}, _keep(second_id))
        return lambda db: ss.update_split(db, group_id, SplitIn(**body))

    result = race(pg_engine, put("200"), put("300"))

    assert isinstance(result, CodedConflictError) and result.code == RETRY
    assert [m.amount for m in _members(db_session, group_id)] == [Decimal("-200"), Decimal("-50")]
    assert _balance(db_session, wallet_id) == Decimal("750")


def test_delete_split_racing_update_leaves_one_outcome(pg_engine, db_session, seed):
    # The PUT holds the group lock; DELETE /splits waits on it, then sees and removes the PUT's members (same ids).
    wallet_id, group_id, [first_id, second_id] = _two_member_split(db_session, seed)
    body = _split({"id": first_id, "account_id": wallet_id, "kind": "expense", "amount": "300"}, _keep(second_id))

    result = race(
        pg_engine,
        lambda db: ss.update_split(db, group_id, SplitIn(**body)),
        lambda db: ss.delete_split(db, group_id),
    )

    assert result == "committed"
    db_session.expire_all()
    assert db_session.get(EntryGroup, group_id) is None
    assert db_session.scalars(select(LedgerEntry).where(LedgerEntry.account_id == wallet_id)).all() == []
    assert _balance(db_session, wallet_id) == Decimal("1000")
```

Replace `test_single_delete_racing_split_update_does_not_deadlock` and `test_split_update_racing_single_delete_does_not_deadlock` (lines 364–407) with:

```python
def _put_dropping_first(wallet_id, second_id) -> dict:
    return _split(
        {"id": second_id, "account_id": wallet_id, "kind": "expense", "amount": "300"},
        {"account_id": wallet_id, "kind": "expense", "amount": "20"},
    )


def test_single_delete_racing_split_update_does_not_deadlock(pg_engine, db_session, seed):
    # Plan review round 5 P2: delete_entry takes lock_group first, so it waits on the PUT's group lock; the PUT
    # dropped the member, so the delete then finds it gone (404), never a deadlock.
    wallet_id, group_id, [first_id, second_id] = _two_member_split(db_session, seed)
    body = _put_dropping_first(wallet_id, second_id)

    result = race(
        pg_engine,
        lambda db: ss.update_split(db, group_id, SplitIn(**body)),
        lambda db: ews.delete_entry(db, first_id),
    )

    assert not isinstance(result, OperationalError)  # no deadlock / lock error
    assert isinstance(result, NotFoundError)
    remaining = _assert_consistent(db_session, wallet_id, group_id)
    assert sorted(m.amount for m in remaining) == [Decimal("-300"), Decimal("-20")]
    assert second_id in [m.id for m in remaining]  # kept in place, not recreated


def test_split_update_racing_single_delete_does_not_deadlock(pg_engine, db_session, seed):
    # The reverse order: the single DELETE holds the group lock (proved with NOWAIT from a third connection), the
    # PUT waits on it; the membership it classified is gone under the lock → 409 retry, nothing half-written.
    wallet_id, group_id, [first_id, second_id] = _two_member_split(db_session, seed)
    body = _put_dropping_first(wallet_id, second_id)

    def delete_and_prove_group_lock(db):
        ews.delete_entry(db, first_id)
        with pg_engine.connect() as probe:
            with pytest.raises(OperationalError):  # LockNotAvailable: the group row is held by this delete
                probe.execute(select(EntryGroup.id).where(EntryGroup.id == group_id).with_for_update(nowait=True))
            probe.rollback()

    result = race(pg_engine, delete_and_prove_group_lock, lambda db: ss.update_split(db, group_id, SplitIn(**body)))

    assert not isinstance(result, OperationalError)  # no deadlock / lock error
    assert isinstance(result, CodedConflictError) and result.code == RETRY
    db_session.expire_all()
    [survivor] = db_session.scalars(select(LedgerEntry).where(LedgerEntry.account_id == wallet_id)).all()
    assert (survivor.id, survivor.amount) == (second_id, Decimal("-50"))
    assert _balance(db_session, wallet_id) == Decimal("950")
```

Append the new upsert tests at the end of `tests/integration/test_splits.py`:

```python
def test_upsert_keeps_ids_and_applies_keep_full_keep_meta_new_and_drop(client, db_session, seed):
    wallet = seed.account("錢包", opening="1000")
    db_session.commit()
    group_id = ss.create_split(db_session, SplitIn(**_split(
        _member(wallet, name="午餐"), _member(wallet, amount="50", name="飲料"), _member(wallet, amount="30", name="甜點"),
    )))
    db_session.commit()
    lunch, drink, dessert = _members(db_session, group_id)
    lunch_id, drink_id, dessert_id, wallet_id = lunch.id, drink.id, dessert.id, wallet.id

    response = client.put(f"/splits/{group_id}", json=_split(
        _member(wallet, amount="40", name="點心", client_key="new"),
        _echo(lunch, amount="120", client_key="lunch"),
        _keep(drink_id, name="珍奶", tags=["手搖"]),
        name="聚餐",
    ))

    assert response.status_code == 200, response.text
    body = response.json()
    new_id = body["member_ids"][0]
    assert body == {"group_id": group_id, "member_ids": [new_id, lunch_id, drink_id], "members": [
        {"id": new_id, "client_key": "new"}, {"id": lunch_id, "client_key": "lunch"},
        {"id": drink_id, "client_key": None},
    ]}
    rows = {m.id: m for m in _members(db_session, group_id)}
    assert set(rows) == {lunch_id, drink_id, new_id}  # dessert dropped, nobody recreated
    assert (rows[lunch_id].amount, rows[lunch_id].name) == (Decimal("-120"), "午餐")
    assert (rows[drink_id].amount, rows[drink_id].name, rows[drink_id].tags) == (Decimal("-50"), "珍奶", ["手搖"])
    assert (rows[new_id].amount, rows[new_id].name) == (Decimal("-40"), "點心")
    assert db_session.get(LedgerEntry, dessert_id) is None
    assert db_session.get(EntryGroup, group_id).name == "聚餐"
    assert _balance(db_session, wallet_id) == Decimal("1000") - 120 - 50 - 40


def test_unknown_or_foreign_member_id_is_404_and_writes_nothing(client, db_session, seed):
    wallet_id, group_id, [first_id, second_id] = _two_member_split(db_session, seed)
    other = ss.create_split(db_session, SplitIn(**_split(
        {"account_id": wallet_id, "kind": "expense", "amount": "1"}, {"account_id": wallet_id, "kind": "expense", "amount": "2"},
    )))
    db_session.commit()
    foreign_id = ss.member_ids(db_session, other)[0]

    for unknown in (foreign_id, 999999):
        response = client.put(f"/splits/{group_id}", json=_split(_keep(first_id, name="改名"), _keep(unknown)))
        assert response.status_code == 404
        assert response.json()["message"].startswith("member_not_found")
    assert [m.name for m in _members(db_session, group_id)] == [None, None]


def test_one_member_put_needs_an_existing_member(client, db_session, seed):
    wallet_id, group_id, _ = _two_member_split(db_session, seed)

    response = client.put(f"/splits/{group_id}", json=_split({"account_id": wallet_id, "kind": "expense", "amount": "5"}))

    assert (response.status_code, [e["loc"] for e in response.json()["detail"]]) == (422, [["members.0.id"]])
    assert len(_members(db_session, group_id)) == 2


@pytest.mark.parametrize("case", sorted(PROTECTED_REASONS))
def test_protected_member_takes_metadata_only(client, db_session, seed, case):
    # Review Focus 3: keep + another member dropped → 200; the same member as full, or dropped → 409, nothing written.
    wallet = seed.account("錢包", opening="1000")
    group = seed.group(name="聚餐")
    plain = seed.entry(wallet, "-100", group_id=group.id)
    extra = seed.entry(wallet, "-40", group_id=group.id)
    member = make_protected_member(seed, case, wallet, group.id)
    db_session.commit()
    group_id, plain_id, extra_id, member_id, wallet_id = group.id, plain.id, extra.id, member.id, wallet.id
    plain_echo = _echo(plain, amount="120")
    as_full = {"id": member_id, "account_id": wallet_id, "kind": "expense", "amount": "1"}
    before = _ledger(db_session)

    full = client.put(f"/splits/{group_id}", json=_split(plain_echo, _keep(extra_id), as_full))
    dropped = client.put(f"/splits/{group_id}", json=_split(plain_echo, _keep(extra_id)))

    assert [(r.status_code, r.json()["message"].split(":")[0]) for r in (full, dropped)] == [(409, "member_locked")] * 2
    assert PROTECTED_REASONS[case] in full.json()["message"]
    assert _ledger(db_session) == before

    kept = client.put(f"/splits/{group_id}", json=_split(plain_echo, _keep(member_id, name="改名", tags=["保護"])))

    assert kept.status_code == 200, kept.text
    after = _ledger(db_session)
    assert extra_id not in after and after[plain_id][0] == Decimal("-120")
    untouched = {key: value for key, value in before.items() if key not in (plain_id, extra_id)}
    assert {key: value for key, value in after.items() if key != plain_id} == untouched
    stored = db_session.get(LedgerEntry, member_id)
    assert (stored.name, stored.tags) == ("改名", ["保護"])


def test_imported_transfer_leg_keeps_its_counterpart_after_cutover(client, db_session, seed, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    a, b = seed.account("A"), seed.account("B")
    group = seed.group(moze_id="pkg-9")
    pair = uuid.uuid4()
    imported = {"source": "moze_backup"}
    out_leg = seed.entry(a, "-100", kind="transfer_out", transfer_group_id=pair, group_id=group.id, moze_id="rec-91", **imported)
    in_leg = seed.entry(b, "100", kind="transfer_in", transfer_group_id=pair, moze_id="rec-92", **imported)
    meal = seed.entry(a, "-30", group_id=group.id, moze_id="rec-93", **imported)
    db_session.commit()
    group_id, out_id, in_id, meal_id = group.id, out_leg.id, in_leg.id, meal.id

    response = client.put(f"/splits/{group_id}", json=_split(_keep(out_id, name="轉給B"), _echo(meal, amount="35")))

    assert response.status_code == 200, response.text
    db_session.expire_all()
    out_row, in_row, meal_row = (db_session.get(LedgerEntry, i) for i in (out_id, in_id, meal_id))
    assert (out_row.amount, out_row.transfer_group_id, out_row.name, out_row.source) == (Decimal("-100"), pair, "轉給B", "moze_backup")
    assert (in_row.amount, in_row.transfer_group_id) == (Decimal("100"), pair)
    assert (meal_row.amount, meal_row.source) == (Decimal("-35"), "manual")


def test_referenced_loan_cannot_be_dropped_or_change_kind(client, db_session, seed):
    wallet = seed.account()
    group = seed.group()
    plain = seed.entry(wallet, "-100", group_id=group.id)
    loan = make_protected_member(seed, "scheduled_loan", wallet, group.id)
    db_session.commit()
    group_id, wallet_id, loan_id, party_id = group.id, wallet.id, loan.id, loan.counterparty_id
    as_receivable = {"id": loan_id, "account_id": wallet_id, "kind": "receivable", "amount": "1000", "counterparty_id": party_id}
    before = _ledger(db_session)

    for body in (_split(_echo(plain), _member(wallet, amount="5")), _split(_echo(plain), as_receivable)):
        response = client.put(f"/splits/{group_id}", json=body)
        assert response.status_code == 409
        assert response.json()["message"].startswith("member_locked") and "scheduled_loan" in response.json()["message"]
    assert _ledger(db_session) == before


def test_attached_disabled_rule_round_trips_on_keep_full_but_not_on_new(client, db_session, seed):
    card = seed.account("卡")
    rule = seed.rule(card, "一般回饋")
    db_session.commit()
    group_id = ss.create_split(db_session, SplitIn(**_split(_member(card, reward_rule_ids=[rule.id]), _member(card, amount="50"))))
    rule.is_enabled = False  # an import disabled it after it was attached
    db_session.commit()
    first, second = _members(db_session, group_id)
    rule_id, card_id, first_id = rule.id, card.id, first.id
    first_body, second_body = _echo(first, amount="120", reward_rule_ids=[rule_id]), _echo(second)

    kept = client.put(f"/splits/{group_id}", json=_split(first_body, second_body))
    refused = client.put(f"/splits/{group_id}", json=_split(
        first_body, second_body, {"account_id": card_id, "kind": "expense", "amount": "10", "reward_rule_ids": [rule_id]},
    ))

    assert kept.status_code == 200, kept.text
    assert (refused.status_code, [e["loc"] for e in refused.json()["detail"]]) == (422, [["members.2.reward_rule_ids"]])
    db_session.expire_all()
    assert db_session.scalars(select(EntryRewardRule.rule_id).where(EntryRewardRule.entry_id == first_id)).all() == [rule_id]
    assert len(_members(db_session, group_id)) == 2


def test_old_split_keeps_each_member_date_when_one_amount_changes(client, db_session, seed):
    # Review Focus 1: an old split whose members have different dates, times and merchants; only the second
    # amount changes → the first is not even prepared, and each member keeps its own date/time/merchant.
    wallet = seed.account()
    group = seed.group(name="舊拆帳")
    first = seed.entry(wallet, "-100", group_id=group.id, day=date(2026, 9, 1), entry_time=time(9, 15, 30), merchant="舊商家")
    second = seed.entry(wallet, "-50", group_id=group.id, day=date(2026, 9, 3), entry_time=time(20, 0))
    db_session.commit()
    group_id, first_id, second_id = group.id, first.id, second.id
    first_updated_at = first.updated_at

    response = client.put(f"/splits/{group_id}", json=_split(_echo(first), _echo(second, amount="60"), name="舊拆帳"))

    assert response.status_code == 200, response.text
    db_session.expire_all()
    a, b = db_session.get(LedgerEntry, first_id), db_session.get(LedgerEntry, second_id)
    assert (a.entry_date, a.entry_time, a.posted_date, a.merchant, a.amount) == (
        date(2026, 9, 1), time(9, 15, 30), date(2026, 9, 1), "舊商家", Decimal("-100"),
    )
    assert a.updated_at == first_updated_at  # fully unchanged: not written at all
    assert (b.entry_date, b.entry_time, b.amount) == (date(2026, 9, 3), time(20, 0), Decimal("-60"))


def test_metadata_only_change_on_an_online_fx_member_makes_no_fx_request(db_session, seed, fake_http):
    # Review Focus 5: name changed only, provider unavailable → no FX request; FX fields and children unchanged.
    card = seed.account("華航卡")
    group = seed.group()
    online = seed.entry(card, "-389.34", group_id=group.id, original_amount=Decimal("-1800"), original_currency="JPY",
                        fx_rate=Decimal("0.2163"), fx_source="fx_api", name="拉麵")
    fee = seed.entry(card, "-15", kind="fee", parent_entry_id=online.id, name="手續費")
    other = seed.entry(card, "-100", group_id=group.id)
    db_session.commit()
    group_id, online_id, fee_id = group.id, online.id, fee.id
    http = fake_http({})  # every request would fail and be recorded
    body = _split(
        _echo(online, amount=None, original_amount="1800", original_currency="JPY", fx_rate=None, name="豚骨拉麵",
              fee={"amount": "15"}),
        _echo(other, amount="120"),
    )

    ss.update_split(db_session, group_id, SplitIn(**body), http_get=http)
    db_session.commit()

    assert http.calls == []
    db_session.expire_all()
    row = db_session.get(LedgerEntry, online_id)
    assert (row.amount, row.original_amount, row.fx_rate, row.fx_source, row.name) == (
        Decimal("-389.34"), Decimal("-1800"), Decimal("0.2163"), "fx_api", "豚骨拉麵",
    )
    assert db_session.get(LedgerEntry, fee_id) is not None  # the child was not rebuilt


def test_an_unchanged_member_with_an_expired_rule_is_not_prepared(client, db_session, seed, monkeypatch):
    card = seed.account("卡")
    rule = seed.rule(card, "限期回饋")
    db_session.commit()
    group_id = ss.create_split(db_session, SplitIn(**_split(_member(card, reward_rule_ids=[rule.id]), _member(card, amount="50"))))
    rule.ends_on = date(2026, 8, 31)  # expired before the entry date
    db_session.commit()
    first, second = _members(db_session, group_id)
    rule_id = rule.id
    prepared = _spy_prepare(monkeypatch)

    response = client.put(f"/splits/{group_id}", json=_split(
        _echo(first, reward_rule_ids=[rule_id], name="改名"), _echo(second, amount="60"),
    ))

    assert response.status_code == 200, response.text
    assert prepared == [Decimal("60")]  # only the changed member went through prepare_entry
    assert [m.name for m in _members(db_session, group_id)] == ["改名", None]


def test_fully_unchanged_payload_is_a_no_op(client, db_session, seed, monkeypatch):
    wallet_id, group_id, [first_id, second_id] = _two_member_split(db_session, seed)
    first, second = _members(db_session, group_id)
    stamps = (first.updated_at, second.updated_at)
    prepared = _spy_prepare(monkeypatch)

    response = client.put(f"/splits/{group_id}", json=_split(_echo(first), _echo(second)))

    assert response.status_code == 200, response.text
    assert prepared == []
    first, second = _members(db_session, group_id)
    assert (first.updated_at, second.updated_at) == stamps


def test_merchant_only_change_is_applied(client, db_session, seed, monkeypatch):
    wallet_id, group_id, [first_id, second_id] = _two_member_split(db_session, seed)
    first, second = _members(db_session, group_id)
    prepared = _spy_prepare(monkeypatch)

    response = client.put(f"/splits/{group_id}", json=_split(_echo(first, merchant="全家"), _echo(second)))

    assert response.status_code == 200, response.text
    assert prepared == []
    assert [(m.merchant, m.amount) for m in _members(db_session, group_id)] == [("全家", Decimal("-100")), (None, Decimal("-50"))]


def test_reward_ledger_rows_are_never_touched(client, db_session, seed):
    card = seed.account("卡")
    db_session.commit()
    group_id = ss.create_split(db_session, SplitIn(**_split(_member(card), _member(card, amount="50"))))
    db_session.commit()
    first, second = _members(db_session, group_id)
    reward = seed.entry(card, "3", kind="reward", reward_source_entry_id=first.id)
    db_session.commit()
    reward_id, first_id = reward.id, first.id

    response = client.put(f"/splits/{group_id}", json=_split(_echo(first, amount="150"), _echo(second)))

    assert response.status_code == 200, response.text
    db_session.expire_all()
    row = db_session.get(LedgerEntry, reward_id)
    assert (row.amount, row.reward_source_entry_id) == (Decimal("3"), first_id)


def test_a_parent_date_change_only_reaches_full_members(client, db_session, seed):
    wallet = seed.account()
    alan = seed.counterparty("Alan")
    group = seed.group()
    plain = seed.entry(wallet, "-100", group_id=group.id)
    collection = seed.entry(wallet, "200", kind="receivable", counterparty_id=alan.id, is_settlement=True, group_id=group.id)
    db_session.commit()
    group_id, plain_id, collection_id = group.id, plain.id, collection.id

    response = client.put(f"/splits/{group_id}", json=_split(
        _echo(plain, entry_date="2026-09-05", posted_date="2026-09-05"), _keep(collection_id), entry_date="2026-09-05",
    ))

    assert response.status_code == 200, response.text
    db_session.expire_all()
    assert db_session.get(LedgerEntry, plain_id).entry_date == date(2026, 9, 5)
    assert db_session.get(LedgerEntry, collection_id).entry_date == DAY
```

In `tests/integration/test_edit_lock.py`, replace line 58:

```python
    "put_split": lambda b: ("PUT", f"/splits/{b['split']}", {"entry_date": DAY, "members": [_entry_body(b, amount="40")]}, 200),
```

with:

```python
    "put_split": lambda b: (
        "PUT", f"/splits/{b['split']}",
        {"entry_date": DAY, "members": [_entry_body(b, amount="40"), _entry_body(b, amount="20")]}, 200,
    ),
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/opc/workspace/home-hub-splitapi/services/accounting-service && /home/opc/workspace/home-hub-schedules/services/accounting-service/.venv/bin/python -m pytest tests/integration/test_splits.py -q`
Expected: FAIL — e.g. `test_upsert_keeps_ids_and_applies_keep_full_keep_meta_new_and_drop` (old PUT recreates every member: lunch id gone), `test_protected_member_takes_metadata_only[*]` (422 `members` instead of 409 / 200), `test_concurrent_split_updates_serialise_and_the_second_retries` (committed, not retry).

- [ ] **Step 3: Extract the in-place rewrite in entry_write_service**

Insert after `insert_prepared` (after line 319) in `app/services/entry_write_service.py`:

```python


def apply_prepared_update(db: Session, entry: LedgerEntry, prepared: PreparedEntry) -> None:
    """Rewrite an editable entry the caller holds FOR UPDATE: columns, fee / discount children and rule links.
    Reward ledger rows point at the entry through reward_source_entry_id, not parent_entry_id, so they stay. A
    posted period's entry stays a schedule row."""
    payload = prepared.payload
    source = "schedule" if entry.source == "schedule" else "manual"
    _apply(entry, prepared, source)
    db.execute(delete(LedgerEntry).where(LedgerEntry.parent_entry_id == entry.id))
    db.flush()
    write_children(db, entry, payload.fee, payload.discount, source=source)
    write_rule_links(db, entry.id, payload.reward_rule_ids)
```

In `update_entry`, replace lines 463–468:

```python
    source = "schedule" if entry.source == "schedule" else "manual"  # a posted period's entry stays a schedule row
    _apply(entry, prepared, source)
    db.execute(delete(LedgerEntry).where(LedgerEntry.parent_entry_id == entry.id))
    db.flush()
    write_children(db, entry, payload.fee, payload.discount, source=source)
    write_rule_links(db, entry.id, payload.reward_rule_ids)
```

with:

```python
    apply_prepared_update(db, entry, prepared)
```

- [ ] **Step 4: Replace split_service with the phased upsert**

Replace `app/services/split_service.py` with:

```python
"""Splits (design D15; split rework §1): an entry_group of kind split whose members each move one account.

Every write follows the rework's phases (§1.4): (1) a preliminary read and classification with no lock and no FX;
(2) prepare: validation and FX resolution, which may commit the session through the FX cache
(fx_rate_service.get_rate), so it runs before any lock or ledger write; (3) locks in the accepted order; (4) a
re-read inside the locks, where anything that moved since (1) is a 409 retry (never a re-preparation); (5) the
writes, through helpers that never commit. The router commits once, so a split write lands completely or not at
all (FX cache rows excepted).
"""

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import EntryGroup, LedgerEntry
from ..schemas.writes import MAX_SPLIT_MEMBERS, EntryIn, SplitIn, SplitKeepIn, SplitMemberIn
from . import schedule_entry_hooks
from .edit_lock import assert_editable
from .entry_write_service import (
    EDITABLE_KINDS,
    PreparedEntry,
    apply_prepared_update,
    attached_rule_ids,
    check_project,
    delete_entries_cascade,
    insert_prepared,
    lock_group,
    prepare_entry,
    remember_all_defaults,
)
from .errors import (
    GROUP_SCHEDULED,
    MEMBER_LOCKED,
    MEMBER_NOT_FOUND,
    RETRY,
    CodedConflictError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from .split_compare import MemberSignature, classify, payload_signature, stored_signature
from .split_protection import protected_reasons

SHARED_FIELDS = ("entry_date", "entry_time", "posted_date", "project_id", "tags")
KEEP_FIELDS = ("name", "project_id", "tags", "description")  # what SplitKeepIn may change


def member_payload(payload: SplitIn, member: SplitMemberIn) -> EntryIn:
    """One full member as an EntryIn; fields the member did not send come from the group, except posted_date,
    which defaults to the member's own entry_date when the member sent one. The rework's client sends every
    per-member field; this inheritance stays for callers that omit them (§1.3)."""
    data = member.model_dump(exclude={"id", "client_key"})
    own_date = data["entry_date"]  # captured before the loop fills entry_date from the group
    for field in SHARED_FIELDS:
        if field not in member.model_fields_set or (field == "entry_date" and data[field] is None):
            if field == "posted_date" and own_date is not None:
                data[field] = own_date  # a member with its own entry_date posts on that date, not the group's
            else:
                data[field] = getattr(payload, field)
    return EntryIn(**data)


def member_payloads(payload: SplitIn) -> list[EntryIn]:
    """member_payload for every full member, in request order (keep members carry no entry payload)."""
    return [member_payload(payload, member) for member in payload.members if isinstance(member, SplitMemberIn)]


@dataclass(frozen=True)
class SplitResult:
    """What every split write answers (§1.6): member ids with their client keys, in request order."""

    group_id: int | None
    members: list[tuple[int, str | None]]

    def out(self) -> dict:
        return {
            "group_id": self.group_id,
            "member_ids": [entry_id for entry_id, _ in self.members],
            "members": [{"id": entry_id, "client_key": key} for entry_id, key in self.members],
        }


@dataclass
class _Member:
    """One payload member through the phases. action: "new" (no id), "full" (SplitMemberIn with an id) or "keep"
    (SplitKeepIn). For "full", `stored` is the signature the decision was taken on and `change` its verdict
    ("unchanged" / "meta" / "financial"); `prepared` is set for "new" and financially changed "full" members."""

    index: int
    item: SplitMemberIn | SplitKeepIn
    entry_id: int | None
    action: str
    payload: EntryIn | None = None
    stored: MemberSignature | None = None
    change: str | None = None
    prepared: PreparedEntry | None = None


def _prepare(db: Session, members: list[_Member], http_get) -> None:
    """Phase 2: prepare_entry for new members and financially changed full ones (a full member passes its current
    rule links as `attached`, so a rule disabled or expired after import still round-trips); a metadata-only
    member only has its project checked. Never called with a lock held: FX resolution may commit the session.
    Errors are renamed members.{i}.{field}."""
    for member in members:
        try:
            if member.action == "new" or member.change == "financial":
                attached = attached_rule_ids(db, member.entry_id) if member.entry_id is not None else ()
                member.prepared = prepare_entry(db, member.payload, http_get=http_get, attached_rules=attached)
            elif member.action == "full" and member.change == "meta":
                check_project(db, member.payload.project_id)
            elif member.action == "keep" and "project_id" in member.item.model_fields_set:
                check_project(db, member.item.project_id)
        except ValidationError as exc:
            raise ValidationError(f"members.{member.index}.{exc.field}", exc.message) from exc


def _check_create_cardinality(payload: SplitIn) -> None:
    """POST /splits: 2 to MAX_SPLIT_MEMBERS members, none of them existing (§1.3)."""
    if not 2 <= len(payload.members) <= MAX_SPLIT_MEMBERS:
        raise ValidationError("members", f"a new split has 2 to {MAX_SPLIT_MEMBERS} members")
    for index, item in enumerate(payload.members):
        if isinstance(item, SplitKeepIn):
            raise ValidationError(f"members.{index}.keep", "a new split has no existing members")
        if item.id is not None:
            raise ValidationError(f"members.{index}.id", "a new split has no existing members")


def create_split_result(db: Session, payload: SplitIn, *, http_get=None) -> SplitResult:
    """POST /splits: cardinality, then every member prepared (FX may commit), then the group and members written."""
    _check_create_cardinality(payload)
    members = [
        _Member(index, item, None, "new", payload=member_payload(payload, item))
        for index, item in enumerate(payload.members)
    ]
    _prepare(db, members, http_get)
    group = EntryGroup(kind="split", name=payload.name, merchant=payload.merchant, description=payload.description)
    db.add(group)
    db.flush()
    for member in members:
        member.entry_id = insert_prepared(db, member.prepared, group_id=group.id, remember=False)
    remember_all_defaults(db, [member.prepared for member in members])
    return SplitResult(group.id, [(member.entry_id, member.item.client_key) for member in members])


def create_split(db: Session, payload: SplitIn, *, http_get=None) -> int:
    return create_split_result(db, payload, http_get=http_get).group_id


def member_ids(db: Session, group_id: int) -> list[int]:
    return list(
        db.scalars(
            select(LedgerEntry.id)
            .where(LedgerEntry.group_id == group_id, LedgerEntry.parent_entry_id.is_(None))
            .order_by(LedgerEntry.seq)
        )
    )


# Lock order for every write that locks a group row (shared with entry_write_service.delete_entry and the schedule
# paths, D32): schedule rows (only the delete paths take them) → the entry_group row(s) (lock_group, ascending id) →
# the entries (one SELECT … FOR UPDATE by ascending id; delete_entry adds transfer legs and the members of the split
# groups it may dissolve to that same statement) → no further row lock except the category rows of the defaults,
# written last in ascending id (remember_all_defaults). A split PUT takes no schedule lock: a scheduled group is
# refused before and again inside the locks. Convert (convert_to_split) locks only its ungrouped anchor row.
#
# Exception: PUT /entries/{id}, settle and refund on a member do NOT take the group lock. They lock only the
# target entry row (locked_entry) and lock nothing else in the group afterwards, which is why they cannot
# invert the order above. Adding a group-row or second-member write to any of them would break this; such a
# change must take the group lock first like delete_entry.


def _locked_split(db: Session, group_id: int) -> EntryGroup:
    """The split's entry_group row, SELECT … FOR UPDATE via lock_group; NotFoundError if a concurrent delete
    removed it."""
    group = lock_group(db, group_id)
    if group is None or group.kind != "split":
        raise NotFoundError(f"split {group_id} not found")
    return group


def _locked_members(db: Session, group_id: int) -> list[LedgerEntry]:
    """The group's members, one SELECT … FOR UPDATE ordered by ascending id (the row lock settle / refund take
    via locked_entry; the same order locked_with_legs uses). Callers hold the group lock first."""
    return list(
        db.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.group_id == group_id, LedgerEntry.parent_entry_id.is_(None))
            .order_by(LedgerEntry.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def _assert_no_transfers_or_system_entries(members: list[LedgerEntry]) -> None:
    """DELETE /splits/{id} (unchanged by the rework): imported split groups can hold a transfer leg whose
    counterpart is outside the group, or reward / interest / balance-adjustment rows; deleting the group would
    orphan the counterpart or delete rows delete_entry refuses, so such groups are deleted one entry at a time."""
    for member in members:
        if member.transfer_group_id is not None or member.kind not in EDITABLE_KINDS:
            raise ValidationError("members", "groups containing transfers, rewards or system entries are edited per entry")


def _assert_not_scheduled(db: Session, group_id: int) -> None:
    """D29: a group a posted period lists is edited one period at a time; 409 group_scheduled naming the instance."""
    try:
        schedule_entry_hooks.assert_group_not_scheduled(db, group_id)
    except ConflictError as exc:
        raise CodedConflictError(GROUP_SCHEDULED, str(exc)) from exc


def _readable_split(db: Session, group_id: int) -> EntryGroup:
    """Phase 1 group checks in precedence order: 404 missing → 409 group_scheduled (before the kind check, so the
    accepted D29 scenario on an installment group still answers 409) → 404 not a split → 409 cutover lock."""
    group = db.get(EntryGroup, group_id)
    if group is None:
        raise NotFoundError(f"split {group_id} not found")
    _assert_not_scheduled(db, group_id)
    if group.kind != "split":
        raise NotFoundError(f"split {group_id} not found")
    assert_editable(group)
    return group


def _current_members(db: Session, group_id: int) -> list[LedgerEntry]:
    """The group's top-level members, unlocked, ascending id."""
    return list(
        db.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.group_id == group_id, LedgerEntry.parent_entry_id.is_(None))
            .order_by(LedgerEntry.id)
        )
    )


def _check_put_cardinality(payload: SplitIn, current_count: int) -> None:
    """PUT /splits/{id}: at most max(50, current) members (an imported group over 50 stays editable but never
    grows); a one-member PUT is a dissolve and must name an existing member."""
    limit = max(MAX_SPLIT_MEMBERS, current_count)
    if len(payload.members) > limit:
        raise ValidationError("members", f"at most {limit} members")
    if len(payload.members) == 1 and payload.members[0].id is None:
        raise ValidationError("members.0.id", "a split edited down to one member keeps one of its members")


def _plan_members(payload: SplitIn, current: list[LedgerEntry]) -> list[_Member]:
    """keep_full / keep_meta / new per payload member; 404 member_not_found for an id outside the group."""
    known = {entry.id for entry in current}
    members = []
    for index, item in enumerate(payload.members):
        if item.id is not None and item.id not in known:
            raise NotFoundError(f"{MEMBER_NOT_FOUND}: entry {item.id} is not a member of this split")
        if isinstance(item, SplitKeepIn):
            members.append(_Member(index, item, item.id, "keep"))
        else:
            action = "new" if item.id is None else "full"
            members.append(_Member(index, item, item.id, action, payload=member_payload(payload, item)))
    return members


def _refuse_locked(members: list[_Member], drop_ids: list[int], protected: dict[int, str | None]) -> None:
    """§1.5: a protected member is only accepted in the keep form and is never dropped (409 member_locked)."""
    for member in members:
        reason = protected.get(member.entry_id) if member.action == "full" else None
        if reason is not None:
            raise CodedConflictError(
                MEMBER_LOCKED, f"member {member.entry_id} is protected ({reason}); send it as {{id, keep: true}}"
            )
    for entry_id in drop_ids:
        if protected[entry_id] is not None:
            raise CodedConflictError(MEMBER_LOCKED, f"member {entry_id} is protected ({protected[entry_id]}) and cannot be removed")


def _classify_full(db: Session, members: list[_Member], by_id: dict[int, LedgerEntry]) -> None:
    """The canonical comparison for every keep_full member (split_compare), on the unlocked read."""
    for member in members:
        if member.action == "full":
            entry = by_id[member.entry_id]
            member.stored = stored_signature(db, entry)
            member.change = classify(member.stored, payload_signature(member.payload, entry.currency))


def _revalidate(
    db: Session,
    group: EntryGroup | None,
    group_id: int,
    locked: list[LedgerEntry],
    before_ids: list[int],
    protected: dict[int, str | None],
    members: list[_Member],
) -> None:
    """Phase 4, inside the group and member locks: the group still a split, still editable and unscheduled; the
    same members, the same protected set and, for every keep_full member, the same stored signature as in phase 1.
    Any drift is a 409 retry: the client re-reads and re-submits."""
    if group is None or group.kind != "split":
        raise CodedConflictError(RETRY, f"split {group_id} changed concurrently")
    assert_editable(group)
    _assert_not_scheduled(db, group_id)
    by_id = {entry.id: entry for entry in locked}
    if sorted(by_id) != before_ids:
        raise CodedConflictError(RETRY, f"the members of split {group_id} changed concurrently")
    if protected_reasons(db, locked) != protected:
        raise CodedConflictError(RETRY, f"a member of split {group_id} was settled, refunded or scheduled concurrently")
    for member in members:
        if member.action == "full" and stored_signature(db, by_id[member.entry_id]) != member.stored:
            raise CodedConflictError(RETRY, f"member {member.entry_id} changed concurrently")


def _apply_meta(entry: LedgerEntry, payload: EntryIn) -> None:
    """A financially unchanged keep_full member: metadata only (no FX, no child or rule-link rebuild)."""
    entry.name, entry.merchant, entry.project_id = payload.name, payload.merchant, payload.project_id
    entry.tags, entry.description = list(payload.tags), payload.description


def _apply_keep(entry: LedgerEntry, item: SplitKeepIn) -> None:
    """A keep_meta member: only the fields the request sent (null and [] included)."""
    for field in KEEP_FIELDS:
        if field in item.model_fields_set:
            value = getattr(item, field)
            setattr(entry, field, list(value or []) if field == "tags" else value)


def update_split(db: Session, group_id: int, payload: SplitIn, *, http_get=None) -> SplitResult:
    """PUT /splits/{group_id}: upsert by member id (§1.5). keep_full members are rewritten in place (financially
    changed), get a metadata-only update (financially unchanged) or are skipped (fully unchanged); keep_meta
    members get the sent metadata; new members are inserted; current members absent from the payload are deleted.
    Protected members (split_protection) only take the keep form and are never dropped. Reward ledger rows are
    never touched. Errors in phase order: see _readable_split, _check_put_cardinality, _plan_members,
    _refuse_locked, _prepare, _revalidate."""
    # 1. preliminary read and classification: no lock, no FX
    _readable_split(db, group_id)
    current = _current_members(db, group_id)
    _check_put_cardinality(payload, len(current))
    members = _plan_members(payload, current)
    kept = {member.entry_id for member in members if member.entry_id is not None}
    drop_ids = sorted(entry.id for entry in current if entry.id not in kept)
    protected = protected_reasons(db, current)
    _refuse_locked(members, drop_ids, protected)
    _classify_full(db, members, {entry.id: entry for entry in current})
    before_ids = sorted(entry.id for entry in current)
    # 2. prepare: may commit through the FX cache, so it runs before any lock or ledger write
    _prepare(db, members, http_get)
    # 3. lock: the group row, then its members in one statement by ascending id
    group = lock_group(db, group_id)
    locked = _locked_members(db, group_id) if group is not None else []
    # 4. re-read and re-validate inside the locks
    _revalidate(db, group, group_id, locked, before_ids, protected, members)
    # 5. write: no helper below commits
    delete_entries_cascade(db, drop_ids)  # expires the session; rows are re-read below under the held locks
    for member in members:
        if member.action == "new":
            member.entry_id = insert_prepared(db, member.prepared, group_id=group_id, remember=False)
        elif member.action == "keep":
            _apply_keep(db.get(LedgerEntry, member.entry_id), member.item)
        elif member.change == "financial":
            apply_prepared_update(db, db.get(LedgerEntry, member.entry_id), member.prepared)
        elif member.change == "meta":
            _apply_meta(db.get(LedgerEntry, member.entry_id), member.payload)
    db.flush()
    group = db.get(EntryGroup, group_id)
    group.name, group.merchant, group.description = payload.name, payload.merchant, payload.description
    db.flush()
    remember_all_defaults(db, [member.prepared for member in members if member.prepared is not None])
    return SplitResult(group_id, [(member.entry_id, member.item.client_key) for member in members])


def delete_split(db: Session, group_id: int) -> None:
    """Same lock order as update_split (group → members), after the schedule rows of a posted period that lists the
    members (D32). Groups holding transfer legs or non-editable kinds are refused under the member locks; settled
    members may be deleted (links are cleared)."""
    instance = schedule_entry_hooks.lock_for_entry_delete(db, member_ids(db, group_id))
    group = _locked_split(db, group_id)
    assert_editable(group)
    members = _locked_members(db, group_id)
    _assert_no_transfers_or_system_entries(members)
    doomed = [member.id for member in members]  # read before the cascade expires (and deletes) the rows
    delete_entries_cascade(db, doomed)
    db.execute(delete(EntryGroup).where(EntryGroup.id == group_id))
    if instance is not None:
        schedule_entry_hooks.after_entries_deleted(db, instance, set(doomed))
```

- [ ] **Step 5: Return the upsert's own result from the router**

In `app/routers/splits.py`, replace the body of `put_split`:

```python
    with service_errors():
        split_service.update_split(db, group_id, payload)
    db.commit()
    ids = split_service.member_ids(db, group_id)  # replaced by the upsert's own result in Task 4
    return split_service.SplitResult(group_id, list(zip(ids, [m.client_key for m in payload.members]))).out()
```

with:

```python
    with service_errors():
        result = split_service.update_split(db, group_id, payload)
    db.commit()
    return result.out()
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd /home/opc/workspace/home-hub-splitapi/services/accounting-service && /home/opc/workspace/home-hub-schedules/services/accounting-service/.venv/bin/python -m pytest tests/integration/test_splits.py tests/integration/test_edit_lock.py tests/integration/test_schedule_entry_hooks.py tests/integration/test_entry_writes.py tests/integration/test_settlements.py -q`
Expected: PASS (`test_schedule_entry_hooks.py` unchanged: its one-member bodies hit 409 `group_scheduled` before the membership checks).

- [ ] **Step 7: Commit**

```bash
cd /home/opc/workspace/home-hub-splitapi
git add services/accounting-service/app/services/entry_write_service.py services/accounting-service/app/services/split_service.py \
  services/accounting-service/app/routers/splits.py services/accounting-service/tests/integration/test_splits.py \
  services/accounting-service/tests/integration/test_edit_lock.py
git commit -m "$(cat <<'EOF'
feat(accounting): upsert split members by id

PUT /splits/{gid} now partitions members into keep_full, keep_meta, new
and drop; untouched members are skipped, metadata-only changes skip FX,
protected members only take the keep form (409 member_locked), and the
group -> members locks are taken after prepare with a 409 retry when
anything moved.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---
### Task 5: Dissolve through a one-member PUT

**Files:**
- Modify: `services/accounting-service/app/services/entry_write_service.py` (insert after `delete_entries_cascade`, which ends at line 489 before Task 4's insert; place it directly above `def _group_ids_to_lock`)
- Modify: `services/accounting-service/app/services/split_service.py` (import block; tail of `update_split`)
- Create: `services/accounting-service/tests/integration/test_split_dissolve.py`

**Interfaces:**
- Consumes: Task 4 `update_split` and its phases; `make_protected_member`.
- Produces: `entry_write_service.is_blank(value: str | None) -> bool`; `entry_write_service.dissolve_group(db: Session, group_id: int, survivor: LedgerEntry, *, name: str | None, merchant: str | None, description: str | None) -> None`; `update_split` returns `SplitResult(None, …)` for a one-member payload.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_split_dissolve.py`:

```python
"""Dissolving a split (split rework §1.6 dissolve, §1.7 DELETE auto-dissolve)."""

import uuid
from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import EntryGroup, EntryRewardRule, LedgerEntry, ScheduleInstance
from app.schemas.writes import EntryUpdateIn, SplitIn
from app.services import entry_write_service as ews
from app.services import schedule_posting as posting
from app.services import schedule_service
from app.services import split_service as ss
from app.services.errors import RETRY, CodedConflictError, NotFoundError
from tests.helpers import race

DAY = date(2026, 9, 1)


def _split(*members, **fields) -> dict:
    body = {"entry_date": DAY.isoformat(), "members": list(members)}
    body.update(fields)
    return body


def _keep(entry_id: int, **fields) -> dict:
    return {"id": entry_id, "keep": True, **fields}


def _row(db, entry_id: int) -> LedgerEntry | None:
    db.expire_all()
    return db.get(LedgerEntry, entry_id)


def _group_of_two(db_session, seed, **survivor_fields) -> tuple[int, int, int, int]:
    """A split 聚餐 / 鼎泰豐 / 生日 of two expenses on one wallet; returns wallet, group, doomed and survivor ids."""
    wallet = seed.account("錢包", opening="1000")
    group = seed.group(name="聚餐", merchant="鼎泰豐", description="生日")
    doomed = seed.entry(wallet, "-100", group_id=group.id)
    survivor = seed.entry(wallet, "-50", group_id=group.id, **survivor_fields)
    db_session.commit()
    return wallet.id, group.id, doomed.id, survivor.id


def test_dissolve_with_a_full_member_fills_blank_fields_from_the_payload(client, db_session, seed):
    wallet_id, group_id, doomed_id, survivor_id = _group_of_two(db_session, seed, merchant="自己的店", description="  ")
    survivor = {"id": survivor_id, "account_id": wallet_id, "kind": "expense", "amount": "50", "merchant": "自己的店",
                "description": "  "}

    response = client.put(f"/splits/{group_id}", json=_split(survivor, name="新名", merchant="新店", description="新備註"))

    assert response.status_code == 200, response.text
    assert response.json() == {"group_id": None, "member_ids": [survivor_id], "members": [{"id": survivor_id, "client_key": None}]}
    row = _row(db_session, survivor_id)
    # field by field, from the payload's parent values (not the stored 聚餐/鼎泰豐/生日); whitespace counts as empty
    assert (row.name, row.merchant, row.description, row.group_id) == ("新名", "自己的店", "新備註", None)
    assert db_session.get(EntryGroup, group_id) is None
    assert db_session.get(LedgerEntry, doomed_id) is None


def test_dissolve_with_a_keep_member(client, db_session, seed):
    wallet_id, group_id, doomed_id, survivor_id = _group_of_two(db_session, seed)

    response = client.put(f"/splits/{group_id}", json=_split(_keep(survivor_id, name="自取", client_key="s"), name="新名", merchant="新店"))

    assert response.status_code == 200, response.text
    assert response.json()["members"] == [{"id": survivor_id, "client_key": "s"}]
    row = _row(db_session, survivor_id)
    assert (row.name, row.merchant, row.description, row.group_id, row.amount) == ("自取", "新店", None, None, Decimal("-50"))


def test_dissolve_onto_a_protected_survivor_keeps_its_dates(client, db_session, seed):
    wallet = seed.account()
    alan = seed.counterparty("Alan")
    group = seed.group(name="代墊")
    seed.entry(wallet, "-100", group_id=group.id)
    collection = seed.entry(wallet, "200", kind="receivable", counterparty_id=alan.id, is_settlement=True,
                            group_id=group.id, entry_time=time(8, 5))
    db_session.commit()
    group_id, wallet_id, alan_id, collection_id = group.id, wallet.id, alan.id, collection.id
    as_full = {"id": collection_id, "account_id": wallet_id, "kind": "receivable", "amount": "200", "counterparty_id": alan_id}

    refused = client.put(f"/splits/{group_id}", json=_split(as_full, entry_date="2026-09-09"))
    kept = client.put(f"/splits/{group_id}", json=_split(_keep(collection_id), name="代墊", entry_date="2026-09-09"))

    assert (refused.status_code, refused.json()["message"].split(":")[0]) == (409, "member_locked")
    assert (kept.status_code, kept.json()["group_id"]) == (200, None)
    row = _row(db_session, collection_id)
    assert (row.amount, row.is_settlement, row.entry_date, row.entry_time, row.name, row.group_id) == (
        Decimal("200"), True, DAY, time(8, 5), "代墊", None,
    )


def test_dissolve_refusals(client, db_session, seed):
    wallet_id, group_id, doomed_id, survivor_id = _group_of_two(db_session, seed)
    other_wallet = seed.account("別的")
    other_group = seed.group()
    foreign = seed.entry(other_wallet, "-1", group_id=other_group.id)
    db_session.commit()

    no_id = client.put(f"/splits/{group_id}", json=_split({"account_id": wallet_id, "kind": "expense", "amount": "5"}))
    foreign_id = client.put(f"/splits/{group_id}", json=_split(_keep(foreign.id)))

    assert (no_id.status_code, no_id.json()["detail"][0]["loc"]) == (422, ["members.0.id"])
    assert (foreign_id.status_code, foreign_id.json()["message"].split(":")[0]) == (404, "member_not_found")
    assert db_session.get(EntryGroup, group_id) is not None


def test_only_split_groups_dissolve(client, db_session, seed):
    wallet = seed.account()
    group = seed.group(kind="installment", name="分期")
    first = seed.entry(wallet, "-100", group_id=group.id)
    seed.entry(wallet, "-100", group_id=group.id)
    db_session.commit()

    response = client.put(f"/splits/{group.id}", json=_split(_keep(first.id)))

    assert response.status_code == 404
    assert _row(db_session, first.id).group_id == group.id
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/opc/workspace/home-hub-splitapi/services/accounting-service && /home/opc/workspace/home-hub-schedules/services/accounting-service/.venv/bin/python -m pytest tests/integration/test_split_dissolve.py -q`
Expected: FAIL — the three dissolve tests answer `group_id: <int>` and the survivor keeps `group_id`; `test_dissolve_refusals` and `test_only_split_groups_dissolve` already PASS (Task 4 behaviour).

- [ ] **Step 3: Add the dissolve helper**

In `app/services/entry_write_service.py`, insert directly above `def _group_ids_to_lock`:

```python
def is_blank(value: str | None) -> bool:
    """Empty for the dissolve copy rule (split rework §1.6): None or whitespace only."""
    return value is None or not value.strip()


def dissolve_group(
    db: Session, group_id: int, survivor: LedgerEntry, *, name: str | None, merchant: str | None, description: str | None
) -> None:
    """Turn a split left with one member back into a plain entry (§1.6 dissolve, §1.7): each non-blank parent value
    fills the survivor's field only when that field is blank, field by field; then every row still carrying the
    group id is detached and the group row deleted, in the caller's transaction. The caller holds the group row
    and the survivor FOR UPDATE."""
    for field, value in (("name", name), ("merchant", merchant), ("description", description)):
        if not is_blank(value) and is_blank(getattr(survivor, field)):
            setattr(survivor, field, value)
    db.flush()
    db.execute(
        update(LedgerEntry)
        .where(LedgerEntry.group_id == group_id)
        .values(group_id=None)
        .execution_options(synchronize_session=False)
    )
    db.execute(delete(EntryGroup).where(EntryGroup.id == group_id))
    db.expire_all()


```

- [ ] **Step 4: Dissolve at the end of the upsert**

In `app/services/split_service.py`, add `dissolve_group,` to the `from .entry_write_service import (...)` list (alphabetically after `delete_entries_cascade,`). Then replace the tail of `update_split`:

```python
    db.flush()
    group = db.get(EntryGroup, group_id)
    group.name, group.merchant, group.description = payload.name, payload.merchant, payload.description
    db.flush()
    remember_all_defaults(db, [member.prepared for member in members if member.prepared is not None])
    return SplitResult(group_id, [(member.entry_id, member.item.client_key) for member in members])
```

with:

```python
    db.flush()
    result = [(member.entry_id, member.item.client_key) for member in members]
    if len(members) == 1:
        # Dissolve (§1.6): the payload's parent values fill the survivor's blank fields, then the group goes.
        survivor = db.get(LedgerEntry, members[0].entry_id)
        dissolve_group(db, group_id, survivor, name=payload.name, merchant=payload.merchant, description=payload.description)
        group_out = None
    else:
        group = db.get(EntryGroup, group_id)
        group.name, group.merchant, group.description = payload.name, payload.merchant, payload.description
        db.flush()
        group_out = group_id
    remember_all_defaults(db, [member.prepared for member in members if member.prepared is not None])
    return SplitResult(group_out, result)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /home/opc/workspace/home-hub-splitapi/services/accounting-service && /home/opc/workspace/home-hub-schedules/services/accounting-service/.venv/bin/python -m pytest tests/integration/test_split_dissolve.py tests/integration/test_splits.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/opc/workspace/home-hub-splitapi
git add services/accounting-service/app/services/entry_write_service.py services/accounting-service/app/services/split_service.py \
  services/accounting-service/tests/integration/test_split_dissolve.py
git commit -m "$(cat <<'EOF'
feat(accounting): dissolve a split through a one-member PUT

The survivor keeps its id; the payload's name/merchant/description fill
only its blank fields, it is detached and the group row deleted;
the response carries group_id null.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Convert `PUT /entries/{entry_id}/split`

**Files:**
- Modify: `services/accounting-service/app/services/split_service.py` (import block; append after `update_split`)
- Modify: `services/accounting-service/app/routers/entries.py` (lines 8–9 imports; new route after `remove_entry`, which ends at line 85)
- Modify: `services/accounting-service/tests/integration/test_edit_lock.py` (`CASES`, after the `put_split` entry; `EXPECTED_WRITES` line 146)
- Create: `services/accounting-service/tests/integration/test_split_convert.py`

**Interfaces:**
- Consumes: `get_entry(db, entry_id) -> LedgerEntry` (404), `locked_entry(db, entry_id) -> LedgerEntry`, `apply_prepared_update`, `insert_prepared`, `remember_all_defaults` (entry_write_service); `split_protection.protected_reason`; Task 1/4 `_Member`, `_prepare`, `member_payload`, `SplitResult`; codes `ALREADY_GROUPED`, `ENTRY_LOCKED`, `KIND_NOT_SPLITTABLE`, `RETRY`.
- Produces: `split_service._check_convert_cardinality(entry_id: int, payload: SplitIn) -> None`; `split_service._refuse_anchor(db: Session, anchor: LedgerEntry) -> None`; `split_service.convert_to_split(db: Session, entry_id: int, payload: SplitIn, *, http_get=None) -> SplitResult`; route `PUT /entries/{entry_id}/split` → `SplitOut`.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_split_convert.py`:

```python
"""PUT /entries/{id}/split: convert a single entry into a split without losing its id (split rework §1.6)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import EntryGroup, LedgerEntry
from app.schemas.writes import EntryUpdateIn, SplitIn
from app.services import entry_write_service as ews
from app.services import ledger_service
from app.services import split_service as ss
from app.services.errors import ALREADY_GROUPED, RETRY, CodedConflictError, ConflictError, ValidationError
from tests.helpers import make_protected_member, race

DAY = date(2026, 9, 1)


def _member(account_id: int, **fields) -> dict:
    body = {"account_id": account_id, "kind": "expense", "amount": "30"}
    body.update(fields)
    return body


def _convert(anchor_id: int, account_id: int, *others, **fields) -> dict:
    anchor = {"id": anchor_id, "account_id": account_id, "kind": "expense", "amount": "70", "client_key": "a"}
    body = {"entry_date": DAY.isoformat(), "members": [anchor, *others]}
    body.update(fields)
    return body


def _groups(db) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(EntryGroup))


def _anchor(db_session, seed, **fields) -> tuple[int, int]:
    wallet = seed.account("錢包", opening="1000")
    anchor = seed.entry(wallet, "-100", name="晚餐", **fields)
    db_session.commit()
    return wallet.id, anchor.id


def test_convert_keeps_the_anchor_id(client, db_session, seed):
    wallet_id, anchor_id = _anchor(db_session, seed)

    response = client.put(
        f"/entries/{anchor_id}/split",
        json=_convert(anchor_id, wallet_id, _member(wallet_id, client_key="b"), name="聚餐", merchant="鼎泰豐"),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    new_id = body["member_ids"][1]
    assert body == {"group_id": body["group_id"], "member_ids": [anchor_id, new_id],
                    "members": [{"id": anchor_id, "client_key": "a"}, {"id": new_id, "client_key": "b"}]}
    db_session.expire_all()
    group = db_session.get(EntryGroup, body["group_id"])
    anchor, other = db_session.get(LedgerEntry, anchor_id), db_session.get(LedgerEntry, new_id)
    assert (group.kind, group.name, group.merchant) == ("split", "聚餐", "鼎泰豐")
    assert (anchor.group_id, anchor.amount, other.group_id, other.amount) == (group.id, Decimal("-70"), group.id, Decimal("-30"))
    assert ledger_service.account_balance(db_session, wallet_id) == Decimal("900")


def test_invalid_second_member_leaves_the_anchor_alone(client, db_session, seed):
    # Review Focus 2: a member validation error → 422, the anchor unchanged, no group.
    wallet_id, anchor_id = _anchor(db_session, seed)

    response = client.put(f"/entries/{anchor_id}/split", json=_convert(anchor_id, wallet_id, _member(wallet_id, kind="receivable")))

    assert (response.status_code, [e["loc"] for e in response.json()["detail"]]) == (422, [["members.1.counterparty_id"]])
    db_session.expire_all()
    anchor = db_session.get(LedgerEntry, anchor_id)
    assert (anchor.amount, anchor.group_id, anchor.name) == (Decimal("-100"), None, "晚餐")
    assert _groups(db_session) == 0


def test_fx_failure_leaves_the_anchor_alone(db_session, seed, fake_http, today):
    # Review Focus 2: the FX provider is down for the second member → 422 members.1.fx_rate, nothing written.
    today(date(2026, 10, 6))
    wallet_id, anchor_id = _anchor(db_session, seed)
    http = fake_http({})
    body = SplitIn(**_convert(anchor_id, wallet_id, _member(wallet_id, amount=None, original_amount="1000", original_currency="JPY")))

    with pytest.raises(ValidationError) as error:
        ss.convert_to_split(db_session, anchor_id, body, http_get=http)
    db_session.rollback()

    assert error.value.field == "members.1.fx_rate"
    assert http.calls  # the rate was attempted, and failed
    anchor = db_session.get(LedgerEntry, anchor_id)
    assert (anchor.amount, anchor.group_id) == (Decimal("-100"), None)
    assert _groups(db_session) == 0


@pytest.mark.parametrize(
    "case, code",
    [
        ("transfer", "entry_locked"), ("fee", "entry_locked"), ("settlement", "entry_locked"),
        ("orphan_settlement", "entry_locked"), ("refunded_original", "entry_locked"), ("orphan_refund", "entry_locked"),
        ("scheduled_loan", "entry_locked"),
    ],
)
def test_protected_anchor_is_refused(client, db_session, seed, case, code):
    wallet = seed.account("錢包")
    anchor = make_protected_member(seed, case, wallet, None)
    db_session.commit()
    wallet_id, anchor_id = wallet.id, anchor.id

    response = client.put(f"/entries/{anchor_id}/split", json=_convert(anchor_id, wallet_id, _member(wallet_id)))

    assert (response.status_code, response.json()["message"].split(":")[0]) == (409, code)
    assert _groups(db_session) == 0


def test_grouped_scheduled_and_imported_anchors_are_refused(client, db_session, seed):
    wallet = seed.account("錢包")
    group = seed.group()
    grouped = seed.entry(wallet, "-100", group_id=group.id)
    scheduled = seed.entry(wallet, "-390", source="schedule")
    imported = seed.entry(wallet, "-50", source="moze_backup", moze_id="rec-1")
    db_session.commit()
    wallet_id = wallet.id

    answers = [
        client.put(f"/entries/{entry_id}/split", json=_convert(entry_id, wallet_id, _member(wallet_id)))
        for entry_id in (grouped.id, scheduled.id, imported.id)
    ]

    assert [(r.status_code, r.json()["message"].split(":")[0]) for r in answers] == [
        (409, "already_grouped"), (409, "kind_not_splittable"), (409, "locked_until_cutover"),
    ]
    assert _groups(db_session) == 1  # only the pre-existing group


def test_convert_cardinality(client, db_session, seed):
    wallet_id, anchor_id = _anchor(db_session, seed)
    anchor_only = _convert(anchor_id, wallet_id)
    no_anchor = {"entry_date": DAY.isoformat(), "members": [_member(wallet_id), _member(wallet_id)]}
    stranger = _convert(anchor_id, wallet_id, _member(wallet_id, id=anchor_id + 1000))
    keep = _convert(anchor_id, wallet_id, {"id": anchor_id + 1000, "keep": True})

    answers = [client.put(f"/entries/{anchor_id}/split", json=body) for body in (anchor_only, no_anchor, stranger, keep)]
    missing = client.put("/entries/999999/split", json=_convert(999999, wallet_id, _member(wallet_id)))

    assert [(r.status_code, r.json()["detail"][0]["loc"]) for r in answers] == [
        (422, ["members"]), (422, ["members"]), (422, ["members.1.id"]), (422, ["members.1.keep"]),
    ]
    assert missing.status_code == 404
    assert _groups(db_session) == 0


def test_a_retry_after_success_is_already_grouped(client, db_session, seed):
    wallet_id, anchor_id = _anchor(db_session, seed)
    body = _convert(anchor_id, wallet_id, _member(wallet_id))

    first = client.put(f"/entries/{anchor_id}/split", json=body)
    again = client.put(f"/entries/{anchor_id}/split", json=body)

    assert first.status_code == 200
    assert (again.status_code, again.json()["message"].split(":")[0]) == (409, "already_grouped")
    assert _groups(db_session) == 1
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry)) == 2


def test_concurrent_converts_one_wins(pg_engine, db_session, seed):
    wallet_id, anchor_id = _anchor(db_session, seed)
    body = SplitIn(**_convert(anchor_id, wallet_id, _member(wallet_id)))

    result = race(pg_engine, lambda db: ss.convert_to_split(db, anchor_id, body), lambda db: ss.convert_to_split(db, anchor_id, body))

    assert isinstance(result, CodedConflictError) and result.code == ALREADY_GROUPED
    assert _groups(db_session) == 1
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry)) == 2


def test_convert_after_a_concurrent_delete_of_the_anchor_retries(pg_engine, db_session, seed):
    wallet_id, anchor_id = _anchor(db_session, seed)
    body = SplitIn(**_convert(anchor_id, wallet_id, _member(wallet_id)))

    result = race(pg_engine, lambda db: ews.delete_entry(db, anchor_id), lambda db: ss.convert_to_split(db, anchor_id, body))

    assert isinstance(result, CodedConflictError) and result.code == RETRY
    assert _groups(db_session) == 0
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry)) == 0


def test_delete_after_a_concurrent_convert_of_the_anchor_retries(pg_engine, db_session, seed):
    wallet_id, anchor_id = _anchor(db_session, seed)
    body = SplitIn(**_convert(anchor_id, wallet_id, _member(wallet_id)))

    result = race(pg_engine, lambda db: ss.convert_to_split(db, anchor_id, body), lambda db: ews.delete_entry(db, anchor_id))

    assert isinstance(result, ConflictError)  # the anchor joined a group under the delete: re-read and retry
    assert _groups(db_session) == 1
    assert db_session.get(LedgerEntry, anchor_id).group_id is not None


@pytest.mark.parametrize("convert_first", [True, False])
def test_convert_and_an_anchor_update_serialise(pg_engine, db_session, seed, convert_first):
    wallet_id, anchor_id = _anchor(db_session, seed)
    body = SplitIn(**_convert(anchor_id, wallet_id, _member(wallet_id)))
    update = EntryUpdateIn(account_id=wallet_id, kind="expense", amount="80", entry_date=DAY)
    convert = lambda db: ss.convert_to_split(db, anchor_id, body)  # noqa: E731
    put = lambda db: ews.update_entry(db, anchor_id, update)  # noqa: E731

    result = race(pg_engine, convert, put) if convert_first else race(pg_engine, put, convert)

    assert result == "committed"
    db_session.expire_all()
    anchor = db_session.get(LedgerEntry, anchor_id)
    assert anchor.group_id is not None  # the PUT /entries never detaches a member
    assert anchor.amount == (Decimal("-80") if convert_first else Decimal("-70"))  # the later writer wins
    assert _groups(db_session) == 1
```

In `tests/integration/test_edit_lock.py`, add after the `"put_split"` case (inside `CASES`):

```python
    "convert_entry": lambda b: (
        "PUT", f"/entries/{b['expense']}/split",
        {"entry_date": DAY, "members": [_entry_body(b, id=b["expense"]), _entry_body(b, amount="20")]}, 200,
    ),
```

and in `EXPECTED_WRITES` replace line 146:

```python
    ("/entries", "POST"), ("/entries/{entry_id}", "PUT"), ("/entries/{entry_id}", "DELETE"),
```

with:

```python
    ("/entries", "POST"), ("/entries/{entry_id}", "PUT"), ("/entries/{entry_id}", "DELETE"),
    ("/entries/{entry_id}/split", "PUT"),
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/opc/workspace/home-hub-splitapi/services/accounting-service && /home/opc/workspace/home-hub-schedules/services/accounting-service/.venv/bin/python -m pytest tests/integration/test_split_convert.py tests/integration/test_edit_lock.py -q`
Expected: FAIL — `AttributeError: module 'app.services.split_service' has no attribute 'convert_to_split'` and HTTP 405 on `PUT /entries/{id}/split`; `test_write_routes_inventory` fails (route missing).

- [ ] **Step 3: Implement convert**

In `app/services/split_service.py`:
- extend the `from .entry_write_service import (...)` list with `get_entry,` and `locked_entry,` (keep it alphabetical);
- replace the `from .errors import (...)` block with:

```python
from .errors import (
    ALREADY_GROUPED,
    ENTRY_LOCKED,
    GROUP_SCHEDULED,
    KIND_NOT_SPLITTABLE,
    MEMBER_LOCKED,
    MEMBER_NOT_FOUND,
    RETRY,
    CodedConflictError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
```

- replace `from .split_protection import protected_reasons` with `from .split_protection import protected_reason, protected_reasons`;
- append after `update_split`:

```python
def _check_convert_cardinality(entry_id: int, payload: SplitIn) -> None:
    """PUT /entries/{entry_id}/split (§1.3): 2 to MAX_SPLIT_MEMBERS full members; exactly one carries an id, the
    anchor's."""
    if not 2 <= len(payload.members) <= MAX_SPLIT_MEMBERS:
        raise ValidationError("members", f"a split has 2 to {MAX_SPLIT_MEMBERS} members")
    for index, item in enumerate(payload.members):
        if isinstance(item, SplitKeepIn):
            raise ValidationError(f"members.{index}.keep", "converting an entry takes full members only")
        if item.id is not None and item.id != entry_id:
            raise ValidationError(f"members.{index}.id", f"only the converted entry {entry_id} carries an id")
    if all(item.id != entry_id for item in payload.members):
        raise ValidationError("members", f"one member must be the converted entry {entry_id}")


def _refuse_anchor(db: Session, anchor: LedgerEntry) -> None:
    """§1.6 convert refusals in order, on the unlocked read and again on the locked anchor: already in a group →
    already_grouped; protected or not an editable kind → entry_locked; posted by a schedule → kind_not_splittable;
    an imported row before cutover → locked_until_cutover."""
    if anchor.group_id is not None:
        raise CodedConflictError(ALREADY_GROUPED, f"entry {anchor.id} already belongs to group {anchor.group_id}")
    reason = protected_reason(db, anchor)  # covers every kind outside EditableKind
    if reason is not None:
        raise CodedConflictError(ENTRY_LOCKED, f"entry {anchor.id} cannot be split ({reason})")
    if anchor.source == "schedule":
        raise CodedConflictError(KIND_NOT_SPLITTABLE, f"entry {anchor.id} was posted by a schedule")
    assert_editable(anchor)


def convert_to_split(db: Session, entry_id: int, payload: SplitIn, *, http_get=None) -> SplitResult:
    """PUT /entries/{entry_id}/split (§1.6): the anchor joins a new split group and is rewritten in place (its id
    is stable); the other members are inserted. Phases as update_split; the only row lock is the anchor (it
    belongs to no group, and the new group row is invisible to other transactions until commit). A concurrent
    convert of the same entry waits on that lock and then answers already_grouped."""
    # 1. cardinality, 404, refusals on the unlocked read
    _check_convert_cardinality(entry_id, payload)
    _refuse_anchor(db, get_entry(db, entry_id))
    members = [
        _Member(
            index, item, item.id, "new" if item.id is None else "full", payload=member_payload(payload, item),
            change=None if item.id is None else "financial",  # the anchor is always prepared, with its rule links
        )
        for index, item in enumerate(payload.members)
    ]
    # 2. prepare (FX may commit), before any lock
    _prepare(db, members, http_get)
    # 3. lock the anchor; 4. re-validate it
    try:
        anchor = locked_entry(db, entry_id)
    except NotFoundError as exc:
        raise CodedConflictError(RETRY, f"entry {entry_id} was deleted concurrently") from exc
    _refuse_anchor(db, anchor)
    # 5. write
    group = EntryGroup(kind="split", name=payload.name, merchant=payload.merchant, description=payload.description)
    db.add(group)
    db.flush()
    anchor.group_id = group.id
    for member in members:
        if member.action == "full":
            apply_prepared_update(db, anchor, member.prepared)
        else:
            member.entry_id = insert_prepared(db, member.prepared, group_id=group.id, remember=False)
    db.flush()
    remember_all_defaults(db, [member.prepared for member in members])
    return SplitResult(group.id, [(member.entry_id, member.item.client_key) for member in members])
```

- [ ] **Step 4: Add the route**

In `app/routers/entries.py`, replace lines 8–9:

```python
from ..schemas.writes import EntryIn, EntryUpdateIn, EntryWriteOut, RefundIn, SettleIn
from ..services import entry_write_service, ledger_service, settlement_service
```

with:

```python
from ..schemas.writes import EntryIn, EntryUpdateIn, EntryWriteOut, RefundIn, SettleIn, SplitIn, SplitOut
from ..services import entry_write_service, ledger_service, settlement_service, split_service
```

and insert after `remove_entry` (after line 85):

```python


@router.put("/{entry_id}/split", response_model=SplitOut)
def put_entry_split(entry_id: int, payload: SplitIn, db: Session = Depends(get_db)):
    """Convert a single entry into a split; the entry keeps its id (split rework §1.6)."""
    with service_errors():
        result = split_service.convert_to_split(db, entry_id, payload)
    db.commit()
    return result.out()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /home/opc/workspace/home-hub-splitapi/services/accounting-service && /home/opc/workspace/home-hub-schedules/services/accounting-service/.venv/bin/python -m pytest tests/integration/test_split_convert.py tests/integration/test_edit_lock.py tests/integration/test_splits.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/opc/workspace/home-hub-splitapi
git add services/accounting-service/app/services/split_service.py services/accounting-service/app/routers/entries.py \
  services/accounting-service/tests/integration/test_split_convert.py services/accounting-service/tests/integration/test_edit_lock.py
git commit -m "$(cat <<'EOF'
feat(accounting): convert an entry into a split keeping its id

PUT /entries/{id}/split creates the split group, rewrites the anchor in
place and inserts the other members; grouped, protected, schedule and
imported anchors are refused before and again under the anchor lock.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---
### Task 7: `DELETE /entries/{id}` auto-dissolve with the affected-set lock order

**Files:**
- Modify: `services/accounting-service/app/services/entry_write_service.py` (line 20 errors import; `locked_with_legs`, lines 368–386; `_group_ids_to_lock` + `delete_entry`, originally lines 492–546, now directly below `dissolve_group`)
- Modify: `services/accounting-service/tests/integration/test_entry_writes.py` (lines 599–603, the barrier spy)
- Test: `services/accounting-service/tests/integration/test_split_dissolve.py` (append)

**Interfaces:**
- Consumes: Task 5 `dissolve_group`; `schedule_entry_hooks.assert_not_referenced`, `lock_for_entry_delete`, `after_entries_deleted`; `lock_group`, `assert_entry_editable`, `delete_entries_cascade`, `get_entry`.
- Produces: `entry_write_service.locked_with_legs(db: Session, entry_id: int, transfer_group_id, also_ids: Iterable[int] = ()) -> list[LedgerEntry]`; `@dataclass(frozen=True) class AffectedSet(target_ids: tuple[int, ...], transfer_group_id: object, group_ids: tuple[int, ...], split_members: tuple[tuple[int, tuple[int, ...]], ...])` with `lock_ids() -> list[int]`; `affected_set(db: Session, entry: LedgerEntry) -> AffectedSet`; `delete_entry(db: Session, entry_id: int) -> None` (same signature, new behaviour). `_group_ids_to_lock` is removed (its only caller was `delete_entry`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_split_dissolve.py`:

```python
def test_delete_down_to_one_member_dissolves_the_split(client, db_session, seed):
    # Survivor fields and links intact; only its blank fields take the group's values.
    wallet = seed.account("錢包", opening="1000")
    alan = seed.counterparty("Alan")
    rule = seed.rule(wallet)
    group = seed.group(name="聚餐", merchant="鼎泰豐", description="生日")
    doomed = seed.entry(wallet, "-100", group_id=group.id)
    survivor = seed.entry(wallet, "-300", kind="receivable", counterparty_id=alan.id, group_id=group.id,
                          merchant="自己的店", description="  ")
    fee = seed.entry(wallet, "-15", kind="fee", parent_entry_id=survivor.id, name="手續費")
    collection = seed.entry(wallet, "100", kind="receivable", counterparty_id=alan.id, is_settlement=True,
                            settles_entry_id=survivor.id)
    db_session.add(EntryRewardRule(entry_id=survivor.id, rule_id=rule.id))
    db_session.commit()
    ids = {"group": group.id, "doomed": doomed.id, "survivor": survivor.id, "fee": fee.id, "collection": collection.id}

    assert client.delete(f"/entries/{ids['doomed']}").status_code == 204

    row = _row(db_session, ids["survivor"])
    assert (row.name, row.merchant, row.description, row.group_id, row.amount) == (
        "聚餐", "自己的店", "生日", None, Decimal("-300"),
    )
    assert db_session.get(EntryGroup, ids["group"]) is None
    assert db_session.get(LedgerEntry, ids["fee"]).parent_entry_id == ids["survivor"]
    assert db_session.get(LedgerEntry, ids["collection"]).settles_entry_id == ids["survivor"]
    assert db_session.scalars(select(EntryRewardRule.rule_id).where(EntryRewardRule.entry_id == ids["survivor"])).all() == [rule.id]


def test_installment_group_is_never_dissolved(client, db_session, seed):
    wallet = seed.account()
    group = seed.group(kind="installment", name="分期")
    first = seed.entry(wallet, "-100", group_id=group.id)
    second = seed.entry(wallet, "-100", group_id=group.id)
    db_session.commit()
    group_id, first_id, second_id = group.id, first.id, second.id

    assert client.delete(f"/entries/{first_id}").status_code == 204
    assert _row(db_session, second_id).group_id == group_id  # one member left: kept as is
    assert client.delete(f"/entries/{second_id}").status_code == 204
    db_session.expire_all()
    assert db_session.get(EntryGroup, group_id) is None  # count 0: today's cleanup


def test_scheduled_split_period_keeps_partial_and_reopen_semantics(client, db_session, seed, today):
    today(date(2026, 10, 22))
    card = seed.account("範例卡")
    bundle = seed.definition(
        [seed.line("expense", card, "390"), seed.line("expense", card, "149"), seed.line("expense", card, "60")],
        name="串流組合",
    )
    instance = seed.instance(bundle, 1, date(2026, 10, 22))
    first_id, second_id, third_id = posting.post_instance(db_session, instance.id, actor="auto").entry_ids
    db_session.commit()
    instance_id = instance.id
    group_id = db_session.get(LedgerEntry, first_id).group_id

    assert client.delete(f"/entries/{first_id}").status_code == 204
    db_session.expire_all()
    row = db_session.get(ScheduleInstance, instance_id)
    assert (row.status, row.is_partial, sorted(row.posted_entry_ids)) == ("posted", True, sorted([second_id, third_id]))
    assert db_session.get(EntryGroup, group_id) is not None  # two members left

    assert client.delete(f"/entries/{second_id}").status_code == 204
    db_session.expire_all()
    assert (db_session.get(ScheduleInstance, instance_id).posted_entry_ids, db_session.get(EntryGroup, group_id)) == ([third_id], None)
    assert db_session.get(LedgerEntry, third_id).group_id is None

    assert client.delete(f"/entries/{third_id}").status_code == 204
    db_session.expire_all()
    row = db_session.get(ScheduleInstance, instance_id)
    assert (row.status, row.posted_entry_ids, row.reopened_at is not None) == ("pending", [], True)


def test_transfer_legs_in_two_splits_dissolve_both(client, db_session, seed):
    # Both legs' groups are expanded up front: one lock set, both survivors dissolved, no dangling group.
    a, b = seed.account("A"), seed.account("B")
    first_group, second_group = seed.group(name="一"), seed.group(name="二")
    pair = uuid.uuid4()
    out_leg = seed.entry(a, "-100", kind="transfer_out", transfer_group_id=pair, group_id=first_group.id)
    in_leg = seed.entry(b, "100", kind="transfer_in", transfer_group_id=pair, group_id=second_group.id)
    x = seed.entry(a, "-10", group_id=first_group.id)
    y = seed.entry(b, "-20", group_id=second_group.id)
    db_session.commit()
    ids = (out_leg.id, in_leg.id, x.id, y.id, first_group.id, second_group.id)

    assert client.delete(f"/entries/{ids[0]}").status_code == 204

    db_session.expire_all()
    assert db_session.get(LedgerEntry, ids[0]) is None and db_session.get(LedgerEntry, ids[1]) is None
    assert db_session.get(EntryGroup, ids[4]) is None and db_session.get(EntryGroup, ids[5]) is None
    assert [(db_session.get(LedgerEntry, i).group_id, db_session.get(LedgerEntry, i).name) for i in ids[2:4]] == [
        (None, "一"), (None, "二"),
    ]


@pytest.mark.parametrize("delete_first", [True, False])
def test_delete_dissolve_races_a_survivor_put(pg_engine, db_session, seed, delete_first):
    wallet_id, group_id, doomed_id, survivor_id = _group_of_two(db_session, seed)
    update = EntryUpdateIn(account_id=wallet_id, kind="expense", amount="70", entry_date=DAY)
    delete = lambda db: ews.delete_entry(db, doomed_id)  # noqa: E731
    put = lambda db: ews.update_entry(db, survivor_id, update)  # noqa: E731

    result = race(pg_engine, delete, put) if delete_first else race(pg_engine, put, delete)

    assert result == "committed"
    row = _row(db_session, survivor_id)
    assert (row.amount, row.group_id) == (Decimal("-70"), None)
    assert db_session.get(EntryGroup, group_id) is None and db_session.get(LedgerEntry, doomed_id) is None


def test_two_deletes_of_a_two_member_split_queue(pg_engine, db_session, seed):
    wallet_id, group_id, doomed_id, survivor_id = _group_of_two(db_session, seed)

    result = race(pg_engine, lambda db: ews.delete_entry(db, doomed_id), lambda db: ews.delete_entry(db, survivor_id))

    assert isinstance(result, CodedConflictError) and result.code == RETRY  # the group dissolved under the second
    assert _row(db_session, survivor_id).group_id is None
    assert db_session.get(EntryGroup, group_id) is None
    ews.delete_entry(db_session, survivor_id)  # the client's retry
    db_session.commit()
    assert db_session.scalars(select(LedgerEntry)).all() == []


def test_delete_dissolve_then_group_put_retries(pg_engine, db_session, seed):
    wallet_id, group_id, doomed_id, survivor_id = _group_of_two(db_session, seed)
    body = SplitIn(**_split(
        {"id": survivor_id, "account_id": wallet_id, "kind": "expense", "amount": "70"},
        {"account_id": wallet_id, "kind": "expense", "amount": "5"},
    ))

    result = race(pg_engine, lambda db: ews.delete_entry(db, doomed_id), lambda db: ss.update_split(db, group_id, body))

    assert isinstance(result, CodedConflictError) and result.code == RETRY
    row = _row(db_session, survivor_id)
    assert (row.amount, row.group_id) == (Decimal("-50"), None)


def test_group_put_then_delete_finds_the_member_gone(pg_engine, db_session, seed):
    wallet_id, group_id, doomed_id, survivor_id = _group_of_two(db_session, seed)
    body = SplitIn(**_split(
        {"id": survivor_id, "account_id": wallet_id, "kind": "expense", "amount": "70"},
        {"account_id": wallet_id, "kind": "expense", "amount": "5"},
    ))

    result = race(pg_engine, lambda db: ss.update_split(db, group_id, body), lambda db: ews.delete_entry(db, doomed_id))

    assert isinstance(result, NotFoundError)  # the PUT dropped it first
    db_session.expire_all()
    members = db_session.scalars(select(LedgerEntry).where(LedgerEntry.group_id == group_id).order_by(LedgerEntry.id)).all()
    assert [(m.id == survivor_id, m.amount) for m in members] == [(True, Decimal("-70")), (False, Decimal("-5"))]


@pytest.fixture()
def scheduled_pair(db_session, seed, today):
    """A posted two-line split period (串流組合 #1) on 2026-10-22."""
    today(date(2026, 10, 22))
    card = seed.account("範例卡")
    bundle = seed.definition([seed.line("expense", card, "390"), seed.line("expense", card, "149")], name="串流組合")
    instance = seed.instance(bundle, 1, date(2026, 10, 22))
    first_id, second_id = posting.post_instance(db_session, instance.id, actor="auto").entry_ids
    db_session.commit()
    return instance.id, first_id, second_id


@pytest.mark.parametrize("delete_first", [True, False])
def test_delete_dissolve_races_a_schedule_repost(pg_engine, db_session, scheduled_pair, delete_first):
    instance_id, first_id, second_id = scheduled_pair
    delete = lambda db: ews.delete_entry(db, first_id)  # noqa: E731
    repost = lambda db: schedule_service.repost_instance(db, instance_id, ["390", "149"])  # noqa: E731

    result = race(pg_engine, delete, repost) if delete_first else race(pg_engine, repost, delete)

    assert result == "committed" if delete_first else isinstance(result, NotFoundError)
    db_session.expire_all()
    row = db_session.get(ScheduleInstance, instance_id)
    assert (row.status, row.is_partial, len(row.posted_entry_ids)) == ("posted", False, 2)
    posted = [db_session.get(LedgerEntry, entry_id) for entry_id in row.posted_entry_ids]
    assert len({entry.group_id for entry in posted}) == 1 and posted[0].group_id is not None
    assert db_session.get(LedgerEntry, second_id) is None  # the repost replaced the survivor too
```

In `tests/integration/test_entry_writes.py`, replace lines 601–603:

```python
    def lock_together(db, entry_id, transfer_group_id):
        barrier.wait(timeout=5)
        return original(db, entry_id, transfer_group_id)
```

with:

```python
    def lock_together(db, entry_id, transfer_group_id, also_ids=()):
        barrier.wait(timeout=5)
        return original(db, entry_id, transfer_group_id, also_ids)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/opc/workspace/home-hub-splitapi/services/accounting-service && /home/opc/workspace/home-hub-schedules/services/accounting-service/.venv/bin/python -m pytest tests/integration/test_split_dissolve.py -q`
Expected: FAIL — `test_delete_down_to_one_member_dissolves_the_split` (survivor keeps `group_id`), `test_transfer_legs_in_two_splits_dissolve_both`, `test_scheduled_split_period_keeps_partial_and_reopen_semantics` (group not removed at one member), `test_two_deletes_of_a_two_member_split_queue` and the race tests (old delete leaves the group).

- [ ] **Step 3: Lock the affected set and dissolve**

In `app/services/entry_write_service.py`, replace line 20:

```python
from .errors import ConflictError, NotFoundError, ValidationError  # noqa: F401  (ValidationError re-exported)
```

with:

```python
from .errors import RETRY, CodedConflictError, ConflictError, NotFoundError, ValidationError  # noqa: F401  (ValidationError re-exported)
```

Replace `locked_with_legs` (lines 368–386) with:

```python
def locked_with_legs(db: Session, entry_id: int, transfer_group_id, also_ids: Iterable[int] = ()) -> list[LedgerEntry]:
    """The entry plus, when transfer_group_id is set, every top-level leg of that transfer, plus `also_ids` (the
    other members of the split groups a delete may dissolve), locked with ONE SELECT … FOR UPDATE ordered by
    ascending id, so two writers on different legs of one transfer, or on members of one split, take the row locks
    in the same order. Rows deleted by a transaction this one waited on are skipped (READ COMMITTED)."""
    condition = LedgerEntry.id.in_([entry_id, *also_ids])
    if transfer_group_id is not None:
        condition = or_(
            condition,
            and_(LedgerEntry.transfer_group_id == transfer_group_id, LedgerEntry.parent_entry_id.is_(None)),
        )
    return list(
        db.scalars(
            select(LedgerEntry)
            .where(condition)
            .order_by(LedgerEntry.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
```

Replace `_group_ids_to_lock` and `delete_entry` (the two functions directly below `dissolve_group`) with:

```python
@dataclass(frozen=True)
class AffectedSet:
    """What DELETE /entries/{id} touches (split rework §1.7), read before any group or entry lock and again under
    them: the targets (the entry and its transfer legs), every group of a target, and the top-level members of each
    split group among them (both legs' groups expanded up front, not leg by leg)."""

    target_ids: tuple[int, ...]
    transfer_group_id: object
    group_ids: tuple[int, ...]
    split_members: tuple[tuple[int, tuple[int, ...]], ...]

    def lock_ids(self) -> list[int]:
        ids = set(self.target_ids)
        for _, member_ids in self.split_members:
            ids.update(member_ids)
        return sorted(ids)


def affected_set(db: Session, entry: LedgerEntry) -> AffectedSet:
    targets = {entry.id: entry}
    if entry.transfer_group_id is not None:
        legs = db.scalars(
            select(LedgerEntry).where(
                LedgerEntry.transfer_group_id == entry.transfer_group_id, LedgerEntry.parent_entry_id.is_(None)
            )
        )
        targets.update({leg.id: leg for leg in legs})
    group_ids = sorted({target.group_id for target in targets.values() if target.group_id is not None})
    split_ids = (
        list(
            db.scalars(
                select(EntryGroup.id)
                .where(EntryGroup.id.in_(group_ids), EntryGroup.kind == "split")
                .order_by(EntryGroup.id)
            )
        )
        if group_ids
        else []
    )
    split_members = tuple(
        (
            group_id,
            tuple(
                db.scalars(
                    select(LedgerEntry.id)
                    .where(LedgerEntry.group_id == group_id, LedgerEntry.parent_entry_id.is_(None))
                    .order_by(LedgerEntry.id)
                )
            ),
        )
        for group_id in split_ids
    )
    return AffectedSet(tuple(sorted(targets)), entry.transfer_group_id, tuple(group_ids), split_members)


def delete_entry(db: Session, entry_id: int) -> None:
    """Deleting needs no FX, so the locks are taken straight away, in the shared order (D32, split rework §1.7):
    the period's schedule rows (lock_for_entry_delete) → every affected entry_group row (lock_group, ascending id)
    → the targets, their transfer legs and every member of an affected split group in ONE statement by ascending id
    (locked_with_legs) → nothing else. The affected set is read before the locks and again under them; any
    difference (a member added or dropped by a split PUT, a convert that grouped the entry, a group dissolved by a
    concurrent delete) is a 409 retry; a target already gone is a 404. Today's refusals stay (referenced loan,
    reward rows, cutover lock). After the delete every affected group of any kind left empty is removed, and a
    split group left with exactly one member is dissolved into it (installment / reward groups are not)."""
    # D29: a loan a live schedule repays cannot be deleted; D32/D33: the period's schedule rows are locked first.
    schedule_entry_hooks.assert_not_referenced(db, loan_entry_id=entry_id)
    instance = schedule_entry_hooks.lock_for_entry_delete(db, [entry_id])
    before = affected_set(db, get_entry(db, entry_id))
    for group_id in before.group_ids:
        lock_group(db, group_id)
    locked = locked_with_legs(db, entry_id, before.transfer_group_id, before.lock_ids())
    entry = next((row for row in locked if row.id == entry_id), None)
    if entry is None:
        raise NotFoundError(f"entry {entry_id} not found")
    if affected_set(db, entry) != before:
        raise CodedConflictError(RETRY, f"entry {entry_id} changed concurrently; retry the delete")
    if entry.kind == "reward":
        raise ConflictError("reward entries cannot be deleted in phase 2a")
    by_id = {row.id: row for row in locked}
    for target_id in before.target_ids:
        assert_entry_editable(db, by_id[target_id])
    target_ids = list(before.target_ids)  # read before the cascade expires (and deletes) the rows
    delete_entries_cascade(db, target_ids)
    for group_id in before.group_ids:  # every kind: an emptied group goes (unchanged)
        remaining = db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.group_id == group_id))
        if remaining == 0:
            db.execute(delete(EntryGroup).where(EntryGroup.id == group_id))
    for group_id, _ in before.split_members:  # split only: one member left → dissolve into it
        left = list(
            db.scalars(
                select(LedgerEntry).where(LedgerEntry.group_id == group_id, LedgerEntry.parent_entry_id.is_(None))
            )
        )
        if len(left) == 1:
            group = db.get(EntryGroup, group_id)
            dissolve_group(db, group_id, left[0], name=group.name, merchant=group.merchant, description=group.description)
    if instance is not None:
        schedule_entry_hooks.after_entries_deleted(db, instance, set(target_ids))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/opc/workspace/home-hub-splitapi/services/accounting-service && /home/opc/workspace/home-hub-schedules/services/accounting-service/.venv/bin/python -m pytest tests/integration/test_split_dissolve.py tests/integration/test_splits.py tests/integration/test_entry_writes.py tests/integration/test_schedule_entry_hooks.py tests/integration/test_schedule_lock_order.py tests/integration/test_split_convert.py tests/integration/test_transfers.py -q`
Expected: PASS (the loan period in `test_schedule_lock_order.py` is an installment group: never dissolved; `test_two_deletes_of_one_period_queue` still empties it).

- [ ] **Step 5: Commit**

```bash
cd /home/opc/workspace/home-hub-splitapi
git add services/accounting-service/app/services/entry_write_service.py \
  services/accounting-service/tests/integration/test_split_dissolve.py services/accounting-service/tests/integration/test_entry_writes.py
git commit -m "$(cat <<'EOF'
feat(accounting): dissolve a split left with one member on delete

DELETE /entries/{id} reads the affected set (targets, transfer legs, every
split group of either leg and its members), locks schedule rows -> groups
-> all affected entries in one statement, re-reads under the locks
(409 retry on drift) and dissolves split groups left with one member;
installment groups keep today's count=0 cleanup only.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Error precedence, envelope across the four writes, concurrency

**Files:**
- Test: `services/accounting-service/tests/integration/test_splits.py` (append)
- Modify (only if a test exposes a gap): `services/accounting-service/app/services/split_service.py`

**Interfaces:**
- Consumes: every service from Tasks 1–7; `tests.helpers.race`; `fake_http`, `today` fixtures; `settlement_service.settle(db, entry_id, payload: SettleIn, ...) -> int`.
- Produces: tests only.

- [ ] **Step 1: Write the tests**

Append to `tests/integration/test_splits.py`:

```python
def test_schema_422_comes_before_a_missing_group(client, db_session, seed):
    wallet = seed.account()
    db_session.commit()

    response = client.put("/splits/999999", json=_split(_member(wallet, client_key="x"), _member(wallet, client_key="x")))

    assert response.status_code == 422


def test_a_foreign_member_id_is_404_before_member_validation(client, db_session, seed):
    wallet_id, group_id, [first_id, second_id] = _two_member_split(db_session, seed)
    other = ss.create_split(db_session, SplitIn(**_split(
        {"account_id": wallet_id, "kind": "expense", "amount": "1"}, {"account_id": wallet_id, "kind": "expense", "amount": "2"},
    )))
    db_session.commit()
    foreign_id = ss.member_ids(db_session, other)[0]

    response = client.put(f"/splits/{group_id}", json=_split(
        {"id": foreign_id, "account_id": 999999, "kind": "expense", "amount": "1"}, _keep(second_id),
    ))

    assert response.status_code == 404 and response.json()["message"].startswith("member_not_found")


def test_member_locked_409_comes_before_member_validation(client, db_session, seed):
    wallet = seed.account()
    alan = seed.counterparty("Alan")
    group = seed.group()
    plain = seed.entry(wallet, "-100", group_id=group.id)
    collection = seed.entry(wallet, "200", kind="receivable", counterparty_id=alan.id, is_settlement=True, group_id=group.id)
    db_session.commit()

    response = client.put(f"/splits/{group.id}", json=_split(
        {"id": collection.id, "account_id": wallet.id, "kind": "receivable", "amount": "200", "counterparty_id": alan.id},
        {"id": plain.id, "account_id": wallet.id, "kind": "receivable", "amount": "100"},  # no counterparty: a 422 later
    ))

    assert response.status_code == 409 and response.json()["message"].startswith("member_locked")


def test_a_scheduled_group_is_409_before_an_unknown_member(client, db_session, seed):
    card = seed.account("範例卡")
    group = seed.group(name="串流組合 #1")
    first = seed.entry(card, "-390", group_id=group.id, source="schedule")
    second = seed.entry(card, "-149", group_id=group.id, source="schedule")
    bundle = seed.definition([seed.line("expense", card, "390"), seed.line("expense", card, "149")], name="串流組合")
    instance = seed.instance(bundle, 1, date(2026, 10, 22), status="posted", entries=[first, second])
    db_session.commit()

    response = client.put(f"/splits/{group.id}", json=_split(_keep(999999), _keep(second.id)))

    assert response.status_code == 409
    assert response.json()["message"].startswith("group_scheduled") and str(instance.id) in response.json()["message"]


def test_membership_change_between_read_and_lock_is_409_retry(client, pg_engine, db_session, seed, monkeypatch):
    wallet_id, group_id, [first_id, second_id] = _two_member_split(db_session, seed)
    original = ss._prepare

    def prepare_then_add_a_member(db, members, http_get):
        original(db, members, http_get)
        with sessionmaker(bind=pg_engine)() as other:  # another request adds a member before this one locks
            other.add(LedgerEntry(account_id=wallet_id, kind="expense", amount=Decimal("-1"), currency="TWD",
                                  entry_date=DAY, posted_date=DAY, source="manual", group_id=group_id))
            other.commit()

    monkeypatch.setattr(ss, "_prepare", prepare_then_add_a_member)

    response = client.put(f"/splits/{group_id}", json=_split(_keep(first_id, name="改名"), _keep(second_id)))

    assert response.status_code == 409 and response.json()["message"].startswith("retry")
    assert [m.name for m in _members(db_session, group_id)] == [None, None, None]


def test_convert_cardinality_422_comes_before_a_missing_entry(client, db_session, seed):
    wallet = seed.account()
    db_session.commit()

    response = client.put("/entries/999999/split", json=_split({"id": 999999, "account_id": wallet.id, "kind": "expense", "amount": "1"}))

    assert (response.status_code, response.json()["detail"][0]["loc"]) == (422, ["members"])


def test_put_accepts_an_imported_group_over_fifty_but_never_grows_it(client, db_session, seed):
    wallet = seed.account()
    group = seed.group()
    rows = [seed.entry(wallet, "-1", group_id=group.id) for _ in range(51)]
    db_session.commit()
    group_id, ids = group.id, [row.id for row in rows]

    same = client.put(f"/splits/{group_id}", json=_split(*[_keep(entry_id) for entry_id in ids]))
    grown = client.put(f"/splits/{group_id}", json=_split(*[_keep(entry_id) for entry_id in ids], _member(wallet)))

    assert same.status_code == 200, same.text
    assert (grown.status_code, grown.json()["detail"][0]["loc"]) == (422, ["members"])
    assert len(_members(db_session, group_id)) == 51


def test_all_four_split_writes_answer_the_same_envelope(client, db_session, seed):
    wallet = seed.account()
    anchor = seed.entry(wallet, "-100")
    db_session.commit()
    wallet_id, anchor_id = wallet.id, anchor.id

    created = client.post("/splits", json=_split(_member(wallet, client_key="a"), _member(wallet, amount="50", client_key="b")))
    group_id = created.json()["group_id"]
    first_id, second_id = created.json()["member_ids"]
    upserted = client.put(f"/splits/{group_id}", json=_split(
        _keep(first_id, client_key="a"), _keep(second_id), _member(wallet, amount="5", client_key="c"),
    ))
    dissolved = client.put(f"/splits/{group_id}", json=_split(_keep(first_id, client_key="a")))
    converted = client.put(f"/entries/{anchor_id}/split", json=_split(
        {"id": anchor_id, "account_id": wallet_id, "kind": "expense", "amount": "100", "client_key": "x"},
        _member(wallet, amount="1"),
    ))

    for response, status in ((created, 201), (upserted, 200), (dissolved, 200), (converted, 200)):
        assert response.status_code == status, response.text
        body = response.json()
        assert set(body) == {"group_id", "member_ids", "members"}
        assert [member["id"] for member in body["members"]] == body["member_ids"]
    assert [m["client_key"] for m in upserted.json()["members"]] == ["a", None, "c"]
    assert upserted.json()["member_ids"][:2] == [first_id, second_id]
    assert dissolved.json() == {"group_id": None, "member_ids": [first_id], "members": [{"id": first_id, "client_key": "a"}]}
    assert converted.json()["member_ids"][0] == anchor_id and converted.json()["group_id"] is not None


def test_cold_fx_cache_put_takes_its_locks_after_the_fx_commit(pg_engine, db_session, seed, fake_http, today):
    # Review Focus 4: the first PUT fetches and commits a rate (fx cache) and only then locks; race() proves it still
    # holds the group lock afterwards (the second PUT must wait), which a lock taken before the commit would not.
    today(date(2026, 10, 6))
    wallet_id, group_id, [first_id, second_id] = _two_member_split(db_session, seed)
    http = fake_http({"currency-api@2026-09-01/v1/currencies/jpy.json": (200, {"date": "2026-09-01", "jpy": {"twd": 0.2}})})
    with_fx = _split(_keep(first_id), _keep(second_id),
                     {"account_id": wallet_id, "kind": "expense", "amount": None, "original_amount": "1000", "original_currency": "JPY"})
    rename = _split(_keep(first_id, name="改名"), _keep(second_id))

    result = race(
        pg_engine,
        lambda db: ss.update_split(db, group_id, SplitIn(**with_fx), http_get=http),
        lambda db: ss.update_split(db, group_id, SplitIn(**rename)),
    )

    assert len(http.calls) == 1
    assert isinstance(result, CodedConflictError) and result.code == RETRY  # a member was added under the second PUT
    members = _members(db_session, group_id)
    assert [(m.amount, m.fx_source, m.name) for m in members] == [
        (Decimal("-100"), None, None), (Decimal("-50"), None, None), (Decimal("-200"), "fx_api", None),
    ]


def _split_with_a_loan(db_session, seed) -> tuple[int, int, int, int, int]:
    wallet = seed.account("錢包", opening="1000")
    alan = seed.counterparty("Alan")
    db_session.commit()
    group_id = ss.create_split(db_session, SplitIn(**_split(
        _member(wallet, kind="receivable", amount="300", counterparty_id=alan.id), _member(wallet, amount="50"),
    )))
    db_session.commit()
    lent_id, meal_id = ss.member_ids(db_session, group_id)
    return wallet.id, alan.id, group_id, lent_id, meal_id


def test_split_put_then_settle_keeps_the_settlement_on_the_same_member(pg_engine, db_session, seed):
    wallet_id, alan_id, group_id, lent_id, meal_id = _split_with_a_loan(db_session, seed)
    body = _split(
        {"id": lent_id, "account_id": wallet_id, "kind": "receivable", "amount": "300", "counterparty_id": alan_id, "name": "代墊"},
        {"id": meal_id, "account_id": wallet_id, "kind": "expense", "amount": "60"},
    )
    settle = SettleIn(account_id=wallet_id, amount="100", entry_date=DAY)

    result = race(pg_engine, lambda db: ss.update_split(db, group_id, SplitIn(**body)), lambda db: st.settle(db, lent_id, settle))

    assert result == "committed"
    db_session.expire_all()
    [collection] = db_session.scalars(select(LedgerEntry).where(LedgerEntry.settles_entry_id == lent_id)).all()
    lent = db_session.get(LedgerEntry, lent_id)
    assert (collection.amount, lent.name, lent.amount) == (Decimal("100"), "代墊", Decimal("-300"))


def test_settle_then_split_put_of_that_member_retries(pg_engine, db_session, seed):
    wallet_id, alan_id, group_id, lent_id, meal_id = _split_with_a_loan(db_session, seed)
    body = _split(
        {"id": lent_id, "account_id": wallet_id, "kind": "receivable", "amount": "350", "counterparty_id": alan_id},
        _keep(meal_id),
    )
    settle = SettleIn(account_id=wallet_id, amount="100", entry_date=DAY)

    result = race(pg_engine, lambda db: st.settle(db, lent_id, settle), lambda db: ss.update_split(db, group_id, SplitIn(**body)))

    assert isinstance(result, CodedConflictError) and result.code == RETRY  # the member became protected under the PUT
    db_session.expire_all()
    assert db_session.get(LedgerEntry, lent_id).amount == Decimal("-300")
```

- [ ] **Step 2: Run the tests**

Run: `cd /home/opc/workspace/home-hub-splitapi/services/accounting-service && /home/opc/workspace/home-hub-schedules/services/accounting-service/.venv/bin/python -m pytest tests/integration/test_splits.py -q`
Expected: PASS — these pin behaviour built in Tasks 1–7. If one fails, the failure names a precedence or lock-order gap: fix it in `split_service.py` at the phase the test names (never by reordering a test's expectation), then re-run. Deadlocks show up as `OperationalError`/`DeadlockDetected` or a `race()` timeout assertion.

- [ ] **Step 3: Run the whole split surface**

Run: `cd /home/opc/workspace/home-hub-splitapi/services/accounting-service && /home/opc/workspace/home-hub-schedules/services/accounting-service/.venv/bin/python -m pytest tests/integration/test_splits.py tests/integration/test_split_convert.py tests/integration/test_split_dissolve.py tests/integration/test_split_protection.py tests/integration/test_split_compare.py tests/unit/test_split_schemas.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
cd /home/opc/workspace/home-hub-splitapi
git add services/accounting-service/tests/integration/test_splits.py services/accounting-service/app/services/split_service.py
git commit -m "$(cat <<'EOF'
test(accounting): split error precedence, envelope and races

Schema 422 before 404, member 404 before member validation, member_locked
before validation, group_scheduled before membership, post-lock retry;
one envelope for create/upsert/dissolve/convert; cold-cache FX locks
after the cache commit; PUT/settle in both orders.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---
### Task 9: OpenSpec — record the contract in `accounting-ledger`

Repo convention (checked: `openspec/changes/archive/2026-10-05-add-accounting-schedules/`, archived by `e2b479b`): a change folder with `proposal.md`, `tasks.md` and per-capability delta specs, validated with `openspec validate --strict`, then `openspec archive` merges the deltas into `openspec/specs/`. The spec's §3 requires `openspec/specs/accounting-ledger` to be updated **in PR-B**, so this PR creates the change and archives it in the same branch (the schedules change was archived in a follow-up commit; here the follow-up is folded in so the main spec and the code merge together).

**Files:**
- Create: `openspec/changes/rework-split-entries/proposal.md`
- Create: `openspec/changes/rework-split-entries/tasks.md`
- Create: `openspec/changes/rework-split-entries/specs/accounting-ledger/spec.md`
- Modify (by `openspec archive`): `openspec/specs/accounting-ledger/spec.md`; the change moves to `openspec/changes/archive/2026-10-06-rework-split-entries/` (the CLI stamps the run date; if it is not 2026-10-06, keep what it wrote)

**Interfaces:**
- Consumes: the behaviour of Tasks 1–8 (error codes, envelope, phases, lock order).
- Produces: requirements `Split endpoint` (modified), `Protected split members`, `Split upsert by member id`, `Dissolving a split`, `Converting an entry into a split`, `Split write phases, errors and lock order`, `Deleting a member dissolves a one-member split` (added).

- [ ] **Step 1: Write the proposal**

Create `openspec/changes/rework-split-entries/proposal.md`:

```markdown
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
```

- [ ] **Step 2: Write tasks.md**

Create `openspec/changes/rework-split-entries/tasks.md`:

```markdown
## 1. Backend (PR-B)

- [x] 1.1 Schemas: member ids, keep form, client keys, envelope (plan Task 1)
- [x] 1.2 Protected members: predicate and read flag (plan Task 2)
- [x] 1.3 Canonical member comparison (plan Task 3)
- [x] 1.4 Upsert by member id (plan Task 4)
- [x] 1.5 Dissolve through a one-member PUT (plan Task 5)
- [x] 1.6 Convert an entry into a split (plan Task 6)
- [x] 1.7 DELETE auto-dissolve with the affected-set lock order (plan Task 7)
- [x] 1.8 Error precedence, envelope and concurrency tests (plan Task 8)

Plan: `docs/superpowers/plans/2026-10-06-split-upsert-api.md`.
```

- [ ] **Step 3: Write the ledger delta**

Create `openspec/changes/rework-split-entries/specs/accounting-ledger/spec.md`:

```markdown
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

`PUT /api/accounting/splits/{group_id}` SHALL accept 1 to max(50, the group's current member count) members, each either a full member (an entry payload, optionally with `id`) or a keep member `{id, keep: true, name?, project_id?, tags?, description?, client_key?}` that SHALL refuse every other field (HTTP 422). Every `id` SHALL belong to the group (else HTTP 404 with a message starting `member_not_found`). The server SHALL partition the members into keep_full (full with `id`), keep_meta (keep form), new (no `id`) and drop (current members absent from the body). A protected member sent as a full member or dropped SHALL be refused with HTTP 409 `member_locked` naming the reason. Each keep_full member SHALL be compared with its stored row on unsigned inputs (an online-FX payload with `amount` and `fx_rate` null equals a stored row with the same `original_amount` / `original_currency` and `fx_source = 'fx_api'`): when kind, amount and FX inputs, account, category, counterparty, fee, discount, rule ids, invoice and dates are all equal and name, merchant, project, tags and description too, the member SHALL be left untouched; when only the latter differ, only they SHALL be written (no FX request, no child or rule-link rebuild); otherwise the member SHALL be updated in place like `PUT /api/accounting/entries/{id}`, keeping its id, with its current rule links accepted even if the rule was disabled or expired since. keep_meta members SHALL change only the fields sent (`null` and `[]` included). New members SHALL be inserted into the group; drop members SHALL be deleted with their children. The group's `name`, `merchant` and `description` SHALL be set from the body. Reward ledger rows SHALL never be touched. Category defaults SHALL be remembered for new and financially changed members only.

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
```

- [ ] **Step 4: Validate the change**

Run: `cd /home/opc/workspace/home-hub-splitapi && openspec validate rework-split-entries --strict`
Expected: `Change 'rework-split-entries' is valid`. If strict validation reports a requirement without `SHALL` in its first paragraph or without a scenario, fix the delta text (not the code) and re-run.

- [ ] **Step 5: Archive into the main spec and re-validate**

Run:

```bash
cd /home/opc/workspace/home-hub-splitapi
openspec archive rework-split-entries --yes
openspec validate --specs --strict
grep -n "Requirement: Split upsert by member id\|Requirement: Converting an entry into a split\|Requirement: Deleting a member dissolves" openspec/specs/accounting-ledger/spec.md
```

Expected: the archive reports the `accounting-ledger` deltas applied (1 modified, 6 added); `validate --specs --strict` passes for every spec; the grep prints three lines. `openspec/specs/accounting-schedules/spec.md` is unchanged (`git diff --stat -- openspec/specs/accounting-schedules` prints nothing).

- [ ] **Step 6: Commit**

```bash
cd /home/opc/workspace/home-hub-splitapi
git add openspec/
git commit -m "$(cat <<'EOF'
docs(openspec): split upsert, dissolve and convert in accounting-ledger

Adds and archives rework-split-entries: the split endpoint becomes an
upsert by member id with protected members, dissolve, convert, the
shared envelope, error codes and the delete auto-dissolve lock order.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Verification and pull request

**Files:**
- None modified (verification); PR body written to the scratch file `/tmp/pr-b-body.md` (not committed).

**Interfaces:**
- Consumes: the branch after Tasks 1–9.
- Produces: an open PR `feat/split-upsert-api` → `main`.

- [ ] **Step 1: Run the whole accounting-service suite**

Run: `cd /home/opc/workspace/home-hub-splitapi/services/accounting-service && /home/opc/workspace/home-hub-schedules/services/accounting-service/.venv/bin/python -m pytest -q`
Expected: every test passes (unit + Postgres integration). A failure outside the split/entry files means an accepted contract moved: stop and fix the code, never the accepted test, unless the test is one of those listed under "Existing tests whose expectations change".

- [ ] **Step 2: Check the scope**

Run:

```bash
cd /home/opc/workspace/home-hub-splitapi
git diff --stat main...HEAD -- frontend | tail -1
git diff --stat main...HEAD -- services/accounting-service/alembic | tail -1
git diff --stat main...HEAD -- openspec/specs/accounting-schedules | tail -1
git diff --name-only main...HEAD
```

Expected: the first three print nothing (no frontend, migration or schedules-spec change); the last lists only files from the File Map plus the archived change folder.

- [ ] **Step 3: Placeholder and leftover scan**

Run: `cd /home/opc/workspace/home-hub-splitapi && git diff main...HEAD -- services | grep -nE "TODO|FIXME|XXX|breakpoint\(|print\(" || echo clean`
Expected: `clean`.

- [ ] **Step 4: Push and open the PR**

Write `/tmp/pr-b-body.md`:

```markdown
## Summary

Backend half (PR-B) of the split (多類別) entry rework — design `docs/superpowers/specs/2026-10-06-split-entry-rework-design.md` §1, plan `docs/superpowers/plans/2026-10-06-split-upsert-api.md`.

- `PUT /splits/{gid}` upserts by member id: keep_full (in place; skipped when fully unchanged, metadata-only without FX when financially unchanged), keep_meta (`{id, keep: true}`), new, drop.
- Protected members (settlements incl. orphans, refunds incl. orphans, transfer legs, fee/discount/reward/interest/balance_adjustment, settled or refunded originals, loans a live schedule references) only take the keep form → 409 `member_locked`; `GET /entries/{id}.group_members[]` gains `protected` / `protected_reason`.
- One-member PUT dissolves the split (payload parent values fill the survivor's blank fields); `DELETE /entries/{id}` dissolves a split left with one member (installment groups never).
- New `PUT /entries/{id}/split` converts a single entry keeping its id.
- One envelope for all four writes: `{group_id | null, member_ids, members: [{id, client_key}]}` — **`group_id` is now nullable**.
- Phases: unlocked classification → prepare/FX (may commit the FX cache) → locks (groups ↑ → entries ↑ in one statement → defaults) → re-check (409 `retry`) → write. DELETE locks its whole affected set (both transfer legs' split groups and their members) up front.
- OpenSpec: `rework-split-entries` added and archived into `openspec/specs/accounting-ledger`.

No migration, no frontend change. Old clients keep working where they sent full replacement bodies of ≥ 2 members without ids (those become drop + insert); one-member replacement bodies now answer 422 `members.0.id`, and groups with protected members now answer 409 `member_locked` instead of 422 `members`.

## Review focus

1. Old split, one amount changed → each member keeps its date/time (`test_old_split_keeps_each_member_date_when_one_amount_changes`).
2. Convert with an invalid second member / FX failure → anchor unchanged, no group (`test_invalid_second_member_leaves_the_anchor_alone`, `test_fx_failure_leaves_the_anchor_alone`).
3. Protected member as keep + another dropped → 200; as full → 409, nothing written; by real kinds (`test_protected_member_takes_metadata_only[*]`, `test_group_members_carry_the_protected_flag[*]`).
4. No lock across the FX commit; D32 order on every path (`test_cold_fx_cache_put_takes_its_locks_after_the_fx_commit`, `test_transfer_legs_in_two_splits_dissolve_both`, the DELETE races in `test_split_dissolve.py`).
5. Canonical comparison: no FX request for an untouched online-FX member; fully unchanged → no-op (`test_online_fx_echo_is_unchanged_only_against_an_online_row`, `test_metadata_only_change_on_an_online_fx_member_makes_no_fx_request`, `test_fully_unchanged_payload_is_a_no_op`).

## Test plan

- [x] `pytest -q` in `services/accounting-service` (unit + Postgres integration)
- [x] `openspec validate --specs --strict`
- [x] no diff under `frontend/`, `alembic/`, `openspec/specs/accounting-schedules`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

Run:

```bash
cd /home/opc/workspace/home-hub-splitapi
git push -u origin feat/split-upsert-api
gh pr create --base main --head feat/split-upsert-api \
  --title "feat(accounting): split upsert, dissolve and convert API (PR-B)" --body-file /tmp/pr-b-body.md
```

Expected: the PR URL is printed. Report it; the frontend PR-6 (Multica via lead-astra) targets this API after merge.

---

## Self-Review

**Spec §1 coverage → tasks**

| Spec | Where |
|---|---|
| §1.1 storage unchanged, legacy member merchant verbatim | no migration (Global Constraints); `member_payload` never fills `merchant` from the group, `_apply_meta` writes the sent value; Task 4 RF1 test keeps `舊商家`; no parent invoice (none added) |
| §1.2 protected predicate, read flag, reason codes | Task 2 (`split_protection`, `GroupMemberOut`), used by Tasks 4 (guard + re-check) and 6 (anchor refusal) |
| §1.3 `SplitMemberIn.id/client_key`, `SplitKeepIn`, discriminated members, max 50 / max(50, current), duplicates, POST/PUT/convert cardinality, explicit fields | Task 1 (schema, POST), Task 4 (`_check_put_cardinality`), Task 6 (`_check_convert_cardinality`); inheritance kept in `member_payload` |
| §1.4 five phases, canonical comparison, FX-cache commit before locks, retry | Task 3 (comparison), Task 4 (`update_split` phases, `_revalidate`), Task 6 (convert phases), Task 8 (cold-cache race, membership retry) |
| §1.5 upsert partition, protected rules, drop, keep_full variants, keep_meta, new, dates only on full members, defaults, scheduled guard, referenced loan | Task 4 |
| §1.6 envelope, dissolve, convert, refusals, retry/concurrency, error precedence, DELETE /splits unchanged | Tasks 1, 5, 6, 8 (`delete_split` untouched except its docstring) |
| §1.7 DELETE affected set, lock order, re-read, refusals, split-only dissolve, count=0 cleanup for every kind | Task 7 |
| §1.8 test list | Upsert → Task 4 + 8; Convert → Task 6; Dissolve → Task 5; DELETE → Task 7; POST → Task 1; protected by real kind → Tasks 2 + 4 (12 shapes); phases/precedence → Task 8; lock order → Task 7; mixed protected/editable dates → Tasks 4 + 5; metadata-only → Task 4; installment → Task 7 |
| §3 openspec ledger update in PR-B | Task 9 |

Gaps found while drafting and fixed in the plan: (1) `test_edit_lock.py` route inventory and its one-member `put_split` body would break → Task 4/6 edits; (2) `test_entry_writes.py`'s barrier spy monkeypatches `locked_with_legs` with a 3-argument function → Task 7 widens it; (3) the accepted D29 scenario PUTs a scheduled **installment** group and expects 409 — a "404 not a split" check first would break it → `_readable_split` checks `group_scheduled` before the kind; (4) `test_schedule_entry_hooks.py` sends one-member id-less bodies → group-state checks run before the dissolve cardinality, so it stays untouched; (5) the accepted ledger contract fixes `locked_until_cutover` as the cutover-lock message → kept instead of inventing `group_locked`; (6) `fx_source='online'` does not exist in the enum → the online source is `fx_api`; (7) `delete_entries_cascade` expires the session → the upsert re-reads rows with `db.get` after the drop; (8) Task 1 tightens POST cardinality, which the two old one-member race fixtures violate → deselected in Task 1 and rewritten in Task 4 rather than patched twice.

**Placeholder scan:** no TBD/TODO/"similar to"; every step has the exact code or command. The only conditional instruction is Task 8 Step 2 ("if one fails…"), which names where the fix goes; Task 9 Step 5 accepts the CLI's archive date stamp.

**Signature consistency:** `update_split(db, group_id, payload, *, http_get=None) -> SplitResult` (Task 4, used by router and Tasks 7/8 tests); `create_split(...) -> int` kept for existing callers (`test_settlements.py`), `create_split_result(...) -> SplitResult` for the router; `convert_to_split(db, entry_id, payload, *, http_get=None) -> SplitResult` (Task 6, router + tests); `dissolve_group(db, group_id, survivor, *, name, merchant, description)` (Task 5, used by Tasks 5 and 7); `locked_with_legs(db, entry_id, transfer_group_id, also_ids=())` (Task 7; `transfer_service` keeps the 3-argument call); `protected_reason(db, entry)` / `protected_reasons(db, entries) -> dict[int, str | None]` (Task 2; Tasks 4/6); `stored_signature(db, entry)`, `payload_signature(payload, account_currency)`, `classify(stored, sent)` (Task 3; Task 4); `CodedConflictError(code, detail=None).code` (Task 1; Tasks 4/6/7 and tests); `_Member(index, item, entry_id, action, payload, stored, change, prepared)` (Task 1, typed `MemberSignature` from Task 4).

**Review focus pinned:** RF1 → Task 4; RF2 → Task 6; RF3 → Tasks 2 + 4; RF4 (locks after FX, D32 order) → Tasks 7 + 8; RF5 (canonical comparison) → Tasks 3 + 4.

**Spec ambiguities resolved (owner may overrule before execution):**

1. Error precedence: the spec's "schema/cardinality 422 → 404 → 409" conflicts with §1.4 step 1's "group exists / editable / not scheduled … cardinality" and with the accepted D29 scenario. Chosen: Pydantic 422 → 404 group → 409 `group_scheduled` → 404 not-a-split → 409 cutover → 422 DB-dependent cardinality (max(50,current), one-member needs `id`) → 404 `member_not_found` → 409 `member_locked` → 422 members → 409 `retry`. POST and convert cardinality need no DB and come first.
2. `group_locked` / convert "import-locked → 409 `import_running`": the accepted ledger requirement fixes the cutover-lock message as `locked_until_cutover`, and `import_running` already means "an import holds the advisory key". Both keep their existing meaning; no new code is invented for the cutover lock.
3. `fx_source='online'` → the enum value `fx_api`.
4. §1.5 "drop: `assert_not_referenced` then cascade" and "referenced loan in keep_full may not change kind/currency/account": a referenced loan is protected (§1.2), so both cases are refused earlier as 409 `member_locked` (reason `scheduled_loan`); tests pin both.
5. 409 codes travel in the shared error envelope's `message` as `<code>` or `<code>: <detail>` (the shared handler stringifies `detail`), and `member_not_found` is the 404 message prefix.
6. Convert always prepares the anchor (spec: "the convert anchor with its attached links"); no unchanged-skip for the anchor.
7. OpenSpec: a change folder per repo convention, archived inside this PR because §3 requires the main spec updated in PR-B.
8. A keep member's `tags: null` is stored as `[]` (the column is NOT NULL); `merchant` is not a keep field (spec list), so a protected member's merchant only changes through a dissolve copy.
