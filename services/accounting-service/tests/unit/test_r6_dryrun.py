import importlib.util
from pathlib import Path

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
    assert dr.gate_failures(changes, after, before) == ["event 11: exact claim lost", "event 11: ambiguous with fewer than two candidates",
                                                         "event 12: unattributed change"]
    # a group representation counts as a candidate: standalone + split group stays a legal two-candidate ambiguous case
    legal = _dump({"1": {"claims": [], "cases": [[11, "ambiguous", None, [5, "g7"], None]]}})
    legal_base = _dump(legal["statements"], sha="base")
    assert dr.gate_failures([], legal, legal_base) == []
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
    assert dr.gate_failures(changes, after, before) == ["event 21: exact claim changed"]


def test_waive_moved_drops_only_moved_claim_failures():
    changes = [{"statement_id": 1, "event_id": 21, "before": ("claim", "exact", (5,)), "after": ("claim", "exact", (6,))},
               {"statement_id": 1, "event_id": 23, "before": ("claim", "exact", (7,)),
                "after": ("case", "line_unmatched", "claimed_by_earlier_line", (), None)},
               {"statement_id": 1, "event_id": 24, "before": ("claim", "exact", (9,)), "after": ("case", "recheck", None, (), None)}]
    failures = ["event 21: exact claim changed", "event 23: exact claim lost", "event 24: exact claim lost", "event 25: unattributed change"]
    assert dr.waive_moved(failures, changes) == ["event 24: exact claim lost", "event 25: unattributed change"]
