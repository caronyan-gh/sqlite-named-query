# Reference

Details for specific tasks: writing exports, rebuilding tables, backups, the timing log. `SKILL.md` has what you need every time; read the section here when you do the task it describes. For every option of a command, run `nq.py <command> --help` (a wrong option also returns that command's usage).

## Database location

Older projects that keep the database at `<root>/project.db` keep working: if `db/project.db` does not exist and `<root>/project.db` does, that one is used. To move it, stop anything using the database and move `project.db` together with its `-wal` and `-shm` files into `db/`.

Caution: even when `--db` points at another database, exports are still written into the project directory. Trying write queries against a scratch database will overwrite the real exported files.

## Text projections

`db/exports/<name>.sql` is a single read statement returning `path` and `content` columns. For each row, `content` is written to `<root>/<path>`.

```sql
-- One markdown card per test
SELECT 'reports/tests/' || test_id || '.md' AS path,
       '# ' || test_id || char(10) || 'status: ' || status || char(10) AS content
FROM tests WHERE test_id = :test_id;
```

- Exports listed in a write query's `-- export:` header run automatically after a successful COMMIT. Each export receives only the parameters it references, taken from the write query's parameters.
- Paths outside the project directory are rejected (`../` and the like).
- Files are replaced via a temporary file. If the content is identical, the file is left untouched (listed under `unchanged`).
- If the write succeeded but an export failed, the result is `ok: false` with `write committed but export failed`. The data is already committed; fix the cause and re-run with `export`.

## Rebuilding a table (migrations)

In SQLite, changing a column constraint (such as CHECK) requires rebuilding the table. That fails while foreign-key enforcement is on, so put this line at the top of the migration:

```sql
-- nq: foreign_keys=off
```

Foreign keys are then disabled for that migration only, and `PRAGMA foreign_key_check` must come back empty before COMMIT (any violation rolls everything back). Enforcement is restored afterwards. The rebuild procedure is: create the new table → copy the rows → drop the old table → rename the new table.

Do not put `BEGIN` / `COMMIT` in migration files; the runner wraps each migration in its own transaction.

## Checking (`check`)

`check` verifies the following and lists issues under `problems` (any issue means `ok: false` and exit code 1). Run it whenever you add queries or schema.

- Applied migration files have not been modified since they were applied (compared with the SHA-256 recorded at `migrate` time; line-ending differences are ignored)
- Read queries contain exactly one statement, and queries declared `mode: read` contain no write statements
- Exports named in `-- export:` exist, and they do not use parameters the triggering query does not take
- Every statement compiles against the current database (via `EXPLAIN`; nothing is executed). Skipped while migrations are pending

Migrations applied before checksums were introduced are recorded from the current file contents on the next `migrate` (`checksumsAdopted`).

## Backups

`backup` copies the database with SQLite's online backup API, so it is safe while the database is in use (WAL included). Backups go to `db/backup/project-<label>-<YYYYMMDD-HHMMSS>.db` (just `project-<YYYYMMDD-HHMMSS>.db` without `--label`; the date-time means the same label never overwrites an earlier backup), only the newest `--keep` (default 7) are kept — "newest" is read from the date-time in the file name, not the file's modified time, which copying or unzipping can reset — and `db/backup/` gets its own `.gitignore` so backups never end up in git. The command only backs up when called; when to call it (every tenth task, before a risky migration, ...) is the project's decision, typically made in one of its own scripts. A failed backup returns `ok: false` and changes nothing, so the caller can warn and carry on.

## Dump and restore

`backup` keeps copies on the same PC. To carry the database somewhere else, or to keep a copy you can read and diff, use `dump`: it writes the whole database as SQL text — tables by name, rows in primary-key order (rowid when a table has none), then indexes, views and triggers — so the same database always gives the same text. The timing log is a separate file and is not included.

- Default destination: `db/dump/project.sql`, in a folder with its own `.gitignore`, because a project's database can hold things that should not be in git (meeting notes, internal names). Whether a dump goes into git is the project's decision: pass `--out <path>` to put it somewhere else.
- Bulky tables that grow on every run (test logs, for example) can be left out: list them in `db/dump-exclude.txt`, one table name per line (`#` starts a comment), or pass `--exclude TABLE`. Their schema stays in the dump, their rows do not; the same-PC `backup` still covers them. The dump stays small enough to read and diff, which compressing it would not. If a kept table has a foreign key to a left-out one, `dump` warns that the restore would fail its foreign key check.
- Rowids are kept, so a restore gives back the same rows with the same rowids.
- `restore FILE` builds the database from a dump. It never overwrites: the target (`db/project.db`, or `--db`) must not exist. Foreign keys are checked after loading, then `check` runs and its result comes back as `check`. Triggers are created after the rows, so restoring does not fire them.

## Timing log

Every `run` and `list <word>` is timed and logged quietly to `db/stats/nq-stats.db` (a separate, git-ignored file; the last 30 days are kept). Logging is on by default. Set `NQ_STATS=0` for calls that should not be counted — for example a dashboard that refreshes every few seconds — or to turn it off entirely. You do not need to look at it during normal work. When something feels slow, or a search keeps finding nothing, check `nq.py stats` (narrow it with `--table`, `--query`, or `--recent N`) instead of guessing.

## Concurrency

- Write connections use WAL mode, so they do not block readers.
- `busy_timeout` is 5 seconds (`NQ_BUSY_TIMEOUT_MS` changes it). Several agents writing at the same time wait for each other instead of failing. If a write still fails with "database is locked", the result says `rolled_back: true` and has a `hint`: the write was not applied, but before running it again, read the state with a read query — another agent may already have written the same thing.
- Foreign keys are enforced (`PRAGMA foreign_keys=ON`).
