# Changelog

`nq.py --version` and `nq.py status` report the version.

## Unreleased

Documentation and examples only; `nq.py` and its output are unchanged.

### Added
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
