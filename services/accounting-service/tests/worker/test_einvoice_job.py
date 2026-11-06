import fcntl
import hashlib
import json
from configparser import ConfigParser
from pathlib import Path

import httpx
import pytest

from worker import cli, config, einvoice
from worker.api import ApiClient
from worker.drive import Drive
from worker.runner import FakeRunner, Result

H1 = "表頭=M|載具名稱|載具號碼|發票日期|商店統編|商店店名|發票號碼|總金額|發票狀態|"
H2 = "明細=D|發票號碼|小計|品項名稱|"
NAME = "2026-10_財政部_手機條碼發票彙整_FAKE.csv"


def csv(*rows) -> bytes:
    return ("\n".join([H1, H2, *rows]) + "\n").encode("utf-8")


def m(number, total="150", status="開立", day="20261005"):
    return f"M|測試載具|/FAKE+01|{day}|12345678|測試商店|{number}|{total}|{status}|"


def d(number, amount):
    return f"D|{number}|{amount}|測試品|"


F1 = csv(m("AB00000001"), d("AB00000001", "150"), m("AB00000002", "40"), d("AB00000002", "40"))
F2 = csv(m("AB00000001", "99"), d("AB00000001", "99"), m("AB00000002", "40", "作廢"), d("AB00000002", "40"))


class HomeHub:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.fail: tuple[int, dict] | None = None

    def __call__(self, request):
        self.calls.append((request.method, request.url.path, json.loads(request.content)))
        if self.fail:
            return httpx.Response(self.fail[0], json=self.fail[1])
        if request.url.path.endswith("/import"):
            return httpx.Response(200, json={"received": 1})
        return httpx.Response(200, json={"matched": 0})


class World:
    def __init__(self, tmp_path):
        self.cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path / "state"),
                                "STATEMENT_API_TOKEN_FILE": str(tmp_path / "tok")})
        self.files: list[dict] = []
        self.list_rc = 0
        self.hub = HomeHub()
        self.listing_md5_override: dict[str, str] = {}
        self.runner = FakeRunner({"rclone": self._rclone})

    def add(self, fid, data, name=None, path=None):
        name = name or f"{fid}_{NAME}"
        self.files.append({"id": fid, "name": name, "path": path or name, "data": data})

    def _rclone(self, args, stdin):
        if args[1] == "lsjson":
            assert args[-1] == "gdrive:財務對帳單/電子發票/財政部"
            rows = []
            for f in self.files:
                row = {"Path": f["path"], "Name": f["name"], "Size": len(f["data"]), "ModTime": "", "ID": f["id"],
                       "Hashes": {"md5": self.listing_md5_override.get(f["id"], hashlib.md5(f["data"]).hexdigest())}}
                if f.get("nomd5"):
                    row["Hashes"] = {}
                rows.append(row)
            return Result(self.list_rc, json.dumps(rows).encode(), b"")
        Path(args[5]).write_bytes(next(f["data"] for f in self.files if f["id"] == args[4]))
        return Result(0, b"", b"")

    def api(self):
        return ApiClient(httpx.Client(base_url="http://homehub.test", transport=httpx.MockTransport(self.hub)), "t")

    def go(self):
        return einvoice.run(self.cfg, drive=Drive(self.cfg, self.runner), api=self.api())

    def posts(self, suffix):
        return [c for c in self.hub.calls if c[1].endswith(suffix)]


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def test_run_posts_one_merged_import(w):
    w.add("drive-fake-1", F1, path="a.csv")
    w.add("drive-fake-2", F2, path="b.csv")
    s = w.go()
    imports = w.posts("/einvoices/import")
    assert len(imports) == 1 and not w.posts("/einvoices/match")
    invs = {i["number"]: i for i in imports[0][2]["invoices"]}
    assert len(imports[0][2]["invoices"]) == 2
    assert invs["AB00000001"]["total_amount"] == "99" and invs["AB00000001"]["source_drive_id"] == "drive-fake-2"
    assert invs["AB00000002"]["status"] == "作廢"
    assert s["files_seen"] == 2 and s["files_parsed"] == 2 and s["invoices"] == 2 and s["import"] == {"received": 1}
    assert s["codes"] == [] and s["failures"] == [] and s["match"] is None


def test_later_path_wins_regardless_of_listing_order(w):
    w.add("drive-fake-2", F2, path="b.csv")
    w.add("drive-fake-1", F1, path="a.csv")
    w.go()
    invs = {i["number"]: i for i in w.posts("/einvoices/import")[0][2]["invoices"]}
    assert invs["AB00000001"]["source_drive_id"] == "drive-fake-2"


def test_repeat_with_another_date_in_the_period_merges_and_later_file_wins(w):
    # the API's identity is (number, period): 10-05 and 09-28 are both period 11509 (Sep-Oct)
    w.add("drive-fake-1", csv(m("AB00000001", "150", day="20261005"), d("AB00000001", "150")), path="a.csv")
    w.add("drive-fake-2", csv(m("AB00000001", "99", day="20260928"), d("AB00000001", "99")), path="b.csv")
    s = w.go()
    invs = w.posts("/einvoices/import")[0][2]["invoices"]
    assert len(invs) == 1 and s["invoices"] == 1
    assert (invs[0]["issue_date"], invs[0]["total_amount"], invs[0]["source_drive_id"]) == (
        "2026-09-28", "99", "drive-fake-2")
    assert invs[0]["items"] == [{"seq": 1, "name": "測試品", "subtotal": "99"}]


def test_same_number_in_another_period_posts_both(w):
    w.add("drive-fake-1", csv(m("AB00000001", "150", day="20261005"), d("AB00000001", "150")), path="a.csv")
    w.add("drive-fake-2", csv(m("AB00000001", "99", day="20261205"), d("AB00000001", "99")), path="b.csv")
    w.go()
    invs = w.posts("/einvoices/import")[0][2]["invoices"]
    assert sorted(i["issue_date"] for i in invs) == ["2026-10-05", "2026-12-05"]


def test_bad_file_does_not_stop_others(w, tmp_path, monkeypatch, capsys):
    w.add("drive-fake-1", csv(d("AB00000001", "5")), path="a.csv")
    w.add("drive-fake-2", F2, path="b.csv")
    s = w.go()
    assert s["failures"] == [{"file": "drive-fake-1", "code": "parse:orphan_item"}]
    assert len(w.posts("/einvoices/import")[0][2]["invoices"]) == 2 and s["parse_failed"] == 1
    # exit code via the CLI
    (tmp_path / "tok").write_text("tok")
    monkeypatch.setenv("STATEMENT_STATE_DIR", str(tmp_path / "state2"))
    monkeypatch.setenv("STATEMENT_API_TOKEN_FILE", str(tmp_path / "tok"))
    w.cfg = config.load()
    real = httpx.Client
    monkeypatch.setattr(cli.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(w.hub), **kw))
    monkeypatch.setattr(cli, "SubprocessRunner", lambda: w.runner)
    assert cli.main(["einvoice"]) == 1
    assert json.loads(capsys.readouterr().out)["failures"][0]["code"] == "parse:orphan_item"


def test_no_invoices_calls_match(w):
    s = w.go()
    assert len(w.posts("/einvoices/match")) == 1 and not w.posts("/einvoices/import")
    assert s["files_seen"] == 0 and s["match"] == {"matched": 0} and s["import"] is None


def test_too_large_and_md5_mismatch_are_file_failures(w, monkeypatch):
    monkeypatch.setattr(einvoice, "MAX_CSV_BYTES", 5000)
    big = b"x" * 6000
    w.add("drive-fake-1", big, path="a.csv")
    w.add("drive-fake-2", F1, path="b.csv")
    w.add("drive-fake-3", F2, path="c.csv")
    w.listing_md5_override["drive-fake-3"] = "0" * 32
    s = w.go()
    assert s["failures"] == [{"file": "drive-fake-1", "code": "drive:DownloadError"},
                             {"file": "drive-fake-3", "code": "drive:VerifyError"}]
    assert len(w.posts("/einvoices/import")[0][2]["invoices"]) == 2


def test_listing_error_is_a_code_without_path(w, caplog, capsys):
    caplog.set_level("DEBUG")
    w.add("drive-fake-1", F1)
    w.list_rc = 1
    s = w.go()
    assert s["codes"] == ["drive_listing"] and not w.hub.calls
    w.list_rc = 0
    w.files[0]["nomd5"] = True
    s = w.go()
    assert s["codes"] == ["drive_listing"]
    assert "FAKE.csv" not in json.dumps(s, ensure_ascii=False) + caplog.text


def test_api_error_is_a_code(w, caplog):
    caplog.set_level("DEBUG")
    w.add("drive-fake-1", F1)
    w.hub.fail = (503, {"detail": "down"})
    assert w.go()["codes"] == ["api:503"]
    w.hub.fail = (422, {"detail": [{"input": "ECHOED-SECRET-TEXT", "msg": "ECHOED-SECRET-TEXT"}]})
    s = w.go()
    assert s["codes"] == ["api:422"]
    assert "ECHOED-SECRET-TEXT" not in caplog.text + json.dumps(s)


def test_privacy_no_names_or_carrier_anywhere(w, tmp_path, caplog, monkeypatch, capsys):
    caplog.set_level("DEBUG")
    seen: list[str] = []
    orig = w._rclone

    def spy(args, stdin):
        out = orig(args, stdin)
        if args[1] == "backend":
            seen.extend(p.name for p in Path(args[5]).parent.iterdir())
        return out

    w.runner = FakeRunner({"rclone": spy})
    w.add("drive-fake-1", F1, name=NAME, path="2026/" + NAME)
    w.add("drive-fake-2", F2, name="財政部_手機條碼_FAKE.csv", path="2026/財政部_手機條碼_FAKE.csv")
    w.add("drive-fake-3", csv(d("AB00000001", "5")), name="bad_FAKE.csv")
    s = w.go()
    print(json.dumps(s, ensure_ascii=False))
    out = capsys.readouterr().out
    blob = (json.dumps(w.hub.calls, ensure_ascii=False) + out + caplog.text + " ".join(seen))
    for needle in ("測試載具", "/FAKE+01", "FAKE.csv", "財政部_手機條碼"):
        assert needle not in blob
    assert seen and all(n.startswith("einvoice-") for n in seen)
    assert list(w.cfg.staging_dir.iterdir()) == []


def test_sweeps_stale_staging(w):
    w.cfg.staging_dir.mkdir(parents=True)
    (w.cfg.staging_dir / "einvoice-1-1.csv").write_bytes(b"old")
    (w.cfg.staging_dir / "other.tmp").write_bytes(b"keep")
    w.go()
    assert [p.name for p in w.cfg.staging_dir.iterdir()] == ["other.tmp"]


def _cli_env(w, tmp_path, monkeypatch):
    (tmp_path / "tok").write_text("tok")
    monkeypatch.setenv("STATEMENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("STATEMENT_API_TOKEN_FILE", str(tmp_path / "tok"))


def test_second_run_already_running(w, tmp_path, monkeypatch, capsys):
    _cli_env(w, tmp_path, monkeypatch)
    (tmp_path / "state").mkdir()
    with open(tmp_path / "state" / einvoice.LOCK, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        assert cli.main(["einvoice"]) == 3
    assert "already running" in capsys.readouterr().out


def test_missing_token_file_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STATEMENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("STATEMENT_API_TOKEN_FILE", str(tmp_path / "absent"))
    assert cli.main(["einvoice"]) == 2
    assert "API token file is missing or not a file" in capsys.readouterr().err


def test_unexpected_exception_is_class_name_exit_1(w, tmp_path, monkeypatch, capsys):
    _cli_env(w, tmp_path, monkeypatch)
    monkeypatch.setattr(cli.einvoice, "run", lambda *a, **k: (_ for _ in ()).throw(KeyError("SECRET")))
    assert cli.main(["einvoice"]) == 1
    err = capsys.readouterr().err
    assert "einvoice failed: KeyError" in err and "SECRET" not in err


def test_units_shape():
    base = Path(__file__).parents[2] / "deploy" / "statements"
    svc, tim = ConfigParser(interpolation=None), ConfigParser(interpolation=None)
    svc.optionxform = tim.optionxform = str
    svc.read(base / "homehub-einvoice.service")
    tim.read(base / "homehub-einvoice.timer")
    assert svc["Service"]["ExecStart"].endswith("-m worker einvoice")
    assert svc["Service"]["Type"] == "oneshot" and svc["Service"]["UMask"] == "0077"
    assert tim["Timer"]["OnCalendar"] == "*-*-* 04:10:00 Asia/Taipei"
    assert tim["Timer"]["Persistent"] == "true" and tim["Timer"]["Unit"] == "homehub-einvoice.service"
