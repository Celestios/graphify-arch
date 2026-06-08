use serde::{Deserialize, Serialize};
use std::collections::HashMap;

// The Parametric Boundary Definition (O_meta)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OntologyConfig {
    pub layers: Vec<String>,
    pub default_layer: String,
    pub purities: HashMap<String, u8>, // Purity string to topological weight mappings
    pub default_purity: String,
    pub roles: Vec<String>,
    pub barriers: Vec<String>,         // Roles acting as C4 topological barriers
    pub rules: Vec<ArchitecturalRule>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArchitecturalRule {
    pub name: String,
    pub source_layer: Option<String>,
    pub target_layer: Option<String>,
    pub target_purity: Option<String>,
    pub message: String,
}

impl OntologyConfig {
    /// Enforces Phi boundary constraints (C2)
    pub fn validate(&self) -> Result<(), String> {
        let k_layers = self.layers.len();
        if k_layers < 2 || k_layers > 12 {
            return Err(format!("Layer cardinality |T|={} violates bounds 2 <= |T| <= 12", k_layers));
        }
        if !self.layers.contains(&self.default_layer) {
            return Err("Default layer must exist within defined layers topology".into());
        }
        if !self.purities.contains_key(&self.default_purity) {
            return Err("Default purity must exist within defined purity weights".into());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum AstNodeType {
    File,
    Module,
    Class,
    Struct,
    Trait,
    Enum,       // Syntax extension
    Extension,  // Syntax extension
    Function,
    Method,
}

impl AstNodeType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::File => "File",
            Self::Module => "Module",
            Self::Class => "Class",
            Self::Struct => "Struct",
            Self::Trait => "Trait",
            Self::Enum => "Enum",
            Self::Extension => "Extension",
            Self::Function => "Function",
            Self::Method => "Method",
        }
    }

    pub fn from_str(s: &str) -> Self {
        match s {
            "File" => Self::File,
            "Module" => Self::Module,
            "Class" => Self::Class,
            "Struct" => Self::Struct,
            "Trait" => Self::Trait,
            "Enum" => Self::Enum,
            "Extension" => Self::Extension,
            "Function" => Self::Function,
            "Method" => Self::Method,
            _ => Self::Function,
        }
    }
}

// Semantic sets decoupled from hardcoded enums
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SemanticFacets {
    pub layer: String,
    pub role: String,
    pub pattern: String,
    pub purity: String,
    pub purity_barrier: bool,
}

impl SemanticFacets {
    pub fn new_default(config: &OntologyConfig) -> Self {
        Self {
            layer: config.default_layer.clone(),
            role: "Unknown".to_string(),
            pattern: "None".to_string(),
            purity: config.default_purity.clone(),
            purity_barrier: false,
        }
    }
}

impl Default for SemanticFacets {
    fn default() -> Self {
        Self {
            layer: "Unknown".to_string(),
            role: "Unknown".to_string(),
            pattern: "None".to_string(),
            purity: "Unknown".to_string(),
            purity_barrier: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CodeNode {
    pub id: String,
    pub filepath: String,
    pub node_type: AstNodeType,
    pub start_byte: usize,
    pub end_byte: usize,
    pub ast_hash: String,
    pub semantics: SemanticFacets,
    pub ai_summary: Option<String>,
    pub raw_code: String,         // Cache raw segment to compare and output context
    pub previous_code: Option<String>, // Tracks deltas to assist agent updates
    pub is_dirty: bool,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum EdgeType {
    Contains,
    Calls,
    Implements,
}

impl EdgeType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Contains => "Contains",
            Self::Calls => "Calls",
            Self::Implements => "Implements",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum UnresolvedRelation {
    Contains { 
        source_id: String, 
        target_id: String 
    },
    Calls { 
        source_id: String, 
        target_symbol: String,
        caller_filepath: String,            // Added for scope matching heuristics
        caller_class_symbol: Option<String>, // Added for class-level receiver inference
    },
    Implements { 
        source_id: String, 
        target_symbol: String 
    },
}
