---
name: sqlite-named-query
description: A thin execution layer that keeps project state (tasks, Q&A, test progress, and so on) in SQLite and reads/writes it only through named queries defined by the project. Use it when you want just the rows and columns you need as JSON instead of searching and reading piles of Markdown, when you need to update that state, or when an update should also write out text files for humans or file-only agents. Also use it when the user casually asks to keep something in "the db" ("make a to-do list in the db", "make whatever you need"). Long-form documents such as designs and specs are out of scope (keep them in Markdown).
---

# sqlite-named-query

Keep a project's **state** in SQLite. The agent never runs ad-hoc SQL; every read and write goes through a **named query** saved as a file in the project. When the agent needs something new, it adds the query file itself.

- Long-form documents (designs, specs) → stay in Markdown
- Project state (tasks, Q&A, test progress, ...) → SQLite is the source of truth
- Readers that can only see text (humans, file-based agents) → text is written out on every write (a *projection*). **Exported text is not the source of truth.**
- Anything whose authoritative version lives elsewhere (for example spec decisions in design documents) → the database holds only an index to it, never a second copy

**Store generously, read sparingly.** Never delete history — switch things off instead, and keep a timestamp for every event. Day to day, pull only what the task needs (active rows, one category, one ID). When something doesn't add up, stop saving: pull everything, history and superseded entries included, in time order.

The skill itself knows nothing about tasks, Q&A, or any other project-specific concept. The schema and the queries live in the project.

## Working with the user

The user is not expected to know SQL, design a schema, or review queries. They just ask in plain words; you build and run everything.

| The user says something like | What you do |
|---|---|
| "Use sqlite-named-query for this project. Make whatever you want." | Look at the project, decide what is worth tracking, create the migrations, queries and exports, then run `migrate` and `check`. |
| "Make a to-do list in the db." | Add a migration and the queries needed for it (add, list, update, ...), then `migrate` and `check`. |
| "What's still left to do?" / "That one's done." | Run the matching named query. If none fits, add one first. |
| "What did you make?" | Run `list` and explain the tables and queries in plain words. |
| "Use it whenever and however you like." | From now on, read and update the state on your own as you work. Also leave a short note in the project's agent instruction file (whichever it uses, e.g. `AGENTS.md` or `CLAUDE.md`; create `AGENTS.md` if there is none) so later sessions keep using it — see "Keep the instruction file short" below for what goes in it. |
| "Anything you'd like to add?" | Look back at what you keep looking up, counting, or piecing together by hand, and at questions you could not answer from the database. Suggest new tables or queries for them in plain words, and add the ones the user agrees to. |

### What to record

Don't record everything that *could* be recorded. Record something when both are true:

1. Recording it will avoid repeated reading, searching, or reconstruction later.
2. It cannot be reliably and cheaply reconstructed from an authoritative source.

Good candidates are events and historical facts: results at a point in time, who did what and when, decisions and their reasons, handoffs, observations, and snapshots whose original context may later disappear.

Do not copy descriptions of things that already have an authoritative source. What a class does belongs in the source code. What a specification says belongs in the specification. What the current diff is belongs in Git. These can be read or recomputed when needed, and duplicated descriptions go stale when the source changes.

**Record what happened. Don't record a description of something that already exists elsewhere.**

If something is derived from another source but expensive to recompute (for example a dependency graph of a large codebase), recording it may still be worthwhile — but store what it was derived from (a commit ID or a timestamp), so you can tell when it has gone stale.

### Keeping the setup healthy

**Keep the instruction file short.** In the file every session loads (`AGENTS.md`, `CLAUDE.md`, ...), write only (1) the entry point — "Project state is in this database. Don't memorize — run `list` first." plus how to run `nq.py`, (2) which query to check before which kind of work (e.g. "before changing the API, run `decision_list`"), and (3) rules that must never be missed (safety, secrets, never pushing without permission). Put everything else you would have added — procedures, sync rules, operational decisions and why — into the database (not spec decisions whose source of truth is a document; see "Keep one source of truth" in `references/lessons.md`). The instruction file then grows far more slowly than the project. This applies to what *you* add: don't move content a human wrote without asking, and when you do move it, build a mapping table so nothing is dropped (see "When moving text into the database" in `references/lessons.md`). If readers without this skill need something, export it to Markdown.

Run `check` yourself after every change to queries, exports or migrations.

## Project layout

```
<root>/
└─ db/
   ├─ project.db               the database (default path; override with --db)
   ├─ migrations/NNN_*.sql     schema changes, applied once each in numeric order and recorded in _nq_migrations
   ├─ queries/<name>.sql       named queries (one file = one query)
   └─ exports/<name>.sql       text projections (read queries returning path and content columns)
```

`<root>` is found automatically as the nearest ancestor directory that contains `db/queries` or `db/migrations` (or pass `--root`).

Older projects that keep the database at `<root>/project.db` keep working: if `db/project.db` does not exist and `<root>/project.db` does, that one is used. To move it, stop anything using the database and move `project.db` together with its `-wal` and `-shm` files into `db/`.

A working example is in `references/example/`.

## Usage

The script is `scripts/nq.py` (relative to this skill's directory). It uses the Python standard library only, and always prints a single JSON object.

```bash
NQ=<path-to-this-skill>/scripts/nq.py

python $NQ migrate                  # create / upgrade the database
python $NQ status                   # applied and pending migrations
python $NQ list                     # queries with mode, parameters (name, type, meaning), exports, description
python $NQ list duration            # search: words are case-insensitive regexes ('duration|timing', '^task_')
python $NQ run get_open_test
python $NQ run close_test --params '{"test_id":"T-A-001","result":"passed"}'
python $NQ export test_summary      # run a projection by hand
python $NQ check                    # lint db/ (executes and writes nothing)
python $NQ backup --label task-090  # copy the database to db/backup/project-task-090-<date-time>.db
python $NQ stats                    # timing log: slowest queries and searches that found nothing
```

Every `run` and `list <word>` is timed and logged quietly to `db/stats/nq-stats.db` (a separate, git-ignored file; the last 30 days are kept). You do not need to look at it during normal work. When something feels slow, or a search keeps finding nothing, check `nq.py stats` (narrow it with `--table`, `--query`, or `--recent N`) instead of guessing.

`backup` copies the database with SQLite's online backup API, so it is safe while the database is in use (WAL included). Backups go to `db/backup/project-<label>-<YYYYMMDD-HHMMSS>.db` (just `project-<YYYYMMDD-HHMMSS>.db` without `--label`; the date-time means the same label never overwrites an earlier backup), only the newest `--keep` (default 7) are kept — "newest" is read from the date-time in the file name, not the file's modified time, which copying or unzipping can reset — and `db/backup/` gets its own `.gitignore` so backups never end up in git. The command only backs up when called; when to call it (every tenth task, before a risky migration, ...) is the project's decision, typically made in one of its own scripts. A failed backup returns `ok: false` and changes nothing, so the caller can warn and carry on.

Parameters can be passed three ways, and the ways can be combined (passing the same key twice is an error).

| Form | Use |
|---|---|
| `--params '{"k":1}'` / `--params-file p.json` | Pass everything at once as a JSON object |
| `--param k=31` (repeatable) | One at a time. The value is parsed as JSON if it is valid JSON (`31`, `null`, `true`), otherwise taken as a string |
| `--param-file k=path.md` (repeatable) | The file's contents as a single string (for long bodies; no escaping needed) |

A JSON array or object can only be passed to a parameter the query declares as `json` (see below); anywhere else it is refused before the query runs, with a message saying how to pass it.

`--raw COLUMN` prints that column's value as-is instead of JSON when the result has exactly one row (for reading long bodies; byte-for-byte what was stored). It is an error if there is not exactly one row.

```bash
python $NQ run task_record_report --param task_id=31 --param-file report_md=rep.md   # body from a file
python $NQ run task_get --param task_id=31 --raw instruction_md                       # read the body as-is
```

Read results are capped at 200 rows by default (`--max-rows`). Beyond that, `truncated: true` is set.

Caution: even when `--db` points at another database, exports are still written into the project directory. Trying write queries against a scratch database will overwrite the real exported files.

## Writing a query file

```sql
-- Mark a test passed and log the run          ← first comment line is the description
-- mode: write                                 ← read / write (inferred from the SQL if omitted)
-- export: test_card, test_summary             ← projections to refresh after a successful write (optional)
-- param result: free text such as passed / failed / blocked   ← what a parameter means (optional)
UPDATE tests SET status = 'passed', last_result = :result WHERE test_id = :test_id;
INSERT INTO test_runs(test_id, result) VALUES (:test_id, :result);
```

Document parameters and result columns in the header, so callers never need to open the `.sql` file:

```sql
-- param items json: one run's items, [{step, rank, item, reason}]   ← json: arrays/objects are passed as JSON text
-- param sha text: commit SHA                                         ← text: always a string (1234567 stays "1234567")
-- param owner: role that ran it                                      ← no type: as given
-- column via: where the link was found (traceability | tests | graph)
```

- Before writing any SQL of your own, search for an existing query with `list <word>`: it matches names, descriptions, and parameter and column docs. "No match" also returns every query's name and description, so an empty search never means "no such query" by itself.
- `list` shows each parameter as `{"name", "type", "doc"}` (missing keys are omitted), and a parameter-mismatch error shows the same. Columns documented with `-- column` appear as `columns` in `list` and in every result of that query. Add a doc only where a name is not self-explanatory: null meaning "all", units, allowed values, JSON shape.
- `check` reports a `-- param` or `-- column` line that names something the query does not have, so docs cannot silently go stale.
- Use SQLite named parameters (`:name`). Values are always bound; never build SQL by string concatenation.
- If the parameters you pass do not match the ones in the query (missing or extra), the query is not run and an error is returned (catches typos).
- **read**: exactly one statement. The database is opened read-only, so writes are physically impossible. Declaring `mode: read` on a query that contains a write statement is an error.
- **write**: may contain several statements. They run in a single transaction (`BEGIN IMMEDIATE`); if any fails, everything is rolled back. Rows from `RETURNING` go into `rows`.

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

## Concurrency

- Write connections use WAL mode, so they do not block readers.
- `busy_timeout` is 5 seconds. Several agents writing at the same time wait for each other instead of failing.
- Foreign keys are enforced (`PRAGMA foreign_keys=ON`).

## Operating rules

1. SQLite is the source of truth for project state. Do not edit exported text as if it were state.
2. Do not run SQL on the fly. Use only the named queries in `db/queries`; when you need a new read or write, add a query file and use that.
3. Queries and schema are added or changed as files. Schema changes go into `db/migrations` as new numbered files; never edit an applied migration (`check` detects it).
4. Run `check` after adding or changing queries, exports or migrations. The user does not need to review them; `list` shows everything if they want to look.
5. Keep returned columns and rows to the minimum needed, in the query itself.
6. `project.db` is binary and does not diff. If you use git, track the exported text instead.
7. When you report numbers, quote values from a query you just ran. Do not mix in values you remember from earlier in the conversation — say where each number came from.

## Lessons from real use

Lessons from running this skill with several agents on a real project are in `references/lessons.md` (next to this file): how to shape the schema, early mistakes, capturing time, and tips such as generating handoff notes and moving text into the database. Read it when you start designing a project's database, and when something in daily use goes wrong or feels awkward.
