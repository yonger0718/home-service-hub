"""The amount a settlement counts against the debt it settles (design D3). No imports from other service modules."""

from sqlalchemy import case, func


def settled_value(settlement, target_currency):
    """Column expression over a (possibly aliased) LedgerEntry `settlement` and the target's currency (a column or a
    string): its `amount` in the same currency; its `original_amount` when another currency whose
    `original_currency` is the target's; otherwise 0. The branches are in that order, so a same-currency row
    carrying an `original_amount` (a MOZE FX row) counts its `amount`."""
    return case(
        (settlement.currency == target_currency, settlement.amount),
        (settlement.original_currency == target_currency, func.coalesce(settlement.original_amount, 0)),
        else_=0,
    )
