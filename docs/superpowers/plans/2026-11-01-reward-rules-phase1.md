# Reward Rules Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner (SPA) and Hermes (API) create, edit, reorder, disable and delete reward rules of any account, see every rule across cards with its state and what it produced, and make the MOZE backup import stop writing rules.

**Architecture:** The existing `reward_rule` table and `RewardRuleOut` stay the contract (no migration). A new `reward_rule_service.py` owns the writes and the overview query; rule routes hang off the `/accounts` router (write scope) plus one `GET /reward-rules` router. The SPA adds a 回饋規則 block + editor sheet to account settings, a whole-page overview at `accounting/reward-rules`, and typed service methods. The backup import's rule pass becomes a read-only resolver by `moze_id`.

**Tech Stack:** FastAPI + SQLAlchemy 2 + pydantic v2 (`services/accounting-service`), pytest (`tests/unit`, `tests/integration` on a disposable Postgres); Angular 21 standalone components + signals, Vitest (`frontend`).

**Spec:** `docs/superpowers/specs/2026-10-31-reward-rules-design.md` (rev 2, `0e1430ce`). Sections cited below as §n.

**Plan rev 2** (2026-11-01) folds the Multica review AGENT-378 (dev-grok, CHANGES-REQUESTED at `845e66c0`): Task 5 covers every test that relied on the import creating rules (`test_backup_replace_and_report.py`, `test_moze_replace_ledger.py`, the split-purchase reward test) and keeps `ROUNDING_MAP` / `POSTING_MAP`; `?rule=` clearing and the overview filter navigate with `relativeTo: this.route`; `fixed_amount` / `total_cap` use `Money`; overlap tests add the both-null pair and the no-op PUT; the stale `.rule-item` assertion in `account-settings.spec.ts` is rewritten; `accountsChanged` is the signal's real name; the 409 body note names `message`.

## Global Constraints

- No schema change: alembic head stays `c7a2e9f4b1d8`; `tests/integration/test_migration.py` keeps passing unchanged (§3).
- Rule CRUD lives under the `/accounts` router, whose dependency is `method_scope()` (GET=`read`, writes=`write`); never `admin` (§3). `GET /reward-rules` needs `read`.
- Error style: `service_errors()` mapping — 404 `account not found` / `rule not found`; 409 `CodedConflictError(code, detail)` → `HTTPException.detail` = `"<code>: <detail>"`, which `shared_lib/errors.py` serialises as JSON `{"code": 409, "message": "<code>: <detail>", "trace_id": …}` — tests and the SPA read `message`; 422 = pydantic or `ValidationError(field, message)` → `{"detail": [{"loc": [field], …}]}` (§5.3).
- `moze_id` is never writable through the API; `account_id` of a rule never changes (§5.1, §5.3).
- Backend fixtures and frontend test literals are fictional (card names like 華航卡 / 玉山 Only from existing tests); no owner data anywhere (§2 anti-goals).
- SPA copy (verbatim, §5.1 / §6): labels 名稱 · 方式 · 比例 % · 固定金額 · 入帳 · 帳單結帳後 · 交易後 · 手動 · 每期上限 · 單筆取整 · 總額取整 · 與…共用上限 · 基本回饋 · 入帳帳戶 · 專案 · 期間 · 啟用 · 說明; rounding labels 不取整 · 四捨五入 · 無條件捨去 · 無條件進位; states 啟用中 · 已停用 · 已到期 · 尚未開始; badges 基本 · 來自 MOZE; buttons 新增規則 · 編輯 · 上移 · 下移 · 刪除 · 停用 · 全部卡片; messages 儲存帳戶後即可新增回饋規則 · 尚無規則 · 尚無回饋規則 · 此規則已有 N 筆交易 / M 筆回饋引用，改為停用？ · N 條規則啟用中但已到期，記帳時不會出現.
- `ruleLabel` text stays what the entry form shows today (`entry-draft.ts:171`): 名稱 (3%) / 名稱 ($50).
- Angular `anyComponentStyle` budget 16 kB warn / 20 kB error (§3); every new component has a spec beside it.
- Backup import: `POST /imports/moze-backup` never creates, updates or deletes a `reward_rule` row after this plan (§5.2).
- Commit messages in English, conventional style; each task commits on its own.

## Review Focus

Inputs the spec implies but no task's tests exercised at first draft; each line now has a test in the task named.

1. PUT that disables a rule which a *future-dated* purchase is linked to — the link must survive and `check_rules` still accepts the existing attachment on edit (Task 2 `test_disabling_a_linked_rule_keeps_the_link`).
2. Basic overlap with open-ended bounds: an enabled basic rule with `ends_on = null` must clash with any later-starting basic rule; two basic rules where one is disabled must not clash (Task 2 `test_basic_overlap_open_ended_and_disabled_neighbour`).
3. Reorder with a stale list: the SPA sends the ids it has while Hermes just created a rule → 422 naming `ids`, nothing re-ordered (Task 3 `test_reorder_rejects_missing_or_extra_ids`).
4. Overview `rewards_total` when one rule's reward rows sit in two currencies, and `linked_entries_12m` when a linked entry is dated exactly 12 months ago today (Task 4 `test_overview_stats_per_currency_and_12m_boundary`).
5. Backup re-import of a rule record whose MOZE account is missing from the backup, and of a reward record pointing at a rule HomeHub never had — neither may touch `reward_rule`, both are counted (Task 5 `test_unknown_rule_records_are_counted_not_created`).

---

## File structure

Backend (`services/accounting-service/`)
- Modify `app/schemas/writes.py` — `RewardRuleIn`, `ShareCapWith`.
- Modify `app/schemas/ledger.py` — `RewardRuleState`, `RewardTotalOut`, `RewardRuleStatsOut`, `RewardRuleOverviewRuleOut`, `RewardRuleOverviewAccountOut`, `RewardRuleCountsOut`, `RewardRuleOverviewOut`.
- Create `app/services/reward_rule_service.py` — `create`, `update`, `reorder`, `delete`, `rule_state`, `overview`.
- Modify `app/routers/accounts.py` — POST / PUT / PUT order / DELETE under `/{account_id}/reward-rules`.
- Create `app/routers/reward_rules.py` — `GET /reward-rules`; register in `app/main.py`.
- Modify `app/services/moze_backup_import_service.py` — `_upsert_rules` → `_resolve_rules`; report counters.
- Modify `README.md` — API list + backup paragraph.
- Tests: create `tests/unit/test_reward_rule_schema.py`, `tests/integration/test_reward_rules_api.py`, `tests/integration/test_reward_rules_overview.py`; modify `tests/integration/test_backup_settings_import.py`, `tests/integration/test_backup_replace_and_report.py` (if it asserts the report keys).

Frontend (`frontend/src/app/`)
- Modify `models/accounting.model.ts` — `RewardRuleInput`, `ShareCapWith`, `RewardRuleState`, `RewardTotal`, `RewardRuleStats`, `RewardRuleOverviewRule`, `RewardRuleOverviewAccount`, `RewardRuleCounts`, `RewardRuleOverview`, `ImportReport` rule keys.
- Modify `services/accounting.service.ts` — five methods.
- Create `components/accounting/reward-rule-text.ts` (+ `.spec.ts`) — pure helpers shared by block, sheet and overview.
- Create `components/accounting/reward-rule-sheet/` — editor sheet component (`.ts`, `.html`, `.spec.ts`; styles from `../sheet.scss` + own small file).
- Modify `components/accounting/account-settings/` — replace the read-only `rules-row` with the block.
- Create `components/accounting/reward-rules/` — overview page (`.ts`, `.html`, `.scss`, `.spec.ts`).
- Modify `components/accounting/accounting-pages.ts`, `app.routes.ts`, `components/accounting/accounting-settings/accounting-settings.html` — route + entry link.

Outside the repo (rollout, Appendix A): `~/.hermes/skills/productivity/homehub-api/{SKILL.md,references/accounting-api.md}`, Caddy `@hub_spa` regexp.

---

### Task 1: `RewardRuleIn` schema + overview output schemas

**Files:**
- Modify: `services/accounting-service/app/schemas/writes.py` (after `ProjectIn`, ~L251)
- Modify: `services/accounting-service/app/schemas/ledger.py` (after `RewardRuleOut`, L42)
- Test: `services/accounting-service/tests/unit/test_reward_rule_schema.py`

**Interfaces:**
- Produces `RewardRuleIn(BaseModel)` in `writes.py`, `model_config = ConfigDict(extra="forbid")`. Single-field shape and range checks are pydantic `Field` constraints so every 422 carries the field in `loc` (the SPA's `fieldErrors()` keys on it):
  `name: Annotated[str, BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v), Field(min_length=1, max_length=128)]`,
  `method: Literal["percent", "fixed"]`, `rate: Annotated[Decimal, Field(gt=0, le=100, decimal_places=4)] | None = None`, `fixed_amount: Money | None = None` (`Money` = `Field(gt=0)` + `_four_places`, `writes.py:15–26`, so 5 decimals or ≥ 1e16 is a 422 on the field, not a DB error),
  `window: Literal["statement_cycle"] = "statement_cycle"`, `posting: Literal["after_window", "after_transaction", "manual"]`,
  `delay_days: int = Field(default=0, ge=0, le=365)`, `post_month_offset: int = Field(default=0, ge=0, le=2)`, `post_day: int = Field(default=1, ge=1, le=31)`,
  `txn_rounding: RoundingMode = "keep"`, `total_rounding: RoundingMode = "keep"`, `total_cap: Money | None = None`,
  `is_basic: bool = False`, `reward_account_id: Int32 | None = None`, `reward_project_id: Int32 | None = None`,
  `starts_on: date | None = None`, `ends_on: date | None = None`, `is_enabled: bool = True`,
  `description: Annotated[str, Field(max_length=2000)] | None = None`, `sort_order: Int32 | None = None`,
  `share_cap_with: ShareCapWith = None`.
- Produces `ShareCapWith = Literal["none"] | Int32 | None` (type alias in `writes.py`).
- `model_validator(mode="after")` on `RewardRuleIn` **normalises only** (never raises): `after_window` → `delay_days = 0`; `after_transaction` → `post_month_offset = 0`, `post_day = 1`; `manual` → all three defaults. Cross-field rules (percent needs `rate` and null `fixed_amount`; fixed needs `fixed_amount` and null `rate`; `starts_on <= ends_on`) are raised by the service in Task 2 as `ValidationError(field, message)` so `loc` names the field — a `model_validator` cannot set `loc` (see `SplitIn._distinct_ids_and_client_keys`, `writes.py:151`, whose errors land under `body`).
- Produces in `ledger.py`:
  ```python
  RewardRuleState = Literal["active", "disabled", "expired", "upcoming"]
  class RewardTotalOut(BaseModel): currency: str; amount: Decimal
  class RewardRuleStatsOut(BaseModel):
      linked_entries: int; linked_entries_12m: int; rewards_count: int
      rewards_total: list[RewardTotalOut]; rewards_last_date: date | None
  class RewardRuleOverviewRuleOut(RewardRuleOut): state: RewardRuleState; stats: RewardRuleStatsOut
  class RewardRuleOverviewAccountOut(BaseModel):
      account_id: int; account_name: str; is_credit: bool; is_archived: bool
      rules: list[RewardRuleOverviewRuleOut]
  class RewardRuleCountsOut(BaseModel): active: int; disabled: int; expired: int; upcoming: int; enabled_expired: int
  class RewardRuleOverviewOut(BaseModel): accounts: list[RewardRuleOverviewAccountOut]; counts: RewardRuleCountsOut
  ```

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_reward_rule_schema.py
import pytest
from decimal import Decimal
from pydantic import ValidationError
from app.schemas.writes import RewardRuleIn

def _body(**fields):
    body = {"name": " 國內 ", "method": "percent", "rate": "1.5", "posting": "after_window",
            "post_month_offset": 1, "post_day": 15}
    body.update(fields)
    return body

def _fields(exc: ValidationError) -> set[str]:
    return {str(e["loc"][-1]) for e in exc.errors()}

def test_percent_rule_parses_and_strips_name():
    rule = RewardRuleIn(**_body())
    assert (rule.name, rule.rate, rule.fixed_amount, rule.delay_days) == ("國內", Decimal("1.5"), None, 0)
    assert rule.share_cap_with is None and rule.sort_order is None

@pytest.mark.parametrize("fields,field", [
    ({"rate": "0"}, "rate"), ({"rate": "100.0001"}, "rate"), ({"rate": "1.12345"}, "rate"),
    ({"method": "fixed", "rate": None, "fixed_amount": "0"}, "fixed_amount"),
    ({"method": "fixed", "rate": None, "fixed_amount": "1.00001"}, "fixed_amount"),
    ({"total_cap": "0"}, "total_cap"), ({"total_cap": "10000000000000000"}, "total_cap"),
    ({"name": "   "}, "name"), ({"description": "x" * 2001}, "description"), ({"delay_days": 366}, "delay_days"),
    ({"post_month_offset": 3}, "post_month_offset"), ({"post_day": 0}, "post_day"), ({"window": "monthly"}, "window"),
    ({"moze_id": "B-1"}, "moze_id"),
])
def test_rejections_name_the_field(fields, field):
    with pytest.raises(ValidationError) as exc:
        RewardRuleIn(**_body(**fields))
    assert field in _fields(exc.value)

def test_posting_resets_dependent_fields():
    after_txn = RewardRuleIn(**_body(posting="after_transaction", delay_days=3))
    manual = RewardRuleIn(**_body(posting="manual", delay_days=3))
    after_window = RewardRuleIn(**_body(delay_days=9))
    assert (after_txn.post_month_offset, after_txn.post_day, after_txn.delay_days) == (0, 1, 3)
    assert (manual.post_month_offset, manual.post_day, manual.delay_days) == (0, 1, 0)
    assert (after_window.post_month_offset, after_window.post_day, after_window.delay_days) == (1, 15, 0)

@pytest.mark.parametrize("value", [None, "none", 7])
def test_share_cap_with_accepts_null_none_and_rule_id(value):
    assert RewardRuleIn(**_body(share_cap_with=value)).share_cap_with == value

def test_share_cap_with_rejects_other_strings():
    with pytest.raises(ValidationError):
        RewardRuleIn(**_body(share_cap_with="all"))
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/accounting-service && OTEL_SDK_DISABLED=true OTEL_SERVICE_NAME_ACCOUNTING=test OTEL_COLLECTOR_ENDPOINT_GRPC=http://127.0.0.1:1 pytest tests/unit/test_reward_rule_schema.py -q`
Expected: FAIL with `ImportError: cannot import name 'RewardRuleIn'`

- [ ] **Step 3: Implement `RewardRuleIn`, `ShareCapWith` in `writes.py` and the overview Out models in `ledger.py`** per Interfaces. Reuse `Int32` and the existing `model_validator` style at `writes.py:151` for field-named errors.

- [ ] **Step 4: Run to verify pass**

Run: same command. Expected: all PASS. Also `pytest tests/unit -q` stays green.

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/schemas/writes.py services/accounting-service/app/schemas/ledger.py services/accounting-service/tests/unit/test_reward_rule_schema.py
git commit -m "feat(rewards): RewardRuleIn and overview schemas"
```

---

### Task 2: `reward_rule_service.create` / `update` + POST / PUT routes

**Files:**
- Create: `services/accounting-service/app/services/reward_rule_service.py`
- Modify: `services/accounting-service/app/routers/accounts.py` (after `list_reward_rules`, L51–54)
- Test: `services/accounting-service/tests/integration/test_reward_rules_api.py`

**Interfaces:**
- Consumes `RewardRuleIn`, `ShareCapWith` (Task 1); `ledger_service.rule_out(rule) -> dict`; `errors.ValidationError`, `CodedConflictError`, `NotFoundError`.
- Produces in `reward_rule_service.py`:
  - `BASIC_RULE_OVERLAP = "basic_rule_overlap"`, `RULE_REFERENCED = "rule_referenced"` (module constants).
  - `create(db: Session, account_id: int, payload: RewardRuleIn) -> int` — returns the new rule id; `sort_order` omitted → `max(sort_order) + 1` of the account (0 when none); `shared_cap_id` from `share_cap_with` (see `_apply_share_cap`); raises `NotFoundError("account not found")`.
  - `update(db: Session, account_id: int, rule_id: int, payload: RewardRuleIn) -> None` — writes every field of 5.1 except `moze_id`, `account_id`, `shared_cap_id` (the latter only via `share_cap_with`); `sort_order` omitted → keeps the row's; raises `NotFoundError("rule not found")` when the rule is missing or belongs to another account.
  - `_check_payload(payload: RewardRuleIn)` — cross-field rules the schema cannot name a `loc` for: percent → `rate` required (`ValidationError("rate", "required for percent rules")`) and `fixed_amount` null (`ValidationError("fixed_amount", "only fixed rules have an amount")`); fixed → `fixed_amount` required (`ValidationError("fixed_amount", "required for fixed rules")`) and `rate` null (`ValidationError("rate", "only percent rules have a rate")`); `starts_on <= ends_on` when both set (`ValidationError("ends_on", "must not be before starts_on")`).
  - `_check_refs(db, payload, account_id)` — `reward_account_id` must exist and not be archived (`ValidationError("reward_account_id", "must be an existing, non-archived account")`); same for `reward_project_id`.
  - `_check_basic_overlap(db, account_id, rule_id: int | None, starts_on, ends_on)` — only when the saved row is `is_enabled and is_basic`; any other enabled basic rule of the account whose `[starts_on, ends_on]` (null = open) intersects → `CodedConflictError(BASIC_RULE_OVERLAP, f"rule {other.id}")`.
  - `_apply_share_cap(db, rule, share_cap_with)` — `None` → unchanged; `"none"` → `rule.shared_cap_id = None`; int → other rule must exist with the same `account_id` and not be `rule` itself (`ValidationError("share_cap_with", "must be another rule of the same account")`); when the other has no cap, `uuid4()` set on both; else copy.
- Routes in `accounts.py`:
  - `@router.post("/{account_id}/reward-rules", response_model=RewardRuleOut, status_code=201)` → `with service_errors(): rule_id = reward_rule_service.create(...)`; `db.commit()`; return `ledger_service.rule_out(db.get(RewardRule, rule_id))`.
  - `@router.put("/{account_id}/reward-rules/{rule_id}", response_model=RewardRuleOut)` → same shape with `update`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_reward_rules_api.py
"""Reward rule CRUD under /accounts/{id}/reward-rules (reward rules design §5.3)."""
from datetime import date
from decimal import Decimal
from uuid import UUID

from app.models import EntryRewardRule, RewardRule

def _body(**fields) -> dict:
    body = {"name": "國內", "method": "percent", "rate": "1.5", "posting": "after_window",
            "post_month_offset": 1, "post_day": 15}
    body.update(fields)
    return body

def _post(client, account_id, **fields):
    response = client.post(f"/accounts/{account_id}/reward-rules", json=_body(**fields))
    assert response.status_code == 201, response.text
    return response.json()

def test_create_appends_last_and_returns_the_row(client, db_session, seed):
    card = seed.account("華航卡", is_credit=True)
    seed.rule(card, name="舊", sort_order=4)
    db_session.commit()
    created = _post(client, card.id, description="國內一般消費")
    assert (created["account_id"], created["name"], created["rate"], created["sort_order"], created["moze_id"]) == (
        card.id, "國內", "1.5000", 5, None)
    assert (created["delay_days"], created["post_month_offset"], created["post_day"], created["shared_cap_id"]) == (0, 1, 15, None)
    assert client.get(f"/accounts/{card.id}/reward-rules").json()[-1]["id"] == created["id"]

def test_create_on_unknown_account_is_404_and_refs_are_checked(client, db_session, seed):
    card = seed.account("華航卡")
    archived = seed.account("舊點數", is_archived=True)
    db_session.commit()
    assert client.post("/accounts/999999/reward-rules", json=_body()).status_code == 404
    bad_account = client.post(f"/accounts/{card.id}/reward-rules", json=_body(reward_account_id=archived.id))
    bad_project = client.post(f"/accounts/{card.id}/reward-rules", json=_body(reward_project_id=999999))
    assert bad_account.status_code == 422 and bad_account.json()["detail"][0]["loc"][-1] == "reward_account_id"
    assert bad_project.status_code == 422 and bad_project.json()["detail"][0]["loc"][-1] == "reward_project_id"

@pytest.mark.parametrize("fields,field", [
    ({"rate": None}, "rate"), ({"fixed_amount": "10"}, "fixed_amount"),
    ({"method": "fixed", "rate": None}, "fixed_amount"), ({"method": "fixed", "fixed_amount": "50"}, "rate"),
    ({"starts_on": "2026-02-01", "ends_on": "2026-01-01"}, "ends_on"),
])
def test_cross_field_rules_are_422_naming_the_field(client, db_session, seed, fields, field):
    card = seed.account("華航卡")
    db_session.commit()
    response = client.post(f"/accounts/{card.id}/reward-rules", json=_body(**fields))
    assert response.status_code == 422 and response.json()["detail"][0]["loc"][-1] == field, response.text

def test_put_writes_the_whole_row_and_keeps_moze_id_and_sort_order(client, db_session, seed):
    card = seed.account("華航卡")
    rule = seed.rule(card, name="國內", moze_id="B-1", sort_order=3)
    db_session.commit()
    response = client.put(f"/accounts/{card.id}/reward-rules/{rule.id}",
                          json=_body(name="國內 2%", rate="2", posting="after_transaction", delay_days=3, is_enabled=False))
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["name"], body["rate"], body["posting"], body["delay_days"], body["post_month_offset"], body["post_day"]) == (
        "國內 2%", "2.0000", "after_transaction", 3, 0, 1)
    assert (body["is_enabled"], body["moze_id"], body["sort_order"]) == (False, "B-1", 3)

def test_put_on_another_accounts_rule_is_404(client, db_session, seed):
    card, other = seed.account("華航卡"), seed.account("玉山卡")
    rule = seed.rule(other)
    db_session.commit()
    assert client.put(f"/accounts/{card.id}/reward-rules/{rule.id}", json=_body()).status_code == 404

def test_basic_overlap_is_409_naming_the_other_rule(client, db_session, seed):
    card = seed.account("華航卡")
    old = seed.rule(card, name="基本舊", is_basic=True, starts_on=date(2026, 1, 1), ends_on=date(2026, 6, 30))
    db_session.commit()
    clash = client.post(f"/accounts/{card.id}/reward-rules",
                        json=_body(name="基本新", is_basic=True, starts_on="2026-06-30", ends_on=None))
    fine = client.post(f"/accounts/{card.id}/reward-rules",
                       json=_body(name="基本新", is_basic=True, starts_on="2026-07-01", ends_on=None))
    assert clash.status_code == 409 and clash.json()["message"].startswith(f"basic_rule_overlap: rule {old.id}")
    assert fine.status_code == 201

def test_basic_overlap_open_ended_and_disabled_neighbour(client, db_session, seed):
    # Review focus 2: an open-ended enabled basic rule clashes with anything later; a disabled one never does.
    card = seed.account("華航卡")
    seed.rule(card, name="開放", is_basic=True, starts_on=date(2026, 1, 1), ends_on=None)
    disabled_card = seed.account("玉山卡")
    seed.rule(disabled_card, name="停用基本", is_basic=True, enabled=False, starts_on=None, ends_on=None)
    db_session.commit()
    assert client.post(f"/accounts/{card.id}/reward-rules", json=_body(is_basic=True, starts_on="2030-01-01")).status_code == 409
    assert client.post(f"/accounts/{card.id}/reward-rules", json=_body(is_basic=True, is_enabled=False)).status_code == 201
    assert client.post(f"/accounts/{disabled_card.id}/reward-rules", json=_body(is_basic=True)).status_code == 201
    # both-null pair: the disabled card's new basic rule is open-ended, a second open-ended one clashes
    assert client.post(f"/accounts/{disabled_card.id}/reward-rules", json=_body(is_basic=True)).status_code == 409

def test_put_of_an_enabled_basic_rule_does_not_clash_with_itself(client, db_session, seed):
    # Review finding 4: autoflush sees the row being saved; the overlap check must exclude the rule's own id.
    card = seed.account("華航卡")
    rule = seed.rule(card, name="基本", is_basic=True, starts_on=date(2026, 1, 1), ends_on=None)
    db_session.commit()
    response = client.put(f"/accounts/{card.id}/reward-rules/{rule.id}",
                          json=_body(name="基本 1.5", is_basic=True, starts_on="2026-01-01", ends_on=None))
    assert response.status_code == 200, response.text

def test_share_cap_with_joins_leaves_and_refuses_other_accounts(client, db_session, seed):
    card, other = seed.account("華航卡"), seed.account("玉山卡")
    a = seed.rule(card, name="A")
    foreign = seed.rule(other, name="F")
    db_session.commit()
    b = _post(client, card.id, name="B", share_cap_with=a.id)
    db_session.expire_all()
    cap = db_session.get(RewardRule, a.id).shared_cap_id
    assert cap is not None and UUID(b["shared_cap_id"]) == cap
    kept = client.put(f"/accounts/{card.id}/reward-rules/{b['id']}", json=_body(name="B")).json()
    left = client.put(f"/accounts/{card.id}/reward-rules/{b['id']}", json=_body(name="B", share_cap_with="none")).json()
    assert UUID(kept["shared_cap_id"]) == cap and left["shared_cap_id"] is None
    assert client.post(f"/accounts/{card.id}/reward-rules", json=_body(share_cap_with=foreign.id)).status_code == 422
    assert client.put(f"/accounts/{card.id}/reward-rules/{a.id}", json=_body(name="A", share_cap_with=a.id)).status_code == 422

def test_disabling_a_linked_rule_keeps_the_link(client, db_session, seed):
    # Review focus 1: the link survives; editing the purchase still passes check_rules through `attached`.
    card = seed.account("華航卡")
    rule = seed.rule(card)
    entry = seed.entry(card, "-100", day=date(2026, 9, 1))
    db_session.add(EntryRewardRule(entry_id=entry.id, rule_id=rule.id))
    db_session.commit()
    assert client.put(f"/accounts/{card.id}/reward-rules/{rule.id}", json=_body(is_enabled=False)).status_code == 200
    assert db_session.scalar(select(EntryRewardRule.rule_id).where(EntryRewardRule.entry_id == entry.id)) == rule.id
    edited = client.put(f"/entries/{entry.id}", json={"account_id": card.id, "kind": "expense", "amount": "120",
                                                      "entry_date": "2026-09-01", "reward_rule_ids": [rule.id]})
    assert edited.status_code == 200, edited.text
```
(Add `import pytest` and `from sqlalchemy import select`. `seed.account(**extra)` → `make_account(..., **columns)` → `Account(...)`, so `is_credit=True` / `is_archived=True` / `moze_id=` / `sort_order=` work. `seed.rule` passes `**extra` to `RewardRule`, so `moze_id`, `sort_order`, `is_basic`, `rate` work; `starts_on=None, ends_on=None` override its defaults. `seed.entry(..., kind="reward", reward_rule_id=…)` → `make_entry(**columns)` → `LedgerEntry`, so extra columns pass through.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/integration/test_reward_rules_api.py -q` (same env vars as Task 1)
Expected: FAIL — 405 / 404 on POST (route missing).

- [ ] **Step 3: Implement `reward_rule_service.py` (`create`, `update`, helpers) and the two routes**

Service structure: `_get_account(db, account_id)`, `_get_rule(db, account_id, rule_id)`, `_check_payload`, `_check_refs`, `_check_basic_overlap`, `_apply_share_cap`, `_write_fields(rule, payload)` (every `RewardRuleIn` field except `share_cap_with`, `sort_order`), then `create` / `update`. Order inside both: payload → refs → write fields → overlap (uses the payload's values and the row's id to exclude itself) → share cap → `db.flush()`.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/integration/test_reward_rules_api.py tests/integration/test_accounts_api.py tests/integration/test_scope_enforcement.py -q`
Expected: PASS (the scope test enumerates the new mutation routes; `_fill` already knows `account_id`; add `rule_id` to its name list if the parametrised path keeps `{rule_id}` unfilled — 404 vs 403: hermes without `write` must get 403 first, which `method_scope` guarantees before any handler runs).

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/reward_rule_service.py services/accounting-service/app/routers/accounts.py services/accounting-service/tests/integration/test_reward_rules_api.py services/accounting-service/tests/integration/test_scope_enforcement.py
git commit -m "feat(rewards): create and update reward rules via /accounts/{id}/reward-rules"
```

---

### Task 3: reorder + delete

**Files:**
- Modify: `services/accounting-service/app/services/reward_rule_service.py`
- Modify: `services/accounting-service/app/routers/accounts.py`
- Test: `services/accounting-service/tests/integration/test_reward_rules_api.py` (append)

**Interfaces:**
- Consumes `OrderIn` (`writes.py:268`, `ids: list[Int32]`), `settings_service._reorder` as the pattern (not reused: the rule version must also demand completeness).
- Produces:
  - `reorder(db, account_id: int, ids: list[int]) -> None` — `ids` must equal the set of the account's rule ids with no repeats, else `ValidationError("ids", "must list every rule of the account exactly once")`; positions 0..n-1 written to `sort_order`.
  - `delete(db, account_id: int, rule_id: int) -> None` — counts `entry_reward_rule` rows and `ledger_entry.reward_rule_id` rows; any → `CodedConflictError(RULE_REFERENCED, json.dumps({"entries": n, "rewards": m}))` (detail string is the JSON object, so the SPA can parse it after the `: `); else `db.delete(rule)`.
- Routes (order matters: `order` **before** `{rule_id}` in the file):
  - `@router.put("/{account_id}/reward-rules/order", response_model=list[RewardRuleOut])` body `OrderIn` → `reorder` → `db.commit()` → `ledger_service.list_reward_rules(db, account_id)`.
  - `@router.delete("/{account_id}/reward-rules/{rule_id}", status_code=204)` → `delete` → `db.commit()` → `Response(status_code=204)`.

- [ ] **Step 1: Write the failing tests** (append to `test_reward_rules_api.py`)

```python
def test_reorder_writes_positions_and_returns_the_list(client, db_session, seed):
    card = seed.account("華航卡")
    a, b, c = (seed.rule(card, name=n, sort_order=i) for i, n in enumerate("ABC"))
    db_session.commit()
    response = client.put(f"/accounts/{card.id}/reward-rules/order", json={"ids": [c.id, a.id, b.id]})
    assert response.status_code == 200, response.text
    assert [(r["name"], r["sort_order"]) for r in response.json()] == [("C", 0), ("A", 1), ("B", 2)]

def test_reorder_rejects_missing_or_extra_ids(client, db_session, seed):
    # Review focus 3: a stale list (a rule Hermes just added is missing) changes nothing.
    card, other = seed.account("華航卡"), seed.account("玉山卡")
    a, b = seed.rule(card, name="A", sort_order=0), seed.rule(card, name="B", sort_order=1)
    foreign = seed.rule(other)
    db_session.commit()
    for ids in ([a.id], [a.id, b.id, foreign.id], [a.id, a.id]):
        response = client.put(f"/accounts/{card.id}/reward-rules/order", json={"ids": ids})
        assert response.status_code == 422 and response.json()["detail"][0]["loc"][-1] == "ids", ids
    assert [r["sort_order"] for r in client.get(f"/accounts/{card.id}/reward-rules").json()] == [0, 1]

def test_delete_unreferenced_rule_and_refuse_referenced_with_counts(client, db_session, seed):
    card = seed.account("華航卡")
    free, used = seed.rule(card, name="free"), seed.rule(card, name="used")
    purchase = seed.entry(card, "-100")
    db_session.add(EntryRewardRule(entry_id=purchase.id, rule_id=used.id))
    seed.entry(card, "5", kind="reward", reward_rule_id=used.id, reward_source_entry_id=purchase.id)
    seed.entry(card, "7", kind="reward", reward_rule_id=used.id)
    db_session.commit()
    assert client.delete(f"/accounts/{card.id}/reward-rules/{free.id}").status_code == 204
    refused = client.delete(f"/accounts/{card.id}/reward-rules/{used.id}")
    assert refused.status_code == 409
    code, _, detail = refused.json()["message"].partition(": ")
    assert code == "rule_referenced" and json.loads(detail) == {"entries": 1, "rewards": 2}
    assert client.delete(f"/accounts/{card.id}/reward-rules/{free.id}").status_code == 404
```
(`import json` at the top of the file.)

- [ ] **Step 2: Run to verify failure** — `pytest tests/integration/test_reward_rules_api.py -q`; expected FAIL on the three new tests.

- [ ] **Step 3: Implement `reorder`, `delete` and the two routes** per Interfaces; place the `order` route above the `{rule_id}` PUT from Task 2.

- [ ] **Step 4: Run to verify pass** — same file plus `tests/integration/test_scope_enforcement.py -q`; expected PASS.

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/reward_rule_service.py services/accounting-service/app/routers/accounts.py services/accounting-service/tests/integration/test_reward_rules_api.py
git commit -m "feat(rewards): reorder and delete reward rules (409 rule_referenced)"
```

---

### Task 4: overview `GET /reward-rules`

**Files:**
- Modify: `services/accounting-service/app/services/reward_rule_service.py`
- Create: `services/accounting-service/app/routers/reward_rules.py`
- Modify: `services/accounting-service/app/main.py` (import + `include_router`)
- Modify: `services/accounting-service/README.md` (API section: the four write routes of Tasks 2–3 and this GET, one bullet each, beside the existing `GET /accounts/{id}/reward-rules` line ~L80)
- Test: `services/accounting-service/tests/integration/test_reward_rules_overview.py`

**Interfaces:**
- Consumes `RewardRuleOverviewOut` & co (Task 1), `ledger_service.rule_out`, `ledger_service._today()` — call it through the module at request time (`ledger_service._today()`), never alias it at import, so the tests' `monkeypatch.setattr(ledger_service, "_today", …)` (the pattern of `test_entry_writes.py:660`) takes effect.
- Produces:
  - `rule_state(rule: RewardRule, today: date) -> RewardRuleState` — `disabled` when not enabled; else `expired` when `ends_on < today`; else `upcoming` when `starts_on > today`; else `active`.
  - `overview(db, *, account_ids: list[int] | None, state: str, include_archived_accounts: bool) -> dict` shaped as `RewardRuleOverviewOut`: accounts ordered by `Account.sort_order, Account.name`; archived accounts dropped unless the flag (an explicit `account_ids` member that is archived is still shown — the caller asked); **every** account in scope appears, with `rules: []` when it has none (the SPA's 尚無規則 card); `state != "all"` filters the rules but not the accounts; `counts` are computed over all rules of the accounts in scope **before** the state filter, `enabled_expired = count(is_enabled and ends_on < today)`.
  - Stats: three grouped queries over the rule ids in scope — (1) `entry_reward_rule` joined to `ledger_entry` grouped by `rule_id`: `count(*)`, `count(*) filter (where entry_date >= today - 1 year)`; (2) `ledger_entry` where `kind = 'reward' and reward_rule_id in (...)` grouped by `(reward_rule_id, currency)`: `count`, `sum(amount)`; (3) same rows grouped by `reward_rule_id`: `max(entry_date)`. Rewards_total list sorted by currency. 12-month boundary: `entry_date >= today - relativedelta(years=1)` inclusive (use `date(today.year - 1, today.month, today.day)` with a Feb-29 fallback to Feb-28).
- Router `reward_rules.py`: `router = APIRouter(prefix="/reward-rules", tags=["Reward rules"], dependencies=[Depends(method_scope())])`; `@router.get("", response_model=RewardRuleOverviewOut)` with `account_id: list[int] | None = Query(default=None)`, `state: Literal["active","disabled","expired","upcoming","all"] = "all"`, `include_archived_accounts: bool = False`. Register in `main.py` beside `accounts.router`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_reward_rules_overview.py
"""GET /reward-rules: every account, rule state, stats (reward rules design §5.3)."""
import time
from datetime import date, timedelta
from decimal import Decimal

from app.models import EntryRewardRule
from app.services import ledger_service

TODAY = date(2026, 11, 1)

def _freeze(monkeypatch):
    monkeypatch.setattr(ledger_service, "_today", lambda: TODAY)

def test_overview_groups_by_account_with_states_and_counts(client, db_session, seed, monkeypatch):
    _freeze(monkeypatch)
    card = seed.account("華航卡", is_credit=True, sort_order=1)
    bare = seed.account("玉山卡", is_credit=True, sort_order=2)
    gone = seed.account("舊卡", is_archived=True, sort_order=0)
    seed.rule(card, name="active", starts_on=date(2026, 1, 1), ends_on=None)
    seed.rule(card, name="expired", starts_on=date(2025, 1, 1), ends_on=date(2026, 10, 31))
    seed.rule(card, name="upcoming", starts_on=date(2027, 1, 1), ends_on=None)
    seed.rule(card, name="disabled", enabled=False, ends_on=date(2026, 1, 1))
    seed.rule(gone, name="archived card rule")
    db_session.commit()
    body = client.get("/reward-rules").json()
    assert [a["account_name"] for a in body["accounts"]] == ["華航卡", "玉山卡"]
    assert [(r["name"], r["state"]) for r in body["accounts"][0]["rules"]] == [
        ("active", "active"), ("expired", "expired"), ("upcoming", "upcoming"), ("disabled", "disabled")]
    assert body["accounts"][1]["rules"] == []
    assert body["counts"] == {"active": 1, "disabled": 1, "expired": 1, "upcoming": 1, "enabled_expired": 1}
    expired_only = client.get("/reward-rules", params={"state": "expired"}).json()
    assert [r["name"] for r in expired_only["accounts"][0]["rules"]] == ["expired"]
    assert expired_only["counts"]["active"] == 1  # counts ignore the filter
    with_archived = client.get("/reward-rules", params={"include_archived_accounts": "true"}).json()
    assert [a["account_name"] for a in with_archived["accounts"]] == ["舊卡", "華航卡", "玉山卡"]
    one = client.get("/reward-rules", params=[("account_id", bare.id)]).json()
    assert [a["account_id"] for a in one["accounts"]] == [bare.id]
    asked_archived = client.get("/reward-rules", params=[("account_id", gone.id)]).json()
    assert [a["account_id"] for a in asked_archived["accounts"]] == [gone.id]  # explicit id wins over the archived default

def test_overview_stats_per_currency_and_12m_boundary(client, db_session, seed, monkeypatch):
    # Review focus 4: two currencies on one rule; a link dated exactly one year ago counts in the 12-month figure.
    _freeze(monkeypatch)
    card = seed.account("華航卡")
    points = seed.account("點數", currency="PTS")
    rule = seed.rule(card, name="R")
    old = seed.entry(card, "-100", day=TODAY - timedelta(days=400))
    edge = seed.entry(card, "-100", day=date(2025, 11, 1))
    recent = seed.entry(card, "-100", day=TODAY)
    for purchase in (old, edge, recent):
        db_session.add(EntryRewardRule(entry_id=purchase.id, rule_id=rule.id))
    seed.entry(card, "3", kind="reward", day=date(2026, 3, 15), reward_rule_id=rule.id)
    seed.entry(points, "120", kind="reward", day=date(2026, 9, 15), reward_rule_id=rule.id)
    seed.entry(points, "80", kind="reward", day=date(2026, 10, 15), reward_rule_id=rule.id)
    db_session.commit()
    stats = client.get("/reward-rules").json()["accounts"][0]["rules"][0]["stats"]
    assert (stats["linked_entries"], stats["linked_entries_12m"], stats["rewards_count"]) == (3, 2, 3)
    assert stats["rewards_total"] == [{"currency": "PTS", "amount": "200.0000"}, {"currency": "TWD", "amount": "3.0000"}]
    assert stats["rewards_last_date"] == "2026-10-15"

def test_overview_without_rewards_has_empty_totals(client, db_session, seed):
    card = seed.account("華航卡")
    seed.rule(card)
    db_session.commit()
    stats = client.get("/reward-rules").json()["accounts"][0]["rules"][0]["stats"]
    assert (stats["rewards_total"], stats["rewards_last_date"], stats["linked_entries"]) == ([], None, 0)

def test_overview_is_grouped_not_per_rule(client, db_session, seed):
    # 20 cards × 5 rules, 3000 links, 1500 reward rows: median of 5 calls well under the reports' 0.5 s gate.
    import random
    rng = random.Random(7)
    cards = [seed.account(f"卡{i}") for i in range(20)]
    rules = [seed.rule(card, name=f"r{j}") for card in cards for j in range(5)]
    purchases = [seed.entry(cards[i % 20], "-100", day=date(2026, 1 + i % 10, 1)) for i in range(3000)]
    db_session.flush()
    db_session.add_all(EntryRewardRule(entry_id=p.id, rule_id=rng.choice(rules).id) for p in purchases)
    for i in range(1500):
        seed.entry(cards[i % 20], "2", kind="reward", day=date(2026, 1 + i % 10, 15), reward_rule_id=rules[i % 100].id)
    db_session.commit()
    timings = []
    for _ in range(5):
        started = time.perf_counter()
        assert client.get("/reward-rules").status_code == 200
        timings.append(time.perf_counter() - started)
    assert sorted(timings)[2] < 0.5, timings
```
(`seed.entry` accepts `day=` and forwards `reward_rule_id` to `LedgerEntry`.)

- [ ] **Step 2: Run to verify failure** — `pytest tests/integration/test_reward_rules_overview.py -q`; expected 404s.

- [ ] **Step 3: Implement `rule_state`, `overview`, the router, the `main.py` registration and the README bullets**

- [ ] **Step 4: Run to verify pass** — the new file plus `pytest tests/integration/test_scope_enforcement.py tests/unit/test_api_auth.py -q`; expected PASS.

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/reward_rule_service.py services/accounting-service/app/routers/reward_rules.py services/accounting-service/app/main.py services/accounting-service/README.md services/accounting-service/tests/integration/test_reward_rules_overview.py
git commit -m "feat(rewards): GET /reward-rules overview with states and stats"
```

---

### Task 5: backup import rule pass becomes read-only

**Files:**
- Modify: `services/accounting-service/app/services/moze_backup_import_service.py` (`SettingsResult` L128–145, `_upsert_rules` L413–500, `upsert_settings` L515, report dict ~L1351–1361)
- Modify: `services/accounting-service/README.md` (backup paragraph L39: "reward rules (by `moze_id`, never recreated)" → "reward rules are only looked up by `moze_id` for the purchase → rule links; the import no longer creates, updates or deletes them (HomeHub is the rules' only writer since 2026-11)")
- Modify: `frontend/src/app/models/accounting.model.ts:588-589` (`ImportReport`: drop `unsupported_rules` / `orphaned_rules`, add `rules_unknown: number`) — and `components/accounting/accounting-settings/import-report.ts` if it renders either key (grep first; today only the model names them).
- Test: `services/accounting-service/tests/integration/test_backup_settings_import.py` (L194–281); `tests/integration/test_backup_entries_import.py` (`_reward_data` ~L563 and every test built on it, **and** `test_reward_on_a_split_purchase_links_to_the_primary_member` L593 which builds its own `rules=[backup.rule("B-1")]` — all relied on the import creating `B-1`/`B-2`; they now seed the card with `moze_id="A-CARD"` and `seed.rule(card, moze_id="B-1")` / `"B-2"` first, `B-GONE` stays unresolved, and `needs_review.count == 0` still holds); `tests/integration/test_backup_replace_and_report.py` (`_doc` L44 lists `backup.rule("B-1")`; `test_report_shape_and_balance_comparison_default` L196 expects `rules == 1` and `attachments == 1`; `test_reimport_keeps_manual_entries_attachments_and_rule_ids` L98–109 reads `select(RewardRule.id)` after the first import — seed the card + rule with `moze_id` before the import so both keep passing, and the survival assertion now proves the import left the seeded row alone); `tests/integration/test_moze_replace_ledger.py` (`test_csv_import_replaces_a_backup_import` L180–200, same null-id insert — same seed fix).

**Interfaces:**
- Produces `_resolve_rules(session, data, result)` replacing `_upsert_rules`: for each `data.rules` record, `select(RewardRule).where(RewardRule.moze_id == record["identifier"])`; found → `result.rules[identifier] = rule.id`; not found → `result.rules_unknown += 1`. No writes, no "gone" sweep. `SettingsResult` gains `rules_unknown: int = 0` and loses `unsupported_rules`, `orphaned_rules`. Report dict: keep `"rules": len(settings.rules)`, add `"rules_unknown": settings.rules_unknown`, drop the two lists. Delete `UNSUPPORTED_RULE_FIELDS` and the import of `REWARD_METHOD_MAP` / `REWARD_WINDOW_MAP` from this module **only** — `ROUNDING_MAP` (fee rounding L265, report ~L1376) and `POSTING_MAP` stay where they are defined (`moze_backup_json.py`, pinned by `tests/unit/test_moze_backup_json.py:113-114`); leave the map definitions in `moze_backup_json.py` untouched.
- `_link_rewards_and_attachments` unchanged: a reward record whose `rewardID` is unresolved already gets `reward_rule_missing`.

- [ ] **Step 1: Rewrite the rule tests**

Delete `test_percent_rule_with_cap`, `test_fixed_rule_with_shared_cap`, `test_unsupported_threshold_disables_the_rule`, `test_unknown_rule_codes_fail_the_import`, `test_reimport_updates_rules_in_place_and_handles_orphans`, `test_posting_fields_are_normalised_when_the_rule_does_not_post_after_the_window`, `test_out_of_range_posting_day_fails_naming_the_rule` (all describe writes). Add:

```python
def test_import_resolves_existing_rules_by_moze_id_and_never_writes_them(db_session, backup, seed):
    card = seed.account("華航卡", moze_id="A-CARD")
    rule = seed.rule(card, name="HomeHub 名稱", rate=Decimal("3"), moze_id="B-1")
    db_session.commit()
    summary = _import_backup(db_session, _rules_backup(backup, backup.rule("B-1", name="MOZE 名稱", rewardPercentage=0.05)))
    db_session.refresh(rule)
    assert (rule.name, rule.rate, rule.is_enabled) == ("HomeHub 名稱", Decimal("3.0000"), True)
    assert db_session.scalar(select(func.count()).select_from(RewardRule)) == 1
    assert (summary["rules"], summary["rules_unknown"]) == (1, 0)
    assert "orphaned_rules" not in summary and "unsupported_rules" not in summary

def test_unknown_rule_records_are_counted_not_created(db_session, backup, seed):
    # Review focus 5: a rule record for an account outside the backup and one HomeHub never had.
    card = seed.account("華航卡", moze_id="A-CARD")
    db_session.commit()
    summary = _import_backup(db_session, _rules_backup(backup,
        backup.rule("B-NEW"), backup.rule("B-OTHER", account="A-MISSING")))
    assert db_session.scalar(select(func.count()).select_from(RewardRule)) == 0
    assert (summary["rules"], summary["rules_unknown"]) == (0, 2)

def test_reimport_with_a_rule_gone_from_the_backup_keeps_the_homehub_row(db_session, backup, seed):
    card = seed.account("華航卡", moze_id="A-CARD")
    rule = seed.rule(card, moze_id="B-1")
    db_session.commit()
    _import_backup(db_session, _rules_backup(backup))  # no rule records at all
    assert db_session.get(RewardRule, rule.id) is not None

def test_reward_record_links_to_the_resolved_rule(db_session, backup, seed):
    card = seed.account("華航卡", moze_id="A-CARD")
    rule = seed.rule(card, moze_id="B-1")
    db_session.commit()
    data = backup.data(
        accounts=[backup.account("A-CARD", "華航卡", isCreditAccount=True)],
        rules=[backup.rule("B-1", "A-CARD", name="MOZE 名稱")],
        records=[
            backup.record("R-BUY", "A-CARD", price=-1000, bonusRewards=["B-1", "B-GONE"]),
            backup.record("R-REWARD", "A-CARD", type_=14, price=10, rewardID="B-1", rewardRecordID="R-BUY",
                          chargeDate="2026-09-15T00:00:00"),
        ],
    )
    _import_backup(db_session, data)
    reward = db_session.scalar(select(LedgerEntry).where(LedgerEntry.moze_id == "R-REWARD"))
    assert reward.reward_rule_id == rule.id
    assert [a.rule_id for a in db_session.scalars(select(EntryRewardRule))] == [rule.id]
    assert db_session.get(RewardRule, rule.id).name == "回饋"  # the seeded name, not MOZE's
```
(`_rules_backup` already puts `A-CARD` and `A-PTS` in the backup doc; the import resolves the seeded card by `moze_id` through `_upsert_named`, so no second account appears. In `test_backup_entries_import.py`, add the same two seed lines at the top of every `_reward_data` test and pass `seed` to them.)

- [ ] **Step 2: Run to verify failure** — `pytest tests/integration/test_backup_settings_import.py -q`; expected the new tests FAIL (rule rewritten / created).

- [ ] **Step 3: Implement `_resolve_rules`, the `SettingsResult` and report changes, README and the frontend `ImportReport` type**

- [ ] **Step 4: Run to verify pass** — `pytest tests/integration/test_backup_settings_import.py tests/integration/test_backup_replace_and_report.py tests/integration/test_backup_entries_import.py tests/integration/test_moze_replace_ledger.py tests/integration/test_backup_imports_api.py tests/unit/test_moze_backup_json.py -q` and `cd frontend && npx vitest run src/app/components/accounting/accounting-settings` — PASS.

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app/services/moze_backup_import_service.py services/accounting-service/README.md services/accounting-service/tests/integration/test_backup_settings_import.py services/accounting-service/tests/integration/test_backup_entries_import.py services/accounting-service/tests/integration/test_backup_replace_and_report.py services/accounting-service/tests/integration/test_moze_replace_ledger.py frontend/src/app/models/accounting.model.ts
git commit -m "feat(import): backup import resolves reward rules by moze_id, never writes them"
```

---

### Task 6: SPA models, service methods, shared rule text helpers

**Files:**
- Modify: `frontend/src/app/models/accounting.model.ts` (after `RewardRule`, L55)
- Modify: `frontend/src/app/services/accounting.service.ts` (after `resetSettingsFlag`, ~L152)
- Modify: `frontend/src/app/components/accounting/format.ts` (receives `decimalText`), `account-settings/account-settings.ts` (re-exports it)
- Create: `frontend/src/app/components/accounting/reward-rule-text.ts`, `reward-rule-text.spec.ts`

**Interfaces:**
- Produces in `accounting.model.ts`:
  ```ts
  export type ShareCapWith = 'none' | number | null;
  export interface RewardRuleInput {
    name: string; method: RewardMethod; rate: string | null; fixed_amount: string | null;
    posting: RewardPosting; delay_days: number; post_month_offset: number; post_day: number;
    txn_rounding: RoundingMode; total_rounding: RoundingMode; total_cap: string | null;
    is_basic: boolean; reward_account_id: number | null; reward_project_id: number | null;
    starts_on: string | null; ends_on: string | null; is_enabled: boolean; description: string | null;
    sort_order?: number; share_cap_with?: ShareCapWith;
  }
  export type RewardRuleState = 'active' | 'disabled' | 'expired' | 'upcoming';
  export interface RewardTotal { currency: string; amount: string }
  export interface RewardRuleStats { linked_entries: number; linked_entries_12m: number; rewards_count: number; rewards_total: RewardTotal[]; rewards_last_date: string | null }
  export interface RewardRuleOverviewRule extends RewardRule { state: RewardRuleState; stats: RewardRuleStats }
  export interface RewardRuleOverviewAccount { account_id: number; account_name: string; is_credit: boolean; is_archived: boolean; rules: RewardRuleOverviewRule[] }
  export interface RewardRuleCounts { active: number; disabled: number; expired: number; upcoming: number; enabled_expired: number }
  export interface RewardRuleOverview { accounts: RewardRuleOverviewAccount[]; counts: RewardRuleCounts }
  export interface RewardRuleOverviewQuery { account_id?: number[]; state?: RewardRuleState | 'all'; include_archived_accounts?: boolean }
  ```
- Produces in `AccountingService` (all account-scoped writes through `bumpAccounts` so `AccountDetail` consumers reload):
  `createRewardRule(accountId: number, input: RewardRuleInput): Observable<RewardRule>` → POST `${apiUrl}/accounts/${accountId}/reward-rules`;
  `updateRewardRule(accountId: number, ruleId: number, input: RewardRuleInput): Observable<RewardRule>` → PUT `.../reward-rules/${ruleId}`;
  `reorderRewardRules(accountId: number, ids: number[]): Observable<RewardRule[]>` → PUT `.../reward-rules/order` `{ ids }`;
  `deleteRewardRule(accountId: number, ruleId: number): Observable<void>` → DELETE;
  `getRewardRuleOverview(query: RewardRuleOverviewQuery = {}): Observable<RewardRuleOverview>` → GET `${apiUrl}/reward-rules` with `toParams(query)` (repeat `account_id`; check `toParams` handles arrays — `getEntries` already sends repeatable `account_id`).
- Produces in `reward-rule-text.ts` (pure, no Angular):
  `ruleState(rule: Pick<RewardRule, 'is_enabled' | 'starts_on' | 'ends_on'>, today: string): RewardRuleState`;
  `RULE_STATE_LABELS: Record<RewardRuleState, string>` = 啟用中 / 已停用 / 已到期 / 尚未開始;
  `postingSummary(rule: Pick<RewardRule, 'posting' | 'post_month_offset' | 'post_day' | 'delay_days'>): string` → `帳單結帳後第 ${offset} 月 ${day} 日` / `交易後 ${n} 天` / `手動`;
  `periodText(rule: Pick<RewardRule, 'starts_on' | 'ends_on'>): string` → `yyyy/MM/dd – yyyy/MM/dd` via `slashDate`, one side `起` / `迄` missing → `2026/01/01 起` / `至 2026/12/31`, both null → `無期限`;
  `REWARD_ROUNDING_LABELS: Record<RoundingMode, string>` = keep 不取整, round 四捨五入, floor 無條件捨去, ceil 無條件進位;
  `ruleToInput(rule: RewardRule): RewardRuleInput` (every writable field; `rate`/`fixed_amount`/`total_cap` through `decimalText`). **Move** `decimalText` from `account-settings.ts` into `components/accounting/format.ts` (same body) and leave `export { decimalText } from '../format';` in `account-settings.ts` so its spec's import keeps working; `reward-rule-text.ts` imports it from `./format` (no component import, no cycle);
  `parseReferenced(err: unknown): { entries: number; rewards: number } | null` — 409 whose message/detail starts with `rule_referenced: ` → JSON after it; `basicOverlapRuleId(err: unknown): number | null` — 409 `basic_rule_overlap: rule <id>`.
  Re-export `ruleLabel` from `entry-form/entry-draft` so callers import one module.

- [ ] **Step 1: Write the failing tests**

```ts
// reward-rule-text.spec.ts
import { HttpErrorResponse } from '@angular/common/http';
import { describe, expect, it } from 'vitest';
import { makeRule } from './testing/fixtures';
import { basicOverlapRuleId, parseReferenced, periodText, postingSummary, ruleState, ruleToInput } from './reward-rule-text';

describe('ruleState', () => {
  it('orders disabled > expired > upcoming > active', () => {
    expect(ruleState(makeRule({ is_enabled: false, ends_on: '2020-01-01' }), '2026-11-01')).toBe('disabled');
    expect(ruleState(makeRule({ ends_on: '2026-10-31' }), '2026-11-01')).toBe('expired');
    expect(ruleState(makeRule({ starts_on: '2026-11-02', ends_on: null }), '2026-11-01')).toBe('upcoming');
    expect(ruleState(makeRule({ starts_on: '2026-11-01', ends_on: '2026-11-01' }), '2026-11-01')).toBe('active');
    expect(ruleState(makeRule({ starts_on: null, ends_on: null }), '2026-11-01')).toBe('active');
  });
});

describe('text', () => {
  it('summarises posting', () => {
    expect(postingSummary(makeRule({ posting: 'after_window', post_month_offset: 1, post_day: 15 }))).toBe('帳單結帳後第 1 月 15 日');
    expect(postingSummary(makeRule({ posting: 'after_transaction', delay_days: 3 }))).toBe('交易後 3 天');
    expect(postingSummary(makeRule({ posting: 'manual' }))).toBe('手動');
  });
  it('renders the period', () => {
    expect(periodText({ starts_on: '2026-01-01', ends_on: '2026-12-31' })).toBe('2026/01/01 – 2026/12/31');
    expect(periodText({ starts_on: '2026-01-01', ends_on: null })).toBe('2026/01/01 起');
    expect(periodText({ starts_on: null, ends_on: '2026-12-31' })).toBe('至 2026/12/31');
    expect(periodText({ starts_on: null, ends_on: null })).toBe('無期限');
  });
  it('maps a rule to its input without id, account, moze_id, shared cap', () => {
    const input = ruleToInput(makeRule({ rate: '1.5000', total_cap: '150.0000', moze_id: 'B-1', shared_cap_id: 'x' }));
    expect(input).not.toHaveProperty('id');
    expect(input).not.toHaveProperty('moze_id');
    expect(input).not.toHaveProperty('shared_cap_id');
    expect(input.total_cap).toBe('150');
    expect(input.rate).toBe('1.5');
  });
});

describe('409 parsing', () => {
  const conflict = (message: string) => new HttpErrorResponse({ status: 409, error: { code: 'conflict', message } });
  it('reads rule_referenced counts', () => {
    expect(parseReferenced(conflict('rule_referenced: {"entries": 3, "rewards": 2}'))).toEqual({ entries: 3, rewards: 2 });
    expect(parseReferenced(conflict('basic_rule_overlap: rule 9'))).toBeNull();
  });
  it('reads the clashing basic rule id', () => {
    expect(basicOverlapRuleId(conflict('basic_rule_overlap: rule 9'))).toBe(9);
    expect(basicOverlapRuleId(new HttpErrorResponse({ status: 422 }))).toBeNull();
  });
});
```
(Check `makeRule` defaults in `testing/fixtures.ts:55` and adjust the literals so the expectations hold.)

- [ ] **Step 2: Run to verify failure** — `cd frontend && npx vitest run src/app/components/accounting/reward-rule-text.spec.ts`; expected module-not-found.

- [ ] **Step 3: Implement the models, the five service methods and `reward-rule-text.ts`**

- [ ] **Step 4: Run to verify pass** — same spec + `npx vitest run src/app/services` + `npx tsc -p tsconfig.app.json --noEmit`; PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/models/accounting.model.ts frontend/src/app/services/accounting.service.ts frontend/src/app/components/accounting/format.ts frontend/src/app/components/accounting/account-settings/account-settings.ts frontend/src/app/components/accounting/reward-rule-text.ts frontend/src/app/components/accounting/reward-rule-text.spec.ts
git commit -m "feat(rewards): SPA models, service methods and rule text helpers"
```

---

### Task 7: reward rule editor sheet

**Files:**
- Create: `frontend/src/app/components/accounting/reward-rule-sheet/reward-rule-sheet.ts`, `.html`, `.scss` (small; `styleUrls: ['../sheet.scss', './reward-rule-sheet.scss']`), `.spec.ts`

**Interfaces:**
- Consumes `AccountingService.createRewardRule/updateRewardRule/deleteRewardRule` (Task 6), helpers from `reward-rule-text.ts`, `fieldErrors` / `writeErrorMessage` (`http-errors.ts`), overlay contract helpers `focusables`, `trapFocus`, `restoreOverlayFocus`, `isHandledKey` (`accounting-ui.ts`), `DirtyFormRegistry` is **not** used (page-level); the sheet keeps its own discard strip.
- Produces `RewardRuleSheetComponent` (`selector: 'app-reward-rule-sheet'`):
  inputs `accountId = input.required<number>()`, `currency = input.required<string>()`, `rule = input<RewardRule | null>(null)` (null = create), `siblings = input<RewardRule[]>([])` (the account's other rules, for 與…共用上限), `accounts = input<LedgerAccount[]>([])` (open accounts for 入帳帳戶; the card itself preselected), `projects = input<Project[]>([])`;
  outputs `saved = output<RewardRule>()`, `deleted = output<number>()`, `closed = output<void>()`.
  Form state: one `signal<RuleForm>` with string fields for numbers (`rate`, `fixed_amount`, `total_cap`, `delay_days`, `post_month_offset`, `post_day`), `share_cap_with: ShareCapWith` initialised from the rule: when `rule.shared_cap_id` matches a sibling's cap → that sibling's id (shows as joined), else `null`; changing the select to 無 sets `'none'` only if the rule had a cap, else `null`.
  `isDirty()`, `cancel()` → if dirty, show strip 放棄未儲存的內容？ with 留下 / 放棄; `save()` → POST or PUT; 422 → `fieldErrors` inline under the field (`data-field` smalls like account settings); 409 `basic_rule_overlap` → message `與「<name>」的基本回饋期間重疊` (name looked up in `siblings`, else `#id`); other → `writeErrorMessage`.
  `remove()` → first tap arms (確定刪除？), second tap DELETE; 409 `rule_referenced` → message `此規則已有 N 筆交易 / M 筆回饋引用，改為停用？` with a 停用 button that PUTs `{...ruleToInput(rule), is_enabled: false}` and emits `saved`.
  Groups rendered in order 基本 / 入帳 / 上限 / 期間 as `<fieldset>`s with `<legend>`; dependent fields: 結帳後第 N 月 D 日 inputs only for `after_window`, 交易後 N 天 only for `after_transaction`.
  Local validation before send (mirrors the schema, so the common mistakes never round-trip): name required; percent needs `0 < rate ≤ 100`; fixed needs `fixed_amount > 0`; `starts_on ≤ ends_on`.

- [ ] **Step 1: Write the failing spec** — cover: (a) create mode posts the body with `share_cap_with` omitted and emits `saved`; (b) edit mode preloads from `rule`, switching 入帳 to 交易後 shows 天 input and hides 月/日; (c) picking a sibling sends `share_cap_with: <id>`, picking 無 on a rule that had a cap sends `'none'`; (d) 422 on `rate` renders `small[data-field="rate"]`; (e) 409 `basic_rule_overlap: rule 2` shows the sibling's name; (f) delete → 409 `rule_referenced` → text 此規則已有 3 筆交易 / 2 筆回饋引用，改為停用？ and the 停用 button PUTs `is_enabled: false`; (g) Esc with a dirty form shows the strip, 放棄 emits `closed`. Use `provideHttpClientTesting` + `HttpTestingController` as `account-settings.spec.ts` does; drive inputs with `fixture.componentRef.setInput`.

- [ ] **Step 2: Run to verify failure** — `npx vitest run src/app/components/accounting/reward-rule-sheet`.

- [ ] **Step 3: Implement the component** per Interfaces; keep the template's controls class-named (`rule-name`, `rule-method`, `rule-rate`, `rule-fixed`, `rule-posting`, `rule-offset`, `rule-day`, `rule-delay`, `rule-txn-rounding`, `rule-total-rounding`, `rule-cap`, `rule-share`, `rule-basic`, `rule-reward-account`, `rule-project`, `rule-starts`, `rule-ends`, `rule-enabled`, `rule-description`, `sheet-confirm`, `sheet-cancel`, `rule-delete`, `rule-disable`) so the specs of Tasks 8 and the sheet select them.

- [ ] **Step 4: Run to verify pass** — the spec, then `npm run build` (budget check; a sheet-only `.scss` should be < 2 kB).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/reward-rule-sheet
git commit -m "feat(rewards): reward rule editor sheet"
```

---

### Task 8: 回饋規則 block on account settings

**Files:**
- Modify: `frontend/src/app/components/accounting/account-settings/account-settings.html` (replace the `rules-row` block, L170–182)
- Modify: `frontend/src/app/components/accounting/account-settings/account-settings.ts`
- Modify: `frontend/src/app/components/accounting/account-settings/account-settings.scss`
- Modify: `frontend/src/app/components/accounting/account-settings/account-settings.spec.ts`

**Interfaces:**
- Consumes `RewardRuleSheetComponent` (Task 7), `SwitchComponent` (`components/ui/switch/switch`), `ruleLabel`, `ruleState`, `RULE_STATE_LABELS`, `postingSummary`, `periodText`, `ruleToInput` (Task 6), `AccountingService.updateRewardRule/reorderRewardRules/getAccount/getProjects`.
- Produces in `AccountSettingsComponent`:
  `readonly rules` (exists) now ordered as returned; `readonly projects = signal<Project[]>([])` loaded once in `ngOnInit`; `readonly ruleSheet = signal<{ rule: RewardRule | null } | null>(null)`; `readonly ruleMessage = signal<string | null>(null)`;
  `openRule(rule: RewardRule | null)`, `onRuleSaved()` / `onRuleDeleted()` → `this.service.getAccount(id)` → `detail.set` (the block and the entry form's chips read the same `AccountDetail`), close the sheet;
  `toggleRule(rule: RewardRule, enabled: boolean)` → `updateRewardRule(accountId, rule.id, { ...ruleToInput(rule), is_enabled: enabled })` → re-read; error → `ruleMessage`;
  `moveRule(index: number, delta: number)` → `reorderRewardRules(accountId, swappedIds)` → `detail.update` with the returned list; error → `ruleMessage` + re-read;
  `ruleStateOf(rule)` using `this.today()`;
  `?rule=<id>` on first load of an account: `route.queryParamMap` → when the detail arrives and a rule with that id exists → `openRule(rule)` once, then the param is cleared with `router.navigate([], { relativeTo: this.route, queryParams: { rule: null }, queryParamsHandling: 'merge', replaceUrl: true })` (`relativeTo` is required — without it `[]` resolves from the root and leaves `accounts/:id/settings`; `reports.ts:436` and `reminders.ts:658` are the precedents).
- Template: header row `回饋規則 · N 條` + `<a routerLink="/accounting/reward-rules" [queryParams]="{ account_id: accountId() }">全部卡片</a>`; `accountId() === null` → `<small>儲存帳戶後即可新增回饋規則</small>` and nothing else; rows `.rule-row` with `.rule-label` (`ruleLabel`), `.rule-posting`, `.rule-period`, `.rule-state` (`[attr.data-state]`, class `warn` when `expired`), `.rule-basic` badge, `.rule-moze` badge, `<app-switch class="rule-enabled" ...>`, `.rule-edit`, `.move-up` / `.move-down` (disabled at the ends); `.rule-add` 新增規則 at the end; `@if (ruleSheet(); as open) { <app-reward-rule-sheet ... /> }`.
- The block does **not** take part in `isDirty()` (rule edits save immediately; the sheet guards its own input).
- Builder's call (§10 left it open): 上移 / 下移 buttons on every width, no drag and no separate phone reorder mode — the settings page's 分類 / 專案 rows already reorder this way.

- [ ] **Step 1: Write the failing specs** (append to `account-settings.spec.ts`; extend `DETAIL.reward_rules` literals with the fields the helpers read: `posting`, `post_month_offset`, `post_day`, `delay_days`, `starts_on`, `ends_on`, `is_basic`, `moze_id`, `sort_order`):
  (a) renders one `.rule-row` per rule with label 一般 (1%), state badge 啟用中 / 已停用, and 來自 MOZE for `moze_id`;
  (b) new-account mode shows 儲存帳戶後即可新增回饋規則 and no 新增規則 button;
  (c) 啟用 switch off → PUT `/api/accounting/accounts/11/reward-rules/1` with `is_enabled: false` and the full body, then GET `/api/accounting/accounts/11` refreshes the list;
  (d) 下移 on the first row → PUT `/accounts/11/reward-rules/order` `{ ids: [2, 1] }`, rows re-rendered from the response;
  (e) 編輯 opens `app-reward-rule-sheet` with the rule; 新增規則 opens it with `rule === null`;
  (f) `?rule=2` in the query params opens the sheet for rule 2 after the detail loads and `router.navigate` is called with `[]`, `relativeTo` the component's route and `queryParams.rule === null` (the path must stay on the settings page);
  (h) rewrite the existing assertion at `account-settings.spec.ts:171` (`['一般 1%', '餐飲 5.2%（停用）']` on `.rule-item`): the rows are now `.rule-row` with `.rule-label` 一般 (1%) / 餐飲 (5.2%) and a `.rule-state` badge 啟用中 / 已停用 — the 「（停用）」 suffix and the component's own `ruleLabel` method go away;
  (g) an expired rule (`ends_on` before the frozen 2026-10-02) shows `.rule-state.warn` 已到期.

- [ ] **Step 2: Run to verify failure** — `npx vitest run src/app/components/accounting/account-settings`.

- [ ] **Step 3: Implement** per Interfaces; delete the `(2b 起可編輯)` placeholder and the old `ruleLabel` method on the component (import the shared one). Styles: the row grid reuses `.rw`/`.k`/`.v` conventions; badges `.badge`, `.badge.warn`.

- [ ] **Step 4: Run to verify pass** — the spec file + `npx vitest run src/app/components/accounting/entry-form` (unchanged chips still green) + `npm run build` (account-settings style budget: file is 138 lines today, stay under 16 kB).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/account-settings
git commit -m "feat(rewards): 回饋規則 block with editor, 啟用 switch and reorder on account settings"
```

---

### Task 9: 回饋規則總覽 page + route + 設定 link

**Files:**
- Create: `frontend/src/app/components/accounting/reward-rules/reward-rules.ts`, `.html`, `.scss`, `.spec.ts`
- Modify: `frontend/src/app/components/accounting/accounting-pages.ts` (add `'rewardRules'` to `AccountingPage` and `ACCOUNTING_PAGES`)
- Modify: `frontend/src/app/app.routes.ts` (`{ path: 'reward-rules', loadComponent: ACCOUNTING_PAGES.rewardRules }` in **both** `ACCOUNTING_PHONE_ROUTES` and `ACCOUNTING_WIDE_ROUTES`, beside `reports`; extend the header comment: `/accounting/reward-rules` must be in Caddy's `@hub_spa`)
- Modify: `frontend/src/app/components/accounting/accounting-settings/accounting-settings.html` (in the 資料 list, after the 專案 `<details>`: `<a class="srow reward-rules-link" routerLink="/accounting/reward-rules"><span><i class="pi pi-gift" aria-hidden="true"></i></span><span class="t">回饋規則</span><span class="r">總覽</span></a>`; add `RouterLink` to the component's imports if missing)
- Modify: `frontend/src/app/components/accounting/accounting-settings/accounting-settings.spec.ts` (one assertion: the link's `href` is `/accounting/reward-rules`)

**Interfaces:**
- Consumes `AccountingService.getRewardRuleOverview` (Task 6), helpers (Task 6), `PageHeaderComponent` (`title="回饋規則總覽"`, `[back]="true"`), the `accountsChanged` signal of `AccountingService` (`accounting.service.ts:85`; reload inside an `effect` the way `accounts.ts:217` does, so a rule edited on a settings pane refreshes the overview).
- Produces `RewardRulesOverviewComponent` (`selector: 'app-reward-rules'`): query params `state` (default `active`), `account_id` (repeatable), `archived` (`1`); signals `overview`, `loading`, `error`, `filter`; computed `callout = counts.enabled_expired > 0`; `setFilter(state)` → `router.navigate([], { relativeTo: this.route, queryParams: { state }, queryParamsHandling: 'merge', replaceUrl: true })`; the callout button 「N 條規則啟用中但已到期，記帳時不會出現」 calls `setFilter('expired')`.
  Template: `app-page-header`; `@if (callout)` `<button class="rr-callout" role="status">`; filter chips `.rr-filter` (啟用中 / 已到期 / 尚未開始 / 已停用 / 全部) with `aria-pressed`; 顯示封存 checkbox `.rr-archived`; one `<section class="rr-card">` per account with `<h2>` name (+ 已封存), rows `.rr-rule` → `<a [routerLink]="['/accounting/accounts', a.account_id, 'settings']" [queryParams]="{ rule: r.id }">` wrapping label / posting / period / state badge, and a `.rr-stats` line `綁定 N 筆（近 12 月 M）· 已入帳 K 筆 · 合計 X CUR[, Y CUR] · 最近 yyyy/MM/dd` (`formatMoney(amount, currency)` per entry; omit 合計 / 最近 when empty / null); account without rules → `尚無規則 → <a ... settings>新增</a>`; no accounts → `尚無回饋規則`; loading → `aria-busy`; error → `writeErrorMessage` in `role="alert"`.

- [ ] **Step 1: Write the failing specs** — (a) default GET `/api/accounting/reward-rules?state=active` renders one card per account and a row per rule with 綁定 / 已入帳 text; (b) `counts.enabled_expired = 31` shows the callout with that number and clicking it requests `state=expired`; (c) `counts.enabled_expired = 0` → no callout; (d) 顯示封存 adds `include_archived_accounts=true`; (e) empty `accounts` → 尚無回饋規則; a card with `rules: []` → 尚無規則; (f) the row's link has `href` `/accounting/accounts/11/settings?rule=2`; (g) `accounting-settings.spec.ts`: the 回饋規則 link exists.

- [ ] **Step 2: Run to verify failure** — `npx vitest run src/app/components/accounting/reward-rules src/app/components/accounting/accounting-settings`.

- [ ] **Step 3: Implement the page, the page registry, both route tables and the 設定 link**

- [ ] **Step 4: Run to verify pass** — those specs, `npx vitest run src/app/app.routes` (if a routes spec exists) and `npm run build`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/reward-rules frontend/src/app/components/accounting/accounting-pages.ts frontend/src/app/app.routes.ts frontend/src/app/components/accounting/accounting-settings
git commit -m "feat(rewards): 回饋規則總覽 page at accounting/reward-rules"
```

---

### Task 10: whole-stack check

**Files:** none new.

- [ ] **Step 1:** `cd services/accounting-service && pytest -q` (full suite, env vars as above) — PASS, count noted in the ledger.
- [ ] **Step 2:** `cd frontend && npm test -- --run && npm run build` — PASS, no budget error.
- [ ] **Step 3:** `grep -rn "2b 起可編輯\|_upsert_rules\|orphaned_rules\|unsupported_rules" services/accounting-service/app frontend/src` → no hits.
- [ ] **Step 4:** `alembic heads` (from `services/accounting-service` with the test DB env) prints `c7a2e9f4b1d8` only.
- [ ] **Step 5:** No commit unless something was fixed; then `fix: …`.

---

## Appendix A — Rollout (owner / ops session, after merge; not an SDD task)

1. API deploy from the release checkout (no migration). Smoke with the hermes token, counts only:
   `curl -s -H "Authorization: Bearer $(cat ~/.config/homehub-hermes-token)" localhost:8000/reward-rules | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d["accounts"]), d["counts"])'`.
   Repeat it with `?state=all&include_archived_accounts=true` (the default scope hides archived cards, so a 500 on an archived card's rule — e.g. a blank `starts_on` — only shows here):
   `curl -s -H "Authorization: Bearer $(cat ~/.config/homehub-hermes-token)" 'localhost:8000/reward-rules?state=all&include_archived_accounts=true' | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d["accounts"]), d["counts"])'`.
   Before step 4, a read-only check (counts only; never paste owner data anywhere): legacy rows whose `rate` a whole-row PUT (edit, 啟用 switch, 停用) would refuse with 422 on `rate`:
   `select id, name, rate from reward_rule where rate is not null and (rate <= 0 or rate > 100)` — report the row count; the owner fixes those rules' rate in the sheet before touching them otherwise.
2. Caddy: in `~/workspace/vaultwarden/Caddyfile` line 53 add `reward-rules` to the `/accounting/(accounts|…|reports)` alternation → `…|reports|reward-rules)`; `docker exec caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile && docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile`.
3. SPA release per the standing recipe (stop preview 18081 first); probe `/hub/accounting/reward-rules` → 200 and the page renders the callout.
4. Hermes skill text (outside git; the PR body quotes it):

   **`references/accounting-api.md`** — in "Reads": extend the `/accounts/{id}/reward-rules` mention with `; GET /reward-rules?account_id=(repeatable)&state=(active|disabled|expired|upcoming|all)&include_archived_accounts= -> {accounts:[{account_id,account_name,rules:[rule+state+stats]}],counts}`. In "Writes" add:

   ```
   - Reward rules (write scope, 2026-11): POST /accounts/{id}/reward-rules (RewardRuleIn: name*, method* percent|fixed, rate (percent, 0<r<=100) | fixed_amount (fixed, >0), posting* after_window|after_transaction|manual, post_month_offset 0-2 + post_day 1-31 (after_window), delay_days 0-365 (after_transaction), txn_rounding/total_rounding keep|round|floor|ceil, total_cap, is_basic, reward_account_id, reward_project_id, starts_on, ends_on, is_enabled, description, sort_order, share_cap_with = null (keep) | "none" (leave) | <same-account rule id> (join)) -> 201 rule.
     PUT /accounts/{id}/reward-rules/{rule_id} (same body, whole row; moze_id never changes) ; PUT /accounts/{id}/reward-rules/order {"ids":[every rule of the account once]} ; DELETE /accounts/{id}/reward-rules/{rule_id} -> 204, or 409 rule_referenced {"entries":n,"rewards":m} -> set is_enabled false instead.
     409 basic_rule_overlap: rule <id> = two enabled 基本 rules of one card overlap in time; end the old one (ends_on) or start the new one later.
   ```

   New section **"## 回饋規則 habits (2026-11)"**:

   ```
   1. Recording a card purchase: GET /accounts/{id}/reward-rules once per session per card. Keep only rules with is_enabled true AND starts_on <= entry_date <= ends_on (null = open); the API refuses the rest with 422 on reward_rule_ids. Pick the rule(s) the merchant / category most plausibly earns under (the is_basic rule when nothing better fits); send them as reward_rule_ids; tell the owner in one line which chip was attached, e.g. 「已掛 國內 (1%)」. If the owner corrects it, PUT /entries/{id} with the full body and the corrected ids, then remember the pair 「商家/類別 → 規則名稱」 for that card. Unknown card or no plausible rule: attach nothing and say so.
   2. Defining or fixing a card's rewards from the owner's words: translate to RewardRuleIn rows (or PUTs that extend ends_on), show the table back (名稱 / 方式 / 入帳 / 期間 / 基本), then write one by one and report the ids. 「延長到年底」 = PUT with ends_on 2026-12-31 and every other field as read.
   3. Points accounts (小樹點, 哩程): when the owner states a current balance, POST /balance-adjustments {account_id, target_balance, entry_date, description:"核對"}; never guess a balance.
   4. Never disable or delete a rule without the owner asking; prefer is_enabled false (停用) to DELETE.
   ```

   **`SKILL.md`** "Write Operations and Safety" list, accounting bullet: append `reward rules (create / edit / reorder / disable; delete only when unreferenced)`.

   Test: ask Hermes to list one card's rules; it should answer from `GET /accounts/{id}/reward-rules` with names and states.
5. Owner data pass (owner, by hand or through Hermes; no script): from the overview's 已到期 filter extend or clear `ends_on` on rules still in force and 停用 dead ones; then define the rule-less cards.
6. Closeout issues: phase 2 engine design, phase 3 guess memory, anything the review parks.

Rollback: SPA → previous publisher record; API → previous release checkout; Caddy line revert + reload.
