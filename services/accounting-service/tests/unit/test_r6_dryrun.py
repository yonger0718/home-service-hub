import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("r6_dryrun", Path(__file__).parents[2] / "scripts" / "r6_dryrun.py")
dr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dr)


def _dump(statements, watermark=(1, 1, 1), sha="x"):
    return {"sha": sha, "watermark": list(watermark), "statements": statements}


def test_diff_attributes_each_change_to_a_rule():  # uses the single _dump helper above
    before = _dump({"1": {"claims": [], "cases": [[11, "ambiguous", None, [5], None], [12, "line_unmatched", None, [], None],
                                               [13, "line_unmatched", None, [], None], [14, "line_unmatched", None, [], None],
                                               [15, "amount_delta", None, [8], 8], [16, "ambiguous", None, [5], None]]}},
                   sha="base")
    after = _dump({"1": {"claims": [[11, "exact", [5]], [12, "exact", [6]]],
                         "cases": [[13, "amount_delta", "date_drift", [7], 7], [14, "line_unmatched", "bank_only", [], None],
                                   [15, "amount_delta", None, [8, 9], 8], [16, "line_unmatched", "claimed_by_earlier_line", [], None]]}},
                  sha="cand")
    changes = dr.diff_dumps(before, after)
    by_event = {c["event_id"]: dr.attribute(c) for c in changes}
    assert by_event == {11: "R6a.2 single-candidate ambiguous now claims", 12: "R6a.1/R6a.3 new exact claim",
                        13: "R6a.5 date_drift proposal", 14: "R6a.6 bank_only by kind", 15: "R6a.4 near candidates changed",
                        16: "R6a.1 claimed by an earlier line"}
    assert dr.gate_failures(changes, after, before) == []


def test_gate_fails_on_lost_claim_single_candidate_ambiguous_unattributed_or_moved_watermark():
    before = _dump({"1": {"claims": [[11, "exact", [5]]], "cases": [[12, "line_unmatched", None, [], None]]}}, sha="base")
    after = _dump({"1": {"claims": [], "cases": [[11, "ambiguous", None, [5], None], [12, "recheck", None, [], None]]}})
    changes = dr.diff_dumps(before, after)
    assert dr.gate_failures(changes, after, before) == ["event 11: claim lost", "event 11: ambiguous with fewer than two candidates",
                                                         "event 12: unattributed change"]
    # a group representation counts as a candidate: standalone + split group stays a legal two-candidate ambiguous case
    legal = _dump({"1": {"claims": [], "cases": [[11, "ambiguous", None, [5, "g7"], None]]}})
    legal_base = _dump(legal["statements"], sha="base")
    assert dr.gate_failures([], legal, legal_base) == []
    clean = _dump({"1": {"claims": [], "cases": []}}, sha="cand")
    assert dr.gate_failures([], clean, _dump(clean["statements"], sha="base-dirty")) == ["baseline dump came from a dirty tree (sha base-dirty)"]
    assert dr.gate_failures([], _dump(clean["statements"], sha="cand-dirty"), _dump(clean["statements"], sha="base")) == [
        "candidate dump came from a dirty tree (sha cand-dirty)"]
    moved = _dump({"1": {"claims": [], "cases": []}}, watermark=(2, 1, 1))
    assert dr.gate_failures([], moved, legal_base) == ["database changed between the dumps (watermark 1,1,1 → 2,1,1)"]
    same_tree = _dump({"1": {"claims": [], "cases": []}}, sha="x")
    assert dr.gate_failures([], same_tree, _dump({"1": {"claims": [], "cases": []}}, sha="x")) == ["both dumps came from the same tree (sha x)"]


def test_exact_claim_moving_to_another_entry_fails_the_gate_but_rule_change_alone_does_not():
    before = _dump({"1": {"claims": [[21, "exact", [5]], [22, "exact", [8, 9]]], "cases": []}}, sha="base")
    after = _dump({"1": {"claims": [[21, "exact", [6]], [22, "exact_children", [8, 9]]], "cases": []}})
    changes = dr.diff_dumps(before, after)
    by_event = {c["event_id"]: dr.attribute(c) for c in changes}
    assert by_event == {21: "R6a.1 claim moved to another entry", 22: "claim rule changed"}
    assert dr.gate_failures(changes, after, before) == ["event 21: claim changed"]


def test_waive_moved_drops_only_moved_claim_failures():
    changes = [{"statement_id": 1, "event_id": 21, "before": ("claim", "exact", (5,)), "after": ("claim", "exact", (6,))},
               {"statement_id": 1, "event_id": 23, "before": ("claim", "exact", (7,)),
                "after": ("case", "line_unmatched", "claimed_by_earlier_line", (), None)},
               {"statement_id": 1, "event_id": 24, "before": ("claim", "exact", (9,)), "after": ("case", "recheck", None, (), None)}]
    failures = ["event 21: claim changed", "event 23: claim lost", "event 24: claim lost", "event 25: unattributed change"]
    assert dr.waive_moved(failures, changes) == ["event 24: claim lost", "event 25: unattributed change"]


def test_gate_covers_claims_of_every_rule_not_only_exact():
    before = _dump({"1": {"claims": [[31, "group", [5, 6]], [32, "payment", [7]], [33, "transfer", [8]], [34, "refund", [9]]],
                          "cases": []}}, sha="base")
    after = _dump({"1": {"claims": [[33, "transfer", [10]]],
                         "cases": [[31, "ambiguous", None, [5, "g2"], None],
                                   [32, "line_unmatched", "claimed_by_earlier_line", [], None],
                                   [34, "recheck", None, [], None]]}})
    changes = dr.diff_dumps(before, after)
    failures = dr.gate_failures(changes, after, before)
    assert "event 31: claim lost" in failures and "event 32: claim lost" in failures
    assert "event 33: claim changed" in failures and "event 34: claim lost" in failures
    # waived only for the moved shapes: the transfer moved to another entry, the payment claimed by an earlier line
    waived = dr.waive_moved(failures, changes)
    assert "event 33: claim changed" not in waived and "event 32: claim lost" not in waived
    assert "event 31: claim lost" in waived and "event 34: claim lost" in waived


def test_policy_summary_counts_by_account_action_and_decision():
    after = _dump({"1": {"claims": [], "cases": []}})
    after["policy"] = {"1": [{"action": "backfill_entry", "kind": "expense", "amount_bucket": "<1000", "decision": "apply"},
                             {"action": "backfill_entry", "kind": "income", "amount_bucket": "<5000", "decision": "propose:max_amount"},
                             {"action": "anchor_opening", "kind": None, "amount_bucket": ">=5000", "decision": "disabled"}]}
    after.update({"anchor_blockers": [7], "anchor_above_earlier_statement": [3, 9], "needs_peer": {"3": 2, "12": 1}, "owner_deleted": {"1": 1},
                  "retirements": {"1": {"backfill_entry": 1, "create_system_entry": 2}},
                  "sweep": {"would_release_old": 5, "would_release_new": 2, "kept_by_key": {"name": 2, "merchant": 1}, "released_by_key": {"amount": 2}},
                  "structural_keys": ["amount", "flow"], "accounts": {"1": 3}})
    text = dr.policy_summary(after)
    assert "| 3 | backfill_entry | apply | 1 |" in text and "| 3 | backfill_entry | propose:max_amount | 1 |" in text
    assert "| 3 | anchor_opening | disabled | 1 |" in text
    assert "anchor blocked by closing adjustments: 1 account\n" in text
    assert "anchor above an earlier statement: 2 accounts\n" in text
    assert "needs_peer lines: 3 (by account: 3=2, 12=1)\n" in text
    assert "retirements pending: backfill_entry 1, create_system_entry 2" in text
    assert "sweep: old rule would release 5 lines, new rule 2 (kept by key: name 2, merchant 1; released by key: amount 2)" in text
    assert "structural keys: amount, flow" in text


def test_policy_summary_without_needs_peer_or_late_anchors_says_none():
    after = _dump({"1": {"claims": [], "cases": []}})
    after["policy"] = {}
    text = dr.policy_summary(after)
    assert "anchor above an earlier statement: 0 accounts\n" in text and "needs_peer lines: 0 (by account: none)\n" in text


def _case_dump():
    spec = importlib.util.spec_from_file_location("r6_case_dump", Path(__file__).parents[2] / "scripts" / "r6_case_dump.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("amount, bucket", [("0", "<1000"), ("999.99", "<1000"), ("1000", "<5000"), ("4999.99", "<5000"),
                                            ("5000", ">=5000"), ("-999.99", "<1000"), ("-1000", "<5000"), ("-5000", ">=5000")])
def test_bucket_boundaries(amount, bucket):
    from decimal import Decimal
    assert _case_dump()._bucket(Decimal(amount)) == bucket


def test_main_policy_refuses_a_dump_without_a_policy_section(tmp_path, monkeypatch, capsys):
    plain = _dump({"1": {"claims": [], "cases": []}})
    for name in ("before.json", "after.json"):
        (tmp_path / name).write_text(json.dumps(plain))
    monkeypatch.setattr("sys.argv", ["r6_dryrun.py", "--policy", "--baseline", str(tmp_path / "before.json"),
                                     "--candidate", str(tmp_path / "after.json"), "--out-dir", str(tmp_path / "out")])
    assert dr.main() != 0
    err = capsys.readouterr().err
    assert "no policy section" in err and "r6_case_dump.py --policy" in err
    assert not (tmp_path / "out").exists()


def test_policy_summary_counts_partially_stranded_actions():
    after = _dump({"1": {"claims": [], "cases": []}})
    after["policy"] = {}
    after["action_partially_stranded"] = {"1": 2, "4": 1}
    assert "action_partially_stranded: 3 (by statement: 1=2, 4=1)" in dr.policy_summary(after)
    del after["action_partially_stranded"]
    assert "action_partially_stranded: 0 (by statement: none)" in dr.policy_summary(after)


def test_attribute_labels_transfer_delta_before_the_generic_near_branch():
    case = lambda hint, kind="amount_delta": ("case", kind, hint, (7,), 7)
    new = {"before": None, "after": case("transfer_delta")}
    assert dr.attribute(new) == "R6c.1 transfer_delta"
    from_peer = {"before": case("needs_peer", "line_unmatched"), "after": case("transfer_delta")}
    assert dr.attribute(from_peer) == "R6c.1 transfer_delta"
    back = {"before": case("transfer_delta"), "after": case("needs_peer", "line_unmatched")}
    assert dr.attribute(back) == "R6c.1 transfer_delta cleared"
    assert dr.attribute({"before": None, "after": case(None)}) == "R6a.4 near proposal"
    assert dr.attribute({"before": case(None, "line_unmatched"), "after": case("needs_peer", "line_unmatched")}) == "unattributed"


def test_transfers_summary_is_counts_only_per_account():
    after = _dump({"1": {"claims": [], "cases": []}}, sha="cand")
    after.update({"transfer_delta": {"3": 2, "12": 1}, "needs_peer": {"3": 1, "9": 4}})
    text = dr.transfers_summary(after)
    assert "| 3 | 2 | 1 |" in text and "| 12 | 1 | 0 |" in text and "| 9 | 0 | 4 |" in text
    assert "| account | transfer_delta cases | needs_peer cases |" in text
    assert "cand" in text and "total: transfer_delta 3, needs_peer 5" in text


def test_main_transfers_writes_private_summary_and_exits_zero(tmp_path, monkeypatch):
    before = _dump({"1": {"claims": [], "cases": []}}, sha="base")
    after = _dump({"1": {"claims": [[1, "exact", [5]]], "cases": []}}, sha="cand")
    after.update({"transfer_delta": {"3": 1}, "needs_peer": {}})
    # a gate-failing pair (watermark differs) still exits 0 under --transfers
    after["watermark"] = [9, 9, 9]
    for name, d in (("before.json", before), ("after.json", after)):
        (tmp_path / name).write_text(json.dumps(d))
    out = tmp_path / "out"
    monkeypatch.setattr("sys.argv", ["r6_dryrun.py", "--transfers", "--baseline", str(tmp_path / "before.json"),
                                     "--candidate", str(tmp_path / "after.json"), "--out-dir", str(out)])
    assert dr.main() == 0
    path = out / "transfers-summary.md"
    assert path.stat().st_mode & 0o777 == 0o600 and "| 3 | 1 | 0 |" in path.read_text()
    assert "gate: FAIL" in (out / "summary.md").read_text()  # the verdict is still recorded


def test_main_transfers_refuses_a_dump_without_the_section(tmp_path, monkeypatch):
    plain = _dump({"1": {"claims": [], "cases": []}})
    for name in ("before.json", "after.json"):
        (tmp_path / name).write_text(json.dumps(plain))
    monkeypatch.setattr("sys.argv", ["r6_dryrun.py", "--transfers", "--baseline", str(tmp_path / "before.json"),
                                     "--candidate", str(tmp_path / "after.json"), "--out-dir", str(tmp_path / "out")])
    assert dr.main() == 2
    assert not (tmp_path / "out").exists()
