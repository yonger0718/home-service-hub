"""Pure reward math: per-transaction estimates, cap allotment, rounding (spec 5.2, P2/P4)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

_Q = Decimal("0.0001")
_ZERO = Decimal("0")
_MODES = {"round": ROUND_HALF_UP, "floor": ROUND_FLOOR, "ceil": ROUND_CEILING}


def _q(value: Decimal) -> Decimal:
    return value.quantize(_Q, rounding=ROUND_HALF_UP)


def apply_rounding(value: Decimal, mode: str) -> Decimal:
    """`keep` = 4 places; `round` / `floor` / `ceil` act on whole units."""
    if mode == "keep":
        return _q(value)
    if mode not in _MODES:
        raise ValueError(f"unknown rounding mode: {mode}")
    return _q(value.quantize(Decimal("1"), rounding=_MODES[mode]))


def estimate(
    *,
    method: str,
    rate: Decimal | None,
    fixed_amount: Decimal | None,
    unit_spend: Decimal | None,
    txn_rounding: str,
    amount: Decimal,
    refund_of: Decimal | None = None,
) -> Decimal:
    """Reward estimate for one entry; negative (claw-back) when `refund_of` is given."""
    base = abs(amount)
    if method == "percent":
        raw = base * (rate or _ZERO) / Decimal("100")
    elif method == "per_unit":
        raw = base / unit_spend if unit_spend else _ZERO
    elif method == "fixed":
        raw = fixed_amount or _ZERO
        if refund_of:
            raw = raw * base / abs(refund_of)
    else:
        raise ValueError(f"unknown method: {method}")
    # Round the magnitude so a refund gives back exactly what the purchase earned.
    rounded = apply_rounding(raw, txn_rounding)
    return _q(-rounded) if refund_of is not None else rounded


@dataclass(frozen=True)
class Slot:
    key: int
    estimate: Decimal
    frozen: Decimal | None


def allot(
    slots: Sequence[Slot],
    *,
    cap: Decimal | None,
    external_used: Decimal,
    total_rounding: str,
) -> dict[int, Decimal]:
    """Amounts for the estimated slots (frozen is None), slots pre-sorted (spec 5.2.2-5.2.4)."""
    frozen_sum = sum((s.frozen for s in slots if s.frozen is not None), _ZERO)
    remaining = None if cap is None else cap - external_used - frozen_sum
    out: dict[int, Decimal] = {}
    est_slots: list[Slot] = []
    for s in slots:
        if s.frozen is not None:
            continue
        est_slots.append(s)
        if remaining is None:
            out[s.key] = _q(s.estimate)
        elif s.estimate < 0:
            out[s.key] = _q(s.estimate)
            remaining -= s.estimate
        else:
            got = min(s.estimate, max(remaining, _ZERO))
            out[s.key] = _q(got)
            remaining -= got
    if not est_slots or total_rounding == "keep":
        return out
    raw = frozen_sum + sum(out.values(), _ZERO)
    target = apply_rounding(raw, total_rounding)
    if cap is not None:
        target = min(target, cap - external_used)
    last = est_slots[-1]
    new = out[last.key] + (target - raw)
    if last.estimate > 0 and new < 0:
        new = _ZERO
    out[last.key] = _q(new)
    return out
