-- Register several tests at once (existing test_ids are left as they are)
-- mode: write
-- param items json: [{"test_id": "T-B-001", "title": "..."}, ...]
INSERT OR IGNORE INTO tests (test_id, title)
SELECT json_extract(value, '$.test_id'), json_extract(value, '$.title') FROM json_each(:items)
RETURNING test_id;
