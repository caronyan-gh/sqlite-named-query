-- Mark a test passed and log the run
-- mode: write
-- export: test_card, test_summary
UPDATE tests SET status = 'passed', last_result = :result, updated_at = CURRENT_TIMESTAMP WHERE test_id = :test_id;
INSERT INTO test_runs(test_id, result) VALUES (:test_id, :result);
