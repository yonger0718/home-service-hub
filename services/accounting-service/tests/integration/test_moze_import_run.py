import json

import pytest
from sqlalchemy import select, text

from app.models import Account
from app.services.moze_csv import MozeImportError
from app.services.moze_import_service import (
    IMPORT_LOCK_KEY,
    ImportAlreadyRunningError,
    ImportLockedError,
    main,
    run_import,
)
from tests.helpers import advisory_locks, wait_until_unlocked


def _good(moze, amount="-120") -> bytes:
    return moze.csv(
        moze.opening("錢包", "TWD", "2000"),
        moze.row("錢包", "TWD", "支出", amount, main="飲食", sub="午餐"),
        moze.row("錢包", "TWD", "支出", "-30", main="交通"),
    )


def _snapshot(pg_engine) -> dict:
    with pg_engine.connect() as conn:
        return {
            "accounts": conn.execute(text("SELECT id, name, currency, opening_balance, is_archived FROM account ORDER BY id")).all(),
            "entries": conn.execute(text("SELECT id, account_id, kind, amount, seq FROM ledger_entry ORDER BY id")).all(),
            "categories": conn.execute(text("SELECT id, kind, parent_id, name FROM category ORDER BY id")).all(),
            "projects": conn.execute(text("SELECT id, name FROM project ORDER BY id")).all(),
        }


def _runs(pg_engine) -> list:
    with pg_engine.connect() as conn:
        return conn.execute(text("SELECT id, status, row_count, summary FROM import_run ORDER BY id")).all()


def test_successful_import_records_succeeded_run(pg_engine, db_session, moze):
    report = run_import(pg_engine, _good(moze), "moze.csv")

    assert report["status"] == "succeeded"
    assert report["file_name"] == "moze.csv"
    assert len(report["file_sha256"]) == 64
    assert report["row_count"] == 3
    assert report["summary"]["accounts"][0]["balance"] == "1850.0000"
    [run] = _runs(pg_engine)
    assert (run.status, run.row_count) == ("succeeded", 3)


def test_failure_mid_file_leaves_previous_import_and_records_failed_run(pg_engine, db_session, moze):
    run_import(pg_engine, _good(moze), "first.csv")
    before = _snapshot(pg_engine)
    bad = moze.csv(
        moze.opening("錢包", "TWD", "0"),
        moze.row("錢包", "TWD", "支出", "-1", main="飲食"),
        moze.row("錢包", "TWD", "支出", "-1", main="飲食", date="2026-13-01"),
    )

    with pytest.raises(MozeImportError, match="row 4"):
        run_import(pg_engine, bad, "bad.csv")

    assert _snapshot(pg_engine) == before
    last = _runs(pg_engine)[-1]
    assert last.status == "failed"
    assert "row 4" in last.summary["error"]


def test_rejected_header_writes_no_ledger_data(pg_engine, db_session, moze):
    run_import(pg_engine, _good(moze), "first.csv")
    before = _snapshot(pg_engine)

    with pytest.raises(MozeImportError, match="missing columns: 對象"):
        run_import(pg_engine, moze.csv(header=moze.header.replace(",對象", "")), "old-format.csv")

    assert _snapshot(pg_engine) == before


def test_dry_run_reports_without_writing_anything(pg_engine, db_session, moze):
    run_import(pg_engine, moze.csv(moze.opening("A", "TWD", "0")), "first.csv")
    before = _snapshot(pg_engine)
    runs_before = _runs(pg_engine)

    report = run_import(pg_engine, moze.csv(moze.opening("B", "TWD", "0")), "next.csv", dry_run=True)

    assert report["status"] == "dry_run"
    assert report["id"] is None
    assert report["summary"]["accounts_created"] == ["B"]
    assert report["summary"]["accounts_archived"] == ["A"]
    assert _snapshot(pg_engine) == before
    assert _runs(pg_engine) == runs_before


def test_second_import_is_refused_while_lock_is_held(pg_engine, db_session, moze):
    with pg_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        holder.commit()
        assert advisory_locks(pg_engine) == 1
        for dry_run in (False, True):
            with pytest.raises(ImportAlreadyRunningError, match="import already running"):
                run_import(pg_engine, _good(moze), "moze.csv", dry_run=dry_run)
        holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
        holder.commit()

    assert _runs(pg_engine) == []
    assert _snapshot(pg_engine)["entries"] == []


def test_lock_is_released_after_a_failed_import(pg_engine, db_session, moze):
    # Review focus: a leaked advisory lock would refuse every later import.
    with pytest.raises(MozeImportError):
        run_import(pg_engine, moze.csv(moze.row("錢包", "TWD", "支出", "-1")), "bad.csv")

    assert advisory_locks(pg_engine) == 0
    assert run_import(pg_engine, _good(moze), "good.csv")["status"] == "succeeded"
    assert advisory_locks(pg_engine) == 0


def test_interrupted_import_is_marked_failed_and_leaves_no_trace(pg_engine, db_session, moze):
    crashed = pg_engine.connect()
    crashed.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
    crashed.execute(
        text("INSERT INTO import_run (started_at, file_name, file_sha256, status) VALUES (now(), 'killed.csv', repeat('0', 64), 'running')")
    )
    crashed.commit()
    crashed.execute(text("INSERT INTO account (name, currency) VALUES ('ghost', 'TWD')"))
    crashed.invalidate()  # the process dies: no commit, the server drops the session and its lock
    crashed.close()
    wait_until_unlocked(pg_engine)

    run_import(pg_engine, _good(moze), "next.csv")

    runs = _runs(pg_engine)
    assert (runs[0].status, runs[0].summary) == ("failed", {"error": "interrupted"})
    assert runs[1].status == "succeeded"
    assert db_session.scalar(select(Account).where(Account.name == "ghost")) is None


def test_dry_run_does_not_touch_stale_running_rows(pg_engine, db_session, moze):
    with pg_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO import_run (started_at, file_name, file_sha256, status) VALUES (now(), 'killed.csv', repeat('0', 64), 'running')")
        )

    run_import(pg_engine, _good(moze), "check.csv", dry_run=True)

    assert [r.status for r in _runs(pg_engine)] == ["running"]


def test_import_locked_flag_refuses_every_path(pg_engine, db_session, moze, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    for dry_run in (False, True):
        with pytest.raises(ImportLockedError, match="import is locked"):
            run_import(pg_engine, _good(moze), "moze.csv", dry_run=dry_run)
    assert _runs(pg_engine) == []
    assert _snapshot(pg_engine)["accounts"] == []


def test_cli_dry_run_prints_report_and_writes_nothing(pg_engine, db_session, moze, tmp_path, capsys):
    csv_path = tmp_path / "moze.csv"
    csv_path.write_bytes(_good(moze))

    assert main([str(csv_path), "--dry-run"], engine=pg_engine) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "dry_run"
    assert report["summary"]["kind_counts"] == {"expense": 2}
    assert _runs(pg_engine) == []
    assert _snapshot(pg_engine)["accounts"] == []


def test_cli_import_with_rename(pg_engine, db_session, moze, tmp_path):
    first, second = tmp_path / "a.csv", tmp_path / "b.csv"
    first.write_bytes(moze.csv(moze.opening("A", "TWD", "1000")))
    second.write_bytes(moze.csv(moze.opening("B", "TWD", "1000")))
    assert main([str(first)], engine=pg_engine) == 0

    assert main([str(second), "--rename", "A=B"], engine=pg_engine) == 0

    assert list(db_session.scalars(select(Account.name))) == ["B"]


def test_cli_refused_while_lock_held_exits_non_zero(pg_engine, db_session, moze, tmp_path, capsys):
    csv_path = tmp_path / "moze.csv"
    csv_path.write_bytes(_good(moze))
    with pg_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        holder.commit()
        code = main([str(csv_path)], engine=pg_engine)
        holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
        holder.commit()

    assert code == 1
    assert "import already running" in capsys.readouterr().err
    assert _runs(pg_engine) == []


def test_cli_failed_import_exits_non_zero_with_message(pg_engine, db_session, moze, tmp_path, capsys):
    csv_path = tmp_path / "moze.csv"
    csv_path.write_bytes(moze.csv(moze.row("Line Bank", "TWD", "支出", "-1")))

    assert main([str(csv_path)], engine=pg_engine) == 1
    assert "import failed: account 'Line Bank' has no 初始金額 row" in capsys.readouterr().err


def _foreign(moze, day="2026/07/10") -> bytes:
    return moze.csv(
        moze.opening("華航卡", "TWD", "0"),
        moze.row("華航卡", "JPY", "支出", "-1800", main="飲食", date=day),
    )


JPY_2026_07_10 = {"currency-api@2026-07-10/v1/currencies/jpy.json": (200, {"date": "2026-07-10", "jpy": {"twd": 0.2}})}


def test_foreign_rows_use_fetched_rates_and_the_cache_on_reimport(pg_engine, db_session, moze, fake_http):
    first = fake_http(JPY_2026_07_10)
    report = run_import(pg_engine, _foreign(moze), "moze.csv", http_get=first)

    assert report["summary"]["accounts"][0]["balance"] == "-360.0000"
    assert len(first.calls) == 1
    second = fake_http({})
    assert run_import(pg_engine, _foreign(moze), "moze.csv", http_get=second)["status"] == "succeeded"
    assert second.calls == []


def test_unobtainable_rate_fails_before_touching_the_ledger(pg_engine, db_session, moze, fake_http):
    run_import(pg_engine, _good(moze), "first.csv")
    before = _snapshot(pg_engine)

    with pytest.raises(MozeImportError, match="no FX rate for JPY→TWD on 2025-06-01"):
        run_import(pg_engine, _foreign(moze, day="2025/06/01"), "foreign.csv", http_get=fake_http({}))

    assert _snapshot(pg_engine) == before
    last = _runs(pg_engine)[-1]
    assert last.status == "failed"
    assert "JPY→TWD on 2025-06-01" in last.summary["error"]


def test_dry_run_fetches_rates_without_caching_them(pg_engine, db_session, moze, fake_http):
    report = run_import(pg_engine, _foreign(moze), "moze.csv", dry_run=True, http_get=fake_http(JPY_2026_07_10))

    assert report["summary"]["accounts"][0]["converted_entry_count"] == 1
    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM fx_rate")).scalar_one() == 0


def test_unexpected_csv_failure_stores_only_the_error_type(pg_engine, db_session, moze, monkeypatch):
    from app.services import moze_import_service

    def explode(*args, **kwargs):
        raise RuntimeError("row 午餐 -120 leaked")

    monkeypatch.setattr(moze_import_service, "_replace_from_csv", explode)
    with pytest.raises(RuntimeError):
        run_import(pg_engine, _good(moze), "moze.csv")

    [run] = _runs(pg_engine)
    assert (run.status, run.summary) == ("failed", {"error_type": "RuntimeError", "error": "import failed; see server log"})
