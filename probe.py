#!/usr/bin/env python3
import json, pathlib
from collections import defaultdict

root = pathlib.Path(".")
counts = defaultdict(int)
for p in root.glob("test_samples/**/*.rs"):
    txt = p.read_text()
    counts["files"] += 1
    counts["pub_fns"] += txt.count("pub fn ")
    counts["crate_mod_uses"] += txt.count("crate::")
    if "mod " in txt:
        counts["has_mod_decl"] += 1
for p in root.glob("**/*.rs"):
    txt = p.read_text()
    if "query_network" in txt:
        print("found query_network in", p)
print(json.dumps(dict(counts), indent=2))
