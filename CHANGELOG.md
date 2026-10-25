# Changelog

`nq.py --version` and `nq.py status` report the version.

## Unreleased

Output of existing commands is unchanged apart from new keys (`optional` in parameter info, `warning` on writes that changed nothing, `hint` on lock failures); the behavior changes are that a wrong `--root` and a folder given as `--db` are now errors.

### Added
- `nq.py batch --file F`: several named queries from one JSON file, `[{"query": NAME, "params": {...}}, ...]`, in one transaction. Every item is checked before anything runs; writes are all-or-nothing; exports run once after the commit; results come back in order; each item is logged to the timing log.
- SKILL.md: on Windows, command-line text arrives intact and garbling comes from reading UTF-8 output as cp932 (`PYTHONUTF8=1`, or read as UTF-8); prefer files to heredocs for parameters.
- Optional parameters: `-- param NAME optional: …` lets a caller leave the parameter out; it is then bound as null (use `coalesce(:NAME, col)` for "keep as is"). Null is the only default; undeclared parameters are still required.
- `nq.py dump [--out FILE]` writes the whole database as SQL text: tables by name, rows in primary-key order, then indexes, views and triggers, so the same database gives the same text. Default `db/dump/project.sql` in a folder with its own `.gitignore`. Tables listed in `db/dump-exclude.txt` (or `--exclude`) keep their schema but not their rows; rowids are kept. `nq.py restore FILE` builds the database from it (never overwrites an existing one), checks foreign keys, and runs `check`.
- `NQ_BUSY_TIMEOUT_MS` sets how long a connection waits for another writer's lock (default 5000). A write that fails on a lock returns a `hint`: it was not applied, and the state should be read before running it again.
- A write that changed no rows (and returned none) carries a `warning`.
- `--root` pointing inside `db/` (for example `db/queries`) says to pass the folder two levels up.
- SKILL.md: optional parameters and a separate query for clearing a value; `WHERE true` for `INSERT … SELECT … FROM t ON CONFLICT`; JSON parameters through `--params-file` on Windows; direct read-only exploration is fine but reported numbers come from a named query run then.
- A `--root` that has neither `db/queries` nor `db/migrations` stops before anything is read or created, with a hint (for example "this looks like the db/ folder itself; pass its parent"). It used to be accepted silently, so `migrate`/`check` ran against an empty project.
- SKILL.md: values with non-ASCII text, line breaks, or quotes go through `--param-file` / `--params-file`.
- Examples: `002_add_status_check.sql` (rebuilding a table to add a CHECK constraint with `-- nq: foreign_keys=off`), `add_tests.sql` (a `json` parameter), `test_history.sql` (documented parameter and columns). Tests cover them.
- README states the SQLite requirement: 3.35 or newer (`RETURNING` in the examples).

### Changed
- SKILL.md keeps what is needed every time; task-specific details (exports, rebuilding tables, what `check` verifies, backups, the timing log, concurrency, the legacy database location) moved to `references/reference.md`, with short "read this first" pointers left in SKILL.md.

## 0.1.0 — 2026-10-07

First tagged release. Everything below was already on `main` before the tag.

### Added
- `nq.py list <word>`: search names, descriptions, notes, and parameter/column docs. Words are case-insensitive regexes (`'duration|timing'`, `^task_`); every word must match. No match says so and returns every query's name and description.
- Query headers can document parameters and columns: `-- param NAME [json|text]: meaning`, `-- column NAME: meaning`. `json` params accept arrays/objects and bind them as JSON text; `text` params are always strings (null stays NULL). Documented columns appear as `columns` in results. `check` reports docs that name things the query does not have.
- An unknown query name returns up to three close names as `did_you_mean`.
- Parameter-mismatch errors include the query's description, notes, and params; lookup errors point to `nq.py list`.
- `nq.py backup [--label L] [--keep N]`: online backup to `db/backup/project-<label>-<date-time>.db`, newest N kept by the date-time in the name, self-ignoring folder.
- Timing log: every `run` and `list <word>` is logged to `db/stats/nq-stats.db` (separate, git-ignored, 30 days; `NQ_STATS=0` turns it off). `nq.py stats` shows the slowest queries and fruitless searches, with `--query`, `--table`, `--recent N`.
- `nq.py --version`; `status` includes `version`.
- `tests/test_nq.py` (standard library only).

### Changed
- **Breaking:** `list` returns `params` as objects `{"name", "type"?, "doc"?}` instead of plain names. Read `.name`.
- New databases go to `<root>/db/project.db`; an existing `<root>/project.db` is still used when `db/project.db` is absent.
- Argument errors are JSON (`"invalid arguments: ..."`) with the misused command's usage; exit code stays 2.
- An array/object given to an undeclared parameter is refused before the query runs, naming the parameter.
- Lessons from real use moved from SKILL.md to `references/lessons.md`.

### Fixed
- UTF-8 BOM in `.sql` files no longer hides the header.
- Paths containing `%`, `#`, or `?` work for read-only connections.
- `status` no longer writes to the database.
- Unexpected errors come back as JSON instead of a traceback.
