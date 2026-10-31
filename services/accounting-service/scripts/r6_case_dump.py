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
from decimal import Decimal
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

    def _keys(cands):  # a candidate is an entry id, "g<id>" for a group representation (matching._ambiguous), or a settlement fingerprint
        out = []
        for x in cands or ():
            if not isinstance(x, dict):
                continue
            if x.get("entry_id") is not None:
                out.append(x["entry_id"])
            elif x.get("group_id") is not None:
                out.append(f"g{x['group_id']}")
            elif isinstance(x.get("fingerprint"), str):  # a settlement candidate (trade_settlement_service.facts_of): its key, never its amounts
                out.append(x["fingerprint"])
        return sorted(out, key=str)
    cases = [[c.get("event_id"), c["kind"], (c.get("context") or {}).get("hint"), _keys(c.get("candidates")), c.get("entry_id")]
             for c in desired]
    return {"claims": sorted(claims), "cases": sorted(cases, key=lambda c: (c[0] or 0, c[1], c[2] or ""))}


def _bucket(amount) -> str:
    """Aggregate-only: never the amount itself."""
    value = abs(amount)
    return "<1000" if value < 1000 else "<5000" if value < 5000 else ">=5000"


def _count_hint(db, statements, hint: str, kind: str | tuple[str, ...] = "line_unmatched") -> dict[str, int]:
    """Open cases of this kind (default line_unmatched; a tuple matches any of them) with this hint, per account."""
    from app.models import ReconciliationCase
    out: dict[str, int] = {}
    for stmt in statements:
        n = db.scalar(select(func.count(ReconciliationCase.id)).where(
            ReconciliationCase.statement_id == stmt.id,
            ReconciliationCase.kind.in_(kind) if isinstance(kind, tuple) else ReconciliationCase.kind == kind,
            ReconciliationCase.status == "open", ReconciliationCase.context["hint"].astext == hint))
        if n:
            out[str(stmt.account_id)] = out.get(str(stmt.account_id), 0) + n
    return out


def _transfer_pairs(db, statements) -> list[dict]:
    """One row per open amount_delta/ambiguous transfer_delta case: ids, kind, residual bucket and day distance only."""
    from app.models import ReconciliationCase
    rows = []
    for stmt in sorted(statements, key=lambda s: s.id):
        cases = db.scalars(select(ReconciliationCase).where(
            ReconciliationCase.statement_id == stmt.id, ReconciliationCase.kind.in_(("amount_delta", "ambiguous")),
            ReconciliationCase.status == "open", ReconciliationCase.context["hint"].astext == "transfer_delta")
            .order_by(ReconciliationCase.id)).all()
        for c in cases:
            ctx = c.context or {}
            rows.append({"statement_id": stmt.id, "account_id": stmt.account_id, "kind": c.kind,
                         "residual_bucket": _bucket(Decimal(str(ctx["residual"]))) if ctx.get("residual") is not None else None,
                         "day_distance": ctx.get("date_distance")})
    return rows


def _policy_section(db, statements) -> dict:
    from app.models import LedgerEntry
    from app.services import coverage_service, policy_service, stand_ins
    from app.services.reconciliation_actions import structural_actions as sa
    policy = settings_service.reconciliation_policy(db)
    out = {"policy": {}, "anchor_blockers": [], "anchor_above_earlier_statement": [], "needs_peer": _count_hint(db, statements, "needs_peer"),
           "transfer_delta": _count_hint(db, statements, "transfer_delta", ("amount_delta", "ambiguous")), "owner_deleted": {},
           "transfer_pairs": _transfer_pairs(db, statements), "retirements": {}, "action_partially_stranded": {}, "sweep": {"would_release_old": 0, "would_release_new": 0, "kept_by_key": {}, "released_by_key": {}},
           "structural_keys": sorted(coverage_service.STRUCTURAL_KEYS), "accounts": {str(s.id): s.account_id for s in statements}}
    seen = set()
    for stmt in statements:
        out["policy"][str(stmt.id)] = [{"action": c.action, "kind": c.params.get("kind"), "amount_bucket": _bucket(c.amount), "decision": d}
                                       for c, d in policy_service.preview(db, stmt, policy)]
        out["owner_deleted"][str(stmt.id)] = len(stand_ins.owner_deleted_events(db, stmt))
        out["retirements"][str(stmt.id)] = stand_ins.retirement_preview(db, stmt)
        partial = len(coverage_service.partially_stranded_ids(db, stmt))  # applied, some targets gone: the owner's
        if partial:
            out["action_partially_stranded"][str(stmt.id)] = partial
        sw = coverage_service.sweep_preview(db, stmt)
        for k in ("would_release_old", "would_release_new"):
            out["sweep"][k] += sw[k]
        for bucket in ("kept_by_key", "released_by_key"):
            for key, n in sw[bucket].items():
                out["sweep"][bucket][key] = out["sweep"][bucket].get(key, 0) + n
        if stmt.kind == "bank" and stmt.account_id not in seen:
            seen.add(stmt.account_id)
            found = sa.anchor_delta(db, stmt.account_id)
            if found is not None and db.scalar(select(func.count(LedgerEntry.id)).where(
                    LedgerEntry.account_id == stmt.account_id, LedgerEntry.kind == "balance_adjustment",
                    LedgerEntry.posted_date >= found[0])):
                out["anchor_blockers"].append(stmt.account_id)
            as_of = sa.anchor_as_of(db, stmt.account_id)  # C1: the anchor absorbed an earlier live month
            if as_of is not None and db.scalar(select(func.count(AccountStatement.id)).where(
                    AccountStatement.account_id == stmt.account_id, AccountStatement.kind == "bank",
                    AccountStatement.mode == "live", AccountStatement.period_start <= as_of)):
                out["anchor_above_earlier_statement"].append(stmt.account_id)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--policy", action="store_true", help="also dump the R6b policy preview, sweep and retirement sections")
    args = ap.parse_args()
    out = {"sha": _sha(), "statements": {}}
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        out["watermark"] = _watermark(db)
        dumped = []
        for stmt in db.execute(select(AccountStatement).where(AccountStatement.mode == "live").order_by(AccountStatement.id)).scalars():
            if stmt.current_revision_id is None:
                continue
            out["statements"][str(stmt.id)] = _statement(db, stmt)
            dumped.append(stmt)
        if args.policy:
            out.update(_policy_section(db, dumped))
        db.rollback()
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(out, fh, default=str)
    print(f"dumped {len(out['statements'])} live statements at {out['sha']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
