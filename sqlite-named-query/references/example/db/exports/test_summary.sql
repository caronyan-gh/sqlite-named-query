-- Whole-table summary
SELECT 'reports/tests/SUMMARY.md' AS path, group_concat(test_id || ' ' || status, char(10)) || char(10) AS content FROM (SELECT * FROM tests ORDER BY test_id);
