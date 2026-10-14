from worker import ocr_text as t


def box(x, y, w, h, text):
    return (float(x), float(y), float(w), float(h), text)


def test_rows_group_by_centre_with_mixed_heights():
    rows = t.rows_of([box(10, 100, 60, 20, "A"), box(400, 102, 80, 16, "B"), box(10, 140, 60, 28, "C"), box(400, 146, 60, 16, "D")])
    assert [[b[4] for b in r] for r in rows] == [["A", "B"], ["C", "D"]]


def test_layout_orders_left_to_right_and_pads_by_gap():
    out = t.layout([box(400, 102, 80, 16, "1,234"), box(10, 100, 60, 20, "2026/09/01"), box(120, 100, 150, 20, "COFFEE"),
                    box(10, 140, 60, 28, "商店"), box(400, 146, 60, 16, "56")])
    lines = out.split("\n")
    assert lines[0].startswith("2026/09/01") and "COFFEE" in lines[0] and lines[0].endswith("1,234")
    assert lines[1].startswith("商店") and lines[1].endswith("56")
    gap = lines[0][lines[0].index("COFFEE") + 6:lines[0].index("1,234")]
    assert 2 <= len(gap) <= 12 and gap.strip() == ""


def test_layout_pads_at_least_one_space_and_at_most_twelve():
    touching = t.layout([box(0, 0, 10, 10, "a"), box(10, 0, 10, 10, "b")])
    far = t.layout([box(0, 0, 10, 10, "a"), box(5000, 0, 10, 10, "b")])
    assert touching == "a b" and far == "a" + " " * 12 + "b"


def test_layout_empty_and_version():
    assert t.layout([]) == "" and t.LAYOUT_VERSION == "1"
