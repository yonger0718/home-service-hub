import hashlib
import json
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from worker import cathay, cli, config
from worker.api import ApiClient
from worker.drive import Drive
from worker.runner import FakeRunner, Result
from tests.worker.pdfgen import encrypt, make_pdf

FOLDER = "投資/國泰證券/日對帳單"
FIXTURES = Path(__file__).parent / "fixtures" / "cathay"
PASSWORD = "pw-fictional-1"


def fx(name: str, ymd: tuple[int, int, int] = (2026, 3, 9)) -> str:
    """A fixture statement moved to another 成交日期 (header, intro and date line)."""
    y, m, d = ymd
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return (text.replace("115年 3月 9日", f"{y - 1911}年 {m}月 {d}日").replace("115年3月9日", f"{y - 1911}年{m}月{d}日")
            .replace("2026/03/09", f"{y}/{m:02d}/{d:02d}"))


class Portfolio:
    """The portfolio API: `reply(payload, params)` answers statement posts; everything is recorded."""

    def __init__(self, reply=None):
        self.reply = reply or (lambda payload, params: self.ok(payload))
        self.posts: list[tuple[dict, dict]] = []
        self.runs: list[dict] = []
        self.acks: list[str] = []
        self.ack_status = 200

    @staticmethod
    def ok(payload, outcome="imported", **extra):
        body = {"outcome": outcome, "trade_date": payload["trade_date"], "reason": None, "counts": {},
                "holdings_diff": [], "errors": []}
        return httpx.Response(200, json={**body, **extra})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/runs"):
            self.runs.append(json.loads(request.content))
            return httpx.Response(204)
        if path.endswith("/ack"):
            self.acks.append(path)
            return httpx.Response(self.ack_status, json={"trade_date": path.split("/")[-2], "ack_status": "acked"})
        assert path == "/api/portfolio/imports/cathay-statement"
        payload, params = json.loads(request.content), dict(request.url.params)
        self.posts.append((payload, params))
        out = self.reply(payload, params)
        if isinstance(out, Exception):
            raise out
        return out


class HomeHub:
    """The HomeHub API: records `(method, path, json)` of every call; `.fail = 503` answers that status."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.fail: int | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, json.loads(request.content)))
        if self.fail:
            return httpx.Response(self.fail, json={"detail": "unavailable"})
        return httpx.Response(200, json={"status": "completed", "booked": 0, "skipped": 0, "housekeeping": {},
                                         "flagged": 0})


class World:
    def __init__(self, tmp_path, monkeypatch, cutover="2026-03-01", password=PASSWORD):
        pw = tmp_path / "passwords.env"
        pw.write_text(f"{FOLDER}={password}\n", encoding="utf-8")
        self.cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path / "state"), "STATEMENT_PASSWORD_FILE": str(pw),
                                "STATEMENT_CATHAY_CUTOVER": cutover, "STATEMENT_PORTFOLIO_API_URL": "http://portfolio.test"})
        self.files: list[dict] = []
        self.texts: dict[bytes, str] = {}
        self.extract_calls: list[str | None] = []
        self.list_rc = 0
        self.fail_ids: set[str] = set()
        self.portfolio = Portfolio()
        self.homehub = HomeHub()
        self.runner = FakeRunner({"rclone": self._rclone})
        monkeypatch.setattr(cathay, "extract_text", self._extract)

    def add(self, fid: str, name: str, text: str, data: bytes | None = None) -> bytes:
        data = data or encrypt(make_pdf([[f"doc {fid} {len(self.files)}"]]), PASSWORD)
        self.texts[data] = text
        self.files.append({"id": fid, "name": name, "data": data})
        return data

    def _extract(self, data, password):
        self.extract_calls.append(password)
        return self.texts[data]

    def _rclone(self, args, stdin):
        if args[1] == "lsjson":
            assert args[-1] == f"gdrive:財務對帳單/{FOLDER}"
            rows = [{"Path": f["name"], "Name": f["name"], "Size": len(f["data"]), "ModTime": "", "ID": f["id"],
                     "Hashes": {"md5": hashlib.md5(f["data"]).hexdigest()}} for f in self.files]
            return Result(self.list_rc, json.dumps(rows).encode(), b"")
        assert args[1:3] == ["backend", "copyid"]
        fid, dest = args[4], Path(args[5])
        if fid in self.fail_ids:
            return Result(1, b"", b"")
        dest.write_bytes(next(f["data"] for f in self.files if f["id"] == fid))
        return Result(0, b"", b"")

    def downloads(self) -> list[str]:
        return [c[4] for c in self.runner.calls if c[1] == "backend"]

    def go(self, **kw):
        homehub = ApiClient(httpx.Client(base_url="http://homehub.test", transport=httpx.MockTransport(self.homehub)),
                            "worker-token")
        with httpx.Client(base_url="http://portfolio.test", transport=httpx.MockTransport(self.portfolio)) as ph:
            return cathay.run(self.cfg, portfolio_http=ph, drive=Drive(self.cfg, self.runner), homehub=homehub, **kw)

    @property
    def state(self) -> dict:
        path = self.cfg.state_dir / "cathay-state.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    @property
    def latch(self) -> Path:
        return self.cfg.state_dir / "cathay-disabled.json"


@pytest.fixture
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_plain_commit_run_calls_homehub_after_posting(w):
    w.add("id-1", "2026-03-09_對帳單.pdf", fx("single.txt"))
    summary = w.go()
    assert w.homehub.calls == [("POST", "/reconciliation/settlements/run", {"source": "portfolio:cathay"})]
    assert summary["settlement_run"] == "completed" and summary["codes"] == []


def test_dry_run_latched_and_file_runs_do_not_call_homehub(w):
    w.add("id-1", "2026-03-09_對帳單.pdf", fx("single.txt"))
    w.go(dry_run=True)
    w.go(file_id="id-1")
    w.latch.write_text("{}", encoding="utf-8")
    with pytest.raises(cathay.CathayDisabled):
        w.go()
    assert w.homehub.calls == []


def test_homehub_failure_never_changes_the_run_result(w):
    w.add("id-1", "2026-03-09_對帳單.pdf", fx("single.txt"))
    w.homehub.fail = 503
    summary = w.go()
    assert summary["settlement_run"] == "failed:ApiError" and summary["codes"] == [] and summary["counts"] == {"imported": 1}
    assert "worker-token" not in json.dumps(summary)


def test_imports_and_records_state(w):
    data = w.add("id-1", "2026-03-09_對帳單.pdf", fx("single.txt"))
    summary = w.go()
    payload, params = w.portfolio.posts[0]
    assert params == {"dry_run": "false"} and payload["trade_date"] == "2026-03-09"
    assert payload["source"] == {"drive_file_id": "id-1", "sha256": sha(data), "file_name": "2026-03-09_對帳單.pdf"}
    entry = w.state[f"id-1:{sha(data)}"]
    assert entry["outcome"] == "imported" and entry["trade_date"] == "2026-03-09"
    assert entry["md5"] == hashlib.md5(data).hexdigest() and entry["at"]
    assert w.extract_calls == [PASSWORD] and summary["codes"] == [] and summary["counts"] == {"imported": 1}
    assert oct((w.cfg.state_dir / "cathay-state.json").stat().st_mode)[-3:] == "600"
    w.go()  # a terminal sha is skipped without a download
    assert w.downloads() == ["id-1"] and len(w.portfolio.posts) == 1


def test_held_reposted_next_run(w):
    w.add("id-1", "a.pdf", fx("unsupported.txt"))
    w.portfolio.reply = lambda payload, params: Portfolio.ok(payload, "held", reason="unsupported_category")
    w.go()
    w.go()
    assert len(w.portfolio.posts) == 2 and w.state == {}
    assert w.portfolio.posts[0][0]["lines"] == [] and "unsupported_category" in w.portfolio.posts[0][0]["warnings"]


def test_mismatch_not_reposted_without_retry(w):
    w.add("id-1", "a.pdf", fx("single.txt"))
    w.portfolio.reply = lambda payload, params: Portfolio.ok(payload, "mismatch")
    w.go()
    w.go()
    assert len(w.portfolio.posts) == 1 and next(iter(w.state.values()))["outcome"] == "mismatch"
    assert w.downloads() == ["id-1"]


def test_retry_reposts_mismatch(w):
    w.add("id-1", "a.pdf", fx("single.txt"))
    w.portfolio.reply = lambda payload, params: Portfolio.ok(payload, "mismatch")
    w.go()
    w.go(retry_date=date(2026, 3, 10))  # another date: still skipped
    assert len(w.portfolio.posts) == 1
    w.portfolio.reply = lambda payload, params: Portfolio.ok(payload, "verified")
    w.go(retry_date=date(2026, 3, 9))
    assert len(w.portfolio.posts) == 2 and next(iter(w.state.values()))["outcome"] == "verified"


def test_dry_run_never_writes_state(w):
    w.add("id-1", "a.pdf", fx("single.txt"))
    summary = w.go(dry_run=True)
    assert w.portfolio.posts[0][1] == {"dry_run": "true"} and w.state == {} and summary["dry_run"] is True
    assert w.portfolio.runs == []  # a dry run files no run report


def test_commit_after_dry_run(w):
    w.add("id-1", "a.pdf", fx("single.txt"))
    w.go(dry_run=True)
    w.go()
    assert [p[1]["dry_run"] for p in w.portfolio.posts] == ["true", "false"] and len(w.state) == 1


def test_422_latches(w):
    w.add("id-1", "a.pdf", fx("single.txt"))
    w.portfolio.reply = lambda payload, params: httpx.Response(422, json={"detail": "x"})
    summary = w.go()
    assert summary["codes"] == ["latched", "portfolio:422"] and w.state == {}
    assert json.loads(w.latch.read_text(encoding="utf-8"))["reason"] == "portfolio:422"
    with pytest.raises(cathay.CathayDisabled):
        w.go()


def test_statement_error_latches(w):
    w.add("id-1", "a.pdf", "not a statement")
    summary = w.go()
    assert summary["codes"] == ["latched", "header"] and w.latch.exists() and w.portfolio.posts == []


def test_real_extract_text_runs_up_to_the_parser(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    monkeypatch.undo()  # keep the real extract_text
    w.add("id-1", "a.pdf", "unused")
    summary = w.go()
    assert summary["codes"] == ["latched", "header"] and w.portfolio.posts == []


def test_unlatch_after_clean_run(w):
    w.add("id-1", "a.pdf", fx("single.txt"))
    w.portfolio.reply = lambda payload, params: httpx.Response(422)
    w.go()
    assert w.latch.exists()
    w.portfolio.reply = lambda payload, params: Portfolio.ok(payload)
    summary = w.go(unlatch=True)
    assert summary["codes"] == [] and not w.latch.exists()


def test_unlatch_keeps_latch_when_the_run_still_fails(w):
    w.add("id-1", "a.pdf", fx("single.txt"))
    w.portfolio.reply = lambda payload, params: httpx.Response(422)
    w.go()
    summary = w.go(unlatch=True)
    assert summary["codes"] == ["latched", "portfolio:422"] and w.latch.exists()


def test_dry_run_keeps_latch(w):
    w.add("id-1", "a.pdf", fx("single.txt"))
    w.portfolio.reply = lambda payload, params: httpx.Response(422)
    w.go()
    w.portfolio.reply = lambda payload, params: Portfolio.ok(payload)
    with pytest.raises(cathay.CathayDisabled):
        w.go(dry_run=True)
    with pytest.raises(cathay.CathayDisabled):
        w.go(validate_history=True)
    summary = w.go(dry_run=True, unlatch=True)
    assert summary["codes"] == [] and w.latch.exists()
    w.go(unlatch=True)
    assert not w.latch.exists()


def test_name_after_cutover_trade_before_is_not_committed(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, cutover="2026-10-16")
    data = w.add("id-1", "2026-10-20_對帳單.pdf", fx("single.txt", (2026, 10, 15)))
    w.go()
    assert w.portfolio.posts == [] and w.state[f"id-1:{sha(data)}"]["outcome"] == "pre_cutover"
    assert w.state[f"id-1:{sha(data)}"]["trade_date"] == "2026-10-15"
    w.go(dry_run=True)  # a dry run does not post before the cutover either
    assert w.portfolio.posts == []
    summary = w.go(validate_history=True)
    assert len(w.portfolio.posts) == 1 and w.portfolio.posts[0][1] == {"dry_run": "true"}
    assert summary["counts"] == {"imported": 1}
    assert w.state[f"id-1:{sha(data)}"]["outcome"] == "pre_cutover"


def test_dry_run_does_not_write_pre_cutover_state(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, cutover="2026-10-16")
    w.add("id-1", "a.pdf", fx("single.txt", (2026, 10, 15)))
    w.go(dry_run=True)
    assert w.state == {}


def test_name_before_cutover_trade_after_is_committed(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, cutover="2026-10-16")
    w.add("id-1", "2026-10-14_對帳單.pdf", fx("single.txt", (2026, 10, 16)))
    w.go()
    assert len(w.portfolio.posts) == 1 and w.portfolio.posts[0][1] == {"dry_run": "false"}
    assert next(iter(w.state.values()))["outcome"] == "imported"


def test_range_uses_trade_date(w):
    w.add("id-1", "2026-03-20_a.pdf", fx("single.txt", (2026, 3, 9)))
    w.add("id-2", "2026-03-01_b.pdf", fx("pair.txt", (2026, 3, 12)))
    w.go(since=date(2026, 3, 10), until=date(2026, 3, 31))
    assert [p[0]["trade_date"] for p in w.portfolio.posts] == ["2026-03-12"] and len(w.state) == 1
    w.go(until=date(2026, 3, 9))
    assert [p[0]["trade_date"] for p in w.portfolio.posts] == ["2026-03-12", "2026-03-09"]


def test_file_and_range_filters(w):
    w.add("id-1", "a.pdf", fx("single.txt", (2026, 3, 9)))
    w.add("id-2", "b.pdf", fx("pair.txt", (2026, 3, 10)))
    w.add("id-3", "c.pdf", fx("two_pages.txt", (2026, 3, 11)))
    w.go(file_id="id-2")
    assert w.downloads() == ["id-2"] and [p[0]["trade_date"] for p in w.portfolio.posts] == ["2026-03-10"]
    summary = w.go(file_id="id-missing")
    assert summary["codes"] == ["failed", "file_not_found"]


def test_validate_history_report(w):
    w.add("id-1", "a.pdf", fx("single.txt", (2026, 3, 9)))
    w.add("id-2", "b.pdf", fx("pair.txt", (2026, 3, 10)))
    w.add("id-3", "c.pdf", fx("two_pages.txt", (2026, 3, 11)))
    outcomes = {"2026-03-09": ("verified", None), "2026-03-10": ("mismatch", None), "2026-03-11": ("held", "unresolved_symbol:範例電子")}
    w.portfolio.reply = lambda payload, params: Portfolio.ok(payload, outcomes[payload["trade_date"]][0],
                                                             reason=outcomes[payload["trade_date"]][1])
    before = {"seed:abc": {"outcome": "imported", "trade_date": "2026-03-01", "md5": "x", "at": "t"}}
    (w.cfg.state_dir).mkdir(parents=True, exist_ok=True)
    state_path = w.cfg.state_dir / "cathay-state.json"
    state_path.write_text(json.dumps(before), encoding="utf-8")
    summary = w.go(validate_history=True)
    report_path = w.cfg.state_dir / "cathay-history-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report == {"counts": {"verified": 1, "mismatch": 1, "held": 1}, "mismatch_dates": ["2026-03-10"],
                      "held": [{"trade_date": "2026-03-11", "reason": "unresolved_symbol"}], "errors": []}
    assert oct(report_path.stat().st_mode)[-3:] == "600" and json.loads(state_path.read_text(encoding="utf-8")) == before
    assert all(p[1] == {"dry_run": "true"} for p in w.portfolio.posts) and w.portfolio.runs == []
    assert summary["counts"] == {"verified": 1, "mismatch": 1, "held": 1} and "mismatch_dates" not in summary
    assert "範例電子" not in report_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("reason, code", [("unsupported_category", "unsupported_category"),
                                          ("unresolved_symbol:範例電子", "unresolved_symbol"),
                                          ("範例電子", ""), (None, ""), (7, "")])
def test_history_reason_keeps_the_code_only(reason, code):
    assert cathay._reason_code(reason) == code


def test_validate_history_ignores_state_and_collects_errors(w):
    data = w.add("id-1", "a.pdf", fx("single.txt"))
    w.add("id-2", "b.pdf", "garbage")
    w.cfg.state_dir.mkdir(parents=True, exist_ok=True)
    (w.cfg.state_dir / "cathay-state.json").write_text(json.dumps(
        {f"id-1:{sha(data)}": {"outcome": "imported", "trade_date": "2026-03-09", "md5": hashlib.md5(data).hexdigest(), "at": "t"}}))
    summary = w.go(validate_history=True)
    report = json.loads((w.cfg.state_dir / "cathay-history-report.json").read_text(encoding="utf-8"))
    assert len(w.portfolio.posts) == 1 and report["errors"] == [{"file_name": "b.pdf", "code": "header"}]
    assert not w.latch.exists() and summary["codes"] == ["failed", "header"]


def test_all_passwords_fail_latches(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, password="some-other-password")
    w.add("id-1", "a.pdf", fx("single.txt"))
    summary = w.go()
    assert summary["codes"] == ["latched", "decrypt"] and w.latch.exists() and w.portfolio.posts == []


def test_no_candidates_for_the_folder_latches(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.cfg.password_file.write_text("其他/資料夾=x\n", encoding="utf-8")
    w.add("id-1", "a.pdf", fx("single.txt"))
    assert w.go()["codes"] == ["latched", "decrypt"]


def test_second_candidate_is_used(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.cfg.password_file.write_text(f"{FOLDER}=wrong-1\n{FOLDER}.alt={PASSWORD}\n", encoding="utf-8")
    w.add("id-1", "a.pdf", fx("single.txt"))
    w.go()
    assert w.extract_calls == [PASSWORD]


@pytest.mark.parametrize("answer", [
    httpx.Response(500, json={"outcome": "held", "errors": ["x"]}), httpx.Response(502), httpx.Response(408),
    httpx.Response(429), httpx.ReadTimeout("slow"), httpx.Response(200, content=b"not json"),
    httpx.Response(200, json={"outcome": "weird", "trade_date": "2026-03-09", "errors": []}),
    httpx.Response(200, json={"outcome": "imported", "trade_date": "2026-03-10", "errors": []}),
    httpx.Response(200, json={"outcome": "imported", "trade_date": "2026-03-09", "errors": ["boom"]}),
])
def test_transient_statuses_no_latch(w, answer):
    w.add("id-1", "a.pdf", fx("single.txt"))
    w.portfolio.reply = lambda payload, params: answer
    summary = w.go()
    assert summary["codes"][0] == "failed" and not w.latch.exists() and w.state == {}
    assert w.portfolio.runs[-1]["result"] == "failed"


def test_run_report_posted_on_ok_latched_failed(w):
    w.add("id-1", "a.pdf", fx("single.txt"))
    w.add("id-2", "b.pdf", fx("pair.txt", (2026, 3, 10)))
    w.go()
    ok = w.portfolio.runs[-1]
    assert set(ok) == {"run_at", "result", "code", "files_seen", "posted"}
    assert (ok["result"], ok["code"], ok["files_seen"], ok["posted"]) == ("ok", None, 2, 2)
    assert datetime.fromisoformat(ok["run_at"]).tzinfo is not None
    w.portfolio.reply = lambda payload, params: httpx.Response(422)
    w.add("id-3", "c.pdf", fx("two_pages.txt", (2026, 3, 11)))
    w.go()
    assert w.portfolio.runs[-1] | {"run_at": ""} == {"run_at": "", "result": "latched", "code": "portfolio:422", "files_seen": 3, "posted": 1}
    with pytest.raises(cathay.CathayDisabled):  # a plain run refused while latched still reports
        w.go()
    assert w.portfolio.runs[-1] | {"run_at": ""} == {"run_at": "", "result": "latched", "code": "portfolio:422", "files_seen": 0, "posted": 0}
    w.latch.unlink()
    w.list_rc = 3
    w.go()
    assert w.portfolio.runs[-1]["result"] == "failed" and w.portfolio.runs[-1]["code"] == "drive_listing"
    assert len(w.portfolio.runs) == 4



def test_missing_password_file_reports(w):
    w.add("id-1", "a.pdf", fx("single.txt"))
    w.cfg.password_file.unlink()
    summary = w.go()
    assert summary["codes"] == ["failed", "password_file"] and w.portfolio.posts == [] and not w.latch.exists()
    assert w.portfolio.runs[-1] | {"run_at": ""} == {"run_at": "", "result": "failed", "code": "password_file", "files_seen": 0, "posted": 0}
    w.go(dry_run=True)
    assert len(w.portfolio.runs) == 1  # an owner run reports nothing
    w.latch.write_text('{"reason": "decrypt"}')
    with pytest.raises(cathay.CathayDisabled):  # the latch still wins
        w.go()
    assert w.portfolio.runs[-1] | {"run_at": ""} == {"run_at": "", "result": "latched", "code": "decrypt", "files_seen": 0, "posted": 0}

def test_only_a_plain_daily_run_reports(w):
    w.add("id-1", "a.pdf", fx("single.txt"))
    w.go(file_id="id-1")
    w.go(since=date(2026, 1, 1))
    w.go(until=date(2026, 12, 31))
    w.go(retry_date=date(2026, 3, 9))
    w.go(unlatch=True)
    w.go(dry_run=True)
    w.go(validate_history=True)
    w.go(ack_date=date(2026, 3, 9))
    assert w.portfolio.runs == []
    w.latch.write_text('{"reason": "x"}')
    for kw in ({"dry_run": True}, {"file_id": "id-1"}, {"since": date(2026, 1, 1)}, {"retry_date": date(2026, 3, 9)}):
        n = len(w.portfolio.runs)
        with pytest.raises(cathay.CathayDisabled):
            w.go(**kw)
        assert len(w.portfolio.runs) == n


def test_nothing_is_written_to_inbox_and_staging_is_empty(w):
    w.add("id-1", "a.pdf", fx("single.txt"))
    w.add("id-2", "b.pdf", "garbage")
    w.cfg.staging_dir.mkdir(parents=True, exist_ok=True)
    stale = w.cfg.staging_dir / "cathay-1-1.pdf"
    stale.write_bytes(b"x")
    other = w.cfg.staging_dir / "id-9.tmp"
    other.write_bytes(b"x")
    for kw in ({}, {"dry_run": True}, {"validate_history": True}):
        w.go(**kw)
        w.latch.unlink(missing_ok=True)
        assert not w.cfg.inbox_dir.exists() and not (w.cfg.state_dir / "inbox").exists()
        assert list(w.cfg.staging_dir.glob("cathay-*")) == []
    assert other.exists()


def test_run_report_failure_does_not_break_the_run(w):
    w.add("id-1", "a.pdf", fx("single.txt"))
    inner = w.portfolio

    def wrapped(request):
        if request.url.path.endswith("/runs"):
            return httpx.Response(500)
        return inner(request)
    w.portfolio = wrapped
    assert w.go()["codes"] == [] and len(w.state) == 1


def test_same_sha_twice_in_one_run(w):
    data = w.add("id-1", "a.pdf", fx("single.txt"))
    w.add("id-2", "copy-of-a.pdf", fx("single.txt"), data=data)
    seen = set()

    def by_sha(payload, params):  # the portfolio answers by sha, as the real one does
        sha_ = payload["source"]["sha256"]
        outcome = "duplicate" if sha_ in seen else "imported"
        seen.add(sha_)
        return Portfolio.ok(payload, outcome)
    w.portfolio.reply = by_sha
    summary = w.go()
    assert len(w.portfolio.posts) == 2 and summary["counts"] == {"imported": 1, "duplicate": 1}
    assert {k.split(":")[0]: v["outcome"] for k, v in w.state.items()} == {"id-1": "imported", "id-2": "duplicate"}


def test_unexpected_exception_reports_failed_run(w, monkeypatch):
    w.add("id-1", "a.pdf", fx("single.txt"))

    def boom(*a, **k):
        raise OSError("disk 範例電子")
    monkeypatch.setattr(cathay.parser, "write_private_json", boom)
    summary = w.go()
    assert summary["codes"] == ["failed", "internal:OSError"] and not w.latch.exists()
    assert w.portfolio.runs[-1]["result"] == "failed" and w.portfolio.runs[-1]["code"] == "internal:OSError"


def test_unlatch_needs_a_plain_run(w):
    w.add("id-1", "a.pdf", fx("single.txt"))
    w.portfolio.reply = lambda payload, params: httpx.Response(422)
    w.go()
    w.portfolio.reply = lambda payload, params: Portfolio.ok(payload)
    for kw in ({"file_id": "id-1"}, {"since": date(2026, 1, 1)}, {"until": date(2026, 12, 31)}, {"retry_date": date(2026, 3, 9)}):
        assert w.go(unlatch=True, **kw)["codes"] == [] and w.latch.exists()
    w.go(unlatch=True)
    assert not w.latch.exists()


def test_ack_works_while_latched_and_rejects_other_flags(w, tmp_path, monkeypatch, capsys):
    w.add("id-1", "a.pdf", fx("single.txt"))
    w.portfolio.reply = lambda payload, params: httpx.Response(422)
    w.go()
    assert w.go(ack_date=date(2026, 3, 9))["ack"]["trade_date"] == "2026-03-09" and w.latch.exists()
    monkeypatch.setattr(config, "load", lambda env=None: w.cfg)
    monkeypatch.setattr(cathay, "run", lambda c, **kw: pytest.fail("must not run"))
    assert cli.main(["cathay", "--ack", "2026-03-09", "--dry-run"]) == 2
    assert "--ack" in capsys.readouterr().err


def test_retry_that_comes_back_held_drops_the_old_entry(w):
    w.add("id-1", "a.pdf", fx("single.txt"))
    w.portfolio.reply = lambda payload, params: Portfolio.ok(payload, "mismatch")
    w.go()
    assert len(w.state) == 1
    w.portfolio.reply = lambda payload, params: Portfolio.ok(payload, "held", reason="symbol_unmapped")
    w.go(retry_date=date(2026, 3, 9))
    assert w.state == {}
    w.go()
    assert len(w.portfolio.posts) == 3  # held is posted again on the daily run


def test_validate_history_prints_counts_only(tmp_path, monkeypatch, capsys):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path / "s"), "STATEMENT_PASSWORD_FILE": str(tmp_path / "pw.env")})
    (tmp_path / "pw.env").write_text("x=y\n")
    monkeypatch.setattr(config, "load", lambda env=None: cfg)
    monkeypatch.setattr(cathay, "run", lambda c, **kw: {"counts": {"verified": 1}, "codes": [], "files_seen": 3, "posted": 1})
    assert cli.main(["cathay", "--validate-history"]) == 0
    assert json.loads(capsys.readouterr().out) == {"counts": {"verified": 1}}


def test_drive_failure_reports_failed_run(w):
    w.add("id-1", "a.pdf", fx("single.txt"))
    w.add("id-2", "b.pdf", fx("pair.txt", (2026, 3, 10)))
    w.fail_ids = {"id-1"}
    summary = w.go()
    assert summary["codes"] == ["failed", "drive:DownloadError"] and not w.latch.exists()
    assert [p[0]["trade_date"] for p in w.portfolio.posts] == ["2026-03-10"]  # the other file still ran
    assert w.portfolio.runs[-1]["result"] == "failed" and w.portfolio.runs[-1]["code"] == "drive:DownloadError"


def test_ack_posts_and_exits(w):
    w.add("id-1", "a.pdf", fx("single.txt"))
    summary = w.go(ack_date=date(2026, 3, 9))
    assert w.portfolio.acks == ["/api/portfolio/imports/cathay-statement/days/2026-03-09/ack"]
    assert summary["ack"] == {"trade_date": "2026-03-09", "ack_status": "acked"} and w.runner.calls == []
    assert w.portfolio.posts == [] and w.portfolio.runs == []
    w.portfolio.ack_status = 404
    with pytest.raises(cathay.NothingPending):
        w.go(ack_date=date(2026, 3, 9))


def test_already_running(w):
    import fcntl
    w.cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with open(w.cfg.state_dir / ".cathay.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        with pytest.raises(cathay.AlreadyRunning):
            w.go()


def test_no_statement_text_in_summary_or_logs(w, caplog):
    w.add("id-1", "a.pdf", fx("single.txt"))
    caplog.set_level("DEBUG")
    out = json.dumps(w.go(), ensure_ascii=False) + caplog.text
    assert "範例" not in out and "王小明" not in out


def test_cli_exit_codes(tmp_path, monkeypatch, capsys):
    token = tmp_path / "token"
    token.write_text("worker-token\n", encoding="utf-8")
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path / "s"), "STATEMENT_PASSWORD_FILE": str(tmp_path / "absent.env"),
                       "STATEMENT_API_TOKEN_FILE": str(token)})
    monkeypatch.setattr(config, "load", lambda env=None: cfg)
    seen = {}

    def fake(kind):
        def run(c, **kw):
            seen.update(kw)
            if kind == "running":
                raise cathay.AlreadyRunning(".cathay.lock")
            if kind == "disabled":
                raise cathay.CathayDisabled("x")
            if kind == "pending":
                raise cathay.NothingPending("2026-03-09")
            if kind == "boom":
                raise RuntimeError("boom 範例電子")
            return {"counts": {}, "codes": ["failed", "x"] if kind == "failed" else []}
        return run
    # a missing password file is not refused by the CLI: the job reports it (test_missing_password_file_reports)
    for kind, code in [("ok", 0), ("failed", 1), ("running", 3), ("disabled", 1), ("pending", 1), ("boom", 1)]:
        monkeypatch.setattr(cathay, "run", fake(kind))
        assert cli.main(["cathay"]) == code, kind
    err = capsys.readouterr().err
    assert "範例電子" not in err and "RuntimeError" in err and "nothing pending" in err
    monkeypatch.setattr(cathay, "run", fake("ok"))
    argv = ["cathay", "--dry-run", "--file", "F", "--since", "2026-03-01", "--until", "2026-03-31", "--retry", "2026-03-09",
            "--unlatch", "--validate-history"]
    assert cli.main(argv) == 0
    assert (seen["dry_run"], seen["file_id"], seen["since"], seen["until"], seen["retry_date"], seen["ack_date"],
            seen["unlatch"], seen["validate_history"]) == (True, "F", date(2026, 3, 1), date(2026, 3, 31),
                                                           date(2026, 3, 9), None, True, True)
    assert seen["homehub"] is None  # a flagged run builds no HomeHub client
    assert cli.main(["cathay", "--ack", "2026-03-10"]) == 0 and seen["ack_date"] == date(2026, 3, 10)
    assert cli.main(["cathay"]) == 0 and seen["homehub"] is not None
