import os
import sys
import json
import time
import queue
import subprocess
from pathlib import Path
from typing import List, Set, Dict, Any

import dataclasses
from schema import OntologyConfig, CodeNode, ContainsRelation, AstNodeType
from project import Workspace
from db import Database
from embedder import LocalEmbedder
from parser import AstParser
from compiler import ContextCompiler
import cli

# Attempt to load filesystem event monitoring library
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent, FileDeletedEvent
except ImportError:
    print("Error: 'watchdog' dependency missing. Run: pip install watchdog",
          file=sys.stderr)
    sys.exit(1)


def load_ontology_from_workspace(workspace: Workspace) -> OntologyConfig:
    return workspace.config.ontology


def get_git_head(dir_str: str) -> str:
    """Retrieves the current git repository HEAD hash."""
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"],
                             capture_output=True,
                             text=True,
                             cwd=dir_str,
                             check=True)
        return res.stdout.strip()
    except Exception:
        return ""


def force_full_reindex(workspace: Workspace, database: Database,
                       ontology: OntologyConfig) -> None:
    """Forces a complete evaluation of all tracked files within the workspace repository topology."""
    try:
        res = subprocess.run(["git", "ls-files"],
                             capture_output=True,
                             text=True,
                             cwd=str(workspace.root_dir),
                             check=True)
    except Exception:
        return

    parsed_nodes: List[CodeNode] = []
    parsed_relations: List[Any] = []

    for line in res.stdout.splitlines():
        path = workspace.root_dir / line
        if not path.exists() or workspace.is_excluded(str(path)):
            continue

        path_str = str(path)
        if path_str.endswith(".rs") or path_str.endswith(".dart"):
            try:
                content = path.read_text(encoding="utf-8")
                nodes, relations = AstParser.parse_file(path_str, content)
                parsed_nodes.extend(nodes)
                parsed_relations.extend(relations)
            except Exception:
                continue

    file_groups: Dict[str, List[CodeNode]] = {}
    for node in parsed_nodes:
        file_groups.setdefault(node.filepath, []).append(node)

    for filepath, nodes in file_groups.items():
        database.sync_nodes(filepath, nodes)

    database.resolve_and_sync_relations(parsed_relations, None)
    database.propagate_semantics(ontology)


def ingest_git_delta(dir_str: str, workspace: Workspace,
                     all_nodes: List[CodeNode], all_relations: List[Any],
                     affected_files: Set[str]) -> None:
    """Scans for untracked, modified, or deleted git file indices to minimize translation pass times."""
    res = subprocess.run([
        "git", "ls-files", "--modified", "--others", "--deleted",
        "--exclude-standard"
    ],
                         capture_output=True,
                         text=True,
                         cwd=dir_str,
                         check=True)

    for line in res.stdout.splitlines():
        path = Path(dir_str) / line
        path_str = str(path)

        if workspace.is_excluded(path_str):
            continue

        if path_str.endswith(".rs") or path_str.endswith(".dart"):
            affected_files.add(path_str)

            if not path.exists():
                continue

            try:
                content = path.read_text(encoding="utf-8")
                nodes, relations = AstParser.parse_file(path_str, content)
                all_nodes.extend(nodes)
                all_relations.extend(relations)
            except Exception as e:
                print(f"Parser error on file {path_str}: {e}", file=sys.stderr)


class WorkspaceWatchHandler(FileSystemEventHandler):
    """Bridges low-level file modifications into the synchronized pipeline queue."""

    def __init__(self, event_queue: queue.Queue):
        self.event_queue = event_queue

    def on_any_event(self, event):
        if isinstance(event,
                       (FileModifiedEvent, FileCreatedEvent, FileDeletedEvent)):
            self.event_queue.put(event.src_path)


# Command Dispatch Handlers

def handle_init(args) -> None:
    from scaffolder import scaffold_project
    current_dir = Path.cwd().resolve()
    dot_arch = current_dir / ".arch"
    dot_arch.mkdir(parents=True, exist_ok=True)

    default_config = scaffold_project(current_dir)
    config_path = dot_arch / "arch.json"
    
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(default_config, f, indent=4)
    print(f"Initialized Graphify-Arch workspace at {config_path}")


def handle_discover_ontology(args, workspace, database) -> None:
    print(json.dumps(dataclasses.asdict(workspace.config.ontology), indent=2))


def handle_reindex(args, workspace, database) -> None:
    from plugins import discover_plugins, run_hook
    plugins = discover_plugins(workspace.root_dir)
    
    force_full_reindex(workspace, database, workspace.config.ontology)
    
    # Trigger post-build plugin hooks and save semantic mutations
    G = database.get_graph()
    G = run_hook(plugins, "on_post_build", G, {}, workspace.root_dir)
    
    cursor = database.conn.cursor()
    for node_id in G.nodes:
        new_sem = G.nodes[node_id]['data'].semantics
        cursor.execute("UPDATE nodes SET semantics = ? WHERE id = ?",
                       (json.dumps(dataclasses.asdict(new_sem)), node_id))
    database.conn.commit()
    
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
            results.append({
                "id": n.id,
                "filepath": n.filepath,
                "node_type": n.node_type.value,
                "raw_code": n.raw_code if args.include_body else None
            })
    print(json.dumps(results, indent=2))


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
    embedder = LocalEmbedder()
    query_emb = embedder.embed(args.query)
    res = database.semantic_vector_search(query_emb, args.limit)
    print(json.dumps(res, indent=2))


def handle_watch(args, workspace, database) -> None:
    print(f"Monitoring workspace {workspace.root_dir} for changes...")
    event_queue = queue.Queue()
    handler = WorkspaceWatchHandler(event_queue)
    observer = Observer()
    observer.schedule(handler, str(workspace.root_dir), recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(1)
            changed_files = set()
            while not event_queue.empty():
                changed_files.add(event_queue.get())
            if changed_files:
                print(f"Detected changes in: {list(changed_files)}. Reindexing...")
                force_full_reindex(workspace, database, workspace.config.ontology)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def main() -> None:
    args = cli.parse_args()

    if args.command == "init":
        handle_init(args)
        return

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
        "watch": handle_watch,
    }

    handler = handlers.get(args.command)
    if handler:
        handler(args, workspace, database)
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
