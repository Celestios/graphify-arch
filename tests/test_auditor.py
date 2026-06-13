import unittest
import sys
from pathlib import Path
import networkx as nx

# Add src to system path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from schema import OntologyConfig, DirectoryConfig, FieldConfig, FieldRuleConfig
from db import Violation
from auditor import audit_architecture_rules

class TestAuditor(unittest.TestCase):
    def test_audit_architecture_rules_no_violations(self):
        # Setup ontology
        ontology = OntologyConfig()
        
        # Build empty graph
        G = nx.MultiDiGraph()
        violations = audit_architecture_rules(G, ontology, Path("src"))
        self.assertEqual(len(violations), 0)

    def test_audit_architecture_rules_dependency_violation(self):
        # Setup ontology with dependency checking field
        rule = FieldRuleConfig(
            source="Presenter",
            target="DB",
            message="Presenter must not call DB directly",
            severity="error"
        )
        
        role_cfg = FieldConfig(
            values=["Presenter", "Service", "DB"],
            default="Presenter",
            handler="dependency_check",
            rules=[rule]
        )
        
        dir_cfg = DirectoryConfig(manual_fields={"role": role_cfg})
        ontology = OntologyConfig(directories={".": dir_cfg})
        
        # Create a graph where Presenter calls DB
        G = nx.MultiDiGraph()
        G.add_node("P", source_file="src/p.rs", arch_meta_role="Presenter", start_byte=10, end_byte=100)
        G.add_node("D", source_file="src/d.rs", arch_meta_role="DB")
        
        # Relation in calls/uses/references/etc.
        G.add_edge("P", "D", type="calls")
        
        violations = audit_architecture_rules(G, ontology, Path("src"))
        
        self.assertEqual(len(violations), 1)
        v = violations[0]
        self.assertEqual(v.rule_name, "role_violation")
        self.assertEqual(v.source_id, "P")
        self.assertEqual(v.target_id, "D")
        self.assertEqual(v.filepath, "src/p.rs")
        self.assertEqual(v.start_byte, 10)
        self.assertEqual(v.end_byte, 100)
        self.assertEqual(v.message, "Presenter must not call DB directly")

    def test_audit_architecture_rules_requires_audit(self):
        ontology = OntologyConfig()
        
        G = nx.MultiDiGraph()
        G.add_node("A", source_file="src/a.rs", arch_meta_requires_audit=True, start_byte=0, end_byte=50)
        
        violations = audit_architecture_rules(G, ontology, Path("src"))
        
        self.assertEqual(len(violations), 1)
        v = violations[0]
        self.assertEqual(v.rule_name, "requires_audit")
        self.assertEqual(v.source_id, "A")
        self.assertEqual(v.target_id, "")
        self.assertEqual(v.filepath, "src/a.rs")
        self.assertEqual(v.message, "Component 'A' requires manual architecture audit due to code/ontology schema changes.")

    def test_audit_directed_edge_preserves_direction(self):
        rule = FieldRuleConfig(
            source="Domain",
            target="Presentation",
            message="Domain must not depend on Presentation",
            severity="error"
        )
        layer_cfg = FieldConfig(
            values=["Domain", "Presentation"],
            default="Domain",
            handler="dependency_check",
            rules=[rule]
        )
        dir_cfg = DirectoryConfig(automatic_fields={"layer": layer_cfg})
        ontology = OntologyConfig(directories={".": dir_cfg})

        G = nx.MultiDiGraph()
        G.add_node("A", source_file="src/a.dart", arch_meta_layer="Domain", start_byte=0, end_byte=50)
        G.add_node("B", source_file="src/b.dart", arch_meta_layer="Presentation")
        G.add_edge("A", "B", type="calls")

        violations = audit_architecture_rules(G, ontology, Path("src"))
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].source_id, "A")
        self.assertEqual(violations[0].target_id, "B")

if __name__ == "__main__":
    unittest.main()
