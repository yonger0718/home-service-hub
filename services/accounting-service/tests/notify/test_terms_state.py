from __future__ import annotations

import fcntl
import json
import os
import stat
from datetime import datetime, timezone

import pytest

from notify.terms_config import CardEntry
from notify.terms_parse import CardResult, Finding, NextTerms, PeriodEnd, TruncatedSource
from notify.terms_state import (
    CardSeen,
    TermsState,
    TermsStateError,
    decide,
    load_terms_state,
    save_terms_state,
    terms_lock,
)

NOW = datetime(2026, 11, 3, 1, 0, tzinfo=timezone.utc)
ENTRY = CardEntry(account_id=101, official="https://bank.example/c", bank_domains=("bank.example",), tier="precise")


def _finding() -> Finding:
    return Finding(rule_id=1, field="rate", site="2", system="1", source="https://bank.example/c", quote="q")


def _card(status="unchanged", **kw) -> CardResult:
    base = dict(
        account_id=101, status=status, current_period_end=None, findings=(), unverified=(),
        next_terms=None, next_terms_unverified_url=None, sources=(), notes="", truncated=(),
    )
    base.update(kw)
    return CardResult(**base)


def _period() -> PeriodEnd:
    return PeriodEnd(date="2026-12-31", source="https://bank.example/c", quote="q")


def _flags(d):
    return {k: v for k, v in vars(d).items() if k.startswith("show_")}


def test_missing_state_is_empty(tmp_path):
    assert load_terms_state(tmp_path / "t.json").cards == {}


def test_roundtrip(tmp_path):
    p = tmp_path / "t.json"
    st = TermsState(cards={101: CardSeen("ab", NOW)})
    save_terms_state(p, st)
    assert json.loads(p.read_text())["101"]["seen_at"] == NOW.isoformat()
    assert load_terms_state(p) == st


def test_corrupt_state_raises_not_resets(tmp_path):
    p = tmp_path / "t.json"
    p.write_text("{")
    with pytest.raises(TermsStateError) as ei:
        load_terms_state(p)
    assert ei.value.reason == "state" and str(ei.value) == "state None"
    assert p.read_text() == "{"


@pytest.mark.parametrize("body", [
    '{"abc": {"digest": "x", "seen_at": "2026-11-03T00:00:00+00:00"}}',
    '{"1": {"seen_at": "2026-11-03T00:00:00+00:00"}}',
    '{"1": {"digest": "x", "seen_at": "nope"}}',
    '{"1": {"digest": "x", "seen_at": "2026-11-03T00:00:00"}}',
    '[1]',
    '{"1": "x"}',
])
def test_bad_shapes_raise(tmp_path, body):
    p = tmp_path / "t.json"
    p.write_text(body)
    with pytest.raises(TermsStateError):
        load_terms_state(p)
    assert p.read_text() == body


def test_unreadable_raises(tmp_path):
    with pytest.raises(TermsStateError):
        load_terms_state(tmp_path)  # a directory


def test_error_archive_read_at_call_time():
    e = TermsStateError()
    e.archive = "/x"
    assert str(e) == "state /x"


def test_save_is_atomic_0600_and_locked(tmp_path):
    p = tmp_path / "sub" / "t.json"
    with terms_lock(p):
        assert (tmp_path / "sub" / "t.lock").exists()
        fd = os.open(p.with_suffix(".lock"), os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        save_terms_state(p, TermsState(cards={101: CardSeen("d", NOW)}))
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700
    assert sorted(x.name for x in p.parent.iterdir()) == ["t.json", "t.lock"]
    fd = os.open(p.with_suffix(".lock"), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released
    finally:
        os.close(fd)


def test_save_under_held_lock_returns(tmp_path):
    p = tmp_path / "t.json"
    with terms_lock(p):
        save_terms_state(p, TermsState(cards={}))
        save_terms_state(p, TermsState(cards={102: CardSeen("e", NOW)}))
    assert load_terms_state(p).cards[102].digest == "e"


def test_decide_not_found_always_shown_even_when_digest_matches():
    d = decide(_card("not_found"), ENTRY, CardSeen("h", NOW), "h")
    assert d.show_not_found and not d.is_new
    assert d.show_not_found == _flags(d)["show_not_found"]


def test_decide_changed_without_findings_is_not_found_line():
    assert decide(_card("changed"), ENTRY, CardSeen("h", NOW), "h").show_not_found


def test_decide_verified_findings_repeat_until_digest_changes_system_side():
    c = _card("changed", findings=(_finding(),))
    d = decide(c, ENTRY, CardSeen("h", NOW), "h")
    assert d.show_findings and not d.show_not_found and not d.is_new


def test_decide_announced_every_run():
    nt = NextTerms(announced=True, period=(None, None), rules=(), source=None, quote=None)
    d = decide(_card("announced", next_terms=nt), ENTRY, CardSeen("h", NOW), "h")
    assert d.show_announced


def test_decide_unverified_and_signal_suppressed_on_match():
    c = _card("signal", unverified=(_finding(),))
    same = decide(c, ENTRY, CardSeen("h", NOW), "h")
    assert not same.show_unverified and not same.show_signal
    for prev in (None, CardSeen("old", NOW)):
        d = decide(c, ENTRY, prev, "h")
        assert d.show_unverified and d.show_signal and d.is_new


def test_decide_next_terms_unverified_url_alone_is_news_once():
    c = _card(next_terms_unverified_url="https://evil.example/n")
    assert decide(c, ENTRY, None, "h").show_unverified
    assert not decide(c, ENTRY, CardSeen("h", NOW), "h").show_unverified


def test_decide_period_only_line():
    c = _card("unchanged", current_period_end=_period())
    assert decide(c, ENTRY, None, "h").show_period_only
    assert not decide(c, ENTRY, CardSeen("h", NOW), "h").show_period_only
    assert not decide(_card("unchanged"), ENTRY, None, "h").show_period_only


def test_decide_truncated_only_card_is_shown_on_digest_match():
    d = decide(_card("unchanged", truncated=(TruncatedSource("https://bank.example/x", True, ""),)), ENTRY, CardSeen("h", NOW), "h")
    assert d.show_truncated and any(v for k, v in _flags(d).items())


def test_decide_changed_card_shows_period_line():
    c = _card("changed", findings=(_finding(),), current_period_end=_period())
    assert decide(c, ENTRY, CardSeen("h", NOW), "h").show_period
    assert not decide(_card("unchanged", current_period_end=_period()), ENTRY, CardSeen("h", NOW), "h").show_period


def test_decide_quiet_card_shows_nothing():
    d = decide(_card("unchanged"), ENTRY, CardSeen("h", NOW), "h")
    assert not any(_flags(d).values())


# --- final fix B3 --------------------------------------------------------------------------------------------------

def test_deeply_nested_state_is_state_error(tmp_path):
    p = tmp_path / "t.json"
    p.write_text("[" * 100000)
    with pytest.raises(TermsStateError) as ei:
        load_terms_state(p)
    assert ei.value.__cause__ is None


def test_save_rejects_naive_seen_at_and_leaves_no_tmp(tmp_path):
    p = tmp_path / "t.json"
    with pytest.raises(TermsStateError):
        save_terms_state(p, TermsState(cards={101: CardSeen("d", datetime(2026, 11, 3, 1, 0))}))
    assert not p.exists() and list(tmp_path.iterdir()) == []


def test_failed_save_removes_tmp_file(tmp_path, monkeypatch):
    p = tmp_path / "t.json"
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        save_terms_state(p, TermsState(cards={101: CardSeen("d", NOW)}))
    assert list(tmp_path.iterdir()) == []


def test_lock_released_and_fd_closed_on_exception(tmp_path, monkeypatch):
    p = tmp_path / "t.json"
    calls, closed, real_close = [], [], os.close
    real_flock = fcntl.flock
    monkeypatch.setattr(fcntl, "flock", lambda fd, op: (calls.append(op), real_flock(fd, op))[1])
    monkeypatch.setattr(os, "close", lambda fd: (closed.append(fd), real_close(fd))[1])
    with pytest.raises(RuntimeError):
        with terms_lock(p):
            raise RuntimeError("inside")
    assert calls == [fcntl.LOCK_EX, fcntl.LOCK_UN] and len(closed) == 1
    with pytest.raises(OSError):
        os.fstat(closed[0])
    fd = os.open(p.with_suffix(".lock"), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # free again
    finally:
        os.close(fd)
