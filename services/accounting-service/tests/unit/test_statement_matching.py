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
                   children=tuple(kw.get("children", ())), instance=kw.get("instance"), in_reverse=kw.get("in_reverse", True))


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


def test_same_day_same_amount_different_merchants_is_ambiguous():
    # text weighs at most 0.10, below the 0.15 margin: on equal dates ambiguity stops automation (§6.3)
    res = run([L(1, date(2026, 9, 3), "-580", merchant="全聯 大安")], [E(10, date(2026, 9, 3), "-580", merchant="全聯 大安"), E(11, date(2026, 9, 3), "-580", merchant="家樂福")])
    assert res.claims == [] and res.cases[0].kind == "ambiguous" and {c["entry_id"] for c in res.cases[0].candidates} == {10, 11}


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
