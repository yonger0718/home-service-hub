"""Verify mode (§5.8, rulings 6, 11, 12): masked snapshots from the worker, then parse + pure matching, read-only."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from worker import drive as drive_mod
from worker import inbox as inbox_mod
from worker import mask, parse_schema, parser as parser_mod, passwords, pdf
from worker.config import WorkerConfig
from worker.runner import Runner


class Refused(RuntimeError):
    pass


def _write_private(path: Path, data: str) -> None:
    inbox_mod.private_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        os.fchmod(fd, 0o600)
        fh.write(data)
    os.replace(tmp, path)


def export_masked(cfg: WorkerConfig, runner: Runner, *, folder: str | None, limit: int | None) -> dict:
    candidates, _ = passwords.load(cfg.password_file)
    masker = mask.Masker(passwords.identity(cfg.password_file))
    drv = drive_mod.Drive(cfg, runner)
    verify_cfg = dataclasses.replace(cfg, state_dir=cfg.verify_dir / "pdf-cache")  # own inbox + staging, never the run state dir
    box, index = inbox_mod.Inbox(verify_cfg), inbox_mod.SourceIndex(cfg.verify_dir / "sources.json")
    out = {"listed": 0, "exported": 0, "password": 0, "no_text_layer": 0, "too_large": 0, "errors": 0}
    masked_dir = inbox_mod.private_dir(cfg.verify_dir / "masked")
    try:
        listing = [i for i in drv.list_pdfs() if folder is None or drive_mod.folder_of(i).startswith(folder)]
    except drive_mod.ListingError:
        out["errors"] += 1
        out["listing_failed"] = True  # bounded code; the exception text (with paths) never leaves the process
        return out
    out["listed"] = len(listing)
    for item in listing:
        if limit is not None and out["exported"] >= limit:
            break
        if index.get(item.drive_file_id, item.md5):
            continue
        if item.size > inbox_mod.MAX_PDF_BYTES:
            out["too_large"] += 1
            continue
        staged = inbox_mod.private_dir(verify_cfg.staging_dir) / f"{item.drive_file_id}.tmp"
        try:
            drv.download(item, staged)
            published = box.publish(staged, expected_md5=item.md5, expected_size=item.size)
        except (drive_mod.DownloadError, inbox_mod.VerifyError, inbox_mod.CollisionError):
            out["errors"] += 1
            continue
        data = published.path.read_bytes()
        key = passwords.password_key(item.root, drive_mod.folder_of(item))
        try:
            unlocked = pdf.unlock(data, candidates.get(key, []))
            password = candidates[key][unlocked.password_index] if unlocked.password_index is not None else None
            extracted = pdf.extract(data, password)
        except pdf.PasswordError:
            out["password"] += 1
            continue
        except pdf.NoTextLayer:
            out["no_text_layer"] += 1
            continue
        except pdf.TooLarge:
            out["too_large"] += 1
            continue
        except (pdf.ExtractTimeout, pdf.Malformed):
            out["errors"] += 1
            continue
        rendered = masker.render(extracted)
        if len(rendered.encode("utf-8")) > parser_mod.STDIN_CAP:
            out["too_large"] += 1
            continue
        kind = "bank" if drive_mod.folder_of(item).startswith("銀行帳戶") else "card"
        _write_private(masked_dir / f"{published.sha256}.txt", rendered)
        _write_private(masked_dir / f"{published.sha256}.json", json.dumps(
            {"root": item.root, "folder": drive_mod.folder_of(item), "name": item.name, "kind": kind,
             "drive_file_id": item.drive_file_id, "md5": item.md5}, ensure_ascii=False))
        index.put(item.drive_file_id, item.md5, published.sha256)
        out["exported"] += 1
    return out


def _account_map(cfg: WorkerConfig, db: Session | None) -> dict[str, int]:
    if cfg.account_map_file is None:
        from app.services import settings_service

        return settings_service.read_reconciliation_settings(db).get("account_map") or {}
    raw = json.loads(cfg.account_map_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise Refused("account map file: not an object")
    for key, value in raw.items():
        parts = key.split("/") if isinstance(key, str) else []
        shape_ok = (len(parts), parts[0] if parts else None) in ((3, "mail"), (2, "manual"))
        if not shape_ok or any(not part or part != part.strip() for part in parts) \
                or isinstance(value, bool) or not isinstance(value, int):
            raise Refused(f"account map file: {key!r} invalid")
    return dict(raw)


@dataclass
class DryRun:
    result: object | None  # matching.MatchResult
    guard: object  # derive.GuardrailResult
    existing_statement_id: int | None


def dry_run_match(db: Session, account_id: int, parsed: parse_schema.StatementParse) -> DryRun:
    from app.models.ledger import Account
    from app.models.statements import AccountStatement
    from app.services import reconciliation_service, settings_service
    from app.services.coverage_service import participating_accounts
    from app.services.entry_write_service import proposed_fx_fee
    from app.services.statements import derive, matching

    accounts = participating_accounts(db, account_id)
    accounts_by_id = {a.id: a for a in db.execute(select(Account).where(Account.id.in_(accounts))).scalars()}
    account = accounts_by_id[account_id]
    line_ins = [derive.LineIn(seq=l.seq, txn_date=l.txn_date, posted_date=l.posted_date, merchant_raw=l.merchant_raw,
                              printed_amount=Decimal(l.printed_amount),
                              foreign_amount=Decimal(l.foreign_amount) if l.foreign_amount else None,
                              foreign_currency=l.foreign_currency, line_kind=l.line_kind,
                              installment_seq=l.installment_seq, installment_total=l.installment_total,
                              is_subtotal=l.is_subtotal) for l in parsed.lines if not l.is_subtotal]
    derived = derive.derive_lines(parsed.kind, line_ins)
    header = derive.Header(parsed.period_start, parsed.period_end, parsed.closing_date, parsed.due_date,
                           Decimal(parsed.opening_balance) if parsed.opening_balance else None,
                           Decimal(parsed.statement_total),
                           Decimal(parsed.minimum_payment) if parsed.minimum_payment else None, parsed.currency)
    guard = derive.guardrails(parsed.kind, header, account.currency, derived)
    existing = db.execute(select(AccountStatement.id).where(
        AccountStatement.account_id == account_id, AccountStatement.currency == parsed.currency,
        AccountStatement.period_end == parsed.period_end)).scalar_one_or_none()
    if not guard.ok:
        return DryRun(None, guard, existing)
    lines = [matching.Line(id=d.seq, event_id=d.seq, posted_date=d.line.posted_date, txn_date=d.line.txn_date,
                           flow=d.flow_amount, foreign_amount=d.line.foreign_amount,
                           foreign_currency=d.line.foreign_currency, line_kind=d.line.line_kind,
                           merchant_norm=d.merchant_norm, installment_seq=d.line.installment_seq,
                           installment_total=d.line.installment_total) for d in derived]
    rules = reconciliation_service.rules_for(settings_service.read_reconciliation_rules(db), parsed.period_end)
    stand_in = SimpleNamespace(id=existing or 0, account_id=account_id, period_start=parsed.period_start,
                               period_end=parsed.period_end, current_revision_id=None, kind=parsed.kind,
                               currency=parsed.currency)
    entries, groups = reconciliation_service.load_candidates(
        db, stand_in, accounts, window_days=rules.candidate_window_days,
        include_reward=any(l.line_kind == "reward" for l in lines))
    result = matching.match(parsed.kind, lines, entries, groups, reconciliation_service.plan_map(db, account_id),
                            lambda e: proposed_fx_fee(accounts_by_id[e.account_id], e.flow), rules, parsed.currency)
    return DryRun(result, guard, existing)


def _key(meta: dict) -> str:
    return "/".join([meta["root"], *[p for p in meta["folder"].split("/") if p][:2]])


def run(cfg: WorkerConfig, runner: Runner, *, limit: int | None) -> dict:
    if not cfg.verify_db_url:
        raise Refused("STATEMENT_VERIFY_DB_URL is required for verify (read-only role, ruling 11)")
    prs = parser_mod.Parser(cfg, runner, config_dir=cfg.verify_parser_config_dir, credentials_writable=False)
    gate = parser_mod.Gate(cfg)
    gate.ensure(prs)  # raises ParserDisabled (ruling 9)
    version = parse_schema.version(prs.cli_version() or "unknown", cfg.parser_model)
    vtag = hashlib.sha256(version.encode("utf-8")).hexdigest()[:16]
    masked_dir, parsed_dir = cfg.verify_dir / "masked", inbox_mod.private_dir(cfg.verify_dir / "parsed")
    reports = inbox_mod.private_dir(cfg.verify_dir / "reports")
    file_map = _account_map(cfg, None) if cfg.account_map_file is not None else None
    rows: list[dict] = []
    totals = {"statements": 0, "lines": 0, "claims": 0, "unmatched_lines": 0, "unmatched_entries": 0,
              "guardrail_failed": 0, "parse_failed": 0, "unmapped": 0, "existing": 0, "verify_errors": 0,
              "cases": {}, "errors": []}
    engine = create_engine(cfg.verify_db_url)
    try:
        # Phase 1: parse (or load the cache) with no database connection open.
        pending: list[tuple[str, int, parse_schema.StatementParse]] = []
        aborted = False
        if file_map is None:
            with Session(engine) as db:
                db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
                account_map = _account_map(cfg, db)
                db.rollback()
        else:
            account_map = file_map
        for meta_path in sorted(masked_dir.glob("*.json")):
            if limit is not None and len(pending) + totals["parse_failed"] + totals["unmapped"] + totals["verify_errors"] >= limit:
                break
            sha = meta_path.stem
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                account_id = account_map.get(_key(meta))
                if account_id is None:
                    totals["unmapped"] += 1
                    continue
                cache = parsed_dir / f"{sha}.{vtag}.json"
                if cache.exists():
                    parsed = parse_schema.StatementParse.model_validate_json(cache.read_text(encoding="utf-8"))
                else:
                    try:
                        parsed = prs.parse((masked_dir / f"{sha}.txt").read_text(encoding="utf-8"))
                    except parser_mod.ParseError as exc:
                        totals["parse_failed"] += 1
                        rows.append({"sha256": sha, "account_id": account_id, "error": exc.reason})
                        if exc.reason in ("sandbox", "cli_version", "auth"):
                            gate.latch(f"{exc.reason} on verify input", gate.key(prs))  # ruling 14
                            totals["errors"].append(exc.reason)
                            aborted = True
                            break
                        continue
                    _write_private(cache, parsed.model_dump_json())
                pending.append((sha, account_id, parsed))
            except Exception:
                totals["verify_errors"] += 1
                rows.append({"sha256": sha, "error": "verify_error"})
        # Phase 2: one read-only snapshot, held only while matching.
        if pending and not aborted:
            with Session(engine) as db:
                db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
                for sha, account_id, parsed in pending:
                    try:
                        with db.begin_nested():  # a failed statement must not poison the shared snapshot
                            _report_one(db, sha, account_id, parsed, rows, totals)
                    except Exception:
                        totals["verify_errors"] += 1
                        rows.append({"sha256": sha, "error": "verify_error"})
                db.rollback()
    finally:
        engine.dispose()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    _write_private(reports / f"{stamp}.json", json.dumps({"parser_version": version, "totals": totals, "rows": rows},
                                                         ensure_ascii=False, indent=1))
    return totals


def _report_one(db: Session, sha: str, account_id: int, parsed: parse_schema.StatementParse, rows: list[dict],
                totals: dict) -> None:
    dry = dry_run_match(db, account_id, parsed)
    real = [l for l in parsed.lines if not l.is_subtotal]
    row = {"sha256": sha, "account_id": account_id, "period_end": parsed.period_end.isoformat(),
           "existing_statement_id": dry.existing_statement_id, "lines": len(real),
           "guardrail_ok": dry.guard.ok, "matched": dry.result is not None}
    if dry.result is None:
        row.update(claims=0, unmatched_lines=None, unmatched_entries=None, cases_by_kind={})
    else:
        cases: dict[str, int] = {}
        for case in dry.result.cases:
            cases[case.kind] = cases.get(case.kind, 0) + 1
        claimed = {c.line_id for c in dry.result.claims}
        row.update(claims=len(dry.result.claims), unmatched_lines=len([l for l in real if l.seq not in claimed]),
                   unmatched_entries=len(dry.result.unmatched_entry_ids), cases_by_kind=cases)
    # totals are only touched once the row is complete, so a failure above leaves them consistent
    totals["statements"] += 1
    totals["lines"] += row["lines"]
    totals["existing"] += 1 if dry.existing_statement_id else 0
    if dry.result is None:
        totals["guardrail_failed"] += 1
    else:
        for kind, n in row["cases_by_kind"].items():
            totals["cases"][kind] = totals["cases"].get(kind, 0) + n
        totals["claims"] += row["claims"]
        totals["unmatched_lines"] += row["unmatched_lines"]
        totals["unmatched_entries"] += row["unmatched_entries"]
    rows.append(row)
