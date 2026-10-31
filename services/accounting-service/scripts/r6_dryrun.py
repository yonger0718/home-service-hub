"""Diff two r6_case_dump.py files and write the R6a merge-gate evidence (summary.md + delta.jsonl, mode 600).
Usage: python scripts/r6_dryrun.py --baseline before.json --candidate after.json --out-dir ~/reports/home-hub-r6a-dryrun-<ts>
Exit 1 when the gate fails: a claim lost, an ambiguous case with fewer than two candidates after, an unattributed
change, or a database change between the two dumps (watermarks differ)."""
import argparse
import json
import os
import sys
from collections import Counter


def _index(dump: dict) -> dict:
    """event_id → ("claim", rule, entries) | ("case", kind, hint, candidates, entry_id) per statement."""
    out = {}
    for sid, st in dump["statements"].items():
        for event_id, rule, entries in st["claims"]:
            out[(sid, event_id)] = ("claim", rule, tuple(entries))
        for event_id, kind, hint, cands, entry_id in st["cases"]:
            if event_id is not None:
                out[(sid, event_id)] = ("case", kind, hint, tuple(cands), entry_id)
    return out


def diff_dumps(before: dict, after: dict) -> list[dict]:
    b, a = _index(before), _index(after)
    changes = []
    for key in sorted(set(b) | set(a), key=lambda k: (int(k[0]), k[1])):
        if b.get(key) != a.get(key):
            changes.append({"statement_id": int(key[0]), "event_id": key[1], "before": b.get(key), "after": a.get(key)})
    return changes


def attribute(change: dict) -> str:
    before, after = change["before"], change["after"]
    if after and after[0] == "claim" and after[1] == "auto_pay":
        return "auto-pay claim (2026-10-26)"
    if after and after[0] == "claim":
        if before and before[0] == "claim":
            return "R6a.1 claim moved to another entry" if before[2] != after[2] else "claim rule changed"
        if before and before[0] == "case" and before[1] == "ambiguous" and len(before[3]) == 1:
            return "R6a.2 single-candidate ambiguous now claims"
        return "R6a.1/R6a.3 new exact claim"
    if after and after[0] == "case":
        kind, hint = after[1], after[2]
        if kind in ("amount_delta", "ambiguous") and hint == "transfer_delta":  # R6c.1, before the generic near branch; also from needs_peer
            return "R6c.1 transfer_delta"
        if kind == "line_unmatched" and hint == "needs_peer" and before and before[0] == "case" and before[1] == "amount_delta" and before[2] == "transfer_delta":
            return "R6c.1 transfer_delta cleared"
        if kind == "amount_delta" and hint == "date_drift":
            return "R6a.5 date_drift proposal"
        if kind == "amount_delta" and (before is None or before[1] != "amount_delta"):
            return "R6a.4 near proposal"
        if kind == "amount_delta" and before[1] == "amount_delta":
            return "R6a.4 near candidates changed"
        if kind == "line_unmatched" and hint == "bank_only" and (before is None or before[2] != "bank_only"):
            return "R6a.6 bank_only by kind"
        if kind == "line_unmatched" and hint == "claimed_by_earlier_line" and (before is None or before[2] != "claimed_by_earlier_line"):
            return "R6a.1 claimed by an earlier line"
        if kind == "ambiguous":
            return "R6a.1 ambiguity changed"
    return "unattributed"


def _claim_changed(c: dict) -> bool:
    b, a = c["before"], c["after"]
    return bool(b and a and b[0] == "claim" and a[0] == "claim" and b[2] != a[2])


def _claim_moved_events(changes: list[dict]) -> list[int]:
    return sorted(c["event_id"] for c in changes if _claim_changed(c) or (
        c["before"] and c["before"][0] == "claim" and c["after"] and c["after"][0] == "case" and c["after"][2] == "claimed_by_earlier_line"))


def waive_moved(failures: list[str], changes: list[dict]) -> list[str]:
    """--allow-moved-claims: drop the lost/changed failures of claims that moved to an earlier line or another entry."""
    waived = {f"event {e}: claim {w}" for e in _claim_moved_events(changes) for w in ("lost", "changed")}
    return [f for f in failures if f not in waived]


def gate_failures(changes: list[dict], after: dict, before: dict | None = None) -> list[str]:
    failures = [f"event {c['event_id']}: claim lost" for c in changes
                if c["before"] and c["before"][0] == "claim" and not (c["after"] and c["after"][0] == "claim")]
    failures += [f"event {c['event_id']}: claim changed" for c in changes if _claim_changed(c)]
    for st in after["statements"].values():
        for event_id, kind, _hint, cands, _entry in st["cases"]:
            if kind == "ambiguous" and len(cands) < 2:  # group candidates count ("g<id>")
                failures.append(f"event {event_id}: ambiguous with fewer than two candidates")
    failures += [f"event {c['event_id']}: unattributed change" for c in changes if attribute(c) == "unattributed"]
    if before is not None and before.get("watermark") != after.get("watermark"):
        a, b = ",".join(map(str, before.get("watermark", []))), ",".join(map(str, after.get("watermark", [])))
        failures.append(f"database changed between the dumps (watermark {a} → {b})")
    if before is not None and before.get("sha") == after.get("sha"):
        failures.append(f"both dumps came from the same tree (sha {after.get('sha')})")
    for which, dump in (("baseline", before), ("candidate", after)):
        if dump is not None and str(dump.get("sha", "")).endswith("-dirty"):
            failures.append(f"{which} dump came from a dirty tree (sha {dump['sha']})")
    return failures


def _counts(dump: dict) -> Counter:
    c = Counter()
    for st in dump["statements"].values():
        c["claims"] += len(st["claims"])
        for _e, kind, hint, _c, _i in st["cases"]:
            c[f"{kind}/{hint or '-'}"] += 1
    return c


def policy_summary(after: dict) -> str:
    """Markdown for the R6b.7 policy section of a `r6_case_dump.py --policy` dump (aggregate counts only)."""
    accounts = after.get("accounts", {})
    table: Counter = Counter()
    for sid, rows in after.get("policy", {}).items():
        for row in rows:
            table[(accounts.get(sid, "?"), row["action"], row["decision"])] += 1
    lines = [f"# R6b policy preview — candidate {after.get('sha')}", "", "| account | action | decision | lines |", "|---|---|---|---|"]
    lines += [f"| {a} | {act} | {dec} | {n} |" for (a, act, dec), n in sorted(table.items(), key=lambda kv: tuple(map(str, kv[0])))]
    blockers = len(after.get("anchor_blockers", []))
    above = len(after.get("anchor_above_earlier_statement", []))
    peers = after.get("needs_peer", {})
    by_account = ", ".join(f"{a}={n}" for a, n in sorted(peers.items(), key=lambda kv: int(kv[0]))) or "none"
    lines += ["", f"anchor blocked by closing adjustments: {blockers} account{'' if blockers == 1 else 's'}",
              f"anchor above an earlier statement: {above} account{'' if above == 1 else 's'}",
              f"needs_peer lines: {sum(peers.values())} (by account: {by_account})",
              f"owner-deleted events: {sum(after.get('owner_deleted', {}).values())}"]
    retire: Counter = Counter()
    for by_action in after.get("retirements", {}).values():
        retire.update(by_action)
    lines.append("retirements pending: " + (", ".join(f"{k} {v}" for k, v in sorted(retire.items())) or "none"))
    partial = after.get("action_partially_stranded", {})
    by_statement = ", ".join(f"{k}={v}" for k, v in sorted(partial.items(), key=lambda kv: int(kv[0]))) or "none"
    lines.append(f"action_partially_stranded: {sum(partial.values())} (by statement: {by_statement})")
    sw = after.get("sweep", {})

    def _keys(d: dict) -> str:
        return ", ".join(f"{k} {v}" for k, v in d.items()) or "none"
    lines.append(f"sweep: old rule would release {sw.get('would_release_old', 0)} lines, new rule {sw.get('would_release_new', 0)} "
                 f"(kept by key: {_keys(sw.get('kept_by_key', {}))}; released by key: {_keys(sw.get('released_by_key', {}))})")
    lines.append("structural keys: " + ", ".join(after.get("structural_keys", [])))
    return "\n".join(lines) + "\n"


def transfers_summary(after: dict) -> str:
    """Markdown for the R6c transfers section of a `r6_case_dump.py --policy` dump: open case counts per account, nothing else."""
    deltas, peers = after.get("transfer_delta", {}), after.get("needs_peer", {})
    lines = [f"# R6c transfers — candidate {after.get('sha')}", "", "| account | transfer_delta cases | needs_peer cases |", "|---|---|---|"]
    lines += [f"| {a} | {deltas.get(a, 0)} | {peers.get(a, 0)} |" for a in sorted(set(deltas) | set(peers), key=int)]
    lines += ["", f"total: transfer_delta {sum(deltas.values())}, needs_peer {sum(peers.values())}"]
    pairs = after.get("transfer_pairs")
    if pairs:
        lines += ["", "## Pairs", "", "| statement | account | kind | residual bucket | day distance |", "|---|---|---|---|---|"]
        lines += [f"| {p['statement_id']} | {p['account_id']} | {p['kind']} | {p['residual_bucket'] or '-'} | "
                  f"{'-' if p['day_distance'] is None else p['day_distance']} |" for p in pairs]
    return "\n".join(lines) + "\n"


_SETTLEMENT_HINTS = {"settlement_ambiguous": "ambiguous", "settlement_pending": "pending", "settlement_mismatch": "mismatch",
                     "source_drift": "drift", "orphaned_stand_in": "orphaned_stand_in"}


def _settlement_outcome(kind: str, hint: str | None) -> str | None:
    if hint in _SETTLEMENT_HINTS:
        return _SETTLEMENT_HINTS[hint]
    return "book_settlement" if kind == "settlement" and hint is None else None


def settlements_listing(dump: dict, stand_ins: dict | None, baseline: dict | None = None) -> dict:
    """R7 Phase 1 dry run: one row per settlement line (stand-in claim, owner match, or a settlement case) of every statement
    in the dump that has at least one, plus the stand-ins the job would book. Ids, dates and codes only: candidate keys are
    entry ids / "g<id>" / settlement fingerprints. `baseline` (optional) adds `supersedes`: the case the line carried before.
    `stand_ins` is a `run_stand_ins(dry_run=True)` report (`booking_id` is None in a dry run) or None."""
    before = _index(baseline) if baseline else {}
    lines = []
    for sid in sorted(dump["statements"], key=int):
        st = dump["statements"][sid]
        rows = []
        for event_id, rule, entries in st["claims"]:
            rows.append({"statement_id": sid, "event_id": event_id,
                         "outcome": "stand_in_claim" if rule == "settlement_stand_in" else "owner_match", "rule": rule, "entries": list(entries)})
        for event_id, kind, hint, cands, entry_id in st["cases"]:
            outcome = _settlement_outcome(kind, hint)
            if outcome is not None and event_id is not None:
                rows.append({"statement_id": sid, "event_id": event_id, "outcome": outcome, "kind": kind, "hint": hint,
                             "candidates": list(cands)})
        if not any(r["outcome"] not in ("owner_match",) for r in rows):  # an owner match alone is not a settlement line
            continue
        for row in sorted(rows, key=lambda r: r["event_id"] or 0):
            prior = before.get((sid, row["event_id"]))
            if prior and prior[0] == "case" and (prior[1], prior[2]) != (row.get("kind"), row.get("hint")):
                row["supersedes"] = {"kind": prior[1], "hint": prior[2]}
            lines.append(row)
    report = stand_ins or {}
    out = {"sha": dump.get("sha"), "lines": lines,
           "stand_ins": [{"account_id": b["account_id"], "settlement_date": b["settlement_date"], "booking_id": b.get("booking_id")}
                         for b in report.get("booked", [])],
           "skipped": [{"account_id": k["account_id"], "settlement_date": k["settlement_date"], "reason": k["reason"]}
                       for k in report.get("skipped", [])]}
    if "previewed_settings" in report:
        out["previewed_settings"] = report["previewed_settings"]
    return out


def _write_private(path: str, payload: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(payload)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--policy", action="store_true",
                    help="print the R6b policy preview (decisions, retirements, sweep regression table) and write policy-summary.md; exit 0")
    ap.add_argument("--transfers", action="store_true",
                    help="write transfers-summary.md (open transfer_delta / needs_peer cases per account) and exit 0, whatever the gate says")
    ap.add_argument("--settlements", action="store_true",
                    help="write settlements-listing.json (one row per settlement line of the candidate dump, plus the stand-ins "
                         "--stand-ins would book) and exit 0")
    ap.add_argument("--stand-ins", help="a run_stand_ins(dry_run=True) report as JSON, for --settlements")
    ap.add_argument("--allow-moved-claims", action="store_true",
                    help="waive both moved-claim shapes (reported, not failed): a claim whose line is now claimed_by_earlier_line, "
                         "and a claim whose entry set changed")
    args = ap.parse_args()
    with open(args.baseline) as fb, open(args.candidate) as fa:
        before, after = json.load(fb), json.load(fa)
    if args.policy and "policy" not in after:
        print(f"{args.candidate}: no policy section; dump it with r6_case_dump.py --policy", file=sys.stderr)
        return 2
    if args.transfers and not args.policy and "transfer_delta" not in after:
        print(f"{args.candidate}: no transfer_delta section; dump it with r6_case_dump.py --policy", file=sys.stderr)
        return 2
    os.makedirs(args.out_dir, mode=0o700, exist_ok=True)
    if args.settlements:
        report = None
        if args.stand_ins:
            with open(args.stand_ins) as fs:
                report = json.load(fs)
        listing = settlements_listing(after, report, before)
        _write_private(os.path.join(args.out_dir, "settlements-listing.json"), json.dumps(listing, indent=1) + "\n")
        print(f"settlements: {len(listing['lines'])} lines, {len(listing['stand_ins'])} stand-ins would book")
        return 0
    if args.policy:
        text = f"baseline {before['sha']}\n" + policy_summary(after)
        _write_private(os.path.join(args.out_dir, "policy-summary.md"), text)
        print(text)
        return 0
    changes = diff_dumps(before, after)
    rules = Counter(attribute(c) for c in changes)
    failures = gate_failures(changes, after, before)
    moved = _claim_moved_events(changes)
    if args.allow_moved_claims:
        failures = waive_moved(failures, changes)
    cb, ca = _counts(before), _counts(after)
    lines = [f"# R6a dry-run — baseline {before['sha']} → candidate {after['sha']}", "",
             f"live statements: {len(after['statements'])}; changed lines: {len(changes)}", "",
             "| kind/hint | before | after |", "|---|---|---|"]
    lines += [f"| {k} | {cb.get(k, 0)} | {ca.get(k, 0)} |" for k in sorted(set(cb) | set(ca))]
    lines += ["", "| attributed rule | lines |", "|---|---|"] + [f"| {k} | {v} |" for k, v in rules.most_common()]
    if moved:
        lines += ["", f"claims moved to an earlier line or another entry (the rescale, see README): {len(moved)} — events {', '.join(map(str, moved))}"
                  + ("" if args.allow_moved_claims else " (failing the gate until --allow-moved-claims after the owner has read them)")]
    lines += ["", "gate: " + ("PASS" if not failures else "FAIL\n- " + "\n- ".join(failures))]
    for name, payload in (("summary.md", "\n".join(lines) + "\n"), ("delta.jsonl", "".join(json.dumps({**c, "rule": attribute(c)}) + "\n" for c in changes))):
        fd = os.open(os.path.join(args.out_dir, name), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(payload)
    print("\n".join(lines))
    if args.transfers:  # evidence mode: the gate verdict stays in summary.md, the exit code is 0
        text = transfers_summary(after)
        _write_private(os.path.join(args.out_dir, "transfers-summary.md"), text)
        print(text)
        return 0
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
