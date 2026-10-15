# R6a dry-run (merge gate)

Read-only. Both dumps run against the live database inside a READ ONLY transaction that is rolled back.
Run as the owner (or the verify user with the read-only role) from the candidate worktree:

    BASE=/home/opc/srv/homehub-worker/checkout/services/accounting-service   # deployed checkout = baseline (merge-base)
    CAND=$PWD                                                                 # services/accounting-service of the branch
    TS=$(date -u +%Y%m%dT%H%M%SZ); OUT=~/reports/home-hub-r6a-dryrun-$TS; mkdir -m 700 -p $OUT
    PYTHONPATH=$BASE .venv/bin/python scripts/r6_case_dump.py --out $OUT/before.json    # prints "... at <baseline sha>"
    PYTHONPATH=$CAND .venv/bin/python scripts/r6_case_dump.py --out $OUT/after.json     # prints "... at <candidate sha>"
    .venv/bin/python scripts/r6_dryrun.py --baseline $OUT/before.json --candidate $OUT/after.json --out-dir $OUT

Never pass `-I` or `-E` to the dump: both drop `PYTHONPATH`, and the dump would silently import the venv's editable
install (the candidate) twice. The dump prints the SHA of the tree that actually supplied `app`; the two printed SHAs
must differ and match `git rev-parse HEAD` of `$BASE` and `$CAND`. Run the two dumps back to back while the poll timer is
idle (`systemctl --user status homehub-statements-poll.timer` as the worker user, or just between two ticks); the dump
records a watermark (max case / coverage / entry ids) and the gate fails if it moved between the runs.

`summary.md` carries counts only; `delta.jsonl` carries statement/event/entry ids (no amounts, no merchants) and stays
mode 600 under ~/reports. Gate: exit 0 — every changed line attributed to an R6a rule, no exact claim lost, no
ambiguous case with fewer than two candidates, same watermark, two different SHAs. Record the two SHAs and the summary
in the release record.

Reading an "exact claim lost" or "exact claim changed" failure (the claim moved to another entry): lines match in id order, so a line that newly claims under R6a (distance 2–3,
below accept before) can take an entry that a later line held before; the later line then shows
`claimed_by_earlier_line` (lost), or takes another entry (changed). That pair is the rescale working, not a harness bug — note the pair in the release record
and re-run the gate with `--allow-moved-claims` only after the owner has read the pair list (the flag turns those
lost/changed failures into an "exact claims moved" line in the summary listing the event ids; any other lost claim still fails).
