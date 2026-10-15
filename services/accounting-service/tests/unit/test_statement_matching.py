import dataclasses
from datetime import date
from decimal import Decimal

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
                   claimable=kw.get("claimable", True))


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
