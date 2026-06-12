import json
import os
from pathlib import Path
from schema import (
    ProjectConfig,
    OntologyConfig,
    ArchitecturalRule,
    MetadataFieldConfig,
    MetadataAssignmentRule,
    AssignmentCondition
)

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
        the config (.graphify/arch.json or .celial/celial.json).
        """
        try:
            current_dir = Path.cwd().resolve()
        except Exception as e:
            raise RuntimeError(f"Failed to read current execution context directory: {e}")

        while True:
            # Check for .graphify/arch.json first
            dot_graphify = current_dir / ".graphify"
            config_path = dot_graphify / "arch.json"
            db_path = dot_graphify / "graph.db"
            
            if not config_path.exists():
                dot_celial = current_dir / ".celial"
                config_path = dot_celial / "celial.json"
                db_path = dot_celial / "graph.db"

            if config_path.exists():
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        content = f.read()
                except Exception as e:
                    raise RuntimeError(f"Failed to read config file {config_path}: {e}")

                try:
                    data = json.loads(content)
                    
                    # Support both top-level ontology (old config format) and nested ontology
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
                            reset_on_hash_change=field_data.get("reset_on_hash_change", True),
                            allowed_values=field_data.get("allowed_values"),
                            assignment_rules=assignment_rules
                        )

                    ontology = OntologyConfig(
                        layers=ontology_data["layers"],
                        default_layer=ontology_data["default_layer"],
                        purities=ontology_data["purities"],
                        default_purity=ontology_data["default_purity"],
                        roles=ontology_data["roles"],
                        barriers=ontology_data["barriers"],
                        rules=rules,
                        layer_assignments=ontology_data.get("layer_assignments", {}),
                        metadata_fields=metadata_fields
                    )
                    
                    config = ProjectConfig(
                        ontology=ontology
                    )
                except Exception as e:
                    raise RuntimeError(f"Invalid configuration schema inside {config_path}: {e}")

                # Parametric constraint validation
                try:
                    config.ontology.validate()
                except ValueError as e:
                    raise RuntimeError(str(e))

                return cls(
                    root_dir=current_dir,
                    db_path=db_path,
                    config_path=config_path,
                    config=config
                )

            # Replicating Rust's path traversal termination condition
            parent_dir = current_dir.parent
            if parent_dir == current_dir:
                raise RuntimeError(
                    "Fatal: Not a valid workspace. No '.graphify/arch.json' or '.celial/celial.json' anchor discovered "
                    "in this or parent directories. Run 'arch_indexer init' to bootstrap this folder."
                )
            current_dir = parent_dir

    def is_excluded(self, filepath: str) -> bool:
        """Tests if a file path matches graphify ignore patterns or noise directories."""
        from graphify.detect import _is_ignored, _is_noise_dir, _SKIP_FILES
        
        path = Path(filepath).resolve()
        
        # Check if any parent component is a noise directory
        try:
            rel_parts = path.relative_to(self.root_dir).parts
        except ValueError:
            rel_parts = path.parts
            
        parent_path = self.root_dir
        for part in rel_parts[:-1]:
            if _is_noise_dir(part, parent_path):
                return True
            parent_path = parent_path / part
            
        # Check if the file name itself is a skipped file
        if path.name in _SKIP_FILES:
            return True
            
        # Check if it is ignored by graphifyignore / gitignore
        if not hasattr(self, "ignore_patterns"):
            from graphify.detect import _load_graphifyignore
            self.ignore_patterns = _load_graphifyignore(self.root_dir)
            
        return _is_ignored(path, self.root_dir, self.ignore_patterns)