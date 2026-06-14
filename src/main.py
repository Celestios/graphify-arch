import os
import sys
import json
from pathlib import Path
from typing import List, Dict, Any

import dataclasses
from networkx.readwrite import json_graph
from schema import OntologyConfig, AstNodeType
from project import Workspace
from db import Database
from embedder import LocalEmbedder
from compiler import ContextCompiler
import cli


def load_ontology_from_workspace(workspace: Workspace) -> OntologyConfig:
    return workspace.config.ontology


def load_graph_from_json(root: Path) -> Any:
    """Loads the NetworkX graph from graphify's graph.json output."""
    graph_path = root / "graphify-out" / "graph.json"
    if not graph_path.exists():
        raise FileNotFoundError(f"graph.json not found at {graph_path}. Run graphify first.")
    with open(graph_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if "links" not in raw and "edges" in raw:
        raw = dict(raw, links=raw["edges"])
    try:
        return json_graph.node_link_graph(raw, edges="links")
    except TypeError:
        return json_graph.node_link_graph(raw)


def sync_graph_to_database(database: Database, G) -> None:
    """Synchronizes a NetworkX graph into the SQLite database."""
    from schema import CodeNode, SemanticFacets
    cursor = database.conn.cursor()
    cursor.execute("DELETE FROM nodes")
    cursor.execute("DELETE FROM edges")
    database.conn.commit()

    for node_id, data in G.nodes(data=True):
        filepath = data.get("source_file", "")
        if not filepath:
            continue
        node_type_str = data.get("type") or "Function"
        try:
            node_type = AstNodeType.from_str(node_type_str)
        except Exception:
            node_type = AstNodeType.FUNCTION
        semantics_data = {}
        for key in data:
            if key.startswith("arch_meta_"):
                field_name = key[len("arch_meta_"):]
                semantics_data[field_name] = data[key]
        semantics = SemanticFacets(**semantics_data) if semantics_data else SemanticFacets()
        cursor.execute(
            """INSERT OR REPLACE INTO nodes
               (id, filepath, node_type, start_byte, end_byte, ast_hash, semantics, ai_summary, raw_code, previous_code, is_dirty)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (node_id, filepath, node_type.value,
             data.get("start_byte", 0), data.get("end_byte", 0),
             data.get("ast_hash", ""),
             json.dumps(dataclasses.asdict(semantics)),
             data.get("ai_summary"),
             data.get("raw_code", ""),
             None, 0)
        )

    for u, v, edge_data in G.edges(data=True):
        edge_type = edge_data.get("type", edge_data.get("relation", "Calls"))
        cursor.execute(
            "INSERT OR IGNORE INTO edges (source_id, target_id, edge_type) VALUES (?, ?, ?)",
            (u, v, edge_type)
        )
    database.conn.commit()


# Command Dispatch Handlers

def handle_discover_ontology(args, workspace, database) -> None:
    print(json.dumps(dataclasses.asdict(workspace.config.ontology), indent=2))


def handle_reindex(args, workspace, database) -> None:
    from plugins import discover_plugins, run_hook

    G = load_graph_from_json(workspace.root_dir)
    sync_graph_to_database(database, G)

    plugins = discover_plugins(workspace.root_dir)
    G = run_hook(plugins, "on_post_build", G, {}, workspace.root_dir)

    cursor = database.conn.cursor()
    for node_id in G.nodes:
        node_data = G.nodes[node_id]
        new_sem = node_data.get("data")
        if new_sem and hasattr(new_sem, "semantics"):
            cursor.execute("UPDATE nodes SET semantics = ? WHERE id = ?",
                           (json.dumps(dataclasses.asdict(new_sem.semantics)), node_id))
    database.conn.commit()
    database.sync_to_graph_json(workspace.root_dir, workspace.config.ontology)

    print("Reindexing and ontology semantic propagation completed.")


def handle_get_dirty_nodes(args, workspace, database) -> None:
    nodes = database._get_all_nodes_internal().values()
    dirty_nodes = [
        {"id": n.id, "filepath": n.filepath, "node_type": n.node_type.value}
        for n in nodes if n.is_dirty
    ]
    print(json.dumps(dirty_nodes, indent=2))


def handle_compile_context(args, workspace, database) -> None:
    nodes = database.get_subgraph(args.target, args.radius, args.direction)
    ContextCompiler.compile(args.target, nodes, args.resolution, args.direction, args.radius, args.out, workspace.config.ontology)
    print(f"Compiled context to {args.out}")


def handle_audit(args, workspace, database) -> None:
    from auditor import audit_architecture_rules
    from plugins import discover_plugins, run_hook
    plugins = discover_plugins(workspace.root_dir)

    G = database.get_graph()
    G = run_hook(plugins, "on_post_build", G, {}, workspace.root_dir)

    violations = audit_architecture_rules(G, workspace.config.ontology, workspace.root_dir)
    analysis = {"violations": [dataclasses.asdict(v) for v in violations]}
    analysis = run_hook(plugins, "on_post_analyze", G, {}, analysis, workspace.root_dir)

    print(json.dumps(analysis, indent=2))


def handle_query_file(args, workspace, database) -> None:
    comp = database.get_component(args.path)
    comp_dict = None
    if comp:
        comp_dict = {
            "filepath": args.path,
            "status": comp.get("status"),
            "manual_status": comp.get("manual_status"),
            "manual_fields": comp.get("manual_fields"),
            "violations": comp.get("violations")
        }
    else:
        comp_dict = {
            "filepath": args.path,
            "status": "Unknown",
            "manual_status": None,
            "manual_fields": {},
            "violations": []
        }

    ontology = workspace.config.ontology
    nodes = database._get_all_nodes_internal().values()
    results = []
    for n in nodes:
        if n.filepath != args.path:
            continue
        match = False
        if not (args.methods or args.independent_functions or args.impl_methods or args.classes or args.functions or args.imports):
            match = True
        else:
            if args.methods and n.node_type == AstNodeType.METHOD:
                match = True
            if args.functions and n.node_type == AstNodeType.FUNCTION:
                match = True
            if args.classes and n.node_type in (AstNodeType.STRUCT, AstNodeType.CLASS):
                match = True
            if args.independent_functions and n.node_type == AstNodeType.FUNCTION and "::" not in n.id.split(args.path + "::")[-1]:
                match = True
            if args.impl_methods and f"impl_{args.impl_methods}" in n.id:
                match = True
        if match:
            result = {
                "id": n.id,
                "filepath": n.filepath,
                "node_type": n.node_type.value,
                "raw_code": n.raw_code if args.include_body else None
            }
            sem = n.semantics
            fields_cfg = ontology.get_fields_for_file(n.filepath)
            for field_name in fields_cfg.keys():
                if field_name == "ai_summary":
                    result[field_name] = n.ai_summary or "Unknown"
                else:
                    result[field_name] = sem.fields.get(field_name, "Unknown")
            results.append(result)

    output = {
        "component": comp_dict,
        "nodes": results
    }
    print(json.dumps(output, indent=2))


def handle_update_nodes(args, workspace, database) -> None:
    with open(args.payload_file, "r", encoding="utf-8") as f:
        payload = json.load(f)
    for update in payload:
        database.update_node_metadata(
            node_id=update["id"],
            summary=update.get("summary"),
            layer=update.get("layer"),
            role=update.get("role"),
            pattern=update.get("pattern"),
            purity=update.get("purity"),
            embedding_bytes=bytes.fromhex(update["embedding"]) if "embedding" in update else None
        )
    print("Updated nodes successfully.")


def handle_semantic_search(args, workspace, database) -> None:
    try:
        embedder = LocalEmbedder()
    except FileNotFoundError:
        sys.exit(1)
    query_emb = embedder.embed(args.query)
    res = database.semantic_vector_search(query_emb, args.limit)
    print(json.dumps(res, indent=2))


def main() -> None:
    args = cli.parse_args()
    workspace = Workspace.discover()
    database = Database(str(workspace.db_path))

    handlers = {
        "discover-ontology": handle_discover_ontology,
        "reindex": handle_reindex,
        "get-dirty-nodes": handle_get_dirty_nodes,
        "compile-context": handle_compile_context,
        "audit": handle_audit,
        "query-file": handle_query_file,
        "update-nodes": handle_update_nodes,
        "semantic-search": handle_semantic_search,
    }

    handler = handlers.get(args.command)
    if handler:
        handler(args, workspace, database)
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        sys.exit(1)

    database.close()


if __name__ == "__main__":
    main()
