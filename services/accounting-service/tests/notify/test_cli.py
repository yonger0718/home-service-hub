"""CLI tests: the API is an httpx.MockTransport serving a fixture Snapshot; hermes is a recorder."""
from __future__ import annotations

import stat
from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import pytest
from notify import cli, render, send
from notify.state import load_state

SECRET = "secret-token"


def _routes(snap, *, status: int | None = None):
    """Build a handler answering the read-only paths from a fixture Snapshot (status forces an error)."""
    cal = snap.calendar

    def handler(request: httpx.Request) -> httpx.Response:
        if status is not None:
            return httpx.Response(status, json={"detail": f"bad {SECRET}"})
        path, params = request.url.path, request.url.params
        if path == "/settings/reconciliation":
            return httpx.Response(200, json=snap.settings)
        if path == "/calendar/business-days":
            return httpx.Response(200, json={"holidays": sorted(d.isoformat() for d in cal.holidays),
                                             "workdays": sorted(d.isoformat() for d in cal.workdays),
                                             "last_known_day": cal.last_known_day.isoformat()})
        if path == "/accounts":
            return httpx.Response(200, json=snap.accounts)
        if path.startswith("/accounts/") and path.endswith("/statements"):
            return httpx.Response(200, json=snap.statements.get(int(path.split("/")[2]), []))
        if path == "/entries":
            ids, kind = [int(i) for i in params.get_list("account_id")], params["kind"]
            pool = snap.payments if kind == "transfer_in" else snap.source_entries
            items = [e for key in ids for e in pool.get(key, []) if kind == "transfer_in" or e["kind"] == kind]
            return httpx.Response(200, json={"items": items, "total": len(items), "limit": 500, "offset": 0})
        if path == "/schedules/instances":
            return httpx.Response(200, json=snap.pending)
        if path == "/reconciliation/audit":
            return httpx.Response(200, json=snap.audit)
        if path == "/reconciliation/inbox":
            return httpx.Response(200, json=snap.inbox or {"accounts": []})
        if path == "/reconciliation/cases":
            return httpx.Response(200, json=snap.open_cases.get(int(params["account_id"]), []))
        return httpx.Response(404)

    return handler


class Env:
    def __init__(self, tmp_path, monkeypatch, snap, *, status=None):
        self.state_path = tmp_path / "state" / "state.json"
        token = tmp_path / "token"
        token.write_text(SECRET + "\n")
        self.cfg = str(tmp_path / "notify.toml")
        (tmp_path / "notify.toml").write_text(
            f'api_base = "http://api.invalid"\ntoken_file = "{token}"\n'
            f'hub_base_url = "https://example.invalid/hub"\ndiscord_target = "discord:000000000000000000"\n'
            f'state_file = "{self.state_path}"\n')
        self.sent: list[tuple[list[str], str]] = []
        self._fail_on: int | None = None
        transport = httpx.MockTransport(_routes(snap, status=status))
        real = httpx.Client
        monkeypatch.setattr(cli.httpx, "Client", lambda **kw: real(transport=transport, **kw))
        monkeypatch.setattr(send, "_run_hermes", self._run)

    def _run(self, argv, text):
        self.sent.append((argv, text))
        return 1 if self._fail_on == len(self.sent) else 0

    def fail_on_chunk(self, n: int) -> None:
        self._fail_on = n

    @property
    def failure_file(self):
        return self.state_path.parent / "last_failure.txt"


@pytest.fixture
def cli_env(tmp_path, monkeypatch, fx):
    return Env(tmp_path, monkeypatch, fx.snapshot())


@pytest.fixture
def cli_env_quiet(tmp_path, monkeypatch, fx):
    # nothing due or outstanding: every statement 0, the rule-only card has its cycle
    zero = {n: [fx.statement_default(n, total="0")] for n in ("旅遊卡", "加油卡", "黑啤卡")}
    zero["超市卡"] = [fx.statement_default("超市卡", total="0")]
    return Env(tmp_path, monkeypatch, fx.weekly_snapshot(
        statements_for=zero, source_overrides={93: {"auto_pay_account_id": 76}, 98: {"auto_pay_account_id": 76}}))


@pytest.fixture
def cli_env_far_due(tmp_path, monkeypatch, fx):
    return Env(tmp_path, monkeypatch, fx.snapshot())


@pytest.fixture
def cli_env_401(tmp_path, monkeypatch, fx):
    return Env(tmp_path, monkeypatch, fx.snapshot(), status=401)


@pytest.fixture
def cli_env_two_chunks(tmp_path, monkeypatch, fx):
    env = Env(tmp_path, monkeypatch, fx.snapshot())
    monkeypatch.setattr(cli, "render_daily", lambda rep, cfg: ["chunk one", "chunk two"])
    return env


main = cli.main


def test_dry_run_never_sends(cli_env, capsys):
    rc = main(["daily", "--as-of", "2026-11-03", "--dry-run", "--config", cli_env.cfg])
    assert rc == 0 and cli_env.sent == [] and not cli_env.state_path.exists()
    assert "旅遊卡" in capsys.readouterr().out


def test_no_flag_prints_and_touches_nothing(cli_env, capsys):
    assert main(["daily", "--as-of", "2026-11-03", "--config", cli_env.cfg]) == 0
    assert cli_env.sent == [] and not cli_env.state_path.exists() and "旅遊卡" in capsys.readouterr().out


def test_send_writes_state_and_calls_hermes(cli_env):
    rc = main(["daily", "--as-of", "2026-11-03", "--send", "--config", cli_env.cfg])
    assert rc == 0 and len(cli_env.sent) == 1
    assert cli_env.sent[0][0] == ["hermes", "send", "-t", "discord:000000000000000000", "-q"]
    state = load_state(cli_env.state_path)
    assert state.last_daily_run is not None and state.last_send.day == date(2026, 11, 3)
    assert stat.S_IMODE(cli_env.state_path.stat().st_mode) == 0o600


def test_daily_nothing_to_send_still_writes_state(cli_env_quiet):
    rc = main(["daily", "--as-of", "2026-11-20", "--send", "--config", cli_env_quiet.cfg])
    assert rc == 0 and cli_env_quiet.sent == []
    assert load_state(cli_env_quiet.state_path).last_daily_run is not None


def test_same_day_rerun_sends_nothing(cli_env):
    assert main(["daily", "--as-of", "2026-11-03", "--send", "--config", cli_env.cfg]) == 0
    assert len(cli_env.sent) == 1
    assert main(["daily", "--as-of", "2026-11-03", "--send", "--config", cli_env.cfg]) == 0
    assert len(cli_env.sent) == 1


def test_calendar_exhausted_is_failure(cli_env_far_due):
    rc = main(["daily", "--as-of", "2028-01-03", "--send", "--config", cli_env_far_due.cfg])
    assert rc == 1 and cli_env_far_due.sent == []
    assert "CalendarExhausted" in cli_env_far_due.failure_file.read_text()
    assert not cli_env_far_due.state_path.exists()


def test_weekly_always_sends(cli_env_quiet):
    rc = main(["weekly", "--as-of", "2026-11-21", "--send", "--config", cli_env_quiet.cfg])
    assert rc == 0 and "✅ 本週沒有待處理" in cli_env_quiet.sent[0][1]
    assert load_state(cli_env_quiet.state_path).last_weekly_run is not None


def test_api_failure_writes_failure_file_and_sends_nothing(cli_env_401, capsys):
    rc = main(["daily", "--as-of", "2026-11-03", "--send", "--config", cli_env_401.cfg])
    assert cli_env_401.sent == [] and stat.S_IMODE(cli_env_401.failure_file.stat().st_mode) == 0o600
    text = cli_env_401.failure_file.read_text()
    assert rc == 1 and text.startswith("❌ 帳務通知失敗：ApiError 401") and SECRET not in text
    err = capsys.readouterr().err
    assert "ApiError 401" in err and SECRET not in err and not cli_env_401.state_path.exists()


def test_bad_config_is_failure(tmp_path, capsys):
    rc = main(["daily", "--send", "--config", str(tmp_path / "missing.toml")])
    assert rc == 1 and "ConfigError" in capsys.readouterr().err


def test_dry_run_failure_writes_no_failure_file(cli_env_401):
    assert main(["daily", "--as-of", "2026-11-03", "--dry-run", "--config", cli_env_401.cfg]) == 1
    assert not cli_env_401.failure_file.exists()


def test_send_failure_leaves_state_absent(cli_env_two_chunks):
    cli_env_two_chunks.fail_on_chunk(2)
    rc = main(["daily", "--as-of", "2026-11-03", "--send", "--config", cli_env_two_chunks.cfg])
    assert rc == 1 and len(cli_env_two_chunks.sent) == 2 and not cli_env_two_chunks.state_path.exists()
    assert "SendError" in cli_env_two_chunks.failure_file.read_text()


def test_failed_subcommand_sends_stored_text_then_removes_it(cli_env_401):
    main(["daily", "--as-of", "2026-11-03", "--send", "--config", cli_env_401.cfg])
    rc = main(["failed", "homehub-notify-daily.service", "--config", cli_env_401.cfg])
    assert rc == 0 and cli_env_401.sent[-1][1].startswith("❌ 帳務通知失敗：ApiError 401")
    assert not cli_env_401.failure_file.exists()


def test_failed_subcommand_generic_without_file(cli_env):
    rc = main(["failed", "homehub-notify-daily.service", "--config", cli_env.cfg])
    assert rc == 0 and cli_env.sent[-1][1] == (
        "❌ 帳務通知失敗：homehub-notify-daily.service 非正常結束，請看 journalctl --user -u homehub-notify-daily.service")
    assert not cli_env.state_path.exists()


def test_failed_subcommand_survives_send_error(cli_env, capsys):
    cli_env.fail_on_chunk(1)
    assert main(["failed", "homehub-notify-daily.service", "--config", cli_env.cfg]) == 0
    assert "SendError" in capsys.readouterr().err


def test_bad_args():
    assert main(["monthly"]) == 2


def test_deliver_runs_hermes_per_chunk_in_order():
    calls = []
    send.deliver(["a", "b"], "discord:1", runner=lambda argv, text: calls.append((argv, text)) or 0)
    assert calls == [(["hermes", "send", "-t", "discord:1", "-q"], "a"), (["hermes", "send", "-t", "discord:1", "-q"], "b")]


def test_deliver_nonzero_raises_send_error():
    with pytest.raises(send.SendError):
        send.deliver(["a", "b"], "discord:1", runner=lambda argv, text: 3)


def test_same_day_rerun_with_new_overdue_and_short_keys_sends_nothing(cli_env, capsys):
    args = ["daily", "--as-of", "2026-11-04", "--send", "--config", cli_env.cfg]
    assert main(args) == 0 and len(cli_env.sent) == 1
    first = load_state(cli_env.state_path).last_send.triggers
    assert any(k.startswith("new:") for k in first) and any(k.startswith(("overdue:", "short:")) for k in first)
    capsys.readouterr()
    assert main(args) == 0 and len(cli_env.sent) == 1
    assert "nothing to send" in capsys.readouterr().err


def test_successful_send_clears_stale_failure_file(cli_env):
    cli_env.failure_file.parent.mkdir(parents=True, exist_ok=True)
    cli_env.failure_file.write_text("❌ 帳務通知失敗：old")
    assert main(["daily", "--as-of", "2026-11-03", "--send", "--config", cli_env.cfg]) == 0
    assert not cli_env.failure_file.exists()


def test_weekly_after_daily_shows_last_daily_run_stamp(cli_env_quiet):
    assert main(["daily", "--as-of", "2026-11-20", "--send", "--config", cli_env_quiet.cfg]) == 0
    assert main(["weekly", "--as-of", "2026-11-21", "--send", "--config", cli_env_quiet.cfg]) == 0
    text = cli_env_quiet.sent[-1][1]
    assert "最近一次每日檢查" in text and "尚無" not in text
