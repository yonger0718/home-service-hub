"""`python -m worker sinopac-futures` (spec 2026-11-25-sinopac-futures-statement §7): fake Drive, fake portfolio."""
import hashlib
import json
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from tests.worker import futures_fixtures as fx
from tests.worker.futures_fixtures import Word
from tests.worker.pdfgen import encrypt, make_pdf
from tests.worker.test_sinopac_futures_statement import DAILY_IDENTITIES, DAILY_LAYOUT, MONTHLY_LAYOUT
from worker import cli, config, sinopac_futures
from worker.drive import Drive
from worker.runner import FakeRunner, Result

DAILY = "投資/永豐期貨/日對帳單"
MONTHLY = "投資/永豐期貨/月對帳單"
PASSWORD = "pw-fictional-1"
ENDPOINT = "/api/portfolio/imports/sinopac-futures-statement"


def day_rows(day: int = 9, rows=None, **kw):
    """The default daily fixture moved to 2026-03-<day> (every 115/03/09 word)."""
    rows = rows if rows is not None else fx.daily_rows(**kw)
    roc = f"115/03/{day:02d}"
    return [(page, tuple(Word(roc, w.x0, w.x1) if w.text == fx.DATE else w for w in words)) for page, words in rows]


def case(table, name):
    return next(c[1:] for c in table if c[0] == name)


def key_of(payload: dict) -> str:
    return payload["statement_date"] if payload["kind"] == "daily" else payload["month"]


class Portfolio:
    """The portfolio API: `reply(payload, params)` answers imports, `validate_reply(payloads)` the batch."""

    def __init__(self):
        self.reply = lambda payload, params: self.ok(payload)
        self.validate_reply = lambda payloads: httpx.Response(200, json=self.answer(payloads))
        self.posts: list[tuple[dict, dict]] = []
        self.validates: list[dict] = []
        self.runs: list[dict] = []
        self.acks: list[tuple[str, dict]] = []
        self.ack_status = 204
        self.timeouts: dict[str, list[float]] = {}

    @staticmethod
    def ok(payload, outcome="imported", **extra):
        body = {"outcome": outcome, "key": key_of(payload), "findings": [], "errors": []}
        return httpx.Response(200, json={**body, **extra})

    @staticmethod
    def answer(payloads, outcomes=None, findings=None):
        return {"items": [{"file_name": p["source"]["file_name"], "key": key_of(p),
                           "outcome": (outcomes or {}).get(key_of(p), "imported")} for p in payloads],
                "findings": findings if findings is not None else {key_of(p): [] for p in payloads}}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.timeouts.setdefault(path.rsplit("/", 1)[-1], []).append(request.extensions["timeout"]["read"])
        if path == f"{ENDPOINT}/runs":
            self.runs.append(json.loads(request.content))
            return httpx.Response(204)
        if path.endswith("/ack"):
            self.acks.append((path, dict(request.url.params)))
            return httpx.Response(self.ack_status)
        if path == f"{ENDPOINT}/validate":
            body = json.loads(request.content)
            self.validates.append(body)
            return self.validate_reply(body["payloads"])
        assert path == ENDPOINT
        payload, params = json.loads(request.content), dict(request.url.params)
        self.posts.append((payload, params))
        out = self.reply(payload, params)
        if isinstance(out, Exception):
            raise out
        return out

    @property
    def keys(self) -> list[str]:
        return [key_of(p) for p, _ in self.posts]


class World:
    def __init__(self, tmp_path, monkeypatch, password=PASSWORD, env=None):
        pw = tmp_path / "passwords.env"
        pw.write_text(f"{DAILY}={password}\n{MONTHLY}={password}\n", encoding="utf-8")
        self.cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path / "state"), "STATEMENT_PASSWORD_FILE": str(pw),
                                "STATEMENT_PORTFOLIO_API_URL": "http://portfolio.test", **(env or {})})
        self.files: dict[str, list[dict]] = {DAILY: [], MONTHLY: []}
        self.lines: dict[bytes, list] = {}
        self.extract_calls: list[str | None] = []
        self.list_rc = 0
        self.fail_ids: set[str] = set()
        self.portfolio = Portfolio()
        self.runner = FakeRunner({"rclone": self._rclone})
        monkeypatch.setattr(sinopac_futures, "extract_lines", self._extract)

    def add(self, folder: str, fid: str, name: str, rows, data: bytes | None = None) -> bytes:
        count = sum(len(v) for v in self.files.values())
        data = data or encrypt(make_pdf([[f"doc {fid} {count}"]]), PASSWORD)
        self.lines[data] = fx.lines(rows)
        self.files[folder].append({"id": fid, "name": name, "data": data})
        return data

    def daily(self, fid: str, day: int = 9, name: str | None = None, **kw) -> bytes:
        return self.add(DAILY, fid, name or f"2026-03-{day:02d}_日對帳單.pdf", day_rows(day, **kw))

    def monthly(self, fid: str, name: str = "2026-03_月對帳單.pdf", rows=None) -> bytes:
        return self.add(MONTHLY, fid, name, rows if rows is not None else fx.monthly_rows())

    def _extract(self, data, password, **kw):
        self.extract_calls.append(password)
        return self.lines[data]

    def _rclone(self, args, stdin):
        if args[1] == "lsjson":
            folder = args[-1].removeprefix("gdrive:財務對帳單/")
            rows = [{"Path": f["name"], "Name": f["name"], "Size": len(f["data"]), "ModTime": "", "ID": f["id"],
                     "Hashes": {"md5": hashlib.md5(f["data"]).hexdigest()}} for f in self.files[folder]]
            return Result(self.list_rc, json.dumps(rows).encode(), b"")
        assert args[1:3] == ["backend", "copyid"]
        fid, dest = args[4], Path(args[5])
        if fid in self.fail_ids:
            return Result(1, b"", b"")
        dest.write_bytes(next(f["data"] for fs in self.files.values() for f in fs if f["id"] == fid))
        return Result(0, b"", b"")

    def downloads(self) -> list[str]:
        return [c[4] for c in self.runner.calls if c[1] == "backend"]

    def go(self, **kw):
        with httpx.Client(base_url="http://portfolio.test", timeout=self.cfg.sinopac_futures_timeout_s,
                          transport=httpx.MockTransport(self.portfolio)) as ph:
            return sinopac_futures.run(self.cfg, portfolio_http=ph, drive=Drive(self.cfg, self.runner), **kw)

    @property
    def state(self) -> dict:
        path = self.cfg.state_dir / "sinopac-futures-state.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    @property
    def latch(self) -> Path:
        return self.cfg.state_dir / "sinopac-futures-disabled.json"

    @property
    def report(self) -> dict:
        return json.loads((self.cfg.state_dir / "sinopac-futures-history-report.json").read_text(encoding="utf-8"))


@pytest.fixture
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def outcomes(state: dict) -> dict[str, str]:
    return {k.split(":")[0]: v["outcome"] for k, v in state.items()}


# ---------------------------------------------------------------- config


def test_config_defaults_and_env():
    cfg = config.load({})
    assert (cfg.sinopac_futures_daily_folder, cfg.sinopac_futures_monthly_folder) == (DAILY, MONTHLY)
    assert (cfg.sinopac_futures_timeout_s, cfg.sinopac_futures_validate_timeout_s) == (60, 600)
    cfg = config.load({"STATEMENT_SINOPAC_FUTURES_DAILY_FOLDER": "/a/日/", "STATEMENT_SINOPAC_FUTURES_MONTHLY_FOLDER": "b/月",
                       "STATEMENT_SINOPAC_FUTURES_TIMEOUT_S": "45", "STATEMENT_SINOPAC_FUTURES_VALIDATE_TIMEOUT_S": "900"})
    assert (cfg.sinopac_futures_daily_folder, cfg.sinopac_futures_monthly_folder) == ("a/日", "b/月")
    assert (cfg.sinopac_futures_timeout_s, cfg.sinopac_futures_validate_timeout_s) == (45, 900)


# ---------------------------------------------------------------- statement rejected at the job boundary


MALFORMED = [
    ("daily", case(DAILY_LAYOUT, "section_missing")),
    ("daily", case(DAILY_LAYOUT, "foreign_currency")),
    ("daily", case(DAILY_LAYOUT, "two_dates")),
    ("monthly", case(MONTHLY_LAYOUT, "numeric_word_without_label")),
]


@pytest.mark.parametrize("kind, mutate, code", [(k, *c) for k, c in MALFORMED],
                         ids=["daily_section_missing", "daily_foreign_currency", "daily_two_dates", "monthly_column"])
def test_malformed_statement_rejected_at_job_boundary(w, kind, mutate, code):
    w.daily("id-ok", 10)
    rows = fx.daily_rows() if kind == "daily" else fx.monthly_rows()
    mutate(rows)
    if kind == "daily":
        w.add(DAILY, "id-bad", "2026-03-09_日對帳單.pdf", rows)
    else:
        w.monthly("id-bad", rows=rows)
    summary = w.go()
    assert w.portfolio.posts == [] and w.state == {}
    assert summary["codes"] == ["latched", code]
    assert json.loads(w.latch.read_text(encoding="utf-8"))["reason"] == code
    assert w.portfolio.runs[-1]["result"] == "latched" and w.portfolio.runs[-1]["code"] == code
    with pytest.raises(sinopac_futures.FuturesDisabled):
        w.go()


def test_parse_failure_latches_and_posts_nothing(w):
    w.daily("id-1", 9)
    name, kw = DAILY_IDENTITIES[0]
    w.add(DAILY, "id-2", "2026-03-10_日對帳單.pdf", day_rows(10, **kw))
    w.monthly("id-3")
    summary = w.go()
    assert summary["codes"] == ["latched", f"identity:{name}"] and w.latch.exists()
    assert w.portfolio.posts == [] and w.state == {}
    w.go(dry_run=True, unlatch=True)  # a dry run reports the same failure and leaves the latch
    assert w.latch.exists() and w.portfolio.keys == ["2026-03-09", "2026-03"]


def test_parser_crash_latches_with_a_class_name_only(w, monkeypatch):
    w.daily("id-1")

    def boom(lines):
        raise IndexError("範例期貨 151,166")
    monkeypatch.setattr(sinopac_futures, "parse_daily", boom)
    summary = w.go()
    assert summary["codes"] == ["latched", "parse:IndexError"] and w.portfolio.posts == []
    assert "範例" not in w.latch.read_text(encoding="utf-8")


# ---------------------------------------------------------------- order, state, repeats


def test_posts_daily_then_monthly_in_order(w):
    w.monthly("id-m", name="0-first-by-name.pdf")
    w.daily("id-11", 11, name="a.pdf")
    w.daily("id-10", 10, name="b.pdf")
    w.daily("id-09", 9, name="c.pdf")
    summary = w.go()
    assert w.portfolio.keys == ["2026-03-09", "2026-03-10", "2026-03-11", "2026-03"]
    assert all(params == {"dry_run": "false"} for _, params in w.portfolio.posts)
    payload = w.portfolio.posts[0][0]
    assert payload["source"]["drive_file_id"] == "id-09" and payload["source"]["file_name"] == "c.pdf"
    assert payload["source"]["sha256"] == sha(w.files[DAILY][2]["data"])
    assert summary["codes"] == [] and summary["counts"] == {"imported": 4}
    assert (summary["files_seen"], summary["posted"], summary["skipped"]) == (4, 4, 0)
    assert w.extract_calls == [PASSWORD] * 4
    assert oct((w.cfg.state_dir / "sinopac-futures-state.json").stat().st_mode)[-3:] == "600"
    assert w.portfolio.runs[-1] | {"run_at": ""} == {"run_at": "", "result": "ok", "code": None, "files_seen": 4,
                                                     "posted": 4}
    assert datetime.fromisoformat(w.portfolio.runs[-1]["run_at"]).tzinfo is not None


def test_state_skips_known_sha(w):
    data = w.daily("id-1", 9)
    m = w.monthly("id-m")
    w.go()
    entry = w.state[f"id-1:{sha(data)}"]
    assert (entry["outcome"], entry["kind"], entry["key"]) == ("imported", "daily", "2026-03-09")
    assert entry["md5"] == hashlib.md5(data).hexdigest() and entry["at"]
    assert w.state[f"id-m:{sha(m)}"]["key"] == "2026-03"
    w.daily("id-2", 10)
    summary = w.go()  # the settled files are skipped without a download
    assert w.downloads() == ["id-1", "id-m", "id-2"] and w.portfolio.keys == ["2026-03-09", "2026-03", "2026-03-10"]
    assert (summary["files_seen"], summary["posted"], summary["skipped"]) == (3, 1, 2)


def test_conflict_is_terminal_and_retry_reposts(w):
    w.daily("id-1", 9)
    w.portfolio.reply = lambda payload, params: Portfolio.ok(payload, "conflict")
    w.go()
    w.go()
    assert len(w.portfolio.posts) == 1 and outcomes(w.state) == {"id-1": "conflict"}
    w.go(retry_date=date(2026, 3, 10))  # another date: still skipped
    assert len(w.portfolio.posts) == 1
    w.portfolio.reply = lambda payload, params: Portfolio.ok(payload, "duplicate")
    w.go(retry_date=date(2026, 3, 9))
    assert len(w.portfolio.posts) == 2 and outcomes(w.state) == {"id-1": "duplicate"}


def test_same_statement_twice_in_run(w):
    data = w.daily("id-1", 9, name="2026-03-09_日對帳單.pdf")
    w.add(DAILY, "id-2", "2026-03-09_日對帳單(1).pdf", day_rows(9), data=data)
    seen: set[str] = set()

    def by_sha(payload, params):  # the portfolio answers by sha, as the real one does
        digest = payload["source"]["sha256"]
        outcome = "duplicate" if digest in seen else "imported"
        seen.add(digest)
        return Portfolio.ok(payload, outcome)
    w.portfolio.reply = by_sha
    summary = w.go()
    assert len(w.portfolio.posts) == 2 and summary["counts"] == {"imported": 1, "duplicate": 1}
    assert summary["codes"] == [] and not w.latch.exists()
    assert outcomes(w.state) == {"id-1": "imported", "id-2": "duplicate"}


# ---------------------------------------------------------------- portfolio failures


@pytest.mark.parametrize("answer, code", [
    (httpx.Response(500, json={"outcome": "imported", "key": "2026-03-10", "findings": [], "errors": ["x"]}),
     "portfolio:500"),
    (httpx.Response(502), "portfolio:502"), (httpx.Response(503, json={"outcome": "duplicate"}), "portfolio:503"),
    (httpx.Response(408), "portfolio:408"), (httpx.Response(429), "portfolio:429"),
    (httpx.ReadTimeout("slow"), "portfolio:ReadTimeout"), (httpx.ConnectError("down"), "portfolio:ConnectError"),
    (httpx.Response(200, content=b"not json"), "portfolio:body"),
    (httpx.Response(200, json={"outcome": "weird", "key": "2026-03-10", "findings": [], "errors": []}), "portfolio:body"),
    (httpx.Response(200, json={"outcome": "imported", "key": "2026-03-11", "findings": [], "errors": []}), "portfolio:body"),
    (httpx.Response(200, json={"outcome": "imported", "key": "2026-03-10", "findings": [], "errors": ["boom"]}),
     "portfolio:body"),
    (httpx.Response(200, json={"outcome": "imported", "key": "2026-03-10", "findings": {}, "errors": []}), "portfolio:body"),
    (httpx.Response(200, json=["imported"]), "portfolio:body"),
])
def test_portfolio_5xx_stops_without_latch(w, answer, code):
    first = w.daily("id-1", 9)
    w.daily("id-2", 10)
    w.daily("id-3", 11)
    w.portfolio.reply = lambda payload, params: answer if payload["statement_date"] == "2026-03-10" else Portfolio.ok(payload)
    summary = w.go()
    assert w.portfolio.keys == ["2026-03-09", "2026-03-10"]  # the run stops at the failure
    assert w.state.keys() == {f"id-1:{sha(first)}"}  # the one posted before it stays recorded; no outcome from the failure
    assert summary["codes"] == ["failed", code] and not w.latch.exists()
    assert summary["counts"] == {"imported": 1}
    assert w.portfolio.runs[-1] | {"run_at": ""} == {"run_at": "", "result": "failed", "code": code, "files_seen": 3,
                                                     "posted": 2}
    w.portfolio.reply = lambda payload, params: Portfolio.ok(payload)
    w.go()  # the next run picks up where it stopped
    assert w.portfolio.keys == ["2026-03-09", "2026-03-10", "2026-03-10", "2026-03-11"]


@pytest.mark.parametrize("status", [400, 404, 409, 422])
def test_422_latches(w, status):
    w.daily("id-1", 9)
    w.daily("id-2", 10)
    w.portfolio.reply = lambda payload, params: httpx.Response(status, json={"detail": "x"})
    summary = w.go()
    assert summary["codes"] == ["latched", f"portfolio:{status}"] and w.state == {}
    assert w.portfolio.keys == ["2026-03-09"]
    assert json.loads(w.latch.read_text(encoding="utf-8"))["reason"] == f"portfolio:{status}"
    with pytest.raises(sinopac_futures.FuturesDisabled):
        w.go()
    assert w.portfolio.runs[-1] | {"run_at": ""} == {"run_at": "", "result": "latched", "code": f"portfolio:{status}",
                                                     "files_seen": 0, "posted": 0}


# ---------------------------------------------------------------- decrypt, Drive, password file


def test_decrypt_failure_latches(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, password="some-other-password")
    w.daily("id-1", 9)
    summary = w.go()
    assert summary["codes"] == ["latched", "decrypt"] and w.latch.exists() and w.portfolio.posts == []
    assert w.extract_calls == []


def test_each_folder_uses_its_own_password_key(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.cfg.password_file.write_text(f"{DAILY}=wrong-1\n{DAILY}.alt={PASSWORD}\n", encoding="utf-8")
    w.daily("id-1", 9)
    w.monthly("id-m")  # no candidate for the monthly folder key
    summary = w.go()
    assert summary["codes"] == ["latched", "decrypt"] and w.portfolio.posts == []
    assert w.extract_calls == [PASSWORD]  # the daily file unlocked with its folder's second candidate


def test_drive_download_failure_posts_nothing(w):
    w.daily("id-1", 9)
    w.daily("id-2", 10)
    w.fail_ids = {"id-1"}
    summary = w.go()
    assert summary["codes"] == ["failed", "drive:DownloadError"] and not w.latch.exists()
    assert w.portfolio.posts == [] and w.state == {}  # a gap would post the later day out of order
    assert w.portfolio.runs[-1]["result"] == "failed" and w.portfolio.runs[-1]["code"] == "drive:DownloadError"
    w.fail_ids = set()
    w.go()
    assert w.portfolio.keys == ["2026-03-09", "2026-03-10"]


def test_drive_listing_failure_reports_failed(w):
    w.daily("id-1", 9)
    w.list_rc = 3
    summary = w.go()
    assert summary["codes"] == ["failed", "drive_listing"] and w.portfolio.posts == [] and not w.latch.exists()
    assert w.portfolio.runs[-1]["code"] == "drive_listing"


def test_missing_password_file_reports_failed(w):
    w.daily("id-1", 9)
    w.cfg.password_file.unlink()
    summary = w.go()
    assert summary["codes"] == ["failed", "password_file"] and w.portfolio.posts == [] and not w.latch.exists()
    assert w.portfolio.runs[-1] | {"run_at": ""} == {"run_at": "", "result": "failed", "code": "password_file",
                                                     "files_seen": 0, "posted": 0}
    w.go(dry_run=True)
    assert len(w.portfolio.runs) == 1  # an owner run reports nothing


# ---------------------------------------------------------------- latch, dry run, filters


def test_unlatch(w):
    w.daily("id-1", 9)
    w.portfolio.reply = lambda payload, params: httpx.Response(422)
    w.go()
    assert w.latch.exists()
    assert w.go(unlatch=True)["codes"] == ["latched", "portfolio:422"] and w.latch.exists()  # still failing
    w.portfolio.reply = lambda payload, params: Portfolio.ok(payload)
    for kw in ({"file_id": "id-1"}, {"since": date(2026, 1, 1)}, {"until": date(2026, 12, 31)},
               {"retry_date": date(2026, 3, 9)}, {"dry_run": True}):
        (w.cfg.state_dir / "sinopac-futures-state.json").unlink(missing_ok=True)
        assert w.go(unlatch=True, **kw)["codes"] == [] and w.latch.exists(), kw
    (w.cfg.state_dir / "sinopac-futures-state.json").unlink(missing_ok=True)
    with pytest.raises(sinopac_futures.FuturesDisabled):
        w.go(validate_history=True)
    summary = w.go(unlatch=True)
    assert summary["codes"] == [] and not w.latch.exists()
    assert w.portfolio.runs[-1]["result"] == "latched"  # the --unlatch run itself files no report


def test_dry_run_never_writes_state(w):
    w.daily("id-1", 9)
    w.monthly("id-m")
    summary = w.go(dry_run=True)
    assert [params for _, params in w.portfolio.posts] == [{"dry_run": "true"}] * 2
    assert w.state == {} and summary["dry_run"] is True and summary["counts"] == {"imported": 2}
    assert w.portfolio.runs == []
    w.portfolio.reply = lambda payload, params: httpx.Response(422)
    assert w.go(dry_run=True)["codes"] == ["failed", "portfolio:422"]
    assert not w.latch.exists() and w.state == {} and len(w.portfolio.posts) == 4  # a dry run goes on after a 422
    w.portfolio.reply = lambda payload, params: Portfolio.ok(payload)
    w.go()
    assert [params["dry_run"] for _, params in w.portfolio.posts][-2:] == ["false", "false"] and len(w.state) == 2


def test_range_and_file_filters(w):
    w.daily("id-1", 9)
    w.daily("id-2", 10)
    w.monthly("id-m")  # dated by its month end, 2026-03-31
    w.go(since=date(2026, 3, 10), until=date(2026, 3, 30))
    assert w.portfolio.keys == ["2026-03-10"] and len(w.state) == 1
    w.go(file_id="id-m")
    assert w.portfolio.keys[-1] == "2026-03" and w.downloads()[-1] == "id-m"
    assert w.go(file_id="id-missing")["codes"] == ["failed", "file_not_found"]
    w.go(until=date(2026, 3, 9))
    assert w.portfolio.keys == ["2026-03-10", "2026-03", "2026-03-09"]


def test_only_plain_run_reports(w):
    w.daily("id-1", 9)
    w.go(file_id="id-1")
    w.go(since=date(2026, 1, 1))
    w.go(until=date(2026, 12, 31))
    w.go(retry_date=date(2026, 3, 9))
    w.go(unlatch=True)
    w.go(dry_run=True)
    w.go(validate_history=True)
    w.go(ack_key="2026-03-09", ack_status="findings")
    assert w.portfolio.runs == []
    w.latch.write_text('{"reason": "x"}')
    for kw in ({"dry_run": True}, {"file_id": "id-1"}, {"since": date(2026, 1, 1)}, {"retry_date": date(2026, 3, 9)},
               {"validate_history": True}):
        with pytest.raises(sinopac_futures.FuturesDisabled):
            w.go(**kw)
    assert w.portfolio.runs == []
    with pytest.raises(sinopac_futures.FuturesDisabled):
        w.go()
    assert len(w.portfolio.runs) == 1 and w.portfolio.runs[0]["code"] == "x"


def test_run_report_failure_does_not_break_the_run(w):
    w.daily("id-1", 9)
    inner = w.portfolio

    def wrapped(request):
        if request.url.path.endswith("/runs"):
            return httpx.Response(500)
        return inner(request)
    w.portfolio = wrapped
    assert w.go()["codes"] == [] and len(w.state) == 1


def test_unexpected_exception_reports_failed_run(w, monkeypatch):
    w.daily("id-1", 9)

    def boom(*a, **k):
        raise OSError("disk 範例期貨")
    monkeypatch.setattr(sinopac_futures.parser, "write_private_json", boom)
    summary = w.go()
    assert summary["codes"] == ["failed", "internal:OSError"] and not w.latch.exists()
    assert w.portfolio.runs[-1]["code"] == "internal:OSError"


def test_staging_is_swept_and_left_empty(w):
    w.daily("id-1", 9)
    w.cfg.staging_dir.mkdir(parents=True, exist_ok=True)
    stale = w.cfg.staging_dir / "sinopac-futures-1-1.pdf"
    stale.write_bytes(b"x")
    other = w.cfg.staging_dir / "cathay-1-1.pdf"
    other.write_bytes(b"x")
    for kw in ({}, {"dry_run": True}, {"validate_history": True}):
        w.go(**kw)
        assert list(w.cfg.staging_dir.glob("sinopac-futures-*")) == []
    assert other.exists() and not w.cfg.inbox_dir.exists()


def test_already_running(w):
    import fcntl
    w.cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with open(w.cfg.state_dir / ".sinopac-futures.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        with pytest.raises(sinopac_futures.AlreadyRunning):
            w.go()


def test_no_statement_content_in_summary_report_or_logs(w, caplog):
    w.daily("id-1", 9)
    w.monthly("id-m")
    caplog.set_level("DEBUG")
    out = json.dumps(w.go(), ensure_ascii=False) + json.dumps(w.state, ensure_ascii=False) + caplog.text
    w.go(validate_history=True)
    out += json.dumps(w.report, ensure_ascii=False)
    for word in ("範例", "sA1B2", "151166", "151,166", "日對帳單"):
        assert word not in out


# ---------------------------------------------------------------- --validate-history


def test_validate_history_posts_one_batch(w):
    w.monthly("id-m", name="0.pdf")
    w.daily("id-10", 10, name="a.pdf")
    w.daily("id-09", 9, name="b.pdf")
    before = {"seed:abc": {"outcome": "imported", "kind": "daily", "key": "2026-03-01", "date": "2026-03-01",
                           "md5": "x", "at": "t"}}
    w.cfg.state_dir.mkdir(parents=True, exist_ok=True)
    state_path = w.cfg.state_dir / "sinopac-futures-state.json"
    state_path.write_text(json.dumps(before), encoding="utf-8")
    findings = {"2026-03-01": [], "2026-03-09": [{"code": "prev", "dates": ["2026-03-01"], "letters": []}],
                "2026-03-10": [], "2026-03": [{"code": "gap", "dates": ["2026-03-02"], "letters": []},
                                              {"code": "diff", "dates": [], "letters": ["b", "k"]}]}
    w.portfolio.validate_reply = lambda payloads: httpx.Response(200, json=Portfolio.answer(
        payloads, outcomes={"2026-03-10": "duplicate"}, findings=findings))
    summary = w.go(validate_history=True)
    assert w.portfolio.posts == [] and len(w.portfolio.validates) == 1
    batch = w.portfolio.validates[0]["payloads"]
    assert [key_of(p) for p in batch] == ["2026-03-09", "2026-03-10", "2026-03"]
    assert [p["source"]["drive_file_id"] for p in batch] == ["id-09", "id-10", "id-m"]
    report = w.report
    assert report.pop("duration_s") >= 0
    assert report == {"result": "ok", "counts": {"imported": 2, "duplicate": 1},
                      "items": [{"key": "2026-03-09", "outcome": "imported"}, {"key": "2026-03-10", "outcome": "duplicate"},
                                {"key": "2026-03", "outcome": "imported"}],
                      "findings": findings, "errors": []}
    assert oct((w.cfg.state_dir / "sinopac-futures-history-report.json").stat().st_mode)[-3:] == "600"
    assert json.loads(state_path.read_text(encoding="utf-8")) == before  # no state written, none read
    assert summary["counts"] == {"imported": 2, "duplicate": 1} and summary["codes"] == []
    assert summary["validate_history"] is True and summary["posted"] == 3 and w.portfolio.runs == []


def test_validate_history_uses_its_own_timeout(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, env={"STATEMENT_SINOPAC_FUTURES_TIMEOUT_S": "45"})
    w.daily("id-1", 9)
    w.go(validate_history=True)
    w.go(dry_run=True)
    w.go()
    assert w.portfolio.timeouts["validate"] == [600]
    assert w.portfolio.timeouts["sinopac-futures-statement"] == [45, 45]
    assert w.portfolio.timeouts["runs"] == [45]
    (tmp_path / "b").mkdir()
    w2 = World(tmp_path / "b", monkeypatch, env={"STATEMENT_SINOPAC_FUTURES_VALIDATE_TIMEOUT_S": "900"})
    w2.daily("id-1", 9)
    w2.go(validate_history=True)
    assert w2.portfolio.timeouts["validate"] == [900]


def test_validate_history_collects_parse_errors_and_posts_the_rest(w):
    w.daily("id-1", 9)
    name, kw = DAILY_IDENTITIES[0]
    w.add(DAILY, "id-2", "2026-03-10_日對帳單.pdf", day_rows(10, **kw))
    w.add(DAILY, "id-3", "scan.pdf", day_rows(11, **kw))
    summary = w.go(validate_history=True)
    assert [key_of(p) for p in w.portfolio.validates[0]["payloads"]] == ["2026-03-09"]
    assert w.report["errors"] == [{"folder": "daily", "file_date": "2026-03-10", "code": f"identity:{name}"},
                                  {"folder": "daily", "file_date": None, "code": f"identity:{name}"}]
    assert summary["codes"] == ["failed", f"identity:{name}"] and not w.latch.exists()


@pytest.mark.parametrize("answer, code", [
    (httpx.Response(500, json={"items": [{"key": "2026-03-09", "outcome": "imported"}], "findings": {}}), "portfolio:500"),
    (httpx.Response(504), "portfolio:504"), (httpx.Response(422), "portfolio:422"),
    (httpx.ReadTimeout("slow"), "portfolio:ReadTimeout"),
    (httpx.Response(200, content=b"nope"), "portfolio:body"),
    (httpx.Response(200, json={"items": [], "findings": {}}), "portfolio:body"),
    (httpx.Response(200, json={"items": [{"file_name": "x", "key": "2026-03-10", "outcome": "imported"}],
                               "findings": {}}), "portfolio:body"),
    (httpx.Response(200, json={"items": [{"file_name": "x", "key": "2026-03-09", "outcome": "held"}],
                               "findings": {}}), "portfolio:body"),
    (httpx.Response(200, json={"items": [{"file_name": "x", "key": "2026-03-09", "outcome": "imported"}],
                               "findings": {"2026-03-09": [{"code": "prev", "dates": ["範例"], "letters": []}]}}),
     "portfolio:body"),
    (httpx.Response(200, json={"items": [{"file_name": "x", "key": "2026-03-09", "outcome": "imported"}],
                               "findings": {"2026-03-09": [{"code": "prev 151166", "dates": [], "letters": []}]}}),
     "portfolio:body"),
])
def test_validate_failure_records_no_outcome(w, answer, code):
    w.daily("id-1", 9)

    def reply(payloads):
        if isinstance(answer, Exception):
            raise answer
        return answer
    w.portfolio.validate_reply = reply
    summary = w.go(validate_history=True)
    report = w.report
    assert summary["codes"] == ["failed", code] and summary["counts"] == {} and not w.latch.exists()
    assert (report["result"], report["counts"], report["items"], report["findings"]) == (code, {}, [], {})
    assert report["duration_s"] >= 0 and w.state == {}


def test_validate_history_with_nothing_parsed_posts_nothing(w):
    summary = w.go(validate_history=True)
    assert w.portfolio.validates == [] and summary["codes"] == []
    assert w.report | {"duration_s": None} == {"result": "ok", "counts": {}, "items": [], "findings": {}, "errors": [],
                                               "duration_s": None}


# ---------------------------------------------------------------- --ack


def test_ack_posts_status(w):
    w.daily("id-1", 9)
    w.portfolio.reply = lambda payload, params: httpx.Response(422)
    w.go()
    assert w.latch.exists()
    calls = len(w.runner.calls)
    summary = w.go(ack_key="2026-03-09", ack_status="conflict")
    assert summary == {"ack": {"key": "2026-03-09", "status": "conflict"}, "codes": []}
    summary = w.go(ack_key="2026-03", ack_status="findings")
    assert summary == {"ack": {"key": "2026-03", "status": "findings"}, "codes": []}
    assert w.portfolio.acks == [(f"{ENDPOINT}/days/2026-03-09/ack", {"status": "conflict"}),
                                (f"{ENDPOINT}/days/2026-03/ack", {"status": "findings"})]
    assert len(w.runner.calls) == calls and len(w.portfolio.posts) == 1 and len(w.portfolio.runs) == 1  # no Drive, no report
    w.portfolio.ack_status = 404
    with pytest.raises(sinopac_futures.NothingPending):
        w.go(ack_key="2026-03-09", ack_status="findings")
    w.portfolio.ack_status = 503
    assert w.go(ack_key="2026-03-09", ack_status="findings")["codes"] == ["failed", "portfolio:503"]


# ---------------------------------------------------------------- CLI


def test_cli_flags_and_exit_codes(tmp_path, monkeypatch, capsys):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path / "s"), "STATEMENT_PASSWORD_FILE": str(tmp_path / "absent.env")})
    monkeypatch.setattr(config, "load", lambda env=None: cfg)
    seen: dict = {}

    def fake(kind):
        def run(c, **kw):
            seen.clear()
            seen.update(kw)
            if kind == "running":
                raise sinopac_futures.AlreadyRunning(".sinopac-futures.lock")
            if kind == "disabled":
                raise sinopac_futures.FuturesDisabled("x")
            if kind == "pending":
                raise sinopac_futures.NothingPending("2026-03")
            if kind == "boom":
                raise RuntimeError("boom 範例期貨")
            return {"counts": {"imported": 1}, "codes": ["failed", "x"] if kind == "failed" else [], "files_seen": 1}
        return run
    for kind, code in [("ok", 0), ("failed", 1), ("running", 3), ("disabled", 1), ("pending", 1), ("boom", 1)]:
        monkeypatch.setattr(sinopac_futures, "run", fake(kind))
        assert cli.main(["sinopac-futures"]) == code, kind
    err = capsys.readouterr().err
    assert "範例期貨" not in err and "RuntimeError" in err and "nothing pending" in err
    monkeypatch.setattr(sinopac_futures, "run", fake("ok"))
    argv = ["sinopac-futures", "--dry-run", "--file", "F", "--since", "2026-03-01", "--until", "2026-03-31",
            "--retry", "2026-03-09", "--unlatch", "--validate-history"]
    assert cli.main(argv) == 0
    assert json.loads(capsys.readouterr().out) == {"counts": {"imported": 1}}  # --validate-history prints counts only
    assert {k: v for k, v in seen.items() if k not in ("portfolio_http", "drive")} == {"dry_run": True, "file_id": "F", "since": date(2026, 3, 1), "until": date(2026, 3, 31),
                    "retry_date": date(2026, 3, 9), "ack_key": None, "ack_status": None, "unlatch": True,
                    "validate_history": True}
    assert cli.main(["sinopac-futures", "--ack", "2026-03", "--status", "findings"]) == 0
    assert (seen["ack_key"], seen["ack_status"]) == ("2026-03", "findings")
    assert cli.main(["sinopac-futures", "--ack", "2026-03-09", "--status", "conflict"]) == 0
    assert (seen["ack_key"], seen["ack_status"]) == ("2026-03-09", "conflict")


@pytest.mark.parametrize("argv", [
    ["--ack", "2026-03-09"], ["--status", "conflict"], ["--ack", "2026-03-09", "--status", "conflict", "--dry-run"],
    ["--ack", "2026-03-09", "--status", "conflict", "--unlatch"],
])
def test_cli_ack_needs_status_alone(tmp_path, monkeypatch, capsys, argv):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path / "s")})
    monkeypatch.setattr(config, "load", lambda env=None: cfg)
    monkeypatch.setattr(sinopac_futures, "run", lambda c, **kw: pytest.fail("must not run"))
    assert cli.main(["sinopac-futures", *argv]) == 2
    assert "--" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["--ack", "2026-13", "--status", "findings"], ["--ack", "2026-02-30", "--status", "findings"],
    ["--ack", "20260309", "--status", "findings"], ["--ack", "2026-03-09", "--status", "held"],
])
def test_cli_rejects_bad_ack_key_or_status(tmp_path, monkeypatch, argv):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path / "s")})
    monkeypatch.setattr(config, "load", lambda env=None: cfg)
    monkeypatch.setattr(sinopac_futures, "run", lambda c, **kw: pytest.fail("must not run"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["sinopac-futures", *argv])
    assert exc.value.code == 2


def test_cli_missing_portfolio_token_file_stops(tmp_path, monkeypatch):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path / "s"),
                       "STATEMENT_PORTFOLIO_TOKEN_FILE": str(tmp_path / "absent-token")})
    monkeypatch.setattr(config, "load", lambda env=None: cfg)
    monkeypatch.setattr(sinopac_futures, "run", lambda c, **kw: pytest.fail("must not run"))
    assert cli.main(["sinopac-futures"]) == 2
