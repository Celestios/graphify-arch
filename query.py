#!/usr/bin/env python3
import sqlite3, json, argparse, re, sys
from pathlib import Path

DB_PATH = Path(".celial_graph.db")
if not DB_PATH.exists():
    print("DB missing; run Reindex first")
    sys.exit(1)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

TYPES = ["File", "Module", "Class", "Struct", "Trait", "Enum", "Extension", "Function", "Method"]
LAYERS = ["Tier1Ui", "Tier2Fsm", "Tier3Domain"]

def nodes(**kw):
    clauses = []
    params = []
    if kw.get("path"):
        clauses.append("filepath = ?"); params.append(kw["path"])
    if kw.get("type"):
        clauses.append("node_type = ?"); params.append(kw["type"])
    if kw.get("like"):
        clauses.append("id LIKE ?"); params.append(f"%{kw['like']}%")
    if kw.get("return_types"):
        clauses.append("json_extract(semantics, '$.purity') = ?"); params.append(kw["return_types"])
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT id, filepath, node_type, start_byte, end_byte, raw_code, semantics FROM nodes {where} ORDER BY filepath, start_byte"
    cur.execute(sql, params)
    return cur.fetchall()

def print_nodes(rows):
    for r in rows:
        print(f"{r['id']}\n  type={r['node_type']} file={r['filepath']}")
        print(f"  code={r['raw_code'][:200].replace(chr(10), ' ')}")
        print()

def methods_for_file(path, klass=None, impl=None):
    cn = f"{path}::%" if not klass else f"{path}::{klass}::%"
    sql = "SELECT id, filepath, start_byte, end_byte, raw_code, semantics FROM nodes WHERE node_type = 'Method' AND id LIKE ?"
    params = [cn]
    if klass and not impl:
        sql += " AND id LIKE ?"; params.append(f"{path}::{klass}::%")
    if impl:
        sql += " AND id LIKE ?"; params.append(f"{path}::impl_{impl}::%")
    cur.execute(sql, params)
    return cur.fetchall()

def independent_functions(filepath=None):
    sql = "SELECT id, filepath, start_byte, end_byte, raw_code FROM nodes WHERE node_type = 'Function'"
    params = []
    if filepath:
        sql += " AND filepath = ?"; params.append(filepath)
    cur.execute(sql, params)
    return cur.fetchall()

def classes(path=None):
    sql = "SELECT id, filepath, node_type, raw_code FROM nodes WHERE node_type IN ('Class', 'Struct')"
    params = []
    if path:
        sql += " AND filepath = ?"; params.append(path)
    cur.execute(sql, params)
    return cur.fetchall()

def imports(path=None, text_contains=None):
    if text_contains:
        pattern = f"%{text_contains}%"
        sql = "SELECT id, filepath, raw_code FROM nodes WHERE (node_type = 'File') AND (id LIKE ? OR raw_code LIKE ?)"
        cur.execute(sql, [pattern, pattern])
        return cur.fetchall()
    sql = "SELECT id, filepath, raw_code FROM nodes WHERE node_type = 'File'"
    params = []
    if path:
        sql += " AND filepath = ?"; params.append(path)
    cur.execute(sql, params)
    return cur.fetchall()

def calls_for(node_id):
    cur.execute("SELECT e.target_id, t.raw_code FROM edges e JOIN nodes t ON e.target_id = t.id WHERE e.source_id = ? AND e.edge_type = 'Calls'", [node_id])
    return cur.fetchall()

def containing(parent_id):
    cur.execute("SELECT target_id, node_type FROM edges e JOIN nodes n ON e.target_id = n.id WHERE e.source_id = ? AND e.edge_type = 'Contains'", [parent_id])
    return cur.fetchall()

parser = argparse.ArgumentParser(description="Arch_indexer query runner")
sub = parser.add_subparsers(dest="cmd")

list_p = sub.add_parser("list", help="list nodes")
list_p.add_argument("--path")
list_p.add_argument("--type", choices=TYPES)
list_p.add_argument("--like")
list_p.add_argument("--return-types")
list_p.add_argument("--methods-for-file")
list_p.add_argument("--klass")
list_p.add_argument("--methods-for-impl")
list_p.add_argument("--independent-functions", action="store_true")
list_p.add_argument("--classes", action="store_true")
list_p.add_argument("--imports", action="store_true")
list_p.add_argument("--imports-contain", help="search import text")
list_p.add_argument("--calls-for")
list_p.add_argument("--containing")

args = parser.parse_args()

cmd = args.cmd or "list"
if cmd == "list":
    kw = {}
    if args.path: kw["path"] = args.path
    if args.type: kw["type"] = args.type
    if args.like: kw["like"] = args.like
    if args.return_types: kw["return_types"] = args.return_types
    rows = nodes(**kw)
    if not rows: print("No nodes found"); sys.exit(0)
    print_nodes(rows)
elif cmd == "methods-for-file":
    for r in methods_for_file(args.methods_for_file, args.klass, args.methods_for_impl):
        print(f"{r['id']}\n  code={r['raw_code'][:200].replace(chr(10), ' ')}\n")
elif cmd == "independent-functions":
    for r in independent_functions(args.path):
        print(f"{r['id']}\n  code={r['raw_code'][:200].replace(chr(10), ' ')}\n")
elif cmd == "classes":
    for r in classes(args.path):
        print(f"{r['id']}\n  code={r['raw_code'][:200].replace(chr(10), ' ')}\n")
elif cmd == "imports":
    for r in imports(args.path, args.imports_contain):
        print(f"{r['filepath']}\n  id={r['id']}\n  code={r['raw_code'][:200].replace(chr(10), ' ')}\n")
elif cmd == "calls-for":
    for r in calls_for(args.calls_for):
        print(f"{r['target_id']}\n  code={r['raw_code'][:200].replace(chr(10), ' ')}\n")
elif cmd == "containing":
    for r in containing(args.containing):
        print(f"{r['target_id']} ({r['node_type']})\n")
