import json
import os
from pathlib import Path
from typing import Tuple, Dict, Any, List
from schema import (
    ProjectConfig,
    OntologyConfig,
    ArchitecturalRule,
    MetadataFieldConfig,
    MetadataAssignmentRule,
    AssignmentCondition,
    FieldConfig,
    DirectoryConfig
)

class ConfigLoader:
    """Handles discovery, reading, parsing, and validation of project configurations."""
    
    @staticmethod
    def find_config(start_dir: Path) -> Tuple[Path, Path]:
        """
        Recursively walks up parent directories to locate config files.
        Returns a tuple of (config_path, db_path).
        """
        current_dir = start_dir
        while True:
            arch_dir = current_dir / "graphify-out" / "arch"
            config_path = arch_dir / "config.json"
            db_path = arch_dir / "graph.db"
            
            if config_path.exists():
                return config_path, db_path

            parent_dir = current_dir.parent
            if parent_dir == current_dir:
                expected_dir = start_dir / "graphify-out" / "arch"
                return expected_dir / "config.json", expected_dir / "graph.db"
            current_dir = parent_dir

    @staticmethod
    def _parse_field_config(name: str, f_data: dict) -> FieldConfig:
        from schema import AssignmentCondition, MetadataAssignmentRule, FieldRuleConfig, FieldConfig
        assignment_rules = []
        for rule_data in f_data.get("assignment_rules", []):
            conds_data = rule_data.get("conditions", {})
            conds = AssignmentCondition(
                path_prefix=conds_data.get("path_prefix"),
                class_suffix=conds_data.get("class_suffix"),
                class_contains=conds_data.get("class_contains"),
                name_contains=conds_data.get("name_contains"),
                imports_prefix=conds_data.get("imports_prefix"),
                calls_prefix=conds_data.get("calls_prefix")
            )
            assignment_rules.append(
                MetadataAssignmentRule(
                    value=rule_data["value"],
                    conditions=conds
                )
            )
            
        rules = []
        for r in f_data.get("rules", []):
            rules.append(FieldRuleConfig(
                source=r["source"],
                target=r["target"],
                message=r["message"],
                severity=r.get("severity", "error")
            ))
            
        return FieldConfig(
            values=f_data.get("values"),
            default=f_data.get("default"),
            reset_on_change=f_data.get("reset_on_change", False),
            assignment_rules=assignment_rules,
            handler=f_data.get("handler"),
            rules=rules,
            barriers=f_data.get("barriers", []),
            barrier_field=f_data.get("barrier_field", "architectural_role"),
            weights=f_data.get("weights", {})
        )

    @classmethod
    def load(cls, config_path: Path) -> ProjectConfig:
        """Loads and parses the project configuration file."""
        from schema import FieldConfig, FieldRuleConfig, DirectoryConfig, OntologyConfig, ProjectConfig
        try:
            with open(config_path, "r", encoding="utf-8-sig") as f:
                content = f.read()
        except Exception as e:
            raise RuntimeError(f"Failed to read config file {config_path}: {e}")

        try:
            data = json.loads(content)
            
            # Determine format
            is_directory_format = False
            for k, v in data.items():
                if isinstance(v, dict):
                    if "manual_fields" in v or "automatic_fields" in v:
                        is_directory_format = True
                        break
                    for fk, fv in v.items():
                        if isinstance(fv, dict) and ("default" in fv or "values" in fv):
                            is_directory_format = True
                            break
                if is_directory_format:
                    break

            if is_directory_format:
                directories = {}
                for dir_name, fields_data in data.items():
                    if not isinstance(fields_data, dict):
                        continue
                    
                    manual_fields_data = fields_data.get("manual_fields", {})
                    automatic_fields_data = fields_data.get("automatic_fields", {})
                    
                    # Fallback for old flat format
                    if not manual_fields_data and not automatic_fields_data:
                        automatic_fields_data = fields_data
                    
                    manual_fields = {}
                    for name, f_data in manual_fields_data.items():
                        if isinstance(f_data, dict):
                            manual_fields[name] = cls._parse_field_config(name, f_data)
                            
                    automatic_fields = {}
                    for name, f_data in automatic_fields_data.items():
                        if isinstance(f_data, dict):
                            automatic_fields[name] = cls._parse_field_config(name, f_data)
                            
                    directories[dir_name] = DirectoryConfig(
                        manual_fields=manual_fields,
                        automatic_fields=automatic_fields
                    )
                ontology = OntologyConfig(directories=directories)
            else:
                ontology_data = data.get("ontology", data)
                
                rules_data = ontology_data.get("rules", [])
                rules = [
                    ArchitecturalRule(
                        name=r["name"],
                        message=r["message"],
                        source_layer=r.get("source_layer"),
                        target_layer=r.get("target_layer"),
                        target_purity=r.get("target_purity")
                    )
                    for r in rules_data
                ]
                
                metadata_fields_data = ontology_data.get("metadata_fields", {})
                metadata_fields = {}
                for name, field_data in metadata_fields_data.items():
                    assignment_rules = []
                    for rule_data in field_data.get("assignment_rules", []):
                        conds_data = rule_data.get("conditions", {})
                        conds = AssignmentCondition(
                            path_prefix=conds_data.get("path_prefix"),
                            class_suffix=conds_data.get("class_suffix"),
                            class_contains=conds_data.get("class_contains"),
                            name_contains=conds_data.get("name_contains"),
                            imports_prefix=conds_data.get("imports_prefix"),
                            calls_prefix=conds_data.get("calls_prefix")
                        )
                        assignment_rules.append(
                            MetadataAssignmentRule(
                                value=rule_data["value"],
                                conditions=conds
                            )
                        )
                    metadata_fields[name] = MetadataFieldConfig(
                        default=field_data["default"],
                        reset_on_change=field_data.get("reset_on_change", True),
                        allowed_values=field_data.get("allowed_values"),
                        assignment_rules=assignment_rules
                    )

                ontology = OntologyConfig(
                    layers=ontology_data.get("layers", []),
                    default_layer=ontology_data.get("default_layer", ""),
                    purities=ontology_data.get("purities", {}),
                    default_purity=ontology_data.get("default_purity", ""),
                    roles=ontology_data.get("roles", []),
                    barriers=ontology_data.get("barriers", []),
                    rules=rules,
                    layer_assignments=ontology_data.get("layer_assignments", {}),
                    metadata_fields=metadata_fields
                )
            
            config = ProjectConfig(ontology=ontology)
        except Exception as e:
            raise RuntimeError(f"Invalid configuration schema inside {config_path}: {e}")

        # Parametric constraint validation
        try:
            config.ontology.validate()
        except ValueError as e:
            raise RuntimeError(str(e))

        return config



class Workspace:
    def __init__(self, root_dir: Path, db_path: Path, config_path: Path, config: ProjectConfig):
        self.root_dir = root_dir
        self.db_path = db_path
        self.config_path = config_path
        self.config = config

    @classmethod
    def discover(cls) -> "Workspace":
        """
        Recursively walks up from the current directory to locate 
        the config and bootstrap workspace context.
        """
        try:
            current_dir = Path.cwd().resolve()
        except Exception as e:
            raise RuntimeError(f"Failed to read current execution context directory: {e}")

        config_path, db_path = ConfigLoader.find_config(current_dir)
        config = ConfigLoader.load(config_path)

        return cls(
            root_dir=config_path.parent.parent.parent,
            db_path=db_path,
            config_path=config_path,
            config=config
        )

    def is_excluded(self, filepath: str) -> bool:
        """Tests if a file path matches graphify ignore patterns or noise directories."""
        from graphify.detect import _is_ignored, _is_noise_dir, _SKIP_FILES, _load_graphifyignore

        path = Path(filepath).resolve()

        try:
            rel_parts = path.relative_to(self.root_dir).parts
        except ValueError:
            rel_parts = path.parts

        parent_path = self.root_dir
        for part in rel_parts[:-1]:
            if _is_noise_dir(part, parent_path):
                return True
            parent_path = parent_path / part

        if path.name in _SKIP_FILES:
            return True

        if not hasattr(self, "ignore_patterns"):
            self.ignore_patterns = _load_graphifyignore(self.root_dir)

        return _is_ignored(path, self.root_dir, self.ignore_patterns)