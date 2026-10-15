"""R5a: rows from OCR boxes. Purely geometric; identifier normalisation and masking live in worker/mask.py (R5b).

A box is (x, y, w, h, text) in pixels. Rows are grouped by vertical centre with a tolerance of half the median box
height, ordered left to right, and joined with spaces proportional to the horizontal gap (one space per 0.6 × median
height, clamped to 1..12) so columns stay apart for the parser. LAYOUT_VERSION is part of the parser version (R5c):
bump it when any rule here changes.
"""
from __future__ import annotations

LAYOUT_VERSION = "1"
Box = tuple[float, float, float, float, str]
MAX_PAD = 12


def char_width(boxes: list[Box]) -> float:
    heights = sorted(b[3] for b in boxes)
    return max((heights[len(heights) // 2] if heights else 1.0) * 0.6, 1.0)


def rows_of(boxes: list[Box]) -> list[list[Box]]:
    if not boxes:
        return []
    heights = sorted(b[3] for b in boxes)
    tol = (heights[len(heights) // 2] or 1.0) * 0.5
    rows: list[list[Box]] = []
    centre = 0.0
    for b in sorted(boxes, key=lambda b: (b[1] + b[3] / 2, b[0])):
        c = b[1] + b[3] / 2
        if rows and abs(c - centre) <= tol:
            rows[-1].append(b)
        else:
            rows.append([b])
            centre = c
    for row in rows:
        row.sort(key=lambda b: b[0])
    return rows


def layout(boxes: list[Box]) -> str:
    if not boxes:
        return ""
    char_w = char_width(boxes)
    lines = []
    for row in rows_of(boxes):
        parts: list[str] = []
        prev_end: float | None = None
        for x, _y, w, _h, text in row:
            if prev_end is not None:
                parts.append(" " * max(1, min(MAX_PAD, int((x - prev_end) / char_w))))
            parts.append(text)
            prev_end = x + w
        lines.append("".join(parts))
    return "\n".join(lines)
