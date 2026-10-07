-- How often each test has run and when it last ran
-- param test_id: one test, or null = every test
-- column runs: number of recorded runs (0 = never run)
-- column last_run: time of the latest run, null = never run
SELECT t.test_id, t.status, count(r.run_id) AS runs, max(r.run_at) AS last_run
FROM tests t LEFT JOIN test_runs r ON r.test_id = t.test_id
WHERE :test_id IS NULL OR t.test_id = :test_id
GROUP BY t.test_id
ORDER BY t.test_id;
