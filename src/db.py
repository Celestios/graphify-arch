import os
from pathlib import Path
import sqlite3
import json
import struct
import networkx as nx
from typing import List, Dict, Optional, Tuple, Set, Any
from dataclasses import dataclass, asdict

from schema import (AstNodeType, CodeNode, EdgeType, OntologyConfig,
                    SemanticFacets, UnresolvedRelation, ContainsRelation,
                    CallsRelation, ImplementsRelation, FfiBridgeRelation,
                    FfiExportRelation)
from propagator import propagate_metadata


@dataclass
class NodeManifest:
    id: str
    filepath: str
    node_type: str


@dataclass
class Violation:
    rule_name: str
    source_id: str
    target_id: str
    filepath: str
    start_byte: int
    end_byte: int
    message: str


class Database:

    def __init__(self, path: str):
        self.db_path = Path(path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def close(self):
        if self.conn:
            self.conn.close()

    def get_component(self, filepath: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT filepath, status, manual_status, manual_fields, ast_hash FROM components WHERE filepath = ?",
            (filepath,))
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                "SELECT filepath, status, manual_status, manual_fields, ast_hash FROM components WHERE filepath LIKE '%' || ? OR ? LIKE '%' || filepath",
                (filepath, filepath))
            row = cursor.fetchone()
        if not row:
            return None
        manual_fields = json.loads(row['manual_fields']) if row['manual_fields'] else {}
        violations = self._get_violations(row['filepath'])
        return {
            "status": row['status'],
            "manual_status": row['manual_status'],
            "manual_fields": manual_fields,
            "violations": violations,
            "ast_hash": row['ast_hash']
        }

    def get_all_components(self) -> Dict[str, Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT filepath, status, manual_status, manual_fields, ast_hash FROM components")
        components = {}
        for row in cursor.fetchall():
            manual_fields = json.loads(row['manual_fields']) if row['manual_fields'] else {}
            violations = self._get_violations(row['filepath'])
            components[row['filepath']] = {
                "status": row['status'],
                "manual_status": row['manual_status'],
                "manual_fields": manual_fields,
                "violations": violations,
                "ast_hash": row['ast_hash']
            }
        return components

    def _get_violations(self, filepath: str) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT origin, rule, message, source_node, target_node FROM component_violations WHERE filepath = ?",
            (filepath,))
        return [
            {
                "origin": r['origin'],
                "rule": r['rule'],
                "message": r['message'],
                "source_node": r['source_node'],
                "target_node": r['target_node']
            }
            for r in cursor.fetchall()
        ]

    def _set_violations(self, filepath: str, violations: List[Dict[str, Any]]):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM component_violations WHERE filepath = ?", (filepath,))
        for v in violations:
            cursor.execute(
                "INSERT INTO component_violations (filepath, origin, rule, message, source_node, target_node) VALUES (?, ?, ?, ?, ?, ?)",
                (filepath, v.get("origin", ""), v.get("rule", ""),
                 v.get("message", ""), v.get("source_node", ""), v.get("target_node", "")))

    def _upsert_component(self, filepath: str, status: str, manual_status: Optional[str],
                          manual_fields: Dict[str, Any], ast_hash: Optional[str] = None):
        cursor = self.conn.cursor()
        cursor.execute(
            """INSERT INTO components (filepath, status, manual_status, manual_fields, ast_hash)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(filepath) DO UPDATE SET
                   status = excluded.status,
                   manual_status = excluded.manual_status,
                   manual_fields = excluded.manual_fields,
                   ast_hash = COALESCE(excluded.ast_hash, components.ast_hash)""",
            (filepath, status, manual_status, json.dumps(manual_fields), ast_hash))

    def save_component(self, filepath: str, status: str, manual_status: Optional[str],
                       manual_fields: Dict[str, Any], violations: List[Dict[str, Any]]):
        existing = self.get_component(filepath)
        ast_hash = existing.get("ast_hash") if existing else None
        self._upsert_component(filepath, status, manual_status, manual_fields, ast_hash)
        self._set_violations(filepath, violations)
        self.conn.commit()

    def set_component_status(self, filepath: str, status: str, violations_msg: str):
        comp = self.get_component(filepath)
        if comp:
            manual_fields = comp.get("manual_fields") or {}
            existing_violations = comp.get("violations") or []
        else:
            manual_fields = {}
            existing_violations = []

        filtered_violations = [v for v in existing_violations if v.get("origin") != "manual"]
        if violations_msg and status == "VIOLATION_DETECTED":
            filtered_violations.append({
                "origin": "manual",
                "message": violations_msg
            })

        self.save_component(filepath, status, status, manual_fields, filtered_violations)

    def set_component_status_bulk(self, updates: Dict[str, Dict[str, Any]], ontology, root: Path):
        import sys
        from utils import resolve_relative_path

        for raw_key, entry_data in updates.items():
            filepath = resolve_relative_path(raw_key, root)

            comp = self.get_component(filepath)
            matched_key = filepath
            if not comp:
                for row in self.conn.execute("SELECT filepath FROM components").fetchall():
                    k = row['filepath']
                    if k.endswith(filepath) or filepath.endswith(k):
                        matched_key = k
                        comp = self.get_component(k)
                        break

            if comp:
                manual_fields = comp.get("manual_fields") or {}
                existing_violations = comp.get("violations") or []
                ast_hash = comp.get("ast_hash")
            else:
                manual_fields = {}
                existing_violations = []
                ast_hash = None

            status = entry_data.get("status")
            violations_raw = entry_data.get("violations", "")
            if isinstance(violations_raw, list):
                violations_msg = " | ".join(str(v) for v in violations_raw)
            else:
                violations_msg = str(violations_raw) if violations_raw is not None else ""

            filtered_violations = [v for v in existing_violations if v.get("origin") != "manual"]
            if violations_msg and status == "VIOLATION_DETECTED":
                filtered_violations.append({
                    "origin": "manual",
                    "message": violations_msg
                })

            dir_fields = ontology.get_manual_fields_for_file(matched_key)
            for k, v in entry_data.items():
                if k in dir_fields:
                    field_cfg = dir_fields[k]
                    if field_cfg.values is None or v in field_cfg.values:
                        manual_fields[k] = v
                    else:
                        print(f"warning: value '{v}' for manual field '{k}' is not allowed. Using default '{field_cfg.default}'", file=sys.stderr)
                        manual_fields[k] = field_cfg.default

            self._upsert_component(matched_key, status, status, manual_fields, ast_hash)
            self._set_violations(matched_key, filtered_violations)

        self.conn.commit()
        self.sync_to_graph_json(root, ontology)

    def update_violations_and_statuses(self, violations: List[Any], ontology, root: Path, G: Optional[Any] = None) -> List[Dict[str, Any]]:
        import sys
        from utils import resolve_relative_path

        violations_by_file = {}
        for v in violations:
            rel_path = resolve_relative_path(v.filepath, root)
            violations_by_file.setdefault(rel_path, []).append(v)

        all_files = set()
        if G is not None:
            for node_id in G.nodes:
                sf = G.nodes[node_id].get("source_file")
                if sf:
                    all_files.add(resolve_relative_path(sf, root))
        else:
            cursor = self.conn.cursor()
            try:
                cursor.execute("SELECT DISTINCT filepath FROM nodes")
                all_files = {resolve_relative_path(row["filepath"], root) for row in cursor.fetchall()}
            except Exception:
                pass

        existing_files = {row['filepath'] for row in self.conn.execute("SELECT filepath FROM components").fetchall()}
        all_files.update(existing_files)
        all_files.update(violations_by_file.keys())

        all_aggregated_violations = []

        for filepath in all_files:
            if not filepath:
                continue
            comp = self.get_component(filepath)
            if comp:
                manual_status = comp.get("manual_status")
                manual_fields = comp.get("manual_fields") or {}
                ast_hash = comp.get("ast_hash")
                aggregated = [v for v in comp.get("violations", []) if v.get("origin") == "manual"]
            else:
                manual_status = None
                manual_fields = {}
                ast_hash = None
                aggregated = []

            file_violations = violations_by_file.get(filepath, [])
            for v in file_violations:
                aggregated.append({
                    "origin": "automated",
                    "rule": v.rule_name,
                    "message": v.message,
                    "source_node": v.source_id,
                    "target_node": v.target_id,
                    "filepath": filepath
                })

            if manual_status:
                status = manual_status
            elif aggregated:
                status = "VIOLATION_DETECTED"
            else:
                status = "COMPLIANT"

            self._upsert_component(filepath, status, manual_status, manual_fields, ast_hash)
            self._set_violations(filepath, aggregated)
            all_aggregated_violations.extend(aggregated)

        self.conn.commit()
        self.sync_to_graph_json(root, ontology)
        return all_aggregated_violations

    def _create_tables(self):
        """Initializes the relational schema and indices."""
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                filepath TEXT NOT NULL,
                node_type TEXT NOT NULL,
                start_byte INTEGER NOT NULL,
                end_byte INTEGER NOT NULL,
                ast_hash TEXT NOT NULL,
                semantics TEXT NOT NULL,
                ai_summary TEXT,
                embedding BLOB,
                raw_code TEXT NOT NULL DEFAULT '',
                previous_code TEXT,
                is_dirty INTEGER NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS edges (
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                PRIMARY KEY (source_id, target_id, edge_type),
                FOREIGN KEY(source_id) REFERENCES nodes(id) ON DELETE CASCADE,
                FOREIGN KEY(target_id) REFERENCES nodes(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS components (
                filepath TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'PENDING_AUDIT',
                manual_status TEXT,
                manual_fields TEXT NOT NULL DEFAULT '{}',
                ast_hash TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS component_violations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filepath TEXT NOT NULL,
                origin TEXT NOT NULL,
                rule TEXT,
                message TEXT,
                source_node TEXT,
                target_node TEXT,
                FOREIGN KEY(filepath) REFERENCES components(filepath) ON DELETE CASCADE
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes (filepath)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_edges_src ON edges (source_id)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_edges_tgt ON edges (target_id)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_cv_filepath ON component_violations (filepath)")
        self.conn.commit()

    def sync_nodes(self, filepath: str, parsed_nodes: List[CodeNode], ontology: Optional[OntologyConfig] = None):
        """Transactionally updates database state, storing deltas for code modifications[cite: 3]."""
        cursor = self.conn.cursor()

        # Fetch existing nodes for the file
        cursor.execute(
            "SELECT id, ast_hash, semantics, ai_summary, raw_code, is_dirty FROM nodes WHERE filepath = ?",
            (filepath, ))
        existing_nodes = {}
        for row in cursor.fetchall():
            existing_nodes[row['id']] = {
                'hash': row['ast_hash'],
                'semantics': row['semantics'],
                'ai_summary': row['ai_summary'],
                'old_raw_code': row['raw_code'],
                'is_dirty': bool(row['is_dirty'])
            }

        for node in parsed_nodes:
            if node.id in existing_nodes:
                old = existing_nodes.pop(node.id)
                updated_hash = node.ast_hash != old['hash']
                is_dirty = old['is_dirty']
                prev_code_backup = None

                if updated_hash:
                    is_dirty = True
                    prev_code_backup = old['old_raw_code']

                cursor.execute(
                    """
                    UPDATE nodes SET 
                        start_byte = ?, end_byte = ?, ast_hash = ?, raw_code = ?, 
                        previous_code = COALESCE(?, previous_code), is_dirty = ? 
                    WHERE id = ?
                """, (node.start_byte, node.end_byte, node.ast_hash,
                      node.raw_code, prev_code_backup, 1 if is_dirty else 0,
                      node.id))
            else:
                semantics_json = json.dumps(asdict(node.semantics))
                cursor.execute(
                    """
                    INSERT INTO nodes (id, filepath, node_type, start_byte, end_byte, ast_hash, semantics, ai_summary, raw_code, previous_code, is_dirty) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, 1)
                """, (node.id, node.filepath, node.node_type.value,
                      node.start_byte, node.end_byte, node.ast_hash,
                      semantics_json, node.raw_code))

        for orphaned_id in existing_nodes:
            cursor.execute("DELETE FROM nodes WHERE id = ?", (orphaned_id, ))

        self.conn.commit()

    def resolve_and_sync_relations(self,
                                   unresolved: List[UnresolvedRelation],
                                   affected_files: Optional[List[str]] = None):
        """Resolves symbol-based relations using file-scope and receiver context."""
        cursor = self.conn.cursor()
        symbol_to_ids = {}
        existing_node_ids = set()

        cursor.execute("SELECT id FROM nodes")
        for row in cursor.fetchall():
            node_id = row['id']
            existing_node_ids.add(node_id)
            symbol = node_id.split("::")[-1]
            symbol_to_ids.setdefault(symbol, []).append(node_id)

        proposed_edges = set()
        for rel in unresolved:
            if isinstance(rel, ContainsRelation):
                if rel.source_id in existing_node_ids and rel.target_id in existing_node_ids:
                    proposed_edges.add(
                        (rel.source_id, rel.target_id, rel.type))

            elif isinstance(rel, (CallsRelation, FfiBridgeRelation)):
                if rel.source_id not in existing_node_ids:
                    continue
                candidates = symbol_to_ids.get(rel.target_symbol, [])
                if not candidates:
                    continue

                # Heuristic resolution: rank by file path and class proximity
                best_candidate = candidates[0]
                high_score = -1
                for cand in candidates:
                    score = 0
                    if rel.caller_filepath in cand: score += 10
                    if rel.caller_class_symbol and rel.caller_class_symbol in cand:
                        score += 5
                    if score > high_score:
                        high_score = score
                        best_candidate = cand

                if best_candidate in existing_node_ids:
                    proposed_edges.add(
                        (rel.source_id, best_candidate, rel.type))

            elif isinstance(rel, (ImplementsRelation, FfiExportRelation)):
                if rel.source_id not in existing_node_ids:
                    continue
                for target_id in symbol_to_ids.get(rel.target_symbol, []):
                    if target_id in existing_node_ids:
                        proposed_edges.add(
                            (rel.source_id, target_id, rel.type))

        # Reconcile proposed edges with database state
        if affected_files:
            existing_edges = set()
            for f in affected_files:
                cursor.execute(
                    "SELECT source_id, target_id, edge_type FROM edges WHERE source_id = ? OR source_id LIKE ?",
                    (f, f + "::%"))
                for r in cursor.fetchall():
                    existing_edges.add((r[0], r[1], r[2]))
        else:
            cursor.execute("SELECT source_id, target_id, edge_type FROM edges")
            existing_edges = {(r[0], r[1], r[2]) for r in cursor.fetchall()}

        to_delete = existing_edges - proposed_edges
        to_insert = proposed_edges - existing_edges

        for s, t, e in to_delete:
            cursor.execute(
                "DELETE FROM edges WHERE source_id = ? AND target_id = ? AND edge_type = ?",
                (s, t, e))
        for s, t, e in to_insert:
            cursor.execute(
                "INSERT INTO edges (source_id, target_id, edge_type) VALUES (?, ?, ?)",
                (s, t, e))

        self.conn.commit()

    def get_graph(self) -> nx.MultiDiGraph:
        """Helper to build NetworkX MultiDiGraph from database nodes and edges."""
        return GraphBuilder.build_graph(self)


    def propagate_semantics(self, ontology: OntologyConfig):
        """Builds a topological graph and propagates layers and purities."""
        G = self.get_graph()

        # Apply layer assignments
        for node_id in G.nodes:
            node = G.nodes[node_id]['data']
            if node.semantics.layer == "Unknown" or not node.semantics.layer:
                assigned = None
                for prefix, layer in ontology.layer_assignments.items():
                    if node.filepath.startswith(prefix):
                        assigned = layer
                        break
                node.semantics.layer = assigned or ontology.default_layer

        roots = [
            n for n in G.nodes
            if G.nodes[n]['data'].node_type == AstNodeType.FILE
        ]
        for root in roots:
            for parent, child in nx.bfs_edges(G, root):
                edge_data = G.get_edge_data(parent, child)
                if any(d['type'] == 'Contains' for d in edge_data.values()):
                    if G.nodes[child]['data'].semantics.layer == "Unknown" or not G.nodes[child]['data'].semantics.layer:
                        G.nodes[child]['data'].semantics.layer = G.nodes[
                            parent]['data'].semantics.layer

        # Use external propagator for purity upward propagation
        G = propagate_metadata(G, ontology, Path(os.getcwd()))

        # Commit semantic updates
        cursor = self.conn.cursor()
        for node_id in G.nodes:
            new_sem = G.nodes[node_id]['data'].semantics
            cursor.execute("UPDATE nodes SET semantics = ? WHERE id = ?",
                           (json.dumps(asdict(new_sem)), node_id))
        self.conn.commit()

    def get_node(self, node_id: str) -> Optional[CodeNode]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM nodes WHERE id = ?", (node_id, ))
        row = cursor.fetchone()
        return self._row_to_node(row) if row else None

    def _row_to_node(self, row: sqlite3.Row) -> CodeNode:
        sem_data = json.loads(row['semantics'])
        semantics = SemanticFacets(**sem_data)
        return CodeNode(id=row['id'],
                        filepath=row['filepath'],
                        node_type=AstNodeType.from_str(row['node_type']),
                        start_byte=row['start_byte'],
                        end_byte=row['end_byte'],
                        ast_hash=row['ast_hash'],
                        semantics=semantics,
                        ai_summary=row['ai_summary'],
                        raw_code=row['raw_code'],
                        previous_code=row['previous_code'],
                        is_dirty=bool(row['is_dirty']))

    def get_subgraph(self, target_id: str, radius: int,
                     direction: str) -> List[CodeNode]:
        """Performs a BFS traversal to collect a sub-graph of nodes."""
        G = self.get_graph()
        return GraphTraverser.get_subgraph(G, target_id, radius, direction)

    def semantic_vector_search(self, query_embedding: List[float],
                               limit: int) -> Dict[str, Any]:
        """Performs cosine similarity search using stored embedding blobs[cite: 3, 4]."""
        return VectorSearchEngine.search(self, query_embedding, limit)


    def _get_all_nodes_internal(self) -> Dict[str, CodeNode]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM nodes")
        return {row['id']: self._row_to_node(row) for row in cursor.fetchall()}

    def _get_all_relations_internal(self) -> List[Tuple[str, str, str]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT source_id, target_id, edge_type FROM edges")
        return [(r['source_id'], r['target_id'], r['edge_type']) for r in cursor.fetchall()]

    def count_dirty_nodes(self) -> int:
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM nodes WHERE is_dirty = 1")
        return cursor.fetchone()[0]

    def update_node_metadata(self,
                             node_id: str,
                             summary: Optional[str] = None,
                             layer: Optional[str] = None,
                             role: Optional[str] = None,
                             pattern: Optional[str] = None,
                             purity: Optional[str] = None,
                             embedding_bytes: Optional[bytes] = None,
                             status: Optional[str] = None):
        """Updates specific node metadata and resets the dirty flag."""
        node = self.get_node(node_id)
        if not node: return

        if summary: node.ai_summary = summary
        if layer: node.semantics.layer = layer
        if role: node.semantics.role = role
        if pattern: node.semantics.pattern = pattern
        if purity: node.semantics.purity = purity

        semantics_json = json.dumps(asdict(node.semantics))
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE nodes SET semantics = ?, ai_summary = ?, embedding = COALESCE(?, embedding), is_dirty = 0 
            WHERE id = ?
        """, (semantics_json, node.ai_summary, embedding_bytes, node_id))
        self.conn.commit()

        if status:
            self.set_component_status(node.filepath, status, "")

    def sync_graph_metadata(self, G, root: Path):
        """Write arch_meta_* from graph nodes back to nodes.semantics in DB."""
        from utils import resolve_relative_path
        cursor = self.conn.cursor()
        field_map = {
            "arch_meta_layer": "layer",
            "arch_meta_tier": "tier",
            "arch_meta_purity": "purity",
            "arch_meta_architectural_role": "architectural_role",
            "arch_meta_pattern": "pattern",
        }
        for node_id, data in G.nodes(data=True):
            node = self.get_node(node_id)
            if not node:
                continue
            changed = False
            for graph_key, sem_key in field_map.items():
                val = data.get(graph_key)
                if val is not None and val != "Unknown":
                    node.semantics.fields[sem_key] = val
                    changed = True
            summary = data.get("arch_meta_ai_summary")
            if summary:
                node.ai_summary = summary
                changed = True
            if changed:
                semantics_json = json.dumps(asdict(node.semantics))
                cursor.execute(
                    "UPDATE nodes SET semantics = ?, ai_summary = ? WHERE id = ?",
                    (semantics_json, node.ai_summary, node_id))
        self.conn.commit()

    def sync_to_graph_json(self, root: Path, ontology: OntologyConfig):
        """
        Synchronizes all metadata from the SQLite database (nodes and components tables)
        back into graphify-out/graph.json.
        """
        graph_path = root / "graphify-out" / "graph.json"
        if not graph_path.exists():
            return

        try:
            # 1. Load database nodes and components in memory
            db_nodes = self._get_all_nodes_internal()
            db_components = self.get_all_components()

            # 2. Load graph.json
            with open(graph_path, "r", encoding="utf-8") as f:
                graph_data = json.load(f)

            # 3. Update nodes in graph_data
            modified = False
            for node in graph_data.get("nodes", []):
                node_id = node.get("id")
                if not node_id:
                    continue

                db_node = db_nodes.get(node_id)
                if db_node:
                    # Sync node-level configured metadata dynamically from ontology
                    fields_cfg = ontology.get_fields_for_file(db_node.filepath)
                    for field_name in fields_cfg.keys():
                        if field_name == "ai_summary":
                            val = db_node.ai_summary
                        else:
                            val = db_node.semantics.fields.get(field_name)

                        if val is not None and val != "Unknown":
                            node[f"arch_meta_{field_name}"] = val
                            node[field_name] = val

                    # Also find component info for the node's file
                    from utils import resolve_relative_path
                    sf = node.get("source_file")
                    if sf:
                        rel_sf = resolve_relative_path(sf, root)
                        comp = db_components.get(rel_sf)
                        if not comp:
                            for k, c in db_components.items():
                                if k.endswith(rel_sf) or rel_sf.endswith(k):
                                    comp = c
                                    break

                        if comp:
                            # Sync component status
                            status = comp.get("status")
                            if status:
                                node["arch_meta_status"] = status
                                node["status"] = status

                            # Sync manual status
                            manual_status = comp.get("manual_status")
                            if manual_status:
                                node["arch_meta_manual_status"] = manual_status

                            # Sync manual violations msg
                            violations_msg = " | ".join(v["message"] for v in comp.get("violations", []) if v.get("origin") == "manual")
                            if violations_msg:
                                node["arch_meta_manual_violations"] = violations_msg

                            # Sync manual fields (like ai_summary, pattern, etc.)
                            manual_fields = comp.get("manual_fields") or {}
                            for field_name, val in manual_fields.items():
                                if val is not None and val != "Unknown":
                                    if field_name == "ai_summary" and db_node.node_type != AstNodeType.FILE:
                                        continue
                                    node[f"arch_meta_{field_name}"] = val
                                    node[field_name] = val

                    modified = True

            if modified:
                with open(graph_path, "w", encoding="utf-8") as f:
                    json.dump(graph_data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            import sys
            print(f"warning: failed to sync database back to graph.json: {e}", file=sys.stderr)


class GraphBuilder:
    @staticmethod
    def build_graph(db: Database) -> nx.MultiDiGraph:
        """Helper to build NetworkX MultiDiGraph from database nodes and edges."""
        nodes = db._get_all_nodes_internal()
        edges = db._get_all_relations_internal()

        G = nx.MultiDiGraph()
        for node_id, node in nodes.items():
            G.add_node(node_id, data=node)

        for s, t, etype in edges:
            if s in G and t in G:
                G.add_edge(s, t, type=etype)
        return G


class VectorSearchEngine:
    @staticmethod
    def search(db: Database, query_embedding: List[float], limit: int) -> Dict[str, Any]:
        """Performs cosine similarity search using stored embedding blobs."""
        cursor = db.conn.cursor()
        cursor.execute(
            "SELECT id, filepath, node_type, ai_summary, embedding FROM nodes WHERE embedding IS NOT NULL"
        )

        scored_nodes = []
        for row in cursor.fetchall():
            blob = row['embedding']
            # Unpack little-endian floats
            n_floats = len(blob) // 4
            node_emb = struct.unpack(f'<{n_floats}f', blob)

            similarity = sum(q * n for q, n in zip(query_embedding, node_emb))
            scored_nodes.append({
                "score": similarity,
                "id": row['id'],
                "filepath": row['filepath'],
                "type": row['node_type'],
                "summary": row['ai_summary'] or ""
            })

        scored_nodes.sort(key=lambda x: x['score'], reverse=True)
        return {
            "count": min(len(scored_nodes), limit),
            "results": scored_nodes[:limit]
        }


class GraphTraverser:
    @staticmethod
    def get_subgraph(G: nx.MultiDiGraph, target_id: str, radius: int, direction: str) -> List[CodeNode]:
        if target_id not in G:
            return []

        visited = {target_id}
        results = [G.nodes[target_id]['data']]
        current_front = [target_id]

        for _ in range(radius):
            next_front = []
            for cid in current_front:
                neighbors = GraphTraverser._get_neighbors(G, cid, direction)
                for nid in neighbors:
                    if nid not in visited and nid in G:
                        visited.add(nid)
                        results.append(G.nodes[nid]['data'])
                        next_front.append(nid)
            if not next_front:
                break
            current_front = next_front
        return results

    @staticmethod
    def _get_neighbors(G: nx.MultiDiGraph, node_id: str, direction: str) -> List[str]:
        neighbors = []
        if direction == "downstream":
            if node_id in G:
                neighbors.extend(G.successors(node_id))
        elif direction == "upstream":
            if node_id in G:
                neighbors.extend(G.predecessors(node_id))
        elif direction == "symmetric":
            for u, v, key, data in G.edges(keys=True, data=True):
                if u == node_id and data.get("type") == "Implements":
                    for w, target_node, k, d in G.in_edges(v, keys=True, data=True):
                        if d.get("type") == "Implements" and w != node_id:
                            neighbors.append(w)
                if v == node_id and data.get("type") == "Contains":
                    for parent_node, w, k, d in G.out_edges(u, keys=True, data=True):
                        if d.get("type") == "Contains" and w != node_id:
                            neighbors.append(w)
        return list(set(neighbors))


