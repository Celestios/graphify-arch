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
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        """Initializes the relational schema and indices[cite: 3]."""
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
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes (filepath)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_edges_src ON edges (source_id)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_edges_tgt ON edges (target_id)")
        self.conn.commit()

    def sync_nodes(self, filepath: str, parsed_nodes: List[CodeNode]):
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

    def resolve_and_save_relations(self,
                                   unresolved: List[UnresolvedRelation],
                                   affected_files: Optional[List[str]] = None):
        """Resolves symbol-based relations using file-scope and receiver context[cite: 3]."""
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
        nodes = self._get_all_nodes_internal()
        cursor = self.conn.cursor()
        cursor.execute("SELECT source_id, target_id, edge_type FROM edges")
        edges = cursor.fetchall()

        G = nx.MultiDiGraph()
        for node_id, node in nodes.items():
            G.add_node(node_id, data=node)

        for s, t, etype in edges:
            if s in G and t in G:
                G.add_edge(s, t, type=etype)
        return G

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
        from propagator import propagate_purities
        G = propagate_purities(G, ontology)

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
        """Performs a BFS traversal to collect a sub-graph of nodes[cite: 3]."""
        visited = {target_id}
        target_node = self.get_node(target_id)
        if not target_node: return []

        results = [target_node]
        current_front = [target_id]

        for _ in range(radius):
            next_front = []
            for cid in current_front:
                neighbors = self._get_neighbors(cid, direction)
                for nid in neighbors:
                    if nid not in visited:
                        visited.add(nid)
                        if node := self.get_node(nid):
                            results.append(node)
                            next_front.append(nid)
            if not next_front: break
            current_front = next_front
        return results

    def _get_neighbors(self, node_id: str, direction: str) -> List[str]:
        cursor = self.conn.cursor()
        neighbors = []
        if direction == "downstream":
            cursor.execute("SELECT target_id FROM edges WHERE source_id = ?",
                           (node_id, ))
        elif direction == "upstream":
            cursor.execute("SELECT source_id FROM edges WHERE target_id = ?",
                           (node_id, ))
        elif direction == "symmetric":
            # Implements/Contains symmetry logic[cite: 3]
            cursor.execute(
                """
                SELECT source_id FROM edges 
                WHERE edge_type = 'Implements' AND target_id IN (
                    SELECT target_id FROM edges WHERE source_id = ? AND edge_type = 'Implements'
                ) AND source_id != ?
            """, (node_id, node_id))
            neighbors.extend([r[0] for r in cursor.fetchall()])
            cursor.execute(
                """
                SELECT target_id FROM edges 
                WHERE edge_type = 'Contains' AND source_id IN (
                    SELECT source_id FROM edges WHERE target_id = ? AND edge_type = 'Contains'
                ) AND target_id != ?
            """, (node_id, node_id))

        neighbors.extend([r[0] for r in cursor.fetchall()])
        return neighbors

    def semantic_vector_search(self, query_embedding: List[float],
                               limit: int) -> Dict[str, Any]:
        """Performs cosine similarity search using stored embedding blobs[cite: 3, 4]."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, filepath, node_type, ai_summary, embedding FROM nodes WHERE embedding IS NOT NULL"
        )

        scored_nodes = []
        for row in cursor.fetchall():
            blob = row['embedding']
            # Unpack little-endian floats[cite: 3, 4]
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

    def _get_all_nodes_internal(self) -> Dict[str, CodeNode]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM nodes")
        return {row['id']: self._row_to_node(row) for row in cursor.fetchall()}

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
                             embedding_bytes: Optional[bytes] = None):
        """Updates specific node metadata and resets the dirty flag[cite: 3]."""
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
