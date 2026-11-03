"""Terms state file (one digest per card) and the per-run show rules."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from notify.terms_config import CardEntry
from notify.terms_parse import CardResult


class TermsStateError(Exception):
    """Unreadable or corrupt state file; never carries its contents. The CLI sets ``archive``."""

    reason = "state"

    def __init__(self, archive: Path | None = None):
        super().__init__()
        self.archive = archive

    def __str__(self) -> str:
        return f"{self.reason} {self.archive}"


@dataclass
class CardSeen:
    digest: str
    seen_at: datetime


@dataclass
class TermsState:
    cards: dict[int, CardSeen] = field(default_factory=dict)


def _parse(raw: object) -> TermsState:
    if not isinstance(raw, dict):
        raise TermsStateError()
    cards: dict[int, CardSeen] = {}
    for key, val in raw.items():
        if not isinstance(val, dict) or not isinstance(val.get("digest"), str):
            raise TermsStateError()
        try:
            seen = datetime.fromisoformat(val["seen_at"])
            account_id = int(key)
        except (TypeError, ValueError, KeyError):
            raise TermsStateError() from None
        if seen.tzinfo is None:
            raise TermsStateError()
        cards[account_id] = CardSeen(val["digest"], seen)
    return TermsState(cards)


def load_terms_state(path: Path) -> TermsState:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return TermsState()
    except (OSError, UnicodeDecodeError):
        raise TermsStateError() from None
    try:
        raw = json.loads(text)
    except (ValueError, RecursionError):
        raise TermsStateError() from None
    return _parse(raw)


def save_terms_state(path: Path, state: TermsState) -> None:
    """Atomic write, mode 0600. Takes no lock; the caller holds ``terms_lock``. A naive ``seen_at`` is refused."""
    path = Path(path)
    if any(c.seen_at.tzinfo is None or c.seen_at.utcoffset() is None for c in state.cards.values()):
        raise TermsStateError()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = {str(i): {"digest": c.digest, "seen_at": c.seen_at.isoformat()} for i, c in state.cards.items()}
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextmanager
def terms_lock(path: Path):
    """The single lock holder (flock is not recursive across fds)."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class ShowDecision:
    show_findings: bool
    show_announced: bool
    show_signal: bool
    show_not_found: bool
    show_unverified: bool
    show_period_only: bool
    show_period: bool
    show_truncated: bool
    is_new: bool


def decide(card: CardResult, entry: CardEntry, prev: CardSeen | None, now_digest: str) -> ShowDecision:
    is_new = prev is None or prev.digest != now_digest
    has_period = card.current_period_end is not None
    return ShowDecision(
        show_findings=bool(card.findings),
        show_announced=bool(card.next_terms and card.next_terms.announced),
        show_signal=card.status == "signal" and is_new,
        show_not_found=card.status == "not_found" or (card.status == "changed" and not card.findings),
        show_unverified=(bool(card.unverified) or bool(card.next_terms_unverified_url)) and is_new,
        show_period_only=card.status == "unchanged" and has_period and is_new,
        show_period=card.status in ("changed", "announced") and has_period,
        show_truncated=bool(card.truncated),
        is_new=is_new,
    )
