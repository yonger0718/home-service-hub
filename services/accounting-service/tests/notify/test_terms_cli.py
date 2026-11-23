"""`notify terms`: archive, send ordering, failure path. API = MockTransport, hermes = recorder, run_hermes = fake."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from notify import cli, hermes_run, send, terms, terms_parse
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
        self.hermes_fails = False
        self.auth: list[str] = []
        rules = {"/reward-rules": {"accounts": []}}
        for aid in (101, 102, 301):
            rules[f"/accounts/{aid}/reward-rules"] = []
        self.routes = rules
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
        archive = kw["archive"]
        if self.hermes_fails:
            (archive / "hermes.out").write_text("SECRET MODEL TEXT")
            raise HermesError(archive)
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
    captured = capsys.readouterr()
    out = captured.out
    assert "回饋條款查核" in out and out.strip() == (arch / "message.md").read_text().replace("\n\n---\n\n", "\n---\n").strip()
    assert tenv.sent == [] and not tenv.tstate.exists() and not tenv.failure.exists()
    assert "terms: 3 cards," in captured.err and "sent 0 messages" in captured.err


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
    tenv.hermes_fails = True
    assert tenv.run("--cause", "monthly", "--send") == 1
    (arch,) = tenv.archives
    text = tenv.failure.read_text()
    assert text == f"❌ 帳務通知失敗：terms 2026-11-03 HermesError hermes {arch}"
    assert "SECRET" not in text and "SECRET" not in capsys.readouterr().err
    assert tenv.sent == [] and not tenv.tstate.exists()


def test_oversized_prompt_fails_cleanly_through_real_run_hermes(tenv, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(terms, "build_prompt", lambda inp: "x" * 120_001)
    monkeypatch.setattr(terms, "run_hermes", lambda **kw: hermes_run.run_hermes(
        **kw, runner=lambda *a: calls.append(a)))
    assert tenv.run("--cause", "monthly", "--send") == 1
    (arch,) = tenv.archives
    assert calls == []
    assert tenv.failure.read_text() == f"❌ 帳務通知失敗：terms 2026-11-03 HermesError hermes {arch}"
    assert "Traceback" not in capsys.readouterr().err and tenv.sent == []


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
    assert tenv.hermes_calls == []
    assert tenv.failure.read_text() == f"❌ 帳務通知失敗：terms 2026-11-03 TermsArchiveError archive {existing}"


def test_archive_error_shape():
    exc = terms.TermsArchiveError(Path("/r/x"))
    assert str(exc) == "archive /r/x" and exc.reason == "archive" and exc.archive == Path("/r/x")
    assert terms.TermsArchiveError in cli.FAILURES
    assert cli._short(exc) == "TermsArchiveError archive /r/x"


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


def test_findings_joined_to_frozen_input(tenv, monkeypatch):
    # FULL_STDOUT: rule 162 ends_on, the model claims system 2026-12-31; HomeHub holds 2027-01-31 (final fix A3)
    tenv.routes["/accounts/101/reward-rules"] = [{"id": 162, "name": "R162", "is_enabled": True,
                                                  "ends_on": "2027-01-31", "rate": "3"}]
    assert tenv.run("--cause", "monthly") == 0
    (arch,) = tenv.archives
    (f,) = json.loads((arch / "result.json").read_text())["cards"][0]["findings"]
    assert f["rule_id"] == 162 and f["system"] == "2027-01-31"
    assert "· R162：結束日 官網 2027-03-31 / 系統 2027-01-31" in (arch / "message.md").read_text()
    tenv.routes["/accounts/101/reward-rules"] = []  # rule 162 unknown to HomeHub: never verified
    monkeypatch.setattr(terms, "_utcnow", lambda: datetime(2026, 11, 3, 23, 31, 5, tzinfo=timezone.utc))
    assert tenv.run("--cause", "monthly") == 0
    data = json.loads((tenv.archives[-1] / "result.json").read_text())["cards"][0]
    assert data["findings"] == [] and "未知規則" in [u["reason"] for u in data["unverified"]]
    assert "R162" not in (tenv.archives[-1] / "message.md").read_text()


def test_summary_count_uses_render_predicate(tenv, capsys, monkeypatch):
    from notify import terms_render
    assert tenv.run("--cause", "monthly") == 0
    (arch,) = tenv.archives
    blocks = (arch / "message.md").read_text().count("【")
    assert f"terms: 3 cards, {blocks} with items," in capsys.readouterr().err
    assert terms.has_items is terms_render.has_items  # one shared predicate (final fix B4)


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


# --- dismiss list (#287) ---

ON_BANK = "https://www.example-bank.com/cards/terms"
RULE_162 = [{"id": 162, "name": "R162", "is_enabled": True, "ends_on": "2026-12-31", "rate": "3"}]


def _denv(tmp_path, monkeypatch, extra: str, stdout: str = FULL_STDOUT) -> TermsEnv:
    block = TERMS_BLOCK.format(reports=tmp_path / "reports", tstate=tmp_path / "state" / "terms-state.json") + extra
    env = TermsEnv(tmp_path, monkeypatch, block=block, stdout=stdout)
    env.routes["/accounts/101/reward-rules"] = RULE_162
    return env


def _stdout(*cards: dict) -> str:
    return "prose\n```json\n" + json.dumps({"cards": list(cards), "fetch_log": {"tool_calls": 1, "failed": [],
                                                                               "truncated": []}}) + "\n```\n"


def test_dismissed_finding_leaves_the_message_and_stays_in_the_archive(tmp_path, monkeypatch):
    env = _denv(tmp_path, monkeypatch,
                '\n[[terms.cards."101".dismiss]]\nfield = "ends_on"\nrule = 162\nvalue = "2027-03-31"\n')
    assert env.run("--cause", "monthly") == 0
    (arch,) = env.archives
    message = (arch / "message.md").read_text()
    assert "R162" not in message and "找不到官網來源" not in message
    assert "已忽略 1 項：Alpha 1" in message.split("\n")
    (kept,) = json.loads((arch / "result.json").read_text())["cards"][0]["findings"]
    assert kept["rule_id"] == 162 and kept["site"] == "2027-03-31"  # the evidence is never dropped
    dismissed = json.loads((arch / "dismissed.json").read_text())
    assert list(dismissed) == ["101"] and dismissed["101"]["hidden_next_terms"] is False
    assert [f["rule_id"] for f in dismissed["101"]["hidden"]] == [162] and dismissed["101"]["stale"] == []
    assert stat.S_IMODE((arch / "dismissed.json").stat().st_mode) == 0o600
    assert not env.tstate.exists()  # a dry run


def test_dismissed_json_is_empty_without_entries(tenv):
    assert tenv.run("--cause", "monthly") == 0
    assert json.loads((tenv.archives[0] / "dismissed.json").read_text()) == {}


def test_pin_on_another_value_keeps_the_finding(tmp_path, monkeypatch):
    env = _denv(tmp_path, monkeypatch,
                '\n[[terms.cards."101".dismiss]]\nfield = "ends_on"\nrule = 162\nvalue = "2027-06-30"\n')
    assert env.run("--cause", "monthly") == 0
    message = (env.archives[0] / "message.md").read_text()
    assert "· R162：結束日 官網 2027-03-31" in message and "已忽略" not in message


def test_dismissed_announced_block_is_hidden(tmp_path, monkeypatch):
    env = _denv(tmp_path, monkeypatch, '\n[[terms.cards."301".dismiss]]\nfield = "next_terms"\nvalue = "2027-03-31"\n')
    assert env.run("--cause", "monthly") == 0
    (arch,) = env.archives
    message = (arch / "message.md").read_text()
    assert "下期條款已公告" not in message and "【Gamma】" not in message
    assert "已忽略 1 項：Gamma 1" in message.split("\n")
    hidden = json.loads((arch / "dismissed.json").read_text())["301"]
    assert hidden["hidden_next_terms"] is True and hidden["hidden_next_terms_rates"] == ["1.5/3000"]
    assert json.loads((arch / "result.json").read_text())["cards"][2]["next_terms"]["announced"] is True


def test_send_saves_the_digest_of_the_filtered_card(tmp_path, monkeypatch):
    def out(quote):
        return _stdout(
            {"account_id": 101, "status": "changed",
             "current_period_end": {"date": "2026-12-31", "source": ON_BANK, "quote": quote},
             "findings": [{"rule_id": 162, "field": "ends_on", "site": "2027-03-31", "system": "2026-12-31",
                           "source": ON_BANK, "quote": quote}]},
            {"account_id": 102, "status": "signal", "notes": "same as before"},
            {"account_id": 301, "status": "unchanged"})

    env = _denv(tmp_path, monkeypatch, '\n[[terms.cards."101".dismiss]]\nfield = "ends_on"\nrule = 162\n',
                stdout=out("valid until the end of 2026"))
    assert env.run("--cause", "monthly", "--send") == 0
    first = (env.archives[0] / "message.md").read_text()
    assert "【Alpha】" in first and "· 無差異，條款期間至 2026-12-31" in first and "找不到官網來源" not in first
    env.hermes_stdout = out("the programme ends on 31 December 2026")  # the model words it differently next week
    monkeypatch.setattr(terms, "_utcnow", lambda: datetime(2026, 11, 10, 1, 0, 0, tzinfo=timezone.utc))
    assert env.run("--cause", "monthly", "--send") == 0
    second = (env.archives[-1] / "message.md").read_text()
    assert second == "📋 回饋條款查核 2026-11-03：3 張卡無變動（已忽略 1 項）"


def test_stale_dismissal_is_reported_and_hides_nothing(tmp_path, monkeypatch):
    env = _denv(tmp_path, monkeypatch,
                '\n[[terms.cards."101".dismiss]]\nfield = "ends_on"\nrule = 162\nuntil = "2026-11-02"\n'
                '\n[[terms.cards."101".dismiss]]\nfield = "rate"\nrule = 999\n')
    assert env.run("--cause", "monthly") == 0
    (arch,) = env.archives
    message = (arch / "message.md").read_text()
    assert "· R162：結束日 官網 2027-03-31" in message and "已忽略" not in message
    assert "忽略清單失效：Alpha 規則 162 結束日（到期）、Alpha 規則 999 回饋率（規則已不在）" in message.split("\n")
    stale = json.loads((arch / "dismissed.json").read_text())["101"]["stale"]
    assert [(s["field"], s["rule"], s["reason"]) for s in stale] == [("ends_on", 162, "expired"), ("rate", 999, "rule")]
    assert stale[0]["until"] == "2026-11-02" and stale[1]["until"] is None and stale[0]["rates"] is None


def test_year_end_run_does_not_judge_cards_outside_the_window(tmp_path, monkeypatch):
    extra = '\n[[terms.cards."301".dismiss]]\nfield = "rate"\nrule = 999\n'  # 301 has year_end_window = false
    two = _stdout({"account_id": 101, "status": "unchanged"}, {"account_id": 102, "status": "signal", "notes": "n"})
    env = _denv(tmp_path, monkeypatch, extra, stdout=two)
    assert env.run("--cause", "year-end") == 0
    assert "忽略清單失效" not in (env.archives[0] / "message.md").read_text()
    env.hermes_stdout = FULL_STDOUT
    monkeypatch.setattr(terms, "_utcnow", lambda: datetime(2026, 11, 10, 1, 0, 0, tzinfo=timezone.utc))
    assert env.run("--cause", "monthly") == 0
    assert "忽略清單失效：Gamma 規則 999 回饋率（規則已不在）" in (env.archives[-1] / "message.md").read_text()


def _rates_env(tmp_path, monkeypatch, rates: str) -> TermsEnv:
    return _denv(tmp_path, monkeypatch, '\n[[terms.cards."301".dismiss]]\nfield = "next_terms"\n'
                                        f'value = "2027-03-31"\nrates = {rates}\n')


def test_announced_block_comes_back_when_its_rates_moved(tmp_path, monkeypatch):
    """FULL_STDOUT announces 1.5% cap 3000 for 301; the entry was written for 2% cap 3000."""
    env = _rates_env(tmp_path, monkeypatch, '["2/3000"]')
    assert env.run("--cause", "monthly") == 0
    message = (env.archives[0] / "message.md").read_text()
    assert "· 下期條款已公告 2027-01-01～2027-03-31：" in message and "已忽略" not in message


def test_announced_block_with_the_pinned_rates_is_hidden(tmp_path, monkeypatch):
    env = _rates_env(tmp_path, monkeypatch, '["1.5/3000.00"]')
    assert env.run("--cause", "monthly") == 0
    assert "下期條款已公告" not in (env.archives[0] / "message.md").read_text()


def _legacy_digest(day: str, source: str, quote: str) -> str:
    """What main saved for an unchanged precise card before #287: the period end as date, source and quote."""
    obj = {"status": "unchanged",
           "current_period_end": {"date": day, "source": source, "quote": terms_parse._q(quote)},
           "findings": [], "unverified": [], "next_terms": None, "next_terms_unverified_url": None}
    return hashlib.sha256(terms_parse._canon(obj).encode("utf-8")).hexdigest()


def test_a_digest_saved_before_the_change_is_news_once(tmp_path, monkeypatch):
    """The state file of the release before this one: the card whose digest shape changed shows once, the cards
    whose digest did not change stay quiet (so the old file is read, not thrown away), then everything is quiet."""
    quote = "valid until the end of 2026"
    env = _denv(tmp_path, monkeypatch, "", stdout=_stdout(
        {"account_id": 101, "status": "unchanged",
         "current_period_end": {"date": "2026-12-31", "source": ON_BANK, "quote": quote}},
        {"account_id": 102, "status": "signal", "notes": "same as before"},
        {"account_id": 301, "status": "unchanged"}))
    assert env.run("--cause", "monthly", "--send") == 0
    state = json.loads(env.tstate.read_text())
    new = state["101"]["digest"]
    legacy = _legacy_digest("2026-12-31", ON_BANK, quote)
    assert legacy != new
    state["101"]["digest"] = legacy
    env.tstate.write_text(json.dumps(state))

    monkeypatch.setattr(terms, "_utcnow", lambda: datetime(2026, 11, 10, 1, 0, 0, tzinfo=timezone.utc))
    assert env.run("--cause", "monthly", "--send") == 0
    first = (env.archives[-1] / "message.md").read_text()
    assert "【Alpha】" in first and "· 無差異，條款期間至 2026-12-31" in first
    assert "【Beta】" not in first and "【Gamma】" not in first
    assert load_terms_state(env.tstate).cards[101].digest == new

    monkeypatch.setattr(terms, "_utcnow", lambda: datetime(2026, 11, 17, 1, 0, 0, tzinfo=timezone.utc))
    assert env.run("--cause", "monthly", "--send") == 0
    assert (env.archives[-1] / "message.md").read_text() == "📋 回饋條款查核 2026-11-03：3 張卡無變動"


# --- #286: a file error is a failure notice, not a traceback ---

def _fail_open_of(monkeypatch, name: str, code: int = errno.ENOSPC):
    real = os.open

    def fake(path, flags, mode=0o777, **kw):
        if str(path).endswith(name):
            raise OSError(code, os.strerror(code), str(path))
        return real(path, flags, mode, **kw)

    monkeypatch.setattr(os, "open", fake)


@pytest.mark.parametrize("name", ["input.json", "result.json", "dismissed.json", "message.md"])
def test_archive_write_error_is_a_failure_notice(tenv, monkeypatch, capsys, name):
    _fail_open_of(monkeypatch, name)
    assert tenv.run("--cause", "monthly", "--send") == 1
    (arch,) = tenv.archives
    assert tenv.failure.read_text() == f"❌ 帳務通知失敗：terms 2026-11-03 TermsArchiveError archive {arch}"
    assert tenv.sent == [] and not tenv.tstate.exists()
    assert "No space" not in capsys.readouterr().err


def test_report_dir_that_cannot_be_created_is_a_failure_notice(tenv):
    tenv.reports.write_text("a file where the directory should be")
    assert tenv.run("--cause", "monthly", "--send") == 1
    assert tenv.failure.read_text() == f"❌ 帳務通知失敗：terms 2026-11-03 TermsArchiveError archive {tenv.reports}"
    assert tenv.hermes_calls == [] and tenv.sent == []


def test_state_file_error_after_the_send_is_a_failure_notice(tenv, monkeypatch, capsys):
    def boom(path, state):
        raise PermissionError(errno.EACCES, "Permission denied", "/secret/place/terms-state.json")

    monkeypatch.setattr(terms, "save_terms_state", boom)
    assert tenv.run("--cause", "monthly", "--send") == 1
    text = tenv.failure.read_text()
    assert text == "❌ 帳務通知失敗：terms 2026-11-03 PermissionError EACCES"
    assert "/secret" not in capsys.readouterr().err
