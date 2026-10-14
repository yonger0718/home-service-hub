"""Masking of personal data before text leaves the worker (§5.4). Applied to text and every table cell."""
from __future__ import annotations

import re
import unicodedata
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


# R5b (OCR path only): OCR splits identifiers across boxes and pads columns with wide or full-width spaces, so the
# OCR entry point first normalises (NFKC) and matches a *compacted* view of each line against compacted literals and
# generic identifier shapes, then replaces only the matched spans. Confusables (O/o→0, I/l/|→1) are corrected only
# inside runs of digit-like tokens that are mostly digits, never in merchant text.
_OCR_WS = re.compile(r"\s+")
_OCR_TOKEN = re.compile(r"\S+")
_OCR_DIGIT_LIKE = re.compile(r"^[0-9OoIl|]+$")
_OCR_CONFUSABLE = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"})
_OCR_ID = re.compile(r"(?<![A-Za-z0-9])[A-Z][12]\d{8}(?![A-Za-z0-9])")
_OCR_LONG_DIGITS = re.compile(r"\d{12,}")
OCR_CONFUSABLE_MIN_DIGIT_SHARE = 0.7


def _ocr_fix_confusables(line: str) -> str:
    """Maximal runs of whitespace-separated digit-like tokens whose characters are mostly ASCII digits get their
    OCR confusables corrected; a run that is mostly letters (OllO12) is left alone."""
    toks = [(m.start(), m.end(), m.group(0)) for m in _OCR_TOKEN.finditer(line)]
    out = list(line)
    i = 0
    while i < len(toks):
        if not _OCR_DIGIT_LIKE.match(toks[i][2]):
            i += 1
            continue
        j = i
        while j < len(toks) and _OCR_DIGIT_LIKE.match(toks[j][2]):
            j += 1
        chars = "".join(t[2] for t in toks[i:j])
        if len(chars) >= 4 and sum(c.isdigit() for c in chars) / len(chars) >= OCR_CONFUSABLE_MIN_DIGIT_SHARE:
            for s_, e_, t in toks[i:j]:
                out[s_:e_] = list(t.translate(_OCR_CONFUSABLE))
        i = j
    return "".join(out)


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

    # ---- R5b: OCR entry point -------------------------------------------------------------------------------------

    def _ocr_literals(self, extra_literals=()) -> list[tuple[str, str, bool]]:
        """(tag, compacted casefolded literal, numeric) — the ID, holder names, birth patterns, password candidates
        (>= 4 chars, like `text`). Longest first so a longer match wins."""
        lits: list[tuple[str, str, bool]] = []
        if self.identity.id_number:
            lits.append(("[ID]", self.identity.id_number.casefold(), False))
        for name in self.names:
            lits.append(("[NAME]", "".join(name.split()).casefold(), False))
        for birth in self.literals:
            lits.append(("[BIRTH]", "".join(birth.split()).casefold(), birth.isdigit()))
        for cand in extra_literals:
            if len(cand) >= 4:
                lits.append(("[PW]", "".join(cand.split()).casefold(), cand.isdigit()))
        return sorted(lits, key=lambda t: -len(t[1]))

    def mask_ocr_line(self, line: str, extra_literals=()) -> str:
        line = _ocr_fix_confusables(unicodedata.normalize("NFKC", line))
        pos = [i for i, ch in enumerate(line) if not ch.isspace()]
        compact = "".join(line[i] for i in pos)
        folded = compact.casefold()
        toks = [(m.start(), m.end(), m.group(0)) for m in _OCR_TOKEN.finditer(line)]
        spans: list[tuple[int, int, str]] = []  # (start, end, tag) in line coordinates

        def free(s_: int, e_: int) -> bool:
            return all(e_ <= a or s_ >= b for a, b, _ in spans)

        def tokens_in(s_: int, e_: int):
            return [t for a, b, t in toks if a < e_ and b > s_]

        def boundary_ok(k: int, length: int) -> bool:
            """The masker's digit-boundary rule in compact coordinates: a numeric literal is not matched inside a
            longer digit run nor as a fragment of a separated date (2026/09/01 keeps 2026)."""
            before = compact[k - 1] if k > 0 else ""
            before2 = compact[k - 2] if k > 1 else ""
            after = compact[k + length] if k + length < len(compact) else ""
            after2 = compact[k + length + 1] if k + length + 1 < len(compact) else ""
            if before.isdigit() or after.isdigit():
                return False
            if after in "/.-" and after2.isdigit():
                return False
            if before in "/.-" and before2.isdigit():
                return False
            return True

        for tag, lit, numeric in self._ocr_literals(extra_literals):
            start = 0
            while True:
                k = folded.find(lit, start)
                if k < 0:
                    break
                s_, e_ = pos[k], pos[k + len(lit) - 1] + 1
                if (not numeric or boundary_ok(k, len(lit))) and free(s_, e_):
                    spans.append((s_, e_, tag))
                start = k + 1
        for m in _OCR_ID.finditer(compact):  # any ID-shaped span, configured or not
            s_, e_ = pos[m.start()], pos[m.end() - 1] + 1
            if free(s_, e_):
                spans.append((s_, e_, "[ID]"))
        for m in _OCR_LONG_DIGITS.finditer(compact):  # card/account numbers in any grouping; dates excluded
            s_, e_ = pos[m.start()], pos[m.end() - 1] + 1
            if any(_compact_date(t) for t in tokens_in(s_, e_)):
                continue
            if free(s_, e_):
                spans.append((s_, e_, f"[NUM…{m.group(0)[-4:]}]"))
        out, last = [], 0
        for s_, e_, tag in sorted(spans):
            out.append(line[last:s_])
            out.append(tag)
            last = e_
        out.append(line[last:])
        return self.text("".join(out), extra_literals)

    def mask_ocr(self, s: str, extra_literals=()) -> str:
        """R5b: mask OCR text. Spans only — other columns of a row survive. Known limitation (ruling 2026-10-14): a
        standalone numeric token equal to a password candidate is masked exactly as on the text-layer path."""
        return "\n".join(self.mask_ocr_line(line, extra_literals) for line in s.split("\n"))

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
