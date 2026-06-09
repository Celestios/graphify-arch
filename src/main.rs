mod cli;
mod compiler;
mod db;
mod embedder;
mod parser;
mod project;
mod schema;

use clap::Parser;
use cli::{Cli, Commands};
use compiler::ContextCompiler;
use db::Database;
use embedder::LocalEmbedder;
use parser::AstParser;
use project::Workspace;
use schema::OntologyConfig;
use std::collections::HashMap;
use std::fs;
use std::path::Path;
use std::process::Command;
use std::thread::sleep;
use std::time::Duration;

fn load_ontology_from_workspace(workspace: &Workspace) -> OntologyConfig {
    workspace.config.ontology.clone()
}

fn get_git_head(dir: &str) -> String {
    Command::new("git")
        .args(["rev-parse", "HEAD"])
        .current_dir(dir)
        .output()
        .map(|out| String::from_utf8_lossy(&out.stdout).trim().to_string())
        .unwrap_or_default()
}

fn force_full_reindex(workspace: &Workspace, database: &mut Database, ontology: &OntologyConfig) {
    let output = Command::new("git")
        .args(["ls-files"])
        .current_dir(&workspace.root_dir)
        .output();

    if let Ok(out) = output {
        let stdout = String::from_utf8_lossy(&out.stdout);
        let mut parsed_nodes = Vec::new();
        let mut parsed_relations = Vec::new();

        for line in stdout.lines() {
            let path = workspace.root_dir.join(line);
            if !path.exists() || workspace.is_excluded(&path.to_string_lossy()) { continue; }
            let path_str = path.to_string_lossy().into_owned();
            if path_str.ends_with(".rs") || path_str.ends_with(".dart") {
                if let Ok(content) = fs::read_to_string(&path) {
                    if let Ok((mut n, mut r)) = AstParser::parse_file(&path_str, &content) {
                        parsed_nodes.append(&mut n);
                        parsed_relations.append(&mut r);
                    }
                }
            }
        }

        let mut file_groups: HashMap<String, Vec<schema::CodeNode>> = HashMap::new();
        for node in parsed_nodes {
            file_groups.entry(node.filepath.clone()).or_default().push(node);
        }
        for (filepath, nodes) in file_groups {
            let _ = database.sync_nodes(&filepath, &nodes);
        }
        let _ = database.resolve_and_save_relations(&parsed_relations);
        let _ = database.propagate_semantics(ontology);
    }
}

fn main() {
    let args = Cli::parse();

    if let Commands::Init = args.command {
        let current_dir = std::env::current_dir().expect("Failed to read current execution context directory");
        let dot_celial = current_dir.join(".celial");

        if !dot_celial.exists() {
            fs::create_dir_all(&dot_celial).expect("Failed to initialize hidden .celial directory container");
        }

        let default_config = serde_json::json!({
            "exclusions": ["test/", "build/", "generated/", "node_modules/", ".dart_tool/", ".git/"],
            "ontology": {
                "layers": ["Tier1Ui", "Tier2Fsm", "Tier3Domain"],
                "default_layer": "Tier3Domain",
                "purities": {"Pure": 1, "StateMutator": 2, "IoBound": 3, "Unknown": 0},
                "default_purity": "Unknown",
                "roles": ["FfiBridge", "StateContainer", "LayoutWidget", "Utility"],
                "barriers": ["FfiBridge"],
                "rules": []
            }
        });

        let target_config_path = dot_celial.join("celial.json");
        fs::write(
            &target_config_path,
            serde_json::to_string_pretty(&default_config).unwrap()
        ).expect("Failed to write celial.json inside the target .celial/ folder boundary");

        println!("Successfully bootstrapped workspace context at: {:?}", current_dir);
        println!("Created hidden folder target location: .celial/");
        println!("Generated operational profile: .celial/celial.json");
        return;
    }

    let workspace = match Workspace::discover() {
        Ok(w) => w,
        Err(e) => {
            eprintln!("{}", e);
            std::process::exit(1);
        }
    };

    let ontology = load_ontology_from_workspace(&workspace);

    let db_path_str = workspace.db_path.to_string_lossy();
    let mut database = match Database::init(&db_path_str) {
        Ok(db) => db,
        Err(e) => {
            eprintln!("Database initialization failed: {:?}", e);
            return;
        }
    };

    match args.command {
        Commands::Init => {}
        Commands::Reindex => {
            println!("Scanning workspace: {}", workspace.root_dir.display());
            let mut parsed_nodes = Vec::new();
            let mut parsed_relations = Vec::new();

            if let Err(e) = ingest_git_delta(&workspace.root_dir.to_string_lossy(), &workspace, &mut parsed_nodes, &mut parsed_relations) {
                eprintln!("Scanning error: {:?}", e);
                return;
            }

            let mut file_groups: std::collections::HashMap<String, Vec<schema::CodeNode>> = std::collections::HashMap::new();
            for node in parsed_nodes {
                file_groups.entry(node.filepath.clone()).or_default().push(node);
            }

            let mut sync_count = 0;
            for (filepath, nodes) in file_groups {
                if let Err(e) = database.sync_nodes(&filepath, &nodes) {
                    eprintln!("Failed syncing file {}: {:?}", filepath, e);
                } else {
                    sync_count += nodes.len();
                }
            }

            println!("Synchronized {} nodes. Resolving structural relationships...", sync_count);
            match database.resolve_and_save_relations(&parsed_relations) {
                Ok(_) => println!("Successfully resolved and saved scope-aware logic edges."),
                Err(e) => {
                    eprintln!("Failed resolving relationships: {:?}", e);
                    return;
                }
            }

            println!("Propagating topological semantics (with purity-barrier protection)...");
            match database.propagate_semantics(&ontology) {
                Ok(_) => println!("Topological propagation completed successfully."),
                Err(e) => eprintln!("Failed to run topological propagation: {:?}", e),
            }
        }
        Commands::DiscoverOntology => {
            let dirty_count = database.count_dirty_nodes().unwrap_or(0);

            let ontology = serde_json::json!({
                "structural_axes": ["upstream", "downstream", "symmetric"],
                "layers": ontology.layers,
                "roles": ontology.roles,
                "patterns": ["Singleton", "Strategy", "Command", "FsmState", "Repository"],
                "purities": ontology.purities.keys().cloned().collect::<Vec<_>>(),
                "rules": ontology.rules.iter().map(|r| r.name.clone()).collect::<Vec<_>>(),
                "state_metrics": {
                    "pending_dirty_nodes": dirty_count
                }
            });

            println!("{}", serde_json::to_string_pretty(&ontology).unwrap());
        }
        Commands::CompileContext { target, radius, direction, resolution, out } => {
            match database.get_subgraph(&target, radius, &direction) {
                Ok(nodes) => {
                    if nodes.is_empty() { std::process::exit(1); }
                    if let Err(_) = ContextCompiler::compile(&target, &nodes, &resolution, &direction, radius, &out, &ontology) {
                        std::process::exit(1);
                    }
                }
                Err(_) => std::process::exit(1)
            }
        }

        Commands::Watch => {
            let root_dir_str = workspace.root_dir.to_string_lossy().into_owned();
            println!("Celial Graph Engine Watch Daemon active on: {}", root_dir_str);
            let mut last_git_head = get_git_head(&root_dir_str);

            loop {
                let current_git_head = get_git_head(&root_dir_str);
                if current_git_head != last_git_head {
                    println!("Git HEAD structural alteration detected ({} -> {}). Synchronizing codebase historical state...", last_git_head, current_git_head);
                    force_full_reindex(&workspace, &mut database, &ontology);
                    last_git_head = current_git_head;
                    sleep(Duration::from_millis(1000));
                    continue;
                }

                let mut parsed_nodes = Vec::new();
                let mut parsed_relations = Vec::new();

                if let Ok(_) = database.get_dirty_nodes() {
                     if ingest_git_delta(&root_dir_str, &workspace, &mut parsed_nodes, &mut parsed_relations).is_ok() {
                         if !parsed_nodes.is_empty() {
                             let mut file_groups: HashMap<String, Vec<schema::CodeNode>> = HashMap::new();
                             for node in parsed_nodes {
                                 file_groups.entry(node.filepath.clone()).or_default().push(node);
                             }
                             for (filepath, nodes) in file_groups {
                                 let _ = database.sync_nodes(&filepath, &nodes);
                             }
                             let _ = database.resolve_and_save_relations(&parsed_relations);
                             let _ = database.propagate_semantics(&ontology);
                             println!("High-frequency synchronization complete.");
                         }
                     }
                }

                sleep(Duration::from_millis(1000));
            }
        }
        Commands::Audit { rule } => {
            println!("Auditing codebase invariants against profile: {} ...", rule);
            match database.check_architectural_violations(&ontology) {
                Ok(violations) => {
                    if violations.is_empty() {
                        println!("Success: Architecture is clean. Zero policy violations discovered.");
                        std::process::exit(0);
                    } else {
                        eprintln!("\nDiscovered {} structural policy violations:", violations.len());
                        for (i, violation) in violations.iter().enumerate() {
                            eprintln!(
                                "\n[{}] Violation Type: {}\n  File: {}\n  Source: {}\n  Target: {}\n  Detail: {}",
                                i + 1,
                                violation.rule_name,
                                violation.filepath,
                                violation.source_id,
                                violation.target_id,
                                violation.message
                            );
                        }
                        std::process::exit(1);
                    }
                }
                Err(e) => {
                    eprintln!("Audit execution failed: {:?}", e);
                    std::process::exit(1);
                }
            }
        }
        Commands::GetDirtyNodes => {
            match database.get_dirty_nodes_manifest() {
                Ok(manifest) => {
                    let json_array = serde_json::json!(manifest);
                    println!("{}", serde_json::to_string_pretty(&json_array).unwrap());
                }
                Err(e) => {
                    eprintln!("Failed to fetch dirty nodes manifest: {:?}", e);
                    std::process::exit(1);
                }
            }
        }
        Commands::QueryFile { path, methods, independent_functions, impl_methods, classes, functions, imports, return_types, return_types_include, include_body } => {
            println!("Querying database for: {}", path);
            match database.query_file(&path, methods, independent_functions, impl_methods.as_deref(), classes, functions, imports, return_types.as_deref(), return_types_include.as_deref(), include_body) {
                Ok(result_json) => println!("{}", result_json),
                Err(e) => { eprintln!("Query failed: {:?}", e); std::process::exit(1); }
            }
        }
        Commands::UpdateNodes { payload_file } => {
            println!("Ingesting IPC payload: {}", payload_file);
            let data = fs::read_to_string(&payload_file).expect("Failed to read IPC payload");
            let updates: Vec<serde_json::Value> = serde_json::from_str(&data).expect("Invalid JSON payload schema");
            
            let mut embedder = LocalEmbedder::new().ok();

            for update in updates {
                let id = update["id"].as_str().expect("ID required");
                let summary = update["summary"].as_str().map(|s| s.to_string());
                let layer = update["layer"].as_str().map(|s| s.to_string());
                let role = update["role"].as_str().map(|s| s.to_string());
                let pattern = update["pattern"].as_str().map(|s| s.to_string());
                let purity = update["purity"].as_str().map(|s| s.to_string());

                let mut barrier_opt = None;
                if let Some(ref r_val) = role {
                    if r_val == "FfiBridge" { barrier_opt = Some(true); }
                }

                let mut embedding_blob = None;
                if let Some(ref text) = summary {
                    if let Some(ref mut em) = embedder {
                        let semantic_payload = format!("Symbol: {}. Summary: {}", id, text);
                        if let Ok(vector) = em.embed(&semantic_payload) {
                            let mut bytes = Vec::with_capacity(vector.len() * 4);
                            for f in vector {
                                bytes.extend_from_slice(&f.to_le_bytes());
                            }
                            embedding_blob = Some(bytes);
                        }
                    }
                }

                if let Err(e) = database.update_node_metadata(id, summary, layer, role, pattern, purity, barrier_opt, embedding_blob) {
                    eprintln!("Failed to update node metadata for ID {}: {:?}", id, e);
                }
            }
            println!("Successfully processed batch updates.");
        }
        Commands::SemanticSearch { query, limit } => {
            let embedder = LocalEmbedder::new();
            let results = if let Ok(mut embedder) = embedder {
                let query_vector = match embedder.embed(&query) {
                    Ok(v) => v,
                    Err(e) => {
                        eprintln!("Failed to embed query: {}", e);
                        std::process::exit(1);
                    }
                };
                match database.semantic_vector_search(&query_vector, limit as usize) {
                    Ok(r) => r,
                    Err(e) => {
                        eprintln!("Semantic vector search failed: {:?}", e);
                        std::process::exit(1);
                    }
                }
            } else {
                match database.semantic_search(&query, limit) {
                    Ok(r) => r,
                    Err(e) => {
                        eprintln!("Semantic search failed: {:?}", e);
                        std::process::exit(1);
                    }
                }
            };
            println!("{}", serde_json::to_string_pretty(&results).unwrap());
        }
    }
}

fn ingest_git_delta(
    dir: &str,
    workspace: &Workspace,
    all_nodes: &mut Vec<schema::CodeNode>,
    all_relations: &mut Vec<schema::UnresolvedRelation>,
) -> std::io::Result<()> {
    let output = Command::new("git")
        .args(["ls-files", "--modified", "--others", "--exclude-standard"])
        .current_dir(dir)
        .output()
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;

    if !output.status.success() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::Other,
            "git diff failed",
        ));
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    for line in stdout.lines() {
        let path = Path::new(dir).join(line);
        if !path.exists() {
            continue;
        }
        let path_str = path.to_string_lossy();
        if workspace.is_excluded(&path_str) {
            continue;
        }
        if path_str.ends_with(".rs") || path_str.ends_with(".dart") {
            if let Ok(content) = fs::read_to_string(&path) {
                match AstParser::parse_file(&path_str, &content) {
                    Ok((mut nodes, mut relations)) => {
                        all_nodes.append(&mut nodes);
                        all_relations.append(&mut relations);
                    }
                    Err(e) => eprintln!("Parser error on file {}: {}", path_str, e),
                }
            }
        }
    }

    Ok(())
}
