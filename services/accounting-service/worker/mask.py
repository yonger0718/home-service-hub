"""Masking of personal data before text leaves the worker (§5.4). Applied to text and every table cell."""
from __future__ import annotations

import re
from datetime import date

from worker import pdf
from worker.passwords import Identity

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# boundaries on ASCII letters/digits only: CJK neighbours must not protect a secret (AGENT-95 Must 1)
_ID = re.compile(r"(?<![A-Za-z0-9])[A-Z][12]\d{8}(?![A-Za-z0-9])")
# Taiwan mobile, international mobile, and landlines only when written with parentheses or dashes
_PHONE = re.compile(
    r"(?<!\d)09\d{2}[- ]?\d{3}[- ]?\d{3}(?!\d)"
    r"|\+886[- ]?9\d{2}[- ]?\d{3}[- ]?\d{3}(?!\d)"
    r"|(?<!\d)(?:\(0\d{1,2}\)[- ]?\d{3,4}[- ]?\d{4}|0\d{1,2}-\d{3,4}-\d{4})(?!\d)"
)
# a run is >= 6 contiguous digits, or >= 2 groups of >= 3 digits joined by one space or dash
_DIGIT_RUN = re.compile(r"(?<!\d)(?:\d{6,}|\d{3,}(?:[ \-]\d{3,})+)(?!\d)")


def _birth_patterns(ymd: str) -> list[str]:
    y, m, d = ymd[0:4], ymd[4:6], ymd[6:8]
    return [f"{y}{m}{d}", f"{y}/{m}/{d}", f"{y}-{m}-{d}", f"{y}.{m}.{d}", f"{d}{m}{y[2:]}", f"{d}/{m}/{y[2:]}",
            f"{m}/{d}/{y}", f"{y}年{int(m)}月{int(d)}日", f"{int(y) - 1911}/{m}/{d}", f"{int(y) - 1911}{m}{d}"]


def _compact_date(raw: str) -> bool:
    """Exactly eight ASCII digits forming a Gregorian YYYYMMDD with year 1990..2039."""
    if not re.fullmatch(r"[0-9]{8}", raw):
        return False
    try:
        date.fromisoformat(f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}")
    except ValueError:
        return False
    return 1990 <= int(raw[0:4]) <= 2039


class Masker:
    def __init__(self, identity: Identity):
        self.identity = identity
        self.literals: list[str] = []
        if identity.birth_date and len(identity.birth_date) == 8:
            self.literals += _birth_patterns(identity.birth_date)
        self.names = sorted((n for n in identity.holder_names if len(n) >= 2), key=len, reverse=True)

    @staticmethod
    def _digits(match: re.Match) -> str:
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if (" " in raw or "-" in raw) and len(digits) < 8:
            return raw  # dates and short references such as "000 2026" are not account numbers
        if _compact_date(raw):
            return raw  # printed compact dates such as 20260930 are not account numbers
        return f"[NUM…{digits[-4:]}]"

    def text(self, s: str, extra_literals=()) -> str:
        for literal in sorted((x for x in extra_literals if len(x) >= 4), key=len, reverse=True):
            # token boundaries: an all-digit literal is not replaced inside a longer digit run (a date such as
            # 20260930 or 2026/09/30 survives a candidate 0115 or 2026), any other literal not inside a longer alphanumeric run.
            # A standalone all-digit candidate is masked only when it stands alone as a token.
            guard = (r"(?<!\d)(?<!\d[/.-])", r"(?!\d)(?![/.-]\d)") if literal.isdigit() else (r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])")
            s = re.sub(guard[0] + re.escape(literal) + guard[1], "[PW]", s)  # before any other rule
        if self.identity.id_number:
            s = s.replace(self.identity.id_number, "[ID]")
        s = _ID.sub("[ID]", s)
        for name in self.names:
            s = s.replace(name, "[NAME]")
        s = _EMAIL.sub("[EMAIL]", s)
        for literal in self.literals:
            s = s.replace(literal, "[BIRTH]")
        s = _PHONE.sub("[PHONE]", s)
        s = _DIGIT_RUN.sub(self._digits, s)
        return s

    def tables(self, tables: list[list[list[str]]], extra_literals=()) -> list[list[list[str]]]:
        return [[[self.text(cell, extra_literals) for cell in row] for row in table] for table in tables]

    def render(self, extracted: pdf.Extracted, extra_literals=()) -> str:
        parts = [self.text(extracted.text, extra_literals)]
        if extracted.tables:
            parts.append("\n\n[tables]\n")
            for table in self.tables(extracted.tables, extra_literals):
                parts.extend(" | ".join(row) + "\n" for row in table)
                parts.append("\n")
        return "".join(parts)
