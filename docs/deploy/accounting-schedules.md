# Accounting schedules deploy (add-accounting-schedules)

Spec: `openspec/changes/add-accounting-schedules/`. Endpoints, the job and its CLI: [`services/accounting-service/README.md`](../../services/accounting-service/README.md) ("Schedules"). The procedures for the pre-deploy dump, the restore drill and the rollback are those of [`accounting-phase-2a.md`](accounting-phase-2a.md), with the names below.

## Prerequisite: production is on phase 2a

This release upgrades from the phase 2a head `7b1e4a2c9d05`, and its rollback restores to that head. Deploy phase 2a first with its own runbook (dump, drill, migration, Caddy matcher, backup import, rollback window) if production is not there yet. Do not run both migrations in one step: the rollback script below checks the 2a head and would stop on a phase 1 dump. Confirm before step 0:

```bash
cd /home/opc/workspace/home-hub/services/accounting-service && .venv/bin/alembic current 2>/dev/null | tail -1
```

Expected: `7b1e4a2c9d05 (head)`. Anything else (for example the phase 1 head `5d2e7c9a1b3f`): stop and deploy phase 2a first.

## Configuration

Add to the root `.env` (`.env.example` documents it):

```text
ACCOUNTING_SCHEDULER_ENABLED=true
```

`true` (the default when the key is absent) runs the schedule job inside `accounting-service` at these times:

- daily at 00:05 Asia/Taipei;
- once, 10 s after start-up;
- every 10 minutes after a run that ended `busy`, `import_running` or `crashed`, until a run completes or the Taipei day changes;
- once after every real backup import made through the API.

The importer CLI has no scheduler. It runs the job inline after its import: generation always, posting only when this switch is on.

`false`, `0` or `no` turn the job off. Periods then wait until someone runs `python -m app.services.schedule_job`, taps 補入帳至今天, or calls `POST /api/accounting/schedules/run-now`.

Run exactly one `accounting-service` process with the job enabled. The job lock (`pg_try_advisory_lock` on key `0x53434844`) turns a second runner into a no-op, but that runner logs `busy` every day.

No other `.env` key is new. `ACCOUNTING_IMPORT_LOCKED` stays `false` until the cutover (see "During the mirror period"). `ACCOUNTING_API_TOKENS` and `ACCOUNTING_SPA_TOKEN` behave as in phase 2a (#42).

## What the migration does

Revision `c4e8b2f1a7d3` (down revision `7b1e4a2c9d05`):

- adds `schedule` to `entry_source`, in an autocommit block that runs before the transaction holding the rest;
- creates `schedule_definition` and `schedule_instance` with their enums and indexes;
- **drops `moze_schedule`.** Its rows are not migrated. They were a raw copy of the backup's future records, periods and installments, and the backup import after the upgrade (step 3) recreates them as definitions and periods. Between the upgrade and that import, 提醒中心 shows no 週期／分期 and nothing is posted.

## Deploy order

0. **Pre-deploy record, dump and restore drill.** Run steps 0 and 0b of [`accounting-phase-2a.md`](accounting-phase-2a.md), changing these names:

   | In the 2a runbook | Here |
   |---|---|
   | `~/backups/home-hub-2a/` | `~/backups/home-hub-schedules/` |
   | file names `*-pre-2a-$TS.*` | `*-pre-schedules-$TS.*` |
   | `rollback-2a.sh` | `rollback-schedules.sh` (the script of the "Rollback" section below) |
   | drill databases `accounting_drill_2a` / `accounting_drill_restore` / `accounting_drill_broken` | `accounting_drill_schedules` / `accounting_drill_restore` / `accounting_drill_broken` |
   | `NEW_BACKEND` = the 2a commit | `NEW_BACKEND` = the schedules commit being deployed (the PR's merge commit) |
   | drill upgrade ends at `-> 7b1e4a2c9d05` | ends at `-> c4e8b2f1a7d3` |
   | drill step a prints `7b1e4a2c9d05\|<n>` | prints `c4e8b2f1a7d3\|<n>`, with `n > 0` |
   | drill step c: `alembic current` prints `5d2e7c9a1b3f (head)`, and `accounting_drill_broken` reads `7b1e4a2c9d05` | prints `7b1e4a2c9d05 (head)`, and `accounting_drill_broken` reads `c4e8b2f1a7d3` |

   Do not make the rollback script by swapping names in the 2a file. The 2a script checks the phase 1 head `5d2e7c9a1b3f`, so it would stop a real rollback of this release at its step c. Instead, save the script from the "Rollback" section below as `~/backups/home-hub-schedules/rollback-schedules.sh` and `chmod 700` it. That script uses the phase 2a head `7b1e4a2c9d05` in its step c check, in its `restore verified` line and in its post-swap check. Confirm it before the drill:

   ```bash
   grep -c '7b1e4a2c9d05' ~/backups/home-hub-schedules/rollback-schedules.sh
   grep -c '5d2e7c9a1b3f\|home-hub-2a\|pre-2a' ~/backups/home-hub-schedules/rollback-schedules.sh
   ```

   Expected: `4` and `0`.

   **Drill step a.** The drill database runs a real backup import with the new code. The CLI's inline job may post today's periods there, which only adds the `schedule` rows a real rollback has to discard. To show that the drill copy holds schedule data, add this check after step a's version query:

   ```bash
   docker exec stonk-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d accounting_drill_schedules -At -c "SELECT count(*) FROM schedule_definition"'
   ```

   Expected: a non-zero count.

   **Drill step c.** `alembic current` on the swapped-in database must print exactly `7b1e4a2c9d05 (head)`. That is the string the script's step c compares against, so the drill proves the script's head check on a good restore.

   **Failure drill (step d).** It runs `rollback-schedules.sh` on the truncated dump, and the expected output is the one the 2a runbook gives.

   Any difference from the expected output: stop, do not deploy.

1. **Dependencies and migration**

   ```bash
   cd /home/opc/workspace/home-hub/services/accounting-service
   .venv/bin/pip install -r requirements.txt 2>&1 | tail -1
   .venv/bin/python -c "import apscheduler; print(apscheduler.__version__)"
   .venv/bin/alembic upgrade head
   ```

   Expected: `3.11.0`, then `Running upgrade 7b1e4a2c9d05 -> c4e8b2f1a7d3, …`.

2. **Backend and frontend together.** The previous SPA's settings page calls `GET /imports/schedules`, which now answers 404, and the new SPA needs the schedule endpoints. So restart the backend and publish the new SPA in the same step.

   ```bash
   cd /home/opc/workspace/home-hub && npx pm2 restart accounting-service
   curl -s http://localhost:8000/health
   curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/schedules/definitions
   curl -s -H "Authorization: Bearer $TOKEN" 'http://localhost:8000/schedules/instances?queue=true'
   sleep 15; npx pm2 logs accounting-service --lines 50 --nostream | grep -c 'schedule_job.run'
   ```

   Expected:
   - `{"status":"ok"}`;
   - `[]` twice;
   - at least `1`: the start-up run, which finds no definitions. With `ACCOUNTING_SCHEDULER_ENABLED=false`, the log shows `schedule_job.scheduler_disabled` instead.

   **Tokens (as in phase 2a step 2).** With `ACCOUNTING_API_TOKENS` unset, the header is ignored. When it is set (#42), export `TOKEN` to one of its tokens first. Without it, every `/schedules/*` route answers 401, `run-now` included. `/health` needs no token.

   **SPA.** Build and publish it as in step 3 of the phase 2a runbook: `cd frontend && npm run build`, then publish `frontend/dist/inventory-ui/browser` with the production publisher.

3. **Post-upgrade backup import.** Do a dry run first, read the report's `schedules` block, then do the real run.

   ```bash
   cd /home/opc/workspace/home-hub/services/accounting-service
   umask 077   # the reports hold loan_remainder_check names and amounts: never world-readable, not even briefly
   .venv/bin/python -m app.services.moze_backup_import_service ~/workspace/moze-backup/MOZE_4.0.zip --dry-run > ~/backups/home-hub-schedules/moze-schedules-dry-run.json
   .venv/bin/python -m app.services.moze_backup_import_service ~/workspace/moze-backup/MOZE_4.0.zip > ~/backups/home-hub-schedules/moze-schedules-import.json
   ls -l ~/backups/home-hub-schedules/moze-schedules-*.json
   ```

   You can also do this from the SPA: 記帳設定 → 匯入 → 試算 → 匯入. The report shows the 週期／分期 counts.

   **What to check.** These are the values the verify run of Task 28 got on the 2026-10-01 export. A newer export can differ in counts, but not in kind.

   - `schedules.definitions`: `recurring` 11 created, 8 of them `ended` (MOZE periods with no enabled future record); `installment` 12 created (2 installments without any record are listed under `review` as `no_records`).
   - Record counts: `records_mapped` 534, `rewards_ignored` 63, `artifact_records` 11, and `unsupported_types` empty.
   - `skipped_future`: 597 in total, balanced.
   - `review` reasons: `interval_mismatch` 1, `same_date` 1 and `no_records` 2. The `interval_mismatch` item is a finished installment with a short-month roll-over in MOZE.
   - `loan_remainder_check`: all 3 loans show a difference. This is an open owner question, not a mapping error.
   - The balance comparison, as in phase 2a. On the verify run `compared_accounts` was 0 of 72 and the CLI printed its WARNING, so check balances by hand against MOZE.

   **The job run at the end of the import.** The real import ends with a job run: the CLI runs it inline and prints a `schedule_job:` counts line on stderr. With `ACCOUNTING_SCHEDULER_ENABLED` on, that run posts:
   - periods due today, dated on or after the import date, are posted;
   - periods due earlier wait in 提醒中心 › 待完成交易 for 補入帳至今天, because nothing from the past is posted silently.

   The verify run's first import created 23 definitions and 542 periods (297 pending, 241 posted by MOZE, 4 skipped), and its job line read `status completed, generated 0, posted 0, failed 0, due_unposted 0`.

   A second import changes nothing: 0 periods are created, deleted or adopted.

   An import waits up to 30 s for running schedule writes and is refused after that (409 `import already running`); run it again.

   Delete both JSON files after reading them: `rm -f ~/backups/home-hub-schedules/moze-schedules-*.json`.

4. **Caddy.** This release adds no SPA path. The entry form's definition mode is the query `?schedule=<id>` on `/accounting/entry`, the manage sheet is `?schedule=<id>` on `/accounting/reminders`, and `app.routes.ts` is unchanged on this branch. The deep links `/accounting/reminders?tab=debts&schedule=<id>` (from the entry detail and settings) and `/accounting/entry?schedule=<id>` still need `/accounting/reminders` and `/accounting/entry` in the live `@hub_spa` matcher. The 2a matcher lists `entry` but not `reminders`: 提醒中心 came with a later PR whose path was never added to the matcher. Check both paths:

   ```bash
   for p in accounting/reminders accounting/entry; do
     printf '%s %s\n' "$(curl -s -o /dev/null -w '%{http_code}' "https://oracle.saola-mamba.ts.net/hub/$p")" "$p"
   done
   ```

   Expected: `200` for both. A `404` for `accounting/reminders` is likely. In that case, add `reminders` to the matcher in place, with the same method as the 2a "Caddy `@hub_spa` matcher" procedure. The container bind-mounts the file, so keep the inode: no `sed -i`.

   ```bash
   cd /home/opc/workspace/vaultwarden
   command cp -p Caddyfile Caddyfile.bak-accounting-schedules
   python3 - <<'EOF'
   path = "Caddyfile"
   text = open(path, encoding="utf-8").read()
   old = "|/accounting/(accounts|dashboard|transactions|settings|cards|categories|recurring|entry)|"
   new = "|/accounting/(accounts|dashboard|transactions|settings|cards|categories|recurring|entry|reminders)|"
   assert text.count(old) == 1, "unexpected @hub_spa line (is the 2a matcher in place?)"
   with open(path, "r+", encoding="utf-8") as handle:
       handle.write(text.replace(old, new))
       handle.truncate()
   EOF
   grep -n '@hub_spa path_regexp' Caddyfile
   docker exec caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
   docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
   ```

   Then re-run the loop. Expected: `Valid configuration`, then `200` for both paths.

   If the `assert` fails, the live matcher is not the 2a one: stop and apply the 2a matcher first, as the Prerequisite section says.

   To undo this Caddy change: `cat Caddyfile.bak-accounting-schedules > Caddyfile`, then the same validate and reload.

5. **Owner check.** Run the verify checklist on production:
   - one loan: its 剩餘 after the posted periods, compared with MOZE;
   - one recurring transfer;
   - one 提醒入帳 item.

   The bell counts the due 提醒入帳 periods.

## During the mirror period (before `ACCOUNTING_IMPORT_LOCKED=true`)

- **Imported definitions are locked.** Editing or deleting one answers 409 `locked_until_cutover`. So does 重新入帳 (or `reopen`) of a period MOZE booked. Pause, resume, end, mode, catch-up, skip, post and the scoped amount edits (`following` / `all`) all work.
- **Re-import whenever MOZE has new records.**
  - A period HomeHub posted or skipped is not imported again from MOZE. The report counts it under `past_records_already_posted` or `past_records_already_skipped`.
  - A period the owner edited while it was pending keeps the owner's choice (`past_records_owner_pending`).
  - An amount difference is listed per line under `past_records_amount_differs` or `amount_differs`, for a manual check.
- **Writes during an import.** For the seconds an import holds its lock, schedule writes answer 409 `import_running`, and the SPA shows the toast `匯入進行中，請稍後再試`. The importer waits up to 30 s for running schedule writes, then refuses (retry).

## Rollback

**Window: 3 days**, as in phase 2a.

**`alembic downgrade 7b1e4a2c9d05` is not the rollback.** Use it only for a migration that failed in step 1. The downgrade guard refuses (`refusing to downgrade c4e8b2f1a7d3: …`) as soon as any `schedule` entry exists, or any period was posted or skipped by HomeHub (`acted_by` `auto` / `owner`). The guard does not cover everything, though. When the downgrade runs, it:

- silently drops every definition and period, including definitions the owner created locally that have only pending periods;
- drops the owner's period edits and pause / mode choices;
- recreates `moze_schedule` empty. Only a backup import made with the old code refills it.

**The rollback is the restore-and-swap of the phase 2a runbook**, with the script below and the `*-pre-schedules-$TS.*` files. The script does everything up to the database being back:

1. verifies the restored copy: the phase 2a head `7b1e4a2c9d05` and the manifest counts;
2. stops the backend;
3. swaps the database names;
4. checks out the pre-deploy backend commit;
5. starts the backend.

The restored dump already holds the pre-deploy `moze_schedule` rows, so no re-import is needed.

**After `ROLLBACK-DB-OK`:**

- Republish the previous SPA release (`spa-release-pre-schedules-$TS.txt`), as in step 1 of the 2a rollback.
- Caddy needs nothing. If step 4 added `reminders`, keep it: the previous release serves 提醒中心 at `/accounting/reminders` too.
- Keep `accounting_db_broken_<ts>` until the window ends, as in phase 2a.

Save as `~/backups/home-hub-schedules/rollback-schedules.sh` (mode `700`) in step 0:

```bash
#!/usr/bin/env bash
# Usage: rollback-schedules.sh <TS from step 0> [dump path: failure drill only]
set -euo pipefail
TS=${1:?usage: rollback-schedules.sh <TS> [dump]}
REPO=/home/opc/workspace/home-hub
B=$HOME/backups/home-hub-schedules
DUMP=${2:-$B/accounting_db-pre-schedules-$TS.dump}
MANIFEST=$B/accounting_db-pre-schedules-$TS.counts
PREV_BACKEND=$(cat "$B/backend-commit-pre-schedules-$TS.txt")
VENV=$REPO/services/accounting-service/.venv/bin
RTS=$(date -u +%Y%m%d%H%M%S)   # digits only: an unquoted database name is folded to lower case
COUNTS_SQL='SELECT (SELECT count(*) FROM account), (SELECT count(*) FROM ledger_entry), (SELECT count(*) FROM category), (SELECT count(*) FROM project), (SELECT count(*) FROM import_run)'
pg() { docker exec -i stonk-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -v ON_ERROR_STOP=1 -At -d "$0"' "$1"; }   # SQL on stdin
fail() { echo "ROLLBACK STOPPED: $*" >&2; exit 1; }

# a. preconditions: no leftover restore database, inputs present, clean checkout
[ "$(pg postgres <<<"SELECT count(*) FROM pg_database WHERE datname = 'accounting_db_restore'")" = 0 ] \
  || fail "accounting_db_restore exists (interrupted attempt?): inspect it, drop it explicitly with dropdb --force accounting_db_restore, then re-run"
{ [ -s "$DUMP" ] && [ -s "$MANIFEST" ]; } || fail "dump or manifest missing"
{ git -C "$REPO" diff --quiet && git -C "$REPO" diff --cached --quiet; } || fail "production checkout has local changes"

# b. restore into a new database; any pg_restore error stops here (the schedules release keeps running, accounting_db untouched)
docker exec stonk-postgres-1 sh -c 'createdb -U "$POSTGRES_USER" accounting_db_restore'
docker exec -i stonk-postgres-1 sh -c 'pg_restore -U "$POSTGRES_USER" -Fc --exit-on-error --no-owner -d accounting_db_restore' < "$DUMP" \
  || fail "pg_restore failed; accounting_db untouched, accounting_db_restore left for inspection"

# c. verify the restored copy BEFORE any service stop or rename: phase 2a head and the manifest's row counts
WORK=$(mktemp -d) && chmod 700 "$WORK"
trap 'git -C "$REPO" worktree remove --force "$WORK/old" >/dev/null 2>&1 || true; rm -rf "$WORK"' EXIT
git -C "$REPO" worktree add --quiet --detach "$WORK/old" "$PREV_BACKEND"
sed -e 's/^ACCOUNTING_DB=.*/ACCOUNTING_DB=accounting_db_restore/' "$REPO/.env" > "$WORK/old/.env" && chmod 600 "$WORK/old/.env"
HEAD_LINE=$(cd "$WORK/old/services/accounting-service" && { "$VENV/alembic" current 2>/dev/null || true; } | tail -1)
[ "$HEAD_LINE" = "7b1e4a2c9d05 (head)" ] || fail "alembic current on accounting_db_restore printed '$HEAD_LINE'"
GOT=$(pg accounting_db_restore <<<"$COUNTS_SQL")
WANT=$(sed -n 2p "$MANIFEST")
[ "$GOT" = "$WANT" ] || fail "restored row counts $GOT differ from the dump manifest $WANT"
echo "restore verified: 7b1e4a2c9d05 (head), counts $GOT"

# d. only now: stop the backend, require no sessions, swap names in one psql session, previous backend, start
cd "$REPO" && npx pm2 stop accounting-service
SESSIONS=$(pg postgres <<<"SELECT datname, pid, application_name, state FROM pg_stat_activity WHERE datname IN ('accounting_db', 'accounting_db_restore')")
if [ -n "$SESSIONS" ]; then
  echo "$SESSIONS" >&2
  npx pm2 start accounting-service
  fail "sessions connected (listed above); backend restarted on the schedules release; close them, dropdb --force accounting_db_restore, re-run"
fi
docker exec stonk-postgres-1 sh -c "psql -U \"\$POSTGRES_USER\" -d postgres -v ON_ERROR_STOP=1 -c 'ALTER DATABASE accounting_db RENAME TO accounting_db_broken_$RTS; ALTER DATABASE accounting_db_restore RENAME TO accounting_db;'"
[ "$(pg accounting_db <<<'SELECT version_num FROM alembic_version')" = 7b1e4a2c9d05 ] \
  || fail "accounting_db after the swap is not 7b1e4a2c9d05; backend left stopped"
git -C "$REPO" switch --detach "$PREV_BACKEND"
npx pm2 start accounting-service
for _ in $(seq 30); do [ "$(curl -s http://localhost:8000/health)" = '{"status":"ok"}' ] && break; sleep 1; done
[ "$(curl -s http://localhost:8000/health)" = '{"status":"ok"}' ] || fail "health check failed after start on $PREV_BACKEND"
echo "ROLLBACK-DB-OK accounting_db_broken_$RTS"
```

Run it with `$TS` from step 0: `bash ~/backups/home-hub-schedules/rollback-schedules.sh "$TS"; echo "exit=$?"`.

Expected output, in order:

1. `restore verified: 7b1e4a2c9d05 (head), counts <the manifest's line>`;
2. the pm2 stop output;
3. `ALTER DATABASE` twice;
4. the pm2 start output;
5. `ROLLBACK-DB-OK accounting_db_broken_<ts>`;
6. `exit=0`.

A `ROLLBACK STOPPED:` line means what the 2a runbook says it means.

Afterwards, return the checkout to its branch once the release is fixed, and redeploy from step 0 with a new dump and manifest, as in phase 2a.
