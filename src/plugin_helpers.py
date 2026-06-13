import os
import sys
import json
import hashlib
import shutil
import subprocess
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List

from db import Violation
from auditor import audit_architecture_rules

class PluginCLIHandler:
    @staticmethod
    def print_arch_help():
        print("""Usage: graphify arch <command>

Architecture enforcement commands:

  audit                                   Check all files for architectural violations
  set-status <file> <status> [msg]        Override compliance status for a file
  update-component <json-file>            Update component metadata from a JSON file
  analyze                                 Validate ontology config syntax and consistency
  discover-ontology                       Print current ontology config
  query-file --path <file>                Query nodes for a specific file
  compile-context --target <id>           Compile subgraph context for LLM input
  semantic-search --query <text>          Vector similarity search over code
  setup-embeddings                        Download ONNX embedding model for semantic search
  install                                 Install arch skill section and reference files

Status values:
  COMPLIANT           File passes all architectural rules
  VIOLATION_DETECTED  File has at least one violation
  PENDING_AUDIT       File needs re-audit (e.g., after code changes)

Config:
  Define rules in graphify-out/arch/config.json
  See references/arch-config.md for the full schema""")

    @staticmethod
    def handle_arch_cli(root: Path, args: list[str], plugin_instance):
        import argparse
        parser = argparse.ArgumentParser(prog="graphify arch")
        subparsers = parser.add_subparsers(dest="arch_command", required=True)
        
        audit_parser = subparsers.add_parser("audit", help="Audit architectural graph structures")
        
        subparsers.add_parser("setup-embeddings", help="Download ONNX embedding model")
        subparsers.add_parser("analyze", help="Analyze configuration completeness and sanity")
        subparsers.add_parser("discover-ontology", help="Print current ontology config")
        
        set_status_parser = subparsers.add_parser("set-status", help="Set manual compliance status for a component")
        set_status_parser.add_argument("file_path", help="Relative file path to the component")
        set_status_parser.add_argument("status", choices=["COMPLIANT", "VIOLATION_DETECTED", "PENDING_AUDIT"], help="Compliance status")
        set_status_parser.add_argument("violations", nargs="?", default="", help="Description of violations")
        
        update_component_parser = subparsers.add_parser("update-component", help="Update component metadata from a JSON file (status, fields, etc.)")
        update_component_parser.add_argument("json_file", help="Path to the JSON file containing component updates")

        query_file_parser = subparsers.add_parser("query-file", help="Query nodes for a specific file")
        query_file_parser.add_argument("--path", required=True, help="Target file path")
        query_file_parser.add_argument("--methods", action="store_true", help="Filter to methods only")
        query_file_parser.add_argument("--classes", action="store_true", help="Filter to classes/structs only")
        query_file_parser.add_argument("--functions", action="store_true", help="Filter to functions only")
        query_file_parser.add_argument("--include-body", action="store_true", help="Include raw code body")

        compile_parser = subparsers.add_parser("compile-context", help="Compile subgraph context for LLM input")
        compile_parser.add_argument("--target", required=True, help="Focal node identifier")
        compile_parser.add_argument("--direction", required=True, choices=["upstream", "downstream", "symmetric"])
        compile_parser.add_argument("--resolution", required=True, choices=["full", "signature"])
        compile_parser.add_argument("--radius", default=1, type=int, help="Traversal hops (default: 1)")
        compile_parser.add_argument("--out", required=True, help="Output file path")

        search_parser = subparsers.add_parser("semantic-search", help="Vector similarity search over code")
        search_parser.add_argument("--query", required=True, help="Natural language query")
        search_parser.add_argument("--limit", default=5, type=int, help="Max results (default: 5)")

        subparsers.add_parser("install", help="Install arch skill section and reference files to AI assistant configs")

        parsed_args = parser.parse_args(args)
        
        if parsed_args.arch_command == "set-status":
            file_path = Path(parsed_args.file_path).as_posix()
            status = parsed_args.status
            violations_msg = parsed_args.violations or ""
            
            from project import ConfigLoader
            from db import Database
            try:
                config_path, db_path = ConfigLoader.find_config(root)
                db = Database(str(db_path))
            except Exception as e:
                print(f"error: failed to load database: {e}", file=sys.stderr)
                sys.exit(1)
                
            db.set_component_status(file_path, status, violations_msg)
            
            # Load and update graph.json
            graph_path = root / "graphify-out" / "graph.json"
            if graph_path.exists():
                try:
                    graph_data = json.loads(graph_path.read_text(encoding="utf-8"))
                    nodes = graph_data.get("nodes", [])
                    modified = False
                    for node in nodes:
                        sf = node.get("source_file") or ""
                        if sf:
                            try:
                                rel_sf = Path(sf).relative_to(root).as_posix()
                            except ValueError:
                                rel_sf = Path(sf).as_posix()
                            if rel_sf == file_path or rel_sf.endswith(file_path) or file_path.endswith(rel_sf):
                                node["arch_meta_manual_status"] = status
                                node["arch_meta_manual_violations"] = violations_msg
                                modified = True
                    if modified:
                        graph_path.write_text(json.dumps(graph_data, ensure_ascii=False), encoding="utf-8")
                except Exception as e:
                    print(f"warning: failed to update graph.json: {e}", file=sys.stderr)
                    
            print(f"Set manual status for '{file_path}' to {status}")
            sys.exit(0)
            
        elif parsed_args.arch_command == "audit":
            gp = root / "graphify-out" / "graph.json"
            if not gp.exists():
                print(f"error: graph file not found at {gp}")
                sys.exit(1)
                
            import networkx as nx
            from networkx.readwrite import json_graph
            with open(gp, "r", encoding="utf-8") as f:
                _raw = json.load(f)
            if "links" not in _raw and "edges" in _raw:
                _raw = dict(_raw, links=_raw["edges"])
            try:
                G = json_graph.node_link_graph(_raw, directed=True, edges="links")
            except TypeError:
                G = json_graph.node_link_graph(_raw, directed=True)
            
            # Run metadata annotation before auditing
            from plugins import discover_plugins, run_hook
            plugins = discover_plugins(root)
            G = run_hook(plugins, "on_post_build", G, {}, root)
                
            ontology = plugin_instance.load_ontology(root)
            violations = audit_architecture_rules(G, ontology, root)
            
            # Load database and update violations/statuses in db.json
            from project import ConfigLoader
            from db import Database
            file_level_violations = []
            from utils import resolve_relative_path
            try:
                config_path, db_path = ConfigLoader.find_config(root)
                db = Database(str(db_path))
                db.update_violations_and_statuses(violations, ontology, root, G)
                db.sync_graph_metadata(G, root)
                
                components = db.get_all_components()
                for filepath, comp in sorted(components.items()):
                    status = comp.get("status")
                    if status in ("VIOLATION_DETECTED", "PENDING_AUDIT"):
                        resolved_vlist = []
                        for raw_v in (comp.get("violations") or []):
                            v = dict(raw_v)
                            src = v.get("source_node")
                            tgt = v.get("target_node")
                            if src and tgt:
                                src_file = G.nodes[src].get("source_file") if src in G.nodes else None
                                tgt_file = G.nodes[tgt].get("source_file") if tgt in G.nodes else None
                                if src_file:
                                    src_file = resolve_relative_path(src_file, root)
                                if tgt_file:
                                    tgt_file = resolve_relative_path(tgt_file, root)
                                v["source_file"] = src_file if src_file else src
                                v["target_file"] = tgt_file if tgt_file else tgt
                                v.pop("source_node", None)
                                v.pop("target_node", None)
                            resolved_vlist.append(v)
                        
                        file_level_violations.append({
                            "filepath": filepath,
                            "status": status,
                            "violations": resolved_vlist
                        })
            except Exception as e:
                print(f"warning: failed to update/query database: {e}", file=sys.stderr)
                by_file = {}
                for v in violations:
                    fpath = resolve_relative_path(v.filepath, root)
                    src = v.source_id
                    tgt = v.target_id
                    src_file = G.nodes[src].get("source_file") if src in G.nodes else None
                    tgt_file = G.nodes[tgt].get("source_file") if tgt in G.nodes else None
                    if src_file:
                        src_file = resolve_relative_path(src_file, root)
                    if tgt_file:
                        tgt_file = resolve_relative_path(tgt_file, root)
                        
                    by_file.setdefault(fpath, []).append({
                        "origin": "automated",
                        "rule": v.rule_name,
                        "message": v.message,
                        "source_file": src_file if src_file else src,
                        "target_file": tgt_file if tgt_file else tgt,
                        "filepath": fpath
                    })
                for filepath, file_v in sorted(by_file.items()):
                    file_level_violations.append({
                        "filepath": filepath,
                        "status": "VIOLATION_DETECTED",
                        "violations": file_v
                    })
                
            analysis = {"violations": file_level_violations}
            print(json.dumps(analysis, indent=2))
            sys.exit(0)
            
        elif parsed_args.arch_command == "setup-embeddings":
            from embedder import LocalEmbedder
            embedder = LocalEmbedder()
            print("Embeddings setup completed successfully.")
            sys.exit(0)
            
        elif parsed_args.arch_command == "analyze":
            try:
                ontology = plugin_instance.load_ontology(root)
            except Exception as e:
                print(f"Failed to load ontology: {e}", file=sys.stderr)
                sys.exit(1)
                
            print("=== Configuration Completeness Analysis ===")
            
            unreachable = []
            for dir_name in ontology.directories:
                fields = ontology.get_fields_for_file(dir_name)
                for name, f_config in fields.items():
                    assigned = {rule.value for rule in f_config.assignment_rules}
                    if f_config.values is not None:
                        for val in f_config.values:
                            if val != f_config.default and val not in assigned:
                                unreachable.append((dir_name, name, val))
                            
            if unreachable:
                print("\n[WARNING] Unreachable allowed values detected:")
                for dir_name, name, val in unreachable:
                    print(f"  - Directory '{dir_name}' Field '{name}': Value '{val}' is allowed but never assigned by rules and is not the default.")
            else:
                print("\n[OK] All allowed values are reachable via assignment rules or defaults.")
                
            errors = []
            for dir_name in ontology.directories:
                fields = ontology.get_fields_for_file(dir_name)
                for name, f_config in fields.items():
                    if f_config.values is not None:
                        for r in f_config.rules:
                            if r.source not in f_config.values:
                                errors.append(f"Directory '{dir_name}' Field '{name}' rule source '{r.source}' is not in values list {f_config.values}.")
                            if r.target not in f_config.values:
                                errors.append(f"Directory '{dir_name}' Field '{name}' rule target '{r.target}' is not in values list {f_config.values}.")
                    for b in f_config.barriers:
                        b_field = fields.get(f_config.barrier_field)
                        if b_field and b_field.values is not None and b not in b_field.values:
                            errors.append(f"Directory '{dir_name}' Field '{name}' barrier '{b}' is not in allowed values of barrier_field '{f_config.barrier_field}' ({b_field.values}).")
            
            if errors:
                print("\n[ERROR] Referential integrity failures:")
                for err in errors:
                    print(f"  - {err}")
                sys.exit(1)
            else:
                print("[OK] Referential integrity validation passed.")
            sys.exit(0)

        elif parsed_args.arch_command == "install":
            from plugin_helpers import PluginReportGenerator
            
            # Write to all known environments
            all_env_skill_dirs = [
                Path.home() / ".gemini" / "config" / "skills" / "graphify",
                Path.home() / ".gemini" / "skills" / "graphify",
                Path.home() / ".claude" / "skills" / "graphify",
                Path.home() / ".codex" / "skills" / "graphify",
                Path.home() / ".config" / "opencode" / "skills" / "graphify",
                Path.home() / ".config" / "kilo" / "skills" / "graphify",
                Path.home() / ".config" / "devin" / "skills" / "graphify",
                Path.home() / ".copilot" / "skills" / "graphify",
                Path.home() / ".openclaw" / "skills" / "graphify",
                Path.home() / ".factory" / "skills" / "graphify",
                Path.home() / ".trae" / "skills" / "graphify",
                Path.home() / ".kiro" / "skills" / "graphify",
                Path.home() / ".pi" / "agent" / "skills" / "graphify",
                Path.home() / ".codebuddy" / "skills" / "graphify",
                Path.home() / ".agents" / "skills" / "graphify",
                root / ".agents" / "skills" / "graphify",
                root / ".gemini" / "skills" / "graphify",
                root / ".devin" / "skills" / "graphify",
                root / ".opencode" / "skills" / "graphify",
                root / ".codex" / "skills" / "graphify",
                root / ".cursor" / "skills" / "graphify",
            ]
            
            installed_count = 0
            for env_dir in all_env_skill_dirs:
                if env_dir.exists():
                    PluginReportGenerator.post_env_installation_hook(env_dir, root)
                    installed_count += 1
            
            if installed_count == 0:
                print("No graphify skill directories found. Install graphify first, then run 'graphify arch install'.")
            else:
                print(f"Installed arch skill section and reference files to {installed_count} environment(s).")
            sys.exit(0)

        elif parsed_args.arch_command == "discover-ontology":
            from project import ConfigLoader
            try:
                config_path, db_path = ConfigLoader.find_config(root)
                ontology = ConfigLoader.load(config_path).ontology
            except Exception as e:
                print(f"error: failed to load ontology: {e}", file=sys.stderr)
                sys.exit(1)
            import dataclasses
            print(json.dumps(dataclasses.asdict(ontology), indent=2))
            sys.exit(0)

        elif parsed_args.arch_command == "update-component":
            from project import ConfigLoader
            from db import Database
            try:
                config_path, db_path = ConfigLoader.find_config(root)
                db = Database(str(db_path))
                ontology = ConfigLoader.load(config_path).ontology
            except Exception as e:
                print(f"error: failed to initialize: {e}", file=sys.stderr)
                sys.exit(1)
            
            json_file_path = Path(parsed_args.json_file).resolve()
            if not json_file_path.exists():
                print(f"error: JSON file not found at {json_file_path}", file=sys.stderr)
                sys.exit(1)
            try:
                payload = json.loads(json_file_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"error: failed to load JSON file: {e}", file=sys.stderr)
                sys.exit(1)
            
            if not isinstance(payload, dict):
                print("error: JSON must be an object with filepath keys", file=sys.stderr)
                sys.exit(1)
            
            db.set_component_status_bulk(payload, ontology, root)
            
            graph_path = root / "graphify-out" / "graph.json"
            if graph_path.exists():
                try:
                    graph_data = json.loads(graph_path.read_text(encoding="utf-8"))
                    nodes = graph_data.get("nodes", [])
                    modified = False
                    for node in nodes:
                        sf = node.get("source_file") or ""
                        if sf:
                            try:
                                rel_sf = Path(sf).relative_to(root).as_posix()
                            except ValueError:
                                rel_sf = Path(sf).as_posix()
                            for raw_key, entry_data in payload.items():
                                from utils import resolve_relative_path as _rlp
                                rel_key = _rlp(raw_key, root)
                                if rel_sf == rel_key or rel_sf.endswith(rel_key) or rel_key.endswith(rel_sf):
                                    status = (entry_data.get("status") or "").upper()
                                    if status in ("COMPLIANT", "VIOLATION_DETECTED", "PENDING_AUDIT"):
                                        node["arch_meta_manual_status"] = status
                                    violations_raw = entry_data.get("violations", "")
                                    if isinstance(violations_raw, list):
                                        violations_msg = " | ".join(str(v) for v in violations_raw)
                                    else:
                                        violations_msg = str(violations_raw) if violations_raw else ""
                                    node["arch_meta_manual_violations"] = violations_msg
                                    for field_name, field_val in entry_data.items():
                                        if field_name not in ("status", "violations"):
                                            node[f"arch_meta_{field_name}"] = field_val
                                    modified = True
                    if modified:
                        graph_path.write_text(json.dumps(graph_data, ensure_ascii=False), encoding="utf-8")
                except Exception as e:
                    print(f"warning: failed to update graph.json: {e}", file=sys.stderr)
            
            print(f"Updated {len(payload)} component(s).")
            sys.exit(0)

        elif parsed_args.arch_command == "query-file":
            from project import ConfigLoader
            from db import Database
            from schema import AstNodeType
            try:
                config_path, db_path = ConfigLoader.find_config(root)
                db = Database(str(db_path))
            except Exception as e:
                print(f"error: failed to load database: {e}", file=sys.stderr)
                sys.exit(1)
            
            nodes = db._get_all_nodes_internal().values()
            results = []
            for n in nodes:
                if n.filepath != parsed_args.path:
                    continue
                match = False
                if not (parsed_args.methods or parsed_args.classes or parsed_args.functions):
                    match = True
                else:
                    if parsed_args.methods and n.node_type == AstNodeType.METHOD:
                        match = True
                    if parsed_args.functions and n.node_type == AstNodeType.FUNCTION:
                        match = True
                    if parsed_args.classes and n.node_type in (AstNodeType.STRUCT, AstNodeType.CLASS):
                        match = True
                if match:
                    result = {
                        "id": n.id,
                        "filepath": n.filepath,
                        "node_type": n.node_type.value,
                        "raw_code": n.raw_code if parsed_args.include_body else None
                    }
                    sem = n.semantics
                    for field_name in ("layer", "tier", "purity", "architectural_role", "pattern"):
                        result[field_name] = sem.fields.get(field_name, "Unknown")
                    results.append(result)
            print(json.dumps(results, indent=2))
            sys.exit(0)

        elif parsed_args.arch_command == "compile-context":
            from project import ConfigLoader
            from db import Database
            from compiler import ContextCompiler
            try:
                config_path, db_path = ConfigLoader.find_config(root)
                db = Database(str(db_path))
                ontology = ConfigLoader.load(config_path).ontology
            except Exception as e:
                print(f"error: failed to initialize: {e}", file=sys.stderr)
                sys.exit(1)
            
            nodes = db.get_subgraph(parsed_args.target, parsed_args.radius, parsed_args.direction)
            ContextCompiler.compile(parsed_args.target, nodes, parsed_args.resolution, parsed_args.direction, parsed_args.radius, parsed_args.out, ontology)
            print(f"Compiled context to {parsed_args.out}")
            sys.exit(0)

        elif parsed_args.arch_command == "semantic-search":
            from project import ConfigLoader
            from db import Database
            from embedder import LocalEmbedder
            try:
                config_path, db_path = ConfigLoader.find_config(root)
                db = Database(str(db_path))
            except Exception as e:
                print(f"error: failed to load database: {e}", file=sys.stderr)
                sys.exit(1)
            
            embedder = LocalEmbedder()
            query_emb = embedder.embed(parsed_args.query)
            res = db.semantic_vector_search(query_emb, parsed_args.limit)
            print(json.dumps(res, indent=2))
            sys.exit(0)

    @staticmethod
    def handle_semantic_query(root: Path, args: list[str]):
        question = args[0] if args else ""
        graph_path = "graphify-out/graph.json"
        for i, a in enumerate(args):
            if a == "--graph" and i + 1 < len(args):
                graph_path = args[i + 1]
                
        gp = Path(graph_path).resolve()
        if not gp.exists():
            gp = root / "graphify-out" / "graph.json"
            
        if not gp.exists():
            print(f"error: graph file not found: {gp}", file=sys.stderr)
            sys.exit(1)
            
        with open(gp, "r", encoding="utf-8") as f:
            _raw = json.load(f)
            
        nodes = _raw.get("nodes", [])
        idx_to_node = {n.get("arch_vector_idx"): n for n in nodes if "arch_vector_idx" in n}
        
        if not idx_to_node:
            print("error: no nodes found with arch_vector_idx. Did you run reindex/export with embeddings enabled?", file=sys.stderr)
            sys.exit(1)
            
        bin_path = gp.parent / "embeddings.bin"
        if not bin_path.exists():
            print(f"error: embeddings file not found at {bin_path}", file=sys.stderr)
            sys.exit(1)
            
        from embedder import LocalEmbedder
        embedder = LocalEmbedder()
        query_vector = np.array(embedder.embed(question), dtype=np.float32)
        
        with open(bin_path, "rb") as f:
            data = f.read()
            
        vectors = np.frombuffer(data, dtype=np.float32).reshape((len(idx_to_node), -1))
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = vectors / np.clip(norms, a_min=1e-9, a_max=None)
        
        similarities = np.dot(vectors, query_vector)
        top_indices = np.argsort(similarities)[::-1][:10]
        
        results = []
        for idx in top_indices:
            score = float(similarities[idx])
            node = idx_to_node[idx]
            results.append({
                "id": node["id"],
                "label": node.get("label", node["id"]),
                "layer": node.get("arch_layer"),
                "score": score
            })
            
        print(json.dumps(results, indent=2))
        sys.exit(0)


class PluginEmbeddingsManager:
    @staticmethod
    def run_hybrid_embeddings(G, out_dir: Path, ontology):
        from embedder import LocalEmbedder
        from utils import resolve_relative_path
        
        manifest_path = out_dir / "arch_embedding_manifest.json"
        bin_path = out_dir / "embeddings.bin"
        
        prev_manifest = {}
        prev_vectors = None
        dim = 384
        
        if manifest_path.exists() and bin_path.exists():
            try:
                prev_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                prev_vectors = np.fromfile(bin_path, dtype=np.float32)
                dim = prev_manifest.get("dim", 384)
                prev_vectors = prev_vectors.reshape(-1, dim)
            except Exception as e:
                print(f"warning: failed to load previous embeddings manifest: {e}")
                prev_manifest = {}
                prev_vectors = None

        node_ids = list(G.nodes())
        
        def node_content_hash(nid) -> str:
            nd = G.nodes[nid]
            snippet = (nd.get("raw_code") or "")[:200]
            val = f"Name:{nd.get('label', nid)}|Code:{snippet}"
            return hashlib.sha256(val.encode("utf-8")).hexdigest()

        node_hashes = {nid: node_content_hash(nid) for nid in node_ids}
        
        unchanged_nodes = []
        dirty_nodes = []
        
        prev_node_map = prev_manifest.get("nodes", {})
        for nid in node_ids:
            curr_hash = node_hashes[nid]
            if prev_vectors is not None and nid in prev_node_map and prev_node_map[nid].get("hash") == curr_hash:
                unchanged_nodes.append((nid, prev_node_map[nid]["idx"]))
            else:
                dirty_nodes.append(nid)

        dirty_count = len(dirty_nodes)
        total_count = len(node_ids)
        threshold = int(total_count * 0.2) or 10
        
        force_global = dirty_count > threshold or prev_vectors is None or len(prev_vectors) == 0
        
        def build_node_context(nid) -> str:
            nd = G.nodes[nid]
            snippet = (nd.get("raw_code") or "")[:200]
            rel_sf = resolve_relative_path(nd.get("source_file"), out_dir.parent)
            node_fields = ontology.get_fields_for_file(rel_sf)
            meta_parts = []
            for name in node_fields:
                meta_parts.append(f"{name}: {nd.get(f'arch_meta_{name}', '')}")
            meta_str = "\n".join(meta_parts)
            return f"Name: {nd.get('label', nid)}\n{meta_str}\nCode: {snippet}"

        if force_global:
            print(f"[graphify-arch] Rebuilding all embeddings globally ({dirty_count}/{total_count} changed)...")
            try:
                embedder = LocalEmbedder()
                vectors_list = []
                new_nodes_manifest = {}
                for idx, nid in enumerate(node_ids):
                    context_str = build_node_context(nid)
                    vector = embedder.embed(context_str)
                    vectors_list.append(vector)
                    new_nodes_manifest[nid] = {"idx": idx, "hash": node_hashes[nid]}
                    G.nodes[nid]["arch_vector_idx"] = idx
                
                vectors_np = np.array(vectors_list, dtype=np.float32)
                with open(bin_path, "wb") as f:
                    f.write(vectors_np.tobytes())
                
                manifest_data = {
                    "dim": len(vectors_list[0]) if vectors_list else dim,
                    "nodes": new_nodes_manifest
                }
                manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
                print(f"[graphify-arch] Generated {len(vectors_list)} global embeddings.")
            except Exception as e:
                print(f"Skipping semantic embeddings generation: {e}")
        else:
            print(f"[graphify-arch] Generating incremental embeddings patch ({dirty_count} changed, {len(unchanged_nodes)} cached)...")
            try:
                embedder = LocalEmbedder()
                new_vectors = np.zeros((total_count, dim), dtype=np.float32)
                new_nodes_manifest = {}
                
                for nid, prev_idx in unchanged_nodes:
                    new_vectors[len(new_nodes_manifest)] = prev_vectors[prev_idx]
                    G.nodes[nid]["arch_vector_idx"] = len(new_nodes_manifest)
                    new_nodes_manifest[nid] = {"idx": len(new_nodes_manifest), "hash": node_hashes[nid]}
                    
                for nid in dirty_nodes:
                    context_str = build_node_context(nid)
                    vector = embedder.embed(context_str)
                    new_vectors[len(new_nodes_manifest)] = vector
                    G.nodes[nid]["arch_vector_idx"] = len(new_nodes_manifest)
                    new_nodes_manifest[nid] = {"idx": len(new_nodes_manifest), "hash": node_hashes[nid]}
                
                with open(bin_path, "wb") as f:
                    f.write(new_vectors.tobytes())
                
                manifest_data = {
                    "dim": dim,
                    "nodes": new_nodes_manifest
                }
                manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
                print(f"[graphify-arch] Patched {dirty_count} dirty embeddings.")
            except Exception as e:
                print(f"Skipping semantic embeddings generation: {e}")


class PluginReportGenerator:
    @staticmethod
    def _copy_arch_references_to_skill_dir(skill_dir: Path) -> None:
        """Copy arch reference files to a skill directory's references/ folder.
        
        This mirrors graphify's behavior of writing reference files locally
        instead of referencing GitHub URLs.
        """
        import sys
        refs_src = Path(__file__).parent / "references"
        if not refs_src.exists():
            print(f"warning: arch references source not found at {refs_src}", file=sys.stderr)
            return
            
        refs_dst = skill_dir / "references"
        refs_dst.mkdir(parents=True, exist_ok=True)
        
        for ref_file in refs_src.glob("*.md"):
            dst_file = refs_dst / ref_file.name
            try:
                shutil.copy2(ref_file, dst_file)
            except Exception as e:
                print(f"warning: failed to copy reference {ref_file.name} to {refs_dst}: {e}", file=sys.stderr)

    @staticmethod
    def write_always_on_prompts(root_path: Path, G, violations: list, ontology):
        # Update Rule Files (Intercept existing, append if not found)
        rule_files = [
            root_path / ".agents" / "rules" / "graphify.md",
            root_path / "CLAUDE.md",
            root_path / "AGENTS.md",
            root_path / ".cursor/rules/graphify.mdc",
            root_path / ".github/copilot-instructions.md",
            root_path / ".cursorrules",
        ]

        import sys
        for rf in rule_files:
            if not rf.exists():
                continue
                
            try:
                content = rf.read_text(encoding="utf-8")
                
                # Clean up legacy old-style headers if they exist
                for marker in ["## Architecture Constraints (graphify-arch)", "## Architecture (graphify-arch)"]:
                    idx = content.find(marker)
                    if idx != -1:
                        next_idx = content.find("\n## ", idx + len(marker))
                        if next_idx == -1:
                            content = content[:idx].rstrip() + "\n"
                        else:
                            content = content[:idx].rstrip() + "\n" + content[next_idx:].lstrip()

                # Clean up old bullet points if they exist
                old_patterns = [
                    "- This project has strict architectural boundaries defined in `graphify-out/arch/config.json` where HIGHER-tier components must NOT directly depend on LOWER-tier details (e.g. Domain layer calling UI details).",
                    "- Before proposing or making code changes, check for existing architecture violations in `graphify-out/arch/db.json` or via MCP resource `graphify://arch-report`.",
                    "- After modifying code files, run `graphify arch audit` to verify compliance with the architecture rules."
                ]
                for pattern in old_patterns:
                    content = content.replace(pattern + "\n", "").replace(pattern, "")

                # If the new rule is already present, do not duplicate
                target_rule = "- Respect the architectural boundaries defined in `graphify-out/arch/config.json` when proposing or making code changes."
                if target_rule in content:
                    rf.write_text(content, encoding="utf-8")
                    continue

                target_rules = [
                    "- Respect the architectural boundaries defined in `graphify-out/arch/config.json` when proposing or making code changes.",
                    "- Before proposing changes, run `graphify arch audit` to check for existing violations.",
                    "- After making code changes, run `graphify arch audit` to verify compliance.",
                    "- Use `graphify arch compile-context --target <node_id>` to understand the impact of changes on dependent code.",
                ]
                target_rules_text = "\n".join(target_rules)

                lines = content.splitlines()
                rules_header_idx = -1
                for idx, line in enumerate(lines):
                    if "Rules:" in line:
                        rules_header_idx = idx
                        break

                if rules_header_idx == -1:
                    new_content = content.rstrip() + "\n\nRules:\n" + target_rules_text + "\n"
                else:
                    # Find the last bullet point of the list under Rules:
                    last_bullet_idx = -1
                    for i in range(rules_header_idx + 1, len(lines)):
                        stripped = lines[i].strip()
                        if stripped.startswith("-"):
                            last_bullet_idx = i
                        elif stripped and not stripped.startswith("-") and last_bullet_idx != -1:
                            break

                    if last_bullet_idx != -1:
                        lines.insert(last_bullet_idx + 1, target_rules_text)
                        new_content = "\n".join(lines) + "\n"
                    else:
                        lines.insert(rules_header_idx + 1, target_rules_text)
                        new_content = "\n".join(lines) + "\n"

                rf.write_text(new_content, encoding="utf-8")
            except Exception as e:
                print(f"warning: failed to write rules to {rf}: {e}", file=sys.stderr)

        # Update Skill Files
        skill_files = [
            Path.home() / ".gemini" / "config" / "skills" / "graphify" / "SKILL.md",
            Path.home() / ".gemini" / "skills" / "graphify" / "SKILL.md",
            Path.home() / ".claude" / "skills" / "graphify" / "SKILL.md",
            Path.home() / ".codex" / "skills" / "graphify" / "SKILL.md",
            Path.home() / ".config" / "opencode" / "skills" / "graphify" / "SKILL.md",
            Path.home() / ".config" / "kilo" / "skills" / "graphify" / "SKILL.md",
            Path.home() / ".config" / "devin" / "skills" / "graphify" / "SKILL.md",
            Path.home() / ".copilot" / "skills" / "graphify" / "SKILL.md",
            Path.home() / ".openclaw" / "skills" / "graphify" / "SKILL.md",
            Path.home() / ".factory" / "skills" / "graphify" / "SKILL.md",
            Path.home() / ".trae" / "skills" / "graphify" / "SKILL.md",
            Path.home() / ".kiro" / "skills" / "graphify" / "SKILL.md",
            Path.home() / ".pi" / "agent" / "skills" / "graphify" / "SKILL.md",
            Path.home() / ".codebuddy" / "skills" / "graphify" / "SKILL.md",
            Path.home() / ".agents" / "skills" / "graphify" / "SKILL.md",
            root_path / ".agents" / "skills" / "graphify" / "SKILL.md",
            root_path / ".gemini" / "skills" / "graphify" / "SKILL.md",
            root_path / ".devin" / "skills" / "graphify" / "SKILL.md",
            root_path / ".opencode" / "skills" / "graphify" / "SKILL.md",
            root_path / ".codex" / "skills" / "graphify" / "SKILL.md",
            root_path / ".cursor" / "skills" / "graphify" / "SKILL.md",
        ]
        
        skill_section = """
---

## graphify-arch: Architecture Enforcement

### What graphify-arch is for

Enforce architectural rules on a codebase. The AI agent defines layers, tiers, and dependency constraints in `graphify-out/arch/config.json`. The plugin enforces them on every graphify run and reports violations.

### Usage

| Command | Description |
|---------|-------------|
| `graphify arch` | Show help and available commands |
| `graphify arch audit` | Check all files for architectural violations |
| `graphify arch set-status <file> <status> [msg]` | Override compliance status for a file |
| `graphify arch update-component <json-file>` | Update component metadata from JSON |
| `graphify arch discover-ontology` | Print current ontology config |
| `graphify arch query-file --path <file>` | Query nodes for a specific file |
| `graphify arch compile-context --target <id>` | Compile subgraph context for LLM input |
| `graphify arch semantic-search --query <text>` | Vector similarity search over code |
| `graphify arch analyze` | Validate config syntax and consistency |
| `graphify arch setup-embeddings` | Download ONNX embedding model |

### Agent workflow

1. **Analyze the codebase** — understand directory structure, identify natural layers
2. **Generate `graphify-out/arch/config.json`** — define fields, assignment rules, and dependency constraints
3. **Before code changes** — `graphify arch audit` to check for existing violations
4. **After code changes** — `graphify arch audit` to verify compliance
5. **Override intentional violations** — `graphify arch set-status <file> COMPLIANT "reason"`

### References

Full documentation is available in the `references/` folder alongside this skill file:
- `arch-config.md` — full config schema, field types, assignment rules, handlers, examples
- `arch-commands.md` — all CLI commands with arguments and output format
- `arch-audit.md` — interpreting violations and fixing them
- `arch-context.md` — compiling subgraph context for LLM input
"""
        for sf in skill_files:
            if not sf.exists():
                continue
                
            try:
                content = sf.read_text(encoding="utf-8")
                if "graphify-arch: Architecture Enforcement" not in content:
                    new_content = content.rstrip() + "\n\n" + skill_section.strip() + "\n"
                    sf.write_text(new_content, encoding="utf-8")
                
                # Copy arch references to the skill directory
                PluginReportGenerator._copy_arch_references_to_skill_dir(sf.parent)
            except Exception as e:
                print(f"warning: failed to write skill to {sf}: {e}", file=sys.stderr)

    @staticmethod
    def post_env_installation_hook(env_skill_dir: Path, root_path: Path = None) -> None:
        """Post environment installation hook that writes arch instructions.
        
        This hook is called after graphify installs to an environment.
        It writes arch references and instructions only for the environment
        that was just installed.
        
        Args:
            env_skill_dir: Path to the environment's skill directory (e.g., ~/.claude/skills/graphify/)
            root_path: Optional project root path for project-scoped installations
        """
        import sys
        
        if not env_skill_dir.exists():
            return
            
        # Copy arch references to the skill directory
        PluginReportGenerator._copy_arch_references_to_skill_dir(env_skill_dir)
        
        # Write arch instructions to the skill file if it exists
        skill_file = env_skill_dir / "SKILL.md"
        if skill_file.exists():
            try:
                content = skill_file.read_text(encoding="utf-8")
                original_content = content
                
                # Update old reference filenames to new arch- prefixed ones
                old_refs = {
                    "- `config.md`": "- `arch-config.md`",
                    "- `commands.md`": "- `arch-commands.md`",
                    "- `audit.md`": "- `arch-audit.md`",
                    "- `context.md`": "- `arch-context.md`",
                }
                for old, new in old_refs.items():
                    if old in content:
                        content = content.replace(old, new)
                
                if "graphify-arch: Architecture Enforcement" not in content:
                    skill_section = """
---

## graphify-arch: Architecture Enforcement

### What graphify-arch is for

Enforce architectural rules on a codebase. The AI agent defines layers, tiers, and dependency constraints in `graphify-out/arch/config.json`. The plugin enforces them on every graphify run and reports violations.

### Usage

| Command | Description |
|---------|-------------|
| `graphify arch` | Show help and available commands |
| `graphify arch audit` | Check all files for architectural violations |
| `graphify arch set-status <file> <status> [msg]` | Override compliance status for a file |
| `graphify arch update-component <json-file>` | Update component metadata from JSON |
| `graphify arch discover-ontology` | Print current ontology config |
| `graphify arch query-file --path <file>` | Query nodes for a specific file |
| `graphify arch compile-context --target <id>` | Compile subgraph context for LLM input |
| `graphify arch semantic-search --query <text>` | Vector similarity search over code |
| `graphify arch analyze` | Validate config syntax and consistency |
| `graphify arch setup-embeddings` | Download ONNX embedding model |

### Agent workflow

1. **Analyze the codebase** — understand directory structure, identify natural layers
2. **Generate `graphify-out/arch/config.json`** — define fields, assignment rules, and dependency constraints
3. **Before code changes** — `graphify arch audit` to check for existing violations
4. **After code changes** — `graphify arch audit` to verify compliance
5. **Override intentional violations** — `graphify arch set-status <file> COMPLIANT "reason"`

### References

Full documentation is available in the `references/` folder alongside this skill file:
- `arch-config.md` — full config schema, field types, assignment rules, handlers, examples
- `arch-commands.md` — all CLI commands with arguments and output format
- `arch-audit.md` — interpreting violations and fixing them
- `arch-context.md` — compiling subgraph context for LLM input
"""
                    new_content = content.rstrip() + "\n\n" + skill_section.strip() + "\n"
                    skill_file.write_text(new_content, encoding="utf-8")
                    print(f"  arch skill section added to {skill_file}")
                elif content != original_content:
                    skill_file.write_text(content, encoding="utf-8")
                    print(f"  arch references updated in {skill_file}")
            except Exception as e:
                print(f"warning: failed to write arch instructions to {skill_file}: {e}", file=sys.stderr)

    @staticmethod
    def on_graphify_install(env_skill_dir: Path) -> None:
        """Hook that graphify can call after installing to an environment.
        
        This function is called by graphify after it installs to an environment.
        It writes arch references and instructions to the environment's skill directory.
        
        Args:
            env_skill_dir: Path to the environment's skill directory (e.g., ~/.claude/skills/graphify/)
        """
        PluginReportGenerator.post_env_installation_hook(env_skill_dir)

    @staticmethod
    def generate_report(report_text: str, G, communities: dict, root: str, plugin_instance) -> str:
        root_path = Path(root).resolve()
        try:
            ontology = plugin_instance.load_ontology(root_path)
        except Exception:
            return report_text

        violations = audit_architecture_rules(G, ontology, root_path)
        
        lines = [
            "",
            "## Architecture (graphify-arch)",
            ""
        ]
        
        # Collect all unique fields defined in ontology
        all_fields = {}
        for dir_name, dir_cfg in ontology.directories.items():
            for f_name, f_cfg in dir_cfg.manual_fields.items():
                all_fields[f_name] = f_cfg
            for f_name, f_cfg in dir_cfg.automatic_fields.items():
                all_fields[f_name] = f_cfg

        for f_name, f_cfg in sorted(all_fields.items()):
            field_summary = {}
            for node_id, node_data in G.nodes(data=True):
                sf = node_data.get("source_file")
                default_val = f_cfg.default
                if sf:
                    from utils import resolve_relative_path
                    rel_sf = resolve_relative_path(sf, root_path)
                    node_fields = ontology.get_fields_for_file(rel_sf)
                    if f_name in node_fields:
                        default_val = node_fields[f_name].default
                
                val = node_data.get(f"arch_meta_{f_name}", default_val)
                field_summary[str(val)] = field_summary.get(str(val), 0) + 1
                
            total_nodes = len(G.nodes) or 1
            lines += [
                f"### {f_name.replace('_', ' ').capitalize()} Distribution",
                f"| Value | Nodes | % |",
                "| :--- | ---: | ---: |"
            ]
            for val, count in sorted(field_summary.items(), key=lambda x: x[1], reverse=True):
                pct = round(count / total_nodes * 100)
                lines.append(f"| {val} | {count} | {pct}% |")
            lines.append("")
        # Group violations by relative file path using G and violations list
        violated_files = {}
        
        for v in violations:
            from utils import resolve_relative_path
            rel_path = resolve_relative_path(v.filepath, root_path)
            violated_files.setdefault(rel_path, []).append({
                "origin": "automated",
                "rule": v.rule_name,
                "message": v.message,
                "source_node": v.source_id,
                "target_node": v.target_id
            })
            
        for node_id, node_data in G.nodes(data=True):
            sf = node_data.get("source_file")
            if sf:
                from utils import resolve_relative_path
                rel_path = resolve_relative_path(sf, root_path)
                
                manual_violations_str = node_data.get("arch_meta_manual_violations")
                if manual_violations_str:
                    msgs = manual_violations_str.split(" | ")
                    for msg in msgs:
                        existing = violated_files.setdefault(rel_path, [])
                        if not any(x.get("origin") == "manual" and x.get("message") == msg for x in existing):
                            existing.append({
                                "origin": "manual",
                                "rule": "manual_violation",
                                "message": msg
                            })
                            
                manual_status = node_data.get("arch_meta_manual_status")
                if manual_status == "VIOLATION_DETECTED":
                    existing = violated_files.setdefault(rel_path, [])
                    if not any(x.get("origin") == "manual" for x in existing):
                        existing.append({
                            "origin": "manual",
                            "rule": "manual_status_override",
                            "message": "Manual compliance status override set to VIOLATION_DETECTED"
                        })

        lines.append(f"### Violations ({len(violated_files)} files with violations)")
        if not violated_files:
            lines.append("- No architectural violations detected! 🎉")
        else:
            for path, file_violations in sorted(violated_files.items()):
                lines.append(f"- ❌ **{path}**:")
                for v in file_violations:
                    origin = v.get("origin", "automated")
                    rule = v.get("rule", v.get("rule_name", "manual_violation"))
                    msg = v.get("message", "")
                    src = v.get("source_node")
                    tgt = v.get("target_node")
                    if src and tgt:
                        src_file = G.nodes[src].get("source_file") if src in G.nodes else None
                        tgt_file = G.nodes[tgt].get("source_file") if tgt in G.nodes else None
                        if src_file:
                            from utils import resolve_relative_path
                            src_file = resolve_relative_path(src_file, root_path)
                        if tgt_file:
                            from utils import resolve_relative_path
                            tgt_file = resolve_relative_path(tgt_file, root_path)
                        src_display = src_file if src_file else src
                        tgt_display = tgt_file if tgt_file else tgt
                        lines.append(f"  * [{rule} ({origin})] {msg} (`{src_display}` &rarr; `{tgt_display}`)")
                    else:
                        lines.append(f"  * [{rule} ({origin})] {msg}")

        lines.append("")
        lines.append("### Active Architectural Rules")
        for dir_name in ontology.directories:
            fields = ontology.get_fields_for_file(dir_name)
            for name, f_config in fields.items():
                if f_config.handler == "dependency_check":
                    for rule in f_config.rules:
                        lines.append(f"- `[{dir_name}]` `{name}` rule: `{rule.source}` &rarr; `{rule.target}` ({rule.severity})")

        report_text += "\n" + "\n".join(lines)
        
        out_dir = root_path / "graphify-out" / "arch"
        out_dir.mkdir(parents=True, exist_ok=True)
        
        PluginEmbeddingsManager.run_hybrid_embeddings(G, out_dir, ontology)
        PluginReportGenerator.write_always_on_prompts(root_path, G, violations, ontology)

        return report_text
