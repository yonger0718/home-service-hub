"""Notifier state file: what was already announced, so reminders are not repeated."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path


@dataclass
class StatementState:
    """Keyed by statement id, or -card_id for rule-only items."""
    first_notified: date | None


@dataclass
class SourceState:
    last_shortfall: Decimal | None


@dataclass
class LastSend:
    day: date
    triggers: frozenset[str]


@dataclass
class State:
    statements: dict[int, StatementState] = field(default_factory=dict)
    sources: dict[int, SourceState] = field(default_factory=dict)
    skips_seen: set[int] = field(default_factory=set)
    last_daily_run: datetime | None = None
    last_weekly_run: datetime | None = None
    last_send: LastSend | None = None

    @classmethod
    def empty(cls) -> "State":
        return cls()


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value is not None else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _to_json(st: State) -> dict:
    return {
        "statements": {str(k): {"first_notified": _iso(v.first_notified)} for k, v in st.statements.items()},
        "sources": {
            str(k): {"last_shortfall": None if v.last_shortfall is None else str(v.last_shortfall)}
            for k, v in st.sources.items()
        },
        "skips_seen": sorted(st.skips_seen),
        "last_daily_run": _iso(st.last_daily_run),
        "last_weekly_run": _iso(st.last_weekly_run),
        "last_send": None
        if st.last_send is None
        else {"day": st.last_send.day.isoformat(), "triggers": sorted(st.last_send.triggers)},
    }


def _from_json(raw: dict) -> State:
    send = raw.get("last_send")
    return State(
        statements={int(k): StatementState(_d(v["first_notified"])) for k, v in raw["statements"].items()},
        sources={
            int(k): SourceState(None if v["last_shortfall"] is None else Decimal(v["last_shortfall"]))
            for k, v in raw["sources"].items()
        },
        skips_seen={int(i) for i in raw["skips_seen"]},
        last_daily_run=_dt(raw["last_daily_run"]),
        last_weekly_run=_dt(raw["last_weekly_run"]),
        last_send=None if send is None else LastSend(date.fromisoformat(send["day"]), frozenset(send["triggers"])),
    )


def load_state(path: Path) -> State:
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return State.empty()
    except OSError as exc:
        print(f"notify: state file unreadable ({type(exc).__name__}); starting empty", file=sys.stderr)
        return State.empty()
    try:
        return _from_json(json.loads(text))
    except Exception as exc:  # corrupt or wrong shape: start over, one stderr line
        print(f"notify: state file corrupt ({type(exc).__name__}); starting empty", file=sys.stderr)
        return State.empty()


def save_state(path: Path, state: State) -> None:
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(_to_json(state), fh, indent=2, sort_keys=True)
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
