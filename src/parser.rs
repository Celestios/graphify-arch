use crate::schema::{AstNodeType, CodeNode, SemanticFacets, UnresolvedRelation};
use tree_sitter::{Node, Parser};

pub struct AstParser;

impl AstParser {
    pub fn parse_file(
        filepath: &str,
        content: &str,
    ) -> Result<(Vec<CodeNode>, Vec<UnresolvedRelation>), String> {
        let mut parser = Parser::new();
        let extension = std::path::Path::new(filepath)
            .extension()
            .and_then(|ext| ext.to_str())
            .unwrap_or("");

        let mut nodes = Vec::new();
        let mut relations = Vec::new();
        let file_node_id = filepath.to_string();

        match extension {
            "rs" => {
                parser
                    .set_language(&tree_sitter_rust::LANGUAGE.into())
                    .map_err(|e| format!("Failed to load Rust grammar: {:?}", e))?;
                let tree = parser
                    .parse(content, None)
                    .ok_or_else(|| "Failed to parse Rust content".to_string())?;

                let root = tree.root_node();
                nodes.push(CodeNode {
                    id: file_node_id.clone(),
                    filepath: filepath.to_string(),
                    node_type: AstNodeType::File,
                    start_byte: 0,
                    end_byte: content.len(),
                    ast_hash: Self::hash_normalized(root, content),
                    semantics: SemanticFacets::default(),
                    ai_summary: None,
                    raw_code: content.to_string(),
                    previous_code: None,
                    is_dirty: true,
                });

                Self::walk_rust_node(
                    root,
                    content,
                    filepath,
                    Some(&file_node_id),
                    None,
                    None,
                    &mut nodes,
                    &mut relations,
                );
            }
            "dart" => {
                parser
                    .set_language(&tree_sitter_dart::LANGUAGE.into())
                    .map_err(|e| format!("Failed to load Dart grammar: {:?}", e))?;
                let tree = parser
                    .parse(content, None)
                    .ok_or_else(|| "Failed to parse Dart content".to_string())?;

                let root = tree.root_node();
                nodes.push(CodeNode {
                    id: file_node_id.clone(),
                    filepath: filepath.to_string(),
                    node_type: AstNodeType::File,
                    start_byte: 0,
                    end_byte: content.len(),
                    ast_hash: Self::hash_normalized(root, content),
                    semantics: SemanticFacets::default(),
                    ai_summary: None,
                    raw_code: content.to_string(),
                    previous_code: None,
                    is_dirty: true,
                });

                Self::walk_dart_node(
                    root,
                    content,
                    filepath,
                    Some(&file_node_id),
                    None,
                    None,
                    &mut nodes,
                    &mut relations,
                );
            }
            _ => return Err(format!("Unsupported file: .{}", extension)),
        }

        Ok((nodes, relations))
    }

    /// Strips comments and all semantic whitespace by walking named AST nodes [2]
    pub fn hash_normalized(node: Node, content: &str) -> String {
        let mut hasher = blake3::Hasher::new();
        let mut cursor = node.walk();
        
        let mut visit_stack = vec![node];
        while let Some(current) = visit_stack.pop() {
            if current.is_named() && current.kind() != "comment" {
                let start = current.start_byte();
                let end = current.end_byte();
                if end <= content.len() {
                    hasher.update(content[start..end].as_bytes());
                }
            }
            
            let mut children: Vec<Node> = current.children(&mut cursor).collect();
            children.reverse();
            for child in children {
                visit_stack.push(child);
            }
        }

        hasher.finalize().to_hex().to_string()
    }

    fn get_node_text<'a>(node: Node, content: &'a str) -> &'a str {
        let start = node.start_byte();
        let end = node.end_byte();
        if end <= content.len() {
            &content[start..end]
        } else {
            ""
        }
    }

    fn find_child_by_kind<'a>(node: Node<'a>, kind: &str) -> Option<Node<'a>> {
        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            if child.kind() == kind {
                return Some(child);
            }
        }
        None
    }

    fn walk_rust_node(
        node: Node,
        content: &str,
        filepath: &str,
        parent_id: Option<&str>,
        active_class_name: Option<&str>, // For scope resolution [1]
        active_function_id: Option<&str>,
        nodes: &mut Vec<CodeNode>,
        relations: &mut Vec<UnresolvedRelation>,
    ) {
        let kind = node.kind();
        let mut current_parent = parent_id.map(String::from);
        let mut current_class = active_class_name.map(String::from);
        let mut current_function = active_function_id.map(String::from);

        match kind {
            "struct_item" | "enum_item" | "trait_item" => {
                let node_type = match kind {
                    "struct_item" => AstNodeType::Struct,
                    "enum_item" => AstNodeType::Enum,
                    _ => AstNodeType::Trait,
                };

                if let Some(ident_node) = Self::find_child_by_kind(node, "type_identifier") {
                    let name = Self::get_node_text(ident_node, content);
                    let id = format!("{}::{}", filepath, name);
                    current_parent = Some(id.clone());
                    current_class = Some(name.to_string());

                    nodes.push(Self::build_code_node(
                        id.clone(),
                        filepath,
                        node_type,
                        node,
                        content,
                    ));

                    if let Some(p) = parent_id {
                        relations.push(UnresolvedRelation::Contains {
                            source_id: p.to_string(),
                            target_id: id,
                        });
                    }
                }
            }
            "impl_item" => {
                let trait_name = Self::find_child_by_kind(node, "type_identifier")
                    .map(|n| Self::get_node_text(n, content).to_string());

                let struct_name = if trait_name.is_some() {
                    let mut cursor = node.walk();
                    let child_node = node.children(&mut cursor).find(|child| {
                        child.kind() == "type_identifier"
                            && Some(*child) != Self::find_child_by_kind(node, "type_identifier")
                    });

                    child_node.map(|s| Self::get_node_text(s, content).to_string())
                } else {
                    Self::find_child_by_kind(node, "type_identifier")
                        .map(|s| Self::get_node_text(s, content).to_string())
                };

                if let Some(ref s_name) = struct_name {
                    current_class = Some(s_name.clone());
                    let struct_id = format!("{}::{}", filepath, s_name);

                    if let Some(ref t_name) = trait_name {
                        relations.push(UnresolvedRelation::Implements {
                            source_id: struct_id,
                            target_symbol: t_name.clone(),
                        });
                    }
                }

                let target_name = trait_name
                    .or(struct_name)
                    .unwrap_or_else(|| "Anonymous".to_string());
                current_parent = Some(format!("{}::impl_{}", filepath, target_name));
            }
            "function_item" => {
                if let Some(ident_node) = Self::find_child_by_kind(node, "identifier") {
                    let name = Self::get_node_text(ident_node, content);
                    let (id, node_type) = match &parent_id {
                        Some(p_id) => (format!("{}::{}", p_id, name), AstNodeType::Method),
                        None => (format!("{}::{}", filepath, name), AstNodeType::Function),
                    };

                    nodes.push(Self::build_code_node(
                        id.clone(),
                        filepath,
                        node_type,
                        node,
                        content,
                    ));
                    current_function = Some(id.clone());

                    if let Some(p) = parent_id {
                        relations.push(UnresolvedRelation::Contains {
                            source_id: p.to_string(),
                            target_id: id,
                        });
                    }
                }
            }
            "call_expression" | "scoped_identifier" => {
                let called_symbol = if let Some(func_node) = Self::find_child_by_kind(node, "identifier") {
                    Self::get_node_text(func_node, content).to_string()
                } else if let Some(ident_node) = Self::find_child_by_kind(node, "field_identifier") {
                    Self::get_node_text(ident_node, content).to_string()
                } else {
                    String::new()
                };
                if !called_symbol.is_empty() {
                    if let Some(ref active_fn) = current_function {
                        relations.push(UnresolvedRelation::Calls {
                            source_id: active_fn.clone(),
                            target_symbol: called_symbol,
                            caller_filepath: filepath.to_string(),
                            caller_class_symbol: current_class.clone(),
                        });
                    }
                }
            }
            "method_call_expression" => {
                if let Some(method_node) = Self::find_child_by_kind(node, "field_identifier") {
                    let called_symbol = Self::get_node_text(method_node, content);
                    if let Some(ref active_fn) = current_function {
                        relations.push(UnresolvedRelation::Calls {
                            source_id: active_fn.clone(),
                            target_symbol: called_symbol.to_string(),
                            caller_filepath: filepath.to_string(),
                            caller_class_symbol: current_class.clone(),
                        });
                    }
                }
            }
            _ => {}
        }

        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            Self::walk_rust_node(
                child,
                content,
                filepath,
                current_parent.as_deref(),
                current_class.as_deref(),
                current_function.as_deref(),
                nodes,
                relations,
            );
        }
    }

    fn walk_dart_node(
        node: Node,
        content: &str,
        filepath: &str,
        parent_id: Option<&str>,
        active_class_name: Option<&str>, // For scope resolution [1]
        active_function_id: Option<&str>,
        nodes: &mut Vec<CodeNode>,
        relations: &mut Vec<UnresolvedRelation>,
    ) {
        let kind = node.kind();
        let mut current_parent = parent_id.map(String::from);
        let mut current_class = active_class_name.map(String::from);
        let mut current_function = active_function_id.map(String::from);

        match kind {
            "class_declaration"
            | "mixin_declaration"
            | "enum_declaration"
            | "extension_declaration" => {
                // Support Enums & Extensions [3]
                let node_type = match kind {
                    "class_declaration" => AstNodeType::Class,
                    "mixin_declaration" => AstNodeType::Class,
                    "enum_declaration" => AstNodeType::Enum,
                    _ => AstNodeType::Extension,
                };

                if let Some(ident_node) = Self::find_child_by_kind(node, "type_identifier") {
                    let name = Self::get_node_text(ident_node, content);
                    let id = format!("{}::{}", filepath, name);
                    current_parent = Some(id.clone());
                    current_class = Some(name.to_string());

                    nodes.push(Self::build_code_node(
                        id.clone(),
                        filepath,
                        node_type,
                        node,
                        content,
                    ));

                    if let Some(p) = parent_id {
                        relations.push(UnresolvedRelation::Contains {
                            source_id: p.to_string(),
                            target_id: id.clone(),
                        });
                    }

                    let mut cursor = node.walk();
                    for child in node.children(&mut cursor) {
                        if child.kind() == "implements_clause" || child.kind() == "superclass" {
                            if let Some(type_node) =
                                Self::find_child_by_kind(child, "type_identifier")
                            {
                                let interface_symbol = Self::get_node_text(type_node, content);
                                relations.push(UnresolvedRelation::Implements {
                                    source_id: id.clone(),
                                    target_symbol: interface_symbol.to_string(),
                                });
                            }
                        }
                    }
                }
            }
            "method_declaration" => {
                if let Some(ident_node) = Self::find_child_by_kind(node, "identifier") {
                    let name = Self::get_node_text(ident_node, content);
                    let id = match &parent_id {
                        Some(p_id) => format!("{}::{}", p_id, name),
                        None => format!("{}::{}", filepath, name),
                    };

                    nodes.push(Self::build_code_node(
                        id.clone(),
                        filepath,
                        AstNodeType::Method,
                        node,
                        content,
                    ));
                    current_function = Some(id.clone());

                    if let Some(p) = parent_id {
                        relations.push(UnresolvedRelation::Contains {
                            source_id: p.to_string(),
                            target_id: id,
                        });
                    }
                }
            }
            "function_declaration" => {
                if let Some(ident_node) = Self::find_child_by_kind(node, "identifier") {
                    let name = Self::get_node_text(ident_node, content);
                    let id = format!("{}::{}", filepath, name);

                    nodes.push(Self::build_code_node(
                        id.clone(),
                        filepath,
                        AstNodeType::Function,
                        node,
                        content,
                    ));
                    current_function = Some(id.clone());

                    if let Some(p) = parent_id {
                        relations.push(UnresolvedRelation::Contains {
                            source_id: p.to_string(),
                            target_id: id,
                        });
                    }
                }
            }
            "method_invocation" => {
                if let Some(name_node) = Self::find_child_by_kind(node, "identifier") {
                    let called_symbol = Self::get_node_text(name_node, content);
                    if let Some(ref active_fn) = current_function {
                        relations.push(UnresolvedRelation::Calls {
                            source_id: active_fn.clone(),
                            target_symbol: called_symbol.to_string(),
                            caller_filepath: filepath.to_string(),
                            caller_class_symbol: current_class.clone(),
                        });
                    }
                }
            }
            _ => {}
        }

        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            Self::walk_dart_node(
                child,
                content,
                filepath,
                current_parent.as_deref(),
                current_class.as_deref(),
                current_function.as_deref(),
                nodes,
                relations,
            );
        }
    }

    fn build_code_node(
        id: String,
        filepath: &str,
        node_type: AstNodeType,
        node: Node,
        content: &str,
    ) -> CodeNode {
        let start_byte = node.start_byte();
        let end_byte = node.end_byte();
        let code_slice = &content[start_byte..end_byte];

        let hash = Self::hash_normalized(node, content); // Run comments-and-whitespace normalization hash [2]

        CodeNode {
            id,
            filepath: filepath.to_string(),
            node_type,
            start_byte,
            end_byte,
            ast_hash: hash,
            semantics: SemanticFacets::default(),
            ai_summary: None,
            raw_code: code_slice.to_string(),
            previous_code: None,
            is_dirty: true,
        }
    }
}
