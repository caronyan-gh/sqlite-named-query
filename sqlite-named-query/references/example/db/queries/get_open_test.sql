-- List tests that have not passed yet
SELECT test_id, title, status FROM tests WHERE status <> 'passed' ORDER BY test_id;
