import unittest
import sys
import tempfile
import shutil
import struct
import json
from pathlib import Path

# Add src to system path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from schema import AstNodeType, CodeNode, SemanticFacets, ContainsRelation, CallsRelation, ImplementsRelation
from db import Database, Violation

class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.db_path = self.temp_dir / "graphify-out" / "arch" / "graph.db"
        self.db = Database(str(self.db_path))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.temp_dir)

    def test_database_creation(self):
        # Tables should exist
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        self.assertIn("nodes", tables)
        self.assertIn("edges", tables)

    def test_component_metadata_save_load(self):
        self.db.save_component(
            filepath="src/main.rs",
            status="PENDING_AUDIT",
            manual_status=None,
            manual_fields={"layer": "Core"},
            violations=[]
        )
        
        comp = self.db.get_component("src/main.rs")
        self.assertIsNotNone(comp)
        self.assertEqual(comp["status"], "PENDING_AUDIT")
        self.assertEqual(comp["manual_fields"]["layer"], "Core")

    def test_sync_nodes(self):
        node1 = CodeNode(
            id="src/main.rs::foo",
            filepath="src/main.rs",
            node_type=AstNodeType.FUNCTION,
            start_byte=10,
            end_byte=50,
            ast_hash="hash1",
            raw_code="fn foo() {}",
            semantics=SemanticFacets(layer="UI")
        )
        
        # Initial sync
        self.db.sync_nodes("src/main.rs", [node1])
        n = self.db.get_node("src/main.rs::foo")
        self.assertIsNotNone(n)
        self.assertEqual(n.ast_hash, "hash1")
        self.assertEqual(n.raw_code, "fn foo() {}")
        self.assertEqual(n.semantics.layer, "UI")
        self.assertTrue(n.is_dirty)

        # Update node
        node1_updated = CodeNode(
            id="src/main.rs::foo",
            filepath="src/main.rs",
            node_type=AstNodeType.FUNCTION,
            start_byte=10,
            end_byte=60,
            ast_hash="hash2",
            raw_code="fn foo() { println!(); }",
            semantics=SemanticFacets(layer="UI")
        )
        self.db.sync_nodes("src/main.rs", [node1_updated])
        n_up = self.db.get_node("src/main.rs::foo")
        self.assertEqual(n_up.ast_hash, "hash2")
        self.assertEqual(n_up.raw_code, "fn foo() { println!(); }")

    def test_resolve_and_sync_relations(self):
        # Insert two nodes
        n1 = CodeNode(
            id="src/main.rs::main",
            filepath="src/main.rs",
            node_type=AstNodeType.FUNCTION,
            start_byte=0,
            end_byte=100,
            ast_hash="h1",
            raw_code="fn main() { foo(); }",
            semantics=SemanticFacets()
        )
        n2 = CodeNode(
            id="src/main.rs::foo",
            filepath="src/main.rs",
            node_type=AstNodeType.FUNCTION,
            start_byte=120,
            end_byte=200,
            ast_hash="h2",
            raw_code="fn foo() {}",
            semantics=SemanticFacets()
        )
        self.db.sync_nodes("src/main.rs", [n1, n2])

        # Resolve CallsRelation
        rel = CallsRelation(
            source_id="src/main.rs::main",
            target_symbol="foo",
            caller_filepath="src/main.rs"
        )
        self.db.resolve_and_sync_relations([rel])

        # Verify edge exists in NetworkX graph
        G = self.db.get_graph()
        self.assertIn("src/main.rs::main", G)
        self.assertIn("src/main.rs::foo", G)
        self.assertTrue(G.has_edge("src/main.rs::main", "src/main.rs::foo"))

    def test_bfs_subgraph_traversal(self):
        # Create small chain: A -> B -> C
        nodes = []
        for name in ["A", "B", "C"]:
            nodes.append(CodeNode(
                id=name,
                filepath=f"src/{name}.rs",
                node_type=AstNodeType.FUNCTION,
                start_byte=0,
                end_byte=10,
                ast_hash=name,
                raw_code="",
                semantics=SemanticFacets()
            ))
        self.db.sync_nodes("src/A.rs", nodes)

        # Add edges
        cursor = self.db.conn.cursor()
        cursor.execute("INSERT INTO edges VALUES ('A', 'B', 'Calls')")
        cursor.execute("INSERT INTO edges VALUES ('B', 'C', 'Calls')")
        self.db.conn.commit()

        # Traversals
        sub_down = self.db.get_subgraph("A", radius=1, direction="downstream")
        sub_down_ids = {node.id for node in sub_down}
        self.assertEqual(sub_down_ids, {"A", "B"})

        sub_up = self.db.get_subgraph("C", radius=1, direction="upstream")
        sub_up_ids = {node.id for node in sub_up}
        self.assertEqual(sub_up_ids, {"B", "C"})

    def test_semantic_vector_search(self):
        n1 = CodeNode(
            id="A", filepath="src/A.rs", node_type=AstNodeType.FUNCTION,
            start_byte=0, end_byte=10, ast_hash="A", raw_code="",
            semantics=SemanticFacets()
        )
        self.db.sync_nodes("src/A.rs", [n1])

        # Insert float array embedding
        emb = [0.1, 0.2, 0.3, 0.4]
        emb_bytes = struct.pack('<4f', *emb)
        self.db.update_node_metadata("A", summary="This is a test summary", embedding_bytes=emb_bytes)

        res = self.db.semantic_vector_search([0.1, 0.2, 0.3, 0.4], limit=1)
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["results"][0]["id"], "A")
        self.assertEqual(res["results"][0]["summary"], "This is a test summary")

if __name__ == "__main__":
    unittest.main()
