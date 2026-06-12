from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Union, Any

class AstNodeType(str, Enum):
    FILE = "File"
    MODULE = "Module"
    CLASS = "Class"
    STRUCT = "Struct"
    TRAIT = "Trait"
    ENUM = "Enum"
    EXTENSION = "Extension"
    FUNCTION = "Function"
    METHOD = "Method"

    @classmethod
    def from_str(cls, s: str) -> "AstNodeType":
        try:
            return cls(s)
        except ValueError:
            return cls.FUNCTION


class EdgeType(str, Enum):
    CONTAINS = "Contains"
    CALLS = "Calls"
    IMPLEMENTS = "Implements"
    FFI_BRIDGE = "FfiBridge"
    FFI_EXPORT = "FfiExport"

@dataclass
class SemanticFacets:
    fields: Dict[str, Any] = field(default_factory=dict)

    def __init__(self, **kwargs):
        object.__setattr__(self, "fields", {})
        for k, v in kwargs.items():
            if k == "fields":
                self.fields.update(v)
            else:
                self.fields[k] = v

    def __getattr__(self, name: str) -> Any:
        return self.fields.get(name, "Unknown")

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "fields":
            object.__setattr__(self, name, value)
        else:
            self.fields[name] = value

    @classmethod
    def new_default(cls, config: "OntologyConfig") -> "SemanticFacets":
        f_dict = {}
        for dir_name, fields in config.directories.items():
            for name, field_cfg in fields.items():
                f_dict[name] = field_cfg.default
        return cls(fields=f_dict)

@dataclass
class AssignmentCondition:
    path_prefix: Optional[str] = None
    class_suffix: Optional[str] = None
    class_contains: Optional[str] = None
    name_contains: Optional[str] = None
    imports_prefix: Optional[str] = None
    calls_prefix: Optional[str] = None

@dataclass
class MetadataAssignmentRule:
    value: Any
    conditions: AssignmentCondition

@dataclass
class FieldRuleConfig:
    source: Any
    target: Any
    message: str
    severity: str = "error"

@dataclass
class FieldConfig:
    values: List[Any]
    default: Any
    reset_on_hash_change: bool = False
    assignment_rules: List[MetadataAssignmentRule] = field(default_factory=list)
    handler: Optional[str] = None
    rules: List[FieldRuleConfig] = field(default_factory=list)
    barriers: List[Any] = field(default_factory=list)
    barrier_field: str = "architectural_role"
    weights: Dict[Any, int] = field(default_factory=dict)

@dataclass
class OntologyConfig:
    directories: Dict[str, Dict[str, FieldConfig]] = field(default_factory=dict)

    def validate(self) -> None:
        """Sanity check ontology setup."""
        for dir_name, fields in self.directories.items():
            for name, f_config in fields.items():
                if f_config.default not in f_config.values:
                    raise ValueError(f"Default value '{f_config.default}' for field '{name}' under directory '{dir_name}' must be one of its allowed values: {f_config.values}")

    def get_fields_for_file(self, rel_file_path: str) -> Dict[str, FieldConfig]:
        from pathlib import Path
        sorted_dirs = sorted(self.directories.keys(), key=lambda d: len(Path(d).parts), reverse=True)
        for d in sorted_dirs:
            if d == "." or rel_file_path == d or rel_file_path.startswith(d + "/"):
                return self.directories[d]
        return {}

@dataclass
class ProjectConfig:
    ontology: OntologyConfig

@dataclass
class CodeNode:
    id: str
    filepath: str
    node_type: AstNodeType
    start_byte: int
    end_byte: int
    ast_hash: str
    raw_code: str
    semantics: Dict[str, Any] = field(default_factory=dict)
    ai_summary: Optional[str] = None
    previous_code: Optional[str] = None
    previous_ai_summary: Optional[str] = None
    is_dirty: bool = True

@dataclass
class ContainsRelation:
    source_id: str
    target_id: str
    type: str = "Contains"

@dataclass
class CallsRelation:
    source_id: str
    target_symbol: str
    caller_filepath: str
    caller_class_symbol: Optional[str] = None
    type: str = "Calls"

@dataclass
class ImplementsRelation:
    source_id: str
    target_symbol: str
    type: str = "Implements"

@dataclass
class FfiBridgeRelation:
    source_id: str
    target_symbol: str
    caller_filepath: str
    caller_class_symbol: Optional[str] = None
    type: str = "FfiBridge"

@dataclass
class FfiExportRelation:
    source_id: str
    target_symbol: str
    type: str = "FfiExport"

UnresolvedRelation = Union[ContainsRelation, CallsRelation, ImplementsRelation,
                           FfiBridgeRelation, FfiExportRelation]
