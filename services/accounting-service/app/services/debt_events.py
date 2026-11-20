"""The record of closing and reopening a debt (#401): one `debt_state_event` per change of `is_closed` made through
POST /entries/{id}/close and /reopen. No entry edit and no entry delete removes an event; the one thing that changes
on it is the owner's confirmation (`reviewed_at`).

A close that takes a remainder out of the open-debt views, made by a caller without the owner's scope, waits on
`to_review` until the owner confirms it (or the debt is reopened).
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import Counterparty, DebtStateEvent, LedgerEntry
from .errors import NotFoundError, ValidationError
from .ledger_service import LEDGER_TZ

CLOSE, REOPEN = "close", "reopen"


def record(
    db: Session, entry: LedgerEntry, action: str, actor: str | None, open_amount: Decimal, *, by_owner: bool
) -> None:
    """Write the event of a flag change the caller is about to make, under the entry's row lock. `open_amount` is the
    remainder a close takes out or a reopen brings back. `by_owner`: the caller holds the owner's scope, so the event
    needs no confirmation."""
    counterparty = db.get(Counterparty, entry.counterparty_id) if entry.counterparty_id is not None else None
    db.add(
        DebtStateEvent(
            entry_id=entry.id, action=action, actor=actor, open_amount=open_amount, currency=entry.currency,
            entry_kind=entry.kind, entry_name=entry.name, counterparty=counterparty.name if counterparty else None,
            entry_amount=entry.amount, entry_date=entry.entry_date, reviewed_at=func.now() if by_owner else None,
        )
    )


def _waits(event: DebtStateEvent) -> bool:
    return event.action == CLOSE and event.open_amount > 0 and event.reviewed_at is None


def _event_date(event: DebtStateEvent) -> date:
    return event.created_at.astimezone(LEDGER_TZ).date()


def events_of(db: Session, entry_id: int) -> list[dict]:
    """The entry's events, oldest first. Only the latest can still wait for the owner: a reopen settles what came
    before it."""
    rows = list(db.scalars(select(DebtStateEvent).where(DebtStateEvent.entry_id == entry_id).order_by(DebtStateEvent.id)))
    last = rows[-1].id if rows else None
    return [
        {
            "id": row.id, "action": row.action, "actor": row.actor, "open_amount": row.open_amount,
            "currency": row.currency, "event_date": _event_date(row), "needs_review": row.id == last and _waits(row),
        }
        for row in rows
    ]


def to_review(db: Session, limit: int) -> dict:
    """The debts whose latest event is a close with a remainder that the owner has not confirmed, newest first; the
    entry is either gone (its snapshot answers) or still closed. The list is decided by the remainder at the close
    (an edit of the debt cannot take it off); a live debt also answers its remainder now (#403)."""
    from .settlement_service import open_amount  # settlement_service imports this module: no module-level import

    latest = select(func.max(DebtStateEvent.id)).group_by(DebtStateEvent.entry_id)
    query = (
        select(DebtStateEvent, LedgerEntry, Counterparty.name)
        .outerjoin(LedgerEntry, LedgerEntry.id == DebtStateEvent.entry_id)
        .outerjoin(Counterparty, Counterparty.id == LedgerEntry.counterparty_id)
        .where(
            DebtStateEvent.id.in_(latest), DebtStateEvent.action == CLOSE, DebtStateEvent.open_amount > 0,
            DebtStateEvent.reviewed_at.is_(None), or_(LedgerEntry.id.is_(None), LedgerEntry.is_closed.is_(True)),
        )
    )
    total = db.scalar(select(func.count()).select_from(query.with_only_columns(DebtStateEvent.id).subquery()))
    items = []
    for event, entry, counterparty in db.execute(query.order_by(DebtStateEvent.id.desc()).limit(limit)):
        live = entry is not None
        items.append({
            "event_id": event.id, "entry_id": event.entry_id, "deleted": not live, "kind": event.entry_kind,
            "name": entry.name if live else event.entry_name,
            "counterparty": counterparty if live else event.counterparty,
            "entry_date": entry.entry_date if live else event.entry_date,
            "amount": entry.amount if live else event.entry_amount,
            "currency": event.currency, "open_amount": event.open_amount,
            # #403: a settlement deleted or changed after the close moves the remainder; the owner confirms this one.
            "current_open_amount": open_amount(db, entry) if live else None, "actor": event.actor,
            "event_date": _event_date(event),
        })
    return {"items": items, "total": total}


def review(db: Session, event_id: int) -> None:
    """The owner's confirmation of a close. Repeating it changes nothing."""
    event = db.get(DebtStateEvent, event_id, with_for_update=True)
    if event is None:
        raise NotFoundError(f"debt event {event_id} not found")
    if event.action != CLOSE:
        raise ValidationError("action", "only a close is reviewed")
    if event.reviewed_at is None:
        event.reviewed_at = func.now()
        db.flush()
