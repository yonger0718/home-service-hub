"""CLI tests: the API is an httpx.MockTransport serving a fixture Snapshot; hermes is a recorder."""
from __future__ import annotations

import stat
from pathlib import Path
from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import pytest
from notify import cli, render, send
from notify.cathay import LATEST_PATH, PENDING_PATH
from notify.state import load_state
from tests.notify.test_cathay import BAD_CASES, FAULT_PATH, LATEST as _L, PENDING as _P, TIMEOUT_CASE, item as cathay_item, pending as cathay_pending

SECRET = "secret-token"


def busy(account_id: int, today: date) -> list[dict]:
    """One fictional expense a week over the read window."""
    return [{"id": 50000 + k, "kind": "expense", "amount": "-100", "account_id": account_id, "parent_entry_id": None,
             "entry_date": (today - timedelta(days=7 * k)).isoformat(),
             "posted_date": (today - timedelta(days=7 * k)).isoformat()} for k in range(18)]


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
            ids, kind = [int(i) for i in params.get_list("account_id")], params.get("kind")
            if kind is None:  # card activity; a world without it is busy every week, so no cycle is quiet
                items = [e for key in ids for e in (busy(key, snap.today) if snap.card_activity is None
                                                    else snap.card_activity.get(key, []))]
                return httpx.Response(200, json={"items": items, "total": len(items), "limit": 500, "offset": 0})
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


PORTFOLIO = "http://portfolio.invalid"


def _portfolio_row(day: date) -> dict:
    return {"run_at": f"{day.isoformat()}T06:41:00+08:00", "result": "ok", "code": None, "files_seen": 1, "posted": 1}


class Env:
    def __init__(self, tmp_path, monkeypatch, snap, *, status=None):
        self.state_path = tmp_path / "state" / "state.json"
        token = tmp_path / "token"
        token.write_text(SECRET + "\n")
        self.cfg = str(tmp_path / "notify.toml")
        (tmp_path / "notify.toml").write_text(
            f'api_base = "http://api.invalid"\ntoken_file = "{token}"\n'
            f'hub_base_url = "https://example.invalid/hub"\ndiscord_target = "discord:000000000000000000"\n'
            f'state_file = "{self.state_path}"\n'
            f'portfolio_base = "{PORTFOLIO}"\n')
        self.sent: list[tuple[list[str], str]] = []
        self._fail_on: int | None = None
        self.today = None  # the as-of day of the current run, captured below
        self.portfolio: dict = {}  # path -> JSON | httpx.Response overriding the healthy default
        self.portfolio_paths: list[str] = []  # every portfolio path the mock served
        api = _routes(snap, status=status)

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path not in (PENDING_PATH, LATEST_PATH):
                return api(request)
            self.portfolio_paths.append(path)
            row = _portfolio_row(self.today)  # fresh: this morning's run, 06:41 Asia/Taipei
            default = {PENDING_PATH: {"items": [], "total": 0, "truncated": False},
                       LATEST_PATH: {"latest": row, "latest_ok": row}}[path]
            value = self.portfolio.get(path, default)
            if isinstance(value, Exception):
                raise value
            return value if isinstance(value, httpx.Response) else httpx.Response(200, json=value)

        transport = httpx.MockTransport(handler)
        real = httpx.Client
        real_run = cli._run

        def run(args, cfg, today):
            self.today = today
            return real_run(args, cfg, today)

        monkeypatch.setattr(cli, "_run", run)
        monkeypatch.setattr(cli.httpx, "Client", lambda **kw: real(transport=transport, **kw))
        monkeypatch.setattr(send, "_run_hermes", self._run)

    def config_without_portfolio(self) -> str:
        """A config identical to the main one but with the section off and its own state file."""
        text = Path(self.cfg).read_text().replace(f'portfolio_base = "{PORTFOLIO}"\n', "")
        text = text.replace(str(self.state_path), str(self.state_path.parent / "off" / "state.json"))
        path = Path(self.cfg).with_name("notify-off.toml")
        path.write_text(text)
        return str(path)

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
def cli_env_skip(tmp_path, monkeypatch, fx):
    audit = [fx.audit_event(id=9, reason="no_link", created_at="2026-11-03T06:30:00+08:00", card_account_id=93,
                            amount="4378")]
    return Env(tmp_path, monkeypatch, fx.snapshot(audit=audit))


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
    text = cli_env_far_due.failure_file.read_text()
    assert text.startswith("❌ 帳務通知失敗：daily 2028-01-03 CalendarExhausted 20")  # the message names the dates
    assert "last known day" in text
    assert not cli_env_far_due.state_path.exists()


def test_weekly_always_sends(cli_env_quiet):
    rc = main(["weekly", "--as-of", "2026-11-21", "--send", "--config", cli_env_quiet.cfg])
    assert rc == 0 and "✅ 本週沒有待處理" in cli_env_quiet.sent[0][1]
    assert load_state(cli_env_quiet.state_path).last_weekly_run is not None


def test_api_failure_writes_failure_file_and_sends_nothing(cli_env_401, capsys):
    rc = main(["daily", "--as-of", "2026-11-03", "--send", "--config", cli_env_401.cfg])
    assert cli_env_401.sent == [] and stat.S_IMODE(cli_env_401.failure_file.stat().st_mode) == 0o600
    text = cli_env_401.failure_file.read_text()
    assert rc == 1 and text.startswith("❌ 帳務通知失敗：daily 2026-11-03 ApiError 401") and SECRET not in text
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
    assert rc == 0 and cli_env_401.sent[-1][1].startswith("❌ 帳務通知失敗：daily 2026-11-03 ApiError 401")
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


def test_same_day_rerun_with_skip_sends_nothing(cli_env_skip, capsys):
    # Ruling 17: the first send consumes the skip (skips_seen); the re-run must still produce the same trigger set
    args = ["daily", "--as-of", "2026-11-03", "--send", "--config", cli_env_skip.cfg]
    assert main(args) == 0 and len(cli_env_skip.sent) == 1 and "⏭ 自動扣繳跳過" in cli_env_skip.sent[0][1]
    assert "skip:9" in load_state(cli_env_skip.state_path).last_send.triggers
    capsys.readouterr()
    assert main(args) == 0 and len(cli_env_skip.sent) == 1
    assert "nothing to send" in capsys.readouterr().err


def test_token_config_error_names_the_key_and_writes_prefixed_file(cli_env, capsys):
    token = cli_env.state_path.parent.parent / "token"
    token.write_text("\n")
    rc = main(["weekly", "--as-of", "2026-11-07", "--send", "--config", cli_env.cfg])
    assert rc == 1 and cli_env.sent == []
    assert cli_env.failure_file.read_text() == "❌ 帳務通知失敗：weekly 2026-11-07 ConfigError token"
    assert "ConfigError token" in capsys.readouterr().err


def test_bad_config_names_the_key(tmp_path, capsys):
    cfg = tmp_path / "notify.toml"
    cfg.write_text('api_base = "http://api.invalid"\n')
    assert main(["daily", "--as-of", "2026-11-03", "--config", str(cfg)]) == 1
    assert "❌ 帳務通知失敗：daily 2026-11-03 ConfigError token_file" in capsys.readouterr().err


# --- 國泰證券 statement section (spec §7.2) -------------------------------------------------------------------------

HELD = cathay_pending([cathay_item("2026-10-27", "held", "unsupported_category")])
CATHAY_BLOCK = "**國泰對帳單**\n• 10/27 保留：含融資融券交易"
UNREADABLE_LINE = "國泰對帳單狀態讀不到"


def _sent_text(env, argv):
    env.sent.clear()
    assert main(argv + ["--send"]) == 0
    return "\n\n".join(text for _, text in env.sent)


def test_daily_only_cathay_section_sends(cli_env_quiet):
    cli_env_quiet.portfolio[PENDING_PATH] = HELD
    assert main(["daily", "--as-of", "2026-11-20", "--send", "--config", cli_env_quiet.cfg]) == 0
    assert len(cli_env_quiet.sent) == 1 and CATHAY_BLOCK in cli_env_quiet.sent[0][1]
    assert set(cli_env_quiet.portfolio_paths) == {PENDING_PATH, LATEST_PATH}


def test_daily_dedupe_includes_cathay_keys(cli_env_quiet):
    cli_env_quiet.portfolio[PENDING_PATH] = HELD
    args = ["daily", "--as-of", "2026-11-20", "--send", "--config", cli_env_quiet.cfg]
    assert main(args) == 0 and len(cli_env_quiet.sent) == 1
    assert "cathay:2026-10-27:held" in load_state(cli_env_quiet.state_path).last_send.triggers
    assert main(args) == 0 and len(cli_env_quiet.sent) == 1  # same day, same lines
    assert main(["daily", "--as-of", "2026-11-21", "--send", "--config", cli_env_quiet.cfg]) == 0
    assert len(cli_env_quiet.sent) == 2  # a new day sends again


def test_daily_portfolio_down_debits_still_sent(cli_env):
    cli_env.portfolio[PENDING_PATH] = httpx.Response(500, json={})
    base = _sent_text(cli_env, ["daily", "--as-of", "2026-11-03", "--config", cli_env.config_without_portfolio()])
    cli_env.sent.clear()
    assert main(["daily", "--as-of", "2026-11-03", "--send", "--config", cli_env.cfg]) == 0
    text = "\n\n".join(t for _, t in cli_env.sent)
    assert UNREADABLE_LINE in text and "旅遊卡" in text
    assert text.replace(f"\n\n**國泰對帳單**\n• {UNREADABLE_LINE}", "") == base


def test_daily_oversized_portfolio_body_debits_within_limit(cli_env):
    cli_env.portfolio[PENDING_PATH] = httpx.Response(200, content=b" " * 70000)
    assert main(["daily", "--as-of", "2026-11-03", "--send", "--config", cli_env.cfg]) == 0
    assert cli_env.sent and all(len(t) <= render.LIMIT for _, t in cli_env.sent)
    assert UNREADABLE_LINE in "\n".join(t for _, t in cli_env.sent)


def test_daily_stale_run_is_listed(cli_env_quiet):
    cli_env_quiet.portfolio[_L] = {"latest": None, "latest_ok": None}
    assert main(["daily", "--as-of", "2026-11-20", "--send", "--config", cli_env_quiet.cfg]) == 0
    assert "國泰對帳單今天沒有執行" in cli_env_quiet.sent[0][1]


@pytest.mark.parametrize("fault_path", [_P, _L])
def test_failure_goes_through_mock_on_either_endpoint(cli_env_quiet, fault_path):
    cli_env_quiet.portfolio[fault_path] = httpx.Response(500, json={})
    assert main(["daily", "--as-of", "2026-11-20", "--send", "--config", cli_env_quiet.cfg]) == 0
    assert fault_path in cli_env_quiet.portfolio_paths and UNREADABLE_LINE in cli_env_quiet.sent[0][1]


@pytest.mark.parametrize("command,as_of", [("daily", "2026-11-03"), ("weekly", "2026-11-07")])
@pytest.mark.parametrize("name", sorted(BAD_CASES) + ["timeout"])
def test_reader_failure_matrix(cli_env, command, as_of, name):
    for path, body in (TIMEOUT_CASE if name == "timeout" else BAD_CASES[name]).items():
        cli_env.portfolio[path] = body
    base = _sent_text(cli_env, [command, "--as-of", as_of, "--config", cli_env.config_without_portfolio()])
    cli_env.sent.clear()
    assert main([command, "--as-of", as_of, "--send", "--config", cli_env.cfg]) == 0
    assert all(len(t) <= render.LIMIT for _, t in cli_env.sent)
    text = "\n\n".join(t for _, t in cli_env.sent)
    block = "**國泰對帳單**" if command == "daily" else "**國泰對帳單待處理**"
    assert text.replace(f"\n\n{block}\n• {UNREADABLE_LINE}", "") == base
    assert UNREADABLE_LINE in text
    if name != "timeout":
        assert FAULT_PATH[name] in cli_env.portfolio_paths, "the failing path must have been requested"
    else:
        assert set(cli_env.portfolio_paths) & {_P, _L}


def test_default_routes_add_nothing(cli_env):
    base = _sent_text(cli_env, ["daily", "--as-of", "2026-11-03", "--config", cli_env.config_without_portfolio()])
    cli_env.portfolio_paths.clear()
    assert _sent_text(cli_env, ["daily", "--as-of", "2026-11-03", "--config", cli_env.cfg]) == base
    assert {_P, _L} <= set(cli_env.portfolio_paths)
