#!/usr/bin/env python3
"""nq.py — thin SQLite named-query runner for project state (tasks, Q&A, test progress, ...).

Queries live in the project, not in this script:
    <root>/db/queries/<name>.sql      named queries (one file = one query; write queries may hold several statements)
    <root>/db/migrations/NNN_*.sql    schema migrations, applied in order and recorded in _nq_migrations
    <root>/db/project.db              the database (default path; an existing <root>/project.db is still used)

Commands
    list [WORD ...]              query names with description, mode (read/write), parameters (name, type, doc)
                                 and documented result columns. With words: only queries whose name, description,
                                 notes, parameter or column docs match every word (each a case-insensitive regex:
                                 duration|timing, ^task_, history$; invalid patterns are plain text); with no match
                                 it says "no match" and returns every query's name and description instead
    run NAME [--params JSON | --params-file F] [--param K=V ...] [--param-file K=PATH ...] [--raw COLUMN]
                                 run a named query; parameters are bound (never string-concatenated).
                                 --param-file feeds a text file (e.g. a markdown body) as one parameter;
                                 --raw prints one column of the single result row as plain text
    migrate                      create/upgrade the database by applying pending migrations
    status                       database path, applied migrations, pending migrations
    export NAME [--params JSON]  run db/exports/NAME.sql and write its (path, content) rows as text files
    check                        verify db/: applied migrations unchanged (checksums), headers/exports consistent,
                                 every statement compiles against the database (nothing is executed)
    backup [--label L] [--keep N]  copy the database to <root>/db/backup/project-<L>-<date-time>.db with SQLite's online
                                 backup API (safe while in use / WAL); keeps the newest N backups (default 7).
                                 When to back up is the project's decision: call this from its own scripts
    stats [--days N] [--top N] [--query RE] [--table T] [--recent N]
                                 slowest queries (runs, avg/max ms, errors) and searches that found nothing, from the
                                 timing log; --query / --table narrow it to matching query names / queries using a
                                 table, --recent N lists the latest runs one by one. Every `run` and `list <word>` is quietly logged to
                                 <root>/db/stats/nq-stats.db (separate file, git-ignored, last NQ_STATS_DAYS=30 days
                                 kept; NQ_STATS=0 turns logging off). Logging never changes a command's output

Text projection: a write query may declare "-- export: a, b". After COMMIT the runner runs db/exports/a.sql and
b.sql (single read statements returning columns path and content) with the subset of parameters they reference,
and writes each row to <root>/<path> (inside the root only, atomic, skipped when unchanged). The database stays the
source of truth; exported text is a projection for humans / file-based agents and must not be edited as state.

Header comments at the top of a query file (optional):
    -- Get one task by task_id          first plain comment line = description
    -- mode: read | write               default: read if every statement is SELECT/WITH/VALUES/EXPLAIN, else write
    -- export: name[, name...]          (write queries) refresh these text projections after a successful commit
    -- param NAME [json|text]: meaning  document a parameter (shown by list and in parameter errors). json: a JSON
                                        array/object is passed as JSON text; text: always passed as a string (a
                                        digit-only value such as a commit SHA would otherwise become a number).
                                        An undeclared parameter given an array/object is refused before running.
    -- column NAME: meaning             document a result column; read results then carry a "columns" object

Output is always one JSON object on stdout. Standard library only.
"""
__version__ = "0.1.0"  # see CHANGELOG.md in the repository

import argparse
import difflib
import io
import json
import os
import pathlib
import re
import sqlite3
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

READ_START = re.compile(r"^\s*(SELECT|WITH|VALUES|EXPLAIN)\b", re.I)
WRITE_WORDS = re.compile(r"\b(INSERT|UPDATE|DELETE|REPLACE(?!\s*\()|CREATE|DROP|ALTER|ATTACH|DETACH|VACUUM|REINDEX)\b", re.I)
PARAM = re.compile(r"(?<![:\w]):([A-Za-z_][A-Za-z0-9_]*)")


IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Added to errors caused by not knowing what queries exist, so the tool itself points the way (instructions get lost).
LIST_HINT = ("run `nq.py list` to see every query with its parameters and description, or `nq.py list <word>` to "
             "search them (no need to open the .sql files)")
PARAM_DECL = re.compile(r"param\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s+([A-Za-z]+))?\s*(?::\s*(.*))?$", re.I)
COLUMN_DECL = re.compile(r"column\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*(.*))?$", re.I)
PARAM_TYPES = ("json", "text")


def collect_params(args, raw=None):
    """Merge parameters from --params / --params-file (JSON object) and repeatable --param KEY=VALUE /
    --param-file KEY=PATH. A --param value is parsed as JSON when it is valid JSON (31, null, true, "x"), otherwise
    taken as a plain string. A --param-file value is the file's text, byte-for-byte (UTF-8, newlines kept).
    The same key given twice is an error. If `raw` is a dict, the unparsed --param strings are stored there
    (for parameters declared as text)."""
    params = {}
    if getattr(args, "params", None):
        params = json.loads(args.params)
    if getattr(args, "params_file", None):
        if params:
            raise ValueError("use either --params or --params-file, not both")
        with open(args.params_file, encoding="utf-8-sig") as fh:
            params = json.load(fh)
    if not isinstance(params, dict):
        raise ValueError("params must be a JSON object")
    pairs = [(kv, False) for kv in (getattr(args, "param", None) or [])] + \
        [(kv, True) for kv in (getattr(args, "param_file", None) or [])]
    for kv, is_file in pairs:
        key, sep, value = kv.partition("=")
        if not sep or not IDENT.fullmatch(key):
            raise ValueError(f"expected KEY=VALUE, got: {kv}")
        if key in params:
            raise ValueError(f"parameter given twice: {key}")
        if is_file:
            with open(value, encoding="utf-8", newline="") as fh:
                params[key] = fh.read()
        else:
            if raw is not None:
                raw[key] = value
            try:
                params[key] = json.loads(value)
            except ValueError:
                params[key] = value
    return params


def bind_values(q, params, raw=None):
    """Apply the query's declared parameter types. json: a list/dict becomes JSON text. text: the value is passed
    as a string (the raw --param text when there is one). An undeclared parameter holding a list/dict is refused
    here, before anything runs, because SQLite can only bind text, numbers, blobs and null."""
    out = dict(params)
    for name, value in params.items():
        kind = q["param_decls"].get(name, {}).get("type")
        if kind == "json":
            if isinstance(value, (list, dict)):
                out[name] = json.dumps(value, ensure_ascii=False)
        elif kind == "text":
            if value is None:
                pass  # null stays NULL (callers pass null for "no filter")
            elif raw and name in raw:
                out[name] = raw[name]
            elif not isinstance(value, str):
                out[name] = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, (list, dict)):
            shape = "array" if isinstance(value, list) else "object"
            raise ValueError(
                f"parameter {name} is a JSON {shape}; SQLite can only bind text, numbers and null. If the query "
                f"expects JSON text, declare '-- param {name} json' in its header, or pass the JSON with "
                f"--param-file {name}=FILE")
    return out


def param_info(q):
    """The query's parameters in order, each as {name, type?, doc?} (absent keys are omitted)."""
    info = []
    for p in q["params"]:
        d = q["param_decls"].get(p, {})
        info.append({"name": p, **({"type": d["type"]} if d.get("type") else {}), **({"doc": d["doc"]} if d.get("doc") else {})})
    return info


def emit_raw(query, rows, column):
    """--raw COLUMN: print one column of exactly one row as plain text (no JSON), for reading long text fields."""
    if len(rows) != 1:
        fail(query, f"--raw needs exactly one row, got {len(rows)}")
    if column not in rows[0]:
        fail(query, f"--raw column not in result: {column}", columns=list(rows[0].keys()))
    value = rows[0][column]
    # Write bytes so the text comes out exactly as stored (no CRLF translation on Windows).
    sys.stdout.flush()
    sys.stdout.buffer.write(("" if value is None else str(value)).encode("utf-8"))
    sys.stdout.buffer.flush()
    LAST_RESULT.clear()
    LAST_RESULT.update({"ok": True, "query": query, "row_count": 1})
    sys.exit(0)


LAST_RESULT = {}  # what was printed last; read by record_stats after the command finishes


def emit(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, default=str))
    LAST_RESULT.clear()
    LAST_RESULT.update(obj)
    sys.exit(code)


def stats_path(root):
    return os.path.join(root, "db", "stats", "nq-stats.db")


def stats_days():
    try:
        return max(1, int(os.environ.get("NQ_STATS_DAYS", "30")))
    except ValueError:
        return 30


def record_stats(root, args, seconds):
    """Quietly log how long each `run` took (and what `list <word>` searched for) to <root>/db/stats/nq-stats.db,
    a separate file so read queries keep the project database read-only. Rows older than NQ_STATS_DAYS (default 30)
    are pruned every 100 records. Never affects the command's output or exit code; NQ_STATS=0 turns it off."""
    if os.environ.get("NQ_STATS", "1").lower() in ("0", "off", "false", "no"):
        return
    words = getattr(args, "keyword", None)
    if args.cmd != "run" and not (args.cmd == "list" and words):
        return
    try:
        path = stats_path(root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ignore = os.path.join(os.path.dirname(path), ".gitignore")
        if not os.path.exists(ignore):
            with open(ignore, "w", encoding="utf-8") as fh:
                fh.write("# created by nq.py: query timing log, local only\n*\n")
        con = sqlite3.connect(path, timeout=1.0, isolation_level=None)
        try:
            con.execute("CREATE TABLE IF NOT EXISTS runs (at TEXT NOT NULL, query TEXT, mode TEXT, ms REAL, "
                        "rows INTEGER, ok INTEGER, error TEXT)")
            con.execute("CREATE TABLE IF NOT EXISTS searches (at TEXT NOT NULL, keyword TEXT, hits INTEGER, ms REAL)")
            con.execute("CREATE INDEX IF NOT EXISTS runs_at ON runs(at)")
            con.execute("CREATE INDEX IF NOT EXISTS searches_at ON searches(at)")
            now = "datetime('now', 'localtime')"
            ms = round(seconds * 1000, 1)
            r = LAST_RESULT
            if args.cmd == "run":
                cur = con.execute(f"INSERT INTO runs VALUES ({now}, ?, ?, ?, ?, ?, ?)",
                                  (args.name, r.get("mode"), ms, r.get("row_count"), 1 if r.get("ok") else 0,
                                   (str(r["error"])[:200] if r.get("error") else None)))
                table = "runs"
            else:
                hits = 0 if r.get("match") == 0 else len(r.get("queries") or [])
                cur = con.execute(f"INSERT INTO searches VALUES ({now}, ?, ?, ?)", (" ".join(words), hits, ms))
                table = "searches"
            if cur.lastrowid and cur.lastrowid % 100 == 0:
                con.execute(f"DELETE FROM {table} WHERE at < datetime('now', 'localtime', ?)", (f"-{stats_days()} days",))
        finally:
            con.close()
    except Exception:
        pass  # timing is a nice-to-have; never let it break or slow a query result


def query_uses_table(root, name, table):
    """True when db/queries/<name>.sql mentions `table` as a word in its SQL (comments and strings ignored)."""
    path = os.path.join(root, "db", "queries", name + ".sql")
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name or "") or not os.path.isfile(path):
        return False
    code = " ".join(parse_query(path)["code"])
    return re.search(rf"(?<![\w.]){re.escape(table)}(?!\w)", code, re.I) is not None


def cmd_stats(args, root):
    """Slowest queries and fruitless searches from the timing log (see record_stats), or the latest runs one by one
    (--recent N). --query (regex on the query name) and --table (queries whose SQL uses that table) narrow both."""
    filters = {k: v for k, v in (("query", args.query), ("table", args.table)) if v}
    path = stats_path(root)
    if not os.path.exists(path):
        emit({"ok": True, "days": args.days, **({"filters": filters} if filters else {}), "queries": [],
              "no_match_searches": [], "note": "no timing log yet"})
    con = sqlite3.connect(ro_uri(path), uri=True, timeout=5.0)
    since = f"-{args.days} days"
    names = [n for (n,) in con.execute("SELECT DISTINCT query FROM runs WHERE at >= datetime('now', 'localtime', ?)", (since,))]
    if args.query:
        try:
            pat = re.compile(args.query, re.I)
        except re.error:
            pat = re.compile(re.escape(args.query), re.I)
        names = [n for n in names if n and pat.search(n)]
    if args.table:
        names = [n for n in names if query_uses_table(root, n, args.table)]
    marks = ",".join("?" * len(names)) or "NULL"
    out = {"ok": True, "days": args.days, **({"filters": filters} if filters else {})}
    if args.recent:
        out["runs"] = [dict(zip(("at", "query", "mode", "ms", "rows", "ok", "error"), row)) for row in con.execute(
            f"SELECT at, query, mode, ms, rows, ok, error FROM runs WHERE at >= datetime('now', 'localtime', ?) "
            f"AND query IN ({marks}) ORDER BY rowid DESC LIMIT ?", (since, *names, args.recent))]
        emit(out)
    out["queries"] = [dict(zip(("query", "runs", "avg_ms", "max_ms", "errors", "last_at"), row)) for row in con.execute(
        f"SELECT query, count(*), round(avg(ms), 1), round(max(ms), 1), sum(ok = 0), max(at) FROM runs "
        f"WHERE at >= datetime('now', 'localtime', ?) AND query IN ({marks}) GROUP BY query ORDER BY max(ms) DESC LIMIT ?",
        (since, *names, args.top))]
    if not filters:
        out["no_match_searches"] = [dict(zip(("keyword", "times", "last_at"), row)) for row in con.execute(
            "SELECT keyword, count(*), max(at) FROM searches WHERE hits = 0 AND at >= datetime('now', 'localtime', ?) "
            "GROUP BY keyword ORDER BY count(*) DESC, max(at) DESC LIMIT ?", (since, args.top))]
    emit(out)


def fail(query, msg, **extra):
    emit({"ok": False, "query": query, "error": msg, **extra}, 1)


def find_root(explicit):
    if explicit:
        return os.path.abspath(explicit)
    cur = os.path.abspath(os.getcwd())
    while True:
        if os.path.isdir(os.path.join(cur, "db", "queries")) or os.path.isdir(os.path.join(cur, "db", "migrations")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            fail(None, "project root not found (no db/queries or db/migrations above cwd); pass --root")
        cur = parent


def strip_comments_and_strings(sql):
    """Remove -- and /* */ comments and string/identifier literals so parameter/keyword scans see only code."""
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in ("'", '"', "`"):
            j = i + 1
            while j < n:
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            out.append(" ")
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def split_statements(sql):
    stmts, buf = [], ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if strip_comments_and_strings(buf).strip().strip(";").strip():
                stmts.append(buf.strip())
            buf = ""
    if strip_comments_and_strings(buf).strip():
        stmts.append(buf.strip())
    return stmts


def parse_query(path):
    with open(path, encoding="utf-8-sig") as fh:  # -sig: tolerate a BOM (e.g. Notepad)
        sql = fh.read()
    desc, mode, exports, notes = None, None, [], []
    param_decls, column_docs, bad_decls = {}, {}, []
    for line in sql.splitlines():
        s = line.strip()
        if not s:
            continue
        if not s.startswith("--"):
            break
        body = s[2:].strip()
        m = re.match(r"mode\s*:\s*(read|write)\s*$", body, re.I)
        x = re.match(r"export\s*:\s*(.+)$", body, re.I)
        pd = PARAM_DECL.match(body)
        cd = COLUMN_DECL.match(body)
        if m:
            mode = m.group(1).lower()
        elif x:
            exports += [e.strip() for e in x.group(1).split(",") if e.strip()]
        elif pd:
            kind = (pd.group(2) or "").lower() or None
            if kind and kind not in PARAM_TYPES:
                bad_decls.append(f"unknown parameter type '{pd.group(2)}' (use json or text): {body}")
                kind = None
            param_decls[pd.group(1)] = {"type": kind, "doc": (pd.group(3) or "").strip() or None}
        elif cd:
            column_docs[cd.group(1)] = (cd.group(2) or "").strip() or None
        elif re.match(r"(param|column)\b", body, re.I):
            bad_decls.append(f"malformed header line: {body}")
            notes.append(body)
        elif desc is None and body:
            desc = body
        elif body:
            notes.append(body)
    stmts = split_statements(sql)
    code = [strip_comments_and_strings(s) for s in stmts]
    inferred = "read" if code and all(READ_START.match(c) and not WRITE_WORDS.search(c) for c in code) else "write"
    params = []
    for c in code:
        for p in PARAM.findall(c):
            if p not in params:
                params.append(p)
    return {"sql": sql, "statements": stmts, "code": code, "description": desc, "exports": exports,
            "mode": mode or inferred, "declaredMode": mode, "inferredMode": inferred, "params": params, "notes": notes,
            "param_decls": param_decls, "column_docs": column_docs, "bad_decls": bad_decls}


def run_export(root, db, name, params, raw=None):
    """Run db/exports/<name>.sql (a single read statement returning path + content columns) and write each row to
    <root>/<path>. Paths must stay inside the project root. Writes are atomic and skipped when content is unchanged.
    Only the parameters the export itself references are bound (a subset of the triggering query's params)."""
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        raise ValueError(f"invalid export name: {name}")
    path = os.path.join(root, "db", "exports", name + ".sql")
    if not os.path.isfile(path):
        raise ValueError(f"unknown export: {name}")
    q = parse_query(path)
    if q["inferredMode"] != "read" or len(q["statements"]) != 1:
        raise ValueError(f"export {name} must be exactly one read statement")
    missing = [p for p in q["params"] if p not in params]
    if missing:
        raise ValueError(f"export {name} needs parameters {missing}")
    bound = bind_values(q, {p: params[p] for p in q["params"]}, raw)
    con = connect(db_path(root, db), True)
    rows = [dict(r) for r in con.execute(q["statements"][0], bound)]
    root_abs = os.path.realpath(root)
    written, unchanged = [], []
    for r in rows:
        if "path" not in r or "content" not in r:
            raise ValueError(f"export {name} rows must have path and content columns")
        target = os.path.realpath(os.path.join(root_abs, str(r["path"])))
        if os.path.commonpath([root_abs, target]) != root_abs:
            raise ValueError(f"export {name} path escapes project root: {r['path']}")
        text = "" if r["content"] is None else str(r["content"])
        if os.path.isfile(target):
            with open(target, encoding="utf-8", newline="") as fh:
                if fh.read() == text:
                    unchanged.append(os.path.relpath(target, root_abs).replace(os.sep, "/"))
                    continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = target + ".nq-tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, target)
        written.append(os.path.relpath(target, root_abs).replace(os.sep, "/"))
    return {"export": name, "written": written, "unchanged": unchanged}


def cmd_export(args, root):
    try:
        raw = {}
        params = collect_params(args, raw)
        emit({"ok": True, **run_export(root, args.db, args.name, params, raw)})
    except (sqlite3.Error, ValueError, OSError) as e:
        fail(args.name, str(e))


def ro_uri(path):
    """Read-only SQLite URI with the path percent-encoded (a raw path breaks on '%', '#' or '?')."""
    return pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"


def db_path(root, explicit):
    """--db if given; else <root>/db/project.db if it exists; else a legacy <root>/project.db if that exists;
    else <root>/db/project.db (where a new database is created)."""
    if explicit:
        return os.path.abspath(explicit)
    new, legacy = os.path.join(root, "db", "project.db"), os.path.join(root, "project.db")
    if not os.path.exists(new) and os.path.exists(legacy):
        return legacy
    return new


def connect(path, read_only):
    if read_only:
        if not os.path.exists(path):
            raise sqlite3.OperationalError(f"database does not exist: {path} (run migrate first)")
        con = sqlite3.connect(ro_uri(path), uri=True, timeout=5.0, isolation_level=None)
    else:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        con = sqlite3.connect(path, timeout=5.0, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA foreign_keys=ON")
    con.row_factory = sqlite3.Row
    return con


def cmd_list(args, root):
    qdir = os.path.join(root, "db", "queries")
    items = []
    for fn in sorted(os.listdir(qdir)) if os.path.isdir(qdir) else []:
        if fn.endswith(".sql"):
            q = parse_query(os.path.join(qdir, fn))
            item = {"name": fn[:-4], "mode": q["mode"], "params": param_info(q), "exports": q["exports"], "description": q["description"]}
            if q["column_docs"]:
                item["columns"] = q["column_docs"]
            items.append((item, q))
    words = getattr(args, "keyword", None) or []
    if not words:
        emit({"ok": True, "root": root, "queries": [i for i, _ in items]})

    def pattern(word):
        # Each word is a case-insensitive regular expression (duration|timing, ^task_, history$); a word that is not
        # a valid pattern is searched as plain text. ^ and $ apply per line; the name is a line of its own, and
        # parameter / column lines start with "param " / "column ", so ^task_ matches query names only.
        try:
            return re.compile(word, re.I | re.M)
        except re.error:
            return re.compile(re.escape(word), re.I)

    def haystack(item, q):
        parts = [item["name"], item["description"] or ""] + q["notes"]
        for p in item["params"]:
            parts.append(f"param {p['name']}: {p.get('doc') or ''}")
        for c, doc in q["column_docs"].items():
            parts.append(f"column {c}: {doc or ''}")
        return "\n".join(parts)

    pats = [pattern(w) for w in words]
    hits = [i for i, q in items if all(p.search(haystack(i, q)) for p in pats)]
    keyword = " ".join(args.keyword)
    if hits:
        emit({"ok": True, "root": root, "keyword": keyword, "queries": hits})
    # An empty search is easily read as "no such query exists"; hand back every name and description instead.
    emit({"ok": True, "root": root, "keyword": keyword, "match": 0, "message": f"no match: {keyword}",
          "queries": [{"name": i["name"], "description": i["description"]} for i, _ in items]})


def similar_queries(root, name):
    """Up to three existing query names close to a mistyped one (most failed runs in real use were guessed names),
    as {"did_you_mean": [...]}; empty when nothing is close."""
    qdir = os.path.join(root, "db", "queries")
    names = [f[:-4] for f in os.listdir(qdir) if f.endswith(".sql")] if os.path.isdir(qdir) else []
    lowered = {n.lower(): n for n in names}
    close = [lowered[m] for m in difflib.get_close_matches(name.lower(), list(lowered), n=3, cutoff=0.6)]
    for n in names:  # also offer names that contain every part of the guess (task_get -> task_get_full)
        if len(close) < 3 and n not in close and all(part in n.lower() for part in name.lower().split("_") if part):
            close.append(n)
    return {"did_you_mean": close} if close else {}


def cmd_run(args, root):
    name = args.name
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        fail(name, "invalid query name")
    path = os.path.join(root, "db", "queries", name + ".sql")
    if not os.path.isfile(path):
        fail(name, f"unknown query: {name}", **similar_queries(root, name), hint=LIST_HINT)
    q = parse_query(path)
    if q["declaredMode"] == "read" and q["inferredMode"] == "write":
        fail(name, "query declares mode: read but contains write statements")
    try:
        raw = {}
        params = collect_params(args, raw)
    except (ValueError, OSError) as e:
        fail(name, f"invalid params: {e}")
    missing = [p for p in q["params"] if p not in params]
    extra = [p for p in params if p not in q["params"]]
    if missing or extra:
        # The definition is already loaded, so hand back what the query is and what its parameters mean
        # (usually written in the header comments) and save the caller a round trip.
        about = {"description": q["description"], "params": param_info(q)}
        if q["notes"]:
            about["notes"] = q["notes"]
        fail(name, "parameter mismatch", missing=missing, unexpected=extra, expected=q["params"], **about, hint=LIST_HINT)
    try:
        bound = bind_values(q, params, raw)
    except ValueError as e:
        fail(name, str(e), params=param_info(q))
    read_only = q["mode"] == "read"
    if read_only and len(q["statements"]) != 1:
        fail(name, "a read query must contain exactly one statement")
    try:
        con = connect(db_path(root, args.db), read_only)
    except sqlite3.Error as e:
        fail(name, str(e))
    try:
        if read_only:
            cur = con.execute(q["statements"][0], {p: bound[p] for p in q["params"]})
            rows = cur.fetchmany(args.max_rows + 1)
            truncated = len(rows) > args.max_rows
            out = [dict(r) for r in rows[:args.max_rows]]
            if args.raw:
                emit_raw(name, [dict(r) for r in rows], args.raw)
            res = {"ok": True, "query": name, "mode": "read", "rows": out, "row_count": len(out), "truncated": truncated}
            if q["column_docs"]:
                res["columns"] = q["column_docs"]
            emit(res)
        con.execute("BEGIN IMMEDIATE")
        affected, returned = 0, []
        for stmt, code in zip(q["statements"], q["code"]):
            own = {p: bound[p] for p in PARAM.findall(code)}
            cur = con.execute(stmt, own)
            if cur.description:
                returned.extend(dict(r) for r in cur.fetchall())
            if cur.rowcount and cur.rowcount > 0:
                affected += cur.rowcount
        con.execute("COMMIT")
        res = {"ok": True, "query": name, "mode": "write", "affected_rows": affected}
        if returned:
            res["rows"] = returned[:args.max_rows]
            res["row_count"] = len(res["rows"])
            if q["column_docs"]:
                res["columns"] = q["column_docs"]
        if q["exports"]:
            try:
                res["exports"] = [run_export(root, args.db, ex, bound, raw) for ex in q["exports"]]
            except (sqlite3.Error, ValueError, OSError) as e:
                res["ok"] = False
                res["error"] = f"write committed but export failed: {e}"
                emit(res, 1)
        if args.raw:
            emit_raw(name, returned, args.raw)
        emit(res)
    except (sqlite3.Error, ValueError, TypeError, OverflowError) as e:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        fail(name, str(e), rolled_back=not read_only)


def migrations(root):
    mdir = os.path.join(root, "db", "migrations")
    files = sorted(f for f in os.listdir(mdir) if re.match(r"^\d+.*\.sql$", f)) if os.path.isdir(mdir) else []
    return mdir, files


def file_sha256(path):
    import hashlib
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read().replace(bytes([13, 10]), bytes([10]))).hexdigest()  # CRLF -> LF: line-ending independent


def applied_set(con):
    con.execute("CREATE TABLE IF NOT EXISTS _nq_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    if "checksum" not in {r[1] for r in con.execute("PRAGMA table_info(_nq_migrations)")}:
        con.execute("ALTER TABLE _nq_migrations ADD COLUMN checksum TEXT")
    return {r[0] for r in con.execute("SELECT name FROM _nq_migrations")}


def adopt_checksums(con, mdir):
    """Record checksums for migrations applied before checksums existed (trusting the current file)."""
    adopted = []
    for (name,) in con.execute("SELECT name FROM _nq_migrations WHERE checksum IS NULL").fetchall():
        path = os.path.join(mdir, name)
        if os.path.isfile(path):
            con.execute("UPDATE _nq_migrations SET checksum = ? WHERE name = ?", (file_sha256(path), name))
            adopted.append(name)
    return adopted


def cmd_migrate(args, root):
    mdir, files = migrations(root)
    con = connect(db_path(root, args.db), False)
    done = applied_set(con)
    applied = []
    for f in files:
        if f in done:
            continue
        with open(os.path.join(mdir, f), encoding="utf-8-sig") as fh:
            sql = fh.read()
        # "-- nq: foreign_keys=off" at the top: the SQLite table-rebuild procedure. Foreign keys are switched off
        # for this migration only (the pragma is ignored inside a transaction, so it is set before BEGIN), and
        # PRAGMA foreign_key_check must come back empty before COMMIT.
        fk_off = re.search(r"^--\s*nq:\s*foreign_keys\s*=\s*off\s*$", sql, re.I | re.M) is not None
        try:
            if fk_off:
                con.execute("PRAGMA foreign_keys=OFF")
            con.execute("BEGIN IMMEDIATE")
            for stmt in split_statements(sql):
                con.execute(stmt)
            if fk_off:
                bad = con.execute("PRAGMA foreign_key_check").fetchall()
                if bad:
                    raise sqlite3.IntegrityError(f"foreign_key_check failed after rebuild: {[tuple(r) for r in bad[:5]]}")
            con.execute("INSERT INTO _nq_migrations(name, checksum) VALUES (?, ?)", (f, file_sha256(os.path.join(mdir, f))))
            con.execute("COMMIT")
            applied.append(f)
        except sqlite3.Error as e:
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            fail(None, f"migration {f} failed: {e}", applied=applied)
        finally:
            if fk_off:
                con.execute("PRAGMA foreign_keys=ON")
    adopted = adopt_checksums(con, mdir)
    emit({"ok": True, "db": db_path(root, args.db), "applied": applied, **({"checksumsAdopted": adopted} if adopted else {})})


def cmd_backup(args, root):
    """Copy the database with sqlite3's backup API into <root>/db/backup/project-<label>-<YYYYMMDD-HHMMSS>.db (or
    project-<YYYYMMDD-HHMMSS>.db without a label), then keep only the newest
    --keep backups. The copy is written to a temp file and renamed, so a failed backup leaves nothing half-written.
    db/backup/ gets its own .gitignore ("*") so backups never end up in git whatever the project ignores."""
    src = db_path(root, args.db)
    if not os.path.exists(src):
        fail(None, f"database does not exist: {src}")
    if args.label is not None and not re.fullmatch(r"[A-Za-z0-9_.-]+", args.label):
        fail(None, f"invalid label: {args.label}")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    label = f"{args.label}-{stamp}" if args.label else stamp
    if args.keep < 1:
        fail(None, "--keep must be at least 1")
    bdir = os.path.join(root, "db", "backup")
    target = os.path.join(bdir, f"project-{label}.db")
    tmp = target + ".nq-tmp"
    try:
        os.makedirs(bdir, exist_ok=True)
        ignore = os.path.join(bdir, ".gitignore")
        if not os.path.exists(ignore):
            with open(ignore, "w", encoding="utf-8") as fh:
                fh.write("# created by nq.py backup: keep database backups out of git\n*\n")
        source = connect(src, True)
        dest = sqlite3.connect(tmp)
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
        os.replace(tmp, target)
    except (sqlite3.Error, OSError) as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        fail(None, f"backup failed: {e}")
    def taken_at(f):
        # The date-time in the file name survives copying and unzipping; the file's mtime may not.
        # mtime only decides files without a date-time in the name, and ties within the same second.
        mtime = os.path.getmtime(os.path.join(bdir, f))
        m = re.search(r"(\d{8})[-T](\d{6})\.db$", f)
        return (m.group(1) + m.group(2) if m else time.strftime("%Y%m%d%H%M%S", time.localtime(mtime)), mtime)
    backups = sorted((f for f in os.listdir(bdir) if re.fullmatch(r"project-.+\.db", f)), key=taken_at, reverse=True)
    removed = []
    for f in backups[args.keep:]:
        os.remove(os.path.join(bdir, f))
        removed.append(f)
    emit({"ok": True, "db": src, "backup": os.path.relpath(target, root).replace(os.sep, "/"),
          "kept": len(backups) - len(removed), "removed": removed})


def cmd_status(args, root):
    path = db_path(root, args.db)
    mdir, files = migrations(root)
    done = set()
    if os.path.exists(path):
        con = connect(path, True)  # read-only: status never creates tables or switches journal mode
        if con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = '_nq_migrations'").fetchone():
            done = {r[0] for r in con.execute("SELECT name FROM _nq_migrations")}
    emit({"ok": True, "root": root, "db": path, "exists": os.path.exists(path),
          "applied": sorted(done), "pending": [f for f in files if f not in done], "version": __version__})


def cmd_check(args, root):
    """Static + compile check of the project's db/ folder: applied migrations unchanged (checksum), query/export
    headers consistent, exports exist and only use parameters their triggering queries have, and every statement
    compiles (EXPLAIN) against the current database. Nothing is executed or written."""
    problems, notes = [], []
    path = db_path(root, args.db)
    mdir, files = migrations(root)
    pending = []
    if os.path.exists(path):
        con = sqlite3.connect(ro_uri(path), uri=True)
        cols = {r[1] for r in con.execute("PRAGMA table_info(_nq_migrations)")}
        rows = con.execute("SELECT name, " + ("checksum" if "checksum" in cols else "NULL") + " FROM _nq_migrations").fetchall() if cols else []
        done = {n for n, _ in rows}
        for name, cs in rows:
            fp = os.path.join(mdir, name)
            if not os.path.isfile(fp):
                problems.append(f"migration {name}: applied but file is missing")
            elif cs is None:
                notes.append(f"migration {name}: no checksum yet (run migrate to record it)")
            elif cs != file_sha256(fp):
                problems.append(f"migration {name}: file changed after it was applied (add a new migration instead)")
        pending = [f for f in files if f not in done]
    else:
        con = None
        notes.append("database does not exist; compile checks skipped")
    if pending:
        notes.append(f"pending migrations {pending}; compile checks skipped")

    def compile_ok(label, stmts, params):
        if con is None or pending:
            return
        for st in stmts:
            try:
                con.execute("EXPLAIN " + st, {p: None for p in params if p in PARAM.findall(strip_comments_and_strings(st))})
            except sqlite3.Error as e:
                problems.append(f"{label}: {e}")

    def docs_ok(label, q):
        """Documented parameters and columns must exist, or the docs silently go stale."""
        for bad in q["bad_decls"]:
            problems.append(f"{label}: {bad}")
        for p in q["param_decls"]:
            if p not in q["params"]:
                problems.append(f"{label}: '-- param {p}' documents a parameter the query does not take")
        if not q["column_docs"]:
            return
        if q["inferredMode"] != "read" or len(q["statements"]) != 1:
            notes.append(f"{label}: column docs are not verified for write queries")
            return
        if con is None or pending:
            return
        # Ask SQLite for the result's column names without producing rows.
        st = re.sub(r";\s*(--[^\n]*)?\s*$", "", q["statements"][0].strip())
        try:
            cur = con.execute(f"SELECT * FROM (\n{st}\n) LIMIT 0", {p: None for p in q["params"]})
            names = [d[0] for d in cur.description]
        except sqlite3.Error as e:
            notes.append(f"{label}: could not read result columns to verify column docs ({e})")
            return
        for c in q["column_docs"]:
            if c not in names:
                problems.append(f"{label}: '-- column {c}' documents a column the result does not have (has {names})")

    exports = {}
    edir = os.path.join(root, "db", "exports")
    for fn in sorted(os.listdir(edir)) if os.path.isdir(edir) else []:
        if fn.endswith(".sql"):
            q = parse_query(os.path.join(edir, fn))
            exports[fn[:-4]] = q
            if q["inferredMode"] != "read" or len(q["statements"]) != 1:
                problems.append(f"export {fn[:-4]}: must be exactly one read statement")
            compile_ok(f"export {fn[:-4]}", q["statements"], q["params"])
            docs_ok(f"export {fn[:-4]}", q)
    qdir = os.path.join(root, "db", "queries")
    count = 0
    for fn in sorted(os.listdir(qdir)) if os.path.isdir(qdir) else []:
        if not fn.endswith(".sql"):
            continue
        count += 1
        name, q = fn[:-4], parse_query(os.path.join(qdir, fn))
        if not q["statements"]:
            problems.append(f"query {name}: empty")
            continue
        if q["declaredMode"] == "read" and q["inferredMode"] == "write":
            problems.append(f"query {name}: declares mode: read but contains write statements")
        if q["mode"] == "read" and len(q["statements"]) != 1:
            problems.append(f"query {name}: a read query must contain exactly one statement")
        if q["mode"] == "read" and q["exports"]:
            problems.append(f"query {name}: exports only run after write queries")
        if not q["description"]:
            notes.append(f"query {name}: no description line")
        for ex in q["exports"]:
            if ex not in exports:
                problems.append(f"query {name}: unknown export {ex}")
            else:
                extra = [p for p in exports[ex]["params"] if p not in q["params"]]
                if extra:
                    problems.append(f"query {name}: export {ex} needs parameters the query does not take: {extra}")
        compile_ok(f"query {name}", q["statements"], q["params"])
        docs_ok(f"query {name}", q)
    emit({"ok": not problems, "root": root, "queries": count, "exports": len(exports), "migrations": len(files),
          "problems": problems, "notes": notes}, 0 if not problems else 1)


class JsonArgumentParser(argparse.ArgumentParser):
    """Argument mistakes come back as one JSON object too (callers are agents), with that command's usage and
    options, so the right arguments are known at the moment of the mistake. Subcommand parsers inherit this."""

    def error(self, message):
        # A command's own help lists its options; the top-level help is the long module docstring, so give only its
        # short usage (which names the commands) there.
        usage = self.format_usage() if self.description else self.format_help()
        print(json.dumps({"ok": False, "query": None, "error": f"invalid arguments: {message}",
                          "usage": usage.strip()}, ensure_ascii=False))
        sys.exit(2)


def main():
    ap = JsonArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"nq.py {__version__}")
    ap.add_argument("--root", help="project root (default: nearest ancestor containing db/queries or db/migrations)")
    ap.add_argument("--db", help="database path (default: <root>/db/project.db, or an existing <root>/project.db)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("list")
    lp.add_argument("keyword", nargs="*", help="only queries whose name, description, notes, parameter or column "
                    "docs match every word (case-insensitive regex, e.g. 'duration|timing', '^task_'); no match "
                    "returns all names and descriptions")
    r = sub.add_parser("run")
    r.add_argument("name")
    r.add_argument("--params", help="JSON object of named parameters")
    r.add_argument("--params-file", help="file containing the JSON params object")
    r.add_argument("--param", action="append", metavar="KEY=VALUE", help="one parameter (JSON value if valid, else string); repeatable")
    r.add_argument("--param-file", action="append", metavar="KEY=PATH", help="one parameter read from a text file; repeatable")
    r.add_argument("--raw", metavar="COLUMN", help="print this column of the single result row as plain text instead of JSON")
    r.add_argument("--max-rows", type=int, default=200)
    sub.add_parser("migrate")
    sub.add_parser("status")
    sub.add_parser("check")
    b = sub.add_parser("backup")
    b.add_argument("--label", help="name part before the date-time, e.g. task-090 (optional)")
    b.add_argument("--keep", type=int, default=7, help="number of backups to keep (default 7)")
    st = sub.add_parser("stats")
    st.add_argument("--days", type=int, default=30, help="look back this many days (default 30)")
    st.add_argument("--top", type=int, default=20, help="rows per list (default 20)")
    st.add_argument("--query", help="only queries whose name matches this (case-insensitive regex, e.g. '^task_')")
    st.add_argument("--table", help="only queries whose SQL uses this table")
    st.add_argument("--recent", type=int, default=0, metavar="N", help="show the latest N runs one by one instead of totals")
    x = sub.add_parser("export")
    x.add_argument("name")
    x.add_argument("--params", help="JSON object of named parameters")
    x.add_argument("--params-file", help="file containing the JSON params object")
    x.add_argument("--param", action="append", metavar="KEY=VALUE")
    x.add_argument("--param-file", action="append", metavar="KEY=PATH")
    args, extra = ap.parse_known_args()
    if extra:  # report unknown arguments against the command they were given to, with that command's options
        sub.choices[args.cmd].error(f"unrecognized arguments: {' '.join(extra)}")
    root = find_root(args.root)
    started = time.perf_counter()
    try:
        try:
            {"list": cmd_list, "run": cmd_run, "migrate": cmd_migrate, "status": cmd_status, "export": cmd_export,
             "check": cmd_check, "backup": cmd_backup, "stats": cmd_stats}[args.cmd](args, root)
        except SystemExit:
            raise
        except Exception as e:  # keep the "always one JSON object" contract
            fail(getattr(args, "name", None), f"{type(e).__name__}: {e}")
    finally:
        record_stats(root, args, time.perf_counter() - started)


if __name__ == "__main__":
    main()
