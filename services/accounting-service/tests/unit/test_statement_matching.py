import dataclasses
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services.statements import matching as m

D = Decimal
R = m.Rules(period_end=date(2026, 9, 30))


def L(i, posted, flow, kind="purchase", merchant="全聯", **kw):
    return m.Line(id=i, event_id=100 + i, posted_date=posted, txn_date=kw.get("txn_date"), flow=D(flow),
                  foreign_amount=None if kw.get("foreign_amount") is None else D(kw["foreign_amount"]), foreign_currency=kw.get("foreign_currency"), line_kind=kind,
                  merchant_norm=merchant, installment_seq=kw.get("seq"), installment_total=kw.get("total"))


def E(i, posted, flow, kind="expense", merchant="全聯", **kw):
    return m.Entry(id=i, kind=kind, flow=D(flow), posted_date=posted, entry_date=kw.get("entry_date", posted),
                   name=kw.get("name"), merchant=merchant, original_amount=None if kw.get("original_amount") is None else D(kw["original_amount"]),
                   original_currency=kw.get("original_currency"), account_id=kw.get("account_id", 1),
                   group_id=kw.get("group_id"), parent_entry_id=kw.get("parent_entry_id"),
                   is_settlement=kw.get("is_settlement", False), refunds_entry_id=kw.get("refunds_entry_id"),
                   transfer_group_id=kw.get("transfer_group_id"), transfer_peer_is_card=kw.get("peer_is_card"),
                   children=tuple(kw.get("children", ())), instance=kw.get("instance"), in_reverse=kw.get("in_reverse", True),
                   claimable=kw.get("claimable", True), stand_in=kw.get("stand_in", False),
                   all_children=tuple(kw.get("all_children", ())), currency=kw.get("currency"))


def S(*a, **kw):
    """A structural row: a split-group member loaded only for the printed-whole guard."""
    return E(*a, claimable=False, in_reverse=False, **kw)


def run(lines, entries, groups=None, plan_map=None, fee=lambda e: None, kind="card", lines_all=None):
    return m.match(kind, lines, entries, groups or {}, plan_map or {}, fee, R, "TWD", lines_all=lines_all)


def test_exact_match_claims_standalone_expense():
    res = run([L(1, date(2026, 9, 3), "-580")], [E(10, date(2026, 9, 3), "-580")])
    assert [(c.line_id, c.representation.rule, [r.entry_id for r in c.representation.rows]) for c in res.claims] == [(1, "exact", [10])]
    assert res.cases == [] and res.unmatched_entry_ids == []


def test_two_identical_candidates_without_merchant_signal_are_ambiguous():
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], [E(10, date(2026, 9, 3), "-580", merchant="A"), E(11, date(2026, 9, 3), "-580", merchant="B")])
    assert res.claims == [] and res.cases[0].kind == "ambiguous" and {c["entry_id"] for c in res.cases[0].candidates} == {10, 11}


def test_merchant_signal_breaks_the_tie():
    res = run([L(1, date(2026, 9, 3), "-580", merchant="全聯 大安")], [E(10, date(2026, 9, 3), "-580", merchant="全聯 大安"), E(11, date(2026, 9, 4), "-580", merchant="家樂福")])
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10]


def test_same_day_same_amount_merchant_text_resolves_on_the_new_scale():
    # R6a.1: text weighs 0.10, the margin is 0.05: on equal dates the better merchant match wins (global competition)
    res = run([L(1, date(2026, 9, 3), "-580", merchant="全聯 大安")], [E(10, date(2026, 9, 3), "-580", merchant="全聯 大安"), E(11, date(2026, 9, 3), "-580", merchant="家樂福")])
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10]


def test_group_claims_every_member_and_blocks_a_second_line():
    g = m.Group(id=5, kind="split", member_ids=(21, 22))
    entries = [E(21, date(2026, 9, 3), "-300", group_id=5), E(22, date(2026, 9, 3), "-280", group_id=5)]
    res = run([L(1, date(2026, 9, 3), "-580"), L(2, date(2026, 9, 3), "-300")], entries, groups={5: g})
    claim = res.claims[0]
    assert claim.representation.rule == "group" and sorted(r.entry_id for r in claim.representation.rows) == [21, 22]
    assert [c.line_id for c in res.cases] == [2] and res.cases[0].kind == "line_unmatched"


def test_group_with_children_is_a_separate_representation():
    g = m.Group(id=5, kind="split", member_ids=(21,))
    fee = m.Child(id=31, kind="fee", flow=D("-2"))
    entries = [E(21, date(2026, 9, 3), "-100", group_id=5, children=(fee,))]
    res = run([L(1, date(2026, 9, 3), "-102")], entries, groups={5: g})
    assert res.claims[0].representation.rule == "group_children" and sorted(r.entry_id for r in res.claims[0].representation.rows) == [21, 31]
    res2 = run([L(1, date(2026, 9, 3), "-100"), L(2, date(2026, 9, 3), "-2", kind="fee")], entries, groups={5: g})
    assert [c.representation.rule for c in res2.claims] == ["group", "exact"]


def test_partial_group_without_its_other_member_fails_closed_and_is_never_admissible():
    # no structural row for 22 either: the guard cannot enumerate the group, so it is whole by fail-closed
    g = m.Group(id=5, kind="split", member_ids=(21, 22))
    entries = [E(21, date(2026, 9, 3), "-300", group_id=5)]  # member 22 not in the candidate window
    res = run([L(1, date(2026, 9, 3), "-300")], entries, groups={5: g})
    assert res.claims == [] and res.cases[0].kind == "line_unmatched"


def test_installment_needs_a_confirmed_plan():
    inst = m.InstanceRef(definition_id=7, seq=2, times=12)
    entries = [E(40, date(2026, 9, 5), "-1000", instance=inst)]
    line = L(1, date(2026, 9, 5), "-1000", kind="installment", merchant="APPLE", seq=2, total=12)
    res = run([line], entries)
    assert res.claims == [] and res.cases[0].context["hint"] == "installment_unmapped" and res.cases[0].candidates == ({"definition_id": 7},)
    res2 = run([line], entries, plan_map={"APPLE|12|1000.0000": 7})
    assert res2.claims[0].representation.rule == "installment"


def test_wrong_plan_with_equal_numbers_stays_a_case():
    entries = [E(40, date(2026, 9, 5), "-1000", instance=m.InstanceRef(definition_id=8, seq=2, times=12))]
    res = run([L(1, date(2026, 9, 5), "-1000", kind="installment", merchant="APPLE", seq=2, total=12)], entries, plan_map={"APPLE|12|1000.0000": 7})
    assert res.claims == [] and res.cases[0].kind == "line_unmatched"


def test_payment_matches_transfer_in_from_a_non_card():
    entries = [E(50, date(2026, 9, 10), "5000", kind="transfer_in", transfer_group_id="t1", peer_is_card=False)]
    res = run([L(1, date(2026, 9, 10), "5000", kind="payment", merchant="繳款")], entries)
    assert res.claims[0].representation.rule == "payment"


def test_orphan_refund_is_eligible():
    entries = [E(60, date(2026, 9, 12), "200", kind="refund", refunds_entry_id=None)]
    res = run([L(1, date(2026, 9, 12), "200", kind="refund")], entries)
    assert res.claims[0].representation.rule == "refund"


def test_refund_line_does_not_claim_an_expense_of_the_same_amount():
    res = run([L(1, date(2026, 9, 12), "200", kind="refund")], [E(61, date(2026, 9, 12), "200", kind="expense")])
    assert res.claims == []


def test_refund_line_claims_a_refund_entry_only():
    entries = [E(60, date(2026, 9, 12), "200", kind="refund", refunds_entry_id=None), E(61, date(2026, 9, 12), "200", kind="expense")]
    res = run([L(1, date(2026, 9, 12), "200", kind="refund")], entries)
    assert [(c.representation.rule, [r.entry_id for r in c.representation.rows]) for c in res.claims] == [("refund", [60])]


def test_foreign_residual_equal_to_fee_is_a_delta_case_not_a_claim():
    entries = [E(70, date(2026, 9, 8), "-660", original_amount=D("-3000"), original_currency="JPY")]
    res = run([L(1, date(2026, 9, 8), "-670", foreign_amount=D("-3000"), foreign_currency="JPY")], entries, fee=lambda e: D("10"))
    case = res.cases[0]
    assert res.claims == [] and case.kind == "amount_delta" and case.context["fee_matches"] is True and case.context["residual"] == "-10.0000"


@pytest.mark.parametrize("printed", ["-3000", "3000"])  # parsers print the foreign amount unsigned on most debit lines
def test_foreign_amounts_are_compared_by_magnitude(printed):
    entries = [E(70, date(2026, 9, 8), "-660", original_amount=D("-3000"), original_currency="JPY")]
    res = run([L(1, date(2026, 9, 8), "-670", foreign_amount=D(printed), foreign_currency="JPY")], entries, fee=lambda e: D("10"))
    assert res.cases[0].kind == "amount_delta" and res.cases[0].candidates[0]["reason"] == "foreign"


def test_foreign_group_amounts_are_compared_by_magnitude():
    g, members = _jpy_group()
    res = run([L(1, date(2026, 9, 8), "-2010", foreign_amount=D("9000"), foreign_currency="JPY")], members, groups={5: g})
    assert res.cases[0].kind == "amount_delta" and res.cases[0].context["group_id"] == 5


def test_foreign_refund_printed_negative_matches_a_refund_entry_with_a_positive_original():
    refund = E(75, date(2026, 9, 8), "640", kind="refund", original_amount=D("3000"), original_currency="JPY")
    res = run([L(1, date(2026, 9, 8), "660", kind="refund", foreign_amount=D("-3000"), foreign_currency="JPY")], [refund])
    assert res.cases[0].kind == "amount_delta" and res.cases[0].candidates[0] == {"entry_id": 75, "reason": "foreign"}


def test_foreign_magnitude_never_crosses_kinds():
    # the gate keeps the direction: a purchase line never takes a refund entry of the same magnitude, nor the reverse
    expense = E(70, date(2026, 9, 8), "-660", original_amount=D("-3000"), original_currency="JPY")
    refund = E(75, date(2026, 9, 8), "640", kind="refund", original_amount=D("3000"), original_currency="JPY")
    purchase = run([L(1, date(2026, 9, 8), "-670", foreign_amount=D("3000"), foreign_currency="JPY")], [refund])
    assert purchase.cases[0].kind == "line_unmatched"
    back = run([L(1, date(2026, 9, 8), "660", kind="refund", foreign_amount=D("3000"), foreign_currency="JPY")], [expense])
    assert back.cases[0].kind == "line_unmatched"


def test_a_zero_foreign_amount_is_never_a_foreign_candidate():
    zero_entry = E(70, date(2026, 9, 8), "-400", original_amount=D("0"), original_currency="JPY")  # not a near amount either
    res = run([L(1, date(2026, 9, 8), "-670", foreign_amount=D("0"), foreign_currency="JPY")], [zero_entry])
    assert res.cases[0].kind == "line_unmatched"
    # each guard alone (AGENT-169): `_same` quantizes to four decimals, so a 0.00001 on the other side would compare
    # equal to zero and only the guard under test stands between the pair and a foreign case
    only_line_guard = run([L(1, date(2026, 9, 8), "-670", foreign_amount=D("0"), foreign_currency="JPY")],
                          [E(70, date(2026, 9, 8), "-400", original_amount=D("-0.00001"), original_currency="JPY")])
    assert only_line_guard.cases[0].kind == "line_unmatched"
    only_single_guard = run([L(1, date(2026, 9, 8), "-670", foreign_amount=D("0.00001"), foreign_currency="JPY")], [zero_entry])
    assert only_single_guard.cases[0].kind == "line_unmatched"
    g, members = _jpy_group(originals=("0", "0"))
    res2 = run([L(1, date(2026, 9, 8), "-2010", foreign_amount=D("0"), foreign_currency="JPY")], members, groups={5: g})
    assert res2.cases[0].kind == "line_unmatched"
    # a zero-original member keeps its group out of the foreign reading even when the line prints an amount
    g3, members3 = _jpy_group(originals=("-9000", "0"))
    res3 = run([L(1, date(2026, 9, 8), "-2010", foreign_amount=D("9000"), foreign_currency="JPY")], members3, groups={5: g3})
    assert res3.cases[0].kind == "line_unmatched"


def test_foreign_residual_not_equal_to_fee():
    entries = [E(70, date(2026, 9, 8), "-660", original_amount=D("-3000"), original_currency="JPY")]
    res = run([L(1, date(2026, 9, 8), "-690", foreign_amount=D("-3000"), foreign_currency="JPY")], entries, fee=lambda e: D("10"))
    assert res.cases[0].kind == "amount_delta" and res.cases[0].context["fee_matches"] is False


def test_near_match_is_always_a_case():
    res = run([L(1, date(2026, 9, 3), "-585", merchant="全聯 大安")], [E(10, date(2026, 9, 3), "-580", merchant="全聯 大安")])
    assert res.claims == [] and res.cases[0].kind == "amount_delta" and res.cases[0].context["residual"] == "-5.0000"


def test_bank_only_line_is_hinted():
    res = run([L(1, date(2026, 9, 30), "-5", kind="fee", merchant="跨行手續費")], [])
    assert res.cases[0].kind == "line_unmatched" and res.cases[0].context["hint"] == "bank_only"


def test_bank_only_by_line_kind_without_any_pattern():
    res = run([L(1, date(2026, 9, 30), "12", kind="interest", merchant="INTEREST CREDIT")], [], kind="bank")
    assert res.cases[0].kind == "line_unmatched" and res.cases[0].context == {"hint": "bank_only", "pattern": None}


def test_bank_only_keeps_the_matched_pattern_as_annotation():
    res = run([L(1, date(2026, 9, 30), "-5", kind="fee", merchant="跨行手續費")], [])
    assert res.cases[0].context == {"hint": "bank_only", "pattern": "跨行手續費"}


def test_unknown_deposit_withdrawal_lines_are_never_bank_only():
    for kind in ("unknown", "deposit", "withdrawal"):
        res = run([L(1, date(2026, 9, 30), "12", kind=kind, merchant="利息")], [], kind="bank")
        assert "hint" not in res.cases[0].context, kind


def test_reverse_population_and_deferral():
    entries = [E(10, date(2026, 9, 3), "-580"), E(11, date(2026, 9, 29), "-90")]
    res = run([L(1, date(2026, 9, 3), "-580")], entries)
    assert res.unmatched_entry_ids == [11] and res.explained == {11: "deferred_next_period"}


def test_bank_deposit_matches_income_and_settlement_inflow():
    entries = [E(10, date(2026, 9, 5), "30000", kind="income"), E(11, date(2026, 9, 6), "500", kind="receivable", is_settlement=True)]
    res = run([L(1, date(2026, 9, 5), "30000", kind="deposit"), L(2, date(2026, 9, 6), "500", kind="deposit")], entries, kind="bank")
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10, 11]


def test_claims_are_conserved():
    g = m.Group(id=5, kind="split", member_ids=(21, 22))
    entries = [E(21, date(2026, 9, 3), "-300", group_id=5), E(22, date(2026, 9, 3), "-281", group_id=5)]
    res = run([L(1, date(2026, 9, 3), "-580")], entries, groups={5: g})
    assert res.claims == [] and res.cases[0].kind == "line_unmatched"


# --- fix round 1 -------------------------------------------------------------

def test_bank_withdrawal_respects_a_split_group_printed_whole_and_takes_a_member_printed_apart():
    # 2026-10-24: a lone −300 withdrawal no longer stays open — with no line near the −580 total the group is printed
    # apart and its member stands alone; with the −580 line present the group is whole and the −300 line stays open
    g = m.Group(id=5, kind="split", member_ids=(21, 22))
    entries = [E(21, date(2026, 9, 3), "-300", group_id=5), E(22, date(2026, 9, 3), "-280", group_id=5)]
    whole = run([L(1, date(2026, 9, 3), "-300", kind="withdrawal"), L(2, date(2026, 9, 3), "-580", kind="withdrawal")],
                entries, groups={5: g}, kind="bank")
    assert [(c.line_id, c.representation.rule, sorted(r.entry_id for r in c.representation.rows)) for c in whole.claims] == [(2, "group", [21, 22])]
    assert [(c.line_id, c.kind) for c in whole.cases] == [(1, "line_unmatched")]
    apart = run([L(1, date(2026, 9, 3), "-300", kind="withdrawal")], entries, groups={5: g}, kind="bank")
    assert [(c.representation.kind, [r.entry_id for r in c.representation.rows]) for c in apart.claims] == [("member_alone", [21])]
    assert apart.unmatched_entry_ids == [22]


def test_bank_withdrawal_never_claims_an_installment_member():
    entries = [E(40, date(2026, 9, 5), "-1000", instance=m.InstanceRef(definition_id=7, seq=2, times=12))]
    res = run([L(1, date(2026, 9, 5), "-1000", kind="withdrawal")], entries, kind="bank")
    assert res.claims == [] and res.cases[0].kind == "line_unmatched"


def _installment_run(entry_day):
    entries = [E(40, date(2026, 9, entry_day), "-1000", instance=m.InstanceRef(definition_id=7, seq=2, times=12))]
    line = L(1, date(2026, 9, 17), "-1000", kind="installment", merchant="APPLE", seq=2, total=12)
    return run([line], entries, plan_map={"APPLE|12|1000.0000": 7})


def test_installment_claims_within_the_candidate_window():
    for day in (16, 8):  # 1-day and 9-day drift
        res = _installment_run(day)
        assert [c.representation.rule for c in res.claims] == ["installment"], day


def test_installment_beyond_the_candidate_window_is_a_drift_case():
    res = _installment_run(5)  # 12-day drift
    assert res.claims == [] and res.cases[0].kind == "line_unmatched"
    assert res.cases[0].context == {"hint": "installment_date_drift", "entry_id": 40, "definition_id": 7}


def test_principal_with_children_is_a_representation():
    fee = m.Child(id=31, kind="fee", flow=D("-2"))
    res = run([L(1, date(2026, 9, 3), "-102")], [E(21, date(2026, 9, 3), "-100", children=(fee,))])
    claim = res.claims[0].representation
    assert claim.rule == "exact_children" and sorted(r.entry_id for r in claim.rows) == [21, 31]


def test_foreign_line_closes_once_the_fee_child_exists():
    fee = m.Child(id=71, kind="fee", flow=D("-10"))
    entries = [E(70, date(2026, 9, 8), "-660", original_amount=D("-3000"), original_currency="JPY", children=(fee,))]
    res = run([L(1, date(2026, 9, 8), "-670", foreign_amount=D("-3000"), foreign_currency="JPY")], entries, fee=lambda e: D("10"))
    assert res.cases == [] and res.claims[0].representation.rule == "exact_children"


def test_foreign_twins_are_ambiguous():
    entries = [E(70, date(2026, 9, 8), "-660", original_amount=D("-3000"), original_currency="JPY"),
               E(72, date(2026, 9, 9), "-661", original_amount=D("-3000"), original_currency="JPY")]
    res = run([L(1, date(2026, 9, 8), "-670", foreign_amount=D("-3000"), foreign_currency="JPY")], entries, fee=lambda e: D("10"))
    case = res.cases[0]
    assert res.claims == [] and case.kind == "ambiguous" and "entry_id" not in case.context
    assert sorted(c["entry_id"] for c in case.candidates) == [70, 72] and {c["reason"] for c in case.candidates} == {"foreign"}


def _jpy_group(gid=5, *, flows=("-660", "-1320"), originals=("-3000", "-6000"), day=date(2026, 9, 8), kinds=("expense", "receivable")):
    members = [E(70 + i, day, flow, kind=kind, original_amount=D(orig), original_currency="JPY", group_id=gid)
               for i, (flow, orig, kind) in enumerate(zip(flows, originals, kinds))]
    return m.Group(id=gid, kind="split", member_ids=tuple(e.id for e in members)), members


def test_foreign_split_group_whose_originals_sum_to_the_line_is_a_delta_case():
    # the owner split a 9000 JPY purchase into an expense and a receivable; the TWD estimate misses the FX fee
    g, members = _jpy_group()
    res = run([L(1, date(2026, 9, 8), "-2010", foreign_amount=D("-9000"), foreign_currency="JPY")], members, groups={5: g},
              fee=lambda e: D("30"))
    (case,) = res.cases[:1]
    assert res.claims == [] and case.kind == "amount_delta" and case.entry_id is None
    assert case.candidates == ({"group_id": 5, "reason": "foreign", "residual": "-30.0000"},)
    assert case.context == {"residual": "-30.0000", "fee_expected": "30", "fee_matches": True, "group_id": 5, "entry_ids": [70, 71]}
    assert "entry_id" not in case.context


def test_foreign_group_members_are_designated_and_never_deferred():
    g, members = _jpy_group(day=date(2026, 9, 30))  # inside deferral_days of the period end
    res = run([L(1, date(2026, 9, 30), "-2010", foreign_amount=D("-9000"), foreign_currency="JPY")], members, groups={5: g})
    assert res.cases[0].kind == "amount_delta" and res.unmatched_entry_ids == [70, 71] and res.explained == {}


def test_foreign_group_with_a_member_in_another_currency_is_not_a_candidate():
    g, members = _jpy_group()
    members[1] = dataclasses.replace(members[1], original_amount=None, original_currency=None)
    res = run([L(1, date(2026, 9, 8), "-2010", foreign_amount=D("-9000"), foreign_currency="JPY")], members, groups={5: g})
    assert res.cases[0].kind == "line_unmatched"


def test_foreign_group_with_a_claimed_or_missing_member_is_not_admissible():
    g, members = _jpy_group()
    # the group is claimed exactly by an earlier line (its TWD sum): the foreign line cannot name it again
    lines = [L(1, date(2026, 9, 8), "-1980"), L(2, date(2026, 9, 8), "-2010", foreign_amount=D("-9000"), foreign_currency="JPY")]
    res = run(lines, members, groups={5: g})
    assert [(c.line_id, c.representation.rule) for c in res.claims] == [(1, "group")]
    assert [(c.line_id, c.kind) for c in res.cases] == [(2, "line_unmatched")]
    # a member outside the population makes the group partial
    res2 = run([L(1, date(2026, 9, 8), "-2010", foreign_amount=D("-9000"), foreign_currency="JPY")], members[:1], groups={5: g})
    assert res2.cases[0].kind == "line_unmatched"


def test_foreign_group_sum_mismatch_or_window_is_not_a_candidate():
    g, members = _jpy_group(originals=("-3000", "-5000"))
    res = run([L(1, date(2026, 9, 8), "-2010", foreign_amount=D("-9000"), foreign_currency="JPY")], members, groups={5: g})
    assert res.cases[0].kind == "line_unmatched"
    g2, members2 = _jpy_group(day=date(2026, 9, 2))  # 6 days > foreign_window_days (5)
    res2 = run([L(1, date(2026, 9, 8), "-2010", foreign_amount=D("-9000"), foreign_currency="JPY")], members2, groups={5: g2})
    assert res2.cases[0].kind == "line_unmatched"


def test_foreign_group_and_single_twins_are_ambiguous():
    g, members = _jpy_group()
    single = E(80, date(2026, 9, 9), "-1980", original_amount=D("-9000"), original_currency="JPY")
    res = run([L(1, date(2026, 9, 8), "-2010", foreign_amount=D("-9000"), foreign_currency="JPY")], members + [single], groups={5: g})
    case = res.cases[0]
    assert case.kind == "ambiguous" and case.entry_id is None
    assert [(c.get("entry_id"), c.get("group_id"), c["reason"]) for c in case.candidates] == [(80, None, "foreign"), (None, 5, "foreign")]


def test_foreign_group_is_only_a_candidate_where_expense_is_eligible():
    g, members = _jpy_group()
    res = run([L(1, date(2026, 9, 8), "-2010", kind="fee", foreign_amount=D("-9000"), foreign_currency="JPY")], members, groups={5: g})
    assert res.cases[0].kind == "line_unmatched"


def _fee_group(gid=6, *, day=date(2026, 9, 3)):
    members = [E(21, day, "-300", group_id=gid), E(22, day, "-280", kind="receivable", group_id=gid),
               E(23, day, "-10", kind="fee", group_id=gid)]
    return m.Group(id=gid, kind="split", member_ids=(21, 22, 23)), members


@pytest.mark.parametrize("fee_first", [False, True])
def test_a_fee_member_printed_as_its_own_line_leaves_the_group_representation_and_is_claimed_alone(fee_first):
    # a MOZE split {expense, receivable, fee}: the statement prints the purchase (expense + receivable) and the fee;
    # `match` walks lines by id, so the fee line gets the lower id to be processed first
    g, members = _fee_group()
    purchase_id, fee_id = (2, 1) if fee_first else (1, 2)
    purchase, fee = L(purchase_id, date(2026, 9, 3), "-580"), L(fee_id, date(2026, 9, 3), "-10", kind="fee")
    res = run([purchase, fee], members, groups={6: g})
    claims = {c.line_id: (c.representation.rule, sorted(r.entry_id for r in c.representation.rows)) for c in res.claims}
    assert claims == {purchase_id: ("group", [21, 22]), fee_id: ("exact", [23])} and res.cases == [] and res.unmatched_entry_ids == []


def test_a_fee_member_the_statement_does_not_print_alone_stays_in_the_net_reading():
    g, members = _fee_group()
    res = run([L(1, date(2026, 9, 3), "-580")], members, groups={6: g})  # no fee line printed (or printed at another amount)
    assert res.claims == [] and res.cases[0].kind == "line_unmatched"
    res2 = run([L(1, date(2026, 9, 3), "-590")], members, groups={6: g})  # the net reading still conserves
    assert [sorted(r.entry_id for r in c.representation.rows) for c in res2.claims] == [[21, 22, 23]]


@pytest.mark.parametrize("other_kind, other_flow", [("discount", "10"), ("income", "10")])
def test_a_net_group_reading_is_kept_for_members_without_a_line_of_their_own(other_kind, other_flow):
    # {expense -100, discount +10}: the statement prints the net -90; a gross -100 line must not take the expense alone
    g = m.Group(id=7, kind="split", member_ids=(31, 32))
    members = [E(31, date(2026, 9, 3), "-100", group_id=7), E(32, date(2026, 9, 3), other_flow, kind=other_kind, group_id=7)]
    net = run([L(1, date(2026, 9, 3), "-90")], members, groups={7: g})
    assert [(c.representation.rule, sorted(r.entry_id for r in c.representation.rows)) for c in net.claims] == [("group", [31, 32])]
    gross = run([L(1, date(2026, 9, 3), "-100")], members, groups={7: g})
    assert gross.claims == [] and gross.cases[0].kind == "line_unmatched"


def test_a_member_is_printed_alone_only_by_a_fee_line_inside_the_exact_window():
    g = m.Group(id=7, kind="split", member_ids=(31, 32))
    members = [E(31, date(2026, 9, 3), "-100", group_id=7), E(32, date(2026, 9, 3), "-10", kind="fee", group_id=7)]
    far = run([L(1, date(2026, 9, 3), "-100"), L(2, date(2026, 9, 20), "-10", kind="fee")], members, groups={7: g})
    assert far.claims == [] and {c.kind for c in far.cases} == {"line_unmatched"}  # the Sep 20 fee line is not the member's
    near = run([L(1, date(2026, 9, 3), "-100"), L(2, date(2026, 9, 5), "-10", kind="fee")], members, groups={7: g})
    assert {c.line_id: sorted(r.entry_id for r in c.representation.rows) for c in near.claims} == {1: [31], 2: [32]}


def test_one_printed_fee_line_excuses_one_fee_member():
    g = m.Group(id=7, kind="split", member_ids=(31, 32, 33))
    members = [E(31, date(2026, 9, 3), "-100", group_id=7), E(32, date(2026, 9, 3), "-10", kind="fee", group_id=7),
               E(33, date(2026, 9, 3), "-10", kind="fee", group_id=7)]
    res = run([L(1, date(2026, 9, 3), "-110"), L(2, date(2026, 9, 3), "-10", kind="fee")], members, groups={7: g})
    # the −110 reading keeps one fee member; which one is unresolved, like the fee line's own choice: both ambiguous
    assert res.claims == [] and [(c.line_id, c.kind) for c in res.cases] == [(1, "ambiguous"), (2, "ambiguous")]
    assert sorted(c["group_id"] for c in res.cases[0].candidates) == [7, 7]


def test_zero_sum_members_printed_alone_go_to_their_own_lines():
    g = m.Group(id=7, kind="split", member_ids=(31, 32, 33))
    members = [E(31, date(2026, 9, 3), "-100", group_id=7), E(32, date(2026, 9, 3), "-10", kind="fee", group_id=7),
               E(33, date(2026, 9, 3), "10", kind="reward", group_id=7)]
    lines = [L(1, date(2026, 9, 3), "-100"), L(2, date(2026, 9, 3), "-10", kind="fee"), L(3, date(2026, 9, 3), "10", kind="reward")]
    res = run(lines, members, groups={7: g})
    assert {c.line_id: sorted(r.entry_id for r in c.representation.rows) for c in res.claims} == {1: [31], 2: [32], 3: [33]}
    assert res.cases == [] and res.unmatched_entry_ids == []


def test_non_nested_readings_of_equal_sum_compete_as_ambiguous():
    # {expense −100, fee −10} and {expense, fee −6, fee −4} both read −110: neither contains the other → the owner decides
    g = m.Group(id=7, kind="split", member_ids=(31, 32, 33, 34))
    members = [E(31, date(2026, 9, 3), "-100", group_id=7), E(32, date(2026, 9, 3), "-10", kind="fee", group_id=7),
               E(33, date(2026, 9, 3), "-6", kind="fee", group_id=7), E(34, date(2026, 9, 3), "-4", kind="fee", group_id=7)]
    lines = [L(1, date(2026, 9, 3), "-110")] + [L(i, date(2026, 9, 3), f, kind="fee") for i, f in ((2, "-10"), (3, "-6"), (4, "-4"))]
    res = run(lines, members, groups={7: g})
    assert {c.line_id: sorted(r.entry_id for r in c.representation.rows) for c in res.claims} == {2: [32], 3: [33], 4: [34]}
    (case,) = res.cases
    assert (case.line_id, case.kind) == (1, "ambiguous") and len(case.candidates) == 2


def test_a_group_with_more_printed_alone_members_than_enumerated_blocks_the_auto_claim():
    fees = [E(40 + i, date(2026, 9, 3), f"-{i}", kind="fee", group_id=7) for i in range(1, 10)]  # 9 > ASIDE_COMBINATIONS_MAX
    g = m.Group(id=7, kind="split", member_ids=(31, *[e.id for e in fees]))
    members = [E(31, date(2026, 9, 3), "-100", group_id=7)] + fees
    standalone = E(90, date(2026, 9, 3), "-110")
    lines = [L(1, date(2026, 9, 3), "-110")] + [L(1 + i, date(2026, 9, 3), f"-{i}", kind="fee") for i in range(1, 10)]
    res = run(lines, members + [standalone], groups={7: g})
    case = next(c for c in res.cases if c.line_id == 1)
    assert case.kind == "ambiguous" and case.context == {"hint": "group_readings_incomplete", "group_ids": [7]}
    assert all(c.line_id != 1 for c in res.claims) and 90 in res.unmatched_entry_ids


def test_lone_member_is_split_only_and_only_for_fee_interest_reward_refund_lines():
    inst = m.Group(id=8, kind="installment", member_ids=(41,))
    res = run([L(1, date(2026, 9, 3), "-10", kind="fee")], [E(41, date(2026, 9, 3), "-10", kind="fee", group_id=8)], groups={8: inst})
    assert res.claims == [] and res.cases[0].kind == "line_unmatched"
    g = m.Group(id=9, kind="split", member_ids=(51, 52))
    members = [E(51, date(2026, 9, 3), "500", kind="income", group_id=9), E(52, date(2026, 9, 3), "-20", kind="fee", group_id=9)]
    deposit = run([L(1, date(2026, 9, 3), "500", kind="deposit")], members, groups={9: g}, kind="bank")
    # the 500 deposit is within the near tolerance (25) of the +480 total: the group is printed whole, so the income
    # member never stands alone (with no such line the group would be apart and the member claimable)
    assert deposit.claims == [] and deposit.cases[0].kind == "line_unmatched"
    fee = run([L(1, date(2026, 9, 3), "-20", kind="fee")], members, groups={9: g}, kind="bank")
    assert [sorted(r.entry_id for r in c.representation.rows) for c in fee.claims] == [[52]]


def test_group_in_gate_members_must_all_be_present_and_unclaimed():
    g, members = _fee_group()
    res = run([L(1, date(2026, 9, 3), "-580")], [members[0], members[2]], groups={6: g})  # the receivable is missing
    assert res.claims == [] and res.cases[0].kind == "line_unmatched"


def test_foreign_group_leaves_a_fee_member_to_the_fee_line():
    g, members = _jpy_group()
    fee = E(72, date(2026, 9, 8), "-33", kind="fee", original_amount=D("-150"), original_currency="JPY", group_id=5)
    g = m.Group(id=5, kind="split", member_ids=(70, 71, 72))
    lines = [L(1, date(2026, 9, 8), "-2010", foreign_amount=D("-9000"), foreign_currency="JPY"),
             L(2, date(2026, 9, 8), "-33", kind="fee", foreign_amount=D("-150"), foreign_currency="JPY")]
    res = run(lines, members + [fee], groups={5: g})
    assert [(c.line_id, c.representation.rule, [r.entry_id for r in c.representation.rows]) for c in res.claims] == [(2, "exact", [72])]
    (case,) = res.cases
    assert (case.line_id, case.kind, case.context["entry_ids"]) == (1, "amount_delta", [70, 71])
    # the fee printed at another amount is not the member's own line: the group counts whole and fails closed
    res2 = run([lines[0], L(2, date(2026, 9, 8), "-35", kind="fee")], members + [fee], groups={5: g})
    assert res2.claims == [] and {c.kind for c in res2.cases} == {"line_unmatched"}


def test_foreign_fee_is_quantised_and_compared_exactly():
    entries = [E(70, date(2026, 9, 8), "-660", original_amount=D("-3000"), original_currency="JPY")]
    res = run([L(1, date(2026, 9, 8), "-670", foreign_amount=D("-3000"), foreign_currency="JPY")], entries, fee=lambda e: D("10.5"))
    assert res.cases[0].context["fee_expected"] == "11" and res.cases[0].context["fee_matches"] is False
    res2 = run([L(1, date(2026, 9, 8), "-670", foreign_amount=D("-3000"), foreign_currency="JPY")], entries, fee=lambda e: D("10.4"))
    assert res2.cases[0].context["fee_matches"] is True


def test_members_only_claim_reports_the_uncovered_child():
    g = m.Group(id=5, kind="split", member_ids=(21,))
    fee = m.Child(id=31, kind="fee", flow=D("-2"))
    res = run([L(1, date(2026, 9, 3), "-100")], [E(21, date(2026, 9, 3), "-100", group_id=5, children=(fee,))], groups={5: g})
    assert res.claims[0].representation.rule == "group"
    assert res.unmatched_entry_ids == [31] and res.explained == {31: "uncovered_child"}


def test_children_of_unclaimed_parents_are_represented_by_the_parent():
    fee = m.Child(id=31, kind="fee", flow=D("-2"))
    res = run([], [E(21, date(2026, 9, 3), "-100", children=(fee,))])
    assert res.unmatched_entry_ids == [21] and res.explained == {}


def test_child_passed_as_an_entry_row_is_not_duplicated():
    fee = m.Child(id=31, kind="fee", flow=D("-2"))
    entries = [E(21, date(2026, 9, 3), "-100", children=(fee,)), E(31, date(2026, 9, 3), "-2", kind="fee", parent_entry_id=21)]
    res = run([L(1, date(2026, 9, 3), "-2", kind="fee")], entries)
    assert [(c.representation.rule, [r.entry_id for r in c.representation.rows]) for c in res.claims] == [("exact", [31])]


def test_near_prefers_smaller_delta_then_date_and_lists_every_candidate():
    entries = [E(12, date(2026, 9, 6), "-580", merchant="全聯 大安"), E(11, date(2026, 9, 4), "-590", merchant="全聯 大安"),
               E(10, date(2026, 9, 3), "-580", merchant="全聯 大安")]
    res = run([L(1, date(2026, 9, 3), "-585", merchant="全聯 大安")], entries)
    case = res.cases[0]
    assert case.kind == "amount_delta" and case.entry_id == 10 and case.context["entry_id"] == 10
    assert [c["entry_id"] for c in case.candidates] == [10, 11, 12]


def test_installment_unmapped_candidates_are_deduplicated_and_skip_claimed_rows():
    entries = [E(40, date(2026, 9, 5), "-1000", instance=m.InstanceRef(definition_id=7, seq=2, times=12)),
               E(41, date(2026, 9, 5), "-1000", instance=m.InstanceRef(definition_id=8, seq=3, times=12)),
               E(42, date(2026, 9, 6), "-1000", instance=m.InstanceRef(definition_id=8, seq=4, times=12))]
    lines = [L(1, date(2026, 9, 5), "-1000", kind="installment", merchant="APPLE", seq=2, total=12),
             L(2, date(2026, 9, 5), "-1000", kind="installment", merchant="APPLE STORE", seq=3, total=12)]
    res = run(lines, entries, plan_map={"APPLE|12|1000.0000": 7})
    assert [c.line_id for c in res.claims] == [1]
    assert res.cases[0].context["hint"] == "installment_unmapped" and res.cases[0].candidates == ({"definition_id": 8},)


def test_second_line_on_an_already_claimed_entry_is_hinted():
    res = run([L(1, date(2026, 9, 3), "-580"), L(2, date(2026, 9, 3), "-580")], [E(10, date(2026, 9, 3), "-580")])
    assert [c.line_id for c in res.claims] == [1]
    assert res.cases[0].kind == "line_unmatched" and res.cases[0].context == {"hint": "claimed_by_earlier_line", "entry_id": 10}


def test_card_to_card_transfer_leg_is_never_a_near_or_foreign_candidate_for_a_payment():
    # the standalone path's card-payment peer check applies to the near and foreign cases too
    line = L(1, date(2026, 9, 10), "5000", kind="payment", merchant="繳款")
    for peer in (True, None):
        near = run([line], [E(80, date(2026, 9, 10), "4990", kind="transfer_in", merchant="繳款", peer_is_card=peer)])
        assert [c.kind for c in near.cases] == ["line_unmatched"], peer
    control = run([line], [E(80, date(2026, 9, 10), "4990", kind="transfer_in", merchant="繳款", peer_is_card=False)])
    assert [c.kind for c in control.cases] == ["amount_delta"]
    foreign_line = L(1, date(2026, 9, 10), "5000", kind="payment", merchant="繳款", foreign_amount=D("160"),
                     foreign_currency="USD")
    leg = dict(kind="transfer_in", merchant="其他", original_amount=D("160"), original_currency="USD")
    foreign = run([foreign_line], [E(81, date(2026, 9, 10), "4990", peer_is_card=True, **leg)])
    assert [c.kind for c in foreign.cases] == ["line_unmatched"]
    control = run([foreign_line], [E(81, date(2026, 9, 10), "4990", peer_is_card=False, **leg)])
    assert [c.kind for c in control.cases] == ["amount_delta"]


def test_non_claimable_entries_only_feed_the_installment_drift_hint():
    exact = run([L(1, date(2026, 9, 3), "-580")], [dataclasses.replace(E(10, date(2026, 9, 3), "-580"), claimable=False)])
    assert exact.claims == [] and [c.kind for c in exact.cases] == ["line_unmatched"] and exact.unmatched_entry_ids == []
    inst = m.InstanceRef(definition_id=7, seq=2, times=12)
    late = dataclasses.replace(E(40, date(2026, 9, 6), "-1000", instance=inst), claimable=False)  # even in window
    res = run([L(1, date(2026, 9, 6), "-1000", kind="installment", merchant="APPLE", seq=2, total=12)], [late],
              plan_map={"APPLE|12|1000.0000": 7})
    assert res.claims == [] and res.cases[0].context == {"hint": "installment_date_drift", "entry_id": 40, "definition_id": 7}


SEP3 = date(2026, 9, 3)
INST = lambda kind, did=8: m.InstanceRef(definition_id=did, seq=3, times=12 if kind == "installment" else None, definition_kind=kind)


def _loan(principal_flow="-10000", interest_flow="-500", **kw):
    g = m.Group(id=99, kind="installment", member_ids=(10, 11))
    entries = [E(10, SEP3, principal_flow, kind="payable", group_id=99, is_settlement=True, instance=INST("installment")),
               E(11, SEP3, interest_flow, kind="interest", group_id=99, instance=INST("installment"), **kw)]
    return entries, {99: g}


def test_bank_payment_line_never_claims_an_unrelated_expense():   # AGENT-101 M5
    res = run([L(1, SEP3, "-5000", kind="payment", merchant="")], [E(10, SEP3, "-5000", kind="expense", merchant="家樂福")], kind="bank")
    assert not res.claims and res.cases[0].kind == "line_unmatched"


def test_installment_group_members_of_different_definitions_are_not_admissible():
    entries, groups = _loan()
    entries[1] = dataclasses.replace(entries[1], instance=INST("installment", did=9))
    assert not run([L(1, SEP3, "-10500", kind="payment")], entries, groups=groups, kind="bank").claims


def test_installment_group_with_a_member_outside_the_population_is_partial():
    entries, groups = _loan()
    assert not run([L(1, SEP3, "-10500", kind="payment")], entries[:1], groups=groups, kind="bank").claims


def test_bank_payment_line_claims_settlement_out():
    res = run([L(1, SEP3, "-5000", kind="payment")], [E(10, SEP3, "-5000", kind="payable", is_settlement=True)], kind="bank")
    assert [c.representation.rule for c in res.claims] == ["payment"]


def test_bank_purchase_line_claims_expense():
    res = run([L(1, SEP3, "-120", kind="purchase")], [E(10, SEP3, "-120", kind="expense")], kind="bank")
    assert [r.entry_id for r in res.claims[0].representation.rows] == [10]


def test_recurring_instance_is_standalone_but_installment_instance_is_not():
    line = L(1, SEP3, "3000", kind="transfer_in")
    assert run([line], [E(10, SEP3, "3000", kind="transfer_in", instance=INST("recurring"))], kind="bank").claims
    assert not run([line], [E(11, SEP3, "3000", kind="transfer_in", instance=INST("installment"))], kind="bank").claims


def test_bank_payment_line_claims_a_complete_installment_group():
    entries, groups = _loan()
    res = run([L(1, SEP3, "-10500", kind="payment")], entries, groups=groups, kind="bank")
    assert res.claims and res.claims[0].representation.rule == "installment_group"
    assert sorted(r.entry_id for r in res.claims[0].representation.rows) == [10, 11]


def test_partial_installment_group_is_not_admissible():
    entries, groups = _loan(claimable=False)   # the interest member is claimed elsewhere
    res = run([L(1, SEP3, "-10500", kind="payment")], entries, groups=groups, kind="bank")
    assert not res.claims and res.cases[0].kind == "line_unmatched"


def test_card_purchase_never_uses_installment_group():
    entries, groups = _loan()
    assert not run([L(1, SEP3, "-10500", kind="purchase")], entries, groups=groups, kind="card").claims


def test_installment_group_sum_mismatch_is_not_admissible():
    entries, groups = _loan()
    res = run([L(1, SEP3, "-10000", kind="payment")], entries, groups=groups, kind="bank")
    assert not res.claims   # the group sums to -10500; a lone member is never admissible either


def test_exact_amount_three_days_apart_claims_without_merchant_text():
    # R6a.1: the configured 3-day window must work; under the old formula distance 3 scored 0.675 < accept
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], [E(10, date(2026, 9, 6), "-580", merchant="")])
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10]
    assert res.cases == []


def test_exact_amount_two_days_apart_claims_without_merchant_text():
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], [E(10, date(2026, 9, 5), "-580", merchant="")])
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10]


def test_exact_amount_four_days_apart_is_not_a_representation():
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], [E(10, date(2026, 9, 7), "-580", merchant="")])
    assert res.claims == [] and res.cases[0].line_id == 1 and res.cases[0].kind != "ambiguous"


def test_score_orders_by_date_then_text_on_the_new_scale():
    score0, _ = m._score(L(1, date(2026, 9, 3), "-580", merchant=""), (E(10, date(2026, 9, 3), "-580", merchant=""),), 3)
    score3, _ = m._score(L(1, date(2026, 9, 3), "-580", merchant=""), (E(10, date(2026, 9, 6), "-580", merchant=""),), 3)
    score_text, _ = m._score(L(1, date(2026, 9, 3), "-580", merchant="全聯 大安"), (E(10, date(2026, 9, 3), "-580", merchant="全聯 大安"),), 3)
    assert (score0, score3, score_text) == (D("0.9000"), D("0.8000"), D("1.0000"))


def test_closer_candidate_wins_when_two_days_apart_with_equal_text():
    entries = [E(10, date(2026, 9, 5), "-580", merchant=""), E(11, date(2026, 9, 3), "-580", merchant="")]
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], entries)
    assert [c.representation.rows[0].entry_id for c in res.claims] == [11]


def test_candidates_one_day_apart_with_equal_text_stay_ambiguous():
    entries = [E(10, date(2026, 9, 4), "-580", merchant=""), E(11, date(2026, 9, 3), "-580", merchant="")]
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], entries)
    assert res.claims == [] and res.cases[0].kind == "ambiguous" and {c["entry_id"] for c in res.cases[0].candidates} == {10, 11}


def test_text_can_reverse_the_date_order():
    # distance 0 / text 0 → 0.90; distance 2 / text 1 → 0.9333: inside the 0.05 margin → ambiguous, not the closer one
    entries = [E(10, date(2026, 9, 3), "-580", merchant="家樂福"), E(11, date(2026, 9, 5), "-580", merchant="全聯 大安")]
    res = run([L(1, date(2026, 9, 3), "-580", merchant="全聯 大安")], entries)
    assert res.claims == [] and res.cases[0].kind == "ambiguous"


def test_exact_tie_stays_ambiguous():
    entries = [E(10, date(2026, 9, 3), "-580", merchant=""), E(11, date(2026, 9, 3), "-580", merchant="")]
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], entries)
    assert res.claims == [] and res.cases[0].kind == "ambiguous"


def test_zero_window_scores_same_day_and_nothing_else():
    rules = dataclasses.replace(R, exact_window_days=0)
    same, _ = m._score(L(1, date(2026, 9, 3), "-580", merchant=""), (E(10, date(2026, 9, 3), "-580", merchant=""),), 0)
    next_day, reasons = m._score(L(1, date(2026, 9, 3), "-580", merchant=""), (E(10, date(2026, 9, 4), "-580", merchant=""),), 0)
    assert same == D("0.9000") and next_day == D("0") and reasons == ("date_out_of_window",)
    res = m.match("card", [L(1, date(2026, 9, 3), "-580", merchant="")], [E(10, date(2026, 9, 3), "-580", merchant="")], {}, {}, lambda e: None, rules, "TWD")
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10]


def test_zero_window_uses_txn_date_distance_too():
    # Review Focus 2: txn_date == entry_date counts as distance 0 even when posted dates differ
    rules = dataclasses.replace(R, exact_window_days=0)
    line = L(1, date(2026, 9, 5), "-580", merchant="", txn_date=date(2026, 9, 3))
    res = m.match("card", [line], [E(10, date(2026, 9, 3), "-580", merchant="")], {}, {}, lambda e: None, rules, "TWD")
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10]


def test_margin_default_is_five_hundredths():
    assert m.Rules(period_end=date(2026, 9, 30)).margin == D("0.05")


def test_two_installment_representations_with_a_text_gap_now_resolve():
    # the shared margin runs before _installment_case: two confirmed-plan reps whose text differs by >= 0.05 claim
    # the better one (under margin 0.15 they stayed ambiguous because text <= 0.10)
    entries = [E(40, date(2026, 9, 5), "-1000", merchant="APPLE STORE", instance=m.InstanceRef(definition_id=7, seq=2, times=12)),
               E(41, date(2026, 9, 5), "-1000", merchant="家樂福", instance=m.InstanceRef(definition_id=7, seq=2, times=12))]
    res = run([L(1, date(2026, 9, 5), "-1000", kind="installment", merchant="APPLE STORE", seq=2, total=12)], entries,
              plan_map={"APPLE STORE|12|1000.0000": 7})
    assert [c.representation.rows[0].entry_id for c in res.claims] == [40]


def test_claimed_by_earlier_line_hint_under_the_new_scale():
    # lines run in id order. Line 1 is 3 days from the entry (old score 0.675: no claim, one-candidate ambiguous),
    # line 2 is on the entry's day. Under R6a line 1 claims (0.80) and line 2 — which used to hold the exact claim —
    # sees exactly one shadow representation whose row is claimed → the hint names it (parent §6.3, newly reachable)
    lines = [L(1, date(2026, 9, 6), "-580", merchant=""), L(2, date(2026, 9, 3), "-580", merchant="")]
    res = run(lines, [E(10, date(2026, 9, 3), "-580", merchant="")])
    assert [c.line_id for c in res.claims] == [1]
    assert res.cases[0].line_id == 2 and res.cases[0].context == {"hint": "claimed_by_earlier_line", "entry_id": 10}


def test_single_representation_below_accept_is_never_ambiguous():
    # R6a.2 contract: a lone candidate never stops a line as ambiguous; with accept raised to 0.95 a distance-0,
    # text-0 representation (0.90) is below accept and must fall through to the fallback chain
    rules = dataclasses.replace(R, accept=D("0.95"))
    res = m.match("card", [L(1, date(2026, 9, 3), "-580", merchant="")], [E(10, date(2026, 9, 3), "-580", merchant="")], {}, {}, lambda e: None, rules, "TWD")
    assert res.claims == [] and res.cases[0].kind != "ambiguous"


def test_group_and_standalone_same_day_equal_text_stay_ambiguous():
    # Review Focus 3: two representation shapes with equal scores compete globally; neither wins by position
    g = m.Group(id=5, kind="split", member_ids=(21, 22))
    entries = [E(10, date(2026, 9, 3), "-580", merchant=""), E(21, date(2026, 9, 3), "-300", group_id=5, merchant=""),
               E(22, date(2026, 9, 3), "-280", group_id=5, merchant="")]
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], entries, groups={5: g})
    assert res.claims == [] and res.cases[0].kind == "ambiguous"
    assert {(c["entry_id"], c["group_id"]) for c in res.cases[0].candidates} == {(10, None), (None, 5)}


def test_card_purchase_matches_a_receivable_i_paid_for_someone():
    # R6a.3: a non-settlement receivable (amount < 0) conserves a purchase flow
    res = run([L(1, date(2026, 9, 3), "-1200")], [E(10, date(2026, 9, 3), "-1200", kind="receivable")])
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10]


def test_card_purchase_never_matches_a_receivable_settlement():
    # a collection (amount > 0, is_settlement) is class settlement_in: not in the purchase gate, and the sign differs anyway
    res = run([L(1, date(2026, 9, 3), "-1200")], [E(10, date(2026, 9, 3), "1200", kind="receivable", is_settlement=True)])
    assert res.claims == [] and res.cases[0].kind == "line_unmatched"


def test_bank_withdrawal_matches_a_receivable_and_deposit_matches_a_payable():
    entries = [E(10, date(2026, 9, 3), "-1200", kind="receivable"), E(11, date(2026, 9, 4), "5000", kind="payable")]
    lines = [L(1, date(2026, 9, 3), "-1200", kind="withdrawal"), L(2, date(2026, 9, 4), "5000", kind="deposit")]
    res = run(lines, entries, kind="bank")
    assert sorted((c.line_id, c.representation.rows[0].entry_id) for c in res.claims) == [(1, 10), (2, 11)]


def test_gates_list_receivable_and_payable():
    assert m.eligible_kinds("card", "purchase") == frozenset({"expense", "receivable"})
    assert m.eligible_kinds("card", "installment") == frozenset({"expense", "receivable"})
    assert "receivable" in m.eligible_kinds("bank", "withdrawal") and "payable" in m.eligible_kinds("bank", "deposit")
    assert "payable" not in m.eligible_kinds("bank", "withdrawal") and "receivable" not in m.eligible_kinds("bank", "deposit")


def test_near_same_day_needs_no_merchant_overlap():
    # R6a.4: a MOZE memo rarely repeats the statement merchant text; on the same day amount proximity is enough
    res = run([L(1, date(2026, 9, 3), "-1030", merchant="AMAZON JP")], [E(10, date(2026, 9, 3), "-1000", merchant="日亞")])
    case = res.cases[0]
    assert case.kind == "amount_delta" and case.entry_id == 10 and case.context["fee_matches"] is False
    assert case.context["residual"] == "-30.0000" and res.claims == []


def test_near_one_day_apart_still_needs_merchant_overlap():
    res = run([L(1, date(2026, 9, 4), "-1030", merchant="AMAZON JP")], [E(10, date(2026, 9, 3), "-1000", merchant="日亞")])
    assert res.cases[0].kind == "line_unmatched"
    res = run([L(1, date(2026, 9, 4), "-1030", merchant="AMAZON JP")], [E(10, date(2026, 9, 3), "-1000", merchant="AMAZON JP")])
    assert res.cases[0].kind == "amount_delta"


def test_near_tolerance_is_five_percent():
    assert m.Rules(period_end=date(2026, 9, 30)).near_tolerance_pct == D("0.05")
    res = run([L(1, date(2026, 9, 3), "-1049", merchant="")], [E(10, date(2026, 9, 3), "-1000", merchant="")])
    assert res.cases[0].kind == "amount_delta"
    # the tolerance base is the LINE amount (spec R6a.4 keeps `max(abs, pct·|line.flow|)`): 5 % of 1053 = 52.65 < 53
    res = run([L(1, date(2026, 9, 3), "-1053", merchant="")], [E(10, date(2026, 9, 3), "-1000", merchant="")])
    assert res.cases[0].kind == "line_unmatched"


def test_near_small_amounts_use_the_absolute_tolerance_and_never_claim():
    # Review Focus 4: 50 vs 58 is 16 % but within abs 10 — a proposal, never a claim
    res = run([L(1, date(2026, 9, 3), "-58", merchant="")], [E(10, date(2026, 9, 3), "-50", merchant="")])
    assert res.claims == [] and res.cases[0].kind == "amount_delta" and res.cases[0].context["fee_matches"] is False


def test_near_applies_to_card_payment_lines_through_peer_ok():
    # a payment line meets a transfer_in leg whose peer is known not to be a card; same day, 1 % off
    ok = E(10, date(2026, 9, 3), "20200", kind="transfer_in", peer_is_card=False)
    card_peer = E(11, date(2026, 9, 3), "20200", kind="transfer_in", peer_is_card=True)
    res = run([L(1, date(2026, 9, 3), "20000", kind="payment", merchant="")], [ok, card_peer])
    assert res.cases[0].kind == "amount_delta" and [c["entry_id"] for c in res.cases[0].candidates] == [10]


def test_drift_window_default_and_settings_shape():
    assert m.Rules(period_end=date(2026, 9, 30)).drift_window_days == 15


def test_exact_amount_four_to_fifteen_days_apart_is_a_date_drift_proposal():
    for days, expect in ((4, "amount_delta"), (15, "amount_delta"), (16, "line_unmatched")):
        res = run([L(1, date(2026, 9, 3), "-580", merchant="")], [E(10, date(2026, 9, 3 + days), "-580", merchant="")])
        case = res.cases[0]
        assert case.kind == expect, days
        if expect == "amount_delta":
            assert res.claims == [] and case.entry_id == 10
            assert case.context == {"hint": "date_drift", "date_distance": days, "entry_id": 10, "residual": "0", "fee_matches": False}
            assert case.candidates == ({"entry_id": 10, "reason": "date_drift", "date_distance": days},)


def test_drift_candidates_are_ordered_by_distance_then_id_and_skip_claimed_and_grouped():
    # Review Focus 5: an entry claimed by an earlier line and a member of a group printed whole (−581, within the
    # near tolerance of the −580 lines) never appear. Line 2 sits 4 days from the
    # claimed entry 10 (outside the exact window, so no claimed_by_earlier_line shadow) and 7 days from 11/12.
    g = m.Group(id=5, kind="split", member_ids=(30, 31))
    entries = [E(10, date(2026, 9, 3), "-580", merchant=""), E(12, date(2026, 9, 14), "-580", merchant=""),
               E(11, date(2026, 9, 14), "-580", merchant=""), E(30, date(2026, 9, 14), "-580", group_id=5, merchant=""),
               E(31, date(2026, 9, 14), "-1", group_id=5, merchant="")]
    lines = [L(1, date(2026, 9, 3), "-580", merchant=""), L(2, date(2026, 9, 7), "-580", merchant="")]
    res = run(lines, entries, groups={5: g})
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10]
    drift = next(c for c in res.cases if c.line_id == 2)
    assert drift.kind == "amount_delta" and drift.context["hint"] == "date_drift"
    assert [c["entry_id"] for c in drift.candidates] == [11, 12]


def test_drift_runs_after_near_and_before_bank_only():
    # a near candidate on the same day wins over a drift candidate ten days away
    entries = [E(10, date(2026, 9, 3), "-1000", merchant=""), E(11, date(2026, 9, 13), "-1030", merchant="")]
    res = run([L(1, date(2026, 9, 3), "-1030", merchant="")], entries)
    assert res.cases[0].context["entry_id"] == 10 and "hint" not in res.cases[0].context
    # a fee line with a drift candidate is drift, not bank_only
    res = run([L(1, date(2026, 9, 3), "-5", kind="fee", merchant="跨行手續費")], [E(10, date(2026, 9, 10), "-5", kind="fee", merchant="")])
    assert res.cases[0].context["hint"] == "date_drift"


def test_exact_amount_four_days_apart_with_merchant_overlap_is_near_not_drift():
    # near runs first and accepts delta 0 inside its 5-day window when the merchant text overlaps; drift takes the
    # same pair only without text overlap. Both outcomes are proposals, never claims.
    res = run([L(1, date(2026, 9, 3), "-580", merchant="全聯 大安")], [E(10, date(2026, 9, 7), "-580", merchant="全聯 大安")])
    assert res.claims == [] and res.cases[0].kind == "amount_delta" and "hint" not in res.cases[0].context
    res = run([L(1, date(2026, 9, 3), "-580", merchant="全聯 大安")], [E(10, date(2026, 9, 7), "-580", merchant="家樂福")])
    assert res.cases[0].context["hint"] == "date_drift"


def test_drift_respects_the_gate_and_peer_rule():
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], [E(10, date(2026, 9, 10), "-580", kind="refund", merchant="")])
    assert res.cases[0].kind == "line_unmatched"
    card_peer = E(11, date(2026, 9, 10), "20000", kind="transfer_in", peer_is_card=True)
    res = run([L(1, date(2026, 9, 3), "20000", kind="payment", merchant="")], [card_peer])
    assert res.cases[0].kind == "line_unmatched"


def test_owner_representation_drops_stand_in_representations():
    # R6b.0: a stand-in (backfilled entry) never competes with the owner's entry for the same line
    entries = [E(10, date(2026, 9, 3), "-580", merchant="", stand_in=True), E(11, date(2026, 9, 3), "-580", merchant="")]
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], entries)
    assert [c.representation.rows[0].entry_id for c in res.claims] == [11]
    assert 10 in res.unmatched_entry_ids


def test_stand_ins_compete_only_among_themselves():
    entries = [E(10, date(2026, 9, 3), "-580", merchant="", stand_in=True), E(11, date(2026, 9, 3), "-580", merchant="", stand_in=True)]
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], entries)
    assert res.claims == [] and res.cases[0].kind == "ambiguous"
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], entries[:1])
    assert [c.representation.rows[0].entry_id for c in res.claims] == [10]


def test_stand_in_drop_applies_to_group_and_child_representations_too():
    g = m.Group(id=5, kind="split", member_ids=(21, 22))
    entries = [E(10, date(2026, 9, 3), "-580", merchant="", stand_in=True),
               E(21, date(2026, 9, 3), "-300", group_id=5, merchant=""), E(22, date(2026, 9, 3), "-280", group_id=5, merchant="")]
    res = run([L(1, date(2026, 9, 3), "-580", merchant="")], entries, groups={5: g})
    assert res.claims and res.claims[0].representation.rule == "group"


@pytest.mark.parametrize("kind, stmt", [("payment", "card"), ("transfer_in", "bank"), ("transfer_out", "bank"), ("payment", "bank")])
def test_payment_and_transfer_lines_without_a_match_get_needs_peer(kind, stmt):
    res = run([L(1, date(2026, 9, 3), "-20000" if kind != "transfer_in" else "20000", kind=kind, merchant="")], [], kind=stmt)
    assert res.cases[0].kind == "line_unmatched" and res.cases[0].context == {"hint": "needs_peer"}


def test_needs_peer_is_not_set_when_a_near_or_drift_case_exists():
    ok = E(10, date(2026, 9, 3), "20200", kind="transfer_in", peer_is_card=False)
    res = run([L(1, date(2026, 9, 3), "20000", kind="payment", merchant="")], [ok])
    assert res.cases[0].kind == "amount_delta"


def test_only_the_designated_entry_of_an_amount_delta_is_never_deferred():
    """The deferral explains entries the NEXT statement prints. The one entry an amount_delta (near, foreign, drift)
    pairs with its line is printed here, so it stays unexplained (an entry_unmatched case); alternatives of an
    ambiguous case and secondary candidates defer as before (at most one of them is this line)."""
    late = date(2026, 9, 30)
    near = run([L(1, late, "-1005", merchant="全聯")], [E(10, late, "-1000", merchant="全聯")])
    assert near.cases[0].kind == "amount_delta" and near.unmatched_entry_ids == [10] and near.explained == {}
    amb = run([L(1, late, "-580", merchant="")], [E(10, late, "-580", merchant="A"), E(11, late, "-580", merchant="B")])
    assert amb.cases[0].kind == "ambiguous"
    assert amb.explained == {10: "deferred_next_period", 11: "deferred_next_period"}
    two = run([L(1, late, "-1005", merchant="全聯")], [E(10, late, "-1000", merchant="全聯"), E(13, late, "-1008", merchant="全聯")])
    assert two.cases[0].entry_id == 13 and {c["entry_id"] for c in two.cases[0].candidates} == {10, 13}
    assert two.explained == {10: "deferred_next_period"}  # the secondary candidate defers
    other = run([L(1, late, "-1005", merchant="全聯")], [E(10, late, "-1000", merchant="全聯"), E(12, late, "-77", merchant="早餐")])
    assert other.explained == {12: "deferred_next_period"}  # an unnamed late entry is still deferred


# --- split members: structural printed-alone pieces, group readings, printed-whole guard (pure) ---

d = date(2026, 9, 3)


def test_printed_alone_children_match_a_non_purchase_line_of_any_coverage():
    g = m.Group(5, "split", (21, 22))
    ents = [E(21, d, "-300", group_id=5, children=[m.Child(211, "discount", D("100"))],
              all_children=[m.Child(210, "reward", D("200")), m.Child(211, "discount", D("100"))]), E(22, d, "-280", group_id=5)]  # 210 covered: structural view only
    assert m.printed_alone_children("card", [L(1, d, "200", kind="reward")], ents, {5: g}) == {210}
    assert m.printed_alone_children("card", [L(1, d, "-200", kind="purchase")], ents, {5: g}) == frozenset()
    # a discount child is never printed alone: no line gate admits "discount" (the matcher's child path cannot claim it)
    assert m.printed_alone_children("card", [L(1, d, "100", kind="refund")], ents, {5: g}) == frozenset()


def test_group_readings_enumerate_members_and_children_like_the_matcher():
    g = m.Group(5, "split", (21, 22, 23))
    ents = [E(21, d, "-300", group_id=5, children=[m.Child(210, "reward", D("200")), m.Child(211, "discount", D("100"))]),
            E(22, d, "-280", group_id=5), S(23, d, "-10", kind="fee", group_id=5)]
    readings, complete = m.group_readings(g, {e.id: e for e in ents}, aside=frozenset({23}), aside_children=frozenset({210}))
    assert complete and {r.flow for r in readings} == {D("-590"), D("-290"), D("-490"), D("-580"), D("-280"), D("-480")}


def test_group_readings_missing_member_is_incomplete():
    g = m.Group(5, "split", (21, 22))
    assert m.group_readings(g, {21: E(21, d, "-300", group_id=5)}, frozenset(), frozenset()) == ([], False)


def test_group_readings_foreign_total_needs_one_currency_on_every_member():
    g = m.Group(5, "split", (21, 22))
    jp = dict(original_currency="JPY")
    ents = [E(21, d, "-3000", group_id=5, original_amount="-15000", **jp), S(22, d, "-100", group_id=5, original_amount="-500", **jp)]
    r, _ = m.group_readings(g, {e.id: e for e in ents}, frozenset({22}), frozenset())
    assert {(x.flow, x.foreign_amount) for x in r} == {(D("-3100"), D("-15500")), (D("-3000"), D("-15000"))}
    ents[1] = S(22, d, "-100", group_id=5)
    assert all(x.foreign_amount is None for x in m.group_readings(g, {e.id: e for e in ents}, frozenset(), frozenset())[0])


def test_printed_whole_by_exact_near_drift_and_sign():
    g = m.Group(5, "split", (21, 22)); ents = [E(21, d, "-300", group_id=5), E(22, d, "-280", group_id=5)]
    pw = lambda lines: m.printed_whole(lines, ents, {5: g}, R, frozenset(), frozenset())
    assert pw([L(1, d, "-580")]) == {5} and pw([L(1, d, "-575")]) == {5} and pw([L(1, d + timedelta(days=12), "-580")]) == {5}
    assert pw([L(1, d + timedelta(days=16), "-580")]) == frozenset() and pw([L(1, d, "-300")]) == frozenset()
    assert pw([L(1, d, "580", kind="refund")]) == frozenset()


def test_printed_whole_ignores_gate_and_uses_structural_rows():
    g = m.Group(5, "split", (21, 22)); ents = [E(21, d, "20000", kind="income", group_id=5), S(22, d, "10000", kind="income", group_id=5)]
    assert m.printed_whole([L(1, d, "30000", kind="deposit")], ents, {5: g}, R, frozenset(), frozenset()) == {5}


def test_printed_whole_fails_closed_without_the_other_member():
    g = m.Group(5, "split", (21, 22))
    assert m.printed_whole([L(1, d, "-300")], [E(21, d, "-300", group_id=5)], {5: g}, R, frozenset(), frozenset()) == {5}


# --- guard holders (issue #136): a guard line an unrelated, non-stand-in entry explains exactly is skipped ---

H = m.Holder


def _pw(lines, ents, holders):
    return m.printed_whole(lines, ents, {5: m.Group(5, "split", (21, 22))}, R, frozenset(), frozenset(), holders=holders)


def SPLIT():
    return [E(21, d, "-300", group_id=5), E(22, d, "-280", group_id=5)]


def test_printed_whole_skips_a_line_an_unrelated_entry_explains_exactly():        # shapes 1/2
    near = [L(1, d, "-575")]
    assert _pw(near, SPLIT(), None) == {5} and _pw(near, SPLIT(), {}) == {5}
    assert _pw(near, SPLIT(), {1: H(frozenset({99}), False, True)}) == frozenset()


def test_printed_whole_keeps_a_stand_in_held_line():                              # shape 3
    assert _pw([L(1, d, "-580")], SPLIT(), {1: H(frozenset({99}), True, True)}) == {5}


def test_printed_whole_keeps_an_inexact_holder_and_a_missing_holder():            # shapes 5/7
    assert _pw([L(1, d, "-575")], SPLIT(), {1: H(frozenset({99}), False, False)}) == {5}
    assert _pw([L(1, d, "-575")], SPLIT(), {2: H(frozenset({99}), False, True)}) == {5}


def test_unrelated_exact_treats_a_member_and_a_members_child_as_related():        # shape 6
    ents = SPLIT(); ents[0] = E(21, d, "-300", group_id=5, children=(m.Child(210, "fee", D("-10")),))
    g = m.Group(5, "split", (21, 22)); related = m._related_ids(g, {e.id: e for e in ents})
    assert related == {21, 22, 210}
    assert not m._unrelated_exact(H(frozenset({21}), False, True), related)
    assert not m._unrelated_exact(H(frozenset({210}), False, True), related)
    assert m._unrelated_exact(H(frozenset({99}), False, True), related)
    # the guard's child view (`_kids`): a child only in `all_children` (covered elsewhere) is related too
    ents[0] = E(21, d, "-300", group_id=5, all_children=(m.Child(211, "fee", D("-10")),))
    assert not m._unrelated_exact(H(frozenset({211}), False, True), m._related_ids(g, {e.id: e for e in ents}))
    # a holder member of the group keeps the line a guard line in printed_whole
    assert _pw([L(1, d, "-575")], SPLIT(), {1: H(frozenset({22}), False, True)}) == {5}


def test_unrelated_exact_a_multi_row_holder_overlapping_a_member_is_related():   # shape 8 overlap
    related = m._related_ids(m.Group(5, "split", (21, 22)), {e.id: e for e in SPLIT()})
    assert not m._unrelated_exact(H(frozenset({21, 99}), False, True), related)    # one member row + one unrelated row
    assert m._unrelated_exact(H(frozenset({98, 99}), False, True), related)        # another group's two rows
    assert _pw([L(1, d, "-575")], SPLIT(), {1: H(frozenset({21, 99}), False, True)}) == {5}   # the line is kept


def test_claim_holders_marks_conserving_claims_exact_and_members_related():
    ents = [E(99, d, "-575"), *SPLIT()]
    lines = [L(1, d, "-575"), L(2, d, "-300")]
    res = m.match("card", lines, ents, {5: m.Group(5, "split", (21, 22))}, {}, lambda e: None, R, "TWD")
    own = m.claim_holders(res, lines, ents, frozenset())
    assert own == {1: H(frozenset({99}), False, True)}            # pass 1: group whole, 99 claims line 1, line 2 open
    res2 = m.match("card", lines, ents, {5: m.Group(5, "split", (21, 22))}, {}, lambda e: None, R, "TWD", holders=own)
    assert {c.line_id: c.representation.rows[0].entry_id for c in res2.claims} == {1: 99, 2: 21}
    assert m.claim_holders(res2, lines, ents, frozenset())[2] == H(frozenset({21}), False, True)
    assert m.claim_holders(res2, lines[:1], ents, frozenset()) == {1: H(frozenset({99}), False, True)}  # not a guard line: skipped


def test_claim_holders_resolves_child_rows_source_and_foreign_fields():          # shapes 11/12
    fee = m.Child(991, "fee", D("-10"), source="statement")
    disc = m.Child(992, "discount", D("5"))
    later = d + timedelta(days=5)  # outside the exact window of the other entry: each line has one representation
    ents = [E(99, d, "-565", children=(fee,)), E(98, later, "-580", children=(disc,))]
    lines = [L(1, d, "-575"), L(2, later, "-575")]
    res = m.match("card", lines, ents, {}, {}, lambda e: None, R, "TWD")
    own = m.claim_holders(res, lines, ents, frozenset())
    assert own[1] == H(frozenset({99, 991}), True, True)          # statement-sourced fee child → stand-in holder
    assert own[2] == H(frozenset({98, 992}), False, True)         # plain parent + discount child, signed sum exact
    assert m.claim_holders(res, lines, ents, frozenset({98}))[2].stand_in is True   # backfill id → stand-in
    assert m.claim_holders(res, lines, ents[:1], frozenset())[2].exact is False     # unresolvable rows → not exact


def test_claim_holders_foreign_exactness_needs_the_full_same_currency_original_sum():   # AGENT-254 3
    # every matcher claim conserves the line's flow (a foreign rule only opens an amount_delta case), so the foreign
    # branch is exercised on a hand-built claim whose rows do not sum to the line
    jp = dict(original_amount="-15000", original_currency="JPY")
    line = L(1, d, "-3010", foreign_amount="-15000", foreign_currency="JPY")

    def own(entry, rows=None, ln=line):
        rows = rows or (m.Row(entry.id, "principal", entry.flow),)
        res = m.MatchResult(claims=[m.Claim(1, m.Representation("standalone", rows, D("1"), "exact", ()))])
        return m.claim_holders(res, [ln], [entry], frozenset())[1]
    assert own(E(99, d, "-3005", **jp)).exact is True                                 # TWD differs (FX), JPY sum equal
    assert own(E(99, d, "-3005", original_amount="-15000", original_currency="USD")).exact is False   # currency mismatch
    assert own(E(99, d, "-3005", original_amount="-14000", original_currency="JPY")).exact is False   # partial sum
    assert own(E(99, d, "-3005", **jp), ln=L(1, d, "-3010")).exact is False                           # no foreign amount
    zero = L(1, d, "-3010", foreign_amount="0", foreign_currency="JPY")                                # zero foreign amount
    assert own(E(99, d, "-3005", original_amount="0", original_currency="JPY"), ln=zero).exact is False
    kid = m.Child(991, "fee", D("-3"), original_currency="JPY")                                       # no original_amount
    parent = E(99, d, "-3005", **jp, children=(kid,))
    assert own(parent, (m.Row(99, "principal", D("-3005")), m.Row(991, "child", D("-3")))).exact is False
    # through match: a conserving claim is exact by its signed flows whatever its foreign fields
    res = m.match("card", [line], [parent], {}, {}, lambda e: None, R, "TWD")
    assert m.claim_holders(res, [line], [parent], frozenset()) == {}                  # -3008: only an amount_delta case
    kid5 = m.Child(991, "fee", D("-5"), original_currency="JPY")
    parent5 = E(99, d, "-3005", **jp, children=(kid5,))
    res = m.match("card", [line], [parent5], {}, {}, lambda e: None, R, "TWD")
    assert m.claim_holders(res, [line], [parent5], frozenset()) == {1: H(frozenset({99, 991}), False, True)}


def test_printed_whole_through_match_keeps_a_group_whose_near_line_a_stand_in_claim_holds():   # shape 11 end to end
    fee = m.Child(991, "fee", D("-10"), source="statement")
    ents = [E(99, d, "-565", children=(fee,)), *SPLIT()]
    lines = [L(1, d, "-575"), L(2, d, "-300")]
    res = m.match("card", lines, ents, {5: m.Group(5, "split", (21, 22))}, {}, lambda e: None, R, "TWD")
    own = m.claim_holders(res, lines, ents, frozenset())
    assert own == {1: H(frozenset({99, 991}), True, True)}
    res2 = m.match("card", lines, ents, {5: m.Group(5, "split", (21, 22))}, {}, lambda e: None, R, "TWD", holders=own)
    assert {c.line_id for c in res2.claims} == {1}                 # line 2 stays open: the group is whole


def test_stand_in_row_is_a_backfill_id_or_a_statement_source():
    assert m.stand_in_row(7, None, frozenset({7})) and m.stand_in_row(8, "statement", frozenset())
    assert not m.stand_in_row(8, None, frozenset({7})) and not m.stand_in_row(8, "moze", frozenset())


def test_merge_holders_is_a_disjoint_union_and_fail_closes_a_collision():
    a = {1: H(frozenset({7}), False, True), 2: H(frozenset({8}), True, True)}
    b = {1: H(frozenset({9}), False, True), 3: H(frozenset({10}), False, True)}
    assert m.merge_holders(a, b) == {1: H(frozenset({7, 9}), False, False), 2: a[2], 3: b[3]}
    assert m.merge_holders({}, b) == b and m.merge_holders(a, {}) == a


def test_statement_context_threads_holders_to_the_guard():
    lines = [L(1, d, "-575")]
    assert m.statement_context("card", lines, lines, SPLIT(), {5: m.Group(5, "split", (21, 22))}, R, "TWD").whole == {5}
    ctx = m.statement_context("card", lines, lines, SPLIT(), {5: m.Group(5, "split", (21, 22))}, R, "TWD",
                              holders={1: H(frozenset({99}), False, True)})
    assert ctx.whole == frozenset() and ctx.apart == {21, 22}


def test_printed_whole_by_reduced_and_children_readings_through_covered_lines():
    g = m.Group(5, "split", (21, 22, 23))
    ents = [E(21, d, "-300", group_id=5, children=[m.Child(210, "reward", D("200")), m.Child(211, "discount", D("100"))]),
            E(22, d, "-280", group_id=5), S(23, d, "-100", kind="fee", group_id=5)]
    # -480 = members 21+22 (fee 23 printed alone, -580) + child 211 only (+100; reward 210 printed alone): the reduced
    # reading alone; without aside/aside_children the nearest totals are -580 and -380, 100 off (tolerance 29)
    lines_all = [L(1, d, "-300"), L(2, d, "-480"), L(3, d, "-100", kind="fee"), L(4, d, "200", kind="reward")]
    ctx = m.statement_context("card", lines_all, lines_all, ents, {5: g}, R, "TWD")
    assert ctx.aside == ctx.aside_all == {23} and ctx.aside_children == {210} and ctx.whole == {5} and ctx.apart == frozenset()
    # the fee and reward lines covered (in lines_all only): the guard's sets are unchanged, the claim side's aside is
    # over the claimable lines (spec §4.1)
    covered = m.statement_context("card", lines_all[:2], lines_all, ents, {5: g}, R, "TWD")
    assert (covered.aside, covered.aside_all, covered.aside_children, covered.whole) == (frozenset(), {23}, {210}, {5})


def test_printed_whole_by_foreign_total():
    g = m.Group(5, "split", (21, 22)); jp = dict(original_currency="JPY")
    ents = [E(21, d, "-3000", group_id=5, original_amount="-15000", **jp), E(22, d, "-100", group_id=5, original_amount="-500", **jp)]
    line = L(1, d, "-3200", foreign_amount=D("15500"), foreign_currency="JPY")
    assert m.printed_whole([line], ents, {5: g}, R, frozenset(), frozenset()) == {5}


def test_apart_members_are_claimable_split_members_of_apart_groups_only():
    g5 = m.Group(5, "split", (21, 22)); g6 = m.Group(6, "split", (31, 32)); g7 = m.Group(7, "installment", (41,))
    ents = [E(21, d, "-300", group_id=5), E(22, d, "-280", group_id=5), E(31, d, "-50", group_id=6), S(32, d, "-60", group_id=6), E(41, d, "-70", group_id=7)]
    ctx = m.statement_context("card", [L(1, d, "-580")], [L(1, d, "-580")], ents, {5: g5, 6: g6, 7: g7}, R, "TWD")
    assert ctx.whole == {5} and ctx.apart == {31}


def test_a_split_with_a_recurring_member_has_no_readings_and_stays_whole():
    g = m.Group(5, "split", (21, 22))
    ents = [E(21, d, "-300", group_id=5, instance=INST("recurring")), E(22, d, "-280", group_id=5)]
    assert m.group_readings(g, {e.id: e for e in ents}, frozenset(), frozenset()) == ([], True)
    ctx = m.statement_context("card", [L(1, d, "-50")], [L(1, d, "-50")], ents, {5: g}, R, "TWD")
    assert ctx.whole == {5} and ctx.apart == frozenset()


def test_split_member_instance_rule_and_children_cap():
    g = m.Group(5, "split", (21, 22, 23, 24, 25))
    ents = [E(21, d, "-300", group_id=5), E(22, d, "-100", group_id=5, instance=INST("installment")),
            E(23, d, "-10", kind="fee", group_id=5, parent_entry_id=21), E(24, d, "-50", group_id=5, instance=INST("recurring")),
            S(25, d, "-60", group_id=5), E(26, d, "-70")]
    assert m.apart_members(ents, {5: g}, frozenset()) == {21, 24}
    assert m.apart_members(ents, {5: g}, frozenset({5})) == frozenset()
    kids = [m.Child(300 + i, "reward", D(i + 1)) for i in range(m.ASIDE_COMBINATIONS_MAX + 1)]
    g6 = m.Group(6, "split", (31, 32))
    capped = [E(31, d, "-300", group_id=6, children=kids), E(32, d, "-280", group_id=6)]
    readings, complete = m.group_readings(g6, {e.id: e for e in capped}, frozenset(), frozenset(c.id for c in kids))
    assert complete is False and {r.flow for r in readings} == {D("-580"), D("-580") + sum(c.flow for c in kids)}
    assert m.printed_whole([L(1, d, "-5000")], capped, {6: g6}, R, frozenset(), frozenset(c.id for c in kids)) == {6}


def test_printed_whole_compares_a_zero_total_with_a_zero_line():
    """Final review, minor 1: a 0 reading (an expense and the receivable that offsets it) against a 0 line has no sign
    to share; `_same` counts it."""
    g = m.Group(5, "split", (21, 22)); ents = [E(21, d, "-100", group_id=5), E(22, d, "100", kind="receivable", group_id=5)]
    assert m.printed_whole([L(1, d, "0")], ents, {5: g}, R, frozenset(), frozenset()) == {5}
    assert m.printed_whole([L(1, d, "-100")], ents, {5: g}, R, frozenset(), frozenset()) == frozenset()


def test_printed_whole_amount_window_is_the_wider_of_the_exact_and_drift_windows():
    """Final review, minor 2: an exact window wider than the drift window still bounds the guard."""
    g = m.Group(5, "split", (21, 22)); ents = [E(21, d, "-300", group_id=5), E(22, d, "-280", group_id=5)]
    wide = dataclasses.replace(R, exact_window_days=20, drift_window_days=15)
    line = L(1, d + timedelta(days=18), "-580")
    assert m.printed_whole([line], ents, {5: g}, wide, frozenset(), frozenset()) == {5}
    assert m.printed_whole([line], ents, {5: g}, R, frozenset(), frozenset()) == frozenset()


def test_a_group_with_a_member_or_child_in_another_currency_is_whole_by_fail_closed():
    """Spec §3 "Mixed currencies": a structural member on a JPY account cannot be summed with a TWD line, so the group
    is whole by fail-closed and the card's member-sized line claims nothing; likewise a child in another currency.
    `None` is the statement's currency."""
    g = m.Group(5, "split", (21, 22))
    jpy = [E(21, d, "-300", group_id=5, currency="TWD"), S(22, d, "-1400", group_id=5, account_id=2, currency="JPY")]
    assert m.group_readings(g, {e.id: e for e in jpy}, frozenset(), frozenset(), "TWD") == ([], False)
    res = run([L(1, d, "-300")], jpy, groups={5: g})
    assert res.claims == [] and [(c.kind, c.candidates) for c in res.cases] == [("line_unmatched", ())]
    usd_kid = m.Child(210, "refund", D("5"), "USD")
    kid = [E(21, d, "-300", group_id=5, all_children=[usd_kid]), S(22, d, "-280", group_id=5, account_id=2)]
    assert run([L(1, d, "-300")], kid, groups={5: g}).claims == []
    same = [E(21, d, "-300", group_id=5, currency="TWD"), S(22, d, "-280", group_id=5, account_id=2, currency="TWD")]
    assert run([L(1, d, "-300")], same, groups={5: g}).claims[0].representation.kind == "member_alone"


# --- split members printed apart (spec 2026-10-24 §4, §7) -------------------------------------------------------------

def test_cross_account_member_claims_its_own_line():
    d = date(2026, 9, 3)
    g = m.Group(5, "split", (21, 22))
    res = run([L(1, d, "-300")], [E(21, d, "-300", group_id=5), S(22, d, "-280", group_id=5, account_id=2)], groups={5: g})  # 22: another account, structural
    rep = res.claims[0].representation
    assert (rep.kind, rep.rule, [(r.entry_id, r.group_id) for r in rep.rows]) == ("member_alone", "exact", [(21, None)])
    assert res.cases == [] and res.unmatched_entry_ids == []


def test_cross_account_expense_plus_receivable_whose_sum_the_card_prints_stays_whole():
    d = date(2026, 9, 3)
    g = m.Group(5, "split", (21, 22))
    res = run([L(1, d, "-300")], [E(21, d, "-580", group_id=5), S(22, d, "280", kind="receivable", group_id=5, account_id=2)], groups={5: g})
    assert res.claims == [] and res.cases[0].kind == "line_unmatched" and res.unmatched_entry_ids == [21]


def test_same_card_split_printed_apart_claims_each_member():
    d = date(2026, 9, 3)
    g = m.Group(5, "split", (21, 22))
    ents = [E(21, d, "-300", group_id=5), E(22, d, "-280", group_id=5)]
    res = run([L(1, d, "-300"), L(2, d, "-280")], ents, groups={5: g})
    assert sorted(c.representation.rows[0].entry_id for c in res.claims) == [21, 22] and all(c.representation.kind == "member_alone" for c in res.claims)
    res2 = run([L(1, d, "-300"), L(2, d, "-280"), L(3, d, "-580")], ents, groups={5: g})
    assert [c.representation.rule for c in res2.claims] == ["group"] and [c.kind for c in res2.cases] == ["line_unmatched", "line_unmatched"]


def test_a_split_member_never_stands_alone_when_the_group_is_printed_whole():
    # rewritten from test_a_split_member_never_stands_alone_for_a_purchase_line: _fee_group is -300/-280/-10 → a -590 line
    d = date(2026, 9, 3)
    g, members = _fee_group()
    res = run([L(1, d, "-300"), L(2, d, "-590")], members, groups={6: g})
    assert [c.representation.rule for c in res.claims] == ["group"] and res.cases[0].kind == "line_unmatched"
    res2 = run([L(1, d, "-10")], members, groups={6: g})  # the fee member, for a purchase line: the gate keeps it out
    assert res2.claims == [] and res2.cases[0].kind == "line_unmatched"


def test_whole_by_covered_sum_line_in_lines_all():
    d = date(2026, 9, 3)
    g = m.Group(5, "split", (21, 22))
    ents = [E(21, d, "-300", group_id=5), E(22, d, "-280", group_id=5)]
    res = run([L(1, d, "-300")], ents, groups={5: g}, lines_all=[L(1, d, "-300"), L(2, d, "-580")])
    assert res.claims == [] and res.cases[0].kind == "line_unmatched"


def test_whole_by_a_neighbouring_statements_sum_line_in_lines_all():
    """Spec §3 "Guard lines": the −580 sum line sits on the previous statement (dated before this period, never in
    `lines`); the orchestrator passes it in `lines_all`, so the group is whole and the −300 line claims nothing."""
    d = date(2026, 9, 3)
    g = m.Group(5, "split", (21, 22))
    ents = [E(21, d, "-300", group_id=5), E(22, d, "-280", group_id=5)]
    previous = L(7, date(2026, 8, 27), "-580")  # R.period_end is 2026-09-30: a line of the August statement
    res = run([L(1, d, "-300")], ents, groups={5: g}, lines_all=[L(1, d, "-300"), previous])
    assert res.claims == [] and [(c.line_id, c.kind, c.candidates) for c in res.cases] == [(1, "line_unmatched", ())]
    assert run([L(1, d, "-300")], ents, groups={5: g}).claims[0].representation.kind == "member_alone"  # without it


def test_a_fee_line_covered_by_another_entry_never_reduces_the_purchase_reading():
    """Final review (spec §4.1): the claim side's `aside` is over the claimable lines. The −10 fee line is held by
    another entry (a system fee: in `lines_all` only), so the fee member is not printed alone for the claim side and
    the −300 purchase line has no reduced `group` reading: it stays `line_unmatched`, as before the branch. The guard
    still reads the covered line: the group is whole, so member 21 never stands alone either."""
    d = date(2026, 9, 3)
    g = m.Group(5, "split", (21, 22))
    ents = [E(21, d, "-300", group_id=5), E(22, d, "-10", kind="fee", group_id=5)]
    purchase, fee_line = L(1, d, "-300"), L(2, d, "-10", kind="fee")
    res = run([purchase], ents, groups={5: g}, lines_all=[purchase, fee_line])
    assert res.claims == [] and [(c.line_id, c.kind, c.candidates) for c in res.cases] == [(1, "line_unmatched", ())]
    assert run([purchase], ents, groups={5: g}).claims == []  # lines_all defaults to lines: same outcome


def test_whole_by_covered_fee_member_keeps_the_purchase_member_home():
    d = date(2026, 9, 3)
    g = m.Group(5, "split", (21, 22, 23))
    ents = [E(21, d, "-300", group_id=5), E(22, d, "-280", group_id=5), S(23, d, "-100", kind="fee", group_id=5)]  # fee 23 owner-covered: structural
    res = run([L(1, d, "-300"), L(2, d, "-580")], ents, groups={5: g},
              lines_all=[L(1, d, "-300"), L(2, d, "-580"), L(3, d, "-100", kind="fee")])
    assert res.claims == [] and [c.kind for c in res.cases] == ["line_unmatched", "line_unmatched"]
    assert run([L(1, d, "-300"), L(2, d, "-580")], ents, groups={5: g},
               lines_all=[L(1, d, "-300"), L(2, d, "-580"), L(3, d, "-100", kind="fee")]).claims == []  # idempotent


def test_whole_by_children_reading_with_a_covered_child_both_line_orders():
    d = date(2026, 9, 3)
    g = m.Group(5, "split", (21, 22))
    # the covered +200 child is a reward (its reward line's gate admits it; a refund line never marks a discount child)
    kids = [m.Child(210, "discount", D("100")), m.Child(211, "reward", D("200"))]
    ents = [E(21, d, "-300", group_id=5, children=kids[:1], all_children=kids), E(22, d, "-280", group_id=5)]  # 211 owner-covered: structural view only
    covered = L(9, d, "200", kind="reward")  # the +200 child's own line: in lines_all only
    for first, second in ((1, 2), (2, 1)):  # match() sorts by line id: swap the ids to test both orders
        lines = [L(first, d, "-300"), L(second, d, "-480")]
        res = run(lines, ents, groups={5: g}, lines_all=lines + [covered])
        claim = res.claims[0].representation
        assert claim.kind == "group_children" and {r.entry_id for r in claim.rows} == {21, 22, 210} and res.cases[0].line_id == first
    # the −480 line only in lines_all (covered): the group is still whole and the −300 line stays open
    only_all = run([L(1, d, "-300")], ents, groups={5: g}, lines_all=[L(1, d, "-300"), L(2, d, "-480"), covered])
    assert only_all.claims == [] and [c.kind for c in only_all.cases] == ["line_unmatched"]


def test_whole_by_near_tolerance_or_drift_window_keeps_the_members_home():
    d = date(2026, 9, 3)
    g = m.Group(5, "split", (21, 22))
    ents = [E(21, d, "-300", group_id=5), E(22, d, "-280", group_id=5)]
    near = run([L(1, d, "-300"), L(2, d, "-575")], ents, groups={5: g})  # −575 within 29 TWD of the −580 total
    assert near.claims == [] and [(c.line_id, c.kind) for c in near.cases] == [(1, "line_unmatched"), (2, "line_unmatched")]
    drift = run([L(1, d, "-300"), L(2, d + timedelta(days=12), "-580")], ents, groups={5: g})  # 12 days ≤ drift window
    assert drift.claims == [] and [(c.line_id, c.kind) for c in drift.cases][0] == (1, "line_unmatched")


def test_foreign_whole_group_member_is_not_claimed_by_a_twd_line_equal_to_it():
    g, members = _jpy_group()  # −660 (−3000 JPY) + −1320 (−6000 JPY); TWD −2500 is far from −1980: whole by foreign only
    day = date(2026, 9, 8)
    res = run([L(1, day, "-660"), L(2, day, "-2500", foreign_amount=D("9000"), foreign_currency="JPY")], members, groups={5: g})
    assert res.claims == [] and [(c.line_id, c.kind) for c in res.cases] == [(1, "line_unmatched"), (2, "amount_delta")]
    assert res.cases[1].context["group_id"] == 5


def test_whole_by_a_covered_reduced_foreign_reading_keeps_the_purchase_member_home():
    d = date(2026, 9, 3)
    g = m.Group(5, "split", (21, 22, 23))
    ents = [E(21, d, "-300", group_id=5, original_amount="-1500", original_currency="JPY"),
            E(22, d, "-280", group_id=5, original_amount="-1400", original_currency="JPY"),
            S(23, d, "-100", kind="fee", group_id=5, original_amount="-500", original_currency="JPY")]  # fee 23 owner-covered
    # covered lines: the fee member's own −100 fee line, and a sum line whose TWD (−900) is near no reading but whose
    # 2900 JPY is the reduced reading {21, 22}
    lines_all = [L(1, d, "-300"), L(2, d, "-900", foreign_amount=D("2900"), foreign_currency="JPY"), L(3, d, "-100", kind="fee")]
    res = run([L(1, d, "-300")], ents, groups={5: g}, lines_all=lines_all)
    assert res.claims == [] and [c.kind for c in res.cases] == ["line_unmatched"]


def test_income_split_on_a_bank_statement_is_whole_only_with_its_deposit_line():
    d = date(2026, 9, 3)
    g = m.Group(5, "split", (21, 22))
    ents = [E(21, d, "20000", kind="income", group_id=5), E(22, d, "10000", kind="income", group_id=5)]
    whole = run([L(1, d, "30000", kind="deposit"), L(2, d, "20000", kind="deposit")], ents, groups={5: g}, kind="bank")
    assert whole.claims == []
    apart = run([L(2, d, "20000", kind="deposit")], ents, groups={5: g}, kind="bank")
    assert apart.claims[0].representation.rows[0].entry_id == 21


def test_apart_member_foreign_near_and_drift_cases():
    d = date(2026, 9, 3)
    g = m.Group(5, "split", (21, 22))
    # 22 is large enough that no line is within the near tolerance (5 % of the total) of the -4000 / 20000 JPY total
    ents = [E(21, d, "-3000", group_id=5, original_amount="-15000", original_currency="JPY"),
            E(22, d, "-1000", group_id=5, original_amount="-5000", original_currency="JPY")]
    res = run([L(1, d, "-3010", foreign_amount=D("15000"), foreign_currency="JPY")], ents, groups={5: g})
    assert res.cases[0].kind == "amount_delta" and res.cases[0].candidates[0] == {"entry_id": 21, "reason": "foreign"}
    assert res.explained.get(21) != "deferred_next_period"
    g2 = m.Group(6, "split", (31, 32))
    ents2 = [E(31, d, "-300", group_id=6), E(32, d, "-280", group_id=6)]
    near = run([L(1, d, "-305")], ents2, groups={6: g2})
    assert near.cases[0].kind == "amount_delta" and near.cases[0].entry_id == 31
    drift = run([L(1, d + timedelta(days=6), "-300")], ents2, groups={6: g2})
    assert drift.cases[0].context.get("hint") == "date_drift" and drift.cases[0].entry_id == 31


def test_apart_member_with_discount_child_conserves_as_member_children():
    d = date(2026, 9, 3)
    g = m.Group(5, "split", (21, 22))
    ents = [E(21, d, "-300", group_id=5, children=[m.Child(210, "discount", D("20"))]), E(22, d, "-250", group_id=5)]  # distinct amounts: no ambiguity
    res = run([L(1, d, "-280"), L(2, d, "-250")], ents, groups={5: g})
    kinds = sorted(c.representation.kind for c in res.claims)
    assert kinds == ["member_alone", "member_children"]


def test_apart_member_and_standalone_twin_are_ambiguous():
    d = date(2026, 9, 3)
    g = m.Group(5, "split", (21, 22))
    ents = [E(21, d, "-300", group_id=5), E(22, d, "-280", group_id=5), E(30, d, "-300")]
    res = run([L(1, d, "-300", merchant="")], ents, groups={5: g})
    assert res.cases[0].kind == "ambiguous" and {(c["entry_id"], c["group_id"]) for c in res.cases[0].candidates} == {(21, None), (30, None)}


def test_group_readings_incomplete_is_structural_and_survives_covered_fee_members():
    # a standalone -100 twin gives the purchase line a reading: the incompleteness check runs only when one exists
    d = date(2026, 9, 3)
    g = m.Group(5, "split", tuple([21] + [30 + i for i in range(1, 10)]))
    fees = [E(30 + i, d, f"-{i}", kind="fee", group_id=5) for i in range(1, 10)]
    twin = E(90, d, "-100")
    lines = [L(1, d, "-100")] + [L(10 + i, d, f"-{i}", kind="fee") for i in range(1, 10)]
    res = run(lines, [E(21, d, "-100", group_id=5), twin] + fees, groups={5: g})
    blocked = [c for c in res.cases if c.line_id == 1]
    assert blocked and blocked[0].context.get("hint") == "group_readings_incomplete" and all(c.line_id != 1 for c in res.claims)
    # the fee members claimed on another statement (structural rows; their fee lines here stay claimable lines)
    structural = [dataclasses.replace(f, claimable=False, in_reverse=False) for f in fees]
    res2 = run(lines, [E(21, d, "-100", group_id=5), twin] + structural, groups={5: g})
    assert res2.claims == [] and res2.cases[0].context.get("hint") == "group_readings_incomplete"
    # the fee lines owner-covered by the fee members (spec §4.1 ruling: the claim side's aside is over the claimable
    # lines): no reading of the group exists on the claim side, the twin is the one reading and is claimed as before
    # the branch; the guard still reads the covered lines, so the group stays whole and 21 never stands alone
    owner = run([L(1, d, "-100")], [E(21, d, "-100", group_id=5), twin] + structural, groups={5: g}, lines_all=lines)
    assert [(c.line_id, [r.entry_id for r in c.representation.rows]) for c in owner.claims] == [(1, [90])]
    # without the twin the line has no reading: whole by fail-closed, so no member claim and a plain open line
    solo = run(lines, [E(21, d, "-100", group_id=5)] + fees, groups={5: g})
    assert all(c.line_id != 1 for c in solo.claims)
    assert [(c.kind, c.context) for c in solo.cases if c.line_id == 1] == [("line_unmatched", {})]
    solo2 = run([L(1, d, "-100")], [E(21, d, "-100", group_id=5)] + structural, groups={5: g}, lines_all=lines)
    assert solo2.claims == [] and [(c.line_id, c.kind, c.context) for c in solo2.cases] == [(1, "line_unmatched", {})]


@pytest.mark.parametrize("order", ["member_line_first", "sum_line_first"])
def test_guard_reads_the_claimable_children_view_beside_all_children(order):
    """Execution finding (Task 3, spec §3): child 211 is covered on another statement, so the claim side attaches only
    child 210 and reads −300 −280 +100 = −480, while the all-children total is −280. The guard must test the
    claimable-children total too: −480 marks the group whole, the −480 line claims `group_children` and the −300 line
    never claims the member alone, whatever the line order."""
    d = date(2026, 9, 3)
    k210, k211 = m.Child(210, "discount", D("100")), m.Child(211, "reward", D("200"))
    g = m.Group(5, "split", (21, 22))
    ents = [E(21, d, "-300", group_id=5, children=[k210], all_children=[k210, k211]), E(22, d, "-280", group_id=5)]
    member_id, sum_id = (1, 2) if order == "member_line_first" else (2, 1)
    lines = [L(member_id, d, "-300"), L(sum_id, d, "-480")]
    assert m.statement_context("card", lines, lines, ents, {5: g}, R, "TWD").whole == {5}
    res = run(lines, ents, groups={5: g})
    assert [(c.line_id, c.representation.kind, sorted(r.entry_id for r in c.representation.rows)) for c in res.claims] \
        == [(sum_id, "group_children", [21, 22, 210])]
    assert [(c.line_id, c.kind) for c in res.cases if c.line_id == member_id] == [(member_id, "line_unmatched")]


@pytest.mark.parametrize("order", ["member_line_first", "member_line_last"])
def test_guard_drops_a_printed_alone_child_from_the_claimable_children_view(order):
    """Review round 2 (spec §3): child 212 (+400) is covered elsewhere, so only `all_children` carries it; the +50
    child is printed alone by its refund line. The matcher reads −300 −280 +30 = −550 (claimable view minus the
    printed-alone +50), which no all-children combination yields (−100, −150): the guard enumerates the printed-alone
    combinations over both views, so −550 marks the group whole and the −300 line never claims member 21 alone."""
    d = date(2026, 9, 3)
    k50, k30, k400 = m.Child(210, "refund", D("50")), m.Child(211, "refund", D("30")), m.Child(212, "refund", D("400"))
    g = m.Group(5, "split", (21, 22))
    ents = [E(21, d, "-300", group_id=5, children=[k50, k30], all_children=[k50, k30, k400]),
            E(22, d, "-280", group_id=5)]
    member, refund, total = (1, 2, 3) if order == "member_line_first" else (3, 1, 2)
    lines = sorted([L(member, d, "-300"), L(refund, d, "50", kind="refund"), L(total, d, "-550")], key=lambda ln: ln.id)
    ctx = m.statement_context("card", lines, lines, ents, {5: g}, R, "TWD")
    assert ctx.whole == {5} and ctx.apart == frozenset()
    res = run(lines, ents, groups={5: g})
    assert sorted((c.line_id, c.representation.kind, sorted(r.entry_id for r in c.representation.rows))
                  for c in res.claims) == sorted([(refund, "child", [210]), (total, "group_children", [21, 22, 211])])
    assert [(c.line_id, c.kind) for c in res.cases if c.line_id == member] == [(member, "line_unmatched")]


@pytest.mark.parametrize("order", ["member_line_first", "sum_line_first"])
def test_guard_reads_a_superset_the_subset_sum_pruning_drops(order):
    """PR #130 review P1 (spec §3): fee 23 and refund 24 are printed alone by guard-only lines (covered elsewhere), so
    `aside_all` = {23, 24} while the claim side's `aside` = ∅. The pruned guard variants drop {21, 22, 23, 24} (net
    −1000, equal to {21, 22}) before children are added, so its child-inclusive −1100 — the claim side's
    `group_children` reading — was never tested and the group went apart. The guard enumerates unpruned member
    readings: −1100 marks the group whole and the −600 line never claims member 21 alone, whatever the line order."""
    d = date(2026, 9, 3)
    k31 = m.Child(31, "fee", D("-100"))
    g = m.Group(5, "split", (21, 22, 23, 24))
    ents = [E(21, d, "-600", group_id=5), E(22, d, "-400", group_id=5),
            E(23, d, "-200", kind="fee", group_id=5, children=[k31]), E(24, d, "200", kind="refund", group_id=5)]
    member_id, sum_id = (1, 2) if order == "member_line_first" else (2, 1)
    lines = sorted([L(member_id, d, "-600"), L(sum_id, d, "-1100")], key=lambda ln: ln.id)
    lines_all = lines + [L(3, d, "-200", kind="fee"), L(4, d, "200", kind="refund")]
    ctx = m.statement_context("card", lines, lines_all, ents, {5: g}, R, "TWD")
    assert (ctx.aside, ctx.aside_all, ctx.whole, ctx.apart) == (frozenset(), {23, 24}, {5}, frozenset())
    res = run(lines, ents, groups={5: g}, lines_all=lines_all)
    assert [(c.line_id, c.representation.kind, sorted(r.entry_id for r in c.representation.rows)) for c in res.claims] \
        == [(sum_id, "group_children", [21, 22, 23, 24, 31])]
    assert [(c.line_id, c.kind) for c in res.cases] == [(member_id, "line_unmatched")]


def test_guard_reads_a_zero_sum_pair_of_guard_only_members_with_children():
    """PR #130 review P1: fee 23 (−50, child −20) and reward 24 (+50) sum to zero and are printed alone by guard-only
    lines; the purchase line prints every member and the child (−320). Pruning would drop the full reading before its
    child is added (nearest pruned totals −300, −350, −370: outside the near tolerance); unpruned, it is whole."""
    d = date(2026, 9, 3)
    g = m.Group(5, "split", (21, 22, 23, 24))
    ents = [E(21, d, "-200", group_id=5), E(22, d, "-100", group_id=5),
            E(23, d, "-50", kind="fee", group_id=5, children=[m.Child(31, "fee", D("-20"))]),
            E(24, d, "50", kind="reward", group_id=5)]
    lines = [L(1, d, "-200"), L(2, d, "-320")]
    lines_all = lines + [L(3, d, "-50", kind="fee"), L(4, d, "50", kind="reward")]
    ctx = m.statement_context("card", lines, lines_all, ents, {5: g}, R, "TWD")
    assert (ctx.aside_all, ctx.whole, ctx.apart) == ({23, 24}, {5}, frozenset())
    res = run(lines, ents, groups={5: g}, lines_all=lines_all)
    assert [(c.line_id, c.representation.kind, sorted(r.entry_id for r in c.representation.rows)) for c in res.claims] \
        == [(2, "group_children", [21, 22, 23, 24, 31])]


def test_group_variants_prunes_by_default_and_not_for_the_guard():
    """The claim side's default (`prune=True`) drops a reading a strict subset of which sums to the same amount; the
    guard's `prune=False` keeps every combination (spec §3 "Group readings for the guard")."""
    d = date(2026, 9, 3)
    g = m.Group(7, "split", (31, 32, 33))
    ents = [E(31, d, "-100", group_id=7), E(32, d, "-10", kind="fee", group_id=7), E(33, d, "10", kind="reward", group_id=7)]
    by_id = {e.id: e for e in ents}
    ids = lambda res: (sorted(sorted(mem.id for mem in r) for r in res[0]), res[1])
    assert ids(m._group_variants(g, by_id, set(), frozenset({32, 33}))) == ([[31], [31, 32], [31, 33]], True)
    assert ids(m._group_variants(g, by_id, set(), frozenset({32, 33}), prune=False)) \
        == ([[31], [31, 32], [31, 32, 33], [31, 33]], True)
