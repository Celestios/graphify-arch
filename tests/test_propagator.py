import unittest
import sys
from pathlib import Path
import networkx as nx

# Add src to system path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from schema import OntologyConfig, DirectoryConfig, FieldConfig
from propagator import propagate_metadata

class TestPropagator(unittest.TestCase):
    def test_propagate_metadata_basic(self):
        # Define field config with propagation handler
        purity_cfg = FieldConfig(
            values=["Impure", "SideEffectFree", "Pure"],
            default="Impure",
            handler="propagation",
            weights={"Impure": 0, "SideEffectFree": 1, "Pure": 2}
        )
        
        dir_cfg = DirectoryConfig(
            manual_fields={"purity": purity_cfg},
            automatic_fields={}
        )
        
        ontology = OntologyConfig(
            directories={".": dir_cfg}
        )
        
        # Build networkx graph
        # Node A calls Node B. Node B has purity "Pure".
        # Node A's purity should be propagated to "Pure" as well because Pure (weight 2) > Impure (weight 0).
        G = nx.MultiDiGraph()
        G.add_node("A", source_file="src/a.rs", arch_meta_purity="Impure")
        G.add_node("B", source_file="src/b.rs", arch_meta_purity="Pure")
        
        # Add edge with "calls" relation
        G.add_edge("A", "B", type="calls")
        
        root = Path("src")
        G_res = propagate_metadata(G, ontology, root)
        
        self.assertEqual(G_res.nodes["A"]["arch_meta_purity"], "Pure")
        self.assertEqual(G_res.nodes["B"]["arch_meta_purity"], "Pure")

    def test_propagate_metadata_barrier(self):
        # Propagation field with barrier
        purity_cfg = FieldConfig(
            values=["Impure", "Pure"],
            default="Impure",
            handler="propagation",
            weights={"Impure": 0, "Pure": 1},
            barriers=["Presenter"],
            barrier_field="architectural_role"
        )
        
        dir_cfg = DirectoryConfig(
            manual_fields={"purity": purity_cfg},
            automatic_fields={}
        )
        ontology = OntologyConfig(directories={".": dir_cfg})
        
        # A calls B. A has role "Presenter" which is a barrier.
        # Propagation should not cross A, so A remains Impure.
        G = nx.MultiDiGraph()
        G.add_node("A", source_file="src/a.rs", arch_meta_purity="Impure", arch_meta_architectural_role="Presenter")
        G.add_node("B", source_file="src/b.rs", arch_meta_purity="Pure", arch_meta_architectural_role="Presenter")
        
        G.add_edge("A", "B", type="calls")
        
        G_res = propagate_metadata(G, ontology, Path("src"))
        self.assertEqual(G_res.nodes["A"]["arch_meta_purity"], "Impure")

    def test_propagate_metadata_no_weight_change(self):
        purity_cfg = FieldConfig(
            values=["Impure", "Pure"],
            default="Impure",
            handler="propagation",
            weights={"Impure": 0, "Pure": 1}
        )
        dir_cfg = DirectoryConfig(manual_fields={"purity": purity_cfg})
        ontology = OntologyConfig(directories={".": dir_cfg})
        
        # A calls B. A is Pure, B is Impure.
        # Since A is Pure (weight 1) and B is Impure (weight 0), no propagation should change A's purity.
        G = nx.MultiDiGraph()
        G.add_node("A", source_file="src/a.rs", arch_meta_purity="Pure")
        G.add_node("B", source_file="src/b.rs", arch_meta_purity="Impure")
        G.add_edge("A", "B", type="calls")
        
        G_res = propagate_metadata(G, ontology, Path("src"))
        self.assertEqual(G_res.nodes["A"]["arch_meta_purity"], "Pure")

if __name__ == "__main__":
    unittest.main()
