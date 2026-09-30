# Lessons from real use

This skill was used to run development with several AI agents — an implementer, a reviewer, a designer — plus a human relaying messages between them, with tasks, spec questions, and test results kept in the database. Below are the mistakes made at first and the practices that worked.

## Design the schema from the project's own material

- Do not impose a fixed set of tables up front. Look at what material the project has (a test plan? a traceability matrix? a Q&A history? just a README?) and how finely it is written, and shape the schema and queries to match. Every project differs in both.
- Keep only the mechanism in the skill (safe execution, projections, migrations, checks) and let the project decide what goes in. When requirements grow later, you just add SQL files.

## Early mistakes

- **Treating "registered" and "delivered" as the same state.** Marking an instruction as "sent" as soon as it was stored meant nobody knew whether the other agent had actually received it. The fix: make the receiving agent's "read my instruction" query a write query that marks it "received" at the moment it is read. Asking the human to record "delivered" only adds work.
- **Having agents transcribe numbers.** Test counts pasted into reports sometimes could not be reproduced. Record them straight into the database from the script that runs the tests; never let an agent type the numbers.
- **Recording pass/fail only per gate.** Items flipping true → false inside a gate that was still failing went unnoticed. Recording per item makes both progress and regressions visible.
- **Having agents list changed files.** The lists disagreed with the actual diff. Save a snapshot of `git status` at recording time instead — and state clearly in the output that it is "the working tree at that moment", not necessarily "that task's changes".
- **Comparing against the immediately previous run.** When implementer and reviewer runs follow each other in the same task, you end up comparing identical code and see nothing. Compare against "the reviewer's last run of the previous task".
- **Making runs partial broke every query that read "the latest".** After adding a run mode that skips heavy tests and records them as `SKIPPED`, the queries that counted "the latest run" (remaining work, trend, items not yet covered, latest evidence) treated the skipped gates as "not passing" and "no evidence". Even the evidence query the implementer was told to check first came back empty. The pass/fail comparisons still matched, so nobody noticed; a drop in the dashboard numbers gave it away. Define "latest" as "the latest result that actually ran for that item" (the highest run where `status <> 'SKIPPED'`). When the writing side gains a new status value or run mode, grep every query and export that reads that table and fix them all — checking the writer alone is not enough.

## Capturing time

- Keep a timestamp column for each state transition (`received_at`, `reported_at`, `committed_at`, ...). A single "last updated" column gets overwritten and you lose the intervals.
- Intervals let you separate human time from agent time:
  - registered → received: human relay time
  - received → reported: implementer time
  - reported → committed: reviewer time
- Fill timestamps as a side effect of the work itself (the receive, report, and close queries). Do not add separate steps just to record time.
- Do not backfill past data by guessing. Leave intervals you could not capture as NULL.
- Difference in minutes: `round((julianday(b) - julianday(a)) * 1440, 1)`.

## Other tips

- Put long bodies (instructions, reports, questions) in a file and pass them with `--param-file`. Escaping JSON in a shell breaks things (Git Bash on Windows in particular can mangle backslashes).
- Read bodies with `--raw COLUMN` to get exactly the bytes that were stored.
- **Edit text without editing text.** Give information that goes stale (handoff notes, conventions, lessons) an `active` flag and a `superseded_by` column. Never delete. Switch an entry off in the same write that registers its replacement — or, when nothing replaces it, through a retire query that records why. A separate "turn it off" step is what agents forget. Then generate the handoff document by exporting only the active entries, and never edit the generated file by hand (rule 1 in SKILL.md). Hand-edited handoff files keep growing and keep stale sections that read like the current state; a generated one is far less likely to. Once in a while, read all active entries in one go (for example with `--raw` on a query that concatenates them) and look for ones that are stale, contradict each other, or say the same thing twice; supersede or retire them. That catches the replacements someone forgot.
- **When moving text into the database, check for losses with a mapping table.** When a hand-written document (such as a handoff file) is moved into the database, anything that falls out goes unnoticed. The new version looks tidy on its own, and a rule that is no longer written down raises no error when it is broken. (In real use, migrating a handoff file dropped "reply in Japanese" and "how to write relay messages" from its "promises to the user" section; both the agent that moved it and the agent that checked it missed this. Another section's procedure was also dropped once and restored later.) When moving, build a table with one row per section (or bullet) of the old document and where it now lives. For anything discarded, write "discarded" and why. An empty row is something you dropped. Whoever checks the move should compare the table with the old document, not read the new version.
- **Keep one source of truth.** When the authoritative version lives elsewhere (for example spec decisions in design documents), store only an index in the database — where it is and what it covers — not a second copy.
- Storing commit IDs in the database lets you answer "which commit belongs to which answer or task" without opening git.
- Once state, counts, commits, and timestamps are all in place, progress reports (progress per group of gates, time per task, the tasks that took longest) take seconds to produce from the database.
- Exported text files tracked in git double as a backup of the database. If the database itself is not in git, do not trim the exports too far.
