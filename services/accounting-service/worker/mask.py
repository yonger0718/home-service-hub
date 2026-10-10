"""Masking of personal data before text leaves the worker (§5.4). Applied to text and every table cell."""
from __future__ import annotations

import re

from worker import pdf
from worker.passwords import Identity

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# boundaries on ASCII letters/digits only: CJK neighbours must not protect a secret (AGENT-95 Must 1)
_ID = re.compile(r"(?<![A-Za-z0-9])[A-Z][12]\d{8}(?![A-Za-z0-9])")
_PHONE = re.compile(r"(?<!\d)(?:\(0\d{1,2}\)|0\d{1,2})[- ]?\d{3,4}[- ]?\d{3,4}(?!\d)")
_DIGIT_RUN = re.compile(r"(?<!\d)\d(?:[ \-]?\d){5,}(?!\d)")  # separator-tolerant, >= 6 digits


def _birth_patterns(ymd: str) -> list[str]:
    y, m, d = ymd[0:4], ymd[4:6], ymd[6:8]
    return [f"{y}{m}{d}", f"{y}/{m}/{d}", f"{y}-{m}-{d}", f"{y}.{m}.{d}", f"{d}{m}{y[2:]}", f"{d}/{m}/{y[2:]}",
            f"{m}/{d}/{y}", f"{y}年{int(m)}月{int(d)}日", f"{int(y) - 1911}/{m}/{d}", f"{int(y) - 1911}{m}{d}"]


class Masker:
    def __init__(self, identity: Identity):
        self.identity = identity
        self.literals: list[str] = []
        if identity.birth_date and len(identity.birth_date) == 8:
            self.literals += _birth_patterns(identity.birth_date)
        self.names = sorted((n for n in identity.holder_names if n), key=len, reverse=True)

    @staticmethod
    def _digits(match: re.Match) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        return f"[NUM…{digits[-4:]}]"

    def text(self, s: str) -> str:
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

    def tables(self, tables: list[list[list[str]]]) -> list[list[list[str]]]:
        return [[[self.text(cell) for cell in row] for row in table] for table in tables]

    def render(self, extracted: pdf.Extracted) -> str:
        parts = [self.text(extracted.text)]
        if extracted.tables:
            parts.append("\n\n[tables]\n")
            for table in self.tables(extracted.tables):
                parts.extend(" | ".join(row) + "\n" for row in table)
                parts.append("\n")
        return "".join(parts)
