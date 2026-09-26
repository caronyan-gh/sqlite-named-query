CREATE TABLE tests (test_id TEXT PRIMARY KEY, title TEXT, status TEXT NOT NULL DEFAULT 'open', last_result TEXT, updated_at TEXT);
CREATE TABLE test_runs (run_id INTEGER PRIMARY KEY, test_id TEXT NOT NULL REFERENCES tests(test_id), result TEXT NOT NULL, run_at TEXT DEFAULT CURRENT_TIMESTAMP);
INSERT INTO tests(test_id, title) VALUES ('T-A-001','alpha'), ('T-A-002','it''s -- tricky; :notparam');
