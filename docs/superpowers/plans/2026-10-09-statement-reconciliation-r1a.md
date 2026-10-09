# Statement reconciliation R1a — schema, scopes, ingest API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the first stacked backend PR of the 對帳 feature: fail-closed token scopes, the complete reconciliation schema with its triggers, and the worker-facing ingest API that turns a parsed statement into immutable revisions, lines, events and lineage, with server-side flow derivation and guardrails. No matching, no actions yet (R1b, R1c).

**Architecture:** New model module `app/models/statements.py` + one Alembic revision after `c4e8b2f1a7d3`. Scope enforcement is a FastAPI dependency driven by `ACCOUNTING_TOKEN_SCOPES`, applied to every router; startup validation refuses unsafe configurations. Ingest endpoints live in `app/routers/statements.py` behind a feature dependency; the pure parts (flow-sign adapter, guardrails, merchant normaliser, lineage pairing) are standalone modules under `app/services/statements/` with unit tests; the DB layer is `statement_ingest_service.py`, which follows "services never commit, routers commit".

**Tech Stack:** Python 3.13, FastAPI 0.129, SQLAlchemy 2.0 (`Column` style, `Base` from `shared_lib`), Alembic 1.18, PostgreSQL 16, pytest with the existing `client`/`db_session`/`seed` fixtures.

**Spec:** `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md` (v4, commit `f794161`). Sections cited as §n.

## Global Constraints

- Services never commit; routers call `db.commit()` after `with service_errors():` (fact sheet §6/§7). Error mapping: `ValidationError(field, message)` → 422, `ConflictError` → 409, `NotFoundError` → 404.
- Money columns `Numeric(20, 4)`; `fx_rate` `Numeric(20, 10)`; ids `Integer`; `Base` has no naming convention — every index/constraint name is written out (`ix_<table>_<cols>`, `ux_`, `uq_`, `ck_`).
- Every new table must be added to `LEDGER_TABLES` in `tests/conftest.py` (TRUNCATE list) and must round-trip in `test_head_schema_matches_the_models` (model and migration agree on types, nullability, server defaults, FKs, index names, unique column sets).
- The migration's `down_revision = "c4e8b2f1a7d3"`; `tests/integration/test_migration.py` pins the head as `SCHEDULES_HEAD` at lines 20/310/346/416/452 → introduce `RECONCILE_HEAD` and update those assertions.
- `ENTRY_SOURCES` gains `statement`; the enum value is added in an `autocommit_block` step before any use (pattern in `c4e8b2f1a7d3`).
- Lock order D32 is unchanged: this PR never locks `ledger_entry`, `entry_group` or `account` rows from the new code paths (R1a writes only statement tables). Triggers are append-only and never update their source tables (§4.6 and the ruling in Task 4).
- Wording in user-facing strings: Traditional Chinese as in the existing routers; code identifiers English.
- Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; the PR description ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- No owner financial data in tests or fixtures; all statement fixtures are synthetic.

## Review Focus

1. A restricted token (`hermes=read,propose`) must get 403 on every existing mutation route (`POST /entries`, `PUT /accounts/{id}`, `POST /transfers`, `POST /imports/moze`, settings writes) — pinned in Task 2's integration test over the full route table, not a sample.
2. A revision whose printed lines do not satisfy the equation must still be stored (lines, revision) but must never become `current_revision_id` of a statement that already has a good one, and must open exactly one `parse_review` case in live mode and none in historical mode — Task 8.
3. A re-parse that only changes whitespace/case in merchant text must pair every line as `normalised`, never `changed`; a merchant change A→B must pair as `changed`; two identical twins reordered must pair `identical` — Task 7 unit tests.
4. The dirty triggers must not fire for no-op updates (`UPDATE ledger_entry SET name = name`) and must record both OLD and NEW rows on a cross-account move — Task 4 integration test.
5. Startup must refuse `ACCOUNTING_RECONCILIATION_ENABLED=true` with `ACCOUNTING_TOKEN_SCOPES` unset, a label holding `legacy` together with `ingest`/`enqueue`, an unknown scope name, a bare token, or a restricted label without a scope entry — Task 2 unit tests.

---

### Task 1: Worktree environment and baseline

**Files:**
- Create: `services/accounting-service/.venv/` (ignored), root `.env` symlink (ignored)

**Interfaces:**
- Consumes: nothing
- Produces: a worktree where `pytest` runs green at base

- [ ] **Step 1: Create the venv and link the env file** (skip steps that already exist)

```bash
cd /home/opc/workspace/home-hub-reconcile
ln -sfn /home/opc/workspace/home-hub/.env .env
cd services/accounting-service
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt pytest httpx
```

- [ ] **Step 2: Run the baseline**

Run: `.venv/bin/pytest -q tests/unit && .venv/bin/pytest -q tests/integration/test_migration.py tests/integration/test_models_schema.py`
Expected: all green (unit ≈ 11 files; migration/model-schema tests pass at head `c4e8b2f1a7d3`).

- [ ] **Step 3: No commit** (environment only).

---

### Task 2: Token scopes, startup validation, feature dependency

**Files:**
- Modify: `services/accounting-service/app/auth.py`
- Modify: `services/accounting-service/app/main.py`
- Modify: every router module to carry the scope dependency: `app/routers/{accounts,entries,balance_adjustments,transfers,splits,settings,imports,schedules}.py`
- Test: `services/accounting-service/tests/unit/test_token_scopes.py`, `services/accounting-service/tests/integration/test_scope_enforcement.py`

**Interfaces:**
- Produces:
  - `auth.SCOPES_ENV = "ACCOUNTING_TOKEN_SCOPES"`, `auth.RESTRICTED_ENV = "ACCOUNTING_RESTRICTED_LABELS"`, `auth.FEATURE_ENV = "ACCOUNTING_RECONCILIATION_ENABLED"`
  - `auth.VALID_SCOPES: frozenset[str] = {"read","propose","write","admin","enqueue","ingest","legacy"}`
  - `auth.parse_scopes(raw: str | None) -> dict[str, frozenset[str]]`
  - `auth.validate_auth_config() -> None` (raises `ValueError`)
  - `auth.scopes_configured() -> bool`
  - `auth.require(*scopes: str) -> Callable` — FastAPI dependency factory; passes when scopes are unconfigured, when the label holds `legacy`, or when it holds any of `scopes`; else `HTTPException(403, "scope")`
  - `auth.method_scope(read: str = "read", write: str = "write") -> Callable` — dependency that applies `read` to GET/HEAD and `write` to other methods
  - `auth.require_feature() -> Callable` — dependency raising `HTTPException(404, "reconciliation disabled")` when `FEATURE_ENV` is not true
  - `auth.restricted_labels() -> frozenset[str]` — `{"hermes", "worker"} ∪ RESTRICTED_ENV`

- [ ] **Step 1: Write the failing unit tests**

```python
# tests/unit/test_token_scopes.py
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app import auth

TOKENS = "spa:spa-token,hermes:hermes-token,worker:worker-token,ops:ops-token"


@pytest.fixture
def env(monkeypatch):
    def _set(scopes=None, feature=None, tokens=TOKENS, restricted=None):
        monkeypatch.setenv(auth.TOKENS_ENV, tokens)
        for name, value in ((auth.SCOPES_ENV, scopes), (auth.FEATURE_ENV, feature), (auth.RESTRICTED_ENV, restricted)):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
    return _set


def test_parse_scopes_splits_labels_and_scopes():
    parsed = auth.parse_scopes("spa=legacy; hermes=read,propose ;worker=ingest")
    assert parsed == {"spa": frozenset({"legacy"}), "hermes": frozenset({"read", "propose"}), "worker": frozenset({"ingest"})}


@pytest.mark.parametrize("raw", ["spa=nope", "spa=", "=read", "spa=read;spa=write", "spa:read"])
def test_parse_scopes_rejects_malformed(raw):
    with pytest.raises(ValueError):
        auth.parse_scopes(raw)


def test_validate_passes_for_all_legacy_without_scopes(env):
    env(tokens="spa:a,ops:b")
    auth.validate_auth_config()


def test_validate_refuses_feature_on_without_scopes(env):
    env(feature="true")
    with pytest.raises(ValueError, match="ACCOUNTING_TOKEN_SCOPES"):
        auth.validate_auth_config()


def test_validate_refuses_restricted_label_without_scopes(env):
    env()  # hermes + worker labels present, no scopes
    with pytest.raises(ValueError, match="restricted"):
        auth.validate_auth_config()


@pytest.mark.parametrize(
    "scopes",
    [
        "spa=legacy; hermes=read,propose",            # worker label missing from the map
        "spa=legacy; hermes=read,propose; worker=ingest; ghost=read",  # label not in tokens
        "spa=legacy,ingest; hermes=read; worker=ingest",  # legacy together with ingest
        "spa=legacy; hermes=read,enqueue,legacy; worker=ingest",  # legacy together with enqueue
        "spa=legacy; hermes=admin; worker=ingest",      # admin is fine; this row only checks ops missing below
    ],
)
def test_validate_refuses_incomplete_or_unsafe_maps(env, scopes):
    env(scopes=scopes)
    with pytest.raises(ValueError):
        auth.validate_auth_config()


def test_validate_refuses_bare_tokens_with_scopes(env):
    env(tokens="spa:a,bare-token", scopes="spa=legacy")
    with pytest.raises(ValueError, match="bare"):
        auth.validate_auth_config()


def test_validate_accepts_complete_map(env):
    env(scopes="spa=legacy; hermes=read,propose; worker=ingest; ops=legacy,admin", feature="true")
    auth.validate_auth_config()


@pytest.fixture
def probe(env):
    env(scopes="spa=legacy; hermes=read,propose; worker=ingest; ops=legacy,admin")
    app = FastAPI()
    app.add_middleware(auth.ApiTokenMiddleware)

    @app.get("/r", dependencies=[Depends(auth.require("read"))])
    def r():
        return {"ok": True}

    @app.post("/w", dependencies=[Depends(auth.require("write"))])
    def w():
        return {"ok": True}

    @app.post("/p", dependencies=[Depends(auth.require("propose"))])
    def p():
        return {"ok": True}

    @app.api_route("/m", methods=["GET", "POST"], dependencies=[Depends(auth.method_scope())])
    def m(request: Request):
        return {"method": request.method}

    @app.get("/f", dependencies=[Depends(auth.require_feature())])
    def f():
        return {"ok": True}

    return TestClient(app)


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def test_require_allows_legacy_everything(probe):
    assert probe.post("/w", headers=_h("spa-token")).status_code == 200
    assert probe.post("/p", headers=_h("spa-token")).status_code == 200


def test_require_restricts_hermes(probe):
    assert probe.get("/r", headers=_h("hermes-token")).status_code == 200
    assert probe.post("/p", headers=_h("hermes-token")).status_code == 200
    denied = probe.post("/w", headers=_h("hermes-token"))
    assert denied.status_code == 403 and denied.json()["detail"] == "scope"


def test_method_scope_maps_get_to_read_and_post_to_write(probe):
    assert probe.get("/m", headers=_h("hermes-token")).status_code == 200
    assert probe.post("/m", headers=_h("hermes-token")).status_code == 403
    assert probe.post("/m", headers=_h("worker-token")).status_code == 403
    assert probe.get("/m", headers=_h("worker-token")).status_code == 403  # ingest holds no read


def test_require_feature_404_when_off(probe, monkeypatch):
    monkeypatch.delenv(auth.FEATURE_ENV, raising=False)
    assert probe.get("/f", headers=_h("spa-token")).status_code == 404
    monkeypatch.setenv(auth.FEATURE_ENV, "true")
    assert probe.get("/f", headers=_h("spa-token")).status_code == 200


def test_unconfigured_scopes_pass_through(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, "spa:a")
    monkeypatch.delenv(auth.SCOPES_ENV, raising=False)
    app = FastAPI()
    app.add_middleware(auth.ApiTokenMiddleware)

    @app.post("/w", dependencies=[Depends(auth.require("write"))])
    def w():
        return {"ok": True}

    assert TestClient(app).post("/w", headers=_h("a")).status_code == 200
```

Note: the probe app uses a bare `FastAPI()` so the 403 body is FastAPI's default `{"detail": "scope"}`; in the real app the shared handler turns it into `{"code": 403, "message": "scope", ...}` — the integration test below asserts `message`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest -q tests/unit/test_token_scopes.py`
Expected: FAIL with `AttributeError: module 'app.auth' has no attribute 'parse_scopes'`.

- [ ] **Step 3: Implement the scope layer in `app/auth.py`** (append after the middleware; keep everything existing)

```python
# --- scopes (reconciliation design §8.1) -------------------------------------------------------------
from fastapi import Depends, HTTPException, Request  # noqa: E402

SCOPES_ENV = "ACCOUNTING_TOKEN_SCOPES"
RESTRICTED_ENV = "ACCOUNTING_RESTRICTED_LABELS"
FEATURE_ENV = "ACCOUNTING_RECONCILIATION_ENABLED"
VALID_SCOPES = frozenset({"read", "propose", "write", "admin", "enqueue", "ingest", "legacy"})
_EXCLUSIVE_WITH_LEGACY = frozenset({"ingest", "enqueue"})
_BUILTIN_RESTRICTED = frozenset({"hermes", "worker"})


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes"}


def feature_enabled() -> bool:
    return _truthy(os.getenv(FEATURE_ENV))


def restricted_labels() -> frozenset[str]:
    extra = {item.strip() for item in (os.getenv(RESTRICTED_ENV) or "").split(",") if item.strip()}
    return _BUILTIN_RESTRICTED | frozenset(extra)


@lru_cache(maxsize=8)
def parse_scopes(raw: str | None) -> dict[str, frozenset[str]]:
    """`"spa=legacy; hermes=read,propose"` -> {label: scopes}. Malformed input raises ValueError."""
    out: dict[str, frozenset[str]] = {}
    for item in (raw or "").split(";"):
        item = item.strip()
        if not item:
            continue
        label, sep, scopes_text = item.partition("=")
        label = label.strip()
        if not sep or not label:
            raise ValueError(f"{SCOPES_ENV}: malformed item {item!r}")
        if label in out:
            raise ValueError(f"{SCOPES_ENV}: duplicate label {label!r}")
        scopes = frozenset(s.strip() for s in scopes_text.split(",") if s.strip())
        if not scopes:
            raise ValueError(f"{SCOPES_ENV}: no scopes for label {label!r}")
        unknown = scopes - VALID_SCOPES
        if unknown:
            raise ValueError(f"{SCOPES_ENV}: unknown scope {sorted(unknown)!r} for label {label!r}")
        if "legacy" in scopes and scopes & _EXCLUSIVE_WITH_LEGACY:
            raise ValueError(f"{SCOPES_ENV}: label {label!r} holds legacy together with ingest/enqueue")
        out[label] = scopes
    return out


def configured_scopes() -> dict[str, frozenset[str]]:
    return parse_scopes(os.getenv(SCOPES_ENV))


def scopes_configured() -> bool:
    return bool((os.getenv(SCOPES_ENV) or "").strip())


def validate_auth_config() -> None:
    """Startup check: refuse configurations that would widen a restricted credential (design §8.1)."""
    tokens = configured_tokens()
    labels = [label for label, _ in tokens]
    if scopes_configured():
        scopes = configured_scopes()
        if any(label is None for label in labels):
            raise ValueError(f"{SCOPES_ENV} is set: bare tokens are not allowed in {TOKENS_ENV}")
        if len(set(labels)) != len(labels):
            raise ValueError(f"{TOKENS_ENV}: duplicate labels")
        missing = set(labels) - set(scopes)
        if missing:
            raise ValueError(f"{SCOPES_ENV}: labels without a scope entry: {sorted(missing)!r}")
        ghosts = set(scopes) - set(labels)
        if ghosts:
            raise ValueError(f"{SCOPES_ENV}: labels without a token: {sorted(ghosts)!r}")
        if feature_enabled() and not tokens:
            raise ValueError(f"{FEATURE_ENV} requires {TOKENS_ENV}")
        return
    if feature_enabled():
        raise ValueError(f"{FEATURE_ENV}=true requires {SCOPES_ENV}")
    present = {label for label in labels if label} & restricted_labels()
    if present:
        raise ValueError(f"restricted labels {sorted(present)!r} need {SCOPES_ENV}")


def _label_scopes(request: Request) -> frozenset[str] | None:
    """None when scopes are unconfigured (compatibility mode); else the caller's scope set."""
    if not scopes_configured():
        return None
    label = getattr(request.state, "client_label", None)
    return configured_scopes().get(label, frozenset())


def require(*scopes: str):
    needed = frozenset(scopes)
    assert needed <= VALID_SCOPES, scopes

    def dependency(request: Request) -> None:
        held = _label_scopes(request)
        if held is None or "legacy" in held or held & needed:
            return
        raise HTTPException(status_code=403, detail="scope")

    return dependency


def method_scope(read: str = "read", write: str = "write"):
    def dependency(request: Request) -> None:
        needed = read if request.method in PUBLIC_METHODS else write
        require(needed)(request)

    return dependency


def require_feature():
    def dependency() -> None:
        if not feature_enabled():
            raise HTTPException(status_code=404, detail="reconciliation disabled")

    return dependency
```

`PUBLIC_METHODS` already exists (`{"GET", "HEAD"}`). `Depends` is imported for callers that write `Depends(auth.require("read"))`.

- [ ] **Step 4: Apply scopes to every existing router**

For each router, add `dependencies=[Depends(method_scope())]` to the `APIRouter(...)` constructor; settings mutations use `admin`:

```python
# app/routers/entries.py (and accounts, balance_adjustments, transfers, splits, schedules, imports)
from fastapi import APIRouter, Depends
from app.auth import method_scope
router = APIRouter(prefix="/entries", tags=["Entries"], dependencies=[Depends(method_scope())])

# app/routers/settings.py
router = APIRouter(tags=["Settings"], dependencies=[Depends(method_scope(write="admin"))])
```

Ruling (recorded here so the implementer does not re-decide it): the SPA holds `legacy`, so making settings mutations `admin` changes nothing for it; `ops` gets `legacy,admin` in the deploy notes.

- [ ] **Step 5: Validate at startup in `app/main.py`**

```python
from app.auth import ApiTokenMiddleware, api_auth_status, docs_paths, validate_auth_config
...
validate_auth_config()  # raises ValueError and stops the process on an unsafe configuration
app.user_middleware.append(Middleware(ApiTokenMiddleware, docs_paths=docs_paths(app)))
```

- [ ] **Step 6: Run the unit tests**

Run: `.venv/bin/pytest -q tests/unit/test_token_scopes.py tests/unit/test_api_auth.py`
Expected: PASS (the existing auth tests keep passing: unconfigured scopes are a pass-through).

- [ ] **Step 7: Integration test over the full route table**

```python
# tests/integration/test_scope_enforcement.py
import pytest
from fastapi.routing import APIRoute

from app import auth
from app.main import app

TOKENS = "spa:spa-token,hermes:hermes-token,worker:worker-token"
SCOPES = "spa=legacy; hermes=read,propose; worker=ingest"


@pytest.fixture
def scoped(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, TOKENS)
    monkeypatch.setenv(auth.SCOPES_ENV, SCOPES)
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


def _mutation_routes():
    for route in app.routes:
        if isinstance(route, APIRoute) and route.methods - {"GET", "HEAD", "OPTIONS"}:
            for method in sorted(route.methods - {"GET", "HEAD", "OPTIONS"}):
                yield method, route.path


def _fill(path: str) -> str:
    out = path
    for name in ("account_id", "entry_id", "group_id", "category_id", "project_id", "counterparty_id",
                 "definition_id", "instance_id", "run_id", "file_id", "statement_id", "sid", "case_id", "proposal_id", "action_id"):
        out = out.replace("{" + name + "}", "1")
    return out


@pytest.mark.parametrize("method,path", list(_mutation_routes()))
def test_hermes_token_cannot_mutate_anything_except_proposals(scoped, client, method, path):
    if path.endswith("/proposals"):
        pytest.skip("propose is the one mutation hermes may perform; covered in test_ingest_api")
    response = client.request(method, _fill(path), headers={"Authorization": "Bearer hermes-token"}, json={})
    assert response.status_code == 403, (method, path, response.text)
    assert response.json()["message"] == "scope"


def test_worker_token_cannot_read_ledger(scoped, client):
    response = client.get("/entries", headers={"Authorization": "Bearer worker-token"})
    assert response.status_code == 403


def test_spa_legacy_token_still_writes(scoped, client, seed, db_session):
    account = seed.account("A")
    db_session.commit()
    response = client.post(
        "/entries",
        headers={"Authorization": "Bearer spa-token"},
        json={"account_id": account.id, "kind": "expense", "amount": "10", "entry_date": "2026-09-01"},
    )
    assert response.status_code == 201


def test_feature_off_keeps_restrictions(scoped, monkeypatch, client):
    monkeypatch.setenv(auth.FEATURE_ENV, "false")
    response = client.post("/entries", headers={"Authorization": "Bearer hermes-token"}, json={})
    assert response.status_code == 403
```

Note `json={}` makes validation-free routes answer 403 before 422 because the dependency runs first; routes with path params use id 1 and still hit the dependency before any lookup.

- [ ] **Step 8: Run the integration tests**

Run: `.venv/bin/pytest -q tests/integration/test_scope_enforcement.py tests/unit`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add services/accounting-service/app/auth.py services/accounting-service/app/main.py services/accounting-service/app/routers services/accounting-service/tests/unit/test_token_scopes.py services/accounting-service/tests/integration/test_scope_enforcement.py
git commit -m "feat(accounting): fail-closed token scopes with startup validation and a reconciliation feature gate"
```

---

### Task 3: Reconciliation models

**Files:**
- Create: `services/accounting-service/app/models/statements.py`
- Modify: `services/accounting-service/app/models/__init__.py` (re-export), `services/accounting-service/app/models/ledger.py` (`ENTRY_SOURCES` + `"statement"`, `Account` columns)
- Test: `services/accounting-service/tests/integration/test_statement_models.py`

**Interfaces:**
- Produces the ORM classes `IngestRun, StatementFile, StatementSource, AccountStatement, StatementRevision, StatementEvent, StatementLine, LineLineage, StatementCoverage, CoverageDirty, ReconciliationCase, ReconciliationProposal, ReconciliationAction, ReconciliationAudit, PolicyBudget, InstallmentPlanMap, ReconciliationSettings`, the enum tuples listed below, and the DDL strings `DIRTY_ENTRY_FUNCTION_SQL, DIRTY_ENTRY_TRIGGER_SQL, DIRTY_GROUP_FUNCTION_SQL, DIRTY_GROUP_TRIGGER_SQL, DIRTY_ACCOUNT_FUNCTION_SQL, DIRTY_ACCOUNT_TRIGGER_SQL`.
- `Account` gains `statement_password_rule: String(64)`, `statement_live_from: Date`, `statement_source_root: statement_source_root enum` (all nullable).

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_statement_models.py
from sqlalchemy import text

from app.models import (
    AccountStatement, CoverageDirty, IngestRun, StatementEvent, StatementFile, StatementLine, StatementRevision,
    StatementSource, ReconciliationSettings,
)


def test_statement_tables_exist_after_migration(db_session):
    names = {row[0] for row in db_session.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))}
    assert {
        "ingest_run", "statement_file", "statement_source", "account_statement", "statement_revision", "statement_event",
        "statement_line", "line_lineage", "statement_coverage", "coverage_dirty", "reconciliation_case",
        "reconciliation_proposal", "reconciliation_action", "reconciliation_audit", "policy_budget",
        "installment_plan_map", "reconciliation_settings",
    } <= names


def test_entry_source_enum_has_statement(db_session):
    values = {row[0] for row in db_session.execute(text("SELECT unnest(enum_range(NULL::entry_source))::text"))}
    assert "statement" in values


def test_dirty_trigger_records_old_and_new_on_account_move(db_session, seed):
    a, b = seed.account("A"), seed.account("B")
    entry = seed.entry(a, "-10")
    db_session.commit()
    db_session.execute(text("DELETE FROM coverage_dirty"))
    entry.account_id = b.id
    db_session.commit()
    rows = db_session.query(CoverageDirty).order_by(CoverageDirty.id).all()
    assert [(r.kind, r.op, r.old_account_id, r.new_account_id) for r in rows] == [("entry", "update", a.id, b.id)]
    assert rows[0].old_row["account_id"] == a.id and rows[0].new_row["account_id"] == b.id


def test_dirty_trigger_ignores_noop_update(db_session, seed):
    entry = seed.entry(seed.account("A"), "-10")
    db_session.commit()
    db_session.execute(text("DELETE FROM coverage_dirty"))
    db_session.execute(text("UPDATE ledger_entry SET name = name WHERE id = :id"), {"id": entry.id})
    db_session.commit()
    assert db_session.query(CoverageDirty).count() == 0


def test_dirty_trigger_tags_action_id_from_transaction_setting(db_session, seed):
    entry = seed.entry(seed.account("A"), "-10")
    db_session.commit()
    db_session.execute(text("DELETE FROM coverage_dirty"))
    db_session.execute(text("SET LOCAL app.reconciliation_action_id = '42'"))
    entry.amount = entry.amount - 1
    db_session.commit()
    assert db_session.query(CoverageDirty).one().action_id == 42


def test_account_config_change_is_dirty(db_session, seed):
    a = seed.account("A")
    db_session.commit()
    db_session.execute(text("DELETE FROM coverage_dirty"))
    a.opening_balance = 5
    db_session.commit()
    row = db_session.query(CoverageDirty).one()
    assert (row.kind, row.op, row.new_account_id) == ("account", "update", a.id)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest -q tests/integration/test_statement_models.py`
Expected: FAIL with `ImportError: cannot import name 'AccountStatement'`.

- [ ] **Step 3: Write `app/models/statements.py`**

```python
"""Reconciliation schema (design §4). Triggers append to coverage_dirty and never touch their source tables."""
from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, DDL, Date, DateTime, Enum, ForeignKey, Index, Integer, Numeric,
    SmallInteger, String, Text, UniqueConstraint, event, func, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from app.database import Base

STATEMENT_KINDS = ("card", "bank")
STATEMENT_FILE_STATUSES = ("new", "unlocked", "parsed", "needs_review", "failed", "ignored")
STATEMENT_FILE_FAILURES = ("password", "no_text_layer", "too_large", "parse", "guardrail", "mapping", "transient", "sandbox")
STATEMENT_SOURCE_ROOTS = ("mail", "manual")
INGEST_TRIGGERS = ("timer", "owner_cli", "enqueue")
INGEST_MODES = ("live", "backfill")
INGEST_STATUSES = ("queued", "claimed", "running", "done", "failed", "expired")
STATEMENT_MODES = ("historical", "live")
STATEMENT_STATUSES = ("open", "reconciled")
STATEMENT_ORIGINS = ("manual", "import")
LINE_KINDS = ("purchase", "refund", "payment", "fee", "interest", "reward", "installment", "balance_adjustment",
              "deposit", "withdrawal", "transfer_in", "transfer_out", "unknown")
EVENT_STATUSES = ("live", "retired", "quarantined")
EQUIVALENCES = ("identical", "normalised", "changed", "unpaired")
COVERAGE_ROLES = ("principal", "child", "member")
COVERAGE_STATUSES = ("active", "stale")
MATCH_KINDS = ("auto", "manual", "agent")
CASE_KINDS = ("line_unmatched", "entry_unmatched", "amount_delta", "ambiguous", "duplicate_claim", "balance_gap",
              "statement_conflict", "parse_review", "recheck")
CASE_STATUSES = ("open", "proposed", "resolved", "dismissed", "superseded")
CASE_EXPLANATIONS = ("deferred_next_period", "accepted_exception")
RECONCILIATION_ACTORS = ("owner", "policy")
PROPOSAL_STATUSES = ("pending", "applied", "rejected", "superseded")
ACTION_STATUSES = ("applied", "reverted", "failed")
DIRTY_KINDS = ("entry", "group", "account")
DIRTY_OPS = ("insert", "update", "delete")

statement_kind_enum = Enum(*STATEMENT_KINDS, name="statement_kind")
statement_file_status_enum = Enum(*STATEMENT_FILE_STATUSES, name="statement_file_status")
statement_file_failure_enum = Enum(*STATEMENT_FILE_FAILURES, name="statement_file_failure")
statement_source_root_enum = Enum(*STATEMENT_SOURCE_ROOTS, name="statement_source_root")
ingest_trigger_enum = Enum(*INGEST_TRIGGERS, name="ingest_trigger")
ingest_mode_enum = Enum(*INGEST_MODES, name="ingest_mode")
ingest_status_enum = Enum(*INGEST_STATUSES, name="ingest_status")
statement_mode_enum = Enum(*STATEMENT_MODES, name="statement_mode")
statement_status_enum = Enum(*STATEMENT_STATUSES, name="statement_status")
statement_origin_enum = Enum(*STATEMENT_ORIGINS, name="statement_origin")
line_kind_enum = Enum(*LINE_KINDS, name="statement_line_kind")
event_status_enum = Enum(*EVENT_STATUSES, name="statement_event_status")
equivalence_enum = Enum(*EQUIVALENCES, name="lineage_equivalence")
coverage_role_enum = Enum(*COVERAGE_ROLES, name="coverage_role")
coverage_status_enum = Enum(*COVERAGE_STATUSES, name="coverage_status")
match_kind_enum = Enum(*MATCH_KINDS, name="match_kind")
case_kind_enum = Enum(*CASE_KINDS, name="case_kind")
case_status_enum = Enum(*CASE_STATUSES, name="case_status")
case_explanation_enum = Enum(*CASE_EXPLANATIONS, name="case_explanation")
reconciliation_actor_enum = Enum(*RECONCILIATION_ACTORS, name="reconciliation_actor")
proposal_status_enum = Enum(*PROPOSAL_STATUSES, name="proposal_status")
action_status_enum = Enum(*ACTION_STATUSES, name="action_status")
dirty_kind_enum = Enum(*DIRTY_KINDS, name="dirty_kind")
dirty_op_enum = Enum(*DIRTY_OPS, name="dirty_op")

MONEY = Numeric(20, 4)
NOW = func.now()


class IngestRun(Base):
    __tablename__ = "ingest_run"
    __table_args__ = (Index("ix_ingest_run_status", "status"),)
    id = Column(Integer, primary_key=True)
    trigger = Column(ingest_trigger_enum, nullable=False)
    principal = Column(String(64), nullable=False)
    initiator_hint = Column(String(64))
    mode = Column(ingest_mode_enum, nullable=False, server_default=text("'live'"))
    status = Column(ingest_status_enum, nullable=False, server_default=text("'queued'"))
    claimed_by = Column(String(64))
    lease_token = Column(String(64))
    lease_expires_at = Column(DateTime(timezone=True))
    attempt = Column(SmallInteger, nullable=False, server_default=text("0"))
    parser_version = Column(String(64))
    rules_version = Column(String(64))
    policy_config_sha256 = Column(String(64))
    mapping_version = Column(String(64))
    credential_version = Column(String(64))
    requested_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    summary = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class StatementFile(Base):
    __tablename__ = "statement_file"
    __table_args__ = (Index("ix_statement_file_status", "status"),)
    id = Column(Integer, primary_key=True)
    sha256 = Column(String(64), nullable=False, unique=True)
    size = Column(BigInteger, nullable=False)
    kind = Column(statement_kind_enum, nullable=False)
    account_id = Column(Integer, ForeignKey("account.id", ondelete="RESTRICT"))
    object_key = Column(String(128), nullable=False)
    status = Column(statement_file_status_enum, nullable=False, server_default=text("'new'"))
    failure = Column(statement_file_failure_enum)
    has_text_layer = Column(Boolean)
    text_chars = Column(Integer)
    pages = Column(SmallInteger)
    credential_version = Column(String(64))
    mapping_version = Column(String(64))
    parser_version = Column(String(64))
    attempts = Column(SmallInteger, nullable=False, server_default=text("0"))
    next_retry_at = Column(DateTime(timezone=True))
    first_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)
    parsed_at = Column(DateTime(timezone=True))
    run_id = Column(Integer, ForeignKey("ingest_run.id", ondelete="RESTRICT"))


class StatementSource(Base):
    __tablename__ = "statement_source"
    __table_args__ = (
        UniqueConstraint("drive_file_id", "drive_md5", name="uq_statement_source_drive_file_md5"),
        Index("ix_statement_source_file_id", "file_id"),
    )
    id = Column(Integer, primary_key=True)
    file_id = Column(Integer, ForeignKey("statement_file.id", ondelete="RESTRICT"), nullable=False)
    root = Column(statement_source_root_enum, nullable=False)
    drive_file_id = Column(String(128), nullable=False)
    drive_path = Column(String(512), nullable=False)
    drive_md5 = Column(String(32), nullable=False)
    drive_size = Column(BigInteger, nullable=False)
    path_history = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    first_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)
    removed_at = Column(DateTime(timezone=True))
    superseded_by_source_id = Column(Integer, ForeignKey("statement_source.id", ondelete="SET NULL"))


class AccountStatement(Base):
    __tablename__ = "account_statement"
    __table_args__ = (
        UniqueConstraint("account_id", "currency", "period_end", name="uq_account_statement_identity"),
        CheckConstraint("period_start <= period_end", name="ck_account_statement_period"),
        Index("ix_account_statement_account_id", "account_id"),
    )
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("account.id", ondelete="RESTRICT"), nullable=False)
    kind = Column(statement_kind_enum, nullable=False)
    currency = Column(String(8), nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    closing_date = Column(Date)
    due_date = Column(Date)
    opening_balance = Column(MONEY)
    statement_total = Column(MONEY, nullable=False)
    minimum_payment = Column(MONEY)
    origin = Column(statement_origin_enum, nullable=False)
    mode = Column(statement_mode_enum, nullable=False)
    status = Column(statement_status_enum, nullable=False, server_default=text("'open'"))
    conflict_open = Column(Boolean, nullable=False, server_default=text("false"))
    needs_recheck = Column(Boolean, nullable=False, server_default=text("false"))
    current_revision_id = Column(Integer)  # FK added by the migration after statement_revision exists (circular)
    swept_through_event_id = Column(BigInteger, nullable=False, server_default=text("0"))
    matched_count = Column(Integer, nullable=False, server_default=text("0"))
    explained_count = Column(Integer, nullable=False, server_default=text("0"))
    open_case_count = Column(Integer, nullable=False, server_default=text("0"))
    note = Column(Text)
    created_run_id = Column(Integer, ForeignKey("ingest_run.id", ondelete="RESTRICT"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW, onupdate=NOW)


class StatementRevision(Base):
    __tablename__ = "statement_revision"
    __table_args__ = (UniqueConstraint("statement_id", "revision", name="uq_statement_revision_number"),)
    id = Column(Integer, primary_key=True)
    statement_id = Column(Integer, ForeignKey("account_statement.id", ondelete="RESTRICT"), nullable=False)
    revision = Column(Integer, nullable=False)
    file_id = Column(Integer, ForeignKey("statement_file.id", ondelete="RESTRICT"))
    parser = Column(String(64), nullable=False)
    parser_version = Column(String(64), nullable=False)
    raw = Column(JSONB, nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    closing_date = Column(Date)
    due_date = Column(Date)
    opening_balance = Column(MONEY)
    statement_total = Column(MONEY, nullable=False)
    minimum_payment = Column(MONEY)
    guardrail = Column(JSONB, nullable=False)
    guardrail_ok = Column(Boolean, nullable=False)
    conflict = Column(Boolean, nullable=False, server_default=text("false"))
    rejected = Column(Boolean, nullable=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)
    run_id = Column(Integer, ForeignKey("ingest_run.id", ondelete="RESTRICT"))


class StatementEvent(Base):
    __tablename__ = "statement_event"
    __table_args__ = (Index("ix_statement_event_statement_id", "statement_id"),)
    id = Column(Integer, primary_key=True)
    statement_id = Column(Integer, ForeignKey("account_statement.id", ondelete="RESTRICT"), nullable=False)
    first_revision_id = Column(Integer, ForeignKey("statement_revision.id", ondelete="RESTRICT"), nullable=False)
    first_line_id = Column(Integer)  # FK added by the migration (circular with statement_line)
    current_line_id = Column(Integer)
    status = Column(event_status_enum, nullable=False, server_default=text("'live'"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)


class StatementLine(Base):
    __tablename__ = "statement_line"
    __table_args__ = (
        UniqueConstraint("revision_id", "seq", name="uq_statement_line_revision_seq"),
        Index("ix_statement_line_event_id", "event_id"),
        Index("ix_statement_line_revision_logical", "revision_id", "logical_key"),
    )
    id = Column(Integer, primary_key=True)
    revision_id = Column(Integer, ForeignKey("statement_revision.id", ondelete="RESTRICT"), nullable=False)
    event_id = Column(Integer, ForeignKey("statement_event.id", ondelete="RESTRICT"), nullable=False)
    seq = Column(Integer, nullable=False)
    canonical_key = Column(String(64), nullable=False)
    logical_key = Column(String(80), nullable=False)
    txn_date = Column(Date)
    posted_date = Column(Date, nullable=False)
    merchant_raw = Column(String(256), nullable=False)
    merchant_norm = Column(String(256), nullable=False)
    printed_amount = Column(MONEY, nullable=False)
    flow_amount = Column(MONEY, nullable=False)
    foreign_amount = Column(MONEY)
    foreign_currency = Column(String(8))
    line_kind = Column(line_kind_enum, nullable=False)
    installment_seq = Column(SmallInteger)
    installment_total = Column(SmallInteger)


class LineLineage(Base):
    __tablename__ = "line_lineage"
    __table_args__ = (Index("ix_line_lineage_event_id", "event_id"),)
    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("statement_event.id", ondelete="RESTRICT"), nullable=False)
    old_line_id = Column(Integer, ForeignKey("statement_line.id", ondelete="RESTRICT"))
    new_line_id = Column(Integer, ForeignKey("statement_line.id", ondelete="RESTRICT"))
    equivalence = Column(equivalence_enum, nullable=False)
    transferred = Column(Boolean, nullable=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)


class StatementCoverage(Base):
    __tablename__ = "statement_coverage"
    __table_args__ = (
        Index("ux_statement_coverage_active_entry", "entry_id", unique=True,
              postgresql_where=text("status = 'active' AND entry_id IS NOT NULL")),
        Index("ix_statement_coverage_statement_id", "statement_id"),
        Index("ix_statement_coverage_event_id", "event_id"),
    )
    id = Column(Integer, primary_key=True)
    statement_id = Column(Integer, ForeignKey("account_statement.id", ondelete="RESTRICT"), nullable=False)
    event_id = Column(Integer, ForeignKey("statement_event.id", ondelete="RESTRICT"), nullable=False)
    line_id = Column(Integer, ForeignKey("statement_line.id", ondelete="RESTRICT"), nullable=False)
    entry_id = Column(Integer, ForeignKey("ledger_entry.id", ondelete="SET NULL"))
    group_id = Column(Integer, ForeignKey("entry_group.id", ondelete="SET NULL"))
    role = Column(coverage_role_enum, nullable=False)
    snapshot = Column(JSONB, nullable=False)
    match_kind = Column(match_kind_enum, nullable=False)
    match_rule = Column(String(32), nullable=False)
    status = Column(coverage_status_enum, nullable=False, server_default=text("'active'"))
    stale_reason = Column(String(64))
    run_id = Column(Integer, ForeignKey("ingest_run.id", ondelete="RESTRICT"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)


class CoverageDirty(Base):
    __tablename__ = "coverage_dirty"
    __table_args__ = (
        Index("ix_coverage_dirty_old_account", "old_account_id", "id"),
        Index("ix_coverage_dirty_new_account", "new_account_id", "id"),
    )
    id = Column(BigInteger, primary_key=True)
    kind = Column(dirty_kind_enum, nullable=False)
    row_id = Column(Integer, nullable=False)
    op = Column(dirty_op_enum, nullable=False)
    old_account_id = Column(Integer)
    new_account_id = Column(Integer)
    old_date = Column(Date)
    new_date = Column(Date)
    old_row = Column(JSONB)
    new_row = Column(JSONB)
    action_id = Column(Integer)
    happened_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)


class ReconciliationCase(Base):
    __tablename__ = "reconciliation_case"
    __table_args__ = (
        Index("ix_reconciliation_case_statement_status", "statement_id", "status"),
        Index("ix_reconciliation_case_event_id", "event_id"),
    )
    id = Column(Integer, primary_key=True)
    statement_id = Column(Integer, ForeignKey("account_statement.id", ondelete="RESTRICT"), nullable=False)
    revision_id = Column(Integer, ForeignKey("statement_revision.id", ondelete="RESTRICT"), nullable=False)
    kind = Column(case_kind_enum, nullable=False)
    event_id = Column(Integer, ForeignKey("statement_event.id", ondelete="RESTRICT"))
    line_id = Column(Integer, ForeignKey("statement_line.id", ondelete="RESTRICT"))
    entry_id = Column(Integer, ForeignKey("ledger_entry.id", ondelete="SET NULL"))
    candidates = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    context = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status = Column(case_status_enum, nullable=False, server_default=text("'open'"))
    explanation = Column(case_explanation_enum)
    deferred_to_period_end = Column(Date)
    deferred_to_statement_id = Column(Integer, ForeignKey("account_statement.id", ondelete="SET NULL"))
    resolved_by = Column(reconciliation_actor_enum)
    resolved_action_id = Column(Integer)
    version = Column(Integer, nullable=False, server_default=text("1"))
    note = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)
    resolved_at = Column(DateTime(timezone=True))


class ReconciliationProposal(Base):
    __tablename__ = "reconciliation_proposal"
    __table_args__ = (Index("ix_reconciliation_proposal_case_status", "case_id", "status"),)
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("reconciliation_case.id", ondelete="RESTRICT"), nullable=False)
    case_version = Column(Integer, nullable=False)
    event_id = Column(Integer, ForeignKey("statement_event.id", ondelete="RESTRICT"))
    action = Column(String(32), nullable=False)
    params = Column(JSONB, nullable=False)
    rationale = Column(String(1000), nullable=False)
    confidence = Column(Numeric(4, 3), nullable=False)
    author = Column(String(64), nullable=False)
    status = Column(proposal_status_enum, nullable=False, server_default=text("'pending'"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)
    decided_at = Column(DateTime(timezone=True))
    applied_action_id = Column(Integer)


class ReconciliationAction(Base):
    __tablename__ = "reconciliation_action"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_reconciliation_action_idempotency"),
        Index("ux_reconciliation_action_effect", "event_id", "action", "effect_slot", unique=True,
              postgresql_where=text("status = 'applied'")),
        Index("ix_reconciliation_action_case_id", "case_id"),
    )
    id = Column(Integer, primary_key=True)
    idempotency_key = Column(String(64), nullable=False)
    event_id = Column(Integer, ForeignKey("statement_event.id", ondelete="RESTRICT"))
    effect_slot = Column(String(16), nullable=False)
    case_id = Column(Integer, ForeignKey("reconciliation_case.id", ondelete="RESTRICT"), nullable=False)
    proposal_id = Column(Integer, ForeignKey("reconciliation_proposal.id", ondelete="RESTRICT"))
    action = Column(String(32), nullable=False)
    params = Column(JSONB, nullable=False)
    actor = Column(reconciliation_actor_enum, nullable=False)
    run_id = Column(Integer, ForeignKey("ingest_run.id", ondelete="RESTRICT"))
    status = Column(action_status_enum, nullable=False)
    created_entry_ids = Column(ARRAY(Integer), nullable=False, server_default=text("'{}'::integer[]"))
    deleted_entry_ids = Column(ARRAY(Integer), nullable=False, server_default=text("'{}'::integer[]"))
    touched_entry_ids = Column(ARRAY(Integer), nullable=False, server_default=text("'{}'::integer[]"))
    touched_group_ids = Column(ARRAY(Integer), nullable=False, server_default=text("'{}'::integer[]"))
    before = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    after = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    inverse = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    reverted_by_action_id = Column(Integer)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)


class ReconciliationAudit(Base):
    __tablename__ = "reconciliation_audit"
    __table_args__ = (Index("ix_reconciliation_audit_statement_id", "statement_id", "id"),)
    id = Column(Integer, primary_key=True)
    statement_id = Column(Integer, ForeignKey("account_statement.id", ondelete="RESTRICT"))
    case_id = Column(Integer, ForeignKey("reconciliation_case.id", ondelete="RESTRICT"))
    action_id = Column(Integer, ForeignKey("reconciliation_action.id", ondelete="RESTRICT"))
    run_id = Column(Integer, ForeignKey("ingest_run.id", ondelete="RESTRICT"))
    event = Column(String(48), nullable=False)
    actor = Column(String(64), nullable=False)
    request_id = Column(String(64))
    detail = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)


class PolicyBudget(Base):
    __tablename__ = "policy_budget"
    month = Column(Date, primary_key=True)
    currency = Column(String(8), primary_key=True)
    used_amount = Column(MONEY, nullable=False, server_default=text("0"))
    used_count = Column(Integer, nullable=False, server_default=text("0"))


class InstallmentPlanMap(Base):
    __tablename__ = "installment_plan_map"
    __table_args__ = (UniqueConstraint("account_id", "plan_key", name="uq_installment_plan_map_account_key"),)
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("account.id", ondelete="RESTRICT"), nullable=False)
    plan_key = Column(String(300), nullable=False)
    definition_id = Column(Integer, ForeignKey("schedule_definition.id", ondelete="RESTRICT"), nullable=False)
    confirmed_by = Column(reconciliation_actor_enum, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)


class ReconciliationSettings(Base):
    __tablename__ = "reconciliation_settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_reconciliation_settings_single_row"),)
    id = Column(Integer, primary_key=True, autoincrement=False, server_default=text("1"))
    data = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    version = Column(Integer, nullable=False, server_default=text("1"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW, onupdate=NOW)


# --- dirty triggers (design §4.6) -------------------------------------------------------------------
DIRTY_ENTRY_FUNCTION_SQL = """
CREATE FUNCTION reconciliation_dirty_entry() RETURNS trigger AS $$
DECLARE
    v_action integer := NULLIF(current_setting('app.reconciliation_action_id', true), '')::integer;
BEGIN
    IF TG_OP = 'UPDATE' AND (to_jsonb(OLD) - 'updated_at') = (to_jsonb(NEW) - 'updated_at') THEN
        RETURN NULL;
    END IF;
    INSERT INTO coverage_dirty (kind, row_id, op, old_account_id, new_account_id, old_date, new_date, old_row, new_row, action_id)
    VALUES (
        'entry',
        COALESCE(NEW.id, OLD.id),
        lower(TG_OP)::dirty_op,
        CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.account_id END,
        CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE NEW.account_id END,
        CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.posted_date END,
        CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE NEW.posted_date END,
        CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END,
        CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END,
        v_action
    );
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""
DIRTY_ENTRY_TRIGGER_SQL = """
CREATE TRIGGER trg_ledger_entry_reconciliation_dirty
AFTER INSERT OR UPDATE OR DELETE ON ledger_entry
FOR EACH ROW EXECUTE FUNCTION reconciliation_dirty_entry()
"""
DIRTY_GROUP_FUNCTION_SQL = """
CREATE FUNCTION reconciliation_dirty_group() RETURNS trigger AS $$
DECLARE
    v_action integer := NULLIF(current_setting('app.reconciliation_action_id', true), '')::integer;
BEGIN
    IF TG_OP = 'UPDATE' AND to_jsonb(OLD) = to_jsonb(NEW) THEN
        RETURN NULL;
    END IF;
    INSERT INTO coverage_dirty (kind, row_id, op, old_row, new_row, action_id)
    VALUES ('group', COALESCE(NEW.id, OLD.id), lower(TG_OP)::dirty_op,
            CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END,
            CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END, v_action);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""
DIRTY_GROUP_TRIGGER_SQL = """
CREATE TRIGGER trg_entry_group_reconciliation_dirty
AFTER INSERT OR UPDATE OR DELETE ON entry_group
FOR EACH ROW EXECUTE FUNCTION reconciliation_dirty_group()
"""
DIRTY_ACCOUNT_FUNCTION_SQL = """
CREATE FUNCTION reconciliation_dirty_account() RETURNS trigger AS $$
BEGIN
    IF OLD.opening_balance IS NOT DISTINCT FROM NEW.opening_balance
       AND OLD.currency IS NOT DISTINCT FROM NEW.currency
       AND OLD.combined_account_id IS NOT DISTINCT FROM NEW.combined_account_id
       AND OLD.closing_day IS NOT DISTINCT FROM NEW.closing_day
       AND OLD.due_rule IS NOT DISTINCT FROM NEW.due_rule
       AND OLD.due_value IS NOT DISTINCT FROM NEW.due_value
       AND OLD.is_credit IS NOT DISTINCT FROM NEW.is_credit
       AND OLD.is_archived IS NOT DISTINCT FROM NEW.is_archived THEN
        RETURN NULL;
    END IF;
    INSERT INTO coverage_dirty (kind, row_id, op, old_account_id, new_account_id, old_row, new_row)
    VALUES ('account', NEW.id, 'update', OLD.id, NEW.id, to_jsonb(OLD), to_jsonb(NEW));
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""
DIRTY_ACCOUNT_TRIGGER_SQL = """
CREATE TRIGGER trg_account_reconciliation_dirty
AFTER UPDATE ON account
FOR EACH ROW EXECUTE FUNCTION reconciliation_dirty_account()
"""


def _install(table, *sql):
    for statement in sql:
        event.listen(table, "after_create", DDL(statement))


# Installed on coverage_dirty's own after_create so the queue table exists first; create_all orders
# by dependency and coverage_dirty has none, so we hang the listeners off the LAST created table instead:
from app.models.ledger import Account, EntryGroup, LedgerEntry  # noqa: E402

_install(CoverageDirty.__table__, DIRTY_ENTRY_FUNCTION_SQL, DIRTY_ENTRY_TRIGGER_SQL,
         DIRTY_GROUP_FUNCTION_SQL, DIRTY_GROUP_TRIGGER_SQL, DIRTY_ACCOUNT_FUNCTION_SQL, DIRTY_ACCOUNT_TRIGGER_SQL)
```

Ordering note for `create_all`: `coverage_dirty` has no FKs, so SQLAlchemy may create it before `ledger_entry`. The listeners therefore run `CREATE TRIGGER ... ON ledger_entry` possibly before that table exists. Solution used by the existing codebase for the posted-date trigger: hang the DDL on the table it targets. Do that instead of the `_install(CoverageDirty...)` call above — replace the last block with:

```python
event.listen(LedgerEntry.__table__, "after_create", DDL(DIRTY_ENTRY_FUNCTION_SQL))
event.listen(LedgerEntry.__table__, "after_create", DDL(DIRTY_ENTRY_TRIGGER_SQL))
event.listen(EntryGroup.__table__, "after_create", DDL(DIRTY_GROUP_FUNCTION_SQL))
event.listen(EntryGroup.__table__, "after_create", DDL(DIRTY_GROUP_TRIGGER_SQL))
event.listen(Account.__table__, "after_create", DDL(DIRTY_ACCOUNT_FUNCTION_SQL))
event.listen(Account.__task__ if False else Account.__table__, "after_create", DDL(DIRTY_ACCOUNT_TRIGGER_SQL))
```

and make `coverage_dirty` sort first by giving `CoverageDirty` a `ForeignKey`-free table but listing `app.models.statements` **before** `ledger` in `app/models/__init__.py` is not enough: `create_all` sorts by FK dependency and then by definition order; `coverage_dirty` (no deps) is created in the first wave, before `ledger_entry` (which depends on account etc.). The triggers are created `after_create` of `ledger_entry`/`entry_group`/`account`, by which time `coverage_dirty` exists. Keep the per-target listeners; delete the `_install` helper. (Write only the final form into the file; this paragraph is the reasoning.)

Ruling on `account.balance_generation` (§4.10/§7.2 C4): the spec's column would make every entry write `UPDATE account` inside the entry trigger, i.e. take an account row lock after entry locks — exactly the inversion D32 forbids (a transfer edit locking entries in two accounts in id order vs another edit locking them in the other order deadlocks). The generation is therefore **derived**: `generation(account) = max(coverage_dirty.id) WHERE old_account_id = :a OR new_account_id = :a` (both columns indexed above). Same compare-and-write semantics, no extra lock. Record in the PR description and in the spec's §16 list when R1c implements the action.

- [ ] **Step 4: Add the ledger changes**

In `app/models/ledger.py`: `ENTRY_SOURCES = ("moze_import", "moze_backup", "manual", "hermes", "rule", "schedule", "statement")` and, on `Account`:

```python
    statement_password_rule = Column(String(64), nullable=True)
    statement_live_from = Column(Date, nullable=True)
    statement_source_root = Column(Enum("mail", "manual", name="statement_source_root", create_type=False), nullable=True)
```

(`create_type=False` because `app/models/statements.py` owns the type; import order: `ledger` is imported first by `__init__`, so declare the enum here with `create_type=False` and in `statements.py` with the default so `create_all` creates it once — verify with `test_models_schema`.)

In `app/models/__init__.py` add `from app.models.statements import *  # noqa` equivalents: explicit names for every class and tuple above.

- [ ] **Step 5: Run the model tests expecting the migration gap**

Run: `.venv/bin/pytest -q tests/integration/test_statement_models.py`
Expected: FAIL on `test_statement_tables_exist_after_migration` (no migration yet) — the import error is gone. Proceed to Task 4.

- [ ] **Step 6: Commit**

```bash
git add services/accounting-service/app/models
git commit -m "feat(accounting): reconciliation models, dirty triggers and the statement entry source"
```

---

### Task 4: Alembic migration `d1f3a7c2e9b4_statement_tables`

**Files:**
- Create: `services/accounting-service/alembic/versions/d1f3a7c2e9b4_statement_tables.py`
- Modify: `services/accounting-service/tests/conftest.py` (`LEDGER_TABLES`), `services/accounting-service/tests/integration/test_migration.py` (`RECONCILE_HEAD`)

**Interfaces:**
- Produces: revision `d1f3a7c2e9b4`, `down_revision = "c4e8b2f1a7d3"`.

- [ ] **Step 1: Update the head pins and the truncate list (tests first)**

In `tests/integration/test_migration.py`: add `RECONCILE_HEAD = "d1f3a7c2e9b4"`; every assertion that expected `SCHEDULES_HEAD` as the current head after `upgrade head` now expects `RECONCILE_HEAD`; the test that downgrades head → `PHASE_2A_HEAD` keeps its target. Add:

```python
def test_reconcile_downgrade_refuses_with_statement_rows(database_factory, alembic_config):
    url = database_factory()
    cfg = alembic_config(url)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO account (name, currency) VALUES ('A', 'TWD')"))
        conn.execute(text("INSERT INTO ingest_run (trigger, principal) VALUES ('timer', 'worker')"))
    with pytest.raises(RuntimeError, match="refusing to downgrade d1f3a7c2e9b4"):
        command.downgrade(cfg, SCHEDULES_HEAD)
```

In `tests/conftest.py` prepend the new tables to `LEDGER_TABLES` so TRUNCATE … CASCADE covers them:

```python
LEDGER_TABLES = (
    "reconciliation_audit, reconciliation_action, reconciliation_proposal, reconciliation_case, statement_coverage, "
    "line_lineage, statement_line, statement_event, statement_revision, account_statement, statement_source, "
    "statement_file, ingest_run, coverage_dirty, policy_budget, installment_plan_map, reconciliation_settings, "
    "entry_reward_rule, schedule_instance, schedule_definition, ledger_entry, reward_rule, entry_group, counterparty, "
    "import_run, category, project, account, account_group, fx_rate, preference"
)
```

- [ ] **Step 2: Run to verify the migration tests fail**

Run: `.venv/bin/pytest -q tests/integration/test_migration.py -k "head or reconcile"`
Expected: FAIL (`d1f3a7c2e9b4` unknown).

- [ ] **Step 3: Write the migration**

```python
"""statement reconciliation tables

Revision ID: d1f3a7c2e9b4
Revises: c4e8b2f1a7d3
Create Date: 2026-10-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1f3a7c2e9b4"
down_revision: Union[str, Sequence[str], None] = "c4e8b2f1a7d3"
branch_labels = None
depends_on = None

NEW_ENUMS = {
    "statement_kind": ("card", "bank"),
    "statement_file_status": ("new", "unlocked", "parsed", "needs_review", "failed", "ignored"),
    "statement_file_failure": ("password", "no_text_layer", "too_large", "parse", "guardrail", "mapping", "transient", "sandbox"),
    "statement_source_root": ("mail", "manual"),
    "ingest_trigger": ("timer", "owner_cli", "enqueue"),
    "ingest_mode": ("live", "backfill"),
    "ingest_status": ("queued", "claimed", "running", "done", "failed", "expired"),
    "statement_mode": ("historical", "live"),
    "statement_status": ("open", "reconciled"),
    "statement_origin": ("manual", "import"),
    "statement_line_kind": ("purchase", "refund", "payment", "fee", "interest", "reward", "installment", "balance_adjustment",
                            "deposit", "withdrawal", "transfer_in", "transfer_out", "unknown"),
    "statement_event_status": ("live", "retired", "quarantined"),
    "lineage_equivalence": ("identical", "normalised", "changed", "unpaired"),
    "coverage_role": ("principal", "child", "member"),
    "coverage_status": ("active", "stale"),
    "match_kind": ("auto", "manual", "agent"),
    "case_kind": ("line_unmatched", "entry_unmatched", "amount_delta", "ambiguous", "duplicate_claim", "balance_gap",
                  "statement_conflict", "parse_review", "recheck"),
    "case_status": ("open", "proposed", "resolved", "dismissed", "superseded"),
    "case_explanation": ("deferred_next_period", "accepted_exception"),
    "reconciliation_actor": ("owner", "policy"),
    "proposal_status": ("pending", "applied", "rejected", "superseded"),
    "action_status": ("applied", "reverted", "failed"),
    "dirty_kind": ("entry", "group", "account"),
    "dirty_op": ("insert", "update", "delete"),
}
MONEY = sa.Numeric(20, 4)
NOW = sa.func.now()
JSONB = postgresql.JSONB
# Trigger SQL copied verbatim from app/models/statements.py (migrations never import app code).
DIRTY_ENTRY_FUNCTION_SQL = """..."""   # paste exactly
DIRTY_ENTRY_TRIGGER_SQL = """..."""
DIRTY_GROUP_FUNCTION_SQL = """..."""
DIRTY_GROUP_TRIGGER_SQL = """..."""
DIRTY_ACCOUNT_FUNCTION_SQL = """..."""
DIRTY_ACCOUNT_TRIGGER_SQL = """..."""
DOWNGRADE_GUARDS = {
    "statement-created entries": "SELECT count(*) FROM ledger_entry WHERE source = 'statement'",
    "coverage rows": "SELECT count(*) FROM statement_coverage",
    "reconciliation actions": "SELECT count(*) FROM reconciliation_action",
    "audit rows": "SELECT count(*) FROM reconciliation_audit",
    "ingest runs": "SELECT count(*) FROM ingest_run",
}
TABLES_IN_DROP_ORDER = (
    "reconciliation_audit", "reconciliation_action", "reconciliation_proposal", "reconciliation_case",
    "statement_coverage", "line_lineage", "statement_line", "statement_event", "statement_revision",
    "account_statement", "statement_source", "statement_file", "ingest_run", "coverage_dirty", "policy_budget",
    "installment_plan_map", "reconciliation_settings",
)


def _enum(name):
    return postgresql.ENUM(*NEW_ENUMS[name], name=name, create_type=False)


def _ts(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, server_default=NOW)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE entry_source ADD VALUE IF NOT EXISTS 'statement'")
    connection = op.get_bind()
    for name, values in NEW_ENUMS.items():
        postgresql.ENUM(*values, name=name).create(connection)

    op.add_column("account", sa.Column("statement_password_rule", sa.String(64), nullable=True))
    op.add_column("account", sa.Column("statement_live_from", sa.Date(), nullable=True))
    op.add_column("account", sa.Column("statement_source_root", _enum("statement_source_root"), nullable=True))

    op.create_table(
        "ingest_run",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("trigger", _enum("ingest_trigger"), nullable=False),
        sa.Column("principal", sa.String(64), nullable=False),
        sa.Column("initiator_hint", sa.String(64)),
        sa.Column("mode", _enum("ingest_mode"), nullable=False, server_default=sa.text("'live'")),
        sa.Column("status", _enum("ingest_status"), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("claimed_by", sa.String(64)),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("attempt", sa.SmallInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("parser_version", sa.String(64)), sa.Column("rules_version", sa.String(64)),
        sa.Column("policy_config_sha256", sa.String(64)), sa.Column("mapping_version", sa.String(64)),
        sa.Column("credential_version", sa.String(64)),
        _ts("requested_at"), sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("summary", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_ingest_run_status", "ingest_run", ["status"])
    # ... statement_file, statement_source, account_statement, statement_revision, statement_event, statement_line,
    #     line_lineage, statement_coverage, coverage_dirty, reconciliation_case, reconciliation_proposal,
    #     reconciliation_action, reconciliation_audit, policy_budget, installment_plan_map, reconciliation_settings:
    #     one op.create_table per model, columns/types/defaults/FKs/indexes EXACTLY as in app/models/statements.py.
    #     Circular FKs added afterwards:
    op.create_foreign_key("fk_account_statement_current_revision", "account_statement", "statement_revision",
                          ["current_revision_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_statement_event_first_line", "statement_event", "statement_line", ["first_line_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_statement_event_current_line", "statement_event", "statement_line", ["current_line_id"], ["id"], ondelete="SET NULL")
    for sql in (DIRTY_ENTRY_FUNCTION_SQL, DIRTY_ENTRY_TRIGGER_SQL, DIRTY_GROUP_FUNCTION_SQL, DIRTY_GROUP_TRIGGER_SQL,
                DIRTY_ACCOUNT_FUNCTION_SQL, DIRTY_ACCOUNT_TRIGGER_SQL):
        op.execute(sql)


def _assert_downgradable(connection) -> None:
    found = []
    for label, sql in DOWNGRADE_GUARDS.items():
        count = connection.execute(sa.text(sql)).scalar_one()
        if count:
            found.append(f"{count} {label}")
    if found:
        raise RuntimeError("refusing to downgrade d1f3a7c2e9b4: " + "; ".join(found))


def downgrade() -> None:
    for table in TABLES_IN_DROP_ORDER:
        op.execute(f'LOCK TABLE "{table}" IN ACCESS EXCLUSIVE MODE')
    _assert_downgradable(op.get_bind())
    op.execute("DROP TRIGGER trg_account_reconciliation_dirty ON account")
    op.execute("DROP FUNCTION reconciliation_dirty_account()")
    op.execute("DROP TRIGGER trg_entry_group_reconciliation_dirty ON entry_group")
    op.execute("DROP FUNCTION reconciliation_dirty_group()")
    op.execute("DROP TRIGGER trg_ledger_entry_reconciliation_dirty ON ledger_entry")
    op.execute("DROP FUNCTION reconciliation_dirty_entry()")
    op.drop_constraint("fk_statement_event_current_line", "statement_event", type_="foreignkey")
    op.drop_constraint("fk_statement_event_first_line", "statement_event", type_="foreignkey")
    op.drop_constraint("fk_account_statement_current_revision", "account_statement", type_="foreignkey")
    for table in TABLES_IN_DROP_ORDER:
        op.drop_table(table)
    op.drop_column("account", "statement_source_root")
    op.drop_column("account", "statement_live_from")
    op.drop_column("account", "statement_password_rule")
    for name in reversed(list(NEW_ENUMS)):
        op.execute(f"DROP TYPE {name}")
    # 'statement' stays in entry_source: the guard above guarantees no row uses it, and shrinking the enum would
    # rewrite ledger_entry; the same choice c4e8b2f1a7d3 made for 'schedule' is kept here.
```

The models must declare the three circular FKs the same way for `test_head_schema_matches_the_models` to agree: add to `AccountStatement` `ForeignKeyConstraint(["current_revision_id"], ["statement_revision.id"], name="fk_account_statement_current_revision", use_alter=True, ondelete="RESTRICT")` in `__table_args__`, and the two on `StatementEvent` with `use_alter=True`. (Adjust the model file from Task 3 accordingly; `use_alter` makes `create_all` emit them as `ALTER TABLE` after both tables exist.)

- [ ] **Step 4: Run migration, schema-parity and model tests**

Run: `.venv/bin/pytest -q tests/integration/test_migration.py tests/integration/test_models_schema.py tests/integration/test_statement_models.py`
Expected: PASS, including `test_head_schema_matches_the_models` (fix any type/default mismatch in the migration until the comparison is exact), and the five trigger tests.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: PASS. (Existing tests that count queries or inspect `ledger_entry` writes are unaffected; the trigger inserts into `coverage_dirty` only.)

- [ ] **Step 6: Commit**

```bash
git add services/accounting-service/alembic/versions/d1f3a7c2e9b4_statement_tables.py services/accounting-service/app/models/statements.py services/accounting-service/tests/conftest.py services/accounting-service/tests/integration/test_migration.py
git commit -m "feat(accounting): migration d1f3a7c2e9b4 for the reconciliation schema with dirty triggers and downgrade guards"
```

---

### Task 5: Pure modules — flow signs, guardrails, merchant normaliser, keys

**Files:**
- Create: `services/accounting-service/app/services/statements/__init__.py` (empty), `app/services/statements/derive.py`, `app/services/statements/merchant.py`
- Test: `services/accounting-service/tests/unit/test_statement_derive.py`, `tests/unit/test_statement_merchant.py`

**Interfaces:**
- Produces:
  - `merchant.normalise(raw: str) -> str` (uppercase, NFKC, punctuation/whitespace collapsed to single spaces, known prefixes `PAYPAL *`, `AMZN MKTP`, `SQ *`, `GOOGLE *`, `APPLE.COM/BILL` collapsed to `PAYPAL`, `AMAZON`, `SQUARE`, `GOOGLE`, `APPLE`)
  - `merchant.tokens(norm: str) -> frozenset[str]` (tokens of length ≥ 2)
  - `derive.LineIn` (dataclass: `seq, txn_date, posted_date, merchant_raw, printed_amount: Decimal, foreign_amount, foreign_currency, line_kind, installment_seq, installment_total, is_subtotal`)
  - `derive.DerivedLine` (adds `merchant_norm, flow_amount, canonical_key, logical_key`)
  - `derive.flow_amount(kind: str, line_kind: str, printed: Decimal) -> Decimal`
  - `derive.derive_lines(kind: str, lines: list[LineIn]) -> list[DerivedLine]` (drops subtotals, assigns occurrence indexes, computes keys)
  - `derive.Header` (dataclass: `period_start, period_end, closing_date, due_date, opening_balance, statement_total, minimum_payment, currency`)
  - `derive.guardrails(kind: str, header: Header, account_currency: str, lines: list[DerivedLine]) -> GuardrailResult` with `.ok: bool`, `.checks: dict[str, bool]`, `.detail: dict`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_statement_derive.py
from datetime import date
from decimal import Decimal

import pytest

from app.services.statements import derive
from app.services.statements.derive import Header, LineIn


def L(seq, posted, printed, kind="purchase", merchant="全聯", **kw):
    return LineIn(seq=seq, txn_date=kw.get("txn_date"), posted_date=posted, merchant_raw=merchant,
                  printed_amount=Decimal(printed), foreign_amount=kw.get("foreign_amount"), foreign_currency=kw.get("foreign_currency"),
                  line_kind=kind, installment_seq=kw.get("installment_seq"), installment_total=kw.get("installment_total"),
                  is_subtotal=kw.get("is_subtotal", False))


@pytest.mark.parametrize("kind,line_kind,printed,expected", [
    ("card", "purchase", "580", "-580"), ("card", "fee", "15", "-15"), ("card", "interest", "3", "-3"),
    ("card", "installment", "1000", "-1000"), ("card", "payment", "-5000", "5000"), ("card", "refund", "-200", "200"),
    ("card", "reward", "-50", "50"), ("card", "balance_adjustment", "7", "-7"),
    ("bank", "deposit", "30000", "30000"), ("bank", "withdrawal", "-1200", "-1200"), ("bank", "fee", "-15", "-15"),
    ("bank", "interest", "12", "12"), ("bank", "transfer_in", "500", "500"), ("bank", "transfer_out", "-500", "-500"),
])
def test_flow_amount_follows_the_ledger_sign(kind, line_kind, printed, expected):
    assert derive.flow_amount(kind, line_kind, Decimal(printed)) == Decimal(expected)


@pytest.mark.parametrize("kind,line_kind,printed", [
    ("card", "purchase", "-1"), ("card", "payment", "1"), ("card", "refund", "1"), ("bank", "deposit", "-1"),
    ("bank", "withdrawal", "1"), ("card", "purchase", "0"),
])
def test_flow_amount_rejects_sign_inconsistent_lines(kind, line_kind, printed):
    with pytest.raises(derive.SignError):
        derive.flow_amount(kind, line_kind, Decimal(printed))


def test_derive_lines_drops_subtotals_and_numbers_twins():
    lines = derive.derive_lines("card", [
        L(1, date(2026, 9, 3), "580"), L(2, date(2026, 9, 3), "580"), L(3, date(2026, 9, 3), "1000", is_subtotal=True),
        L(4, date(2026, 9, 4), "580"),
    ])
    assert [l.seq for l in lines] == [1, 2, 4]
    assert [l.logical_key for l in lines] == ["2026-09-03|-580.0000|0", "2026-09-03|-580.0000|1", "2026-09-04|-580.0000|0"]
    assert lines[0].canonical_key == lines[1].canonical_key and lines[0].canonical_key != lines[2].canonical_key
    assert lines[0].flow_amount == Decimal("-580")


def test_canonical_key_ignores_whitespace_case_but_not_merchant_change():
    a = derive.derive_lines("card", [L(1, date(2026, 9, 3), "580", merchant="PAYPAL *Spotify")])[0]
    b = derive.derive_lines("card", [L(1, date(2026, 9, 3), "580", merchant="paypal  * SPOTIFY")])[0]
    c = derive.derive_lines("card", [L(1, date(2026, 9, 3), "580", merchant="PAYPAL *Netflix")])[0]
    assert a.canonical_key == b.canonical_key != c.canonical_key


def _hdr(total, opening=None, currency="TWD", start=date(2026, 9, 1), end=date(2026, 9, 30)):
    return Header(period_start=start, period_end=end, closing_date=end, due_date=None,
                  opening_balance=Decimal(opening) if opening is not None else None, statement_total=Decimal(total),
                  minimum_payment=None, currency=currency)


def test_card_equation_total_equals_opening_minus_flow():
    lines = derive.derive_lines("card", [L(1, date(2026, 9, 3), "580"), L(2, date(2026, 9, 10), "-500", kind="payment")])
    ok = derive.guardrails("card", _hdr("1080", opening="1000"), "TWD", lines)
    assert ok.ok and ok.checks["equation"] and ok.detail["sum_flow"] == "-80.0000"
    bad = derive.guardrails("card", _hdr("1081", opening="1000"), "TWD", lines)
    assert not bad.ok and not bad.checks["equation"]


def test_card_missing_opening_assumes_zero_and_flags_it():
    lines = derive.derive_lines("card", [L(1, date(2026, 9, 3), "580")])
    res = derive.guardrails("card", _hdr("580"), "TWD", lines)
    assert res.ok and res.detail["opening_assumed_zero"] is True


def test_bank_equation_requires_opening():
    lines = derive.derive_lines("bank", [L(1, date(2026, 9, 3), "30000", kind="deposit")])
    assert not derive.guardrails("bank", _hdr("30000"), "TWD", lines).ok
    assert derive.guardrails("bank", _hdr("31000", opening="1000"), "TWD", lines).ok


@pytest.mark.parametrize("bad", [
    dict(hdr=_hdr("580", currency="USD")),                                             # currency mismatch
    dict(hdr=_hdr("580", start=date(2026, 6, 1))),                                      # period > 62 d
    dict(lines=[L(1, date(2026, 10, 20), "580")]),                                      # posted outside window
    dict(lines=[L(1, date(2026, 9, 3), "580", kind="installment", installment_seq=5, installment_total=3)]),
])
def test_other_guardrails(bad):
    lines = derive.derive_lines("card", bad.get("lines", [L(1, date(2026, 9, 3), "580")]))
    assert not derive.guardrails("card", bad.get("hdr", _hdr("580")), "TWD", lines).ok
```

```python
# tests/unit/test_statement_merchant.py
from app.services.statements import merchant


def test_normalise_collapses_case_space_and_prefixes():
    assert merchant.normalise("  paypal *Spotify  ") == "PAYPAL SPOTIFY"
    assert merchant.normalise("AMZN Mktp JP*AB12") == "AMAZON JP AB12"
    assert merchant.normalise("全聯福利中心-大安") == "全聯福利中心 大安"


def test_tokens_drop_single_characters():
    assert merchant.tokens("PAYPAL SPOTIFY 7") == frozenset({"PAYPAL", "SPOTIFY"})
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest -q tests/unit/test_statement_derive.py tests/unit/test_statement_merchant.py`
Expected: FAIL with `ModuleNotFoundError: app.services.statements`.

- [ ] **Step 3: Implement `merchant.py`**

```python
import re
import unicodedata

_PREFIXES = (
    (re.compile(r"^PAYPAL\s*\*\s*"), "PAYPAL "),
    (re.compile(r"^AMZN\s+MKTP\s*"), "AMAZON "),
    (re.compile(r"^SQ\s*\*\s*"), "SQUARE "),
    (re.compile(r"^GOOGLE\s*\*\s*"), "GOOGLE "),
    (re.compile(r"^APPLE\.COM/BILL\s*"), "APPLE "),
)
_NON_TOKEN = re.compile(r"[^0-9A-Z一-鿿]+")


def normalise(raw: str) -> str:
    text = unicodedata.normalize("NFKC", raw or "").upper().strip()
    for pattern, replacement in _PREFIXES:
        text = pattern.sub(replacement, text)
    return " ".join(part for part in _NON_TOKEN.split(text) if part)


def tokens(norm: str) -> frozenset[str]:
    return frozenset(part for part in norm.split(" ") if len(part) >= 2)
```

- [ ] **Step 4: Implement `derive.py`**

```python
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256

from app.services.statements.merchant import normalise

QUANTUM = Decimal("0.0001")
# Lines whose printed amount is positive on the issuer side (charges / debits) carry the ledger sign −.
CARD_DEBIT = {"purchase", "fee", "interest", "installment", "balance_adjustment"}
CARD_CREDIT = {"payment", "refund", "reward"}
BANK_CREDIT = {"deposit", "interest", "transfer_in", "refund", "reward"}
BANK_DEBIT = {"withdrawal", "fee", "transfer_out", "purchase", "payment"}
WINDOW = timedelta(days=5)
MAX_PERIOD = timedelta(days=62)


class SignError(ValueError):
    pass


@dataclass(frozen=True)
class LineIn:
    seq: int
    txn_date: date | None
    posted_date: date
    merchant_raw: str
    printed_amount: Decimal
    foreign_amount: Decimal | None
    foreign_currency: str | None
    line_kind: str
    installment_seq: int | None
    installment_total: int | None
    is_subtotal: bool = False


@dataclass(frozen=True)
class DerivedLine:
    line: LineIn
    merchant_norm: str
    flow_amount: Decimal
    canonical_key: str
    logical_key: str

    @property
    def seq(self) -> int:
        return self.line.seq


@dataclass(frozen=True)
class Header:
    period_start: date
    period_end: date
    closing_date: date | None
    due_date: date | None
    opening_balance: Decimal | None
    statement_total: Decimal
    minimum_payment: Decimal | None
    currency: str


@dataclass
class GuardrailResult:
    ok: bool
    checks: dict[str, bool] = field(default_factory=dict)
    detail: dict = field(default_factory=dict)


def _q(value: Decimal) -> Decimal:
    return Decimal(value).quantize(QUANTUM)


def flow_amount(kind: str, line_kind: str, printed: Decimal) -> Decimal:
    printed = _q(printed)
    if printed == 0 and line_kind != "unknown":
        raise SignError(f"{line_kind}: zero amount")
    if kind == "card":
        if line_kind in CARD_DEBIT and printed < 0 or line_kind in CARD_CREDIT and printed > 0:
            raise SignError(f"card {line_kind}: sign {printed}")
        return _q(-printed)
    if line_kind in BANK_CREDIT and printed < 0 or line_kind in BANK_DEBIT and printed > 0:
        raise SignError(f"bank {line_kind}: sign {printed}")
    return printed


def _canonical(line: LineIn, norm: str, flow: Decimal) -> str:
    parts = (line.posted_date.isoformat(), line.txn_date.isoformat() if line.txn_date else "", str(flow),
             str(_q(line.foreign_amount)) if line.foreign_amount is not None else "", line.foreign_currency or "",
             line.line_kind, str(line.installment_seq or ""), str(line.installment_total or ""), norm)
    return sha256("|".join(parts).encode()).hexdigest()


def derive_lines(kind: str, lines: list[LineIn]) -> list[DerivedLine]:
    out: list[DerivedLine] = []
    occurrences: dict[tuple[date, Decimal], int] = {}
    for line in sorted(lines, key=lambda l: l.seq):
        if line.is_subtotal:
            continue
        flow = flow_amount(kind, line.line_kind, line.printed_amount)
        norm = normalise(line.merchant_raw)
        index = occurrences.get((line.posted_date, flow), 0)
        occurrences[(line.posted_date, flow)] = index + 1
        out.append(DerivedLine(line=line, merchant_norm=norm, flow_amount=flow,
                               canonical_key=_canonical(line, norm, flow),
                               logical_key=f"{line.posted_date.isoformat()}|{flow}|{index}"))
    return out


def guardrails(kind: str, header: Header, account_currency: str, lines: list[DerivedLine]) -> GuardrailResult:
    result = GuardrailResult(ok=True)
    total = _q(header.statement_total)
    sum_flow = _q(sum((l.flow_amount for l in lines), Decimal(0)))
    opening = header.opening_balance
    if kind == "card":
        if opening is None:
            result.detail["opening_assumed_zero"] = True
            opening = Decimal(0)
        expected = _q(opening - sum_flow)
    else:
        if opening is None:
            result.checks["opening_present"] = False
            result.ok = False
            expected = None
        else:
            expected = _q(opening + sum_flow)
    result.checks["equation"] = expected is not None and expected == total
    result.detail.update(sum_flow=str(sum_flow), expected_total=str(expected) if expected is not None else None, statement_total=str(total))
    result.checks["currency"] = header.currency == account_currency
    result.checks["period_length"] = timedelta(0) <= header.period_end - header.period_start <= MAX_PERIOD
    lo, hi = header.period_start - WINDOW, header.period_end + WINDOW
    result.checks["dates_in_window"] = all(lo <= l.line.posted_date <= hi for l in lines)
    result.checks["installments"] = all(
        l.line.installment_seq is None or (l.line.installment_total is not None and 1 <= l.line.installment_seq <= l.line.installment_total)
        for l in lines)
    result.checks["line_count"] = len(lines) <= 2000
    result.ok = result.ok and all(result.checks.values())
    return result
```

- [ ] **Step 5: Run the unit tests**

Run: `.venv/bin/pytest -q tests/unit/test_statement_derive.py tests/unit/test_statement_merchant.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/accounting-service/app/services/statements services/accounting-service/tests/unit/test_statement_derive.py services/accounting-service/tests/unit/test_statement_merchant.py
git commit -m "feat(accounting): pure statement derivation — flow signs, keys, guardrails, merchant normaliser"
```

---

### Task 6: Request/response schemas

**Files:**
- Create: `services/accounting-service/app/schemas/statements.py`
- Test: `services/accounting-service/tests/unit/test_statement_schemas.py`

**Interfaces:**
- Produces (all `ConfigDict(extra="forbid")`): `RunEnqueueOut`, `RunClaimOut(run_id, lease_token, lease_expires_at, attempt)`, `RunFinishIn(status: Literal["done","failed"], summary: dict)`, `RunLeaseIn(lease_token: str)`, `FileRegisterIn(run_id, lease_token, sha256, size, kind, object_key)`, `FileUpdateIn(run_id, lease_token, status, failure?, has_text_layer?, text_chars?, pages?, credential_version?, mapping_version?, parser_version?, next_retry_at?)`, `SourceRegisterIn(run_id, lease_token, file_id, root, drive_file_id, drive_path, drive_md5, drive_size)`, `LineIn` (pydantic mirror of `derive.LineIn` with `printed_amount: SignedMoney`, `line_kind: Literal[...LINE_KINDS]`, `seq: Int32 >= 1`, `merchant_raw: str max 256`), `RevisionIn(run_id, lease_token, file_id, account_id: Int32, kind, parser, parser_version, currency: Currency, period_start, period_end, closing_date?, due_date?, opening_balance?: SignedMoney, statement_total: SignedMoney, minimum_payment?, lines: list[LineIn] (max 2000), raw: dict)`, `RevisionOut(statement_id, revision_id, revision, guardrail_ok, mode, conflict, lineage: dict[str,int], case_ids: list[int])`, `StatementOut`, `StatementDetailOut` (header + `lines: list[LineOut]` + `cases: list[CaseOut]` + `stale_events_pending: bool`), `LineOut` (derived fields incl. `event_id`, `flow_amount`, `merchant_norm`), `CaseOut`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_statement_schemas.py
import pytest
from pydantic import ValidationError

from app.schemas.statements import LineIn, RevisionIn


def _line(**kw):
    base = dict(seq=1, posted_date="2026-09-03", merchant_raw="全聯", printed_amount="580", line_kind="purchase")
    base.update(kw)
    return base


def _rev(**kw):
    base = dict(run_id=1, lease_token="t", file_id=1, account_id=1, kind="card", parser="claude-cli", parser_version="2.1.295",
                currency="TWD", period_start="2026-09-01", period_end="2026-09-30", statement_total="580", lines=[_line()], raw={})
    base.update(kw)
    return base


def test_revision_in_forbids_extra_and_caps_lines():
    with pytest.raises(ValidationError):
        RevisionIn(**_rev(extra=1))
    with pytest.raises(ValidationError):
        RevisionIn(**_rev(lines=[_line(seq=i) for i in range(1, 2002)]))


def test_line_in_validates_kind_and_amount_scale():
    with pytest.raises(ValidationError):
        LineIn(**_line(line_kind="snack"))
    with pytest.raises(ValidationError):
        LineIn(**_line(printed_amount="1.00001"))
    assert LineIn(**_line()).printed_amount == 580
```

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the module** (shape; fill every field listed in Interfaces)

```python
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.statements import INGEST_STATUSES, LINE_KINDS, STATEMENT_FILE_FAILURES, STATEMENT_FILE_STATUSES, STATEMENT_KINDS, STATEMENT_SOURCE_ROOTS
from app.schemas.writes import Currency, Int32, SignedMoney

Strict = ConfigDict(extra="forbid")
LineKind = Literal[LINE_KINDS]  # type: ignore[valid-type]
StatementKind = Literal[STATEMENT_KINDS]  # type: ignore[valid-type]


class Lease(BaseModel):
    model_config = Strict
    run_id: Int32
    lease_token: str = Field(min_length=16, max_length=64)


class LineIn(BaseModel):
    model_config = Strict
    seq: int = Field(ge=1, le=100000)
    txn_date: date | None = None
    posted_date: date
    merchant_raw: str = Field(max_length=256)
    printed_amount: SignedMoney
    foreign_amount: SignedMoney | None = None
    foreign_currency: Currency | None = None
    line_kind: LineKind
    installment_seq: int | None = Field(default=None, ge=1, le=120)
    installment_total: int | None = Field(default=None, ge=1, le=120)
    is_subtotal: bool = False


class RevisionIn(Lease):
    file_id: Int32 | None = None
    account_id: Int32
    kind: StatementKind
    parser: str = Field(max_length=64)
    parser_version: str = Field(max_length=64)
    currency: Currency
    period_start: date
    period_end: date
    closing_date: date | None = None
    due_date: date | None = None
    opening_balance: SignedMoney | None = None
    statement_total: SignedMoney
    minimum_payment: SignedMoney | None = None
    lines: list[LineIn] = Field(max_length=2000)
    raw: dict
# ... remaining models per the Interfaces list
```

- [ ] **Step 4: Run the tests** — PASS.
- [ ] **Step 5: Commit** `feat(accounting): statement ingest schemas`.

---

### Task 7: Lineage pairing (pure)

**Files:**
- Create: `services/accounting-service/app/services/statements/lineage.py`
- Test: `services/accounting-service/tests/unit/test_statement_lineage.py`

**Interfaces:**
- Produces: `lineage.Old(line_id, event_id, logical_key, canonical_key, merchant_norm, kind_fields: tuple)`, `lineage.New(index, logical_key, canonical_key, merchant_norm, kind_fields)`, `lineage.Pairing(old: Old | None, new: New | None, equivalence: str)`, `lineage.pair(old: list[Old], new: list[New]) -> list[Pairing]`, `lineage.needs_review(pairings) -> bool` (twin count changed or any `changed`/`unpaired` whose old event carries effects is decided by the caller; this function only reports twin-count changes).
- `kind_fields` = `(posted_date, txn_date, flow_amount, foreign_amount, foreign_currency, line_kind, installment_seq, installment_total)` — everything in the canonical key except the merchant; `normalised` means `kind_fields` equal and `merchant tokens` equal.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_statement_lineage.py
from app.services.statements import lineage
from app.services.statements.lineage import New, Old
from app.services.statements.merchant import tokens


def O(i, lk, ck, m, kf=("k",)):
    return Old(line_id=i, event_id=100 + i, logical_key=lk, canonical_key=ck, merchant_norm=m, kind_fields=kf)


def N(i, lk, ck, m, kf=("k",)):
    return New(index=i, logical_key=lk, canonical_key=ck, merchant_norm=m, kind_fields=kf)


def test_identical_pairs_by_logical_then_canonical():
    out = lineage.pair([O(1, "d|-580|0", "c1", "全聯")], [N(0, "d|-580|0", "c1", "全聯")])
    assert [(p.old.line_id, p.new.index, p.equivalence) for p in out] == [(1, 0, "identical")]


def test_whitespace_change_is_normalised_not_changed():
    out = lineage.pair([O(1, "d|-580|0", "c1", "PAYPAL SPOTIFY")], [N(0, "d|-580|0", "c2", "PAYPAL  SPOTIFY".replace("  ", " "))])
    assert out[0].equivalence == "identical"  # same tokens -> same canonical in practice; explicit normalised case below


def test_token_equal_but_canonical_differs_is_normalised():
    out = lineage.pair([O(1, "d|-580|0", "c1", "PAYPAL SPOTIFY")], [N(0, "d|-580|0", "c2", "SPOTIFY PAYPAL")])
    assert out[0].equivalence == "normalised"


def test_merchant_change_is_changed():
    out = lineage.pair([O(1, "d|-580|0", "c1", "PAYPAL SPOTIFY")], [N(0, "d|-580|0", "c2", "PAYPAL NETFLIX")])
    assert out[0].equivalence == "changed" and out[0].old.line_id == 1 and out[0].new.index == 0


def test_identical_twins_reordered_pair_in_print_order():
    old = [O(1, "d|-580|0", "c", "A"), O(2, "d|-580|1", "c", "A")]
    new = [N(0, "d|-580|0", "c", "A"), N(1, "d|-580|1", "c", "A")]
    out = lineage.pair(old, new)
    assert [(p.old.line_id, p.new.index, p.equivalence) for p in out] == [(1, 0, "identical"), (2, 1, "identical")]
    assert lineage.twin_count_changed(old, new) is False


def test_twin_count_change_is_flagged():
    old = [O(1, "d|-580|0", "c", "A"), O(2, "d|-580|1", "c", "A")]
    new = [N(0, "d|-580|0", "c", "A")]
    out = lineage.pair(old, new)
    assert [p.equivalence for p in out] == ["identical", "unpaired"]
    assert lineage.twin_count_changed(old, new) is True


def test_new_line_without_old_is_unpaired_new():
    out = lineage.pair([], [N(0, "d|-1|0", "c", "A")])
    assert out[0].old is None and out[0].new.index == 0 and out[0].equivalence == "unpaired"
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement**

```python
from collections import Counter
from dataclasses import dataclass

from app.services.statements.merchant import tokens


@dataclass(frozen=True)
class Old:
    line_id: int
    event_id: int
    logical_key: str
    canonical_key: str
    merchant_norm: str
    kind_fields: tuple


@dataclass(frozen=True)
class New:
    index: int
    logical_key: str
    canonical_key: str
    merchant_norm: str
    kind_fields: tuple


@dataclass(frozen=True)
class Pairing:
    old: Old | None
    new: New | None
    equivalence: str


def _equivalence(old: Old, new: New) -> str:
    if old.canonical_key == new.canonical_key:
        return "identical"
    if old.kind_fields == new.kind_fields and tokens(old.merchant_norm) == tokens(new.merchant_norm):
        return "normalised"
    return "changed"


def pair(old: list[Old], new: list[New]) -> list[Pairing]:
    """Pair by logical key in print order; classify by canonical key / token equality (design §5.7)."""
    by_key: dict[str, list[Old]] = {}
    for item in sorted(old, key=lambda o: o.line_id):
        by_key.setdefault(item.logical_key, []).append(item)
    out: list[Pairing] = []
    for item in sorted(new, key=lambda n: n.index):
        bucket = by_key.get(item.logical_key)
        if bucket:
            out.append(Pairing(bucket.pop(0), item, _equivalence(bucket_item := out[-1].old if False else None, item)) if False else None)  # placeholder removed below
    return out
```

Write the real loop (no placeholder):

```python
def pair(old: list[Old], new: list[New]) -> list[Pairing]:
    by_key: dict[str, list[Old]] = {}
    for item in sorted(old, key=lambda o: o.line_id):
        by_key.setdefault(item.logical_key, []).append(item)
    out: list[Pairing] = []
    for item in sorted(new, key=lambda n: n.index):
        bucket = by_key.get(item.logical_key)
        if bucket:
            matched = bucket.pop(0)
            out.append(Pairing(matched, item, _equivalence(matched, item)))
        else:
            out.append(Pairing(None, item, "unpaired"))
    for bucket in by_key.values():
        for leftover in bucket:
            out.append(Pairing(leftover, None, "unpaired"))
    return out


def twin_count_changed(old: list[Old], new: list[New]) -> bool:
    def twins(items, key):
        return Counter(k for k, c in Counter(key(i) for i in items).items() if c > 1)
    strip = lambda lk: lk.rsplit("|", 1)[0]  # (posted_date, flow) without the occurrence index
    return twins(old, lambda o: strip(o.logical_key)) != twins(new, lambda n: strip(n.logical_key))
```

- [ ] **Step 4: Run the tests** — PASS (fix `test_whitespace_change_is_normalised_not_changed` expectation if the canonical keys differ: with equal tokens and equal kind fields it must be `normalised`; the test as written feeds canonical `c2`, so expect `"normalised"`. Correct the test to `assert out[0].equivalence == "normalised"`).

- [ ] **Step 5: Commit** `feat(accounting): statement line lineage pairing`.

---

### Task 8: Ingest service — runs, files, sources, revisions, events, lineage, parse_review

**Files:**
- Create: `services/accounting-service/app/services/statement_ingest_service.py`
- Test: `services/accounting-service/tests/integration/test_statement_ingest_service.py`

**Interfaces:**
- Produces:
  - `enqueue_run(db, *, principal: str) -> IngestRun` (coalesce on any non-terminal run; append to `summary["coalesced"]`)
  - `create_worker_run(db, *, trigger: str, initiator_hint: str | None, mode: str) -> IngestRun`
  - `list_runs(db, *, status: str | None) -> list[IngestRun]`
  - `claim_run(db, run_id, *, label: str) -> tuple[IngestRun, str]` — atomic `UPDATE ... WHERE status='queued'` (or `expired`), new `lease_token = secrets.token_hex(24)`, `lease_expires_at = now + 30 min`, `attempt += 1`, `claimed_by = label`; `ConflictError("run not claimable")` otherwise
  - `renew_lease(db, run_id, token) -> IngestRun`; `finish_run(db, run_id, token, *, status, summary)`
  - `require_lease(db, run_id, token, *, label) -> IngestRun` — `ConflictError("lease")` unless `status in ('claimed','running')`, token matches, lease live, `claimed_by == label`; sets `status='running'` on first use
  - `register_file(db, run, payload) -> StatementFile` (upsert by sha256; `run_id` set on first insert)
  - `register_source(db, run, payload) -> StatementSource` (upsert by `(drive_file_id, drive_md5)`; path change appends to `path_history`, `last_seen_at` refreshed)
  - `update_file(db, run, file_id, payload) -> StatementFile`
  - `mark_removed_sources(db, run, seen_ids: set[str]) -> int`
  - `submit_revision(db, run, payload: RevisionIn, *, account_map: dict[str, int]) -> SubmitResult(statement, revision, lineage_counts, case_ids)`:
    1. `account = db.get(Account, payload.account_id)` → `NotFoundError`; `kind` must match `account.is_credit` (card ⇔ credit) → `ValidationError("kind")`; when `file_id` is given, the file's newest source folder (`root/<first two path parts>`) must map to `payload.account_id` in `account_map` → `ValidationError("account_id", "folder maps elsewhere")`.
    2. `derive_lines` (a `SignError` → `ValidationError("lines", str(exc))`), `guardrails`.
    3. `mode = "live" if account.statement_live_from and payload.period_end >= account.statement_live_from else "historical"`.
    4. Statement identity `(account_id, currency, period_end)`: fetch `FOR UPDATE`; create when absent (`origin='import'` when `file_id` else `'manual'`, `mode`, header from payload); else keep its `mode`.
    5. Insert `StatementRevision(revision = max+1, guardrail=result.checks|detail, guardrail_ok, conflict = statement.status=='reconciled')`.
    6. Insert lines (`event_id` filled after pairing): build `Old` from the current revision's lines (if any) and `New` from derived lines; `pair()`; for each pairing: `identical`/`normalised`/`changed` with an old → reuse `old.event_id`; `unpaired` new → new `StatementEvent(first_revision_id=rev, status='live')`; `unpaired` old → event `status='retired'` (R1b upgrades to `quarantined` when coverage/effects exist). Write `LineLineage` rows (`transferred` stays false in R1a; R1b sets it when coverage moves). Update `event.current_line_id`.
    7. Current revision: when `guardrail_ok` and not `conflict` and not `twin_count_changed` and no header change vs current → `statement.current_revision_id = rev.id`, header columns refreshed. When `conflict` → `statement.conflict_open = True` (current unchanged). When guardrail fails or twins changed or header changed on a non-first revision → current unchanged (first revision with a failed guardrail becomes current anyway so the statement has lines to show, with `guardrail_ok=false` visible).
    8. Cases: in `live` mode open one `parse_review` case (`kind='parse_review'`, `revision_id=rev.id`, `context={"checks":..., "detail":...}`) when `not guardrail_ok` or twins changed or header changed; one `statement_conflict` case when `conflict`. In `historical` mode no cases; the revision's `guardrail` JSON carries the diagnostics.
    9. Return counts `{"identical": n, "normalised": n, "changed": n, "unpaired_old": n, "new": n}`.
  - `list_statements(db, account_id) -> list[AccountStatement]`, `get_statement(db, statement_id) -> dict` (header, current lines with event ids, cases, `stale_events_pending` computed as `exists(coverage_dirty.id > statement.swept_through_event_id and (old_account_id = account or new_account_id = account))`).

- [ ] **Step 1: Write the failing integration tests** (abridged list; each is a separate test function)

```python
# tests/integration/test_statement_ingest_service.py
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models import AccountStatement, IngestRun, ReconciliationCase, StatementEvent, StatementLine
from app.schemas.statements import LineIn, RevisionIn
from app.services import statement_ingest_service as svc
from app.services.errors import ConflictError, ValidationError


def _line(seq, posted, printed, kind="purchase", merchant="全聯", **kw):
    return LineIn(seq=seq, posted_date=posted, merchant_raw=merchant, printed_amount=Decimal(printed), line_kind=kind, **kw)


def _rev(run, token, account, lines, total, opening=None, **kw):
    base = dict(run_id=run.id, lease_token=token, file_id=None, account_id=account.id, kind="card", parser="test",
                parser_version="1", currency="TWD", period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
                opening_balance=Decimal(opening) if opening is not None else None, statement_total=Decimal(total), lines=lines, raw={})
    base.update(kw)
    return RevisionIn(**base)


@pytest.fixture
def card(seed, db_session):
    account = seed.account("卡", is_credit=True, statement_live_from=date(2026, 9, 1))
    db_session.commit()
    return account


@pytest.fixture
def run(db_session):
    r = svc.create_worker_run(db_session, trigger="timer", initiator_hint=None, mode="live")
    r, token = svc.claim_run(db_session, r.id, label="worker")
    db_session.commit()
    return r, token


def test_enqueue_coalesces_non_terminal_runs(db_session):
    a = svc.enqueue_run(db_session, principal="hermes")
    b = svc.enqueue_run(db_session, principal="hermes")
    assert a.id == b.id and b.summary["coalesced"][0]["principal"] == "hermes"


def test_claim_is_atomic_and_fences_submissions(db_session, run):
    r, token = run
    with pytest.raises(ConflictError):
        svc.claim_run(db_session, r.id, label="worker")       # already claimed
    with pytest.raises(ConflictError):
        svc.require_lease(db_session, r.id, "wrong-token", label="worker")
    r.lease_expires_at = r.lease_expires_at - timedelta(hours=1)
    db_session.flush()
    with pytest.raises(ConflictError):
        svc.require_lease(db_session, r.id, token, label="worker")  # expired
    r2, token2 = svc.claim_run(db_session, r.id, label="worker")    # reclaim after expiry
    assert r2.attempt == 2 and token2 != token


def test_first_revision_creates_statement_events_and_lines(db_session, card, run):
    r, token = run
    result = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580"), _line(2, date(2026, 9, 10), "-500", kind="payment")], total="80"), account_map={})
    stmt = result.statement
    assert (stmt.mode, stmt.current_revision_id, result.revision.guardrail_ok) == ("live", result.revision.id, True)
    lines = db_session.query(StatementLine).filter_by(revision_id=result.revision.id).order_by(StatementLine.seq).all()
    assert [str(l.flow_amount) for l in lines] == ["-580.0000", "500.0000"]
    assert db_session.query(StatementEvent).filter_by(statement_id=stmt.id).count() == 2
    assert result.lineage_counts == {"identical": 0, "normalised": 0, "changed": 0, "unpaired_old": 0, "new": 2}


def test_reparse_identical_keeps_events_and_current_moves(db_session, card, run):
    r, token = run
    first = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580")], total="580"), account_map={})
    second = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580", merchant="全聯 ")], total="580"), account_map={})
    assert second.lineage_counts["identical"] == 1
    events = db_session.query(StatementEvent).filter_by(statement_id=first.statement.id).all()
    assert len(events) == 1 and events[0].current_line_id != events[0].first_line_id
    assert first.statement.current_revision_id == second.revision.id


def test_merchant_change_pairs_changed_and_keeps_event(db_session, card, run):
    r, token = run
    svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580", merchant="PAYPAL *Spotify")], total="580"), account_map={})
    second = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580", merchant="PAYPAL *Netflix")], total="580"), account_map={})
    assert second.lineage_counts["changed"] == 1 and db_session.query(StatementEvent).count() == 1


def test_guardrail_failure_stores_revision_opens_parse_review_and_keeps_current(db_session, card, run):
    r, token = run
    first = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580")], total="580"), account_map={})
    bad = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580")], total="581"), account_map={})
    assert bad.revision.guardrail_ok is False and first.statement.current_revision_id == first.revision.id
    cases = db_session.query(ReconciliationCase).filter_by(statement_id=first.statement.id).all()
    assert [c.kind for c in cases] == ["parse_review"] and bad.case_ids == [cases[0].id]


def test_historical_mode_never_opens_cases(db_session, seed, run):
    r, token = run
    old_card = seed.account("舊卡", is_credit=True)  # statement_live_from = None -> historical
    db_session.commit()
    res = svc.submit_revision(db_session, r, _rev(r, token, old_card, [_line(1, date(2026, 9, 3), "580")], total="999"), account_map={})
    assert res.statement.mode == "historical" and res.revision.guardrail_ok is False and res.case_ids == []


def test_reconciled_statement_gets_conflict_not_new_current(db_session, card, run):
    r, token = run
    first = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580")], total="580"), account_map={})
    first.statement.status = "reconciled"
    db_session.flush()
    second = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "581")], total="581"), account_map={})
    assert second.revision.conflict and first.statement.conflict_open and first.statement.current_revision_id == first.revision.id
    assert [c.kind for c in db_session.query(ReconciliationCase).all()] == ["statement_conflict"]


def test_kind_must_match_account_and_folder_map(db_session, seed, run, card):
    r, token = run
    bank = seed.account("活存")
    db_session.commit()
    with pytest.raises(ValidationError) as exc:
        svc.submit_revision(db_session, r, _rev(r, token, bank, [_line(1, date(2026, 9, 3), "580")], total="580"), account_map={})
    assert exc.value.field == "kind"


def test_sign_inconsistent_line_is_422(db_session, card, run):
    r, token = run
    with pytest.raises(ValidationError) as exc:
        svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "-580")], total="580"), account_map={})
    assert exc.value.field == "lines"
```

- [ ] **Step 2: Run to verify failure** (`ModuleNotFoundError`).

- [ ] **Step 3: Implement the service** — write it in full following the Interfaces; key fragments:

```python
LEASE = timedelta(minutes=30)
TERMINAL = ("done", "failed")


def claim_run(db: Session, run_id: int, *, label: str) -> tuple[IngestRun, str]:
    token = secrets.token_hex(24)
    now = _now()
    updated = db.execute(
        update(IngestRun)
        .where(IngestRun.id == run_id, IngestRun.status.in_(("queued", "expired")))
        .values(status="claimed", claimed_by=label, lease_token=token, lease_expires_at=now + LEASE,
                attempt=IngestRun.attempt + 1, started_at=func.coalesce(IngestRun.started_at, now))
        .returning(IngestRun.id)
    ).scalar_one_or_none()
    if updated is None:
        run = db.get(IngestRun, run_id)
        if run is None:
            raise NotFoundError(f"run {run_id} not found")
        if run.status in ("claimed", "running") and run.lease_expires_at < now:
            run.status = "expired"  # lazily expire, then retry once
            db.flush()
            return claim_run(db, run_id, label=label)
        raise ConflictError("run not claimable")
    return db.get(IngestRun, run_id, populate_existing=True), token


def require_lease(db, run_id, token, *, label):
    run = db.get(IngestRun, run_id)
    if run is None:
        raise NotFoundError(f"run {run_id} not found")
    if run.status not in ("claimed", "running") or run.claimed_by != label or not hmac.compare_digest(run.lease_token or "", token) \
            or run.lease_expires_at is None or run.lease_expires_at < _now():
        raise ConflictError("lease")
    if run.status == "claimed":
        run.status = "running"
    return run
```

```python
@dataclass
class SubmitResult:
    statement: AccountStatement
    revision: StatementRevision
    lineage_counts: dict[str, int]
    case_ids: list[int]


def submit_revision(db, run, payload, *, account_map):
    account = db.get(Account, payload.account_id)
    if account is None:
        raise NotFoundError("account not found")
    if (payload.kind == "card") != bool(account.is_credit):
        raise ValidationError("kind", "statement kind does not match the account")
    if payload.file_id is not None:
        _assert_folder_maps(db, payload.file_id, payload.account_id, account_map)
    try:
        derived = derive.derive_lines(payload.kind, [derive.LineIn(**line.model_dump()) for line in payload.lines])
    except derive.SignError as exc:
        raise ValidationError("lines", str(exc)) from exc
    header = derive.Header(**{k: getattr(payload, k) for k in ("period_start","period_end","closing_date","due_date","opening_balance","statement_total","minimum_payment","currency")})
    check = derive.guardrails(payload.kind, header, account.currency, derived)
    mode = "live" if account.statement_live_from and payload.period_end >= account.statement_live_from else "historical"
    statement = db.execute(select(AccountStatement).where(AccountStatement.account_id == account.id, AccountStatement.currency == payload.currency,
                                                          AccountStatement.period_end == payload.period_end).with_for_update()).scalar_one_or_none()
    created = statement is None
    if created:
        statement = AccountStatement(account_id=account.id, kind=payload.kind, currency=payload.currency, mode=mode,
                                     origin="import" if payload.file_id else "manual", created_run_id=run.id, **_header_columns(payload))
        db.add(statement); db.flush()
    number = (db.execute(select(func.max(StatementRevision.revision)).where(StatementRevision.statement_id == statement.id)).scalar() or 0) + 1
    conflict = statement.status == "reconciled"
    revision = StatementRevision(statement_id=statement.id, revision=number, file_id=payload.file_id, parser=payload.parser,
                                 parser_version=payload.parser_version, raw=payload.raw, guardrail={"checks": check.checks, "detail": check.detail},
                                 guardrail_ok=check.ok, conflict=conflict, run_id=run.id, **_header_columns(payload))
    db.add(revision); db.flush()
    counts, twins_changed = _write_lines_and_lineage(db, statement, revision, derived)
    header_changed = (not created) and _header_differs(statement, payload)
    becomes_current = created or (check.ok and not conflict and not twins_changed and not header_changed)
    case_ids: list[int] = []
    if becomes_current:
        statement.current_revision_id = revision.id
        for column, value in _header_columns(payload).items():
            setattr(statement, column, value)
    if conflict:
        statement.conflict_open = True
    if statement.mode == "live":
        if not check.ok or twins_changed or header_changed:
            case_ids.append(_open_case(db, statement, revision, "parse_review", context={"guardrail": revision.guardrail, "twins_changed": twins_changed, "header_changed": header_changed}))
        if conflict:
            case_ids.append(_open_case(db, statement, revision, "statement_conflict", context={"revision": number}))
    _recount(db, statement)
    db.flush()
    return SubmitResult(statement, revision, counts, case_ids)
```

`_write_lines_and_lineage` inserts `StatementLine` rows with a temporary `event_id` — because `event_id` is NOT NULL, insert events first: build `Old` from `statement.current_revision_id`'s lines, build `New` from `derived`, call `lineage.pair`, create a `StatementEvent` for every `unpaired` new pairing (`first_revision_id=revision.id`), then insert all lines with their event ids, then set `first_line_id` on the new events and `current_line_id` on every paired event, mark `unpaired` old events `retired`, and insert `LineLineage` rows. Return `(Counter(...), lineage.twin_count_changed(old, new))`.

`_recount(db, statement)` sets `open_case_count` from `reconciliation_case` (`status in ('open','proposed')`), leaves `matched_count`/`explained_count` at 0 until R1b.

- [ ] **Step 4: Run the tests** — PASS.
- [ ] **Step 5: Commit** `feat(accounting): statement ingest service — runs with leases, files, sources, revisions, events, lineage`.

---

### Task 9: Router `/statements` + `/accounts/{id}/statements`, settings row, main wiring

**Files:**
- Create: `services/accounting-service/app/routers/statements.py`
- Modify: `services/accounting-service/app/main.py` (add router), `services/accounting-service/app/services/settings_service.py` (`get_reconciliation_settings(db) -> dict`, `update_reconciliation_settings(db, data) -> dict`, single-row upsert like `_preference_row`)
- Test: `services/accounting-service/tests/integration/test_statement_ingest_api.py`

**Interfaces:**
- Routes (all under `Depends(auth.require_feature())`):

| method | path | scope | handler |
|---|---|---|---|
| POST | `/statements/ingest/run` | `enqueue` | `enqueue_run(principal=request.state.client_label)` → 202 `RunEnqueueOut` |
| GET | `/statements/ingest-runs?status=` | `ingest` | list |
| POST | `/statements/ingest-runs/{run_id}/claim` | `ingest` | 200 `RunClaimOut` |
| POST | `/statements/ingest-runs/{run_id}/renew` | `ingest` | body `RunLeaseIn` |
| POST | `/statements/ingest-runs/{run_id}/finish` | `ingest` | body `RunFinishIn` + lease |
| POST | `/statements/files` | `ingest` | `FileRegisterIn` → 201 |
| PATCH | `/statements/files/{file_id}` | `ingest` | `FileUpdateIn` |
| GET | `/statements/files?status=` | `ingest` | list |
| POST | `/statements/sources` | `ingest` | `SourceRegisterIn` → 201 |
| POST | `/statements/revisions` | `ingest` | `RevisionIn` → 201 `RevisionOut` (account map read from `reconciliation_settings.data["account_map"]`) |
| GET | `/accounts/{account_id}/statements` | `read` | `list[StatementOut]` |
| GET | `/accounts/{account_id}/statements/{statement_id}` | `read` | `StatementDetailOut` |
| GET/PUT | `/settings/reconciliation` | `read` / `admin` | settings JSON (`account_map`, versions) |

Every `ingest` handler first calls `require_lease(db, body.run_id, body.lease_token, label=request.state.client_label)` (except `claim` and the run listing). Router commits after each service call (`with service_errors(): ...; db.commit()`).

- [ ] **Step 1: Write the failing API tests**

```python
# tests/integration/test_statement_ingest_api.py
from datetime import date

import pytest

from app import auth

TOKENS = "spa:spa-token,hermes:hermes-token,worker:worker-token"
SCOPES = "spa=legacy; hermes=read,propose,enqueue; worker=ingest"
W = {"Authorization": "Bearer worker-token"}
H = {"Authorization": "Bearer hermes-token"}
S = {"Authorization": "Bearer spa-token"}


@pytest.fixture(autouse=True)
def recon_env(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, TOKENS)
    monkeypatch.setenv(auth.SCOPES_ENV, SCOPES)
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


@pytest.fixture
def card(seed, db_session):
    account = seed.account("卡", is_credit=True, statement_live_from=date(2026, 9, 1))
    db_session.commit()
    return account


def _claim(client):
    run = client.post("/statements/ingest/run", headers=H).json()
    claim = client.post(f"/statements/ingest-runs/{run['run_id']}/claim", headers=W)
    assert claim.status_code == 200
    return claim.json()


def _revision(lease, account, lines, total):
    return {"run_id": lease["run_id"], "lease_token": lease["lease_token"], "account_id": account.id, "kind": "card",
            "parser": "test", "parser_version": "1", "currency": "TWD", "period_start": "2026-09-01", "period_end": "2026-09-30",
            "statement_total": total, "lines": lines, "raw": {}}


def test_enqueue_needs_enqueue_scope_and_returns_202(client):
    assert client.post("/statements/ingest/run", headers=W).status_code == 403
    assert client.post("/statements/ingest/run", headers=S).status_code == 202
    assert client.post("/statements/ingest/run", headers=H).status_code == 202


def test_feature_off_hides_routes(client, monkeypatch):
    monkeypatch.setenv(auth.FEATURE_ENV, "false")
    assert client.post("/statements/ingest/run", headers=H).status_code == 404


def test_worker_submits_revision_and_owner_reads_it(client, card):
    lease = _claim(client)
    body = _revision(lease, card, [{"seq": 1, "posted_date": "2026-09-03", "merchant_raw": "全聯", "printed_amount": "580", "line_kind": "purchase"}], "580")
    created = client.post("/statements/revisions", headers=W, json=body)
    assert created.status_code == 201, created.text
    out = created.json()
    assert out["guardrail_ok"] is True and out["mode"] == "live" and out["lineage"]["new"] == 1
    listing = client.get(f"/accounts/{card.id}/statements", headers=S)
    assert listing.status_code == 200 and listing.json()[0]["statement_total"] == "580.0000"
    detail = client.get(f"/accounts/{card.id}/statements/{out['statement_id']}", headers=S)
    assert detail.status_code == 200
    assert [l["flow_amount"] for l in detail.json()["lines"]] == ["-580.0000"]
    assert detail.json()["stale_events_pending"] is False


def test_hermes_cannot_submit_revisions(client, card):
    lease = _claim(client)
    assert client.post("/statements/revisions", headers=H, json=_revision(lease, card, [], "0")).status_code == 403


def test_wrong_lease_is_409(client, card):
    lease = _claim(client)
    body = _revision(lease, card, [], "0") | {"lease_token": "x" * 48}
    response = client.post("/statements/revisions", headers=W, json=body)
    assert (response.status_code, response.json()["message"]) == (409, "lease")


def test_folder_map_is_enforced_for_file_backed_revisions(client, card, seed, db_session):
    other = seed.account("另一張卡", is_credit=True)
    db_session.commit()
    assert client.put("/settings/reconciliation", headers=S, json={"account_map": {"mail/信用卡/國泰世華": other.id}}).status_code == 200
    lease = _claim(client)
    file = client.post("/statements/files", headers=W, json={"run_id": lease["run_id"], "lease_token": lease["lease_token"],
                                                                "sha256": "a" * 64, "size": 10, "kind": "card", "object_key": "a" * 64}).json()
    client.post("/statements/sources", headers=W, json={"run_id": lease["run_id"], "lease_token": lease["lease_token"], "file_id": file["id"],
                                                        "root": "mail", "drive_file_id": "d1", "drive_path": "信用卡/國泰世華/2026-09.pdf", "drive_md5": "m" * 32, "drive_size": 10})
    body = _revision(lease, card, [], "0") | {"file_id": file["id"]}
    response = client.post("/statements/revisions", headers=W, json=body)
    assert response.status_code == 422 and response.json()["detail"][0]["loc"][-1] == "account_id"
```

- [ ] **Step 2: Run to verify failure** (404s: router missing).
- [ ] **Step 3: Implement the router** following the existing pattern (`service_errors`, `db.commit()`, dict responses), settings helpers, and add `statements.router` to `create_app`'s router list in `main.py`.
- [ ] **Step 4: Run** `.venv/bin/pytest -q tests/integration/test_statement_ingest_api.py tests/integration/test_scope_enforcement.py` — PASS (the scope-matrix test now also covers the new mutation routes: hermes gets 403 on every one except `/statements/ingest/run`; adjust `_fill`/skip list for that path, since `enqueue` is granted to hermes in that test's SCOPES only if you add it — keep that test's map without `enqueue`, so 403 is expected there too).
- [ ] **Step 5: Commit** `feat(accounting): statement ingest and read endpoints behind the reconciliation feature gate`.

---

### Task 10: openspec delta and docs

**Files:**
- Create: `openspec/changes/add-statement-reconciliation-r1a/{proposal.md,tasks.md,.openspec.yaml}`, `openspec/changes/add-statement-reconciliation-r1a/specs/accounting-reconciliation/spec.md` (ADDED requirements: Token scopes; Statement identity and revisions; Flow sign derivation; Guardrails; Line events and lineage; Dirty events; Ingest runs and leases), `…/specs/accounting-ledger/spec.md` (MODIFIED: entry sources include `statement`; account statement columns)
- Modify: `services/accounting-service/README.md` (env vars `ACCOUNTING_TOKEN_SCOPES`, `ACCOUNTING_RESTRICTED_LABELS`, `ACCOUNTING_RECONCILIATION_ENABLED`, the deploy note "SPA/ops labels → `legacy`")

- [ ] **Step 1: Write the requirement blocks** in the repo's SHALL/scenario style; at minimum one scenario per Review Focus item (1–5) above, worded as GIVEN/WHEN/THEN with the exact values from the tests.
- [ ] **Step 2: `openspec validate add-statement-reconciliation-r1a`** (if the CLI is installed; otherwise a structural self-check: every `### Requirement:` has ≥ 1 `#### Scenario:`).
- [ ] **Step 3: Commit** `docs(openspec): accounting-reconciliation R1a requirements and ledger delta`.

---

### Task 11: Whole-branch verification and PR

- [ ] **Step 1:** `.venv/bin/pytest -q` → all green; record the counts.
- [ ] **Step 2:** `cd services/accounting-service && .venv/bin/alembic -c alembic.ini heads` prints `d1f3a7c2e9b4 (head)`.
- [ ] **Step 3:** `git diff --stat main..HEAD` touches only `services/accounting-service/**`, `openspec/**`, `docs/**`.
- [ ] **Step 4:** Push `feat/statement-reconciliation` and open PR "feat(accounting): statement reconciliation R1a — scopes, schema, ingest API" with the description listing: the three rulings (settings mutations = `admin`, derived balance generation, `statement` kept in `entry_source` on downgrade), the env variables operators must add, and the §16 acceptance items this PR covers (S1 lease state machine, S3 null-safe triggers, C9 is **not** in this PR — worker). The owner merges.

---

## Self-review notes (done while writing)

- Spec coverage: §4 tables all present (plus `reconciliation_settings`, which the spec names under "settings" without a table); §4.1 lease protocol; §4.4/4.5 events + lineage; §4.6 triggers (sweep is R1b); §5.6 derivation + guardrails; §5.7 pairing rules incl. twins/header → `parse_review`; §8.1 scopes incl. compatibility-mode refusal; §8 ingest routes; §10/§9 nothing (frontend/agent later). Matching (§6), coverage writes, actions, policy, confirm, dismiss, proposals: R1b/R1c.
- Placeholder scan: the migration lists "one `op.create_table` per model … EXACTLY as in the model file" instead of repeating 17 tables; the implementer transcribes from Task 3's code, and `test_head_schema_matches_the_models` is the check. Task 9's router body is described by the route table + the existing pattern (Task 9 Step 3), which the fact sheet shows in full.
- Type consistency: `RevisionIn`/`LineIn` field names match `derive.LineIn`/`Header` (`model_dump()` feeds the dataclass directly); `SubmitResult` fields are used by the router's `RevisionOut`; `require_lease` signature matches Task 9's call.
- Review Focus → tests: 1 → Task 2 Step 7; 2 → Task 8 `test_guardrail_failure…`, `test_historical_mode…`; 3 → Task 7; 4 → Task 3 trigger tests; 5 → Task 2 unit tests.
