# Backup and restore

Traceforge keeps everything (users, projects, API key hashes, traces, datasets, runs, diagnoses, experiments,
decisions, audit log) in PostgreSQL 16 in the Docker container `traceforge-postgres`, whose data lives in the
Docker volume `traceforge_pgdata`. There are three databases:

| Database | Role | Back it up? |
| --- | --- | --- |
| `traceforge_dev` | Default database of `python -m app.serve`: your local data | **Yes** |
| `traceforge` | Main database, used only with `--database main` | Yes, if you use it, and always before migrating it |
| `traceforge_test` | Disposable; the test suite truncates it | No |

Use logical backups (`pg_dump`, one database at a time, custom format). They are consistent while the server is
running, small, portable across PostgreSQL 16 minor versions and verifiable without restoring. Do not copy the
volume's files while PostgreSQL is running.

## Prerequisites

- Docker Desktop running and the container healthy: `docker ps --filter name=traceforge-postgres` shows `healthy`.
- PowerShell in the repository root. The commands run `pg_dump` *inside* the container as `POSTGRES_USER` (from the
  container's own environment), so no password is typed, shown or stored in the backup command.
- A folder outside the repository for backups, e.g. `$HOME\traceforge-backups`. **Backups contain sensitive data**
  (password hashes, API key hashes, everything your agents recorded): keep them private and never commit them.

Always write the dump to a file inside the container and copy it out with `docker cp`. Redirecting `pg_dump`
output with `>` in Windows PowerShell 5.1 re-encodes the bytes and corrupts the archive.

## Back up

```powershell
$db = "traceforge_dev"                                  # or "traceforge"
$dir = "$HOME\traceforge-backups"; New-Item -ItemType Directory -Force $dir | Out-Null
$name = "$db-$(Get-Date -Format yyyyMMdd-HHmmss).dump"

docker exec traceforge-postgres sh -c "pg_dump -U `"`$POSTGRES_USER`" -d $db --format=custom --file=/tmp/$name"
docker cp "traceforge-postgres:/tmp/$name" "$dir\$name"
docker exec traceforge-postgres sha256sum "/tmp/$name"  # note the checksum
docker exec traceforge-postgres rm -f "/tmp/$name"
(Get-FileHash "$dir\$name" -Algorithm SHA256).Hash      # must equal the checksum above (case aside)
```

`/tmp` is the container's own filesystem, not the data volume.

## Verify a backup (no database is touched)

```powershell
$file = "$HOME\traceforge-backups\traceforge_dev-YYYYMMDD-HHMMSS.dump"
docker cp $file traceforge-postgres:/tmp/verify.dump
docker exec traceforge-postgres pg_restore --list /tmp/verify.dump > "$file.list.txt"
docker exec traceforge-postgres rm -f /tmp/verify.dump
Select-String -Path "$file.list.txt" -Pattern "dbname:|Dumped from"
(Select-String -Path "$file.list.txt" -Pattern " TABLE public ").Count        # 21 at schema revision 0007 (20 at 0006, 19 at 0005)
(Select-String -Path "$file.list.txt" -Pattern " TRIGGER public ").Count      # 10 at schema revisions 0005 to 0007
Select-String -Path "$file.list.txt" -Pattern "TABLE DATA public alembic_version"
```

A backup is only good if this listing works and shows every table, trigger and the `alembic_version` data. It
proves the archive is complete and readable; it does not prove that the data is what you expect, so also check
the restored copy (below) before relying on a backup for something important.

**Tested on 2026-10-31** against `traceforge_dev` (schema 0005, PostgreSQL 16.15): back-up, copy-out checksum
match, and verification from the host copy all succeeded (19 tables with data, 10 triggers, 8 functions,
`alembic_version` present). No restore was executed.

## Restore

> Not executed as part of the release test. Steps 3–4 replace data and cannot be undone: take a fresh backup of
> the current database first, and stop every Traceforge server (`python -m app.serve`, the test suite) before them.

1. **Restore into a new, scratch database** (non-destructive; leaves the existing database alone):

   ```powershell
   $file = "$HOME\traceforge-backups\traceforge_dev-YYYYMMDD-HHMMSS.dump"
   docker cp $file traceforge-postgres:/tmp/restore.dump
   docker exec traceforge-postgres sh -c "createdb -U `"`$POSTGRES_USER`" traceforge_restore_check"
   docker exec traceforge-postgres sh -c "pg_restore -U `"`$POSTGRES_USER`" -d traceforge_restore_check --no-owner --exit-on-error /tmp/restore.dump"
   ```

2. **Check the restored copy**: the revision and some row counts.

   ```powershell
   docker exec traceforge-postgres sh -c "psql -U `"`$POSTGRES_USER`" -d traceforge_restore_check -tAc 'select version_num from alembic_version'"
   docker exec traceforge-postgres sh -c "psql -U `"`$POSTGRES_USER`" -d traceforge_restore_check -tAc 'select count(*) from users; select count(*) from projects; select count(*) from traces'"
   ```

3. **Replace the original** (destructive). With all servers stopped:

   ```powershell
   docker exec traceforge-postgres sh -c "dropdb -U `"`$POSTGRES_USER`" traceforge_dev"
   docker exec traceforge-postgres sh -c "psql -U `"`$POSTGRES_USER`" -d postgres -c 'ALTER DATABASE traceforge_restore_check RENAME TO traceforge_dev'"
   docker exec traceforge-postgres rm -f /tmp/restore.dump
   ```

4. **Start Traceforge** (`python -m app.serve`). It refuses to start if the restored schema is not at the code's
   revision. A dev database from an *older* release is migrated forward automatically; a newer one is refused.
   The main database is never migrated automatically (see `docs/release-checklist.md`).

If anything fails in steps 1–2, drop the scratch database
(`dropdb -U "$POSTGRES_USER" traceforge_restore_check`) and keep the original untouched.

## Whole-volume copy (optional, offline only)

Copying `traceforge_pgdata` is only safe with PostgreSQL stopped (`docker compose stop postgres`), restores only
onto the same PostgreSQL major version, and replaces *all* databases at once. Prefer the logical backups above.
Not tested for this release.

## What backups do not cover

- Secrets in `.env` (`TRACEFORGE_SECRET_KEY`, `TRACEFORGE_API_KEY_PEPPER`, database password). Keep a separate,
  private copy. **Restoring with a different `TRACEFORGE_API_KEY_PEPPER` invalidates every API key**; a different
  secret key only signs everyone out.
- Work in progress. A backup taken while an evaluation, diagnosis or experiment is running restores it in that
  state; the recovery sweep then marks it failed (see below).

## Interrupted work after a crash or restore

Recovery is in-process (`backend/app/services/recovery.py`): there is no job queue and **interrupted work is marked
failed, never resumed**. At startup and every `TRACEFORGE_RECOVERY_INTERVAL_SECONDS` (60) the server fails:

- diagnoses that never started (after `TRACEFORGE_STALE_JOB_GRACE_SECONDS`, 120 s) or that stopped mid-way (after
  their time limit + provider timeout + grace, about 8 minutes by default) — use *Run again*;
- evaluation runs stuck in scoring (after the grace period, unless a live scorer holds the row) — re-run them;
- running experiments past their deadline (`TRACEFORGE_EXPERIMENT_TIME_LIMIT_SECONDS`, 1 h) and their arm runs.

Until a sweep runs, interrupted work keeps showing its last state. Experiments waiting for an executor and
evaluation runs driven by your SDK process have no heartbeat and are never failed automatically; abort them in
the console. An interrupted `traceforge.run_experiment(...)` can be started again before its deadline: arms that
were already scored are skipped. The sweep only runs while a server runs and assumes one server process per
database.
