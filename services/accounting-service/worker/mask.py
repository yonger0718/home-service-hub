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
# A card transaction row's trailing columns (國泰 CUBE): the amount, the card's last four digits, optionally a four-digit
# authorisation code, then a two-letter country code and a three-letter currency code. Without this pass `_DIGIT_RUN`
# joins the amount with the codes (174 9538 1814 → [NUM…1814]) and the parser never sees the amount. Only this exact
# shape is rewritten — the amount stays printed and the code groups become one masked token — every other digit run
# keeps the generic rule, so no identifier printed as a short group followed by longer groups is exposed.
_TXN_CODES = re.compile(r"(?<![\d,.])(-?\d{1,3}(?:,\d{3})*(?:\.\d+)?) (\d{4})(?: (\d{4}))? ([A-Z]{2}) ([A-Z]{3})(?![A-Za-z0-9])")
# Folded into the parser version (parse_schema.version): bump when a masking rule changes what the parser receives,
# so files that failed under the old rule are re-parsed.
FINGERPRINT = "mask2-amount-before-codes"


# R5b (OCR path only): OCR splits identifiers across boxes and pads columns with wide or full-width spaces, so the
# OCR entry point first normalises (NFKC, invisible characters dropped) and matches *views* of each line — compacted
# (no whitespace), alphanumeric-only (for names) and digit-only (for card/account numbers) — against the configured
# literals and generic identifier shapes, every view carrying a map back to line positions. Only matched spans are
# replaced. Confusables (O/o→0, I/l/|→1) are corrected inside runs of digit-like tokens that are mostly digits, or
# that are at least 12 characters long (a whole card group read as letters), never in merchant text. Matching is per
# line: an identifier wrapped across two OCR lines is a known limitation (the text-layer path has the same limit; the
# external parser receives both lines in one stdin blob).
_OCR_TOKEN = re.compile(r"\S+")
_OCR_DIGIT_LIKE = re.compile(r"^[0-9OoIl|]+$")
_OCR_MIXED_LIKE = re.compile(r"^[A-Za-z][0-9OoIl|]{4,}$")  # an ID whose digits carry confusables (A12345678O)
_OCR_CONFUSABLE = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"})
# Cyrillic and Greek lookalikes of a Latin capital, accepted only at the ID-letter position of an ID-shaped span
# (the compact view is casefolded, so the lower-case forms are listed)
_OCR_ID_LETTER_HOMOGLYPHS = "авекмнорстху" "αβεζηικμνορτυχ"
_OCR_ID_CORE = re.compile(rf"[A-Za-z{_OCR_ID_LETTER_HOMOGLYPHS}][12]\d{{8}}")
_OCR_LONG_DIGITS = re.compile(r"\d{12,}")
# group separators between digit groups, dropped from the compact and digits views: dash, en/em dash, hyphen, minus
# sign, dot, middle dots, slash, pipe (table borders)
_OCR_SEPARATOR = set("-\u2013\u2014\u2010\u2212.\u00b7\u30fb/|")
OCR_CONFUSABLE_RUN_MIN = 12  # a digit-like run this long is corrected whatever its digit share
_OCR_DATE_TOKEN = re.compile(r"^\d{2,4}[-/.]\d{1,2}[-/.]\d{1,2}$|^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}$")  # separated dates, incl. ROC years


def _ocr_is_date(token: str) -> bool:
    return _compact_date(token) or bool(_OCR_DATE_TOKEN.match(token))
OCR_CONFUSABLE_MIN_DIGIT_SHARE = 0.7


def _ocr_clean(line: str) -> str:
    """NFKC, then invisible format characters (zero-width, BOM, soft hyphen, bidi controls) and nonspacing marks that
    did not compose (a stray combining accent inside an identifier) dropped."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", line) if unicodedata.category(ch) not in ("Cf", "Mn"))


def _ocr_run_token(tok: str) -> bool:
    """A token that can belong to a digit-like run: digits and the O/o/I/l/| confusables, including a token that is
    all letters (a group read as OOOO or llll); a token made only of pipes is a table border, never part of a run."""
    return bool(_OCR_DIGIT_LIKE.match(tok)) and set(tok) != {"|"}


def _ocr_fix_confusables(line: str) -> str:
    """Maximal runs of whitespace-separated digit-like tokens get their OCR confusables corrected when the run is at
    least OCR_CONFUSABLE_RUN_MIN characters long (a card or account number, even if whole groups were read as
    letters) or when it is at least 4 characters and mostly (>= 70 %) ASCII digits. A lone `|`, a short run that is
    mostly letters (OllO12, a lone 4lll, I0O1) and merchant text are left alone. A mixed token such as A12345678O
    (letter + mostly-digit remainder) is corrected in its remainder."""
    toks = [(m.start(), m.end(), m.group(0)) for m in _OCR_TOKEN.finditer(line)]
    out = list(line)
    i = 0
    while i < len(toks):
        tok = toks[i][2]
        if _OCR_MIXED_LIKE.match(tok) and sum(c.isdigit() for c in tok[1:]) / (len(tok) - 1) >= OCR_CONFUSABLE_MIN_DIGIT_SHARE:
            s_, e_, _ = toks[i]
            out[s_:e_] = list(tok[0] + tok[1:].translate(_OCR_CONFUSABLE))
            i += 1
            continue
        if not _ocr_run_token(tok):
            i += 1
            continue
        j = i
        while j < len(toks) and _ocr_run_token(toks[j][2]):
            j += 1
        chars = "".join(t[2] for t in toks[i:j])
        share = sum(c.isdigit() for c in chars) / len(chars)
        if len(chars) >= OCR_CONFUSABLE_RUN_MIN or (len(chars) >= 4 and share >= OCR_CONFUSABLE_MIN_DIGIT_SHARE):
            for s_, e_, t in toks[i:j]:
                out[s_:e_] = list(t.translate(_OCR_CONFUSABLE))
        i = j
    return "".join(out)


class _View:
    """A derived string plus, for each of its characters, the line position it came from."""

    def __init__(self, text: str, pos: list[int]):
        self.text, self.pos = text, pos

    def span(self, k: int, length: int) -> tuple[int, int]:
        return self.pos[k], self.pos[k + length - 1] + 1


def _ocr_views(line: str, blanked: list[tuple[int, int]]) -> tuple[_View, _View, _View]:
    """compact (no whitespace and no group separators, casefolded char by char), alnum (letters and digits only,
    casefolded), digits (digits only, with the `blanked` spans — printed dates and spans already claimed by a literal
    or an ID — turned into non-digits so a card/account run never crosses them, and group separators dropped)."""
    c_text, c_pos, a_text, a_pos, d_text, d_pos = [], [], [], [], [], []
    in_date = [False] * (len(line) + 1)
    for s_, e_ in blanked:
        for i in range(s_, e_):
            in_date[i] = True
    for i, ch in enumerate(line):
        if ch.isspace() or ch in _OCR_SEPARATOR:
            continue  # whitespace and a separator between groups (B12-345-6789, 4111/2222/…) never break a span
        folded = ch.casefold()
        c_text.append(folded); c_pos.extend([i] * len(folded))
        if ch.isalnum():
            a_text.append(folded); a_pos.extend([i] * len(folded))
        if ch.isdigit() and ch.isascii():
            d_text.append("#" if in_date[i] else ch); d_pos.append(i)
        else:
            d_text.append("x"); d_pos.append(i)  # anything else breaks a digit run
    return _View("".join(c_text), c_pos), _View("".join(a_text), a_pos), _View("".join(d_text), d_pos)


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

    @staticmethod
    def _txn_codes(match: re.Match) -> str:
        amount, card, auth, country, currency = match.groups()
        return f"{amount} [NUM…{(auth or card)[-4:]}] {country} {currency}"

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
        if "@" in s:  # the pattern cannot match without "@"; the guard keeps a long dotted line linear (R5b review)
            s = _EMAIL.sub("[EMAIL]", s)
        for literal in self.literals:
            s = s.replace(literal, "[BIRTH]")
        s = _PHONE.sub("[PHONE]", s)
        s = _TXN_CODES.sub(self._txn_codes, s)  # before the generic rule, which would swallow the amount
        s = _DIGIT_RUN.sub(self._digits, s)
        return s

    # ---- R5b: OCR entry point -------------------------------------------------------------------------------------

    def _ocr_literals(self, extra_literals=()) -> list[tuple[str, str, str]]:
        """(tag, view, literal) with the literal NFKC-normalised, whitespace and group separators removed, casefolded,
        in the view it is matched against: names in the alphanumeric view (punctuation between parts ignored), the ID
        and numeric literals in the compact view. Longest first so a longer match wins. Empty literals are skipped."""
        def norm(x: str, alnum: bool) -> str:
            x = _ocr_clean(x)
            return "".join(ch.casefold() for ch in x if (ch.isalnum() if alnum else not (ch.isspace() or ch in _OCR_SEPARATOR)))

        lits: list[tuple[str, str, str]] = []
        if self.identity.id_number:
            lits.append(("[ID]", "compact", norm(self.identity.id_number, False)))
        for name in self.names:
            lits.append(("[NAME]", "alnum", norm(name, True)))
        for birth in self.literals:
            lits.append(("[BIRTH]", "compact", norm(birth, False)))
        for cand in extra_literals:
            if len(cand) >= 4:
                lits.append(("[PW]", "compact", norm(cand, False)))
        return sorted(((t, v, l) for t, v, l in lits if l), key=lambda t: -len(t[2]))

    def mask_ocr_line(self, line: str, extra_literals=()) -> str:
        line = _ocr_fix_confusables(_ocr_clean(line))
        toks = [(m.start(), m.end(), m.group(0)) for m in _OCR_TOKEN.finditer(line)]
        dates = [(s_, e_) for s_, e_, t in toks if _ocr_is_date(t)]
        compact, alnum, _ = _ocr_views(line, dates)
        spans: list[tuple[int, int, str]] = []

        def edge_ok(s_: int, e_: int, numeric: bool) -> bool:
            """Boundaries judged on the real line: the character before/after the span must not be an ASCII letter
            or digit — a CJK neighbour (持卡人WANG…先生, 身分證A12…末) never protects a secret, as on the text path's
            `_ID` — (numeric spans: not a digit, and not a separated date fragment such as the 2026 of 2026/09/01)."""
            before = line[s_ - 1] if s_ > 0 else ""
            after = line[e_] if e_ < len(line) else ""
            if numeric:
                before2 = line[s_ - 2] if s_ > 1 else ""
                after2 = line[e_ + 1] if e_ + 1 < len(line) else ""
                if before.isdigit() or after.isdigit():
                    return False
                if after in "/.-" and after2.isdigit():
                    return False
                return not (before in "/.-" and before2.isdigit())
            return not ((before.isascii() and before.isalnum()) or (after.isascii() and after.isalnum()))

        def add(s_: int, e_: int, tag: str) -> None:
            spans.append((s_, e_, tag))

        for tag, view_name, lit in self._ocr_literals(extra_literals):
            view = compact if view_name == "compact" else alnum
            start = 0
            while True:
                k = view.text.find(lit, start)
                if k < 0:
                    break
                s_, e_ = view.span(k, len(lit))
                numeric = lit.isdigit()
                guarded = numeric or (lit.isascii() and lit.isalnum())  # short Latin names need word edges; CJK does not
                if not guarded or edge_ok(s_, e_, numeric):
                    add(s_, e_, tag)
                start = k + 1
        for m in _OCR_ID_CORE.finditer(compact.text):  # any ID-shaped span, configured or not, any case, homoglyph letter
            s_, e_ = compact.span(m.start(), m.end() - m.start())
            if edge_ok(s_, e_, False):
                add(s_, e_, "[ID]")
        _, _, digits = _ocr_views(line, dates + [(s_, e_) for s_, e_, _ in spans])  # claimed spans are not card digits
        for m in _OCR_LONG_DIGITS.finditer(digits.text):  # card/account numbers in any grouping; dates never inside
            s_, e_ = digits.span(m.start(), m.end() - m.start())
            add(s_, e_, f"[NUM…{m.group(0)[-4:]}]")
        merged: list[tuple[int, int, str]] = []
        for s_, e_, tag in sorted(spans):
            if merged and s_ < merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e_), merged[-1][2])
            else:
                merged.append((s_, e_, tag))
        out, last = [], 0
        for s_, e_, tag in merged:
            out.append(line[last:s_])
            out.append(tag)
            last = e_
        out.append(line[last:])
        return self.text("".join(out), extra_literals)

    def mask_ocr(self, s: str, extra_literals=()) -> str:
        """R5b: mask OCR text line by line. Spans only — other columns of a row survive. Known limitations (ruling
        2026-10-14): a standalone numeric token equal to a password candidate is masked exactly as on the text-layer
        path, and an identifier wrapped across two OCR lines is not reassembled (the external parser receives both
        lines)."""
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
