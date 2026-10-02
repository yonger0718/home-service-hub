import json
import threading

from sqlalchemy import select, text

from app.models import Account, ImportRun
from app.services import moze_import_service

from tests.helpers import advisory_locks


def _file(moze, name="錢包", amount="-120") -> bytes:
    return moze.csv(moze.opening(name, "TWD", "2000"), moze.row(name, "TWD", "支出", amount, main="飲食"))


def _upload(client, data: bytes, *, dry_run=False, renames=None):
    form = {"renames": json.dumps(renames)} if renames is not None else {}
    return client.post(
        "/imports/moze",
        params={"dry_run": str(dry_run).lower()},
        files={"file": ("moze.csv", data, "text/csv")},
        data=form,
    )


def test_import_and_latest_report(client, db_session, moze):
    response = _upload(client, _file(moze))

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["status"] == "succeeded"
    assert report["summary"]["accounts"][0]["balance"] == "1880.0000"
    latest = client.get("/imports/latest")
    assert latest.status_code == 200
    assert latest.json()["id"] == report["id"]


def test_latest_is_404_before_any_import(client, db_session):
    response = client.get("/imports/latest")
    assert response.status_code == 404
    assert response.json()["message"] == "no import has run yet"


def test_dry_run_and_renames_field(client, db_session, moze):
    assert _upload(client, _file(moze, name="A")).status_code == 200

    preview = _upload(client, _file(moze, name="B"), dry_run=True)
    assert preview.status_code == 200
    assert preview.json()["status"] == "dry_run"
    assert preview.json()["summary"]["accounts_archived"] == ["A"]
    assert list(db_session.scalars(select(Account.name))) == ["A"]

    renamed = _upload(client, _file(moze, name="B"), renames={"A": "B"})
    assert renamed.status_code == 200
    assert renamed.json()["summary"]["accounts_renamed"] == [{"from": "A", "to": "B"}]


def test_invalid_renames_field_is_400(client, db_session, moze):
    response = client.post(
        "/imports/moze", files={"file": ("moze.csv", _file(moze), "text/csv")}, data={"renames": "A=B"}
    )
    assert response.status_code == 400


def test_failed_import_is_422_and_latest_returns_the_failed_run(client, db_session, moze):
    assert _upload(client, _file(moze)).status_code == 200
    bad = moze.csv(moze.opening("錢包", "TWD", "0"), moze.row("錢包", "TWD", "測試", "-1"))

    response = _upload(client, bad)

    assert response.status_code == 422
    assert "row 3: unknown 記錄類型 '測試'" in response.json()["message"]
    latest = client.get("/imports/latest").json()
    assert latest["status"] == "failed"
    assert "row 3" in latest["summary"]["error"]


def test_upload_over_20_mb_is_413(client, db_session):
    response = client.post(
        "/imports/moze", files={"file": ("big.csv", b"x" * (20 * 1024 * 1024 + 1), "text/csv")}
    )
    assert response.status_code == 413


def test_locked_import_is_423(client, db_session, moze, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    response = _upload(client, _file(moze))
    assert response.status_code == 423
    assert "import is locked" in response.json()["message"]
    assert db_session.scalar(select(ImportRun)) is None


def test_rest_import_during_cli_import_gets_409_and_cli_data_wins(client, db_session, pg_engine, moze, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    real_parse = moze_import_service.parse_moze_csv

    def slow_parse(data: bytes):
        entered.set()
        assert release.wait(10)
        return real_parse(data)

    monkeypatch.setattr(moze_import_service, "parse_moze_csv", slow_parse)
    results = []
    cli = threading.Thread(
        target=lambda: results.append(moze_import_service.run_import(pg_engine, _file(moze, name="CLI"), "cli.csv"))
    )
    cli.start()
    assert entered.wait(10)

    response = _upload(client, _file(moze, name="REST"))

    release.set()
    cli.join(10)
    assert response.status_code == 409
    assert response.json()["message"] == "import already running"
    assert results[0]["status"] == "succeeded"
    assert list(db_session.scalars(select(Account.name))) == ["CLI"]
    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM import_run")).scalar_one() == 1
    assert advisory_locks(pg_engine) == 0
