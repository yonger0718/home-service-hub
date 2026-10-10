import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app import auth
from tests.integration.test_statement_ingest_api import SCOPES, TOKENS
from tests.worker import fake_claude
from tests.worker.pdfgen import encrypt, make_pdf, statement_lines
from worker import config, parse_schema, verify
from worker.runner import FakeRunner, Result, SubprocessRunner


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


TABLES = ("statement_coverage", "reconciliation_case", "account_statement", "statement_revision", "ledger_entry",
          "coverage_dirty", "reconciliation_settings")


def _counts(db_session):
    return {t: db_session.execute(text(f"select count(*) from {t}")).scalar_one() for t in TABLES}


def test_dry_run_match_claims_an_exact_entry_without_writing(db_session, seed):
    account = seed.account("卡", is_credit=True)
    seed.entry(account, "-80", day=date(2026, 9, 4), name="COFFEE")
    seed.entry(account, "-500", day=date(2026, 9, 10), name="BOOKS")
    db_session.commit()
    before = _counts(db_session)
    parsed = parse_schema.StatementParse.model_validate(fake_claude.GOOD)
    dry = verify.dry_run_match(db_session, account.id, parsed)
    assert dry.guard.ok and len(dry.result.claims) == 2 and dry.result.cases == [] and dry.existing_statement_id is None
    db_session.rollback()
    assert _counts(db_session) == before


def test_dry_run_skips_matching_on_guardrail_failure(db_session, seed):
    account = seed.account("卡", is_credit=True)
    db_session.commit()
    parsed = parse_schema.StatementParse.model_validate({**fake_claude.GOOD, "statement_total": "999"})
    dry = verify.dry_run_match(db_session, account.id, parsed)
    assert not dry.guard.ok and dry.result is None


def test_dry_run_respects_currency_and_combined_accounts(db_session, seed):
    account = seed.account("卡", is_credit=True)
    usd_child = seed.account("USD子", currency="USD", combined_account_id=account.id)
    seed.entry(usd_child, "-80", day=date(2026, 9, 4), name="COFFEE")  # same number, wrong currency
    db_session.commit()
    dry = verify.dry_run_match(db_session, account.id, parse_schema.StatementParse.model_validate(fake_claude.GOOD))
    assert dry.result.claims == []


def test_dry_run_of_an_existing_statement_excludes_its_own_claims(client, db_session, seed, card):
    from tests.integration.test_statement_ingest_api import _claim, _revision
    seed.entry(card, "-80", day=date(2026, 9, 4), name="COFFEE")
    seed.entry(card, "-500", day=date(2026, 9, 10), name="BOOKS")
    db_session.commit()
    lease = _claim(client)
    body = {**_revision(lease, card, fake_claude.GOOD["lines"], "580"), "period_start": "2026-09-01", "period_end": "2026-09-30"}
    assert client.post("/statements/revisions", headers={"Authorization": "Bearer worker-token"}, json=body).status_code == 201
    statement_id = db_session.execute(text("select id from account_statement")).scalar_one()
    client.post(f"/accounts/{card.id}/statements/{statement_id}/reconcile", headers={"Authorization": "Bearer spa-token"})
    assert db_session.execute(text("select count(*) from statement_coverage where status='active'")).scalar_one() == 2
    dry = verify.dry_run_match(db_session, card.id, parse_schema.StatementParse.model_validate(fake_claude.GOOD))
    assert dry.existing_statement_id == statement_id and len(dry.result.claims) == 2  # not "claimed by another"


def test_export_masked_writes_snapshots_only(tmp_path):
    pdf_bytes = encrypt(make_pdf([statement_lines()]), "A123456789")

    def rclone(args, stdin):
        if args[1] == "lsjson":
            if not args[-1].endswith("/銀行"):
                return Result(0, b"[]", b"")
            import hashlib
            return Result(0, json.dumps([{"Path": "信用卡/國泰世華/2026-09.pdf", "Name": "2026-09.pdf", "Size": len(pdf_bytes),
                                          "ModTime": "x", "Hashes": {"md5": hashlib.md5(pdf_bytes).hexdigest()},
                                          "ID": "id-1", "MimeType": "application/pdf"}]).encode(), b"")
        from pathlib import Path
        Path(args[5]).write_bytes(pdf_bytes)
        return Result(0, b"", b"")

    pw = tmp_path / "pw.env"
    pw.write_text("STATEMENT_ID_NUMBER=A123456789\nSTATEMENT_BIRTH_DATE=19900101\nSTATEMENT_HOLDER_NAMES=王小明\n"
                  "信用卡/國泰世華=$ID\n", encoding="utf-8")
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path / "s"), "STATEMENT_VERIFY_DIR": str(tmp_path / "v"),
                       "STATEMENT_PASSWORD_FILE": str(pw)})
    out = verify.export_masked(cfg, FakeRunner({"rclone": rclone}), folder=None, limit=None)
    assert out["exported"] == 1 and out["password"] == 0
    snaps = list((tmp_path / "v" / "masked").glob("*.txt"))
    assert len(snaps) == 1 and oct(snaps[0].stat().st_mode)[-3:] == "600"
    assert oct((tmp_path / "v" / "masked").stat().st_mode)[-3:] == "700"
    assert "A123456789" not in snaps[0].read_text(encoding="utf-8")
    assert not (tmp_path / "s" / "inbox").exists()
    assert verify.export_masked(cfg, FakeRunner({"rclone": rclone}), folder=None, limit=None)["exported"] == 0
    # replacement bytes under the same Drive id are exported again
    pdf_bytes = encrypt(make_pdf([statement_lines(26)]), "A123456789")
    assert verify.export_masked(cfg, FakeRunner({"rclone": rclone}), folder=None, limit=None)["exported"] == 1
    assert len(list((tmp_path / "v" / "masked").glob("*.txt"))) == 2


def test_export_masked_refuses_mismatched_bytes(tmp_path):
    pdf_bytes = encrypt(make_pdf([statement_lines()]), "A123456789")

    def rclone(args, stdin):
        if args[1] == "lsjson":
            if not args[-1].endswith("/銀行"):
                return Result(0, b"[]", b"")
            return Result(0, json.dumps([{"Path": "信用卡/國泰世華/x.pdf", "Name": "x.pdf", "Size": len(pdf_bytes),
                                          "ModTime": "x", "Hashes": {"md5": "00" * 16}, "ID": "id-1",
                                          "MimeType": "application/pdf"}]).encode(), b"")
        from pathlib import Path
        Path(args[5]).write_bytes(pdf_bytes)
        return Result(0, b"", b"")

    pw = tmp_path / "pw.env"
    pw.write_text("STATEMENT_ID_NUMBER=A123456789\n信用卡/國泰世華=$ID\n", encoding="utf-8")
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path / "s"), "STATEMENT_VERIFY_DIR": str(tmp_path / "v"),
                       "STATEMENT_PASSWORD_FILE": str(pw)})
    out = verify.export_masked(cfg, FakeRunner({"rclone": rclone}), folder=None, limit=None)
    assert out["exported"] == 0 and out["errors"] == 1 and not list((tmp_path / "v" / "masked").glob("*"))


@pytest.fixture
def ro_url(pg_engine):
    """A SELECT-only role (ruling 11) with a unique, fixture-owned name; only what this fixture created is dropped."""
    import uuid
    name = f"verify_ro_{uuid.uuid4().hex[:12]}"
    created = False
    try:
        with pg_engine.connect() as conn:
            conn.execute(text(f"CREATE ROLE {name} LOGIN PASSWORD 'ro'"))
            created = True
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {name}"))
            conn.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {name}"))
            conn.execute(text(f"ALTER ROLE {name} SET default_transaction_read_only = on"))
            conn.commit()
        yield pg_engine.url.set(username=name, password="ro").render_as_string(hide_password=False)
    finally:
        if created:
            with pg_engine.connect() as conn:
                conn.execute(text(f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {name}"))
                conn.execute(text(f"REVOKE ALL PRIVILEGES ON SCHEMA public FROM {name}"))
                conn.execute(text(f"DROP ROLE {name}"))
                conn.commit()


def _state_digest(db_session) -> dict:
    """Content digest of EVERY public table (counts miss UPDATEs): md5 over all rows in their text form."""
    names = db_session.execute(text("select table_name from information_schema.tables where table_schema='public' "
                                    "and table_type='BASE TABLE' and table_name <> 'alembic_version'")).scalars().all()
    return {t: db_session.execute(text(f'select coalesce(md5(string_agg(x::text, \'|\' order by x::text)), \'empty\') '
                                       f'from (select r.* from "{t}" r) x')).scalar_one() for t in names}


def test_verify_requires_the_read_only_url(tmp_path):
    cfg = config.load({"STATEMENT_VERIFY_DIR": str(tmp_path / "v")})
    with pytest.raises(Exception, match="STATEMENT_VERIFY_DB_URL"):
        verify.run(cfg, SubprocessRunner(), limit=None)


def test_verify_run_reports_aggregates_and_caches_parses(tmp_path, db_session, seed, pg_engine, ro_url, monkeypatch):
    account = seed.account("卡", is_credit=True)
    seed.entry(account, "-80", day=date(2026, 9, 4), name="COFFEE")
    db_session.commit()
    masked = tmp_path / "v" / "masked"
    masked.mkdir(parents=True)
    (masked / ("ab" * 32 + ".txt")).write_text("masked statement", encoding="utf-8")
    (masked / ("ab" * 32 + ".json")).write_text(json.dumps({"root": "mail", "folder": "信用卡/國泰世華", "name": "n",
                                                            "kind": "card", "drive_file_id": "id-1"}), encoding="utf-8")
    (tmp_path / "map.json").write_text(json.dumps({"mail/信用卡/國泰世華": account.id}), encoding="utf-8")
    cli = fake_claude.write(tmp_path / "claude", fake_claude.transcript())
    cfg = config.load({"STATEMENT_VERIFY_DIR": str(tmp_path / "v"), "STATEMENT_STATE_DIR": str(tmp_path / "s"),
                       "STATEMENT_PARSER_CLI": str(cli), "STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_PARSER_ALLOW_UNSANDBOXED": "true",
                       "STATEMENT_PARSER_TIMEOUT": "5", "STATEMENT_PARSER_ATTEMPTS": "1",
                       "STATEMENT_ACCOUNT_MAP_FILE": str(tmp_path / "map.json"), "STATEMENT_VERIFY_DB_URL": ro_url})
    before = _state_digest(db_session)
    totals = verify.run(cfg, SubprocessRunner(), limit=None)
    assert totals["statements"] == 1 and totals["lines"] == 2 and totals["claims"] == 1 and totals["unmatched_lines"] == 1
    report = json.loads(next((tmp_path / "v" / "reports").glob("*.json")).read_text(encoding="utf-8"))
    assert report["rows"][0]["account_id"] == account.id and "merchant" not in json.dumps(report).lower()
    assert "COFFEE" not in json.dumps(report) and "BOOKS" not in json.dumps(report)
    assert len(list((tmp_path / "v" / "parsed").glob("*.json"))) == 1 and len(list((tmp_path / "s").glob("gate-*.json"))) == 1
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(extra_tool=True))  # would fail if re-parsed
    assert verify.run(cfg, SubprocessRunner(), limit=None)["statements"] == 1
    assert _state_digest(db_session) == before


def test_verify_latches_on_a_real_input_violation(tmp_path, db_session, seed, ro_url):
    account = seed.account("卡", is_credit=True)
    db_session.commit()
    masked = tmp_path / "v" / "masked"
    masked.mkdir(parents=True)
    (masked / ("ef" * 32 + ".txt")).write_text("masked", encoding="utf-8")
    (masked / ("ef" * 32 + ".json")).write_text(json.dumps({"root": "mail", "folder": "信用卡/國泰世華", "name": "n",
                                                            "kind": "card", "drive_file_id": "id-8", "md5": "0" * 32}), encoding="utf-8")
    (tmp_path / "map.json").write_text(json.dumps({"mail/信用卡/國泰世華": account.id}), encoding="utf-8")
    cli = fake_claude.write(tmp_path / "claude", fake_claude.transcript())
    cfg = config.load({"STATEMENT_VERIFY_DIR": str(tmp_path / "v"), "STATEMENT_STATE_DIR": str(tmp_path / "s"),
                       "STATEMENT_PARSER_CLI": str(cli), "STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_PARSER_ALLOW_UNSANDBOXED": "true",
                       "STATEMENT_PARSER_TIMEOUT": "5", "STATEMENT_PARSER_ATTEMPTS": "1",
                       "STATEMENT_ACCOUNT_MAP_FILE": str(tmp_path / "map.json"), "STATEMENT_VERIFY_DB_URL": ro_url})
    from worker import parser as parser_mod
    parser_mod.Gate(cfg).ensure(parser_mod.Parser(cfg, SubprocessRunner(), config_dir=cfg.verify_parser_config_dir,
                                                  credentials_writable=False))  # good canary cached (verify key)
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(extra_tool=True))
    totals = verify.run(cfg, SubprocessRunner(), limit=None)
    assert totals["errors"] == ["sandbox"] and (tmp_path / "s" / "parser-disabled.json").exists()
    with pytest.raises(parser_mod.ParserDisabled):
        verify.run(cfg, SubprocessRunner(), limit=None)


def test_verify_without_map_file_reads_settings_read_only(tmp_path, db_session, seed, ro_url, client, card):
    """Settings with dirty triggers on, a live account and a reconciled statement with coverage exist (the closest
    R2 can get to "policies enabled": the policy engine itself is R1c, so this acceptance is re-run there with a
    seeded enabled policy); verify reads SELECT-only and the content digest of every table is unchanged."""
    from tests.integration.test_statement_ingest_api import _claim, _revision
    account = card
    seed.entry(card, "-80", day=date(2026, 9, 4), name="COFFEE")
    db_session.commit()
    client.put("/settings/reconciliation", headers={"Authorization": "Bearer spa-token"},
               json={"account_map": {"mail/信用卡/國泰世華": account.id}, "dirty_enabled": True, "rules": {}})
    lease = _claim(client)
    body = {**_revision(lease, card, fake_claude.GOOD["lines"][:1], "80"), "period_start": "2026-09-01", "period_end": "2026-09-30"}
    assert client.post("/statements/revisions", headers={"Authorization": "Bearer worker-token"}, json=body).status_code == 201
    sid = db_session.execute(text("select id from account_statement")).scalar_one()
    client.post(f"/accounts/{card.id}/statements/{sid}/reconcile", headers={"Authorization": "Bearer spa-token"})
    masked = tmp_path / "v" / "masked"
    masked.mkdir(parents=True)
    (masked / ("cd" * 32 + ".txt")).write_text("masked", encoding="utf-8")
    (masked / ("cd" * 32 + ".json")).write_text(json.dumps({"root": "mail", "folder": "信用卡/國泰世華", "name": "n",
                                                            "kind": "card", "drive_file_id": "id-9", "md5": "0" * 32}), encoding="utf-8")
    cli = fake_claude.write(tmp_path / "claude", fake_claude.transcript())
    cfg = config.load({"STATEMENT_VERIFY_DIR": str(tmp_path / "v"), "STATEMENT_STATE_DIR": str(tmp_path / "s"),
                       "STATEMENT_PARSER_CLI": str(cli), "STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_PARSER_ALLOW_UNSANDBOXED": "true",
                       "STATEMENT_PARSER_TIMEOUT": "5", "STATEMENT_PARSER_ATTEMPTS": "1", "STATEMENT_VERIFY_DB_URL": ro_url})
    before = _state_digest(db_session)
    assert verify.run(cfg, SubprocessRunner(), limit=None)["statements"] == 1
    assert _state_digest(db_session) == before


def _snapshot(masked, sha, folder="信用卡/國泰世華", drive_id="id-x"):
    masked.mkdir(parents=True, exist_ok=True)
    (masked / f"{sha}.txt").write_text("masked", encoding="utf-8")
    (masked / f"{sha}.json").write_text(json.dumps({"root": "mail", "folder": folder, "name": "n", "kind": "card",
                                                    "drive_file_id": drive_id, "md5": "0" * 32}), encoding="utf-8")


def _verify_cfg(tmp_path, cli, ro_url, map_name="map.json"):
    return config.load({"STATEMENT_VERIFY_DIR": str(tmp_path / "v"), "STATEMENT_STATE_DIR": str(tmp_path / "s"),
                        "STATEMENT_PARSER_CLI": str(cli), "STATEMENT_PARSER_SANDBOX": "false",
                        "STATEMENT_PARSER_ALLOW_UNSANDBOXED": "true", "STATEMENT_PARSER_TIMEOUT": "5",
                        "STATEMENT_PARSER_ATTEMPTS": "1", "STATEMENT_ACCOUNT_MAP_FILE": str(tmp_path / map_name),
                        "STATEMENT_VERIFY_DB_URL": ro_url})


def test_verify_leaves_the_worker_gate_evidence_untouched(tmp_path, db_session, seed, ro_url):
    from worker import parser as parser_mod
    account = seed.account("卡", is_credit=True)
    db_session.commit()
    _snapshot(tmp_path / "v" / "masked", "ab" * 32)
    (tmp_path / "map.json").write_text(json.dumps({"mail/信用卡/國泰世華": account.id}), encoding="utf-8")
    cli = fake_claude.write(tmp_path / "claude", fake_claude.transcript())
    cfg = _verify_cfg(tmp_path, cli, ro_url)
    gate = parser_mod.Gate(cfg)
    worker_parser = parser_mod.Parser(cfg, SubprocessRunner())
    gate.ensure(worker_parser)
    worker_file = gate.evidence_path_for(gate.key(worker_parser))
    before = worker_file.read_bytes()
    verify.run(cfg, SubprocessRunner(), limit=None)
    verify_parser = parser_mod.Parser(cfg, SubprocessRunner(), config_dir=cfg.verify_parser_config_dir, credentials_writable=False)
    assert gate.evidence_path_for(gate.key(verify_parser)) != worker_file and worker_file.read_bytes() == before


def test_verify_contains_a_per_statement_failure(tmp_path, db_session, seed, ro_url):
    account = seed.account("卡", is_credit=True)
    db_session.commit()
    masked = tmp_path / "v" / "masked"
    _snapshot(masked, "ab" * 32)
    (masked / ("cd" * 32 + ".txt")).write_text("masked", encoding="utf-8")
    (masked / ("cd" * 32 + ".json")).write_text("{not json", encoding="utf-8")
    _snapshot(masked, "ef" * 32, drive_id="id-y")
    (tmp_path / "map.json").write_text(json.dumps({"mail/信用卡/國泰世華": account.id + 9999}), encoding="utf-8")
    cli = fake_claude.write(tmp_path / "claude", fake_claude.transcript())
    totals = verify.run(_verify_cfg(tmp_path, cli, ro_url), SubprocessRunner(), limit=None)
    assert totals["verify_errors"] == 3 and totals["statements"] == 0
    report = json.loads(next((tmp_path / "v" / "reports").glob("*.json")).read_text(encoding="utf-8"))
    assert all(r["error"] == "verify_error" and set(r) == {"sha256", "account_id", "error"} for r in report["rows"])
    assert sorted(r["account_id"] is None for r in report["rows"]) == [False, False, True]
    assert len(report["rows"]) == 3


@pytest.mark.parametrize("content,bad", [({"mail/x": 1}, "mail/x"), ({"mail/a/b": True}, "mail/a/b"),
                                          ({"mail/a/b": "1"}, "mail/a/b"), ({"manual/a/b": 1}, "manual/a/b")])
def test_account_map_file_is_validated(tmp_path, content, bad):
    (tmp_path / "map.json").write_text(json.dumps(content), encoding="utf-8")
    cfg = config.load({"STATEMENT_ACCOUNT_MAP_FILE": str(tmp_path / "map.json")})
    with pytest.raises(verify.Refused, match="account map file") as exc:
        verify._account_map(cfg, None)
    assert bad in str(exc.value)


def _drive_world(tmp_path, pdfs, pw_text="STATEMENT_ID_NUMBER=A123456789\n信用卡/國泰世華=$ID\n", failing=False):
    import hashlib
    from pathlib import Path

    def rclone(args, stdin):
        if args[1] == "lsjson":
            if failing:
                return Result(1, b"", b"boom")
            if not args[-1].endswith("/銀行"):
                return Result(0, b"[]", b"")
            return Result(0, json.dumps([{"Path": f"信用卡/國泰世華/{i}.pdf", "Name": f"{i}.pdf", "Size": len(b),
                                          "ModTime": "x", "Hashes": {"md5": hashlib.md5(b).hexdigest()}, "ID": i,
                                          "MimeType": "application/pdf"} for i, b in pdfs.items()]).encode(), b"")
        Path(args[5]).write_bytes(pdfs[args[4]])
        return Result(0, b"", b"")

    pw = tmp_path / "pw.env"
    pw.write_text(pw_text, encoding="utf-8")
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path / "s"), "STATEMENT_VERIFY_DIR": str(tmp_path / "v"),
                       "STATEMENT_PASSWORD_FILE": str(pw)})
    return cfg, FakeRunner({"rclone": rclone})


def test_export_masked_listing_error_and_limit(tmp_path):
    cfg, runner = _drive_world(tmp_path, {}, failing=True)
    out = verify.export_masked(cfg, runner, folder=None, limit=None)
    assert out["errors"] == 1 and out["listing_failed"] is True and out["exported"] == 0
    pdfs = {"a": encrypt(make_pdf([statement_lines()]), "A123456789"), "b": encrypt(make_pdf([statement_lines(26)]), "A123456789")}
    cfg, runner = _drive_world(tmp_path, pdfs)
    out = verify.export_masked(cfg, runner, folder=None, limit=1)
    assert out["listed"] == 2 and out["exported"] == 1


def test_export_masked_cap_counts_too_large(tmp_path, monkeypatch):
    from worker import parser as parser_mod
    cfg, runner = _drive_world(tmp_path, {"a": encrypt(make_pdf([statement_lines()]), "A123456789")})
    monkeypatch.setattr(parser_mod, "STDIN_CAP", 5)
    out = verify.export_masked(cfg, runner, folder=None, limit=None)
    assert out["too_large"] == 1 and out["exported"] == 0 and not list((tmp_path / "v" / "masked").glob("*.json"))


def test_zero_amount_purchase_is_a_guardrail_failure(tmp_path, db_session, seed, ro_url):
    account = seed.account("卡", is_credit=True)
    db_session.commit()
    bad = {**fake_claude.GOOD, "lines": [{**fake_claude.GOOD["lines"][0], "printed_amount": "0"}], "statement_total": "0"}
    _snapshot(tmp_path / "v" / "masked", "ab" * 32)
    (tmp_path / "map.json").write_text(json.dumps({"mail/信用卡/國泰世華": account.id}), encoding="utf-8")
    cli = fake_claude.write(tmp_path / "claude", fake_claude.transcript(bad))
    totals = verify.run(_verify_cfg(tmp_path, cli, ro_url), SubprocessRunner(), limit=None)
    assert totals["guardrail_failed"] == 1 and totals["verify_errors"] == 0
    row = json.loads(next((tmp_path / "v" / "reports").glob("*.json")).read_text(encoding="utf-8"))["rows"][0]
    assert row["account_id"] == account.id and row["guardrail_ok"] is False


def test_kind_of():
    from worker.drive import Listed, kind_of
    mk = lambda root, path: Listed("i", path, "x.pdf", 1, "0" * 32, "", root)
    assert kind_of(mk("mail", "信用卡/國泰世華/x.pdf")) == "card"
    assert kind_of(mk("mail", "銀行帳戶/國泰/x.pdf")) == "bank"
    assert kind_of(mk("manual", "國泰/x.pdf")) == "bank"
