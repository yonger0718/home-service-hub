"""Unlock (pypdf) and extract (pdfplumber) within the §5.4 bounds. Decrypted bytes stay in memory."""
from __future__ import annotations

import signal
import threading
from dataclasses import dataclass
from io import BytesIO

import pdfplumber
from pdfplumber.utils.exceptions import MalformedPDFException, PdfminerException
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfminer.pdfexceptions import PDFException
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
_LIBRARY_ERRORS = (PdfReadError, PDFException, PdfminerException, MalformedPDFException, ValueError, KeyError, TypeError, IndexError, NotImplementedError)


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
        raise TooLarge(f"{len(data)} bytes")
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
    except _LIBRARY_ERRORS as exc:
        raise Malformed("malformed") from exc
    raise PasswordError("password")


class _Alarm:
    """SIGALRM bound on extraction. Signals only exist in the main thread, so elsewhere the bound is skipped."""

    def __init__(self, seconds: int):
        self.seconds = seconds
        self.active = threading.current_thread() is threading.main_thread()

    def __enter__(self):
        if self.active:
            signal.signal(signal.SIGALRM, self._fire)
            signal.alarm(self.seconds)
        return self

    def __exit__(self, *exc):
        if self.active:
            signal.alarm(0)

    @staticmethod
    def _fire(signum, frame):
        raise ExtractTimeout("extraction timed out")


def extract(data: bytes, password: str | None, *, max_pages: int = 40, max_bytes: int = 20_000_000,
            timeout_s: int = 60) -> Extracted:
    if len(data) > max_bytes:
        raise TooLarge(f"{len(data)} bytes")
    with _Alarm(timeout_s):
        try:
            doc = pdfplumber.open(BytesIO(data), password=password)
        except PdfminerException as exc:
            if isinstance(exc.args[0] if exc.args else None, PDFPasswordIncorrect):
                raise PasswordError("password") from exc
            raise Malformed("malformed") from exc
        except _LIBRARY_ERRORS as exc:
            raise Malformed("malformed") from exc
        with doc:
            if len(doc.pages) > max_pages:
                raise TooLarge(f"{len(doc.pages)} pages")
            texts: list[str] = []
            tables: list[list[list[str]]] = []
            try:
                for page in doc.pages:
                    texts.append(page.extract_text() or "")
                    for table in page.extract_tables() or []:
                        tables.append([[("" if cell is None else str(cell)) for cell in row] for row in table])
            except _LIBRARY_ERRORS as exc:
                raise Malformed("malformed") from exc
            page1 = len(texts[0].strip()) if texts else 0
            if page1 < 200:
                raise NoTextLayer(f"page 1 has {page1} characters")
            return Extracted("\f".join(texts), tables, len(doc.pages), page1)
