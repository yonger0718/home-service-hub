import hashlib
import json
from datetime import date

import pytest
from sqlalchemy import text

from app import auth
from tests.integration.test_statement_ingest_api import SCOPES, TOKENS
from tests.worker import fake_claude
from tests.worker.pdfgen import encrypt, make_pdf, statement_lines
from worker import config, pipeline
from worker.runner import FakeRunner, Result, SubprocessRunner

PW_FILE = "STATEMENT_ID_NUMBER=A123456789\nSTATEMENT_BIRTH_DATE=19900101\nSTATEMENT_HOLDER_NAMES=王小明\n信用卡/國泰世華=$ID\n"


@pytest.fixture(autouse=True)
def recon_env(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, TOKENS)
    monkeypatch.setenv(auth.SCOPES_ENV, SCOPES)
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


@pytest.fixture
def card(seed, db_session):
    account = seed.account("卡", is_credit=True, statement_live_from=date(2026, 9, 1))
    db_session.commit()
    return account


@pytest.fixture
def world(tmp_path, client, card):
    """Drive with one encrypted card PDF; the fake parser returns GOOD; the account map points at `card`."""
    pdf_bytes = encrypt(make_pdf([statement_lines()]), "A123456789")
    drive_files = {"id-1": ("信用卡/國泰世華/2026-09_國泰世華.pdf", pdf_bytes)}

    def rclone(args, stdin):
        if args[1] == "lsjson":
            if not args[-1].endswith("/銀行"):
                return Result(0, b"[]", b"")
            rows = [{"Path": p, "Name": p.split("/")[-1], "Size": len(b), "ModTime": "2026-10-01T00:00:00Z",
                     "Hashes": {"md5": hashlib.md5(b).hexdigest()}, "ID": fid, "MimeType": "application/pdf"}
                    for fid, (p, b) in drive_files.items()]
            return Result(0, json.dumps(rows).encode(), b"")
        if args[1:3] == ["backend", "copyid"]:
            from pathlib import Path
            Path(args[5]).write_bytes(drive_files[args[4]][1])
            return Result(0, b"", b"")
        raise AssertionError(args)

    pw = tmp_path / "pw.env"
    pw.write_text(PW_FILE, encoding="utf-8")
    token = tmp_path / "token"
    token.write_text("worker-token")
    cli = fake_claude.write(tmp_path / "claude", fake_claude.transcript())
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path / "state"), "STATEMENT_PASSWORD_FILE": str(pw),
                       "STATEMENT_API_TOKEN_FILE": str(token), "STATEMENT_PARSER_CLI": str(cli),
                       "STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_PARSER_ALLOW_UNSANDBOXED": "true", "STATEMENT_PARSER_TIMEOUT": "5",
                       "STATEMENT_PARSER_ATTEMPTS": "1"})
    client.put("/settings/reconciliation", headers={"Authorization": "Bearer spa-token"},
               json={"account_map": {"mail/信用卡/國泰世華": card.id}, "dirty_enabled": False, "rules": {}})

    class Runner:  # rclone through the fake, claude through a real subprocess (the fake script)
        def __init__(self):
            self.fake, self.real = FakeRunner({"rclone": rclone}), SubprocessRunner()
            self.downloads = 0

        def run(self, args, **kw):
            if args[0] == "rclone" and args[1:3] == ["backend", "copyid"]:
                self.downloads += 1
            return self.fake.run(args, **kw) if args[0] == "rclone" else self.real.run(args, **kw)

    runner = Runner()
    services = pipeline.build(cfg, client, runner)
    return services, drive_files, cfg


def _operator_gate(services):
    """The operator gate needs a verify parser to clear the latch; reuse the fake CLI for it."""
    return services.gate.run_operator_gate(services.parser, verify_parser=services.parser)


def _counts(db_session):
    return {t: db_session.execute(text(f"select count(*) from {t}")).scalar_one()
            for t in ("statement_file", "statement_source", "account_statement", "statement_revision", "ingest_run")}


def _file_rows(db_session):
    return db_session.execute(text("select status, failure from statement_file order by id")).all()


def test_end_to_end_parses_and_is_idempotent(world, db_session, client):
    services, _, cfg = world
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["listed"] == 1 and summary["new_files"] == 1 and summary["parsed"] == 1 and summary["errors"] == []
    assert _counts(db_session) == {"statement_file": 1, "statement_source": 1, "account_statement": 1,
                                   "statement_revision": 1, "ingest_run": 1}
    row = db_session.execute(text("select status, failure, has_text_layer, credential_version from statement_file")).one()
    assert row[0] == "parsed" and row[1] is None and row[2] is True and len(row[3]) == 64
    assert list(cfg.inbox_dir.iterdir())[0].suffix == ".pdf"
    seen_before = db_session.execute(text("select last_seen_at from statement_source")).scalar_one()
    second = pipeline.run(services, trigger="owner_cli")
    assert second["new_files"] == 0 and second["parsed"] == 0 and second["unchanged"] == 1 and second["skipped"] == 0
    assert services.runner.downloads == 1  # the second run did not download again (counted by the test Runner)
    assert _counts(db_session)["statement_revision"] == 1  # unchanged, successful file is a no-op
    run_status = db_session.execute(text("select status, summary from ingest_run order by id")).all()
    assert [r[0] for r in run_status] == ["done", "done"]
    assert run_status[0][1]["versions"]["parser_version"].startswith("claude-cli-2.1.296-")
    assert "sweep" in run_status[0][1] and (cfg.state_dir / "gate.json").exists()
    assert not (cfg.state_dir / "run.json").exists()
    db_session.expire_all()
    sources = db_session.execute(text("select last_seen_at from statement_source")).scalars().all()
    assert len(sources) == 1 and sources[0] > seen_before  # re-registered every listing (ruling 10): last_seen_at moved on the second run


def test_rename_in_drive_updates_path_history_without_a_download(world, db_session):
    services, drive_files, cfg = world
    pipeline.run(services, trigger="owner_cli")
    path, data = drive_files["id-1"]
    drive_files["id-1"] = (path.replace("2026-09_", "2026-09_renamed_"), data)
    second = pipeline.run(services, trigger="owner_cli")
    assert services.runner.downloads == 1
    hist = db_session.execute(text("select drive_path, path_history from statement_source")).one()
    assert "renamed" in hist[0] and hist[1] and hist[1][0]["path"] == path


def test_wrong_password_then_credential_change_retries(world, db_session, tmp_path):
    services, _, cfg = world
    cfg.password_file.write_text(PW_FILE.replace("$ID", "nope"), encoding="utf-8")
    services = pipeline.build(cfg, services.api.client, services.runner)
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["failed"] == 1
    row = db_session.execute(text("select status, failure, credential_version from statement_file")).one()
    assert row[:2] == ("failed", "password")
    assert pipeline.run(services, trigger="owner_cli")["unchanged"] == 1  # same credential version: not retried
    cfg.password_file.write_text(PW_FILE, encoding="utf-8")
    services = pipeline.build(cfg, services.api.client, services.runner)
    assert pipeline.run(services, trigger="owner_cli")["parsed"] == 1


def test_unmapped_folder_is_ignored_and_remapping_requeues(world, db_session, client, card):
    services, drive_files, cfg = world
    client.put("/settings/reconciliation", headers={"Authorization": "Bearer spa-token"},
               json={"account_map": {}, "dirty_enabled": False, "rules": {}})
    assert pipeline.run(services, trigger="owner_cli")["ignored"] == 1
    assert db_session.execute(text("select failure from statement_file")).scalar_one() == "mapping"
    client.put("/settings/reconciliation", headers={"Authorization": "Bearer spa-token"},
               json={"account_map": {"mail/信用卡/國泰世華": card.id}, "dirty_enabled": False, "rules": {}})
    assert pipeline.run(services, trigger="owner_cli")["parsed"] == 1


def test_sandbox_violation_latches_and_fails_the_run(world, db_session, tmp_path):
    services, drive_files, cfg = world
    services.gate.ensure(services.parser)  # a valid cached gate, so the violation happens on real input
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(extra_tool=True))
    drive_files["id-2"] = ("信用卡/國泰世華/2026-08_國泰世華.pdf", encrypt(make_pdf([statement_lines(30)]), "A123456789"))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["failed"] == 1 and summary["skipped"] == 1 and "parser_disabled" in summary["errors"]
    assert sorted(_file_rows(db_session)) == [("failed", "sandbox"), ("new", None)]
    assert db_session.execute(text("select status from ingest_run")).scalar_one() == "failed"
    assert (cfg.state_dir / "parser-disabled.json").exists()
    fake_claude.write(tmp_path / "claude", fake_claude.transcript())
    again = pipeline.run(services, trigger="owner_cli")  # still latched: nothing parsed, run failed
    assert again["parsed"] == 0 and "parser_disabled" in again["errors"]
    assert _operator_gate(services).ok
    assert pipeline.run(services, trigger="owner_cli")["parsed"] >= 1


def test_gate_runs_before_the_first_parse(world, db_session, tmp_path):
    services, _, cfg = world
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(extra_tool=True))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["parsed"] == 0 and summary["skipped"] == 1 and "parser_disabled" in summary["errors"]
    assert _file_rows(db_session) == [("new", None)]  # acquired and registered, never parsed


def _shift(monkeypatch, hours):
    import datetime as dt
    monkeypatch.setattr(pipeline, "_now", lambda: dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours))


def test_store_pending_is_retried_independently_of_parse(world, db_session, monkeypatch):
    services, _, cfg = world
    calls = {"put": 0, "exists": 0}

    class FlakyStore:
        def exists(self, key):
            calls["exists"] += 1
            if calls["exists"] == 2:
                raise ConnectionError("stat failed")  # transport failure on stat is guarded too
            return calls["put"] > 1

        def put(self, key, path):
            calls["put"] += 1
            if calls["put"] == 1:
                raise RuntimeError("minio down")

    services.store = FlakyStore()
    first = pipeline.run(services, trigger="owner_cli")
    assert first["transient"] == 1 and _file_rows(db_session) == [("failed", "transient")]
    sha = db_session.execute(text("select sha256 from statement_file")).scalar_one()
    assert services.retries.get(sha)["store"]["attempts"] == 1 and services.retries.get(sha)["transient_attempts"] == 0
    immediate = pipeline.run(services, trigger="owner_cli")  # not due yet: no second put, no ladder bump
    assert calls["put"] == 1 and immediate["deferred"] == 1 and services.retries.get(sha)["store"]["attempts"] == 1
    _shift(monkeypatch, 2)
    third = pipeline.run(services, trigger="owner_cli")  # due: stat raises → attempt 2, still not parsed
    assert calls["exists"] == 2 and services.retries.get(sha)["store"]["attempts"] == 2 and third["parsed"] == 0
    _shift(monkeypatch, 9)
    fourth = pipeline.run(services, trigger="owner_cli")  # due again: put succeeds, parse proceeds
    assert calls["put"] == 2 and fourth["parsed"] == 1 and services.runner.downloads == 1


def test_enabling_minio_later_uploads_but_never_touches_a_parsed_file(world, db_session):
    import dataclasses
    services, _, cfg = world
    pipeline.run(services, trigger="owner_cli")  # NullStore confirms "null": parsed
    puts = []

    class Minio:
        def exists(self, key):
            return False

        def put(self, key, path):
            puts.append(key)

    services.cfg = dataclasses.replace(cfg, minio_endpoint="minio:9000")  # MinIO configured later
    services.store = Minio()
    summary = pipeline.run(services, trigger="owner_cli")
    assert len(puts) == 1 and _file_rows(db_session) == [("parsed", None)] and summary["unchanged"] == 1
    pipeline.run(services, trigger="owner_cli")
    assert len(puts) == 1  # confirmed by this endpoint now

    class Down(Minio):
        def put(self, key, path):
            raise RuntimeError("down")

    services.cfg = dataclasses.replace(cfg, minio_endpoint="other:9000")
    services.store = Down()
    summary = pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [("parsed", None)] and "store" in summary["errors"]


def test_store_ladder_stops_after_five_attempts(world, db_session, monkeypatch):
    services, _, cfg = world
    calls = []

    class Down:
        def exists(self, key):
            calls.append(1)
            raise ConnectionError("down")

    services.store = Down()
    for hours in (0, 2, 9, 40, 80, 200):
        _shift(monkeypatch, hours)
        pipeline.run(services, trigger="owner_cli")
    sha = db_session.execute(text("select sha256 from statement_file")).scalar_one()
    assert services.retries.get(sha)["store"]["attempts"] == 5 and len(calls) == 5  # the sixth run did not try


def test_corrupt_retry_log_loads_empty(world, tmp_path):
    services, _, cfg = world
    (cfg.state_dir / "retries.json").write_text("{not json")
    assert pipeline.build(cfg, services.api.client, services.runner).retries.data == {}
    (cfg.state_dir / "retries.json").write_text("[1]")
    assert pipeline.RetryLog(cfg.state_dir / "retries.json").data == {}


def test_a_failing_file_is_contained(world, db_session, monkeypatch):
    from worker.api import ApiError
    services, drive_files, _ = world
    drive_files["id-2"] = ("信用卡/國泰世華/2026-08_國泰世華.pdf", encrypt(make_pdf([statement_lines(30)]), "A123456789"))
    real = services.api.submit_revision
    calls = []

    def boom(lease, body):
        calls.append(1)
        if len(calls) == 1:
            raise ApiError(500, "x")
        return real(lease, body)

    monkeypatch.setattr(services.api, "submit_revision", boom)
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["errors"] == ["file_error"] and summary["parsed"] == 1 and summary["transient"] == 1
    assert sorted(_file_rows(db_session)) == [("failed", "transient"), ("parsed", None)]
    assert db_session.execute(text("select status from ingest_run")).scalar_one() == "done"


def test_cli_exit_is_transient(world, db_session, tmp_path):
    services, _, cfg = world
    services.gate.ensure(services.parser)
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(), exit_code=1)
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["transient"] == 1 and summary["failed"] == 0
    assert db_session.execute(text("select status, failure, next_retry_at is not null from statement_file")).one() \
        == ("failed", "transient", True)


def test_resume_unavailable_keeps_run_json(world, monkeypatch):
    from worker.api import ApiError
    services, _, cfg = world
    real_list = services.drive.list_pdfs
    services.drive.list_pdfs = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        pipeline.run(services, trigger="owner_cli")
    services.drive.list_pdfs = real_list
    monkeypatch.setattr(services.api, "renew", lambda *a, **k: (_ for _ in ()).throw(ApiError(503, "down")))
    with pytest.raises(pipeline.RunAborted, match="resume_unavailable"):
        pipeline.run(services, trigger="owner_cli")
    assert (cfg.state_dir / "run.json").exists()


def test_cli_missing_token_file_exits_2(world, monkeypatch, capsys):
    import dataclasses
    from worker import cli
    _, _, cfg = world
    monkeypatch.setattr(config, "load", lambda *a, **k: dataclasses.replace(cfg, api_token_file=cfg.state_dir / "nope"))
    assert cli.main(["run"]) == 2
    assert "token" in capsys.readouterr().err


def test_transient_backoff_ladders(world, db_session, monkeypatch):
    """Acquisition failures: identity-keyed 1h/6h/24h backoff; storage failures: per-sha ladder, max 5."""
    services, drive_files, cfg = world
    from worker import drive as drive_mod
    import hashlib
    ident = "acquire:id-1:" + hashlib.md5(drive_files["id-1"][1]).hexdigest()
    real_download = services.drive.download
    services.drive.download = lambda item, dest: (_ for _ in ()).throw(drive_mod.DownloadError("x"))
    pipeline.run(services, trigger="owner_cli")
    assert services.retries.get(ident)["attempts"] == 1 and pipeline.run(services, trigger="owner_cli")["deferred"] == 1
    _shift(monkeypatch, 2)
    pipeline.run(services, trigger="owner_cli")
    assert services.retries.get(ident)["attempts"] == 2  # 1h step passed, second failure
    _shift(monkeypatch, 9)
    services.drive.download = real_download
    pipeline.run(services, trigger="owner_cli")  # 6h step passed: success resets the identity ladder
    assert services.retries.get(ident)["attempts"] == 0 and _file_rows(db_session) == [("parsed", None)]


def test_lease_lost_during_parse_blocks_the_submission(world, db_session, monkeypatch):
    services, _, cfg = world
    original = services.parser.parse
    sent = []

    def parse_then_lose(text_):
        out = original(text_)
        services.keeper.lost = True  # the renew thread reported a loss while the parse was in flight
        return out

    monkeypatch.setattr(services.parser, "parse", parse_then_lose)
    real_request = services.api.client.request

    def spy(method, url, **kw):  # the transport boundary: a blocked call never reaches here
        if str(url).endswith("/statements/revisions"):
            sent.append(1)
        return real_request(method, url, **kw)

    monkeypatch.setattr(services.api.client, "request", spy)
    with pytest.raises(pipeline.RunAborted):
        pipeline.run(services, trigger="owner_cli")
    assert sent == [] and db_session.execute(text("select count(*) from statement_revision")).scalar_one() == 0
    assert db_session.execute(text("select status from statement_file")).scalar_one() != "parsed"


def test_crash_after_claim_resumes_the_same_run(world, db_session, monkeypatch):
    services, _, cfg = world
    real_list = services.drive.list_pdfs  # not monkeypatch.undo(): that would also revert the autouse env fixture
    services.drive.list_pdfs = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        pipeline.run(services, trigger="owner_cli")
    assert (cfg.state_dir / "run.json").exists()
    services.drive.list_pdfs = real_list
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["resumed"] is True and summary["parsed"] == 1
    assert db_session.execute(text("select count(*) from ingest_run")).scalar_one() == 1
    assert not (cfg.state_dir / "run.json").exists()


def test_expired_lease_is_reclaimed_on_restart(world, db_session, monkeypatch):
    services, _, cfg = world
    real_list = services.drive.list_pdfs  # not monkeypatch.undo(): that would also revert the autouse env fixture
    services.drive.list_pdfs = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        pipeline.run(services, trigger="owner_cli")
    services.drive.list_pdfs = real_list
    db_session.execute(text("update ingest_run set lease_expires_at = now() - interval '1 hour'"))
    db_session.commit()
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["resumed"] is True and summary["parsed"] == 1
    assert db_session.execute(text("select count(*), max(attempt) from ingest_run")).one() == (1, 2)


def test_poll_resumes_its_own_crashed_run_first(world, db_session, monkeypatch, client):
    services, _, cfg = world
    client.post("/statements/ingest/run", headers={"Authorization": "Bearer hermes-token"})
    real_list = services.drive.list_pdfs  # not monkeypatch.undo(): that would also revert the autouse env fixture
    services.drive.list_pdfs = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        pipeline.poll(services, initiator_hint="t")
    assert (cfg.state_dir / "run.json").exists()
    services.drive.list_pdfs = real_list
    summary = pipeline.poll(services, initiator_hint="t")
    assert summary["resumed"] is True and summary["claimed"] == 1 and summary["parsed"] == 1
    assert db_session.execute(text("select count(*) from ingest_run")).scalar_one() == 1


def test_sandbox_recovery_after_an_operator_gate_at_the_same_version(world, db_session, tmp_path):
    services, _, cfg = world
    services.gate.ensure(services.parser)
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(extra_tool=True))
    pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [("failed", "sandbox")]
    fake_claude.write(tmp_path / "claude", fake_claude.transcript())
    assert _operator_gate(services).ok
    summary = pipeline.run(services, trigger="owner_cli")  # same parser_version, newer gate → due again
    assert summary["parsed"] == 1 and _file_rows(db_session) == [("parsed", None)]


def test_backfill_needs_acknowledgement(world):
    services, _, _ = world
    with pytest.raises(pipeline.Refused):
        pipeline.run(services, trigger="owner_cli", mode="backfill")


def test_singleton(world, tmp_path):
    services, _, cfg = world
    with pipeline.singleton(cfg):
        with pytest.raises(pipeline.AlreadyRunning):
            with pipeline.singleton(cfg):
                pass
    assert oct(cfg.state_dir.stat().st_mode)[-3:] == "700"


def test_summary_carries_codes_not_text(world, monkeypatch):
    services, _, cfg = world
    from worker import drive as drive_mod
    monkeypatch.setattr(services.drive, "list_pdfs", lambda: (_ for _ in ()).throw(drive_mod.ListingError("rclone lsjson exit 3: /secret/path")))
    with pytest.raises(pipeline.RunAborted):
        pipeline.run(services, trigger="owner_cli")
    summary = services.api.client.get("/statements/ingest-runs", headers={"Authorization": "Bearer worker-token"}).json()[-1]["summary"]
    assert summary["errors"] == ["listing"] and "/secret" not in json.dumps(summary)


def test_md5_mismatch_is_transient(world, db_session):
    services, drive_files, _ = world
    path, data = drive_files["id-1"]
    real = services.drive.download

    def corrupt(item, dest):
        real(item, dest)
        dest.write_bytes(data + b"x")

    services.drive.download = corrupt
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["transient"] == 1 and "download" in summary["errors"]
    assert _counts(db_session)["statement_file"] == 0  # refused before registration


def test_backfill_cli_refused_without_acknowledgement(world, db_session, monkeypatch, capsys):
    services, _, cfg = world
    from worker import cli
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    assert cli.main(["backfill"]) == 2
    assert "acknowledge-live-periods" in capsys.readouterr().err
    assert _counts(db_session)["ingest_run"] == 0


def test_retry_due_rules():
    import datetime as dt
    now = dt.datetime(2026, 10, 10, tzinfo=dt.timezone.utc)
    row = {"status": "failed", "failure": "transient", "next_retry_at": (now + dt.timedelta(minutes=1)).isoformat()}
    args = ("cred", "map", "pv")
    assert not pipeline.retry_due(row, {"transient_attempts": 1}, now, *args)
    row["next_retry_at"] = now.isoformat()
    assert pipeline.retry_due(row, {"transient_attempts": 4}, now, *args)
    assert not pipeline.retry_due(row, {"transient_attempts": 5}, now, *args)  # max-5 boundary
    parse = {"status": "failed", "failure": "parse", "parser_version": "pv"}
    assert not pipeline.retry_due(parse, {}, now, *args) and pipeline.retry_due(parse, {}, now, "cred", "map", "pv2")
    sandbox = {"status": "failed", "failure": "sandbox", "parser_version": "pv"}
    retry = {"sandbox_failed_at": now.isoformat()}
    assert not pipeline.retry_due(sandbox, retry, now, *args, gate_ok_at=now - dt.timedelta(hours=1))
    assert pipeline.retry_due(sandbox, retry, now, *args, gate_ok_at=now + dt.timedelta(hours=1))
    assert not pipeline.retry_due(sandbox, retry, now, *args, latched=True, gate_ok_at=now + dt.timedelta(hours=1))


def test_transient_backoff_steps():
    import datetime as dt
    assert [pipeline.BACKOFF.get(n, dt.timedelta(hours=24)) for n in (1, 2, 3, 5)] == [
        dt.timedelta(hours=1), dt.timedelta(hours=6), dt.timedelta(hours=24), dt.timedelta(hours=24)]
