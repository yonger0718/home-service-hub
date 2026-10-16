# R6a dry-run (merge gate)

Read-only. Both dumps run against the live database inside a READ ONLY transaction that is rolled back.
Run as the owner (or the verify user with the read-only role) from the candidate worktree:

    git worktree add --detach <path> <merge-base>                            # baseline tree, clean; copy the mode-600 .env to its repo root
    BASE=<path>/services/accounting-service                                   # baseline = the merge-base worktree
    CAND=$PWD                                                                 # services/accounting-service of the branch
    TS=$(date -u +%Y%m%dT%H%M%SZ); OUT=~/reports/home-hub-r6a-dryrun-$TS; mkdir -m 700 -p $OUT
    PYTHONPATH=$BASE .venv/bin/python scripts/r6_case_dump.py --out $OUT/before.json    # prints "... at <baseline sha>"
    PYTHONPATH=$CAND .venv/bin/python scripts/r6_case_dump.py --out $OUT/after.json     # prints "... at <candidate sha>"
    .venv/bin/python scripts/r6_dryrun.py --baseline $OUT/before.json --candidate $OUT/after.json --out-dir $OUT

The baseline tree needs its own `.env` at its repo root (copy the candidate's, mode 600): the dump loads the `.env` of
whichever tree supplied `app`. A `PYTHONPATH` that points at a missing directory is not an error: Python silently
imports the candidate instead (the same-SHA gate catches it). Both trees must be clean: the dump appends `-dirty` to the
SHA of a tree with uncommitted changes, and such a dump is not evidence: a dirty dump fails the gate.

Never pass `-I` or `-E` to the dump: both drop `PYTHONPATH`, and the dump would silently import the venv's editable
install (the candidate) twice. The dump prints the SHA of the tree that actually supplied `app`; the two printed SHAs
must differ and match `git rev-parse HEAD` of `$BASE` and `$CAND`. Run the two dumps back to back while the poll timer is
idle (`systemctl --user status homehub-statements-poll.timer` as the worker user, or just between two ticks); the dump
records a watermark (max case / coverage / entry ids) and the gate fails if it moved between the runs. The watermark
sees only new rows: edits and deletes between the two dumps are not detected, so run them back to back while the poll
timer is idle.

`summary.md` carries counts only; `delta.jsonl` carries statement/event/entry ids (no amounts, no merchants) and stays
mode 600 under ~/reports. Gate: exit 0 — every changed line attributed to an R6a rule, no claim (of any rule) lost or changed, no
ambiguous case with fewer than two candidates, same watermark, two different SHAs. Record the two SHAs and the summary
in the release record.

Reading a "claim lost" or "claim changed" failure (the claim moved to another entry): lines match in id order, so a line that newly claims under R6a (distance 2–3,
below accept before) can take an entry that a later line held before; the later line then shows
`claimed_by_earlier_line` (lost), or takes another entry (changed). That pair is the rescale working, not a harness bug — note the pair in the release record
and re-run the gate with `--allow-moved-claims` only after the owner has read the pair list (the flag turns those
lost/changed failures into an "claims moved" line in the summary listing the event ids; any other lost claim still fails).

## Policy mode (R6b)

Two commands, run by the controller at release time against the live database (read-only transaction; never by an implementer):

    PYTHONPATH=$CAND .venv/bin/python scripts/r6_case_dump.py --policy --out $OUT/after-policy.json
    .venv/bin/python scripts/r6_dryrun.py --policy --baseline $OUT/before.json --candidate $OUT/after-policy.json --out-dir $OUT

`--policy` on the dump adds aggregate sections (no amount, merchant or name; amounts appear only as `<1000` / `<5000` /
`>=5000` buckets) computed by the service-side previews the integration tests share. `--policy` on the diff prints the
summary and writes `policy-summary.md` (mode 600) next to the others, then exits 0; it is evidence, not a gate.

- decision table (`| account | action | decision | lines |`): what `run_policy` would decide per candidate: `apply`,
  `propose:<cap>`, `skip:<code>`, `disabled` (policy or action off), `excluded:<reason>` (statement not live, conflict,
  recheck, blocking case, currency not enabled).
- `anchor blocked by closing adjustments`: accounts whose opening anchor is refused because a `balance_adjustment` sits at or after the anchor date.
- `needs_peer lines` / `owner-deleted events`: lines the policy never backfills (peer entry needed / the owner removed the created entry).
- `retirements pending`: applied creations (by action type) whose entry is already gone; the next sweep retires them.
- `sweep: old rule ... new rule ...`: lines with a pending entry update event the pre-R6b rule would release (every edit) vs the
  R6b.0 rule (structural keys only), with the key breakdown (cosmetic keys kept, structural keys released) and the structural key set.
  This table is the R6b.0 regression-gate evidence; nothing fails automatically, the release record keeps it with both SHAs.
