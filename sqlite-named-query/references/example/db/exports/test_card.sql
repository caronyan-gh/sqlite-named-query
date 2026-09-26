-- One markdown card per test
SELECT 'reports/tests/' || test_id || '.md' AS path,
       '# ' || test_id || char(10) || char(10) || 'status: ' || status || char(10) || 'last: ' || coalesce(last_result,'-') || char(10) AS content
FROM tests WHERE test_id = :test_id;
