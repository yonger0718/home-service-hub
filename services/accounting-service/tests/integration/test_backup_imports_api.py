import io
import json
import zipfile

from sqlalchemy import select, text

from app.main import app
from app.models import ImportRun
from app.routers import imports
from app.routers.imports import get_backup_exporter
from app.services.moze_import_service import IMPORT_LOCK_KEY


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("moze.realm", b"synthetic")
    return buffer.getvalue()


def _doc(backup):
    return backup.doc(
        accounts=[backup.account("A-WALLET", "錢包", originalAmount=1000)],
        records=[
            backup.record("R-1", price=-120),
            backup.record("R-LOAN", type_=6, price=-3000, date="2026-11-09T00:00:00", name="房貸"),
            backup.record("R-INT", type_=15, price=-12, date="2026-12-09T00:00:00"),
        ],
    )


def _use_exporter(command):
    """The client fixture clears app.dependency_overrides at teardown."""
    app.dependency_overrides[get_backup_exporter] = lambda: command


def _upload(client, *, dry_run=False, strict=True, renames=None, data=None):
    form = {"renames": json.dumps(renames)} if renames is not None else {}
    return client.post(
        "/imports/moze-backup",
        params={"dry_run": str(dry_run).lower(), "strict": str(strict).lower()},
        files={"file": ("MOZE_4.0.zip", data if data is not None else _zip_bytes(), "application/zip")},
        data=form,
    )


def test_backup_upload_imports_and_latest_reports_kind(client, db_session, backup, fake_exporter):
    _use_exporter(fake_exporter(_doc(backup)))
    response = _upload(client)

    assert response.status_code == 200, response.text
    report = response.json()
    assert (report["kind"], report["status"], report["file_name"]) == ("moze_backup", "succeeded", "MOZE_4.0.zip")
    assert report["summary"]["compared_accounts"] == {"compared": 0, "total": 1}
    latest = client.get("/imports/latest").json()
    assert (latest["id"], latest["kind"]) == (report["id"], "moze_backup")
    assert latest["exported_at"] is not None


def test_backup_dry_run_writes_nothing(client, db_session, backup, fake_exporter):
    _use_exporter(fake_exporter(_doc(backup)))
    response = _upload(client, dry_run=True)
    assert response.json()["status"] == "dry_run"
    assert db_session.scalar(select(ImportRun)) is None


def test_converter_failure_is_422_and_writes_no_run(client, db_session, fake_exporter):
    _use_exporter(fake_exporter(exit_code=1, message="moze-realm-export: archive has no moze.realm"))
    response = _upload(client)
    assert response.status_code == 422
    assert "moze.realm" in response.json()["message"]
    assert db_session.scalar(select(ImportRun)) is None


def test_upload_over_the_limit_is_413(client, db_session, fake_exporter, monkeypatch):
    monkeypatch.setattr(imports, "MAX_BACKUP_UPLOAD_BYTES", 10)
    _use_exporter(fake_exporter({}))
    assert _upload(client, data=b"x" * 11).status_code == 413


def test_backup_upload_refused_while_lock_held_or_locked(client, db_session, pg_engine, backup, fake_exporter, monkeypatch):
    _use_exporter(fake_exporter(_doc(backup)))
    with pg_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        holder.commit()
        assert _upload(client).status_code == 409
        holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
        holder.commit()
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    assert _upload(client).status_code == 423
    assert db_session.scalar(select(ImportRun)) is None


def test_invalid_renames_field_is_400(client, db_session, fake_exporter):
    _use_exporter(fake_exporter({}))
    response = client.post("/imports/moze-backup", files={"file": ("b.zip", _zip_bytes(), "application/zip")}, data={"renames": "A=B"})
    assert response.status_code == 400
