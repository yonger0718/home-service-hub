from decimal import Decimal as D

from app.services.reward_math import Slot, allot, estimate


def est(**kw):
    base = dict(method="percent", rate=None, fixed_amount=None, unit_spend=None,
                txn_rounding="keep", amount=D("0"))
    base.update(kw)
    return estimate(**base)


def test_estimate_percent():
    assert est(rate=D("1.2"), amount=D("512")) == D("6.1440")
    assert est(rate=D("1.2"), amount=D("512"), txn_rounding="round") == D("6")


def test_estimate_per_unit_floor():
    assert est(method="per_unit", unit_spend=D("20"), amount=D("1999"), txn_rounding="floor") == D("99")


def test_estimate_fixed():
    assert est(method="fixed", fixed_amount=D("50"), amount=D("10")) == D("50")


def test_estimate_refund_negative_and_prorated_fixed():
    assert est(method="fixed", fixed_amount=D("50"), amount=D("300"), refund_of=D("1200")) == D("-12.5000")
    assert est(rate=D("2"), amount=D("300"), refund_of=D("1200")) == D("-6.0000")


def S(k, e, f=None):
    return Slot(key=k, estimate=D(e), frozen=None if f is None else D(f))


def run(slots, cap=None, ext="0", tr="keep"):
    return allot(slots, cap=None if cap is None else D(cap), external_used=D(ext), total_rounding=tr)


def test_allot_uncapped_passes_estimates():
    assert run([S(1, "5"), S(2, "-2")]) == {1: D("5"), 2: D("-2")}


def test_allot_cap_in_order():
    assert run([S(1, "60"), S(2, "60"), S(3, "60")], cap="100") == {1: D("60"), 2: D("40"), 3: D("0")}


def test_allot_frozen_counts_first():
    assert run([S(1, "0", "80"), S(2, "60")], cap="100") == {2: D("20")}


def test_allot_external_used():
    assert run([S(1, "30")], cap="100", ext="90") == {1: D("10")}


def test_allot_negative_in_full_raises_remaining():
    assert run([S(1, "100"), S(2, "-20"), S(3, "50")], cap="100") == {1: D("100"), 2: D("-20"), 3: D("20")}


def test_allot_total_rounding_delta_on_last_estimated():
    assert run([S(1, "1.4"), S(2, "1.4"), S(3, "1.4")], tr="round") == {1: D("1.4"), 2: D("1.4"), 3: D("1.2")}


def test_allot_total_rounding_never_exceeds_cap():
    out = run([S(1, "6.6"), S(2, "3.3")], cap="10", tr="ceil")
    assert out == {1: D("6.6"), 2: D("3.4")}
    assert sum(out.values()) <= D("10")


def test_allot_no_estimated_no_adjustment():
    assert run([S(1, "0", "5.5")], tr="round") == {}


def test_allot_negative_delta_stops_at_zero():
    assert run([S(1, "0.3"), S(2, "0.3")], tr="floor") == {1: D("0.3"), 2: D("0")}


def test_allot_rounding_clamped_to_cap():
    assert run([S(1, "0", "9.6"), S(2, "0.4")], cap="10", tr="ceil") == {2: D("0.4")}
    slots = [S(1, "0", "9.7"), S(2, "0.2")]
    assert run(slots, cap="10", tr="round") == {2: D("0.3")}
    assert run(slots, cap="9.8", tr="round") == {2: D("0.1")}
