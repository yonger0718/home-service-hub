from decimal import Decimal

import pytest

from app.services import inbox_service as ib


def test_pattern_strips_digit_runs_and_collapses_space():
    assert ib.pattern("放款還款 20260925 001") == "放款還款"
    assert ib.pattern("ＡＰＰＬＥ 1234  5678 TW") == "ＡＰＰＬＥ TW"


def test_pattern_only_digits_is_empty():
    assert ib.pattern("20260925") == ""


@pytest.mark.parametrize("kind,status,skind,hint,merchant,allowed,expected", [
    ("line_unmatched", "open", "bank", None, "交割股款 1234", ["create_entry", "dismiss"], "settlement"),
    ("line_unmatched", "proposed", "bank", None, "交割股款", [], "settlement"),
    ("line_unmatched", "proposed", "card", None, "全聯", [], "proposed"),
    ("line_unmatched", "open", "bank", "needs_peer", "放款還款", ["match", "dismiss"], "transfer"),
    ("line_unmatched", "open", "card", None, "全聯", ["match", "create_entry", "dismiss"], "bank_only"),
    ("line_unmatched", "open", "card", "installment_unmapped", "分期", ["match", "link_installment_plan", "dismiss"], "other"),
    ("entry_unmatched", "open", "card", None, None, ["defer_next_period", "dismiss"], "ledger_only"),
    ("amount_delta", "open", "card", "date_drift", "自動扣繳", ["match", "dismiss"], "date_differs"),
    ("amount_delta", "open", "bank", None, "卡費", ["match", "adjust_amount", "dismiss"], "amount_differs"),
    ("ambiguous", "open", "card", None, "全聯", ["match", "dismiss"], "ambiguous"),
    ("balance_gap", "open", "bank", None, None, ["anchor_opening", "dismiss"], "balance_gap"),
    ("duplicate_claim", "open", "card", None, "全聯", ["unmatch", "dismiss"], "other"),
])
def test_classify(kind, status, skind, hint, merchant, allowed, expected):
    assert ib.classify(kind=kind, status=status, statement_kind=skind, hint=hint, merchant_norm=merchant,
                       allowed=allowed) == expected


def test_folded_set():
    assert ib.FOLDED == {"proposed", "balance_gap", "settlement", "other"}


def test_group_key_merges_same_pattern_and_amount_only():
    k = lambda m, a, acct=1, kind="purchase": ib.group_key("bank_only", acct, kind, m, Decimal(a))
    assert k("放款還款 0925", "-3865") == k("放款還款 1025", "-3865")
    assert k("放款還款", "-3865") != k("放款還款", "-3870")
    assert k("轉帳支取", "-20637") != k("轉帳支取", "-5000")
    assert k("放款還款", "-3865") != k("放款還款", "-3865", acct=2)
    assert k("放款還款", "-3865") != k("放款還款", "3865")
    assert ib.group_key("transfer", 1, "payment", "放款還款", Decimal("-3865")) is None
    assert ib.group_key("ledger_only", 1, "purchase", "x", Decimal("-1")) is None


def test_group_key_empty_pattern_never_merges():
    assert ib.group_key("bank_only", 1, "purchase", "20260925", Decimal("-100")) is None


@pytest.mark.parametrize("question,allowed,adjustable,expected", [
    ("bank_only", ["match", "create_entry", "create_system_entry", "backfill_entry", "dismiss"], False, ["record", "match_candidate", "dismiss"]),
    ("transfer", ["match", "dismiss"], False, ["match_candidate", "dismiss"]),
    ("ledger_only", ["defer_next_period", "move_account", "dismiss"], False, ["defer", "dismiss"]),
    ("amount_differs", ["match", "adjust_amount", "split_entry", "dismiss"], True, ["accept_bank_amount", "match_candidate", "dismiss"]),
    ("amount_differs", ["match", "adjust_amount", "dismiss"], False, ["match_candidate", "dismiss"]),
    ("date_differs", ["match", "dismiss"], False, ["match_candidate", "dismiss"]),
    ("balance_gap", ["balance_adjustment_asof", "anchor_opening", "dismiss"], False, ["anchor", "dismiss"]),
    ("balance_gap", ["balance_adjustment_asof", "dismiss"], False, ["dismiss"]),
    ("proposed", [], False, ["apply_proposal", "reject_proposal"]),
    ("settlement", ["create_entry", "dismiss"], False, []),
    ("other", ["unmatch", "dismiss"], False, ["dismiss"]),
])
def test_buttons(question, allowed, adjustable, expected):
    assert ib.buttons(question, allowed, adjustable=adjustable) == expected


def test_learnable_and_flow_sign():
    assert ib.learnable("bank_only", "record") and ib.learnable("bank_only", "dismiss")
    assert ib.learnable("amount_differs", "accept_bank_amount")
    assert not ib.learnable("bank_only", "match_candidate") and not ib.learnable("ledger_only", "dismiss")
    assert ib.flow_sign(Decimal("-1")) == "-" and ib.flow_sign(Decimal("0")) == "+"
