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
