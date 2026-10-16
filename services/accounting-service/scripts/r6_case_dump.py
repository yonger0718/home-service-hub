"""Dump the matcher's desired claims and cases for every live statement, read-only, from the code on PYTHONPATH.

Run once with PYTHONPATH pointing at the baseline checkout's services/accounting-service and once at the working
tree; r6_dryrun.py diffs the two files. Opens one transaction, SET TRANSACTION READ ONLY, never commits.
Usage: PYTHONPATH=<tree>/services/accounting-service python scripts/r6_case_dump.py --out /path/dump.json  (no -I/-E: they drop PYTHONPATH)
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import func, select, text

from app.database import SessionLocal  # the tree on PYTHONPATH
from app.models import Account, AccountStatement
from app.services import reconciliation_service as rec
from app.services import settings_service
from app.services.entry_write_service import proposed_fx_fee
from app.services.statements import matching


def _sha() -> str:
    """HEAD of the tree that actually supplied the imported `app` package (not whatever PYTHONPATH says)."""
    import app
    tree = str(Path(app.__file__).resolve().parents[1])
    sha = subprocess.run(["git", "-C", tree, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", tree, "status", "--porcelain"], capture_output=True, text=True, check=True).stdout.strip()
    return f"{sha}-dirty" if dirty else sha


def _watermark(db) -> list[int]:
    from app.models import LedgerEntry, ReconciliationCase, StatementCoverage
    return [db.scalar(select(func.coalesce(func.max(t.id), 0))) for t in (ReconciliationCase, StatementCoverage, LedgerEntry)]


def _statement(db, statement) -> dict:
    accounts = rec.participating_accounts(db, statement.account_id)
    accounts_by_id = {a.id: a for a in db.execute(select(Account).where(Account.id.in_(accounts))).scalars()}
    rules = rec.rules_for(settings_service.reconciliation_rules(db), statement.period_end)
    all_lines = rec._current_lines(db, statement)
    manual = rec._manual_claims(db, statement)
    line_rows = [line for line in all_lines if line.id not in manual]
    owned = set().union(*manual.values()) if manual else set()
    window = matching.population_window_days(rules) if hasattr(matching, "population_window_days") else rules.candidate_window_days
    entries, groups = rec.load_candidates(db, statement, accounts, window_days=window,
                                          include_reward=any(l.line_kind == "reward" for l in all_lines), exclude=owned)
    result = matching.match(statement.kind, [rec._as_line(r) for r in line_rows], entries, groups,
                            rec.plan_map(db, statement.account_id),
                            lambda e: proposed_fx_fee(accounts_by_id[e.account_id], e.flow), rules, statement.currency)
    lines = {r.id: r for r in line_rows}
    by_id = {e.id: e for e in entries if e.claimable}
    desired = rec._desired_cases(db, statement, accounts, result, lines, by_id, rules)
    claims = [[lines[c.line_id].event_id, c.representation.rule, sorted(r.entry_id for r in c.representation.rows)] for c in result.claims]

    def _keys(cands):  # a candidate is an entry id, or "g<id>" for a group representation (matching._ambiguous)
        out = []
        for x in cands or ():
            if not isinstance(x, dict):
                continue
            if x.get("entry_id") is not None:
                out.append(x["entry_id"])
            elif x.get("group_id") is not None:
                out.append(f"g{x['group_id']}")
        return sorted(out, key=str)
    cases = [[c.get("event_id"), c["kind"], (c.get("context") or {}).get("hint"), _keys(c.get("candidates")), c.get("entry_id")]
             for c in desired]
    return {"claims": sorted(claims), "cases": sorted(cases, key=lambda c: (c[0] or 0, c[1], c[2] or ""))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = {"sha": _sha(), "statements": {}}
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        out["watermark"] = _watermark(db)
        for stmt in db.execute(select(AccountStatement).where(AccountStatement.mode == "live").order_by(AccountStatement.id)).scalars():
            if stmt.current_revision_id is None:
                continue
            out["statements"][str(stmt.id)] = _statement(db, stmt)
        db.rollback()
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(out, fh, default=str)
    print(f"dumped {len(out['statements'])} live statements at {out['sha']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
