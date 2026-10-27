# Accounting Notifications (扣款提醒 + 週報) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only notifier package `services/accounting-service/notify/` that renders the daily debit reminder and the Saturday digest from the accounting API and delivers them to Discord through `hermes send`, plus the `opc` user timers that run it.

**Architecture:** Pure functions over API payloads (`cycles`, `debits`, `digest`, `dedupe`) are fed by one GET-only client (`api`) and rendered to Discord text (`render`); `cli` wires fetch → compute → dedupe → render → `send` and owns exit codes. State is one JSON file; config is one TOML file; nothing writes to the API. Mirrors `worker/` (config from a file/env, `python -m notify <cmd>`, tests under `tests/notify/`).

**Tech Stack:** Python 3.13, `httpx==0.28.1` (already pinned for the worker), stdlib `tomllib`/`json`/`datetime`/`decimal`/`subprocess`, `pytest` with `httpx.MockTransport` fakes, systemd user units.

**Spec:** `docs/superpowers/specs/2026-10-27-accounting-notifications-design.md` (rev 2; plan rev 2 folds Multica AGENT-314).

## Global Constraints

- GET only. The notifier never calls a non-GET route; `api.ReadClient` has no method that could.
- Token label `notify`, scope `read`, read from `token_file` (default `~/.config/homehub-notify-token`); the token value never appears in logs, messages, exceptions or test output.
- Config file `~/.config/homehub-notify.toml` (keys exactly as spec §4); the repo ships `deploy/notify/homehub-notify.example.toml` with placeholders only.
- No owner financial data anywhere in the repo: fixtures use fictional account names (薪轉戶 A, 旅遊卡, …) and amounts.
- Asia/Taipei dates; "today" = `--as-of` or `datetime.now(ZoneInfo("Asia/Taipei")).date()`.
- Business days only from `GET /calendar/business-days`; no local holiday table.
- Discord message ≤ 2000 characters; split on block boundaries, never inside a line.
- Exit codes: 0 sent or nothing to send, 1 failure, 2 bad arguments. `--dry-run` never sends and never writes state.
- Weekly digest always sent; daily only when at least one rendered item. State is written on **every** exit 0 (also a quiet daily run); never on exit 1.
- Capped reads are failures (spec §7): page `/entries`; a full page of audit/inbox/cases whose oldest row is still inside the needed window → failure message, exit 1. A due date after `calendar.last_known_day` → failure.
- "Today" = `ZoneInfo("Asia/Taipei")`; the units set `Environment=TZ=Asia/Taipei`.
- Copy strings exactly as spec §5.5 / §5.7 / §6.2 (reason wording table, 「📄 帳單未到 …」, 「✅ 本週沒有待處理 · 最近一次每日檢查 MM/DD HH:MM」, 「❌ 帳務通知失敗：…」).
- No new DB tables, no API or frontend changes.

## Review Focus

1. Today is a Saturday or holiday: the window still counts business days strictly after today (Sat→Tue = 2), so a Tuesday debit is reminded Saturday 07:00 — Task 1 `test_business_days_between_from_weekend`.
2. Payment made from a 副卡 only: counts for the master's statement — Task 4 `test_paid_by_child_card`.
3. Closing day 31 in February with `fixed_day` 31: clamps to 02-28 and the due date clamps too — Task 1 `test_due_date_clamps_short_month`.
4. One source account feeding a TWD card and a USD card: the USD item shows 「幣別不同」 and is left out of the TWD projection — Task 4 `test_mixed_currency_source`.
5. API returns 401 (token rotated): one 「❌ 帳務通知失敗」 message without the token or response body, exit 1, state untouched — Task 8 `test_api_failure_sends_failure_message`.
6. A digest longer than 2000 characters: split between blocks, every chunk under the limit, order preserved — Task 6 `test_split_on_block_boundary`.
7. A statement the rule cycle did not expect (re-issued bill, odd period) still debits: every live card statement with a due date is projected — Task 4 `test_unexpected_statement_still_projected`.
8. A pending scheduled income on the source account must not inflate the projection: only negative lines count — Task 4 `test_projection_and_shortfall`.

---

### Task 1: Cycle rules and business-day counting

**Files:**
- Create: `services/accounting-service/notify/__init__.py` (empty), `services/accounting-service/notify/cycles.py`
- Test: `services/accounting-service/tests/notify/__init__.py` (empty), `services/accounting-service/tests/notify/test_cycles.py`

**Interfaces:**
- Produces:
  - `Period = tuple[date, date]` (start, end)
  - `statement_period(closing_day: int | None, today: date) -> Period` — port of `cycle.ts statementPeriod`
  - `shift_period(closing_day: int | None, period: Period, delta: int) -> Period` — port of `shiftPeriod`
  - `due_date(closing_date: date, rule: str | None, value: int | None) -> date | None` — port of `dueDate` (`fixed_day` / `days_after_closing`)
  - `last_closed_period(closing_day: int | None, today: date) -> Period` = `shift_period(closing_day, statement_period(closing_day, today), -1)`
  - `Calendar = frozenset[date]` holidays + `frozenset[date]` workdays wrapped in `@dataclass(frozen=True) class Calendar: holidays; workdays; last_known_day: date`
  - `is_business_day(day: date, cal: Calendar) -> bool` (weekend unless in `workdays`; `holidays` closed; `workdays` wins)
  - `next_business_day(day: date, cal: Calendar) -> date` (first business day on/after `day`)
  - `business_days_between(today: date, debit_day: date, cal: Calendar) -> int` (count of business days in `(today, debit_day]`; 0 when equal; negative inputs raise `ValueError`)
  - `class CalendarExhausted(Exception)` raised by `next_business_day`/`business_days_between` when any day consulted is after `cal.last_known_day`

- [ ] **Step 1: Write the failing tests** — port every case of `frontend/src/app/components/accounting/cycle.spec.ts` verbatim (dates as `date(2026, 10, 2)` etc.), plus:

```python
def test_due_date_clamps_short_month():
    assert due_date(date(2026, 1, 31), "fixed_day", 31) == date(2026, 2, 28)
    assert statement_period(31, date(2026, 2, 10)) == (date(2026, 2, 1), date(2026, 2, 28))

def test_business_days_between_from_weekend():
    cal = Calendar(holidays=frozenset({date(2026, 10, 26)}), workdays=frozenset(), last_known_day=date(2027, 12, 31))
    assert business_days_between(date(2026, 10, 24), date(2026, 10, 27), cal) == 1   # Sat → Tue over the 10-26 holiday
    assert business_days_between(date(2026, 10, 23), date(2026, 10, 26), cal) == 0   # Fri → holiday Mon: no business day in between
    assert business_days_between(date(2026, 10, 24), date(2026, 10, 24), cal) == 0
    assert next_business_day(date(2026, 10, 24), cal) == date(2026, 10, 27)
    with pytest.raises(ValueError):
        business_days_between(date(2026, 10, 27), date(2026, 10, 26), cal)

def test_calendar_exhausted():
    cal = Calendar(holidays=frozenset(), workdays=frozenset(), last_known_day=date(2027, 12, 31))
    with pytest.raises(CalendarExhausted):
        next_business_day(date(2028, 1, 1), cal)

def test_workday_wins_over_weekend():
    cal = Calendar(holidays=frozenset(), workdays=frozenset({date(2026, 10, 31)}), last_known_day=date(2027, 12, 31))
    assert is_business_day(date(2026, 10, 31), cal) is True
```

Note the second `business_days_between` case: 10-26 is a holiday, so `(10-23, 10-26]` holds no business day; `next_business_day(10-26)` is 10-27 and the caller always passes a debit day that is itself a business day.

- [ ] **Step 2: Run** `cd services/accounting-service && PYTHONPATH=.:../shared-python-lib .venv/bin/python -m pytest -q -p no:cacheprovider tests/notify/test_cycles.py` — expected: ImportError.
- [ ] **Step 3: Implement `notify/cycles.py`** with the signatures above. `statement_period`/`shift_period`/`due_date` follow `cycle.ts` line for line (month clamp via `calendar.monthrange`).
- [ ] **Step 4: Run the tests** — expected: all pass.
- [ ] **Step 5: Commit** `feat(notify): cycle rules and business-day counting`

### Task 2: Config and state files

**Files:**
- Create: `notify/config.py`, `notify/state.py`, `deploy/notify/homehub-notify.example.toml`
- Test: `tests/notify/test_config.py`, `tests/notify/test_state.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class NotifyConfig: api_base: str; token_file: Path; hub_base_url: str; discord_target: str; lead_business_days: int = 2; lead_overrides: dict[int, int]; weekly_open_case_limit: int = 10; expected_statement_grace_days: int = 7; min_buffer: Decimal = Decimal("0"); state_file: Path = ~/.local/state/homehub-notify/state.json`
  - `load_config(path: Path) -> NotifyConfig` — `tomllib`; missing required key → `ConfigError(key)`; `lead_overrides` keys are account ids as strings in TOML, ints in the dataclass; `~` expanded.
  - `read_token(path: Path) -> str` — stripped file content; empty → `ConfigError("token")`.
  - `@dataclass class StatementState: first_notified: date | None` (keys: statement id, or `-card_id` for rule-only items)
  - `@dataclass class SourceState: last_shortfall: Decimal | None`
  - `@dataclass class LastSend: day: date; triggers: frozenset[str]`
  - `@dataclass class State: statements: dict[int, StatementState]; sources: dict[int, SourceState]; skips_seen: set[int]; last_daily_run: datetime | None; last_weekly_run: datetime | None; last_send: LastSend | None` with `State.empty()`
  - `load_state(path: Path) -> State` (missing/corrupt → empty `State`, one stderr line on corrupt) and `save_state(path: Path, state: State) -> None` (atomic: temp file + `os.replace`, mode 0600, parent dir 0700 created).

- [ ] **Step 1: Write the failing tests**

```python
def test_load_config_example(tmp_path):
    cfg = load_config(Path("deploy/notify/homehub-notify.example.toml"))
    assert cfg.lead_business_days == 2 and cfg.discord_target.startswith("discord:")

def test_lead_overrides_keys_are_ints(tmp_path):
    p = tmp_path / "c.toml"; p.write_text(EXAMPLE + '\n[lead_overrides]\n"123" = 4\n')
    assert load_config(p).lead_overrides == {123: 4}

def test_missing_required_key(tmp_path):
    p = tmp_path / "c.toml"; p.write_text('api_base = "http://x"\n')
    with pytest.raises(ConfigError) as e: load_config(p)
    assert "discord_target" in str(e.value)

def test_state_roundtrip_and_corrupt(tmp_path):
    p = tmp_path / "s" / "state.json"
    st = State(statements={7: StatementState(date(2026, 11, 3)), -9: StatementState(date(2026, 11, 2))}, sources={76: SourceState(Decimal("3900"))}, skips_seen={41}, last_daily_run=None, last_weekly_run=None, last_send=LastSend(date(2026, 11, 3), frozenset({"new:7"})))
    save_state(p, st); assert load_state(p) == st and oct(p.stat().st_mode)[-3:] == "600"
    p.write_text("{"); assert load_state(p) == State.empty()
```

- [ ] **Step 2: Run** `pytest -q tests/notify/test_config.py tests/notify/test_state.py` — expected: ImportError.
- [ ] **Step 3: Implement** `notify/config.py`, `notify/state.py` and the example TOML (every key from spec §4 with placeholder values: `discord_target = "discord:000000000000000000"`, `hub_base_url = "https://example.invalid/hub"`).
- [ ] **Step 4: Run the tests** — expected: pass.
- [ ] **Step 5: Commit** `feat(notify): config and state files`

### Task 3: Read-only API client and snapshot

**Files:**
- Create: `notify/api.py`
- Test: `tests/notify/test_api.py`, `tests/notify/fakes.py` (an `httpx.MockTransport` handler serving canned JSON per path)

**Interfaces:**
- Consumes: `cycles.Calendar`.
- Produces:
  - `class ApiError(Exception): status: int; path: str` — message is `f"{status} {path}"`, never the body.
  - `class ReadClient: __init__(self, client: httpx.Client, token: str)`; every method is a GET via `_get(path, params) -> Any` that raises `ApiError` on ≥ 400:
    - `settings() -> dict` (`/settings/reconciliation`)
    - `calendar(date_from: date, date_to: date) -> Calendar` (`/calendar/business-days?from&to`; `last_known_day` kept on the `Calendar`)
    - `accounts(as_of: date) -> list[dict]` (`/accounts?include_archived=false&as_of=`)
    - `statements(account_id: int) -> list[dict]` (`/accounts/{id}/statements`)
    - `entries(account_ids: list[int], kind: str, date_from: date, date_to: date) -> list[dict]` (`/entries` with repeated `account_id`, `limit=500`, paging by `offset` until `len(items) == total`)
    - `pending_instances(until: date) -> list[dict]` (`/schedules/instances?status=pending&until=`; `until` always passed — the default is today + 30)
    - `audit(limit: int = 1000) -> list[dict]` (`/reconciliation/audit?limit=`; newest first)
    - `open_cases(account_id: int) -> list[dict]` (`/reconciliation/cases?status=open&account_id=&limit=1000`)
    - `inbox() -> dict` (`/reconciliation/inbox?limit=1000`)
  - `class CappedRead(ApiError)` raised by `fetch_*` when a capped list came back full and its oldest row is still inside the window the run needs (audit: `created_at >= since`; cases: any full page; inbox: `len(items) == 1000`).
  - `@dataclass(frozen=True) class Snapshot: today: date; settings: dict; calendar: Calendar; accounts: list[dict]; statements: dict[int, list[dict]]; payments: dict[int, list[dict]]; pending: list[dict]; audit: list[dict]; open_cases: dict[int, list[dict]]; inbox: dict | None`
  - `fetch_daily(client: ReadClient, today: date) -> Snapshot` — calendar `today−40 … today+60`; statements for every non-archived credit account; payments = `transfer_in` entries for each master+children group from `today − 120 days` to `today` (paged); pending instances until `today + 60`; audit (cap-checked against `yesterday 06:00`); `open_cases`/`inbox` left `{}`/`None`.
  - `fetch_weekly(client: ReadClient, today: date) -> Snapshot` — as daily plus `inbox` and `open_cases` per inbox account; statements for **every** non-archived account (missing-statement check covers banks too); audit cap-checked against `today − 7`.

- [ ] **Step 1: Write the failing tests** using `fakes.make_client(routes: dict[str, Any])` that returns an `httpx.Client(transport=MockTransport(handler), base_url="http://api")` and records every request:

```python
def test_only_get_and_bearer(fake):
    client, log = fake({"/settings/reconciliation": {"auto_pay": {}}})
    ReadClient(client, "secret-token").settings()
    assert [(r.method, r.headers["authorization"]) for r in log] == [("GET", "Bearer secret-token")]

def test_entries_pages_until_total(fake):
    client, log = fake({"/entries": [{"items": [{"id": 1}], "total": 2, "limit": 1, "offset": 0}, {"items": [{"id": 2}], "total": 2, "limit": 1, "offset": 1}]})
    assert [e["id"] for e in ReadClient(client, "t").entries([3, 4], "transfer_in", date(2026, 7, 1), date(2026, 10, 27))] == [1, 2]
    assert log[0].url.params.get_list("account_id") == ["3", "4"] and log[1].url.params["offset"] == "1"

def test_error_hides_body(fake):
    client, _ = fake({"/accounts": httpx.Response(401, json={"detail": "bad token secret-token"})})
    with pytest.raises(ApiError) as e: ReadClient(client, "secret-token").accounts(date(2026, 10, 27))
    assert "secret-token" not in str(e.value) and e.value.status == 401

def test_fetch_daily_groups_children(fake_daily_routes):
    snap = fetch_daily(ReadClient(*fake_daily_routes), date(2026, 10, 27))
    assert set(snap.payments) == {100}            # master id; the child 104 is folded into it
    assert snap.calendar.holidays == frozenset({date(2026, 10, 26)}) and snap.calendar.last_known_day == date(2027, 12, 31)

def test_full_audit_page_inside_window_is_capped(fake):
    rows = [{"id": 2000 - i, "event": "auto_pay_skipped", "created_at": "2026-10-27T06:00:00+08:00", "detail": {}} for i in range(1000)]
    client, _ = fake(DAILY_ROUTES | {"/reconciliation/audit": rows})
    with pytest.raises(CappedRead): fetch_daily(ReadClient(client, "t"), date(2026, 10, 27))
```

- [ ] **Step 2: Run** — expected: ImportError.
- [ ] **Step 3: Implement `notify/api.py`.** `_get` mirrors `worker/api.py::_call` minus lease logic and minus body echo. Children grouping: `combined_account_id` → master; the master's payment key is its own id.
- [ ] **Step 4: Run the tests** — expected: pass.
- [ ] **Step 5: Commit** `feat(notify): read-only API client and snapshots`

### Task 4: Daily debit items (spec §5.1–§5.5)

**Files:**
- Create: `notify/debits.py`
- Test: `tests/notify/test_debits.py`, `tests/notify/fixtures.py` (builders: `account(...)`, `statement(...)`, `entry(...)`, `instance(...)`, `audit_event(...)`, `snapshot(...)` returning `api.Snapshot` with fictional data)

**Interfaces:**
- Consumes: `cycles.*`, `api.Snapshot`, `config.NotifyConfig`.
- Produces:
  - `@dataclass(frozen=True) class DebitItem: statement_id: int | None; card_id: int; card_name: str; currency: str; amount: Decimal | None; paid: Decimal; due: date | None; debit_day: date | None; from_rule: bool; cls: Literal["auto", "manual"]; source_id: int | None; open_cases: int; overdue_days: int; unpaid_reason: str | None; in_window: bool; note: str | None` (`note` carries 「幣別不同，請自行確認」 / 「到期日未辨識」; `key` property = `statement_id` or `-card_id`)
  - `@dataclass(frozen=True) class SourceBlock: account_id: int; name: str; currency: str; balance: Decimal; projected: Decimal; shortfall: Decimal | None; items: list[DebitItem]`
  - `@dataclass(frozen=True) class Skip: event_id: int; card_name: str; amount: Decimal; reason: str`
  - `@dataclass(frozen=True) class DailyReport: today: date; blocks: list[SourceBlock]; manual: list[DebitItem]; skips: list[Skip]; cleared: list[SourceBlock] = field(default_factory=list)` (`cleared` is filled by Task 5 only)
  - `tests/notify/fixtures.py` also exports the helpers later tasks use: `find(report, card_name) -> DebitItem | None` (searches blocks and manual), `block(report, source_name) -> SourceBlock`, `report(items=..., shortfall=..., skips=..., today=...) -> DailyReport` (one block 薪轉戶 A unless `shortfall`/items say otherwise), `item(statement_id, debit_day=..., **overrides) -> DebitItem`, `skip(event_id) -> Skip`, `weekly_snapshot(**overrides) -> Snapshot`.
  - `build_daily(snap: Snapshot, cfg: NotifyConfig) -> DailyReport`
  - `REASON_TEXT: dict[str, str]` — exactly the spec §5.5 table (`no_link` → 卡片沒有設定扣款帳戶, …).
- Rules the tests pin (all from spec rev 2):
  - one item per card for the last closed period (and the one before, for overdue): statement matched by `period_end` within ±3 days of the cycle end (closest first, then highest id) among `mode == "live" and not retired`; rule-only when none (`from_rule=True`, `amount=None`, `cls="manual"`). A matched statement with `due_date` null → manual item, `note="到期日未辨識"`, `due=debit_day=None`, never projected.
  - **plus** one item per every other live, non-retired card statement with `statement_total > 0` and a due date (its own due/debit day); 帳單未到 only when the cycle had no match.
  - `cls="auto"` iff `auto_pay_account_id` set and `settings["auto_pay"]["enabled"]` and statement present with a due date and `debit_day >= enabled_from` and the link is eligible now: target account exists in `snap.accounts`, not archived, not `is_credit`, `currency == card.currency`. A currency mismatch sets `note="幣別不同，請自行確認"` on the (manual) item.
  - `debit_day = next_business_day(due, cal)`; `in_window = business_days_between(today, debit_day) <= lead(card)`; a due date after `cal.last_known_day` raises `CalendarExhausted` (handled in Task 8 as a failure).
  - payment window = `[period_end, min(today, max(debit_day, next_closing − 1))]`; `paid` = Σ `transfer_in` on master+children with `entry_date` in it. Auto item dropped iff some `transfer_in` on master+children has `amount == statement_total` and `|entry_date − debit_day| <= settings["auto_pay"]["twin_window_days"]`. Manual item dropped iff `paid >= amount`. `amount <= 0` dropped.
  - projection per source: `balance(as_of today)` − Σ **full** `amount` of auto items with `debit_day <= D` − Σ `|line.amount|` over pending instance lines with `line.account_id == source`, `line.amount < 0`, `instance.due_date <= D`; `shortfall = cfg.min_buffer − projected` if positive. D = max debit day among the block's in-window items.
  - overdue: manual item with `debit_day < today` and unpaid → `overdue_days = (today − due).days` (rule-only: kept 30 days after the due date, `note="帳單未到"`); auto item with `debit_day < today` unpaid → `unpaid_reason` = latest non-cutoff skip reason for that statement (or `"unknown"`).
  - skips: `auto_pay_skipped` events with `created_at >= yesterday 06:00 Asia/Taipei` and `reason != "cutoff"`.

- [ ] **Step 1: Write the failing tests** (fictional fixtures; `today = 2026-11-03` Tue, calendar with 2026-11-05 as a holiday):

```python
def test_window_and_debit_day(fx):
    # 旅遊卡: closing 15, fixed_day 5 → due 11-05 (holiday) → debit 11-06 Fri; lead 2 from Tue 11-03: business days (11-03, 11-06] = 11-04, 11-06 → 2 → in window
    rep = build_daily(fx.snapshot(), fx.cfg())
    item = fx.find(rep, "旅遊卡"); assert item.debit_day == date(2026, 11, 6) and item.in_window and item.cls == "auto"

def test_rule_only_when_statement_missing(fx):
    rep = build_daily(fx.snapshot(statements_for={"超市卡": []}), fx.cfg())
    item = fx.find(rep, "超市卡"); assert item.from_rule and item.amount is None and item.cls == "manual" and item in rep.manual

def test_cutoff_uses_debit_day(fx):
    # due Thu 11-05 (holiday) → debit Fri 11-06: enabled_from 11-06 keeps it auto, 11-07 makes it manual
    assert fx.find(build_daily(fx.snapshot(enabled_from=date(2026, 11, 6)), fx.cfg()), "旅遊卡").cls == "auto"
    assert fx.find(build_daily(fx.snapshot(enabled_from=date(2026, 11, 7)), fx.cfg()), "旅遊卡").cls == "manual"
    assert fx.find(build_daily(fx.snapshot(auto_pay_enabled=False), fx.cfg()), "旅遊卡").cls == "manual"

def test_link_predicate_evaluated_now(fx):
    for bad in (dict(is_archived=True), dict(is_credit=True), dict(currency="USD")):
        item = fx.find(build_daily(fx.snapshot(source_overrides={76: bad}), fx.cfg()), "旅遊卡")
        assert item.cls == "manual" and item in build_daily(fx.snapshot(source_overrides={76: bad}), fx.cfg()).manual
    assert fx.find(build_daily(fx.snapshot(source_overrides={76: dict(currency="USD")}), fx.cfg()), "旅遊卡").note == "幣別不同，請自行確認"

def test_paid_by_child_card(fx):
    # auto item: exact-amount transfer_in on the 副卡 within twin_window_days of the debit day → dropped
    snap = fx.snapshot(payments={100: [fx.entry(account_id=104, kind="transfer_in", amount="48200", entry_date=date(2026, 11, 1))]})
    assert fx.find(build_daily(snap, fx.cfg()), "旅遊卡") is None

def test_partial_payment_stays_and_projects_full_total(fx):
    snap = fx.snapshot(payments={100: [fx.entry(account_id=100, kind="transfer_in", amount="10000", entry_date=date(2026, 11, 1))]})
    rep = build_daily(snap, fx.cfg()); item = fx.find(rep, "旅遊卡")
    assert item.paid == Decimal("10000") and item.amount == Decimal("48200") and fx.block(rep, "薪轉戶 A").projected == Decimal("4100")
    # two 24,100 payments sum to the total but are not a twin: the auto item stays
    snap = fx.snapshot(payments={100: [fx.entry(account_id=100, kind="transfer_in", amount="24100", entry_date=d) for d in (date(2026, 10, 20), date(2026, 11, 1))]})
    assert fx.find(build_daily(snap, fx.cfg()), "旅遊卡") is not None

def test_payment_window_stops_before_next_closing(fx):
    # manual card 黑啤卡 (closing 15, fixed_day 6 → due 11-06); a transfer_in on 11-20 belongs to the next window
    snap = fx.snapshot(today=date(2026, 11, 25), payments={93: [fx.entry(account_id=93, kind="transfer_in", amount="4378", entry_date=date(2026, 11, 20))]})
    assert fx.find(build_daily(snap, fx.cfg()), "黑啤卡").overdue_days == 19

def test_unexpected_statement_still_projected(fx):
    extra = fx.statement(account_id=100, total="5000", period_end=date(2026, 9, 30), due_date=date(2026, 11, 4))
    rep = build_daily(fx.snapshot(statements_for={"旅遊卡": [fx.statement_default("旅遊卡"), extra]}), fx.cfg())
    assert fx.block(rep, "薪轉戶 A").projected == Decimal("-900") and len([i for i in rep.blocks[0].items if i.card_name == "旅遊卡"]) == 2

def test_null_due_statement_is_manual_note(fx):
    rep = build_daily(fx.snapshot(statements_for={"旅遊卡": [fx.statement_default("旅遊卡", due_date=None)]}), fx.cfg())
    item = fx.find(rep, "旅遊卡"); assert item.cls == "manual" and item.note == "到期日未辨識" and item.debit_day is None

def test_projection_and_shortfall(fx):
    # 薪轉戶 A balance 52,300; 旅遊卡 48,200 (11-06) + 加油卡 8,000 (11-09: outside D = max in-window debit 11-06 → excluded) → projected 4,100, no shortfall
    blk = fx.block(build_daily(fx.snapshot(), fx.cfg()), "薪轉戶 A"); assert blk.projected == Decimal("4100") and blk.shortfall is None
    # a pending expense line of −8,000 on 11-04 from 薪轉戶 A → projected −3,900, shortfall 3,900; a +9,000 income line is ignored
    pending = [fx.instance(lines=[fx.line(account_id=76, amount="-8000")], due_date=date(2026, 11, 4)), fx.instance(lines=[fx.line(account_id=76, amount="9000")], due_date=date(2026, 11, 4))]
    blk = fx.block(build_daily(fx.snapshot(pending=pending), fx.cfg()), "薪轉戶 A"); assert blk.shortfall == Decimal("3900")

def test_mixed_currency_source(fx):
    rep = build_daily(fx.snapshot(extra_cards=[fx.account(id=9, name="海外卡", currency="USD", auto_pay_account_id=76, closing_day=15, due_rule="fixed_day", due_value=5)], statements_for={"海外卡": [fx.statement(account_id=9, currency="USD", total="150", period_end=date(2026, 10, 15), due_date=date(2026, 11, 5))]}), fx.cfg())
    item = fx.find(rep, "海外卡"); assert item.cls == "manual" and item.note == "幣別不同，請自行確認" and item in rep.manual
    assert fx.block(rep, "薪轉戶 A").projected == Decimal("4100")

def test_overdue_manual_and_unpaid_auto(fx):
    snap = fx.snapshot(today=date(2026, 11, 12), audit=[fx.audit_event(id=5, statement_id=155, reason="over_cap", created_at="2026-11-06T06:00:00+08:00")])
    rep = build_daily(snap, fx.cfg())
    assert fx.find(rep, "黑啤卡").overdue_days == 6 and fx.find(rep, "旅遊卡").unpaid_reason == "over_cap"

def test_rule_only_stays_overdue_for_30_days(fx):
    snap = fx.snapshot(today=date(2026, 11, 20), statements_for={"超市卡": []})
    item = fx.find(build_daily(snap, fx.cfg()), "超市卡"); assert item.from_rule and item.overdue_days == 13 and item.note == "帳單未到"
    assert fx.find(build_daily(fx.snapshot(today=date(2026, 12, 10), statements_for={"超市卡": []}), fx.cfg()), "超市卡") is None

def test_skips_since_yesterday_0600(fx):
    snap = fx.snapshot(audit=[fx.audit_event(id=1, reason="cutoff", created_at="2026-11-03T06:00:00+08:00"), fx.audit_event(id=2, reason="no_link", created_at="2026-11-03T06:00:01+08:00"), fx.audit_event(id=3, reason="twin", created_at="2026-11-02T05:59:00+08:00")])
    assert [s.event_id for s in build_daily(snap, fx.cfg()).skips] == [2]
```

- [ ] **Step 2: Run** `pytest -q tests/notify/test_debits.py` — expected: ImportError.
- [ ] **Step 3: Implement `notify/debits.py`** per the rules above; keep it pure (no I/O).
- [ ] **Step 4: Run the tests** — expected: pass.
- [ ] **Step 5: Commit** `feat(notify): daily debit items and projection`

### Task 5: Dedupe (spec §5.6)

**Files:**
- Create: `notify/dedupe.py`
- Test: `tests/notify/test_dedupe.py`

**Interfaces:**
- Consumes: `debits.DailyReport`, `state.State`.
- Produces: `apply(report: DailyReport, state: State, today: date) -> tuple[DailyReport, State, frozenset[str]]` — the returned report keeps only blocks/manual items/skips that pass a trigger; a block that passes keeps **all** its items and its full projection (spec §5.2/§5.6); the third value is the trigger-key set (`new:<key>`, `today:<key>`, `short:<source>`, `cleared:<source>`, `overdue:<key>`, `skip:<event>`); `State` is the updated copy (`statements` by `item.key`, `sources[source].last_shortfall`, `skips_seen`).
- Block triggers: any item new in window (no `first_notified`); any `debit_day == today`; block shortfall positive (every day); shortfall cleared since `sources[source].last_shortfall` (one 「✅ <source> 已補足」 → `report.cleared`); any item overdue / unpaid_reason / rule-only (every day). The manual block uses the same triggers minus shortfall. Skips: `event_id ∉ skips_seen`. Same-day suppression lives in Task 8 (compares the key set with `state.last_send`).

- [ ] **Step 1: Write the failing tests**

```python
def test_new_then_quiet_then_debit_day(fx):
    rep = fx.report(items=[fx.item(statement_id=1, debit_day=date(2026, 11, 6))], today=date(2026, 11, 3))
    out, st, keys = apply(rep, State.empty(), date(2026, 11, 3)); assert out.blocks and keys == {"new:1"}
    out, st, keys = apply(rep, st, date(2026, 11, 4)); assert not out.blocks and keys == frozenset()
    out, st, keys = apply(fx.report(items=[fx.item(statement_id=1, debit_day=date(2026, 11, 6))], today=date(2026, 11, 6)), st, date(2026, 11, 6)); assert out.blocks and keys == {"today:1"}

def test_shortfall_every_day_then_cleared_once(fx):
    rep = fx.report(items=[fx.item(statement_id=1)], shortfall=Decimal("3900"))
    _, st, _ = apply(rep, State.empty(), date(2026, 11, 3)); out, st, keys = apply(rep, st, date(2026, 11, 4)); assert out.blocks and "short:76" in keys
    out, st, keys = apply(fx.report(items=[fx.item(statement_id=1)], shortfall=None), st, date(2026, 11, 5)); assert out.cleared and not out.blocks and "cleared:76" in keys
    out, st, _ = apply(fx.report(items=[fx.item(statement_id=1)], shortfall=None), st, date(2026, 11, 6)); assert not out.cleared

def test_block_keeps_all_items_when_one_triggers(fx):
    rep = fx.report(items=[fx.item(statement_id=1), fx.item(statement_id=2)])
    _, st, _ = apply(rep, State.empty(), date(2026, 11, 3))
    rep2 = fx.report(items=[fx.item(statement_id=1), fx.item(statement_id=2), fx.item(statement_id=3)])
    out, _, _ = apply(rep2, st, date(2026, 11, 4)); assert len(out.blocks[0].items) == 3 and out.blocks[0].projected == rep2.blocks[0].projected

def test_skip_once(fx):
    rep = fx.report(skips=[fx.skip(event_id=9)])
    out, st, _ = apply(rep, State.empty(), date(2026, 11, 3)); assert out.skips
    out, _, _ = apply(rep, st, date(2026, 11, 4)); assert not out.skips
```

- [ ] **Step 2: Run** — expected: ImportError. **Step 3: Implement `notify/dedupe.py`.** **Step 4: Run** — pass.
- [ ] **Step 5: Commit** `feat(notify): daily dedupe state`

### Task 6: Rendering and splitting

**Files:**
- Create: `notify/render.py`
- Test: `tests/notify/test_render.py`, `tests/notify/golden/daily.txt`, `tests/notify/golden/weekly.txt`, `tests/notify/golden/weekly_empty.txt`

**Interfaces:**
- Consumes: `debits.DailyReport`, `digest.WeeklyReport` (Task 7 — define the dataclass in this task's test as the contract: `WeeklyReport(today, open_cases: list[AccountCases], missing: list[MissingStatement], skips: list[Skip], gaps: Gaps, last_daily_run: datetime | None)`; Task 7 imports it from `notify/digest.py`, so create `notify/digest.py` here with only the dataclasses).
- Produces: `render_daily(rep: DailyReport, cfg: NotifyConfig) -> list[str]`, `render_weekly(rep: WeeklyReport, cfg: NotifyConfig) -> list[str]`, `render_failure(reason: str) -> str`, `split_blocks(blocks: list[str], limit: int = 2000) -> list[str]`, `fmt_amount(d: Decimal) -> str` (`48,200`, no decimals when integral, else two), `weekday_zh(d: date) -> str` (（一）…（日）).
- Daily text = spec §5.7 sample exactly (header `🔔 扣款提醒 YYYY-MM-DD（X）`, block header `**name**  餘額 B → 扣款後 P` + ` ⚠ 缺 S` when shortfall, item lines, `**手動繳**`, `⏭ 自動扣繳跳過`, cleared lines `✅ name 已補足`); links as spec. Weekly text = sections of §6.1 in order with `📋 帳務週報 YYYY-MM-DD` header; empty → spec §6.2 line.

- [ ] **Step 1: Write the failing tests** — golden comparison for the §5.7 sample built from fixtures (fictional data, `hub_base_url="https://example.invalid/hub"`), weekly sample, empty weekly; plus:

```python
def test_split_on_block_boundary():
    blocks = ["a" * 1500, "b" * 1500, "c" * 10]
    out = split_blocks(blocks, limit=2000)
    assert out == ["a" * 1500, "b" * 1500 + "\n\n" + "c" * 10] and all(len(m) <= 2000 for m in out)

def test_oversized_block_is_split_on_lines():
    block = "\n".join(["x" * 100] * 30)   # 3029 chars, no block boundary
    out = split_blocks([block], limit=2000); assert len(out) == 2 and all(len(m) <= 2000 for m in out) and "\n".join(out) == block
```

- [ ] **Step 2: Run** — expected: ImportError. **Step 3: Implement `notify/render.py`** and the dataclass-only `notify/digest.py`. **Step 4: Run** — pass (update golden files only by reading the rendered text and confirming it matches spec §5.7 line by line).
- [ ] **Step 5: Commit** `feat(notify): Discord rendering and splitting`

### Task 7: Weekly digest (spec §6)

**Files:**
- Modify: `notify/digest.py` (add the functions)
- Test: `tests/notify/test_digest.py`

**Interfaces:**
- Consumes: `api.Snapshot` (weekly), `cycles.*`, `config.NotifyConfig`, `debits.REASON_TEXT`.
- Produces: `build_weekly(snap: Snapshot, cfg: NotifyConfig, last_daily_run: datetime | None) -> WeeklyReport` with
  - `AccountCases(account_id, name, count, oldest_days, items: list[CaseLine], truncated: int)` where `CaseLine(date: date | None, amount: Decimal | None, text: str, question_zh: str, statement_id: int)`; items from `inbox.accounts[*].items` (date/amount/text from `item.line` or `item.entry`), capped at `cfg.weekly_open_case_limit`, `oldest_days` from `snap.open_cases[account_id][*].created_at`; `QUESTION_TEXT` map exactly spec §6.1: bank_only→只有銀行有, ledger_only→只有帳本有, transfer→疑似轉帳, amount_differs→金額不同, date_differs→日期不同, ambiguous→多個候選, balance_gap→餘額差, settlement→結算, proposed→已提議, other→其他.
  - `MissingStatement(account_id, name, last_period_end: date, expected: date, days_late: int)` — cadence: `closing_day` set → `shift_period(closing_day, (…, last_period_end), 1).end`; else median gap of the last three live `period_end`s (one statement → +1 month via `shift_period(None, …)`); listed when `expected + grace < today` and no live statement with `period_end > last`.
  - `Gaps(unlinked_cards: list[str], cards_without_rule: list[str], statements_without_due: list[tuple[str, int]])`.
  - skips: last 7 days, reason ≠ cutoff, one per statement (latest).

- [ ] **Step 1: Write the failing tests**

```python
def test_missing_statement_card_cadence(fx):
    snap = fx.weekly_snapshot(today=date(2026, 11, 28), statements_for={"旅遊卡": [fx.statement(period_end=date(2026, 10, 15))]})
    m = build_weekly(snap, fx.cfg(), None).missing[0]; assert (m.expected, m.days_late) == (date(2026, 11, 15), 13)

def test_missing_statement_bank_median_gap(fx):
    snap = fx.weekly_snapshot(today=date(2026, 11, 28), statements_for={"薪轉戶 A": [fx.statement(period_end=d) for d in (date(2026, 7, 31), date(2026, 8, 31), date(2026, 9, 30))]})
    assert build_weekly(snap, fx.cfg(), None).missing[0].expected == date(2026, 10, 30)   # median gap 30 days

def test_not_missing_inside_grace(fx):
    snap = fx.weekly_snapshot(today=date(2026, 11, 20), statements_for={"旅遊卡": [fx.statement(period_end=date(2026, 10, 15))]})
    assert build_weekly(snap, fx.cfg(), None).missing == []

def test_open_cases_capped_and_aged(fx):
    snap = fx.weekly_snapshot(inbox_items={"旅遊卡": 12}, oldest_created=date(2026, 11, 1), today=date(2026, 11, 28))
    ac = build_weekly(snap, fx.cfg(), None).open_cases[0]; assert (ac.count, len(ac.items), ac.truncated, ac.oldest_days) == (12, 10, 2, 27)
    assert {l.question_zh for l in ac.items} <= set(QUESTION_TEXT.values()) and ac.items[0].question_zh == "只有銀行有"

def test_gaps(fx):
    snap = fx.weekly_snapshot(extra_cards=[fx.account(id=11, name="無規則卡", is_credit=True, closing_day=None)], statements_for={"無規則卡": [fx.statement(account_id=11, due_date=None, total="500")]})
    g = build_weekly(snap, fx.cfg(), None).gaps; assert "無規則卡" in g.cards_without_rule and ("無規則卡", snap.statements[11][0]["id"]) in g.statements_without_due
```

- [ ] **Step 2: Run** — expected: AttributeError/ImportError. **Step 3: Implement.** **Step 4: Run** — pass.
- [ ] **Step 5: Commit** `feat(notify): weekly digest`

### Task 8: Sender and CLI

**Files:**
- Create: `notify/send.py`, `notify/cli.py`, `notify/__main__.py`
- Test: `tests/notify/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `send.deliver(messages: list[str], target: str, runner: Callable[[list[str], str], int] = _run_hermes) -> None` — runs `["hermes", "send", "-t", target, "-q"]` with the message on stdin, in order; non-zero → `SendError`.
  - `cli.main(argv: list[str] | None = None) -> int` — `daily|weekly [--as-of D] [--dry-run] [--send] [--config PATH]`; default config path `~/.config/homehub-notify.toml`; flow: load config/token → `httpx.Client(base_url=cfg.api_base, timeout=30)` → fetch → build → (daily: dedupe) → render → if `--send`: deliver (when there is anything) then `save_state` on **every** exit 0 (daily sets `state.last_daily_run`, weekly `state.last_weekly_run`, both `datetime.now(Asia/Taipei)`; daily also sets `state.last_send = LastSend(today, keys)` when it sent; weekly passes the stored `last_daily_run` to `build_weekly`); else print messages to stdout separated by `\n---\n` and touch nothing. Same-day suppression: if `state.last_send.day == today` and `keys == state.last_send.triggers`, send nothing (exit 0, state still saved). Weekly always has ≥ 1 message; daily with none → log `daily: nothing to send`, exit 0. `ApiError` (incl. `CappedRead`)/`cycles.CalendarExhausted`/`ConfigError`/`httpx.HTTPError` → with `--send` deliver `render_failure(short)` where `short = f"{type(exc).__name__} {getattr(exc, 'status', '')}".strip(); exit 1; state untouched. One stderr line per run: `daily: N items, sent M messages`.

- [ ] **Step 1: Write the failing tests** (monkeypatch `send._run_hermes` with a recorder; API via Task 3 fakes; config via `tmp_path`):

```python
def test_dry_run_never_sends(cli_env):
    rc = main(["daily", "--as-of", "2026-11-03", "--dry-run", "--config", cli_env.cfg]); assert rc == 0 and cli_env.sent == [] and not cli_env.state_path.exists()

def test_send_writes_state_and_calls_hermes(cli_env):
    rc = main(["daily", "--as-of", "2026-11-03", "--send", "--config", cli_env.cfg]); assert rc == 0 and len(cli_env.sent) == 1 and cli_env.sent[0][0][:4] == ["hermes", "send", "-t", "discord:000000000000000000"] and cli_env.state_path.exists()

def test_daily_nothing_to_send_still_writes_state(cli_env_quiet, capsys):
    rc = main(["daily", "--as-of", "2026-11-20", "--send", "--config", cli_env_quiet.cfg]); assert rc == 0 and cli_env_quiet.sent == []
    assert load_state(cli_env_quiet.state_path).last_daily_run is not None

def test_same_day_rerun_sends_nothing(cli_env):
    assert main(["daily", "--as-of", "2026-11-03", "--send", "--config", cli_env.cfg]) == 0 and len(cli_env.sent) == 1
    assert main(["daily", "--as-of", "2026-11-03", "--send", "--config", cli_env.cfg]) == 0 and len(cli_env.sent) == 1

def test_calendar_exhausted_is_failure(cli_env_far_due):
    rc = main(["daily", "--as-of", "2028-01-03", "--send", "--config", cli_env_far_due.cfg]); assert rc == 1 and "CalendarExhausted" in cli_env_far_due.sent[0][1]

def test_weekly_always_sends(cli_env_quiet):
    rc = main(["weekly", "--as-of", "2026-11-21", "--send", "--config", cli_env_quiet.cfg]); assert rc == 0 and "✅ 本週沒有待處理" in cli_env_quiet.sent[0][1]

def test_api_failure_sends_failure_message(cli_env_401):
    rc = main(["daily", "--as-of", "2026-11-03", "--send", "--config", cli_env_401.cfg])
    assert rc == 1 and cli_env_401.sent[0][1].startswith("❌ 帳務通知失敗：ApiError 401") and "secret-token" not in cli_env_401.sent[0][1] and not cli_env_401.state_path.exists()

def test_bad_args(): assert main(["monthly"]) == 2
```

- [ ] **Step 2: Run** — expected: ImportError. **Step 3: Implement** the three modules. **Step 4: Run** `pytest -q tests/notify` — all pass.
- [ ] **Step 5: Commit** `feat(notify): hermes sender and CLI`

### Task 9: Deployment units, install script, docs

**Files:**
- Create: `deploy/notify/homehub-notify-daily.service`, `deploy/notify/homehub-notify-daily.timer`, `deploy/notify/homehub-notify-weekly.service`, `deploy/notify/homehub-notify-weekly.timer`, `deploy/notify/install.sh`, `services/accounting-service/requirements-notify.txt` (`httpx==0.28.1` only)
- Modify: `services/accounting-service/README.md` (new section "Notifications (notify/)" after the auto-pay section: purpose, config file, token label, CLI, timers, rollout steps from spec §9), `openspec/changes/accounting-notifications/` (proposal.md, tasks.md, specs/accounting-notifications/spec.md — new capability, requirements ≤ 500 chars each; `openspec validate accounting-notifications --strict` passes)
- Test: `tests/notify/test_units.py`

**Interfaces:**
- Units (user scope, run by `opc`): `.service` `Type=oneshot`, `WorkingDirectory=%h/releases/home-hub-notify/current/services/accounting-service`, `ExecStart=%h/releases/home-hub-notify/venv/bin/python -m notify daily --send` (weekly likewise), `Environment=PYTHONUNBUFFERED=1`, `Environment=TZ=Asia/Taipei`; `.timer` `OnCalendar=*-*-* 07:00:00 Asia/Taipei` / `OnCalendar=Sat *-*-* 10:00:00 Asia/Taipei`, `Persistent=true`, `WantedBy=timers.target`.
- `install.sh`: `set -euo pipefail`; copies the four units to `~/.config/systemd/user/`, `systemctl --user daemon-reload`, `enable --now` both timers; idempotent; prints `systemctl --user list-timers | grep homehub-notify`. Does not create the venv or the checkout (release steps are in the README).

- [ ] **Step 1: Write the failing test**

```python
def test_units_match_spec():
    d = Path("deploy/notify")
    assert "OnCalendar=*-*-* 07:00:00 Asia/Taipei" in (d / "homehub-notify-daily.timer").read_text()
    assert "OnCalendar=Sat *-*-* 10:00:00 Asia/Taipei" in (d / "homehub-notify-weekly.timer").read_text()
    for name in ("daily", "weekly"):
        svc = (d / f"homehub-notify-{name}.service").read_text()
        assert f"-m notify {name} --send" in svc and "Type=oneshot" in svc and "Environment=TZ=Asia/Taipei" in svc
    assert "Persistent=true" in (d / "homehub-notify-daily.timer").read_text()
```

- [ ] **Step 2: Run** — expected: FileNotFoundError. **Step 3: Create** the units, script, requirements, README section, openspec change. **Step 4: Run** the test, `bash -n deploy/notify/install.sh`, `openspec validate accounting-notifications --strict`, then the full accounting suite `pytest -q -p no:cacheprovider` — all pass.
- [ ] **Step 5: Commit** `feat(notify): timers, install script, docs, openspec change`
