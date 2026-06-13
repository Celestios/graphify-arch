import unittest
import sys
import json
import tempfile
import shutil
from pathlib import Path

# Add src to system path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from project import ConfigLoader, Workspace
from schema import ProjectConfig

class TestProject(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_find_config_not_found(self):
        start_dir = self.temp_dir / "a" / "b" / "c"
        start_dir.mkdir(parents=True)
        
        config_path, db_path = ConfigLoader.find_config(start_dir)
        
        # Should fallback to start_dir / "graphify-out" / "arch" / ...
        expected_dir = start_dir / "graphify-out" / "arch"
        self.assertEqual(config_path, expected_dir / "config.json")
        self.assertEqual(db_path, expected_dir / "graph.db")

    def test_find_config_found(self):
        root_dir = self.temp_dir
        arch_dir = root_dir / "graphify-out" / "arch"
        arch_dir.mkdir(parents=True)
        
        config_file = arch_dir / "config.json"
        config_file.touch()
        
        start_dir = root_dir / "a" / "b"
        start_dir.mkdir(parents=True)
        
        config_path, db_path = ConfigLoader.find_config(start_dir)
        
        self.assertEqual(config_path, config_file)
        self.assertEqual(db_path, arch_dir / "graph.db")

    def test_load_flat_format(self):
        config_data = {
            "ontology": {
                "layers": ["UI", "Core"],
                "default_layer": "Core",
                "purities": {"Impure": 0, "Pure": 1},
                "default_purity": "Impure",
                "roles": ["Presenter", "Entity"],
                "barriers": ["Presenter"],
                "rules": [
                    {
                        "name": "layer_rule",
                        "message": "Presenter cannot call DB",
                        "source_layer": "UI",
                        "target_layer": "Core"
                    }
                ],
                "metadata_fields": {
                    "layer": {
                        "default": "Core",
                        "reset_on_change": True,
                        "allowed_values": ["UI", "Core"],
                        "assignment_rules": [
                            {
                                "value": "UI",
                                "conditions": {"path_prefix": "src/ui"}
                            }
                        ]
                    }
                }
            }
        }
        
        config_file = self.temp_dir / "config.json"
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config_data, f)
            
        config = ConfigLoader.load(config_file)
        self.assertIsInstance(config, ProjectConfig)
        self.assertEqual(config.ontology.layers, ["UI", "Core"])
        self.assertEqual(config.ontology.default_layer, "Core")
        self.assertIn("layer", config.ontology.metadata_fields)
        self.assertEqual(config.ontology.metadata_fields["layer"].default, "Core")
        self.assertEqual(len(config.ontology.metadata_fields["layer"].assignment_rules), 1)

    def test_load_directory_format(self):
        config_data = {
            "src/ui": {
                "manual_fields": {
                    "layer": {
                        "values": ["UI", "Domain"],
                        "default": "UI",
                        "reset_on_change": False
                    }
                },
                "automatic_fields": {
                    "role": {
                        "values": ["Presenter", "View"],
                        "default": "View",
                        "assignment_rules": [
                            {
                                "value": "Presenter",
                                "conditions": {"class_suffix": "Presenter"}
                            }
                        ]
                    }
                }
            }
        }
        
        config_file = self.temp_dir / "config.json"
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config_data, f)
            
        config = ConfigLoader.load(config_file)
        self.assertIsInstance(config, ProjectConfig)
        self.assertIn("src/ui", config.ontology.directories)
        dir_cfg = config.ontology.directories["src/ui"]
        self.assertIn("layer", dir_cfg.manual_fields)
        self.assertIn("role", dir_cfg.automatic_fields)
        self.assertEqual(dir_cfg.manual_fields["layer"].default, "UI")
        self.assertEqual(dir_cfg.automatic_fields["role"].default, "View")

if __name__ == "__main__":
    unittest.main()
