import sys
import json
from pathlib import Path
import networkx as nx

from plugins import PluginHookInterface
from db import Violation, Database
from propagator import propagate_metadata
from auditor import audit_architecture_rules
from schema import OntologyConfig, FieldConfig, FieldRuleConfig, AssignmentCondition, MetadataAssignmentRule
from utils import resolve_relative_path

# Import delegated helper handlers to satisfy SRP
from plugin_helpers import PluginCLIHandler, PluginEmbeddingsManager, PluginReportGenerator

class ArchPlugin(PluginHookInterface):
    name = "arch"

    def should_activate(self, root) -> bool:
        root = Path(root)
        from project import ConfigLoader
        try:
            self.config_path, self.db_path = ConfigLoader.find_config(root)
        except Exception:
            self.config_path = root / "graphify-out" / "arch" / "config.json"
            self.db_path = root / "graphify-out" / "arch" / "graph.db"
        return True

    def handle_cli(self, args: list[str]) -> bool:
        """Handles plugin CLI subcommands and returns True if handled."""
        if not args:
            return False
        cmd = args[0]
        if cmd == "arch":
            if not args[1:]:
                PluginCLIHandler.print_arch_help()
                sys.exit(0)
            if args[1] == "install":
                PluginCLIHandler.handle_arch_cli(Path.cwd(), args[1:], self)
                sys.exit(0)
            if not self.config_path.exists():
                print("warning: graphify-out/arch/config.json not found. The AI agent should create it, or write an ontology config manually.", file=sys.stderr)
                sys.exit(1)
            PluginCLIHandler.handle_arch_cli(Path.cwd(), args[1:], self)
            sys.exit(0)
        elif cmd == "query" and "--semantic" in args:
            PluginCLIHandler.handle_semantic_query(Path.cwd(), args[1:])
            sys.exit(0)
        return False

    def load_ontology(self, root: Path) -> OntologyConfig:
        from project import ConfigLoader
        config_path, _ = ConfigLoader.find_config(root)
        if not config_path.exists():
            raise FileNotFoundError("Error: Ontology configuration file (graphify-out/arch/config.json) not found.")
        return ConfigLoader.load(config_path).ontology

    def _assign_edge_weights(self, G, ontology, root: Path):
        """Inflate/deflate edge weights based on architectural relationships."""
        all_barriers = set()
        for dir_name, dir_cfg in ontology.directories.items():
            all_fields = {}
            all_fields.update(dir_cfg.manual_fields)
            all_fields.update(dir_cfg.automatic_fields)
            for p_name, cfg in all_fields.items():
                if cfg.handler == "propagation":
                    all_barriers.update(cfg.barriers)
                    
        for u, v, data in G.edges(data=True):
            weight = 1.0
            
            rel_u_sf = resolve_relative_path(G.nodes[u].get("source_file"), root)
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

    def on_post_build(self, G, extraction: dict, root):
        root = Path(root)
        try:
            ontology = self.load_ontology(root)
        except Exception:
            return G

        try:
            # Resolve Database path and load
            try:
                db = Database(str(self.db_path))
            except Exception:
                db = None

            # Load Graphify's manifest.json to detect AST changes
            manifest_path = root / "graphify-out" / "manifest.json"
            manifest_data = {}
            if manifest_path.exists():
                try:
                    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

            modified_files = set()
            if db:
                try:
                    # Find all unique source files in the graph
                    graph_files = set()
                    for node_id in G.nodes:
                        sf = G.nodes[node_id].get("source_file")
                        if sf:
                            graph_files.add(resolve_relative_path(sf, root))

                    for rel_file in graph_files:
                        manifest_entry = manifest_data.get(rel_file)
                        curr_ast_hash = manifest_entry.get("ast_hash") if manifest_entry else None

                        comp = db.get_component(rel_file)
                        if comp:
                            prev_ast_hash = comp.get("ast_hash")
                            if curr_ast_hash and prev_ast_hash != curr_ast_hash:
                                modified_files.add(rel_file)

                                existing_violations = comp.get("violations") or []
                                filtered_violations = [v for v in existing_violations if v.get("origin") != "manual"]

                                manual_cfg = ontology.get_manual_fields_for_file(rel_file)
                                manual_fields = comp.get("manual_fields") or {}
                                new_manual_fields = {}
                                for field_name, val in manual_fields.items():
                                    field_config = manual_cfg.get(field_name)
                                    if field_config and field_config.reset_on_change:
                                        pass
                                    else:
                                        new_manual_fields[field_name] = val

                                db.save_component(rel_file, "PENDING_AUDIT", None, new_manual_fields, filtered_violations)
                                cursor = db.conn.cursor()
                                cursor.execute("UPDATE components SET ast_hash = ? WHERE filepath = ?", (curr_ast_hash, rel_file))
                                db.conn.commit()
                            elif curr_ast_hash and not prev_ast_hash:
                                cursor = db.conn.cursor()
                                cursor.execute("UPDATE components SET ast_hash = ? WHERE filepath = ?", (curr_ast_hash, rel_file))
                                db.conn.commit()
                        else:
                            db.save_component(rel_file, "PENDING_AUDIT", None, {}, [])
                            cursor = db.conn.cursor()
                            cursor.execute("UPDATE components SET ast_hash = ? WHERE filepath = ?", (curr_ast_hash, rel_file))
                            db.conn.commit()
                except Exception as e:
                    print(f"warning: failed to sync file-level changes: {e}", file=sys.stderr)

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
                rel_source_file = resolve_relative_path(source_file, root)
                
                hash_changed = rel_source_file in modified_files

                comp_info = db.get_component(rel_source_file) if db else None

                # 1. Process manual_fields (User-guided overrides or defaults)
                manual_cfg = ontology.get_manual_fields_for_file(rel_source_file)
                for field_name, field_config in manual_cfg.items():
                    val = comp_info.get("manual_fields", {}).get(field_name) if comp_info else None
                    if val is None:
                        val = field_config.default
                        
                    # Check schema drift from manual overrides
                    if field_config.values is not None and val not in field_config.values:
                        print(f"warning: value '{val}' for manual field '{field_name}' in '{rel_source_file}' is no longer allowed. Resetting to default '{field_config.default}' and marking for audit.", file=sys.stderr)
                        val = field_config.default
                        data["arch_meta_requires_audit"] = True
                        
                    data[f"arch_meta_{field_name}"] = val

                # 2. Process automatic_fields (Rule-injected metadata fields)
                automatic_cfg = ontology.get_automatic_fields_for_file(rel_source_file)
                for field_name, field_config in automatic_cfg.items():
                    # Check schema drift from previous AST database node value
                    prev_node = db.get_node(node_id) if db else None
                    prev_val = prev_node.semantics.fields.get(field_name) if prev_node else None
                    
                    if prev_val is not None and field_config.values is not None and prev_val not in field_config.values:
                        print(f"warning: value '{prev_val}' for automatic field '{field_name}' in '{rel_source_file}' is no longer allowed. Resetting to default '{field_config.default}' and marking for audit.", file=sys.stderr)
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
                        
                        if conds.file_name is not None:
                            if Path(rel_source_file).name != conds.file_name:
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
                    if hash_changed and field_config.reset_on_change:
                        if not field_config.assignment_rules or not matched:
                            assigned_value = field_config.default
                            data["arch_meta_requires_audit"] = True
                            
                    data[f"arch_meta_{field_name}"] = assigned_value

                # Preserve manual_status and manual_violations from DB
                if comp_info:
                    manual_status = comp_info.get("manual_status")
                    if manual_status is not None:
                        data["arch_meta_manual_status"] = manual_status
                    
                    manual_v_msgs = [v["message"] for v in comp_info.get("violations", []) if v.get("origin") == "manual"]
                    if manual_v_msgs:
                        data["arch_meta_manual_violations"] = " | ".join(manual_v_msgs)

            G = propagate_metadata(G, ontology, root)
            self._assign_edge_weights(G, ontology, root)
            return G
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise e

    def on_post_analyze(self, G, communities: dict, analysis: dict, root) -> dict:
        root = Path(root)
        try:
            ontology = self.load_ontology(root)
        except Exception:
            return analysis

        violations = audit_architecture_rules(G, ontology, root)
        
        try:
            db = Database(str(self.db_path))
            all_violations = db.update_violations_and_statuses(violations, ontology, root, G)
            db.sync_graph_metadata(G, root)
            
            components = db.get_all_components()
            
            file_level_violations = []
            for filepath, comp in sorted(components.items()):
                status = comp.get("status")
                if status in ("VIOLATION_DETECTED", "PENDING_AUDIT"):
                    file_level_violations.append({
                        "filepath": filepath,
                        "status": status,
                        "violations": comp.get("violations") or []
                    })
            analysis["arch_violations"] = file_level_violations
        except Exception as e:
            print(f"warning: failed to update violations in database: {e}", file=sys.stderr)
            all_violations = [
                {
                    "origin": "automated",
                    "rule": v.rule_name,
                    "message": v.message,
                    "source_node": v.source_id,
                    "target_node": v.target_id,
                    "filepath": resolve_relative_path(v.filepath, root)
                } for v in violations
            ]
            
            violated_files = {}
            for v in all_violations:
                violated_files.setdefault(v["filepath"], []).append(v)
                
            analysis["arch_violations"] = [
                {
                    "filepath": filepath,
                    "status": "VIOLATION_DETECTED",
                    "violations": file_violations
                }
                for filepath, file_violations in sorted(violated_files.items())
            ]
        
        # Inject violations as surprises
        for v in all_violations:
            analysis.setdefault("surprises", []).append({
                "source": v.get("source_node", ""),
                "target": v.get("target_node", ""),
                "relation": "arch_violation",
                "note": f"[ARCH] {v['message']}",
                "confidence": "EXTRACTED",
                "source_files": [v.get("filepath", ""), ""],
            })
            
        # Convert all surprises from node-based to file-based
        if "surprises" in analysis:
            file_surprises = []
            seen_surprises = set()
            from utils import resolve_relative_path
            for s in analysis["surprises"]:
                src_node = s.get("source", "")
                tgt_node = s.get("target", "")
                
                # Resolve source file
                src_file = None
                if s.get("source_files") and len(s["source_files"]) > 0 and s["source_files"][0]:
                    src_file = s["source_files"][0]
                elif src_node in G.nodes:
                    src_file = G.nodes[src_node].get("source_file")
                
                # Resolve target file
                tgt_file = None
                if s.get("source_files") and len(s["source_files"]) > 1 and s["source_files"][1]:
                    tgt_file = s["source_files"][1]
                elif tgt_node in G.nodes:
                    tgt_file = G.nodes[tgt_node].get("source_file")
                
                if src_file:
                    src_file = resolve_relative_path(src_file, root)
                if tgt_file:
                    tgt_file = resolve_relative_path(tgt_file, root)
                    
                src_key = src_file if src_file else src_node
                tgt_key = tgt_file if tgt_file else tgt_node
                
                dup_key = (src_key, tgt_key, s.get("relation"), s.get("note") or s.get("why"))
                if dup_key in seen_surprises:
                    continue
                seen_surprises.add(dup_key)
                
                note = s.get("note") or s.get("why") or ""
                file_surprises.append({
                    "source": src_key,
                    "target": tgt_key,
                    "relation": s.get("relation", ""),
                    "note": note,
                    "confidence": s.get("confidence", "EXTRACTED"),
                    "source_files": [src_key, tgt_key] if src_key and tgt_key else (s.get("source_files") or [])
                })
            analysis["surprises"] = file_surprises
            
        return analysis

    def on_report(self, report_text: str, G, communities: dict, root) -> str:
        root = Path(root)
        return PluginReportGenerator.generate_report(report_text, G, communities, root, self)
