"""E-invoice matcher (spec §7): eligible targets, candidate rules, one-pass decision and the auto-link writes.
Never log invoice payloads (privacy)."""
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Account, Einvoice, EinvoiceLink, EinvoiceSellerMute, EntryGroup, LedgerEntry
from .einvoice_rules import TargetKey, decide, is_integral, near_tolerance, period_bounds, shares_token
from .einvoice_service import lock, stamp
from .ledger_service import main_currency
from .settings_service import read_einvoice_settings

NET_CHILD_KINDS = ("fee", "discount")
GROUP_NET_KINDS = ("expense", "receivable")
PROCESSED_STATES = ("unmatched", "ambiguous", "near")


@dataclass(frozen=True)
class Target:
    key: TargetKey
    family: int | None  # an entry's group_id (only split groups are ever candidates); a group's own id
    day: date
    net: Decimal
    currency: str
    original_currency: str | None
    original_amount: Decimal | None
    invoice_number: str | None
    merchant: str | None
    name: str | None
    account_name: str
    category_id: int | None


# --- eligible targets (§7.2) -------------------------------------------------------------------------------------

def _entry_linked():
    return exists().where(EinvoiceLink.entry_id == LedgerEntry.id)


def _group_linked(group_id_col):
    return exists().where(EinvoiceLink.group_id == group_id_col)


def _child_sums(db: Session, parent_ids: Sequence[int]) -> dict[int, Decimal]:
    if not parent_ids:
        return {}
    rows = db.execute(
        select(LedgerEntry.parent_entry_id, func.sum(LedgerEntry.amount))
        .where(LedgerEntry.parent_entry_id.in_(parent_ids), LedgerEntry.kind.in_(NET_CHILD_KINDS))
        .group_by(LedgerEntry.parent_entry_id)
    ).all()
    return {pid: Decimal(total) for pid, total in rows}


def _entry_rows(db: Session, *conditions):
    """Eligible top-level expense entries (no link on the entry or on its group), with the account name."""
    return db.execute(
        select(LedgerEntry, Account.name)
        .join(Account, Account.id == LedgerEntry.account_id)
        .where(LedgerEntry.kind == "expense", LedgerEntry.parent_entry_id.is_(None), ~_entry_linked(),
               or_(LedgerEntry.group_id.is_(None), ~_group_linked(LedgerEntry.group_id)), *conditions)
        .order_by(LedgerEntry.id)
    ).all()


def _entry_targets(rows, nets: dict[int, Decimal]) -> list[Target]:
    return [
        Target(key=("entry", e.id), family=e.group_id, day=e.entry_date,
               net=Decimal(e.amount) + nets.get(e.id, Decimal(0)), currency=e.currency,
               original_currency=e.original_currency,
               original_amount=Decimal(e.original_amount) if e.original_amount is not None else None,
               invoice_number=e.invoice_number, merchant=e.merchant, name=e.name, account_name=account_name,
               category_id=e.category_id)
        for e, account_name in rows
    ]


def _group_targets(db: Session, group_ids: Sequence[int]) -> list[Target]:
    """Split groups among `group_ids` with no link on the group or a member and one currency over the
    expense / receivable members. Loads all members in one query."""
    if not group_ids:
        return []
    groups = {g.id: g for g in db.scalars(
        select(EntryGroup).where(EntryGroup.id.in_(group_ids), EntryGroup.kind == "split",
                                 ~_group_linked(EntryGroup.id)))}
    if not groups:
        return []
    members = db.execute(
        select(LedgerEntry, Account.name, _entry_linked().label("linked"))
        .join(Account, Account.id == LedgerEntry.account_id)
        .where(LedgerEntry.group_id.in_(list(groups)), LedgerEntry.parent_entry_id.is_(None))
        .order_by(LedgerEntry.entry_date, LedgerEntry.id)
    ).all()
    nets = _child_sums(db, [e.id for e, _, _ in members if e.kind in GROUP_NET_KINDS])
    by_group: dict[int, list] = defaultdict(list)
    for row in members:
        by_group[row[0].group_id].append(row)
    out = []
    for gid in sorted(by_group):
        rows = by_group[gid]
        if any(linked for _, _, linked in rows):
            continue
        counted = [e for e, _, _ in rows if e.kind in GROUP_NET_KINDS]
        currencies = {e.currency for e in counted}
        if len(currencies) != 1:
            continue
        first, first_account, _ = rows[0]
        group = groups[gid]
        out.append(Target(
            key=("group", gid), family=gid, day=first.entry_date,
            net=sum((Decimal(e.amount) + nets.get(e.id, Decimal(0)) for e in counted), Decimal(0)),
            currency=currencies.pop(), original_currency=None, original_amount=None, invoice_number=None,
            merchant=group.merchant, name=group.name, account_name=first_account, category_id=first.category_id))
    return out


def eligible_targets(db: Session, date_from: date, date_to: date) -> list[Target]:
    """Entries dated in [date_from, date_to] and split groups whose earliest member is. The per-invoice
    `invoice_number` condition is applied in `rule_candidates` / `near_candidates`."""
    rows = _entry_rows(db, LedgerEntry.entry_date.between(date_from, date_to))
    entries = _entry_targets(rows, _child_sums(db, [e.id for e, _ in rows]))
    group_ids = db.scalars(
        select(LedgerEntry.group_id)
        .where(LedgerEntry.group_id.is_not(None), LedgerEntry.parent_entry_id.is_(None))
        .group_by(LedgerEntry.group_id)
        .having(func.min(LedgerEntry.entry_date).between(date_from, date_to))
    ).all()
    return entries + _group_targets(db, group_ids)


def entry_target(db: Session, entry_id: int) -> Target | None:
    rows = _entry_rows(db, LedgerEntry.id == entry_id)
    if not rows:
        return None
    return _entry_targets(rows, _child_sums(db, [entry_id]))[0]


def group_target(db: Session, group_id: int) -> Target | None:
    found = _group_targets(db, [group_id])
    return found[0] if found else None


# --- candidate rules (§7.3, §7.4) ----------------------------------------------------------------------------------

def _in_window(t: Target, day: date, window_days: int) -> bool:
    return abs((t.day - day).days) <= window_days


def _number_ok(t: Target, number: str) -> bool:
    return t.key[0] != "entry" or t.invoice_number is None or t.invoice_number == number


def rule_candidates(invoice: Einvoice, targets: Sequence[Target], *, window_days: int, main: str) -> list[Target]:
    number, day, amount = invoice.number, invoice.issue_date, Decimal(invoice.total_amount)
    start, end = period_bounds(invoice.period)
    exact = [t for t in targets if t.key[0] == "entry" and t.invoice_number == number and start <= t.day <= end]
    if exact:
        return exact
    near_day = [t for t in targets if _in_window(t, day, window_days) and _number_ok(t, number)]
    if is_integral(amount):
        return [t for t in near_day if t.currency == main and t.net == -amount
                and (t.key[0] == "group" or t.original_currency is None)]
    return [t for t in near_day if t.key[0] == "entry" and t.original_currency not in (None, main)
            and t.original_amount is not None and abs(t.original_amount) == amount]


def near_candidates(invoice: Einvoice, targets: Sequence[Target], *, window_days: int) -> list[Target]:
    amount = Decimal(invoice.total_amount)
    tolerance = near_tolerance(amount)
    return [t for t in targets
            if _in_window(t, invoice.issue_date, window_days) and _number_ok(t, invoice.number)
            and (abs(t.net + amount) <= tolerance or shares_token(invoice.seller_name, t.merchant, t.name))]


def order_by_closeness(targets: Sequence[Target], amount: Decimal, day: date) -> list[Target]:
    return sorted(targets, key=lambda t: (abs(abs(t.net) - amount), abs((t.day - day).days), t.key))


def _span(invoice: Einvoice, window_days: int) -> tuple[date, date]:
    start, end = period_bounds(invoice.period)
    window = timedelta(days=window_days)
    return min(start, invoice.issue_date - window), max(end, invoice.issue_date + window)


def _candidates(invoice: Einvoice, targets: Sequence[Target], window_days: int, main: str) -> tuple[list[Target], bool]:
    rule = rule_candidates(invoice, targets, window_days=window_days, main=main)
    if rule:
        return rule, False
    return near_candidates(invoice, targets, window_days=window_days), True


def candidates_for(db: Session, invoice: Einvoice) -> tuple[list[Target], bool]:
    """(rule candidates, False) when any, else (near candidates, True)."""
    window_days = read_einvoice_settings(db)["date_window_days"]
    targets = eligible_targets(db, *_span(invoice, window_days))
    return _candidates(invoice, targets, window_days, main_currency(db))


# --- the pass --------------------------------------------------------------------------------------------------

def _zero(ran: bool) -> dict:
    return {"ran": ran, "processed": 0, "linked": 0, "ambiguous": 0, "near": 0, "unmatched": 0, "unchanged": 0,
            "retry_later": 0}


def _insert_link(db: Session, invoice: Einvoice, key: TargetKey) -> None:
    kind, target_id = key
    db.add(EinvoiceLink(invoice_id=invoice.id, linked_by="auto",
                        entry_id=target_id if kind == "entry" else None,
                        group_id=target_id if kind == "group" else None))
    db.flush()


def run_matcher(db: Session) -> dict:
    settings = read_einvoice_settings(db)
    if not settings["enabled"]:
        return _zero(False)
    lock(db)  # re-entrant within the transaction: the import handler may already hold it
    db.flush()
    invoices = db.execute(
        select(Einvoice)
        .where(or_(Einvoice.state.in_(PROCESSED_STATES),
                   and_(Einvoice.state == "dismissed", Einvoice.dismiss_reason == "seller_muted")))
        .order_by(Einvoice.id)
        .with_for_update()
    ).scalars().all()
    counts = _zero(True)
    counts["processed"] = len(invoices)
    if not invoices:
        return counts

    window_days, main = settings["date_window_days"], main_currency(db)
    spans = [_span(inv, window_days) for inv in invoices]
    targets = eligible_targets(db, min(s for s, _ in spans), max(e for _, e in spans))
    family = {t.key: t.family for t in targets}
    candidates: dict[int, list[TargetKey]] = {}
    near: dict[int, bool] = {}
    for inv in invoices:
        found, is_near = _candidates(inv, targets, window_days, main)
        candidates[inv.id] = [] if is_near else [t.key for t in found]
        near[inv.id] = is_near and bool(found)
    decisions = decide(candidates, near, family)

    tax_ids = {inv.seller_tax_id for inv in invoices if inv.seller_tax_id}
    muted = set(db.scalars(select(EinvoiceSellerMute.seller_tax_id)
                           .where(EinvoiceSellerMute.seller_tax_id.in_(tax_ids)))) if tax_ids else set()

    for inv in invoices:
        decision, key = decisions[inv.id]
        before = (inv.state, inv.dismiss_reason)
        if decision == "link":
            try:
                with db.begin_nested():
                    _insert_link(db, inv, key)
            except IntegrityError:  # a concurrent ledger write took the target: this invoice only (R6)
                counts["retry_later"] += 1
                continue
            inv.state, inv.dismiss_reason = "linked", None
            if key[0] == "entry":
                stamp(db, key[1], inv.number)
            bucket = "linked"
        elif decision in ("ambiguous", "near"):
            inv.state, inv.dismiss_reason = decision, None
            bucket = decision
        elif inv.seller_tax_id in muted:
            inv.state, inv.dismiss_reason = "dismissed", "seller_muted"
            bucket = "unmatched"
        else:
            inv.state, inv.dismiss_reason = "unmatched", None
            bucket = "unmatched"
        counts["unchanged" if (inv.state, inv.dismiss_reason) == before else bucket] += 1
    db.flush()
    return counts
