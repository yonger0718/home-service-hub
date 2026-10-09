from app.services.statements import lineage
from app.services.statements.lineage import New, Old


def O(i, lk, ck, m, kf=("k",)):
    return Old(line_id=i, event_id=100 + i, logical_key=lk, canonical_key=ck, merchant_norm=m, kind_fields=kf)


def N(i, lk, ck, m, kf=("k",)):
    return New(index=i, logical_key=lk, canonical_key=ck, merchant_norm=m, kind_fields=kf)


def test_identical_pairs_by_logical_then_canonical():
    out = lineage.pair([O(1, "d|-580|0", "c1", "全聯")], [N(0, "d|-580|0", "c1", "全聯")])
    assert [(p.old.line_id, p.new.index, p.equivalence) for p in out] == [(1, 0, "identical")]


def test_whitespace_change_is_normalised_not_changed():
    out = lineage.pair([O(1, "d|-580|0", "c1", "PAYPAL SPOTIFY")], [N(0, "d|-580|0", "c2", "PAYPAL  SPOTIFY")])
    assert out[0].equivalence == "normalised"


def test_token_equal_but_canonical_differs_is_normalised():
    out = lineage.pair([O(1, "d|-580|0", "c1", "PAYPAL SPOTIFY")], [N(0, "d|-580|0", "c2", "SPOTIFY PAYPAL")])
    assert out[0].equivalence == "normalised"


def test_merchant_change_is_changed():
    out = lineage.pair([O(1, "d|-580|0", "c1", "PAYPAL SPOTIFY")], [N(0, "d|-580|0", "c2", "PAYPAL NETFLIX")])
    assert out[0].equivalence == "changed" and out[0].old.line_id == 1 and out[0].new.index == 0


def test_identical_twins_reordered_pair_in_print_order():
    old = [O(1, "d|-580|0", "c", "A"), O(2, "d|-580|1", "c", "A")]
    new = [N(0, "d|-580|0", "c", "A"), N(1, "d|-580|1", "c", "A")]
    out = lineage.pair(old, new)
    assert [(p.old.line_id, p.new.index, p.equivalence) for p in out] == [(1, 0, "identical"), (2, 1, "identical")]
    assert lineage.twin_count_changed(old, new) is False


def test_twin_count_change_is_flagged():
    old = [O(1, "d|-580|0", "c", "A"), O(2, "d|-580|1", "c", "A")]
    new = [N(0, "d|-580|0", "c", "A")]
    out = lineage.pair(old, new)
    assert [p.equivalence for p in out] == ["identical", "unpaired"]
    assert lineage.twin_count_changed(old, new) is True


def test_new_line_without_old_is_unpaired_new():
    out = lineage.pair([], [N(0, "d|-1|0", "c", "A")])
    assert out[0].old is None and out[0].new.index == 0 and out[0].equivalence == "unpaired"
