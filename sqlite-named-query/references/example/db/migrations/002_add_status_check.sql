-- nq: foreign_keys=off
-- Allow only known statuses. SQLite cannot add a CHECK to an existing column, so the table is rebuilt:
-- create the new table -> copy the rows -> drop the old table -> rename the new one.
-- test_runs references tests; foreign keys are off for this migration only and checked before COMMIT.
-- No BEGIN / COMMIT here: nq.py runs each migration in its own transaction.
CREATE TABLE tests_new (
  test_id     TEXT PRIMARY KEY,
  title       TEXT,
  status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'passed', 'failed')),
  last_result TEXT,
  updated_at  TEXT
);
INSERT INTO tests_new (test_id, title, status, last_result, updated_at)
SELECT test_id, title, status, last_result, updated_at FROM tests;
DROP TABLE tests;
ALTER TABLE tests_new RENAME TO tests;
