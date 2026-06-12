import sys
import json
from typing import Any
from pathlib import Path
import numpy as np
from plugins import PluginHookInterface
from db import Violation
from propagator import propagate_purities
from auditor import audit_architecture_rules
from schema import OntologyConfig, FieldConfig, FieldRuleConfig, AssignmentCondition, MetadataAssignmentRule

class ArchPlugin(PluginHookInterface):
    name = "arch"

    def should_activate(self, root: Path) -> bool:
        self.config_path = root / ".graphify" / "arch.json"
        if not self.config_path.exists():
            print("Error: .graphify/arch.json not found. You must write an ontology config in .graphify/arch.json.", file=sys.stderr)
            return False
        return True

    def handle_cli(self, args: list[str]) -> bool:
        """Handles plugin CLI subcommands and returns True if handled."""
        if not args:
            return False
        cmd = args[0]
        if cmd == "arch":
            self._handle_arch_cli(Path.cwd(), args[1:])
            sys.exit(0)
        elif cmd == "query" and "--semantic" in args:
            self._handle_semantic_query(Path.cwd(), args[1:])
            sys.exit(0)
        return False

    def load_ontology(self, root: Path) -> OntologyConfig:
        config_path = root / ".graphify" / "arch.json"
        if not config_path.exists():
            raise FileNotFoundError("Error: .graphify/arch.json not found. You must write an ontology config in .graphify/arch.json.")
            
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        fields = {}
        directories = {}
        for dir_name, fields_data in data.items():
            fields = {}
            for name, f_data in fields_data.items():
                assignment_rules = []
                for rule_data in f_data.get("assignment_rules", []):
                    conds_data = rule_data.get("conditions", {})
                    conds = AssignmentCondition(
                        path_prefix=conds_data.get("path_prefix"),
                        class_suffix=conds_data.get("class_suffix"),
                        class_contains=conds_data.get("class_contains"),
                        name_contains=conds_data.get("name_contains"),
                        imports_prefix=conds_data.get("imports_prefix"),
                        calls_prefix=conds_data.get("calls_prefix")
                    )
                    assignment_rules.append(
                        MetadataAssignmentRule(
                            value=rule_data["value"],
                            conditions=conds
                        )
                    )
                    
                rules = []
                for r in f_data.get("rules", []):
                    rules.append(FieldRuleConfig(
                        source=r["source"],
                        target=r["target"],
                        message=r["message"],
                        severity=r.get("severity", "error")
                    ))
                    
                fields[name] = FieldConfig(
                    values=f_data["values"],
                    default=f_data["default"],
                    reset_on_hash_change=f_data.get("reset_on_hash_change", False),
                    assignment_rules=assignment_rules,
                    handler=f_data.get("handler"),
                    rules=rules,
                    barriers=f_data.get("barriers", []),
                    barrier_field=f_data.get("barrier_field", "architectural_role"),
                    weights=f_data.get("weights", {})
                )
            directories[dir_name] = fields
            
        ontology = OntologyConfig(directories=directories)
        ontology.validate()
        return ontology

    def _handle_arch_cli(self, root: Path, args: list[str]):
        import argparse
        parser = argparse.ArgumentParser(prog="graphify arch")
        subparsers = parser.add_subparsers(dest="arch_command", required=True)
        
        audit_parser = subparsers.add_parser("audit", help="Audit architectural graph structures")
        audit_parser.add_argument("--graph", default="graphify-out/graph.json", help="Path to graph.json")
        
        subparsers.add_parser("setup-embeddings", help="Download ONNX embedding model")
        subparsers.add_parser("analyze", help="Analyze configuration completeness and sanity")
        
        set_status_parser = subparsers.add_parser("set-status", help="Set manual compliance status for a component")
        set_status_parser.add_argument("file_path", help="Relative file path to the component")
        set_status_parser.add_argument("status", choices=["COMPLIANT", "VIOLATION_DETECTED"], help="Compliance status")
        set_status_parser.add_argument("violations", nargs="?", default="", help="Description of violations")
        
        set_status_bulk_parser = subparsers.add_parser("set-status-bulk", help="Set manual compliance status for multiple components via a JSON file")
        set_status_bulk_parser.add_argument("json_file", help="Path to the JSON file containing status changes")

        update_bulk_parser = subparsers.add_parser("update-bulk", help="Set manual compliance status for multiple components via a JSON file (alias of set-status-bulk)")
        update_bulk_parser.add_argument("json_file", help="Path to the JSON file containing status changes")
        
        parsed_args = parser.parse_args(args)
        
        if parsed_args.arch_command == "set-status":
            file_path = Path(parsed_args.file_path).as_posix()
            status = parsed_args.status
            violations_msg = parsed_args.violations or ""
            
            # Load and update arch_report.json
            report_path = root / "graphify-out" / "arch_report.json"
            if not report_path.exists():
                print(f"error: arch_report.json not found at {report_path}", file=sys.stderr)
                sys.exit(1)
                
            try:
                report_data = json.loads(report_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"error: failed to load arch_report.json: {e}", file=sys.stderr)
                sys.exit(1)
                
            components = report_data.setdefault("components", {})
            if file_path not in components:
                matched_key = None
                for k in components:
                    if k.endswith(file_path) or file_path.endswith(k):
                        matched_key = k
                        break
                if matched_key:
                    file_path = matched_key
                else:
                    components[file_path] = {}
            
            components[file_path]["manual_status"] = status
            components[file_path]["manual_violations"] = violations_msg
            
            try:
                report_path.write_text(json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                print(f"error: failed to write arch_report.json: {e}", file=sys.stderr)
                sys.exit(1)
                
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
                            if rel_sf == file_path:
                                node["arch_meta_manual_status"] = status
                                node["arch_meta_manual_violations"] = violations_msg
                                modified = True
                    if modified:
                        graph_path.write_text(json.dumps(graph_data, ensure_ascii=False), encoding="utf-8")
                except Exception as e:
                    print(f"warning: failed to update graph.json: {e}", file=sys.stderr)
                    
            print(f"Set manual status for '{file_path}' to {status}")
            sys.exit(0)
            
        elif parsed_args.arch_command in ["set-status-bulk", "update-bulk"]:
            json_file_path = Path(parsed_args.json_file).resolve()
            if not json_file_path.exists():
                print(f"error: JSON file not found at {json_file_path}", file=sys.stderr)
                sys.exit(1)
                
            try:
                bulk_data = json.loads(json_file_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"error: failed to load JSON file: {e}", file=sys.stderr)
                sys.exit(1)
                
            if not isinstance(bulk_data, dict):
                print("error: bulk update JSON must be a dictionary object", file=sys.stderr)
                sys.exit(1)
                
            # Load and update arch_report.json
            report_path = root / "graphify-out" / "arch_report.json"
            if not report_path.exists():
                print(f"error: arch_report.json not found at {report_path}", file=sys.stderr)
                sys.exit(1)
                
            try:
                report_data = json.loads(report_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"error: failed to load arch_report.json: {e}", file=sys.stderr)
                sys.exit(1)
                
            components = report_data.setdefault("components", {})
            
            status_updates = {}
            for raw_key, entry_data in bulk_data.items():
                if not isinstance(entry_data, dict):
                    print(f"warning: entry '{raw_key}' is not a dictionary, skipping.", file=sys.stderr)
                    continue
                
                status = (entry_data.get("status") or "").upper()
                if status not in ["COMPLIANT", "VIOLATION_DETECTED", "PENDING_AUDIT"]:
                    print(f"warning: invalid status '{status}' for '{raw_key}', skipping.", file=sys.stderr)
                    continue
                
                violations_raw = entry_data.get("violations", "")
                if isinstance(violations_raw, list):
                    violations_msg = " | ".join(str(v) for v in violations_raw)
                elif isinstance(violations_raw, str):
                    violations_msg = violations_raw
                else:
                    violations_msg = str(violations_raw) if violations_raw is not None else ""
                
                file_path = Path(raw_key).as_posix()
                if file_path.startswith("./"):
                    file_path = file_path[2:]
                if not file_path or file_path == ".":
                    continue
                
                matched_key = None
                for k in components:
                    if k.endswith(file_path) or file_path.endswith(k):
                        matched_key = k
                        break
                if matched_key:
                    file_path = matched_key
                else:
                    components[file_path] = {}
                    
                components[file_path]["manual_status"] = status
                components[file_path]["manual_violations"] = violations_msg
                status_updates[file_path] = (status, violations_msg)
            
            try:
                report_path.write_text(json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                print(f"error: failed to write arch_report.json: {e}", file=sys.stderr)
                sys.exit(1)
                
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
                            
                            matched_update_key = None
                            for update_key in status_updates:
                                if rel_sf == update_key or rel_sf.endswith(update_key) or update_key.endswith(rel_sf):
                                    matched_update_key = update_key
                                    break
                            
                            if matched_update_key:
                                status, violations_msg = status_updates[matched_update_key]
                                node["arch_meta_manual_status"] = status
                                node["arch_meta_manual_violations"] = violations_msg
                                modified = True
                    if modified:
                        graph_path.write_text(json.dumps(graph_data, ensure_ascii=False), encoding="utf-8")
                except Exception as e:
                    print(f"warning: failed to update graph.json: {e}", file=sys.stderr)
                    
            print(f"Successfully bulk-updated {len(status_updates)} status overrides.")
            sys.exit(0)
            
        elif parsed_args.arch_command == "audit":
            gp = Path(parsed_args.graph).resolve()
            if not gp.exists():
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
                G = json_graph.node_link_graph(_raw, edges="links")
            except TypeError:
                G = json_graph.node_link_graph(_raw)
                
            ontology = self.load_ontology(root)
            violations = audit_architecture_rules(G, ontology, root)
            analysis = {"violations": [
                {
                    "rule": v.rule_name,
                    "message": v.message,
                    "source_node": v.source_id,
                    "target_node": v.target_id,
                    "source_file": v.filepath,
                }
                for v in violations
            ]}
            print(json.dumps(analysis, indent=2))
            sys.exit(0)
            
        elif parsed_args.arch_command == "setup-embeddings":
            from embedder import LocalEmbedder
            embedder = LocalEmbedder()
            print("Embeddings setup completed successfully.")
            sys.exit(0)
            
        elif parsed_args.arch_command == "analyze":
            try:
                ontology = self.load_ontology(root)
            except Exception as e:
                print(f"Failed to load ontology: {e}", file=sys.stderr)
                sys.exit(1)
                
            print("=== Configuration Completeness Analysis ===")
            
            # 1. Check unreachable allowed values
            unreachable = []
            for dir_name, fields in ontology.directories.items():
                for name, f_config in fields.items():
                    assigned = {rule.value for rule in f_config.assignment_rules}
                    for val in f_config.values:
                        if val != f_config.default and val not in assigned:
                            unreachable.append((dir_name, name, val))
                            
            if unreachable:
                print("\n[WARNING] Unreachable allowed values detected:")
                for dir_name, name, val in unreachable:
                    print(f"  - Directory '{dir_name}' Field '{name}': Value '{val}' is allowed but never assigned by rules and is not the default.")
            else:
                print("\n[OK] All allowed values are reachable via assignment rules or defaults.")
                
            # 2. Check referential integrity
            errors = []
            for dir_name, fields in ontology.directories.items():
                for name, f_config in fields.items():
                    for r in f_config.rules:
                        if r.source not in f_config.values:
                            errors.append(f"Directory '{dir_name}' Field '{name}' rule source '{r.source}' is not in values list {f_config.values}.")
                        if r.target not in f_config.values:
                            errors.append(f"Directory '{dir_name}' Field '{name}' rule target '{r.target}' is not in values list {f_config.values}.")
                    for b in f_config.barriers:
                        b_field = fields.get(f_config.barrier_field)
                        if b_field and b not in b_field.values:
                            errors.append(f"Directory '{dir_name}' Field '{name}' barrier '{b}' is not in allowed values of barrier_field '{f_config.barrier_field}' ({b_field.values}).")
            
            if errors:
                print("\n[ERROR] Referential integrity failures:")
                for err in errors:
                    print(f"  - {err}")
                sys.exit(1)
            else:
                print("[OK] Referential integrity validation passed.")
            sys.exit(0)

    def _handle_semantic_query(self, root: Path, args: list[str]):
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

    def _assign_edge_weights(self, G, ontology, root: Path):
        """Inflate/deflate edge weights based on architectural relationships."""
        all_barriers = set()
        for dir_name, fields in ontology.directories.items():
            for p_name, cfg in fields.items():
                if cfg.handler == "propagation":
                    all_barriers.update(cfg.barriers)
                    
        for u, v, data in G.edges(data=True):
            weight = 1.0
            
            u_sf = G.nodes[u].get("source_file") or ""
            if u_sf:
                try:
                    rel_u_sf = Path(u_sf).relative_to(root).as_posix()
                except ValueError:
                    rel_u_sf = Path(u_sf).as_posix()
            else:
                rel_u_sf = ""
            u_fields_cfg = ontology.get_fields_for_file(rel_u_sf)
            
            layer_field = u_fields_cfg.get("layer")
            tier_field = u_fields_cfg.get("tier")
            
            # 1. Same-layer edges get higher weight
            if layer_field:
                u_layer = G.nodes[u].get("arch_meta_layer", layer_field.default)
                v_layer = G.nodes[v].get("arch_meta_layer", layer_field.default)
                if u_layer and v_layer and u_layer == v_layer:
                    weight *= 1.5
                    
            # 2. Cross-tier violations get penalty weight
            if tier_field:
                u_tier = G.nodes[u].get("arch_meta_tier", tier_field.default)
                v_tier = G.nodes[v].get("arch_meta_tier", tier_field.default)
                if u_tier is not None and v_tier is not None:
                    try:
                        if int(u_tier) > int(v_tier):
                            weight *= 0.5
                    except ValueError:
                        pass
                        
            # 3. Barrier-crossing edges get low weight
            u_role = G.nodes[u].get("arch_meta_architectural_role", "")
            if u_role in all_barriers:
                weight *= 0.3
                
            data["weight"] = weight

    def on_post_build(self, G, extraction: dict, root: Path):
        try:
            ontology = self.load_ontology(root)
        except Exception:
            return G

        try:
            # Check for previous components to detect schema drift / value deletion
            prev_components = {}
            report_path = root / "graphify-out" / "arch_report.json"
            if report_path.exists():
                try:
                    prev_report = json.loads(report_path.read_text(encoding="utf-8"))
                    prev_components = prev_report.get("components", {})
                except Exception:
                    pass

            manifest_path = root / "graphify-out" / "arch_embedding_manifest.json"
            prev_manifest = {}
            if manifest_path.exists():
                try:
                    prev_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

            # Helper to check prefix matching including package: imports/calls
            def matches_prefix(target_str: str, prefix: str) -> bool:
                if target_str.startswith(prefix):
                    return True
                if target_str.startswith("package:") and prefix.startswith("lib/"):
                    parts = target_str.split("/", 1)
                    if len(parts) > 1:
                        target_pkg_rel = parts[1]
                        prefix_rel = prefix[4:]
                        if target_pkg_rel.startswith(prefix_rel):
                            return True
                return False

            outgoing_imports = {}
            outgoing_calls = {}
            edges_fn = G.out_edges if hasattr(G, "out_edges") else G.edges
            for u in G.nodes:
                outgoing_imports[u] = []
                outgoing_calls[u] = []
                for _, v, edge_data in edges_fn(u, data=True):
                    rel = edge_data.get("type") or edge_data.get("relation") or ""
                    rel_lower = rel.lower()
                    if "import" in rel_lower:
                        outgoing_imports[u].append(v)
                    elif "call" in rel_lower or "use" in rel_lower or "reference" in rel_lower:
                        outgoing_calls[u].append(v)

            for node_id in G.nodes:
                data = G.nodes[node_id]
                source_file = data.get("source_file") or ""
                node_label = data.get("label") or ""
                
                # Normalize source_file to relative posix path for matching
                if source_file:
                    try:
                        rel_source_file = Path(source_file).relative_to(root).as_posix()
                    except ValueError:
                        rel_source_file = Path(source_file).as_posix()
                else:
                    rel_source_file = ""
                
                # Compute current hash for reset check
                snippet = (data.get("raw_code") or "")[:200]
                curr_val = f"Name:{node_label}|Code:{snippet}"
                import hashlib
                curr_hash = hashlib.sha256(curr_val.encode("utf-8")).hexdigest()
                
                hash_changed = False
                prev_node_manifest = prev_manifest.get("nodes", {}).get(node_id)
                if prev_node_manifest and prev_node_manifest.get("hash") != curr_hash:
                    hash_changed = True

                node_prev_comp = prev_components.get(rel_source_file, {})

                fields_cfg = ontology.get_fields_for_file(rel_source_file)
                for field_name, field_config in fields_cfg.items():
                    # Check schema drift from previous output
                    prev_val = node_prev_comp.get(field_name)
                    if prev_val is not None and prev_val not in field_config.values:
                        print(f"warning: value '{prev_val}' for field '{field_name}' in '{rel_source_file}' is no longer allowed. Resetting to default '{field_config.default}' and marking for audit.", file=sys.stderr)
                        data[f"arch_meta_{field_name}"] = field_config.default
                        data["arch_meta_requires_audit"] = True
                        continue

                    # Strict assignment rules
                    assigned_value = field_config.default
                    matched = False
                    for rule in field_config.assignment_rules:
                        conds = rule.conditions
                        match = True
                        
                        if conds.path_prefix is not None:
                            if not rel_source_file.startswith(conds.path_prefix):
                                match = False
                        
                        if conds.class_suffix is not None:
                            if not node_label.endswith(conds.class_suffix):
                                match = False
                                
                        if conds.class_contains is not None:
                            if conds.class_contains not in node_label:
                                match = False
                                
                        if conds.name_contains is not None:
                            if conds.name_contains not in node_label:
                                match = False
                                
                        if conds.imports_prefix is not None:
                            imports = outgoing_imports.get(node_id, [])
                            if not any(matches_prefix(imp, conds.imports_prefix) or matches_prefix(G.nodes[imp].get("label") or "", conds.imports_prefix) for imp in imports):
                                match = False
                                
                        if conds.calls_prefix is not None:
                            calls = outgoing_calls.get(node_id, [])
                            if not any(matches_prefix(call, conds.calls_prefix) or matches_prefix(G.nodes[call].get("label") or "", conds.calls_prefix) for call in calls):
                                match = False
                                
                        if match:
                            assigned_value = rule.value
                            matched = True
                            break
                            
                    # Handle reset on hash change
                    if hash_changed and field_config.reset_on_hash_change:
                        if not field_config.assignment_rules or not matched:
                            assigned_value = field_config.default
                            data["arch_meta_requires_audit"] = True
                            
                    data[f"arch_meta_{field_name}"] = assigned_value

                # Preserve manual_status and manual_violations from prev_components if exists
                prev_manual_status = node_prev_comp.get("manual_status")
                prev_manual_violations = node_prev_comp.get("manual_violations")
                if prev_manual_status is not None:
                    data["arch_meta_manual_status"] = prev_manual_status
                if prev_manual_violations is not None:
                    data["arch_meta_manual_violations"] = prev_manual_violations

            G = propagate_purities(G, ontology, root)
            self._assign_edge_weights(G, ontology, root)
            return G
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise e

    def on_post_analyze(self, G, communities: dict, analysis: dict, root: Path) -> dict:
        try:
            ontology = self.load_ontology(root)
        except Exception:
            return analysis

        violations = audit_architecture_rules(G, ontology, root)
        
        analysis["arch_violations"] = [
            {
                "rule": v.rule_name,
                "message": v.message,
                "source_node": v.source_id,
                "target_node": v.target_id,
                "source_file": v.filepath,
            }
            for v in violations
        ]
        
        # Inject violations as surprises
        for v in violations:
            analysis.setdefault("surprises", []).append({
                "source": v.source_id,
                "target": v.target_id,
                "relation": "arch_violation",
                "note": f"[ARCH] {v.message}",
                "confidence": "EXTRACTED",
                "source_files": [v.filepath, ""],
            })
            
        return analysis

    def on_report(self, report_text: str, G, communities: dict, root: str) -> str:
        root_path = Path(root).resolve()
        try:
            ontology = self.load_ontology(root_path)
        except Exception:
            return report_text

        violations = audit_architecture_rules(G, ontology, root_path)
        
        # 1. Compile layer distributions (if layer exists in any directory config)
        lines = [
            "",
            "## Architecture (graphify-arch)",
            ""
        ]
        
        has_layer = False
        layer_defaults = {}
        for dir_name, fields in ontology.directories.items():
            if "layer" in fields:
                has_layer = True
                layer_defaults[dir_name] = fields["layer"].default
                
        if has_layer:
            layer_summary = {}
            for node_id in G.nodes:
                data = G.nodes[node_id]
                sf = data.get("source_file") or ""
                if sf:
                    try:
                        rel_sf = Path(sf).relative_to(root_path).as_posix()
                    except ValueError:
                        rel_sf = Path(sf).as_posix()
                else:
                    rel_sf = ""
                node_fields = ontology.get_fields_for_file(rel_sf)
                default_layer = node_fields.get("layer").default if "layer" in node_fields else "Domain"
                layer = data.get("arch_meta_layer", default_layer)
                layer_summary[layer] = layer_summary.get(layer, 0) + 1
            
            total_nodes = len(G.nodes) or 1
            lines += [
                "### Layer Distribution",
                "| Layer | Nodes | % |",
                "| :--- | ---: | ---: |"
            ]
            for layer, count in sorted(layer_summary.items(), key=lambda x: x[1], reverse=True):
                pct = round(count / total_nodes * 100)
                lines.append(f"| {layer} | {count} | {pct}% |")
            lines.append("")

        # Compile purity (if propagation exists in any directory config)
        propagation_fields = set()
        for dir_name, fields in ontology.directories.items():
            for p_name, cfg in fields.items():
                if cfg.handler == "propagation":
                    propagation_fields.add(p_name)
                    
        for p_name in sorted(propagation_fields):
            purity_summary = {}
            for node_id in G.nodes:
                data = G.nodes[node_id]
                sf = data.get("source_file") or ""
                if sf:
                    try:
                        rel_sf = Path(sf).relative_to(root_path).as_posix()
                    except ValueError:
                        rel_sf = Path(sf).as_posix()
                else:
                    rel_sf = ""
                node_fields = ontology.get_fields_for_file(rel_sf)
                default_val = node_fields.get(p_name).default if p_name in node_fields else "Unknown"
                val = data.get(f"arch_meta_{p_name}", default_val)
                purity_summary[val] = purity_summary.get(val, 0) + 1
            purity_str = " · ".join(f"{val}: {count} node(s)" for val, count in sorted(purity_summary.items(), key=lambda x: x[1], reverse=True))
            lines.append(f"### {p_name.capitalize()} Summary")
            lines.append(f"- {purity_str}")
            lines.append("")

        lines.append(f"### Violations ({len(violations)} detected)")
        if not violations:
            lines.append("- No architectural violations detected! 🎉")
        else:
            for idx, v in enumerate(violations, start=1):
                lines.append(f"- ❌ **{v.rule_name}**: `{v.source_id}` &rarr; `{v.target_id}`")
                lines.append(f"  * Message: {v.message}")
                lines.append(f"  * Location: `{v.filepath}`")

        lines.append("")
        lines.append("### Active Architectural Rules")
        for dir_name, fields in ontology.directories.items():
            for name, f_config in fields.items():
                if f_config.handler == "dependency_check":
                    for rule in f_config.rules:
                        lines.append(f"- `[{dir_name}]` `{name}` rule: `{rule.source}` &rarr; `{rule.target}` ({rule.severity})")

        report_text += "\n" + "\n".join(lines)
        
        out_dir = root_path / "graphify-out"
        out_dir.mkdir(parents=True, exist_ok=True)
        
        # Write arch_audit.md
        audit_report_path = out_dir / "arch_audit.md"
        audit_report_path.write_text("\n".join(lines), encoding="utf-8")
        
        # Compile arch_report.json
        components = {}
        files_to_nodes = {}
        for node_id, data in G.nodes(data=True):
            source_file = data.get("source_file") or ""
            if not source_file:
                continue
            try:
                rel_path = Path(source_file).relative_to(root_path).as_posix()
            except ValueError:
                rel_path = Path(source_file).as_posix()
            files_to_nodes.setdefault(rel_path, []).append((node_id, data))

        import hashlib
        def get_file_sha256(rel_p: str) -> str:
            full_p = root_path / rel_p
            if not full_p.exists():
                return ""
            try:
                return hashlib.sha256(full_p.read_bytes()).hexdigest()
            except Exception:
                return ""

        def find_test_file(rel_p: str) -> str:
            p = Path(rel_p)
            if rel_p.startswith("lib/"):
                test_path = Path("test") / p.relative_to("lib").with_name(p.stem + "_test" + p.suffix)
                return test_path.as_posix()
            elif rel_p.startswith("src/"):
                test_path = Path("tests") / p.relative_to("src").with_name(p.stem + "_test" + p.suffix)
                return test_path.as_posix()
            return ""

        def get_prioritized_meta(node_list: list, field_name: str, default_val: Any) -> Any:
            best_val = default_val
            for _, data in node_list:
                val = data.get(f"arch_meta_{field_name}")
                if val is not None:
                    if val != default_val:
                        return val
                    best_val = val
            return best_val

        prev_components = {}
        report_path = root_path / "graphify-out" / "arch_report.json"
        if report_path.exists():
            try:
                prev_report = json.loads(report_path.read_text(encoding="utf-8"))
                prev_components = prev_report.get("components", {})
            except Exception:
                pass

        for rel_path, node_list in files_to_nodes.items():
            main_class = ""
            for node_id, data in node_list:
                label = data.get("label", "")
                type_val = data.get("type", "") or data.get("file_type", "") or ""
                if any(kw in type_val.lower() for kw in ["class", "struct", "trait", "enum"]):
                    main_class = label
                    break
            if not main_class and node_list:
                main_class = Path(rel_path).stem.capitalize()

            sha256_val = get_file_sha256(rel_path)

            comp_violations = []
            for v in violations:
                if rel_path in str(v.filepath) or any(v.source_id == nid for nid, _ in node_list):
                    comp_violations.append(v.message)

            status = "VIOLATION_DETECTED" if comp_violations else "COMPLIANT"

            meta_assigned = {}
            fields_cfg = ontology.get_fields_for_file(rel_path)
            for field_name, field_config in fields_cfg.items():
                meta_assigned[field_name] = get_prioritized_meta(node_list, field_name, field_config.default)

            comp_imports = []
            for node_id, _ in node_list:
                edges_fn = G.out_edges if hasattr(G, "out_edges") else G.edges
                for _, v, edge_data in edges_fn(node_id, data=True):
                    rel = edge_data.get("type") or edge_data.get("relation") or ""
                    if "import" in rel.lower():
                        target_label = G.nodes[v].get("label") or v
                        if target_label not in comp_imports:
                            comp_imports.append(target_label)

            test_file_path = find_test_file(rel_path)
            has_tests = (root_path / test_file_path).exists() if test_file_path else False

            public_apis = []
            for node_id, data in node_list:
                type_val = data.get("type") or ""
                label = data.get("label") or ""
                if any(kw in type_val.lower() for kw in ["function", "method"]) and not label.startswith("_"):
                    public_apis.append(label)

            node_prev_comp = prev_components.get(rel_path, {})
            manual_status = get_prioritized_meta(node_list, "manual_status", None)
            manual_violations = get_prioritized_meta(node_list, "manual_violations", None)
            if manual_status is None:
                manual_status = node_prev_comp.get("manual_status") if node_prev_comp else None
            if manual_violations is None:
                manual_violations = node_prev_comp.get("manual_violations") if node_prev_comp else None

            components[rel_path] = {
                "class": main_class,
                "sha256": sha256_val,
                "status": status,
                "violations": comp_violations,
                "imports": comp_imports,
                "test_file": test_file_path,
                "has_tests": has_tests,
                "public_apis": public_apis,
                **meta_assigned
            }
            if manual_status is not None:
                components[rel_path]["manual_status"] = manual_status
            if manual_violations is not None:
                components[rel_path]["manual_violations"] = manual_violations

        import subprocess
        def get_git_commit(r: Path) -> str:
            try:
                res = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    cwd=str(r),
                    check=True
                )
                return res.stdout.strip()
            except Exception:
                return "unknown"

        from datetime import datetime, timezone
        last_audit_commit = get_git_commit(root_path)
        last_audit_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # Compile layers meta from config
        report_json = {
            "last_audit_commit": last_audit_commit,
            "last_audit_time": last_audit_time,
            "components": components
        }

        report_json_path = out_dir / "arch_report.json"
        with open(report_json_path, "w", encoding="utf-8") as f:
            json.dump(report_json, f, indent=2)

        self._run_hybrid_embeddings(G, out_dir, ontology)
        self._write_always_on_prompts(root_path, violations, ontology)

        return report_text

    def _run_hybrid_embeddings(self, G, out_dir: Path, ontology):
        from embedder import LocalEmbedder
        import hashlib
        
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
        
        # Build embedding context string containing values of all field metadata
        def build_node_context(nid) -> str:
            nd = G.nodes[nid]
            snippet = (nd.get("raw_code") or "")[:200]
            sf = nd.get("source_file") or ""
            if sf:
                try:
                    rel_sf = Path(sf).relative_to(out_dir.parent).as_posix()
                except ValueError:
                    rel_sf = Path(sf).as_posix()
            else:
                rel_sf = ""
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

    def _write_always_on_prompts(self, root_path: Path, violations: list, ontology):
        """Write architecture steering guidelines to various platform directory settings."""
        rules_dir = root_path / ".gemini"
        rules_dir.mkdir(exist_ok=True)
        rules_file = rules_dir / "antigravity-rules.md"
        
        violations_bullets = "\n".join(f"- ❌ **{v.rule_name}**: {v.message}" for v in violations[:3])
        if violations:
            violations_bullets = "Current violations active:\n" + violations_bullets
        else:
            violations_bullets = "No active violations currently."

        rules_content = f"""---
trigger: always_on
description: Consult the architecture guidelines at graphify-out/arch_audit.md before proposing code changes.
---

## Architecture Constraints (graphify-arch)

This project has strict architectural boundaries defined in `.graphify/arch.json`.

Rules:
- HIGHER-tier components must NOT directly depend on LOWER-tier details (e.g. Domain layer calling UI details).
- Read `graphify-out/arch_audit.md` or MCP resource `graphify://arch-report` for details.
- After modifying code files in this session, run `graphify update .` to sync changes and verify architecture compliance.

{violations_bullets}
"""
        try:
            rules_file.write_text(rules_content, encoding="utf-8")
        except Exception:
            pass
