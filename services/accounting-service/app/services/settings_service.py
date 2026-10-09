"""Settings reads and writes. Task 5: preference; Task 16 adds accounts, groups, categories, projects, counterparties."""

from dataclasses import fields
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from ..models import (
    SYSTEM_KINDS,
    Account,
    AccountGroup,
    Category,
    Counterparty,
    LedgerEntry,
    Preference,
    STATEMENT_SOURCE_ROOTS,
    Project,
    RewardRule,
)
from ..models.statements import ReconciliationSettings
from .statements import matching
from ..schemas.writes import AccountGroupIn, AccountIn, CategoryIn, CounterpartyIn, PreferenceIn, ProjectIn
from . import schedule_entry_hooks
from .errors import ConflictError, NotFoundError, ValidationError  # noqa: F401  (re-exported)
from .ledger_service import open_debt_filter

PREFERENCE_FIELDS = (
    "expense_income_colors", "keypad_layout", "week_start", "main_currency", "hide_rewards_on_timeline",
    "abbreviate_totals",
)


def _preference_row(db: Session) -> Preference:
    """The single preference row; inserted with the column defaults (D27) when absent. Not committed."""
    db.execute(pg_insert(Preference).values(id=1).on_conflict_do_nothing(index_elements=["id"]))
    return db.get(Preference, 1, populate_existing=True)


def get_preference(db: Session) -> dict:
    preference = _preference_row(db)
    return {field: getattr(preference, field) for field in PREFERENCE_FIELDS}


def update_preference(db: Session, payload: PreferenceIn) -> dict:
    preference = _preference_row(db)
    for field in PREFERENCE_FIELDS:
        setattr(preference, field, getattr(payload, field))
    db.flush()
    return {field: getattr(preference, field) for field in PREFERENCE_FIELDS}


# --- reconciliation settings (single row, id = 1) -------------------------------


def _reconciliation_row(db: Session, *, lock: bool = False) -> ReconciliationSettings:
    """The single reconciliation settings row; inserted with the column defaults when absent. Not committed."""
    db.execute(pg_insert(ReconciliationSettings).values(id=1).on_conflict_do_nothing(index_elements=["id"]))
    return db.get(ReconciliationSettings, 1, populate_existing=True, with_for_update=lock)


RULES_VERSION = "r1b-1"


def _rule_default(value):
    if isinstance(value, tuple):
        return list(value)
    return str(value) if isinstance(value, Decimal) else value


# matching.Rules defaults, JSON-serialised (Decimal → str, tuple → list); period_end is per statement, not a setting.
RULE_DEFAULTS = {f.name: _rule_default(f.default) for f in fields(matching.Rules) if f.name != "period_end"}


def _reconciliation_dict(row: ReconciliationSettings) -> dict:
    """Stored data plus defaults: `rules` = the matching.Rules defaults overlaid with the stored keys, and
    `rules_version` (bumped whenever the defaults change meaning)."""
    data = dict(row.data or {})
    rules = {**RULE_DEFAULTS, **(data.get("rules") or {})}
    return {**data, "account_map": data.get("account_map") or {}, "dirty_enabled": data.get("dirty_enabled") is True,
            "rules": rules, "rules_version": data.get("rules_version") or RULES_VERSION, "version": row.version}


def _check_account_map(account_map) -> None:
    """Keys are Drive folders `<root>/<folder>/<subfolder>` (the shape the revision folder check builds); values
    are account ids."""
    if not isinstance(account_map, dict):
        raise ValidationError("account_map", "must be an object")
    for key, value in account_map.items():
        parts = key.split("/") if isinstance(key, str) else []
        if (len(parts) != 3 or any(not part or part != part.strip() for part in parts)
                or parts[0] not in STATEMENT_SOURCE_ROOTS):
            raise ValidationError("account_map", f"key {key!r} is not <root>/<folder>/<subfolder>")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError("account_map", f"value for {key!r} is not an account id")


def reconciliation_rules(db: Session) -> dict:
    """The matching rules (defaults overlaid with the stored keys), read without inserting the settings row: the
    reconcile transaction writes nothing outside its statement."""
    row = db.get(ReconciliationSettings, 1)
    stored = (row.data or {}).get("rules") if row is not None else None
    return {**RULE_DEFAULTS, **(stored or {})}


def get_reconciliation_settings(db: Session) -> dict:
    return _reconciliation_dict(_reconciliation_row(db))


def update_reconciliation_settings(db: Session, data: dict) -> dict:
    """Replace the settings data and bump `version`; the row is locked so concurrent saves bump it once each.
    `dirty_enabled` is the dirty-trigger kill switch the ledger triggers read (off unless true)."""
    _check_account_map(data.get("account_map", {}))
    if not isinstance(data.get("dirty_enabled", False), bool):
        raise ValidationError("dirty_enabled", "must be a boolean")
    row = _reconciliation_row(db, lock=True)
    row.data = dict(data)
    row.version = row.version + 1
    db.flush()
    return _reconciliation_dict(row)


# --- shared ---------------------------------------------------------------------

CREDIT_ONLY_FIELDS = (
    "due_rule", "due_value", "credit_limit", "combined_account_id", "credit_sharing_id", "auto_pay_account_id",
)


def _get(db: Session, model, row_id: int, label: str):
    row = db.get(model, row_id)
    if row is None:
        raise NotFoundError(f"{label} {row_id} not found")
    return row


def _unique_name(db: Session, model, name: str, own_id: int | None) -> None:
    other = db.scalar(select(model.id).where(model.name == name))
    if other is not None and other != own_id:
        raise ValidationError("name", f"{name!r} already exists")


def _count(db: Session, *criteria) -> int:
    return db.scalar(select(func.count()).select_from(LedgerEntry).where(*criteria))


def _reorder(db: Session, model, ids: list[int]) -> None:
    rows = {row.id: row for row in db.scalars(select(model).where(model.id.in_(ids)))}
    missing = [row_id for row_id in ids if row_id not in rows]
    if missing or len(set(ids)) != len(ids):
        raise ValidationError("ids", f"unknown or repeated ids: {missing or ids}")
    for position, row_id in enumerate(ids):
        rows[row_id].sort_order = position
    db.flush()


# --- accounts -------------------------------------------------------------------


def _check_combined(db: Session, self_id: int | None, combined_id: int) -> None:
    if combined_id == self_id:
        raise ValidationError("combined_account_id", "an account cannot be its own 主帳戶")
    target = db.get(Account, combined_id)
    if target is None or target.is_archived:
        raise ValidationError("combined_account_id", "must be an existing, non-archived account")
    seen: set[int] = set()
    current = target
    while current is not None and current.combined_account_id is not None:
        if current.combined_account_id == self_id or current.combined_account_id in seen:
            raise ValidationError("combined_account_id", "would form a 主帳戶 cycle")
        seen.add(current.id)
        current = db.get(Account, current.combined_account_id)


def _validate_account(db: Session, payload: AccountIn, account: Account | None) -> None:
    self_id = account.id if account is not None else None
    if not payload.is_credit:
        for field in CREDIT_ONLY_FIELDS:
            if getattr(payload, field) is not None:
                raise ValidationError(field, "only allowed when is_credit is true")
    if (payload.due_rule is None) != (payload.due_value is None):
        raise ValidationError("due_value", "due_rule and due_value are set together")
    _unique_name(db, Account, payload.name, self_id)
    if payload.group_id is not None and db.get(AccountGroup, payload.group_id) is None:
        raise ValidationError("group_id", f"account group {payload.group_id} not found")
    # The link targets are validated only when they change: an archived 主帳戶 or auto-pay account must not block
    # every later edit (a rename) of the accounts already linked to it.
    if payload.combined_account_id is not None and payload.combined_account_id != getattr(account, "combined_account_id", None):
        _check_combined(db, self_id, payload.combined_account_id)
    if payload.auto_pay_account_id is not None and payload.auto_pay_account_id != getattr(account, "auto_pay_account_id", None):
        target = db.get(Account, payload.auto_pay_account_id)
        if payload.auto_pay_account_id == self_id or target is None or target.is_archived:
            raise ValidationError("auto_pay_account_id", "must be another existing, non-archived account")
    if account is not None and payload.currency != account.currency and _count(db, LedgerEntry.account_id == account.id):
        raise ValidationError("currency", "currency cannot change while the account has entries")
    _check_sharing_members(db, payload, self_id)


ACCOUNT_COLUMNS_EXCLUDE = {"credit_sharing_members"}  # request-only field, not a column


def _check_sharing_members(db: Session, payload: AccountIn, self_id: int | None) -> None:
    members = payload.credit_sharing_members
    if not members:
        return
    if not payload.is_credit:
        raise ValidationError("credit_sharing_members", "only allowed when is_credit is true")
    for member_id in members:
        if member_id == self_id:
            raise ValidationError("credit_sharing_members", "an account cannot share a limit with itself")
        member = db.get(Account, member_id)
        if member is None or not member.is_credit:
            raise ValidationError("credit_sharing_members", f"account {member_id} is not an existing credit account")


def _apply_sharing(db: Session, account: Account, previous: UUID | None, members: list[int]) -> None:
    """Give `{account} ∪ members` one credit_sharing_id and clear it from accounts that left the set.

    `members == []`: only this account leaves its set; the others keep sharing among themselves.
    `settings_locally_edited = True` is set on the account and on every member added or removed (their
    credit_sharing_id changed, so a re-import must keep it); members whose sharing is unchanged are not touched.
    Runs inside the request transaction, so the whole diff commits or none of it does.
    """
    if not members:
        account.credit_sharing_id = None
        account.settings_locally_edited = True
        db.flush()
        return
    sharing_id = previous or uuid4()
    keep = {account.id, *members}
    db.flush()
    current = set(db.scalars(select(Account.id).where(Account.credit_sharing_id == sharing_id)))
    removed, added = current - keep, keep - current
    if removed:
        db.execute(
            update(Account)
            .where(Account.id.in_(removed))
            .values(credit_sharing_id=None, settings_locally_edited=True)
            .execution_options(synchronize_session=False)
        )
    db.execute(
        update(Account)
        .where(Account.id.in_(keep))
        .values(credit_sharing_id=sharing_id)
        .execution_options(synchronize_session=False)
    )
    db.execute(
        update(Account)
        .where(Account.id.in_({account.id, *added}))
        .values(settings_locally_edited=True)
        .execution_options(synchronize_session=False)
    )
    db.flush()
    db.expire_all()


def create_account(db: Session, payload: AccountIn) -> int:
    _validate_account(db, payload, None)
    account = Account(**payload.model_dump(exclude=ACCOUNT_COLUMNS_EXCLUDE), settings_locally_edited=True)
    db.add(account)
    db.flush()
    if payload.credit_sharing_members is not None:
        _apply_sharing(db, account, account.credit_sharing_id, payload.credit_sharing_members)
    return account.id


def update_account(db: Session, account_id: int, payload: AccountIn) -> None:
    """Every column except moze_id; marks the settings as locally edited so a re-import keeps them (D19).

    With `credit_sharing_members` present, the sharing set is rebuilt from it (the sent `credit_sharing_id` is
    ignored); without it, `credit_sharing_id` is written as sent, as before.
    """
    account = _get(db, Account, account_id, "account")
    _validate_account(db, payload, account)
    if payload.is_archived and not account.is_archived:
        schedule_entry_hooks.assert_not_referenced(db, account_id=account_id)
    previous_sharing_id = account.credit_sharing_id
    for field, value in payload.model_dump(exclude=ACCOUNT_COLUMNS_EXCLUDE).items():
        setattr(account, field, value)
    account.settings_locally_edited = True
    db.flush()
    if payload.credit_sharing_members is not None:
        _apply_sharing(db, account, previous_sharing_id, payload.credit_sharing_members)


def delete_account(db: Session, account_id: int) -> None:
    account = _get(db, Account, account_id, "account")
    schedule_entry_hooks.assert_not_referenced(db, account_id=account_id)
    entries = _count(db, LedgerEntry.account_id == account_id)
    if entries:
        raise ConflictError(f"account has {entries} entries; set is_archived instead of deleting it")
    rule = db.scalar(
        select(RewardRule.id)
        .where(or_(RewardRule.account_id == account_id, RewardRule.reward_account_id == account_id))
        .limit(1)
    )
    if rule is not None:
        raise ConflictError("account is used by reward rules; set is_archived instead of deleting it")
    # Accounts whose links are cleared changed settings, so a re-import must keep that (settings_locally_edited).
    db.execute(
        update(Account)
        .where(Account.combined_account_id == account_id)
        .values(combined_account_id=None, settings_locally_edited=True)
    )
    db.execute(
        update(Account)
        .where(Account.auto_pay_account_id == account_id)
        .values(auto_pay_account_id=None, settings_locally_edited=True)
    )
    db.execute(update(Category).where(Category.default_account_id == account_id).values(default_account_id=None))
    db.delete(account)
    db.flush()


def reset_settings_flag(db: Session, account_id: int) -> None:
    """Let the next backup import overwrite this account's settings again (還原 MOZE 設定)."""
    _get(db, Account, account_id, "account").settings_locally_edited = False
    db.flush()


# --- account groups -------------------------------------------------------------


def _group_dict(group: AccountGroup) -> dict:
    return {"id": group.id, "name": group.name, "sort_order": group.sort_order, "moze_id": group.moze_id}


def list_groups(db: Session) -> list[dict]:
    return [_group_dict(g) for g in db.scalars(select(AccountGroup).order_by(AccountGroup.sort_order, AccountGroup.name))]


def create_group(db: Session, payload: AccountGroupIn) -> int:
    _unique_name(db, AccountGroup, payload.name, None)
    group = AccountGroup(name=payload.name, sort_order=payload.sort_order)
    db.add(group)
    db.flush()
    return group.id


def update_group(db: Session, group_id: int, payload: AccountGroupIn) -> None:
    group = _get(db, AccountGroup, group_id, "account group")
    _unique_name(db, AccountGroup, payload.name, group_id)
    group.name, group.sort_order = payload.name, payload.sort_order
    db.flush()


def delete_group(db: Session, group_id: int) -> None:
    group = _get(db, AccountGroup, group_id, "account group")
    accounts = db.scalar(select(func.count()).select_from(Account).where(Account.group_id == group_id))
    if accounts:
        raise ConflictError(f"account group has {accounts} accounts")
    db.delete(group)
    db.flush()


def reorder_groups(db: Session, ids: list[int]) -> None:
    _reorder(db, AccountGroup, ids)


# --- categories -----------------------------------------------------------------


def _category_dict(category: Category, parent: Category | None) -> dict:
    return {
        "id": category.id,
        "kind": category.kind,
        "parent_id": category.parent_id,
        "name": category.name,
        "icon": category.icon or (parent.icon if parent is not None else None),
        "color": category.color or (parent.color if parent is not None else None),
        "sort_order": category.sort_order,
        "is_hidden": category.is_hidden,
        "default_account_id": category.default_account_id,
        "default_project_id": category.default_project_id,
        "moze_id": category.moze_id,
        "children": [],
    }


def list_categories(db: Session, kind: str) -> list[dict]:
    """Two-level tree ordered by sort_order then name; sub-categories inherit a missing icon or colour."""
    rows = list(
        db.scalars(select(Category).where(Category.kind == kind).order_by(Category.sort_order, Category.name, Category.id))
    )
    mains = {row.id: row for row in rows if row.parent_id is None}
    tree = {row_id: _category_dict(row, None) for row_id, row in mains.items()}
    for row in rows:
        if row.parent_id in tree:
            tree[row.parent_id]["children"].append(_category_dict(row, mains[row.parent_id]))
    return list(tree.values())


def get_category(db: Session, category_id: int) -> dict:
    category = _get(db, Category, category_id, "category")
    parent = db.get(Category, category.parent_id) if category.parent_id is not None else None
    node = _category_dict(category, parent)
    if parent is None:
        node["children"] = [
            _category_dict(child, category)
            for child in db.scalars(
                select(Category)
                .where(Category.parent_id == category.id)
                .order_by(Category.sort_order, Category.name, Category.id)
            )
        ]
    return node


def _validate_category(db: Session, payload: CategoryIn, category: Category | None) -> None:
    if payload.kind in SYSTEM_KINDS:
        raise ValidationError("kind", f"{payload.kind} uses a single fixed category")
    if category is not None and payload.kind != category.kind:
        raise ValidationError("kind", "a category cannot change kind")
    if payload.parent_id is not None:
        parent = db.get(Category, payload.parent_id)
        if (
            parent is None
            or parent.kind != payload.kind
            or parent.parent_id is not None
            or (category is not None and parent.id == category.id)
        ):
            raise ValidationError("parent_id", "parent must be another main category of the same kind")
        if category is not None and db.scalar(select(Category.id).where(Category.parent_id == category.id).limit(1)):
            raise ValidationError("parent_id", "a main category with sub-categories cannot become a sub-category")
    parent_filter = Category.parent_id.is_(None) if payload.parent_id is None else Category.parent_id == payload.parent_id
    duplicate = db.scalar(
        select(Category.id).where(Category.kind == payload.kind, parent_filter, Category.name == payload.name)
    )
    if duplicate is not None and (category is None or duplicate != category.id):
        raise ValidationError("name", f"{payload.name!r} already exists here")


def create_category(db: Session, payload: CategoryIn) -> int:
    _validate_category(db, payload, None)
    category = Category(**payload.model_dump())
    db.add(category)
    db.flush()
    return category.id


def update_category(db: Session, category_id: int, payload: CategoryIn) -> None:
    category = _get(db, Category, category_id, "category")
    _validate_category(db, payload, category)
    if payload.is_hidden and not category.is_hidden:
        schedule_entry_hooks.assert_not_referenced(db, category_id=category_id)
    for field, value in payload.model_dump().items():
        setattr(category, field, value)
    db.flush()


def delete_category(db: Session, category_id: int) -> None:
    category = _get(db, Category, category_id, "category")
    schedule_entry_hooks.assert_not_referenced(db, category_id=category_id)
    entries = _count(db, LedgerEntry.category_id == category_id)
    if entries:
        raise ConflictError(f"category is used by {entries} entries; hide it instead")
    if db.scalar(select(Category.id).where(Category.parent_id == category_id).limit(1)) is not None:
        raise ConflictError("category has sub-categories")
    db.delete(category)
    db.flush()


def reorder_categories(db: Session, ids: list[int]) -> None:
    _reorder(db, Category, ids)


# --- projects -------------------------------------------------------------------


def _project_dict(project: Project) -> dict:
    return {
        "id": project.id, "name": project.name, "is_archived": project.is_archived,
        "sort_order": project.sort_order, "moze_id": project.moze_id,
    }


def list_projects(db: Session) -> list[dict]:
    rows = db.scalars(select(Project).order_by(Project.is_archived, Project.sort_order, Project.name))
    return [_project_dict(project) for project in rows]


def create_project(db: Session, payload: ProjectIn) -> int:
    _unique_name(db, Project, payload.name, None)
    project = Project(**payload.model_dump())
    db.add(project)
    db.flush()
    return project.id


def update_project(db: Session, project_id: int, payload: ProjectIn) -> None:
    project = _get(db, Project, project_id, "project")
    _unique_name(db, Project, payload.name, project_id)
    if payload.is_archived and not project.is_archived:
        schedule_entry_hooks.assert_not_referenced(db, project_id=project_id)
    for field, value in payload.model_dump().items():
        setattr(project, field, value)
    db.flush()


def delete_project(db: Session, project_id: int) -> None:
    project = _get(db, Project, project_id, "project")
    schedule_entry_hooks.assert_not_referenced(db, project_id=project_id)
    entries = _count(db, LedgerEntry.project_id == project_id)
    if entries:
        raise ConflictError(f"project is used by {entries} entries; archive it instead")
    if db.scalar(select(RewardRule.id).where(RewardRule.reward_project_id == project_id).limit(1)) is not None:
        raise ConflictError("project is used by reward rules; archive it instead")
    db.execute(update(Category).where(Category.default_project_id == project_id).values(default_project_id=None))
    db.delete(project)
    db.flush()


# --- counterparties -------------------------------------------------------------


def _open_amounts(db: Session) -> dict[int, list[dict]]:
    """Per counterparty and currency: −Σ amount over its receivable and payable entries (settlements included).

    Closed debts (`is_closed`) and the settlements linked to them are left out: MOZE counts them as settled. A
    settlement linked to an original in another currency is left out too: it never nets against that original.
    """
    closed = select(LedgerEntry.id).where(LedgerEntry.is_closed).scalar_subquery()
    original = aliased(LedgerEntry)
    cross_currency = (
        select(original.id)
        .where(original.id == LedgerEntry.settles_entry_id, original.currency != LedgerEntry.currency)
        .exists()
    )
    rows = db.execute(
        select(LedgerEntry.counterparty_id, LedgerEntry.currency, func.sum(LedgerEntry.amount))
        .where(
            LedgerEntry.counterparty_id.is_not(None),
            LedgerEntry.kind.in_(("receivable", "payable")),
            ~LedgerEntry.is_closed,
            or_(LedgerEntry.settles_entry_id.is_(None), LedgerEntry.settles_entry_id.not_in(closed)),
            ~cross_currency,
        )
        .group_by(LedgerEntry.counterparty_id, LedgerEntry.currency)
        .order_by(LedgerEntry.currency)
    )
    amounts: dict[int, list[dict]] = {}
    for counterparty_id, currency, total in rows:
        if total != 0:
            amounts.setdefault(counterparty_id, []).append({"currency": currency, "amount": -total})
    return amounts


def _open_counts(db: Session) -> dict[int, int]:
    """Per counterparty: its open receivable / payable originals (the `GET /entries?open=true` definition)."""
    rows = db.execute(
        select(LedgerEntry.counterparty_id, func.count())
        .where(LedgerEntry.counterparty_id.is_not(None), open_debt_filter())
        .group_by(LedgerEntry.counterparty_id)
    )
    return {counterparty_id: count for counterparty_id, count in rows}


def list_counterparties(db: Session) -> list[dict]:
    amounts = _open_amounts(db)
    counts = _open_counts(db)
    return [
        {
            "id": row.id, "name": row.name, "moze_id": row.moze_id, "open_amounts": amounts.get(row.id, []),
            "open_count": counts.get(row.id, 0),
        }
        for row in db.scalars(select(Counterparty).order_by(Counterparty.name))
    ]


def create_counterparty(db: Session, payload: CounterpartyIn) -> int:
    _unique_name(db, Counterparty, payload.name, None)
    counterparty = Counterparty(name=payload.name)
    db.add(counterparty)
    db.flush()
    return counterparty.id


def update_counterparty(db: Session, counterparty_id: int, payload: CounterpartyIn) -> None:
    """Rename; entries reference the row, so every listing shows the new name."""
    counterparty = _get(db, Counterparty, counterparty_id, "counterparty")
    _unique_name(db, Counterparty, payload.name, counterparty_id)
    counterparty.name = payload.name
    db.flush()


def delete_counterparty(db: Session, counterparty_id: int) -> None:
    counterparty = _get(db, Counterparty, counterparty_id, "counterparty")
    schedule_entry_hooks.assert_not_referenced(db, counterparty_id=counterparty_id)
    entries = _count(db, LedgerEntry.counterparty_id == counterparty_id)
    if entries:
        raise ConflictError(f"counterparty is used by {entries} entries")
    db.delete(counterparty)
    db.flush()
