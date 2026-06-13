import unittest
import sys
from pathlib import Path

# Add src to system path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from schema import (
    AstNodeType,
    SemanticFacets,
    AssignmentCondition,
    MetadataAssignmentRule,
    FieldRuleConfig,
    FieldConfig,
    ArchitecturalRule,
    MetadataFieldConfig,
    DirectoryConfig,
    OntologyConfig,
    ProjectConfig,
    CodeNode,
)

class TestSchema(unittest.TestCase):
    def test_ast_node_type_from_str(self):
        self.assertEqual(AstNodeType.from_str("File"), AstNodeType.FILE)
        self.assertEqual(AstNodeType.from_str("Struct"), AstNodeType.STRUCT)
        self.assertEqual(AstNodeType.from_str("NonExistent"), AstNodeType.FUNCTION)

    def test_semantic_facets_attrs(self):
        facets = SemanticFacets(layer="UI", role="Presenter", custom_val=42)
        self.assertEqual(facets.layer, "UI")
        self.assertEqual(facets.role, "Presenter")
        self.assertEqual(facets.custom_val, 42)
        self.assertEqual(facets.non_existent, "Unknown")

        # Mutability
        facets.layer = "Domain"
        self.assertEqual(facets.layer, "Domain")

    def test_semantic_facets_new_default(self):
        f_config = FieldConfig(default="DefaultRole")
        dir_cfg = DirectoryConfig(manual_fields={"role": f_config}, automatic_fields={})
        ontology = OntologyConfig(directories={"src/ui": dir_cfg})

        facets = SemanticFacets.new_default(ontology)
        self.assertEqual(facets.role, "DefaultRole")

    def test_ontology_validation_success(self):
        f_config = FieldConfig(values=["A", "B"], default="A")
        dir_cfg = DirectoryConfig(manual_fields={"layer": f_config}, automatic_fields={})
        ontology = OntologyConfig(directories={"src/": dir_cfg})
        
        # Should not raise ValueError
        ontology.validate()

    def test_ontology_validation_manual_field_with_rules(self):
        rule = MetadataAssignmentRule(value="A", conditions=AssignmentCondition())
        f_config = FieldConfig(assignment_rules=[rule])
        dir_cfg = DirectoryConfig(manual_fields={"layer": f_config}, automatic_fields={})
        ontology = OntologyConfig(directories={"src/": dir_cfg})

        with self.assertRaises(ValueError) as context:
            ontology.validate()
        self.assertIn("cannot have assignment rules", str(context.exception))

    def test_ontology_validation_invalid_default(self):
        f_config = FieldConfig(values=["A", "B"], default="C")
        dir_cfg = DirectoryConfig(manual_fields={"layer": f_config}, automatic_fields={})
        ontology = OntologyConfig(directories={"src/": dir_cfg})

        with self.assertRaises(ValueError) as context:
            ontology.validate()
        self.assertIn("must be one of its allowed values", str(context.exception))

    def test_ontology_get_directory_config_for_file(self):
        dir_cfg_root = DirectoryConfig()
        dir_cfg_ui = DirectoryConfig()
        ontology = OntologyConfig(directories={".": dir_cfg_root, "src/ui": dir_cfg_ui})

        self.assertEqual(ontology.get_directory_config_for_file("src/ui/main.rs"), dir_cfg_ui)
        self.assertEqual(ontology.get_directory_config_for_file("src/main.rs"), dir_cfg_root)
        self.assertEqual(ontology.get_directory_config_for_file("other/main.rs"), dir_cfg_root)

    def test_ontology_get_fields_for_file(self):
        manual_f = FieldConfig(default="man")
        auto_f = FieldConfig(default="auto")
        dir_cfg = DirectoryConfig(manual_fields={"m": manual_f}, automatic_fields={"a": auto_f})
        ontology = OntologyConfig(directories={"src": dir_cfg})

        manual_fields = ontology.get_manual_fields_for_file("src/main.rs")
        self.assertEqual(manual_fields, {"m": manual_f})

        automatic_fields = ontology.get_automatic_fields_for_file("src/main.rs")
        self.assertEqual(automatic_fields, {"a": auto_f})

        fields = ontology.get_fields_for_file("src/main.rs")
        self.assertEqual(fields, {"m": manual_f, "a": auto_f})

if __name__ == "__main__":
    unittest.main()
