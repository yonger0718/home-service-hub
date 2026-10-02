from app.services.moze_csv import parse_moze_csv
from app.services.transfer_pairing import pair_transfers


def _parse(moze, *rows):
    openings = [moze.opening(name, cur, "0") for name, cur in (("A", "TWD"), ("B", "TWD"), ("C", "TWD"), ("J", "JPY"))]
    return parse_moze_csv(moze.csv(*openings, *rows)).rows


def test_interleaved_transfers_in_one_minute_pair_by_amount(moze):
    plus_30000, minus_30000, plus_7500, minus_7500 = _parse(
        moze,
        moze.row("A", "TWD", "轉入", "30000"),
        moze.row("B", "TWD", "轉出", "-30000"),
        moze.row("C", "TWD", "轉入", "7500"),
        moze.row("A", "TWD", "轉出", "-7500"),
    )
    result = pair_transfers([plus_30000, minus_30000, plus_7500, minus_7500])
    groups = result.group_by_row
    assert groups[minus_30000.row_no] == groups[plus_30000.row_no]
    assert groups[minus_7500.row_no] == groups[plus_7500.row_no]
    assert groups[minus_30000.row_no] != groups[plus_7500.row_no]
    assert result.unpaired_rows == ()
    assert result.pass_counts == (0, 2, 0)


def test_adjacent_opposite_pair_uses_pass_one(moze):
    rows = _parse(moze, moze.row("A", "TWD", "轉出", "-100"), moze.row("B", "TWD", "轉入", "100"))
    result = pair_transfers(rows)
    assert result.pass_counts == (1, 0, 0)
    assert len(set(result.group_by_row.values())) == 1


def test_non_adjacent_transfer_is_paired_by_amount(moze):
    rows = _parse(
        moze,
        moze.row("A", "TWD", "轉出", "-22000"),
        moze.row("A", "TWD", "支出", "-1", main="飲食"),
        moze.row("A", "TWD", "支出", "-2", main="飲食"),
        moze.row("B", "TWD", "轉入", "22000"),
    )
    result = pair_transfers(rows)
    out_row, in_row = rows[0].row_no, rows[3].row_no
    assert result.group_by_row[out_row] == result.group_by_row[in_row]
    assert result.pass_counts == (0, 1, 0)


def test_cross_currency_transfer_is_paired_when_unique(moze):
    rows = _parse(moze, moze.row("A", "TWD", "轉出", "-10000"), moze.row("J", "JPY", "轉入", "46000"))
    result = pair_transfers(rows)
    assert result.group_by_row[rows[0].row_no] == result.group_by_row[rows[1].row_no]
    assert result.pass_counts == (0, 0, 1)


def test_ambiguous_candidates_leave_all_legs_unpaired(moze):
    rows = _parse(
        moze,
        moze.row("A", "TWD", "轉出", "-500"),
        moze.row("B", "TWD", "轉出", "-500"),
        moze.row("C", "TWD", "支出", "-1", main="飲食"),
        moze.row("A", "TWD", "轉入", "500"),
        moze.row("C", "TWD", "支出", "-1", main="飲食"),
        moze.row("B", "TWD", "轉入", "500"),
    )
    result = pair_transfers(rows)
    assert result.group_by_row == {}
    assert len(result.unpaired_rows) == 4


def test_one_sided_uniqueness_is_not_enough(moze):
    rows = _parse(
        moze,
        moze.row("A", "TWD", "轉出", "-500"),
        moze.row("B", "TWD", "轉出", "-500"),
        moze.row("C", "TWD", "支出", "-1", main="飲食"),
        moze.row("C", "TWD", "轉入", "500"),
    )
    result = pair_transfers(rows)
    assert result.group_by_row == {}
    assert len(result.unpaired_rows) == 3


def test_same_currency_legs_that_are_not_exact_negations_stay_unpaired(moze):
    rows = _parse(moze, moze.row("A", "TWD", "轉出", "-500"), moze.row("B", "TWD", "轉入", "499"))
    result = pair_transfers(rows)
    assert result.group_by_row == {}
    assert len(result.unpaired_rows) == 2


def test_different_minute_is_never_paired(moze):
    rows = _parse(
        moze,
        moze.row("A", "TWD", "轉出", "-500", time="10:00"),
        moze.row("B", "TWD", "轉入", "500", time="10:01"),
    )
    assert pair_transfers(rows).group_by_row == {}


def test_pass_one_adjacency_counts_records_not_lines(moze):
    # Review focus: a multi-line 描述 on the 轉出 must not break adjacency.
    rows = _parse(
        moze,
        moze.row("A", "TWD", "轉出", "-100", description='"first line\nsecond line"'),
        moze.row("B", "TWD", "轉入", "100"),
    )
    assert pair_transfers(rows).pass_counts == (1, 0, 0)
