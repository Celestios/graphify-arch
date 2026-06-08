use crate::schema::{AstNodeType, CodeNode, SemanticFacets, UnresolvedRelation};
use tree_sitter::{Node, Parser, Query, QueryCursor};
use streaming_iterator::StreamingIterator;

pub struct AstParser;

impl AstParser {
    pub fn parse_file(
        filepath: &str,
        content: &str,
    ) -> Result<(Vec<CodeNode>, Vec<UnresolvedRelation>), String> {
        let extension = std::path::Path::new(filepath)
            .extension()
            .and_then(|ext| ext.to_str())
            .unwrap_or("");

        match extension {
            "rs" => {
                let query_src = std::fs::read_to_string("queries/rust.scm")
                    .map_err(|e| format!("Failed to load Rust query: {}", e))?;
                Self::exec_query(filepath, content, &query_src)
            }
            "dart" => {
                let query_src = std::fs::read_to_string("queries/dart.scm")
                    .map_err(|e| format!("Failed to load Dart query: {}", e))?;
                Self::exec_query(filepath, content, &query_src)
            }
            _ => Err(format!("Unsupported file: .{}", extension)),
        }
    }

    fn exec_query(
        filepath: &str,
        content: &str,
        query_src: &str,
    ) -> Result<(Vec<CodeNode>, Vec<UnresolvedRelation>), String> {
        let mut parser = Parser::new();
        let language = match std::path::Path::new(filepath)
            .extension()
            .and_then(|ext| ext.to_str())
        {
            Some("rs") => {
                let lang: tree_sitter::Language = tree_sitter_rust::LANGUAGE.into();
                lang
            }
            Some("dart") => {
                let lang: tree_sitter::Language = tree_sitter_dart::LANGUAGE.into();
                lang
            }
            _ => return Err("Unsupported extension".to_string()),
        };
        parser.set_language(&language).map_err(|e| format!("Failed to set language: {:?}", e))?;
        let tree = parser.parse(content, None).ok_or_else(|| "Failed to parse content".to_string())?;
        let root = tree.root_node();

        let mut nodes = Vec::new();
        let mut relations = Vec::new();
        let file_node_id = filepath.to_string();

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
            previous_ai_summary: None,
            is_dirty: true,
        });

        let query = Query::new(&language, query_src)
            .map_err(|e| format!("Failed to create query: {:?}", e))?;
        let capture_names: Vec<&str> = query.capture_names().to_vec();
        let mut qcursor = QueryCursor::new();
        let mut matches_iter = qcursor.matches(&query, root, content.as_bytes());

        while let Some(m) = matches_iter.next() {
            for capture in m.captures {
                let node = capture.node;
                let idx = capture.index as usize;
                if idx >= capture_names.len() { continue; }
                let name = capture_names[idx];

                match name {
                    "node.class" => {
                        if let Some(name_node) = Self::find_child_by_kind(node, "type_identifier") {
                            let name = Self::get_node_text(name_node, content);
                            let id = format!("{}::{}", filepath, name);
                            let node_type = match std::path::Path::new(filepath)
                                .extension()
                                .and_then(|ext| ext.to_str())
                            {
                                Some("rs") => AstNodeType::Struct,
                                Some("dart") => AstNodeType::Class,
                                _ => AstNodeType::Class,
                            };
                            nodes.push(Self::build_code_node(id.clone(), filepath, node_type, node, content));
                            relations.push(UnresolvedRelation::Contains {
                                source_id: file_node_id.clone(),
                                target_id: id,
                            });
                        }
                    }
                    "node.function" => {
                        if let Some(name_node) = Self::find_child_by_kind(node, "identifier") {
                            let name = Self::get_node_text(name_node, content);
                            let id = format!("{}::{}", file_node_id, name);
                            nodes.push(Self::build_code_node(id.clone(), filepath, AstNodeType::Function, node, content));
                            relations.push(UnresolvedRelation::Contains {
                                source_id: file_node_id.clone(),
                                target_id: id,
                            });
                        }
                    }
                    "node.impl_target" => {
                        if let Some(name_node) = Self::find_child_by_kind(node, "type_identifier") {
                            let name = Self::get_node_text(name_node, content);
                            let id = format!("{}::impl_{}", filepath, name);
                            nodes.push(Self::build_code_node(id.clone(), filepath, AstNodeType::Struct, node, content));
                            relations.push(UnresolvedRelation::Contains {
                                source_id: file_node_id.clone(),
                                target_id: id,
                            });
                        }
                    }
                    "relation.calls" => {
                        let symbol = Self::get_node_text(node, content).to_string();
                        relations.push(UnresolvedRelation::Calls {
                            source_id: file_node_id.clone(),
                            target_symbol: symbol,
                            caller_filepath: filepath.to_string(),
                            caller_class_symbol: None,
                        });
                    }
                    "relation.implements" => {
                        let symbol = Self::get_node_text(node, content).to_string();
                        relations.push(UnresolvedRelation::Implements {
                            source_id: file_node_id.clone(),
                            target_symbol: symbol,
                        });
                    }
                    "relation.ffi_bridge" => {
                        let symbol = Self::get_node_text(node, content).to_string();
                        relations.push(UnresolvedRelation::FfiBridge {
                            source_id: file_node_id.clone(),
                            target_symbol: symbol,
                            caller_filepath: filepath.to_string(),
                            caller_class_symbol: None,
                        });
                    }
                    "node.ffi_export" => {
                        let symbol = Self::get_node_text(node, content).to_string();
                        relations.push(UnresolvedRelation::FfiExport {
                            source_id: file_node_id.clone(),
                            target_symbol: symbol,
                        });
                    }
                    _ => {}
                }
            }
        }

        Ok((nodes, relations))
    }

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
        let hash = Self::hash_normalized(node, content);

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
            previous_ai_summary: None,
            is_dirty: true,
        }
    }
}
