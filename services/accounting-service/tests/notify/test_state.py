from datetime import date, datetime, timezone
from decimal import Decimal

from notify.state import LastSend, SourceState, State, StatementState, load_state, save_state


def _state():
    return State(
        statements={7: StatementState(date(2026, 11, 3)), -9: StatementState(date(2026, 11, 2))},
        sources={76: SourceState(Decimal("3900"))},
        skips_seen={41},
        last_daily_run=datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc),
        last_weekly_run=None,
        last_send=LastSend(date(2026, 11, 3), frozenset({"new:7"})),
    )


def test_state_roundtrip_and_corrupt(tmp_path, capsys):
    p = tmp_path / "s" / "state.json"
    st = _state()
    save_state(p, st)
    assert load_state(p) == st
    assert oct(p.stat().st_mode)[-3:] == "600" and oct(p.parent.stat().st_mode)[-3:] == "700"
    p.write_text("{")
    assert load_state(p) == State.empty()
    assert capsys.readouterr().err.count("\n") == 1


def test_missing_is_empty(tmp_path, capsys):
    assert load_state(tmp_path / "nope.json") == State.empty()
    assert capsys.readouterr().err == ""


def test_none_fields_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    st = State.empty()
    st.statements[1] = StatementState(None)
    st.sources[2] = SourceState(None)
    save_state(p, st)
    assert load_state(p) == st


def test_wrong_shape_is_empty(tmp_path):
    p = tmp_path / "state.json"
    p.write_text('{"statements": {"x": {"first_notified": "bad"}}}')
    assert load_state(p) == State.empty()


def test_non_utf8_state_is_empty(tmp_path, capsys):
    p = tmp_path / "state.json"
    p.write_bytes(b"\xff\xfe")
    assert load_state(p) == State.empty()
    assert capsys.readouterr().err.count("\n") == 1
