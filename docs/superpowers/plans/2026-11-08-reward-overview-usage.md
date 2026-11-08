# Reward overview usage (polish-reward-and-account-settings PR A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `GET /reward-rules` returns, per account, the current cap usage and the header fields the overview cards need, and every usage row (also on `GET /accounts/{id}/reward-usage`) says which account the reward is paid into and whether that is money.

**Architecture:** Additive API change in accounting-service only. `reward_engine.usage` gains three unit fields per row; `reward_rule_service.overview` calls `reward_engine.usage` once per listed account and adds `icon`, `logo`, `logo_hash`, effective `closing_day`. No migration, no SPA change (PR B consumes it).

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Pydantic 2, pytest against the shared Postgres test DB.

**Spec:** `openspec/changes/polish-reward-and-account-settings/` — `design.md` D1 and D1a, `specs/accounting-ledger/spec.md` (requirements "Reward overview usage", "Reward overview account header fields", "Reward usage unit"), `tasks.md` 1.1, 1.1a, 1.2.

## Global Constraints

- `usage` on `GET /reward-rules` SHALL equal `GET /accounts/{id}/reward-usage` for the window containing today (`ledger_service._today()`), whatever `state` and `account_id` select; `[]` without active rules.
- Header fields: `icon`, `logo`, `logo_hash` exactly as `GET /accounts` returns them; `closing_day` = `reward_engine.card_closing_day(db, account)` (own, else combined parent's, else null).
- Unit fields on every usage row: `reward_account_id`, `reward_account_name`, `reward_is_money`, taken from the row's first rule in rule order (the `rule` the `usage` loop is on when it emits the row). Unset `reward_account_id` means the card itself. `reward_is_money` is true when the resolved account is the card itself or `is_credit`, archived or not.
- Additive only: no field renamed or removed; existing callers (SPA account settings, Hermes) keep working.
- Existing gate: `test_overview_is_grouped_not_per_rule` median < 0.5 s must still pass with usage included.
- Test commands (from `services/accounting-service`): `PYTHONPATH=.:../shared-python-lib .venv/bin/python -m pytest -q -p no:cacheprovider <path>`; pipe output through `sed -E 's#postgresql[^ ]*#<url>#g'`. Never run alembic or uvicorn from the worktree.
- Tests use fictional names and amounts only.

## Review Focus

1. Archived points account as reward account with the default overview (archived accounts not listed) → row still says `reward_is_money` false with the account's name (Task 1 `test_usage_unit_archived_points_account`).
2. Overview filtered with `?account_id=<card>` while the points account is not in the list → unit unchanged (Task 2 `test_overview_usage_ignores_filters`).
3. Shared-cap group whose first rule pays points and second pays the card → one row, points unit (Task 1 `test_usage_unit_comes_from_the_groups_first_rule`).
4. Perf: 20 cards × 5 active rules now run `usage` per card → existing 0.5 s gate (Task 2 Step 4).
5. Card with no own closing day under a combined parent → parent's day; plain account → null (Task 2 `test_overview_closing_day_is_effective`).

---

### Task 1: Unit fields on usage rows

**Files:**
- Modify: `app/services/reward_engine.py` (`usage`, ~line 936)
- Modify: `app/schemas/ledger.py` (`RewardUsageOut`, ~line 215)
- Test: `tests/integration/test_reward_usage_api.py`

**Interfaces:**
- Produces: each dict from `reward_engine.usage(db, account_id, on)` gains `"reward_account_id": int`, `"reward_account_name": str`, `"reward_is_money": bool`; `RewardUsageOut` gains `reward_account_id: int`, `reward_account_name: str`, `reward_is_money: bool`.

- [ ] **Step 1: Write the failing tests** (reuse the file's `_rule`, `_usage`, `_card_with_spends`; all rules `calendar_month`, `date="2026-11-10"`)

```python
def _unit(item):
    return item["reward_account_id"], item["reward_account_name"], item["reward_is_money"]


def test_usage_unit_card_itself_and_unset(client, db_session, seed):
    card, _ = _card_with_spends(seed, db_session)
    _rule(seed, card, name="A")                                   # seed default: reward_account_id = card
    _rule(seed, card, name="B", reward_account_id=None)
    db_session.commit()
    assert [_unit(i) for i in _usage(client, card, date="2026-11-10")] == [(card.id, "測試卡", True)] * 2


def test_usage_unit_other_credit_card_is_money(client, db_session, seed):
    card, _ = _card_with_spends(seed, db_session)
    other = seed.account("另一張卡", is_credit=True)
    _rule(seed, card, reward_account_id=other.id)
    db_session.commit()
    assert [_unit(i) for i in _usage(client, card, date="2026-11-10")] == [(other.id, "另一張卡", True)]


def test_usage_unit_archived_points_account(client, db_session, seed):
    card, _ = _card_with_spends(seed, db_session)
    miles = seed.account("測試里程", is_archived=True)
    _rule(seed, card, reward_account_id=miles.id)
    db_session.commit()
    assert [_unit(i) for i in _usage(client, card, date="2026-11-10")] == [(miles.id, "測試里程", False)]


def test_usage_unit_comes_from_the_groups_first_rule(client, db_session, seed):
    card, _ = _card_with_spends(seed, db_session)
    miles = seed.account("測試里程")
    group = uuid4()
    _rule(seed, card, name="先", shared_cap_id=group, total_cap=Decimal("100"), sort_order=0, reward_account_id=miles.id)
    _rule(seed, card, name="後", shared_cap_id=group, total_cap=Decimal("100"), sort_order=1)
    db_session.commit()
    [item] = _usage(client, card, date="2026-11-10")
    assert _unit(item) == (miles.id, "測試里程", False)
```

- [ ] **Step 2: Run them, expect FAIL** (`KeyError: 'reward_account_id'`)

Run: `… pytest tests/integration/test_reward_usage_api.py -k unit`

- [ ] **Step 3: Implement**

In `usage`, resolve once per emitted row: `payee = db.get(Account, rule.reward_account_id or account_id)`; add `reward_account_id=payee.id`, `reward_account_name=payee.name`, `reward_is_money=(payee.id == account_id or payee.is_credit)`. Add the three fields to `RewardUsageOut` after `remaining`. Update the `usage` docstring with one line on the unit.

- [ ] **Step 4: Run the whole file, expect PASS**

Run: `… pytest tests/integration/test_reward_usage_api.py` — all pass (existing tests compare selected keys, not whole dicts; if one compares a whole row, add the three keys to its expectation).

- [ ] **Step 5: Commit** — `feat(accounting): reward usage rows carry their reward unit (#323 D1a)`

---

### Task 2: Usage and header fields on the overview

**Files:**
- Modify: `app/services/reward_rule_service.py` (`overview`, ~line 314)
- Modify: `app/schemas/ledger.py` (`RewardRuleOverviewAccountOut`, ~line 71)
- Test: `tests/integration/test_reward_rules_overview.py`
- Modify: `openspec/changes/polish-reward-and-account-settings/tasks.md` (tick 1.1, 1.1a, 1.2)

**Interfaces:**
- Consumes: Task 1's `reward_engine.usage` rows; `reward_engine.card_closing_day(db, account) -> int | None`; `account_logo_service.HASH_PREFIX` (16).
- Produces: each overview account dict gains `"icon": str | None`, `"logo": str | None`, `"logo_hash": str | None`, `"closing_day": int | None`, `"usage": list[dict]`; `RewardRuleOverviewAccountOut` gains the same with `usage: list[RewardUsageOut]`.

- [ ] **Step 1: Write the failing tests** (file's `_freeze` pins today to 2026-11-01)

```python
def test_overview_usage_matches_reward_usage(client, db_session, seed, monkeypatch):
    _freeze(monkeypatch)
    card = seed.account("測試卡", is_credit=True)
    rule = seed.rule(card, name="一般", window="calendar_month", total_cap=Decimal("500"))
    spend = seed.entry(card, "-1000", day=date(2026, 11, 1))
    db_session.add(RewardAccrual(source_entry_id=spend.id, rule_id=rule.id, window_start=date(2026, 11, 1),
                                 window_end=date(2026, 11, 30), post_on=date(2026, 12, 5), estimate=Decimal("64"),
                                 amount=Decimal("64"), state="estimated"))
    db_session.commit()
    [account] = client.get("/reward-rules").json()["accounts"]
    assert account["usage"] == client.get(f"/accounts/{card.id}/reward-usage").json()
    assert [Decimal(account["usage"][0][k]) for k in ("estimated", "cap", "remaining")] == [64, 500, 436]


def test_overview_usage_ignores_filters(client, db_session, seed, monkeypatch):
    _freeze(monkeypatch)
    card = seed.account("測試卡", is_credit=True)
    miles = seed.account("測試里程")
    seed.rule(card, name="生效", window="calendar_month", reward_account_id=miles.id)
    seed.rule(card, name="過期", starts_on=date(2025, 1, 1), ends_on=date(2026, 10, 31))
    db_session.commit()
    full = client.get("/reward-rules").json()["accounts"]
    expected = next(a for a in full if a["account_id"] == card.id)["usage"]
    assert [u["name"] for u in expected] == ["生效"] and expected[0]["reward_is_money"] is False
    for params in ({"state": "expired"}, [("account_id", card.id)]):
        [account] = [a for a in client.get("/reward-rules", params=params).json()["accounts"]
                     if a["account_id"] == card.id]
        assert account["usage"] == expected


def test_overview_usage_empty_without_active_rules(client, db_session, seed, monkeypatch):
    _freeze(monkeypatch)
    card = seed.account("測試卡", is_credit=True)
    bare = seed.account("空卡", is_credit=True)
    seed.rule(card, name="停用", enabled=False)
    db_session.commit()
    body = client.get("/reward-rules").json()
    assert [a["usage"] for a in body["accounts"]] == [[], []]


def test_overview_header_fields_match_accounts(client, db_session, seed):
    card = seed.account("測試卡", is_credit=True, icon="💳", logo="esun")
    plain = seed.account("錢包")
    db_session.add(AccountLogoImage(account_id=card.id, png=b"\x89PNG", sha256="ab" * 32,
                                    source_media_type="image/png", source_bytes=4))
    db_session.commit()
    listed = {a["id"]: a for a in client.get("/accounts").json()}
    for account in client.get("/reward-rules").json()["accounts"]:
        mine = listed[account["account_id"]]
        assert (account["icon"], account["logo"], account["logo_hash"]) == (mine["icon"], mine["logo"], mine["logo_hash"])
    assert listed[card.id]["logo_hash"] == "ab" * 8 and listed[plain.id]["logo_hash"] is None


def test_overview_closing_day_is_effective(client, db_session, seed):
    parent = seed.account("主卡", is_credit=True, closing_day=25)
    child = seed.account("附卡", is_credit=True, combined_account_id=parent.id)
    plain = seed.account("錢包")
    db_session.commit()
    days = {a["account_id"]: a["closing_day"] for a in client.get("/reward-rules").json()["accounts"]}
    assert (days[parent.id], days[child.id], days[plain.id]) == (25, 25, None)
```

Imports to add: `from decimal import Decimal`, `from app.models import AccountLogoImage, RewardAccrual`. If `GET /accounts` returns a wrapper rather than a list, index into it the way `tests/integration/test_accounts_api.py` does.

- [ ] **Step 2: Run them, expect FAIL** (`KeyError: 'usage'` / `'icon'`)

Run: `… pytest tests/integration/test_reward_rules_overview.py`

- [ ] **Step 3: Implement**

- Schema: add to `RewardRuleOverviewAccountOut` after `is_archived`: `icon: str | None`, `logo: str | None`, `logo_hash: str | None`, `closing_day: int | None`, `usage: list[RewardUsageOut]` (move or forward-reference `RewardUsageOut` so it is defined first).
- Service: one query for logo hashes of the listed ids — `select(AccountLogoImage.account_id, func.substr(AccountLogoImage.sha256, 1, HASH_PREFIX)).where(AccountLogoImage.account_id.in_(ids))` — then per account add `icon`, `logo`, `logo_hash` (from that map, else None), `closing_day=reward_engine.card_closing_day(db, a)`, `usage=reward_engine.usage(db, a.id, today)`. `today` is the value `overview` already takes from `ledger_service._today()`. Import `reward_engine` lazily inside `overview` if a module-level import cycles (`reward_engine` imports `reward_rule_service`).

- [ ] **Step 4: Run the file, expect PASS, including the perf gate**

Run: `… pytest tests/integration/test_reward_rules_overview.py` — all pass; `test_overview_is_grouped_not_per_rule` median < 0.5 s. If the gate fails, do not loosen it: batch the per-account `usage` work (one `_window_rows` call for all `(rule_id, window_start)` pairs) and report what changed.

- [ ] **Step 5: Run the reward suites**

Run: `… pytest tests/integration/test_reward_usage_api.py tests/integration/test_reward_rules_api.py tests/integration/test_reward_rules_overview.py tests/integration/test_accounts_api.py tests/integration/test_reward_engine_hooks.py` — all pass.

- [ ] **Step 6: Tick spec tasks 1.1, 1.1a, 1.2** in `openspec/changes/polish-reward-and-account-settings/tasks.md`; `openspec validate polish-reward-and-account-settings --strict` passes.

- [ ] **Step 7: Commit** — `feat(accounting): reward overview carries usage and card header fields (#323 D1)`

---

## Verification before PR

- Full accounting suite (≈30 min): `… pytest tests` — record the pass count in the PR body.
- PR body: what changed (additive fields), no migration, rollout = API + worker checkout to the merge sha (same script as `~/reports/home-hub-smallfix334-release-20261108T091250Z/deploy-accounting.sh`), then a live read of `GET /reward-rules` showing `usage` present and a points rule with `reward_is_money` false (counts only, no amounts in chat).

## Rollout notes (outside the repo, after merge)

- Hermes skill `references/accounting-api.md`: add the new fields to the `/reward-rules` and `/reward-usage` descriptions (one line each).
- SPA unchanged until PR B; it ignores unknown fields.
