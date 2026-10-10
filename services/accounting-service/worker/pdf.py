"""Unlock (pypdf) and extract (pdfplumber) within the §5.4 bounds. Decrypted bytes stay in memory.

PasswordError, Malformed, NoTextLayer and TooLarge are raised from None so no library text is chained.
Callers must never log exc_info for these: log the exception class name only.
"""
from __future__ import annotations

import signal
import threading
from dataclasses import dataclass
from io import BytesIO

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfminer.pdfexceptions import PDFException
from pdfplumber.utils.exceptions import MalformedPDFException, PdfminerException
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
_LIBRARY_ERRORS = (PdfReadError, PDFException, PdfminerException, MalformedPDFException,
                   ValueError, KeyError, TypeError, IndexError, NotImplementedError)


@dataclass
class Unlocked:
    reader: PdfReader
    password_index: int | None


@dataclass(frozen=True)
class Extracted:
    text: str
    tables: list[list[list[str]]]
    pages: int
    text_chars_page1: int


def unlock(data: bytes, candidates: list[str], *, max_bytes: int = 20_000_000) -> Unlocked:
    if len(data) > max_bytes:
        raise TooLarge(f"{len(data)} bytes") from None
    try:
        reader = PdfReader(BytesIO(data))
        if not reader.is_encrypted:
            return Unlocked(reader, None)
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
                return Extracted("\f".join(texts), tables, npages, page1)
        except (PasswordError, NoTextLayer, TooLarge, ExtractTimeout):
            raise
        except PdfminerException as exc:
            if isinstance(exc.args[0] if exc.args else None, PDFPasswordIncorrect):
                raise PasswordError("password") from None
            raise Malformed("malformed") from None
        except Exception:  # noqa: BLE001
            raise Malformed("malformed") from None
