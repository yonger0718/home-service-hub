# Accounting phase 2a deploy (add-accounting-entry)

Spec: `openspec/changes/add-accounting-entry/`. Backend endpoints and the import CLI: [`services/accounting-service/README.md`](../../services/accounting-service/README.md).

## Configuration

Add to the root `.env` (and `.env.example` already documents both):

```text
ACCOUNTING_IMPORT_LOCKED=false
MOZE_REALM_EXPORTER=
```

`MOZE_REALM_EXPORTER` empty means the default command `node <repo>/tools/moze-realm-export/index.js`. Set it only when `node` is not on the service's `PATH`, e.g. `MOZE_REALM_EXPORTER=/usr/bin/node /home/opc/workspace/home-hub/tools/moze-realm-export/index.js`. `ACCOUNTING_IMPORT_LOCKED` stays `false` until the 2b cutover; while it is false, MOZE-imported rows are read-only and imports run.

## Converter install (once per checkout, Node ≥ 20)

```bash
cd /home/opc/workspace/home-hub/tools/moze-realm-export && npm ci
node --version
```

The converter copies `moze.realm` into a scratch directory before opening it; it needs write access to the system temp directory (or `--work <dir>`).

## Deploy order

0. **Pre-deploy record and dump** (before `alembic upgrade`; nothing below prints credentials — `pg_dump` / `pg_restore` read `POSTGRES_USER` inside the container)

   ```bash
   TS=$(date -u +%Y%m%dT%H%M%SZ)
   mkdir -p ~/backups/home-hub-2a && chmod 700 ~/backups/home-hub-2a
   git -C /home/opc/workspace/home-hub rev-parse HEAD > ~/backups/home-hub-2a/backend-commit-pre-2a-$TS.txt
   python3 -c "import json; print(json.load(open('/var/lib/home-hub-production/store/state.json'))['active'])" \
     > ~/backups/home-hub-2a/spa-release-pre-2a-$TS.txt
   readlink /var/lib/home-hub-production/store/current
   docker exec stonk-postgres-1 sh -c 'pg_dump -U "$POSTGRES_USER" -Fc accounting_db' \
     > ~/backups/home-hub-2a/accounting_db-pre-2a-$TS.dump
   printf 'account|ledger_entry|category|project|import_run\n' > ~/backups/home-hub-2a/accounting_db-pre-2a-$TS.counts
   docker exec stonk-postgres-1 sh -c "psql -U \"\$POSTGRES_USER\" -d accounting_db -At -c 'SELECT (SELECT count(*) FROM account), (SELECT count(*) FROM ledger_entry), (SELECT count(*) FROM category), (SELECT count(*) FROM project), (SELECT count(*) FROM import_run)'" \
     >> ~/backups/home-hub-2a/accounting_db-pre-2a-$TS.counts
   cat ~/backups/home-hub-2a/accounting_db-pre-2a-$TS.counts
   chmod 600 ~/backups/home-hub-2a/*-$TS.*
   ls -l ~/backups/home-hub-2a/
   docker exec -i stonk-postgres-1 pg_restore -l < ~/backups/home-hub-2a/accounting_db-pre-2a-$TS.dump | grep -c ' TABLE DATA '
   ```

   Expected: the `readlink` target ends in the id written to `spa-release-pre-2a-$TS.txt` (the active release in `/var/lib/home-hub-production/store`); the manifest prints its header and one line of five numbers (`account|ledger_entry|category|project|import_run` row counts of `accounting_db`, taken right after the dump; step 0b proves they match the dump, so a write that slipped in between is caught before the deploy — then redo step 0); a non-empty `.dump`, the `.counts` manifest and the two `.txt` files, mode `600`; a non-zero `TABLE DATA` count. Keep `$TS`: the rollback script names these four files by it.

0b. **Restore drill** (same session, before step 1): rehearse the rollback of the "Rollback" section on disposable databases — a copy of the dump that has been upgraded to 2a and holds 2a data plays `accounting_db`, a second restore of the pre-2a dump plays `accounting_db_restore`, and their names are swapped exactly as the rollback does; then the **failure drill** runs the real rollback script against a truncated copy of the dump and must stop before any service stop or rename. First save the script block of the "Rollback" section verbatim as `~/backups/home-hub-2a/rollback-2a.sh` and `chmod 700` it. `NEW_BACKEND` is the 2a commit being deployed (the PR's merge commit); the production checkout, its services and the name and contents of `accounting_db` are not touched (the failure drill only creates and drops `accounting_db_restore`).

   ```bash
   NEW_BACKEND=<2a commit being deployed>
   DUMP=~/backups/home-hub-2a/accounting_db-pre-2a-$TS.dump
   VENV=/home/opc/workspace/home-hub/services/accounting-service/.venv/bin
   DRILL=$(mktemp -d) && chmod 700 "$DRILL"
   git -C /home/opc/workspace/home-hub worktree add --detach "$DRILL/new" "$NEW_BACKEND"
   git -C /home/opc/workspace/home-hub worktree add --detach "$DRILL/old" "$(cat ~/backups/home-hub-2a/backend-commit-pre-2a-$TS.txt)"
   for tree in new old; do
     sed -e 's/^ACCOUNTING_DB=.*/ACCOUNTING_DB=accounting_drill_2a/' /home/opc/workspace/home-hub/.env > "$DRILL/$tree/.env"
     chmod 600 "$DRILL/$tree/.env" && grep -c '^ACCOUNTING_DB=accounting_drill_2a$' "$DRILL/$tree/.env"
   done

   # a. disposable copy of the dump, upgraded to 2a and holding 2a data (a real backup import, not a dry run)
   docker exec stonk-postgres-1 sh -c 'createdb -U "$POSTGRES_USER" accounting_drill_2a'
   docker exec -i stonk-postgres-1 sh -c 'pg_restore -U "$POSTGRES_USER" -Fc --exit-on-error --no-owner -d accounting_drill_2a' < "$DUMP"
   (cd "$DRILL/new/tools/moze-realm-export" && npm ci --silent)
   (cd "$DRILL/new/services/accounting-service" && "$VENV/alembic" upgrade head)
   (cd "$DRILL/new/services/accounting-service" && "$VENV/python" -m app.services.moze_backup_import_service ~/workspace/moze-backup/MOZE_4.0.zip > "$DRILL/import.json") && echo import-ok
   docker exec stonk-postgres-1 sh -c "psql -U \"\$POSTGRES_USER\" -d accounting_drill_2a -At -c \"SELECT (SELECT version_num FROM alembic_version), (SELECT count(*) FROM ledger_entry WHERE source = 'moze_backup')\""

   # b. second disposable database from the pre-2a dump, then the name swap of the rollback
   docker exec stonk-postgres-1 sh -c 'createdb -U "$POSTGRES_USER" accounting_drill_restore'
   docker exec -i stonk-postgres-1 sh -c 'pg_restore -U "$POSTGRES_USER" -Fc --exit-on-error --no-owner -d accounting_drill_restore' < "$DUMP"
   docker exec stonk-postgres-1 sh -c "psql -U \"\$POSTGRES_USER\" -d accounting_drill_restore -At -c 'SELECT (SELECT count(*) FROM account), (SELECT count(*) FROM ledger_entry), (SELECT count(*) FROM category), (SELECT count(*) FROM project), (SELECT count(*) FROM import_run)'"
   sed -n 2p ~/backups/home-hub-2a/accounting_db-pre-2a-$TS.counts
   docker exec stonk-postgres-1 sh -c "psql -U \"\$POSTGRES_USER\" -d postgres -At -c \"SELECT datname, count(*) FROM pg_stat_activity WHERE datname IN ('accounting_drill_2a', 'accounting_drill_restore') GROUP BY datname\""
   docker exec stonk-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE accounting_drill_2a RENAME TO accounting_drill_broken; ALTER DATABASE accounting_drill_restore RENAME TO accounting_drill_2a;"'

   # c. the swapped-in database is the pre-2a state; the swapped-out one holds the 2a data
   (cd "$DRILL/old/services/accounting-service" && "$VENV/alembic" current)
   for db in accounting_db accounting_drill_2a; do
     docker exec stonk-postgres-1 sh -c "psql -U \"\$POSTGRES_USER\" -d $db -At -c 'SELECT (SELECT count(*) FROM account), (SELECT count(*) FROM ledger_entry), (SELECT count(*) FROM import_run)'"
   done
   docker exec stonk-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d accounting_drill_broken -At -c "SELECT version_num FROM alembic_version"'

   # d. failure drill: the real rollback script on a truncated dump stops before any pm2 stop or rename
   head -c $(( $(stat -c %s "$DUMP") / 2 )) "$DUMP" > "$DRILL/truncated.dump"
   bash ~/backups/home-hub-2a/rollback-2a.sh "$TS" "$DRILL/truncated.dump"; echo "exit=$?"
   docker exec stonk-postgres-1 sh -c "psql -U \"\$POSTGRES_USER\" -d postgres -At -c \"SELECT datname FROM pg_database WHERE datname LIKE 'accounting_db%' ORDER BY 1\""
   cd /home/opc/workspace/home-hub && npx pm2 describe accounting-service | grep -m1 ' status '
   git -C /home/opc/workspace/home-hub rev-parse HEAD; cat ~/backups/home-hub-2a/backend-commit-pre-2a-$TS.txt
   bash ~/backups/home-hub-2a/rollback-2a.sh "$TS"; echo "exit=$?"
   docker exec stonk-postgres-1 sh -c 'dropdb -U "$POSTGRES_USER" --force accounting_db_restore'

   # e. clean up
   git -C /home/opc/workspace/home-hub worktree remove --force "$DRILL/new"
   git -C /home/opc/workspace/home-hub worktree remove --force "$DRILL/old" && rm -rf "$DRILL"
   docker exec stonk-postgres-1 sh -c 'dropdb -U "$POSTGRES_USER" --force accounting_drill_2a'
   docker exec stonk-postgres-1 sh -c 'dropdb -U "$POSTGRES_USER" --force accounting_drill_broken'
   ```

   Expected: `1` twice (both `.env` copies point at the drill database); `alembic upgrade head` ends at `-> 7b1e4a2c9d05`; `import-ok`; `7b1e4a2c9d05|<n>` with `n > 0` (the copy holds 2a schema and 2a data, so its foreign keys are the ones a real rollback meets); the restored `accounting_drill_restore` counts line equals the manifest's second line (otherwise the manifest does not describe the dump: redo step 0); the `pg_stat_activity` query prints nothing; `ALTER DATABASE` twice (both renames run in one session as one implicit transaction: either both apply or neither); `alembic current` on the swapped-in `accounting_drill_2a` prints `5d2e7c9a1b3f (head)` (the phase 1 head); the two count lines are identical (`accounting_db` still holds exactly the dumped state, since step 1 has not run); `accounting_drill_broken` reads `7b1e4a2c9d05`. Failure drill: the first script run ends with a `pg_restore` error and `ROLLBACK STOPPED: pg_restore failed …`, then `exit=1`; it prints no `restore verified`, no pm2 output, no `ALTER DATABASE` and no `ROLLBACK-DB-OK`; the `pg_database` query lists exactly `accounting_db` and `accounting_db_restore` (no `accounting_db_broken_*`: the live name is untouched); pm2 shows `status │ online`; the two commit lines are identical (the checkout was not switched); the second run (leftover still present) prints the script's leftover refusal for `accounting_db_restore` and `exit=1` without restoring anything; `dropdb` removes the leftover. If the first run reaches `restore verified` or anything after it, the script is wrong: stop, do not deploy. Any difference or error: drop whichever drill databases exist (`accounting_drill_2a`, `accounting_drill_restore`, `accounting_drill_broken`, and `accounting_db_restore` left by the failure drill), remove the worktrees, stop, do not deploy.

1. **Migration**

   ```bash
   cd /home/opc/workspace/home-hub/services/accounting-service && .venv/bin/alembic upgrade head
   ```

   Expected: `Running upgrade … -> 7b1e4a2c9d05`.

2. **Backend**

   ```bash
   cd /home/opc/workspace/home-hub && npx pm2 restart accounting-service
   curl -s http://localhost:8000/health
   curl -s http://localhost:8000/preference
   ```

   Expected: `{"status":"ok"}` and the preference row (defaults on first read).

3. **Frontend publish**: `cd /home/opc/workspace/home-hub/frontend && npm run build`, then publish `frontend/dist/inventory-ui/browser` with the existing production publisher (the procedure that fills `/var/lib/home-hub-production/store/current/web`).

4. **Caddy**: replace the `@hub_spa` matcher (see "Caddy `@hub_spa` matcher" below) and reload.

5. **Backup import**: dry run first, read the report, then the real run.

   ```bash
   cd /home/opc/workspace/home-hub/services/accounting-service
   .venv/bin/python -m app.services.moze_backup_import_service ~/workspace/moze-backup/MOZE_4.0.zip --dry-run > /tmp/moze-2a-dry-run.json
   .venv/bin/python -m app.services.moze_backup_import_service ~/workspace/moze-backup/MOZE_4.0.zip > /tmp/moze-2a-import.json
   ```

   Or from the SPA: 記帳設定 → 匯入 → 試算 → 匯入. Check `compared_accounts`, `skipped_future`, `needs_review` and the accounts whose `moze_part` differs from `previous_moze_part`, then spot-check the timeline and a few passbooks against MOZE.

## Caddy `@hub_spa` matcher

Exact new line (inside `handle /hub/*`, after `uri strip_prefix /hub`):

```caddyfile
            @hub_spa path_regexp hub_spa "^(/|/shopping-list|/settings|/portfolio|/portfolio/(transactions|dividends|realized-pnl|accounts|import|import-broker)|/portfolio/accounts/[0-9]+|/accounting|/accounting/(accounts|dashboard|transactions|settings|cards|categories|recurring|entry)|/accounting/accounts/new|/accounting/accounts/[0-9]+|/accounting/accounts/[0-9]+/settings|/accounting/entries/[0-9]+|/accounting/entries/[0-9]+/edit)$"
```

New SPA paths: `/accounting` (timeline), `/accounting/entry`, `/accounting/settings` (now a page), `/accounting/accounts/new`, `/accounting/accounts/<n>/settings`, `/accounting/entries/<n>`, `/accounting/entries/<n>/edit`. The phase 1 paths (`dashboard`, `transactions`, `cards`, `categories`, `recurring`) stay so bookmarks reach the SPA, which redirects them to `/accounting`. Query strings (`?kind=`, `?copy=`) are not part of the path.

Edit in place (the container bind-mounts the file, so keep the inode; no `sed -i`):

```bash
cd /home/opc/workspace/vaultwarden
command cp -p Caddyfile Caddyfile.bak-accounting-2a
python3 - <<'EOF'
path = "Caddyfile"
text = open(path, encoding="utf-8").read()
old = '|/accounting|/accounting/(accounts|dashboard|transactions|settings|cards|categories|recurring)|/accounting/accounts/[0-9]+)$"'
new = ('|/accounting|/accounting/(accounts|dashboard|transactions|settings|cards|categories|recurring|entry)'
       '|/accounting/accounts/new|/accounting/accounts/[0-9]+|/accounting/accounts/[0-9]+/settings'
       '|/accounting/entries/[0-9]+|/accounting/entries/[0-9]+/edit)$"')
assert text.count(old) == 1, "unexpected @hub_spa line"
with open(path, "r+", encoding="utf-8") as handle:
    handle.write(text.replace(old, new))
    handle.truncate()
EOF
grep -n '@hub_spa path_regexp' Caddyfile
docker exec caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
for p in accounting accounting/entry accounting/settings accounting/accounts/new accounting/accounts/12/settings accounting/entries/34 accounting/entries/34/edit accounting/cards; do
  printf '%s %s\n' "$(curl -s -o /dev/null -w '%{http_code}' "https://oracle.saola-mamba.ts.net/hub/$p")" "$p"
done
```

Expected: the `grep` line equals the matcher above, `Valid configuration`, and `200` for every path.

## Rollback (restore the pre-deploy dump into a new database and swap names)

Rollback window: **3 days** after the deploy. A rollback returns `accounting_db` to the moment of the step 0 dump and discards every write since then (entries recorded in the SPA, backup imports, account and display settings) — they stay readable in `accounting_db_broken_<ts>` until the window ends. Inside the window, note what the owner recorded since the deploy before rolling back; after 3 days, fix forward instead. Keep the files in `~/backups/home-hub-2a/` at least until the window has passed.

`alembic downgrade` is **not** the rollback: Task 3's guard refuses on purpose as soon as any 2a data exists (`manual` / `hermes` / `rule` entries, `moze_backup` entries, account groups, rules, schedules, a `moze_backup` import run). `.venv/bin/alembic downgrade 5d2e7c9a1b3f` is only for a migration that failed in step 1, before any import or entry.

`pg_restore --clean` into the live `accounting_db` is **not** the rollback either and must not be used: it fails on the foreign keys of the 2a tables, which reference phase 1 tables the dump wants to drop (verified). The rollback restores into a new database and swaps the database names; the 2a database is renamed, never cleaned or modified.

Rollback script. Save it as `~/backups/home-hub-2a/rollback-2a.sh` (mode `700`) in step 0b, whose failure drill runs it. Every step is a precondition for the next: nothing stops the backend or renames a database until the restored copy has proven itself, and any failure ends the script with a `ROLLBACK STOPPED:` line.

```bash
#!/usr/bin/env bash
# Usage: rollback-2a.sh <TS from step 0> [dump path: failure drill only]
set -euo pipefail
TS=${1:?usage: rollback-2a.sh <TS> [dump]}
REPO=/home/opc/workspace/home-hub
B=$HOME/backups/home-hub-2a
DUMP=${2:-$B/accounting_db-pre-2a-$TS.dump}
MANIFEST=$B/accounting_db-pre-2a-$TS.counts
PREV_BACKEND=$(cat "$B/backend-commit-pre-2a-$TS.txt")
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

# b. restore into a new database; any pg_restore error stops here (2a keeps running, accounting_db untouched)
docker exec stonk-postgres-1 sh -c 'createdb -U "$POSTGRES_USER" accounting_db_restore'
docker exec -i stonk-postgres-1 sh -c 'pg_restore -U "$POSTGRES_USER" -Fc --exit-on-error --no-owner -d accounting_db_restore' < "$DUMP" \
  || fail "pg_restore failed; accounting_db untouched, accounting_db_restore left for inspection"

# c. verify the restored copy BEFORE any service stop or rename: phase 1 head and the manifest's row counts
WORK=$(mktemp -d) && chmod 700 "$WORK"
trap 'git -C "$REPO" worktree remove --force "$WORK/old" >/dev/null 2>&1 || true; rm -rf "$WORK"' EXIT
git -C "$REPO" worktree add --quiet --detach "$WORK/old" "$PREV_BACKEND"
sed -e 's/^ACCOUNTING_DB=.*/ACCOUNTING_DB=accounting_db_restore/' "$REPO/.env" > "$WORK/old/.env" && chmod 600 "$WORK/old/.env"
HEAD_LINE=$(cd "$WORK/old/services/accounting-service" && { "$VENV/alembic" current 2>/dev/null || true; } | tail -1)
[ "$HEAD_LINE" = "5d2e7c9a1b3f (head)" ] || fail "alembic current on accounting_db_restore printed '$HEAD_LINE'"
GOT=$(pg accounting_db_restore <<<"$COUNTS_SQL")
WANT=$(sed -n 2p "$MANIFEST")
[ "$GOT" = "$WANT" ] || fail "restored row counts $GOT differ from the dump manifest $WANT"
echo "restore verified: 5d2e7c9a1b3f (head), counts $GOT"

# d. only now: stop the backend, require no sessions, swap names in one psql session, previous backend, start
cd "$REPO" && npx pm2 stop accounting-service
SESSIONS=$(pg postgres <<<"SELECT datname, pid, application_name, state FROM pg_stat_activity WHERE datname IN ('accounting_db', 'accounting_db_restore')")
if [ -n "$SESSIONS" ]; then
  echo "$SESSIONS" >&2
  npx pm2 start accounting-service
  fail "sessions connected (listed above); backend restarted on 2a; close them, dropdb --force accounting_db_restore, re-run"
fi
docker exec stonk-postgres-1 sh -c "psql -U \"\$POSTGRES_USER\" -d postgres -v ON_ERROR_STOP=1 -c 'ALTER DATABASE accounting_db RENAME TO accounting_db_broken_$RTS; ALTER DATABASE accounting_db_restore RENAME TO accounting_db;'"
[ "$(pg accounting_db <<<'SELECT version_num FROM alembic_version')" = 5d2e7c9a1b3f ] \
  || fail "accounting_db after the swap is not 5d2e7c9a1b3f; backend left stopped"
git -C "$REPO" switch --detach "$PREV_BACKEND"
npx pm2 start accounting-service
for _ in $(seq 30); do [ "$(curl -s http://localhost:8000/health)" = '{"status":"ok"}' ] && break; sleep 1; done
[ "$(curl -s http://localhost:8000/health)" = '{"status":"ok"}' ] || fail "health check failed after start on $PREV_BACKEND"
echo "ROLLBACK-DB-OK accounting_db_broken_$RTS"
```

Run it (`$TS` from step 0):

```bash
bash ~/backups/home-hub-2a/rollback-2a.sh "$TS"; echo "exit=$?"
PREV_SPA=$(cat ~/backups/home-hub-2a/spa-release-pre-2a-$TS.txt)
```

Expected: `restore verified: 5d2e7c9a1b3f (head), counts <the manifest's line>`, the pm2 stop output, `ALTER DATABASE` twice (both renames in one `psql -c`, one implicit transaction: both or neither), the pm2 start output, `ROLLBACK-DB-OK accounting_db_broken_<ts>` and `exit=0`. Note the printed `accounting_db_broken_<ts>` name. A `ROLLBACK STOPPED:` line means nothing after it ran: up to and including step c, the backend still serves 2a on the untouched `accounting_db` and `accounting_db_restore` is left for inspection (drop it explicitly before a re-run, which the script otherwise refuses); a stop in step d says what it left. Run the steps below **only after `ROLLBACK-DB-OK`**.

1. **SPA**: republish release `$PREV_SPA` through the publisher used for phase 1 (the `publish_approved.py` of the phase 1 release workflow directory, `~/reports/home-hub-moze-release-*/`), following that workflow's own steps; then `readlink /var/lib/home-hub-production/store/current` must end in `$PREV_SPA`.

2. **Caddy**: restore the phase 1 `@hub_spa` line from `Caddyfile.bak-accounting-2a` in place (keeps the inode of the bind-mounted file), validate and reload:

   ```bash
   cd /home/opc/workspace/vaultwarden
   cat Caddyfile.bak-accounting-2a > Caddyfile
   grep -n '@hub_spa path_regexp' Caddyfile
   docker exec caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
   docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
   ```

   Expected: the phase 1 matcher (no `/accounting/entries`), `Valid configuration`.

3. Keep `accounting_db_broken_<ts>` untouched until the 3-day window after the deploy has ended (it holds every 2a write, for re-entering what the owner recorded); then drop it: `docker exec stonk-postgres-1 sh -c 'dropdb -U "$POSTGRES_USER" accounting_db_broken_<ts>'`.

4. Return the checkout to its branch once 2a is fixed (`git -C /home/opc/workspace/home-hub switch <branch>`), and redeploy from step 0 with a new dump and manifest.
