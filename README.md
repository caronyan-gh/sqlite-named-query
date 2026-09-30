# sqlite-named-query

A skill (`SKILL.md` format) for AI coding agents: the agent keeps your project's state (tasks, Q&A, test progress, whatever it needs) in SQLite and reads and writes it **only through named queries** saved as files in your project. You don't need to know SQL — or even keep the whole project in your head. Just ask.

Most agent-memory tools give you a fixed API (`remember` / `recall`). This one gives you no schema at all — the agent designs its own tables and queries for whatever your project needs, then sticks to them.

## How to use

1. **Install the skill** (see [Install](#install)).
2. **Let your agent build whatever it needs.** No schema design needed on your side — just say:

   > Use sqlite-named-query for this project. Make whatever you want.

   It looks at your project and decides what to keep track of. It has to read your project anyway — this just keeps what it learned instead of throwing it away. Setup takes minutes, not a new platform. The agent creates `db/migrations/`, `db/queries/`, and `db/exports/` in your project, then runs `migrate` and `check`.
3. **Work as usual.** From now on, ask in plain words and the agent reads and updates state through the named queries:

   > What's still left to do?
   >
   > That one's done.

   Curious what it built? Just ask:

   > What did you make?

   The agent lists its queries and what each one does. Humans can also read the exported Markdown files; the database stays the source of truth.
4. **Hand it over.** Finally, tell the agent:

   > Use it whenever and however you like.

   From then on it checks and updates the state on its own as it works. It also leaves a short note in your project's agent instruction file (such as `AGENTS.md` or `CLAUDE.md`), so later sessions keep using it.
5. **Check in once in a while.** Every so often, ask:

   > Anything you'd like to add?

   The agent looks back at what it keeps looking up or re-deriving by hand, and suggests new tables or queries for it.

**Bonus:** you can also ask for something specific, any time:

> Make a to-do list in the db.

The agent adds the tables and queries for it, and from then on keeps the list up to date by itself (probably 😉). New needs always become new query files — it never writes SQL on the fly.

## Features

- **External memory for your agent — store generously, read sparingly.** State lives in the database, not in the context window. It survives new sessions and context compaction, and the agent pulls back just the rows and columns it needs as JSON — so long projects don't drown the context. When something doesn't add up, it can pull the whole history instead of digging through old conversations.
- **Built for teamwork.** Several agents, sessions, and humans share one source of truth, and simultaneous writes wait their turn instead of breaking. Battle-tested on a multi-agent development project: when the implementer ran out of quota mid-project, a different AI took over from the database and carried on as if nothing happened (see [lessons from real use](sqlite-named-query/references/lessons.md)).
- **Plays well with git and other tools.** Every write can refresh plain Markdown files: humans can read them, git tracks their history, and tools such as graphify can index them. Store commit IDs in the database to link tasks and commits.
- **Queries are files.** Every read and write the agent does is a saved `.sql` file you can look at, reuse, or put in git — nothing is run on the fly.
- **Safe writes.** Parameters are always bound. Read queries open the database read-only. Write queries run in a single transaction and roll back on failure.
- **Schema migrations** with checksums, and a `check` command that lints and compiles every query without running it.
- **Zero dependencies.** A single Python script using only the standard library.

Long-form documents such as designs and specs are out of scope — keep those in Markdown. This is for the state that changes as you work.

## Install

Copy the `sqlite-named-query/` directory into the skills directory your agent reads (for example `~/.claude/skills/` or `~/.agents/skills/`; check your agent's docs):

```bash
git clone https://github.com/caronyan-gh/sqlite-named-query.git
cp -r sqlite-named-query/sqlite-named-query <your-skills-dir>/
```

Requires Python 3.8+.

## Try it by hand

You never have to run these yourself, but if you want to see what the agent does:

```bash
cp -r <your-skills-dir>/sqlite-named-query/references/example/db ./db
NQ=<your-skills-dir>/sqlite-named-query/scripts/nq.py

python $NQ migrate
python $NQ list
python $NQ run get_open_test
python $NQ run close_test --params '{"test_id":"T-A-001","result":"passed"}'
cat reports/tests/SUMMARY.md
```

See [`sqlite-named-query/SKILL.md`](sqlite-named-query/SKILL.md) for the full reference: project layout, query file headers, projections, migrations, `check`, and operating rules. Lessons from real use are in [`references/lessons.md`](sqlite-named-query/references/lessons.md).

## Repository layout

```
sqlite-named-query/
├─ SKILL.md                  skill definition and reference
├─ scripts/nq.py             the runner
└─ references/
   ├─ lessons.md             lessons from real use (read when designing a database or when stuck)
   └─ example/db/            example migrations, queries and exports
```

## What it ended up doing in my project (nobody planned these)

- **"How's it going?" in one question**: pass counts over time, remaining work per area, and what moved since yesterday, straight from the database instead of re-reading files.
- **One-command status board**: open tasks, unanswered questions, the latest test run, and what changed since the last one.
- **Delivery receipts**: an instruction flips to "received" the moment the other agent reads it, so the human relay never has to confirm it.
- **Item-level test tracking**: catches regressions hidden inside gates that were already failing.
- **Regression blame**: for any item that broke, which run, task, agent, and commit broke it.
- **Reproducibility check**: flags tests where the implementer's reported result and the reviewer's rerun disagree.
- **Time per stage**: human relay time vs implementer time vs reviewer time for every task.
- **Burndown and remaining work** per group of test gates.
- **Spec impact analysis**: "this spec section changed — which tests cite it, and are they passing?"
- **Traceability holes**: spec sections no test covers, and tests that cite no spec.
- **Q&A audit trail**: question → answer → the exact commit that changed the spec.
- **Reviewer follow-ups** linked to the task or answer they came from.
- **Guard rails the agents added themselves**: finished tasks can't be picked up again; broken test runs are excluded from comparisons.
- **Comparing AI implementers**: when we swapped implementer models, their speed, progress per task, and regressions came straight out of the database.

## Field report

In the agent's own words, after running this on a real project:

- **Status checks**: from thousands of tokens (reading reports, test JSON, and git log, then piecing them together) down to a few hundred — one query, a few hundred bytes.
- **Reviews**: roughly half the reading, because the query already points at exactly which items moved.
- **Overall**: maybe 20–30% fewer tokens. Designing, reading diffs, and writing code don't shrink.
- **The bigger win**: nothing gets lost when the conversation is summarized. The state is still in the database.

<sub>*Individual results may vary. Testimonial from an AI agent, not a benchmark.</sub>

## Background

The story of how this came about — it started with "reading all this Markdown every time is too heavy" — is on note (in Japanese): [AIにMarkdownを毎回読ませるのが重いのでSQLiteにしたら、AI開発チームの「記憶」ができた話](https://note.com/caronyan/n/na486cf0db761)

## License

[MIT](LICENSE) © caronyan
