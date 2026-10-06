"""Tests for nq.py. Standard library only.

    python -m unittest discover tests

Each test copies references/example/db into a temporary project and drives nq.py as a subprocess, the same way an
agent does, so they check the JSON contract rather than internals.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(HERE, "..", "sqlite-named-query")
NQ = os.path.join(SKILL, "scripts", "nq.py")
EXAMPLE = os.path.join(SKILL, "references", "example", "db")


class Project(unittest.TestCase):
    """A fresh copy of the example project per test; timing log off unless a test turns it on."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="nq-test-")
        shutil.copytree(EXAMPLE, os.path.join(self.root, "db"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def nq(self, *args, env=None, raw=False):
        e = dict(os.environ, NQ_STATS="0", PYTHONIOENCODING="utf-8")
        e.update(env or {})
        p = subprocess.run([sys.executable, NQ, *args], cwd=self.root, capture_output=True, env=e)
        out = p.stdout.decode("utf-8")
        return p.returncode, (out if raw else json.loads(out))

    def write(self, rel, text, newline="\n"):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline=newline) as fh:
            fh.write(text)
        return path

    def migrate(self):
        code, out = self.nq("migrate")
        self.assertEqual(code, 0, out)
        return out

    def db(self):
        return sqlite3.connect(os.path.join(self.root, "db", "project.db"))


class Basics(Project):
    def test_migrate_and_status(self):
        code, out = self.nq("status")
        self.assertFalse(out["exists"])
        self.assertEqual(out["pending"], ["001_init.sql"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "db", "project.db")), "status must not create the db")
        self.migrate()
        code, out = self.nq("status")
        self.assertEqual((out["exists"], out["pending"]), (True, []))

    def test_read(self):
        self.migrate()
        code, out = self.nq("run", "get_open_test")
        self.assertEqual(code, 0)
        self.assertEqual([r["test_id"] for r in out["rows"]], ["T-A-001", "T-A-002"])
        self.assertFalse(out["truncated"])

    def test_write_runs_exports(self):
        self.migrate()
        code, out = self.nq("run", "close_test", "--param", "test_id=T-A-001", "--param", "result=passed")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["affected_rows"], 2)
        card = os.path.join(self.root, "reports", "tests", "T-A-001.md")
        with open(card, encoding="utf-8") as fh:
            self.assertIn("status: passed", fh.read())
        code, out = self.nq("run", "close_test", "--param", "test_id=T-A-001", "--param", "result=passed")
        self.assertIn("reports/tests/T-A-001.md", out["exports"][0]["unchanged"])

    def test_write_rolls_back_on_failure(self):
        self.migrate()
        self.write("db/queries/half.sql", "-- Half then fail\n-- mode: write\n"
                   "UPDATE tests SET status = 'x' WHERE test_id = :id;\nINSERT INTO nope VALUES (1);\n")
        code, out = self.nq("run", "half", "--param", "id=T-A-001")
        self.assertEqual(code, 1)
        self.assertTrue(out["rolled_back"])
        self.assertEqual(self.db().execute("SELECT status FROM tests WHERE test_id = 'T-A-001'").fetchone()[0], "open")

    def test_declared_read_with_write_is_refused(self):
        self.migrate()
        self.write("db/queries/sneaky.sql", "-- Sneaky\n-- mode: read\nDELETE FROM tests;\n")
        code, out = self.nq("run", "sneaky")
        self.assertEqual(code, 1)
        self.assertIn("mode: read", out["error"])
        self.assertEqual(self.db().execute("SELECT count(*) FROM tests").fetchone()[0], 2)

    def test_raw_is_byte_exact(self):
        self.migrate()
        body = self.write("body.md", "line one\r\nline two\n", newline="")
        self.nq("run", "close_test", "--param", "test_id=T-A-001", "--param-file", f"result={body}")
        self.write("db/queries/last.sql", "-- Last result\nSELECT last_result FROM tests WHERE test_id = :id;\n")
        code, out = self.nq("run", "last", "--param", "id=T-A-001", "--raw", "last_result", raw=True)
        self.assertEqual(out, "line one\r\nline two\n")

    def test_bom_header_is_read(self):
        self.migrate()
        with open(os.path.join(self.root, "db", "queries", "bom.sql"), "wb") as fh:
            fh.write("﻿-- BOM desc\n-- mode: read\nSELECT 1 AS x;\n".encode("utf-8"))
        code, out = self.nq("list", "bom")
        self.assertEqual(out["queries"][0]["description"], "BOM desc")
        self.assertEqual(out["queries"][0]["mode"], "read")


class Parameters(Project):
    def test_mismatch_explains_query(self):
        self.migrate()
        code, out = self.nq("run", "close_test", "--param", "test_id=T-A-001")
        self.assertEqual(code, 1)
        self.assertEqual(out["missing"], ["result"])
        self.assertEqual(out["description"], "Mark a test passed and log the run")
        self.assertIn({"name": "test_id"}, out["params"])
        self.assertIn("nq.py list", out["hint"])

    def test_unknown_query_suggests_names(self):
        self.migrate()
        code, out = self.nq("run", "get_open_tests")
        self.assertEqual(code, 1)
        self.assertIn("get_open_test", out["did_you_mean"])
        code, out = self.nq("run", "xyzzy")
        self.assertNotIn("did_you_mean", out)

    def test_json_and_text_types(self):
        self.migrate()
        self.write("db/queries/add_tags.sql",
                   "-- Add tests from a JSON array\n-- mode: write\n-- param ids json: array of ids\n"
                   "-- param sha text: commit SHA\n"
                   "INSERT INTO tests(test_id, last_result) SELECT value, :sha FROM json_each(:ids) RETURNING test_id, last_result;\n")
        code, out = self.nq("run", "add_tags", "--params", '{"ids": ["X-1", "X-2"], "sha": 1234567}')
        self.assertEqual(code, 0, out)
        self.assertEqual([r["last_result"] for r in out["rows"]], ["1234567", "1234567"])
        code, out = self.nq("run", "add_tags", "--param", 'ids=["X-3"]', "--param", "sha=0012345")
        self.assertEqual(out["rows"][0]["last_result"], "0012345")
        code, out = self.nq("run", "add_tags", "--param", 'ids=["X-4"]', "--param", "sha=null")
        self.assertIsNone(out["rows"][0]["last_result"])

    def test_undeclared_array_is_refused_before_running(self):
        self.migrate()
        code, out = self.nq("run", "close_test", "--params", '{"test_id": ["T-A-001"], "result": "x"}')
        self.assertEqual(code, 1)
        self.assertIn("parameter test_id is a JSON array", out["error"])
        self.assertNotIn("rolled_back", out)

    def test_column_docs_in_results(self):
        self.migrate()
        self.write("db/queries/titles.sql", "-- Titles\n-- column title: short title\nSELECT test_id, title FROM tests;\n")
        code, out = self.nq("run", "titles")
        self.assertEqual(out["columns"], {"title": "short title"})


class Listing(Project):
    def test_search_and_no_match(self):
        self.migrate()
        code, out = self.nq("list", "^close")
        self.assertEqual([q["name"] for q in out["queries"]], ["close_test"])
        code, out = self.nq("list", "zzzz")
        self.assertEqual(out["match"], 0)
        self.assertEqual({q["name"] for q in out["queries"]}, {"close_test", "get_open_test"})
        self.assertEqual(set(out["queries"][0]), {"name", "description"})

    def test_params_are_objects(self):
        code, out = self.nq("list")
        close = [q for q in out["queries"] if q["name"] == "close_test"][0]
        self.assertEqual(close["params"][1], {"name": "test_id"})
        self.assertIn("doc", close["params"][0])


class Checks(Project):
    def test_check_passes_on_example(self):
        self.migrate()
        code, out = self.nq("check")
        self.assertEqual((code, out["problems"]), (0, []))

    def test_changed_migration_is_reported(self):
        self.migrate()
        self.write("db/migrations/001_init.sql", "CREATE TABLE changed (x);\n")
        code, out = self.nq("check")
        self.assertEqual(code, 1)
        self.assertTrue(any("file changed after it was applied" in p for p in out["problems"]))

    def test_stale_and_malformed_docs_are_reported(self):
        self.migrate()
        self.write("db/queries/bad.sql", "-- Bad\n-- param nope: no such param\n-- column zzz: no such column\n"
                   "-- param x int: wrong type\nSELECT test_id FROM tests WHERE :x IS NULL;\n")
        code, out = self.nq("check")
        text = "\n".join(out["problems"])
        self.assertIn("'-- param nope'", text)
        self.assertIn("'-- column zzz'", text)
        self.assertIn("unknown parameter type 'int'", text)

    def test_export_cannot_escape_root(self):
        self.migrate()
        self.write("db/exports/evil.sql", "-- Evil\nSELECT '../outside.md' AS path, 'x' AS content;\n")
        code, out = self.nq("export", "evil")
        self.assertEqual(code, 1)
        self.assertIn("escapes project root", out["error"])

    def test_table_rebuild_with_foreign_keys_off(self):
        self.migrate()
        self.nq("run", "close_test", "--param", "test_id=T-A-001", "--param", "result=passed")
        rebuild = ("-- nq: foreign_keys=off\n"
                   "CREATE TABLE tests_new (test_id TEXT PRIMARY KEY, title TEXT, status TEXT NOT NULL DEFAULT 'open' "
                   "CHECK (status IN ('open', 'passed')), last_result TEXT, updated_at TEXT);\n"
                   "INSERT INTO tests_new SELECT * FROM tests;\nDROP TABLE tests;\n"
                   "ALTER TABLE tests_new RENAME TO tests;\n")
        self.write("db/migrations/002_rebuild.sql", rebuild)
        code, out = self.nq("migrate")
        self.assertEqual((code, out["applied"]), (0, ["002_rebuild.sql"]), out)
        self.write("db/migrations/003_broken.sql", "-- nq: foreign_keys=off\nINSERT INTO test_runs(test_id, result) VALUES ('NOPE', 'x');\n")
        code, out = self.nq("migrate")
        self.assertEqual(code, 1)
        self.assertIn("foreign_key_check failed", out["error"])
        code, out = self.nq("status")
        self.assertEqual(out["pending"], ["003_broken.sql"])


class Storage(Project):
    def test_new_projects_use_db_folder_and_legacy_still_works(self):
        out = self.migrate()
        self.assertTrue(out["db"].endswith(os.path.join("db", "project.db")))
        legacy = Project()
        legacy.setUp()
        try:
            legacy.nq("--db", os.path.join(legacy.root, "project.db"), "migrate")
            code, out = legacy.nq("status")
            self.assertEqual(os.path.basename(out["db"]), "project.db")
            self.assertEqual(os.path.dirname(out["db"]), legacy.root)
        finally:
            legacy.tearDown()

    def test_backup_naming_keep_and_gitignore(self):
        self.migrate()
        for label in ("a", "b", "c"):
            code, out = self.nq("backup", "--label", label, "--keep", "2")
            self.assertEqual(code, 0, out)
        bdir = os.path.join(self.root, "db", "backup")
        dbs = sorted(f for f in os.listdir(bdir) if f.endswith(".db"))
        self.assertEqual(len(dbs), 2)
        self.assertTrue(all(f.startswith(("project-b-", "project-c-")) for f in dbs), dbs)
        with open(os.path.join(bdir, ".gitignore"), encoding="utf-8") as fh:
            self.assertIn("*", fh.read())

    def test_timing_log_and_opt_out(self):
        self.migrate()
        self.nq("run", "get_open_test")
        self.assertFalse(os.path.exists(os.path.join(self.root, "db", "stats")), "NQ_STATS=0 must not log")
        on = {"NQ_STATS": "1"}
        self.nq("run", "get_open_test", env=on)
        self.nq("list", "zzzz", env=on)
        code, out = self.nq("stats")
        self.assertEqual(out["queries"][0]["query"], "get_open_test")
        self.assertEqual(out["no_match_searches"][0]["keyword"], "zzzz")
        code, out = self.nq("stats", "--recent", "5", "--table", "tests")
        self.assertEqual([r["query"] for r in out["runs"]], ["get_open_test"])


class Arguments(Project):
    def test_argument_errors_are_json_with_usage(self):
        code, out = self.nq("stats", "--bogus")
        self.assertEqual(code, 2)
        self.assertIn("invalid arguments", out["error"])
        self.assertIn("--recent", out["usage"])
        code, out = self.nq("nope")
        self.assertIn("invalid choice", out["error"])


if __name__ == "__main__":
    unittest.main()
