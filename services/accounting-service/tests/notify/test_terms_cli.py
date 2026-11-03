"""`notify terms`: archive, send ordering, failure path. API = MockTransport, hermes = recorder, run_hermes = fake."""
from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from notify import cli, send, terms
from notify.hermes_run import HermesError, HermesResult
from notify.terms_state import load_terms_state
from tests.notify.notify_fakes import make_client
from tests.notify.terms_fixtures import FULL_STDOUT, NO_JSON_STDOUT

SECRET = "api-token-secret"
FROZEN = datetime(2026, 11, 3, 23, 30, 5, tzinfo=timezone.utc)  # 2026-11-04 07:30:05 in Taipei

TERMS_BLOCK = '''
[terms]
report_dir = "{reports}"
hermes_model = "m"
terms_state_file = "{tstate}"

[terms.cards."101"]
official = "Alpha"
bank_domains = ["example-bank.com"]
tier = "precise"

[terms.cards."102"]
official = "Beta"
bank_domains = ["example-bank.com"]
tier = "signal"

[terms.cards."301"]
official = "Gamma"
bank_domains = ["us-example.com"]
tier = "precise"
year_end_window = false
'''


class TermsEnv:
    def __init__(self, tmp_path, monkeypatch, *, block: str | None = None, stdout: str = FULL_STDOUT):
        self.reports = tmp_path / "reports"
        self.tstate = tmp_path / "state" / "terms-state.json"
        self.failure = tmp_path / "state" / "last_failure.txt"
        token = tmp_path / "token"
        token.write_text(SECRET + "\n")
        body = (TERMS_BLOCK.format(reports=self.reports, tstate=self.tstate) if block is None else block)
        self.cfg = str(tmp_path / "notify.toml")
        Path(self.cfg).write_text(
            f'api_base = "http://api.invalid"\ntoken_file = "{token}"\nhub_base_url = "https://h.invalid"\n'
            f'discord_target = "discord:1"\nstate_file = "{tmp_path / "state" / "state.json"}"\n' + body)
        self.sent: list[tuple[list[str], str]] = []
        self.fail_send = False
        self.hermes_calls: list[dict] = []
        self.hermes_stdout = stdout
        self.hermes_error: Exception | None = None
        self.auth: list[str] = []
        rules = {"/reward-rules": {"accounts": []}}
        for aid in (101, 102, 301):
            rules[f"/accounts/{aid}/reward-rules"] = []
        real = httpx.Client
        client, _ = make_client(rules)
        transport = client._transport

        def handler(request):
            self.auth.append(request.headers.get("authorization", ""))
            return transport.handler(request)

        monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
        monkeypatch.setattr(send, "_run_hermes", self._send)
        monkeypatch.setattr(terms, "run_hermes", self._hermes)
        monkeypatch.setattr(terms, "_utcnow", lambda: FROZEN)

    def _send(self, argv, text):
        self.sent.append((argv, text))
        return 1 if self.fail_send else 0

    def _hermes(self, **kw):
        self.hermes_calls.append(kw)
        if self.hermes_error is not None:
            raise self.hermes_error
        archive = kw["archive"]
        for name, text in (("hermes.out", self.hermes_stdout), ("hermes.err", "")):  # as run_hermes: 0600
            with os.fdopen(os.open(archive / name, os.O_WRONLY | os.O_CREAT, 0o600), "w") as fh:
                fh.write(text)
        return HermesResult(self.hermes_stdout, "", 0, 1.0, False)

    def run(self, *extra):
        return cli.main(["terms", "--as-of", "2026-11-03", "--config", self.cfg, *extra])

    @property
    def archives(self):
        return sorted(self.reports.iterdir()) if self.reports.exists() else []


@pytest.fixture
def tenv(tmp_path, monkeypatch):
    return TermsEnv(tmp_path, monkeypatch)


def test_dry_run_writes_archive_prints_and_saves_nothing(tenv, capsys):
    assert tenv.run("--cause", "monthly", "--dry-run") == 0
    (arch,) = tenv.archives
    for name in ("input.json", "prompt.md", "hermes.out", "hermes.err", "result.json", "message.md"):
        assert (arch / name).exists()
    out = capsys.readouterr().out
    assert "回饋條款查核" in out and out.strip() == (arch / "message.md").read_text().replace("\n\n---\n\n", "\n---\n").strip()
    assert tenv.sent == [] and not tenv.tstate.exists() and not tenv.failure.exists()


def test_no_mode_flag_is_dry_run(tenv):
    assert tenv.run("--cause", "monthly") == 0
    assert tenv.sent == [] and not tenv.tstate.exists()


def test_send_saves_digests_only_after_deliver(tenv, monkeypatch):
    seen_at_send = []
    real = tenv._send
    monkeypatch.setattr(send, "_run_hermes", lambda a, t: (seen_at_send.append(tenv.tstate.exists()), real(a, t))[1])
    assert tenv.run("--cause", "monthly", "--send") == 0
    assert tenv.sent and seen_at_send == [False] * len(tenv.sent)
    st = load_terms_state(tenv.tstate)
    assert set(st.cards) == {101, 102, 301}
    assert all(c.seen_at == FROZEN for c in st.cards.values())
    assert stat.S_IMODE(tenv.tstate.stat().st_mode) == 0o600


def test_send_clears_stale_failure_file(tenv):
    tenv.failure.parent.mkdir(parents=True)
    tenv.failure.write_text("old")
    assert tenv.run("--cause", "monthly", "--send") == 0
    assert not tenv.failure.exists()


def test_year_end_with_no_cards_is_a_noop(tmp_path, monkeypatch, capsys):
    block = TERMS_BLOCK.format(reports=tmp_path / "reports", tstate=tmp_path / "s" / "t.json")
    block = block.replace('tier = "precise"\n\n[terms.cards."102"]', 'tier = "precise"\nyear_end_window = false\n\n[terms.cards."102"]')
    block = block.replace('tier = "signal"\n', 'tier = "signal"\nyear_end_window = false\n')
    env = TermsEnv(tmp_path, monkeypatch, block=block)
    assert env.run("--cause", "year-end", "--send") == 0
    assert "no cards for --cause year-end" in capsys.readouterr().err
    assert env.hermes_calls == [] and env.archives == [] and env.sent == [] and not env.tstate.exists()


def test_no_json_block_fails_run_keeps_archive(tenv, capsys):
    tenv.hermes_stdout = NO_JSON_STDOUT
    assert tenv.run("--cause", "monthly", "--send") == 1
    (arch,) = tenv.archives
    line = f"❌ 帳務通知失敗：terms 2026-11-03 TermsParseError parse {arch}"
    assert tenv.failure.read_text() == line and line in capsys.readouterr().err
    assert not (arch / "result.json").exists() and (arch / "hermes.out").exists()
    assert tenv.sent == [] and not tenv.tstate.exists()


def test_failure_file_only_with_send(tenv):
    tenv.hermes_stdout = NO_JSON_STDOUT
    assert tenv.run("--cause", "monthly", "--dry-run") == 1
    assert not tenv.failure.exists()


def test_send_failure_keeps_archive_and_state(tenv):
    tenv.fail_send = True
    assert tenv.run("--cause", "monthly", "--send") == 1
    (arch,) = tenv.archives
    assert (arch / "message.md").exists() and not tenv.tstate.exists()
    assert "SendError" in tenv.failure.read_text()


def test_hermes_failure_reason_has_no_output_text(tenv, capsys):
    tenv.hermes_stdout = "SECRET MODEL TEXT"

    def boom(**kw):
        (kw["archive"] / "hermes.out").write_text("SECRET MODEL TEXT")
        raise HermesError(kw["archive"])

    terms_run = terms.run_hermes
    try:
        terms.run_hermes = boom
        assert tenv.run("--cause", "monthly", "--send") == 1
    finally:
        terms.run_hermes = terms_run
    (arch,) = tenv.archives
    text = tenv.failure.read_text()
    assert text == f"❌ 帳務通知失敗：terms 2026-11-03 HermesError hermes {arch}"
    assert "SECRET" not in text and "SECRET" not in capsys.readouterr().err
    assert tenv.sent == [] and not tenv.tstate.exists()


def test_missing_terms_section_exits_2_without_failure_file(tmp_path, monkeypatch, capsys):
    env = TermsEnv(tmp_path, monkeypatch, block="")
    assert env.run("--cause", "monthly", "--send") == 2
    assert not env.failure.exists() and "terms" in capsys.readouterr().err


def test_invalid_terms_section_exits_2_without_failure_file(tmp_path, monkeypatch):
    env = TermsEnv(tmp_path, monkeypatch, block='\n[terms]\nreport_dir = "/r"\nhermes_model = "m"\nhermes_toolsets = "all"\n')
    assert env.run("--cause", "monthly", "--send") == 2
    assert not env.failure.exists() and env.hermes_calls == []


def test_archive_stamp_is_utc(tenv):
    assert tenv.run("--cause", "monthly") == 0
    (arch,) = tenv.archives
    assert arch.name == "20261103T233005Z"  # not Taipei 20261104T073005


def test_duplicate_stamp_dir_fails_and_is_not_overwritten(tenv):
    existing = tenv.reports / "20261103T233005Z"
    existing.mkdir(parents=True)
    (existing / "keep").write_text("x")
    assert tenv.run("--cause", "monthly", "--send") == 1
    assert (existing / "keep").read_text() == "x" and list(existing.iterdir()) == [existing / "keep"]
    assert tenv.hermes_calls == [] and "ConfigError" in tenv.failure.read_text()


def test_corrupt_terms_state_fails_run(tenv):
    tenv.tstate.parent.mkdir(parents=True)
    tenv.tstate.write_text("{not json")
    assert tenv.run("--cause", "monthly", "--send") == 1
    (arch,) = tenv.archives
    assert tenv.failure.read_text() == f"❌ 帳務通知失敗：terms 2026-11-03 TermsStateError state {arch}"
    assert tenv.sent == [] and tenv.tstate.read_text() == "{not json"


def test_archive_modes_0700_0600(tenv):
    assert tenv.run("--cause", "monthly") == 0
    (arch,) = tenv.archives
    assert stat.S_IMODE(arch.stat().st_mode) == 0o700 and stat.S_IMODE(tenv.reports.stat().st_mode) == 0o700
    for f in arch.iterdir():
        assert stat.S_IMODE(f.stat().st_mode) == 0o600, f.name


def test_result_json_is_parsed_result(tenv):
    assert tenv.run("--cause", "monthly") == 0
    (arch,) = tenv.archives
    data = json.loads((arch / "result.json").read_text())
    assert [c["account_id"] for c in data["cards"]] == [101, 102, 301] and "fetch_log" in data
    assert isinstance(data["cards"][0]["findings"], list)


def test_cause_required(tenv):
    assert tenv.run() == 2


def test_hermes_env_is_allowlisted_only(tenv, monkeypatch):
    monkeypatch.setenv("HOMEHUB_TOKEN", "fake-homehub")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic")
    monkeypatch.setenv("PATH", "/usr/bin")
    assert tenv.run("--cause", "monthly") == 0
    env = tenv.hermes_calls[0]["env"]
    assert set(env) <= {"PATH", "HOME", "LANG", "TZ"} and env["PATH"] == "/usr/bin"
    assert "fake-homehub" not in env.values() and "fake-anthropic" not in env.values()


def test_api_token_used_but_never_archived(tenv):
    assert tenv.run("--cause", "monthly", "--send") == 0
    assert any(SECRET in a for a in tenv.auth)
    (arch,) = tenv.archives
    for f in arch.iterdir():
        assert SECRET not in f.read_text(), f.name
    assert SECRET not in json.dumps(tenv.hermes_calls[0], default=str)
