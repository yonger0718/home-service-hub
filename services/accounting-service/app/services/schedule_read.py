"""Response shapes of /schedules and the schedule links on entries (spec "Definition endpoints", "Instance
endpoints", "Loan summary", "Schedule links on entries")."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, case, false, func, or_, select
from sqlalchemy.orm import Session

from ..models import Account, Category, Counterparty, LedgerEntry, ScheduleDefinition, ScheduleInstance
from . import ledger_service, settlement_service
from .edit_lock import import_locked
from .errors import NotFoundError
from .schedule_rules import plain
from .schedule_templates import LEDGER_KINDS, loan_line, resolved_amounts

POSITIVE_LINE_KINDS = ("income", "payable", "collection")
QUEUE_DAYS = 30
LOAN_SCHEDULE_KEYS = (
    "definition_id", "name", "status", "posting_mode", "posted_count", "times", "next_due_date", "next_amount",
    "remaining", "repaid", "needs_check",
)


def signed_line_amount(kind: str, amount: Decimal) -> Decimal:
    return amount if kind in POSITIVE_LINE_KINDS else -amount


class _Names:
    """Account, category (path, icon, colour) and counterparty names, read once per response."""

    def __init__(self, db: Session):
        self.accounts = {account.id: account.name for account in db.scalars(select(Account))}
        rows = {category.id: category for category in db.scalars(select(Category))}
        self.categories: dict[int, tuple[str, str | None, str | None]] = {}
        for category in rows.values():
            parent = rows.get(category.parent_id) if category.parent_id is not None else None
            path = f"{parent.name}/{category.name}" if parent else category.name
            icon = category.icon or (parent.icon if parent else None)
            color = category.color or (parent.color if parent else None)
            self.categories[category.id] = (path, icon, color)
        self.counterparties = {row.id: row.name for row in db.scalars(select(Counterparty))}

    def category(self, category_id: int | None) -> tuple[str | None, str | None, str | None]:
        return self.categories.get(category_id, (None, None, None)) if category_id is not None else (None, None, None)

    def account(self, account_id: int | None) -> str | None:
        return self.accounts.get(account_id) if account_id is not None else None


def _totals(pairs: dict[str, Decimal]) -> list[dict]:
    return [{"currency": currency, "amount": amount} for currency, amount in sorted(pairs.items())]


def instance_out(
    db: Session,
    instance: ScheduleInstance,
    *,
    today: date | None = None,
    definition: ScheduleDefinition | None = None,
    names: _Names | None = None,
) -> dict:
    today = today or ledger_service._today()
    definition = definition or db.get(ScheduleDefinition, instance.definition_id)
    names = names or _Names(db)
    lines, totals = [], defaultdict(Decimal)
    resolved = resolved_amounts(definition, instance)
    for line, amount in zip(definition.template["lines"], resolved):
        if amount == 0:
            continue
        signed = signed_line_amount(line["kind"], amount)
        lines.append(
            {
                "kind": line["kind"], "account_id": line["account_id"], "account_name": names.account(line["account_id"]),
                "to_account_id": line["to_account_id"], "to_account_name": names.account(line["to_account_id"]),
                "category": names.category(line["category_id"])[0],
                "counterparty": names.counterparties.get(line["counterparty_id"]),
                "amount": signed, "currency": line["currency"],
            }
        )
        totals[line["currency"]] += signed
    _, icon, color = names.category(definition.template["lines"][0].get("category_id"))
    overdue = (today - instance.due_date).days if instance.status == "pending" and instance.due_date < today else 0
    return {
        "id": instance.id, "definition_id": definition.id, "definition_name": definition.name, "kind": definition.kind,
        "posting_mode": definition.posting_mode, "seq": instance.seq, "times": definition.times,
        "due_date": instance.due_date, "rule_date": instance.rule_date, "status": instance.status,
        "is_partial": instance.is_partial, "overdue_days": overdue, "lines": lines, "totals": _totals(totals),
        "amounts": [plain(value) for value in resolved],  # unsigned, aligned with the template (repost bodies)
        "last_error": instance.last_error, "reopened": instance.reopened_at is not None,
        "edited_by_owner": instance.edited_by_owner, "note": instance.note,
        "posted_entry_ids": list(instance.posted_entry_ids), "acted_at": instance.acted_at,
        "acted_by": instance.acted_by, "category_icon": icon, "category_color": color,
    }


def loan_summary(db: Session, definition: ScheduleDefinition) -> tuple[Decimal | None, Decimal | None]:
    """(remaining, repaid) per spec "Loan summary"."""
    reference = loan_line(definition.template)
    if reference is not None:
        loan_id = reference[1].get("loan_entry_id")
        loan = db.get(LedgerEntry, loan_id) if loan_id is not None else None
        if loan is None:
            return None, None
        open_amount = Decimal(0) if loan.is_closed else settlement_service.open_amount(db, loan)
        remaining = -open_amount if loan.kind == "payable" and open_amount != 0 else open_amount
        repaid = db.scalar(
            select(func.coalesce(func.sum(func.abs(LedgerEntry.amount)), 0)).where(LedgerEntry.settles_entry_id == loan.id)
        )
        return remaining, Decimal(repaid)
    if definition.kind == "installment" and definition.total_amount is not None:
        first = definition.template["lines"][0]
        posted = db.scalars(
            select(ScheduleInstance.posted_entry_ids).where(
                ScheduleInstance.definition_id == definition.id, ScheduleInstance.status == "posted"
            )
        ).all()
        ids = [entry_id for entry_ids in posted for entry_id in entry_ids]
        entries = {
            entry.id: entry
            for entry in db.scalars(
                select(LedgerEntry).where(
                    LedgerEntry.id.in_(ids) if ids else false(),
                    LedgerEntry.account_id == first["account_id"],
                    LedgerEntry.kind == LEDGER_KINDS[first["kind"]],
                )
            )
        }
        used = Decimal(0)
        for entry_ids in posted:
            match = next((entries[entry_id] for entry_id in entry_ids if entry_id in entries), None)
            if match is not None:
                used += abs(Decimal(match.amount))
        return Decimal(definition.total_amount) - used, None
    return None, None


def _line_out(line: dict, names: _Names) -> dict:
    return {
        **line,
        "account_name": names.account(line["account_id"]),
        "to_account_name": names.account(line["to_account_id"]),
        "category": names.category(line["category_id"])[0],
        "counterparty": names.counterparties.get(line["counterparty_id"]),
    }


def definition_out(
    db: Session, definition: ScheduleDefinition, *, today: date | None = None, names: _Names | None = None
) -> dict:
    today = today or ledger_service._today()
    names = names or _Names(db)
    counts = dict(
        db.execute(
            select(ScheduleInstance.status, func.count())
            .where(ScheduleInstance.definition_id == definition.id)
            .group_by(ScheduleInstance.status)
        ).all()
    )
    pending = select(ScheduleInstance).where(
        ScheduleInstance.definition_id == definition.id, ScheduleInstance.status == "pending"
    )
    next_instance = db.scalar(pending.order_by(ScheduleInstance.due_date, ScheduleInstance.seq).limit(1))
    failing = db.scalar(
        pending.where(ScheduleInstance.last_error.is_not(None))
        .order_by(ScheduleInstance.due_date, ScheduleInstance.seq)
        .limit(1)
    )
    remaining, repaid = loan_summary(db, definition)
    reference = loan_line(definition.template)
    loan_open = reference is not None and remaining is not None and remaining != 0
    _, icon, color = names.category(definition.template["lines"][0].get("category_id"))
    next_amount = (
        instance_out(db, next_instance, today=today, definition=definition, names=names)["totals"] if next_instance else []
    )
    return {
        "id": definition.id, "kind": definition.kind, "name": definition.name, "status": definition.status,
        "posting_mode": definition.posting_mode, "interval_unit": definition.interval_unit,
        "interval_n": definition.interval_n, "anchor_date": definition.anchor_date,
        "day_of_month": definition.day_of_month, "first_seq": definition.first_seq, "times": definition.times,
        "end_date": definition.end_date, "total_amount": definition.total_amount,
        "auto_post_from": definition.auto_post_from,
        "template": {
            "lines": [_line_out(line, names) for line in definition.template["lines"]],
            "description": definition.template.get("description"),
            "tags": list(definition.template.get("tags") or []),
        },
        "created_locally": definition.created_locally, "template_owner_edited": definition.template_owner_edited,
        "imported": definition.moze_id is not None,
        "locked": definition.moze_id is not None and not import_locked(),
        "review_reason": definition.review_reason, "generated_until": definition.generated_until,
        "posted_count": counts.get("posted", 0), "skipped_count": counts.get("skipped", 0),
        "pending_count": counts.get("pending", 0),
        "next_due_date": next_instance.due_date if next_instance else None, "next_amount": next_amount,
        "remaining": remaining, "repaid": repaid,
        "loan_entry_id": reference[1].get("loan_entry_id") if reference else None,
        "needs_check": definition.review_reason is not None or (definition.status == "ended" and loan_open),
        "failing": (
            {"instance_id": failing.id, "due_date": failing.due_date, "last_error": failing.last_error}
            if failing is not None else None
        ),
        "category_icon": icon, "category_color": color,
    }


def list_definitions(db: Session, *, status: str | None = None, kind: str | None = None) -> list[dict]:
    query = select(ScheduleDefinition)
    if status is not None:
        query = query.where(ScheduleDefinition.status == status)
    if kind is not None:
        query = query.where(ScheduleDefinition.kind == kind)
    names, today = _Names(db), ledger_service._today()
    items = [definition_out(db, row, today=today, names=names) for row in db.scalars(query.order_by(ScheduleDefinition.id))]
    return sorted(items, key=lambda item: (item["next_due_date"] is None, item["next_due_date"] or date.max, item["id"]))


def get_definition(db: Session, definition_id: int) -> dict:
    definition = db.get(ScheduleDefinition, definition_id)
    if definition is None:
        raise NotFoundError(f"schedule definition {definition_id} not found")
    names, today = _Names(db), ledger_service._today()
    out = definition_out(db, definition, today=today, names=names)
    rows = db.scalars(
        select(ScheduleInstance).where(ScheduleInstance.definition_id == definition_id).order_by(ScheduleInstance.seq)
    )
    out["instances"] = [instance_out(db, row, today=today, definition=definition, names=names) for row in rows]
    return out


def list_instances(
    db: Session,
    *,
    date_from: date | None = None,
    until: date | None = None,
    status: str | None = "pending",
    definition_id: int | None = None,
    queue: bool = False,
) -> list[dict]:
    today = ledger_service._today()
    until = until or today + timedelta(days=QUEUE_DAYS)
    query = select(ScheduleInstance, ScheduleDefinition).join(
        ScheduleDefinition, ScheduleDefinition.id == ScheduleInstance.definition_id
    )
    if queue:
        waiting = and_(
            ScheduleInstance.status == "pending",
            ScheduleInstance.due_date <= until,
            or_(
                ScheduleDefinition.posting_mode == "confirm",
                ScheduleInstance.reopened_at.is_not(None),
                ScheduleInstance.last_error.is_not(None),
                ScheduleInstance.due_date < ScheduleDefinition.auto_post_from,
            ),
        )
        partial = and_(ScheduleInstance.status == "posted", ScheduleInstance.is_partial.is_(True))
        query = query.where(ScheduleDefinition.status == "active", or_(waiting, partial))
    else:
        query = query.where(ScheduleInstance.due_date <= until)
        if status is not None:
            query = query.where(ScheduleInstance.status == status)
        if date_from is not None:
            query = query.where(ScheduleInstance.due_date >= date_from)
    if definition_id is not None:
        query = query.where(ScheduleInstance.definition_id == definition_id)
    query = query.order_by(ScheduleInstance.due_date, ScheduleInstance.definition_id, ScheduleInstance.seq)
    names = _Names(db)
    return [
        instance_out(db, instance, today=today, definition=definition, names=names)
        for instance, definition in db.execute(query).all()
    ]


def schedule_links(db: Session, entry_ids: list[int]) -> dict[int, dict]:
    """entry id → the `schedule` object of its posted instance (one @> lookup per id through the GIN index)."""
    wanted = set(entry_ids)
    if not wanted:
        return {}
    rows = db.execute(
        select(ScheduleInstance, ScheduleDefinition)
        .join(ScheduleDefinition, ScheduleDefinition.id == ScheduleInstance.definition_id)
        .where(or_(*[ScheduleInstance.posted_entry_ids.contains([entry_id]) for entry_id in sorted(wanted)]))
    ).all()
    links: dict[int, dict] = {}
    for instance, definition in rows:
        link = {
            "definition_id": definition.id, "instance_id": instance.id, "kind": definition.kind, "seq": instance.seq,
            "times": definition.times, "name": definition.name, "is_partial": instance.is_partial,
            "acted_by": instance.acted_by, "posted_entry_ids": list(instance.posted_entry_ids),
        }
        for entry_id in instance.posted_entry_ids:
            if entry_id in wanted:
                links[entry_id] = link
    return links


def loan_schedule_for(db: Session, entry_id: int) -> dict | None:
    """`loan_schedule` of a payable / receivable original that a definition's line references (a live one first)."""
    definition = db.scalar(
        select(ScheduleDefinition)
        .where(ScheduleDefinition.template["lines"].contains([{"loan_entry_id": entry_id}]))
        .order_by(case((ScheduleDefinition.status == "ended", 1), else_=0), ScheduleDefinition.id.desc())
        .limit(1)
    )
    if definition is None:
        return None
    out = definition_out(db, definition)
    out["definition_id"] = out["id"]
    return {key: out[key] for key in LOAN_SCHEDULE_KEYS}
