import dataclasses
import hashlib
import json
from datetime import date

import pytest
from sqlalchemy import text

from app import auth
from tests.integration.test_statement_ingest_api import SCOPES, TOKENS
from tests.worker import fake_claude, fake_ocr_child
from tests.worker.composite_fixture import DECLARED, HOLDINGS, statement as composite_statement
from tests.worker.pdfgen import encrypt, make_pdf, statement_lines
from worker import config, parse_schema, pdf, pipeline
from worker import parser as parser_mod
from worker.api import ApiError
from worker.runner import FakeRunner, Result, SubprocessRunner

PW_FILE = "STATEMENT_ID_NUMBER=A123456789\nSTATEMENT_BIRTH_DATE=19900101\nSTATEMENT_HOLDER_NAMES=王小明,WANG XIAO MING\n信用卡/國泰世華=$ID\n"


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
            # the two roots partition drive_files (AGENT-159 Must 2): mail holds 信用卡/ and 銀行帳戶/, manual the rest
            mail = args[-1].endswith("/" + cfg.mail_root.rsplit("/", 1)[-1])
            if not mail and not args[-1].endswith("/" + cfg.manual_root.rsplit("/", 1)[-1]):
                return Result(0, b"[]", b"")
            rows = [{"Path": p, "Name": p.split("/")[-1], "Size": len(b), "ModTime": "2026-10-01T00:00:00Z",
                     "Hashes": {"md5": hashlib.md5(b).hexdigest()}, "ID": fid, "MimeType": "application/pdf"}
                    for fid, (p, b) in drive_files.items() if p.startswith(("信用卡/", "銀行帳戶/")) == mail]
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

        def start(self, args):
            if args[1:3] == ["backend", "copyid"]:
                self.downloads += 1
            return self.fake.start(args)

        def run(self, args, **kw):
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
    assert "sweep" in run_status[0][1] and services.gate.evidence_path_for(services.gate.key(services.parser)).exists()
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


def test_containment_leaves_a_settled_row_alone(world, db_session, monkeypatch):
    from worker.api import ApiError
    services, _, cfg = world
    cfg.password_file.write_text(PW_FILE.replace("$ID", "nope"), encoding="utf-8")
    services = pipeline.build(cfg, services.api.client, services.runner)
    pipeline.run(services, trigger="owner_cli")  # failed/password
    monkeypatch.setattr(services.api, "register_source", lambda *a, **k: (_ for _ in ()).throw(ApiError(503, "x")))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["errors"] == ["file_error"] and _file_rows(db_session) == [("failed", "password")]


def test_auth_failure_aborts_the_run(world, db_session, monkeypatch):
    from worker.api import ApiError
    services, _, _ = world
    monkeypatch.setattr(services.api, "submit_revision", lambda *a, **k: (_ for _ in ()).throw(ApiError(403, "no")))
    with pytest.raises(pipeline.RunAborted):
        pipeline.run(services, trigger="owner_cli")
    summary = services.api.client.get("/statements/ingest-runs", headers={"Authorization": "Bearer worker-token"}).json()[-1]["summary"]
    assert "auth" in summary["errors"] and "file_error" not in summary["errors"]


def test_epoch_moves_only_after_the_api_accepted_it(world, monkeypatch):
    from worker.api import ApiError
    services, _, _ = world
    row = {"id": 1, "sha256": "ab" * 32}
    monkeypatch.setattr(services.api, "update_file", lambda *a, **k: (_ for _ in ()).throw(ApiError(500, "x")))
    with pytest.raises(ApiError):
        pipeline._fail(services, None, row, "transient", pipeline.Summary())
    assert services.retries.get(row["sha256"])["transient_attempts"] == 0


def test_store_id_normalises_endpoint_and_includes_bucket(world):
    import dataclasses
    _, _, cfg = world
    ids = [pipeline.store_id_for(dataclasses.replace(cfg, minio_endpoint=e, minio_bucket=b))
           for e, b in (("http://minio:9000", "b"), ("minio:9000", "b"), ("minio:9000", "c"))]
    assert ids[0] == ids[1] == "minio:9000/b" and ids[2] != ids[0]
    assert pipeline.store_id_for(cfg) == "null"


def test_transient_canary_failure_skips_parsing_without_latching(world, db_session, tmp_path):
    services, _, cfg = world
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(), exit_code=1)
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["errors"] == ["gate_unavailable"] and summary["parsed"] == 0 and summary["skipped"] == 1
    assert db_session.execute(text("select status from ingest_run")).scalar_one() == "done"
    assert not (cfg.state_dir / "parser-disabled.json").exists()
    fake_claude.write(tmp_path / "claude", fake_claude.transcript())
    assert pipeline.run(services, trigger="owner_cli")["parsed"] == 1  # recovers on its own


def test_drive_deletion_marks_removed_and_reappearance_clears_it(world, db_session):
    services, drive_files, _ = world
    pipeline.run(services, trigger="owner_cli")
    entry = drive_files.pop("id-1")
    pipeline.run(services, trigger="owner_cli")
    db_session.expire_all()
    assert db_session.execute(text("select removed_at is not null from statement_source")).scalar_one() is True
    drive_files["id-1"] = entry
    pipeline.run(services, trigger="owner_cli")
    db_session.expire_all()
    assert db_session.execute(text("select removed_at is null from statement_source")).scalar_one() is True
    assert services.runner.downloads == 1


def test_stale_staging_files_are_swept_at_run_start(world):
    services, _, cfg = world
    from worker import inbox
    inbox.private_dir(cfg.staging_dir)
    (cfg.staging_dir / "old.tmp").write_bytes(b"x")
    pipeline.run(services, trigger="owner_cli")
    assert not (cfg.staging_dir / "old.tmp").exists()


def test_store_failure_never_changes_a_settled_status(world, db_session):
    services, _, _ = world
    pipeline.run(services, trigger="owner_cli")
    db_session.execute(text("update statement_file set status = 'needs_review', failure = 'guardrail'"))
    db_session.commit()
    sha = db_session.execute(text("select sha256 from statement_file")).scalar_one()
    services.retries.set(sha, store={"pending": True, "attempts": 0, "next_at": None, "confirmed_by": None})

    class Down:
        def exists(self, key):
            raise ConnectionError("down")

    services.store = Down()
    summary = pipeline.run(services, trigger="owner_cli")
    assert "store" in summary["errors"] and _file_rows(db_session) == [("needs_review", "guardrail")]
    assert services.retries.get(sha)["store"]["attempts"] == 1


def test_a_new_endpoint_starts_a_fresh_store_ladder(world, db_session):
    import dataclasses
    services, _, cfg = world
    pipeline.run(services, trigger="owner_cli")
    sha = db_session.execute(text("select sha256 from statement_file")).scalar_one()
    services.retries.set(sha, store={"pending": True, "attempts": 5, "next_at": None, "confirmed_by": None,
                                     "for_store": "old:9000/b"})
    puts = []

    class Minio:
        def exists(self, key):
            return False

        def put(self, key, path):
            puts.append(key)

    services.cfg = dataclasses.replace(cfg, minio_endpoint="new:9000")
    services.store = Minio()
    pipeline.run(services, trigger="owner_cli")
    assert len(puts) == 1 and _file_rows(db_session) == [("parsed", None)]


# ---- R5.3: the OCR branch ----------------------------------------------------------------------------------------

def _ocr_world(world, tmp_path, response=None, pdf_pages=(["short"],), name="ocr-child.sh", **kw):
    """Drive holds a PDF without a text layer; the OCR child is a fake script (unsandboxed, like the fake CLI)."""
    services, drive_files, cfg = world
    drive_files["id-1"] = ("信用卡/國泰世華/2026-09_國泰世華.pdf", encrypt(make_pdf(list(pdf_pages)), "A123456789"))
    child = fake_ocr_child.write(tmp_path / name, response, **kw)
    cfg = dataclasses.replace(cfg, ocr_child=str(child), ocr_timeout_s=2)
    services.cfg = cfg
    from worker import ocr
    services.ocr = ocr.Engine(cfg)
    return services, drive_files, cfg


RAW_OCR_TEXT = ("\n".join(statement_lines()) + "\n2026/09/01   COFFEE   1,234\n卡號 4111    2222　　3333    4444"
                "\n持卡人 王 小 明\nWANG XIAO  MING\n身分證 A12 345 6789\n4lll 2222 3333 444O")


def _record_parser(services):
    seen = []
    real = services.parser.parse

    def parse(masked):
        seen.append(masked)
        return real(masked)
    services.parser.parse = parse
    return seen


@pytest.mark.parametrize("closing_day", [30, None])  # 30: the fake parser's period_end day, so the guardrail passes
def test_parser_input_starts_with_the_closing_day_hint_when_the_card_has_one(world, db_session, card, closing_day):
    """Fix 2026-10-17: the account map carries the cards' closing days and the pipeline prefixes the masked text
    with the [hint] preamble; no closing day, no preamble (the masked statement is unchanged)."""
    services, _, _ = world
    db_session.execute(text("update account set closing_day = :d where id = :i"), {"d": closing_day, "i": card.id})
    db_session.commit()
    seen = _record_parser(services)
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["parsed"] == 1 and summary["errors"] == []
    (masked,) = seen
    if closing_day is None:
        assert not masked.startswith("[hint]")
    else:
        assert masked.startswith(parse_schema.hint(closing_day)) and f"day {closing_day}" in masked.split("\n", 1)[0]


def test_ocr_path_parser_input_starts_with_the_closing_day_hint(world, tmp_path, db_session, card):
    """The hint is prepended on the OCR path too (same `_parse_submit`), ahead of the masked OCR text."""
    db_session.execute(text("update account set closing_day = 30 where id = :i"), {"i": card.id})
    db_session.commit()
    services, _, _ = _ocr_world(world, tmp_path, fake_ocr_child.ok(text=RAW_OCR_TEXT, boxes=42))
    seen = _record_parser(services)
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["parsed"] == 1 and services.ocr.starts == 1
    (masked,) = seen
    assert masked.startswith(parse_schema.hint(30)) and "COFFEE" in masked


def test_stdin_cap_is_checked_after_the_hint_is_added(world, db_session, card, monkeypatch):
    """A statement under the cap on its own but over it with the preamble goes to review as too_large: the cap
    applies to the final parser input, and the parser is never called."""
    db_session.execute(text("update account set closing_day = 30 where id = :i"), {"i": card.id})
    db_session.commit()
    services, _, _ = world
    monkeypatch.setattr(parse_schema, "hint", lambda day: "[hint] " + "x" * pipeline.parser_mod.STDIN_CAP + "\n\n")
    seen = _record_parser(services)
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["needs_review"] == 1 and summary["parsed"] == 0 and seen == []
    assert db_session.execute(text("select status, failure from statement_file")).one() == ("needs_review", "too_large")


def test_ocr_path_parses_and_records_provenance(world, tmp_path, db_session):
    services, _, _ = _ocr_world(world, tmp_path, fake_ocr_child.ok(text=RAW_OCR_TEXT, boxes=42))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["parsed"] == 1 and summary["errors"] == [] and services.ocr.starts == 1
    assert db_session.execute(text("select status, has_text_layer, text_chars from statement_file")).one() == ("parsed", False, len(RAW_OCR_TEXT))
    rev = db_session.execute(text("select parser, raw from statement_revision")).one()
    assert rev[0] == "claude-cli+ocr" and rev[1]["notes"] == "ocr: mean_confidence=0.93 boxes=42 empty_pages=0 scale=3.0"


def test_ocr_parser_stdin_canary(world, tmp_path):
    services, _, _ = _ocr_world(world, tmp_path, fake_ocr_child.ok(text=RAW_OCR_TEXT))
    seen = _record_parser(services)
    pipeline.run(services, trigger="owner_cli")
    assert len(seen) == 1
    compact = "".join(seen[0].split())
    for secret in ("4111", "2222", "3333", "王小明", "WANGXIAOMING", "A123456789", "4111222233334440"):
        assert secret not in compact, secret
    assert "2026/09/01   COFFEE   1,234" in seen[0] and "MERCHANT 001" in seen[0]
    leaky = "".join(services.masker.text(RAW_OCR_TEXT).split())  # control: the text-layer masker leaks on the same text
    assert "4111" in leaky and "王小明" in leaky


def test_text_layer_file_makes_zero_ocr_calls(world, tmp_path):
    services, _, cfg = world
    from worker import ocr
    services.ocr = ocr.Engine(dataclasses.replace(cfg, ocr_child=str(fake_ocr_child.write(tmp_path / "never.sh", None, exit_code=7))))
    assert pipeline.run(services, trigger="owner_cli")["parsed"] == 1 and services.ocr.starts == 0


def test_password_reaches_the_ocr_child(world, tmp_path):
    hdr = tmp_path / "hdr"
    services, _, _ = _ocr_world(world, tmp_path, fake_ocr_child.ok(), name="recorder.sh", record_header_to=hdr)
    pipeline.run(services, trigger="owner_cli")
    assert json.loads(hdr.read_text())["password"] == "A123456789"


def test_ocr_disabled_keeps_todays_behaviour(world, tmp_path, db_session):
    services, _, _ = _ocr_world(world, tmp_path, fake_ocr_child.ok())
    services.ocr = None
    assert pipeline.run(services, trigger="owner_cli")["needs_review"] == 1
    assert _file_rows(db_session) == [("needs_review", "no_text_layer")]
    assert db_session.execute(text("select has_text_layer from statement_file")).scalar_one() is False


@pytest.mark.parametrize("response,kw,expected", [
    ({"ok": False, "code": "low_confidence"}, {}, ("needs_review", "no_text_layer")),
    ({"ok": False, "code": "resource"}, {}, ("needs_review", "no_text_layer")),
    ({"ok": False, "code": "too_large"}, {}, ("needs_review", "too_large")),
    ({"ok": False, "code": "malformed"}, {}, ("failed", "parse")),
    ({"ok": True, "pages": "SECRET", "text": "x"}, {}, ("failed", "parse")),
    (None, {"exit_code": 139}, ("failed", "parse")),
    (fake_ocr_child.ok(), {"sleep": 30}, ("needs_review", "no_text_layer")),
])
def test_ocr_outcomes(world, tmp_path, db_session, response, kw, expected):
    services, _, _ = _ocr_world(world, tmp_path, response, **kw)
    summary = pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [expected] and summary["errors"] == []
    assert db_session.execute(text("select has_text_layer from statement_file")).scalar_one() is False
    if expected[0] == "failed":
        assert db_session.execute(text("select parser_version from statement_file")).scalar_one() == services.parser_version


def test_ocr_timeout_then_next_file_runs(world, tmp_path, db_session):
    services, drive_files, _ = _ocr_world(world, tmp_path, fake_ocr_child.ok(), sleep=30)
    drive_files["id-2"] = ("信用卡/國泰世華/2026-08_國泰世華.pdf", encrypt(make_pdf([statement_lines(26)]), "A123456789"))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["needs_review"] == 1 and summary["parsed"] == 1 and services.ocr.starts == 1


def test_ocr_unavailable_exits_1_and_files_wait(world, tmp_path, db_session):
    services, drive_files, cfg = _ocr_world(world, tmp_path, fake_ocr_child.ok(), selftest="unavailable")
    drive_files["id-2"] = ("信用卡/國泰世華/2026-08_國泰世華.pdf", encrypt(make_pdf([["tiny"]]), "A123456789"))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["errors"] == ["ocr_unavailable"] and summary["skipped"] == 2 and summary["failed"] == 0
    assert services.ocr.starts == 0  # the self-test failed once; no file was offered to the child
    assert _file_rows(db_session) == [("new", None), ("new", None)]
    assert db_session.execute(text("select status from ingest_run order by id desc limit 1")).scalar_one() == "done"
    from worker import cli
    assert cli._bad(summary) is True
    with pytest.MonkeyPatch.context() as mp:  # scoped: the autouse env fixture must survive for the second run
        mp.setattr(pipeline, "build", lambda c, client, runner: services)
        mp.setattr(pipeline, "run", lambda *a, **k: summary)
        for key, value in (("STATEMENT_API_TOKEN_FILE", cfg.api_token_file), ("STATEMENT_PASSWORD_FILE", cfg.password_file),
                           ("STATEMENT_STATE_DIR", cfg.state_dir), ("STATEMENT_API_URL", cfg.api_url)):
            mp.setenv(key, str(value))
        assert cli.main(["run"]) == 1
    fake_ocr_child.write(tmp_path / "ocr-child.sh", fake_ocr_child.ok())  # host fixed: the next run resets the cache
    assert pipeline.run(services, trigger="owner_cli")["parsed"] == 2 and services.ocr.starts == 2


def test_ocr_unavailable_in_the_child_itself_is_one_code_and_one_start(world, tmp_path, db_session):
    services, drive_files, _ = _ocr_world(world, tmp_path, {"ok": False, "code": "unavailable"})  # self-test ok, read says unavailable
    drive_files["id-2"] = ("信用卡/國泰世華/2026-08_國泰世華.pdf", encrypt(make_pdf([["tiny"]]), "A123456789"))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["errors"] == ["ocr_unavailable"] and summary["skipped"] == 2 and services.ocr.starts == 1
    assert _file_rows(db_session) == [("new", None), ("new", None)]


def test_ocr_unavailable_leaves_an_existing_needs_review_row_alone(world, tmp_path, db_session):
    services, _, cfg = _ocr_world(world, tmp_path, {"ok": False, "code": "low_confidence"})
    pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [("needs_review", "no_text_layer")]
    db_session.execute(text("update statement_file set parser_version='old'"))
    db_session.commit()
    fake_ocr_child.write(tmp_path / "ocr-child.sh", None, selftest="unavailable")
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["errors"] == ["ocr_unavailable"] and summary["skipped"] == 1
    assert _file_rows(db_session) == [("needs_review", "no_text_layer")]
    assert db_session.execute(text("select parser_version from statement_file")).scalar_one() == "old"


def test_poll_keeps_the_degraded_code(world, tmp_path, db_session, client, monkeypatch):
    """A degraded run (resumed) followed by a clean queued one in the same poll: the result still carries
    ocr_unavailable, so the unit exits 1 and the operator sees the host problem."""
    services, drive_files, cfg = _ocr_world(world, tmp_path, None, selftest="unavailable")
    hermes = {"Authorization": "Bearer hermes-token"}
    assert client.post("/statements/ingest/run", headers=hermes).status_code == 202
    real_list = services.drive.list_pdfs
    services.drive.list_pdfs = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        pipeline.poll(services, initiator_hint="t")  # claimed, then interrupted: run.json stays for the resume
    services.drive.list_pdfs = real_list
    db_session.execute(text("update ingest_run set lease_expires_at = now() - interval '1 hour'"))  # dead lease: no coalescing
    db_session.commit()
    assert client.post("/statements/ingest/run", headers=hermes).status_code == 202  # a second, queued run
    calls = []
    real = pipeline._execute

    def execute(services_, summary, lease, trigger, mode):
        calls.append(1)
        if len(calls) == 2:  # second run: host fixed, Drive now text-layer only
            fake_ocr_child.write(tmp_path / "ocr-child.sh", fake_ocr_child.ok())
            drive_files["id-1"] = ("信用卡/國泰世華/2026-09_國泰世華.pdf", encrypt(make_pdf([statement_lines()]), "A123456789"))
        return real(services_, summary, lease, trigger, mode)
    monkeypatch.setattr(pipeline, "_execute", execute)
    out = pipeline.poll(services, initiator_hint="t")
    assert out["claimed"] == 2 and out["errors"] == ["ocr_unavailable"] and out["bad_runs"] == 0 and out["parsed"] == 1
    from worker import cli
    assert cli._bad(out) is True
    clean = pipeline.poll(services, initiator_hint="t")  # nothing queued: a clean poll
    assert clean["claimed"] == 0 and clean["errors"] == [] and cli._bad(clean) is False


def _big_text_child(tmp_path, line_len: int, lines: int):
    """A fake child whose text is `lines` lines of `line_len` x's (built in the shell: a literal that long would be slow)."""
    script = tmp_path / "big.sh"
    script.write_text("#!/bin/sh\nif [ \"$1\" = \"--selftest\" ]; then printf '{\"ready\": true}\\n'; exit 0; fi\ncat >/dev/null\n"
                      "printf '{\"ok\": true, \"pages\": 1, \"empty_pages\": 0, \"boxes\": 2, \"mean_confidence\": 0.9, \"text\": \"'\n"
                      f"i=0; while [ $i -lt {lines} ]; do head -c {line_len} /dev/zero | tr '\\0' x; printf '\\\\n'; i=$((i+1)); done\n"
                      "printf '\"}\\n'\n")
    script.chmod(0o700)
    return script


@pytest.mark.parametrize("line_len,lines", [(1024, 401), (pipeline.OCR_LINE_MAX + 1, 1)])
def test_ocr_text_over_the_pre_mask_bound_is_too_large_without_masking_or_a_parser_call(world, tmp_path, db_session, line_len, lines, monkeypatch):
    """Over the parser's STDIN_CAP (400 KB) in total, or one line over OCR_LINE_MAX: the masker (quadratic in the line
    length) and the parser are never called; the file goes to review as too_large."""
    from worker import ocr
    services, _, _ = _ocr_world(world, tmp_path, None, name="unused.sh")
    services.cfg = dataclasses.replace(services.cfg, ocr_child=str(_big_text_child(tmp_path, line_len, lines)))
    services.ocr = ocr.Engine(services.cfg)
    seen = _record_parser(services)
    monkeypatch.setattr(services.masker, "mask_ocr", lambda *a, **k: (_ for _ in ()).throw(AssertionError("masker called")))
    summary = pipeline.run(services, trigger="owner_cli")
    assert seen == [] and _file_rows(db_session) == [("needs_review", "too_large")] and services.ocr.starts == 1
    assert summary["errors"] == []


def test_ocr_text_too_large_rule_at_each_bound():
    from worker import parser as parser_mod
    line_max = pipeline.OCR_LINE_MAX
    assert pipeline.ocr_text_too_large("x" * line_max + "\n" + "y" * line_max) is False      # lines exactly at the bound
    assert pipeline.ocr_text_too_large("x" * (line_max + 1)) is True                           # one over
    assert pipeline.ocr_text_too_large("y\n" + "x" * (line_max + 1) + "\n") is True            # one over on a later line
    body = "\n".join(["x" * 1000] * (parser_mod.STDIN_CAP // 1000))
    exact = body + "x" * (parser_mod.STDIN_CAP - len(body))  # the last line stays well under OCR_LINE_MAX
    assert len(exact.encode("utf-8")) == parser_mod.STDIN_CAP and pipeline.ocr_text_too_large(exact) is False  # total exactly at the cap
    assert pipeline.ocr_text_too_large(exact + "x") is True                                     # one byte over
    assert pipeline.ocr_text_too_large("中" * (parser_mod.STDIN_CAP // 3) + "x") is True          # bytes, not characters
    assert pipeline.ocr_text_too_large("") is False


def test_parser_notes_survive_beside_the_ocr_note(world, tmp_path, db_session):
    from worker import parser as parser_mod
    services, _, cfg = _ocr_world(world, tmp_path, fake_ocr_child.ok())
    noted = fake_claude.write(tmp_path / "claude-noted", fake_claude.transcript({**fake_claude.GOOD, "notes": "parser note"}))
    services.parser = parser_mod.Parser(dataclasses.replace(cfg, parser_cli=noted), services.runner)
    pipeline.run(services, trigger="owner_cli")
    notes = db_session.execute(text("select raw->>'notes' from statement_revision")).scalar_one()
    assert notes == "parser note\nocr: mean_confidence=0.93 boxes=2 empty_pages=0 scale=3.0"


def test_build_creates_an_engine_only_when_enabled(world, client):
    services, _, cfg = world
    assert services.ocr is not None and services.ocr.cfg is cfg
    assert pipeline.build(dataclasses.replace(cfg, ocr_enabled=False), client, services.runner).ocr is None


def test_revision_body_parser_and_notes():
    parsed = parse_schema.StatementParse.model_validate(fake_claude.GOOD)
    plain = pipeline.revision_body(parsed, file_id=1, account_id=2, parser_version="v")
    assert plain["parser"] == "claude-cli" and plain["raw"]["notes"] == parsed.notes
    ocr = pipeline.revision_body(parsed, file_id=1, account_id=2, parser_version="v", parser="claude-cli+ocr", extra_note="ocr: x")
    assert ocr["parser"] == "claude-cli+ocr" and ocr["raw"]["notes"].endswith("ocr: x")


# ---- R5.3 fallback: OCR after a failed text-layer parse (AGENT-118 ruling 2026-10-14) ------------------------------

OCR_MARK_TEXT = "\n".join(statement_lines()) + "\nOCRMARK"


def _text_parse_fails(services, reason="schema", mark="OCRMARK"):
    """The real fake-CLI parse for OCR text (carries `mark`); a ParseError(`reason`) for the text-layer text."""
    from worker import parser as parser_mod
    calls = []
    real = services.parser.parse

    def parse(masked):
        calls.append("ocr" if mark in masked else "text")
        if mark in masked:
            return real(masked)
        raise parser_mod.ParseError(reason, "synthetic")
    services.parser.parse = parse
    return calls


def _text_layer_world(world, tmp_path, response=None, **kw):
    """Drive holds a text-layer PDF (the fixture's); the OCR child is a fake script."""
    services, drive_files, cfg = world
    child = fake_ocr_child.write(tmp_path / "ocr-child.sh", response, **kw)
    services.cfg = dataclasses.replace(cfg, ocr_child=str(child), ocr_timeout_s=2)
    from worker import ocr
    services.ocr = ocr.Engine(services.cfg)
    return services, drive_files, services.cfg


def test_fallback_after_schema_failure_parses_with_provenance(world, tmp_path, db_session):
    services, _, _ = _text_layer_world(world, tmp_path, fake_ocr_child.ok(text=OCR_MARK_TEXT, boxes=7))
    calls = _text_parse_fails(services, "schema")
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["parsed"] == 1 and summary["failed"] == 0 and summary["errors"] == []
    assert summary["ocr_fallback"] == 1 and summary["ocr_trigger"] == 0 and services.ocr.starts == 1 and calls == ["text", "ocr"]
    assert db_session.execute(text("select status, has_text_layer from statement_file")).one() == ("parsed", True)
    rev = db_session.execute(text("select parser, raw->>'notes' from statement_revision")).one()
    assert rev[0] == "claude-cli+ocr"
    assert rev[1] == "ocr: mean_confidence=0.93 boxes=7 empty_pages=0 scale=3.0\nocr: fallback after text-layer parse failure"


def test_fallback_after_api_422(world, tmp_path, db_session, monkeypatch):
    from worker.api import ApiError
    services, _, _ = _text_layer_world(world, tmp_path, fake_ocr_child.ok(text=OCR_MARK_TEXT))
    real_submit = services.api.submit_revision
    seen = []

    def submit(lease, body):
        seen.append(body["parser"])
        if body["parser"] == "claude-cli":
            raise ApiError(422, {"detail": "synthetic"})
        return real_submit(lease, body)
    monkeypatch.setattr(services.api, "submit_revision", submit)
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["parsed"] == 1 and summary["ocr_fallback"] == 1 and seen == ["claude-cli", "claude-cli+ocr"]
    assert _file_rows(db_session) == [("parsed", None)]


@pytest.mark.parametrize("reason,expected", [
    ("timeout", ("failed", "transient")), ("exit", ("failed", "transient")), ("sandbox", ("failed", "sandbox")),
])
def test_excluded_parse_outcomes_do_not_fall_back(world, tmp_path, db_session, reason, expected):
    services, _, _ = _text_layer_world(world, tmp_path, fake_ocr_child.ok(text=OCR_MARK_TEXT))
    if reason == "sandbox":
        services.gate.ensure(services.parser)  # a valid cached gate, so the violation latches on real input
    calls = _text_parse_fails(services, reason)
    summary = pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [expected] and services.ocr.starts == 0 and calls == ["text"] and summary["ocr_fallback"] == 0


def test_guardrail_failure_does_not_fall_back(world, tmp_path, db_session, monkeypatch):
    services, _, _ = _text_layer_world(world, tmp_path, fake_ocr_child.ok(text=OCR_MARK_TEXT))
    real_submit = services.api.submit_revision
    monkeypatch.setattr(services.api, "submit_revision", lambda lease, body: {**real_submit(lease, body), "guardrail_ok": False})
    summary = pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [("needs_review", "guardrail")] and services.ocr.starts == 0 and summary["ocr_fallback"] == 0


def test_password_mapping_and_too_large_never_reach_the_fallback(world, tmp_path, db_session, monkeypatch):
    from worker import parser as parser_mod
    services, drive_files, _ = _text_layer_world(world, tmp_path, fake_ocr_child.ok(text=OCR_MARK_TEXT))
    drive_files["id-1"] = ("信用卡/國泰世華/2026-09_國泰世華.pdf", encrypt(make_pdf([statement_lines()]), "WRONG"))
    drive_files["id-2"] = ("信用卡/未知/2026-09_x.pdf", encrypt(make_pdf([statement_lines()]), "A123456789"))
    drive_files["id-3"] = ("信用卡/國泰世華/2026-08_國泰世華.pdf", encrypt(make_pdf([statement_lines(400)]), "A123456789"))
    monkeypatch.setattr(parser_mod, "STDIN_CAP", 2000)
    _text_parse_fails(services, "schema")
    pipeline.run(services, trigger="owner_cli")
    assert sorted(_file_rows(db_session)) == sorted([("failed", "password"), ("ignored", "mapping"), ("needs_review", "too_large")])
    assert services.ocr.starts == 0


def test_both_attempts_fail_is_parse_with_one_ocr_start(world, tmp_path, db_session):
    services, _, _ = _text_layer_world(world, tmp_path, fake_ocr_child.ok(text=OCR_MARK_TEXT))
    calls = _text_parse_fails(services, "schema", mark="NEVER-PRESENT")  # both texts fail
    summary = pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [("failed", "parse")] and services.ocr.starts == 1 and calls == ["text", "text"]
    assert summary["failed"] == 1 and summary["parsed"] == 0 and summary["ocr_fallback"] == 1
    assert db_session.execute(text("select has_text_layer, parser_version from statement_file")).one() == (True, services.parser_version)
    again = pipeline.run(services, trigger="owner_cli")  # same parser version: no retry, no second OCR start
    assert again["unchanged"] == 1 and services.ocr.starts == 1


def _garbled_text_parse(services, *, ocr_merchant="咖啡店", ocr_amount=None, mark="OCRMARK"):
    """#206: the text-layer parse prints line 1's merchant as `(cid:N)` garbage; the OCR text (carries `mark`) parses
    to the same statement with `ocr_merchant` on line 1 (and `ocr_amount` instead of its amount when given)."""
    calls = []
    real = services.parser.parse

    def parse(masked):
        parsed = real(masked)
        first = parsed.lines[0]
        if mark in masked:
            calls.append("ocr")
            update = {"merchant_raw": ocr_merchant} | ({"printed_amount": ocr_amount} if ocr_amount else {})
            return parsed.model_copy(update={"lines": [first.model_copy(update=update), *parsed.lines[1:]]})
        calls.append("text")
        garbled = first.model_copy(update={"merchant_raw": "(cid:222)(cid:221)X"})
        return parsed.model_copy(update={"lines": [garbled, *parsed.lines[1:]]})
    services.parser.parse = parse
    return calls


def _revision(db_session):
    return db_session.execute(text(
        "select r.parser, r.raw->>'notes', (select string_agg(l.merchant_raw, '|' order by l.seq) from statement_line l "
        "where l.revision_id = r.id), (select string_agg(l.printed_amount::text, '|' order by l.seq) "
        "from statement_line l where l.revision_id = r.id) from statement_revision r")).one()


def test_garbled_merchants_are_repaired_from_the_ocr_parse(world, tmp_path, db_session):
    services, _, _ = _text_layer_world(world, tmp_path, fake_ocr_child.ok(text=OCR_MARK_TEXT, boxes=7))
    calls = _garbled_text_parse(services)
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["parsed"] == 1 and summary["errors"] == [] and calls == ["text", "ocr"]
    assert summary["ocr_merchant_repair"] == 1 and summary["merchant_lines_repaired"] == 1
    assert summary["ocr_trigger"] == 0 and summary["ocr_fallback"] == 0 and services.ocr.starts == 1
    parser, notes, merchants, amounts = _revision(db_session)
    assert parser == "claude-cli+ocr-merchants" and merchants == "咖啡店|BOOKS"
    assert notes == "ocr: merchant repair 1/1 lines mean_confidence=0.93 boxes=7 scale=3.0"
    assert db_session.execute(text("select status, has_text_layer from statement_file")).one() == ("parsed", True)


def test_ocr_line_with_another_amount_does_not_repair(world, tmp_path, db_session):
    """Amounts always come from the text layer: an OCR line that disagrees on the amount is no match."""
    services, _, _ = _text_layer_world(world, tmp_path, fake_ocr_child.ok(text=OCR_MARK_TEXT))
    _garbled_text_parse(services, ocr_amount="81")
    summary = pipeline.run(services, trigger="owner_cli")
    parser, notes, merchants, amounts = _revision(db_session)
    assert parser == "claude-cli" and merchants.startswith("(cid:222)") and summary["merchant_lines_repaired"] == 0
    assert notes.startswith("ocr: merchant repair 0/1 lines") and summary["parsed"] == 1


def test_still_garbled_ocr_merchant_does_not_replace(world, tmp_path, db_session):
    services, _, _ = _text_layer_world(world, tmp_path, fake_ocr_child.ok(text=OCR_MARK_TEXT))
    _garbled_text_parse(services, ocr_merchant="(cid:5)")
    pipeline.run(services, trigger="owner_cli")
    assert _revision(db_session)[0] == "claude-cli" and _revision(db_session)[2].startswith("(cid:222)")


def test_ocr_failure_during_repair_submits_the_text_parse(world, tmp_path, db_session):
    services, _, _ = _text_layer_world(world, tmp_path, None, selftest="unavailable")
    calls = _garbled_text_parse(services)
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["parsed"] == 1 and calls == ["text"] and summary["errors"] == ["ocr_unavailable"]
    parser, notes, merchants, _ = _revision(db_session)
    assert parser == "claude-cli" and merchants.startswith("(cid:222)")
    assert notes == "ocr: merchant repair skipped (OCRUnavailable)"


def test_ocr_parse_failure_during_repair_submits_the_text_parse(world, tmp_path, db_session):
    from worker import parser as parser_mod
    services, _, _ = _text_layer_world(world, tmp_path, fake_ocr_child.ok(text=OCR_MARK_TEXT))
    calls = _garbled_text_parse(services)
    inner = services.parser.parse

    def parse(masked):
        if "OCRMARK" in masked:
            raise parser_mod.ParseError("timeout", "synthetic")
        return inner(masked)
    services.parser.parse = parse
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["parsed"] == 1 and _file_rows(db_session) == [("parsed", None)]
    assert _revision(db_session)[1] == "ocr: merchant repair skipped (parse timeout)" and calls == ["text"]


def test_clean_text_parse_never_starts_the_repair(world, tmp_path, db_session):
    services, _, _ = _text_layer_world(world, tmp_path, fake_ocr_child.ok(text=OCR_MARK_TEXT))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["parsed"] == 1 and services.ocr.starts == 0 and summary["ocr_merchant_repair"] == 0
    assert _revision(db_session)[0] == "claude-cli"


def test_ocr_unavailable_during_fallback_keeps_parse_and_reports_the_code(world, tmp_path, db_session):
    services, _, _ = _text_layer_world(world, tmp_path, None, selftest="unavailable")
    _text_parse_fails(services, "schema")
    summary = pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [("failed", "parse")] and summary["errors"] == ["ocr_unavailable"]
    assert summary["failed"] == 1 and summary["skipped"] == 0 and summary["ocr_fallback"] == 1  # counted at the OCR start
    from worker import cli
    assert cli._bad(summary) is True


@pytest.mark.parametrize("response,kw", [
    ({"ok": False, "code": "low_confidence"}, {}), ({"ok": False, "code": "resource"}, {}), ({"ok": False, "code": "too_large"}, {}),
    ({"ok": False, "code": "malformed"}, {}), (fake_ocr_child.ok(), {"sleep": 30}), (fake_ocr_child.ok(text="x" * 5000), {}),
])
def test_ocr_failure_during_fallback_stays_parse(world, tmp_path, db_session, response, kw):
    services, _, _ = _text_layer_world(world, tmp_path, response, **kw)
    _text_parse_fails(services, "schema")
    summary = pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [("failed", "parse")] and summary["errors"] == [] and services.ocr.starts == 1
    assert db_session.execute(text("select has_text_layer from statement_file")).scalar_one() is True


def test_fallback_is_refused_when_the_parser_latch_is_set(world, tmp_path, db_session):
    from worker import parser as parser_mod
    services, _, _ = _text_layer_world(world, tmp_path, fake_ocr_child.ok(text=OCR_MARK_TEXT))

    def parse(masked):  # a parse failure that also closed the gate (the latch is the only parser budget there is)
        services.parser_disabled = True
        raise parser_mod.ParseError("schema", "synthetic")
    services.parser.parse = parse
    pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [("failed", "parse")] and services.ocr.starts == 0


def test_fallback_is_off_when_ocr_is_disabled(world, tmp_path, db_session):
    services, _, _ = _text_layer_world(world, tmp_path, fake_ocr_child.ok(text=OCR_MARK_TEXT))
    services.ocr = None
    calls = _text_parse_fails(services, "schema")
    pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [("failed", "parse")] and calls == ["text"]


def _exc_recorder(engine):
    """Wrap Engine.read to record the active exception at call time (R5a handoff: no OCR call under a handler)."""
    import sys
    seen = []
    real = engine.read

    def read(data, password):
        seen.append(sys.exc_info()[1])
        return real(data, password)
    engine.read = read
    return seen


def test_ocr_child_starts_with_no_active_exception_on_the_trigger_path(world, tmp_path):
    services, _, _ = _ocr_world(world, tmp_path, fake_ocr_child.ok(text=RAW_OCR_TEXT))
    seen = _exc_recorder(services.ocr)
    assert pipeline.run(services, trigger="owner_cli")["parsed"] == 1 and seen == [None]


def test_ocr_child_starts_with_no_active_exception_on_the_fallback_path(world, tmp_path):
    services, _, _ = _text_layer_world(world, tmp_path, fake_ocr_child.ok(text=OCR_MARK_TEXT))
    _text_parse_fails(services, "schema")
    seen = _exc_recorder(services.ocr)
    assert pipeline.run(services, trigger="owner_cli")["parsed"] == 1 and seen == [None]


# ---- AGENT-124 fix round: exception context, final fallback, counters, poll union, UTF-8 boundary, raw-box canaries ----

import logging
import traceback

CANARY = "CANARY-SECRET-7731"


def _capture_read_errors(engine):
    """Wrap Engine.read to keep every exception it raises (re-raised unchanged)."""
    seen = []
    real = engine.read

    def read(data, password):
        try:
            return real(data, password)
        except BaseException as exc:  # noqa: BLE001
            seen.append(exc)
            raise
    engine.read = read
    return seen


class _Records(logging.Handler):
    """Captures the worker logger directly (its propagation is not relied on)."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def worker_log():
    handler = _Records()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    loggers = [logging.getLogger("worker"), logging.getLogger()]
    old = [(lg, lg.level, lg.disabled) for lg in loggers]
    for lg in loggers:
        lg.addHandler(handler)
        lg.setLevel(logging.DEBUG)
        lg.disabled = False  # the app's logging config (imported by the API fixture) disables pre-existing loggers
    try:
        yield handler
    finally:
        for lg, level, disabled in old:
            lg.removeHandler(handler)
            lg.setLevel(level)
            lg.disabled = disabled


def _assert_canary_free(exc, log):
    assert exc.__cause__ is None and (exc.__context__ is None or CANARY not in repr(exc.__context__))
    assert CANARY not in "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    assert CANARY not in log.text


def test_trigger_ocr_error_carries_no_extraction_exception(world, tmp_path, db_session, monkeypatch, worker_log):
    """A content-bearing NoTextLayer, then an OCR timeout: the OCRTimeout has no context, no cause, no canary anywhere."""
    services, _, _ = _ocr_world(world, tmp_path, fake_ocr_child.ok(), sleep=30)
    monkeypatch.setattr(pipeline.pdf, "extract", lambda data, password, **kw: (_ for _ in ()).throw(pipeline.pdf.NoTextLayer(CANARY)))
    seen = _capture_read_errors(services.ocr)
    pipeline.run(services, trigger="owner_cli")
    assert [type(e) for e in seen] == [pipeline.ocr_mod.OCRTimeout] and _file_rows(db_session) == [("needs_review", "no_text_layer")]
    _assert_canary_free(seen[0], worker_log)


def test_fallback_ocr_error_carries_no_parser_exception(world, tmp_path, db_session, worker_log):
    """A content-bearing ParseError on the text attempt, then an OCR timeout in the fallback."""
    from worker import parser as parser_mod
    services, _, _ = _text_layer_world(world, tmp_path, fake_ocr_child.ok(), sleep=30)
    services.parser.parse = lambda masked: (_ for _ in ()).throw(parser_mod.ParseError("schema", CANARY))
    seen = _capture_read_errors(services.ocr)
    pipeline.run(services, trigger="owner_cli")
    assert [type(e) for e in seen] == [pipeline.ocr_mod.OCRTimeout] and _file_rows(db_session) == [("failed", "parse")]
    _assert_canary_free(seen[0], worker_log)


def test_second_parser_call_carries_no_first_attempt_exception(world, tmp_path, db_session, worker_log):
    from worker import parser as parser_mod
    services, _, _ = _text_layer_world(world, tmp_path, fake_ocr_child.ok(text=OCR_MARK_TEXT))
    raised = []

    def parse(masked):
        exc = parser_mod.ParseError("timeout", "second attempt") if "OCRMARK" in masked else parser_mod.ParseError("schema", CANARY)
        raised.append(exc)
        raise exc
    services.parser.parse = parse
    pipeline.run(services, trigger="owner_cli")
    assert len(raised) == 2 and _file_rows(db_session) == [("failed", "parse")]
    _assert_canary_free(raised[1], worker_log)


@pytest.mark.parametrize("reason", ["timeout", "exit", "schema"])
def test_second_attempt_parse_failure_is_final_at_this_version(world, tmp_path, db_session, reason):
    """Must 2 ruling: a timeout, exit or schema failure on the second parse ends failed/parse at the current version;
    a second run makes no OCR start and no parser call."""
    from worker import parser as parser_mod
    services, _, _ = _text_layer_world(world, tmp_path, fake_ocr_child.ok(text=OCR_MARK_TEXT))
    calls = []

    def parse(masked):
        calls.append("ocr" if "OCRMARK" in masked else "text")
        raise parser_mod.ParseError(reason if "OCRMARK" in masked else "schema", "synthetic")
    services.parser.parse = parse
    summary = pipeline.run(services, trigger="owner_cli")
    assert calls == ["text", "ocr"] and services.ocr.starts == 1 and summary["ocr_fallback"] == 1
    assert db_session.execute(text("select status, failure, has_text_layer, parser_version from statement_file")).one() == ("failed", "parse", True, services.parser_version)
    assert services.parser_disabled is False and "parser_disabled" not in summary["errors"]
    again = pipeline.run(services, trigger="owner_cli")
    assert again["unchanged"] == 1 and calls == ["text", "ocr"] and services.ocr.starts == 1


@pytest.mark.parametrize("reason", ["sandbox", "auth", "cli_version"])
def test_second_attempt_host_fault_is_sandbox_with_the_latch(world, tmp_path, db_session, reason):
    """Must 2 ruling (realigned): a sandbox, auth or CLI-version failure on the second parse is a host fault — the
    latch is set and the row is failed/sandbox exactly as on a first attempt; after the operator gate clears the
    latch the file is processed again and may fall back again (the accepted exception to once-per-version)."""
    from worker import parser as parser_mod
    services, _, _ = _text_layer_world(world, tmp_path, fake_ocr_child.ok(text=OCR_MARK_TEXT))
    services.gate.ensure(services.parser)  # a valid cached gate, so the violation latches on real input
    calls = []
    real = services.parser.parse
    broken = [True]

    def parse(masked):
        calls.append("ocr" if "OCRMARK" in masked else "text")
        if "OCRMARK" in masked and not broken[0]:
            return real(masked)  # host repaired: the OCR text parses
        raise parser_mod.ParseError(reason if "OCRMARK" in masked else "schema", "synthetic")
    services.parser.parse = parse
    summary = pipeline.run(services, trigger="owner_cli")
    assert calls == ["text", "ocr"] and services.ocr.starts == 1 and summary["ocr_fallback"] == 1
    assert _file_rows(db_session) == [("failed", "sandbox")] and summary["failed"] == 1
    assert services.parser_disabled is True and "parser_disabled" in summary["errors"] and services.gate.latch_path.exists()
    latched = pipeline.run(services, trigger="owner_cli")  # latched: the sandbox row is not due, nothing parses
    assert "parser_disabled" in latched["errors"] and latched["unchanged"] == 1 and calls == ["text", "ocr"]
    assert _operator_gate(services).ok  # the operator repairs the host and clears the latch
    broken[0] = False
    again = pipeline.run(services, trigger="owner_cli")
    assert again["parsed"] == 1 and again["ocr_fallback"] == 1 and calls == ["text", "ocr", "text", "ocr"] and services.ocr.starts == 2
    assert _file_rows(db_session) == [("parsed", None)]


def test_counters_count_at_ocr_start_and_log_the_outcome_class(world, tmp_path, db_session, worker_log):
    """Should 1: a timeout on the trigger path still counts ocr_trigger; a pre-mask rejection logs its class."""
    services, _, _ = _ocr_world(world, tmp_path, fake_ocr_child.ok(), sleep=30)
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["ocr_trigger"] == 1 and summary["ocr_fallback"] == 0 and "ocr trigger: OCRTimeout" in worker_log.text
    fake_ocr_child.write(tmp_path / "ocr-child.sh", fake_ocr_child.ok(text="x" * (pipeline.OCR_LINE_MAX + 1)))
    db_session.execute(text("update statement_file set parser_version='old'")); db_session.commit()
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["ocr_trigger"] == 1 and "ocr trigger: text too_large" in worker_log.text and _file_rows(db_session) == [("needs_review", "too_large")]


def test_fallback_counter_counts_a_timed_out_ocr(world, tmp_path, db_session, worker_log):
    services, _, _ = _text_layer_world(world, tmp_path, fake_ocr_child.ok(), sleep=30)
    _text_parse_fails(services, "schema")
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["ocr_fallback"] == 1 and "ocr fallback: OCRTimeout" in worker_log.text and _file_rows(db_session) == [("failed", "parse")]


def test_poll_keeps_codes_of_an_aborted_run(world, tmp_path, db_session, client, monkeypatch):
    """Should 2: ocr_unavailable in a run that then aborts, followed by a clean run: the union still carries it."""
    from worker import api as api_mod
    services, drive_files, cfg = _ocr_world(world, tmp_path, None, selftest="unavailable")
    hermes = {"Authorization": "Bearer hermes-token"}
    assert client.post("/statements/ingest/run", headers=hermes).status_code == 202
    services.drive.list_pdfs = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        pipeline.poll(services, initiator_hint="t")
    del services.drive.list_pdfs
    db_session.execute(text("update ingest_run set lease_expires_at = now() - interval '1 hour'")); db_session.commit()
    assert client.post("/statements/ingest/run", headers=hermes).status_code == 202
    real_removed = services.api.mark_removed
    calls = []

    def mark_removed(lease, ids, **kw):  # the first (degraded) run aborts after its files were processed
        calls.append(1)
        if len(calls) == 1:
            raise api_mod.ApiError(500, {"detail": "synthetic"})
        return real_removed(lease, ids, **kw)
    monkeypatch.setattr(services.api, "mark_removed", mark_removed)
    real_execute = pipeline._execute

    def execute(services_, summary, lease, trigger, mode):
        if len(calls) == 1:  # second run: host fixed, Drive text-layer only
            fake_ocr_child.write(tmp_path / "ocr-child.sh", fake_ocr_child.ok())
            drive_files["id-1"] = ("信用卡/國泰世華/2026-09_國泰世華.pdf", encrypt(make_pdf([statement_lines()]), "A123456789"))
        return real_execute(services_, summary, lease, trigger, mode)
    monkeypatch.setattr(pipeline, "_execute", execute)
    out = pipeline.poll(services, initiator_hint="t")
    assert out["claimed"] == 2 and out["bad_runs"] == 1 and "ocr_unavailable" in out["errors"] and out["parsed"] == 1


@pytest.mark.parametrize("path", ["trigger", "fallback"])
def test_non_utf8_ocr_text_is_rejected_at_the_boundary(world, tmp_path, db_session, path):
    """Should 3: a lone surrogate in the child's JSON text is Malformed → failed/parse, no masker, no parser."""
    body = '{"ok": true, "pages": 1, "empty_pages": 0, "boxes": 2, "mean_confidence": 0.9, "text": "MERCHANT \\ud800 1,234"}'
    if path == "trigger":
        services, _, _ = _ocr_world(world, tmp_path, body)
    else:
        services, _, _ = _text_layer_world(world, tmp_path, body)
        _text_parse_fails(services, "schema")
    seen = _record_parser(services) if path == "trigger" else []
    summary = pipeline.run(services, trigger="owner_cli")
    assert _file_rows(db_session) == [("failed", "parse")] and summary["errors"] == [] and services.ocr.starts == 1 and seen == []


# ---- Should 4: raw boxes → real layout → caller → sink, with mutation controls ------------------------------------

def _boxes_text():
    """Boxes as the child would produce them, laid out by the real ocr_text.layout: a card number split across
    boxes with OCR confusables, a split holder name, a split ID, and ordinary rows."""
    from worker import ocr_text
    rows = [["2026/09/01", "COFFEE", "1,234"], ["MERCHANT 001", "", "5,678"], ["卡號", "4lll", "222O", "333O", "444O"],
            ["持卡人", "王", "小", "明"], ["身分證", "A12", "345", "6789"], ["TOTAL", "", "6,912"]]
    boxes = []
    for r, cells in enumerate(rows):
        x = 10.0
        for cell in cells:
            if cell:
                boxes.append((x, 20.0 + r * 30.0, 8.0 * len(cell), 12.0, cell))
            x += 8.0 * max(len(cell), 2) + 40.0
    return ocr_text.layout(boxes)


SECRETS = ("4111222233334440", "4lll", "444O", "王小明", "A123456789")


def test_raw_boxes_reach_the_parser_masked(world, tmp_path):
    services, _, _ = _ocr_world(world, tmp_path, fake_ocr_child.ok(text=_boxes_text()))
    seen = _record_parser(services)
    pipeline.run(services, trigger="owner_cli")
    compact = "".join(seen[0].split())
    assert all(s not in compact for s in SECRETS), compact
    assert "2026/09/01" in seen[0] and "COFFEE" in seen[0] and "6,912" in seen[0]


@pytest.mark.parametrize("mutation", ["no_masking", "no_normalisation"])
def test_raw_box_canary_discriminates(world, tmp_path, monkeypatch, mutation):
    """Mutation controls: bypass masking, or bypass the confusable normalisation, and the parser sees a secret."""
    from worker import mask
    if mutation == "no_masking":
        monkeypatch.setattr(mask.Masker, "mask_ocr", lambda self, s, extra_literals=(): s)
    else:
        monkeypatch.setattr(mask, "_ocr_fix_confusables", lambda line: line)
    services, _, _ = _ocr_world(world, tmp_path, fake_ocr_child.ok(text=_boxes_text()))
    seen = _record_parser(services)
    pipeline.run(services, trigger="owner_cli")
    compact = "".join(seen[0].split())
    assert any(s in compact for s in SECRETS), compact


@pytest.mark.parametrize("reason", ["timeout", "exit"])
def test_parser_timeout_after_a_triggered_ocr_is_transient(world, tmp_path, db_session, reason, monkeypatch):
    """dev-grok final re-check Must: on the trigger path the first parser call is not final — a parser timeout or
    exit after a successful OCR read is failed/transient with a backoff, and due again once it has passed."""
    from worker import parser as parser_mod
    services, _, _ = _ocr_world(world, tmp_path, fake_ocr_child.ok(text=RAW_OCR_TEXT))
    calls = []
    real = services.parser.parse
    broken = [True]

    def parse(masked):
        calls.append(1)
        if broken[0]:
            raise parser_mod.ParseError(reason, "synthetic")
        return real(masked)
    services.parser.parse = parse
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["transient"] == 1 and summary["failed"] == 0 and summary["ocr_trigger"] == 1 and services.ocr.starts == 1
    row = db_session.execute(text("select status, failure, has_text_layer, next_retry_at from statement_file")).one()
    assert row[0:3] == ("failed", "transient", False) and row[3] is not None
    retry = services.retries.get(db_session.execute(text("select sha256 from statement_file")).scalar_one())
    assert retry["transient_attempts"] == 1
    assert pipeline.run(services, trigger="owner_cli")["deferred"] == 1 and calls == [1]  # backoff: not due yet
    _shift(monkeypatch, hours=2)
    broken[0] = False
    again = pipeline.run(services, trigger="owner_cli")
    assert again["parsed"] == 1 and calls == [1, 1] and services.ocr.starts == 2  # due again: OCR'd and parsed


def test_thread_error_from_the_caller_carries_no_extraction_exception(world, tmp_path, db_session, monkeypatch, worker_log):
    """Nit (dev-grok final re-check): an OCRThreadError raised at the real call site, with a content-bearing
    NoTextLayer handled just before, has no canary in its context, cause, traceback or the log."""
    from worker import ocr as ocr_mod
    services, _, _ = _ocr_world(world, tmp_path, fake_ocr_child.ok())
    monkeypatch.setattr(pipeline.pdf, "extract", lambda data, password, **kw: (_ for _ in ()).throw(pipeline.pdf.NoTextLayer(CANARY)))
    seen = []

    def read(data, password):
        exc = ocr_mod.OCRThreadError("ocr engine used off its owning thread")
        seen.append(exc)
        raise exc
    services.ocr.read = read
    summary = pipeline.run(services, trigger="owner_cli")
    assert "file_error" in summary["errors"] and _file_rows(db_session) == [("failed", "transient")]  # contained per file
    assert len(seen) == 1
    _assert_canary_free(seen[0], worker_log)


def test_requeued_identical_card_file_replays_and_counts_no_live_revision(world, db_session):
    services, *_ = world
    assert pipeline.run(services, trigger="owner_cli")["live_revisions"] == 1
    db_session.execute(text("update statement_file set status = 'new', failure = null"))
    db_session.commit()
    again = pipeline.run(services, trigger="owner_cli")
    assert again["parsed"] == 1 and again["live_revisions"] == 0  # the identical resubmission replayed (spec §5)
    assert _counts(db_session)["statement_revision"] == 1


# --- composite statements (spec §4): one revision per declared section, no parser call ---------------------------------


@pytest.fixture
def composite_world(world, client, seed, db_session, card, monkeypatch):
    """The `world` drive plus one composite PDF under the manual root; `pdf.extract_lines` returns the synthetic
    statement (the ASCII PDF only has to pass unlock/extract); the parser raises if ever called."""
    services, drive_files, cfg = world
    accounts = {k: seed.account(k.replace("/", "_"), is_credit=False, currency=k.split("/")[1],
                                statement_live_from=date(2026, 9, 1) if k == "59/TWD" else None) for k in DECLARED}
    db_session.commit()
    drive_files["id-c"] = ("國泰世華/2609.pdf", make_pdf([statement_lines()]))
    composite_map = {"manual/國泰世華": {k: {"account_id": a.id, "anchor_only": DECLARED[k]} for k, a in accounts.items()}}
    response = client.put("/settings/reconciliation", headers={"Authorization": "Bearer spa-token"},
                          json={"account_map": {"mail/信用卡/國泰世華": card.id}, "composite_map": composite_map,
                                "dirty_enabled": False, "rules": {}})
    assert response.status_code == 200, response.text
    monkeypatch.setattr(pdf, "extract_lines", lambda data, password, **kw: composite_statement())
    monkeypatch.setattr(services.parser, "parse", lambda *a, **k: (_ for _ in ()).throw(AssertionError("parser called")))
    return services, drive_files, cfg, accounts


def _statements(db_session, account_id):
    return db_session.execute(text("select id, mode, current_revision_id, opening_balance, statement_total from account_statement "
                                   "where account_id = :a order by id"), {"a": account_id}).all()


def _bank_row(db_session):
    return db_session.execute(text("select status, failure from statement_file where kind = 'bank'")).one()


def test_composite_file_posts_one_statement_per_declared_section_without_the_parser(composite_world, db_session):
    services, drive_files, cfg, accounts = composite_world
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["composite_files"] == 1 and summary["sections_parsed"] == 2 and summary["sections_anchor"] == 2
    assert summary["sections_skipped"] == 1 and summary["sections_failed"] == 0 and summary["sections_replayed"] == 0
    sections = summary["composite"][0]["sections"]
    # the row sections in document order, then the anchors (in the stored map's key order), then the skipped keys
    assert sections[:2] == [{"key": "91/TWD", "outcome": "parsed", "reason": None},
                            {"key": "59/TWD", "outcome": "parsed", "reason": None}]
    assert sorted(sections[2:4], key=lambda s: s["key"]) == [{"key": "50/JPY", "outcome": "anchor", "reason": None},
                                                             {"key": "68/TWD", "outcome": "anchor", "reason": None}]
    assert sections[4:] == [{"key": "50/USD", "outcome": "skipped", "reason": "unmapped"}]
    assert summary["live_revisions"] == 1  # the live 59/TWD section (the card file hits the raising parser stub)
    rows = {r.key: r for r in db_session.execute(text(
        "select r.raw->>'section' as key, r.parser, r.guardrail_ok, s.mode, s.account_id, f.account_id as file_account "
        "from statement_revision r join account_statement s on s.id = r.statement_id join statement_file f on f.id = r.file_id "
        "where r.raw ? 'section'")).all()}
    assert set(rows) == {"91/TWD", "59/TWD", "68/TWD", "50/JPY"} and all(r.guardrail_ok for r in rows.values())
    assert rows["91/TWD"].parser == "composite-rows" and rows["68/TWD"].parser == "composite-anchor"
    assert rows["59/TWD"].mode == "live" and rows["91/TWD"].mode == "historical"
    assert all(r.file_account is None for r in rows.values())
    assert _bank_row(db_session) == ("parsed", None)
    # the card file hit the raising parser stub: contained, failed/transient
    assert db_session.execute(text("select status, failure from statement_file where kind = 'card'")).one() == ("failed", "transient")
    # registered once, from the manual root (AGENT-159 Must 2: the listings partition the drive)
    sources = db_session.execute(text("select src.root from statement_source src join statement_file f on f.id = src.file_id "
                                      "where f.kind = 'bank'")).scalars().all()
    assert sources == ["manual"]
    # second run: a parsed file is not due, so the composite path does not run
    again = pipeline.run(services, trigger="owner_cli")
    assert again["unchanged"] >= 1 and again.get("composite_files", 0) == 0


def test_composite_partial_failure_keeps_earlier_sections_and_requeue_replays_them(composite_world, db_session, monkeypatch):
    services, drive_files, cfg, accounts = composite_world
    real = services.api.submit_revision
    calls = []

    def flaky(lease, body):
        calls.append(body["raw"]["section"])
        if body["raw"]["section"] == "59/TWD" and calls.count("59/TWD") == 1:
            raise ApiError(422, {"detail": "lines"})
        return real(lease, body)

    monkeypatch.setattr(services.api, "submit_revision", flaky)
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["sections_failed"] == 1 and summary["sections_parsed"] == 1 and summary["sections_anchor"] == 2
    assert [s for s in summary["composite"][0]["sections"] if s["key"] == "59/TWD"][0] == {"key": "59/TWD", "outcome": "parse", "reason": "422"}
    assert _bank_row(db_session) == ("failed", "parse")
    assert len(_statements(db_session, accounts["91/TWD"].id)) == 1 and _statements(db_session, accounts["59/TWD"].id) == []
    revisions = db_session.execute(text("select count(*) from statement_revision")).scalar_one()
    db_session.execute(text("update statement_file set status = 'new', failure = null where failure = 'parse'"))
    db_session.commit()
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["sections_replayed"] == 3 and summary["sections_parsed"] == 1 and summary["sections_failed"] == 0
    assert summary["live_revisions"] == 1  # the 59/TWD revision only: replays are not revisions
    assert len(_statements(db_session, accounts["91/TWD"].id)) == 1 and len(_statements(db_session, accounts["59/TWD"].id)) == 1
    assert db_session.execute(text("select count(*) from statement_revision")).scalar_one() == revisions + 1
    assert _bank_row(db_session) == ("parsed", None)


def test_composite_split_error_fails_the_file_as_parse(composite_world, db_session, monkeypatch):
    services, *_ = composite_world
    monkeypatch.setattr(pdf, "extract_lines", lambda data, password, **kw: composite_statement(holdings={**HOLDINGS, "91/TWD": "1"}))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["failed"] == 1 and summary["composite"][0]["sections"] == [{"key": None, "outcome": "parse", "reason": "split_error"}]
    assert summary["sections_failed"] == 1 and _bank_row(db_session) == ("failed", "parse")
    assert db_session.execute(text("select count(*) from statement_revision where raw ? 'section'")).scalar_one() == 0


def test_composite_guardrail_failure_is_needs_review(composite_world, db_session, monkeypatch):
    services, *_ = composite_world
    real = services.api.submit_revision

    def off_by_one(lease, body):
        if body["raw"]["section"] == "59/TWD":  # the live section
            body = {**body, "statement_total": "52753"}
        return real(lease, body)

    monkeypatch.setattr(services.api, "submit_revision", off_by_one)
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["needs_review"] == 1 and _bank_row(db_session) == ("needs_review", "guardrail")
    assert [s for s in summary["composite"][0]["sections"] if s["key"] == "59/TWD"][0]["outcome"] == "guardrail"
    assert summary["sections_failed"] == 1 and summary["sections_parsed"] == 1
    assert summary["live_revisions"] == 1  # a new live revision counts even when it fails the guardrail
    # re-queued unchanged: the 59/TWD resubmission replays its failed current revision and still ranks `guardrail`
    db_session.execute(text("update statement_file set status = 'new', failure = null where kind = 'bank'"))
    db_session.commit()
    summary = pipeline.run(services, trigger="owner_cli")
    outcomes = {s["key"]: s["outcome"] for s in summary["composite"][0]["sections"]}
    assert outcomes == {"91/TWD": "replayed", "59/TWD": "guardrail", "68/TWD": "replayed", "50/JPY": "replayed",
                        "50/USD": "skipped"}
    assert summary["live_revisions"] == 0 and _bank_row(db_session) == ("needs_review", "guardrail")


def test_composite_runs_while_the_parser_is_latched_and_skips_ocr(composite_world, db_session, monkeypatch):
    services, *_ = composite_world
    # the run resets parser_disabled at its start; the gate sets it for the run, before any file is processed
    monkeypatch.setattr(services.gate, "ensure", lambda parser: (_ for _ in ()).throw(parser_mod.ParserDisabled("latched")))
    summary = pipeline.run(services, trigger="owner_cli")
    assert "parser_disabled" in summary["errors"] and summary["skipped"] == 1  # the card file waits for the parser
    assert summary["composite_files"] == 1 and summary["sections_parsed"] == 2  # composite needs no parser
    assert _bank_row(db_session) == ("parsed", None)
    monkeypatch.setattr(pdf, "extract", lambda data, password, **kw: (_ for _ in ()).throw(pdf.NoTextLayer("none")))
    db_session.execute(text("update statement_file set status = 'new', failure = null where kind = 'bank'"))
    db_session.commit()
    summary = pipeline.run(services, trigger="owner_cli")
    assert _bank_row(db_session) == ("needs_review", "no_text_layer") and summary["ocr_trigger"] == 0
    assert summary.get("composite_files", 0) == 0


@pytest.mark.parametrize("target", ["extract", "extract_lines"])
def test_composite_pdf_failures_keep_their_classes(composite_world, db_session, monkeypatch, target):
    services, *_ = composite_world
    for exc, expected in ((pdf.PasswordError("password"), ("failed", "password")), (pdf.Malformed("m"), ("failed", "parse")),
                          (pdf.TooLarge("t"), ("needs_review", "too_large")), (pdf.ExtractTimeout("t"), ("failed", "transient"))):
        monkeypatch.setattr(pdf, target, lambda data, password, _e=exc, **kw: (_ for _ in ()).throw(_e))
        db_session.execute(text("update statement_file set status = 'new', failure = null, attempts = 0"))
        db_session.commit()
        summary = pipeline.run(services, trigger="owner_cli")
        assert _bank_row(db_session) == expected, (target, expected)
        if target == "extract_lines":
            assert summary["composite_files"] == 0 and summary["composite"] == []  # the file never reached the split
        db_session.expire_all()


def test_composite_map_change_requeues_only_mapping_failures(composite_world, client, db_session, card):
    services, drive_files, cfg, accounts = composite_world
    client.put("/settings/reconciliation", headers={"Authorization": "Bearer spa-token"},
               json={"account_map": {"mail/信用卡/國泰世華": card.id}, "composite_map": {}, "dirty_enabled": False, "rules": {}})
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["ignored"] == 1 and _bank_row(db_session) == ("ignored", "mapping")
    # a parse failure at the current parser version (not due again); the stub parser never parsed the card file
    db_session.execute(text("update statement_file set status = 'failed', failure = 'parse', parser_version = :v "
                            "where kind = 'card'"), {"v": services.parser_version})
    db_session.commit()
    composite_map = {"manual/國泰世華": {k: {"account_id": a.id, "anchor_only": DECLARED[k]} for k, a in accounts.items()}}
    client.put("/settings/reconciliation", headers={"Authorization": "Bearer spa-token"},
               json={"account_map": {"mail/信用卡/國泰世華": card.id}, "composite_map": composite_map, "dirty_enabled": False, "rules": {}})
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["composite_files"] == 1 and _bank_row(db_session) == ("parsed", None)
    assert db_session.execute(text("select status, failure from statement_file where kind = 'card'")).one() == ("failed", "parse")
