use crate::schema::{AstNodeType, CodeNode, SemanticFacets, UnresolvedRelation, EdgeType, OntologyConfig};
use byteorder::{LittleEndian, ReadBytesExt};
use rusqlite::{params, Connection, Result};
use std::io::Cursor;
use std::collections::HashMap;
use petgraph::graph::NodeIndex;
use petgraph::stable_graph::StableGraph;
use petgraph::Direction;
use petgraph::visit::Bfs;

#[derive(serde::Serialize)]
pub struct NodeManifest {
    pub id: String,
    pub filepath: String,
    pub node_type: String,
}

pub struct Database {
    conn: Connection,
}

impl Database {
    pub fn init(path: &str) -> Result<Self> {
        let conn = Connection::open(path)?;

        conn.execute(
            "CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                filepath TEXT NOT NULL,
                node_type TEXT NOT NULL,
                start_byte INTEGER NOT NULL,
                end_byte INTEGER NOT NULL,
                ast_hash TEXT NOT NULL,
                semantics TEXT NOT NULL,
                ai_summary TEXT,
                embedding BLOB,
                raw_code TEXT NOT NULL DEFAULT '',
                previous_code TEXT,
                is_dirty INTEGER NOT NULL
            )",
            [],
        )?;
        let _ = conn.execute("ALTER TABLE nodes ADD COLUMN embedding BLOB", []);

        conn.execute(
            "CREATE TABLE IF NOT EXISTS edges (
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                PRIMARY KEY (source_id, target_id, edge_type),
                FOREIGN KEY(source_id) REFERENCES nodes(id) ON DELETE CASCADE,
                FOREIGN KEY(target_id) REFERENCES nodes(id) ON DELETE CASCADE
            )",
            [],
        )?;

        conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes (filepath)", [])?;
        conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_src ON edges (source_id)", [])?;
        conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_tgt ON edges (target_id)", [])?;

        Ok(Database { conn })
    }

    /// Transactionally update database state, storing deltas when code modifications happen [5]
    pub fn sync_nodes(&mut self, filepath: &str, parsed_nodes: &[CodeNode]) -> Result<()> {
        let tx = self.conn.transaction()?;

        let mut existing_nodes = HashMap::new();
        {
            let mut stmt = tx.prepare(
                "SELECT id, ast_hash, semantics, ai_summary, raw_code, is_dirty 
                 FROM nodes WHERE filepath = ?",
            )?;
            let mut rows = stmt.query(params![filepath])?;
            while let Some(row) = rows.next()? {
                let id: String = row.get(0)?;
                let hash: String = row.get(1)?;
                let semantics_json: String = row.get(2)?;
                let ai_summary: Option<String> = row.get(3)?;
                let old_raw_code: String = row.get(4)?;
                let is_dirty: bool = row.get::<_, i32>(5)? != 0;

                let semantics: SemanticFacets = serde_json::from_str(&semantics_json)
                    .unwrap_or_else(|_| SemanticFacets::default());

                existing_nodes.insert(
                    id.clone(),
                    (hash, semantics, ai_summary, old_raw_code, is_dirty),
                );
            }
        }

        for node in parsed_nodes {
            match existing_nodes.remove(&node.id) {
                Some((old_hash, _semantics, _ai_summary, old_raw_code, mut is_dirty)) => {
                    let updated_hash = node.ast_hash != old_hash;
                    let mut prev_code_backup = None;

                    if updated_hash {
                        is_dirty = true;
                        prev_code_backup = Some(old_raw_code); // Keep old raw code slice as context for AI delta [5]
                    }

                    tx.execute(
                        "UPDATE nodes SET 
                            start_byte = ?1, 
                            end_byte = ?2, 
                            ast_hash = ?3, 
                            raw_code = ?4, 
                            previous_code = COALESCE(?5, previous_code),
                            is_dirty = ?6 
                         WHERE id = ?7",
                        params![
                            node.start_byte,
                            node.end_byte,
                            node.ast_hash,
                            node.raw_code,
                            prev_code_backup,
                            if is_dirty { 1 } else { 0 },
                            node.id
                        ],
                    )?;
                }
                None => {
                    let semantics_json = serde_json::to_string(&node.semantics)
                        .unwrap_or_else(|_| "{}".to_string());
                    tx.execute(
                        "INSERT INTO nodes (id, filepath, node_type, start_byte, end_byte, ast_hash, semantics, ai_summary, raw_code, previous_code, is_dirty) 
                         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, NULL, ?8, NULL, 1)",
                        params![
                            node.id,
                            node.filepath,
                            node.node_type.as_str(),
                            node.start_byte,
                            node.end_byte,
                            node.ast_hash,
                            semantics_json,
                            node.raw_code
                        ],
                    )?;
                }
            }
        }

        for (orphaned_id, _) in existing_nodes {
            tx.execute("DELETE FROM nodes WHERE id = ?", params![orphaned_id])?;
        }

        tx.commit()?;
        Ok(())
    }

    /// Resolve unresolved relations using file-scope and receiver context to prevent global matching conflicts [1]
    pub fn resolve_and_save_relations(&mut self, unresolved: &[UnresolvedRelation]) -> Result<()> {
        let tx = self.conn.transaction()?;

        let mut symbol_to_ids: HashMap<String, Vec<String>> = HashMap::new();
        let mut existing_node_ids = std::collections::HashSet::new();
        {
            let mut stmt = tx.prepare("SELECT id FROM nodes")?;
            let mut rows = stmt.query([])?;
            while let Some(row) = rows.next()? {
                let id: String = row.get(0)?;
                existing_node_ids.insert(id.clone());
                if let Some(symbol) = id.split("::").last() {
                    symbol_to_ids.entry(symbol.to_string()).or_default().push(id.clone());
                }
            }
        }

        tx.execute("DELETE FROM edges WHERE edge_type != 'Contains'", [])?;

        for relation in unresolved {
            match relation {
                UnresolvedRelation::Contains { source_id, target_id } => {
                    if existing_node_ids.contains(source_id) && existing_node_ids.contains(target_id) {
                        tx.execute(
                            "INSERT OR IGNORE INTO edges (source_id, target_id, edge_type) VALUES (?1, ?2, ?3)",
                            params![source_id, target_id, EdgeType::Contains.as_str()],
                        )?;
                    }
                }
                UnresolvedRelation::Calls { source_id, target_symbol, caller_filepath, caller_class_symbol } => {
                    if !existing_node_ids.contains(source_id) { continue; }
                    if let Some(target_ids) = symbol_to_ids.get(target_symbol) {
                        if target_ids.is_empty() { continue; }
                        
                        // Select target node using context heuristics
                        let resolved_target = if target_ids.len() == 1 {
                            &target_ids[0]
                        } else {
                            // Score targets using path matching and class hierarchies [1]
                            let mut best_candidate = &target_ids[0];
                            let mut high_score = -1;

                            for candidate in target_ids {
                                let mut score = 0;
                                if candidate.contains(caller_filepath) {
                                    score += 10; // High score for matching filepath
                                }
                                if let Some(ref class_sym) = caller_class_symbol {
                                    if candidate.contains(class_sym) {
                                        score += 5; // Medium score for matching container scope
                                    }
                                }
                                if score > high_score {
                                    high_score = score;
                                    best_candidate = candidate;
                                }
                            }
                            best_candidate
                        };

                        if existing_node_ids.contains(resolved_target) {
                            tx.execute(
                                "INSERT OR IGNORE INTO edges (source_id, target_id, edge_type) VALUES (?1, ?2, ?3)",
                                params![source_id, resolved_target, EdgeType::Calls.as_str()],
                            )?;
                        }
                    }
                }
                UnresolvedRelation::Implements { source_id, target_symbol } => {
                    if !existing_node_ids.contains(source_id) { continue; }
                    if let Some(target_ids) = symbol_to_ids.get(target_symbol) {
                        for target_id in target_ids {
                            if existing_node_ids.contains(target_id) {
                                tx.execute(
                                    "INSERT OR IGNORE INTO edges (source_id, target_id, edge_type) VALUES (?1, ?2, ?3)",
                                    params![source_id, target_id, EdgeType::Implements.as_str()],
                                )?;
                            }
                        }
                    }
                }
                UnresolvedRelation::FfiBridge { source_id, target_symbol, caller_filepath, caller_class_symbol } => {
                    if !existing_node_ids.contains(source_id) { continue; }
                    if let Some(target_ids) = symbol_to_ids.get(target_symbol) {
                        if target_ids.is_empty() { continue; }
                        let resolved_target = if target_ids.len() == 1 {
                            &target_ids[0]
                        } else {
                            let mut best_candidate = &target_ids[0];
                            let mut high_score = -1;
                            for candidate in target_ids {
                                let mut score = 0;
                                if candidate.contains(caller_filepath) { score += 10; }
                                if let Some(ref class_sym) = caller_class_symbol {
                                    if candidate.contains(class_sym) { score += 5; }
                                }
                                if score > high_score {
                                    high_score = score;
                                    best_candidate = candidate;
                                }
                            }
                            best_candidate
                        };
                        if existing_node_ids.contains(resolved_target) {
                            tx.execute(
                                "INSERT OR IGNORE INTO edges (source_id, target_id, edge_type) VALUES (?1, ?2, ?3)",
                                params![source_id, resolved_target, EdgeType::FfiBridge.as_str()],
                            )?;
                        }
                    }
                }
                UnresolvedRelation::FfiExport { source_id, target_symbol } => {
                    if !existing_node_ids.contains(source_id) { continue; }
                    if let Some(target_ids) = symbol_to_ids.get(target_symbol) {
                        for target_id in target_ids {
                            if existing_node_ids.contains(target_id) {
                                tx.execute(
                                    "INSERT OR IGNORE INTO edges (source_id, target_id, edge_type) VALUES (?1, ?2, ?3)",
                                    params![source_id, target_id, EdgeType::FfiExport.as_str()],
                                )?;
                            }
                        }
                    }
                }
            }
        }

        tx.commit()?;
        Ok(())
    }

    /// Propagate semantics via parameterized edge traversal using petgraph (C3, C4)
    pub fn propagate_semantics(&mut self, ontology: &OntologyConfig) -> Result<()> {
        let nodes = self.get_all_nodes_internal()?;
        let mut contains_edges = Vec::new();
        let mut calls_edges = Vec::new();
        let mut impl_edges = Vec::new();
        let mut ffi_bridge_edges = Vec::new();
        let mut ffi_export_edges = Vec::new();

        {
            let mut stmt = self.conn.prepare("SELECT source_id, target_id, edge_type FROM edges")?;
            let mut rows = stmt.query([])?;
            while let Some(row) = rows.next()? {
                let source_id: String = row.get(0)?;
                let target_id: String = row.get(1)?;
                let edge_type_str: String = row.get(2)?;
                match edge_type_str.as_str() {
                    "Contains" => contains_edges.push((source_id, target_id)),
                    "Calls" => calls_edges.push((source_id, target_id)),
                    "Implements" => impl_edges.push((source_id, target_id)),
                    "FfiBridge" => ffi_bridge_edges.push((source_id, target_id)),
                    "FfiExport" => ffi_export_edges.push((source_id, target_id)),
                    _ => {}
                }
            }
        }

        // Build topological graph using petgraph StableGraph
        let mut graph = StableGraph::<CodeNode, EdgeType>::new();
        let mut id_map = HashMap::new();

        for (id, node) in &nodes {
            let idx = graph.add_node(node.clone());
            id_map.insert(id.clone(), idx);
        }

        for (parent, child) in contains_edges {
            if let (Some(&parent_idx), Some(&child_idx)) = (id_map.get(&parent), id_map.get(&child)) {
                graph.add_edge(parent_idx, child_idx, EdgeType::Contains);
            }
        }

        for (caller, callee) in calls_edges {
            if let (Some(&caller_idx), Some(&callee_idx)) = (id_map.get(&caller), id_map.get(&callee)) {
                graph.add_edge(caller_idx, callee_idx, EdgeType::Calls);
            }
        }

        for (impl_source, impl_target) in impl_edges {
            if let (Some(&src_idx), Some(&tgt_idx)) = (id_map.get(&impl_source), id_map.get(&impl_target)) {
                graph.add_edge(src_idx, tgt_idx, EdgeType::Implements);
            }
        }

        for (bridge_source, bridge_target) in ffi_bridge_edges {
            if let (Some(&src_idx), Some(&tgt_idx)) = (id_map.get(&bridge_source), id_map.get(&bridge_target)) {
                graph.add_edge(src_idx, tgt_idx, EdgeType::FfiBridge);
            }
        }

        for (exp_source, exp_target) in ffi_export_edges {
            if let (Some(&src_idx), Some(&tgt_idx)) = (id_map.get(&exp_source), id_map.get(&exp_target)) {
                graph.add_edge(src_idx, tgt_idx, EdgeType::FfiExport);
            }
        }

        // BFS Layer Propagation: Initialize base state
        for idx in graph.node_indices().collect::<Vec<_>>() {
            let node = &mut graph[idx];
            if node.node_type == AstNodeType::File && node.semantics.layer == "Unknown" {
                node.semantics.layer = ontology.default_layer.clone();
            }
            if node.semantics.layer == "Unknown" {
                node.semantics.layer = ontology.default_layer.clone();
            }
        }

        let roots: Vec<NodeIndex> = graph.node_indices()
            .filter(|&idx| graph[idx].node_type == AstNodeType::File)
            .collect();

        for root in roots {
            let mut bfs = Bfs::new(&graph, root);
            while let Some(current_idx) = bfs.next(&graph) {
                let parent_layer = graph[current_idx].semantics.layer.clone();
                
                let mut contains_neighbors = Vec::new();
                let mut neighbors = graph.neighbors_directed(current_idx, Direction::Outgoing);
                while let Some(neighbor_idx) = neighbors.next() {
                    if let Some(edge_idx) = graph.find_edge(current_idx, neighbor_idx) {
                        if graph[edge_idx] == EdgeType::Contains {
                            contains_neighbors.push(neighbor_idx);
                        }
                    }
                }

                for neighbor_idx in contains_neighbors {
                    if graph[neighbor_idx].semantics.layer == "Unknown" {
                        graph[neighbor_idx].semantics.layer = parent_layer.clone();
                    }
                }
            }
        }

        // Parametric Convergence Logic for Purities
        let mut changed = true;
        let mut iterations = 0;

        let weight_to_purity: HashMap<u8, String> = ontology
            .purities
            .iter()
            .map(|(k, v)| (*v, k.clone()))
            .collect();

        while changed && iterations < 100 {
            changed = false;
            iterations += 1;

            let current_purities: HashMap<NodeIndex, String> = graph
                .node_indices()
                .map(|idx| (idx, graph[idx].semantics.purity.clone()))
                .collect();

            let mut purity_updates = Vec::new();

            for caller_idx in graph.node_indices() {
                let caller_node = &graph[caller_idx];
                // C4 Barrier Violation Guard
                if caller_node.semantics.purity_barrier || ontology.barriers.contains(&caller_node.semantics.role) {
                    continue;
                }

                let current_weight = *ontology.purities.get(&caller_node.semantics.purity).unwrap_or(&0);
                let mut max_callee_weight = current_weight;

                let mut neighbors = graph.neighbors_directed(caller_idx, Direction::Outgoing);
                while let Some(callee_idx) = neighbors.next() {
                    if let Some(edge_idx) = graph.find_edge(caller_idx, callee_idx) {
                        if graph[edge_idx] == EdgeType::Calls {
                            if let Some(callee_purity) = current_purities.get(&callee_idx) {
                                let callee_weight = *ontology.purities.get(callee_purity).unwrap_or(&0);
                                if callee_weight > max_callee_weight {
                                    max_callee_weight = callee_weight;
                                }
                            }
                        }
                    }
                }

                if max_callee_weight > current_weight {
                    if let Some(target_purity) = weight_to_purity.get(&max_callee_weight) {
                        purity_updates.push((caller_idx, target_purity.clone()));
                    }
                }
            }

            if !purity_updates.is_empty() {
                changed = true;
                for (idx, new_purity) in purity_updates {
                    graph[idx].semantics.purity = new_purity;
                }
            }
        }
        
        let tx = self.conn.transaction()?;
        for idx in graph.node_indices() {
            let node = &graph[idx];
            let semantics_json = serde_json::to_string(&node.semantics).unwrap_or_default();
            tx.execute(
                "UPDATE nodes SET semantics = ?1 WHERE id = ?2",
                params![semantics_json, node.id],
            )?;
        }
        tx.commit()?;
        Ok(())
    }

    /// Match search strings against existing IDs and suggest corrections [4]
    pub fn fuzzy_suggest_target(&self, target: &str) -> Result<Vec<String>> {
        let pattern = format!("%{}%", target);
        let mut stmt = self.conn.prepare("SELECT id FROM nodes WHERE id LIKE ? LIMIT 5")?;
        let mut rows = stmt.query(params![pattern])?;
        let mut suggestions = Vec::new();
        while let Some(row) = rows.next()? {
            suggestions.push(row.get(0)?);
        }
        Ok(suggestions)
    }

    pub fn get_node(&self, id: &str) -> Result<Option<CodeNode>> {
        let mut stmt = self.conn.prepare("SELECT filepath, node_type, start_byte, end_byte, ast_hash, semantics, ai_summary, raw_code, previous_code, is_dirty FROM nodes WHERE id = ?")?;
        let mut rows = stmt.query(params![id])?;

        if let Some(row) = rows.next()? {
            let filepath: String = row.get(0)?;
            let node_type_str: String = row.get(1)?;
            let start_byte: usize = row.get(2)?;
            let end_byte: usize = row.get(3)?;
            let ast_hash: String = row.get(4)?;
            let semantics_json: String = row.get(5)?;
            let ai_summary: Option<String> = row.get(6)?;
            let raw_code: String = row.get(7)?;
            let previous_code: Option<String> = row.get(8)?;
            let is_dirty: bool = row.get::<_, i32>(9)? != 0;

            let semantics: SemanticFacets = serde_json::from_str(&semantics_json)
                .unwrap_or_else(|_| SemanticFacets::default());

            Ok(Some(CodeNode {
                id: id.to_string(),
                filepath,
                node_type: AstNodeType::from_str(&node_type_str),
                start_byte,
                end_byte,
                ast_hash,
                semantics,
                ai_summary,
                raw_code,
                previous_code,
                previous_ai_summary: None,
                is_dirty,
            }))
        } else {
            Ok(None)
        }
    }

    pub fn get_subgraph(&self, target_id: &str, radius: u8, direction: &str) -> Result<Vec<CodeNode>> {
        let mut visited = std::collections::HashSet::new();
        let mut results = Vec::new();

        let Some(target_node) = self.get_node(target_id)? else {
            return Ok(results);
        };
        visited.insert(target_id.to_string());
        results.push(target_node);

        let mut current_front = vec![target_id.to_string()];

        for _ in 0..radius {
            let mut next_front = Vec::new();
            for current_id in current_front {
                let neighbors = self.get_neighbors(&current_id, direction)?;
                for neighbor_id in neighbors {
                    if !visited.contains(&neighbor_id) {
                        visited.insert(neighbor_id.clone());
                        if let Some(node) = self.get_node(&neighbor_id)? {
                            results.push(node);
                        }
                        next_front.push(neighbor_id);
                    }
                }
            }
            if next_front.is_empty() {
                break;
            }
            current_front = next_front;
        }

        Ok(results)
    }

    fn get_neighbors(&self, id: &str, direction: &str) -> Result<Vec<String>> {
        let mut neighbors = Vec::new();

        match direction {
            "downstream" => {
                let mut stmt = self.conn.prepare("SELECT target_id FROM edges WHERE source_id = ?")?;
                let mut rows = stmt.query(params![id])?;
                while let Some(row) = rows.next()? {
                    neighbors.push(row.get(0)?);
                }
            }
            "upstream" => {
                let mut stmt = self.conn.prepare("SELECT source_id FROM edges WHERE target_id = ?")?;
                let mut rows = stmt.query(params![id])?;
                while let Some(row) = rows.next()? {
                    neighbors.push(row.get(0)?);
                }
            }
            "symmetric" => {
                let mut stmt_impl = self.conn.prepare(
                    "SELECT source_id FROM edges 
                     WHERE edge_type = 'Implements' AND target_id IN (
                        SELECT target_id FROM edges WHERE source_id = ? AND edge_type = 'Implements'
                     ) AND source_id != ?"
                )?;
                let mut rows_impl = stmt_impl.query(params![id, id])?;
                while let Some(row) = rows_impl.next()? {
                    neighbors.push(row.get(0)?);
                }

                let mut stmt_cont = self.conn.prepare(
                    "SELECT target_id FROM edges 
                     WHERE edge_type = 'Contains' AND source_id IN (
                        SELECT source_id FROM edges WHERE target_id = ? AND edge_type = 'Contains'
                     ) AND target_id != ?"
                )?;
                let mut rows_cont = stmt_cont.query(params![id, id])?;
                while let Some(row) = rows_cont.next()? {
                    neighbors.push(row.get(0)?);
                }
            }
            _ => {}
        }

        Ok(neighbors)
    }

    pub fn get_dirty_nodes(&self) -> Result<Vec<CodeNode>> {
        let mut stmt = self.conn.prepare("SELECT id, filepath, node_type, start_byte, end_byte, ast_hash, semantics, ai_summary, raw_code, previous_code FROM nodes WHERE is_dirty = 1")?;
        let mut rows = stmt.query([])?;
        let mut dirty_nodes = Vec::new();

        while let Some(row) = rows.next()? {
            let id: String = row.get(0)?;
            let filepath: String = row.get(1)?;
            let node_type_str: String = row.get(2)?;
            let start_byte: usize = row.get(3)?;
            let end_byte: usize = row.get(4)?;
            let ast_hash: String = row.get(5)?;
            let semantics_json: String = row.get(6)?;
            let ai_summary: Option<String> = row.get(7)?;
            let raw_code: String = row.get(8)?;
            let previous_code: Option<String> = row.get(9)?;

            let semantics: SemanticFacets = serde_json::from_str(&semantics_json)
                .unwrap_or_else(|_| SemanticFacets::default());

            dirty_nodes.push(CodeNode {
                id,
                filepath,
                node_type: AstNodeType::from_str(&node_type_str),
                start_byte,
                end_byte,
                ast_hash,
                semantics,
                ai_summary,
                raw_code,
                previous_code,
                previous_ai_summary: None,
                is_dirty: true,
            });
        }
        Ok(dirty_nodes)
    }

    pub fn get_dirty_nodes_manifest(&self) -> Result<Vec<NodeManifest>> {
        let mut stmt = self.conn.prepare("SELECT id, filepath, node_type FROM nodes WHERE is_dirty = 1")?;
        let mut rows = stmt.query([])?;
        let mut manifest = Vec::new();

        while let Some(row) = rows.next()? {
            manifest.push(NodeManifest {
                id: row.get(0)?,
                filepath: row.get(1)?,
                node_type: row.get(2)?,
            });
        }
        Ok(manifest)
    }

    pub fn count_dirty_nodes(&self) -> Result<i64> {
        let mut stmt = self.conn.prepare("SELECT COUNT(*) FROM nodes WHERE is_dirty = 1")?;
        let count: i64 = stmt.query_row([], |row| row.get(0))?;
        Ok(count)
    }

    pub fn update_node_metadata(
        &self,
        id: &str,
        summary: Option<String>,
        layer: Option<String>,
        role: Option<String>,
        pattern: Option<String>,
        purity: Option<String>,
        purity_barrier: Option<bool>,
        embedding_bytes: Option<Vec<u8>>,
    ) -> Result<()> {
        let mut stmt = self.conn.prepare("SELECT semantics, ai_summary, embedding FROM nodes WHERE id = ?")?;
        let mut rows = stmt.query(params![id])?;
        
        let (mut semantics, mut current_summary, mut current_embedding) = if let Some(row) = rows.next()? {
            let s_json: String = row.get(0)?;
            let s: SemanticFacets = serde_json::from_str(&s_json).unwrap_or_else(|_| SemanticFacets::default());
            let sum: Option<String> = row.get(1)?;
            let emb: Option<Vec<u8>> = row.get(2)?;
            (s, sum, emb)
        } else {
            return Ok(());
        };

        if summary.is_some() {
            current_summary = summary;
        }
        if embedding_bytes.is_some() {
            current_embedding = embedding_bytes;
        }
        if let Some(l) = layer {
            semantics.layer = l;
        }
        if let Some(r) = role {
            semantics.role = r;
        }
        if let Some(p) = pattern {
            semantics.pattern = p;
        }
        if let Some(pu) = purity {
            semantics.purity = pu;
        }
        if let Some(pb) = purity_barrier {
            semantics.purity_barrier = pb;
        }

        let semantics_json = serde_json::to_string(&semantics).unwrap_or_default();

        self.conn.execute(
            "UPDATE nodes SET semantics = ?1, ai_summary = ?2, embedding = ?3, is_dirty = 0 WHERE id = ?4",
            params![semantics_json, current_summary, current_embedding, id],
        )?;

        Ok(())
    }

    fn row_to_node(&self, row: &rusqlite::Row) -> Result<CodeNode> {
        let id: String = row.get(0)?;
        let filepath: String = row.get(1)?;
        let node_type_str: String = row.get(2)?;
        let start_byte: usize = row.get(3)?;
        let end_byte: usize = row.get(4)?;
        let ast_hash: String = row.get(5)?;
        let semantics_json: String = row.get(6)?;
        let ai_summary: Option<String> = row.get(7)?;
        let raw_code: String = row.get(8)?;
        let previous_code: Option<String> = row.get(9)?;
        let is_dirty: bool = row.get::<_, i32>(10)? != 0;
        let semantics: SemanticFacets = serde_json::from_str(&semantics_json).unwrap_or_default();
        Ok(CodeNode { id, filepath, node_type: AstNodeType::from_str(&node_type_str), start_byte, end_byte, ast_hash, semantics, ai_summary, raw_code, previous_code, previous_ai_summary: None, is_dirty })
    }

    pub fn query_file(
        &self,
        path: &str,
        methods: bool,
        independent_functions: bool,
        impl_methods: Option<&str>,
        classes: bool,
        functions: bool,
        imports: bool,
        return_types: Option<&str>,
        return_types_include: Option<&str>,
        include_body: bool,
    ) -> Result<String> {
        let mut nodes_list = Vec::new();
        let mut errors = Vec::new();
        let mut populated = false;

        let mut push_node = |n: CodeNode, populated_ref: &mut bool| {
            *populated_ref = true;
            let code_payload = if include_body {
                n.raw_code.clone()
            } else {
                n.raw_code.lines().next().unwrap_or("").to_string()
            };
            nodes_list.push(serde_json::json!({
                "id": n.id,
                "filepath": n.filepath,
                "type": n.node_type.as_str(),
                "code": code_payload
            }));
        };

        if imports {
            let pattern = if path.ends_with(".rs") { "use %" } else { "import %" };
            let matches = self.nodes_by_file_like(path, Some(pattern), 200)?;
            for n in matches { push_node(n, &mut populated); }
        }

        if methods {
            let matches = self.methods_by_file(path)?;
            for n in matches { push_node(n, &mut populated); }
        }

        if let Some(impl_name) = impl_methods {
            match self.methods_by_impl(path, impl_name) {
                Ok(matches) => {
                    for n in matches { push_node(n, &mut populated); }
                }
                Err(e) => errors.push(format!("impl_methods error: {}", e)),
            }
        }

        if independent_functions {
            let matches = self.functions_by_file(path)?;
            for n in matches { push_node(n, &mut populated); }
        }

        if classes {
            let matches = self.class_like_by_file(path)?;
            for n in matches { push_node(n, &mut populated); }
        }

        if functions {
            let matches = self.functions_by_file(path)?;
            for n in matches { push_node(n, &mut populated); }
        }

        if let Some(rt) = return_types {
            match self.by_return_type(path, rt) {
                Ok(matches) => {
                    for n in matches { push_node(n, &mut populated); }
                }
                Err(e) => errors.push(format!("return_types error: {}", e)),
            }
        }

        if let Some(fragment) = return_types_include {
            match self.by_return_type_include(path, fragment) {
                Ok(matches) => {
                    for n in matches { push_node(n, &mut populated); }
                }
                Err(e) => errors.push(format!("return_types_include error: {}", e)),
            }
        }

        if !populated && !imports {
            let matches = self.nodes_by_file(path)?;
            for n in matches { push_node(n, &mut populated); }
        }

        let result = serde_json::json!({
            "path": path,
            "count": nodes_list.len(),
            "nodes": nodes_list,
            "errors": errors
        });

        Ok(serde_json::to_string_pretty(&result).expect("Serialization must succeed"))
    }

    pub fn methods_by_file(&self, filepath: &str) -> Result<Vec<CodeNode>> {
        let mut stmt = self.conn.prepare("SELECT id, filepath, node_type, start_byte, end_byte, ast_hash, semantics, ai_summary, raw_code, previous_code, is_dirty FROM nodes WHERE filepath = ? AND node_type = 'Method'")?;
        let mut rows = stmt.query(params![filepath])?;
        let mut nodes = Vec::new();
        while let Some(row) = rows.next()? { nodes.push(self.row_to_node(row)?); }
        Ok(nodes)
    }

    pub fn methods_by_impl(&self, filepath: &str, impl_name: &str) -> Result<Vec<CodeNode>> {
        let pattern = format!("{}::impl_{}::%", filepath, impl_name);
        let sql = format!("SELECT id, filepath, node_type, start_byte, end_byte, ast_hash, semantics, ai_summary, raw_code, previous_code, is_dirty FROM nodes WHERE id LIKE ?");
        let mut stmt = self.conn.prepare(&sql)?;
        let mut rows = stmt.query(params![pattern])?;
        let mut nodes = Vec::new();
        while let Some(row) = rows.next()? { nodes.push(self.row_to_node(row)?); }
        Ok(nodes)
    }

    pub fn functions_by_file(&self, filepath: &str) -> Result<Vec<CodeNode>> {
        let mut stmt = self.conn.prepare("SELECT id, filepath, node_type, start_byte, end_byte, ast_hash, semantics, ai_summary, raw_code, previous_code, is_dirty FROM nodes WHERE filepath = ? AND node_type = 'Function'")?;
        let mut rows = stmt.query(params![filepath])?;
        let mut nodes = Vec::new();
        while let Some(row) = rows.next()? { nodes.push(self.row_to_node(row)?); }
        Ok(nodes)
    }

    pub fn class_like_by_file(&self, filepath: &str) -> Result<Vec<CodeNode>> {
        let mut stmt = self.conn.prepare("SELECT id, filepath, node_type, start_byte, end_byte, ast_hash, semantics, ai_summary, raw_code, previous_code, is_dirty FROM nodes WHERE filepath = ? AND node_type IN ('Class','Struct','Enum','Extension')")?;
        let mut rows = stmt.query(params![filepath])?;
        let mut nodes = Vec::new();
        while let Some(row) = rows.next()? { nodes.push(self.row_to_node(row)?); }
        Ok(nodes)
    }

    pub fn nodes_by_file(&self, filepath: &str) -> Result<Vec<CodeNode>> {
        let mut stmt = self.conn.prepare("SELECT id, filepath, node_type, start_byte, end_byte, ast_hash, semantics, ai_summary, raw_code, previous_code, is_dirty FROM nodes WHERE filepath = ?")?;
        let mut rows = stmt.query(params![filepath])?;
        let mut nodes = Vec::new();
        while let Some(row) = rows.next()? { nodes.push(self.row_to_node(row)?); }
        Ok(nodes)
    }

    pub fn nodes_by_file_like(&self, filepath: &str, pattern: Option<&str>, limit: u32) -> Result<Vec<CodeNode>> {
        let sql = if let Some(_p) = pattern {
            format!("SELECT id, filepath, node_type, start_byte, end_byte, ast_hash, semantics, ai_summary, raw_code, previous_code, is_dirty FROM nodes WHERE filepath = ? AND raw_code LIKE ? ORDER BY start_byte LIMIT {}", limit)
        } else {
            format!("SELECT id, filepath, node_type, start_byte, end_byte, ast_hash, semantics, ai_summary, raw_code, previous_code, is_dirty FROM nodes WHERE filepath = ? ORDER BY start_byte LIMIT {}", limit)
        };
        let mut stmt = self.conn.prepare(&sql)?;
        let mut rows = if let Some(p) = pattern { stmt.query(params![filepath, p])? } else { stmt.query(params![filepath])? };
        let mut nodes = Vec::new();
        while let Some(row) = rows.next()? { nodes.push(self.row_to_node(row)?); }
        Ok(nodes)
    }

    pub fn by_return_type(&self, filepath: &str, return_type: &str) -> Result<Vec<CodeNode>> {
        let pattern = format!("%{}%", return_type);
        let sql = "SELECT id, filepath, node_type, start_byte, end_byte, ast_hash, semantics, ai_summary, raw_code, previous_code, is_dirty FROM nodes WHERE filepath = ? AND raw_code LIKE ? ORDER BY start_byte";
        let mut stmt = self.conn.prepare(sql)?;
        let mut rows = stmt.query(params![filepath, pattern])?;
        let mut nodes = Vec::new();
        while let Some(row) = rows.next()? { nodes.push(self.row_to_node(row)?); }
        Ok(nodes)
    }

    pub fn by_return_type_include(&self, filepath: &str, fragment: &str) -> Result<Vec<CodeNode>> {
        let pattern = format!("%{}%", fragment);
        let sql = "SELECT id, filepath, node_type, start_byte, end_byte, ast_hash, semantics, ai_summary, raw_code, previous_code, is_dirty FROM nodes WHERE filepath = ? AND raw_code LIKE ? ORDER BY start_byte";
        let mut stmt = self.conn.prepare(sql)?;
        let mut rows = stmt.query(params![filepath, pattern])?;
        let mut nodes = Vec::new();
        while let Some(row) = rows.next()? { nodes.push(self.row_to_node(row)?); }
        Ok(nodes)
    }

    fn get_all_nodes_internal(&self) -> Result<HashMap<String, CodeNode>> {
        let mut stmt = self.conn.prepare("SELECT id, filepath, node_type, start_byte, end_byte, ast_hash, semantics, ai_summary, raw_code, previous_code FROM nodes")?;
        let mut rows = stmt.query([])?;
        let mut map = HashMap::new();

        while let Some(row) = rows.next()? {
            let id: String = row.get(0)?;
            let filepath: String = row.get(1)?;
            let node_type_str: String = row.get(2)?;
            let start_byte: usize = row.get(3)?;
            let end_byte: usize = row.get(4)?;
            let ast_hash: String = row.get(5)?;
            let semantics_json: String = row.get(6)?;
            let ai_summary: Option<String> = row.get(7)?;
            let raw_code: String = row.get(8)?;
            let previous_code: Option<String> = row.get(9)?;

            let semantics: SemanticFacets = serde_json::from_str(&semantics_json)
                .unwrap_or_else(|_| SemanticFacets::default());

            let node = CodeNode {
                id: id.clone(),
                filepath,
                node_type: AstNodeType::from_str(&node_type_str),
                start_byte,
                end_byte,
                ast_hash,
                semantics,
                ai_summary,
                raw_code,
                previous_code,
                previous_ai_summary: None,
                is_dirty: false,
            };
            map.insert(id, node);
        }
        Ok(map)
    }

    pub fn semantic_search(&self, query: &str, limit: u32) -> Result<serde_json::Value> {
        let mut stmt = self.conn.prepare(
            "SELECT id, filepath, node_type, start_byte, end_byte, ast_hash, semantics, ai_summary, raw_code, previous_code, is_dirty 
             FROM nodes 
             WHERE ai_summary IS NOT NULL 
                OR raw_code LIKE ? 
             ORDER BY filepath, start_byte 
             LIMIT ?1",
        )?;
        let pattern = format!("%{}%", query);
        let mut rows = stmt.query(params![limit, pattern])?;

        let mut results = Vec::new();
        while let Some(row) = rows.next()? {
            let node = self.row_to_node(row)?;
            results.push(serde_json::json!({
                "id": node.id,
                "filepath": node.filepath,
                "type": node.node_type.as_str(),
                "summary": node.ai_summary,
                "raw_code": node.raw_code,
            }));
        }

        Ok(serde_json::json!({
            "query": query,
            "count": results.len(),
            "results": results,
        }))
    }

    pub fn semantic_vector_search(&self, query_embedding: &[f32], limit: usize) -> Result<serde_json::Value> {
        let mut stmt = self.conn.prepare(
            "SELECT id, filepath, node_type, ai_summary, embedding 
             FROM nodes WHERE embedding IS NOT NULL",
        )?;

        let mut rows = stmt.query([])?;
        let mut scored_nodes = Vec::new();

        while let Some(row) = rows.next()? {
            let id: String = row.get(0)?;
            let filepath: String = row.get(1)?;
            let node_type: String = row.get(2)?;
            let summary: Option<String> = row.get(3)?;
            let blob: Vec<u8> = row.get(4)?;

            let mut cursor = Cursor::new(blob);
            let mut node_emb = Vec::with_capacity(768);
            while let Ok(f) = cursor.read_f32::<LittleEndian>() {
                node_emb.push(f);
            }

            let mut similarity: f32 = 0.0;
            let len = std::cmp::min(query_embedding.len(), node_emb.len());
            for i in 0..len {
                similarity += query_embedding[i] * node_emb[i];
            }

            scored_nodes.push((
                similarity,
                id,
                filepath,
                node_type,
                summary.unwrap_or_default(),
            ));
        }

        scored_nodes.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap());

        let mut results = Vec::new();
        for (score, id, filepath, n_type, summary) in scored_nodes.into_iter().take(limit) {
            results.push(serde_json::json!({
                "score": score,
                "id": id,
                "filepath": filepath,
                "type": n_type,
                "summary": summary
            }));
        }

        Ok(serde_json::json!({
            "count": results.len(),
            "results": results,
        }))
    }

    pub fn check_architectural_violations(&self, ontology: &OntologyConfig) -> Result<Vec<Violation>> {
        let mut violations = Vec::new();

        for rule in &ontology.rules {
            let mut query = String::from(
                "SELECT e.source_id, e.target_id, s.filepath, s.start_byte, s.end_byte, 
                        json_extract(s.semantics, '$.layer'), 
                        json_extract(t.semantics, '$.layer'), 
                        json_extract(t.semantics, '$.purity') 
                 FROM edges e
                 JOIN nodes s ON e.source_id = s.id
                 JOIN nodes t ON e.target_id = t.id
                 WHERE e.edge_type = 'Calls'"
            );

            let mut params_vec: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();

            if let Some(ref s_layer) = rule.source_layer {
                query.push_str(" AND json_extract(s.semantics, '$.layer') = ?");
                params_vec.push(Box::new(s_layer.clone()));
            }
            if let Some(ref t_layer) = rule.target_layer {
                query.push_str(" AND json_extract(t.semantics, '$.layer') = ?");
                params_vec.push(Box::new(t_layer.clone()));
            }
            if let Some(ref t_purity) = rule.target_purity {
                query.push_str(" AND json_extract(t.semantics, '$.purity') = ?");
                params_vec.push(Box::new(t_purity.clone()));
            }

            let ref_params: Vec<&dyn rusqlite::ToSql> = params_vec.iter().map(|p| p.as_ref()).collect();
            let mut stmt = self.conn.prepare(&query)?;
            let mut rows = stmt.query(rusqlite::params_from_iter(ref_params))?;

            while let Some(row) = rows.next()? {
                violations.push(Violation {
                    rule_name: rule.name.clone(),
                    source_id: row.get(0)?,
                    target_id: row.get(1)?,
                    filepath: row.get(2)?,
                    start_byte: row.get(3)?,
                    end_byte: row.get(4)?,
                    message: rule.message.clone(),
                });
            }
        }

        Ok(violations)
    }
}

pub struct Violation {
    pub rule_name: String,
    pub source_id: String,
    pub target_id: String,
    pub filepath: String,
    pub start_byte: usize,
    pub end_byte: usize,
    pub message: String,
}
