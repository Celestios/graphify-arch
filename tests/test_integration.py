"""Integration tests for the full metadata assignment pipeline.

Covers: config parsing → rule matching → graph annotation → DB persistence → query.
Tests use a realistic config.json matching the mycelium project structure.
"""
import unittest
import sys
import tempfile
import shutil
import json
from pathlib import Path
import networkx as nx

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from schema import (
    AstNodeType, CodeNode, SemanticFacets, AssignmentCondition,
    MetadataAssignmentRule, FieldConfig, DirectoryConfig, OntologyConfig
)
from project import ConfigLoader
from db import Database
from propagator import propagate_metadata
from auditor import audit_architecture_rules


MYCELIUM_CONFIG = {
    "lib": {
        "manual_fields": {
            "ai_summary": {"default": "", "reset_on_change": True},
            "pattern": {
                "values": ["Notifier", "Controller", "Service", "Repository", "Model", "Widget", "None"],
                "default": "None",
                "reset_on_change": False
            }
        },
        "automatic_fields": {
            "layer": {
                "values": ["Presentation", "Application", "Domain", "Infrastructure", "Bridge", "Shared", "EntryPoint"],
                "default": "Domain",
                "assignment_rules": [
                    {"value": "EntryPoint", "conditions": {"file_name": "main.dart"}},
                    {"value": "Presentation", "conditions": {"path_prefix": "lib/presentation"}},
                    {"value": "Presentation", "conditions": {"path_prefix": "lib/features/graph/ui"}},
                    {"value": "Presentation", "conditions": {"path_prefix": "lib/features/graph/presentation"}},
                    {"value": "Presentation", "conditions": {"path_prefix": "lib/features/workspace/ui"}},
                    {"value": "Application", "conditions": {"path_prefix": "lib/features/graph/store"}},
                    {"value": "Bridge", "conditions": {"path_prefix": "lib/src/rust"}},
                    {"value": "Infrastructure", "conditions": {"path_prefix": "lib/infrastructure"}},
                    {"value": "Shared", "conditions": {"path_prefix": "lib/shared"}},
                    {"value": "Domain", "conditions": {"path_prefix": "lib/features/graph/engine"}},
                    {"value": "Domain", "conditions": {"path_prefix": "lib/features/graph/models"}}
                ],
                "handler": "dependency_check",
                "rules": [
                    {"source": "Presentation", "target": "Infrastructure", "message": "Presentation -> Infrastructure"},
                    {"source": "Application", "target": "Presentation", "message": "Application -> Presentation"},
                    {"source": "Domain", "target": "Presentation", "message": "Domain -> Presentation"},
                    {"source": "Domain", "target": "Application", "message": "Domain -> Application"},
                    {"source": "Domain", "target": "Infrastructure", "message": "Domain -> Infrastructure"},
                    {"source": "Domain", "target": "Bridge", "message": "Domain -> Bridge"},
                    {"source": "Infrastructure", "target": "Presentation", "message": "Infrastructure -> Presentation"},
                    {"source": "Infrastructure", "target": "Application", "message": "Infrastructure -> Application"}
                ]
            },
            "tier": {
                "values": [0, 1, 2, 3],
                "default": 3,
                "assignment_rules": [
                    {"value": 0, "conditions": {"file_name": "main.dart"}},
                    {"value": 1, "conditions": {"path_prefix": "lib/presentation"}},
                    {"value": 1, "conditions": {"path_prefix": "lib/features/graph/ui"}},
                    {"value": 1, "conditions": {"path_prefix": "lib/features/graph/presentation"}},
                    {"value": 1, "conditions": {"path_prefix": "lib/features/workspace/ui"}},
                    {"value": 1, "conditions": {"path_prefix": "lib/src/rust"}},
                    {"value": 2, "conditions": {"path_prefix": "lib/features/graph/store"}}
                ],
                "handler": "dependency_check",
                "rules": [
                    {"source": 3, "target": 2, "message": "Tier 3 -> Tier 2"},
                    {"source": 3, "target": 1, "message": "Tier 3 -> Tier 1"},
                    {"source": 2, "target": 1, "message": "Tier 2 -> Tier 1"}
                ]
            },
            "architectural_role": {
                "values": ["Repository", "Controller", "Service", "Utility"],
                "default": "Utility",
                "assignment_rules": [
                    {"value": "Controller", "conditions": {"class_contains": "Controller"}},
                    {"value": "Repository", "conditions": {"path_prefix": "lib/infrastructure"}}
                ]
            }
        }
    }
}


class TestConfigParsing(unittest.TestCase):
    """Tests for config loading and condition parsing."""

    def setUp(self):
        self.tmp = tempfile.mkstemp(suffix=".json")
        self.fd, self.path = self.tmp

    def tearDown(self):
        os_close_fd(self.fd)
        Path(self.path).unlink(missing_ok=True)

    def test_load_directory_format_config(self):
        with open(self.path, "w") as f:
            json.dump(MYCELIUM_CONFIG, f)
        config = ConfigLoader.load(Path(self.path))
        ontology = config.ontology

        self.assertIn("lib", ontology.directories)
        lib_cfg = ontology.directories["lib"]
        self.assertIn("layer", lib_cfg.automatic_fields)
        self.assertIn("tier", lib_cfg.automatic_fields)
        self.assertIn("architectural_role", lib_cfg.automatic_fields)

    def test_file_name_condition_parsed(self):
        with open(self.path, "w") as f:
            json.dump(MYCELIUM_CONFIG, f)
        config = ConfigLoader.load(Path(self.path))
        layer_rules = config.ontology.directories["lib"].automatic_fields["layer"].assignment_rules

        first_rule = layer_rules[0]
        self.assertEqual(first_rule.value, "EntryPoint")
        self.assertEqual(first_rule.conditions.file_name, "main.dart")
        self.assertIsNone(first_rule.conditions.path_prefix)

    def test_path_prefix_condition_parsed(self):
        with open(self.path, "w") as f:
            json.dump(MYCELIUM_CONFIG, f)
        config = ConfigLoader.load(Path(self.path))
        layer_rules = config.ontology.directories["lib"].automatic_fields["layer"].assignment_rules

        store_rule = next(r for r in layer_rules if r.value == "Application")
        self.assertEqual(store_rule.conditions.path_prefix, "lib/features/graph/store")

    def test_class_contains_condition_parsed(self):
        with open(self.path, "w") as f:
            json.dump(MYCELIUM_CONFIG, f)
        config = ConfigLoader.load(Path(self.path))
        role_rules = config.ontology.directories["lib"].automatic_fields["architectural_role"].assignment_rules

        controller_rule = next(r for r in role_rules if r.value == "Controller")
        self.assertEqual(controller_rule.conditions.class_contains, "Controller")

    def test_all_condition_types_supported(self):
        cond = AssignmentCondition(
            file_name="main.dart",
            path_prefix="lib/features",
            class_suffix="State",
            class_contains="Controller",
            name_contains="build",
            imports_prefix="package:flutter",
            calls_prefix="setState"
        )
        self.assertEqual(cond.file_name, "main.dart")
        self.assertEqual(cond.path_prefix, "lib/features")
        self.assertEqual(cond.class_suffix, "State")
        self.assertEqual(cond.class_contains, "Controller")
        self.assertEqual(cond.name_contains, "build")
        self.assertEqual(cond.imports_prefix, "package:flutter")
        self.assertEqual(cond.calls_prefix, "setState")


class TestRuleMatching(unittest.TestCase):
    """Tests for the assignment rule matching logic used in on_post_build."""

    def _match_rule(self, rule, rel_source_file, node_label=""):
        """Replicate the matching logic from plugin.py on_post_build."""
        conds = rule.conditions
        match = True

        if conds.path_prefix is not None:
            prefix = conds.path_prefix
            if not (rel_source_file == prefix or rel_source_file.startswith(prefix + "/")):
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
        return match

    def setUp(self):
        self.tmp = tempfile.mkstemp(suffix=".json")
        self.fd, self.path = self.tmp
        with open(self.path, "w") as f:
            json.dump(MYCELIUM_CONFIG, f)
        self.config = ConfigLoader.load(Path(self.path))
        self.layer_rules = self.config.ontology.directories["lib"].automatic_fields["layer"].assignment_rules

    def tearDown(self):
        os_close_fd(self.fd)
        Path(self.path).unlink(missing_ok=True)

    def test_file_name_matches_only_main_dart(self):
        rule = self.layer_rules[0]  # EntryPoint rule
        self.assertTrue(self._match_rule(rule, "lib/main.dart"))
        self.assertTrue(self._match_rule(rule, "lib/features/graph/main.dart"))
        self.assertFalse(self._match_rule(rule, "lib/features/graph/store/graph_data_controller.dart"))
        self.assertFalse(self._match_rule(rule, "lib/main.dart.bak"))

    def test_path_prefix_matches_store(self):
        store_rule = next(r for r in self.layer_rules if r.value == "Application")
        self.assertTrue(self._match_rule(store_rule, "lib/features/graph/store/graph_data_controller.dart"))
        self.assertTrue(self._match_rule(store_rule, "lib/features/graph/store/sub/nested.dart"))
        self.assertFalse(self._match_rule(store_rule, "lib/features/graph/engine/interactor.dart"))
        self.assertFalse(self._match_rule(store_rule, "lib/features/graph/storex/fake.dart"))

    def test_path_prefix_matches_engine(self):
        engine_rules = [r for r in self.layer_rules if r.value == "Domain"]
        engine_rule = next(r for r in engine_rules if r.conditions.path_prefix == "lib/features/graph/engine")
        self.assertTrue(self._match_rule(engine_rule, "lib/features/graph/engine/base_interaction_state.dart"))
        self.assertFalse(self._match_rule(engine_rule, "lib/features/graph/store/graph_data.dart"))

    def test_path_prefix_matches_presentation(self):
        pres_rules = [r for r in self.layer_rules if r.value == "Presentation"]
        self.assertTrue(self._match_rule(pres_rules[0], "lib/presentation/home.dart"))
        self.assertTrue(self._match_rule(pres_rules[1], "lib/features/graph/ui/canvas.dart"))
        self.assertTrue(self._match_rule(pres_rules[2], "lib/features/graph/presentation/widget.dart"))
        self.assertTrue(self._match_rule(pres_rules[3], "lib/features/workspace/ui/panel.dart"))
        self.assertFalse(self._match_rule(pres_rules[0], "lib/features/graph/store/data.dart"))

    def test_no_rule_matches_uses_default(self):
        path = "lib/random/unknown/path.dart"
        matched = False
        for rule in self.layer_rules:
            if self._match_rule(rule, path):
                matched = True
                break
        self.assertFalse(matched)

    def test_first_matching_rule_wins(self):
        path = "lib/main.dart"
        for rule in self.layer_rules:
            if self._match_rule(rule, path):
                self.assertEqual(rule.value, "EntryPoint")
                break

    def test_class_contains_matches_controller(self):
        role_rules = self.config.ontology.directories["lib"].automatic_fields["architectural_role"].assignment_rules
        ctrl_rule = next(r for r in role_rules if r.value == "Controller")
        self.assertTrue(self._match_rule(ctrl_rule, "lib/store/data.dart", node_label="GraphDataController"))
        self.assertFalse(self._match_rule(ctrl_rule, "lib/store/data.dart", node_label="GraphDataQuery"))
        self.assertFalse(self._match_rule(ctrl_rule, "lib/store/data.dart", node_label="GraphService"))


class TestSyncGraphMetadata(unittest.TestCase):
    """Tests for Database.sync_graph_metadata persisting to DB."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.db_path = self.temp_dir / "graph.db"
        self.db = Database(str(self.db_path))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.temp_dir)

    def _insert_node(self, node_id, filepath="lib/store/data.dart"):
        node = CodeNode(
            id=node_id, filepath=filepath,
            node_type=AstNodeType.FUNCTION,
            start_byte=0, end_byte=100, ast_hash="h1", raw_code="",
            semantics=SemanticFacets()
        )
        self.db.sync_nodes(filepath, [node])

    def test_persists_all_fields(self):
        self._insert_node("n1")
        G = nx.MultiDiGraph()
        G.add_node("n1", source_file="lib/store/data.dart",
                    arch_meta_layer="Application", arch_meta_tier=2,
                    arch_meta_purity="IoBound", arch_meta_architectural_role="Controller",
                    arch_meta_pattern="Notifier", arch_meta_ai_summary="Summary text")

        self.db.sync_graph_metadata(G, self.temp_dir)

        node = self.db.get_node("n1")
        self.assertEqual(node.semantics.fields.get("layer"), "Application")
        self.assertEqual(node.semantics.fields.get("tier"), 2)
        self.assertEqual(node.semantics.fields.get("purity"), "IoBound")
        self.assertEqual(node.semantics.fields.get("architectural_role"), "Controller")
        self.assertEqual(node.semantics.fields.get("pattern"), "Notifier")
        self.assertEqual(node.ai_summary, "Summary text")

    def test_skips_unknown_values(self):
        self._insert_node("n1")
        G = nx.MultiDiGraph()
        G.add_node("n1", source_file="lib/store/data.dart",
                    arch_meta_layer="Application", arch_meta_tier=2,
                    arch_meta_purity="Unknown", arch_meta_architectural_role="Unknown")

        self.db.sync_graph_metadata(G, self.temp_dir)

        node = self.db.get_node("n1")
        self.assertEqual(node.semantics.fields.get("layer"), "Application")
        self.assertEqual(node.semantics.fields.get("tier"), 2)
        self.assertNotIn("purity", node.semantics.fields)
        self.assertNotIn("architectural_role", node.semantics.fields)

    def test_skips_nodes_not_in_db(self):
        G = nx.MultiDiGraph()
        G.add_node("nonexistent", source_file="lib/ghost.dart",
                    arch_meta_layer="Domain")
        self.db.sync_graph_metadata(G, self.temp_dir)

    def test_bulk_update_many_nodes(self):
        for i in range(50):
            self._insert_node(f"node_{i}", f"lib/file_{i}.dart")

        G = nx.MultiDiGraph()
        for i in range(50):
            G.add_node(f"node_{i}", source_file=f"lib/file_{i}.dart",
                       arch_meta_layer="Domain" if i < 25 else "Application",
                       arch_meta_tier=3 if i < 25 else 2)

        self.db.sync_graph_metadata(G, self.temp_dir)

        for i in range(50):
            node = self.db.get_node(f"node_{i}")
            expected_layer = "Domain" if i < 25 else "Application"
            expected_tier = 3 if i < 25 else 2
            self.assertEqual(node.semantics.fields.get("layer"), expected_layer,
                             f"node_{i} layer mismatch")
            self.assertEqual(node.semantics.fields.get("tier"), expected_tier,
                             f"node_{i} tier mismatch")

    def test_idempotent_sync(self):
        self._insert_node("n1")
        G = nx.MultiDiGraph()
        G.add_node("n1", source_file="lib/store/data.dart",
                    arch_meta_layer="Application", arch_meta_tier=2)

        self.db.sync_graph_metadata(G, self.temp_dir)
        self.db.sync_graph_metadata(G, self.temp_dir)

        node = self.db.get_node("n1")
        self.assertEqual(node.semantics.fields.get("layer"), "Application")
        self.assertEqual(node.semantics.fields.get("tier"), 2)


class TestEndToEndPipeline(unittest.TestCase):
    """End-to-end: config → rule matching → graph annotation → DB persist → read back."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.db_path = self.temp_dir / "graph.db"
        self.db = Database(str(self.db_path))
        self.tmp = tempfile.mkstemp(suffix=".json")
        self.fd, self.config_path = self.tmp
        with open(self.config_path, "w") as f:
            json.dump(MYCELIUM_CONFIG, f)
        self.config = ConfigLoader.load(Path(self.config_path))
        self.ontology = self.config.ontology

    def tearDown(self):
        self.db.close()
        os_close_fd(self.fd)
        Path(self.config_path).unlink(missing_ok=True)
        shutil.rmtree(self.temp_dir)

    def _simulate_on_post_build(self, G):
        """Simulate the assignment logic from plugin.py on_post_build."""
        for nid, data in G.nodes(data=True):
            source_file = data.get("source_file") or ""
            node_label = data.get("label") or ""

            automatic_cfg = self.ontology.get_automatic_fields_for_file(source_file)
            for field_name, field_config in automatic_cfg.items():
                assigned_value = field_config.default
                for rule in field_config.assignment_rules:
                    conds = rule.conditions
                    match = True
                    if conds.path_prefix is not None:
                        if not source_file.startswith(conds.path_prefix):
                            match = False
                    if conds.file_name is not None:
                        if Path(source_file).name != conds.file_name:
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
                    if match:
                        assigned_value = rule.value
                        break
                data[f"arch_meta_{field_name}"] = assigned_value

    def _make_node(self, node_id, filepath, label=None):
        node = CodeNode(
            id=node_id, filepath=filepath,
            node_type=AstNodeType.FUNCTION,
            start_byte=0, end_byte=100, ast_hash="h", raw_code="",
            semantics=SemanticFacets()
        )
        self.db.sync_nodes(filepath, [node])
        return node_id

    def test_store_file_gets_application_layer(self):
        nid = self._make_node("store_ctrl", "lib/features/graph/store/graph_data_controller.dart")
        G = nx.MultiDiGraph()
        G.add_node(nid, source_file="lib/features/graph/store/graph_data_controller.dart",
                   label="GraphDataController")

        self._simulate_on_post_build(G)

        self.assertEqual(G.nodes[nid].get("arch_meta_layer"), "Application")
        self.assertEqual(G.nodes[nid].get("arch_meta_tier"), 2)

    def test_engine_file_gets_domain_layer(self):
        nid = self._make_node("engine_state", "lib/features/graph/engine/base_interaction_state.dart")
        G = nx.MultiDiGraph()
        G.add_node(nid, source_file="lib/features/graph/engine/base_interaction_state.dart")

        self._simulate_on_post_build(G)

        self.assertEqual(G.nodes[nid].get("arch_meta_layer"), "Domain")
        self.assertEqual(G.nodes[nid].get("arch_meta_tier"), 3)

    def test_main_dart_gets_entrypoint(self):
        nid = self._make_node("main", "lib/main.dart")
        G = nx.MultiDiGraph()
        G.add_node(nid, source_file="lib/main.dart")

        self._simulate_on_post_build(G)

        self.assertEqual(G.nodes[nid].get("arch_meta_layer"), "EntryPoint")
        self.assertEqual(G.nodes[nid].get("arch_meta_tier"), 0)

    def test_controller_class_gets_role(self):
        nid = self._make_node("ctrl", "lib/store/data.dart")
        G = nx.MultiDiGraph()
        G.add_node(nid, source_file="lib/features/graph/store/data.dart",
                   label="GraphDataController")

        self._simulate_on_post_build(G)

        self.assertEqual(G.nodes[nid].get("arch_meta_architectural_role"), "Controller")

    def test_full_pipeline_persists_to_db(self):
        nid = self._make_node("store_ctrl", "lib/features/graph/store/graph_data_controller.dart")
        G = nx.MultiDiGraph()
        G.add_node(nid, source_file="lib/features/graph/store/graph_data_controller.dart",
                   label="GraphDataController")

        self._simulate_on_post_build(G)
        self.db.sync_graph_metadata(G, self.temp_dir)

        node = self.db.get_node(nid)
        self.assertEqual(node.semantics.fields.get("layer"), "Application")
        self.assertEqual(node.semantics.fields.get("tier"), 2)
        self.assertEqual(node.semantics.fields.get("architectural_role"), "Controller")

    def test_violation_detection_after_assignment(self):
        store_id = self._make_node("store", "lib/features/graph/store/data.dart")
        engine_id = self._make_node("engine", "lib/features/graph/engine/logic.dart")

        G = nx.MultiDiGraph()
        G.add_node(store_id, source_file="lib/features/graph/store/data.dart",
                   arch_meta_layer="Application", arch_meta_tier=2)
        G.add_node(engine_id, source_file="lib/features/graph/engine/logic.dart",
                   arch_meta_layer="Domain", arch_meta_tier=3)
        G.add_edge(engine_id, store_id, type="calls")

        violations = audit_architecture_rules(G, self.ontology, self.temp_dir)

        domain_to_app = [v for v in violations if "Domain -> Application" in v.message]
        self.assertTrue(len(domain_to_app) > 0,
                        "Expected Domain->Application violation for engine calling store")

    def test_multiple_files_different_layers(self):
        nodes = {
            "main": ("lib/main.dart", None),
            "store": ("lib/features/graph/store/data.dart", "GraphDataController"),
            "engine": ("lib/features/graph/engine/logic.dart", None),
            "infra": ("lib/infrastructure/db.dart", None),
            "shared": ("lib/shared/utils.dart", None),
            "ui": ("lib/features/graph/ui/canvas.dart", None),
        }
        G = nx.MultiDiGraph()
        for nid, (fp, label) in nodes.items():
            self._make_node(nid, fp)
            data = {"source_file": fp}
            if label:
                data["label"] = label
            G.add_node(nid, **data)

        self._simulate_on_post_build(G)

        self.assertEqual(G.nodes["main"].get("arch_meta_layer"), "EntryPoint")
        self.assertEqual(G.nodes["store"].get("arch_meta_layer"), "Application")
        self.assertEqual(G.nodes["engine"].get("arch_meta_layer"), "Domain")
        self.assertEqual(G.nodes["infra"].get("arch_meta_layer"), "Infrastructure")
        self.assertEqual(G.nodes["shared"].get("arch_meta_layer"), "Shared")
        self.assertEqual(G.nodes["ui"].get("arch_meta_layer"), "Presentation")

    def test_tier_values_match_layer(self):
        nodes = {
            "main": "lib/main.dart",
            "store": "lib/features/graph/store/data.dart",
            "engine": "lib/features/graph/engine/logic.dart",
        }
        G = nx.MultiDiGraph()
        for nid, fp in nodes.items():
            self._make_node(nid, fp)
            G.add_node(nid, source_file=fp)

        self._simulate_on_post_build(G)

        self.assertEqual(G.nodes["main"].get("arch_meta_tier"), 0)
        self.assertEqual(G.nodes["store"].get("arch_meta_tier"), 2)
        self.assertEqual(G.nodes["engine"].get("arch_meta_tier"), 3)


def os_close_fd(fd):
    """Close a file descriptor safely."""
    try:
        import os
        os.close(fd)
    except OSError:
        pass


if __name__ == "__main__":
    unittest.main()
