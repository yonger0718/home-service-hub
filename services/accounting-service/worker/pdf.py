"""Unlock (pypdf) and extract (pdfplumber) within the §5.4 bounds. Decrypted bytes stay in memory.

PasswordError, Malformed, NoTextLayer and TooLarge are raised from None so no library text is chained.
Callers must never log exc_info for these: log the exception class name only.
"""
from __future__ import annotations

import signal
import threading
from dataclasses import dataclass, field
from io import BytesIO

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfplumber.utils.exceptions import PdfminerException
from pypdf import PdfReader
from pypdf.errors import PdfReadError


class PasswordError(RuntimeError):
    pass


class NoTextLayer(RuntimeError):
    pass


class TooLarge(RuntimeError):
    pass


class ExtractTimeout(RuntimeError):
    pass


class Malformed(RuntimeError):
    pass


# every library failure on hostile or corrupt input becomes Malformed; messages never carry file content

@dataclass
class Unlocked:
    reader: PdfReader
    password_index: int | None


@dataclass(frozen=True)
class Extracted:
    text: str = field(repr=False)  # statement content never prints through a repr (R5a)
    tables: list[list[list[str]]]
    pages: int
    text_chars_page1: int


@dataclass(frozen=True)
class Word:
    text: str = field(repr=False)  # statement content never prints through a repr (R5a); no default — positional args stay (text, x0, x1)
    x0: float
    x1: float


@dataclass(frozen=True)
class Line:
    page: int
    top: float
    words: tuple[Word, ...] = field(repr=False)  # sorted by x0

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


LINE_TOLERANCE = 2.0  # pt: words whose `top` differ by at most this much are one line


def extract_lines(data: bytes, password: str | None, *, max_pages: int = 40, max_bytes: int = 20_000_000,
                  timeout_s: int = 60) -> list[Line]:
    """Words with their x-ranges, grouped into lines by `top`, in reading order. Same bounds and exception
    mapping as `extract`; used by the composite split only. Line text never reaches logs."""
    if len(data) > max_bytes:
        raise TooLarge(f"{len(data)} bytes") from None
    with _Alarm(timeout_s):
        try:
            doc = pdfplumber.open(BytesIO(data), password=password)
            with doc:
                if len(doc.pages) > max_pages:
                    raise TooLarge(f"{len(doc.pages)} pages") from None
                out: list[Line] = []
                for number, page in enumerate(doc.pages, start=1):
                    rows: list[tuple[float, list[dict]]] = []
                    for word in sorted(page.extract_words() or [], key=lambda w: (w["top"], w["x0"])):
                        if rows and abs(rows[-1][0] - word["top"]) <= LINE_TOLERANCE:
                            rows[-1][1].append(word)
                        else:
                            rows.append((word["top"], [word]))
                    for top, words in rows:
                        ordered = sorted(words, key=lambda w: w["x0"])
                        out.append(Line(number, float(top), tuple(Word(w["text"], float(w["x0"]), float(w["x1"]))
                                                                  for w in ordered)))
                return out
        except (PasswordError, TooLarge, ExtractTimeout):
            raise
        except PdfminerException as exc:
            if isinstance(exc.args[0] if exc.args else None, PDFPasswordIncorrect):
                raise PasswordError("password") from None
            raise Malformed("malformed") from None
        except Exception:  # noqa: BLE001
            raise Malformed("malformed") from None


def unlock(data: bytes, candidates: list[str], *, max_bytes: int = 20_000_000) -> Unlocked:
    if len(data) > max_bytes:
        raise TooLarge(f"{len(data)} bytes") from None
    try:
        reader = PdfReader(BytesIO(data))
        if not reader.is_encrypted:
            return Unlocked(reader, None)
        # Owner-password-only encryption (an empty user password, e.g. AMEX): opens without a credential,
        # so it is tried first and counts as "no password" (index None) like a plain file. Only a USER-password
        # match (1) counts: pypdf checks the owner password first, so an empty owner password answers 2 even when
        # the user password is set — that file stays on the candidate path.
        try:
            if reader.decrypt("") == 1:
                return Unlocked(reader, None)
        except (PdfReadError, NotImplementedError):
            pass
        for index, candidate in enumerate(candidates):
            try:
                if reader.decrypt(candidate) > 0:
                    return Unlocked(reader, index)
            except (PdfReadError, NotImplementedError):
                continue
    except Exception:  # noqa: BLE001 — any library surprise is a malformed file; never carry its text along
        raise Malformed("malformed") from None
    raise PasswordError("password") from None


class _Alarm:
    """SIGALRM bound on extraction. Signals only exist in the main thread, so elsewhere the bound is skipped.

    The previous SIGALRM handler is restored on exit, so a caller's own handler survives.
    """

    def __init__(self, seconds: int):
        self.seconds = seconds
        self.active = threading.current_thread() is threading.main_thread()
        self._previous = None

    def __enter__(self):
        if self.active:
            self._previous = signal.signal(signal.SIGALRM, self._fire)
            signal.alarm(self.seconds)
        return self

    def __exit__(self, *exc):
        if self.active:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, self._previous)

    @staticmethod
    def _fire(signum, frame):
        raise ExtractTimeout("extraction timed out")


GARBAGE_GLYPH = "(cid:"
# R5.1 (AGENT-118 ruling 2026-10-14): share of page-1 glyph objects that no font maps to Unicode (pdfminer renders
# them as `(cid:N)`). Measured on the owner's corpus: 遠東/渣打 (Type3, no ToUnicode) 53–56 %; 國泰 cards (CID with
# ToUnicode, icons unmapped) 36–40 % and they parse from the text layer; 星展 1–2 %; every other issuer 0 %. The
# threshold sits between the two populations (5 points under the garbage side, 8 over 國泰).
GARBAGE_GLYPH_SHARE = 0.45


def glyph_garbage_share(chars) -> float:
    """The share of glyph objects (pdfplumber `page.chars`) whose text is an unmapped `(cid:N)`; 0 for no glyphs."""
    total = len(chars)
    if not total:
        return 0.0
    return sum(1 for ch in chars if str(ch.get("text", "")).startswith(GARBAGE_GLYPH)) / total


def is_font_garbage(chars) -> bool:
    """R5.1: at least GARBAGE_GLYPH_SHARE of page 1's glyphs unmapped means there is no usable text layer."""
    return glyph_garbage_share(chars) >= GARBAGE_GLYPH_SHARE


def extract(data: bytes, password: str | None, *, max_pages: int = 40, max_bytes: int = 20_000_000,
            timeout_s: int = 60) -> Extracted:
    if len(data) > max_bytes:
        raise TooLarge(f"{len(data)} bytes") from None
    with _Alarm(timeout_s):
        try:
            doc = pdfplumber.open(BytesIO(data), password=password)
            with doc:
                npages = len(doc.pages)
                if npages > max_pages:
                    raise TooLarge(f"{npages} pages") from None
                texts: list[str] = []
                tables: list[list[list[str]]] = []
                for page in doc.pages:
                    texts.append(page.extract_text() or "")
                    for table in page.extract_tables() or []:
                        tables.append([[("" if cell is None else str(cell)) for cell in row] for row in table])
                page1 = len(texts[0].strip()) if texts else 0
                if page1 < 200:
                    raise NoTextLayer(f"page 1 has {page1} characters") from None
                if is_font_garbage(doc.pages[0].chars):
                    raise NoTextLayer("page 1 is font garbage") from None
                return Extracted("\f".join(texts), tables, npages, page1)
        except (PasswordError, NoTextLayer, TooLarge, ExtractTimeout):
            raise
        except PdfminerException as exc:
            if isinstance(exc.args[0] if exc.args else None, PDFPasswordIncorrect):
                raise PasswordError("password") from None
            raise Malformed("malformed") from None
        except Exception:  # noqa: BLE001
            raise Malformed("malformed") from None
