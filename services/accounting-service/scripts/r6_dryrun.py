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
    if after and after[0] == "claim":
        if before and before[0] == "claim":
            return "R6a.1 claim moved to another entry" if before[2] != after[2] else "claim rule changed"
        if before and before[0] == "case" and before[1] == "ambiguous" and len(before[3]) == 1:
            return "R6a.2 single-candidate ambiguous now claims"
        return "R6a.1/R6a.3 new exact claim"
    if after and after[0] == "case":
        kind, hint = after[1], after[2]
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
    return failures


def _counts(dump: dict) -> Counter:
    c = Counter()
    for st in dump["statements"].values():
        c["claims"] += len(st["claims"])
        for _e, kind, hint, _c, _i in st["cases"]:
            c[f"{kind}/{hint or '-'}"] += 1
    return c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--allow-moved-claims", action="store_true",
                    help="a claim lost by a line whose entry is now held by an earlier line is reported, not failed")
    args = ap.parse_args()
    with open(args.baseline) as fb, open(args.candidate) as fa:
        before, after = json.load(fb), json.load(fa)
    os.makedirs(args.out_dir, mode=0o700, exist_ok=True)
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
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
