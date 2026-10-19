import dataclasses
from datetime import date
from decimal import Decimal

import pytest

from app.services.statements import matching as m

D = Decimal
R = m.Rules(period_end=date(2026, 9, 30))


def L(i, posted, flow, kind="purchase", merchant="全聯", **kw):
    return m.Line(id=i, event_id=100 + i, posted_date=posted, txn_date=kw.get("txn_date"), flow=D(flow),
                  foreign_amount=kw.get("foreign_amount"), foreign_currency=kw.get("foreign_currency"), line_kind=kind,
                  merchant_norm=merchant, installment_seq=kw.get("seq"), installment_total=kw.get("total"))


def E(i, posted, flow, kind="expense", merchant="全聯", **kw):
    return m.Entry(id=i, kind=kind, flow=D(flow), posted_date=posted, entry_date=kw.get("entry_date", posted),
                   name=kw.get("name"), merchant=merchant, original_amount=kw.get("original_amount"),
                   original_currency=kw.get("original_currency"), account_id=kw.get("account_id", 1),
                   group_id=kw.get("group_id"), parent_entry_id=kw.get("parent_entry_id"),
                   is_settlement=kw.get("is_settlement", False), refunds_entry_id=kw.get("refunds_entry_id"),
                   transfer_group_id=kw.get("transfer_group_id"), transfer_peer_is_card=kw.get("peer_is_card"),
                   children=tuple(kw.get("children", ())), instance=kw.get("instance"), in_reverse=kw.get("in_reverse", True),
                   claimable=kw.get("claimable", True), stand_in=kw.get("stand_in", False))


def run(lines, entries, groups=None, plan_map=None, fee=lambda e: None, kind="card"):
    return m.match(kind, lines, entries, groups or {}, plan_map or {}, fee, R, "TWD")


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


def test_partial_group_is_never_admissible():
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

def test_bank_withdrawal_respects_split_groups():
    g = m.Group(id=5, kind="split", member_ids=(21, 22))
    entries = [E(21, date(2026, 9, 3), "-300", group_id=5), E(22, date(2026, 9, 3), "-280", group_id=5)]
    res = run([L(1, date(2026, 9, 3), "-300", kind="withdrawal")], entries, groups={5: g}, kind="bank")
    assert res.claims == [] and res.cases[0].kind == "line_unmatched"
    res2 = run([L(1, date(2026, 9, 3), "-580", kind="withdrawal")], entries, groups={5: g}, kind="bank")
    assert res2.claims[0].representation.rule == "group" and sorted(r.entry_id for r in res2.claims[0].representation.rows) == [21, 22]


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
    assert deposit.claims == [] and deposit.cases[0].kind == "line_unmatched"  # a deposit never takes a member alone
    fee = run([L(1, date(2026, 9, 3), "-20", kind="fee")], members, groups={9: g}, kind="bank")
    assert [sorted(r.entry_id for r in c.representation.rows) for c in fee.claims] == [[52]]


def test_a_split_member_never_stands_alone_for_a_purchase_line():
    g, members = _fee_group()
    res = run([L(1, date(2026, 9, 3), "-300")], members, groups={6: g})
    assert res.claims == [] and res.cases[0].kind == "line_unmatched"
    res2 = run([L(1, date(2026, 9, 3), "-10")], members, groups={6: g})  # the fee member, for a purchase line
    assert res2.claims == [] and res2.cases[0].kind == "line_unmatched"


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
    # Review Focus 5: an entry claimed by an earlier line and a group member never appear. Line 2 sits 4 days from the
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
