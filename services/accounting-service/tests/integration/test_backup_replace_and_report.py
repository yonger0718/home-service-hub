import json
import logging
import sys
import zipfile
from pathlib import Path
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.models import (
    Account,
    Category,
    Counterparty,
    EntryGroup,
    EntryRewardRule,
    LedgerEntry,
    MozeSchedule,
    Project,
    RewardRule,
)
from app.services import moze_backup_import_service as service
from app.services.moze_backup_import_service import ConverterError, convert_backup, main, run_backup_import
from app.services.moze_backup_json import TAIPEI, parse_backup_doc
from app.services.moze_csv import MozeImportError
from app.services.moze_import_service import IMPORT_LOCK_KEY, ImportAlreadyRunningError, ImportLockedError
from tests.helpers import _by_moze_id, _import_backup, advisory_locks


def _zip(tmp_path, name="MOZE_4.0.zip") -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("moze.realm", b"synthetic")
        archive.writestr("info", "version: 205")
    return path


def _doc(backup, *, price=-120, extra_records=()):
    return backup.doc(
        accounts=[backup.account("A-WALLET", "錢包", originalAmount=1000), backup.account("A-CARD", "華航卡", isCreditAccount=True)],
        categories=[backup.category()], classifications=[backup.classification()],
        projects=[backup.project("P-1", "日常")], targets=[backup.target("T-1", "Alan")],
        rules=[backup.rule("B-1", "A-CARD")],
        records=[
            backup.record("R-1", price=price, classification="K-LUNCH", project="P-1", bonusRewards=[]),
            backup.record("R-2", "A-CARD", price=-500, bonusRewards=["B-1"]),
            backup.record("R-3", type_=3, price=-200, target="T-1"),
            backup.record("R-4", "A-CARD", type_=6, price=-3000, date="2026-11-09T00:00:00"),
            *extra_records,
        ],
        packages=[backup.package("PK-1", ["R-1", "R-3"])],
        periods=[{"identifier": "PER-1", "startDate": "2026-01-05T00:00:00", "unit": 2}],
        installments=[{"identifier": "INS-1", "startDate": "2026-03-09T00:00:00", "installment": 3000.5,
                       "dateInfo": {"0": "2026-11-09T00:00:00"}}],
    )


def _manual(session, account_name: str, amount: str, **fields) -> LedgerEntry:
    account = session.scalar(select(Account).where(Account.name == account_name))
    entry = LedgerEntry(account_id=account.id, kind="expense", amount=Decimal(amount), currency=account.currency,
                        entry_date=date(2026, 9, 20), posted_date=date(2026, 9, 20), source="manual", **fields)
    session.add(entry)
    session.flush()
    return entry


def _snapshot(pg_engine) -> dict:
    with pg_engine.connect() as conn:
        return {
            table: conn.execute(text(f"SELECT * FROM {table} ORDER BY 1")).all()
            for table in ("account", "ledger_entry", "category", "project", "counterparty", "reward_rule",
                          "entry_group", "entry_reward_rule", "moze_schedule", "import_run")
        }


def _ids(session) -> dict:
    return {m.__name__: sorted(session.scalars(select(m.id))) for m in (Account, Category, Project, Counterparty, RewardRule)}


def test_reimport_is_idempotent(db_session, backup):
    data = parse_backup_doc(_doc(backup))
    first = _import_backup(db_session, data)
    before = _ids(db_session)
    entries_before = sorted((e.moze_id, e.amount, e.kind) for e in db_session.scalars(select(LedgerEntry)))

    second = _import_backup(db_session, data)

    assert _ids(db_session) == before
    assert sorted((e.moze_id, e.amount, e.kind) for e in db_session.scalars(select(LedgerEntry))) == entries_before
    assert db_session.scalar(select(func.count()).select_from(EntryGroup)) == 1
    assert [a["moze_part"] for a in second["accounts"]] == [a["moze_part"] for a in first["accounts"]]


def test_reimport_keeps_manual_entries_attachments_and_rule_ids(db_session, backup):
    # Review focus 3: manual rows, their attachments and the rule ids survive; nothing is duplicated.
    data = parse_backup_doc(_doc(backup))
    _import_backup(db_session, data)
    rule_id = db_session.scalar(select(RewardRule.id))
    lunch = db_session.scalar(select(Category).where(Category.name == "午餐"))
    manual = [_manual(db_session, "華航卡", "-10", category_id=lunch.id) for _ in range(3)]
    db_session.add(EntryRewardRule(entry_id=manual[0].id, rule_id=rule_id))
    db_session.commit()

    _import_backup(db_session, parse_backup_doc(_doc(backup, price=-150)))

    remaining = db_session.scalars(select(LedgerEntry).where(LedgerEntry.source == "manual")).all()
    assert sorted(e.id for e in remaining) == sorted(e.id for e in manual)
    assert db_session.scalar(select(RewardRule.id)) == rule_id
    assert db_session.scalar(select(EntryRewardRule)).entry_id == manual[0].id
    assert db_session.get(Category, lunch.id) is not None
    assert _by_moze_id(db_session, "R-1").amount == Decimal("-150.0000")
    assert db_session.scalar(select(func.count()).select_from(Category).where(Category.name == "午餐")) == 1
    assert db_session.scalar(select(func.count()).select_from(EntryGroup)) == 1


def test_reimport_refuses_currency_change_with_remaining_entries(db_session, backup):
    _import_backup(db_session, parse_backup_doc(_doc(backup)))
    _manual(db_session, "錢包", "-1")
    db_session.commit()
    doc = _doc(backup)
    doc["classes"]["AHAccount"][0]["mainCurrency"] = "JPY"

    with pytest.raises(MozeImportError, match="account '錢包': currency TWD → JPY refused"):
        _import_backup(db_session, parse_backup_doc(doc))
    db_session.rollback()
    assert db_session.scalar(select(Account.currency).where(Account.name == "錢包")) == "TWD"
    assert _by_moze_id(db_session, "R-1") is not None


def test_csv_rows_are_replaced_and_accounts_not_in_the_backup_archived(db_session, backup):
    old = Account(name="舊帳戶", currency="TWD", opening_balance=Decimal("50"))
    wallet = Account(name="錢包", currency="TWD")
    db_session.add_all([old, wallet])
    db_session.flush()
    db_session.add(LedgerEntry(account_id=wallet.id, kind="expense", amount=Decimal("-9"), currency="TWD",
                               entry_date=date(2026, 9, 1), posted_date=date(2026, 9, 1), source="moze_import"))
    db_session.commit()

    summary = _import_backup(db_session, parse_backup_doc(_doc(backup)))

    assert db_session.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.source == "moze_import")) == 0
    assert summary["accounts_archived"] == ["舊帳戶"]
    assert (old.is_archived, old.opening_balance) == (True, Decimal("0"))


def test_only_unused_rows_whose_moze_id_left_the_backup_are_deleted(db_session, backup):
    db_session.add_all([Project(name="舊專案"), Counterparty(name="Bob"), Project(name="已刪", moze_id="P-GONE")])
    db_session.commit()
    _import_backup(db_session, parse_backup_doc(_doc(backup)))
    assert sorted(db_session.scalars(select(Project.name))) == ["日常", "舊專案"]
    assert sorted(db_session.scalars(select(Counterparty.name))) == ["Alan", "Bob"]


def test_reimport_keeps_a_locally_created_account_and_its_opening_balance(client, db_session, backup):
    _import_backup(db_session, parse_backup_doc(_doc(backup)))
    created = client.post("/accounts", json={"name": "新銀行", "currency": "TWD", "opening_balance": "50000"})
    assert created.status_code == 201

    summary = _import_backup(db_session, parse_backup_doc(_doc(backup)))

    db_session.expire_all()
    account = db_session.scalar(select(Account).where(Account.name == "新銀行"))
    assert "新銀行" not in summary["accounts_archived"]
    assert (account.is_archived, account.opening_balance, account.settings_locally_edited) == (
        False, Decimal("50000.0000"), True
    )


def test_reimport_keeps_unused_settings_created_through_the_api(client, db_session, backup):
    _import_backup(db_session, parse_backup_doc(_doc(backup)))
    assert client.post("/counterparties", json={"name": "Bob"}).status_code == 201
    assert client.post("/projects", json={"name": "2026 京都"}).status_code == 201
    assert client.post("/categories", json={"kind": "expense", "name": "寵物"}).status_code == 201

    _import_backup(db_session, parse_backup_doc(_doc(backup)))

    db_session.expire_all()
    assert sorted(db_session.scalars(select(Counterparty.name))) == ["Alan", "Bob"]
    assert sorted(db_session.scalars(select(Project.name))) == ["2026 京都", "日常"]
    assert db_session.scalar(select(Category.id).where(Category.name == "寵物", Category.kind == "expense")) is not None


def test_schedule_is_replaced_on_each_import(db_session, backup):
    summary = _import_backup(db_session, parse_backup_doc(_doc(backup)))
    _import_backup(db_session, parse_backup_doc(_doc(backup)))
    rows = {(r.kind, r.moze_id) for r in db_session.scalars(select(MozeSchedule))}
    assert rows == {("period", "PER-1"), ("installment", "INS-1"), ("skipped_record", "R-4")}
    skipped = db_session.scalar(select(MozeSchedule).where(MozeSchedule.kind == "skipped_record"))
    assert (skipped.payload["date"], skipped.payload["total"]) == ("2026-11-09T00:00:00", "-3000")
    assert summary["schedules"] == {"period": 1, "installment": 1, "skipped_record": 1}


def test_report_shape_and_balance_comparison_default(db_session, backup):
    summary = _import_backup(db_session, parse_backup_doc(_doc(backup)))
    assert summary["kind"] == "moze_backup"
    assert summary["exported_at"] == "2026-10-01T17:00:37+08:00"
    assert summary["compared_accounts"] == {"compared": 0, "total": 2}
    assert summary["not_compared"] == ["錢包", "華航卡"]
    wallet = summary["accounts"][0]
    assert wallet == {
        "name": "錢包", "currency": "TWD", "balance": "680.0000", "moze_part": "680.0000",
        "previous_moze_part": None, "moze_balance": None, "difference": None, "compared": False,
    }
    assert summary["confirmed_maps"]["balance_info_key"] is None
    assert summary["confirmed_maps"]["due_rule"] == {"0": "fixed_day", "1": "days_after_closing"}
    assert (summary["rules"], summary["attachments"], summary["counterparties"], summary["groups"]) == (1, 1, 1, 1)
    assert summary["fx_backup_rate_missing"] == {"count": 0, "accounts": [], "reasons": {}}


def test_report_counts_records_whose_moze_rate_is_zero(db_session, backup):
    doc = _doc(backup, extra_records=[
        backup.record("R-YEN", "A-CARD", price=-1000, currency="JPY", currencyConversion="R-YEN"),
    ])
    doc["classes"]["AHCurrencyConversion"] = [backup.conversion("R-YEN", 0)]
    summary = _import_backup(db_session, parse_backup_doc(doc), rates={(date(2026, 9, 1), "JPY", "TWD"): Decimal("0.2")})
    assert summary["fx_backup_rate_missing"] == {"count": 1, "accounts": ["華航卡"], "reasons": {"zero_rate": 1}}
    assert summary["fx_outliers"] == []


def test_previous_moze_part_comes_from_the_ledger_before_deletion(db_session, backup):
    _import_backup(db_session, parse_backup_doc(_doc(backup)))
    summary = _import_backup(db_session, parse_backup_doc(_doc(backup, price=-150)))
    wallet = summary["accounts"][0]
    assert (wallet["previous_moze_part"], wallet["moze_part"]) == ("680.0000", "650.0000")


def latest_period(account: dict) -> str | None:
    """Test rule: the largest balanceInfo key holds the balance at export time."""
    return max(account["balanceInfo"], key=int) if account["balanceInfo"] else None


def _compared_doc(backup, moze_balance):
    doc = _doc(backup)
    doc["classes"]["AHAccount"][0]["balanceInfo"] = {"1759248000": moze_balance, "1756569600": 1}
    return doc


def test_manual_entries_do_not_break_strict_mode(db_session, backup, monkeypatch):
    monkeypatch.setattr(service, "balance_info_key", latest_period)
    _import_backup(db_session, parse_backup_doc(_compared_doc(backup, 680)), strict=True)
    _manual(db_session, "錢包", "-100")
    db_session.commit()

    summary = _import_backup(db_session, parse_backup_doc(_compared_doc(backup, 680)), strict=True)

    wallet = summary["accounts"][0]
    assert (wallet["moze_part"], wallet["moze_balance"], wallet["difference"], wallet["balance"]) == (
        "680.0000", "680.0000", "0.0000", "580.0000")
    assert summary["compared_accounts"] == {"compared": 1, "total": 2}
    assert summary["confirmed_maps"]["balance_info_key"] == "latest_period"


def test_strict_mode_fails_on_a_compared_difference(db_session, backup, monkeypatch):
    monkeypatch.setattr(service, "balance_info_key", latest_period)
    with pytest.raises(MozeImportError, match=r"strict mode: 1 compared account\(s\) differ from MOZE: 錢包 \(-20.0000\)"):
        _import_backup(db_session, parse_backup_doc(_compared_doc(backup, 700)), strict=True)
    db_session.rollback()
    summary = _import_backup(db_session, parse_backup_doc(_compared_doc(backup, 700)), strict=False)
    assert summary["accounts"][0]["difference"] == "-20.0000"


def test_convert_backup_runs_the_exporter_and_reports_failures(tmp_path, backup, fake_exporter):
    out = convert_backup(_zip(tmp_path), tmp_path, fake_exporter(_doc(backup)))
    assert json.loads(out.read_text(encoding="utf-8"))["exported_at"] == "2026-10-01T17:00:37"
    with pytest.raises(ConverterError, match=r"exit 1\): moze-realm-export: archive has no moze.realm"):
        convert_backup(_zip(tmp_path), tmp_path, fake_exporter(exit_code=1, message="moze-realm-export: archive has no moze.realm"))


def test_converter_missing_names_the_path(tmp_path, monkeypatch):
    monkeypatch.setenv("MOZE_REALM_EXPORTER", "/nonexistent/moze-realm-export/index.js")
    with pytest.raises(ConverterError, match="MOZE realm exporter not found: /nonexistent/moze-realm-export/index.js"):
        convert_backup(_zip(tmp_path), tmp_path)


def test_exporter_command_accepts_a_single_path_or_a_command_line(tmp_path, monkeypatch):
    script = tmp_path / "my tools" / "index.js"
    script.parent.mkdir()
    script.write_text("// synthetic", encoding="utf-8")
    monkeypatch.setenv("MOZE_REALM_EXPORTER", str(script))
    single = service.exporter_command()
    assert single[1:] == [str(script)] and single[0].endswith("node")

    monkeypatch.setenv("MOZE_REALM_EXPORTER", f"/usr/bin/node '{script}'")
    assert service.exporter_command() == ["/usr/bin/node", str(script)]

    monkeypatch.setenv("MOZE_REALM_EXPORTER", "/usr/bin/node /nonexistent/moze-realm-export/index.js")
    with pytest.raises(ConverterError, match="MOZE realm exporter script not found: /nonexistent/moze-realm-export/index.js"):
        service.exporter_command()


def test_converter_runs_from_a_two_word_exporter_setting(tmp_path, backup, monkeypatch):
    # "<interpreter> <script>.js": a bash wrapper stands in for node and runs the Python fake converter script.
    script = tmp_path / "exporter.js"
    script.write_text((Path(__file__).resolve().parents[1] / "fake_exporter.py").read_text(), encoding="utf-8")
    prepared = tmp_path / "prepared.json"
    prepared.write_text(json.dumps(_doc(backup), ensure_ascii=False), encoding="utf-8")
    wrapper = tmp_path / "run-fake"
    wrapper.write_text(
        f'#!/bin/bash\nscript="$1"; shift\nexec {sys.executable} "$script" {prepared} 0 "" "$@"\n', encoding="utf-8"
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("MOZE_REALM_EXPORTER", f"{wrapper} {script}")

    out = convert_backup(_zip(tmp_path), tmp_path)

    assert json.loads(out.read_text(encoding="utf-8"))["exported_at"] == "2026-10-01T17:00:37"


def test_unexpected_failure_stores_only_the_error_type(pg_engine, db_session, backup, fake_exporter, tmp_path, monkeypatch, caplog):
    def explode(*args, **kwargs):
        raise RuntimeError("row 午餐 -120 leaked")

    monkeypatch.setattr(service, "_backup_summary_run", explode)
    # alembic's fileConfig (the session's in-process upgrade) disables loggers that already exist; the service
    # runs migrations in a separate process, so re-enable the importer's logger here.
    monkeypatch.setattr(logging.getLogger("app.services.moze_import_service"), "disabled", False)
    with pytest.raises(RuntimeError):
        run_backup_import(pg_engine, _zip(tmp_path), "MOZE_4.0.zip", exporter=fake_exporter(_doc(backup)))

    [(_, status, _, summary)] = _runs(pg_engine)
    assert (status, summary) == ("failed", {"error_type": "RuntimeError", "error": "import failed; see server log"})
    assert "午餐 -120 leaked" in caplog.text  # the server log keeps the detail


def test_moze_import_errors_keep_their_message(pg_engine, db_session, backup, fake_exporter, tmp_path):
    with pytest.raises(MozeImportError):
        run_backup_import(pg_engine, _zip(tmp_path), "MOZE_4.0.zip", exporter=fake_exporter(_doc(backup, price=-120)),
                          renames={"不存在": "新"})
    [(_, status, _, summary)] = _runs(pg_engine)
    assert status == "failed" and summary == {"error": "rename 不存在=新: account '不存在' does not exist"}


def test_database_errors_hide_their_parameters(pg_engine):
    from app import database

    assert database.engine.hide_parameters is True
    with pytest.raises(DBAPIError) as exc, pg_engine.connect() as conn:
        conn.execute(text("INSERT INTO project (name) VALUES (:name)"), {"name": "機密" * 100})
    assert "機密" not in str(exc.value)


def test_keep_json_is_private_from_the_start_and_never_follows_a_symlink(pg_engine, db_session, backup, fake_exporter, tmp_path):
    kept = tmp_path / "kept.json"
    kept.write_text("old", encoding="utf-8")
    kept.chmod(0o644)
    run_backup_import(pg_engine, _zip(tmp_path), "MOZE_4.0.zip", dry_run=True, exporter=fake_exporter(_doc(backup)), keep_json=kept)
    assert kept.stat().st_mode & 0o777 == 0o600
    assert json.loads(kept.read_text(encoding="utf-8"))["exported_at"] == "2026-10-01T17:00:37"

    target = tmp_path / "elsewhere.json"
    target.write_text("untouched", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(MozeImportError, match="--keep-json destination is a symbolic link"):
        run_backup_import(pg_engine, _zip(tmp_path), "MOZE_4.0.zip", dry_run=True, exporter=fake_exporter(_doc(backup)), keep_json=link)
    assert target.read_text(encoding="utf-8") == "untouched"


def _runs(pg_engine):
    with pg_engine.connect() as conn:
        return conn.execute(text("SELECT kind, status, exported_at, summary FROM import_run ORDER BY id")).all()


def test_run_records_kind_and_export_date(pg_engine, db_session, backup, fake_exporter, tmp_path):
    report = run_backup_import(pg_engine, _zip(tmp_path), "MOZE_4.0.zip", exporter=fake_exporter(_doc(backup)))
    assert (report["kind"], report["status"], report["row_count"]) == ("moze_backup", "succeeded", 4)
    assert datetime.fromisoformat(report["exported_at"]) == datetime(2026, 10, 1, 17, 0, 37, tzinfo=TAIPEI)
    [run] = _runs(pg_engine)
    assert (run.kind, run.status, run.summary["kind_counts"]) == ("moze_backup", "succeeded", {"expense": 2, "receivable": 1})


def test_zip_without_database_writes_no_run(pg_engine, db_session, fake_exporter, tmp_path):
    with pytest.raises(ConverterError, match="moze.realm"):
        run_backup_import(pg_engine, _zip(tmp_path), "x.zip", exporter=fake_exporter(exit_code=1, message="archive has no moze.realm"))
    assert _runs(pg_engine) == []
    assert advisory_locks(pg_engine) == 0


def test_archive_over_200_mb_is_refused(pg_engine, db_session, tmp_path, fake_exporter):
    big = tmp_path / "big.zip"
    with big.open("wb") as handle:
        handle.truncate(200 * 1024 * 1024 + 1)
    with pytest.raises(MozeImportError, match="larger than 200 MB"):
        run_backup_import(pg_engine, big, "big.zip", exporter=fake_exporter({}))
    assert _runs(pg_engine) == []


def test_failed_import_keeps_the_previous_ledger(pg_engine, db_session, backup, fake_exporter, tmp_path):
    run_backup_import(pg_engine, _zip(tmp_path), "a.zip", exporter=fake_exporter(_doc(backup)))
    before = {k: v for k, v in _snapshot(pg_engine).items() if k != "import_run"}
    bad = _doc(backup, extra_records=[backup.record("R-BAD", type_=99)])

    with pytest.raises(MozeImportError, match="unknown record type 99"):
        run_backup_import(pg_engine, _zip(tmp_path), "b.zip", exporter=fake_exporter(bad))

    assert {k: v for k, v in _snapshot(pg_engine).items() if k != "import_run"} == before
    assert [r.status for r in _runs(pg_engine)] == ["succeeded", "failed"]
    assert "unknown record type 99" in _runs(pg_engine)[-1].summary["error"]


def test_dry_run_writes_nothing(pg_engine, db_session, backup, fake_exporter, tmp_path):
    before = _snapshot(pg_engine)
    report = run_backup_import(pg_engine, _zip(tmp_path), "a.zip", dry_run=True, exporter=fake_exporter(_doc(backup)))
    assert (report["status"], report["id"], report["kind"]) == ("dry_run", None, "moze_backup")
    assert report["summary"]["kind_counts"] == {"expense": 2, "receivable": 1}
    assert _snapshot(pg_engine) == before


def test_lock_and_locked_flag_refuse_before_writing(pg_engine, db_session, backup, fake_exporter, tmp_path, monkeypatch):
    with pg_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        holder.commit()
        with pytest.raises(ImportAlreadyRunningError, match="import already running"):
            run_backup_import(pg_engine, _zip(tmp_path), "a.zip", exporter=fake_exporter(_doc(backup)))
        holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
        holder.commit()
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    with pytest.raises(ImportLockedError, match="import is locked"):
        run_backup_import(pg_engine, _zip(tmp_path), "a.zip", exporter=fake_exporter(_doc(backup)))
    assert _runs(pg_engine) == []


def test_cli_dry_run_prints_report_warning_and_keeps_json(pg_engine, db_session, backup, fake_exporter, tmp_path, capsys):
    kept = tmp_path / "kept.json"
    code = main([str(_zip(tmp_path)), "--dry-run", "--keep-json", str(kept)], engine=pg_engine, exporter=fake_exporter(_doc(backup)))

    assert code == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "dry_run"
    assert "compared_accounts: 0 of 2" in captured.err
    assert "WARNING: 0 of 2 accounts compared" in captured.err
    assert json.loads(kept.read_text(encoding="utf-8"))["exported_at"] == "2026-10-01T17:00:37"
    assert kept.stat().st_mode & 0o777 == 0o600
    assert _runs(pg_engine) == []


def test_cli_real_run_with_options_and_failure_exit(pg_engine, db_session, backup, fake_exporter, tmp_path, capsys):
    db_session.add(Account(name="舊錢包", currency="TWD"))
    db_session.commit()
    zip_path = str(_zip(tmp_path))
    assert main([zip_path, "--rename", "舊錢包=錢包", "--no-strict", "--allow-fx-outliers"], engine=pg_engine,
                exporter=fake_exporter(_doc(backup))) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["accounts_renamed"] == [{"from": "舊錢包", "to": "錢包"}]

    assert main([zip_path], engine=pg_engine, exporter=fake_exporter(exit_code=3, message="boom")) == 1
    assert "import failed: MOZE realm exporter failed (exit 3): boom" in capsys.readouterr().err
